"""Ollama backend — local daemon or Ollama Cloud (Bearer API key)."""

from __future__ import annotations

import os
from typing import Any, Dict, List, Optional

import requests

from server.llm.base import LLMBackend

OLLAMA_URL = os.getenv("OLLAMA_URL", "http://localhost:11434").rstrip("/")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "qwen2.5:32b")
OLLAMA_API_KEY = os.getenv("OLLAMA_API_KEY")


class CreditExhaustedError(RuntimeError):
    """Raised when Ollama returns auth/credit/quota failures suitable for fallback."""


class OllamaBackend(LLMBackend):
    """Ollama HTTP API (local or https://ollama.com)."""

    def __init__(
        self,
        model: str = OLLAMA_MODEL,
        base_url: Optional[str] = None,
        api_key: Optional[str] = None,
        timeout: int = 120,
    ):
        self._model = model
        self._base_url = (base_url or OLLAMA_URL).rstrip("/")
        self._api_key = api_key if api_key is not None else OLLAMA_API_KEY
        self._timeout = timeout

    @property
    def name(self) -> str:
        return "ollama"

    @property
    def model_name(self) -> str:
        return self._model

    def _headers(self) -> Dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
        return headers

    def _raise_if_credit(self, resp: requests.Response) -> None:
        if resp.status_code in (401, 402, 403, 429):
            raise CreditExhaustedError(f"Ollama {resp.status_code}: {resp.text[:300]}")
        body = (resp.text or "").lower()
        if resp.status_code >= 400 and any(
            k in body for k in ("quota", "credit", "billing", "insufficient", "rate limit")
        ):
            raise CreditExhaustedError(f"Ollama {resp.status_code}: {resp.text[:300]}")

    async def chat(
        self,
        messages: List[Dict[str, str]],
        *,
        stream: bool = False,
        **kwargs: Any,
    ) -> str:
        url = f"{self._base_url}/api/chat"
        payload = {
            "model": self._model,
            "messages": messages,
            "stream": stream,
        }
        resp = requests.post(
            url, headers=self._headers(), json=payload, timeout=self._timeout
        )
        self._raise_if_credit(resp)
        resp.raise_for_status()
        return resp.json()["message"]["content"]

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
        url = f"{self._base_url}/api/tags"
        resp = requests.get(url, headers=self._headers(), timeout=10)
        resp.raise_for_status()
        return [m["name"] for m in resp.json().get("models", [])]

    async def health(self) -> Dict[str, Any]:
        try:
            resp = requests.get(
                f"{self._base_url}/api/tags",
                headers=self._headers(),
                timeout=5,
            )
            if resp.status_code == 200:
                models = [m["name"] for m in resp.json().get("models", [])]
                return {
                    "status": "up",
                    "backend": "ollama",
                    "model": self._model,
                    "url": self._base_url,
                    "auth": bool(self._api_key),
                    "available_models": models,
                }
            return {
                "status": "down",
                "backend": "ollama",
                "error": f"HTTP {resp.status_code}",
                "url": self._base_url,
            }
        except Exception as exc:
            return {
                "status": "down",
                "backend": "ollama",
                "error": str(exc),
                "url": self._base_url,
            }
