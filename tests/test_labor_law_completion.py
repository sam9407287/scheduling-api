"""
Labor-law completion tests (2026-09-18, from the 勞基法.docx handoff).

New rules added to the backend compliance engine (rules and judgement live
in the BACKEND — the frontend only renders returned violations):
  §32 max_daily_total_hours 12h (hard) / max_monthly_overtime_hours 46h (hard)
  §36 weekly_rest_days 2 per full ISO week (soft — 休息日出勤可加班費)
  §35 min_break_minutes 30 for >4h shifts (soft — 輪班制可調配)
  §37 holiday_scheduling via org Holiday table (soft — 加倍工資提醒)
"""
from datetime import date, time
from decimal import Decimal

import pytest

from apps.compliance.engine import check_schedule_violations
from apps.compliance.models import Holiday
from apps.employees.models import Employee
from apps.schedules.models import Schedule, ScheduleVersion
from apps.shifts.models import ShiftTemplate

pytestmark = pytest.mark.django_db


@pytest.fixture
def employee(employee_user, organization, branch):
    return Employee.objects.create(
        user=employee_user, employee_id='LAW1',
        organization=organization, branch=branch,
        position='nurse', hire_date=date(2024, 1, 1),
    )


@pytest.fixture
def version(organization, branch, admin_user):
    return ScheduleVersion.objects.create(
        organization=organization, branch=branch,
        version_label='LAW', version_type='actual',
        # 2026-06-01 是週一：期間含完整 ISO 週
        period_start=date(2026, 6, 1), period_end=date(2026, 6, 30),
        created_by=admin_user,
    )


def _shift(org, name, start, end, break_minutes=60):
    return ShiftTemplate.objects.create(
        organization=org, name=name, start_time=start, end_time=end,
        break_minutes=break_minutes, min_staff_count=1,
    )


def _cell(version, employee, shift, day, hours):
    return Schedule.objects.create(
        schedule_version=version, employee=employee, shift_template=shift,
        schedule_date=day, expected_hours=Decimal(str(hours)),
    )


def _rules_of(violations, rule):
    return [v for v in violations if v.rule == rule]


class TestDailyTotalCap:
    def test_over_12h_day_is_hard_violation(self, employee, version, organization):
        am = _shift(organization, 'AM', time(6, 0), time(13, 0))
        pm = _shift(organization, 'PM', time(13, 0), time(20, 0))
        _cell(version, employee, am, date(2026, 6, 2), 7)
        _cell(version, employee, pm, date(2026, 6, 2), 6)  # 13h > 12h

        hits = _rules_of(check_schedule_violations(version), 'max_daily_total_hours')
        assert len(hits) == 1
        assert hits[0].severity == 'hard'
        assert hits[0].detail['total_hours'] == 13.0

    def test_12h_day_ok(self, employee, version, organization):
        long = _shift(organization, 'L', time(8, 0), time(21, 0))
        _cell(version, employee, long, date(2026, 6, 2), 12)
        assert _rules_of(check_schedule_violations(version), 'max_daily_total_hours') == []


class TestMonthlyOvertime:
    def test_over_46h_month_is_hard_violation(self, employee, version, organization):
        long = _shift(organization, 'L', time(8, 0), time(21, 0))
        # 12 天 × 每天加班 4h（12-8）= 48h > 46h
        for i in range(12):
            _cell(version, employee, long, date(2026, 6, 1 + i), 12)
        hits = _rules_of(check_schedule_violations(version), 'max_monthly_overtime_hours')
        assert len(hits) == 1
        assert hits[0].detail['overtime_hours'] == 48.0
        assert hits[0].severity == 'hard'

    def test_under_cap_ok(self, employee, version, organization):
        long = _shift(organization, 'L', time(8, 0), time(21, 0))
        for i in range(10):  # 40h 加班 < 46h
            _cell(version, employee, long, date(2026, 6, 1 + i), 12)
        assert _rules_of(check_schedule_violations(version), 'max_monthly_overtime_hours') == []


