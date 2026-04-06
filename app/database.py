"""

app/database.py

SQLite database layer for MuTech AI Caller

Tables: users, leads, campaigns, calls, system_logs, system_config


Changelog vs original server version:

- Added transcript column to calls table

- Added system_config table (telephony provider switch, key-value store)

- Added _normalize_phone() for robust phone normalization

- Added update_lead_full() for full lead field updates

- Added bulk_insert_leads() with scientific notation fix + dedup

- Added assign_leads_to_campaign() and reset_campaign_leads()

- Added get_call_by_phone(), count_calls()

- Added get_hourly_call_stats() for heatmap

- Added complete_call() with transcript param

- Added get_config() / set_config() for system_config table

- Migration helpers run on every startup (idempotent)

"""


import sqlite3

import hashlib

import os

from typing import Optional


DB_PATH = os.path.join(os.path.dirname(__file__), '..', 'mutech.db')



def get_conn():

    conn = sqlite3.connect(DB_PATH)

    conn.row_factory = sqlite3.Row

    conn.execute("PRAGMA journal_mode=WAL")

    conn.execute("PRAGMA foreign_keys=ON")

    return conn



# ─────────────────────────────────────────

# INIT — create all tables

# ─────────────────────────────────────────

def init_db():

    with get_conn() as conn:

        conn.executescript("""

        CREATE TABLE IF NOT EXISTS users (

            id            INTEGER PRIMARY KEY AUTOINCREMENT,

            username      TEXT UNIQUE NOT NULL,

            password_hash TEXT NOT NULL,

            role          TEXT NOT NULL DEFAULT 'sales',

            name          TEXT,

            email         TEXT,

            created_at    TEXT DEFAULT (datetime('now'))

        );


        CREATE TABLE IF NOT EXISTS leads (

            id          INTEGER PRIMARY KEY AUTOINCREMENT,

            name        TEXT NOT NULL,

            phone       TEXT NOT NULL,

            company     TEXT,

            designation TEXT,

            city        TEXT,

            language    TEXT DEFAULT 'hi',

            status      TEXT DEFAULT 'new',

            notes       TEXT,

            campaign_id INTEGER,

            created_at  TEXT DEFAULT (datetime('now')),

            last_called TEXT

        );


        CREATE TABLE IF NOT EXISTS campaigns (

            id               INTEGER PRIMARY KEY AUTOINCREMENT,

            name             TEXT NOT NULL,

            description      TEXT,

            status           TEXT DEFAULT 'draft',

            leads_count      INTEGER DEFAULT 0,

            calls_made       INTEGER DEFAULT 0,

            calls_answered   INTEGER DEFAULT 0,

            demos_booked     INTEGER DEFAULT 0,

            created_at       TEXT DEFAULT (datetime('now')),

            started_at       TEXT,

            completed_at     TEXT

        );


        CREATE TABLE IF NOT EXISTS calls (

            id           INTEGER PRIMARY KEY AUTOINCREMENT,

            lead_id      INTEGER REFERENCES leads(id),

            campaign_id  INTEGER REFERENCES campaigns(id),

            phone        TEXT,

            lead_name    TEXT,

            company      TEXT,

            started_at   TEXT DEFAULT (datetime('now')),

            ended_at     TEXT,

            duration_sec INTEGER DEFAULT 0,

            outcome      TEXT DEFAULT 'unknown',

            sentiment    TEXT DEFAULT 'neutral',

            summary      TEXT,

            transcript   TEXT,

            call_sid     TEXT

        );


        CREATE TABLE IF NOT EXISTS system_logs (

            id         INTEGER PRIMARY KEY AUTOINCREMENT,

            level      TEXT DEFAULT 'info',

            message    TEXT NOT NULL,

            created_at TEXT DEFAULT (datetime('now'))

        );


        CREATE TABLE IF NOT EXISTS system_config (

            key        TEXT PRIMARY KEY,

            value      TEXT NOT NULL,

            updated_at TEXT DEFAULT (datetime('now'))

        );

        """)


        # Seed default users if none exist

        cur = conn.execute("SELECT COUNT(*) FROM users")

        if cur.fetchone()[0] == 0:

            for u in [

                ('admin',   'mutech123',   'admin',   'Admin User',    'admin@mutechautomation.com'),

                ('agent',   'agent123',    'sales',   'Sales Agent',   'agent@mutechautomation.com'),

                ('manager', 'manager123',  'manager', 'Sales Manager', 'manager@mutechautomation.com'),

                ('view',    'view123',     'viewer',  'View Only',     'view@mutechautomation.com'),

            ]:

                conn.execute(

                    "INSERT INTO users (username, password_hash, role, name, email) VALUES (?,?,?,?,?)",

                    (u[0], _hash(u[1]), u[2], u[3], u[4])

                )

        conn.commit()


        # ── Migrations (idempotent — safe to run every startup) ──

        # transcript column

        try:

            conn.execute("ALTER TABLE calls ADD COLUMN transcript TEXT")

            conn.commit()

        except Exception:

            pass  # already exists


        # call_sid column (in case old DB missing it)

        try:

            conn.execute("ALTER TABLE calls ADD COLUMN call_sid TEXT")

            conn.commit()

        except Exception:

            pass


        # Fix any scientific notation phone numbers from Excel imports

        _fix_scientific_notation_phones(conn)


    print(f"[DB] Initialized at {DB_PATH}")



