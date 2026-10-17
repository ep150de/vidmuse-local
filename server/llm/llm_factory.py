"""Factory for selecting the best available LLM backend.

Priority (local mode):
  1. llama.cpp (GGUF on disk)
  2. Local Ollama
  3. OpenCode Go (if key present)

Priority (cloud mode / VIDMUSE_MODE=cloud):
  1. Configured Ollama Cloud model (OLLAMA_MODEL)
  2. Free-tier Ollama Cloud model alternates (subscription 403 → next model)
  3. OpenCode Go (when OPENCODE_API_KEY is set)

Override with LLM_PROVIDER=ollama|llamacpp|opencode-go|home-agent|hermes
"""

from __future__ import annotations

import logging
import os
from typing import Any, Dict, List, Optional

from server.llm.base import LLMBackend
from server.llm.hermes_backend import HermesAgentBackend
from server.llm.home_agent_backend import HomeAgentBackend
from server.llm.llamacpp_backend import auto_detect_llama_cpp
from server.llm.ollama_backend import CreditExhaustedError, OllamaBackend
from server.llm.opencode_backend import OpenCodeGoBackend

LOG = logging.getLogger("vidmuse.llm")

_active_backend: Optional[LLMBackend] = None

# Prefer models that currently work on Ollama Cloud free tier.
# deepseek-v4-flash / deepseek-v4-pro require Pro as of 2026.
DEFAULT_CLOUD_OLLAMA_MODEL = "gemma4:31b"
DEFAULT_LOCAL_OLLAMA_MODEL = "qwen2.5:32b"

# Tried in order after the configured OLLAMA_MODEL fails with subscription/quota.
DEFAULT_CLOUD_OLLAMA_FALLBACKS = (
    "gemma4:31b",
    "gpt-oss:20b",
    "gpt-oss:120b",
    "gemma3:12b",
    "ministral-3:8b",
)


class CascadeLLMBackend(LLMBackend):
    """Try backends in order; advance on credit/auth/subscription failures."""

    def __init__(self, backends: List[LLMBackend]):
        if not backends:
            raise ValueError("CascadeLLMBackend requires at least one backend")
        self.backends = backends
        self.last_used: Optional[str] = None
        self.used_fallback = False
        self.last_errors: List[str] = []

    @property
    def name(self) -> str:
        return "+".join(b.name for b in self.backends)

    @property
    def model_name(self) -> str:
        if self.last_used:
            for b in self.backends:
                label = f"{b.name}/{b.model_name}"
                if label == self.last_used or b.name == self.last_used:
                    return b.model_name
        return self.backends[0].model_name

    @property
    def primary(self) -> LLMBackend:
        return self.backends[0]

    @property
    def fallback(self) -> LLMBackend:
        return self.backends[-1]

    @property
    def last_primary_error(self) -> Optional[str]:
        return self.last_errors[0] if self.last_errors else None

    async def chat(
        self,
        messages: List[Dict[str, str]],
        *,
        stream: bool = False,
        **kwargs: Any,
    ) -> str:
        self.last_errors = []
        last_exc: Optional[Exception] = None

        for i, backend in enumerate(self.backends):
            try:
                result = await backend.chat(messages, stream=stream, **kwargs)
                label = f"{backend.name}/{backend.model_name}"
                self.last_used = label
                self.used_fallback = i > 0
                if i > 0:
                    LOG.info("LLM cascade succeeded on %s after earlier failures", label)
                return result
            except Exception as exc:
                last_exc = exc
                self.last_errors.append(f"{backend.name}/{backend.model_name}: {exc}")
                if i < len(self.backends) - 1 and self._should_fallback(exc):
                    LOG.warning(
                        "LLM %s/%s failed (%s) — trying next backend",
                        backend.name,
                        backend.model_name,
                        exc,
                    )
                    continue
                if i < len(self.backends) - 1:
                    # Non-fallbackable error — still try next if it's a different provider
                    if backend.name != self.backends[i + 1].name:
                        LOG.warning(
                            "LLM %s/%s failed (%s) — trying alternate provider",
                            backend.name,
                            backend.model_name,
                            exc,
                        )
                        continue
                raise

        raise CreditExhaustedError(
            "All LLM backends failed:\n  - "
            + "\n  - ".join(self.last_errors)
            + f"\n\nTry a free-tier Ollama model (e.g. {DEFAULT_CLOUD_OLLAMA_MODEL}) "
            "via OLLAMA_MODEL in .env, or set OPENCODE_API_KEY / upgrade Ollama Cloud."
        ) from last_exc

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
                "unauthorized",
                "payment required",
                "insufficient",
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
        models: List[str] = []
        for backend in self.backends:
            try:
                models = list(dict.fromkeys(models + await backend.list_models()))
            except Exception:
                pass
        return models

    async def health(self) -> Dict[str, Any]:
        chain = []
        for backend in self.backends:
            try:
                chain.append(await backend.health())
            except Exception as exc:
                chain.append({"status": "down", "backend": backend.name, "error": str(exc)})
        return {
            "status": "up",
            "backend": self.name,
            "chain": chain,
            "last_used": self.last_used,
            "used_fallback": self.used_fallback,
            "last_errors": self.last_errors,
        }


