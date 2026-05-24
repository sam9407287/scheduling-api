"""
Phase 1 compliance engine — per-cell violation list and the new
`POST /api/schedules/versions/{id}/check-compliance/` endpoint.

These tests target the *forward-facing* shape (the `Violation.to_dict()` keys
the frontend grid will consume). Backward-compatibility for legacy callers is
covered by tests/test_bugfixes.py — keep both green.

Uses global fixtures from conftest.py: organization, branch, admin_user,
admin_api_client, employee_user.
"""
import pytest
from datetime import date, time, timedelta
from decimal import Decimal

from apps.employees.models import Employee
from apps.shifts.models import ShiftTemplate
from apps.schedules.models import Schedule, ScheduleVersion
from apps.compliance.engine import (
    check_schedule_violations,
    summarize_by_rule,
)


@pytest.fixture
def employee(db, organization, branch, employee_user):
    return Employee.objects.create(
        user=employee_user, employee_id='EPC1',
        organization=organization, branch=branch,
        position='nurse', hire_date=date(2024, 1, 1),
    )


def _shift(org, name, start, end):
    return ShiftTemplate.objects.create(
        organization=org, name=name,
        start_time=start, end_time=end,
        min_staff_count=1,
    )


def _version(org, branch, user, label='V1'):
    return ScheduleVersion.objects.create(
        organization=org, branch=branch,
        version_label=label, version_type='actual',
        period_start=date(2026, 5, 25), period_end=date(2026, 6, 7),
        created_by=user,
    )


class TestPerCellViolationShape:
    """Each rule produces a violation pinned to one trigger cell."""

    def test_rest_interval_pins_trigger_to_next_shift(
        self, db, organization, branch, admin_user, employee
    ):
        night = _shift(organization, '深夜', time(22, 0), time(6, 0))
        morning = _shift(organization, '早班', time(8, 0), time(16, 0))
        version = _version(organization, branch, admin_user)
        Schedule.objects.create(
            schedule_version=version, employee=employee,
            shift_template=night, schedule_date=date(2026, 5, 25),
            expected_hours=Decimal('8'),
        )
        morning_cell = Schedule.objects.create(
            schedule_version=version, employee=employee,
            shift_template=morning, schedule_date=date(2026, 5, 26),
            expected_hours=Decimal('8'),
        )

        vs = check_schedule_violations(version)
        rest_vs = [v for v in vs if v.rule == 'min_rest_hours']
        assert len(rest_vs) == 1
        v = rest_vs[0]
        # Trigger cell = the next-day morning shift (the one that started too early)
        assert v.schedule_date == '2026-05-26'
        assert v.shift_template_id == morning_cell.shift_template_id
        # The earlier night shift goes into related_dates
        assert v.related_dates == ['2026-05-25']
        assert v.detail['rest_hours'] == pytest.approx(2.0, abs=0.01)
        assert v.detail['required_hours'] == 11
        assert v.severity == 'hard'
        assert v.employee_code == 'EPC1'

    def test_consecutive_days_pins_trigger_to_first_illegal_day(
        self, db, organization, branch, admin_user, employee
    ):
        shift = _shift(organization, '日班', time(9, 0), time(17, 0))
        version = _version(organization, branch, admin_user)
        # 8 consecutive days — limit 6 → 7th day (index 6) is the first illegal
        for i in range(8):
            Schedule.objects.create(
                schedule_version=version, employee=employee,
                shift_template=shift,
                schedule_date=date(2026, 5, 25) + timedelta(days=i),
                expected_hours=Decimal('8'),
            )
        vs = check_schedule_violations(
            version,
            # boost weekly cap so only the consecutive-days rule fires
            {'max_consecutive_days': 6, 'max_weekly_hours': 999, 'max_daily_hours': 24, 'min_rest_hours': 0},
        )
        cd_vs = [v for v in vs if v.rule == 'max_consecutive_days']
        assert len(cd_vs) == 1
        # 6 days after start → 2026-05-25 + 6 days = 2026-05-31 (the 7th day)
        assert cd_vs[0].schedule_date == '2026-05-31'
        assert cd_vs[0].detail['consecutive_days'] == 8
        assert cd_vs[0].detail['max_days'] == 6
        # All eight days minus the trigger appear in related_dates
        assert len(cd_vs[0].related_dates) == 7
        assert '2026-05-25' in cd_vs[0].related_dates
        assert '2026-06-01' in cd_vs[0].related_dates

    def test_weekly_hours_pins_trigger_to_last_shift_of_week(
        self, db, organization, branch, admin_user, employee
    ):
        long_shift = _shift(organization, '長班', time(8, 0), time(22, 0))  # 14h
        version = _version(organization, branch, admin_user)
        # Mon-Wed × 14h = 42h > 40h limit (2026-05-25 is a Monday)
        for i in range(3):
            Schedule.objects.create(
                schedule_version=version, employee=employee,
                shift_template=long_shift,
                schedule_date=date(2026, 5, 25) + timedelta(days=i),
                expected_hours=Decimal('14'),
            )
        vs = check_schedule_violations(
            version,
            {'max_weekly_hours': 40, 'max_daily_hours': 24, 'max_consecutive_days': 99, 'min_rest_hours': 0},
        )
        wk_vs = [v for v in vs if v.rule == 'max_weekly_hours']
        assert len(wk_vs) == 1
        # Trigger = Wednesday (last shift chronologically within the week)
        assert wk_vs[0].schedule_date == '2026-05-27'
        assert wk_vs[0].detail['total_hours'] == 42.0
        assert wk_vs[0].detail['max_hours'] == 40

    def test_daily_hours_violation_detected(
        self, db, organization, branch, admin_user, employee
    ):
        morning = _shift(organization, '早', time(8, 0), time(14, 0))
        evening = _shift(organization, '晚', time(15, 0), time(21, 0))
        version = _version(organization, branch, admin_user)
        Schedule.objects.create(
            schedule_version=version, employee=employee, shift_template=morning,
            schedule_date=date(2026, 5, 25), expected_hours=Decimal('6'),
        )
        Schedule.objects.create(
            schedule_version=version, employee=employee, shift_template=evening,
            schedule_date=date(2026, 5, 25), expected_hours=Decimal('6'),
        )
        vs = check_schedule_violations(
            version,
            {'max_daily_hours': 8, 'max_weekly_hours': 999, 'max_consecutive_days': 99, 'min_rest_hours': 0},
        )
        dh_vs = [v for v in vs if v.rule == 'max_daily_hours']
        assert len(dh_vs) == 1
        assert dh_vs[0].schedule_date == '2026-05-25'
        assert dh_vs[0].detail['total_hours'] == 12.0


