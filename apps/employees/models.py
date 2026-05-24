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


class EmployeeTag(models.Model):
    """員工自訂標籤（組織內共用，例：can_lift_high、driver、bilingual）"""
    organization = models.ForeignKey(
        'organizations.Organization',
        on_delete=models.CASCADE,
        related_name='employee_tags',
        verbose_name='所屬機構',
    )
    code = models.CharField(max_length=64, verbose_name='標籤代碼')
    label = models.CharField(max_length=100, verbose_name='顯示名稱')
    description = models.TextField(blank=True, verbose_name='描述')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = '員工標籤'
        verbose_name_plural = '員工標籤'
        unique_together = [['organization', 'code']]
        ordering = ['organization', 'code']

    def __str__(self):
        return f"{self.code} ({self.label})"


class Employee(models.Model):
    """員工"""
    CONTRACT_TYPE_CHOICES = [
        ('full_time', '全職'),
        ('part_time', '兼職'),
        ('dispatch', '派遣'),
    ]
    GENDER_CHOICES = [
        ('male', '男'),
        ('female', '女'),
        ('other', '其他'),
        ('undisclosed', '不公開'),
    ]
    SHIFT_PATTERN_CHOICES = [
        ('none', '無偏好'),
        ('alternating', '花花班（早晚交錯）'),
        ('consecutive', '連上放長假'),
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
    tags = models.ManyToManyField(
        EmployeeTag,
        blank=True,
        related_name='employees',
        verbose_name='員工標籤',
    )
    # --- 個資欄位（需透過 EmployeeDataConsent 授權才能用於 OR-Tools 排班）---
    gender = models.CharField(
        max_length=20,
        choices=GENDER_CHOICES,
        blank=True,
        default='',
        verbose_name='性別',
    )
    birth_date = models.DateField(
        null=True,
        blank=True,
        verbose_name='生日',
        help_text='用於推算年齡；OR-Tools 需引用時改取派生年齡',
    )
    height_cm = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        null=True,
        blank=True,
        validators=[MinValueValidator(Decimal('50.00')), MaxValueValidator(Decimal('250.00'))],
        verbose_name='身高（公分）',
    )
    weight_kg = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        null=True,
        blank=True,
        validators=[MinValueValidator(Decimal('20.00')), MaxValueValidator(Decimal('400.00'))],
        verbose_name='體重（公斤）',
    )
    shift_pattern_preference = models.CharField(
        max_length=20,
        choices=SHIFT_PATTERN_CHOICES,
        default='none',
        verbose_name='排班模式偏好',
    )
    # --- 個資欄位結束 ---
    hire_date = models.DateField(verbose_name='到職日期')
    is_active = models.BooleanField(default=True, verbose_name='在職')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    # 列入「敏感」欄位：未取得 EmployeeDataConsent 時，OR-Tools 不可讀取
    SENSITIVE_FIELDS = ('gender', 'birth_date', 'height_cm', 'weight_kg')

    class Meta:
        verbose_name = '員工'
        verbose_name_plural = '員工'
        ordering = ['employee_id']

    def __str__(self):
        return f"{self.employee_id} - {self.user.get_full_name() or self.user.username}"

    def has_active_data_consent(self) -> bool:
        """員工是否有有效的個資使用授權（用於 OR-Tools 排班）。"""
        consent = getattr(self, 'data_consent', None)
        if consent is None:
            return False
        return consent.is_active()

    def sensitive_attributes_for_solver(self) -> dict:
        """
        提供給 OR-Tools 使用的敏感欄位 dict。
        未授權員工一律回傳 None，這是團隊規則 generator 的單一信任邊界。
        """
        if not self.has_active_data_consent():
            return {field: None for field in self.SENSITIVE_FIELDS}
        return {field: getattr(self, field) or None for field in self.SENSITIVE_FIELDS}


