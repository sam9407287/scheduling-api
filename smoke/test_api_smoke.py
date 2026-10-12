"""
Production API smoke test — one pass over every flow the frontend handoffs
promised (9/23, 10/5, 10/13), run against the deployed backend.

    SMOKE_USERNAME=… SMOKE_PASSWORD=… venv/bin/pytest smoke/ -v

Without credentials only the anonymous checks run. With SMOKE_RUN_LLM=1
the AI-schedule call is exercised too (Gemini, 30–150 s, not billed).
"""
from __future__ import annotations

import datetime as dt

import pytest
import requests

from smoke import helpers as H

pytestmark = pytest.mark.smoke

NEEDS_LOGIN = pytest.mark.skipif(not H.have_credentials(),
                                 reason='set SMOKE_USERNAME / SMOKE_PASSWORD')


# =====================================================================
# 0. Anonymous: is the deployment alive and is it the build we expect?
# =====================================================================
class TestAnonymous:
    def test_swagger_and_schema_served(self):
        assert requests.get(f'{H.API_URL}/docs/', timeout=30).status_code == 200
        assert requests.get(f'{H.API_URL}/schema/', timeout=30).status_code == 200

    @pytest.mark.parametrize('path', [
        '/employees/employees/', '/schedules/versions/', '/ai/schedule/llm-generate/',
        '/overtime/records/', '/compliance/settings/', '/audit/logs/', '/billing/usage/',
    ])
    def test_protected_endpoints_reject_anonymous(self, path):
        r = requests.get(f'{H.API_URL}{path}', timeout=30)
        assert r.status_code in (401, 403), f'{path} → {r.status_code}'

    def test_cors_allows_web_origin(self):
        r = requests.options(f'{H.API_URL}/auth/users/me/', headers={
            'Origin': H.WEB_URL, 'Access-Control-Request-Method': 'GET',
            'Access-Control-Request-Headers': 'authorization',
        }, timeout=30)
        assert r.headers.get('Access-Control-Allow-Origin') == H.WEB_URL, r.headers

    def test_frontend_bundle_is_current_build(self):
        """The live JS must contain the strings introduced by the 10/5 + 10/13 tasks."""
        html = requests.get(H.WEB_URL + '/', timeout=30).text
        assert '<div id="root">' in html
        import re
        m = re.search(r'/assets/index-[^"]+\.js', html)
        if not m and '/@vite/client' in html:
            pytest.skip('dev server (no hashed bundle) — only meaningful against a built deploy')
        assert m, 'no main bundle in index.html'
        js = requests.get(H.WEB_URL + m.group(0), timeout=60).text
        for marker in ('llm-generate', 'employees_out_of_scope', 'allow_multiple_shifts_per_day',
                       'pay_type', '僅排', '不限分店', 'settings/priorities', 'weekly_closed_days'):
            assert marker in js, f'frontend bundle missing {marker!r} — stale deploy?'


# =====================================================================
# 1. Authenticated end-to-end inside the login user's organisation
# =====================================================================
@pytest.fixture(scope='module')
def api():
    if not H.have_credentials():
        pytest.skip('set SMOKE_USERNAME / SMOKE_PASSWORD')
    a = H.Api()
    a.login(H.USERNAME, H.PASSWORD)
    return a


@pytest.fixture(scope='module')
def fx(api):
    fixture = H.build_fixture(api)
    yield fixture
    problems = H.teardown_fixture(api, fixture)
    assert not problems, 'cleanup left objects behind:\n' + '\n'.join(problems)


@NEEDS_LOGIN
class TestIdentity:
    def test_me_has_org_and_role(self, api):
        me = api.user
        assert me['organization'], 'smoke account has no organisation (onboarding state)'
        assert me['role_name'] in ('admin', 'manager', 'supervisor')

    def test_org_visible_and_duplicate_org_rejected_for_manager(self, api):
        orgs = api.get('/organizations/organizations/')
        ids = [o['id'] for o in orgs['results']]
        assert api.user['organization'] in ids
        if api.user.get('is_staff') or api.user.get('role_name') != 'manager':
            pytest.skip('one-org-per-account rule only applies to plain managers')
        r = api.raw('POST', '/organizations/organizations/', json={'name': H.tag('dup')})
        if r.status_code == 201:                      # never leave a stray org behind
            api.delete(f"/organizations/organizations/{r.json()['id']}/")
        assert r.status_code == 409 and r.json().get('code') == 'organization_already_exists'


