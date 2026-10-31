# 2026-10-31: 一例一休休息日意願 + 國定假日排班意願（員工層級）

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('employees', '0004_employee_pay_type'),
    ]

    operations = [
        migrations.AddField(
            model_name='employeeavailability',
            name='rest_day_work_ok',
            field=models.BooleanField(default=False, verbose_name='願意上休息日（每週第 6 天，需加班費）'),
        ),
        migrations.AddField(
            model_name='employeeavailability',
            name='holiday_work',
            field=models.CharField(
                choices=[('forbid', '不可排國定假日'), ('allow', '可排（不特別偏好）'),
                         ('prefer', '偏好排假日班'), ('avoid', '不優先（必要時仍可排）')],
                default='allow', max_length=10, verbose_name='國定假日排班意願'),
        ),
    ]
