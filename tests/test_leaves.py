"""
Leave management tests.

Product decisions (2026-08-26):
- Single-layer approval: supervisor+ approves; supervisor creating on
  behalf of an employee is auto-approved (phone-in leave).
- Approval marks schedules in range as status='leave' (kept, not deleted);
  cancelling an approved leave restores the snapshot statuses.
- Annual-leave (特休) quota follows Labor Standards Act §38 seniority
  ladder with anniversary-year accounting.
- Approved leave = hard unavailable dates for the AI solver.
"""
from datetime import date
from decimal import Decimal

import pytest
from rest_framework import status

from apps.employees.models import Employee
from apps.leaves.annual import entitled_days, entitlement_year
from apps.leaves.models import LeaveRequest
from apps.leaves.solver_dates import approved_leave_dates
from apps.schedules.models import Schedule, ScheduleVersion
from apps.shifts.models import ShiftTemplate

pytestmark = pytest.mark.django_db

LEAVES_URL = '/api/leaves/requests/'


# conftest 的 *_api_client 共用同一個 APIClient 實例，同測試混用兩種身份時
# 後 force_authenticate 的會蓋掉前者——這裡覆寫為各自獨立的 client。
@pytest.fixture
def employee_api_client(employee_user):
    from rest_framework.test import APIClient
    client = APIClient()
    client.force_authenticate(user=employee_user)
    return client


@pytest.fixture
def admin_api_client(admin_user):
    from rest_framework.test import APIClient
    client = APIClient()
    client.force_authenticate(user=admin_user)
    return client


@pytest.fixture
def supervisor_api_client(supervisor_user):
    from rest_framework.test import APIClient
    client = APIClient()
    client.force_authenticate(user=supervisor_user)
    return client


@pytest.fixture
def employee(employee_user, organization, branch):
    return Employee.objects.create(
        user=employee_user, employee_id='EMP1',
        organization=organization, branch=branch,
        position='nurse', hire_date=date(2024, 1, 1),
    )


@pytest.fixture
def shift(organization):
    return ShiftTemplate.objects.create(
        organization=organization, name='早班',
        start_time='08:00', end_time='16:00', min_staff_count=1,
    )


@pytest.fixture
def version(organization, admin_user):
    return ScheduleVersion.objects.create(
        organization=organization, version_label='V', version_type='actual',
        period_start=date(2026, 9, 1), period_end=date(2026, 9, 30),
        created_by=admin_user,
    )


def _schedule(version, employee, shift, day):
    return Schedule.objects.create(
        schedule_version=version, employee=employee,
        shift_template=shift, schedule_date=day,
        expected_hours=Decimal('8.00'), status='assigned',
    )


class TestAnnualEntitlement:
    def test_seniority_ladder(self):
        hire = date(2020, 3, 1)
        assert entitled_days(hire, date(2020, 8, 1)) == 0     # <6mo
        assert entitled_days(hire, date(2020, 9, 1)) == 3     # 6mo
        assert entitled_days(hire, date(2021, 3, 1)) == 7     # 1y
        assert entitled_days(hire, date(2022, 3, 1)) == 10    # 2y
        assert entitled_days(hire, date(2023, 3, 1)) == 14    # 3y
        assert entitled_days(hire, date(2025, 3, 1)) == 15    # 5y
        assert entitled_days(hire, date(2030, 3, 1)) == 16    # 10y → 15+1
        assert entitled_days(date(1990, 1, 1), date(2026, 6, 1)) == 30  # cap

    def test_entitlement_year_window(self):
        hire = date(2024, 1, 1)
        start, end = entitlement_year(hire, date(2026, 8, 26))
        assert start == date(2026, 1, 1)
        assert end == date(2026, 12, 31)
        # 未滿六個月 → 無窗口
        assert entitlement_year(date(2026, 5, 1), date(2026, 8, 1)) == (None, None)


