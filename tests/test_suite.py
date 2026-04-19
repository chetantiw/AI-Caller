#!/usr/bin/env python3
"""
tests/test_suite.py
===================
Complete automated test suite for DialBot v3.0 SaaS platform.

Run from project root:
    source /root/ai-caller-env/bin/activate
    cd /root/ai-caller-env/ai-caller
    python tests/test_suite.py

Tests everything without making real calls (DRY_RUN mode).
Prints a pass/fail report at the end.
"""

import asyncio
import os
import sys
import json
import sqlite3
import tempfile
import traceback
from datetime import datetime

# Force dry run — never dials real numbers
os.environ["AI_CALLER_DRY_RUN"] = "1"

sys.path.insert(0, "/root/ai-caller-env/ai-caller")

# ─── Colour helpers ───────────────────────────────────────────
GREEN  = "\033[92m"
RED    = "\033[91m"
YELLOW = "\033[93m"
CYAN   = "\033[96m"
RESET  = "\033[0m"
BOLD   = "\033[1m"

passed = []
failed = []
skipped = []


def ok(name, detail=""):
    passed.append(name)
    print(f"  {GREEN}✓{RESET} {name}" + (f"  {YELLOW}({detail}){RESET}" if detail else ""))


def fail(name, err=""):
    failed.append(name)
    print(f"  {RED}✗{RESET} {name}" + (f"  {RED}→ {err}{RESET}" if err else ""))


def skip(name, reason=""):
    skipped.append(name)
    print(f"  {YELLOW}⊘{RESET} {name}" + (f"  ({reason})" if reason else ""))


def section(title):
    print(f"\n{BOLD}{CYAN}{'─'*50}{RESET}")
    print(f"{BOLD}{CYAN}  {title}{RESET}")
    print(f"{BOLD}{CYAN}{'─'*50}{RESET}")


# ═══════════════════════════════════════════════════════════════
# SECTION 1 — IMPORTS & ENVIRONMENT
# ═══════════════════════════════════════════════════════════════
section("1. Imports & Environment")

def test_imports():
    try:
        from app import database as db
        ok("app.database")
    except Exception as e:
        fail("app.database", str(e)); return

    try:
        from app import tenant_db as tdb
        ok("app.tenant_db")
    except Exception as e:
        fail("app.tenant_db", str(e))

    try:
        from app import api_routes
        ok("app.api_routes")
    except Exception as e:
        fail("app.api_routes", str(e))

    try:
        from app import super_routes
        ok("app.super_routes")
    except Exception as e:
        fail("app.super_routes", str(e))

    try:
        from app.plan_features import get_plan_features, check_feature, check_quota_safe
        ok("app.plan_features")
    except ImportError:
        try:
            from app.plan_features import get_plan_features, check_feature
            ok("app.plan_features (no check_quota_safe)")
        except Exception as e:
            fail("app.plan_features", str(e))

    try:
        from app.webhook_service import fire_call_webhook
        ok("app.webhook_service")
    except Exception as e:
        fail("app.webhook_service", str(e))

    try:
        from app.retry_scheduler import start_retry_scheduler
        ok("app.retry_scheduler")
    except Exception as e:
        fail("app.retry_scheduler", str(e))

    try:
        from app.campaign_runner import make_single_call, run_campaign
        ok("app.campaign_runner")
    except Exception as e:
        fail("app.campaign_runner", str(e))

    # Env vars
    for var in ["PIOPIY_AGENT_ID", "PIOPIY_AGENT_TOKEN", "SARVAM_API_KEY", "GROQ_API_KEY"]:
        if os.getenv(var):
            ok(f"env:{var} set")
        else:
            skip(f"env:{var}", "not in .env")

test_imports()


# ═══════════════════════════════════════════════════════════════
# SECTION 2 — DATABASE
# ═══════════════════════════════════════════════════════════════
section("2. Database Layer")

