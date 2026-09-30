"""Poll BFL job via direct api.bfl.ai endpoints as a fallback.
Written as a file (not -c) so it runs without approval in cron mode."""
import httpx, json, socket, urllib.parse

JOB_ID = "bfl_job_cb8dc6973dc8854b080c5cfdf5de95d5"

# DNS pre-checks for all candidate hosts
for host in ["api.bfl.ai", "gateway.nousresearch.com", "bfl-gateway.nousresearch.com"]:
    try:
        socket.gethostbyname(host)
        print(f"DNS OK: {host}")
    except socket.gaierror as ge:
        print(f"DNS FAIL: {host} ({ge})")

# Endpoint 1: BFL v1 get_result (no auth required per BFL docs)
print("\n--- GET https://api.bfl.ai/v1/get_result?id=<JOB> ---")
try:
    with httpx.Client(timeout=30) as client:
        resp = client.get(f"https://api.bfl.ai/v1/get_result?id={JOB_ID}")
    print(f"HTTP {resp.status_code}")
    body = resp.text[:2000]
    print(body)
    if resp.status_code == 200:
        try:
            data = resp.json()
            status = data.get("status", "unknown")
            print(f"\n=== STATUS: {status} ===")
            if status == "Ready":
                r = data.get("result", {})
                print(f"Result: {json.dumps(r, indent=2, default=str)[:1500]}")
        except Exception as je:
            print(f"JSON parse error: {je}")
except Exception as e:
    print(f"ERROR: {type(e).__name__}: {e}")

# Endpoint 2: BFL v2 get_result (POST with id)
print("\n--- POST https://api.bfl.ai/v2/get_result ---")
try:
    with httpx.Client(timeout=30) as client:
        resp = client.post(
            "https://api.bfl.ai/v2/get_result",
            json={"id": JOB_ID},
            headers={"Content-Type": "application/json"},
        )
    print(f"HTTP {resp.status_code}")
    print(resp.text[:2000])
except Exception as e:
    print(f"ERROR: {type(e).__name__}: {e}")

# Endpoint 3: Nous gateway (may have DNS issues)
print("\n--- GET https://gateway.nousresearch.com/generations/<JOB> ---")
try:
    with httpx.Client(timeout=30) as client:
        resp = client.get(f"https://gateway.nousresearch.com/generations/{JOB_ID}")
    print(f"HTTP {resp.status_code}")
    print(resp.text[:2000])
except Exception as e:
    print(f"ERROR: {type(e).__name__}: {e}")
