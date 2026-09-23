"""
Google login (Firebase) MVP provisioning tests.

Product decision (2026-09-17): every Google login IS a manager.
- known firebase_uid → existing user
- verified email matching exactly one unbound user → bind (keeps role/org)
- brand-new account → manager User with NO organization (2026-09-23:
  the user creates their own org in-app; POST organizations binds it)
- unverified email → 403 email_not_verified; inactive user → 403
Firebase verification is mocked — no real credentials needed.
"""
from datetime import date

import pytest
from rest_framework import exceptions

from apps.accounts.authentication import FirebaseAuthentication
from apps.accounts.models import Role, User
from apps.organizations.models import Organization

pytestmark = pytest.mark.django_db


class FakeRequest:
    def __init__(self, header):
        self.META = {'HTTP_AUTHORIZATION': header} if header else {}


@pytest.fixture
def mock_firebase(monkeypatch):
    """Patch the Firebase verify path; tests set `payload` per case."""
    state = {'payload': None}
    monkeypatch.setattr('apps.accounts.authentication.get_firebase_app', lambda: None)
    monkeypatch.setattr(
        'apps.accounts.authentication.auth.verify_id_token',
        lambda token: state['payload'],
    )
    return state


def _authenticate(payload, mock_firebase):
    mock_firebase['payload'] = payload
    user, _ = FirebaseAuthentication().authenticate(FakeRequest('Bearer faketoken'))
    return user


GOOGLE_PAYLOAD = {
    'uid': 'google-uid-001',
    'email': 'newmanager@gmail.com',
    'email_verified': True,
    'name': '王 大明',
}


class TestAutoProvision:
    def test_new_google_account_becomes_manager_without_org(self, mock_firebase):
        user = _authenticate(GOOGLE_PAYLOAD, mock_firebase)
        assert user.firebase_uid == 'google-uid-001'
        assert user.role.name == 'manager'
        assert user.organization is None              # 機構進系統後自建
        assert user.email == 'newmanager@gmail.com'
        assert Organization.objects.count() == 0      # 不再自動開機構

    def test_second_login_reuses_same_user(self, mock_firebase):
        first = _authenticate(GOOGLE_PAYLOAD, mock_firebase)
        second = _authenticate(GOOGLE_PAYLOAD, mock_firebase)
        assert first.pk == second.pk
        assert User.objects.count() == 1
        assert Organization.objects.count() == 0


class TestEmailBinding:
    def test_verified_email_binds_precreated_account(self, mock_firebase, organization):
        role = Role.objects.create(name='admin', permissions={})
        existing = User.objects.create_user(
            username='sam', email='newmanager@gmail.com', password='pw',
            role=role, organization=organization,
        )
        user = _authenticate(GOOGLE_PAYLOAD, mock_firebase)
        assert user.pk == existing.pk
        assert user.firebase_uid == 'google-uid-001'
        assert user.role.name == 'admin'              # 保留原角色
        assert user.organization_id == organization.pk  # 保留原機構
        assert Organization.objects.count() == 1      # 不另建租戶

    def test_unverified_email_rejected(self, mock_firebase):
        with pytest.raises(exceptions.AuthenticationFailed) as excinfo:
            _authenticate({**GOOGLE_PAYLOAD, 'email_verified': False}, mock_firebase)
        assert excinfo.value.detail['code'] == 'email_not_verified'

    def test_inactive_user_rejected(self, mock_firebase):
        user = _authenticate(GOOGLE_PAYLOAD, mock_firebase)
        User.objects.filter(pk=user.pk).update(is_active=False)
        with pytest.raises(exceptions.AuthenticationFailed) as excinfo:
            _authenticate(GOOGLE_PAYLOAD, mock_firebase)
        assert excinfo.value.detail['code'] == 'account_inactive'


class TestSchemeFallthrough:
    def test_token_scheme_ignored(self, mock_firebase):
        """Token scheme 交給 TokenAuthentication，不在這裡失敗。"""
        result = FirebaseAuthentication().authenticate(FakeRequest('Token abc123'))
        assert result is None

    def test_no_header_ignored(self, mock_firebase):
        assert FirebaseAuthentication().authenticate(FakeRequest(None)) is None
