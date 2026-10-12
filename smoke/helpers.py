"""
Shared helpers for the production smoke suite.

Everything here talks HTTP to a deployed backend; nothing touches the
Django ORM. Credentials and targets come from environment variables so the
same suite runs against production, a Railway preview, or localhost.

  SMOKE_API_URL   default https://web-production-8eb28.up.railway.app/api
  SMOKE_WEB_URL   default https://intelligent-scheduling-system.web.app
  SMOKE_USERNAME  token-login account (manager or superuser with an org)
  SMOKE_PASSWORD
  SMOKE_RUN_LLM   "1" to also call Gemini through llm-generate (30–150s,
                  consume_token=false so no billing)

Every object the suite creates is prefixed with SMOKE_PREFIX + run id and
deleted in teardown, so a run leaves the organisation as it found it.
"""
from __future__ import annotations

import os
import time
import uuid
from dataclasses import dataclass, field

import requests

API_URL = os.getenv('SMOKE_API_URL', 'https://web-production-8eb28.up.railway.app/api').rstrip('/')
WEB_URL = os.getenv('SMOKE_WEB_URL', 'https://intelligent-scheduling-system.web.app').rstrip('/')
USERNAME = os.getenv('SMOKE_USERNAME', '')
PASSWORD = os.getenv('SMOKE_PASSWORD', '')
RUN_LLM = os.getenv('SMOKE_RUN_LLM', '') == '1'

SMOKE_PREFIX = 'SMOKE'
RUN_ID = time.strftime('%m%d%H%M') + uuid.uuid4().hex[:4]
TIMEOUT = 30


def have_credentials() -> bool:
    return bool(USERNAME and PASSWORD)


def tag(label: str) -> str:
    """Name for a throwaway object: SMOKE-<run>-<label>."""
    return f'{SMOKE_PREFIX}-{RUN_ID}-{label}'


class Api:
    """Thin requests wrapper with token auth and uniform error reporting."""

    def __init__(self, base: str = API_URL):
        self.base = base
        self.s = requests.Session()
        self.s.headers['Accept'] = 'application/json'
        self.user: dict = {}

    # ---- auth -------------------------------------------------------
    def login(self, username: str, password: str) -> dict:
        r = self.s.post(f'{self.base}/auth/login/', json={'username': username, 'password': password},
                        timeout=TIMEOUT)
        assert r.status_code == 200, f'login failed {r.status_code}: {r.text[:300]}'
        token = r.json()['token']
        self.s.headers['Authorization'] = f'Token {token}'
        self.user = self.get('/auth/users/me/')
        return self.user

    # ---- verbs ------------------------------------------------------
    def _check(self, r: requests.Response, ok=(200, 201, 204)):
        assert r.status_code in ok, f'{r.request.method} {r.url} → {r.status_code}: {r.text[:400]}'
        return r.json() if r.content and r.status_code != 204 else None

    def get(self, path, ok=(200,), **params):
        return self._check(self.s.get(f'{self.base}{path}', params=params or None, timeout=TIMEOUT), ok)

    def post(self, path, body=None, ok=(200, 201), timeout=TIMEOUT):
        return self._check(self.s.post(f'{self.base}{path}', json=body or {}, timeout=timeout), ok)

    def patch(self, path, body, ok=(200,)):
        return self._check(self.s.patch(f'{self.base}{path}', json=body, timeout=TIMEOUT), ok)

    def put(self, path, body, ok=(200,)):
        return self._check(self.s.put(f'{self.base}{path}', json=body, timeout=TIMEOUT), ok)

    def delete(self, path, ok=(204, 200, 404)):
        r = self.s.delete(f'{self.base}{path}', timeout=TIMEOUT)
        assert r.status_code in ok, f'DELETE {path} → {r.status_code}: {r.text[:300]}'
        return r.status_code

    def raw(self, method, path, **kw) -> requests.Response:
        return self.s.request(method, f'{self.base}{path}', timeout=TIMEOUT, **kw)


@dataclass
class Fixture:
    """Throwaway data created inside the login user's organisation."""
    org_id: int
    branch: dict
    shift: dict
    org_name: str = ''
    employees: list = field(default_factory=list)
    versions: list = field(default_factory=list)
    holidays: list = field(default_factory=list)
    attendances: list = field(default_factory=list)


def build_fixture(api: Api) -> Fixture:
    org_id = api.user['organization']
    assert org_id, 'smoke user must belong to an organisation'

    branch = api.post('/organizations/branches/', {
        'organization': org_id, 'name': tag('分店'), 'code': f'S{RUN_ID[-6:]}',
    })
    shift = api.post('/shifts/templates/', {
        'organization': org_id, 'name': tag('早班'),
        'start_time': '08:00', 'end_time': '16:00',
        'break_minutes': 60, 'min_staff_count': 1,
    })
    fx = Fixture(org_id=org_id, branch=branch, shift=shift,
                 org_name=api.get(f'/organizations/organizations/{org_id}/')['name'])
    for i, pay in enumerate(('monthly', 'hourly'), start=1):
        uname = f'smoke_{RUN_ID}_{i}'
        emp = api.post('/employees/employees/', {
            'user': {'username': uname, 'password': 'Smoke-' + uuid.uuid4().hex[:10],
                     'email': f'{uname}@smoke.invalid',
                     'first_name': f'煙霧{i}', 'last_name': '測'},
            'employee_id': tag(f'E{i}'), 'organization': org_id,
            'branch': branch['id'], 'position': 'smoke', 'hire_date': '2024-01-01',
            'pay_type': pay,
        })
        fx.employees.append(emp)
    return fx


def teardown_fixture(api: Api, fx: Fixture) -> list[str]:
    """Delete everything in reverse dependency order; return what failed."""
    problems = []

    def _try(path):
        try:
            api.delete(path)
        except AssertionError as exc:          # keep going, report at end
            problems.append(str(exc)[:160])

    for att in fx.attendances:
        _try(f"/attendance/attendances/{att['id']}/")
    for h in fx.holidays:
        _try(f"/compliance/holidays/{h['id']}/")
    for v in fx.versions:
        _try(f"/schedules/versions/{v['id']}/")
    for e in fx.employees:                     # hard delete removes the login user too
        _try(f"/employees/employees/{e['id']}/")
    _try(f"/shifts/templates/{fx.shift['id']}/")
    _try(f"/organizations/branches/{fx.branch['id']}/")
    return problems
