#!/usr/bin/env bash
# render_gpc_scene.sh <SCENE_INDEX> — render ONE scene for Gifted Prophet on the Chain
# Star: DJ Gifted Prophet (described in text, no reference image).
# Uses BFL text_to_video. Slow-mo scenes flagged in plan are slowed at ASSEMBLY.
set -u
cd /home/lingleblater/vidmuse-local
export NOUS_TOOLS_ENABLED=1
export MANAGED_NOUS_TOOLS=1
IDX="${1:?scene index}"
export IDX
SONG="/home/lingleblater/vidmuse-local/Gifted Prophet on the Chain.mp3"
export SONG

server/.venv/bin/python - <<'PY'
import os, sys, asyncio, json
sys.path.insert(0, "/home/lingleblater/.hermes/hermes-agent")
os.environ["NOUS_TOOLS_ENABLED"]="1"; os.environ["MANAGED_NOUS_TOOLS"]="1"
from tools.flux3_video_tool import (_handle_text_to_video, _handle_get_result,
                                    _endpoints, _call_gateway, check_bfl_requirements)
assert check_bfl_requirements(), "BFL gated"

plan = json.load(open("analysis_gpc/timeline_plan.json"))["scenes"]
i = int(os.environ["IDX"]); sc = plan[i]
stype = sc["type"]; lyric = sc.get("lyric","")

# Consistent character description so DJ Gifted Prophet looks the same every scene.
CHAR = ("DJ Gifted Prophet, a young man with olive skin, Ray-Ban nerdy glasses, a goatee, "
        "wearing a black baseball cap with white letters 'LL' on the front")
LOOK = "West Coast neon-decay palette, golden-hour and night LA, 90s MTV music video grain, cinematic"
NO_TXT = "NO text, NO captions, NO letters, NO words anywhere in frame"

if stype == "PERF":
    prompt = (f"{CHAR} performing: {lyric}. Behind turntables / dancing / walking with confident DJ energy, "
              f"{LOOK}. {NO_TXT}")
elif stype == "THEME":
    prompt = (f"{CHAR} in an LA scene: {lyric}. {LOOK}. West Coast streets, park bench, lowrider, "
              f"palm trees, glowing digital screens, engraved ledger, neon. {NO_TXT}")
else:  # OUTRO
    prompt = (f"{CHAR} at the DJ decks in a final pose, warm pink-orange LA sunset behind, "
              f"90s MTV ending shot. {NO_TXT}")

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
            res = await _handle_get_result({"id": jid, "save_to": f"/home/lingleblater/vidmuse-local/gpc_{i:02d}.mp4"})
            print("RESULT:", res[:160]); return True
        if st == "Request Moderated":
            print("MODERATED"); return False
        await asyncio.sleep(15)
    print("still generating — re-run: bash render_gpc_scene.sh", i); return None

async def go():
    jfile = f"gpc_{i}_jobid.txt"
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
        args["prompt"] = (f"{CHAR} in an LA street at dusk, West Coast DJ energy, {LOOK}. {NO_TXT}")
        raw = await _handle_text_to_video(args)
        jid2 = (json.loads(raw).get("details") or {}).get("id")
        if jid2:
            open(jfile,"w").write(jid2)
            await submit_and_save(jid2)
asyncio.run(go())
PY
