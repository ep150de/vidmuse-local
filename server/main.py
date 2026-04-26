"""
VidMuse Local — Server (B70 Workstation)
FastAPI backend serving LLM, image gen, video gen, and audio analysis over LAN
"""
import os, json, uuid, asyncio, tempfile, shutil
from datetime import datetime
from typing import Optional, List, Dict, Any
from pathlib import Path

from fastapi import FastAPI, UploadFile, File, Form, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import librosa
import numpy as np
import requests

# ─── CONFIG ──────────────────────────────────────────────────────────
OLLAMA_URL = os.getenv("OLLAMA_URL", "http://localhost:11434")
COMFYUI_URL = os.getenv("COMFYUI_URL", "http://localhost:8188")
SERVER_HOST = os.getenv("SERVER_HOST", "0.0.0.0")
SERVER_PORT = int(os.getenv("SERVER_PORT", 8000))
OUTPUT_DIR = Path(os.getenv("OUTPUT_DIR", "/home/lingleblater/vidmuse-outputs"))
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

# ─── APP ─────────────────────────────────────────────────────────────
app = FastAPI(title="VidMuse Local Server", version="1.0.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # LAN access
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ─── STATE ───────────────────────────────────────────────────────────
active_sessions: Dict[str, dict] = {}
connected_clients: Dict[str, WebSocket] = {}

# ─── MODELS ──────────────────────────────────────────────────────────
class ChatMessage(BaseModel):
    session_id: str
    role: str  # "user" | "agent"
    content: str
    phase: str = "ideation"  # ideation | storyboard | video | edit

class StoryboardFrame(BaseModel):
    id: str
    timestamp: float  # seconds into song
    prompt: str
    image_path: Optional[str] = None
    video_path: Optional[str] = None
    approved: bool = False

class ProjectState(BaseModel):
    session_id: str
    audio_path: Optional[str] = None
    reference_images: List[str] = []
    concept_prompt: str = ""
    audio_analysis: Optional[dict] = None
    chat_history: List[ChatMessage] = []
    storyboard: List[StoryboardFrame] = []
    selected_concept: Optional[str] = None
    final_video_path: Optional[str] = None
    status: str = "created"  # created | analyzing | ideating | storyboarding | generating | editing | done

# ─── AUDIO ANALYSIS ──────────────────────────────────────────────────
class AudioAnalyzer:
    def analyze(self, audio_path: str) -> dict:
        y, sr = librosa.load(audio_path, sr=None, mono=True)
        duration = librosa.get_duration(y=y, sr=sr)
        tempo, beat_frames = librosa.beat.beat_track(y=y, sr=sr)
        beat_times = librosa.frames_to_time(beat_frames, sr=sr).tolist()
        
        # Energy per beat
        rms = librosa.feature.rms(y=y)[0]
        beat_energy = [float(np.mean(rms[max(0, f-5):f+5])) for f in beat_frames]
        
        # Spectral features
        spectral_centroid = librosa.feature.spectral_centroid(y=y, sr=sr)[0]
        spectral_rolloff = librosa.feature.spectral_rolloff(y=y, sr=sr)[0]
        
        # Segment structure (simple: divide into 4 parts)
        segment_duration = duration / 4
        segments = [
            {"name": "intro", "start": 0, "end": segment_duration},
            {"name": "verse", "start": segment_duration, "end": segment_duration * 2},
            {"name": "chorus", "start": segment_duration * 2, "end": segment_duration * 3},
            {"name": "outro", "start": segment_duration * 3, "end": duration},
        ]
        
        # Detect chorus (highest energy segment)
        segment_energies = []
        for seg in segments:
            start_frame = int(seg["start"] * sr / 512)
            end_frame = int(seg["end"] * sr / 512)
            seg_energy = float(np.mean(rms[start_frame:end_frame]))
            segment_energies.append(seg_energy)
        
        max_seg_idx = int(np.argmax(segment_energies))
        segments[max_seg_idx]["name"] = "chorus"  # rename highest energy to chorus
        
        return {
            "duration": float(duration),
            "tempo": float(tempo),
            "beat_count": len(beat_times),
            "beat_times": beat_times[:20],  # first 20 beats
            "energy_profile": beat_energy[:20],
            "brightness": float(np.mean(spectral_centroid)),
            "segments": segments,
            "file_format": Path(audio_path).suffix,
        }

audio_analyzer = AudioAnalyzer()

# ─── LLM AGENT ───────────────────────────────────────────────────────
class VidMuseAgent:
    SYSTEM_PROMPT = """You are VidMuse, an expert AI music video director.
You help creators turn their songs into stunning visual experiences.

Your workflow has 4 phases:
1. IDEATION: Analyze the song + user prompt, propose 3 video concepts
2. STORYBOARD: Break the song into segments, generate frame prompts
3. VIDEO: Create motion from storyboard images
4. EDIT: Sync everything to the music beats

Be creative, cinematic, and specific. Use the NbNW synthwave aesthetic:
- Dark navy backgrounds (#0b1629)
- Neon cyan (#06b6d4) and gold (#f59e0b) accents
- 80s retro sci-fi geometric shapes
- Space-age design

When proposing concepts, always provide:
- Title
- Visual style (color palette, mood)
- Narrative arc (what happens when)
- Key visual moments (drops, chorus, transitions)
"""

    def __init__(self):
        self.model = "qwen2.5:32b"
    
    async def chat(self, session_id: str, user_message: str, context: dict) -> str:
        """Send message to Ollama and return response"""
        messages = [
            {"role": "system", "content": self.SYSTEM_PROMPT},
        ]
        
        # Add context
        if context.get("audio_analysis"):
            analysis = context["audio_analysis"]
            messages.append({
                "role": "system",
                "content": f"SONG ANALYSIS: Duration {analysis['duration']:.1f}s, Tempo {analysis['tempo']:.0f} BPM, {analysis['beat_count']} beats"
            })
        
        if context.get("chat_history"):
            for msg in context["chat_history"][-10:]:  # last 10 messages
                messages.append({"role": msg["role"], "content": msg["content"]})
        
        messages.append({"role": "user", "content": user_message})
        
        try:
            resp = requests.post(
                f"{OLLAMA_URL}/api/chat",
                json={"model": self.model, "messages": messages, "stream": False},
                timeout=120
            )
            resp.raise_for_status()
            return resp.json()["message"]["content"]
        except Exception as e:
            return f"[Error contacting LLM: {e}]"
    
    async def generate_concepts(self, session_id: str, context: dict) -> List[dict]:
        """Generate 3 video concepts based on song analysis + user prompt"""
        prompt = f"""Based on this song and the user's vision, propose 3 distinct music video concepts.

SONG: {context.get('audio_analysis', {}).get('duration', 0):.1f}s, {context.get('audio_analysis', {}).get('tempo', 0):.0f} BPM
USER PROMPT: {context.get('concept_prompt', 'No specific prompt')}

Respond with EXACTLY this JSON format:
{{
  "concepts": [
    {{
      "title": "Concept name",
      "style": "Visual style description",
      "palette": ["#hex1", "#hex2", "#hex3"],
      "narrative": "What happens from start to finish",
      "key_moments": {{"intro": "...", "verse": "...", "chorus": "...", "outro": "..."}},
      "complexity": "simple|medium|complex"
    }}
  ]
}}
"""
        response = await self.chat(session_id, prompt, context)
        
        # Try to parse JSON from response
        try:
            # Find JSON in response
            start = response.find("{")
            end = response.rfind("}") + 1
            if start >= 0 and end > start:
                data = json.loads(response[start:end])
                return data.get("concepts", [])
        except:
            pass
        
        # Fallback: return default concepts
        return [
            {
                "title": "Synthwave Lunar Journey",
                "style": "80s retro sci-fi with neon grids and space vistas",
                "palette": ["#0b1629", "#f59e0b", "#06b6d4"],
                "narrative": "Journey from Earth to Moon, building the mass driver, launching into space",
                "key_moments": {
                    "intro": "Earth at night, cities glowing",
                    "verse": "Moon surface, robots constructing",
                    "chorus": "Mass driver launch, pod ascending",
                    "outro": "Pod arriving at Earth, new dawn"
                },
                "complexity": "medium"
            }
        ]
    
    async def generate_storyboard(self, session_id: str, concept: dict, audio_analysis: dict) -> List[StoryboardFrame]:
        """Generate storyboard prompts for each song segment"""
        segments = audio_analysis.get("segments", [])
        frames = []
        
        for i, seg in enumerate(segments):
            # Generate detailed prompt for this segment
            seg_name = seg["name"]
            seg_prompt = f"""Generate a detailed image generation prompt for the {seg_name} segment of a music video.

CONCEPT: {concept['title']}
STYLE: {concept['style']}
PALETTE: {', '.join(concept['palette'])}

SEGMENT: {seg_name} ({seg['start']:.1f}s - {seg['end']:.1f}s)
KEY MOMENT: {concept['key_moments'].get(seg_name, 'Cinematic visual')}

Respond with a single detailed prompt (100-200 words) suitable for an AI image generator like FLUX."""
            
            prompt_response = await self.chat(session_id, seg_prompt, {})
            
            frames.append(StoryboardFrame(
                id=f"frame_{i:02d}",
                timestamp=seg["start"],
                prompt=prompt_response.strip()
            ))
        
        return frames
    
    async def generate_video_prompt(self, session_id: str, frame: StoryboardFrame) -> str:
        """Convert storyboard image prompt to video motion prompt"""
        prompt = f"""Convert this image prompt into a video motion prompt:

IMAGE PROMPT: {frame.prompt}

Add camera movement, motion description, and temporal details.
Respond with a single detailed video prompt (50-100 words)."""
        
        return await self.chat(session_id, prompt, {})

agent = VidMuseAgent()

# ─── COMFYUI CLIENT ──────────────────────────────────────────────────
class ComfyUIClient:
    """Async client for ComfyUI running on the B70"""
    
    def __init__(self):
        self.url = COMFYUI_URL
        self.client_id = str(uuid.uuid4())
    
    async def generate_image(self, prompt: str, width: int = 1024, height: int = 576) -> str:
        """Generate image using FLUX workflow"""
        # Load workflow template
        workflow = self._load_flux_workflow()
        workflow["6"]["inputs"]["text"] = prompt
        workflow["5"]["inputs"]["width"] = width
        workflow["5"]["inputs"]["height"] = height
        workflow["5"]["inputs"]["seed"] = np.random.randint(0, 2**32)
        
        # Queue prompt
        resp = requests.post(f"{self.url}/prompt", json={
            "prompt": workflow,
            "client_id": self.client_id
        })
        prompt_id = resp.json()["prompt_id"]
        
        # Poll for completion (simplified)
        image_path = await self._wait_for_image(prompt_id)
        return image_path
    
    async def generate_video(self, image_path: str, prompt: str, num_frames: int = 81) -> str:
        """Generate video from image using Wan workflow"""
        workflow = self._load_wan_workflow()
        workflow["10"]["inputs"]["image"] = image_path
        workflow["6"]["inputs"]["text"] = prompt
        workflow["15"]["inputs"]["frame_count"] = num_frames
        
        resp = requests.post(f"{self.url}/prompt", json={
            "prompt": workflow,
            "client_id": self.client_id
        })
        prompt_id = resp.json()["prompt_id"]
        
        video_path = await self._wait_for_video(prompt_id)
        return video_path
    
    def _load_flux_workflow(self) -> dict:
        """Load FLUX image generation workflow"""
        # Simplified — in production load from JSON file
        return {
            "5": {"inputs": {"width": 1024, "height": 576, "batch_size": 1}, "class_type": "EmptyLatentImage"},
            "6": {"inputs": {"text": "", "clip": ["11", 0]}, "class_type": "CLIPTextEncode"},
            "11": {"inputs": {"ckpt_name": "flux1-dev-fp8.safetensors"}, "class_type": "CheckpointLoaderNF4"},
            # ... simplified
        }
    
    def _load_wan_workflow(self) -> dict:
        """Load Wan image-to-video workflow"""
        return {
            "10": {"inputs": {"image": ""}, "class_type": "LoadImage"},
            "6": {"inputs": {"text": ""}, "class_type": "CLIPTextEncode"},
            # ... simplified
        }
    
    async def _wait_for_image(self, prompt_id: str) -> str:
        """Poll ComfyUI until image is ready"""
        # Simplified — in production use WebSocket
        await asyncio.sleep(30)  # Placeholder
        return f"outputs/{prompt_id}_00001_.png"
    
    async def _wait_for_video(self, prompt_id: str) -> str:
        """Poll ComfyUI until video is ready"""
        await asyncio.sleep(180)  # Video takes longer
        return f"outputs/{prompt_id}_00001_.mp4"

comfy_client = ComfyUIClient()

# ─── VIDEO EDITOR ────────────────────────────────────────────────────
class VideoEditor:
    """Assemble final video synced to beats"""
    
    def __init__(self, audio_path: str):
        self.audio_path = audio_path
        self.y, self.sr = librosa.load(audio_path, sr=None)
        _, self.beat_frames = librosa.beat.beat_track(y=self.y, sr=self.sr)
        self.beat_times = librosa.frames_to_time(self.beat_frames, sr=self.sr)
    
    def assemble(self, video_clips: List[str], output_path: str):
        """Assemble clips synced to beat drops"""
        # Use FFmpeg directly for speed
        # Create concat file
        concat_list = []
        for i, clip in enumerate(video_clips):
            # Determine duration based on beat intervals
            if i < len(self.beat_times) - 1:
                duration = self.beat_times[i + 1] - self.beat_times[i]
            else:
                duration = 2.0
            
            concat_list.append(f"file '{clip}'")
            concat_list.append(f"duration {duration:.3f}")
        
        concat_path = OUTPUT_DIR / f"concat_{uuid.uuid4().hex}.txt"
        concat_path.write_text("\n".join(concat_list))
        
        # FFmpeg concat with audio
        cmd = [
            "ffmpeg", "-y",
            "-f", "concat", "-safe", "0",
            "-i", str(concat_path),
            "-i", self.audio_path,
            "-c:v", "libx264", "-preset", "medium", "-crf", "23",
            "-c:a", "aac", "-b:a", "192k",
            "-shortest",
            "-movflags", "+faststart",
            output_path
        ]
        
        import subprocess
        subprocess.run(cmd, check=True)
        concat_path.unlink()
        
        return output_path

# ─── WEBSOCKET MANAGER ───────────────────────────────────────────────
class ConnectionManager:
    def __init__(self):
        self.active_connections: Dict[str, WebSocket] = {}
    
    async def connect(self, websocket: WebSocket, client_id: str):
        await websocket.accept()
        self.active_connections[client_id] = websocket
    
    def disconnect(self, client_id: str):
        self.active_connections.pop(client_id, None)
    
    async def send_progress(self, client_id: str, phase: str, progress: int, status: str):
        if client_id in self.active_connections:
            await self.active_connections[client_id].send_json({
                "type": "progress",
                "phase": phase,
                "progress": progress,
                "status": status,
                "timestamp": datetime.now().isoformat()
            })
    
    async def broadcast(self, message: dict):
        for conn in self.active_connections.values():
            await conn.send_json(message)

manager = ConnectionManager()

# ─── API ROUTES ──────────────────────────────────────────────────────

@app.post("/api/session/create")
async def create_session():
    """Create a new video generation session"""
    session_id = str(uuid.uuid4())
    active_sessions[session_id] = ProjectState(
        session_id=session_id,
        status="created"
    ).dict()
    return {"session_id": session_id, "status": "created"}

@app.post("/api/upload/audio")
async def upload_audio(session_id: str = Form(...), file: UploadFile = File(...)):
    """Upload MP3 and analyze"""
    session_dir = OUTPUT_DIR / session_id
    session_dir.mkdir(exist_ok=True)
    
    audio_path = session_dir / f"audio{Path(file.filename).suffix}"
    with open(audio_path, "wb") as f:
        shutil.copyfileobj(file.file, f)
    
    # Analyze audio
    analysis = audio_analyzer.analyze(str(audio_path))
    
    # Update session
    if session_id in active_sessions:
        active_sessions[session_id]["audio_path"] = str(audio_path)
        active_sessions[session_id]["audio_analysis"] = analysis
        active_sessions[session_id]["status"] = "analyzing"
    
    return {
        "session_id": session_id,
        "analysis": analysis,
        "status": "analyzed"
    }

@app.post("/api/upload/images")
async def upload_images(session_id: str = Form(...), files: List[UploadFile] = File(...)):
    """Upload reference images"""
    session_dir = OUTPUT_DIR / session_id / "references"
    session_dir.mkdir(parents=True, exist_ok=True)
    
    image_paths = []
    for file in files:
        img_path = session_dir / file.filename
        with open(img_path, "wb") as f:
            shutil.copyfileobj(file.file, f)
        image_paths.append(str(img_path))
    
    if session_id in active_sessions:
        active_sessions[session_id]["reference_images"] = image_paths
    
    return {"session_id": session_id, "images": image_paths}

@app.post("/api/chat")
async def chat_endpoint(request: dict):
    """Agentic chat — the heart of the workflow"""
    session_id = request["session_id"]
    message = request["message"]
    
    if session_id not in active_sessions:
        return {"error": "Session not found"}
    
    session = active_sessions[session_id]
    
    # Add user message to history
    session["chat_history"].append({
        "role": "user",
        "content": message,
        "phase": session.get("status", "ideation")
    })
    
    # Get agent response
    response = await agent.chat(session_id, message, session)
    
    # Add agent response
    session["chat_history"].append({
        "role": "assistant",
        "content": response,
        "phase": session.get("status", "ideation")
    })
    
    # Check for phase transitions
    if "concept" in message.lower() or "idea" in message.lower():
        session["status"] = "ideating"
    elif "storyboard" in message.lower():
        session["status"] = "storyboarding"
    elif "generate" in message.lower() and "video" in message.lower():
        session["status"] = "generating"
    
    return {
        "session_id": session_id,
        "response": response,
        "phase": session["status"],
        "history": session["chat_history"][-6:]  # last 6 messages
    }

@app.post("/api/concepts/generate")
async def generate_concepts(request: dict):
    """Generate 3 video concepts based on analysis + prompt"""
    session_id = request["session_id"]
    
    if session_id not in active_sessions:
        return {"error": "Session not found"}
    
    session = active_sessions[session_id]
    concepts = await agent.generate_concepts(session_id, session)
    
    return {
        "session_id": session_id,
        "concepts": concepts
    }

@app.post("/api/concept/select")
async def select_concept(request: dict):
    """User selects a concept to proceed with"""
    session_id = request["session_id"]
    concept_index = request["concept_index"]
    
    session = active_sessions.get(session_id)
    if not session:
        return {"error": "Session not found"}
    
    # Store selected concept
    session["selected_concept"] = concept_index
    session["status"] = "storyboarding"
    
    return {"session_id": session_id, "selected": concept_index}

@app.post("/api/storyboard/generate")
async def generate_storyboard(request: dict):
    """Generate storyboard frames for selected concept"""
    session_id = request["session_id"]
    session = active_sessions.get(session_id)
    
    if not session or not session.get("selected_concept"):
        return {"error": "No concept selected"}
    
    # Get concept details
    concept = {}  # In production, retrieve from stored concepts
    analysis = session.get("audio_analysis", {})
    
    frames = await agent.generate_storyboard(session_id, concept, analysis)
    
    session["storyboard"] = [f.dict() for f in frames]
    session["status"] = "storyboarding"
    
    return {
        "session_id": session_id,
        "frames": [f.dict() for f in frames]
    }

@app.post("/api/storyboard/generate-images")
async def generate_storyboard_images(request: dict):
    """Generate images for all storyboard frames"""
    session_id = request["session_id"]
    session = active_sessions.get(session_id)
    
    if not session or not session.get("storyboard"):
        return {"error": "No storyboard"}
    
    session_dir = OUTPUT_DIR / session_id / "storyboard"
    session_dir.mkdir(parents=True, exist_ok=True)
    
    generated = []
    for i, frame in enumerate(session["storyboard"]):
        # Notify client
        await manager.send_progress(
            session_id, "storyboard",
            int((i / len(session["storyboard"])) * 100),
            f"Generating frame {i+1}/{len(session['storyboard'])}"
        )
        
        # Generate image via ComfyUI
        image_path = await comfy_client.generate_image(
            frame["prompt"],
            width=1024,
            height=576
        )
        
        # Copy to session dir
        dest = session_dir / f"frame_{i:02d}.png"
        shutil.copy(image_path, dest)
        
        frame["image_path"] = str(dest)
        generated.append({"frame_id": frame["id"], "path": str(dest)})
    
    return {
        "session_id": session_id,
        "generated": generated
    }

@app.post("/api/storyboard/approve")
async def approve_storyboard(request: dict):
    """User approves storyboard, proceed to video generation"""
    session_id = request["session_id"]
    approved_frames = request.get("approved_frames", [])  # list of frame IDs
    
    session = active_sessions.get(session_id)
    if not session:
        return {"error": "Session not found"}
    
    # Mark approved frames
    for frame in session["storyboard"]:
        frame["approved"] = frame["id"] in approved_frames
    
    session["status"] = "generating"
    
    return {"session_id": session_id, "status": "approved"}

@app.post("/api/video/generate")
async def generate_video_clips(request: dict):
    """Generate video clips from approved storyboard frames"""
    session_id = request["session_id"]
    session = active_sessions.get(session_id)
    
    if not session:
        return {"error": "Session not found"}
    
    session_dir = OUTPUT_DIR / session_id / "videos"
    session_dir.mkdir(parents=True, exist_ok=True)
    
    approved = [f for f in session["storyboard"] if f.get("approved")]
    generated = []
    
    for i, frame in enumerate(approved):
        await manager.send_progress(
            session_id, "video",
            int((i / len(approved)) * 100),
            f"Animating frame {i+1}/{len(approved)}"
        )
        
        # Generate video motion prompt
        video_prompt = await agent.generate_video_prompt(session_id, StoryboardFrame(**frame))
        
        # Generate video via ComfyUI
        video_path = await comfy_client.generate_video(
            frame["image_path"],
            video_prompt,
            num_frames=81  # ~3.3 seconds at 24fps
        )
        
        dest = session_dir / f"clip_{i:02d}.mp4"
        shutil.copy(video_path, dest)
        
        frame["video_path"] = str(dest)
        generated.append({"frame_id": frame["id"], "path": str(dest)})
    
    session["status"] = "editing"
    
    return {
        "session_id": session_id,
        "generated": generated
    }

@app.post("/api/edit/final")
async def edit_final_video(request: dict):
    """Assemble final video synced to audio beats"""
    session_id = request["session_id"]
    session = active_sessions.get(session_id)
    
    if not session or not session.get("audio_path"):
        return {"error": "No audio uploaded"}
    
    # Collect video clips
    clips = []
    for frame in session.get("storyboard", []):
        if frame.get("video_path") and frame.get("approved"):
            clips.append(frame["video_path"])
    
    if not clips:
        return {"error": "No video clips generated"}
    
    # Assemble
    output_path = str(OUTPUT_DIR / session_id / "final_video.mp4")
    editor = VideoEditor(session["audio_path"])
    editor.assemble(clips, output_path)
    
    session["final_video_path"] = output_path
    session["status"] = "done"
    
    return {
        "session_id": session_id,
        "video_path": output_path,
        "status": "done"
    }

@app.get("/api/download/{session_id}")
async def download_video(session_id: str):
    """Download final video"""
    session = active_sessions.get(session_id)
    if not session or not session.get("final_video_path"):
        return {"error": "Video not ready"}
    
    return FileResponse(
        session["final_video_path"],
        media_type="video/mp4",
        filename=f"vidmuse_{session_id}.mp4"
    )

@app.get("/api/session/{session_id}")
async def get_session(session_id: str):
    """Get full session state"""
    session = active_sessions.get(session_id)
    if not session:
        return {"error": "Session not found"}
    return session

# ─── WEBSOCKET ───────────────────────────────────────────────────────
@app.websocket("/api/ws/{client_id}")
async def websocket_endpoint(websocket: WebSocket, client_id: str):
    await manager.connect(websocket, client_id)
    try:
        while True:
            data = await websocket.receive_text()
            # Handle client messages if needed
            await websocket.send_json({"type": "ack", "received": data})
    except WebSocketDisconnect:
        manager.disconnect(client_id)

# ─── HEALTH ──────────────────────────────────────────────────────────
@app.get("/api/health")
async def health():
    """Check server status and available services"""
    services = {}
    
    # Check Ollama
    try:
        resp = requests.get(f"{OLLAMA_URL}/api/tags", timeout=5)
        services["ollama"] = {"status": "up", "models": [m["name"] for m in resp.json().get("models", [])]}
    except:
        services["ollama"] = {"status": "down"}
    
    # Check ComfyUI
    try:
        resp = requests.get(f"{COMFYUI_URL}/system_stats", timeout=5)
        services["comfyui"] = {"status": "up"}
    except:
        services["comfyui"] = {"status": "down"}
    
    return {
        "status": "healthy",
        "services": services,
        "gpu": "Intel Arc Pro B70",
        "vram_gb": 32
    }

# ─── MAIN ────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host=SERVER_HOST, port=SERVER_PORT)
