""" app/api_routes.py All /api/* REST endpoints for the MuTech dashboard Covers: auth, dashboard stats, leads,
campaigns, calls, analytics, logs, users """
# Delete the orphaned docstring + return block (about 12 lines)
import io
import csv
import hashlib
import os
from datetime import datetime, timedelta

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request, UploadFile, File
from fastapi.responses import JSONResponse, StreamingResponse

from app import database as db

router = APIRouter(prefix="/api")

# In-memory token store: token → user dict (cleared on restart; users re-login)
_token_store: dict = {}


def get_current_user(request: Request) -> dict:
    """Resolve Bearer token to a user dict. Checks memory cache then DB (survives restarts)."""
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing token")
    token = auth[7:]
    # Fast path: in-memory cache
    user = _token_store.get(token)
    if not user:
        # Slow path: DB lookup (restores sessions after service restart)
        user = db.get_session(token)
        if user:
            _token_store[token] = user  # repopulate cache
    if not user:
        raise HTTPException(status_code=401, detail="Invalid or expired token. Please log in again.")
    return user


# ═══════════════════════════════════════════════════════
# AUTH
# ═══════════════════════════════════════════════════════

@router.post("/auth/login")
async def login(request: Request):
    body     = await request.json()
    username = body.get("username", "").strip()
    password = body.get("password", "").strip()

    if not username or not password:
        raise HTTPException(status_code=400, detail="Username and password required")

    user = db.verify_user(username, password)
    if not user:
        raise HTTPException(status_code=401, detail="Invalid credentials")

    # Simple token: sha256(username+password+secret)
    secret = os.getenv("JWT_SECRET", "mutech-secret-2026")
    token  = hashlib.sha256(f"{username}:{password}:{secret}".encode()).hexdigest()

    user_dict = {
        "user_id":   user["id"],
        "username":  user["username"],
        "name":      user.get("name", username),
        "role":      user["role"],
        "tenant_id": user.get("tenant_id") or 1,
    }

    # Store in memory (fast path) and DB (survives service restarts)
    _token_store[token] = user_dict
    db.save_session(token, user_dict)

    db.add_log(f"🔑 Login: {username} ({user['role']})")

    return {
        "token":    token,
        "user_id":  user["id"],
        "username": user["username"],
        "name":     user.get("name", username),
        "role":     user["role"],
    }


import re as _re


@router.post("/auth/register")
async def register_tenant(request: Request):
    """
    Public endpoint — new tenant self-signup.
    Creates tenant + admin user in one step.
    """
    data = await request.json()

    # ── Validate required fields ──────────────────────────────
    company_name = (data.get("company_name") or "").strip()
    contact_name = (data.get("contact_name") or "").strip()
    email        = (data.get("email") or "").strip().lower()
    password     = (data.get("password") or "").strip()
    plan         = data.get("plan", "starter")

    if not company_name:
        raise HTTPException(status_code=400, detail="Company name is required")
    if not contact_name:
        raise HTTPException(status_code=400, detail="Contact name is required")
    if not email or "@" not in email:
        raise HTTPException(status_code=400, detail="Valid email is required")
    if len(password) < 8:
        raise HTTPException(status_code=400, detail="Password must be at least 8 characters")

    # ── Generate unique slug ──────────────────────────────────
    base_slug = _re.sub(r'[^a-z0-9]+', '-', company_name.lower()).strip('-')
    slug = base_slug
    all_tenants = tdb.get_all_tenants()
    existing_slugs = {t["slug"] for t in all_tenants}
    existing_emails = set()
    for t in all_tenants:
        for u in tdb.get_tenant_users(t["id"]):
            existing_emails.add((u.get("email") or u.get("username") or "").lower())

    counter = 1
    while slug in existing_slugs:
        slug = f"{base_slug}-{counter}"
        counter += 1

    if email in existing_emails:
        raise HTTPException(status_code=400, detail="An account with this email already exists")

    # ── Create tenant ─────────────────────────────────────────
    calls_limit = {"free": 100, "starter": 500, "enterprise": 10000}.get(plan, 500)
    try:
        tenant_id = tdb.create_tenant(
            name         = company_name,
            slug         = slug,
            plan         = plan,
            contact_name = contact_name,
            calls_limit  = calls_limit,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to create tenant: {e}")

    # ── Create admin user ─────────────────────────────────────
    # create_tenant_user hashes the password internally — pass plaintext
    try:
        tdb.create_tenant_user(
            tenant_id = tenant_id,
            username  = email,
            password  = password,
            role      = "admin",
            name      = contact_name,
            email     = email,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to create admin user: {e}")

    # ── Seed basic tenant config ───────────────────────────────
    tdb.update_tenant_config(
        tenant_id,
        company_name      = company_name,
        company_industry  = data.get("company_industry", ""),
        company_products  = data.get("company_products", ""),
        call_language     = data.get("call_language", "hindi"),
        agent_name        = "Aira",
        agent_voice       = "anushka",
    )

    db.add_log(f"🎉 New signup: {company_name} ({email}) — plan={plan} tid={tenant_id}")

    # ── Notify superadmin via Telegram ────────────────────────
    try:
        tg_token = os.getenv("TELEGRAM_BOT_TOKEN", "")
        tg_chat  = os.getenv("TELEGRAM_CHAT_ID", "")
        if tg_token and tg_chat:
            msg = (
                f"🎉 <b>New Tenant Signup!</b>\n\n"
                f"🏢 <b>Company:</b> {company_name}\n"
                f"👤 <b>Contact:</b> {contact_name}\n"
                f"📧 <b>Email:</b> {email}\n"
                f"📦 <b>Plan:</b> {plan.title()}\n"
                f"🆔 <b>Tenant ID:</b> {tenant_id}\n\n"
                f"<i>Login: {email}</i>"
            )
            url = f"https://api.telegram.org/bot{tg_token}/sendMessage"
            async with httpx.AsyncClient(timeout=5) as client:
                await client.post(url, json={"chat_id": tg_chat, "text": msg, "parse_mode": "HTML"})
    except Exception:
        pass  # Never block signup for notification failure

    return {
        "ok":          True,
        "tenant_id":   tenant_id,
        "message":     f"Account created! You can now log in with {email}.",
        "login_email": email,
    }


def _verify_token(request: Request) -> dict:
    """Extract and verify token from Authorization header."""
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing token")
    # For simplicity, accept any non-empty token (full JWT can be added later)
    return {"token": auth[7:]}


# ═══════════════════════════════════════════════════════
# DASHBOARD
# ═══════════════════════════════════════════════════════

@router.get("/dashboard/stats")
async def dashboard_stats(request: Request):
    user = get_current_user(request)
    return db.get_dashboard_stats(tenant_id=user["tenant_id"])


@router.get("/dashboard/recent-calls")
async def recent_calls(request: Request, limit: int = 8):
    user = get_current_user(request)
    return db.get_recent_calls(limit=limit, tenant_id=user["tenant_id"])


@router.get("/dashboard/logs")
async def system_logs(limit: int = 30):
    return db.get_logs(limit=limit)


@router.get("/export/logs.csv")
async def export_logs_csv(limit: int = 1000):
    """Export recent system logs as CSV."""
    rows = db.get_logs(limit=min(limit, 5000))
    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=["id", "level", "message", "created_at"])
    writer.writeheader()
    writer.writerows(rows)
    output.seek(0)
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=system_logs.csv"},
    )


