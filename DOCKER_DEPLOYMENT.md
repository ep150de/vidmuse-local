# Docker Deployment — SceneMux

Formerly SceneMux. Prefer the root [README](README.md) for current studio / engine setup.

## Prerequisites

- Docker Engine 24+ with NVIDIA Container Toolkit (local GPU mode)
- NVIDIA GPU with drivers installed (local mode)
- At least 32GB free disk space for models (local mode)
- GGUF model for llama.cpp (recommended for local mode)

For Chromebook / no-GPU **cloud** mode (Ollama Cloud + Comfy Cloud):

```bash
./setup   # or setup.bat on Windows
docker compose -f docker-compose.cloud.yml up -d --build
```

See the README cloud quick start for API keys.

## Quick Start

1. **Clone and enter the repo**
```bash
git clone <your-repo>
cd vidmuse-local
```

2. **Download ComfyUI models** (one-time, ~50GB total)
```bash
mkdir -p ComfyUI/models/diffusion_models
# Place these in ComfyUI/models/diffusion_models/:
# - flux1-dev-fp8.safetensors (~17GB)
# - wan2.1_i2v_480p_14B_fp8.safetensors (~30GB)
# - zimage_turbo.safetensors (~17GB)
# - ip-adapter_sd15.safetensors (~300MB)
```

3. **Download GGUF model for llama.cpp** (recommended over Ollama)
```bash
mkdir -p models
# Example: download qwen2.5-32B Q4_K_M GGUF
wget https://huggingface.co/bartowski/qwen2.5-32B-Instruct-GGUF/resolve/main/qwen2.5-32b-instruct-Q4_K_M.gguf
mv qwen2.5-32b-instruct-Q4_K_M.gguf models/
```

4. **Start all services**
```bash
docker compose up -d
```

This starts:
- ComfyUI on http://localhost:8188
- SceneMux server on http://localhost:8000 (with llama.cpp)

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
| server | 8000 | SceneMux FastAPI + llama.cpp |
| comfyui | 8188 | Image/Video generation |
| client | 5173 | React UI (dev only) |

## Volumes

Data persists across container restarts:

- `models` — GGUF models for llama.cpp (mounted from `models/`)
- `./ComfyUI` — ComfyUI models (FLUX, Wan, z-image turbo, IP-Adapter)
- `outputs` — Generated videos and temporary files

**Important:** Place downloaded model files in their respective directories before starting.

## Management

