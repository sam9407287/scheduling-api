"""
Org-level ShiftRule resolution helpers for solver constraint assembly.
"""
from .models import ShiftRule


def resolve_max_daily_hours(organization_id):
    """Return the org's active max_daily_hours rule value, or None.

    Accepts the value-JSON shapes the frontend may store:
    {"max_hours": n} / {"hours": n} / {"value": n} / bare number.
    """
    rule = ShiftRule.objects.filter(
        organization_id=organization_id,
        rule_type='max_daily_hours',
        is_active=True,
    ).order_by('-updated_at').first()
    if rule is None:
        return None

    value = rule.value
    if isinstance(value, dict):
        for key in ('max_hours', 'hours', 'value'):
            if key in value:
                value = value[key]
                break
        else:
            return None
    try:
        hours = float(value)
    except (TypeError, ValueError):
        return None
    return hours if hours > 0 else None
