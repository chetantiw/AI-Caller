"""
app/pipeline.py
Core Pipecat voice pipeline: Twilio → STT → LLM → TTS → Twilio
"""

import os
import asyncio
from loguru import logger
from dotenv import load_dotenv

from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.runner import PipelineRunner
from pipecat.pipeline.task import PipelineParams, PipelineTask

from pipecat.transports.services.twilio import TwilioTransport, TwilioParams
from pipecat.services.openai import OpenAILLMService
from pipecat.services.elevenlabs import ElevenLabsTTSService
from pipecat.services.deepgram import DeepgramSTTService

from pipecat.processors.aggregators.openai_llm_context import OpenAILLMContext
from pipecat.processors.audio.silero_vad_analyzer import SileroVADAnalyzer
from pipecat.audio.vad.silero import SileroVADAnalyzer as VAD

load_dotenv()


def load_system_prompt() -> str:
    """Load the sales agent prompt from file."""
    prompt_path = os.path.join(os.path.dirname(__file__), "../prompts/sales_agent.txt")
    try:
        with open(prompt_path, "r") as f:
            return f.read()
    except FileNotFoundError:
        logger.warning("Prompt file not found, using default prompt.")
        return "You are a helpful sales assistant. Be concise and friendly."


async def run_pipeline(websocket, lead: dict = None):
    """
    Run the full voice pipeline for a single call.

    Args:
        websocket: WebSocket connection from Twilio Media Streams
        lead: Dict with prospect info {name, company, designation, language}
    """

    lead_name = lead.get("name", "there") if lead else "there"
    lead_company = lead.get("company", "") if lead else ""
    lead_language = lead.get("language", "en") if lead else "en"

    system_prompt = load_system_prompt()

    # Personalize the prompt with lead info
    personalized_prompt = f"""
{system_prompt}

## Current Call Info
- Prospect Name: {lead_name}
- Company: {lead_company}
- Preferred Language: {"Hindi" if lead_language == "hi" else "English"}
- Start by asking for {lead_name} if they haven't confirmed yet.
"""

    logger.info(f"Starting pipeline for lead: {lead_name} @ {lead_company}")

    # ── 1. TRANSPORT — Twilio Media Streams ──────────────────────────────────
    transport = TwilioTransport(
        websocket=websocket,
        params=TwilioParams(
            audio_in_sample_rate=8000,
            audio_out_sample_rate=8000,
            add_wav_header=False,
            vad_analyzer=VAD(),
        ),
    )

    # ── 2. STT — Speech to Text (Deepgram) ───────────────────────────────────
    stt = DeepgramSTTService(
        api_key=os.getenv("DEEPGRAM_API_KEY"),
        language="hi-en" if lead_language == "hi" else "en-IN",  # Code-switch for India
    )

    # ── 3. LLM — OpenAI GPT-4o ───────────────────────────────────────────────
    llm = OpenAILLMService(
        api_key=os.getenv("OPENAI_API_KEY"),
        model=os.getenv("OPENAI_MODEL", "gpt-4o"),
    )

    # ── 4. TTS — ElevenLabs ──────────────────────────────────────────────────
    tts = ElevenLabsTTSService(
        api_key=os.getenv("ELEVENLABS_API_KEY"),
        voice_id=os.getenv("ELEVENLABS_VOICE_ID", "21m00Tcm4TlvDq8ikWAM"),
        model="eleven_turbo_v2",          # Lowest latency model
        output_format="ulaw_8000",        # Twilio-compatible format
    )

    # ── 5. LLM CONTEXT — Conversation history ────────────────────────────────
    messages = [
        {"role": "system", "content": personalized_prompt},
        {
            "role": "user",
            "content": "The call has just connected. Start with your introduction.",
        },
    ]
    context = OpenAILLMContext(messages)
    context_aggregator = llm.create_context_aggregator(context)

    # ── 6. BUILD PIPELINE ─────────────────────────────────────────────────────
    pipeline = Pipeline(
        [
            transport.input(),           # Audio in from Twilio
            stt,                         # Speech → Text
            context_aggregator.user(),   # Add user message to context
            llm,                         # Text → LLM response
            tts,                         # LLM text → Speech
            transport.output(),          # Audio out to Twilio
            context_aggregator.assistant(), # Save assistant response
        ]
    )

    # ── 7. RUN ────────────────────────────────────────────────────────────────
    task = PipelineTask(
        pipeline,
        params=PipelineParams(
            allow_interruptions=True,    # Prospect can interrupt agent
            enable_metrics=True,
        ),
    )

    runner = PipelineRunner()

    @transport.event_handler("on_client_connected")
    async def on_connected(transport, client):
        logger.info(f"Call connected: {lead_name}")
        await task.queue_frames([context_aggregator.user().get_context_frame()])

    @transport.event_handler("on_client_disconnected")
    async def on_disconnected(transport, client):
        logger.info(f"Call ended: {lead_name}")
        await task.cancel()

    await runner.run(task)
