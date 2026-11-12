"""VidMuse Local — Server (B70 Workstation)

FastAPI backend serving LLM, image gen, video gen, and audio analysis over LAN.

New architecture (v2):
- llama.cpp backend (preferred) + Ollama fallback via server/llm/
- Hermes Director for autonomous music video generation
- Simplified API: single /api/pipeline/start endpoint
- WebSocket progress streaming for real-time updates
"""

import os
from pathlib import Path

# Load .env BEFORE importing LLM backends (they read API keys at import/init).
try:
    from dotenv import load_dotenv

    load_dotenv(Path(__file__).resolve().parents[1] / ".env", override=True)
    load_dotenv(Path(__file__).resolve().parent / ".env", override=True)
except ImportError:
    pass

# SciPy 1.15+ removed scipy.signal.hann — patch before any librosa import chain.
import scipy.signal  # noqa: E402
import scipy.signal.windows as _scipy_windows  # noqa: E402

scipy.signal.hann = getattr(scipy.signal, "hann", None) or _scipy_windows.hann  # type: ignore[attr-defined]
scipy.signal.hanning = getattr(scipy.signal, "hanning", None) or _scipy_windows.hann  # type: ignore[attr-defined]

import uuid
import asyncio
import json
import tempfile
import shutil
from datetime import datetime
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, UploadFile, File, Form, WebSocket, WebSocketDisconnect, HTTPException
from fastapi.responses import FileResponse, StreamingResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

# ─── Imports from new modules ───────────────────────────────────────────
from server.llm.base import LLMBackend
from server.llm.llm_factory import (
    create_llm_backend,
    health_check_all,
    set_active_backend,
    friendly_llm_error,
)
from server.llm.ollama_backend import CreditExhaustedError
from server.director.hermes_director import HermesDirector
from server.audio_analyzer import AudioAnalyzer
from server.image_generator import ComfyUIClient
from server.video_editor import VideoEditor
import logging

LOG = logging.getLogger("vidmuse.server")

# ─── CONFIG ─────────────────────────────────────────────────────────────
OLLAMA_URL = os.getenv("OLLAMA_URL", "http://localhost:11434")
COMFYUI_URL = os.getenv("COMFYUI_URL", "http://localhost:8188")
COMFY_CLOUD_URL = os.getenv("COMFY_CLOUD_URL", "https://cloud.comfy.org")
COMFYUI_BACKEND = (
    os.getenv("COMFYUI_BACKEND")
    or ("cloud" if (os.getenv("VIDMUSE_MODE") or "local").strip().lower() == "cloud" else "local")
).strip().lower()
VIDMUSE_MODE = (os.getenv("VIDMUSE_MODE") or "local").strip().lower()
SERVER_HOST = os.getenv("SERVER_HOST", "0.0.0.0")
SERVER_PORT = int(os.getenv("SERVER_PORT", 8000))
OUTPUT_DIR = Path(os.getenv("OUTPUT_DIR", "/tmp/vidmuse/outputs"))
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

