"""個股六爻（P1-B2 §B2.1–B2.6）＋三期間權重（§B2.7）。純函式：只吃 `StockInputs`（dict／numpy），不碰 DB。

依賴順序（B2.6）：大盤六爻先算，個股上爻族 A 吃 `market_direction_score`。
慣例：所有序列升冪、最後一筆＝T；價格與 ATR 同一還原口徑（B2.0 規則 2，由 IO 層保證）。缺值一律 `Missing`。
"""
from __future__ import annotations

import dataclasses
import math
from dataclasses import dataclass, field
from typing import Any, Sequence

import numpy as np

from .aggregate import (FamilyResult, LineResult, coverage_label, direction_score, family_score, line_score,
                        sub_result, trigram_mean)
from .indicators import (as_f, atr14_prev, atr_series_prev, ma_change, obv, ols_slope, sma_last, swing_points)
from .params import (HORIZONS, SCOPE_STOCK, STK_L2_WIN, STK_L3_WIN, STK_L4_WIN, STK_L5_WIN, STK_L6_WIN, ParamSet)
from .transform import (Ind, L, Missing, OVERHEAT_CAP, REASON_DENOM_ZERO, REASON_INSUFFICIENT, REASON_MISSING,
                        REASON_NOT_ELIGIBLE, REVENUE_HIGH_FLOOR, S_RANGE, S_clip, scenario)

INDUSTRY_MIN_SAMPLE = 5
EPS_YOY_MIN_BASE = 0.1
P_CS_OVERHEAT = 95.0
OVERHEAT_DIST_ATR = 3.0


@dataclass
class StockInputs:
    market: str
    stock_id: str
    tpe_date: str
    industry: str | None = None
    is_financial: bool = False
    # 價量（張）
    open: Any = None
    high: Any = None
    low: Any = None
    close: Any = None
    volume: Any = None
    index_close: Any = None                                   # 所屬市場指數收盤，與價量同日對齊
    # 產業聚合（B3.1 #8；industry_aggregate 鍵 market×horizon×industry×date）
    industry_median_return: dict[int, float | None] = field(default_factory=dict)   # n → 產業中位 n 日報酬 %
    industry_n: int | None = None
    industry_above_ma20_ratio: float | None = None
    p_cs_long_excess: float | None = None                     # 原生 0–100，不套 N
    # 基本面（IO 層已依 available_at 過濾）
    monthly_revenue: Sequence[tuple[str, float]] | None = None   # [('YYYY-MM', revenue), …] 升冪
    industry_median_3m_yoy: float | None = None
    industry_revenue_n: int | None = None
    fundamentals: dict | None = None    # eps, eps_ly, gross_margin, gross_margin_prev_q, price_at_period_end,
                                        # pretax_income, pretax_income_ly, equity, equity_prev_q
    # 籌碼（與 volume 同單位）
    foreign_net_shares: Any = None
    trust_net_shares: Any = None
    margin_balance: Any = None
    margin_eligible: bool = True
    short_sale_balance: Any = None
    shares_outstanding: float | None = None
    # 跨日相依（B3.1 #11）：horizon → 二爻分數序列 T−9…T−1（升冪，長度 9；缺者 None）
    line2_score_history: dict[str, Sequence[float | None]] = field(default_factory=dict)
    # 大盤方向分數（B2.6 族 A）：horizon → 分數
    market_direction_score: dict[str, float | None] = field(default_factory=dict)


def _arr(a) -> np.ndarray | None:
    if a is None:
        return None
    arr = as_f(a)
    return arr if arr.size else None


def _pct_ret(a: np.ndarray, n: int, end_offset: int = 0) -> float | None | Missing:
    """(a[T−off] / a[T−off−n] − 1) × 100。"""
    end = a.size - end_offset
    if end - n - 1 < 0:
        return Missing(REASON_INSUFFICIENT, f"return {n}")
    base = float(a[end - 1 - n])
    if base == 0:
        return Missing(REASON_DENOM_ZERO, "base=0")
    return (float(a[end - 1]) / base - 1.0) * 100.0


# ---------------------------------------------------------------------------
# B2.1 初爻｜營運基礎
# ---------------------------------------------------------------------------
def _ym_shift(ym: str, k: int) -> str:
    y, m = int(ym[:4]), int(ym[5:7])
    m -= k
    while m <= 0:
        m += 12
        y -= 1
    return f"{y:04d}-{m:02d}"


