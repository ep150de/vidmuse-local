"""Factory for selecting the best available LLM backend.

Priority (local mode):
  1. llama.cpp (GGUF on disk)
  2. Local Ollama
  3. OpenCode Go (if key present)

Priority (cloud mode / VIDMUSE_MODE=cloud):
  1. Ollama Cloud (OLLAMA_API_KEY)
  2. OpenCode Go fallback on credit/auth errors (or if primary unavailable)

Override with LLM_PROVIDER=ollama|llamacpp|opencode-go
"""

from __future__ import annotations

import os
from typing import Any, Dict, List, Optional

from server.llm.base import LLMBackend
from server.llm.llamacpp_backend import auto_detect_llama_cpp
from server.llm.ollama_backend import CreditExhaustedError, OllamaBackend
from server.llm.opencode_backend import OpenCodeGoBackend

_active_backend: Optional[LLMBackend] = None


class FallbackLLMBackend(LLMBackend):
    """Primary backend with automatic fallback on credit/auth failures."""

    def __init__(self, primary: LLMBackend, fallback: LLMBackend):
        self.primary = primary
        self.fallback = fallback
        self.last_used: Optional[str] = None
        self.used_fallback = False

    @property
    def name(self) -> str:
        return f"{self.primary.name}+{self.fallback.name}"

    @property
    def model_name(self) -> str:
        if self.used_fallback:
            return self.fallback.model_name
        return self.primary.model_name

    async def chat(
        self,
        messages: List[Dict[str, str]],
        *,
        stream: bool = False,
        **kwargs: Any,
    ) -> str:
        try:
            result = await self.primary.chat(messages, stream=stream, **kwargs)
            self.last_used = self.primary.name
            self.used_fallback = False
            return result
        except CreditExhaustedError:
            result = await self.fallback.chat(messages, stream=stream, **kwargs)
            self.last_used = self.fallback.name
            self.used_fallback = True
            return result
        except Exception as exc:
            msg = str(exc).lower()
            if any(k in msg for k in ("401", "402", "403", "429", "quota", "credit", "billing")):
                result = await self.fallback.chat(messages, stream=stream, **kwargs)
                self.last_used = self.fallback.name
                self.used_fallback = True
                return result
            raise

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
        models = await self.primary.list_models()
        try:
            models = list(dict.fromkeys(models + await self.fallback.list_models()))
        except Exception:
            pass
        return models

    async def health(self) -> Dict[str, Any]:
        return {
            "status": "up",
            "backend": self.name,
            "primary": await self.primary.health(),
            "fallback": await self.fallback.health(),
            "last_used": self.last_used,
            "used_fallback": self.used_fallback,
        }


def _mode() -> str:
    return (os.getenv("VIDMUSE_MODE") or "local").strip().lower()


def _want_fallback() -> bool:
    raw = (os.getenv("LLM_FALLBACK") or "").strip().lower()
    if raw in ("none", "off", "false", "0"):
        return False
    if raw in ("opencode-go", "opencode", "opencode-zen", "1", "true", "yes"):
        return True
    # Default: enable fallback in cloud mode when OpenCode key exists
    return _mode() == "cloud" and bool(os.getenv("OPENCODE_API_KEY"))


def _ollama_for_mode() -> OllamaBackend:
    mode = _mode()
    url = os.getenv("OLLAMA_URL")
    model = os.getenv("OLLAMA_MODEL")
    if mode == "cloud":
        url = url or "https://ollama.com"
        model = model or "gpt-oss:120b"
    else:
        url = url or "http://localhost:11434"
        model = model or "qwen2.5:32b"
    return OllamaBackend(model=model, base_url=url, api_key=os.getenv("OLLAMA_API_KEY"))


def _maybe_wrap_fallback(primary: LLMBackend) -> LLMBackend:
    if not _want_fallback():
        return primary
    if not os.getenv("OPENCODE_API_KEY"):
        return primary
    if primary.name == "opencode-go":
        return primary
    return FallbackLLMBackend(primary, OpenCodeGoBackend())


def create_llm_backend() -> Optional[LLMBackend]:
    """Auto-detect the best available backend for the current mode."""
    global _active_backend

    provider = (os.getenv("LLM_PROVIDER") or "").strip().lower()
    mode = _mode()

    # Explicit provider override
    if provider in ("opencode-go", "opencode", "opencode-zen"):
        backend: Optional[LLMBackend] = OpenCodeGoBackend()
        _active_backend = backend
        return backend
    if provider in ("ollama", "ollama-local", "ollama-cloud"):
        backend = _maybe_wrap_fallback(_ollama_for_mode())
        _active_backend = backend
        return backend
    if provider in ("llamacpp", "llama.cpp", "llama-cpp"):
        backend = auto_detect_llama_cpp()
        if backend:
            backend = _maybe_wrap_fallback(backend)
        _active_backend = backend
        return backend

    # Cloud mode: prefer Ollama Cloud (+ OpenCode fallback)
    if mode == "cloud":
        backend = _maybe_wrap_fallback(_ollama_for_mode())
        _active_backend = backend
        return backend

    # Local mode: llama.cpp first, then Ollama, then OpenCode
    llama_backend = auto_detect_llama_cpp()
    if llama_backend is not None:
        backend = _maybe_wrap_fallback(llama_backend)
        _active_backend = backend
        return backend

    try:
        ollama = _ollama_for_mode()
        # Avoid blocking event-loop anti-pattern; health is best-effort at startup
        backend = _maybe_wrap_fallback(ollama)
        _active_backend = backend
        return backend
    except Exception:
        pass

    if os.getenv("OPENCODE_API_KEY"):
        backend = OpenCodeGoBackend()
        _active_backend = backend
        return backend

    return None


def get_active_backend() -> Optional[LLMBackend]:
    return _active_backend


def set_active_backend(backend: LLMBackend) -> None:
    global _active_backend
    _active_backend = backend


async def health_check_all() -> Dict[str, Any]:
    results: Dict[str, Any] = {"mode": _mode()}

    try:
        backend = auto_detect_llama_cpp()
        if backend:
            results["llama.cpp"] = await backend.health()
        else:
            results["llama.cpp"] = {"status": "unavailable", "reason": "no GGUF / library"}
    except Exception:
        results["llama.cpp"] = {"status": "unavailable", "reason": "not installed"}

    try:
        results["ollama"] = await _ollama_for_mode().health()
    except Exception:
        results["ollama"] = {"status": "unavailable", "reason": "connection failed"}

    try:
        results["opencode-go"] = await OpenCodeGoBackend().health()
    except Exception:
        results["opencode-go"] = {"status": "unavailable"}

    return results
