"""
Leave V2 P1 tests: org settings, multi-type balances, time-range leave,
solver interval avoidance, one-click unapprove is covered in the approval
test file.

Product decisions (2026-09-01, managers-only user base):
- day = 480 minutes by default, org-adjustable (60..1440)
- non-annual quotas org-configurable with statutory reference defaults
  (sick 30d / personal 14d / menstrual 12d); overdraft warns, never blocks
- time_range leave: single-day, same-day start<end, no cross-midnight;
  approval does NOT touch schedule statuses; solver blocks only
  time-overlapping shifts
"""
from datetime import date
from decimal import Decimal

import pytest
from rest_framework import status
from rest_framework.test import APIClient

from apps.employees.models import Employee
from apps.leaves.models import LeaveRequest
from apps.schedules.models import Schedule, ScheduleVersion
from apps.shifts.models import ShiftTemplate

pytestmark = pytest.mark.django_db

LEAVES_URL = '/api/leaves/requests/'
SETTINGS_URL = '/api/leaves/settings/'
BALANCES_URL = '/api/leaves/balances/'


@pytest.fixture
def admin_api_client(admin_user):
    client = APIClient()
    client.force_authenticate(user=admin_user)
    return client


@pytest.fixture
def employee_api_client(employee_user):
    client = APIClient()
    client.force_authenticate(user=employee_user)
    return client


@pytest.fixture
def employee(employee_user, organization, branch):
    return Employee.objects.create(
        user=employee_user, employee_id='EMP1',
        organization=organization, branch=branch,
        position='nurse', hire_date=date(2024, 1, 1),
    )


@pytest.fixture
def am_shift(organization):
    return ShiftTemplate.objects.create(
        organization=organization, name='早段', start_time='08:00',
        end_time='12:00', min_staff_count=1,
    )


@pytest.fixture
def pm_shift(organization):
    return ShiftTemplate.objects.create(
        organization=organization, name='午段', start_time='13:00',
        end_time='17:00', min_staff_count=1,
    )


class TestLeaveSettings:
    def test_defaults(self, admin_api_client):
        response = admin_api_client.get(SETTINGS_URL)
        assert response.status_code == status.HTTP_200_OK
        assert response.data['day_minutes'] == 480
        by_type = {q['leave_type']: q for q in response.data['quotas']}
        assert by_type['sick']['annual_quota_minutes'] == 30 * 480
        assert by_type['personal']['annual_quota_minutes'] == 14 * 480
        assert by_type['marriage']['annual_quota_minutes'] is None  # 事件制不限
        assert 'annual' not in by_type  # 特休法定，不在設定裡

    def test_put_updates_day_minutes_and_quota(self, admin_api_client):
        response = admin_api_client.put(SETTINGS_URL, {
            'day_minutes': 420,
            'quotas': [{'leave_type': 'sick', 'annual_quota_minutes': 10000}],
        }, format='json')
        assert response.status_code == status.HTTP_200_OK
        assert response.data['day_minutes'] == 420
        by_type = {q['leave_type']: q for q in response.data['quotas']}
        assert by_type['sick']['annual_quota_minutes'] == 10000
        assert by_type['sick']['is_default'] is False
        assert by_type['personal']['is_default'] is True

    def test_invalid_day_minutes_400(self, admin_api_client):
        for bad in (30, 2000, 'abc'):
            response = admin_api_client.put(SETTINGS_URL, {'day_minutes': bad}, format='json')
            assert response.status_code == status.HTTP_400_BAD_REQUEST


class TestTimeRangeLeave:
    def _create(self, client, employee, **overrides):
        body = {
            'employee': employee.pk, 'leave_type': 'sick',
            'request_unit': 'time_range',
            'start_date': '2026-09-10', 'end_date': '2026-09-10',
            'start_time': '13:00', 'end_time': '17:00',
        }
        body.update(overrides)
        return client.post(LEAVES_URL, body, format='json')

    def test_create_time_range(self, admin_api_client, employee):
        response = self._create(admin_api_client, employee)
        assert response.status_code == status.HTTP_201_CREATED
        assert response.data['request_unit'] == 'time_range'
        assert response.data['status'] == 'approved'  # 代登記自動核准

    def test_validation(self, admin_api_client, employee):
        no_time = self._create(admin_api_client, employee, start_time=None, end_time=None)
        assert no_time.status_code == status.HTTP_400_BAD_REQUEST
        multi_day = self._create(admin_api_client, employee, end_date='2026-09-11')
        assert multi_day.status_code == status.HTTP_400_BAD_REQUEST
        backwards = self._create(admin_api_client, employee, start_time='17:00', end_time='13:00')
        assert backwards.status_code == status.HTTP_400_BAD_REQUEST

    def test_approval_does_not_touch_schedules(self, admin_api_client, employee,
                                               am_shift, organization, admin_user):
        version = ScheduleVersion.objects.create(
            organization=organization, version_label='V', version_type='actual',
            period_start=date(2026, 9, 1), period_end=date(2026, 9, 30),
            created_by=admin_user,
        )
        row = Schedule.objects.create(
            schedule_version=version, employee=employee, shift_template=am_shift,
            schedule_date=date(2026, 9, 10), expected_hours=Decimal('4.00'),
            status='assigned',
        )
        response = self._create(admin_api_client, employee)
        assert response.data['status'] == 'approved'
        assert response.data['affected_schedule_ids'] == []
        row.refresh_from_db()
        assert row.status == 'assigned'  # 不動班次，純疊顯示