def revenue_yoy_3m(rev: dict[str, float], latest: str, offset_months: int = 0) -> float | Missing:
    """近 3 月合計 ÷ 去年同期 3 月合計 − 1（×100 pp）。`offset_months` 往前平移（加速度的前一組用 3）。"""
    months = [_ym_shift(latest, offset_months + i) for i in range(3)]
    ly = [_ym_shift(m, 12) for m in months]
    if any(m not in rev for m in months + ly):
        return Missing(REASON_MISSING, "revenue months incomplete")
    num = sum(rev[m] for m in months)
    den = sum(rev[m] for m in ly)
    if den == 0:
        return Missing(REASON_DENOM_ZERO, "last-year 3M sum=0")
    return (num / den - 1.0) * 100.0


def revenue_yoy_single(rev: dict[str, float], latest: str) -> float | Missing:
    ly = _ym_shift(latest, 12)
    if latest not in rev or ly not in rev:
        return Missing(REASON_MISSING, "single-month YoY needs same month last year")
    if rev[ly] == 0:
        return Missing(REASON_DENOM_ZERO, "last-year month=0")
    return (rev[latest] / rev[ly] - 1.0) * 100.0


def revenue_is_12m_high(rev: dict[str, float], latest: str) -> bool | Missing:
    months = [_ym_shift(latest, i) for i in range(12)]
    if any(m not in rev for m in months):
        return Missing(REASON_MISSING, "12 months incomplete")
    return rev[latest] >= max(rev[m] for m in months)


def ind_revenue_yoy(rev, latest, single: bool, d: float) -> Ind | Missing:
    x = revenue_yoy_single(rev, latest) if single else revenue_yoy_3m(rev, latest)
    return x if isinstance(x, Missing) else S_clip(x, 0.0, d)


def ind_revenue_accel(rev, latest, d: float) -> Ind | Missing:
    a, b = revenue_yoy_3m(rev, latest, 0), revenue_yoy_3m(rev, latest, 3)
    if isinstance(a, Missing):
        return a
    if isinstance(b, Missing):
        return b
    return S_clip(a - b, 0.0, d)


def ind_eps(f: dict, d_yoy: float, d_diff: float) -> tuple[str, Ind | Missing]:
    """前期（去年同期）EPS > 0.1 → 季 EPS YoY；否則 (本期 − 去年同期) ÷ 期末股價（替代指標，兩者不混尺度）。"""
    eps, eps_ly = f.get("eps"), f.get("eps_ly")
    if eps is None or eps_ly is None:
        return "eps_yoy", Missing(REASON_MISSING, "eps/eps_ly")
    if eps_ly > EPS_YOY_MIN_BASE:
        return "eps_yoy", S_clip((eps / eps_ly - 1.0) * 100.0, 0.0, d_yoy)
    px = f.get("price_at_period_end")
    if px is None:
        return "eps_diff_over_price", Missing(REASON_MISSING, "price_at_period_end")
    if px == 0:
        return "eps_diff_over_price", Missing(REASON_DENOM_ZERO, "price=0")
    return "eps_diff_over_price", S_clip((eps - eps_ly) / px * 100.0, 0.0, d_diff)


def ind_diff(f: dict, cur: str, prev: str, d: float, scale: float = 1.0) -> Ind | Missing:
    a, b = f.get(cur), f.get(prev)
    if a is None or b is None:
        return Missing(REASON_MISSING, f"{cur}/{prev}")
    return S_clip((a - b) * scale, 0.0, d)


def ind_growth(f: dict, cur: str, prev: str, d: float) -> Ind | Missing:
    """(cur/prev − 1) × 100；prev ≤ 0 → 分母無效（YoY／QoQ 對非正基期無定義）。"""
    a, b = f.get(cur), f.get(prev)
    if a is None or b is None:
        return Missing(REASON_MISSING, f"{cur}/{prev}")
    if b <= 0:
        return Missing(REASON_DENOM_ZERO, f"{prev}<=0")
    return S_clip((a / b - 1.0) * 100.0, 0.0, d)


