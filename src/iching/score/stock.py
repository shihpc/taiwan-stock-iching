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
from .params import HORIZONS, LINE2_SERIES_LEN, SCOPE_STOCK, ParamSet, Rules
from .transform import (Ind, L, Missing, REASON_DENOM_ZERO, REASON_INSUFFICIENT, REASON_MISSING,
                        REASON_NOT_ELIGIBLE, S_RANGE, S_clip, scenario, scenario_value_after_N)


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
    industry_above_ma_ratio: dict[int, float | None] = field(default_factory=dict)   # MA 窗長 → 產業內站上 MA_n 家數比
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


MONTHS_PER_YEAR = 12   # YoY＝對去年同月（曆法常數）


def revenue_yoy_3m(rev: dict[str, float], latest: str, offset_months: int = 0, months: int = 3) -> float | Missing:
    """近 `months`（3）月合計 ÷ 去年同期合計 − 1（×100 pp）。`offset_months` 往前平移（加速度的前一組用 `months`）。"""
    ms = [_ym_shift(latest, offset_months + i) for i in range(months)]
    ly = [_ym_shift(m, MONTHS_PER_YEAR) for m in ms]
    if any(m not in rev for m in ms + ly):
        return Missing(REASON_MISSING, "revenue months incomplete")
    num = sum(rev[m] for m in ms)
    den = sum(rev[m] for m in ly)
    if den == 0:
        return Missing(REASON_DENOM_ZERO, f"last-year {months}M sum=0")
    return (num / den - 1.0) * 100.0


def revenue_yoy_single(rev: dict[str, float], latest: str) -> float | Missing:
    return revenue_yoy_3m(rev, latest, 0, 1)


def revenue_is_12m_high(rev: dict[str, float], latest: str, months_n: int) -> bool | Missing:
    months = [_ym_shift(latest, i) for i in range(months_n)]
    if any(m not in rev for m in months):
        return Missing(REASON_MISSING, f"{months_n} months incomplete")
    return rev[latest] >= max(rev[m] for m in months)


def ind_revenue_yoy(rev, latest, months: int, d: float) -> Ind | Missing:
    """`months`＝Param.window：短線 1（最新單月 YoY）、波段／中期 3（三月合計 YoY）。"""
    x = revenue_yoy_3m(rev, latest, 0, months)
    return x if isinstance(x, Missing) else S_clip(x, 0.0, d)


def ind_revenue_accel(rev, latest, months: int, d: float) -> Ind | Missing:
    """近 `months` 月合計 YoY − 前一組 `months` 月合計 YoY（`months`＝Param.window，B2.1 為 3）。"""
    a, b = revenue_yoy_3m(rev, latest, 0, months), revenue_yoy_3m(rev, latest, months, months)
    if isinstance(a, Missing):
        return a
    if isinstance(b, Missing):
        return b
    return S_clip(a - b, 0.0, d)


