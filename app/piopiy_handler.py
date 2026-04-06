"""
app/piopiy_handler.py

Makes outbound calls via PIOPIY AI Agent REST API.
API: POST https://rest.piopiy.com/v3/voice/ai/call
Auth: Bearer token (PIOPIY_AGENT_TOKEN)
"""

import os
import json
import requests
from loguru import logger
from dotenv import load_dotenv

load_dotenv()


def make_outbound_call(to_number: str, lead_id: str = None, metadata: dict = None) -> str:
    """
    Make an outbound call via PIOPIY AI Agent REST API.
    When the customer answers, PIOPIY connects the call to the
    running AI agent (piopiy_agent.py) via the signaling server.

    Args:
        to_number: Customer's phone number (any format).
        lead_id:   Optional lead identifier forwarded as a variable.
        metadata:  Optional dict forwarded as variables (e.g. customer_name).

    Returns:
        call_id string from PIOPIY

    Raises:
        Exception on credential error or API failure
    """
    # PIOPIY_TOKEN  — REST API token from dashboard (API Keys section)
    # PIOPIY_AGENT_TOKEN — signaling-server JWT (used by piopiy_agent.py, NOT the REST API)
    api_key   = os.getenv("PIOPIY_TOKEN") or os.getenv("PIOPIY_AGENT_TOKEN")
    agent_id  = os.getenv("PIOPIY_AGENT_ID")
    caller_id = os.getenv("PIOPIY_CALLER_ID") or os.getenv("PIOPIY_NUMBER", "")

    if not api_key or not agent_id:
        raise Exception("Missing credentials: set PIOPIY_TOKEN (REST API key) and PIOPIY_AGENT_ID")
    if not caller_id:
        raise Exception("PIOPIY_CALLER_ID not set")

    from app.database import _normalize_phone
    to_normalized   = _normalize_phone(to_number)
    from_normalized = _normalize_phone(caller_id)

    logger.info(f"PIOPIY outbound call | to: {to_normalized} | from: {from_normalized} | agent: {agent_id}")

    variables = {**(metadata or {})}
    if lead_id:
        variables["lead_id"] = lead_id

    payload = {
        "caller_id": from_normalized,
        "to_number":  to_normalized,
        "agent_id":   agent_id,
    }
    if variables:
        payload["variables"] = variables

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type":  "application/json",
    }

    try:
        resp = requests.post(
            "https://rest.piopiy.com/v3/voice/ai/call",
            json=payload,
            headers=headers,
            timeout=15,
        )
        logger.info(f"PIOPIY API [{resp.status_code}]: {resp.text}")

        if resp.status_code in (200, 201):
            try:
                data    = resp.json()
                call_id = data.get("call_id") or data.get("request_id") or data.get("request") or data.get("id") or str(data)
            except Exception:
                call_id = resp.text.strip()
            logger.info(f"Call initiated: {call_id}")
            return str(call_id)

        raise Exception(f"PIOPIY API returned {resp.status_code}: {resp.text}")

    except requests.exceptions.RequestException as e:
        logger.warning(f"PIOPIY primary API failed: {e} — trying PCMO fallback")

    return _call_via_pcmo(to_normalized, from_normalized, api_key, agent_id)


def _call_via_pcmo(to_digits: str, caller_digits: str, api_key: str, agent_id: str) -> str:
    """
    Fallback: Direct HTTP to TeleCMI PCMO endpoint (rest.telecmi.com).
    Only reached when the primary REST API is unreachable.
    Avoids importing the system piopiy SDK which would shadow the venv package.
    """
    try:
        app_id_int = int(agent_id)
    except ValueError:
        raise Exception(f"PCMO fallback requires a numeric PIOPIY_AGENT_ID, got: {agent_id!r}")

    caller_int = int(caller_digits)
    to_int     = int(to_digits)

    # Build PCMO bridge action (same format as piopiy 1.0.7 RestClient internally)
    pcmo = [{
        "action":   "bridge",
        "from":     caller_int,
        "connect":  [{"type": "pstn", "number": caller_int}],
        "duration": 300,
        "timeout":  30,
        "loop":     1,
    }]
    payload = {
        "appid":    app_id_int,
        "secret":   api_key,
        "from":     caller_int,
        "to":       to_int,
        "duration": 300,
        "pcmo":     pcmo,
    }

    # ind_pcmo for Indian mobile (91XXXXXXXXXX, 12 digits); global otherwise
    if len(to_digits) == 12 and to_digits.startswith("91"):
        url = "https://rest.telecmi.com/v2/ind_pcmo_make_call"
    else:
        url = "https://rest.telecmi.com/v2/global_pcmo_make_call"

    resp = requests.post(url, data=json.dumps(payload),
                         headers={"content-type": "application/json"}, timeout=15)
    logger.info(f"PCMO fallback [{resp.status_code}]: {resp.text}")

    if resp.status_code in (200, 201):
        try:
            data = resp.json()
            return str(data.get("request_id") or data.get("call_id") or data)
        except Exception:
            return resp.text.strip()

    raise Exception(f"PCMO fallback returned {resp.status_code}: {resp.text}")