def _fix_scientific_notation_phones(conn):

    """Fix phone numbers stored in scientific notation (e.g. 9.18827E+11)."""

    rows = conn.execute("SELECT id, phone FROM leads").fetchall()

    fixed = 0

    for row in rows:

        phone = str(row['phone'] or '')

        if 'e' in phone.lower():

            try:

                clean = str(int(float(phone)))

                conn.execute("UPDATE leads SET phone=? WHERE id=?", (clean, row['id']))

                fixed += 1

            except Exception:

                pass

    if fixed:

        conn.commit()

        print(f"[DB] Fixed {fixed} scientific notation phone numbers")



def _hash(password: str) -> str:

    return hashlib.sha256(password.encode()).hexdigest()



# ─────────────────────────────────────────

# SYSTEM CONFIG  (telephony switch etc.)

# ─────────────────────────────────────────

def get_config(key: str, default: str = None) -> Optional[str]:

    """Read a system config value from DB. Returns default if key not found."""

    with get_conn() as conn:

        row = conn.execute(

            "SELECT value FROM system_config WHERE key=?", (key,)

        ).fetchone()

        return row["value"] if row else default



def set_config(key: str, value: str):

    """Write (upsert) a system config value to DB."""

    with get_conn() as conn:

        conn.execute(

            """INSERT INTO system_config (key, value, updated_at)

               VALUES (?, ?, datetime('now'))

               ON CONFLICT(key) DO UPDATE

               SET value=excluded.value, updated_at=excluded.updated_at""",

            (key, value)

        )

        conn.commit()



# ─────────────────────────────────────────

# AUTH

# ─────────────────────────────────────────

def verify_user(username: str, password: str) -> Optional[dict]:

    with get_conn() as conn:

        row = conn.execute(

            "SELECT * FROM users WHERE username=? AND password_hash=?",

            (username, _hash(password))

        ).fetchone()

        return dict(row) if row else None



def get_user_by_id(user_id: int) -> Optional[dict]:

    with get_conn() as conn:

        row = conn.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()

        return dict(row) if row else None



def get_all_users() -> list:

    with get_conn() as conn:

        return [dict(r) for r in conn.execute(

            "SELECT id, username, role, name, email, created_at FROM users ORDER BY id"

        ).fetchall()]



def add_user(username: str, password: str, role: str, name: str, email: str) -> int:

    with get_conn() as conn:

        cur = conn.execute(

            "INSERT INTO users (username, password_hash, role, name, email) VALUES (?,?,?,?,?)",

            (username, _hash(password), role, name, email)

        )

        conn.commit()

        return cur.lastrowid



def delete_user(user_id: int):

    with get_conn() as conn:

        conn.execute("DELETE FROM users WHERE id=?", (user_id,))

        conn.commit()



# ─────────────────────────────────────────

# LEADS

# ─────────────────────────────────────────

