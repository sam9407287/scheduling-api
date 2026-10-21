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
        ordering = ['name']

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


class EmailVerificationCode(models.Model):
    """
    Email 驗證碼（自助註冊，2026-10-22）。

    流程：POST register/send-code/ 寄 6 位數驗證碼到任何信箱（不限 Gmail）
    → POST register/ 帶 email + code + password 建帳號。驗證碼只存 hash，
    10 分鐘到期、最多驗 5 次；同一 email 60 秒內不得重寄、每小時上限
    5 封，同一 IP 每小時 20 封——防止瘋狂洗帳號。一個 email 只能有一個帳號。
    """
    PURPOSE_CHOICES = [
        ('register', '註冊'),
    ]

    email = models.EmailField(db_index=True, verbose_name='Email')
    purpose = models.CharField(
        max_length=20, choices=PURPOSE_CHOICES, default='register', verbose_name='用途',
    )
    code_hash = models.CharField(max_length=64, verbose_name='驗證碼雜湊')
    attempts = models.PositiveSmallIntegerField(default=0, verbose_name='已嘗試次數')
    ip_address = models.GenericIPAddressField(null=True, blank=True, verbose_name='請求 IP')
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    expires_at = models.DateTimeField(verbose_name='到期時間')
    consumed_at = models.DateTimeField(null=True, blank=True, verbose_name='使用時間')

    class Meta:
        verbose_name = 'Email 驗證碼'
        verbose_name_plural = 'Email 驗證碼'
        ordering = ['-created_at']

    def __str__(self):
        return f'{self.email} ({self.purpose}) @ {self.created_at:%Y-%m-%d %H:%M}'