```bash
# View logs
docker compose logs -f server
docker compose logs -f comfyui

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

## llama.cpp Configuration

llama.cpp is built into the server Docker image from source. To configure:

### Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `GGUF_MODEL_PATH` | `/app/models/` | Directory containing GGUF files |
| `N_GPU_LAYERS` | `-1` | Layers to offload to GPU (-1 = all) |
| `N_CTX` | `4096` | Context window size |
| `N_BATCH` | `512` | Batch size |
| `FLASH_ATTN` | `1` | Enable flash attention |

### Selecting a Model

The first `.gguf` file in `models/` is auto-loaded. Override with:
```bash
GGUF_MODEL_PATH=/app/models/my-model.gguf docker compose up -d
```

### Model Sources

Recommended GGUF models (quantized for VRAM efficiency):
- `qwen2.5-32b-instruct-Q4_K_M.gguf` — balanced quality/speed
- `qwen2.5-14b-instruct-Q5_K_M.gguf` — faster, good quality
- `llama-3.1-8b-instruct-Q5_K_S.gguf` — fastest, lower quality

Download from: https://huggingface.co/bartowski or https://huggingface.co/TheBloke

## ComfyUI z-image Turbo

For fast reference image generation, ensure `zimage_turbo.safetensors` is in:
```
ComfyUI/models/diffusion_models/zimage_turbo.safetensors
```

This model generates images in 4-6 steps (vs. 30+ for standard FLUX).

## Intel XPU ComfyUI Support

SceneMux supports Intel XPU (Arc GPU) via the Intel AI-Playground ComfyUI build.

### Prerequisites

1. Intel Arc GPU with latest drivers
2. Intel XPU drivers installed (`xpu-smi` or `xpum` available on PATH)
3. Optional: Intel AI-Playground installation on any drive

### Detection

The server automatically detects:
1. **COMFYUI_URL** environment variable (explicit override)
2. **AI-Playground bundled ComfyUI** with XPU support (scans all drives)
3. **Default localhost:8188** (CUDA ComfyUI)

### Setup with AI-Playground

If Intel AI-Playground is installed (e.g., `D:/AI Playground/`):
```bash
# Set environment variable to point to AI-Playground ComfyUI
COMFYUI_URL=http://localhost:8188 XPU_AVAILABLE=true docker compose up -d
```

The server will:
- Detect the ComfyUI installation in AI-Playground
- Use torch-xpu for Intel GPU acceleration
- Load Intel-specific custom nodes

### Fallback: Auto-Install XPU ComfyUI

When AI-Playground is not detected but Intel XPU hardware is found:
```bash
# The server will attempt to install ComfyUI with XPU support
# This clones Intel's comfyui-deps and installs torch-xpu
# Requires git and python in PATH
XPU_AVAILABLE=true docker compose up -d
```

### Intel XPU ComfyUI Features

- **HuMo Lip-Sync**: High-quality mouth movement synchronization
- **LTX-2.3 I2V**: Image-to-video generation with motion control
- **Intel GPU acceleration**: Uses torch-xpu for fast inference
- **Auto-detection**: Scans all Windows drives for AI-Playground

### ComfyUI Workflow Integration

Two new workflows are included for enhanced music video generation:

1. **HuMo Lip-Sync** (`comfyui_workflows/humolipsync_workflow.json`)
   - Synchronizes lip movement to audio
   - Requires: HuMo checkpoint, audio input, video input
   - Custom nodes: ComfyUI-HuMo, ComfyUI-AudioSync, ComfyUI-Frame-Interpolation

2. **LTX-2.3 I2V** (`comfyui_workflows/ltx23_i2v_workflow.json`)
   - Image-to-video with motion control
   - Requires: LTX-2.3 checkpoint, reference image
   - Custom nodes: ComfyUI-LTX, ComfyUI-Vid2Vid, ComfyUI-Motion-Module

### XPU vs CUDA Backend

The server auto-selects the backend:
- **XPU**: When Intel XPU hardware detected + XPU ComfyUI available
- **CUDA**: When NVIDIA GPU detected + CUDA ComfyUI available
- **Default**: Falls back to localhost:8188 if neither detected

Set `COMFYUI_URL` explicitly to override detection.

## Troubleshooting

| Issue | Solution |
|-------|----------|
| Out of memory | Use GGUF Q4/Q5 quantized models; reduce ComfyUI batch size |
| Server can't find GGUF | Check `models/` directory is mounted: `docker compose logs server` |
| ComfyUI slow to start | Wait ~30s after `docker compose up` for model load |
| Port already in use | Stop conflicting service or change ports in docker-compose.yml |
| Permission denied | Add user to `docker` group: `sudo usermod -aG docker $USER` |
| Video generation fails | Check VRAM: `docker stats` — need at least 20GB free |
| llama.cpp not loading | Ensure GGUF file is in `models/` and has correct permissions |

## Production Considerations

- **Reverse proxy:** Add nginx container with SSL termination
- **Authentication:** Add basic auth or OAuth proxy
- **Backups:** Mount external volume for `outputs/`
- **Monitoring:** Add Prometheus/Grafana for GPU metrics
- **Scaling:** Run multiple ComfyUI workers with load balancer

## Building Custom Images

```bash
# Build server image (if you modify code)
docker compose build server

# Force rebuild without cache
docker compose build --no-cache server
```

## Alternative: Standalone Docker Run

```bash
# ComfyUI
docker run -d --gpus all -p 8188:8188 \
  -v $(pwd)/ComfyUI:/root/comfyui \
  -v models:/root/comfyui/models \
  -v outputs:/root/comfyui/outputs \
  comfyanonymous/comfyui

# SceneMux server
docker run -d --gpus all -p 8000:8000 \
  -v $(pwd)/server:/app \
  -v $(pwd)/outputs:/app/outputs \
  -v $(pwd)/models:/app/models \
  -e COMFYUI_URL=http://host.docker.internal:8188 \
  vidmuse-server:latest

# Client
cd client
VITE_SERVER_URL=http://localhost:8000 npm run dev
```

---

**That's it!** One command to start the entire AI music video generation pipeline.