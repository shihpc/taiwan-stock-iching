"""共用變換（P1-B1 §B1.0／P1-B2 §B2.0，兩節逐字相同）。純函式、無 I/O。

- `S(x; c, d)`      ＝ 100／(1＋exp(−k(x−c)))，k＝ln(7/3)/d（v1.2.2 §4.1：d＝分數由 50 升到 70 所需的 x 變化）
- `clip_3d(x; c, d)`＝ 把 x 截在 [c−3d, c+3d]（v1.2.2 §4.1a，預設政策；截斷只作用於送入 S 的 x）
- `S_clip`          ＝ S(clip_3d(x))，可達區間 [7.30, 92.70]（k·3d ＝ 3·ln(7/3)）
- `N(v; lo, hi)`    ＝ 7.30 + (v − lo) ÷ (hi − lo) × 85.40（原生值域 → S 的有效值域；中點恆映到 50.0）
- `L(x; a, b, c)`   ＝ 三點線性映射 a→20、b→50、c→80，兩端夾在 [10, 90]（宣告值域）
- `P_hist`          ＝ 序列自身近 n（250）日百分位（0–100）

**缺值不得靜默成 50**：子指標回 `Missing(reason)`，原因碼依 S1 §A2.3 區分 `missing`／`denominator_zero`，
另有 `insufficient_history`／`stale`／`contract_rolled`／`spec_gap`（規格缺口，見各處 `# SPEC-GAP:`）。
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Sequence

import numpy as np

LN_7_3 = math.log(7.0 / 3.0)
S_LO = 7.30
S_HI = 92.70
S_SPAN = 85.40
S_RANGE: tuple[float, float] = (S_LO, S_HI)
L_DECLARED_RANGE: tuple[float, float] = (10.0, 90.0)
PCT_RANGE: tuple[float, float] = (0.0, 100.0)

# 缺值原因碼（S1 §A2.3：denominator_zero 與 missing 分開統計）
REASON_MISSING = "missing"
REASON_DENOM_ZERO = "denominator_zero"
REASON_INSUFFICIENT = "insufficient_history"
REASON_STALE = "stale"
REASON_CONTRACT_ROLLED = "contract_rolled"
REASON_SPEC_GAP = "spec_gap"
REASON_NOT_ELIGIBLE = "not_eligible"
REASON_LINE_UNKNOWN = "line_unknown"


@dataclass(frozen=True)
class Missing:
    """缺值。`reason` 為原因碼，`detail` 供報表；**永遠不是數字**，任何算術都會在型別上炸掉而不是變 50。"""
    reason: str
    detail: str = ""

    def __bool__(self) -> bool:  # 讓 `if not result` 讀起來像「缺值」
        return False


@dataclass(frozen=True)
class Ind:
    """單一子指標的轉換輸出（**原生尺度**）。

    - `native`：轉換輸出（S＋clip_3d 者已在 [7.30, 92.70]；L 在 [10, 90]；百分位／情境表在 [0, 100]）
    - `native_range`：宣告值域，框架據此**套用一次** `N`（政策第 7 點：不得重複）
    - `x`：送入轉換前的原始值（未截斷），供情境門檻／旗標／截斷比例統計
    - `clipped`：`clip_3d` 是否生效（截斷比例統計用）
    """
    native: float
    native_range: tuple[float, float]
    x: float | None = None
    clipped: bool = False
    meta: dict = field(default_factory=dict)


def S(x: float, c: float, d: float) -> float:
    """S(x; c, d)。d ≤ 0 是設定錯誤，直接拋錯（不是缺值）。"""
    if d <= 0:
        raise ValueError(f"d must be > 0, got {d}")
    k = LN_7_3 / d
    z = -k * (x - c)
    # 數值保護：exp 溢位時極限為 0／100
    if z > 700:
        return 0.0
    if z < -700:
        return 100.0
    return 100.0 / (1.0 + math.exp(z))


def clip_3d(x: float, c: float, d: float) -> float:
    lo, hi = c - 3.0 * d, c + 3.0 * d
    return min(max(x, lo), hi)


def S_clip(x: float, c: float, d: float, direction: int = 1) -> Ind:
    """`clip_3d` → `S`。`direction=-1` 為反向（B1.4 族 D、B1.6 B1、B2.5 族 E）：以 (−x, −c) 送入，
    等價於 100 − S(x) 當 c=0；c≠0 時採「以 c 為中心反向」的讀法。"""
    if direction not in (1, -1):
        raise ValueError("direction must be +1 or -1")
    xx, cc = (x, c) if direction == 1 else (-x, -c)
    xc = clip_3d(xx, cc, d)
    return Ind(native=S(xc, cc, d), native_range=S_RANGE, x=x, clipped=(xc != xx))


def N(v: float, lo: float, hi: float) -> float:
    if hi <= lo:
        raise ValueError(f"native_range must satisfy lo < hi, got ({lo}, {hi})")
    return S_LO + (v - lo) / (hi - lo) * S_SPAN


def normalize(ind: Ind) -> float:
    """依 `native_range` 套用**一次** `N`；`native_range == S_RANGE` 者為恆等映射、**不呼叫 N**
    （政策第 4 點表格「S＋clip_3d → 恆等映射，不重複套用」；避免 (v−7.30)/85.40×85.40 的浮點誤差）。"""
    if tuple(ind.native_range) == S_RANGE:
        return float(ind.native)
    return N(ind.native, ind.native_range[0], ind.native_range[1])


def L(x: float, a: float, b: float, c: float) -> Ind:
    """三點線性映射 a→20、b→50、c→80；錨點外沿相鄰線段延伸後夾在 [10, 90]。宣告值域 [10, 90]。"""
    if not (a < b < c):
        raise ValueError(f"L anchors must satisfy a < b < c, got {a}, {b}, {c}")
    if x <= b:
        v = 50.0 + (x - b) / (b - a) * 30.0
    else:
        v = 50.0 + (x - b) / (c - b) * 30.0
    v = min(max(v, 10.0), 90.0)
    return Ind(native=v, native_range=L_DECLARED_RANGE, x=x)


def scenario(native_value: float, x: float | None = None, **meta) -> Ind:
    """情境表／離散映射的原生值（0–100 直覺尺度），框架套 `N(v; 0, 100)`。"""
    return Ind(native=float(native_value), native_range=PCT_RANGE, x=x, meta=dict(meta))


def P_hist(window: Sequence[float], n: int, include_today: bool, tie: str) -> Ind | Missing:
    """序列自身近 n 日百分位（0–100）。`n` 必填（由 Param.window 供應，250）。
    裁定（2026-09-10，P2-KICKOFF §5 #14）：`include_today=True`＝視窗含當日（最後一個元素）；`tie="mid"`＝平手取中位名次
    ＝ 100 × (#小於 + 0.5 × #等於) ÷ n（常數序列得 50、視窗最大值得 100 − 50/n）；`"low"`／`"high"` 為可選慣例。
    兩者由 `Rules.phist_include_today`／`Rules.phist_tie` 決定並進指紋。`include_today=False` 時視窗為 T−n…T−1、
    被評分的值仍為當日值。視窗不足 n 筆 → `insufficient_history`。"""
    arr = np.asarray(window, dtype=float)
    need = n if include_today else n + 1
    if arr.size < need:
        return Missing(REASON_INSUFFICIENT, f"P_hist needs {need}, got {arr.size}")
    v = float(arr[-1])
    win = arr[-n:] if include_today else arr[-n - 1:-1]
    if np.isnan(win).any() or np.isnan(v):
        return Missing(REASON_MISSING, "P_hist window has NaN")
    below = float(np.sum(win < v))
    equal = float(np.sum(win == v))
    if tie == "mid":
        rank = below + 0.5 * equal
    elif tie == "low":
        rank = below
    elif tie == "high":
        rank = below + equal
    else:
        raise ValueError(f"unknown tie rule {tie!r}")
    return Ind(native=100.0 * rank / float(n), native_range=PCT_RANGE, x=v)


def percentile_threshold(window: Sequence[float], q: float, n: int, include_today: bool, interp: str) -> float | Missing:
    """近 n 日的第 q 百分位門檻（旗標 F-高波動用：VIX ≥ 自身 250 日 80 百分位）。
    裁定（2026-09-10，P2-KICKOFF §5 #14）：`interp="linear"`＝numpy 線性內插；視窗含當日與否同 `P_hist`。
    由 `Rules.pct_interp`／`Rules.phist_include_today` 決定並進指紋。"""
    arr = np.asarray(window, dtype=float)
    need = n if include_today else n + 1
    if arr.size < need:
        return Missing(REASON_INSUFFICIENT, f"percentile needs {need}, got {arr.size}")
    win = arr[-n:] if include_today else arr[-n - 1:-1]
    if np.isnan(win).any():
        return Missing(REASON_MISSING, "window has NaN")
    return float(np.percentile(win, q, method=interp))


def scenario_value_after_N(v: float) -> float:
    """情境表離散值換算（政策第 5 點對照表）：N(v; 0, 100) ＝ 7.30 + v × 0.854。"""
    return N(v, 0.0, 100.0)

