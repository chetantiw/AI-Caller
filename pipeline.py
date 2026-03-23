"""
app/pipeline.py
Core Pipecat voice pipeline: Twilio → STT (Sarvam) → LLM (GPT-4o) → TTS (Sarvam) → Twilio
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
from pipecat.services.sarvam import SarvamTTSService, SarvamSTTService
from pipecat.processors.aggregators.openai_llm_context import OpenAILLMContext
from pipecat.audio.vad.silero import SileroVADAnalyzer

load_dotenv()

# ── Sarvam Language Code Map ──────────────────────────────────────────────────
SARVAM_LANG_MAP = {
    "en":    "en-IN",   # English (India)
    "hi":    "hi-IN",   # Hindi
    "mr":    "mr-IN",   # Marathi
    "gu":    "gu-IN",   # Gujarati
    "ta":    "ta-IN",   # Tamil
    "te":    "te-IN",   # Telugu
    "kn":    "kn-IN",   # Kannada
    "bn":    "bn-IN",   # Bengali
    "pa":    "pa-IN",   # Punjabi
    "ml":    "ml-IN",   # Malayalam
}

# ── Sarvam Voice Map (per language) ──────────────────────────────────────────
SARVAM_VOICE_MAP = {
    "en-IN": "anushka",
    "hi-IN": "anushka",
    "mr-IN": "anushka",
    "gu-IN": "anushka",
    "ta-IN": "anushka",
    "te-IN": "anushka",
}


def load_system_prompt() -> str:
    """Load the sales agent prompt from file."""
    prompt_path = os.path.join(os.path.dirname(__file__), "../prompts/sales_agent.txt")
    try:
        with open(prompt_path, "r") as f:
            return f.read()
    except FileNotFoundError:
        logger.warning("Prompt file not found, using default.")
        return "You are a helpful sales assistant. Be concise and friendly."


async def run_pipeline(websocket, lead: dict = None):
    """
    Run the full voice pipeline for a single call.

    Args:
        websocket : WebSocket connection from Twilio Media Streams
        lead      : Dict with prospect info {name, company, designation, language}
    """

    # ── Lead info ─────────────────────────────────────────────────────────────
    lead_name     = lead.get("name", "there")    if lead else "there"
    lead_company  = lead.get("company", "")      if lead else ""
    lead_language = lead.get("language", "en")   if lead else "en"

    sarvam_lang   = SARVAM_LANG_MAP.get(lead_language, "en-IN")
    sarvam_voice  = SARVAM_VOICE_MAP.get(sarvam_lang, "anushka")

    # ── Personalized system prompt ────────────────────────────────────────────
    system_prompt = load_system_prompt()
    personalized_prompt = f"""
{system_prompt}

## Current Call Info
- Prospect Name     : {lead_name}
- Company           : {lead_company}
- Preferred Language: {"Hindi" if lead_language == "hi" else "English (Indian accent)"}
- Speak naturally with Indian cultural context.
- Keep each response under 3 sentences for natural conversation flow.
"""

    logger.info(f"Starting pipeline | Lead: {lead_name} @ {lead_company} | Lang: {sarvam_lang}")

    # ── 1. TRANSPORT — Twilio Media Streams ──────────────────────────────────
    transport = TwilioTransport(
        websocket=websocket,
        params=TwilioParams(
            audio_in_sample_rate=8000,
            audio_out_sample_rate=8000,
            add_wav_header=False,
            vad_analyzer=SileroVADAnalyzer(),
        ),
    )

    # ── 2. STT — Sarvam Speech to Text ───────────────────────────────────────
    stt = SarvamSTTService(
        api_key=os.getenv("SARVAM_API_KEY"),
        language_code=sarvam_lang,
        model="saarika:v2",              # Sarvam multilingual STT
    )

    # ── 3. LLM — OpenAI GPT-4o ───────────────────────────────────────────────
    llm = OpenAILLMService(
        api_key=os.getenv("OPENAI_API_KEY"),
        model=os.getenv("OPENAI_MODEL", "gpt-4o"),
    )

    # ── 4. TTS — Sarvam Text to Speech ───────────────────────────────────────
    tts = SarvamTTSService(
        api_key=os.getenv("SARVAM_API_KEY"),
        language_code=sarvam_lang,
        speaker=sarvam_voice,
        model="bulbul:v2",               # Sarvam multilingual TTS
        output_format="wav",
        sample_rate=8000,                # Twilio-compatible
    )

    # ── 5. LLM CONTEXT — Conversation history ────────────────────────────────
    messages = [
        {"role": "system", "content": personalized_prompt},
        {
            "role": "user",
            "content": "The call has just connected. Start with your introduction now.",
        },
    ]
    context = OpenAILLMContext(messages)
    context_aggregator = llm.create_context_aggregator(context)

    # ── 6. BUILD PIPELINE ─────────────────────────────────────────────────────
    pipeline = Pipeline(
        [
            transport.input(),              # 🎙️ Audio in from Twilio
            stt,                            # 🔤 Speech → Text (Sarvam)
            context_aggregator.user(),      # 📝 Add to conversation context
            llm,                            # 🧠 GPT-4o generates response
            tts,                            # 🔊 Text → Speech (Sarvam)
            transport.output(),             # 📞 Audio out to Twilio
            context_aggregator.assistant(), # 💾 Save assistant turn
        ]
    )

    # ── 7. PIPELINE TASK ──────────────────────────────────────────────────────
    task = PipelineTask(
        pipeline,
        params=PipelineParams(
            allow_interruptions=True,       # Prospect can interrupt agent
            enable_metrics=True,
        ),
    )

    runner = PipelineRunner()

    # ── 8. EVENT HANDLERS ────────────────────────────────────────────────────
    @transport.event_handler("on_client_connected")
    async def on_connected(transport, client):
        logger.info(f"✅ Call connected: {lead_name}")
        await task.queue_frames([context_aggregator.user().get_context_frame()])

    @transport.event_handler("on_client_disconnected")
    async def on_disconnected(transport, client):
        logger.info(f"📴 Call ended: {lead_name}")
        await task.cancel()

    # ── 9. RUN ────────────────────────────────────────────────────────────────
    await runner.run(task)