def _normalize_phone(phone: str) -> str:

    """

    Convert any phone format to clean 12-digit string (91XXXXXXXXXX).

    Handles: scientific notation, +91, 0XXX, 10-digit, 12-digit.

    """

    import re

    phone = str(phone).strip()

    # Handle scientific notation from Excel (e.g. 9.18827E+11)

    if 'e' in phone.lower():

        try:

            phone = str(int(float(phone)))

        except Exception:

            pass

    digits = re.sub(r'[^\d]', '', phone)

    if len(digits) == 12 and digits.startswith('91'):

        return digits                   # 91XXXXXXXXXX — already normalized

    elif len(digits) == 11 and digits.startswith('0'):

        return '91' + digits[1:]        # 0XXXXXXXXXX → 91XXXXXXXXXX

    elif len(digits) == 10:

        return '91' + digits            # XXXXXXXXXX → 91XXXXXXXXXX

    elif len(digits) == 13 and digits.startswith('091'):

        return '91' + digits[3:]        # 091XXXXXXXXXX → 91XXXXXXXXXX

    return digits



def get_leads(status: str = None, campaign_id: int = None,

              limit: int = 100, offset: int = 0) -> list:

    query = "SELECT * FROM leads WHERE 1=1"

    params = []

    if status:

        query += " AND status=?"; params.append(status)

    if campaign_id:

        query += " AND campaign_id=?"; params.append(campaign_id)

    query += " ORDER BY created_at DESC LIMIT ? OFFSET ?"

    params += [limit, offset]

    with get_conn() as conn:

        return [dict(r) for r in conn.execute(query, params).fetchall()]



def get_lead(lead_id: int) -> Optional[dict]:

    with get_conn() as conn:

        row = conn.execute("SELECT * FROM leads WHERE id=?", (lead_id,)).fetchone()

        return dict(row) if row else None



def get_lead_by_phone(phone: str) -> Optional[dict]:

    clean = phone.replace("+91", "").replace("+", "").strip()

    with get_conn() as conn:

        row = conn.execute(

            "SELECT * FROM leads WHERE phone LIKE ?", (f"%{clean}",)

        ).fetchone()

        return dict(row) if row else None



def create_lead(name: str, phone: str, company: str = None, designation: str = None,

                city: str = None, language: str = 'hi', campaign_id: int = None) -> int:

    with get_conn() as conn:

        cur = conn.execute(

            """INSERT INTO leads (name, phone, company, designation, city, language, campaign_id)

               VALUES (?,?,?,?,?,?,?)""",

            (name, phone, company, designation, city, language, campaign_id)

        )

        conn.commit()

        return cur.lastrowid



def update_lead(lead_id: int, status: str = None, notes: str = None):

    """Quick update — status and/or notes only."""

    with get_conn() as conn:

        if status and notes:

            conn.execute(

                "UPDATE leads SET status=?, notes=?, last_called=datetime('now') WHERE id=?",

                (status, notes, lead_id)

            )

        elif status:

            conn.execute(

                "UPDATE leads SET status=?, last_called=datetime('now') WHERE id=?",

                (status, lead_id)

            )

        elif notes:

            conn.execute("UPDATE leads SET notes=? WHERE id=?", (notes, lead_id))

        conn.commit()



def update_lead_full(lead_id: int, name: str = None, phone: str = None,

                     company: str = None, designation: str = None,

                     language: str = None, status: str = None, notes: str = None):

    """Full update — any combination of fields."""

    with get_conn() as conn:

        sets, params = [], []

        if name        is not None: sets.append("name=?");        params.append(name)

        if phone       is not None: sets.append("phone=?");       params.append(phone)

        if company     is not None: sets.append("company=?");     params.append(company)

        if designation is not None: sets.append("designation=?"); params.append(designation)

        if language    is not None: sets.append("language=?");    params.append(language)

        if status      is not None: sets.append("status=?");      params.append(status)

        if notes       is not None: sets.append("notes=?");       params.append(notes)

        if not sets:

            return

        params.append(lead_id)

        conn.execute(f"UPDATE leads SET {', '.join(sets)} WHERE id=?", params)

        conn.commit()



def delete_lead(lead_id: int):

    with get_conn() as conn:

        conn.execute("DELETE FROM leads WHERE id=?", (lead_id,))

        conn.commit()



