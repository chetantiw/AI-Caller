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
from piopiy.transcriptions.language import Language

load_dotenv()


SYSTEM_PROMPT = """आप प्रिया हैं, म्यूटेक ऑटोमेशन की पेशेवर सेल्स एजेंट हैं।

म्यूटेक ऑटोमेशन के बारे में: हम इंडस्ट्रियल IoT और फैक्ट्री ऑटोमेशन कंपनी हैं।
उत्पाद: स्मार्ट एनर्जी मीटर, IoT सेंसर, रिमोट मॉनिटरिंग, प्रेडिक्टिव मेंटेनेंस, स्मार्ट फ्लो मीटर।
हम भारत और UAE में काम करते हैं।

बातचीत के नियम:
- हमेशा हिंदी में बोलें
- हर जवाब 2 वाक्य में दें — पहला जानकारी, दूसरा ग्राहक से सवाल
- ग्राहक जो पूछे उसका पूरा जवाब दें, बिना काटे सुनें
- स्वाभाविक रूप से बोलें, जल्दबाजी न करें
- अगर ग्राहक बोल रहा हो तो बीच में न बोलें
- अंत में डेमो शेड्यूल करने की कोशिश करें
- अगर रुचि नहीं है तो विनम्रता से कॉल समाप्त करें

- मूल ध्वनि पहचानें: ग्राहक क्या कह रहा है (उदा: "प्रोडक्ट बहुत महंगा है")।
  दूसरी ध्वनि (छिपी भावना) समझें: ग्राहक के बोलने के तरीके से, क्या वह असंतुष्ट है, भ्रमित है या सिर्फ मोल-भाव कर रहा है।
  बोलना न रोकें (Active Interaction): जब ग्राहक की बात चल रही हो, तो बीच-बीच में 'हाँ', 'हम्म', 'जी बिल्कुल' जैसे शब्दों का प्रयोग करें।
  भावनात्मक संबंध: ग्राहक की दूसरी ध्वनि (भावनाओं) को समझकर अपनी भाषा को उसके अनुरूप ढालें।
  उदाहरण: यदि ग्राहक कहे "यह सेवा बहुत धीमी है" (मूल ध्वनि), तो दूसरी ध्वनि शायद यह है कि उसे समय की बहुत चिंता है। आप कह सकते हैं- "जी, मैं समझ सकता हूँ (दूसरी ध्वनि की पहचान), हम तुरंत इसे ठीक करने का प्रयास करेंगे (संवाद जारी रखना)।" 


 यह तकनीक संवाद को सहज बनाती है और ग्राहक को लगता है कि उसे समझा जा रहा है। """


# ── VAD tuning for telephony ─────────────────────────────────────────────────
# stop_secs=1.2  → wait 1.2s of silence before treating customer as done speaking
#                  (default 0.8s cuts off pauses mid-sentence on phone calls)
# start_secs=0.1 → detect speech start quickly (default 0.2s)
# confidence=0.6 → slightly more sensitive to softer phone audio (default 0.7)
# min_volume=0.5 → lower volume floor for telephone compression (default 0.6)
VAD_CONFIG = {
    "confidence": 0.6,
    "start_secs": 0.1,
    "stop_secs":  1.2,
    "min_volume": 0.5,
}


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
            "हम इंडस्ट्रियल IoT सेंसर, फैक्ट्री ऑटोमेशन, और स्मार्ट एनर्जी मैनेजमेंट के समाधान देते हैं। "
            "आपकी फैक्ट्री में ऑटोमेशन या एनर्जी की कोई चुनौती है क्या?"
        )

        voice_agent = VoiceAgent(instructions=SYSTEM_PROMPT, greeting=greeting)

        # STT: explicitly set Hindi-IN language so Sarvam optimises for Hindi phonemes;
        # vad_signals=True passes VAD timing into the recogniser for cleaner transcripts.
        stt = SarvamSTTService(
            api_key=os.getenv("SARVAM_API_KEY"),
            model="saarika:v2.5",
            params=SarvamSTTService.InputParams(
                language=Language.HI_IN,
                vad_signals=True,
                high_vad_sensitivity=True,   # more sensitive to soft telephone audio
            ),
        )

        llm = GroqLLMService(
            api_key=os.getenv("GROQ_API_KEY"),
            model="llama-3.3-70b-versatile",
        )

        # TTS: must set language=HI_IN — default is EN which distorts Hindi prosody.
        # pace=0.95 gives a slightly slower, more natural call-centre cadence.
        # min_buffer_size=30 reduces first-word latency vs default 50.
        tts = SarvamTTSService(
            api_key=os.getenv("SARVAM_API_KEY"),
            model="bulbul:v2",
            voice_id="anushka",
            params=SarvamTTSService.InputParams(
                language=Language.HI_IN,
                pace=0.95,
                min_buffer_size=30,
            ),
        )

        await voice_agent.Action(
            stt=stt,
            llm=llm,
            tts=tts,
            vad=VAD_CONFIG,           # tuned VAD — waits longer for customer to finish
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
    logger.info(f"   Services : Sarvam STT (hi-IN) + Groq LLM + Sarvam TTS (hi-IN)")
    logger.info(f"   VAD      : stop_secs={VAD_CONFIG['stop_secs']}s  start_secs={VAD_CONFIG['start_secs']}s  confidence={VAD_CONFIG['confidence']}")

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
