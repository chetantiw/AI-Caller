"""
app/piopiy_outbound_caller.py
PIOPIY Outbound Call Trigger - uses REST API to initiate calls
This can be called from your FastAPI routes or background tasks.

Required Environment Variables:
- PIOPIY_TOKEN: REST API authentication token
- AGENT_ID: Your PIOPIY agent ID
- PIOPIY_NUMBER: Your purchased phone number for outbound calls
"""

import os
from loguru import logger
from dotenv import load_dotenv

load_dotenv()

logger.add("logs/piopiy_outbound.log", rotation="500 MB", level="DEBUG")


def trigger_outbound_call(
    to_number: str,
    customer_name: str = None,
    lead_id: str = None,
    additional_context: dict = None
) -> dict:
    """
    Trigger an outbound call using PIOPIY REST API.
    
    Args:
        to_number: Destination phone number (10 or 11 digits, with or without country code)
        customer_name: Optional customer name for personalized greeting
        lead_id: Optional lead ID for tracking in database
        additional_context: Optional dict with additional metadata
        
    Returns:
        dict with call_id, status, and response details
        
    Example:
        result = trigger_outbound_call(
            to_number="9876543210",
            customer_name="Rajesh Kumar",
            lead_id="123"
        )
    """
    
    try:
        from piopiy_voice import RestClient
    except ImportError:
        logger.error("❌ piopiy-ai not installed. Run: pip install piopiy-ai")
        return {"status": "error", "message": "PIOPIY SDK not installed"}
    
    # Validate required environment variables
    token = (
        os.getenv("PIOPIY_TOKEN")
        or os.getenv("PIOPIY_AGENT_TOKEN")
        or os.getenv("AGENT_TOKEN")
    )
    agent_id = os.getenv("AGENT_ID") or os.getenv("PIOPIY_AGENT_ID")
    caller_id = os.getenv("PIOPIY_NUMBER")
    
    if not all([token, agent_id, caller_id]):
        missing = []
        if not token:
            missing.append("PIOPIY_TOKEN")
        if not agent_id:
            missing.append("AGENT_ID")
        if not caller_id:
            missing.append("PIOPIY_NUMBER")
        
        error_msg = f"Missing environment variables: {', '.join(missing)}"
        logger.error(f"❌ {error_msg}")
        return {"status": "error", "message": error_msg}
    
    # Normalize phone number
    to_number = str(to_number).strip()
    if to_number.startswith("+"):
        to_number = to_number[1:]
    
    # Remove non-digits
    digits = "".join(c for c in to_number if c.isdigit())
    
    # Handle Indian numbers
    if len(digits) == 10:
        to_number = "+91" + digits
    elif len(digits) == 11 and digits.startswith("0"):
        to_number = "+91" + digits[1:]
    elif len(digits) == 12 and digits.startswith("91"):
        to_number = "+" + digits
    elif len(digits) == 13 and digits.startswith("091"):
        to_number = "+91" + digits[2:]
    else:
        logger.warning(f"⚠️  Phone number {digits} may not be valid (length: {len(digits)})")
        to_number = "+" + digits if not digits.startswith("0") else digits
    
    # Ensure caller_id is also in E.164 format
    if not caller_id.startswith("+"):
        caller_digits = "".join(c for c in caller_id if c.isdigit())
        if len(caller_digits) == 10:
            caller_id = "+91" + caller_digits
        elif len(caller_digits) == 11 and caller_digits.startswith("0"):
            caller_id = "+91" + caller_digits[1:]
        elif not caller_id.startswith("+"):
            caller_id = "+" + caller_digits
    
    # Build metadata for session
    variables = {
        "customer_name": customer_name or "there",
    }
    
    # Add any additional context
    if additional_context:
        variables.update(additional_context)
    
    if lead_id:
        variables["lead_id"] = lead_id
    
    logger.info(f"📞 Triggering outbound call")
    logger.info(f"   To: {to_number} | From: {caller_id} | Agent: {agent_id}")
    if customer_name:
        logger.info(f"   Customer: {customer_name}")
    if lead_id:
        logger.info(f"   Lead ID: {lead_id}")
    
    try:
        # Initialize REST Client
        client = RestClient(token=token)
        
        # Trigger the outbound call
        response = client.ai.call(
            caller_id=caller_id,
            to_number=to_number,
            agent_id=agent_id,
            variables=variables,
        )
        
        logger.info(f"✅ Call successfully initiated!")
        logger.debug(f"   Response: {response}")
        
        return {
            "status": "success",
            "to_number": to_number,
            "caller_id": caller_id,
            "customer_name": customer_name,
            "lead_id": lead_id,
            "response": response,
        }
        
    except Exception as e:
        logger.error(f"❌ Failed to initiate call: {e}", exc_info=True)
        return {
            "status": "error",
            "message": str(e),
            "to_number": to_number,
            "customer_name": customer_name,
            "lead_id": lead_id,
        }


async def trigger_outbound_call_async(
    to_number: str,
    customer_name: str = None,
    lead_id: str = None,
    additional_context: dict = None
) -> dict:
    """Async wrapper for trigger_outbound_call (for use in FastAPI routes)."""
    return trigger_outbound_call(to_number, customer_name, lead_id, additional_context)


if __name__ == "__main__":
    import sys
    
    # Quick test: python piopiy_outbound_caller.py <phone_number> [customer_name]
    if len(sys.argv) < 2:
        print("Usage: python piopiy_outbound_caller.py <phone_number> [customer_name]")
        print("Example: python piopiy_outbound_caller.py 9876543210 'Rajesh Kumar'")
        sys.exit(1)
    
    phone = sys.argv[1]
    name = sys.argv[2] if len(sys.argv) > 2 else None
    
    result = trigger_outbound_call(phone, customer_name=name)
    print("\n" + "="*60)
    print("RESULT:")
    print("="*60)
    import json
    print(json.dumps(result, indent=2, default=str))
