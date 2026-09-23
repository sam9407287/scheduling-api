"""
Labor Law Compliance Engine

Two public entry points:
  * `check_schedule_violations(version, rules)` — pure function, no DB writes,
    returns `List[Violation]` keyed per-cell so the frontend grid can highlight
    individual squares. Used by `POST /schedules/versions/{id}/check_compliance/`.
  * `ComplianceEngine.check_schedule_compliance(version, rules)` — legacy path
    that wraps the pure check and persists a `ComplianceCheck` row. Each stored
    violation dict carries both the new keys (`rule`, `schedule_date`,
    `shift_template_id`, `severity`, `related_dates`, `detail`) and legacy keys
    (`type`, `rest_hours`, …) so older callers and tests keep working.
"""
from dataclasses import dataclass, field, asdict
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import List, Dict, Any, Optional, Iterable
from django.db.models import Sum, Q
from apps.employees.models import Employee
from apps.schedules.models import Schedule, ScheduleVersion
from apps.attendance.models import Attendance
from .models import LaborLawRule, ComplianceCheck


# Mapping between machine rule code and the legacy `type` value persisted in
# ComplianceCheck.violations. Keeps backward compatibility for old API consumers
# and the test_bugfixes assertions.
LEGACY_TYPE_BY_RULE = {
    'max_weekly_hours':     'weekly_hours_violation',
    'max_consecutive_days': 'consecutive_days_violation',
    'min_rest_hours':       'rest_interval_violation',
    'max_daily_hours':      'daily_hours_violation',
}

RULE_LABELS_ZH = {
    'max_weekly_hours':     '週工時超標',
    'max_consecutive_days': '連續工作天數超標',
    'min_rest_hours':       '兩班間隔不足',
    'max_daily_hours':      '單日工時超標',
    # 勞基法補全（2026-09-18，依 §32/§35/§36/§37）
    'max_daily_total_hours':      '單日含加班總工時超過12小時',
    'max_monthly_overtime_hours': '單月加班時數超標',
    'weekly_rest_days':           '每七日未足二日休息',
    'min_break_minutes':          '連續工作四小時未安排休息',
    'holiday_scheduling':         '國定假日出勤（工資應加倍）',
    'org_closed_day':             '機構公休日排班',
}


@dataclass
class Violation:
    """
    A single rule violation pinned to one schedule cell (the *trigger cell*).

    Cross-cell rules (weekly hours, consecutive days, rest interval) still
    select one cell as the trigger — typically the last cell in the offending
    window — and list the rest of the window in `related_dates` so the
    frontend can choose to highlight only the trigger or the entire span.
    """
    rule: str                       # machine code, e.g. "min_rest_hours"
    severity: str                   # "hard" | "soft"
    employee_pk: int                # Employee primary key
    employee_code: str              # Employee.employee_id (human-readable)
    employee_name: str
    schedule_date: str              # ISO date of the trigger cell
    shift_template_id: Optional[int] = None
    related_dates: List[str] = field(default_factory=list)
    detail: Dict[str, Any] = field(default_factory=dict)

    @property
    def rule_label(self) -> str:
        return RULE_LABELS_ZH.get(self.rule, self.rule)

    def to_dict(self) -> Dict[str, Any]:
        """Forward-facing per-cell representation (for the API response)."""
        return {
            'rule': self.rule,
            'rule_label': self.rule_label,
            'severity': self.severity,
            'employee_pk': self.employee_pk,
            'employee_code': self.employee_code,
            'employee_name': self.employee_name,
            'schedule_date': self.schedule_date,
            'shift_template_id': self.shift_template_id,
            'related_dates': list(self.related_dates),
            'detail': dict(self.detail),
        }

    def to_storage_dict(self) -> Dict[str, Any]:
        """
        Combined representation for `ComplianceCheck.violations` JSON storage:
        new per-cell keys plus the legacy keys older API consumers and tests
        rely on. The legacy keys are derived from `detail` per rule type.
        """
        d = self.to_dict()
        legacy_type = LEGACY_TYPE_BY_RULE.get(self.rule, self.rule)
        d['type'] = legacy_type
        d['employee_id'] = self.employee_code  # legacy meaning: code, not PK
        d['message'] = _legacy_message(self)
        if self.rule == 'min_rest_hours':
            d['rest_hours'] = self.detail.get('rest_hours')
            d['min_rest_hours'] = self.detail.get('required_hours')
            d['date1'] = (self.related_dates[0] if self.related_dates
                          else self.schedule_date)
            d['date2'] = self.schedule_date
        elif self.rule == 'max_weekly_hours':
            d['week_start'] = self.detail.get('week_start')
            d['total_hours'] = self.detail.get('total_hours')
            d['max_hours'] = self.detail.get('max_hours')
        elif self.rule == 'max_consecutive_days':
            d['start_date'] = (self.related_dates[0] if self.related_dates
                               else self.schedule_date)
            d['consecutive_days'] = self.detail.get('consecutive_days')
            d['max_days'] = self.detail.get('max_days')
        elif self.rule == 'max_daily_hours':
            d['date'] = self.schedule_date
            d['hours'] = self.detail.get('total_hours')
            d['max_hours'] = self.detail.get('max_hours')
        return d


