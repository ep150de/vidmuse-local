"""Phase-2 agentic job envelopes for Home Agent media tasks."""

from __future__ import annotations

import base64
import json
import logging
import re
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

import aiohttp

from server.home_agent.client import HomeAgentClient, HomeAgentReply

LOG = logging.getLogger("vidmuse.home_agent.jobs")

_DATA_URL_RE = re.compile(
    r"data:(image/(?:png|jpeg|jpg|webp|gif)|video/(?:mp4|webm));base64,([A-Za-z0-9+/=\s]+)",
    re.IGNORECASE,
)
_HTTP_MEDIA_RE = re.compile(
    r"https?://[^\s\"'<>]+\.(?:png|jpe?g|webp|gif|mp4|webm)(?:\?[^\s\"'<>]*)?",
    re.IGNORECASE,
)


def build_job_envelope(
    *,
    job_type: str,
    prompt: str,
    extras: Optional[Dict[str, Any]] = None,
) -> str:
    """Natural-language ask for AI Playground's agentic Home Agent.

    Prefer plain creative/media prompts (Playground tools decide image/LTX/H3).
    A compact JSON hint is appended only when extras need structured fields.
    """
    still_like = job_type in (
        "still",
        "storyboard_still",
        "identity_sheet",
        "image",
    )
    h3_like = job_type in ("h3_r2v", "h3_t2v", "video", "minimax_h3")
    ltx_like = job_type in ("ltx23", "ltx", "i2v")
    if still_like:
        body = (
            "Generate one photorealistic music-video still for this brief. "
            "Attach the finished image in your reply (required — not text-only). "
            "Wide storyboard framing unless the brief says otherwise.\n\n"
            f"{prompt}"
        )
    elif h3_like:
        body = (
            "Animate / lip-sync this performance on the Playground host "
            "(MiniMax H3 Ref2VA when available). Use any attached reference "
            "still and audio. Return the finished MP4 as a video attachment "
            "or a direct file URL — not a description only.\n\n"
            f"{prompt}"
        )
    elif ltx_like:
        body = (
            "Animate this still with LTX image-to-video on the Playground host. "
            "Return the finished MP4 as a chat video attachment.\n\n"
            f"{prompt}"
        )
    else:
        body = (
            "Please complete this SceneMux request on the Playground host. "
            "If you generate media, attach the file in your reply.\n\n"
            f"{prompt}"
        )
    if extras:
        # Light structured hint — agent can ignore keys it does not need
        body += (
            "\n\n(Context for tools — optional JSON)\n```json\n"
            + json.dumps({"scenemux": {"type": job_type, "extras": extras}}, indent=2)
            + "\n```\n"
        )
    return body


def encode_local_images(paths: Optional[List[str]], *, limit: int = 2) -> List[Dict[str, str]]:
    """Encode local files as Home Agent chat image payloads."""
    out: List[Dict[str, str]] = []
    for raw in (paths or [])[:limit]:
        path = Path(raw)
        if not path.is_file():
            continue
        suffix = path.suffix.lower()
        mime = "image/jpeg" if suffix in (".jpg", ".jpeg") else "image/png"
        if suffix == ".webp":
            mime = "image/webp"
        try:
            out.append(
                {
                    "mime": mime,
                    "data_base64": base64.b64encode(path.read_bytes()).decode("ascii"),
                }
            )
        except Exception as exc:
            LOG.warning("Failed to encode ref %s: %s", path, exc)
    return out


def _ext_for_mime(mime: str) -> str:
    m = (mime or "").lower()
    if "mp4" in m:
        return ".mp4"
    if "webm" in m:
        return ".webm"
    if "webp" in m:
        return ".webp"
    if "jpeg" in m or "jpg" in m:
        return ".jpg"
    return ".png"


def _save_b64(dest_dir: Path, b64: str, mime: str, stem: str) -> Optional[str]:
    dest_dir.mkdir(parents=True, exist_ok=True)
    clean = re.sub(r"^data:[^;]+;base64,", "", b64, flags=re.IGNORECASE)
    clean = re.sub(r"\s+", "", clean)
    try:
        raw = base64.b64decode(clean)
    except Exception:
        return None
    if not raw:
        return None
    path = dest_dir / f"{stem}{_ext_for_mime(mime)}"
    path.write_bytes(raw)
    return str(path)


