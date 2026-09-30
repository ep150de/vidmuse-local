import json, os, sys, socket, urllib.parse
import httpx

sys.path.insert(0, "/home/lingleblater/.hermes/hermes-agent")
os.environ["NOUS_TOOLS_ENABLED"] = "1"
os.environ["MANAGED_NOUS_TOOLS"] = "1"

from tools.managed_tool_gateway import read_nous_access_token

JOB_ID = "cb8dc6973dc8854b080c5cfdf5de95d5"
token = read_nous_access_token()

print(f"Nous token: {'SET' if token else 'None'}")
if token:
    print(f"Token length: {len(token)}")

# Approach 1: Try the general Nous gateway with BFL generations endpoint
# bfl-gateway.nousresearch.com is dead, try gateway.nousresearch.com
for gateway in [
    "https://gateway.nousresearch.com",
    "https://api.bfl.ai",
    "https://api.bfl.ai/v2/get_result",
]:
    host = urllib.parse.urlsplit(gateway).netloc.split(":")[0]
    try:
        socket.gethostbyname(host)
        print(f"\nDNS OK: {host}")
    except socket.gaierror:
        print(f"\nDNS FAIL: {host}")
        continue

    headers = {"Authorization": f"Bearer {token}" if token else "", "Content-Type": "application/json"}

    if gateway.startswith("https://api.bfl.ai/v2/get_result"):
        # BFL API format: POST with {'id': job_id}
        try:
            with httpx.Client(timeout=15) as client:
                resp = client.post(gateway, json={"id": JOB_ID}, headers=headers)
            print(f"  POST {gateway}/get_result -> {resp.status_code}")
            print(f"  Body: {resp.text[:500]}")
        except Exception as e:
            print(f"  POST {gateway}/get_result -> ERROR: {type(e).__name__}: {e}")
    elif "api.bfl.ai" in gateway:
        # Try BFL GET result endpoint
        url = f"{gateway}/v2/get_result"
        try:
            with httpx.Client(timeout=15) as client:
                resp = client.post(url, json={"id": JOB_ID}, headers={"Content-Type": "application/json"})
            print(f"  POST {url} (no auth) -> {resp.status_code}")
            print(f"  Body: {resp.text[:500]}")
        except Exception as e:
            print(f"  POST {url} -> ERROR: {type(e).__name__}: {e}")
    else:
        url = f"{gateway}/generations/{urllib.parse.quote(JOB_ID, safe='')}"
        try:
            with httpx.Client(timeout=15) as client:
                resp = client.get(url, headers=headers)
            print(f"  GET {url} -> {resp.status_code}")
            print(f"  Body: {resp.text[:500]}")
        except Exception as e:
            print(f"  GET {url} -> ERROR: {type(e).__name__}: {e}")

# Approach 2: Try BFL API directly (they have a get_result endpoint)
print("\n--- BFL direct API (api.bfl.ai) ---")
try:
    with httpx.Client(timeout=15) as client:
        resp = client.get(f"https://api.bfl.ai/v2/result?id={JOB_ID}")
    print(f"GET api.bfl.ai/v2/result?id={JOB_ID} -> {resp.status_code}")
    print(f"Body: {resp.text[:500]}")
except Exception as e:
    print(f"GET api.bfl.ai/v2/result -> ERROR: {type(e).__name__}: {e}")
