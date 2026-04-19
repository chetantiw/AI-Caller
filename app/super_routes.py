"""
app/super_routes.py
Super Admin API routes for MuTech AI Caller SaaS Platform
Endpoints: /super/api/*
All routes require JWT with role=superadmin
"""

import os
import jwt
import aiohttp
from datetime import datetime, timedelta
from fastapi import APIRouter, HTTPException, Depends, Request
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel
from typing import Optional
from app import tenant_db as tdb

router = APIRouter()
security = HTTPBearer()
JWT_SECRET = os.getenv("JWT_SECRET", "mutech-secret-2026")


# ─────────────────────────────────────────
# AUTH HELPERS
# ─────────────────────────────────────────

def create_token(user_id: int, username: str) -> str:
    payload = {
        "sub":      str(user_id),
        "username": username,
        "role":     "superadmin",
        "exp":      datetime.utcnow() + timedelta(hours=12),
    }
    return jwt.encode(payload, JWT_SECRET, algorithm="HS256")


def verify_super_token(credentials: HTTPAuthorizationCredentials = Depends(security)):
    try:
        payload = jwt.decode(credentials.credentials, JWT_SECRET, algorithms=["HS256"])
        if payload.get("role") != "superadmin":
            raise HTTPException(status_code=403, detail="Super admin access required")
        return payload
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token expired")
    except Exception:
        raise HTTPException(status_code=401, detail="Invalid token")


# ─────────────────────────────────────────
# REQUEST MODELS
# ─────────────────────────────────────────

class SuperLoginRequest(BaseModel):
    username: str
    password: str


class CreateTenantRequest(BaseModel):
    name:           str
    slug:           str
    plan:           str = "starter"
    contact_name:   Optional[str] = None
    contact_email:  Optional[str] = None
    contact_phone:  Optional[str] = None
    calls_limit:    int = 1000
    admin_username: Optional[str] = None
    admin_password: Optional[str] = None


class UpdateTenantRequest(BaseModel):
    name:             Optional[str] = None
    plan:             Optional[str] = None
    status:           Optional[str] = None
    calls_limit:      Optional[int] = None
    groq_daily_limit: Optional[int] = None
    contact_name:     Optional[str] = None
    contact_email:    Optional[str] = None
    contact_phone:    Optional[str] = None


class UpdateTenantStatusRequest(BaseModel):
    status: str  # active | suspended | expired


class UpdateTenantConfigRequest(BaseModel):
    # Agent
    agent_name:           Optional[str] = None
    agent_language:       Optional[str] = None
    agent_voice:          Optional[str] = None
    system_prompt:        Optional[str] = None
    greeting_template:    Optional[str] = None
    # Speech
    sarvam_api_key:       Optional[str] = None
    elevenlabs_api_key:   Optional[str] = None
    elevenlabs_voice_id:  Optional[str] = None
    elevenlabs_model:     Optional[str] = None
    stt_provider:         Optional[str] = None
    tts_provider:         Optional[str] = None
    # LLM
    groq_api_key:         Optional[str] = None
    xai_api_key:          Optional[str] = None
    openai_api_key:       Optional[str] = None
    llm_provider:         Optional[str] = None
    llm_model:            Optional[str] = None
    # Telephony
    piopiy_agent_id:      Optional[str] = None
    piopiy_agent_token:   Optional[str] = None
    piopiy_number:        Optional[str] = None
    exotel_sid:           Optional[str] = None
    exotel_api_key:       Optional[str] = None
    exotel_api_token:     Optional[str] = None
    exotel_number:        Optional[str] = None
    # Notifications
    telegram_bot_token:   Optional[str] = None
    telegram_chat_id:     Optional[str] = None
    whatsapp_api_key:     Optional[str] = None
    whatsapp_number:      Optional[str] = None
    # Webhook
    webhook_url:          Optional[str] = None
    webhook_events:       Optional[str] = None
    webhook_secret:       Optional[str] = None


# ─────────────────────────────────────────
# AUTH
# ─────────────────────────────────────────

