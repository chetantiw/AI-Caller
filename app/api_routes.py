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

    db.update_lead(
        lead_id = lead_id,
        status  = body.get("status"),
        notes   = body.get("notes"),
    )
    return {"message": "Lead updated"}


@router.delete("/leads/{lead_id}")
async def delete_lead(lead_id: int):
    lead = db.get_lead(lead_id)
    if not lead:
        raise HTTPException(status_code=404, detail="Lead not found")
    db.delete_lead(lead_id)
    return {"message": "Lead deleted"}


@router.post("/leads/upload-csv")
async def upload_leads_csv(
    file:        UploadFile = File(...),
    campaign_id: int = None,
):
    """
    Upload leads from CSV file.
    Expected columns: name, phone, company, designation, language
    """
    if not file.filename.endswith('.csv'):
        raise HTTPException(status_code=400, detail="File must be a .csv")

    content = await file.read()
    try:
        text   = content.decode('utf-8-sig')   # handle BOM
        reader = csv.DictReader(io.StringIO(text))
        rows   = list(reader)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"CSV parse error: {e}")

    if not rows:
        raise HTTPException(status_code=400, detail="CSV is empty")

    count = db.bulk_insert_leads(rows, campaign_id=campaign_id)
    db.add_log(f"📂 CSV uploaded: {count} new leads from {file.filename}")

    return {
        "message":  f"Successfully imported {count} leads",
        "imported": count,
        "skipped":  len(rows) - count,
        "total":    len(rows),
    }


# ═══════════════════════════════════════════════════════
# CAMPAIGNS
# ═══════════════════════════════════════════════════════

@router.get("/campaigns")
async def list_campaigns(status: str = None):
    campaigns = db.get_campaigns(status=status)
    return {"total": len(campaigns), "campaigns": campaigns}


@router.get("/campaigns/{campaign_id}")
async def get_campaign(campaign_id: int):
    c = db.get_campaign(campaign_id)
    if not c:
        raise HTTPException(status_code=404, detail="Campaign not found")
    return c


@router.post("/campaigns")
async def create_campaign(request: Request):
    body = await request.json()
    name = body.get("name", "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="Campaign name required")

    camp_id = db.create_campaign(
        name        = name,
        description = body.get("description"),
    )
    db.add_log(f"🚀 Campaign created: {name}")
    return {"id": camp_id, "message": "Campaign created"}


@router.post("/campaigns/{campaign_id}/start")
async def start_campaign(campaign_id: int):
    c = db.get_campaign(campaign_id)
    if not c:
        raise HTTPException(status_code=404, detail="Campaign not found")
    db.update_campaign_status(campaign_id, "running")
    db.add_log(f"▶️ Campaign started: {c['name']}")
    return {"message": f"Campaign '{c['name']}' started"}


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
    return {
        "status":   "ok",
        "version":  "2.0.0",
        "database": "connected",
        "agent":    "Priya",
        "telephony":"Exotel",
        "stt":      "Sarvam saarika:v2.5",
        "tts":      "Sarvam bulbul:v2 (anushka)",
        "llm":      "Groq llama-3.3-70b-versatile",
        "timestamp": datetime.utcnow().isoformat(),
    }
