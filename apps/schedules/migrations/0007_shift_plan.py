# 2026-10-31: 每張班表自己的每日班別計畫（取代 ShiftTemplate.min_staff_count +
# 機構 StaffingRequirement 作為人數來源）。資料回填：既有每個版本的每一天 ×
# 機構啟用班別，各生成一列，人數 = 當時的機構規則（date > weekday > 每天、
# 分店專屬優先）否則班別 min_staff_count。當日總人數規則無法對應，略過。

from django.db import migrations, models
import django.db.models.deletion


def _resolve(rules, day, shift_id, branch_id):
    best, best_score = None, -1
    for r in rules:
        if r['shift_template_id'] != shift_id:
            continue
        if r['branch_id'] is not None and r['branch_id'] != branch_id:
            continue
        if r['date'] is not None:
            if r['date'] != day:
                continue
            score = 4
        elif r['weekday'] is not None:
            if r['weekday'] != day.weekday():
                continue
            score = 2
        else:
            score = 0
        if r['branch_id'] is not None:
            score += 1
        if score > best_score:
            best, best_score = r, score
    return best


def backfill(apps, schema_editor):
    from datetime import timedelta
    ScheduleVersion = apps.get_model('schedules', 'ScheduleVersion')
    Plan = apps.get_model('schedules', 'ScheduleVersionShiftPlan')
    ShiftTemplate = apps.get_model('shifts', 'ShiftTemplate')
    StaffingRequirement = apps.get_model('shifts', 'StaffingRequirement')

    rules_by_org = {}
    for r in StaffingRequirement.objects.filter(is_active=True, shift_template__isnull=False):
        rules_by_org.setdefault(r.organization_id, []).append({
            'shift_template_id': r.shift_template_id, 'branch_id': r.branch_id,
            'weekday': r.weekday, 'date': r.date,
            'min_staff': r.min_staff, 'max_staff': r.max_staff,
        })
    shifts_by_org = {}
    for st in ShiftTemplate.objects.filter(is_active=True):
        shifts_by_org.setdefault(st.organization_id, []).append(st)

    for version in ScheduleVersion.objects.all().iterator():
        if (version.period_end - version.period_start).days > 400:
            continue  # 保護：異常長的期間不回填
        shifts = shifts_by_org.get(version.organization_id, [])
        if not shifts:
            continue
        rules = rules_by_org.get(version.organization_id, [])
        rows = []
        day = version.period_start
        while day <= version.period_end:
            for st in shifts:
                hit = _resolve(rules, day, st.id, version.branch_id)
                rows.append(Plan(
                    schedule_version_id=version.id, date=day, shift_template_id=st.id,
                    min_staff=(hit['min_staff'] if hit else st.min_staff_count),
                    max_staff=(hit['max_staff'] if hit else None),
                ))
            day += timedelta(days=1)
        Plan.objects.bulk_create(rows, ignore_conflicts=True)


class Migration(migrations.Migration):

    dependencies = [
        ('schedules', '0006_alter_schedule_status'),
        ('shifts', '0006_staffingrequirement'),
    ]

    operations = [
        migrations.AddField(
            model_name='scheduleversion',
            name='shift_plan_template',
            field=models.JSONField(blank=True, default=dict, verbose_name='每日班別計畫星期範本'),
        ),
        migrations.CreateModel(
            name='ScheduleVersionShiftPlan',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('date', models.DateField(verbose_name='日期')),
                ('min_staff', models.PositiveIntegerField(default=1, verbose_name='需要人數（最少）')),
                ('max_staff', models.PositiveIntegerField(blank=True, null=True, verbose_name='人數上限（null = 不限）')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('schedule_version', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='shift_plan', to='schedules.scheduleversion', verbose_name='排班版本')),
                ('shift_template', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='version_plans', to='shifts.shifttemplate', verbose_name='班別')),
            ],
            options={
                'verbose_name': '每日班別計畫',
                'verbose_name_plural': '每日班別計畫',
                'ordering': ['date', 'shift_template__start_time', 'shift_template_id'],
                'unique_together': {('schedule_version', 'date', 'shift_template')},
            },
        ),
        migrations.RunPython(backfill, migrations.RunPython.noop),
    ]
