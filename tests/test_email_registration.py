"""
Email 驗證碼自助註冊（2026-10-22）。

- send-code：寄 6 位數碼（任何信箱）、已註冊 409、節流 429（60 秒 cooldown / 每小時上限）
- register：正確碼 → 201 + Token，manager、無機構、username=email
- 錯碼 / 過期 / 鎖定 / 弱密碼 / 重複 email 各自的錯誤碼
- 用 email 登入 /auth/login/
- 之後用同 email Google 登入會綁到同一個帳號（一個 email 一個帳號）
"""
import re
from datetime import timedelta

import pytest
from django.core import mail
from django.utils import timezone
from rest_framework.test import APIClient

from apps.accounts.models import EmailVerificationCode, User

pytestmark = pytest.mark.django_db

SEND = '/api/auth/register/send-code/'
REGISTER = '/api/auth/register/'
LOGIN = '/api/auth/login/'


@pytest.fixture
def client():
    return APIClient()


def _send(client, email='New.User@Example.com', ip='203.0.113.9'):
    return client.post(SEND, {'email': email}, format='json', REMOTE_ADDR=ip)


def _code_from_mail():
    body = mail.outbox[-1].body
    return re.search(r'(\d{6})', body).group(1)


def _register(client, email='new.user@example.com', code=None, password='Str0ng-Pass-2026',
              **extra):
    payload = {'email': email, 'code': code or _code_from_mail(), 'password': password, **extra}
    return client.post(REGISTER, payload, format='json')


class TestSendCode:
    def test_sends_six_digit_code_to_any_mailbox(self, client):
        resp = _send(client, 'someone@outlook.com')
        assert resp.status_code == 200, resp.content
        assert resp.json()['email'] == 'someone@outlook.com'
        assert resp.json()['expires_in_seconds'] == 600
        assert 'debug_code' not in resp.json()  # 測試/生產都不回碼
        assert len(mail.outbox) == 1
        assert mail.outbox[0].to == ['someone@outlook.com']
        assert re.search(r'\d{6}', mail.outbox[0].body)
        row = EmailVerificationCode.objects.get()
        assert row.email == 'someone@outlook.com'
        assert row.ip_address == '203.0.113.9'
        assert row.code_hash != _code_from_mail()  # 只存 hash

    def test_existing_email_is_409(self, client, manager_user):
        manager_user.email = 'taken@example.com'
        manager_user.save()
        resp = _send(client, 'TAKEN@example.com')
        assert resp.status_code == 409
        assert resp.json()['code'] == 'email_already_registered'
        assert not mail.outbox

    def test_invalid_email_is_400(self, client):
        assert _send(client, 'not-an-email').status_code == 400

    def test_resend_cooldown(self, client):
        assert _send(client).status_code == 200
        resp = _send(client)
        assert resp.status_code == 429
        assert resp.json()['code'] == 'too_many_requests'
        assert resp.json()['reason'] == 'resend_cooldown'
        assert 0 < resp.json()['retry_after_seconds'] <= 61
        assert resp['Retry-After']

    def test_hourly_limit_per_email(self, client):
        # 5 封都在一小時內（往前撥 cooldown）→ 第 6 封被擋
        for _ in range(5):
            assert _send(client).status_code == 200
            EmailVerificationCode.objects.update(created_at=timezone.now() - timedelta(minutes=2))
        resp = _send(client)
        assert resp.status_code == 429
        assert resp.json()['reason'] == 'email_hourly_limit'

    def test_hourly_limit_per_ip(self, client, settings):
        settings.EMAIL_VERIFICATION = {**settings.EMAIL_VERIFICATION, 'MAX_SENDS_PER_IP_PER_HOUR': 3}
        for i in range(3):
            assert _send(client, f'bot{i}@example.com').status_code == 200
        resp = _send(client, 'bot9@example.com')
        assert resp.status_code == 429
        assert resp.json()['reason'] == 'ip_hourly_limit'

    def test_smtp_failure_is_503_and_frees_cooldown(self, client, monkeypatch):
        from apps.accounts import registration

        def boom(*a, **k):
            raise ConnectionRefusedError('smtp down')
        monkeypatch.setattr(registration, 'send_code_email', boom)
        resp = _send(client)
        assert resp.status_code == 503
        assert resp.json()['code'] == 'email_send_failed'
        assert EmailVerificationCode.objects.count() == 0

    def test_dev_setting_echoes_code(self, client, settings):
        settings.EMAIL_VERIFICATION_EXPOSE_CODE = True
        resp = _send(client)
        assert resp.json()['debug_code'] == _code_from_mail()


