"""LLM backend that routes director prompts through AI Playground Home Agent LAN chat."""

from __future__ import annotations

import os
from typing import Any, Dict, List, Optional

from server.home_agent.client import HomeAgentClient, HomeAgentError
from server.llm.base import LLMBackend


class HomeAgentBackend(LLMBackend):
    """Use Home Agent local-web as a remote director LLM."""

    def __init__(
        self,
        base_url: Optional[str] = None,
        password: Optional[str] = None,
        *,
        timeout: Optional[float] = None,
    ):
        self._url = (
            base_url or os.getenv("HOME_AGENT_URL") or "http://127.0.0.1:8765"
        ).rstrip("/")
        self._password = (
            password
            if password is not None
            else (os.getenv("HOME_AGENT_PASSWORD") or "")
        ).strip()
        self._timeout = float(
            timeout
            if timeout is not None
            else os.getenv("HOME_AGENT_TIMEOUT", "180")
        )
        self._client = HomeAgentClient(
            self._url, self._password, timeout=self._timeout
        )

    @property
    def name(self) -> str:
        return "home-agent"

    @property
    def model_name(self) -> str:
        return "playground-home-agent"

    def configure(self, *, base_url: Optional[str] = None, password: Optional[str] = None) -> None:
        if base_url:
            self._url = base_url.rstrip("/")
            self._client.base_url = self._url
        if password is not None:
            self._password = password.strip()
            self._client.password = self._password
            self._client._cookie = None

    async def chat(
        self,
        messages: List[Dict[str, str]],
        *,
        stream: bool = False,
        **kwargs: Any,
    ) -> str:
        parts: List[str] = [
            "You are the SceneMux music-video director LLM. "
            "Reply with plain text only — no chat fluff, no markdown fences unless asked."
        ]
        for msg in messages:
            role = (msg.get("role") or "user").upper()
            content = (msg.get("content") or "").strip()
            if content:
                parts.append(f"{role}:\n{content}")
        prompt = "\n\n".join(parts)
        try:
            reply = await self._client.chat(prompt, wait_timeout=self._timeout)
        except HomeAgentError:
            raise
        except Exception as exc:
            raise HomeAgentError(str(exc)) from exc
        return reply.text

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
        return await self.chat(messages, stream=False)

    async def list_models(self) -> List[str]:
        return [self.model_name]

    async def health(self) -> Dict[str, Any]:
        try:
            result = await self._client.test_connection()
            return {
                "status": "up",
                "backend": self.name,
                "model": self.model_name,
                "url": self._url,
                "latency_ms": result.get("latency_ms"),
            }
        except Exception as exc:
            return {
                "status": "down",
                "backend": self.name,
                "url": self._url,
                "error": str(exc),
            }
