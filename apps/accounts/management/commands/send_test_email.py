"""
`python manage.py send_test_email you@example.com`

部署後確認 SMTP 設定（EMAIL_HOST_USER / EMAIL_HOST_PASSWORD）真的能寄信用。
不走節流、不寫 EmailVerificationCode，純粹用 settings.EMAIL_BACKEND 寄一封測試信；
寄失敗會把 SMTP 例外原樣印出來並以非零碼結束，方便在 Railway 上
`railway ssh --service web -- python manage.py send_test_email you@example.com` 直接看原因。
"""
from django.conf import settings
from django.core.mail import send_mail
from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = '寄一封測試信確認 SMTP 設定（EMAIL_HOST_USER / EMAIL_HOST_PASSWORD）可用'

    def add_arguments(self, parser):
        parser.add_argument('to', help='收件信箱')

    def handle(self, *args, **options):
        to = options['to']
        backend = settings.EMAIL_BACKEND
        self.stdout.write(f'EMAIL_BACKEND      = {backend}')
        self.stdout.write(f'EMAIL_HOST         = {settings.EMAIL_HOST}:{settings.EMAIL_PORT} '
                          f'(TLS={settings.EMAIL_USE_TLS}, SSL={settings.EMAIL_USE_SSL})')
        self.stdout.write(f'EMAIL_HOST_USER    = {settings.EMAIL_HOST_USER or "(未設定)"}')
        self.stdout.write(f'DEFAULT_FROM_EMAIL = {settings.DEFAULT_FROM_EMAIL}')
        if backend.endswith('console.EmailBackend'):
            self.stdout.write(self.style.WARNING(
                '目前是 console backend：EMAIL_HOST_USER 沒設，信只會印在 log，不會真的寄出。'))
        try:
            sent = send_mail(
                '【智慧排班系統】SMTP 測試信',
                '這是一封由 send_test_email 指令寄出的測試信，收到代表 SMTP 設定正確。',
                settings.DEFAULT_FROM_EMAIL, [to], fail_silently=False,
            )
        except Exception as exc:  # noqa: BLE001 — 要把 SMTP 原始錯誤印給操作者看
            raise CommandError(f'寄信失敗：{type(exc).__name__}: {exc}') from exc
        if sent != 1:
            raise CommandError(f'send_mail 回傳 {sent}，信未寄出')
        self.stdout.write(self.style.SUCCESS(f'已寄出測試信到 {to}'))
