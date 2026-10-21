#!/usr/bin/env bash
# Quick dependency installer for Linux (apt / dnf / pacman)
set -euo pipefail

echo "==> SceneMux Linux dependency helper"

PKG=""
if command -v apt-get >/dev/null 2>&1; then
  PKG=apt
elif command -v dnf >/dev/null 2>&1; then
  PKG=dnf
elif command -v pacman >/dev/null 2>&1; then
  PKG=pacman
else
  echo "No supported package manager found (apt/dnf/pacman)."
  echo "Install python3, ffmpeg, nodejs, and npm manually."
  exit 1
fi

echo "Using package manager: $PKG"
echo "You may be prompted for sudo."

case "$PKG" in
  apt)
    sudo apt-get update
    sudo apt-get install -y python3 python3-venv python3-pip ffmpeg nodejs npm
    echo "Optional Docker: sudo apt-get install -y docker.io docker-compose-v2"
    ;;
  dnf)
    sudo dnf install -y python3 python3-pip ffmpeg nodejs npm
    echo "Optional Docker: sudo dnf install -y docker docker-compose"
    ;;
  pacman)
    sudo pacman -Sy --needed --noconfirm python python-pip ffmpeg nodejs npm
    echo "Optional Docker: sudo pacman -S docker docker-compose"
    ;;
esac

echo "==> Done. Re-run ./setup to continue configuration."
