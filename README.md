# VidMuse — AI Music Video Generator (v2)

Agentic music video creation for **Windows, Linux, and macOS**. Upload an MP3 + text direction and get a synced MP4.

## Runtime modes

| Mode | LLM | Image / video | Best for |
|------|-----|---------------|----------|
| **local** | llama.cpp (preferred) → Ollama | Local ComfyUI (z-image / FLUX / Wan) | GPU workstation (NVIDIA / Intel Arc) |
| **cloud** | Ollama Cloud → OpenCode Go fallback | Comfy Cloud | Chromebook / no local GPU |

Docker compose files are OS-agnostic. FFmpeg assembly always runs on the FastAPI host.

## First-time setup wizards (recommended)

| OS | Command |
|----|---------|
| **Linux / ChromeOS** | `./setup` |
| **macOS** | `./setup` |
| **Windows** | `setup.bat` |

```bash
# Linux / macOS / ChromeOS
chmod +x setup && ./setup

# Windows
setup.bat

# Any OS
python3 scripts/setup_wizard.py
```

The TUI wizard picks local vs cloud, collects API keys, checks dependencies, and writes `.env`. Re-run with `./setup --force` or `setup.bat --force`.

### Optional OS dependency helpers

| OS | Helper |
|----|--------|
| Linux | `bash scripts/install_deps_linux.sh` |
| macOS | `bash scripts/install_deps_macos.sh` |
| Windows | `powershell -ExecutionPolicy Bypass -File scripts\install_deps_windows.ps1` |

## Architecture (v2)

- **llama.cpp** — Preferred local LLM (GGUF). Ollama used as local/cloud alternative.
- **OpenCode Go** — Automatic LLM fallback when Ollama Cloud credits run out.
- **ComfyUI / Comfy Cloud** — Storyboard images + image-to-video.
- **Hermes Director** — Orchestrates audio → concepts → storyboard → video → final MP4.
- **FFmpeg** — Beat-synced assembly.

## Cloud quick start

1. Keys: [Ollama](https://ollama.com/settings/keys) · [OpenCode](https://opencode.ai) · [Comfy Cloud](https://platform.comfy.org/profile/api-keys) (API needs Standard+)
2. `./setup` (choose cloud) **or** `cp .env.example .env` and fill keys
3. Start server:

```bash
docker compose -f docker-compose.cloud.yml up -d --build
# or: cd server && pip install -r requirements.txt && VIDMUSE_MODE=cloud uvicorn main:app --host 0.0.0.0 --port 8000
```

4. Client:

```bash
cd client && npm install
VITE_SERVER_URL=http://localhost:8000 npm run dev
# Windows CMD: set VITE_SERVER_URL=http://localhost:8000 && npm run dev
```

## Local quick start (GPU)

```bash
docker compose up -d

# Place GGUF models in models/ and ComfyUI checkpoints under ComfyUI/models/
cd client && npm install
VITE_SERVER_URL=http://localhost:8000 npm run dev
```

See [DOCKER_DEPLOYMENT.md](DOCKER_DEPLOYMENT.md).

### Standalone server

```bash
cd server
pip install -r requirements.txt
uvicorn main:app --host 0.0.0.0 --port 8000
```

## Environment reference

| Variable | Description |
|----------|-------------|
| `VIDMUSE_MODE` | `local` or `cloud` |
| `LLM_PROVIDER` | `llamacpp` \| `ollama` \| `opencode-go` (optional override) |
| `LLM_FALLBACK` | `opencode-go` or `none` |
| `OLLAMA_URL` / `OLLAMA_API_KEY` / `OLLAMA_MODEL` | Local or cloud Ollama |
| `OPENCODE_API_KEY` / `OPENCODE_MODEL` | OpenCode Go fallback |
| `COMFYUI_BACKEND` | `local` or `cloud` |
| `COMFYUI_URL` | Local ComfyUI |
| `COMFY_CLOUD_API_KEY` / `COMFY_CLOUD_URL` | Comfy Cloud |

## Requirements

**Local:** GPU + VRAM, llama.cpp GGUF and/or Ollama, ComfyUI models, FFmpeg, Python 3.9+, Node LTS.

**Cloud:** CPU FastAPI host + FFmpeg, Ollama Cloud key, Comfy Cloud Standard+, optional OpenCode Go key.

## Windows packaged installer

```cmd
setup.bat                 # first-time config (keys / mode)
build-windows.bat         # build vidmuse-local-setup.exe
```

See [WINDOWS_BUILD.md](WINDOWS_BUILD.md).

## License

MIT — Private repository, not for distribution without permission.
