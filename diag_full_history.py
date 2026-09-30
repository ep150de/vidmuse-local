import json, os, sys, sqlite3
import datetime

db_path = "/home/lingleblater/.hermes/state.db"
job_id = "cb8dc6973dc8854b080c5cfdf5de95d5"

conn = sqlite3.connect(db_path)
cursor = conn.cursor()

# Search for all messages containing the job_id, ordered by timestamp
cursor.execute("""
    SELECT id, session_id, role, timestamp, content
    FROM messages
    WHERE content LIKE ?
    ORDER BY timestamp ASC
""", (f"%{job_id}%",))

rows = cursor.fetchall()
print(f"Found {len(rows)} messages mentioning job_id\n")

for i, row in enumerate(rows):
    msg_id, session_id, role, timestamp, content = row
    ts = datetime.datetime.fromtimestamp(timestamp, tz=datetime.timezone.utc) if timestamp else "N/A"
    print(f"--- Message {i+1}: id={msg_id}, session={session_id}, role={role}, ts={ts} ---")
    
    # Try to parse content as JSON
    try:
        data = json.loads(content)
        output = data.get("output", "")
        if output:
            print(f"OUTPUT (first 500 chars):")
            print(output[:500])
        else:
            print(f"CONTENT (first 500 chars):")
            print(content[:500])
    except:
        print(f"RAW (first 500 chars):")
        print(content[:500])
    print()

conn.close()
