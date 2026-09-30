#!/usr/bin/env python3
"""Validate coverage, duration, and canonical lyric fields in a lyric-video timeline JSON."""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path


def fail(message: str) -> None:
    print(f"ERROR: {message}", file=sys.stderr)


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate timeline coverage and lyric integrity.")
    parser.add_argument("manifest", type=Path, help="JSON manifest containing duration_seconds and timeline")
    parser.add_argument("--fps", type=float, default=30.0, help="Tolerance basis; default 30")
    args = parser.parse_args()

    try:
        data = json.loads(args.manifest.read_text(encoding="utf-8"))
    except Exception as exc:
        fail(f"cannot read JSON: {exc}")
        return 2

    duration = data.get("duration_seconds")
    timeline = data.get("timeline")
    if not isinstance(duration, (int, float)) or not math.isfinite(duration) or duration <= 0:
        fail("duration_seconds must be a positive finite number")
        return 2

    if not isinstance(timeline, list) or not timeline:
        fail("timeline must be a non-empty array")
        return 2

    tolerance = 0.5 / args.fps
    expected = 0.0
    errors: list[str] = []
    seen_ids: set[str] = set()

    for index, item in enumerate(timeline):
        label = item.get("id", f"index {index}") if isinstance(item, dict) else f"index {index}"
        if not isinstance(item, dict):
            errors.append(f"{label}: interval must be an object")
            continue
        if label in seen_ids:
            errors.append(f"{label}: duplicate id")
        seen_ids.add(label)

        start = item.get("start_seconds")
        end = item.get("end_seconds")
        if not all(isinstance(v, (int, float)) and math.isfinite(v) for v in (start, end)):
            errors.append(f"{label}: start_seconds and end_seconds must be finite numbers")
            continue
        if end <= start:
            errors.append(f"{label}: end must be greater than start")
            continue

        delta = start - expected
        if abs(delta) > tolerance:
            word = "gap" if delta > 0 else "overlap/out-of-order interval"
            errors.append(f"{label}: {word} of {abs(delta):.6f}s before interval")

        if item.get("type") in {"lyric", "overlap"}:
            text = item.get("text_canonical")
            if not isinstance(text, str) or not text.strip():
                errors.append(f"{label}: lyric/overlap interval requires non-empty text_canonical")

        expected = end

    if abs(expected - duration) > tolerance:
        word = "uncovered tail" if expected < duration else "timeline exceeds duration"
        errors.append(f"{word}: timeline ends at {expected:.6f}s, song ends at {duration:.6f}s")

    if errors:
        for error in errors:
            fail(error)
        print(f"FAIL: {len(errors)} timeline error(s)")
        return 1

    lyric_count = sum(1 for x in timeline if x.get("type") in {"lyric", "overlap"})
    gap_count = sum(1 for x in timeline if x.get("type") in {"intro", "instrumental", "transition", "outro"})
    print(
        f"PASS: {len(timeline)} intervals cover 0.000000-{duration:.6f}s "
        f"within +/-{tolerance:.6f}s ({lyric_count} lyric, {gap_count} non-lyric)."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
