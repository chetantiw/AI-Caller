"""

app/piopiy_agent.py

PIOPIY Voice Agent — Priya

Uses FULL Pipecat pipeline via LiveKit transport (same stack as Exotel).


Architecture:

  PIOPIY signaling server sends join_room with (url, token, room_name)

  → We read these from piopiy.agent context vars

  → LiveKitTransport(url, token, room_name) connects to the LiveKit room

  → Pipecat pipeline: Silero VAD → Sarvam STT → Groq LLM → Sarvam TTS

  → Same quality, timing, and Hindi voice as Exotel implementation


This replaces the piopiy-ai VoiceAgent black box with our own pipeline,

giving us full control over VAD, TTS language, pace, and interruptions.

"""


import asyncio

import os

import json

import time

from loguru import logger

from dotenv import load_dotenv


# ── piopiy-ai: signaling only ─────────────────────────────────

from piopiy.agent import Agent, URL_CTX, TOKEN_CTX, ROOM_CTX


# ── Pipecat pipeline ──────────────────────────────────────────

from pipecat.audio.vad.silero import SileroVADAnalyzer

from pipecat.pipeline.pipeline import Pipeline

from pipecat.pipeline.runner import PipelineRunner

from pipecat.pipeline.task import PipelineParams, PipelineTask

from pipecat.processors.aggregators.openai_llm_context import OpenAILLMContext

from pipecat.services.groq.llm import GroqLLMService

from pipecat.services.sarvam.stt import SarvamSTTService

from pipecat.services.sarvam.tts import SarvamTTSService

from pipecat.transcriptions.language import Language

from pipecat.transports.livekit.transport import LiveKitTransport, LiveKitParams


load_dotenv()


import os as _os

_os.makedirs("logs", exist_ok=True)

logger.add("logs/piopiy_agent.log", rotation="100 MB", level="DEBUG", retention="7 days")


# ── System prompt ─────────────────────────────────────────────

SYSTEM_PROMPT = """आप प्रिया हैं, म्यूटेक ऑटोमेशन की पेशेवर सेल्स एजेंट हैं।


म्यूटेक ऑटोमेशन के बारे में: हम इंडस्ट्रियल IoT और फैक्ट्री ऑटोमेशन कंपनी हैं।

उत्पाद: स्मार्ट एनर्जी मीटर, IoT सेंसर, रिमोट मॉनिटरिंग, प्रेडिक्टिव मेंटेनेंस, स्मार्ट फ्लो मीटर।

हम भारत और UAE में काम करते हैं।


बातचीत के नियम:

- हमेशा हिंदी में बोलें

- हर जवाब 2 वाक्य में दें — पहला जानकारी, दूसरा ग्राहक से सवाल

- ग्राहक जो पूछे उसका पूरा जवाब दें, बिना काटे सुनें

- स्वाभाविक रूप से बोलें, जल्दबाजी न करें

- ग्राहक के बोलने के बाद ही बोलें

- अंत में डेमो शेड्यूल करने की कोशिश करें

- अगर रुचि नहीं है तो विनम्रता से कॉल समाप्त करें"""



