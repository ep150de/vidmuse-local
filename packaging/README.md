# Packaging (SceneMux)

**Status (pre-stable):** there are **no published installer binaries** on
[GitHub Releases](https://github.com/ep150de/scenemux/releases) yet. Day-to-day
use is **run from source** (`./setup` / `setup.bat`, then start the API + client).

This folder holds the **maintained packaging sources** so a future stable cut
can ship Windows (NSIS) and Linux installers without reviving stale VidMuse
scripts.

| Platform | Today | Target for stable release |
|----------|--------|---------------------------|
| **Any OS** | Clone + setup wizard + deps helpers | Same (always supported) |
| **Windows** | `build-windows.bat` builds `scenemux-setup.exe` (dev/maintainer use) | Signed GitHub Release `.exe` |
| **Linux** | Source + `scripts/install_deps_linux.sh` | Packaged binary / archive (see [linux/](linux/)) |

## Do not ship stale artifacts

- Old names (`vidmuse-local-setup.exe`, “VidMuse Local”) are retired.
- Do not upload random local `.exe` builds as “the” SceneMux installer until a
  tagged **stable** release and release notes say so.
- Prefer documenting engines/models from [docs/ENGINES.md](../docs/ENGINES.md)
  (H3 / FastH3 / LTX) — not legacy FLUX/Wan installer copy.

## Quick links

- Windows NSIS + build steps → [WINDOWS_BUILD.md](../WINDOWS_BUILD.md)
- Windows script entrypoint → [`../build-windows.bat`](../build-windows.bat)
- Linux packaging plan → [linux/README.md](linux/README.md)
- Optional server-only PyInstaller binary → [server-binary.md](server-binary.md)

## Maintainer checklist (before a public installer release)

1. Tag a stable version; bump `!define VERSION` in the NSIS script.
2. Build client (`cd client && npm ci && npm run build`).
3. Build server binary + NSIS (`build-windows.bat`) or Linux package (TBD).
4. Smoke-test install on a clean machine (cloud mode at minimum).
5. Attach artifacts to the GitHub Release; update README download links.

When `client/dist` exists, the FastAPI app also serves the studio UI from `/`
(same port as the API) for packaged / production-style runs.
