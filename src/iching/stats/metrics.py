"""只報不設門檻的三項（§1.4 裁定 T4 `:285-286`：夏普、最大回撤、月勝率）＋月度彙總（次要①的「月度為正比例」）。
純 numpy。登錄書未定義這三項的算式（`spec/` 全文亦無），**本檔的口徑是本層的選擇、報告須寫明**：

- `sharpe`：`mean / std(ddof=1) × √periods_per_year`；`periods_per_year=None` 時不年化。
- `max_drawdown`：權益曲線（`compound=True` 用 `cumprod(1+x)`，否則 `cumsum(x)`，起點 1／0）自高點回落的最大幅度，
  回正數（0 ＝ 無回撤）。
- 月度：日期取 ISO 字串前 7 碼 `YYYY-MM` 分桶（字串切片，不用日期時間模組），每月一個彙總值（均值或加總由參數定）。
  `monthly_positive_share`：正月數 ÷ 月數；`monthly_winrate` ＝ 月加總 > 0 的比例。
"""
from __future__ import annotations

import numpy as np


def sharpe(x, periods_per_year: int | None = None) -> float:
    arr = np.asarray(x, float).ravel()
    if arr.size < 2:
        return float("nan")
    sd = float(np.std(arr, ddof=1))
    if sd == 0.0:
        return float("nan")
    s = float(np.mean(arr)) / sd
    return s * float(np.sqrt(periods_per_year)) if periods_per_year else s


def max_drawdown(x, compound: bool = True) -> float:
    arr = np.asarray(x, float).ravel()
    if arr.size == 0:
        return float("nan")
    if compound:
        eq = np.concatenate(([1.0], np.cumprod(1.0 + arr)))
        peak = np.maximum.accumulate(eq)
        dd = 1.0 - eq / peak
    else:
        eq = np.concatenate(([0.0], np.cumsum(arr)))
        peak = np.maximum.accumulate(eq)
        dd = peak - eq
    return float(dd.max())


def monthly_agg(dates, x, how: str = "mean") -> tuple[np.ndarray, np.ndarray]:
    """依 `YYYY-MM` 分桶：回 `(months, values)`，months 排序。`how` ∈ {"mean", "sum"}。"""
    d = np.asarray(dates).astype(str)
    arr = np.asarray(x, float).ravel()
    if d.shape[0] != arr.size:
        raise ValueError("monthly_agg：長度不同")
    if how not in ("mean", "sum"):
        raise ValueError(f"monthly_agg：how={how!r}")
    keys = np.array([s[:7] for s in d])
    months, inv = np.unique(keys, return_inverse=True)
    sums = np.bincount(inv, weights=arr, minlength=months.size)
    if how == "sum":
        return months, sums
    cnt = np.bincount(inv, minlength=months.size)
    return months, sums / cnt


def monthly_positive_share(dates, x, how: str = "mean") -> tuple[float, int, int]:
    """回 `(正比例, 正月數, 月數)`；月數 0 → `(nan, 0, 0)`。"""
    months, vals = monthly_agg(dates, x, how)
    n = int(months.size)
    if n == 0:
        return float("nan"), 0, 0
    pos = int((vals > 0).sum())
    return pos / n, pos, n


def monthly_winrate(dates, x) -> float:
    """月勝率＝月加總 > 0 的月份比例。"""
    return monthly_positive_share(dates, x, how="sum")[0]
