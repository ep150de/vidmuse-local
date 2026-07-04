"""llama.cpp backend via llama-cpp-python."""

from __future__ import annotations

import asyncio
import importlib
import json
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

from server.llm.base import LLMBackend, MODELS_DIR

GGUF_EXTENSIONS = {".gguf", ".gguf.bin"}

# ─── helpers ───────────────────────────────────────────────────────────

def _find_gguf_models(directory: Path) -> List[Path]:
    """Find all GGUF model files in *directory* (recursively)."""
    results: List[Path] = []
    if not directory.is_dir():
        return results
    for ext in GGUF_EXTENSIONS:
        results.extend(directory.rglob(f"*{ext}"))
    return results


# ─── backend ───────────────────────────────────────────────────────────

class LlamaCppBackend(LLMBackend):
    """llama.cpp backend using llama-cpp-python."""

    def __init__(
        self,
        model_path: Optional[str] = None,
        n_gpu_layers: int = -1,  # -1 = all layers to GPU
        n_ctx: int = 4096,
        n_batch: int = 512,
        flash_attn: bool = True,
    ):
        self._model_path = model_path
        self._n_gpu_layers = n_gpu_layers
        self._n_ctx = n_ctx
        self._n_batch = n_batch
        self._flash_attn = flash_attn
        self._llama = None  # lazy-init to avoid hard dep
        self._model_name = "llama.cpp (unloaded)"

    # -- property getters ------------------------------------------------

    @property
    def name(self) -> str:
        return "llama.cpp"

    @property
    def model_name(self) -> str:
        return self._model_name

    # -- lazy load -------------------------------------------------------

    def _ensure_model(self):
        if self._llama is not None:
            return
        try:
            from llama_cpp import Llama  # type: ignore
        except ImportError:
            raise RuntimeError(
                "llama-cpp-python not installed. "
                "Install with: pip install llama-cpp-python"
            )

        path = self._model_path
        if not path:
            models = _find_gguf_models(MODELS_DIR)
            if not models:
                raise RuntimeError(
                    "No GGUF models found. Place a .gguf file in the 'models/' "
                    "directory or set GGUF_MODEL_PATH in environment."
                )
            path = str(models[0])

        self._llama = Llama(
            model_path=path,
            n_gpu_layers=self._n_gpu_layers,
            n_ctx=self._n_ctx,
            n_batch=self._n_batch,
            flash_attn=self._flash_attn,
            verbose=False,
        )
        self._model_name = Path(path).name

    # -- LLMBackend contract --------------------------------------------

    async def chat(
        self,
        messages: List[Dict[str, str]],
        *,
        stream: bool = False,
        **kwargs: Any,
    ) -> str:
        self._ensure_model()
        # llama-cpp-python expects format: list of {role, content}
        # Convert to its expected format
        llm_messages = []
        for m in messages:
            role = m.get("role", "user")
            content = m.get("content", "")
            if role == "system":
                llm_messages.append({"role": "system", "content": content})
            elif role == "user":
                llm_messages.append({"role": "user", "content": content})
            elif role == "assistant":
                llm_messages.append({"role": "assistant", "content": content})

        result = await asyncio.get_event_loop().run_in_executor(
            None,
            lambda: self._llama.create_chat_completion(  # type: ignore
                messages=llm_messages,
                stream=stream,
                max_tokens=kwargs.get("max_tokens", 4096),
                temperature=kwargs.get("temperature", 0.7),
            ),
        )
        if isinstance(result, dict):
            return result.get("choices", [{}])[0].get("message", {}).get("content", "")
        # fallback
        return str(result)

    async def generate(
        self,
        prompt: str,
        *,
        system_prompt: Optional[str] = None,
        max_tokens: int = 4096,
        temperature: float = 0.7,
        **kwargs: Any,
    ) -> str:
        self._ensure_model()
        messages: List[Dict[str, str]] = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})
        return await self.chat(messages, max_tokens=max_tokens, temperature=temperature)

    async def list_models(self) -> List[str]:
        return [p.name for p in _find_gguf_models(MODELS_DIR)]

    async def health(self) -> Dict[str, Any]:
        if self._llama is None:
            self._ensure_model()
        return {
            "status": "up",
            "backend": "llama.cpp",
            "model": self._model_name,
            "n_gpu_layers": self._n_gpu_layers,
            "n_ctx": self._n_ctx,
        }


def auto_detect_llama_cpp() -> Optional[LlamaCppBackend]:
    """Try to create a LlamaCppBackend. Returns None if unavailable."""
    # Check import
    try:
        import llama_cpp  # noqa: F401
    except ImportError:
        return None

    # Check GGUF models
    models = _find_gguf_models(MODELS_DIR)
    if not models:
        return None

    model_path = os.getenv("GGUF_MODEL_PATH")
    return LlamaCppBackend(model_path=model_path)
