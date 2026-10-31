"""
每張班表自己的每日班別計畫（2026-10-31，前端 Frank 提案）。

- 班別模板不再帶人數（API 不回 min_staff_count）
- 計畫端點：GET / PUT 整張 / PUT 單日 / 星期範本展開 / 複製另一張 / 鎖定 409 / 驗證 400
- 計畫日期超出版本期間 → 期間只擴不縮；手動排班長出來的日子用範本補列
- solver explicit：沒列到的 (日期, 班別) 不排；版本沒計畫 → generate / llm 400
- LLM 驗證層丟掉計畫沒開的班；coverage 只看計畫列到的格子
- 派生合規版沿用計畫（沒開的班在 A 裡被拿掉）
"""
from datetime import date, time, timedelta
from decimal import Decimal

import pytest

from apps.accounts.models import User
from apps.ai_engine.providers.base import ScheduleRequest
from apps.ai_engine.providers.ortools_provider import ORToolsProvider
from apps.employees.models import Employee
from apps.schedules.models import Schedule, ScheduleVersion, ScheduleVersionShiftPlan
from apps.shifts.models import ShiftTemplate

pytestmark = pytest.mark.django_db

MON = date(2026, 11, 2)
TUE = date(2026, 11, 3)
SAT = date(2026, 11, 7)
SUN = date(2026, 11, 8)


def _employee(org, branch, role, code):
    user = User.objects.create_user(username=f'sp_{code}', password='pw', role=role,
                                    organization=org, branch=branch)
    return Employee.objects.create(user=user, employee_id=code, organization=org,
                                   branch=branch, position='nurse', hire_date=date(2024, 1, 1))


def _shift(org, name='早班', start=time(8, 0), end=time(16, 0)):
    return ShiftTemplate.objects.create(organization=org, name=name, start_time=start,
                                        end_time=end, break_minutes=60)


def _version(org, branch, user, start=MON, end=SUN, **kw):
    return ScheduleVersion.objects.create(organization=org, branch=branch, version_label='SP',
                                          version_type='actual', period_start=start,
                                          period_end=end, created_by=user, **kw)


def _url(version, tail=''):
    return f'/api/schedules/versions/{version.id}/shift-plan/{tail}'


def _rows(body):
    return {(r['date'], r['shift_template']): (r['min_staff'], r['max_staff']) for r in body['rows']}


# ---------------------------------------------------------------------------
# 班別模板不再帶人數
# ---------------------------------------------------------------------------
class TestTemplateNoHeadcount:
    def test_template_api_has_no_min_staff_count(self, manager_api_client, organization):
        resp = manager_api_client.post('/api/shifts/templates/', {
            'organization': organization.id, 'name': '夜班',
            'start_time': '22:00', 'end_time': '06:00', 'break_minutes': 30,
        }, format='json')
        assert resp.status_code == 201, resp.content
        assert 'min_staff_count' not in resp.json()
        detail = manager_api_client.get(f"/api/shifts/templates/{resp.json()['id']}/").json()
        assert 'min_staff_count' not in detail


