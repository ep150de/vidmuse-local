#!/bin/bash
# Build SceneMux API as a standalone executable (PyInstaller)
# This is NOT a full OS installer — see packaging/README.md

set -euo pipefail

echo "═══════════════════════════════════════════════════"
echo "  SceneMux server — PyInstaller build"
echo "═══════════════════════════════════════════════════"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

echo -e "${GREEN}[1/5]${NC} Checking Python..."
python3 --version || { echo -e "${RED}Python 3 not found${NC}"; exit 1; }

cd "$(dirname "$0")"
echo -e "${GREEN}[2/5]${NC} Setting up build environment..."
if [ ! -d "build-venv" ]; then
    python3 -m venv build-venv
fi
# shellcheck disable=SC1091
source build-venv/bin/activate

echo -e "${GREEN}[3/5]${NC} Installing dependencies..."
pip install --upgrade pip setuptools wheel
pip install -r requirements.txt
pip install pyinstaller

echo -e "${GREEN}[4/5]${NC} Building with scenemux-server.spec..."
pyinstaller scenemux-server.spec --clean --noconfirm

if [ -f "dist/scenemux-server" ] || [ -f "dist/scenemux-server.exe" ]; then
    echo -e "${GREEN}[5/5]${NC} Build successful"
    ls -lh dist/scenemux-server* 2>/dev/null || true
    echo ""
    echo -e "${YELLOW}NOTE:${NC} API binary only. Still need FFmpeg, Comfy/cloud keys, and models."
    echo "  Full packaged installers: packaging/README.md (Windows NSIS now; Linux planned)."
else
    echo -e "${RED}[5/5] Build failed — no dist/scenemux-server${NC}"
    exit 1
fi
