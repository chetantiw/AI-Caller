"""
app/communication_service.py
Handles WhatsApp, SMS, Email sending and trigger execution.
"""
import asyncio
import os
import re
from datetime import datetime
from loguru import logger
from app import tenant_db as tdb


def _render(template_body: str, data: dict) -> str:
    """Replace {variable} placeholders in template."""
    for key, value in data.items():
        template_body = template_body.replace(f"{{{key}}}", str(value or ""))
    return template_body


def _build_vars(tenant_config: dict, lead: dict, call_summary: str = "") -> dict:
    """Build variable dict for template rendering."""
    now = datetime.now()
    return {
        "name":         lead.get("name") or lead.get("lead_name") or "",
        "phone":        lead.get("phone") or "",
        "email":        lead.get("email") or "",
        "company":      lead.get("company") or "",
        "agent":        tenant_config.get("agent_name") or "Aira",
        "tenant_name":  tenant_config.get("company_name") or "",
        "date":         now.strftime("%d %B %Y"),
        "time":         now.strftime("%I:%M %p"),
        "summary":      call_summary or "",
        "website":      tenant_config.get("company_website") or "",
    }


# ── WhatsApp via TeleCMI ───────────────────────────────────────
async def send_whatsapp(phone: str, message: str, tenant_config: dict) -> bool:
    api_key = tenant_config.get("whatsapp_api_key") or ""
    wa_num  = tenant_config.get("whatsapp_number") or ""
    if not api_key or not wa_num:
        logger.warning("[WhatsApp] Credentials not configured")
        return False
    try:
        import httpx
        phone = re.sub(r"[^\d]", "", phone)
        if not phone.startswith("91"):
            phone = "91" + phone.lstrip("0")

        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(
                "https://rest.telecmi.com/v2/send_whatsapp",
                json={
                    "appid":   api_key,
                    "secret":  tenant_config.get("whatsapp_secret", ""),
                    "to":      phone,
                    "from":    wa_num,
                    "message": message,
                }
            )
            if resp.status_code == 200:
                logger.info(f"[WhatsApp] Sent to {phone}")
                return True
            else:
                logger.error(f"[WhatsApp] Failed: {resp.text}")
                return False
    except Exception as e:
        logger.error(f"[WhatsApp] Error: {e}")
        return False


# ── SMS via TeleCMI ────────────────────────────────────────────
async def send_sms(phone: str, message: str, tenant_config: dict) -> bool:
    appid  = tenant_config.get("telecmi_sms_appid") or ""
    secret = tenant_config.get("telecmi_sms_secret") or ""
    if not appid or not secret:
        logger.warning("[SMS] TeleCMI SMS credentials not configured")
        return False
    try:
        import httpx
        phone = re.sub(r"[^\d]", "", phone)
        if not phone.startswith("91"):
            phone = "91" + phone.lstrip("0")

        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(
                "https://rest.telecmi.com/v2/send_sms",
                json={
                    "appid":   appid,
                    "secret":  secret,
                    "to":      phone,
                    "message": message,
                }
            )
            if resp.status_code == 200:
                logger.info(f"[SMS] Sent to {phone}")
                return True
            else:
                logger.error(f"[SMS] Failed: {resp.text}")
                return False
    except Exception as e:
        logger.error(f"[SMS] Error: {e}")
        return False


# ── Email via yagmail ──────────────────────────────────────────
async def send_email(to_email: str, subject: str, body: str,
                     tenant_config: dict) -> bool:
    if not to_email or "@" not in to_email:
        logger.warning(f"[Email] Invalid email: {to_email}")
        return False
    email_user = tenant_config.get("email_user") or os.getenv("EMAIL_USER", "")
    email_pass = tenant_config.get("email_pass") or os.getenv("EMAIL_PASS", "")
    if not email_user or not email_pass:
        logger.warning("[Email] Email credentials not configured")
        return False
    try:
        import yagmail
        loop = asyncio.get_event_loop()

        def _send():
            yg = yagmail.SMTP(email_user, email_pass)
            yg.send(to=to_email, subject=subject, contents=body)

        await loop.run_in_executor(None, _send)
        logger.info(f"[Email] Sent to {to_email}")
        return True
    except Exception as e:
        logger.error(f"[Email] Error: {e}")
        return False


# ── Main trigger executor ──────────────────────────────────────
async def execute_triggers(
    tenant_id: int,
    outcome: str,
    lead: dict,
    call_summary: str = "",
) -> None:
    """
    Run all active triggers for this outcome concurrently.
    Called from _post_call() after every call.
    """
    tenant_config = tdb.get_tenant_config(tenant_id) or {}

    # Master switches — check before anything
    wa_enabled    = bool(tenant_config.get("whatsapp_enabled", 0))
    sms_enabled   = bool(tenant_config.get("sms_enabled", 0))
    email_enabled = bool(tenant_config.get("email_enabled", 1))

    if not any([wa_enabled, sms_enabled, email_enabled]):
        logger.debug(f"[Triggers] All channels disabled for tenant={tenant_id}")
        return

    triggers = tdb.get_active_triggers(tenant_id, outcome)
    if not triggers:
        logger.debug(f"[Triggers] No triggers for outcome={outcome} tenant={tenant_id}")
        return

    vars_map = _build_vars(tenant_config, lead, call_summary)
    tasks    = []

    for t in triggers:
        message = _render(t["body"], vars_map)
        channel = t["channel"]
        subject = _render(t.get("subject") or "Message from " + vars_map["tenant_name"], vars_map)
        phone   = lead.get("phone", "")
        email   = lead.get("email", "")

        # Respect master switches
        if channel in ("whatsapp", "all") and wa_enabled and phone:
            tasks.append(send_whatsapp(phone, message, tenant_config))
        if channel in ("sms", "all") and sms_enabled and phone:
            tasks.append(send_sms(phone, message, tenant_config))
        if channel in ("email", "all") and email_enabled and email:
            tasks.append(send_email(email, subject, message, tenant_config))

        logger.info(f"[Triggers] Firing: outcome={outcome} channel={channel} tenant={tenant_id}")

    if tasks:
        results = await asyncio.gather(*tasks, return_exceptions=True)
        errors  = [r for r in results if isinstance(r, Exception)]
        if errors:
            logger.warning(f"[Triggers] {len(errors)} send errors: {errors}")
