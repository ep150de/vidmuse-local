import sqlite3, json, datetime
db_path = "/home/lingleblater/.hermes/state.db"
conn = sqlite3.connect(db_path)
cursor = conn.cursor()

# Full text of the earlier comprehensive report (message 39566)
for mid in (39566,):
    cursor.execute("SELECT id, session_id, role, timestamp, content FROM messages WHERE id=?", (mid,))
    row = cursor.fetchone()
    if row:
        msg_id, session_id, role, ts, content = row
        print(f"=== MESSAGE id={msg_id} session={session_id} role={role} ===")
        try:
            data = json.loads(content)
            print(data.get("output", content))
        except Exception:
            print(content)
        print()

# Also pull the original generation submit + first Generating polls from session 20260731
print("=== Generation-time gateway responses (session 20260731_000440_cbf4c3) ===")
cursor.execute("""
    SELECT id, session_id, role, timestamp, content
    FROM messages
    WHERE content LIKE '%bfl_job_cb8dc6973dc8854b080c5cfdf5de95d5%'
      AND session_id = '20260731_000440_cbf4c3'
    ORDER BY timestamp ASC
""")
for row in cursor.fetchall():
    msg_id, session_id, role, ts, content = row
    t = datetime.datetime.fromtimestamp(ts, tz=datetime.timezone.utc) if ts else "N/A"
    print(f"--- id={msg_id} role={role} ts={t} ---")
    try:
        data = json.loads(content)
        out = data.get("output", content)
        print(out[:1500])
    except Exception:
        print(content[:1500])
    print()
conn.close()
