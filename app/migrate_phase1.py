"""
Phase 1 DB migration — run this on VPS:
  python3 app/migrate_phase1.py
"""
import sqlite3, os

DB = os.path.join(os.path.dirname(__file__), '..', 'mutech.db')

def migrate(db_path):
    conn = sqlite3.connect(db_path)
    def safe(sql):
        try:
            conn.execute(sql)
            conn.commit()
            print(f"  ✓ {sql[:60]}")
        except Exception as e:
            if "duplicate column" in str(e).lower() or "already exists" in str(e).lower():
                print(f"  ○ already exists: {sql[:60]}")
            else:
                print(f"  ✗ {e}: {sql[:60]}")

    print("── Phase 1 Migration ──────────────────────")

    # tenants
    safe("ALTER TABLE tenants ADD COLUMN minutes_limit INTEGER DEFAULT 0")

    # leads
    safe("ALTER TABLE leads ADD COLUMN retry_count INTEGER DEFAULT 0")
    safe("ALTER TABLE leads ADD COLUMN next_retry_at TEXT DEFAULT NULL")

    # usage_logs
    safe("ALTER TABLE usage_logs ADD COLUMN alert_sent TEXT DEFAULT NULL")

    # addon_purchases — create if missing, then ensure each column exists
    # (CREATE TABLE IF NOT EXISTS is a no-op on legacy rows with the older schema,
    #  so we follow up with explicit ALTERs for the columns added in Phase 1.)
    safe("""CREATE TABLE IF NOT EXISTS addon_purchases (
        id           INTEGER PRIMARY KEY AUTOINCREMENT,
        tenant_id    INTEGER REFERENCES tenants(id),
        addon_type   TEXT,
        minutes      INTEGER DEFAULT 0,
        amount       REAL DEFAULT 0,
        purchased_at TEXT DEFAULT (datetime('now')),
        notes        TEXT
    )""")
    safe("ALTER TABLE addon_purchases ADD COLUMN addon_type   TEXT")
    safe("ALTER TABLE addon_purchases ADD COLUMN amount       REAL DEFAULT 0")
    safe("ALTER TABLE addon_purchases ADD COLUMN purchased_at TEXT DEFAULT (datetime('now'))")

    # seed minutes_limit from plan
    for plan, limit in [("starter",1000),("growth",2500),("pro",5000)]:
        conn.execute(
            "UPDATE tenants SET minutes_limit=? WHERE plan=? AND minutes_limit=0",
            (limit, plan)
        )
    conn.commit()
    print("  ✓ minutes_limit seeded from plan")
    print("── Done ───────────────────────────────────")
    conn.close()

if __name__ == "__main__":
    path = os.environ.get("DB_PATH", "/root/ai-caller-dev/mutech.db")
    migrate(path)