@router.post("/api/auth/login")
async def super_login(req: SuperLoginRequest):
    admin = tdb.verify_super_admin(req.username, req.password)
    if not admin:
        raise HTTPException(status_code=401, detail="Invalid credentials")
    token = create_token(admin["id"], admin["username"])
    return {
        "token":    token,
        "role":     "superadmin",
        "name":     admin.get("name", "Super Admin"),
        "username": admin["username"],
    }


# ─────────────────────────────────────────
# PLATFORM DASHBOARD
# ─────────────────────────────────────────

@router.get("/api/dashboard")
async def super_dashboard(auth=Depends(verify_super_token)):
    stats   = tdb.get_platform_stats()
    tenants = tdb.get_all_usage_today()
    return {"stats": stats, "tenants": tenants}


@router.get("/api/token-usage")
async def super_token_usage(auth=Depends(verify_super_token)):
    """Per-tenant LLM token usage today — for quota monitoring."""
    rows = tdb.get_all_tenants_token_usage_today()
    GROQ_FREE_LIMIT = 100_000
    result = []
    for r in rows:
        used = r["tokens_today"]
        pct  = round(used / GROQ_FREE_LIMIT * 100, 1) if used else 0
        result.append({
            **r,
            "groq_limit":   GROQ_FREE_LIMIT,
            "groq_pct":     pct,
            "groq_status":  "exhausted" if used >= GROQ_FREE_LIMIT
                            else ("warning" if pct >= 80 else "ok"),
        })
    return {"date": "today", "tenants": result}


# ─────────────────────────────────────────
# TENANT MANAGEMENT
# ─────────────────────────────────────────

@router.get("/api/tenants")
async def list_tenants(auth=Depends(verify_super_token)):
    return {"tenants": tdb.get_all_tenants()}


@router.post("/api/tenants")
async def create_tenant(req: CreateTenantRequest, auth=Depends(verify_super_token)):
    from app import telegram_notify as tg
    # Check slug is unique
    existing = tdb.get_tenant_by_slug(req.slug)
    if existing:
        raise HTTPException(status_code=400, detail=f"Slug '{req.slug}' already exists")
    tenant_id = tdb.create_tenant(
        name          = req.name,
        slug          = req.slug,
        plan          = req.plan,
        contact_name  = req.contact_name,
        contact_email = req.contact_email,
        contact_phone = req.contact_phone,
        calls_limit   = req.calls_limit,
    )
    # Use provided credentials or auto-generate
    import secrets, string
    temp_password  = req.admin_password or ''.join(secrets.choice(string.ascii_letters + string.digits) for _ in range(10))
    admin_username = req.admin_username or (req.contact_email or req.slug).split('@')[0].lower().replace(' ', '')
    try:
        tdb.create_tenant_user(
            tenant_id = tenant_id,
            username  = admin_username,
            password  = temp_password,
            role      = 'admin',
            name      = req.contact_name or 'Admin',
            email     = req.contact_email or '',
        )
    except Exception:
        # Username conflict — append tenant_id to make unique
        admin_username = f"{admin_username}_{tenant_id}"
        tdb.create_tenant_user(
            tenant_id = tenant_id,
            username  = admin_username,
            password  = temp_password,
            role      = 'admin',
            name      = req.contact_name or 'Admin',
            email     = req.contact_email or '',
        )
    dashboard_url = "https://ai.mutechautomation.com/dashboard"
    # Notify super admin via Telegram with full credentials
    await tg.notify_tenant_created(
        tenant_name    = req.name,
        slug           = req.slug,
        plan           = req.plan,
        admin_username = admin_username,
        admin_password = temp_password,
        dashboard_url  = dashboard_url,
        contact_email  = req.contact_email or "",
    )
    return {
        "tenant_id":      tenant_id,
        "message":        "Tenant created successfully",
        "admin_username": admin_username,
        "temp_password":  temp_password,
        "dashboard_url":  dashboard_url,
        "note":           "Share these credentials with the tenant. They should change password on first login."
    }


