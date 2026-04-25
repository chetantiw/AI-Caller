"""
app/reseller_db.py
All DB functions for the reseller tier.
"""
from __future__ import annotations
import sqlite3, os, secrets, hashlib
from typing import Optional

DB_PATH = os.environ.get(
    "DB_PATH",
    os.path.join(os.path.dirname(__file__), '..', 'mutech.db')
)

def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

# ── Plan definitions ───────────────────────────────────────────────────────────
RESELLER_PLANS = {
    "silver": {
        "label":          "Silver",
        "max_numbers":    5,
        "max_channels":   3,
        "max_concurrent": 15,
        "max_tenants":    15,
        "minutes_floor":  10000,
        "outbound_rate":  5.00,
        "inbound_rate":   3.00,
        "overage_rate":   6.50,
    },
    "gold": {
        "label":          "Gold",
        "max_numbers":    10,
        "max_channels":   3,
        "max_concurrent": 30,
        "max_tenants":    30,
        "minutes_floor":  25000,
        "outbound_rate":  4.25,
        "inbound_rate":   2.75,
        "overage_rate":   5.50,
    },
    "platinum": {
        "label":          "Platinum",
        "max_numbers":    20,
        "max_channels":   5,
        "max_concurrent": 100,
        "max_tenants":    0,   # unlimited
        "minutes_floor":  50000,
        "outbound_rate":  3.50,
        "inbound_rate":   2.50,
        "overage_rate":   4.50,
    },
}

def _hash_password(password: str) -> str:
    return hashlib.sha256(password.encode()).hexdigest()

def _verify_password(password: str, hashed: str) -> bool:
    return hashlib.sha256(password.encode()).hexdigest() == hashed

# ── CRUD ───────────────────────────────────────────────────────────────────────

