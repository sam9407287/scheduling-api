"""
機構公休日（PM#1 排休）tests — 2026-09-23.

診所固定週休（如週日公休）：
- 設定：/api/compliance/settings/ 的 weekly_closed_days（0=週一…6=週日）
- 手動排班可排但一鍵合規檢查回 soft `org_closed_day` 提醒（警告哲學）
- OR-Tools solver 硬性避開公休日，且公休日不套 min_staff（不會 INFEASIBLE）
- LLM 排班：公休日 assignments 被驗證層拒絕，缺口警告跳過公休日
"""
from datetime import date, time, timedelta
from decimal import Decimal

import pytest
from rest_framework import status

from apps.ai_engine.providers.base import ScheduleRequest
from apps.ai_engine.providers.ortools_provider import ORToolsProvider
from apps.compliance.engine import check_schedule_violations
from apps.compliance.models import OrgComplianceSettings
from apps.employees.models import Employee
from apps.schedules.models import Schedule, ScheduleVersion
from apps.shifts.models import ShiftTemplate

pytestmark = pytest.mark.django_db

MONDAY = date(2026, 8, 3)   # weekday 0
SUNDAY = date(2026, 8, 9)   # weekday 6


def _employee(org, branch, role, code):
    from apps.accounts.models import User
    user = User.objects.create_user(
        username=f'closed_{code}', password='pw', role=role,
        organization=org, branch=branch,
    )
    return Employee.objects.create(
        user=user, employee_id=code, organization=org, branch=branch,
        position='nurse', hire_date=date(2024, 1, 1),
    )


def _shift(org, name='早班', start=time(8, 0), end=time(16, 0)):
    return ShiftTemplate.objects.create(
        organization=org, name=name, start_time=start, end_time=end,
        min_staff_count=1, break_minutes=60,
    )


def _version(org, branch, user):
    return ScheduleVersion.objects.create(
        organization=org, branch=branch, version_label='CD',
        version_type='actual', period_start=MONDAY, period_end=SUNDAY,
        created_by=user,
    )


class TestSettingsEndpoint:
    def test_default_empty(self, manager_api_client):
        resp = manager_api_client.get('/api/compliance/settings/')
        assert resp.status_code == 200
        assert resp.json()['weekly_closed_days'] == []

    def test_patch_and_normalize(self, manager_api_client):
        resp = manager_api_client.patch(
            '/api/compliance/settings/',
            {'weekly_closed_days': [6, 6, 0]}, format='json',
        )
        assert resp.status_code == 200
        assert resp.json()['weekly_closed_days'] == [0, 6]  # 去重＋排序

    def test_patch_rejects_out_of_range(self, manager_api_client):
        resp = manager_api_client.patch(
            '/api/compliance/settings/',
            {'weekly_closed_days': [7]}, format='json',
        )
        assert resp.status_code == 400

    def test_patch_rejects_all_week_closed(self, manager_api_client):
        resp = manager_api_client.patch(
            '/api/compliance/settings/',
            {'weekly_closed_days': [0, 1, 2, 3, 4, 5, 6]}, format='json',
        )
        assert resp.status_code == 400


class TestComplianceSoftWarning:
    def test_closed_day_schedule_warns_soft(self, organization, branch, admin_user, employee_role):
        OrgComplianceSettings.objects.create(
            organization=organization, weekly_closed_days=[6],  # 週日公休
        )
        emp = _employee(organization, branch, employee_role, 'CD1')
        shift = _shift(organization)
        version = _version(organization, branch, admin_user)
        Schedule.objects.create(
            schedule_version=version, employee=emp, shift_template=shift,
            schedule_date=SUNDAY, expected_hours=Decimal('8'),
        )
        violations = [v for v in check_schedule_violations(version)
                      if v.rule == 'org_closed_day']
        assert len(violations) == 1
        assert violations[0].severity == 'soft'
        assert violations[0].schedule_date == SUNDAY.isoformat()
        assert violations[0].detail['weekday_label'] == '週日'

    def test_no_settings_no_warning(self, organization, branch, admin_user, employee_role):
        emp = _employee(organization, branch, employee_role, 'CD2')
        shift = _shift(organization)
        version = _version(organization, branch, admin_user)
        Schedule.objects.create(
            schedule_version=version, employee=emp, shift_template=shift,
            schedule_date=SUNDAY, expected_hours=Decimal('8'),
        )
        assert not [v for v in check_schedule_violations(version)
                    if v.rule == 'org_closed_day']


class TestSolverAvoidsClosedDays:
    def test_closed_days_have_no_assignments_and_feasible(
        self, organization, branch, employee_role,
    ):
        emps = [_employee(organization, branch, employee_role, f'CS{i}') for i in range(2)]
        shift = _shift(organization)
        request = ScheduleRequest(
            organization_id=organization.id, branch_id=branch.id,
            period_start=MONDAY, period_end=SUNDAY,
            employees=[
                {'id': e.id, 'employee_id': e.employee_id,
                 'agreed_hours_per_week': 40.0, 'certifications': [],
                 'unavailable_dates': [], 'availability': {}}
                for e in emps
            ],
            shift_templates=[{
                'id': shift.id, 'name': shift.name,
                'start_time': shift.start_time.isoformat(),
                'end_time': shift.end_time.isoformat(),
                'break_minutes': shift.break_minutes,
                'min_staff_count': 1,
                'required_certifications': [], 'employee_priorities': [],
            }],
            constraints={'closed_weekdays': [5, 6]},  # 週六日公休
            preferences={},
        )
        result = ORToolsProvider().generate_schedule(request)
        assert result.success, result.message
        weekdays = {date.fromisoformat(a['date']).weekday() for a in result.assignments}
        assert not weekdays & {5, 6}
        assert weekdays  # 平日有排班


class TestLLMValidation:
    def test_closed_day_rows_rejected_and_warnings_skip(
        self, organization, branch, admin_user, employee_role,
    ):
        OrgComplianceSettings.objects.create(
            organization=organization, weekly_closed_days=[6],
        )
        emp = _employee(organization, branch, employee_role, 'CL1')
        shift = _shift(organization)
        version = _version(organization, branch, admin_user)

        from apps.ai_engine.llm_scheduler import (
            build_context, coverage_warnings, validate_assignments,
        )
        payload, lookups = build_context(version, MONDAY, SUNDAY)
        assert payload['org']['closed_weekdays'] == [6]

        raw = [
            {'employee_id': emp.pk, 'date': MONDAY.isoformat(), 'shift_id': shift.pk},
            {'employee_id': emp.pk, 'date': SUNDAY.isoformat(), 'shift_id': shift.pk},
        ]
        valid, rejected = validate_assignments(raw, lookups, version)
        assert len(valid) == 1 and valid[0]['date'] == MONDAY.isoformat()
        assert len(rejected) == 1 and rejected[0]['reason'] == '機構公休日'

        # 公休日不算 min_staff 缺口
        warnings = coverage_warnings(valid, lookups)
        assert not any(SUNDAY.isoformat() in w for w in warnings)
        assert any(w.startswith('2026-08-04') for w in warnings)  # 平日缺口照報