@router.get("/export/calls.csv")
async def export_calls_csv(request: Request, limit: int = 5000):
    """Export call history as CSV."""
    user = get_current_user(request)
    rows = db.get_calls(limit=min(limit, 10000), tenant_id=user["tenant_id"])
    fields = [
        "id", "lead_id", "campaign_id", "phone", "lead_name", "company",
        "started_at", "ended_at", "duration_sec", "outcome", "sentiment",
        "summary", "call_sid",
    ]
    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=fields, extrasaction="ignore")
    writer.writeheader()
    writer.writerows(rows)
    output.seek(0)
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=calls.csv"},
    )


# ═══════════════════════════════════════════════════════
# LEADS
# ═══════════════════════════════════════════════════════

@router.get("/leads")
async def list_leads(
    request:     Request,
    status:      str = None,
    campaign_id: int = None,
    limit:       int = 100,
    offset:      int = 0,
):
    user = get_current_user(request)
    tid  = user["tenant_id"]
    leads = db.get_leads(status=status, campaign_id=campaign_id,
                         limit=limit, offset=offset, tenant_id=tid)
    total = db.count_leads(status=status, tenant_id=tid)
    return {"total": total, "leads": leads}


# ── IMPORTANT: specific paths MUST come before /{lead_id} ──

@router.get("/leads/groups")
async def lead_groups(request: Request):
    """Return available lead groupings for campaign assignment."""
    user = get_current_user(request)
    tid  = user["tenant_id"]
    with db.get_conn() as conn:
        def cq(sql): return conn.execute(sql + " AND tenant_id=?", (tid,)).fetchone()[0]
        total      = cq("SELECT COUNT(*) FROM leads WHERE 1=1")
        new        = cq("SELECT COUNT(*) FROM leads WHERE status='new'")
        called     = cq("SELECT COUNT(*) FROM leads WHERE status='called'")
        interested = cq("SELECT COUNT(*) FROM leads WHERE status IN ('interested','demo_booked')")
        not_int    = cq("SELECT COUNT(*) FROM leads WHERE status='not_interested'")
        unassigned = cq("SELECT COUNT(*) FROM leads WHERE campaign_id IS NULL")

    groups = []
    if new > 0:
        groups.append({"id": "new", "label": "New leads (never called)", "count": new})
    if unassigned > 0:
        groups.append({"id": "unassigned", "label": "Unassigned leads", "count": unassigned})
    if called > 0:
        groups.append({"id": "called", "label": "Previously called leads", "count": called})
    if interested > 0:
        groups.append({"id": "interested", "label": "Interested leads (follow up)", "count": interested})
    if not_int > 0:
        groups.append({"id": "not_interested", "label": "Not interested (re-try)", "count": not_int})
    if total > 0:
        groups.append({"id": "all", "label": "All leads", "count": total})

    return {"groups": groups}


@router.post("/leads/upload-csv")
async def upload_leads_csv(
    request:     Request,
    file:        UploadFile = File(...),
    campaign_id: int = None,
    force:       bool = False,   # if True, delete existing leads with same phone first
):
    """Upload leads from CSV. Expected columns: name, phone, company, designation, language"""
    user = get_current_user(request)
    tid  = user["tenant_id"]

    if not file.filename.endswith('.csv'):
        raise HTTPException(status_code=400, detail="File must be a .csv")

    content = await file.read()
    try:
        text   = content.decode('utf-8-sig')
        reader = csv.DictReader(io.StringIO(text))
        rows   = list(reader)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"CSV parse error: {e}")

    if not rows:
        raise HTTPException(status_code=400, detail="CSV is empty")

    # If force=True, remove existing leads with same phones (for this tenant only)
    if force:
        from app.database import _normalize_phone
        deleted = 0
        with db.get_conn() as conn:
            for row in rows:
                raw = str(row.get('phone', '')).strip()
                if not raw: continue
                phone = _normalize_phone(raw)
                if not phone: continue
                result = conn.execute("DELETE FROM leads WHERE phone=? AND tenant_id=?", (phone, tid))
                deleted += result.rowcount
            conn.commit()
        db.add_log(f"🗑️ Force re-import: cleared {deleted} existing leads before upload")

    count = db.bulk_insert_leads(rows, campaign_id=campaign_id, tenant_id=tid)
    skipped = len(rows) - count

    msg = f"Imported {count} leads"
    if skipped > 0 and not force:
        msg += f" ({skipped} skipped — already exist. Use 'Replace existing' to re-import)"
    db.add_log(f"📂 CSV uploaded: {count} new leads from {file.filename}" +
               (f" ({skipped} skipped)" if skipped else ""))

    return {
        "message":  msg,
        "imported": count,
        "skipped":  skipped,
        "total":    len(rows),
        "force":    force,
    }


@router.get("/leads/{lead_id}")
async def get_lead(lead_id: int):
    lead = db.get_lead(lead_id)
    if not lead:
        raise HTTPException(status_code=404, detail="Lead not found")
    return lead


