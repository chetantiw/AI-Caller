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


def _clean_summary(summary: str) -> str:
    s = (summary or "").strip()
    if not s:
        return ""
    if s.lower() in (
        "call completed. analysis unavailable.",
        "call completed",
        "n/a",
        "none",
        "null",
    ):
        return ""
    return s


def _extract_demo_schedule(summary: str) -> dict:
    """
    Pull demo day/time from call summary when the agent booked or discussed a demo.
    Returns keys: demo_when (human text), has_schedule (bool).
    """
    text = _clean_summary(summary)
    if not text:
        return {"demo_when": "", "has_schedule": False}

    # Common patterns from transcripts/summaries
    patterns = [
        # Monday at 2 PM / Monday 2PM / on Monday at 14:00
        r"(?:on\s+)?((?:mon|tues|wednes|thurs|fri|satur|sun)day)\s*(?:at\s*)?(\d{1,2}(?::\d{2})?\s*(?:am|pm|a\.m\.|p\.m\.)?)",
        # 2 PM on Monday
        r"(\d{1,2}(?::\d{2})?\s*(?:am|pm|a\.m\.|p\.m\.))\s+on\s+((?:mon|tues|wednes|thurs|fri|satur|sun)day)",
        # 20 Sept / September 20 at 2 PM
        r"((?:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\.?\s+\d{1,2}(?:st|nd|rd|th)?(?:\s*,?\s*\d{4})?)\s*(?:at\s*)?(\d{1,2}(?::\d{2})?\s*(?:am|pm|a\.m\.|p\.m\.)?)",
        r"(\d{1,2}(?:st|nd|rd|th)?\s+(?:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*(?:\s+\d{4})?)\s*(?:at\s*)?(\d{1,2}(?::\d{2})?\s*(?:am|pm|a\.m\.|p\.m\.)?)",
        # demo scheduled for ...
        r"demo(?:\s+is)?\s+(?:scheduled|booked|fixed|set)\s+(?:for|on)\s+([^\.\n,]{5,60})",
        r"(?:schedule|booked|confirmed).*?demo.*?(?:for|on|at)\s+([^\.\n,]{5,60})",
    ]
    low = text.lower()
    for pat in patterns:
        m = re.search(pat, low, flags=re.I)
        if not m:
            continue
        parts = [p.strip(" .,:;") for p in m.groups() if p and str(p).strip()]
        if not parts:
            continue
        # Prefer original casing slice when possible
        start, end = m.span()
        raw = text[start:end].strip(" .,:;")
        demo_when = raw if len(raw) >= 4 else " ".join(parts)
        # Normalize whitespace
        demo_when = re.sub(r"\s+", " ", demo_when)
        if len(demo_when) >= 4:
            return {"demo_when": demo_when, "has_schedule": True}
    return {"demo_when": "", "has_schedule": False}


def _offer_line(tenant_config: dict) -> str:
    """Short product offer line — keep WhatsApp concise."""
    products = (tenant_config.get("company_products") or "").strip()
    industry = (tenant_config.get("company_industry") or "").strip().lower()
    company = (tenant_config.get("company_name") or "").strip() or "our team"
    company_l = company.lower()

    # Prefer a clean one-liner for MuTech / IoT-robotics tenants
    if "mutech" in company_l or "iot" in industry or "robot" in industry:
        return "We are happy to offer you our IoT and robotics solutions."

    if products:
        # Turn bullets/newlines into a short phrase; avoid dumping full catalog
        cleaned = products.replace("\r", "\n")
        parts = []
        for line in cleaned.split("\n"):
            line = re.sub(r"^[\-\*\u2022\d\.\)\s]+", "", line).strip(" -•\t")
            if line:
                parts.append(line)
        if not parts:
            parts = [re.sub(r"\s+", " ", products).strip()]
        # Max 3 items for WhatsApp readability
        short_items = parts[:3]
        short = ", ".join(short_items)
        if len(parts) > 3:
            short += ", and more"
        if len(short) > 120:
            short = short[:117].rstrip() + "..."
        return f"We are happy to offer you our {short}."

    if industry:
        return f"We are happy to offer you our {industry} solutions."
    return "We are happy to offer you our solutions."


