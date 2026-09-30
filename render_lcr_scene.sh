#!/usr/bin/env bash
# render_lcr_scene.sh <SCENE_INDEX> — render ONE scene for Lunar Cargo Route
# using the 10-scene plan (analysis_lcr/timeline_plan.json).
# Differences from the old pipeline:
#  - Fewer/longer scenes (10 total, 15-21s windows).
#  - CLOSEUP scenes are a DISTINCT route: if moderated, retry with a
#    safer singing prompt (never silently converted to a THEME shot).
#  - No on-screen lyric text (visuals only).
#  - Flux3 max duration is 10s, so each scene renders one 10s clip that
#    covers its window start; the song carries the rest (no fake extension).
set -u
cd /home/lingleblater/vidmuse-local
export NOUS_TOOLS_ENABLED=1
export MANAGED_NOUS_TOOLS=1
IDX="${1:?scene index}"
export IDX
SONG="/home/lingleblater/vidmuse-local/Lunar Cargo Route.mp3"
SINGER="/home/lingleblater/vidmuse-local/outputs/8961593b-61cf-43b2-a9a9-f350940c3029/references/vidmuse_technologist_zimage_832x480_00001_.png"
DIR="neon paper-cut lunar cargo agency world, foil-stamped type glow, basalt-dust grain, synthwave grid wipes, cargo depots and conveyor routes"
export SONG SINGER DIR

server/.venv/bin/python - <<'PY'
import os, sys, asyncio, json
sys.path.insert(0, "/home/lingleblater/.hermes/hermes-agent")
os.environ["NOUS_TOOLS_ENABLED"]="1"; os.environ["MANAGED_NOUS_TOOLS"]="1"
from tools.flux3_video_tool import (_handle_image_to_video, _handle_get_result,
                                    _endpoints, _call_gateway, check_bfl_requirements)
assert check_bfl_requirements(), "BFL gated"

plan = json.load(open("analysis_lcr/timeline_plan.json"))["scenes"]
i = int(os.environ["IDX"]); sc = plan[i]
stype = sc["type"]; lyric = sc.get("lyric","")
SINGER = os.environ["SINGER"]; DIR = os.environ["DIR"]

# per-scene still: close-up = tight crop; theme/outro = wide frame
if stype == "CLOSEUP":
    vf = "crop=600:480:116:0,scale=832:480"   # tight close-up of singer
else:
    vf = "scale=832:480"                       # wide lunar world
os.system(f"ffmpeg -y -i {SINGER} -vf '{vf}' _lcr_{i}.png >/dev/null 2>&1")

if stype == "CLOSEUP":
    # HARD RULE: never render text/captions/lyrics into the frame.
    # The character must be visibly singing (mouth moving), lip-synced to the audio.
    prompt = (f"A single person in tight cinematic close-up, lips moving and singing along, "
              f"neon lunar rim-light on the face, slow push-in, synthwave mood, {DIR}. "
              f"NO text, NO captions, NO words, NO subtitles, NO letters anywhere in frame.")
else:
    prompt = f"{DIR}; vast lunar cargo depot and conveyor horizon, synthwave grid, atmospheric drift, cinematic. NO text, NO captions, NO letters."

args = {
    "prompt": prompt,
    "input_image": f"/home/lingleblater/vidmuse-local/_lcr_{i}.png",
    "input_audio": os.environ["SONG"],
    "aspect_ratio": "16:9", "duration": 10, "resolution": "720p",
    "generate_audio": False, "grounding": True, "seed": None, "version": "latest",
}

async def submit_and_save(jid):
    ep = _endpoints(); from urllib.parse import quote
    for _ in range(2):
        raw = await _call_gateway("GET", f"{ep['base_url']}/generations/{quote(jid, safe='')}")
        det = json.loads(raw).get("details", {}); st = det.get("status")
        if st == "Ready":
            res = await _handle_get_result({"id": jid, "save_to": f"/home/lingleblater/vidmuse-local/lcr_{i:02d}.mp4"})
            print("RESULT:", res[:160]); return True
        if st == "Request Moderated":
            print("MODERATED"); return False
        await asyncio.sleep(15)
    print("still generating — re-run: bash render_lcr_scene.sh", i); return None

async def go():
    jfile = f"lcr_{i}_jobid.txt"
    jid = open(jfile).read().strip() if os.path.exists(jfile) else None
    if not jid:
        raw = await _handle_image_to_video(args)
        jid = (json.loads(raw).get("details") or {}).get("id")
        if jid: open(jfile,"w").write(jid)
        else:
            print("submit no id; raw:", raw[:200]); return
    print(f"scene {i} [{stype}] job: {jid}")
    ok = await submit_and_save(jid)
    if ok is False and stype == "CLOSEUP":
        # retry close-up with a safer singing prompt (never become a theme shot)
        print("retry close-up with safer prompt...")
        args["prompt"] = f"A person singing into the void, neon lunar rim-light, lips moving, cinematic close-up, {DIR}. NO text, NO captions, NO letters anywhere."
        raw = await _handle_image_to_video(args)
        jid2 = (json.loads(raw).get("details") or {}).get("id")
        if jid2:
            open(jfile,"w").write(jid2)
            ok2 = await submit_and_save(jid2)
            if ok2 is False:
                print("close-up still moderated -> SKIP scene", i)
    elif ok is False:
        # theme/outro moderated: rewording already safe-ish, retry once
        print("retry theme with safer prompt...")
        args["prompt"] = f"Cinematic lunar cargo vista, glowing synthwave grid, atmospheric, no text. {DIR}"
        raw = await _handle_image_to_video(args)
        jid2 = (json.loads(raw).get("details") or {}).get("id")
        if jid2:
            open(jfile,"w").write(jid2)
            await submit_and_save(jid2)
asyncio.run(go())
PY