def _legacy_message(v: Violation) -> str:
    d = v.detail
    if v.rule == 'min_rest_hours':
        return (f'兩班之間休息時間 {d.get("rest_hours")} 小時，'
                f'低於限制 {d.get("required_hours")} 小時')
    if v.rule == 'max_weekly_hours':
        return (f'員工 {v.employee_code} 在 {d.get("week_start")} 當週工時 '
                f'{d.get("total_hours")} 小時，超過限制 {d.get("max_hours")} 小時')
    if v.rule == 'max_consecutive_days':
        return (f'員工 {v.employee_code} 連續工作 {d.get("consecutive_days")} 天，'
                f'超過限制 {d.get("max_days")} 天')
    if v.rule == 'max_daily_hours':
        return (f'員工 {v.employee_code} 於 {v.schedule_date} 工時 '
                f'{d.get("total_hours")} 小時，超過單日限制 {d.get("max_hours")} 小時')
    return ''


DEFAULT_RULES = {
    'max_weekly_hours': 40,
    'max_daily_hours': 8,
    'min_rest_hours': 11,
    'max_consecutive_days': 6,
    'mandatory_rest_day': 1,
    # 勞基法補全（§32/§35/§36）
    'max_daily_total_hours': 12,        # 正常+延長 一日上限（§32-2）
    'max_monthly_overtime_hours': 46,   # 延長工時每月上限（§32-2）
    'weekly_rest_days': 2,              # 每七日應有二日休息（§36）
    'min_break_minutes': 30,            # 連續工作4h應有30分休息（§35）
}


