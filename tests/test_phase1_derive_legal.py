"""
Phase 1 — OR-Tools drift mode and the `derive-legal` API.

Coverage:
  * Drift mode with a legal seed produces a result identical to the seed.
  * Drift mode repairs an illegal seed (rest-interval violation) by changing
    cells, and the produced schedule passes the per-cell compliance engine.
  * Drift mode honours `time_decay_n`: given two symmetric illegal cells, the
    one further from `today` is the one the solver chooses to change.
  * The endpoint validates input (must target an actual/B version).
  * The endpoint persists an A version with derived_from pointing back to B
    and returns a diff summary.

These tests deliberately use the live OR-Tools solver — the production code
path — rather than mocking. Each scenario stays tiny (≤ 2 shifts × 2-5 days)
so the solver finishes in well under a second.
"""
import pytest  # noqa: F401  (fixtures via conftest)
from datetime import date, time, timedelta
from decimal import Decimal

from apps.employees.models import Employee
from apps.shifts.models import ShiftTemplate
from apps.schedules.models import Schedule, ScheduleVersion
from apps.ai_engine.providers.ortools_provider import ORToolsProvider
from apps.ai_engine.providers.base import ScheduleRequest
from apps.compliance.engine import check_schedule_violations


# ---------------------------------------------------------------------------
# Tiny test factory: shared by all test classes here.
# ---------------------------------------------------------------------------

def _shift(org, name, start, end, **kw):
    return ShiftTemplate.objects.create(
        organization=org, name=name,
        start_time=start, end_time=end,
        min_staff_count=kw.pop('min_staff_count', 1),
        **kw,
    )


def _make_employee(org, branch, role, code, idx):
    from apps.accounts.models import User
    u = User.objects.create_user(
        username=f'e_drift_{code}_{idx}', password='pw',
        first_name=f'F{idx}', last_name=f'L{idx}',
        role=role, organization=org, branch=branch,
    )
    return Employee.objects.create(
        user=u, employee_id=f'{code}{idx}',
        organization=org, branch=branch,
        position='nurse', hire_date=date(2024, 1, 1),
    )


def _version(org, branch, user, label, vtype='actual'):
    return ScheduleVersion.objects.create(
        organization=org, branch=branch, version_label=label,
        version_type=vtype,
        period_start=date(2026, 6, 1), period_end=date(2026, 6, 7),
        created_by=user,
    )


def _provider_inputs(employees, shifts):
    """Pack ORM employees + shifts into the dict shape the provider expects."""
    return (
        [
            {
                'id': e.id,
                'employee_id': e.employee_id,
                'agreed_hours_per_week': float(e.agreed_hours_per_week),
                'certifications': [], 'unavailable_dates': [],
                'availability': {},
            }
            for e in employees
        ],
        [
            {
                'id': st.id, 'name': st.name,
                'start_time': st.start_time.isoformat(),
                'end_time': st.end_time.isoformat(),
                'break_minutes': st.break_minutes,
                'min_staff_count': st.min_staff_count,
                'required_certifications': [],
                'employee_priorities': [],
            }
            for st in shifts
        ],
    )


# ===========================================================================
# Provider-level: drift objective + labor-law hard constraints
# ===========================================================================

