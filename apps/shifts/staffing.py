"""
Daily staffing requirement resolution（每天需要幾個人）.

Shared by the OR-Tools solver, the LLM scheduler and the compliance
coverage report so all three agree on how many people a (date, shift)
needs. Works on plain dicts so the solver side stays JSON-serialisable
(Celery) and unit-testable without the ORM.

Rule dict shape (what `load_staffing_rules` returns and the solver reads):
    {"id", "branch_id", "shift_template_id", "weekday", "date", "min_staff", "max_staff"}
Resolved requirement shape (`staffing_plan` output):
    {"date": "YYYY-MM-DD", "shift_id": int | None, "min_staff": int, "max_staff": int | None}
`shift_id is None` ⇒ 當日總人數（distinct employees working that day）.
"""
from datetime import date, timedelta
from typing import Any, Dict, Iterable, List, Optional


def load_staffing_rules(organization_id: int, branch_id: Optional[int] = None) -> List[Dict[str, Any]]:
    """Active rules for an org. Branch-specific rules are included only when
    scheduling that branch; an org-wide run (branch_id=None) sees org-wide
    rules only, because a branch's headcount is not the org's headcount."""
    from .models import StaffingRequirement
    qs = StaffingRequirement.objects.filter(
        organization_id=organization_id, is_active=True,
    )
    if branch_id:
        qs = qs.filter(models_q_branch(branch_id))
    else:
        qs = qs.filter(branch__isnull=True)
    return [
        {
            'id': r.id,
            'branch_id': r.branch_id,
            'shift_template_id': r.shift_template_id,
            'weekday': r.weekday,
            'date': r.date.isoformat() if r.date else None,
            'min_staff': r.min_staff,
            'max_staff': r.max_staff,
        }
        for r in qs
    ]


def models_q_branch(branch_id: int):
    from django.db.models import Q
    return Q(branch__isnull=True) | Q(branch_id=branch_id)


def _specificity(rule: Dict[str, Any]) -> int:
    """Higher wins: date(4) > weekday(2) > any-day(0); +1 when branch-specific."""
    score = 4 if rule.get('date') else 2 if rule.get('weekday') is not None else 0
    if rule.get('branch_id'):
        score += 1
    return score


def resolve_staffing(
    rules: Iterable[Dict[str, Any]],
    day: date,
    shift_id: Optional[int],
) -> Optional[Dict[str, Any]]:
    """Pick the most specific rule for (day, shift_id). None when nothing matches."""
    day_iso = day.isoformat()
    best, best_score = None, -1
    for rule in rules:
        if rule.get('shift_template_id') != shift_id:
            continue
        if rule.get('date'):
            if rule['date'] != day_iso:
                continue
        elif rule.get('weekday') is not None and rule['weekday'] != day.weekday():
            continue
        score = _specificity(rule)
        if score > best_score:
            best, best_score = rule, score
    if best is None:
        return None
    return {'min_staff': int(best['min_staff']),
            'max_staff': (int(best['max_staff']) if best.get('max_staff') is not None else None),
            'rule_id': best.get('id')}


def staffing_plan(
    rules: Iterable[Dict[str, Any]],
    period_start: date,
    period_end: date,
    shift_ids: Iterable[int],
) -> List[Dict[str, Any]]:
    """Expand rules into one entry per (date, shift|day-total) that has a rule."""
    rules = list(rules)
    if not rules:
        return []
    out: List[Dict[str, Any]] = []
    day = period_start
    targets = list(shift_ids) + [None]
    while day <= period_end:
        for sid in targets:
            hit = resolve_staffing(rules, day, sid)
            if hit is None:
                continue
            out.append({
                'date': day.isoformat(), 'shift_id': sid,
                'min_staff': hit['min_staff'], 'max_staff': hit['max_staff'],
            })
        day += timedelta(days=1)
    return out


def plan_index(plan: Iterable[Dict[str, Any]]) -> Dict[tuple, Dict[str, Any]]:
    """(date_iso, shift_id|None) → {min_staff, max_staff} for O(1) lookups."""
    return {(p['date'], p.get('shift_id')): p for p in plan}


