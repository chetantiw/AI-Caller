"""
migrations/weekend_saas_v2.py

Weekend SaaS Phase 6-10 migration.
Run ONCE on the VPS:

    cd /root/ai-caller-env/ai-caller
    source /root/ai-caller-env/bin/activate
    python migrations/weekend_saas_v2.py

All ALTER TABLE statements are idempotent — safe to re-run.
"""

import sqlite3
import os

DB_PATH = os.path.join(os.path.dirname(__file__), '..', 'mutech.db')


def run_migration():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA journal_mode=WAL")

    migrations = [
        # ── Retry tracking on leads ──────────────────────────
        ("leads",          "retry_count",       "INTEGER DEFAULT 0"),
        ("leads",          "next_retry_at",     "TEXT"),

        # ── Campaign max retries setting ─────────────────────
        ("campaigns",      "max_retries",       "INTEGER DEFAULT 2"),

        # ── Minutes-based limit per tenant ───────────────────
        ("tenants",        "minutes_limit",     "INTEGER DEFAULT 0"),

        # ── Webhook per tenant ───────────────────────────────
        ("tenant_configs", "webhook_url",       "TEXT"),
        ("tenant_configs", "webhook_secret",    "TEXT"),
        ("tenant_configs", "webhook_events",    "TEXT DEFAULT 'call_completed'"),

        # ── STT/TTS split providers ──────────────────────────
        ("tenant_configs", "stt_provider",      "TEXT DEFAULT 'sarvam'"),
        ("tenant_configs", "tts_provider",      "TEXT DEFAULT 'sarvam'"),
        ("tenant_configs", "elevenlabs_model",  "TEXT DEFAULT 'eleven_flash_v2_5'"),

        # ── Usage alert tracking ─────────────────────────────
        ("usage_logs",     "alert_sent",        "TEXT"),

        # ── webhook_logs payload column ──────────────────────
        ("webhook_logs",   "payload",           "TEXT"),
    ]

    success = 0
    skipped = 0

    for table, column, definition in migrations:
        try:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")
            conn.commit()
            print(f"  ✅ {table}.{column} added")
            success += 1
        except sqlite3.OperationalError as e:
            if "duplicate column" in str(e).lower() or "already exists" in str(e).lower():
                print(f"  ⏭  {table}.{column} already exists — skipped")
                skipped += 1
            else:
                print(f"  ❌ {table}.{column} FAILED: {e}")

    # ── Create addon_purchases table ─────────────────────────
    try:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS addon_purchases (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                tenant_id    INTEGER REFERENCES tenants(id),
                minutes      INTEGER NOT NULL,
                amount_inr   REAL DEFAULT 0,
                purchased_at TEXT DEFAULT (datetime('now')),
                notes        TEXT,
                added_by     TEXT
            )
        """)
        conn.commit()
        print("  ✅ addon_purchases table created (or already exists)")
    except Exception as e:
        print(f"  ❌ addon_purchases table FAILED: {e}")

    # ── Create webhook_logs table ────────────────────────────
    try:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS webhook_logs (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                tenant_id   INTEGER REFERENCES tenants(id),
                event       TEXT,
                url         TEXT,
                payload     TEXT,
                status_code INTEGER,
                response    TEXT,
                fired_at    TEXT DEFAULT (datetime('now'))
            )
        """)
        conn.commit()
        print("  ✅ webhook_logs table created (or already exists)")
    except Exception as e:
        print(f"  ❌ webhook_logs table FAILED: {e}")

    conn.close()
    print(f"\nMigration complete: {success} added, {skipped} skipped.")


if __name__ == "__main__":
    print("Running weekend SaaS v2 migration...")
    run_migration()
