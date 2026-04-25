"""
app/migrate_phase3.py
Reseller tier DB migration — run once on VPS:
  python3 app/migrate_phase3.py
"""
import sqlite3, os, secrets

DB = os.environ.get("DB_PATH", "/root/ai-caller-dev/mutech.db")

def migrate(db_path):
    conn = sqlite3.connect(db_path)
    def safe(sql, label=""):
        try:
            conn.execute(sql)
            conn.commit()
            print(f"  ✓ {label or sql[:55]}")
        except Exception as e:
            if "duplicate column" in str(e).lower() or "already exists" in str(e).lower():
                print(f"  ○ already exists: {label or sql[:55]}")
            else:
                print(f"  ✗ {e}")

    print("── Phase 3 Migration ──────────────────────────")

    # ── resellers table ──────────────────────────────────
    safe("""CREATE TABLE IF NOT EXISTS resellers (
        id                INTEGER PRIMARY KEY AUTOINCREMENT,
        name              TEXT NOT NULL,
        slug              TEXT UNIQUE NOT NULL,
        email             TEXT UNIQUE NOT NULL,
        password_hash     TEXT NOT NULL,
        plan              TEXT DEFAULT 'silver',
        status            TEXT DEFAULT 'active',
        -- Limits
        max_numbers       INTEGER DEFAULT 5,
        max_channels      INTEGER DEFAULT 3,
        max_concurrent    INTEGER DEFAULT 15,
        max_tenants       INTEGER DEFAULT 15,
        minutes_included  INTEGER DEFAULT 10000,
        minutes_used      REAL DEFAULT 0,
        minutes_reset_date TEXT,
        -- Rates (paise per minute stored as float INR)
        outbound_rate     REAL DEFAULT 5.00,
        inbound_rate      REAL DEFAULT 3.00,
        overage_rate      REAL DEFAULT 6.50,
        -- Branding
        company_name      TEXT,
        company_logo      TEXT,
        brand_color       TEXT DEFAULT '#00C48C',
        custom_domain     TEXT,
        -- Billing
        commitment_months INTEGER DEFAULT 3,
        plan_start_date   TEXT DEFAULT (date('now')),
        next_billing_date TEXT,
        -- Auth
        api_key           TEXT UNIQUE,
        telegram_bot_token TEXT DEFAULT '',
        telegram_chat_id   TEXT DEFAULT '',
        created_at        TEXT DEFAULT (datetime('now')),
        updated_at        TEXT DEFAULT (datetime('now'))
    )""", "resellers table")

    # ── reseller_usage table ──────────────────────────────
    safe("""CREATE TABLE IF NOT EXISTS reseller_usage (
        id            INTEGER PRIMARY KEY AUTOINCREMENT,
        reseller_id   INTEGER REFERENCES resellers(id),
        tenant_id     INTEGER REFERENCES tenants(id),
        call_id       INTEGER,
        duration_min  REAL DEFAULT 0,
        call_date     TEXT DEFAULT (date('now')),
        created_at    TEXT DEFAULT (datetime('now'))
    )""", "reseller_usage table")

    # ── reseller_tenants mapping ──────────────────────────
    safe("ALTER TABLE tenants ADD COLUMN reseller_id INTEGER DEFAULT NULL",
         "tenants.reseller_id")

    # ── reseller_alerts table ─────────────────────────────
    safe("""CREATE TABLE IF NOT EXISTS reseller_alerts (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        reseller_id INTEGER REFERENCES resellers(id),
        alert_type  TEXT NOT NULL,
        sent_at     TEXT DEFAULT (datetime('now'))
    )""", "reseller_alerts table")

    # ── indexes ───────────────────────────────────────────
    safe("CREATE INDEX IF NOT EXISTS idx_reseller_usage_rid ON reseller_usage(reseller_id)",
         "index reseller_usage.reseller_id")
    safe("CREATE INDEX IF NOT EXISTS idx_tenants_reseller ON tenants(reseller_id)",
         "index tenants.reseller_id")

    conn.close()
    print("── Done ───────────────────────────────────────")

if __name__ == "__main__":
    migrate(DB)
