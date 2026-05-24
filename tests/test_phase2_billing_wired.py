"""
Phase 2 PR8 — billing wired into AI generate + derive-legal.

Two endpoints now pre-flight the monthly cap and post-debit a
UsageRecord regardless of solver outcome (the customer's "先扱不退"
rule):

  POST /api/ai/schedule/generate/
  POST /api/schedules/versions/{B}/derive-legal/

Tests assert four behaviours per endpoint:

  1. Happy path → UsageRecord created with the right billing_mode and
     solver_status='success'; response carries `tokens_charged` and
     `period_usage_after`.
  2. Over-cap → 402 Payment Required before the solver runs (no
     UsageRecord), with the projected/cap pair returned so the frontend
     can suggest a top-up.
  3. INFEASIBLE → still charged (pre-debit), UsageRecord stamped
     `solver_status='infeasible'`.
  4. `consume_token=false` → no UsageRecord, no cap check (dry run).
"""
import pytest  # noqa: F401  (conftest fixtures)
from datetime import date, time, timedelta
from decimal import Decimal

from apps.employees.models import Employee
from apps.shifts.models import ShiftTemplate
from apps.schedules.models import Schedule, ScheduleVersion
from apps.billing.models import (
    OrgBillingSettings, UsageRecord, BillingPeriod,
)


# ---------------------------------------------------------------------------
# Helpers — keep fixtures lightweight so OR-Tools finishes sub-second.
# ---------------------------------------------------------------------------

def _shift(org, name, start, end, **kw):
    return ShiftTemplate.objects.create(
        organization=org, name=name,
        start_time=start, end_time=end,
        min_staff_count=kw.pop('min_staff_count', 1),
        **kw,
    )


def _make_employee(org, branch, role, code):
    from apps.accounts.models import User
    u = User.objects.create_user(
        username=f'emp_b_{code}', password='pw',
        role=role, organization=org, branch=branch,
    )
    return Employee.objects.create(
        user=u, employee_id=code,
        organization=org, branch=branch,
        position='nurse', hire_date=date(2024, 1, 1),
    )


def _b_version_with_seed(org, branch, admin_user, role, days, max_streak_ok=True):
    """B version with `days` consecutive single-employee shifts; if
    max_streak_ok=False the streak length exceeds default cap=6."""
    e1 = _make_employee(org, branch, role, 'B1')
    _make_employee(org, branch, role, 'B2')  # candidate for repair
    day = _shift(org, 'D', time(9, 0), time(17, 0))
    v = ScheduleVersion.objects.create(
        organization=org, branch=branch, version_label='B',
        version_type='actual',
        period_start=date(2026, 6, 1),
        period_end=date(2026, 6, 1) + timedelta(days=days - 1),
        created_by=admin_user,
    )
    for i in range(days):
        Schedule.objects.create(
            schedule_version=v, employee=e1, shift_template=day,
            schedule_date=date(2026, 6, 1) + timedelta(days=i),
            expected_hours=Decimal('8'),
        )
    return v


# ===========================================================================
# /api/ai/schedule/generate/  ←  billing pre-flight + post-record
# ===========================================================================

class TestGenerateBillingHappyPath:
    def test_charges_on_success(
        self, db, admin_api_client, organization, branch, employee_role
    ):
        _make_employee(organization, branch, employee_role, 'G1')
        _shift(organization, 'D', time(9, 0), time(17, 0))

        resp = admin_api_client.post(
            '/api/ai/schedule/generate/',
            {
                'organization_id': organization.id,
                'branch_id': branch.id,
                'period_start': '2026-06-01', 'period_end': '2026-06-02',
                'consume_token': True,
            },
            format='json',
        )
        assert resp.status_code == 200, resp.content
        billing = resp.json()['metadata']['billing']
        assert billing['billing_mode'] == 'generate'
        assert billing['tokens_charged'] == 10  # seeded rate
        assert billing['period_usage_after'] == 10

        # UsageRecord row exists, stamped success.
        records = UsageRecord.objects.filter(organization=organization)
        assert records.count() == 1
        assert records.first().solver_status == 'success'
        assert records.first().billing_mode == 'generate'

    def test_charges_on_infeasible(
        self, db, admin_api_client, organization, branch, employee_role
    ):
        """Force INFEASIBLE: shift requires a cert nobody has → still charged."""
        from apps.employees.models import Certification
        cert = Certification.objects.create(name='ACLS', code='ACLS')
        _make_employee(organization, branch, employee_role, 'INF1')
        shift = _shift(organization, 'D', time(9, 0), time(17, 0))
        shift.required_certifications.add(cert)

        resp = admin_api_client.post(
            '/api/ai/schedule/generate/',
            {
                'organization_id': organization.id,
                'branch_id': branch.id,
                'period_start': '2026-06-01', 'period_end': '2026-06-01',
                'consume_token': True,
            },
            format='json',
        )
        # OR-Tools returns success=False, view returns 200 with success=false
        # in body — this is the historical contract and we keep it.
        body = resp.json()
        assert body['success'] is False
        billing = body['metadata']['billing']
        assert billing['tokens_charged'] == 10  # pre-debit per "先扱不退"
        rec = UsageRecord.objects.filter(organization=organization).first()
        assert rec.solver_status == 'infeasible'


