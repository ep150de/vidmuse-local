"""VidMuse Local — hardware detection for GPU/XPU backends."""

from .intel_xpu import (
    detect_intel_xpu,
    find_ai_playground,
    find_comfyui_xpu_install,
    get_gpu_info,
    GPUInfo,
)
from .comfyui_xpu_installer import install_comfyui_xpu

__all__ = [
    "detect_intel_xpu",
    "find_ai_playground",
    "find_comfyui_xpu_install",
    "get_gpu_info",
    "GPUInfo",
    "install_comfyui_xpu",
]
