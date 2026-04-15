"""
app/exotel_pipeline.py
Pipecat pipeline for Exotel WebSocket
Uses: ExotelFrameSerializer + Silero VAD + Sarvam STT + Groq LLM + Sarvam TTS

CHANGES vs original:
- Imports database module
- Creates call record on connect (create_call)
- Uses Groq to generate summary + sentiment after call ends
- Saves summary/outcome to DB (complete_call)
- Updates lead status in DB
- Adds system log entry
"""

import os
import time
import json
from loguru import logger
from dotenv import load_dotenv
from fastapi import WebSocket

from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.runner import PipelineRunner
from pipecat.pipeline.task import PipelineParams, PipelineTask
from pipecat.processors.aggregators.openai_llm_context import OpenAILLMContext
from pipecat.serializers.exotel import ExotelFrameSerializer
from pipecat.services.groq.llm import GroqLLMService
from pipecat.services.sarvam.stt import SarvamSTTService
from pipecat.services.sarvam.tts import SarvamTTSService
from pipecat.transcriptions.language import Language
from pipecat.transports.websocket.fastapi import (
    FastAPIWebsocketParams,
    FastAPIWebsocketTransport,
)

from app import database as db

load_dotenv()

SYSTEM_PROMPT = """आप आइरा हैं, म्यूटेक ऑटोमेशन की पेशेवर सेल्स एजेंट हैं।

म्यूटेक ऑटोमेशन के बारे में: हम इंडस्ट्रियल IoT और फैक्ट्री ऑटोमेशन कंपनी हैं। हमारे उत्पाद: स्मार्ट एनर्जी मीटर, IoT सेंसर, रिमोट मॉनिटरिंग, प्रेडिक्टिव मेंटेनेंस। हम भारत और UAE में काम करते हैं।

बातचीत का तरीका:
- हमेशा हिंदी में बोलें
- हर जवाब 2 वाक्य में दें — पहला जानकारी, दूसरा ग्राहक से सवाल
- बिना रुके स्वाभाविक रूप से बोलें
- ग्राहक जो पूछे उसका पूरा जवाब दें
- अंत में डेमो शेड्यूल करने की कोशिश करें
- अगर रुचि नहीं है तो विनम्रता से कॉल समाप्त करें"""


# ─────────────────────────────────────────────────────────────
# POST-CALL ANALYSIS via Groq
# ─────────────────────────────────────────────────────────────
async def analyze_call(conversation: list) -> dict:
    """
    Send full conversation to Groq and get back:
      - summary (2-3 sentences in English)
      - outcome: answered | no_answer | failed
      - sentiment: interested | neutral | rejected | demo_booked
      - lead_status: new | called | interested | demo_booked | not_interested
    """
    if not conversation:
        return {
            "summary": "Call ended with no conversation.",
            "outcome": "no_answer",
            "sentiment": "neutral",
            "lead_status": "called",
        }

    # Build readable transcript for analysis
    transcript_text = "\n".join(
        f"{m['role'].upper()}: {m['content']}"
        for m in conversation
        if m['role'] in ('user', 'assistant') and m.get('content')
    )

    if not transcript_text.strip():
        return {
            "summary": "Call connected but no speech detected.",
            "outcome": "no_answer",
            "sentiment": "neutral",
            "lead_status": "called",
        }

    prompt = f"""You are analyzing a Hindi sales call between Aira (AI sales agent for MuTech Automation) and a customer.

CONVERSATION:
{transcript_text}

Analyze this conversation and respond ONLY with a JSON object (no markdown, no explanation):
{{
  "summary": "Point-wise summary as bullet points (max 3 bullets, one line each)",
  "outcome": "answered",
  "sentiment": "one of: interested | neutral | rejected | demo_booked",
  "lead_status": "one of: called | interested | demo_booked | not_interested"
}}

Summary format: "• Customer concern: [key issue] • Aira response: [solution offered] • Outcome: [result/sentiment]"

Rules:
- outcome is always "answered" if there was any conversation
- sentiment = "demo_booked" if customer agreed to a demo
- sentiment = "interested" if customer showed interest but no demo yet
- sentiment = "rejected" if customer clearly not interested
- sentiment = "neutral" if unclear
- lead_status maps directly from sentiment (interested→interested, demo_booked→interested, rejected→not_interested, neutral→called)"""

    try:
        from groq import Groq
        client = Groq(api_key=os.getenv("GROQ_API_KEY"))
        response = client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[{"role": "user", "content": prompt}],
            max_tokens=200,
            temperature=0.1,
        )
        raw = response.choices[0].message.content.strip()
        # Strip markdown fences if present
        raw = raw.replace("```json", "").replace("```", "").strip()
        result = json.loads(raw)
        return result
    except Exception as e:
        logger.error(f"Call analysis error: {e}")
        return {
            "summary": "Call completed. Analysis unavailable.",
            "outcome": "answered",
            "sentiment": "neutral",
            "lead_status": "called",
        }