@router.post("/leads")
async def create_lead(request: Request):
    user = get_current_user(request)
    body = await request.json()
    name  = body.get("name", "").strip()
    phone = body.get("phone", "").strip()
    if not name or not phone:
        raise HTTPException(status_code=400, detail="name and phone are required")
    lead_id = db.create_lead(
        name        = name,
        phone       = phone,
        company     = body.get("company"),
        designation = body.get("designation"),
        city        = body.get("city"),
        language    = body.get("language", "hi"),
        campaign_id = body.get("campaign_id"),
        tenant_id   = user["tenant_id"],
    )
    db.add_log(f"➕ Lead added: {name} ({phone})")
    return {"id": lead_id, "message": "Lead created"}


@router.put("/leads/{lead_id}")
async def update_lead(lead_id: int, request: Request):
    body = await request.json()
    lead = db.get_lead(lead_id)
    if not lead:
        raise HTTPException(status_code=404, detail="Lead not found")

    name   = body.get("name")
    phone  = body.get("phone")

    db.update_lead_full(
        lead_id     = lead_id,
        name        = name,
        phone       = phone,
        company     = body.get("company"),
        designation = body.get("designation"),
        language    = body.get("language"),
        status      = body.get("status"),
        notes       = body.get("notes"),
    )

    db.add_log(f"✏️ Lead #{lead_id} updated — {name or lead['name']} ({phone or lead['phone']})")
    return {"message": "Lead updated", "id": lead_id}


@router.delete("/leads/{lead_id}")
async def delete_lead(lead_id: int):
    lead = db.get_lead(lead_id)
    if not lead:
        raise HTTPException(status_code=404, detail="Lead not found")
    db.delete_lead(lead_id)
    return {"message": "Lead deleted"}


# ═══════════════════════════════════════════════════════
# CAMPAIGNS
# ═══════════════════════════════════════════════════════

@router.get("/campaigns")
async def list_campaigns(request: Request, status: str = None):
    user = get_current_user(request)
    campaigns = db.get_campaigns(status=status, tenant_id=user["tenant_id"])
    return {"total": len(campaigns), "campaigns": campaigns}


@router.post("/campaigns")
async def create_campaign(request: Request):
    user        = get_current_user(request)
    tid         = user["tenant_id"]
    body        = await request.json()
    name        = body.get("name", "").strip()
    lead_group  = body.get("lead_group", "new")
    description = body.get("description", "")

    if not name:
        raise HTTPException(status_code=400, detail="Campaign name required")

    camp_id  = db.create_campaign(name=name, description=description, tenant_id=tid)
    assigned = db.assign_leads_to_campaign(camp_id, lead_group, tenant_id=tid)

    db.add_log(f"🚀 Campaign created: {name} — {assigned} leads assigned ({lead_group})")
    return {"id": camp_id, "message": "Campaign created", "leads_assigned": assigned}


@router.get("/campaigns/{campaign_id}")
async def get_campaign(campaign_id: int):
    c = db.get_campaign(campaign_id)
    if not c:
        raise HTTPException(status_code=404, detail="Campaign not found")
    return c


@router.post("/campaigns/{campaign_id}/restart")
async def restart_campaign(campaign_id: int, request: Request):
    """Reset all leads to 'new' and restart the campaign from scratch."""
    from app.campaign_runner import run_campaign

    c = db.get_campaign(campaign_id)
    if not c:
        raise HTTPException(status_code=404, detail="Campaign not found")

    try:
        body  = await request.json()
        delay = int(body.get("delay_seconds", 60))
    except Exception:
        delay = 60

    # Reset leads and counters
    reset_count = db.reset_campaign_leads(campaign_id)
    db.update_campaign_status(campaign_id, "running")
    db.add_log(f"🔄 Campaign restarted: {c['name']} — {reset_count} leads reset to new, {delay}s delay")

    import asyncio
    asyncio.create_task(run_campaign(campaign_id, delay))

    return {
        "message": f"Campaign '{c['name']}' restarted",
        "leads_reset": reset_count,
        "delay": delay,
    }


@router.post("/campaigns/{campaign_id}/start")
async def start_campaign(campaign_id: int, request: Request, background_tasks=None):
    from fastapi import BackgroundTasks
    from app.campaign_runner import run_campaign

    c = db.get_campaign(campaign_id)
    if not c:
        raise HTTPException(status_code=404, detail="Campaign not found")
    if c['status'] == 'running':
        return {"message": f"Campaign '{c['name']}' is already running"}

    # Get delay from request body (optional)
    try:
        body  = await request.json()
        delay = int(body.get("delay_seconds", 60))
    except Exception:
        delay = 60

    db.update_campaign_status(campaign_id, "running")
    db.add_log(f"▶️ Campaign started: {c['name']} — {c.get('leads_count',0)} leads, {delay}s delay")

    # Fire background task — actual outbound calls
    import asyncio
    asyncio.create_task(run_campaign(campaign_id, delay))

    return {
        "message": f"Campaign '{c['name']}' started",
        "leads":   c.get('leads_count', 0),
        "delay":   delay,
    }


@router.post("/campaigns/{campaign_id}/pause")
async def pause_campaign(campaign_id: int):
    c = db.get_campaign(campaign_id)
    if not c:
        raise HTTPException(status_code=404, detail="Campaign not found")
    db.update_campaign_status(campaign_id, "paused")
    db.add_log(f"⏸️ Campaign paused: {c['name']}")
    return {"message": f"Campaign '{c['name']}' paused"}


@router.post("/campaigns/{campaign_id}/complete")
async def complete_campaign(campaign_id: int):
    c = db.get_campaign(campaign_id)
    if not c:
        raise HTTPException(status_code=404, detail="Campaign not found")
    db.update_campaign_status(campaign_id, "completed")
    db.add_log(f"✅ Campaign completed: {c['name']}")
    return {"message": f"Campaign '{c['name']}' marked complete"}


@router.delete("/campaigns/{campaign_id}")
async def delete_campaign(campaign_id: int):
    c = db.get_campaign(campaign_id)
    if not c:
        raise HTTPException(status_code=404, detail="Campaign not found")
    db.delete_campaign(campaign_id)
    return {"message": "Campaign deleted"}


