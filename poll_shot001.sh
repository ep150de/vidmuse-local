#!/usr/bin/env bash
# Poll the shot_001 job id found in shot_001_jobid.txt (real upstream status).
set -u
cd /home/lingleblater/vidmuse-local
export NOUS_TOOLS_ENABLED=1
export MANAGED_NOUS_TOOLS=1
server/.venv/bin/python - <<'PY'
import os, sys, json
sys.path.insert(0, "/home/lingleblater/.hermes/hermes-agent")
os.environ["NOUS_TOOLS_ENABLED"]="1"
os.environ["MANAGED_NOUS_TOOLS"]="1"
from tools.flux3_video_tool import _handle_get_result
import asyncio
jid = open("shot_001_jobid.txt").read().strip()
print("polling shot_001 job:", jid)
res = asyncio.run(_handle_get_result({"id": jid}))
parsed = res if isinstance(res, dict) else json.loads(res)
status = "unknown"
try:
    det = parsed.get("details", {}) or {}
    status = det.get("status", "unknown")
except Exception:
    pass
print(f"[shot_001] job status: {status}")
print(json.dumps(parsed, indent=2, default=str)[:800])
PY