class TestGenerateBillingCapEnforcement:
    def test_blocks_when_cap_would_be_exceeded(
        self, db, admin_api_client, organization, branch, employee_role
    ):
        OrgBillingSettings.objects.create(
            organization=organization, monthly_cap_tokens=5,
        )
        _make_employee(organization, branch, employee_role, 'CAP1')
        _shift(organization, 'D', time(9, 0), time(17, 0))

        resp = admin_api_client.post(
            '/api/ai/schedule/generate/',
            {
                'organization_id': organization.id,
                'period_start': '2026-06-01', 'period_end': '2026-06-02',
                'consume_token': True,
            },
            format='json',
        )
        assert resp.status_code == 402
        body = resp.json()
        assert body['billing_mode'] == 'generate'
        assert body['projected_period_tokens'] == 10
        assert body['monthly_cap_tokens'] == 5
        # No record written — solver never ran.
        assert UsageRecord.objects.filter(organization=organization).count() == 0

    def test_blocks_when_billing_disabled(
        self, db, admin_api_client, organization, branch, employee_role
    ):
        OrgBillingSettings.objects.create(
            organization=organization, is_billing_enabled=False,
        )
        _make_employee(organization, branch, employee_role, 'OFF1')
        _shift(organization, 'D', time(9, 0), time(17, 0))

        resp = admin_api_client.post(
            '/api/ai/schedule/generate/',
            {
                'organization_id': organization.id,
                'period_start': '2026-06-01', 'period_end': '2026-06-01',
                'consume_token': True,
            },
            format='json',
        )
        assert resp.status_code == 402
        assert 'billing is disabled' in resp.json()['error']

    def test_consume_token_false_bypasses_cap(
        self, db, admin_api_client, organization, branch, employee_role
    ):
        OrgBillingSettings.objects.create(
            organization=organization, monthly_cap_tokens=0,
        )
        _make_employee(organization, branch, employee_role, 'DRY1')
        _shift(organization, 'D', time(9, 0), time(17, 0))

        resp = admin_api_client.post(
            '/api/ai/schedule/generate/',
            {
                'organization_id': organization.id,
                'period_start': '2026-06-01', 'period_end': '2026-06-01',
                'consume_token': False,
            },
            format='json',
        )
        # Cap should be ignored when consume_token=false.
        assert resp.status_code == 200, resp.content
        billing = resp.json()['metadata']['billing']
        # No tokens_charged because nothing was recorded.
        assert 'tokens_charged' not in billing
        assert UsageRecord.objects.filter(organization=organization).count() == 0


# ===========================================================================
# /api/schedules/versions/{B}/derive-legal/  ←  billing wired
# ===========================================================================