def line1_operations(inp: StockInputs, ps: ParamSet, horizon: str) -> LineResult:
    g = lambda fam, iid: ps.get(SCOPE_STOCK, horizon, "1", fam, iid)  # noqa: E731
    weights = ps.family_weights[(SCOPE_STOCK, horizon, "1")]
    rev = {ym: float(v) for ym, v in (inp.monthly_revenue or [])}
    latest = max(rev) if rev else None
    if latest is None:
        miss = Missing(REASON_MISSING, "no monthly revenue")
        famA = family_score("A", [sub_result("revenue_yoy", miss), sub_result("revenue_accel", miss)])
    else:
        famA = family_score("A", [
            sub_result("revenue_yoy", ind_revenue_yoy(rev, latest, horizon == "short", g("A", "revenue_yoy").d)),
            sub_result("revenue_accel", ind_revenue_accel(rev, latest, g("A", "revenue_accel").d)),
        ])
        if horizon == "mid" and famA.score is not None:
            high = revenue_is_12m_high(rev, latest)
            if high is True and famA.score < REVENUE_HIGH_FLOOR:
                famA = dataclasses.replace(famA, score=REVENUE_HIGH_FLOOR, meta={**famA.meta, "revenue_high_12m": True, "floor_applied": True})
            elif high is True:
                famA = dataclasses.replace(famA, meta={**famA.meta, "revenue_high_12m": True, "floor_applied": False})
            else:
                famA = dataclasses.replace(famA, meta={**famA.meta, "revenue_high_12m": False if high is False else None})
    fams = [famA]
    if horizon == "mid":
        f = inp.fundamentals or {}
        if inp.is_financial:
            subs = [sub_result("pretax_income_yoy", ind_growth(f, "pretax_income", "pretax_income_ly", g("B", "pretax_income_yoy").d)),
                    sub_result("equity_qoq", ind_growth(f, "equity", "equity_prev_q", g("B", "equity_qoq").d))]
        else:
            iid, r = ind_eps(f, g("B", "eps_yoy").d, g("B", "eps_diff_over_price").d)
            subs = [sub_result(iid, r), sub_result("gross_margin_qoq", ind_diff(f, "gross_margin", "gross_margin_prev_q", g("B", "gross_margin_qoq").d))]
        fams.append(family_score("B", subs, financial_rule=inp.is_financial))
        if latest is None:
            c = Missing(REASON_MISSING, "no monthly revenue")
        elif inp.industry_revenue_n is None or inp.industry_revenue_n < INDUSTRY_MIN_SAMPLE or inp.industry_median_3m_yoy is None:
            c = Missing(REASON_MISSING, "industry sample < 5 or median missing")
        else:
            y = revenue_yoy_3m(rev, latest)
            c = y if isinstance(y, Missing) else S_clip(y - inp.industry_median_3m_yoy, 0.0, g("C", "revenue_yoy_vs_industry").d)
        fams.append(family_score("C", [sub_result("revenue_yoy_vs_industry", c)]))
    return line_score("1", fams, weights)


# ---------------------------------------------------------------------------
# B2.2 二爻｜價格趨勢
# ---------------------------------------------------------------------------
def ind_ma_distance(close, atr_prev, n_ma: int, d: float) -> Ind | Missing:
    ma = sma_last(close, n_ma)
    if ma is None:
        return Missing(REASON_INSUFFICIENT, f"MA{n_ma}")
    if atr_prev is None:
        return Missing(REASON_INSUFFICIENT, "ATR14")
    if atr_prev == 0:
        return Missing(REASON_DENOM_ZERO, "ATR14=0")
    return S_clip((float(as_f(close)[-1]) - ma) / atr_prev, 0.0, d)


def ind_ma_slope(close, atr_prev, n_ma: int, n_change: int, d: float) -> Ind | Missing:
    chg = ma_change(close, n_ma, n_change)
    if chg is None:
        return Missing(REASON_INSUFFICIENT, f"MA{n_ma} change {n_change}")
    if atr_prev is None:
        return Missing(REASON_INSUFFICIENT, "ATR14")
    if atr_prev == 0:
        return Missing(REASON_DENOM_ZERO, "ATR14=0")
    return S_clip(chg / atr_prev, 0.0, d)


def ind_structure(high, low, k: int, window: int) -> Ind | Missing:
    """已確認高低點結構：最近兩波峰兩波谷 HH＋HL→80、LH＋LL→20、其他→50（原生值）。
    # SPEC-NOTE: 波峰＝H_i 等於前後各 k 根視窗最高（平手亦計）；擺動點不足兩對 → 「其他」50。"""
    h, l = _arr(high), _arr(low)
    if h is None or l is None:
        return Missing(REASON_MISSING, "high/low")
    if h.size < 2 * k + 1:
        return Missing(REASON_INSUFFICIENT, f"structure needs {2 * k + 1}")
    peaks, troughs = swing_points(h, l, k, window)
    if len(peaks) < 2 or len(troughs) < 2:
        return scenario(50, structure="insufficient_swings", n_peaks=len(peaks), n_troughs=len(troughs))
    p1, p2 = peaks[-2], peaks[-1]
    t1, t2 = troughs[-2], troughs[-1]
    hh, hl = h[p2] > h[p1], l[t2] > l[t1]
    lh, ll = h[p2] < h[p1], l[t2] < l[t1]
    if hh and hl:
        return scenario(80, structure="HH+HL")
    if lh and ll:
        return scenario(20, structure="LH+LL")
    return scenario(50, structure="other")


