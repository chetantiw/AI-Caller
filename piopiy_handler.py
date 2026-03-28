"""
app/piopiy_handler.py
Handles outbound call initiation via PIOPIY (TeleCMI).
Uses direct REST API — no TwiML/XML needed.
"""

import os
import requests
from loguru import logger
from dotenv import load_dotenv

load_dotenv()

PIOPIY_INDIA_URL  = "https://rest.telecmi.com/v2/ind_pcmo_make_call"
PIOPIY_GLOBAL_URL = "https://rest.telecmi.com/v2/global_pcmo_make_call"


def make_outbound_call(to_number: str, lead_id: str = None) -> str:
    """
    Initiate an outbound AI streaming call via PIOPIY REST API.

    PIOPIY flow:
    1. We POST to PIOPIY with target number + WebSocket URL
    2. PIOPIY calls the prospect
    3. When they answer, PIOPIY opens WebSocket to our server
    4. Audio streams bidirectionally through our Pipecat pipeline

    Args:
        to_number : Phone number with country code, no + (e.g. 919876543210)
        lead_id   : Optional lead identifier appended to ws_url as query param

    Returns:
        PIOPIY request_id string
    """
    app_id       = os.getenv("PIOPIY_APP_ID")
    app_secret   = os.getenv("PIOPIY_APP_SECRET")
    from_number  = os.getenv("PIOPIY_PHONE_NUMBER")
    public_url   = os.getenv("PUBLIC_URL", "https://ai.mutechautomation.com")

    # Build WebSocket URL — PIOPIY connects here when call is answered
    ws_host = public_url.replace("https://", "").replace("http://", "")
    ws_url  = f"ws://{ws_host}/ws/piopiy"
    if lead_id:
        ws_url += f"?lead_id={lead_id}"

    # Clean number — PIOPIY expects integer, no + sign
    to_clean   = int(str(to_number).replace("+", "").strip())
    from_clean = int(str(from_number).replace("+", "").strip())

    payload = {
        "appid":  int(app_id),
        "secret": app_secret,
        "from":   from_clean,
        "to":     to_clean,
        "extra_params": {"lead_id": lead_id or ""},
        "pcmo": [
            {
                "action":           "stream",
                "ws_url":           ws_url,
                "listen_mode":      "both",
                "voice_quality":    8000,
                "stream_on_answer": True,
            }
        ],
    }

    logger.info(f"PIOPIY call | to: {to_clean} | ws: {ws_url}")

    # Use India endpoint for +91 numbers, global for others
    is_india = str(to_number).startswith("91") or str(to_number).startswith("+91")
    endpoint = PIOPIY_INDIA_URL if is_india else PIOPIY_GLOBAL_URL

    try:
        resp = requests.post(
            endpoint,
            json=payload,
            headers={"Content-Type": "application/json"},
            timeout=15,
        )
        data = resp.json()
        logger.info(f"PIOPIY response: {data}")

        # Success: cmi_code 200 or status progress
        if data.get("cmi_code") == 200 or data.get("code") == 200 or data.get("status") == "progress":
            request_id = data.get("request_id", "")
            logger.info(f"Call initiated | request_id: {request_id}")
            return request_id
        else:
            raise Exception(f"PIOPIY error: {data}")

    except requests.exceptions.RequestException as e:
        logger.error(f"PIOPIY request failed: {e}")
        raise
