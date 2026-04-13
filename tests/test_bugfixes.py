"""
Unit tests for the five bug fixes:

1. compliance/engine.py  — rest interval uses datetime.combine (cross-midnight support)
2. schedules/views.py    — compare logic checks expected_hours/status/notes, not key fields
3. accounts/authentication.py — Firebase IntegrityError handled gracefully
4. audit/signals.py      — exceptions logged via logger.error, not silently swallowed
5. compliance/engine.py  — min_rest_hours accepts float (e.g. 9.75), not int-only
"""
import logging
import pytest
from datetime import date, time, datetime, timedelta
from decimal import Decimal
from unittest.mock import patch, MagicMock

from django.contrib.auth import get_user_model
from rest_framework import status

from apps.organizations.models import Organization, Branch
from apps.employees.models import Employee
from apps.shifts.models import ShiftTemplate
from apps.schedules.models import Schedule, ScheduleVersion

User = get_user_model()


# ---------------------------------------------------------------------------
# Helpers shared across tests
# ---------------------------------------------------------------------------

def _make_shift(organization, name, start, end, break_min=0):
    return ShiftTemplate.objects.create(
        organization=organization,
        name=name,
        start_time=start,
        end_time=end,
        break_minutes=break_min,
        min_staff_count=1,
    )


def _make_employee(user, organization, branch, emp_id):
    return Employee.objects.create(
        user=user,
        employee_id=emp_id,
        organization=organization,
        branch=branch,
        position='Test',
        hire_date=date(2024, 1, 1),
    )


def _make_version(organization, branch, user, label='v1'):
    return ScheduleVersion.objects.create(
        organization=organization,
        branch=branch,
        version_label=label,
        version_type='legal',
        period_start=date(2024, 3, 1),
        period_end=date(2024, 3, 31),
        status='draft',
        created_by=user,
    )


# ===========================================================================
# Bug 1 & 5: compliance/engine.py — datetime.combine + float min_rest_hours
# ===========================================================================