class TestWeeklyRestDays:
    def test_six_workdays_in_full_week_is_soft(self, employee, version, organization):
        day = _shift(organization, 'D', time(9, 0), time(17, 0))
        for i in range(6):  # 6/1(一)~6/6(六)：休息日只有 1 天
            _cell(version, employee, day, date(2026, 6, 1 + i), 7)
        hits = _rules_of(check_schedule_violations(version), 'weekly_rest_days')
        assert len(hits) == 1
        assert hits[0].severity == 'soft'
        assert hits[0].detail['rest_days'] == 1

    def test_five_workdays_ok(self, employee, version, organization):
        day = _shift(organization, 'D', time(9, 0), time(17, 0))
        for i in range(5):
            _cell(version, employee, day, date(2026, 6, 1 + i), 7)
        assert _rules_of(check_schedule_violations(version), 'weekly_rest_days') == []

    def test_partial_boundary_week_not_checked(self, employee, organization, branch, admin_user):
        # 期間只涵蓋半週：不判，避免假警報
        v = ScheduleVersion.objects.create(
            organization=organization, branch=branch,
            version_label='HALF', version_type='actual',
            period_start=date(2026, 6, 3), period_end=date(2026, 6, 6),
            created_by=admin_user,
        )
        day = _shift(organization, 'D', time(9, 0), time(17, 0))
        for i in range(4):
            _cell(v, employee, day, date(2026, 6, 3 + i), 7)
        assert _rules_of(check_schedule_violations(v), 'weekly_rest_days') == []


class TestBreakMinutes:
    def test_long_shift_without_break_is_soft(self, employee, version, organization):
        no_break = _shift(organization, 'NB', time(8, 0), time(16, 0), break_minutes=0)
        _cell(version, employee, no_break, date(2026, 6, 2), 8)
        hits = _rules_of(check_schedule_violations(version), 'min_break_minutes')
        assert len(hits) == 1
        assert hits[0].severity == 'soft'

    def test_short_shift_exempt(self, employee, version, organization):
        short = _shift(organization, 'S', time(8, 0), time(12, 0), break_minutes=0)
        _cell(version, employee, short, date(2026, 6, 2), 4)
        assert _rules_of(check_schedule_violations(version), 'min_break_minutes') == []


class TestHolidayScheduling:
    def test_holiday_cell_is_soft_reminder(self, employee, version, organization):
        Holiday.objects.create(organization=organization, date=date(2026, 6, 19), name='端午節')
        day = _shift(organization, 'D', time(9, 0), time(17, 0))
        _cell(version, employee, day, date(2026, 6, 19), 7)
        hits = _rules_of(check_schedule_violations(version), 'holiday_scheduling')
        assert len(hits) == 1
        assert hits[0].severity == 'soft'
        assert hits[0].detail['holiday_name'] == '端午節'

    def test_other_org_holiday_ignored(self, employee, version, organization):
        from apps.organizations.models import Organization
        other = Organization.objects.create(
            name='他', code='OT-LAW', address='x', phone='1', email='a@b.c')
        Holiday.objects.create(organization=other, date=date(2026, 6, 19), name='端午節')
        day = _shift(organization, 'D', time(9, 0), time(17, 0))
        _cell(version, employee, day, date(2026, 6, 19), 7)
        assert _rules_of(check_schedule_violations(version), 'holiday_scheduling') == []


class TestHolidayAPI:
    def test_crud_and_org_isolation(self, admin_api_client, organization):
        created = admin_api_client.post('/api/compliance/holidays/', {
            'organization': organization.pk, 'date': '2026-10-10', 'name': '國慶日',
        }, format='json')
        assert created.status_code == 201
        listing = admin_api_client.get('/api/compliance/holidays/?year=2026')
        assert listing.data['count'] == 1
        dup = admin_api_client.post('/api/compliance/holidays/', {
            'organization': organization.pk, 'date': '2026-10-10', 'name': '重複',
        }, format='json')
        assert dup.status_code == 400


class TestSolverDailyTotalCap:
    def test_solver_respects_12h_cap(self, admin_api_client, employee, organization):
        # 三個 5h 班（無重疊）共 15h：solver 最多讓同員工排到 ≤12h
        for idx, (start, end) in enumerate([(time(5,0),time(10,0)), (time(10,0),time(15,0)), (time(15,0),time(20,0))]):
            ShiftTemplate.objects.create(
                organization=organization, name=f'S{idx}', start_time=start,
                end_time=end, break_minutes=60, min_staff_count=0,
            )
        resp = admin_api_client.post('/api/ai/schedule/generate/', {
            'organization_id': organization.pk,
            'period_start': '2026-06-02', 'period_end': '2026-06-02',
            'enforce_labor_law': True,
            'constraints': {'max_daily_hours': 24},  # 放寬 8h 正常上限，測 12h 絕對上限
            'consume_token': False,
        }, format='json')
        assert resp.status_code == 200
        mine = [a for a in resp.data['assignments'] if a['employee_id'] == employee.pk]
        assert len(mine) <= 2  # 3 班全排 = 15h 會爆 12h
