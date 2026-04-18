"""

app/campaign_runner.py

Background campaign execution engine.

Reads leads assigned to a campaign, fires outbound calls one by one

with a configurable delay, and updates progress in real time.


Telephony: uses whichever provider is currently active (PIOPIY or Exotel).

"""


import asyncio

import os
import uuid

from loguru import logger

from dotenv import load_dotenv


from app import database as db
from app import tenant_db as tdb


load_dotenv()


def _calls_dry_run_enabled() -> bool:

    return os.getenv("AI_CALLER_DRY_RUN", "").strip().lower() in {
        "1", "true", "yes", "on"
    }



async def make_single_call(phone: str, lead_id: str = None, metadata: dict = None) -> dict:

    """

    Fire one outbound call via the currently active telephony provider.

    Returns {"phone": ..., "call_id": ...} on success, None on failure.


    Telephony dispatch:

      PIOPIY  → app.piopiy_handler.make_outbound_call  (sync, runs in thread)

      Exotel  → app.exotel_handler.make_outbound_call  (async)

    """

    # Determine active provider

    provider = "piopiy"

    try:

        val = db.get_config("telephony_provider")

        if val in ("piopiy", "exotel"):

            provider = val

    except Exception:

        provider = os.getenv("TELEPHONY_PROVIDER", "piopiy").lower()

        if provider not in ("piopiy", "exotel"):

            provider = "piopiy"


    # Normalize phone

    digits = "".join(c for c in str(phone) if c.isdigit())

    if "e" in str(phone).lower():

        try:

            digits = str(int(float(phone)))

        except Exception:

            pass

    if len(digits) == 10:

        normalized = "91" + digits

    elif len(digits) == 11 and digits.startswith("0"):

        normalized = "91" + digits[1:]

    elif len(digits) == 12 and digits.startswith("91"):

        normalized = digits

    else:

        normalized = digits


    logger.info(f"[{provider.upper()}] Outbound call → +{normalized}")


    if _calls_dry_run_enabled():

        call_id = f"dryrun-{provider}-{uuid.uuid4().hex[:10]}"

        logger.warning(
            f"[DRY RUN] Skipping real {provider.upper()} dial → +{normalized} | ID: {call_id}"
        )

        return {
            "phone": normalized,
            "call_id": call_id,
            "provider": "dryrun",
            "target_provider": provider,
            "dry_run": True,
        }


    try:

        if provider == "piopiy":

            from app.piopiy_handler import make_outbound_call as _piopiy_call

            call_id = await asyncio.to_thread(

                _piopiy_call, normalized, lead_id, metadata

            )

        else:

            from app.exotel_handler import make_outbound_call as _exotel_call

            call_id = await _exotel_call(normalized, lead_id)


        logger.info(f"✅ [{provider.upper()}] Call initiated → +{normalized} | ID: {call_id}")

        return {"phone": normalized, "call_id": str(call_id), "provider": provider}


    except Exception as e:

        db.add_log(f"❌ [{provider.upper()}] Call failed → +{normalized} | ERROR: {str(e)}")

        logger.error(f"❌ [{provider.upper()}] Call failed → +{normalized} | {e}", exc_info=True)

        return None



