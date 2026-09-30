"""Inspect state.db schema + any rows mentioning our job id. Run as file (no -c approval)."""
import sqlite3, json, datetime, datetime as dt
db_path = "/home/lingleblater/.hermes/state.db"
job_id = "bfl_job_cb8dc6973dc8854b080c5cfdf5de95d5"
conn = sqlite3.connect(db_path)
conn.row_factory = sqlite3.Row
cur = conn.cursor()
print("=== TABLES ===")
for name, in cur.execute("SELECT name FROM sqlite_master WHERE type='table'"):
    cnt = cur.execute(f"SELECT COUNT(*) FROM {name}").fetchone()[0]
    print(f"  {name}: {cnt} rows")
print("\n=== schema per table ===")
for name, in cur.execute("SELECT name FROM sqlite_master WHERE type='table'"):
    print(f"--- {name} ---")
    print(cur.execute("SELECT sql FROM sqlite_master WHERE name=?", (name,)).fetchone()[0])
# scan all text-ish columns for the job id
print("\n=== scans for job id across all tables/columns ===")
for name, in cur.execute("SELECT name FROM sqlite_master WHERE type='table'"):
    cols = cur.execute(f"PRAGMA table_info({name})").fetchall()
    text_cols = [c["name"] for c in cols]
    for col in text_cols:
        try:
            rows = cur.execute(
                f"SELECT rowid, {col} FROM {name} WHERE CAST({col} AS TEXT) LIKE ?", (f"%{job_id}%",)
            ).fetchall()
            if rows:
                print(f"\n[{name}.{col}] -> {len(rows)} matches")
                for r in rows[:20]:
                    val = r[col]
                    try:
                        val = json.loads(val)
                        s = json.dumps(val, default=str)
                    except Exception:
                        s = str(val)
                    print(f"  rowid={r['rowid']} ts-n/a: {s[:800]}")
        except Exception as e:
            pass
conn.close()
