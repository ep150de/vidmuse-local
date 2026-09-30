#!/usr/bin/env bash
# render_inscribed_scene.sh <SCENE_INDEX> — render ONE scene for Inscribed in the Block
# Single performer: DJ Gifted Prophet (canonical look, locked). No female vocalist.
# Uses BFL text_to_video. Slow-mo scenes flagged in plan are slowed at ASSEMBLY.
set -u
cd /home/lingleblater/vidmuse-local
export NOUS_TOOLS_ENABLED=1
export MANAGED_NOUS_TOOLS=1
IDX="${1:?scene index}"
export IDX
SONG="/home/lingleblater/vidmuse-local/inscribed.mp3"
export SONG

server/.venv/bin/python - <<'PY'
import os, sys, asyncio, json
sys.path.insert(0, "/home/lingleblater/.hermes/hermes-agent")
os.environ["NOUS_TOOLS_ENABLED"]="1"; os.environ["MANAGED_NOUS_TOOLS"]="1"
from tools.flux3_video_tool import (_handle_text_to_video, _handle_get_result,
                                    _endpoints, _call_gateway, check_bfl_requirements)
assert check_bfl_requirements(), "BFL gated"

plan = json.load(open("analysis_inscribed/timeline_plan.json"))["scenes"]
i = int(os.environ["IDX"]); sc = plan[i]
role = sc["role"]; lyric = sc.get("lyric","")

# CANONICAL DJ GIFTED PROPHET (locked by explicit user recast)
PROPHET = ("DJ Gifted Prophet, a young Caucasian man with Ray-Ban nerdy glasses, "
           "a black baseball cap with white letters 'LL' on the front, "
           "wearing a light blue Lacoste polo and black pants with Nike Air Max shoes, "
           "dressed as a modern hip hop DJ")
LOOK = "modern hip hop music video, West Coast neon-decay and club-basement palette, graffiti wall, cinematic, 90s MTV grain"
NO_TXT = "NO text, NO captions, NO letters, NO words anywhere in frame"

if role == "DJ":
    prompt = f"{PROPHET} performing: {lyric}. Confident modern hip hop DJ energy, dancing and performing to the beat, {LOOK}. {NO_TXT}"
else:  # THEME
    prompt = (f"An LA scene illustrating: {lyric}. {PROPHET} present in frame, West Coast hip hop atmosphere, "
              f"{LOOK}. {NO_TXT}")

args = {
    "prompt": prompt,
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
            res = await _handle_get_result({"id": jid, "save_to": f"/home/lingleblater/vidmuse-local/ins_{i:02d}.mp4"})
            print("RESULT:", res[:160]); return True
        if st == "Request Moderated":
            print("MODERATED"); return False
        await asyncio.sleep(15)
    print("still generating — re-run: bash render_inscribed_scene.sh", i); return None

async def go():
    jfile = f"ins_{i}_jobid.txt"
    jid = open(jfile).read().strip() if os.path.exists(jfile) else None
    if not jid:
        raw = await _handle_text_to_video(args)
        jid = (json.loads(raw).get("details") or {}).get("id")
        if jid: open(jfile,"w").write(jid)
        else:
            print("submit no id; raw:", raw[:200]); return
    print(f"scene {i} [{role}] job: {jid}")
    ok = await submit_and_save(jid)
    if ok is False:
        args["prompt"] = (f"{PROPHET} in an LA street at dusk, West Coast energy, {LOOK}. {NO_TXT}")
        raw = await _handle_text_to_video(args)
        jid2 = (json.loads(raw).get("details") or {}).get("id")
        if jid2:
            open(jfile,"w").write(jid2)
            await submit_and_save(jid2)
asyncio.run(go())
PY
