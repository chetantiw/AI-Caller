"""
app/openclaw_service.py

Per-tenant OpenClaw WhatsApp integration.

Design:
- Each tenant links its OWN OpenClaw profile / WhatsApp number via the client portal.
- MuTech's linked number (+918827055691) is ONLY for tenant profiles that explicitly
  point at the default/mutech OpenClaw profile — never a global fallback for all clients.
- Sending uses the local `openclaw` CLI with `--profile <tenant_profile>`.
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Optional

from loguru import logger

from app import tenant_db as tdb

OPENCLAW_BIN = os.environ.get("OPENCLAW_BIN") or shutil.which("openclaw") or "openclaw"
OPENCLAW_HOME = Path(os.environ.get("OPENCLAW_HOME", str(Path.home())))
DEFAULT_GATEWAY_URL = os.environ.get("OPENCLAW_GATEWAY_URL", "http://127.0.0.1:18789")


def _slugify(value: str) -> str:
    value = (value or "").strip().lower()
    value = re.sub(r"[^a-z0-9]+", "-", value)
    value = re.sub(r"-+", "-", value).strip("-")
    return value or "tenant"


def default_profile_for_tenant(tenant_id: int, tenant_config: Optional[dict] = None) -> str:
    """Stable per-tenant OpenClaw profile name. Never reuse another tenant's profile."""
    cfg = tenant_config or tdb.get_tenant_config(tenant_id) or {}
    existing = (cfg.get("openclaw_profile") or "").strip()
    if existing:
        return existing
    tenant = tdb.get_tenant(tenant_id) or {}
    slug = _slugify(tenant.get("slug") or tenant.get("name") or cfg.get("company_name") or f"tenant-{tenant_id}")
    return f"tenant{tenant_id}-{slug}"[:48]


def profile_state_dir(profile: str) -> Path:
    profile = (profile or "default").strip()
    if profile in ("", "default", "main"):
        return OPENCLAW_HOME / ".openclaw"
    return OPENCLAW_HOME / f".openclaw-{profile}"


def _normalize_e164(phone: str) -> str:
    digits = re.sub(r"[^\d+]", "", str(phone or ""))
    if not digits:
        return ""
    if digits.startswith("+"):
        return "+" + re.sub(r"\D", "", digits)
    digits = re.sub(r"\D", "", digits)
    if len(digits) == 10:
        digits = "91" + digits
    return "+" + digits


def _run_openclaw(args: list[str], profile: Optional[str] = None, timeout: int = 45) -> dict:
    """Run openclaw CLI and return parsed JSON when possible."""
    cmd = [OPENCLAW_BIN]
    if profile and profile not in ("default", "main", ""):
        cmd += ["--profile", profile]
    cmd += args
    env = os.environ.copy()
    env.setdefault("NO_COLOR", "1")
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            env=env,
        )
    except FileNotFoundError:
        return {"ok": False, "error": "openclaw_cli_not_found", "cmd": cmd}
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "openclaw_timeout", "cmd": cmd}

    stdout = (proc.stdout or "").strip()
    stderr = (proc.stderr or "").strip()
    payload = None
    for chunk in (stdout, stderr):
        if not chunk:
            continue
        # Prefer last JSON object in output
        try:
            payload = json.loads(chunk)
            break
        except Exception:
            # try last {...} block
            m = re.search(r"\{[\s\S]*\}\s*$", chunk)
            if m:
                try:
                    payload = json.loads(m.group(0))
                    break
                except Exception:
                    pass

    ok = proc.returncode == 0
    return {
        "ok": ok,
        "code": proc.returncode,
        "stdout": stdout[-4000:],
        "stderr": stderr[-2000:],
        "data": payload,
        "cmd": cmd,
    }


