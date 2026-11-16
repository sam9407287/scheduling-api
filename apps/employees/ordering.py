"""員工編號的「自然排序」。

`employee_id` 是自由文字（`1`、`12`、`EMP007`、`A-3`…）。直接用字串排序會得到
1, 10, 11, 12, 2, 3；管理者要的是照數字大小：1, 2, 3, 10, 11, 12（PM 2026-11-16）。
做法：把編號切成「數字段／文字段」，數字段用數值比較、文字段不分大小寫比較。
"""
import re
from typing import Iterable, List, Tuple

_CHUNKS = re.compile(r'(\d+)')


def natural_key(text: str) -> Tuple:
    """`'EMP12'` → `(((1, 0, 'emp'), (0, 12, '')), 'EMP12')`；同位置數字段排在文字段前面。

    每一段都是同型別的三元組，混合編號（有的純數字、有的帶字首）互相比較也不會丟
    TypeError。前導零不影響大小（`007` 與 `7` 相等），所以最後再用原字串當決勝鍵，
    讓排序結果固定。
    """
    value = text or ''
    chunks = tuple(
        (0, int(chunk), '') if chunk.isdigit() else (1, 0, chunk.casefold())
        for chunk in _CHUNKS.split(value) if chunk != ''
    )
    return (chunks, value)


def sort_employees_naturally(employees: Iterable) -> List:
    """依員工編號自然排序；編號相同（不同機構）時用主鍵固定順序。"""
    return sorted(employees, key=lambda e: (natural_key(e.employee_id), e.pk or 0))
