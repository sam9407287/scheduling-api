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
    reason = models.TextField(blank=True, verbose_name='事由')
    status = models.CharField(
        max_length=10,
        choices=STATUS_CHOICES,
        default='pending',
        verbose_name='狀態'
    )
    # 主管代員工登記時 created_by != employee.user；代登記直接視同已核准
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

    def __str__(self):
        return f"{self.employee.employee_id} {self.get_leave_type_display()} {self.start_date}~{self.end_date} ({self.get_status_display()})"
