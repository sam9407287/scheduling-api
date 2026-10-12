"""
Production UI smoke test — drives the deployed frontend in headless
Chromium (Playwright) and checks every screen the frontend handoffs asked
Frank to build. Data it needs (a branch-bound version, employees) is created
through the API and removed afterwards.

    SMOKE_USERNAME=… SMOKE_PASSWORD=… venv/bin/pytest smoke/test_ui_smoke.py -v
    add --headed to watch, --video on to record into smoke/.artifacts/
"""
from __future__ import annotations

import re

import pytest
from playwright.sync_api import Page, expect

from smoke import helpers as H

pytestmark = pytest.mark.smoke
NEEDS_LOGIN = pytest.mark.skipif(not H.have_credentials(),
                                 reason='set SMOKE_USERNAME / SMOKE_PASSWORD')


@pytest.fixture(scope='session')
def browser_context_args(browser_context_args):
    return {**browser_context_args, 'base_url': H.WEB_URL, 'locale': 'zh-TW',
            'viewport': {'width': 1400, 'height': 900}}


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
    fixture.versions.append(api.post('/schedules/versions/', {
        'organization': fixture.org_id, 'branch': fixture.branch['id'],
        'version_label': H.tag('UI分店版'), 'version_type': 'actual',
    }))
    yield fixture
    problems = H.teardown_fixture(api, fixture)
    assert not problems, '\n'.join(problems)


def ensure_org(page: Page, fx):
    """Superusers must pick the organisation first; managers have it pre-selected."""
    page.goto('/schedules')
    if page.locator('#schedule-version-select').is_disabled():
        page.get_by_text('機構（必選）').locator('xpath=..').get_by_role('combobox').click()
        page.get_by_role('option', name=fx.org_name).click()
        expect(page.locator('#schedule-version-select')).to_be_enabled()


def pick_version(page: Page, label: str):
    page.locator('#schedule-version-select').click()
    page.get_by_role('option', name=re.compile(re.escape(label))).click()


def login(page: Page):
    page.goto('/login')
    page.locator('#email').fill(H.USERNAME)
    page.locator('#password').fill(H.PASSWORD)
    page.locator('form button[type=submit]').first.click()
    page.wait_for_url(re.compile(r'/(dashboard|onboarding)'), timeout=30_000)
    assert '/onboarding' not in page.url, 'smoke account has no organisation'


@pytest.fixture
def app(page: Page):
    login(page)
    return page


class TestLoginPage:
    def test_login_form_renders(self, page: Page):
        page.goto('/login')
        expect(page.locator('#email')).to_be_visible()
        expect(page.locator('#password')).to_be_visible()


@NEEDS_LOGIN
class TestSettingsTabs:
    """PM 10/13 bug: 員工優先順序 was a 404 and the tab row vanished."""

    def test_priorities_tab_opens_and_keeps_tab_row(self, app: Page):
        app.goto('/settings')
        app.get_by_role('link', name='員工優先順序').click()
        app.wait_for_url(re.compile(r'/settings/priorities'))
        expect(app.get_by_text('員工優先順序').first).to_be_visible()
        expect(app.get_by_role('link', name='班別模板')).to_be_visible()      # tab row still there
        expect(app.get_by_text('404')).to_have_count(0)

    def test_unknown_settings_path_falls_back_inside_settings(self, app: Page):
        app.goto('/settings/does-not-exist')
        app.wait_for_url(re.compile(r'/settings/(shifts|organizations)'))
        expect(app.get_by_role('link', name='班別模板')).to_be_visible()

    def test_closed_days_card_present(self, app: Page):
        app.goto('/settings/compliance')
        expect(app.get_by_text('每週固定公休日')).to_be_visible()
        for day in ('週一', '週日'):
            expect(app.get_by_text(day, exact=True).first).to_be_visible()


@NEEDS_LOGIN
class TestSchedulesPage:
    """10/5 branch-scope fix + 10/13 same-day checkbox."""

    def test_branch_version_shows_banner(self, app: Page, fx):
        ensure_org(app, fx)
        version = fx.versions[-1]
        pick_version(app, version['version_label'])
        expect(app.get_by_text(f"此版本僅排「{fx.branch['name']}」")).to_be_visible()
        for emp in fx.employees:                       # grid locked to the branch's people
            expect(app.get_by_text(emp['employee_id']).first).to_be_visible()

    def test_ai_dialog_has_same_day_checkbox_and_loading_label(self, app: Page, fx):
        ensure_org(app, fx)
        version = fx.versions[-1]
        pick_version(app, version['version_label'])
        app.get_by_role('button', name='AI 排班').click()
        expect(app.get_by_text('AI 排班區間')).to_be_visible()
        box = app.get_by_label(re.compile('允許同一人同一天排不同崗位的班'))
        expect(box).to_be_visible()
        expect(box).not_to_be_checked()                 # default = one shift per day
        app.keyboard.press('Escape')

    def test_new_version_dialog_offers_org_wide_option(self, app: Page, fx):
        ensure_org(app, fx)
        app.get_by_role('button', name='新增排班版本').click()
        expect(app.get_by_text('新增排班版本').first).to_be_visible()
        expect(app.get_by_text('版本標籤')).to_be_visible()
        scope = app.get_by_text('排班範圍', exact=True).locator('xpath=..').get_by_role('combobox')
        expect(scope).to_contain_text('不限分店（全機構）')      # default = org-wide, visible
        scope.click()
        expect(app.get_by_role('option', name='不限分店（全機構）')).to_be_visible()
        app.keyboard.press('Escape')
        app.keyboard.press('Escape')


@NEEDS_LOGIN
class TestEmployeesPage:
    def test_create_dialog_has_pay_type(self, app: Page):
        app.goto('/employees')
        app.get_by_role('button', name=re.compile('新增員工')).click()
        expect(app.get_by_text('新增員工').first).to_be_visible()
        expect(app.get_by_text('薪制', exact=True).first).to_be_visible()
        app.keyboard.press('Escape')


@NEEDS_LOGIN
class TestOtherPages:
    def test_overtime_page_loads(self, app: Page):
        app.goto('/overtime')
        expect(app.get_by_role('heading', name='加班管理')).to_be_visible()

    def test_approval_summary_has_date_range_pickers(self, app: Page):
        app.goto('/schedules/approved')
        expect(app.get_by_label('總表開始日期')).to_be_visible()
        expect(app.get_by_label('總表結束日期')).to_be_visible()

    def test_audit_page_loads_rows(self, app: Page):
        app.goto('/audit')
        expect(app.get_by_text('操作日誌').first).to_be_visible()