def bulk_insert_leads(leads: list, campaign_id: int = None) -> int:

    """

    Insert leads from CSV into DB.

    - Normalizes phone numbers (handles scientific notation, various formats)

    - Skips rows with no name or phone

    - Skips duplicates (phone already in DB)

    - Returns count of newly inserted leads

    """

    count = 0

    with get_conn() as conn:

        for row in leads:

            raw_phone = str(row.get('phone', '')).strip()

            name      = str(row.get('name', 'Unknown')).strip()

            if not raw_phone or not name:

                continue


            phone = _normalize_phone(raw_phone)

            if not phone or len(phone) < 10:

                continue


            exists = conn.execute(

                "SELECT id FROM leads WHERE phone=?", (phone,)

            ).fetchone()

            if exists:

                continue


            conn.execute(

                """INSERT INTO leads

                   (name, phone, company, designation, language, campaign_id)

                   VALUES (?,?,?,?,?,?)""",

                (name, phone,

                 str(row.get('company',     '') or '').strip(),

                 str(row.get('designation', '') or '').strip(),

                 str(row.get('language',    'hi') or 'hi').strip(),

                 campaign_id)

            )

            count += 1


        conn.commit()


        if campaign_id and count > 0:

            conn.execute(

                "UPDATE campaigns SET leads_count = leads_count + ? WHERE id=?",

                (count, campaign_id)

            )

            conn.commit()


    return count



def reset_campaign_leads(campaign_id: int) -> int:

    """Reset all leads in a campaign back to 'new' so it can be re-run."""

    with get_conn() as conn:

        cur = conn.execute(

            """UPDATE leads SET status='new', last_called=NULL

               WHERE campaign_id=? AND status NOT IN ('demo_booked')""",

            (campaign_id,)

        )

        count = cur.rowcount

        conn.execute(

            """UPDATE campaigns SET

               calls_made=0, calls_answered=0, demos_booked=0,

               status='draft', started_at=NULL, completed_at=NULL

               WHERE id=?""",

            (campaign_id,)

        )

        conn.commit()

        return count



def assign_leads_to_campaign(campaign_id: int, group: str) -> int:

    """

    Assign leads to a campaign based on group selection.

    Only takes leads whose current campaign is NULL or completed/draft.

    group: 'new' | 'unassigned' | 'called' | 'interested' | 'not_interested' | 'all'

    Returns count of leads assigned.

    """

    safe_camp = """(campaign_id IS NULL OR campaign_id IN (

        SELECT id FROM campaigns WHERE status IN ('completed','draft')

    ))"""


    with get_conn() as conn:

        if group == 'new':

            query  = f"UPDATE leads SET campaign_id=? WHERE status='new' AND {safe_camp}"

            params = [campaign_id]

        elif group == 'unassigned':

            query  = "UPDATE leads SET campaign_id=? WHERE campaign_id IS NULL"

            params = [campaign_id]

        elif group == 'called':

            query  = f"UPDATE leads SET campaign_id=? WHERE status='called' AND {safe_camp}"

            params = [campaign_id]

        elif group == 'interested':

            query  = f"UPDATE leads SET campaign_id=? WHERE status IN ('interested','demo_booked') AND {safe_camp}"

            params = [campaign_id]

        elif group == 'not_interested':

            query  = f"UPDATE leads SET campaign_id=? WHERE status='not_interested' AND {safe_camp}"

            params = [campaign_id]

        elif group == 'all':

            query  = f"UPDATE leads SET campaign_id=? WHERE {safe_camp}"

            params = [campaign_id]

        else:

            return 0


        cur   = conn.execute(query, params)

        count = cur.rowcount

        conn.commit()


        if count > 0:

            conn.execute(

                "UPDATE campaigns SET leads_count=? WHERE id=?",

                (count, campaign_id)

            )

            conn.commit()

        return count



def count_leads(status: str = None) -> int:

    with get_conn() as conn:

        if status:

            return conn.execute(

                "SELECT COUNT(*) FROM leads WHERE status=?", (status,)

            ).fetchone()[0]

        return conn.execute("SELECT COUNT(*) FROM leads").fetchone()[0]



# ─────────────────────────────────────────

# CAMPAIGNS

