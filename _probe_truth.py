"""Authoritative, minimal probe of state.db for THIS job's status progression.
Resolves the inspect_state.py vs check_state.py contradiction. Written as a file (no -c approval)."""
import sqlite3, json, datetime
db_path = "/home/lingleblater/.hermes/state.db"
job_id = "bfl_job_cb8dc6973dc8854b080c5cfdf5de95d5"
uuid_id = "cb8dc6973dc8854b080c5cfdf5de95d5"

conn = sqlite3.connect(db_path)
conn.row_factory = sqlite3.Row
cur = conn.cursor()

# 1) tables
tabs = [r[0] for r in cur.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
print("TABLES:", tabs)

# 2) job messages, ordered by timestamp, with role + parsed status
rows = cur.execute(
    "SELECT id, session_id, role, timestamp, content FROM messages "
    "WHERE content LIKE ? ORDER BY timestamp ASC", (f"%{uuid_id}%",)
).fetchall()
print(f"\nFOUND {len(rows)} message rows mentioning the job UUID\n")

for r in rows:
    ts = datetime.datetime.fromtimestamp(r["timestamp"], tz=datetime.timezone.utc) if r["timestamp"] else "N/A"
    # try parse JSON content
    status = None
    details_status = None
    result_snippet = ""
    try:
        d = json.loads(r["content"])
        out = d.get("output", "") or ""
        # scrape status from the tool-call output text
        result_snippet = out[:400]
        # look for "status":"X" in output
        for key in ("status",):
            import re
            m = re.search(r'"status"\s*:\s*"([^"]+)"', out)
            if m:
                details_status = m.group(1)
    except Exception:
        result_snippet = str(r["content"])[:400]
    print(f"id={r['id']} session={r['session_id']} role={r['role']} ts={ts} status={details_status}")
    print(f"   out: {result_snippet}")
    print()

conn.close()
