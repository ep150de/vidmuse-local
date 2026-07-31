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
    if session.get("performance_mix"):
        director.performance_mix = session["performance_mix"]
    director.project_mode = session.get("project_mode") or "mtv"
    comfy_backend = session.get("comfy_backend")
    comfy_url = session.get("comfy_url")
    if comfy_backend and (
        director.comfy_backend != comfy_backend
        or (comfy_url and director.comfy_url != comfy_url.rstrip("/"))
    ):
        director.configure_comfy(comfy_backend, comfy_url)
    if session.get("reference_images"):
        director.reference_images = list(session["reference_images"])
    if session.get("identity_sheet_path"):
        director.identity_sheet_path = session["identity_sheet_path"]
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
        "singer_profile": {},
        "video_backend": "infinitetalk",
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
    backend = str(request.get("video_backend") or session.get("video_backend") or "infinitetalk")
    backend = backend.strip().lower()
    if backend not in ("infinitetalk", "ltx23", "humo"):
        backend = "infinitetalk"
    session["video_backend"] = backend

    # Optional pasted lyrics (overrides / supplements Whisper later)
    pasted = str(request.get("lyrics") or "").strip()
    if pasted:
        from server.lyrics import compact_lyrics

        lyrics = compact_lyrics(pasted, max_chars=8000)
        session["lyrics"] = lyrics
        analysis = session.get("audio_analysis") or {}
        analysis["lyrics"] = lyrics
        analysis["lyrics_source"] = "user"
        analysis["lyrics_cues"] = analysis.get("lyrics_cues") or []
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

    comfy_backend = str(
        request.get("comfy_backend") or session.get("comfy_backend") or COMFYUI_BACKEND
    ).strip().lower()
    if comfy_backend not in ("cloud", "local", "ai-playground"):
        comfy_backend = COMFYUI_BACKEND
    comfy_url = str(
        request.get("comfy_url") or session.get("comfy_url") or COMFYUI_URL
    ).strip().rstrip("/")
    if not comfy_url.startswith(("http://", "https://")):
        raise HTTPException(status_code=400, detail="Comfy URL must start with http:// or https://")
    session["comfy_backend"] = comfy_backend
    session["comfy_url"] = comfy_url

    director = _get_director(session_id)
    director.singer_profile = session.get("singer_profile") or {}
    director.video_backend = backend
    director.performance_mix = mix
    director.project_mode = session["project_mode"]
    director.configure_comfy(comfy_backend, comfy_url)
    director.character_lock = ""  # rebuild with new singer details
    _write_project_manifest(session_id, session)
    return {
        "session_id": session_id,
        "singer_profile": session.get("singer_profile"),
        "video_backend": backend,
        "performance_mix": mix,
        "project_mode": session["project_mode"],
        "comfy_backend": comfy_backend,
        "comfy_url": comfy_url,
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
    if not director.identity_sheet_path:
        return None
    return (
        f"/api/session/{session_id}/asset/identity/"
        f"{Path(director.identity_sheet_path).name}"
    )


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