@router.get("/api/tenants/{tenant_id}")
async def get_tenant(tenant_id: int, auth=Depends(verify_super_token)):
    tenant = tdb.get_tenant(tenant_id)
    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant not found")
    config = tdb.get_tenant_config(tenant_id)
    users  = tdb.get_tenant_users(tenant_id)
    usage  = tdb.get_tenant_usage(tenant_id, days=30)
    return {
        "tenant": tenant,
        "config": config,
        "users":  users,
        "usage":  usage,
    }


@router.put("/api/tenants/{tenant_id}")
async def update_tenant(tenant_id: int, req: UpdateTenantRequest,
                        auth=Depends(verify_super_token)):
    tenant = tdb.get_tenant(tenant_id)
    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant not found")
    updates = {k: v for k, v in req.dict().items() if v is not None}
    if not updates:
        raise HTTPException(status_code=400, detail="No fields to update")
    if "status" in updates and updates["status"] not in ("active", "suspended", "expired"):
        raise HTTPException(status_code=400, detail="Invalid status value")
    if "plan" in updates and updates["plan"] not in ("starter", "growth", "pro", "enterprise"):
        raise HTTPException(status_code=400, detail="Invalid plan value")
    tdb.update_tenant(tenant_id, **updates)
    return {"message": "Tenant updated successfully"}


@router.put("/api/tenants/{tenant_id}/status")
async def update_tenant_status(tenant_id: int, req: UpdateTenantStatusRequest,
                                auth=Depends(verify_super_token)):
    if req.status not in ("active", "suspended", "expired"):
        raise HTTPException(status_code=400, detail="Invalid status")
    tdb.update_tenant_status(tenant_id, req.status)
    return {"message": f"Tenant status updated to {req.status}"}


@router.delete("/api/tenants/{tenant_id}")
async def delete_tenant(tenant_id: int, auth=Depends(verify_super_token)):
    if tenant_id == 1:
        raise HTTPException(status_code=400, detail="Cannot delete primary tenant")
    tdb.delete_tenant(tenant_id)
    return {"message": "Tenant deleted"}


# ─────────────────────────────────────────
# TENANT CONFIG
# ─────────────────────────────────────────

@router.get("/api/tenants/{tenant_id}/config")
async def get_tenant_config(tenant_id: int, auth=Depends(verify_super_token)):
    config = tdb.get_tenant_config(tenant_id)
    if not config:
        raise HTTPException(status_code=404, detail="Config not found")
    # Mask sensitive keys for display
    for key in ("sarvam_api_key", "groq_api_key", "piopiy_agent_token",
                "exotel_api_key", "exotel_api_token", "telegram_bot_token"):
        if config.get(key):
            config[key] = config[key][:8] + "••••••••"
    return {"config": config}


@router.put("/api/tenants/{tenant_id}/config")
async def update_tenant_config(tenant_id: int, req: UpdateTenantConfigRequest,
                                auth=Depends(verify_super_token)):
    updates = {k: v for k, v in req.dict().items() if v is not None}
    tdb.update_tenant_config(tenant_id, **updates)
    return {"message": "Config updated successfully"}


# ─────────────────────────────────────────
# USAGE
# ─────────────────────────────────────────

@router.get("/api/tenants/{tenant_id}/usage")
async def get_usage(tenant_id: int, days: int = 30, auth=Depends(verify_super_token)):
    usage = tdb.get_tenant_usage(tenant_id, days=days)
    return {"usage": usage, "days": days}


# ─────────────────────────────────────────
# DEBUG — Test tenant API keys
# ─────────────────────────────────────────

