#!/usr/bin/env bash
# One-command AI MTV music video:
#   mp3 + text direction + reference image of the singer  ->  FLUX 3 image-to-video clip
#
# This is the CORRECTED pipeline. The previous run used bfl_flux3_text_to_video
# (which generates its OWN audio track). For YOUR music to be the audio layer,
# we use bfl_flux3_image_to_video with input_audio = your .mp3, so the clip's
# sound IS the original track, lip-synced to the singer image you supply.
#
# Usage:
#   bash run-bfl-video.sh "<PROMPT>" <MP3_PATH> <SINGER_IMAGE_PATH>
#
# No manual job-id tracking: the script captures the submitted job id from the
# gateway response and polls bfl_flux3_get_result automatically until Ready.
set -u
cd /home/lingleblater/vidmuse-local
export NOUS_TOOLS_ENABLED=1
export MANAGED_NOUS_TOOLS=1

PROMPT="${1:?usage: run-bfl-video.sh '<PROMPT>' <MP3> <SINGER_IMG>}"
MP3="${2:?}"
IMG="${3:?}"

server/.venv/bin/python - <<PY
import os, sys, asyncio, time
sys.path.insert(0, "/home/lingleblater/.hermes/hermes-agent")
os.environ["NOUS_TOOLS_ENABLED"]="1"
os.environ["MANAGED_NOUS_TOOLS"]="1"
from tools.flux3_video_tool import (
    _handle_image_to_video, _handle_get_result, check_bfl_requirements
)

async def main():
    assert check_bfl_requirements(), "BFL not available in this build (account not flagged paid/logged-in)"
    args = {
        "prompt": os.environ["PROMPT"],
        "input_image": os.environ["IMG"],
        "input_audio": os.environ["MP3"],
        "aspect_ratio": "16:9", "duration": 20, "resolution": "720p",
        "generate_audio": False, "grounding": True, "seed": None, "version": "latest",
    }
    # _handle_image_to_video submits and returns a JSON string (response body)
    raw = await _handle_image_to_video(args)
    import json as _json
    sub = _json.loads(raw) if isinstance(raw, str) else raw
    jid = (sub.get("details") or {}).get("id")
    print("submitted job:", jid)
    for _ in range(40):
        res = await _handle_get_result({"id": jid})
        det = (res.get("details") if isinstance(res, dict) else {})
        if det.get("status") == "Ready":
            print("READY. saved_path:", (det.get("saved_path") or (det.get("result", {}) or {}).get("sample")))
            return
        time.sleep(15)
    print("gave up polling after 40 tries")

asyncio.run(main())
PY
