import sqlite3, os
conn = sqlite3.connect('/root/ai-caller-env/ai-caller/mutech.db')
conn.executescript("""

-- Super admins (platform owners)
CREATE TABLE IF NOT EXISTS super_admins (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    username      TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    name          TEXT,
    email         TEXT,
    created_at    TEXT DEFAULT (datetime('now'))
);

-- Tenants (each company using the platform)
CREATE TABLE IF NOT EXISTS tenants (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    name            TEXT NOT NULL,
    slug            TEXT UNIQUE NOT NULL,
    status          TEXT DEFAULT 'active',
    plan            TEXT DEFAULT 'starter',
    calls_limit     INTEGER DEFAULT 1000,
    calls_used      INTEGER DEFAULT 0,
    minutes_used    REAL DEFAULT 0,
    contact_name    TEXT,
    contact_email   TEXT,
    contact_phone   TEXT,
    created_at      TEXT DEFAULT (datetime('now')),
    expires_at      TEXT
);

-- Per-tenant configuration (API keys, agent settings)
CREATE TABLE IF NOT EXISTS tenant_configs (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    tenant_id           INTEGER UNIQUE REFERENCES tenants(id),
    agent_name          TEXT DEFAULT 'Aira',
    agent_language      TEXT DEFAULT 'hi-IN',
    agent_voice         TEXT DEFAULT 'anushka',
    system_prompt       TEXT,
    greeting_template   TEXT,
    piopiy_agent_id     TEXT,
    piopiy_agent_token  TEXT,
    piopiy_number       TEXT,
    sarvam_api_key      TEXT,
    groq_api_key        TEXT,
    exotel_sid          TEXT,
    exotel_api_key      TEXT,
    exotel_api_token    TEXT,
    exotel_number       TEXT,
    telegram_bot_token  TEXT,
    telegram_chat_id    TEXT,
    updated_at          TEXT DEFAULT (datetime('now'))
);

-- Usage tracking per tenant per day
CREATE TABLE IF NOT EXISTS usage_logs (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    tenant_id     INTEGER REFERENCES tenants(id),
    date          TEXT DEFAULT (date('now')),
    calls_made    INTEGER DEFAULT 0,
    minutes_used  REAL DEFAULT 0,
    api_errors    INTEGER DEFAULT 0,
    created_at    TEXT DEFAULT (datetime('now'))
);

""")
conn.commit()

# Add columns individually, ignoring duplicates
for stmt in [
    "ALTER TABLE users     ADD COLUMN tenant_id INTEGER REFERENCES tenants(id)",
    "ALTER TABLE leads     ADD COLUMN tenant_id INTEGER REFERENCES tenants(id)",
    "ALTER TABLE campaigns ADD COLUMN tenant_id INTEGER REFERENCES tenants(id)",
    "ALTER TABLE calls     ADD COLUMN tenant_id INTEGER REFERENCES tenants(id)",
    "ALTER TABLE calls     ADD COLUMN transcript TEXT",
    "ALTER TABLE system_logs ADD COLUMN tenant_id INTEGER REFERENCES tenants(id)",
]:
    try:
        conn.execute(stmt)
    except Exception as e:
        if 'duplicate column' in str(e):
            pass  # column already exists, skip
        else:
            raise
conn.commit()

# Seed first tenant — MuTech Automation (existing data)
import hashlib
def h(p): return hashlib.sha256(p.encode()).hexdigest()

conn.execute("""INSERT OR IGNORE INTO tenants
    (id, name, slug, status, plan, contact_name, contact_email)
    VALUES (1, 'MuTech Automation', 'mutech', 'active', 'enterprise',
    'Chetan', 'chetantiw@gmail.com')""")

conn.execute("""INSERT OR IGNORE INTO tenant_configs (tenant_id, agent_name)
    VALUES (1, 'Aira')""")

# Seed super admin
conn.execute("""INSERT OR IGNORE INTO super_admins
    (username, password_hash, name, email)
    VALUES ('superadmin', ?, 'Super Admin', 'chetantiw@gmail.com')""",
    (h('MuTech@Super2026'),))

# Assign all existing data to tenant 1
conn.execute("UPDATE users     SET tenant_id=1 WHERE tenant_id IS NULL")
conn.execute("UPDATE leads     SET tenant_id=1 WHERE tenant_id IS NULL")
conn.execute("UPDATE campaigns SET tenant_id=1 WHERE tenant_id IS NULL")
conn.execute("UPDATE calls     SET tenant_id=1 WHERE tenant_id IS NULL")
conn.execute("UPDATE system_logs SET tenant_id=1 WHERE tenant_id IS NULL")

conn.commit()
conn.close()
print('✅ SaaS migration complete')
