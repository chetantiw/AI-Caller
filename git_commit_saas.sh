#!/bin/bash
# git_commit_saas.sh
# Run AFTER tests pass.
# Usage: bash git_commit_saas.sh

set -e

cd /root/ai-caller-env/ai-caller

echo "============================================"
echo "  DialBot SaaS — Git Commit Script"
echo "============================================"

# 1. Show current status
echo ""
echo "📋 Current git status:"
git status --short

echo ""
echo "📋 Last 3 commits:"
git log --oneline -3

# 2. Confirm
echo ""
read -p "Proceed with staging and committing? (y/N): " confirm
if [[ "$confirm" != "y" && "$confirm" != "Y" ]]; then
    echo "Aborted."
    exit 0
fi

# 3. Stage everything EXCEPT sensitive files
git add \
  app/multi_agent_manager.py \
  app/api_routes.py \
  app/super_routes.py \
  app/tenant_db.py \
  app/campaign_runner.py \
  app/plan_features.py \
  app/webhook_service.py \
  app/retry_scheduler.py \
  app/main.py \
  app/database.py \
  app/exotel_pipeline.py \
  migrations/ \
  static/dashboard.html \
  static/super_dashboard.html \
  static/signup.html \
  tests/ \
  prompts/ \
  .github/ \
  QUICK_START.md \
  PIOPIY_TESTING_GUIDE.md \
  start.sh \
  requirements.txt 2>/dev/null || true

# Stage any new files not listed above (excluding secrets)
git add --all \
  -- ':!.env' \
  ':!*.db' \
  ':!*.db-*' \
  ':!mutech.db*' \
  ':!*.bak' \
  ':!logs/' \
  ':!leads/' \
  ':!__pycache__/' \
  ':!*.pyc' \
  ':!backups/' \
  ':!simple_test.txt' \
  ':!.claude/' 2>/dev/null || true

echo ""
echo "📋 Staged files:"
git diff --cached --name-only

# 4. Commit
COMMIT_MSG="feat: DialBot SaaS v3.0 — complete platform implementation

Backend:
- plan_features.py: 4-tier plan matrix (Starter/Growth/Pro/Enterprise)
- tenant_db.py: check_quota(), usage alerts (80%/100%), _fire_usage_alert()
- campaign_runner.py: quota gate per call, smart retry scheduling
- multi_agent_manager.py: _build_stt_tts() reads stt_provider/tts_provider independently
- webhook_service.py: HMAC-signed post-call webhook delivery
- retry_scheduler.py: 5-min background retry engine
- super_routes.py: addon grant endpoint (POST /super/api/tenants/{id}/addon)
- api_routes.py: billing, plan-features, webhook CRUD, addon, campaign analytics

Database migrations (weekend_saas_v2.py):
- leads: retry_count, next_retry_at
- campaigns: max_retries
- tenants: minutes_limit
- tenant_configs: webhook_url, webhook_secret, webhook_events,
                  stt_provider, tts_provider, elevenlabs_model
- usage_logs: alert_sent
- New tables: addon_purchases, webhook_logs

Frontend:
- dashboard.html: Billing page (3-row layout, quota bar, 30d table, CSV export)
- dashboard.html: Feature lock overlays + plan badge + upgrade modal
- dashboard.html: Integrations tab (webhook URL, events, secret, test, delivery log)
- dashboard.html: STT/TTS split UI (replaces old single speech_provider card)
- dashboard.html: Lead tagging modal from Call Logs
- super_dashboard.html: Addon grant modal (+ Mins button per tenant)
- signup.html: 4 plan cards (Starter / Growth / Pro / Enterprise)

Tests:
- tests/test_suite.py: 10-section automated test suite (dry-run, no real calls)"

git commit -m "$COMMIT_MSG"

echo ""
echo "✅ Committed successfully."

# 5. Push
echo ""
read -p "Push to origin/main? (y/N): " push_confirm
if [[ "$push_confirm" == "y" || "$push_confirm" == "Y" ]]; then
    git push origin main
    echo "✅ Pushed to GitHub."
else
    echo "Skipped push. Run 'git push origin main' when ready."
fi

echo ""
echo "============================================"
echo "  Done. DialBot SaaS v3.0 committed."
echo "============================================"