# Back-compat alias used by older imports / docs
FallbackLLMBackend = CascadeLLMBackend


def _mode() -> str:
    return (os.getenv("VIDMUSE_MODE") or "local").strip().lower()


def _opencode_key() -> Optional[str]:
    key = (os.getenv("OPENCODE_API_KEY") or "").strip()
    return key or None


def _want_opencode_fallback() -> bool:
    """Include OpenCode in the cascade when keyed and not explicitly disabled."""
    if not _opencode_key():
        return False
    raw = (os.getenv("LLM_FALLBACK") or "").strip().lower()
    if raw in ("none", "off", "false", "0"):
        return False
    if raw in ("opencode-go", "opencode", "opencode-zen", "1", "true", "yes", "auto"):
        return True
    # Default on in cloud mode when a key is present
    return _mode() == "cloud"


def _ollama_url_and_key() -> tuple[str, Optional[str]]:
    mode = _mode()
    url = os.getenv("OLLAMA_URL")
    if mode == "cloud":
        url = url or "https://ollama.com"
    else:
        url = url or "http://localhost:11434"
    key = (os.getenv("OLLAMA_API_KEY") or "").strip() or None
    return url.rstrip("/"), key


def _ollama_for_mode(model: Optional[str] = None) -> OllamaBackend:
    mode = _mode()
    url, key = _ollama_url_and_key()
    chosen = model or os.getenv("OLLAMA_MODEL")
    if not chosen:
        chosen = DEFAULT_CLOUD_OLLAMA_MODEL if mode == "cloud" else DEFAULT_LOCAL_OLLAMA_MODEL
    return OllamaBackend(model=chosen, base_url=url, api_key=key)


def _cloud_fallback_models(primary_model: str) -> List[str]:
    """Ordered free-tier Ollama models to try after the primary fails."""
    raw = (os.getenv("OLLAMA_FALLBACK_MODELS") or "").strip()
    if raw:
        candidates = [m.strip() for m in raw.split(",") if m.strip()]
    else:
        candidates = list(DEFAULT_CLOUD_OLLAMA_FALLBACKS)
    seen = {primary_model.lower()}
    out: List[str] = []
    for m in candidates:
        if m.lower() in seen:
            continue
        seen.add(m.lower())
        out.append(m)
    return out


def _build_cascade(backends: List[LLMBackend]) -> LLMBackend:
    # Deduplicate identical name+model pairs
    unique: List[LLMBackend] = []
    seen: set[str] = set()
    for b in backends:
        key = f"{b.name}:{b.model_name}"
        if key in seen:
            continue
        seen.add(key)
        unique.append(b)
    if len(unique) == 1:
        return unique[0]
    return CascadeLLMBackend(unique)


def _ollama_cascade(primary: Optional[OllamaBackend] = None) -> LLMBackend:
    """Ollama primary + free-tier model alternates + optional OpenCode."""
    primary = primary or _ollama_for_mode()
    backends: List[LLMBackend] = [primary]

    if _mode() == "cloud":
        url, key = _ollama_url_and_key()
        for model in _cloud_fallback_models(primary.model_name):
            backends.append(OllamaBackend(model=model, base_url=url, api_key=key))

    if _want_opencode_fallback():
        key = _opencode_key()
        if key:
            backends.append(OpenCodeGoBackend(api_key=key))

    return _build_cascade(backends)


