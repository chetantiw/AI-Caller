"""
app/main.py
FastAPI server — MuTech AI Caller
Handles: Exotel WebSocket pipeline, REST API, dashboard static files

CHANGES vs original:
- Added database init on startup (init_db)
- Mounted /api/* routes from api_routes.py
- Kept all original routes intact
- Lead info now passed correctly from DB to pipeline
"""

import os
import csv
from datetime import datetime

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request, HTTPException
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from loguru import logger
from dotenv import load_dotenv

from app.exotel_pipeline import run_exotel_pipeline
from app.api_routes import router as api_router
from app import database as db

load_dotenv()

app = FastAPI(
    title="MuTech AI Caller — Priya",
    description="Exotel + Sarvam AI + Groq powered Hindi voice sales agent",
    version="2.0.0",
)

# ── Startup: initialize database ──────────────────────────────
@app.on_event("startup")
async def startup_event():
    db.init_db()
    db.add_log("🟢 MuTech AI Caller server started")
    logger.info("Database initialized")


# ── Mount API routes ───────────────────────────────────────────
app.include_router(api_router)


# ── Static files + Dashboard ───────────────────────────────────
_static_dir = os.path.join(os.path.dirname(__file__), "../static")
os.makedirs(_static_dir, exist_ok=True)

app.mount("/static", StaticFiles(directory=_static_dir), name="static")


@app.get("/")
async def root():
    from fastapi.responses import RedirectResponse
    return RedirectResponse(url="/dashboard")


@app.get("/dashboard")
async def dashboard():
    _path = os.path.join(_static_dir, "dashboard.html")
    if os.path.exists(_path):
        return FileResponse(_path)
    return JSONResponse(
        {"error": "dashboard.html not found in static/ folder"},
        status_code=404
    )


# ── Health check ───────────────────────────────────────────────
@app.get("/health")
async def health():
    stats = db.get_dashboard_stats()
    return JSONResponse({
        "status":      "ok",
        "version":     "2.0.0",
        "agent":       "Priya",
        "telephony":   "Exotel",
        "calls_today": stats.get("calls_today", 0),
        "total_calls": stats.get("total_calls", 0),
    })


# ── Exotel WebSocket ───────────────────────────────────────────
@app.websocket("/ws/exotel")
async def exotel_websocket(websocket: WebSocket):
    await websocket.accept()

    lead_id = websocket.query_params.get("lead_id")
    phone   = websocket.query_params.get("phone")

    # Look up lead from DB first, fallback to query params
    lead = None
    if lead_id:
        try:
            lead = db.get_lead(int(lead_id))
        except Exception:
            pass

    if not lead and phone:
        lead = db.get_lead_by_phone(phone)

    if not lead and lead_id:
        # Minimal lead dict from query params
        lead = {"id": None, "name": "", "phone": phone or "", "company": "",
                "campaign_id": None}

    logger.info(f"Exotel WS connected | lead_id={lead_id} | phone={phone}")

    try:
        await run_exotel_pipeline(websocket, lead or {})
    except WebSocketDisconnect:
        logger.info(f"Exotel WS disconnected | lead_id={lead_id}")
    except Exception as e:
        logger.error(f"Pipeline error: {e}")
        try:
            await websocket.close()
        except Exception:
            pass


# ── Exotel outbound trigger ────────────────────────────────────
@app.post("/exotel/call")
async def exotel_outbound(request: Request):
    from app.campaign_runner import make_single_call

    body    = await request.json()
    phone   = body.get("phone")
    lead_id = body.get("lead_id")

    if not phone:
        return JSONResponse({"error": "phone required"}, status_code=400)

    # Look up lead for DB record
    lead = None
    if lead_id:
        try:
            lead = db.get_lead(int(lead_id))
        except Exception:
            pass
    if not lead and phone:
        lead = db.get_lead_by_phone(phone)

    result = await make_single_call(phone)

    if result:
        call_sid = result.get("call_sid")
        norm_phone = result.get("phone", phone)
        db.create_call(
            phone       = norm_phone,
            lead_name   = lead.get("name") if lead else None,
            company     = lead.get("company") if lead else None,
            lead_id     = lead.get("id") if lead else None,
            campaign_id = None,
            call_sid    = call_sid,
        )
        db.add_log(f"📞 Manual call initiated: {lead.get('name','') if lead else phone} ({norm_phone})")
        return JSONResponse({"status": "initiated", "call_sid": call_sid})
    else:
        return JSONResponse({"status": "failed", "error": "Call not initiated"}, status_code=400)


