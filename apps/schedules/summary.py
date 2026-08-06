"""
Approved-timeline aggregation for the approval summary view (簽核總表).

Aggregates schedules across ALL approved versions of one version_type.
When the same cell (employee × date) carries different content in different
approved versions, the cell is flagged as a discrepancy so the frontend can
ask the manager to confirm keeping all entries. Overlapping shift times are
NEVER treated as errors — future rosters (e.g. volunteer task rosters)
legitimately overlap the working-time roster.
"""
import hashlib

from django.db.models import Q

from .models import Schedule, ScheduleCellAcknowledgment, ScheduleVersion


def _version_applies_to_employee(version, employee):
    """Version with no branch applies to every employee; otherwise branches must match.

    Mirrors the frontend's current logic in ApprovalScheduleSummaryPage.
    """
    return version.branch_id is None or version.branch_id == employee.branch_id


def _cell_signature(entries):
    """Content signature of one version's entries in a cell.

    Sorted (shift_template_id, expected_hours) pairs; an empty cell is the
    empty signature. Times are derived from the template, so the pair fully
    identifies the content the manager sees.
    """
    return tuple(sorted(
        (e.shift_template_id, str(e.expected_hours)) for e in entries
    ))


def compute_cell_hash(employee_id, date, version_type, entries_by_version):
    """Stable hash of a cell's full cross-version content.

    entries_by_version: {version_id: [Schedule, ...]}
    Any change to the involved version set or any version's entries changes
    the hash, which invalidates prior acknowledgments.
    """
    parts = []
    for version_id in sorted(entries_by_version):
        sig = _cell_signature(entries_by_version[version_id])
        sig_str = ','.join(f"{sid}:{hours}" for sid, hours in sig)
        parts.append(f"v{version_id}:[{sig_str}]")
    payload = f"{employee_id}|{date.isoformat()}|{version_type}|" + ';'.join(parts)
    return hashlib.sha256(payload.encode('utf-8')).hexdigest()


def build_approved_timeline(organization_id, version_type, date_from, date_to, branch_id=None):
    """Build the approval-summary payload.

    Returns (versions, schedules, cells):
      - versions: approved ScheduleVersion queryset overlapping the range
      - schedules: Schedule queryset of those versions within the range
      - cells: list of discrepant-cell dicts (entries never filtered)
    """
    versions = ScheduleVersion.objects.filter(
        organization_id=organization_id,
        version_type=version_type,
        status='approved',
        period_start__lte=date_to,
        period_end__gte=date_from,
    ).select_related('organization', 'branch', 'approved_by', 'created_by')
    if branch_id is not None:
        versions = versions.filter(Q(branch__isnull=True) | Q(branch_id=branch_id))

    version_list = list(versions)
    version_by_id = {v.pk: v for v in version_list}

    schedules = Schedule.objects.filter(
        schedule_version__in=version_list,
        schedule_date__gte=date_from,
        schedule_date__lte=date_to,
    ).select_related('employee', 'shift_template', 'schedule_version')

    schedule_list = list(schedules)

    # (employee_id, date) -> {version_id: [Schedule, ...]}
    cells = {}
    employees = {}
    for s in schedule_list:
        key = (s.employee_id, s.schedule_date)
        cells.setdefault(key, {}).setdefault(s.schedule_version_id, []).append(s)
        employees[s.employee_id] = s.employee

    discrepant_cells = []
    for (employee_id, date), entries_by_version in cells.items():
        employee = employees[employee_id]

        # Versions covering this date and applicable to this employee.
        covering = [
            v for v in version_list
            if v.period_start <= date <= v.period_end
            and _version_applies_to_employee(v, employee)
        ]
        if len(covering) < 2:
            continue

        # Signature per covering version; a covering version with no entries
        # in this cell contributes the empty signature.
        signatures = {
            _cell_signature(entries_by_version.get(v.pk, []))
            for v in covering
        }
        if len(signatures) < 2:
            continue

        hash_input = {
            v.pk: entries_by_version.get(v.pk, []) for v in covering
        }
        content_hash = compute_cell_hash(employee_id, date, version_type, hash_input)

        entries = []
        for v in sorted(covering, key=lambda v: v.pk):
            for s in sorted(entries_by_version.get(v.pk, []), key=lambda s: s.shift_template_id):
                entries.append({
                    'schedule_id': s.pk,
                    'version_id': v.pk,
                    'version_label': v.version_label,
                    'shift_template_id': s.shift_template_id,
                    'shift_name': s.shift_template.name,
                    'start_time': str(s.shift_template.start_time),
                    'end_time': str(s.shift_template.end_time),
                    'expected_hours': str(s.expected_hours),
                    'notes': s.notes,
                })

        discrepant_cells.append({
            'employee_id': employee_id,
            'date': date.isoformat(),
            'entries': entries,
            'is_discrepant': True,
            'content_hash': content_hash,
            'acknowledged': False,
            'acknowledged_by': None,
            'acknowledged_at': None,
        })

    _annotate_acknowledgments(discrepant_cells, organization_id, version_type)

    discrepant_cells.sort(key=lambda c: (c['date'], c['employee_id']))
    return version_list, schedule_list, discrepant_cells


def _annotate_acknowledgments(cells, organization_id, version_type):
    if not cells:
        return
    hashes = [c['content_hash'] for c in cells]
    acks = ScheduleCellAcknowledgment.objects.filter(
        organization_id=organization_id,
        version_type=version_type,
        content_hash__in=hashes,
    ).select_related('acknowledged_by')
    ack_by_key = {
        (a.employee_id, a.schedule_date.isoformat(), a.content_hash): a
        for a in acks
    }
    for cell in cells:
        ack = ack_by_key.get((cell['employee_id'], cell['date'], cell['content_hash']))
        if ack:
            cell['acknowledged'] = True
            cell['acknowledged_by'] = {
                'id': ack.acknowledged_by_id,
                'username': ack.acknowledged_by.username if ack.acknowledged_by else None,
            }
            cell['acknowledged_at'] = ack.acknowledged_at.isoformat()


def current_cell_state(organization_id, version_type, employee, date):
    """Recompute one cell's current discrepancy state (for acknowledgment validation).

    Returns (content_hash, involved_snapshot), or (None, None) when the cell
    is not currently discrepant.
    """
    versions = list(ScheduleVersion.objects.filter(
        organization_id=organization_id,
        version_type=version_type,
        status='approved',
        period_start__lte=date,
        period_end__gte=date,
    ))
    covering = [v for v in versions if _version_applies_to_employee(v, employee)]
    if len(covering) < 2:
        return None, None

    entries = Schedule.objects.filter(
        schedule_version__in=covering,
        employee=employee,
        schedule_date=date,
    ).select_related('shift_template')
    entries_by_version = {v.pk: [] for v in covering}
    for s in entries:
        entries_by_version[s.schedule_version_id].append(s)

    signatures = {_cell_signature(e) for e in entries_by_version.values()}
    if len(signatures) < 2:
        return None, None

    content_hash = compute_cell_hash(employee.pk, date, version_type, entries_by_version)
    involved = [
        {
            'version_id': v.pk,
            'version_label': v.version_label,
            'entries': [
                {
                    'schedule_id': s.pk,
                    'shift_template_id': s.shift_template_id,
                    'shift_name': s.shift_template.name,
                    'expected_hours': str(s.expected_hours),
                }
                for s in sorted(entries_by_version[v.pk], key=lambda s: s.shift_template_id)
            ],
        }
        for v in sorted(covering, key=lambda v: v.pk)
    ]
    return content_hash, involved
