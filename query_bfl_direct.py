import json, httpx

JOB_ID = "cb8dc6973dc8854b080c5cfdf5de95d5"

# BFL API: GET /v1/get_result?id=<job_id> - NO auth required for get_result
url = f"https://api.bfl.ai/v1/get_result"
params = {"id": JOB_ID}

print(f"GET {url}?id={JOB_ID}")
try:
    with httpx.Client(timeout=30) as client:
        resp = client.get(url, params=params)
    print(f"HTTP Code: {resp.status_code}")
    print(f"Body: {resp.text[:2000]}")
    
    if resp.status_code == 200:
        data = resp.json()
        status = data.get("status", "unknown")
        print(f"\n=== STATUS: {status} ===")
        if status == "Ready":
            result = data.get("result", {})
            details = data.get("details", {})
            preview = data.get("preview", {})
            print(f"Result: {json.dumps(result, indent=2)[:1000]}")
            print(f"Details: {json.dumps(details, indent=2)[:1000]}")
            print(f"Preview: {json.dumps(preview, indent=2)[:1000]}")
        elif status == "Task not found":
            print("Job ID not found in BFL's system (may have been submitted via Nous gateway with a different internal ID)")
except Exception as e:
    print(f"ERROR: {type(e).__name__}: {e}")
