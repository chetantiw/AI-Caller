"""
app/plan_features.py
Central plan feature definitions and enforcement.
Import check_feature(), check_campaign_limit(), check_seat_limit() anywhere.
"""
from __future__ import annotations

# ── Plan feature matrix ────────────────────────────────────────────────────────
PLAN_FEATURES: dict[str, dict] = {
    "starter": {
        # Limits
        "minutes_limit":        1000,
        "max_campaigns":        1,
        "max_team_seats":       1,
        # Voice
        "voice_options":        ["anushka", "abhilash"],
        "multi_language":       False,
        # Calling
        "smart_retry":          False,
        "call_transfer":        False,
        "end_call_tool":        True,
        # Leads & tagging
        "lead_tagging":         False,
        "dynamic_variables":    False,
        # Automation
        "whatsapp_triggers":    False,
        "sms_triggers":         False,
        "email_triggers":       True,   # email is free
        "auto_quotation":       False,
        "flow_builder":         False,
        # CRM
        "crm_webhook":          False,
        "crm_2way":             False,
        # Analytics
        "campaign_analytics":   False,
        "detailed_analytics":   False,
        "custom_reporting":     False,
        # Team
        "role_based_access":    False,
        "api_access":           False,
        # Billing
        "usage_alerts":         True,
        "billing_dashboard":    True,
        "addon_minutes":        True,
    },
    "growth": {
        "minutes_limit":        2500,
        "max_campaigns":        3,
        "max_team_seats":       3,
        "voice_options":        ["anushka", "abhilash", "kavya", "priya"],
        "multi_language":       False,
        "smart_retry":          False,
        "call_transfer":        True,
        "end_call_tool":        True,
        "lead_tagging":         True,
        "dynamic_variables":    True,
        "whatsapp_triggers":    True,
        "sms_triggers":         True,
        "email_triggers":       True,
        "auto_quotation":       False,
        "flow_builder":         False,
        "crm_webhook":          True,
        "crm_2way":             False,
        "campaign_analytics":   True,
        "detailed_analytics":   False,
        "custom_reporting":     False,
        "role_based_access":    False,
        "api_access":           False,
        "usage_alerts":         True,
        "billing_dashboard":    True,
        "addon_minutes":        True,
    },
    "pro": {
        "minutes_limit":        5000,
        "max_campaigns":        10,
        "max_team_seats":       10,
        "voice_options":        None,   # None = all voices
        "multi_language":       True,
        "smart_retry":          True,
        "call_transfer":        True,
        "end_call_tool":        True,
        "lead_tagging":         True,
        "dynamic_variables":    True,
        "whatsapp_triggers":    True,
        "sms_triggers":         True,
        "email_triggers":       True,
        "auto_quotation":       True,
        "flow_builder":         True,
        "crm_webhook":          True,
        "crm_2way":             False,
        "campaign_analytics":   True,
        "detailed_analytics":   True,
        "custom_reporting":     False,
        "role_based_access":    True,
        "api_access":           True,
        "usage_alerts":         True,
        "billing_dashboard":    True,
        "addon_minutes":        True,
    },
    "enterprise": {
        "minutes_limit":        0,      # 0 = unlimited (superadmin sets custom)
        "max_campaigns":        0,
        "max_team_seats":       0,
        "voice_options":        None,
        "multi_language":       True,
        "smart_retry":          True,
        "call_transfer":        True,
        "end_call_tool":        True,
        "lead_tagging":         True,
        "dynamic_variables":    True,
        "whatsapp_triggers":    True,
        "sms_triggers":         True,
        "email_triggers":       True,
        "auto_quotation":       True,
        "flow_builder":         True,
        "crm_webhook":          True,
        "crm_2way":             True,
        "campaign_analytics":   True,
        "detailed_analytics":   True,
        "custom_reporting":     True,
        "role_based_access":    True,
        "api_access":           True,
        "usage_alerts":         True,
        "billing_dashboard":    True,
        "addon_minutes":        True,
    },
}

_PLAN_ORDER = ["starter", "growth", "pro", "enterprise"]

# Human-readable labels for upgrade prompts
FEATURE_LABELS: dict[str, str] = {
    "smart_retry":        "Smart Retry",
    "call_transfer":      "Human Call Transfer",
    "lead_tagging":       "Lead Tagging",
    "dynamic_variables":  "Dynamic Script Variables",
    "whatsapp_triggers":  "WhatsApp Automation",
    "sms_triggers":       "SMS Automation",
    "auto_quotation":     "Auto PDF Quotation",
    "flow_builder":       "Flow Builder",
    "crm_webhook":        "CRM Integration",
    "crm_2way":           "CRM 2-Way Sync",
    "campaign_analytics": "Campaign Analytics",
    "detailed_analytics": "Detailed Analytics",
    "custom_reporting":   "Custom Reporting",
    "role_based_access":  "Role-Based Access",
    "api_access":         "API Access",
    "multi_language":     "Multi-Language Agent",
}


