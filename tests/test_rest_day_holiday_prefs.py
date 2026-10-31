"""
員工層級「休息日同意」與「國定假日意願」＋ 期間排休配額（2026-10-31）。

法律事實（勞基法 §36 一例一休）：每 7 日 2 天休息 = 例假 1 天（不可出勤）
＋ 休息日 1 天（可出勤，需勞工同意＋加班費）。所以：
- 例假：每 ISO 週最多上 6 天，hard，誰都一樣
- 休息日（第 6 天）：rest_day_work_ok=True 的員工才可排（soft 罰 2 當成本）；
  沒同意的員工仍硬守 5 天（與 10/22 行為相同）
- 國定假日：holiday_work forbid（硬擋）/ allow / prefer / avoid（分級懲罰）
- 期間排休配額：月薪制上班天數 ≤ 期間天數 − 週休 − 國定假日（soft 罰 8/天）
"""
from datetime import date, time, timedelta
from decimal import Decimal

import pytest

from apps.accounts.models import User
from apps.ai_engine.providers.base import ScheduleRequest
from apps.ai_engine.providers.ortools_provider import ORToolsProvider
from apps.ai_engine.rest_quota import compute_rest_quota, quota_applies
from apps.compliance.engine import check_schedule_violations
from apps.employees.models import Employee, EmployeeAvailability
from apps.schedules.models import Schedule, ScheduleVersion
from apps.shifts.models import ShiftTemplate

pytestmark = pytest.mark.django_db

MON = date(2026, 11, 2)
SAT = date(2026, 11, 7)
SUN = date(2026, 11, 8)
WED = date(2026, 11, 4)
# 2026-12-25 行憲紀念日（週五）—— 內建假日表，API / 合規測試用
XMAS_MON, XMAS_FRI, XMAS_SUN = date(2026, 12, 21), date(2026, 12, 25), date(2026, 12, 27)

SHIFT = {'id': 1, 'name': '早', 'start_time': '08:00', 'end_time': '16:00',
         'break_minutes': 60, 'min_staff_count': 1, 'required_certifications': [],
         'employee_priorities': []}
LAX = {'max_weekly_hours': 999, 'min_rest_hours': 0, 'max_consecutive_days': 7}


def _emp(eid, **extra):
    base = {'id': eid, 'employee_id': f'E{eid}', 'agreed_hours_per_week': 40.0,
            'certifications': []}
    base.update(extra)
    return base


def _request(employees, constraints, start=MON, end=SUN, **kw):
    return ScheduleRequest(
        organization_id=1, branch_id=None, period_start=start, period_end=end,
        employees=employees, shift_templates=[SHIFT], constraints=constraints,
        preferences={}, **kw,
    )


def _days(result, emp_id):
    return {a['date'] for a in result.assignments if a['employee_id'] == emp_id}


def _employee(org, branch, role, code, **fields):
    user = User.objects.create_user(username=f'hp_{code}', password='pw', role=role,
                                    organization=org, branch=branch)
    return Employee.objects.create(user=user, employee_id=code, organization=org,
                                   branch=branch, position='nurse',
                                   hire_date=date(2024, 1, 1), **fields)


def _shift(org):
    return ShiftTemplate.objects.create(organization=org, name='早班', start_time=time(8, 0),
                                        end_time=time(16, 0), min_staff_count=1, break_minutes=60)


def _version(org, branch, user, start, end):
    return ScheduleVersion.objects.create(organization=org, branch=branch, version_label='HP',
                                          version_type='actual', period_start=start,
                                          period_end=end, created_by=user)


