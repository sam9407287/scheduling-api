"""
Self-service email + password registration（2026-10-22）.

Pure helpers around EmailVerificationCode: issue a code (rate-limited,
hashed at rest, delivered through Django's email backend — SMTP via any
provider, Gmail app-password being the expected production setup) and
verify it when the account is created. Views in `views.py` are thin.

Tunables (settings.EMAIL_VERIFICATION, all optional):
    CODE_TTL_SECONDS            600   驗證碼有效期
    RESEND_COOLDOWN_SECONDS      60   同 email 重寄間隔
    MAX_SENDS_PER_EMAIL_PER_HOUR  5
    MAX_SENDS_PER_IP_PER_HOUR    20
    MAX_ATTEMPTS                  5   驗證失敗上限（超過須重新索取）
"""
import hashlib
import hmac
import secrets
from dataclasses import dataclass
from datetime import timedelta
from typing import Optional

from django.conf import settings
from django.core.mail import send_mail
from django.utils import timezone

from .models import EmailVerificationCode

DEFAULTS = {
    'CODE_TTL_SECONDS': 600,
    'RESEND_COOLDOWN_SECONDS': 60,
    'MAX_SENDS_PER_EMAIL_PER_HOUR': 5,
    'MAX_SENDS_PER_IP_PER_HOUR': 20,
    'MAX_ATTEMPTS': 5,
}


def _cfg(key: str) -> int:
    return int(getattr(settings, 'EMAIL_VERIFICATION', {}).get(key, DEFAULTS[key]))


def normalize_email(email: str) -> str:
    return (email or '').strip().lower()


def hash_code(email: str, code: str) -> str:
    payload = f'{settings.SECRET_KEY}:{normalize_email(email)}:{code}'.encode()
    return hashlib.sha256(payload).hexdigest()


class RateLimited(Exception):
    def __init__(self, retry_after_seconds: int, reason: str):
        super().__init__(reason)
        self.retry_after_seconds = retry_after_seconds
        self.reason = reason


@dataclass
class IssuedCode:
    row: EmailVerificationCode
    code: str  # plaintext, only for delivery (and dev echo)
    ttl_seconds: int
    cooldown_seconds: int


def check_send_allowed(email: str, ip: Optional[str], purpose: str = 'register') -> None:
    """Raise RateLimited when this email / IP has asked too often."""
    now = timezone.now()
    hour_ago = now - timedelta(hours=1)
    cooldown = _cfg('RESEND_COOLDOWN_SECONDS')

    latest = (EmailVerificationCode.objects
              .filter(email=email, purpose=purpose)
              .order_by('-created_at').first())
    if latest is not None:
        elapsed = (now - latest.created_at).total_seconds()
        if elapsed < cooldown:
            raise RateLimited(int(cooldown - elapsed) + 1, 'resend_cooldown')

    per_email = EmailVerificationCode.objects.filter(
        email=email, purpose=purpose, created_at__gte=hour_ago,
    ).count()
    if per_email >= _cfg('MAX_SENDS_PER_EMAIL_PER_HOUR'):
        raise RateLimited(3600, 'email_hourly_limit')

    if ip:
        per_ip = EmailVerificationCode.objects.filter(
            ip_address=ip, created_at__gte=hour_ago,
        ).count()
        if per_ip >= _cfg('MAX_SENDS_PER_IP_PER_HOUR'):
            raise RateLimited(3600, 'ip_hourly_limit')


def issue_code(email: str, ip: Optional[str], purpose: str = 'register') -> IssuedCode:
    """Create a fresh code row (older unconsumed ones for this email are
    invalidated so only the newest code works)."""
    email = normalize_email(email)
    check_send_allowed(email, ip, purpose)
    code = f'{secrets.randbelow(10 ** 6):06d}'
    now = timezone.now()
    ttl = _cfg('CODE_TTL_SECONDS')
    EmailVerificationCode.objects.filter(
        email=email, purpose=purpose, consumed_at__isnull=True,
    ).update(consumed_at=now)  # 舊碼作廢
    row = EmailVerificationCode.objects.create(
        email=email, purpose=purpose,
        code_hash=hash_code(email, code),
        ip_address=ip,
        expires_at=now + timedelta(seconds=ttl),
    )
    return IssuedCode(row=row, code=code, ttl_seconds=ttl,
                      cooldown_seconds=_cfg('RESEND_COOLDOWN_SECONDS'))


def send_code_email(email: str, code: str, ttl_seconds: int) -> None:
    """Deliver through settings.EMAIL_BACKEND. Raises on SMTP failure so the
    view can report 503 instead of pretending the mail went out."""
    minutes = max(1, ttl_seconds // 60)
    subject = '【智慧排班系統】註冊驗證碼'
    body = (
        f'您的註冊驗證碼是：{code}\n\n'
        f'此驗證碼 {minutes} 分鐘內有效，請回到註冊頁面輸入。\n'
        f'如果您沒有申請註冊，請忽略這封信。\n'
    )
    send_mail(subject, body, getattr(settings, 'DEFAULT_FROM_EMAIL', None), [email],
              fail_silently=False)


class CodeInvalid(Exception):
    """code: invalid_code | code_expired | code_locked"""
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def verify_code(email: str, code: str, purpose: str = 'register') -> EmailVerificationCode:
    """Return the matching live row (not yet consumed) or raise CodeInvalid.
    Failed attempts are counted; after MAX_ATTEMPTS the row is locked."""
    email = normalize_email(email)
    now = timezone.now()
    row = (EmailVerificationCode.objects
           .filter(email=email, purpose=purpose, consumed_at__isnull=True)
           .order_by('-created_at').first())
    if row is None:
        raise CodeInvalid('code_expired')
    if row.expires_at < now:
        raise CodeInvalid('code_expired')
    if row.attempts >= _cfg('MAX_ATTEMPTS'):
        raise CodeInvalid('code_locked')
    if not hmac.compare_digest(row.code_hash, hash_code(email, (code or '').strip())):
        EmailVerificationCode.objects.filter(pk=row.pk).update(attempts=row.attempts + 1)
        if row.attempts + 1 >= _cfg('MAX_ATTEMPTS'):
            raise CodeInvalid('code_locked')
        raise CodeInvalid('invalid_code')
    return row
