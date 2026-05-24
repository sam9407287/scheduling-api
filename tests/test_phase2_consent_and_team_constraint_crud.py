"""
Phase 2 — REST CRUD for EmployeeDataConsent and TeamConstraint.

These endpoints unblock frontend work on the first-login consent dialog
and the team-constraint builder UI. They are deliberately thin — the
heavy logic lives in the consent-gated solver invariant (Phase 1
test_phase1_team_constraints) and the compiler (Phase 1 same file).

What's verified here:

  * Employee can POST their own consent and the row is created with
    `revoked_at = None`, `is_active = True`.
  * Manager cannot create consent on behalf of an employee (PDPA self-
    consent rule, returns 403).
  * Employee can DELETE their own consent (revoked_at stamped, row
    preserved for audit).
  * Re-POST after revocation reactivates the record (same employee, no
    duplicate rows).
  * GET returns 204 when no consent exists.
  * TeamConstraint full CRUD (create / list / patch / delete).
  * TeamConstraint queryset is org-scoped for non-superusers.
  * TeamConstraint validates condition_value shape per condition_type.
"""
import pytest  # noqa: F401  (conftest fixtures)
from datetime import date, time
from decimal import Decimal
from rest_framework.test import APIClient
from rest_framework.authtoken.models import Token

from apps.accounts.models import User
from apps.employees.models import Employee, EmployeeDataConsent
from apps.shifts.models import TeamConstraint


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def employee(db, organization, branch, employee_user):
    return Employee.objects.create(
        user=employee_user, employee_id='EMP-PC1',
        organization=organization, branch=branch,
        position='nurse', hire_date=date(2024, 1, 1),
    )


@pytest.fixture
def employee_client(employee_user):
    c = APIClient()
    c.force_authenticate(user=employee_user)
    return c


# ===========================================================================
# EmployeeDataConsent endpoints
# ===========================================================================

class TestDataConsent:
    def test_get_returns_204_when_no_consent(
        self, db, employee, employee_client
    ):
        resp = employee_client.get(f'/api/employees/employees/{employee.id}/data-consent/')
        assert resp.status_code == 204

    def test_employee_can_post_own_consent(
        self, db, employee, employee_client
    ):
        resp = employee_client.post(
            f'/api/employees/employees/{employee.id}/data-consent/',
            {'consent_version': '1.0'}, format='json',
        )
        assert resp.status_code == 201, resp.content
        body = resp.json()
        assert body['is_active'] is True
        assert body['consent_version'] == '1.0'
        assert body['revoked_at'] is None
        # Row exists.
        assert EmployeeDataConsent.objects.filter(employee=employee).count() == 1

    def test_manager_cannot_consent_on_behalf(
        self, db, employee, admin_api_client
    ):
        """admin_api_client is a superuser; this guards the PDPA "self-
        consent" requirement irrespective of seniority."""
        resp = admin_api_client.post(
            f'/api/employees/employees/{employee.id}/data-consent/',
            {'consent_version': '1.0'}, format='json',
        )
        assert resp.status_code == 403
        assert EmployeeDataConsent.objects.filter(employee=employee).count() == 0

    def test_manager_can_get_consent_status(
        self, db, employee, employee_client, admin_api_client
    ):
        employee_client.post(
            f'/api/employees/employees/{employee.id}/data-consent/',
            {'consent_version': '1.0'}, format='json',
        )
        resp = admin_api_client.get(
            f'/api/employees/employees/{employee.id}/data-consent/'
        )
        assert resp.status_code == 200
        assert resp.json()['is_active'] is True

    def test_employee_can_revoke_via_delete(
        self, db, employee, employee_client
    ):
        employee_client.post(
            f'/api/employees/employees/{employee.id}/data-consent/',
            {'consent_version': '1.0'}, format='json',
        )
        resp = employee_client.delete(
            f'/api/employees/employees/{employee.id}/data-consent/'
        )
        assert resp.status_code == 200, resp.content
        body = resp.json()
        assert body['is_active'] is False
        assert body['revoked_at'] is not None
        # Row preserved — audit trail.
        assert EmployeeDataConsent.objects.filter(employee=employee).count() == 1

    def test_repost_after_revoke_reactivates(
        self, db, employee, employee_client
    ):
        employee_client.post(
            f'/api/employees/employees/{employee.id}/data-consent/',
            {'consent_version': '1.0'}, format='json',
        )
        employee_client.delete(
            f'/api/employees/employees/{employee.id}/data-consent/'
        )
        resp = employee_client.post(
            f'/api/employees/employees/{employee.id}/data-consent/',
            {'consent_version': '1.1'}, format='json',
        )
        assert resp.status_code == 200, resp.content  # update, not create
        body = resp.json()
        assert body['is_active'] is True
        assert body['consent_version'] == '1.1'
        assert body['revoked_at'] is None
        # Still one row.
        assert EmployeeDataConsent.objects.filter(employee=employee).count() == 1

    def test_solver_invariant_holds_after_revoke(
        self, db, employee, employee_client
    ):
        """End-to-end: employee opts in → solver sees attributes → revokes
        → solver loses them again. Re-fetch the employee between calls so
        Django's reverse-OneToOne cache doesn't mask the DB state — this
        mirrors how production sees one fresh instance per request anyway.
        """
        from apps.ai_engine.views import _employee_attributes_for_solver
        employee.gender = 'male'
        employee.height_cm = Decimal('180')
        employee.save()

        employee_client.post(
            f'/api/employees/employees/{employee.id}/data-consent/',
            {'consent_version': '1.0'}, format='json',
        )
        emp = Employee.objects.get(id=employee.id)
        attrs = _employee_attributes_for_solver(emp)
        assert attrs['gender'] == 'male'
        assert attrs['height_cm'] == 180.0

        employee_client.delete(
            f'/api/employees/employees/{employee.id}/data-consent/'
        )
        emp = Employee.objects.get(id=employee.id)
        attrs = _employee_attributes_for_solver(emp)
        assert attrs['gender'] is None
        assert attrs['height_cm'] is None


