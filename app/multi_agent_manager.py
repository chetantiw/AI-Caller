"""
app/multi_agent_manager.py

Multi-Tenant PIOPIY Agent Manager

Loads all active tenants from the DB, and for each tenant that has a valid
piopiy_agent_id + piopiy_agent_token, spins up a dedicated VoiceAgent
instance.  All agents run concurrently inside a single asyncio event loop.

Pipeline per tenant: Sarvam STT (HI_IN) → Groq LLM → Sarvam TTS (HI)
"""

import asyncio
import os
import sys
import time
from datetime import datetime

from dotenv import load_dotenv

load_dotenv()

sys.path.insert(0, "/root/ai-caller-env/ai-caller")

# ── piopiy-ai SDK ──────────────────────────────────────────────
from piopiy.agent import Agent
from piopiy.voice_agent import VoiceAgent
from piopiy.adapters.schemas.tools_schema import ToolsSchema
from piopiy.adapters.schemas.function_schema import FunctionSchema
from piopiy.services.sarvam.stt import SarvamSTTService
from piopiy.services.sarvam.tts import SarvamTTSService
from piopiy.services.elevenlabs.tts import ElevenLabsTTSService
from piopiy.services.elevenlabs.stt import ElevenLabsRealtimeSTTService
from piopiy.services.groq.llm import GroqLLMService
from piopiy.transcriptions.language import Language
from piopiy.frames.frames import (
    LLMTextFrame, LLMFullResponseStartFrame, LLMFullResponseEndFrame, LLMContextFrame,
)
from piopiy.processors.frame_processor import FrameDirection

# ── DB ─────────────────────────────────────────────────────────
from app import database as db
from app import tenant_db as tdb


class _ContextCommittingGroqLLM(GroqLLMService):
    """GroqLLMService that self-commits assistant responses to the LLM context.

    VoiceAgent places the LLMAssistantAggregator after transport.output(), which
    swallows LLMFullResponseStartFrame / LLMTextFrame / LLMFullResponseEndFrame so
    the aggregator never fires.  This subclass intercepts those frames as they leave
    the LLM (before TTS) and writes the completed assistant turn directly into the
    shared LLMContext — fixing both the transcript and multi-turn conversation memory.
    """

    def __init__(self, tenant_id: int = 1, **kwargs):
        super().__init__(**kwargs)
        self._tenant_id = tenant_id
        self._llm_context = None
        self._response_buf: list = []

    async def start_llm_usage_metrics(self, tokens):
        await super().start_llm_usage_metrics(tokens)
        try:
            from app import tenant_db as _tdb
            _tdb.log_llm_tokens(
                tenant_id         = self._tenant_id,
                provider          = "groq",
                model             = self._model or "llama-3.3-70b-versatile",
                prompt_tokens     = tokens.prompt_tokens or 0,
                completion_tokens = tokens.completion_tokens or 0,
            )
        except Exception:
            pass

    async def process_frame(self, frame, direction):
        if isinstance(frame, LLMContextFrame):
            self._llm_context = frame.context
            self._response_buf = []
            # Strip consecutive duplicate messages before LLM sees the context
            msgs = self._llm_context.get_messages()
            cleaned = []
            for m in msgs:
                if (cleaned
                        and cleaned[-1].get("role") == m.get("role")
                        and cleaned[-1].get("content", "").strip() == m.get("content", "").strip()):
                    continue
                cleaned.append(m)
            if len(cleaned) != len(msgs):
                self._llm_context.set_messages(cleaned)
        await super().process_frame(frame, direction)

    async def push_frame(self, frame, direction=FrameDirection.DOWNSTREAM):
        if isinstance(frame, LLMFullResponseStartFrame):
            self._response_buf = []
        elif isinstance(frame, LLMTextFrame):
            self._response_buf.append(frame.text)
        elif isinstance(frame, LLMFullResponseEndFrame):
            self._response_buf = []
        await super().push_frame(frame, direction)

class _TrackingElevenLabsTTS(ElevenLabsTTSService):
    """ElevenLabsTTSService that logs character usage to tenant_db."""

    def __init__(self, tenant_id: int = 1, **kwargs):
        super().__init__(**kwargs)
        self._tenant_id = tenant_id

    async def start_tts_usage_metrics(self, text: str):
        await super().start_tts_usage_metrics(text)
        try:
            from app import tenant_db as _tdb
            _tdb.log_tts_chars(self._tenant_id, "elevenlabs", len(text or ""))
        except Exception:
            pass


# Valid Sarvam AI TTS speaker IDs (bulbul:v2)
_VALID_SARVAM_VOICES = {
    "anushka", "abhilash", "manisha", "vidya", "arya", "karun", "hitesh",
    "aditya", "ritu", "priya", "neha", "rahul", "pooja", "rohan", "simran",
    "kavya", "amit", "dev", "ishita", "shreya", "ratan", "varun", "manan",
    "sumit", "roopa", "kabir", "aayan", "shubh", "ashutosh", "advait",
    "amelia", "sophia", "anand", "tanya", "tarun", "sunny", "mani", "gokul",
    "vijay", "shruti", "suhani", "mohit", "kavitha", "rehan", "soham", "rupali",
}
_DEFAULT_VOICE = "anushka"

def _safe_voice(v: str) -> str:
    """Return v if it's a valid Sarvam voice, else fall back to default."""
    return v if v and v.lower() in _VALID_SARVAM_VOICES else _DEFAULT_VOICE


_ELEVENLABS_DEFAULT_VOICE = "TX3LPaxmHKxFdv7VOQHJ"  # Liam — clear, neutral


async def _validate_elevenlabs_voice(api_key: str, voice_id: str) -> str:
    """Check voice_id exists in the account; return default if not."""
    if not voice_id or not api_key:
        return voice_id
    try:
        import aiohttp
        async with aiohttp.ClientSession() as s:
            async with s.get(
                f"https://api.elevenlabs.io/v1/voices/{voice_id}",
                headers={"xi-api-key": api_key},
                timeout=aiohttp.ClientTimeout(total=5),
            ) as r:
                if r.status == 200:
                    return voice_id
                logger.warning(
                    f"[TTS] ElevenLabs voice_id={voice_id!r} returned {r.status} — "
                    f"falling back to default voice {_ELEVENLABS_DEFAULT_VOICE}"
                )
                return _ELEVENLABS_DEFAULT_VOICE
    except Exception as e:
        logger.warning(f"[TTS] Could not validate ElevenLabs voice_id: {e} — keeping configured ID")
        return voice_id


