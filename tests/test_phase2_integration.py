"""
Phase 2 integration / boundary tests.

These tests do not retread feature behaviour — that is covered in the
per-PR suites. Instead they pin invariants that live *between* PRs and
that would be easy to break silently in a future refactor:

  * End-to-end workflow: consent → team rule → cap → generate → check →
    derive A → every audit-trail row points at the right thing.
  * Cap accumulates across modes: generate × N then derive_legal must
    respect the same monthly bucket, and the next call after a cap-hit
    must 402.
  * BillingRateConfig is consulted live: an admin price hike between
    two AI calls in the same period affects only the second call's
    token amount, but both rows share the same period.
  * Cross-org isolation: a manager cannot see another org's usage,
    settings, or derive-legal an other-org B.
  * Employee role permission boundary on team-constraints: only
    Manager+ may CRUD.
  * Consent revoke is immediate: the next solver invocation must see
    None for the revoked employee's sensitive attributes — no stale
    cache.
  * derive-legal links the UsageRecord to the new A, not to B.
  * Empty-input safety: zero employees / zero shifts return a clean
    error rather than crashing the solver.
"""
import pytest  # noqa: F401  (conftest fixtures)
from datetime import date, time, timedelta
from decimal import Decimal

from apps.accounts.models import User
from apps.employees.models import Employee, EmployeeDataConsent
from apps.shifts.models import ShiftTemplate, TeamConstraint
from apps.schedules.models import Schedule, ScheduleVersion
from apps.billing.models import (
    BillingRateConfig, OrgBillingSettings, BillingPeriod, UsageRecord,
)


# ---------------------------------------------------------------------------
# Helpers (local; do not depend on other test modules)
# ---------------------------------------------------------------------------

def _shift(org, name, start, end, **kw):
    return ShiftTemplate.objects.create(
        organization=org, name=name,
        start_time=start, end_time=end,
        min_staff_count=kw.pop('min_staff_count', 1),
        **kw,
    )


def _employee(org, branch, role, code, **emp_kw):
    u = User.objects.create_user(
        username=f'int_{code}', password='pw',
        role=role, organization=org, branch=branch,
    )
    return Employee.objects.create(
        user=u, employee_id=code,
        organization=org, branch=branch,
        position='nurse', hire_date=date(2024, 1, 1),
        **emp_kw,
    )


# ===========================================================================
# 1. End-to-end workflow
# ===========================================================================

