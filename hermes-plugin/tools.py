"""SceneMux tool handlers.

Full studio pipeline: doctor / setup / project / plan / render / status /
export / cancel.

Handler contract: ``def handler(args: dict, **kwargs) -> str`` — ALWAYS a
JSON string, success or error; never raise.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any, Callable, Dict

from . import client
from .client import DEFAULT_BASE_URL, SceneMuxError, resolve_base_url

PLUGIN_NAME = "scenemux"
HEALTH_WAIT_SECONDS = 60
HEALTH_POLL_INTERVAL = 3

# (env name, what it is + where to get one)
ALL_KEYS = (
    ("COMFY_CLOUD_API_KEY", "Comfy Cloud renderer (cloud mode; Standard+ plan) — https://platform.comfy.org/profile/api-keys"),
    ("OLLAMA_API_KEY", "Ollama Cloud LLM (cloud mode) — https://ollama.com/settings/keys"),
    ("OPENCODE_API_KEY", "OpenCode Go LLM (optional fallback) — https://opencode.ai/"),
    ("FAL_KEY", "FAL.ai drafts (optional) — https://fal.ai/dashboard/keys"),
)

COMPOSE_BY_MODE = {
    "cloud": "docker-compose.cloud.yml",
    "local": "docker-compose.yml",
}


# ----------------------------------------------------------------- secrets

def _secret(name: str) -> str:
    """Read one secret through Hermes' secret scope.

    Never reads ``.env`` files or ``os.environ`` directly (catalog rule 11).
    """
    try:
        from agent.secret_scope import get_secret
    except Exception:
        return ""
    try:
        value = get_secret(name)
    except Exception:
        return ""
    return value.strip() if isinstance(value, str) else ""


def _key_report(mode: str) -> Dict[str, Any]:
    local_ollama = _local_ollama_up() if mode == "cloud" else False
    present = {name: bool(_secret(name)) for name, _ in ALL_KEYS}
    missing = []
    if mode == "cloud":
        if not present["COMFY_CLOUD_API_KEY"]:
            missing.append("COMFY_CLOUD_API_KEY")
        # A locally running Ollama satisfies the LLM lane for free — the
        # setup tool then points the server at it.
        if not (present["OLLAMA_API_KEY"] or present["OPENCODE_API_KEY"]) and not local_ollama:
            missing.append("OLLAMA_API_KEY or OPENCODE_API_KEY (or run Ollama locally)")
    # FAL_KEY is always optional.
    return {
        "present": present,
        "missing": missing,
        "local_ollama": local_ollama or None,
    }


# ----------------------------------------------------------------- helpers

def _repo_root(ctx=None) -> Path:
    """SceneMux repo root — the compose files + ``server/`` the plugin
    launches.

    Prefer an explicit ``repo_path`` plugin setting (a full clone of the
    SceneMux repo): a plugin installed from the catalog is copied into
    ``~/.hermes/plugins/`` with no server code beside it, so it needs the
    user's clone pointed at by hand. Otherwise fall back to the plugin's
    parent dir (the layout when the plugin is installed from a full clone
    / local repo, where it sits in ``<repo>/hermes-plugin``).
    """
    if ctx is not None:
        try:
            rp = str(ctx.get_config("repo_path", default="") or "").strip()
        except Exception:
            rp = ""
        if rp:
            try:
                p = Path(rp).expanduser().resolve()
                if (p / "docker-compose.yml").exists() or (p / "docker-compose.cloud.yml").exists():
                    return p
            except Exception:
                pass
    return Path(__file__).resolve().parent.parent


def _mode(ctx) -> str:
    mode = str(ctx.get_config("mode", default="cloud") or "cloud").lower()
    return mode if mode in ("cloud", "local") else "cloud"


def _base_url(ctx, explicit: str = "") -> str:
    base = explicit or str(ctx.get_config("base_url", default=DEFAULT_BASE_URL) or DEFAULT_BASE_URL)
    return resolve_base_url(base)


def _data_dir() -> Path:
    from plugins.plugin_storage import plugin_data_dir

    path = Path(plugin_data_dir(PLUGIN_NAME))
    path.mkdir(parents=True, exist_ok=True)
    return path


# ------------------------------------------- local Ollama detection (free LLM)

def _local_ollama_up(timeout: float = 1.5) -> bool:
    """Is a local Ollama server answering on 127.0.0.1:11434?"""
    import urllib.request

    try:
        with urllib.request.urlopen("http://127.0.0.1:11434/api/tags", timeout=timeout) as r:
            return r.status == 200
    except Exception:
        return False


def _has_gpu(timeout: float = 2.0) -> bool:
    """Best-effort: is there a discrete GPU a model could run on?"""
    import shutil
    import subprocess

    try:
        if shutil.which("nvidia-smi"):
            p = subprocess.run(["nvidia-smi", "-L"], capture_output=True, timeout=timeout)
            if p.returncode == 0 and b"GPU" in (p.stdout or b""):
                return True
        if shutil.which("rocm-smi"):
            p = subprocess.run(["rocm-smi", "--showproductname"], capture_output=True, timeout=timeout)
            if p.returncode == 0 and b"gfx" in (p.stdout or b""):
                return True
    except Exception:
        pass
    return False


def _model_params_b(model: dict) -> float:
    """Parameter size in billions ('8.0B' -> 8.0), 0.0 when unknown."""
    raw = str((model.get("details") or {}).get("parameter_size") or "")
    try:
        return float(raw.rstrip("bB"))
    except ValueError:
        return 0.0


def _local_ollama_model(timeout: float = 2.0) -> str:
    """Name of the best local Ollama model for this machine, '' if none.

    With a GPU: the largest model (quality wins).
    CPU-only: the model closest to ~8B parameters — the server gives each
    LLM call 120s, and 30B-class models on CPU time out on every call,
    silently degrading the plan to fallback prompts.
    """
    import urllib.request

    try:
        with urllib.request.urlopen("http://127.0.0.1:11434/api/tags", timeout=timeout) as r:
            models = json.loads(r.read()).get("models", [])
        if not models:
            return ""
    except Exception:
        return ""
    if _has_gpu():
        best = max(models, key=lambda m: m.get("size") or 0)
    else:
        best = min(models, key=lambda m: (abs(_model_params_b(m) - 8.0), m.get("size") or 0))
    return str(best.get("name") or "")


# ------------------------------------------------- server .env provisioning

def _provision_server_env(root: Path) -> list:
    """Make the server this plugin launches able to find its credentials.

    The compose files load ``.env`` from the repo root into the container
    (``env_file: .env``), so the plugin writes the needed values there:
    the four declared keys, taken from Hermes' secret scope, plus local
    Ollama wiring when Ollama is running locally and no cloud LLM key
    exists (the director then runs free on the user's own machine).

    Existing lines for those names are replaced, missing ones appended;
    nothing else in the file is touched and nothing is ever read back into
    plugin output. Returns the list of names written (for the report).
    """
    env_path = root / ".env"
    try:
        lines = env_path.read_text(encoding="utf-8").splitlines() if env_path.exists() else []
    except Exception:
        lines = []

    def upsert(name: str, value: str) -> None:
        for i, line in enumerate(lines):
            if line.strip().startswith(name + "="):
                lines[i] = f"{name}={value}"
                return
        lines.append(f"{name}={value}")

    written = []
    for name in (
        "COMFY_CLOUD_API_KEY",
        "OLLAMA_API_KEY",
        "OPENCODE_API_KEY",
        "FAL_KEY",
        "HOME_AGENT_PASSWORD",
    ):
        value = _secret(name)
        if value:
            upsert(name, value)
            written.append(name)

    has_cloud_llm = bool(_secret("OLLAMA_API_KEY") or _secret("OPENCODE_API_KEY"))
    if not has_cloud_llm and _local_ollama_up():
        model = _local_ollama_model()
        upsert("LLM_PROVIDER", "ollama")
        # host.docker.internal reaches the Docker host from inside the container
        upsert("OLLAMA_URL", "http://host.docker.internal:11434")
        if model:
            upsert("OLLAMA_MODEL", model)
        written.append("LLM_PROVIDER=ollama (local)")

    # Hermes-director lane: let the server's env fallback (HERMES_URL /
    # HERMES_TOKEN, read by server.llm.hermes_backend) find this machine's
    # Hermes API server, so even sessions configured outside the plugin work.
    hermes_key = _secret("API_SERVER_KEY")
    if hermes_key:
        upsert("HERMES_URL", "http://host.docker.internal:8642")
        upsert("HERMES_TOKEN", hermes_key)
        written.append("HERMES_URL+HERMES_TOKEN (hermes director lane)")

    try:
        env_path.write_text("\n".join(lines).rstrip("\n") + "\n", encoding="utf-8")
    except Exception:
        pass
    return written


def _server_alive(base: str) -> bool:
    try:
        client.get_json(base, "/api/health", timeout=client.HEALTH_TIMEOUT)
        return True
    except SceneMuxError:
        return False


def _http_reachable(url: str, timeout: float = 4.0) -> bool:
    """True if any HTTP response comes back (even 4xx) from url within timeout."""
    import urllib.error
    import urllib.request

    if not url.startswith(("http://", "https://")):
        return False
    try:
        urllib.request.urlopen(url, timeout=timeout)
        return True
    except urllib.error.HTTPError:
        return True  # a response means the host is up
    except Exception:
        return False


def _loopback_base(base: str) -> bool:
    """True when a SceneMux base URL points at this machine."""
    import urllib.parse

    try:
        host = (urllib.parse.urlparse(base).hostname or "").lower()
    except Exception:
        return False
    return host in ("127.0.0.1", "localhost", "::1", "")


def _hermes_urls(ctx, explicit: str = "") -> tuple:
    """Resolve (server_url, probe_url) for the hermes-director lane.

    ``probe_url`` is what the plugin probes from the host: the Hermes API
    server runs on this machine, so ``127.0.0.1:8642`` is always the right
    form. ``server_url`` is what the SceneMux server uses to reach the API
    server. An explicit value (tool arg or plugin setting) is used as-is.
    Otherwise: when the SceneMux server is expected to run in Docker on this
    machine (loopback base), ``host.docker.internal:8642`` reaches the host;
    for a remote base, leave it empty so the server falls back to its own
    HERMES_URL env / default.
    """
    explicit = str(explicit or "").strip().rstrip("/")
    if explicit:
        return explicit, explicit
    cfg = str(ctx.get_config("hermes_url", default="") or "").strip().rstrip("/")
    if cfg:
        return cfg, cfg
    if _loopback_base(_base_url(ctx)):
        return "http://host.docker.internal:8642", "http://127.0.0.1:8642"
    return "", "http://127.0.0.1:8642"


def _start_server(ctx, mode: str, base: str) -> Dict[str, Any]:
    """Launch the SceneMux server detached via Docker, then poll for health."""
    root = _repo_root(ctx)
    compose = root / COMPOSE_BY_MODE[mode]
    if not compose.exists():
        return {
            "started": False,
            "error": (
                f"Compose file not found at {compose}. Set the plugin's "
                "``repo_path`` setting to a full SceneMux repo clone (one that "
                "contains docker-compose.yml + server/), or install the plugin "
                "from the repo (hermes plugins install ep150de/scenemux) so the "
                "server code sits beside it, or start the server yourself and "
                "point base_url at it."
            ),
        }
    docker = shutil.which("docker")
    if not docker:
        return {
            "started": False,
            "error": (
                "Docker is not on PATH. Install Docker (Desktop or engine), or start "
                f"the server manually: cd {root} && docker compose -f {COMPOSE_BY_MODE[mode]} up -d --build"
            ),
        }

    # The plugin supplies credentials to the server it spawns: compose
    # files load ./env into the container, so the declared keys (from
    # Hermes' secret scope) are provisioned into that file, alongside
    # local-Ollama wiring when a local LLM is available.
    provisioned = _provision_server_env(root)
    env = dict(os.environ)
    for name, _ in ALL_KEYS:
        value = _secret(name)
        if value:
            env[name] = value

    log_file = _data_dir() / "server-launch.log"
    cmd = [docker, "compose", "-f", str(compose), "up", "-d", "--build"]
    creation_flags = (
        subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS
        if os.name == "nt"
        else 0
    )
    try:
        with open(log_file, "ab") as log:
            proc = subprocess.Popen(
                cmd,
                cwd=str(root),
                env=env,
                stdout=log,
                stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL,
                creationflags=creation_flags,
                start_new_session=(os.name != "nt"),
            )
    except Exception as e:  # noqa: BLE001 — handler must not raise
        return {"started": False, "error": f"Failed to launch docker compose: {e}"}

    out: Dict[str, Any] = {
        "started": True,
        "launcher": "docker-compose (detached)",
        "compose_file": str(compose),
        "pid": proc.pid,
        "log": str(log_file),
        "note": "First run builds the image; that can take several minutes.",
    }
    if provisioned:
        out["server_env"] = "Provisioned into the repo .env (server config): " + ", ".join(provisioned)

    deadline = time.time() + HEALTH_WAIT_SECONDS
    last_error = ""
    while time.time() < deadline:
        try:
            health = client.get_json(base, "/api/health", timeout=client.HEALTH_TIMEOUT)
            out["healthy"] = True
            out["health"] = health
            return out
        except SceneMuxError as e:
            last_error = str(e)
            time.sleep(HEALTH_POLL_INTERVAL)
    out["healthy"] = False
    out["note"] = (
        "Server still starting (image build in progress). Re-run scenemux_doctor "
        "in a couple of minutes; the launch log is at " + str(log_file)
    )
    out["last_error"] = last_error
    return out


# ----------------------------------------------------------------- handlers

def make_doctor(ctx) -> Callable[..., str]:
    def handler(args: dict, **kwargs) -> str:
        try:
            base = _base_url(ctx, str(args.get("base_url") or ""))
            report: Dict[str, Any] = {
                "base_url": base,
                "ok": False,
                "server": None,
                "llm": None,
                "keys": None,
                "ffmpeg": None,
                "hints": [],
            }
            try:
                server = client.get_json(base, "/api/health", timeout=client.CONNECT_TIMEOUT)
                report["server"] = {"reachable": True, "health": server}
            except SceneMuxError as e:
                report["server"] = {"reachable": False, "error": str(e)}
                report["hints"].append(
                    "Server not reachable — run scenemux_setup to start it, "
                    "or pass base_url if it runs elsewhere."
                )

            if report["server"].get("reachable"):
                try:
                    report["llm"] = client.get_json(base, "/api/llm/status", timeout=client.READ_TIMEOUT)
                except SceneMuxError as e:
                    report["llm"] = {"error": str(e)}

            mode = _mode(ctx)
            report["mode"] = mode
            keys = _key_report(mode)
            report["keys"] = keys
            if keys["missing"]:
                report["hints"].append(
                    "Missing API keys for mode '" + mode + "': "
                    + ", ".join(keys["missing"])
                    + ". Add them to ~/.hermes/.env (see the plugin README), then re-run scenemux_doctor."
                )

            ffmpeg = shutil.which("ffmpeg")
            report["ffmpeg"] = {"found": bool(ffmpeg), "path": ffmpeg or None}
            if not ffmpeg:
                report["hints"].append(
                    "ffmpeg not found on PATH (only needed for non-Docker server runs; Docker mode bundles it)."
                )

            # Hermes-director lane (director_backend=hermes): is the local
            # Hermes API server configured and answering?
            hermes_key = _secret("API_SERVER_KEY")
            _, hermes_probe = _hermes_urls(ctx, "")
            hermes_up = bool(hermes_key) and _http_reachable(
                hermes_probe + "/health", timeout=3.0
            )
            report["hermes_director"] = {
                "key_configured": bool(hermes_key),
                "api_server_url": hermes_probe,
                "reachable": hermes_up,
            }
            if not hermes_key:
                report["hints"].append(
                    "Hermes-director lane not configured: add API_SERVER_ENABLED=true "
                    "and API_SERVER_KEY=<key> to ~/.hermes/.env and run the Hermes "
                    "gateway (hermes gateway) to use director_backend=hermes."
                )
            elif not hermes_up:
                report["hints"].append(
                    "Hermes API server not reachable at " + hermes_probe +
                    " — check API_SERVER_ENABLED=true in ~/.hermes/.env and that the "
                    "gateway is running (hermes gateway). Only needed for "
                    "director_backend=hermes."
                )

            report["ok"] = bool(report["server"].get("reachable")) and not keys["missing"]
            if report["ok"]:
                report["hint"] = "Ready. Create a project and upload the song next (scenemux_project)."
            return json.dumps(report)
        except Exception as e:  # noqa: BLE001
            return json.dumps({"error": f"scenemux_doctor failed: {e}"})

    return handler


def make_setup(ctx) -> Callable[..., str]:
    def handler(args: dict, **kwargs) -> str:
        try:
            mode = str(args.get("mode") or _mode(ctx)).lower()
            if mode not in ("cloud", "local"):
                return json.dumps({"error": "mode must be 'cloud' or 'local'"})
            ctx.set_config("mode", mode)

            base = _base_url(ctx, str(args.get("base_url") or ""))
            if args.get("base_url"):
                ctx.set_config("base_url", base)

            result: Dict[str, Any] = {"mode": mode, "base_url": base, "keys": _key_report(mode)}

            if _server_alive(base):
                result["already_running"] = True
                result["health"] = client.get_json(base, "/api/health", timeout=client.HEALTH_TIMEOUT)
                result["ok"] = not result["keys"]["missing"]
                result["note"] = (
                    "Server already running."
                    if result["keys"]["missing"]
                    else " Ready. Next: scenemux_project (create project + upload the song)."
                )
                return json.dumps(result)

            start = bool(args.get("start_server", True))
            if start:
                launch = _start_server(ctx, mode, base)
                result["launch"] = launch
                result["ok"] = bool(launch.get("healthy"))
                if not launch.get("started") and launch.get("error"):
                    result["error"] = launch["error"]
            else:
                result["note"] = "start_server=false — settings saved, server not started."
                result["ok"] = not result["keys"]["missing"]

            if result["keys"]["missing"] and not result.get("error"):
                result["hint"] = (
                    "Some keys for mode '" + mode + "' are missing: "
                    + ", ".join(result["keys"]["missing"])
                    + ". Add them to ~/.hermes/.env (see the plugin README for exact names and where to get each), "
                    "then re-run scenemux_doctor."
                )
            return json.dumps(result)
        except Exception as e:  # noqa: BLE001
            return json.dumps({"error": f"scenemux_setup failed: {e}"})

    return handler


# --------------------------------------------------------- session memory

def _sessions_file() -> Path:
    return _data_dir() / "sessions.json"


def _load_sessions() -> Dict[str, Any]:
    try:
        data = json.loads(_sessions_file().read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _remember_session(sid: str, info: Dict[str, Any]) -> None:
    try:
        data = _load_sessions()
        data[sid] = {**data.get(sid, {}), **info}
        _sessions_file().write_text(json.dumps(data, indent=1), encoding="utf-8")
    except Exception:
        pass


# ------------------------------------------------------- studio API helpers

def _down_error(base: str, mode: str) -> str:
    return json.dumps({
        "error": f"SceneMux server not reachable at {base}.",
        "fix": "Run scenemux_setup (mode: " + mode + ") to start it, or pass base_url if it runs elsewhere.",
    })


def _call(ctx, args: dict, path: str, payload: dict, timeout: float) -> Any:
    """POST JSON to the studio API; error dicts from the server become errors."""
    base = _base_url(ctx, str(args.get("base_url") or ""))
    if not _server_alive(base):
        raise SceneMuxError(_down_error(base, _mode(ctx)))
    body = client.post_json(base, path, payload, timeout=timeout)
    if isinstance(body, dict) and body.get("error"):
        raise SceneMuxError(str(body["error"]))
    return body


def _extract_plan(res: Any) -> Dict[str, Any]:
    """Pull the timeline plan out of either plan/stills/states response shape."""
    if not isinstance(res, dict):
        return {}
    for key in ("timeline_plan", "plan", "storyboard"):
        value = res.get(key)
        if isinstance(value, dict) and value.get("scenes"):
            return value
    if res.get("scenes"):
        return res
    return {}


def _compact_scenes(plan: Dict[str, Any], sid: str) -> list:
    """Compact scene list for the agent; copies stills into plugin data so
    they can be shown as local files."""
    out = []
    board_dir = _data_dir() / sid / "storyboard"
    for i, scene in enumerate(plan.get("scenes") or []):
        entry = {
            "index": i,
            "time": scene.get("time") or scene.get("start"),
            "type": scene.get("type"),
            "status": scene.get("status"),
            "prompt": str(scene.get("prompt") or "")[:280],
        }
        still = scene.get("still_path") or scene.get("img_path")
        if still:
            src = Path(str(still))
            if src.is_file():
                try:
                    board_dir.mkdir(parents=True, exist_ok=True)
                    local = board_dir / f"still_{i:02d}.png"
                    shutil.copyfile(src, local)
                    entry["still_preview"] = str(local)
                except Exception:
                    entry["still_server_path"] = str(still)
            else:
                entry["still_server_path"] = str(still)
        out.append(entry)
    return out


# --------------------------------------------------------------- handlers

def make_project(ctx) -> Callable[..., str]:
    def handler(args: dict, **kwargs) -> str:
        try:
            base = _base_url(ctx, str(args.get("base_url") or ""))
            if not _server_alive(base):
                return _down_error(base, _mode(ctx))

            audio = Path(str(args.get("audio_path") or "")).expanduser()
            if not audio.is_file():
                return json.dumps({"error": f"Audio file not found: {audio}"})
            direction = str(args.get("direction") or "").strip()
            if not direction:
                return json.dumps({"error": "direction is required — 1-3 sentences of creative direction."})

            # Pipeline lanes declared in the tool schema — resolve them BEFORE
            # touching the server so a misconfigured lane never leaks a session.
            video_backend = str(args.get("video_backend") or "").strip()
            stills_backend = str(args.get("stills_backend") or "").strip()
            director_backend = str(args.get("director_backend") or "").strip().lower()
            hermes_server_url = ""
            hermes_key = ""
            if director_backend == "hermes":
                hermes_key = _secret("API_SERVER_KEY")
                if not hermes_key:
                    return json.dumps({
                        "error": "director_backend=hermes needs the local Hermes API server key.",
                        "fix": "Add API_SERVER_ENABLED=true and API_SERVER_KEY=<key> to ~/.hermes/.env "
                               "(the key the Hermes gateway's API server authenticates with), make sure "
                               "the gateway is running (hermes gateway), then re-run scenemux_project.",
                    })
                hermes_server_url, hermes_probe_url = _hermes_urls(ctx, str(args.get("hermes_url") or ""))
                if args.get("hermes_url"):
                    ctx.set_config("hermes_url", hermes_server_url)
                if not _http_reachable(hermes_probe_url + "/health", timeout=4.0):
                    return json.dumps({
                        "error": "The Hermes API server is not reachable at " + hermes_probe_url +
                                 " (needed for director_backend=hermes).",
                        "fix": "Check that API_SERVER_ENABLED=true and API_SERVER_KEY are set in "
                               "~/.hermes/.env and the gateway is running (`hermes gateway`; it listens "
                               "on port 8642). Re-run scenemux_doctor to verify.",
                    })

            sid = str(args.get("session_id") or "")
            resumed = bool(sid)
            if not sid:
                created = client.post_json(base, "/api/session/create", {}, timeout=client.READ_TIMEOUT)
                sid = str((created or {}).get("session_id") or "")
            if not sid:
                return json.dumps({"error": "Could not create/identify a session."})

            cfg = {"session_id": sid}
            preset = str(args.get("pipeline_preset") or "").strip()
            if preset:
                cfg["pipeline_preset"] = preset
            video_model = str(args.get("video_model") or "").strip()
            if video_model:
                cfg["video_model"] = video_model
            # Explicit lanes override the preset (server normalizes + applies the preset)
            if video_backend:
                cfg["pipeline_video"] = video_backend
            if stills_backend:
                cfg["pipeline_stills"] = stills_backend
            if director_backend:
                cfg["pipeline_director"] = director_backend
            comfy_url = str(
                args.get("comfy_url") or ctx.get_config("comfy_url", default="") or ""
            ).strip()
            if comfy_url:
                cfg["comfy_url"] = comfy_url
            home_agent_url = str(
                args.get("home_agent_url") or ctx.get_config("home_agent_url", default="") or ""
            ).strip()
            if home_agent_url:
                cfg["home_agent_url"] = home_agent_url
                home_agent_password = _secret("HOME_AGENT_PASSWORD")
                if home_agent_password:
                    cfg["home_agent_password"] = home_agent_password
            if director_backend == "hermes":
                if hermes_server_url:
                    cfg["hermes_url"] = hermes_server_url
                # Always send the token per session; the server also falls
                # back to its HERMES_TOKEN env for URL resolution.
                cfg["hermes_token"] = hermes_key
            cfg["creative_direction"] = direction
            try:
                cfg_res = _call(ctx, args, "/api/session/config", cfg, timeout=client.READ_TIMEOUT)
            except SceneMuxError as e:
                return json.dumps({"error": str(e), "session_id": sid, "stage": "pipeline config"})

            try:
                up = client.upload_file(
                    base, "/api/upload/audio", "file", audio.name,
                    audio.read_bytes(), fields={"session_id": sid}, timeout=600,
                )
            except SceneMuxError as e:
                return json.dumps({"error": str(e), "session_id": sid, "stage": "audio upload/analysis"})
            if isinstance(up, dict) and up.get("error"):
                return json.dumps({"error": str(up["error"]), "session_id": sid, "stage": "audio upload/analysis"})

            refs_uploaded = 0
            for rp in args.get("refs_paths") or []:
                p = Path(str(rp)).expanduser()
                if not p.is_file():
                    continue
                try:
                    client.upload_files(
                        base, "/api/upload/images", "files",
                        [(p.name, p.read_bytes())], fields={"session_id": sid}, timeout=300,
                    )
                    refs_uploaded += 1
                except SceneMuxError as e:
                    return json.dumps({"error": f"Reference image upload failed: {e}", "session_id": sid})

            up = up if isinstance(up, dict) else {}
            analysis = up.get("analysis") if isinstance(up.get("analysis"), dict) else {}
            cfg_res = cfg_res if isinstance(cfg_res, dict) else {}
            lanes = {
                "director": cfg_res.get("pipeline_director") or "server-default",
                "stills": cfg_res.get("pipeline_stills") or "server-default",
                "video": cfg_res.get("pipeline_video") or "server-default",
            }
            _remember_session(sid, {
                "direction": direction,
                "preset": preset or "server-default",
                "audio": str(audio),
                "track_title": analysis.get("track_title") or up.get("track_title"),
                "lanes": lanes,
            })
            return json.dumps({
                "session_id": sid,
                "resumed": resumed,
                "track_title": analysis.get("track_title") or up.get("track_title"),
                "duration": analysis.get("duration"),
                "lyrics_source": analysis.get("lyrics_source") or up.get("lyrics_source"),
                "has_lyrics": analysis.get("has_lyrics"),
                "lyrics_preview": (analysis.get("lyrics_preview") or "")[:300] or None,
                "references_uploaded": refs_uploaded,
                "preset": preset or "server-default",
                "pipeline": lanes,
                "next": "scenemux_plan with this session_id to build the storyboard; show the scene list to the user BEFORE any render.",
            })
        except Exception as e:  # noqa: BLE001
            return json.dumps({"error": f"scenemux_project failed: {e}"})

    return handler


def make_plan(ctx) -> Callable[..., str]:
    def handler(args: dict, **kwargs) -> str:
        try:
            sid = str(args.get("session_id") or "")
            if not sid:
                return json.dumps({"error": "session_id is required."})
            base = _base_url(ctx, str(args.get("base_url") or ""))
            if not _server_alive(base):
                return _down_error(base, _mode(ctx))

            # Idempotency: an existing storyboard is returned, not re-planned.
            try:
                pre_state = client.get_json(base, f"/api/studio/{sid}/state", timeout=30)
                pre_plan = _extract_plan(pre_state)
                if pre_plan.get("scenes"):
                    return json.dumps({
                        "session_id": sid,
                        "already_planned": True,
                        "scene_count": len(pre_plan["scenes"]),
                        "scenes": _compact_scenes(pre_plan, sid),
                        "next": "Storyboard already exists — show it to the user. Use scene_indices with this tool to (re)generate specific stills.",
                    })
            except SceneMuxError:
                pass

            note = _load_sessions().get(sid) or {}
            direction = str(args.get("direction") or note.get("direction") or "").strip()
            payload = {"session_id": sid}
            if direction:
                payload["direction"] = direction

            plan_res = _call(ctx, args, "/api/studio/plan", payload, timeout=900)
            plan = _extract_plan(plan_res)

            stills_error = None
            stills_payload: Dict[str, Any] = {"session_id": sid}
            if args.get("max_scenes") is not None:
                stills_payload["max_scenes"] = int(args["max_scenes"])
            if args.get("scene_indices") is not None:
                stills_payload["scene_indices"] = [int(i) for i in args["scene_indices"]]
            try:
                stills_res = _call(ctx, args, "/api/studio/storyboard-stills", stills_payload, timeout=1800)
                plan = _extract_plan(stills_res) or plan
            except SceneMuxError as e:
                stills_error = str(e)

            if not plan:
                return json.dumps({
                    "error": "Plan returned no scenes.",
                    "session_id": sid,
                    "hint": "Check the server log; if the LLM lane failed (credits/subscription), fix the key or model and re-run.",
                    "raw": str(plan_res)[:500],
                })

            _remember_session(sid, {"scenes_planned": len(plan.get("scenes") or [])})
            out: Dict[str, Any] = {
                "session_id": sid,
                "scene_count": len(plan.get("scenes") or []),
                "scenes": _compact_scenes(plan, sid),
                "next": "Show the storyboard to the user. If approved, scenemux_render (estimate first, then confirm=true).",
            }
            if stills_error:
                out["stills_error"] = stills_error
                out["stills_note"] = (
                    "Scene prompts are planned; still previews failed. "
                    "If it was a missing key/credit error, fix it and re-run scenemux_plan with scene_indices to retry only the stills."
                )
            return json.dumps(out)
        except SceneMuxError as e:
            return json.dumps({"error": str(e), "session_id": args.get("session_id")})
        except Exception as e:  # noqa: BLE001
            return json.dumps({"error": f"scenemux_plan failed: {e}"})

    return handler


def make_render(ctx) -> Callable[..., str]:
    def handler(args: dict, **kwargs) -> str:
        try:
            sid = str(args.get("session_id") or "")
            if not sid:
                return json.dumps({"error": "session_id is required."})
            base = _base_url(ctx, str(args.get("base_url") or ""))
            if not _server_alive(base):
                return _down_error(base, _mode(ctx))

            # State check: is there a plan to render?
            try:
                state = client.get_json(base, f"/api/studio/{sid}/state", timeout=30)
            except SceneMuxError as e:
                return json.dumps({"error": str(e), "session_id": sid})
            queue = (state or {}).get("queue") or {}
            if not queue.get("scene_total"):
                return json.dumps({
                    "error": "No storyboard plan for this session — run scenemux_plan first.",
                    "session_id": sid,
                })

            est = _call(ctx, args, "/api/studio/estimate", {"session_id": sid}, timeout=60)

            if not bool(args.get("confirm", False)):
                return json.dumps({
                    "session_id": sid,
                    "estimate_only": True,
                    "estimate": est,
                    "queue": queue,
                    "spend_warning": "Rendering will SPEND API credits per the estimate above (video model + stills backends).",
                    "next": "Present the estimate to the user. Only after EXPLICIT user approval, call scenemux_render again with confirm=true. Never set confirm yourself.",
                })

            payload: Dict[str, Any] = {"session_id": sid, "rebuild_plan": bool(args.get("rebuild_plan", False))}
            direction = str(args.get("direction") or "").strip()
            if direction:
                payload["direction"] = direction
            if args.get("scenes"):
                payload["scenes"] = args["scenes"]
            started = _call(ctx, args, "/api/studio/render", payload, timeout=120)
            return json.dumps({
                "session_id": sid,
                "render_started": True,
                "response": started,
                "next": "Render is async. Poll scenemux_status (wait_seconds=120 between calls) until download_url appears, then scenemux_export. Tell the user the expected time from the estimate.",
            })
        except SceneMuxError as e:
            return json.dumps({"error": str(e), "session_id": args.get("session_id")})
        except Exception as e:  # noqa: BLE001
            return json.dumps({"error": f"scenemux_render failed: {e}"})

    return handler


def make_status(ctx) -> Callable[..., str]:
    def handler(args: dict, **kwargs) -> str:
        try:
            sid = str(args.get("session_id") or "")
            if not sid:
                return json.dumps({"error": "session_id is required."})
            base = _base_url(ctx, str(args.get("base_url") or ""))
            if not _server_alive(base):
                return _down_error(base, _mode(ctx))

            wait = max(0, min(int(args.get("wait_seconds") or 0), 120))
            deadline = time.time() + wait
            state = None
            while True:
                state = client.get_json(base, f"/api/studio/{sid}/state", timeout=30)
                queue = (state or {}).get("queue") or {}
                total = queue.get("scene_total") or 0
                finished = bool((state or {}).get("download_url")) or (
                    total > 0 and queue.get("ready", 0) == total
                )
                if finished or time.time() >= deadline:
                    break
                time.sleep(5)

            queue = (state or {}).get("queue") or {}
            scenes = []
            for i, s in enumerate(((state or {}).get("timeline_plan") or {}).get("scenes") or []):
                scenes.append({"i": i, "type": s.get("type"), "status": s.get("status")})
            total = queue.get("scene_total") or 0
            finished = bool((state or {}).get("download_url")) or (total > 0 and queue.get("ready", 0) == total)
            out: Dict[str, Any] = {
                "session_id": sid,
                "session_status": (state or {}).get("status"),
                "queue": queue,
                "download_url": (state or {}).get("download_url"),
                "error": (state or {}).get("error"),
                "scenes": scenes,
                "finished": finished,
                "next": "scenemux_export to download the video." if finished
                        else "Keep polling scenemux_status (wait_seconds=120). Cancel with scenemux_cancel if the user wants to stop.",
            }
            return json.dumps(out)
        except SceneMuxError as e:
            return json.dumps({"error": str(e), "session_id": args.get("session_id")})
        except Exception as e:  # noqa: BLE001
            return json.dumps({"error": f"scenemux_status failed: {e}"})

    return handler


def make_export(ctx) -> Callable[..., str]:
    def handler(args: dict, **kwargs) -> str:
        try:
            sid = str(args.get("session_id") or "")
            if not sid:
                return json.dumps({"error": "session_id is required."})
            base = _base_url(ctx, str(args.get("base_url") or ""))
            if not _server_alive(base):
                return _down_error(base, _mode(ctx))

            state = client.get_json(base, f"/api/studio/{sid}/state", timeout=30)
            if not (state or {}).get("download_url"):
                return json.dumps({
                    "error": "Video not ready yet — no final cut on the server.",
                    "session_id": sid,
                    "queue": (state or {}).get("queue"),
                    "next": "Poll scenemux_status until download_url appears, then re-run scenemux_export.",
                })

            dest_dir = _data_dir() / sid
            dest_dir.mkdir(parents=True, exist_ok=True)
            title = str((state or {}).get("track_title") or "scenemux").strip()
            safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in title)[:60] or "track"
            master = dest_dir / f"{safe}_master.mp4"
            size = client.download_file(base, f"/api/download/{sid}", master, timeout=900)

            out: Dict[str, Any] = {
                "session_id": sid,
                "master": str(master),
                "size_bytes": size,
            }

            if bool(args.get("vertical", False)):
                vert = _call(ctx, args, "/api/studio/export-vertical", {"session_id": sid}, timeout=900)
                session_dir = _repo_root(ctx) / "outputs" / sid
                vfiles = []
                if session_dir.is_dir():
                    vfiles = sorted(
                        session_dir.glob("*_vertical.mp4"),
                        key=lambda p: p.stat().st_mtime,
                        reverse=True,
                    )
                if vfiles:
                    vdest = dest_dir / f"{safe}_vertical.mp4"
                    shutil.copyfile(vfiles[0], vdest)
                    out["vertical"] = str(vdest)
                else:
                    out["vertical"] = (
                        "export-vertical succeeded on the server, but the outputs volume "
                        "is not visible to the plugin (remote server). Server response: "
                        + str(vert)[:300]
                    )

            out["next"] = "Hand the file path(s) to the user."
            return json.dumps(out)
        except SceneMuxError as e:
            return json.dumps({"error": str(e), "session_id": args.get("session_id")})
        except Exception as e:  # noqa: BLE001
            return json.dumps({"error": f"scenemux_export failed: {e}"})

    return handler


def make_cancel(ctx) -> Callable[..., str]:
    def handler(args: dict, **kwargs) -> str:
        try:
            sid = str(args.get("session_id") or "")
            if not sid:
                return json.dumps({"error": "session_id is required."})
            res = _call(ctx, args, "/api/studio/cancel-render", {"session_id": sid}, timeout=30)
            return json.dumps({"session_id": sid, "cancel_requested": True, "response": res})
        except SceneMuxError as e:
            return json.dumps({"error": str(e), "session_id": args.get("session_id")})
        except Exception as e:  # noqa: BLE001
            return json.dumps({"error": f"scenemux_cancel failed: {e}"})

    return handler