def ensure_tenant_openclaw_profile(tenant_id: int) -> dict:
    """
    Ensure tenant has an isolated OpenClaw profile name stored on tenant_configs.
    Does NOT auto-link WhatsApp and does NOT reuse MuTech's default session.
    """
    cfg = tdb.get_tenant_config(tenant_id) or {}
    profile = default_profile_for_tenant(tenant_id, cfg)
    updates = {}
    if (cfg.get("openclaw_profile") or "").strip() != profile:
        updates["openclaw_profile"] = profile
    if not (cfg.get("openclaw_account") or "").strip():
        updates["openclaw_account"] = "default"
    if not (cfg.get("whatsapp_provider") or "").strip():
        updates["whatsapp_provider"] = "openclaw"
    if updates:
        tdb.update_tenant_config(tenant_id, **updates)
        cfg = {**cfg, **updates}

    state = profile_state_dir(profile)
    state.mkdir(parents=True, exist_ok=True)
    # Touch a minimal config if missing so CLI profile isolation works.
    cfg_path = state / "openclaw.json"
    if not cfg_path.exists():
        minimal = {
            "meta": {"createdBy": "ai-caller", "tenantId": tenant_id},
            "agents": {"defaults": {"workspace": str(state / "workspace")}},
            "channels": {"whatsapp": {"enabled": True, "dmPolicy": "open", "allowFrom": ["*"]}},
            "gateway": {"mode": "local", "bind": "loopback"},
        }
        (state / "workspace").mkdir(parents=True, exist_ok=True)
        cfg_path.write_text(json.dumps(minimal, indent=2))
        try:
            os.chmod(cfg_path, 0o600)
        except Exception:
            pass

    return {
        "tenant_id": tenant_id,
        "profile": profile,
        "state_dir": str(state),
        "config": cfg,
    }


def get_openclaw_status(tenant_id: int) -> dict:
    """Return portal-friendly OpenClaw link status for one tenant only."""
    info = ensure_tenant_openclaw_profile(tenant_id)
    cfg = info["config"]
    profile = info["profile"]
    state = profile_state_dir(profile)

    # Probe CLI status for THIS profile only
    probe = _run_openclaw(["status", "--json"], profile=profile, timeout=25)
    linked = False
    sender = (cfg.get("openclaw_whatsapp_number") or "").strip()
    summary = []
    detail = ""

    data = probe.get("data") or {}
    if isinstance(data, dict):
        link = data.get("linkChannel") or {}
        if isinstance(link, dict) and link.get("id") == "whatsapp":
            linked = bool(link.get("linked"))
        for line in data.get("channelSummary") or []:
            summary.append(str(line))
            if "WhatsApp: linked" in str(line) or "whatsapp" in str(line).lower():
                m = re.search(r"\+(\d{8,15})", str(line))
                if m and not sender:
                    sender = "+" + m.group(1)
                if "linked" in str(line).lower():
                    linked = True
        detail = "; ".join(summary)[:500]

    # Fallback: credentials folder presence
    cred_dir = state / "credentials"
    if not linked and cred_dir.exists():
        # WhatsApp web auth files typically live under credentials/
        if any(cred_dir.rglob("*")):
            # still not proof of live link
            pass

    status = "linked" if linked else ("configured" if (cfg.get("openclaw_enabled") and sender) else "not_linked")
    # Persist latest observed status (best-effort)
    try:
        updates = {"openclaw_status": status}
        if linked and sender and sender != (cfg.get("openclaw_whatsapp_number") or ""):
            updates["openclaw_whatsapp_number"] = sender
        if linked and not cfg.get("openclaw_linked_at"):
            updates["openclaw_linked_at"] = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
        tdb.update_tenant_config(tenant_id, **updates)
    except Exception:
        pass

    return {
        "ok": True,
        "tenant_id": tenant_id,
        "enabled": bool(cfg.get("openclaw_enabled", 0)),
        "whatsapp_enabled": bool(cfg.get("whatsapp_enabled", 0)),
        "provider": cfg.get("whatsapp_provider") or "openclaw",
        "profile": profile,
        "account": cfg.get("openclaw_account") or "default",
        "sender_number": sender or "",
        "status": status,
        "linked": linked,
        "linked_at": cfg.get("openclaw_linked_at") or "",
        "gateway_url": cfg.get("openclaw_gateway_url") or DEFAULT_GATEWAY_URL,
        "detail": detail or (probe.get("stderr") or probe.get("stdout") or "")[:400],
        "state_dir": str(state),
        "cli_ok": bool(probe.get("ok")),
        # Explicitly document that default MuTech session is NOT used unless this
        # tenant's profile is the default profile AND they opted in.
        "uses_shared_mutech_session": profile in ("default", "main", ""),
    }


def start_whatsapp_link(tenant_id: int) -> dict:
    """
    Prepare tenant profile for WhatsApp linking.
    Actual QR login is interactive (`openclaw --profile X channels login --channel whatsapp`).
    Portal stores the profile + enables OpenClaw for this tenant only.
    """
    info = ensure_tenant_openclaw_profile(tenant_id)
    profile = info["profile"]
    tdb.update_tenant_config(
        tenant_id,
        openclaw_enabled=1,
        openclaw_profile=profile,
        whatsapp_provider="openclaw",
        openclaw_status="pending_link",
    )
    login_cmd = f"openclaw --profile {profile} channels login --channel whatsapp --verbose"
    return {
        "ok": True,
        "profile": profile,
        "status": "pending_link",
        "message": (
            "OpenClaw profile ready for this client. Complete WhatsApp link with the command below "
            "on the server (QR scan). This does not use another client's number."
        ),
        "login_command": login_cmd,
        "docs": "After scanning QR, click Refresh Status in the portal.",
    }


