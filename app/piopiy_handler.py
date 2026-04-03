
"""
app/piopiy_handler.py
PIOPIY Outbound Call Handler - wrapper around RestClient for integration with FastAPI

This module provides a high-level interface to trigger PIOPIY outbound calls
and can be integrated into API routes and background tasks.
"""

import os
from loguru import logger
from dotenv import load_dotenv

# Import the outbound caller
from app.piopiy_outbound_caller import trigger_outbound_call, trigger_outbound_call_async

load_dotenv()

logger.add("logs/piopiy_handler.log", rotation="500 MB", level="DEBUG")


def make_outbound_call(
    to_number: str,
    lead_id: str = None,
    customer_name: str = None,
    metadata: dict = None,
) -> dict:
    """
    Make an outbound call via PIOPIY.
    
    Args:
        to_number: Destination phone number
        lead_id: Optional lead ID for tracking
        customer_name: Optional customer name for greeting
        metadata: Optional additional metadata
        
    Returns:
        dict with call status and details
        
    Example:
        result = make_outbound_call(
            to_number="9876543210",
            lead_id="123",
            customer_name="Rajesh Kumar"
        )
        if result["status"] == "success":
            print(f"Call initiated: {result['to_number']}")
    """
    logger.info(f"📞 PIOPIY outbound call request | to: {to_number} | lead: {lead_id}")
    
    return trigger_outbound_call(
        to_number=to_number,
        customer_name=customer_name,
        lead_id=lead_id,
        additional_context=metadata,
    )


async def make_outbound_call_async(
    to_number: str,
    lead_id: str = None,
    customer_name: str = None,
    metadata: dict = None,
) -> dict:
    """Async version for FastAPI route handlers."""
    logger.info(f"📞 PIOPIY async outbound call request | to: {to_number} | lead: {lead_id}")
    
    return await trigger_outbound_call_async(
        to_number=to_number,
        customer_name=customer_name,
        lead_id=lead_id,
        additional_context=metadata,
    )


