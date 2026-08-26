"""
Approved-leave dates for solver consumption.

Approved leave is a HARD unavailability for AI scheduling (both full
generate and derive-legal). Manual scheduling on leave days stays allowed
(warn-don't-block philosophy) — the hard stop only applies to the solver.
"""
from datetime import timedelta

from .models import LeaveRequest


def approved_leave_dates(employee_ids, period_start, period_end):
    """{employee_id: [iso-date, ...]} of approved leave days within the period."""
    result = {}
    leaves = LeaveRequest.objects.filter(
        employee_id__in=list(employee_ids),
        status='approved',
        end_date__gte=period_start,
        start_date__lte=period_end,
    ).values('employee_id', 'start_date', 'end_date')
    for leave in leaves:
        day = max(leave['start_date'], period_start)
        end = min(leave['end_date'], period_end)
        while day <= end:
            result.setdefault(leave['employee_id'], []).append(day.isoformat())
            day += timedelta(days=1)
    return result
