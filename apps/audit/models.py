"""
Audit Log models
"""
from django.db import models
from django.contrib.contenttypes.models import ContentType
from django.contrib.contenttypes.fields import GenericForeignKey


class AuditLog(models.Model):
    """稽核日誌"""
    ACTION_CHOICES = [
        ('create', '建立'),
        ('update', '更新'),
        ('delete', '刪除'),
        ('approve', '簽核'),
        ('reject', '拒絕'),
        ('publish', '發布'),
        ('cancel', '取消'),
    ]

    user = models.ForeignKey(
        'accounts.User',
        on_delete=models.SET_NULL,
        null=True,
        related_name='audit_logs',
        verbose_name='操作人'
    )
    action = models.CharField(
        max_length=20,
        choices=ACTION_CHOICES,
        verbose_name='操作類型'
    )
    model_name = models.CharField(max_length=100, verbose_name='模型名稱')
    record_id = models.PositiveIntegerField(verbose_name='記錄ID')
    
    # Generic foreign key to any model
    content_type = models.ForeignKey(
        ContentType,
        on_delete=models.SET_NULL,
        null=True,
        verbose_name='內容類型'
    )
    object_id = models.PositiveIntegerField(null=True)
    content_object = GenericForeignKey('content_type', 'object_id')
    
    old_data = models.JSONField(null=True, blank=True, verbose_name='舊資料')
    new_data = models.JSONField(null=True, blank=True, verbose_name='新資料')
    changes = models.JSONField(null=True, blank=True, verbose_name='變更內容')
    ip_address = models.GenericIPAddressField(null=True, blank=True, verbose_name='IP 位址')
    user_agent = models.TextField(blank=True, verbose_name='User Agent')
    timestamp = models.DateTimeField(auto_now_add=True, verbose_name='時間戳記')

    class Meta:
        verbose_name = '稽核日誌'
        verbose_name_plural = '稽核日誌'
        ordering = ['-timestamp']
        indexes = [
            models.Index(fields=['model_name', 'record_id']),
            models.Index(fields=['user', 'timestamp']),
            models.Index(fields=['action', 'timestamp']),
        ]

    def __str__(self):
        return f"{self.get_action_display()} - {self.model_name}#{self.record_id} by {self.user} at {self.timestamp}"
