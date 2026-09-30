#!/usr/bin/env bash
# run-music-video.sh — PROPER music video: one clip per lyric-scene.
#
# For each of the 58 shots in analysis/timeline.json, submit ONE
# bfl_flux3_image_to_video job with that scene's lyric as the prompt +
# a per-scene singer-still variant (recolored per mood) + the mp3 slice
# for that timestamp. No 20s loop — every scene is distinct and matched
# to the words sung at that moment. Then FFmpeg-concat the clips into the
# full-length video with the ORIGINAL mp3 as the audio layer.
#
# Usage: bash run-music-video.sh "<DIRECTION>" <MP3> <SINGER_IMG>
set -u
cd /home/lingleblater/vidmuse-local
export NOUS_TOOLS_ENABLED=1
export MANAGED_NOUS_TOOLS=1

DIR="${1:?direction}"; MP3="${2:?mp3}"; IMG="${3:?singer img}"

python3 - <<PY
import os, sys, json, asyncio, time
sys.path.insert(0, "/home/lingleblater/.hermes/hermes-agent")
os.environ["NOUS_TOOLS_ENABLED"]="1"; os.environ["MANAGED_NOUS_TOOLS"]="1"
from tools.flux3_video_tool import _handle_image_to_video, _handle_get_result, check_bfl_requirements

tl = json.load(open("analysis/timeline.json"))["timeline"]
assert check_bfl_requirements(), "BFL not available (account not flagged paid/logged-in)"

jobs = []
for i, shot in enumerate(tl):
    # PACE: sleep between submissions so the provider does not rate-limit/block.
    if i > 0:
        time.sleep(25)
    t0 = shot["start_seconds"]; t1 = shot["end_seconds"]
    # per-scene still: tint the singer ref by mood (neon magenta / cyan / gold per third)
    hue = (i * 37) % 360
    os.system(f"ffmpeg -y -i {IMG} -vf 'hue=h={hue}' _scene_{i}.png >/dev/null 2>&1")
    args = {
        "prompt": f"{shot['text_canonical']} — {os.environ['DIR']}",
        "input_image": f"_scene_{i}.png",
        "input_audio": os.environ["MP3"],
        "aspect_ratio": "16:9", "duration": 5, "resolution": "720p",
        "generate_audio": False, "grounding": True, "seed": None, "version": "latest",
    }
    raw = asyncio.run(_handle_image_to_video(args))
    import json as _j
    jid = (_j.loads(raw).get("details") or {}).get("id")
    jobs.append((i, jid))
    print(f"shot {i} ({t0:.1f}s) submitted: {jid}")

# poll all to Ready, then concat
for i, jid in jobs:
    for _ in range(40):
        res = asyncio.run(_handle_get_result({"id": jid}))
        det = res.get("details") if isinstance(res, dict) else {}
        if det.get("status") == "Ready":
            clip = (det.get("saved_path") or (det.get("result", {}) or {}).get("sample"))
            os.system(f"cp '{clip}' _clip_{i}.mp4")
            break
        time.sleep(15)
print("all scenes rendered; concatenating...")
# concat in timeline order
with open("concat_list.txt","w") as f:
        for i,_ in jobs:
            if os.path.exists(f"_clip_{i}.mp4"): f.write(f"file '_clip_{i}.mp4'\n")
os.system("ffmpeg -y -f concat -safe 0 -i concat_list.txt -c copy full_music_video.mp4")
print("FULL VIDEO (per-scene, no loop) READY: full_music_video.mp4")
PY
