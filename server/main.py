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

    load_dotenv(Path(__file__).resolve().parents[1] / ".env")
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

# ─── CONFIG ─────────────────────────────────────────────────────────────
OLLAMA_URL = os.getenv("OLLAMA_URL", "http://localhost:11434")
COMFYUI_URL = os.getenv("COMFYUI_URL", "http://localhost:8188")
VIDMUSE_MODE = (os.getenv("VIDMUSE_MODE") or "local").strip().lower()
SERVER_HOST = os.getenv("SERVER_HOST", "0.0.0.0")
SERVER_PORT = int(os.getenv("SERVER_PORT", 8000))
OUTPUT_DIR = Path(os.getenv("OUTPUT_DIR", "/tmp/vidmuse/outputs"))
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

# ─── APP ────────────────────────────────────────────────────────────────
app = FastAPI(title="VidMuse Local Server", version="2.0.0")
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
        active_directors[session_id] = director
    return active_directors[session_id]

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
        services["comfyui"] = await ComfyUIClient().health_check()
    except Exception as exc:
        services["comfyui"] = {"status": "down", "error": str(exc)}

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
        "version": "2.1.0",
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
    }
    return {"session_id": session_id, "status": "created"}


@app.post("/api/upload/audio")
async def upload_audio(session_id: str = Form(...), file: UploadFile = File(...)):
    """Upload MP3 and analyze (legacy)."""
    session_dir = OUTPUT_DIR / session_id
    session_dir.mkdir(parents=True, exist_ok=True)

    suffix = Path(file.filename or "audio.mp3").suffix or ".mp3"
    audio_path = session_dir / f"audio{suffix}"
    with open(audio_path, "wb") as f:
        shutil.copyfileobj(file.file, f)

    try:
        analysis = AudioAnalyzer.analyze(str(audio_path))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Audio analysis failed: {exc}") from exc

    session = legacy_sessions.setdefault(session_id, {"session_id": session_id})
    session["audio_path"] = str(audio_path)
    session["audio_analysis"] = analysis
    session["status"] = "analyzed"
    return {"session_id": session_id, "analysis": analysis, "status": "analyzed"}


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
                    "You are VidMuse, an expert AI music video director. "
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
    if session.get("audio_analysis"):
        director.audio_analysis = session["audio_analysis"]
    try:
        concepts = await director.generate_concepts(
            text_direction=session.get("concept_prompt") or "cinematic synthwave video",
            reference_images=session.get("reference_images") or None,
        )
    except Exception as exc:
        raise HTTPException(status_code=402, detail=friendly_llm_error(exc)) from exc
    session["concepts"] = concepts
    return {"session_id": session_id, "concepts": concepts}


@app.post("/api/concept/select")
async def select_concept(request: dict):
    session_id = request.get("session_id")
    session = legacy_sessions.get(session_id or "")
    if not session:
        return {"error": "Session not found"}
    session["selected_concept"] = request.get("concept_index", 0)
    session["status"] = "storyboarding"
    return {"session_id": session_id, "selected": session["selected_concept"]}


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
    if session.get("audio_analysis"):
        director.audio_analysis = session["audio_analysis"]
    if session.get("audio_path"):
        director.audio_path = session["audio_path"]
    frames = await director.generate_storyboard(concept=concept)
    session["storyboard"] = frames
    analysis = session.get("audio_analysis") or {}
    return {
        "session_id": session_id,
        "frames": frames,
        "plan": {
            "song_duration": analysis.get("duration"),
            "scene_count": len(frames),
            "tempo": analysis.get("tempo"),
        },
    }


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
    concepts = session.get("concepts") or []
    idx = session.get("selected_concept") or 0
    concept = concepts[idx] if isinstance(idx, int) and 0 <= idx < len(concepts) else {
        "style": "cinematic",
        "palette": ["#0b1629", "#06b6d4", "#f59e0b"],
    }
    director = _get_director(session_id)
    director.storyboard = session["storyboard"]
    generated = await director.generate_storyboard_images(concept=concept)
    session["storyboard"] = director.storyboard
    total = len(session["storyboard"])
    return {
        "session_id": session_id,
        "frames": session["storyboard"],
        "generated": generated,
        "summary": {
            "total": total,
            "succeeded": len(generated),
            "failed": max(0, total - len(generated)),
        },
    }


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
    if session.get("audio_path"):
        director.audio_path = session["audio_path"]
    if session.get("audio_analysis"):
        director.audio_analysis = session["audio_analysis"]
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
    if session.get("audio_analysis"):
        director.audio_analysis = session["audio_analysis"]
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
    return {
        "session_id": session_id,
        "video_path": final_path,
        "status": "done",
        "duration": (session.get("audio_analysis") or {}).get("duration"),
        "scenes": len(clips),
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


# ─── MAIN ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn

    _init_llm_backend()
    uvicorn.run(app, host=SERVER_HOST, port=SERVER_PORT)