# ===========================================================================
# TeamConstraint CRUD
# ===========================================================================

class TestTeamConstraintCRUD:
    def test_manager_can_create(self, db, admin_api_client, organization):
        payload = {
            'organization': organization.id,
            'branch': None, 'shift_template': None,
            'scope_time_of_day': 'night',
            'condition_type': 'gender', 'condition_operator': 'eq',
            'condition_value': 'male',
            'quantifier': 'at_least', 'quantity': 1,
            'description': 'Night shift ≥ 1 male',
            'severity': 'hard', 'is_active': True,
        }
        resp = admin_api_client.post(
            '/api/shifts/team-constraints/', payload, format='json',
        )
        assert resp.status_code == 201, resp.content
        assert TeamConstraint.objects.count() == 1

    def test_list_filters_by_org_for_non_superuser(
        self, db, manager_api_client, organization
    ):
        from apps.organizations.models import Organization
        other = Organization.objects.create(name='Other', code='OTH')
        TeamConstraint.objects.create(
            organization=organization,
            condition_type='gender', condition_operator='eq',
            condition_value='male', quantifier='at_least', quantity=1,
        )
        TeamConstraint.objects.create(
            organization=other,
            condition_type='gender', condition_operator='eq',
            condition_value='female', quantifier='at_least', quantity=1,
        )
        resp = manager_api_client.get('/api/shifts/team-constraints/')
        assert resp.status_code == 200
        ids = [tc['organization'] for tc in resp.json()['results']]
        assert all(o == organization.id for o in ids)

    def test_patch_updates_quantity(
        self, db, admin_api_client, organization
    ):
        tc = TeamConstraint.objects.create(
            organization=organization,
            condition_type='gender', condition_operator='eq',
            condition_value='male', quantifier='at_least', quantity=1,
        )
        resp = admin_api_client.patch(
            f'/api/shifts/team-constraints/{tc.id}/',
            {'quantity': 3}, format='json',
        )
        assert resp.status_code == 200, resp.content
        tc.refresh_from_db()
        assert tc.quantity == 3

    def test_delete_removes_row(self, db, admin_api_client, organization):
        tc = TeamConstraint.objects.create(
            organization=organization,
            condition_type='gender', condition_operator='eq',
            condition_value='male', quantifier='at_least', quantity=1,
        )
        resp = admin_api_client.delete(
            f'/api/shifts/team-constraints/{tc.id}/'
        )
        assert resp.status_code == 204
        assert TeamConstraint.objects.filter(id=tc.id).count() == 0

    def test_validation_tag_value_must_be_list(
        self, db, admin_api_client, organization
    ):
        resp = admin_api_client.post(
            '/api/shifts/team-constraints/',
            {
                'organization': organization.id,
                'condition_type': 'tag', 'condition_operator': 'contains',
                'condition_value': 'driver',  # invalid — must be list
                'quantifier': 'at_least', 'quantity': 1,
            },
            format='json',
        )
        assert resp.status_code == 400
        assert 'condition_value' in resp.json()

    def test_validation_numeric_value_must_be_numeric(
        self, db, admin_api_client, organization
    ):
        resp = admin_api_client.post(
            '/api/shifts/team-constraints/',
            {
                'organization': organization.id,
                'condition_type': 'height_cm', 'condition_operator': 'gte',
                'condition_value': 'tall',  # invalid
                'quantifier': 'at_least', 'quantity': 1,
            },
            format='json',
        )
        assert resp.status_code == 400

    def test_filter_branch_null(
        self, db, admin_api_client, organization, branch
    ):
        tc_org = TeamConstraint.objects.create(
            organization=organization,
            condition_type='gender', condition_operator='eq',
            condition_value='male', quantifier='at_least', quantity=1,
        )
        TeamConstraint.objects.create(
            organization=organization, branch=branch,
            condition_type='gender', condition_operator='eq',
            condition_value='female', quantifier='at_least', quantity=1,
        )
        resp = admin_api_client.get('/api/shifts/team-constraints/?branch=null')
        assert resp.status_code == 200
        ids = [tc['id'] for tc in resp.json()['results']]
        assert tc_org.id in ids
        # The branch-scoped one is excluded.
        assert len(ids) == 1
