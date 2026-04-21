"""
app/piopiy_agent.py

PIOPIY VoiceAgent — Aira Hindi Sales Agent

Uses piopiy.voice_agent.VoiceAgent (handles inbound + outbound automatically)

Pipeline: Sarvam STT (HI_IN) → Groq LLM → Sarvam TTS (HI)

"""

import asyncio
import os
import time

from dotenv import load_dotenv

load_dotenv()

# ── piopiy-ai SDK ─────────────────────────────────────────────
from piopiy.agent import Agent
from piopiy.voice_agent import VoiceAgent
from piopiy.services.sarvam.stt import SarvamSTTService
from piopiy.services.sarvam.tts import SarvamTTSService
from piopiy.services.groq.llm import GroqLLMService
from piopiy.transcriptions.language import Language
from piopiy.turns.user_start.vad_user_turn_start_strategy import VADUserTurnStartStrategy
from piopiy.adapters.schemas.tools_schema import ToolsSchema
from piopiy.adapters.schemas.function_schema import FunctionSchema

# ── DB ────────────────────────────────────────────────────────
import sys
sys.path.insert(0, '/root/ai-caller-env/ai-caller')
from app import database as db

from app.telegram_notify import (
    notify_demo_booked,
    notify_interested,
    notify_call_completed,
    notify_service_started,
    notify_error,
)


# ── Logging — must be configured AFTER any piopiy imports
#    (Agent.__init__ wipes loguru handlers when debug=False)
#    We pass debug=True to Agent so handlers are preserved.
from loguru import logger
os.makedirs("logs", exist_ok=True)
logger.add("logs/piopiy_agent.log", rotation="100 MB", level="DEBUG", retention="7 days")


SYSTEM_PROMPT = """आप आइरा हैं, म्यूटेक ऑटोमेशन की पेशेवर सेल्स एजेंट हैं।

म्यूटेक ऑटोमेशन के बारे में: हम इंडस्ट्रियल IoT और फैक्ट्री ऑटोमेशन कंपनी हैं।
हमारे उत्पाद: स्मार्ट एनर्जी मीटरिंग, स्मार्ट वॉटर मीटरिंग, प्रेडिक्टिव मेंटेनेंस, बिल्डिंग ऑटोमेशन, इंडस्ट्रियल IoT सेंसर।
हम भारत और UAE में काम करते हैं।

बातचीत के नियम:
- हमेशा हिंदी में बोलें
- हर जवाब 2 वाक्य में दें — पहला जानकारी, दूसरा ग्राहक से सवाल
- स्वाभाविक रूप से बोलें
- ग्राहक जो पूछे उसका पूरा जवाब दें
- अंत में डेमो शेड्यूल करने की कोशिश करें
- रुचि नहीं है तो विनम्रता से कॉल समाप्त करें"""


# ── Our own virtual number digits (used to detect inbound direction)
_OWN_NUMBER_DIGITS = "".join(
    c for c in os.getenv("PIOPIY_NUMBER", os.getenv("PIOPIY_CALLER_ID", "")) if c.isdigit()
)



