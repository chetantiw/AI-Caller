"""

app/campaign_scheduler.py

Campaign auto-scheduler service.

Runs as a background task and automatically starts campaigns at scheduled times.

Supports: one-time, daily, weekly, and monthly scheduling.

"""


import asyncio
import os
from datetime import datetime, timedelta

from loguru import logger
from dotenv import load_dotenv

from app import database as db


load_dotenv()
os.makedirs("logs", exist_ok=True)
logger.add("logs/campaign_scheduler.log", rotation="100 MB", level="INFO", retention="7 days")


class CampaignScheduler:
    """Background scheduler for auto-running campaigns."""
    
    def __init__(self):
        self.running = False
        self.last_check = None
        
    async def start(self):
        """Start the scheduler background task."""
        logger.info("🎯 Campaign Scheduler starting…")
        self.running = True
        
        while self.running:
            try:
                await self._check_and_run_scheduled_campaigns()
                # Check every 60 seconds (at the start of each minute)
                await asyncio.sleep(60)
            except Exception as e:
                logger.error(f"Scheduler error: {e}", exc_info=True)
                await asyncio.sleep(60)
    
    async def stop(self):
        """Stop the scheduler."""
        logger.info("🛑 Campaign Scheduler stopping…")
        self.running = False
    
    async def _check_and_run_scheduled_campaigns(self):
        """Check if any campaigns should run and start them."""
        try:
            now = datetime.now()
            current_time = now.strftime("%H:%M")
            current_day = now.weekday()  # 0 = Monday, 6 = Sunday
            
            scheduled = db.get_scheduled_campaigns()
            
            if not scheduled:
                # Log occasionally, not every time
                if self.last_check is None or (now - self.last_check).seconds > 3600:
                    logger.debug("No scheduled campaigns to check")
                    self.last_check = now
                return
            
            logger.debug(f"Checking {len(scheduled)} scheduled campaigns at {current_time}")
            
            for campaign in scheduled:
                campaign_id = campaign.get('id')
                camp_name = campaign.get('name', f'Campaign {campaign_id}')
                scheduled_time = campaign.get('scheduled_time', '')
                repeat_type = campaign.get('repeat_type', 'once')
                scheduled_days = campaign.get('scheduled_days', '')
                status = campaign.get('status')
                last_run = campaign.get('last_run_at')
                
                # Check if it's time to run
                if scheduled_time != current_time:
                    continue
                
                # Check if it should run based on frequency
                should_run = await self._should_run_campaign(
                    campaign_id, repeat_type, current_day, last_run
                )
                
                if should_run:
                    await self._auto_start_campaign(campaign_id, camp_name)
            
            self.last_check = now
            
        except Exception as e:
            logger.error(f"Error checking scheduled campaigns: {e}", exc_info=True)
    
    async def _should_run_campaign(self, campaign_id: int, repeat_type: str, 
                                   current_day: int, last_run_at: str) -> bool:
        """
        Determine if a campaign should run based on repeat type.
        
        Returns:
          - True if campaign should run
          - False otherwise
        """
        if repeat_type == "once":
            # Only run if it hasn't been run before
            if last_run_at:
                return False
            return True
        
        elif repeat_type == "daily":
            # Run every day, but only once per day
            if not last_run_at:
                return True
            
            try:
                last_run = datetime.fromisoformat(last_run_at)
                now = datetime.now()
                # If last run was yesterday or earlier, run again
                return (now - last_run).days >= 1
            except Exception:
                return True
        
        elif repeat_type == "weekly":
            # Get scheduled days from campaign
            try:
                c = db.get_campaign(campaign_id)
                scheduled_days_str = c.get('scheduled_days', '')
                
                if not scheduled_days_str:
                    # No specific days set, run weekly on same day as created
                    logger.warning(f"Campaign {campaign_id}: no scheduled_days for weekly recurrence")
                    return False
                
                # Parse scheduled days (comma-separated, 0-6)
                scheduled_days = [int(d) for d in scheduled_days_str.split(',')]
                
                # Check if today is a scheduled day
                if current_day not in scheduled_days:
                    return False
                
                # Check if it's been at least 7 days since last run
                if not last_run_at:
                    return True
                
                try:
                    last_run = datetime.fromisoformat(last_run_at)
                    now = datetime.now()
                    return (now - last_run).days >= 7
                except Exception:
                    return True
            
            except Exception as e:
                logger.error(f"Error checking weekly recurrence for campaign {campaign_id}: {e}")
                return False
        
        elif repeat_type == "monthly":
            # Run once a month on the same date
            try:
                if not last_run_at:
                    return True
                
                last_run = datetime.fromisoformat(last_run_at)
                now = datetime.now()
                
                # Check if we're in a different month
                if (now.year, now.month) == (last_run.year, last_run.month):
                    return False
                
                return True
            except Exception:
                return True
        
        return False
    
    async def _auto_start_campaign(self, campaign_id: int, camp_name: str):
        """Automatically start a campaign."""
        try:
            c = db.get_campaign(campaign_id)
            
            if not c:
                logger.error(f"Campaign {campaign_id} not found")
                return
            
            # Check if campaign can be started
            current_status = c.get('status', 'draft')
            if current_status == 'running':
                logger.info(f"Campaign {campaign_id} already running, skipping auto-start")
                return
            
            # Update campaign status
            db.update_campaign_status(campaign_id, 'running')
            db.update_campaign_last_run(campaign_id)
            
            # Get delay from environment or use default
            delay = int(os.getenv("CAMPAIGN_CALL_DELAY", "60"))
            
            # Fire background task to run the campaign
            from app.campaign_runner import run_campaign
            asyncio.create_task(run_campaign(campaign_id, delay))
            
            db.add_log(
                f"📅 [AUTO-START] Campaign '{camp_name}' started by scheduler "
                f"({c.get('leads_count', 0)} leads, {delay}s delay)"
            )
            
            logger.info(
                f"✅ Campaign {campaign_id} ({camp_name}) auto-started by scheduler"
            )
        
        except Exception as e:
            logger.error(f"Error auto-starting campaign {campaign_id}: {e}", exc_info=True)
            db.add_log(f"❌ Campaign {campaign_id} auto-start failed: {e}")


# Global scheduler instance
_scheduler = None


async def start_scheduler():
    """Start the global campaign scheduler."""
    global _scheduler
    
    if _scheduler is not None:
        logger.warning("Scheduler already running")
        return
    
    _scheduler = CampaignScheduler()
    asyncio.create_task(_scheduler.start())
    logger.info("🚀 Campaign scheduler task created")


async def stop_scheduler():
    """Stop the global campaign scheduler."""
    global _scheduler
    
    if _scheduler:
        await _scheduler.stop()
        _scheduler = None
        logger.info("Campaign scheduler stopped")


def get_scheduler() -> CampaignScheduler:
    """Get the global scheduler instance."""
    global _scheduler
    return _scheduler
