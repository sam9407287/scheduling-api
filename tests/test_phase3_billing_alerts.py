"""
Phase 3 PR12 — monthly usage threshold alerts.

`scan_billing_thresholds` is a Celery task that runs hourly and emails the
org's billing contact when the current month's usage crosses
`alert_threshold_pct`. Phase 3 uses the console email backend; tests run
under pytest-django's locmem backend so `mail.outbox` captures the message.

The task is idempotent — a BillingAlert dedupe row means at most one email
per (period, threshold) per month even though the beat fires hourly.

Covered:

  * Below threshold → no alert, no email.
  * At / above threshold → one alert row + one email to billing_email.
  * Second scan in the same period → no duplicate (dedupe).
  * No cap set (unlimited) → never alerts.
  * Threshold crossing recorded with the token count at alert time.
  * Blank billing_email → alert row still created, no email sent.
  * Two orgs scanned independently.
"""
import pytest  # noqa: F401  (conftest fixtures)
from django.core import mail

from apps.billing.models import (
    OrgBillingSettings, BillingPeriod, BillingAlert, record_usage,
    BillingRateConfig,
)
from apps.billing.tasks import scan_billing_thresholds


def _set_usage(org, tokens):
    """Drive a period's total to `tokens` by recording 'generate' calls
    (rate 10 each from the seeded config). tokens must be a multiple of 10."""
    assert tokens % 10 == 0
    for _ in range(tokens // 10):
        record_usage(org, 'generate', 'success')


class TestThresholdScan:
    def test_below_threshold_no_alert(self, db, organization):
        OrgBillingSettings.objects.create(
            organization=organization, monthly_cap_tokens=100,
            alert_threshold_pct=80, billing_email='ops@example.com',
        )
        _set_usage(organization, 70)  # 70% < 80%
        mail.outbox.clear()
        result = scan_billing_thresholds()
        assert result['alerted'] == 0
        assert BillingAlert.objects.count() == 0
        assert len(mail.outbox) == 0

    def test_at_threshold_sends_one_alert(self, db, organization):
        OrgBillingSettings.objects.create(
            organization=organization, monthly_cap_tokens=100,
            alert_threshold_pct=80, billing_email='ops@example.com',
        )
        _set_usage(organization, 80)  # exactly 80%
        mail.outbox.clear()
        result = scan_billing_thresholds()
        assert result['alerted'] == 1
        alert = BillingAlert.objects.get()
        assert alert.threshold_pct == 80
        assert alert.tokens_at_alert == 80
        assert alert.recipient == 'ops@example.com'
        assert len(mail.outbox) == 1
        assert 'ops@example.com' in mail.outbox[0].to
        assert organization.name in mail.outbox[0].subject

    def test_above_threshold_alerts(self, db, organization):
        OrgBillingSettings.objects.create(
            organization=organization, monthly_cap_tokens=100,
            alert_threshold_pct=75, billing_email='ops@example.com',
        )
        _set_usage(organization, 90)  # 90% > 75%
        mail.outbox.clear()
        scan_billing_thresholds()
        assert BillingAlert.objects.count() == 1

    def test_second_scan_does_not_duplicate(self, db, organization):
        OrgBillingSettings.objects.create(
            organization=organization, monthly_cap_tokens=100,
            alert_threshold_pct=80, billing_email='ops@example.com',
        )
        _set_usage(organization, 80)
        mail.outbox.clear()
        scan_billing_thresholds()
        scan_billing_thresholds()  # hourly beat would call again
        assert BillingAlert.objects.count() == 1
        assert len(mail.outbox) == 1  # not 2

    def test_unlimited_org_never_alerts(self, db, organization):
        OrgBillingSettings.objects.create(
            organization=organization, monthly_cap_tokens=None,  # unlimited
            alert_threshold_pct=80, billing_email='ops@example.com',
        )
        _set_usage(organization, 1000)
        mail.outbox.clear()
        result = scan_billing_thresholds()
        assert result['alerted'] == 0
        assert BillingAlert.objects.count() == 0

    def test_no_settings_row_skipped(self, db, organization):
        # Period exists (usage recorded) but no OrgBillingSettings.
        _set_usage(organization, 50)
        mail.outbox.clear()
        result = scan_billing_thresholds()
        assert result['alerted'] == 0

    def test_blank_email_still_records_alert(self, db, organization):
        OrgBillingSettings.objects.create(
            organization=organization, monthly_cap_tokens=100,
            alert_threshold_pct=80, billing_email='',  # no contact
        )
        _set_usage(organization, 80)
        mail.outbox.clear()
        scan_billing_thresholds()
        # Alert row created (the ledger), but no email delivered.
        assert BillingAlert.objects.count() == 1
        assert len(mail.outbox) == 0

    def test_two_orgs_independent(self, db, organization):
        from apps.organizations.models import Organization
        other = Organization.objects.create(name='Other', code='OTH2')
        OrgBillingSettings.objects.create(
            organization=organization, monthly_cap_tokens=100,
            alert_threshold_pct=80, billing_email='a@example.com',
        )
        OrgBillingSettings.objects.create(
            organization=other, monthly_cap_tokens=100,
            alert_threshold_pct=80, billing_email='b@example.com',
        )
        _set_usage(organization, 80)   # org A crosses
        _set_usage(other, 20)          # org B does not
        mail.outbox.clear()
        result = scan_billing_thresholds()
        assert result['alerted'] == 1
        assert BillingAlert.objects.filter(organization=organization).count() == 1
        assert BillingAlert.objects.filter(organization=other).count() == 0
