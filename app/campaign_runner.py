"""
app/campaign_runner.py
Background campaign execution engine.
Reads leads assigned to a campaign, fires outbound calls one by one
with a configurable delay, and updates progress in real time.
"""

import asyncio
import os
from loguru import logger
from dotenv import load_dotenv

from app import database as db

load_dotenv()


async def make_single_call(phone: str):
    """
    Fire one outbound call via PIOPIY.
    Returns {"phone": ..., "call_id": ...} on success, None on failure.
    """
    from app.piopiy_handler import make_outbound_call

    try:
        call_id = await asyncio.to_thread(make_outbound_call, phone)
        logger.info(f"✅ PIOPIY call initiated to {phone} — ID: {call_id}")
        return {"phone": phone, "call_id": call_id}
    except Exception as e:
        logger.error(f"PIOPIY call failed for {phone}: {e}")
        return None


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

        result = await make_single_call(phone)

        if result:
            called += 1
            call_id          = result.get("call_id")
            normalized_phone = result.get("phone", phone)

            # Create DB call record so stats/dashboard update immediately
            db.create_call(
                phone       = normalized_phone,
                lead_name   = name,
                company     = lead.get("company", ""),
                lead_id     = lead_id,
                campaign_id = campaign_id,
                call_sid    = call_id,
            )

            db.update_lead(lead_id, status='called')
            db.increment_campaign_calls(campaign_id, answered=False)
            db.add_log(f"✅ Call initiated — {name} ({normalized_phone}) ID:{call_id}")
        else:
            skipped += 1
            db.update_lead(lead_id, status='called')
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