# ---------------------------------------------------------------------------
# 計畫端點
# ---------------------------------------------------------------------------
class TestPlanEndpoints:
    def test_get_empty(self, admin_api_client, organization, branch, admin_user):
        v = _version(organization, branch, admin_user)
        body = admin_api_client.get(_url(v)).json()
        assert body['rows'] == [] and body['template'] == {}
        assert body['summary'] == {'rows': 0, 'days_planned': 0, 'days_in_period': 7,
                                   'days_without_plan': 7}

    def test_put_replaces_and_expands_period(self, admin_api_client, organization, branch,
                                             admin_user):
        morning, evening = _shift(organization), _shift(organization, '晚班', time(16, 0), time(23, 0))
        v = _version(organization, branch, admin_user, start=MON, end=MON)
        resp = admin_api_client.put(_url(v), {'rows': [
            {'date': MON.isoformat(), 'shift_template': morning.id, 'min_staff': 2},
            {'date': MON.isoformat(), 'shift_template': evening.id, 'min_staff': 1, 'max_staff': 2},
            {'date': SUN.isoformat(), 'shift_template': morning.id, 'min_staff': 1},
        ]}, format='json')
        assert resp.status_code == 200, resp.content
        body = resp.json()
        assert _rows(body) == {(MON.isoformat(), morning.id): (2, None),
                               (MON.isoformat(), evening.id): (1, 2),
                               (SUN.isoformat(), morning.id): (1, None)}
        assert body['period_end'] == SUN.isoformat()          # 只擴不縮
        assert body['rows'][0]['shift_name'] == '早班'
        # 再 PUT 一次是整張覆寫
        resp = admin_api_client.put(_url(v), {'rows': [
            {'date': TUE.isoformat(), 'shift_template': morning.id, 'min_staff': 1}]}, format='json')
        assert list(_rows(resp.json())) == [(TUE.isoformat(), morning.id)]
        assert resp.json()['period_start'] == MON.isoformat()  # 不縮

    def test_put_validation(self, admin_api_client, organization, branch, admin_user):
        from apps.organizations.models import Organization
        other = Organization.objects.create(name='別家', code='OTHER-SP')
        foreign = _shift(other)
        mine = _shift(organization)
        v = _version(organization, branch, admin_user)
        bad = [
            [{'date': MON.isoformat(), 'shift_template': foreign.id, 'min_staff': 1}],
            [{'date': MON.isoformat(), 'shift_template': mine.id, 'min_staff': 3, 'max_staff': 2}],
            [{'date': 'not-a-date', 'shift_template': mine.id, 'min_staff': 1}],
            [{'date': MON.isoformat(), 'min_staff': 1}],
        ]
        for rows in bad:
            resp = admin_api_client.put(_url(v), {'rows': rows}, format='json')
            assert resp.status_code == 400, (rows, resp.content)
        assert not ScheduleVersionShiftPlan.objects.filter(schedule_version=v).exists()

    def test_put_day_replaces_one_day_only(self, admin_api_client, organization, branch,
                                           admin_user):
        morning, evening = _shift(organization), _shift(organization, '晚班', time(16, 0), time(23, 0))
        v = _version(organization, branch, admin_user)
        admin_api_client.put(_url(v), {'rows': [
            {'date': MON.isoformat(), 'shift_template': morning.id, 'min_staff': 1},
            {'date': TUE.isoformat(), 'shift_template': morning.id, 'min_staff': 1}]}, format='json')
        resp = admin_api_client.put(_url(v, 'day/'), {
            'date': TUE.isoformat(),
            'shifts': [{'shift_template': evening.id, 'min_staff': 3}]}, format='json')
        assert resp.status_code == 200, resp.content
        assert _rows(resp.json()) == {(MON.isoformat(), morning.id): (1, None),
                                      (TUE.isoformat(), evening.id): (3, None)}
        # 空清單 = 那天不開班
        resp = admin_api_client.put(_url(v, 'day/'), {'date': TUE.isoformat(), 'shifts': []},
                                    format='json')
        assert list(_rows(resp.json())) == [(MON.isoformat(), morning.id)]

    def test_apply_template(self, admin_api_client, organization, branch, admin_user):
        morning, evening = _shift(organization), _shift(organization, '晚班', time(16, 0), time(23, 0))
        v = _version(organization, branch, admin_user, start=MON, end=MON)
        # 先手動給 11/2（週一）晚班，驗證 overwrite=false 會保留
        admin_api_client.put(_url(v, 'day/'), {
            'date': MON.isoformat(), 'shifts': [{'shift_template': evening.id, 'min_staff': 9}]},
            format='json')
        template = {'0': [{'shift_template': morning.id, 'min_staff': 2}],
                    '5': [{'shift_template': morning.id, 'min_staff': 1},
                          {'shift_template': evening.id, 'min_staff': 1, 'max_staff': 1}]}
        resp = admin_api_client.post(_url(v, 'apply-template/'), {
            'template': template, 'period_start': MON.isoformat(),
            'period_end': (MON + timedelta(days=13)).isoformat()}, format='json')
        assert resp.status_code == 200, resp.content
        body = resp.json()
        rows = _rows(body)
        assert rows[(MON.isoformat(), evening.id)] == (9, None)        # 保留
        assert (MON.isoformat(), morning.id) not in rows                # 當天已有計畫不覆蓋
        assert rows[('2026-11-09', morning.id)] == (2, None)           # 下週一
        assert rows[(SAT.isoformat(), morning.id)] == (1, None)
        assert rows[(SAT.isoformat(), evening.id)] == (1, 1)
        assert (TUE.isoformat(), morning.id) not in rows                # 範本沒有週二
        assert body['period_end'] == '2026-11-15'
        assert body['template']['0'][0]['shift_template_id'] == morning.id
        assert body['summary']['days_planned'] == 4                      # 11/2、11/7、11/9、11/14
        # overwrite=true 覆蓋 11/2
        resp = admin_api_client.post(_url(v, 'apply-template/'), {
            'template': template, 'period_start': MON.isoformat(),
            'period_end': MON.isoformat(), 'overwrite': True}, format='json')
        rows = _rows(resp.json())
        assert rows[(MON.isoformat(), morning.id)] == (2, None)
        assert (MON.isoformat(), evening.id) not in rows

    def test_apply_template_validation(self, admin_api_client, organization, branch, admin_user):
        morning = _shift(organization)
        v = _version(organization, branch, admin_user)
        for template in ({'7': []}, {'0': [{'shift_template': 999999, 'min_staff': 1}]}, 'x'):
            resp = admin_api_client.post(_url(v, 'apply-template/'), {'template': template},
                                         format='json')
            assert resp.status_code == 400, (template, resp.content)
        resp = admin_api_client.post(_url(v, 'apply-template/'), {
            'template': {'0': [{'shift_template': morning.id}]},
            'period_start': SUN.isoformat(), 'period_end': MON.isoformat()}, format='json')
        assert resp.status_code == 400

    def test_copy_from(self, admin_api_client, organization, branch, admin_user):
        morning = _shift(organization)
        src = _version(organization, branch, admin_user)
        admin_api_client.post(_url(src, 'apply-template/'), {
            'template': {'2': [{'shift_template': morning.id, 'min_staff': 4}]},
            'period_start': MON.isoformat(), 'period_end': SUN.isoformat()}, format='json')
        dst = _version(organization, branch, admin_user, start=date(2026, 12, 1), end=date(2026, 12, 14))
        resp = admin_api_client.post(_url(dst, 'copy-from/'), {'source_version_id': src.id},
                                     format='json')
        assert resp.status_code == 200, resp.content
        rows = _rows(resp.json())
        assert set(rows) == {('2026-12-02', morning.id), ('2026-12-09', morning.id)}  # 兩個週三
        assert rows[('2026-12-02', morning.id)] == (4, None)
        assert resp.json()['template'] == {'2': [{'shift_template_id': morning.id,
                                                  'min_staff': 4, 'max_staff': None}]}

    def test_copy_from_derives_template_when_source_has_rows_only(
            self, admin_api_client, organization, branch, admin_user):
        morning = _shift(organization)
        src = _version(organization, branch, admin_user, start=MON, end=TUE)
        ScheduleVersionShiftPlan.objects.create(schedule_version=src, date=TUE,
                                                shift_template=morning, min_staff=2)
        dst = _version(organization, branch, admin_user, start=MON, end=SUN)
        resp = admin_api_client.post(_url(dst, 'copy-from/'), {'source_version_id': src.id},
                                     format='json')
        assert resp.status_code == 200, resp.content
        assert list(_rows(resp.json())) == [(TUE.isoformat(), morning.id)]

    def test_copy_from_errors(self, admin_api_client, organization, branch, admin_user):
        dst = _version(organization, branch, admin_user)
        empty = _version(organization, branch, admin_user)
        assert admin_api_client.post(_url(dst, 'copy-from/'), {'source_version_id': empty.id},
                                     format='json').status_code == 400
        assert admin_api_client.post(_url(dst, 'copy-from/'), {'source_version_id': 999999},
                                     format='json').status_code == 404

    def test_locked_version_rejects_writes(self, admin_api_client, organization, branch,
                                           admin_user):
        morning = _shift(organization)
        v = _version(organization, branch, admin_user, status='approved')
        calls = [
            ('put', _url(v), {'rows': []}),
            ('put', _url(v, 'day/'), {'date': MON.isoformat(), 'shifts': []}),
            ('post', _url(v, 'apply-template/'), {'template': {'0': [{'shift_template': morning.id}]}}),
            ('post', _url(v, 'copy-from/'), {'source_version_id': v.id}),
        ]
        for method, url, body in calls:
            resp = getattr(admin_api_client, method)(url, body, format='json')
            assert resp.status_code == 409, (url, resp.content)
            assert resp.json()['code'] == 'schedule_version_locked'
        assert admin_api_client.get(_url(v)).status_code == 200   # 讀仍可

    def test_permissions(self, employee_user, supervisor_api_client, organization,
                         branch, admin_user):
        from rest_framework.test import APIClient
        morning = _shift(organization)
        v = _version(organization, branch, admin_user)
        # conftest 的 *_api_client 共用同一個 APIClient，這裡另開一個給員工
        employee_client = APIClient()
        employee_client.force_authenticate(user=employee_user)
        assert employee_client.put(_url(v), {'rows': []}, format='json').status_code == 403
        resp = supervisor_api_client.put(_url(v), {'rows': [
            {'date': MON.isoformat(), 'shift_template': morning.id, 'min_staff': 1}]}, format='json')
        assert resp.status_code == 200, resp.content

    def test_manual_cell_beyond_period_fills_from_template(
            self, admin_api_client, organization, branch, admin_user, employee_role):
        morning = _shift(organization)
        emp = _employee(organization, branch, employee_role, 'X1')
        v = _version(organization, branch, admin_user, start=MON, end=SUN)
        admin_api_client.post(_url(v, 'apply-template/'), {
            'template': {'0': [{'shift_template': morning.id, 'min_staff': 2}]}}, format='json')
        next_mon = MON + timedelta(days=7)
        resp = admin_api_client.post('/api/schedules/schedules/', {
            'schedule_version': v.id, 'employee': emp.id, 'shift_template': morning.id,
            'schedule_date': next_mon.isoformat(), 'expected_hours': '7.00'}, format='json')
        assert resp.status_code == 201, resp.content
        v.refresh_from_db()
        assert v.period_end == next_mon
        row = ScheduleVersionShiftPlan.objects.get(schedule_version=v, date=next_mon)
        assert row.shift_template_id == morning.id and row.min_staff == 2


