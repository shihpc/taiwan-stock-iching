"""IC ＝ 每日 Spearman(方向分數, h 日前向報酬) 的日序列（§1.4 `:277-278`）。純 numpy，無 scipy。

- `average_rank`：tie 取平均秩（scipy `rankdata` 預設；登錄書未寫 tie 處理，`plan_stats_layer.md` §A.2 #7 建議值，
  **介面上不鎖死**——若日後裁定改用其他 tie 規則，換 `spearman` 的 `rank_fn` 即可）。
- `spearman`：秩的 Pearson 相關；任一邊常數（秩全相同）回 nan。
- `daily_ic`：逐日分組、配對任一邊非有限值的列自動落掉並計數、某日有效對數 `< min_n` 不進序列並計數。
  `min_n` 登錄書未定（§A.2 #8），由呼叫端傳入。
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

import numpy as np


def average_rank(a) -> np.ndarray:
    """1 起算的平均秩：`[1,2,2,3,4] → [1,2.5,2.5,4,5]`。不接受非有限值。"""
    arr = np.asarray(a, float).ravel()
    n = arr.size
    if n == 0:
        return np.empty(0)
    if not np.isfinite(arr).all():
        raise ValueError("average_rank：含非有限值")
    order = np.argsort(arr, kind="stable")
    s = arr[order]
    change = np.empty(n, dtype=bool)
    change[0] = True
    change[1:] = s[1:] != s[:-1]
    start = np.flatnonzero(change)
    end = np.append(start[1:], n)
    avg = (start + end - 1) / 2.0 + 1.0          # 同值群在 0 起算位置 start..end-1 的平均，再 +1
    grp = np.cumsum(change) - 1
    ranks = np.empty(n)
    ranks[order] = avg[grp]
    return ranks


def spearman(a, b, rank_fn: Callable = average_rank) -> float:
    """Spearman ρ ＝ Pearson(rank(a), rank(b))。n<2 或任一邊秩為常數 → nan。"""
    ra = rank_fn(a)
    rb = rank_fn(b)
    if ra.shape != rb.shape:
        raise ValueError(f"spearman：長度不同 {ra.shape} vs {rb.shape}")
    n = ra.size
    if n < 2:
        return float("nan")
    da = ra - ra.mean()
    db = rb - rb.mean()
    ssa = float(np.dot(da, da))
    ssb = float(np.dot(db, db))
    if ssa == 0.0 or ssb == 0.0:
        return float("nan")
    return float(np.dot(da, db) / np.sqrt(ssa * ssb))


@dataclass
class DailyIC:
    """`daily_ic` 的結果：`dates`／`ic`／`n` 三個等長陣列（只含進序列的日子，依日期排序）＋丟棄計數。"""
    dates: np.ndarray
    ic: np.ndarray
    n: np.ndarray
    dropped: dict[str, int] = field(default_factory=dict)

    @property
    def mean(self) -> float:
        return float(np.mean(self.ic)) if self.ic.size else float("nan")


def daily_ic(dates, score, ret, min_n: int, rank_fn: Callable = average_rank) -> DailyIC:
    """逐日 Spearman。`dates` 任意可排序鍵（字串日期或日曆索引）；三個輸入等長、一列一個 (日, 分數, 報酬) 配對。

    丟棄計數（`dropped`）：
    - `nan_pairs`：分數或報酬任一非有限值的列（不進任何一天的配對）；
    - `days_below_min_n`：有效對數 `< min_n` 的日子（含全部配對都被 `nan_pairs` 吃掉的日子）；
    - `days_degenerate`：有效對數夠但秩為常數（ρ 算不出）的日子。
    """
    d = np.asarray(dates)
    s = np.asarray(score, float).ravel()
    r = np.asarray(ret, float).ravel()
    if not (d.shape[0] == s.size == r.size):
        raise ValueError(f"daily_ic：長度不同 dates={d.shape[0]} score={s.size} ret={r.size}")
    valid = np.isfinite(s) & np.isfinite(r)
    dropped = {"nan_pairs": int((~valid).sum()), "days_below_min_n": 0, "days_degenerate": 0}
    all_days = np.unique(d)
    dv, inv = np.unique(d[valid], return_inverse=True)
    sv, rv = s[valid], r[valid]
    order = np.argsort(inv, kind="stable")
    bounds = np.searchsorted(inv[order], np.arange(dv.size + 1))
    out_d, out_ic, out_n = [], [], []
    for k in range(dv.size):
        idx = order[bounds[k]:bounds[k + 1]]
        if idx.size < min_n:
            dropped["days_below_min_n"] += 1
            continue
        rho = spearman(sv[idx], rv[idx], rank_fn)
        if not np.isfinite(rho):
            dropped["days_degenerate"] += 1
            continue
        out_d.append(dv[k])
        out_ic.append(rho)
        out_n.append(int(idx.size))
    dropped["days_below_min_n"] += int(all_days.size - dv.size)     # 整天都被 nan_pairs 吃掉的日子
    return DailyIC(np.asarray(out_d), np.asarray(out_ic, float), np.asarray(out_n, int), dropped)