def create_reseller(
    name: str, slug: str, email: str, password: str,
    plan: str = "silver", company_name: str = "",
    commitment_months: int = 3,
) -> int:
    plan_cfg = RESELLER_PLANS.get(plan, RESELLER_PLANS["silver"])
    api_key  = secrets.token_urlsafe(32)
    from datetime import date, timedelta
    next_billing = (date.today() + timedelta(days=30 * commitment_months)).isoformat()
    with get_conn() as conn:
        cur = conn.execute("""
            INSERT INTO resellers (
                name, slug, email, password_hash, plan, company_name,
                max_numbers, max_channels, max_concurrent, max_tenants,
                minutes_included, outbound_rate, inbound_rate, overage_rate,
                commitment_months, next_billing_date, api_key
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (
            name, slug, email, _hash_password(password), plan,
            company_name or name,
            plan_cfg["max_numbers"], plan_cfg["max_channels"],
            plan_cfg["max_concurrent"], plan_cfg["max_tenants"],
            plan_cfg["minutes_floor"],
            plan_cfg["outbound_rate"], plan_cfg["inbound_rate"],
            plan_cfg["overage_rate"],
            commitment_months, next_billing, api_key,
        ))
        conn.commit()
        return cur.lastrowid


def get_reseller(reseller_id: int) -> Optional[dict]:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM resellers WHERE id=?", (reseller_id,)
        ).fetchone()
        return dict(row) if row else None


def get_reseller_by_email(email: str) -> Optional[dict]:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM resellers WHERE email=?", (email,)
        ).fetchone()
        return dict(row) if row else None


def get_reseller_by_slug(slug: str) -> Optional[dict]:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM resellers WHERE slug=?", (slug,)
        ).fetchone()
        return dict(row) if row else None


def get_all_resellers() -> list[dict]:
    with get_conn() as conn:
        return [dict(r) for r in conn.execute(
            "SELECT * FROM resellers ORDER BY created_at DESC"
        ).fetchall()]


def authenticate_reseller(email: str, password: str) -> Optional[dict]:
    r = get_reseller_by_email(email)
    if r and _verify_password(password, r["password_hash"]):
        return r
    return None


def update_reseller(reseller_id: int, **kwargs):
    allowed = {
        "name", "status", "plan", "company_name", "company_logo",
        "brand_color", "custom_domain", "max_numbers", "max_channels",
        "max_concurrent", "max_tenants", "minutes_included",
        "outbound_rate", "inbound_rate", "overage_rate",
        "commitment_months", "next_billing_date",
        "telegram_bot_token", "telegram_chat_id",
    }
    fields = {k: v for k, v in kwargs.items() if k in allowed}
    if not fields:
        return
    sets = ", ".join(f"{k}=?" for k in fields)
    with get_conn() as conn:
        conn.execute(
            f"UPDATE resellers SET {sets}, updated_at=datetime('now') WHERE id=?",
            list(fields.values()) + [reseller_id]
        )
        conn.commit()


# ── Tenant management ─────────────────────────────────────────────────────────

def get_reseller_tenants(reseller_id: int) -> list[dict]:
    with get_conn() as conn:
        return [dict(r) for r in conn.execute("""
            SELECT t.*, COALESCE(u.minutes_today,0) AS minutes_today,
                   COALESCE(u.calls_today,0) AS calls_today
            FROM tenants t
            LEFT JOIN (
                SELECT tenant_id,
                       SUM(minutes_used) AS minutes_today,
                       SUM(calls_made)   AS calls_today
                FROM usage_logs WHERE date=date('now')
                GROUP BY tenant_id
            ) u ON u.tenant_id=t.id
            WHERE t.reseller_id=?
            ORDER BY t.created_at DESC
        """, (reseller_id,)).fetchall()]


def count_reseller_tenants(reseller_id: int) -> int:
    with get_conn() as conn:
        return conn.execute(
            "SELECT COUNT(*) FROM tenants WHERE reseller_id=? AND status='active'",
            (reseller_id,)
        ).fetchone()[0]


def assign_tenant_to_reseller(tenant_id: int, reseller_id: int):
    with get_conn() as conn:
        conn.execute(
            "UPDATE tenants SET reseller_id=? WHERE id=?",
            (reseller_id, tenant_id)
        )
        conn.commit()


# ── Usage tracking ─────────────────────────────────────────────────────────────

def log_reseller_usage(
    reseller_id: int, tenant_id: int, call_id: int, duration_min: float
):
    with get_conn() as conn:
        conn.execute("""
            INSERT INTO reseller_usage (reseller_id, tenant_id, call_id, duration_min)
            VALUES (?,?,?,?)
        """, (reseller_id, tenant_id, call_id, duration_min))
        conn.execute("""
            UPDATE resellers
            SET minutes_used = minutes_used + ?
            WHERE id=?
        """, (duration_min, reseller_id))
        conn.commit()


def get_reseller_usage_this_month(reseller_id: int) -> dict:
    with get_conn() as conn:
        row = conn.execute("""
            SELECT
                COALESCE(SUM(duration_min), 0) AS minutes_used,
                COUNT(*)                        AS calls_count
            FROM reseller_usage
            WHERE reseller_id=?
              AND call_date >= date('now', 'start of month')
        """, (reseller_id,)).fetchone()
        return dict(row) if row else {"minutes_used": 0, "calls_count": 0}


def get_reseller_usage_daily(reseller_id: int, days: int = 30) -> list[dict]:
    with get_conn() as conn:
        return [dict(r) for r in conn.execute("""
            SELECT call_date AS date,
                   SUM(duration_min) AS minutes,
                   COUNT(*)          AS calls
            FROM reseller_usage
            WHERE reseller_id=?
              AND call_date >= date('now', ?)
            GROUP BY call_date ORDER BY call_date ASC
        """, (reseller_id, f"-{days} days")).fetchall()]


def check_reseller_quota(reseller_id: int) -> dict:
    r = get_reseller(reseller_id)
    if not r:
        return {"status": "unknown"}
    usage    = get_reseller_usage_this_month(reseller_id)
    used     = usage["minutes_used"]
    limit    = r["minutes_included"]
    pct      = (used / limit * 100) if limit else 0
    status   = "exceeded" if pct >= 100 else "warning_80" if pct >= 80 else "ok"
    return {
        "status":    status,
        "pct":       round(pct, 1),
        "used":      round(used, 1),
        "limit":     limit,
        "remaining": max(0, round(limit - used, 1)),
        "calls":     usage["calls_count"],
    }


def check_tenant_limit(reseller_id: int) -> dict:
    r = get_reseller(reseller_id)
    if not r:
        return {"allowed": False, "reason": "Reseller not found"}
    limit   = r["max_tenants"]
    current = count_reseller_tenants(reseller_id)
    if limit == 0:
        return {"allowed": True, "current": current, "limit": "Unlimited"}
    if current >= limit:
        return {
            "allowed": False,
            "reason":  f"Your {r['plan'].title()} plan allows {limit} tenants. Upgrade to add more.",
            "current": current,
            "limit":   limit,
        }
    return {"allowed": True, "current": current, "limit": limit}


# ── Stats for superadmin ───────────────────────────────────────────────────────

def get_reseller_stats() -> dict:
    with get_conn() as conn:
        total    = conn.execute("SELECT COUNT(*) FROM resellers").fetchone()[0]
        active   = conn.execute("SELECT COUNT(*) FROM resellers WHERE status='active'").fetchone()[0]
        by_plan  = {r[0]: r[1] for r in conn.execute(
            "SELECT plan, COUNT(*) FROM resellers GROUP BY plan"
        ).fetchall()}
        total_min = conn.execute(
            "SELECT COALESCE(SUM(duration_min),0) FROM reseller_usage"
        ).fetchone()[0]
    return {
        "total_resellers":  total,
        "active_resellers": active,
        "by_plan":          by_plan,
        "total_minutes":    round(total_min, 1),
    }