# ---------------------------------------------------------------------------
# solver explicit 模式
# ---------------------------------------------------------------------------
SHIFTS = [
    {'id': 1, 'name': '早', 'start_time': '08:00', 'end_time': '16:00', 'break_minutes': 60,
     'min_staff_count': 1, 'required_certifications': [], 'employee_priorities': []},
    {'id': 2, 'name': '晚', 'start_time': '16:00', 'end_time': '23:00', 'break_minutes': 0,
     'min_staff_count': 1, 'required_certifications': [], 'employee_priorities': []},
]


class TestSolverExplicit:
    def _solve(self, plan, explicit=True):
        constraints = {'staffing_requirements': plan, 'max_weekly_hours': 999,
                       'min_rest_hours': 0, 'max_consecutive_days': 7}
        if explicit:
            constraints['staffing_mode'] = 'explicit'
        return ORToolsProvider().generate_schedule(ScheduleRequest(
            organization_id=1, branch_id=None, period_start=MON, period_end=SUN,
            employees=[{'id': e, 'employee_id': f'E{e}', 'agreed_hours_per_week': 40.0,
                        'certifications': []} for e in (1, 2)],
            shift_templates=SHIFTS, constraints=constraints, preferences={},
        ))

    def test_unplanned_cells_stay_empty(self):
        plan = [{'date': MON.isoformat(), 'shift_id': 1, 'min_staff': 1, 'max_staff': None},
                {'date': TUE.isoformat(), 'shift_id': 2, 'min_staff': 2, 'max_staff': None}]
        r = self._solve(plan)
        assert r.success, r.message
        cells = {(a['date'], a['shift_id']) for a in r.assignments}
        assert cells == {(MON.isoformat(), 1), (TUE.isoformat(), 2)}
        assert sum(1 for a in r.assignments if a['date'] == TUE.isoformat()) == 2

    def test_legacy_mode_still_falls_back_to_template_headcount(self):
        plan = [{'date': MON.isoformat(), 'shift_id': 1, 'min_staff': 1, 'max_staff': None}]
        r = self._solve(plan, explicit=False)
        assert r.success
        assert len({a['date'] for a in r.assignments}) == 7


