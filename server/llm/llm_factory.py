"""Factory for selecting the best available LLM backend."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from server.llm.base import LLMBackend
from server.llm.llamacpp_backend import auto_detect_llama_cpp
from server.llm.ollama_backend import OllamaBackend

# Global backend instance — set once at startup, read by director.
_active_backend: Optional[LLMBackend] = None


def create_llm_backend() -> Optional[LLMBackend]:
    """Auto-detect the best available backend.

    Priority:
    1. llama.cpp (if GGUF models exist + library installed)
    2. Ollama (if reachable)
    """
    global _active_backend

    # Try llama.cpp first (preferred for speed / local inference)
    llama_backend = auto_detect_llama_cpp()
    if llama_backend is not None:
        _active_backend = llama_backend
        return llama_backend

    # Fallback to Ollama
    try:
        ollama = OllamaBackend()
        if hasattr(ollama, "is_available"):
            import asyncio
            available = asyncio.get_event_loop().run_until_complete(ollama.is_available())
            if available:
                _active_backend = ollama
                return ollama
    except Exception:
        pass

    return None


def get_active_backend() -> Optional[LLMBackend]:
    """Return the currently configured backend, or None."""
    return _active_backend


def set_active_backend(backend: LLMBackend) -> None:
    """Manually set the active backend."""
    global _active_backend
    _active_backend = backend


async def health_check_all() -> Dict[str, Any]:
    """Check health of both backends (non-blocking)."""
    results: Dict[str, Any] = {}

    # llama.cpp
    try:
        backend = auto_detect_llama_cpp()
        if backend:
            results["llama.cpp"] = await backend.health()
    except Exception:
        results["llama.cpp"] = {"status": "unavailable", "reason": "not installed or no GGUF models"}

    # Ollama
    try:
        ollama = OllamaBackend()
        results["ollama"] = await ollama.health()
    except Exception:
        results["ollama"] = {"status": "unavailable", "reason": "connection failed"}

    return results
