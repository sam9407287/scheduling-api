"""
Approval workflow tests: unapprove action + approved-version locking.

Product rules (2026-08-06):
- Approved versions are read-only; schedule writes return 409
  `schedule_version_locked`. Editing requires unapprove first.
- Unapprove has NO overlap checks — multiple approved versions coexisting
  (even with identical periods) is normal.
"""
from datetime import date
from decimal import Decimal

import pytest
from rest_framework import status

from apps.audit.models import AuditLog
from apps.employees.models import Employee
from apps.schedules.models import Schedule, ScheduleVersion
from apps.shifts.models import ShiftTemplate

pytestmark = pytest.mark.django_db


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
        start_time='08:00', end_time='16:00',
        min_staff_count=1,
    )


def _version(organization, admin_user, status_value='draft', label='V1'):
    version = ScheduleVersion.objects.create(
        organization=organization,
        version_label=label, version_type='actual',
        period_start=date(2026, 8, 1), period_end=date(2026, 8, 31),
        created_by=admin_user,
    )
    if status_value != 'draft':
        ScheduleVersion.objects.filter(pk=version.pk).update(status=status_value)
        version.refresh_from_db()
    return version


def _schedule(version, employee, shift, day=date(2026, 8, 3)):
    return Schedule.objects.create(
        schedule_version=version, employee=employee,
        shift_template=shift, schedule_date=day,
        expected_hours=Decimal('8.00'), status='assigned',
    )


class TestUnapprove:
    def test_unapprove_returns_version_to_draft(self, admin_api_client, organization, admin_user):
        version = _version(organization, admin_user)
        admin_api_client.post(f'/api/schedules/versions/{version.pk}/approve/')

        response = admin_api_client.post(
            f'/api/schedules/versions/{version.pk}/unapprove/',
            {'reason': '需要修改班表'}, format='json',
        )
        assert response.status_code == status.HTTP_200_OK
        assert response.data['status'] == 'draft'
        assert response.data['approved_by'] is None
        assert response.data['approved_at'] is None

    def test_unapprove_without_reason_succeeds(self, admin_api_client, organization, admin_user):
        """一鍵取消：reason 選填（2026-09-01 前端要求）。"""
        version = _version(organization, admin_user, status_value='approved')
        response = admin_api_client.post(
            f'/api/schedules/versions/{version.pk}/unapprove/', {}, format='json',
        )
        assert response.status_code == status.HTTP_200_OK
        assert response.data['status'] == 'draft'

    def test_unapprove_non_approved_returns_409(self, admin_api_client, organization, admin_user):
        version = _version(organization, admin_user)  # draft
        response = admin_api_client.post(
            f'/api/schedules/versions/{version.pk}/unapprove/',
            {'reason': 'x'}, format='json',
        )
        assert response.status_code == status.HTTP_409_CONFLICT
        assert response.data['code'] == 'unapprove_conflict'

    def test_double_unapprove_second_returns_409(self, admin_api_client, organization, admin_user):
        version = _version(organization, admin_user, status_value='approved')
        first = admin_api_client.post(
            f'/api/schedules/versions/{version.pk}/unapprove/', {'reason': 'x'}, format='json',
        )
        second = admin_api_client.post(
            f'/api/schedules/versions/{version.pk}/unapprove/', {'reason': 'x'}, format='json',
        )
        assert first.status_code == status.HTTP_200_OK
        assert second.status_code == status.HTTP_409_CONFLICT

    def test_unapprove_writes_audit_log_with_reason(self, admin_api_client, organization, admin_user):
        version = _version(organization, admin_user, status_value='approved')
        admin_api_client.post(
            f'/api/schedules/versions/{version.pk}/unapprove/',
            {'reason': '排班內容有誤'}, format='json',
        )
        log = AuditLog.objects.filter(
            action='cancel', model_name='ScheduleVersion', record_id=version.pk,
        ).first()
        assert log is not None
        assert log.changes == {'reason': '排班內容有誤'}
        assert log.user == admin_user

    def test_unapprove_forbidden_for_employee(self, employee_api_client, organization, admin_user):
        version = _version(organization, admin_user, status_value='approved')
        response = employee_api_client.post(
            f'/api/schedules/versions/{version.pk}/unapprove/', {'reason': 'x'}, format='json',
        )
        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_no_overlap_check_on_approve(self, admin_api_client, organization, admin_user):
        """兩個期間完全相同的版本可以同時 approved——重疊是正常狀態。"""
        v1 = _version(organization, admin_user, label='A')
        v2 = _version(organization, admin_user, label='B')
        r1 = admin_api_client.post(f'/api/schedules/versions/{v1.pk}/approve/')
        r2 = admin_api_client.post(f'/api/schedules/versions/{v2.pk}/approve/')
        assert r1.status_code == status.HTTP_200_OK
        assert r2.status_code == status.HTTP_200_OK