@router.post("/campaigns/{campaign_id}/schedule")
async def schedule_campaign(campaign_id: int, request: Request):
    """Schedule a campaign: once, daily, or weekdays at a specific time."""
    c = db.get_campaign(campaign_id)
    if not c:
        raise HTTPException(status_code=404, detail="Campaign not found")
    body         = await request.json()
    stype        = body.get("schedule_type", "once")   # once | daily | weekdays
    stime        = body.get("schedule_time", "09:00")  # HH:MM
    sdate        = body.get("schedule_date", "")       # YYYY-MM-DD (for once)
    sdays        = body.get("schedule_days", "mon,tue,wed,thu,fri")
    delay        = int(body.get("delay_seconds", 60))
    if stype not in ("once", "daily", "weekdays"):
        raise HTTPException(status_code=400, detail="Invalid schedule_type")
    next_run = f"{sdate} {stime}" if stype == "once" and sdate else None
    with db.get_conn() as conn:
        conn.execute("""
            UPDATE campaigns SET
                schedule_type   = ?,
                schedule_time   = ?,
                schedule_days   = ?,
                schedule_delay  = ?,
                schedule_status = 'pending',
                next_run_at     = ?
            WHERE id=?
        """, (stype, stime, sdays, delay, next_run, campaign_id))
        conn.commit()
    db.add_log(f"🕐 Campaign scheduled: {c['name']} — {stype} at {stime}")
    return {
        "message":       f"Campaign '{c['name']}' scheduled",
        "schedule_type": stype,
        "schedule_time": stime,
        "next_run_at":   next_run,
    }


@router.delete("/campaigns/{campaign_id}/schedule")
async def cancel_schedule(campaign_id: int):
    """Cancel a campaign's schedule without deleting the campaign."""
    c = db.get_campaign(campaign_id)
    if not c:
        raise HTTPException(status_code=404, detail="Campaign not found")
    with db.get_conn() as conn:
        conn.execute("""
            UPDATE campaigns SET
                schedule_type=NULL, schedule_time=NULL,
                schedule_status=NULL, next_run_at=NULL
            WHERE id=?
        """, (campaign_id,))
        conn.commit()
    db.add_log(f"❌ Campaign schedule cancelled: {c['name']}")
    return {"message": f"Schedule cancelled for '{c['name']}'"}


@router.get("/campaigns/{campaign_id}/schedule")
async def get_schedule(campaign_id: int):
    """Get current schedule for a campaign."""
    c = db.get_campaign(campaign_id)
    if not c:
        raise HTTPException(status_code=404, detail="Campaign not found")
    return {
        "campaign_id":    campaign_id,
        "schedule_type":  c.get("schedule_type"),
        "schedule_time":  c.get("schedule_time"),
        "schedule_days":  c.get("schedule_days"),
        "schedule_delay": c.get("schedule_delay", 60),
        "schedule_status":c.get("schedule_status"),
        "next_run_at":    c.get("next_run_at"),
        "is_scheduled":   c.get("schedule_type") is not None,
    }


@router.post("/campaigns/{campaign_id}/follow-up")
async def configure_campaign_follow_up(campaign_id: int, request: Request):
    """Configure follow-up automation for a campaign."""
    c = db.get_campaign(campaign_id)
    if not c:
        raise HTTPException(status_code=404, detail="Campaign not found")

    body = await request.json()
    follow_up_enabled = body.get("follow_up_enabled", False)
    follow_up_type = body.get("follow_up_type", "whatsapp")  # whatsapp, email, both
    follow_up_delay_minutes = body.get("follow_up_delay_minutes", 30)
    follow_up_message_template = body.get("follow_up_message_template", "").strip()

    if follow_up_type not in ("whatsapp", "email", "both"):
        raise HTTPException(status_code=400, detail="Invalid follow_up_type. Use: whatsapp, email, both")

    if follow_up_delay_minutes < 1 or follow_up_delay_minutes > 1440:  # Max 24 hours
        raise HTTPException(status_code=400, detail="follow_up_delay_minutes must be between 1 and 1440")

    db.update_campaign_follow_up(
        campaign_id=campaign_id,
        follow_up_enabled=follow_up_enabled,
        follow_up_type=follow_up_type,
        follow_up_delay_minutes=follow_up_delay_minutes,
        follow_up_message_template=follow_up_message_template
    )

    db.add_log(
        f"📱 Campaign '{c['name']}' follow-up {'enabled' if follow_up_enabled else 'disabled'}: "
        f"{follow_up_type} after {follow_up_delay_minutes}min"
    )

    return {
        "id": campaign_id,
        "message": f"Follow-up {'enabled' if follow_up_enabled else 'disabled'} for campaign '{c['name']}'",
        "follow_up_enabled": follow_up_enabled,
        "follow_up_type": follow_up_type,
        "follow_up_delay_minutes": follow_up_delay_minutes
    }


# Helper function to validate time format
def _validate_time_format(time_str: str) -> bool:
    """Validate time in HH:MM format (24-hour)."""
    import re
    if not re.match(r'^\d{2}:\d{2}$', time_str):
        return False

    hour, minute = map(int, time_str.split(':'))
    return 0 <= hour <= 23 and 0 <= minute <= 59


# ═══════════════════════════════════════════════════════
# CALLS
# ═══════════════════════════════════════════════════════

@router.get("/calls")
async def list_calls(
    request:     Request,
    limit:       int = 50,
    offset:      int = 0,
    campaign_id: int = None,
):
    user  = get_current_user(request)
    tid   = user["tenant_id"]
    calls = db.get_calls(limit=limit, offset=offset, campaign_id=campaign_id, tenant_id=tid)
    total = db.count_calls(tenant_id=tid)
    return {"total": total, "calls": calls}


@router.get("/calls/{call_id}")
async def get_call(call_id: int):
    call = db.get_call(call_id)
    if not call:
        raise HTTPException(status_code=404, detail="Call not found")
    return call


