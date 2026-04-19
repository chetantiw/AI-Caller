"""

app/main.py

FastAPI server — MuTech AI Caller (Aira)


Telephony: PIOPIY (primary) + Exotel (secondary)

Admin can switch active telephony in Settings → Agent Settings

Active provider stored in DB system_config table (falls back to TELEPHONY env var)


WebSocket routes:

  /ws/exotel  — Exotel voicebot stream (keep alive for Exotel dashboard config)

  /ws/piopiy  — PIOPIY audio stream (future: if PIOPIY adds WS streaming)


REST routes:

  /exotel/call    — trigger via Exotel

  /exotel/status  — Exotel callback

  All /api/* routes from api_routes.py

"""


import asyncio

import os

import csv

from datetime import datetime


from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request, HTTPException

from fastapi.responses import JSONResponse, FileResponse, RedirectResponse

from fastapi.staticfiles import StaticFiles

from loguru import logger

from dotenv import load_dotenv


from app.exotel_pipeline import run_exotel_pipeline

from app.api_routes import router as api_router
from app import super_routes

from app import database as db


load_dotenv()


app = FastAPI(

    title="MuTech AI Caller — Aira",

    description="PIOPIY + Exotel | Sarvam AI + LLM | Hindi voice sales agent",

    version="3.0.0",

)



# ── Startup ────────────────────────────────────────────────────

@app.on_event("startup")

async def startup_event():

    db.init_db()

    provider = _get_telephony()

    db.add_log(f"🟢 MuTech AI Caller v3.0 started | Telephony: {provider}")

    logger.info(f"Server started | Telephony: {provider}")

    

    # Start campaign schedulers

    from app.campaign_scheduler import start_scheduler

    await start_scheduler()

    from app.scheduler import start_scheduler as start_new_scheduler

    start_new_scheduler()

    logger.info("Campaign scheduler started")

    db.add_log("📅 Campaign scheduler initialized")

    from app.retry_scheduler import start_retry_scheduler

    asyncio.create_task(start_retry_scheduler())

    logger.info("Retry scheduler started")

    db.add_log("🔁 Retry scheduler initialized")



# ── Mount API routes ───────────────────────────────────────────

app.include_router(api_router)
app.include_router(super_routes.router, prefix="/super")



# ── Static files + Dashboard ───────────────────────────────────

_static_dir = os.path.join(os.path.dirname(__file__), "../static")

os.makedirs(_static_dir, exist_ok=True)

app.mount("/static", StaticFiles(directory=_static_dir), name="static")



# ════════════════════════════════════════════════════════════════

# TELEPHONY HELPER  — reads active provider from DB or .env

# ════════════════════════════════════════════════════════════════

def _get_telephony() -> str:

    """

    Returns active telephony provider: 'piopiy' or 'exotel'.

    Order of precedence:

      1. DB system_config key 'telephony_provider' (set by admin via dashboard)

      2. TELEPHONY_PROVIDER env var

      3. Default: 'piopiy'

    """

    try:

        val = db.get_config("telephony_provider")

        if val in ("piopiy", "exotel"):

            return val

    except Exception:

        pass

    env_val = os.getenv("TELEPHONY_PROVIDER", "piopiy").lower()

    return env_val if env_val in ("piopiy", "exotel") else "piopiy"



def _make_outbound_call(phone: str, lead_id: str = None, metadata: dict = None) -> str:

    """Dispatch outbound call to the currently active telephony provider."""

    provider = _get_telephony()

    if provider == "piopiy":

        from app.piopiy_handler import make_outbound_call as piopiy_call

        return piopiy_call(phone, lead_id=lead_id, metadata=metadata)

    else:

        import asyncio

        from app.exotel_handler import make_outbound_call as exotel_call

        # exotel_call is async — run it synchronously here

        loop = asyncio.new_event_loop()

        result = loop.run_until_complete(exotel_call(phone, lead_id))

        loop.close()

        return str(result)



# ════════════════════════════════════════════════════════════════

# BASIC ROUTES

# ════════════════════════════════════════════════════════════════

@app.get("/")

async def root():

    return RedirectResponse(url="/dashboard")



@app.get("/dashboard")

async def dashboard():

    _path = os.path.join(_static_dir, "dashboard.html")

    if os.path.exists(_path):

        return FileResponse(_path)

    return JSONResponse({"error": "dashboard.html not found"}, status_code=404)



@app.get("/super")
async def super_admin_dashboard():
    _path = os.path.join(_static_dir, "super_dashboard.html")
    if os.path.exists(_path):
        return FileResponse(_path)
    return JSONResponse({"error": "super_dashboard.html not found"}, status_code=404)


@app.get("/settings")
async def tenant_settings_page():
    _path = os.path.join(_static_dir, "tenant_settings.html")
    if os.path.exists(_path):
        return FileResponse(_path)
    return JSONResponse({"error": "tenant_settings.html not found"}, status_code=404)


@app.get("/signup")
async def signup_page():
    _path = os.path.join(_static_dir, "signup.html")
    if os.path.exists(_path):
        return FileResponse(_path)
    return JSONResponse({"error": "signup.html not found"}, status_code=404)



@app.get("/health")

