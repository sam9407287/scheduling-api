"""
Shift Template and Rules models
"""
from django.db import models
from django.core.validators import MinValueValidator, MaxValueValidator


class ShiftTemplate(models.Model):
    """班別模板"""
    organization = models.ForeignKey(
        'organizations.Organization',
        on_delete=models.CASCADE,
        related_name='shift_templates',
        verbose_name='所屬機構'
    )
    name = models.CharField(max_length=100, verbose_name='班別名稱')
    start_time = models.TimeField(verbose_name='開始時間')
    end_time = models.TimeField(verbose_name='結束時間')
    break_minutes = models.IntegerField(
        default=0,
        validators=[MinValueValidator(0)],
        verbose_name='休息分鐘數'
    )
    overlap_minutes = models.IntegerField(
        default=30,
        validators=[MinValueValidator(0)],
        verbose_name='交接重疊分鐘數'
    )
    min_staff_count = models.IntegerField(
        default=1,
        validators=[MinValueValidator(1)],
        verbose_name='最低人力配置'
    )
    required_certifications = models.ManyToManyField(
        'employees.Certification',
        blank=True,
        related_name='shift_templates',
        verbose_name='所需證照'
    )
    is_active = models.BooleanField(default=True, verbose_name='啟用')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = '班別模板'
        verbose_name_plural = '班別模板'
        ordering = ['organization', 'start_time']

    def __str__(self):
        return f"{self.name} ({self.start_time} - {self.end_time})"

    @property
    def duration_hours(self):
        """計算班別時數"""
        from datetime import datetime, timedelta
        start = datetime.combine(datetime.today(), self.start_time)
        end = datetime.combine(datetime.today(), self.end_time)
        if end < start:
            end += timedelta(days=1)
        duration = (end - start).total_seconds() / 3600
        return duration - (self.break_minutes / 60)


class ShiftRule(models.Model):
    """排班規則"""
    organization = models.ForeignKey(
        'organizations.Organization',
        on_delete=models.CASCADE,
        related_name='shift_rules',
        verbose_name='所屬機構'
    )
    name = models.CharField(max_length=100, verbose_name='規則名稱')
    rule_type = models.CharField(
        max_length=50,
        choices=[
            ('max_consecutive_days', '最大連續工作天數'),
            ('min_rest_hours', '最小休息時數'),
            ('max_weekly_hours', '最大每週工時'),
            ('mandatory_rest_day', '強制休息日'),
        ],
        verbose_name='規則類型'
    )
    value = models.JSONField(verbose_name='規則值')
    is_active = models.BooleanField(default=True, verbose_name='啟用')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = '排班規則'
        verbose_name_plural = '排班規則'
        ordering = ['organization', 'name']

    def __str__(self):
        return f"{self.name} ({self.get_rule_type_display()})"