_XAI_BASE_URL = "https://api.x.ai/v1"
_XAI_DEFAULT_MODEL = "grok-4-1-fast"
_GROQ_DEFAULT_MODEL = "llama-3.3-70b-versatile"

def _build_llm(tenant_config: dict, tenant_id: int = 1) -> _ContextCommittingGroqLLM:
    """Build the LLM service based on llm_provider in tenant config.

    Supports: groq (default), grok (xAI), openai, anthropic, gemini.
    xAI Grok uses the OpenAI-compatible endpoint via GroqLLMService base_url override.
    Falls back to Groq on any config error.
    """
    provider  = (tenant_config.get("llm_provider") or "groq").lower().strip()
    llm_model = (tenant_config.get("llm_model") or "").strip()
    groq_key  = (tenant_config.get("groq_api_key") or "").strip()
    xai_key   = (tenant_config.get("xai_api_key")  or "").strip()

    if provider == "grok" and xai_key:
        model = llm_model or _XAI_DEFAULT_MODEL
        logger.info(f"[LLM] Using xAI Grok | model={model} | tenant={tenant_id}")
        return _ContextCommittingGroqLLM(
            tenant_id=tenant_id,
            api_key=xai_key,
            base_url=_XAI_BASE_URL,
            model=model,
        )

    # Default / fallback: Groq
    if provider != "groq":
        logger.warning(
            f"[LLM] Provider '{provider}' not yet wired or key missing — falling back to Groq"
        )
    model = llm_model or _GROQ_DEFAULT_MODEL
    logger.info(f"[LLM] Using Groq | model={model} | tenant={tenant_id}")
    return _ContextCommittingGroqLLM(
        tenant_id=tenant_id,
        api_key=groq_key,
        model=model,
    )


def _build_stt_tts(tenant_config: dict, tenant_id: int = None):
    """
    Build STT and TTS independently.
    STT: sarvam (saarika:v2.5 default) | sarvam_v3 (saaras:v3) | deepgram (Nova-3)
    TTS: sarvam bulbul v2/v3 (default) | elevenlabs
    """
    tid          = tenant_config.get("tenant_id", tenant_id or "?")
    sarvam_key   = (tenant_config.get("sarvam_api_key")     or "").strip()
    deepgram_key = (tenant_config.get("deepgram_api_key")   or os.getenv("DEEPGRAM_API_KEY") or "").strip()
    labs_key     = (tenant_config.get("elevenlabs_api_key") or "").strip()
    labs_voice   = (tenant_config.get("elevenlabs_voice_id") or "").strip()
    stt_provider = (tenant_config.get("stt_provider") or "sarvam").lower()
    speech_provider = (tenant_config.get("speech_provider") or "sarvam").lower()

    # ── STT ─────────────────────────────────────────────────────
    if stt_provider == "deepgram" and deepgram_key:
        try:
            from piopiy.services.deepgram.stt import DeepgramSTTService as _DG
            from deepgram import LiveOptions as _LO
            stt = _DG(
                api_key=deepgram_key,
                live_options=_LO(
                    model="nova-3-general",
                    language="hi",
                    encoding="linear16",
                    channels=1,
                    interim_results=True,
                    smart_format=True,
                    punctuate=True,
                    endpointing=300,
                ),
            )
            stt_label = "Deepgram nova-3"
        except Exception as e:
            logger.warning(f"[T{tid}][STT] Deepgram init failed ({e}) — falling back to Sarvam")
            stt = SarvamSTTService(
                api_key=sarvam_key, model="saarika:v2.5",
                params=SarvamSTTService.InputParams(
                    language=Language.HI_IN, vad_signals=True,
                    high_vad_sensitivity=True, mode="codemix"),
            )
            stt_label = "Sarvam saarika:v2.5 (fallback)"

    elif stt_provider == "sarvam_v3" and sarvam_key:
        stt = SarvamSTTService(
            api_key=sarvam_key, model="saaras:v3",
            params=SarvamSTTService.InputParams(
                vad_signals=True,
                high_vad_sensitivity=True, mode="codemix"),
        )
        stt_label = "Sarvam saaras:v3"

    else:
        if stt_provider == "deepgram" and not deepgram_key:
            logger.warning(f"[T{tid}][STT] Deepgram selected but key not set — using Sarvam")
        stt = SarvamSTTService(
            api_key=sarvam_key, model="saarika:v2.5",
            params=SarvamSTTService.InputParams(
                language=Language.HI_IN, vad_signals=True,
                high_vad_sensitivity=True, mode="codemix"),
        )
        stt_label = "Sarvam saarika:v2.5"

    # ── TTS ─────────────────────────────────────────────────────
    tts_model_ver = (tenant_config.get("tts_model") or "v3").lower()
    tts_pace      = max(0.5, min(2.0, float(tenant_config.get("tts_pace") or 1.1)))
    tts_temp      = max(0.1, min(1.0, float(tenant_config.get("tts_temperature") or 0.75)))

    if speech_provider == "elevenlabs" and labs_key:
        if not labs_voice:
            labs_voice = _ELEVENLABS_DEFAULT_VOICE
        try:
            tts = ElevenLabsTTSService(
                api_key=labs_key, voice_id=labs_voice, model="eleven_flash_v2_5",
                params=ElevenLabsTTSService.InputParams(
                    stability=0.5, similarity_boost=0.75,
                    style=0.0, use_speaker_boost=True, speed=tts_pace),
            )
            tts_label = f"ElevenLabs flash | voice={labs_voice[:8]}…"
        except Exception as e:
            logger.warning(f"[T{tid}][TTS] ElevenLabs init failed ({e}) — falling back to Sarvam")
            tts = SarvamTTSService(
                api_key=sarvam_key, model="bulbul:v3", voice_id="kavya",
                params=SarvamTTSService.InputParams(
                    language=Language.HI, pace=tts_pace, temperature=tts_temp,
                    enable_preprocessing=True),
            )
            tts_label = "Sarvam bulbul:v3 (fallback)"
    else:
        _V3 = {"kavya","priya","suhani","ritu","simran","pooja","shubh",
               "ashutosh","amit","rahul","ratan","rohan","manan","dev",
               "sunny","sumit","aditya","neha","ishita","shreya","varun",
               "roopa","kabir","aayan","advait","amelia","sophia"}
        _V2 = {"anushka","manisha","arya","vidya","abhilash","karun","hitesh"}
        raw_voice = (tenant_config.get("agent_voice") or "").lower()

        if tts_model_ver == "v3":
            tts_voice = raw_voice if raw_voice in _V3 else "kavya"
            tts = SarvamTTSService(
                api_key=sarvam_key, model="bulbul:v3", voice_id=tts_voice,
                params=SarvamTTSService.InputParams(
                    language=Language.HI, pace=tts_pace, temperature=tts_temp,
                    enable_preprocessing=True),
            )
        else:
            tts_voice = raw_voice if raw_voice in _V2 else "anushka"
            tts = SarvamTTSService(
                api_key=sarvam_key, model="bulbul:v2", voice_id=tts_voice,
                params=SarvamTTSService.InputParams(
                    language=Language.HI, pace=tts_pace, pitch=0.0, loudness=1.2,
                    enable_preprocessing=True),
            )
        tts_label = f"Sarvam bulbul:{tts_model_ver} | voice={tts_voice} | pace={tts_pace}"

    logger.info(f"[T{tid}] STT: {stt_label} | TTS: {tts_label}")
    return stt, tts

