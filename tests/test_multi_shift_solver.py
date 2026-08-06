"""
Multi-shift solver tests (multi_shift_combine backend).

Product rules (2026-08-06):
- One employee may take several shifts on the same day; only time-OVERLAPPING
  shift pairs are mutually exclusive in the solver.
- Daily total hours are capped by max_daily_hours (default 8, org ShiftRule
  override, request-constraints override) — hard by default, soft via
  soft_rule_types.
- A same-day split shift is NOT a rest-interval violation; the min-rest rule
  only guards gaps between different working days.
"""
import pytest  # noqa: F401  (fixtures via conftest)
from datetime import date, time, timedelta

from apps.ai_engine.providers.base import ScheduleRequest
from apps.ai_engine.providers.ortools_provider import ORToolsProvider
from apps.employees.models import Employee
from apps.shifts.models import ShiftRule, ShiftTemplate

from rest_framework import status


def _shift(org, name, start, end, **kw):
    return ShiftTemplate.objects.create(
        organization=org, name=name,
        start_time=start, end_time=end,
        min_staff_count=kw.pop('min_staff_count', 1),
        **kw,
    )


def _make_employee(org, branch, role, code, idx):
    from apps.accounts.models import User
    u = User.objects.create_user(
        username=f'e_multi_{code}_{idx}', password='pw',
        first_name=f'F{idx}', last_name=f'L{idx}',
        role=role, organization=org, branch=branch,
    )
    return Employee.objects.create(
        user=u, employee_id=f'{code}{idx}',
        organization=org, branch=branch,
        position='nurse', hire_date=date(2024, 1, 1),
    )


def _provider_inputs(employees, shifts):
    return (
        [
            {
                'id': e.id,
                'employee_id': e.employee_id,
                'agreed_hours_per_week': float(e.agreed_hours_per_week),
                'certifications': [], 'unavailable_dates': [],
                'availability': {},
            }
            for e in employees
        ],
        [
            {
                'id': st.id, 'name': st.name,
                'start_time': st.start_time.isoformat(),
                'end_time': st.end_time.isoformat(),
                'break_minutes': st.break_minutes,
                'min_staff_count': st.min_staff_count,
                'required_certifications': [],
                'employee_priorities': [],
            }
            for st in shifts
        ],
    )


def _request(org, branch, employees, shifts, days=1, constraints=None, **kw):
    emps, sfts = _provider_inputs(employees, shifts)
    start = date(2026, 8, 3)
    return ScheduleRequest(
        organization_id=org.id, branch_id=branch.id,
        period_start=start, period_end=start + timedelta(days=days - 1),
        employees=emps, shift_templates=sfts,
        constraints=constraints if constraints is not None else {},
        preferences={},
        **kw,
    )


class TestMultiShiftPerDay:
    def test_one_employee_covers_two_adjacent_shifts_same_day(
        self, db, organization, branch, employee_role
    ):
        """1 員工 + 早段 08-12 與午段 13-17 都要 1 人 → 同日雙班可行。"""
        e1 = _make_employee(organization, branch, employee_role, 'M', 1)
        am = _shift(organization, 'AM', time(8, 0), time(12, 0))
        pm = _shift(organization, 'PM', time(13, 0), time(17, 0))

        req = _request(organization, branch, [e1], [am, pm])
        result = ORToolsProvider().generate_schedule(req)
        assert result.success, result.message
        assert len(result.assignments) == 2
        assert {a['shift_id'] for a in result.assignments} == {am.id, pm.id}
        assert all(a['employee_id'] == e1.id for a in result.assignments)

    def test_overlapping_shifts_never_same_employee_same_day(
        self, db, organization, branch, employee_role
    ):
        """兩個時間重疊的班別（08-16 vs 10-14）不會排給同一人同一天。"""
        e1 = _make_employee(organization, branch, employee_role, 'O', 1)
        e2 = _make_employee(organization, branch, employee_role, 'O', 2)
        full = _shift(organization, 'FULL', time(8, 0), time(16, 0))
        mid = _shift(organization, 'MID', time(10, 0), time(14, 0))

        req = _request(organization, branch, [e1, e2], [full, mid])
        result = ORToolsProvider().generate_schedule(req)
        assert result.success, result.message
        by_emp_day = {}
        for a in result.assignments:
            by_emp_day.setdefault((a['employee_id'], a['date']), set()).add(a['shift_id'])
        for shift_ids in by_emp_day.values():
            assert not ({full.id, mid.id} <= shift_ids)

    def test_split_shift_day_does_not_trigger_min_rest(
        self, db, organization, branch, employee_role
    ):
        """同日拆班（間隔 1 小時 < 11h）＋勞基法硬約束仍可行。"""
        e1 = _make_employee(organization, branch, employee_role, 'R', 1)
        am = _shift(organization, 'AM', time(8, 0), time(12, 0))
        pm = _shift(organization, 'PM', time(13, 0), time(17, 0))

        req = _request(
            organization, branch, [e1], [am, pm],
            constraints={'min_rest_hours': 11},
            enforce_labor_law=True,
        )
        result = ORToolsProvider().generate_schedule(req)
        assert result.success, result.message
        assert len(result.assignments) == 2

    def test_cross_day_rest_still_enforced(
        self, db, organization, branch, employee_role
    ):
        """晚班 15-23 → 隔日早班 08-16 只有 9h 休息：兩人下不同班。"""
        e1 = _make_employee(organization, branch, employee_role, 'C', 1)
        e2 = _make_employee(organization, branch, employee_role, 'C', 2)
        late = _shift(organization, 'LATE', time(15, 0), time(23, 0))
        early = _shift(organization, 'EARLY', time(8, 0), time(16, 0))

        req = _request(
            organization, branch, [e1, e2], [late, early], days=2,
            constraints={'min_rest_hours': 11, 'max_daily_hours': 8},
            enforce_labor_law=True,
        )
        result = ORToolsProvider().generate_schedule(req)
        assert result.success, result.message
        day1, day2 = date(2026, 8, 3).isoformat(), date(2026, 8, 4).isoformat()
        for emp in (e1, e2):
            took_late_d1 = any(
                a['employee_id'] == emp.id and a['date'] == day1 and a['shift_id'] == late.id
                for a in result.assignments
            )
            took_early_d2 = any(
                a['employee_id'] == emp.id and a['date'] == day2 and a['shift_id'] == early.id
                for a in result.assignments
            )
            assert not (took_late_d1 and took_early_d2)


