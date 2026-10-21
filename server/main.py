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
    payload = {
        "session_id": session_id,
        "project_mode": session.get("project_mode") or "mtv",
        "creative_direction": session.get("creative_direction") or "",
        "comfy": {
            "backend": session.get("comfy_backend") or COMFYUI_BACKEND or "local",
            "url": session.get("comfy_url") or COMFYUI_URL,
        },
        "pipeline": {
            "director": session.get("pipeline_director") or "cloud_llm",
            "stills": session.get("pipeline_stills") or "comfy",
            "video": session.get("pipeline_video") or "comfy_cloud",
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
    """TCP reachability probe for Home Agent local-web (no password)."""
    from server.home_agent.client import HomeAgentClient, HomeAgentError

    url = str(
        request.get("url") or os.getenv("HOME_AGENT_URL") or ""
    ).strip().rstrip("/")
    if not url:
        raise HTTPException(status_code=400, detail="Home Agent URL required")
    client = HomeAgentClient(url, password="")
    try:
        return await client.diagnose()
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
            session["status"] = "done"
            _write_project_manifest(session_id, session)
            if session_id in connected_clients:
                try:
                    await connected_clients[session_id].send_json(
                        {
                            "type": "studio_complete",
                            "timeline_plan": _studio_plan_public(
                                session_id, director.timeline_plan
                            ),
                            "download_url": f"/api/download/{session_id}",
                        }
                    )
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
    for path in sorted(OUTPUT_DIR.glob("*/project.json"), key=lambda p: p.stat().st_mtime, reverse=True):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        sid = data.get("session_id") or path.parent.name
        final = path.parent / f"vidmuse_{sid}.mp4"
        # Also accept any assembled mp4
        finals = list(path.parent.glob("vidmuse_*.mp4")) + list(path.parent.glob("*_final.mp4"))
        projects.append(
            {
                "session_id": sid,
                "creative_direction": (data.get("creative_direction") or "")[:160],
                "pipeline": data.get("pipeline") or {},
                "comfy": data.get("comfy") or {},
                "has_final": final.is_file() or any(f.is_file() for f in finals),
                "mtime": path.stat().st_mtime,
            }
        )
        if len(projects) >= 24:
            break
    return {"projects": projects}


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
    n = len(remaining) if remaining else len(scenes)
    # Rough heuristics by host + engine
    if video == "fal":
        per = "1 FAL draft clip"
        minutes = n * 2
    elif video == "home_agent":
        per = "1 Home Agent H3 job"
        minutes = n * 8
    elif video_model in ("fasth3", "hybrid_fast"):
        per = (
            "1 FastH3 draft (PERF→H3 Ref2VA if hybrid)"
            if video_model == "hybrid_fast"
            else "1 FastH3 8-step draft"
        )
        minutes = n * (3 if video == "comfy_cloud" else 4)
    elif video_model in ("ltx23", "hybrid_ltx"):
        per = (
            "1 LTX IA2V (PERF→H3 Ref2VA if hybrid)"
            if video_model == "hybrid_ltx"
            else "1 LTX-2.3 IA2V job"
        )
        minutes = n * (3 if video == "comfy_cloud" else 5)
    elif video == "comfy_cloud":
        per = "1 Comfy Cloud H3 Ref2VA job"
        minutes = n * 4
    else:
        per = "1 local / Playground H3 Ref2VA job"
        minutes = n * 6
    return {
        "session_id": session_id,
        "scene_total": len(scenes),
        "scenes_to_render": n,
        "pipeline_video": video,
        "video_model": video_model,
        "per_scene": per,
        "est_minutes_low": max(1, int(minutes * 0.6)),
        "est_minutes_high": max(2, int(minutes * 1.4)),
        "note": (
            "Estimates are approximate. FastH3/LTX are drafts — not Ref2VA lip-sync quality."
        ),
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


# ─── MAIN ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn

    _init_llm_backend()
    uvicorn.run(app, host=SERVER_HOST, port=SERVER_PORT)
