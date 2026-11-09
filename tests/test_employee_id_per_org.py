"""
員工編號改為「機構內唯一」＋到職日年份檢查（2026-11-10，PM 回報）。

PM 在新機構「PM Test」新增員工編號 1 被擋：`employee_id` 原本全系統 unique，
別的機構用過就不能再用。另外原生 date input 打民國年會送出西元 0110 年。
"""
from datetime import date

import pytest
from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction
from rest_framework import status

from apps.employees.models import Employee
from apps.organizations.models import Organization

User = get_user_model()
URL = '/api/employees/employees/'


@pytest.fixture
def other_org(db):
    return Organization.objects.create(name='別的機構', code='OTHER')


def _payload(org, *, code='1', username='u1', hire_date='2021-01-01'):
    return {
        'user': {
            'username': username,
            'email': f'{username}@example.com',
            'first_name': '1',
            'last_name': '趙',
            'password': 'testpass123',
        },
        'employee_id': code,
        'organization': org.pk,
        'position': '護理師',
        'contract_type': 'full_time',
        'pay_type': 'monthly',
        'agreed_hours_per_week': 40,
        'hire_date': hire_date,
    }


def _existing_employee(org, code='1', username='taken'):
    user = User.objects.create_user(username=username, password='x')
    return Employee.objects.create(
        user=user, employee_id=code, organization=org,
        position='照服員', hire_date=date(2024, 1, 1),
    )


@pytest.mark.django_db
class TestEmployeeIdUniquePerOrg:
    def test_same_code_allowed_in_another_org(self, admin_api_client, organization, other_org):
        _existing_employee(other_org, code='1')
        resp = admin_api_client.post(URL, _payload(organization, code='1'), format='json')
        assert resp.status_code == status.HTTP_201_CREATED, resp.data
        assert Employee.objects.filter(employee_id='1').count() == 2

    def test_same_code_rejected_within_org(self, admin_api_client, organization):
        _existing_employee(organization, code='1')
        resp = admin_api_client.post(URL, _payload(organization, code='1'), format='json')
        assert resp.status_code == status.HTTP_400_BAD_REQUEST
        assert 'employee_id' in resp.data
        # 被擋下時不能留下孤兒登入帳號
        assert not User.objects.filter(username='u1').exists()

    def test_update_keeps_own_code(self, admin_api_client, organization):
        emp = _existing_employee(organization, code='1')
        resp = admin_api_client.patch(
            f'{URL}{emp.pk}/', {'employee_id': '1', 'position': '護理師'}, format='json')
        assert resp.status_code == status.HTTP_200_OK, resp.data

    def test_update_to_colleagues_code_rejected(self, admin_api_client, organization):
        _existing_employee(organization, code='1', username='a')
        emp = _existing_employee(organization, code='2', username='b')
        resp = admin_api_client.patch(f'{URL}{emp.pk}/', {'employee_id': '1'}, format='json')
        assert resp.status_code == status.HTTP_400_BAD_REQUEST
        assert 'employee_id' in resp.data

    def test_db_constraint_backs_the_serializer(self, organization):
        _existing_employee(organization, code='1', username='a')
        with pytest.raises(IntegrityError), transaction.atomic():
            _existing_employee(organization, code='1', username='b')


@pytest.mark.django_db
class TestHireDateYear:
    def test_roc_year_rejected(self, admin_api_client, organization):
        # <input type="date"> 打 110/01/01 會送出 0110-01-01
        resp = admin_api_client.post(
            URL, _payload(organization, hire_date='0110-01-01'), format='json')
        assert resp.status_code == status.HTTP_400_BAD_REQUEST
        assert 'hire_date' in resp.data
        assert '西元' in str(resp.data['hire_date'][0])

    def test_gregorian_year_accepted(self, admin_api_client, organization):
        resp = admin_api_client.post(
            URL, _payload(organization, hire_date='2021-01-01'), format='json')
        assert resp.status_code == status.HTTP_201_CREATED, resp.data
