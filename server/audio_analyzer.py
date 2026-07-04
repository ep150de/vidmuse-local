"""Audio analysis module for VidMuse Local.

Extracts tempo, beats, energy profile, spectral features, and segment structure
from audio files using librosa.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List

import librosa
import numpy as np


class AudioAnalyzer:
    """Analyze audio tracks for music video timing."""

    @staticmethod
    def analyze(audio_path: str | Path) -> Dict[str, Any]:
        """Analyze an audio file and return timing/energy data.

        Returns a dict with:
            duration, tempo, beat_count, beat_times, energy_profile,
            brightness, segments, file_format
        """
        audio_path = Path(audio_path)
        y, sr = librosa.load(str(audio_path), sr=None, mono=True)
        duration = float(librosa.get_duration(y=y, sr=sr))

        # Beat tracking
        tempo, beat_frames = librosa.beat.beat_track(y=y, sr=sr)
        beat_times = librosa.frames_to_time(beat_frames, sr=sr).tolist()

        # Energy per beat
        rms = librosa.feature.rms(y=y)[0]
        beat_energy: List[float] = [
            float(np.mean(rms[max(0, f - 5) : f + 5]))
            for f in beat_frames
        ]

        # Spectral features
        spectral_centroid = librosa.feature.spectral_centroid(y=y, sr=sr)[0]
        spectral_rolloff = librosa.feature.spectral_rolloff(y=y, sr=sr)[0]

        # Segment structure (divide into 4 parts)
        segment_duration = duration / 4
        segments: List[Dict[str, Any]] = [
            {"name": "intro", "start": 0, "end": segment_duration},
            {"name": "verse", "start": segment_duration, "end": segment_duration * 2},
            {"name": "chorus", "start": segment_duration * 2, "end": segment_duration * 3},
            {"name": "outro", "start": segment_duration * 3, "end": duration},
        ]

        # Detect chorus (highest energy segment)
        segment_energies: List[float] = []
        for seg in segments:
            start_frame = int(seg["start"] * sr / 512)
            end_frame = int(seg["end"] * sr / 512)
            seg_energy = float(np.mean(rms[start_frame:end_frame]))
            segment_energies.append(seg_energy)

        max_seg_idx = int(np.argmax(segment_energies))
        segments[max_seg_idx]["name"] = "chorus"

        return {
            "duration": duration,
            "tempo": float(tempo),
            "beat_count": len(beat_times),
            "beat_times": beat_times[:20],
            "energy_profile": beat_energy[:20],
            "brightness": float(np.mean(spectral_centroid)),
            "segments": segments,
            "file_format": audio_path.suffix,
        }
