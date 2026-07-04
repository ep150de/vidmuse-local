"""Ollama backend — keeps the existing Ollama integration."""

from __future__ import annotations

import os
import requests
from typing import Any, Dict, List, Optional

from server.llm.base import LLMBackend

OLLAMA_URL = os.getenv("OLLAMA_URL", "http://localhost:11434")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "qwen2.5:32b")


class OllamaBackend(LLMBackend):
    """llama.cpp backend using llama-cpp-python."""

    def __init__(
        self,
        model: str = OLLAMA_MODEL,
        timeout: int = 120,
    ):
        self._model = model
        self._timeout = timeout

    # -- property getters ------------------------------------------------

    @property
    def name(self) -> str:
        return "ollama"

    @property
    def model_name(self) -> str:
        return self._model

    # -- LLMBackend contract --------------------------------------------

    async def chat(
        self,
        messages: List[Dict[str, str]],
        *,
        stream: bool = False,
        **kwargs: Any,
    ) -> str:
        url = f"{OLLAMA_URL}/api/chat"
        payload = {
            "model": self._model,
            "messages": messages,
            "stream": stream,
        }
        resp = requests.post(url, json=payload, timeout=self._timeout)
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
        url = f"{OLLAMA_URL}/api/tags"
        resp = requests.get(url, timeout=10)
        resp.raise_for_status()
        return [m["name"] for m in resp.json().get("models", [])]

    async def health(self) -> Dict[str, Any]:
        try:
            resp = requests.get(f"{OLLAMA_URL}/api/tags", timeout=5)
            if resp.status_code == 200:
                models = [m["name"] for m in resp.json().get("models", [])]
                return {"status": "up", "backend": "ollama", "model": self._model, "available_models": models}
        except Exception:
            pass
        return {"status": "down", "backend": "ollama", "error": "connection failed"}