# ─── APP ────────────────────────────────────────────────────────────────
app = FastAPI(title="SceneMux Server", version="2.0.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ─── GLOBALS ────────────────────────────────────────────────────────────
# WebSocket connections: session_id -> WebSocket
connected_clients: Dict[str, WebSocket] = {}

# Active pipeline instances: session_id -> HermesDirector
active_directors: Dict[str, HermesDirector] = {}

# Legacy interactive session state (client UI flow)
legacy_sessions: Dict[str, Dict[str, Any]] = {}

# LLM backend (set at startup / import)
llm_backend: Optional[LLMBackend] = None


def _init_llm_backend() -> Optional[LLMBackend]:
    """Create and register the active LLM backend once."""
    global llm_backend
    if llm_backend is not None:
        return llm_backend
    backend = create_llm_backend()
    llm_backend = backend
    if backend is not None:
        set_active_backend(backend)
    return backend


@app.on_event("startup")
async def _startup() -> None:
    backend = _init_llm_backend()
    if backend:
        print(f"[VidMuse] LLM backend: {backend.name} ({backend.model_name})")
    else:
        print(
            "[VidMuse] WARNING: No LLM backend available. "
            "For cloud mode set OLLAMA_API_KEY; for local install llama-cpp-python or start Ollama."
        )

# ─── MODELS ─────────────────────────────────────────────────────────────

class PipelineRequest(BaseModel):
    """Request for the /api/pipeline/start endpoint."""
    session_id: str
    text_direction: str
    reference_image_prompt: Optional[str] = None
    concept_index: Optional[int] = None

class ChatMessage(BaseModel):
    session_id: str
    role: str
    content: str
    phase: str = "idle"

# ─── HELPERS ────────────────────────────────────────────────────────────

async def _progress_callback(session_id: str, phase: str, progress: int, status: str) -> None:
    """Send progress update to connected WebSocket clients."""
    if session_id in connected_clients:
        try:
            await connected_clients[session_id].send_json({
                "type": "progress",
                "phase": phase,
                "progress": progress,
                "status": status,
                "timestamp": datetime.now().isoformat(),
            })
        except Exception:
            pass


async def _frame_ready_callback(
    session_id: str, index: int, frame: Dict[str, Any], total: int
) -> None:
    """Push a single storyboard frame to the client as soon as it finishes rendering."""
    if session_id in connected_clients:
        try:
            await connected_clients[session_id].send_json({
                "type": "storyboard_frame",
                "index": index,
                "frame": frame,
                "total": total,
                "timestamp": datetime.now().isoformat(),
            })
        except Exception:
            pass

def _get_director(session_id: str) -> HermesDirector:
    """Get or create a HermesDirector for the session."""
    if session_id not in active_directors:
        director = HermesDirector(
            llm_backend=llm_backend,
            output_dir=OUTPUT_DIR / session_id,
        )
        director.on_progress(lambda phase, progress, status: asyncio.create_task(
            _progress_callback(session_id, phase, progress, status)
        ))
        director.on_frame_ready(lambda idx, frame, total: asyncio.create_task(
            _frame_ready_callback(session_id, idx, frame, total)
        ))
        active_directors[session_id] = director
    return active_directors[session_id]


def _hydrate_director(director: HermesDirector, session: Dict[str, Any]) -> None:
    """Copy session artifacts onto the director instance."""
    if session.get("audio_analysis"):
        director.audio_analysis = session["audio_analysis"]
    if session.get("audio_path"):
        director.audio_path = session["audio_path"]
    if session.get("singer_profile"):
        director.singer_profile = session["singer_profile"]
    if session.get("video_backend"):
        director.video_backend = session["video_backend"]
    if session.get("video_model"):
        director.video_model = session["video_model"]
    if session.get("performance_mix"):
        director.performance_mix = session["performance_mix"]
    director.project_mode = session.get("project_mode") or "mtv"
    comfy_backend = session.get("comfy_backend")
    comfy_url = session.get("comfy_url")
    if comfy_backend == "cloud" and session.get("pipeline_video") != "fal":
        comfy_url = COMFY_CLOUD_URL
        session["comfy_url"] = COMFY_CLOUD_URL
    if comfy_backend and (
        director.comfy_backend != comfy_backend
        or (comfy_url and director.comfy_url != comfy_url.rstrip("/"))
    ):
        director.configure_comfy(comfy_backend, comfy_url)
    if session.get("reference_images"):
        director.reference_images = list(session["reference_images"])
    if session.get("identity_sheet_path"):
        director.identity_sheet_path = session["identity_sheet_path"]
    if session.get("timeline_plan"):
        director.timeline_plan = session["timeline_plan"]
    from server.pipeline import normalize_pipeline

    lanes = normalize_pipeline(session, {})
    director.configure_home_agent(
        session.get("home_agent_url") or "",
        session.get("home_agent_password")
        or os.getenv("HOME_AGENT_PASSWORD")
        or "",
        use_stills=lanes["pipeline_stills"] == "home_agent",
        use_video=lanes["pipeline_video"] == "home_agent",
    )
    director.configure_pipeline(
        director=lanes["pipeline_director"],
        stills=lanes["pipeline_stills"],
        video=lanes["pipeline_video"],
        video_model=lanes.get("video_model")
        or session.get("video_model")
        or "minimax-h3",
    )
    lyrics = (session.get("lyrics") or "").strip()
    if lyrics and isinstance(director.audio_analysis, dict):
        if not (director.audio_analysis.get("lyrics") or "").strip():
            director.audio_analysis["lyrics"] = lyrics


def _write_project_manifest(session_id: str, session: Dict[str, Any]) -> None:
    """Persist portable project state and asset provenance for later resume/QC."""
    session_dir = OUTPUT_DIR / session_id
    session_dir.mkdir(parents=True, exist_ok=True)
    plan = session.get("timeline_plan") or {}
    scenes = plan.get("scenes") or []
    ready = sum(
        1 for s in scenes if s.get("status") == "ready" and s.get("video_path")
    )
    payload = {
        "session_id": session_id,
        "status": session.get("status") or "created",
        "project_mode": session.get("project_mode") or "mtv",
        "creative_direction": session.get("creative_direction") or "",
        "lyrics": (session.get("lyrics") or "")[:8000],
        "singer_profile": session.get("singer_profile") or {},
        "video_model": session.get("video_model")
        or session.get("video_backend")
        or "minimax-h3",
        "video_backend": session.get("video_backend") or "minimax-h3",
        "identity_sheet_path": session.get("identity_sheet_path"),
        "reference_images": session.get("reference_images") or [],
        "final_video_path": session.get("final_video_path"),
        "comfy": {
            "backend": session.get("comfy_backend") or COMFYUI_BACKEND or "local",
            "url": session.get("comfy_url") or COMFYUI_URL,
        },
        "pipeline": {
            "director": session.get("pipeline_director") or "cloud_llm",
            "stills": session.get("pipeline_stills") or "comfy",
            "video": session.get("pipeline_video") or "comfy_cloud",
            "video_model": session.get("video_model")
            or session.get("video_backend")
            or "minimax-h3",
            "home_agent_url": session.get("home_agent_url") or "",
            "hermes_url": session.get("hermes_url") or "",
        },
        "audio": {
            "path": session.get("audio_path"),
            "analysis": {
                k: v
                for k, v in (session.get("audio_analysis") or {}).items()
                if k
                not in (
                    "beat_times",
                    "energy_profile",
                    "lyrics_srt",
                )
            },
            "coverage_timeline": (session.get("audio_analysis") or {}).get(
                "coverage_timeline"
            ),
        },
        "timeline": {
            "scene_count": len(scenes),
            "ready_count": ready,
            "review_status": plan.get("review_status"),
            "duration": plan.get("duration"),
            "character_lock": plan.get("character_lock") or plan.get("character"),
        },
        "selected_concept": session.get("selected_concept"),
        "visual_bible": session.get("visual_bible"),
        "concepts": session.get("concepts") or [],
        "storyboard": session.get("storyboard") or [],
        "technical_qc": session.get("technical_qc"),
    }
    (session_dir / "project.json").write_text(
        json.dumps(payload, indent=2, default=str),
        encoding="utf-8",
    )


def _refresh_plan_asset_urls(session_id: str, plan: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Rebuild still/video/identity URLs from on-disk paths after resume."""
    if not plan:
        return {"scenes": [], "duration": 0}
    session_dir = (OUTPUT_DIR / session_id).resolve()
    for sc in plan.get("scenes") or []:
        still = sc.get("still_path")
        if still:
            p = Path(still)
            if not p.is_file() and not p.is_absolute():
                p = Path(still)
            if p.is_file():
                try:
                    p.resolve().relative_to(session_dir)
                    sc["still_url"] = (
                        f"/api/session/{session_id}/asset/storyboard/{p.name}"
                    )
                except ValueError:
                    pass
        video = sc.get("video_path")
        if video:
            p = Path(video)
            if p.is_file():
                try:
                    p.resolve().relative_to(session_dir)
                    sc["video_url"] = (
                        f"/api/session/{session_id}/asset/video/{p.name}"
                    )
                    if sc.get("status") != "ready":
                        sc["status"] = "ready"
                except ValueError:
                    pass
    identity = plan.get("identity_sheet_path")
    if not identity:
        # Prefer session identity/ folder
        identity_dir = session_dir / "identity"
        if identity_dir.is_dir():
            for cand in sorted(identity_dir.glob("*")):
                if cand.suffix.lower() in (".png", ".jpg", ".jpeg", ".webp"):
                    identity = str(cand)
                    break
    if identity and Path(identity).is_file():
        plan["identity_sheet_path"] = identity
        plan["identity_sheet_url"] = (
            f"/api/session/{session_id}/asset/identity/{Path(identity).name}"
        )
    return plan


def _load_session_from_disk(session_id: str) -> Dict[str, Any]:
    """Hydrate an in-memory session from outputs/<session_id>/."""
    session_dir = OUTPUT_DIR / session_id
    if not session_dir.is_dir():
        raise HTTPException(status_code=404, detail="Project not found on disk")

    manifest: Dict[str, Any] = {}
    manifest_path = session_dir / "project.json"
    if manifest_path.is_file():
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except Exception:
            manifest = {}

    audio_path = None
    for name in ("audio.mp3", "audio.wav", "audio.m4a", "audio.flac"):
        cand = session_dir / name
        if cand.is_file():
            audio_path = str(cand)
            break
    if not audio_path:
        recorded = (manifest.get("audio") or {}).get("path")
        if recorded and Path(recorded).is_file():
            audio_path = recorded
        elif recorded:
            # Relative path from repo root
            rel = Path(recorded)
            if rel.is_file():
                audio_path = str(rel.resolve())

    analysis = (manifest.get("audio") or {}).get("analysis") or {}
    if not analysis and audio_path:
        try:
            analysis = AudioAnalyzer.analyze(audio_path)
        except Exception:
            analysis = {"duration": 0, "track_title": session_id[:8]}

    plan = None
    plan_path = session_dir / "timeline_plan.json"
    if plan_path.is_file():
        try:
            plan = json.loads(plan_path.read_text(encoding="utf-8"))
        except Exception:
            plan = None
    if plan:
        plan = _refresh_plan_asset_urls(session_id, plan)

    pipeline = manifest.get("pipeline") or {}
    comfy = manifest.get("comfy") or {}
    refs = list(manifest.get("reference_images") or [])
    ref_dir = session_dir / "references"
    if ref_dir.is_dir():
        for p in sorted(ref_dir.glob("*")):
            if p.suffix.lower() in (".png", ".jpg", ".jpeg", ".webp") and str(p) not in refs:
                refs.append(str(p))

    identity_path = manifest.get("identity_sheet_path")
    if plan and plan.get("identity_sheet_path"):
        identity_path = plan["identity_sheet_path"]
    if not identity_path:
        identity_dir = session_dir / "identity"
        if identity_dir.is_dir():
            for cand in sorted(identity_dir.glob("*")):
                if cand.suffix.lower() in (".png", ".jpg", ".jpeg", ".webp"):
                    identity_path = str(cand)
                    break

    finals = list(session_dir.glob("vidmuse_*.mp4")) + list(
        session_dir.glob("*_final.mp4")
    )
    final_path = manifest.get("final_video_path")
    if not final_path or not Path(str(final_path)).is_file():
        final_path = str(finals[0]) if finals else None

    from server.pipeline import normalize_video_model

    video_model = normalize_video_model(
        manifest.get("video_model")
        or pipeline.get("video_model")
        or manifest.get("video_backend")
        or "minimax-h3"
    )

    session: Dict[str, Any] = {
        "session_id": session_id,
        "status": manifest.get("status")
        or ("done" if final_path else ("review" if plan else "analyzed")),
        "chat_history": [],
        "audio_path": audio_path,
        "audio_analysis": analysis,
        "reference_images": refs,
        "concepts": manifest.get("concepts") or [],
        "selected_concept": manifest.get("selected_concept"),
        "storyboard": manifest.get("storyboard") or [],
        "concept_prompt": manifest.get("creative_direction") or "",
        "creative_direction": manifest.get("creative_direction") or "",
        "lyrics": manifest.get("lyrics")
        or (analysis.get("lyrics") if isinstance(analysis, dict) else "")
        or "",
        "singer_profile": manifest.get("singer_profile") or {},
        "video_backend": video_model
        if video_model in ("minimax-h3", "ltx23", "fasth3")
        else "minimax-h3",
        "video_model": video_model,
        "project_mode": manifest.get("project_mode") or "mtv",
        "performance_mix": "performance",
        "comfy_backend": comfy.get("backend") or COMFYUI_BACKEND,
        "comfy_url": comfy.get("url")
        or (COMFY_CLOUD_URL if COMFYUI_BACKEND == "cloud" else COMFYUI_URL),
        "pipeline_director": pipeline.get("director") or "cloud_llm",
        "pipeline_stills": pipeline.get("stills") or "comfy",
        "pipeline_video": pipeline.get("video") or "comfy_cloud",
        "home_agent_url": pipeline.get("home_agent_url") or "",
        "hermes_url": pipeline.get("hermes_url") or "",
        "timeline_plan": plan,
        "identity_sheet_path": identity_path,
        "final_video_path": final_path,
        "visual_bible": manifest.get("visual_bible"),
        "technical_qc": manifest.get("technical_qc"),
        "resumed_from_disk": True,
    }
    legacy_sessions[session_id] = session

    # Warm director so later studio calls see the plan
    director = _get_director(session_id)
    _hydrate_director(director, session)
    if plan:
        director.timeline_plan = plan
    if identity_path:
        director.identity_sheet_path = identity_path
    return session

# ─── WEBSOCKET MANAGER ─────────────────────────────────────────────────

@app.websocket("/api/ws/{client_id}")
async def websocket_endpoint(websocket: WebSocket, client_id: str):
    """WebSocket connection for real-time progress updates."""
    await websocket.accept()
    connected_clients[client_id] = websocket
    try:
        while True:
            data = await websocket.receive_text()
            await websocket.send_json({"type": "ack", "received": data})
    except WebSocketDisconnect:
        connected_clients.pop(client_id, None)

# ─── HEALTH & STATUS ───────────────────────────────────────────────────

@app.get("/api/health")
async def health():
    """Check server status and available services."""
    services = {}

    # Check LLM backend
    llm_status = {}
    if llm_backend:
        llm_status = await llm_backend.health()
    else:
        llm_status = await health_check_all()
    services["llm"] = llm_status

    # Check ComfyUI / Comfy Cloud
    try:
        from server.image_generator import ComfyUIClient

        services["comfyui"] = await ComfyUIClient(
            backend=COMFYUI_BACKEND,
            url=(
                COMFY_CLOUD_URL
                if COMFYUI_BACKEND == "cloud"
                else COMFYUI_URL
            ),
        ).health_check()
    except Exception as exc:
        services["comfyui"] = {"status": "down", "error": str(exc)}

    # Optional FAL draft host
    try:
        from server.fal_client import FalClient, fal_configured

        if fal_configured():
            services["fal"] = await FalClient().health()
        else:
            services["fal"] = {
                "status": "unconfigured",
                "ready": False,
                "note": "Set FAL_KEY for optional draft stills/clips (prepaid credits)",
            }
    except Exception as exc:
        services["fal"] = {"status": "down", "ready": False, "error": str(exc)}

    # Check disk space
    import shutil
    disk = shutil.disk_usage(str(OUTPUT_DIR))
    services["disk"] = {
        "total_gb": round(disk.total / (1024**3), 1),
        "used_gb": round(disk.used / (1024**3), 1),
        "free_gb": round(disk.free / (1024**3), 1),
    }

    mode = VIDMUSE_MODE
    return {
        "status": "healthy" if services["comfyui"].get("status") in ("up", "auth_required") else "degraded",
        "mode": mode,
        "services": services,
        "gpu": "cloud" if mode == "cloud" else "Intel Arc Pro B70",
        "vram_gb": None if mode == "cloud" else 32,
        "version": "2.2.0",
    }

@app.get("/api/llm/status")
async def llm_status():
    """Get LLM backend status."""
    status = {}
    if llm_backend:
        status["active"] = await llm_backend.health()
    else:
        status["active"] = {"status": "none", "message": "No LLM backend configured"}
    status["available"] = await health_check_all()
    return status


def _home_agent_http_status(exc: Exception) -> int:
    code = getattr(exc, "code", "") or ""
    if code == "unreachable":
        return 503
    if code == "auth":
        return 401
    return 400


@app.post("/api/home-agent/diagnose")
async def home_agent_diagnose(request: dict):
    """LAN diagnose for AI Playground Home Agent local-web (+ optional Comfy).

    Without password: TCP + HTTP. With password: also login + SSE.
    ``probe_comfy`` (default true) checks Playground Comfy on the same host :8188
    or ``comfy_url`` when provided.
    """
    from server.home_agent.client import HomeAgentClient, HomeAgentError

    url = str(
        request.get("url") or os.getenv("HOME_AGENT_URL") or ""
    ).strip().rstrip("/")
    # Trim ends only — local-web passphrases may contain spaces
    password = str(
        request.get("password") or os.getenv("HOME_AGENT_PASSWORD") or ""
    ).strip()
    if not url:
        raise HTTPException(status_code=400, detail="Home Agent URL required")
    probe_comfy = request.get("probe_comfy")
    if probe_comfy is None:
        probe_comfy = True
    comfy_url = str(request.get("comfy_url") or "").strip() or None
    client = HomeAgentClient(url, password)
    try:
        return await client.diagnose_lan(
            password=password,
            probe_comfy=bool(probe_comfy),
            comfy_url=comfy_url,
        )
    except HomeAgentError as exc:
        raise HTTPException(
            status_code=_home_agent_http_status(exc), detail=str(exc)
        ) from exc


@app.post("/api/home-agent/test")
async def home_agent_test(request: dict):
    """Login-only probe for AI Playground Home Agent LAN local-web.

    Does not affect Comfy Cloud / Local video backends.
    """
    from server.home_agent.client import HomeAgentClient, HomeAgentError

    url = str(
        request.get("url") or os.getenv("HOME_AGENT_URL") or ""
    ).strip().rstrip("/")
    password = str(
        request.get("password") or os.getenv("HOME_AGENT_PASSWORD") or ""
    ).strip()
    if not url:
        raise HTTPException(status_code=400, detail="Home Agent URL required")
    if not password:
        raise HTTPException(status_code=400, detail="Home Agent password required")
    client = HomeAgentClient(url, password)
    try:
        return await client.test_connection()
    except HomeAgentError as exc:
        raise HTTPException(
            status_code=_home_agent_http_status(exc), detail=str(exc)
        ) from exc
    except Exception as exc:
        detail = str(exc).strip() or (
            f"{type(exc).__name__}: cannot reach Home Agent at {url}"
        )
        raise HTTPException(status_code=503, detail=detail) from exc


@app.post("/api/home-agent/job")
async def home_agent_job(request: dict):
    """Phase-2: send an agentic job envelope to Home Agent (optional media return)."""
    from server.home_agent.client import HomeAgentClient, HomeAgentError
    from server.home_agent.jobs import run_media_job

    session_id = request.get("session_id")
    url = str(
        request.get("url") or os.getenv("HOME_AGENT_URL") or ""
    ).strip().rstrip("/")
    password = str(
        request.get("password") or os.getenv("HOME_AGENT_PASSWORD") or ""
    ).strip()
    job_type = str(request.get("job_type") or "still").strip()[:80]
    prompt = str(request.get("prompt") or "").strip()
    if not url or not password:
        raise HTTPException(
            status_code=400, detail="Home Agent URL and password required"
        )
    if not prompt:
        raise HTTPException(status_code=400, detail="prompt required")

    dest = OUTPUT_DIR / (session_id or "home_agent") / "home_agent"
    client = HomeAgentClient(url, password)
    try:
        result = await run_media_job(
            client,
            job_type=job_type,
            prompt=prompt,
            dest_dir=dest,
            extras=request.get("extras")
            if isinstance(request.get("extras"), dict)
            else None,
        )
        return {"session_id": session_id, **result}
    except HomeAgentError as exc:
        raise HTTPException(
            status_code=_home_agent_http_status(exc), detail=str(exc)
        ) from exc
    except Exception as exc:
        LOG.exception("Home Agent job failed")
        raise HTTPException(status_code=503, detail=str(exc)) from exc


# ─── PIPELINE START (MAIN ENDPOINT) ─────────────────────────────────────

@app.post("/api/pipeline/start")
async def start_pipeline(request: dict):
    """Start autonomous music video generation pipeline.

    This is the primary entry point for VidMuse Local.

    Request body:
        session_id (str): Unique session identifier
        audio (file): Uploaded MP3/audio file
        text_direction (str): Creative direction/brief for the video
        reference_images (List[file]): Optional reference images
        reference_image_prompt (str): Optional prompt to generate reference images
        concept_index (int): Optional concept selection (0-2), auto-selects if omitted
    """
    session_id = request.get("session_id", str(uuid.uuid4()))
    text_direction = request.get("text_direction", "")

    if not text_direction:
        return {"error": "text_direction is required"}

    session_dir = OUTPUT_DIR / session_id
    session_dir.mkdir(parents=True, exist_ok=True)

    # Save uploaded audio
    audio_path = None
    if "audio" in request.get("file", {}):
        file = request["file"]
        audio_path = session_dir / f"audio{Path(file.get('filename', 'audio')).suffix}"
        with open(audio_path, "wb") as f:
            shutil.copyfileobj(file.file, f)

    # Save reference images if provided
    ref_images = []
    if "reference_images" in request:
        refs_dir = session_dir / "references"
        refs_dir.mkdir(parents=True, exist_ok=True)
        for file in request["reference_images"]:
            img_path = refs_dir / file.filename
            with open(img_path, "wb") as f:
                shutil.copyfileobj(file.file, f)
            ref_images.append(str(img_path))

    # Create and run the director
    director = _get_director(session_id)
    director.on_progress(lambda phase, progress, status: asyncio.create_task(
        _progress_callback(session_id, phase, progress, status)
    ))

    concept_index = request.get("concept_index")

    # Run the pipeline
    result = await director.generate(
        audio_path=audio_path,
        text_direction=text_direction,
        reference_images=ref_images if ref_images else None,
        reference_image_prompt=request.get("reference_image_prompt"),
        concept_index=concept_index,
    )

    return {
        "session_id": session_id,
        **result,
    }

# ─── LEGACY ENDPOINTS (backward compat for client UI) ───────────────────

@app.post("/api/session/create")
async def create_session():
    """Create a new video generation session (legacy)."""
    session_id = str(uuid.uuid4())
    legacy_sessions[session_id] = {
        "session_id": session_id,
        "status": "created",
        "chat_history": [],
        "audio_path": None,
        "audio_analysis": None,
        "reference_images": [],
        "concepts": [],
        "selected_concept": None,
        "storyboard": [],
        "concept_prompt": "",
        "singer_profile": {},
        "video_backend": "minimax-h3",
        "project_mode": "mtv",
        "performance_mix": "hybrid",
        "comfy_backend": COMFYUI_BACKEND,
        "comfy_url": COMFY_CLOUD_URL if COMFYUI_BACKEND == "cloud" else COMFYUI_URL,
    }
    return {"session_id": session_id, "status": "created"}


@app.post("/api/session/config")
async def configure_session(request: dict):
    """Store singer profile + preferred video backend for a session."""
    session_id = request.get("session_id")
    if not session_id:
        return {"error": "session_id required"}
    session = legacy_sessions.setdefault(session_id, {"session_id": session_id})
    singer = request.get("singer_profile") or {}
    if isinstance(singer, dict):
        session["singer_profile"] = {
            "name": str(singer.get("name") or "").strip()[:80],
            "presentation": str(singer.get("presentation") or "").strip()[:80],
            "age_range": str(singer.get("age_range") or "").strip()[:40],
            "look": str(singer.get("look") or "").strip()[:240],
            "framing": str(singer.get("framing") or "close-up on face and head").strip()[:80],
        }
    from server.pipeline import normalize_video_model

    # Prefer explicit video_model; keep video_backend as legacy alias
    video_model = normalize_video_model(
        request.get("video_model")
        or request.get("video_backend")
        or session.get("video_model")
        or session.get("video_backend")
        or "minimax-h3"
    )
    backend = str(
        request.get("video_backend") or session.get("video_backend") or video_model
    ).strip().lower()
    if backend in ("minimax_h3", "h3"):
        backend = "minimax-h3"
    # Studio engines map onto video_backend for older code paths
    if backend not in (
        "infinitetalk",
        "ltx23",
        "humo",
        "minimax-h3",
        "fasth3",
        "fast-h3",
        "fast_h3",
    ):
        backend = video_model if video_model in (
            "minimax-h3",
            "ltx23",
            "fasth3",
        ) else "minimax-h3"
    if backend in ("fast-h3", "fast_h3"):
        backend = "fasth3"
    session["video_backend"] = backend
    session["video_model"] = video_model

    # Optional pasted lyrics (overrides / supplements Whisper later)
    pasted = str(request.get("lyrics") or "").strip()
    if pasted:
        from server.lyrics import compact_lyrics, infer_line_cues

        lyrics = compact_lyrics(pasted, max_chars=8000)
        session["lyrics"] = lyrics
        analysis = session.get("audio_analysis") or {}
        analysis["lyrics"] = lyrics
        analysis["lyrics_source"] = "user"
        duration = float(analysis.get("duration") or 0)
        existing_cues = analysis.get("lyrics_cues") or []
        # Rebuild proportional cues when user pastes fresh lyrics without timings
        if duration > 0 and (
            not existing_cues
            or analysis.get("lyrics_source") == "user"
        ):
            analysis["lyrics_cues"] = infer_line_cues(lyrics, duration)
        session["audio_analysis"] = analysis

    direction = str(request.get("creative_direction") or request.get("prompt") or "").strip()
    if direction:
        session["creative_direction"] = direction[:2000]
        # Prefer explicit direction over chat-derived concept_prompt
        session["concept_prompt"] = session["creative_direction"]

    mix = str(request.get("performance_mix") or session.get("performance_mix") or "hybrid")
    mix = mix.strip().lower()
    if mix in ("performance", "performance-first", "performance_first", "avatar"):
        mix = "performance"
    else:
        mix = "hybrid"
    session["performance_mix"] = mix

    project_mode = str(
        request.get("project_mode") or session.get("project_mode") or "mtv"
    ).strip().lower()
    session["project_mode"] = (
        "typography" if project_mode in ("typography", "lyric", "lyrics") else "mtv"
    )

    from server.pipeline import (
        comfy_backend_for_video,
        needs_comfy_url,
        needs_fal_key,
        needs_home_agent,
        normalize_pipeline,
    )

    # Optional named preset (cloud / playground / hybrid_fast / fast_draft / …)
    if request.get("pipeline_preset"):
        from server.pipeline import apply_preset

        preset = apply_preset(str(request.get("pipeline_preset") or ""))
        if preset:
            request = {**request, **preset}

    lanes = normalize_pipeline(session, request)
    # Prefer request video_model over preset when both present
    if request.get("video_model"):
        lanes["video_model"] = normalize_video_model(request.get("video_model"))
    session.update(lanes)
    session["video_model"] = lanes.get("video_model") or video_model
    # Legacy boolean mirrors for older clients
    session["use_home_agent_llm"] = lanes["pipeline_director"] == "home_agent"
    session["use_home_agent_stills"] = lanes["pipeline_stills"] == "home_agent"

    comfy_backend = comfy_backend_for_video(lanes["pipeline_video"])
    # Stills via Comfy on a LAN box while video is cloud still needs a URL only for stills
    if lanes["pipeline_video"] == "fal":
        comfy_backend = "cloud"
    elif lanes["pipeline_stills"] == "comfy" and lanes["pipeline_video"] == "comfy_cloud":
        comfy_backend = "cloud"
    elif lanes["pipeline_video"] == "comfy_local" or (
        lanes["pipeline_stills"] == "comfy" and lanes["pipeline_video"] == "home_agent"
    ):
        # Prefer explicit request backend when local/playground
        req_backend = str(
            request.get("comfy_backend") or session.get("comfy_backend") or "local"
        ).strip().lower()
        if req_backend in ("local", "ai-playground"):
            comfy_backend = req_backend
        else:
            comfy_backend = "local"

    if "home_agent_url" in request:
        session["home_agent_url"] = str(
            request.get("home_agent_url") or ""
        ).strip().rstrip("/")
    if "home_agent_password" in request:
        session["home_agent_password"] = str(
            request.get("home_agent_password") or ""
        ).strip()[:200]
    if "hermes_url" in request:
        session["hermes_url"] = str(request.get("hermes_url") or "").strip().rstrip("/")
    if "hermes_token" in request:
        session["hermes_token"] = str(request.get("hermes_token") or "").strip()[:500]

    if needs_home_agent(lanes):
        if not session.get("home_agent_url"):
            raise HTTPException(
                status_code=400,
                detail="Home Agent URL required for the selected pipeline lanes",
            )
        if not (
            session.get("home_agent_password") or os.getenv("HOME_AGENT_PASSWORD")
        ):
            raise HTTPException(
                status_code=400,
                detail="Home Agent password required for the selected pipeline lanes",
            )

    if needs_fal_key(lanes):
        from server.fal_client import fal_configured

        if not fal_configured():
            raise HTTPException(
                status_code=400,
                detail="FAL selected but FAL_KEY is not set in .env",
            )

    if comfy_backend == "cloud" or lanes["pipeline_video"] == "fal":
        comfy_url = COMFY_CLOUD_URL.rstrip("/")
        if lanes["pipeline_video"] == "comfy_cloud" and not os.getenv(
            "COMFY_CLOUD_API_KEY"
        ):
            raise HTTPException(
                status_code=400,
                detail="Comfy Cloud selected but COMFY_CLOUD_API_KEY is not set in .env",
            )
        if lanes["pipeline_video"] == "fal":
            # Still allow a local Comfy URL for stills when stills=comfy local
            if lanes["pipeline_stills"] == "comfy" and request.get("comfy_url"):
                comfy_url = str(request.get("comfy_url")).strip().rstrip("/")
    else:
        comfy_url = str(
            request.get("comfy_url") or session.get("comfy_url") or COMFYUI_URL
        ).strip().rstrip("/")
        if needs_comfy_url(lanes) or lanes["pipeline_stills"] == "comfy":
            if not comfy_url.startswith(("http://", "https://")):
                raise HTTPException(
                    status_code=400,
                    detail="Comfy URL must start with http:// or https://",
                )
    session["comfy_backend"] = comfy_backend
    session["comfy_url"] = comfy_url

    # Director LLM routing
    global llm_backend
    if lanes["pipeline_director"] == "home_agent" and session.get("home_agent_url"):
        from server.llm.home_agent_backend import HomeAgentBackend
        from server.llm.llm_factory import set_active_backend

        ha = HomeAgentBackend(
            base_url=session.get("home_agent_url"),
            password=session.get("home_agent_password")
            or os.getenv("HOME_AGENT_PASSWORD")
            or "",
        )
        set_active_backend(ha)
        llm_backend = ha
        LOG.info("LLM switched to Home Agent at %s", session.get("home_agent_url"))
        if session_id in active_directors:
            active_directors[session_id].llm = ha
    elif lanes["pipeline_director"] == "hermes":
        from server.llm.hermes_backend import HermesAgentBackend
        from server.llm.llm_factory import set_active_backend

        hermes = HermesAgentBackend(
            base_url=session.get("hermes_url") or os.getenv("HERMES_URL"),
            token=session.get("hermes_token") or os.getenv("HERMES_TOKEN"),
        )
        set_active_backend(hermes)
        llm_backend = hermes
        LOG.info("LLM switched to Hermes at %s", hermes.base_url)
        if session_id in active_directors:
            active_directors[session_id].llm = hermes

    director = _get_director(session_id)
    if llm_backend is not None:
        director.llm = llm_backend
    director.singer_profile = session.get("singer_profile") or {}
    director.video_backend = backend
    director.video_model = session["video_model"]
    director.performance_mix = mix
    director.project_mode = session["project_mode"]
    director.configure_comfy(comfy_backend, comfy_url)
    director.configure_home_agent(
        session.get("home_agent_url") or "",
        session.get("home_agent_password")
        or os.getenv("HOME_AGENT_PASSWORD")
        or "",
        use_stills=lanes["pipeline_stills"] == "home_agent",
        use_video=lanes["pipeline_video"] == "home_agent",
    )
    director.configure_pipeline(
        director=lanes["pipeline_director"],
        stills=lanes["pipeline_stills"],
        video=lanes["pipeline_video"],
        video_model=session["video_model"],
    )
    LOG.info(
        "Session %s pipeline dir=%s stills=%s video=%s model=%s comfy=%s (%s)",
        session_id,
        lanes["pipeline_director"],
        lanes["pipeline_stills"],
        lanes["pipeline_video"],
        session["video_model"],
        comfy_backend,
        director.comfy_url,
    )
    director.character_lock = ""  # rebuild with new singer details
    _write_project_manifest(session_id, session)
    return {
        "session_id": session_id,
        "singer_profile": session.get("singer_profile"),
        "video_backend": backend,
        "video_model": session["video_model"],
        "performance_mix": mix,
        "project_mode": session["project_mode"],
        "comfy_backend": comfy_backend,
        "comfy_url": comfy_url,
        "pipeline_director": lanes["pipeline_director"],
        "pipeline_stills": lanes["pipeline_stills"],
        "pipeline_video": lanes["pipeline_video"],
        "has_lyrics": bool((session.get("lyrics") or "").strip()),
    }

@app.post("/api/upload/audio")
async def upload_audio(session_id: str = Form(...), file: UploadFile = File(...)):
    """Upload MP3, analyze timing, and transcribe lyrics for concept grounding."""
    session_dir = OUTPUT_DIR / session_id
    session_dir.mkdir(parents=True, exist_ok=True)

    original_name = file.filename or "audio.mp3"
    suffix = Path(original_name).suffix or ".mp3"
    audio_path = session_dir / f"audio{suffix}"
    with open(audio_path, "wb") as f:
        shutil.copyfileobj(file.file, f)

    try:
        analysis = AudioAnalyzer.analyze(str(audio_path))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Audio analysis failed: {exc}") from exc

    from server.lyrics import compact_lyrics, infer_line_cues, title_from_filename

    analysis["track_title"] = title_from_filename(original_name)
    analysis["original_filename"] = original_name

    # Optional pasted lyrics from the client (Form field)
    # multipart may also include lyrics via a parallel JSON config call

    lyrics_payload: Dict[str, Any] = {"text": "", "srt": "", "cues": []}
    # Prefer user-pasted lyrics from session config if already provided
    existing_lyrics = (session_id and (legacy_sessions.get(session_id) or {}).get("lyrics")) or ""
    if existing_lyrics.strip():
        lyrics_payload = {"text": existing_lyrics.strip(), "srt": "", "cues": []}
        analysis["lyrics_source"] = "user"
    else:
        # 1) Local faster-whisper (free, reliable) — primary auto path
        try:
            from server.transcribe import transcribe_audio_local

            lyrics_payload = await asyncio.to_thread(
                transcribe_audio_local, str(audio_path)
            )
            if lyrics_payload.get("text"):
                analysis["lyrics_source"] = "whisper-local"
                LOG.info(
                    "Auto lyrics extracted locally (%d chars)",
                    len(lyrics_payload["text"]),
                )
        except Exception as exc:
            LOG.warning("Local lyrics transcription failed: %s", exc)
            lyrics_payload = {"text": "", "srt": "", "cues": []}

        # 2) Optional Comfy Cloud Whisper fallback (often requires paid credits)
        if not (lyrics_payload.get("text") or "").strip():
            try:
                comfy = ComfyUIClient()
                cloud = await comfy.transcribe_lyrics(str(audio_path))
                if cloud.get("text"):
                    lyrics_payload = cloud
                    analysis["lyrics_source"] = "whisper-cloud"
            except Exception as exc:
                LOG.warning("Cloud lyrics transcription failed: %s", exc)

        if not (lyrics_payload.get("text") or "").strip():
            analysis["lyrics_source"] = "none"
            LOG.warning(
                "No lyrics extracted from %s — concepts will rely on title/direction only",
                original_name,
            )

    lyrics_text = compact_lyrics(lyrics_payload.get("text") or "")
    analysis["lyrics"] = lyrics_text
    analysis["lyrics_srt"] = lyrics_payload.get("srt") or ""
    lyric_cues = lyrics_payload.get("cues") or []
    if lyrics_text and not lyric_cues:
        lyric_cues = infer_line_cues(lyrics_text, float(analysis.get("duration") or 0))
        analysis["lyrics_timing_warning"] = (
            "Official lyrics had no timestamps; proportional line timing was inferred."
        )
    analysis["lyrics_cues"] = lyric_cues
    analysis["coverage_timeline"] = AudioAnalyzer.build_coverage_timeline(
        duration=float(analysis.get("duration") or 0),
        lyric_cues=lyric_cues,
        energy_profile=analysis.get("energy_profile") or [],
    )
    # Use coverage intervals for typography and richer gap semantics; MTV also benefits.
    if analysis["coverage_timeline"]:
        analysis["scenes"] = analysis["coverage_timeline"]
    if lyrics_text and analysis.get("lyrics_source") == "none":
        analysis["lyrics_source"] = "whisper"

    session = legacy_sessions.setdefault(session_id, {"session_id": session_id})
    session["audio_path"] = str(audio_path)
    session["audio_analysis"] = analysis
    session["status"] = "analyzed"
    # Keep a dedicated copy for director helpers
    session["lyrics"] = lyrics_text
    _write_project_manifest(session_id, session)
    return {
        "session_id": session_id,
        "analysis": {
            **{k: v for k, v in analysis.items() if k not in ("beat_times", "energy_profile", "lyrics_srt")},
            "lyrics_preview": (lyrics_text[:400] + "…") if len(lyrics_text) > 400 else lyrics_text,
            "has_lyrics": bool(lyrics_text),
        },
        "status": "analyzed",
    }


@app.post("/api/upload/images")
async def upload_images(
    session_id: str = Form(...),
    files: List[UploadFile] = File(...),
):
    """Upload reference images (legacy)."""
    session_dir = OUTPUT_DIR / session_id / "references"
    session_dir.mkdir(parents=True, exist_ok=True)
    paths: List[str] = []
    for file in files:
        dest = session_dir / (file.filename or f"ref_{uuid.uuid4().hex}.png")
        with open(dest, "wb") as f:
            shutil.copyfileobj(file.file, f)
        paths.append(str(dest))
    session = legacy_sessions.setdefault(session_id, {"session_id": session_id})
    session["reference_images"] = paths
    return {"session_id": session_id, "images": paths}


@app.post("/api/chat")
async def chat_endpoint(request: dict):
    """Director chat for the interactive client (legacy)."""
    session_id = request.get("session_id")
    message = request.get("message", "")
    if not session_id:
        return {"error": "session_id required"}

    session = legacy_sessions.setdefault(
        session_id,
        {"session_id": session_id, "chat_history": [], "status": "ideating"},
    )
    history = session.setdefault("chat_history", [])
    history.append({"role": "user", "content": message})

    backend = llm_backend or _init_llm_backend()
    if backend is None:
        reply = (
            "[No LLM backend configured. Set OLLAMA_API_KEY for cloud mode, "
            "or install llama.cpp / start local Ollama.]"
        )
    else:
        messages: List[Dict[str, str]] = [
            {
                "role": "system",
                "content": (
                    "You are SceneMux, an expert AI music video director. "
                    "Be cinematic, specific, and helpful. When proposing concepts, "
                    "number them clearly (1. 2. 3.)."
                ),
            }
        ]
        analysis = session.get("audio_analysis")
        if analysis:
            messages.append(
                {
                    "role": "system",
                    "content": (
                        f"SONG ANALYSIS: {analysis.get('duration', 0):.1f}s, "
                        f"{analysis.get('tempo', 0):.0f} BPM, "
                        f"{analysis.get('beat_count', 0)} beats"
                        + (
                            f", track title hint: {analysis.get('track_title')}"
                            if analysis.get("track_title")
                            else ""
                        )
                    ),
                }
            )
            lyrics = (analysis.get("lyrics") or session.get("lyrics") or "").strip()
            if lyrics:
                from server.lyrics import compact_lyrics

                messages.append(
                    {
                        "role": "system",
                        "content": (
                            "LYRICS (primary thematic source — concepts and visuals must "
                            "reflect these words, metaphors, characters, and story):\n"
                            + compact_lyrics(lyrics, max_chars=2800)
                        ),
                    }
                )
        for msg in history[-12:]:
            role = "assistant" if msg["role"] in ("assistant", "agent") else msg["role"]
            messages.append({"role": role, "content": msg["content"]})

        try:
            reply = await backend.chat(messages)
        except Exception as exc:
            reply = f"[LLM error: {friendly_llm_error(exc)}]"

    history.append({"role": "assistant", "content": reply})
    session["status"] = "ideating"
    if "concept" in message.lower():
        session["concept_prompt"] = message

    return {
        "session_id": session_id,
        "response": reply,
        "phase": session.get("status", "ideation"),
        "history": history[-6:],
        "llm": {
            "provider": getattr(backend, "name", None) if backend else None,
            "model": getattr(backend, "model_name", None) if backend else None,
            "used_fallback": bool(getattr(backend, "used_fallback", False)),
        },
    }


@app.post("/api/concepts/generate")
async def generate_concepts(request: dict):
    session_id = request.get("session_id")
    session = legacy_sessions.get(session_id or "")
    if not session:
        return {"error": "Session not found"}

    director = _get_director(session_id)
    _hydrate_director(director, session)
    text_direction = (
        session.get("creative_direction")
        or session.get("concept_prompt")
        or "cinematic performance video grounded in the lyrics"
    )
    try:
        concepts = await director.generate_concepts(
            text_direction=text_direction,
            reference_images=session.get("reference_images") or None,
        )
    except Exception as exc:
        raise HTTPException(status_code=402, detail=friendly_llm_error(exc)) from exc
    session["concepts"] = concepts
    if isinstance(director.audio_analysis, dict):
        session["audio_analysis"] = director.audio_analysis
    _write_project_manifest(session_id, session)

    ref_urls = []
    for path in session.get("reference_images") or []:
        name = Path(path).name
        ref_urls.append(f"/api/session/{session_id}/asset/references/{name}")

    return {
        "session_id": session_id,
        "concepts": concepts,
        "lyric_brief": (session.get("audio_analysis") or {}).get("lyric_brief"),
        "has_lyrics": bool((session.get("audio_analysis") or {}).get("lyrics")),
        "has_references": bool(session.get("reference_images")),
        "reference_urls": ref_urls,
        "project_mode": session.get("project_mode") or "mtv",
    }


@app.post("/api/concept/select")
async def select_concept(request: dict):
    session_id = request.get("session_id")
    session = legacy_sessions.get(session_id or "")
    if not session:
        return {"error": "Session not found"}
    idx = request.get("concept_index", 0)
    session["selected_concept"] = idx
    session["status"] = "storyboarding"
    concepts = session.get("concepts") or []
    concept = concepts[idx] if isinstance(idx, int) and 0 <= idx < len(concepts) else {}
    director = _get_director(session_id)
    _hydrate_director(director, session)
    if isinstance(concept, dict) and concept:
        bible = await director.expand_visual_bible(concept)
        concepts[idx] = concept
        session["concepts"] = concepts
        session["visual_bible"] = bible
        director.selected_concept = concept
    _write_project_manifest(session_id, session)
    return {
        "session_id": session_id,
        "selected": session["selected_concept"],
        "visual_bible": session.get("visual_bible"),
        "project_mode": session.get("project_mode") or "mtv",
    }


@app.post("/api/storyboard/generate")
async def generate_storyboard(request: dict):
    session_id = request.get("session_id")
    session = legacy_sessions.get(session_id or "")
    if not session:
        return {"error": "Session not found"}
    concepts = session.get("concepts") or []
    idx = session.get("selected_concept") or 0
    concept = concepts[idx] if isinstance(idx, int) and 0 <= idx < len(concepts) else {}
    director = _get_director(session_id)
    _hydrate_director(director, session)
    if director.project_mode != "typography":
        await director.ensure_identity_sheet(concept)
    if director.identity_sheet_path:
        session["identity_sheet_path"] = director.identity_sheet_path
    frames = await director.generate_storyboard(concept=concept)
    session["storyboard"] = frames
    _write_project_manifest(session_id, session)
    analysis = session.get("audio_analysis") or {}
    project_mode = session.get("project_mode") or "mtv"
    preview_indices = _tracer_indices(frames, project_mode)
    identity_url = None
    if director.identity_sheet_path:
        identity_url = (
            f"/api/session/{session_id}/asset/identity/"
            f"{Path(director.identity_sheet_path).name}"
        )
    return {
        "session_id": session_id,
        "frames": frames,
        "identity_sheet_url": identity_url,
        "plan": {
            "song_duration": analysis.get("duration"),
            "scene_count": len(frames),
            "tempo": analysis.get("tempo"),
            "has_lyrics": bool(analysis.get("lyrics")),
            "has_references": bool(session.get("reference_images")),
            "performance_mix": session.get("performance_mix") or "hybrid",
            "avatar_shots": sum(1 for f in frames if f.get("shot_type") == "avatar"),
            "cinematic_shots": sum(1 for f in frames if f.get("shot_type") == "cinematic"),
            "preview_count": PREVIEW_SCENES,
            "preview_indices": preview_indices,
            "project_mode": project_mode,
        },
    }


PREVIEW_SCENES = max(1, int(os.getenv("VIDMUSE_PREVIEW_SCENES", "3")))


def _tracer_indices(
    frames: List[Dict[str, Any]],
    project_mode: str,
    count: int = PREVIEW_SCENES,
) -> List[int]:
    """Pick a representative lyric/performance, energy peak, and instrumental shot."""
    if not frames:
        return []
    selected: List[int] = []

    if project_mode == "typography":
        primary_types = ("lyric", "overlap")
        gap_types = ("instrumental", "transition")
    else:
        primary_types = ("avatar",)
        gap_types = ("cinematic",)

    for i, frame in enumerate(frames):
        if frame.get("shot_type") in primary_types and (frame.get("lyric_window") or ""):
            selected.append(i)
            break
    energy_idx = max(
        range(len(frames)),
        key=lambda i: float(frames[i].get("energy") or 0),
    )
    if energy_idx not in selected:
        selected.append(energy_idx)
    for i, frame in enumerate(frames):
        if frame.get("shot_type") in gap_types and not (frame.get("lyric_window") or ""):
            if i not in selected:
                selected.append(i)
            break
    for i in range(len(frames)):
        if len(selected) >= min(count, len(frames)):
            break
        if i not in selected:
            selected.append(i)
    return selected[:count]


def _storyboard_concept(session: Dict[str, Any]) -> Dict[str, Any]:
    concepts = session.get("concepts") or []
    idx = session.get("selected_concept") or 0
    if isinstance(idx, int) and 0 <= idx < len(concepts):
        return concepts[idx]
    return {
        "style": "cinematic",
        "palette": ["#0b1629", "#06b6d4", "#f59e0b"],
    }


def _identity_sheet_url(session_id: str, director: HermesDirector) -> Optional[str]:
    """Prefer director helper so identity/ vs references/ URLs stay correct."""
    url = director._studio_identity_url()
    if url:
        return url
    if not director.identity_sheet_path:
        return None
    path = Path(director.identity_sheet_path)
    if not path.is_file():
        return None
    # Last resort only when file already lives under session identity/
    identity_dir = (OUTPUT_DIR / session_id / "identity").resolve()
    try:
        path.resolve().relative_to(identity_dir)
        return f"/api/session/{session_id}/asset/identity/{path.name}"
    except ValueError:
        return None


def _safe_session_asset(session_id: str, subdir: str, filename: str) -> Path:
    """Resolve a session asset path and reject directory traversal."""
    base = (OUTPUT_DIR / session_id / subdir).resolve()
    path = (base / filename).resolve()
    if not str(path).startswith(str(base)) or not path.is_file():
        raise HTTPException(status_code=404, detail="Asset not found")
    return path


@app.post("/api/storyboard/generate-images")
async def generate_storyboard_images(request: dict):
    session_id = request.get("session_id")
    session = legacy_sessions.get(session_id or "")
    if not session or not session.get("storyboard"):
        return {"error": "No storyboard"}
    concept = _storyboard_concept(session)
    director = _get_director(session_id)
    _hydrate_director(director, session)
    director.storyboard = session["storyboard"]
    start_index = int(request.get("start_index", 0))
    end_index = request.get("end_index")
    end = int(end_index) if end_index is not None else None
    generated = await director.generate_storyboard_images(
        concept=concept,
        start_index=start_index,
        end_index=end,
    )
    session["storyboard"] = director.storyboard
    if director.identity_sheet_path:
        session["identity_sheet_path"] = director.identity_sheet_path
    total = len(session["storyboard"])
    return {
        "session_id": session_id,
        "frames": session["storyboard"],
        "generated": generated,
        "identity_sheet_url": _identity_sheet_url(session_id, director),
        "cancelled": director._storyboard_cancel,
        "preview_count": PREVIEW_SCENES,
        "summary": {
            "total": total,
            "succeeded": len(generated),
            "failed": max(0, total - len(generated)),
            "used_reference_images": bool(session.get("reference_images")),
            "has_identity_sheet": bool(director.identity_sheet_path),
        },
    }


@app.post("/api/storyboard/render-range")
async def render_storyboard_range(request: dict):
    """Render a slice of storyboard frames with live WebSocket updates per frame."""
    session_id = request.get("session_id")
    session = legacy_sessions.get(session_id or "")
    if not session or not session.get("storyboard"):
        return {"error": "No storyboard"}
    concept = _storyboard_concept(session)
    director = _get_director(session_id)
    _hydrate_director(director, session)
    director.storyboard = session["storyboard"]
    start_index = int(request.get("start_index", 0))
    end_index = request.get("end_index")
    end = int(end_index) if end_index is not None else None
    raw_indices = request.get("frame_indices")
    frame_indices = (
        [int(i) for i in raw_indices]
        if isinstance(raw_indices, list)
        else None
    )
    generated = await director.generate_storyboard_images(
        concept=concept,
        start_index=start_index,
        end_index=end,
        frame_indices=frame_indices,
    )
    session["storyboard"] = director.storyboard
    if director.identity_sheet_path:
        session["identity_sheet_path"] = director.identity_sheet_path
    total = len(session["storyboard"])
    return {
        "session_id": session_id,
        "frames": session["storyboard"],
        "generated": generated,
        "identity_sheet_url": _identity_sheet_url(session_id, director),
        "cancelled": director._storyboard_cancel,
        "preview_count": PREVIEW_SCENES,
        "range": {"start": start_index, "end": end if end is not None else total},
        "frame_indices": frame_indices,
        "summary": {
            "total": total,
            "succeeded": len(generated),
            "failed": max(0, total - len(generated)),
        },
    }


@app.post("/api/storyboard/render-frame")
async def render_storyboard_frame(request: dict):
    """Render one storyboard frame by index."""
    session_id = request.get("session_id")
    session = legacy_sessions.get(session_id or "")
    if not session or not session.get("storyboard"):
        return {"error": "No storyboard"}
    frame_index = int(request.get("frame_index", 0))
    concept = _storyboard_concept(session)
    director = _get_director(session_id)
    _hydrate_director(director, session)
    director.storyboard = session["storyboard"]
    director.clear_storyboard_cancel()
    frame = await director.render_storyboard_frame(concept, frame_index)
    session["storyboard"] = director.storyboard
    if director.identity_sheet_path:
        session["identity_sheet_path"] = director.identity_sheet_path
    return {
        "session_id": session_id,
        "index": frame_index,
        "frame": frame,
        "frames": session["storyboard"],
        "identity_sheet_url": _identity_sheet_url(session_id, director),
    }


@app.post("/api/storyboard/cancel-render")
async def cancel_storyboard_render(request: dict):
    session_id = request.get("session_id")
    if not session_id:
        return {"error": "session_id required"}
    director = _get_director(session_id)
    director.request_storyboard_cancel()
    return {"session_id": session_id, "cancelled": True}


@app.post("/api/storyboard/approve")
async def approve_storyboard(request: dict):
    session_id = request.get("session_id")
    session = legacy_sessions.get(session_id or "")
    if not session:
        return {"error": "Session not found"}
    approved = set(request.get("approved_frames") or [])
    for frame in session.get("storyboard") or []:
        if isinstance(frame, dict):
            frame["approved"] = frame.get("id") in approved
    session["status"] = "generating"
    return {"session_id": session_id, "status": "approved"}


@app.post("/api/video/generate")
async def generate_video(request: dict):
    session_id = request.get("session_id")
    session = legacy_sessions.get(session_id or "")
    if not session:
        return {"error": "Session not found"}
    concepts = session.get("concepts") or []
    idx = session.get("selected_concept") or 0
    concept = concepts[idx] if isinstance(idx, int) and 0 <= idx < len(concepts) else {
        "style": "cinematic",
        "palette": ["#0b1629", "#06b6d4", "#f59e0b"],
    }
    director = _get_director(session_id)
    _hydrate_director(director, session)
    # Only animate approved frames when present
    frames = [
        f for f in (session.get("storyboard") or [])
        if isinstance(f, dict) and f.get("approved", True)
    ]
    director.storyboard = frames or session.get("storyboard") or []
    generated = await director.generate_video_clips(concept=concept)
    session["storyboard"] = director.storyboard
    analysis = session.get("audio_analysis") or {}
    return {
        "session_id": session_id,
        "generated": generated,
        "clips": generated,
        "summary": {
            "total": len(director.storyboard),
            "succeeded": len(generated),
            "failed": max(0, len(director.storyboard) - len(generated)),
            "song_duration": analysis.get("duration"),
        },
    }


@app.get("/api/session/{session_id}/asset/storyboard/{filename}")
async def get_storyboard_asset(session_id: str, filename: str):
    path = _safe_session_asset(session_id, "storyboard", filename)
    return FileResponse(path)


@app.get("/api/session/{session_id}/asset/references/{filename}")
async def get_reference_asset(session_id: str, filename: str):
    path = _safe_session_asset(session_id, "references", filename)
    return FileResponse(path)


@app.get("/api/session/{session_id}/asset/identity/{filename}")
async def get_identity_asset(session_id: str, filename: str):
    path = _safe_session_asset(session_id, "identity", filename)
    return FileResponse(path)


@app.get("/api/session/{session_id}/asset/video/{filename}")
async def get_video_asset(session_id: str, filename: str):
    path = _safe_session_asset(session_id, "videos", filename)
    return FileResponse(path, media_type="video/mp4")


@app.post("/api/edit/final")
async def edit_final(request: dict):
    session_id = request.get("session_id")
    session = legacy_sessions.get(session_id or "")
    if not session or not session.get("audio_path"):
        return {"error": "No audio uploaded"}
    director = _get_director(session_id)
    _hydrate_director(director, session)
    director.storyboard = [
        f for f in (session.get("storyboard") or []) if isinstance(f, dict)
    ]
    clips = [
        {
            "path": f["video_path"],
            "duration": float(f.get("target_duration") or f.get("duration") or 0),
            "start": float(f.get("timestamp") or 0),
        }
        for f in director.storyboard
        if f.get("video_path")
    ]
    if not clips:
        return {"error": "No video clips generated"}
    final_path = await director.assemble_final_video(
        video_clips=clips,
        audio_path=session["audio_path"],
    )
    session["final_video_path"] = final_path
    session["status"] = "done"
    qc_path = OUTPUT_DIR / session_id / "technical_qc.json"
    technical_qc = None
    if qc_path.is_file():
        try:
            technical_qc = json.loads(qc_path.read_text(encoding="utf-8"))
            session["technical_qc"] = technical_qc
        except Exception:
            technical_qc = None
    _write_project_manifest(session_id, session)
    return {
        "session_id": session_id,
        "video_path": final_path,
        "status": "done",
        "duration": (session.get("audio_analysis") or {}).get("duration"),
        "scenes": len(clips),
        "project_mode": session.get("project_mode") or "mtv",
        "technical_qc": technical_qc,
    }


# ─── Studio (H3-only media editor) ──────────────────────────────────────


def _studio_plan_public(session_id: str, plan: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    if not plan:
        return {"scenes": [], "duration": 0}
    scenes = []
    for sc in plan.get("scenes") or []:
        item = dict(sc)
        item.pop("video_path", None)
        item.pop("still_path", None)
        scenes.append(item)
    return {
        "version": plan.get("version"),
        "duration": plan.get("duration"),
        "direction": plan.get("direction"),
        "character": plan.get("character"),
        "character_lock": plan.get("character_lock"),
        "identity_sheet_url": plan.get("identity_sheet_url"),
        "review_status": plan.get("review_status"),
        "backend": plan.get("backend") or "minimax-h3",
        "scenes": scenes,
    }


@app.post("/api/studio/plan")
async def studio_plan(request: dict):
    """Build a lyric storyboard plan + lock performer identity (no H3 video yet)."""
    session_id = request.get("session_id")
    session = legacy_sessions.get(session_id or "")
    if not session or not session.get("audio_path"):
        raise HTTPException(status_code=400, detail="Upload audio first")
    direction = str(
        request.get("direction")
        or session.get("creative_direction")
        or session.get("concept_prompt")
        or ""
    ).strip()
    if not direction:
        raise HTTPException(status_code=400, detail="Creative direction required")

    director = _get_director(session_id)
    _hydrate_director(director, session)
    session["creative_direction"] = direction[:2000]
    from server.pipeline import normalize_video_model

    vm = normalize_video_model(
        session.get("video_model") or session.get("video_backend") or "minimax-h3"
    )
    session["video_model"] = vm
    session["video_backend"] = vm if vm in ("minimax-h3", "ltx23", "fasth3") else "minimax-h3"
    director.video_backend = session["video_backend"]
    director.video_model = vm
    lock_identity = bool(request.get("lock_identity", True))
    reset_identity = bool(request.get("reset_identity", False))
    if reset_identity:
        director.identity_sheet_path = None
        director.character_lock = ""
        director._video_seed_base = None
        session.pop("identity_sheet_path", None)

    try:
        plan = await director.build_studio_timeline_plan(
            direction, lock_identity=lock_identity
        )
    except Exception as exc:
        LOG.exception("Studio plan failed")
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    if director.identity_sheet_path:
        session["identity_sheet_path"] = director.identity_sheet_path
    elif reset_identity:
        session.pop("identity_sheet_path", None)
    session["timeline_plan"] = plan
    session["status"] = "studio_planned"
    _write_project_manifest(session_id, session)
    return {
        "session_id": session_id,
        "timeline_plan": _studio_plan_public(session_id, plan),
        "identity_sheet_url": plan.get("identity_sheet_url"),
    }


@app.post("/api/studio/storyboard-stills")
async def studio_storyboard_stills(request: dict):
    """Generate cheap still previews for storyboard review before H3 credits."""
    session_id = request.get("session_id")
    session = legacy_sessions.get(session_id or "")
    if not session or not session.get("audio_path"):
        raise HTTPException(status_code=400, detail="Upload audio first")

    director = _get_director(session_id)
    _hydrate_director(director, session)
    if not director.timeline_plan and session.get("timeline_plan"):
        director.timeline_plan = session["timeline_plan"]
    if not director.timeline_plan:
        raise HTTPException(status_code=400, detail="Plan the storyboard first")

    max_scenes = request.get("max_scenes")
    scene_indices = request.get("scene_indices")
    try:
        max_scenes_i = int(max_scenes) if max_scenes is not None else None
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail="max_scenes must be an int") from exc
    indices = None
    if isinstance(scene_indices, list):
        try:
            indices = [int(i) for i in scene_indices]
        except (TypeError, ValueError) as exc:
            raise HTTPException(
                status_code=400, detail="scene_indices must be ints"
            ) from exc

    try:
        plan = await director.generate_studio_storyboard_stills(
            max_scenes=max_scenes_i,
            scene_indices=indices,
        )
    except Exception as exc:
        LOG.exception("Studio storyboard stills failed")
        # Persist partial plan so UI can show still_failed badges
        if director.timeline_plan:
            session["timeline_plan"] = director.timeline_plan
            if director.identity_sheet_path:
                session["identity_sheet_path"] = director.identity_sheet_path
            _write_project_manifest(session_id, session)
        detail = str(exc).strip() or "Storyboard stills failed"
        low = detail.lower()
        status = 503 if (
            "credit" in low
            or "quota" in low
            or "blocked" in low
            or "fal" in low
        ) else 502
        raise HTTPException(status_code=status, detail=detail) from exc

    if director.identity_sheet_path:
        session["identity_sheet_path"] = director.identity_sheet_path
    session["timeline_plan"] = plan
    session["status"] = "studio_review"
    _write_project_manifest(session_id, session)
    return {
        "session_id": session_id,
        "timeline_plan": _studio_plan_public(session_id, plan),
        "identity_sheet_url": plan.get("identity_sheet_url"),
    }


@app.post("/api/studio/update-plan")
async def studio_update_plan(request: dict):
    """Persist client-edited scene prompts before preview / full render."""
    session_id = request.get("session_id")
    session = legacy_sessions.get(session_id or "")
    if not session:
        raise HTTPException(status_code=400, detail="Invalid session")
    director = _get_director(session_id)
    _hydrate_director(director, session)
    if not director.timeline_plan and session.get("timeline_plan"):
        director.timeline_plan = session["timeline_plan"]
    plan = director.timeline_plan
    if not plan or not plan.get("scenes"):
        raise HTTPException(status_code=400, detail="No timeline plan yet")

    updates = request.get("scenes") or []
    by_index = {int(s["index"]): s for s in plan["scenes"] if "index" in s}
    for item in updates:
        try:
            idx = int(item.get("index"))
        except (TypeError, ValueError, AttributeError):
            continue
        scene = by_index.get(idx)
        if not scene:
            continue
        if item.get("prompt") is not None:
            scene["prompt"] = str(item.get("prompt") or "")[:4000]
        if item.get("type") is not None:
            scene["type"] = str(item.get("type") or scene.get("type"))[:40]
        if "video_engine" in item or "engine_override" in item:
            raw = item.get("video_engine") or item.get("engine_override")
            if raw is None or str(raw).strip() in ("", "auto", "default"):
                scene.pop("video_engine", None)
            else:
                from server.pipeline import normalize_video_model

                eng = normalize_video_model(raw)
                if eng in ("minimax-h3", "fasth3", "ltx23"):
                    scene["video_engine"] = eng
                else:
                    scene.pop("video_engine", None)

    plan_path = director.output_dir / "timeline_plan.json"
    plan_path.write_text(json.dumps(plan, indent=2), encoding="utf-8")
    director.timeline_plan = plan
    session["timeline_plan"] = plan
    _write_project_manifest(session_id, session)
    return {
        "session_id": session_id,
        "timeline_plan": _studio_plan_public(session_id, plan),
    }


@app.post("/api/studio/render")
async def studio_render(request: dict):
    """Render all H3 scenes (rebuild plan by default) and assemble."""
    session_id = request.get("session_id")
    session = legacy_sessions.get(session_id or "")
    if not session or not session.get("audio_path"):
        raise HTTPException(status_code=400, detail="Upload audio first")
    direction = str(
        request.get("direction") or session.get("creative_direction") or ""
    ).strip()
    if not direction:
        raise HTTPException(status_code=400, detail="Creative direction required")

    rebuild = bool(request.get("rebuild_plan", True))
    director = _get_director(session_id)
    _hydrate_director(director, session)
    director.clear_studio_cancel()
    if not director.timeline_plan and session.get("timeline_plan"):
        director.timeline_plan = session["timeline_plan"]
    # Apply any last-minute prompt edits from the client
    for item in request.get("scenes") or []:
        try:
            idx = int(item.get("index"))
        except (TypeError, ValueError, AttributeError):
            continue
        scenes = (director.timeline_plan or {}).get("scenes") or []
        if 0 <= idx < len(scenes) and item.get("prompt") is not None:
            scenes[idx]["prompt"] = str(item.get("prompt") or "")[:4000]
    session["creative_direction"] = direction[:2000]
    from server.pipeline import normalize_video_model

    vm = normalize_video_model(
        session.get("video_model") or session.get("video_backend") or "minimax-h3"
    )
    session["video_model"] = vm
    session["video_backend"] = (
        vm if vm in ("minimax-h3", "ltx23", "fasth3") else "minimax-h3"
    )
    director.video_backend = session["video_backend"]
    director.video_model = vm
    session["status"] = "studio_rendering"

    async def _run():
        try:
            result = await director.render_studio_all(
                direction, rebuild_plan=rebuild
            )
            session["timeline_plan"] = director.timeline_plan
            if director.identity_sheet_path:
                session["identity_sheet_path"] = director.identity_sheet_path
            session["final_video_path"] = result.get("final_video_path")
            cancelled = bool(result.get("cancelled"))
            session["status"] = "studio_cancelled" if cancelled else "done"
            _write_project_manifest(session_id, session)
            if session_id in connected_clients:
                try:
                    payload = {
                        "type": "studio_cancelled" if cancelled else "studio_complete",
                        "timeline_plan": _studio_plan_public(
                            session_id, director.timeline_plan
                        ),
                        "cancelled": cancelled,
                    }
                    if session.get("final_video_path"):
                        payload["download_url"] = f"/api/download/{session_id}"
                    await connected_clients[session_id].send_json(payload)
                except Exception:
                    pass
        except Exception as exc:
            LOG.exception("Studio render failed")
            session["status"] = "studio_failed"
            session["error"] = str(exc)
            if session_id in connected_clients:
                try:
                    await connected_clients[session_id].send_json(
                        {"type": "studio_error", "error": str(exc)}
                    )
                except Exception:
                    pass

    asyncio.create_task(_run())
    return {
        "session_id": session_id,
        "status": "started",
        "timeline_plan": _studio_plan_public(
            session_id, session.get("timeline_plan") or director.timeline_plan
        ),
    }


@app.post("/api/studio/cancel-render")
async def studio_cancel_render(request: dict):
    """Request cancel of an in-flight studio full render (stops before next scene)."""
    session_id = request.get("session_id")
    session = legacy_sessions.get(session_id or "")
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    director = active_directors.get(session_id) or _get_director(session_id)
    director.request_studio_cancel()
    return {"session_id": session_id, "status": "cancel_requested"}


@app.post("/api/studio/render-scene")
async def studio_render_scene(request: dict):
    """Re-render one H3 scene and optionally reassemble the cut."""
    session_id = request.get("session_id")
    scene_index = request.get("scene_index")
    session = legacy_sessions.get(session_id or "")
    if not session or not session.get("audio_path"):
        raise HTTPException(status_code=400, detail="Upload audio first")
    if scene_index is None:
        raise HTTPException(status_code=400, detail="scene_index required")
    try:
        scene_index = int(scene_index)
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail="scene_index must be an int") from exc

    director = _get_director(session_id)
    _hydrate_director(director, session)
    if not director.timeline_plan and session.get("timeline_plan"):
        director.timeline_plan = session["timeline_plan"]
    if not director.timeline_plan:
        raise HTTPException(status_code=400, detail="No timeline plan yet")

    direction = str(
        request.get("direction")
        or session.get("creative_direction")
        or director.timeline_plan.get("direction")
        or ""
    ).strip()
    prompt_override = str(request.get("prompt") or "").strip()
    scenes = director.timeline_plan.get("scenes") or []
    if 0 <= scene_index < len(scenes) and prompt_override:
        scenes[scene_index]["prompt"] = prompt_override

    try:
        scene = await director.render_studio_scene(
            scene_index, plan=director.timeline_plan, direction=direction or None
        )
    except Exception as exc:
        LOG.exception("Studio scene render failed")
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    session["timeline_plan"] = director.timeline_plan
    reassemble = bool(request.get("reassemble", True))
    download_url = None
    if reassemble:
        try:
            final_path = await director.assemble_studio_final()
            session["final_video_path"] = final_path
            session["status"] = "done"
            download_url = f"/api/download/{session_id}"
        except Exception as exc:
            LOG.warning("Studio reassemble skipped: %s", exc)

    _write_project_manifest(session_id, session)
    public_scene = dict(scene)
    public_scene.pop("video_path", None)
    return {
        "session_id": session_id,
        "scene": public_scene,
        "timeline_plan": _studio_plan_public(session_id, director.timeline_plan),
        "download_url": download_url,
    }


@app.get("/api/studio/{session_id}/state")
async def studio_state(session_id: str):
    """Poll studio timeline + final cut status."""
    session = legacy_sessions.get(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    plan = session.get("timeline_plan")
    director = active_directors.get(session_id)
    if director and director.timeline_plan:
        plan = director.timeline_plan
        session["timeline_plan"] = plan
    scenes = (plan or {}).get("scenes") or []
    ready = sum(
        1 for s in scenes if s.get("status") == "ready" and s.get("video_path")
    )
    rendering = sum(1 for s in scenes if s.get("status") == "rendering")
    failed = sum(1 for s in scenes if s.get("status") == "failed")
    remaining = sum(
        1 for s in scenes if s.get("status") != "ready" or not s.get("video_path")
    )
    cancel_requested = bool(
        director and getattr(director, "_studio_cancel", False)
    )
    return {
        "session_id": session_id,
        "status": session.get("status"),
        "timeline_plan": _studio_plan_public(session_id, plan),
        "download_url": (
            f"/api/download/{session_id}" if session.get("final_video_path") else None
        ),
        "error": session.get("error"),
        "track_title": (session.get("audio_analysis") or {}).get("track_title"),
        "duration": (session.get("audio_analysis") or {}).get("duration"),
        "lyrics_preview": ((session.get("lyrics") or "")[:400] or None),
        "comfy_backend": session.get("comfy_backend"),
        "comfy_url": session.get("comfy_url"),
        "has_references": bool(session.get("reference_images")),
        "queue": {
            "scene_total": len(scenes),
            "ready": ready,
            "rendering": rendering,
            "failed": failed,
            "remaining": remaining,
            "cancel_requested": cancel_requested,
        },
    }


@app.get("/api/download/{session_id}")
async def download_video(session_id: str):
    session = legacy_sessions.get(session_id)
    if not session or not session.get("final_video_path"):
        return {"error": "Video not ready"}
    return FileResponse(
        session["final_video_path"],
        media_type="video/mp4",
        filename=f"vidmuse_{session_id}.mp4",
    )


@app.get("/api/session/{session_id}")
async def get_session(session_id: str):
    session = legacy_sessions.get(session_id)
    if not session:
        return {"error": "Session not found"}
    return session


@app.get("/api/projects")
async def list_projects():
    """List recent project manifests for resume."""
    projects = []
    if not OUTPUT_DIR.is_dir():
        return {"projects": []}
    # Prefer folders that have project.json OR timeline_plan.json OR audio
    candidates = set()
    for path in OUTPUT_DIR.iterdir():
        if not path.is_dir():
            continue
        if (
            (path / "project.json").is_file()
            or (path / "timeline_plan.json").is_file()
            or (path / "audio.mp3").is_file()
        ):
            candidates.add(path)
    for path in sorted(
        candidates,
        key=lambda p: (
            (p / "project.json").stat().st_mtime
            if (p / "project.json").is_file()
            else p.stat().st_mtime
        ),
        reverse=True,
    ):
        data: Dict[str, Any] = {}
        manifest = path / "project.json"
        if manifest.is_file():
            try:
                data = json.loads(manifest.read_text(encoding="utf-8"))
            except Exception:
                data = {}
        sid = data.get("session_id") or path.name
        finals = list(path.glob("vidmuse_*.mp4")) + list(path.glob("*_final.mp4"))
        timeline = data.get("timeline") or {}
        scene_count = timeline.get("scene_count")
        ready_count = timeline.get("ready_count")
        if scene_count is None and (path / "timeline_plan.json").is_file():
            try:
                plan = json.loads((path / "timeline_plan.json").read_text(encoding="utf-8"))
                scenes = plan.get("scenes") or []
                scene_count = len(scenes)
                ready_count = sum(
                    1 for s in scenes if s.get("status") == "ready" and s.get("video_path")
                )
            except Exception:
                pass
        track = ((data.get("audio") or {}).get("analysis") or {}).get("track_title")
        if not track:
            track = ((data.get("audio") or {}).get("analysis") or {}).get(
                "original_filename"
            )
        projects.append(
            {
                "session_id": sid,
                "creative_direction": (data.get("creative_direction") or "")[:160],
                "track_title": track,
                "pipeline": data.get("pipeline") or {},
                "comfy": data.get("comfy") or {},
                "has_final": any(f.is_file() for f in finals),
                "has_audio": (path / "audio.mp3").is_file()
                or bool((data.get("audio") or {}).get("path")),
                "has_plan": (path / "timeline_plan.json").is_file(),
                "scene_count": scene_count,
                "ready_count": ready_count,
                "mtime": (
                    manifest.stat().st_mtime
                    if manifest.is_file()
                    else path.stat().st_mtime
                ),
            }
        )
        if len(projects) >= 24:
            break
    return {"projects": projects}


@app.post("/api/projects/resume")
async def resume_project(request: dict):
    """Load a prior outputs/<session_id> project into memory for in-app resume."""
    session_id = str(request.get("session_id") or "").strip()
    if not session_id:
        raise HTTPException(status_code=400, detail="session_id required")
    # Basic path-safety: session ids are UUIDs / simple folder names
    if "/" in session_id or "\\" in session_id or ".." in session_id:
        raise HTTPException(status_code=400, detail="Invalid session_id")
    session = _load_session_from_disk(session_id)
    if not session.get("audio_path"):
        raise HTTPException(
            status_code=400,
            detail="Project has no audio on disk — cannot resume",
        )
    _write_project_manifest(session_id, session)
    plan = session.get("timeline_plan")
    analysis = session.get("audio_analysis") or {}
    return {
        "session_id": session_id,
        "status": session.get("status"),
        "creative_direction": session.get("creative_direction") or "",
        "lyrics": session.get("lyrics") or "",
        "singer_profile": session.get("singer_profile") or {},
        "track_title": analysis.get("track_title")
        or analysis.get("original_filename")
        or session_id[:8],
        "duration": analysis.get("duration") or 0,
        "pipeline_director": session.get("pipeline_director"),
        "pipeline_stills": session.get("pipeline_stills"),
        "pipeline_video": session.get("pipeline_video"),
        "video_model": session.get("video_model"),
        "comfy_backend": session.get("comfy_backend"),
        "comfy_url": session.get("comfy_url"),
        "home_agent_url": session.get("home_agent_url"),
        "hermes_url": session.get("hermes_url"),
        "timeline_plan": _studio_plan_public(session_id, plan),
        "download_url": (
            f"/api/download/{session_id}" if session.get("final_video_path") else None
        ),
        "has_references": bool(session.get("reference_images")),
        "identity_sheet_url": (plan or {}).get("identity_sheet_url"),
    }


@app.post("/api/studio/estimate")
async def studio_estimate(request: dict):
    """Rough credit/time estimate before full render."""
    session_id = request.get("session_id")
    session = legacy_sessions.get(session_id or "")
    plan = (session or {}).get("timeline_plan") or {}
    scenes = plan.get("scenes") or []
    remaining = [
        s
        for s in scenes
        if s.get("status") != "ready" or not s.get("video_path")
    ]
    ready = [
        s for s in scenes if s.get("status") == "ready" and s.get("video_path")
    ]
    video = str(
        (session or {}).get("pipeline_video")
        or request.get("pipeline_video")
        or "comfy_cloud"
    )
    from server.pipeline import normalize_video_model

    video_model = normalize_video_model(
        (session or {}).get("video_model")
        or request.get("video_model")
        or "minimax-h3"
    )
    n = len(remaining) if remaining else (0 if ready else len(scenes))
    # Rough heuristics by host + engine
    if video == "fal":
        per = "1 FAL draft clip"
        minutes = n * 2
        cost_hint = f"~{n} FAL prepaid credit unit(s)"
    elif video == "home_agent":
        per = "1 Home Agent H3 job"
        minutes = n * 8
        cost_hint = f"~{n} local / HA GPU job(s) (no cloud bill)"
    elif video_model in ("fasth3", "hybrid_fast"):
        per = (
            "1 FastH3 draft (PERF→H3 Ref2VA if hybrid)"
            if video_model == "hybrid_fast"
            else "1 FastH3 8-step draft"
        )
        minutes = n * (3 if video == "comfy_cloud" else 4)
        cost_hint = (
            f"~{n} draft job(s)"
            + (" + H3 for PERF scenes" if video_model == "hybrid_fast" else "")
        )
    elif video_model in ("ltx23", "hybrid_ltx"):
        per = (
            "1 LTX IA2V (PERF→H3 Ref2VA if hybrid)"
            if video_model == "hybrid_ltx"
            else "1 LTX-2.3 IA2V job"
        )
        minutes = n * (3 if video == "comfy_cloud" else 5)
        cost_hint = f"~{n} LTX/hybrid job(s)"
    elif video == "comfy_cloud":
        per = "1 Comfy Cloud H3 Ref2VA job"
        minutes = n * 4
        cost_hint = f"~{n} Comfy Cloud H3 credit(s)"
    else:
        per = "1 local / Playground H3 Ref2VA job"
        minutes = n * 6
        cost_hint = f"~{n} local GPU job(s)"
    return {
        "session_id": session_id,
        "scene_total": len(scenes),
        "scenes_ready": len(ready),
        "scenes_to_render": n,
        "pipeline_video": video,
        "video_model": video_model,
        "per_scene": per,
        "cost_hint": cost_hint,
        "est_minutes_low": max(1, int(minutes * 0.6)) if n else 0,
        "est_minutes_high": max(2, int(minutes * 1.4)) if n else 0,
        "note": (
            "Estimates are approximate. FastH3/LTX are drafts — not Ref2VA lip-sync quality."
            if n
            else "All scenes already have clips — reassemble only."
        ),
    }


@app.post("/api/studio/enhance-prompt")
async def studio_enhance_prompt(request: dict):
    """Optional Advanced polish for one scene prompt (H3 / LTX / FastH3 wording)."""
    session_id = request.get("session_id")
    session = legacy_sessions.get(session_id or "")
    if not session:
        raise HTTPException(status_code=400, detail="Invalid session")
    try:
        scene_index = int(request.get("scene_index"))
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail="scene_index required") from exc

    director = _get_director(session_id)
    _hydrate_director(director, session)
    if not director.timeline_plan and session.get("timeline_plan"):
        director.timeline_plan = session["timeline_plan"]
    plan = director.timeline_plan or {}
    scenes = plan.get("scenes") or []
    if not (0 <= scene_index < len(scenes)):
        raise HTTPException(status_code=400, detail="scene_index out of range")

    scene = scenes[scene_index]
    draft = str(request.get("prompt") or scene.get("prompt") or "").strip()
    if not draft:
        raise HTTPException(status_code=400, detail="No prompt to enhance")

    from server.pipeline import normalize_video_model, resolve_scene_engine

    video_model = normalize_video_model(
        session.get("video_model") or session.get("video_backend") or "minimax-h3"
    )
    engine = resolve_scene_engine(
        video_model,
        scene.get("type"),
        has_image=bool(scene.get("still_path") or scene.get("still_url")),
        override=scene.get("video_engine"),
    )
    engine_label = {
        "minimax-h3": "MiniMax H3 Ref2VA lip-sync performance",
        "fasth3": "FastH3 8-step draft motion",
        "ltx23": "LTX-2.3 image-audio-to-video",
    }.get(engine, engine)
    character = (
        plan.get("character_lock")
        or plan.get("character")
        or (session.get("singer_profile") or {}).get("look")
        or "lead performer"
    )
    direction = (
        request.get("direction")
        or session.get("creative_direction")
        or plan.get("direction")
        or ""
    )
    lyric = scene.get("lyric") or ""

    if not director.llm:
        raise HTTPException(
            status_code=503,
            detail="LLM unavailable — set director lane / API keys to enhance prompts",
        )
    try:
        polished = await director.llm.generate(
            prompt=(
                f"Rewrite into one {engine_label} shot prompt (45-80 words). "
                "No on-screen text or logos. Keep the SAME performer identity. "
                "Prefer concrete camera, lighting, wardrobe, and motion cues.\n"
                f"Direction: {direction}\n"
                f"Character lock: {character}\n"
                f"Shot type: {scene.get('type')}\n"
                f"Lyric: {lyric}\n"
                f"Draft: {draft}"
            ),
            max_tokens=260,
            temperature=0.4,
        )
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Enhance failed: {exc}") from exc

    text = (polished or "").strip()
    if len(text) < 20:
        raise HTTPException(status_code=502, detail="Enhance returned empty prompt")
    if "Same performer" not in text:
        text = f"{text} Same performer: {character}."
    scene["prompt"] = text[:4000]
    plan_path = director.output_dir / "timeline_plan.json"
    plan_path.write_text(json.dumps(plan, indent=2), encoding="utf-8")
    director.timeline_plan = plan
    session["timeline_plan"] = plan
    _write_project_manifest(session_id, session)
    return {
        "session_id": session_id,
        "scene_index": scene_index,
        "prompt": scene["prompt"],
        "engine": engine,
        "timeline_plan": _studio_plan_public(session_id, plan),
    }


@app.get("/api/studio/{session_id}/media")
async def studio_media_bin(session_id: str):
    """List stills + clips in the session folder for media-bin reuse."""
    session_dir = OUTPUT_DIR / session_id
    if not session_dir.is_dir():
        raise HTTPException(status_code=404, detail="Session not found")
    items = []
    board = session_dir / "storyboard"
    if board.is_dir():
        for p in sorted(board.glob("studio_still_*.png")):
            items.append(
                {
                    "kind": "still",
                    "name": p.name,
                    "url": f"/api/session/{session_id}/asset/storyboard/{p.name}",
                }
            )
    identity = session_dir / "identity"
    if identity.is_dir():
        for p in sorted(identity.glob("*")):
            if p.suffix.lower() in (".png", ".jpg", ".jpeg", ".webp"):
                items.append(
                    {
                        "kind": "identity",
                        "name": p.name,
                        "url": f"/api/session/{session_id}/asset/identity/{p.name}",
                    }
                )
    refs = session_dir / "references"
    if refs.is_dir():
        for p in sorted(refs.glob("*")):
            if p.suffix.lower() in (".png", ".jpg", ".jpeg", ".webp"):
                items.append(
                    {
                        "kind": "reference",
                        "name": p.name,
                        "url": f"/api/session/{session_id}/asset/references/{p.name}",
                    }
                )
    videos = session_dir / "videos"
    if videos.is_dir():
        for p in sorted(videos.glob("studio_*.mp4")):
            items.append(
                {
                    "kind": "clip",
                    "name": p.name,
                    "url": f"/api/session/{session_id}/asset/video/{p.name}",
                }
            )
    return {"session_id": session_id, "items": items}


@app.post("/api/studio/media/use-as-ref")
async def studio_media_use_as_ref(request: dict):
    """Copy a media-bin still/identity into references/ for identity locking."""
    session_id = request.get("session_id")
    session = legacy_sessions.get(session_id or "")
    if not session:
        # Allow resume-from-disk then use
        try:
            session = _load_session_from_disk(str(session_id))
        except HTTPException:
            raise HTTPException(status_code=400, detail="Invalid session") from None
    name = str(request.get("name") or "").strip()
    kind = str(request.get("kind") or "still").strip().lower()
    if not name or "/" in name or "\\" in name or ".." in name:
        raise HTTPException(status_code=400, detail="Invalid asset name")
    subdir = {
        "still": "storyboard",
        "identity": "identity",
        "reference": "references",
        "clip": "videos",
    }.get(kind)
    if not subdir:
        raise HTTPException(status_code=400, detail="Unsupported media kind")
    if kind == "clip":
        raise HTTPException(
            status_code=400,
            detail="Clips cannot be used as still references — pick a still or identity",
        )
    src = _safe_session_asset(session_id, subdir, name)
    dest_dir = OUTPUT_DIR / session_id / "references"
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / f"ref_{src.stem}{src.suffix.lower()}"
    shutil.copy2(src, dest)
    refs = list(session.get("reference_images") or [])
    if str(dest) not in refs:
        refs.append(str(dest))
    session["reference_images"] = refs
    director = _get_director(session_id)
    director.reference_images = list(refs)
    _write_project_manifest(session_id, session)
    return {
        "session_id": session_id,
        "reference_path": str(dest),
        "reference_url": f"/api/session/{session_id}/asset/references/{dest.name}",
        "reference_count": len(refs),
    }


@app.post("/api/studio/export-vertical")
async def studio_export_vertical(request: dict):
    """Create a 9:16 center-crop derivative of the finished master."""
    session_id = request.get("session_id")
    session = legacy_sessions.get(session_id or "")
    if not session or not session.get("final_video_path"):
        raise HTTPException(status_code=400, detail="No finished video to export")
    src = Path(session["final_video_path"])
    if not src.is_file():
        raise HTTPException(status_code=404, detail="Final video file missing")
    dest = src.parent / f"{src.stem}_vertical.mp4"
    import subprocess

    cmd = [
        "ffmpeg",
        "-y",
        "-i",
        str(src),
        "-vf",
        "scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920",
        "-c:v",
        "libx264",
        "-preset",
        "fast",
        "-crf",
        "20",
        "-c:a",
        "aac",
        "-b:a",
        "192k",
        str(dest),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0 or not dest.is_file():
        raise HTTPException(
            status_code=500,
            detail=f"Vertical export failed: {(result.stderr or '')[-300:]}",
        )
    session["vertical_video_path"] = str(dest)
    return {
        "session_id": session_id,
        "download_url": f"/api/session/{session_id}/download-vertical",
    }


@app.get("/api/session/{session_id}/download-vertical")
async def download_vertical(session_id: str):
    session = legacy_sessions.get(session_id)
    if not session or not session.get("vertical_video_path"):
        raise HTTPException(status_code=404, detail="No vertical export")
    return FileResponse(
        session["vertical_video_path"],
        media_type="video/mp4",
        filename=f"scenemux_{session_id}_vertical.mp4",
    )


# ─── STATIC STUDIO UI (optional packaged / production build) ────────────
# When client/dist exists (npm run build), serve it so one process can host
# API + UI. Dev still uses Vite on :5173. Mounted last so /api/* wins.

_CLIENT_DIST = Path(__file__).resolve().parents[1] / "client" / "dist"
if _CLIENT_DIST.is_dir() and (_CLIENT_DIST / "index.html").is_file():
    from fastapi.staticfiles import StaticFiles

    app.mount(
        "/",
        StaticFiles(directory=str(_CLIENT_DIST), html=True),
        name="studio_ui",
    )
    LOG.info("Serving studio UI from %s", _CLIENT_DIST)


# ─── MAIN ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn

    _init_llm_backend()
    uvicorn.run(app, host=SERVER_HOST, port=SERVER_PORT)
