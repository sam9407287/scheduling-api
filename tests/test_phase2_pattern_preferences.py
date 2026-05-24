"""
Phase 2 PR9 — shift_pattern_preference soft constraints.

The field `Employee.shift_pattern_preference` was added in PR1 with three
values:

  * `none`        → not modelled (no objective term emitted)
  * `alternating` → 花花班; penalty per pair of consecutive days where the
                    employee works the *same time-of-day bucket* on both days
  * `consecutive` → 連上放長假; penalty per work/rest transition between
                    consecutive days, so the solver prefers packing work-days
                    together with rest-days together

These tests pin the *observable* behaviour rather than the inner variable
names, so the solver is free to choose any equivalently-scored solution.

Penalty weight is 2 per pair, deliberately below the fairness weight of
10, so when fairness and pattern preference conflict fairness wins — the
customer wanted preferences as a tie-breaker, not a hammer.
"""
import pytest  # noqa: F401  (conftest fixtures)
from datetime import date, time, timedelta
from ortools.sat.python import cp_model

from apps.ai_engine.providers.ortools_provider import ORToolsProvider
from apps.ai_engine.providers.base import ScheduleRequest


def _shift_dict(sid, name, start_hm):
    return {
        'id': sid, 'name': name,
        'start_time': start_hm, 'end_time': '17:00',
        'break_minutes': 0, 'min_staff_count': 1,
        'required_certifications': [], 'employee_priorities': [],
    }


def _emp_dict(eid, pattern='none'):
    return {
        'id': eid,
        'employee_id': f'E{eid}',
        'agreed_hours_per_week': 40.0,
        'certifications': [], 'unavailable_dates': [],
        'availability': {},
        'attributes': {'shift_pattern_preference': pattern},
    }


def _solve(employees, shifts, days):
    """Run a one-shot solve with default settings. Returns the assignments list."""
    req = ScheduleRequest(
        organization_id=1, branch_id=None,
        period_start=days[0], period_end=days[-1],
        employees=employees, shift_templates=shifts,
        constraints={}, preferences={},
    )
    result = ORToolsProvider().generate_schedule(req)
    assert result.success, result.message
    return result.assignments


# ===========================================================================
# alternating: penalty per consecutive same-bucket pair
# ===========================================================================

class TestAlternatingPreference:
    def test_solver_avoids_consecutive_same_bucket(self):
        """
        Setup: 2 employees, 2 shifts in *different* time buckets (morning
        and afternoon), 2 days. min_staff=1 per (day, shift). Both
        employees must work each day to cover the four cells.

        E1 prefers `alternating`. Without the preference, fairness alone
        leaves several equivalent solutions; the alternating penalty
        breaks the tie by giving E1 a different bucket on each day.
        """
        e1 = _emp_dict(1, pattern='alternating')
        e2 = _emp_dict(2, pattern='none')
        morning = _shift_dict(10, 'morning', '08:00')  # bucket=morning
        afternoon = _shift_dict(20, 'afternoon', '13:00')  # bucket=afternoon
        days = [date(2026, 7, 1), date(2026, 7, 2)]
        assignments = _solve([e1, e2], [morning, afternoon], days)

        e1_buckets = sorted(
            (a['date'], a['shift_id']) for a in assignments
            if a['employee_id'] == 1
        )
        # E1 should hit two different shift ids across the two days.
        e1_shift_ids = {sid for _, sid in e1_buckets}
        assert len(e1_shift_ids) == 2, (
            f'alternating-pref employee got same shift twice: {e1_buckets}'
        )

    def test_no_pref_employee_can_repeat(self):
        """Same scenario, but E1 has `none` — solver is free to repeat
        the same bucket because there is no per-employee penalty."""
        e1 = _emp_dict(1, pattern='none')
        e2 = _emp_dict(2, pattern='none')
        morning = _shift_dict(10, 'morning', '08:00')
        afternoon = _shift_dict(20, 'afternoon', '13:00')
        days = [date(2026, 7, 1), date(2026, 7, 2)]
        # Multiple optimal solutions exist; we just assert the solve runs.
        # Pinning a specific layout would over-specify the solver.
        assignments = _solve([e1, e2], [morning, afternoon], days)
        assert len(assignments) == 4  # 2 days × 2 shifts × 1 staff each


