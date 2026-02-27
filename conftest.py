"""
Global test fixtures
"""
import pytest
from django.contrib.auth import get_user_model
from apps.accounts.models import Role
from apps.organizations.models import Organization, Branch

User = get_user_model()


@pytest.fixture
def admin_role(db):
    """Create admin role"""
    return Role.objects.create(
        name='admin',
        description='系統管理員',
        permissions={'all': True}
    )


@pytest.fixture
def manager_role(db):
    """Create manager role"""
    return Role.objects.create(
        name='manager',
        description='管理者',
        permissions={'manage_schedules': True}
    )


@pytest.fixture
def supervisor_role(db):
    """Create supervisor role"""
    return Role.objects.create(
        name='supervisor',
        description='主管',
        permissions={'view_schedules': True}
    )


@pytest.fixture
def employee_role(db):
    """Create employee role"""
    return Role.objects.create(
        name='employee',
        description='員工',
        permissions={'view_own': True}
    )


@pytest.fixture
def organization(db):
    """Create test organization"""
    return Organization.objects.create(
        name='測試機構',
        code='TEST',
        address='台北市信義區',
        phone='02-12345678',
        email='test@example.com',
    )


@pytest.fixture
def branch(db, organization):
    """Create test branch"""
    return Branch.objects.create(
        organization=organization,
        name='測試分店',
        code='BR01',
        address='台北市信義區101路',
        phone='02-11111111',
    )


@pytest.fixture
def admin_user(db, admin_role, organization):
    """Create admin user"""
    user = User.objects.create_user(
        username='admin',
        email='admin@example.com',
        password='testpass123',
        first_name='管理',
        last_name='員',
        is_staff=True,
        is_superuser=True,
        role=admin_role,
        organization=organization,
    )
    return user


@pytest.fixture
def manager_user(db, manager_role, organization, branch):
    """Create manager user"""
    user = User.objects.create_user(
        username='manager',
        email='manager@example.com',
        password='testpass123',
        first_name='管理',
        last_name='者',
        role=manager_role,
        organization=organization,
        branch=branch,
    )
    return user


@pytest.fixture
def supervisor_user(db, supervisor_role, organization, branch):
    """Create supervisor user"""
    user = User.objects.create_user(
        username='supervisor',
        email='supervisor@example.com',
        password='testpass123',
        first_name='主',
        last_name='管',
        role=supervisor_role,
        organization=organization,
        branch=branch,
    )
    return user


@pytest.fixture
def employee_user(db, employee_role, organization, branch):
    """Create employee user"""
    user = User.objects.create_user(
        username='employee1',
        email='employee1@example.com',
        password='testpass123',
        first_name='員',
        last_name='工',
        role=employee_role,
        organization=organization,
        branch=branch,
    )
    return user


@pytest.fixture
def api_client():
    """Create DRF API client"""
    from rest_framework.test import APIClient
    return APIClient()


@pytest.fixture
def admin_api_client(api_client, admin_user):
    """Create authenticated admin API client"""
    api_client.force_authenticate(user=admin_user)
    return api_client


@pytest.fixture
def manager_api_client(api_client, manager_user):
    """Create authenticated manager API client"""
    api_client.force_authenticate(user=manager_user)
    return api_client


@pytest.fixture
def supervisor_api_client(api_client, supervisor_user):
    """Create authenticated supervisor API client"""
    api_client.force_authenticate(user=supervisor_user)
    return api_client


@pytest.fixture
def employee_api_client(api_client, employee_user):
    """Create authenticated employee API client"""
    api_client.force_authenticate(user=employee_user)
    return api_client