def ind_eps(f: dict, d_yoy: float, d_diff: float, rules: Rules) -> tuple[str, Ind | Missing]:
    """前期（去年同期）EPS > `rules.eps_yoy_min_base`（0.1）→ 季 EPS YoY；否則 (本期 − 去年同期) ÷ 期末股價（替代指標，兩者不混尺度）。"""
    eps, eps_ly = f.get("eps"), f.get("eps_ly")
    if eps is None or eps_ly is None:
        return "eps_yoy", Missing(REASON_MISSING, "eps/eps_ly")
    if eps_ly > rules.eps_yoy_min_base:
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
    rules = ps.rules
    weights = ps.family_weights[(SCOPE_STOCK, horizon, "1")]
    revenue_high_floor = scenario_value_after_N(rules.revenue_high_floor_native)   # 90 → 84.16, ps.rules.unknown_below
    rev = {ym: float(v) for ym, v in (inp.monthly_revenue or [])}
    latest = max(rev) if rev else None
    if latest is None:
        miss = Missing(REASON_MISSING, "no monthly revenue")
        famA = family_score("A", [sub_result("revenue_yoy", miss), sub_result("revenue_accel", miss)])
    else:
        famA = family_score("A", [
            sub_result("revenue_yoy", ind_revenue_yoy(rev, latest, g("A", "revenue_yoy").window, g("A", "revenue_yoy").d)),
            sub_result("revenue_accel", ind_revenue_accel(rev, latest, g("A", "revenue_accel").window, g("A", "revenue_accel").d)),
        ])
        if horizon == "mid" and famA.score is not None:
            high = revenue_is_12m_high(rev, latest, g("A", "revenue_high_12m").window)
            if high is True and famA.score < revenue_high_floor:
                famA = dataclasses.replace(famA, score=revenue_high_floor, meta={**famA.meta, "revenue_high_12m": True, "floor_applied": True})
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
            iid, r = ind_eps(f, g("B", "eps_yoy").d, g("B", "eps_diff_over_price").d, rules)
            subs = [sub_result(iid, r), sub_result("gross_margin_qoq", ind_diff(f, "gross_margin", "gross_margin_prev_q", g("B", "gross_margin_qoq").d))]
        fams.append(family_score("B", subs, financial_rule=inp.is_financial))
        if latest is None:
            c = Missing(REASON_MISSING, "no monthly revenue")
        # SPEC-NOTE: B2.1 只寫「產業樣本不足」未給門檻；此處借用 B2.3 族 B 的「同產業有效樣本 < 5 檔 → 族缺」（rules.industry_min_sample）
        elif inp.industry_revenue_n is None or inp.industry_revenue_n < rules.industry_min_sample or inp.industry_median_3m_yoy is None:
            c = Missing(REASON_MISSING, f"industry sample < {rules.industry_min_sample} or median missing")
        else:
            pcv = g("C", "revenue_yoy_vs_industry")
            y = revenue_yoy_3m(rev, latest, 0, pcv.window)
            c = y if isinstance(y, Missing) else S_clip(y - inp.industry_median_3m_yoy, 0.0, pcv.d)
        fams.append(family_score("C", [sub_result("revenue_yoy_vs_industry", c)]))
    return line_score("1", fams, weights, ps.rules.unknown_below)


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


def ind_structure(high, low, k: int, window: int, rules: Rules) -> Ind | Missing:
    """已確認高低點結構：最近兩波峰兩波谷 HH＋HL→80、LH＋LL→20、其他→50（原生值，取自 `rules.structure_scores`）。
    # SPEC-NOTE: 波峰＝H_i 等於前後各 k 根視窗最高（平手亦計）；擺動點不足兩對 → 「其他」50。"""
    h, l = _arr(high), _arr(low)
    if h is None or l is None:
        return Missing(REASON_MISSING, "high/low")
    if h.size < 2 * k + 1:
        return Missing(REASON_INSUFFICIENT, f"structure needs {2 * k + 1}")
    peaks, troughs = swing_points(h, l, k, window)
    s_up, s_dn, s_other = rules.structure_scores
    if len(peaks) < 2 or len(troughs) < 2:
        return scenario(s_other, structure="insufficient_swings", n_peaks=len(peaks), n_troughs=len(troughs))
    p1, p2 = peaks[-2], peaks[-1]
    t1, t2 = troughs[-2], troughs[-1]
    hh, hl = h[p2] > h[p1], l[t2] > l[t1]
    lh, ll = h[p2] < h[p1], l[t2] < l[t1]
    if hh and hl:
        return scenario(s_up, structure="HH+HL")
    if lh and ll:
        return scenario(s_dn, structure="LH+LL")
    return scenario(s_other, structure="other")


def line2_trend(inp: StockInputs, ps: ParamSet, horizon: str) -> LineResult:
    g = lambda fam, iid: ps.get(SCOPE_STOCK, horizon, "2", fam, iid)  # noqa: E731
    pa_s, pa_l, pb, pc = g("A", "dist_ma_short"), g("A", "dist_ma_long"), g("B", "ma_long_slope"), g("C", "structure")
    ma_s, ma_l = pa_s.window, pa_l.window
    slope_ma, slope_n = pb.window
    struct_w, k = pc.window
    close, high, low = _arr(inp.close), _arr(inp.high), _arr(inp.low)
    if close is None:
        miss = Missing(REASON_MISSING, "close")
        fams = [family_score("A", [sub_result("dist_ma_short", miss), sub_result("dist_ma_long", miss)]),
                family_score("B", [sub_result("ma_long_slope", miss)]), family_score("C", [sub_result("structure", miss)])]
        return line_score("2", fams, ps.family_weights[(SCOPE_STOCK, horizon, "2")], ps.rules.unknown_below)
    atr = atr14_prev(high, low, close) if (high is not None and low is not None) else None
    famA = family_score("A", [
        sub_result("dist_ma_short", ind_ma_distance(close, atr, ma_s, pa_s.d)),
        sub_result("dist_ma_long", ind_ma_distance(close, atr, ma_l, pa_l.d)),
    ])
    famB = family_score("B", [sub_result("ma_long_slope", ind_ma_slope(close, atr, slope_ma, slope_n, pb.d))])
    famC = family_score("C", [sub_result("structure", ind_structure(high, low, k, struct_w, ps.rules))])
    return line_score("2", [famA, famB, famC], ps.family_weights[(SCOPE_STOCK, horizon, "2")], ps.rules.unknown_below, atr14_prev=atr)


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