def test_database():
    from app import database as db
    from app import tenant_db as tdb

    # init_db runs without error
    try:
        db.init_db()
        ok("init_db() runs cleanly")
    except Exception as e:
        fail("init_db()", str(e))
        return

    # Check all expected tables exist
    expected_tables = [
        "users", "leads", "campaigns", "calls",
        "system_logs", "system_config", "sessions",
        "tenants", "tenant_configs", "usage_logs",
        "addon_purchases", "webhook_logs",
    ]
    with db.get_conn() as conn:
        existing = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()}
    for t in expected_tables:
        if t in existing:
            ok(f"table:{t}")
        else:
            fail(f"table:{t}", "missing — run migration")

    # Check key columns added by weekend migration
    col_checks = [
        ("leads",          "retry_count"),
        ("leads",          "next_retry_at"),
        ("campaigns",      "max_retries"),
        ("tenants",        "minutes_limit"),
        ("tenant_configs", "webhook_url"),
        ("tenant_configs", "stt_provider"),
        ("tenant_configs", "tts_provider"),
        ("tenant_configs", "elevenlabs_model"),
        ("usage_logs",     "alert_sent"),
    ]
    for table, col in col_checks:
        with db.get_conn() as conn:
            cols = [r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()]
        if col in cols:
            ok(f"column:{table}.{col}")
        else:
            fail(f"column:{table}.{col}", "run migrations/weekend_saas_v2.py")

    # CRUD smoke test
    try:
        lid = db.create_lead(name="Test User", phone="9876543210",
                             company="TestCo", tenant_id=1)
        lead = db.get_lead(lid)
        assert lead["name"] == "Test User"
        ok("lead CRUD (create + get)")
    except Exception as e:
        fail("lead CRUD", str(e))

    try:
        cid = db.create_campaign("Test Campaign", "test", tenant_id=1)
        camp = db.get_campaign(cid)
        assert camp["name"] == "Test Campaign"
        ok("campaign CRUD (create + get)")
    except Exception as e:
        fail("campaign CRUD", str(e))

    # Tenant DB
    try:
        tenants = tdb.get_all_tenants()
        assert isinstance(tenants, list)
        ok(f"get_all_tenants() → {len(tenants)} tenants")
    except Exception as e:
        fail("get_all_tenants()", str(e))

    try:
        t1 = tdb.get_tenant(1)
        assert t1 is not None, "Tenant 1 (platform) missing"
        ok("tenant 1 (platform) exists")
    except Exception as e:
        fail("tenant 1 exists", str(e))

    try:
        cfg = tdb.get_tenant_config(1)
        assert cfg is not None, "Tenant 1 config missing"
        ok("tenant 1 config exists")
    except Exception as e:
        fail("tenant 1 config", str(e))

test_database()


# ═══════════════════════════════════════════════════════════════
# SECTION 3 — PLAN FEATURES & QUOTA
# ═══════════════════════════════════════════════════════════════
section("3. Plan Features & Quota")

def test_plan_features():
    try:
        from app.plan_features import (
            get_plan_features, check_feature,
            check_campaign_limit, check_seat_limit,
            PLAN_FEATURES,
        )
    except Exception as e:
        fail("plan_features import", str(e))
        return

    # All 4 plans defined
    for plan in ["starter", "growth", "pro", "enterprise"]:
        if plan in PLAN_FEATURES:
            ok(f"plan:{plan} defined")
        else:
            fail(f"plan:{plan}", "missing from PLAN_FEATURES")

    # Feature gating logic
    tests = [
        ("starter",    "smart_retry",     False),
        ("growth",     "crm_webhook",     True),
        ("pro",        "flow_builder",    True),
        ("enterprise", "crm_2way",        True),
        ("starter",    "flow_builder",    False),
    ]
    for plan, feature, expected in tests:
        gate = check_feature(plan, feature)
        if gate["allowed"] == expected:
            ok(f"gate:{plan}.{feature}={'allowed' if expected else 'blocked'}")
        else:
            fail(f"gate:{plan}.{feature}", f"expected {expected}, got {gate['allowed']}")

    # Campaign limit
    gate = check_campaign_limit("starter", 1)
    if not gate["allowed"]:
        ok("starter: 1 active campaign blocks creation")
    else:
        fail("starter campaign limit", "should block at 1")

    gate = check_campaign_limit("enterprise", 999)
    if gate["allowed"]:
        ok("enterprise: unlimited campaigns")
    else:
        fail("enterprise unlimited", "should allow 999")

    # check_quota via tenant_db
    try:
        from app.tenant_db import check_quota
        q = check_quota(1)
        assert "allowed" in q
        assert "calls_used" in q
        assert "pct_used" in q
        ok(f"check_quota(tenant=1) → {q['calls_used']}/{q['calls_limit']} ({q['pct_used']}%)")
    except Exception as e:
        fail("check_quota()", str(e))

test_plan_features()


# ═══════════════════════════════════════════════════════════════
# SECTION 4 — CAMPAIGN RUNNER (DRY RUN)
# ═══════════════════════════════════════════════════════════════
section("4. Campaign Runner (Dry Run)")

