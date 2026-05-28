"""
Phase 3 PR11 — soft vs hard labour-law rule severity.

A rule type listed in OrgComplianceSettings.soft_rule_types is:
  * still reported by the one-click compliance check, but labelled
    severity='soft' (yellow reminder, not a red blocker) — the customer
    wants every labour-law hit shown;
  * relaxed in derive-legal from a hard CP-SAT constraint to a heavy
    objective penalty, so the solver avoids it but won't go INFEASIBLE
    when respecting it is impossible.

Coverage:

  TestComplianceSeverityLabelling   engine labels soft vs hard correctly
  TestSettingsEndpoint              GET/PATCH /api/compliance/settings/
  TestCheckComplianceSeverity       endpoint reflects org soft config
  TestDeriveLegalSoftRelaxation     a soft rule lets derive-legal succeed
                                    where a hard rule would be INFEASIBLE
"""
import pytest  # noqa: F401  (conftest fixtures)
from datetime import date, time, timedelta
from decimal import Decimal

from apps.accounts.models import User
from apps.employees.models import Employee
from apps.shifts.models import ShiftTemplate
from apps.schedules.models import Schedule, ScheduleVersion
from apps.compliance.models import OrgComplianceSettings
from apps.compliance.engine import check_schedule_violations


def _shift(org, name, start, end, **kw):
    return ShiftTemplate.objects.create(
        organization=org, name=name, start_time=start, end_time=end,
        min_staff_count=kw.pop('min_staff_count', 1), **kw,
    )


def _employee(org, branch, role, code):
    u = User.objects.create_user(
        username=f's_{code}', password='pw',
        role=role, organization=org, branch=branch,
    )
    return Employee.objects.create(
        user=u, employee_id=code, organization=org, branch=branch,
        position='nurse', hire_date=date(2024, 1, 1),
    )


def _version(org, branch, user, label='V'):
    return ScheduleVersion.objects.create(
        organization=org, branch=branch, version_label=label,
        version_type='actual',
        period_start=date(2026, 6, 1), period_end=date(2026, 6, 14),
        created_by=user,
    )


# ===========================================================================
# Engine-level severity labelling
# ===========================================================================

class TestComplianceSeverityLabelling:
    def test_default_all_hard(self, db, organization, branch, admin_user, employee_role):
        emp = _employee(organization, branch, employee_role, 'SL1')
        night = _shift(organization, 'N', time(22, 0), time(6, 0))
        morning = _shift(organization, 'M', time(8, 0), time(16, 0))
        v = _version(organization, branch, admin_user)
        Schedule.objects.create(
            schedule_version=v, employee=emp, shift_template=night,
            schedule_date=date(2026, 6, 1), expected_hours=Decimal('8'),
        )
        Schedule.objects.create(
            schedule_version=v, employee=emp, shift_template=morning,
            schedule_date=date(2026, 6, 2), expected_hours=Decimal('8'),
        )
        violations = check_schedule_violations(v)  # no soft config
        assert violations
        assert all(x.severity == 'hard' for x in violations)

    def test_soft_rule_labelled_soft(self, db, organization, branch, admin_user, employee_role):
        emp = _employee(organization, branch, employee_role, 'SL2')
        night = _shift(organization, 'N', time(22, 0), time(6, 0))
        morning = _shift(organization, 'M', time(8, 0), time(16, 0))
        v = _version(organization, branch, admin_user)
        Schedule.objects.create(
            schedule_version=v, employee=emp, shift_template=night,
            schedule_date=date(2026, 6, 1), expected_hours=Decimal('8'),
        )
        Schedule.objects.create(
            schedule_version=v, employee=emp, shift_template=morning,
            schedule_date=date(2026, 6, 2), expected_hours=Decimal('8'),
        )
        violations = check_schedule_violations(
            v, soft_rule_types=['min_rest_hours'],
        )
        rest = [x for x in violations if x.rule == 'min_rest_hours']
        assert rest and all(x.severity == 'soft' for x in rest)

    def test_soft_does_not_suppress_reporting(self, db, organization, branch, admin_user, employee_role):
        """Marking a rule soft must NOT drop the violation — only relabel it."""
        emp = _employee(organization, branch, employee_role, 'SL3')
        night = _shift(organization, 'N', time(22, 0), time(6, 0))
        morning = _shift(organization, 'M', time(8, 0), time(16, 0))
        v = _version(organization, branch, admin_user)
        Schedule.objects.create(
            schedule_version=v, employee=emp, shift_template=night,
            schedule_date=date(2026, 6, 1), expected_hours=Decimal('8'),
        )
        Schedule.objects.create(
            schedule_version=v, employee=emp, shift_template=morning,
            schedule_date=date(2026, 6, 2), expected_hours=Decimal('8'),
        )
        hard_count = len(check_schedule_violations(v))
        soft_count = len(check_schedule_violations(v, soft_rule_types=['min_rest_hours']))
        assert hard_count == soft_count  # same number reported, just relabelled


# ===========================================================================
# Settings endpoint
# ===========================================================================

