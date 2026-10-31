"""
期間排休配額（2026-10-31）。

使用者選定排班期間後，AI 先算這段期間「應該休幾天」，再把工作天數
控制在配額內：

    應休天數 = 每週休息數（weekly_rest_days，預設 2 ⇒ 例假＋休息日）按期間
               天數等比換算（四捨五入） ＋ 期間內國定假日數（含補假日、機構自訂）
    最多上班天數 = 期間天數 − 應休天數

國定假日的員工若有上班（holiday_work 允許），總上限不變，solver 會自然在
別天多給一天休 ── 這就是月薪制「國定假日出勤以補休為原則」的排班面實作。

只套用在 pay_type='monthly'（月薪制）員工；時薪制是時數驅動、假日出勤
直接加倍不補休，不套配額（與 overtime 的國定假日加給分流一致）。
"""
from datetime import date
from typing import Any, Dict, Iterable, List, Optional, Union

DateLike = Union[date, str]


def _as_date(value: DateLike) -> Optional[date]:
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def _half_up(x: float) -> int:
    return int(x + 0.5)


def compute_rest_quota(
    period_start: date,
    period_end: date,
    holidays: Iterable[DateLike] = (),
    weekly_rest_days: int = 2,
) -> Dict[str, Any]:
    """純函式：期間 × 假日表 → 配額摘要（不碰 DB）。"""
    period_days = (period_end - period_start).days + 1
    if period_days <= 0:
        return {
            'period_days': 0, 'weekly_rest_days': 0, 'holiday_days': 0,
            'holidays': [], 'rest_days_total': 0, 'work_days_max': 0,
        }
    in_period = sorted({
        d for d in (_as_date(h) for h in holidays)
        if d is not None and period_start <= d <= period_end
    })
    weekly = _half_up(period_days * max(0, int(weekly_rest_days)) / 7)
    rest_total = min(period_days, weekly + len(in_period))
    return {
        'period_days': period_days,
        'weekly_rest_days': weekly,
        'holiday_days': len(in_period),
        'holidays': [d.isoformat() for d in in_period],
        'rest_days_total': rest_total,
        'work_days_max': period_days - rest_total,
    }


def quota_applies(employee: Dict[str, Any]) -> bool:
    """月薪制才套期間排休配額（沒給 pay_type 視為月薪）。"""
    return (employee.get('pay_type') or 'monthly') == 'monthly'


def quota_from_constraints(days: List[date], constraints: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """solver 用：constraints 有 `holidays` 鍵（API 路徑一定有）即套用，
    `apply_rest_quota` 可顯式開關（派生合規版關閉：只守法律最低限度）。"""
    if not days:
        return None
    apply = constraints.get('apply_rest_quota')
    if apply is None:
        apply = 'holidays' in constraints
    if not apply:
        return None
    return compute_rest_quota(
        days[0], days[-1],
        constraints.get('holidays') or [],
        int(constraints.get('weekly_rest_days', 2)),
    )