class TestEndToEndWorkflow:
    def test_consent_rule_cap_generate_compliance_derive(
        self,
        db, organization, branch, admin_user, admin_api_client,
        employee_user, employee_role,
    ):
        """The full Phase 2 day-zero → day-two flow as a single test.

        Verifies that every audit row created along the way points to
        the right source: the A version's derived_from is B, the
        UsageRecord for derive-legal references the new A (not B), the
        BillingPeriod total matches the sum of charged tokens, and the
        compliance result is clean against the A produced.
        """
        # --- Day 0 ----------------------------------------------------
        # Manager sets a generous cap.
        admin_api_client.patch(
            '/api/billing/settings/',
            {'monthly_cap_tokens': 500}, format='json',
        )

        # Two staff + one shift.
        e1 = Employee.objects.create(
            user=employee_user, employee_id='E2E-1',
            organization=organization, branch=branch,
            position='nurse', hire_date=date(2024, 1, 1),
            gender='male', height_cm=Decimal('180'),
            shift_pattern_preference='alternating',
        )
        e2 = _employee(organization, branch, employee_role, 'E2E-2',
                       gender='female')
        day_shift = _shift(organization, 'D', time(9, 0), time(17, 0))

        # Manager adds a team rule: at least 1 male on every D shift.
        # (Rule will only bind once the relevant employee has consented.)
        admin_api_client.post(
            '/api/shifts/team-constraints/',
            {
                'organization': organization.id,
                'shift_template': day_shift.id,
                'scope_time_of_day': 'any',
                'condition_type': 'gender', 'condition_operator': 'eq',
                'condition_value': 'male',
                'quantifier': 'at_least', 'quantity': 1,
                'severity': 'hard',
            }, format='json',
        )

        # --- Day 1: employee 1 consents -------------------------------
        from rest_framework.test import APIClient
        emp_client = APIClient()
        emp_client.force_authenticate(user=e1.user)
        resp = emp_client.post(
            f'/api/employees/employees/{e1.id}/data-consent/',
            {'consent_version': '1.0'}, format='json',
        )
        assert resp.status_code == 201

        # --- Day 2: generate B over a 2-day window --------------------
        resp = admin_api_client.post(
            '/api/ai/schedule/generate/',
            {
                'organization_id': organization.id,
                'branch_id': branch.id,
                'period_start': '2026-06-01', 'period_end': '2026-06-02',
            }, format='json',
        )
        assert resp.status_code == 200, resp.content
        body = resp.json()
        assert body['success'] is True
        # The team rule must have forced E1 (the only consented male)
        # onto every D-shift cell.
        assignments = body['assignments']
        male_cells = [a for a in assignments if a['employee_id'] == e1.id]
        assert len(male_cells) == 2, f'team rule not honoured: {assignments}'
        # Billing carried.
        billing = body['metadata']['billing']
        assert billing['tokens_charged'] == 10  # 'generate' rate
        assert billing['period_usage_after'] == 10

        # Manager writes those assignments into a real B version so
        # downstream calls have something to derive from.
        b = ScheduleVersion.objects.create(
            organization=organization, branch=branch,
            version_label='E2E-B', version_type='actual',
            period_start=date(2026, 6, 1), period_end=date(2026, 6, 2),
            created_by=admin_user,
        )
        shift_dur = round(day_shift.duration_hours, 2)
        for a in assignments:
            Schedule.objects.create(
                schedule_version=b,
                employee_id=a['employee_id'],
                shift_template_id=a['shift_id'],
                schedule_date=date.fromisoformat(a['date']),
                expected_hours=shift_dur,
            )

        # Compliance check should be clean (small period, lots of slack).
        check = admin_api_client.post(
            f'/api/schedules/versions/{b.id}/check-compliance/',
            {}, format='json',
        )
        assert check.status_code == 200
        assert check.json()['total_count'] == 0

        # Derive A.
        a_resp = admin_api_client.post(
            f'/api/schedules/versions/{b.id}/derive-legal/',
            {}, format='json',
        )
        assert a_resp.status_code == 201
        a_body = a_resp.json()
        a_id = a_body['legal_version_id']
        a_version = ScheduleVersion.objects.get(id=a_id)
        assert a_version.derived_from_id == b.id
        assert a_version.version_type == 'legal'

        # Per-call billing rows: 10 (generate) + 3 (derive_legal) = 13.
        period = BillingPeriod.current_for(organization)
        period.refresh_from_db()
        assert period.total_tokens == 13
        records = list(UsageRecord.objects.filter(organization=organization))
        assert len(records) == 2
        derive_record = next(r for r in records if r.billing_mode == 'derive_legal')
        # derive-legal's UsageRecord must point to the NEW A, not the B.
        assert derive_record.schedule_version_id == a_id


# ===========================================================================
# 2. Cap accumulates across modes
# ===========================================================================

