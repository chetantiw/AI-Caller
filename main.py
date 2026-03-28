"""
app/main.py
FastAPI server - PIOPIY WebSocket audio stream + campaign API
"""

import os
import csv
from datetime import datetime
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request, HTTPException
from fastapi.responses import JSONResponse
from loguru import logger
from dotenv import load_dotenv

from app.piopiy_handler import make_outbound_call
from app.pipeline import run_pipeline
from app.celery_worker import launch_campaign

load_dotenv()

app = FastAPI(
    title="AI Cold Calling Agent",
    description="PIOPIY + Sarvam AI + GPT-4o powered outbound sales agent",
    version="1.0.0",
)

# In-memory call log
call_log: dict = {}


def load_leads(csv_path: str = "leads/sample_leads.csv") -> list:
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
# PIOPIY WEBSOCKET — AUDIO STREAM
# ─────────────────────────────────────────────────────────────────────────────

@app.websocket("/ws/piopiy")
async def piopiy_websocket(websocket: WebSocket):
    """
    PIOPIY streams bidirectional audio to this WebSocket endpoint.
    We run the full Pipecat STT -> LLM -> TTS pipeline here.

    PIOPIY connects here automatically when the prospect answers the call.
    No webhook/XML needed — it's all configured in the PIOPIY dashboard.
    """
    await websocket.accept()

    # Extract lead info from query params
    lead_id = websocket.query_params.get("lead_id", "")
    lead = next(
        (l for l in leads_cache if l.get("phone", "").replace("+", "") in lead_id),
        None
    )

    logger.info(f"PIOPIY WebSocket connected | lead_id: {lead_id} | lead: {lead}")

    try:
        await run_pipeline(websocket, lead=lead)
    except WebSocketDisconnect:
        logger.info(f"PIOPIY WebSocket disconnected | lead_id: {lead_id}")
    except Exception as e:
        logger.error(f"Pipeline error for {lead_id}: {e}")
        try:
            await websocket.close()
        except Exception:
            pass


# ─────────────────────────────────────────────────────────────────────────────
# CAMPAIGN & CALL APIs
# ─────────────────────────────────────────────────────────────────────────────

@app.post("/call/single")
async def single_call(request: Request):
    """
    Trigger a single outbound AI call immediately.

    Body: { "phone": "919876543210", "lead_id": "optional" }
    Note: Phone number without + prefix for PIOPIY
    """
    body    = await request.json()
    phone   = body.get("phone")
    lead_id = body.get("lead_id", phone)

    if not phone:
        raise HTTPException(status_code=400, detail="Phone number required")

    request_id = make_outbound_call(phone, lead_id)

    call_log[request_id] = {
        "lead_id": lead_id,
        "phone": phone,
        "status": "initiated",
        "created_at": datetime.utcnow().isoformat(),
    }

    return JSONResponse({
        "request_id": request_id,
        "status": "initiated",
        "message": f"Call to {phone} initiated via PIOPIY"
    })


@app.post("/campaign/start")
async def start_campaign(request: Request):
    """
    Start a bulk outbound calling campaign from lead CSV.

    Body: { "csv_path": "leads/sample_leads.csv", "delay_seconds": 30 }
    """
    body     = await request.json()
    csv_path = body.get("csv_path", "leads/sample_leads.csv")
    delay    = body.get("delay_seconds", 30)

    leads = load_leads(csv_path)
    if not leads:
        raise HTTPException(status_code=400, detail="No leads found in CSV")

    task = launch_campaign.delay(leads, delay)
    logger.info(f"Campaign started | {len(leads)} leads | task_id: {task.id}")

    return JSONResponse({
        "message": f"Campaign started with {len(leads)} leads",
        "task_id": task.id,
        "delay_between_calls": delay,
    })


@app.get("/calls")
async def list_calls():
    """List all calls in current session."""
    return JSONResponse({"total": len(call_log), "calls": call_log})


@app.get("/leads")
async def list_leads():
    """List loaded leads."""
    return JSONResponse({"total": len(leads_cache), "leads": leads_cache})


@app.get("/health")
async def health():
    return JSONResponse({
        "status": "ok",
        "version": "1.0.0",
        "agent": os.getenv("AGENT_NAME", "Priya"),
        "telephony": "PIOPIY (TeleCMI)",
        "stt_tts": "Sarvam AI",
        "llm": os.getenv("OPENAI_MODEL", "gpt-4o"),
        "call_rate": "₹0.59/min",
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
