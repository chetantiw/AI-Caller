"""
app/twilio_handler.py
Handles outbound call initiation and Twilio webhook TwiML responses.
"""

import os
from loguru import logger
from twilio.rest import Client
from twilio.twiml.voice_response import VoiceResponse, Connect, Stream
from dotenv import load_dotenv

load_dotenv()

# Twilio client
twilio_client = Client(
    os.getenv("TWILIO_ACCOUNT_SID"),
    os.getenv("TWILIO_AUTH_TOKEN"),
)


def make_outbound_call(to_number: str, lead_id: str = None) -> str:
    """
    Initiate an outbound call to the prospect.

    Args:
        to_number: Prospect phone number in E.164 format (+919876543210)
        lead_id: Optional lead ID to pass as call parameter

    Returns:
        Twilio Call SID
    """
    public_url = os.getenv("PUBLIC_URL", "https://yourdomain.com")
    webhook_url = f"{public_url}/twilio/answer"

    if lead_id:
        webhook_url += f"?lead_id={lead_id}"

    logger.info(f"Initiating call to {to_number} | webhook: {webhook_url}")

    call = twilio_client.calls.create(
        to=to_number,
        from_=os.getenv("TWILIO_PHONE_NUMBER"),
        url=webhook_url,
        method="POST",
        status_callback=f"{public_url}/twilio/status",
        status_callback_method="POST",
        status_callback_event=["initiated", "ringing", "answered", "completed"],
        timeout=30,         # Ring timeout in seconds
        machine_detection="Enable",  # AMD — detects voicemail
        machine_detection_timeout=5,
    )

    logger.info(f"Call initiated | SID: {call.sid} | Status: {call.status}")
    return call.sid


def build_twiml_response(server_url: str, lead_id: str = None) -> str:
    """
    Build TwiML to connect call to our WebSocket pipeline.

    Twilio calls this webhook when the prospect picks up.
    Returns TwiML XML that connects audio stream to our Pipecat pipeline.
    """
    response = VoiceResponse()

    # Short pause to let audio settle
    response.pause(length=1)

    connect = Connect()
    stream_url = f"wss://{server_url.replace('https://', '').replace('http://', '')}/ws/audio"

    if lead_id:
        stream_url += f"?lead_id={lead_id}"

    stream = Stream(url=stream_url)
    stream.parameter(name="lead_id", value=lead_id or "")
    connect.append(stream)
    response.append(connect)

    return str(response)


def end_call(call_sid: str) -> bool:
    """
    Forcefully end an active call.
    """
    try:
        twilio_client.calls(call_sid).update(status="completed")
        logger.info(f"Call {call_sid} ended programmatically")
        return True
    except Exception as e:
        logger.error(f"Failed to end call {call_sid}: {e}")
        return False


def get_call_status(call_sid: str) -> dict:
    """
    Fetch current call status from Twilio.
    """
    try:
        call = twilio_client.calls(call_sid).fetch()
        return {
            "sid": call.sid,
            "status": call.status,
            "duration": call.duration,
            "direction": call.direction,
            "from": call.from_formatted,
            "to": call.to_formatted,
            "start_time": str(call.start_time),
            "end_time": str(call.end_time),
            "price": call.price,
        }
    except Exception as e:
        logger.error(f"Failed to fetch call status: {e}")
        return {}
