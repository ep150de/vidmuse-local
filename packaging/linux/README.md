# Linux packaged installer (planned)

**Not available yet.** Until a stable SceneMux release, install from source:

```bash
git clone https://github.com/ep150de/scenemux.git
cd scenemux
bash scripts/install_deps_linux.sh   # optional
chmod +x setup && ./setup
```

Then start the API and Vite client as described in the root [README](../../README.md).

## Intended stable deliverable

When packaging lands, expect something like:

- A single downloadable archive or `.deb` / AppImage with:
  - SceneMux API binary (or venv) + static studio UI
  - `.env.example` and first-run wizard hook
- Still **external**: FFmpeg, ComfyUI / Playground / cloud keys, GPU drivers, models

Tracking notes for maintainers:

- Reuse `server/build.sh` / PyInstaller patterns from [../server-binary.md](../server-binary.md)
- Do not revive FLUX/Wan-era install copy; point model docs at `docs/ENGINES.md`
- Publish only via a tagged GitHub Release alongside the Windows installer