# ── Logging ────────────────────────────────────────────────────
from loguru import logger

os.makedirs("logs", exist_ok=True)
logger.add(
    "logs/multi_agent_manager.log",
    rotation="100 MB",
    level="DEBUG",
    retention="7 days",
)

# ── Module-level helpers ───────────────────────────────────────

async def _send_telegram(token: str, chat_id: str, text: str) -> None:
    """Fire-and-forget Telegram message. Silently drops on error."""
    if not (token and chat_id):
        return
    try:
        import httpx
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        async with httpx.AsyncClient(timeout=10) as client:
            await client.post(url, json={"chat_id": chat_id, "text": text, "parse_mode": "HTML"})
    except Exception as e:
        logger.warning(f"Telegram notify failed: {e}")


async def _post_call(
    tenant_id: int,
    call_db_id: int,
    call_start: float,
    customer_name: str,
    customer_phone: str,
    company: str,
    lead_id_db,
    voice_agent,
    tg_token: str,
    tg_chat: str,
    greeting: str = "",
) -> None:
    """Analyze conversation, persist results, notify, log usage."""
    if call_db_id is None:
        return
    try:
        duration_sec = int(time.time() - call_start)
        logger.info(f"[Tenant {tenant_id}] Call ended — {duration_sec}s | analyzing…")

        from app.exotel_pipeline import analyze_call

        conversation = list(voice_agent._messages) if hasattr(voice_agent, "_messages") else []

        # Prepend greeting as first assistant message if not already present
        if greeting:
            first_assistant = next((m for m in conversation if m.get("role") == "assistant"), None)
            if not first_assistant or first_assistant.get("content", "").strip() != greeting.strip():
                conversation = [{"role": "assistant", "content": greeting}] + conversation

        # Reload agent_name from DB for accurate transcript labelling
        cfg = tdb.get_tenant_config(tenant_id) or {}
        agent_name = cfg.get("agent_name") or "Aira"

        # Ensure agent_name is properly formatted (female names for female agent)
        if agent_name.lower() in ["aira", "meera", "anushka", "priya", "neha", "shreya", "kavya", "simran", "riddhi"]:
            # These are female names, keep as is
            pass
        elif agent_name.lower() in ["arjun", "rahul", "vikram", "amit", "rohan", "karan", "dev", "aditya"]:
            # If somehow a male name got set, change to female default
            agent_name = "Aira"
            logger.warning(f"[Tenant {tenant_id}] Agent name was male '{cfg.get('agent_name')}', changed to female 'Aira'")

        # Deduplicate consecutive messages with the same role+content (SDK double-fire bug)
        deduped_conversation = []
        for m in conversation:
            if not deduped_conversation or (
                m.get("role") != deduped_conversation[-1].get("role") or
                m.get("content", "").strip() != deduped_conversation[-1].get("content", "").strip()
            ):
                deduped_conversation.append(m)

        transcript_lines = []
        for m in deduped_conversation:
            role    = m.get("role", "").lower()
            content = m.get("content", "").strip()
            if content:
                if role == "assistant":
                    transcript_lines.append(f"{agent_name}: {content}")
                elif role == "user":
                    transcript_lines.append(f"Customer: {content}")
        full_transcript = "\n".join(transcript_lines)

        # Log transcript for debugging
        logger.info(f"[Tenant {tenant_id}] Generated transcript with {len(transcript_lines)} lines, agent_name='{agent_name}'")
        if len(transcript_lines) == 0:
            logger.warning(f"[Tenant {tenant_id}] No transcript lines generated from {len(conversation)} messages")

        analysis  = await analyze_call(conversation)
        outcome   = analysis.get("outcome", "answered")
        sentiment = analysis.get("sentiment", "neutral")
        summary   = analysis.get("summary", "")

        db.complete_call(
            call_id      = call_db_id,
            duration_sec = duration_sec,
            outcome      = outcome,
            sentiment    = sentiment,
            summary      = summary,
            transcript   = full_transcript,
        )

        from app.follow_up_service import schedule_call_follow_up
        await schedule_call_follow_up(call_db_id)

        if lead_id_db:
            if outcome == "no_answer":
                db.set_lead_retry(lead_id_db, retry_count_floor=1, gap_minutes=30)
            else:
                db.update_lead(lead_id_db, status=analysis.get("lead_status", "called"))

        db.add_log(
            f"✅ [T{tenant_id}] Call done — {customer_phone} | "
            f"{duration_sec}s | {sentiment} | {summary[:80]}"
        )
        logger.info(f"[Tenant {tenant_id}] DB updated: call_db_id={call_db_id} | {analysis}")

        # Bill by minute pulse (ceiling): 1-60 sec = 1 min, 61-120 sec = 2 min, etc.
        import math
        billed_minutes = max(1, math.ceil(duration_sec / 60))
        tdb.log_usage(tenant_id, minutes=billed_minutes)

        # CRM webhook — fire-and-forget
        # ── Fire webhook (non-blocking) ──────────────────
        try:
            from app.webhook_service import fire_call_webhook as _fire_call_webhook
            asyncio.create_task(_fire_call_webhook(tenant_id, {
                "call_id":      call_db_id,
                "phone":        customer_phone,
                "lead_name":    customer_name,
                "company":      company,
                "duration_sec": duration_sec,
                "outcome":      outcome,
                "sentiment":    sentiment,
                "summary":      summary,
                "transcript":   full_transcript,
                "campaign_id":  None,
                "lead_id":      lead_id_db,
            }))
        except Exception:
            pass  # never block call completion for webhook errors

        if outcome == "answered":
            lead_label = customer_name or customer_phone
            if sentiment == "demo_booked":
                await _send_telegram(tg_token, tg_chat,
                    f"🎉 <b>Demo Booked — Tenant {tenant_id}</b>\n"
                    f"Lead: {lead_label}\nCompany: {company}\nSummary: {summary}")
            elif sentiment == "interested":
                await _send_telegram(tg_token, tg_chat,
                    f"👍 <b>Interested Lead — Tenant {tenant_id}</b>\n"
                    f"Lead: {lead_label}\nCompany: {company}\nSummary: {summary}")
            else:
                await _send_telegram(tg_token, tg_chat,
                    f"📞 <b>Call Completed — Tenant {tenant_id}</b>\n"
                    f"Lead: {lead_label} | {duration_sec}s\n"
                    f"Sentiment: {sentiment}\nSummary: {summary}")

    except Exception as e:
        logger.error(f"[Tenant {tenant_id}] Post-call error: {e}")


