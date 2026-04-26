# VidMuse Local — AI Music Video Generator

Fully local, agentic music video creation. Upload an MP3 + reference images, chat with the AI director, and get a synced music video. No cloud. Runs on Ollama + ComfyUI + FFmpeg.

## Architecture

Client/server split over LAN:
- **Server** (B70 workstation): FastAPI, Ollama LLM, ComfyUI image/video gen, FFmpeg assembly
- **Client** (any browser): React + Vite + Tailwind UI

## Quick Start

### Server
```bash
cd server
pip install -r requirements.txt
# Ensure Ollama and ComfyUI are running
uvicorn main:app --host 0.0.0.0 --port 8000
```

### Client
```bash
cd client
npm install
VITE_SERVER_URL=http://localhost:8000 npm run dev
```

## Requirements

- **GPU:** NVIDIA RTX 3060 12GB+ or Intel Arc A770 16GB+ (32GB+ recommended)
- **RAM:** 32 GB (64 GB for 70B models)
- **Ollama:** `qwen2.5:32b` or similar
- **ComfyUI:** FLUXDev fp8 + Wan 2.1 i2v 480p fp8
- **FFmpeg:** with libx264/libx265

## License

MIT — Private repository, not for distribution without permission.
