import sqlite3, json, datetime
db_path = "/home/lingleblater/.hermes/state.db"
job_id = "bfl_job_cb8dc6973dc8854b080c5cfdf5de95d5"
conn = sqlite3.connect(db_path)
cursor = conn.cursor()
cursor.execute("""
    SELECT id, session_id, role, timestamp, content
    FROM messages
    WHERE content LIKE ?
    ORDER BY timestamp DESC
    LIMIT 6
""", (f"%{job_id}%",))
rows = cursor.fetchall()
print(f"=== Most recent {len(rows)} messages mentioning job_id ===\n")
for row in rows:
    msg_id, session_id, role, timestamp, content = row
    ts = datetime.datetime.fromtimestamp(timestamp, tz=datetime.timezone.utc) if timestamp else "N/A"
    print(f"--- id={msg_id}, session={session_id}, role={role}, ts={ts} ---")
    try:
        data = json.loads(content)
        out = data.get("output", content)
        print(f"OUTPUT:\n{out[:1500]}")
    except:
        print(f"RAW:\n{content[:1500]}")
    print()
conn.close()