class EmployeeDataConsent(models.Model):
    """
    員工敏感資料使用同意紀錄（首次進入排班時彈窗確認）。

    Invariants:
      - 一名員工最多一筆紀錄（OneToOne）。
      - `revoked_at` 為 None 視為授權中。
      - `consent_version` 用於未來條款更新時重新請求。
    """
    employee = models.OneToOneField(
        Employee,
        on_delete=models.CASCADE,
        related_name='data_consent',
        verbose_name='員工',
    )
    consented_at = models.DateTimeField(verbose_name='同意時間')
    revoked_at = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name='撤回時間（None = 仍有效）',
    )
    consent_version = models.CharField(
        max_length=20,
        default='1.0',
        verbose_name='同意書版本',
    )
    notes = models.TextField(blank=True, verbose_name='備註')

    class Meta:
        verbose_name = '員工個資同意'
        verbose_name_plural = '員工個資同意'

    def __str__(self):
        status = '撤回' if self.revoked_at else '有效'
        return f"{self.employee.employee_id} consent v{self.consent_version} ({status})"

    def is_active(self) -> bool:
        return self.revoked_at is None


class EmployeeAvailability(models.Model):
    """
    員工排班可用性與偏好設定（每人一筆，可隨時更新）。

    前端可讓使用者：
    - 只填 required_hours_per_week（其餘留空 = 無限制）
    - 動態增加多筆 EmployeeTimeSlot（blocked / preferred）
    - 在 special_rules 文字區填自然語言特殊規則
    """
    employee = models.OneToOneField(
        Employee,
        on_delete=models.CASCADE,
        related_name='availability',
        verbose_name='員工',
    )
    required_hours_per_week = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        null=True,
        blank=True,
        verbose_name='每週所需工時（None = 沿用合約設定）',
    )
    special_rules = models.TextField(
        blank=True,
        verbose_name='特殊規則（自然語言）',
        help_text='例如：只能排上午班；週三不可排班；盡量安排連續班次',
    )
    effective_from = models.DateField(
        null=True,
        blank=True,
        verbose_name='生效起始日（None = 永久）',
    )
    effective_to = models.DateField(
        null=True,
        blank=True,
        verbose_name='生效結束日（None = 永久）',
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = '員工可用性設定'
        verbose_name_plural = '員工可用性設定'

    def __str__(self):
        return f"{self.employee.employee_id} availability"


class EmployeeTimeSlot(models.Model):
    """
    員工的單一時段設定（可新增多筆）。

    slot_type:
      - blocked   → 此時段不可排班（硬約束）
      - preferred → 此時段為優先/偏好（軟約束，違反會有懲罰分數）

    day_of_week = None 表示「每天都適用」
    """
    SLOT_TYPE_CHOICES = [
        ('blocked',   '不可排班'),
        ('preferred', '優先偏好'),
    ]
    DAY_CHOICES = [
        (0, '週一'), (1, '週二'), (2, '週三'),
        (3, '週四'), (4, '週五'), (5, '週六'), (6, '週日'),
    ]

    availability = models.ForeignKey(
        EmployeeAvailability,
        on_delete=models.CASCADE,
        related_name='time_slots',
        verbose_name='可用性設定',
    )
    slot_type = models.CharField(
        max_length=10,
        choices=SLOT_TYPE_CHOICES,
        verbose_name='類型',
    )
    day_of_week = models.IntegerField(
        choices=DAY_CHOICES,
        null=True,
        blank=True,
        verbose_name='星期幾（None = 每天）',
    )
    start_time = models.TimeField(verbose_name='開始時間')
    end_time = models.TimeField(verbose_name='結束時間')
    label = models.CharField(
        max_length=100,
        blank=True,
        verbose_name='備註標籤',
        help_text='例如：接送孩子、學校課程',
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = '員工時段設定'
        verbose_name_plural = '員工時段設定'
        ordering = ['day_of_week', 'start_time']

    def __str__(self):
        day = self.get_day_of_week_display() if self.day_of_week is not None else '每天'
        return f"{self.availability.employee.employee_id} {self.get_slot_type_display()} {day} {self.start_time}-{self.end_time}"


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
