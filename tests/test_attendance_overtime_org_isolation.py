"""
Data isolation for attendance, anomaly records and overtime (2026-11-28).

Before this fix a supervisor/manager of one organization could list, edit and
delete another organization's attendance rows and anomaly records, compute
overtime from any attendance id, and read/write every organization's overtime
rules. `calculate/` also produced a duplicate overtime record on every call.
"""
from datetime import date
from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model
from rest_framework.test import APIClient

from apps.attendance.models import AnomalyRecord, Attendance
from apps.employees.models import Employee
from apps.organizations.models import Branch, Organization
from apps.overtime.models import OvertimeRecord, OvertimeRule

User = get_user_model()
pytestmark = pytest.mark.django_db

ATT_URL = '/api/attendance/attendances/'
ANOMALY_URL = '/api/attendance/anomalies/'
CALC_URL = '/api/overtime/records/calculate/'
RECORD_URL = '/api/overtime/records/'
RULE_URL = '/api/overtime/rules/'
DAY = date(2026, 11, 3)  # Tuesday, no schedule ⇒ whole day counts as overtime


def _client(user):
    client = APIClient()
    client.force_authenticate(user=user)
    return client


def _ids(response):
    data = response.data
    rows = data['results'] if isinstance(data, dict) and 'results' in data else data
    return {row['id'] for row in rows}


def _employee(organization, branch, role, username, employee_id):
    user = User.objects.create_user(
        username=username, email=f'{username}@example.com', password='x',
        role=role, organization=organization, branch=branch)
    return Employee.objects.create(
        user=user, employee_id=employee_id, organization=organization,
        branch=branch, position='nurse', hire_date=date(2024, 1, 1))


def _attendance(employee, hours='9.00', day=DAY):
    return Attendance.objects.create(
        employee=employee, work_date=day, actual_hours=Decimal(hours))


@pytest.fixture
def own_employee(organization, branch, employee_role):
    return _employee(organization, branch, employee_role, 'own.emp', 'A1')


@pytest.fixture
def other_org(db):
    return Organization.objects.create(name='別家機構', code='OTHER')


@pytest.fixture
def other_branch(other_org):
    return Branch.objects.create(organization=other_org, name='別家分店', code='OB1')


@pytest.fixture
def other_employee(other_org, other_branch, employee_role):
    return _employee(other_org, other_branch, employee_role, 'other.emp', 'A1')


@pytest.fixture
def own_att(own_employee):
    return _attendance(own_employee)


@pytest.fixture
def other_att(other_employee):
    return _attendance(other_employee)


@pytest.fixture
def orgless_manager(manager_role):
    """Self-registered manager who has not created an organization yet."""
    return User.objects.create_user(
        username='orgless', email='orgless@example.com', password='x', role=manager_role)


class TestAttendanceIsolation:
    def test_list_shows_only_own_org(self, supervisor_user, own_att, other_att):
        response = _client(supervisor_user).get(ATT_URL)
        assert response.status_code == 200
        assert _ids(response) == {own_att.pk}

    @pytest.mark.parametrize('method', ['get', 'patch', 'delete'])
    def test_other_org_row_is_404(self, supervisor_user, other_att, method):
        response = getattr(_client(supervisor_user), method)(
            f'{ATT_URL}{other_att.pk}/', {'notes': 'x'}, format='json')
        assert response.status_code == 404
        other_att.refresh_from_db()
        assert other_att.notes == ''

    def test_employee_filter_cannot_reach_other_org(self, supervisor_user, other_att):
        response = _client(supervisor_user).get(ATT_URL, {'employee': other_att.employee_id})
        assert _ids(response) == set()

    def test_cannot_move_row_to_other_org_employee(self, supervisor_user, own_att, other_employee):
        response = _client(supervisor_user).patch(
            f'{ATT_URL}{own_att.pk}/', {'employee_id': other_employee.pk}, format='json')
        assert response.status_code == 400
        assert 'employee_id' in response.data
        own_att.refresh_from_db()
        assert own_att.employee_id != other_employee.pk

    def test_cannot_reference_other_org_substitute_or_branch(
            self, supervisor_user, own_att, other_employee, other_branch):
        client = _client(supervisor_user)
        r1 = client.patch(f'{ATT_URL}{own_att.pk}/',
                          {'substitute_for': other_employee.pk}, format='json')
        r2 = client.patch(f'{ATT_URL}{own_att.pk}/',
                          {'cross_branch': other_branch.pk}, format='json')
        assert r1.status_code == 400 and 'substitute_for' in r1.data
        assert r2.status_code == 400 and 'cross_branch' in r2.data

    def test_own_org_edit_still_works(self, supervisor_user, own_att, branch):
        response = _client(supervisor_user).patch(
            f'{ATT_URL}{own_att.pk}/',
            {'notes': '補登', 'cross_branch': branch.pk}, format='json')
        assert response.status_code == 200, response.data
        assert response.data['notes'] == '補登'

    def test_orgless_manager_sees_nothing(self, orgless_manager, own_att, other_att):
        response = _client(orgless_manager).get(ATT_URL)
        assert response.status_code == 200
        assert _ids(response) == set()

    def test_superuser_sees_all(self, admin_user, own_att, other_att):
        assert _ids(_client(admin_user).get(ATT_URL)) == {own_att.pk, other_att.pk}


