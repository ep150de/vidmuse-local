# Docker Deployment — VidMuse Local

## Prerequisites

- Docker Engine 24+ with NVIDIA Container Toolkit
- NVIDIA GPU with drivers installed
- At least 32GB free disk space for models

## Quick Start

1. **Clone and enter the repo**
```bash
git clone <your-repo>
cd vidmuse-local
```

2. **Pull large model files** (one-time, ~50GB total)
```bash
mkdir -p ComfyUI/models/diffusion_models
# Download these manually from HuggingFace:
# - flux1-dev-fp8.safetensors (~17GB)
# - wan2.1_i2v_480p_14B_fp8.safetensors (~30GB)
# - ip-adapter_sd15.safetensors (~300MB)
# Place them in ComfyUI/models/diffusion_models/
```

3. **Start all services**
```bash
docker compose up -d
```

This starts:
- Ollama on http://localhost:11434
- ComfyUI on http://localhost:8188  
- VidMuse server on http://localhost:8000

4. **Pull Ollama model** (inside container)
```bash
docker exec vidmuse-ollama ollama pull qwen2.5:32b
```

5. **Start client**
```bash
cd client
npm install
VITE_SERVER_URL=http://localhost:8000 npm run dev
# → Open http://localhost:5173
```

## Services

| Service | Port | Purpose |
|---------|------|---------|
| `ollama` | 11434 | LLM (Qwen 32B) |
| `comfyui` | 8188 | Image/Video generation |
| `server` | 8000 | VidMuse FastAPI |
| `client` | 5173 | React UI (dev only) |

## Volumes

Data persists across container restarts:

- `ollama` — Docker volume for Ollama models
- `models` — ComfyUI models (FLUX, Wan, IP-Adapter)  
- `outputs` — Generated videos and temporary files

**Important:** Place your downloaded model files in `ComfyUI/models/diffusion_models/` before starting.

## Management

```bash
# View logs
docker compose logs -f ollama
docker compose logs -f comfyui  
docker compose logs -f server

# Stop services
docker compose down

# Stop and remove volumes (WARNING: deletes outputs)
docker compose down -v

# Restart a service
docker compose restart server

# Update images
docker compose pull
docker compose up -d
```

## NVIDIA GPU Setup

Ensure NVIDIA Container Toolkit is installed:

```bash
# Ubuntu/Debian
distribution=$(. /etc/os-release;echo $ID$VERSION_ID)
curl -s -L https://nvidia.github.io/nvidia-docker/gpgkey | sudo apt-key add -
curl -s -L https://nvidia.github.io/nvidia-docker/$distribution/nvidia-docker.list | sudo tee /etc/apt/sources.list.d/nvidia-docker.list
sudo apt update && sudo apt install -y nvidia-docker2
sudo systemctl restart docker
```

Verify: `docker run --rm --gpus all nvidia/cuda:12.2-base nvidia-smi`

## Troubleshooting

| Issue | Solution |
|-------|----------|
| Out of memory | Reduce batch size in ComfyUI, use fp8 models |
| Ollama slow to start | Wait ~30s after `docker compose up` for model load |
| Port already in use | Stop conflicting service or change ports in docker-compose.yml |
| Permission denied | Add user to `docker` group: `sudo usermod -aG docker $USER` |
| Video generation fails | Check VRAM: `docker stats` — need at least 20GB free |

## Production Considerations

- **Reverse proxy:** Add nginx container with SSL termination
- **Authentication:** Add basic auth or OAuth proxy  
- **Backups:** Mount external volume for `outputs/`
- **Monitoring:** Add Prometheus/Grafana for GPU metrics
- **Scaling:** Run multiple ComfyUI workers with load balancer

## Building Custom Images

The repo includes pre-built Dockerfiles. To rebuild:

```bash
# Build server image (if you modify code)
docker compose build server

# Force rebuild without cache
docker compose build --no-cache server
```

---

## Alternative: Standalone Docker Run

If you prefer not to use Compose, you can run each service manually:

```bash
# Ollama
docker run -d --gpus all -p 11434:11434 --name ollama ollama/ollama

# ComfyUI
docker run -d --gpus all -p 8188:8188 \
  -v $(pwd)/ComfyUI:/root/comfyui \
  -v models:/root/comfyui/models \
  comfyanonymous/comfyui

# VidMuse server
docker run -d --gpus all -p 8000:8000 \
  -v $(pwd)/server:/app \
  -v $(pwd)/outputs:/app/outputs \
  -e OLLAMA_URL=http://host.docker.internal:11434 \
  -e COMFYUI_URL=http://host.docker.internal:8188 \
  vidmuse-server:latest
```

(Requires building server image first: `docker build -t vidmuse-server ./server`)

---

**That's it!** One command to start the entire AI music video generation pipeline.
