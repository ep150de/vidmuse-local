"""OpenCode Go backend — OpenAI-compatible chat completions for LLM fallback."""

from __future__ import annotations

import os
from typing import Any, Dict, List, Optional

import requests

from server.llm.base import LLMBackend
from server.llm.ollama_backend import CreditExhaustedError

# Defaults only — always re-read env in __init__ so dotenv load order is safe.
DEFAULT_OPENCODE_BASE_URL = "https://opencode.ai/zen/go/v1"
DEFAULT_OPENCODE_MODEL = "deepseek-v4-flash"


class OpenCodeGoBackend(LLMBackend):
    """OpenCode Go subscription / Zen gateway via chat completions."""

    def __init__(
        self,
        model: Optional[str] = None,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        timeout: int = 120,
    ):
        self._model = model or os.getenv("OPENCODE_MODEL") or DEFAULT_OPENCODE_MODEL
        self._api_key = (api_key if api_key is not None else os.getenv("OPENCODE_API_KEY") or "").strip() or None
        self._base_url = (
            base_url or os.getenv("OPENCODE_BASE_URL") or DEFAULT_OPENCODE_BASE_URL
        ).rstrip("/")
        self._timeout = timeout

    @property
    def name(self) -> str:
        return "opencode-go"

    @property
    def model_name(self) -> str:
        return self._model

    def _headers(self) -> Dict[str, str]:
        if not self._api_key:
            raise RuntimeError("OPENCODE_API_KEY is not configured")
        return {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self._api_key}",
        }

    def _raise_if_credit(self, resp: requests.Response) -> None:
        if resp.status_code in (401, 402, 403, 429):
            raise CreditExhaustedError(
                f"OpenCode {resp.status_code}: {resp.text[:300]}"
            )
        body = (resp.text or "").lower()
        if resp.status_code >= 400 and any(
            k in body
            for k in ("quota", "credit", "billing", "insufficient", "unauthorized")
        ):
            raise CreditExhaustedError(
                f"OpenCode {resp.status_code}: {resp.text[:300]}"
            )

    async def chat(
        self,
        messages: List[Dict[str, str]],
        *,
        stream: bool = False,
        **kwargs: Any,
    ) -> str:
        resp = requests.post(
            f"{self._base_url}/chat/completions",
            headers=self._headers(),
            json={"model": self._model, "messages": messages, "stream": False},
            timeout=self._timeout,
        )
        self._raise_if_credit(resp)
        resp.raise_for_status()
        data = resp.json()
        choices = data.get("choices") or []
        if not choices:
            raise RuntimeError("OpenCode returned no choices")
        return (choices[0].get("message") or {}).get("content") or ""

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
        if not self._api_key:
            return []
        try:
            resp = requests.get(
                f"{self._base_url}/models",
                headers=self._headers(),
                timeout=10,
            )
            resp.raise_for_status()
            data = resp.json()
            items = data.get("data") or data.get("models") or []
            return [m.get("id") or m.get("name") for m in items if isinstance(m, dict)]
        except Exception:
            return [self._model]

    async def health(self) -> Dict[str, Any]:
        if not self._api_key:
            return {"status": "unconfigured", "backend": "opencode-go"}
        try:
            models = await self.list_models()
            return {
                "status": "up",
                "backend": "opencode-go",
                "model": self._model,
                "available_models": models,
            }
        except Exception as exc:
            return {"status": "down", "backend": "opencode-go", "error": str(exc)}
