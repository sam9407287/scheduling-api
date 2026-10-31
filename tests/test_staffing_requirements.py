"""
每日人力需求規則（「每天需要幾個人」，PM 2026-10-13 截圖；2026-10-22 實作）。

- 純函式：date > weekday > every-day、分店專屬 > 全機構、無規則回 None
- REST CRUD：/api/shifts/staffing-requirements/、機構隔離、date/weekday 擇一、max ≥ min
- OR-Tools：星期規則覆蓋 min_staff、max_staff 硬上限、全日總人數規則
- 一鍵合規檢查回 coverage（需要 vs 已排）
- LLM：缺口警告用版本每日班別計畫人數（2026-10-31 起）；超過 max_staff 的列被驗證層丟掉
"""
from datetime import date, time, timedelta
from decimal import Decimal

import pytest
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.ai_engine.providers.base import ScheduleRequest
from apps.ai_engine.providers.ortools_provider import ORToolsProvider
from apps.employees.models import Employee
from apps.schedules.models import Schedule, ScheduleVersion
from apps.shifts.models import ShiftTemplate, StaffingRequirement
from apps.shifts.staffing import (
    coverage_report, resolve_staffing, staffing_plan,
)

pytestmark = pytest.mark.django_db

MON = date(2026, 11, 2)   # weekday 0
SUN = date(2026, 11, 8)   # weekday 6
URL = '/api/shifts/staffing-requirements/'


def _employee(org, branch, role, code):
    user = User.objects.create_user(
        username=f'staff_{code}', password='pw', role=role,
        organization=org, branch=branch,
    )
    return Employee.objects.create(
        user=user, employee_id=code, organization=org, branch=branch,
        position='nurse', hire_date=date(2024, 1, 1),
    )


def _shift(org, name='早班', start=time(8, 0), end=time(16, 0), min_staff=1):
    return ShiftTemplate.objects.create(
        organization=org, name=name, start_time=start, end_time=end,
        min_staff_count=min_staff, break_minutes=60,
    )


def _version(org, branch, user, start=MON, end=SUN):
    return ScheduleVersion.objects.create(
        organization=org, branch=branch, version_label='SR',
        version_type='actual', period_start=start, period_end=end,
        created_by=user,
    )


# ---------------------------------------------------------------------------
# Pure resolver
# ---------------------------------------------------------------------------
class TestResolver:
    def test_specificity_order(self):
        rules = [
            {'id': 1, 'branch_id': None, 'shift_template_id': 7, 'weekday': None, 'date': None,
             'min_staff': 2, 'max_staff': None},
            {'id': 2, 'branch_id': None, 'shift_template_id': 7, 'weekday': 5, 'date': None,
             'min_staff': 1, 'max_staff': None},
            {'id': 3, 'branch_id': None, 'shift_template_id': 7, 'weekday': None,
             'date': '2026-11-07', 'min_staff': 4, 'max_staff': 6},
        ]
        assert resolve_staffing(rules, date(2026, 11, 4), 7)['min_staff'] == 2   # 週三：每天
        assert resolve_staffing(rules, date(2026, 11, 14), 7)['min_staff'] == 1  # 週六：星期
        hit = resolve_staffing(rules, date(2026, 11, 7), 7)                      # 週六但有特定日期
        assert (hit['min_staff'], hit['max_staff'], hit['rule_id']) == (4, 6, 3)
        assert resolve_staffing(rules, date(2026, 11, 4), 99) is None            # 別的班別
        assert resolve_staffing(rules, date(2026, 11, 4), None) is None          # 無全日規則

    def test_branch_specific_beats_org_wide_at_same_specificity(self):
        rules = [
            {'id': 1, 'branch_id': None, 'shift_template_id': None, 'weekday': 0, 'date': None,
             'min_staff': 3, 'max_staff': None},
            {'id': 2, 'branch_id': 5, 'shift_template_id': None, 'weekday': 0, 'date': None,
             'min_staff': 1, 'max_staff': None},
        ]
        assert resolve_staffing(rules, MON, None)['rule_id'] == 2

    def test_plan_expands_only_matching_days(self):
        rules = [{'id': 1, 'branch_id': None, 'shift_template_id': 7, 'weekday': 6,
                  'date': None, 'min_staff': 0, 'max_staff': 0}]
        plan = staffing_plan(rules, MON, SUN, [7])
        assert plan == [{'date': SUN.isoformat(), 'shift_id': 7, 'min_staff': 0, 'max_staff': 0}]

    def test_coverage_report_statuses(self):
        shifts = [{'id': 7, 'name': '早班', 'min_staff_count': 2}]
        plan = [{'date': '2026-11-03', 'shift_id': 7, 'min_staff': 1, 'max_staff': 1},
                {'date': '2026-11-03', 'shift_id': None, 'min_staff': 1, 'max_staff': None}]
        assignments = [
            {'employee_id': 1, 'date': '2026-11-02', 'shift_id': 7},   # 需要 2 只有 1 → under
            {'employee_id': 1, 'date': '2026-11-03', 'shift_id': 7},
            {'employee_id': 2, 'date': '2026-11-03', 'shift_id': 7},   # 上限 1 排了 2 → over
        ]
        rows = coverage_report(period_start=MON, period_end=date(2026, 11, 3), shifts=shifts,
                               assignments=assignments, plan=plan)
        by = {(r['date'], r['shift_template_id']): r for r in rows}
        assert by[('2026-11-02', 7)]['status'] == 'under'
        assert by[('2026-11-02', 7)]['source'] == 'template'
        assert by[('2026-11-03', 7)]['status'] == 'over'
        assert by[('2026-11-03', None)] == {
            'date': '2026-11-03', 'shift_template_id': None, 'shift_name': None,
            'required_min': 1, 'required_max': None, 'assigned': 2, 'status': 'ok',
            'source': 'rule',
        }


