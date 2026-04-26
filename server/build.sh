#!/bin/bash
# Build VidMuse Local Server as standalone executable using PyInstaller

set -e

echo "═══════════════════════════════════════════════════"
echo "  VidMuse Local Server — PyInstaller Build"
echo "═══════════════════════════════════════════════════"

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# 1. Check Python
echo -e "${GREEN}[1/7]${NC} Checking Python..."
python3 --version || { echo -e "${RED}Python 3 not found${NC}"; exit 1; }

# 2. Setup virtual environment
echo -e "${GREEN}[2/7]${NC} Setting up build environment..."
cd "$(dirname "$0")"
if [ ! -d "build-venv" ]; then
    python3 -m venv build-venv
fi
source build-venv/bin/activate

# 3. Install dependencies
echo -e "${GREEN}[3/7]${NC} Installing dependencies..."
pip install --upgrade pip setuptools wheel
pip install -r requirements.txt

# 4. Install PyInstaller
echo -e "${GREEN}[4/7]${NC} Installing PyInstaller..."
pip install pyinstaller

# 5. Create icon if not exists
if [ ! -f "icon.ico" ]; then
    echo -e "${YELLOW}[5/7]${NC} Creating placeholder icon..."
    # Create a simple 256x256 PNG and convert to ICO
    python3 -c "
from PIL import Image
img = Image.new('RGBA', (256, 256), (6, 182, 212, 255))
img.save('icon.png')
"
    # Convert PNG to ICO (if possible)
    python3 -c "
from PIL import Image
img = Image.open('icon.png')
img.save('icon.ico', format='ICO', sizes=[(16,16), (32,32), (48,48), (256,256)])
" 2>/dev/null || echo -e "${YELLOW}Skipping icon creation${NC}"
fi

# 6. Build executable
echo -e "${GREEN}[6/7]${NC} Building executable with PyInstaller..."
pyinstaller \
    --clean \
    --noconfirm \
    --onefile \
    --name vidmuse-server \
    --add-data "workflows:workflows" \
    --hidden-import fastapi \
    --hidden-import uvicorn \
    --hidden-import uvicorn.logging \
    --hidden-import uvicorn.loops.auto \
    --hidden-import uvicorn.protocols.http.auto \
    --hidden-import uvicorn.protocols.websockets.auto \
    --hidden-import uvicorn.lifespan.on \
    --hidden-import librosa \
    --hidden-import scipy \
    --hidden-import numpy \
    --hidden-import websockets \
    --hidden-import requests \
    --collect-all fastapi \
    --collect-all starlette \
    --collect-all pydantic \
    main.py

# 7. Verify build
if [ -f "dist/vidmuse-server" ]; then
    echo -e "${GREEN}[7/7]${NC} Build successful!"
    echo ""
    echo "Executable: $(pwd)/dist/vidmuse-server"
    ls -lh dist/vidmuse-server
    echo ""
    echo -e "${GREEN}To run:${NC}"
    echo "  ./dist/vidmuse-server"
    echo ""
    echo -e "${YELLOW}NOTE:${NC} This executable contains ONLY the Python backend."
    echo "  You still need these services running separately:"
    echo "    1. Ollama (ollama serve)"
    echo "    2. ComfyUI (python main.py)"
else
    echo -e "${RED}[7/7] Build failed!${NC}"
    exit 1
fi

echo ""
echo "═══════════════════════════════════════════════════"
echo "  Optional: Create systemd service"
echo "═══════════════════════════════════════════════════"
echo ""
echo "To auto-start on boot, create:"
echo "  /etc/systemd/system/vidmuse-server.service"
echo ""
echo "Content:"
cat << 'EOF'
[Unit]
Description=VidMuse Local Server
After=network.target ollama.service

[Service]
Type=simple
User=vidmuse
WorkingDirectory=/opt/vidmuse
ExecStart=/opt/vidmuse/vidmuse-server
Restart=always
RestartSec=10
Environment="OLLAMA_URL=http://localhost:11434"
Environment="COMFYUI_URL=http://localhost:8188"
Environment="OUTPUT_DIR=/opt/vidmuse/outputs"

[Install]
WantedBy=multi-user.target
EOF

echo ""
echo "Then run:"
echo "  sudo systemctl enable --now vidmuse-server"
