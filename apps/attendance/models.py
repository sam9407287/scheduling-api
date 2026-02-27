"""
Attendance models
"""
from django.db import models
from django.core.validators import MinValueValidator
from decimal import Decimal


class Attendance(models.Model):
    """出勤紀錄"""
    employee = models.ForeignKey(
        'employees.Employee',
        on_delete=models.CASCADE,
        related_name='attendances',
        verbose_name='員工'
    )
    work_date = models.DateField(verbose_name='工作日期')
    clock_in = models.DateTimeField(null=True, blank=True, verbose_name='上班打卡時間')
    clock_out = models.DateTimeField(null=True, blank=True, verbose_name='下班打卡時間')
    actual_hours = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        null=True,
        blank=True,
        validators=[MinValueValidator(Decimal('0'))],
        verbose_name='實際工時'
    )
    is_substitute = models.BooleanField(default=False, verbose_name='是否代班')
    substitute_for = models.ForeignKey(
        'employees.Employee',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='substitute_attendances',
        verbose_name='代班對象'
    )
    is_cross_branch = models.BooleanField(default=False, verbose_name='是否跨店支援')
    cross_branch = models.ForeignKey(
        'organizations.Branch',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='cross_branch_attendances',
        verbose_name='支援分店'
    )
    anomaly_flag = models.BooleanField(default=False, verbose_name='異常標記')
    anomaly_reason = models.TextField(blank=True, verbose_name='異常原因')
    notes = models.TextField(blank=True, verbose_name='備註')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = '出勤紀錄'
        verbose_name_plural = '出勤紀錄'
        unique_together = [['employee', 'work_date']]
        ordering = ['-work_date', 'employee']
        indexes = [
            models.Index(fields=['work_date', 'employee']),
            models.Index(fields=['work_date', 'anomaly_flag']),
        ]

    def __str__(self):
        return f"{self.employee.employee_id} - {self.work_date}"

    def calculate_hours(self):
        """計算實際工時"""
        if self.clock_in and self.clock_out:
            delta = self.clock_out - self.clock_in
            hours = delta.total_seconds() / 3600
            self.actual_hours = Decimal(str(round(hours, 2)))
            self.save(update_fields=['actual_hours'])
        return self.actual_hours


class AnomalyRecord(models.Model):
    """異常紀錄"""
    ANOMALY_TYPE_CHOICES = [
        ('late', '遲到'),
        ('early_leave', '早退'),
        ('no_clock_in', '未打卡上班'),
        ('no_clock_out', '未打卡下班'),
        ('overtime', '超時工作'),
        ('mismatch', '與排班不符'),
    ]

    attendance = models.ForeignKey(
        Attendance,
        on_delete=models.CASCADE,
        related_name='anomalies',
        verbose_name='出勤紀錄'
    )
    anomaly_type = models.CharField(
        max_length=20,
        choices=ANOMALY_TYPE_CHOICES,
        verbose_name='異常類型'
    )
    description = models.TextField(verbose_name='異常描述')
    severity = models.CharField(
        max_length=20,
        choices=[
            ('low', '低'),
            ('medium', '中'),
            ('high', '高'),
        ],
        default='medium',
        verbose_name='嚴重程度'
    )
    resolved = models.BooleanField(default=False, verbose_name='已處理')
    resolved_by = models.ForeignKey(
        'accounts.User',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='resolved_anomalies',
        verbose_name='處理人'
    )
    resolved_at = models.DateTimeField(null=True, blank=True, verbose_name='處理時間')
    resolution_notes = models.TextField(blank=True, verbose_name='處理備註')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = '異常紀錄'
        verbose_name_plural = '異常紀錄'
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.attendance} - {self.get_anomaly_type_display()}"