# ---------------------------------------------------------------------------
# generate / derive-legal / LLM / coverage 走計畫
# ---------------------------------------------------------------------------
class TestGenerateEndpoint:
    def _setup(self, organization, branch, admin_user, employee_role):
        for i in range(2):
            _employee(organization, branch, employee_role, f'G{i}')
        morning = _shift(organization)
        _shift(organization, '晚班', time(16, 0), time(23, 0))   # 沒進計畫
        v = _version(organization, branch, admin_user)
        for day in (MON, TUE):
            ScheduleVersionShiftPlan.objects.create(schedule_version=v, date=day,
                                                    shift_template=morning, min_staff=1)
        return v, morning

    def test_generate_uses_version_plan(self, admin_api_client, organization, branch, admin_user,
                                        employee_role):
        v, morning = self._setup(organization, branch, admin_user, employee_role)
        resp = admin_api_client.post('/api/ai/schedule/generate/', {
            'organization_id': organization.id, 'schedule_version_id': v.id,
            'period_start': MON.isoformat(), 'period_end': SUN.isoformat(),
            'consume_token': False}, format='json')
        assert resp.status_code == 200, resp.content
        cells = {(a['date'], a['shift_id']) for a in resp.json()['assignments']}
        assert cells == {(MON.isoformat(), morning.id), (TUE.isoformat(), morning.id)}

    def test_generate_without_plan_is_400(self, admin_api_client, organization, branch,
                                          admin_user, employee_role):
        _employee(organization, branch, employee_role, 'N1')
        _shift(organization)
        v = _version(organization, branch, admin_user)
        resp = admin_api_client.post('/api/ai/schedule/generate/', {
            'organization_id': organization.id, 'schedule_version_id': v.id,
            'period_start': MON.isoformat(), 'period_end': SUN.isoformat(),
            'consume_token': False}, format='json')
        assert resp.status_code == 400
        assert resp.json()['code'] == 'shift_plan_required'

    def test_derive_legal_drops_unplanned_shift(self, admin_api_client, organization, branch,
                                                admin_user, employee_role):
        e1 = _employee(organization, branch, employee_role, 'D1')
        _employee(organization, branch, employee_role, 'D2')
        morning = _shift(organization)
        evening = _shift(organization, '晚班', time(16, 0), time(23, 0))
        b = _version(organization, branch, admin_user)
        for i in range(7):
            ScheduleVersionShiftPlan.objects.create(
                schedule_version=b, date=MON + timedelta(days=i), shift_template=morning, min_staff=1)
            Schedule.objects.create(schedule_version=b, employee=e1, shift_template=morning,
                                    schedule_date=MON + timedelta(days=i), expected_hours=Decimal('7'))
        Schedule.objects.create(schedule_version=b, employee=e1, shift_template=evening,
                                schedule_date=MON, expected_hours=Decimal('7'))   # 計畫沒開
        resp = admin_api_client.post(f'/api/schedules/versions/{b.id}/derive-legal/',
                                     {'consume_token': False,
                                      'constraints': {'max_weekly_hours': 999, 'min_rest_hours': 0}},
                                     format='json')
        assert resp.status_code == 201, resp.content
        a_rows = Schedule.objects.filter(schedule_version_id=resp.json()['legal_version_id'])
        assert not a_rows.filter(shift_template=evening).exists()
        assert {s.schedule_date for s in a_rows} == {MON + timedelta(days=i) for i in range(7)}