def _apply_dynamic_vars(text: str, lead_obj: dict | None,
                        customer_name: str = "",
                        agent_name: str = "",
                        company_name: str = "") -> str:
    """Replace {lead_name}, {company}, {city}, {designation} (and legacy {name}/{agent})
    with actual lead values. Safe — undefined placeholders are left unchanged.
    """
    if not text:
        return text
    lead = lead_obj or {}
    replacements = {
        "{lead_name}":   customer_name or lead.get("name", ""),
        "{name}":        customer_name or lead.get("name", ""),
        "{company}":     lead.get("company", "") or company_name,
        "{city}":        lead.get("city", ""),
        "{designation}": lead.get("designation", ""),
        "{agent}":       agent_name,
    }
    for placeholder, value in replacements.items():
        text = text.replace(placeholder, value or "")
    return text


async def _fire_webhook(webhook_url: str, payload: dict):
    """POST call result to tenant webhook URL. Fire-and-forget — never blocks."""
    try:
        import httpx
        async with httpx.AsyncClient(timeout=5) as client:
            r = await client.post(webhook_url, json=payload)
            logger.info(f"[Webhook] POST {webhook_url} → {r.status_code}")
    except Exception as e:
        logger.warning(f"[Webhook] POST {webhook_url} failed: {e}")


