"""
新增員工沒帶機構時用操作者的機構；availability 尚未建立時回空的 204（2026-11-23）。

前端「新增員工」表單把機構標成「選填」，但後端原本沒帶 `organization` 就回 400。
另外 `GET …/availability/` 在沒有設定時回 204 卻帶 JSON body，違反 HTTP 規範
（OkHttp 丟 ProtocolException、Vite dev proxy 回 502）。
"""
from datetime import date

import pytest
from django.contrib.auth import get_user_model
from rest_framework import status

from apps.employees.models import Employee
from apps.organizations.models import Organization

User = get_user_model()
URL = '/api/employees/employees/'


def _payload(**overrides):
    payload = {
        'user': {
            'username': 'newbie',
            'email': 'newbie@example.com',
            'first_name': '小明',
            'last_name': '王',
            'password': 'testpass123',
        },
        'employee_id': 'N1',
        'position': '照服員',
        'contract_type': 'full_time',
        'pay_type': 'monthly',
        'agreed_hours_per_week': 40,
        'hire_date': '2023-07-01',
    }
    payload.update(overrides)
    return payload


@pytest.mark.django_db
class TestOrganizationDefaultsToCaller:
    def test_manager_without_organization_field(self, manager_api_client, organization):
        resp = manager_api_client.post(URL, _payload(), format='json')
        assert resp.status_code == status.HTTP_201_CREATED, resp.data
        assert resp.data['organization'] == organization.pk
        emp = Employee.objects.get(employee_id='N1')
        assert emp.organization_id == organization.pk
        # 登入帳號也要綁到同一間機構
        assert emp.user.organization_id == organization.pk

    def test_superuser_bound_to_org_without_organization_field(self, admin_api_client, organization):
        resp = admin_api_client.post(URL, _payload(), format='json')
        assert resp.status_code == status.HTTP_201_CREATED, resp.data
        assert resp.data['organization'] == organization.pk

    def test_explicit_organization_still_wins(self, admin_api_client, organization):
        other = Organization.objects.create(name='別的機構', code='OTHER')
        resp = admin_api_client.post(URL, _payload(organization=other.pk), format='json')
        assert resp.status_code == status.HTTP_201_CREATED, resp.data
        assert resp.data['organization'] == other.pk

    def test_caller_without_organization_gets_400(self, api_client, manager_role):
        user = User.objects.create_user(username='noorg', password='x', role=manager_role)
        api_client.force_authenticate(user=user)
        resp = api_client.post(URL, _payload(), format='json')
        assert resp.status_code == status.HTTP_400_BAD_REQUEST
        assert 'organization' in resp.data
        # 被擋下時不能留下孤兒登入帳號
        assert not User.objects.filter(username='newbie').exists()

    def test_duplicate_code_checked_against_defaulted_org(self, manager_api_client, organization):
        taken = User.objects.create_user(username='taken', password='x')
        Employee.objects.create(
            user=taken, employee_id='N1', organization=organization,
            position='照服員', hire_date=date(2024, 1, 1),
        )
        resp = manager_api_client.post(URL, _payload(), format='json')
        assert resp.status_code == status.HTTP_400_BAD_REQUEST
        assert 'employee_id' in resp.data

    def test_update_does_not_require_or_change_organization(self, admin_api_client, organization):
        other = Organization.objects.create(name='別的機構', code='OTHER')
        user = User.objects.create_user(username='stay', password='x')
        emp = Employee.objects.create(
            user=user, employee_id='S1', organization=other,
            position='照服員', hire_date=date(2024, 1, 1),
        )
        resp = admin_api_client.patch(f'{URL}{emp.pk}/', {'position': '護理師'}, format='json')
        assert resp.status_code == status.HTTP_200_OK, resp.data
        emp.refresh_from_db()
        assert emp.organization_id == other.pk


@pytest.mark.django_db
class TestAvailabilityEmpty204:
    def test_no_availability_returns_bodyless_204(self, admin_api_client, organization):
        user = User.objects.create_user(username='fresh', password='x')
        emp = Employee.objects.create(
            user=user, employee_id='F1', organization=organization,
            position='照服員', hire_date=date(2024, 1, 1),
        )
        resp = admin_api_client.get(f'{URL}{emp.pk}/availability/')
        assert resp.status_code == status.HTTP_204_NO_CONTENT
        # 測試 client 會自動把 204 的 body 清掉，所以要看 view 實際回的 data
        assert resp.data is None
