"""
app/telegram_notify.py
Telegram notification service for MuTech AI Caller (Aira)
 
Sends real-time alerts to admin Telegram when:
  - 🎯 Demo is booked
  - 👍 Lead shows interest
  - 📞 Call completed (answered calls only)
  - 🚀 Service starts up
  - 📊 Campaign completes
  - 🚨 Error / service issue
"""
 
import os
import aiohttp
from loguru import logger
from dotenv import load_dotenv
 
load_dotenv()
 
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID   = os.getenv("TELEGRAM_CHAT_ID", "")
 
TELEGRAM_API_URL = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
 
 
# ─────────────────────────────────────────────────────────────
# CORE SENDER
# ─────────────────────────────────────────────────────────────
async def send_message(text: str):
    """Send a message to admin Telegram. Fails silently — never crashes pipeline."""
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        logger.warning("Telegram credentials not set — skipping notification")
        return
 
    try:
        async with aiohttp.ClientSession() as session:
            payload = {
                "chat_id":    TELEGRAM_CHAT_ID,
                "text":       text,
                "parse_mode": "HTML",
            }
            async with session.post(TELEGRAM_API_URL, json=payload, timeout=aiohttp.ClientTimeout(total=5)) as resp:
                if resp.status != 200:
                    body = await resp.text()
                    logger.warning(f"Telegram send failed: {resp.status} — {body}")
                else:
                    logger.info("Telegram notification sent ✅")
    except Exception as e:
        logger.warning(f"Telegram notification error (non-critical): {e}")
 
 
# ─────────────────────────────────────────────────────────────
# NOTIFICATION TYPES
# ─────────────────────────────────────────────────────────────
 
async def notify_demo_booked(lead_name: str, company: str, phone: str, summary: str):
    """🎯 Highest priority — demo was booked."""
    msg = (
        f"🎯 <b>DEMO BOOKED!</b>\n\n"
        f"👤 <b>Name:</b> {lead_name or 'Unknown'}\n"
        f"🏢 <b>Company:</b> {company or '—'}\n"
        f"📱 <b>Phone:</b> {phone}\n\n"
        f"📝 <b>Summary:</b>\n{summary or '—'}\n\n"
        f"<i>— Aira | MuTech AI Caller</i>"
    )
    await send_message(msg)
 
 
async def notify_interested(lead_name: str, company: str, phone: str, summary: str):
    """👍 Lead showed interest — worth a follow-up."""
    msg = (
        f"👍 <b>Interested Lead!</b>\n\n"
        f"👤 <b>Name:</b> {lead_name or 'Unknown'}\n"
        f"🏢 <b>Company:</b> {company or '—'}\n"
        f"📱 <b>Phone:</b> {phone}\n\n"
        f"📝 <b>Summary:</b>\n{summary or '—'}\n\n"
        f"<i>— Aira | MuTech AI Caller</i>"
    )
    await send_message(msg)
 
 
async def notify_call_completed(lead_name: str, phone: str, duration_sec: int,
                                 sentiment: str, summary: str):
    """📞 Call completed — sent for every answered call."""
    emoji_map = {
        "demo_booked":   "🎯",
        "interested":    "👍",
        "neutral":       "😐",
        "rejected":      "❌",
    }
    emoji = emoji_map.get(sentiment, "📞")
    mins  = duration_sec // 60
    secs  = duration_sec % 60
 
    msg = (
        f"{emoji} <b>Call Completed</b>\n\n"
        f"👤 <b>Lead:</b> {lead_name or phone}\n"
        f"⏱ <b>Duration:</b> {mins}m {secs}s\n"
        f"📊 <b>Outcome:</b> {sentiment.replace('_', ' ').title()}\n\n"
        f"📝 <b>Summary:</b>\n{summary or '—'}\n\n"
        f"<i>— Aira | MuTech AI Caller</i>"
    )
    await send_message(msg)
 
 
async def notify_service_started():
    """🚀 Service came online."""
    msg = (
        f"🚀 <b>Aira is Online</b>\n\n"
        f"MuTech AI Caller service has started successfully.\n"
        f"Ready to make and receive calls.\n\n"
        f"<i>— MuTech Automation | ai.mutechautomation.com</i>"
    )
    await send_message(msg)
 
 
async def notify_campaign_completed(campaign_name: str, calls_made: int,
                                     calls_answered: int, demos_booked: int):
    """📊 Campaign finished."""
    answer_rate = round((calls_answered / calls_made * 100), 1) if calls_made > 0 else 0
    demo_rate   = round((demos_booked / calls_answered * 100), 1) if calls_answered > 0 else 0
 
    msg = (
        f"📊 <b>Campaign Completed!</b>\n\n"
        f"📁 <b>Campaign:</b> {campaign_name}\n\n"
        f"📞 Calls Made:     <b>{calls_made}</b>\n"
        f"✅ Answered:       <b>{calls_answered}</b> ({answer_rate}%)\n"
        f"🎯 Demos Booked:   <b>{demos_booked}</b> ({demo_rate}%)\n\n"
        f"<i>— Aira | MuTech AI Caller</i>"
    )
    await send_message(msg)
 
 
async def notify_tenant_created(tenant_name: str, slug: str, plan: str,
                                admin_username: str, admin_password: str,
                                dashboard_url: str, contact_email: str = ""):
    """🏢 New tenant created — send credentials to super admin."""
    msg = (
        f"🏢 <b>New Tenant Created!</b>\n\n"
        f"🏷 <b>Name:</b> {tenant_name}\n"
        f"🔑 <b>Slug:</b> {slug}\n"
        f"📦 <b>Plan:</b> {plan.title()}\n"
        + (f"📧 <b>Email:</b> {contact_email}\n" if contact_email else "")
        + f"\n🔐 <b>Admin Login Credentials:</b>\n"
        f"   <code>Username: {admin_username}</code>\n"
        f"   <code>Password: {admin_password}</code>\n\n"
        f"🌐 <b>Dashboard:</b> {dashboard_url}\n\n"
        f"<i>Share these credentials with the tenant. They should change password on first login.</i>"
    )
    await send_message(msg)


async def notify_error(error_message: str):
    """🚨 Something went wrong."""
    msg = (
        f"🚨 <b>Alert — MuTech AI Caller</b>\n\n"
        f"⚠️ {error_message}\n\n"
        f"<i>Check logs: journalctl -u ai-caller -n 50</i>"
    )
    await send_message(msg)
