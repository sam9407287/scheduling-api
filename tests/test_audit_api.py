"""
Audit-log read API tests (操作日誌接真資料，2026-09-18).

The API is strictly read-only; rows are created by the signal pipeline
(disabled in tests — rows are created directly here).
"""
from datetime import date

import pytest
from rest_framework import status
from rest_framework.test import APIClient

from apps.accounts.models import Role, User
from apps.audit.models import AuditLog
from apps.organizations.models import Organization

pytestmark = pytest.mark.django_db

URL = '/api/audit/logs/'


@pytest.fixture
def manager_api_client(manager_user):
    client = APIClient()
    client.force_authenticate(user=manager_user)
    return client


def _log(user, action='update', model_name='Schedule', record_id=1, changes=None):
    return AuditLog.objects.create(
        user=user, action=action, model_name=model_name,
        record_id=record_id, changes=changes or {'field': 'x'},
    )


class TestAuditLogAPI:
    def test_list_and_detail(self, manager_api_client, manager_user):
        row = _log(manager_user, action='cancel',
                   changes={'reason': '取消簽核測試'})
        listing = manager_api_client.get(URL)
        assert listing.status_code == status.HTTP_200_OK
        assert listing.data['count'] == 1
        item = listing.data['results'][0]
        assert item['action'] == 'cancel'
        assert item['user_name']
        assert 'old_data' not in item  # 清單瘦身，快照只在 detail

        detail = manager_api_client.get(f'{URL}{row.pk}/')
        assert 'old_data' in detail.data and 'ip_address' in detail.data

    def test_filters(self, manager_api_client, manager_user):
        _log(manager_user, action='create', model_name='LeaveRequest')
        _log(manager_user, action='delete', model_name='Schedule')
        by_action = manager_api_client.get(f'{URL}?action=delete')
        assert by_action.data['count'] == 1
        by_model = manager_api_client.get(f'{URL}?model_name=leaverequest')
        assert by_model.data['count'] == 1
        by_search = manager_api_client.get(f'{URL}?search=Sched')
        assert by_search.data['count'] == 1

    def test_org_isolation(self, manager_api_client, manager_user):
        other_org = Organization.objects.create(
            name='他機構', code='AUD-X', address='x', phone='1', email='a@b.c')
        role = Role.objects.create(name='manager', permissions={}) \
            if not Role.objects.filter(name='manager').exists() \
            else Role.objects.get(name='manager')
        outsider = User.objects.create_user(
            username='aud_outsider', password='pw', role=role, organization=other_org)
        _log(manager_user)          # 自己機構
        _log(outsider)              # 他機構
        _log(None, action='update') # 系統寫入（user=None）

        listing = manager_api_client.get(URL)
        assert listing.data['count'] == 1  # 只看得到自己機構的

    def test_employee_forbidden(self, employee_api_client):
        response = employee_api_client.get(URL)
        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_read_only(self, manager_api_client, manager_user):
        row = _log(manager_user)
        assert manager_api_client.post(URL, {}, format='json').status_code == 405
        assert manager_api_client.delete(f'{URL}{row.pk}/').status_code == 405


class TestAuditCapturesTokenAuthUser:
    """API 寫入的稽核必須記到操作者，不能因 DRF 認證時序記成系統。"""

    def test_api_write_logs_acting_user(self, manager_api_client, manager_user,
                                        monkeypatch, settings):
        from apps.audit import signals as audit_signals
        monkeypatch.setattr(audit_signals, '_audit_disabled', False)
        # 測試設定平常拔掉 audit middleware；這裡補回來模擬生產路徑
        settings.MIDDLEWARE = settings.MIDDLEWARE + ['apps.audit.middleware.AuditLogMiddleware']
        response = manager_api_client.post('/api/employees/certifications/', {
            'name': '稽核測試證照', 'code': 'AUDIT-CERT',
        }, format='json')
        assert response.status_code == status.HTTP_201_CREATED
        log = AuditLog.objects.filter(
            model_name='employees.certification', action='create',
        ).order_by('-timestamp').first()
        assert log is not None
        assert log.user == manager_user  # 不是 None（系統）