# ---------------------------------------------------------------------------
# 休息日同意（第 6 天）
# ---------------------------------------------------------------------------
class TestSixthDayConsent:
    def test_without_consent_six_days_is_infeasible(self):
        # 單一員工、週一～週六每天要 1 人：沒同意休息日出勤 ⇒ 硬守 5 天 ⇒ 排不出
        r = ORToolsProvider().generate_schedule(
            _request([_emp(1)], LAX, end=SAT, enforce_labor_law=True))
        assert not r.success

    def test_with_consent_six_days_is_feasible(self):
        r = ORToolsProvider().generate_schedule(
            _request([_emp(1, rest_day_work_ok=True)], LAX, end=SAT, enforce_labor_law=True))
        assert r.success, r.message
        assert len(_days(r, 1)) == 6

    def test_seventh_day_is_regular_day_off_even_with_consent(self):
        # 例假誰都不能上：同意休息日也排不出 7 天
        r = ORToolsProvider().generate_schedule(
            _request([_emp(1, rest_day_work_ok=True)], LAX, enforce_labor_law=True))
        assert not r.success

    def test_sixth_day_goes_to_the_consenting_employee(self):
        # 兩人 7 天、其中 4 天要 2 人 ⇒ 11 人日 ⇒ 必有一人上 6 天：要是同意的那位
        heavy = [(MON + timedelta(days=i)).isoformat() for i in (0, 1, 2, 3)]
        constraints = {**LAX, 'staffing_requirements': [
            {'date': d, 'shift_id': 1, 'min_staff': 2, 'max_staff': None} for d in heavy]}
        r = ORToolsProvider().generate_schedule(_request(
            [_emp(1, rest_day_work_ok=True), _emp(2, rest_day_work_ok=False)],
            constraints, enforce_labor_law=True,
        ))
        assert r.success, r.message
        assert len(_days(r, 1)) == 6
        assert len(_days(r, 2)) == 5

    def test_consent_does_not_invite_extra_days_when_not_needed(self):
        # 人力充足時，同意的人也不該被多排（公平性 10 > 休息日成本 2）
        r = ORToolsProvider().generate_schedule(_request(
            [_emp(1, rest_day_work_ok=True), _emp(2)], LAX, enforce_labor_law=True))
        assert r.success
        assert len(_days(r, 1)) <= 4 and len(_days(r, 2)) <= 4


# ---------------------------------------------------------------------------
# 國定假日意願
# ---------------------------------------------------------------------------
class TestHolidayWork:
    HOL = {'holidays': [WED.isoformat()]}

    def test_forbid_is_hard(self):
        r = ORToolsProvider().generate_schedule(_request(
            [_emp(1, holiday_work='forbid'), _emp(2)], {**LAX, **self.HOL}))
        assert r.success, r.message
        assert WED.isoformat() not in _days(r, 1)
        assert WED.isoformat() in _days(r, 2)

    def test_forbid_everyone_is_infeasible(self):
        r = ORToolsProvider().generate_schedule(_request(
            [_emp(1, holiday_work='forbid')], {**LAX, **self.HOL}))
        assert not r.success

    def test_prefer_beats_avoid_on_the_holiday(self):
        r = ORToolsProvider().generate_schedule(_request(
            [_emp(1, holiday_work='avoid'), _emp(2, holiday_work='prefer')],
            {**LAX, **self.HOL}))
        assert r.success
        assert WED.isoformat() in _days(r, 2)
        assert WED.isoformat() not in _days(r, 1)

    def test_avoid_still_gets_scheduled_when_alone(self):
        # 「不優先」不是「不可以」：只有他一個人時假日還是排他
        r = ORToolsProvider().generate_schedule(_request(
            [_emp(1, holiday_work='avoid')], {**LAX, **self.HOL}))
        assert r.success
        assert WED.isoformat() in _days(r, 1)

    def test_holidays_accept_date_objects(self):
        r = ORToolsProvider().generate_schedule(_request(
            [_emp(1, holiday_work='forbid'), _emp(2)], {**LAX, 'holidays': [WED]}))
        assert r.success and WED.isoformat() not in _days(r, 1)