@NEEDS_LOGIN
class TestEmployeesAndPayType:
    def test_employee_create_returns_pay_type(self, fx):
        monthly, hourly = fx.employees
        assert monthly['pay_type'] == 'monthly' and monthly['pay_type_display'] == '月薪制'
        assert hourly['pay_type'] == 'hourly'

    def test_pay_type_patch_roundtrip(self, api, fx):
        emp = fx.employees[0]
        api.patch(f"/employees/employees/{emp['id']}/", {'pay_type': 'hourly'})
        assert api.get(f"/employees/employees/{emp['id']}/")['pay_type'] == 'hourly'
        api.patch(f"/employees/employees/{emp['id']}/", {'pay_type': 'monthly'})

    def test_list_filters_by_branch(self, api, fx):
        rows = api.get('/employees/employees/', branch=fx.branch['id'], is_active='true')['results']
        got = {r['id'] for r in rows}
        assert {e['id'] for e in fx.employees} <= got


@NEEDS_LOGIN
class TestComplianceSettings:
    def test_weekly_closed_days_roundtrip(self, api):
        original = api.get('/compliance/settings/')
        assert 'weekly_closed_days' in original and 'soft_rule_types' in original
        try:
            after = api.patch('/compliance/settings/', {'weekly_closed_days': [6, 6, 0]})
            assert after['weekly_closed_days'] == [0, 6]          # dedup + sorted
            r = api.raw('PATCH', '/compliance/settings/', json={'weekly_closed_days': [0, 1, 2, 3, 4, 5, 6]})
            assert r.status_code == 400                           # whole week closed rejected
        finally:
            api.patch('/compliance/settings/', {'weekly_closed_days': original['weekly_closed_days']})


@NEEDS_LOGIN
class TestScheduleVersions:
    @pytest.fixture(scope='class')
    def versions(self, api, fx):
        scoped = api.post('/schedules/versions/', {
            'organization': fx.org_id, 'branch': fx.branch['id'],
            'version_label': H.tag('分店版'), 'version_type': 'actual',
        })
        org_wide = api.post('/schedules/versions/', {
            'organization': fx.org_id, 'branch': None,
            'version_label': H.tag('全機構版'), 'version_type': 'actual',
        })
        fx.versions += [scoped, org_wide]
        return scoped, org_wide

    def test_branch_scoped_version_exposes_branch_name(self, versions, fx):
        scoped, org_wide = versions
        assert scoped['branch'] == fx.branch['id'] and scoped['branch_name'] == fx.branch['name']
        assert org_wide['branch'] is None

    def test_rename_version(self, api, versions):
        scoped, _ = versions
        new_label = H.tag('改名')
        assert api.patch(f"/schedules/versions/{scoped['id']}/", {'version_label': new_label})['version_label'] == new_label

    def test_manual_cells_expand_period_and_compliance_check(self, api, versions, fx):
        scoped, _ = versions
        emp = fx.employees[0]
        monday = dt.date.today() + dt.timedelta(days=(7 - dt.date.today().weekday()) % 7 or 7)
        for i in range(7):                                      # 7 straight days → 七休一 violation
            api.post('/schedules/schedules/', {
                'schedule_version': scoped['id'], 'employee': emp['id'],
                'shift_template': fx.shift['id'],
                'schedule_date': (monday + dt.timedelta(days=i)).isoformat(),
                'expected_hours': '7.00', 'status': 'draft',
            })
        v = api.get(f"/schedules/versions/{scoped['id']}/")
        assert v['period_start'] <= monday.isoformat() <= v['period_end'] and v['schedule_count'] >= 7
        report = api.post(f"/schedules/versions/{scoped['id']}/check-compliance/")
        kinds = {x.get('rule') for x in report.get('violations', [])}
        assert 'max_consecutive_days' in kinds, kinds

    def test_approve_locks_then_unapprove_unlocks(self, api, versions, fx):
        scoped, _ = versions
        api.post(f"/schedules/versions/{scoped['id']}/approve/")
        r = api.raw('POST', '/schedules/schedules/', json={
            'schedule_version': scoped['id'], 'employee': fx.employees[1]['id'],
            'shift_template': fx.shift['id'], 'schedule_date': dt.date.today().isoformat(),
            'expected_hours': '7.00',
        })
        assert r.status_code == 409 and r.json()['code'] == 'schedule_version_locked'
        tl = api.get('/schedules/versions/approved-timeline/', organization=fx.org_id,
                     version_type='actual', date_from=dt.date.today().isoformat(),
                     date_to=(dt.date.today() + dt.timedelta(days=30)).isoformat())
        assert scoped['id'] in {v['id'] for v in tl['versions']}
        assert api.post(f"/schedules/versions/{scoped['id']}/unapprove/", {})['status'] == 'draft'


