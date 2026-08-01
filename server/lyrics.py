"""Lyrics helpers — parse transcriptions and timed SRT for concept/storyboard grounding."""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional


_SRT_BLOCK = re.compile(
    r"(\d+)\s+(\d{2}:\d{2}:\d{2}[,.]\d{3})\s*-->\s*(\d{2}:\d{2}:\d{2}[,.]\d{3})\s+([\s\S]*?)(?=\n\d+\s+\d{2}:|\Z)",
    re.MULTILINE,
)


def _ts_to_seconds(ts: str) -> float:
    ts = ts.replace(",", ".")
    hh, mm, rest = ts.split(":")
    ss = float(rest)
    return int(hh) * 3600 + int(mm) * 60 + ss


def parse_srt(srt_text: str) -> List[Dict[str, Any]]:
    """Parse SRT into [{start, end, text}, ...]."""
    if not srt_text or not srt_text.strip():
        return []
    cues: List[Dict[str, Any]] = []
    for match in _SRT_BLOCK.finditer(srt_text.strip() + "\n"):
        text = re.sub(r"\s+", " ", match.group(4)).strip()
        if not text:
            continue
        cues.append(
            {
                "start": _ts_to_seconds(match.group(2)),
                "end": _ts_to_seconds(match.group(3)),
                "text": text,
            }
        )
    return cues


def lyrics_in_window(
    cues: List[Dict[str, Any]],
    start: float,
    end: float,
) -> str:
    """Join lyric lines overlapping [start, end)."""
    if not cues:
        return ""
    parts: List[str] = []
    for cue in cues:
        if cue["end"] < start or cue["start"] > end:
            continue
        parts.append(str(cue["text"]))
    return " / ".join(parts)


def compact_lyrics(text: str, max_chars: int = 3500) -> str:
    """Normalize and truncate lyrics for LLM context."""
    cleaned = re.sub(r"[ \t]+", " ", (text or "").strip())
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    if len(cleaned) <= max_chars:
        return cleaned
    return cleaned[: max_chars - 20].rstrip() + "\n…[lyrics truncated]"


def infer_line_cues(text: str, duration: float) -> List[Dict[str, Any]]:
    """Create clearly-marked proportional line cues when exact timing is unavailable."""
    lines = [
        re.sub(r"\s+", " ", line).strip()
        for line in (text or "").splitlines()
        if line.strip()
    ]
    if not lines or duration <= 0:
        return []
    weights = [max(1, len(re.findall(r"\w+", line))) for line in lines]
    total_weight = sum(weights)
    cursor = 0.0
    cues: List[Dict[str, Any]] = []
    for i, (line, weight) in enumerate(zip(lines, weights)):
        end = duration if i == len(lines) - 1 else cursor + duration * weight / total_weight
        cues.append(
            {
                "start": cursor,
                "end": end,
                "text": line,
                "timing_source": "proportional-inference",
                "confidence": 0.25,
            }
        )
        cursor = end
    return cues


def title_from_filename(filename: Optional[str]) -> str:
    if not filename:
        return ""
    name = PathName(filename)
    stem = name.rsplit(".", 1)[0] if "." in name else name
    stem = re.sub(r"[_\-]+", " ", stem).strip()
    return stem


def PathName(filename: str) -> str:
    return filename.replace("\\", "/").split("/")[-1]