class TestRegister:
    def test_happy_path(self, client):
        _send(client)
        resp = _register(client, first_name='小', last_name='明', phone='0912345678')
        assert resp.status_code == 201, resp.content
        body = resp.json()
        assert body['token']
        assert body['user']['role_name'] == 'manager'
        assert body['user']['organization'] is None
        assert body['user']['username'] == 'new.user@example.com'
        assert body['user']['firebase_linked'] is False
        user = User.objects.get(email='new.user@example.com')
        assert user.check_password('Str0ng-Pass-2026')
        assert user.first_name == '小' and user.phone == '0912345678'
        assert EmailVerificationCode.objects.get().consumed_at is not None
        # token 可用
        api = APIClient()
        api.credentials(HTTP_AUTHORIZATION=f'Token {body["token"]}')
        assert api.get('/api/auth/users/me/').status_code == 200

    def test_wrong_code(self, client):
        _send(client)
        resp = _register(client, code='000000' if _code_from_mail() != '000000' else '111111')
        assert resp.status_code == 400
        assert resp.json()['code'] == 'invalid_code'
        assert EmailVerificationCode.objects.get().attempts == 1
        assert not User.objects.filter(email='new.user@example.com').exists()

    def test_locks_after_max_attempts(self, client):
        _send(client)
        real = _code_from_mail()
        wrong = '000000' if real != '000000' else '111111'
        for _ in range(4):
            assert _register(client, code=wrong).json()['code'] == 'invalid_code'
        assert _register(client, code=wrong).json()['code'] == 'code_locked'
        # 正確碼也不再接受，要重寄
        assert _register(client, code=real).json()['code'] == 'code_locked'

    def test_expired_code(self, client):
        _send(client)
        EmailVerificationCode.objects.update(expires_at=timezone.now() - timedelta(seconds=1))
        resp = _register(client)
        assert resp.status_code == 400
        assert resp.json()['code'] == 'code_expired'

    def test_no_code_requested(self, client):
        resp = _register(client, code='123456')
        assert resp.status_code == 400
        assert resp.json()['code'] == 'code_expired'

    def test_only_newest_code_valid(self, client):
        _send(client)
        first = _code_from_mail()
        EmailVerificationCode.objects.update(created_at=timezone.now() - timedelta(minutes=2))
        _send(client)
        assert _register(client, code=first).json()['code'] in ('invalid_code', 'code_expired')
        assert _register(client).status_code == 201

    def test_weak_password(self, client):
        _send(client)
        resp = _register(client, password='12345678')
        assert resp.status_code == 400
        assert resp.json()['code'] == 'weak_password'
        assert resp.json()['details']
        # 碼沒被消耗，改密碼再試可成功
        assert _register(client).status_code == 201

    def test_code_is_single_use(self, client):
        _send(client)
        code = _code_from_mail()
        assert _register(client, code=code).status_code == 201
        resp = _register(client, code=code)
        assert resp.status_code == 409  # email 已註冊優先回報

    def test_email_taken_by_google_user(self, client, manager_user):
        _send(client)
        manager_user.email = 'new.user@example.com'
        manager_user.firebase_uid = 'g-uid'
        manager_user.save()
        resp = _register(client)
        assert resp.status_code == 409
        assert resp.json()['code'] == 'email_already_registered'


class TestLoginAndLinking:
    def test_login_with_email(self, client):
        _send(client)
        assert _register(client).status_code == 201
        resp = client.post(LOGIN, {'email': 'New.User@example.com', 'password': 'Str0ng-Pass-2026'},
                           format='json')
        assert resp.status_code == 200, resp.content
        assert resp.json()['token']
        # username 欄位填 email 也行
        resp = client.post(LOGIN, {'username': 'new.user@example.com',
                                   'password': 'Str0ng-Pass-2026'}, format='json')
        assert resp.status_code == 200

    def test_login_requires_identifier(self, client):
        assert client.post(LOGIN, {'password': 'x'}, format='json').status_code == 400

    def test_google_login_binds_to_registered_account(self, client, monkeypatch):
        _send(client)
        assert _register(client).status_code == 201
        from apps.accounts.authentication import FirebaseAuthentication
        monkeypatch.setattr('apps.accounts.authentication.get_firebase_app', lambda: None)
        monkeypatch.setattr('apps.accounts.authentication.auth.verify_id_token',
                            lambda token: {'uid': 'g-123', 'email': 'new.user@example.com',
                                           'email_verified': True})

        class Req:
            META = {'HTTP_AUTHORIZATION': 'Bearer x'}
        user, _ = FirebaseAuthentication().authenticate(Req())
        assert user.username == 'new.user@example.com'
        assert user.firebase_uid == 'g-123'
        assert User.objects.filter(email='new.user@example.com').count() == 1