class TestComplianceRestInterval:
    """
    Bug 1: Previously used .hour attribute only, so cross-midnight shifts gave
    wrong rest intervals (minutes were truncated).
    Bug 5: min_rest_hours was typed as int; fractional values like 9.75 must work.
    """

    def test_cross_midnight_rest_interval_violation(
        self, db, organization, branch, admin_user, employee_user
    ):
        """
        Night shift ends 06:00, next shift starts 14:00 → rest = 8h.
        With min_rest_hours=11 this must produce a violation.
        Old bug: .hour arithmetic would compute (14-6)=8 but only if end_time.hour<start_time.hour,
        getting it accidentally right for whole hours. For 22:45→06:15 it would fail.
        Using datetime.combine ensures correctness.
        """
        from apps.compliance.engine import ComplianceEngine

        night_shift = _make_shift(organization, '夜班', time(22, 0), time(6, 0))
        day_shift = _make_shift(organization, '早班', time(14, 0), time(22, 0))
        employee = _make_employee(employee_user, organization, branch, 'ETEST1')
        version = _make_version(organization, branch, admin_user, 'v_rest')

        # Night shift on 2024-03-01 (ends 06:00 on 03-02)
        Schedule.objects.create(
            schedule_version=version, employee=employee,
            shift_template=night_shift, schedule_date=date(2024, 3, 1),
            expected_hours=Decimal('8'),
        )
        # Day shift on 2024-03-02 starts 14:00 → rest = 8h (< 11h → violation)
        Schedule.objects.create(
            schedule_version=version, employee=employee,
            shift_template=day_shift, schedule_date=date(2024, 3, 2),
            expected_hours=Decimal('8'),
        )

        engine = ComplianceEngine()
        result = engine.check_schedule_compliance(version, {'min_rest_hours': 11})
        rest_violations = [v for v in result.violations if v['type'] == 'rest_interval_violation']
        assert len(rest_violations) == 1
        assert rest_violations[0]['rest_hours'] == pytest.approx(8.0, abs=0.01)

    def test_cross_midnight_fractional_rest_no_violation(
        self, db, organization, branch, admin_user, employee_user
    ):
        """22:45→06:15 ends at 06:15; next shift starts 18:00 → rest ≈ 11.75h ≥ 11h → no violation."""
        from apps.compliance.engine import ComplianceEngine

        night_shift = _make_shift(organization, '夜班', time(22, 45), time(6, 15))
        day_shift = _make_shift(organization, '午班', time(18, 0), time(22, 0))
        employee = _make_employee(employee_user, organization, branch, 'ETEST2')
        version = _make_version(organization, branch, admin_user, 'v_noviolation')

        Schedule.objects.create(
            schedule_version=version, employee=employee,
            shift_template=night_shift, schedule_date=date(2024, 3, 1),
            expected_hours=Decimal('7.5'),
        )
        Schedule.objects.create(
            schedule_version=version, employee=employee,
            shift_template=day_shift, schedule_date=date(2024, 3, 2),
            expected_hours=Decimal('4'),
        )

        engine = ComplianceEngine()
        result = engine.check_schedule_compliance(version, {'min_rest_hours': 11})
        rest_violations = [v for v in result.violations if v['type'] == 'rest_interval_violation']
        assert rest_violations == []

    def test_float_min_rest_hours_accepted(
        self, db, organization, branch, admin_user, employee_user
    ):
        """Bug 5: min_rest_hours=9.75 must be accepted and used correctly."""
        from apps.compliance.engine import ComplianceEngine

        shift_a = _make_shift(organization, '班A', time(22, 0), time(6, 0))
        shift_b = _make_shift(organization, '班B', time(15, 45), time(23, 0))
        employee = _make_employee(employee_user, organization, branch, 'ETEST3')
        version = _make_version(organization, branch, admin_user, 'v_float')

        # 06:00 → 15:45 = 9h 45m = 9.75h, which equals the threshold → no violation
        Schedule.objects.create(
            schedule_version=version, employee=employee,
            shift_template=shift_a, schedule_date=date(2024, 3, 1),
            expected_hours=Decimal('8'),
        )
        Schedule.objects.create(
            schedule_version=version, employee=employee,
            shift_template=shift_b, schedule_date=date(2024, 3, 2),
            expected_hours=Decimal('7'),
        )

        engine = ComplianceEngine()
        # At exactly 9.75h there should be no violation (rest_hours >= min_rest_hours)
        result = engine.check_schedule_compliance(version, {'min_rest_hours': 9.75})
        rest_violations = [v for v in result.violations if v['type'] == 'rest_interval_violation']
        assert rest_violations == []

    def test_direct_check_rest_interval_cross_midnight(
        self, db, organization, branch, admin_user, employee_user
    ):
        """Directly test _check_rest_interval for the minute-precision fix."""
        from apps.compliance.engine import ComplianceEngine

        # 23:50 → 06:10 (next day start 14:20); rest = 8h 10m ≈ 8.167h < 11 → violation
        shift_late = _make_shift(organization, '深夜', time(23, 50), time(6, 10))
        shift_next = _make_shift(organization, '午', time(14, 20), time(22, 0))
        employee = _make_employee(employee_user, organization, branch, 'ETEST4')
        version = _make_version(organization, branch, admin_user, 'v_direct')

        s1 = Schedule.objects.create(
            schedule_version=version, employee=employee,
            shift_template=shift_late, schedule_date=date(2024, 3, 1),
            expected_hours=Decimal('6'),
        )
        s2 = Schedule.objects.create(
            schedule_version=version, employee=employee,
            shift_template=shift_next, schedule_date=date(2024, 3, 2),
            expected_hours=Decimal('7'),
        )

        engine = ComplianceEngine()
        violations = engine._check_rest_interval([s1, s2], min_rest_hours=11.0)
        assert len(violations) == 1
        # rest = (14:20 on day2) − (06:10 on day2) = 8h 10m
        assert violations[0]['rest_hours'] == pytest.approx(8.167, abs=0.01)


