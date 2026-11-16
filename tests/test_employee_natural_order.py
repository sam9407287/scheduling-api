"""
員工清單依員工編號「自然排序」（2026-11-16，PM 回報）。

PM 的機構員工編號是 1、2、3…14。字串排序會排成 1, 10, 11, 12, 13, 14, 2, 3…，
員工列表與排班表都很難找人；要照數字大小排。
"""
from datetime import date

import pytest
from django.contrib.auth import get_user_model

from apps.employees.models import Employee
from apps.employees.ordering import natural_key, sort_employees_naturally

User = get_user_model()
URL = '/api/employees/employees/'


def _employee(org, code):
    user = User.objects.create_user(username=f'natural-{org.pk}-{code}', password='x')
    return Employee.objects.create(
        user=user, employee_id=code, organization=org,
        position='護理師', hire_date=date(2024, 1, 1),
    )


def _codes(resp):
    return [row['employee_id'] for row in resp.data['results']]


class TestNaturalKey:
    def test_numbers_sort_by_value(self):
        codes = ['1', '10', '11', '12', '13', '14', '2', '3', '4', '5']
        assert sorted(codes, key=natural_key) == ['1', '2', '3', '4', '5', '10', '11', '12', '13', '14']

    def test_prefixed_codes_sort_by_their_number(self):
        codes = ['EMP10', 'EMP2', 'EMP1', 'emp3']
        assert sorted(codes, key=natural_key) == ['EMP1', 'EMP2', 'emp3', 'EMP10']

    def test_zero_padded_codes_keep_their_order(self):
        codes = ['EMP010', 'EMP002', 'EMP001']
        assert sorted(codes, key=natural_key) == ['EMP001', 'EMP002', 'EMP010']

    def test_mixed_numeric_and_text_codes_do_not_raise(self):
        codes = ['A-3', '7', 'B1', '12', 'A-12', '']
        assert sorted(codes, key=natural_key) == ['', '7', '12', 'A-3', 'A-12', 'B1']

    def test_leading_zero_variants_have_a_stable_order(self):
        assert sorted(['7', '007', '07'], key=natural_key) == ['007', '07', '7']


@pytest.mark.django_db
class TestEmployeeListNaturalOrder:
    def test_list_is_ordered_by_number(self, admin_api_client, organization):
        for code in ['1', '10', '11', '12', '13', '14', '2', '3', '4', '5']:
            _employee(organization, code)
        resp = admin_api_client.get(URL, {'organization': organization.pk})
        assert resp.status_code == 200
        assert _codes(resp) == ['1', '2', '3', '4', '5', '10', '11', '12', '13', '14']

    def test_order_holds_across_pages(self, admin_api_client, organization):
        """PAGE_SIZE=20：第 1 頁是 1–20、第 2 頁是 21–25，不能因為分頁又變回字串順序。"""
        for n in range(25, 0, -1):
            _employee(organization, str(n))
        first = admin_api_client.get(URL, {'organization': organization.pk})
        second = admin_api_client.get(URL, {'organization': organization.pk, 'page': 2})
        assert first.data['count'] == 25
        assert _codes(first) == [str(n) for n in range(1, 21)]
        assert _codes(second) == [str(n) for n in range(21, 26)]

    def test_explicit_ordering_param_is_respected(self, admin_api_client, organization, monkeypatch):
        """呼叫端明確給 `?ordering=` 時照資料庫排序，不再套自然排序。

        測試設定沒有掛 OrderingFilter（正式環境有），這裡補上才測得到兩者的互動。
        """
        from rest_framework.filters import OrderingFilter
        from apps.employees.views import EmployeeViewSet
        monkeypatch.setattr(EmployeeViewSet, 'filter_backends', [OrderingFilter])
        for code in ['2', '10', '1']:
            _employee(organization, code)
        resp = admin_api_client.get(URL, {'organization': organization.pk, 'ordering': '-employee_id'})
        assert _codes(resp) == ['2', '10', '1']      # 字串倒序，不是自然排序的 10, 2, 1

    def test_filters_still_apply(self, admin_api_client, organization):
        _employee(organization, '2')
        inactive = _employee(organization, '10')
        inactive.is_active = False
        inactive.save(update_fields=['is_active'])
        _employee(organization, '1')
        resp = admin_api_client.get(URL, {'organization': organization.pk, 'is_active': 'true'})
        assert _codes(resp) == ['1', '2']

    def test_helper_breaks_ties_between_orgs_by_pk(self, organization):
        from apps.organizations.models import Organization
        other = Organization.objects.create(name='別的機構', code='OTHER-NAT')
        a = _employee(organization, '1')
        b = _employee(other, '1')
        assert sort_employees_naturally([b, a]) == [a, b]
