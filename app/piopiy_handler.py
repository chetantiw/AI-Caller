"""

app/piopiy_handler.py

PIOPIY Outbound Call Handler

Uses: POST https://rest.piopiy.com/v3/voice/ai/call  (Bearer token auth)

When customer answers → PIOPIY routes to running piopiy_agent.py via signaling server


Required .env keys:

  PIOPIY_AGENT_ID     — agent UUID from dashboard

  PIOPIY_AGENT_TOKEN  — JWT token (also used for REST API)

  PIOPIY_NUMBER       — caller ID e.g. +917314854688

"""


import os

import requests

from loguru import logger

from dotenv import load_dotenv


load_dotenv()


# Primary REST endpoint — PIOPIY AI Agent outbound call

_PIOPIY_REST_URL = "https://rest.piopiy.com/v3/voice/ai/call"


# Fallback: TeleCMI PCMO endpoint for Indian numbers

_TELECMI_IND_URL = "https://rest.telecmi.com/v2/ind_pcmo_make_call"



def _normalize(phone: str) -> str:

    """Convert any phone format to 12-digit string (91XXXXXXXXXX)."""

    digits = "".join(c for c in str(phone) if c.isdigit())

    # Handle scientific notation from Excel (e.g. 9.18827E+11)

    if "e" in str(phone).lower():

        try:

            digits = str(int(float(phone)))

        except Exception:

            pass

    if len(digits) == 10:

        return "91" + digits

    if len(digits) == 11 and digits.startswith("0"):

        return "91" + digits[1:]

    if len(digits) == 12 and digits.startswith("91"):

        return digits

    if len(digits) == 13 and digits.startswith("091"):

        return "91" + digits[3:]

    return digits



def make_outbound_call(

    to_number: str,

    lead_id: str = None,

    metadata: dict = None,

) -> str:

    """

    Trigger an outbound PIOPIY AI agent call.


    Flow:

      1. REST API dials to_number

      2. Customer answers → PIOPIY routes to tenant's agent via signaling

      3. Agent speaks using tenant's configured services


    Returns call_id string on success, raises Exception on failure.

    """

    # Extract tenant_id from metadata to load tenant-specific credentials
    metadata = metadata or {}
    tenant_id = int(metadata.get("tenant_id", 1)) if str(metadata.get("tenant_id", 1)).isdigit() else 1
    
    logger.info(f"🔍 DEBUG: make_outbound_call received metadata={metadata}, tenant_id={tenant_id}")
    
    # Load tenant-specific PIOPIY credentials from database
    from app import tenant_db as tdb
    tenant_config = tdb.get_tenant_config(tenant_id)
    logger.info(f"🔍 DEBUG: tenant_config loaded for tenant_id={tenant_id}")

    if not tenant_config:
        logger.error(f"❌ No tenant config found for tenant_id={tenant_id}")
        raise Exception(f"Tenant {tenant_id} not configured")

    agent_id  = (tenant_config.get("piopiy_agent_id")    or "").strip()
    api_token = (tenant_config.get("piopiy_agent_token") or "").strip()
    caller_id = (tenant_config.get("piopiy_number")       or "").strip()

    # For tenants without their own PIOPIY agent credentials, fall back to
    # the platform account (tenant 1). When using the platform's agent we MUST
    # also use the platform's caller number — mixing tenant's number with
    # platform's agent causes PIOPIY "app_map_not_valid" (number not registered
    # under that account). Tenant's own piopiy_number is only valid when they
    # have their own full PIOPIY account set up.
    if (not agent_id or not api_token) and tenant_id != 1:
        logger.info(f"[Tenant {tenant_id}] No own PIOPIY credentials — falling back to platform (tenant 1)")
        platform_cfg = tdb.get_tenant_config(1) or {}
        agent_id  = (platform_cfg.get("piopiy_agent_id")    or "").strip()
        api_token = (platform_cfg.get("piopiy_agent_token") or "").strip()
        caller_id = (platform_cfg.get("piopiy_number")       or "").strip()

    logger.info(f"[Tenant {tenant_id}] agent_id={agent_id[:20] if agent_id else 'NONE'}… | caller_id={caller_id}")

    if not agent_id:
        raise Exception(f"Tenant {tenant_id}: PIOPIY Agent ID not configured. Set it in Account Settings → Telephony.")

    if not api_token:
        raise Exception(f"Tenant {tenant_id}: PIOPIY Agent Token not configured. Set it in Account Settings → Telephony.")

    if not caller_id:
        raise Exception(f"Tenant {tenant_id}: PIOPIY caller number not configured. Set it in Account Settings → Telephony.")


    to_normalized   = _normalize(to_number)

    from_normalized = _normalize(caller_id)


    logger.info(f"PIOPIY outbound | to: +{to_normalized} | from: +{from_normalized} | agent: {agent_id}")


    variables = {**(metadata or {})}

    if lead_id:

        variables["lead_id"] = str(lead_id)


    payload = {

        "caller_id": from_normalized,

        "to_number":  to_normalized,

        "agent_id":   agent_id,

    }

    if variables:

        payload["variables"] = variables


    headers = {

        "Authorization": f"Bearer {api_token}",

        "Content-Type":  "application/json",

    }


    # ── Primary: PIOPIY AI Agent REST API ──────────────────────

    try:

        resp = requests.post(

            _PIOPIY_REST_URL,

            json=payload,

            headers=headers,

            timeout=15,

        )

        logger.info(f"PIOPIY REST [{resp.status_code}]: {resp.text[:200]}")


        if resp.status_code in (200, 201):

            try:

                data = resp.json()

                call_id = (

                    data.get("request") or
                    data.get("call_id") or

                    data.get("request_id") or

                    data.get("id") or

                    str(data)

                )

            except Exception:

                call_id = resp.text.strip() or "initiated"

            logger.info(f"✅ PIOPIY call initiated: {call_id}")

            return str(call_id)


        # 4xx/5xx — log and fall through to PCMO fallback

        logger.warning(f"PIOPIY REST returned {resp.status_code}: {resp.text[:200]}")


    except requests.exceptions.RequestException as e:

        logger.warning(f"PIOPIY REST unreachable: {e} — trying PCMO fallback")


    # ── Fallback: TeleCMI PCMO (bridged call) ──────────────────

    return _pcmo_fallback(to_normalized, from_normalized, api_token, agent_id)