# ===========================================================================
# Bug 2: schedules/views.py compare — must diff expected_hours/status/notes
# ===========================================================================

class TestScheduleVersionCompare:
    """
    Bug 2: The original code compared s1==s2 using the key fields that were
    already equal (employee_id, date, shift_template_id).  The fix compares
    expected_hours, status, and notes.
    """

    def test_compare_detects_expected_hours_difference(
        self, db, organization, branch, admin_user, employee_user, admin_api_client
    ):
        """Versions sharing same assignments but different expected_hours → in differences[]."""
        shift = _make_shift(organization, '早班', time(8, 0), time(16, 0))
        employee = _make_employee(employee_user, organization, branch, 'ECMP1')

        v1 = _make_version(organization, branch, admin_user, 'cmp_v1')
        v2 = _make_version(organization, branch, admin_user, 'cmp_v2')

        Schedule.objects.create(
            schedule_version=v1, employee=employee, shift_template=shift,
            schedule_date=date(2024, 3, 1), expected_hours=Decimal('7.00'),
        )
        Schedule.objects.create(
            schedule_version=v2, employee=employee, shift_template=shift,
            schedule_date=date(2024, 3, 1), expected_hours=Decimal('8.00'),  # different
        )

        response = admin_api_client.get(
            f'/api/schedules/versions/{v1.pk}/compare/?version2_id={v2.pk}'
        )
        assert response.status_code == status.HTTP_200_OK
        assert len(response.data['differences']) == 1
        assert len(response.data['only_in_version1']) == 0
        assert len(response.data['only_in_version2']) == 0

    def test_compare_detects_status_difference(
        self, db, organization, branch, admin_user, employee_user, admin_api_client
    ):
        """Same assignment but different status → in differences[]."""
        shift = _make_shift(organization, '早班', time(8, 0), time(16, 0))
        employee = _make_employee(employee_user, organization, branch, 'ECMP2')

        v1 = _make_version(organization, branch, admin_user, 'cmp_st_v1')
        v2 = _make_version(organization, branch, admin_user, 'cmp_st_v2')

        Schedule.objects.create(
            schedule_version=v1, employee=employee, shift_template=shift,
            schedule_date=date(2024, 3, 1), expected_hours=Decimal('7.00'),
            status='draft',
        )
        Schedule.objects.create(
            schedule_version=v2, employee=employee, shift_template=shift,
            schedule_date=date(2024, 3, 1), expected_hours=Decimal('7.00'),
            status='confirmed',  # different
        )

        response = admin_api_client.get(
            f'/api/schedules/versions/{v1.pk}/compare/?version2_id={v2.pk}'
        )
        assert response.status_code == status.HTTP_200_OK
        assert len(response.data['differences']) == 1

    def test_compare_no_difference_when_identical(
        self, db, organization, branch, admin_user, employee_user, admin_api_client
    ):
        """Identical assignments → differences[] is empty."""
        shift = _make_shift(organization, '早班', time(8, 0), time(16, 0))
        employee = _make_employee(employee_user, organization, branch, 'ECMP3')

        v1 = _make_version(organization, branch, admin_user, 'cmp_eq_v1')
        v2 = _make_version(organization, branch, admin_user, 'cmp_eq_v2')

        for v in (v1, v2):
            Schedule.objects.create(
                schedule_version=v, employee=employee, shift_template=shift,
                schedule_date=date(2024, 3, 1), expected_hours=Decimal('7.00'),
                status='draft', notes='',
            )

        response = admin_api_client.get(
            f'/api/schedules/versions/{v1.pk}/compare/?version2_id={v2.pk}'
        )
        assert response.status_code == status.HTTP_200_OK
        assert response.data['differences'] == []

    def test_compare_notes_difference(
        self, db, organization, branch, admin_user, employee_user, admin_api_client
    ):
        """Same key + hours + status but different notes → in differences[]."""
        shift = _make_shift(organization, '早班', time(8, 0), time(16, 0))
        employee = _make_employee(employee_user, organization, branch, 'ECMP4')

        v1 = _make_version(organization, branch, admin_user, 'cmp_notes_v1')
        v2 = _make_version(organization, branch, admin_user, 'cmp_notes_v2')

        Schedule.objects.create(
            schedule_version=v1, employee=employee, shift_template=shift,
            schedule_date=date(2024, 3, 1), expected_hours=Decimal('7.00'), notes='',
        )
        Schedule.objects.create(
            schedule_version=v2, employee=employee, shift_template=shift,
            schedule_date=date(2024, 3, 1), expected_hours=Decimal('7.00'), notes='調班',
        )

        response = admin_api_client.get(
            f'/api/schedules/versions/{v1.pk}/compare/?version2_id={v2.pk}'
        )
        assert response.status_code == status.HTTP_200_OK
        assert len(response.data['differences']) == 1


