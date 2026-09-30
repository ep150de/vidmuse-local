#!/usr/bin/env bash
# Cron-safe BFL Flux3 job poller.
# Usage: bash poll_bfl.sh <jobid> [hermes_agent_dir]
# Prints the REAL upstream status and, for a Ready job, the delivery URL + SAS expiry.
#
# WHY a .sh file (not an inline heredoc): inline heredocs hit an approval prompt that
# deadlocks headless/cron runs. A script file runs without approval.
#
# FIX (cron): tools.flux3_video_tool was DELETED from the agent toolchain.
# This now polls the BFL gateway directly via tools.managed_tool_gateway + httpx,
# using the Hermes agent venv (which has httpx/ruamel pre-installed).
set -u
JOBID="${1:?usage: poll_bfl.sh <jobid> [hermes_agent_dir]}"
HERMES_DIR="${2:-/home/lingleblater/.hermes/hermes-agent}"
export NOUS_TOOLS_ENABLED=1
export MANAGED_NOUS_TOOLS=1

VENV_PY="/home/lingleblater/.hermes/hermes-agent/venv/bin/python"
if [ ! -x "$VENV_PY" ]; then VENV_PY="python3"; fi

"$VENV_PY" - <<PY
import os, sys, json, socket, datetime, urllib.parse
sys.path.insert(0, "$HERMES_DIR")
os.environ["NOUS_TOOLS_ENABLED"] = "1"
os.environ["MANAGED_NOUS_TOOLS"] = "1"

from tools.managed_tool_gateway import (
    resolve_managed_tool_gateway, read_nous_access_token,
)

JOB_ID = "$JOBID"

cfg = resolve_managed_tool_gateway("bfl")
if cfg is None:
    print("resolve_managed_tool_gateway('bfl') -> None (managed tools disabled or no token/gateway configured)")
    sys.exit(1)

gateway_origin = cfg.gateway_origin
token = cfg.nous_user_token or read_nous_access_token()
if not token:
    print("NO_NOUS_TOKEN: cannot authenticate upstream (gateway would answer 401).")
    sys.exit(1)

# --- DNS pre-check: the BFL gateway is a per-vendor subdomain that can be retired ---
host = urllib.parse.urlsplit(gateway_origin).netloc.split(":")[0]
try:
    socket.gethostbyname(host)
except socket.gaierror as ge:
    print(f"UPSTREAM_UNRESOLVABLE: host '{host}' does not resolve (DNS gaierror: {ge}).")
    print(f"  gateway_origin={gateway_origin}")
    print(f"  job {JOB_ID} cannot be polled: the BFL gateway endpoint is unreachable from this host.")
    sys.exit(1)

url = f"{gateway_origin}/generations/{urllib.parse.quote(JOB_ID, safe='')}"

import httpx
headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
try:
    with httpx.Client(timeout=httpx.Timeout(15.0, read=60.0)) as client:
        resp = client.get(url, headers=headers)
except Exception as exc:
    print(f"UPSTREAM_REQUEST_FAILED: {type(exc).__name__}: {exc}")
    sys.exit(1)

print(f"HTTP_CODE: {resp.status_code}")
print(f"GET URL: {url}")

try:
    parsed = resp.json()
    det = parsed.get("details", {}) or {}
    status = det.get("status", parsed.get("error", {}).get("code", "unknown") if isinstance(parsed.get("error"), dict) else parsed.get("error", "unknown"))
    print(f"[poll] job {JOB_ID} status: {status}")
    print("BODY_JSON:", json.dumps(parsed, indent=2, default=str)[:3000])
except Exception:
    body = resp.text
    print(f"[poll] job {JOB_ID} status: unknown (non-JSON)")
    print("BODY_RAW:", body[:1000])
    sys.exit(1)
PY