def check_schedule_violations(
    schedule_version: ScheduleVersion,
    rules: Optional[Dict[str, Any]] = None,
    soft_rule_types: Optional[Iterable[str]] = None,
) -> List[Violation]:
    """
    Pure function: scan one ScheduleVersion and return per-cell violations.

    Does not write to the database; safe to call on every keystroke from the
    "一鍵檢查" button without polluting audit history. Use
    `ComplianceEngine.check_schedule_compliance` when persistence is needed.

    `soft_rule_types` (PR11) labels matching violations with
    `severity='soft'` so the frontend can render them as yellow reminders
    rather than red blockers. Every violation is still returned regardless
    of severity — the customer wants all labour-law hits shown.
    """
    if rules is None:
        rules = DEFAULT_RULES.copy()
    soft = set(soft_rule_types or ())

    schedules = list(
        Schedule.objects
        .filter(schedule_version=schedule_version)
        # 請假/取消的格子不是實際工時，不參與勞基法檢查
        .exclude(status__in=('cancelled', 'leave'))
        .select_related('employee', 'employee__user', 'shift_template')
    )

    violations: List[Violation] = []
    by_employee: Dict[int, List[Schedule]] = {}
    for s in schedules:
        by_employee.setdefault(s.employee_id, []).append(s)

    for emp_pk, emp_schedules in by_employee.items():
        employee = emp_schedules[0].employee
        emp_schedules.sort(key=lambda s: (s.schedule_date, s.shift_template.start_time))

        violations.extend(_check_weekly_hours(
            employee, emp_schedules, rules.get('max_weekly_hours', 40)
        ))
        violations.extend(_check_consecutive_days(
            employee, emp_schedules, rules.get('max_consecutive_days', 6)
        ))
        violations.extend(_check_rest_interval(
            employee, emp_schedules, rules.get('min_rest_hours', 11)
        ))
        violations.extend(_check_daily_hours(
            employee, emp_schedules, rules.get('max_daily_hours', 8)
        ))
        violations.extend(_check_daily_total_hours(
            employee, emp_schedules, rules.get('max_daily_total_hours', 12)
        ))
        violations.extend(_check_monthly_overtime(
            employee, emp_schedules,
            rules.get('max_monthly_overtime_hours', 46),
            normal_daily=float(rules.get('max_daily_hours', 8)),
        ))
        violations.extend(_check_weekly_rest_days(
            employee, emp_schedules,
            int(rules.get('weekly_rest_days', 2)),
            schedule_version.period_start, schedule_version.period_end,
        ))
        violations.extend(_check_break_minutes(
            employee, emp_schedules, int(rules.get('min_break_minutes', 30))
        ))

    # 國定假日出勤（§37/§39）：跨員工一次查表
    violations.extend(_check_holidays(schedules, schedule_version))

    # 機構公休日（PM#1 排休）：手動排班可排但以 soft 提醒
    violations.extend(_check_org_closed_days(schedules, schedule_version))

    # Label severity in one pass: rules the org marked soft become 'soft',
    # everything else stays 'hard'. All violations are returned either way.
    if soft:
        for v in violations:
            if v.rule in soft:
                v.severity = 'soft'

    return violations


def summarize_by_rule(violations: Iterable[Violation]) -> Dict[str, int]:
    counts: Dict[str, int] = {}
    for v in violations:
        counts[v.rule] = counts.get(v.rule, 0) + 1
    return counts


def _employee_name(employee: Employee) -> str:
    if employee.user_id and employee.user:
        return employee.user.get_full_name() or employee.user.username
    return employee.employee_id


def _check_weekly_hours(
    employee: Employee,
    schedules: List[Schedule],
    max_hours: float,
) -> List[Violation]:
    weekly: Dict[date, List[Schedule]] = {}
    for s in schedules:
        wk = s.schedule_date - timedelta(days=s.schedule_date.weekday())
        weekly.setdefault(wk, []).append(s)

    out: List[Violation] = []
    for week_start, week_schedules in weekly.items():
        total = sum(float(s.expected_hours) for s in week_schedules)
        if total <= max_hours:
            continue
        # Trigger cell = the last (chronologically) schedule of the week.
        # Frontend can light up either the trigger or every related_date.
        week_schedules.sort(key=lambda s: (s.schedule_date, s.shift_template.start_time))
        trigger = week_schedules[-1]
        related = [s.schedule_date.isoformat() for s in week_schedules[:-1]]
        out.append(Violation(
            rule='max_weekly_hours',
            severity='hard',
            employee_pk=employee.pk,
            employee_code=employee.employee_id,
            employee_name=_employee_name(employee),
            schedule_date=trigger.schedule_date.isoformat(),
            shift_template_id=trigger.shift_template_id,
            related_dates=related,
            detail={
                'week_start': week_start.isoformat(),
                'total_hours': total,
                'max_hours': max_hours,
            },
        ))
    return out


