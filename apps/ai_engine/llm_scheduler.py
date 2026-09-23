"""
Pure-LLM scheduling (product decision 2026-09-23): the free model API
produces the assignments directly. Quality is guarded by a strict
validation layer — every row the model emits is checked against reality
(employee/shift exist in org, date in period, certifications, approved
leave, duplicates); invalid rows are dropped and reported, never written.
"""
from datetime import date as date_cls, timedelta

from apps.employees.models import Employee
from apps.leaves.solver_dates import approved_leave_dates
from apps.schedules.models import Schedule
from apps.shifts.models import ShiftTemplate

SYSTEM_PROMPT = """你是排班助手。根據提供的員工、班別與規則，產生一份排班表。

必須遵守：
1. 只能使用提供的 employee_id 與 shift_id，日期必須在期間內。
2. 每個班別每天至少排滿 min_staff 人。
3. 員工缺少班別要求的證照(required_cert_ids)時，不可排入該班別。
4. 不可排在員工的請假日(leave_dates)或每週不可排星期(blocked_weekdays, 0=週一)。
   也不可排在機構公休日(org.closed_weekdays, 0=週一)，公休日整天不排任何班。
5. 同一員工同一天不可排時間重疊的班別；每人每日總工時不超過 12 小時。
6. 盡量遵守：優先排 priority 名單靠前的員工；每人每週工時接近 weekly_hours；
   每人每 7 天至少休 1 天、連續工作不超過 6 天。

只輸出 JSON，格式：
{"assignments": [{"employee_id": 1, "date": "YYYY-MM-DD", "shift_id": 2}, ...]}
不要輸出任何其他文字。"""


def build_context(version, period_start, period_end):
    """Serialize org reality for the model + return lookups for validation."""
    employees = list(
        Employee.objects.filter(
            organization=version.organization, is_active=True,
        ).select_related('user').prefetch_related('certifications')
    )
    if version.branch_id:
        employees = [e for e in employees if e.branch_id == version.branch_id]
    shifts = list(
        ShiftTemplate.objects.filter(
            organization=version.organization, is_active=True,
        ).prefetch_related('required_certifications', 'employee_priorities')
    )
    emp_ids = [e.pk for e in employees]
    leave_map = approved_leave_dates(emp_ids, period_start, period_end)

    # 機構每週公休日（PM#1 排休）
    from apps.compliance.models import OrgComplianceSettings
    cfg = OrgComplianceSettings.objects.filter(
        organization=version.organization,
    ).first()
    closed_weekdays = sorted(set(cfg.weekly_closed_days or [])) if cfg else []

    blocked_weekdays = {}
    for emp in employees:
        try:
            slots = emp.availability.time_slots.filter(slot_type='blocked')
            days = sorted({s.day_of_week for s in slots if s.day_of_week is not None})
            if days:
                blocked_weekdays[emp.pk] = days
        except Exception:
            pass

    days = []
    day = period_start
    while day <= period_end:
        days.append(day)
        day += timedelta(days=1)

    payload = {
        'period': {'start': period_start.isoformat(), 'end': period_end.isoformat(),
                   'dates': [d.isoformat() for d in days]},
        'org': {'closed_weekdays': closed_weekdays},
        'employees': [
            {
                'employee_id': e.pk,
                'name': (e.user.get_full_name() or e.user.username) if e.user else e.employee_id,
                'cert_ids': [c.pk for c in e.certifications.all()],
                'weekly_hours': float(e.agreed_hours_per_week),
                'leave_dates': leave_map.get(e.pk, []),
                'blocked_weekdays': blocked_weekdays.get(e.pk, []),
            }
            for e in employees
        ],
        'shifts': [
            {
                'shift_id': s.pk,
                'name': s.name,
                'start': s.start_time.strftime('%H:%M'),
                'end': s.end_time.strftime('%H:%M'),
                'hours': float(s.duration_hours),
                'min_staff': s.min_staff_count,
                'required_cert_ids': [c.pk for c in s.required_certifications.all()],
                'priority_employee_ids': [
                    p.employee_id for p in sorted(
                        s.employee_priorities.all(), key=lambda p: p.priority_rank)
                ],
            }
            for s in shifts
        ],
    }
    lookups = {
        'employees': {e.pk: e for e in employees},
        'shifts': {s.pk: s for s in shifts},
        'leave_map': leave_map,
        'blocked_weekdays': blocked_weekdays,
        'dates': {d.isoformat() for d in days},
        'closed_weekdays': set(closed_weekdays),
    }
    return payload, lookups


def validate_assignments(raw, lookups, version):
    """Filter the model output down to writable rows.

    Returns (valid_rows, rejected list of {row, reason}).
    """
    existing = {
        (s.employee_id, s.schedule_date.isoformat(), s.shift_template_id)
        for s in Schedule.objects.filter(schedule_version=version)
    }
    valid, rejected, seen = [], [], set()
    if not isinstance(raw, list):
        return [], [{'row': raw, 'reason': 'assignments 不是清單'}]

    for row in raw:
        if not isinstance(row, dict):
            rejected.append({'row': row, 'reason': '格式錯誤'})
            continue
        emp_id, date_str, shift_id = row.get('employee_id'), row.get('date'), row.get('shift_id')
        employee = lookups['employees'].get(emp_id)
        shift = lookups['shifts'].get(shift_id)
        if employee is None:
            rejected.append({'row': row, 'reason': f'員工 {emp_id} 不存在或不在此機構'})
            continue
        if shift is None:
            rejected.append({'row': row, 'reason': f'班別 {shift_id} 不存在或未啟用'})
            continue
        if date_str not in lookups['dates']:
            rejected.append({'row': row, 'reason': f'日期 {date_str} 不在排班期間內'})
            continue
        required = {c.pk for c in shift.required_certifications.all()}
        held = {c.pk for c in employee.certifications.all()}
        if required and not required <= held:
            rejected.append({'row': row, 'reason': '缺少班別要求的證照'})
            continue
        if date_str in lookups['leave_map'].get(emp_id, []):
            rejected.append({'row': row, 'reason': '該日已核准請假'})
            continue
        if date_cls.fromisoformat(date_str).weekday() in lookups.get('closed_weekdays', set()):
            rejected.append({'row': row, 'reason': '機構公休日'})
            continue
        key = (emp_id, date_str, shift_id)
        if key in seen or key in existing:
            rejected.append({'row': row, 'reason': '重複班次（已存在或模型重複輸出）'})
            continue
        seen.add(key)
        valid.append({'employee': employee, 'shift': shift, 'date': date_str})
    return valid, rejected


def coverage_warnings(valid_rows, lookups):
    """min_staff 缺口回報（不阻擋——警告哲學）。"""
    count = {}
    for row in valid_rows:
        count[(row['date'], row['shift'].pk)] = count.get((row['date'], row['shift'].pk), 0) + 1
    warnings = []
    closed = lookups.get('closed_weekdays', set())
    for date_str in sorted(lookups['dates']):
        if date_cls.fromisoformat(date_str).weekday() in closed:
            continue  # 公休日本來就不該有人，不算缺口
        for shift in lookups['shifts'].values():
            got = count.get((date_str, shift.pk), 0)
            if got < shift.min_staff_count:
                warnings.append(
                    f'{date_str} {shift.name} 只排到 {got}/{shift.min_staff_count} 人')
    return warnings