def ind_excess_vs_industry(close, industry_median_ret: float | None, industry_n: int | None, n: int, d: float, rules: Rules) -> Ind | Missing:
    if industry_n is None or industry_n < rules.industry_min_sample:
        return Missing(REASON_MISSING, f"industry sample < {rules.industry_min_sample}")
    if industry_median_ret is None:
        return Missing(REASON_MISSING, "industry median return")
    c = _arr(close)
    if c is None:
        return Missing(REASON_MISSING, "close")
    r = _pct_ret(c, n)
    if isinstance(r, Missing):
        return r
    return S_clip(r - industry_median_ret, 0.0, d)


MA20_N = 20   # B2.3 過熱旗標／B2.4 序 1 的「MA20」與 VMA20_{t−1}（B2.0 符號定義，非校準對象）


def overheated(p_cs_long_excess: float | None, close, atr_prev, rules: Rules) -> bool | None:
    """過熱旗標（未截斷原值）：P_cs(長視窗超額) ≥ 95 且 (C − MA20)/ATR14 > 3（門檻取自 `Rules`）。任一不可得 → None。"""
    if p_cs_long_excess is None:
        return None
    ma20 = sma_last(close, MA20_N)
    if ma20 is None or atr_prev is None or atr_prev == 0:
        return None
    return p_cs_long_excess >= rules.p_cs_overheat and (float(as_f(close)[-1]) - ma20) / atr_prev > rules.overheat_dist_atr


def line3_momentum(inp: StockInputs, ps: ParamSet, horizon: str) -> LineResult:
    g = lambda fam, iid: ps.get(SCOPE_STOCK, horizon, "3", fam, iid)  # noqa: E731
    pl, psh, pb, pc = g("A", "excess_long"), g("A", "excess_short"), g("B", "excess_vs_industry"), g("C", "excess_accel")
    famA = family_score("A", [
        sub_result("excess_long", ind_excess(inp.close, inp.index_close, pl.window, pl.d)),
        sub_result("excess_short", ind_excess(inp.close, inp.index_close, psh.window, psh.d)),
    ])
    famB = family_score("B", [sub_result("excess_vs_industry", ind_excess_vs_industry(
        inp.close, inp.industry_median_return.get(pb.window), inp.industry_n, pb.window, pb.d, ps.rules))])
    famC = family_score("C", [sub_result("excess_accel", ind_excess_accel(inp.close, inp.index_close, pc.window, pc.d))])
    lr = line_score("3", [famA, famB, famC], ps.family_weights[(SCOPE_STOCK, horizon, "3")], ps.rules.unknown_below)
    atr = atr14_prev(inp.high, inp.low, inp.close) if (inp.high is not None and inp.low is not None and inp.close is not None) else None
    hot = overheated(inp.p_cs_long_excess, inp.close, atr, ps.rules) if inp.close is not None else None
    cap = scenario_value_after_N(ps.rules.overheat_cap_native)   # 85 → 79.89
    meta = {**lr.meta, "overheated": hot, "p_cs_long_excess": inp.p_cs_long_excess}
    if hot and lr.score is not None and lr.score > cap:
        return dataclasses.replace(lr, score=cap, meta={**meta, "overheat_cap_applied": True})
    return dataclasses.replace(lr, meta=meta)


