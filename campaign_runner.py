"""
app/campaign_runner.py
Background campaign execution engine.
Reads leads assigned to a campaign, fires outbound calls one by one
with a configurable delay, and updates progress in real time.
"""

import asyncio
import os
import subprocess
from loguru import logger
from dotenv import load_dotenv

from app import database as db

load_dotenv()


async def make_single_call(phone: str) -> bool:
    """
    Fire one outbound call via Exotel using subprocess curl.
    Returns True if call was initiated successfully.
    """
    api_key   = os.getenv("EXOTEL_API_KEY", "")
    api_token = os.getenv("EXOTEL_API_TOKEN", "")
    sid       = os.getenv("EXOTEL_ACCOUNT_SID", "mutechautomation1")
    subdomain = os.getenv("EXOTEL_SUBDOMAIN", "api.exotel.com")
    caller_id = "+917314854688"  # hardcoded working CallerID

    if not api_key or not api_token:
        logger.error("Exotel credentials missing — cannot make outbound call")
        return False

    # ── Normalize phone number ──────────────────────────────
    phone = str(phone).strip()
    try:
        if 'E' in phone.upper():
            phone = str(int(float(phone)))
    except Exception:
        pass

    import re
    phone_digits = re.sub(r'[^\d]', '', phone)

    if len(phone_digits) == 10:
        phone = "+91" + phone_digits
    elif len(phone_digits) == 12 and phone_digits.startswith("91"):
        phone = "+" + phone_digits
    elif len(phone_digits) == 11 and phone_digits.startswith("0"):
        phone = "+91" + phone_digits[1:]
    elif len(phone_digits) >= 11:
        phone = "+" + phone_digits
    else:
        logger.error(f"Cannot normalize phone number: {phone} (digits: {phone_digits})")
        return False

    logger.info(f"Normalized phone: {phone_digits} → {phone}")

    # Exotel outbound call API:
    # - From    = number to call (customer)
    # - CallerId = your Exotel landline (shows as caller ID)
    # - Url     = ExoML app URL that handles the call flow
    # NOTE: Url uses + as space in flow name — must NOT be percent-encoded
    app_url = f"http://my.exotel.com/{sid}/exoml/start/{sid}+Landing+Flow"

    cmd = [
        "curl", "-s", "-X", "POST",
        f"https://{api_key}:{api_token}@{subdomain}/v1/Accounts/{sid}/Calls/connect",
        "-F", f"From={phone}",
        "-F", f"CallerId={caller_id}",
        "-F", f"Url={app_url}",
        "-F", "StatusCallback=",
    ]

    # Log the exact command for debugging (mask credentials)
    safe_cmd = ' '.join(cmd).replace(api_key, 'KEY').replace(api_token, 'TOKEN')
    logger.info(f"Curl cmd: {safe_cmd}")

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
        response_text = result.stdout or ""
        logger.info(f"Exotel response for {phone}: {response_text[:300]}")

        if result.returncode == 0 and ('"Sid"' in response_text or '"sid"' in response_text.lower()):
            return True
        elif "403" in response_text or "DND" in response_text.upper() or "NDNC" in response_text.upper():
            logger.warning(f"DND/NDNC blocked: {phone}")
            return False
        else:
            logger.warning(f"Exotel unexpected response for {phone}: {response_text[:300]}")
            return False
    except subprocess.TimeoutExpired:
        logger.error(f"Exotel call timeout for {phone}")
        return False
    except Exception as e:
        logger.error(f"Exotel call error for {phone}: {e}")
        return False


async def run_campaign(campaign_id: int, delay_seconds: int = 60):
    """
    Background task: iterate through all leads in a campaign,
    fire outbound calls one by one with delay_seconds between each call.
    Respects pause/stop state by checking DB status before each call.
    """
    logger.info(f"Campaign {campaign_id} executor started — delay={delay_seconds}s")

    c = db.get_campaign(campaign_id)
    camp_name = c['name'] if c else f"Campaign {campaign_id}"

    # Get all leads assigned to this campaign
    # Runner calls ALL leads assigned to the campaign regardless of status
    # (status filtering happens at assignment time, not call time)
    all_leads = db.get_leads(campaign_id=campaign_id, limit=500)

    # Only skip leads that already have a positive outcome — don't re-call demo_booked
    to_call = [l for l in all_leads if l.get('status') not in ('demo_booked',)]

    if not to_call:
        logger.info(f"Campaign {campaign_id}: no leads to call")
        db.add_log(f"⚠️ Campaign '{camp_name}': no leads to call (all completed or demo booked)")
        db.update_campaign_status(campaign_id, "completed")
        return
    total = len(to_call)

    db.add_log(f"📞 Campaign '{camp_name}' dialing {total} leads with {delay_seconds}s delay")
    logger.info(f"Campaign {campaign_id}: {total} leads to call")

    called  = 0
    skipped = 0

    for i, lead in enumerate(to_call):
        # Check if campaign was paused or stopped
        current = db.get_campaign(campaign_id)
        if not current or current['status'] != 'running':
            logger.info(f"Campaign {campaign_id} paused/stopped after {called} calls")
            db.add_log(f"⏸️ Campaign '{camp_name}' paused after {called}/{total} calls")
            return

        phone = lead.get('phone', '').strip()
        name  = lead.get('name', 'Lead')
        lead_id = lead.get('id')

        if not phone:
            logger.warning(f"Lead {lead_id} has no phone — skipping")
            skipped += 1
            continue

        logger.info(f"Campaign {campaign_id} [{i+1}/{total}] calling {name} ({phone})")
        db.add_log(f"📞 [{i+1}/{total}] Dialing {name} — {phone}")

        success = await make_single_call(phone)

        if success:
            called += 1
            # Update lead status to 'called' immediately
            db.update_lead(lead_id, status='called')
            # Increment campaign calls_made counter
            db.increment_campaign_calls(campaign_id, answered=False)
            db.add_log(f"✅ Call initiated — {name} ({phone})")
        else:
            skipped += 1
            db.update_lead(lead_id, status='called')  # mark as attempted
            db.add_log(f"❌ Call failed/DND — {name} ({phone})")

        # Wait before next call (unless this is the last lead)
        if i < total - 1:
            logger.info(f"Waiting {delay_seconds}s before next call…")
            # Check status every second during the wait so pause works instantly
            for _ in range(delay_seconds):
                await asyncio.sleep(1)
                check = db.get_campaign(campaign_id)
                if not check or check['status'] != 'running':
                    logger.info(f"Campaign {campaign_id} paused during delay")
                    db.add_log(f"⏸️ Campaign '{camp_name}' paused")
                    return

    # Campaign finished
    db.update_campaign_status(campaign_id, "completed")
    db.add_log(
        f"🏁 Campaign '{camp_name}' completed — "
        f"{called} calls made, {skipped} skipped out of {total} leads"
    )
    logger.info(f"Campaign {campaign_id} completed: {called} called, {skipped} skipped")
