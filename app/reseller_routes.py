"""
app/reseller_routes.py
All API routes for the reseller portal.
Mount with: app.include_router(reseller_router, prefix="")
"""
from __future__ import annotations
import os, secrets
from datetime import datetime, timedelta
from fastapi import APIRouter, Request, HTTPException, Depends
from fastapi.responses import JSONResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles

reseller_router = APIRouter()

# ── JWT helpers (reuse same secret as main app) ───────────────────────────────
import jwt as _jwt

JWT_SECRET  = os.getenv("JWT_SECRET", "mutech-secret-2026")
JWT_ALGO    = "HS256"
JWT_EXPIRE  = 24  # hours


def _make_token(reseller_id: int, email: str) -> str:
    payload = {
        "sub":         str(reseller_id),
        "email":       email,
        "role":        "reseller",
        "exp":         datetime.utcnow() + timedelta(hours=JWT_EXPIRE),
    }
    return _jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGO)


def _verify_reseller_token(request: Request) -> dict:
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing reseller token")
    try:
        payload = _jwt.decode(
            auth[7:], JWT_SECRET, algorithms=[JWT_ALGO]
        )
        if payload.get("role") != "reseller":
            raise HTTPException(status_code=403, detail="Not a reseller token")
        return payload
    except _jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token expired")
    except Exception:
        raise HTTPException(status_code=401, detail="Invalid token")


# ── Serve reseller dashboard HTML ─────────────────────────────────────────────
@reseller_router.get("/reseller", response_class=HTMLResponse)
@reseller_router.get("/reseller/", response_class=HTMLResponse)
async def serve_reseller_dashboard():
    path = os.path.join(
        os.path.dirname(__file__), '..', 'static', 'reseller_dashboard.html'
    )
    if os.path.exists(path):
        return HTMLResponse(content=open(path).read())
    return HTMLResponse(content="<h2>Reseller dashboard not found</h2>", status_code=404)


# ── AUTH ──────────────────────────────────────────────────────────────────────

@reseller_router.post("/reseller/api/login")
async def reseller_login(request: Request):
    from app.reseller_db import authenticate_reseller
    data     = await request.json()
    email    = (data.get("email") or "").strip().lower()
    password = (data.get("password") or "").strip()
    if not email or not password:
        raise HTTPException(400, "Email and password required")
    r = authenticate_reseller(email, password)
    if not r:
        raise HTTPException(401, "Invalid credentials")
    if r["status"] != "active":
        raise HTTPException(403, "Account suspended. Contact support.")
    token = _make_token(r["id"], r["email"])
    return {
        "token":        token,
        "reseller_id":  r["id"],
        "name":         r["name"],
        "email":        r["email"],
        "plan":         r["plan"],
        "company_name": r["company_name"],
    }


# ── RESELLER PROFILE ──────────────────────────────────────────────────────────

@reseller_router.get("/reseller/api/profile")
async def reseller_profile(payload=Depends(_verify_reseller_token)):
    from app.reseller_db import get_reseller, check_reseller_quota, RESELLER_PLANS
    rid = int(payload["sub"])
    r   = get_reseller(rid)
    if not r:
        raise HTTPException(404, "Reseller not found")
    quota    = check_reseller_quota(rid)
    plan_cfg = RESELLER_PLANS.get(r["plan"], {})
    return {
        "reseller":  {k: v for k, v in r.items() if k != "password_hash"},
        "quota":     quota,
        "plan_info": plan_cfg,
    }


@reseller_router.put("/reseller/api/profile")
async def update_reseller_profile(request: Request, payload=Depends(_verify_reseller_token)):
    from app.reseller_db import update_reseller
    rid  = int(payload["sub"])
    data = await request.json()
    allowed = {"company_name", "brand_color", "telegram_bot_token", "telegram_chat_id"}
    fields  = {k: v for k, v in data.items() if k in allowed}
    if fields:
        update_reseller(rid, **fields)
    return {"ok": True}


# ── TENANTS ───────────────────────────────────────────────────────────────────

@reseller_router.get("/reseller/api/tenants")
async def reseller_list_tenants(payload=Depends(_verify_reseller_token)):
    from app.reseller_db import get_reseller_tenants, check_tenant_limit
    rid = int(payload["sub"])
    tenants = get_reseller_tenants(rid)
    gate    = check_tenant_limit(rid)
    return {"tenants": tenants, "tenant_gate": gate}


@reseller_router.post("/reseller/api/tenants")
async def reseller_create_tenant(request: Request, payload=Depends(_verify_reseller_token)):
    from app.reseller_db import check_tenant_limit, assign_tenant_to_reseller
    import app.tenant_db as tdb
    import re

    rid  = int(payload["sub"])
    gate = check_tenant_limit(rid)
    if not gate["allowed"]:
        raise HTTPException(402, gate["reason"])

    data         = await request.json()
    company_name = (data.get("company_name") or "").strip()
    contact_name = (data.get("contact_name") or "").strip()
    email        = (data.get("email") or "").strip().lower()
    password     = data.get("password") or secrets.token_urlsafe(8)
    plan         = data.get("plan", "starter")

    if not company_name:
        raise HTTPException(400, "Company name is required")

    # Generate slug
    base_slug = re.sub(r'[^a-z0-9]+', '-', company_name.lower()).strip('-')
    slug      = base_slug
    existing  = {t["slug"] for t in tdb.get_all_tenants()}
    counter   = 1
    while slug in existing:
        slug = f"{base_slug}-{counter}"; counter += 1

    calls_limit = {"starter": 500, "growth": 2500, "pro": 5000}.get(plan, 500)
    tenant_id   = tdb.create_tenant(
        name=company_name, slug=slug, plan=plan,
        contact_name=contact_name, calls_limit=calls_limit,
    )
    assign_tenant_to_reseller(tenant_id, rid)

    # Create admin user
    tdb.create_tenant_user(
        tenant_id=tenant_id, username=email or f"admin@{slug}",
        password=password, role="admin",
        name=contact_name, email=email,
    )

    return {
        "ok":          True,
        "tenant_id":   tenant_id,
        "slug":        slug,
        "credentials": {"username": email or f"admin@{slug}", "password": password},
        "message":     f"Tenant '{company_name}' created successfully.",
    }


