#!/usr/bin/env python3
"""Smoke checks for no-dial manual and campaign call flows."""

import asyncio
import os
import tempfile

from app import database as db
from app.api_routes import test_call
from app.campaign_runner import make_single_call, run_campaign


async def main():
    os.environ["AI_CALLER_DRY_RUN"] = "true"

    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    original_db_path = db.DB_PATH
    db.DB_PATH = path

    try:
        db.init_db()

        manual = await make_single_call("9876543210")
        assert manual["dry_run"] is True
        assert manual["provider"] == "dryrun"
        assert manual["phone"] == "919876543210"

        class FakeRequest:
            async def json(self):
                return {"phone": "9876543210"}

        api_result = await test_call(FakeRequest())
        assert api_result["dry_run"] is True
        assert api_result["provider"] == "dryrun"

        campaign_id = db.create_campaign("Dry Run Campaign", "stabilization smoke")
        lead_id = db.create_lead(
            name="Test Lead",
            phone="9876543210",
            company="Test Co",
            campaign_id=campaign_id,
        )
        db.update_campaign_status(campaign_id, "running")

        await run_campaign(campaign_id, delay_seconds=0)

        campaign = db.get_campaign(campaign_id)
        lead = db.get_lead(lead_id)
        calls = db.get_calls(campaign_id=campaign_id)

        assert campaign["status"] == "completed"
        assert campaign["calls_made"] == 1
        assert lead["status"] == "called"
        assert len(calls) == 1
        assert calls[0]["call_sid"].startswith("dryrun-")

        print("dry-run stabilization smoke ok")
    finally:
        db.DB_PATH = original_db_path
        os.remove(path)


if __name__ == "__main__":
    asyncio.run(main())