def line2_trend(inp: StockInputs, ps: ParamSet, horizon: str) -> LineResult:
    ma_s, ma_l, slope_n, struct_w, k = STK_L2_WIN[horizon]
    g = lambda fam, iid: ps.get(SCOPE_STOCK, horizon, "2", fam, iid)  # noqa: E731
    close, high, low = _arr(inp.close), _arr(inp.high), _arr(inp.low)
    if close is None:
        miss = Missing(REASON_MISSING, "close")
        fams = [family_score("A", [sub_result("dist_ma_short", miss), sub_result("dist_ma_long", miss)]),
                family_score("B", [sub_result("ma_long_slope", miss)]), family_score("C", [sub_result("structure", miss)])]
        return line_score("2", fams, ps.family_weights[(SCOPE_STOCK, horizon, "2")])
    atr = atr14_prev(high, low, close) if (high is not None and low is not None) else None
    famA = family_score("A", [
        sub_result("dist_ma_short", ind_ma_distance(close, atr, ma_s, g("A", "dist_ma_short").d)),
        sub_result("dist_ma_long", ind_ma_distance(close, atr, ma_l, g("A", "dist_ma_long").d)),
    ])
    famB = family_score("B", [sub_result("ma_long_slope", ind_ma_slope(close, atr, ma_l, slope_n, g("B", "ma_long_slope").d))])
    famC = family_score("C", [sub_result("structure", ind_structure(high, low, k, struct_w))])
    return line_score("2", [famA, famB, famC], ps.family_weights[(SCOPE_STOCK, horizon, "2")], atr14_prev=atr)


# ---------------------------------------------------------------------------
# B2.3 三爻｜相對動能
# ---------------------------------------------------------------------------
def excess_return(close, index_close, n: int, end_offset: int = 0) -> float | Missing:
    c, i = _arr(close), _arr(index_close)
    if c is None or i is None:
        return Missing(REASON_MISSING, "close/index_close")
    if c.size != i.size:
        return Missing(REASON_MISSING, "close/index length mismatch")
    a, b = _pct_ret(c, n, end_offset), _pct_ret(i, n, end_offset)
    if isinstance(a, Missing):
        return a
    if isinstance(b, Missing):
        return b
    return a - b


def ind_excess(close, index_close, n: int, d: float) -> Ind | Missing:
    x = excess_return(close, index_close, n)
    return x if isinstance(x, Missing) else S_clip(x, 0.0, d)


def ind_excess_accel(close, index_close, n: int, d: float) -> Ind | Missing:
    """最近 n 日超額 − 其前 n 日超額（共 2n 日）。"""
    a = excess_return(close, index_close, n, 0)
    b = excess_return(close, index_close, n, n)
    if isinstance(a, Missing):
        return a
    if isinstance(b, Missing):
        return b
    return S_clip(a - b, 0.0, d)


def ind_excess_vs_industry(close, industry_median_ret: float | None, industry_n: int | None, n: int, d: float) -> Ind | Missing:
    if industry_n is None or industry_n < INDUSTRY_MIN_SAMPLE:
        return Missing(REASON_MISSING, "industry sample < 5")
    if industry_median_ret is None:
        return Missing(REASON_MISSING, "industry median return")
    c = _arr(close)
    if c is None:
        return Missing(REASON_MISSING, "close")
    r = _pct_ret(c, n)
    if isinstance(r, Missing):
        return r
    return S_clip(r - industry_median_ret, 0.0, d)


def overheated(p_cs_long_excess: float | None, close, atr_prev) -> bool | None:
    """過熱旗標（未截斷原值）：P_cs(長視窗超額) ≥ 95 且 (C − MA20)/ATR14 > 3。任一不可得 → None。"""
    if p_cs_long_excess is None:
        return None
    ma20 = sma_last(close, 20)
    if ma20 is None or atr_prev is None or atr_prev == 0:
        return None
    return p_cs_long_excess >= P_CS_OVERHEAT and (float(as_f(close)[-1]) - ma20) / atr_prev > OVERHEAT_DIST_ATR


