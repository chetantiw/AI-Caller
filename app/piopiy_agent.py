"""
app/piopiy_agent.py

PIOPIY AI voice agent — connects to signaling.piopiy.com and handles
every inbound/outbound call with Sarvam STT + Groq LLM + Sarvam TTS.

Run standalone:  python -m app.piopiy_agent
Or via start.sh (preferred).
"""

import asyncio
import os

from loguru import logger
from dotenv import load_dotenv

from piopiy.agent import Agent
from piopiy.voice_agent import VoiceAgent
from piopiy.services.sarvam.stt import SarvamSTTService
from piopiy.services.sarvam.tts import SarvamTTSService
from piopiy.services.groq.llm import GroqLLMService

load_dotenv()


SYSTEM_PROMPT = """आप प्रिया हैं, म्यूटेक ऑटोमेशन की पेशेवर सेल्स एजेंट हैं।

म्यूटेक ऑटोमेशन: इंडस्ट्रियल IoT और फैक्ट्री ऑटोमेशन कंपनी।

उत्पाद: स्मार्ट एनर्जी मीटर, IoT सेंसर, रिमोट मॉनिटरिंग, प्रेडिक्टिव मेंटेनेंस।

नियम: हमेशा हिंदी में बोलें। हर जवाब 2 वाक्य। स्वाभाविक रूप से बोलें। अंत में डेमो शेड्यूल करें।"""


async def create_session(
    agent_id=None,
    call_id=None,
    from_number=None,
    to_number=None,
    **kwargs,
):
    """
    Called by the Agent framework for every answered call (inbound or outbound).
    Sets up the STT → LLM → TTS pipeline for this call session.
    """
    logger.info(f"📞 New call session | agent={agent_id} | call_id={call_id} | from={from_number} | to={to_number}")

    try:
        metadata      = kwargs.get("metadata") or {}
        customer_name = metadata.get("customer_name", "")

        name_part = f"{customer_name} जी" if customer_name else ""
        greeting = (
            f"नमस्ते {name_part}! मैं प्रिया बोल रही हूँ म्यूटेक ऑटोमेशन से। "
            "हम इंडस्ट्रियल IoT सेंसर, प्रेडिक्टिव मेंटेनेंस, स्मार्ट फ्लो मीटर, और स्मार्ट एनर्जी मैनेजमेंट के "
            "समाधान देते हैं। आपकी फैक्ट्री में कोई चुनौती है क्या?"
        )

        voice_agent = VoiceAgent(instructions=SYSTEM_PROMPT, greeting=greeting)

        stt = SarvamSTTService(
            api_key=os.getenv("SARVAM_API_KEY"),
            model="saarika:v2.5",
        )
        llm = GroqLLMService(
            api_key=os.getenv("GROQ_API_KEY"),
            model="llama-3.3-70b-versatile",
        )
        tts = SarvamTTSService(
            api_key=os.getenv("SARVAM_API_KEY"),
            model="bulbul:v2",
            voice_id="anushka",
        )

        await voice_agent.Action(
            stt=stt,
            llm=llm,
            tts=tts,
            vad=True,
            allow_interruptions=True,
        )
        await voice_agent.start()

    except asyncio.CancelledError:
        logger.info(f"Session cancelled | call_id={call_id}")
    except Exception as e:
        logger.error(f"Session error | call_id={call_id} | {e}")


async def main():
    agent_id    = os.getenv("PIOPIY_AGENT_ID")
    agent_token = os.getenv("PIOPIY_AGENT_TOKEN")

    if not agent_id or not agent_token:
        raise RuntimeError("PIOPIY_AGENT_ID and PIOPIY_AGENT_TOKEN must be set in .env")

    logger.info("🚀 Starting Priya PIOPIY Agent…")
    logger.info(f"   Agent ID : {agent_id}")
    logger.info(f"   Services : Sarvam STT + Groq LLM + Sarvam TTS")

    agent = Agent(
        agent_id=agent_id,
        agent_token=agent_token,
        create_session=create_session,
        debug=True,
    )

    logger.info("📡 Connecting to PIOPIY signaling server…")
    await agent.connect()


if __name__ == "__main__":
    asyncio.run(main())