class TestApprovedVersionLock:
    LOCKED = 'schedule_version_locked'

    def _payload(self, version, employee, shift, day='2026-08-05'):
        return {
            'schedule_version': version.pk,
            'employee': employee.pk,
            'shift_template': shift.pk,
            'schedule_date': day,
            'expected_hours': '8.00',
            'status': 'assigned',
        }

    def test_create_in_approved_version_409(self, admin_api_client, organization, admin_user, employee, shift):
        version = _version(organization, admin_user, status_value='approved')
        response = admin_api_client.post(
            '/api/schedules/schedules/', self._payload(version, employee, shift), format='json',
        )
        assert response.status_code == status.HTTP_409_CONFLICT
        assert response.data['code'] == self.LOCKED

    def test_update_delete_in_approved_version_409(self, admin_api_client, organization, admin_user, employee, shift):
        version = _version(organization, admin_user)
        row = _schedule(version, employee, shift)
        ScheduleVersion.objects.filter(pk=version.pk).update(status='approved')

        patch = admin_api_client.patch(
            f'/api/schedules/schedules/{row.pk}/', {'notes': 'x'}, format='json',
        )
        put = admin_api_client.put(
            f'/api/schedules/schedules/{row.pk}/',
            self._payload(version, employee, shift, day='2026-08-03'), format='json',
        )
        delete = admin_api_client.delete(f'/api/schedules/schedules/{row.pk}/')

        for response in (patch, put, delete):
            assert response.status_code == status.HTTP_409_CONFLICT
            assert response.data['code'] == self.LOCKED

    def test_moving_schedule_into_approved_version_409(self, admin_api_client, organization, admin_user, employee, shift):
        draft = _version(organization, admin_user, label='draft')
        approved = _version(organization, admin_user, status_value='approved', label='appr')
        row = _schedule(draft, employee, shift)
        response = admin_api_client.patch(
            f'/api/schedules/schedules/{row.pk}/',
            {'schedule_version': approved.pk}, format='json',
        )
        assert response.status_code == status.HTTP_409_CONFLICT

    def test_published_and_archived_also_locked(self, admin_api_client, organization, admin_user, employee, shift):
        for status_value in ('published', 'archived'):
            version = _version(organization, admin_user, status_value=status_value, label=status_value)
            response = admin_api_client.post(
                '/api/schedules/schedules/', self._payload(version, employee, shift), format='json',
            )
            assert response.status_code == status.HTTP_409_CONFLICT

    def test_unapprove_unlocks_editing(self, admin_api_client, organization, admin_user, employee, shift):
        version = _version(organization, admin_user, status_value='approved')
        admin_api_client.post(
            f'/api/schedules/versions/{version.pk}/unapprove/', {'reason': 'x'}, format='json',
        )
        response = admin_api_client.post(
            '/api/schedules/schedules/', self._payload(version, employee, shift), format='json',
        )
        assert response.status_code == status.HTTP_201_CREATED

    def test_draft_version_still_editable(self, admin_api_client, organization, admin_user, employee, shift):
        version = _version(organization, admin_user)
        response = admin_api_client.post(
            '/api/schedules/schedules/', self._payload(version, employee, shift), format='json',
        )
        assert response.status_code == status.HTTP_201_CREATED

    def test_patch_version_status_is_ignored(self, admin_api_client, organization, admin_user):
        """status 只能經 approve/unapprove action 變更，PATCH 繞不過狀態機。"""
        version = _version(organization, admin_user)
        response = admin_api_client.patch(
            f'/api/schedules/versions/{version.pk}/', {'status': 'approved'}, format='json',
        )
        assert response.status_code == status.HTTP_200_OK
        version.refresh_from_db()
        assert version.status == 'draft'