@router.get("/api/tenants/{tenant_id}/debug")
async def debug_tenant(tenant_id: int, auth=Depends(verify_super_token)):
    config = tdb.get_tenant_config(tenant_id)
    if not config:
        raise HTTPException(status_code=404, detail="Config not found")

    results = {}

    # Test Sarvam API
    sarvam_key = config.get("sarvam_api_key")
    if sarvam_key:
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    "https://api.sarvam.ai/v1/models",
                    headers={"api-subscription-key": sarvam_key},
                    timeout=aiohttp.ClientTimeout(total=5)
                ) as resp:
                    results["sarvam"] = {
                        "status": "ok" if resp.status == 200 else "error",
                        "code": resp.status
                    }
        except Exception as e:
            results["sarvam"] = {"status": "error", "message": str(e)}
    else:
        results["sarvam"] = {"status": "not_configured"}

    # Test Groq API
    groq_key = config.get("groq_api_key")
    if groq_key:
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    "https://api.groq.com/openai/v1/models",
                    headers={"Authorization": f"Bearer {groq_key}"},
                    timeout=aiohttp.ClientTimeout(total=5)
                ) as resp:
                    results["groq"] = {
                        "status": "ok" if resp.status == 200 else "error",
                        "code": resp.status
                    }
        except Exception as e:
            results["groq"] = {"status": "error", "message": str(e)}
    else:
        results["groq"] = {"status": "not_configured"}

    # Test Telegram Bot
    tg_token = config.get("telegram_bot_token")
    if tg_token:
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    f"https://api.telegram.org/bot{tg_token}/getMe",
                    timeout=aiohttp.ClientTimeout(total=5)
                ) as resp:
                    data = await resp.json()
                    results["telegram"] = {
                        "status": "ok" if data.get("ok") else "error",
                        "bot_name": data.get("result", {}).get("username", "")
                    }
        except Exception as e:
            results["telegram"] = {"status": "error", "message": str(e)}
    else:
        results["telegram"] = {"status": "not_configured"}

    # PIOPIY — just check if credentials are set
    results["piopiy"] = {
        "status": "configured" if config.get("piopiy_agent_id") else "not_configured",
        "number": config.get("piopiy_number", "—"),
    }

    return {"tenant_id": tenant_id, "debug": results}


# ─────────────────────────────────────────
# COMPREHENSIVE QUOTA & USAGE (per tenant, today)
# ─────────────────────────────────────────

class UpdateQuotaRequest(BaseModel):
    calls_limit:      Optional[int]   = None
    minutes_limit:    Optional[float] = None
    groq_daily_limit: Optional[int]   = None


class AddonRequest(BaseModel):
    minutes:    int
    amount_inr: float = 0.0
    notes:      Optional[str] = ""


@router.get("/api/quotas")
async def get_all_quotas(auth=Depends(verify_super_token)):
    """All tenants with full service usage today + quota config."""
    import httpx, asyncio

    rows = tdb.get_all_usage_today()

    # Live key-validity checks per tenant (parallel)
    async def _check_key(key_type: str, key: str) -> str:
        if not key:
            return "not_configured"
        try:
            async with httpx.AsyncClient(timeout=4) as c:
                if key_type == "groq":
                    r = await c.get("https://api.groq.com/openai/v1/models",
                                    headers={"Authorization": f"Bearer {key}"})
                elif key_type == "elevenlabs":
                    r = await c.get("https://api.elevenlabs.io/v1/models",
                                    headers={"xi-api-key": key})
                elif key_type == "sarvam":
                    r = await c.get("https://api.sarvam.ai/v1/models",
                                    headers={"api-subscription-key": key})
                else:
                    return "unknown"
            return "ok" if r.status_code == 200 else ("invalid_key" if r.status_code in (401,403) else f"error_{r.status_code}")
        except Exception:
            return "unreachable"

    # Fetch configs for all tenants and check keys in parallel
    all_configs = {r["id"]: (tdb.get_tenant_config(r["id"]) or {}) for r in rows}
    checks = []
    for r in rows:
        cfg = all_configs[r["id"]]
        checks.append(_check_key("groq",        cfg.get("groq_api_key",       "")))
        checks.append(_check_key("elevenlabs",  cfg.get("elevenlabs_api_key", "")))
        checks.append(_check_key("sarvam",      cfg.get("sarvam_api_key",     "")))

    statuses = await asyncio.gather(*checks, return_exceptions=True)

    result = []
    for i, r in enumerate(rows):
        groq_key_ok   = statuses[i*3]   if not isinstance(statuses[i*3],   Exception) else "unreachable"
        labs_key_ok   = statuses[i*3+1] if not isinstance(statuses[i*3+1], Exception) else "unreachable"
        sarvam_key_ok = statuses[i*3+2] if not isinstance(statuses[i*3+2], Exception) else "unreachable"

        groq_limit = r.get("groq_daily_limit") or 100_000
        groq_used  = r.get("groq_tokens_today", 0)
        groq_pct   = round(groq_used / groq_limit * 100, 1) if groq_limit else 0
        calls_pct  = round(r.get("calls_used", 0) / r["calls_limit"] * 100, 1) if r["calls_limit"] else 0

        result.append({
            **r,
            "groq_daily_limit": groq_limit,
            "groq_pct":         groq_pct,
            "groq_status":      "exhausted" if groq_used >= groq_limit
                                else ("warning" if groq_pct >= 80 else groq_key_ok),
            "groq_key_status":  groq_key_ok,
            "labs_key_status":  labs_key_ok,
            "sarvam_key_status": sarvam_key_ok,
            "calls_pct":        calls_pct,
            "calls_status":     "exhausted" if calls_pct >= 100
                                else ("warning" if calls_pct >= 80 else "ok"),
        })
    return {"date": "today", "tenants": result}


