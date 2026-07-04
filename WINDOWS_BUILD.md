# Building Windows Installer for VidMuse Local

## Prerequisites
- Python 3.11+
- PyInstaller
- NSIS (Nullsoft Scriptable Install System)
- CMake and build tools (for llama.cpp)
- Docker Desktop (optional)

## GGUF Model Setup

Download a GGUF model and place it in `server/models/`:
```cmd
mkdir server\models
REM Download qwen2.5-32B Q4_K_M
powershell -Command "Invoke-WebRequest -Uri 'https://huggingface.co/bartowski/qwen2.5-32B-Instruct-GGUF/resolve/main/qwen2.5-32b-instruct-Q4_K_M.gguf' -OutFile 'server\models\qwen2.5-32b-instruct-Q4_K_M.gguf'"
```

## Quick Build
```cmd
build-windows.bat
```
Output: `vidmuse-local-setup.exe`

## What Gets Installed
- Server EXE: `C:\Program Files\VidMuse Local\server\`
- ComfyUI integration
- GGUF model directory: `C:\Program Files\VidMuse Local\models\`
- Desktop shortcuts

## Distribution
Upload `.exe` to GitHub Releases.

## Notes for llama.cpp on Windows

The llama.cpp build on Windows requires:
1. Visual Studio Build Tools (CMake, MSVC)
2. CUDA toolkit (for GPU acceleration)
3. `llama-cpp-python` with CUDA backend

For GPU support on Windows:
```cmd
pip install llama-cpp-python --extra-index-url https://abetlen.github.io/llama-cpp-python/whl/cu124
```
(Adjust CUDA version as needed: cu124 for CUDA 12.4, cu123 for 12.3, etc.)

## Intel XPU Support on Windows

VidMuse Local supports Intel Arc GPUs with automatic AI-Playground detection.

### Detection Process

The server scans for Intel AI-Playground on all drives in this order:
1. Default paths: `C:/Program Files/AI Playground`, `D:/AI Playground`
2. Full drive scan for folders named "AI Playground"
3. Checks for `device-service/xpu-smi.exe` to confirm XPU support
4. Looks for bundled ComfyUI in AI-Playground installation
5. Checks for Intel Vulkan driver as fallback XPU indicator

### XPU ComfyUI Installation (Fallback)

When AI-Playground is NOT detected but Intel XPU hardware IS found:
```cmd
# The server will auto-install XPU ComfyUI
# This clones Intel's comfyui-deps and installs torch-xpu
# Requires git and python in PATH
XPU_AVAILABLE=1 server\server.exe
```

Installation sources (in priority order):
1. Intel AI-Playground repo's `comfyui-deps`
2. Standalone ComfyUI + `torch-xpu`
3. Intel's ComfyUI fork (if available)

### Manual XPU Setup

If auto-detection fails, set explicitly:
```cmd
set COMFYUI_URL=http://localhost:8188
set XPU_AVAILABLE=1
server\server.exe
```

### Intel Arc GPU Drivers

1. Download from Intel Driver & Support Assistant
2. Verify with: `xpu-smi top` (should show GPU info)
3. Install Intel oneAPI toolkit for full XPU support
4. Verify Vulkan driver: check for `igvk64.dll` in `C:\Windows\System32\`

### HuMo Lip-Sync Setup

For enhanced lip-sync quality:
```cmd
# Download HuMo model to ComfyUI models directory
mkdir server\comfyui_workflows\models
REM Download humod.safetensors from model hub
```

### LTX-2.3 I2V Setup

For enhanced video generation:
```cmd
# Download LTX-2.3 model to ComfyUI models directory
mkdir server\comfyui_workflows\models
REM Download ltx23_i2v.safetensors from model hub
```
