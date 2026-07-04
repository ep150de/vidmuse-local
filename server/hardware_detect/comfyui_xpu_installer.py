"""ComfyUI XPU installer for Intel GPU.

When AI-Playground is not detected but Intel XPU hardware is found,
attempt to install a ComfyUI build that supports Intel XPU.

Strategy:
1. Clone Intel's comfyui-deps from AI-Playground repo
2. Install torch-xpu for Intel GPU acceleration
3. Set up the necessary Intel-specific custom nodes
"""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
from pathlib import Path
from typing import Optional

LOG = logging.getLogger("vidmuse.hardware_detect")

# Intel's comfyui-deps repo (from AI-Playground)
_COMFYUI_DEPS_REPO = "https://github.com/intel/AI-Playground.git"
# Fallback: standalone ComfyUI with XPU support
_STANDALONE_COMFYUI_REPO = "https://github.com/comfyanonymous/ComfyUI.git"
# Intel GPU-specific ComfyUI fork (if available)
_XPU_COMFYUI_REPO = "https://github.com/intel/ComfyUI.git"


async def install_comfyui_xpu(
    target_dir: Optional[Path] = None,
    source: str = "intel",
) -> Optional[Path]:
    """Install ComfyUI with Intel XPU support.

    Args:
        target_dir: Where to install. Defaults to ~/.local/share/vidmuse/comfyui-xpu
        source: Where to get ComfyUI from:
            - "intel": Use Intel's comfyui-deps from AI-Playground
            - "standalone": Clone ComfyUI + install torch-xpu
            - "xpu-fork": Use Intel's ComfyUI fork (if available)

    Returns:
        Path to installed ComfyUI, or None on failure.
    """
    if target_dir is None:
        target_dir = Path.home() / ".local" / "share" / "vidmuse" / "comfyui-xpu"
        target_dir.mkdir(parents=True, exist_ok=True)

    LOG.info("Installing ComfyUI XPU to %s", target_dir)

    try:
        if source == "intel":
            return await _install_from_intel(target_dir)
        elif source == "standalone":
            return await _install_standalone_xpu(target_dir)
        elif source == "xpu-fork":
            return await _install_xpu_fork(target_dir)
        else:
            LOG.warning("Unknown source '%s', trying intel first then standalone", source)
            result = await _install_from_intel(target_dir)
            if not result:
                return await _install_standalone_xpu(target_dir)
            return result
    except Exception as e:
        LOG.error("ComfyUI XPU install failed: %s", e)
        return None


async def _install_from_intel(target_dir: Path) -> Optional[Path]:
    """Install from Intel AI-Playground repo's comfyui-deps."""
    import subprocess

    # Clone the full AI-Playground repo to get comfyui-deps
    install_base = target_dir.parent
    repo_dir = install_base / "AI-Playground"

    LOG.info("Cloning AI-Playground repo for comfyui-deps...")
    clone_cmd = [
        "git", "clone", "--depth", "1",
        _COMFYUI_DEPS_REPO,
        str(repo_dir),
    ]

    try:
        proc = await asyncio.create_subprocess_exec(
            *clone_cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()
        if proc.returncode != 0:
            LOG.error("Git clone failed: %s", stderr.decode())
            return None
    except FileNotFoundError:
        LOG.error("git not found in PATH")
        return None

    # Copy comfyui-deps to target
    comfyui_deps = repo_dir / "comfyui-deps"
    if comfyui_deps.exists():
        LOG.info("Copying comfyui-deps to %s", target_dir)
        if target_dir.exists():
            shutil.rmtree(target_dir)
        shutil.copytree(comfyui_deps, target_dir)
        LOG.info("ComfyUI XPU installed from Intel repo")
        return target_dir

    LOG.warning("comfyui-deps not found in cloned repo")
    return None


async def _install_standalone_xpu(target_dir: Path) -> Optional[Path]:
    """Clone ComfyUI and install torch-xpu."""
    import subprocess

    LOG.info("Cloning ComfyUI standalone...")
    clone_cmd = [
        "git", "clone", "--depth", "1",
        _STANDALONE_COMFYUI_REPO,
        str(target_dir),
    ]

    try:
        proc = await asyncio.create_subprocess_exec(
            *clone_cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()
        if proc.returncode != 0:
            LOG.error("Git clone failed: %s", stderr.decode())
            return None
    except FileNotFoundError:
        LOG.error("git not found in PATH")
        return None

    # Install torch-xpu (Intel GPU PyTorch)
    LOG.info("Installing torch-xpu for Intel GPU support...")
    pip_cmd = [
        shutil.which("python") or shutil.which("python3"),
        "-m", "pip", "install",
        "torch", "--index-url", "https://download.pytorch.org/whl/xpu",
    ]

    try:
        proc = await asyncio.create_subprocess_exec(
            *pip_cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()
        if proc.returncode != 0:
            LOG.warning("torch-xpu install may have issues: %s", stderr.decode()[:200])
        else:
            LOG.info("torch-xpu installed successfully")
    except FileNotFoundError:
        LOG.error("python/pip not found")
        return None

    LOG.info("ComfyUI XPU installed (standalone + torch-xpu)")
    return target_dir


async def _install_xpu_fork(target_dir: Path) -> Optional[Path]:
    """Clone Intel's ComfyUI fork."""
    import subprocess

    LOG.info("Cloning Intel ComfyUI fork...")
    clone_cmd = [
        "git", "clone", "--depth", "1",
        _XPU_COMFYUI_REPO,
        str(target_dir),
    ]

    try:
        proc = await asyncio.create_subprocess_exec(
            *clone_cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()
        if proc.returncode != 0:
            LOG.warning("Intel ComfyUI fork clone failed, falling back to standalone")
            return None
    except FileNotFoundError:
        LOG.error("git not found in PATH")
        return None

    LOG.info("ComfyUI XPU installed (Intel fork)")
    return target_dir


# ---------------------------------------------------------------------------
# Installation status helpers
# ---------------------------------------------------------------------------

def get_comfyui_installation_status() -> dict:
    """Check all known ComfyUI installations and return status."""
    from .intel_xpu import detect_intel_xpu, is_comfyui_xpu_install

    info = detect_intel_xpu()
    results = {
        "has_intel_xpu": info.has_intel_xpu,
        "ai_playground_path": str(info.ai_playground_path) if info.ai_playground_path else None,
        "comfyui_found": str(info.comfyui_path) if info.comfyui_path else None,
        "comfyui_is_xpu": False,
        "comfyui_port": None,
        "xpu_available": info.xpu_smi_available,
        "vulkan_xpu": info.vulkan_xpu_driver,
        "needs_install": info.has_intel_xpu and not info.comfyui_path,
    }

    if info.comfyui_path:
        results["comfyui_is_xpu"] = is_comfyui_xpu_install(info.comfyui_path)
        from .intel_xpu import get_comfyui_port
        results["comfyui_port"] = get_comfyui_port(info.comfyui_path)

    return results
