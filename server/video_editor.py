"""Video editing module — assembles clips to the full song length via FFmpeg."""

from __future__ import annotations

import os
import json
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
    def _ass_time(seconds: float) -> str:
        centis = int(round(max(0.0, seconds) * 100))
        hours, rem = divmod(centis, 360000)
        minutes, rem = divmod(rem, 6000)
        secs, cs = divmod(rem, 100)
        return f"{hours}:{minutes:02d}:{secs:02d}.{cs:02d}"

    def apply_typography(
        self,
        video_path: str | Path,
        storyboard: List[Dict[str, Any]],
        output_path: str | Path,
    ) -> str:
        """Burn exact canonical lyrics as a polished, text-safe ASS layer."""
        source = Path(video_path)
        output = Path(output_path)
        ass_path = output.with_suffix(".lyrics.ass")
        header = """[Script Info]
ScriptType: v4.00+
PlayResX: 1920
PlayResY: 1080
WrapStyle: 2
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name,Fontname,Fontsize,PrimaryColour,SecondaryColour,OutlineColour,BackColour,Bold,Italic,Underline,StrikeOut,ScaleX,ScaleY,Spacing,Angle,BorderStyle,Outline,Shadow,Alignment,MarginL,MarginR,MarginV,Encoding
Style: Lyric,DejaVu Sans,72,&H00FFFFFF,&H0000D7FF,&HCC071018,&H88000000,-1,0,0,0,100,100,1.5,0,1,4,2,2,150,150,110,1
Style: Hero,DejaVu Sans,92,&H00FFFFFF,&H0000D7FF,&HCC071018,&H88000000,-1,0,0,0,100,100,2,0,1,5,2,5,130,130,80,1

[Events]
Format: Layer,Start,End,Style,Name,MarginL,MarginR,MarginV,Effect,Text
"""
        events: List[str] = []
        for index, frame in enumerate(storyboard):
            text = str(
                frame.get("text_display")
                or frame.get("text_canonical")
                or frame.get("lyric_window")
                or ""
            ).strip()
            if not text:
                continue
            text = (
                text.replace("\\", r"\\")
                .replace("{", r"\{")
                .replace("}", r"\}")
                .replace("\n", r"\N")
                .replace(" / ", r"\N")
            )
            start = float(frame.get("timestamp") or 0.0)
            end = float(frame.get("end") or (start + float(frame.get("duration") or 4.0)))
            if end <= start:
                continue
            style = "Hero" if any(
                word in str(frame.get("segment") or "").lower()
                for word in ("chorus", "hook", "drop")
            ) else "Lyric"
            # Exact glyphs remain stable; entrance/exit provide restrained kinetic motion.
            tags = r"{\fad(220,280)\blur0.4}"
            events.append(
                "Dialogue: 0,"
                f"{self._ass_time(start)},{self._ass_time(end)},{style},"
                f",0,0,0,,{tags}{text}"
            )
        ass_path.write_text(header + "\n".join(events) + "\n", encoding="utf-8")

        escaped_ass = str(ass_path).replace("\\", "/").replace(":", r"\:")
        cmd = [
            "ffmpeg",
            "-y",
            "-i",
            str(source),
            "-vf",
            f"ass='{escaped_ass}'",
            "-map",
            "0:v:0",
            "-map",
            "0:a:0",
            "-c:v",
            "libx264",
            "-preset",
            "medium",
            "-crf",
            "19",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "copy",
            "-movflags",
            "+faststart",
            str(output),
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=3600)
        if result.returncode != 0:
            raise RuntimeError(f"Typography composite failed: {result.stderr[-800:]}")
        return str(output.resolve())

    def technical_qc(
        self,
        video_path: str | Path,
        expected_duration: Optional[float] = None,
    ) -> Dict[str, Any]:
        """Run lightweight duration, stream, black-frame, and freeze checks."""
        path = Path(video_path)
        probe = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-show_entries",
                "format=duration:stream=codec_type,codec_name,width,height,pix_fmt",
                "-of",
                "json",
                str(path),
            ],
            capture_output=True,
            text=True,
            timeout=60,
        )
        if probe.returncode != 0:
            return {"passed": False, "error": probe.stderr[-500:]}
        data = json.loads(probe.stdout or "{}")
        actual = float((data.get("format") or {}).get("duration") or 0)
        checks: Dict[str, Any] = {
            "duration": actual,
            "expected_duration": expected_duration,
            "duration_delta": (
                abs(actual - expected_duration) if expected_duration is not None else None
            ),
            "streams": data.get("streams") or [],
        }
        diagnostics: Dict[str, List[str]] = {"black": [], "freeze": []}
        for name, vf in (
            ("black", "blackdetect=d=0.5:pix_th=0.02"),
            ("freeze", "freezedetect=n=-50dB:d=2.5"),
        ):
            run = subprocess.run(
                ["ffmpeg", "-v", "warning", "-i", str(path), "-vf", vf, "-an", "-f", "null", "-"],
                capture_output=True,
                text=True,
                timeout=600,
            )
            markers = [
                line.strip()
                for line in (run.stderr or "").splitlines()
                if f"{name}_start" in line or f"{name}_duration" in line
            ]
            diagnostics[name] = markers[-20:]
        checks["diagnostics"] = diagnostics
        checks["passed"] = (
            actual > 0
            and (
                expected_duration is None
                or abs(actual - expected_duration) <= max(0.25, 1 / 24)
            )
            and any(s.get("codec_type") == "video" for s in checks["streams"])
            and any(s.get("codec_type") == "audio" for s in checks["streams"])
        )
        return checks

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
            or os.getenv("VIDMUSE_CLIP_FILL", "freeze")
        ).strip().lower()

        audio_dur = song_duration or self.audio_duration()
        work_dir = OUTPUT_DIR / f"edit_{uuid.uuid4().hex[:10]}"
        work_dir.mkdir(parents=True, exist_ok=True)

        specs: List[Dict[str, Any]] = []
        for item in video_clips:
            if isinstance(item, dict):
                path = Path(item["path"])
                dur = float(item.get("duration") or 0)
                clip_fill = str(item.get("fill_mode") or fill_mode).strip().lower()
            else:
                path = Path(item)
                dur = 0.0
                clip_fill = fill_mode
            if not path.exists():
                continue
            specs.append({"path": path, "duration": dur, "fill_mode": clip_fill})

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
                    fill_mode=spec.get("fill_mode") or fill_mode,
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