# ---------------------------------------------------------------------------
# B2.4 四爻｜量價確認
# ---------------------------------------------------------------------------
def volume_scenario_day(close, volume, atr_prev_series, i: int, n_dd: int, line2_score: float | None, rules: Rules) -> float | Missing:
    """B2.4 族 A 情境表在第 i 日的**原生**分數（有序 if–elif，未截斷原值；門檻與分數取自 `Rules`）。"""
    c, v = as_f(close), as_f(volume)
    if i < MA20_N or i < n_dd - 1:
        return Missing(REASON_INSUFFICIENT, "VMA20/drawdown window")
    atr = atr_prev_series[i]
    vma = float(np.mean(v[i - MA20_N:i]))
    if v[i] == 0:
        return Missing(REASON_MISSING, "no trade")
    if np.isnan(atr) or atr == 0 or vma == 0:
        return Missing(REASON_DENOM_ZERO, "ATR14/VMA20 missing or zero")
    vr = v[i] / vma
    dchg = (c[i] - c[i - 1]) / atr
    dd = (float(np.max(c[i - n_dd + 1:i + 1])) - c[i]) / atr
    ma20 = float(np.mean(c[i - MA20_N + 1:i + 1]))
    s_pullback, s_up_base, s_down_base, s_neutral = rules.vs_scores
    seq1_rest = (0 < dd <= rules.vs_drawdown_max) and vr < rules.vs_low_ratio and c[i] >= ma20
    if seq1_rest:
        if line2_score is None:
            return Missing(REASON_MISSING, "line2 score needed for seq 1")
        if line2_score >= rules.vs_line2_min:
            return s_pullback
    s = S_clip(vr - 1.0, rules.vs_ratio_c, rules.vs_ratio_d).native
    if dchg >= rules.vs_day_change and vr >= rules.vs_high_ratio:
        return s_up_base + rules.vs_formula_half * (s - rules.hysteresis_first)
    if dchg <= -rules.vs_day_change and vr >= rules.vs_high_ratio:
        return s_down_base - rules.vs_formula_half * (s - rules.hysteresis_first)
    return s_neutral


def ind_volume_scenario(close, volume, high, low, n_dd: int, horizon: str, line2_series: Sequence[float | None],
                        rules: Rules) -> Ind | Missing:
    """短線：0.6×當日 + 0.4×近 5 日（含當日）平均；波段：近 10 日平均。`line2_series`＝T−9…T（長度 10）。
    # SPEC-NOTE: 多日平均取「可得日」平均；短線當日缺 → 族缺；全缺 → 族缺。"""
    c, v, h, l = _arr(close), _arr(volume), _arr(high), _arr(low)
    if c is None or v is None or h is None or l is None:
        return Missing(REASON_MISSING, "ohlcv")
    if not (c.size == v.size == h.size == l.size):
        return Missing(REASON_MISSING, "ohlcv length mismatch")
    atrs = atr_series_prev(h, l, c)
    n = c.size
    days = rules.vs_avg_days_short if horizon == "short" else rules.vs_avg_days_swing
    if n < MA20_N + 1 + days:
        return Missing(REASON_INSUFFICIENT, "scenario history")
    l2 = list(line2_series)
    if len(l2) != LINE2_SERIES_LEN:
        return Missing(REASON_MISSING, f"line2 series must be T-{LINE2_SERIES_LEN - 1}..T ({LINE2_SERIES_LEN})")
    vals = []
    for j in range(days):
        i = n - 1 - j
        r = volume_scenario_day(c, v, atrs, i, n_dd, l2[LINE2_SERIES_LEN - 1 - j], rules)
        vals.append(r)
    today = vals[0]
    avail = [x for x in vals if not isinstance(x, Missing)]
    if horizon == "short":
        if isinstance(today, Missing):
            return today
        w = rules.vs_today_weight
        return scenario(w * today + (1.0 - w) * (sum(avail) / len(avail)), today=today, n_avail=len(avail))
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
    if c.size < max(n, MA20_N + 1):
        return Missing(REASON_INSUFFICIENT, "OBV/VMA20 window")
    vma = float(np.mean(v[-MA20_N - 1:-1]))
    if vma == 0:
        return Missing(REASON_DENOM_ZERO, "VMA20=0")
    slope = ols_slope(obv(c, v)[-n:])
    if slope is None:
        return Missing(REASON_INSUFFICIENT, "slope")
    return S_clip(slope / vma, 0.0, d)


