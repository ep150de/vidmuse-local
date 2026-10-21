# Windows packaging (SceneMux)

> **Status:** packaging scripts are maintained for a future stable release.
> There is **no official `scenemux-setup.exe` on GitHub Releases yet**.
> For daily use, run from source: `setup.bat` → start API + client.
>
> Overview: [packaging/README.md](packaging/README.md)

## First-time config (any Windows PC)

```cmd
setup.bat
```

Optional deps (Python / Node / FFmpeg via winget):

```powershell
powershell -ExecutionPolicy Bypass -File scripts\install_deps_windows.ps1
```

## Maintainer: build the NSIS installer

Prerequisites:

- Python 3.11+
- Node.js LTS + npm
- [PyInstaller](https://pyinstaller.org/) (`pip install pyinstaller`)
- [NSIS](https://nsis.sourceforge.io/) (`makensis` on `PATH`)

```cmd
build-windows.bat
```

Produces `scenemux-setup.exe` in the repo root (version `0.1.0-dev` until bumped
in `packaging/windows/scenemux-installer.nsi`).

What it packages:

- `server/dist/scenemux-server.exe` (API only)
- Built studio UI from `client/dist`
- `docker-compose.yml` / `docker-compose.cloud.yml` / `.env.example`
- Short model pointer file (H3 / FastH3 / LTX — full detail in `docs/ENGINES.md`)

Still required on the machine after install: FFmpeg, ComfyUI or Comfy Cloud /
Playground (and models or cloud keys). Optional `FAL_KEY` for drafts.

## Intel Arc / AI Playground

SceneMux can target Playground Comfy (`COMFYUI_URL`) and Home Agent. Detection
and XPU notes live in `server/hardware_detect/` and [docs/HOME_AGENT.md](docs/HOME_AGENT.md).

## Distribution policy

Upload installers only as **tagged stable** GitHub Release assets, with matching
docs. Do not treat local `scenemux-setup.exe` or legacy `vidmuse-local-setup.exe`
builds as official downloads.
