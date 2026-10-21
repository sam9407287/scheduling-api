"""
一例一休（勞基法 §36，PM 2026-10-13「一例一休進去」；2026-10-22 實作）。

- OR-Tools 勞基法模式：每 ISO 週最多上 5 天（hard），標 soft 時可被重罰放寬
- 派生合規版：從上滿 7 天的 B 派生出每人每週 ≤ 5 天的 A
- 一鍵合規檢查：休 1 天 → soft（休息日出勤需加班費）、休 0 天 → hard（例假出勤）
- LLM 驗證層：同一週第 6 天的班被丟掉（既有班次也算）
- 設定端點接受 soft_rule_types=['weekly_rest_days']
"""
from datetime import date, time, timedelta
from decimal import Decimal

import pytest

from apps.accounts.models import User
from apps.ai_engine.providers.base import ScheduleRequest
from apps.ai_engine.providers.ortools_provider import ORToolsProvider
from apps.compliance.engine import check_schedule_violations
from apps.employees.models import Employee
from apps.schedules.models import Schedule, ScheduleVersion
from apps.shifts.models import ShiftTemplate

pytestmark = pytest.mark.django_db

MON = date(2026, 11, 2)
SUN = date(2026, 11, 8)
SHIFT = {'id': 1, 'name': '早', 'start_time': '08:00', 'end_time': '16:00',
         'break_minutes': 60, 'min_staff_count': 1, 'required_certifications': [],
         'employee_priorities': []}


def _employee(org, branch, role, code):
    user = User.objects.create_user(username=f'wr_{code}', password='pw', role=role,
                                    organization=org, branch=branch)
    return Employee.objects.create(user=user, employee_id=code, organization=org,
                                   branch=branch, position='nurse', hire_date=date(2024, 1, 1))


def _shift(org):
    return ShiftTemplate.objects.create(organization=org, name='早班', start_time=time(8, 0),
                                        end_time=time(16, 0), min_staff_count=1, break_minutes=60)


def _version(org, branch, user, start=MON, end=SUN):
    return ScheduleVersion.objects.create(organization=org, branch=branch, version_label='WR',
                                          version_type='actual', period_start=start,
                                          period_end=end, created_by=user)


def _request(employee_ids, constraints, **kw):
    return ScheduleRequest(
        organization_id=1, branch_id=None, period_start=MON, period_end=SUN,
        employees=[{'id': e, 'employee_id': f'E{e}', 'agreed_hours_per_week': 40.0,
                    'certifications': []} for e in employee_ids],
        shift_templates=[SHIFT], constraints=constraints, preferences={}, **kw,
    )


def _days_worked(result, emp_id):
    return {a['date'] for a in result.assignments if a['employee_id'] == emp_id}


class TestSolver:
    def test_hard_max_five_days_per_week(self):
        # 兩人輪 7 天、每天至少 1 人：沒有一例一休時公平性會讓某人上 6-7 天
        result = ORToolsProvider().generate_schedule(_request(
            [1, 2], {'max_weekly_hours': 999, 'min_rest_hours': 0, 'max_consecutive_days': 7},
            enforce_labor_law=True,
        ))
        assert result.success, result.message
        assert len(_days_worked(result, 1)) <= 5
        assert len(_days_worked(result, 2)) <= 5
        assert len({a['date'] for a in result.assignments}) == 7

    def test_single_employee_infeasible_unless_soft(self):
        base = {'max_weekly_hours': 999, 'min_rest_hours': 0, 'max_consecutive_days': 7}
        hard = ORToolsProvider().generate_schedule(_request([1], base, enforce_labor_law=True))
        assert not hard.success
        soft = ORToolsProvider().generate_schedule(_request(
            [1], base, enforce_labor_law=True, soft_labor_rules=['weekly_rest_days'],
        ))
        assert soft.success, soft.message
        assert len(_days_worked(soft, 1)) == 7

    def test_constraint_value_is_tunable(self):
        result = ORToolsProvider().generate_schedule(_request(
            [1], {'max_weekly_hours': 999, 'min_rest_hours': 0, 'max_consecutive_days': 7,
                  'weekly_rest_days': 0},
            enforce_labor_law=True,
        ))
        assert result.success
        assert len(_days_worked(result, 1)) == 7

    def test_plain_generate_without_labor_law_is_unchanged(self):
        result = ORToolsProvider().generate_schedule(_request([1], {}))
        assert result.success
        assert len(_days_worked(result, 1)) == 7