def _check_consecutive_days(
    employee: Employee,
    schedules: List[Schedule],
    max_days: int,
) -> List[Violation]:
    if not schedules:
        return []
    # Dedupe to one date per workday (multiple shifts on the same day still
    # count as a single consecutive day).
    dates = sorted({s.schedule_date for s in schedules})
    by_date = {s.schedule_date: s for s in schedules}

    out: List[Violation] = []
    streak_start_idx = 0
    for i in range(1, len(dates) + 1):
        broken = i == len(dates) or (dates[i] - dates[i - 1]).days != 1
        if not broken:
            continue
        length = i - streak_start_idx
        if length > max_days:
            streak_dates = dates[streak_start_idx:i]
            # Trigger cell = first day past the limit (`max_days`-th onward
            # is illegal; choosing the (max_days+1)-th makes the highlight
            # land exactly on the first illegal cell).
            trigger_date = streak_dates[max_days]
            trigger = by_date[trigger_date]
            related = [d.isoformat() for d in streak_dates if d != trigger_date]
            out.append(Violation(
                rule='max_consecutive_days',
                severity='hard',
                employee_pk=employee.pk,
                employee_code=employee.employee_id,
                employee_name=_employee_name(employee),
                schedule_date=trigger_date.isoformat(),
                shift_template_id=trigger.shift_template_id,
                related_dates=related,
                detail={
                    'consecutive_days': length,
                    'max_days': max_days,
                },
            ))
        streak_start_idx = i
    return out


def _check_rest_interval(
    employee: Employee,
    schedules: List[Schedule],
    min_rest_hours: float,
) -> List[Violation]:
    if len(schedules) < 2:
        return []
    # `schedules` is already sorted by (date, start_time) at the caller.
    out: List[Violation] = []
    for i in range(len(schedules) - 1):
        current = schedules[i]
        nxt = schedules[i + 1]
        current_end_dt = datetime.combine(
            current.schedule_date, current.shift_template.end_time
        )
        if current.shift_template.end_time < current.shift_template.start_time:
            current_end_dt += timedelta(days=1)
        next_start_dt = datetime.combine(
            nxt.schedule_date, nxt.shift_template.start_time
        )
        rest_hours = (next_start_dt - current_end_dt).total_seconds() / 3600
        if rest_hours >= min_rest_hours:
            continue
        # Trigger cell = the next shift (the one that started too early).
        out.append(Violation(
            rule='min_rest_hours',
            severity='hard',
            employee_pk=employee.pk,
            employee_code=employee.employee_id,
            employee_name=_employee_name(employee),
            schedule_date=nxt.schedule_date.isoformat(),
            shift_template_id=nxt.shift_template_id,
            related_dates=[current.schedule_date.isoformat()],
            detail={
                'rest_hours': round(rest_hours, 2),
                'required_hours': min_rest_hours,
                'previous_shift_id': current.shift_template_id,
            },
        ))
    return out


def _check_daily_hours(
    employee: Employee,
    schedules: List[Schedule],
    max_hours: float,
) -> List[Violation]:
    daily: Dict[date, List[Schedule]] = {}
    for s in schedules:
        daily.setdefault(s.schedule_date, []).append(s)
    out: List[Violation] = []
    for d, day_schedules in daily.items():
        total = sum(float(s.expected_hours) for s in day_schedules)
        if total <= max_hours:
            continue
        day_schedules.sort(key=lambda s: s.shift_template.start_time)
        trigger = day_schedules[-1]
        related = [s.schedule_date.isoformat() for s in day_schedules[:-1]]
        out.append(Violation(
            rule='max_daily_hours',
            severity='hard',
            employee_pk=employee.pk,
            employee_code=employee.employee_id,
            employee_name=_employee_name(employee),
            schedule_date=d.isoformat(),
            shift_template_id=trigger.shift_template_id,
            related_dates=related,
            detail={
                'total_hours': total,
                'max_hours': max_hours,
            },
        ))
    return out