@NEEDS_LOGIN
class TestHolidayPayBySalaryType:
    def test_hourly_doubles_monthly_does_not_and_manager_can_settle(self, api, fx):
        day = dt.date.today() - dt.timedelta(days=1)
        holiday = api.post('/compliance/holidays/', {
            'organization': fx.org_id, 'date': day.isoformat(), 'name': H.tag('假日')})
        fx.holidays.append(holiday)
        records = {}
        for emp in fx.employees:
            att = api.post('/attendance/attendances/', {
                'employee_id': emp['id'], 'work_date': day.isoformat(), 'actual_hours': '8.00'})
            fx.attendances.append(att)
            out = api.post('/overtime/records/calculate/', {'attendance_id': att['id']})
            assert len(out) == 1 and out[0]['overtime_type'] == 'holiday'
            records[emp['pay_type']] = out[0]
        assert records['monthly']['multiplier'] == '1.00' and '補休' in records['monthly']['notes']
        assert float(records['hourly']['multiplier']) >= 2.0
        settled = api.patch(f"/overtime/records/{records['monthly']['id']}/",
                            {'multiplier': '2.00', 'notes': H.tag('折發')})
        assert settled['multiplier'] == '2.00'
        assert api.raw('PUT', f"/overtime/records/{records['monthly']['id']}/", json={}).status_code == 405


@NEEDS_LOGIN
class TestAuditAndBilling:
    def test_audit_logs_record_this_run(self, api):
        logs = api.get('/audit/logs/', page_size=50)
        assert logs['count'] > 0
        assert any(H.RUN_ID in str(row) for row in logs['results']), 'no audit row for this smoke run'

    def test_billing_usage_and_estimate(self, api):
        usage = api.get('/billing/usage/')
        assert 'total_tokens' in str(usage)
        est = api.post('/billing/estimate/', {'billing_mode': 'generate'})
        assert 10 in [v for v in est.values() if isinstance(v, int)], est


@NEEDS_LOGIN
@pytest.mark.skipif(not H.RUN_LLM, reason='set SMOKE_RUN_LLM=1 to call Gemini')
class TestLLMSchedule:
    def test_branch_scope_reported_and_one_shift_per_day_default(self, api, fx):
        scoped = next(v for v in fx.versions if v['branch'])
        start = dt.date.today() + dt.timedelta(days=14)
        res = api.post('/ai/schedule/llm-generate/', {
            'schedule_version': scoped['id'], 'consume_token': False,
            'period_start': start.isoformat(), 'period_end': (start + dt.timedelta(days=6)).isoformat(),
        }, timeout=200)
        assert res['engine'] == 'llm' and res['model']
        assert res['scope']['branch_id'] == fx.branch['id']
        assert set(res['scope']['employees_in_scope']) == {e['id'] for e in fx.employees}
        assert res['options'] == {'allow_multiple_shifts_per_day': False}
        assert res['billing'] is None or res['billing'].get('tokens_charged') in (None, 0)
        if res['scope']['employees_out_of_scope']:
            assert '只排分店' in res['warnings'][0]
        for row in res['rejected']:
            assert row['reason'], row