def line3_momentum(inp: StockInputs, ps: ParamSet, horizon: str) -> LineResult:
    ws, wl = STK_L3_WIN[horizon]
    g = lambda fam, iid: ps.get(SCOPE_STOCK, horizon, "3", fam, iid)  # noqa: E731
    famA = family_score("A", [
        sub_result("excess_long", ind_excess(inp.close, inp.index_close, wl, g("A", "excess_long").d)),
        sub_result("excess_short", ind_excess(inp.close, inp.index_close, ws, g("A", "excess_short").d)),
    ])
    famB = family_score("B", [sub_result("excess_vs_industry", ind_excess_vs_industry(
        inp.close, inp.industry_median_return.get(wl), inp.industry_n, wl, g("B", "excess_vs_industry").d))])
    famC = family_score("C", [sub_result("excess_accel", ind_excess_accel(inp.close, inp.index_close, ws, g("C", "excess_accel").d))])
    lr = line_score("3", [famA, famB, famC], ps.family_weights[(SCOPE_STOCK, horizon, "3")])
    atr = atr14_prev(inp.high, inp.low, inp.close) if (inp.high is not None and inp.low is not None and inp.close is not None) else None
    hot = overheated(inp.p_cs_long_excess, inp.close, atr) if inp.close is not None else None
    meta = {**lr.meta, "overheated": hot, "p_cs_long_excess": inp.p_cs_long_excess}
    if hot and lr.score is not None and lr.score > OVERHEAT_CAP:
        return dataclasses.replace(lr, score=OVERHEAT_CAP, meta={**meta, "overheat_cap_applied": True})
    return dataclasses.replace(lr, meta=meta)


# ---------------------------------------------------------------------------
# B2.4 四爻｜量價確認
# ---------------------------------------------------------------------------
def volume_scenario_day(close, volume, atr_prev_series, i: int, n_dd: int, line2_score: float | None) -> float | Missing:
    """B2.4 族 A 情境表在第 i 日的**原生**分數（有序 if–elif，未截斷原值）。"""
    c, v = as_f(close), as_f(volume)
    if i < 20 or i < n_dd - 1:
        return Missing(REASON_INSUFFICIENT, "VMA20/drawdown window")
    atr = atr_prev_series[i]
    vma = float(np.mean(v[i - 20:i]))
    if v[i] == 0:
        return Missing(REASON_MISSING, "no trade")
    if np.isnan(atr) or atr == 0 or vma == 0:
        return Missing(REASON_DENOM_ZERO, "ATR14/VMA20 missing or zero")
    vr = v[i] / vma
    dchg = (c[i] - c[i - 1]) / atr
    dd = (float(np.max(c[i - n_dd + 1:i + 1])) - c[i]) / atr
    ma20 = float(np.mean(c[i - 19:i + 1]))
    seq1_rest = (0 < dd <= 2) and vr < 0.8 and c[i] >= ma20
    if seq1_rest:
        if line2_score is None:
            return Missing(REASON_MISSING, "line2 score needed for seq 1")
        if line2_score >= 55.0:
            return 60.0
    s = S_clip(vr - 1.0, 0.3, 0.7).native
    if dchg >= 0.5 and vr >= 1.3:
        return 60.0 + 0.5 * (s - 50.0)
    if dchg <= -0.5 and vr >= 1.3:
        return 40.0 - 0.5 * (s - 50.0)
    return 50.0


def ind_volume_scenario(close, volume, high, low, n_dd: int, horizon: str, line2_series: Sequence[float | None]) -> Ind | Missing:
    """短線：0.6×當日 + 0.4×近 5 日（含當日）平均；波段：近 10 日平均。`line2_series`＝T−9…T（長度 10）。
    # SPEC-NOTE: 多日平均取「可得日」平均；短線當日缺 → 族缺；全缺 → 族缺。"""
    c, v, h, l = _arr(close), _arr(volume), _arr(high), _arr(low)
    if c is None or v is None or h is None or l is None:
        return Missing(REASON_MISSING, "ohlcv")
    if not (c.size == v.size == h.size == l.size):
        return Missing(REASON_MISSING, "ohlcv length mismatch")
    atrs = atr_series_prev(h, l, c)
    n = c.size
    days = 5 if horizon == "short" else 10
    if n < 21 + days:
        return Missing(REASON_INSUFFICIENT, "scenario history")
    l2 = list(line2_series)
    if len(l2) != 10:
        return Missing(REASON_MISSING, "line2 series must be T-9..T (10)")
    vals = []
    for j in range(days):
        i = n - 1 - j
        r = volume_scenario_day(c, v, atrs, i, n_dd, l2[9 - j])
        vals.append(r)
    today = vals[0]
    avail = [x for x in vals if not isinstance(x, Missing)]
    if horizon == "short":
        if isinstance(today, Missing):
            return today
        return scenario(0.6 * today + 0.4 * (sum(avail) / len(avail)), today=today, n_avail=len(avail))
    if not avail:
        return vals[0]
    return scenario(sum(avail) / len(avail), n_avail=len(avail))