# ---------------------------------------------------------------------------
# REST
# ---------------------------------------------------------------------------
class TestCrud:
    def test_create_defaults_org_to_caller(self, manager_api_client, organization):
        shift = _shift(organization)
        resp = manager_api_client.post(URL, {
            'shift_template': shift.id, 'weekday': 5, 'min_staff': 1, 'max_staff': 2,
            'note': '週六人少',
        }, format='json')
        assert resp.status_code == 201, resp.content
        body = resp.json()
        assert body['organization'] == organization.id
        assert body['weekday_label'] == '週六'
        assert body['shift_template_name'] == shift.name

    def test_superuser_with_org_defaults_org_when_omitted(self, admin_user, organization):
        """superuser 綁有機構時，沒傳 organization 也用自己的機構（前端只在 user.organization 存在時才送）。"""
        from rest_framework.test import APIClient
        c = APIClient(); c.force_authenticate(user=admin_user)
        resp = c.post(URL, {'min_staff': 1}, format='json')
        assert resp.status_code == 201, resp.content
        assert resp.json()['organization'] == admin_user.organization_id == organization.id

    def test_day_total_rule_without_shift(self, manager_api_client):
        resp = manager_api_client.post(URL, {'min_staff': 3, 'max_staff': 5}, format='json')
        assert resp.status_code == 201, resp.content
        assert resp.json()['shift_template'] is None
        assert resp.json()['weekday'] is None and resp.json()['date'] is None

    def test_rejects_date_and_weekday_together(self, manager_api_client):
        resp = manager_api_client.post(URL, {
            'weekday': 1, 'date': '2026-11-03', 'min_staff': 1,
        }, format='json')
        assert resp.status_code == 400
        assert 'date' in resp.json()

    def test_rejects_max_below_min(self, manager_api_client):
        resp = manager_api_client.post(URL, {'min_staff': 3, 'max_staff': 2}, format='json')
        assert resp.status_code == 400
        assert 'max_staff' in resp.json()

    def test_rejects_shift_from_other_org(self, manager_api_client):
        from apps.organizations.models import Organization
        other = Organization.objects.create(name='別家', code='OTHER')
        foreign_shift = _shift(other)
        resp = manager_api_client.post(URL, {
            'shift_template': foreign_shift.id, 'min_staff': 1,
        }, format='json')
        assert resp.status_code == 400
        assert 'shift_template' in resp.json()

    def test_org_isolation_on_list(self, manager_api_client, organization):
        from apps.organizations.models import Organization
        other = Organization.objects.create(name='別家', code='OTHER2')
        StaffingRequirement.objects.create(organization=other, min_staff=9)
        mine = StaffingRequirement.objects.create(organization=organization, min_staff=2)
        resp = manager_api_client.get(URL)
        assert resp.status_code == 200
        ids = [r['id'] for r in resp.json()['results']]
        assert ids == [mine.id]

    def test_supervisor_cannot_write(self, supervisor_api_client):
        resp = supervisor_api_client.post(URL, {'min_staff': 1}, format='json')
        assert resp.status_code == 403

    def test_filter_shift_template_null(self, manager_api_client, organization):
        shift = _shift(organization)
        StaffingRequirement.objects.create(organization=organization, shift_template=shift, min_staff=1)
        day_rule = StaffingRequirement.objects.create(organization=organization, min_staff=4)
        resp = manager_api_client.get(URL + '?shift_template=null')
        assert [r['id'] for r in resp.json()['results']] == [day_rule.id]