class ComplianceEngine:
    """勞基法合規檢查引擎（向後相容入口，會持久化 ComplianceCheck）"""

    # Exposed for callers that historically read this dict.
    DEFAULT_RULES = DEFAULT_RULES

    def check_schedule_compliance(
        self,
        schedule_version: ScheduleVersion,
        rules: Dict[str, Any] = None,
    ) -> ComplianceCheck:
        """檢查排班表合規性並寫入 ComplianceCheck 紀錄。"""
        if rules is None:
            rules = self.DEFAULT_RULES.copy()

        vs = check_schedule_violations(schedule_version, rules)
        violations_payload = [v.to_storage_dict() for v in vs]
        warnings: List[Dict[str, Any]] = []

        status = 'pass' if not vs else 'violation'
        return ComplianceCheck.objects.create(
            organization=schedule_version.organization,
            check_type='schedule',
            check_period_start=schedule_version.period_start,
            check_period_end=schedule_version.period_end,
            status=status,
            violations=violations_payload,
            warnings=warnings,
        )

    # ------------------------------------------------------------------
    # Legacy shims — older tests call these private methods directly with
    # ad-hoc Schedule lists. They return the legacy dict shape so existing
    # `v['rest_hours']` / `v['type']` assertions keep working.
    # ------------------------------------------------------------------
    def _check_rest_interval(
        self,
        schedules: List[Schedule],
        min_rest_hours: float,
    ) -> List[Dict[str, Any]]:
        if not schedules:
            return []
        employee = schedules[0].employee
        ordered = sorted(
            schedules,
            key=lambda s: (s.schedule_date, s.shift_template.start_time),
        )
        return [v.to_storage_dict()
                for v in _check_rest_interval(employee, ordered, min_rest_hours)]

    def _check_weekly_hours(
        self,
        schedules: List[Schedule],
        employee: Employee,
        max_hours: float,
    ) -> List[Dict[str, Any]]:
        return [v.to_storage_dict()
                for v in _check_weekly_hours(employee, list(schedules), max_hours)]

    def _check_consecutive_days(
        self,
        schedules: List[Schedule],
        max_days: int,
    ) -> List[Dict[str, Any]]:
        if not schedules:
            return []
        employee = schedules[0].employee
        return [v.to_storage_dict()
                for v in _check_consecutive_days(employee, list(schedules), max_days)]

    def check_attendance_compliance(
        self,
        organization_id: int,
        period_start: date,
        period_end: date,
    ) -> ComplianceCheck:
        """檢查出勤合規性（暫保留原邏輯，第二階段再升級為逐格）"""
        violations: List[Dict[str, Any]] = []
        warnings: List[Dict[str, Any]] = []

        attendances = Attendance.objects.filter(
            employee__organization_id=organization_id,
            work_date__gte=period_start,
            work_date__lte=period_end,
        )

        anomalies = attendances.filter(anomaly_flag=True)
        if anomalies.exists():
            violations.append({
                'type': 'attendance_anomaly',
                'count': anomalies.count(),
                'message': f'發現 {anomalies.count()} 筆異常出勤記錄',
            })

        for attendance in attendances:
            if attendance.actual_hours and attendance.actual_hours > Decimal('12'):
                violations.append({
                    'type': 'overtime_violation',
                    'employee_id': attendance.employee.employee_id,
                    'date': attendance.work_date.isoformat(),
                    'hours': float(attendance.actual_hours),
                    'message': (f'員工 {attendance.employee.employee_id} 於 '
                                f'{attendance.work_date} 工作超過 12 小時'),
                })

        status = 'pass' if not violations else 'violation'
        return ComplianceCheck.objects.create(
            organization_id=organization_id,
            check_type='attendance',
            check_period_start=period_start,
            check_period_end=period_end,
            status=status,
            violations=violations,
            warnings=warnings,
        )


# ===========================================================================
# 勞基法補全（2026-09-18）：§32 / §35 / §36 / §37
# 硬性程度設計：§32 兩條為 hard（絕對上限）；§35/§36 第二休息日/§37 假日
# 出勤在法律上有徵得同意/加倍給付的空間，預設 soft 提醒不阻擋。
# ===========================================================================

