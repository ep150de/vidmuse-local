#!/usr/bin/env bash
# render_oco_scene.sh <SCENE_INDEX> — render ONE scene for On-Chain OG
# West Coast classic hip-hop MTV concept: a crew c-walking in LA streets,
# no reference singer. Uses BFL text_to_video (no input image needed).
# Lyrics drive the THEME (visual meaning), NO on-screen text ever.
set -u
cd /home/lingleblater/vidmuse-local
export NOUS_TOOLS_ENABLED=1
export MANAGED_NOUS_TOOLS=1
IDX="${1:?scene index}"
export IDX
SONG="/home/lingleblater/vidmuse-local/On-Chain OG.mp3"
export SONG

server/.venv/bin/python - <<'PY'
import os, sys, asyncio, json
sys.path.insert(0, "/home/lingleblater/.hermes/hermes-agent")
os.environ["NOUS_TOOLS_ENABLED"]="1"; os.environ["MANAGED_NOUS_TOOLS"]="1"
from tools.flux3_video_tool import (_handle_text_to_video, _handle_get_result,
                                    _endpoints, _call_gateway, check_bfl_requirements)
assert check_bfl_requirements(), "BFL gated"

plan = json.load(open("analysis_oco/timeline_plan.json"))["scenes"]
i = int(os.environ["IDX"]); sc = plan[i]
stype = sc["type"]; lyric = sc.get("lyric","")

# West Coast hip-hop visual language, per scene type.
CREW = "a Black and Brown street dance crew c-walking and performing in the streets of Los Angeles"
LOOK = "90s MTV music video, golden-hour sundown LA, palm trees, lowriders, gritty film grain, neon-decay palette, cinematic"
NO_TXT = "NO text, NO captions, NO letters, NO words anywhere in frame"

if stype == "PERF":
    prompt = (f"{CREW}, mid c-walk dance move to the beat, confident poses, baggy jeans, white tees, low caps, "
              f"{LOOK}. Thematic: {lyric}. {NO_TXT}")
elif stype == "THEME":
    prompt = (f"{CREW} in an LA street scene: {lyric}. {LOOK}. West Coast neighborhood, "
              f"boomboxes, murals, swap meets, street corner life. {NO_TXT}")
else:  # OUTRO
    prompt = (f"{CREW} striking a final group pose at sundown, Los Angeles skyline and palm trees behind, "
              f"warm pink-orange sky, 90s MTV ending shot. {NO_TXT}")

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
            res = await _handle_get_result({"id": jid, "save_to": f"/home/lingleblater/vidmuse-local/oco_{i:02d}.mp4"})
            print("RESULT:", res[:160]); return True
        if st == "Request Moderated":
            print("MODERATED"); return False
        await asyncio.sleep(15)
    print("still generating — re-run: bash render_oco_scene.sh", i); return None

async def go():
    jfile = f"oco_{i}_jobid.txt"
    jid = open(jfile).read().strip() if os.path.exists(jfile) else None
    if not jid:
        raw = await _handle_text_to_video(args)
        jid = (json.loads(raw).get("details") or {}).get("id")
        if jid: open(jfile,"w").write(jid)
        else:
            print("submit no id; raw:", raw[:200]); return
    print(f"scene {i} [{stype}] job: {jid}")
    ok = await submit_and_save(jid)
    if ok is False:
        # retry with a safer thematic prompt (never silently change concept)
        print("retry with safer prompt...")
        args["prompt"] = (f"{CREW} dancing in an LA street at dusk, West Coast hip-hop energy, {LOOK}. {NO_TXT}")
        raw = await _handle_text_to_video(args)
        jid2 = (json.loads(raw).get("details") or {}).get("id")
        if jid2:
            open(jfile,"w").write(jid2)
            await submit_and_save(jid2)
asyncio.run(go())
PY