class TestLLMPath:
    def test_rejects_unplanned_and_requires_plan(self, admin_api_client, organization, branch,
                                                 admin_user, employee_role, monkeypatch):
        from apps.ai_engine import llm_provider
        emp = _employee(organization, branch, employee_role, 'L1')
        morning = _shift(organization)
        evening = _shift(organization, '晚班', time(16, 0), time(23, 0))
        v = _version(organization, branch, admin_user, start=MON, end=TUE)
        url = '/api/ai/schedule/llm-generate/'
        resp = admin_api_client.post(url, {'schedule_version': v.id, 'consume_token': False},
                                     format='json')
        assert resp.status_code == 400 and resp.json()['code'] == 'shift_plan_required'

        ScheduleVersionShiftPlan.objects.create(schedule_version=v, date=MON,
                                                shift_template=morning, min_staff=2)
        captured = {}

        def fake(system, user, timeout=90):
            captured['user'] = user
            return {'assignments': [
                {'employee_id': emp.pk, 'date': MON.isoformat(), 'shift_id': morning.pk},
                {'employee_id': emp.pk, 'date': MON.isoformat(), 'shift_id': evening.pk},
                {'employee_id': emp.pk, 'date': TUE.isoformat(), 'shift_id': morning.pk},
            ]}, 'mock'
        monkeypatch.setattr(llm_provider, 'generate_json', fake)
        resp = admin_api_client.post(url, {'schedule_version': v.id, 'consume_token': False,
                                           'allow_multiple_shifts_per_day': True}, format='json')
        assert resp.status_code == 201, resp.content
        body = resp.json()
        assert body['created_count'] == 1
        reasons = [r['reason'] for r in body['rejected']]
        assert len(reasons) == 2 and all('沒有開' in r for r in reasons)
        assert any('只排到 1/2 人' in w for w in body['warnings'])
        assert not any(TUE.isoformat() in w for w in body['warnings'])   # 週二沒開班不算缺口
        assert '"min_staff"' in captured['user'] and 'staffing_requirements' in captured['user']
        # 期間完全沒計畫 → 400
        resp = admin_api_client.post(url, {'schedule_version': v.id, 'consume_token': False,
                                           'period_start': TUE.isoformat(),
                                           'period_end': TUE.isoformat()}, format='json')
        assert resp.status_code == 400 and resp.json()['code'] == 'shift_plan_required'


