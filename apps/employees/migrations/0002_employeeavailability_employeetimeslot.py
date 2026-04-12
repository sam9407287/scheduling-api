"""
Migration: Add EmployeeAvailability and EmployeeTimeSlot models
"""
import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("employees", "0001_initial"),
    ]

    operations = [
        migrations.CreateModel(
            name="EmployeeAvailability",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("required_hours_per_week", models.DecimalField(
                    blank=True, decimal_places=2, max_digits=5, null=True,
                    verbose_name="每週所需工時（None = 沿用合約設定）",
                )),
                ("special_rules", models.TextField(
                    blank=True,
                    verbose_name="特殊規則（自然語言）",
                    help_text="例如：只能排上午班；週三不可排班；盡量安排連續班次",
                )),
                ("effective_from", models.DateField(blank=True, null=True, verbose_name="生效起始日（None = 永久）")),
                ("effective_to", models.DateField(blank=True, null=True, verbose_name="生效結束日（None = 永久）")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("employee", models.OneToOneField(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name="availability",
                    to="employees.employee",
                    verbose_name="員工",
                )),
            ],
            options={
                "verbose_name": "員工可用性設定",
                "verbose_name_plural": "員工可用性設定",
            },
        ),
        migrations.CreateModel(
            name="EmployeeTimeSlot",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("slot_type", models.CharField(
                    choices=[("blocked", "不可排班"), ("preferred", "優先偏好")],
                    max_length=10,
                    verbose_name="類型",
                )),
                ("day_of_week", models.IntegerField(
                    blank=True,
                    choices=[(0, "週一"), (1, "週二"), (2, "週三"), (3, "週四"), (4, "週五"), (5, "週六"), (6, "週日")],
                    null=True,
                    verbose_name="星期幾（None = 每天）",
                )),
                ("start_time", models.TimeField(verbose_name="開始時間")),
                ("end_time", models.TimeField(verbose_name="結束時間")),
                ("label", models.CharField(
                    blank=True, max_length=100,
                    verbose_name="備註標籤",
                    help_text="例如：接送孩子、學校課程",
                )),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("availability", models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name="time_slots",
                    to="employees.employeeavailability",
                    verbose_name="可用性設定",
                )),
            ],
            options={
                "verbose_name": "員工時段設定",
                "verbose_name_plural": "員工時段設定",
                "ordering": ["day_of_week", "start_time"],
            },
        ),
    ]
