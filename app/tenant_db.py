"""
app/tenant_db.py
Tenant-aware database layer for MuTech AI Caller SaaS Platform
Handles: tenants, tenant_configs, usage_logs, super_admins
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


def _hash(password: str) -> str:
    return hashlib.sha256(password.encode()).hexdigest()


# ─────────────────────────────────────────
# SUPER ADMIN AUTH
# ─────────────────────────────────────────

def verify_super_admin(username: str, password: str) -> Optional[dict]:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM super_admins WHERE username=? AND password_hash=?",
            (username, _hash(password))
        ).fetchone()
        return dict(row) if row else None


# ─────────────────────────────────────────
# TENANTS
# ─────────────────────────────────────────

def get_all_tenants() -> list:
    with get_conn() as conn:
        rows = conn.execute("""
            SELECT
                t.*,
                COUNT(DISTINCT c.id)  AS total_calls,
                COUNT(DISTINCT l.id)  AS total_leads,
                COUNT(DISTINCT u.id)  AS total_users,
                COALESCE(SUM(CASE WHEN c.started_at >= date('now', '-30 days') THEN 1 ELSE 0 END), 0) AS calls_30d
            FROM tenants t
            LEFT JOIN calls      c ON c.tenant_id = t.id
            LEFT JOIN leads      l ON l.tenant_id = t.id
            LEFT JOIN users      u ON u.tenant_id = t.id
            GROUP BY t.id
            ORDER BY t.created_at DESC
        """).fetchall()
        return [dict(r) for r in rows]


def get_tenant(tenant_id: int) -> Optional[dict]:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM tenants WHERE id=?", (tenant_id,)
        ).fetchone()
        return dict(row) if row else None


def get_tenant_by_slug(slug: str) -> Optional[dict]:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM tenants WHERE slug=?", (slug,)
        ).fetchone()
        return dict(row) if row else None


def create_tenant(name: str, slug: str, plan: str = 'starter',
                  contact_name: str = None, contact_email: str = None,
                  contact_phone: str = None, calls_limit: int = 1000) -> int:
    with get_conn() as conn:
        cur = conn.execute("""
            INSERT INTO tenants (name, slug, plan, contact_name, contact_email,
                                 contact_phone, calls_limit, status)
            VALUES (?, ?, ?, ?, ?, ?, ?, 'active')
        """, (name, slug, plan, contact_name, contact_email, contact_phone, calls_limit))
        tenant_id = cur.lastrowid

        # Create default config for tenant
        conn.execute("""
            INSERT INTO tenant_configs (tenant_id, agent_name)
            VALUES (?, 'Aira')
        """, (tenant_id,))
        conn.commit()
        return tenant_id


def update_tenant(tenant_id: int, **kwargs):
    allowed = {'name', 'status', 'plan', 'calls_limit', 'contact_name',
               'contact_email', 'contact_phone', 'expires_at'}
    fields = {k: v for k, v in kwargs.items() if k in allowed}
    if not fields:
        return
    sets = ', '.join(f"{k}=?" for k in fields)
    with get_conn() as conn:
        conn.execute(
            f"UPDATE tenants SET {sets} WHERE id=?",
            list(fields.values()) + [tenant_id]
        )
        conn.commit()


def update_tenant_status(tenant_id: int, status: str):
    with get_conn() as conn:
        conn.execute(
            "UPDATE tenants SET status=? WHERE id=?", (status, tenant_id)
        )
        conn.commit()


def delete_tenant(tenant_id: int):
    with get_conn() as conn:
        conn.execute("DELETE FROM users WHERE tenant_id=?", (tenant_id,))
        conn.execute("DELETE FROM tenant_configs WHERE tenant_id=?", (tenant_id,))
        conn.execute("DELETE FROM usage_logs WHERE tenant_id=?", (tenant_id,))
        conn.execute("DELETE FROM tenants WHERE id=?", (tenant_id,))
        conn.commit()


# ─────────────────────────────────────────
# TENANT CONFIG (API KEYS, AGENT SETTINGS)
# ─────────────────────────────────────────

def get_tenant_config(tenant_id: int) -> Optional[dict]:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM tenant_configs WHERE tenant_id=?", (tenant_id,)
        ).fetchone()
        return dict(row) if row else None


def update_tenant_config(tenant_id: int, **kwargs):
    allowed = {
        'agent_name', 'agent_language', 'agent_voice', 'system_prompt',
        'greeting_template', 'piopiy_agent_id', 'piopiy_agent_token',
        'piopiy_number', 'sarvam_api_key', 'groq_api_key',
        'exotel_sid', 'exotel_api_key', 'exotel_api_token', 'exotel_number',
        'telegram_bot_token', 'telegram_chat_id',
        'company_name', 'company_industry', 'company_products',
        'company_website', 'call_language', 'call_guidelines', 'setup_complete',
        # LLM provider & keys
        'llm_provider', 'llm_model',
        'openai_api_key', 'anthropic_api_key', 'gemini_api_key',
        # Speech provider & ElevenLabs
        'speech_provider', 'elevenlabs_api_key', 'elevenlabs_voice_id',
        # WhatsApp
        'whatsapp_api_key', 'whatsapp_number',
    }
    fields = {k: v for k, v in kwargs.items() if k in allowed}
    if not fields:
        return
    fields['updated_at'] = "datetime('now')"
    sets = ', '.join(
        f"{k}=datetime('now')" if v == "datetime('now')" else f"{k}=?"
        for k, v in fields.items()
    )
    values = [v for v in fields.values() if v != "datetime('now')"]
    with get_conn() as conn:
        conn.execute(
            f"UPDATE tenant_configs SET {sets} WHERE tenant_id=?",
            values + [tenant_id]
        )
        conn.commit()


# ─────────────────────────────────────────
# USAGE TRACKING
# ─────────────────────────────────────────

def log_usage(tenant_id: int, minutes: float, errors: int = 0):
    with get_conn() as conn:
        # Upsert today's usage log
        conn.execute("""
            INSERT INTO usage_logs (tenant_id, date, calls_made, minutes_used, api_errors)
            VALUES (?, date('now'), 1, ?, ?)
            ON CONFLICT DO NOTHING
        """, (tenant_id, minutes, errors))
        conn.execute("""
            UPDATE usage_logs
            SET calls_made   = calls_made + 1,
                minutes_used = minutes_used + ?,
                api_errors   = api_errors + ?
            WHERE tenant_id=? AND date=date('now')
        """, (minutes, errors, tenant_id))

        # Update tenant totals
        conn.execute("""
            UPDATE tenants
            SET calls_used   = calls_used + 1,
                minutes_used = minutes_used + ?
            WHERE id=?
        """, (minutes, tenant_id))
        conn.commit()


def get_tenant_usage(tenant_id: int, days: int = 30) -> list:
    with get_conn() as conn:
        return [dict(r) for r in conn.execute("""
            SELECT date, calls_made, minutes_used, api_errors
            FROM usage_logs
            WHERE tenant_id=? AND date >= date('now', ?)
            ORDER BY date ASC
        """, (tenant_id, f'-{days} days')).fetchall()]


def get_all_usage_today() -> list:
    with get_conn() as conn:
        return [dict(r) for r in conn.execute("""
            SELECT
                t.id, t.name, t.slug, t.status, t.plan,
                t.calls_used, t.calls_limit, t.minutes_used,
                COALESCE(u.calls_made, 0)    AS calls_today,
                COALESCE(u.minutes_used, 0)  AS minutes_today,
                COALESCE(u.api_errors, 0)    AS errors_today
            FROM tenants t
            LEFT JOIN usage_logs u ON u.tenant_id=t.id AND u.date=date('now')
            ORDER BY calls_today DESC
        """).fetchall()]


# ─────────────────────────────────────────
# SUPER ADMIN DASHBOARD STATS
# ─────────────────────────────────────────

def get_platform_stats() -> dict:
    with get_conn() as conn:
        total_tenants      = conn.execute("SELECT COUNT(*) FROM tenants").fetchone()[0]
        active_tenants     = conn.execute(
            "SELECT COUNT(*) FROM tenants WHERE status='active'"
        ).fetchone()[0]
        suspended_tenants  = conn.execute(
            "SELECT COUNT(*) FROM tenants WHERE status IN ('suspended','expired')"
        ).fetchone()[0]
        total_calls        = conn.execute("SELECT COUNT(*) FROM calls").fetchone()[0]
        calls_today        = conn.execute(
            "SELECT COUNT(*) FROM calls WHERE date(started_at)=date('now')"
        ).fetchone()[0]
        calls_this_month   = conn.execute(
            "SELECT COUNT(*) FROM calls WHERE started_at >= date('now', 'start of month')"
        ).fetchone()[0]
        total_minutes      = conn.execute(
            "SELECT COALESCE(SUM(duration_sec)/60.0, 0) FROM calls"
        ).fetchone()[0]
        total_users        = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]

        return {
            "total_tenants":     total_tenants,
            "active_tenants":    active_tenants,
            "suspended_tenants": suspended_tenants,
            "total_calls":       total_calls,
            "calls_today":       calls_today,
            "calls_this_month":  calls_this_month,
            "total_minutes":     round(total_minutes, 1),
            "total_users":       total_users,
        }


# ─────────────────────────────────────────
# TENANT USER MANAGEMENT
# ─────────────────────────────────────────

def get_tenant_users(tenant_id: int) -> list:
    with get_conn() as conn:
        return [dict(r) for r in conn.execute(
            "SELECT id, username, role, name, email, created_at FROM users WHERE tenant_id=?",
            (tenant_id,)
        ).fetchall()]


def create_tenant_user(tenant_id: int, username: str, password: str,
                       role: str, name: str, email: str) -> int:
    with get_conn() as conn:
        cur = conn.execute(
            """INSERT INTO users (username, password_hash, role, name, email, tenant_id)
               VALUES (?,?,?,?,?,?)""",
            (username, _hash(password), role, name, email, tenant_id)
        )
        conn.commit()
        return cur.lastrowid
