"""Intel XPU and AI-Playground detection.

Detects:
1. Intel XPU hardware (via xpu-smi, device-service, or Vulkan)
2. Intel AI-Playground installation (scans all drives for 'AI Playground' folder)
3. ComfyUI XPU builds (in AI-Playground or standalone)

Known AI-Playground structure:
    D:/AI Playground/
    ├── AI Playground.exe
    ├── ComfyUI/                 # bundled ComfyUI
    ├── comfyui-deps/            # XPU-specific Python deps
    ├── device-service/          # xpu-smi.exe, xpum.dll
    ├── models/                  # AI models
    ├── LlamaCPP/                # llama.cpp binaries
    ├── modes/                   # AI mode configs
    └── backend-versions.json    # backend version info
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import platform
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

LOG = logging.getLogger("vidmuse.hardware_detect")


@dataclass
class GPUInfo:
    """GPU/XPU detection result."""
    has_intel_xpu: bool = False
    xpu_name: str = ""
    xpu_memory_gb: float = 0.0
    ai_playground_path: Optional[Path] = None
    comfyui_path: Optional[Path] = None
    comfyui_xpu_version: Optional[str] = None
    device_service_path: Optional[Path] = None
    xpu_smi_available: bool = False
    vulkan_xpu_driver: bool = False
    details: Dict[str, Any] = field(default_factory=dict)


# Known AI-Playground search paths (common install locations)
_AI_PLAYGROUND_DEFAULT_PATHS: List[Path] = [
    Path("C:/Program Files/AI Playground"),
    Path("C:/Users/Public/AI Playground"),
    Path("D:/AI Playground"),
]

# Drives to scan for AI-Playground
_WINDOWS_DRIVES = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"


def _scan_drives_for(path_fragment: str) -> List[Path]:
    """Scan all Windows drives for a path fragment (e.g. 'AI Playground')."""
    results: List[Path] = []
    for letter in _WINDOWS_DRIVES:
        candidate = Path(f"{letter}:/")
        if not candidate.exists():
            continue
        try:
            for item in candidate.iterdir():
                if item.is_dir() and path_fragment in item.name:
                    results.append(item)
        except (PermissionError, OSError):
            continue
    return results


def _try_xpu_smi() -> bool:
    """Check if xpu-smi is available on PATH."""
    return shutil.which("xpu-smi") is not None


def _try_xpu_smi_output() -> Optional[Dict[str, Any]]:
    """Run xpu-smi and parse output for XPU info."""
    try:
        result = asyncio.get_event_loop().run_in_executor(
            None, lambda: _sync_xpu_smi()
        )
        return asyncio.get_event_loop().run_until_complete(result)
    except Exception:
        return None


def _sync_xpu_smi() -> Optional[Dict[str, Any]]:
    """Sync version of xpu-smi check."""
    import subprocess
    try:
        result = subprocess.run(
            ["xpu-smi", "top"],
            capture_output=True, text=True, timeout=10
        )
        if result.returncode == 0 and result.stdout:
            return {"output": result.stdout, "returncode": result.returncode}
    except Exception:
        pass
    return None


def _try_device_service(ai_playground_path: Path) -> Optional[Path]:
    """Find device-service in AI-Playground install."""
    ds = ai_playground_path / "device-service"
    if ds.exists() and ds.is_dir():
        return ds
    # Check for xpu-smi.exe inside device-service
    for sub in ai_playground_path.iterdir():
        if sub.is_dir() and "device" in sub.name.lower():
            return sub
    return None


def _find_comfyui_in_ai_playground(ai_pg_path: Path) -> Optional[Path]:
    """Find ComfyUI installation within AI-Playground."""
    # Direct ComfyUI folder
    comfyui = ai_pg_path / "ComfyUI"
    if comfyui.exists() and comfyui.is_dir():
        # Check for main.py or server.py to confirm it's ComfyUI
        if (comfyui / "main.py").exists() or (comfyui / "server.py").exists():
            return comfyui
        # Also check for requirements.txt
        if (comfyui / "requirements.txt").exists():
            return comfyui

    # Check for comfyui in subdirs
    for sub in ai_pg_path.rglob("ComfyUI*"):
        if sub.is_dir() and "main.py" in [f.name for f in sub.iterdir() if f.is_file()]:
            return sub
    return None


def _try_vulkan_xpu() -> bool:
    """Check if Intel XPU Vulkan driver is available."""
    vulkan_libs = [
        "libvk_swiftshader.dll",
        "IntelGFX",
        "xeoxl64",
    ]
    # Check Vulkan loader
    try:
        import ctypes
        # Look for Intel Vulkan driver
        vk_paths = [
            r"C:\Windows\System32\vulkan-1.dll",
            r"C:\Windows\System32\igvk64.dll",
        ]
        for vk_path in vk_paths:
            if os.path.exists(vk_path):
                return True
    except Exception:
        pass
    return False


def detect_intel_xpu() -> GPUInfo:
    """Detect Intel XPU hardware and AI-Playground installation.

    Returns GPUInfo with all detection results.
    """
    info = GPUInfo()

    # 1. Check xpu-smi
    info.xpu_smi_available = _try_xpu_smi()

    # 2. Check device-service in known AI-Playground locations
    for default_path in _AI_PLAYGROUND_DEFAULT_PATHS:
        if default_path.exists():
            info.ai_playground_path = default_path
            info.device_service_path = _try_device_service(default_path)
            info.comfyui_path = _find_comfyui_in_ai_playground(default_path)
            break

    # 3. Scan all drives for AI-Playground
    if not info.ai_playground_path:
        found = _scan_drives_for("AI Playground")
        if found:
            # Prefer the one with device-service
            for candidate in found:
                ds = _try_device_service(candidate)
                if ds:
                    info.ai_playground_path = candidate
                    info.device_service_path = ds
                    info.comfyui_path = _find_comfyui_in_ai_playground(candidate)
                    break
            # Fallback to first found
            if not info.ai_playground_path and found:
                info.ai_playground_path = found[0]
                info.device_service_path = _try_device_service(found[0])
                info.comfyui_path = _find_comfyui_in_ai_playground(found[0])

    # 4. Check Vulkan XPU driver
    info.vulkan_xpu_driver = _try_vulkan_xpu()

    # 5. Check xpu-smi output for memory info
    if info.xpu_smi_available:
        xpu_data = _try_xpu_smi_output()
        if xpu_data and xpu_data.get("output"):
            output = xpu_data["output"]
            # Try to parse XPU memory from xpu-smi output
            for line in output.splitlines():
                line_lower = line.lower()
                if "memory" in line_lower or "mem" in line_lower:
                    # Extract GB value
                    try:
                        parts = line.split()
                        for i, p in enumerate(parts):
                            if "gb" in p.lower():
                                prev = parts[i-1] if i > 0 else ""
                                if prev.replace(".", "").isdigit():
                                    info.xpu_memory_gb = float(prev)
                                    break
                    except (ValueError, IndexError):
                        pass
                if "name" in line_lower or "device" in line_lower:
                    # Extract device name
                    parts = line.split()
                    if parts:
                        info.xpu_name = " ".join(parts[-2:]).strip()

    # 6. If no direct detection but Vulkan driver found, assume XPU possible
    if info.vulkan_xpu_driver and not info.has_intel_xpu:
        info.has_intel_xpu = True
        if not info.xpu_name:
            info.xpu_name = "Intel XPU (Vulkan driver detected)"
        if info.xpu_memory_gb == 0.0:
            info.xpu_memory_gb = 8.0  # estimate

    # 7. Check for ComfyUI XPU version
    if info.comfyui_path:
        requirements = info.comfyui_path / "requirements.txt"
        if requirements.exists():
            with open(requirements) as f:
                reqs = f.read().lower()
            if "torch-xpu" in reqs or "intel-gpu" in reqs:
                info.comfyui_xpu_version = "xpu"
            else:
                info.comfyui_xpu_version = "cuda"  # default assumption

    LOG.info("XPU detection: XPU=%s, AI-Playground=%s, ComfyUI=%s",
             info.has_intel_xpu,
             info.ai_playground_path,
             info.comfyui_path)

    return info


def find_ai_playground() -> Optional[Path]:
    """Find AI-Playground installation. Convenience wrapper."""
    info = detect_intel_xpu()
    return info.ai_playground_path


def recommended_comfy_url(info: Optional[GPUInfo] = None) -> str:
    """Return the Comfy queue URL to use for local / AI Playground installs."""
    explicit = (os.getenv("COMFYUI_URL") or "").strip()
    if explicit:
        return explicit.rstrip("/")
    info = info or detect_intel_xpu()
    # AI Playground's bundled ComfyUI typically listens on 8188 once started.
    if info.comfyui_path or info.ai_playground_path:
        return "http://localhost:8188"
    return "http://localhost:8188"


def find_comfyui_xpu_install() -> Optional[Path]:
    """Find ComfyUI with XPU support. Convenience wrapper."""
    info = detect_intel_xpu()
    return info.comfyui_path


def get_gpu_info() -> GPUInfo:
    """Get comprehensive GPU/XPU information. Convenience wrapper."""
    return detect_intel_xpu()


# ---------------------------------------------------------------------------
# ComfyUI XPU detection helpers
# ---------------------------------------------------------------------------

def is_comfyui_xpu_install(comfyui_path: Path) -> bool:
    """Check if a ComfyUI installation supports XPU (Intel GPU)."""
    if not comfyui_path or not comfyui_path.exists():
        return False

    # Check for torch-xpu or Intel-specific deps
    requirements = comfyui_path / "requirements.txt"
    if requirements.exists():
        with open(requirements) as f:
            content = f.read().lower()
        if "torch-xpu" in content or "intel-gpu" in content:
            return True

    # Check for Intel-specific custom_nodes
    custom_nodes = comfyui_path / "custom_nodes"
    if custom_nodes.exists():
        for node in custom_nodes.iterdir():
            if node.is_dir() and "intel" in node.name.lower():
                return True

    return False


def get_comfyui_port(comfyui_path: Path, default_port: int = 8188) -> int:
    """Detect ComfyUI port from configuration."""
    # Check for server.json or server_config.json
    config_files = [
        comfyui_path / "server.json",
        comfyui_path / "server_config.json",
        comfyui_path / ".comfyui_server_config",
    ]
    for config in config_files:
        if config.exists():
            try:
                with open(config) as f:
                    data = json.load(f)
                return int(data.get("port", default_port))
            except (json.JSONDecodeError, KeyError, ValueError):
                continue

    # Check main.py for port arg
    main_py = comfyui_path / "main.py"
    if main_py.exists():
        with open(main_py) as f:
            content = f.read()
        if "--port" in content:
            lines = content.splitlines()
            for i, line in enumerate(lines):
                if "--port" in line and i + 1 < len(lines):
                    try:
                        return int(lines[i + 1].strip().rstrip(",)"))
                    except (ValueError, IndexError):
                        pass

    return default_port