# ---------------------------------------------------------------------------
# 期間排休配額
# ---------------------------------------------------------------------------
class TestRestQuota:
    def test_compute_rounds_weekly_rest_and_counts_in_period_holidays(self):
        q = compute_rest_quota(date(2026, 10, 1), date(2026, 10, 30),
                               ['2026-10-09', '2026-10-10', '2026-10-25', '2026-10-26',
                                '2026-12-25'])
        assert q['period_days'] == 30
        assert q['weekly_rest_days'] == 9          # 30 × 2 / 7 = 8.57 → 9
        assert q['holiday_days'] == 4              # 12/25 不在期間
        assert q['rest_days_total'] == 13
        assert q['work_days_max'] == 17

    def test_compute_other_lengths(self):
        assert compute_rest_quota(date(2026, 2, 1), date(2026, 2, 28))['weekly_rest_days'] == 8
        assert compute_rest_quota(date(2026, 3, 1), date(2026, 3, 31))['weekly_rest_days'] == 9
        assert compute_rest_quota(MON, SUN, [WED])['work_days_max'] == 4
        assert compute_rest_quota(SUN, MON)['work_days_max'] == 0

    def test_applies_to_monthly_only(self):
        assert quota_applies({'pay_type': 'monthly'})
        assert quota_applies({})                   # 沒給視為月薪
        assert not quota_applies({'pay_type': 'hourly'})

    def test_metadata_reports_quota_when_holidays_given(self):
        r = ORToolsProvider().generate_schedule(_request(
            [_emp(1, pay_type='monthly'), _emp(2, pay_type='hourly')],
            {**LAX, 'holidays': [WED.isoformat()]}))
        assert r.success
        rq = r.metadata['rest_quota']
        assert rq['work_days_max'] == 4 and rq['holiday_days'] == 1
        rows = {row['employee_id']: row for row in rq['employees']}
        assert rows[1]['applies'] and rows[1]['work_days_max'] == 4
        assert not rows[2]['applies'] and rows[2]['work_days_max'] is None
        assert rows[1]['over_quota'] == 0
        assert rows[1]['work_days'] + rows[1]['rest_days'] == 7

    def test_no_quota_without_holidays_key(self):
        r = ORToolsProvider().generate_schedule(_request([_emp(1)], LAX))
        assert r.success and r.metadata['rest_quota'] is None

    def test_quota_steers_work_to_hourly_employee(self):
        # 月薪 E1 配額 4 天（7 − 2 − 1 假日）；時薪 E2 無配額。7 天各 1 人：
        # 公平性只差 1 天，E1 多上第 4 天後再多就罰 8 ⇒ E1 4 天、E2 3 天
        r = ORToolsProvider().generate_schedule(_request(
            [_emp(1, pay_type='monthly'), _emp(2, pay_type='hourly')],
            {**LAX, 'holidays': [WED.isoformat()]}))
        assert r.success
        assert len(_days(r, 1)) <= 4

    def test_apply_rest_quota_false_disables_it(self):
        r = ORToolsProvider().generate_schedule(_request(
            [_emp(1)], {**LAX, 'holidays': [WED.isoformat()], 'apply_rest_quota': False}))
        assert r.success and r.metadata['rest_quota'] is None


# ---------------------------------------------------------------------------
# API：availability 欄位 / 員工列表摘要 / rest-quota 試算 / generate metadata
# ---------------------------------------------------------------------------
class TestAvailabilityAPI:
    def test_put_and_get_new_fields(self, admin_api_client, organization, branch, employee_role):
        emp = _employee(organization, branch, employee_role, 'A1')
        url = f'/api/employees/employees/{emp.id}/availability/'
        resp = admin_api_client.put(url, {'rest_day_work_ok': True, 'holiday_work': 'prefer'},
                                    format='json')
        assert resp.status_code in (200, 201), resp.content
        body = admin_api_client.get(url).json()
        assert body['rest_day_work_ok'] is True
        assert body['holiday_work'] == 'prefer'
        assert body['holiday_work_display'] == '偏好排假日班'

    def test_invalid_choice_rejected(self, admin_api_client, organization, branch, employee_role):
        emp = _employee(organization, branch, employee_role, 'A2')
        resp = admin_api_client.put(f'/api/employees/employees/{emp.id}/availability/',
                                    {'holiday_work': 'sometimes'}, format='json')
        assert resp.status_code == 400

    def test_defaults_when_no_availability(self, admin_api_client, organization, branch,
                                           employee_role):
        emp = _employee(organization, branch, employee_role, 'A3')
        detail = admin_api_client.get(f'/api/employees/employees/{emp.id}/').json()
        assert detail['scheduling_prefs'] == {'rest_day_work_ok': False, 'holiday_work': 'allow'}
        listing = admin_api_client.get('/api/employees/employees/').json()
        rows = listing['results'] if isinstance(listing, dict) else listing
        mine = next(r for r in rows if r['id'] == emp.id)
        assert mine['scheduling_prefs']['holiday_work'] == 'allow'


