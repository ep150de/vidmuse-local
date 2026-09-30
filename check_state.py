import sqlite3, os, json, time
from datetime import datetime

# Check Hermes state.db for any record of the job
db_path = "/home/lingleblater/.hermes/state.db"
job_id = "cb8dc6973dc8854b080c5cfdf5de95d5"
full_job = f"bfl_job_{job_id}"

print("=== Checking state.db ===")
if os.path.exists(db_path):
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
    tables = [t[0] for t in cursor.fetchall()]
    print(f"Tables: {tables}")

    for tname in tables:
        try:
            # Check if table has columns containing text
            cursor.execute(f"PRAGMA table_info({tname})")
            cols = cursor.fetchall()
            col_names = [c[1] for c in cols]
            
            # Search for job_id in text columns
            for col in col_names:
                try:
                    cursor.execute(f"SELECT * FROM {tname} WHERE {col} LIKE '%{job_id}%' OR {col} LIKE '%bfl_job_cb8%'")
                    rows = cursor.fetchall()
                    if rows:
                        print(f"  Found {len(rows)} rows in {tname}.{col} matching job_id")
                        for row in rows:
                            print(f"    Row: {dict(zip(col_names, row))}")
                except:
                    pass
            
            # Also check the full table for any bfl references
            try:
                cursor.execute(f"SELECT COUNT(*) FROM {tname}")
                count = cursor.fetchone()[0]
                print(f"  {tname}: {count} rows")
            except:
                pass
        except Exception as e:
            print(f"  {tname}: error - {e}")
    conn.close()
else:
    print(f"state.db not found at {db_path}")

# Check shared-state.db
print("\n=== Checking shared-state.db ===")
sdb_path = "/home/lingleblater/.hermes/shared-state.db"
if os.path.exists(sdb_path):
    conn = sqlite3.connect(sdb_path)
    cursor = conn.cursor()
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
    tables = [t[0] for t in cursor.fetchall()]
    print(f"Tables: {tables}")
    for tname in tables:
        try:
            cursor.execute(f"PRAGMA table_info({tname})")
            cols = cursor.fetchall()
            col_names = [c[1] for c in cols]
            for col in col_names:
                try:
                    cursor.execute(f"SELECT * FROM {tname} WHERE {col} LIKE '%{job_id}%'")
                    rows = cursor.fetchall()
                    if rows:
                        print(f"  Found {len(rows)} rows in {tname}.{col}")
                        for row in rows:
                            print(f"    Row: {dict(zip(col_names, row))}")
                except:
                    pass
            cursor.execute(f"SELECT COUNT(*) FROM {tname}")
            count = cursor.fetchone()[0]
            print(f"  {tname}: {count} rows")
        except Exception as e:
            print(f"  {tname}: error - {e}")
    conn.close()

# Check sessions directory
print("\n=== Checking sessions directory ===")
sessions_dir = "/home/lingleblater/.hermes/sessions"
if os.path.exists(sessions_dir):
    for root, dirs, files in os.walk(sessions_dir):
        for fname in files:
            if job_id in fname or "bfl" in fname.lower():
                fpath = os.path.join(root, fname)
                print(f"  Found: {fpath}")