def ind_updown_volume_ratio(close, volume, n: int, d: float) -> Ind | Missing:
    c, v = _arr(close), _arr(volume)
    if c is None or v is None:
        return Missing(REASON_MISSING, "close/volume")
    if c.size < n + 1:
        return Missing(REASON_INSUFFICIENT, f"window {n}")
    cc, vv = c[-n - 1:], v[-n:]
    diff = np.diff(cc)
    up, dn = vv[diff > 0], vv[diff < 0]
    if up.size == 0 or dn.size == 0 or np.mean(dn) == 0 or np.mean(up) == 0:
        return Missing(REASON_DENOM_ZERO, "no up or down days")
    return S_clip(math.log(float(np.mean(up)) / float(np.mean(dn))), 0.0, d)


def ind_obv_slope(close, volume, n: int, d: float) -> Ind | Missing:
    c, v = _arr(close), _arr(volume)
    if c is None or v is None:
        return Missing(REASON_MISSING, "close/volume")
    if c.size < max(n, 21):
        return Missing(REASON_INSUFFICIENT, "OBV/VMA20 window")
    vma = float(np.mean(v[-21:-1]))
    if vma == 0:
        return Missing(REASON_DENOM_ZERO, "VMA20=0")
    slope = ols_slope(obv(c, v)[-n:])
    if slope is None:
        return Missing(REASON_INSUFFICIENT, "slope")
    return S_clip(slope / vma, 0.0, d)


def ind_close_position(high, low, close, n: int) -> Ind | Missing:
    """n 日均 (C−L)/(H−L)，H=L（一價成交）日不計入；全部不計入 → denominator_zero。"""
    h, l, c = _arr(high), _arr(low), _arr(close)
    if h is None or l is None or c is None:
        return Missing(REASON_MISSING, "hlc")
    if c.size < n:
        return Missing(REASON_INSUFFICIENT, f"window {n}")
    hh, ll, cc = h[-n:], l[-n:], c[-n:]
    m = hh != ll
    if not m.any():
        return Missing(REASON_DENOM_ZERO, "all H=L")
    return L(float(np.mean((cc[m] - ll[m]) / (hh[m] - ll[m]))), 0.0, 0.5, 1.0)


def ind_continuation(close, base_n: int, k: int) -> Ind | Missing:
    """突破／跌破延續（B2.4 族 C）。
    # SPEC-NOTE: 規格只給「突破基準＝近 n 日最高收盤、第 k 日守住→80、跌破後第 k 日未收復→20、期間內無突破跌破→50、
    #   確認窗未完成前一律 50」。實作讀法：事件日 b＝C_b 高於其前 n 日最高收盤（突破）／低於其前 n 日最低收盤（跌破）；
    #   「期間」＝最近 n 日；最近事件若在 (T−k, T]（確認未完成）→ 50；否則取最近事件 b（≥ T−n），以第 b+k 日收盤對該基準
    #   守住（≥ 基準）→80、跌破未收復（< 基準）→20，確認失敗的反向情形→50；無事件→50。"""
    c = _arr(close)
    if c is None:
        return Missing(REASON_MISSING, "close")
    n = c.size
    if n < base_n + k + 1:
        return Missing(REASON_INSUFFICIENT, f"continuation needs {base_n + k + 1}")
    T = n - 1
    events = []
    # 事件登錄後，其確認窗 (b, b+k] 內的再創高／再創低**不另立事件**（否則趨勢中每天都是新事件、永遠「確認未完成」）
    for b in range(max(base_n, T - base_n - k), T + 1):
        if events and b <= events[-1][0] + k:
            continue
        prior = c[b - base_n:b]
        if c[b] > prior.max():
            events.append((b, "up", float(prior.max())))
        elif c[b] < prior.min():
            events.append((b, "down", float(prior.min())))
    events = [e for e in events if e[0] >= T - base_n]
    if not events:
        return scenario(50, continuation="no_event")
    b, kind, level = events[-1]
    if b + k > T:
        return scenario(50, continuation="pending", event_day_offset=T - b)
    confirm = c[b + k]
    if kind == "up":
        return scenario(80, continuation="breakout_held", level=level) if confirm >= level else scenario(50, continuation="breakout_failed", level=level)
    return scenario(20, continuation="breakdown_unrecovered", level=level) if confirm < level else scenario(50, continuation="breakdown_recovered", level=level)


