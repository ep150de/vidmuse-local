#!/usr/bin/env bash
# render_coc_scene.sh <SCENE_INDEX> — render ONE scene for Chain on the Canvas
# Two consistent characters: DJ Gifted Prophet (male, lighter skin) + female vocalist (femcee).
# Uses BFL text_to_video. Slow-mo scenes flagged in plan are slowed at ASSEMBLY.
set -u
cd /home/lingleblater/vidmuse-local
export NOUS_TOOLS_ENABLED=1
export MANAGED_NOUS_TOOLS=1
IDX="${1:?scene index}"
export IDX
SONG="/home/lingleblater/vidmuse-local/Chain on the Canvas.mp3"
export SONG

server/.venv/bin/python - <<'PY'
import os, sys, asyncio, json
sys.path.insert(0, "/home/lingleblater/.hermes/hermes-agent")
os.environ["NOUS_TOOLS_ENABLED"]="1"; os.environ["MANAGED_NOUS_TOOLS"]="1"
from tools.flux3_video_tool import (_handle_text_to_video, _handle_get_result,
                                    _endpoints, _call_gateway, check_bfl_requirements)
assert check_bfl_requirements(), "BFL gated"

plan = json.load(open("analysis_coc/timeline_plan.json"))["scenes"]
i = int(os.environ["IDX"]); sc = plan[i]
role = sc["role"]; lyric = sc.get("lyric","")

PROPHET = ("DJ Gifted Prophet, a young man with light olive skin, Ray-Ban nerdy glasses, a goatee, "
           "wearing a black baseball cap with white letters 'LL' on the front")
FEM = ("a young Black woman with bold curly hair, gold hoop earrings, "
       "wearing an oversized vintage graphic tee, West Coast femcee style")
LOOK = "West Coast neon-decay palette, golden-hour and night LA, 90s MTV music video grain, cinematic"
NO_TXT = "NO text, NO captions, NO letters, NO words anywhere in frame"

if role == "MALE":
    who = PROPHET
    prompt = f"{who} performing: {lyric}. Behind turntables / dancing / walking with confident DJ energy, {LOOK}. {NO_TXT}"
elif role == "FEM":
    who = FEM
    if "instrumental" in lyric or "break" in lyric:
        prompt = f"{FEM} in an LA scene: {lyric}, {LOOK}. {NO_TXT}"
    else:
        prompt = f"{who} performing/singing: {lyric}. Confident West Coast femcee energy, {LOOK}. {NO_TXT}"
else:  # GROUP
    prompt = (f"{PROPHET} and {FEM} together in an LA scene: {lyric}. West Coast duo, mural and canvas, "
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
            res = await _handle_get_result({"id": jid, "save_to": f"/home/lingleblater/vidmuse-local/coc_{i:02d}.mp4"})
            print("RESULT:", res[:160]); return True
        if st == "Request Moderated":
            print("MODERATED"); return False
        await asyncio.sleep(15)
    print("still generating — re-run: bash render_coc_scene.sh", i); return None

async def go():
    jfile = f"coc_{i}_jobid.txt"
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