class TestDriftMode:
    def test_legal_seed_is_preserved_unchanged(
        self, db, organization, branch, admin_user, employee_role
    ):
        """No violations in the seed ⇒ drift = 0 ⇒ result equals seed."""
        e1 = _make_employee(organization, branch, employee_role, 'A', 1)
        e2 = _make_employee(organization, branch, employee_role, 'A', 2)
        day = _shift(organization, 'D', time(9, 0), time(17, 0))

        period = [date(2026, 6, 1) + timedelta(days=i) for i in range(3)]
        seed = []
        for i, d in enumerate(period):
            emp = e1 if i % 2 == 0 else e2
            seed.append({'employee_id': emp.id, 'date': d.isoformat(),
                         'shift_id': day.id})

        emps, sfts = _provider_inputs([e1, e2], [day])
        req = ScheduleRequest(
            organization_id=organization.id, branch_id=branch.id,
            period_start=period[0], period_end=period[-1],
            employees=emps, shift_templates=sfts,
            constraints={'max_weekly_hours': 40, 'max_consecutive_days': 6,
                         'min_rest_hours': 11},
            preferences={},
            seed=seed, minimize_drift_from_seed=True,
            today=period[0], time_decay_n=7,
        )
        result = ORToolsProvider().generate_schedule(req)
        assert result.success, result.message
        assert result.metadata['mode'] == 'derive_legal'
        produced = {(a['employee_id'], a['date'], a['shift_id'])
                    for a in result.assignments}
        expected = {(s['employee_id'], s['date'], s['shift_id']) for s in seed}
        assert produced == expected

    def test_consecutive_days_violation_gets_repaired(
        self, db, organization, branch, admin_user, employee_role
    ):
        """E1 scheduled 7 straight days; cap=5 → solver must move ≥ 2 cells."""
        e1 = _make_employee(organization, branch, employee_role, 'B', 1)
        e2 = _make_employee(organization, branch, employee_role, 'B', 2)
        day = _shift(organization, 'D', time(9, 0), time(17, 0))

        period = [date(2026, 6, 1) + timedelta(days=i) for i in range(7)]
        # Seed: E1 every day, E2 never works.
        seed = [{'employee_id': e1.id, 'date': d.isoformat(),
                 'shift_id': day.id} for d in period]

        emps, sfts = _provider_inputs([e1, e2], [day])
        req = ScheduleRequest(
            organization_id=organization.id, branch_id=branch.id,
            period_start=period[0], period_end=period[-1],
            employees=emps, shift_templates=sfts,
            constraints={'max_weekly_hours': 999, 'max_consecutive_days': 5,
                         'min_rest_hours': 0},
            preferences={},
            seed=seed, minimize_drift_from_seed=True,
            today=period[0], time_decay_n=1,  # flat-ish weighting
        )
        result = ORToolsProvider().generate_schedule(req)
        assert result.success
        # E1 must have *some* day off (so the streak is broken). Total days
        # can be 6 with the off-day in the middle — that is legal and
        # minimises drift, so accept it.
        e1_dates = sorted(
            date.fromisoformat(a['date'])
            for a in result.assignments if a['employee_id'] == e1.id
        )
        # Longest consecutive run for E1 must be ≤ 5.
        longest = current = 1
        for prev, curr in zip(e1_dates, e1_dates[1:]):
            current = current + 1 if (curr - prev).days == 1 else 1
            longest = max(longest, current)
        assert longest <= 5, f'E1 longest streak {longest} > 5: {e1_dates}'
        # E2 must have picked up at least one day because min_staff=1.
        e2_days = [a for a in result.assignments if a['employee_id'] == e2.id]
        assert len(e2_days) >= 1

    def test_time_decay_protects_near_cells(
        self, db, organization, branch, admin_user, employee_role
    ):
        """
        Two cells must be flipped to satisfy max_consecutive_days; one is close
        to `today`, one is far. Time-decay weighting should make the far cell
        the one the solver changes.
        """
        e1 = _make_employee(organization, branch, employee_role, 'C', 1)
        e2 = _make_employee(organization, branch, employee_role, 'C', 2)
        day = _shift(organization, 'D', time(9, 0), time(17, 0))

        # 6-day period: day0 (today) through day5
        period = [date(2026, 6, 1) + timedelta(days=i) for i in range(6)]
        # Seed: E1 every day, cap=2 → solver must break the streak with
        # ≥ 2 cells reassigned to E2. Time decay should push the changes
        # toward the far end.
        seed = [{'employee_id': e1.id, 'date': d.isoformat(),
                 'shift_id': day.id} for d in period]

        emps, sfts = _provider_inputs([e1, e2], [day])
        req = ScheduleRequest(
            organization_id=organization.id, branch_id=branch.id,
            period_start=period[0], period_end=period[-1],
            employees=emps, shift_templates=sfts,
            constraints={'max_weekly_hours': 999, 'max_consecutive_days': 2,
                         'min_rest_hours': 0},
            preferences={},
            seed=seed, minimize_drift_from_seed=True,
            today=period[0], time_decay_n=10,  # steep gradient
        )
        result = ORToolsProvider().generate_schedule(req)
        assert result.success
        # Verify: the produced schedule for the first three days (closest to
        # today) should match seed for at least the first two — those are the
        # most expensive cells to flip. The streak break should appear later.
        first_two = sorted(
            [a for a in result.assignments
             if a['date'] in {period[0].isoformat(), period[1].isoformat()}],
            key=lambda a: a['date'],
        )
        assert all(a['employee_id'] == e1.id for a in first_two), (
            f'Near cells were flipped despite high time-decay weight: '
            f'{first_two}'
        )

    def test_derived_a_passes_compliance_check(
        self, db, organization, branch, admin_user, employee_role
    ):
        """End-to-end: feed a seed with rest-interval violations, derive A,
        run A through the per-cell compliance engine — expect zero hard
        violations."""
        e1 = _make_employee(organization, branch, employee_role, 'D', 1)
        e2 = _make_employee(organization, branch, employee_role, 'D', 2)
        night = _shift(organization, 'N', time(22, 0), time(6, 0))
        morning = _shift(organization, 'M', time(8, 0), time(16, 0))

        # Seed: E1 night → next-morning, only 2h rest → rest interval violation.
        seed = [
            {'employee_id': e1.id, 'date': '2026-06-01', 'shift_id': night.id},
            {'employee_id': e1.id, 'date': '2026-06-02', 'shift_id': morning.id},
        ]
        emps, sfts = _provider_inputs([e1, e2], [night, morning])
        req = ScheduleRequest(
            organization_id=organization.id, branch_id=branch.id,
            period_start=date(2026, 6, 1), period_end=date(2026, 6, 2),
            employees=emps, shift_templates=sfts,
            constraints={'max_weekly_hours': 40, 'max_consecutive_days': 6,
                         'min_rest_hours': 11},
            preferences={},
            seed=seed, minimize_drift_from_seed=True,
            today=date(2026, 6, 1), time_decay_n=2,
        )
        result = ORToolsProvider().generate_schedule(req)
        assert result.success

        # Persist the result into a real A version, then run compliance.
        a_version = ScheduleVersion.objects.create(
            organization=organization, branch=branch,
            version_label='A-test', version_type='legal',
            period_start=date(2026, 6, 1), period_end=date(2026, 6, 2),
            created_by=admin_user,
        )
        shift_orm = {st.id: st for st in [night, morning]}
        for a in result.assignments:
            Schedule.objects.create(
                schedule_version=a_version,
                employee_id=a['employee_id'],
                shift_template_id=a['shift_id'],
                schedule_date=date.fromisoformat(a['date']),
                expected_hours=round(shift_orm[a['shift_id']].duration_hours, 2),
            )
        violations = check_schedule_violations(a_version)
        hard = [v for v in violations if v.severity == 'hard']
        assert hard == [], f'derived A still has hard violations: {hard}'