def _check_daily_total_hours(
    employee: Employee,
    schedules: List[Schedule],
    max_hours: float,
) -> List[Violation]:
    """§32：正常＋延長工時一日不得超過 12 小時（絕對上限，hard）。"""
    by_day: Dict[date, List[Schedule]] = {}
    for s in schedules:
        by_day.setdefault(s.schedule_date, []).append(s)

    out: List[Violation] = []
    for day, day_schedules in by_day.items():
        total = sum(float(s.expected_hours) for s in day_schedules)
        if total <= max_hours:
            continue
        day_schedules.sort(key=lambda s: s.shift_template.start_time)
        trigger = day_schedules[-1]
        out.append(Violation(
            rule='max_daily_total_hours',
            severity='hard',
            employee_pk=employee.pk,
            employee_code=employee.employee_id,
            employee_name=_employee_name(employee),
            schedule_date=day.isoformat(),
            shift_template_id=trigger.shift_template_id,
            related_dates=[],
            detail={'total_hours': round(total, 2), 'max_hours': max_hours},
        ))
    return out


def _check_monthly_overtime(
    employee: Employee,
    schedules: List[Schedule],
    max_overtime_hours: float,
    normal_daily: float,
) -> List[Violation]:
    """§32：延長工時（單日超過正常工時的部分）每月不得超過 46 小時。

    以「單日總工時 − 正常工時上限」的正數部分累計為當月加班時數。
    """
    by_day: Dict[date, float] = {}
    last_cell_of_day: Dict[date, Schedule] = {}
    for s in schedules:
        by_day[s.schedule_date] = by_day.get(s.schedule_date, 0.0) + float(s.expected_hours)
        prev = last_cell_of_day.get(s.schedule_date)
        if prev is None or s.shift_template.start_time > prev.shift_template.start_time:
            last_cell_of_day[s.schedule_date] = s

    monthly: Dict[str, float] = {}
    ot_days: Dict[str, List[date]] = {}
    for day, total in by_day.items():
        overtime = max(0.0, total - normal_daily)
        if overtime <= 0:
            continue
        key = day.strftime('%Y-%m')
        monthly[key] = monthly.get(key, 0.0) + overtime
        ot_days.setdefault(key, []).append(day)

    out: List[Violation] = []
    for month, overtime_total in monthly.items():
        if overtime_total <= max_overtime_hours:
            continue
        days = sorted(ot_days[month])
        trigger = last_cell_of_day[days[-1]]
        out.append(Violation(
            rule='max_monthly_overtime_hours',
            severity='hard',
            employee_pk=employee.pk,
            employee_code=employee.employee_id,
            employee_name=_employee_name(employee),
            schedule_date=days[-1].isoformat(),
            shift_template_id=trigger.shift_template_id,
            related_dates=[d.isoformat() for d in days[:-1]],
            detail={
                'month': month,
                'overtime_hours': round(overtime_total, 2),
                'max_overtime_hours': max_overtime_hours,
            },
        ))
    return out


def _check_weekly_rest_days(
    employee: Employee,
    schedules: List[Schedule],
    required_rest_days: int,
    period_start: date,
    period_end: date,
) -> List[Violation]:
    """§36：勞工每七日中應有二日之休息（一例一休）。

    只檢查「版本期間完整涵蓋」的 ISO 週（週一~週日），避免期間頭尾的
    不完整週產生假警報。soft：休息日出勤法律允許（需加班費），僅提醒。
    """
    if not schedules:
        return []
    worked_days_by_week: Dict[date, set] = {}
    cells_by_week: Dict[date, List[Schedule]] = {}
    for s in schedules:
        wk = s.schedule_date - timedelta(days=s.schedule_date.weekday())
        worked_days_by_week.setdefault(wk, set()).add(s.schedule_date)
        cells_by_week.setdefault(wk, []).append(s)

    out: List[Violation] = []
    for week_start, worked_days in worked_days_by_week.items():
        week_end = week_start + timedelta(days=6)
        if week_start < period_start or week_end > period_end:
            continue  # 不完整週不判
        rest_days = 7 - len(worked_days)
        if rest_days >= required_rest_days:
            continue
        cells = sorted(
            cells_by_week[week_start],
            key=lambda s: (s.schedule_date, s.shift_template.start_time),
        )
        trigger = cells[-1]
        out.append(Violation(
            rule='weekly_rest_days',
            severity='soft',
            employee_pk=employee.pk,
            employee_code=employee.employee_id,
            employee_name=_employee_name(employee),
            schedule_date=trigger.schedule_date.isoformat(),
            shift_template_id=trigger.shift_template_id,
            related_dates=[
                s.schedule_date.isoformat() for s in cells[:-1]
            ],
            detail={
                'week_start': week_start.isoformat(),
                'rest_days': rest_days,
                'required_rest_days': required_rest_days,
            },
        ))
    return out


