"""
Metered billing models.

Design (locked in 2026-05-25 with the customer):
  * Charging model is metered, **not** prepaid wallet. Customers attach a
    credit card and get billed per usage with a configurable monthly cap.
  * Charging is pre-debit, no refund: an AI call records a UsageRecord
    even when the solver fails. This keeps accounting simple and is what
    the customer asked for explicitly ("先扱不退").
  * Phase 2 only writes UsageRecords and enforces the monthly cap; real
    payment provider integration (Stripe) lands in Phase 3, so
    `PaymentMethod` carries the schema fields Stripe will need but the
    `provider='mock'` rows are dummy until then.

Aggregation strategy:
  * Each `UsageRecord` belongs to one `BillingPeriod` (org × YYYY-MM).
    The period row carries a denormalised `total_tokens` updated by
    `record_usage(...)` under `select_for_update`. That gives the
    pre-flight cap check an O(1) read; otherwise the AI generate hot path
    would have to SUM across UsageRecords on every call.
"""
from django.db import models, transaction
from django.db.models import Sum
from django.core.validators import MinValueValidator
from django.utils import timezone


# Locked enumeration: must match the `billing_mode` strings emitted by
# `ai_engine.views.generate` in `metadata.billing.billing_mode`.
BILLING_MODES = [
    ('generate', '全自動生成 B'),
    ('fill_gaps', 'AI 補齊'),
    ('derive_legal', '派生 A'),
]


class BillingRateConfig(models.Model):
    """
    Admin-tunable flat-fee rate per billing_mode.

    History is preserved by `effective_from`: pricing changes create a new
    row, the old row stays for audit. `current_rate_for(mode)` picks the
    row whose `effective_from <= now`.
    """
    billing_mode = models.CharField(
        max_length=20, choices=BILLING_MODES, verbose_name='計費類型',
    )
    tokens_per_call = models.PositiveIntegerField(
        validators=[MinValueValidator(0)],
        verbose_name='每次 token 數',
    )
    effective_from = models.DateTimeField(
        default=timezone.now, verbose_name='生效時間',
    )
    notes = models.TextField(blank=True, verbose_name='備註')

    class Meta:
        verbose_name = '計費費率'
        verbose_name_plural = '計費費率'
        ordering = ['billing_mode', '-effective_from']
        indexes = [
            models.Index(fields=['billing_mode', '-effective_from']),
        ]

    def __str__(self):
        return f'{self.get_billing_mode_display()} = {self.tokens_per_call} tokens (from {self.effective_from:%Y-%m-%d})'

    @classmethod
    def current_rate_for(cls, billing_mode: str) -> int:
        """Return the active token cost for the given mode, or 0 if unset."""
        now = timezone.now()
        row = (
            cls.objects
            .filter(billing_mode=billing_mode, effective_from__lte=now)
            .order_by('-effective_from')
            .first()
        )
        return row.tokens_per_call if row else 0


