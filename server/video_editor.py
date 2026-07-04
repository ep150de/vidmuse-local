"""Video editing module — assembles clips synced to music beats via FFmpeg."""

from __future__ import annotations

import os
import subprocess
import uuid
from pathlib import Path
from typing import List

OUTPUT_DIR = Path(os.getenv("OUTPUT_DIR", "/tmp/vidmuse/outputs"))


class VideoEditor:
    """Assemble final music video synced to beat drops."""

    def __init__(self, audio_path: str | Path):
        self.audio_path = Path(audio_path)

    def assemble(
        self,
        video_clips: List[str],
        output_path: str | Path,
    ) -> str:
        """Assemble video clips synced to beat intervals.

        Args:
            video_clips: List of paths to individual video clips.
            output_path: Where to write the final MP4.

        Returns:
            Absolute path to the final video.
        """
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)

        # Build FFmpeg concat list with duration hints from audio beats
        import librosa
        import numpy as np

        y, sr = librosa.load(str(self.audio_path), sr=None)
        _, beat_frames = librosa.beat.beat_track(y=y, sr=sr)
        beat_times = librosa.frames_to_time(beat_frames, sr=sr)

        concat_lines = []
        clip_idx = 0

        for i, clip_path in enumerate(video_clips):
            clip_path = Path(clip_path)
            if not clip_path.exists():
                continue

            # Duration for this segment: up to next beat or 2s minimum
            if clip_idx < len(beat_times) - 1:
                duration = beat_times[clip_idx + 1] - beat_times[clip_idx]
                duration = max(duration, 1.0)  # minimum 1 second per clip
            else:
                duration = 2.0

            concat_lines.append(f"file '{clip_path.resolve()}'")
            concat_lines.append(f"# duration {duration:.3f}")
            clip_idx += 1

        concat_path = OUTPUT_DIR / f"concat_{uuid.uuid4().hex}.txt"
        concat_path.write_text("\n".join(concat_lines))

        # FFmpeg concat with audio
        cmd = [
            "ffmpeg", "-y",
            "-f", "concat", "-safe", "0",
            "-i", str(concat_path),
            "-i", str(self.audio_path),
            "-c:v", "libx264", "-preset", "medium", "-crf", "23",
            "-c:a", "aac", "-b:a", "192k",
            "-shortest",
            "-movflags", "+faststart",
            str(output_path),
        ]

        result = subprocess.run(cmd, capture_output=True, text=True, timeout=3600)
        concat_path.unlink(missing_ok=True)

        if result.returncode != 0:
            raise RuntimeError(f"FFmpeg concat failed: {result.stderr}")

        return str(output_path.resolve())
