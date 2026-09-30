#!/usr/bin/env python3
"""Inspect an audio file with ffprobe and optional ffmpeg silence detection."""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path


def run(command: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, text=True, capture_output=True, check=False)


def main() -> int:
    parser = argparse.ArgumentParser(description="Inspect audio with ffprobe and silence detection.")
    parser.add_argument("audio", type=Path)
    parser.add_argument("-o", "--output", type=Path, help="Write JSON report here")
    parser.add_argument("--silence-db", default="-45dB")
    parser.add_argument("--silence-duration", type=float, default=0.30)
    parser.add_argument("--skip-silence", action="store_true")
    args = parser.parse_args()

    if not args.audio.is_file():
        print(f"ERROR: file not found: {args.audio}", file=sys.stderr)
        return 2

    for tool in ("ffprobe", "ffmpeg"):
        if shutil.which(tool) is None:
            print(f"ERROR: required command not found: {tool}", file=sys.stderr)
            return 2

    probe = run([
        "ffprobe", "-v", "error", "-show_entries",
        "format=duration,start_time,format_name,bit_rate:stream=index,codec_name,codec_type,sample_rate,channels,channel_layout",
        "-of", "json", str(args.audio),
    ])
    if probe.returncode != 0:
        print(probe.stderr, file=sys.stderr)
        return probe.returncode

    report = {
        "source": str(args.audio.resolve()),
        "probe": json.loads(probe.stdout),
        "silence_intervals": [],
    }

    if not args.skip_silence:
        silence = run([
            "ffmpeg", "-hide_banner", "-nostats", "-i", str(args.audio),
            "-af", f"silencedetect=noise={args.silence_db}:d={args.silence_duration}",
            "-f", "null", "-",
        ])
        starts = [float(x) for x in re.findall(r"silence_start: ([0-9.]+)", silence.stderr)]
        ends = [
            (float(end), float(duration))
            for end, duration in re.findall(r"silence_end: ([0-9.]+) \| silence_duration: ([0-9.]+)", silence.stderr)
        ]
        intervals: list[dict[str, object]] = []
        for index, start in enumerate(starts):
            if index < len(ends):
                end, duration = ends[index]
                intervals.append({"start": start, "end": end, "duration": duration})
            else:
                intervals.append({"start": start, "end": None, "duration": None})
        report["silence_intervals"] = intervals
        report["silence_threshold"] = {"noise": args.silence_db, "minimum_duration": args.silence_duration}

    payload = json.dumps(report, indent=2, ensure_ascii=False) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(payload, encoding="utf-8")
        print(f"Wrote {args.output}")
    else:
        print(payload, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
