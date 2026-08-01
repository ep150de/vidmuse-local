#!/usr/bin/env python3
"""
VidMuse first-run TUI setup wizard.

No extra dependencies — Python 3.9+ stdlib only.
Guides new users through local vs cloud mode, API keys,
dependency checks, and writing .env.
"""
from __future__ import annotations

import os
import platform
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Dict, List, Optional, Tuple

# Repo root = parent of scripts/
ROOT = Path(__file__).resolve().parent.parent
ENV_PATH = ROOT / ".env"
ENV_EXAMPLE = ROOT / ".env.example"
MARKER = ROOT / ".vidmuse-setup-complete"
SERVER_DIR = ROOT / "server"
CLIENT_DIR = ROOT / "client"
SCRIPTS_DIR = Path(__file__).resolve().parent


def detect_os() -> str:
    """Return linux | macos | windows | unknown"""
    sysname = platform.system().lower()
    if sysname == "darwin":
        return "macos"
    if sysname == "windows":
        return "windows"
    if sysname == "linux":
        return "linux"
    return "unknown"


def enable_windows_ansi() -> None:
    if os.name != "nt":
        return
    try:
        import ctypes

        kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
        handle = kernel32.GetStdHandle(-11)
        mode = ctypes.c_uint32()
        if kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
            kernel32.SetConsoleMode(handle, mode.value | 0x0004)
    except Exception:
        pass


# ─── Terminal helpers ────────────────────────────────────────────────

CSI = "\033["
RESET = f"{CSI}0m"
BOLD = f"{CSI}1m"
DIM = f"{CSI}2m"
CYAN = f"{CSI}36m"
GREEN = f"{CSI}32m"
YELLOW = f"{CSI}33m"
RED = f"{CSI}31m"
MAGENTA = f"{CSI}35m"


def color_enabled() -> bool:
    return sys.stdout.isatty() and os.getenv("NO_COLOR") is None


def c(text: str, *codes: str) -> str:
    if not color_enabled():
        return text
    return "".join(codes) + text + RESET


def clear() -> None:
    if sys.stdout.isatty():
        print("\033[2J\033[H", end="")


def hr() -> None:
    print(c("─" * 56, DIM))


def title(text: str) -> None:
    print()
    print(c(f"  {text}", BOLD, CYAN))
    hr()


def info(text: str) -> None:
    print(c(f"  ℹ  {text}", DIM))


def ok(text: str) -> None:
    print(c(f"  ✓  {text}", GREEN))


def warn(text: str) -> None:
    print(c(f"  !  {text}", YELLOW))


def err(text: str) -> None:
    print(c(f"  ✗  {text}", RED))


def pause(msg: str = "Press Enter to continue…") -> None:
    try:
        input(c(f"\n  {msg}", DIM))
    except EOFError:
        print()


def ask(prompt: str, default: Optional[str] = None, secret: bool = False) -> str:
    suffix = f" [{default}]" if default is not None and default != "" else ""
    label = c(f"  › {prompt}{suffix}: ", BOLD)
    try:
        if secret:
            import getpass

            # getpass doesn't show the prompt styling well on all TTYs; print first
            print(label, end="", flush=True)
            value = getpass.getpass("")
        else:
            value = input(label)
    except EOFError:
        if default is not None:
            return default
        raise SystemExit(
            "\n  Setup cancelled (no interactive input). Re-run in a terminal: ./setup\n"
        )
    value = value.strip()
    if not value and default is not None:
        return default
    return value


def choose(prompt: str, options: List[Tuple[str, str]], default: str = "1") -> str:
    """options: list of (id, label). Returns selected id."""
    print(c(f"\n  {prompt}", BOLD))
    for i, (_oid, label) in enumerate(options, 1):
        marker = c("*", CYAN) if str(i) == default else " "
        print(f"    {marker} {c(str(i), BOLD)}. {label}")
    while True:
        raw = ask("Choice", default=default)
        if raw.isdigit():
            idx = int(raw)
            if 1 <= idx <= len(options):
                return options[idx - 1][0]
        for oid, label in options:
            if raw.lower() == oid.lower() or raw.lower() == label.lower():
                return oid
        warn("Please enter a number from the list.")


