"""內建台灣國定假日（2026/2027）＋假日日曆端點（2026-10-22）。"""
from datetime import date, time
from decimal import Decimal

import pytest

from apps.compliance.engine import check_schedule_violations
from apps.compliance.models import Holiday
from apps.compliance.tw_holidays import TW_NATIONAL_HOLIDAYS, national_holidays

pytestmark = pytest.mark.django_db

CAL = '/api/compliance/holidays/calendar/'


class TestTable:
    def test_two_years_recorded_and_sorted(self):
        assert set(TW_NATIONAL_HOLIDAYS) >= {2026, 2027}
        for year, rows in TW_NATIONAL_HOLIDAYS.items():
            dates = [d for d, _, _ in rows]
            assert dates == sorted(dates)
            assert len(dates) == len(set(dates))
            assert all(d.year == year for d in dates)
            assert all(kind in ('holiday', 'makeup') for _, _, kind in rows)

    def test_known_dates(self):
        names = {h['date']: h['name'] for h in national_holidays(date(2026, 1, 1), date(2027, 12, 31))}
        assert names[date(2026, 2, 17)] == '春節（初一）'
        assert names[date(2026, 9, 25)] == '中秋節'
        assert names[date(2026, 10, 9)] == '國慶日補假'
        assert names[date(2027, 2, 6)] == '春節（初一）'
        assert date(2026, 1, 2) not in names  # 115 年起不再彈性放假

    def test_range_filter(self):
        rows = national_holidays(date(2026, 4, 1), date(2026, 4, 30))
        assert [r['date'].day for r in rows] == [3, 4, 5, 6]


class TestCalendarEndpoint:
    def test_default_range_is_this_and_next_year(self, supervisor_api_client):
        resp = supervisor_api_client.get(CAL)
        assert resp.status_code == 200, resp.content
        body = resp.json()
        assert body['start'].endswith('-01-01')
        assert body['count'] >= 1
        row = body['holidays'][0]
        assert set(row) == {'date', 'name', 'kind', 'source', 'id'}

    def test_merges_org_holiday_and_overrides_name(self, manager_api_client, organization):
        Holiday.objects.create(organization=organization, date=date(2026, 10, 10), name='院慶暨國慶')
        Holiday.objects.create(organization=organization, date=date(2026, 11, 11), name='院慶')
        resp = manager_api_client.get(CAL + '?start=2026-10-01&end=2026-11-30')
        rows = {r['date']: r for r in resp.json()['holidays']}
        assert rows['2026-10-10']['source'] == 'org'
        assert rows['2026-10-10']['name'] == '院慶暨國慶'
        assert rows['2026-11-11']['source'] == 'org'
        assert rows['2026-10-25']['source'] == 'national'
        assert rows['2026-10-26']['kind'] == 'makeup'

    def test_bad_range(self, manager_api_client):
        assert manager_api_client.get(CAL + '?start=2026-05-01&end=2026-01-01').status_code == 400
        assert manager_api_client.get(CAL + '?start=2020-01-01&end=2027-01-01').status_code == 400

    def test_other_org_custom_days_not_visible(self, manager_api_client):
        from apps.organizations.models import Organization
        other = Organization.objects.create(name='別家', code='HOL-O')
        Holiday.objects.create(organization=other, date=date(2026, 11, 12), name='別家院慶')
        resp = manager_api_client.get(CAL + '?start=2026-11-01&end=2026-11-30')
        assert all(r['date'] != '2026-11-12' for r in resp.json()['holidays'])


class TestComplianceUsesBuiltInHolidays:
    def test_national_holiday_shift_is_soft_reminder(self, organization, branch, admin_user,
                                                     employee_role):
        from apps.accounts.models import User
        from apps.employees.models import Employee
        from apps.schedules.models import Schedule, ScheduleVersion
        from apps.shifts.models import ShiftTemplate
        user = User.objects.create_user(username='hol_e', password='pw', role=employee_role,
                                        organization=organization, branch=branch)
        emp = Employee.objects.create(user=user, employee_id='HOL1', organization=organization,
                                      branch=branch, position='nurse', hire_date=date(2024, 1, 1))
        shift = ShiftTemplate.objects.create(organization=organization, name='早', start_time=time(8),
                                             end_time=time(16), min_staff_count=1)
        version = ScheduleVersion.objects.create(
            organization=organization, branch=branch, version_label='H', version_type='actual',
            period_start=date(2026, 10, 9), period_end=date(2026, 10, 11), created_by=admin_user)
        Schedule.objects.create(schedule_version=version, employee=emp, shift_template=shift,
                                schedule_date=date(2026, 10, 10), expected_hours=Decimal('8'))
        hits = [v for v in check_schedule_violations(version) if v.rule == 'holiday_scheduling']
        assert len(hits) == 1
        assert hits[0].severity == 'soft'
        assert hits[0].detail['holiday_name'] == '國慶日'