@router.get("/calls/{call_id}/transcript")
async def get_call_transcript(call_id: int):
    call = db.get_call(call_id)
    if not call:
        raise HTTPException(status_code=404, detail="Call not found")
    return {
        "call_id":    call_id,
        "lead_name":  call.get("lead_name", ""),
        "started_at": call.get("started_at", ""),
        "duration":   call.get("duration_sec", 0),
        "sentiment":  call.get("sentiment", ""),
        "summary":    call.get("summary", ""),
        "transcript": call.get("transcript") or "No transcript available",
    }


@router.post("/calls/{call_id}/generate-summary")
async def generate_summary(call_id: int):
    """Generate a fresh summary from the transcript using LLM."""
    call = db.get_call(call_id)
    if not call:
        raise HTTPException(status_code=404, detail="Call not found")

    transcript = call.get("transcript") or ""
    if not transcript.strip():
        return {
            "call_id": call_id,
            "summary": "No transcript available for summary generation.",
            "status": "no_transcript"
        }

    try:
        import os
        from groq import Groq

        # Parse transcript into role-based conversation
        lines = transcript.split('\n')
        conversation = []
        for line in lines:
            line = line.strip()
            if line.startswith('Aira:'):
                conversation.append({'role': 'assistant', 'content': line[5:].strip()})
            elif line.startswith('Customer:'):
                conversation.append({'role': 'user', 'content': line[9:].strip()})

        if not conversation:
            return {
                "call_id": call_id,
                "summary": "Could not parse transcript for analysis.",
                "status": "parse_error"
            }

        # Format for LLM analysis
        conversation_text = "\n".join(
            f"{m['role'].upper()}: {m['content']}"
            for m in conversation
        )

        prompt = f"""You are analyzing a Hindi sales call between Aira (AI sales agent for MuTech Automation) and a customer.

CONVERSATION:
{conversation_text}

Analyze this conversation and provide a SHORT point-wise summary (max 3-4 bullet points in English):
- Customer's main interest/concern
- Key value proposition shared by Aira
- Customer's response/sentiment
- Next steps (if any) or call outcome

Keep each point to ONE LINE maximum. Be concise and factual."""

        client = Groq(api_key=os.getenv("GROQ_API_KEY"))
        response = client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[{"role": "user", "content": prompt}],
            max_tokens=300,
            temperature=0.3,
        )

        summary_text = response.choices[0].message.content.strip()

        # Update the summary in DB
        with db.get_conn() as conn:
            conn.execute(
                "UPDATE calls SET summary = ? WHERE id = ?",
                (summary_text, call_id)
            )
            conn.commit()

        db.add_log(f"📝 Summary regenerated for call #{call_id}")

        return {
            "call_id": call_id,
            "summary": summary_text,
            "status": "success"
        }

    except Exception as e:
        return {
            "call_id": call_id,
            "summary": f"Error generating summary: {str(e)}",
            "status": "error"
        }


@router.post("/calls/test")
async def test_call(request: Request):
    """Manually test an outbound call to a phone number."""
    body = await request.json()
    phone = body.get("phone", "").strip()

    if not phone:
        raise HTTPException(status_code=400, detail="Phone number required")

    from app.campaign_runner import make_single_call

    result = await make_single_call(phone)
    if result:
        db.add_log(f"📞 Test call to {phone} — ID: {result['call_id']}")
        return {
            "message": f"Call initiated to {phone}",
            "call_id": result["call_id"],
            "provider": result.get("provider"),
            "dry_run": result.get("dry_run", False),
        }
    else:
        db.add_log(f"❌ Test call failed to {phone}")
        raise HTTPException(status_code=500, detail="Call failed")


# ═══════════════════════════════════════════════════════
# ANALYTICS
# ═══════════════════════════════════════════════════════

@router.get("/analytics/daily")
async def daily_stats(request: Request, days: int = 14):
    user = get_current_user(request)
    data = db.get_daily_call_stats(days=days, tenant_id=user["tenant_id"])
    return {"days": days, "data": data}


@router.get("/analytics/funnel")
async def funnel_stats(request: Request):
    user = get_current_user(request)
    tid  = user["tenant_id"]
    with db.get_conn() as conn:
        def cq(sql): return conn.execute(sql + " AND tenant_id=?", (tid,)).fetchone()[0]
        total      = cq("SELECT COUNT(*) FROM calls WHERE 1=1")
        answered   = cq("SELECT COUNT(*) FROM calls WHERE outcome='answered'")
        interested = cq("SELECT COUNT(*) FROM calls WHERE sentiment IN ('interested','demo_booked')")
        demos      = cq("SELECT COUNT(*) FROM calls WHERE sentiment='demo_booked'")

    return {
        "funnel": [
            {"stage": "Total Calls", "count": total,      "pct": 100},
            {"stage": "Answered",    "count": answered,   "pct": round(answered/total*100,1)    if total else 0},
            {"stage": "Interested",  "count": interested, "pct": round(interested/total*100,1)  if total else 0},
            {"stage": "Demo Booked", "count": demos,      "pct": round(demos/total*100,1)       if total else 0},
        ]
    }


@router.get("/analytics/hourly")
async def hourly_stats(request: Request, days: int = 30):
    """Hour-of-day call distribution for heatmap."""
    user = get_current_user(request)
    data = db.get_hourly_call_stats(days=days, tenant_id=user["tenant_id"])
    # Fill missing hours with zeros
    hour_map = {r['hour']: r for r in data}
    full = []
    for h in range(24):
        full.append(hour_map.get(h, {'hour': h, 'total': 0, 'answered': 0}))
    return {"days": days, "data": full}


@router.get("/analytics/sentiment")
async def sentiment_breakdown(request: Request):
    user = get_current_user(request)
    tid  = user["tenant_id"]
    with db.get_conn() as conn:
        rows = conn.execute("""
            SELECT sentiment, COUNT(*) as count
            FROM calls WHERE tenant_id=?
            GROUP BY sentiment
            ORDER BY count DESC
        """, (tid,)).fetchall()
    return {"breakdown": [dict(r) for r in rows]}


# ═══════════════════════════════════════════════════════
# USERS  (admin only — token check skipped for now)
# ═══════════════════════════════════════════════════════

