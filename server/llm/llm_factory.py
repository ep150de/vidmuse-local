"""Factory for selecting the best available LLM backend.

Priority (local mode):
  1. llama.cpp (GGUF on disk)
  2. Local Ollama
  3. OpenCode Go (if key present)

Priority (cloud mode / VIDMUSE_MODE=cloud):
  1. Ollama Cloud (OLLAMA_API_KEY)
  2. OpenCode Go fallback ONLY when OPENCODE_API_KEY is set

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

# Prefer models that work on Ollama Cloud free/entry tiers.
# gpt-oss:120b and other large tags often require a paid subscription.
DEFAULT_CLOUD_OLLAMA_MODEL = "deepseek-v4-flash"
DEFAULT_LOCAL_OLLAMA_MODEL = "qwen2.5:32b"


class FallbackLLMBackend(LLMBackend):
    """Primary backend with automatic fallback on credit/auth failures."""

    def __init__(self, primary: LLMBackend, fallback: LLMBackend):
        self.primary = primary
        self.fallback = fallback
        self.last_used: Optional[str] = None
        self.used_fallback = False
        self.last_primary_error: Optional[str] = None

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
            self.last_primary_error = None
            return result
        except Exception as primary_exc:
            if not self._should_fallback(primary_exc):
                raise
            self.last_primary_error = str(primary_exc)
            try:
                result = await self.fallback.chat(messages, stream=stream, **kwargs)
                self.last_used = self.fallback.name
                self.used_fallback = True
                return result
            except Exception as fallback_exc:
                raise CreditExhaustedError(
                    f"Primary LLM failed ({primary_exc}); "
                    f"fallback also failed ({fallback_exc}). "
                    f"Try a free-tier Ollama model (e.g. {DEFAULT_CLOUD_OLLAMA_MODEL}) "
                    "via OLLAMA_MODEL in .env, or set OPENCODE_API_KEY / upgrade Ollama Cloud."
                ) from fallback_exc

    @staticmethod
    def _should_fallback(exc: Exception) -> bool:
        if isinstance(exc, CreditExhaustedError):
            return True
        msg = str(exc).lower()
        return any(
            k in msg
            for k in (
                "401",
                "402",
                "403",
                "429",
                "quota",
                "credit",
                "billing",
                "subscription",
                "upgrade for access",
            )
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
            "last_primary_error": self.last_primary_error,
        }


def _mode() -> str:
    return (os.getenv("VIDMUSE_MODE") or "local").strip().lower()


def _opencode_key() -> Optional[str]:
    key = (os.getenv("OPENCODE_API_KEY") or "").strip()
    return key or None


def _want_fallback() -> bool:
    """Fallback only when explicitly enabled AND an OpenCode key exists."""
    if not _opencode_key():
        return False
    raw = (os.getenv("LLM_FALLBACK") or "").strip().lower()
    if raw in ("none", "off", "false", "0"):
        return False
    if raw in ("opencode-go", "opencode", "opencode-zen", "1", "true", "yes"):
        return True
    # Default on in cloud mode when a key is present
    return _mode() == "cloud"


def _ollama_for_mode() -> OllamaBackend:
    mode = _mode()
    url = os.getenv("OLLAMA_URL")
    model = os.getenv("OLLAMA_MODEL")
    if mode == "cloud":
        url = url or "https://ollama.com"
        model = model or DEFAULT_CLOUD_OLLAMA_MODEL
    else:
        url = url or "http://localhost:11434"
        model = model or DEFAULT_LOCAL_OLLAMA_MODEL
    return OllamaBackend(model=model, base_url=url, api_key=os.getenv("OLLAMA_API_KEY"))


def _maybe_wrap_fallback(primary: LLMBackend) -> LLMBackend:
    if not _want_fallback():
        return primary
    key = _opencode_key()
    if not key:
        return primary
    if primary.name == "opencode-go":
        return primary
    return FallbackLLMBackend(primary, OpenCodeGoBackend(api_key=key))


def create_llm_backend() -> Optional[LLMBackend]:
    """Auto-detect the best available backend for the current mode."""
    global _active_backend

    provider = (os.getenv("LLM_PROVIDER") or "").strip().lower()
    mode = _mode()

    # Explicit provider override
    if provider in ("opencode-go", "opencode", "opencode-zen"):
        key = _opencode_key()
        if not key:
            _active_backend = None
            return None
        backend: Optional[LLMBackend] = OpenCodeGoBackend(api_key=key)
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

    # Cloud mode: prefer Ollama Cloud (+ OpenCode fallback when keyed)
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
        backend = _maybe_wrap_fallback(ollama)
        _active_backend = backend
        return backend
    except Exception:
        pass

    key = _opencode_key()
    if key:
        backend = OpenCodeGoBackend(api_key=key)
        _active_backend = backend
        return backend

    return None


def get_active_backend() -> Optional[LLMBackend]:
    return _active_backend


def set_active_backend(backend: LLMBackend) -> None:
    global _active_backend
    _active_backend = backend


def friendly_llm_error(exc: Exception) -> str:
    """Human-readable guidance for common cloud LLM failures."""
    text = str(exc)
    lower = text.lower()
    if "subscription" in lower or "upgrade for access" in lower:
        return (
            f"{text}\n\n"
            f"Your Ollama Cloud plan cannot use the current model. "
            f"In .env set OLLAMA_MODEL={DEFAULT_CLOUD_OLLAMA_MODEL} "
            f"(or another free-tier model), then restart the server. "
            "Optionally add OPENCODE_API_KEY for automatic fallback."
        )
    if "opencode_api_key is not configured" in lower:
        return (
            f"{text}\n\n"
            "LLM_FALLBACK is enabled but OPENCODE_API_KEY is empty. "
            "Either paste an OpenCode key into .env, set LLM_FALLBACK=none, "
            f"or switch OLLAMA_MODEL to a free-tier model like {DEFAULT_CLOUD_OLLAMA_MODEL}."
        )
    return text


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
        key = _opencode_key()
        if key:
            results["opencode-go"] = await OpenCodeGoBackend(api_key=key).health()
        else:
            results["opencode-go"] = {"status": "unconfigured"}
    except Exception:
        results["opencode-go"] = {"status": "unavailable"}

    return results