class TestRestQuotaEndpoint:
    URL = '/api/ai/schedule/rest-quota/'

    def test_quota_with_builtin_holiday(self, manager_api_client, organization, branch,
                                        employee_role):
        monthly = _employee(organization, branch, employee_role, 'Q1', pay_type='monthly')
        hourly = _employee(organization, branch, employee_role, 'Q2', pay_type='hourly')
        EmployeeAvailability.objects.create(employee=monthly, holiday_work='forbid',
                                            rest_day_work_ok=True)
        resp = manager_api_client.get(self.URL, {'period_start': XMAS_MON.isoformat(),
                                                 'period_end': XMAS_SUN.isoformat()})
        assert resp.status_code == 200, resp.content
        body = resp.json()
        assert body['period_days'] == 7
        assert body['weekly_rest_days'] == 2
        assert body['holiday_days'] == 1
        assert body['holidays'][0] == {'date': '2026-12-25', 'name': '行憲紀念日', 'kind': 'holiday'}
        assert body['work_days_max'] == 4
        rows = {r['employee_id']: r for r in body['employees']}
        assert rows[monthly.id]['applies'] and rows[monthly.id]['work_days_max'] == 4
        assert rows[monthly.id]['holiday_work'] == 'forbid'
        assert rows[monthly.id]['rest_day_work_ok'] is True
        assert not rows[hourly.id]['applies'] and rows[hourly.id]['work_days_max'] is None

    def test_missing_params_is_400(self, manager_api_client):
        assert manager_api_client.get(self.URL).status_code == 400
        assert manager_api_client.get(self.URL, {'period_start': '2026-12-27',
                                                 'period_end': '2026-12-21'}).status_code == 400

    def test_employee_role_forbidden(self, employee_api_client):
        resp = employee_api_client.get(self.URL, {'period_start': '2026-12-21',
                                                  'period_end': '2026-12-27'})
        assert resp.status_code == 403


class TestGenerateAPI:
    def test_generate_carries_holidays_and_prefs(self, admin_api_client, organization, branch,
                                                 employee_role):
        e1 = _employee(organization, branch, employee_role, 'G1')
        e2 = _employee(organization, branch, employee_role, 'G2')
        EmployeeAvailability.objects.create(employee=e1, holiday_work='forbid')
        _shift(organization)
        resp = admin_api_client.post('/api/ai/schedule/generate/', {
            'organization_id': organization.id,
            'period_start': XMAS_MON.isoformat(), 'period_end': XMAS_SUN.isoformat(),
            'consume_token': False,
            'constraints': {'max_weekly_hours': 999, 'min_rest_hours': 0,
                            'max_consecutive_days': 7},
        }, format='json')
        assert resp.status_code == 200, resp.content
        body = resp.json()
        assert body['success'], body
        rq = body['metadata']['rest_quota']
        assert rq['holidays'] == ['2026-12-25'] and rq['work_days_max'] == 4
        on_xmas = {a['employee_id'] for a in body['assignments'] if a['date'] == '2026-12-25'}
        assert e1.id not in on_xmas and e2.id in on_xmas


# ---------------------------------------------------------------------------
# 合規檢查：forbid 卻被手動排在假日 → hard
# ---------------------------------------------------------------------------
class TestComplianceHolidayRefused:
    def _cell(self, version, emp, shift, day):
        return Schedule.objects.create(schedule_version=version, employee=emp,
                                       shift_template=shift, schedule_date=day,
                                       expected_hours=Decimal('7'))

    def test_forbid_employee_on_holiday_is_hard(self, organization, branch, admin_user,
                                                employee_role):
        emp = _employee(organization, branch, employee_role, 'C1')
        EmployeeAvailability.objects.create(employee=emp, holiday_work='forbid')
        version = _version(organization, branch, admin_user, XMAS_MON, XMAS_SUN)
        self._cell(version, emp, _shift(organization), XMAS_FRI)
        hits = [v for v in check_schedule_violations(version)
                if v.rule in ('holiday_work_refused', 'holiday_scheduling')]
        assert len(hits) == 1
        assert hits[0].rule == 'holiday_work_refused'
        assert hits[0].severity == 'hard'
        assert hits[0].detail == {'holiday_name': '行憲紀念日', 'holiday_work': 'forbid'}

    def test_other_prefs_keep_soft_reminder(self, organization, branch, admin_user,
                                            employee_role):
        emp = _employee(organization, branch, employee_role, 'C2')
        EmployeeAvailability.objects.create(employee=emp, holiday_work='avoid')
        version = _version(organization, branch, admin_user, XMAS_MON, XMAS_SUN)
        self._cell(version, emp, _shift(organization), XMAS_FRI)
        hits = [v for v in check_schedule_violations(version) if v.rule == 'holiday_scheduling']
        assert len(hits) == 1 and hits[0].severity == 'soft'
        assert hits[0].detail['holiday_work'] == 'avoid'

    def test_check_compliance_endpoint_labels_rule(self, admin_api_client, organization, branch,
                                                   admin_user, employee_role):
        emp = _employee(organization, branch, employee_role, 'C3')
        EmployeeAvailability.objects.create(employee=emp, holiday_work='forbid')
        version = _version(organization, branch, admin_user, XMAS_MON, XMAS_SUN)
        self._cell(version, emp, _shift(organization), XMAS_FRI)
        resp = admin_api_client.post(f'/api/schedules/versions/{version.id}/check-compliance/',
                                     {}, format='json')
        assert resp.status_code == 200
        rows = [v for v in resp.json()['violations'] if v['rule'] == 'holiday_work_refused']
        assert rows and rows[0]['rule_label'] == '員工設定不可排國定假日卻被排班'


