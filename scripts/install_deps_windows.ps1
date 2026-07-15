# Quick dependency installer for Windows (winget)
$ErrorActionPreference = "Continue"

Write-Host "==> VidMuse Windows dependency helper"

function Ensure-Winget {
  if (-not (Get-Command winget -ErrorAction SilentlyContinue)) {
    Write-Host "winget not found. Install App Installer from the Microsoft Store, or install tools manually:"
    Write-Host "  Python  https://www.python.org/downloads/"
    Write-Host "  Node    https://nodejs.org/"
    Write-Host "  FFmpeg  https://ffmpeg.org/download.html"
    Write-Host "  Docker  https://docs.docker.com/desktop/setup/install/windows-install/"
    exit 1
  }
}

Ensure-Winget

Write-Host "Installing Python, Node.js LTS, and FFmpeg via winget..."
winget install --id Python.Python.3.12 -e --accept-source-agreements --accept-package-agreements
winget install --id OpenJS.NodeJS.LTS -e --accept-source-agreements --accept-package-agreements
winget install --id Gyan.FFmpeg -e --accept-source-agreements --accept-package-agreements

Write-Host "Optional Docker Desktop: winget install Docker.DockerDesktop"
Write-Host "==> Done. Open a NEW terminal, then run: setup.bat"