def _opencode_cascade() -> Optional[LLMBackend]:
    """OpenCode primary with Ollama (and free-model) fallback when OpenCode is out of credits."""
    key = _opencode_key()
    if not key:
        return None
    backends: List[LLMBackend] = [OpenCodeGoBackend(api_key=key)]
    # Prefer Ollama when OpenCode fails — user often has one or the other credited
    if os.getenv("OLLAMA_API_KEY") or _mode() != "cloud":
        backends.append(_ollama_for_mode())
        if _mode() == "cloud":
            url, okey = _ollama_url_and_key()
            for model in _cloud_fallback_models(_ollama_for_mode().model_name):
                backends.append(OllamaBackend(model=model, base_url=url, api_key=okey))
    return _build_cascade(backends)


def _maybe_wrap_fallback(primary: LLMBackend) -> LLMBackend:
    """Legacy helper: wrap a non-Ollama primary with OpenCode, or expand Ollama cascades."""
    if isinstance(primary, OllamaBackend):
        return _ollama_cascade(primary)
    backends: List[LLMBackend] = [primary]
    if _want_opencode_fallback() and primary.name != "opencode-go":
        key = _opencode_key()
        if key:
            backends.append(OpenCodeGoBackend(api_key=key))
    return _build_cascade(backends)


def create_llm_backend() -> Optional[LLMBackend]:
    """Auto-detect the best available backend for the current mode."""
    global _active_backend

    provider = (os.getenv("LLM_PROVIDER") or "").strip().lower()
    mode = _mode()

    # Explicit provider override
    if provider in ("home-agent", "home_agent", "aipg-home-agent"):
        backend = HomeAgentBackend()
        _active_backend = backend
        return backend
    if provider in ("hermes", "hermes-agent", "hermes_agent"):
        backend = HermesAgentBackend()
        _active_backend = backend
        return backend
    if provider in ("opencode-go", "opencode", "opencode-zen"):
        backend = _opencode_cascade()
        _active_backend = backend
        return backend
    if provider in ("ollama", "ollama-local", "ollama-cloud"):
        backend = _ollama_cascade()
        _active_backend = backend
        return backend
    if provider in ("llamacpp", "llama.cpp", "llama-cpp"):
        backend = auto_detect_llama_cpp()
        if backend:
            backend = _maybe_wrap_fallback(backend)
        _active_backend = backend
        return backend

    # Cloud mode: Ollama Cloud cascade (+ OpenCode when keyed)
    if mode == "cloud":
        backend = _ollama_cascade()
        _active_backend = backend
        return backend

    # Local mode: llama.cpp first, then Ollama cascade, then OpenCode
    llama_backend = auto_detect_llama_cpp()
    if llama_backend is not None:
        backend = _maybe_wrap_fallback(llama_backend)
        _active_backend = backend
        return backend

    try:
        backend = _ollama_cascade()
        _active_backend = backend
        return backend
    except Exception:
        pass

    backend = _opencode_cascade()
    _active_backend = backend
    return backend


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
            f"Your Ollama Cloud plan cannot use that model (many large tags need Pro). "
            f"In .env set OLLAMA_MODEL={DEFAULT_CLOUD_OLLAMA_MODEL} "
            f"(or another free-tier model like gpt-oss:20b), then restart the server. "
            "The server also auto-tries free-tier alternates, then OpenCode if keyed."
        )
    if "opencode_api_key is not configured" in lower:
        return (
            f"{text}\n\n"
            "LLM_FALLBACK is enabled but OPENCODE_API_KEY is empty. "
            "Either paste an OpenCode key into .env, set LLM_FALLBACK=none, "
            f"or rely on free-tier Ollama (OLLAMA_MODEL={DEFAULT_CLOUD_OLLAMA_MODEL})."
        )
    if "401" in lower or "unauthorized" in lower:
        return (
            f"{text}\n\n"
            "OpenCode rejected the key (expired / out of credits / invalid). "
            f"Ollama Cloud free models should still work — set OLLAMA_MODEL={DEFAULT_CLOUD_OLLAMA_MODEL} "
            "and ensure OLLAMA_API_KEY is set, then restart."
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

    try:
        ha_url = (os.getenv("HOME_AGENT_URL") or "").strip()
        ha_pwd = (os.getenv("HOME_AGENT_PASSWORD") or "").strip()
        if ha_url and ha_pwd:
            results["home-agent"] = await HomeAgentBackend(
                base_url=ha_url, password=ha_pwd
            ).health()
        elif ha_url:
            results["home-agent"] = {
                "status": "unconfigured",
                "url": ha_url,
                "reason": "HOME_AGENT_PASSWORD missing",
            }
        else:
            results["home-agent"] = {"status": "unconfigured"}
    except Exception as exc:
        results["home-agent"] = {"status": "unavailable", "error": str(exc)}

    return results
