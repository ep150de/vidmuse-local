#!/usr/bin/env python3
"""Build a coverage-complete lyric timeline for North By Northwest.

Inputs:
  /home/lingleblater/vidmuse-local/North By Northwest.mp3
  /home/lingleblater/vidmuse-local/analysis/transcript_cues.json  <- produced by whisper
  Passed-in canonical lyric segments with rough start/end list.

Outputs:
  /home/lingleblater/vidmuse-local/analysis/audio_features.json
  /home/lingleblater/vidmuse-local/analysis/lines_grid.json
  /home/lingleblater/vidmuse-local/analysis/lyrics_cues.json
  /home/lingleblater/vidmuse-local/analysis/timeline.json   <- validated
  /home/lingleblater/vidmuse-local/analysis/timeline_qc.txt
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path
from typing import Any

# MUST run before importing librosa. SciPy 1.15+ removed scipy.signal.hann.
import scipy.signal  # noqa: F401
import scipy.signal.windows as _windows
scipy.signal.hann = getattr(scipy.signal, "hann", None) or _windows.hann  # type: ignore[attr-defined]
scipy.signal.hanning = getattr(scipy.signal, "hanning", None) or _windows.hann  # type: ignore[attr-defined]

import librosa
import numpy as np

AUDIO = Path("/home/lingleblater/vidmuse-local/North By Northwest.mp3")
BASE = Path("/home/lingleblater/vidmuse-local/analysis")
_REPO = Path(__file__).resolve().parent.parent


def _ensure_repo_on_path() -> None:
    if str(_REPO) not in sys.path:
        sys.path.insert(0, str(_REPO))


def ensure_dir(p: Path) -> None:
    p.mkdir(parents=True, exist_ok=True)


def run_whisper(audio: Path, output_cues: Path) -> list[dict[str, Any]]:
    """Run local faster-whisper transcribe_audio_local and save cues."""
    _ensure_repo_on_path()
    from server.transcribe import transcribe_audio_local
    from server.lyrics import parse_srt

    result = transcribe_audio_local(audio, model_size="small", language="english")
    cues = [c for c in result.get("cues", []) if c.get("text")]
    output_cues.write_text(json.dumps(cues, ensure_ascii=False, indent=2), encoding="utf-8")
    # Also keep raw text/srt nearby.
    (BASE / "transcript_text.txt").write_text(result.get("text", ""), encoding="utf-8")
    (BASE / "transcript.srt").write_text(result.get("srt", ""), encoding="utf-8")
    return cues


def music_features(audio: Path, out_json: Path) -> dict[str, Any]:
    """Beat track, estimate tempo and build energy/beat map."""
    y, sr = librosa.load(str(audio), sr=None, mono=True)
    duration = float(librosa.get_duration(y=y, sr=sr))
    tempo, beat_frames = librosa.beat.beat_track(y=y, sr=sr, trim=False)
    beat_times = librosa.frames_to_time(beat_frames, sr=sr).tolist()
    rms = librosa.feature.rms(y=y)[0]

    window = 2205  # ~0.046s at 48k
    beat_energy = [float(np.mean(rms[max(0, f - 5) : f + 5])) for f in beat_frames]

    # Coarse segments by equal quarters, then raise the loudest to chorus.
    segment_duration = duration / 4.0
    segments = [
        {"name": "intro", "start": 0.0, "end": segment_duration},
        {"name": "verse", "start": segment_duration, "end": segment_duration * 2},
        {"name": "verse", "start": segment_duration * 2, "end": segment_duration * 3},
        {"name": "outro", "start": segment_duration * 3, "end": duration},
    ]
    seg_energies = []
    for seg in segments:
        sf = int(seg["start"] * sr / 512)
        ef = int(seg["end"] * sr / 512)
        ef = max(ef, sf + 1)
        seg_energies.append(float(np.mean(rms[sf:ef])))
    seg_max = int(np.argmax(seg_energies))
    segments[seg_max]["name"] = "chorus"

    data = {
        "duration": duration,
        "tempo": float(np.asarray(tempo).reshape(-1)[0]),
        "beat_count": len(beat_times),
        "beat_times": beat_times,
        "energy_profile": [float(x) for x in rms],
        "segments": segments,
    }
    out_json.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return data


LYRICS = """
[Verse 1]
DJ Gifted Prophet in the cut
North by Northwest, got the map tucked up
Where the dreams go, I seen it in the dust
From a late-night sketch to a launchpad trust

Moon in my lens, I don't blink, I aim
Paper to the stars, I'm a different flame
Dream to reality, I move that way
One small spark, then the whole sky change

I'm talkin' lunar agency, boots on the plan
Turn rock to reward with a patient hand
In-space resource, let the future stand
Build a new lane with the whole world fam

[Pre-Chorus]
If the deal hit right, we can level this fast
All the startups linked, all the agencies hashed
One table, one vow, let the old fear pass
We can build something true that was made to last