class TestSummary:
    def test_summarize_groups_by_rule(
        self, db, organization, branch, admin_user, employee
    ):
        night = _shift(organization, '夜', time(22, 0), time(6, 0))
        morning = _shift(organization, '早', time(8, 0), time(16, 0))
        version = _version(organization, branch, admin_user)
        for d in [date(2026, 5, 25), date(2026, 5, 27)]:
            Schedule.objects.create(
                schedule_version=version, employee=employee,
                shift_template=night, schedule_date=d, expected_hours=Decimal('8'),
            )
            Schedule.objects.create(
                schedule_version=version, employee=employee,
                shift_template=morning,
                schedule_date=d + timedelta(days=1),
                expected_hours=Decimal('8'),
            )
        vs = check_schedule_violations(version)
        summary = summarize_by_rule(vs)
        assert summary.get('min_rest_hours', 0) == 2


class TestCheckComplianceEndpoint:
    """`POST /api/schedules/versions/{id}/check-compliance/`"""

    def test_endpoint_returns_per_cell_payload(
        self, db, organization, branch, admin_user, admin_api_client, employee
    ):
        night = _shift(organization, '夜', time(22, 0), time(6, 0))
        morning = _shift(organization, '早', time(8, 0), time(16, 0))
        version = _version(organization, branch, admin_user)
        Schedule.objects.create(
            schedule_version=version, employee=employee, shift_template=night,
            schedule_date=date(2026, 5, 25), expected_hours=Decimal('8'),
        )
        Schedule.objects.create(
            schedule_version=version, employee=employee, shift_template=morning,
            schedule_date=date(2026, 5, 26), expected_hours=Decimal('8'),
        )

        url = f'/api/schedules/versions/{version.id}/check-compliance/'
        resp = admin_api_client.post(url, {}, format='json')
        assert resp.status_code == 200, resp.content
        body = resp.json()
        assert body['schedule_version_id'] == version.id
        assert body['total_count'] >= 1
        assert body['summary_by_rule'].get('min_rest_hours', 0) >= 1
        v = body['violations'][0]
        for key in (
            'rule', 'rule_label', 'severity', 'employee_pk', 'employee_code',
            'employee_name', 'schedule_date', 'shift_template_id',
            'related_dates', 'detail',
        ):
            assert key in v

    def test_endpoint_pass_when_clean(
        self, db, organization, branch, admin_user, admin_api_client, employee
    ):
        day = _shift(organization, '日班', time(9, 0), time(17, 0))
        version = _version(organization, branch, admin_user)
        Schedule.objects.create(
            schedule_version=version, employee=employee, shift_template=day,
            schedule_date=date(2026, 5, 25), expected_hours=Decimal('8'),
        )
        url = f'/api/schedules/versions/{version.id}/check-compliance/'
        resp = admin_api_client.post(url, {}, format='json')
        assert resp.status_code == 200
        assert resp.json()['total_count'] == 0
        assert resp.json()['violations'] == []

    def test_endpoint_does_not_write_compliance_check(
        self, db, organization, branch, admin_user, admin_api_client
    ):
        from apps.compliance.models import ComplianceCheck
        version = _version(organization, branch, admin_user)
        before = ComplianceCheck.objects.count()
        url = f'/api/schedules/versions/{version.id}/check-compliance/'
        admin_api_client.post(url, {}, format='json')
        assert ComplianceCheck.objects.count() == before