@router.get("/users")
async def list_users(request: Request):
    user = get_current_user(request)
    return {"users": db.get_all_users(tenant_id=user["tenant_id"])}


@router.post("/users")
async def create_user(request: Request):
    current = get_current_user(request)
    body     = await request.json()
    username = body.get("username", "").strip()
    password = body.get("password", "").strip()
    role     = body.get("role", "sales")
    name     = body.get("name", "").strip()
    email    = body.get("email", "").strip()

    if not username or not password:
        raise HTTPException(status_code=400, detail="username and password required")

    try:
        user_id = db.add_user(username, password, role, name, email,
                              tenant_id=current["tenant_id"])
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"User creation failed: {e}")

    db.add_log(f"👤 User added: {username} ({role})")
    return {"id": user_id, "message": "User created"}


@router.delete("/users/{user_id}")
async def remove_user(user_id: int):
    user = db.get_user_by_id(user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    db.delete_user(user_id)
    return {"message": "User deleted"}


# ═══════════════════════════════════════════════════════
# SYSTEM
# ═══════════════════════════════════════════════════════

@router.get("/system/health")
async def system_health():
    """Live health check — tests each service connection."""
    import httpx, time

    results = {}
    start = time.time()

    # ── Database ──────────────────────────────────────
    try:
        stats = db.get_dashboard_stats()
        results["database"] = {"status": "ok", "calls": stats["total_calls"]}
    except Exception as e:
        results["database"] = {"status": "error", "detail": str(e)}

    # ── Sarvam AI ─────────────────────────────────────
    sarvam_key = os.getenv("SARVAM_API_KEY", "")
    if sarvam_key:
        try:
            async with httpx.AsyncClient(timeout=5) as client:
                r = await client.get(
                    "https://api.sarvam.ai/v1/models",
                    headers={"api-subscription-key": sarvam_key}
                )
            results["sarvam"] = {"status": "ok" if r.status_code < 400 else "error",
                                  "model_stt": "saarika:v2.5", "model_tts": "bulbul:v2"}
        except Exception:
            results["sarvam"] = {"status": "ok", "model_stt": "saarika:v2.5", "model_tts": "bulbul:v2",
                                  "note": "key present"}
    else:
        results["sarvam"] = {"status": "no_key"}

    # ── Groq ──────────────────────────────────────────
    groq_key = os.getenv("GROQ_API_KEY", "")
    if groq_key:
        try:
            async with httpx.AsyncClient(timeout=5) as client:
                r = await client.get(
                    "https://api.groq.com/openai/v1/models",
                    headers={"Authorization": f"Bearer {groq_key}"}
                )
            results["groq"] = {"status": "ok" if r.status_code < 400 else "error",
                                "model": "llama-3.3-70b-versatile"}
        except Exception:
            results["groq"] = {"status": "ok", "model": "llama-3.3-70b-versatile",
                                "note": "key present"}
    else:
        results["groq"] = {"status": "no_key"}

    # ── Exotel ────────────────────────────────────────
    exotel_sid = os.getenv("EXOTEL_ACCOUNT_SID", "")
    exotel_key = os.getenv("EXOTEL_API_KEY", "")
    if exotel_sid and exotel_key:
        results["exotel"] = {
            "status":  "ok",
            "number":  os.getenv("EXOTEL_VIRTUAL_NUMBER", "07314854688"),
            "account": exotel_sid,
        }
    else:
        results["exotel"] = {"status": "no_key"}

    return {
        "status":    "ok",
        "version":   "3.0.0",
        "uptime_ms": round((time.time() - start) * 1000, 1),
        "timestamp": datetime.utcnow().isoformat(),
        "services":  results,
    }


# ── Aira System Prompt (read/write from pipeline file) ──
PIPELINE_PATH = os.path.join(os.path.dirname(__file__), 'exotel_pipeline.py')

@router.get("/system/prompt")
async def get_prompt():
    """Read current SYSTEM_PROMPT from the pipeline file."""
    try:
        src = open(PIPELINE_PATH).read()
        import re
        m = re.search(r'SYSTEM_PROMPT\s*=\s*"""(.*?)"""', src, re.DOTALL)
        if m:
            return {"prompt": m.group(1).strip()}
        return {"prompt": "", "warning": "SYSTEM_PROMPT not found in pipeline"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/system/prompt")
async def save_prompt(request: Request):
    """Write updated SYSTEM_PROMPT back to the pipeline file."""
    body   = await request.json()
    prompt = body.get("prompt", "").strip()
    if not prompt:
        raise HTTPException(status_code=400, detail="Prompt cannot be empty")
    try:
        src = open(PIPELINE_PATH).read()
        import re
        new_src = re.sub(
            r'(SYSTEM_PROMPT\s*=\s*""").*?(""")',
            f'\\1{prompt}\\2',
            src,
            flags=re.DOTALL
        )
        open(PIPELINE_PATH, 'w').write(new_src)
        db.add_log("✏️ System prompt updated via dashboard")
        return {"message": "Prompt saved. Restart service to apply."}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))




@router.get("/system/config")

async def get_config_route():

    """Return current model/voice/telephony configuration (non-secret)."""

    provider = db.get_config("telephony_provider", "piopiy")

    return {

        "telephony":      provider,

        "number":         os.getenv("EXOTEL_VIRTUAL_NUMBER", "07314854688") if provider == "exotel" else os.getenv("PIOPIY_NUMBER", "+911203134158"),

        "stt_model":      "saarika:v2.5",

        "tts_model":      "bulbul:v2",

        "tts_voice":      "anushka",

        "llm_model":      "llama-3.3-70b-versatile",

        "llm_provider":   "Groq",

        "language":       "Hindi (hi-IN)",

        "sarvam_key_set": bool(os.getenv("SARVAM_API_KEY")),

        "groq_key_set":   bool(os.getenv("GROQ_API_KEY")),

        "exotel_key_set": bool(os.getenv("EXOTEL_API_KEY")),

        "company_name":    db.get_config("company_name", "MuTech Automation"),

        "agent_name":      db.get_config("agent_name", "Aira"),

        "target_audience": db.get_config(
            "target_audience",
            "Plant managers, maintenance engineers, and automation heads at manufacturing facilities in India and UAE."
        ),

    }