# ---------------------------------------------------------------------------
# OR-Tools solver
# ---------------------------------------------------------------------------
def _solve(employees, shifts, constraints, start=MON, end=SUN):
    req = ScheduleRequest(
        organization_id=1, branch_id=None, period_start=start, period_end=end,
        employees=[{'id': e, 'employee_id': f'E{e}', 'agreed_hours_per_week': 40.0,
                    'certifications': []} for e in employees],
        shift_templates=shifts, constraints=constraints, preferences={},
    )
    return ORToolsProvider().generate_schedule(req)


def _count(result, day, shift_id=None):
    rows = [a for a in result.assignments if a['date'] == day.isoformat()
            and (shift_id is None or a['shift_id'] == shift_id)]
    return len(rows)


class TestSolver:
    SHIFTS = [{'id': 1, 'name': '早', 'start_time': '08:00', 'end_time': '16:00',
               'break_minutes': 60, 'min_staff_count': 1, 'required_certifications': [],
               'employee_priorities': []}]

    def test_weekday_rule_raises_min(self):
        plan = [{'date': SUN.isoformat(), 'shift_id': 1, 'min_staff': 3, 'max_staff': None}]
        result = _solve([1, 2, 3, 4], self.SHIFTS, {'staffing_requirements': plan})
        assert result.success, result.message
        assert _count(result, SUN, 1) >= 3
        assert _count(result, MON, 1) >= 1

    def test_max_staff_is_hard_cap(self):
        # 沒上限時公平性會把 4 個人都排上；上限 1 之後每天恰好 1 人
        plan = [{'date': (MON + timedelta(days=i)).isoformat(), 'shift_id': 1,
                 'min_staff': 1, 'max_staff': 1} for i in range(7)]
        result = _solve([1, 2, 3, 4], self.SHIFTS, {'staffing_requirements': plan})
        assert result.success, result.message
        for i in range(7):
            assert _count(result, MON + timedelta(days=i), 1) == 1

    def test_zero_min_allows_empty_day(self):
        plan = [{'date': SUN.isoformat(), 'shift_id': 1, 'min_staff': 0, 'max_staff': 0}]
        result = _solve([1, 2], self.SHIFTS, {'staffing_requirements': plan})
        assert result.success
        assert _count(result, SUN, 1) == 0

    def test_day_total_rule_counts_distinct_people(self):
        shifts = self.SHIFTS + [{'id': 2, 'name': '晚', 'start_time': '16:00', 'end_time': '22:00',
                                 'break_minutes': 0, 'min_staff_count': 1,
                                 'required_certifications': [], 'employee_priorities': []}]
        # 當日總人數最多 2：兩個班各要 1 人，所以不能用 3 個不同的人
        plan = [{'date': (MON + timedelta(days=i)).isoformat(), 'shift_id': None,
                 'min_staff': 2, 'max_staff': 2} for i in range(7)]
        result = _solve([1, 2, 3], shifts, {'staffing_requirements': plan})
        assert result.success, result.message
        for i in range(7):
            day = (MON + timedelta(days=i)).isoformat()
            people = {a['employee_id'] for a in result.assignments if a['date'] == day}
            assert len(people) == 2

    def test_infeasible_when_min_exceeds_headcount(self):
        plan = [{'date': MON.isoformat(), 'shift_id': 1, 'min_staff': 5, 'max_staff': None}]
        result = _solve([1, 2], self.SHIFTS, {'staffing_requirements': plan})
        assert not result.success


class TestGenerateEndpointLoadsRules:
    def test_generate_applies_weekday_rule(self, admin_api_client, organization, branch,
                                           employee_role):
        for i in range(4):
            _employee(organization, branch, employee_role, f'G{i}')
        shift = _shift(organization)
        StaffingRequirement.objects.create(
            organization=organization, shift_template=shift, weekday=6, min_staff=3,
        )
        resp = admin_api_client.post('/api/ai/schedule/generate/', {
            'organization_id': organization.id, 'period_start': MON.isoformat(),
            'period_end': SUN.isoformat(), 'consume_token': False,
        }, format='json')
        assert resp.status_code == 200, resp.content
        rows = [a for a in resp.json()['assignments'] if a['date'] == SUN.isoformat()]
        assert len(rows) >= 3