def _build_vars(tenant_config: dict, lead: dict, call_summary: str = "",
                outcome: str = "") -> dict:
    """Build variable dict for template rendering."""
    now = datetime.now()
    summary = _clean_summary(call_summary)
    demo = _extract_demo_schedule(summary)
    company = (tenant_config.get("company_name") or "").strip() or "our team"
    company = company.rstrip(" .")  # avoid "Ltd.."
    name = (lead.get("name") or lead.get("lead_name") or "").strip()
    greeting_name = f" {name}" if name else ""

    if demo["has_schedule"]:
        demo_block = (
            f"Your demo is noted for *{demo['demo_when']}*. "
            "Reply here if you need to reschedule."
        )
        demo_or_followup = demo_block
    else:
        demo_block = (
            "If you would like a demo, please share your preferred *date and time* here."
        )
        demo_or_followup = (
            "Please share a convenient *date and time* for a demo or follow-up call."
        )

    followup_ask = (
        "Also let us know a good time for a follow-up call if you prefer a call back."
    )

    return {
        "name":            name,
        "greeting_name":   greeting_name,
        "phone":           lead.get("phone") or "",
        "email":           lead.get("email") or "",
        "company":         lead.get("company") or "",
        "agent":           tenant_config.get("agent_name") or "Aira",
        "tenant_name":     company,
        "date":            now.strftime("%d %B %Y"),
        "time":            now.strftime("%I:%M %p"),
        "summary":         summary,
        "website":         tenant_config.get("company_website") or "",
        "offer_line":      _offer_line(tenant_config),
        "demo_when":       demo["demo_when"],
        "demo_block":      demo_block,
        "demo_or_followup": demo_or_followup,
        "followup_ask":    followup_ask,
        "outcome":         (outcome or "").strip().lower(),
    }


def _normalize_phone(phone: str) -> str:
    digits = re.sub(r"\D", "", str(phone or ""))
    if not digits:
        return ""
    if len(digits) == 10:
        digits = "91" + digits
    return digits


# Canonical WhatsApp copy (also seeded into message_templates)
TEMPLATE_ANSWERED = (
    "Hi{greeting_name}! Thank you for reaching out to {tenant_name}. "
    "{offer_line}\n\n"
    "{demo_or_followup}\n"
    "{followup_ask}\n\n"
    "Reply on this chat anytime — we are happy to help."
)

TEMPLATE_INTERESTED = (
    "Hi{greeting_name}! Thank you for reaching out to {tenant_name}. "
    "{offer_line}\n\n"
    "Glad to know you are interested. {demo_or_followup}\n"
    "{followup_ask}\n\n"
    "Reply here and our team will assist you."
)

TEMPLATE_DEMO = (
    "Hi{greeting_name}! Thank you for reaching out to {tenant_name}. "
    "{offer_line}\n\n"
    "{demo_block}\n"
    "{followup_ask}\n\n"
    "Reply on this WhatsApp for any change in schedule or questions."
)


def _default_feedback_message(tenant_config: dict, lead: dict, outcome: str,
                              call_summary: str = "") -> str:
    o = (outcome or "answered").strip().lower()
    vars_map = _build_vars(tenant_config, lead, call_summary, outcome=o)
    if o == "demo_booked":
        body = _render(TEMPLATE_DEMO, vars_map)
    elif o == "interested":
        body = _render(TEMPLATE_INTERESTED, vars_map)
    else:
        body = _render(TEMPLATE_ANSWERED, vars_map)
    return re.sub(r"\n{3,}", "\n\n", body).strip()


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

    primary_outcome = outcomes[0] if outcomes else (outcome or "answered")
    # If summary clearly has a demo booking, prefer demo copy even when
    # sentiment came back as neutral/answered.
    summary_l = _clean_summary(call_summary).lower()
    if primary_outcome in ("answered", "neutral", "interested") and (
        "demo" in summary_l and any(
            k in summary_l for k in ("book", "schedule", "monday", "tuesday", "wednesday",
                                     "thursday", "friday", "saturday", "sunday", "pm", "am")
        )
    ):
        primary_outcome = "demo_booked"

    vars_map = _build_vars(tenant_config, lead, call_summary, outcome=primary_outcome)
    tasks = []
    fired = 0

    for t in triggers:
        # Prefer smart canonical WhatsApp copy for known outcomes so portal
        # templates stay in sync with product messaging.
        trig_on = (t.get("trigger_on") or "").lower()
        channel = (t.get("channel") or t.get("msg_channel") or "").lower()
        if channel in ("whatsapp", "all"):
            if trig_on == "demo_booked" or primary_outcome == "demo_booked":
                message = _render(TEMPLATE_DEMO, vars_map)
            elif trig_on == "interested":
                message = _render(TEMPLATE_INTERESTED, vars_map)
            elif trig_on in ("answered", "any", "neutral"):
                message = _render(TEMPLATE_ANSWERED, vars_map)
            else:
                message = _render(t.get("body") or "", vars_map)
        else:
            message = _render(t.get("body") or "", vars_map)

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
    # still send feedback so portal toggle "just works".
    answered_like = (outcomes[0] if outcomes else "") in (
        "answered", "interested", "demo_booked", "neutral", "callback"
    ) or (outcome or "").lower() in ("answered", "interested", "demo_booked", "neutral")
    if wa_enabled and phone and feedback_on_answered and answered_like and fired == 0:
        msg = _default_feedback_message(
            tenant_config, lead, primary_outcome, call_summary
        )
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
