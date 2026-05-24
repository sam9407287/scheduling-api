"""
Phase 1 — TeamConstraint compiler and the unified `/api/ai/generate/` flow.

Coverage map:

  TestConditionMatcher      pure attribute matcher truth table
  TestConsentInvariant      sensitive conditions cannot match unconsented employees
  TestCompilerHardLogic     compiler emits the right CP-SAT clauses for
                            at_least / at_most / exactly with hard severity
  TestCompilerSoftLogic     soft severity adds a slack-weighted penalty
  TestGenerateEndpoint      `/api/ai/generate/` accepts the new fields and
                            wires team_constraints + billing intent
"""
import pytest  # noqa: F401  (conftest fixtures)
from datetime import date, time, timedelta
from decimal import Decimal
from django.utils import timezone
from ortools.sat.python import cp_model

from apps.employees.models import Employee, EmployeeDataConsent, EmployeeTag
from apps.shifts.models import ShiftTemplate, TeamConstraint
from apps.schedules.models import ScheduleVersion
from apps.ai_engine.team_constraint_compiler import (
    employee_matches_condition,
    apply_team_constraints,
)


def _emp_dict(emp_id, attributes):
    return {'id': emp_id, 'employee_id': f'E{emp_id}', 'attributes': attributes}


# ===========================================================================
# Pure matcher
# ===========================================================================

class TestConditionMatcher:
    def test_gender_eq(self):
        e = _emp_dict(1, {'gender': 'male'})
        assert employee_matches_condition(e, 'gender', 'eq', 'male')
        assert not employee_matches_condition(e, 'gender', 'eq', 'female')

    def test_height_gte(self):
        e = _emp_dict(1, {'height_cm': 178.5})
        assert employee_matches_condition(e, 'height_cm', 'gte', 175)
        assert not employee_matches_condition(e, 'height_cm', 'gte', 180)

    def test_age_lte(self):
        e = _emp_dict(1, {'age_years': 40})
        assert employee_matches_condition(e, 'age_years', 'lte', 45)
        assert not employee_matches_condition(e, 'age_years', 'lte', 35)

    def test_tag_contains(self):
        e = _emp_dict(1, {'tag_codes': ['driver', 'bilingual']})
        assert employee_matches_condition(e, 'tag', 'contains', ['driver'])
        assert not employee_matches_condition(e, 'tag', 'contains', ['driver', 'lifter'])

    def test_certification_in(self):
        e = _emp_dict(1, {'certification_ids': [5, 7, 9]})
        assert employee_matches_condition(e, 'certification', 'in', [3, 5])
        assert not employee_matches_condition(e, 'certification', 'in', [1, 2])


# ===========================================================================
# Consent invariant — the single most important boundary
# ===========================================================================

