"""
Shift Template and Rules models
"""
from django.db import models
from django.core.validators import MinValueValidator, MaxValueValidator
from decimal import Decimal


class ShiftTemplate(models.Model):
    """班別模板"""
    organization = models.ForeignKey(
        'organizations.Organization',
        on_delete=models.CASCADE,
        related_name='shift_templates',
        verbose_name='所屬機構'
    )
    name = models.CharField(max_length=100, verbose_name='班別名稱')
    start_time = models.TimeField(verbose_name='開始時間')
    end_time = models.TimeField(verbose_name='結束時間')
    break_minutes = models.PositiveIntegerField(
        default=0,
        verbose_name='休息分鐘數'
    )
    overlap_minutes = models.PositiveIntegerField(
        default=30,
        verbose_name='交接重疊分鐘數'
    )
    min_staff_count = models.IntegerField(
        default=1,
        validators=[MinValueValidator(1)],
        verbose_name='最低人力配置'
    )
    required_certifications = models.ManyToManyField(
        'employees.Certification',
        blank=True,
        related_name='shift_templates',
        verbose_name='所需證照'
    )
    is_active = models.BooleanField(default=True, verbose_name='啟用')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = '班別模板'
        verbose_name_plural = '班別模板'
        ordering = ['organization', 'start_time']

    def __str__(self):
        return f"{self.name} ({self.start_time} - {self.end_time})"

    @property
    def duration_hours(self):
        """計算班別時數"""
        from datetime import datetime, timedelta
        start = datetime.combine(datetime.today(), self.start_time)
        end = datetime.combine(datetime.today(), self.end_time)
        if end < start:
            end += timedelta(days=1)
        duration = (end - start).total_seconds() / 3600
        return duration - (self.break_minutes / 60)


class ShiftRule(models.Model):
    """排班規則"""
    organization = models.ForeignKey(
        'organizations.Organization',
        on_delete=models.CASCADE,
        related_name='shift_rules',
        verbose_name='所屬機構'
    )
    name = models.CharField(max_length=100, verbose_name='規則名稱')
    rule_type = models.CharField(
        max_length=50,
        choices=[
            ('max_consecutive_days', '最大連續工作天數'),
            ('min_rest_hours', '最小休息時數'),
            ('max_weekly_hours', '最大每週工時'),
            ('mandatory_rest_day', '強制休息日'),
        ],
        verbose_name='規則類型'
    )
    value = models.JSONField(verbose_name='規則值')
    is_active = models.BooleanField(default=True, verbose_name='啟用')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = '排班規則'
        verbose_name_plural = '排班規則'
        ordering = ['organization', 'name']

    def __str__(self):
        return f"{self.name} ({self.get_rule_type_display()})"


class TeamConstraint(models.Model):
    """
    團隊/部門層級規則（表格式 builder：scope × condition × quantifier）。

    範例：
      - 夜班需至少 1 名男性 ≥ 175cm
      - 急診室每日早班至少要有 2 名持「ACLS」證照者
      - 某分店週末班每班至少有 1 名 tag='driver' 的員工

    儲存方式：scope 由可選欄位組合；condition 用 type+operator+value JSON；
    quantifier 由 (mode, count) 構成。OR-Tools 端會把每筆 TeamConstraint
    動態翻譯為 CP-SAT 約束。
    """
    SCOPE_TIME_CHOICES = [
        ('any',   '不限'),
        ('morning', '早班'),
        ('afternoon', '中班'),
        ('evening', '晚班'),
        ('night', '深夜'),
    ]
    CONDITION_TYPE_CHOICES = [
        ('gender', '性別'),
        ('height_cm', '身高（公分）'),
        ('weight_kg', '體重（公斤）'),
        ('age_years', '年齡（歲）'),
        ('tag', '員工標籤'),
        ('certification', '證照'),
    ]
    CONDITION_OPERATOR_CHOICES = [
        ('eq', '等於'),
        ('ne', '不等於'),
        ('gte', '大於等於'),
        ('lte', '小於等於'),
        ('in', '屬於'),
        ('contains', '包含'),
    ]
    QUANTIFIER_CHOICES = [
        ('at_least', '至少'),
        ('at_most', '至多'),
        ('exactly', '恰好'),
    ]

    organization = models.ForeignKey(
        'organizations.Organization',
        on_delete=models.CASCADE,
        related_name='team_constraints',
        verbose_name='所屬機構',
    )
    branch = models.ForeignKey(
        'organizations.Branch',
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name='team_constraints',
        verbose_name='適用分店（None = 全機構）',
    )
    shift_template = models.ForeignKey(
        ShiftTemplate,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name='team_constraints',
        verbose_name='適用班別（None = 任何班別）',
    )
    scope_time_of_day = models.CharField(
        max_length=20,
        choices=SCOPE_TIME_CHOICES,
        default='any',
        verbose_name='時段範圍',
    )
    condition_type = models.CharField(
        max_length=20,
        choices=CONDITION_TYPE_CHOICES,
        verbose_name='條件類型',
    )
    condition_operator = models.CharField(
        max_length=10,
        choices=CONDITION_OPERATOR_CHOICES,
        verbose_name='比較運算子',
    )
    condition_value = models.JSONField(
        verbose_name='條件值',
        help_text='依 condition_type 而定：性別="male"、身高=175、tag=["driver"]、證照=[cert_id,...]',
    )
    quantifier = models.CharField(
        max_length=10,
        choices=QUANTIFIER_CHOICES,
        default='at_least',
        verbose_name='數量規則',
    )
    quantity = models.PositiveIntegerField(
        default=1,
        verbose_name='數量',
    )
    description = models.CharField(
        max_length=255,
        blank=True,
        verbose_name='人類可讀描述',
        help_text='前端 builder 自動生成，用於 audit log',
    )
    severity = models.CharField(
        max_length=10,
        choices=[('hard', '硬約束'), ('soft', '軟約束')],
        default='hard',
        verbose_name='約束強度',
    )
    is_active = models.BooleanField(default=True, verbose_name='啟用')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = '團隊規則'
        verbose_name_plural = '團隊規則'
        ordering = ['organization', 'shift_template', 'condition_type']

    def __str__(self):
        return self.description or (
            f"{self.get_quantifier_display()} {self.quantity} 人 "
            f"({self.get_condition_type_display()} {self.get_condition_operator_display()} {self.condition_value})"
        )


class ShiftEmployeePriority(models.Model):
    """班別員工優先順序（用於超時加班意願分配）"""
    shift_template = models.ForeignKey(
        ShiftTemplate,
        on_delete=models.CASCADE,
        related_name='employee_priorities',
        verbose_name='班別',
    )
    employee = models.ForeignKey(
        'employees.Employee',
        on_delete=models.CASCADE,
        related_name='shift_priorities',
        verbose_name='員工',
    )
    priority_rank = models.PositiveIntegerField(
        verbose_name='優先排序（1 = 最優先）',
        validators=[MinValueValidator(1)],
    )
    max_extra_shifts = models.PositiveIntegerField(
        null=True,
        blank=True,
        verbose_name='最大額外班次（null = 不限）',
    )

    class Meta:
        verbose_name = '班別員工優先順序'
        verbose_name_plural = '班別員工優先順序'
        unique_together = [['shift_template', 'employee']]
        ordering = ['shift_template', 'priority_rank']

    def __str__(self):
        return f"{self.shift_template.name} - 第{self.priority_rank}順位 - {self.employee}"