def version_shift_plan(version) -> Optional[List[Dict[str, Any]]]:
    """每張班表自己的每日班別計畫（2026-10-31，ScheduleVersionShiftPlan）→
    staffing plan rows。版本沒有任何計畫列回 None（呼叫端決定 400 或走舊路）。

    有計畫時語意是 explicit：沒列到的 (日期, 班別) = 那天不開那個班。
    呼叫端要同時設 constraints['staffing_mode'] = 'explicit'。
    """
    from apps.schedules.models import ScheduleVersionShiftPlan
    version_id = getattr(version, 'pk', version)
    rows = list(
        ScheduleVersionShiftPlan.objects.filter(schedule_version_id=version_id)
        .values('date', 'shift_template_id', 'min_staff', 'max_staff')
        .order_by('date', 'shift_template_id')
    )
    if not rows:
        return None
    return [
        {'date': r['date'].isoformat(), 'shift_id': r['shift_template_id'],
         'min_staff': int(r['min_staff']), 'max_staff': r['max_staff']}
        for r in rows
    ]


def coverage_report(
    *,
    period_start: date,
    period_end: date,
    shifts: List[Dict[str, Any]],
    assignments: Iterable[Dict[str, Any]],
    plan: Iterable[Dict[str, Any]],
    closed_weekdays: Iterable[int] = (),
    explicit: bool = False,
) -> List[Dict[str, Any]]:
    """
    Per (date, shift) and per day-total: required vs assigned headcount.

    `shifts`: [{id, name, min_staff_count}], `assignments`: [{employee_id, date, shift_id}].
    Day-total rows appear only when a day-level rule exists for that date.
    Closed weekdays are skipped (nobody should be there, so no "shortage").
    status: ok | under | over

    `explicit=True`（2026-10-31 版本每日班別計畫）：只有計畫列到的 (日期, 班別)
    才算「需要」（source="plan"）；沒列到卻有人被排的格子以 required 0/0、
    status "over"、source "unplanned" 回報，完全沒人的就不出現。
    """
    idx = plan_index(plan)
    closed = set(closed_weekdays or ())
    per_shift: Dict[tuple, int] = {}
    per_day: Dict[str, set] = {}
    for a in assignments:
        key = (a['date'], a['shift_id'])
        per_shift[key] = per_shift.get(key, 0) + 1
        per_day.setdefault(a['date'], set()).add(a['employee_id'])

    rows: List[Dict[str, Any]] = []
    day = period_start
    while day <= period_end:
        day_iso = day.isoformat()
        if day.weekday() in closed:
            day += timedelta(days=1)
            continue
        for s in shifts:
            req = idx.get((day_iso, s['id']))
            got = per_shift.get((day_iso, s['id']), 0)
            if explicit:
                if req is None:
                    if got == 0:
                        continue  # 那天沒開這個班、也沒人 → 不是缺口
                    rows.append({
                        'date': day_iso, 'shift_template_id': s['id'],
                        'shift_name': s.get('name'),
                        'required_min': 0, 'required_max': 0,
                        'assigned': got, 'status': 'over', 'source': 'unplanned',
                    })
                    continue
                min_staff, max_staff, source = req['min_staff'], req['max_staff'], 'plan'
            else:
                min_staff = req['min_staff'] if req else int(s.get('min_staff_count') or 0)
                max_staff = req['max_staff'] if req else None
                source = 'rule' if req else 'template'
            rows.append({
                'date': day_iso, 'shift_template_id': s['id'],
                'shift_name': s.get('name'),
                'required_min': min_staff, 'required_max': max_staff,
                'assigned': got,
                'status': _status(got, min_staff, max_staff),
                'source': source,
            })
        req = idx.get((day_iso, None))
        if req:
            got = len(per_day.get(day_iso, ()))
            rows.append({
                'date': day_iso, 'shift_template_id': None, 'shift_name': None,
                'required_min': req['min_staff'], 'required_max': req['max_staff'],
                'assigned': got,
                'status': _status(got, req['min_staff'], req['max_staff']),
                'source': 'rule',
            })
        day += timedelta(days=1)
    return rows


def _status(got: int, min_staff: int, max_staff: Optional[int]) -> str:
    if got < min_staff:
        return 'under'
    if max_staff is not None and got > max_staff:
        return 'over'
    return 'ok'
