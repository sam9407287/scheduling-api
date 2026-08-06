"""
Cross-version overlap detection for the approval summary view (簽核總表).

A conflict group is a set of schedules for the SAME employee, in the SAME
version_type track, from DIFFERENT approved versions, whose actual datetime
intervals intersect (cross-midnight aware). Same-version combinations are
never conflicts (that's the multi-shift feature), and versions from
different branches still conflict — one person cannot work two places at
once. Overlaps are informational: nothing here blocks saving or approving;
the manager resolves each group with a ScheduleOverlapDecision
(select = keep a non-overlapping subset, coexist = keep all with a comment).

conflict_key is derived from the group's schedule ids + updated_at stamps:
any change to a member schedule produces a new key, so stale decisions
never silently apply.
"""
import hashlib
from datetime import datetime, timedelta

from .models import Schedule, ScheduleOverlapDecision, ScheduleVersion


def _interval(schedule):
    """Concrete (start, end) datetimes; end <= start means cross-midnight."""
    template = schedule.shift_template
    start = datetime.combine(schedule.schedule_date, template.start_time)
    end = datetime.combine(schedule.schedule_date, template.end_time)
    if end <= start:
        end += timedelta(days=1)
    return start, end


def compute_conflict_key(schedules):
    """Deterministic key over the group's ids + updated_at stamps."""
    parts = sorted(
        f"{s.pk}:{s.updated_at.isoformat()}" for s in schedules
    )
    return hashlib.sha256('|'.join(parts).encode('utf-8')).hexdigest()


def _conflict_groups_for_employee(rows):
    """Connected components of cross-version overlapping schedules.

    rows: schedules of ONE employee. Edges only between different versions
    with intersecting intervals; components with >= 2 schedules are groups.
    """
    intervals = {s.pk: _interval(s) for s in rows}
    parent = {s.pk: s.pk for s in rows}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb

    linked = set()
    for i, a in enumerate(rows):
        for b in rows[i + 1:]:
            if a.schedule_version_id == b.schedule_version_id:
                continue
            a_start, a_end = intervals[a.pk]
            b_start, b_end = intervals[b.pk]
            if a_start < b_end and b_start < a_end:
                union(a.pk, b.pk)
                linked.add(a.pk)
                linked.add(b.pk)

    components = {}
    for s in rows:
        if s.pk not in linked:
            continue
        components.setdefault(find(s.pk), []).append(s)
    return [group for group in components.values() if len(group) >= 2]


def build_conflicts(schedules):
    """Group schedules into cross-version conflict dicts (undecided yet).

    Returns a list of {conflict_key, starts_at, ends_at, employee_id,
    schedule_ids, schedules(model list)} sorted by start.
    """
    by_employee = {}
    for s in schedules:
        by_employee.setdefault(s.employee_id, []).append(s)

    conflicts = []
    for employee_id, rows in by_employee.items():
        for group in _conflict_groups_for_employee(rows):
            group_intervals = [_interval(s) for s in group]
            conflicts.append({
                'conflict_key': compute_conflict_key(group),
                'starts_at': min(i[0] for i in group_intervals).isoformat(),
                'ends_at': max(i[1] for i in group_intervals).isoformat(),
                'employee_id': employee_id,
                'schedule_ids': sorted(s.pk for s in group),
                'schedules': sorted(group, key=lambda s: (_interval(s)[0], s.pk)),
            })

    conflicts.sort(key=lambda c: (c['starts_at'], c['employee_id']))
    return conflicts


def timeline_schedules(organization_id, version_type, date_from, date_to,
                       branch_id=None):
    """Schedules of approved versions for the range.

    Includes the previous day's rows so cross-midnight shifts bleeding into
    date_from still surface; non-cross-midnight rows of that day are
    dropped. branch filters by the EMPLOYEE's current branch (not the
    version's) — a person working two branches is still one person.
    """
    query = Schedule.objects.filter(
        schedule_version__organization_id=organization_id,
        schedule_version__version_type=version_type,
        schedule_version__status='approved',
        schedule_date__gte=date_from - timedelta(days=1),
        schedule_date__lte=date_to,
    ).select_related('employee', 'shift_template', 'schedule_version')
    if branch_id is not None:
        query = query.filter(employee__branch_id=branch_id)

    rows = []
    for s in query:
        if s.schedule_date < date_from:
            template = s.shift_template
            if template.end_time > template.start_time:
                continue  # 前一日非跨午夜班，不影響查詢範圍
        rows.append(s)
    return rows


def find_current_group(organization_id, version_type, schedule_ids):
    """Recompute the live conflict group containing the given schedules.

    Returns (group_schedules, conflict_key) or (None, None) when the
    submitted set no longer matches a current group (member edited/removed,
    or overlap dissolved).
    """
    members = list(Schedule.objects.filter(
        pk__in=schedule_ids,
        schedule_version__organization_id=organization_id,
        schedule_version__version_type=version_type,
        schedule_version__status='approved',
    ).select_related('employee', 'shift_template', 'schedule_version'))
    if len(members) != len(set(schedule_ids)) or not members:
        return None, None

    employee_ids = {s.employee_id for s in members}
    if len(employee_ids) != 1:
        return None, None
    employee_id = employee_ids.pop()

    # 以群組日期範圍前後各一天重算該員工的衝突群組
    dates = [s.schedule_date for s in members]
    rows = list(Schedule.objects.filter(
        schedule_version__organization_id=organization_id,
        schedule_version__version_type=version_type,
        schedule_version__status='approved',
        employee_id=employee_id,
        schedule_date__gte=min(dates) - timedelta(days=1),
        schedule_date__lte=max(dates) + timedelta(days=1),
    ).select_related('employee', 'shift_template', 'schedule_version'))

    target = set(schedule_ids)
    for group in _conflict_groups_for_employee(rows):
        if {s.pk for s in group} == target:
            return group, compute_conflict_key(group)
    return None, None


def annotate_decisions(conflicts, organization_id):
    """Attach the stored decision (or None) to each conflict dict."""
    keys = [c['conflict_key'] for c in conflicts]
    decisions = {
        d.conflict_key: d
        for d in ScheduleOverlapDecision.objects.filter(
            organization_id=organization_id,
            conflict_key__in=keys,
        ).select_related('decided_by')
    }
    for c in conflicts:
        c['decision'] = decisions.get(c['conflict_key'])
    return conflicts