@reseller_router.put("/reseller/api/tenants/{tenant_id}/status")
async def reseller_update_tenant_status(
    tenant_id: int, request: Request, payload=Depends(_verify_reseller_token)
):
    from app.reseller_db import get_reseller_tenants
    import app.tenant_db as tdb
    rid     = int(payload["sub"])
    # Verify this tenant belongs to reseller
    tenants = get_reseller_tenants(rid)
    if not any(t["id"] == tenant_id for t in tenants):
        raise HTTPException(403, "Tenant not found in your account")
    data   = await request.json()
    status = data.get("status", "active")
    if status not in ("active", "suspended"):
        raise HTTPException(400, "Invalid status")
    tdb.update_tenant_status(tenant_id, status)
    return {"ok": True}


# ── USAGE ─────────────────────────────────────────────────────────────────────

@reseller_router.get("/reseller/api/usage")
async def reseller_usage(payload=Depends(_verify_reseller_token)):
    from app.reseller_db import (
        check_reseller_quota, get_reseller_usage_daily,
        get_reseller_tenants, get_reseller
    )
    rid     = int(payload["sub"])
    quota   = check_reseller_quota(rid)
    daily   = get_reseller_usage_daily(rid, days=30)
    tenants = get_reseller_tenants(rid)

    # Per-tenant usage this month
    per_tenant = []
    for t in tenants:
        per_tenant.append({
            "tenant_id":    t["id"],
            "tenant_name":  t["name"],
            "calls_today":  t.get("calls_today", 0),
            "minutes_today":t.get("minutes_today", 0),
        })

    return {"quota": quota, "daily": daily, "per_tenant": per_tenant}


# ── PLAN INFO ─────────────────────────────────────────────────────────────────

@reseller_router.get("/reseller/api/plan-info")
async def reseller_plan_info(payload=Depends(_verify_reseller_token)):
    from app.reseller_db import get_reseller, RESELLER_PLANS, check_reseller_quota, check_tenant_limit
    rid  = int(payload["sub"])
    r    = get_reseller(rid)
    plan = RESELLER_PLANS.get(r["plan"], {})
    return {
        "plan":         r["plan"],
        "plan_details": plan,
        "quota":        check_reseller_quota(rid),
        "tenant_gate":  check_tenant_limit(rid),
        "all_plans":    RESELLER_PLANS,
    }


# ── SUPERADMIN: manage resellers ──────────────────────────────────────────────

def _require_super(request: Request):
    """Reuse superadmin JWT verification from super_routes.py"""
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        raise HTTPException(401, "Superadmin token required")
    try:
        payload = _jwt.decode(auth[7:], JWT_SECRET, algorithms=[JWT_ALGO])
        if payload.get("role") not in ("superadmin", "super"):
            raise HTTPException(403, "Superadmin only")
        return payload
    except Exception:
        # Try with the app's JWT secret
        try:
            APP_SECRET = os.getenv("JWT_SECRET", "mutech-secret-2026")
            payload = _jwt.decode(auth[7:], APP_SECRET, algorithms=[JWT_ALGO])
            if payload.get("role") not in ("superadmin", "super"):
                raise HTTPException(403, "Superadmin only")
            return payload
        except Exception:
            raise HTTPException(401, "Invalid superadmin token")


@reseller_router.get("/super/api/resellers")
async def super_list_resellers(request: Request, _=Depends(_require_super)):
    from app.reseller_db import get_all_resellers, get_reseller_stats
    return {"resellers": get_all_resellers(), "stats": get_reseller_stats()}


@reseller_router.post("/super/api/resellers")
async def super_create_reseller(request: Request, _=Depends(_require_super)):
    from app.reseller_db import create_reseller, get_reseller_by_email
    import re
    data     = await request.json()
    name     = (data.get("name") or "").strip()
    email    = (data.get("email") or "").strip().lower()
    password = data.get("password") or secrets.token_urlsafe(10)
    plan     = data.get("plan", "silver")

    if not name or not email:
        raise HTTPException(400, "Name and email required")
    if get_reseller_by_email(email):
        raise HTTPException(409, "Reseller with this email already exists")

    slug = re.sub(r'[^a-z0-9]+', '-', name.lower()).strip('-')
    rid  = create_reseller(
        name=name, slug=slug, email=email, password=password,
        plan=plan, company_name=data.get("company_name", name),
        commitment_months=int(data.get("commitment_months", 3)),
    )
    return {"ok": True, "reseller_id": rid, "password": password,
            "message": f"Reseller '{name}' created on {plan} plan."}


@reseller_router.put("/super/api/resellers/{rid}")
async def super_update_reseller(rid: int, request: Request, _=Depends(_require_super)):
    from app.reseller_db import update_reseller
    data = await request.json()
    update_reseller(rid, **data)
    return {"ok": True}


@reseller_router.get("/super/api/resellers/{rid}/usage")
async def super_reseller_usage(rid: int, _=Depends(_require_super)):
    from app.reseller_db import check_reseller_quota, get_reseller_usage_daily, get_reseller
    return {
        "reseller": get_reseller(rid),
        "quota":    check_reseller_quota(rid),
        "daily":    get_reseller_usage_daily(rid, days=30),
    }