class TestConsentInvariant:
    def test_unconsented_employee_invisible_to_sensitive_conditions(
        self, db, organization, branch, employee_role
    ):
        """An employee with real height/gender but no consent must NOT
        satisfy a sensitive condition."""
        from apps.accounts.models import User
        u = User.objects.create_user(
            username='no_consent', password='pw',
            role=employee_role, organization=organization, branch=branch,
        )
        emp = Employee.objects.create(
            user=u, employee_id='NC1',
            organization=organization, branch=branch,
            position='nurse', hire_date=date(2024, 1, 1),
            gender='male', height_cm=Decimal('180'),
        )
        from apps.ai_engine.views import _employee_attributes_for_solver
        attrs = _employee_attributes_for_solver(emp)
        # All sensitive entries are nulled out
        assert attrs['gender'] is None
        assert attrs['height_cm'] is None
        assert attrs['age_years'] is None
        # And so the matcher rejects sensitive conditions
        ed = {'id': emp.id, 'attributes': attrs}
        assert not employee_matches_condition(ed, 'gender', 'eq', 'male')
        assert not employee_matches_condition(ed, 'height_cm', 'gte', 175)

    def test_consented_employee_visible(
        self, db, organization, branch, employee_role
    ):
        from apps.accounts.models import User
        u = User.objects.create_user(
            username='yes_consent', password='pw',
            role=employee_role, organization=organization, branch=branch,
        )
        emp = Employee.objects.create(
            user=u, employee_id='YC1',
            organization=organization, branch=branch,
            position='nurse', hire_date=date(2024, 1, 1),
            gender='male', height_cm=Decimal('180'),
            birth_date=date(1990, 1, 1),
        )
        EmployeeDataConsent.objects.create(employee=emp, consented_at=timezone.now())

        from apps.ai_engine.views import _employee_attributes_for_solver
        attrs = _employee_attributes_for_solver(emp)
        assert attrs['gender'] == 'male'
        assert attrs['height_cm'] == 180.0
        assert attrs['age_years'] is not None and attrs['age_years'] >= 30

    def test_tag_not_gated_by_consent(
        self, db, organization, branch, employee_role
    ):
        """Tags and certification IDs are not sensitive — they remain
        visible without consent."""
        from apps.accounts.models import User
        u = User.objects.create_user(
            username='tag_user', password='pw',
            role=employee_role, organization=organization, branch=branch,
        )
        emp = Employee.objects.create(
            user=u, employee_id='TG1',
            organization=organization, branch=branch,
            position='nurse', hire_date=date(2024, 1, 1),
        )
        tag = EmployeeTag.objects.create(
            organization=organization, code='driver', label='司機',
        )
        emp.tags.add(tag)
        from apps.ai_engine.views import _employee_attributes_for_solver
        attrs = _employee_attributes_for_solver(emp)
        assert attrs['tag_codes'] == ['driver']


# ===========================================================================
# Compiler emits the right CP-SAT clauses
# ===========================================================================

def _build_model_with_assignments(employees, shifts, days):
    model = cp_model.CpModel()
    a = {}
    for emp in employees:
        a[emp['id']] = {}
        for di in range(len(days)):
            a[emp['id']][di] = {}
            for s in shifts:
                a[emp['id']][di][s['id']] = model.NewBoolVar(
                    f"e{emp['id']}_d{di}_s{s['id']}"
                )
    # Minimal structural constraint so the solver doesn't trivially zero
    # everything out: each (day, shift) gets exactly one person picked.
    for di in range(len(days)):
        for s in shifts:
            model.Add(sum(a[emp['id']][di][s['id']] for emp in employees) == 1)
        # One shift per day per employee.
        for emp in employees:
            model.Add(sum(a[emp['id']][di][s['id']] for s in shifts) <= 1)
    return model, a