def _next_plan(plan: str) -> str | None:
    try:
        idx = _PLAN_ORDER.index(plan.lower())
        return _PLAN_ORDER[idx + 1] if idx + 1 < len(_PLAN_ORDER) else None
    except ValueError:
        return "growth"


def get_plan_features(plan: str) -> dict:
    """Return full feature dict for a plan. Defaults to starter."""
    return PLAN_FEATURES.get(plan.lower(), PLAN_FEATURES["starter"])


def check_feature(plan: str, feature: str) -> dict:
    """
    Check if a feature is allowed on this plan.
    Returns: {"allowed": bool, "reason": str, "upgrade_to": str|None}
    """
    features = get_plan_features(plan)
    value = features.get(feature, False)
    allowed = value is True or value is None  # None = unrestricted (e.g. voice_options)

    if allowed:
        return {"allowed": True, "reason": "", "upgrade_to": None}

    label = FEATURE_LABELS.get(feature, feature.replace("_", " ").title())
    upgrade_to = _next_plan(plan)
    plan_label = upgrade_to.title() if upgrade_to else "Enterprise"

    return {
        "allowed":    False,
        "reason":     f"'{label}' is not available on the {plan.title()} plan. Upgrade to {plan_label} to unlock.",
        "upgrade_to": upgrade_to,
        "feature":    feature,
    }


def check_campaign_limit(plan: str, current_active: int) -> dict:
    """Check if tenant can create another active campaign."""
    limit = get_plan_features(plan).get("max_campaigns", 1)
    if limit == 0:  # unlimited
        return {"allowed": True}
    if current_active >= limit:
        return {
            "allowed":    False,
            "reason":     f"Your {plan.title()} plan allows {limit} active campaign(s). Pause or complete existing ones, or upgrade your plan.",
            "upgrade_to": _next_plan(plan),
            "limit":      limit,
            "current":    current_active,
        }
    return {"allowed": True, "limit": limit, "current": current_active}


def check_seat_limit(plan: str, current_seats: int) -> dict:
    """Check if tenant can add another team member."""
    limit = get_plan_features(plan).get("max_team_seats", 1)
    if limit == 0:
        return {"allowed": True}
    if current_seats >= limit:
        return {
            "allowed":    False,
            "reason":     f"Your {plan.title()} plan allows {limit} team member(s). Upgrade to add more.",
            "upgrade_to": _next_plan(plan),
            "limit":      limit,
            "current":    current_seats,
        }
    return {"allowed": True, "limit": limit, "current": current_seats}


def check_minutes_quota(plan: str, minutes_used: float, minutes_limit: int) -> dict:
    """
    Check minutes usage against plan limit.
    Returns status: ok | warning_80 | warning_100 | exceeded
    """
    effective_limit = minutes_limit or get_plan_features(plan).get("minutes_limit", 1000)
    if effective_limit == 0:
        return {"status": "ok", "pct": 0, "used": minutes_used, "limit": 0}

    pct = (minutes_used / effective_limit * 100) if effective_limit else 0
    if pct >= 100:
        status = "exceeded"
    elif pct >= 80:
        status = "warning_80"
    else:
        status = "ok"

    return {
        "status":   status,
        "pct":      round(pct, 1),
        "used":     round(minutes_used, 1),
        "limit":    effective_limit,
        "remaining": max(0, round(effective_limit - minutes_used, 1)),
    }


def get_all_plan_comparison() -> list[dict]:
    """Return feature comparison across all plans — for the billing dashboard."""
    features_to_show = [
        ("minutes_limit",       "Included Minutes / Month"),
        ("max_campaigns",       "Active Campaigns"),
        ("max_team_seats",      "Team Seats"),
        ("call_transfer",       "Human Call Transfer"),
        ("lead_tagging",        "Lead Tagging"),
        ("whatsapp_triggers",   "WhatsApp Automation"),
        ("sms_triggers",        "SMS Automation"),
        ("auto_quotation",      "Auto PDF Quotation"),
        ("smart_retry",         "Smart Retry"),
        ("flow_builder",        "Flow Builder"),
        ("crm_webhook",         "CRM Integration"),
        ("crm_2way",            "CRM 2-Way Sync"),
        ("campaign_analytics",  "Campaign Analytics"),
        ("detailed_analytics",  "Detailed Analytics"),
        ("role_based_access",   "Role-Based Access"),
        ("api_access",          "API Access"),
    ]
    rows = []
    for key, label in features_to_show:
        row = {"feature": key, "label": label}
        for plan in _PLAN_ORDER:
            val = PLAN_FEATURES[plan].get(key, False)
            if isinstance(val, int) and key in ("minutes_limit", "max_campaigns", "max_team_seats"):
                row[plan] = "Unlimited" if val == 0 else f"{val:,}"
            elif val is True or val is None:
                row[plan] = "✓"
            else:
                row[plan] = "—"
        rows.append(row)
    return rows
