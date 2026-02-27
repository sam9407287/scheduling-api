"""
Organization and Branch models
"""
from django.db import models
from django.core.validators import MinLengthValidator


class Organization(models.Model):
    """機構/公司"""
    name = models.CharField(max_length=200, verbose_name='機構名稱')
    code = models.CharField(
        max_length=50,
        unique=True,
        validators=[MinLengthValidator(2)],
        verbose_name='機構代碼'
    )
    address = models.TextField(blank=True, verbose_name='地址')
    phone = models.CharField(max_length=20, blank=True, verbose_name='電話')
    email = models.EmailField(blank=True, verbose_name='Email')
    is_active = models.BooleanField(default=True, verbose_name='啟用')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = '機構'
        verbose_name_plural = '機構'
        ordering = ['name']

    def __str__(self):
        return self.name


class Branch(models.Model):
    """分店/據點"""
    organization = models.ForeignKey(
        Organization,
        on_delete=models.CASCADE,
        related_name='branches',
        verbose_name='所屬機構'
    )
    name = models.CharField(max_length=200, verbose_name='分店名稱')
    code = models.CharField(
        max_length=50,
        validators=[MinLengthValidator(2)],
        verbose_name='分店代碼'
    )
    address = models.TextField(blank=True, verbose_name='地址')
    phone = models.CharField(max_length=20, blank=True, verbose_name='電話')
    is_active = models.BooleanField(default=True, verbose_name='啟用')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = '分店'
        verbose_name_plural = '分店'
        unique_together = [['organization', 'code']]
        ordering = ['organization', 'name']

    def __str__(self):
        return f"{self.organization.name} - {self.name}"
