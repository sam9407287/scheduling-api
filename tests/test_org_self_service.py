"""
Self-service organization tests (2026-09-23).

Google 登入不再自動開機構——新 manager organization=None，進系統後
POST /api/organizations/organizations/ 自建，建立後自動綁定為自己的租戶。
可見性：非 superuser 只看得到自己的機構；刪除限 superuser。
"""
import pytest
from rest_framework import status
from rest_framework.test import APIClient

from apps.accounts.models import Role, User
from apps.organizations.models import Organization

pytestmark = pytest.mark.django_db

URL = '/api/organizations/organizations/'


@pytest.fixture
def manager_role(db):
    role, _ = Role.objects.get_or_create(
        name='manager', defaults={'description': '管理者', 'permissions': {}},
    )
    return role


def _manager_client(manager_role, username, organization=None):
    user = User.objects.create_user(
        username=username, email=f'{username}@example.com', password='pw',
        role=manager_role, organization=organization,
    )
    client = APIClient()
    client.force_authenticate(user=user)
    return client, user


class TestSelfServiceCreate:
    def test_orgless_manager_creates_org_and_gets_bound(self, manager_role):
        client, user = _manager_client(manager_role, 'newbie')
        response = client.post(URL, {'name': '小天使照護'}, format='json')
        assert response.status_code == status.HTTP_201_CREATED
        user.refresh_from_db()
        assert user.organization_id == response.data['id']
        assert response.data['code'].startswith('ORG-')  # code 可省略、自動產生

    def test_explicit_code_is_kept(self, manager_role):
        client, _ = _manager_client(manager_role, 'coder')
        response = client.post(URL, {'name': 'X', 'code': 'MY-CODE'}, format='json')
        assert response.status_code == status.HTTP_201_CREATED
        assert response.data['code'] == 'MY-CODE'

    def test_second_org_returns_409(self, manager_role):
        client, _ = _manager_client(manager_role, 'greedy')
        first = client.post(URL, {'name': '第一間'}, format='json')
        second = client.post(URL, {'name': '第二間'}, format='json')
        assert first.status_code == status.HTTP_201_CREATED
        assert second.status_code == status.HTTP_409_CONFLICT
        assert second.data['code'] == 'organization_already_exists'

    def test_manager_with_existing_org_gets_409(self, manager_role, organization):
        client, _ = _manager_client(manager_role, 'settled', organization)
        response = client.post(URL, {'name': '另一間'}, format='json')
        assert response.status_code == status.HTTP_409_CONFLICT


class TestVisibility:
    def test_manager_sees_only_own_org(self, manager_role):
        client_a, _ = _manager_client(manager_role, 'alice')
        client_b, _ = _manager_client(manager_role, 'bob')
        client_a.post(URL, {'name': 'A 機構'}, format='json')
        client_b.post(URL, {'name': 'B 機構'}, format='json')

        listed = client_a.get(URL)
        names = [o['name'] for o in listed.data['results']] \
            if 'results' in listed.data else [o['name'] for o in listed.data]
        assert names == ['A 機構']

    def test_orgless_manager_sees_empty_list(self, manager_role, organization):
        client, _ = _manager_client(manager_role, 'lonely')
        listed = client.get(URL)
        rows = listed.data['results'] if 'results' in listed.data else listed.data
        assert rows == []

    def test_other_org_detail_404(self, manager_role, organization):
        client, _ = _manager_client(manager_role, 'nosy')
        client.post(URL, {'name': '自己的'}, format='json')
        response = client.get(f'{URL}{organization.pk}/')
        assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_superuser_sees_all(self, admin_user, manager_role):
        client_m, _ = _manager_client(manager_role, 'maker')
        client_m.post(URL, {'name': '私有機構'}, format='json')
        admin_client = APIClient()
        admin_client.force_authenticate(user=admin_user)
        listed = admin_client.get(URL)
        rows = listed.data['results'] if 'results' in listed.data else listed.data
        assert len(rows) == Organization.objects.count() >= 2


class TestDestroy:
    def test_manager_cannot_delete_own_org(self, manager_role):
        client, user = _manager_client(manager_role, 'destroyer')
        created = client.post(URL, {'name': '要刪的'}, format='json')
        response = client.delete(f"{URL}{created.data['id']}/")
        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_superuser_can_delete(self, admin_user, manager_role):
        org = Organization.objects.create(name='孤兒機構', code='ORPHAN-1')
        admin_client = APIClient()
        admin_client.force_authenticate(user=admin_user)
        response = admin_client.delete(f'{URL}{org.pk}/')
        assert response.status_code == status.HTTP_204_NO_CONTENT
