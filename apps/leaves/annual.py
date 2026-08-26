"""
Taiwan Labor Standards Act §38 annual-leave (特休) entitlement.

Seniority-based ladder, anniversary-year accounting:
  6 months  -> 3 days
  1 year    -> 7 days
  2 years   -> 10 days
  3-4 years -> 14 days
  5-9 years -> 15 days
  10+ years -> 15 + (years - 9), capped at 30

"Used" = calendar days of approved annual-leave requests overlapping the
current entitlement year. Full-day leave only; each date counts one day.
"""
from datetime import date, timedelta


def entitled_days(hire_date: date, on: date) -> int:
    """Annual-leave days entitled for the entitlement year containing `on`."""
    if on < hire_date:
        return 0
    years = _full_years(hire_date, on)
    if years >= 10:
        return min(30, 15 + (years - 9))
    if years >= 5:
        return 15
    if years >= 3:
        return 14
    if years >= 2:
        return 10
    if years >= 1:
        return 7
    if _months(hire_date, on) >= 6:
        return 3
    return 0


def entitlement_year(hire_date: date, on: date):
    """(start, end) of the anniversary year containing `on`.

    Before the first anniversary the window opens at the 6-month mark
    (where the 3-day tier starts).
    """
    years = _full_years(hire_date, on)
    if years >= 1:
        start = _add_years(hire_date, years)
        end = _add_years(hire_date, years + 1) - timedelta(days=1)
    else:
        start = _add_months(hire_date, 6)
        end = _add_years(hire_date, 1) - timedelta(days=1)
        if on < start:  # 未滿六個月，尚無特休窗口
            return None, None
    return start, end


def _full_years(hire_date: date, on: date) -> int:
    years = on.year - hire_date.year
    if (on.month, on.day) < (hire_date.month, hire_date.day):
        years -= 1
    return max(0, years)


def _months(hire_date: date, on: date) -> int:
    months = (on.year - hire_date.year) * 12 + on.month - hire_date.month
    if on.day < hire_date.day:
        months -= 1
    return max(0, months)


def _add_years(d: date, years: int) -> date:
    try:
        return d.replace(year=d.year + years)
    except ValueError:  # 2/29
        return d.replace(year=d.year + years, day=28)


def _add_months(d: date, months: int) -> date:
    month = d.month - 1 + months
    year = d.year + month // 12
    month = month % 12 + 1
    day = min(d.day, [31, 29 if year % 4 == 0 and (year % 100 != 0 or year % 400 == 0) else 28,
                      31, 30, 31, 30, 31, 31, 30, 31, 30, 31][month - 1])
    return date(year, month, day)
