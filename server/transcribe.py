"""Local lyrics transcription from uploaded audio (faster-whisper).

Comfy Cloud's AudioSpeechToTextWhisper node often requires paid credits
(InsufficientFundsError). Local faster-whisper keeps MP3 → lyrics automatic.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

LOG = logging.getLogger("vidmuse.transcribe")

_model = None
_model_key: Optional[str] = None


def _get_model(size: str):
    global _model, _model_key
    size = (size or "base").strip()
    key = size
    if _model is not None and _model_key == key:
        return _model
    from faster_whisper import WhisperModel

    device = (os.getenv("VIDMUSE_WHISPER_DEVICE") or "cpu").strip().lower()
    compute = (os.getenv("VIDMUSE_WHISPER_COMPUTE") or "int8").strip()
    LOG.info("Loading faster-whisper model=%s device=%s compute=%s", size, device, compute)
    _model = WhisperModel(size, device=device, compute_type=compute)
    _model_key = key
    return _model


def transcribe_audio_local(
    audio_path: str | Path,
    model_size: Optional[str] = None,
    language: Optional[str] = None,
) -> Dict[str, Any]:
    """Transcribe an audio file to {text, srt, cues}.

    Returns empty text on failure (never raises for missing dependency after log).
    """
    empty: Dict[str, Any] = {"text": "", "srt": "", "cues": []}
    path = Path(audio_path)
    if not path.is_file():
        return empty

    size = (model_size or os.getenv("VIDMUSE_WHISPER_SIZE") or "base").strip()
    lang_env = (language or os.getenv("VIDMUSE_WHISPER_LANGUAGE") or "en").strip()
    # Map UI-style names to whisper codes
    lang_map = {
        "english": "en",
        "auto": None,
        "auto-detect": None,
        "german": "de",
        "spanish": "es",
        "french": "fr",
        "italian": "it",
        "japanese": "ja",
        "chinese": "zh",
        "russian": "ru",
        "korean": "ko",
        "portuguese": "pt",
    }
    lang = lang_map.get(lang_env.lower(), lang_env.lower() if len(lang_env) <= 3 else "en")
    if lang_env.lower() in ("auto", "auto-detect"):
        lang = None

    try:
        model = _get_model(size)
    except Exception as e:
        LOG.warning("faster-whisper unavailable: %s", e)
        return empty

    try:
        segments_iter, info = model.transcribe(
            str(path),
            language=lang,
            task="transcribe",
            vad_filter=True,
            vad_parameters=dict(min_silence_duration_ms=400),
            condition_on_previous_text=False,
            beam_size=int(os.getenv("VIDMUSE_WHISPER_BEAM", "3")),
        )
        cues: List[Dict[str, Any]] = []
        plain_lines: List[str] = []
        srt_blocks: List[str] = []
        idx = 1
        prompt_echo = {"music lyrics:", "music lyrics"}
        for seg in segments_iter:
            text = (seg.text or "").strip()
            if not text:
                continue
            # Drop Whisper prompt-echo artifacts
            if text.lower().rstrip(".:") in prompt_echo:
                continue
            cues.append({"start": float(seg.start), "end": float(seg.end), "text": text})
            plain_lines.append(text)
            srt_blocks.append(
                f"{idx}\n{_fmt_ts(seg.start)} --> {_fmt_ts(seg.end)}\n{text}\n"
            )
            idx += 1

        text_out = "\n".join(plain_lines).strip()
        LOG.info(
            "Local Whisper: %d chars, %d cues (lang=%s)",
            len(text_out),
            len(cues),
            getattr(info, "language", lang),
        )
        return {"text": text_out, "srt": "\n".join(srt_blocks), "cues": cues}
    except Exception as e:
        LOG.warning("Local Whisper transcription failed: %s", e)
        return empty


def _fmt_ts(seconds: float) -> str:
    ms = int(round(max(0.0, seconds) * 1000))
    h, rem = divmod(ms, 3600_000)
    m, rem = divmod(rem, 60_000)
    s, ms = divmod(rem, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"
