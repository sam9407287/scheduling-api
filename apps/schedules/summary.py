"""
Approved-timeline aggregation for the approval summary view (簽核總表).

Aggregates schedules across ALL approved versions of one version_type.
When the same cell (employee × date) carries different content in different
approved versions, the cell is flagged as a discrepancy so the frontend can
ask the manager to confirm keeping all entries. Overlapping shift times are
NEVER treated as errors — future rosters (e.g. volunteer task rosters)
legitimately overlap the working-time roster.

There are NO time restrictions (2026-08-07): schedules may live on any date,
including outside their version's period_start/period_end — the period is
display metadata only. Everything here is therefore driven by the schedules
that actually exist, never by period coverage:
  - a version participates in the timeline if it has schedules in the range
    (or its period overlaps, so empty-but-relevant versions still show);
  - a version participates in a CELL only through its own entries there.
    A version that simply doesn't schedule that employee that day is not a
    discrepancy — only differing entries are.
"""
import hashlib

from django.db.models import Q

from .models import Schedule, ScheduleCellAcknowledgment, ScheduleVersion


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
      - versions: approved versions whose period overlaps the range OR that
        have schedules in the range (out-of-period schedules stay visible)
      - schedules: Schedule queryset of those versions within the range
      - cells: list of discrepant-cell dicts (entries never filtered)
    """
    versions = ScheduleVersion.objects.filter(
        organization_id=organization_id,
        version_type=version_type,
        status='approved',
    ).filter(
        Q(period_start__lte=date_to, period_end__gte=date_from)
        | Q(schedules__schedule_date__gte=date_from,
            schedules__schedule_date__lte=date_to)
    ).distinct().select_related('organization', 'branch', 'approved_by', 'created_by')
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
    for s in schedule_list:
        key = (s.employee_id, s.schedule_date)
        cells.setdefault(key, {}).setdefault(s.schedule_version_id, []).append(s)

    discrepant_cells = []
    for (employee_id, date), entries_by_version in cells.items():
        # A version participates in a cell only through its own entries:
        # "didn't schedule this employee that day" is not a discrepancy.
        participants = [version_by_id[vid] for vid in entries_by_version]
        if len(participants) < 2:
            continue

        signatures = {
            _cell_signature(entries) for entries in entries_by_version.values()
        }
        if len(signatures) < 2:
            continue

        content_hash = compute_cell_hash(
            employee_id, date, version_type, entries_by_version
        )

        entries = []
        for v in sorted(participants, key=lambda v: v.pk):
            for s in sorted(entries_by_version[v.pk], key=lambda s: s.shift_template_id):
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
    # Participation is entries-based (no period coverage — schedules may live
    # on any date): only approved versions with entries in this cell count.
    entries = Schedule.objects.filter(
        schedule_version__organization_id=organization_id,
        schedule_version__version_type=version_type,
        schedule_version__status='approved',
        employee=employee,
        schedule_date=date,
    ).select_related('shift_template', 'schedule_version')

    entries_by_version = {}
    participants = {}
    for s in entries:
        entries_by_version.setdefault(s.schedule_version_id, []).append(s)
        participants[s.schedule_version_id] = s.schedule_version
    if len(participants) < 2:
        return None, None

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
        for v in sorted(participants.values(), key=lambda v: v.pk)
    ]
    return content_hash, involved
