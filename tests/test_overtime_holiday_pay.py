"""
國定假日出勤加給依薪制分流（PM 2026-10-13 補充）。

月薪制：國定假日沒有雙倍薪，以補休為原則，無法補休才折發工資。
時薪制：當日工資加倍。
"""
from datetime import date
from decimal import Decimal

import pytest
from rest_framework import status
from rest_framework.test import APIClient

from apps.attendance.models import Attendance
from apps.compliance.models import Holiday
from apps.employees.models import Employee
from apps.overtime.models import OvertimeRecord, OvertimeRule

pytestmark = pytest.mark.django_db

CALC_URL = '/api/overtime/records/calculate/'


@pytest.fixture
def admin_api_client(admin_user):
    client = APIClient()
    client.force_authenticate(user=admin_user)
    return client


@pytest.fixture
def employee(employee_user, organization, branch):
    return Employee.objects.create(
        user=employee_user, employee_id='HOL1', organization=organization,
        branch=branch, position='nurse', hire_date=date(2024, 1, 1),
    )


@pytest.fixture
def holiday(organization):
    return Holiday.objects.create(
        organization=organization, date=date(2026, 10, 10), name='國慶日')


def _attendance(employee, day, hours):
    return Attendance.objects.create(
        employee=employee, work_date=day, actual_hours=Decimal(hours))


class TestHolidayPayBySalaryType:
    def test_hourly_employee_gets_double_pay(self, admin_api_client, employee, holiday):
        employee.pay_type = 'hourly'
        employee.save(update_fields=['pay_type'])
        att = _attendance(employee, holiday.date, '8.00')

        response = admin_api_client.post(CALC_URL, {'attendance_id': att.pk}, format='json')

        assert response.status_code == status.HTTP_201_CREATED
        record = OvertimeRecord.objects.get(attendance=att)
        assert record.overtime_type == 'holiday'
        assert record.hours == Decimal('8.00')
        assert record.multiplier == Decimal('2.00')

    def test_hourly_uses_org_holiday_rule_when_present(self, admin_api_client, employee,
                                                       holiday, organization):
        OvertimeRule.objects.create(
            organization=organization, overtime_type='holiday',
            multiplier=Decimal('2.50'))
        employee.pay_type = 'hourly'
        employee.save(update_fields=['pay_type'])
        att = _attendance(employee, holiday.date, '8.00')

        admin_api_client.post(CALC_URL, {'attendance_id': att.pk}, format='json')

        assert OvertimeRecord.objects.get(attendance=att).multiplier == Decimal('2.50')

    def test_monthly_employee_no_double_pay_comp_leave_first(self, admin_api_client,
                                                             employee, holiday):
        assert employee.pay_type == 'monthly'   # 預設月薪制
        att = _attendance(employee, holiday.date, '8.00')

        response = admin_api_client.post(CALC_URL, {'attendance_id': att.pk}, format='json')

        assert response.status_code == status.HTTP_201_CREATED
        record = OvertimeRecord.objects.get(attendance=att)
        assert record.overtime_type == 'holiday'
        assert record.multiplier == Decimal('1.00')
        assert '補休' in record.notes

    def test_non_holiday_unaffected(self, admin_api_client, employee, holiday):
        employee.pay_type = 'hourly'
        employee.save(update_fields=['pay_type'])
        att = _attendance(employee, date(2026, 10, 12), '9.00')   # 週一，非假日

        admin_api_client.post(CALC_URL, {'attendance_id': att.pk}, format='json')

        record = OvertimeRecord.objects.get(attendance=att)
        assert record.overtime_type == 'regular'
        assert record.multiplier == Decimal('1.34')


class TestPayTypeApi:
    def test_pay_type_defaults_and_patch(self, admin_api_client, employee):
        detail = admin_api_client.get(f'/api/employees/employees/{employee.pk}/')
        assert detail.data['pay_type'] == 'monthly'
        assert detail.data['pay_type_display'] == '月薪制'

        response = admin_api_client.patch(
            f'/api/employees/employees/{employee.pk}/', {'pay_type': 'hourly'}, format='json')
        assert response.status_code == status.HTTP_200_OK
        employee.refresh_from_db()
        assert employee.pay_type == 'hourly'

    def test_pay_type_rejects_unknown_value(self, admin_api_client, employee):
        response = admin_api_client.patch(
            f'/api/employees/employees/{employee.pk}/', {'pay_type': 'daily'}, format='json')
        assert response.status_code == status.HTTP_400_BAD_REQUEST


class TestOvertimeRecordAdjust:
    """管理者可 PATCH 倍率與備註（月薪制決定折發時改 2.00）；其他方法與欄位不開放。"""

    @pytest.fixture
    def record(self, employee, holiday):
        att = _attendance(employee, holiday.date, '8.00')
        return OvertimeRecord.objects.create(
            employee=employee, attendance=att, overtime_date=holiday.date,
            overtime_type='holiday', hours=Decimal('8.00'), multiplier=Decimal('1.00'),
            notes='月薪制國定假日出勤',
        )

    def test_manager_can_patch_multiplier_and_notes(self, manager_user, record):
        client = APIClient()
        client.force_authenticate(user=manager_user)
        response = client.patch(f'/api/overtime/records/{record.pk}/', {
            'multiplier': '2.00', 'notes': '無法補休，折發一日工資',
        }, format='json')
        assert response.status_code == status.HTTP_200_OK
        assert response.data['multiplier'] == '2.00'
        record.refresh_from_db()
        assert record.multiplier == Decimal('2.00')
        assert '折發' in record.notes

    def test_patch_ignores_system_fields(self, manager_user, record):
        client = APIClient()
        client.force_authenticate(user=manager_user)
        response = client.patch(f'/api/overtime/records/{record.pk}/', {
            'hours': '99.00', 'overtime_type': 'regular', 'multiplier': '2.00',
        }, format='json')
        assert response.status_code == status.HTTP_200_OK
        record.refresh_from_db()
        assert record.hours == Decimal('8.00')
        assert record.overtime_type == 'holiday'
        assert record.multiplier == Decimal('2.00')

    def test_employee_cannot_patch(self, employee_user, record):
        client = APIClient()
        client.force_authenticate(user=employee_user)
        response = client.patch(f'/api/overtime/records/{record.pk}/',
                                {'multiplier': '2.00'}, format='json')
        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_put_and_delete_not_allowed(self, manager_user, record):
        client = APIClient()
        client.force_authenticate(user=manager_user)
        assert client.put(f'/api/overtime/records/{record.pk}/',
                          {'multiplier': '2.00'}, format='json').status_code == 405
        assert client.delete(f'/api/overtime/records/{record.pk}/').status_code == 405

    def test_other_org_manager_cannot_see_or_patch(self, manager_role, record):
        from apps.accounts.models import User
        from apps.organizations.models import Organization
        other_org = Organization.objects.create(name='別家機構', code='OTHER')
        outsider = User.objects.create_user(
            username='other_mgr', password='x', role=manager_role, organization=other_org)
        client = APIClient()
        client.force_authenticate(user=outsider)
        assert client.get('/api/overtime/records/').data['count'] == 0
        assert client.patch(f'/api/overtime/records/{record.pk}/',
                            {'multiplier': '2.00'}, format='json').status_code == 404
