"""
Overtime models
"""
from django.db import models
from django.core.validators import MinValueValidator
from decimal import Decimal


class OvertimeRecord(models.Model):
    """加班紀錄"""
    OVERTIME_TYPE_CHOICES = [
        ('regular', '平日延長工時'),
        ('rest_day', '休息日'),
        ('holiday', '國定假日'),
        ('special_holiday', '特別休假'),
    ]

    employee = models.ForeignKey(
        'employees.Employee',
        on_delete=models.CASCADE,
        related_name='overtime_records',
        verbose_name='員工'
    )
    attendance = models.ForeignKey(
        'attendance.Attendance',
        on_delete=models.CASCADE,
        related_name='overtime_records',
        verbose_name='出勤紀錄'
    )
    overtime_date = models.DateField(verbose_name='加班日期')
    overtime_type = models.CharField(
        max_length=20,
        choices=OVERTIME_TYPE_CHOICES,
        verbose_name='加班類型'
    )
    hours = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        validators=[MinValueValidator(Decimal('0.01'))],
        verbose_name='加班時數'
    )
    multiplier = models.DecimalField(
        max_digits=4,
        decimal_places=2,
        default=Decimal('1.34'),
        verbose_name='倍率'
    )
    calculated_amount = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        null=True,
        blank=True,
        verbose_name='計算金額'
    )
    notes = models.TextField(blank=True, verbose_name='備註')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = '加班紀錄'
        verbose_name_plural = '加班紀錄'
        ordering = ['-overtime_date', 'employee']
        indexes = [
            models.Index(fields=['overtime_date', 'employee']),
        ]

    def __str__(self):
        return f"{self.employee.employee_id} - {self.overtime_date} - {self.hours}小時"


class OvertimeRule(models.Model):
    """加班規則（勞基法規定）"""
    organization = models.ForeignKey(
        'organizations.Organization',
        on_delete=models.CASCADE,
        related_name='overtime_rules',
        verbose_name='所屬機構'
    )
    overtime_type = models.CharField(
        max_length=20,
        choices=OvertimeRecord.OVERTIME_TYPE_CHOICES,
        verbose_name='加班類型'
    )
    multiplier = models.DecimalField(
        max_digits=4,
        decimal_places=2,
        default=Decimal('1.34'),
        verbose_name='倍率'
    )
    max_hours_per_day = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        null=True,
        blank=True,
        verbose_name='每日最大時數'
    )
    max_hours_per_month = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        null=True,
        blank=True,
        verbose_name='每月最大時數'
    )
    is_active = models.BooleanField(default=True, verbose_name='啟用')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = '加班規則'
        verbose_name_plural = '加班規則'
        unique_together = [['organization', 'overtime_type']]
        ordering = ['organization', 'overtime_type']

    def __str__(self):
        return f"{self.organization.name} - {self.get_overtime_type_display()} ({self.multiplier}x)"
