
"""
app/piopiy_agent.py
PIOPIY Phone Agent - handles inbound and outbound calls
This agent runs in the background and waits for call events from PIOPIY infrastructure.

Connection Parameters:
- AGENT_ID: Unique agent identifier from PIOPIY Dashboard
- AGENT_TOKEN: Agent authentication token
- Required STT/LLM/TTS API keys from respective service providers
"""

import asyncio
import os
from dotenv import load_dotenv
from loguru import logger

from piopiy.agent import Agent
from piopiy.voice_agent import VoiceAgent

load_dotenv()

logger.add("logs/piopiy_agent.log", rotation="500 MB", level="DEBUG")

# System prompt in Hindi for sales agent
SYSTEM_PROMPT = """आप प्रिया हैं, म्यूटेक ऑटोमेशन की पेशेवर सेल्स एजेंट हैं।

म्यूटेक ऑटोमेशन: इंडस्ट्रियल IoT और फैक्ट्री ऑटोमेशन कंपनी।

उत्पाद: स्मार्ट एनर्जी मीटर, IoT सेंसर, रिमोट मॉनिटरिंग, प्रेडिक्टिव मेंटेनेंस।

नियम: हमेशा हिंदी में बोलें। हर जवाब 2 वाक्य। स्वाभाविक रूप से बोलें। अंत में डेमो शेड्यूल करें।"""


async def create_session(agent_id: str, call_id: str, from_number: str, to_number: str, metadata: dict = None, **kwargs):
    """
    Callback invoked by PIOPIY when a new call arrives (inbound or outbound).
    
    Parameters:
    - agent_id: Your agent's unique identifier
    - call_id: Unique identifier for this session
    - from_number: Caller's phone number (E.164 format)
    - to_number: Dialed phone number
    - metadata: Additional context (e.g., customer_name from outbound trigger)
    """
    
    logger.info(f"📞 New call session | Call ID: {call_id} | From: {from_number} | To: {to_number}")
    
    # Extract metadata for personalization
    metadata = metadata or {}
    customer_name = metadata.get("customer_name", "")
    
    # Personalize greeting based on metadata
    if customer_name:
        greeting = f"नमस्ते {customer_name} जी! मैं प्रिया बोल रही हूँ म्यूटेक ऑटोमेशन से। क्या आपके पास एक मिनट है?"
        logger.info(f"   Using personalized greeting for: {customer_name}")
    else:
        greeting = "नमस्ते! मैं प्रिया बोल रही हूँ म्यूटेक ऑटोमेशन से। क्या आपके पास एक मिनट है?"
    
    try:
        # Initialize VoiceAgent with instructions and greeting
        voice_agent = VoiceAgent(
            instructions=SYSTEM_PROMPT,
            greeting=greeting,
        )
        
        # Import based on configured STT/LLM/TTS providers
        # Check which providers are configured in environment
        
        stt = None
        llm = None
        tts = None
        
        # Try Deepgram for STT (preferred - low latency)
        if os.getenv("DEEPGRAM_API_KEY"):
            from piopiy.services.deepgram.stt import DeepgramSTTService
            stt = DeepgramSTTService(
                api_key=os.getenv("DEEPGRAM_API_KEY"),
                model="nova-2",
                language="hi-IN"  # Hindi
            )
            logger.info("STT: Using Deepgram")
        elif os.getenv("SARVAM_API_KEY"):
            from piopiy.services.sarvam.stt import SarvamSTTService
            stt = SarvamSTTService(
                api_key=os.getenv("SARVAM_API_KEY"),
                model="saarika:v2.5"
            )
            logger.info("STT: Using Sarvam")
        else:
            logger.error("❌ No STT provider configured. Set DEEPGRAM_API_KEY or SARVAM_API_KEY")
            return
        
        # Try OpenAI for LLM (preferred - most capable)
        if os.getenv("OPENAI_API_KEY"):
            from piopiy.services.openai.llm import OpenAILLMService
            llm = OpenAILLMService(
                api_key=os.getenv("OPENAI_API_KEY"),
                model="gpt-4o-mini"
            )
            logger.info("LLM: Using OpenAI")
        elif os.getenv("GROQ_API_KEY"):
            from piopiy.services.groq.llm import GroqLLMService
            llm = GroqLLMService(
                api_key=os.getenv("GROQ_API_KEY"),
                model="llama-3.3-70b-versatile"
            )
            logger.info("LLM: Using Groq")
        else:
            logger.error("❌ No LLM provider configured. Set OPENAI_API_KEY or GROQ_API_KEY")
            return
        
        # Try Cartesia for TTS (preferred - natural voice)
        if os.getenv("CARTESIA_API_KEY"):
            from piopiy.services.cartesia.tts import CartesiaTTSService
            tts = CartesiaTTSService(
                api_key=os.getenv("CARTESIA_API_KEY"),
                voice_id="694f9ed8-38bb-4e94-91ce-f831ae3f3fc0"  # Professional voice
            )
            logger.info("TTS: Using Cartesia")
        elif os.getenv("SARVAM_API_KEY"):
            from piopiy.services.sarvam.tts import SarvamTTSService
            tts = SarvamTTSService(
                api_key=os.getenv("SARVAM_API_KEY"),
                model="bulbul:v2",
                voice_id="anushka"
            )
            logger.info("TTS: Using Sarvam")
        else:
            logger.error("❌ No TTS provider configured. Set CARTESIA_API_KEY or SARVAM_API_KEY")
            return
        
        # Configure the voice agent pipeline
        await voice_agent.Action(
            stt=stt,
            llm=llm,
            tts=tts,
            vad=True,  # Voice Activity Detection
            allow_interruptions=True  # Allow natural interruptions
        )
        
        # Start the streaming conversation
        logger.info(f"✅ Starting voice pipeline for call {call_id}")
        await voice_agent.start()
        
    except Exception as e:
        logger.error(f"❌ Error in create_session: {e}", exc_info=True)
        raise


async def main():
    """Initialize and run the PIOPIY Agent."""
    
    agent_id = os.getenv("AGENT_ID")
    agent_token = os.getenv("AGENT_TOKEN")
    
    if not agent_id or not agent_token:
        logger.error("❌ Missing AGENT_ID or AGENT_TOKEN in environment")
        return
    
    logger.info("🚀 Starting PIOPIY Agent (Priya)...")
    logger.info(f"   Agent ID: {agent_id}")
    
    try:
        # Initialize the Agent with callback
        agent = Agent(
            agent_id=agent_id,
            agent_token=agent_token,
            create_session=create_session,
            debug=True,
        )
        
        logger.info("📡 Connecting to PIOPIY signaling server...")
        await agent.connect()
        
    except Exception as e:
        logger.error(f"❌ Fatal error: {e}", exc_info=True)
        raise


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("🛑 Agent stopped by user")
    except Exception as e:
        logger.error(f"❌ Unexpected error: {e}", exc_info=True)

