"""
app/communication_service.py
Handles WhatsApp, SMS, Email sending and trigger execution.

WhatsApp providers (per tenant):
  1. openclaw  — preferred; uses the tenant's own linked OpenClaw profile/number
  2. telecmi   — legacy TeleCMI credentials if configured on that tenant

IMPORTANT:
  MuTech's OpenClaw WhatsApp number is NOT a global sender.
  Each client must link their own OpenClaw profile from Account → Notifications.
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


def _normalize_phone(phone: str) -> str:
    digits = re.sub(r"\D", "", str(phone or ""))
    if not digits:
        return ""
    if len(digits) == 10:
        digits = "91" + digits
    return digits


def _default_feedback_message(tenant_config: dict, lead: dict, outcome: str, call_summary: str = "") -> str:
    vars_map = _build_vars(tenant_config, lead, call_summary)
    name = vars_map["name"] or "there"
    agent = vars_map["agent"]
    company = vars_map["tenant_name"] or "our team"
    summary = (call_summary or "").strip()
    if outcome == "demo_booked":
        body = (
            f"Hi {name}! Your demo request with {company} is noted. "
            f"{agent} will follow up with schedule details soon."
        )
    elif outcome == "interested":
        body = (
            f"Hi {name}! Thanks for your interest in {company}. "
            f"{agent} will share the next details shortly. Reply here anytime."
        )
    else:
        body = (
            f"Hi {name}! Thanks for speaking with {agent} from {company}."
        )
    if summary and summary.lower() not in (
        "call completed. analysis unavailable.",
        "call completed",
        "n/a",
    ):
        body += f"\n\nSummary: {summary[:280]}"
    body += "\n\nReply to this chat if you have any questions."
    return body


# ── WhatsApp via OpenClaw (per-tenant profile) ─────────────────
async def send_whatsapp_openclaw(phone: str, message: str, tenant_config: dict,
                                 tenant_id: int | None = None) -> bool:
    try:
        from app.openclaw_service import send_whatsapp_openclaw as _oc_send
        return await _oc_send(phone, message, tenant_config, tenant_id=tenant_id)
    except Exception as e:
        logger.error(f"[WhatsApp/OpenClaw] Error: {e}")
        return False


# ── WhatsApp via TeleCMI (legacy per-tenant credentials) ───────
async def send_whatsapp_telecmi(phone: str, message: str, tenant_config: dict) -> bool:
    api_key = tenant_config.get("whatsapp_api_key") or ""
    wa_num  = tenant_config.get("whatsapp_number") or ""
    if not api_key or not wa_num:
        logger.warning("[WhatsApp/TeleCMI] Credentials not configured")
        return False
    try:
        import httpx
        phone = _normalize_phone(phone)
        if not phone:
            return False

        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(
                "https://rest.telecmi.com/v2/send_whatsapp",
                json={
                    "appid":   api_key,
                    "secret":  tenant_config.get("whatsapp_secret", ""),
                    "to":      phone,
                    "from":    re.sub(r"\D", "", str(wa_num)),
                    "message": message,
                }
            )
            if resp.status_code == 200:
                logger.info(f"[WhatsApp/TeleCMI] Sent to {phone}")
                return True
            logger.error(f"[WhatsApp/TeleCMI] Failed: {resp.text}")
            return False
    except Exception as e:
        logger.error(f"[WhatsApp/TeleCMI] Error: {e}")
        return False


async def send_whatsapp(phone: str, message: str, tenant_config: dict,
                        tenant_id: int | None = None) -> bool:
    """
    Send WhatsApp using the tenant's configured provider.
    Default provider is OpenClaw (tenant-linked). TeleCMI only if explicitly set
    or OpenClaw is unavailable and TeleCMI creds exist for THIS tenant.
    """
    provider = (tenant_config.get("whatsapp_provider") or "openclaw").strip().lower()
    openclaw_ready = bool(tenant_config.get("openclaw_enabled")) and bool(
        (tenant_config.get("openclaw_profile") or "").strip()
    )
    telecmi_ready = bool(tenant_config.get("whatsapp_api_key")) and bool(
        tenant_config.get("whatsapp_number")
    )

    if provider == "telecmi":
        if telecmi_ready:
            return await send_whatsapp_telecmi(phone, message, tenant_config)
        if openclaw_ready:
            return await send_whatsapp_openclaw(phone, message, tenant_config, tenant_id=tenant_id)
        logger.warning("[WhatsApp] No provider credentials for tenant")
        return False

    # default: openclaw first
    if openclaw_ready:
        ok = await send_whatsapp_openclaw(phone, message, tenant_config, tenant_id=tenant_id)
        if ok:
            return True
        if telecmi_ready:
            logger.warning("[WhatsApp] OpenClaw failed; trying tenant TeleCMI credentials")
            return await send_whatsapp_telecmi(phone, message, tenant_config)
        return False

    if telecmi_ready:
        return await send_whatsapp_telecmi(phone, message, tenant_config)

    logger.warning(
        "[WhatsApp] Not configured for this tenant. "
        "Link OpenClaw WhatsApp under Account → Notifications."
    )
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
        phone = _normalize_phone(phone)
        if not phone:
            return False

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


def _effective_outcomes(outcome: str) -> list[str]:
    """Map runtime sentiments to trigger keys, with safe fallbacks."""
    o = (outcome or "").strip().lower() or "answered"
    aliases = {
        "demo": "demo_booked",
        "booked": "demo_booked",
        "positive": "interested",
        "hot": "interested",
        "callback": "interested",
        "completed": "answered",
        "success": "answered",
        "neutral": "answered",
        "unknown": "answered",
    }
    o = aliases.get(o, o)
    # Always allow exact + 'any'. For answered-like outcomes also try 'answered'.
    out = [o]
    if o not in ("answered", "any"):
        # interested/demo still may want answered thank-you only if configured;
        # keep exact first.
        pass
    if o in ("neutral",):
        out.append("answered")
    return list(dict.fromkeys(out))


# ── Main trigger executor ──────────────────────────────────────
async def execute_triggers(
    tenant_id: int,
    outcome: str,
    lead: dict,
    call_summary: str = "",
    customer_phone: str = "",
) -> None:
    """
    Run all active triggers for this outcome concurrently.
    Called from _post_call() after every call.

    customer_phone: optional fallback when lead record is missing (common on
    inbound / quick test calls).
    """
    tenant_config = tdb.get_tenant_config(tenant_id) or {}
    tenant_config = {**tenant_config, "tenant_id": tenant_id}

    # Master switches — check before anything
    wa_enabled    = bool(int(tenant_config.get("whatsapp_enabled") or 0))
    sms_enabled   = bool(int(tenant_config.get("sms_enabled") or 0))
    email_enabled = bool(int(tenant_config.get("email_enabled") if tenant_config.get("email_enabled") is not None else 1))
    feedback_on_answered = bool(int(
        tenant_config.get("whatsapp_feedback_on_answered")
        if tenant_config.get("whatsapp_feedback_on_answered") is not None else 1
    ))

    if not any([wa_enabled, sms_enabled, email_enabled]):
        logger.info(f"[Triggers] All channels disabled for tenant={tenant_id}")
        return

    lead = dict(lead or {})
    phone = lead.get("phone") or customer_phone or ""
    if phone and not lead.get("phone"):
        lead["phone"] = phone
    if not lead.get("name") and lead.get("lead_name"):
        lead["name"] = lead.get("lead_name")

    outcomes = _effective_outcomes(outcome)
    triggers = []
    for o in outcomes:
        triggers.extend(tdb.get_active_triggers(tenant_id, o))

    # De-dupe by trigger id
    seen = set()
    uniq = []
    for t in triggers:
        tid = t.get("id")
        if tid in seen:
            continue
        seen.add(tid)
        uniq.append(t)
    triggers = uniq

    vars_map = _build_vars(tenant_config, lead, call_summary)
    tasks = []
    fired = 0

    for t in triggers:
        message = _render(t.get("body") or "", vars_map)
        channel = (t.get("channel") or t.get("msg_channel") or "").lower()
        subject = _render(
            t.get("subject") or f"Message from {vars_map['tenant_name'] or 'our team'}",
            vars_map,
        )
        email = lead.get("email", "")

        if channel in ("whatsapp", "all") and wa_enabled and phone:
            tasks.append(send_whatsapp(phone, message, tenant_config, tenant_id=tenant_id))
            fired += 1
        if channel in ("sms", "all") and sms_enabled and phone:
            tasks.append(send_sms(phone, message, tenant_config))
            fired += 1
        if channel in ("email", "all") and email_enabled and email:
            tasks.append(send_email(email, subject, message, tenant_config))
            fired += 1

        logger.info(
            f"[Triggers] Firing: outcome={outcome} matched={t.get('trigger_on')} "
            f"channel={channel} tenant={tenant_id}"
        )

    # Fallback: if WhatsApp is enabled, call was answered-like, no template fired,
    # still send a basic feedback SMS-less WhatsApp so portal toggle "just works".
    answered_like = (outcomes[0] if outcomes else "") in (
        "answered", "interested", "demo_booked", "neutral", "callback"
    ) or (outcome or "").lower() in ("answered", "interested", "demo_booked", "neutral")
    if wa_enabled and phone and feedback_on_answered and answered_like and fired == 0:
        msg = _default_feedback_message(tenant_config, lead, outcomes[0] if outcomes else outcome, call_summary)
        logger.info(f"[Triggers] Fallback WhatsApp feedback tenant={tenant_id} outcome={outcome}")
        tasks.append(send_whatsapp(phone, msg, tenant_config, tenant_id=tenant_id))

    if not phone and wa_enabled:
        logger.warning(f"[Triggers] WhatsApp enabled but no phone on lead/call tenant={tenant_id}")

    if tasks:
        results = await asyncio.gather(*tasks, return_exceptions=True)
        errors  = [r for r in results if isinstance(r, Exception)]
        failed  = [r for r in results if r is False]
        if errors:
            logger.warning(f"[Triggers] {len(errors)} send errors: {errors}")
        if failed:
            logger.warning(f"[Triggers] {len(failed)} sends returned False tenant={tenant_id}")
        logger.info(
            f"[Triggers] Done tenant={tenant_id} outcome={outcome} "
            f"tasks={len(tasks)} ok={sum(1 for r in results if r is True)}"
        )
    else:
        logger.info(f"[Triggers] Nothing to send tenant={tenant_id} outcome={outcome}")