async def health():

    stats    = db.get_dashboard_stats()

    provider = _get_telephony()

    return JSONResponse({

        "status":      "ok",

        "version":     "3.0.0",

        "agent":       "Aira",

        "telephony":   provider.upper(),

        "calls_today": stats.get("calls_today", 0),

        "total_calls": stats.get("total_calls", 0),

    })



# ════════════════════════════════════════════════════════════════

# EXOTEL WEBSOCKET  — stays live (Exotel dashboard points here)

# ════════════════════════════════════════════════════════════════

@app.websocket("/ws/exotel")

async def exotel_websocket(websocket: WebSocket):

    await websocket.accept()


    lead_id = websocket.query_params.get("lead_id")

    phone   = websocket.query_params.get("phone")


    lead = None

    if lead_id:

        try:

            lead = db.get_lead(int(lead_id))

        except Exception:

            pass

    if not lead and phone:

        lead = db.get_lead_by_phone(phone)

    if not lead:

        lead = {"id": None, "name": "", "phone": phone or "", "company": "", "campaign_id": None}


    logger.info(f"Exotel WS connected | lead_id={lead_id} | phone={phone}")


    try:

        await run_exotel_pipeline(websocket, lead)

    except WebSocketDisconnect:

        logger.info(f"Exotel WS disconnected | lead_id={lead_id}")

    except Exception as e:

        logger.error(f"Exotel pipeline error: {e}")

        try:

            await websocket.close()

        except Exception:

            pass



# ════════════════════════════════════════════════════════════════

# EXOTEL STATUS CALLBACK

# ════════════════════════════════════════════════════════════════

@app.post("/exotel/status")

async def exotel_status(request: Request):

    data = dict(await request.form())

    logger.info(f"Exotel status: {data}")


    call_sid = data.get("CallSid", "")

    status   = data.get("Status", "unknown")

    duration = int(data.get("ConversationDuration", 0) or 0)

    from_num = data.get("From", "")


    outcome_map = {

        "completed": "answered",

        "no-answer": "no_answer",

        "busy":      "no_answer",

        "failed":    "failed",

    }

    outcome = outcome_map.get(status, "unknown")


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

            summary      = f"Exotel: {status} | {duration}s",

        )

        if call.get("lead_id") and outcome == "answered":

            db.update_lead(call["lead_id"], status="called")


    return JSONResponse({"status": "ok"})



# ════════════════════════════════════════════════════════════════

# UNIFIED OUTBOUND TRIGGER  — uses active provider

# ════════════════════════════════════════════════════════════════

@app.post("/call/trigger")

async def trigger_call(request: Request):

    """

    Trigger a single outbound call via the currently active telephony provider.

    Body: { "phone": "...", "lead_id": "...", "name": "..." }

    """

    from app.campaign_runner import make_single_call

    body  = await request.json()

    phone = body.get("phone", "").strip()

    if not phone:

        return JSONResponse({"error": "phone required"}, status_code=400)


    result = await make_single_call(phone)

    if result:

        provider = _get_telephony()

        db.add_log(f"📞 Manual call [{provider.upper()}]: {phone}")

        return JSONResponse({"status": "initiated", "provider": provider, **result})

    return JSONResponse({"status": "failed"}, status_code=500)



# ════════════════════════════════════════════════════════════════

# LEGACY ROUTES  — kept for backward compatibility

# ════════════════════════════════════════════════════════════════

@app.post("/exotel/call")

async def exotel_outbound_legacy(request: Request):

    """Legacy Exotel outbound trigger — now routes through active provider."""

    from app.campaign_runner import make_single_call

    body  = await request.json()

    phone = body.get("phone")

    if not phone:

        return JSONResponse({"error": "phone required"}, status_code=400)

    result = await make_single_call(phone)

    if result:

        return JSONResponse({"status": "initiated", "result": str(result)[:100]})

    return JSONResponse({"status": "failed"}, status_code=500)



@app.get("/calls")

async def list_calls_legacy():

    return JSONResponse({"total": 0, "calls": db.get_calls(limit=50)})



@app.get("/leads")

async def list_leads_legacy():

    return JSONResponse({"total": 0, "leads": db.get_leads(limit=100)})



@app.post("/call/single")

async def single_call_legacy(request: Request):

    from app.campaign_runner import make_single_call

    body  = await request.json()

    phone = body.get("phone")

    if not phone:

        raise HTTPException(status_code=400, detail="Phone required")

    result = await make_single_call(phone)

    if result:

        return JSONResponse({"status": "initiated", "message": f"Call to {phone} initiated"})

    return JSONResponse({"status": "failed"}, status_code=500)



@app.post("/campaign/start")

async def start_campaign_legacy(request: Request):

    body     = await request.json()

    csv_path = body.get("csv_path", "leads/sample_leads.csv")

    delay    = body.get("delay_seconds", 60)

    leads    = []

    try:

        with open(csv_path, newline="") as f:

            leads = list(csv.DictReader(f))

    except FileNotFoundError:

        raise HTTPException(status_code=400, detail=f"CSV not found: {csv_path}")

    if not leads:

        raise HTTPException(status_code=400, detail="No leads in CSV")

    count = db.bulk_insert_leads(leads)

    db.add_log(f"📂 CSV imported: {count} leads from {csv_path}")

    return JSONResponse({

        "message":  f"Campaign queued — {len(leads)} leads ({count} new)",

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