def line4_volume_price(inp: StockInputs, ps: ParamSet, horizon: str, line2_today: float | None) -> LineResult:
    dd_n, cp_n, base_n, confirm_k = STK_L4_WIN[horizon]
    g = lambda fam, iid: ps.get(SCOPE_STOCK, horizon, "4", fam, iid)  # noqa: E731
    if horizon == "mid":
        pa1, pa2 = g("A", "updown_volume_ratio"), g("A", "obv_slope")
        famA = family_score("A", [
            sub_result("updown_volume_ratio", ind_updown_volume_ratio(inp.close, inp.volume, 20, pa1.d), pa1.sub_weight),
            sub_result("obv_slope", ind_obv_slope(inp.close, inp.volume, 20, pa2.d), pa2.sub_weight),
        ])
    else:
        hist = list(inp.line2_score_history.get(horizon) or [])
        series = (hist + [line2_today]) if len(hist) == 9 else [None] * 9 + [line2_today]
        famA = family_score("A", [sub_result("volume_scenario", ind_volume_scenario(
            inp.close, inp.volume, inp.high, inp.low, dd_n, horizon, series))])
    famB = family_score("B", [sub_result("close_position", ind_close_position(inp.high, inp.low, inp.close, cp_n))])
    famC = family_score("C", [sub_result("continuation", ind_continuation(inp.close, base_n, confirm_k))])
    return line_score("4", [famA, famB, famC], ps.family_weights[(SCOPE_STOCK, horizon, "4")])


# ---------------------------------------------------------------------------
# B2.5 五爻｜籌碼供需
# ---------------------------------------------------------------------------
def ind_net_strength(net, volume, n: int, d: float) -> Ind | Missing:
    """期間淨買超股數 ÷ 同期間成交股數 × 100 → S(0, d%)。"""
    x, v = _arr(net), _arr(volume)
    if x is None or v is None:
        return Missing(REASON_MISSING, "net/volume")
    if x.size < n or v.size < n:
        return Missing(REASON_INSUFFICIENT, f"window {n}")
    den = float(np.sum(v[-n:]))
    if den == 0:
        return Missing(REASON_DENOM_ZERO, "Σvolume=0")
    return S_clip(float(np.sum(x[-n:])) / den * 100.0, 0.0, d)


def ind_persistence(net, n: int, d: float) -> Ind | Missing:
    x = _arr(net)
    if x is None:
        return Missing(REASON_MISSING, "foreign_net")
    if x.size < n:
        return Missing(REASON_INSUFFICIENT, f"window {n}")
    return S_clip(int(np.sum(x[-n:] > 0)) - n / 2.0, 0.0, d)


def ind_margin_scenario(margin_balance, close, eligible: bool, n: int, d: float) -> Ind | Missing:
    """S1 §A2.1 有序情境（r＝融資餘額 n 日變化率 %；期間報酬＝close n 日報酬）。輸出原生值域即 S 值域、N 恆等。"""
    if not eligible:
        return Missing(REASON_NOT_ELIGIBLE, "無信用交易資格")
    m, c = _arr(margin_balance), _arr(close)
    if m is None or c is None:
        return Missing(REASON_MISSING, "margin_balance/close")
    if m.size < n + 1 or c.size < n + 1:
        return Missing(REASON_INSUFFICIENT, f"window {n}")
    base = float(m[-1 - n])
    if base == 0:
        return Missing(REASON_DENOM_ZERO, "margin base=0")
    r = (float(m[-1]) / base - 1.0) * 100.0
    ret = _pct_ret(c, n)
    if isinstance(ret, Missing):
        return ret
    if abs(r) < 0.5:
        return Ind(50.0, S_RANGE, r, meta={"seq": 1, "r": r})
    if r >= 0.5 and ret > 0:
        return Ind(50.0, S_RANGE, r, meta={"seq": 2, "r": r})
    s = S_clip(-r, 0.0, d)
    if r >= 0.5:
        return Ind(s.native, S_RANGE, r, s.clipped, {"seq": 3, "r": r})
    return Ind(50.0 + 0.5 * (s.native - 50.0), S_RANGE, r, s.clipped, {"seq": 4, "r": r})


def ind_short_sale_change(balance, shares_outstanding: float | None, n: int, d: float, direction: int = -1) -> Ind | Missing:
    b = _arr(balance)
    if b is None:
        return Missing(REASON_MISSING, "short_sale_balance")
    if shares_outstanding is None:
        return Missing(REASON_MISSING, "shares_outstanding")
    if shares_outstanding == 0:
        return Missing(REASON_DENOM_ZERO, "shares_outstanding=0")
    if b.size < n + 1:
        return Missing(REASON_INSUFFICIENT, f"window {n}")
    return S_clip((float(b[-1]) - float(b[-1 - n])) / shares_outstanding * 100.0, 0.0, d, direction)