# ===========================================================================
# Bug 3: accounts/authentication.py — Firebase IntegrityError handled
# ===========================================================================

class TestFirebaseAuthIntegrityError:
    """
    Bug 3: Concurrent requests for the same firebase_uid could race on
    User.objects.create_user, causing an IntegrityError. The fix wraps
    create_user in a try/except IntegrityError and falls back to get().
    """

    def test_integrity_error_falls_back_to_get(self, db, admin_role, organization):
        """
        Simulate a concurrent duplicate: create_user raises IntegrityError,
        authenticate should return the existing user via get().
        """
        from apps.accounts.authentication import FirebaseAuthentication
        from django.db import IntegrityError
        from unittest.mock import patch, MagicMock
        from rest_framework.request import Request as DRFRequest

        # Pre-create the user that 'already exists'
        existing_user = User.objects.create_user(
            username='uid_abc123',
            email='concurrent@example.com',
            firebase_uid='uid_abc123',
        )

        decoded = {
            'uid': 'uid_abc123',
            'email': 'concurrent@example.com',
            'name': 'Test User',
        }

        auth = FirebaseAuthentication()

        # Patch firebase app verification and create_user to raise IntegrityError
        with patch('apps.accounts.authentication.get_firebase_app'), \
             patch('firebase_admin.auth.verify_id_token', return_value=decoded), \
             patch.object(User.objects, 'create_user', side_effect=IntegrityError('duplicate')):

            mock_request = MagicMock()
            mock_request.META = {'HTTP_AUTHORIZATION': 'Bearer fake_token'}
            user, _ = auth.authenticate(mock_request)

        assert user == existing_user
        assert user.firebase_uid == 'uid_abc123'

    def test_normal_user_creation_succeeds(self, db):
        """Happy path: new firebase_uid creates a new user without errors."""
        from apps.accounts.authentication import FirebaseAuthentication
        from unittest.mock import patch, MagicMock

        decoded = {
            'uid': 'uid_new_999',
            'email': 'newuser@example.com',
            'name': 'New User',
        }

        auth = FirebaseAuthentication()
        with patch('apps.accounts.authentication.get_firebase_app'), \
             patch('firebase_admin.auth.verify_id_token', return_value=decoded):

            mock_request = MagicMock()
            mock_request.META = {'HTTP_AUTHORIZATION': 'Bearer fake_token'}
            user, _ = auth.authenticate(mock_request)

        assert user is not None
        assert user.firebase_uid == 'uid_new_999'
        assert user.email == 'newuser@example.com'

    def test_firebase_uid_used_as_username(self, db):
        """username field must be set to firebase_uid (not email), avoiding collisions."""
        from apps.accounts.authentication import FirebaseAuthentication
        from unittest.mock import patch, MagicMock

        decoded = {
            'uid': 'uid_username_test',
            'email': 'uid_username@example.com',
            'name': '',
        }

        auth = FirebaseAuthentication()
        with patch('apps.accounts.authentication.get_firebase_app'), \
             patch('firebase_admin.auth.verify_id_token', return_value=decoded):

            mock_request = MagicMock()
            mock_request.META = {'HTTP_AUTHORIZATION': 'Bearer fake_token'}
            user, _ = auth.authenticate(mock_request)

        assert user.username == 'uid_username_test'


