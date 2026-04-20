"""
app/tenant_db.py
Tenant-aware database layer for MuTech AI Caller SaaS Platform
Handles: tenants, tenant_configs, usage_logs, super_admins
"""

import asyncio
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


def create_addon_purchase(tenant_id: int, minutes: int, amount_inr: float,
                          notes: str = "") -> int:
    """Insert addon_purchases row, increment calls_limit, return new limit."""
    with get_conn() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS addon_purchases (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                tenant_id   INTEGER NOT NULL,
                minutes     INTEGER NOT NULL,
                amount_inr  REAL    NOT NULL DEFAULT 0,
                notes       TEXT,
                granted_at  TEXT    DEFAULT (datetime('now'))
            )
        """)
        conn.execute(
            "INSERT INTO addon_purchases (tenant_id, minutes, amount_inr, notes) VALUES (?,?,?,?)",
            (tenant_id, minutes, amount_inr, notes),
        )
        conn.execute(
            "UPDATE tenants SET calls_limit = calls_limit + ? WHERE id = ?",
            (minutes, tenant_id),
        )
        conn.commit()
        row = conn.execute("SELECT calls_limit FROM tenants WHERE id=?", (tenant_id,)).fetchone()
        return row["calls_limit"] if row else 0


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
    allowed = {'name', 'status', 'plan', 'calls_limit', 'groq_daily_limit',
               'minutes_limit', 'contact_name', 'contact_email', 'contact_phone', 'expires_at'}
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
        'openai_api_key', 'xai_api_key', 'anthropic_api_key', 'gemini_api_key',
        # Speech provider & ElevenLabs
        'speech_provider', 'sarvam_api_key', 'deepgram_api_key',
        'elevenlabs_api_key', 'elevenlabs_voice_id', 'elevenlabs_model',
        'stt_provider', 'tts_provider',
        # WhatsApp
        'whatsapp_api_key', 'whatsapp_number',
        'faq_content',
        'webhook_url', 'webhook_secret', 'webhook_events',
        'tts_model', 'tts_pace', 'tts_temperature',
        'agent_gender', 'behavior_rules',
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
        # Upsert today's usage log (single statement — no double-counting)
        conn.execute("""
            INSERT INTO usage_logs (tenant_id, date, calls_made, minutes_used, api_errors)
            VALUES (?, date('now'), 1, ?, ?)
            ON CONFLICT(tenant_id, date) DO UPDATE SET
                calls_made   = calls_made   + 1,
                minutes_used = minutes_used + excluded.minutes_used,
                api_errors   = api_errors   + excluded.api_errors
        """, (tenant_id, minutes, errors))

        # Update tenant totals
        conn.execute("""
            UPDATE tenants
            SET calls_used   = calls_used + 1,
                minutes_used = minutes_used + ?
            WHERE id=?
        """, (minutes, tenant_id))

        # ── Usage alerts (80% and 100%) ────────────────────
        try:
            tenant_row = conn.execute(
                "SELECT calls_used, calls_limit FROM tenants WHERE id=?",
                (tenant_id,)
            ).fetchone()
            if tenant_row:
                cu = (tenant_row["calls_used"] or 0)
                cl = tenant_row["calls_limit"] or 0
                if cl > 0:
                    pct = cu / cl * 100
                    alert_type = None
                    if pct >= 100:
                        alert_type = "quota_100"
                    elif pct >= 80:
                        alert_type = "quota_80"

                    if alert_type:
                        already = conn.execute(
                            "SELECT id FROM usage_logs"
                            " WHERE tenant_id=? AND date=date('now') AND alert_sent=?",
                            (tenant_id, alert_type)
                        ).fetchone()
                        if not already:
                            conn.execute(
                                "UPDATE usage_logs SET alert_sent=?"
                                " WHERE tenant_id=? AND date=date('now')",
                                (alert_type, tenant_id)
                            )
                            try:
                                asyncio.create_task(
                                    _fire_usage_alert(tenant_id, pct, alert_type)
                                )
                            except RuntimeError:
                                asyncio.ensure_future(
                                    _fire_usage_alert(tenant_id, pct, alert_type)
                                )
        except Exception:
            pass  # never crash log_usage over alert logic

        conn.commit()


def check_quota(tenant_id: int) -> dict:
    """
    Returns quota status for a tenant.
    calls_limit = 0 means unlimited (enterprise / platform override).
    """
    with get_conn() as conn:
        row = conn.execute(
            "SELECT calls_used, calls_limit, plan, status FROM tenants WHERE id=?",
            (tenant_id,)
        ).fetchone()
    if not row:
        return {"allowed": False, "reason": "Tenant not found"}

    calls_used  = row["calls_used"]  or 0
    calls_limit = row["calls_limit"] or 0
    status      = row["status"]
    plan        = row["plan"]

    if status != "active":
        return {
            "allowed":     False,
            "calls_used":  calls_used,
            "calls_limit": calls_limit,
            "remaining":   0,
            "pct_used":    100.0,
            "plan":        plan,
            "reason":      f"Tenant account is {status}. Contact support.",
        }

    if calls_limit == 0:
        return {
            "allowed":     True,
            "calls_used":  calls_used,
            "calls_limit": 0,
            "remaining":   999999,
            "pct_used":    0.0,
            "plan":        plan,
            "reason":      "",
        }

    remaining = calls_limit - calls_used
    pct_used  = round((calls_used / calls_limit) * 100, 1)

    if remaining <= 0:
        return {
            "allowed":     False,
            "calls_used":  calls_used,
            "calls_limit": calls_limit,
            "remaining":   0,
            "pct_used":    pct_used,
            "plan":        plan,
            "reason":      (
                f"Call quota exhausted ({calls_used}/{calls_limit}). "
                f"Upgrade your {plan} plan or purchase add-on minutes."
            ),
        }

    return {
        "allowed":     True,
        "calls_used":  calls_used,
        "calls_limit": calls_limit,
        "remaining":   remaining,
        "pct_used":    pct_used,
        "plan":        plan,
        "reason":      "",
    }


def get_tenant_usage(tenant_id: int, days: int = 30) -> list:
    with get_conn() as conn:
        return [dict(r) for r in conn.execute("""
            SELECT date,
                   SUM(calls_made)   AS calls_made,
                   ROUND(SUM(minutes_used),2) AS minutes_used,
                   SUM(api_errors)   AS api_errors
            FROM usage_logs
            WHERE tenant_id=? AND date >= date('now', ?)
            GROUP BY date
            ORDER BY date ASC
        """, (tenant_id, f'-{days} days')).fetchall()]


def get_all_usage_today() -> list:
    with get_conn() as conn:
        return [dict(r) for r in conn.execute("""
            SELECT
                t.id, t.name, t.slug, t.status, t.plan,
                t.calls_used, t.calls_limit, t.minutes_used,
                COALESCE(t.minutes_limit, 0)      AS minutes_limit,
                COALESCE(t.groq_daily_limit, 100000) AS groq_daily_limit,
                COALESCE(ul.calls_today, 0)    AS calls_today,
                COALESCE(ul.minutes_today, 0)  AS minutes_today,
                COALESCE(ul.errors_today, 0)   AS errors_today,
                COALESCE(ltok.total_tokens, 0) AS groq_tokens_today,
                COALESCE(ltok.llm_calls, 0)    AS groq_calls_today,
                COALESCE(ltok.prompt_tokens, 0)     AS prompt_tokens_today,
                COALESCE(ltok.completion_tokens, 0) AS completion_tokens_today,
                COALESCE(tts.chars_used, 0)    AS tts_chars_today,
                COALESCE(tc.stt_provider, 'sarvam')     AS stt_provider,
                COALESCE(tc.tts_provider, 'elevenlabs') AS tts_provider
            FROM tenants t
            LEFT JOIN (
                SELECT tenant_id,
                       SUM(calls_made)    AS calls_today,
                       SUM(minutes_used)  AS minutes_today,
                       SUM(api_errors)    AS errors_today
                FROM usage_logs WHERE date=date('now')
                GROUP BY tenant_id
            ) ul ON ul.tenant_id=t.id
            LEFT JOIN (
                SELECT tenant_id,
                       SUM(total_tokens)      AS total_tokens,
                       SUM(call_count)        AS llm_calls,
                       SUM(prompt_tokens)     AS prompt_tokens,
                       SUM(completion_tokens) AS completion_tokens
                FROM llm_token_usage WHERE date=date('now')
                GROUP BY tenant_id
            ) ltok ON ltok.tenant_id=t.id
            LEFT JOIN (
                SELECT tenant_id, SUM(chars_used) AS chars_used
                FROM tts_char_usage WHERE date=date('now') AND provider='elevenlabs'
                GROUP BY tenant_id
            ) tts ON tts.tenant_id=t.id
            LEFT JOIN tenant_configs tc ON tc.tenant_id=t.id
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


