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
    name:          Optional[str] = None
    plan:          Optional[str] = None
    status:        Optional[str] = None
    calls_limit:   Optional[int] = None
    contact_name:  Optional[str] = None
    contact_email: Optional[str] = None
    contact_phone: Optional[str] = None


class UpdateTenantStatusRequest(BaseModel):
    status: str  # active | suspended | expired


class UpdateTenantConfigRequest(BaseModel):
    agent_name:         Optional[str] = None
    agent_language:     Optional[str] = None
    agent_voice:        Optional[str] = None
    system_prompt:      Optional[str] = None
    greeting_template:  Optional[str] = None
    piopiy_agent_id:    Optional[str] = None
    piopiy_agent_token: Optional[str] = None
    piopiy_number:      Optional[str] = None
    sarvam_api_key:     Optional[str] = None
    groq_api_key:       Optional[str] = None
    exotel_sid:         Optional[str] = None
    exotel_api_key:     Optional[str] = None
    exotel_api_token:   Optional[str] = None
    exotel_number:      Optional[str] = None
    telegram_bot_token: Optional[str] = None
    telegram_chat_id:   Optional[str] = None


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
    if "plan" in updates and updates["plan"] not in ("starter", "growth", "enterprise"):
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