# ---------------------------------------------------------------------------
# LLM 路徑：context / prompt / 驗證層
# ---------------------------------------------------------------------------
class TestLLMPath:
    def test_context_and_prompt(self, organization, branch, admin_user, employee_role):
        from apps.ai_engine import llm_scheduler
        emp = _employee(organization, branch, employee_role, 'L1', pay_type='hourly')
        EmployeeAvailability.objects.create(employee=emp, holiday_work='prefer',
                                            rest_day_work_ok=True)
        _shift(organization)
        version = _version(organization, branch, admin_user, XMAS_MON, XMAS_SUN)
        payload, lookups = llm_scheduler.build_context(version, XMAS_MON, XMAS_SUN)
        assert payload['org']['holidays'] == [{'date': '2026-12-25', 'name': '行憲紀念日'}]
        assert payload['rest_quota']['work_days_max'] == 4
        row = payload['employees'][0]
        assert row['holiday_work'] == 'prefer' and row['rest_day_work_ok'] is True
        assert row['work_days_max'] is None          # 時薪制不套配額
        assert lookups['holidays'] == {'2026-12-25'}
        prompt = llm_scheduler.build_system_prompt()
        assert 'holiday_work' in prompt and 'rest_day_work_ok' in prompt
        assert '最多只能上 5 天' in prompt and '一例一休' in prompt

    def test_forbid_holiday_row_rejected(self, organization, branch, admin_user, employee_role):
        from apps.ai_engine import llm_scheduler
        emp = _employee(organization, branch, employee_role, 'L2')
        EmployeeAvailability.objects.create(employee=emp, holiday_work='forbid')
        shift = _shift(organization)
        version = _version(organization, branch, admin_user, XMAS_MON, XMAS_SUN)
        _, lookups = llm_scheduler.build_context(version, XMAS_MON, XMAS_SUN)
        raw = [{'employee_id': emp.pk, 'date': '2026-12-25', 'shift_id': shift.pk},
               {'employee_id': emp.pk, 'date': '2026-12-24', 'shift_id': shift.pk}]
        valid, rejected = llm_scheduler.validate_assignments(raw, lookups, version)
        assert [r['date'] for r in valid] == ['2026-12-24']
        assert rejected[0]['reason'] == '該員工設定不可排國定假日'

    def test_sixth_day_needs_consent(self, organization, branch, admin_user, employee_role):
        from apps.ai_engine import llm_scheduler
        ok = _employee(organization, branch, employee_role, 'L3')
        no = _employee(organization, branch, employee_role, 'L4')
        EmployeeAvailability.objects.create(employee=ok, rest_day_work_ok=True)
        shift = _shift(organization)
        version = _version(organization, branch, admin_user, MON, SUN)
        _, lookups = llm_scheduler.build_context(version, MON, SUN)
        raw = [{'employee_id': e.pk, 'date': (MON + timedelta(days=i)).isoformat(),
                'shift_id': shift.pk} for e in (ok, no) for i in range(6)]
        valid, rejected = llm_scheduler.validate_assignments(raw, lookups, version)
        per = {}
        for r in valid:
            per.setdefault(r['employee'].pk, 0)
            per[r['employee'].pk] += 1
        assert per[ok.pk] == 6 and per[no.pk] == 5
        assert len(rejected) == 1 and '未同意休息日出勤' in rejected[0]['reason']
        # 第 7 天連同意的人也不行（例假）
        raw7 = [{'employee_id': ok.pk, 'date': SUN.isoformat(), 'shift_id': shift.pk}]
        for r in valid:
            if r['employee'].pk == ok.pk:
                Schedule.objects.create(schedule_version=version, employee=ok,
                                        shift_template=shift, schedule_date=r['date'],
                                        expected_hours=Decimal('7'))
        valid7, rejected7 = llm_scheduler.validate_assignments(raw7, lookups, version)
        assert not valid7 and rejected7
