#!/usr/bin/env bash
# Dump the FULL shot_001 job result JSON so we can inspect the samples/video URL + SAS expiry.
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
res = asyncio.run(_handle_get_result({"id": jid}))
parsed = res if isinstance(res, dict) else json.loads(res)
print(json.dumps(parsed, indent=2, default=str))
PY
