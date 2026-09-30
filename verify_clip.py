import json, os, httpx
import sys
sys.path.insert(0, "/home/lingleblater/.hermes/hermes-agent")
os.environ["NOUS_TOOLS_ENABLED"] = "1"
os.environ["MANAGED_NOUS_TOOLS"] = "1"

JOB_ID = "cb8dc6973dc8854b080c5cfdf5de95d5"

# Check the delivery URL from the state.db record
delivery_url = "https://delivery.isr.bfl.ai/durable/2026080102/cf06c5a7-c303-4c80-a5da"
print(f"Checking delivery URL: {delivery_url}")
try:
    with httpx.Client(timeout=20, follow_redirects=True) as client:
        resp = client.head(delivery_url, headers={"Authorization": f"Bearer {os.environ.get('NOUS_TOKEN', '')}"})
    print(f"HEAD -> {resp.status_code}, content-type: {resp.headers.get('content-type', 'unknown')}")
    print(f"content-length: {resp.headers.get('content-length', 'unknown')}")
except Exception as e:
    print(f"HEAD -> ERROR: {type(e).__name__}: {e}")

# Also try without auth - the URL might be a pre-signed URL
try:
    with httpx.Client(timeout=20, follow_redirects=True) as client:
        resp = client.get(delivery_url + "_", headers={"Range": "bytes=0-100"})
    print(f"GET (first 100 bytes) -> {resp.status_code}")
    print(f"Body first 100: {resp.content[:100]}")
except Exception as e:
    print(f"GET -> ERROR: {type(e).__name__}: {e}")

# Verify the local saved clip
clip_path = "/home/lingleblater/vidmuse-local/video.mp4"
print(f"\n=== Local saved clip: {clip_path} ===")
if os.path.exists(clip_path):
    stat = os.stat(clip_path)
    print(f"Exists: True")
    print(f"Size: {stat.st_size} bytes ({stat.st_size / 1024 / 1024:.2f} MB)")
    print(f"Modified: {stat.st_mtime}")
    # Check if it starts with MP4 magic bytes
    with open(clip_path, "rb") as f:
        magic = f.read(16)
    print(f"Magic bytes: {magic.hex()}")
    is_mp4 = magic[:4] == b'\x00\x00\x00' and b'ftyp' in magic[4:8]
    print(f"Is MP4 (ftyp): {is_mp4}")
else:
    print(f"Exists: False")