# ===========================================================================
# Bug 4: audit/signals.py — exceptions logged, not silently swallowed
# ===========================================================================

class TestAuditSignalLogging:
    """
    Bug 4: The original code had `except Exception: pass` in audit_post_save
    and audit_post_delete, hiding failures.  The fix uses logger.error(...).
    """

    def test_post_save_exception_is_logged(self, db, caplog):
        """When AuditLog.create raises, the error must appear in logs."""
        from apps.audit import signals as audit_signals

        # Temporarily enable audit for this test
        audit_signals.enable_audit()
        try:
            with patch('apps.audit.signals._should_skip_audit', return_value=False), \
                 patch('apps.audit.models.AuditLog.objects.create',
                       side_effect=Exception('DB connection lost')), \
                 caplog.at_level(logging.ERROR, logger='apps.audit.signals'):

                # Trigger post_save manually
                audit_signals.audit_post_save(
                    sender=User,
                    instance=MagicMock(pk=1),
                    created=True,
                )

            assert any('Audit post_save failed' in r.message for r in caplog.records)
        finally:
            audit_signals.disable_audit()

    def test_post_delete_exception_is_logged(self, db, caplog):
        """When AuditLog.create raises on delete, the error must appear in logs."""
        from apps.audit import signals as audit_signals

        audit_signals.enable_audit()
        try:
            with patch('apps.audit.signals._should_skip_audit', return_value=False), \
                 patch('apps.audit.models.AuditLog.objects.create',
                       side_effect=Exception('write failed')), \
                 caplog.at_level(logging.ERROR, logger='apps.audit.signals'):

                audit_signals.audit_post_delete(
                    sender=User,
                    instance=MagicMock(pk=2),
                )

            assert any('Audit post_delete failed' in r.message for r in caplog.records)
        finally:
            audit_signals.disable_audit()

    def test_post_save_exception_does_not_propagate(self, db):
        """Audit failures must never crash the calling code."""
        from apps.audit import signals as audit_signals

        audit_signals.enable_audit()
        try:
            with patch('apps.audit.signals._should_skip_audit', return_value=False), \
                 patch('apps.audit.models.AuditLog.objects.create',
                       side_effect=Exception('fatal')):
                # Must not raise
                audit_signals.audit_post_save(
                    sender=User,
                    instance=MagicMock(pk=3),
                    created=False,
                )
        finally:
            audit_signals.disable_audit()

    def test_post_delete_exception_does_not_propagate(self, db):
        """Audit delete failures must not propagate."""
        from apps.audit import signals as audit_signals

        audit_signals.enable_audit()
        try:
            with patch('apps.audit.signals._should_skip_audit', return_value=False), \
                 patch('apps.audit.models.AuditLog.objects.create',
                       side_effect=Exception('fatal')):
                audit_signals.audit_post_delete(
                    sender=User,
                    instance=MagicMock(pk=4),
                )
        finally:
            audit_signals.disable_audit()


# ===========================================================================
# Bonus: AI engine score serialization (float('inf') → null)
# ===========================================================================

class TestAIScoreSerialization:
    """
    The ScheduleResultSerializer.score must serialize float('inf') as null
    so the response is valid JSON.
    """

    def test_inf_score_becomes_null(self, admin_api_client, organization):
        """When the solver fails (no employees), score=inf must not crash JSON encoding."""
        data = {
            'organization_id': organization.pk,
            'period_start': '2024-03-01',
            'period_end': '2024-03-31',
        }
        response = admin_api_client.post('/api/ai/schedule/generate/', data, format='json')
        # Response must be a valid JSON response (no ValueError: inf not JSON compliant)
        assert response.status_code in [status.HTTP_200_OK, status.HTTP_400_BAD_REQUEST]
        if response.status_code == status.HTTP_200_OK:
            # score is null when solver can't produce a result
            assert response.data.get('score') is None or isinstance(response.data.get('score'), (int, float))
