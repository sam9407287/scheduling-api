"""
User and Role models
"""
from django.db import models
from django.contrib.auth.models import AbstractUser


class Role(models.Model):
    """角色"""
    ROLE_CHOICES = [
        ('admin', '系統管理員'),
        ('manager', '管理者'),
        ('supervisor', '主管'),
        ('employee', '員工'),
    ]
    
    name = models.CharField(max_length=50, choices=ROLE_CHOICES, unique=True, verbose_name='角色名稱')
    description = models.TextField(blank=True, verbose_name='描述')
    permissions = models.JSONField(default=dict, verbose_name='權限設定')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = '角色'
        verbose_name_plural = '角色'

    def __str__(self):
        return self.get_name_display()


class User(AbstractUser):
    """使用者（擴展 Django User）"""
    firebase_uid = models.CharField(
        max_length=128,
        unique=True,
        null=True,
        blank=True,
        verbose_name='Firebase UID'
    )
    role = models.ForeignKey(
        Role,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='users',
        verbose_name='角色'
    )
    organization = models.ForeignKey(
        'organizations.Organization',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='users',
        verbose_name='所屬機構'
    )
    branch = models.ForeignKey(
        'organizations.Branch',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='users',
        verbose_name='所屬分店'
    )
    phone = models.CharField(max_length=20, blank=True, verbose_name='電話')
    is_active = models.BooleanField(default=True, verbose_name='啟用')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = '使用者'
        verbose_name_plural = '使用者'
        ordering = ['username']

    def __str__(self):
        return self.username