def _pcmo_fallback(to_digits: str, caller_digits: str, api_token: str, agent_id: str) -> str:

    """

    Fallback outbound via TeleCMI PCMO bridge.

    Only reached if primary PIOPIY REST API fails.

    """

    import json as _json


    # PCMO needs numeric appid — if agent_id is UUID, use token hash as fallback key

    try:

        app_id_int = int(agent_id)

    except ValueError:

        # UUID-style agent_id — can't use PCMO (needs numeric appid)

        raise Exception(

            f"PIOPIY REST API failed and PCMO fallback requires numeric app_id. "

            f"Agent ID '{agent_id}' is not numeric. "

            f"Check PIOPIY_AGENT_TOKEN validity or contact PIOPIY support."

        )


    caller_int = int(caller_digits)

    to_int     = int(to_digits)


    pcmo = [{

        "action":   "bridge",

        "from":     caller_int,

        "connect":  [{"type": "pstn", "number": caller_int}],

        "duration": 300,

        "timeout":  30,

        "loop":     1,

    }]

    body = {

        "appid":    app_id_int,

        "secret":   api_token,

        "from":     caller_int,

        "to":       to_int,

        "duration": 300,

        "pcmo":     pcmo,

    }


    resp = requests.post(

        _TELECMI_IND_URL,

        data=_json.dumps(body),

        headers={"content-type": "application/json"},

        timeout=15,

    )

    logger.info(f"PCMO fallback [{resp.status_code}]: {resp.text[:200]}")


    if resp.status_code in (200, 201):

        try:

            data = resp.json()

            return str(data.get("request") or data.get("request_id") or data.get("call_id") or data)

        except Exception:

            return resp.text.strip()


    raise Exception(f"PCMO fallback failed {resp.status_code}: {resp.text[:200]}")