class TestDeriveLegal:
    def test_derived_a_has_at_most_five_days_per_week(
            self, admin_api_client, organization, branch, admin_user, employee_role):
        e1 = _employee(organization, branch, employee_role, 'D1')
        e2 = _employee(organization, branch, employee_role, 'D2')
        shift = _shift(organization)
        b = _version(organization, branch, admin_user)
        for i in range(7):  # e1 上滿 7 天，e2 完全沒班
            Schedule.objects.create(schedule_version=b, employee=e1, shift_template=shift,
                                    schedule_date=MON + timedelta(days=i),
                                    expected_hours=Decimal('7'))
        resp = admin_api_client.post(
            f'/api/schedules/versions/{b.id}/derive-legal/',
            {'consume_token': False,
             'constraints': {'max_weekly_hours': 999, 'min_rest_hours': 0}},
            format='json',
        )
        assert resp.status_code == 201, resp.content
        a_id = resp.json()['legal_version_id']
        per_emp = {}
        for s in Schedule.objects.filter(schedule_version_id=a_id):
            per_emp.setdefault(s.employee_id, set()).add(s.schedule_date)
        assert len(per_emp[e1.pk]) <= 5
        assert len(per_emp.get(e2.pk, ())) >= 2
        # 每天都有人（min_staff=1）
        assert set().union(*per_emp.values()) == {MON + timedelta(days=i) for i in range(7)}


class TestComplianceSeverity:
    def _fill(self, org, branch, user, role, n_days):
        emp = _employee(org, branch, role, f'S{n_days}')
        shift = _shift(org)
        version = _version(org, branch, user)
        for i in range(n_days):
            Schedule.objects.create(schedule_version=version, employee=emp, shift_template=shift,
                                    schedule_date=MON + timedelta(days=i),
                                    expected_hours=Decimal('7'))
        return version

    def test_six_days_is_soft_reminder(self, organization, branch, admin_user, employee_role):
        version = self._fill(organization, branch, admin_user, employee_role, 6)
        hits = [v for v in check_schedule_violations(version) if v.rule == 'weekly_rest_days']
        assert len(hits) == 1
        assert hits[0].severity == 'soft'
        assert hits[0].detail['rest_days'] == 1
        assert hits[0].detail['regular_day_off_worked'] is False

    def test_seven_days_is_hard(self, organization, branch, admin_user, employee_role):
        version = self._fill(organization, branch, admin_user, employee_role, 7)
        hits = [v for v in check_schedule_violations(version) if v.rule == 'weekly_rest_days']
        assert len(hits) == 1
        assert hits[0].severity == 'hard'
        assert hits[0].detail['regular_day_off_worked'] is True

    def test_five_days_clean(self, organization, branch, admin_user, employee_role):
        version = self._fill(organization, branch, admin_user, employee_role, 5)
        assert not [v for v in check_schedule_violations(version) if v.rule == 'weekly_rest_days']

    def test_org_can_mark_rule_soft(self, manager_api_client):
        resp = manager_api_client.patch('/api/compliance/settings/',
                                        {'soft_rule_types': ['weekly_rest_days']}, format='json')
        assert resp.status_code == 200, resp.content
        assert resp.json()['soft_rule_types'] == ['weekly_rest_days']


class TestLLMValidation:
    def test_sixth_day_in_week_rejected(self, organization, branch, admin_user, employee_role):
        from apps.ai_engine import llm_scheduler
        emp = _employee(organization, branch, employee_role, 'LW1')
        shift = _shift(organization)
        version = _version(organization, branch, admin_user)
        # 週一、二已在版本裡，模型再給週三～週日 5 天 → 只能收 3 天（合計 5）
        for i in range(2):
            Schedule.objects.create(schedule_version=version, employee=emp, shift_template=shift,
                                    schedule_date=MON + timedelta(days=i),
                                    expected_hours=Decimal('7'))
        _, lookups = llm_scheduler.build_context(version, MON, SUN)
        raw = [{'employee_id': emp.pk, 'date': (MON + timedelta(days=i)).isoformat(),
                'shift_id': shift.pk} for i in range(2, 7)]
        valid, rejected = llm_scheduler.validate_assignments(raw, lookups, version)
        assert len(valid) == 3
        assert len(rejected) == 2
        # 週日先被七休一（第 7 連續日）擋下，週六再被一例一休（第 6 天）擋下
        reasons = sorted(r['reason'] for r in rejected)
        assert any('一例一休' in r for r in reasons)
        assert all('七休一' in r or '一例一休' in r for r in reasons)

    def test_prompt_states_the_rule(self):
        from apps.ai_engine import llm_scheduler
        prompt = llm_scheduler.build_system_prompt()
        assert '最多只能上 5 天' in prompt and '一例一休' in prompt