async def create_session(
    agent_id=None,
    call_id=None,
    from_number=None,
    to_number=None,
    metadata=None,
    **kwargs,
):
    logger.info(f"📞 Call | call_id={call_id} | from={from_number} | to={to_number}")

    # ── Detect call direction ─────────────────────────────────
    # Outbound: from_number = our number, to_number = customer
    # Inbound : from_number = customer,   to_number = our number
    _from_digits = "".join(c for c in str(from_number or "") if c.isdigit())
    is_inbound = bool(_OWN_NUMBER_DIGITS and _OWN_NUMBER_DIGITS in _from_digits) is False and \
                 bool(_OWN_NUMBER_DIGITS and _OWN_NUMBER_DIGITS in "".join(c for c in str(to_number or "") if c.isdigit()))

    customer_phone = str(from_number if is_inbound else to_number or from_number or "unknown")
    logger.info(f"   Direction : {'INBOUND' if is_inbound else 'OUTBOUND'} | customer_phone={customer_phone}")

    # ── DB: record call start ─────────────────────────────────
    metadata      = metadata or {}
    logger.info(f"   Received metadata: {metadata}")  # DEBUG
    lead_id_str   = metadata.get("lead_id", "")
    lead_id_db    = int(lead_id_str) if str(lead_id_str).isdigit() else None
    lead_obj      = db.get_lead(lead_id_db) if lead_id_db else None
    customer_name = metadata.get("customer_name", "").strip()
    if not customer_name and lead_obj:
        customer_name = lead_obj.get("name", "")

    company    = lead_obj.get("company", "") if lead_obj else ""
    tenant_id  = int(metadata.get("tenant_id", 1)) if str(metadata.get("tenant_id", 1)).isdigit() else 1
    logger.info(f"   Extracted tenant_id={tenant_id} from metadata")  # DEBUG
    call_start = time.time()
    call_db_id = db.create_call(
        phone       = customer_phone,
        lead_name   = customer_name,
        company     = company,
        lead_id     = lead_id_db,
        campaign_id = lead_obj.get("campaign_id") if lead_obj else None,
        call_sid    = str(call_id or ""),
        tenant_id   = tenant_id,
    )
    db.add_log(f"📞 PIOPIY {'inbound' if is_inbound else 'outbound'} call started — {customer_phone} | call_db_id={call_db_id}")
    logger.info(f"DB call record created: call_db_id={call_db_id}")

    # ── Fetch tenant-specific system prompt & config ────────
    from app import tenant_db as tdb
    tenant_config = tdb.get_tenant_config(tenant_id) or {}
    system_prompt = tenant_config.get("system_prompt", "").strip() or SYSTEM_PROMPT
    faq_content = (tenant_config.get("faq_content") or "").strip()
    if faq_content:
        system_prompt += (
            "\n\n--- Frequently Asked Questions ---\n"
            "Use the following Q&A to answer customer questions accurately. "
            "If a customer asks something covered here, use this answer directly.\n\n"
            + faq_content
        )
    agent_name = tenant_config.get("agent_name", "Agent").strip()
    company_name = tenant_config.get("company_name", "Company").strip()
    call_language = tenant_config.get("call_language", "hindi").strip().lower()
    logger.info(f"   Using tenant config: agent={agent_name}, company={company_name}, lang={call_language}, prompt_len={len(system_prompt)}")

    # ── Greeting (tenant-specific) ─────────────────────────
    if call_language == "english":
        greeting = f"Hello{' ' + customer_name if customer_name else ''}! I'm {agent_name} from {company_name}."
    else:  # hindi or default
        greeting = f"नमस्ते{' ' + customer_name + ' जी' if customer_name else ''}! मैं {agent_name} हूँ {company_name} से।"

    # ── Services ──────────────────────────────────────────────
    stt = SarvamSTTService(
        api_key=os.getenv("SARVAM_API_KEY"),
        model="saarika:v2.5",
        params=SarvamSTTService.InputParams(
            language=Language.HI_IN,
            vad_signals=True,
            high_vad_sensitivity=True,
            mode="codemix",
        ),
    )

    llm = GroqLLMService(
        api_key=os.getenv("GROQ_API_KEY"),
        model="llama-3.3-70b-versatile",
    )

    tts = SarvamTTSService(
        api_key=os.getenv("SARVAM_API_KEY"),
        model="bulbul:v3",
        voice_id="kavya",
        params=SarvamTTSService.InputParams(
            language=Language.HI,
            pace=1.1,
            temperature=0.75,
            enable_preprocessing=True,
        ),
    )

    # ── VoiceAgent ────────────────────────────────────────────
    voice_agent = VoiceAgent(
        instructions=system_prompt,
        greeting=greeting,
        idle_timeout_secs=120,
    )

    _vad = {
        "stop_secs":  0.6,
        "start_secs": 0.1,
        "confidence": 0.65,
        "min_volume": 0.5,
    }

    _end_call_tool = FunctionSchema(
        name="end_call",
        description=(
            "Hang up and end this call immediately. "
            "Call this when: customer says bye/goodbye/band karo/rakhna, "
            "or says 'number hatao'/'DNC'/'do not call', "
            "or becomes abusive, or gives a legal threat, "
            "or says 'not interested' twice, "
            "or the conversation has naturally concluded."
        ),
        properties={},
        required=[],
    )
    _tools = ToolsSchema(standard_tools=[_end_call_tool])

    try:
        await voice_agent.Action(
            stt=stt,
            llm=llm,
            tts=tts,
            vad=True,
            allow_interruptions=True,
            mcp_tools=_tools,
        )
        await voice_agent.start()

    except asyncio.CancelledError:
        logger.info(f"Session cancelled | call_id={call_id}")

    except Exception as e:
        logger.error(f"VoiceAgent error | call_id={call_id} | {e}")
        await notify_error(f"VoiceAgent error on call {call_id}: {e}")

    finally:
        # ── Post-call: save to DB ─────────────────────────────
        try:
            duration_sec = int(time.time() - call_start)
            logger.info(f"Call ended — duration={duration_sec}s | analyzing...")
            from app.exotel_pipeline import analyze_call
            conversation = voice_agent._messages if hasattr(voice_agent, "_messages") else []

            # Format full transcript for storage
            transcript_lines = []
            for m in conversation:
                role = m.get('role', '').lower()
                content = m.get('content', '').strip()
                if content:
                    if role == 'assistant':
                        transcript_lines.append(f"Aira: {content}")
                    elif role == 'user':
                        transcript_lines.append(f"Customer: {content}")
            full_transcript = "\n".join(transcript_lines)

            analysis = await analyze_call(conversation)
            outcome  = analysis.get("outcome", "answered")
            sentiment = analysis.get("sentiment", "neutral")
            summary  = analysis.get("summary", "")
            db.complete_call(
                call_id      = call_db_id,
                duration_sec = duration_sec,
                outcome      = outcome,
                sentiment    = sentiment,
                summary      = summary,
                transcript   = full_transcript,
            )

            # Schedule follow-up if enabled for this campaign
            from app.follow_up_service import schedule_call_follow_up
            await schedule_call_follow_up(call_db_id)

            if lead_id_db:
                db.update_lead(lead_id_db, status=analysis.get("lead_status", "called"))
            db.add_log(
                f"✅ Call done — {customer_phone} | {duration_sec}s | "
                f"{sentiment} | {summary[:80]}"
            )
            logger.info(f"DB updated: call_db_id={call_db_id} | {analysis}")
            if outcome == "answered":
                if sentiment == "demo_booked":
                    await notify_demo_booked(lead_name=customer_name, company=company, phone=customer_phone, summary=summary)
                elif sentiment == "interested":
                    await notify_interested(lead_name=customer_name, company=company, phone=customer_phone, summary=summary)
                else:
                    await notify_call_completed(lead_name=customer_name, phone=customer_phone, duration_sec=duration_sec, sentiment=sentiment, summary=summary)
        except Exception as e:
            logger.error(f"Post-call DB error: {e}")


