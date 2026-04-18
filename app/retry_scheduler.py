"""
app/retry_scheduler.py

Smart Retry Scheduler — fires missed / no-answer calls after a cooling-off period.

Logic:
  Every 5 minutes:
  1. Find leads where:
       retry_count > 0 AND retry_count < max_retries (per campaign setting)
       status IN ('called', 'no_answer')
       next_retry_at <= now
       campaign is NOT paused/completed/draft
  2. Check tenant quota before each retry
  3. Check tenant plan has smart_retry = True
  4. Fire make_single_call()
  5. Increment retry_count, set next_retry_at = now + (retry_count * 30 min)
  6. After max retries: set status = 'exhausted'

Wire-up in app/main.py startup_event:
    from app.retry_scheduler import start_retry_scheduler
    asyncio.create_task(start_retry_scheduler())
"""

import asyncio
import os
from datetime import datetime, timedelta

from loguru import logger
from dotenv import load_dotenv

from app import database as db
from app import tenant_db as tdb

load_dotenv()

os.makedirs("logs", exist_ok=True)
logger.add(
    "logs/retry_scheduler.log",
    rotation="50 MB",
    level="INFO",
    retention="7 days",
)

# Default retry config — overridden per campaign if campaign stores max_retries
DEFAULT_MAX_RETRIES   = 2
RETRY_INTERVAL_MIN    = 30   # minutes between retries (multiplied by attempt number)
CHECK_INTERVAL_SEC    = 300  # check every 5 minutes

_running = False


def _get_retryable_leads() -> list:
    """
    Query leads that are due for a retry right now.
    Returns list of dicts with lead + campaign + tenant info joined.
    """
    now_str = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
    with db.get_conn() as conn:
        rows = conn.execute("""
            SELECT
                l.id            AS lead_id,
                l.name          AS lead_name,
                l.phone,
                l.company,
                l.retry_count,
                l.next_retry_at,
                l.campaign_id,
                l.tenant_id     AS lead_tenant_id,
                l.language,
                c.name          AS campaign_name,
                c.status        AS campaign_status,
                c.tenant_id     AS campaign_tenant_id,
                COALESCE(c.max_retries, ?) AS max_retries
            FROM leads l
            JOIN campaigns c ON c.id = l.campaign_id
            WHERE
                l.retry_count   > 0
                AND l.status    IN ('called', 'no_answer')
                AND l.next_retry_at IS NOT NULL
                AND l.next_retry_at <= ?
                AND c.status    NOT IN ('paused', 'completed', 'draft')
        """, (DEFAULT_MAX_RETRIES, now_str)).fetchall()
    return [dict(r) for r in rows]


def _mark_exhausted(lead_id: int):
    with db.get_conn() as conn:
        conn.execute(
            "UPDATE leads SET status='exhausted', retry_count=retry_count+1 WHERE id=?",
            (lead_id,)
        )
        conn.commit()


def _schedule_next_retry(lead_id: int, new_retry_count: int):
    """Set next_retry_at = now + (new_retry_count * RETRY_INTERVAL_MIN) minutes."""
    next_at = (
        datetime.utcnow() + timedelta(minutes=RETRY_INTERVAL_MIN * new_retry_count)
    ).strftime("%Y-%m-%d %H:%M:%S")
    with db.get_conn() as conn:
        conn.execute(
            "UPDATE leads SET retry_count=?, next_retry_at=?, status='called' WHERE id=?",
            (new_retry_count, next_at, lead_id)
        )
        conn.commit()


