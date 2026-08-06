"""
Day-overview endpoint tests: informational cross-version view of one date.
No conflict detection by design — the manager just sees what other rosters
already scheduled that day.
"""
from datetime import date
from decimal import Decimal

import pytest
from rest_framework import status

from apps.employees.models import Employee
from apps.organizations.models import Organization
from apps.schedules.models import Schedule, ScheduleVersion
from apps.shifts.models import ShiftTemplate

pytestmark = pytest.mark.django_db

DAY = date(2026, 8, 3)
URL = f'/api/schedules/day-overview/?date={DAY.isoformat()}'


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


def _version(organization, user, label, status_value='draft'):
    version = ScheduleVersion.objects.create(
        organization=organization, version_label=label, version_type='actual',
        period_start=date(2026, 8, 1), period_end=date(2026, 8, 31),
        created_by=user,
    )
    if status_value != 'draft':
        ScheduleVersion.objects.filter(pk=version.pk).update(status=status_value)
    return version


def _schedule(version, employee, shift, day=DAY):
    return Schedule.objects.create(
        schedule_version=version, employee=employee,
        shift_template=shift, schedule_date=day,
        expected_hours=Decimal('8.00'), status='assigned',
    )


class TestDayOverview:
    def test_requires_date(self, admin_api_client):
        response = admin_api_client.get('/api/schedules/day-overview/')
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_groups_schedules_by_version(self, admin_api_client, organization,
                                         admin_user, employee, shift):
        v1 = _version(organization, admin_user, 'A')
        v2 = _version(organization, admin_user, 'B', status_value='approved')
        _schedule(v1, employee, shift)
        _schedule(v2, employee, shift)
        _schedule(v1, employee, shift, day=date(2026, 8, 4))  # 其他日期不入列

        response = admin_api_client.get(URL)
        assert response.status_code == status.HTTP_200_OK
        assert response.data['date'] == DAY.isoformat()
        entries = response.data['entries']
        assert {e['version']['id'] for e in entries} == {v1.pk, v2.pk}
        for entry in entries:
            assert len(entry['schedules']) == 1
            assert entry['version']['version_label'] in ('A', 'B')

    def test_exclude_version_param(self, admin_api_client, organization,
                                   admin_user, employee, shift):
        v1 = _version(organization, admin_user, 'A')
        v2 = _version(organization, admin_user, 'B')
        _schedule(v1, employee, shift)
        _schedule(v2, employee, shift)

        response = admin_api_client.get(f'{URL}&exclude_version={v1.pk}')
        assert {e['version']['id'] for e in response.data['entries']} == {v2.pk}

    def test_employee_filter(self, admin_api_client, organization, admin_user,
                             employee, shift, manager_user, branch):
        other = Employee.objects.create(
            user=manager_user, employee_id='EMP2',
            organization=organization, branch=branch,
            position='nurse', hire_date=date(2024, 1, 1),
        )
        v1 = _version(organization, admin_user, 'A')
        _schedule(v1, employee, shift)
        _schedule(v1, other, shift)

        response = admin_api_client.get(f'{URL}&employee={employee.pk}')
        entries = response.data['entries']
        assert len(entries) == 1
        assert len(entries[0]['schedules']) == 1

    def test_archived_excluded_by_default(self, admin_api_client, organization,
                                          admin_user, employee, shift):
        archived = _version(organization, admin_user, 'old', status_value='archived')
        _schedule(archived, employee, shift)

        default = admin_api_client.get(URL)
        assert default.data['entries'] == []

        included = admin_api_client.get(f'{URL}&include_archived=true')
        assert {e['version']['id'] for e in included.data['entries']} == {archived.pk}

    def test_org_isolation(self, supervisor_api_client, employee, shift):
        other_org = Organization.objects.create(
            name='他機構', code='OTHER2', address='x', phone='02-1', email='o2@x.com',
        )
        other_version = ScheduleVersion.objects.create(
            organization=other_org, version_label='X', version_type='actual',
            period_start=date(2026, 8, 1), period_end=date(2026, 8, 31),
        )
        # 他機構的班次不可見
        response = supervisor_api_client.get(URL)
        assert response.status_code == status.HTTP_200_OK
        assert all(
            e['version']['id'] != other_version.pk for e in response.data['entries']
        )