class TestLeaveWorkflow:
    def test_employee_submits_pending(self, employee_api_client, employee):
        response = employee_api_client.post(LEAVES_URL, {
            'employee': employee.pk, 'leave_type': 'personal',
            'start_date': '2026-09-10', 'end_date': '2026-09-11',
            'reason': '家中有事',
        }, format='json')
        assert response.status_code == status.HTTP_201_CREATED
        assert response.data['status'] == 'pending'
        assert response.data['total_days'] == 2

    def test_employee_cannot_submit_for_others(self, employee_api_client, organization,
                                               branch, manager_user):
        other = Employee.objects.create(
            user=manager_user, employee_id='EMP2', organization=organization,
            branch=branch, position='nurse', hire_date=date(2024, 1, 1),
        )
        response = employee_api_client.post(LEAVES_URL, {
            'employee': other.pk, 'leave_type': 'sick',
            'start_date': '2026-09-10', 'end_date': '2026-09-10',
        }, format='json')
        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_supervisor_on_behalf_auto_approved(self, supervisor_api_client, employee):
        response = supervisor_api_client.post(LEAVES_URL, {
            'employee': employee.pk, 'leave_type': 'sick',
            'start_date': '2026-09-10', 'end_date': '2026-09-10',
            'reason': '電話請病假',
        }, format='json')
        assert response.status_code == status.HTTP_201_CREATED
        assert response.data['status'] == 'approved'

    def test_approve_marks_schedules_and_snapshot(self, admin_api_client, employee_api_client,
                                                  employee, shift, version):
        s1 = _schedule(version, employee, shift, date(2026, 9, 10))
        s2 = _schedule(version, employee, shift, date(2026, 9, 11))
        outside = _schedule(version, employee, shift, date(2026, 9, 15))

        leave_id = employee_api_client.post(LEAVES_URL, {
            'employee': employee.pk, 'leave_type': 'annual',
            'start_date': '2026-09-10', 'end_date': '2026-09-11',
        }, format='json').data['id']

        response = admin_api_client.post(f'{LEAVES_URL}{leave_id}/approve/', {}, format='json')
        assert response.status_code == status.HTTP_200_OK

        s1.refresh_from_db(); s2.refresh_from_db(); outside.refresh_from_db()
        assert s1.status == 'leave' and s2.status == 'leave'
        assert outside.status == 'assigned'
        assert {x['id'] for x in response.data['affected_schedule_ids']} == {s1.pk, s2.pk}

    def test_double_approve_409(self, admin_api_client, employee_api_client, employee):
        leave_id = employee_api_client.post(LEAVES_URL, {
            'employee': employee.pk, 'leave_type': 'personal',
            'start_date': '2026-09-10', 'end_date': '2026-09-10',
        }, format='json').data['id']
        first = admin_api_client.post(f'{LEAVES_URL}{leave_id}/approve/', {}, format='json')
        second = admin_api_client.post(f'{LEAVES_URL}{leave_id}/approve/', {}, format='json')
        assert first.status_code == status.HTTP_200_OK
        assert second.status_code == status.HTTP_409_CONFLICT

    def test_reject_requires_note(self, admin_api_client, employee_api_client, employee):
        leave_id = employee_api_client.post(LEAVES_URL, {
            'employee': employee.pk, 'leave_type': 'personal',
            'start_date': '2026-09-10', 'end_date': '2026-09-10',
        }, format='json').data['id']
        missing = admin_api_client.post(f'{LEAVES_URL}{leave_id}/reject/', {}, format='json')
        assert missing.status_code == status.HTTP_400_BAD_REQUEST
        ok = admin_api_client.post(f'{LEAVES_URL}{leave_id}/reject/',
                                   {'note': '人力不足'}, format='json')
        assert ok.status_code == status.HTTP_200_OK
        assert ok.data['status'] == 'rejected'

    def test_cancel_approved_restores_schedules(self, admin_api_client, employee_api_client,
                                                employee, shift, version):
        s1 = _schedule(version, employee, shift, date(2026, 9, 10))
        leave_id = employee_api_client.post(LEAVES_URL, {
            'employee': employee.pk, 'leave_type': 'personal',
            'start_date': '2026-09-10', 'end_date': '2026-09-10',
        }, format='json').data['id']
        admin_api_client.post(f'{LEAVES_URL}{leave_id}/approve/', {}, format='json')
        s1.refresh_from_db(); assert s1.status == 'leave'

        response = admin_api_client.post(f'{LEAVES_URL}{leave_id}/cancel/', {}, format='json')
        assert response.status_code == status.HTTP_200_OK
        s1.refresh_from_db(); assert s1.status == 'assigned'

    def test_employee_cannot_approve(self, employee_api_client, employee):
        leave_id = employee_api_client.post(LEAVES_URL, {
            'employee': employee.pk, 'leave_type': 'personal',
            'start_date': '2026-09-10', 'end_date': '2026-09-10',
        }, format='json').data['id']
        response = employee_api_client.post(f'{LEAVES_URL}{leave_id}/approve/', {}, format='json')
        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_employee_sees_only_own(self, employee_api_client, supervisor_api_client,
                                    employee, organization, branch, manager_user):
        other = Employee.objects.create(
            user=manager_user, employee_id='EMP2', organization=organization,
            branch=branch, position='nurse', hire_date=date(2024, 1, 1),
        )
        supervisor_api_client.post(LEAVES_URL, {
            'employee': other.pk, 'leave_type': 'sick',
            'start_date': '2026-09-10', 'end_date': '2026-09-10',
        }, format='json')
        employee_api_client.post(LEAVES_URL, {
            'employee': employee.pk, 'leave_type': 'personal',
            'start_date': '2026-09-12', 'end_date': '2026-09-12',
        }, format='json')

        mine = employee_api_client.get(LEAVES_URL)
        assert mine.data['count'] == 1
        theirs = supervisor_api_client.get(LEAVES_URL)
        assert theirs.data['count'] == 2