# ─────────────────────────────────────────

def get_campaigns(status: str = None) -> list:

    query = "SELECT * FROM campaigns WHERE 1=1"

    params = []

    if status:

        query += " AND status=?"; params.append(status)

    query += " ORDER BY created_at DESC"

    with get_conn() as conn:

        return [dict(r) for r in conn.execute(query, params).fetchall()]



def get_campaign(campaign_id: int) -> Optional[dict]:

    with get_conn() as conn:

        row = conn.execute(

            "SELECT * FROM campaigns WHERE id=?", (campaign_id,)

        ).fetchone()

        return dict(row) if row else None



def create_campaign(name: str, description: str = None) -> int:

    with get_conn() as conn:

        cur = conn.execute(

            "INSERT INTO campaigns (name, description) VALUES (?,?)",

            (name, description)

        )

        conn.commit()

        return cur.lastrowid



def update_campaign_status(campaign_id: int, status: str):

    with get_conn() as conn:

        if status == 'running':

            conn.execute(

                "UPDATE campaigns SET status=?, started_at=datetime('now') WHERE id=?",

                (status, campaign_id)

            )

        elif status == 'completed':

            conn.execute(

                "UPDATE campaigns SET status=?, completed_at=datetime('now') WHERE id=?",

                (status, campaign_id)

            )

        else:

            conn.execute(

                "UPDATE campaigns SET status=? WHERE id=?", (status, campaign_id)

            )

        conn.commit()



def increment_campaign_calls(campaign_id: int, answered: bool = False, demo: bool = False):

    with get_conn() as conn:

        conn.execute(

            """UPDATE campaigns SET

               calls_made     = calls_made + 1,

               calls_answered = calls_answered + ?,

               demos_booked   = demos_booked + ?

               WHERE id=?""",

            (1 if answered else 0, 1 if demo else 0, campaign_id)

        )

        conn.commit()



def delete_campaign(campaign_id: int):

    with get_conn() as conn:

        conn.execute("DELETE FROM campaigns WHERE id=?", (campaign_id,))

        conn.commit()



# ─────────────────────────────────────────

# CALLS

# ─────────────────────────────────────────

def create_call(phone: str, lead_name: str = None, company: str = None,

                lead_id: int = None, campaign_id: int = None,

                call_sid: str = None) -> int:

    with get_conn() as conn:

        cur = conn.execute(

            """INSERT INTO calls (phone, lead_name, company, lead_id, campaign_id, call_sid)

               VALUES (?,?,?,?,?,?)""",

            (phone, lead_name, company, lead_id, campaign_id, call_sid)

        )

        conn.commit()

        return cur.lastrowid



def complete_call(call_id: int, duration_sec: int, outcome: str,

                  sentiment: str, summary: str, transcript: str = None):

    with get_conn() as conn:

        conn.execute(

            """UPDATE calls SET

               ended_at     = datetime('now'),

               duration_sec = ?,

               outcome      = ?,

               sentiment    = ?,

               summary      = ?,

               transcript   = ?

               WHERE id=?""",

            (duration_sec, outcome, sentiment, summary, transcript, call_id)

        )

        conn.commit()



def get_calls(limit: int = 50, offset: int = 0, campaign_id: int = None) -> list:

    query = "SELECT * FROM calls WHERE 1=1"

    params = []

    if campaign_id:

        query += " AND campaign_id=?"; params.append(campaign_id)

    query += " ORDER BY started_at DESC LIMIT ? OFFSET ?"

    params += [limit, offset]

    with get_conn() as conn:

        return [dict(r) for r in conn.execute(query, params).fetchall()]



def get_recent_calls(limit: int = 10) -> list:

    with get_conn() as conn:

        return [dict(r) for r in conn.execute(

            "SELECT * FROM calls ORDER BY started_at DESC LIMIT ?", (limit,)

        ).fetchall()]



def get_call_by_phone(phone: str) -> Optional[dict]:

    """Find most recent call by phone number (partial match)."""

    clean = phone.replace("+91", "").replace("+", "").lstrip("0").strip()

    with get_conn() as conn:

        row = conn.execute(

            """SELECT * FROM calls WHERE phone LIKE ?

               ORDER BY started_at DESC LIMIT 1""",

            (f"%{clean}",)

        ).fetchone()

        return dict(row) if row else None



