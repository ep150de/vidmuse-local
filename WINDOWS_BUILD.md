# Building Windows Installer for VidMuse Local

## Prerequisites
- Python 3.11+, PyInstaller, NSIS
- Docker Desktop (optional)

## Quick Build
```cmd
build-windows.bat
```
Output: `vidmuse-local-setup.exe`

## What Gets Installed
- Server EXE: `C:\Program Files\VidMuse Local\server\`
- Client: `C:\Program Files\VidMuse Local\client\`
- Docker config: `docker-compose.yml`
- Desktop shortcuts

## Distribution
Upload `.exe` to GitHub Releases.
