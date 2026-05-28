"""
Celery tasks for billing.

`scan_billing_thresholds` runs hourly (see CELERY_BEAT_SCHEDULE) and emails
the org's billing contact when its current-month usage crosses the
configured alert threshold. Phase 3 uses the console email backend so the
message is logged, not delivered — Phase 4 swaps EMAIL_BACKEND for a real
provider with no code change here.

The scan is idempotent: a BillingAlert row dedupes per
(billing_period, threshold_pct), so even though the beat fires every hour
a customer receives at most one alert per threshold per month.
"""
from celery import shared_task
from django.conf import settings
from django.core.mail import send_mail
from django.utils import timezone


@shared_task
def scan_billing_thresholds() -> dict:
    """
    Scan all open BillingPeriods; alert any whose usage has reached the
    org's alert_threshold_pct. Returns a small summary dict for logging /
    test assertions: {scanned, alerted}.
    """
    # Imported here to keep the module import cheap for the Celery worker.
    from .models import (
        BillingPeriod, OrgBillingSettings, BillingAlert,
    )

    now = timezone.now()
    scanned = 0
    alerted = 0

    periods = (
        BillingPeriod.objects
        .filter(status='open', period_year=now.year, period_month=now.month)
        .select_related('organization')
    )
    for period in periods:
        scanned += 1
        cfg = OrgBillingSettings.objects.filter(
            organization=period.organization
        ).first()
        # No settings, no cap, or zero cap → nothing to alert against.
        if not cfg or not cfg.monthly_cap_tokens:
            continue
        cap = cfg.monthly_cap_tokens
        threshold = cfg.alert_threshold_pct
        pct_used = 100.0 * period.total_tokens / cap
        if pct_used < threshold:
            continue
        # Dedupe: one alert per (period, threshold).
        if BillingAlert.objects.filter(
            billing_period=period, threshold_pct=threshold
        ).exists():
            continue

        recipient = cfg.billing_email or ''
        _send_threshold_email(period, cfg, pct_used, recipient)
        BillingAlert.objects.create(
            organization=period.organization,
            billing_period=period,
            threshold_pct=threshold,
            tokens_at_alert=period.total_tokens,
            recipient=recipient,
        )
        alerted += 1

    return {'scanned': scanned, 'alerted': alerted}


def _send_threshold_email(period, cfg, pct_used, recipient) -> None:
    """
    Send (or, with the console backend, log) the threshold alert. Pulled
    out so Phase 4's real-provider swap and richer templating land in one
    place. A blank recipient still goes to the backend so the console log
    captures the event during development.
    """
    org = period.organization
    subject = (
        f'[排班系統] {org.name} 本月 AI 用量已達 {pct_used:.0f}%'
    )
    body = (
        f'機構：{org.name} ({org.code})\n'
        f'期間：{period.period_year}-{period.period_month:02d}\n'
        f'已用 token：{period.total_tokens} / 上限 {cfg.monthly_cap_tokens}'
        f' ({pct_used:.1f}%)\n'
        f'通知門檻：{cfg.alert_threshold_pct}%\n\n'
        f'達上限後，本月所有 AI 排班動作將被拒絕。'
        f'如需繼續使用，請至「帳務 → 設定」調高月度上限。'
    )
    send_mail(
        subject=subject,
        message=body,
        from_email=settings.DEFAULT_FROM_EMAIL,
        recipient_list=[recipient] if recipient else [],
        fail_silently=True,  # console backend never fails; guard real providers
    )