async def main():
    agent_id    = os.getenv("PIOPIY_AGENT_ID")
    agent_token = os.getenv("PIOPIY_AGENT_TOKEN")
    if not agent_id or not agent_token:
        raise RuntimeError("PIOPIY_AGENT_ID and PIOPIY_AGENT_TOKEN must be set")

    logger.info("🚀 Aira — PIOPIY VoiceAgent")
    logger.info("   Pipeline: Sarvam STT (HI_IN) → Groq LLM → Sarvam TTS (HI)")
    logger.info("   Handles: inbound + outbound automatically")
    logger.info(f"   Own number: {_OWN_NUMBER_DIGITS or '(not set)'}")
    logger.info("📡 Connecting to PIOPIY signaling server…")
    await notify_service_started()

    agent = Agent(
        agent_id=agent_id,
        agent_token=agent_token,
        create_session=create_session,
        debug=True,   # keeps loguru handlers + prints join_room payloads
    )

    # Re-add file logger after Agent() because debug=True resets loguru to INFO/stderr.
    # Adding again ensures we always have a persistent file log.
    logger.add("logs/piopiy_agent.log", rotation="100 MB", level="DEBUG", retention="7 days")

    await agent.connect()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("🛑 Agent stopped")
    except Exception as e:
        logger.error(f"❌ Fatal: {e}", exc_info=True)