async def _process_retries():
    """Main retry loop — called every CHECK_INTERVAL_SEC seconds."""
    leads = _get_retryable_leads()
    if not leads:
        return

    logger.info(f"[RetryScheduler] {len(leads)} leads due for retry")

    from app.campaign_runner import make_single_call
    from app.plan_features   import check_feature, get_plan_features

    for lead in leads:
        lead_id     = lead["lead_id"]
        tenant_id   = lead["campaign_tenant_id"] or lead["lead_tenant_id"] or 1
        retry_count = lead["retry_count"]
        max_retries = lead["max_retries"] or DEFAULT_MAX_RETRIES

        try:
            # ── 1. Plan gate ──────────────────────────────────
            tenant = tdb.get_tenant(tenant_id)
            plan   = (tenant or {}).get("plan", "starter")
            gate   = check_feature(plan, "smart_retry")
            if not gate["allowed"]:
                logger.debug(
                    f"[RetryScheduler] Tenant {tenant_id} plan={plan} "
                    f"does not have smart_retry — skipping lead {lead_id}"
                )
                continue

            # ── 2. Quota gate ─────────────────────────────────
            quota = tdb.check_quota(tenant_id)
            if not quota["allowed"]:
                logger.warning(
                    f"[RetryScheduler] Tenant {tenant_id} quota exceeded "
                    f"({quota.get('reason', '')}) — skipping retry for lead {lead_id}"
                )
                continue

            # ── 3. Max retries reached → exhaust ─────────────
            if retry_count >= max_retries:
                logger.info(
                    f"[RetryScheduler] Lead {lead_id} ({lead['lead_name']}) "
                    f"reached max retries ({max_retries}) — marking exhausted"
                )
                _mark_exhausted(lead_id)
                db.add_log(
                    f"🔚 Retry exhausted: {lead['lead_name']} "
                    f"({lead['phone']}) — {retry_count}/{max_retries} attempts"
                )
                continue

            # ── 4. Fire the call ──────────────────────────────
            logger.info(
                f"[RetryScheduler] Retrying lead {lead_id} "
                f"({lead['lead_name']}, {lead['phone']}) "
                f"attempt {retry_count + 1}/{max_retries}"
            )
            db.add_log(
                f"🔄 Retry #{retry_count + 1}: {lead['lead_name']} "
                f"({lead['phone']}) — campaign '{lead['campaign_name']}'"
            )

            result = await make_single_call(
                phone   = lead["phone"],
                lead_id = str(lead_id),
                metadata={
                    "customer_name": lead["lead_name"],
                    "company":       lead["company"] or "",
                    "tenant_id":     str(tenant_id),
                    "is_retry":      "true",
                    "retry_count":   str(retry_count + 1),
                },
            )

            new_count = retry_count + 1

            if result:
                # Schedule next retry window (or mark exhausted if this was the last)
                if new_count >= max_retries:
                    _mark_exhausted(lead_id)
                    db.add_log(
                        f"🔚 Final retry placed: {lead['lead_name']} "
                        f"({lead['phone']}) — no more retries"
                    )
                else:
                    _schedule_next_retry(lead_id, new_count)
                    next_in = RETRY_INTERVAL_MIN * new_count
                    db.add_log(
                        f"⏲ Next retry scheduled in {next_in}min: "
                        f"{lead['lead_name']} ({lead['phone']})"
                    )
            else:
                # Call failed to initiate — still increment so we don't hammer
                _schedule_next_retry(lead_id, new_count)
                logger.warning(
                    f"[RetryScheduler] make_single_call returned None "
                    f"for lead {lead_id} — scheduled next retry"
                )

            # Small gap between retries to avoid burst dialling
            await asyncio.sleep(10)

        except Exception as e:
            logger.error(
                f"[RetryScheduler] Error processing lead {lead_id}: {e}",
                exc_info=True
            )
            db.add_log(f"❌ Retry scheduler error for lead {lead_id}: {e}")


async def start_retry_scheduler():
    """
    Entry point — call this once from main.py startup_event.
    Runs forever in background.
    """
    global _running
    if _running:
        logger.warning("[RetryScheduler] Already running — skipping duplicate start")
        return
    _running = True

    logger.info(
        f"[RetryScheduler] Started — checking every {CHECK_INTERVAL_SEC}s, "
        f"max {DEFAULT_MAX_RETRIES} retries, "
        f"{RETRY_INTERVAL_MIN}min spacing"
    )
    db.add_log("🔄 Smart retry scheduler started")

    while True:
        try:
            await _process_retries()
        except Exception as e:
            logger.error(f"[RetryScheduler] Top-level error: {e}", exc_info=True)
        await asyncio.sleep(CHECK_INTERVAL_SEC)
