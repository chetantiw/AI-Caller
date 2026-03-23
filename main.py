"""
app/main.py
FastAPI server — Twilio webhooks + WebSocket audio stream + campaign API
"""

import os
import json
import csv
import asyncio
from datetime import datetime
from typing import Optional

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request, HTTPException
from fastapi.responses import PlainTextResponse, JSONResponse
from loguru import logger
from dotenv import load_dotenv

from app.twilio_handler import make_outbound_call, build_twiml_response, get_call_status
from app.pipeline import run_pipeline
from app.celery_worker import launch_campaign

load_dotenv()

app = FastAPI(
    title="AI Cold Calling Agent",
    description="Pipecat-powered outbound sales voice agent",
    version="1.0.0",
)

# ── In-memory call log (replace with PostgreSQL in production) ────────────────
call_log: dict = {}

# ── Lead cache ────────────────────────────────────────────────────────────────
def load_leads(csv_path: str = "leads/sample_leads.csv") -> list[dict]:
    leads = []
    try:
        with open(csv_path, newline="") as f:
            reader = csv.DictReader(f)
            for row in reader:
                leads.append(row)
        logger.info(f"Loaded {len(leads)} leads from {csv_path}")
    except FileNotFoundError:
        logger.warning(f"Lead file not found: {csv_path}")
    return leads

leads_cache = load_leads()


# ─────────────────────────────────────────────────────────────────────────────
# TWILIO WEBHOOKS
# ─────────────────────────────────────────────────────────────────────────────

@app.post("/twilio/answer", response_class=PlainTextResponse)
async def twilio_answer(request: Request):
    """
    Twilio calls this when the prospect answers.
    We return TwiML to connect their audio to our WebSocket pipeline.
    """
    params = dict(request.query_params)
    lead_id = params.get("lead_id", "")
    public_url = os.getenv("PUBLIC_URL", "https://yourdomain.com")

    logger.info(f"Call answered | lead_id: {lead_id}")

    twiml = build_twiml_response(public_url, lead_id)
    return PlainTextResponse(content=twiml, media_type="application/xml")


@app.post("/twilio/status")
async def twilio_status(request: Request):
    """
    Receives call status updates from Twilio (initiated, ringing, answered, completed).
    """
    form = await request.form()
    call_sid = form.get("CallSid", "")
    call_status = form.get("CallStatus", "")
    duration = form.get("CallDuration", "0")
    answered_by = form.get("AnsweredBy", "unknown")  # human / machine

    logger.info(f"Call status | SID: {call_sid} | Status: {call_status} | Duration: {duration}s | AnsweredBy: {answered_by}")

    # Handle voicemail detection
    if answered_by in ["machine_start", "fax"]:
        logger.info(f"Voicemail detected for {call_sid} — hanging up")
        # Optionally schedule SMS follow-up here

    # Update call log
    if call_sid in call_log:
        call_log[call_sid].update({
            "status": call_status,
            "duration": duration,
            "answered_by": answered_by,
            "updated_at": datetime.utcnow().isoformat(),
        })

    return JSONResponse({"received": True})


# ─────────────────────────────────────────────────────────────────────────────
# WEBSOCKET — AUDIO STREAM (Pipecat Pipeline)
# ─────────────────────────────────────────────────────────────────────────────

@app.websocket("/ws/audio")
async def websocket_audio(websocket: WebSocket):
    """
    WebSocket endpoint that receives Twilio Media Streams audio
    and runs it through the Pipecat STT → LLM → TTS pipeline.
    """
    await websocket.accept()

    # Extract lead_id from query params
    lead_id = websocket.query_params.get("lead_id", "")
    lead = next((l for l in leads_cache if l.get("phone", "").replace("+", "") in lead_id or lead_id == ""), None)

    logger.info(f"WebSocket connected | lead_id: {lead_id} | lead: {lead}")

    try:
        await run_pipeline(websocket, lead=lead)
    except WebSocketDisconnect:
        logger.info(f"WebSocket disconnected | lead_id: {lead_id}")
    except Exception as e:
        logger.error(f"Pipeline error: {e}")
        await websocket.close()


# ─────────────────────────────────────────────────────────────────────────────
# CAMPAIGN API
# ─────────────────────────────────────────────────────────────────────────────

@app.post("/campaign/start")
async def start_campaign(request: Request):
    """
    Start an outbound calling campaign from the lead list.

    Body: { "csv_path": "leads/sample_leads.csv", "delay_seconds": 30 }
    """
    body = await request.json()
    csv_path = body.get("csv_path", "leads/sample_leads.csv")
    delay = body.get("delay_seconds", 30)

    leads = load_leads(csv_path)
    if not leads:
        raise HTTPException(status_code=400, detail="No leads found in CSV")

    # Queue via Celery
    task = launch_campaign.delay(leads, delay)

    logger.info(f"Campaign started | {len(leads)} leads | task_id: {task.id}")
    return JSONResponse({
        "message": f"Campaign started with {len(leads)} leads",
        "task_id": task.id,
        "delay_between_calls": delay,
    })


@app.post("/call/single")
async def single_call(request: Request):
    """
    Make a single outbound call immediately.

    Body: { "phone": "+919876543210", "lead_id": "optional" }
    """
    body = await request.json()
    phone = body.get("phone")
    lead_id = body.get("lead_id", phone)

    if not phone:
        raise HTTPException(status_code=400, detail="Phone number required")

    call_sid = make_outbound_call(phone, lead_id)

    call_log[call_sid] = {
        "lead_id": lead_id,
        "phone": phone,
        "status": "initiated",
        "created_at": datetime.utcnow().isoformat(),
    }

    return JSONResponse({"call_sid": call_sid, "status": "initiated"})


@app.get("/call/{call_sid}")
async def call_status(call_sid: str):
    """Get status of a specific call."""
    status = get_call_status(call_sid)
    local = call_log.get(call_sid, {})
    return JSONResponse({**local, **status})


@app.get("/calls")
async def list_calls():
    """List all calls in current session log."""
    return JSONResponse({"total": len(call_log), "calls": call_log})


@app.get("/leads")
async def list_leads():
    """List loaded leads."""
    return JSONResponse({"total": len(leads_cache), "leads": leads_cache})


@app.get("/health")
async def health():
    return JSONResponse({"status": "ok", "version": "1.0.0", "agent": os.getenv("AGENT_NAME", "Priya")})


# ─────────────────────────────────────────────────────────────────────────────
# ENTRY POINT
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "app.main:app",
        host=os.getenv("SERVER_HOST", "0.0.0.0"),
        port=int(os.getenv("SERVER_PORT", 8000)),
        reload=False,
        log_level="info",
    )
