"""
app/celery_worker.py
Celery task queue - manages bulk outbound PIOPIY call campaigns.
"""

import os
import time
from loguru import logger
from celery import Celery
from dotenv import load_dotenv

load_dotenv()

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
    worker_prefetch_multiplier=1,
    task_acks_late=True,
)


@celery_app.task(bind=True, name="launch_campaign")
def launch_campaign(self, leads: list, delay_seconds: int = 30):
    """Process leads and make outbound PIOPIY calls with delay between each."""
    from app.piopiy_handler import make_outbound_call

    total = len(leads)
    success = failed = skipped = 0

    logger.info(f"Campaign started | {total} leads | {delay_seconds}s delay")

    for i, lead in enumerate(leads):
        phone = lead.get("phone", "").strip().replace("+", "")
        name  = lead.get("name", "Unknown")

        if not phone:
            logger.warning(f"Skipping {name} — no phone")
            skipped += 1
            continue

        self.update_state(
            state="PROGRESS",
            meta={"current": i+1, "total": total, "lead": name, "success": success, "failed": failed},
        )

        try:
            request_id = make_outbound_call(phone, phone)
            logger.info(f"[{i+1}/{total}] Called {name} ({phone}) | ID: {request_id}")
            success += 1
        except Exception as e:
            logger.error(f"[{i+1}/{total}] Failed {name} ({phone}): {e}")
            failed += 1

        if i < total - 1:
            time.sleep(delay_seconds)

    result = {"status": "completed", "total": total, "success": success, "failed": failed, "skipped": skipped}
    logger.info(f"Campaign complete | {result}")
    return result