class TestAnomalyIsolation:
    @pytest.fixture
    def own_anomaly(self, own_att):
        return AnomalyRecord.objects.create(
            attendance=own_att, anomaly_type='late', description='own')

    @pytest.fixture
    def other_anomaly(self, other_att):
        return AnomalyRecord.objects.create(
            attendance=other_att, anomaly_type='late', description='other')

    def test_list_shows_only_own_org(self, supervisor_user, own_anomaly, other_anomaly):
        response = _client(supervisor_user).get(ANOMALY_URL)
        assert response.status_code == 200
        assert _ids(response) == {own_anomaly.pk}

    def test_cannot_resolve_other_org_anomaly(self, supervisor_user, other_anomaly):
        response = _client(supervisor_user).post(
            f'{ANOMALY_URL}{other_anomaly.pk}/resolve/', {'notes': 'x'}, format='json')
        assert response.status_code == 404
        other_anomaly.refresh_from_db()
        assert other_anomaly.resolved is False

    def test_cannot_delete_other_org_anomaly(self, supervisor_user, other_anomaly):
        response = _client(supervisor_user).delete(f'{ANOMALY_URL}{other_anomaly.pk}/')
        assert response.status_code == 404
        assert AnomalyRecord.objects.filter(pk=other_anomaly.pk).exists()

    def test_cannot_create_anomaly_on_other_org_attendance(self, supervisor_user, other_att):
        response = _client(supervisor_user).post(ANOMALY_URL, {
            'attendance': other_att.pk, 'anomaly_type': 'late', 'description': 'x',
        }, format='json')
        assert response.status_code == 400
        assert 'attendance' in response.data
        assert not AnomalyRecord.objects.filter(attendance=other_att).exists()

    def test_cannot_move_anomaly_to_other_org_attendance(
            self, supervisor_user, own_anomaly, other_att):
        response = _client(supervisor_user).patch(
            f'{ANOMALY_URL}{own_anomaly.pk}/', {'attendance': other_att.pk}, format='json')
        assert response.status_code == 400
        own_anomaly.refresh_from_db()
        assert own_anomaly.attendance_id != other_att.pk

    def test_own_org_resolve_still_works(self, supervisor_user, own_anomaly):
        response = _client(supervisor_user).post(
            f'{ANOMALY_URL}{own_anomaly.pk}/resolve/', {'notes': '已確認'}, format='json')
        assert response.status_code == 200
        assert response.data['resolved'] is True

    def test_orgless_manager_sees_nothing(self, orgless_manager, own_anomaly, other_anomaly):
        assert _ids(_client(orgless_manager).get(ANOMALY_URL)) == set()


