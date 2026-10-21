"""Optional FAL cloud host for draft stills / short clips (FAL_KEY required).

Uses prepaid / trial FAL credits — not a daily free-gen entitlement.
Does not replace Comfy Cloud or Playground as primary production paths.
FAL video host is draft-only (cannot run Hybrid Fast H3 Ref2VA).
SceneMux still owns audio slicing and final mux.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from pathlib import Path
from typing import Any, Dict, Optional
from urllib.parse import urlparse

import aiohttp

LOG = logging.getLogger("scenemux.fal")

# Draft defaults — short clips only (prepaid credits)
_DEFAULT_VIDEO_MODEL = "fal-ai/minimax/video-01-live"
_DEFAULT_IMAGE_MODEL = "fal-ai/flux/schnell"
_MAX_CONCURRENT = int(os.getenv("FAL_MAX_CONCURRENT", "1"))
_SEMAPHORE: Optional[asyncio.Semaphore] = None


def fal_key() -> str:
    return (os.getenv("FAL_KEY") or os.getenv("FAL_API_KEY") or "").strip()


def fal_configured() -> bool:
    return bool(fal_key())


def _semaphore() -> asyncio.Semaphore:
    global _SEMAPHORE
    if _SEMAPHORE is None:
        _SEMAPHORE = asyncio.Semaphore(max(1, _MAX_CONCURRENT))
    return _SEMAPHORE


class FalError(RuntimeError):
    def __init__(self, message: str, *, code: str = "fal"):
        super().__init__(message)
        self.code = code


class FalClient:
    """Minimal FAL queue client (REST) for draft image/video generation."""

    def __init__(self, api_key: Optional[str] = None):
        self.api_key = (api_key or fal_key()).strip()
        if not self.api_key:
            raise FalError(
                "FAL_KEY is not set — add it to .env for the FAL draft host",
                code="config",
            )
        self.base = (os.getenv("FAL_QUEUE_URL") or "https://queue.fal.run").rstrip("/")
        self.video_model = (
            os.getenv("FAL_VIDEO_MODEL") or _DEFAULT_VIDEO_MODEL
        ).strip()
        self.image_model = (
            os.getenv("FAL_IMAGE_MODEL") or _DEFAULT_IMAGE_MODEL
        ).strip()

    def _headers(self) -> Dict[str, str]:
        return {
            "Authorization": f"Key {self.api_key}",
            "Content-Type": "application/json",
        }

    async def health(self) -> Dict[str, Any]:
        if not self.api_key:
            return {
                "status": "unconfigured",
                "ready": False,
                "note": "Set FAL_KEY for optional draft stills/clips (prepaid credits)",
            }
        # Lightweight auth probe — list models is not always public; try a HEAD-ish GET
        try:
            async with aiohttp.ClientSession(headers=self._headers()) as session:
                url = f"{self.base}/{self.video_model}"
                async with session.get(url, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                    # 405/404/200 all mean the key reached FAL
                    if resp.status in (200, 404, 405, 422):
                        return {
                            "status": "up",
                            "ready": True,
                            "video_model": self.video_model,
                            "image_model": self.image_model,
                            "max_concurrent": _MAX_CONCURRENT,
                            "note": "Draft host (prepaid credits); not Hybrid Fast H3 / production",
                        }
                    if resp.status in (401, 403):
                        return {
                            "status": "auth",
                            "ready": False,
                            "error": "FAL_KEY rejected",
                        }
                    body = (await resp.text())[:200]
                    return {
                        "status": "degraded",
                        "ready": False,
                        "http": resp.status,
                        "error": body,
                    }
        except Exception as exc:
            return {"status": "down", "ready": False, "error": str(exc)}

    async def _submit_and_wait(
        self,
        model: str,
        payload: Dict[str, Any],
        *,
        wait_timeout: float = 300,
    ) -> Dict[str, Any]:
        async with _semaphore():
            async with aiohttp.ClientSession(headers=self._headers()) as session:
                submit_url = f"{self.base}/{model}"
                async with session.post(
                    submit_url,
                    json=payload,
                    timeout=aiohttp.ClientTimeout(total=60),
                ) as resp:
                    text = await resp.text()
                    if resp.status >= 400:
                        low = text.lower()
                        credit_hit = any(
                            token in low
                            for token in (
                                "quota",
                                "insufficient credit",
                                "insufficient_credit",
                                "out of credit",
                                "no credits",
                                "balance",
                                "billing",
                                "exhausted",
                                "payment required",
                                "upgrade your",
                            )
                        )
                        rate_hit = "rate limit" in low or "too many requests" in low
                        if credit_hit or resp.status == 402:
                            raise FalError(
                                "FAL credits exhausted or request blocked — "
                                "check https://fal.ai/dashboard",
                                code="quota",
                            )
                        if resp.status in (401, 403) and not credit_hit:
                            raise FalError(
                                f"FAL auth/forbidden ({resp.status}): {text[:240]}",
                                code="auth",
                            )
                        if rate_hit or resp.status == 429:
                            raise FalError(
                                "FAL rate limited — wait and retry",
                                code="rate",
                            )
                        raise FalError(
                            f"FAL submit failed ({resp.status}): {text[:300]}",
                            code="submit",
                        )
                    try:
                        data = await resp.json(content_type=None)
                    except Exception:
                        raise FalError(f"FAL submit non-JSON: {text[:200]}", code="submit")

                # Sync response with result
                if isinstance(data, dict) and (
                    data.get("video") or data.get("images") or data.get("image")
                ):
                    return data

                request_id = (
                    (data or {}).get("request_id")
                    or (data or {}).get("requestId")
                    or ""
                )
                status_url = (data or {}).get("status_url") or (
                    f"{self.base}/{model}/requests/{request_id}/status"
                    if request_id
                    else ""
                )
                result_url = (data or {}).get("response_url") or (
                    f"{self.base}/{model}/requests/{request_id}"
                    if request_id
                    else ""
                )
                if not status_url and not result_url:
                    raise FalError(
                        f"FAL submit missing request id: {str(data)[:200]}",
                        code="submit",
                    )

                deadline = time.time() + wait_timeout
                while time.time() < deadline:
                    async with session.get(
                        status_url or result_url,
                        timeout=aiohttp.ClientTimeout(total=30),
                    ) as st:
                        st_body = await st.json(content_type=None)
                    status = str(
                        (st_body or {}).get("status")
                        or (st_body or {}).get("detail")
                        or ""
                    ).upper()
                    if status in ("COMPLETED", "OK", "SUCCESS"):
                        async with session.get(
                            result_url,
                            timeout=aiohttp.ClientTimeout(total=60),
                        ) as rr:
                            return await rr.json(content_type=None)
                    if status in ("FAILED", "ERROR", "CANCELLED"):
                        raise FalError(
                            f"FAL job failed: {str(st_body)[:300]}",
                            code="failed",
                        )
                    await asyncio.sleep(2.5)

                raise FalError("FAL job timed out", code="timeout")

    async def generate_image(
        self,
        prompt: str,
        *,
        dest: Path,
        width: int = 768,
        height: int = 448,
    ) -> Optional[str]:
        payload = {
            "prompt": prompt,
            "image_size": {"width": width, "height": height},
            "num_images": 1,
        }
        result = await self._submit_and_wait(self.image_model, payload, wait_timeout=180)
        url = _first_media_url(result, keys=("images", "image"))
        if not url:
            LOG.warning("FAL image response had no URL: %s", str(result)[:200])
            return None
        return await _download(url, dest)

    async def generate_video(
        self,
        prompt: str,
        *,
        dest: Path,
        image_path: Optional[str] = None,
        duration: float = 5.0,
        seed: Optional[int] = None,
    ) -> Optional[str]:
        """Short draft clip. Prefer image_url when a still exists."""
        # Cap short drafts to limit spend
        dur = max(2, min(6, int(round(float(duration)))))
        payload: Dict[str, Any] = {
            "prompt": prompt,
            "prompt_optimizer": True,
        }
        if seed is not None:
            payload["seed"] = int(seed)

        # Model-specific fields — MiniMax Live / Hailuo style
        if "minimax" in self.video_model.lower() or "hailuo" in self.video_model.lower():
            payload["duration"] = dur
            if image_path and Path(image_path).is_file():
                # fal accepts data URI for small images
                import base64

                raw = Path(image_path).read_bytes()
                if len(raw) < 4_500_000:
                    mime = "image/png" if image_path.lower().endswith(".png") else "image/jpeg"
                    payload["image_url"] = (
                        f"data:{mime};base64,{base64.b64encode(raw).decode('ascii')}"
                    )
        else:
            payload["duration"] = dur
            if image_path and Path(image_path).is_file():
                import base64

                raw = Path(image_path).read_bytes()
                if len(raw) < 4_500_000:
                    mime = "image/png" if image_path.lower().endswith(".png") else "image/jpeg"
                    payload["image_url"] = (
                        f"data:{mime};base64,{base64.b64encode(raw).decode('ascii')}"
                    )

        result = await self._submit_and_wait(
            self.video_model, payload, wait_timeout=float(os.getenv("FAL_TIMEOUT", "420"))
        )
        url = _first_media_url(result, keys=("video", "videos"))
        if not url:
            LOG.warning("FAL video response had no URL: %s", str(result)[:200])
            return None
        path = await _download(url, dest)
        return path


def _first_media_url(result: Any, keys: tuple) -> Optional[str]:
    if not isinstance(result, dict):
        return None
    for key in keys:
        val = result.get(key)
        if isinstance(val, dict) and val.get("url"):
            return str(val["url"])
        if isinstance(val, list) and val:
            first = val[0]
            if isinstance(first, dict) and first.get("url"):
                return str(first["url"])
            if isinstance(first, str) and first.startswith("http"):
                return first
        if isinstance(val, str) and val.startswith("http"):
            return val
    # Nested under data/output
    for nest in ("data", "output", "result"):
        inner = result.get(nest)
        if isinstance(inner, dict):
            found = _first_media_url(inner, keys)
            if found:
                return found
    return None


async def _download(url: str, dest: Path) -> str:
    dest.parent.mkdir(parents=True, exist_ok=True)
    async with aiohttp.ClientSession() as session:
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=180)) as resp:
            if resp.status >= 400:
                raise FalError(f"FAL download failed ({resp.status})", code="download")
            dest.write_bytes(await resp.read())
    # Ensure extension
    if dest.suffix.lower() not in (".mp4", ".webm", ".mov", ".png", ".jpg", ".jpeg", ".webp"):
        parsed = urlparse(url)
        suffix = Path(parsed.path).suffix or ".mp4"
        alt = dest.with_suffix(suffix)
        dest.rename(alt)
        return str(alt)
    return str(dest)
