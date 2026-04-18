"""
app/webhook_service.py

Post-call outbound webhook delivery.
Fires a JSON POST to the tenant's configured webhook_url after every call.

Used by:
  - multi_agent_manager.py  (after PIOPIY call ends)
  - exotel_pipeline.py      (after Exotel call ends)

Usage:
    from app.webhook_service import fire_call_webhook
    asyncio.create_task(fire_call_webhook(tenant_id, call_data))
"""

import asyncio
import hashlib
import hmac
import json
import time
import os
from datetime import datetime

import httpx
from loguru import logger

from app import tenant_db as tdb
from app import database as db
from app.plan_features import check_feature


async def fire_call_webhook(tenant_id: int, call_data: dict) -> None:
    """
    Fire-and-forget webhook to tenant's configured endpoint.
    Checks plan gate before firing.
    Logs result to webhook_logs table.
    Times out after 8 seconds — never blocks call completion.
    """
    try:
        # ── Plan gate ─────────────────────────────────────────
        tenant = tdb.get_tenant(tenant_id)
        if not tenant:
            return
        plan = tenant.get("plan", "starter")
        gate = check_feature(plan, "crm_webhook")
        if not gate["allowed"]:
            return

        # ── Load config ───────────────────────────────────────
        cfg = tdb.get_tenant_config(tenant_id) or {}
        webhook_url    = (cfg.get("webhook_url") or "").strip()
        webhook_secret = (cfg.get("webhook_secret") or "").strip()
        webhook_events = (cfg.get("webhook_events") or "call_completed")

        if not webhook_url:
            return

        # ── Build payload ─────────────────────────────────────
        payload = {
            "event":       "call_completed",
            "timestamp":   datetime.utcnow().isoformat() + "Z",
            "tenant_id":   tenant_id,
            "call": {
                "id":           call_data.get("call_id") or call_data.get("id"),
                "phone":        call_data.get("phone", ""),
                "lead_name":    call_data.get("lead_name", ""),
                "company":      call_data.get("company", ""),
                "duration_sec": call_data.get("duration_sec", 0),
                "outcome":      call_data.get("outcome", "answered"),
                "sentiment":    call_data.get("sentiment", "neutral"),
                "summary":      call_data.get("summary", ""),
                "transcript":   call_data.get("transcript", ""),
                "campaign_id":  call_data.get("campaign_id"),
                "lead_id":      call_data.get("lead_id"),
            },
        }
        payload_str = json.dumps(payload, ensure_ascii=False)

        # ── HMAC signature ────────────────────────────────────
        headers = {
            "Content-Type":        "application/json",
            "X-DialBot-Event":     "call_completed",
            "X-DialBot-Timestamp": str(int(time.time())),
        }
        if webhook_secret:
            sig = hmac.new(
                webhook_secret.encode(), payload_str.encode(), hashlib.sha256
            ).hexdigest()
            headers["X-DialBot-Signature"] = f"sha256={sig}"

        # ── Fire ──────────────────────────────────────────────
        status_code = None
        response_text = ""
        try:
            async with httpx.AsyncClient(timeout=8.0) as client:
                resp = await client.post(
                    webhook_url,
                    content=payload_str,
                    headers=headers,
                )
                status_code   = resp.status_code
                response_text = resp.text[:500]
                if 200 <= status_code < 300:
                    logger.info(
                        f"[Webhook] Tenant {tenant_id} → {webhook_url} "
                        f"status={status_code}"
                    )
                else:
                    logger.warning(
                        f"[Webhook] Tenant {tenant_id} → {webhook_url} "
                        f"status={status_code} body={response_text[:100]}"
                    )
        except Exception as req_err:
            logger.error(f"[Webhook] Tenant {tenant_id} request failed: {req_err}")
            response_text = str(req_err)

        # ── Log to DB ─────────────────────────────────────────
        try:
            with db.get_conn() as conn:
                conn.execute("""
                    INSERT INTO webhook_logs
                        (tenant_id, event, url, payload, status_code, response)
                    VALUES (?, 'call_completed', ?, ?, ?, ?)
                """, (
                    tenant_id,
                    webhook_url,
                    payload_str[:2000],
                    status_code,
                    response_text,
                ))
                conn.commit()
        except Exception as log_err:
            logger.error(f"[Webhook] Failed to log to DB: {log_err}")

    except Exception as e:
        # Never propagate — webhook must never crash the caller
        logger.error(f"[Webhook] Unhandled error for tenant {tenant_id}: {e}")