# ─────────────────────────────────────────
# LLM TOKEN USAGE TRACKING
# ─────────────────────────────────────────

def log_llm_tokens(tenant_id: int, provider: str, model: str,
                   prompt_tokens: int, completion_tokens: int):
    total = prompt_tokens + completion_tokens
    with get_conn() as conn:
        conn.execute("""
            INSERT INTO llm_token_usage
                (tenant_id, date, provider, model, prompt_tokens, completion_tokens, total_tokens, call_count)
            VALUES (?, date('now'), ?, ?, ?, ?, ?, 1)
            ON CONFLICT(tenant_id, date, provider, model) DO UPDATE SET
                prompt_tokens     = prompt_tokens     + excluded.prompt_tokens,
                completion_tokens = completion_tokens + excluded.completion_tokens,
                total_tokens      = total_tokens      + excluded.total_tokens,
                call_count        = call_count        + 1,
                updated_at        = datetime('now')
        """, (tenant_id, provider, model, prompt_tokens, completion_tokens, total))
        conn.commit()


def get_tenant_token_usage_today(tenant_id: int) -> list:
    with get_conn() as conn:
        return [dict(r) for r in conn.execute("""
            SELECT provider, model, prompt_tokens, completion_tokens,
                   total_tokens, call_count
            FROM llm_token_usage
            WHERE tenant_id=? AND date=date('now')
            ORDER BY total_tokens DESC
        """, (tenant_id,)).fetchall()]