def get_call(call_id: int) -> Optional[dict]:

    with get_conn() as conn:

        row = conn.execute("SELECT * FROM calls WHERE id=?", (call_id,)).fetchone()

        return dict(row) if row else None



def count_calls() -> int:

    with get_conn() as conn:

        return conn.execute("SELECT COUNT(*) FROM calls").fetchone()[0]



def get_calls_today() -> int:

    with get_conn() as conn:

        return conn.execute(

            "SELECT COUNT(*) FROM calls WHERE date(started_at)=date('now')"

        ).fetchone()[0]



def get_daily_call_stats(days: int = 14) -> list:

    with get_conn() as conn:

        return [dict(r) for r in conn.execute("""

            SELECT

                date(started_at)                                            AS date,

                COUNT(*)                                                    AS total,

                SUM(CASE WHEN outcome='answered'      THEN 1 ELSE 0 END)   AS answered,

                SUM(CASE WHEN sentiment='interested'  THEN 1 ELSE 0 END)   AS interested,

                SUM(CASE WHEN sentiment='demo_booked' THEN 1 ELSE 0 END)   AS demos

            FROM calls

            WHERE started_at >= date('now', ?)

            GROUP BY date(started_at)

            ORDER BY date ASC

        """, (f"-{days} days",)).fetchall()]



def get_hourly_call_stats(days: int = 30) -> list:

    """Returns call counts by hour-of-day (0–23) for the heatmap widget."""

    with get_conn() as conn:

        return [dict(r) for r in conn.execute("""

            SELECT

                CAST(strftime('%H', started_at) AS INTEGER)              AS hour,

                COUNT(*)                                                  AS total,

                SUM(CASE WHEN outcome='answered' THEN 1 ELSE 0 END)      AS answered

            FROM calls

            WHERE started_at >= date('now', ?)

            GROUP BY hour

            ORDER BY hour ASC

        """, (f"-{days} days",)).fetchall()]



# ─────────────────────────────────────────

# SYSTEM LOGS

# ─────────────────────────────────────────

def add_log(message: str, level: str = 'info'):

    with get_conn() as conn:

        conn.execute(

            "INSERT INTO system_logs (message, level) VALUES (?,?)",

            (message, level)

        )

        conn.commit()



def get_logs(limit: int = 50) -> list:

    with get_conn() as conn:

        return [dict(r) for r in conn.execute(

            "SELECT * FROM system_logs ORDER BY created_at DESC LIMIT ?", (limit,)

        ).fetchall()]



# ─────────────────────────────────────────

# DASHBOARD STATS

# ─────────────────────────────────────────

def get_dashboard_stats() -> dict:

    with get_conn() as conn:

        total_calls  = conn.execute("SELECT COUNT(*) FROM calls").fetchone()[0]

        calls_today  = conn.execute(

            "SELECT COUNT(*) FROM calls WHERE date(started_at)=date('now')"

        ).fetchone()[0]

        answered     = conn.execute(

            "SELECT COUNT(*) FROM calls WHERE outcome='answered'"

        ).fetchone()[0]

        demos        = conn.execute(

            "SELECT COUNT(*) FROM calls WHERE sentiment='demo_booked'"

        ).fetchone()[0]

        total_leads  = conn.execute("SELECT COUNT(*) FROM leads").fetchone()[0]

        new_leads    = conn.execute(

            "SELECT COUNT(*) FROM leads WHERE status='new'"

        ).fetchone()[0]

        active_camps = conn.execute(

            "SELECT COUNT(*) FROM campaigns WHERE status='running'"

        ).fetchone()[0]


        connect_rate = round((answered / total_calls * 100), 1) if total_calls > 0 else 0

        demo_rate    = round((demos / answered * 100), 1) if answered > 0 else 0


        return {

            "total_calls":      total_calls,

            "calls_today":      calls_today,

            "answered":         answered,

            "demos_booked":     demos,

            "total_leads":      total_leads,

            "new_leads":        new_leads,

            "active_campaigns": active_camps,

            "connect_rate":     connect_rate,

            "demo_rate":        demo_rate,

        }