def _check_break_minutes(
    employee: Employee,
    schedules: List[Schedule],
    required_break_minutes: int,
) -> List[Violation]:
    """§35：繼續工作四小時至少應有三十分鐘休息。

    以班別模板設定判斷：時段跨度 > 4 小時且 break_minutes 不足者提醒。
    輪班/連續性工作法律允許另行調配，預設 soft。
    """
    out: List[Violation] = []
    seen_cells = set()
    for s in schedules:
        template = s.shift_template
        span_start = template.start_time
        span_end = template.end_time
        span_minutes = (span_end.hour * 60 + span_end.minute) - (span_start.hour * 60 + span_start.minute)
        if span_minutes <= 0:
            span_minutes += 24 * 60  # 跨午夜
        if span_minutes <= 4 * 60:
            continue
        if (template.break_minutes or 0) >= required_break_minutes:
            continue
        key = (s.schedule_date, template.pk)
        if key in seen_cells:
            continue
        seen_cells.add(key)
        out.append(Violation(
            rule='min_break_minutes',
            severity='soft',
            employee_pk=employee.pk,
            employee_code=employee.employee_id,
            employee_name=_employee_name(employee),
            schedule_date=s.schedule_date.isoformat(),
            shift_template_id=template.pk,
            related_dates=[],
            detail={
                'shift_span_minutes': span_minutes,
                'break_minutes': template.break_minutes or 0,
                'required_break_minutes': required_break_minutes,
            },
        ))
    return out


def _check_holidays(
    schedules: List[Schedule],
    schedule_version: ScheduleVersion,
) -> List[Violation]:
    """§37/§39：國定假日出勤——不違法但工資應加倍，soft 提醒。"""
    from .models import Holiday
    holidays = {
        h.date: h.name
        for h in Holiday.objects.filter(
            organization=schedule_version.organization,
            date__gte=schedule_version.period_start,
            date__lte=schedule_version.period_end,
        )
    }
    if not holidays:
        return []
    out: List[Violation] = []
    for s in schedules:
        name = holidays.get(s.schedule_date)
        if not name:
            continue
        out.append(Violation(
            rule='holiday_scheduling',
            severity='soft',
            employee_pk=s.employee.pk,
            employee_code=s.employee.employee_id,
            employee_name=_employee_name(s.employee),
            schedule_date=s.schedule_date.isoformat(),
            shift_template_id=s.shift_template_id,
            related_dates=[],
            detail={'holiday_name': name},
        ))
    return out


WEEKDAY_ZH = ['週一', '週二', '週三', '週四', '週五', '週六', '週日']


def _check_org_closed_days(
    schedules: List[Schedule],
    schedule_version: ScheduleVersion,
) -> List[Violation]:
    """機構公休日（PM#1）：排在每週公休日的班以 soft 提醒，不阻擋。"""
    from .models import OrgComplianceSettings
    cfg = OrgComplianceSettings.objects.filter(
        organization=schedule_version.organization,
    ).first()
    closed = set(cfg.weekly_closed_days or []) if cfg else set()
    if not closed:
        return []
    out: List[Violation] = []
    for s in schedules:
        dow = s.schedule_date.weekday()
        if dow not in closed:
            continue
        out.append(Violation(
            rule='org_closed_day',
            severity='soft',
            employee_pk=s.employee.pk,
            employee_code=s.employee.employee_id,
            employee_name=_employee_name(s.employee),
            schedule_date=s.schedule_date.isoformat(),
            shift_template_id=s.shift_template_id,
            related_dates=[],
            detail={'weekday': dow, 'weekday_label': WEEKDAY_ZH[dow]},
        ))
    return out
