"""ComfyUI integration for VidMuse Local.

Supports:
  - z-image turbo text-to-image (generating image references)
  - Wan 2.1 image-to-video / InfiniteTalk lip-sync
  - MiniMax H3 Reference-to-Video (open weights, local + cloud)
  - LTX-2.3 image+audio→video (IA2V)
  - HuMo lip-sync workflow
  - FLUX Dev (fallback)
  - Intel XPU ComfyUI (auto-detect + auto-install)
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

import aiohttp

LOG = logging.getLogger("vidmuse.comfy")

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
_DEFAULT_INFINITETALK_UNET = "wan2.1_i2v_480p_14B_fp8_scaled.safetensors"
_DEFAULT_INFINITETALK_PATCH = "wan2.1_infiniteTalk_single_fp16.safetensors"
_DEFAULT_INFINITETALK_AUDIO_ENC = "wav2vec2_large_english_fp16.safetensors"
# MiniMax H3 Reference-to-Video (open weights; local ComfyUI ≥ 0.30)
# R2V uses ref2va weights — not the fl2va I2V/T2V set.
_DEFAULT_H3_UNET = "minimax_h3_ref2va_pruned_int8_convrot.safetensors"
_DEFAULT_H3_CLIP = "qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors"
_DEFAULT_H3_VIDEO_VAE = "minimax_h3_video_vae_fp16.safetensors"
_DEFAULT_H3_AUDIO_VAE = "minimax_h3_audio_vae_fp32.safetensors"
_DEFAULT_H3_LIGHTNING_LORA = "minimax_h3_ref2v_turbo_4step_v0.1_comfyui_bf16.safetensors"
# FastVideo FastH3 8-step distilled (I2V first/last + T2V only — no Ref2VA)
_DEFAULT_FASTH3_UNET = "fastvideo_fasth3_8step_v2_pruned_int8_convrot.safetensors"
_DEFAULT_FASTH3_VIDEO_VAE = "minimax_h3_video_vae_int8_convrot.safetensors"


def _h3_align_frames(num_frames: int) -> int:
    """Align MiniMax H3 length to supported 5 + 17k frame counts (@24fps)."""
    n = max(5, int(num_frames))
    return n + (5 - (n % 17)) % 17


def _ltx_align_frames(num_frames: int) -> int:
    """Align LTX-2.3 length to 8n+1."""
    length = max(9, int(num_frames))
    if (length - 1) % 8 != 0:
        length = ((length - 1) // 8) * 8 + 1
    return length


def _is_loopback_url(url: Optional[str]) -> bool:
    if not url:
        return False
    low = url.strip().lower()
    return any(
        token in low
        for token in (
            "://localhost",
            "://127.0.0.1",
            "://[::1]",
            "://0.0.0.0",
        )
    )


def _resolve_comfy_config(
    backend_override: Optional[str] = None,
    url_override: Optional[str] = None,
):
    """Return (base_url, api_key, backend_label)."""
    backend = (backend_override or COMFYUI_BACKEND or (
        "cloud" if VIDMUSE_MODE == "cloud" else "local"
    )).strip().lower()
    if backend == "cloud":
        # UI often keeps a local default URL even when Cloud is selected —
        # never let loopback override the real Comfy Cloud endpoint.
        url = (url_override or "").strip()
        if not url or _is_loopback_url(url):
            url = COMFY_CLOUD_URL
        return url.rstrip("/"), COMFY_CLOUD_API_KEY, "cloud"
    label = "ai-playground" if backend in ("ai-playground", "ai_playground") else "local"
    return (url_override or COMFYUI_URL).rstrip("/"), None, label


class ComfyUIClient:
    """Async client for ComfyUI's HTTP + WebSocket API (local or Comfy Cloud)."""

    def __init__(
        self,
        backend: Optional[str] = None,
        url: Optional[str] = None,
    ):
        self.url, self.api_key, self.backend = _resolve_comfy_config(backend, url)
        self.client_id = str(uuid.uuid4())
        self._available_models: Optional[List[str]] = None
        self._model_probe_error: Optional[str] = None
        self._resolved_ckpt_log: set[str] = set()

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

    def _extract_models_from_object_info(self, objects: Dict[str, Any]) -> List[str]:
        checkpoints: List[str] = []
        for node_name in (
            "CheckpointLoaderSimple",
            "UNETLoader",
            "DiffusionModelLoader",
            "VAELoader",
            "CLIPLoader",
        ):
            node = objects.get(node_name) or {}
            required = (node.get("input") or {}).get("required") or {}
            for input_spec in required.values():
                if (
                    isinstance(input_spec, list)
                    and input_spec
                    and isinstance(input_spec[0], list)
                ):
                    checkpoints.extend(str(x) for x in input_spec[0])
        return list(dict.fromkeys(checkpoints))

    async def probe_available_models(self, force: bool = False) -> List[str]:
        """Probe installed Comfy models (local / AI Playground). Cached per client."""
        if self.backend == "cloud":
            return []
        if self._available_models is not None and not force:
            return self._available_models
        try:
            async with aiohttp.ClientSession(headers=self._headers()) as session:
                async with session.get(f"{self.url}/object_info", timeout=8) as resp:
                    if resp.status != 200:
                        self._model_probe_error = f"HTTP {resp.status}"
                        self._available_models = []
                        return []
                    objects = await resp.json()
                    models = self._extract_models_from_object_info(objects)
                    self._available_models = models
                    self._model_probe_error = None
                    return models
        except Exception as exc:
            self._model_probe_error = str(exc)
            self._available_models = []
            return []

    def _pick_from_available(
        self,
        preferred: List[str],
        *,
        substrings: Optional[List[str]] = None,
    ) -> Optional[str]:
        available = self._available_models or []
        if not available:
            return None
        lower_map = {m.lower(): m for m in available}
        for name in preferred:
            if not name:
                continue
            if name in available:
                return name
            hit = lower_map.get(name.lower())
            if hit:
                return hit
            # Basename match (Comfy sometimes returns relative paths)
            base = Path(name).name.lower()
            for m in available:
                if Path(m).name.lower() == base:
                    return m
        if substrings:
            for sub in substrings:
                sub_l = sub.lower()
                for m in available:
                    if sub_l in m.lower():
                        return m
        # Last resort: any safetensors checkpoint-looking file
        for m in available:
            low = m.lower()
            if low.endswith((".safetensors", ".ckpt")) and "vae" not in low and "clip" not in low:
                return m
        return available[0] if available else None

    def _default_ckpt(self, kind: str = "turbo") -> str:
        """Resolve checkpoint name for local vs cloud backends with local fallbacks."""
        if kind == "flux":
            env = os.getenv("COMFY_CHECKPOINT_FLUX")
            preferred = [
                env,
                _CLOUD_DEFAULT_FLUX if self.backend == "cloud" else _LOCAL_DEFAULT_FLUX,
                "sd_xl_base_1.0.safetensors",
                "sd_xl_turbo_1.0_fp16.safetensors",
            ]
            if self.backend == "cloud":
                return env or _CLOUD_DEFAULT_FLUX
            picked = self._pick_from_available(
                [p for p in preferred if p],
                substrings=["flux", "sd_xl", "sdxl", "turbo"],
            )
            result = picked or env or _LOCAL_DEFAULT_FLUX
        else:
            env = os.getenv("COMFY_CHECKPOINT_TURBO")
            preferred = [
                env,
                _CLOUD_DEFAULT_TURBO if self.backend == "cloud" else _LOCAL_DEFAULT_TURBO,
                "sd_xl_turbo_1.0_fp16.safetensors",
                "sd_xl_base_1.0.safetensors",
                "zimage_turbo.safetensors",
            ]
            if self.backend == "cloud":
                return env or _CLOUD_DEFAULT_TURBO
            picked = self._pick_from_available(
                [p for p in preferred if p],
                substrings=["turbo", "sd_xl", "sdxl", "zimage", "z_image"],
            )
            result = picked or env or _LOCAL_DEFAULT_TURBO

        key = f"{kind}:{result}"
        if key not in self._resolved_ckpt_log:
            self._resolved_ckpt_log.add(key)
            print(f"[ComfyUI] Using {kind} checkpoint: {result} (backend={self.backend})")
        return result

    def _local_image_backend(self) -> str:
        """Prefer SDXL-style fused checkpoints when Z-Image split files are absent."""
        forced = os.getenv("COMFY_IMAGE_BACKEND", "").strip().lower()
        if forced:
            return forced
        if self.backend == "cloud":
            return "zimage"
        models = self._available_models or []
        if not models:
            return "sdxl"
        joined = " ".join(m.lower() for m in models)
        has_zimage = "z_image" in joined or "zimage" in joined
        has_qwen = "qwen_3" in joined or "qwen3" in joined
        has_ae = "ae.safetensors" in joined or "/ae." in joined
        if has_zimage and has_qwen and has_ae:
            return "zimage"
        return "sdxl"

    # ── text-to-image (Z-Image Turbo) ─────────────────────────────────────

    def _build_zimage_turbo_workflow(
        self,
        prompt: str,
        width: int = 1024,
        height: int = 576,
        seed: Optional[int] = None,
        cfg_scale: float = 1.0,
        steps: int = 8,
        reference_image_names: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """Official Z-Image Turbo graph (UNET + Qwen CLIP + ae VAE).

        When reference_image_names are provided, uses TextEncodeZImageOmni + VAE
        reference latents so the generated still keeps the uploaded face/identity.
        """
        if os.getenv("COMFY_IMAGE_BACKEND", "").strip().lower() == "sdxl":
            return self._build_sdxl_turbo_workflow(
                prompt,
                width,
                height,
                seed,
                cfg_scale=1.5,
                steps=4,
                reference_image_names=reference_image_names,
            )

        if seed is None:
            seed = int(time.time()) % (2**32)

        unet = os.getenv("COMFY_CHECKPOINT_TURBO", _CLOUD_DEFAULT_ZIMAGE_UNET)
        clip = os.getenv("COMFY_ZIMAGE_CLIP", _CLOUD_DEFAULT_ZIMAGE_CLIP)
        vae = os.getenv("COMFY_ZIMAGE_VAE", _CLOUD_DEFAULT_ZIMAGE_VAE)

        # Local installs may still use a fused checkpoint name without split files.
        if self.backend != "cloud":
            local_backend = self._local_image_backend()
            if local_backend == "sdxl" or (
                unet.endswith(".safetensors")
                and "z_image" not in unet
                and "zimage" not in unet.lower()
            ):
                return self._build_sdxl_turbo_workflow(
                    prompt,
                    width,
                    height,
                    seed,
                    cfg_scale=2.5,
                    steps=4,
                    ckpt=self._default_ckpt("turbo"),
                    reference_image_names=reference_image_names,
                )
            # Remap Z-Image piece names from whatever is installed locally.
            unet = (
                self._pick_from_available(
                    [unet, _CLOUD_DEFAULT_ZIMAGE_UNET, _LOCAL_DEFAULT_TURBO],
                    substrings=["z_image", "zimage"],
                )
                or unet
            )
            clip = (
                self._pick_from_available(
                    [clip, _CLOUD_DEFAULT_ZIMAGE_CLIP],
                    substrings=["qwen_3", "qwen3"],
                )
                or clip
            )
            vae = (
                self._pick_from_available(
                    [vae, _CLOUD_DEFAULT_ZIMAGE_VAE],
                    substrings=["ae.safetensors", "/ae."],
                )
                or vae
            )

        refs = [n for n in (reference_image_names or []) if n][:3]
        workflow: Dict[str, Any] = {
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

        positive_src: Any = ["27", 0]
        if refs:
            # Identity via Omni image inputs only.
            # Do NOT use ReferenceLatent / VAEEncode here — Z-Image packed latents
            # mismatch photo VAE shapes (reshape errors on Cloud).
            identity_prompt = (
                f"{prompt}. The person in the reference photo is the lead singer — "
                f"same face, bone structure, hair, skin tone, age, and distinctive features. "
                f"Do not invent a different person."
            )
            omni_inputs: Dict[str, Any] = {
                "clip": ["30", 0],
                "prompt": identity_prompt,
                "auto_resize_images": True,
                # Intentionally omit `vae` — wiring it triggers latent packing bugs
                # with mismatched reference photo resolutions on Comfy Cloud.
            }
            for i, name in enumerate(refs):
                node_id = str(40 + i)
                workflow[node_id] = {
                    "inputs": {"image": name},
                    "class_type": "LoadImage",
                }
                omni_inputs[f"image{i + 1}"] = [node_id, 0]
            workflow["27"] = {
                "inputs": omni_inputs,
                "class_type": "TextEncodeZImageOmni",
            }
        else:
            workflow["27"] = {
                "inputs": {"text": prompt, "clip": ["30", 0]},
                "class_type": "CLIPTextEncode",
            }

        workflow["33"] = {
            "inputs": {"conditioning": positive_src},
            "class_type": "ConditioningZeroOut",
        }
        workflow["3"] = {
            "inputs": {
                "seed": seed,
                "steps": steps,
                "cfg": cfg_scale,
                "sampler_name": "res_multistep",
                "scheduler": "simple",
                "denoise": 1.0,
                "model": ["11", 0],
                "positive": positive_src,
                "negative": ["33", 0],
                "latent_image": ["13", 0],
            },
            "class_type": "KSampler",
        }
        return workflow

    def _build_sdxl_turbo_workflow(
        self,
        prompt: str,
        width: int = 1024,
        height: int = 576,
        seed: Optional[int] = None,
        cfg_scale: float = 1.5,
        steps: int = 4,
        ckpt: Optional[str] = None,
        reference_image_names: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """SDXL Turbo / fused-checkpoint fallback workflow.

        With a reference still, applies IPAdapter FaceID Plus V2 for identity lock.
        """
        if seed is None:
            seed = int(time.time()) % (2**32)

        ckpt = ckpt or (
            _CLOUD_DEFAULT_TURBO if self.backend == "cloud" else self._default_ckpt("turbo")
        )
        refs = [n for n in (reference_image_names or []) if n][:1]
        workflow: Dict[str, Any] = {
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

        model_src: Any = ["5", 0]
        if refs:
            workflow["20"] = {
                "inputs": {"image": refs[0]},
                "class_type": "LoadImage",
            }
            workflow["21"] = {
                "inputs": {
                    "model": ["5", 0],
                    "preset": "FACEID PLUS V2",
                    "lora_strength": 0.7,
                    "provider": "CUDA",
                },
                "class_type": "IPAdapterUnifiedLoaderFaceID",
            }
            workflow["22"] = {
                "inputs": {
                    "model": ["21", 0],
                    "ipadapter": ["21", 1],
                    "image": ["20", 0],
                    "weight": 0.85,
                    "weight_faceidv2": 0.85,
                    "weight_type": "linear",
                    "combine_embeds": "concat",
                    "start_at": 0.0,
                    "end_at": 1.0,
                    "embeds_scaling": "V only",
                },
                "class_type": "IPAdapterFaceID",
            }
            model_src = ["22", 0]

        workflow["4"] = {
            "inputs": {
                "seed": seed,
                "steps": steps,
                "cfg": cfg_scale,
                "sampler_name": "euler_ancestral",
                "scheduler": "sgm_uniform",
                "denoise": 1.0,
                "model": model_src,
                "positive": ["1", 0],
                "negative": ["2", 0],
                "latent_image": ["3", 0],
            },
            "class_type": "KSampler",
        }
        return workflow

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
        """Native LTX-2.3 image + audio → video (IA2V), local + cloud.

        Aligned with Comfy's official ``video_ltx2_3_ia2v`` single-stage core:
        preprocess image → EmptyLTXVLatentVideo → LTXVImgToVideoInplace,
        encode the song slice with LTXVAudioVAEEncode → ConcatAV → sample.
        CreateVideo always muxes the **user** audio (master-safe), never decoded
        model audio. Defaults target distilled local checkpoints; override via env
        for the full two-stage dev template weights if desired.
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
        steps = int(os.getenv("COMFY_LTX_STEPS", "8"))
        cfg = float(os.getenv("COMFY_LTX_CFG", "1.0"))
        strength = float(os.getenv("COMFY_LTX_I2V_STRENGTH", "0.85"))
        audio_mode = (os.getenv("COMFY_LTX_AUDIO_MODE") or "encode").strip().lower()
        identity_scale = float(os.getenv("COMFY_LTX_AUDIO_IDENTITY", "1.0"))
        sampler_name = os.getenv("COMFY_LTX_SAMPLER", "euler")

        workflow: Dict[str, Any] = {
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
            # Image-locked video latent (official IA2V uses ImgToVideoInplace)
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
                    **(
                        {"bypass": False}
                        if (os.getenv("COMFY_LTX_I2V_NODE") or "inplace").strip().lower()
                        != "condition"
                        else {}
                    ),
                },
                "class_type": (
                    "LTXVImgToVideoConditionOnly"
                    if (os.getenv("COMFY_LTX_I2V_NODE") or "inplace").strip().lower()
                    == "condition"
                    else "LTXVImgToVideoInplace"
                ),
            },
        }

        if audio_mode in ("reference", "ref", "identity"):
            # Legacy distilled path: empty audio latent + ReferenceAudio guidance
            fps_i = max(1, int(round(fps)))
            workflow["32"] = {
                "inputs": {
                    "frames_number": length,
                    "frame_rate": fps_i,
                    "batch_size": 1,
                    "audio_vae": ["12", 0],
                },
                "class_type": "LTXVEmptyLatentAudio",
            }
            workflow["33"] = {
                "inputs": {
                    "video_latent": ["31", 0],
                    "audio_latent": ["32", 0],
                },
                "class_type": "LTXVConcatAVLatent",
            }
            workflow["34"] = {
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
            }
            model_src, pos_src, neg_src = ["34", 0], ["34", 1], ["34", 2]
            latent_src = ["33", 0]
            sampler_name = os.getenv("COMFY_LTX_SAMPLER", "euler_ancestral_cfg_pp")
        else:
            # Official IA2V: encode the music slice into the AV latent
            workflow["32"] = {
                "inputs": {
                    "audio": ["2", 0],
                    "audio_vae": ["12", 0],
                },
                "class_type": "LTXVAudioVAEEncode",
            }
            workflow["33"] = {
                "inputs": {
                    "video_latent": ["31", 0],
                    "audio_latent": ["32", 0],
                },
                "class_type": "LTXVConcatAVLatent",
            }
            model_src, pos_src, neg_src = ["10", 0], ["22", 0], ["22", 1]
            latent_src = ["33", 0]

        workflow.update(
            {
                "40": {
                    "inputs": {
                        "steps": steps,
                        "max_shift": 2.05,
                        "base_shift": 0.95,
                        "stretch": True,
                        "terminal": 0.1,
                        "latent": latent_src,
                    },
                    "class_type": "LTXVScheduler",
                },
                "41": {
                    "inputs": {"sampler_name": sampler_name},
                    "class_type": "KSamplerSelect",
                },
                "42": {
                    "inputs": {"noise_seed": seed},
                    "class_type": "RandomNoise",
                },
                "43": {
                    "inputs": {
                        "model": model_src,
                        "positive": pos_src,
                        "negative": neg_src,
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
                        "latent_image": latent_src,
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
                # Always mux the user's song slice — discard any model audio
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
        )
        return workflow

    def _build_minimax_h3_r2v_workflow(
        self,
        image_name: str,
        audio_name: str,
        prompt: str,
        num_frames: int = 124,
        seed: Optional[int] = None,
        width: int = 768,
        height: int = 448,
        previous_image_name: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Native MiniMax H3 Reference-to-Video (open weights).

        Uses ``MiniMaxH3ReferenceToVideo`` with flat dotted sockets
        (``ref_images.ref_image_0``, ``ref_audios.ref_audio_0``) — nested dicts are
        ignored by Comfy ``/prompt``. Prompt tags are ``<Picture 1>`` / ``<Audio 1>``.
        CreateVideo muxes the **user** audio slice (H3 stereo is discarded).
        Works on local ComfyUI ≥ 0.30 and Comfy Cloud with the same graph.
        """
        if not audio_name:
            raise ValueError("MiniMax H3 R2V requires an audio input")
        if seed is None:
            seed = int(time.time()) % (2**32)

        length = _h3_align_frames(num_frames)
        length = min(length, int(os.getenv("COMFY_H3_MAX_FRAMES", "362")))
        length = _h3_align_frames(length)

        width = max(64, (int(width) // 32) * 32)
        height = max(64, (int(height) // 32) * 32)

        unet = os.getenv("COMFY_H3_UNET", _DEFAULT_H3_UNET)
        clip = os.getenv("COMFY_H3_CLIP", _DEFAULT_H3_CLIP)
        video_vae = os.getenv("COMFY_H3_VIDEO_VAE", _DEFAULT_H3_VIDEO_VAE)
        audio_vae = os.getenv("COMFY_H3_AUDIO_VAE", _DEFAULT_H3_AUDIO_VAE)
        weight_dtype = os.getenv("COMFY_H3_WEIGHT_DTYPE", "default")
        fps = float(os.getenv("COMFY_H3_FPS", "24"))
        steps = int(os.getenv("COMFY_H3_STEPS", "20"))
        sampler_name = os.getenv("COMFY_H3_SAMPLER", "res_multistep")
        scheduler = os.getenv("COMFY_H3_SCHEDULER", "simple")
        ref_image_size = os.getenv("COMFY_H3_REF_IMAGE_SIZE", "match")
        use_lightning = (os.getenv("COMFY_H3_LIGHTNING") or "").strip().lower() in (
            "1",
            "true",
            "yes",
            "on",
        )
        if use_lightning:
            steps = int(os.getenv("COMFY_H3_LIGHTNING_STEPS", "4"))

        # Official R2V prompts address refs with 1-based Picture/Audio tags
        tagged = prompt.strip()
        if "<Picture 1>" not in tagged and "<picture 1>" not in tagged.lower():
            tagged = (
                f"<Picture 1> is the performer identity. "
                f"They sing to camera matching <Audio 1> with accurate lip sync. "
                f"{tagged}"
            )
        elif "<Audio 1>" not in tagged and "<audio 1>" not in tagged.lower():
            tagged = f"{tagged} Match lip sync and performance energy to <Audio 1>."

        workflow: Dict[str, Any] = {
            "1": {"inputs": {"image": image_name}, "class_type": "LoadImage"},
            "2": {"inputs": {"audio": audio_name}, "class_type": "LoadAudio"},
            "10": {
                "inputs": {"unet_name": unet, "weight_dtype": weight_dtype},
                "class_type": "UNETLoader",
            },
            "11": {
                "inputs": {
                    "clip_name": clip,
                    "type": "minimax",
                    "device": "default",
                },
                "class_type": "CLIPLoader",
            },
            "12": {
                "inputs": {"vae_name": video_vae},
                "class_type": "VAELoader",
            },
            "13": {
                "inputs": {"vae_name": audio_vae},
                "class_type": "VAELoader",
            },
        }

        model_ref: Any = ["10", 0]
        if use_lightning:
            lora = os.getenv("COMFY_H3_LIGHTNING_LORA", _DEFAULT_H3_LIGHTNING_LORA)
            workflow["14"] = {
                "inputs": {
                    "model": ["10", 0],
                    "lora_name": lora,
                    "strength_model": float(os.getenv("COMFY_H3_LIGHTNING_STRENGTH", "1.0")),
                },
                "class_type": "LoraLoaderModelOnly",
            }
            model_ref = ["14", 0]

        r2v_inputs: Dict[str, Any] = {
            "clip": ["11", 0],
            "vae": ["12", 0],
            "audio_vae": ["13", 0],
            "prompt": tagged,
            "width": width,
            "height": height,
            "length": length,
            "ref_image_size": ref_image_size,
            # Flat dotted keys required for growable slots on /prompt
            "ref_images.ref_image_0": ["1", 0],
            "ref_audios.ref_audio_0": ["2", 0],
        }
        if previous_image_name:
            workflow["3"] = {
                "inputs": {"image": previous_image_name},
                "class_type": "LoadImage",
            }
            r2v_inputs["ref_images.ref_image_1"] = ["3", 0]

        workflow["20"] = {
            "inputs": r2v_inputs,
            "class_type": "MiniMaxH3ReferenceToVideo",
        }
        workflow.update(
            {
                "30": {
                    "inputs": {"noise_seed": seed},
                    "class_type": "RandomNoise",
                },
                "31": {
                    "inputs": {"sampler_name": sampler_name},
                    "class_type": "KSamplerSelect",
                },
                "32": {
                    "inputs": {
                        "model": model_ref,
                        "scheduler": scheduler,
                        "steps": steps,
                        "denoise": 1.0,
                    },
                    "class_type": "BasicScheduler",
                },
                "33": {
                    "inputs": {
                        "model": model_ref,
                        "conditioning": ["20", 0],
                    },
                    "class_type": "BasicGuider",
                },
                "34": {
                    "inputs": {
                        "noise": ["30", 0],
                        "guider": ["33", 0],
                        "sampler": ["31", 0],
                        "sigmas": ["32", 0],
                        "latent_image": ["20", 1],
                    },
                    "class_type": "SamplerCustomAdvanced",
                },
                "40": {
                    "inputs": {"samples": ["34", 0], "vae": ["12", 0]},
                    "class_type": "VAEDecode",
                },
                # Mux user song slice — do not use VAEDecodeAudio / H3 stereo
                "50": {
                    "inputs": {
                        "images": ["40", 0],
                        "fps": fps,
                        "audio": ["2", 0],
                    },
                    "class_type": "CreateVideo",
                },
                "51": {
                    "inputs": {
                        "video": ["50", 0],
                        "filename_prefix": "vidmuse_h3",
                        "format": "mp4",
                        "codec": "h264",
                    },
                    "class_type": "SaveVideo",
                },
            }
        )
        return workflow

    def _build_minimax_h3_t2v_workflow(
        self,
        audio_name: str,
        prompt: str,
        num_frames: int = 124,
        seed: Optional[int] = None,
        width: int = 768,
        height: int = 448,
    ) -> Dict[str, Any]:
        """MiniMax H3 prompt + audio → video (no singer still).

        Uses ``MiniMaxH3ReferenceToVideo`` with only ``ref_audios.ref_audio_0`` so the
        song slice drives timing/energy while the prompt describes the world/character.
        Same ref2va weights as R2V. CreateVideo muxes the user audio slice.
        """
        if not audio_name:
            raise ValueError("MiniMax H3 T2V requires an audio input")
        if seed is None:
            seed = int(time.time()) % (2**32)

        length = _h3_align_frames(num_frames)
        length = min(length, int(os.getenv("COMFY_H3_MAX_FRAMES", "362")))
        length = _h3_align_frames(length)

        width = max(64, (int(width) // 32) * 32)
        height = max(64, (int(height) // 32) * 32)

        unet = os.getenv("COMFY_H3_UNET", _DEFAULT_H3_UNET)
        clip = os.getenv("COMFY_H3_CLIP", _DEFAULT_H3_CLIP)
        video_vae = os.getenv("COMFY_H3_VIDEO_VAE", _DEFAULT_H3_VIDEO_VAE)
        audio_vae = os.getenv("COMFY_H3_AUDIO_VAE", _DEFAULT_H3_AUDIO_VAE)
        weight_dtype = os.getenv("COMFY_H3_WEIGHT_DTYPE", "default")
        fps = float(os.getenv("COMFY_H3_FPS", "24"))
        steps = int(os.getenv("COMFY_H3_STEPS", "20"))
        sampler_name = os.getenv("COMFY_H3_SAMPLER", "res_multistep")
        scheduler = os.getenv("COMFY_H3_SCHEDULER", "simple")
        ref_image_size = os.getenv("COMFY_H3_REF_IMAGE_SIZE", "match")
        use_lightning = (os.getenv("COMFY_H3_LIGHTNING") or "").strip().lower() in (
            "1",
            "true",
            "yes",
            "on",
        )
        if use_lightning:
            steps = int(os.getenv("COMFY_H3_LIGHTNING_STEPS", "4"))

        tagged = prompt.strip()
        if "<Audio 1>" not in tagged and "<audio 1>" not in tagged.lower():
            tagged = (
                f"{tagged} Match performance energy, rhythm, and lip sync to <Audio 1>. "
                "NO text, NO captions, NO letters, NO watermarks."
            )

        workflow: Dict[str, Any] = {
            "2": {"inputs": {"audio": audio_name}, "class_type": "LoadAudio"},
            "10": {
                "inputs": {"unet_name": unet, "weight_dtype": weight_dtype},
                "class_type": "UNETLoader",
            },
            "11": {
                "inputs": {
                    "clip_name": clip,
                    "type": "minimax",
                    "device": "default",
                },
                "class_type": "CLIPLoader",
            },
            "12": {
                "inputs": {"vae_name": video_vae},
                "class_type": "VAELoader",
            },
            "13": {
                "inputs": {"vae_name": audio_vae},
                "class_type": "VAELoader",
            },
        }

        model_ref: Any = ["10", 0]
        if use_lightning:
            lora = os.getenv("COMFY_H3_LIGHTNING_LORA", _DEFAULT_H3_LIGHTNING_LORA)
            workflow["14"] = {
                "inputs": {
                    "model": ["10", 0],
                    "lora_name": lora,
                    "strength_model": float(os.getenv("COMFY_H3_LIGHTNING_STRENGTH", "1.0")),
                },
                "class_type": "LoraLoaderModelOnly",
            }
            model_ref = ["14", 0]

        workflow["20"] = {
            "inputs": {
                "clip": ["11", 0],
                "vae": ["12", 0],
                "audio_vae": ["13", 0],
                "prompt": tagged,
                "width": width,
                "height": height,
                "length": length,
                "ref_image_size": ref_image_size,
                "ref_audios.ref_audio_0": ["2", 0],
            },
            "class_type": "MiniMaxH3ReferenceToVideo",
        }
        workflow.update(
            {
                "30": {
                    "inputs": {"noise_seed": seed},
                    "class_type": "RandomNoise",
                },
                "31": {
                    "inputs": {"sampler_name": sampler_name},
                    "class_type": "KSamplerSelect",
                },
                "32": {
                    "inputs": {
                        "model": model_ref,
                        "scheduler": scheduler,
                        "steps": steps,
                        "denoise": 1.0,
                    },
                    "class_type": "BasicScheduler",
                },
                "33": {
                    "inputs": {
                        "model": model_ref,
                        "conditioning": ["20", 0],
                    },
                    "class_type": "BasicGuider",
                },
                "34": {
                    "inputs": {
                        "noise": ["30", 0],
                        "guider": ["33", 0],
                        "sampler": ["31", 0],
                        "sigmas": ["32", 0],
                        "latent_image": ["20", 1],
                    },
                    "class_type": "SamplerCustomAdvanced",
                },
                "40": {
                    "inputs": {"samples": ["34", 0], "vae": ["12", 0]},
                    "class_type": "VAEDecode",
                },
                "50": {
                    "inputs": {
                        "images": ["40", 0],
                        "fps": fps,
                        "audio": ["2", 0],
                    },
                    "class_type": "CreateVideo",
                },
                "51": {
                    "inputs": {
                        "video": ["50", 0],
                        "filename_prefix": "vidmuse_h3_t2v",
                        "format": "mp4",
                        "codec": "h264",
                    },
                    "class_type": "SaveVideo",
                },
            }
        )
        return workflow

    def _build_fasth3_i2v_workflow(
        self,
        prompt: str,
        num_frames: int = 124,
        seed: Optional[int] = None,
        width: int = 768,
        height: int = 448,
        image_name: Optional[str] = None,
        last_image_name: Optional[str] = None,
        audio_name: Optional[str] = None,
    ) -> Dict[str, Any]:
        """FastVideo FastH3 8-step I2V / T2V (no Ref2VA).

        Matches Comfy ``video_fastvideo_fasth3_i2v``: MiniMaxH3ImageToVideo +
        MiniMaxH3SigmaShift + BlockSparseAttention, 8-step simple scheduler.
        CreateVideo muxes the **user** audio slice when provided (master-safe);
        otherwise falls back to decoded model audio.
        """
        if seed is None:
            seed = int(time.time()) % (2**32)

        length = _h3_align_frames(num_frames)
        length = min(length, int(os.getenv("COMFY_FASTH3_MAX_FRAMES", "362")))
        length = _h3_align_frames(length)

        width = max(64, (int(width) // 32) * 32)
        height = max(64, (int(height) // 32) * 32)

        unet = os.getenv("COMFY_FASTH3_UNET", _DEFAULT_FASTH3_UNET)
        clip = os.getenv("COMFY_H3_CLIP", _DEFAULT_H3_CLIP)
        video_vae = os.getenv(
            "COMFY_FASTH3_VIDEO_VAE",
            os.getenv("COMFY_H3_VIDEO_VAE", _DEFAULT_FASTH3_VIDEO_VAE),
        )
        audio_vae = os.getenv("COMFY_H3_AUDIO_VAE", _DEFAULT_H3_AUDIO_VAE)
        weight_dtype = os.getenv("COMFY_FASTH3_WEIGHT_DTYPE", "default")
        fps = float(os.getenv("COMFY_FASTH3_FPS", os.getenv("COMFY_H3_FPS", "24")))
        steps = int(os.getenv("COMFY_FASTH3_STEPS", "8"))  # distilled: keep at 8
        sampler_name = os.getenv("COMFY_FASTH3_SAMPLER", "res_multistep")
        scheduler = os.getenv("COMFY_FASTH3_SCHEDULER", "simple")
        sigma_video = float(os.getenv("COMFY_FASTH3_SIGMA_VIDEO", "10"))
        sigma_audio = float(os.getenv("COMFY_FASTH3_SIGMA_AUDIO", "3"))
        vsa_keep = float(os.getenv("COMFY_FASTH3_VSA_KEEP_PERCENT", "10"))
        vsa_start = float(os.getenv("COMFY_FASTH3_VSA_START", "0.2"))
        attention_backend = os.getenv(
            "COMFY_FASTH3_ATTENTION", "comfy kitchen attention"
        )

        tagged = prompt.strip()
        if not tagged:
            tagged = "Cinematic music video motion, expressive performance."

        workflow: Dict[str, Any] = {
            "6": {
                "inputs": {"unet_name": unet, "weight_dtype": weight_dtype},
                "class_type": "UNETLoader",
            },
            "13": {
                "inputs": {
                    "clip_name": clip,
                    "type": "minimax",
                    "device": "default",
                },
                "class_type": "CLIPLoader",
            },
            "11": {
                "inputs": {"vae_name": video_vae},
                "class_type": "VAELoader",
            },
            "24": {
                "inputs": {"vae_name": audio_vae},
                "class_type": "VAELoader",
            },
            "143": {
                "inputs": {
                    "model": ["6", 0],
                    "shift_video": sigma_video,
                    "shift_audio": sigma_audio,
                },
                "class_type": "MiniMaxH3SigmaShift",
            },
            "128": {
                "inputs": {
                    "model": ["143", 0],
                    "attention": attention_backend,
                },
                "class_type": "ModelAttentionBackend",
            },
            "127": {
                "inputs": {
                    "model": ["128", 0],
                    "selection": "vsa",
                    "keep_percent": vsa_keep,
                    "start_percent": vsa_start,
                    "end_percent": 1.0,
                    "dense_blocks": "",
                    "min_tokens": 12288,
                    "extra_tokens": 256,
                    "sink_conditioning": "exact_kv_and_rows",
                    "verbose": False,
                },
                "class_type": "BlockSparseAttention",
            },
            "15": {
                "inputs": {"noise_seed": seed},
                "class_type": "RandomNoise",
            },
            "17": {
                "inputs": {"sampler_name": sampler_name},
                "class_type": "KSamplerSelect",
            },
            "9": {
                "inputs": {
                    "model": ["127", 0],
                    "scheduler": scheduler,
                    "steps": steps,
                    "denoise": 1.0,
                },
                "class_type": "BasicScheduler",
            },
        }

        i2v_inputs: Dict[str, Any] = {
            "clip": ["13", 0],
            "vae": ["11", 0],
            "prompt": tagged,
            "width": width,
            "height": height,
            "length": length,
        }
        if image_name:
            workflow["1"] = {
                "inputs": {"image": image_name},
                "class_type": "LoadImage",
            }
            i2v_inputs["first_frame"] = ["1", 0]
        if last_image_name:
            workflow["3"] = {
                "inputs": {"image": last_image_name},
                "class_type": "LoadImage",
            }
            i2v_inputs["last_frame"] = ["3", 0]

        workflow["104"] = {
            "inputs": i2v_inputs,
            "class_type": "MiniMaxH3ImageToVideo",
        }
        workflow["16"] = {
            "inputs": {
                "model": ["127", 0],
                "conditioning": ["104", 0],
            },
            "class_type": "BasicGuider",
        }
        workflow["14"] = {
            "inputs": {
                "noise": ["15", 0],
                "guider": ["16", 0],
                "sampler": ["17", 0],
                "sigmas": ["9", 0],
                "latent_image": ["104", 1],
            },
            "class_type": "SamplerCustomAdvanced",
        }
        workflow["10"] = {
            "inputs": {"samples": ["14", 0], "vae": ["11", 0]},
            "class_type": "VAEDecode",
        }

        if audio_name:
            workflow["2"] = {
                "inputs": {"audio": audio_name},
                "class_type": "LoadAudio",
            }
            create_audio: Any = ["2", 0]
        else:
            workflow["23"] = {
                "inputs": {"samples": ["14", 0], "vae": ["24", 0]},
                "class_type": "VAEDecodeAudio",
            }
            create_audio = ["23", 0]

        workflow["91"] = {
            "inputs": {
                "images": ["10", 0],
                "fps": fps,
                "audio": create_audio,
            },
            "class_type": "CreateVideo",
        }
        workflow["92"] = {
            "inputs": {
                "video": ["91", 0],
                "filename_prefix": "vidmuse_fasth3",
                "format": "mp4",
                "codec": "h264",
            },
            "class_type": "SaveVideo",
        }
        return workflow

    async def generate_video_fasth3(
        self,
        prompt: str,
        *,
        audio_path: Optional[str] = None,
        image_path: Optional[str] = None,
        last_image_path: Optional[str] = None,
        num_frames: int = 124,
        seed: Optional[int] = None,
        duration: Optional[float] = None,
        width: int = 768,
        height: int = 448,
    ) -> Optional[str]:
        """FastH3 draft clip: I2V (first/last) or T2V. Muxes user audio when given."""
        fps = float(os.getenv("COMFY_FASTH3_FPS", os.getenv("COMFY_H3_FPS", "24")))
        frames = num_frames
        if duration is not None:
            frames = max(5, int(round(float(duration) * fps)))
        frames = _h3_align_frames(frames)
        frames = min(frames, int(os.getenv("COMFY_FASTH3_MAX_FRAMES", "362")))
        frames = _h3_align_frames(frames)

        headers = self._headers()
        async with aiohttp.ClientSession(headers=headers) as session:
            image_name = None
            last_name = None
            audio_name = None
            if image_path and Path(image_path).is_file():
                image_name = await self._upload_input_file(session, image_path, "image")
            if last_image_path and Path(last_image_path).is_file():
                last_name = await self._upload_input_file(
                    session, last_image_path, "image"
                )
            if audio_path and Path(audio_path).is_file():
                audio_name = await self._upload_input_file(session, audio_path, "audio")

        workflow = self._build_fasth3_i2v_workflow(
            prompt=prompt,
            num_frames=frames,
            seed=seed,
            width=width,
            height=height,
            image_name=image_name,
            last_image_name=last_name,
            audio_name=audio_name,
        )
        return await self._dispatch_workflow(workflow, wait_timeout=1200)

    async def generate_video_h3(
        self,
        prompt: str,
        audio_path: str,
        *,
        image_path: Optional[str] = None,
        num_frames: int = 124,
        seed: Optional[int] = None,
        duration: Optional[float] = None,
        width: int = 768,
        height: int = 448,
    ) -> Optional[str]:
        """Render one MiniMax H3 scene: R2V with still, or audio-conditioned T2V."""
        if not audio_path or not Path(audio_path).is_file():
            print("[ComfyUI] H3 requires a local audio_path")
            return None

        fps = float(os.getenv("COMFY_H3_FPS", "24"))
        frames = num_frames
        if duration is not None:
            frames = max(5, int(round(float(duration) * fps)))
        frames = _h3_align_frames(frames)
        frames = min(frames, int(os.getenv("COMFY_H3_MAX_FRAMES", "362")))
        frames = _h3_align_frames(frames)

        headers = self._headers()
        async with aiohttp.ClientSession(headers=headers) as session:
            audio_name = await self._upload_input_file(session, audio_path, "audio")
            if not audio_name:
                print("[ComfyUI] audio upload failed; cannot run H3")
                return None
            image_name = None
            if image_path and Path(image_path).is_file():
                image_name = await self._upload_input_file(session, image_path, "image")

        if image_name:
            h3_prompt = (
                f"{prompt}. <Picture 1> sings to camera matching <Audio 1> with "
                f"accurate lip sync, expressive face, consistent identity. "
                f"NO text, NO captions, NO letters."
            )
            workflow = self._build_minimax_h3_r2v_workflow(
                image_name=image_name,
                audio_name=audio_name,
                prompt=h3_prompt,
                num_frames=frames,
                seed=seed,
                width=width,
                height=height,
            )
        else:
            workflow = self._build_minimax_h3_t2v_workflow(
                audio_name=audio_name,
                prompt=prompt,
                num_frames=frames,
                seed=seed,
                width=width,
                height=height,
            )
        return await self._dispatch_workflow(workflow, wait_timeout=1800)

    def _build_infinitetalk_workflow(
        self,
        image_name: str,
        audio_name: str,
        prompt: str,
        num_frames: int = 81,
        seed: Optional[int] = None,
        width: int = 832,
        height: int = 480,
        previous_frames_name: Optional[str] = None,
        negative_prompt: str = (
            "blurry face, identity drift, morphing features, distorted mouth, "
            "extra limbs, watermark, subtitles, low quality, frozen expression"
        ),
    ) -> Dict[str, Any]:
        """Native Wan2.1 InfiniteTalk: image + audio → face/performance lip-sync video.

        Based on ComfyUI WanInfiniteTalkToVideo (single speaker), matching the
        InfiniteTalk Music template: close-up / head-focused singing performance.
        """
        if not audio_name:
            raise ValueError("InfiniteTalk requires an audio input")
        if seed is None:
            seed = int(time.time()) % (2**32)

        # Wan length is 4n+1
        length = max(5, int(num_frames))
        if (length - 1) % 4 != 0:
            length = ((length - 1) // 4) * 4 + 1

        width = max(16, (int(width) // 16) * 16)
        height = max(16, (int(height) // 16) * 16)

        unet = os.getenv("COMFY_INFINITETALK_UNET", _DEFAULT_INFINITETALK_UNET)
        patch = os.getenv("COMFY_INFINITETALK_PATCH", _DEFAULT_INFINITETALK_PATCH)
        clip_name = os.getenv("COMFY_WAN_CLIP", _DEFAULT_WAN_CLIP)
        vae_name = os.getenv("COMFY_WAN_VAE", _DEFAULT_WAN_VAE)
        clip_vision = os.getenv("COMFY_WAN_CLIP_VISION", _DEFAULT_WAN_CLIP_VISION)
        audio_enc = os.getenv("COMFY_INFINITETALK_AUDIO_ENC", _DEFAULT_INFINITETALK_AUDIO_ENC)
        steps = int(os.getenv("COMFY_INFINITETALK_STEPS", "24"))
        cfg = float(os.getenv("COMFY_INFINITETALK_CFG", "5.0"))
        shift = float(os.getenv("COMFY_INFINITETALK_SHIFT", "8.0"))
        fps = float(os.getenv("COMFY_INFINITETALK_FPS", "25"))
        audio_scale = float(os.getenv("COMFY_INFINITETALK_AUDIO_SCALE", "1.35"))
        motion_frames = int(os.getenv("COMFY_INFINITETALK_MOTION_FRAMES", "9"))

        workflow: Dict[str, Any] = {
            "1": {"inputs": {"image": image_name}, "class_type": "LoadImage"},
            "2": {"inputs": {"audio": audio_name}, "class_type": "LoadAudio"},
            "10": {
                "inputs": {"unet_name": unet, "weight_dtype": "default"},
                "class_type": "UNETLoader",
            },
            "11": {
                "inputs": {"name": patch},
                "class_type": "ModelPatchLoader",
            },
            "12": {
                "inputs": {
                    "clip_name": clip_name,
                    "type": "wan",
                    "device": "default",
                },
                "class_type": "CLIPLoader",
            },
            "13": {
                "inputs": {"vae_name": vae_name},
                "class_type": "VAELoader",
            },
            "14": {
                "inputs": {"clip_name": clip_vision},
                "class_type": "CLIPVisionLoader",
            },
            "15": {
                "inputs": {
                    "clip_vision": ["14", 0],
                    "image": ["1", 0],
                    "crop": "center",
                },
                "class_type": "CLIPVisionEncode",
            },
            "16": {
                "inputs": {"audio_encoder_name": audio_enc},
                "class_type": "AudioEncoderLoader",
            },
            "17": {
                "inputs": {
                    "audio_encoder": ["16", 0],
                    "audio": ["2", 0],
                },
                "class_type": "AudioEncoderEncode",
            },
            "20": {
                "inputs": {"text": prompt, "clip": ["12", 0]},
                "class_type": "CLIPTextEncode",
            },
            "21": {
                "inputs": {"text": negative_prompt, "clip": ["12", 0]},
                "class_type": "CLIPTextEncode",
            },
            "30": {
                "inputs": {
                    "mode": "single_speaker",
                    "model": ["10", 0],
                    "model_patch": ["11", 0],
                    "positive": ["20", 0],
                    "negative": ["21", 0],
                    "vae": ["13", 0],
                    "width": width,
                    "height": height,
                    "length": length,
                    "audio_encoder_output_1": ["17", 0],
                    "motion_frame_count": motion_frames,
                    "audio_scale": audio_scale,
                    "clip_vision_output": ["15", 0],
                    "start_image": ["1", 0],
                },
                "class_type": "WanInfiniteTalkToVideo",
            },
            "31": {
                "inputs": {"model": ["30", 0], "shift": shift},
                "class_type": "ModelSamplingSD3",
            },
            "40": {
                "inputs": {
                    "model": ["31", 0],
                    "seed": seed,
                    "steps": steps,
                    "cfg": cfg,
                    "sampler_name": "uni_pc",
                    "scheduler": "simple",
                    "positive": ["30", 1],
                    "negative": ["30", 2],
                    "latent_image": ["30", 3],
                    "denoise": 1.0,
                },
                "class_type": "KSampler",
            },
            "50": {
                "inputs": {"samples": ["40", 0], "vae": ["13", 0]},
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
                    "filename_prefix": "vidmuse_infinitetalk",
                    "format": "mp4",
                    "codec": "h264",
                },
                "class_type": "SaveVideo",
            },
        }
        if previous_frames_name:
            workflow["3"] = {
                "inputs": {"image": previous_frames_name},
                "class_type": "LoadImage",
            }
            workflow["30"]["inputs"]["previous_frames"] = ["3", 0]
        return workflow

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
        backend: Optional[str] = None,
        previous_frames_path: Optional[str] = None,
    ) -> Optional[str]:
        """Generate image+audio→video via native ComfyUI backends."""
        if not audio_path or not Path(audio_path).is_file():
            print("[ComfyUI] image+audio video requires a local audio_path")
            return None

        backend = (backend or os.getenv("COMFY_VIDEO_BACKEND") or "minimax-h3").strip().lower()
        headers = self._headers()
        async with aiohttp.ClientSession(headers=headers) as session:
            image_name = await self._upload_input_file(session, image_path, "image")
            if not image_name:
                return None
            audio_name = await self._upload_input_file(session, audio_path, "audio")
            if not audio_name:
                print("[ComfyUI] audio upload failed; cannot run A/V i2v")
                return None
            prev_name = None
            if previous_frames_path and Path(previous_frames_path).is_file():
                prev_name = await self._upload_input_file(
                    session, previous_frames_path, "image"
                )

        if backend in ("infinitetalk", "infinite-talk", "wan-infinitetalk", "talk"):
            fps = float(os.getenv("COMFY_INFINITETALK_FPS", "25"))
            frames = num_frames
            if duration is not None:
                frames = max(5, int(round(float(duration) * fps)))
                if (frames - 1) % 4 != 0:
                    frames = ((frames - 1) // 4) * 4 + 1
            frames = min(frames, int(os.getenv("COMFY_INFINITETALK_MAX_FRAMES", "81")))
            if (frames - 1) % 4 != 0:
                frames = ((frames - 1) // 4) * 4 + 1
            # Face/head performance default: portrait 480×832 unless landscape requested
            out_w, out_h = 480, 832
            if width >= height and width >= 640:
                out_w, out_h = 832, 480
            talk_prompt = (
                f"{prompt}, singing to camera, close-up on face and head, "
                f"sings with accurate lip sync to the audio, expressive performance, "
                f"consistent identity"
            )
            workflow = self._build_infinitetalk_workflow(
                image_name=image_name,
                audio_name=audio_name,
                prompt=talk_prompt,
                num_frames=frames,
                seed=seed,
                width=out_w,
                height=out_h,
                previous_frames_name=prev_name,
            )
        elif backend in ("minimax-h3", "minimax_h3", "h3", "minimax"):
            fps = float(os.getenv("COMFY_H3_FPS", "24"))
            frames = num_frames
            if duration is not None:
                frames = max(5, int(round(float(duration) * fps)))
            frames = _h3_align_frames(frames)
            frames = min(frames, int(os.getenv("COMFY_H3_MAX_FRAMES", "362")))
            frames = _h3_align_frames(frames)
            # Native H3 canvas: 768 short edge @ ~16:9 (0.4 MP preview-friendly default)
            out_w = int(os.getenv("COMFY_H3_WIDTH", str(width if width >= 640 else 768)))
            out_h = int(os.getenv("COMFY_H3_HEIGHT", str(height if height >= 360 else 448)))
            if width < height:
                out_w = int(os.getenv("COMFY_H3_WIDTH", "448"))
                out_h = int(os.getenv("COMFY_H3_HEIGHT", "768"))
            h3_prompt = (
                f"{prompt}. <Picture 1> sings to camera matching <Audio 1> with "
                f"accurate lip sync, expressive face, consistent identity."
            )
            workflow = self._build_minimax_h3_r2v_workflow(
                image_name=image_name,
                audio_name=audio_name,
                prompt=h3_prompt,
                num_frames=frames,
                seed=seed,
                width=out_w,
                height=out_h,
                previous_image_name=prev_name,
            )
        elif backend in ("fasth3", "fast-h3", "fast_h3", "fastvideo"):
            fps = float(os.getenv("COMFY_FASTH3_FPS", os.getenv("COMFY_H3_FPS", "24")))
            frames = num_frames
            if duration is not None:
                frames = max(5, int(round(float(duration) * fps)))
            frames = _h3_align_frames(frames)
            frames = min(frames, int(os.getenv("COMFY_FASTH3_MAX_FRAMES", "362")))
            frames = _h3_align_frames(frames)
            out_w = int(os.getenv("COMFY_H3_WIDTH", str(width if width >= 640 else 768)))
            out_h = int(os.getenv("COMFY_H3_HEIGHT", str(height if height >= 360 else 448)))
            workflow = self._build_fasth3_i2v_workflow(
                prompt=prompt,
                num_frames=frames,
                seed=seed,
                width=out_w,
                height=out_h,
                image_name=image_name,
                last_image_name=prev_name,
                audio_name=audio_name,
            )
        elif backend in ("humo", "wan", "wan-humo"):
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
        elif backend in ("ltx23", "ltx-23", "ltx_23", "ltx", "ltx2.3"):
            fps = float(os.getenv("COMFY_LTX_FPS", "24"))
            frames = num_frames if duration is None else max(9, int(round(float(duration) * fps)))
            frames = _ltx_align_frames(frames)
            frames = min(frames, int(os.getenv("COMFY_LTX_MAX_FRAMES", "97")))
            frames = _ltx_align_frames(frames)
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
        else:
            print(f"[ComfyUI] Unknown video backend {backend!r}; trying LTX-2.3 IA2V")
            fps = float(os.getenv("COMFY_LTX_FPS", "24"))
            frames = num_frames if duration is None else max(9, int(round(float(duration) * fps)))
            frames = _ltx_align_frames(frames)
            frames = min(frames, int(os.getenv("COMFY_LTX_MAX_FRAMES", "97")))
            frames = _ltx_align_frames(frames)
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

        path = await self._dispatch_workflow(workflow, wait_timeout=1800)
        # Soft-fallback: H3 → InfiniteTalk → HuMo; others → HuMo
        if not path and backend in ("minimax-h3", "minimax_h3", "h3", "minimax"):
            print("[ComfyUI] MiniMax H3 failed — falling back to InfiniteTalk")
            fps = float(os.getenv("COMFY_INFINITETALK_FPS", "25"))
            frames = max(5, int(round(float(duration or 3.5) * fps)))
            if (frames - 1) % 4 != 0:
                frames = ((frames - 1) // 4) * 4 + 1
            frames = min(frames, int(os.getenv("COMFY_INFINITETALK_MAX_FRAMES", "81")))
            workflow = self._build_infinitetalk_workflow(
                image_name=image_name,
                audio_name=audio_name,
                prompt=f"{prompt}, lip sync singing, consistent identity",
                num_frames=frames,
                seed=seed,
                width=480,
                height=832,
                previous_frames_name=prev_name,
            )
            path = await self._dispatch_workflow(workflow, wait_timeout=1200)
        if not path and backend not in ("humo", "wan", "wan-humo"):
            print(f"[ComfyUI] {backend} failed — falling back to HuMo lip-sync")
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

    @staticmethod
    def _parse_text_output(outputs: Dict[str, Any]) -> Optional[str]:
        """Extract first text/string payload from ComfyUI history/job outputs."""
        if not isinstance(outputs, dict):
            return None

        def from_node(node_out: Any) -> Optional[str]:
            if isinstance(node_out, str) and node_out.strip():
                return node_out.strip()
            if not isinstance(node_out, dict):
                return None
            for key in ("text", "string", "strings", "srt_text"):
                val = node_out.get(key)
                if isinstance(val, list) and val:
                    joined = "\n".join(str(x) for x in val if str(x).strip()).strip()
                    if joined:
                        return joined
                if isinstance(val, str) and val.strip():
                    return val.strip()
            ui = node_out.get("ui") or {}
            if isinstance(ui, dict):
                for key in ("text", "string"):
                    val = ui.get(key)
                    if isinstance(val, list) and val:
                        joined = "\n".join(str(x) for x in val if str(x).strip()).strip()
                        if joined:
                            return joined
            return None

        # Prefer PreviewAny / text nodes; also accept Whisper node keys
        for node_id, node_out in outputs.items():
            text = from_node(node_out)
            if text:
                return text
        return from_node(outputs)

    async def _dispatch_workflow_outputs(
        self,
        workflow: Dict[str, Any],
        wait_timeout: int = 600,
    ) -> Optional[Dict[str, Any]]:
        """Submit workflow and return raw outputs dict (no file download)."""
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
                        print(f"[ComfyUI] submit failed ({resp.status}): {body[:400]}")
                        return None
                    try:
                        data = json.loads(body)
                    except Exception:
                        return None
                    prompt_id = data.get("prompt_id")
                    break
            if not prompt_id:
                return None
            print(f"[ComfyUI] lyrics job submitted: {prompt_id} ({self.backend})")
            if self.backend == "cloud":
                return await self._wait_cloud_job(session, prompt_id, wait_timeout)
            # Local history poll
            start = time.time()
            while time.time() - start < wait_timeout:
                async with session.get(f"{self.url}/history/{prompt_id}") as resp:
                    if resp.status == 200:
                        hist = await resp.json()
                        entry = hist.get(prompt_id) if isinstance(hist, dict) else None
                        if isinstance(entry, dict) and entry.get("outputs"):
                            return entry.get("outputs")
                await asyncio.sleep(1.5)
            return None

    async def transcribe_lyrics(
        self,
        audio_path: str,
        model_size: Optional[str] = None,
        language: str = "English",
    ) -> Dict[str, Any]:
        """Transcribe song lyrics via ComfyUI AudioSpeechToTextWhisper.

        Returns {text, srt, cues} — empty text on failure.
        """
        empty = {"text": "", "srt": "", "cues": []}
        if not audio_path or not Path(audio_path).is_file():
            return empty

        model_size = (model_size or os.getenv("VIDMUSE_WHISPER_SIZE", "base")).strip()
        language = (os.getenv("VIDMUSE_WHISPER_LANGUAGE") or language or "English").strip()
        # Prefer English for music videos unless auto
        if language.lower() in ("auto", "auto-detect"):
            language = "Auto-Detect"

        headers = self._headers()
        async with aiohttp.ClientSession(headers=headers) as session:
            audio_name = await self._upload_input_file(session, audio_path, "audio")
            if not audio_name:
                print("[ComfyUI] lyrics: audio upload failed")
                return empty

        workflow = {
            "1": {"inputs": {"audio": audio_name}, "class_type": "LoadAudio"},
            "2": {
                "inputs": {
                    "audio": ["1", 0],
                    "model_size": model_size,
                    "language": language,
                    "task": "Transcribe",
                    "generate_srt": True,
                    "srt_max_line_len": 80,
                    "srt_max_lines": 2,
                    "srt_max_duration_sec": 6.0,
                },
                "class_type": "AudioSpeechToTextWhisper",
            },
            # Capture plain transcript
            "3": {
                "inputs": {"source": ["2", 0]},
                "class_type": "PreviewAny",
            },
            # Capture SRT track
            "4": {
                "inputs": {"source": ["2", 1]},
                "class_type": "PreviewAny",
            },
        }

        outputs = await self._dispatch_workflow_outputs(workflow, wait_timeout=900)
        if not outputs:
            print("[ComfyUI] lyrics: no outputs")
            return empty

        # Node 3 = full text, node 4 = srt (keys may be string ids)
        text = ""
        srt = ""
        for key, node_out in outputs.items():
            parsed = self._parse_text_output({key: node_out})
            if not parsed:
                continue
            # Heuristic: SRT has --> arrows
            if "-->" in parsed:
                srt = parsed
            elif not text or len(parsed) > len(text):
                text = parsed

        # Fallbacks if heuristic missed
        if not text:
            text = self._parse_text_output(outputs) or ""
        if text and "-->" in text and not srt:
            srt = text
            # strip rough SRT to plain lines
            plain_lines = []
            for line in text.splitlines():
                line = line.strip()
                if not line or line.isdigit() or "-->" in line:
                    continue
                plain_lines.append(line)
            text = "\n".join(plain_lines)

        from server.lyrics import parse_srt

        cues = parse_srt(srt) if srt else []
        print(
            f"[ComfyUI] lyrics: {len(text)} chars, {len(cues)} timed cues"
        )
        return {"text": text.strip(), "srt": srt.strip(), "cues": cues}

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
                        if status in (
                            "failed",
                            "cancelled",
                            "error",
                            "non_retryable_error",
                        ):
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

    def _prepare_identity_ref(self, path: str | Path, size: int = 768) -> Path:
        """Normalize a face still to a square JPEG for Omni (stable sizing)."""
        src = Path(path)
        out_dir = OUTPUT_DIR / "identity_prep"
        out_dir.mkdir(parents=True, exist_ok=True)
        dest = out_dir / f"{src.stem}_id{size}.jpg"
        if dest.is_file() and dest.stat().st_mtime >= src.stat().st_mtime:
            return dest
        try:
            from PIL import Image

            with Image.open(src) as im:
                im = im.convert("RGB")
                w, h = im.size
                side = min(w, h)
                left = (w - side) // 2
                top = (h - side) // 2
                im = im.crop((left, top, left + side, top + side))
                im = im.resize((size, size), Image.Resampling.LANCZOS)
                im.save(dest, format="JPEG", quality=92)
            return dest
        except Exception as e:
            print(f"[ComfyUI] identity prep failed ({e}), using original")
            return src

    def _prepare_performance_portrait(
        self,
        path: str | Path,
        width: int = 480,
        height: int = 832,
    ) -> Path:
        """Crop/pad a still to InfiniteTalk portrait aspect (default 480×832)."""
        src = Path(path)
        out_dir = OUTPUT_DIR / "identity_prep"
        out_dir.mkdir(parents=True, exist_ok=True)
        dest = out_dir / f"{src.stem}_portrait_{width}x{height}.jpg"
        if dest.is_file() and dest.stat().st_mtime >= src.stat().st_mtime:
            return dest
        try:
            from PIL import Image

            with Image.open(src) as im:
                im = im.convert("RGB")
                target_aspect = width / float(height)
                w, h = im.size
                src_aspect = w / float(h)
                if src_aspect > target_aspect:
                    # too wide — crop sides, keep center face
                    new_w = int(h * target_aspect)
                    left = (w - new_w) // 2
                    im = im.crop((left, 0, left + new_w, h))
                else:
                    # too tall — crop top/bottom favoring upper body/face
                    new_h = int(w / target_aspect)
                    top = max(0, (h - new_h) // 5)
                    im = im.crop((0, top, w, top + new_h))
                im = im.resize((width, height), Image.Resampling.LANCZOS)
                im.save(dest, format="JPEG", quality=92)
            return dest
        except Exception as e:
            print(f"[ComfyUI] performance portrait prep failed ({e})")
            return src

    async def generate_image_turbo(
        self,
        prompt: str,
        width: int = 1024,
        height: int = 576,
        seed: Optional[int] = None,
        reference_image_paths: Optional[List[str]] = None,
    ) -> Optional[str]:
        """Generate image using z-image turbo (fast, few steps).

        Pass reference_image_paths to lock identity via TextEncodeZImageOmni.
        FaceID is opt-in only (COMFY_IDENTITY_BACKEND=faceid) — Comfy Cloud often
        lacks the FaceID LoRA / ClipVision weights.
        """
        if self.backend != "cloud":
            await self.probe_available_models()

        ref_names: List[str] = []
        paths = [p for p in (reference_image_paths or []) if p and Path(p).is_file()][:3]
        if paths:
            headers = self._headers()
            async with aiohttp.ClientSession(headers=headers) as session:
                for path in paths:
                    prepared = self._prepare_identity_ref(path)
                    name = await self._upload_input_file(session, prepared, "image")
                    if name:
                        ref_names.append(name)
            if ref_names:
                print(f"[ComfyUI] identity refs uploaded: {ref_names}")
            else:
                print(f"[ComfyUI] WARNING: {len(paths)} ref path(s) on disk but upload failed")

        force_sdxl = (
            os.getenv("COMFY_IMAGE_BACKEND", "").strip().lower() == "sdxl"
            or (self.backend != "cloud" and self._local_image_backend() == "sdxl")
        )
        identity_backend = os.getenv("COMFY_IDENTITY_BACKEND", "").strip().lower()
        prefer_faceid = bool(ref_names) and identity_backend in ("faceid", "sdxl")

        if prefer_faceid or force_sdxl:
            workflow = self._build_sdxl_turbo_workflow(
                prompt,
                width,
                height,
                seed,
                reference_image_names=ref_names or None,
            )
            path = await self._dispatch_workflow(workflow, wait_timeout=180)
            if path:
                return path
            if force_sdxl and self.backend != "cloud":
                print(
                    "[ComfyUI] Local SDXL checkpoint failed — "
                    f"available models: {(self._available_models or [])[:8]}"
                )

        # Primary: Z-Image (+ Omni when refs present)
        workflow = self._build_zimage_turbo_workflow(
            prompt,
            width,
            height,
            seed,
            reference_image_names=ref_names or None,
        )
        path = await self._dispatch_workflow(workflow, wait_timeout=180)
        if path:
            return path

        # Omni failed — do NOT auto-try FaceID on Cloud (missing LoRA/ClipVision).
        # Fall back to plain Z-Image so storyboard frames still render.
        if ref_names:
            print(
                "[ComfyUI] Z-Image Omni identity failed — falling back to text-only Z-Image "
                "(set COMFY_IDENTITY_BACKEND=faceid only if FaceID models are installed)"
            )
            workflow = self._build_zimage_turbo_workflow(
                prompt,
                width,
                height,
                seed,
                reference_image_names=None,
            )
            path = await self._dispatch_workflow(workflow, wait_timeout=180)
            if path:
                return path

        if prefer_faceid and ref_names:
            print("[ComfyUI] retrying SDXL FaceID (explicit COMFY_IDENTITY_BACKEND)")
            workflow = self._build_sdxl_turbo_workflow(
                prompt,
                width,
                height,
                seed,
                reference_image_names=ref_names,
            )
            return await self._dispatch_workflow(workflow, wait_timeout=180)

        # Local / AI Playground: if Z-Image stack is missing, fall back to any fused checkpoint.
        if self.backend != "cloud":
            print("[ComfyUI] Falling back to local fused checkpoint workflow")
            workflow = self._build_sdxl_turbo_workflow(
                prompt,
                width,
                height,
                seed,
                reference_image_names=None,
            )
            return await self._dispatch_workflow(workflow, wait_timeout=180)
        return None

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
                            models = await self.probe_available_models(force=True)
                            result["models"] = models[:100]
                            result["model_count"] = len(models)
                            result["resolved_turbo_ckpt"] = self._default_ckpt("turbo")
                            result["local_image_backend"] = self._local_image_backend()
                            result["video_models"] = self._probe_video_backend_models(models)
                            if self._model_probe_error:
                                result["model_probe_error"] = self._model_probe_error
                            try:
                                async with session.get(f"{self.url}/queue") as queue_resp:
                                    if queue_resp.status == 200:
                                        queue = await queue_resp.json()
                                        result["queue"] = {
                                            "running": len(queue.get("queue_running") or []),
                                            "pending": len(queue.get("queue_pending") or []),
                                        }
                            except Exception:
                                pass
                    elif backend != "cloud":
                        async with session.post(
                            f"{self.url}/prompt",
                            json={"prompt": {}, "client_id": self.client_id},
                        ) as resp2:
                            if resp2.status == 200:
                                result["status"] = "up"
        except Exception as e:
            result["error"] = str(e)

        if backend in ("ai-playground", "local"):
            try:
                from server.hardware_detect.intel_xpu import (
                    detect_intel_xpu,
                    recommended_comfy_url,
                )

                info = detect_intel_xpu()
                result["ai_playground_path"] = (
                    str(info.ai_playground_path) if info.ai_playground_path else None
                )
                result["comfyui_path"] = (
                    str(info.comfyui_path) if info.comfyui_path else None
                )
                result["recommended_url"] = recommended_comfy_url(info)
                if backend == "ai-playground" and not info.comfyui_path:
                    result["start_hint"] = (
                        "Start Intel AI Playground's ComfyUI server, then set "
                        "COMFYUI_URL to its queue (usually http://localhost:8188)."
                    )
            except Exception as detect_exc:
                result["detection_error"] = str(detect_exc)
        return result

    @staticmethod
    def _model_list_has(models: List[str], *needles: str) -> bool:
        lower = [m.lower() for m in models]
        for needle in needles:
            n = needle.lower()
            if any(n in m or Path(m).name.lower() == Path(n).name.lower() for m in lower):
                return True
        return False

    def _probe_video_backend_models(self, models: List[str]) -> Dict[str, Any]:
        """Report which open video backends look installable on local Comfy."""
        h3_unet = os.getenv("COMFY_H3_UNET", _DEFAULT_H3_UNET)
        h3_clip = os.getenv("COMFY_H3_CLIP", _DEFAULT_H3_CLIP)
        h3_vvae = os.getenv("COMFY_H3_VIDEO_VAE", _DEFAULT_H3_VIDEO_VAE)
        h3_avae = os.getenv("COMFY_H3_AUDIO_VAE", _DEFAULT_H3_AUDIO_VAE)
        fasth3_unet = os.getenv("COMFY_FASTH3_UNET", _DEFAULT_FASTH3_UNET)
        fasth3_vvae = os.getenv(
            "COMFY_FASTH3_VIDEO_VAE",
            os.getenv("COMFY_H3_VIDEO_VAE", _DEFAULT_FASTH3_VIDEO_VAE),
        )
        ltx_ckpt = os.getenv("COMFY_LTX_CKPT", _DEFAULT_LTX_CKPT)
        ltx_te = os.getenv("COMFY_LTX_TEXT_ENCODER", _DEFAULT_LTX_TEXT_ENCODER)
        it_unet = os.getenv("COMFY_INFINITETALK_UNET", _DEFAULT_INFINITETALK_UNET)
        it_patch = os.getenv("COMFY_INFINITETALK_PATCH", _DEFAULT_INFINITETALK_PATCH)
        humo_unet = os.getenv("COMFY_HUMO_UNET", _DEFAULT_HUMO_UNET)

        h3_ok = all(
            self._model_list_has(models, name)
            for name in (h3_unet, h3_clip, h3_vvae, h3_avae)
        ) or self._model_list_has(models, "minimax_h3_ref2va")
        fasth3_ok = (
            self._model_list_has(models, fasth3_unet, "fasth3", "fastvideo_fasth3")
            and self._model_list_has(models, h3_clip)
            and (
                self._model_list_has(models, fasth3_vvae, h3_vvae)
                or self._model_list_has(models, "minimax_h3_video_vae")
            )
            and self._model_list_has(models, h3_avae, "minimax_h3_audio_vae")
        )
        ltx_ok = self._model_list_has(models, ltx_ckpt, "ltx-2.3", "ltx_2.3")
        it_ok = self._model_list_has(models, it_patch, "infinitetalk", "infiniteTalk")
        humo_ok = self._model_list_has(models, humo_unet, "humo")

        return {
            "minimax-h3": {
                "ready": h3_ok,
                "requires": [h3_unet, h3_clip, h3_vvae, h3_avae],
                "note": "ComfyUI ≥ 0.30; Ref2VA lip sync. HF Comfy-Org/MiniMax-H3",
            },
            "fasth3": {
                "ready": fasth3_ok,
                "requires": [fasth3_unet, h3_clip, fasth3_vvae, h3_avae],
                "note": (
                    "ComfyUI ≥ 0.36; 8-step I2V/T2V only — no Ref2VA. "
                    "Draft/iteration; keep base H3 for performer lip sync."
                ),
            },
            "ltx23": {
                "ready": ltx_ok,
                "requires": [ltx_ckpt, ltx_te],
                "note": (
                    "Official IA2V-style graph; audio-conditioned mouth motion "
                    "(not phoneme Ref2VA). Distilled fp8 recommended for local/Arc."
                ),
            },
            "infinitetalk": {
                "ready": it_ok or self._model_list_has(models, it_unet),
                "requires": [it_unet, it_patch],
            },
            "humo": {
                "ready": humo_ok,
                "requires": [humo_unet],
            },
            "comfy_url": self.url,
            "backend": self.backend,
        }
