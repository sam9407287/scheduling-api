"""
Phase 1 schema additions:
  - Employee sensitive attributes (gender / birth_date / height / weight / pattern preference)
  - EmployeeTag (org-scoped tag pool, M2M to employees)
  - EmployeeDataConsent (one-shot consent record + revoke)
  - ScheduleVersion.derived_from (A → B lineage)
  - TeamConstraint (scope × condition × quantifier)

Invariant under test: an employee without an active consent must expose all
sensitive attributes as None to the OR-Tools solver, even if the underlying
columns hold real values.
"""
import pytest
from datetime import date, timedelta
from decimal import Decimal
from django.utils import timezone

from apps.accounts.models import User
from apps.organizations.models import Organization
from apps.employees.models import Employee, EmployeeTag, EmployeeDataConsent
from apps.shifts.models import ShiftTemplate, TeamConstraint
from apps.schedules.models import ScheduleVersion


@pytest.fixture
def org(db):
    return Organization.objects.create(name='Test Org', code='ORG1')


@pytest.fixture
def employee(db, org):
    user = User.objects.create_user(username='emp1', password='pw')
    return Employee.objects.create(
        user=user,
        employee_id='E001',
        organization=org,
        position='nurse',
        hire_date=date(2024, 1, 1),
        gender='male',
        birth_date=date(1990, 5, 1),
        height_cm=Decimal('178.50'),
        weight_kg=Decimal('72.00'),
    )


class TestEmployeeSensitiveFields:
    def test_employee_holds_sensitive_columns(self, employee):
        assert employee.gender == 'male'
        assert employee.height_cm == Decimal('178.50')
        assert employee.weight_kg == Decimal('72.00')

    def test_default_pattern_preference_is_none(self, employee):
        assert employee.shift_pattern_preference == 'none'

    def test_without_consent_solver_sees_nones(self, employee):
        """The single most important invariant of phase 1."""
        assert employee.has_active_data_consent() is False
        attrs = employee.sensitive_attributes_for_solver()
        assert attrs == {
            'gender': None, 'birth_date': None,
            'height_cm': None, 'weight_kg': None,
        }

    def test_with_active_consent_solver_sees_values(self, employee):
        EmployeeDataConsent.objects.create(
            employee=employee, consented_at=timezone.now(),
        )
        attrs = employee.sensitive_attributes_for_solver()
        assert attrs['gender'] == 'male'
        assert attrs['height_cm'] == Decimal('178.50')
        assert attrs['weight_kg'] == Decimal('72.00')
        assert attrs['birth_date'] == date(1990, 5, 1)

    def test_revoked_consent_hides_values_again(self, employee):
        consent = EmployeeDataConsent.objects.create(
            employee=employee, consented_at=timezone.now(),
        )
        consent.revoked_at = timezone.now()
        consent.save()
        assert employee.has_active_data_consent() is False
        assert employee.sensitive_attributes_for_solver()['gender'] is None


class TestEmployeeTag:
    def test_tag_scoped_to_org(self, db, org, employee):
        tag = EmployeeTag.objects.create(
            organization=org, code='can_lift_high', label='可搬高處'
        )
        employee.tags.add(tag)
        assert list(employee.tags.values_list('code', flat=True)) == ['can_lift_high']

    def test_tag_code_unique_per_org(self, db, org):
        EmployeeTag.objects.create(organization=org, code='driver', label='司機')
        with pytest.raises(Exception):
            EmployeeTag.objects.create(organization=org, code='driver', label='Dup')

    def test_same_code_allowed_across_orgs(self, db, org):
        org2 = Organization.objects.create(name='Org 2', code='ORG2')
        EmployeeTag.objects.create(organization=org, code='driver', label='司機')
        # Same code on a different org must be allowed.
        EmployeeTag.objects.create(organization=org2, code='driver', label='司機')


class TestScheduleVersionLineage:
    def test_derive_a_from_b(self, db, org):
        b_version = ScheduleVersion.objects.create(
            organization=org, version_label='B-W22',
            version_type='actual',
            period_start=date(2026, 5, 25), period_end=date(2026, 5, 31),
        )
        a_version = ScheduleVersion.objects.create(
            organization=org, version_label='A-W22',
            version_type='legal',
            period_start=date(2026, 5, 25), period_end=date(2026, 5, 31),
            derived_from=b_version,
        )
        assert a_version.derived_from_id == b_version.id
        assert list(b_version.derived_versions.all()) == [a_version]

    def test_b_can_exist_without_a(self, db, org):
        b_version = ScheduleVersion.objects.create(
            organization=org, version_label='B-W22',
            version_type='actual',
            period_start=date(2026, 5, 25), period_end=date(2026, 5, 31),
        )
        assert b_version.derived_from is None


class TestTeamConstraint:
    def test_create_height_constraint(self, db, org):
        shift = ShiftTemplate.objects.create(
            organization=org, name='夜班',
            start_time='22:00', end_time='06:00', min_staff_count=2,
        )
        c = TeamConstraint.objects.create(
            organization=org, shift_template=shift,
            scope_time_of_day='night',
            condition_type='height_cm', condition_operator='gte',
            condition_value=175,
            quantifier='at_least', quantity=1,
            description='夜班至少 1 名身高 ≥ 175cm',
        )
        assert c.severity == 'hard'  # default
        assert str(c) == '夜班至少 1 名身高 ≥ 175cm'

    def test_constraint_supports_json_value_for_tag(self, db, org):
        c = TeamConstraint.objects.create(
            organization=org,
            condition_type='tag', condition_operator='contains',
            condition_value=['driver', 'bilingual'],
            quantifier='at_least', quantity=1,
        )
        assert c.condition_value == ['driver', 'bilingual']

    def test_constraint_can_be_soft(self, db, org):
        c = TeamConstraint.objects.create(
            organization=org,
            condition_type='gender', condition_operator='ne',
            condition_value='female',
            quantifier='at_least', quantity=1,
            severity='soft',
        )
        assert c.severity == 'soft'
