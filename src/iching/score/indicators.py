"""技術量共用算式（純函式、numpy）。所有視窗一律以**陣列索引（交易日）**計，不出現 timedelta（B3.3「交易日 vs 曆日」）。

慣例：輸入陣列升冪、最後一個元素＝T 日；`*_prev` 表示「取 T−1 為止」（B2.0：`ATR14_{t−1}`／`VMA20_{t−1}`）。
"""
from __future__ import annotations

import numpy as np

ATR_N = 14   # ATR14：B1.0／B2.0 的符號定義（ATR14_{t−1}），非校準對象


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


def _atr_series_of_tr(tr: np.ndarray, n: int, method: str) -> np.ndarray:
    """由 TR 序列算逐點 ATR（第 j 點＝以 tr[j] 為終點）。simple＝n 個 TR 簡單平均；wilder＝首值簡單平均、之後
    ATR_j = (ATR_{j−1}×(n−1) + TR_j)/n。不足 n 筆者 NaN。"""
    if method == "simple":
        return sma_series(tr, n)
    if method == "wilder":
        out = np.full(tr.shape, np.nan)
        if tr.size >= n:
            out[n - 1] = float(np.mean(tr[:n]))
            for j in range(n, tr.size):
                out[j] = (out[j - 1] * (n - 1) + tr[j]) / n
        return out
    raise ValueError(f"unknown ATR method {method!r}")


def atr14_prev(high, low, close, method: str, n: int = ATR_N) -> float | None:
    """ATR14 取 T−1 為止：以 T−1 為終點的 14 個 TR。
    裁定（2026-09-10，P2-KICKOFF §5 #13）：`method="simple"`＝簡單平均（spec 字面「14 日平均」，非 Wilder）；
    `"wilder"` 為可選慣例，由 `Rules.atr_method` 決定並進指紋。需 len ≥ n+2。"""
    tr = true_range(high, low, close)
    # tr[j] 對應索引 j+1；T−1 對應索引 len−2 → tr 索引 len−3
    if tr.size < n + 1:
        return None
    a = _atr_series_of_tr(tr, n, method)
    v = a[-2]
    return None if np.isnan(v) else float(v)


def atr_series_prev(high, low, close, method: str, n: int = ATR_N) -> np.ndarray:
    """逐日的 ATR14_{t−1}（第 t 筆＝以 t−1 為終點的 14 個 TR），不足者 NaN。`method` 同 `atr14_prev`。"""
    h = as_f(high)
    out = np.full(h.shape, np.nan)
    tr = true_range(high, low, close)          # tr[j] ↔ index j+1
    if tr.size < n:
        return out
    a = _atr_series_of_tr(tr, n, method)        # a[j] ↔ TR window ending at index j+1
    # ATR_{t−1} for index t = a at tr index (t−1)−1 = t−2
    for t in range(n + 1, h.size):
        out[t] = a[t - 2]
    return out


def ma_change(a, n_ma: int, n_change: int) -> float | None:
    """MA_{n_ma}(T) − MA_{n_ma}(T−n_change)。"""
    now = sma_at(a, n_ma, 0)
    then = sma_at(a, n_ma, n_change)
    if now is None or then is None:
        return None
    return now - then


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


def swing_points(high, low, k: int, window: int, tie_counts: bool) -> tuple[list[int], list[int]]:
    """已確認擺動點（B2.2）：`H_i` 為前後各 k 根的最高 → 波峰；`L_i` 為前後各 k 根的最低 → 波谷。
    只取 i ∈ [len−window, len−1−k]（須有 k 根後續 K 才確認）。回 (peaks, troughs) 索引升冪。
    裁定（2026-09-10，P2-KICKOFF §5 #18）：`tie_counts=True`＝平手也計為波峰／波谷（H_i 等於視窗最高即算）；
    False＝須嚴格高於視窗內其他 K。由 `Rules.swing_tie_counts` 決定。"""
    h, l = as_f(high), as_f(low)
    n = h.size
    peaks, troughs = [], []
    start = max(k, n - window)
    for i in range(start, n - k):
        others_h = np.r_[h[i - k:i], h[i + 1:i + k + 1]]
        others_l = np.r_[l[i - k:i], l[i + 1:i + k + 1]]
        if (h[i] >= others_h.max()) if tie_counts else (h[i] > others_h.max()):
            peaks.append(i)
        if (l[i] <= others_l.min()) if tie_counts else (l[i] < others_l.min()):
            troughs.append(i)
    return peaks, troughs