class TestCoverage:
    def test_coverage_only_for_planned_cells(self, admin_api_client, organization, branch,
                                             admin_user, employee_role):
        emp = _employee(organization, branch, employee_role, 'C1')
        morning = _shift(organization)
        evening = _shift(organization, '晚班', time(16, 0), time(23, 0))
        v = _version(organization, branch, admin_user, start=MON, end=TUE)
        ScheduleVersionShiftPlan.objects.create(schedule_version=v, date=MON,
                                                shift_template=morning, min_staff=2)
        Schedule.objects.create(schedule_version=v, employee=emp, shift_template=morning,
                                schedule_date=MON, expected_hours=Decimal('7'))
        Schedule.objects.create(schedule_version=v, employee=emp, shift_template=evening,
                                schedule_date=TUE, expected_hours=Decimal('7'))
        resp = admin_api_client.post(f'/api/schedules/versions/{v.id}/check-compliance/', {},
                                     format='json')
        cov = {(c['date'], c['shift_template_id']): c for c in resp.json()['coverage']}
        assert set(cov) == {(MON.isoformat(), morning.id), (TUE.isoformat(), evening.id)}
        assert cov[(MON.isoformat(), morning.id)]['status'] == 'under'
        assert cov[(MON.isoformat(), morning.id)]['source'] == 'plan'
        unplanned = cov[(TUE.isoformat(), evening.id)]
        assert unplanned['source'] == 'unplanned' and unplanned['status'] == 'over'
        assert unplanned['required_min'] == 0 and unplanned['required_max'] == 0
        assert resp.json()['coverage_summary'] == {'under': 1, 'over': 1, 'ok': 0}