@app.post("/exotel/status")
async def exotel_status(request: Request):
    data = dict(await request.form())
    logger.info(f"Exotel status callback: {data}")

    call_sid  = data.get("CallSid", "")
    status    = data.get("Status", "unknown")       # completed, no-answer, busy, failed
    duration  = int(data.get("ConversationDuration", 0) or 0)
    from_num  = data.get("From", "")

    # Map Exotel status → our outcome/sentiment
    outcome_map = {
        "completed": "answered",
        "no-answer": "no_answer",
        "busy":      "no_answer",
        "failed":    "failed",
    }
    outcome = outcome_map.get(status, "unknown")

    # Find matching call record by call_sid or phone
    call = None
    if call_sid:
        with db.get_conn() as conn:
            row = conn.execute(
                "SELECT * FROM calls WHERE call_sid=?", (call_sid,)
            ).fetchone()
            if row:
                call = dict(row)

    if not call and from_num:
        call = db.get_call_by_phone(from_num)

    if call:
        db.complete_call(
            call_id      = call["id"],
            duration_sec = duration,
            outcome      = outcome,
            sentiment    = "neutral",
            summary      = f"Exotel: {status} | Duration: {duration}s",
        )
        # Update lead status
        if call.get("lead_id") and outcome == "answered":
            db.update_lead(call["lead_id"], status="called")
        logger.info(f"DB updated for CallSid {call_sid}: {outcome} ({duration}s)")
    else:
        logger.info(f"No matching call found for CallSid {call_sid}")

    return JSONResponse({"status": "ok"})


# ── Legacy routes (kept for backward compatibility) ────────────

@app.get("/calls")
async def list_calls_legacy():
    calls = db.get_calls(limit=50)
    return JSONResponse({"total": len(calls), "calls": calls})


@app.get("/leads")
async def list_leads_legacy():
    leads = db.get_leads(limit=100)
    return JSONResponse({"total": len(leads), "leads": leads})


@app.post("/call/single")
async def single_call(request: Request):
    from app.campaign_runner import make_single_call
    body  = await request.json()
    phone = body.get("phone")
    if not phone:
        raise HTTPException(status_code=400, detail="Phone number required")
    result = await make_single_call(phone)
    if result:
        return JSONResponse({"status": "initiated", "message": f"Call to {phone} initiated"})
    return JSONResponse({"status": "failed"}, status_code=400)


@app.post("/campaign/start")
async def start_campaign_legacy(request: Request):
    body     = await request.json()
    csv_path = body.get("csv_path", "leads/sample_leads.csv")
    delay    = body.get("delay_seconds", 60)

    leads = []
    try:
        with open(csv_path, newline='') as f:
            reader = csv.DictReader(f)
            leads = list(reader)
    except FileNotFoundError:
        raise HTTPException(status_code=400, detail=f"CSV not found: {csv_path}")

    if not leads:
        raise HTTPException(status_code=400, detail="No leads in CSV")

    # Import leads into DB
    count = db.bulk_insert_leads(leads)
    db.add_log(f"📂 Campaign CSV imported: {count} leads from {csv_path}")

    return JSONResponse({
        "message":  f"Campaign queued with {len(leads)} leads ({count} new in DB)",
        "imported": count,
        "delay":    delay,
    })


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "app.main:app",
        host=os.getenv("SERVER_HOST", "0.0.0.0"),
        port=int(os.getenv("SERVER_PORT", 8000)),
        reload=False,
        log_level="info",
    )