def make_create_session(tenant_id: int, initial_config: dict):
    """
    Returns a create_session coroutine for a specific tenant.
    Config is reloaded from DB on EVERY call so settings changes
    take effect without restarting the service.
    """

    async def create_session(
        agent_id=None, call_id=None, from_number=None,
        to_number=None, metadata=None, **kwargs
    ):
        # ── Reload config fresh from DB on every call ─────────
        tenant_config = tdb.get_tenant_config(tenant_id) or initial_config
        logger.info(f"[Tenant {tenant_id}] 🔍 DEBUG: Loaded config for tenant_id={tenant_id}")

        # Resolve credentials from DB — tenant settings are the single source of truth
        sarvam_key    = tenant_config.get("sarvam_api_key")     or ""
        groq_key      = tenant_config.get("groq_api_key")       or ""
        agent_name    = tenant_config.get("agent_name")         or "Aira"
        agent_voice   = _safe_voice(tenant_config.get("agent_voice") or "")
        system_prompt = tenant_config.get("system_prompt")      or ""
        faq_content = (tenant_config.get("faq_content") or "").strip()
        if faq_content:
            system_prompt += (
                "\n\n--- Frequently Asked Questions ---\n"
                "Use the following Q&A to answer customer questions accurately. "
                "If a customer asks something covered here, use this answer directly.\n\n"
                + faq_content
            )
        logger.info(f"[Tenant {tenant_id}] system_prompt length={len(system_prompt)}, starts with={system_prompt[:50] if system_prompt else 'EMPTY'}")
        tg_token      = tenant_config.get("telegram_bot_token") or ""
        tg_chat       = tenant_config.get("telegram_chat_id")   or ""
        own_digits    = "".join(
            c for c in (tenant_config.get("piopiy_number") or "") if c.isdigit()
        )

        # Build system prompt if not set via settings page
        if not system_prompt:
            company_name     = tenant_config.get("company_name", "")
            company_industry = tenant_config.get("company_industry", "")
            company_products = tenant_config.get("company_products", "")
            company_website  = tenant_config.get("company_website", "")
            call_language    = tenant_config.get("call_language", "hindi")
            call_guidelines  = tenant_config.get("call_guidelines", "")
            lang_instruction = {
                "hindi":    "हमेशा हिंदी में बोलें।",
                "english":  "Always speak in English.",
                "hinglish": "Hinglish में बोलें — Hindi और English mix करें।"
            }.get(call_language, "हमेशा हिंदी में बोलें।")
            default_guidelines = (
                "- हर जवाब 2-3 वाक्य में दें\n"
                "- अंत में demo schedule करने की कोशिश करें\n"
                "- रुचि नहीं है तो विनम्रता से call समाप्त करें"
            )
            system_prompt = (
                f"आप {agent_name} हैं, {company_name or 'हमारी कंपनी'} की professional sales agent हैं।\n\n"
                f"कंपनी: {company_name}\n"
                f"Industry: {company_industry}\n"
                f"Products/Services: {company_products}\n"
                f"Website: {company_website}\n\n"
                f"{lang_instruction}\n\n"
                f"Call Guidelines:\n{call_guidelines or default_guidelines}\n\n"
                "हर जवाब में: पहले information दें, फिर customer से एक question पूछें।"
            )

        logger.info(
            f"[Tenant {tenant_id}] 📞 Call | call_id={call_id} "
            f"| from={from_number} | to={to_number}"
        )

        # ── Detect call direction ─────────────────────────────
        from_digits = "".join(c for c in str(from_number or "") if c.isdigit())
        to_digits   = "".join(c for c in str(to_number   or "") if c.isdigit())
        is_inbound  = bool(own_digits and own_digits in to_digits)
        customer_phone = str(from_number if is_inbound else to_number or from_number or "unknown")
        logger.info(
            f"[Tenant {tenant_id}] Direction: {'INBOUND' if is_inbound else 'OUTBOUND'} "
            f"| customer={customer_phone}"
        )

        # ── DB: resolve lead ──────────────────────────────────
        metadata      = metadata or {}
        lead_id_str   = metadata.get("lead_id", "")
        lead_id_db    = int(lead_id_str) if str(lead_id_str).isdigit() else None
        lead_obj      = db.get_lead(lead_id_db) if lead_id_db else None
        customer_name = metadata.get("customer_name", "").strip()
        if not customer_name and lead_obj:
            customer_name = lead_obj.get("name", "")
        company = lead_obj.get("company", "") if lead_obj else ""

        # Dynamic variable substitution in system_prompt (plan-gated)
        _tenant_plan = (tdb.get_tenant(tenant_id) or {}).get("plan", "starter")
        from app.plan_features import check_feature as _cf_dv
        if _cf_dv(_tenant_plan, "dynamic_variables")["allowed"]:
            _company_name_dv = tenant_config.get("company_name", "")
            system_prompt = _apply_dynamic_vars(
                system_prompt, lead_obj, customer_name, agent_name, _company_name_dv
            )

        # ── DB: create call record ────────────────────────────
        call_start = time.time()
        call_db_id = db.create_call(
            phone       = customer_phone,
            lead_name   = customer_name,
            company     = company,
            lead_id     = lead_id_db,
            campaign_id = lead_obj.get("campaign_id") if lead_obj else None,
            call_sid    = str(call_id or ""),
            tenant_id   = tenant_id,
            direction   = 'inbound' if is_inbound else 'outbound',
        )
        with db.get_conn() as conn:
            conn.execute(
                "UPDATE calls SET tenant_id=? WHERE id=?",
                (tenant_id, call_db_id)
            )
            conn.commit()

        db.add_log(
            f"📞 [T{tenant_id}] {'Inbound' if is_inbound else 'Outbound'} call — "
            f"{customer_phone} | call_db_id={call_db_id}"
        )

        # Register active call
        try:
            import httpx
            async with httpx.AsyncClient(timeout=5) as client:
                await client.post("http://localhost:8000/api/calls/active/register", json={
                    "call_id":    str(call_db_id),
                    "lead_name":  customer_name,
                    "phone":      customer_phone,
                    "company":    company,
                    "tenant_id":  tenant_id,
                    "started_at": str(datetime.utcnow()),
                })
        except Exception as _e:
            logger.warning(f"Active-call register failed for call {call_db_id}: {_e}")

        # ── Greeting ──────────────────────────────────────────
        company_name  = tenant_config.get("company_name", "")
        greeting_tmpl = tenant_config.get("greeting_template") or ""
        if greeting_tmpl:
            _tenant_plan_g = (tdb.get_tenant(tenant_id) or {}).get("plan", "starter")
            from app.plan_features import check_feature as _cf_g
            if _cf_g(_tenant_plan_g, "dynamic_variables")["allowed"]:
                greeting = _apply_dynamic_vars(
                    greeting_tmpl, lead_obj, customer_name, agent_name, company_name
                )
            else:
                greeting = greeting_tmpl.replace("{name}", customer_name or "").replace("{agent}", agent_name).replace("{company}", company_name)
        elif is_inbound:
            co = f"आपकी {company_name} में " if company_name else ""
            greeting = f"नमस्ते! {co}स्वागत है। बताइए, मैं आपकी कैसे सहायता कर सकती हूँ?"
        else:
            greeting = f"नमस्ते{' ' + customer_name + ' जी' if customer_name else ''}!"

        # Normalize greeting: collapse newlines/extra spaces so TTS speaks it fully
        import re as _re
        greeting = _re.sub(r'\s*\n\s*', ' ', greeting).strip()

        # ── Services ──────────────────────────────────────────
        # Validate ElevenLabs voice ID; fall back to default if voice was deleted
        _labs_key_v = (tenant_config.get("elevenlabs_api_key") or "").strip()
        _labs_voice_v = (tenant_config.get("elevenlabs_voice_id") or "").strip()
        if _labs_key_v and _labs_voice_v:
            _validated_voice = await _validate_elevenlabs_voice(_labs_key_v, _labs_voice_v)
            if _validated_voice != _labs_voice_v:
                tenant_config = dict(tenant_config)
                tenant_config["elevenlabs_voice_id"] = _validated_voice

        stt, tts = _build_stt_tts(tenant_config, tenant_id=tenant_id)
        llm = _build_llm(tenant_config, tenant_id=tenant_id)

        voice_agent = VoiceAgent(
            instructions=system_prompt,
            greeting=greeting,
            idle_timeout_secs=120,
        )

        try:
            _end_call_tool = FunctionSchema(
                name="end_call",
                description=(
                    "Call this function to hang up and end the conversation. "
                    "Use when: customer says goodbye / call khatam karo / band karo / "
                    "DNC / number hatao / abusive / task complete / natural end."
                ),
                properties={},
                required=[],
            )
            _tools = ToolsSchema(standard_tools=[_end_call_tool])
            await voice_agent.Action(
                stt=stt, llm=llm, tts=tts,
                vad={
                    "stop_secs":  0.6,
                    "start_secs": 0.1,
                    "confidence": 0.65,
                    "min_volume": 0.5,
                },
                allow_interruptions=True,
                tools=_tools,
            )
            await voice_agent.start()
        except asyncio.CancelledError:
            logger.info(f"[Tenant {tenant_id}] Session cancelled | call_id={call_id}")
        except Exception as e:
            logger.error(f"[Tenant {tenant_id}] VoiceAgent error | call_id={call_id} | {e}")
            await _send_telegram(tg_token, tg_chat,
                f"🚨 <b>VoiceAgent Error — Tenant {tenant_id}</b>\n⚠️ {e}")
        finally:
            await _post_call(
                tenant_id, call_db_id, call_start,
                customer_name, customer_phone, company,
                lead_id_db, voice_agent,
                tg_token, tg_chat,
                greeting=greeting,
            )
            try:
                import httpx
                async with httpx.AsyncClient() as client:
                    await client.post("http://localhost:8000/api/calls/active/unregister",
                                      json={"call_id": str(call_db_id)})
            except Exception as _e:
                logger.warning(f"Active-call unregister failed for call {call_db_id}: {_e}")

    return create_session