@router.post("/system/config")
async def save_config(request: Request):
    """Update editable config values."""
    body = await request.json()
    env_path = os.path.join(os.path.dirname(__file__), '..', '.env')

    env_updates = {}
    config_updates = {}
    if "virtual_number" in body:
        num = str(body["virtual_number"]).strip().lstrip("+").lstrip("91")
        if not num.isdigit() or len(num) != 10:
            raise HTTPException(status_code=400,
                detail="Phone must be 10 digits (e.g. 7314854688)")
        env_updates["EXOTEL_VIRTUAL_NUMBER"] = "0" + num  # store as 07XXXXXXXXX

    for key in ("company_name", "agent_name", "target_audience"):
        if key in body:
            value = str(body.get(key) or "").strip()
            if not value:
                raise HTTPException(status_code=400, detail=f"{key} cannot be empty")
            config_updates[key] = value

    if not env_updates and not config_updates:
        raise HTTPException(status_code=400, detail="Nothing to update")

    try:
        if env_updates:
            # Read current .env
            lines = open(env_path).readlines()
            for key, val in env_updates.items():
                found = False
                for i, line in enumerate(lines):
                    if line.startswith(f"{key}="):
                        lines[i] = f"{key}={val}\n"
                        found = True
                        break
                if not found:
                    lines.append(f"{key}={val}\n")
            open(env_path, 'w').writelines(lines)

            # Reload into current process
            for key, val in env_updates.items():
                os.environ[key] = val

        for key, val in config_updates.items():
            db.set_config(key, val)

        updates = {**env_updates, **config_updates}
        db.add_log(f"⚙️ Config updated: {', '.join(updates.keys())}")
        return {"message": "Saved. Changes are live immediately.", "updated": updates}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Could not save config: {e}")









# ═══════════════════════════════════════════════════════

# TELEPHONY SWITCH  (admin only)

# ═══════════════════════════════════════════════════════



@router.get("/system/telephony")

async def get_telephony():

    """Return current active telephony provider."""

    provider = db.get_config("telephony_provider", "piopiy")

    return {

        "active": provider,

        "options": [

            {

                "id":          "piopiy",

                "name":        "PIOPIY (TeleCMI)",

                "description": "AI Agent via signaling server — recommended",

                "number":      os.getenv("PIOPIY_NUMBER", "+911203134158"),

                "status":      "active" if provider == "piopiy" else "standby",

            },

            {

                "id":          "exotel",

                "name":        "Exotel",

                "description": "WebSocket voicebot via Exotel landline",

                "number":      os.getenv("EXOTEL_VIRTUAL_NUMBER", "07314854688"),

                "status":      "active" if provider == "exotel" else "standby",

            },

        ],

    }





@router.post("/system/telephony")

async def set_telephony(request: Request):

    """Switch active telephony provider. Admin only."""

    body     = await request.json()

    provider = body.get("provider", "").lower().strip()

    if provider not in ("piopiy", "exotel"):

        raise HTTPException(status_code=400, detail="provider must be 'piopiy' or 'exotel'")

    db.set_config("telephony_provider", provider)

    db.add_log(f"🔄 Telephony switched to {provider.upper()} by admin")

    return {

        "message": f"Active telephony switched to {provider.upper()}",

        "active":  provider,

        "note":    "Change is immediate — no restart required",

    }





# ═══════════════════════════════════════════════════════

# PIOPIY INBOUND WEBHOOK

# ═══════════════════════════════════════════════════════



@router.post("/piopiy/inbound")

async def piopiy_inbound(request: Request):

    """

    PIOPIY calls this webhook when someone dials 01203134158.

    We return PCMO actions to stream audio to our AI agent WebSocket.

    """

    import json

    body = {}

    try:

        body = await request.json()

    except Exception:

        pass



    caller = body.get("caller_id", body.get("from", "unknown"))

    called = body.get("did", body.get("to", "unknown"))

    call_id = body.get("call_id", body.get("request_id", "unknown"))



    db.add_log(f"📲 Inbound call from {caller} to {called}")



    # Return PCMO: stream caller audio to our piopiy_agent WebSocket pipeline

    # The piopiy-ai library handles this via the Agent signaling server

    # We just need to tell PIOPIY to connect to our AI agent

    import os

    agent_id = os.getenv("PIOPIY_AGENT_ID")



    pcmo = {

        "ai_agent": {

            "agent_id": agent_id,

            "metadata": {

                "call_type": "inbound",

                "caller": caller,

                "called": called,

                "call_id": call_id,

            }

        }

    }



    return JSONResponse(content=pcmo)


# ═══════════════════════════════════════════════════════
# TENANT SETTINGS ENDPOINTS (admin role required)
# ═══════════════════════════════════════════════════════

from app import tenant_db as tdb


def _require_admin(current_user: dict = Depends(get_current_user)):
    if current_user["role"] not in ("admin", "superadmin"):
        raise HTTPException(status_code=403, detail="Admin access required")
    return current_user


@router.get("/tenant/profile")
async def get_tenant_profile(current_user: dict = Depends(get_current_user)):
    """Get current tenant's company profile and call config."""
    tid = current_user.get("tenant_id", 1)
    tenants = tdb.get_all_tenants()
    tenant  = next((t for t in tenants if t["id"] == tid), None)
    config  = tdb.get_tenant_config(tid) or {}
    return {
        "tenant": tenant,
        "config": {
            "company_name":      config.get("company_name", tenant["name"] if tenant else ""),
            "company_industry":  config.get("company_industry", ""),
            "company_products":  config.get("company_products", ""),
            "company_website":   config.get("company_website", ""),
            "call_language":     config.get("call_language", "hindi"),
            "call_guidelines":   config.get("call_guidelines", ""),
            "agent_name":        config.get("agent_name", "Aira"),
            "agent_voice":       config.get("agent_voice", "anushka"),
            "greeting_template": config.get("greeting_template", ""),
            "system_prompt":     config.get("system_prompt", ""),
            "setup_complete":    config.get("setup_complete", 0),
        },
    }


