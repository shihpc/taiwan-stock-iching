"""技術量共用算式（純函式、numpy）。所有視窗一律以**陣列索引（交易日）**計，不出現 timedelta（B3.3「交易日 vs 曆日」）。

慣例：輸入陣列升冪、最後一個元素＝T 日；`*_prev` 表示「取 T−1 為止」（B2.0：`ATR14_{t−1}`／`VMA20_{t−1}`）。
"""
from __future__ import annotations

import numpy as np


def as_f(a) -> np.ndarray:
    return np.asarray(a, dtype=float)


def sma_last(a, n: int) -> float | None:
    """最後 n 筆的簡單平均；不足 n 筆回 None。"""
    a = as_f(a)
    if a.size < n or n <= 0:
        return None
    return float(np.mean(a[-n:]))


def sma_at(a, n: int, offset: int) -> float | None:
    """以 T−offset 為終點的 n 日均（offset=0 為含 T）。"""
    a = as_f(a)
    end = a.size - offset
    if end - n < 0 or end <= 0:
        return None
    return float(np.mean(a[end - n:end]))


def sma_series(a, n: int) -> np.ndarray:
    """逐日 n 日均，前 n−1 筆為 NaN。"""
    a = as_f(a)
    out = np.full(a.shape, np.nan)
    if a.size >= n:
        c = np.cumsum(np.insert(a, 0, 0.0))
        out[n - 1:] = (c[n:] - c[:-n]) / n
    return out


def true_range(high, low, close) -> np.ndarray:
    """TR_i（i ≥ 1）＝max(H−L, |H−C_{i−1}|, |L−C_{i−1}|)；長度 len−1（對應索引 1..len−1）。"""
    h, l, c = as_f(high), as_f(low), as_f(close)
    if not (h.size == l.size == c.size):
        raise ValueError("high/low/close length mismatch")
    if h.size < 2:
        return np.array([])
    pc = c[:-1]
    return np.maximum.reduce([h[1:] - l[1:], np.abs(h[1:] - pc), np.abs(l[1:] - pc)])


def atr14_prev(high, low, close, n: int = 14) -> float | None:
    """ATR14 取 T−1 為止：以 T−1 為終點的 14 個 TR 的簡單平均（v1.2.2「以 T−1 為止 14 日計算的平均真實波幅」）。
    # SPEC-NOTE: 規格未指明 Wilder 平滑或簡單平均，採**簡單平均**（原文用字「平均」）。需 len ≥ n+2。"""
    tr = true_range(high, low, close)
    # tr[j] 對應索引 j+1；T−1 對應索引 len−2 → tr 索引 len−3
    if tr.size < n + 1:
        return None
    seg = tr[-(n + 1):-1]
    return float(np.mean(seg))


def atr_series_prev(high, low, close, n: int = 14) -> np.ndarray:
    """逐日的 ATR14_{t−1}（第 t 筆＝以 t−1 為終點的 14 個 TR 平均），不足者 NaN。"""
    h = as_f(high)
    out = np.full(h.shape, np.nan)
    tr = true_range(high, low, close)          # tr[j] ↔ index j+1
    if tr.size < n:
        return out
    ma = sma_series(tr, n)                       # ma[j] ↔ TR window ending at index j+1
    # ATR_{t−1} for index t = ma at tr index (t−1)−1 = t−2
    for t in range(n + 1, h.size):
        out[t] = ma[t - 2]
    return out


def ma_change(a, n_ma: int, n_change: int) -> float | None:
    """MA_{n_ma}(T) − MA_{n_ma}(T−n_change)。"""
    now = sma_at(a, n_ma, 0)
    then = sma_at(a, n_ma, n_change)
    if now is None or then is None:
        return None
    return now - then


def pct_change(a, n: int) -> float | None | str:
    """(a_T / a_{T−n} − 1) × 100；a_{T−n}=0 回 'denominator_zero'；不足回 None。"""
    a = as_f(a)
    if a.size < n + 1:
        return None
    base = a[-1 - n]
    if base == 0:
        return "denominator_zero"
    return float((a[-1] / base - 1.0) * 100.0)


def obv(close, volume) -> np.ndarray:
    """OBV_t = OBV_{t−1} + sign(C_t − C_{t−1}) × V_t（C 相等計 0）；OBV_0 = 0。"""
    c, v = as_f(close), as_f(volume)
    out = np.zeros(c.shape)
    for i in range(1, c.size):
        s = np.sign(c[i] - c[i - 1])
        out[i] = out[i - 1] + s * v[i]
    return out


def ols_slope(y) -> float | None:
    """對 y（等距 x=0..n−1）做最小平方線性迴歸的斜率。"""
    y = as_f(y)
    n = y.size
    if n < 2:
        return None
    x = np.arange(n, dtype=float)
    xm, ym = x.mean(), y.mean()
    den = float(np.sum((x - xm) ** 2))
    if den == 0:
        return None
    return float(np.sum((x - xm) * (y - ym)) / den)


def swing_points(high, low, k: int, window: int) -> tuple[list[int], list[int]]:
    """已確認擺動點（B2.2）：`H_i` 為前後各 k 根的最高 → 波峰；`L_i` 為前後各 k 根的最低 → 波谷。
    只取 i ∈ [len−window, len−1−k]（須有 k 根後續 K 才確認）。回 (peaks, troughs) 索引升冪。"""
    h, l = as_f(high), as_f(low)
    n = h.size
    peaks, troughs = [], []
    start = max(k, n - window)
    for i in range(start, n - k):
        seg_h = h[i - k:i + k + 1]
        seg_l = l[i - k:i + k + 1]
        if h[i] == seg_h.max():
            peaks.append(i)
        if l[i] == seg_l.min():
            troughs.append(i)
    return peaks, troughs
