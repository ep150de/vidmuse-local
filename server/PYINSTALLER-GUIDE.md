# PyInstaller Guide — VidMuse Local Server

## What PyInstaller Does

PyInstaller bundles your Python backend into a **single executable file** (~200-500MB) that includes:
- Python runtime
- All pip dependencies (fastapi, uvicorn, librosa, numpy, etc.)
- Your source code
- Static assets (workflow JSONs, etc.)

## What PyInstaller Does NOT Bundle

These must still be installed separately on the B70:

| Service | Why Not Bundled | How to Install |
|---------|----------------|----------------|
| **Ollama** | External binary + models | `curl -fsSL https://ollama.com/install.sh \| sh` |
| **ComfyUI** | External Python app + models | `git clone` + `pip install` |
| **FFmpeg** | System binary | `apt install ffmpeg` |
| **Intel GPU Drivers** | Kernel modules | `apt install intel-gpu-tools` |
| **Models** | Too large (FLUX 17GB, Wan 30GB) | Download separately to `models/` |

## Build Process

```bash
cd vidmuse-local/server

# 1. Install PyInstaller
pip install pyinstaller

# 2. Build
pyinstaller vidmuse-server.spec

# 3. Output
ls -lh dist/vidmuse-server
# → dist/vidmuse-server (~300MB)
```

## Deployment on B70

```bash
# Create service user
sudo useradd -r -s /bin/false vidmuse

# Copy executable
sudo mkdir -p /opt/vidmuse
sudo cp dist/vidmuse-server /opt/vidmuse/
sudo chmod +x /opt/vidmuse/vidmuse-server

# Create output directory
sudo mkdir -p /opt/vidmuse/outputs
sudo chown -R vidmuse:vidmuse /opt/vidmuse

# Install systemd services
sudo cp vidmuse-server.service ollama.service comfyui.service /etc/systemd/system/
sudo systemctl daemon-reload

# Enable auto-start
sudo systemctl enable ollama
sudo systemctl enable comfyui
sudo systemctl enable vidmuse-server

# Start everything
sudo systemctl start ollama
sudo systemctl start comfyui
sudo systemctl start vidmuse-server

# Check status
sudo systemctl status vidmuse-server
journalctl -u vidmuse-server -f
```

## Managing the Service

```bash
# Start/stop/restart
sudo systemctl start vidmuse-server
sudo systemctl stop vidmuse-server
sudo systemctl restart vidmuse-server

# View logs
sudo journalctl -u vidmuse-server -f

# Check if running
curl http://localhost:8000/api/health
```

## File Size Breakdown

| Component | Size | Notes |
|-----------|------|-------|
| Python runtime | ~30MB | Embedded in executable |
| FastAPI/Starlette | ~5MB | Web framework |
| Uvicorn | ~3MB | ASGI server |
| librosa + scipy | ~50MB | Audio analysis |
| numpy | ~25MB | Array operations |
| Other deps | ~20MB | requests, websockets, etc. |
| Your code | ~1MB | main.py + modules |
| **Total executable** | **~130-200MB** | Compressed with UPX |

## Advantages of PyInstaller

1. **No Python install needed** — executable is self-contained
2. **No venv management** — all deps embedded
3. **Easy deployment** — just scp one file
4. **Version control** — each build is a specific version
5. **User-friendly** — double-click to run (if desktop)

## Disadvantages

1. **Large file** — 200MB+ vs ~1MB of source
2. **Slow startup** — ~2-5 seconds to unpack
3. **No hot-reload** — must rebuild for changes
4. **Platform-specific** — Linux build only runs on Linux
5. **Doesn't bundle external services** — Ollama/ComfyUI still separate

## Alternative: Docker (Recommended for B70)

If PyInstaller feels too heavy, use Docker instead:

```dockerfile
FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt
COPY . .
EXPOSE 8000
CMD ["python", "main.py"]
```

```bash
# Build
docker build -t vidmuse-server .

# Run
docker run -d \
  --name vidmuse \
  --gpus all \
  -p 8000:8000 \
  -v /opt/vidmuse/outputs:/app/outputs \
  -e OLLAMA_URL=http://host.docker.internal:11434 \
  vidmuse-server
```

Docker is often easier for GPU servers because:
- Intel GPU drivers can be included in image
- Easier dependency management
- Better isolation
- Standard deployment pattern

## Recommendation

**For the B70 server:** Use the PyInstaller executable for the VidMuse backend, but manage Ollama and ComfyUI as separate systemd services. This gives you:
- One self-contained backend executable
- Clean separation of concerns
- Easy updates (just replace one file)
- Systemd auto-restart on crash