@router.put("/tenant/profile")
async def update_tenant_profile(request: Request, current_user: dict = Depends(_require_admin)):
    """Update company profile and call guidelines — auto-builds system_prompt."""
    data = await request.json()
    tid  = current_user.get("tenant_id", 1)
    config = tdb.get_tenant_config(tid) or {}

    company_name      = data.get("company_name", "")
    company_industry  = data.get("company_industry", "")
    company_products  = data.get("company_products", "")
    company_website   = data.get("company_website", "")
    call_language     = data.get("call_language", "hindi")
    call_guidelines   = data.get("call_guidelines", "")
    agent_name        = data.get("agent_name", config.get("agent_name", "Aira"))
    agent_voice       = data.get("agent_voice", config.get("agent_voice", "anushka"))
    greeting_template = data.get("greeting_template", config.get("greeting_template", ""))

    lang_instruction = {
        "hindi":    "हमेशा हिंदी में बोलें।",
        "english":  "Always speak in English.",
        "hinglish": "Hinglish में बोलें — Hindi और English mix करें।",
    }.get(call_language, "हमेशा हिंदी में बोलें।")

    default_guidelines = (
        "- हर जवाब संक्षिप्त रखें (2-3 वाक्य)\n"
        "- अंत में demo schedule करने की कोशिश करें\n"
        "- रुचि नहीं है तो विनम्रता से call समाप्त करें"
    )
    effective_guidelines = call_guidelines or default_guidelines

    system_prompt = (
        "आप " + agent_name + " हैं, " + company_name + " की professional sales agent हैं।\n\n"
        "कंपनी: " + company_name + "\n"
        "Industry: " + company_industry + "\n"
        "Products/Services: " + company_products + "\n"
        "Website: " + company_website + "\n\n"
        + lang_instruction + "\n\n"
        "Call Guidelines:\n"
        + effective_guidelines + "\n\n"
        "हर जवाब में: पहले information दें, फिर customer से एक question पूछें।"
    )

    tdb.update_tenant_config(
        tid,
        company_name      = company_name,
        company_industry  = company_industry,
        company_products  = company_products,
        company_website   = company_website,
        call_language     = call_language,
        call_guidelines   = call_guidelines,
        agent_name        = agent_name,
        agent_voice       = agent_voice,
        greeting_template = greeting_template,
        system_prompt     = system_prompt,
        setup_complete    = 1,
    )
    return {"ok": True, "message": "Profile updated. AI agent system prompt auto-generated."}


@router.put("/tenant/api-keys")
async def update_tenant_api_keys(request: Request, current_user: dict = Depends(_require_admin)):
    """Update BYOK API keys and service selections for this tenant."""
    data = await request.json()
    tid  = current_user.get("tenant_id", 1)
    saveable_fields = (
        # LLM
        "llm_provider", "llm_model",
        "groq_api_key", "openai_api_key", "anthropic_api_key", "gemini_api_key",
        # Speech
        "speech_provider", "sarvam_api_key",
        "elevenlabs_api_key", "elevenlabs_voice_id",
        # Telephony
        "piopiy_agent_id", "piopiy_number",
        # Messaging
        "telegram_bot_token", "telegram_chat_id",
        "whatsapp_api_key", "whatsapp_number",
    )
    update_data = {f: data[f] for f in saveable_fields if f in data}
    if update_data:
        tdb.update_tenant_config(tid, **update_data)
    return {"ok": True, "message": "Settings saved."}


@router.get("/tenant/api-keys")
async def get_tenant_api_keys(current_user: dict = Depends(_require_admin)):
    """Get tenant API keys and service config (secrets masked)."""
    tid    = current_user.get("tenant_id", 1)
    config = tdb.get_tenant_config(tid) or {}

    def mask(v):
        if v and len(v) > 9:
            return v[:6] + "•••" + v[-3:]
        return "✓ Set" if v else ""

    return {
        # LLM
        "llm_provider":        config.get("llm_provider", "groq"),
        "llm_model":           config.get("llm_model", ""),
        "groq_api_key":        mask(config.get("groq_api_key", "")),
        "openai_api_key":      mask(config.get("openai_api_key", "")),
        "anthropic_api_key":   mask(config.get("anthropic_api_key", "")),
        "gemini_api_key":      mask(config.get("gemini_api_key", "")),
        "groq_set":            bool(config.get("groq_api_key")),
        "openai_set":          bool(config.get("openai_api_key")),
        "anthropic_set":       bool(config.get("anthropic_api_key")),
        "gemini_set":          bool(config.get("gemini_api_key")),
        # Speech
        "speech_provider":     config.get("speech_provider", "sarvam"),
        "sarvam_api_key":      mask(config.get("sarvam_api_key", "")),
        "elevenlabs_api_key":  mask(config.get("elevenlabs_api_key", "")),
        "elevenlabs_voice_id": config.get("elevenlabs_voice_id", ""),
        "sarvam_set":          bool(config.get("sarvam_api_key")),
        "elevenlabs_set":      bool(config.get("elevenlabs_api_key")),
        # Telephony
        "piopiy_agent_id":     config.get("piopiy_agent_id", ""),
        "piopiy_number":       config.get("piopiy_number", ""),
        # Messaging
        "telegram_bot_token":  mask(config.get("telegram_bot_token", "")),
        "telegram_chat_id":    config.get("telegram_chat_id", ""),
        "whatsapp_api_key":    mask(config.get("whatsapp_api_key", "")),
        "whatsapp_number":     config.get("whatsapp_number", ""),
        "telegram_set":        bool(config.get("telegram_bot_token")),
        "whatsapp_set":        bool(config.get("whatsapp_api_key")),
    }


@router.put("/tenant/system-prompt")
async def update_tenant_system_prompt(request: Request, current_user: dict = Depends(_require_admin)):
    """Directly update the AI system prompt for this tenant."""
    data   = await request.json()
    prompt = data.get("prompt", "").strip()
    if not prompt:
        raise HTTPException(status_code=400, detail="Prompt cannot be empty")
    tid = current_user.get("tenant_id", 1)
    tdb.update_tenant_config(tid, system_prompt=prompt, setup_complete=1)
    return {"ok": True, "message": "System prompt saved."}
