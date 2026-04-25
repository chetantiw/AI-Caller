"""
app/usage_alert_service.py
Fires Telegram alerts when tenant hits 80% or 100% of minute quota.
Called from _post_call() in multi_agent_manager.py after log_usage().
"""
from __future__ import annotations
import asyncio
from loguru import logger


async def _send_telegram(token: str, chat_id: str, text: str) -> None:
    try:
        import httpx
        async with httpx.AsyncClient(timeout=8) as client:
            await client.post(
                f"https://api.telegram.org/bot{token}/sendMessage",
                json={"chat_id": chat_id, "text": text, "parse_mode": "HTML"},
            )
    except Exception as e:
        logger.warning(f"[UsageAlert] Telegram send failed: {e}")


async def check_and_alert(
    tenant_id: int,
    tenant_name: str,
    minutes_used: float,
    minutes_limit: int,
    telegram_token: str,
    telegram_chat_id: str,
    db_conn=None,          # pass sqlite3 connection to check/set alert_sent
) -> None:
    """
    Check quota and send Telegram alert at 80% and 100%.
    Uses alert_sent column in usage_logs to avoid duplicate alerts per day.
    """
    if not minutes_limit or not telegram_token or not telegram_chat_id:
        return

    pct = (minutes_used / minutes_limit * 100) if minutes_limit else 0

    if pct < 80:
        return

    alert_type = "quota_100" if pct >= 100 else "quota_80"

    # Check if already sent today
    if db_conn:
        try:
            row = db_conn.execute(
                "SELECT alert_sent FROM usage_logs WHERE tenant_id=? AND date=date('now')",
                (tenant_id,)
            ).fetchone()
            if row and row[0] == alert_type:
                return  # already sent
            # Mark as sent
            db_conn.execute(
                "UPDATE usage_logs SET alert_sent=? WHERE tenant_id=? AND date=date('now')",
                (alert_type, tenant_id)
            )
            db_conn.commit()
        except Exception as e:
            logger.warning(f"[UsageAlert] DB check failed: {e}")

    # Build message
    if pct >= 100:
        emoji = "🚨"
        status = "QUOTA EXCEEDED — All outbound calls are now paused."
        action = "Please top up your minutes or upgrade your plan to resume calling."
    else:
        emoji = "⚠️"
        status = f"Usage at {pct:.0f}% of monthly quota."
        action = "Consider topping up minutes or upgrading your plan to avoid interruption."

    msg = (
        f"{emoji} <b>DialBot Usage Alert</b>\n\n"
        f"🏢 <b>Tenant:</b> {tenant_name}\n"
        f"📊 <b>Minutes used:</b> {minutes_used:.0f} / {minutes_limit:,} ({pct:.1f}%)\n"
        f"📌 <b>Status:</b> {status}\n\n"
        f"💡 {action}\n\n"
        f"<i>Dashboard → Billing for details</i>"
    )

    asyncio.create_task(_send_telegram(telegram_token, telegram_chat_id, msg))
    logger.info(f"[UsageAlert] Fired {alert_type} for tenant {tenant_id} ({pct:.0f}%)")
