"""
Employee, Contract, and Certification models
"""
from django.db import models
from django.core.validators import MinValueValidator, MaxValueValidator
from decimal import Decimal


class Certification(models.Model):
    """證照/資格"""
    name = models.CharField(max_length=200, unique=True, verbose_name='證照名稱')
    code = models.CharField(max_length=50, unique=True, verbose_name='證照代碼')
    description = models.TextField(blank=True, verbose_name='描述')
    is_required = models.BooleanField(default=False, verbose_name='是否必需')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = '證照'
        verbose_name_plural = '證照'
        ordering = ['name']

    def __str__(self):
        return self.name


class Employee(models.Model):
    """員工"""
    CONTRACT_TYPE_CHOICES = [
        ('full_time', '全職'),
        ('part_time', '兼職'),
        ('dispatch', '派遣'),
    ]

    user = models.OneToOneField(
        'accounts.User',
        on_delete=models.CASCADE,
        related_name='employee_profile',
        verbose_name='使用者帳號'
    )
    employee_id = models.CharField(
        max_length=50,
        unique=True,
        verbose_name='員工編號'
    )
    organization = models.ForeignKey(
        'organizations.Organization',
        on_delete=models.CASCADE,
        related_name='employees',
        verbose_name='所屬機構'
    )
    branch = models.ForeignKey(
        'organizations.Branch',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='employees',
        verbose_name='所屬分店'
    )
    position = models.CharField(max_length=100, verbose_name='職位')
    contract_type = models.CharField(
        max_length=20,
        choices=CONTRACT_TYPE_CHOICES,
        default='full_time',
        verbose_name='契約類型'
    )
    agreed_hours_per_week = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        default=Decimal('40.00'),
        validators=[MinValueValidator(Decimal('0.01')), MaxValueValidator(Decimal('168.00'))],
        verbose_name='約定每週工時'
    )
    certifications = models.ManyToManyField(
        Certification,
        blank=True,
        related_name='employees',
        verbose_name='持有證照'
    )
    hire_date = models.DateField(verbose_name='到職日期')
    is_active = models.BooleanField(default=True, verbose_name='在職')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = '員工'
        verbose_name_plural = '員工'
        ordering = ['employee_id']

    def __str__(self):
        return f"{self.employee_id} - {self.user.get_full_name() or self.user.username}"


class Contract(models.Model):
    """勞動契約"""
    CONTRACT_TYPE_CHOICES = [
        ('full_time', '全職'),
        ('part_time', '兼職'),
        ('dispatch', '派遣'),
    ]

    employee = models.ForeignKey(
        Employee,
        on_delete=models.CASCADE,
        related_name='contracts',
        verbose_name='員工'
    )
    contract_type = models.CharField(
        max_length=20,
        choices=CONTRACT_TYPE_CHOICES,
        verbose_name='契約類型'
    )
    start_date = models.DateField(verbose_name='開始日期')
    end_date = models.DateField(null=True, blank=True, verbose_name='結束日期')
    base_salary = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        null=True,
        blank=True,
        verbose_name='基本薪資'
    )
    agreed_hours_per_week = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        default=Decimal('40.00'),
        verbose_name='約定每週工時'
    )
    notes = models.TextField(blank=True, verbose_name='備註')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = '勞動契約'
        verbose_name_plural = '勞動契約'
        ordering = ['-start_date']

    def __str__(self):
        return f"{self.employee.employee_id} - {self.get_contract_type_display()} ({self.start_date})"
