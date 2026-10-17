"""HTTP client for AI Playground Home Agent local-web channel.

Protocol (intel/AI-Playground home-agent/channels/local_web.py):
  POST /api/login  {"password"} -> Set-Cookie aipg_local_web
  GET  /api/events (SSE, cookie) — open BEFORE chat so replies are not dropped
  POST /api/chat   {"text", "images"?, "callback"?}
  POST /api/logout
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

import aiohttp

LOG = logging.getLogger("vidmuse.home_agent")

_COOKIE_NAME = "aipg_local_web"


class HomeAgentError(RuntimeError):
    """Home Agent transport / auth failure."""

    def __init__(self, message: str, *, code: str = "error"):
        super().__init__(message)
        self.code = code  # unreachable | auth | protocol | error


@dataclass
class HomeAgentReply:
    text: str = ""
    events: List[Dict[str, Any]] = field(default_factory=list)
    media: List[Dict[str, Any]] = field(default_factory=list)
    correlation_id: str = ""


class HomeAgentClient:
    """Login + SSE + chat against a LAN Home Agent local-web server."""

    def __init__(
        self,
        base_url: Optional[str] = None,
        password: Optional[str] = None,
        *,
        timeout: float = 180.0,
    ):
        self.base_url = (
            base_url or os.getenv("HOME_AGENT_URL") or "http://127.0.0.1:8765"
        ).rstrip("/")
        self.password = (
            password
            if password is not None
            else (os.getenv("HOME_AGENT_PASSWORD") or "")
        ).strip()
        self.timeout = timeout
        self._cookie: Optional[str] = None

    def _headers(self, *, json_body: bool = False) -> Dict[str, str]:
        headers: Dict[str, str] = {}
        if json_body:
            headers["Content-Type"] = "application/json"
        if self._cookie:
            headers["Cookie"] = f"{_COOKIE_NAME}={self._cookie}"
        return headers

    def _store_cookie(self, resp: aiohttp.ClientResponse) -> None:
        raw = resp.headers.getall("Set-Cookie", [])
        for item in raw:
            if _COOKIE_NAME in item:
                # aipg_local_web=TOKEN; HttpOnly; ...
                part = item.split(";", 1)[0]
                if "=" in part:
                    self._cookie = part.split("=", 1)[1].strip()
                    return
        # Fallback: CookieJar
        for cookie in resp.cookies.values():
            if cookie.key == _COOKIE_NAME:
                self._cookie = cookie.value
                return

    @staticmethod
    def _parse_host_port(base_url: str) -> tuple[str, int]:
        from urllib.parse import urlparse

        parsed = urlparse(base_url if "://" in base_url else f"http://{base_url}")
        host = parsed.hostname or ""
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        if not host:
            raise HomeAgentError("Home Agent URL is missing a host", code="protocol")
        return host, int(port)

    async def diagnose(self, connect_timeout: float = 5.0) -> Dict[str, Any]:
        """TCP reachability probe (no password). Used by UI Diagnose."""
        host, port = self._parse_host_port(self.base_url)
        started = time.monotonic()
        tcp_ok = False
        error = ""
        try:
            conn = asyncio.open_connection(host, port)
            reader, writer = await asyncio.wait_for(conn, timeout=connect_timeout)
            tcp_ok = True
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:
                pass
            del reader
        except Exception as exc:
            error = str(exc).strip() or type(exc).__name__
        elapsed = round((time.monotonic() - started) * 1000)
        hint = ""
        if not tcp_ok:
            hint = (
                f"Host {host} did not accept TCP on port {port} from this SceneMux "
                "machine (ping can still succeed). On the Playground PC: enable "
                "Local web → Allow LAN, then open Windows Firewall inbound TCP for "
                f"that port. ChromeOS Linux (Crostini) often needs Tailscale or an "
                f"SSH tunnel; then use http://127.0.0.1:{port}."
            )
        return {
            "status": "ok" if tcp_ok else "unreachable",
            "url": self.base_url,
            "host": host,
            "port": port,
            "tcp_ok": tcp_ok,
            "latency_ms": elapsed,
            "error": error or None,
            "hint": hint or None,
        }

    async def login(self, password: Optional[str] = None) -> None:
        pwd = (password if password is not None else self.password).strip()
        if not pwd:
            raise HomeAgentError(
                "Home Agent password is required (HOME_AGENT_PASSWORD or UI field)",
                code="auth",
            )
        timeout = aiohttp.ClientTimeout(total=30)
        login_url = f"{self.base_url}/api/login"
        try:
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.post(
                    login_url,
                    json={"password": pwd},
                    headers={"Content-Type": "application/json"},
                ) as resp:
                    body = await resp.text()
                    if resp.status == 429:
                        raise HomeAgentError(
                            f"Home Agent login locked out (429): {body[:200]}",
                            code="auth",
                        )
                    if resp.status == 401:
                        raise HomeAgentError(
                            "Home Agent login failed — wrong password",
                            code="auth",
                        )
                    if resp.status >= 400:
                        raise HomeAgentError(
                            f"Home Agent login HTTP {resp.status}: {body[:200]}",
                            code="protocol",
                        )
                    self._store_cookie(resp)
                    if not self._cookie:
                        raise HomeAgentError(
                            "Home Agent login succeeded but no session cookie returned",
                            code="protocol",
                        )
                    self.password = pwd
        except HomeAgentError:
            raise
        except Exception as exc:
            # aiohttp TimeoutError / ClientConnectorError often have empty str()
            detail = str(exc).strip() or type(exc).__name__
            host, port = self._parse_host_port(self.base_url)
            raise HomeAgentError(
                f"Cannot reach Home Agent at {self.base_url} ({detail}). "
                f"TCP to {host}:{port} timed out from this machine — ping can "
                "succeed while the firewall still blocks the local-web port. "
                "On the Playground PC: Local web → Allow LAN + Windows Firewall "
                f"inbound TCP {port}. From ChromeOS Linux, use Tailscale or: "
                f"ssh -N -L {port}:127.0.0.1:{port} user@{host} then Test "
                f"http://127.0.0.1:{port}.",
                code="unreachable",
            ) from exc

    async def logout(self) -> None:
        if not self._cookie:
            return
        try:
            timeout = aiohttp.ClientTimeout(total=10)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                await session.post(
                    f"{self.base_url}/api/logout",
                    json={},
                    headers=self._headers(json_body=True),
                )
        except Exception as exc:
            LOG.debug("Home Agent logout ignored: %s", exc)
        finally:
            self._cookie = None

    async def test_connection(self) -> Dict[str, Any]:
        """Login-only probe for UI Test button."""
        started = time.monotonic()
        await self.login()
        elapsed = round((time.monotonic() - started) * 1000)
        await self.logout()
        return {
            "status": "ok",
            "url": self.base_url,
            "latency_ms": elapsed,
        }

    async def chat(
        self,
        text: str,
        *,
        images: Optional[List[Dict[str, str]]] = None,
        callback: Optional[str] = None,
        correlation_id: Optional[str] = None,
        wait_timeout: Optional[float] = None,
    ) -> HomeAgentReply:
        """Send a message and collect SSE replies until typing stop or timeout."""
        if not self._cookie:
            await self.login()

        corr = correlation_id or uuid.uuid4().hex[:12]
        payload_text = text
        if corr not in text:
            payload_text = f"[id:{corr}]\n{text}"

        wait = wait_timeout if wait_timeout is not None else self.timeout
        reply = HomeAgentReply(correlation_id=corr)
        event_q: asyncio.Queue = asyncio.Queue()
        stop = asyncio.Event()

        async def _sse_reader(session: aiohttp.ClientSession) -> None:
            try:
                async with session.get(
                    f"{self.base_url}/api/events",
                    headers=self._headers(),
                ) as resp:
                    if resp.status == 401:
                        await event_q.put({"_error": "sse_unauthorized"})
                        return
                    if resp.status >= 400:
                        await event_q.put(
                            {"_error": f"sse_http_{resp.status}"}
                        )
                        return
                    buffer = ""
                    async for raw in resp.content:
                        if stop.is_set():
                            break
                        buffer += raw.decode("utf-8", errors="replace")
                        while "\n" in buffer:
                            line, buffer = buffer.split("\n", 1)
                            line = line.strip("\r")
                            if line.startswith("data:"):
                                data = line[5:].strip()
                                if not data:
                                    continue
                                try:
                                    event = json.loads(data)
                                except json.JSONDecodeError:
                                    event = {"action": "reply", "text": data}
                                await event_q.put(event)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                await event_q.put({"_error": str(exc)})

        timeout = aiohttp.ClientTimeout(total=None, sock_connect=30, sock_read=wait + 30)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            sse_task = asyncio.create_task(_sse_reader(session))
            # Give SSE a moment to subscribe before enqueueing chat
            await asyncio.sleep(0.25)

            body: Dict[str, Any] = {"text": payload_text}
            if images:
                body["images"] = images
            if callback:
                body["callback"] = callback

            async with session.post(
                f"{self.base_url}/api/chat",
                json=body,
                headers=self._headers(json_body=True),
            ) as resp:
                if resp.status == 401:
                    # Session expired — one retry
                    self._cookie = None
                    await self.login()
                    async with session.post(
                        f"{self.base_url}/api/chat",
                        json=body,
                        headers=self._headers(json_body=True),
                    ) as resp2:
                        if resp2.status >= 400:
                            stop.set()
                            sse_task.cancel()
                            raise HomeAgentError(
                                f"Home Agent chat HTTP {resp2.status}: "
                                f"{(await resp2.text())[:200]}"
                            )
                elif resp.status >= 400:
                    stop.set()
                    sse_task.cancel()
                    raise HomeAgentError(
                        f"Home Agent chat HTTP {resp.status}: "
                        f"{(await resp.text())[:200]}"
                    )

            deadline = time.monotonic() + wait
            texts: List[str] = []
            saw_typing_start = False
            try:
                while time.monotonic() < deadline:
                    remaining = deadline - time.monotonic()
                    try:
                        event = await asyncio.wait_for(
                            event_q.get(), timeout=min(2.0, max(0.1, remaining))
                        )
                    except asyncio.TimeoutError:
                        if texts and saw_typing_start:
                            # Likely finished if we have text and quiet period
                            if time.monotonic() + 1.5 >= deadline:
                                break
                        continue

                    if "_error" in event:
                        raise HomeAgentError(f"Home Agent SSE: {event['_error']}")

                    reply.events.append(event)
                    action = str(event.get("action") or "")
                    state = str(event.get("state") or "")

                    if action == "typing" and state == "start":
                        saw_typing_start = True

                    chunk = event.get("text")
                    if isinstance(chunk, str) and chunk.strip():
                        if action in ("reply", "editMessage", "") or "text" in event:
                            texts.append(chunk.strip())

                    # Collect media-ish payloads when present
                    for key in ("image", "photo", "video", "audio", "file", "media"):
                        if key in event and event[key]:
                            reply.media.append({key: event[key], "action": action})
                    if event.get("images"):
                        reply.media.append(
                            {"images": event["images"], "action": action}
                        )

                    if action == "typing" and state == "stop" and (
                        texts or reply.media
                    ):
                        break
            finally:
                stop.set()
                sse_task.cancel()
                try:
                    await sse_task
                except (asyncio.CancelledError, Exception):
                    pass

        reply.text = "\n".join(texts).strip()
        if not reply.text and not reply.media:
            raise HomeAgentError(
                "Home Agent returned no reply text or media "
                "(is Home Agent running with LAN local-web enabled?)"
            )
        return reply

    async def save_media_payloads(
        self,
        media: List[Dict[str, Any]],
        dest_dir: Path,
    ) -> List[str]:
        """Best-effort: write base64 image payloads from SSE media to disk."""
        import base64

        dest_dir.mkdir(parents=True, exist_ok=True)
        saved: List[str] = []
        for i, item in enumerate(media):
            images = item.get("images") or []
            if isinstance(item.get("image"), dict):
                images = [item["image"]]
            for j, img in enumerate(images if isinstance(images, list) else []):
                if not isinstance(img, dict):
                    continue
                b64 = img.get("data_base64") or img.get("data") or ""
                if not b64:
                    continue
                mime = str(img.get("mime") or "image/png")
                ext = ".jpg" if "jpeg" in mime or "jpg" in mime else ".png"
                path = dest_dir / f"ha_media_{i}_{j}{ext}"
                try:
                    raw = base64.b64decode(re.sub(r"^data:[^;]+;base64,", "", b64))
                    path.write_bytes(raw)
                    saved.append(str(path))
                except Exception as exc:
                    LOG.warning("Failed to save Home Agent media: %s", exc)
        return saved
