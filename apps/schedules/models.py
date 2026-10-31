"""
Schedule models - Dual track (Legal vs Actual)
"""
from django.db import models
from django.core.validators import MinValueValidator
from decimal import Decimal


class ScheduleVersion(models.Model):
    """排班版本（法規版/實際版）"""
    VERSION_TYPE_CHOICES = [
        ('legal', '法規版'),
        ('actual', '實際版'),
    ]
    
    STATUS_CHOICES = [
        ('draft', '草稿'),
        ('published', '已發布'),
        ('approved', '已簽核'),
        ('archived', '已歸檔'),
    ]

    organization = models.ForeignKey(
        'organizations.Organization',
        on_delete=models.CASCADE,
        related_name='schedule_versions',
        verbose_name='所屬機構'
    )
    branch = models.ForeignKey(
        'organizations.Branch',
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name='schedule_versions',
        verbose_name='所屬分店'
    )
    version_label = models.CharField(max_length=100, verbose_name='版本標籤')
    version_type = models.CharField(
        max_length=20,
        choices=VERSION_TYPE_CHOICES,
        verbose_name='版本類型'
    )
    period_start = models.DateField(verbose_name='期間開始')
    period_end = models.DateField(verbose_name='期間結束')
    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default='draft',
        verbose_name='狀態'
    )
    approved_by = models.ForeignKey(
        'accounts.User',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='approved_schedules',
        verbose_name='簽核人'
    )
    approved_at = models.DateTimeField(null=True, blank=True, verbose_name='簽核時間')
    created_by = models.ForeignKey(
        'accounts.User',
        on_delete=models.SET_NULL,
        null=True,
        related_name='created_schedules',
        verbose_name='建立人'
    )
    derived_from = models.ForeignKey(
        'self',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='derived_versions',
        verbose_name='派生來源版本',
        help_text='A 班表（legal）通常派生自 B 班表（actual）；保留歷史可重新派生'
    )
    # 2026-10-31 每日班別計畫的「星期範本」：{"0": [{shift_template, min_staff, max_staff}], …}
    # 0=週一…6=週日。apply-template 存入；期間自動延長時用它補新長出來的日子。
    shift_plan_template = models.JSONField(
        default=dict, blank=True, verbose_name='每日班別計畫星期範本',
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = '排班版本'
        verbose_name_plural = '排班版本'
        ordering = ['-period_start', '-created_at']

    def __str__(self):
        return f"{self.version_label} ({self.get_version_type_display()}) - {self.period_start} ~ {self.period_end}"


class Schedule(models.Model):
    """排班表"""
    STATUS_CHOICES = [
        ('draft', '草稿'),
        ('assigned', '已指派'),
        ('confirmed', '已確認'),
        ('completed', '已完成'),
        ('cancelled', '已取消'),
        ('leave', '請假'),
    ]

    schedule_version = models.ForeignKey(
        ScheduleVersion,
        on_delete=models.CASCADE,
        related_name='schedules',
        verbose_name='排班版本'
    )
    employee = models.ForeignKey(
        'employees.Employee',
        on_delete=models.CASCADE,
        related_name='schedules',
        verbose_name='員工'
    )
    shift_template = models.ForeignKey(
        'shifts.ShiftTemplate',
        on_delete=models.CASCADE,
        related_name='schedules',
        verbose_name='班別'
    )
    schedule_date = models.DateField(verbose_name='排班日期')
    expected_hours = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        validators=[MinValueValidator(Decimal('0'))],
        verbose_name='預計工時'
    )
    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default='draft',
        verbose_name='狀態'
    )
    notes = models.TextField(blank=True, verbose_name='備註')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = '排班'
        verbose_name_plural = '排班'
        unique_together = [
            ['schedule_version', 'employee', 'schedule_date', 'shift_template']
        ]
        # 'id' is a deterministic tie-breaker: without it, rows sharing the
        # same (schedule_date, shift_template start_time) have a non-unique
        # order, so PageNumberPagination can drop/duplicate rows across page
        # boundaries. See docs BACKEND_HANDOFF (frontend drag feature).
        ordering = ['schedule_date', 'shift_template__start_time', 'id']
        indexes = [
            models.Index(fields=['schedule_date', 'employee']),
            models.Index(fields=['schedule_version', 'schedule_date']),
        ]

    def __str__(self):
        return f"{self.employee.employee_id} - {self.schedule_date} - {self.shift_template.name}"