class TestOvertimeCalculate:
    def test_other_org_attendance_is_404_and_creates_nothing(self, manager_user, other_att):
        response = _client(manager_user).post(
            CALC_URL, {'attendance_id': other_att.pk}, format='json')
        assert response.status_code == 404
        assert OvertimeRecord.objects.count() == 0

    def test_own_org_attendance_creates_record(self, manager_user, own_att):
        response = _client(manager_user).post(
            CALC_URL, {'attendance_id': own_att.pk}, format='json')
        assert response.status_code == 201, response.data
        assert len(response.data) == 1
        assert Decimal(response.data[0]['hours']) == Decimal('9.00')

    def test_second_call_returns_existing_record_instead_of_duplicating(
            self, manager_user, own_att):
        client = _client(manager_user)
        first = client.post(CALC_URL, {'attendance_id': own_att.pk}, format='json')
        # Manager adjusts the multiplier; a re-run must not wipe or duplicate it.
        record_id = first.data[0]['id']
        client.patch(f'{RECORD_URL}{record_id}/', {'multiplier': '2.00'}, format='json')

        second = client.post(CALC_URL, {'attendance_id': own_att.pk}, format='json')

        assert first.status_code == 201
        assert second.status_code == 200
        assert [row['id'] for row in second.data] == [record_id]
        assert Decimal(second.data[0]['multiplier']) == Decimal('2.00')
        assert OvertimeRecord.objects.filter(attendance=own_att).count() == 1

    def test_employee_cannot_calculate_a_colleagues_attendance(
            self, organization, branch, employee_role, own_att):
        colleague = _employee(organization, branch, employee_role, 'colleague', 'A2')
        response = _client(colleague.user).post(
            CALC_URL, {'attendance_id': own_att.pk}, format='json')
        assert response.status_code == 404
        assert OvertimeRecord.objects.count() == 0

    def test_employee_can_calculate_own_attendance(self, own_employee, own_att):
        response = _client(own_employee.user).post(
            CALC_URL, {'attendance_id': own_att.pk}, format='json')
        assert response.status_code == 201

    def test_orgless_manager_gets_404(self, orgless_manager, own_att):
        response = _client(orgless_manager).post(
            CALC_URL, {'attendance_id': own_att.pk}, format='json')
        assert response.status_code == 404

    def test_garbage_id_is_404_not_500(self, manager_user):
        response = _client(manager_user).post(
            CALC_URL, {'attendance_id': 'abc'}, format='json')
        assert response.status_code == 404

    def test_superuser_can_calculate_any_org(self, admin_user, other_att):
        response = _client(admin_user).post(
            CALC_URL, {'attendance_id': other_att.pk}, format='json')
        assert response.status_code == 201


class TestOvertimeRuleIsolation:
    @pytest.fixture
    def own_rule(self, organization):
        return OvertimeRule.objects.create(
            organization=organization, overtime_type='regular', multiplier=Decimal('1.34'))

    @pytest.fixture
    def other_rule(self, other_org):
        return OvertimeRule.objects.create(
            organization=other_org, overtime_type='regular', multiplier=Decimal('1.50'))

    def test_list_shows_only_own_org(self, manager_user, own_rule, other_rule):
        response = _client(manager_user).get(RULE_URL)
        assert response.status_code == 200
        assert _ids(response) == {own_rule.pk}

    @pytest.mark.parametrize('method', ['get', 'patch', 'delete'])
    def test_other_org_rule_is_404(self, manager_user, other_rule, method):
        response = getattr(_client(manager_user), method)(
            f'{RULE_URL}{other_rule.pk}/', {'multiplier': '9.99'}, format='json')
        assert response.status_code == 404
        other_rule.refresh_from_db()
        assert other_rule.multiplier == Decimal('1.50')

    def test_cannot_create_rule_for_other_org(self, manager_user, other_org):
        response = _client(manager_user).post(RULE_URL, {
            'organization': other_org.pk, 'overtime_type': 'holiday', 'multiplier': '2.00',
        }, format='json')
        assert response.status_code == 400
        assert 'organization' in response.data
        assert not OvertimeRule.objects.filter(organization=other_org).exists()

    def test_cannot_move_rule_to_other_org(self, manager_user, own_rule, other_org):
        response = _client(manager_user).patch(
            f'{RULE_URL}{own_rule.pk}/', {'organization': other_org.pk}, format='json')
        assert response.status_code == 400
        own_rule.refresh_from_db()
        assert own_rule.organization_id != other_org.pk

    def test_own_org_create_still_works(self, manager_user, organization):
        response = _client(manager_user).post(RULE_URL, {
            'organization': organization.pk, 'overtime_type': 'holiday', 'multiplier': '2.00',
        }, format='json')
        assert response.status_code == 201, response.data

    def test_orgless_manager_sees_nothing(self, orgless_manager, own_rule, other_rule):
        assert _ids(_client(orgless_manager).get(RULE_URL)) == set()