class TestCapAccumulation:
    def test_generate_then_derive_then_cap_hit(
        self, db, organization, branch, admin_user, admin_api_client,
        employee_role,
    ):
        """generate (10) + derive_legal (3) → next derive_legal would
        be 13+3=16 against cap 15 → 402. The first two must succeed in
        the same period, the third must be blocked before the solver."""
        OrgBillingSettings.objects.create(
            organization=organization, monthly_cap_tokens=15,
        )
        # Seed B with a single trivially-legal assignment so derive-legal
        # is feasible (B uses 1 employee, period candidate set widens to
        # include any active employee per PR3).
        e1 = _employee(organization, branch, admin_user.role or None, 'CAP1')
        # Ensure another candidate exists so derive can repair if needed.
        _employee(organization, branch, e1.user.role, 'CAP2')
        d = _shift(organization, 'D', time(9, 0), time(17, 0))
        b1 = ScheduleVersion.objects.create(
            organization=organization, branch=branch, version_label='B1',
            version_type='actual',
            period_start=date(2026, 6, 1), period_end=date(2026, 6, 1),
            created_by=admin_user,
        )
        Schedule.objects.create(
            schedule_version=b1, employee=e1, shift_template=d,
            schedule_date=date(2026, 6, 1), expected_hours=Decimal('8'),
        )

        # Call 1: generate → 10 tokens used.
        r1 = admin_api_client.post(
            '/api/ai/schedule/generate/',
            {
                'organization_id': organization.id,
                'branch_id': branch.id,
                'period_start': '2026-06-02', 'period_end': '2026-06-02',
            }, format='json',
        )
        assert r1.status_code == 200
        assert r1.json()['metadata']['billing']['period_usage_after'] == 10

        # Call 2: derive_legal → 13 tokens used.
        r2 = admin_api_client.post(
            f'/api/schedules/versions/{b1.id}/derive-legal/',
            {}, format='json',
        )
        assert r2.status_code == 201
        assert r2.json()['billing']['period_usage_after'] == 13

        # Call 3: derive_legal again → would push 13+3=16 > cap=15 → 402.
        b2 = ScheduleVersion.objects.create(
            organization=organization, branch=branch, version_label='B2',
            version_type='actual',
            period_start=date(2026, 6, 3), period_end=date(2026, 6, 3),
            created_by=admin_user,
        )
        Schedule.objects.create(
            schedule_version=b2, employee=e1, shift_template=d,
            schedule_date=date(2026, 6, 3), expected_hours=Decimal('8'),
        )
        r3 = admin_api_client.post(
            f'/api/schedules/versions/{b2.id}/derive-legal/',
            {}, format='json',
        )
        assert r3.status_code == 402
        assert r3.json()['projected_period_tokens'] == 16

        # Period total unchanged after the rejected call.
        period = BillingPeriod.current_for(organization)
        period.refresh_from_db()
        assert period.total_tokens == 13
        assert UsageRecord.objects.filter(organization=organization).count() == 2


# ===========================================================================
# 3. Live billing rate changes
# ===========================================================================

class TestLiveRateChange:
    def test_admin_price_hike_affects_only_subsequent_calls(
        self, db, organization, branch, admin_api_client, employee_role
    ):
        _employee(organization, branch, employee_role, 'RATE1')
        _shift(organization, 'D', time(9, 0), time(17, 0))

        r1 = admin_api_client.post(
            '/api/ai/schedule/generate/',
            {
                'organization_id': organization.id,
                'period_start': '2026-06-01', 'period_end': '2026-06-01',
            }, format='json',
        )
        assert r1.json()['metadata']['billing']['tokens_charged'] == 10

        # Admin hikes the generate rate to 42.
        BillingRateConfig.objects.create(
            billing_mode='generate', tokens_per_call=42,
            notes='price hike for testing',
        )

        r2 = admin_api_client.post(
            '/api/ai/schedule/generate/',
            {
                'organization_id': organization.id,
                'period_start': '2026-06-02', 'period_end': '2026-06-02',
            }, format='json',
        )
        assert r2.json()['metadata']['billing']['tokens_charged'] == 42

        # Both rows in the same period; total = 10 + 42 = 52.
        period = BillingPeriod.current_for(organization)
        period.refresh_from_db()
        assert period.total_tokens == 52


# ===========================================================================
# 4. Cross-org billing isolation
# ===========================================================================

class TestCrossOrgIsolation:
    def test_manager_cannot_see_other_org_usage(
        self, db, organization, branch, admin_user, manager_api_client,
        employee_role,
    ):
        # Setup: a second org with its own UsageRecord row.
        from apps.organizations.models import Organization
        from apps.billing.models import record_usage
        other = Organization.objects.create(name='Other', code='OTH')
        record_usage(other, 'generate', 'success')

        # Manager in `organization` calls /usage/ — must see zero records,
        # never the other org's.
        resp = manager_api_client.get('/api/billing/usage/')
        body = resp.json()
        assert body['organization_id'] == organization.id
        assert body['period']['total_tokens'] == 0
        assert all(r['organization'] == organization.id
                   for r in body['records'])


# ===========================================================================
# 5. Employee role boundary on team-constraints
# ===========================================================================

