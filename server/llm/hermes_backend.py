"""Hermes Agent LLM backend (local gateway or cloud) for SceneMux director."""

from __future__ import annotations

import logging
import os
from typing import Any, Dict, List, Optional

import aiohttp

from server.llm.base import LLMBackend

LOG = logging.getLogger("vidmuse.hermes")


class HermesAgentBackend(LLMBackend):
    """Chat against a Hermes agent HTTP endpoint.

    Expected local shape (configurable):
      POST {base}/v1/chat/completions  OpenAI-compatible
    or
      POST {base}/api/chat  {"messages":[...]} / {"prompt": "..."}

    Env: HERMES_URL, HERMES_TOKEN, HERMES_TIMEOUT
    """

    def __init__(
        self,
        base_url: Optional[str] = None,
        token: Optional[str] = None,
        *,
        timeout: Optional[float] = None,
        model: Optional[str] = None,
    ):
        self.base_url = (
            base_url or os.getenv("HERMES_URL") or "http://127.0.0.1:8642"
        ).rstrip("/")
        self.token = (token if token is not None else os.getenv("HERMES_TOKEN") or "").strip()
        self.timeout = float(
            timeout if timeout is not None else os.getenv("HERMES_TIMEOUT", "180")
        )
        self._model = model or os.getenv("HERMES_MODEL") or "hermes"

    @property
    def name(self) -> str:
        return "hermes"

    @property
    def model_name(self) -> str:
        return self._model

    def _headers(self) -> Dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        return headers

    async def chat(
        self,
        messages: List[Dict[str, str]],
        *,
        stream: bool = False,
        **kwargs: Any,
    ) -> str:
        timeout = aiohttp.ClientTimeout(total=self.timeout)
        # Prefer OpenAI-compatible completions
        payload = {
            "model": self._model,
            "messages": messages,
            "stream": False,
            "temperature": kwargs.get("temperature", 0.7),
            "max_tokens": kwargs.get("max_tokens", 4096),
        }
        async with aiohttp.ClientSession(timeout=timeout) as session:
            for path in ("/v1/chat/completions", "/api/chat", "/chat"):
                url = f"{self.base_url}{path}"
                try:
                    async with session.post(
                        url, json=payload, headers=self._headers()
                    ) as resp:
                        text = await resp.text()
                        if resp.status >= 400:
                            LOG.debug("Hermes %s → %s %s", path, resp.status, text[:200])
                            continue
                        data = await resp.json(content_type=None)
                        # OpenAI shape
                        choices = data.get("choices") or []
                        if choices:
                            msg = choices[0].get("message") or {}
                            content = msg.get("content") or choices[0].get("text")
                            if content:
                                return str(content).strip()
                        for key in ("reply", "text", "content", "output"):
                            if data.get(key):
                                return str(data[key]).strip()
                except Exception as exc:
                    LOG.debug("Hermes path %s failed: %s", path, exc)
                    continue
        raise RuntimeError(
            f"Hermes agent at {self.base_url} did not return chat content "
            "(is the gateway running? try HERMES_URL)"
        )

    async def generate(
        self,
        prompt: str,
        *,
        system_prompt: Optional[str] = None,
        max_tokens: int = 4096,
        temperature: float = 0.7,
        **kwargs: Any,
    ) -> str:
        messages: List[Dict[str, str]] = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})
        return await self.chat(
            messages, max_tokens=max_tokens, temperature=temperature, **kwargs
        )

    async def list_models(self) -> List[str]:
        return [self._model]

    async def health(self) -> Dict[str, Any]:
        try:
            timeout = aiohttp.ClientTimeout(total=8)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                for path in ("/health", "/v1/models", "/"):
                    try:
                        async with session.get(
                            f"{self.base_url}{path}", headers=self._headers()
                        ) as resp:
                            if resp.status < 500:
                                return {
                                    "status": "up",
                                    "backend": self.name,
                                    "model": self._model,
                                    "url": self.base_url,
                                    "auth": bool(self.token),
                                }
                    except Exception:
                        continue
            return {
                "status": "down",
                "backend": self.name,
                "url": self.base_url,
                "reason": "unreachable",
            }
        except Exception as exc:
            return {
                "status": "down",
                "backend": self.name,
                "url": self.base_url,
                "error": str(exc)[:200],
            }