def log_tts_chars(tenant_id: int, provider: str, chars: int):
    with get_conn() as conn:
        conn.execute("""
            INSERT INTO tts_char_usage (tenant_id, date, provider, chars_used, call_count)
            VALUES (?, date('now'), ?, ?, 1)
            ON CONFLICT(tenant_id, date, provider) DO UPDATE SET
                chars_used = chars_used + excluded.chars_used,
                call_count = call_count + 1,
                updated_at = datetime('now')
        """, (tenant_id, provider, chars))
        conn.commit()


def get_tenant_tts_chars_today(tenant_id: int, provider: str = "elevenlabs") -> int:
    with get_conn() as conn:
        row = conn.execute("""
            SELECT COALESCE(SUM(chars_used), 0) AS total
            FROM tts_char_usage
            WHERE tenant_id=? AND provider=? AND date=date('now')
        """, (tenant_id, provider)).fetchone()
        return row["total"] if row else 0


def get_all_tenants_token_usage_today() -> list:
    with get_conn() as conn:
        return [dict(r) for r in conn.execute("""
            SELECT
                t.id AS tenant_id, t.name, t.slug,
                COALESCE(SUM(u.total_tokens), 0)    AS tokens_today,
                COALESCE(SUM(u.call_count), 0)      AS llm_calls_today,
                COALESCE(SUM(u.prompt_tokens), 0)   AS prompt_tokens_today,
                COALESCE(SUM(u.completion_tokens),0) AS completion_tokens_today,
                GROUP_CONCAT(u.provider || ':' || u.model, ', ') AS providers_used
            FROM tenants t
            LEFT JOIN llm_token_usage u ON u.tenant_id=t.id AND u.date=date('now')
            GROUP BY t.id
            ORDER BY tokens_today DESC
        """).fetchall()]


# ─────────────────────────────────────────
# TENANT USER MANAGEMENT
# ─────────────────────────────────────────

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


# ─────────────────────────────────────────
# USAGE ALERTS
# ─────────────────────────────────────────

async def _fire_usage_alert(tenant_id: int, pct: float, alert_type: str) -> None:
    """Send Telegram alert when tenant hits 80% or 100% quota."""
    try:
        cfg     = get_tenant_config(tenant_id) or {}
        token   = (cfg.get("telegram_bot_token") or "").strip()
        chat_id = (cfg.get("telegram_chat_id")   or "").strip()
        if not token or not chat_id:
            return

        emoji = "\U0001F6A8" if pct >= 100 else "\u26A0\uFE0F"
        msg   = (
            f"{emoji} <b>Usage Alert — Tenant {tenant_id}</b>\n\n"
            f"\U0001F4CA Usage: <b>{pct:.0f}%</b> of call quota\n"
        )
        if pct >= 100:
            msg += "\U0001F534 All calls are now <b>blocked</b>. Upgrade to continue."
        else:
            msg += "\U0001F7E1 Approaching limit. Consider upgrading."

        import httpx
        async with httpx.AsyncClient(timeout=5) as client:
            await client.post(
                f"https://api.telegram.org/bot{token}/sendMessage",
                json={"chat_id": chat_id, "text": msg, "parse_mode": "HTML"},
            )
    except Exception:
        pass  # never propagate alert errors