class TestMaxDailyHours:
    def test_daily_cap_blocks_second_shift(
        self, db, organization, branch, employee_role
    ):
        """兩個 6h 班（共 12h）超過 8h 上限，min_staff=0 下不會硬塞給同一人。"""
        e1 = _make_employee(organization, branch, employee_role, 'D', 1)
        am = _shift(organization, 'AM6', time(6, 0), time(12, 0), min_staff_count=0)
        pm = _shift(organization, 'PM6', time(13, 0), time(19, 0), min_staff_count=0)

        req = _request(
            organization, branch, [e1], [am, pm],
            constraints={'max_daily_hours': 8},
            enforce_labor_law=True,
        )
        result = ORToolsProvider().generate_schedule(req)
        assert result.success, result.message
        assert len(result.assignments) <= 1

    def test_daily_cap_infeasible_when_min_staff_forces_overrun(
        self, db, organization, branch, employee_role
    ):
        """min_staff=1 強迫兩班都排給唯一員工 → 硬約束衝突 INFEASIBLE。"""
        e1 = _make_employee(organization, branch, employee_role, 'E', 1)
        am = _shift(organization, 'AM6', time(6, 0), time(12, 0))
        pm = _shift(organization, 'PM6', time(13, 0), time(19, 0))

        req = _request(
            organization, branch, [e1], [am, pm],
            constraints={'max_daily_hours': 8},
            enforce_labor_law=True,
        )
        result = ORToolsProvider().generate_schedule(req)
        assert not result.success

    def test_daily_cap_soft_allows_overrun_with_penalty(
        self, db, organization, branch, employee_role
    ):
        """max_daily_hours 列入 soft_rule_types → 超時仍可行（重罰不擋）。"""
        e1 = _make_employee(organization, branch, employee_role, 'S', 1)
        am = _shift(organization, 'AM6', time(6, 0), time(12, 0))
        pm = _shift(organization, 'PM6', time(13, 0), time(19, 0))

        req = _request(
            organization, branch, [e1], [am, pm],
            constraints={'max_daily_hours': 8},
            enforce_labor_law=True,
            soft_labor_rules=['max_daily_hours'],
        )
        result = ORToolsProvider().generate_schedule(req)
        assert result.success, result.message
        assert len(result.assignments) == 2

    def test_pattern_preference_with_multi_shift_not_infeasible(
        self, db, organization, branch, employee_role
    ):
        """pattern preference 的 Bool 連結在多班日不可造成 INFEASIBLE（回歸）。"""
        e1 = _make_employee(organization, branch, employee_role, 'P', 1)
        Employee.objects.filter(pk=e1.pk).update(shift_pattern_preference='consecutive')
        am = _shift(organization, 'AM', time(8, 0), time(12, 0))
        pm = _shift(organization, 'PM', time(13, 0), time(17, 0))

        emps, sfts = _provider_inputs([e1], [am, pm])
        emps[0]['shift_pattern_preference'] = 'consecutive'
        start = date(2026, 8, 3)
        req = ScheduleRequest(
            organization_id=organization.id, branch_id=branch.id,
            period_start=start, period_end=start + timedelta(days=2),
            employees=emps, shift_templates=sfts,
            constraints={}, preferences={},
        )
        result = ORToolsProvider().generate_schedule(req)
        assert result.success, result.message


class TestMaxDailyHoursRuleAPI:
    def test_create_max_daily_hours_rule(self, admin_api_client, organization):
        response = admin_api_client.post('/api/shifts/rules/', {
            'organization': organization.pk,
            'name': '每日工時上限',
            'rule_type': 'max_daily_hours',
            'value': {'max_hours': 10},
            'is_active': True,
        }, format='json')
        assert response.status_code == status.HTTP_201_CREATED

    def test_resolve_org_rule_value_shapes(self, db, organization):
        from apps.shifts.rules import resolve_max_daily_hours

        rule = ShiftRule.objects.create(
            organization=organization, name='cap',
            rule_type='max_daily_hours', value={'max_hours': 10},
        )
        assert resolve_max_daily_hours(organization.pk) == 10.0

        rule.value = {'hours': 9}
        rule.save()
        assert resolve_max_daily_hours(organization.pk) == 9.0

        rule.value = 12
        rule.save()
        assert resolve_max_daily_hours(organization.pk) == 12.0

        rule.is_active = False
        rule.save()
        assert resolve_max_daily_hours(organization.pk) is None