# ─────────────────────────────────────────────────────────────
# MAIN PIPELINE
# ─────────────────────────────────────────────────────────────
async def run_exotel_pipeline(websocket: WebSocket, lead: dict = None):
    """Run Pipecat pipeline for Exotel WebSocket call."""

    lead_name   = lead.get("name", "")    if lead else ""
    lead_phone  = lead.get("phone", "")   if lead else ""
    lead_company= lead.get("company", "") if lead else ""
    lead_id     = lead.get("id")          if lead else None
    campaign_id = lead.get("campaign_id") if lead else None

    # ── Wait for Exotel 'start' event ──────────────────────
    stream_sid = None
    call_sid   = None

    while True:
        msg  = await websocket.receive_text()
        data = json.loads(msg)
        event = data.get("event")
        if event == "connected":
            logger.info("Exotel connected, waiting for start...")
            continue
        elif event == "start":
            stream_sid = (data.get("stream_sid") or
                          data.get("start", {}).get("stream_sid") or
                          data.get("start", {}).get("streamSid", "stream"))
            call_sid   = data.get("start", {}).get("call_sid", "")
            logger.info(f"Stream started: {stream_sid} | call_sid: {call_sid}")
            break

    # ── Create call record in DB ────────────────────────────
    call_start = time.time()
    call_db_id = db.create_call(
        phone       = lead_phone or "unknown",
        lead_name   = lead_name,
        company     = lead_company,
        lead_id     = lead_id,
        campaign_id = campaign_id,
        call_sid    = call_sid,
    )
    db.add_log(f"📞 Call started — {lead_name or lead_phone} | call_id={call_db_id}")
    logger.info(f"DB call record created: call_id={call_db_id}")

    # ── Pipecat transport setup ─────────────────────────────
    serializer = ExotelFrameSerializer(stream_sid=stream_sid, call_sid=call_sid)

    transport = FastAPIWebsocketTransport(
        websocket=websocket,
        params=FastAPIWebsocketParams(
            audio_in_enabled=True,
            audio_out_enabled=True,
            add_wav_header=False,
            vad_enabled=True,
            vad_analyzer=SileroVADAnalyzer(),
            vad_audio_passthrough=True,
            serializer=serializer,
        ),
    )

    # ── Services ────────────────────────────────────────────
    llm = GroqLLMService(
        api_key=os.getenv("GROQ_API_KEY"),
        model="llama-3.3-70b-versatile",
    )

    stt = SarvamSTTService(
        api_key=os.getenv("SARVAM_API_KEY"),
        model="saarika:v2.5",
        params=SarvamSTTService.InputParams(
            language=Language.HI_IN,
            vad_signals=True,
        ),
    )

    tts = SarvamTTSService(
        api_key=os.getenv("SARVAM_API_KEY"),
        model="bulbul:v2",
        voice_id="anushka",
        params=SarvamTTSService.InputParams(
            language=Language.HI,
            pace=1.0,
        ),
    )

    # ── LLM context with greeting ───────────────────────────
    greeting = (
        f"नमस्ते{', ' + lead_name if lead_name else ''}! "
        "मैं आइरा बोल रही हूँ म्यूटेक ऑटोमेशन से। "
        "हम इंडस्ट्रियल IoT सेंसर, फैक्ट्री ऑटोमेशन, और स्मार्ट एनर्जी मैनेजमेंट के समाधान देते हैं। "
        "आपकी फैक्ट्री में ऑटोमेशन या एनर्जी की कोई चुनौती है क्या?"
    )

    messages = [
        {"role": "system",    "content": SYSTEM_PROMPT},
        {"role": "assistant", "content": greeting},
    ]

    context            = OpenAILLMContext(messages)
    context_aggregator = llm.create_context_aggregator(context)

    # ── Greeting trigger processor ──────────────────────────
    from pipecat.frames.frames import UserStartedSpeakingFrame
    from pipecat.processors.frame_processor import FrameProcessor, FrameDirection

    class GreetingTrigger(FrameProcessor):
        def __init__(self):
            super().__init__()
            self._greeted = False

        async def process_frame(self, frame, direction):
            await super().process_frame(frame, direction)
            if isinstance(frame, UserStartedSpeakingFrame) and not self._greeted:
                self._greeted = True
                logger.info("Customer started speaking — triggering greeting")
                await task.queue_frames([context_aggregator.user().get_context_frame()])
            await self.push_frame(frame, direction)

    greeting_trigger = GreetingTrigger()

    # ── Build pipeline ──────────────────────────────────────
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
            audio_in_sample_rate=8000,
            audio_out_sample_rate=8000,
        ),
    )

    @transport.event_handler("on_client_connected")
    async def on_connected(transport, client):
        logger.info("Client connected — waiting for customer to speak first")

    @transport.event_handler("on_client_disconnected")
    async def on_disconnected(transport, client):
        logger.info("Client disconnected")
        await task.cancel()

    # ── Run pipeline ────────────────────────────────────────
    runner = PipelineRunner()
    await runner.run(task)

    # ── Post-call: save to DB ───────────────────────────────
    duration_sec = int(time.time() - call_start)

    # Get full conversation from context
    conversation = context.messages if hasattr(context, 'messages') else messages

    # Build plain-text transcript for storage
    transcript_lines = []
    for m in conversation:
        role = m.get('role', '')
        content = m.get('content', '')
        if role in ('user', 'assistant') and content:
            label = 'Aira' if role == 'assistant' else 'Customer'
            transcript_lines.append(f"{label}: {content}")
    transcript_text = "\n".join(transcript_lines) if transcript_lines else None

    logger.info(f"Call ended — duration={duration_sec}s | analyzing...")
    analysis = await analyze_call(conversation)

    db.complete_call(
        call_id      = call_db_id,
        duration_sec = duration_sec,
        outcome      = analysis.get("outcome", "answered"),
        sentiment    = analysis.get("sentiment", "neutral"),
        summary      = analysis.get("summary", ""),
        transcript   = transcript_text,
    )

    # Schedule follow-up if enabled for this campaign
    from app.follow_up_service import schedule_call_follow_up
    await schedule_call_follow_up(call_db_id)

    # Update lead status if we have a lead
    if lead_id:
        db.update_lead(lead_id, status=analysis.get("lead_status", "called"))

    # Update campaign counters
    if campaign_id:
        db.increment_campaign_calls(
            campaign_id,
            answered = analysis.get("outcome") == "answered",
            demo     = analysis.get("sentiment") == "demo_booked",
        )

    db.add_log(
        f"✅ Call completed — {lead_name or lead_phone} | "
        f"{duration_sec}s | {analysis.get('sentiment','neutral')} | "
        f"{analysis.get('summary','')[:80]}"
    )
    logger.info(f"DB updated: call_id={call_db_id} | {analysis}")