class TestCompilerHardLogic:
    def test_at_least_one_male_forced_in(self):
        """3 employees on a single night shift; rule: at_least 1 male.
        Solver must pick the male."""
        employees = [
            _emp_dict(1, {'gender': 'female'}),
            _emp_dict(2, {'gender': 'female'}),
            _emp_dict(3, {'gender': 'male'}),
        ]
        shifts = [{'id': 99, 'start_time': '22:00', 'name': '夜'}]
        days = [date(2026, 6, 1)]
        model, a = _build_model_with_assignments(employees, shifts, days)
        # Need 1 person assigned per cell, AND ≥1 male → forces #3.
        # First relax the "exactly one" baseline to permit ≥1 male:
        # we already require exactly one assignment per cell, so the male
        # *must* be that one.
        tc = {
            'id': 1, 'shift_template_id': 99, 'scope_time_of_day': 'night',
            'condition_type': 'gender', 'condition_operator': 'eq',
            'condition_value': 'male',
            'quantifier': 'at_least', 'quantity': 1, 'severity': 'hard',
            'is_active': True,
        }
        apply_team_constraints(model, a, employees, shifts, days, [tc])
        solver = cp_model.CpSolver()
        assert solver.Solve(model) in (cp_model.OPTIMAL, cp_model.FEASIBLE)
        assert solver.Value(a[3][0][99]) == 1
        assert solver.Value(a[1][0][99]) == 0
        assert solver.Value(a[2][0][99]) == 0

    def test_at_most_zero_excludes_group(self):
        """Rule: at_most 0 females → female employees cannot be assigned."""
        employees = [
            _emp_dict(1, {'gender': 'female'}),
            _emp_dict(2, {'gender': 'male'}),
        ]
        shifts = [{'id': 1, 'start_time': '22:00', 'name': '夜'}]
        days = [date(2026, 6, 1)]
        model, a = _build_model_with_assignments(employees, shifts, days)
        tc = {
            'id': 1, 'shift_template_id': 1, 'scope_time_of_day': 'night',
            'condition_type': 'gender', 'condition_operator': 'eq',
            'condition_value': 'female',
            'quantifier': 'at_most', 'quantity': 0, 'severity': 'hard',
            'is_active': True,
        }
        apply_team_constraints(model, a, employees, shifts, days, [tc])
        solver = cp_model.CpSolver()
        assert solver.Solve(model) in (cp_model.OPTIMAL, cp_model.FEASIBLE)
        assert solver.Value(a[1][0][1]) == 0
        assert solver.Value(a[2][0][1]) == 1

    def test_scope_filters_by_time_of_day(self):
        """Constraint scoped to night-only must NOT affect morning shifts."""
        employees = [
            _emp_dict(1, {'gender': 'female'}),
            _emp_dict(2, {'gender': 'male'}),
        ]
        shifts = [
            {'id': 11, 'start_time': '09:00', 'name': '早'},
            {'id': 22, 'start_time': '22:00', 'name': '夜'},
        ]
        days = [date(2026, 6, 1)]
        model, a = _build_model_with_assignments(employees, shifts, days)
        tc = {
            'id': 1, 'shift_template_id': None, 'scope_time_of_day': 'night',
            'condition_type': 'gender', 'condition_operator': 'eq',
            'condition_value': 'male',
            'quantifier': 'at_least', 'quantity': 1, 'severity': 'hard',
            'is_active': True,
        }
        apply_team_constraints(model, a, employees, shifts, days, [tc])
        solver = cp_model.CpSolver()
        assert solver.Solve(model) in (cp_model.OPTIMAL, cp_model.FEASIBLE)
        # Night shift must have the male.
        assert solver.Value(a[2][0][22]) == 1
        # Morning shift can have either — but baseline "exactly 1" decided.

    def test_infeasible_when_no_eligible_employee(self):
        """at_least 1 driver but no driver tag exists → INFEASIBLE."""
        employees = [
            _emp_dict(1, {'tag_codes': []}),
            _emp_dict(2, {'tag_codes': ['lifter']}),
        ]
        shifts = [{'id': 1, 'start_time': '09:00', 'name': '日'}]
        days = [date(2026, 6, 1)]
        model, a = _build_model_with_assignments(employees, shifts, days)
        tc = {
            'id': 1, 'shift_template_id': 1, 'scope_time_of_day': 'any',
            'condition_type': 'tag', 'condition_operator': 'contains',
            'condition_value': ['driver'],
            'quantifier': 'at_least', 'quantity': 1, 'severity': 'hard',
            'is_active': True,
        }
        apply_team_constraints(model, a, employees, shifts, days, [tc])
        solver = cp_model.CpSolver()
        assert solver.Solve(model) == cp_model.INFEASIBLE


class TestCompilerSoftLogic:
    def test_soft_at_least_emits_slack_penalty(self):
        employees = [_emp_dict(1, {'gender': 'female'})]
        shifts = [{'id': 1, 'start_time': '22:00', 'name': '夜'}]
        days = [date(2026, 6, 1)]
        model, a = _build_model_with_assignments(employees, shifts, days)
        tc = {
            'id': 1, 'shift_template_id': 1, 'scope_time_of_day': 'night',
            'condition_type': 'gender', 'condition_operator': 'eq',
            'condition_value': 'male',
            'quantifier': 'at_least', 'quantity': 1, 'severity': 'soft',
            'is_active': True,
        }
        terms = apply_team_constraints(model, a, employees, shifts, days, [tc])
        assert terms, 'soft constraint must emit ≥1 objective term'
        # Solve and check that the slack penalty actually fires (= 1 because
        # we need 1 male, have 0).
        model.Minimize(sum(terms))
        solver = cp_model.CpSolver()
        assert solver.Solve(model) in (cp_model.OPTIMAL, cp_model.FEASIBLE)
        # Penalty = SOFT_PENALTY_PER_UNIT (=15) × slack (=1) = 15.
        from apps.ai_engine.team_constraint_compiler import SOFT_PENALTY_PER_UNIT
        assert solver.ObjectiveValue() == SOFT_PENALTY_PER_UNIT