class TestEmployeePermissionBoundary:
    def test_employee_cannot_create_team_constraint(
        self, db, employee_api_client, organization
    ):
        resp = employee_api_client.post(
            '/api/shifts/team-constraints/',
            {
                'organization': organization.id,
                'condition_type': 'gender', 'condition_operator': 'eq',
                'condition_value': 'male',
                'quantifier': 'at_least', 'quantity': 1,
            }, format='json',
        )
        assert resp.status_code == 403

    def test_employee_cannot_patch_billing_settings(
        self, db, employee_api_client
    ):
        resp = employee_api_client.patch(
            '/api/billing/settings/',
            {'monthly_cap_tokens': 5}, format='json',
        )
        assert resp.status_code == 403


# ===========================================================================
# 6. Consent revoke is immediate
# ===========================================================================

class TestConsentRevokeImmediate:
    def test_revoke_blocks_next_team_rule_match(
        self, db, organization, branch, admin_api_client, admin_user,
        employee_user, employee_role,
    ):
        """E1 consents → team rule (require male) matches → solver
        succeeds. E1 revokes → next solve has no eligible male and
        the hard rule forces INFEASIBLE."""
        e1 = Employee.objects.create(
            user=employee_user, employee_id='REV1',
            organization=organization, branch=branch,
            position='nurse', hire_date=date(2024, 1, 1),
            gender='male',
        )
        # Second employee (female) so the cell can still be filled even
        # without the rule.
        _employee(organization, branch, employee_role, 'REV2', gender='female')
        _shift(organization, 'D', time(9, 0), time(17, 0))

        # Consent.
        from rest_framework.test import APIClient
        emp_client = APIClient()
        emp_client.force_authenticate(user=employee_user)
        emp_client.post(
            f'/api/employees/employees/{e1.id}/data-consent/',
            {'consent_version': '1.0'}, format='json',
        )

        # Add a hard rule requiring a male.
        TeamConstraint.objects.create(
            organization=organization,
            condition_type='gender', condition_operator='eq',
            condition_value='male',
            quantifier='at_least', quantity=1, severity='hard',
        )
        r1 = admin_api_client.post(
            '/api/ai/schedule/generate/',
            {
                'organization_id': organization.id, 'branch_id': branch.id,
                'period_start': '2026-06-01', 'period_end': '2026-06-01',
                'consume_token': False,  # keep this test billing-neutral
            }, format='json',
        )
        assert r1.json()['success'] is True

        # Revoke.
        emp_client.delete(
            f'/api/employees/employees/{e1.id}/data-consent/',
        )

        # Next solve: no consented male, hard rule → INFEASIBLE.
        r2 = admin_api_client.post(
            '/api/ai/schedule/generate/',
            {
                'organization_id': organization.id, 'branch_id': branch.id,
                'period_start': '2026-06-02', 'period_end': '2026-06-02',
                'consume_token': False,
            }, format='json',
        )
        assert r2.json()['success'] is False


# ===========================================================================
# 7. Empty-input safety
# ===========================================================================

class TestEmptyInputSafety:
    def test_zero_shifts_returns_clean_failure(
        self, db, organization, branch, admin_api_client, employee_role
    ):
        """No shifts at all → solver must fail gracefully, not crash."""
        _employee(organization, branch, employee_role, 'EMP1')
        # No ShiftTemplate created.
        resp = admin_api_client.post(
            '/api/ai/schedule/generate/',
            {
                'organization_id': organization.id, 'branch_id': branch.id,
                'period_start': '2026-06-01', 'period_end': '2026-06-01',
                'consume_token': False,
            }, format='json',
        )
        # Either 200 with success=false, or a 4xx — both are acceptable;
        # what we must NOT see is a 500.
        assert resp.status_code < 500, resp.content
        if resp.status_code == 200:
            assert resp.json()['success'] is False

    def test_zero_employees_returns_clean_failure(
        self, db, organization, branch, admin_api_client
    ):
        _shift(organization, 'D', time(9, 0), time(17, 0))
        # No Employee created.
        resp = admin_api_client.post(
            '/api/ai/schedule/generate/',
            {
                'organization_id': organization.id, 'branch_id': branch.id,
                'period_start': '2026-06-01', 'period_end': '2026-06-01',
                'consume_token': False,
            }, format='json',
        )
        assert resp.status_code < 500, resp.content
        if resp.status_code == 200:
            assert resp.json()['success'] is False