class OrgBillingSettings(models.Model):
    """
    Per-organisation billing preferences. One row per org; created lazily.
    `monthly_cap_tokens = None` means unlimited.
    """
    organization = models.OneToOneField(
        'organizations.Organization',
        on_delete=models.CASCADE,
        related_name='billing_settings',
        verbose_name='所屬機構',
    )
    monthly_cap_tokens = models.PositiveIntegerField(
        null=True, blank=True,
        verbose_name='月度上限 (None = 不限)',
    )
    alert_threshold_pct = models.PositiveSmallIntegerField(
        default=80,
        validators=[MinValueValidator(1)],
        verbose_name='通知門檻 (% of cap)',
        help_text='達 cap 的此百分比時寄通知',
    )
    billing_email = models.EmailField(
        blank=True, default='',
        verbose_name='帳單通知 email',
    )
    is_billing_enabled = models.BooleanField(
        default=True,
        verbose_name='啟用計費 (False = AI 拒絕呼叫)',
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = '機構計費設定'
        verbose_name_plural = '機構計費設定'

    def __str__(self):
        cap = self.monthly_cap_tokens if self.monthly_cap_tokens is not None else '∞'
        return f'{self.organization.code} cap={cap}'


class BillingPeriod(models.Model):
    """
    Org × YYYY-MM monthly bucket. `total_tokens` is the denormalised sum of
    all UsageRecords in the month; bumped under select_for_update inside
    `record_usage` to stay race-safe.
    """
    STATUS_CHOICES = [
        ('open', '開放中'),    # 本月，仍可計費
        ('closed', '已結算'),  # 月底切換
        ('billed', '已開立帳單'),  # Stripe 月結回拋
    ]
    organization = models.ForeignKey(
        'organizations.Organization',
        on_delete=models.CASCADE,
        related_name='billing_periods',
        verbose_name='所屬機構',
    )
    period_year = models.PositiveSmallIntegerField(verbose_name='年')
    period_month = models.PositiveSmallIntegerField(verbose_name='月 (1-12)')
    total_tokens = models.PositiveIntegerField(
        default=0, verbose_name='累積 token 數',
    )
    status = models.CharField(
        max_length=10, choices=STATUS_CHOICES, default='open',
        verbose_name='狀態',
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = '計費週期'
        verbose_name_plural = '計費週期'
        unique_together = [['organization', 'period_year', 'period_month']]
        indexes = [
            models.Index(fields=['organization', 'period_year', 'period_month']),
        ]
        ordering = ['-period_year', '-period_month']

    def __str__(self):
        return f'{self.organization.code} {self.period_year}-{self.period_month:02d} ({self.total_tokens} tokens)'

    @classmethod
    def current_for(cls, organization) -> 'BillingPeriod':
        """Get or create the current month's period for the org."""
        now = timezone.now()
        period, _ = cls.objects.get_or_create(
            organization=organization,
            period_year=now.year,
            period_month=now.month,
            defaults={'status': 'open', 'total_tokens': 0},
        )
        return period


class UsageRecord(models.Model):
    """
    One row per AI call. Pre-debit: created even when `solver_status` is
    'infeasible' or 'error' (the customer chose "先扱不退").
    """
    SOLVER_STATUS_CHOICES = [
        ('success', '成功'),
        ('infeasible', '無解'),
        ('error', '錯誤'),
    ]

    organization = models.ForeignKey(
        'organizations.Organization',
        on_delete=models.CASCADE,
        related_name='usage_records',
        verbose_name='所屬機構',
    )
    billing_period = models.ForeignKey(
        BillingPeriod,
        on_delete=models.CASCADE,
        related_name='records',
        verbose_name='所屬週期',
    )
    billing_mode = models.CharField(
        max_length=20, choices=BILLING_MODES, verbose_name='計費類型',
    )
    tokens_charged = models.PositiveIntegerField(
        verbose_name='扱費 token 數',
    )
    solver_status = models.CharField(
        max_length=20, choices=SOLVER_STATUS_CHOICES,
        verbose_name='求解狀態',
    )
    schedule_version = models.ForeignKey(
        'schedules.ScheduleVersion',
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='usage_records',
        verbose_name='相關排班版本',
    )
    user = models.ForeignKey(
        'accounts.User',
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='usage_records',
        verbose_name='觸發人',
    )
    request_metadata = models.JSONField(
        default=dict, blank=True,
        verbose_name='請求元資料',
        help_text='period_start, period_end, employee count, etc.',
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = '用量紀錄'
        verbose_name_plural = '用量紀錄'
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['organization', '-created_at']),
            models.Index(fields=['billing_period']),
        ]

    def __str__(self):
        return (
            f'{self.organization.code} {self.billing_mode} '
            f'{self.tokens_charged}t {self.solver_status} '
            f'@ {self.created_at:%Y-%m-%d %H:%M}'
        )


class PaymentMethod(models.Model):
    """
    Mock-only in Phase 2; Stripe integration lands in Phase 3.

    `external_token` will hold the Stripe `payment_method` id once
    integration arrives. `provider='mock'` rows are dummies that always
    succeed — they only exist so the cap-and-record flow can be exercised
    end-to-end without touching real money.
    """
    PROVIDER_CHOICES = [
        ('mock', 'Mock (Phase 2)'),
        ('stripe', 'Stripe (Phase 3)'),
    ]
    organization = models.OneToOneField(
        'organizations.Organization',
        on_delete=models.CASCADE,
        related_name='payment_method',
        verbose_name='所屬機構',
    )
    provider = models.CharField(
        max_length=20, choices=PROVIDER_CHOICES, default='mock',
        verbose_name='付款服務商',
    )
    external_token = models.CharField(
        max_length=255, blank=True, default='',
        verbose_name='外部 token (Stripe payment_method id)',
    )
    last_4 = models.CharField(
        max_length=4, blank=True, default='',
        verbose_name='卡號末四碼',
    )
    brand = models.CharField(
        max_length=20, blank=True, default='',
        verbose_name='卡片品牌',
    )
    is_active = models.BooleanField(default=True, verbose_name='啟用')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = '付款方式'
        verbose_name_plural = '付款方式'

    def __str__(self):
        return f'{self.organization.code} via {self.provider} (**** {self.last_4})'


# ---------------------------------------------------------------------------
# Pure helpers — used by ai_engine and tests. No business logic in views.
# ---------------------------------------------------------------------------

def estimate_tokens(billing_mode: str, request_metadata: dict = None) -> int:
    """
    Pure read of the current `BillingRateConfig` for a mode.

    Flat-fee per the customer decision ("後台寫定額"). `request_metadata`
    is accepted (unused now) so Phase 3 can introduce regression-safe
    per-call dynamic pricing without changing the call sites.
    """
    return BillingRateConfig.current_rate_for(billing_mode)


@transaction.atomic
def record_usage(
    organization,
    billing_mode: str,
    solver_status: str,
    user=None,
    schedule_version=None,
    request_metadata: dict = None,
) -> 'UsageRecord':
    """
    Create a UsageRecord and atomically bump the BillingPeriod total.

    Pre-debit: this is called regardless of solver_status. The caller
    decides whether to record at all (e.g. consume_token=False); once
    called the charge is final.
    """
    tokens = estimate_tokens(billing_mode, request_metadata or {})
    # Lock the period row so concurrent generators cannot blow past the
    # cap by racing each other.
    period = BillingPeriod.current_for(organization)
    period = (
        BillingPeriod.objects
        .select_for_update()
        .get(pk=period.pk)
    )
    period.total_tokens = period.total_tokens + tokens
    period.save(update_fields=['total_tokens', 'updated_at'])

    return UsageRecord.objects.create(
        organization=organization,
        billing_period=period,
        billing_mode=billing_mode,
        tokens_charged=tokens,
        solver_status=solver_status,
        schedule_version=schedule_version,
        user=user,
        request_metadata=request_metadata or {},
    )


def would_exceed_cap(organization, billing_mode: str) -> tuple:
    """
    Pre-flight check used by AI generate before invoking the solver.

    Returns `(would_exceed: bool, current_tokens: int, projected: int, cap)`.
    When `cap is None` the org is unlimited and the boolean is always False.
    """
    settings_row = (
        OrgBillingSettings.objects
        .filter(organization=organization)
        .first()
    )
    cap = settings_row.monthly_cap_tokens if settings_row else None
    period = BillingPeriod.current_for(organization)
    cost = estimate_tokens(billing_mode)
    projected = period.total_tokens + cost
    if cap is None:
        return False, period.total_tokens, projected, None
    return projected > cap, period.total_tokens, projected, cap
