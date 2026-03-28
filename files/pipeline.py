"""
app/pipeline.py
Core Pipecat pipeline: PIOPIY WebSocket -> Sarvam STT -> OpenRouter LLM -> Sarvam TTS -> PIOPIY

Stack:
  STT : Sarvam saarika:v2              — Indian multilingual speech recognition
  LLM : OpenRouter (configurable free) — Default: Llama 3.3 70B (best for Hindi/English)
  TTS : Sarvam bulbul:v2               — Natural Indian voice output

Supported free models (set OPENROUTER_MODEL in .env):
  meta-llama/llama-3.3-70b-instruct:free  ← Default (best quality + Hindi support)
  stepfun/step-3.5-flash:free             ← Fastest (disable reasoning)
  arcee-ai/trinity-mini:free              ← Ultra fast (lower quality)
"""

import os
from loguru import logger
from dotenv import load_dotenv

from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.runner import PipelineRunner
from pipecat.pipeline.task import PipelineParams, PipelineTask
from pipecat.transports.websocket.fastapi import (
    FastAPIWebsocketTransport,
    FastAPIWebsocketParams,
)
from pipecat.services.sarvam.stt import SarvamSTTService
from pipecat.services.sarvam.tts import SarvamTTSService, SarvamTTSSpeakerV2
from pipecat.services.openai.llm import OpenAILLMService
from pipecat.processors.aggregators.openai_llm_context import OpenAILLMContext
from pipecat.audio.vad.silero import SileroVADAnalyzer

load_dotenv()

# ── Language maps ─────────────────────────────────────────────────────────────
SARVAM_LANG_MAP = {
    "en": "en-IN",
    "hi": "hi-IN",
    "mr": "mr-IN",
    "gu": "gu-IN",
    "ta": "ta-IN",
    "te": "te-IN",
    "kn": "kn-IN",
    "bn": "bn-IN",
    "pa": "pa-IN",
    "ml": "ml-IN",
}

# Sarvam V2 speakers: anushka, abhilash, manisha, vidya, arya, karun, hitesh
SARVAM_SPEAKER = SarvamTTSSpeakerV2.ANUSHKA


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
    Full voice AI pipeline for a PIOPIY WebSocket call.

    LLM is configured via OPENROUTER_MODEL env variable — swap models without code changes.
    OpenRouter uses OpenAI-compatible API format.
    """

    lead_name     = lead.get("name", "there")  if lead else "there"
    lead_company  = lead.get("company", "")    if lead else ""
    lead_language = lead.get("language", "en") if lead else "en"
    sarvam_lang   = SARVAM_LANG_MAP.get(lead_language, "en-IN")

    # Get model from env — easy to swap without code changes
    llm_model = os.getenv("OPENROUTER_MODEL", "meta-llama/llama-3.3-70b-instruct:free")

    logger.info(f"Pipeline | Lead: {lead_name} @ {lead_company} | Lang: {sarvam_lang} | LLM: {llm_model}")

    # ── Personalized system prompt ────────────────────────────────────────────
    system_prompt = load_system_prompt()
    personalized_prompt = f"""
{system_prompt}

## Current Call Info
- Prospect Name     : {lead_name}
- Company           : {lead_company}
- Preferred Language: {"Hindi" if lead_language == "hi" else "English (Indian accent)"}
- Speak naturally with Indian cultural context.
- Keep each response under 3 sentences.
- Respond DIRECTLY. Do NOT show any thinking, reasoning, or internal monologue.
"""

    # ── 1. TRANSPORT — PIOPIY WebSocket ──────────────────────────────────────
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

    # ── 2. STT — Sarvam saarika:v2 ───────────────────────────────────────────
    stt = SarvamSTTService(
        api_key=os.getenv("SARVAM_API_KEY"),
        model=os.getenv("SARVAM_STT_MODEL", "saarika:v2"),
        mode="codemix",           # Handles Hindi-English switching naturally
        sample_rate=8000,
        input_audio_codec="wav",
    )

    # ── 3. LLM — OpenRouter (configurable free model) ────────────────────────
    # Uses OpenAI-compatible API — just different base_url
    # No reasoning tokens for voice — direct responses only
    llm = OpenAILLMService(
        api_key=os.getenv("OPENROUTER_API_KEY"),
        model=llm_model,
        base_url="https://openrouter.ai/api/v1",
        default_headers={
            "HTTP-Referer": os.getenv("PUBLIC_URL", "https://ai.mutechautomation.com"),
            "X-Title": os.getenv("COMPANY_NAME", "MuTech Automation"),
        },
    )

    # ── 4. TTS — Sarvam bulbul:v2 ────────────────────────────────────────────
    tts = SarvamTTSService(
        api_key=os.getenv("SARVAM_API_KEY"),
        model=os.getenv("SARVAM_TTS_MODEL", "bulbul:v2"),
        voice_id=SARVAM_SPEAKER.value,
        sample_rate=8000,
    )

    # ── 5. LLM Context ───────────────────────────────────────────────────────
    messages = [
        {"role": "system", "content": personalized_prompt},
        {"role": "user",   "content": "The call just connected. Start your introduction now."},
    ]
    context = OpenAILLMContext(messages)
    context_aggregator = llm.create_context_aggregator(context)

    # ── 6. Pipeline ──────────────────────────────────────────────────────────
    pipeline = Pipeline([
        transport.input(),
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

    runner = PipelineRunner()

    @transport.event_handler("on_client_connected")
    async def on_connected(transport, client):
        logger.info(f"✅ Call connected: {lead_name}")
        await task.queue_frames([context_aggregator.user().get_context_frame()])

    @transport.event_handler("on_client_disconnected")
    async def on_disconnected(transport, client):
        logger.info(f"📴 Call ended: {lead_name}")
        await task.cancel()

    await runner.run(task)