class TestImpactAndBalance:
    def test_impact_preview(self, employee_api_client, employee, shift, version):
        _schedule(version, employee, shift, date(2026, 9, 10))
        _schedule(version, employee, shift, date(2026, 9, 11))
        response = employee_api_client.get(
            f'/api/leaves/requests/impact/?employee={employee.pk}'
            '&start_date=2026-09-10&end_date=2026-09-12')
        assert response.status_code == status.HTTP_200_OK
        assert response.data['affected_count'] == 2

    def test_balance_deducts_approved_annual(self, admin_api_client, employee_api_client,
                                             employee):
        # hire 2024-01-01 → 2026 當年度年資 2 年 → 10 天
        leave_id = employee_api_client.post(LEAVES_URL, {
            'employee': employee.pk, 'leave_type': 'annual',
            'start_date': '2026-09-10', 'end_date': '2026-09-12',
        }, format='json').data['id']
        admin_api_client.post(f'{LEAVES_URL}{leave_id}/approve/', {}, format='json')

        response = employee_api_client.get('/api/leaves/requests/balance/')
        assert response.status_code == status.HTTP_200_OK
        assert response.data['entitled_days'] == 10
        assert response.data['used_days'] == 3
        assert response.data['remaining_days'] == 7

    def test_pending_and_other_types_not_deducted(self, admin_api_client,
                                                  employee_api_client, employee):
        employee_api_client.post(LEAVES_URL, {  # pending annual：不扣
            'employee': employee.pk, 'leave_type': 'annual',
            'start_date': '2026-09-10', 'end_date': '2026-09-10',
        }, format='json')
        sick_id = employee_api_client.post(LEAVES_URL, {  # approved sick：不扣特休
            'employee': employee.pk, 'leave_type': 'sick',
            'start_date': '2026-09-20', 'end_date': '2026-09-20',
        }, format='json').data['id']
        admin_api_client.post(f'{LEAVES_URL}{sick_id}/approve/', {}, format='json')

        response = employee_api_client.get('/api/leaves/requests/balance/')
        assert response.data['used_days'] == 0


class TestSolverIntegration:
    def test_approved_leave_dates_helper(self, admin_api_client, employee_api_client,
                                         employee):
        leave_id = employee_api_client.post(LEAVES_URL, {
            'employee': employee.pk, 'leave_type': 'personal',
            'start_date': '2026-09-10', 'end_date': '2026-09-12',
        }, format='json').data['id']
        # pending 不算
        assert approved_leave_dates([employee.pk], date(2026, 9, 1), date(2026, 9, 30)) == {}
        admin_api_client.post(f'{LEAVES_URL}{leave_id}/approve/', {}, format='json')
        dates = approved_leave_dates([employee.pk], date(2026, 9, 1), date(2026, 9, 30))
        assert dates[employee.pk] == ['2026-09-10', '2026-09-11', '2026-09-12']

    def test_generate_skips_leave_days(self, admin_api_client, employee_api_client,
                                       employee, shift, organization):
        leave_id = employee_api_client.post(LEAVES_URL, {
            'employee': employee.pk, 'leave_type': 'personal',
            'start_date': '2026-09-02', 'end_date': '2026-09-02',
        }, format='json').data['id']
        admin_api_client.post(f'{LEAVES_URL}{leave_id}/approve/', {}, format='json')

        ShiftTemplate.objects.filter(pk=shift.pk).update(min_staff_count=0)
        response = admin_api_client.post('/api/ai/schedule/generate/', {
            'organization_id': organization.pk,
            'period_start': '2026-09-01', 'period_end': '2026-09-03',
            'consume_token': False,
        }, format='json')
        assert response.status_code == status.HTTP_200_OK
        leave_day_assignments = [
            a for a in response.data['assignments']
            if a['employee_id'] == employee.pk and a['date'] == '2026-09-02'
        ]
        assert leave_day_assignments == []