class TestBalances:
    def test_multi_type_minutes(self, admin_api_client, employee_api_client, employee):
        # 全日事假 2 天（代登記自動核准）→ 960 分鐘
        admin_api_client.post(LEAVES_URL, {
            'employee': employee.pk, 'leave_type': 'personal',
            'start_date': '2026-09-10', 'end_date': '2026-09-11',
        }, format='json')
        # 時段病假 4 小時（自動核准）→ 240 分鐘
        admin_api_client.post(LEAVES_URL, {
            'employee': employee.pk, 'leave_type': 'sick',
            'request_unit': 'time_range',
            'start_date': '2026-09-15', 'end_date': '2026-09-15',
            'start_time': '08:00', 'end_time': '12:00',
        }, format='json')
        # 員工自送 pending 特休 1 天 → pending 480
        employee_api_client.post(LEAVES_URL, {
            'employee': employee.pk, 'leave_type': 'annual',
            'start_date': '2026-09-20', 'end_date': '2026-09-20',
        }, format='json')

        response = admin_api_client.get(f'{BALANCES_URL}?employee={employee.pk}')
        assert response.status_code == status.HTTP_200_OK
        by_type = {b['leave_type']: b for b in response.data['balances']}
        assert by_type['personal']['used_minutes'] == 960
        assert by_type['sick']['used_minutes'] == 240
        assert by_type['annual']['pending_minutes'] == 480
        # hire 2024-01-01 → 2026 年資 2 年 → 特休 10 天 = 4800 分
        assert by_type['annual']['entitled_minutes'] == 4800
        assert by_type['marriage']['entitled_minutes'] is None  # 不限

    def test_overdraft_shows_negative_not_blocked(self, admin_api_client, employee):
        admin_api_client.put(SETTINGS_URL, {
            'quotas': [{'leave_type': 'personal', 'annual_quota_minutes': 480}],
        }, format='json')
        response = admin_api_client.post(LEAVES_URL, {
            'employee': employee.pk, 'leave_type': 'personal',
            'start_date': '2026-09-10', 'end_date': '2026-09-12',  # 3 天 > 1 天額度
        }, format='json')
        assert response.status_code == status.HTTP_201_CREATED  # 不擋

        balances = admin_api_client.get(f'{BALANCES_URL}?employee={employee.pk}')
        personal = next(b for b in balances.data['balances'] if b['leave_type'] == 'personal')
        assert personal['remaining_minutes'] == 480 - 3 * 480  # 負值供前端警告


class TestSolverIntervalAvoidance:
    def test_partial_leave_blocks_only_overlapping_shift(self, admin_api_client, employee,
                                                         am_shift, pm_shift, organization):
        # 下午 13:00-17:00 請假 → PM 班被擋、AM 班照排
        admin_api_client.post(LEAVES_URL, {
            'employee': employee.pk, 'leave_type': 'sick',
            'request_unit': 'time_range',
            'start_date': '2026-09-02', 'end_date': '2026-09-02',
            'start_time': '13:00', 'end_time': '17:00',
        }, format='json')
        ShiftTemplate.objects.filter(pk__in=[am_shift.pk, pm_shift.pk]).update(min_staff_count=0)

        response = admin_api_client.post('/api/ai/schedule/generate/', {
            'organization_id': organization.pk,
            'period_start': '2026-09-02', 'period_end': '2026-09-02',
            'consume_token': False,
        }, format='json')
        assert response.status_code == status.HTTP_200_OK
        mine = [a for a in response.data['assignments'] if a['employee_id'] == employee.pk]
        assert all(a['shift_id'] != pm_shift.pk for a in mine)

    def test_impact_overlap_minutes(self, admin_api_client, employee, am_shift,
                                    organization, admin_user):
        version = ScheduleVersion.objects.create(
            organization=organization, version_label='V', version_type='actual',
            period_start=date(2026, 9, 1), period_end=date(2026, 9, 30),
            created_by=admin_user,
        )
        Schedule.objects.create(
            schedule_version=version, employee=employee, shift_template=am_shift,
            schedule_date=date(2026, 9, 10), expected_hours=Decimal('4.00'),
            status='assigned',
        )
        response = admin_api_client.get(
            f'{LEAVES_URL}impact/?employee={employee.pk}'
            '&start_date=2026-09-10&end_date=2026-09-10'
            '&start_time=10:00&end_time=14:00')
        assert response.status_code == status.HTTP_200_OK
        # 早段班 08-12 與請假 10-14 重疊 2 小時
        assert response.data['overlap_minutes'] == 120
        assert response.data['daily_breakdown'][0]['scheduled_minutes'] == 240


class TestLeaveRowsExcludedFromWorkHours:
    """請假/取消的班次不是實際工時：合規檢查與 derive-legal seed 都要排除。"""

    def test_compliance_ignores_leave_rows(self, admin_api_client, employee, am_shift,
                                           organization, admin_user):
        from apps.compliance.engine import check_schedule_violations
        version = ScheduleVersion.objects.create(
            organization=organization, version_label='V', version_type='actual',
            period_start=date(2026, 9, 1), period_end=date(2026, 9, 30),
            created_by=admin_user,
        )
        # 連續 8 天班（cap 6 會違規），但其中 3 天標記請假 → 連續 5 天，不違規
        for i in range(8):
            Schedule.objects.create(
                schedule_version=version, employee=employee, shift_template=am_shift,
                schedule_date=date(2026, 9, 1 + i), expected_hours=Decimal('4.00'),
                status='leave' if i >= 5 else 'assigned',
            )
        violations = check_schedule_violations(version)
        assert [v for v in violations if v.rule == 'max_consecutive_days'] == []
