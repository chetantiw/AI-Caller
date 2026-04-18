"""
app/plan_features.py
Central plan feature definitions and gate checker.
Import check_feature() anywhere to enforce plan limits.
"""

PLAN_FEATURES = {
    "starter": {
        "minutes_limit":       1000,
        "max_campaigns":       1,
        "max_team_seats":      1,
        "voice_options":       ["anushka", "abhilash"],     # 2 voices only
        "dynamic_variables":   False,
        "lead_tagging":        False,
        "crm_webhook":         False,
        "crm_2way":            False,
        "smart_retry":         False,
        "flow_builder":        False,
        "multi_language":      False,
        "role_based_access":   False,
        "campaign_analytics":  False,
        "detailed_analytics":  False,
        "custom_reporting":    False,
        "api_access":          False,
        "usage_alerts":        True,
        "addon_minutes":       True,
        "billing_dashboard":   True,
    },
    "growth": {
        "minutes_limit":       2500,
        "max_campaigns":       3,
        "max_team_seats":      3,
        "voice_options":       ["anushka", "abhilash", "priya", "kavya"],
        "dynamic_variables":   True,
        "lead_tagging":        True,
        "crm_webhook":         True,
        "crm_2way":            False,
        "smart_retry":         False,
        "flow_builder":        False,
        "multi_language":      False,
        "role_based_access":   False,
        "campaign_analytics":  True,
        "detailed_analytics":  False,
        "custom_reporting":    False,
        "api_access":          False,
        "usage_alerts":        True,
        "addon_minutes":       True,
        "billing_dashboard":   True,
    },
    "pro": {
        "minutes_limit":       5000,
        "max_campaigns":       10,
        "max_team_seats":      10,
        "voice_options":       None,       # None = all voices
        "dynamic_variables":   True,
        "lead_tagging":        True,
        "crm_webhook":         True,
        "crm_2way":            False,      # 1-way only on pro
        "smart_retry":         True,
        "flow_builder":        True,
        "multi_language":      True,
        "role_based_access":   True,
        "campaign_analytics":  True,
        "detailed_analytics":  True,
        "custom_reporting":    False,
        "api_access":          True,
        "usage_alerts":        True,
        "addon_minutes":       True,
        "billing_dashboard":   True,
    },
    "enterprise": {
        "minutes_limit":       0,          # 0 = unlimited (superadmin sets custom)
        "max_campaigns":       0,
        "max_team_seats":      0,
        "voice_options":       None,
        "dynamic_variables":   True,
        "lead_tagging":        True,
        "crm_webhook":         True,
        "crm_2way":            True,
        "smart_retry":         True,
        "flow_builder":        True,
        "multi_language":      True,
        "role_based_access":   True,
        "campaign_analytics":  True,
        "detailed_analytics":  True,
        "custom_reporting":    True,
        "api_access":          True,
        "usage_alerts":        True,
        "addon_minutes":       True,
        "billing_dashboard":   True,
    },
}


def get_plan_features(plan: str) -> dict:
    return PLAN_FEATURES.get(plan, PLAN_FEATURES["starter"])


def check_feature(plan: str, feature: str) -> dict:
    """
    Returns {"allowed": bool, "reason": str, "upgrade_to": str}
    Use this at every feature gate in api_routes.py.
    """
    features = get_plan_features(plan)
    allowed  = features.get(feature, False)

    if allowed or allowed is None:
        return {"allowed": True, "reason": "", "upgrade_to": None}

    # Determine which plan unlocks this feature
    upgrade_to = None
    for p in ["starter", "growth", "pro", "enterprise"]:
        if PLAN_FEATURES[p].get(feature):
            upgrade_to = p
            break

    return {
        "allowed":    False,
        "reason":     f"'{feature}' is not available on the {plan} plan.",
        "upgrade_to": upgrade_to,
    }


def check_campaign_limit(plan: str, current_active: int) -> dict:
    limit = get_plan_features(plan).get("max_campaigns", 1)
    if limit == 0:
        return {"allowed": True}
    if current_active >= limit:
        return {
            "allowed":    False,
            "reason":     f"Your {plan} plan allows {limit} active campaign(s). Pause or complete existing ones, or upgrade.",
            "upgrade_to": _next_plan(plan),
        }
    return {"allowed": True}


def check_seat_limit(plan: str, current_seats: int) -> dict:
    limit = get_plan_features(plan).get("max_team_seats", 1)
    if limit == 0:
        return {"allowed": True}
    if current_seats >= limit:
        return {
            "allowed":    False,
            "reason":     f"Your {plan} plan allows {limit} team member(s). Upgrade to add more.",
            "upgrade_to": _next_plan(plan),
        }
    return {"allowed": True}


def _next_plan(plan: str) -> str:
    order = ["starter", "growth", "pro", "enterprise"]
    idx = order.index(plan) if plan in order else 0
    return order[min(idx + 1, len(order) - 1)]
