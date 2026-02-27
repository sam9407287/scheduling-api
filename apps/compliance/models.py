"""
Compliance models
"""
from django.db import models
from django.core.validators import MinValueValidator
from decimal import Decimal


class LaborLawRule(models.Model):
    """勞基法規則"""
    RULE_TYPE_CHOICES = [
        ('max_weekly_hours', '每週最大工時'),
        ('max_daily_hours', '每日最大工時'),
        ('min_rest_hours', '最小休息時數'),
        ('max_consecutive_days', '最大連續工作天數'),
        ('mandatory_rest_day', '強制休息日'),
        ('overtime_multiplier', '加班倍率'),
    ]

    name = models.CharField(max_length=200, verbose_name='規則名稱')
    rule_type = models.CharField(
        max_length=50,
        choices=RULE_TYPE_CHOICES,
        verbose_name='規則類型'
    )
    value = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        null=True,
        blank=True,
        verbose_name='數值'
    )
    description = models.TextField(blank=True, verbose_name='描述')
    is_active = models.BooleanField(default=True, verbose_name='啟用')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = '勞基法規則'
        verbose_name_plural = '勞基法規則'
        ordering = ['rule_type', 'name']

    def __str__(self):
        return f"{self.name} ({self.get_rule_type_display()})"


class ComplianceCheck(models.Model):
    """合規檢查紀錄"""
    STATUS_CHOICES = [
        ('pass', '通過'),
        ('warning', '警告'),
        ('violation', '違規'),
    ]

    organization = models.ForeignKey(
        'organizations.Organization',
        on_delete=models.CASCADE,
        related_name='compliance_checks',
        verbose_name='所屬機構'
    )
    check_type = models.CharField(
        max_length=50,
        choices=[
            ('schedule', '排班檢查'),
            ('attendance', '出勤檢查'),
            ('overtime', '加班檢查'),
            ('weekly', '週度檢查'),
            ('monthly', '月度檢查'),
        ],
        verbose_name='檢查類型'
    )
    check_period_start = models.DateField(verbose_name='檢查期間開始')
    check_period_end = models.DateField(verbose_name='檢查期間結束')
    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        verbose_name='檢查狀態'
    )
    violations = models.JSONField(default=list, verbose_name='違規項目')
    warnings = models.JSONField(default=list, verbose_name='警告項目')
    checked_by = models.ForeignKey(
        'accounts.User',
        on_delete=models.SET_NULL,
        null=True,
        related_name='compliance_checks',
        verbose_name='檢查人'
    )
    checked_at = models.DateTimeField(auto_now_add=True, verbose_name='檢查時間')
    notes = models.TextField(blank=True, verbose_name='備註')

    class Meta:
        verbose_name = '合規檢查'
        verbose_name_plural = '合規檢查'
        ordering = ['-checked_at']

    def __str__(self):
        return f"{self.organization.name} - {self.get_check_type_display()} ({self.get_status_display()})"
