#!/usr/bin/env bash
# render_scene.sh <INDEX> — submit ONE Flux 3 image_to_video job for timeline
# shot INDEX, poll (raw GET) until Ready, then save in the SAME call.
#
# FORWARD-PIPELINE RULES (from user feedback, applied 017+; 001-015 left as-is):
#  - NO hue-shift (looked green). Fixed character look; vary POSE via crop/zoom.
#  - Alternate scene TYPE:
#      even index  -> MAIN-CHAR CLOSE-UP lip-sync (character prominent, singing)
#      odd  index  -> THEME/WORLD shot (lunar environment described by lyric; char small/absent)
#  - Moderated: reword prompt (drop risky phrasing), retry once; if still moderated, skip + extend prev.
#  - input_image/audio MUST be absolute local paths (wrapper auto-uploads).
set -u
cd /home/lingleblater/vidmuse-local
export NOUS_TOOLS_ENABLED=1
export MANAGED_NOUS_TOOLS=1
IDX="${1:?scene index}"
export IDX
SINGER="/home/lingleblater/vidmuse-local/outputs/8961593b-61cf-43b2-a9a9-f350940c3029/references/vidmuse_technologist_zimage_832x480_00001_.png"
MP3="/home/lingleblater/ai-radio-station/music/North By Northwest.mp3"
DIR="neon paper-cut lunar agency world, foil-stamped type glow, basalt-dust grain, synthwave grid wipes"
export SINGER MP3 DIR

server/.venv/bin/python - <<'PY'
import os, sys, asyncio, json
sys.path.insert(0, "/home/lingleblater/.hermes/hermes-agent")
os.environ["NOUS_TOOLS_ENABLED"]="1"; os.environ["MANAGED_NOUS_TOOLS"]="1"
from tools.flux3_video_tool import (_handle_image_to_video, _handle_get_result,
                                    _endpoints, _call_gateway, check_bfl_requirements)
assert check_bfl_requirements(), "BFL gated"

tl = json.load(open("analysis/timeline.json"))["timeline"]
i = int(os.environ["IDX"]); shot = tl[i]
lyric = shot["text_canonical"]
SINGER = os.environ["SINGER"]; DIR = os.environ["DIR"]

# --- per-scene still: vary POSE via crop/zoom (NO hue-shift) ---
# even idx = close-up (center crop, zoomed); odd idx = wide (full frame, char small)
if i % 2 == 0:
    vf = "crop=600:480:116:0,scale=832:480"   # tight close-up of singer
    ctype = "MAIN-CHAR CLOSE-UP lip-sync"
else:
    vf = "scale=832:480"                       # full wide frame, lunar world
    ctype = "THEME/WORLD shot"
os.system(f"ffmpeg -y -i {SINGER} -vf '{vf}' _scene_{i}.png >/dev/null 2>&1")

# --- build prompt per scene type ---
if i % 2 == 0:
    prompt = f"{lyric} — {DIR}; the singer in tight close-up, lips synced to the lyric, neon rim-light, slow push-in"
else:
    prompt = f"{lyric} — {DIR}; vast lunar megastructure horizon, synthwave grid, the singer a small silhouette amid the architecture, slow drift"

args = {
    "prompt": prompt,
    "input_image": f"/home/lingleblater/vidmuse-local/_scene_{i}.png",
    "input_audio": os.environ["MP3"],
    "aspect_ratio": "16:9", "duration": 5, "resolution": "720p",
    "generate_audio": False, "grounding": True, "seed": None, "version": "latest",
}

async def submit_and_save(jid):
    ep = _endpoints(); from urllib.parse import quote
    for _ in range(2):
        raw = await _call_gateway("GET", f"{ep['base_url']}/generations/{quote(jid, safe='')}")
        det = json.loads(raw).get("details", {})
        st = det.get("status")
        if st == "Ready":
            res = await _handle_get_result({"id": jid, "save_to": f"/home/lingleblater/vidmuse-local/shot_{i:03d}.mp4"})
            print("RESULT:", res[:160]); return True
        if st == "Request Moderated":
            print("MODERATED"); return False
        await asyncio.sleep(15)
    print("still generating — re-run: bash render_scene.sh", i); return None

async def go():
    jfile = f"scene_{i}_jobid.txt"
    jid = open(jfile).read().strip() if os.path.exists(jfile) else None
    if not jid:
        raw = await _handle_image_to_video(args)
        jid = (json.loads(raw).get("details") or {}).get("id")
        if jid: open(jfile, "w").write(jid)
        else:
            print("submit no id; raw:", raw[:200]); return
    print(f"shot {i} [{ctype}] job: {jid}")
    ok = await submit_and_save(jid)
    if ok is False:  # moderated -> reword + resubmit once
        print("reword attempt...")
        args["prompt"] = f"Cinematic lunar vista with glowing synthwave grid, atmospheric, no text. {DIR}"
        raw = await _handle_image_to_video(args)
        jid2 = (json.loads(raw).get("details") or {}).get("id")
        if jid2:
            open(jfile, "w").write(jid2)
            ok2 = await submit_and_save(jid2)
            if ok2 is False:
                print("still moderated -> SKIP scene", i, "(extend previous on assemble)")
asyncio.run(go())
PY