def yes_no(prompt: str, default: bool = True) -> bool:
    d = "Y/n" if default else "y/N"
    raw = ask(f"{prompt} ({d})", default="y" if default else "n").lower()
    if not raw:
        return default
    return raw in ("y", "yes", "1", "true")


# ─── System detection ────────────────────────────────────────────────

def is_chromebook() -> bool:
    paths = [
        "/etc/lsb-release",
        "/opt/google/cros-containers",
        "/mnt/chromeos",
    ]
    for p in paths:
        if Path(p).exists():
            if "chrome" in p or "cros" in p:
                return True
    try:
        text = Path("/etc/lsb-release").read_text(errors="ignore").lower()
        if "chromebook" in text or "chromeos" in text or "cros" in text:
            return True
    except OSError:
        pass
    # Linux inside ChromeOS often reports chromeOS in uname or env
    if "CHROME_DESKTOP" in os.environ or Path("/dev/.cros_mnt").exists():
        return True
    return False


def which(cmd: str) -> Optional[str]:
    return shutil.which(cmd)


def run(cmd: List[str], cwd: Optional[Path] = None, timeout: int = 600) -> Tuple[int, str]:
    try:
        proc = subprocess.run(
            cmd,
            cwd=str(cwd) if cwd else None,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        out = (proc.stdout or "") + (proc.stderr or "")
        return proc.returncode, out.strip()
    except FileNotFoundError:
        return 127, f"not found: {cmd[0]}"
    except subprocess.TimeoutExpired:
        return 124, "timed out"


def python_cmd() -> str:
    """Prefer current interpreter; fall back to python3/python on PATH."""
    return sys.executable or which("python3") or which("python") or "python3"


def check_deps() -> List[Dict[str, str]]:
    rows = []
    # On Windows the binary is usually "python"; elsewhere "python3"
    py_bin = "python" if os.name == "nt" else "python3"
    if not which(py_bin) and which("python"):
        py_bin = "python"
    if not which(py_bin) and which("python3"):
        py_bin = "python3"

    checks = [
        (py_bin, "Python 3", [py_bin, "--version"]),
        ("docker", "Docker", ["docker", "--version"]),
        ("ffmpeg", "FFmpeg", ["ffmpeg", "-version"]),
        ("node", "Node.js", ["node", "--version"]),
        ("npm", "npm", ["npm", "--version"]),
    ]
    # Deduplicate if python and python3 collide
    seen = set()
    for binary, label, cmd in checks:
        if binary in seen:
            continue
        seen.add(binary)
        path = which(binary)
        if not path:
            rows.append({"id": binary if binary != "python" else "python3", "label": label, "status": "missing", "detail": ""})
            continue
        code, out = run(cmd, timeout=10)
        version = out.splitlines()[0] if out else ""
        rows.append({
            "id": "python3" if binary in ("python", "python3") else binary,
            "label": label,
            "status": "ok" if code == 0 else "error",
            "detail": version[:80],
        })
    return rows


def install_hints_for(os_id: str) -> Dict[str, str]:
    common_docker = "https://docs.docker.com/get-docker/"
    if os_id == "macos":
        return {
            "python3": "brew install python   OR download from https://python.org",
            "docker": f"Install Docker Desktop · {common_docker}",
            "ffmpeg": "brew install ffmpeg",
            "node": "brew install node   OR https://nodejs.org (LTS)",
            "npm": "Comes with Node.js (brew install node)",
        }
    if os_id == "windows":
        return {
            "python3": "winget install Python.Python.3.12   OR https://python.org (check Add to PATH)",
            "docker": f"Install Docker Desktop · {common_docker}",
            "ffmpeg": "winget install Gyan.FFmpeg   OR choco install ffmpeg",
            "node": "winget install OpenJS.NodeJS.LTS   OR https://nodejs.org",
            "npm": "Comes with Node.js",
        }
    # linux default (incl. Chromebook / Debian)
    return {
        "python3": "sudo apt install python3 python3-venv python3-pip   (or dnf/pacman equivalent)",
        "docker": f"{common_docker}  · or: sudo apt install docker.io docker-compose-v2",
        "ffmpeg": "sudo apt install ffmpeg",
        "node": "sudo apt install nodejs npm   OR https://nodejs.org (LTS)",
        "npm": "Usually installed with Node.js",
    }


def run_os_dep_installer(os_id: str) -> None:
    """Optional helper scripts for quick OS package installs."""
    if os_id == "linux":
        script = SCRIPTS_DIR / "install_deps_linux.sh"
        if script.exists() and yes_no("Run Linux quick dependency helper (apt/dnf/pacman)?", default=False):
            code, out = run(["bash", str(script)], timeout=900)
            print(c(out[-600:], DIM) if out else "")
            ok("Linux helper finished") if code == 0 else warn("Linux helper exited with errors")
    elif os_id == "macos":
        script = SCRIPTS_DIR / "install_deps_macos.sh"
        if script.exists() and yes_no("Run macOS quick dependency helper (Homebrew)?", default=False):
            code, out = run(["bash", str(script)], timeout=900)
            print(c(out[-600:], DIM) if out else "")
            ok("macOS helper finished") if code == 0 else warn("macOS helper exited with errors")
    elif os_id == "windows":
        script = SCRIPTS_DIR / "install_deps_windows.ps1"
        if script.exists() and yes_no("Run Windows quick dependency helper (winget)?", default=False):
            code, out = run(
                ["powershell", "-ExecutionPolicy", "Bypass", "-File", str(script)],
                timeout=900,
            )
            print(c(out[-600:], DIM) if out else "")
            ok("Windows helper finished") if code == 0 else warn("Windows helper exited with errors")


def http_probe(url: str, headers: Optional[Dict[str, str]] = None, timeout: int = 8) -> Tuple[bool, str]:
    req = urllib.request.Request(url, headers=headers or {}, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return True, f"HTTP {resp.status}"
    except urllib.error.HTTPError as e:
        # Auth endpoints may 401 without key — still "reachable"
        if e.code in (401, 403):
            return True, f"reachable (HTTP {e.code})"
        return False, f"HTTP {e.code}"
    except Exception as e:
        return False, str(e)


# ─── .env writing ────────────────────────────────────────────────────

def load_example_env() -> Dict[str, str]:
    values: Dict[str, str] = {}
    if not ENV_EXAMPLE.exists():
        return values
    for line in ENV_EXAMPLE.read_text(encoding="utf-8").splitlines():
        s = line.strip()
        if not s or s.startswith("#") or "=" not in s:
            continue
        key, _, val = s.partition("=")
        values[key.strip()] = val.strip()
    return values


def write_env(config: Dict[str, str]) -> None:
    base = load_example_env()
    base.update(config)

    lines = [
        "# Generated by VidMuse setup wizard",
        f"# {time.strftime('%Y-%m-%d %H:%M:%S')}",
        "",
        f"VIDMUSE_MODE={base.get('VIDMUSE_MODE', 'cloud')}",
        "",
        "SERVER_HOST=0.0.0.0",
        f"SERVER_PORT={base.get('SERVER_PORT', '8000')}",
        "OUTPUT_DIR=./outputs",
        "",
        f"OLLAMA_API_KEY={base.get('OLLAMA_API_KEY', '')}",
        f"OLLAMA_MODEL={base.get('OLLAMA_MODEL', 'gemma4:31b')}",
        f"LLM_PROVIDER={base.get('LLM_PROVIDER', 'ollama')}",
        f"LLM_FALLBACK={base.get('LLM_FALLBACK', 'opencode-go')}",
        "",
        f"OPENCODE_API_KEY={base.get('OPENCODE_API_KEY', '')}",
        f"OPENCODE_BASE_URL={base.get('OPENCODE_BASE_URL', 'https://opencode.ai/zen/go/v1')}",
        f"OPENCODE_MODEL={base.get('OPENCODE_MODEL', 'deepseek-v4-flash')}",
        "",
        f"COMFYUI_BACKEND={base.get('COMFYUI_BACKEND', 'cloud')}",
        f"COMFYUI_URL={base.get('COMFYUI_URL', 'http://localhost:8188')}",
        f"COMFY_CLOUD_URL={base.get('COMFY_CLOUD_URL', 'https://cloud.comfy.org')}",
        f"COMFY_CLOUD_API_KEY={base.get('COMFY_CLOUD_API_KEY', '')}",
        "",
    ]
    if base.get("VIDMUSE_MODE") == "local":
        lines.insert(4, "OLLAMA_URL=http://localhost:11434")
    else:
        lines.insert(4, "OLLAMA_URL=https://ollama.com")

    ENV_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")
    try:
        ENV_PATH.chmod(0o600)
    except OSError:
        pass


def mark_complete() -> None:
    MARKER.write_text(
        f"completed={time.strftime('%Y-%m-%dT%H:%M:%S')}\nmode={os.getenv('VIDMUSE_MODE', '')}\n",
        encoding="utf-8",
    )


# ─── Wizard steps ────────────────────────────────────────────────────

def welcome() -> None:
    clear()
    title("Welcome to VidMuse")
    os_id = detect_os()
    os_labels = {"linux": "Linux", "macos": "macOS", "windows": "Windows", "unknown": "Unknown OS"}
    print(
        c(
            f"""
  This wizard sets up VidMuse for first-time use on {os_labels.get(os_id, os_id)}.

  You will:
    1. Choose local GPU or cloud (Chromebook-friendly)
    2. Enter API keys (cloud mode)
    3. Check / install OS dependencies
    4. Write your .env config

  Launchers:
    Linux / macOS / ChromeOS  →  ./setup
    Windows                   →  setup.bat
""",
            DIM,
        )
    )
    chrome = is_chromebook()
    if chrome:
        ok("ChromeOS / Chromebook detected — cloud mode is recommended.")
    else:
        info(f"System: {os_labels.get(os_id, os_id)} · {platform.machine()} · Python {platform.python_version()}")
    pause()


def step_mode(chromebook: bool) -> str:
    clear()
    title("Step 1 — Choose runtime mode")
    print(
        """
  cloud  — Ollama Cloud + Comfy Cloud (no local GPU needed)
           Best for Chromebooks and laptops without a big GPU.

  local  — Ollama + ComfyUI on this machine (needs GPU + VRAM)
           Best for a workstation like the B70.
"""
    )
    default = "cloud" if chromebook else "local"
    return choose(
        "How do you want to run VidMuse?",
        [
            ("cloud", "Cloud mode (recommended for Chromebook / no GPU)"),
            ("local", "Local mode (Ollama + ComfyUI on this machine)"),
        ],
        default="1" if default == "cloud" else "2",
    )


def step_cloud_keys() -> Dict[str, str]:
    clear()
    title("Step 2 — Cloud API keys")
    print(
        c(
            """
  Create free / trial keys at:

    Ollama Cloud   https://ollama.com/settings/keys
    OpenCode Go    https://opencode.ai  (LLM fallback when Ollama credits run out)
    Comfy Cloud    https://platform.comfy.org/profile/api-keys
                   (API requires Standard+ subscription)

  Keys are saved only to .env on this machine (not uploaded).
  Paste is hidden for privacy.
""",
            DIM,
        )
    )

    config: Dict[str, str] = {}

    for _attempt in range(5):
        config["OLLAMA_API_KEY"] = ask("Ollama Cloud API key", secret=True)
        if config["OLLAMA_API_KEY"]:
            break
        if yes_no("No Ollama key entered. Continue anyway?", default=False):
            break
        warn("Ollama key is required for cloud LLM. Try again.")
    else:
        if not config.get("OLLAMA_API_KEY"):
            warn("Continuing without an Ollama key — you can edit .env later.")

    config["OPENCODE_API_KEY"] = ask(
        "OpenCode Go API key (optional but recommended)", secret=True
    )
    if not config["OPENCODE_API_KEY"]:
        warn("Without OpenCode, there is no LLM fallback when Ollama credits run out.")

    for _attempt in range(5):
        config["COMFY_CLOUD_API_KEY"] = ask("Comfy Cloud API key", secret=True)
        if config["COMFY_CLOUD_API_KEY"]:
            break
        if yes_no("No Comfy key — image/video gen will fail. Continue?", default=False):
            break
        warn("Comfy Cloud key is required for the full cloud pipeline.")
    else:
        if not config.get("COMFY_CLOUD_API_KEY"):
            warn("Continuing without a Comfy key — you can edit .env later.")

    clear()
    title("Cloud models")
    info("Tip: deepseek-v4-flash/pro need Ollama Pro; free-tier defaults to gemma4:31b.")
    config["OLLAMA_MODEL"] = ask(
        "Ollama model (free-tier friendly default)",
        default="gemma4:31b",
    )
    config["OPENCODE_MODEL"] = ask("OpenCode fallback model", default="deepseek-v4-flash")
    use_fallback = yes_no(
        "Enable OpenCode as automatic LLM fallback? (needs OpenCode API key above)",
        default=bool(config.get("OPENCODE_API_KEY")),
    )
    config["LLM_FALLBACK"] = (
        "opencode-go" if use_fallback and config.get("OPENCODE_API_KEY") else "none"
    )
    config["LLM_PROVIDER"] = "ollama"
    config["COMFYUI_BACKEND"] = "cloud"
    return config


def step_local_config() -> Dict[str, str]:
    clear()
    title("Step 2 — Local mode settings")
    print(
        c(
            """
  Local mode expects:
    • Ollama running (default http://localhost:11434)
    • ComfyUI running (default http://localhost:8188)
    • FFmpeg installed
    • Enough VRAM for FLUX + Wan workflows

  You can start everything later with:  docker compose up -d
""",
            DIM,
        )
    )
    config: Dict[str, str] = {
        "LLM_PROVIDER": "ollama",
        "LLM_FALLBACK": "none",
        "COMFYUI_BACKEND": "local",
        "OLLAMA_API_KEY": "",
        "OPENCODE_API_KEY": "",
        "COMFY_CLOUD_API_KEY": "",
    }
    config["OLLAMA_MODEL"] = ask("Ollama model to use", default="qwen2.5:32b")
    engine = ask(
        "Comfy engine [local / ai-playground]",
        default="local",
    ).strip().lower()
    if engine in ("ai-playground", "ai_playground", "playground", "intel"):
        config["COMFYUI_BACKEND"] = "ai-playground"
        info(
            "Start Intel AI Playground's ComfyUI server before generating. "
            "It usually exposes http://localhost:8188."
        )
    else:
        config["COMFYUI_BACKEND"] = "local"
    config["COMFYUI_URL"] = ask("ComfyUI queue URL", default="http://localhost:8188")
    # Optional: still collect OpenCode as manual override later
    if yes_no("Also configure OpenCode Go as optional LLM fallback?", default=False):
        config["OPENCODE_API_KEY"] = ask("OpenCode Go API key", secret=True)
        config["OPENCODE_MODEL"] = ask("OpenCode model", default="deepseek-v4-flash")
        config["LLM_FALLBACK"] = "opencode-go" if config["OPENCODE_API_KEY"] else "none"
    return config


def step_deps(mode: str) -> None:
    clear()
    title("Step 3 — Dependency check")
    os_id = detect_os()
    info(f"Detected platform: {os_id}")
    rows = check_deps()
    for row in rows:
        if row["status"] == "ok":
            ok(f"{row['label']}: {row['detail']}")
        elif row["status"] == "missing":
            warn(f"{row['label']}: not found")
        else:
            err(f"{row['label']}: {row['detail']}")

    print()
    if mode == "cloud":
        info("Cloud mode needs: Python 3, ffmpeg (for final edit), node/npm (UI).")
        info("Docker is optional — you can also run uvicorn directly.")
    else:
        info("Local mode needs: docker (recommended) or local Ollama+ComfyUI, ffmpeg, node/npm.")

    missing = [r for r in rows if r["status"] != "ok"]
    if missing and yes_no("Show install hints for missing tools?", default=True):
        print()
        hints = install_hints_for(os_id)
        for row in missing:
            warn(f"{row['label']}: {hints.get(row['id'], 'see README')}")

    if missing:
        run_os_dep_installer(os_id)

    # Python server deps
    py = python_cmd()
    if which(Path(py).name) or Path(py).exists():
        if yes_no("Install Python server packages now? (pip / venv)", default=True):
            print()
            info("Installing server/requirements.txt …")
            venv = SERVER_DIR / ".venv"
            if not venv.exists():
                code, out = run([py, "-m", "venv", str(venv)], timeout=120)
                if code != 0:
                    warn(f"venv create failed: {out[:200]}")
                else:
                    ok(f"Created {venv}")
            venv_py = venv / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
            if venv_py.exists():
                run([str(venv_py), "-m", "pip", "install", "-U", "pip"], timeout=180)
                code, out = run(
                    [str(venv_py), "-m", "pip", "install", "-r", "requirements.txt"],
                    cwd=SERVER_DIR,
                    timeout=600,
                )
                if code == 0:
                    ok("Server Python packages installed")
                else:
                    warn("pip install had issues — you may need: python -m ensurepip")
                    print(c(out[-400:], DIM))
            else:
                warn("Could not locate venv python; skip pip install for now.")

    # Client npm
    if which("npm") and CLIENT_DIR.exists():
        if yes_no("Install client npm packages now?", default=True):
            info("Running npm install in client/ …")
            code, out = run(["npm", "install"], cwd=CLIENT_DIR, timeout=600)
            if code == 0:
                ok("Client packages installed")
            else:
                warn("npm install failed")
                print(c(out[-400:], DIM))

    # Outputs dir
    (ROOT / "outputs").mkdir(exist_ok=True)
    ok("Created outputs/ directory")
    pause()


def step_validate(mode: str, config: Dict[str, str]) -> None:
    clear()
    title("Step 4 — Quick connectivity checks")
    if mode == "cloud":
        checks = [
            ("Ollama Cloud", "https://ollama.com/api/tags", {"Authorization": f"Bearer {config.get('OLLAMA_API_KEY', '')}"} if config.get("OLLAMA_API_KEY") else None),
            ("Comfy Cloud", "https://cloud.comfy.org/api/user", {"X-API-Key": config.get("COMFY_CLOUD_API_KEY", "")} if config.get("COMFY_CLOUD_API_KEY") else None),
        ]
        if config.get("OPENCODE_API_KEY"):
            checks.append((
                "OpenCode Go",
                "https://opencode.ai/zen/go/v1/models",
                {"Authorization": f"Bearer {config['OPENCODE_API_KEY']}"},
            ))
        for name, url, headers in checks:
            info(f"Probing {name}…")
            good, detail = http_probe(url, headers=headers)
            if good:
                ok(f"{name}: {detail}")
            else:
                warn(f"{name}: {detail} (you can fix keys later in .env)")
    else:
        for name, url in (
            ("Ollama", "http://localhost:11434/api/tags"),
            ("ComfyUI", "http://localhost:8188/system_stats"),
        ):
            info(f"Probing {name}…")
            good, detail = http_probe(url)
            if good:
                ok(f"{name}: {detail}")
            else:
                warn(f"{name}: not reachable yet ({detail})")
                info("Start services later with docker compose up -d")
    pause()


def step_write(mode: str, config: Dict[str, str]) -> None:
    clear()
    title("Step 5 — Save configuration")
    config = dict(config)
    config["VIDMUSE_MODE"] = mode
    if mode == "cloud":
        config.setdefault("COMFYUI_BACKEND", "cloud")
        config.setdefault("LLM_PROVIDER", "ollama")
    else:
        config.setdefault("COMFYUI_BACKEND", "local")

    if ENV_PATH.exists():
        if not yes_no(f".env already exists at {ENV_PATH}. Overwrite?", default=False):
            warn("Left existing .env unchanged.")
            return

    write_env(config)
    ok(f"Wrote {ENV_PATH}")
    mark_complete()
    ok("Marked setup complete")


def step_finish(mode: str) -> None:
    clear()
    title("Setup complete")
    venv_py = SERVER_DIR / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    print(
        c(
            f"""
  Mode: {mode}

  Next steps:
""",
            BOLD,
        )
    )
    if mode == "cloud":
        print(
            """
  Start the server (pick one):

    A) Docker
         docker compose -f docker-compose.cloud.yml up -d --build

    B) Direct
         cd server && source .venv/bin/activate   # if venv was created
         cd ..   # back to repo root
         PYTHONPATH=. uvicorn server.main:app --host 0.0.0.0 --port 8000

  Then start the UI:

         cd client
         VITE_SERVER_URL=http://localhost:8000 npm run dev

  Open http://localhost:5173 in your browser.
"""
        )
    else:
        print(
            """
  Start GPU stack + server:

         docker compose up -d
         docker exec vidmuse-ollama ollama pull qwen2.5:32b

  Start the UI:

         cd client
         VITE_SERVER_URL=http://localhost:8000 npm run dev
"""
        )

    print(c(f"  Config file: {ENV_PATH}", DIM))
    print(c("  Re-run anytime:  ./setup   or   python3 scripts/setup_wizard.py", DIM))
    print()

    if yes_no("Try starting the API server now?", default=False):
        env = os.environ.copy()
        # Load .env into child process environment lightly
        if ENV_PATH.exists():
            for line in ENV_PATH.read_text().splitlines():
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, _, v = line.partition("=")
                env[k.strip()] = v.strip()
        # server.* imports require the repo root on PYTHONPATH
        env["PYTHONPATH"] = str(ROOT) + (
            os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else ""
        )
        py = str(venv_py) if venv_py.exists() else sys.executable
        info("Starting uvicorn on :8000 (Ctrl+C to stop)…")
        try:
            subprocess.run(
                [
                    py,
                    "-m",
                    "uvicorn",
                    "server.main:app",
                    "--host",
                    "0.0.0.0",
                    "--port",
                    "8000",
                ],
                cwd=str(ROOT),
                env=env,
            )
        except KeyboardInterrupt:
            print()
            ok("Server stopped")


def already_configured() -> bool:
    return ENV_PATH.exists() or MARKER.exists()


def main(argv: Optional[List[str]] = None) -> int:
    argv = argv or sys.argv[1:]
    enable_windows_ansi()

    if "--help" in argv or "-h" in argv:
        print(
            "VidMuse setup wizard (Linux / macOS / Windows)\n\n"
            "Usage:\n"
            "  ./setup [--force]                 # Linux, macOS, ChromeOS\n"
            "  setup.bat [--force]               # Windows\n"
            "  python3 scripts/setup_wizard.py\n\n"
            "OS dependency helpers:\n"
            "  scripts/install_deps_linux.sh\n"
            "  scripts/install_deps_macos.sh\n"
            "  scripts/install_deps_windows.ps1\n\n"
            "  --force, -f   Re-run even if .env / setup marker already exists\n"
            "  --help,  -h   Show this help\n"
        )
        return 0

    if not sys.stdin.isatty():
        err("This wizard needs an interactive terminal.")
        info("Run: ./setup")
        return 1

    force = "--force" in argv or "-f" in argv

    if already_configured() and not force:
        clear()
        title("VidMuse setup")
        ok("Looks like setup was already done (.env or marker present).")
        print(c(f"\n  .env:    {ENV_PATH} {'(exists)' if ENV_PATH.exists() else '(missing)'}", DIM))
        print(c(f"  marker:  {MARKER} {'(exists)' if MARKER.exists() else '(missing)'}", DIM))
        if not yes_no("\nRun the wizard again anyway?", default=False):
            info("Exiting. Use ./setup --force to skip this prompt.")
            return 0

    chromebook = is_chromebook()
    welcome()
    mode = step_mode(chromebook)

    if mode == "cloud":
        config = step_cloud_keys()
    else:
        config = step_local_config()

    step_deps(mode)
    step_validate(mode, config)
    step_write(mode, config)
    step_finish(mode)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print()
        warn("Setup cancelled.")
        raise SystemExit(130)