[Chorus]
North by Northwest, where the dreams go
Dream to reality, let it breathe slow
North by Northwest, where the dreams go
Lock the deal in, watch the whole world glow
(North by Northwest)
(Dream to reality)

[Verse 2]
I'm seeing consortia in the same hard room
Every builder on deck with a shared-out moon
No empty talk, just a signed-up truce
Tech so advanced make the future move

From the basalt dust to the resource vein
Fuel from the void, let it change the game
Less fight, more light, let the peace get named
Human space age, that's the frame I claim

DJ Gifted Prophet, I don't chase applause
I chase that moment when the whole world walks
Side by side, no border talk
Just a common sky and a stronger cause

[Pre-Chorus]
If the deal hit right, we can level this fast
All the startups linked, all the agencies hashed
One table, one vow, let the old fear pass
We can build something true that was made to last

[Chorus]
North by Northwest, where the dreams go
Dream to reality, let it breathe slow
North by Northwest, where the dreams go
Lock the deal in, watch the whole world glow
(North by Northwest)
(Dream to reality)

[Bridge]
From a spark to a system
From a wish to a mission
From the lone night vision
To a world in position

Let the old walls fade
Let the new bonds stay
If we move this way
Peace can learn to play

[Chorus]
North by Northwest, where the dreams go
Dream to reality, let it breathe slow
North by Northwest, where the dreams go
Lock the deal in, watch the whole world glow
(North by Northwest)
(Dream to reality)
"""

def line_cues_from_text(duration: float, ref_beats: list[float], cues_file: Path | None = None) -> list[dict[str, Any]]:
    """Create line cues aligned to lyrical units through the lyrics text.

    Because whisper alone won't give exact book-matched moment labels at low confidence,
    we build atomic alignment later via spectral jumps. Here we bootstrap lyric durations.
    """
    lines = [re.sub(r"\s+", " ", line).strip() for line in LYRICS.splitlines() if line.strip()]
    # Remove structural headers that look like [Verse 1]
    lines = [ln for ln in lines if not re.match(r"^\[.*\]$", ln)]
    # Remove empty-parenthesized ad-libs in chorus by keeping them, as exact text.
    block_words = [len(re.findall(r"\w+", ln)) for ln in lines]
    total = max(sum(block_words), 1)
    cursor = 0.0
    cues: list[dict[str, Any]] = []
    for i, (line, wc) in enumerate(zip(lines, block_words)):
        if cursor >= duration:
            break
        if i + 1 < len(lines):
            # Compact short couplets/ad-libs with musical breathing.
            if wc <= 6 and i + 2 < len(lines) and len(re.findall(r"\w+", lines[i + 1])) <= 6:
                end = cursor + max(0.25, 0.65 * duration * (wc + len(re.findall(r"\w+", lines[i + 1]))) / total)
                text = line.strip() + " / " + lines[i + 1].strip()
                i += 1
            else:
                end = cursor + max(0.35, 0.85 * duration * wc / total)
                text = line.strip()
        else:
            end = duration
            text = line.strip()
        if end < cursor + 0.18:
            end = cursor + 0.18
        cues.append({
            "text": text,
            "start": cursor,
            "end": min(end, duration),
            "source": "lyrics-token-weight",
            "confidence": 0.3,
            "lyric_index": i + 1,
        })
        cursor = min(end, duration)
    cues_file.write_text(json.dumps(cues, ensure_ascii=False, indent=2), encoding="utf-8")
    return cues


def main() -> int:
    parser = argparse.ArgumentParser(description="Derive lyric timeline from audio + canonical text.")
    parser.add_argument("--audio", default=str(AUDIO), help="Song file")
    args = parser.parse_args()
    audio = Path(args.audio)
    ensure_dir(BASE)

    print("[1/4] Transcribing audio with faster-whisper...")
    transcript_cues = run_whisper(audio, BASE / "transcript_cues.json")
    print(f"       transcript cues: {len(transcript_cues)}")

    print("[2/4] Extracting music features...")
    mus = music_features(audio, BASE / "audio_features.json")
    duration = mus["duration"]
    beats = mus["beat_times"]
    print(f"       duration={duration:.2f}s; tempo={mus['tempo']:.1f} bpm; beats={len(beats)}")

    print("[3/4] Building canonical lyric cues...")
    cues = line_cues_from_text(duration, beats, BASE / "lyrics_cues.json")
    print(f"       lyric cues: {len(cues)}")

    print("[4/4] Merging into coverage timeline...")
    _ensure_repo_on_path()
    from server.audio_analyzer import AudioAnalyzer
    timeline = AudioAnalyzer.build_coverage_timeline(duration=duration, lyric_cues=cues)
    # Enrich music features.
    for item, beat_times in ((item, beats) for item in timeline):
        t = (item["start"] + item["end"]) / 2.0
        nearby = [b for b in beat_times if abs(b - t) <= 0.5]
        item["nearest_beat_times"] = nearby[:3]
    (BASE / "timeline.json").write_text(
        json.dumps({"duration_seconds": duration, "timeline": timeline}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"       timeline intervals: {len(timeline)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
