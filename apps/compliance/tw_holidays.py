"""
台灣國定假日內建表（2026-10-22，PM：班表一開始就要顯示所有節日）。

資料依「紀念日及節日實施條例」（2025 年 5 月修正：新增小年夜、教師節、
光復節、行憲紀念日、全民勞動節，自 115 年起不再彈性調整放假／補班）
與行政院人事行政總處辦公日曆表整理。`kind`：
    holiday  — 紀念日/節日本日
    makeup   — 補假日（假日逢週六/週日依規定補假）

2026（115 年）為人事總處已公布之日曆表；2027（116 年）依條例補假規則
推算，官方日曆表公布後若有出入請在此修正（只改這個檔案）。
機構自訂假日仍走 Holiday model（可新增院慶等），讀取時兩者合併，
同一天以機構自訂名稱優先。
"""
from datetime import date
from typing import Dict, List

# (date, name, kind)
TW_NATIONAL_HOLIDAYS: Dict[int, List[tuple]] = {
    2026: [
        (date(2026, 1, 1),  '元旦', 'holiday'),
        (date(2026, 2, 15), '小年夜', 'holiday'),
        (date(2026, 2, 16), '除夕', 'holiday'),
        (date(2026, 2, 17), '春節（初一）', 'holiday'),
        (date(2026, 2, 18), '春節（初二）', 'holiday'),
        (date(2026, 2, 19), '春節（初三）', 'holiday'),
        (date(2026, 2, 20), '小年夜補假', 'makeup'),
        (date(2026, 2, 27), '和平紀念日補假', 'makeup'),
        (date(2026, 2, 28), '和平紀念日', 'holiday'),
        (date(2026, 4, 3),  '兒童節補假', 'makeup'),
        (date(2026, 4, 4),  '兒童節', 'holiday'),
        (date(2026, 4, 5),  '清明節', 'holiday'),
        (date(2026, 4, 6),  '清明節補假', 'makeup'),
        (date(2026, 5, 1),  '勞動節', 'holiday'),
        (date(2026, 6, 19), '端午節', 'holiday'),
        (date(2026, 9, 25), '中秋節', 'holiday'),
        (date(2026, 9, 28), '教師節', 'holiday'),
        (date(2026, 10, 9), '國慶日補假', 'makeup'),
        (date(2026, 10, 10), '國慶日', 'holiday'),
        (date(2026, 10, 25), '光復節', 'holiday'),
        (date(2026, 10, 26), '光復節補假', 'makeup'),
        (date(2026, 12, 25), '行憲紀念日', 'holiday'),
    ],
    2027: [
        (date(2027, 1, 1),  '元旦', 'holiday'),
        (date(2027, 2, 4),  '小年夜', 'holiday'),
        (date(2027, 2, 5),  '除夕', 'holiday'),
        (date(2027, 2, 6),  '春節（初一）', 'holiday'),
        (date(2027, 2, 7),  '春節（初二）', 'holiday'),
        (date(2027, 2, 8),  '春節（初三）', 'holiday'),
        (date(2027, 2, 9),  '春節補假', 'makeup'),
        (date(2027, 2, 10), '春節補假', 'makeup'),
        (date(2027, 2, 28), '和平紀念日', 'holiday'),
        (date(2027, 3, 1),  '和平紀念日補假', 'makeup'),
        (date(2027, 4, 3),  '兒童節補假', 'makeup'),
        (date(2027, 4, 4),  '兒童節', 'holiday'),
        (date(2027, 4, 5),  '清明節', 'holiday'),
        (date(2027, 4, 6),  '兒童節補假', 'makeup'),
        (date(2027, 4, 30), '勞動節補假', 'makeup'),
        (date(2027, 5, 1),  '勞動節', 'holiday'),
        (date(2027, 6, 9),  '端午節', 'holiday'),
        (date(2027, 9, 15), '中秋節', 'holiday'),
        (date(2027, 9, 28), '教師節', 'holiday'),
        (date(2027, 10, 10), '國慶日', 'holiday'),
        (date(2027, 10, 11), '國慶日補假', 'makeup'),
        (date(2027, 10, 25), '光復節', 'holiday'),
        (date(2027, 12, 24), '行憲紀念日補假', 'makeup'),
        (date(2027, 12, 25), '行憲紀念日', 'holiday'),
    ],
}


def national_holidays(start: date, end: date) -> List[Dict]:
    """[{date, name, kind}] 依日期排序，含首尾。"""
    out = []
    for year in range(start.year, end.year + 1):
        for day, name, kind in TW_NATIONAL_HOLIDAYS.get(year, ()):
            if start <= day <= end:
                out.append({'date': day, 'name': name, 'kind': kind})
    out.sort(key=lambda h: h['date'])
    return out


def holiday_calendar(organization, start: date, end: date) -> List[Dict]:
    """國定假日 + 機構自訂假日合併；同一天機構自訂優先（source='org'）。"""
    from .models import Holiday
    merged: Dict[date, Dict] = {
        h['date']: {'date': h['date'], 'name': h['name'], 'kind': h['kind'],
                    'source': 'national', 'id': None}
        for h in national_holidays(start, end)
    }
    for row in Holiday.objects.filter(organization=organization,
                                      date__gte=start, date__lte=end):
        merged[row.date] = {'date': row.date, 'name': row.name, 'kind': 'holiday',
                            'source': 'org', 'id': row.id}
    return [merged[d] for d in sorted(merged)]


def holiday_names(organization, start: date, end: date) -> Dict[date, str]:
    return {h['date']: h['name'] for h in holiday_calendar(organization, start, end)}
