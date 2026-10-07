"""分位組與名單對照的日序列（§1.4 `:281-282` 次要①②的原料）。純 numpy。

登錄書只寫「頂減底十分位」「候選名單對全池等權超額」，**分組邊界、tie、名單定義都未寫死**
（`plan_stats_layer.md` §A.2 #14／#15 待裁定）。本檔只提供機械零件、把裁定留給呼叫端：
- `group_labels`：依分數序位切 `n_groups` 等份（`floor(序位 × n_groups / n)`，0 ＝ 最低、`n_groups−1` ＝ 最高）；
  同分依輸入順序（stable sort）——tie 規則未裁定，呼叫端可先把分數換成任何秩再傳入。
- `spread_series`：逐日「頂組報酬均值 − 底組報酬均值」；報酬欄由呼叫端決定是原始還是扣成本（次要①要扣成本）。
- `excess_series`：逐日「名單內報酬均值 − 全池等權均值」；名單由呼叫端以布林遮罩給。
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np


def group_labels(score, n_groups: int = 10) -> np.ndarray:
    """0 起算的分位組標籤（int），長度同 `score`；`n < n_groups` 時仍照公式切（組會有空）。不接受非有限值。"""
    arr = np.asarray(score, float).ravel()
    if n_groups <= 0:
        raise ValueError(f"group_labels：n_groups={n_groups}")
    if not np.isfinite(arr).all():
        raise ValueError("group_labels：含非有限值")
    n = arr.size
    order = np.argsort(arr, kind="stable")
    labels = np.empty(n, dtype=int)
    labels[order] = (np.arange(n) * n_groups) // n
    return labels


@dataclass
class DailySeries:
    dates: np.ndarray
    value: np.ndarray
    dropped: dict[str, int] = field(default_factory=dict)

    @property
    def mean(self) -> float:
        return float(np.mean(self.value)) if self.value.size else float("nan")


def _group_by_day(dates, *cols):
    d = np.asarray(dates)
    arrs = [np.asarray(c).ravel() for c in cols]
    for a in arrs:
        if a.size != d.shape[0]:
            raise ValueError("長度不同")
    uniq, inv = np.unique(d, return_inverse=True)
    order = np.argsort(inv, kind="stable")
    bounds = np.searchsorted(inv[order], np.arange(uniq.size + 1))
    for k in range(uniq.size):
        idx = order[bounds[k]:bounds[k + 1]]
        yield uniq[k], idx, arrs


def spread_series(dates, score, ret, n_groups: int = 10, min_n: int = 1) -> DailySeries:
    """逐日頂組減底組的報酬均值。報酬非有限值的列先落掉（計 `nan_rows`）；
    當日有效列 `< min_n`、或頂／底任一組為空 → 該日不進序列（計 `days_dropped`）。"""
    s = np.asarray(score, float).ravel()
    r = np.asarray(ret, float).ravel()
    valid = np.isfinite(s) & np.isfinite(r)
    dropped = {"nan_rows": int((~valid).sum()), "days_dropped": 0}
    d = np.asarray(dates)[valid]
    out_d, out_v = [], []
    for day, idx, (sv, rv) in _group_by_day(d, s[valid], r[valid]):
        if idx.size < min_n:
            dropped["days_dropped"] += 1
            continue
        g = group_labels(sv[idx], n_groups)
        top, bot = rv[idx][g == n_groups - 1], rv[idx][g == 0]
        if top.size == 0 or bot.size == 0:
            dropped["days_dropped"] += 1
            continue
        out_d.append(day)
        out_v.append(float(top.mean() - bot.mean()))
    return DailySeries(np.asarray(out_d), np.asarray(out_v, float), dropped)


def excess_series(dates, ret, member) -> DailySeries:
    """逐日「`member` 為 True 的列報酬均值 − 當日全部列等權均值」。報酬非有限值的列先落掉；當日無名單成員 → 不進序列。"""
    r = np.asarray(ret, float).ravel()
    m = np.asarray(member, bool).ravel()
    if m.size != r.size:
        raise ValueError("excess_series：member 與 ret 長度不同")
    valid = np.isfinite(r)
    dropped = {"nan_rows": int((~valid).sum()), "days_dropped": 0}
    d = np.asarray(dates)[valid]
    out_d, out_v = [], []
    for day, idx, (rv, mv) in _group_by_day(d, r[valid], m[valid]):
        sel = rv[idx][mv[idx]]
        if sel.size == 0:
            dropped["days_dropped"] += 1
            continue
        out_d.append(day)
        out_v.append(float(sel.mean() - rv[idx].mean()))
    return DailySeries(np.asarray(out_d), np.asarray(out_v, float), dropped)
