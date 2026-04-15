"""
app/scheduler.py
Background campaign scheduler.
Runs every 60 seconds, checks for campaigns due to run, fires them.
Supports: once (run at specific datetime), daily (run at HH:MM every day)
"""
import asyncio
from datetime import datetime
from loguru import logger
from app import database as db

_scheduler_running = False

async def scheduler_loop():
    global _scheduler_running
    if _scheduler_running:
        return
    _scheduler_running = True
    logger.info("🕐 Campaign scheduler started")
    while True:
        try:
            await _check_and_fire()
        except Exception as e:
            logger.error(f"Scheduler error: {e}")
        await asyncio.sleep(60)

async def _check_and_fire():
    now = datetime.now()
    current_time = now.strftime("%H:%M")
    current_day  = now.strftime("%a").lower()  # mon, tue, wed...
    current_dt   = now.strftime("%Y-%m-%d %H:%M")

    with db.get_conn() as conn:
        campaigns = [dict(r) for r in conn.execute(
            "SELECT * FROM campaigns WHERE schedule_type IS NOT NULL AND schedule_status='pending'"
        ).fetchall()]

    for c in campaigns:
        should_run = False
        stype = c.get('schedule_type')

        if stype == 'once':
            # next_run_at stores full datetime YYYY-MM-DD HH:MM
            nra = (c.get('next_run_at') or '')[:16]
            if nra and nra <= current_dt:
                should_run = True

        elif stype == 'daily':
            # schedule_time stores HH:MM, runs every day at that time
            if c.get('schedule_time') == current_time:
                should_run = True

        elif stype == 'weekdays':
            # schedule_time stores HH:MM, schedule_days stores "mon,tue,wed,thu,fri"
            days = [d.strip() for d in (c.get('schedule_days') or '').split(',')]
            if c.get('schedule_time') == current_time and current_day in days:
                should_run = True

        if should_run and c.get('status') != 'running':
            logger.info(f"🕐 Scheduler firing campaign {c['id']}: {c['name']}")
            db.add_log(f"🕐 Scheduled campaign started: {c['name']}")
            db.update_campaign_status(c['id'], 'running')

            # For one-time, mark as triggered so it doesn't run again
            if stype == 'once':
                with db.get_conn() as conn:
                    conn.execute(
                        "UPDATE campaigns SET schedule_status='triggered' WHERE id=?",
                        (c['id'],)
                    )
                    conn.commit()

            from app.campaign_runner import run_campaign
            delay = c.get('schedule_delay') or 60
            asyncio.create_task(run_campaign(c['id'], delay))
            logger.info(f"✅ Campaign {c['id']} fired by scheduler")


def start_scheduler():
    asyncio.create_task(scheduler_loop())
