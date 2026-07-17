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

# Cloud machines have Z-Image Turbo (split files) + Wan partner i2v APIs.
_CLOUD_DEFAULT_ZIMAGE_UNET = "z_image_turbo_bf16.safetensors"
_CLOUD_DEFAULT_ZIMAGE_CLIP = "qwen_3_4b.safetensors"
_CLOUD_DEFAULT_ZIMAGE_VAE = "ae.safetensors"
_CLOUD_DEFAULT_TURBO = "sd_xl_turbo_1.0_fp16.safetensors"  # legacy SDXL fallback
# Schnell FP8 usually needs Flux-specific nodes; SDXL base works with KSampler.
_CLOUD_DEFAULT_FLUX = "sd_xl_base_1.0.safetensors"
_LOCAL_DEFAULT_TURBO = "zimage_turbo.safetensors"
_LOCAL_DEFAULT_FLUX = "flux1-dev-fp8.safetensors"
_DEFAULT_WAN_UNET = "wan2.1_i2v_480p_14B_fp8_scaled.safetensors"
_DEFAULT_WAN_CLIP = "umt5_xxl_fp8_e4m3fn_scaled.safetensors"
_DEFAULT_WAN_VAE = "wan_2.1_vae.safetensors"
_DEFAULT_WAN_CLIP_VISION = "clip_vision_h.safetensors"
_DEFAULT_HUMO_UNET = "humo_17B_fp8_e4m3fn.safetensors"
# HuMo indexes Whisper encoder layers up to 32 — wav2vec2 only exposes ~25 and crashes.
_DEFAULT_HUMO_AUDIO_ENC = "whisper_large_v3_fp16.safetensors"
_DEFAULT_LTX_CKPT = "ltx-2.3-22b-distilled-fp8.safetensors"
_DEFAULT_LTX_TEXT_ENCODER = "gemma_3_12B_it.safetensors"


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

    def _default_ckpt(self, kind: str = "turbo") -> str:
        """Resolve checkpoint name for local vs cloud backends."""
        if kind == "flux":
            env = os.getenv("COMFY_CHECKPOINT_FLUX")
            if env:
                return env
            return _CLOUD_DEFAULT_FLUX if self.backend == "cloud" else _LOCAL_DEFAULT_FLUX
        env = os.getenv("COMFY_CHECKPOINT_TURBO")
        if env:
            return env
        return _CLOUD_DEFAULT_TURBO if self.backend == "cloud" else _LOCAL_DEFAULT_TURBO

    # ── text-to-image (Z-Image Turbo) ─────────────────────────────────────

    def _build_zimage_turbo_workflow(
        self,
        prompt: str,
        width: int = 1024,
        height: int = 576,
        seed: Optional[int] = None,
        cfg_scale: float = 1.0,
        steps: int = 8,
    ) -> Dict[str, Any]:
        """Official Z-Image Turbo graph (UNET + Qwen CLIP + ae VAE).

        Matches Comfy-Org template: EmptySD3LatentImage + ModelSamplingAuraFlow
        + ConditioningZeroOut negatives. Falls back to SDXL Turbo graph only when
        explicitly requested via COMFY_IMAGE_BACKEND=sdxl.
        """
        if os.getenv("COMFY_IMAGE_BACKEND", "").strip().lower() == "sdxl":
            return self._build_sdxl_turbo_workflow(
                prompt, width, height, seed, cfg_scale=1.5, steps=4
            )

        if seed is None:
            seed = int(time.time()) % (2**32)

        unet = os.getenv("COMFY_CHECKPOINT_TURBO", _CLOUD_DEFAULT_ZIMAGE_UNET)
        clip = os.getenv("COMFY_ZIMAGE_CLIP", _CLOUD_DEFAULT_ZIMAGE_CLIP)
        vae = os.getenv("COMFY_ZIMAGE_VAE", _CLOUD_DEFAULT_ZIMAGE_VAE)

        # Local installs may still use a fused checkpoint name without split files.
        if self.backend != "cloud" and unet.endswith(".safetensors") and "z_image" not in unet:
            return self._build_sdxl_turbo_workflow(
                prompt, width, height, seed, cfg_scale=2.5, steps=4, ckpt=unet
            )

        return {
            "28": {
                "inputs": {
                    "unet_name": unet,
                    "weight_dtype": "default",
                },
                "class_type": "UNETLoader",
            },
            "30": {
                "inputs": {
                    "clip_name": clip,
                    "type": "lumina2",
                    "device": "default",
                },
                "class_type": "CLIPLoader",
            },
            "29": {
                "inputs": {"vae_name": vae},
                "class_type": "VAELoader",
            },
            "27": {
                "inputs": {"text": prompt, "clip": ["30", 0]},
                "class_type": "CLIPTextEncode",
            },
            "33": {
                "inputs": {"conditioning": ["27", 0]},
                "class_type": "ConditioningZeroOut",
            },
            "13": {
                "inputs": {
                    "width": width,
                    "height": height,
                    "batch_size": 1,
                },
                "class_type": "EmptySD3LatentImage",
            },
            "11": {
                "inputs": {"model": ["28", 0], "shift": 3.0},
                "class_type": "ModelSamplingAuraFlow",
            },
            "3": {
                "inputs": {
                    "seed": seed,
                    "steps": steps,
                    "cfg": cfg_scale,
                    "sampler_name": "res_multistep",
                    "scheduler": "simple",
                    "denoise": 1.0,
                    "model": ["11", 0],
                    "positive": ["27", 0],
                    "negative": ["33", 0],
                    "latent_image": ["13", 0],
                },
                "class_type": "KSampler",
            },
            "8": {
                "inputs": {"samples": ["3", 0], "vae": ["29", 0]},
                "class_type": "VAEDecode",
            },
            "9": {
                "inputs": {
                    "filename_prefix": "vidmuse_zimage",
                    "images": ["8", 0],
                },
                "class_type": "SaveImage",
            },
        }

    def _build_sdxl_turbo_workflow(
        self,
        prompt: str,
        width: int = 1024,
        height: int = 576,
        seed: Optional[int] = None,
        cfg_scale: float = 1.5,
        steps: int = 4,
        ckpt: Optional[str] = None,
    ) -> Dict[str, Any]:
        """SDXL Turbo / fused-checkpoint fallback workflow."""
        if seed is None:
            seed = int(time.time()) % (2**32)

        ckpt = ckpt or (
            _CLOUD_DEFAULT_TURBO if self.backend == "cloud" else self._default_ckpt("turbo")
        )
        return {
            "1": {
                "inputs": {"text": prompt, "clip": ["5", 1]},
                "class_type": "CLIPTextEncode",
            },
            "2": {
                "inputs": {"text": "", "clip": ["5", 1]},
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
                    "model": ["5", 0],
                    "positive": ["1", 0],
                    "negative": ["2", 0],
                    "latent_image": ["3", 0],
                },
                "class_type": "KSampler",
            },
            "5": {
                "inputs": {"ckpt_name": ckpt},
                "class_type": "CheckpointLoaderSimple",
            },
            "6": {
                "inputs": {"samples": ["4", 0], "vae": ["5", 2]},
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

        ckpt = self._default_ckpt("flux")
        return {
            "11": {
                "inputs": {"ckpt_name": ckpt},
                "class_type": "CheckpointLoaderSimple",
            },
            "1": {
                "inputs": {"text": prompt, "clip": ["11", 1]},
                "class_type": "CLIPTextEncode",
            },
            "2": {
                "inputs": {"text": "", "clip": ["11", 1]},
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
                    "model": ["11", 0],
                    "positive": ["1", 0],
                    "negative": ["2", 0],
                    "latent_image": ["3", 0],
                },
                "class_type": "KSampler",
            },
            "6": {
                "inputs": {"samples": ["4", 0], "vae": ["11", 2]},
                "class_type": "VAEDecode",
            },
            "7": {
                "inputs": {"filename_prefix": "vidmuse", "images": ["6", 0]},
                "class_type": "SaveImage",
            },
        }

    # ── image-to-video (Wan partner API / HuMo-capable audio+image) ───────

    async def _upload_input_file(
        self,
        session: aiohttp.ClientSession,
        path: str | Path,
        kind: str = "image",
    ) -> Optional[str]:
        """Upload a local file into Comfy input folder; return remote filename."""
        source = Path(path)
        if not source.is_file():
            print(f"[ComfyUI] upload missing file: {source}")
            return None

        if kind == "audio":
            # Comfy Cloud has no /api/upload/audio — upload through the image
            # endpoint into the input folder (same as desktop LoadAudio inputs).
            field = "image"
            urls = [f"{self.url}/api/upload/image", f"{self.url}/upload/image"]
            suffix = source.suffix.lower()
            content_type = {
                ".wav": "audio/wav",
                ".mp3": "audio/mpeg",
                ".flac": "audio/flac",
                ".ogg": "audio/ogg",
                ".m4a": "audio/mp4",
            }.get(suffix, "application/octet-stream")
        else:
            field = "image"
            urls = [f"{self.url}/api/upload/image", f"{self.url}/upload/image"]
            content_type = "image/png"

        data = source.read_bytes()
        for url in urls:
            form = aiohttp.FormData()
            form.add_field(
                field,
                data,
                filename=source.name,
                content_type=content_type,
            )
            try:
                async with session.post(url, data=form) as resp:
                    if resp.status == 404:
                        continue
                    body = await resp.text()
                    if resp.status != 200:
                        print(f"[ComfyUI] upload failed ({resp.status}): {body[:300]}")
                        continue
                    meta = json.loads(body)
                    name = meta.get("name")
                    sub = meta.get("subfolder") or ""
                    return f"{sub}/{name}" if sub else name
            except Exception as e:
                print(f"[ComfyUI] upload error: {e}")
                continue
        return None

    def _build_humo_lipsync_workflow(
        self,
        image_name: str,
        audio_name: str,
        prompt: str,
        num_frames: int = 49,
        seed: Optional[int] = None,
        width: int = 512,
        height: int = 512,
        negative_prompt: str = (
            "overexposure, static, blurred details, subtitles, worst quality, "
            "low quality, deformed face, watermark, frozen mouth"
        ),
    ) -> Dict[str, Any]:
        """Native ComfyUI HuMo image+audio lip-sync video (no partner APIs).

        Graph: LoadImage + LoadAudio → AudioEncoderEncode → WanHuMoImageToVideo
        → KSampler → CreateVideo/SaveVideo, using humo_17B on ComfyUI workers.
        """
        if not audio_name:
            raise ValueError("HuMo lip-sync requires an audio input")
        if seed is None:
            seed = int(time.time()) % (2**32)

        # HuMo/Wan length must be 4n+1
        length = max(9, int(num_frames))
        if (length - 1) % 4 != 0:
            length = ((length - 1) // 4) * 4 + 1

        clip_name = os.getenv("COMFY_WAN_CLIP", _DEFAULT_WAN_CLIP)
        vae_name = os.getenv("COMFY_WAN_VAE", _DEFAULT_WAN_VAE)
        unet = os.getenv("COMFY_HUMO_UNET", _DEFAULT_HUMO_UNET)
        enc = os.getenv("COMFY_HUMO_AUDIO_ENC", _DEFAULT_HUMO_AUDIO_ENC)
        steps = int(os.getenv("COMFY_HUMO_STEPS", os.getenv("COMFY_WAN_STEPS", "20")))
        cfg = float(os.getenv("COMFY_HUMO_CFG", os.getenv("COMFY_WAN_CFG", "5")))
        shift = float(os.getenv("COMFY_HUMO_SHIFT", os.getenv("COMFY_WAN_SHIFT", "5")))
        fps = float(os.getenv("COMFY_HUMO_FPS", "25"))

        return {
            "1": {
                "inputs": {"image": image_name},
                "class_type": "LoadImage",
            },
            "2": {
                "inputs": {"audio": audio_name},
                "class_type": "LoadAudio",
            },
            "40": {
                "inputs": {"audio_encoder_name": enc},
                "class_type": "AudioEncoderLoader",
            },
            "41": {
                "inputs": {
                    "audio_encoder": ["40", 0],
                    "audio": ["2", 0],
                },
                "class_type": "AudioEncoderEncode",
            },
            "37": {
                "inputs": {"unet_name": unet, "weight_dtype": "default"},
                "class_type": "UNETLoader",
            },
            "36": {
                "inputs": {"model": ["37", 0]},
                "class_type": "HuMoI2VPatch",
            },
            "38": {
                "inputs": {
                    "clip_name": clip_name,
                    "type": "wan",
                    "device": "default",
                },
                "class_type": "CLIPLoader",
            },
            "39": {
                "inputs": {"vae_name": vae_name},
                "class_type": "VAELoader",
            },
            "6": {
                "inputs": {"text": prompt, "clip": ["38", 0]},
                "class_type": "CLIPTextEncode",
            },
            "7": {
                "inputs": {"text": negative_prompt, "clip": ["38", 0]},
                "class_type": "CLIPTextEncode",
            },
            "50": {
                "inputs": {
                    "positive": ["6", 0],
                    "negative": ["7", 0],
                    "vae": ["39", 0],
                    "width": width,
                    "height": height,
                    "length": length,
                    "batch_size": 1,
                    "ref_image": ["1", 0],
                    "audio_encoder_output": ["41", 0],
                },
                "class_type": "WanHuMoImageToVideo",
            },
            "48": {
                "inputs": {"model": ["36", 0], "shift": shift},
                "class_type": "ModelSamplingSD3",
            },
            "3": {
                "inputs": {
                    "seed": seed,
                    "steps": steps,
                    "cfg": cfg,
                    "sampler_name": "uni_pc",
                    "scheduler": "simple",
                    "denoise": 1.0,
                    "model": ["48", 0],
                    "positive": ["50", 0],
                    "negative": ["50", 1],
                    "latent_image": ["50", 2],
                },
                "class_type": "KSampler",
            },
            "8": {
                "inputs": {"samples": ["3", 0], "vae": ["39", 0]},
                "class_type": "VAEDecode",
            },
            "12": {
                "inputs": {
                    "images": ["8", 0],
                    "fps": fps,
                    "audio": ["2", 0],
                },
                "class_type": "CreateVideo",
            },
            "13": {
                "inputs": {
                    "video": ["12", 0],
                    "filename_prefix": "vidmuse_humo",
                    "format": "mp4",
                    "codec": "h264",
                },
                "class_type": "SaveVideo",
            },
        }

    def _build_ltx23_av_i2v_workflow(
        self,
        image_name: str,
        audio_name: str,
        prompt: str,
        num_frames: int = 97,
        seed: Optional[int] = None,
        width: int = 768,
        height: int = 512,
        negative_prompt: str = (
            "blurry, low quality, distorted face, identity drift, morphing face, "
            "watermark, text overlay, jitter, flicker, washed colors"
        ),
    ) -> Dict[str, Any]:
        """Native LTX-2.3 image + audio → video (no partner/API nodes).

        Matches the official single-stage distilled I2V shape:
        Empty video latent + ImgToVideoConditionOnly + Empty audio latent → ConcatAV,
        with LTXVReferenceAudio guiding from the music slice; CreateVideo muxes the
        original audio for faithful playback.
        """
        if not audio_name:
            raise ValueError("LTX-2.3 A/V i2v requires an audio input")
        if seed is None:
            seed = int(time.time()) % (2**32)

        # LTX length must be 8n+1 (widget step=8, default 97)
        length = max(9, int(num_frames))
        if (length - 1) % 8 != 0:
            length = ((length - 1) // 8) * 8 + 1

        # Match EmptyLTXVLatentVideo step constraints
        width = max(64, (int(width) // 32) * 32)
        height = max(64, (int(height) // 32) * 32)

        ckpt = os.getenv("COMFY_LTX_CKPT", _DEFAULT_LTX_CKPT)
        text_enc = os.getenv("COMFY_LTX_TEXT_ENCODER", _DEFAULT_LTX_TEXT_ENCODER)
        fps = float(os.getenv("COMFY_LTX_FPS", "24"))
        fps_i = max(1, int(round(fps)))
        steps = int(os.getenv("COMFY_LTX_STEPS", "8"))
        cfg = float(os.getenv("COMFY_LTX_CFG", "1.0"))
        strength = float(os.getenv("COMFY_LTX_I2V_STRENGTH", "0.85"))
        identity_scale = float(os.getenv("COMFY_LTX_AUDIO_IDENTITY", "1.0"))

        return {
            # Media
            "1": {"inputs": {"image": image_name}, "class_type": "LoadImage"},
            "2": {"inputs": {"audio": audio_name}, "class_type": "LoadAudio"},
            "3": {
                "inputs": {"image": ["1", 0], "img_compression": 18},
                "class_type": "LTXVPreprocess",
            },
            # Models
            "10": {
                "inputs": {"ckpt_name": ckpt},
                "class_type": "CheckpointLoaderSimple",
            },
            "11": {
                "inputs": {
                    "text_encoder": text_enc,
                    "ckpt_name": ckpt,
                    "device": "default",
                },
                "class_type": "LTXAVTextEncoderLoader",
            },
            "12": {
                "inputs": {"ckpt_name": ckpt},
                "class_type": "LTXVAudioVAELoader",
            },
            # Text
            "20": {
                "inputs": {"text": prompt, "clip": ["11", 0]},
                "class_type": "CLIPTextEncode",
            },
            "21": {
                "inputs": {"text": negative_prompt, "clip": ["11", 0]},
                "class_type": "CLIPTextEncode",
            },
            "22": {
                "inputs": {
                    "positive": ["20", 0],
                    "negative": ["21", 0],
                    "frame_rate": fps,
                },
                "class_type": "LTXVConditioning",
            },
            # Image-locked video latent + size-matched empty audio latent
            "30": {
                "inputs": {
                    "width": width,
                    "height": height,
                    "length": length,
                    "batch_size": 1,
                },
                "class_type": "EmptyLTXVLatentVideo",
            },
            "31": {
                "inputs": {
                    "vae": ["10", 2],
                    "image": ["3", 0],
                    "latent": ["30", 0],
                    "strength": strength,
                },
                "class_type": "LTXVImgToVideoConditionOnly",
            },
            "32": {
                "inputs": {
                    "frames_number": length,
                    "frame_rate": fps_i,
                    "batch_size": 1,
                    "audio_vae": ["12", 0],
                },
                "class_type": "LTXVEmptyLatentAudio",
            },
            "33": {
                "inputs": {
                    "video_latent": ["31", 0],
                    "audio_latent": ["32", 0],
                },
                "class_type": "LTXVConcatAVLatent",
            },
            # Music slice → audio identity / A2V guidance
            "34": {
                "inputs": {
                    "model": ["10", 0],
                    "positive": ["22", 0],
                    "negative": ["22", 1],
                    "reference_audio": ["2", 0],
                    "audio_vae": ["12", 0],
                    "identity_guidance_scale": identity_scale,
                    "start_percent": 0.0,
                    "end_percent": 1.0,
                },
                "class_type": "LTXVReferenceAudio",
            },
            # Sampling
            "40": {
                "inputs": {
                    "steps": steps,
                    "max_shift": 2.05,
                    "base_shift": 0.95,
                    "stretch": True,
                    "terminal": 0.1,
                    "latent": ["33", 0],
                },
                "class_type": "LTXVScheduler",
            },
            "41": {
                "inputs": {"sampler_name": "euler_ancestral_cfg_pp"},
                "class_type": "KSamplerSelect",
            },
            "42": {
                "inputs": {"noise_seed": seed},
                "class_type": "RandomNoise",
            },
            "43": {
                "inputs": {
                    "model": ["34", 0],
                    "positive": ["34", 1],
                    "negative": ["34", 2],
                    "cfg": cfg,
                },
                "class_type": "CFGGuider",
            },
            "44": {
                "inputs": {
                    "noise": ["42", 0],
                    "guider": ["43", 0],
                    "sampler": ["41", 0],
                    "sigmas": ["40", 0],
                    "latent_image": ["33", 0],
                },
                "class_type": "SamplerCustomAdvanced",
            },
            "45": {
                "inputs": {"av_latent": ["44", 0]},
                "class_type": "LTXVSeparateAVLatent",
            },
            "50": {
                "inputs": {"samples": ["45", 0], "vae": ["10", 2]},
                "class_type": "VAEDecode",
            },
            "51": {
                "inputs": {
                    "images": ["50", 0],
                    "fps": fps,
                    "audio": ["2", 0],
                },
                "class_type": "CreateVideo",
            },
            "52": {
                "inputs": {
                    "video": ["51", 0],
                    "filename_prefix": "vidmuse_ltx23",
                    "format": "mp4",
                    "codec": "h264",
                },
                "class_type": "SaveVideo",
            },
        }

    async def generate_video_i2v(
        self,
        image_path: str,
        prompt: str,
        num_frames: int = 49,
        seed: Optional[int] = None,
        audio_path: Optional[str] = None,
        duration: Optional[float] = None,
        width: int = 768,
        height: int = 512,
    ) -> Optional[str]:
        """Generate image+audio→video via native ComfyUI (LTX-2.3 default, HuMo fallback)."""
        if not audio_path or not Path(audio_path).is_file():
            print("[ComfyUI] image+audio video requires a local audio_path")
            return None

        backend = (os.getenv("COMFY_VIDEO_BACKEND") or "ltx23").strip().lower()
        headers = self._headers()
        async with aiohttp.ClientSession(headers=headers) as session:
            image_name = await self._upload_input_file(session, image_path, "image")
            if not image_name:
                return None
            audio_name = await self._upload_input_file(session, audio_path, "audio")
            if not audio_name:
                print("[ComfyUI] audio upload failed; cannot run A/V i2v")
                return None

        if backend in ("humo", "wan", "wan-humo"):
            fps = float(os.getenv("COMFY_HUMO_FPS", "25"))
            frames = num_frames
            if duration is not None:
                frames = max(9, int(round(float(duration) * fps)))
                if (frames - 1) % 4 != 0:
                    frames = ((frames - 1) // 4) * 4 + 1
            lipsync_prompt = (
                f"{prompt}, talking to camera, natural mouth movement, lip sync, "
                f"consistent character identity, facial animation driven by speech"
            )
            workflow = self._build_humo_lipsync_workflow(
                image_name=image_name,
                audio_name=audio_name,
                prompt=lipsync_prompt,
                num_frames=frames,
                seed=seed,
                width=min(width, 512),
                height=min(height, 512),
            )
        else:
            fps = float(os.getenv("COMFY_LTX_FPS", "24"))
            frames = num_frames if duration is None else max(9, int(round(float(duration) * fps)))
            if (frames - 1) % 8 != 0:
                frames = ((frames - 1) // 8) * 8 + 1
            # Cap very long slices — LTX comfort zone is ~4s (97 @ 24fps)
            frames = min(frames, int(os.getenv("COMFY_LTX_MAX_FRAMES", "97")))
            if (frames - 1) % 8 != 0:
                frames = ((frames - 1) // 8) * 8 + 1
            av_prompt = (
                f"{prompt}, same character throughout, consistent face and outfit, "
                f"cinematic music video, natural motion, audio-reactive performance"
            )
            workflow = self._build_ltx23_av_i2v_workflow(
                image_name=image_name,
                audio_name=audio_name,
                prompt=av_prompt,
                num_frames=frames,
                seed=seed,
                width=width,
                height=height,
            )

        path = await self._dispatch_workflow(workflow, wait_timeout=1200)
        # Soft-fallback: if LTX fails, try HuMo once
        if not path and backend not in ("humo", "wan", "wan-humo"):
            print("[ComfyUI] LTX-2.3 failed — falling back to HuMo lip-sync")
            fps = float(os.getenv("COMFY_HUMO_FPS", "25"))
            frames = max(9, int(round(float(duration or 2.0) * fps)))
            if (frames - 1) % 4 != 0:
                frames = ((frames - 1) // 4) * 4 + 1
            workflow = self._build_humo_lipsync_workflow(
                image_name=image_name,
                audio_name=audio_name,
                prompt=f"{prompt}, lip sync, consistent character",
                num_frames=frames,
                seed=seed,
                width=512,
                height=512,
            )
            path = await self._dispatch_workflow(workflow, wait_timeout=900)
        return path

    # Back-compat alias used by older call sites
    def _build_wan_i2v_workflow(self, *args, **kwargs) -> Dict[str, Any]:
        return self._build_humo_lipsync_workflow(*args, **kwargs)

    # ── dispatch helpers ──────────────────────────────────────────────────

    @staticmethod
    def _parse_output(data: Dict[str, Any]) -> Optional[Dict[str, str]]:
        """Extract first image or video output metadata from ComfyUI history."""
        outputs = data.get("outputs") or data.get("output") or {}

        if isinstance(outputs, dict):
            if outputs.get("images") or outputs.get("videos") or outputs.get("gifs"):
                items = (
                    outputs.get("images")
                    or outputs.get("videos")
                    or outputs.get("gifs")
                    or []
                )
                if items:
                    return items[0]

            for node_out in outputs.values():
                if not isinstance(node_out, dict):
                    continue
                for key in ("images", "videos", "gifs", "video"):
                    items = node_out.get(key) or []
                    if isinstance(items, dict):
                        items = [items]
                    if items:
                        item = items[0]
                        if isinstance(item, dict) and item.get("filename"):
                            return item
        return None

    async def _fetch_output_file(
        self,
        session: aiohttp.ClientSession,
        meta: Dict[str, str],
        dest: Path,
    ) -> bool:
        """Download a Comfy output file via /view (follows 302 on cloud)."""
        filename = meta.get("filename")
        if not filename:
            return False

        params = {
            "filename": filename,
            "subfolder": meta.get("subfolder", "") or "",
            "type": meta.get("type", "output") or "output",
        }
        for view_path in ("/api/view", "/view"):
            try:
                async with session.get(
                    f"{self.url}{view_path}",
                    params=params,
                    allow_redirects=True,
                ) as resp:
                    if resp.status != 200:
                        print(
                            f"[ComfyUI] view failed ({resp.status}) {view_path} "
                            f"filename={filename}"
                        )
                        continue
                    data = await resp.read()
                    if not data:
                        continue
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    dest.write_bytes(data)
                    return True
            except Exception as e:
                print(f"[ComfyUI] view error: {e}")
                continue
        return False

    async def _wait_cloud_job(
        self,
        session: aiohttp.ClientSession,
        prompt_id: str,
        wait_timeout: int,
    ) -> Optional[Dict[str, Any]]:
        """Poll Comfy Cloud job status until completed/failed; return outputs."""
        start = time.time()
        while time.time() - start < wait_timeout:
            try:
                async with session.get(
                    f"{self.url}/api/job/{prompt_id}/status"
                ) as resp:
                    if resp.status == 200:
                        status_data = await resp.json()
                        status = status_data.get("status") or status_data.get("state")
                        if status in ("failed", "cancelled", "error"):
                            # Pull detail for logging
                            err = status_data
                            async with session.get(
                                f"{self.url}/api/jobs/{prompt_id}"
                            ) as detail:
                                if detail.status == 200:
                                    err = await detail.json()
                            msg = (
                                (err.get("execution_error") or {}).get(
                                    "exception_message"
                                )
                                or status_data.get("error_message")
                                or err.get("error")
                                or status
                            )
                            print(f"[ComfyUI] job {prompt_id} {status}: {msg}")
                            return None
                        # Cloud status endpoint uses "success"/"executed"; jobs detail uses "completed"
                        if status in ("completed", "success", "executed"):
                            async with session.get(
                                f"{self.url}/api/jobs/{prompt_id}"
                            ) as detail:
                                if detail.status != 200:
                                    print(
                                        f"[ComfyUI] jobs detail failed "
                                        f"({detail.status})"
                                    )
                                    return None
                                job = await detail.json()
                                return job.get("outputs") or {}
                        # queued_limited / in_progress / pending — keep polling
            except Exception as e:
                print(f"[ComfyUI] status poll error: {e}")
            await asyncio.sleep(2)
        print(f"[ComfyUI] job {prompt_id} timed out after {wait_timeout}s")
        return None

    async def _dispatch_workflow(
        self,
        workflow: Dict[str, Any],
        wait_timeout: int = 300,
    ) -> Optional[str]:
        """Submit workflow to ComfyUI and wait for output.

        Returns the local path to the downloaded file, or None on failure.
        """
        headers = self._headers()
        payload = self._prompt_payload(workflow)
        async with aiohttp.ClientSession(headers=headers) as session:
            submit_urls = [f"{self.url}/api/prompt", f"{self.url}/prompt"]
            prompt_id = None
            for submit_url in submit_urls:
                async with session.post(submit_url, json=payload) as resp:
                    if resp.status == 404:
                        continue
                    body = await resp.text()
                    if resp.status != 200:
                        print(
                            f"[ComfyUI] submit failed ({resp.status}): {body[:500]}"
                        )
                        return None
                    try:
                        data = json.loads(body)
                    except Exception:
                        print(f"[ComfyUI] submit non-JSON: {body[:300]}")
                        return None
                    if data.get("error") or data.get("node_errors"):
                        # Soft validation errors can still include prompt_id on cloud,
                        # but treat hard errors as failure when present without id.
                        if not data.get("prompt_id"):
                            print(f"[ComfyUI] submit rejected: {body[:500]}")
                            return None
                        if data.get("node_errors"):
                            print(
                                f"[ComfyUI] submit node_errors: "
                                f"{json.dumps(data.get('node_errors'))[:500]}"
                            )
                    prompt_id = data.get("prompt_id")
                    break

            if not prompt_id:
                print("[ComfyUI] submit returned no prompt_id")
                return None

            print(f"[ComfyUI] job submitted: {prompt_id} ({self.backend})")

            cache_dir = OUTPUT_DIR / "comfy_cache"
            cache_dir.mkdir(parents=True, exist_ok=True)

            # Cloud: use job status API (history is not reliable / returns SPA HTML)
            if self.backend == "cloud":
                outputs = await self._wait_cloud_job(session, prompt_id, wait_timeout)
                if not outputs:
                    return None
                meta = self._parse_output({"outputs": outputs})
                if not meta or not meta.get("filename"):
                    print(f"[ComfyUI] no image in job outputs: {list(outputs.keys())}")
                    return None
                local_path = cache_dir / meta["filename"]
                if await self._fetch_output_file(session, meta, local_path):
                    print(f"[ComfyUI] saved {local_path}")
                    return str(local_path)
                return None

            # Local: poll history like classic ComfyUI
            start = time.time()
            while time.time() - start < wait_timeout:
                for hist_path in (
                    f"/api/history/{prompt_id}",
                    f"/history/{prompt_id}",
                ):
                    try:
                        async with session.get(f"{self.url}{hist_path}") as resp:
                            if resp.status == 404:
                                continue
                            history = await resp.json()
                            entry = (
                                history.get(prompt_id)
                                if prompt_id in history
                                else history
                            )
                            if not entry:
                                continue
                            meta = self._parse_output(entry)
                            if not meta:
                                continue
                            local_path = cache_dir / meta["filename"]
                            if await self._fetch_output_file(
                                session, meta, local_path
                            ):
                                return str(local_path)
                    except Exception:
                        continue
                await asyncio.sleep(2)

            print(f"[ComfyUI] local history timeout for {prompt_id}")
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
                        meta = self._parse_output(entry)
                        if meta:
                            cache_dir = OUTPUT_DIR / "comfy_cache"
                            cache_dir.mkdir(parents=True, exist_ok=True)
                            local_path = cache_dir / meta["filename"]
                            import requests as req

                            params = {
                                "filename": meta["filename"],
                                "subfolder": meta.get("subfolder", ""),
                                "type": meta.get("type", "output"),
                            }
                            for view_path in ("/api/view", "/view"):
                                view_resp = req.get(
                                    f"{self.url}{view_path}",
                                    headers=headers,
                                    params=params,
                                    timeout=60,
                                )
                                if view_resp.status_code == 200:
                                    local_path.write_bytes(view_resp.content)
                                    return str(local_path)
            except Exception:
                pass
            await asyncio.sleep(3)

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
