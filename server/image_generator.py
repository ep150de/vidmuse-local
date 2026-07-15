"""ComfyUI integration for VidMuse Local.

Supports:
  - z-image turbo text-to-image (generating image references)
  - Wan 2.1 image-to-video
  - FLUX Dev (fallback)
  - HuMo lip-sync workflow
  - LTX-2.3 image-to-video workflow
  - Intel XPU ComfyUI (auto-detect + auto-install)
"""

from __future__ import annotations

import asyncio
import json
import os
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

import aiohttp

COMFYUI_URL = os.getenv("COMFYUI_URL", "http://localhost:8188").rstrip("/")
COMFY_CLOUD_URL = os.getenv("COMFY_CLOUD_URL", "https://cloud.comfy.org").rstrip("/")
COMFY_CLOUD_API_KEY = os.getenv("COMFY_CLOUD_API_KEY")
COMFYUI_BACKEND = (os.getenv("COMFYUI_BACKEND") or "").strip().lower()
VIDMUSE_MODE = (os.getenv("VIDMUSE_MODE") or "local").strip().lower()
OUTPUT_DIR = Path(os.getenv("OUTPUT_DIR", "/tmp/vidmuse/outputs"))

# ComfyUI workflow definitions directory
WORKFLOWS_DIR = Path(__file__).parent / "comfyui_workflows"


def _resolve_comfy_config():
    """Return (base_url, api_key, backend_label)."""
    backend = COMFYUI_BACKEND or ("cloud" if VIDMUSE_MODE == "cloud" else "local")
    if backend == "cloud":
        return COMFY_CLOUD_URL, COMFY_CLOUD_API_KEY, "cloud"
    return COMFYUI_URL, None, "local"