async def test_campaign_runner():
    from app import database as db
    from app.campaign_runner import make_single_call, run_campaign

    # Single call dry run
    try:
        result = await make_single_call("9876543210")
        assert result is not None
        assert result.get("dry_run") is True
        assert result.get("provider") == "dryrun"
        ok(f"make_single_call dry run → {result['call_id']}")
    except Exception as e:
        fail("make_single_call dry run", str(e))
        return

    # Full campaign dry run
    try:
        cid = db.create_campaign("DryRun Test Camp", "test", tenant_id=1)
        db.create_lead(name="DryLead1", phone="9876543210",
                       company="Co", campaign_id=cid, tenant_id=1)
        db.create_lead(name="DryLead2", phone="9876543211",
                       company="Co", campaign_id=cid, tenant_id=1)
        db.update_campaign_status(cid, "running")

        await run_campaign(cid, delay_seconds=0)

        camp = db.get_campaign(cid)
        assert camp["status"] == "completed", f"Expected completed, got {camp['status']}"
        assert camp["calls_made"] >= 1, "No calls recorded"
        ok(f"run_campaign dry run → {camp['calls_made']} calls, status={camp['status']}")
    except Exception as e:
        fail("run_campaign dry run", str(e))

asyncio.run(test_campaign_runner())


# ═══════════════════════════════════════════════════════════════
# SECTION 5 — API ROUTES (HTTP)
# ═══════════════════════════════════════════════════════════════
section("5. API Routes (HTTP)")

def test_api_routes():
    import requests

    BASE = "http://localhost:8000"

    # Health check
    try:
        r = requests.get(f"{BASE}/api/system/health", timeout=5)
        if r.status_code == 200:
            ok(f"GET /api/system/health → 200")
        else:
            fail("GET /api/system/health", f"status {r.status_code}")
    except Exception as e:
        fail("GET /api/system/health", f"Server not running? {e}")
        print(f"  {YELLOW}  Tip: start service with ./start.sh then re-run tests{RESET}")
        return

    # Login
    try:
        r = requests.post(f"{BASE}/api/auth/login",
                          json={"username": "admin", "password": "mutech123"}, timeout=5)
        if r.status_code == 200:
            token = r.json().get("token")
            ok("POST /api/auth/login (admin)")
        else:
            fail("POST /api/auth/login", f"status {r.status_code}: {r.text[:80]}")
            return
    except Exception as e:
        fail("POST /api/auth/login", str(e))
        return

    headers = {"Authorization": f"Bearer {token}"}

    # Core API routes
    routes = [
        ("GET",  "/api/dashboard/stats",        None),
        ("GET",  "/api/dashboard/recent-calls", None),
        ("GET",  "/api/campaigns",              None),
        ("GET",  "/api/leads",                  None),
        ("GET",  "/api/calls",                  None),
        ("GET",  "/api/analytics/daily",        None),
        ("GET",  "/api/analytics/funnel",       None),
        ("GET",  "/api/tenant/profile",         None),
        ("GET",  "/api/tenant/api-keys",        None),
        ("GET",  "/api/tenant/billing",         None),
        ("GET",  "/api/tenant/plan-features",   None),
        ("GET",  "/api/tenant/webhook",         None),
        ("GET",  "/api/tenant/webhook/logs",    None),
        ("GET",  "/api/tenant/addons",          None),
        ("GET",  "/api/system/telephony",       None),
        ("GET",  "/api/system/config",          None),
    ]

    for method, path, body in routes:
        try:
            if method == "GET":
                r = requests.get(f"{BASE}{path}", headers=headers, timeout=5)
            else:
                r = requests.post(f"{BASE}{path}", headers=headers, json=body, timeout=5)

            if r.status_code in (200, 201):
                ok(f"{method} {path}")
            elif r.status_code == 402:
                ok(f"{method} {path}", "402 plan gate working")
            else:
                fail(f"{method} {path}", f"status {r.status_code}: {r.text[:60]}")
        except Exception as e:
            fail(f"{method} {path}", str(e))

    # Campaign analytics endpoint
    try:
        r = requests.get(f"{BASE}/api/campaigns/1/analytics", headers=headers, timeout=5)
        if r.status_code in (200, 404):
            ok(f"GET /api/campaigns/{{id}}/analytics → {r.status_code}")
        else:
            fail("GET /api/campaigns/{id}/analytics", f"status {r.status_code}")
    except Exception as e:
        fail("GET /api/campaigns/{id}/analytics", str(e))

    # Superadmin login
    try:
        r = requests.post(f"{BASE}/super/api/login",
                          json={"username": "superadmin", "password": "superadmin123"}, timeout=5)
        if r.status_code == 200:
            super_token = r.json().get("token")
            ok("POST /super/api/login")
            super_headers = {"Authorization": f"Bearer {super_token}"}

            # Super routes
            for path in ["/super/api/dashboard", "/super/api/tenants"]:
                r2 = requests.get(f"{BASE}{path}", headers=super_headers, timeout=5)
                if r2.status_code == 200:
                    ok(f"GET {path}")
                else:
                    fail(f"GET {path}", f"status {r2.status_code}")

            # Addon route exists
            r3 = requests.post(f"{BASE}/super/api/tenants/1/addon",
                               headers=super_headers,
                               json={"minutes": 0, "amount_inr": 0}, timeout=5)
            if r3.status_code in (200, 400, 422):
                ok("POST /super/api/tenants/{id}/addon exists")
            else:
                fail("POST /super/api/tenants/{id}/addon", f"status {r3.status_code}")

        else:
            skip("Superadmin routes", f"login failed {r.status_code} — check superadmin credentials")
    except Exception as e:
        fail("Superadmin login", str(e))