# ===========================================================================
# API: POST /api/schedules/versions/{B_id}/derive-legal/
# ===========================================================================

class TestDeriveLegalEndpoint:
    def test_rejects_non_actual_version(
        self, db, organization, branch, admin_user, admin_api_client
    ):
        a = _version(organization, branch, admin_user, 'A-only', vtype='legal')
        resp = admin_api_client.post(
            f'/api/schedules/versions/{a.id}/derive-legal/', {}, format='json'
        )
        assert resp.status_code == 400
        assert 'actual' in resp.json()['error']

    def test_rejects_empty_b(
        self, db, organization, branch, admin_user, admin_api_client
    ):
        b = _version(organization, branch, admin_user, 'B-empty')
        resp = admin_api_client.post(
            f'/api/schedules/versions/{b.id}/derive-legal/', {}, format='json'
        )
        assert resp.status_code == 400
        assert 'no schedule rows' in resp.json()['error']

    def test_happy_path_persists_a_and_returns_diff(
        self, db, organization, branch, admin_user, admin_api_client,
        employee_role,
    ):
        e1 = _make_employee(organization, branch, employee_role, 'API', 1)
        e2 = _make_employee(organization, branch, employee_role, 'API', 2)
        day = _shift(organization, 'D', time(9, 0), time(17, 0))

        b = _version(organization, branch, admin_user, 'B-api')
        # 7 consecutive days for E1 (cap=6 → 1 cell must move)
        period = [date(2026, 6, 1) + timedelta(days=i) for i in range(7)]
        for d in period:
            Schedule.objects.create(
                schedule_version=b, employee=e1, shift_template=day,
                schedule_date=d, expected_hours=Decimal('8'),
            )

        resp = admin_api_client.post(
            f'/api/schedules/versions/{b.id}/derive-legal/',
            {'today': period[0].isoformat(), 'time_decay_n': 14,
             'constraints': {'max_consecutive_days': 6, 'max_weekly_hours': 999,
                             'min_rest_hours': 0}},
            format='json',
        )
        assert resp.status_code == 201, resp.content
        body = resp.json()
        assert body['derived_from_id'] == b.id
        new_id = body['legal_version_id']
        a_ver = ScheduleVersion.objects.get(id=new_id)
        assert a_ver.version_type == 'legal'
        assert a_ver.derived_from_id == b.id
        # 1 cell must have moved (E2 takes one day)
        assert body['diff_summary']['cells_in_b'] == 7
        assert body['diff_summary']['cells_in_a'] == 7
        assert body['diff_summary']['cells_removed_from_b'] >= 1
        assert body['diff_summary']['cells_added_in_a'] >= 1

        # The A version's compliance check returns 0 violations.
        check_resp = admin_api_client.post(
            f'/api/schedules/versions/{new_id}/check-compliance/',
            {'rules': {'max_consecutive_days': 6, 'max_weekly_hours': 999,
                       'min_rest_hours': 0, 'max_daily_hours': 24}},
            format='json',
        )
        assert check_resp.status_code == 200
        assert check_resp.json()['total_count'] == 0
