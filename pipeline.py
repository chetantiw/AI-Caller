"""
app/pipeline.py
Core Pipecat voice pipeline: PIOPIY WebSocket -> STT (Sarvam) -> LLM (GPT-4o) -> TTS (Sarvam) -> PIOPIY

PIOPIY streams raw mulaw/PCM audio over a plain WebSocket TCP connection.
We handle the WebSocket manually and plug into Pipecat's pipeline directly.
"""

import os
import json
import base64
import asyncio
import websockets
from loguru import logger
from dotenv import load_dotenv

from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.runner import PipelineRunner
from pipecat.pipeline.task import PipelineParams, PipelineTask
from pipecat.transports.network.fastapi_websocket import (
    FastAPIWebsocketTransport,
    FastAPIWebsocketParams,
)
from pipecat.services.openai import OpenAILLMService
from pipecat.services.sarvam import SarvamTTSService, SarvamSTTService
from pipecat.processors.aggregators.openai_llm_context import OpenAILLMContext
from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.frames.frames import AudioRawFrame, EndFrame

load_dotenv()

SARVAM_LANG_MAP = {
    "en": "en-IN", "hi": "hi-IN", "mr": "mr-IN",
    "gu": "gu-IN", "ta": "ta-IN", "te": "te-IN",
    "kn": "kn-IN", "bn": "bn-IN", "pa": "pa-IN", "ml": "ml-IN",
}

SARVAM_VOICE_MAP = {
    "en-IN": "anushka", "hi-IN": "anushka", "mr-IN": "anushka",
    "gu-IN": "anushka", "ta-IN": "anushka", "te-IN": "anushka",
}


def load_system_prompt() -> str:
    prompt_path = os.path.join(os.path.dirname(__file__), "../prompts/sales_agent.txt")
    try:
        with open(prompt_path, "r") as f:
            return f.read()
    except FileNotFoundError:
        logger.warning("Prompt file not found, using default.")
        return "You are a helpful sales assistant. Be concise and friendly."


async def run_pipeline(websocket, lead: dict = None):
    """
    Run full voice AI pipeline for a PIOPIY WebSocket connection.

    PIOPIY sends raw binary audio frames over WebSocket.
    We receive audio -> STT -> LLM -> TTS -> send audio back.

    Args:
        websocket : FastAPI WebSocket from PIOPIY audio stream
        lead      : Dict with prospect info {name, company, language}
    """

    lead_name     = lead.get("name", "there")  if lead else "there"
    lead_company  = lead.get("company", "")    if lead else ""
    lead_language = lead.get("language", "en") if lead else "en"

    sarvam_lang  = SARVAM_LANG_MAP.get(lead_language, "en-IN")
    sarvam_voice = SARVAM_VOICE_MAP.get(sarvam_lang, "anushka")

    logger.info(f"Pipeline starting | Lead: {lead_name} @ {lead_company} | Lang: {sarvam_lang}")

    # Personalized system prompt
    system_prompt = load_system_prompt()
    personalized_prompt = f"""
{system_prompt}

## Current Call Info
- Prospect Name     : {lead_name}
- Company           : {lead_company}
- Preferred Language: {"Hindi" if lead_language == "hi" else "English (Indian accent)"}
- Speak naturally with Indian cultural context.
- Keep each response under 3 sentences for natural conversation flow.
- You are calling via phone — be concise and respect the prospect's time.
"""

    # ── 1. TRANSPORT — FastAPI WebSocket (PIOPIY streams raw audio) ───────────
    transport = FastAPIWebsocketTransport(
        websocket=websocket,
        params=FastAPIWebsocketParams(
            audio_in_enabled=True,
            audio_out_enabled=True,
            add_wav_header=False,
            vad_analyzer=SileroVADAnalyzer(),
            audio_in_sample_rate=8000,
            audio_out_sample_rate=8000,
        ),
    )

    # ── 2. STT — Sarvam Speech to Text ───────────────────────────────────────
    stt = SarvamSTTService(
        api_key=os.getenv("SARVAM_API_KEY"),
        language_code=sarvam_lang,
        model=os.getenv("SARVAM_STT_MODEL", "saarika:v2"),
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
        model=os.getenv("SARVAM_TTS_MODEL", "bulbul:v2"),
        output_format="wav",
        sample_rate=8000,
    )

    # ── 5. LLM Context ───────────────────────────────────────────────────────
    messages = [
        {"role": "system", "content": personalized_prompt},
        {"role": "user", "content": "The call just connected. Start your introduction now."},
    ]
    context = OpenAILLMContext(messages)
    context_aggregator = llm.create_context_aggregator(context)

    # ── 6. Pipeline ──────────────────────────────────────────────────────────
    pipeline = Pipeline([
        transport.input(),               # Audio in from PIOPIY
        stt,                             # Speech -> Text (Sarvam)
        context_aggregator.user(),       # Add to conversation
        llm,                             # GPT-4o response
        tts,                             # Text -> Speech (Sarvam)
        transport.output(),              # Audio out to PIOPIY
        context_aggregator.assistant(),  # Save turn
    ])

    task = PipelineTask(
        pipeline,
        params=PipelineParams(
            allow_interruptions=True,
            enable_metrics=True,
        ),
    )

    runner = PipelineRunner()

    @transport.event_handler("on_client_connected")
    async def on_connected(transport, client):
        logger.info(f"PIOPIY stream connected | {lead_name}")
        await task.queue_frames([context_aggregator.user().get_context_frame()])

    @transport.event_handler("on_client_disconnected")
    async def on_disconnected(transport, client):
        logger.info(f"PIOPIY stream disconnected | {lead_name}")
        await task.cancel()

    await runner.run(task)
