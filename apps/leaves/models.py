"""
Leave management models.

Full-day leave requests with a single-layer approval flow. Approved leave:
  - marks the employee's Schedule rows in range as status='leave' (records
    are kept, never deleted — the roster shows the vacancy);
  - feeds the AI solver as hard unavailable dates;
  - for annual leave (特休), counts against the labour-law entitlement
    computed from seniority (see annual.py).
"""
from django.db import models


class LeaveRequest(models.Model):
    LEAVE_TYPE_CHOICES = [
        ('annual', '特休'),
        ('sick', '病假'),
        ('personal', '事假'),
        ('menstrual', '生理假'),
        ('marriage', '婚假'),
        ('bereavement', '喪假'),
        ('maternity', '產假'),
        ('paternity', '陪產假'),
        ('official', '公假'),
        ('other', '其他'),
    ]

    STATUS_CHOICES = [
        ('pending', '待審核'),
        ('approved', '已核准'),
        ('rejected', '已駁回'),
        ('cancelled', '已取消'),
    ]

    SUBMISSION_SOURCE_CHOICES = [
        ('self', '本人申請'),
        ('manager_proxy', '主管代登記'),
        ('system', '系統'),
    ]

    REQUEST_UNIT_CHOICES = [
        ('full_day', '全日'),
        ('time_range', '時段'),
    ]

    organization = models.ForeignKey(
        'organizations.Organization',
        on_delete=models.CASCADE,
        related_name='leave_requests',
        verbose_name='所屬機構'
    )
    employee = models.ForeignKey(
        'employees.Employee',
        on_delete=models.CASCADE,
        related_name='leave_requests',
        verbose_name='員工'
    )
    leave_type = models.CharField(
        max_length=20,
        choices=LEAVE_TYPE_CHOICES,
        verbose_name='假別'
    )
    start_date = models.DateField(verbose_name='開始日期')
    end_date = models.DateField(verbose_name='結束日期')
    # 小時制（簡化版）：time_range 限單日、同日 start<end、不跨午夜
    request_unit = models.CharField(
        max_length=10,
        choices=REQUEST_UNIT_CHOICES,
        default='full_day',
        verbose_name='請假單位'
    )
    start_time = models.TimeField(null=True, blank=True, verbose_name='開始時間')
    end_time = models.TimeField(null=True, blank=True, verbose_name='結束時間')
    reason = models.TextField(blank=True, verbose_name='事由')
    status = models.CharField(
        max_length=10,
        choices=STATUS_CHOICES,
        default='pending',
        verbose_name='狀態'
    )
    # 送件來源由後端保存為唯一可信值（LEAVE_V2 P0）：
    # self=本人（含主管幫自己請假，走 pending）；manager_proxy=主管代登記（自動核准）
    submission_source = models.CharField(
        max_length=20,
        choices=SUBMISSION_SOURCE_CHOICES,
        default='self',
        verbose_name='送件來源'
    )
    created_by = models.ForeignKey(
        'accounts.User',
        on_delete=models.SET_NULL,
        null=True,
        related_name='created_leave_requests',
        verbose_name='申請人'
    )
    reviewed_by = models.ForeignKey(
        'accounts.User',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='reviewed_leave_requests',
        verbose_name='審核人'
    )
    reviewed_at = models.DateTimeField(null=True, blank=True, verbose_name='審核時間')
    review_note = models.TextField(blank=True, verbose_name='審核備註')
    # 核准當下被標記為 leave 的班次 id 快照，取消核准時據此還原
    affected_schedule_ids = models.JSONField(default=list, blank=True, verbose_name='受影響班次')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = '請假申請'
        verbose_name_plural = '請假申請'
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['organization', 'status']),
            models.Index(fields=['employee', 'start_date', 'end_date']),
        ]

    @property
    def total_days(self) -> int:
        """全日假：起訖含頭尾的日曆天數。"""
        return (self.end_date - self.start_date).days + 1

    def duration_minutes(self, day_minutes: int) -> int:
        """扣額度用的分鐘數（動態計算不落庫）。

        time_range＝時段長；full_day＝天數 × 機構的 day_minutes。
        """
        if self.request_unit == 'time_range' and self.start_time and self.end_time:
            return (
                (self.end_time.hour * 60 + self.end_time.minute)
                - (self.start_time.hour * 60 + self.start_time.minute)
            )
        return self.total_days * day_minutes

    def __str__(self):
        return f"{self.employee.employee_id} {self.get_leave_type_display()} {self.start_date}~{self.end_date} ({self.get_status_display()})"


class OrgLeaveSettings(models.Model):
    """機構層級請假設定。

    day_minutes：小時制請假換算「一天」的分鐘數（預設 8h=480，機構可調）。
    改動會即時重算歷史用量（餘額為動態計算不落庫）。
    """
    organization = models.OneToOneField(
        'organizations.Organization',
        on_delete=models.CASCADE,
        related_name='leave_settings',
        verbose_name='所屬機構'
    )
    day_minutes = models.PositiveIntegerField(default=480, verbose_name='一天等於幾分鐘')
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = '請假設定'
        verbose_name_plural = '請假設定'

    def __str__(self):
        return f"{self.organization_id}: 1 day = {self.day_minutes} min"


class LeaveTypeQuota(models.Model):
    """機構可設定的假別年度額度（分鐘）。

    annual_quota_minutes=null 代表不限。特休（annual）不吃這張表——
    永遠依勞基法年資級距（annual.py）。超額一律警告不擋。
    """
    organization = models.ForeignKey(
        'organizations.Organization',
        on_delete=models.CASCADE,
        related_name='leave_type_quotas',
        verbose_name='所屬機構'
    )
    leave_type = models.CharField(
        max_length=20,
        choices=LeaveRequest.LEAVE_TYPE_CHOICES,
        verbose_name='假別'
    )
    annual_quota_minutes = models.PositiveIntegerField(
        null=True, blank=True, verbose_name='年度額度（分鐘，null=不限）'
    )
    is_active = models.BooleanField(default=True, verbose_name='啟用')
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = '假別額度'
        verbose_name_plural = '假別額度'
        unique_together = [['organization', 'leave_type']]

    def __str__(self):
        return f"{self.organization_id} {self.leave_type}: {self.annual_quota_minutes or '∞'}"