def make_platform_create_session():
    """
    Returns a create_session coroutine for the platform-level fallback agent.
    This agent is used when a tenant does NOT have their own PIOPIY credentials.
    The correct tenant is identified by reading 'tenant_id' from call metadata/variables,
    then their full config (system_prompt, API keys, etc.) is loaded from the DB.
    """

    async def create_session(
        agent_id=None, call_id=None, from_number=None,
        to_number=None, metadata=None, **kwargs
    ):
        metadata = metadata or {}
        # metadata["tenant_id"] is set by piopiy_handler when it fires the call
        raw_tid = metadata.get("tenant_id", "1")
        tenant_id = int(raw_tid) if str(raw_tid).isdigit() else 1

        # Reload config fresh from DB so settings changes take effect immediately
        tenant_config = tdb.get_tenant_config(tenant_id) or {}
        logger.info(
            f"[Platform Agent] 📞 call_id={call_id} | tenant_id={tenant_id} "
            f"| from={from_number} | to={to_number}"
        )

        # Resolve credentials from DB — tenant settings are the single source of truth
        sarvam_key    = tenant_config.get("sarvam_api_key")     or ""
        groq_key      = tenant_config.get("groq_api_key")       or ""
        agent_name    = tenant_config.get("agent_name")         or "Aira"
        agent_voice   = _safe_voice(tenant_config.get("agent_voice") or "")
        system_prompt = tenant_config.get("system_prompt")      or ""
        faq_content = (tenant_config.get("faq_content") or "").strip()
        if faq_content:
            system_prompt += (
                "\n\n--- Frequently Asked Questions ---\n"
                "Use the following Q&A to answer customer questions accurately. "
                "If a customer asks something covered here, use this answer directly.\n\n"
                + faq_content
            )
        tg_token      = tenant_config.get("telegram_bot_token") or ""
        tg_chat       = tenant_config.get("telegram_chat_id")   or ""
        own_digits    = "".join(
            c for c in (tenant_config.get("piopiy_number") or "") if c.isdigit()
        )

        logger.info(
            f"[Platform Agent | T{tenant_id}] system_prompt length={len(system_prompt)} "
            f"| agent_name={agent_name}"
        )

        # Build default system prompt if tenant has none configured
        if not system_prompt:
            company_name     = tenant_config.get("company_name", "")
            company_industry = tenant_config.get("company_industry", "")
            company_products = tenant_config.get("company_products", "")
            company_website  = tenant_config.get("company_website", "")
            call_language    = tenant_config.get("call_language", "hindi")
            call_guidelines  = tenant_config.get("call_guidelines", "")
            lang_instruction = {
                "hindi":    "हमेशा हिंदी में बोलें।",
                "english":  "Always speak in English.",
                "hinglish": "Hinglish में बोलें — Hindi और English mix करें।"
            }.get(call_language, "हमेशा हिंदी में बोलें।")
            default_guidelines = (
                "- हर जवाब 2-3 वाक्य में दें\n"
                "- अंत में demo schedule करने की कोशिश करें\n"
                "- रुचि नहीं है तो विनम्रता से call समाप्त करें"
            )
            system_prompt = (
                f"आप {agent_name} हैं, {company_name or 'हमारी कंपनी'} की professional sales agent हैं।\n\n"
                f"कंपनी: {company_name}\n"
                f"Industry: {company_industry}\n"
                f"Products/Services: {company_products}\n"
                f"Website: {company_website}\n\n"
                f"{lang_instruction}\n\n"
                f"Call Guidelines:\n{call_guidelines or default_guidelines}\n\n"
                "हर जवाब में: पहले information दें, फिर customer से एक question पूछें।"
            )

        # ── Detect call direction ─────────────────────────────
        from_digits = "".join(c for c in str(from_number or "") if c.isdigit())
        to_digits   = "".join(c for c in str(to_number   or "") if c.isdigit())
        is_inbound  = bool(own_digits and own_digits in to_digits)
        customer_phone = str(from_number if is_inbound else to_number or from_number or "unknown")
        logger.info(
            f"[Platform Agent | T{tenant_id}] Direction: {'INBOUND' if is_inbound else 'OUTBOUND'} "
            f"| customer={customer_phone}"
        )

        # ── DB: resolve lead ──────────────────────────────────
        lead_id_str   = metadata.get("lead_id", "")
        lead_id_db    = int(lead_id_str) if str(lead_id_str).isdigit() else None
        lead_obj      = db.get_lead(lead_id_db) if lead_id_db else None
        customer_name = metadata.get("customer_name", "").strip()
        if not customer_name and lead_obj:
            customer_name = lead_obj.get("name", "")
        company = lead_obj.get("company", "") if lead_obj else ""

        # Dynamic variable substitution in system_prompt (plan-gated)
        _tenant_plan = (tdb.get_tenant(tenant_id) or {}).get("plan", "starter")
        from app.plan_features import check_feature as _cf_dv
        if _cf_dv(_tenant_plan, "dynamic_variables")["allowed"]:
            _company_name_dv = tenant_config.get("company_name", "")
            system_prompt = _apply_dynamic_vars(
                system_prompt, lead_obj, customer_name, agent_name, _company_name_dv
            )

        # ── DB: create call record ────────────────────────────
        call_start = time.time()
        call_db_id = db.create_call(
            phone       = customer_phone,
            lead_name   = customer_name,
            company     = company,
            lead_id     = lead_id_db,
            campaign_id = lead_obj.get("campaign_id") if lead_obj else None,
            call_sid    = str(call_id or ""),
            tenant_id   = tenant_id,
            direction   = 'inbound' if is_inbound else 'outbound',
        )
        with db.get_conn() as conn:
            conn.execute(
                "UPDATE calls SET tenant_id=? WHERE id=?",
                (tenant_id, call_db_id)
            )
            conn.commit()

        db.add_log(
            f"📞 [Platform | T{tenant_id}] {'Inbound' if is_inbound else 'Outbound'} call — "
            f"{customer_phone} | call_db_id={call_db_id}"
        )

        # Register active call
        try:
            import httpx
            async with httpx.AsyncClient() as client:
                await client.post("http://localhost:8000/api/calls/active/register", json={
                    "call_id":    str(call_db_id),
                    "lead_name":  customer_name,
                    "phone":      customer_phone,
                    "company":    company,
                    "tenant_id":  tenant_id,
                    "started_at": str(datetime.utcnow()),
                })
        except Exception as _e:
            logger.warning(f"Active-call register failed: {_e}")

        # ── Greeting ──────────────────────────────────────────
        company_name  = tenant_config.get("company_name", "")
        greeting_tmpl = tenant_config.get("greeting_template") or ""
        if greeting_tmpl:
            _tenant_plan_g = (tdb.get_tenant(tenant_id) or {}).get("plan", "starter")
            from app.plan_features import check_feature as _cf_g
            if _cf_g(_tenant_plan_g, "dynamic_variables")["allowed"]:
                greeting = _apply_dynamic_vars(
                    greeting_tmpl, lead_obj, customer_name, agent_name, company_name
                )
            else:
                greeting = greeting_tmpl.replace("{name}", customer_name or "").replace("{agent}", agent_name).replace("{company}", company_name)
        elif is_inbound:
            co = f"आपकी {company_name} में " if company_name else ""
            greeting = f"नमस्ते! {co}स्वागत है। बताइए, मैं आपकी कैसे सहायता कर सकती हूँ?"
        else:
            greeting = f"नमस्ते{' ' + customer_name + ' जी' if customer_name else ''}!"

        # Normalize greeting: collapse newlines/extra spaces so TTS speaks it fully
        import re as _re
        greeting = _re.sub(r'\s*\n\s*', ' ', greeting).strip()

        # ── Services ──────────────────────────────────────────
        # Validate ElevenLabs voice ID; fall back to default if voice was deleted
        _labs_key_v = (tenant_config.get("elevenlabs_api_key") or "").strip()
        _labs_voice_v = (tenant_config.get("elevenlabs_voice_id") or "").strip()
        if _labs_key_v and _labs_voice_v:
            _validated_voice = await _validate_elevenlabs_voice(_labs_key_v, _labs_voice_v)
            if _validated_voice != _labs_voice_v:
                tenant_config = dict(tenant_config)
                tenant_config["elevenlabs_voice_id"] = _validated_voice

        stt, tts = _build_stt_tts(tenant_config, tenant_id=tenant_id)
        llm = _build_llm(tenant_config, tenant_id=tenant_id)

        voice_agent = VoiceAgent(
            instructions=system_prompt,
            greeting=greeting,
            idle_timeout_secs=120,
        )

        try:
            _end_call_tool = FunctionSchema(
                name="end_call",
                description=(
                    "Call this function to hang up and end the conversation. "
                    "Use when: customer says goodbye / call khatam karo / band karo / "
                    "DNC / number hatao / abusive / task complete / natural end."
                ),
                properties={},
                required=[],
            )
            _tools = ToolsSchema(standard_tools=[_end_call_tool])
            await voice_agent.Action(
                stt=stt, llm=llm, tts=tts,
                vad={
                    "stop_secs":  0.6,
                    "start_secs": 0.1,
                    "confidence": 0.65,
                    "min_volume": 0.5,
                },
                allow_interruptions=True,
                tools=_tools,
            )
            await voice_agent.start()
        except asyncio.CancelledError:
            logger.info(f"[Platform Agent | T{tenant_id}] Session cancelled | call_id={call_id}")
        except Exception as e:
            logger.error(f"[Platform Agent | T{tenant_id}] VoiceAgent error | call_id={call_id} | {e}")
            await _send_telegram(tg_token, tg_chat,
                f"🚨 <b>VoiceAgent Error — Platform Agent T{tenant_id}</b>\n⚠️ {e}")
        finally:
            await _post_call(
                tenant_id, call_db_id, call_start,
                customer_name, customer_phone, company,
                lead_id_db, voice_agent,
                tg_token, tg_chat,
                greeting=greeting,
            )
            try:
                import httpx
                async with httpx.AsyncClient() as client:
                    await client.post("http://localhost:8000/api/calls/active/unregister",
                                      json={"call_id": str(call_db_id)})
            except Exception as _e:
                logger.warning(f"Active-call unregister failed: {_e}")

    return create_session


