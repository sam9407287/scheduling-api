"""Supervisors can log attendance on behalf of an employee (employee_id); scoped to own org."""
from datetime import date
from decimal import Decimal

import pytest
from rest_framework import status
from rest_framework.test import APIClient

from apps.attendance.models import Attendance
from apps.employees.models import Employee
from apps.organizations.models import Organization

pytestmark = pytest.mark.django_db
URL = '/api/attendance/attendances/'


@pytest.fixture
def employee(employee_user, organization, branch):
    return Employee.objects.create(user=employee_user, employee_id='ATT1', organization=organization,
                                   branch=branch, position='nurse', hire_date=date(2024, 1, 1))


def _client(user):
    c = APIClient(); c.force_authenticate(user=user); return c


def test_supervisor_logs_attendance_for_employee(supervisor_user, employee):
    r = _client(supervisor_user).post(URL, {'employee_id': employee.pk, 'work_date': '2026-10-10',
                                            'actual_hours': '8.00'}, format='json')
    assert r.status_code == status.HTTP_201_CREATED, r.data
    assert Attendance.objects.get(pk=r.data['id']).employee_id == employee.pk
    assert r.data['employee']['id'] == employee.pk


def test_employee_clocks_in_without_employee_id(employee_user, employee):
    r = _client(employee_user).post(URL, {'work_date': '2026-10-10', 'actual_hours': '8.00'}, format='json')
    assert r.status_code == status.HTTP_201_CREATED, r.data
    assert r.data['employee']['id'] == employee.pk


def test_account_without_profile_gets_400_not_500(supervisor_user):
    r = _client(supervisor_user).post(URL, {'work_date': '2026-10-10', 'actual_hours': '8.00'}, format='json')
    assert r.status_code == status.HTTP_400_BAD_REQUEST
    assert 'employee_id' in r.data


def test_cross_org_employee_rejected(supervisor_user, employee):
    other = Organization.objects.create(name='別家', code='OTHER')
    employee.organization = other; employee.save(update_fields=['organization'])
    r = _client(supervisor_user).post(URL, {'employee_id': employee.pk, 'work_date': '2026-10-10',
                                            'actual_hours': '8.00'}, format='json')
    assert r.status_code == status.HTTP_400_BAD_REQUEST


def test_duplicate_day_rejected_with_400(supervisor_user, employee):
    body = {'employee_id': employee.pk, 'work_date': '2026-10-10', 'actual_hours': '8.00'}
    assert _client(supervisor_user).post(URL, body, format='json').status_code == 201
    assert _client(supervisor_user).post(URL, body, format='json').status_code == 400


# --- 2026-10-26: 代建／修改出勤後，工時與異常由後端重算（前端交接文件假設） ---

def test_manager_create_with_clock_times_computes_hours_and_anomaly(supervisor_user, employee):
    body = {'employee_id': employee.pk, 'work_date': '2026-10-20',
            'clock_in': '2026-10-20T00:00:00Z', 'clock_out': '2026-10-20T13:00:00Z',
            'notes': '代建', 'actual_hours': '1.00', 'anomaly_flag': False, 'anomaly_reason': 'x'}
    r = _client(supervisor_user).post(URL, body, format='json')
    assert r.status_code == status.HTTP_201_CREATED, r.data
    assert Decimal(r.data['actual_hours']) == Decimal('13.00')   # 前端送的 1.00 被覆蓋
    assert r.data['anomaly_flag'] is True
    assert '超時工作' in r.data['anomaly_reason']
    assert '無排班出勤' in r.data['anomaly_reason']
    assert r.data['anomaly_reason'] != 'x'


def test_patch_clock_out_recomputes_and_clears_stale_anomaly(supervisor_user, employee):
    from apps.attendance.models import AnomalyRecord
    c = _client(supervisor_user)
    r = c.post(URL, {'employee_id': employee.pk, 'work_date': '2026-10-20',
                     'clock_in': '2026-10-20T00:00:00Z', 'clock_out': '2026-10-20T13:00:00Z'}, format='json')
    assert r.status_code == 201, r.data
    att_id = r.data['id']
    assert AnomalyRecord.objects.filter(attendance_id=att_id, anomaly_type='overtime').exists()

    r = c.patch(f'{URL}{att_id}/', {'clock_out': '2026-10-20T09:00:00Z'}, format='json')
    assert r.status_code == 200, r.data
    assert Decimal(r.data['actual_hours']) == Decimal('9.00')
    assert not AnomalyRecord.objects.filter(attendance_id=att_id, anomaly_type='overtime').exists()
    assert '超時工作' not in r.data['anomaly_reason']
    assert '無排班出勤' in r.data['anomaly_reason']   # 仍無排班 → 這條保留


def test_create_without_clock_times_keeps_legacy_actual_hours(supervisor_user, employee):
    r = _client(supervisor_user).post(URL, {'employee_id': employee.pk, 'work_date': '2026-10-21',
                                            'actual_hours': '8.00'}, format='json')
    assert r.status_code == 201, r.data
    assert Decimal(r.data['actual_hours']) == Decimal('8.00')
    assert r.data['anomaly_flag'] is False
