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

from dotenv import load_dotenv

load_dotenv()

sys.path.insert(0, "/root/ai-caller-env/ai-caller")

# ── piopiy-ai SDK ──────────────────────────────────────────────
from piopiy.agent import Agent
from piopiy.voice_agent import VoiceAgent
from piopiy.services.sarvam.stt import SarvamSTTService
from piopiy.services.sarvam.tts import SarvamTTSService
from piopiy.services.groq.llm import GroqLLMService
from piopiy.transcriptions.language import Language

# ── DB ─────────────────────────────────────────────────────────
from app import database as db
from app import tenant_db as tdb

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
) -> None:
    """Analyze conversation, persist results, notify, log usage."""
    if call_db_id is None:
        return
    try:
        duration_sec = int(time.time() - call_start)
        logger.info(f"[Tenant {tenant_id}] Call ended — {duration_sec}s | analyzing…")

        from app.exotel_pipeline import analyze_call

        conversation = voice_agent.messages if hasattr(voice_agent, "messages") else []

        # Reload agent_name from DB for accurate transcript labelling
        cfg = tdb.get_tenant_config(tenant_id) or {}
        agent_name = cfg.get("agent_name") or "Aira"

        transcript_lines = []
        for m in conversation:
            role    = m.get("role", "").lower()
            content = m.get("content", "").strip()
            if content:
                if role == "assistant":
                    transcript_lines.append(f"{agent_name}: {content}")
                elif role == "user":
                    transcript_lines.append(f"Customer: {content}")
        full_transcript = "\n".join(transcript_lines)

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
            db.update_lead(lead_id_db, status=analysis.get("lead_status", "called"))

        db.add_log(
            f"✅ [T{tenant_id}] Call done — {customer_phone} | "
            f"{duration_sec}s | {sentiment} | {summary[:80]}"
        )
        logger.info(f"[Tenant {tenant_id}] DB updated: call_db_id={call_db_id} | {analysis}")

        tdb.log_usage(tenant_id, minutes=round(duration_sec / 60, 2))

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

        # Resolve credentials — tenant keys take priority over platform .env
        sarvam_key    = tenant_config.get("sarvam_api_key")  or os.getenv("SARVAM_API_KEY", "")
        groq_key      = tenant_config.get("groq_api_key")    or os.getenv("GROQ_API_KEY", "")
        agent_name    = tenant_config.get("agent_name")      or "Aira"
        agent_voice   = tenant_config.get("agent_voice")     or "anushka"
        system_prompt = tenant_config.get("system_prompt")   or ""
        tg_token      = tenant_config.get("telegram_bot_token") or os.getenv("TELEGRAM_BOT_TOKEN", "")
        tg_chat       = tenant_config.get("telegram_chat_id")   or os.getenv("TELEGRAM_CHAT_ID", "")
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

        # ── DB: create call record ────────────────────────────
        call_start = time.time()
        call_db_id = db.create_call(
            phone       = customer_phone,
            lead_name   = customer_name,
            company     = company,
            lead_id     = lead_id_db,
            campaign_id = lead_obj.get("campaign_id") if lead_obj else None,
            call_sid    = str(call_id or ""),
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

        # ── Greeting ──────────────────────────────────────────
        greeting_tmpl = tenant_config.get("greeting_template") or ""
        if greeting_tmpl:
            greeting = greeting_tmpl.replace("{name}", customer_name or "").replace("{agent}", agent_name)
        else:
            greeting = (
                f"नमस्ते{' ' + customer_name + ' जी' if customer_name else ''}! "
                f"मैं {agent_name} बोल रही हूँ। "
                "क्या आपके पास 2 मिनट हैं?"
            )

        # ── Services ──────────────────────────────────────────
        stt = SarvamSTTService(
            api_key=sarvam_key,
            model="saarika:v2.5",
            params=SarvamSTTService.InputParams(
                language=Language.HI_IN, vad_signals=True,
            ),
        )
        llm = GroqLLMService(
            api_key=groq_key,
            model="llama-3.3-70b-versatile",
        )
        tts = SarvamTTSService(
            api_key=sarvam_key,
            model="bulbul:v2",
            voice_id=agent_voice,
            params=SarvamTTSService.InputParams(language=Language.HI, pace=0.95),
        )

        voice_agent = VoiceAgent(
            instructions=system_prompt,
            greeting=greeting,
            idle_timeout_secs=120,
        )

        try:
            await voice_agent.Action(
                stt=stt, llm=llm, tts=tts,
                vad=True, allow_interruptions=True,
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
            )

    return create_session


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
        raise


async def main():
    logger.info("🏢 Multi-Tenant PIOPIY Agent Manager starting…")

    # Load all active tenants with a valid PIOPIY config
    all_tenants = tdb.get_all_tenants()
    active = [t for t in all_tenants if t.get("status") == "active"]

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
                "No PIOPIY credentials — skipping"
            )
            continue

        logger.info(
            f"  → Tenant {tenant_id}: {tenant.get('name')} | "
            f"agent_id={agent_id[:8]}… | number={cfg.get('piopiy_number', '?')}"
        )
        tasks.append(run_tenant_agent(tenant_id, cfg))

    if not tasks:
        logger.error("No active tenants with PIOPIY credentials found. Exiting.")
        return

    logger.info(f"📡 Connecting {len(tasks)} tenant agent(s) to PIOPIY…")
    await asyncio.gather(*tasks)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("🛑 Multi-Agent Manager stopped")
    except Exception as e:
        logger.error(f"❌ Fatal: {e}", exc_info=True)