# ---------------------------------------------------------------------------
# Compliance coverage
# ---------------------------------------------------------------------------
class TestCheckComplianceCoverage:
    def test_coverage_in_response(self, admin_api_client, organization, branch, admin_user,
                                  employee_role):
        e1 = _employee(organization, branch, employee_role, 'C1')
        shift = _shift(organization, min_staff=2)
        StaffingRequirement.objects.create(
            organization=organization, shift_template=shift, date=MON, min_staff=1,
        )
        version = _version(organization, branch, admin_user, start=MON, end=MON + timedelta(days=1))
        Schedule.objects.create(schedule_version=version, employee=e1, shift_template=shift,
                                schedule_date=MON, expected_hours=Decimal('7'))
        resp = admin_api_client.post(f'/api/schedules/versions/{version.id}/check-compliance/',
                                     {}, format='json')
        assert resp.status_code == 200, resp.content
        body = resp.json()
        cov = {(c['date'], c['shift_template_id']): c for c in body['coverage']}
        assert cov[(MON.isoformat(), shift.id)]['status'] == 'ok'          # 規則降到 1
        assert cov[(MON.isoformat(), shift.id)]['source'] == 'rule'
        nxt = (MON + timedelta(days=1)).isoformat()
        assert cov[(nxt, shift.id)]['status'] == 'under'                   # 模板 2，排 0
        assert body['coverage_summary'] == {'under': 1, 'over': 0, 'ok': 1}


# ---------------------------------------------------------------------------
# LLM path
# ---------------------------------------------------------------------------
class TestLLMPath:
    @pytest.fixture
    def admin_client(self, admin_user):
        c = APIClient()
        c.force_authenticate(user=admin_user)
        return c

    def _mock(self, monkeypatch, assignments):
        from apps.ai_engine import llm_provider
        monkeypatch.setattr(
            llm_provider, 'generate_json',
            lambda system, user, timeout=90: ({'assignments': assignments}, 'mock'),
        )

    def test_context_carries_rules_and_warnings_use_them(
            self, admin_client, organization, branch, admin_user, employee_role, monkeypatch):
        e1 = _employee(organization, branch, employee_role, 'L1')
        _employee(organization, branch, employee_role, 'L2')
        shift = _shift(organization, min_staff=1)
        version = _version(organization, branch, admin_user, start=MON, end=MON)
        # 2026-10-31：LLM 的人數只來自版本每日班別計畫（機構規則不再進 LLM）
        from apps.schedules.models import ScheduleVersionShiftPlan
        ScheduleVersionShiftPlan.objects.create(
            schedule_version=version, date=MON, shift_template=shift, min_staff=2)
        captured = {}
        from apps.ai_engine import llm_provider

        def fake(system, user, timeout=90):
            captured['system'], captured['user'] = system, user
            return {'assignments': [{'employee_id': e1.pk, 'date': MON.isoformat(),
                                     'shift_id': shift.pk}]}, 'mock'
        monkeypatch.setattr(llm_provider, 'generate_json', fake)
        resp = admin_client.post('/api/ai/schedule/llm-generate/', {
            'schedule_version': version.id, 'consume_token': False,
        }, format='json')
        assert resp.status_code == 201, resp.content
        assert 'staffing_requirements' in captured['user']
        assert '一例一休' in captured['system']
        assert any('只排到 1/2 人' in w for w in resp.json()['warnings'])

    def test_rows_over_max_staff_are_rejected(
            self, admin_client, organization, branch, admin_user, employee_role, monkeypatch):
        e1 = _employee(organization, branch, employee_role, 'M1')
        e2 = _employee(organization, branch, employee_role, 'M2')
        shift = _shift(organization, min_staff=1)
        version = _version(organization, branch, admin_user, start=MON, end=MON)
        from apps.schedules.models import ScheduleVersionShiftPlan
        ScheduleVersionShiftPlan.objects.create(
            schedule_version=version, date=MON, shift_template=shift, min_staff=1, max_staff=1)
        self._mock(monkeypatch, [
            {'employee_id': e1.pk, 'date': MON.isoformat(), 'shift_id': shift.pk},
            {'employee_id': e2.pk, 'date': MON.isoformat(), 'shift_id': shift.pk},
        ])
        resp = admin_client.post('/api/ai/schedule/llm-generate/', {
            'schedule_version': version.id, 'consume_token': False,
        }, format='json')
        assert resp.status_code == 201, resp.content
        body = resp.json()
        assert body['created_count'] == 1
        assert body['rejected_count'] == 1
        assert '人數上限' in body['rejected'][0]['reason']