def ind_close_position(high, low, close, n: int, anchors: tuple[float, float, float]) -> Ind | Missing:
    """n 日均 (C−L)/(H−L) → L(anchors＝Param.anchors (0, 0.5, 1))，H=L（一價成交）日不計入；全部不計入 → denominator_zero。"""
    h, l, c = _arr(high), _arr(low), _arr(close)
    if h is None or l is None or c is None:
        return Missing(REASON_MISSING, "hlc")
    if c.size < n:
        return Missing(REASON_INSUFFICIENT, f"window {n}")
    hh, ll, cc = h[-n:], l[-n:], c[-n:]
    m = hh != ll
    if not m.any():
        return Missing(REASON_DENOM_ZERO, "all H=L")
    return L(float(np.mean((cc[m] - ll[m]) / (hh[m] - ll[m]))), *anchors)


def ind_continuation(close, base_n: int, k: int, rules: Rules) -> Ind | Missing:
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
    s_held, s_unrecovered, s_other = rules.continuation_scores
    if not events:
        return scenario(s_other, continuation="no_event")
    b, kind, level = events[-1]
    if b + k > T:
        return scenario(s_other, continuation="pending", event_day_offset=T - b)
    confirm = c[b + k]
    if kind == "up":
        return scenario(s_held, continuation="breakout_held", level=level) if confirm >= level else scenario(s_other, continuation="breakout_failed", level=level)
    return scenario(s_unrecovered, continuation="breakdown_unrecovered", level=level) if confirm < level else scenario(s_other, continuation="breakdown_recovered", level=level)


def line4_volume_price(inp: StockInputs, ps: ParamSet, horizon: str, line2_today: float | None) -> LineResult:
    g = lambda fam, iid: ps.get(SCOPE_STOCK, horizon, "4", fam, iid)  # noqa: E731
    pb, pc = g("B", "close_position"), g("C", "continuation")
    base_n, confirm_k = pc.window
    if horizon == "mid":
        pa1, pa2 = g("A", "updown_volume_ratio"), g("A", "obv_slope")
        famA = family_score("A", [
            sub_result("updown_volume_ratio", ind_updown_volume_ratio(inp.close, inp.volume, pa1.window, pa1.d), pa1.sub_weight),
            sub_result("obv_slope", ind_obv_slope(inp.close, inp.volume, pa2.window, pa2.d), pa2.sub_weight),
        ])
    else:
        pa = g("A", "volume_scenario")
        hist = list(inp.line2_score_history.get(horizon) or [])
        hlen = LINE2_SERIES_LEN - 1
        series = (hist + [line2_today]) if len(hist) == hlen else [None] * hlen + [line2_today]
        famA = family_score("A", [sub_result("volume_scenario", ind_volume_scenario(
            inp.close, inp.volume, inp.high, inp.low, pa.window, horizon, series, ps.rules))])
    famB = family_score("B", [sub_result("close_position", ind_close_position(inp.high, inp.low, inp.close, pb.window, pb.anchors))])
    famC = family_score("C", [sub_result("continuation", ind_continuation(inp.close, base_n, confirm_k, ps.rules))])
    return line_score("4", [famA, famB, famC], ps.family_weights[(SCOPE_STOCK, horizon, "4")], ps.rules.unknown_below)


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
    if np.isnan(x[-n:]).any() or np.isnan(v[-n:]).any():
        return Missing(REASON_MISSING, "NaN in window")
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