class ComfyUIClient:
    """Async client for ComfyUI's HTTP + WebSocket API (local or Comfy Cloud)."""

    def __init__(self):
        self.url, self.api_key, self.backend = _resolve_comfy_config()
        self.client_id = str(uuid.uuid4())

    def _headers(self) -> Dict[str, str]:
        headers: Dict[str, str] = {}
        if self.api_key:
            headers["X-API-Key"] = self.api_key
        return headers

    def _prompt_payload(self, workflow: Dict[str, Any]) -> Dict[str, Any]:
        payload: Dict[str, Any] = {
            "prompt": workflow,
            "client_id": self.client_id,
        }
        if self.backend == "cloud" and self.api_key:
            payload["extra_data"] = {"api_key_comfy_org": self.api_key}
        return payload

    # ── text-to-image (z-image turbo) ────────────────────────────────────

    def _build_zimage_turbo_workflow(
        self,
        prompt: str,
        width: int = 1024,
        height: int = 576,
        seed: Optional[int] = None,
        cfg_scale: float = 2.5,
        steps: int = 4,  # turbo = few steps
    ) -> Dict[str, Any]:
        """Build a ComfyUI workflow for z-image turbo text-to-image.

        z-image turbo is a distillation variant that can produce good results
        in very few steps (2-6). Adjust as needed for your ComfyUI setup.
        """
        if seed is None:
            seed = int(time.time()) % (2**32)

        return {
            "1": {
                "inputs": {
                    "text": prompt,
                    "clip": ["11", 0],
                },
                "class_type": "CLIPTextEncode",
            },
            "2": {
                "inputs": {
                    "text": "",  # negative prompt (empty for turbo)
                    "clip": ["11", 0],
                },
                "class_type": "CLIPTextEncode",
            },
            "3": {
                "inputs": {
                    "width": width,
                    "height": height,
                    "batch_size": 1,
                },
                "class_type": "EmptyLatentImage",
            },
            "4": {
                "inputs": {
                    "seed": seed,
                    "steps": steps,
                    "cfg": cfg_scale,
                    "sampler_name": "euler_ancestral",
                    "scheduler": "sgm_uniform",
                    "denoise": 1.0,
                },
                "class_type": "KSampler",
            },
            "5": {
                "inputs": {
                    "ckpt_name": "zimage_turbo.safetensors",
                },
                "class_type": "CheckpointLoaderSimple",
            },
            "6": {
                "inputs": {
                    "samples": ["4", 0],
                    "vae": ["5", 1],
                },
                "class_type": "VAEDecode",
            },
            "7": {
                "inputs": {
                    "filename_prefix": "vidmuse",
                    "images": ["6", 0],
                },
                "class_type": "SaveImage",
            },
        }

    # ── text-to-image (FLUX Dev fallback) ─────────────────────────────────

    def _build_flux_workflow(
        self,
        prompt: str,
        width: int = 1024,
        height: int = 576,
        seed: Optional[int] = None,
        cfg_scale: float = 4.0,
        steps: int = 30,
    ) -> Dict[str, Any]:
        """Build a ComfyUI workflow for FLUX Dev image generation."""
        if seed is None:
            seed = int(time.time()) % (2**32)

        return {
            "11": {
                "inputs": {"ckpt_name": "flux1-dev-fp8.safetensors"},
                "class_type": "CheckpointLoaderSimple",
            },
            "1": {
                "inputs": {"text": prompt, "clip": ["11", 0]},
                "class_type": "CLIPTextEncode",
            },
            "2": {
                "inputs": {"text": "", "clip": ["11", 0]},
                "class_type": "CLIPTextEncode",
            },
            "3": {
                "inputs": {"width": width, "height": height, "batch_size": 1},
                "class_type": "EmptyLatentImage",
            },
            "4": {
                "inputs": {
                    "seed": seed,
                    "steps": steps,
                    "cfg": cfg_scale,
                    "sampler_name": "euler_ancestral",
                    "scheduler": "normal",
                    "denoise": 1.0,
                },
                "class_type": "KSampler",
            },
            "6": {
                "inputs": {"samples": ["4", 0], "vae": ["11", 1]},
                "class_type": "VAEDecode",
            },
            "7": {
                "inputs": {"filename_prefix": "vidmuse", "images": ["6", 0]},
                "class_type": "SaveImage",
            },
        }

    # ── image-to-video (Wan 2.1) ─────────────────────────────────────────

    def _build_wan_i2v_workflow(
        self,
        image_path: str,
        prompt: str,
        num_frames: int = 81,
        seed: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Build a ComfyUI workflow for Wan 2.1 image-to-video."""
        if seed is None:
            seed = int(time.time()) % (2**32)

        return {
            "10": {
                "inputs": {"image": image_path},
                "class_type": "LoadImage",
            },
            "6": {
                "inputs": {"text": prompt},
                "class_type": "CLIPTextEncode",
            },
            "11": {
                "inputs": {"ckpt_name": "wan2.1_i2v_480p_14B_fp8.safetensors"},
                "class_type": "CheckpointLoaderSimple",
            },
            "15": {
                "inputs": {"frame_count": num_frames},
                "class_type": "EmptyHunyuanVideoLatentVideo",
            },
            "16": {
                "inputs": {"samples": ["11", 0]},
                "class_type": "VAEDecode",
            },
            "17": {
                "inputs": {
                    "filename_prefix": "vidmuse_video",
                    "videos": ["16", 0],
                },
                "class_type": "SaveVideo",
            },
        }

    # ── dispatch helpers ──────────────────────────────────────────────────

    async def _dispatch_workflow(
        self,
        workflow: Dict[str, Any],
        wait_timeout: int = 300,
    ) -> Optional[str]:
        """Submit workflow to ComfyUI and wait for output.

        Returns the path to the generated file, or None on failure.
        """
        headers = self._headers()
        payload = self._prompt_payload(workflow)
        async with aiohttp.ClientSession(headers=headers) as session:
            # Prefer /api/prompt (Comfy Cloud); fall back to /prompt (local)
            submit_urls = [f"{self.url}/api/prompt", f"{self.url}/prompt"]
            prompt_id = None
            for submit_url in submit_urls:
                async with session.post(submit_url, json=payload) as resp:
                    if resp.status == 404:
                        continue
                    if resp.status != 200:
                        return None
                    data = await resp.json()
                    prompt_id = data.get("prompt_id")
                    break

            if not prompt_id:
                return None

            # Poll history (more reliable across local + cloud than WS alone)
            start = time.time()
            while time.time() - start < wait_timeout:
                for hist_path in (f"/api/history/{prompt_id}", f"/history/{prompt_id}"):
                    try:
                        async with session.get(f"{self.url}{hist_path}") as resp:
                            if resp.status == 404:
                                continue
                            history = await resp.json()
                            entry = history.get(prompt_id) if prompt_id in history else history
                            if entry and (entry.get("outputs") or entry.get("output")):
                                # Normalize output key
                                if "output" not in entry and "outputs" in entry:
                                    entry = {**entry, "output": entry["outputs"]}
                                path = self._parse_output(entry)
                                if path:
                                    return path
                    except Exception:
                        continue
                await asyncio.sleep(2)

            return None

    async def _dispatch_workflow_sync(
        self,
        workflow: Dict[str, Any],
        wait_timeout: int = 300,
    ) -> Optional[str]:
        """Sync-compatible dispatch (for non-async contexts)."""
        import requests

        headers = self._headers()
        payload = self._prompt_payload(workflow)
        resp = None
        for path in ("/api/prompt", "/prompt"):
            resp = requests.post(
                f"{self.url}{path}",
                headers=headers,
                json=payload,
                timeout=30,
            )
            if resp.status_code != 404:
                break
        if resp is None or resp.status_code != 200:
            return None
        prompt_id = resp.json().get("prompt_id")

        if not prompt_id:
            return None

        start = time.time()
        while time.time() - start < wait_timeout:
            try:
                for path in (f"/api/history/{prompt_id}", f"/history/{prompt_id}"):
                    status_resp = requests.get(
                        f"{self.url}{path}", headers=headers, timeout=5
                    )
                    if status_resp.status_code == 404:
                        continue
                    history = status_resp.json()
                    entry = history.get(prompt_id) if prompt_id in history else history
                    if entry:
                        if "output" not in entry and "outputs" in entry:
                            entry = {**entry, "output": entry["outputs"]}
                        output_path = self._parse_output(entry)
                        if output_path:
                            return output_path
            except Exception:
                pass
            await asyncio.sleep(3)

        return None

    @staticmethod
    def _parse_output(data: Dict[str, Any]) -> Optional[str]:
        """Extract output file path from ComfyUI output data."""
        outputs = data.get("output", {})
        images = outputs.get("images", [])
        videos = outputs.get("videos", [])

        if images:
            img = images[0]
            subfolder = img.get("subfolder", "")
            filename = img.get("filename", "")
            return f"{subfolder}/{filename}" if subfolder else filename

        if videos:
            vid = videos[0]
            subfolder = vid.get("subfolder", "")
            filename = vid.get("filename", "")
            return f"{subfolder}/{filename}" if subfolder else filename

        return None

    # ── high-level methods ────────────────────────────────────────────────

    async def generate_image_turbo(
        self,
        prompt: str,
        width: int = 1024,
        height: int = 576,
        seed: Optional[int] = None,
    ) -> Optional[str]:
        """Generate image using z-image turbo (fast, few steps)."""
        workflow = self._build_zimage_turbo_workflow(prompt, width, height, seed)
        return await self._dispatch_workflow(workflow, wait_timeout=120)

    async def generate_image_flux(
        self,
        prompt: str,
        width: int = 1024,
        height: int = 576,
        seed: Optional[int] = None,
    ) -> Optional[str]:
        """Generate image using FLUX Dev (fallback)."""
        workflow = self._build_flux_workflow(prompt, width, height, seed)
        return await self._dispatch_workflow(workflow, wait_timeout=300)

    async def generate_video_i2v(
        self,
        image_path: str,
        prompt: str,
        num_frames: int = 81,
        seed: Optional[int] = None,
    ) -> Optional[str]:
        """Generate video from image using Wan 2.1 i2v."""
        workflow = self._build_wan_i2v_workflow(image_path, prompt, num_frames, seed)
        return await self._dispatch_workflow(workflow, wait_timeout=600)

    # ── HuMo Lip-Sync Workflow ────────────────────────────────────────────

    def _build_humolipsync_workflow(
        self,
        image_path: str,
        audio_path: str,
        prompt: str = "realistic talking face, natural lip-sync",
        negative_prompt: str = "",
        width: int = 512,
        height: int = 512,
        seed: Optional[int] = None,
        steps: int = 30,
        cfg_scale: float = 4.5,
        sync_strength: float = 0.8,
    ) -> Dict[str, Any]:
        """Build a ComfyUI workflow for HuMo lip-sync generation.

        Synchronizes mouth movement in a face video to an audio track.
        """
        if seed is None:
            seed = int(time.time()) % (2**32)

        # Load image
        image_input = {
            "1": {
                "inputs": {"image": image_path},
                "class_type": "LoadImage",
            }
        }

        # Checkpoint loader for HuMo model
        ckpt_loader = {
            "2": {
                "inputs": {"ckpt_name": "humod.safetensors"},
                "class_type": "CheckpointLoaderSimple",
            }
        }

        # CLIP text encoding
        pos_encode = {
            "3": {
                "inputs": {"text": prompt, "clip": ["2", 0]},
                "class_type": "CLIPTextEncode",
            }
        }
        neg_encode = {
            "4": {
                "inputs": {"text": negative_prompt, "clip": ["2", 0]},
                "class_type": "CLIPTextEncode",
            }
        }

        # Empty latent for video frames
        empty_latent = {
            "5": {
                "inputs": {"width": width, "height": height, "batch_size": 1},
                "class_type": "EmptyLatentImage",
            }
        }

        # KSampler for image generation
        k_sampler = {
            "6": {
                "inputs": {
                    "seed": seed,
                    "steps": steps,
                    "cfg": cfg_scale,
                    "sampler_name": "dpmpp_2m",
                    "scheduler": "karras",
                    "denoise": 0.85,
                    "model": ["2", 0],
                    "positive": ["3", 0],
                    "negative": ["4", 0],
                    "latent_image": ["5", 0],
                },
                "class_type": "KSampler",
            }
        }

        # VAEDecode
        vae_decode = {
            "7": {
                "inputs": {"samples": ["6", 0], "vae": ["2", 1]},
                "class_type": "VAEDecode",
            }
        }

        # HuMo LipSync Processor — applies audio-driven mouth movement
        humo_processor = {
            "8": {
                "inputs": {
                    "image": ["1", 0],
                    "audio_path": audio_path,
                    "sync_strength": sync_strength,
                    "model": ["2", 0],
                },
                "class_type": "HuMoLipSyncProcessor",
            }
        }

        # Frame interpolation for smoother output
        frame_interp = {
            "9": {
                "inputs": {
                    "image": ["8", 0],
                    "frame_interpolation_factor": 2,
                    "method": "rife",
                },
                "class_type": "FrameInterpolation",
            }
        }

        # Save video output
        save_video = {
            "10": {
                "inputs": {
                    "filename_prefix": "vidmuse_humo",
                    "video": ["9", 0],
                    "fps": 24,
                },
                "class_type": "SaveVideo",
            }
        }

        return {**image_input, **ckpt_loader, **pos_encode, **neg_encode,
                **empty_latent, **k_sampler, **vae_decode, **humo_processor,
                **frame_interp, **save_video}

    # ── LTX-2.3 Image-to-Video Workflow ───────────────────────────────────

    def _build_ltx23_i2v_workflow(
        self,
        image_path: str,
        prompt: str = "smooth camera movement, cinematic lighting",
        negative_prompt: str = "",
        width: int = 768,
        height: int = 512,
        seed: Optional[int] = None,
        steps: int = 40,
        cfg_scale: float = 3.5,
        num_frames: int = 81,
        motion_strength: float = 0.7,
        camera_motion: str = "horizontal_pan",
    ) -> Dict[str, Any]:
        """Build a ComfyUI workflow for LTX-2.3 image-to-video generation.

        Uses LTX-2.3 model for high-quality video from reference image.
        """
        if seed is None:
            seed = int(time.time()) % (2**32)

        # Load image as input for I2V
        image_input = {
            "1": {
                "inputs": {"image": image_path, "resize_mode": "center_crop"},
                "class_type": "LoadImageForI2V",
            }
        }

        # Checkpoint loader for LTX-2.3
        ckpt_loader = {
            "2": {
                "inputs": {"ckpt_name": "ltx23_i2v.safetensors"},
                "class_type": "CheckpointLoaderSimple",
            }
        }

        # CLIP text encoding
        pos_encode = {
            "3": {
                "inputs": {"text": prompt, "clip": ["2", 0]},
                "class_type": "CLIPTextEncode",
            }
        }
        neg_encode = {
            "4": {
                "inputs": {"text": negative_prompt, "clip": ["2", 0]},
                "class_type": "CLIPTextEncode",
            }
        }

        # LTX latent video (different from standard latent)
        empty_latent = {
            "5": {
                "inputs": {"frame_count": num_frames, "width": width, "height": height},
                "class_type": "EmptyLTXLatentVideo",
            }
        }

        # Motion module for controlled camera movement
        motion_module = {
            "6": {
                "inputs": {
                    "motion_strength": motion_strength,
                    "camera_motion": camera_motion,
                    "zoom": 0.3 if "zoom" in camera_motion else 0.0,
                },
                "class_type": "LTXMotionModule",
            }
        }

        # KSampler for LTX video generation
        k_sampler = {
            "7": {
                "inputs": {
                    "seed": seed,
                    "steps": steps,
                    "cfg": cfg_scale,
                    "sampler_name": "dpmpp_2m_sde",
                    "scheduler": "karras",
                    "denoise": 0.9,
                    "model": ["2", 0],
                    "positive": ["3", 0],
                    "negative": ["4", 0],
                    "latent_image": ["5", 0],
                },
                "class_type": "KSampler",
            }
        }

        # VAEDecode
        vae_decode = {
            "8": {
                "inputs": {"samples": ["7", 0], "vae": ["2", 1]},
                "class_type": "VAEDecode",
            }
        }

        # Save video output
        save_video = {
            "9": {
                "inputs": {
                    "filename_prefix": "vidmuse_ltx23",
                    "video": ["8", 0],
                    "fps": 24,
                },
                "class_type": "SaveVideo",
            }
        }

        return {**image_input, **ckpt_loader, **pos_encode, **neg_encode,
                **empty_latent, **motion_module, **k_sampler, **vae_decode,
                **save_video}

    # ── Workflow dispatch methods ───────────────────────────────────────────

    async def generate_humo_lipsync(
        self,
        image_path: str,
        audio_path: str,
        prompt: str = "realistic talking face, natural lip-sync",
        width: int = 512,
        height: int = 512,
        seed: Optional[int] = None,
        sync_strength: float = 0.8,
    ) -> Optional[str]:
        """Generate lip-synced video using HuMo model."""
        workflow = self._build_humolipsync_workflow(
            image_path=image_path,
            audio_path=audio_path,
            prompt=prompt,
            width=width,
            height=height,
            seed=seed,
            sync_strength=sync_strength,
        )
        return await self._dispatch_workflow(workflow, wait_timeout=600)

    async def generate_ltx23_i2v(
        self,
        image_path: str,
        prompt: str = "smooth camera movement, cinematic lighting",
        width: int = 768,
        height: int = 512,
        seed: Optional[int] = None,
        steps: int = 40,
        num_frames: int = 81,
        motion_strength: float = 0.7,
        camera_motion: str = "horizontal_pan",
    ) -> Optional[str]:
        """Generate video from image using LTX-2.3 model."""
        workflow = self._build_ltx23_i2v_workflow(
            image_path=image_path,
            prompt=prompt,
            width=width,
            height=height,
            seed=seed,
            steps=steps,
            num_frames=num_frames,
            motion_strength=motion_strength,
            camera_motion=camera_motion,
        )
        return await self._dispatch_workflow(workflow, wait_timeout=600)

    # ── XPU ComfyUI detection & selection ───────────────────────────────────

    def _detect_comfyui_backend(self) -> str:
        """Detect which ComfyUI backend to use (CUDA vs XPU).

        Checks in order:
        1. COMFYUI_URL environment variable (explicit override)
        2. AI-Playground bundled ComfyUI (with XPU support)
        3. Default localhost:8188
        """
        # 1. Explicit override
        url = os.getenv("COMFYUI_URL")
        if url:
            LOG.info("Using explicit COMFYUI_URL: %s", url)
            return "explicit"

        # 2. Check for AI-Playground bundled ComfyUI
        from server.hardware_detect.intel_xpu import (
            detect_intel_xpu,
            is_comfyui_xpu_install,
        )
        info = detect_intel_xpu()
        if info.comfyui_path and is_comfyui_xpu_install(info.comfyui_path):
            LOG.info("Found XPU ComfyUI at %s", info.comfyui_path)
            return "xpu"

        # 3. Check for any ComfyUI in AI-Playground
        if info.comfyui_path:
            LOG.info("Found ComfyUI at %s", info.comfyui_path)
            return "standard"

        # 4. Default to localhost
        LOG.info("No ComfyUI detected, using default localhost:8188")
        return "default"

    @property
    def backend_type(self) -> str:
        """Get the detected ComfyUI backend type."""
        return self._detect_comfyui_backend()

    async def health_check(self) -> Dict[str, Any]:
        """Check ComfyUI availability and backend type."""
        backend = self.backend
        result = {
            "status": "down",
            "backend": backend,
            "url": self.url,
            "auth": bool(self.api_key),
        }

        try:
            async with aiohttp.ClientSession(headers=self._headers()) as session:
                probe = (
                    f"{self.url}/api/user"
                    if backend == "cloud"
                    else f"{self.url}/system_stats"
                )
                async with session.get(probe) as resp:
                    if resp.status in (200, 401, 403):
                        # 401/403 still means endpoint is reachable when key missing
                        result["status"] = "up" if resp.status == 200 else "auth_required"
                        if resp.status == 200 and backend != "cloud":
                            data = await resp.json()
                            result["gpu"] = data.get("gpu", {})
                            result["free_memory"] = data.get("free_memory", {})
                    elif backend != "cloud":
                        async with session.post(
                            f"{self.url}/prompt",
                            json={"prompt": {}, "client_id": self.client_id},
                        ) as resp2:
                            if resp2.status == 200:
                                result["status"] = "up"
        except Exception as e:
            result["error"] = str(e)

        return result
