# VidMuse Local — AI Music Video Generator

Fully local, agentic music video creation. Upload an MP3 + text direction, and get a synced music video. No cloud processing. Runs on llama.cpp + ComfyUI + FFmpeg.

## Architecture

**Single-workstation pipeline:**

- **llama.cpp** — Local LLM inference (preferred over Ollama) for concept generation, storyboard prompts, and video motion descriptions
- **ComfyUI** — Image generation (z-image turbo for fast reference images, FLUX Dev fallback) + Wan 2.1 image-to-video
- **Hermes Director** — Autonomous orchestrator that drives the full pipeline from audio upload to final MP4
- **FFmpeg** — Final video assembly synced to music beats

**How it works:**

1. Upload an MP3 file + provide a text direction (e.g., "neon synthwave cityscape at night")
2. Optionally provide reference images OR a prompt to generate reference images
3. The Hermes Director autonomously:
   - Analyzes the audio (tempo, beats, energy, segments)
   - Generates 3 video concepts via LLM
   - Creates reference images via ComfyUI z-image turbo
   - Generates storyboard prompts and images
   - Produces video clips via Wan 2.1
   - Assembles the final MP4 synced to music beats
4. Download your final video

## Quick Start

### Docker Deployment (Recommended)

```bash
# 1. Start ComfyUI + Server
docker compose up -d

# 2. Download ComfyUI models (one-time, ~50GB)
mkdir -p ComfyUI/models/diffusion_models
# Place these in ComfyUI/models/diffusion_models/:
#   - flux1-dev-fp8.safetensors (~17GB)
#   - wan2.1_i2v_480p_14B_fp8.safetensors (~30GB)
#   - zimage_turbo.safetensors (~17GB)
#   - ip-adapter_sd15.safetensors (~300MB)

# 3. Download GGUF model for llama.cpp
# Place a GGUF file in the models/ directory:
#   wget https://huggingface.co/bartowski/qwen2.5-32B-Instruct-GGUF/resolve/main/qwen2.5-32b-instruct-Q4_K_M.gguf
#   mv qwen2.5-32b-instruct-Q4_K_M.gguf models/

# 4. Start client
cd client
npm install
VITE_SERVER_URL=http://localhost:8000 npm run dev
# Open http://localhost:5173
```

### Standalone Server

```bash
cd server
pip install -r requirements.txt
uvicorn main:app --host 0.0.0.0 --port 8000
```

Ensure ComfyUI is running on port 8188 before starting the server.

## Requirements

- **GPU:** NVIDIA RTX 3060 12GB+ or Intel Arc A770 16GB+ (32GB+ recommended)
- **RAM:** 32 GB (64 GB recommended for large models)
- **llama.cpp:** GGUF models placed in `server/models/`
- **ComfyUI:** z-image turbo + Wan 2.1 i2v 480p + FLUX Dev fp8
- **FFmpeg:** with libx264/libx265

## API

### Start Pipeline (Main Endpoint)

```bash
curl -X POST http://localhost:8000/api/pipeline/start \
  -F "session_id=my-session-001" \
  -F "text_direction=neon synthwave cityscape at night with neon lights and rain" \
  -F "audio=@song.mp3"
```

### Check Health

```bash
curl http://localhost:8000/api/health
```

### WebSocket Progress

```
ws://localhost:8000/api/ws/{client_id}
```

## License

MIT — Private repository, not for distribution without permission.