class TestLeaveV2P0:
    """LEAVE_V2 P0：身分對應、本人 vs 代登記、禁止自我核准。"""

    def test_me_returns_employee_pk(self, employee_api_client, employee):
        response = employee_api_client.get('/api/auth/users/me/')
        assert response.status_code == status.HTTP_200_OK
        assert response.data['employee_pk'] == employee.pk
        assert response.data['employee_code'] == employee.employee_id
        assert response.data['organization'] == employee.organization_id

    def test_me_without_profile_returns_null(self, admin_api_client):
        response = admin_api_client.get('/api/auth/users/me/')
        assert response.status_code == status.HTTP_200_OK
        assert response.data['employee_pk'] is None
        assert response.data['employee_code'] is None

    def test_supervisor_self_submission_stays_pending(self, supervisor_api_client,
                                                      supervisor_user, organization, branch):
        sup_emp = Employee.objects.create(
            user=supervisor_user, employee_id='SUP1', organization=organization,
            branch=branch, position='supervisor', hire_date=date(2024, 1, 1),
        )
        response = supervisor_api_client.post(LEAVES_URL, {
            'employee': sup_emp.pk, 'leave_type': 'personal',
            'start_date': '2026-09-10', 'end_date': '2026-09-10',
        }, format='json')
        assert response.status_code == status.HTTP_201_CREATED
        assert response.data['status'] == 'pending'
        assert response.data['submission_source'] == 'self'

    def test_on_behalf_records_manager_proxy(self, supervisor_api_client, employee):
        response = supervisor_api_client.post(LEAVES_URL, {
            'employee': employee.pk, 'leave_type': 'sick',
            'start_date': '2026-09-10', 'end_date': '2026-09-10',
        }, format='json')
        assert response.data['status'] == 'approved'
        assert response.data['submission_source'] == 'manager_proxy'

    def test_cannot_approve_own_request(self, supervisor_api_client, admin_api_client,
                                        supervisor_user, organization, branch):
        sup_emp = Employee.objects.create(
            user=supervisor_user, employee_id='SUP1', organization=organization,
            branch=branch, position='supervisor', hire_date=date(2024, 1, 1),
        )
        leave_id = supervisor_api_client.post(LEAVES_URL, {
            'employee': sup_emp.pk, 'leave_type': 'personal',
            'start_date': '2026-09-10', 'end_date': '2026-09-10',
        }, format='json').data['id']

        own_approve = supervisor_api_client.post(f'{LEAVES_URL}{leave_id}/approve/', {}, format='json')
        assert own_approve.status_code == status.HTTP_403_FORBIDDEN
        assert own_approve.data['code'] == 'self_approval_forbidden'
        own_reject = supervisor_api_client.post(f'{LEAVES_URL}{leave_id}/reject/', {'note': 'x'}, format='json')
        assert own_reject.status_code == status.HTTP_403_FORBIDDEN

        # 其他主管（admin）可以正常核准
        other = admin_api_client.post(f'{LEAVES_URL}{leave_id}/approve/', {}, format='json')
        assert other.status_code == status.HTTP_200_OK

    def test_submission_source_not_client_writable(self, employee_api_client, employee):
        response = employee_api_client.post(LEAVES_URL, {
            'employee': employee.pk, 'leave_type': 'personal',
            'start_date': '2026-09-10', 'end_date': '2026-09-10',
            'submission_source': 'manager_proxy',  # 應被忽略
        }, format='json')
        assert response.data['submission_source'] == 'self'
        assert response.data['status'] == 'pending'
