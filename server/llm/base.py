"""Abstract base class for LLM backends used by the VidMuse director."""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, Dict, List, Optional

# Directory where GGUF models are stored (relative to server root).
MODELS_DIR = Path(__file__).resolve().parents[1] / "models"


class LLMBackend(ABC):
    """Abstract interface for LLM inference backends."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Human-readable backend name (e.g. 'llama.cpp', 'ollama')."""

    @property
    @abstractmethod
    def model_name(self) -> str:
        """Currently loaded model identifier."""

    @abstractmethod
    async def chat(
        self,
        messages: List[Dict[str, str]],
        *,
        stream: bool = False,
        **kwargs: Any,
    ) -> str:
        """Run inference on the given message list and return text output."""

    @abstractmethod
    async def generate(
        self,
        prompt: str,
        *,
        system_prompt: Optional[str] = None,
        max_tokens: int = 4096,
        temperature: float = 0.7,
        **kwargs: Any,
    ) -> str:
        """High-level convenience for single-turn generation."""

    @abstractmethod
    async def list_models(self) -> List[str]:
        """Return available model identifiers on this backend."""

    @abstractmethod
    async def health(self) -> Dict[str, Any]:
        """Return health status and metadata for this backend."""

    async def is_available(self) -> bool:
        """Quick check — overrides health() to return True if status == 'up'."""
        try:
            status = await self.health()
            return status.get("status") == "up"
        except Exception:
            return False


__all__ = ["LLMBackend", "MODELS_DIR"]
