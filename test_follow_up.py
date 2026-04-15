#!/usr/bin/env python3
"""
Test script for follow-up automation functionality.
"""

import os
import sys
import asyncio
from datetime import datetime

# Add the app directory to the path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'app'))

from app.database import init_db, create_call, get_call
from app.follow_up_service import schedule_call_follow_up

async def test_follow_up_flow():
    """Test the complete follow-up flow."""
    print("🧪 Testing Follow-up Automation")
    print("=" * 50)

    # Initialize database
    print("📊 Initializing database...")
    init_db()

    # Create a test call (without foreign keys for simplicity)
    print("📞 Creating test call...")
    call_id = create_call(
        phone="+919876543210",
        lead_name="John Doe",
        company="Test Company"
    )
    print(f"✅ Created call with ID: {call_id}")

    # Complete the call with outcome
    from app.database import complete_call
    complete_call(
        call_id=call_id,
        duration_sec=120,
        outcome="interested",
        sentiment="positive",
        summary="Customer showed interest in our services"
    )
    print("✅ Completed call with outcome")

    # Get the call details
    call = get_call(call_id)
    print(f"📋 Call details: {call}")

    # Schedule follow-up
    print("📅 Scheduling follow-up...")
    await schedule_call_follow_up(call_id)
    print("✅ Follow-up scheduled")

    # Wait a bit to let the follow-up service initialize
    await asyncio.sleep(2)

    print("🎉 Follow-up automation test completed!")
    print("\nNote: Actual message sending requires teleCMI/Email credentials in .env")
    print("The service will log warnings if credentials are missing.")

if __name__ == "__main__":
    asyncio.run(test_follow_up_flow())