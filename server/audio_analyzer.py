"""Audio analysis module for VidMuse Local.

Extracts tempo, beats, energy profile, spectral features, and segment structure
from audio files using librosa.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List

import numpy as np

# MUST run before importing librosa. SciPy 1.15+ removed scipy.signal.hann.
import scipy.signal  # noqa: F401
import scipy.signal.windows as _windows

scipy.signal.hann = getattr(scipy.signal, "hann", None) or _windows.hann  # type: ignore[attr-defined]
scipy.signal.hanning = getattr(scipy.signal, "hanning", None) or _windows.hann  # type: ignore[attr-defined]

import librosa


def _as_float(value: Any) -> float:
    """librosa may return a 0-d / 1-d numpy array for tempo."""
    return float(np.asarray(value).reshape(-1)[0])


class AudioAnalyzer:
    """Analyze audio tracks for music video timing."""

    @staticmethod
    def analyze(audio_path: str | Path) -> Dict[str, Any]:
        """Analyze an audio file and return timing/energy data.

        Returns a dict with:
            duration, tempo, beat_count, beat_times, energy_profile,
            brightness, segments, file_format
        """
        # Re-assert shim in case another import rebound scipy.signal
        if not hasattr(scipy.signal, "hann"):
            scipy.signal.hann = _windows.hann  # type: ignore[attr-defined]

        audio_path = Path(audio_path)
        y, sr = librosa.load(str(audio_path), sr=None, mono=True)
        duration = float(librosa.get_duration(y=y, sr=sr))

        # trim=False avoids librosa's __trim_beats path that calls scipy.signal.hann
        tempo, beat_frames = librosa.beat.beat_track(y=y, sr=sr, trim=False)
        beat_times = librosa.frames_to_time(beat_frames, sr=sr).tolist()

        rms = librosa.feature.rms(y=y)[0]
        beat_energy: List[float] = [
            float(np.mean(rms[max(0, f - 5) : f + 5]))
            for f in beat_frames
        ]

        spectral_centroid = librosa.feature.spectral_centroid(y=y, sr=sr)[0]

        # Coarse musical acts (for concept LLM)
        segment_duration = duration / 4
        segments: List[Dict[str, Any]] = [
            {"name": "intro", "start": 0, "end": segment_duration},
            {"name": "verse", "start": segment_duration, "end": segment_duration * 2},
            {"name": "chorus", "start": segment_duration * 2, "end": segment_duration * 3},
            {"name": "outro", "start": segment_duration * 3, "end": duration},
        ]

        segment_energies: List[float] = []
        for seg in segments:
            start_frame = int(seg["start"] * sr / 512)
            end_frame = int(seg["end"] * sr / 512)
            end_frame = max(end_frame, start_frame + 1)
            seg_energy = float(np.mean(rms[start_frame:end_frame]))
            segment_energies.append(seg_energy)

        max_seg_idx = int(np.argmax(segment_energies))
        segments[max_seg_idx]["name"] = "chorus"

        # Dense scene plan spanning the FULL song (storyboard + edit timeline)
        scenes = AudioAnalyzer.build_scene_plan(
            duration=duration,
            beat_times=beat_times,
            segments=segments,
            tempo=_as_float(tempo),
        )

        return {
            "duration": duration,
            "tempo": _as_float(tempo),
            "beat_count": len(beat_times),
            "beat_times": beat_times,
            "energy_profile": beat_energy,
            "brightness": float(np.mean(spectral_centroid)),
            "segments": segments,
            "scenes": scenes,
            "file_format": audio_path.suffix,
        }

    @staticmethod
    def build_scene_plan(
        duration: float,
        beat_times: List[float],
        segments: List[Dict[str, Any]],
        tempo: float = 120.0,
    ) -> List[Dict[str, Any]]:
        """Build timed scenes covering the entire track.

        Env:
          VIDMUSE_SCENE_SECONDS — target seconds per scene (default 6)
          VIDMUSE_MAX_SCENES — hard cap (default 48) to control cloud cost
          VIDMUSE_MIN_SCENES — floor (default 8)
        """
        import os

        scene_sec = float(os.getenv("VIDMUSE_SCENE_SECONDS", "6"))
        max_scenes = int(os.getenv("VIDMUSE_MAX_SCENES", "48"))
        min_scenes = int(os.getenv("VIDMUSE_MIN_SCENES", "8"))

        duration = max(1.0, float(duration))
        scene_sec = max(2.0, scene_sec)

        # Prefer ~1 scene every N seconds, snap to nearby beats when available
        ideal = max(min_scenes, int(np.ceil(duration / scene_sec)))
        count = min(max_scenes, ideal)
        if count < 1:
            count = 1

        # Evenly spaced anchors, then pull toward nearest beat
        anchors = [i * duration / count for i in range(count)]
        if beat_times:
            snaps = []
            for a in anchors:
                nearest = min(beat_times, key=lambda b: abs(b - a))
                # Only snap if within half a scene
                snaps.append(nearest if abs(nearest - a) <= scene_sec * 0.6 else a)
            anchors = snaps
        anchors = sorted(set(max(0.0, min(duration - 0.05, a)) for a in anchors))
        if anchors[0] > 0.15:
            anchors.insert(0, 0.0)
        if anchors[-1] < duration - 0.5:
            anchors.append(duration)
        # Deduplicate again after bounds
        cleaned: List[float] = []
        for a in sorted(anchors):
            if not cleaned or a - cleaned[-1] >= 1.0:
                cleaned.append(a)
        if cleaned[-1] < duration:
            cleaned.append(duration)
        # Cap scene count by merging if still over max
        while len(cleaned) - 1 > max_scenes:
            # drop every other interior boundary
            cleaned = [cleaned[0]] + cleaned[2:-1:2] + [cleaned[-1]]

        def _label(t: float) -> str:
            for seg in segments:
                if seg["start"] <= t < seg["end"] or (
                    abs(t - seg["end"]) < 1e-3 and seg is segments[-1]
                ):
                    return str(seg["name"])
            return "bridge"

        scenes: List[Dict[str, Any]] = []
        for i in range(len(cleaned) - 1):
            start = cleaned[i]
            end = cleaned[i + 1]
            if end <= start:
                continue
            scenes.append(
                {
                    "index": len(scenes),
                    "name": _label(start),
                    "start": float(start),
                    "end": float(end),
                    "duration": float(end - start),
                }
            )

        if not scenes:
            scenes = [
                {
                    "index": 0,
                    "name": "full",
                    "start": 0.0,
                    "end": duration,
                    "duration": duration,
                }
            ]
        return scenes

    @staticmethod
    def build_coverage_timeline(
        duration: float,
        lyric_cues: List[Dict[str, Any]],
        energy_profile: List[float] | None = None,
    ) -> List[Dict[str, Any]]:
        """Build contiguous lyric/instrumental coverage across the complete song."""
        duration = max(0.0, float(duration))
        cues: List[Dict[str, Any]] = []
        for cue in sorted(lyric_cues or [], key=lambda c: float(c.get("start") or 0)):
            start = max(0.0, min(duration, float(cue.get("start") or 0)))
            end = max(start, min(duration, float(cue.get("end") or start)))
            text = str(cue.get("text") or "").strip()
            if end > start and text:
                cues.append({"start": start, "end": end, "text": text})

        timeline: List[Dict[str, Any]] = []
        cursor = 0.0

        def add_interval(
            start: float,
            end: float,
            kind: str,
            text: str = "",
        ) -> None:
            if end - start <= 0.01:
                return
            midpoint = (start + end) / 2
            if duration > 0 and energy_profile:
                idx = min(
                    len(energy_profile) - 1,
                    max(0, int((midpoint / duration) * len(energy_profile))),
                )
                energy = float(energy_profile[idx])
                peak = max(float(x) for x in energy_profile) or 1.0
                normalized = min(1.0, max(0.0, energy / peak))
            else:
                normalized = 0.5
            if kind == "lyric":
                function = "carry vocal narrative"
                sound = "lead vocal phrase"
            elif start <= 0.05:
                kind = "intro"
                function = "establish visual world"
                sound = "opening instrumental"
            elif end >= duration - 0.05:
                kind = "outro"
                function = "resolve and decay"
                sound = "closing instrumental or vocal tail"
            elif end - start < 2.0:
                kind = "transition"
                function = "breathe and bridge phrases"
                sound = "short musical fill or breath"
            elif normalized >= 0.7:
                function = "build or create impact"
                sound = "high-energy instrumental passage"
            else:
                function = "breathe and develop recurring motif"
                sound = "instrumental passage"
            timeline.append(
                {
                    "index": len(timeline),
                    "name": kind,
                    "type": kind,
                    "start": float(start),
                    "end": float(end),
                    "duration": float(end - start),
                    "text_canonical": text,
                    "sound": sound,
                    "function": function,
                    "energy": normalized,
                }
            )

        for cue in cues:
            start = cue["start"]
            end = cue["end"]
            if start > cursor + 0.01:
                add_interval(cursor, start, "instrumental")
            if start < cursor and timeline and timeline[-1]["type"] == "lyric":
                # Preserve overlapping vocals in one explicit overlap interval.
                overlap_start = start
                overlap_end = min(end, timeline[-1]["end"])
                if overlap_end > overlap_start:
                    timeline[-1]["type"] = "overlap"
                    timeline[-1]["name"] = "overlap"
                    timeline[-1]["text_canonical"] = (
                        f"{timeline[-1]['text_canonical']} / {cue['text']}"
                    )
                if end > cursor:
                    add_interval(cursor, end, "lyric", cue["text"])
            else:
                add_interval(max(cursor, start), end, "lyric", cue["text"])
            cursor = max(cursor, end)
        if cursor < duration:
            add_interval(cursor, duration, "instrumental")
        if not timeline and duration > 0:
            add_interval(0.0, duration, "instrumental")

        # Keep indexes stable after overlap handling.
        for i, item in enumerate(timeline):
            item["index"] = i
        return timeline