async def run_platform_agent():
    """
    Connect the platform-level fallback PIOPIY agent.
    Used for tenants that do not have their own piopiy_agent_id/token.
    Reads tenant_id from call metadata to load the correct tenant's config.
    Credentials are loaded from tenant 1 (platform owner) in the database.
    """
    # Primary: read from tenant 1 (platform owner) DB config
    platform_cfg = tdb.get_tenant_config(1) or {}
    agent_id     = (platform_cfg.get("piopiy_agent_id")    or "").strip()
    agent_token  = (platform_cfg.get("piopiy_agent_token") or "").strip()

    if not agent_id or not agent_token:
        logger.warning(
            "[Platform Agent] Tenant 1 has no PIOPIY credentials in DB — skipping platform agent. "
            "Set PIOPIY Agent ID and Token in Account Settings → Telephony."
        )
        return

    logger.info(f"[Platform Agent] 🚀 Connecting | agent_id={agent_id[:8]}…")

    create_session = make_platform_create_session()

    agent = Agent(
        agent_id=agent_id,
        agent_token=agent_token,
        create_session=create_session,
        debug=True,
    )

    # Re-add file logger (Agent(debug=True) resets loguru to INFO/stderr)
    logger.add(
        "logs/multi_agent_manager.log",
        rotation="100 MB",
        level="DEBUG",
        retention="7 days",
    )

    try:
        await agent.connect()
    except Exception as e:
        logger.error(f"[Platform Agent] Agent.connect() failed: {e}")


