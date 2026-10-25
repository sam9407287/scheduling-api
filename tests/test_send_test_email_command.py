"""`manage.py send_test_email` — 部署後驗證 SMTP 用的指令。"""
from io import StringIO
from unittest import mock

import pytest
from django.core import mail
from django.core.management import call_command
from django.core.management.base import CommandError


def test_sends_one_mail_and_prints_backend():
    out = StringIO()
    call_command('send_test_email', 'ops@example.com', stdout=out)
    assert len(mail.outbox) == 1
    assert mail.outbox[0].to == ['ops@example.com']
    assert 'SMTP 測試信' in mail.outbox[0].subject
    assert 'EMAIL_BACKEND' in out.getvalue()
    assert '已寄出測試信到 ops@example.com' in out.getvalue()


def test_smtp_failure_surfaces_as_command_error():
    with mock.patch('apps.accounts.management.commands.send_test_email.send_mail',
                    side_effect=OSError('[Errno 111] Connection refused')):
        with pytest.raises(CommandError, match='寄信失敗：OSError'):
            call_command('send_test_email', 'ops@example.com', stdout=StringIO())