class TestSettingsEndpoint:
    def test_get_creates_default_empty(self, db, manager_api_client):
        resp = manager_api_client.get('/api/compliance/settings/')
        assert resp.status_code == 200
        assert resp.json()['soft_rule_types'] == []

    def test_patch_sets_soft_rules(self, db, manager_api_client):
        resp = manager_api_client.patch(
            '/api/compliance/settings/',
            {'soft_rule_types': ['max_weekly_hours', 'min_rest_hours']},
            format='json',
        )
        assert resp.status_code == 200
        assert set(resp.json()['soft_rule_types']) == {'max_weekly_hours', 'min_rest_hours'}

    def test_patch_rejects_unknown_rule(self, db, manager_api_client):
        resp = manager_api_client.patch(
            '/api/compliance/settings/',
            {'soft_rule_types': ['not_a_rule']}, format='json',
        )
        assert resp.status_code == 400
        assert 'soft_rule_types' in resp.json()

    def test_employee_blocked(self, db, employee_api_client):
        resp = employee_api_client.get('/api/compliance/settings/')
        assert resp.status_code == 403


# ===========================================================================
# check-compliance endpoint reflects org soft config
# ===========================================================================

class TestCheckComplianceSeverity:
    def test_endpoint_uses_org_soft_config(
        self, db, organization, branch, admin_user, admin_api_client, employee_role
    ):
        OrgComplianceSettings.objects.create(
            organization=organization, soft_rule_types=['min_rest_hours'],
        )
        emp = _employee(organization, branch, employee_role, 'CE1')
        night = _shift(organization, 'N', time(22, 0), time(6, 0))
        morning = _shift(organization, 'M', time(8, 0), time(16, 0))
        v = _version(organization, branch, admin_user)
        Schedule.objects.create(
            schedule_version=v, employee=emp, shift_template=night,
            schedule_date=date(2026, 6, 1), expected_hours=Decimal('8'),
        )
        Schedule.objects.create(
            schedule_version=v, employee=emp, shift_template=morning,
            schedule_date=date(2026, 6, 2), expected_hours=Decimal('8'),
        )
        resp = admin_api_client.post(
            f'/api/schedules/versions/{v.id}/check-compliance/', {}, format='json',
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body['soft_rule_types'] == ['min_rest_hours']
        rest = [x for x in body['violations'] if x['rule'] == 'min_rest_hours']
        assert rest and all(x['severity'] == 'soft' for x in rest)

    def test_body_override_wins_over_org_config(
        self, db, organization, branch, admin_user, admin_api_client, employee_role
    ):
        OrgComplianceSettings.objects.create(
            organization=organization, soft_rule_types=[],
        )
        emp = _employee(organization, branch, employee_role, 'CE2')
        night = _shift(organization, 'N', time(22, 0), time(6, 0))
        morning = _shift(organization, 'M', time(8, 0), time(16, 0))
        v = _version(organization, branch, admin_user)
        Schedule.objects.create(
            schedule_version=v, employee=emp, shift_template=night,
            schedule_date=date(2026, 6, 1), expected_hours=Decimal('8'),
        )
        Schedule.objects.create(
            schedule_version=v, employee=emp, shift_template=morning,
            schedule_date=date(2026, 6, 2), expected_hours=Decimal('8'),
        )
        resp = admin_api_client.post(
            f'/api/schedules/versions/{v.id}/check-compliance/',
            {'soft_rule_types': ['min_rest_hours']}, format='json',
        )
        rest = [x for x in resp.json()['violations'] if x['rule'] == 'min_rest_hours']
        assert rest and all(x['severity'] == 'soft' for x in rest)


# ===========================================================================
# derive-legal relaxation: soft rule avoids INFEASIBLE
# ===========================================================================

class TestDeriveLegalSoftRelaxation:
    def _single_employee_overworked_b(self, org, branch, user, employee_role):
        """One employee on 7 straight days; only that one employee exists."""
        e1 = _employee(org, branch, employee_role, 'SOFT1')
        day = _shift(org, 'D', time(9, 0), time(17, 0))
        b = ScheduleVersion.objects.create(
            organization=org, branch=branch, version_label='B',
            version_type='actual',
            period_start=date(2026, 6, 1), period_end=date(2026, 6, 7),
            created_by=user,
        )
        for i in range(7):
            Schedule.objects.create(
                schedule_version=b, employee=e1, shift_template=day,
                schedule_date=date(2026, 6, 1) + timedelta(days=i),
                expected_hours=Decimal('8'),
            )
        return b

    def test_hard_consecutive_is_infeasible(
        self, db, organization, branch, admin_user, admin_api_client, employee_role
    ):
        """Baseline: single employee, cap=3 hard → INFEASIBLE (409)."""
        b = self._single_employee_overworked_b(
            organization, branch, admin_user, employee_role
        )
        resp = admin_api_client.post(
            f'/api/schedules/versions/{b.id}/derive-legal/',
            {'consume_token': False,
             'constraints': {'max_consecutive_days': 3, 'max_weekly_hours': 999,
                             'min_rest_hours': 0}},
            format='json',
        )
        assert resp.status_code == 409  # infeasible

    def test_soft_consecutive_succeeds(
        self, db, organization, branch, admin_user, admin_api_client, employee_role
    ):
        """Same scenario but max_consecutive_days marked soft → the solver
        produces an A (keeping the employee working) instead of failing."""
        b = self._single_employee_overworked_b(
            organization, branch, admin_user, employee_role
        )
        resp = admin_api_client.post(
            f'/api/schedules/versions/{b.id}/derive-legal/',
            {'consume_token': False,
             'soft_rule_types': ['max_consecutive_days'],
             'constraints': {'max_consecutive_days': 3, 'max_weekly_hours': 999,
                             'min_rest_hours': 0}},
            format='json',
        )
        assert resp.status_code == 201, resp.content
        # A still covers all 7 days (the single employee had to keep working
        # because there is no one else — the soft rule permitted it).
        a_id = resp.json()['legal_version_id']
        assert Schedule.objects.filter(schedule_version_id=a_id).count() == 7
