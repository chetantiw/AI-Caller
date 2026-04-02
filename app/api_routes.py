"""

app/api_routes.py

All /api/* REST endpoints for the MuTech dashboard

Covers: auth, dashboard stats, leads, campaigns, calls, analytics, logs, users

"""


import io

import csv

import hashlib

import os

from datetime import datetime, timedelta


import httpx

from fastapi import APIRouter, HTTPException, Request, UploadFile, File

from fastapi.responses import JSONResponse


from app import database as db


router = APIRouter(prefix="/api")



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


    db.add_log(f"🔑 Login: {username} ({user['role']})")


    return {

        "token":    token,

        "user_id":  user["id"],

        "username": user["username"],

        "name":     user.get("name", username),

        "role":     user["role"],

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

async def dashboard_stats():

    return db.get_dashboard_stats()



@router.get("/dashboard/recent-calls")

async def recent_calls(limit: int = 8):

    return db.get_recent_calls(limit=limit)



@router.get("/dashboard/logs")

async def system_logs(limit: int = 30):

    return db.get_logs(limit=limit)



# ═══════════════════════════════════════════════════════

# LEADS

# ═══════════════════════════════════════════════════════


@router.get("/leads")

async def list_leads(

    status:      str = None,

    campaign_id: int = None,

    limit:       int = 100,

    offset:      int = 0,

):

    leads = db.get_leads(status=status, campaign_id=campaign_id,

                         limit=limit, offset=offset)

    total = db.count_leads(status=status)

    return {"total": total, "leads": leads}



# ── IMPORTANT: specific paths MUST come before /{lead_id} ──


@router.get("/leads/groups")

async def lead_groups():

    """Return available lead groupings for campaign assignment."""

    with db.get_conn() as conn:

        total      = conn.execute("SELECT COUNT(*) FROM leads").fetchone()[0]

        new        = conn.execute("SELECT COUNT(*) FROM leads WHERE status='new'").fetchone()[0]

        called     = conn.execute("SELECT COUNT(*) FROM leads WHERE status='called'").fetchone()[0]

        interested = conn.execute("SELECT COUNT(*) FROM leads WHERE status='interested'").fetchone()[0]

        unassigned = conn.execute(

            "SELECT COUNT(*) FROM leads WHERE campaign_id IS NULL"

        ).fetchone()[0]

    return {

        "groups": [

            {"id": "new",        "label": "New leads",         "count": new},

            {"id": "unassigned", "label": "Unassigned leads",  "count": unassigned},

            {"id": "called",     "label": "Previously called", "count": called},

            {"id": "interested", "label": "Interested leads",  "count": interested},

            {"id": "all",        "label": "All leads",         "count": total},

        ]

    }



@router.post("/leads/upload-csv")

async def upload_leads_csv(

    file:        UploadFile = File(...),

    campaign_id: int = None,

    force:       bool = False,   # if True, delete existing leads with same phone first

):

    """Upload leads from CSV. Expected columns: name, phone, company, designation, language"""

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


    # If force=True, remove existing leads with same phones so re-import works

    if force:

        from app.database import _normalize_phone

        deleted = 0

        with db.get_conn() as conn:

            for row in rows:

                raw = str(row.get('phone', '')).strip()

                if not raw: continue

                phone = _normalize_phone(raw)

                if not phone: continue

                result = conn.execute("DELETE FROM leads WHERE phone=?", (phone,))

                deleted += result.rowcount

            conn.commit()

        db.add_log(f"🗑️ Force re-import: cleared {deleted} existing leads before upload")


    count = db.bulk_insert_leads(rows, campaign_id=campaign_id)

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

async def list_campaigns(status: str = None):

    campaigns = db.get_campaigns(status=status)

    return {"total": len(campaigns), "campaigns": campaigns}



@router.post("/campaigns")

async def create_campaign(request: Request):

    body        = await request.json()

    name        = body.get("name", "").strip()

    lead_group  = body.get("lead_group", "new")

    description = body.get("description", "")


    if not name:

        raise HTTPException(status_code=400, detail="Campaign name required")


    camp_id  = db.create_campaign(name=name, description=description)

    assigned = db.assign_leads_to_campaign(camp_id, lead_group)


    db.add_log(f"🚀 Campaign created: {name} — {assigned} leads assigned ({lead_group})")

    return {"id": camp_id, "message": "Campaign created", "leads_assigned": assigned}



@router.get("/campaigns/{campaign_id}")

async def get_campaign(campaign_id: int):

    c = db.get_campaign(campaign_id)

    if not c:

        raise HTTPException(status_code=404, detail="Campaign not found")

    return c



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



# ═══════════════════════════════════════════════════════

# CALLS

# ═══════════════════════════════════════════════════════


@router.get("/calls")

async def list_calls(

    limit:       int = 50,

    offset:      int = 0,

    campaign_id: int = None,

):

    calls = db.get_calls(limit=limit, offset=offset, campaign_id=campaign_id)

    total = db.count_calls()

    return {"total": total, "calls": calls}



@router.get("/calls/{call_id}")

async def get_call(call_id: int):

    call = db.get_call(call_id)

    if not call:

        raise HTTPException(status_code=404, detail="Call not found")

    return call



# ═══════════════════════════════════════════════════════

# ANALYTICS

# ═══════════════════════════════════════════════════════


@router.get("/analytics/daily")

async def daily_stats(days: int = 14):

    data = db.get_daily_call_stats(days=days)

    return {"days": days, "data": data}



@router.get("/analytics/funnel")

async def funnel_stats():

    with db.get_conn() as conn:

        total     = conn.execute("SELECT COUNT(*) FROM calls").fetchone()[0]

        answered  = conn.execute(

            "SELECT COUNT(*) FROM calls WHERE outcome='answered'"

        ).fetchone()[0]

        interested = conn.execute(

            "SELECT COUNT(*) FROM calls WHERE sentiment IN ('interested','demo_booked')"

        ).fetchone()[0]

        demos     = conn.execute(

            "SELECT COUNT(*) FROM calls WHERE sentiment='demo_booked'"

        ).fetchone()[0]


    return {

        "funnel": [

            {"stage": "Total Calls",   "count": total,      "pct": 100},

            {"stage": "Answered",      "count": answered,   "pct": round(answered/total*100,1)    if total else 0},

            {"stage": "Interested",    "count": interested, "pct": round(interested/total*100,1)  if total else 0},

            {"stage": "Demo Booked",   "count": demos,      "pct": round(demos/total*100,1)       if total else 0},

        ]

    }



@router.get("/analytics/hourly")

async def hourly_stats(days: int = 30):

    """Hour-of-day call distribution for heatmap."""

    data = db.get_hourly_call_stats(days=days)

    # Fill missing hours with zeros

    hour_map = {r['hour']: r for r in data}

    full = []

    for h in range(24):

        full.append(hour_map.get(h, {'hour': h, 'total': 0, 'answered': 0}))

    return {"days": days, "data": full}



@router.get("/analytics/sentiment")

async def sentiment_breakdown():

    with db.get_conn() as conn:

        rows = conn.execute("""

            SELECT sentiment, COUNT(*) as count

            FROM calls

            GROUP BY sentiment

            ORDER BY count DESC

        """).fetchall()

    return {"breakdown": [dict(r) for r in rows]}



# ═══════════════════════════════════════════════════════

# USERS  (admin only — token check skipped for now)

# ═══════════════════════════════════════════════════════


@router.get("/users")

async def list_users():

    return {"users": db.get_all_users()}



@router.post("/users")

async def create_user(request: Request):

    body     = await request.json()

    username = body.get("username", "").strip()

    password = body.get("password", "").strip()

    role     = body.get("role", "sales")

    name     = body.get("name", "").strip()

    email    = body.get("email", "").strip()


    if not username or not password:

        raise HTTPException(status_code=400, detail="username and password required")


    try:

        user_id = db.add_user(username, password, role, name, email)

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

        "version":   "2.0.0",

        "uptime_ms": round((time.time() - start) * 1000, 1),

        "timestamp": datetime.utcnow().isoformat(),

        "services":  results,

    }



# ── Priya System Prompt (read/write from pipeline file) ──

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

async def get_config():

    """Return current model/voice configuration (non-secret)."""

    return {

        "telephony":  "Exotel",

        "number":     os.getenv("EXOTEL_VIRTUAL_NUMBER", "07314854688"),

        "stt_model":  "saarika:v2.5",

        "tts_model":  "bulbul:v2",

        "tts_voice":  "anushka",

        "llm_model":  "llama-3.3-70b-versatile",

        "llm_provider": "Groq",

        "language":   "Hindi (hi-IN)",

        "sarvam_key_set": bool(os.getenv("SARVAM_API_KEY")),

        "groq_key_set":   bool(os.getenv("GROQ_API_KEY")),

        "exotel_key_set": bool(os.getenv("EXOTEL_API_KEY")),

    }
