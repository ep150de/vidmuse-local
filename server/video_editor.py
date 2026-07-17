"""Video editing module — assembles clips to the full song length via FFmpeg."""

from __future__ import annotations

import os
import subprocess
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

OUTPUT_DIR = Path(os.getenv("OUTPUT_DIR", "/tmp/vidmuse/outputs"))

ClipSpec = Union[str, Dict[str, Any]]


class VideoEditor:
    """Assemble a full-length music video timed to the master audio track."""

    def __init__(self, audio_path: str | Path):
        self.audio_path = Path(audio_path)

    def audio_duration(self) -> float:
        """Return audio duration in seconds via ffprobe."""
        cmd = [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(self.audio_path),
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        if result.returncode != 0:
            raise RuntimeError(f"ffprobe failed: {result.stderr}")
        return float(result.stdout.strip())

    @staticmethod
    def _probe_duration(path: Path) -> float:
        cmd = [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(path),
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        if result.returncode != 0:
            return 0.0
        try:
            return float(result.stdout.strip())
        except ValueError:
            return 0.0

    def _normalize_clip(
        self,
        clip_path: Path,
        target_sec: float,
        work_dir: Path,
        index: int,
        fill_mode: str = "loop",
    ) -> Path:
        """Resize/pad a clip so it lasts exactly target_sec at 24fps 1280x720."""
        target_sec = max(0.5, float(target_sec))
        out = work_dir / f"norm_{index:03d}.mp4"
        src_dur = self._probe_duration(clip_path) or 1.0
        fill_mode = (fill_mode or "loop").lower()

        # Common scale + fps + silence strip (audio comes from master track later)
        vf = (
            "scale=1280:720:force_original_aspect_ratio=decrease,"
            "pad=1280:720:(ow-iw)/2:(oh-ih)/2,"
            "fps=24,format=yuv420p"
        )

        if fill_mode == "stretch" and src_dur > 0.05:
            speed = src_dur / target_sec
            # setpts: larger pts = slower. speed>1 means speed up (shorter clip stretched out differently)
            # Want output duration = target: setpts = PTS * (target/src) = PTS / speed when speed=src/target
            vf = f"{vf},setpts=PTS*{target_sec / src_dur}"
            cmd = [
                "ffmpeg",
                "-y",
                "-i",
                str(clip_path),
                "-an",
                "-vf",
                vf,
                "-t",
                f"{target_sec:.3f}",
                "-c:v",
                "libx264",
                "-preset",
                "veryfast",
                "-crf",
                "20",
                "-pix_fmt",
                "yuv420p",
                str(out),
            ]
        elif fill_mode == "freeze":
            # Play once, then freeze last frame to fill remaining time
            hold = max(0.0, target_sec - src_dur)
            if hold > 0.05:
                vf = f"{vf},tpad=stop_mode=clone:stop_duration={hold:.3f}"
            cmd = [
                "ffmpeg",
                "-y",
                "-i",
                str(clip_path),
                "-an",
                "-vf",
                vf,
                "-t",
                f"{target_sec:.3f}",
                "-c:v",
                "libx264",
                "-preset",
                "veryfast",
                "-crf",
                "20",
                "-pix_fmt",
                "yuv420p",
                str(out),
            ]
        else:
            # loop: stream_loop enough times then hard-cut to target
            loops = max(0, int(target_sec / max(src_dur, 0.1)) + 1)
            cmd = [
                "ffmpeg",
                "-y",
                "-stream_loop",
                str(loops),
                "-i",
                str(clip_path),
                "-an",
                "-vf",
                vf,
                "-t",
                f"{target_sec:.3f}",
                "-c:v",
                "libx264",
                "-preset",
                "veryfast",
                "-crf",
                "20",
                "-pix_fmt",
                "yuv420p",
                str(out),
            ]

        result = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
        if result.returncode != 0 or not out.exists():
            raise RuntimeError(
                f"Failed to normalize clip {clip_path.name}: {result.stderr[-500:]}"
            )
        return out

    def assemble(
        self,
        video_clips: List[ClipSpec],
        output_path: str | Path,
        song_duration: Optional[float] = None,
        fill_mode: Optional[str] = None,
    ) -> str:
        """Assemble clips into a video matching the full song length.

        Each item may be a path string or dict with keys:
          path, duration (scene length), start (optional timeline offset).
        Clips are placed in order and padded/looped so their sum equals the audio.
        """
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        fill_mode = (
            fill_mode
            or os.getenv("VIDMUSE_CLIP_FILL", "loop")
        ).strip().lower()

        audio_dur = song_duration or self.audio_duration()
        work_dir = OUTPUT_DIR / f"edit_{uuid.uuid4().hex[:10]}"
        work_dir.mkdir(parents=True, exist_ok=True)

        specs: List[Dict[str, Any]] = []
        for item in video_clips:
            if isinstance(item, dict):
                path = Path(item["path"])
                dur = float(item.get("duration") or 0)
            else:
                path = Path(item)
                dur = 0.0
            if not path.exists():
                continue
            specs.append({"path": path, "duration": dur})

        if not specs:
            raise RuntimeError("No valid video clips to assemble")

        # Distribute time across clips to cover the full song
        known = sum(s["duration"] for s in specs if s["duration"] > 0)
        missing = [s for s in specs if s["duration"] <= 0]
        if known <= 0:
            even = audio_dur / len(specs)
            for s in specs:
                s["duration"] = even
        else:
            leftover = max(0.0, audio_dur - known)
            if missing:
                share = leftover / len(missing) if leftover > 0 else 2.0
                for s in missing:
                    s["duration"] = max(1.0, share)
            # Scale if still short/long of audio
            total = sum(s["duration"] for s in specs)
            if total > 0 and abs(total - audio_dur) > 0.25:
                scale = audio_dur / total
                for s in specs:
                    s["duration"] *= scale

        normalized: List[Path] = []
        for i, spec in enumerate(specs):
            normalized.append(
                self._normalize_clip(
                    spec["path"],
                    spec["duration"],
                    work_dir,
                    i,
                    fill_mode=fill_mode,
                )
            )

        concat_path = work_dir / "concat.txt"
        concat_path.write_text(
            "\n".join(f"file '{p.resolve()}'" for p in normalized) + "\n"
        )

        silent_video = work_dir / "video_only.mp4"
        cmd_concat = [
            "ffmpeg",
            "-y",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            str(concat_path),
            "-c:v",
            "libx264",
            "-preset",
            "medium",
            "-crf",
            "20",
            "-pix_fmt",
            "yuv420p",
            "-an",
            "-t",
            f"{audio_dur:.3f}",
            str(silent_video),
        ]
        result = subprocess.run(cmd_concat, capture_output=True, text=True, timeout=3600)
        if result.returncode != 0:
            raise RuntimeError(f"FFmpeg concat failed: {result.stderr[-800:]}")

        # Mux master audio for the full song (audio is the duration authority)
        cmd_mux = [
            "ffmpeg",
            "-y",
            "-i",
            str(silent_video),
            "-i",
            str(self.audio_path),
            "-map",
            "0:v:0",
            "-map",
            "1:a:0",
            "-c:v",
            "copy",
            "-c:a",
            "aac",
            "-b:a",
            "192k",
            "-t",
            f"{audio_dur:.3f}",
            "-movflags",
            "+faststart",
            str(output_path),
        ]
        result = subprocess.run(cmd_mux, capture_output=True, text=True, timeout=3600)
        if result.returncode != 0:
            raise RuntimeError(f"FFmpeg mux failed: {result.stderr[-800:]}")

        # Cleanup work dir (best effort)
        try:
            for p in work_dir.iterdir():
                p.unlink(missing_ok=True)
            work_dir.rmdir()
        except OSError:
            pass

        return str(output_path.resolve())