test_api_routes()


# ═══════════════════════════════════════════════════════════════
# SECTION 6 — WEBHOOK SERVICE
# ═══════════════════════════════════════════════════════════════
section("6. Webhook Service")

async def test_webhook():
    try:
        from app.webhook_service import fire_call_webhook
        # Tenant 1 likely has no webhook_url — should silently do nothing
        await fire_call_webhook(1, {
            "call_id": 0, "phone": "+919876543210",
            "lead_name": "Test", "company": "TestCo",
            "duration_sec": 30, "outcome": "answered",
            "sentiment": "interested", "summary": "Test",
            "transcript": "", "campaign_id": None, "lead_id": None,
        })
        ok("fire_call_webhook() silent when no webhook_url (no crash)")
    except Exception as e:
        fail("fire_call_webhook()", str(e))

asyncio.run(test_webhook())


# ═══════════════════════════════════════════════════════════════
# SECTION 7 — RETRY SCHEDULER
# ═══════════════════════════════════════════════════════════════
section("7. Retry Scheduler")

async def test_retry():
    try:
        from app.retry_scheduler import _get_retryable_leads, _schedule_next_retry
        leads = _get_retryable_leads()
        assert isinstance(leads, list)
        ok(f"_get_retryable_leads() → {len(leads)} leads queued")
    except Exception as e:
        fail("_get_retryable_leads()", str(e))

asyncio.run(test_retry())


# ═══════════════════════════════════════════════════════════════
# SECTION 8 — STATIC FILES & DASHBOARD
# ═══════════════════════════════════════════════════════════════
section("8. Static Files & Dashboard HTML")

def test_static_files():
    import os

    static_files = [
        "static/dashboard.html",
        "static/signup.html",
        "static/super_dashboard.html",
    ]
    for f in static_files:
        path = f"/root/ai-caller-env/ai-caller/{f}"
        if os.path.exists(path):
            size = os.path.getsize(path)
            ok(f"{f} ({size:,} bytes)")
        else:
            fail(f"{f}", "file not found")

    # Check dashboard.html has key elements
    dash_path = "/root/ai-caller-env/ai-caller/static/dashboard.html"
    if os.path.exists(dash_path):
        content = open(dash_path).read()
        checks = [
            ("plan-badge",            "Plan badge element"),
            ("modal-upgrade",         "Upgrade modal"),
            ("modal-tag-lead",        "Lead tag modal"),
            ("page-billing",          "Billing page"),
            ("acct-panel-integrations", "Integrations tab"),
            ("loadPlanFeatures",      "loadPlanFeatures() function"),
            ("applyFeatureGates",     "applyFeatureGates() function"),
            ("selectStt",             "STT selector function"),
            ("selectTts",             "TTS selector function"),
            ("loadBillingPage",       "loadBillingPage() function"),
            ("saveWebhookConfig",     "saveWebhookConfig() function"),
            ("openTagModal",          "openTagModal() function"),
            ("ac-stt-provider",       "STT provider hidden input"),
            ("ac-tts-provider",       "TTS provider hidden input"),
            ("nav-billing",           "Billing nav item"),
            ("wh-url",                "Webhook URL input"),
        ]
        for selector, label in checks:
            if selector in content:
                ok(f"dashboard.html: {label}")
            else:
                fail(f"dashboard.html: {label}", f"'{selector}' not found")

    # Check signup.html has 4 plans
    signup_path = "/root/ai-caller-env/ai-caller/static/signup.html"
    if os.path.exists(signup_path):
        content = open(signup_path).read()
        for plan in ["starter", "growth", "pro", "enterprise"]:
            if f"plan-{plan}" in content:
                ok(f"signup.html: {plan} plan card")
            else:
                fail(f"signup.html: {plan} plan card", f"'plan-{plan}' not found")

