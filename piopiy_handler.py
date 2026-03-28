"""
app/piopiy_handler.py
Handles outbound call initiation via PIOPIY (TeleCMI) API.
PIOPIY uses a direct REST API call + WebSocket TCP stream (no TwiML/XML needed).
"""

import os
import json
import requests
from loguru import logger
from dotenv import load_dotenv

load_dotenv()

# PIOPIY India API endpoint
PIOPIY_INDIA_URL = "https://rest.telecmi.com/v2/ind_pcmo_make_call"
PIOPIY_GLOBAL_URL = "https://rest.telecmi.com/v2/global_pcmo_make_call"


def make_outbound_call(to_number: str, lead_id: str = None) -> str:
    """
    Initiate an outbound AI streaming call via PIOPIY.

    PIOPIY works differently from Twilio/Plivo:
    - No webhook XML needed
    - You pass the WebSocket URL directly in the API call
    - PIOPIY streams audio to your WebSocket server in real-time

    Args:
        to_number : Prospect number with country code (919876543210 — no + prefix)
        lead_id   : Optional lead identifier

    Returns:
        PIOPIY request_id
    """
    app_id     = int(os.getenv("PIOPIY_APP_ID"))
    app_secret = os.getenv("PIOPIY_APP_SECRET")
    from_number = int(os.getenv("PIOPIY_PHONE_NUMBER"))
    public_url  = os.getenv("PUBLIC_URL", "https://ai.mutechautomation.com")

    # PIOPIY needs a TCP WebSocket URL (ws:// not wss://)
    # Your VPS must expose port 8765 or use ngrok TCP tunnel
    ws_host = public_url.replace("https://", "").replace("http://", "")
    ws_url  = f"ws://{ws_host}/ws/piopiy"

    if lead_id:
        ws_url += f"?lead_id={lead_id}"

    # Clean number — PIOPIY expects number without + sign
    to_clean = int(str(to_number).replace("+", ""))

    payload = {
        "appid": app_id,
        "secret": app_secret,
        "from": from_number,
        "to": to_clean,
        "extra_params": {"lead_id": lead_id or ""},
        "pcmo": [
            {
                "action": "stream",
                "ws_url": ws_url,
                "listen_mode": "both",       # Stream both caller + agent audio
                "voice_quality": 8000,       # 8kHz — standard telephony
                "stream_on_answer": True     # Start streaming only after call is answered
            }
        ]
    }

    logger.info(f"Making PIOPIY call | to: {to_clean} | ws_url: {ws_url}")

    # Use India endpoint for Indian numbers, global for UAE/others
    is_india = str(to_number).startswith("+91") or str(to_number).startswith("91")
    endpoint = PIOPIY_INDIA_URL if is_india else PIOPIY_GLOBAL_URL

    try:
        response = requests.post(
            endpoint,
            json=payload,
            headers={"Content-Type": "application/json"},
            timeout=15,
        )
        data = response.json()

        if data.get("code") == 200 or data.get("status") == "progress":
            request_id = data.get("request_id", "")
            logger.info(f"PIOPIY call initiated | request_id: {request_id}")
            return request_id
        else:
            logger.error(f"PIOPIY call failed | response: {data}")
            raise Exception(f"PIOPIY error: {data}")

    except requests.exceptions.RequestException as e:
        logger.error(f"PIOPIY API request failed: {e}")
        raise