async def run_campaign(campaign_id: int, delay_seconds: int = 60):

    """

    Background task: iterate through all leads in a campaign,

    fire outbound calls one by one with delay_seconds between each.

    Respects pause/stop by checking DB status before each call.

    """

    logger.info(f"Campaign {campaign_id} executor started — delay={delay_seconds}s")


    c         = db.get_campaign(campaign_id)

    camp_name = c["name"] if c else f"Campaign {campaign_id}"

    tenant_id = c.get("tenant_id", 1) if c else 1


    # Get all leads — skip only demo_booked

    all_leads = db.get_leads(campaign_id=campaign_id, limit=500)

    to_call   = [l for l in all_leads if l.get("status") not in ("demo_booked",)]


    if not to_call:

        logger.info(f"Campaign {campaign_id}: no leads to call")

        db.add_log(f"⚠️ Campaign '{camp_name}': no leads to call (all completed or demo booked)")

        db.update_campaign_status(campaign_id, "completed")

        return


    total = len(to_call)

    db.add_log(f"📞 Campaign '{camp_name}' dialing {total} leads — {delay_seconds}s delay")


    called  = 0

    skipped = 0


    for i, lead in enumerate(to_call):

        # ── Quota gate (checked per call, not just once) ──
        try:
            quota = tdb.check_quota(tenant_id)
            if not quota["allowed"]:
                logger.warning(
                    f"Campaign {campaign_id}: quota exceeded for tenant {tenant_id} "
                    f"— {quota.get('reason', '')} — pausing campaign"
                )
                db.add_log(
                    f"⛔ Campaign '{camp_name}' paused: {quota.get('reason', 'quota exceeded')}"
                )
                db.update_campaign_status(campaign_id, "paused")
                return
        except Exception as quota_err:
            logger.error(f"Quota check error (failing open): {quota_err}")

        # Pause check

        current = db.get_campaign(campaign_id)

        if not current or current["status"] != "running":

            logger.info(f"Campaign {campaign_id} paused/stopped after {called} calls")

            db.add_log(f"⏸️ Campaign '{camp_name}' paused after {called}/{total} calls")

            return


        phone   = lead.get("phone", "").strip()

        name    = lead.get("name", "Lead")

        lead_id = lead.get("id")


        if not phone:

            logger.warning(f"Lead {lead_id} has no phone — skipping")

            skipped += 1

            continue


        logger.info(f"Campaign {campaign_id} [{i+1}/{total}] calling {name} ({phone})")

        db.add_log(f"📞 [{i+1}/{total}] Dialing {name} — {phone}")

        result = await make_single_call(

            phone,

            lead_id=str(lead_id) if lead_id else None,

            metadata={"customer_name": name, "company": lead.get("company", ""), "tenant_id": str(tenant_id)},

        )


        if result:

            called += 1

            call_id          = result.get("call_id")

            normalized_phone = result.get("phone", phone)

            provider         = result.get("provider", "piopiy")


            db.create_call(

                phone       = normalized_phone,

                lead_name   = name,

                company     = lead.get("company", ""),

                lead_id     = lead_id,

                campaign_id = campaign_id,

                call_sid    = call_id,

            )

            db.update_lead(lead_id, status="called")

            # ── Schedule retry if campaign has max_retries set ──
            try:
                campaign_fresh = db.get_campaign(campaign_id)
                max_retries    = campaign_fresh.get("max_retries", 0) if campaign_fresh else 0
                if max_retries and max_retries > 0:
                    from datetime import datetime as _dt, timedelta as _td
                    next_retry = (
                        _dt.utcnow() + _td(minutes=30)
                    ).strftime("%Y-%m-%d %H:%M:%S")
                    with db.get_conn() as conn:
                        conn.execute(
                            """UPDATE leads
                               SET retry_count = 1,
                                   next_retry_at = ?,
                                   status = 'no_answer'
                               WHERE id = ?
                                 AND (retry_count IS NULL OR retry_count = 0)""",
                            (next_retry, lead_id)
                        )
                        conn.commit()
            except Exception as retry_err:
                logger.warning(f"Retry scheduling error for lead {lead_id}: {retry_err}")

            db.increment_campaign_calls(campaign_id, answered=False)

            db.add_log(f"✅ [{provider.upper()}] {name} ({normalized_phone}) ID:{call_id}")

        else:

            skipped += 1

            db.set_lead_retry(lead_id, retry_count_floor=1, gap_minutes=30)

            db.add_log(f"❌ Call failed/DND — {name} ({phone}) | scheduled for retry in 30 min")


        # Respect delay between calls (check pause every second)

        if i < total - 1:

            for _ in range(delay_seconds):

                await asyncio.sleep(1)

                check = db.get_campaign(campaign_id)

                if not check or check["status"] != "running":

                    db.add_log(f"⏸️ Campaign '{camp_name}' paused during delay")

                    return


    db.update_campaign_status(campaign_id, "completed")

    db.add_log(

        f"🏁 Campaign '{camp_name}' completed — "

        f"{called} calls made, {skipped} skipped out of {total} leads"

    )

    logger.info(f"Campaign {campaign_id} completed: {called} called, {skipped} skipped")
