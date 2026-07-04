# Intel XPU and AI-Playground Integration

This module provides Intel XPU hardware detection and Intel AI-Playground ComfyUI integration for VidMuse Local.

## Architecture

```
server/hardware_detect/
├── __init__.py          # Public API
├── intel_xpu.py         # XPU detection + AI-Playground discovery
├── comfyui_xpu_installer.py  # Auto-install fallback
└── instructions.md      # This file
```

## XPU Detection

### How it works

1. **xpu-smi check** — looks for `xpu-smi` on PATH
2. **device-service** — scans AI-Playground for `device-service/xpu-smi.exe`
3. **Vulkan XPU driver** — checks for Intel Vulkan drivers on disk
4. **GPU memory** — extracts VRAM info from xpu-smi output

### AI-Playground detection

Scans in priority order:
1. Default paths (`C:/Program Files/AI Playground`, `D:/AI Playground`)
2. All Windows drives for folders named "AI Playground"
3. Verifies installation via `device-service/` presence
4. Locates bundled ComfyUI (`ComfyUI/` directory)

### Usage

```python
from server.hardware_detect import detect_intel_xpu, GPUInfo

info = detect_intel_xpu()
# Returns GPUInfo with:
#   - has_intel_xpu: bool
#   - xpu_name: str
#   - xpu_memory_gb: float
#   - ai_playground_path: Optional[Path]
#   - comfyui_path: Optional[Path]
#   - device_service_path: Optional[Path]
#   - xpu_smi_available: bool
#   - vulkan_xpu_driver: bool
#   - details: Dict[str, Any]
```

### ComfyUI XPU detection

```python
from server.hardware_detect.intel_xpu import (
    is_comfyui_xpu_install,
    get_comfyui_port,
    detect_intel_xpu,
)

info = detect_intel_xpu()
if info.comfyui_path:
    is_xpu = is_comfyui_xpu_install(info.comfyui_path)
    port = get_comfyui_port(info.comfyui_path)  # default 8188
```

## Auto-Install Fallback

When AI-Playground is not detected but Intel XPU hardware is found:

```python
from server.hardware_detect.comfyui_xpu_installer import install_comfyui_xpu

# Auto-install with priority: intel > standalone > xpu-fork
result = await install_comfyui_xpu()

# Or specify source:
result = await install_comfyui_xpu(source="standalone")
```

Installation sources:
- `"intel"` — clone AI-Playground repo, extract comfyui-deps (default)
- `"standalone"` — clone ComfyUI + install torch-xpu
- `"xpu-fork"` — clone Intel's ComfyUI fork (if available)

## ComfyUI Backend Selection

`image_generator.py` auto-selects the backend:

1. `COMFYUI_URL` env var (explicit override)
2. AI-Playground bundled ComfyUI with XPU support
3. Any detected ComfyUI
4. Default `localhost:8188`

```python
client = ComfyUIClient()
backend = client.backend_type  # "explicit" | "xpu" | "standard" | "default"
```

## New Workflows

### HuMo Lip-Sync

Enhanced lip-sync for music videos using the HuMo model.

**Workflow file:** `comfyui_workflows/humolipsync_workflow.json`

**Usage in code:**
```python
result = await client.generate_humo_lipsync(
    image_path="/path/to/face_video.mp4",
    audio_path="/path/to/audio.mp3",
    prompt="realistic talking face, natural lip-sync",
    width=512,
    height=512,
    sync_strength=0.8,  # 0.0 to 1.0
)
```

**Requirements:**
- `humod.safetensors` checkpoint
- `HuMoLipSyncProcessor` custom node
- `FrameInterpolation` custom node
- Audio track for lip-sync reference

### LTX-2.3 I2V

Enhanced video generation from reference images using LTX-2.3.

**Workflow file:** `comfyui_workflows/ltx23_i2v_workflow.json`

**Usage in code:**
```python
result = await client.generate_ltx23_i2v(
    image_path="/path/to/reference.jpg",
    prompt="smooth camera movement, cinematic lighting",
    width=768,
    height=512,
    num_frames=81,  # ~3.3s at 24fps
    motion_strength=0.7,  # 0.0 to 1.0
    camera_motion="horizontal_pan",  # pan_left, pan_right, zoom_in, zoom_out, tilt_up, tilt_down
)
```

**Requirements:**
- `ltx23_i2v.safetensors` checkpoint
- `LTXMotionModule` custom node
- Reference image for video generation

### Workflow JSON Files

Both workflows are stored as JSON in `comfyui_workflows/` and can be:
- Loaded directly by ComfyUI
- Used as templates for custom pipeline generation
- Modified for specific use cases

## Docker/XPU Configuration

### Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `COMFYUI_URL` | `http://localhost:8188` | Override ComfyUI URL |
| `XPU_AVAILABLE` | `false` | Enable XPU support |
| `OUTPUT_DIR` | `/app/outputs` | Output directory |

### docker-compose.yml XPU setup

```yaml
server:
  environment:
    - XPU_AVAILABLE=true  # Enable XPU support
    - COMFYUI_URL=http://localhost:8188  # Override if needed
  volumes:
    - ./comfyui-xpu:/app/comfyui-xpu  # XPU ComfyUI directory
    - ./comfyui_workflows:/app/comfyui_workflows  # Workflow files
```

## Troubleshooting

### XPU not detected

1. Verify Intel drivers: `xpu-smi top`
2. Check Vulkan driver: `ls /usr/lib/x86_64-linux-gnu/libvulkan_*`
3. Verify AI-Playground: `ls D:/AI\ Playground/device-service/xpu-smi.exe`

### ComfyUI XPU not working

1. Verify torch-xpu: `python -c "import torch; print(torch.xpu.is_available())"`
2. Check ComfyUI logs for XPU errors
3. Verify custom nodes are installed: `ls ComfyUI/custom_nodes/`

### Audio-video sync issues

1. Check audio format: MP3/WAV 44.1kHz recommended
2. Adjust `sync_strength`: 0.5-0.8 for subtle, 0.8-1.0 for strong sync
3. Try `generate_humo_lipsync()` with higher `steps` (40+)

### LTX-2.3 errors

1. Verify model file: `ls ComfyUI/models/diffusion_models/ltx23_i2v.safetensors`
2. Check VRAM: LTX-2.3 requires ~16GB+
3. Try lower `num_frames` or `motion_strength`