"""
app/celery_worker.py
Celery task queue — manages bulk outbound call campaigns with rate limiting.
"""

import os
import time
from loguru import logger
from celery import Celery
from dotenv import load_dotenv

load_dotenv()

# ── Celery App ────────────────────────────────────────────────────────────────
celery_app = Celery(
    "ai_caller",
    broker=os.getenv("REDIS_URL", "redis://localhost:6379/0"),
    backend=os.getenv("REDIS_URL", "redis://localhost:6379/0"),
)

celery_app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone="Asia/Kolkata",
    enable_utc=True,
    task_track_started=True,
    worker_prefetch_multiplier=1,    # One call at a time per worker
    task_acks_late=True,
)


# ── Tasks ─────────────────────────────────────────────────────────────────────

@celery_app.task(bind=True, name="launch_campaign")
def launch_campaign(self, leads: list, delay_seconds: int = 30):
    """
    Process a list of leads and make outbound calls with a delay between each.

    Args:
        leads: List of lead dicts from CSV
        delay_seconds: Wait time between calls (avoid spam flagging)
    """
    from app.twilio_handler import make_outbound_call

    total = len(leads)
    success = 0
    failed = 0
    skipped = 0

    logger.info(f"Campaign started | {total} leads | {delay_seconds}s delay")

    for i, lead in enumerate(leads):
        phone = lead.get("phone", "").strip()
        name = lead.get("name", "Unknown")

        if not phone:
            logger.warning(f"Skipping lead {name} — no phone number")
            skipped += 1
            continue

        # Update task progress
        self.update_state(
            state="PROGRESS",
            meta={
                "current": i + 1,
                "total": total,
                "lead": name,
                "success": success,
                "failed": failed,
            },
        )

        try:
            call_sid = make_outbound_call(
                to_number=phone,
                lead_id=phone.replace("+", ""),
            )
            logger.info(f"[{i+1}/{total}] Called {name} ({phone}) | SID: {call_sid}")
            success += 1

        except Exception as e:
            logger.error(f"[{i+1}/{total}] Failed to call {name} ({phone}): {e}")
            failed += 1

        # Delay between calls (skip delay after last call)
        if i < total - 1:
            logger.debug(f"Waiting {delay_seconds}s before next call...")
            time.sleep(delay_seconds)

    result = {
        "status": "completed",
        "total": total,
        "success": success,
        "failed": failed,
        "skipped": skipped,
    }
    logger.info(f"Campaign complete | {result}")
    return result


@celery_app.task(name="single_call_task")
def single_call_task(phone: str, lead_id: str = None):
    """Queue a single call as a background task."""
    from app.twilio_handler import make_outbound_call
    try:
        call_sid = make_outbound_call(phone, lead_id or phone)
        return {"status": "initiated", "call_sid": call_sid}
    except Exception as e:
        logger.error(f"Single call task failed: {e}")
        return {"status": "failed", "error": str(e)}
