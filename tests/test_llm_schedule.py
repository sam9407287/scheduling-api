"""
Pure-LLM scheduling endpoint tests (2026-09-23 product decision).

The model API is mocked; what's under test is the guardrail layer —
validation drops hallucinated rows, persistence lands in the roster,
approved versions stay locked, billing charges as 'generate'.
"""
from datetime import date
from decimal import Decimal

import pytest
from rest_framework import status
from rest_framework.test import APIClient

from apps.employees.models import Employee
from apps.schedules.models import Schedule, ScheduleVersion
from apps.shifts.models import ShiftTemplate

pytestmark = pytest.mark.django_db

URL = '/api/ai/schedule/llm-generate/'


@pytest.fixture
def admin_api_client(admin_user):
    client = APIClient()
    client.force_authenticate(user=admin_user)
    return client


@pytest.fixture
def employee(employee_user, organization, branch):
    return Employee.objects.create(
        user=employee_user, employee_id='LLM1',
        organization=organization, branch=branch,
        position='nurse', hire_date=date(2024, 1, 1),
    )


@pytest.fixture
def shift(organization):
    return ShiftTemplate.objects.create(
        organization=organization, name='早班', start_time='08:00',
        end_time='16:00', break_minutes=60, min_staff_count=1,
    )


@pytest.fixture
def version(organization, admin_user):
    return ScheduleVersion.objects.create(
        organization=organization, version_label='LLM測試', version_type='actual',
        period_start=date(2026, 10, 1), period_end=date(2026, 10, 7),
        created_by=admin_user,
    )


def _mock_llm(monkeypatch, assignments):
    from apps.ai_engine import llm_provider
    monkeypatch.setattr(
        llm_provider, 'generate_json',
        lambda system, user, timeout=90: ({'assignments': assignments}, 'mock-model'),
    )


class TestLLMGenerate:
    def test_valid_rows_persist_to_roster(self, admin_api_client, employee, shift,
                                          version, monkeypatch):
        _mock_llm(monkeypatch, [
            {'employee_id': employee.pk, 'date': '2026-10-01', 'shift_id': shift.pk},
            {'employee_id': employee.pk, 'date': '2026-10-02', 'shift_id': shift.pk},
        ])
        response = admin_api_client.post(URL, {
            'schedule_version': version.pk, 'consume_token': False,
        }, format='json')
        assert response.status_code == status.HTTP_201_CREATED
        assert response.data['created_count'] == 2
        assert response.data['engine'] == 'llm'
        rows = Schedule.objects.filter(schedule_version=version)
        assert rows.count() == 2
        assert all(r.notes == 'AI 排班' and r.status == 'assigned' for r in rows)

    def test_hallucinated_rows_rejected(self, admin_api_client, employee, shift,
                                        version, monkeypatch):
        _mock_llm(monkeypatch, [
            {'employee_id': employee.pk, 'date': '2026-10-01', 'shift_id': shift.pk},
            {'employee_id': 99999, 'date': '2026-10-01', 'shift_id': shift.pk},   # 幽靈員工
            {'employee_id': employee.pk, 'date': '2026-12-25', 'shift_id': shift.pk},  # 期間外
            {'employee_id': employee.pk, 'date': '2026-10-01', 'shift_id': shift.pk},  # 重複
        ])
        response = admin_api_client.post(URL, {
            'schedule_version': version.pk, 'consume_token': False,
        }, format='json')
        assert response.data['created_count'] == 1
        assert response.data['rejected_count'] == 3
        assert Schedule.objects.filter(schedule_version=version).count() == 1

    def test_certification_guard(self, admin_api_client, employee, version,
                                 organization, monkeypatch):
        from apps.employees.models import Certification
        cert = Certification.objects.create(name='護理師', code='RN-LLM')
        guarded = ShiftTemplate.objects.create(
            organization=organization, name='專業班', start_time='08:00',
            end_time='16:00', break_minutes=60, min_staff_count=1,
        )
        guarded.required_certifications.add(cert)
        _mock_llm(monkeypatch, [
            {'employee_id': employee.pk, 'date': '2026-10-01', 'shift_id': guarded.pk},
        ])
        response = admin_api_client.post(URL, {
            'schedule_version': version.pk, 'consume_token': False,
        }, format='json')
        assert response.data['created_count'] == 0
        assert '證照' in response.data['rejected'][0]['reason']

    def test_leave_day_guard(self, admin_api_client, employee, shift, version,
                             organization, admin_user, monkeypatch):
        from apps.leaves.models import LeaveRequest
        LeaveRequest.objects.create(
            organization=organization, employee=employee, leave_type='personal',
            start_date=date(2026, 10, 3), end_date=date(2026, 10, 3),
            status='approved', created_by=admin_user,
        )
        _mock_llm(monkeypatch, [
            {'employee_id': employee.pk, 'date': '2026-10-03', 'shift_id': shift.pk},
        ])
        response = admin_api_client.post(URL, {
            'schedule_version': version.pk, 'consume_token': False,
        }, format='json')
        assert response.data['created_count'] == 0
        assert '請假' in response.data['rejected'][0]['reason']

    def test_approved_version_locked(self, admin_api_client, employee, shift,
                                     version, monkeypatch):
        ScheduleVersion.objects.filter(pk=version.pk).update(status='approved')
        response = admin_api_client.post(URL, {
            'schedule_version': version.pk, 'consume_token': False,
        }, format='json')
        assert response.status_code == status.HTTP_409_CONFLICT
        assert response.data['code'] == 'schedule_version_locked'

    def test_missing_api_key_503(self, admin_api_client, employee, shift,
                                 version, monkeypatch):
        for key in ('LLM_API_KEY', 'GEMINI_API_KEY'):
            monkeypatch.delenv(key, raising=False)
        response = admin_api_client.post(URL, {
            'schedule_version': version.pk, 'consume_token': False,
        }, format='json')
        assert response.status_code == status.HTTP_503_SERVICE_UNAVAILABLE
        assert response.data['code'] == 'llm_not_configured'

    def test_billing_charged_as_generate(self, admin_api_client, employee, shift,
                                         version, monkeypatch):
        _mock_llm(monkeypatch, [
            {'employee_id': employee.pk, 'date': '2026-10-01', 'shift_id': shift.pk},
        ])
        response = admin_api_client.post(URL, {
            'schedule_version': version.pk,  # consume_token 預設 true
        }, format='json')
        assert response.status_code == status.HTTP_201_CREATED
        assert response.data['billing']['billing_mode'] == 'generate'
        assert response.data['billing']['tokens_charged'] == 10

    def test_coverage_warning_reported(self, admin_api_client, employee, shift,
                                       version, monkeypatch):
        _mock_llm(monkeypatch, [])  # 模型什麼都沒排
        response = admin_api_client.post(URL, {
            'schedule_version': version.pk, 'consume_token': False,
        }, format='json')
        assert response.data['created_count'] == 0
        assert any('只排到 0/1' in w for w in response.data['warnings'])
