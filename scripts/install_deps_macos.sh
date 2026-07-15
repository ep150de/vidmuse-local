#!/usr/bin/env bash
# Quick dependency installer for macOS (Homebrew)
set -euo pipefail

echo "==> VidMuse macOS dependency helper"

if ! command -v brew >/dev/null 2>&1; then
  echo "Homebrew not found."
  echo "Install from https://brew.sh then re-run this script."
  exit 1
fi

brew update
brew install python ffmpeg node

echo "Optional Docker Desktop: https://docs.docker.com/desktop/setup/install/mac-install/"
echo "==> Done. Re-run ./setup to continue configuration."