async def run_tenant_agent(tenant_id: int, cfg: dict):
    """Connect a single tenant's PIOPIY agent and keep it running."""
    agent_id    = cfg.get("piopiy_agent_id", "").strip()
    agent_token = cfg.get("piopiy_agent_token", "").strip()
    agent_name  = cfg.get("agent_name") or "Aira"

    if not agent_id or not agent_token:
        logger.warning(f"[tenant={tenant_id}] Missing piopiy_agent_id or token — skipping")
        return

    logger.info(
        f"[tenant={tenant_id}] 🚀 {agent_name} — agent_id={agent_id[:8]}… "
        f"| number={cfg.get('piopiy_number', '?')}"
    )

    create_session = make_create_session(tenant_id, cfg)

    agent = Agent(
        agent_id=agent_id,
        agent_token=agent_token,
        create_session=create_session,
        debug=True,
    )

    # Re-add file logger (Agent(debug=True) resets loguru to INFO/stderr)
    logger.add(
        "logs/multi_agent_manager.log",
        rotation="100 MB",
        level="DEBUG",
        retention="7 days",
    )

    try:
        await agent.connect()
    except Exception as e:
        logger.error(f"[tenant={tenant_id}] Agent.connect() failed: {e}")


async def main():
    logger.info("🏢 Multi-Tenant PIOPIY Agent Manager starting…")

    # Load all active tenants with a valid PIOPIY config
    all_tenants = tdb.get_all_tenants()
    active = [t for t in all_tenants if t.get("status") == "active"]

    # Tenants whose piopiy_agent_id == platform agent_id (tenant 1) should NOT get a separate
    # per-tenant agent — the platform agent covers them with dynamic tenant_id routing.
    platform_cfg          = tdb.get_tenant_config(1) or {}
    platform_agent_id_env = (platform_cfg.get("piopiy_agent_id") or "").strip()

    tasks = []
    for tenant in active:
        tenant_id = tenant["id"]
        cfg = tdb.get_tenant_config(tenant_id)
        if not cfg:
            logger.warning(f"[tenant={tenant_id}] No config found — skipping")
            continue

        agent_id    = (cfg.get("piopiy_agent_id")    or "").strip()
        agent_token = (cfg.get("piopiy_agent_token") or "").strip()
        if not agent_id or not agent_token:
            logger.info(
                f"[tenant={tenant_id}] ({tenant.get('name')}) "
                "No PIOPIY credentials — skipping (will use platform agent)"
            )
            continue

        # If this tenant's agent_id matches the platform agent_id, the platform agent
        # (with dynamic metadata routing) will serve it — skip the per-tenant agent
        # to avoid double-connecting the same agent_id to PIOPIY.
        if agent_id == platform_agent_id_env:
            logger.info(
                f"[tenant={tenant_id}] ({tenant.get('name')}) "
                f"agent_id matches platform agent — served by platform agent dynamically"
            )
            continue

        logger.info(
            f"  → Tenant {tenant_id}: {tenant.get('name')} | "
            f"agent_id={agent_id[:8]}… | number={cfg.get('piopiy_number', '?')}"
        )
        tasks.append(run_tenant_agent(tenant_id, cfg))

    # Always add the platform-level fallback agent using tenant 1's DB credentials.
    # This handles calls for ANY tenant by reading tenant_id from call metadata dynamically.
    # IMPORTANT: If tenant 1's agent_id matches a per-tenant agent_id, we must NOT
    # double-connect the same agent_id. The platform agent replaces the per-tenant one
    # (it defaults to tenant_id=1 when metadata carries no tenant_id).
    platform_agent_id = platform_agent_id_env  # already loaded from tenant 1 DB above
    if platform_agent_id:
        logger.info(f"[Platform Agent] Adding dynamic fallback agent | agent_id={platform_agent_id[:8]}…")
        tasks.append(run_platform_agent())
    else:
        logger.warning("[Platform Agent] No PIOPIY Agent ID in DB (tenant 1 config) — no platform fallback agent. Set it in Super Admin → API Config.")

    if not tasks:
        logger.error("No agents to connect (no tenant credentials and no platform .env). Exiting.")
        return

    n_tenant   = len(tasks) - (1 if platform_agent_id else 0)
    n_platform = 1 if platform_agent_id else 0
    logger.info(f"📡 Connecting {len(tasks)} agent(s) to PIOPIY ({n_tenant} tenant-dedicated + {n_platform} platform)…")
    await asyncio.gather(*tasks, return_exceptions=True)


def _acquire_pid_lock(pid_file: str) -> bool:
    """Return True if we got the lock, False if another instance is already running."""
    import fcntl
    try:
        fd = open(pid_file, "w")
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        fd.write(str(os.getpid()))
        fd.flush()
        # Keep fd open for the lifetime of the process — closing releases the lock.
        _acquire_pid_lock._fd = fd  # noqa: SLF001
        return True
    except BlockingIOError:
        return False


if __name__ == "__main__":
    _PID_FILE = "/tmp/multi_agent_manager.lock"
    if not _acquire_pid_lock(_PID_FILE):
        logger.error(
            "Another instance of multi_agent_manager.py is already running. "
            f"Lock held by {_PID_FILE}. Exiting."
        )
        sys.exit(1)

    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("🛑 Multi-Agent Manager stopped")
    except Exception as e:
        logger.error(f"❌ Fatal: {e}", exc_info=True)
