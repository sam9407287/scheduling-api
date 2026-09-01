"""
Org-level leave quota resolution.

Reference defaults follow Taiwan's 勞工請假規則 where an annual cap exists;
event-based leaves (marriage/bereavement/maternity/…) have no annual cap.
An org's LeaveTypeQuota row overrides the default for that type. Annual
leave (特休) never uses quotas — it is always statutory via annual.py.
Overdraft is never blocked, only surfaced (negative remaining).
"""
DAY = 'day'  # marker: values below are in DAYS, converted via day_minutes

# leave_type -> default annual cap in days (None = unlimited / event-based)
DEFAULT_QUOTA_DAYS = {
    'sick': 30,        # 普通傷病假
    'personal': 14,    # 事假
    'menstrual': 12,   # 生理假（1/月）
    'marriage': None,
    'bereavement': None,
    'maternity': None,
    'paternity': None,
    'official': None,
    'other': None,
}


def resolve_quota_minutes(organization_id, day_minutes):
    """{leave_type: quota_minutes or None} for every non-annual type."""
    from .models import LeaveTypeQuota
    result = {
        lt: (days * day_minutes if days is not None else None)
        for lt, days in DEFAULT_QUOTA_DAYS.items()
    }
    for row in LeaveTypeQuota.objects.filter(organization_id=organization_id):
        if not row.is_active:
            result[row.leave_type] = None
        else:
            result[row.leave_type] = row.annual_quota_minutes
    return result


def day_minutes_for(organization_id):
    from .models import OrgLeaveSettings
    settings_row = OrgLeaveSettings.objects.filter(
        organization_id=organization_id
    ).first()
    return settings_row.day_minutes if settings_row else 480