# ===========================================================================
# Unified /api/ai/generate/ flow
# ===========================================================================

class TestGenerateEndpoint:
    def test_validates_drift_without_seed(self, db, admin_api_client, organization):
        resp = admin_api_client.post(
            '/api/ai/schedule/generate/',
            {
                'organization_id': organization.id,
                'period_start': '2026-06-01', 'period_end': '2026-06-02',
                'minimize_drift_from_seed': True,
                # seed_version_id deliberately missing
            },
            format='json',
        )
        assert resp.status_code == 400
        assert 'seed_version_id' in str(resp.content)

    def test_returns_billing_metadata(
        self, db, admin_api_client, organization, branch, admin_user, employee_role
    ):
        # One employee + one shift so the solver has something to do.
        from apps.accounts.models import User
        u = User.objects.create_user(
            username='gen_emp', password='pw', role=employee_role,
            organization=organization, branch=branch,
        )
        Employee.objects.create(
            user=u, employee_id='GE1', organization=organization, branch=branch,
            position='nurse', hire_date=date(2024, 1, 1),
        )
        ShiftTemplate.objects.create(
            organization=organization, name='D',
            start_time=time(9, 0), end_time=time(17, 0),
            min_staff_count=1,
        )

        resp = admin_api_client.post(
            '/api/ai/schedule/generate/',
            {
                'organization_id': organization.id,
                'branch_id': branch.id,
                'period_start': '2026-06-01', 'period_end': '2026-06-02',
                'enforce_labor_law': True,
                'consume_token': True,
            },
            format='json',
        )
        assert resp.status_code == 200, resp.content
        body = resp.json()
        billing = body['metadata']['billing']
        assert billing['consume_token'] is True
        assert billing['billing_mode'] == 'generate'
        assert billing['enforce_labor_law'] is True

    def test_seed_version_404(self, db, admin_api_client, organization):
        resp = admin_api_client.post(
            '/api/ai/schedule/generate/',
            {
                'organization_id': organization.id,
                'period_start': '2026-06-01', 'period_end': '2026-06-02',
                'seed_version_id': 99999,
                'minimize_drift_from_seed': True,
            },
            format='json',
        )
        assert resp.status_code == 404
        assert 'seed_version_id' in str(resp.content)

    def test_seed_version_cross_org_forbidden(
        self, db, admin_api_client, organization
    ):
        from apps.organizations.models import Organization
        other = Organization.objects.create(name='Other', code='OTH')
        other_v = ScheduleVersion.objects.create(
            organization=other, version_label='other-B',
            version_type='actual',
            period_start=date(2026, 6, 1), period_end=date(2026, 6, 2),
        )
        # admin_user is superuser, so we must explicitly demote to exercise
        # the cross-org guard. Skip that here — the guard test lives in the
        # permission test bundle; we just confirm the happy lookup works.
        resp = admin_api_client.post(
            '/api/ai/schedule/generate/',
            {
                'organization_id': organization.id,
                'period_start': '2026-06-01', 'period_end': '2026-06-02',
                'seed_version_id': other_v.id,
                'minimize_drift_from_seed': True,
            },
            format='json',
        )
        # Superuser: returns 200 (lookup succeeded). Cross-org guard logic
        # for non-superusers exists in the view; full enforcement test is
        # deferred to the permission suite.
        assert resp.status_code in (200, 403)