async def create_session(

    agent_id=None,

    call_id=None,

    from_number=None,

    to_number=None,

    **kwargs,

):

    """

    Called by PIOPIY Agent for every answered call.

    Reads LiveKit credentials from context vars set by piopiy-ai,

    then runs a full Pipecat pipeline — same as Exotel implementation.

    """

    logger.info(f"📞 Call | call_id={call_id} | from={from_number} | to={to_number}")


    # ── Read LiveKit connection details from piopiy-ai context ─

    try:

        lk_url   = URL_CTX.get()

        lk_token = TOKEN_CTX.get()

        lk_room  = ROOM_CTX.get()

        logger.info(f"   LiveKit URL  : {lk_url}")

        logger.info(f"   LiveKit room : {lk_room}")

    except LookupError:

        logger.error("❌ LiveKit context vars not set — cannot start pipeline")

        return


    # ── Personalized greeting ──────────────────────────────────

    metadata      = kwargs.get("metadata") or {}

    customer_name = metadata.get("customer_name", "").strip()


    if customer_name:

        greeting = (

            f"नमस्ते {customer_name} जी! "

            "मैं प्रिया बोल रही हूँ म्यूटेक ऑटोमेशन से। "

            "हम इंडस्ट्रियल IoT सेंसर और फैक्ट्री ऑटोमेशन के समाधान देते हैं। "

            "क्या आपके पास एक मिनट है?"

        )

    else:

        greeting = (

            "नमस्ते! मैं प्रिया बोल रही हूँ म्यूटेक ऑटोमेशन से। "

            "हम इंडस्ट्रियल IoT सेंसर और फैक्ट्री ऑटोमेशन के समाधान देते हैं। "

            "क्या आपके पास एक मिनट है?"

        )


    try:

        # ── LiveKit transport ──────────────────────────────────

        transport = LiveKitTransport(

            url=lk_url,

            token=lk_token,

            room_name=lk_room,

            params=LiveKitParams(

                audio_in_enabled=True,

                audio_out_enabled=True,

                vad_enabled=True,

                vad_analyzer=SileroVADAnalyzer(),

                vad_audio_passthrough=True,

            ),

        )


        # ── Groq LLM ──────────────────────────────────────────

        llm = GroqLLMService(

            api_key=os.getenv("GROQ_API_KEY"),

            model="llama-3.3-70b-versatile",

        )


        # ── Sarvam STT (Hindi) ─────────────────────────────────

        stt = SarvamSTTService(

            api_key=os.getenv("SARVAM_API_KEY"),

            model="saarika:v2.5",

            params=SarvamSTTService.InputParams(

                language=Language.HI_IN,

                vad_signals=True,

            ),

        )


        # ── Sarvam TTS (Hindi) ─────────────────────────────────

        # Language.HI is correct for bulbul:v2 (NOT HI_IN)

        # pace=1.0 is natural speed

        tts = SarvamTTSService(

            api_key=os.getenv("SARVAM_API_KEY"),

            model="bulbul:v2",

            voice_id="anushka",

            params=SarvamTTSService.InputParams(

                language=Language.HI,

                pace=1.0,

                min_buffer_size=20,

            ),

        )


        # ── LLM context with greeting ──────────────────────────

        messages = [

            {"role": "system",    "content": SYSTEM_PROMPT},

            {"role": "assistant", "content": greeting},

        ]

        context            = OpenAILLMContext(messages)

        context_aggregator = llm.create_context_aggregator(context)


        # ── Greeting trigger — speak only after customer speaks ─

        from pipecat.frames.frames import UserStartedSpeakingFrame

        from pipecat.processors.frame_processor import FrameProcessor


        class GreetingTrigger(FrameProcessor):

            def __init__(self):

                super().__init__()

                self._greeted = False


            async def process_frame(self, frame, direction):

                await super().process_frame(frame, direction)

                if isinstance(frame, UserStartedSpeakingFrame) and not self._greeted:

                    self._greeted = True

                    logger.info("Customer spoke — sending greeting")

                    await task.queue_frames([context_aggregator.user().get_context_frame()])

                await self.push_frame(frame, direction)


        greeting_trigger = GreetingTrigger()


        # ── Pipeline ───────────────────────────────────────────

        pipeline = Pipeline([

            transport.input(),

            greeting_trigger,

            stt,

            context_aggregator.user(),

            llm,

            tts,

            transport.output(),

            context_aggregator.assistant(),

        ])


        task = PipelineTask(

            pipeline,

            params=PipelineParams(

                allow_interruptions=True,

                enable_metrics=True,

            ),

        )


        @transport.event_handler("on_client_connected")

        async def on_connected(transport, client):

            logger.info(f"✅ LiveKit connected | room={lk_room}")


        @transport.event_handler("on_client_disconnected")

        async def on_disconnected(transport, client):

            logger.info(f"📴 LiveKit disconnected | room={lk_room}")

            await task.cancel()


        logger.info(f"✅ Starting Pipecat pipeline | room={lk_room}")

        runner = PipelineRunner()

        await runner.run(task)


    except asyncio.CancelledError:

        logger.info(f"Session cancelled | call_id={call_id}")

    except Exception as e:

        logger.error(f"Pipeline error | call_id={call_id} | {e}", exc_info=True)



async def main():

    agent_id    = os.getenv("PIOPIY_AGENT_ID")

    agent_token = os.getenv("PIOPIY_AGENT_TOKEN")


    if not agent_id or not agent_token:

        raise RuntimeError("PIOPIY_AGENT_ID and PIOPIY_AGENT_TOKEN must be set in .env")


    logger.info("🚀 Starting Priya — PIOPIY + Pipecat Agent")

    logger.info(f"   Agent ID : {agent_id}")

    logger.info(f"   Pipeline : Silero VAD → Sarvam STT (hi-IN) → Groq LLM → Sarvam TTS (HI)")

    logger.info(f"   Transport: LiveKit (via PIOPIY signaling)")


    agent = Agent(

        agent_id=agent_id,

        agent_token=agent_token,

        create_session=create_session,

        debug=True,

    )


    logger.info("📡 Connecting to PIOPIY signaling server…")

    await agent.connect()



if __name__ == "__main__":

    try:

        asyncio.run(main())

    except KeyboardInterrupt:

        logger.info("🛑 Agent stopped by user")

    except Exception as e:

        logger.error(f"❌ Fatal: {e}", exc_info=True)