class TestDeriveLegalBilling:
    def test_charges_on_success(
        self, db, admin_api_client, organization, branch, admin_user, employee_role
    ):
        # 7 straight days → cap=6 forces a 1-cell move; both employees exist
        # so the solve is feasible.
        b = _b_version_with_seed(organization, branch, admin_user, employee_role, days=7)
        resp = admin_api_client.post(
            f'/api/schedules/versions/{b.id}/derive-legal/',
            {'today': '2026-06-01', 'time_decay_n': 14,
             'constraints': {'max_consecutive_days': 6, 'max_weekly_hours': 999,
                             'min_rest_hours': 0}},
            format='json',
        )
        assert resp.status_code == 201, resp.content
        billing = resp.json()['billing']
        assert billing['billing_mode'] == 'derive_legal'
        assert billing['tokens_charged'] == 3  # seeded rate
        rec = UsageRecord.objects.filter(organization=organization).first()
        assert rec is not None
        assert rec.solver_status == 'success'
        assert rec.billing_mode == 'derive_legal'
        assert rec.schedule_version_id == resp.json()['legal_version_id']

    def test_charges_on_infeasible(
        self, db, admin_api_client, organization, branch, admin_user, employee_role
    ):
        """Single employee + cap=3 over 7 days → infeasible. Customer still pays."""
        from apps.accounts.models import User
        u = User.objects.create_user(
            username='solo', password='pw', role=employee_role,
            organization=organization, branch=branch,
        )
        e1 = Employee.objects.create(
            user=u, employee_id='SOLO', organization=organization, branch=branch,
            position='nurse', hire_date=date(2024, 1, 1),
        )
        day = _shift(organization, 'D', time(9, 0), time(17, 0))
        b = ScheduleVersion.objects.create(
            organization=organization, branch=branch, version_label='B',
            version_type='actual',
            period_start=date(2026, 6, 1), period_end=date(2026, 6, 7),
            created_by=admin_user,
        )
        for i in range(7):
            Schedule.objects.create(
                schedule_version=b, employee=e1, shift_template=day,
                schedule_date=date(2026, 6, 1) + timedelta(days=i),
                expected_hours=Decimal('8'),
            )
        resp = admin_api_client.post(
            f'/api/schedules/versions/{b.id}/derive-legal/',
            {'constraints': {'max_consecutive_days': 3, 'max_weekly_hours': 999,
                             'min_rest_hours': 0}},
            format='json',
        )
        assert resp.status_code == 409  # infeasible
        body = resp.json()
        assert body['billing']['tokens_charged'] == 3  # pre-debit
        rec = UsageRecord.objects.filter(organization=organization).first()
        assert rec.solver_status == 'infeasible'

    def test_blocks_when_cap_exceeded(
        self, db, admin_api_client, organization, branch, admin_user, employee_role
    ):
        OrgBillingSettings.objects.create(
            organization=organization, monthly_cap_tokens=2,  # < 3 (derive_legal rate)
        )
        b = _b_version_with_seed(organization, branch, admin_user, employee_role, days=3)
        resp = admin_api_client.post(
            f'/api/schedules/versions/{b.id}/derive-legal/',
            {}, format='json',
        )
        assert resp.status_code == 402
        body = resp.json()
        assert body['monthly_cap_tokens'] == 2
        assert body['tokens_required'] == 3
        assert UsageRecord.objects.filter(organization=organization).count() == 0

    def test_consume_token_false_bypasses(
        self, db, admin_api_client, organization, branch, admin_user, employee_role
    ):
        OrgBillingSettings.objects.create(
            organization=organization, monthly_cap_tokens=0,
        )
        b = _b_version_with_seed(organization, branch, admin_user, employee_role, days=3)
        resp = admin_api_client.post(
            f'/api/schedules/versions/{b.id}/derive-legal/',
            {'consume_token': False}, format='json',
        )
        assert resp.status_code == 201, resp.content
        assert resp.json()['billing'] is None
        assert UsageRecord.objects.filter(organization=organization).count() == 0


# ===========================================================================
# Billing visibility: usage endpoint reflects what the AI calls wrote.
# ===========================================================================

class TestUsageReflectsAICharges:
    def test_total_after_two_calls(
        self, db, admin_api_client, organization, branch, employee_role
    ):
        _make_employee(organization, branch, employee_role, 'VIZ1')
        _shift(organization, 'D', time(9, 0), time(17, 0))
        for _ in range(2):
            admin_api_client.post(
                '/api/ai/schedule/generate/',
                {'organization_id': organization.id,
                 'period_start': '2026-06-01', 'period_end': '2026-06-01'},
                format='json',
            )
        resp = admin_api_client.get('/api/billing/usage/')
        body = resp.json()
        assert body['period']['total_tokens'] == 20  # 2 × 10
        assert len(body['records']) == 2
