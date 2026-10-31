"""
每日班別計畫服務（2026-10-31）。

ScheduleVersionShiftPlan 的讀寫都走這裡，讓 view、序列化器的期間擴張、
LLM 的期間擴張共用同一套規則：
  * 計畫列的日期落在版本期間外 → 版本期間向外擴張（只擴不縮，與班次寫入一致）
  * 星期範本（version.shift_plan_template）存在時，期間自動延長長出來的日子
    用範本補列；沒有範本就留空（那幾天不開班）
  * 所有寫入都先驗證：班別屬於同機構、min_staff ≥ 0、max_staff ≥ min_staff
"""
from datetime import date, timedelta
from typing import Any, Dict, Iterable, List, Optional

from django.db import transaction
from rest_framework import serializers

from .models import ScheduleVersion, ScheduleVersionShiftPlan

WEEKDAY_KEYS = [str(i) for i in range(7)]


# ---------------------------------------------------------------------------
# validation
# ---------------------------------------------------------------------------
def _as_date(value) -> date:
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value))
    except (TypeError, ValueError):
        raise serializers.ValidationError({'date': f'日期格式錯誤：{value!r}（要 YYYY-MM-DD）'})


def _org_shift_ids(organization_id: int) -> Dict[int, str]:
    from apps.shifts.models import ShiftTemplate
    return dict(
        ShiftTemplate.objects.filter(organization_id=organization_id)
        .values_list('id', 'name')
    )


def _clean_entry(entry: Dict[str, Any], valid_shifts: Dict[int, str], where: str) -> Dict[str, Any]:
    if not isinstance(entry, dict):
        raise serializers.ValidationError({where: '每筆要是物件 {shift_template, min_staff, max_staff}'})
    try:
        shift_id = int(entry.get('shift_template') or entry.get('shift_template_id') or entry.get('shift_id'))
    except (TypeError, ValueError):
        raise serializers.ValidationError({where: 'shift_template 必填（班別 id）'})
    if shift_id not in valid_shifts:
        raise serializers.ValidationError({where: f'班別 {shift_id} 不屬於本機構'})
    try:
        min_staff = int(entry.get('min_staff', 1))
    except (TypeError, ValueError):
        raise serializers.ValidationError({where: 'min_staff 要是整數'})
    if min_staff < 0:
        raise serializers.ValidationError({where: 'min_staff 不可為負'})
    max_staff = entry.get('max_staff')
    if max_staff is not None:
        try:
            max_staff = int(max_staff)
        except (TypeError, ValueError):
            raise serializers.ValidationError({where: 'max_staff 要是整數或 null'})
        if max_staff < min_staff:
            raise serializers.ValidationError({where: 'max_staff 必須 ≥ min_staff'})
    return {'shift_template_id': shift_id, 'min_staff': min_staff, 'max_staff': max_staff}


