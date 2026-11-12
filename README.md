# SceneMux — AI music video studio

Upload an MP3, add creative direction, review a storyboard, then render a beat-aligned MP4. SceneMux always owns **audio slicing** and **final stitch + master MP3 mux**; video engines (Comfy Cloud, local Playground, optional FAL) only produce per-scene picture.

Formerly branded **VidMuse** in older docs and env vars (`VIDMUSE_*` still work).

## Studio flow

1. Drop a song  
2. Direction + optional refs / look notes  
3. **Plan storyboard** (stills) → review prompts  
4. Preview scenes or **Render full video**  
5. Export (incl. optional 9:16)

**Simple** mode: presets only (Cloud · Playground · Hybrid Fast · Fast Draft · Custom).  
**Advanced**: director / stills / video host / video engine, Comfy URL, Home Agent, Hermes, engine health chips.

## Runtime modes

| Mode | LLM | Image / video | Best for |
|------|-----|---------------|----------|
| **cloud** | Ollama Cloud → OpenCode Go fallback | Comfy Cloud (H3 Ref2VA) | Chromebook / no local GPU |
| **local** | llama.cpp (preferred) → Ollama | Local / AI Playground Comfy | GPU workstation (NVIDIA / Intel Arc) |

Docker compose files are OS-agnostic. FFmpeg assembly always runs on the FastAPI host.

## Video engines

| Engine | Role |
|--------|------|
| **H3 Ref2VA** | True phoneme lip sync — quality PERF / finals |
| **FastH3** | 8-step I2V/T2V drafts — **not** Ref2VA |
| **LTX-2.3 IA2V** | Faster audio-conditioned drafts — **not** Ref2VA |
| **Hybrid Fast / Hybrid LTX** | PERF → H3; other scenes → FastH3 or LTX |

Full matrix, Arc / Playground notes, and weight names: [docs/ENGINES.md](docs/ENGINES.md).  
Pipeline lanes (Director / Stills / Video host): [docs/PIPELINE.md](docs/PIPELINE.md).  
Home Agent LAN: [docs/HOME_AGENT.md](docs/HOME_AGENT.md).

## Optional FAL (drafts only)

[FAL](https://fal.ai) is a third-party cloud for short draft stills/clips (prepaid / trial credits — not a daily free-gen entitlement). **Not required** for Cloud or Playground. FAL video host is draft-only; Hybrid Fast / H3 Ref2VA need Comfy or Home Agent.

1. Create a key at [fal.ai/dashboard/keys](https://fal.ai/dashboard/keys)  
2. Add `FAL_KEY=…` to `.env` (never commit it) and restart the API  
3. Studio **Advanced** → Video host / Stills = **FAL (draft)**

Details: [docs/ENGINES.md](docs/ENGINES.md#fal-drafts-optional).

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

## Architecture

- **LLM** — Cloud Ollama / OpenCode, or Home Agent / Hermes as director  
- **ComfyUI / Comfy Cloud** — Storyboard stills + H3 / FastH3 / LTX video  
- **Hermes Director** — Plan → stills → per-scene render → assemble  
- **FFmpeg** — Scene concat + master MP3 mux  
- **Optional FAL** — Prepaid/trial draft stills/clips only (not Hybrid Fast H3)  

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

## Local / Playground quick start (GPU)

```bash
docker compose up -d

# Place GGUF models in models/ and ComfyUI checkpoints under ComfyUI/models/
# For AI Playground: Comfy :8188; Home Agent Local web default :8765
cd client && npm install
VITE_SERVER_URL=http://localhost:8000 npm run dev
```

See [DOCKER_DEPLOYMENT.md](DOCKER_DEPLOYMENT.md) and [docs/HOME_AGENT.md](docs/HOME_AGENT.md).

### Standalone server

```bash
# from repo root (preferred)
./dev.sh
# or:
PYTHONPATH=. server/.venv/bin/python -m uvicorn server.main:app --host 0.0.0.0 --port 8000
```

After `cd client && npm run build`, the API also serves the studio UI from
`client/dist` on the same port (packaged / production-style). Dev still uses Vite.

## Environment reference

| Variable | Description |
|----------|-------------|
| `VIDMUSE_MODE` | `local` or `cloud` |
| `LLM_PROVIDER` | `llamacpp` \| `ollama` \| `opencode-go` \| `home-agent` \| `hermes` |
| `LLM_FALLBACK` | `opencode-go` or `none` |
| `OLLAMA_URL` / `OLLAMA_API_KEY` / `OLLAMA_MODEL` | Local or cloud Ollama |
| `OPENCODE_API_KEY` / `OPENCODE_MODEL` | OpenCode Go fallback |
| `COMFYUI_BACKEND` | `local` or `cloud` |
| `COMFYUI_URL` | Local / Playground Comfy |
| `COMFY_CLOUD_API_KEY` / `COMFY_CLOUD_URL` | Comfy Cloud |
| `COMFY_VIDEO_BACKEND` | Default engine: `minimax-h3` \| `fasth3` \| `ltx23` (studio can override via `video_model`) |
| `HOME_AGENT_URL` / `HOME_AGENT_PASSWORD` | Playground Home Agent local-web |
| `HERMES_URL` / `HERMES_TOKEN` | Optional Hermes director |
| `FAL_KEY` | Optional FAL draft host ([get a key](https://fal.ai/dashboard/keys)) |

## Requirements

**Local:** GPU + VRAM, llama.cpp GGUF and/or Ollama, ComfyUI models (H3 / FastH3 / LTX as needed), FFmpeg, Python 3.9+, Node LTS.

**Cloud:** CPU FastAPI host + FFmpeg, Ollama Cloud key, Comfy Cloud Standard+, optional OpenCode Go key. Optional `FAL_KEY` for drafts only.

## Installers / packaging

**No official Windows or Linux installer binaries are published yet.** Use the
setup wizards above and run from source until a tagged stable release.

| | |
|--|--|
| Status & policy | [packaging/README.md](packaging/README.md) |
| Windows NSIS (maintainer builds) | [WINDOWS_BUILD.md](WINDOWS_BUILD.md) · `build-windows.bat` → `scenemux-setup.exe` |
| Linux package | Planned for stable — [packaging/linux/README.md](packaging/linux/README.md) |
| Server-only PyInstaller | [packaging/server-binary.md](packaging/server-binary.md) |

Legacy `vidmuse-local-setup.exe` / “VidMuse Local” installer names are retired.

## License

MIT — see [LICENSE](LICENSE). Public repo: [github.com/ep150de/scenemux](https://github.com/ep150de/scenemux).