# ===========================================================================
# consecutive: penalty per work/rest transition
# ===========================================================================

class TestConsecutivePreference:
    def test_consecutive_pref_packs_work_days_together(self):
        """
        Setup: 1 shift, 4 days, 2 employees. Each day needs exactly 1 person,
        and one-shift-per-day caps each employee at 4 shifts. Fairness wants
        a 2:2 split; consecutive preference for E1 picks the *contiguous*
        2-day block over the alternating one, so the work/rest transition
        count drops from 3 to 1.
        """
        e1 = _emp_dict(1, pattern='consecutive')
        e2 = _emp_dict(2, pattern='none')
        only = _shift_dict(10, 'day', '09:00')
        days = [date(2026, 7, 1) + timedelta(days=i) for i in range(4)]
        assignments = _solve([e1, e2], [only], days)
        e1_dates = sorted(
            a['date'] for a in assignments if a['employee_id'] == 1
        )
        # E1 worked some days; verify the days form a single contiguous
        # block (transitions = 1: work → rest, or rest → work).
        assert len(e1_dates) >= 1
        as_dates = [date.fromisoformat(d) for d in e1_dates]
        gaps = [
            (as_dates[i + 1] - as_dates[i]).days
            for i in range(len(as_dates) - 1)
        ]
        # Every consecutive pair of E1-dates should be adjacent (gap=1).
        assert all(g == 1 for g in gaps), (
            f'consecutive-pref employee was scattered: {e1_dates}'
        )

    def test_alternating_and_consecutive_dont_clash(self):
        """Smoke test: solver still produces a valid schedule when both
        preferences appear in the same period."""
        e1 = _emp_dict(1, pattern='alternating')
        e2 = _emp_dict(2, pattern='consecutive')
        m = _shift_dict(10, 'morning', '08:00')
        a = _shift_dict(20, 'afternoon', '13:00')
        days = [date(2026, 7, 1) + timedelta(days=i) for i in range(3)]
        assignments = _solve([e1, e2], [m, a], days)
        # 3 days × 2 shifts × 1 staff each
        assert len(assignments) == 6


# ===========================================================================
# Wiring sanity: the field flows from Employee → solver input → objective.
# ===========================================================================

class TestPreferenceWiring:
    def test_attributes_dict_carries_preference(self, db, organization, branch, employee_role):
        """The view's `_employee_attributes_for_solver` puts the
        preference into `attributes` (non-sensitive, no consent gate)."""
        from apps.accounts.models import User
        from apps.employees.models import Employee
        from apps.ai_engine.views import _employee_attributes_for_solver

        u = User.objects.create_user(
            username='pref_u', password='pw', role=employee_role,
            organization=organization, branch=branch,
        )
        emp = Employee.objects.create(
            user=u, employee_id='PREF1',
            organization=organization, branch=branch,
            position='nurse', hire_date=date(2024, 1, 1),
            shift_pattern_preference='consecutive',
        )
        attrs = _employee_attributes_for_solver(emp)
        assert attrs['shift_pattern_preference'] == 'consecutive'

    def test_none_preference_emits_no_terms(self):
        """An employee with `none` should not contribute any objective
        term — we verify by counting the terms the helper returns."""
        provider = ORToolsProvider()
        # Build a minimal model + assignments grid so the helper runs.
        model = cp_model.CpModel()
        days = [date(2026, 7, 1), date(2026, 7, 2)]
        emp = _emp_dict(1, pattern='none')
        shift = _shift_dict(10, 'm', '08:00')
        # The helper expects `assignments[emp_id][day_idx][shift_id]` BoolVars.
        a = {1: {0: {10: model.NewBoolVar('x0')},
                 1: {10: model.NewBoolVar('x1')}}}
        terms = provider._add_pattern_preference_terms(
            model, a, [emp], [shift], days
        )
        assert terms == []