def line5_chips(inp: StockInputs, ps: ParamSet, horizon: str) -> LineResult:
    ws, wl, wn = STK_L5_WIN[horizon]
    g = lambda fam, iid: ps.get(SCOPE_STOCK, horizon, "5", fam, iid)  # noqa: E731
    fams = []
    for fam, who, net in (("A", "foreign", inp.foreign_net_shares), ("B", "trust", inp.trust_net_shares)):
        pl, psh = g(fam, f"{who}_strength_long"), g(fam, f"{who}_strength_short")
        fams.append(family_score(fam, [
            sub_result(pl.indicator_id, ind_net_strength(net, inp.volume, wl, pl.d), pl.sub_weight),
            sub_result(psh.indicator_id, ind_net_strength(net, inp.volume, ws, psh.d), psh.sub_weight),
        ]))
    fams.append(family_score("C", [sub_result("foreign_persistence", ind_persistence(inp.foreign_net_shares, wn, g("C", "foreign_persistence").d))]))
    pdm = g("D", "margin_scenario")
    fams.append(family_score("D", [sub_result("margin_scenario", ind_margin_scenario(inp.margin_balance, inp.close, inp.margin_eligible, wn, pdm.d))]))
    pe = g("E", "short_sale_change")
    fams.append(family_score("E", [sub_result("short_sale_change", ind_short_sale_change(inp.short_sale_balance, inp.shares_outstanding, wn, pe.d, pe.direction))]))
    return line_score("5", fams, ps.family_weights[(SCOPE_STOCK, horizon, "5")])


# ---------------------------------------------------------------------------
# B2.6 上爻｜外部支持
# ---------------------------------------------------------------------------
def ind_market_direction(score: float | None) -> Ind | Missing:
    """大盤方向分數直接沿用（原生即 [7.30, 92.70]，恆等映射）。"""
    if score is None:
        return Missing(REASON_MISSING, "market direction score")
    return Ind(float(score), S_RANGE, float(score))


def ind_industry_relative(industry_median_ret: float | None, index_close, n: int, d: float) -> Ind | Missing:
    if industry_median_ret is None:
        return Missing(REASON_MISSING, "industry median return")
    i = _arr(index_close)
    if i is None:
        return Missing(REASON_MISSING, "index_close")
    r = _pct_ret(i, n)
    if isinstance(r, Missing):
        return r
    return S_clip(industry_median_ret - r, 0.0, d)


def ind_industry_above_ma20(ratio: float | None) -> Ind | Missing:
    if ratio is None:
        return Missing(REASON_MISSING, "industry_above_ma20_ratio")
    return L(float(ratio), 0.30, 0.50, 0.70)


def line6_external(inp: StockInputs, ps: ParamSet, horizon: str) -> LineResult:
    n = STK_L6_WIN[horizon]
    g = lambda fam, iid: ps.get(SCOPE_STOCK, horizon, "6", fam, iid)  # noqa: E731
    famA = family_score("A", [sub_result("market_direction", ind_market_direction(inp.market_direction_score.get(horizon)))])
    b1, b2 = g("B", "industry_relative_return"), g("B", "industry_above_ma20_ratio")
    famB = family_score("B", [
        sub_result("industry_relative_return", ind_industry_relative(inp.industry_median_return.get(n), inp.index_close, n, b1.d), b1.sub_weight),
        sub_result("industry_above_ma20_ratio", ind_industry_above_ma20(inp.industry_above_ma20_ratio), b2.sub_weight),
    ])
    return line_score("6", [famA, famB], ps.family_weights[(SCOPE_STOCK, horizon, "6")])


# ---------------------------------------------------------------------------
# 六爻 + 方向分數
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class StockScores:
    market: str
    stock_id: str
    horizon: str
    tpe_date: str
    lines: dict[str, LineResult]
    direction_score: float | Missing
    inner_trigram_score: float | Missing
    outer_trigram_score: float | Missing
    coverage: str

    def line_scores(self) -> list[float | None]:
        return [self.lines[k].score for k in ("1", "2", "3", "4", "5", "6")]


def score_stock(inp: StockInputs, ps: ParamSet, horizon: str) -> StockScores:
    if horizon not in HORIZONS:
        raise ValueError(f"horizon must be one of {HORIZONS}")
    if ps.market != inp.market:
        raise ValueError(f"ParamSet is for {ps.market}, inputs are for {inp.market} — 兩市場不得共用設定物件")
    l1 = line1_operations(inp, ps, horizon)
    l2 = line2_trend(inp, ps, horizon)
    l3 = line3_momentum(inp, ps, horizon)
    l4 = line4_volume_price(inp, ps, horizon, l2.score)
    l5 = line5_chips(inp, ps, horizon)
    l6 = line6_external(inp, ps, horizon)
    lines = {"1": l1, "2": l2, "3": l3, "4": l4, "5": l5, "6": l6}
    w = ps.line_weights[(SCOPE_STOCK, horizon)]
    return StockScores(inp.market, inp.stock_id, horizon, inp.tpe_date, lines, direction_score(lines, w),
                       trigram_mean(lines, ("1", "2", "3")), trigram_mean(lines, ("4", "5", "6")), coverage_label(lines))
