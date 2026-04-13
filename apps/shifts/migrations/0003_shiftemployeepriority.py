# Generated migration for ShiftEmployeePriority

import django.core.validators
import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('shifts', '0002_alter_shifttemplate_break_minutes_and_more'),
        ('employees', '0002_employeeavailability_employeetimeslot'),
    ]

    operations = [
        migrations.CreateModel(
            name='ShiftEmployeePriority',
            fields=[
                (
                    'id',
                    models.BigAutoField(
                        auto_created=True, primary_key=True,
                        serialize=False, verbose_name='ID',
                    ),
                ),
                (
                    'priority_rank',
                    models.PositiveIntegerField(
                        validators=[django.core.validators.MinValueValidator(1)],
                        verbose_name='優先排序（1 = 最優先）',
                    ),
                ),
                (
                    'max_extra_shifts',
                    models.PositiveIntegerField(
                        blank=True, null=True,
                        verbose_name='最大額外班次（null = 不限）',
                    ),
                ),
                (
                    'shift_template',
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name='employee_priorities',
                        to='shifts.shifttemplate',
                        verbose_name='班別',
                    ),
                ),
                (
                    'employee',
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name='shift_priorities',
                        to='employees.employee',
                        verbose_name='員工',
                    ),
                ),
            ],
            options={
                'verbose_name': '班別員工優先順序',
                'verbose_name_plural': '班別員工優先順序',
                'ordering': ['shift_template', 'priority_rank'],
                'unique_together': {('shift_template', 'employee')},
            },
        ),
    ]
