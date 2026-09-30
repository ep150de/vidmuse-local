"""Probe BFL API directly using venv Python (has httpx). Query both ID forms."""
import httpx, json, socket, urllib.parse, sys, os

# The job as given, and the UUID-only form
FULL_ID = "bfl_job_cb8dc6973dc8854b080c5cfdf5de95d5"
UUID_ID = "cb8dc6973dc8854b080c5cfdf5de95d5"

print("=== DNS pre-check ===", flush=True)
for host in ["bfl-gateway.nousresearch.com", "gateway.nousresearch.com", "api.bfl.ai"]:
    try:
        ip = socket.gethostbyname(host)
        print(f"  DNS OK: {host} -> {ip}", flush=True)
    except socket.gaierror as ge:
        print(f"  DNS FAIL: {host} ({ge})", flush=True)

def probe_get(url, params=None, headers=None, label=""):
    try:
        with httpx.Client(timeout=httpx.Timeout(15.0, read=45.0), follow_redirects=False) as c:
            r = c.get(url, params=params, headers=headers)
        print(f"\n[{label}] GET {url} params={params}", flush=True)
        print(f"  HTTP {r.status_code}", flush=True)
        body = r.text[:2000]
        print(f"  Body: {body}", flush=True)
        return r
    except Exception as e:
        print(f"\n[{label}] GET {url} ERROR: {type(e).__name__}: {e}", flush=True)
        return None

def probe_post(url, payload=None, headers=None, label=""):
    try:
        with httpx.Client(timeout=httpx.Timeout(15.0, read=45.0), follow_redirects=False) as c:
            r = c.post(url, json=payload, headers=headers)
        print(f"\n[{label}] POST {url} json={payload}", flush=True)
        print(f"  HTTP {r.status_code}", flush=True)
        print(f"  Body: {r.text[:2000]}", flush=True)
        return r
    except Exception as e:
        print(f"\n[{label}] POST {url} ERROR: {type(e).__name__}: {e}", flush=True)
        return None

# Try both ID forms on multiple BFL endpoints
for idform, idval in [("full", FULL_ID), ("uuid", UUID_ID)]:
    probe_get("https://api.bfl.ai/v1/get_result", params={"id": idval}, label=f"BFL-v1-{idform}")
    probe_post("https://api.bfl.ai/v2/get_result", payload={"id": idval}, label=f"BFL-v2-{idform}")
    probe_get("https://api.bfl.ai/v2/get_result", params={"id": idval}, label=f"BFL-v2-get-{idform}")

# Try Nous general gateway (resolves) with both ID forms
nous_token = None
try:
    sys.path.insert(0, "/home/lingleblater/.hermes/hermes-agent")
    os.environ["NOUS_TOOLS_ENABLED"] = "1"
    os.environ["MANAGED_NOUS_TOOLS"] = "1"
    from tools.managed_tool_gateway import resolve_managed_tool_gateway, read_nous_access_token
    cfg = resolve_managed_tool_gateway("bfl")
    print(f"\n=== Nous gateway cfg: {cfg.gateway_origin if cfg else None} ===", flush=True)
    nous_token = read_nous_access_token() if cfg else None
    print(f"  Nous token present: {'YES' if nous_token else 'NO'}", flush=True)
except Exception as e:
    print(f"\n  Nous gateway resolution error: {type(e).__name__}: {e}", flush=True)

for idform, idval in [("full", FULL_ID), ("uuid", UUID_ID)]:
    headers = {"Authorization": f"Bearer {nous_token}"} if nous_token else {}
    if "Content-Type" not in (headers or {}):
        headers["Content-Type"] = "application/json"
    probe_get("https://gateway.nousresearch.com/generations/" + urllib.parse.quote(idval, safe=""),
              headers=headers, label=f"Nous-gateway-{idform}")

sys.exit(0)