def clean_rows(organization_id: int, rows: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """[{date, shift_template, min_staff, max_staff}] → 驗證、去重（後者蓋前者）。"""
    if not isinstance(rows, list):
        raise serializers.ValidationError({'rows': '要是清單'})
    valid = _org_shift_ids(organization_id)
    out: Dict[tuple, Dict[str, Any]] = {}
    for i, row in enumerate(rows):
        if not isinstance(row, dict):
            raise serializers.ValidationError({'rows': f'第 {i} 筆格式錯誤'})
        day = _as_date(row.get('date'))
        entry = _clean_entry(row, valid, f'rows[{i}]')
        out[(day, entry['shift_template_id'])] = {'date': day, **entry}
    return list(out.values())


def clean_template(organization_id: int, template: Any) -> Dict[str, List[Dict[str, Any]]]:
    """{"0": [{shift_template, min_staff, max_staff}], …, "6": [...]}；缺的星期＝那天不開班。"""
    if not isinstance(template, dict):
        raise serializers.ValidationError({'template': '要是 {"0": [...], …, "6": [...]} 物件（0=週一）'})
    valid = _org_shift_ids(organization_id)
    out: Dict[str, List[Dict[str, Any]]] = {}
    for key, entries in template.items():
        k = str(key)
        if k not in WEEKDAY_KEYS:
            raise serializers.ValidationError({'template': f'星期鍵 {key!r} 不合法（0=週一…6=週日）'})
        if not isinstance(entries, list):
            raise serializers.ValidationError({'template': f'template[{k}] 要是清單'})
        seen: Dict[int, Dict[str, Any]] = {}
        for i, e in enumerate(entries):
            entry = _clean_entry(e, valid, f'template[{k}][{i}]')
            seen[entry['shift_template_id']] = entry
        out[k] = list(seen.values())
    return out


# ---------------------------------------------------------------------------
# read
# ---------------------------------------------------------------------------
def plan_payload(version: ScheduleVersion) -> Dict[str, Any]:
    rows = list(
        ScheduleVersionShiftPlan.objects.filter(schedule_version=version)
        .select_related('shift_template')
        .order_by('date', 'shift_template__start_time', 'shift_template_id')
    )
    version.refresh_from_db(fields=['period_start', 'period_end', 'shift_plan_template'])
    days_planned = len({r.date for r in rows})
    period_days = (version.period_end - version.period_start).days + 1
    return {
        'schedule_version': version.pk,
        'period_start': version.period_start.isoformat(),
        'period_end': version.period_end.isoformat(),
        'template': version.shift_plan_template or {},
        'rows': [
            {
                'date': r.date.isoformat(),
                'shift_template': r.shift_template_id,
                'shift_name': r.shift_template.name,
                'min_staff': r.min_staff,
                'max_staff': r.max_staff,
            }
            for r in rows
        ],
        'summary': {
            'rows': len(rows),
            'days_planned': days_planned,
            'days_in_period': period_days,
            'days_without_plan': max(0, period_days - days_planned),
        },
    }


# ---------------------------------------------------------------------------
# write
# ---------------------------------------------------------------------------
def expand_period(version: ScheduleVersion, start: date, end: date) -> None:
    """只擴不縮；filter 條件確保並發時只有真正需要擴張的 update 生效。"""
    ScheduleVersion.objects.filter(pk=version.pk, period_start__gt=start).update(period_start=start)
    ScheduleVersion.objects.filter(pk=version.pk, period_end__lt=end).update(period_end=end)
    version.refresh_from_db(fields=['period_start', 'period_end'])


def _write_rows(version: ScheduleVersion, rows: List[Dict[str, Any]]) -> None:
    objs = [
        ScheduleVersionShiftPlan(
            schedule_version=version, date=r['date'],
            shift_template_id=r['shift_template_id'],
            min_staff=r['min_staff'], max_staff=r['max_staff'],
        )
        for r in rows
    ]
    if objs:
        ScheduleVersionShiftPlan.objects.bulk_create(objs)
        expand_period(version, min(r['date'] for r in rows), max(r['date'] for r in rows))


@transaction.atomic
def replace_plan(version: ScheduleVersion, rows: Iterable[Dict[str, Any]]) -> None:
    clean = clean_rows(version.organization_id, list(rows))
    ScheduleVersionShiftPlan.objects.filter(schedule_version=version).delete()
    _write_rows(version, clean)


@transaction.atomic
def replace_day(version: ScheduleVersion, day, shifts: Iterable[Dict[str, Any]]) -> None:
    """覆寫某一天的班別清單；空清單 = 那天不開班。"""
    day = _as_date(day)
    if not isinstance(shifts, list):
        raise serializers.ValidationError({'shifts': '要是清單'})
    valid = _org_shift_ids(version.organization_id)
    seen: Dict[int, Dict[str, Any]] = {}
    for i, e in enumerate(shifts):
        entry = _clean_entry(e, valid, f'shifts[{i}]')
        seen[entry['shift_template_id']] = entry
    ScheduleVersionShiftPlan.objects.filter(schedule_version=version, date=day).delete()
    _write_rows(version, [{'date': day, **e} for e in seen.values()])
    expand_period(version, day, day)


def _expand_template(template: Dict[str, List[Dict[str, Any]]], start: date, end: date,
                     skip_days: Iterable[date] = ()) -> List[Dict[str, Any]]:
    skip = set(skip_days)
    out: List[Dict[str, Any]] = []
    day = start
    while day <= end:
        if day not in skip:
            for e in template.get(str(day.weekday()), []):
                out.append({'date': day, **e})
        day += timedelta(days=1)
    return out


@transaction.atomic
def apply_template(version: ScheduleVersion, template: Any, start, end,
                   overwrite: bool = False) -> None:
    """存範本到版本，並展開到 [start, end]。overwrite=False 時已有計畫列的日子不動。"""
    start, end = _as_date(start), _as_date(end)
    if end < start:
        raise serializers.ValidationError({'period_end': 'period_end 不可早於 period_start'})
    if (end - start).days > 366:
        raise serializers.ValidationError({'period_end': '一次最多展開 366 天'})
    clean = clean_template(version.organization_id, template)
    ScheduleVersion.objects.filter(pk=version.pk).update(shift_plan_template=clean)
    version.shift_plan_template = clean
    qs = ScheduleVersionShiftPlan.objects.filter(
        schedule_version=version, date__gte=start, date__lte=end)
    if overwrite:
        qs.delete()
        existing: set = set()
    else:
        existing = set(qs.values_list('date', flat=True))
    rows = _expand_template(clean, start, end, skip_days=existing)
    _write_rows(version, rows)
    expand_period(version, start, end)


def fill_from_template(version: ScheduleVersion, start: date, end: date) -> int:
    """期間自動延長用：只補 [start, end] 內還沒有計畫列的日子。回補了幾列。"""
    template = version.shift_plan_template or {}
    if not template or end < start:
        return 0
    existing = set(
        ScheduleVersionShiftPlan.objects.filter(
            schedule_version=version, date__gte=start, date__lte=end,
        ).values_list('date', flat=True)
    )
    rows = _expand_template(template, start, end, skip_days=existing)
    if rows:
        ScheduleVersionShiftPlan.objects.bulk_create(
            [ScheduleVersionShiftPlan(
                schedule_version=version, date=r['date'],
                shift_template_id=r['shift_template_id'],
                min_staff=r['min_staff'], max_staff=r['max_staff'],
            ) for r in rows],
            ignore_conflicts=True,
        )
    return len(rows)


def derive_template_from_rows(version: ScheduleVersion) -> Dict[str, List[Dict[str, Any]]]:
    """沒有存範本的版本：拿每個星期幾「第一個有計畫的日子」當範本。"""
    template: Dict[str, List[Dict[str, Any]]] = {}
    first_day: Dict[str, date] = {}
    for r in ScheduleVersionShiftPlan.objects.filter(schedule_version=version).order_by('date'):
        k = str(r.date.weekday())
        if k not in first_day:
            first_day[k] = r.date
        if r.date != first_day[k]:
            continue
        template.setdefault(k, []).append({
            'shift_template_id': r.shift_template_id,
            'min_staff': r.min_staff, 'max_staff': r.max_staff,
        })
    return template


def copy_from(version: ScheduleVersion, source: ScheduleVersion, start, end,
              overwrite: bool = False) -> None:
    """用另一張班表的範本（沒有就從它的計畫列推）展開到本版本的 [start, end]。"""
    template = source.shift_plan_template or derive_template_from_rows(source)
    if not template:
        raise serializers.ValidationError({'source_version_id': '來源班表沒有任何每日班別計畫可複製'})
    # 範本用 shift_template_id 鍵；clean_template 兩種鍵都收
    apply_template(version, template, start, end, overwrite=overwrite)


def has_plan(version_id: int) -> bool:
    return ScheduleVersionShiftPlan.objects.filter(schedule_version_id=version_id).exists()