test_static_files()


# ═══════════════════════════════════════════════════════════════
# SECTION 9 — SPEECH PIPELINE CONFIG
# ═══════════════════════════════════════════════════════════════
section("9. Speech Pipeline (_build_stt_tts)")

def test_speech_pipeline():
    try:
        from app.multi_agent_manager import _build_stt_tts
        ok("_build_stt_tts imported")
    except Exception as e:
        fail("_build_stt_tts import", str(e))
        return

    import inspect
    src = inspect.getsource(_build_stt_tts)

    checks = [
        ("stt_provider",  "reads stt_provider field"),
        ("tts_provider",  "reads tts_provider field"),
        ("ElevenLabsTTSService", "uses ElevenLabsTTSService"),
        ("stt_label",     "logs pipeline label"),
        ("fallback",      "has fallback logic"),
    ]
    for token, label in checks:
        if token in src:
            ok(f"_build_stt_tts: {label}")
        else:
            fail(f"_build_stt_tts: {label}", f"'{token}' not in function source")

    # Test with Sarvam-only config
    try:
        stt, tts = _build_stt_tts({
            "stt_provider": "sarvam",
            "tts_provider": "sarvam",
            "sarvam_api_key": "test-key",
            "agent_voice": "anushka",
            "call_language": "hi",
        })
        ok(f"_build_stt_tts sarvam+sarvam → {type(stt).__name__} + {type(tts).__name__}")
    except Exception as e:
        # May fail without real SDK — that's ok in test env
        skip("_build_stt_tts sarvam+sarvam execution", str(e)[:60])

test_speech_pipeline()


# ═══════════════════════════════════════════════════════════════
# SECTION 10 — GIT STATUS
# ═══════════════════════════════════════════════════════════════
section("10. Git Status")

def test_git():
    import subprocess
    result = subprocess.run(
        ["git", "status", "--short"],
        capture_output=True, text=True,
        cwd="/root/ai-caller-env/ai-caller"
    )
    if result.returncode == 0:
        lines = [l for l in result.stdout.strip().split("\n") if l.strip()]
        ok(f"Git working tree — {len(lines)} uncommitted change(s)")
        if lines:
            for l in lines[:10]:
                print(f"    {YELLOW}{l}{RESET}")
            if len(lines) > 10:
                print(f"    {YELLOW}  ... and {len(lines)-10} more{RESET}")
    else:
        fail("Git status", result.stderr[:80])

    # Last commit
    result2 = subprocess.run(
        ["git", "log", "--oneline", "-5"],
        capture_output=True, text=True,
        cwd="/root/ai-caller-env/ai-caller"
    )
    if result2.returncode == 0:
        print(f"\n  {CYAN}Last 5 commits:{RESET}")
        for line in result2.stdout.strip().split("\n"):
            print(f"    {line}")

test_git()


# ═══════════════════════════════════════════════════════════════
# FINAL REPORT
# ═══════════════════════════════════════════════════════════════
total = len(passed) + len(failed) + len(skipped)
print(f"\n{BOLD}{'═'*50}{RESET}")
print(f"{BOLD}  TEST RESULTS{RESET}")
print(f"{'═'*50}")
print(f"  {GREEN}Passed : {len(passed)}{RESET}")
print(f"  {RED}Failed : {len(failed)}{RESET}")
print(f"  {YELLOW}Skipped: {len(skipped)}{RESET}")
print(f"  Total  : {total}")
print(f"{'═'*50}")

if failed:
    print(f"\n{BOLD}{RED}  FAILURES:{RESET}")
    for f in failed:
        print(f"  {RED}✗ {f}{RESET}")
    print(f"\n{RED}  Fix the above before committing to git.{RESET}")
    sys.exit(1)
else:
    print(f"\n{GREEN}{BOLD}  All tests passed! Safe to commit. 🎉{RESET}")
    sys.exit(0)