def _collect_media_dicts(reply: HomeAgentReply) -> List[Dict[str, Any]]:
    items: List[Dict[str, Any]] = []
    for item in reply.media or []:
        if isinstance(item.get("image"), dict):
            items.append(item["image"])
        if isinstance(item.get("video"), dict):
            items.append(item["video"])
        if isinstance(item.get("file"), dict):
            items.append(item["file"])
        for img in item.get("images") or []:
            if isinstance(img, dict):
                items.append(img)
        for key in ("image", "photo", "video", "file", "media"):
            val = item.get(key)
            if isinstance(val, str) and val.startswith(("http://", "https://", "data:")):
                items.append({"url": val})
    return items


async def _download_url(url: str, dest_dir: Path, stem: str) -> Optional[str]:
    dest_dir.mkdir(parents=True, exist_ok=True)
    parsed = urlparse(url)
    suffix = Path(parsed.path).suffix or ".bin"
    path = dest_dir / f"{stem}{suffix}"
    try:
        timeout = aiohttp.ClientTimeout(total=120)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(url) as resp:
                if resp.status >= 400:
                    return None
                path.write_bytes(await resp.read())
                return str(path)
    except Exception as exc:
        LOG.warning("Failed to download Home Agent media URL: %s", exc)
        return None


async def save_media_from_reply(
    reply: HomeAgentReply,
    dest_dir: Path,
    *,
    stem: str = "ha_media",
) -> List[str]:
    """Save image/video attachments, data-URLs, and http(s) links from a reply."""
    saved: List[str] = []
    n = 0
    for media in _collect_media_dicts(reply):
        b64 = media.get("data_base64") or media.get("data") or ""
        url = media.get("url") or media.get("uri") or ""
        mime = str(media.get("mime") or media.get("content_type") or "")
        if b64:
            if not mime:
                mime = "video/mp4" if "video" in str(media) else "image/png"
            path = _save_b64(dest_dir, b64, mime, f"{stem}_{n}")
            if path:
                saved.append(path)
                n += 1
        elif isinstance(url, str) and url.startswith(("http://", "https://")):
            path = await _download_url(url, dest_dir, f"{stem}_{n}")
            if path:
                saved.append(path)
                n += 1

    text = reply.text or ""
    if text:
        for i, match in enumerate(_DATA_URL_RE.finditer(text)):
            mime, b64 = match.group(1), match.group(2)
            path = _save_b64(dest_dir, b64, mime, f"{stem}_text_{i}")
            if path:
                saved.append(path)
        if not saved:
            for i, match in enumerate(_HTTP_MEDIA_RE.finditer(text)):
                path = await _download_url(match.group(0), dest_dir, f"{stem}_url_{i}")
                if path:
                    saved.append(path)

    return saved


# Back-compat alias
def save_images_from_reply(
    reply: HomeAgentReply,
    dest_dir: Path,
    *,
    stem: str = "ha_media",
) -> List[str]:
    """Sync wrapper used by older callers — prefer save_media_from_reply."""
    import asyncio

    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None
    if loop and loop.is_running():
        # Best-effort sync path without downloads
        saved: List[str] = []
        n = 0
        for media in _collect_media_dicts(reply):
            b64 = media.get("data_base64") or media.get("data") or ""
            if not b64:
                continue
            mime = str(media.get("mime") or "image/png")
            path = _save_b64(dest_dir, b64, mime, f"{stem}_{n}")
            if path:
                saved.append(path)
                n += 1
        return saved
    return asyncio.run(save_media_from_reply(reply, dest_dir, stem=stem))


async def run_media_job(
    client: HomeAgentClient,
    *,
    job_type: str,
    prompt: str,
    dest_dir: Path,
    extras: Optional[Dict[str, Any]] = None,
    images: Optional[List[Dict[str, str]]] = None,
    wait_timeout: Optional[float] = None,
) -> Dict[str, Any]:
    text = build_job_envelope(job_type=job_type, prompt=prompt, extras=extras)
    reply: HomeAgentReply = await client.chat(
        text,
        images=images,
        wait_timeout=wait_timeout,
    )
    saved = await save_media_from_reply(reply, dest_dir, stem=f"ha_{job_type}")
    if not saved:
        LOG.warning(
            "Home Agent %s job returned no media (events=%d, text_len=%d)",
            job_type,
            len(reply.events),
            len(reply.text or ""),
        )
    return {
        "job_type": job_type,
        "reply_text": reply.text,
        "saved_paths": saved,
        "event_count": len(reply.events),
        "correlation_id": reply.correlation_id,
    }
