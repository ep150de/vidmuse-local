"""Extract EXACT delivery URL + result JSON for the Ready message (id 9188),
and stream-inspect video.mp4. File-based (cron-safe)."""
import sqlite3, json, subprocess, os
db_path = "/home/lingleblater/.hermes/state.db"
conn = sqlite3.connect(db_path)
cur = conn.cursor()

# Get full content of the Ready poll message (id 9188) and the surrounding tool msgs
for mid in (9188,):
    row = cur.execute("SELECT id, session_id, role, timestamp, content FROM messages WHERE id=?", (mid,)).fetchone()
    if row:
        msg_id, session_id, role, ts, content = row
        print(f"=== message id={msg_id} session={session_id} role={role} ts={ts} ===")
        try:
            d = json.loads(content)
            out = d.get("output", "")
            print("RAW output:\n", out)
        except Exception:
            print("RAW:", content[:2000])

# Also find the row whose content has the cleanest "status":"Ready" + "samples" with delivery URL
print("\n=== rows with 'status':'Ready' for this job ===")
rows = cur.execute(
    "SELECT id, timestamp, content FROM messages WHERE content LIKE ? AND content LIKE '%\"status\":\"Ready\"%'",
    ("%bfl_job_cb8dc6973dc8854b080c5cfdf5de95d5%",)
).fetchall()
print(f"count={len(rows)}")
for r in rows:
    mid, ts, content = r
    print(f"\n--- id={mid} ts={ts} ---")
    print(content[:3000])

conn.close()

# Stream inspect the local clip
clip = "/home/lingleblater/vidmuse-local/video.mp4"
print("\n=== ffprobe streams for video.mp4 ===")
if os.path.exists(clip):
    r = subprocess.run(["ffprobe","-v","error","-show_entries",
        "stream=index,codec_type,codec_name,width,height,nb_frames,duration","format=duration,size",
        "-of","json", clip], capture_output=True, text=True, timeout=60)
    print(r.stdout)
    if r.stderr: print("STDERR:", r.stderr[:500])
else:
    print("clip missing")
