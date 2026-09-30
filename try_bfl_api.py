import json, os, sys, socket, urllib.parse
import httpx

# Try the correct BFL API endpoint format
JOB_ID = "cb8dc6973dc8854b080c5cfdf5de95d5"

# BFL API uses GET with query params: api_key and id
# https://api.bfl.ai/v2/get_result?api_key=YOUR_API_KEY&id=JOB_ID
# Try with the job_id only (no key) to see what error we get
for endpoint in ["https://api.bfl.ai/v2/get_result"]:
    params = {"id": JOB_ID}
    try:
        with httpx.Client(timeout=15) as client:
            resp = client.get(endpoint, params=params)
        print(f"GET {endpoint}?id={JOB_ID} -> {resp.status_code}")
        print(f"  Body: {resp.text[:800]}")
    except Exception as e:
        print(f"ERROR: {type(e).__name__}: {e}")

# Also try POST format
for endpoint in ["https://api.bfl.ai/v2/get_result"]:
    body = {"id": JOB_ID}
    try:
        with httpx.Client(timeout=15) as client:
            resp = client.post(endpoint, json=body)
        print(f"\nPOST {endpoint} json={body} -> {resp.status_code}")
        print(f"  Body: {resp.text[:800]}")
    except Exception as e:
        print(f"ERROR: {type(e).__name__}: {e}")

# Try the Nous gateway with different path formats
nous_token = None
sys.path.insert(0, "/home/lingleblater/.hermes/hermes-agent")
os.environ["NOUS_TOOLS_ENABLED"] = "1"
os.environ["MANAGED_NOUS_TOOLS"] = "1"
from tools.managed_tool_gateway import read_nous_access_token
nous_token = read_nous_access_token()

print(f"\n--- Nous gateway attempts ---")
for path in ["/generations/", "/v1/generations/", "/api/generations/", "/bfl/generations/"]:
    url = f"https://gateway.nousresearch.com{path}{JOB_ID}"
    headers = {"Authorization": f"Bearer {nous_token}"} if nous_token else {}
    try:
        with httpx.Client(timeout=15) as client:
            resp = client.get(url, headers=headers)
        print(f"GET {url} -> {resp.status_code}: {resp.text[:300]}")
    except Exception as e:
        print(f"GET {url} -> ERROR: {type(e).__name__}: {e}")