@router.post("/api/tenants/{tenant_id}/addon")
async def grant_addon_minutes(tenant_id: int, req: AddonRequest,
                              auth=Depends(verify_super_token)):
    """Grant add-on minutes to a tenant and notify via platform Telegram."""
    tenant = tdb.get_tenant(tenant_id)
    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant not found")
    if req.minutes <= 0:
        raise HTTPException(status_code=400, detail="minutes must be positive")

    new_limit = tdb.create_addon_purchase(
        tenant_id  = tenant_id,
        minutes    = req.minutes,
        amount_inr = req.amount_inr,
        notes      = req.notes or "",
    )

    # Notify via platform tenant 1's Telegram config
    try:
        cfg = tdb.get_tenant_config(1) or {}
        bot_token = cfg.get("telegram_bot_token", "")
        chat_id   = cfg.get("telegram_chat_id", "")
        if bot_token and chat_id:
            msg = (
                f"➕ <b>Add-on Minutes Granted</b>\n\n"
                f"🏢 Tenant: <b>{tenant['name']}</b> (ID {tenant_id})\n"
                f"⏱ Minutes Added: <b>{req.minutes:,}</b>\n"
                f"💰 Amount: ₹{req.amount_inr:,.2f}\n"
                f"📊 New Limit: <b>{new_limit:,}</b> calls\n"
                f"📝 Notes: {req.notes or '—'}"
            )
            url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
            async with aiohttp.ClientSession() as session:
                await session.post(url, json={
                    "chat_id": chat_id, "text": msg, "parse_mode": "HTML"
                }, timeout=aiohttp.ClientTimeout(total=5))
    except Exception:
        pass  # Telegram failure must never block the response

    return {
        "ok":              True,
        "tenant_id":       tenant_id,
        "minutes_granted": req.minutes,
        "new_calls_limit": new_limit,
        "message":         f"Granted {req.minutes} minutes to {tenant.get('name')}",
    }


@router.put("/api/tenants/{tenant_id}/quota")
async def update_quota(tenant_id: int, req: UpdateQuotaRequest,
                       auth=Depends(verify_super_token)):
    """Update calls_limit and/or groq_daily_limit for a tenant."""
    tenant = tdb.get_tenant(tenant_id)
    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant not found")
    updates = {k: v for k, v in req.dict().items() if v is not None}
    if not updates:
        raise HTTPException(status_code=400, detail="No fields to update")
    tdb.update_tenant(tenant_id, **updates)
    return {"message": "Quota updated", "updates": updates}