def ind_margin_scenario(margin_balance, close, eligible: bool, n: int, c_: float, d: float, rules: Rules) -> Ind | Missing:
    """S1 §A2.1 有序情境（r＝融資餘額 n 日變化率 %；期間報酬＝close n 日報酬；近零門檻 `rules.margin_near_zero_pct`）。
    輸出原生值域即 S 值域、N 恆等；序 1／2 的 50 是中性點（`rules.hysteresis_first`）。"""
    if not eligible:
        return Missing(REASON_NOT_ELIGIBLE, "無信用交易資格")
    m, c = _arr(margin_balance), _arr(close)
    if m is None or c is None:
        return Missing(REASON_MISSING, "margin_balance/close")
    if m.size < n + 1 or c.size < n + 1:
        return Missing(REASON_INSUFFICIENT, f"window {n}")
    if np.isnan(m[-1 - n:]).any():
        return Missing(REASON_MISSING, "margin balance NaN in window")
    base, cur = float(m[-1 - n]), float(m[-1])
    if base == 0:
        return Missing(REASON_DENOM_ZERO, "margin base=0")
    r = (cur / base - 1.0) * 100.0
    ret = _pct_ret(c, n)
    if isinstance(ret, Missing):
        return ret
    mid, z = rules.hysteresis_first, rules.margin_near_zero_pct
    if abs(r) < z:
        return Ind(mid, S_RANGE, r, meta={"seq": 1, "r": r})
    if r >= z and ret > 0:
        return Ind(mid, S_RANGE, r, meta={"seq": 2, "r": r})
    s = S_clip(-r, c_, d)
    if r >= z:
        return Ind(s.native, S_RANGE, r, s.clipped, {"seq": 3, "r": r})
    return Ind(mid + rules.margin_half * (s.native - mid), S_RANGE, r, s.clipped, {"seq": 4, "r": r})


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
    if np.isnan(b[-1 - n:]).any():
        return Missing(REASON_MISSING, "short sale balance NaN in window")
    cur, base = float(b[-1]), float(b[-1 - n])
    return S_clip((cur - base) / shares_outstanding * 100.0, 0.0, d, direction)


def line5_chips(inp: StockInputs, ps: ParamSet, horizon: str) -> LineResult:
    g = lambda fam, iid: ps.get(SCOPE_STOCK, horizon, "5", fam, iid)  # noqa: E731
    fams = []
    for fam, who, net in (("A", "foreign", inp.foreign_net_shares), ("B", "trust", inp.trust_net_shares)):
        pl, psh = g(fam, f"{who}_strength_long"), g(fam, f"{who}_strength_short")
        fams.append(family_score(fam, [
            sub_result(pl.indicator_id, ind_net_strength(net, inp.volume, pl.window, pl.d), pl.sub_weight),
            sub_result(psh.indicator_id, ind_net_strength(net, inp.volume, psh.window, psh.d), psh.sub_weight),
        ]))
    pcp = g("C", "foreign_persistence")
    fams.append(family_score("C", [sub_result("foreign_persistence", ind_persistence(inp.foreign_net_shares, pcp.window, pcp.d))]))
    pdm = g("D", "margin_scenario")
    fams.append(family_score("D", [sub_result("margin_scenario", ind_margin_scenario(inp.margin_balance, inp.close, inp.margin_eligible, pdm.window, pdm.c, pdm.d, ps.rules))]))
    pe = g("E", "short_sale_change")
    fams.append(family_score("E", [sub_result("short_sale_change", ind_short_sale_change(inp.short_sale_balance, inp.shares_outstanding, pe.window, pe.d, pe.direction))]))
    return line_score("5", fams, ps.family_weights[(SCOPE_STOCK, horizon, "5")], ps.rules.unknown_below)


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


def ind_industry_above_ma20(ratio: float | None, anchors: tuple[float, float, float]) -> Ind | Missing:
    if ratio is None:
        return Missing(REASON_MISSING, "industry_above_ma_ratio[window]")
    return L(float(ratio), *anchors)


def line6_external(inp: StockInputs, ps: ParamSet, horizon: str) -> LineResult:
    g = lambda fam, iid: ps.get(SCOPE_STOCK, horizon, "6", fam, iid)  # noqa: E731
    famA = family_score("A", [sub_result("market_direction", ind_market_direction(inp.market_direction_score.get(horizon)))])
    b1, b2 = g("B", "industry_relative_return"), g("B", "industry_above_ma20_ratio")
    n = b1.window
    famB = family_score("B", [
        sub_result("industry_relative_return", ind_industry_relative(inp.industry_median_return.get(n), inp.index_close, n, b1.d), b1.sub_weight),
        sub_result("industry_above_ma20_ratio", ind_industry_above_ma20(inp.industry_above_ma_ratio.get(b2.window), b2.anchors), b2.sub_weight),
    ])
    return line_score("6", [famA, famB], ps.family_weights[(SCOPE_STOCK, horizon, "6")], ps.rules.unknown_below)


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