class ScheduleChange(models.Model):
    """排班異動紀錄"""
    CHANGE_TYPE_CHOICES = [
        ('substitute', '臨時代班'),
        ('split', '拆班'),
        ('transfer', '跨店支援'),
        ('cancel', '取消'),
        ('modify', '修改'),
    ]

    schedule = models.ForeignKey(
        Schedule,
        on_delete=models.CASCADE,
        related_name='changes',
        verbose_name='原排班'
    )
    change_type = models.CharField(
        max_length=20,
        choices=CHANGE_TYPE_CHOICES,
        verbose_name='異動類型'
    )
    original_employee = models.ForeignKey(
        'employees.Employee',
        on_delete=models.SET_NULL,
        null=True,
        related_name='original_schedule_changes',
        verbose_name='原員工'
    )
    replacement_employee = models.ForeignKey(
        'employees.Employee',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='replacement_schedule_changes',
        verbose_name='代班員工'
    )
    reason = models.TextField(verbose_name='異動原因')
    changed_by = models.ForeignKey(
        'accounts.User',
        on_delete=models.SET_NULL,
        null=True,
        related_name='schedule_changes',
        verbose_name='異動人'
    )
    changed_at = models.DateTimeField(auto_now_add=True, verbose_name='異動時間')
    approved_by = models.ForeignKey(
        'accounts.User',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='approved_changes',
        verbose_name='核准人'
    )
    approved_at = models.DateTimeField(null=True, blank=True, verbose_name='核准時間')

    class Meta:
        verbose_name = '排班異動'
        verbose_name_plural = '排班異動'
        ordering = ['-changed_at']

    def __str__(self):
        return f"{self.get_change_type_display()} - {self.schedule} ({self.changed_at})"


class ScheduleOverlapDecision(models.Model):
    """簽核總表的跨版本重疊裁決。

    衝突群組 = 同員工、同軌、不同已簽核版本、實際時間相交的班次集合。
    conflict_key 由群組班次的 id + updated_at 產生：任何班次異動都會改變
    key，舊裁決自然失效不會誤套。同一 key 重複送出會更新原裁決。
    """
    DECISION_CHOICES = [
        ('select', '保留指定班次'),
        ('coexist', '允許並存'),
    ]

    organization = models.ForeignKey(
        'organizations.Organization',
        on_delete=models.CASCADE,
        related_name='overlap_decisions',
        verbose_name='所屬機構'
    )
    branch = models.ForeignKey(
        'organizations.Branch',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='overlap_decisions',
        verbose_name='員工分店'
    )
    version_type = models.CharField(
        max_length=10,
        choices=ScheduleVersion.VERSION_TYPE_CHOICES,
        verbose_name='版本類型'
    )
    employee = models.ForeignKey(
        'employees.Employee',
        on_delete=models.CASCADE,
        related_name='overlap_decisions',
        verbose_name='員工'
    )
    schedule_date = models.DateField(verbose_name='群組起始日期')
    conflict_key = models.CharField(max_length=64, db_index=True, verbose_name='衝突鍵')
    schedule_ids = models.JSONField(verbose_name='群組班次 ID')
    decision = models.CharField(
        max_length=10,
        choices=DECISION_CHOICES,
        verbose_name='裁決'
    )
    selected_schedule_ids = models.JSONField(verbose_name='保留班次 ID')
    comment = models.TextField(blank=True, verbose_name='備註')
    decided_by = models.ForeignKey(
        'accounts.User',
        on_delete=models.SET_NULL,
        null=True,
        related_name='overlap_decisions',
        verbose_name='裁決人'
    )
    decided_at = models.DateTimeField(auto_now=True, verbose_name='裁決時間')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = '重疊裁決'
        verbose_name_plural = '重疊裁決'
        unique_together = [['organization', 'conflict_key']]
        ordering = ['-decided_at']

    def __str__(self):
        return f"{self.conflict_key[:8]} {self.get_decision_display()}"


class ScheduleVersionShiftPlan(models.Model):
    """
    每張班表自己的每日班別計畫（2026-10-31，前端 Frank 提案）。

    一列 = 「這張班表、這一天、這個班別、需要幾人（可選上限）」。
    這是 AI 排班、合規 coverage 唯一的人數來源：
      * 某天沒有任何列 ⇒ 那天不開班、AI 不排、coverage 不檢查
      * 某天只列早班＋晚班 ⇒ 那天只有這兩個班
      * ShiftTemplate 不再帶人數（min_staff_count 欄位保留但 API 不再使用）
      * 機構層級 StaffingRequirement 由本表取代（只剩回填舊版本時用過一次）
    版本沒有任何計畫列時，AI 排班回 400 `shift_plan_required`，不會默默排出空表。
    """
    schedule_version = models.ForeignKey(
        ScheduleVersion,
        on_delete=models.CASCADE,
        related_name='shift_plan',
        verbose_name='排班版本',
    )
    date = models.DateField(verbose_name='日期')
    shift_template = models.ForeignKey(
        'shifts.ShiftTemplate',
        on_delete=models.CASCADE,
        related_name='version_plans',
        verbose_name='班別',
    )
    min_staff = models.PositiveIntegerField(default=1, verbose_name='需要人數（最少）')
    max_staff = models.PositiveIntegerField(
        null=True, blank=True, verbose_name='人數上限（null = 不限）',
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = '每日班別計畫'
        verbose_name_plural = '每日班別計畫'
        unique_together = [['schedule_version', 'date', 'shift_template']]
        ordering = ['date', 'shift_template__start_time', 'shift_template_id']

    def __str__(self):
        cap = f'–{self.max_staff}' if self.max_staff is not None else ''
        return f'{self.schedule_version_id} {self.date} {self.shift_template_id} {self.min_staff}{cap} 人'
