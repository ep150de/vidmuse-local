"""VidMuse Local — Server (B70 Workstation)

FastAPI backend serving LLM, image gen, video gen, and audio analysis over LAN.

New architecture (v2):
- llama.cpp backend (preferred) + Ollama fallback via server/llm/
- Hermes Director for autonomous music video generation
- Simplified API: single /api/pipeline/start endpoint
- WebSocket progress streaming for real-time updates
"""

import os
import uuid
import asyncio
import tempfile
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, UploadFile, File, Form, WebSocket, WebSocketDisconnect, HTTPException
from fastapi.responses import FileResponse, StreamingResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

# ─── Imports from new modules ───────────────────────────────────────────
from server.llm.base import LLMBackend
from server.llm.llm_factory import create_llm_backend, health_check_all, set_active_backend
from server.director.hermes_director import HermesDirector
from server.audio_analyzer import AudioAnalyzer
from server.image_generator import ComfyUIClient
from server.video_editor import VideoEditor

# ─── CONFIG ─────────────────────────────────────────────────────────────
try:
    from dotenv import load_dotenv
    from pathlib import Path as _P
    load_dotenv(_P(__file__).resolve().parents[1] / ".env")
    load_dotenv(_P(__file__).resolve().parent / ".env", override=True)
except ImportError:
    pass

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

# LLM backend (set at startup)
llm_backend: Optional[LLMBackend] = None

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

# ─── LEGACY ENDPOINTS (backward compat) ─────────────────────────────────

@app.post("/api/session/create")
async def create_session():
    """Create a new video generation session (legacy)."""
    session_id = str(uuid.uuid4())
    return {"session_id": session_id, "status": "created"}

@app.post("/api/upload/audio")
async def upload_audio(session_id: str = Form(...), file: UploadFile = File(...)):
    """Upload MP3 and analyze (legacy)."""
    session_dir = OUTPUT_DIR / session_id
    session_dir.mkdir(exist_ok=True)

    audio_path = session_dir / f"audio{Path(file.filename).suffix}"
    with open(audio_path, "wb") as f:
        shutil.copyfileobj(file.file, f)

    analysis = AudioAnalyzer.analyze(str(audio_path))
    return {"session_id": session_id, "analysis": analysis, "status": "analyzed"}

# ─── MAIN ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn

    # Initialize LLM backend
    global llm_backend
    llm_backend = create_llm_backend()
    if llm_backend:
        print(f"[VidMuse] LLM backend: {llm_backend.name} ({llm_backend.model_name})")
    else:
        print("[VidMuse] WARNING: No LLM backend available. Install llama-cpp-python or start Ollama.")

    uvicorn.run(app, host=SERVER_HOST, port=SERVER_PORT)
