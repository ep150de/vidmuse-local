# Optional server-only binary (PyInstaller)

This builds **only** the FastAPI backend into a single executable. It is **not**
a full SceneMux installer (no studio UI packaging, no ComfyUI, no models).

Use for lab machines / systemd hosts. End users should prefer **run from source**
until a stable packaged release (see [README.md](README.md)).

## Build

```bash
cd server
./build.sh
# → server/dist/scenemux-server
```

Windows (same tree as the NSIS installer):

```cmd
cd server
pyinstaller scenemux-server.spec --clean
```

## Not bundled (install separately)

| Dependency | Notes |
|------------|--------|
| FFmpeg | Required for slice + final mux |
| ComfyUI / Playground / Comfy Cloud | Stills + video engines |
| Ollama / llama.cpp / cloud LLM keys | Director |
| Models | H3 / FastH3 / LTX — see [docs/ENGINES.md](../docs/ENGINES.md) |
| Optional `FAL_KEY` | Draft host only |

## Legacy

Older docs referred to `vidmuse-server`. The binary name is now `scenemux-server`.
The stub at `server/PYINSTALLER-GUIDE.md` only points here.