def unlink_whatsapp(tenant_id: int) -> dict:
    """Disable OpenClaw WhatsApp for this tenant only (does not delete other tenants)."""
    info = ensure_tenant_openclaw_profile(tenant_id)
    profile = info["profile"]
    # Best-effort logout for this profile
    _run_openclaw(["channels", "logout", "--channel", "whatsapp"], profile=profile, timeout=30)
    tdb.update_tenant_config(
        tenant_id,
        openclaw_enabled=0,
        openclaw_status="not_linked",
        openclaw_whatsapp_number="",
        openclaw_linked_at="",
        whatsapp_enabled=0,
    )
    return {"ok": True, "profile": profile, "status": "not_linked"}


async def send_whatsapp_openclaw(
    phone: str,
    message: str,
    tenant_config: dict,
    tenant_id: Optional[int] = None,
) -> bool:
    """
    Send WhatsApp via the tenant's own OpenClaw profile.
    Returns False if this tenant has no linked OpenClaw WhatsApp.
    """
    if not message or not str(message).strip():
        logger.warning("[OpenClaw] Empty message")
        return False

    tid = tenant_id or tenant_config.get("tenant_id")
    if tid is None:
        # try resolve from config row
        tid = tenant_config.get("id")

    profile = (tenant_config.get("openclaw_profile") or "").strip()
    if not profile and tid is not None:
        profile = default_profile_for_tenant(int(tid), tenant_config)

    if not profile:
        logger.warning("[OpenClaw] No profile configured for tenant")
        return False

    # Hard guard: do not silently fall back to default MuTech session for arbitrary tenants.
    # Only profiles explicitly stored on the tenant may send.
    if not bool(tenant_config.get("openclaw_enabled", 0)):
        logger.warning(f"[OpenClaw] Disabled for profile={profile}")
        return False

    target = _normalize_e164(phone)
    if not target:
        logger.warning("[OpenClaw] Invalid target phone")
        return False

    account = (tenant_config.get("openclaw_account") or "default").strip() or "default"
    args = [
        "message", "send",
        "--channel", "whatsapp",
        "--account", account,
        "--target", target,
        "--message", message,
        "--json",
    ]

    loop = asyncio.get_event_loop()
    result = await loop.run_in_executor(None, lambda: _run_openclaw(args, profile=profile, timeout=60))

    ok = bool(result.get("ok"))
    data = result.get("data") or {}
    if isinstance(data, dict) and data.get("ok") is False:
        ok = False

    if ok:
        logger.info(f"[OpenClaw] WhatsApp sent via profile={profile} to {target}")
    else:
        logger.error(
            f"[OpenClaw] Send failed profile={profile} target={target} "
            f"code={result.get('code')} err={(result.get('stderr') or result.get('stdout') or '')[:300]}"
        )

    # Audit log
    try:
        from app.database import get_conn
        with get_conn() as conn:
            conn.execute(
                """INSERT INTO notification_logs
                   (tenant_id, channel, provider, target, outcome, status, detail)
                   VALUES (?, 'whatsapp', ?, ?, '', ?, ?)""",
                (
                    int(tid) if tid is not None else 0,
                    f"openclaw:{profile}",
                    target,
                    "sent" if ok else "failed",
                    json.dumps({
                        "code": result.get("code"),
                        "stderr": (result.get("stderr") or "")[:500],
                        "stdout": (result.get("stdout") or "")[:500],
                    })[:1000],
                ),
            )
            conn.commit()
    except Exception as e:
        logger.debug(f"[OpenClaw] notification_logs write skipped: {e}")

    return ok


async def send_test_whatsapp(tenant_id: int, target_phone: str, message: Optional[str] = None) -> dict:
    cfg = tdb.get_tenant_config(tenant_id) or {}
    cfg = {**cfg, "tenant_id": tenant_id}
    ensure_tenant_openclaw_profile(tenant_id)
    cfg = tdb.get_tenant_config(tenant_id) or cfg
    msg = message or (
        f"Test from {cfg.get('company_name') or 'AI Caller'} via OpenClaw. "
        f"If you received this, WhatsApp feedback is linked for this client only."
    )
    ok = await send_whatsapp_openclaw(target_phone, msg, cfg, tenant_id=tenant_id)
    status = get_openclaw_status(tenant_id)
    return {"ok": ok, "target": _normalize_e164(target_phone), "status": status}
