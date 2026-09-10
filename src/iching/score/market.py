"""大盤六爻（P1-B1 §B1.1–B1.6）＋三期間權重（§B1.7）＋旗標（§B1.8）。純函式：只吃 `MarketInputs`（dict／numpy），不碰 DB。

慣例：所有台北序列升冪、最後一筆＝T；上爻序列以**美股交易日**索引（`us_dates`），對齊走
`iching.calendar.us_session_closed_by()`（唯一實作，不另寫）。缺值一律回 `Missing`，不會變 50。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Sequence

import numpy as np

from ..calendar import us_session_closed_by
from .aggregate import (FamilyResult, LineResult, coverage_label, direction_score, family_score, line_score,
                        sub_result, trigram_mean)
from .indicators import ATR_N, as_f, atr14_prev, atr_series_prev, ma_change, sma_at, sma_last, sma_series
from .params import HORIZONS, SCOPE_MARKET, ParamSet, Rules
from .transform import (Ind, L, Missing, P_hist, REASON_CONTRACT_ROLLED, REASON_DENOM_ZERO, REASON_INSUFFICIENT,
                        REASON_MISSING, REASON_STALE, S_clip, percentile_threshold, scenario)


@dataclass
class MarketInputs:
    """大盤計分輸入（B3.1 #1／#2／#3／#8／#10／#11 的大盤部分）。None＝該來源缺（→ 對應族缺值）。"""
    market: str
    tpe_date: str
    tpe_dates: Sequence[str] | None = None            # 台北交易日曆（升冪、含 T），上爻 stale_days 用
    # 指數（TaiwanStockPrice data_id=TAIEX/TPEx）
    index_open: Any = None
    index_high: Any = None
    index_low: Any = None
    index_close: Any = None
    amount: Any = None                                 # 市場成交金額 AMT（FMTQIK／tradingIndex）
    # 廣度（全市場切片聚合；分子分母同一份 PIT 名單）
    n_stocks: Any = None                               # N_t
    above_ma_ratio: dict[int, Any] = field(default_factory=dict)   # {5,10,20,60} → 站上 MA_n 家數 ÷ N
    advance_ratio: Any = None                          # 上漲家數 ÷ N
    new_high_low_ratio: dict[int, Any] = field(default_factory=dict)   # {10,20,60} → (n 日新高 − 新低) ÷ N
    ad_line: Any = None                                # 騰落線（Σ(上漲 − 下跌家數)，原始家數）
    up_amount_ratio: Any = None                        # 上漲股成交金額 ÷ 總成交金額
    # 現貨資金（官方口徑 BFI82U／TPEx summary；融資餘額）
    foreign_net_amount: Any = None
    trust_net_amount: Any = None
    margin_balance: Any = None
    # 衍生品
    foreign_net_oi: Any = None                         # 外資台指期淨未平倉口數
    basis: Any = None                                  # (近月期指 − 現貨)/現貨 × 100
    contract_rolled: bool = False
    vix: Any = None
    put_call_ratio: float | None = None                # 只顯示
    # 外部（美股曆）
    us_dates: Sequence[str] | None = None
    spx_high: Any = None
    spx_low: Any = None
    spx_close: Any = None
    sox_close: Any = None
    fx_dates: Sequence[str] | None = None
    fx_usdtwd: Any = None
    # 跨日相依（B3.1 #11）
    line2_score_t_minus_5: dict[str, float | None] = field(default_factory=dict)   # horizon → 二爻分數(T−5)
    # 旗標 F-分歧 的另一市場基本狀態（需正式爻態；None＝不可得）
    own_state: str | None = None
    other_market_state: str | None = None


def _arr(a) -> np.ndarray | None:
    if a is None:
        return None
    arr = as_f(a)
    return arr if arr.size else None


# ---------------------------------------------------------------------------
# B1.1 初爻｜趨勢
# ---------------------------------------------------------------------------
def ind_index_ma_distance(close, atr_prev: float | None, n_ma: int, d: float) -> Ind | Missing:
    """(I − MA_n) ÷ ATR14_{t−1} → S(0, d)。"""
    ma = sma_last(close, n_ma)
    if ma is None:
        return Missing(REASON_INSUFFICIENT, f"MA{n_ma}")
    if atr_prev is None:
        return Missing(REASON_INSUFFICIENT, "ATR14")
    if atr_prev == 0:
        return Missing(REASON_DENOM_ZERO, "ATR14=0")
    x = (float(as_f(close)[-1]) - ma) / atr_prev
    return S_clip(x, 0.0, d)


def ind_index_ma_slope(close, atr_prev: float | None, n_ma: int, n_change: int, d: float) -> Ind | Missing:
    """MA_{n_ma} n_change 日變化 ÷ ATR14_{t−1} → S(0, d)。大盤恆為 MA20。"""
    chg = ma_change(close, n_ma, n_change)
    if chg is None:
        return Missing(REASON_INSUFFICIENT, f"MA{n_ma} change {n_change}")
    if atr_prev is None:
        return Missing(REASON_INSUFFICIENT, "ATR14")
    if atr_prev == 0:
        return Missing(REASON_DENOM_ZERO, "ATR14=0")
    return S_clip(chg / atr_prev, 0.0, d)


def ind_range_position(close, n: int, anchors: tuple[float, float, float]) -> Ind | Missing:
    """(I − min_n)/(max_n − min_n) → L(anchors＝Param.anchors (0, 0.5, 1))。視窗含 T。"""
    c = as_f(close)
    if c.size < n:
        return Missing(REASON_INSUFFICIENT, f"range {n}")
    w = c[-n:]
    lo, hi = float(w.min()), float(w.max())
    if hi == lo:
        return Missing(REASON_DENOM_ZERO, "max=min")
    return L((float(c[-1]) - lo) / (hi - lo), *anchors)


def line1_trend(inp: MarketInputs, ps: ParamSet, horizon: str) -> LineResult:
    g = lambda fam, iid: ps.get(SCOPE_MARKET, horizon, "1", fam, iid)  # noqa: E731
    pa_s, pa_l, pb, pc = g("A", "dist_ma_short"), g("A", "dist_ma_long"), g("B", "ma20_slope"), g("C", "range_position")
    close, high, low = _arr(inp.index_close), _arr(inp.index_high), _arr(inp.index_low)
    if close is None:
        miss = Missing(REASON_MISSING, "index_close")
        fams = [family_score(f, [sub_result(i, miss)]) for f, i in (("A", "dist_ma_short"), ("B", "ma20_slope"), ("C", "range_position"))]
        return line_score("1", fams, ps.family_weights[(SCOPE_MARKET, horizon, "1")], ps.rules.unknown_below)
    atr = atr14_prev(high, low, close) if (high is not None and low is not None) else None
    famA = family_score("A", [
        sub_result("dist_ma_short", ind_index_ma_distance(close, atr, pa_s.window, pa_s.d)),
        sub_result("dist_ma_long", ind_index_ma_distance(close, atr, pa_l.window, pa_l.d)),
    ])
    ma_n, slope_n = pb.window
    famB = family_score("B", [sub_result("ma20_slope", ind_index_ma_slope(close, atr, ma_n, slope_n, pb.d))])
    famC = family_score("C", [sub_result("range_position", ind_range_position(close, pc.window, pc.anchors))])
    return line_score("1", [famA, famB, famC], ps.family_weights[(SCOPE_MARKET, horizon, "1")], ps.rules.unknown_below, atr14_prev=atr)


# ---------------------------------------------------------------------------
# B1.2 二爻｜廣度
# ---------------------------------------------------------------------------
def ind_ratio_L(series, n_avg: int, anchors: tuple[float, float, float]) -> Ind | Missing:
    """家數比（n_avg 日平均，n_avg=1 即當日）→ L(anchors)。"""
    a = _arr(series)
    if a is None:
        return Missing(REASON_MISSING, "series")
    v = sma_last(a, n_avg)
    if v is None:
        return Missing(REASON_INSUFFICIENT, f"avg {n_avg}")
    return L(v, *anchors)


def ind_new_high_low(series, d: float) -> Ind | Missing:
    """(n 日新高家數 − n 日新低家數) ÷ N × 100 → S(0, d%)。輸入序列已是比值（−1..1）。"""
    a = _arr(series)
    if a is None:
        return Missing(REASON_MISSING, "new_high_low_ratio")
    return S_clip(float(a[-1]) * 100.0, 0.0, d)


def ind_ad_line_dev(ad_line, n_stocks, n: int, d: float) -> Ind | Missing:
    """騰落線偏離：dev_t ＝ (AD_t − MA_n(AD)_t) ÷ N_t，x ＝ dev_t ÷ std_n(dev) → S(0, d)。
    # SPEC-NOTE: 規格原文「(AD線 − AD線MA_n) ÷ N 的 n 日標準差」語法有歧義；依同節 R5g 註「分母是自身的 n 日標準差，
    #   屬自我標準化量」讀作：先以 N 正規化的偏離量，再除以該偏離量自身的 n 日標準差（母體標準差 ddof=0）。
    #   需 AD 長度 ≥ 2n−1；std=0 → denominator_zero。"""
    ad, nn = _arr(ad_line), _arr(n_stocks)
    if ad is None or nn is None:
        return Missing(REASON_MISSING, "ad_line/n_stocks")
    if ad.size != nn.size:
        return Missing(REASON_MISSING, "ad_line/n_stocks length mismatch")
    if ad.size < 2 * n - 1:
        return Missing(REASON_INSUFFICIENT, f"AD needs {2 * n - 1}")
    ma = sma_series(ad, n)
    dev = (ad - ma) / np.where(nn == 0, np.nan, nn)
    w = dev[-n:]
    if np.isnan(w).any():
        return Missing(REASON_DENOM_ZERO, "N=0 in window")
    sd = float(np.std(w))
    if sd == 0:
        return Missing(REASON_DENOM_ZERO, "std=0")
    return S_clip(float(w[-1]) / sd, 0.0, d)


def line2_breadth(inp: MarketInputs, ps: ParamSet, horizon: str) -> LineResult:
    g = lambda fam, iid: ps.get(SCOPE_MARKET, horizon, "2", fam, iid)  # noqa: E731
    pa_s, pa_l, pb, pc, pd_ = g("A", "above_ma_short_ratio"), g("A", "above_ma_long_ratio"), g("B", "advance_ratio"), g("C", "new_high_low_ratio"), g("D", "ad_line_dev")
    famA = family_score("A", [
        sub_result("above_ma_short_ratio", ind_ratio_L(inp.above_ma_ratio.get(pa_s.window), 1, pa_s.anchors)),
        sub_result("above_ma_long_ratio", ind_ratio_L(inp.above_ma_ratio.get(pa_l.window), 1, pa_l.anchors)),
    ])
    famB = family_score("B", [sub_result("advance_ratio", ind_ratio_L(inp.advance_ratio, pb.window, pb.anchors))])
    famC = family_score("C", [sub_result("new_high_low_ratio", ind_new_high_low(inp.new_high_low_ratio.get(pc.window), pc.d))])
    famD = family_score("D", [sub_result("ad_line_dev", ind_ad_line_dev(inp.ad_line, inp.n_stocks, pd_.window, pd_.d))])
    return line_score("2", [famA, famB, famC, famD], ps.family_weights[(SCOPE_MARKET, horizon, "2")], ps.rules.unknown_below)


# ---------------------------------------------------------------------------
# B1.3 三爻｜量價參與
# ---------------------------------------------------------------------------
def ind_amount_ratio(amount, num_n: int, den_n: int, c: float, d: float) -> Ind | Missing:
    """AMT 分子（num_n 日均、含 T）÷ AMTMA_den_n（**取 T−1 為止**，B1.0）→ S(c, d)。
    # SPEC-NOTE: B1.0 只定義了 `AMTMA20_{t−1}`；波段分母 AMTMA20 與中期分母 AMTMA60 一律比照「取 T−1 為止」，
    #   分子（當日／5 日均／20 日均）含 T。B1.3 對 AMTMA60 的取法未另定。"""
    a = _arr(amount)
    if a is None:
        return Missing(REASON_MISSING, "amount")
    num = sma_at(a, num_n, 0)
    den = sma_at(a, den_n, 1)
    if num is None or den is None:
        return Missing(REASON_INSUFFICIENT, f"amount {num_n}/{den_n}")
    if den == 0:
        return Missing(REASON_DENOM_ZERO, "AMTMA=0")
    return S_clip(num / den, c, d)


def ind_divergence_scenario(close, amount, n: int, rules: Rules) -> Ind | Missing:
    """B1.3 族 C 有序情境（未截斷原值）：n 日新高 ∧ AMT<AMTMA_n → 35；新低 ∧ AMT ≥ 1.5×AMTMA_n → 25；
    新低 ∧ AMT < 0.8×AMTMA_n → 55；其他 50（倍數與分數取自 `Rules`）。AMTMA_n 取 T−1 為止；max=min → denominator_zero。"""
    c, a = _arr(close), _arr(amount)
    if c is None or a is None:
        return Missing(REASON_MISSING, "close/amount")
    if c.size < n:
        return Missing(REASON_INSUFFICIENT, f"close {n}")
    ma = sma_at(a, n, 1)
    if ma is None:
        return Missing(REASON_INSUFFICIENT, f"AMTMA{n}")
    w = c[-n:]
    hi, lo = float(w.max()), float(w.min())
    if hi == lo:
        return Missing(REASON_DENOM_ZERO, "max=min")
    cur, amt = float(c[-1]), float(a[-1])
    new_high, new_low = cur >= hi, cur <= lo
    s1, s2, s3, s4 = rules.divergence_scores
    if new_high and amt < ma:
        return scenario(s1, seq=1)
    if new_low and amt >= rules.divergence_high_amt_mult * ma:
        return scenario(s2, seq=2)
    if new_low and amt < rules.divergence_low_amt_mult * ma:
        return scenario(s3, seq=3)
    return scenario(s4, seq=4)


def line3_participation(inp: MarketInputs, ps: ParamSet, horizon: str) -> LineResult:
    g = lambda fam, iid: ps.get(SCOPE_MARKET, horizon, "3", fam, iid)  # noqa: E731
    pa, pb, pc = g("A", "amount_ratio"), g("B", "up_amount_ratio"), g("C", "divergence_scenario")
    num_n, den_n = pa.window
    famA = family_score("A", [sub_result("amount_ratio", ind_amount_ratio(inp.amount, num_n, den_n, pa.c, pa.d))])
    famB = family_score("B", [sub_result("up_amount_ratio", ind_ratio_L(inp.up_amount_ratio, pb.window, pb.anchors))])
    famC = family_score("C", [sub_result("divergence_scenario", ind_divergence_scenario(inp.index_close, inp.amount, pc.window, ps.rules))])
    return line_score("3", [famA, famB, famC], ps.family_weights[(SCOPE_MARKET, horizon, "3")], ps.rules.unknown_below)


# ---------------------------------------------------------------------------
# B1.4 四爻｜現貨資金
# ---------------------------------------------------------------------------
def ind_net_amount_ratio(net_amount, amount, n: int, d: float) -> Ind | Missing:
    """Σ_{n} 淨買超金額 ÷ Σ_{n} 市場成交金額 × 100 → S(0, d%)。"""
    x, a = _arr(net_amount), _arr(amount)
    if x is None or a is None:
        return Missing(REASON_MISSING, "net_amount/amount")
    if x.size < n or a.size < n:
        return Missing(REASON_INSUFFICIENT, f"window {n}")
    den = float(np.sum(a[-n:]))
    if den == 0:
        return Missing(REASON_DENOM_ZERO, "Σamount=0")
    return S_clip(float(np.sum(x[-n:])) / den * 100.0, 0.0, d)


def ind_buy_days(net, n: int, d: float) -> Ind | Missing:
    """近 n 日買超天數（淨買 > 0）− n/2 → S(0, d)。"""
    x = _arr(net)
    if x is None:
        return Missing(REASON_MISSING, "net")
    if x.size < n:
        return Missing(REASON_INSUFFICIENT, f"window {n}")
    days = int(np.sum(x[-n:] > 0))
    return S_clip(days - n / 2.0, 0.0, d)


def ind_balance_change(balance, n: int, d: float, direction: int = -1) -> Ind | Missing:
    """餘額 n 日變化率 × 100 → S(0, d%)（預設反向）。"""
    b = _arr(balance)
    if b is None:
        return Missing(REASON_MISSING, "balance")
    if b.size < n + 1:
        return Missing(REASON_INSUFFICIENT, f"window {n}")
    if np.isnan(b[-1 - n:]).any():
        return Missing(REASON_MISSING, "balance NaN in window")
    base, cur = float(b[-1 - n]), float(b[-1])
    if base == 0:
        return Missing(REASON_DENOM_ZERO, "base=0")
    return S_clip((cur / base - 1.0) * 100.0, 0.0, d, direction)


def line4_spot_flow(inp: MarketInputs, ps: ParamSet, horizon: str) -> LineResult:
    g = lambda fam, iid: ps.get(SCOPE_MARKET, horizon, "4", fam, iid)  # noqa: E731
    pa, pb, pc, pd_ = g("A", "foreign_net_ratio"), g("B", "trust_net_ratio"), g("C", "foreign_buy_days"), g("D", "margin_change")
    famA = family_score("A", [sub_result("foreign_net_ratio", ind_net_amount_ratio(inp.foreign_net_amount, inp.amount, pa.window, pa.d))])
    famB = family_score("B", [sub_result("trust_net_ratio", ind_net_amount_ratio(inp.trust_net_amount, inp.amount, pb.window, pb.d))])
    famC = family_score("C", [sub_result("foreign_buy_days", ind_buy_days(inp.foreign_net_amount, pc.window, pc.d))])
    famD = family_score("D", [sub_result("margin_change", ind_balance_change(inp.margin_balance, pd_.window, pd_.d, pd_.direction))])
    return line_score("4", [famA, famB, famC, famD], ps.family_weights[(SCOPE_MARKET, horizon, "4")], ps.rules.unknown_below)


# ---------------------------------------------------------------------------
# B1.5 五爻｜衍生品
# ---------------------------------------------------------------------------
def ind_oi_phist(net_oi, n: int = 250) -> Ind | Missing:
    x = _arr(net_oi)
    if x is None:
        return Missing(REASON_MISSING, "foreign_net_oi")
    return P_hist(x, n)


def ind_oi_change(net_oi, n: int, d: float) -> Ind | Missing:
    x = _arr(net_oi)
    if x is None:
        return Missing(REASON_MISSING, "foreign_net_oi")
    if x.size < n + 1:
        return Missing(REASON_INSUFFICIENT, f"window {n}")
    return S_clip(float(x[-1] - x[-1 - n]), 0.0, d)


def ind_basis(basis, contract_rolled: bool, d: float, median_n: int = 60) -> Ind | Missing:
    """基差 → S(c＝近 60 日中位數（含 T）, d)。換月日 → contract_rolled 缺值（B1.5.1）。
    # SPEC-NOTE: 「近 60 交易日」視窗含當日；不足 60 筆 → insufficient_history。"""
    if contract_rolled:
        return Missing(REASON_CONTRACT_ROLLED, "換月日")
    b = _arr(basis)
    if b is None:
        return Missing(REASON_MISSING, "basis")
    if b.size < median_n:
        return Missing(REASON_INSUFFICIENT, f"basis {median_n}")
    c = float(np.median(b[-median_n:]))
    out = S_clip(float(b[-1]), c, d)
    return Ind(out.native, out.native_range, out.x, out.clipped, {"c_rolling_median": c})


def ind_vix_rev(vix, n: int = 250) -> Ind | Missing:
    """100 − P_hist(250)：反向在原生尺度做完再套 N（政策第 6 點）；方向欄不再取負。"""
    x = _arr(vix)
    if x is None:
        return Missing(REASON_MISSING, "vix")
    p = P_hist(x, n)
    if isinstance(p, Missing):
        return p
    return Ind(100.0 - p.native, p.native_range, p.x)


def line5_derivatives(inp: MarketInputs, ps: ParamSet, horizon: str) -> LineResult:
    g = lambda fam, iid: ps.get(SCOPE_MARKET, horizon, "5", fam, iid)  # noqa: E731
    a1, a2 = g("A", "foreign_net_oi_phist"), g("A", "foreign_net_oi_change")
    famA = family_score("A", [
        sub_result("foreign_net_oi_phist", ind_oi_phist(inp.foreign_net_oi, a1.window), a1.sub_weight),
        sub_result("foreign_net_oi_change", ind_oi_change(inp.foreign_net_oi, a2.window, a2.d), a2.sub_weight),
    ])
    famB = family_score("B", [sub_result("basis", ind_basis(inp.basis, inp.contract_rolled, g("B", "basis").d, g("B", "basis").window))])
    famC = family_score("C", [sub_result("vix_phist_rev", ind_vix_rev(inp.vix, g("C", "vix_phist_rev").window))])
    return line_score("5", [famA, famB, famC], ps.family_weights[(SCOPE_MARKET, horizon, "5")], ps.rules.unknown_below,
                      put_call_ratio=inp.put_call_ratio)


# ---------------------------------------------------------------------------
# B1.6 上爻｜外部（美股曆）
# ---------------------------------------------------------------------------
def us_asof(tpe_date: str, us_dates: Sequence[str] | None) -> str | None:
    """上爻資料日＝截至台北 T 日 08:00 已收盤的最近美股交易日（唯一實作：iching.calendar.us_session_closed_by）。"""
    if not us_dates:
        return None
    return us_session_closed_by(tpe_date, list(us_dates))


def stale_days(tpe_date: str, tpe_dates: Sequence[str] | None, us_dates: Sequence[str] | None) -> int | None:
    """沿用同一根美股 bar 的台北交易日數（0＝T 日拿到新 bar）。
    # SPEC-NOTE: B1.6 只寫「美國假日導致無新資料時，沿用最近一筆並標 stale_days=N」，未定 N 的計數單位；
    #   此處＝T 之前**連續**與 T 對齊到同一個美股日的台北交易日數（週一因對齊上週五、上週五對齊上週四 → 0；
    #   美國週一休市則台北週二 stale=1）。若要改成曆日計數，只改此函式。"""
    if not tpe_dates or not us_dates:
        return None
    asof = us_asof(tpe_date, us_dates)
    if asof is None:
        return None
    prev = [d for d in tpe_dates if d < tpe_date]
    n = 0
    for d in reversed(prev):
        if us_asof(d, us_dates) == asof:
            n += 1
        else:
            break
    return n


def _slice_asof(dates: Sequence[str] | None, series, asof: str) -> np.ndarray | None:
    if dates is None or series is None:
        return None
    a = as_f(series)
    if a.size != len(dates):
        return None
    idx = [i for i, d in enumerate(dates) if d <= asof]
    if not idx:
        return None
    return a[: idx[-1] + 1]


def ind_period_return(series, n: int, d: float, direction: int = 1) -> Ind | Missing:
    a = _arr(series)
    if a is None:
        return Missing(REASON_MISSING, "series")
    if a.size < n + 1:
        return Missing(REASON_INSUFFICIENT, f"window {n}")
    base = float(a[-1 - n])
    if base == 0:
        return Missing(REASON_DENOM_ZERO, "base=0")
    return S_clip((float(a[-1]) / base - 1.0) * 100.0, 0.0, d, direction)


def line6_external(inp: MarketInputs, ps: ParamSet, horizon: str) -> LineResult:
    g = lambda fam, iid: ps.get(SCOPE_MARKET, horizon, "6", fam, iid)  # noqa: E731
    asof = us_asof(inp.tpe_date, inp.us_dates)
    sd = stale_days(inp.tpe_date, inp.tpe_dates, inp.us_dates)
    meta = {"us_asof": asof, "stale_days": sd}
    if asof is None:
        miss = Missing(REASON_MISSING, "us calendar/asof")
        famA = family_score("A", [sub_result("spx_return", miss, .35), sub_result("spx_ma_distance", miss, .30), sub_result("sox_return", miss, .35)])
        famB = family_score("B", [sub_result("usdtwd_change", miss)])
        return line_score("6", [famA, famB], ps.family_weights[(SCOPE_MARKET, horizon, "6")], ps.rules.unknown_below, **meta)
    if sd is None:
        famA_miss = Missing(REASON_MISSING, "tpe_dates required for stale_days")
    elif sd >= ps.rules.stale_degrade_at:
        famA_miss = Missing(REASON_STALE, f"stale_days={sd}")
    else:
        famA_miss = None
    if famA_miss is not None:
        famA = family_score("A", [sub_result("spx_return", famA_miss, .35), sub_result("spx_ma_distance", famA_miss, .30),
                                  sub_result("sox_return", famA_miss, .35)])
    else:
        spx_c = _slice_asof(inp.us_dates, inp.spx_close, asof)
        spx_h = _slice_asof(inp.us_dates, inp.spx_high, asof)
        spx_l = _slice_asof(inp.us_dates, inp.spx_low, asof)
        sox_c = _slice_asof(inp.us_dates, inp.sox_close, asof)
        p1, p2, p3 = g("A", "spx_return"), g("A", "spx_ma_distance"), g("A", "sox_return")
        if spx_c is not None and spx_h is not None and spx_l is not None:
            atr = atr14_prev(spx_h, spx_l, spx_c)
            dist = ind_index_ma_distance(spx_c, atr, p2.window, p2.d)
        else:
            dist = Missing(REASON_MISSING, "spx ohlc")
        famA = family_score("A", [
            sub_result("spx_return", ind_period_return(spx_c, p1.window, p1.d), p1.sub_weight),
            sub_result("spx_ma_distance", dist, p2.sub_weight),
            sub_result("sox_return", ind_period_return(sox_c, p3.window, p3.d), p3.sub_weight),
        ])
    # SPEC-NOTE: USD/TWD 為台灣資料集（TaiwanExchangeRate），規格要求上爻整族以美股交易日計窗；此處取
    #   匯率自身觀測日 ≤ 對齊美股日的最後 n+1 筆計期間變化（不會用到 T 日以後才知道的值）。
    fx = _slice_asof(inp.fx_dates, inp.fx_usdtwd, asof)
    pb = g("B", "usdtwd_change")
    famB = family_score("B", [sub_result("usdtwd_change", ind_period_return(fx, pb.window, pb.d, pb.direction))])
    return line_score("6", [famA, famB], ps.family_weights[(SCOPE_MARKET, horizon, "6")], ps.rules.unknown_below, **meta)


# ---------------------------------------------------------------------------
# 六爻 + 方向分數
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class MarketScores:
    market: str
    horizon: str
    tpe_date: str
    lines: dict[str, LineResult]
    direction_score: float | Missing
    inner_trigram_score: float | Missing
    outer_trigram_score: float | Missing
    coverage: str

    def line_scores(self) -> list[float | None]:
        return [self.lines[k].score for k in ("1", "2", "3", "4", "5", "6")]


LINE_FUNCS = {"1": line1_trend, "2": line2_breadth, "3": line3_participation, "4": line4_spot_flow,
              "5": line5_derivatives, "6": line6_external}


def score_market(inp: MarketInputs, ps: ParamSet, horizon: str) -> MarketScores:
    if horizon not in HORIZONS:
        raise ValueError(f"horizon must be one of {HORIZONS}")
    if ps.market != inp.market:
        raise ValueError(f"ParamSet is for {ps.market}, inputs are for {inp.market} — 兩市場不得共用設定物件")
    lines = {k: f(inp, ps, horizon) for k, f in LINE_FUNCS.items()}
    w = ps.line_weights[(SCOPE_MARKET, horizon)]
    return MarketScores(inp.market, horizon, inp.tpe_date, lines, direction_score(lines, w),
                        trigram_mean(lines, ("1", "2", "3")), trigram_mean(lines, ("4", "5", "6")), coverage_label(lines))


# ---------------------------------------------------------------------------
# B1.8 旗標
# ---------------------------------------------------------------------------
FLAG_NAMES = ("F-臨界", "F-高波動", "F-分歧", "F-廣度擴張", "F-廣度收縮")
# 門檻位移／名額乘數／門檻值全部在 `Rules`（進 model_version 指紋；B3.3 重播含旗標集合）


def flag_critical(line1: float | None, line2: float | None, rules: Rules) -> str:
    if line1 is None or line2 is None:
        return "unknown"
    lo, hi = rules.critical_band
    return "true" if (lo <= line1 <= hi or lo <= line2 <= hi) else "false"


def flag_high_vol(vix, index_high, index_low, index_close, rules: Rules, n: int) -> tuple[str, str]:
    """VIX ≥ 自身 n（250）日 `high_vol_pct` 百分位；VIX 缺 → 大盤 ATR14÷I 的 n 日百分位；皆缺 → unknown。回 (status, source)。"""
    v = _arr(vix)
    if v is not None:
        thr = percentile_threshold(v, rules.high_vol_pct, n)
        if not isinstance(thr, Missing):
            return ("true" if float(v[-1]) >= thr else "false"), "vix"
    h, l, c = _arr(index_high), _arr(index_low), _arr(index_close)
    if h is not None and l is not None and c is not None and c.size >= n + ATR_N + 2:
        atr = atr_series_prev(h, l, c)
        ratio = atr / c
        w = ratio[-n:]
        if not np.isnan(w).any():
            thr = float(np.percentile(w, rules.high_vol_pct))
            return ("true" if float(w[-1]) >= thr else "false"), "atr_ratio"
    return "unknown", "none"


def flag_divergence(own_state: str | None, other_state: str | None, inner: float | Missing, outer: float | Missing,
                    rules: Rules) -> str:
    """F-分歧＝加權與櫃買基本狀態不同 **或** 內外卦方向相反（一者 ≥55 且另一者 ≤45）。
    降級（B1.8）：另一市場狀態未定 → 只用內外卦條件；內外卦不可得時**無論狀態條件是否成立**皆 `unknown`
    （S1a §3.3：旗標缺值不得默認為沒有風險——兩市場狀態相同不能證明內外卦不分歧）。"""
    states_ok = own_state in ("S1", "S2", "S3", "S4") and other_state in ("S1", "S2", "S3", "S4")
    trig_ok = not isinstance(inner, Missing) and not isinstance(outer, Missing)
    if states_ok and own_state != other_state:
        return "true"
    if trig_ok and ((inner >= rules.trigram_hi and outer <= rules.trigram_lo) or (outer >= rules.trigram_hi and inner <= rules.trigram_lo)):
        return "true"
    if trig_ok:
        return "false"
    return "unknown"


def flag_breadth(line2_now: float | None, line2_t5: float | None, rules: Rules) -> tuple[str, str]:
    """(F-廣度擴張, F-廣度收縮)：二爻分數 5 交易日變化 ≥ +5.2／≤ −5.2；T−5 不存在 → 兩者 unknown（同一缺因）。"""
    if line2_now is None or line2_t5 is None:
        return "unknown", "unknown"
    chg = line2_now - line2_t5
    t = rules.breadth_change_threshold
    return ("true" if chg >= t else "false"), ("true" if chg <= -t else "false")


def resolve_unknown(flag: str, direction: str, rules: Rules) -> bool:
    """S1a §3.3 按方向：對該方向是收緊（門檻 + 或名額 ×<1）→ unknown 視為成立；放寬 → 視為不成立。"""
    shift, mult = rules.flag_effects[flag][direction]
    return shift > 0 or mult < 1.0


def market_flags(ms: MarketScores, inp: MarketInputs, ps: ParamSet) -> dict:
    rules = ps.rules
    if ps.market != ms.market:
        raise ValueError(f"ParamSet is for {ps.market}, scores are for {ms.market}")
    l1, l2 = ms.lines["1"].score, ms.lines["2"].score
    raw = {}
    raw["F-臨界"] = flag_critical(l1, l2, rules)
    hv, hv_src = flag_high_vol(inp.vix, inp.index_high, inp.index_low, inp.index_close, rules,
                               ps.get(SCOPE_MARKET, ms.horizon, "5", "C", "vix_phist_rev").window)
    raw["F-高波動"] = hv
    raw["F-分歧"] = flag_divergence(inp.own_state, inp.other_market_state, ms.inner_trigram_score, ms.outer_trigram_score, rules)
    exp, con = flag_breadth(l2, inp.line2_score_t_minus_5.get(ms.horizon), rules)
    raw["F-廣度擴張"], raw["F-廣度收縮"] = exp, con
    # 獨立缺因（B5.4）：T−5 state 缺＝1；VIX 與 ATR 皆缺＝1；分歧兩條件皆不可得＝1；初/二爻未知（臨界）＝1
    causes = []
    if exp == "unknown":
        causes.append("line2_t_minus_5_missing")
    if hv == "unknown":
        causes.append("vix_and_atr_missing")
    if raw["F-分歧"] == "unknown":
        causes.append("state_and_trigram_unavailable")
    if raw["F-臨界"] == "unknown":
        causes.append("line1_or_line2_unknown")
    data_insufficient = (hv == "unknown") or (len(causes) >= rules.insufficient_causes)
    per_dir = {}
    for direction in ("long", "short"):
        resolved = {}
        shift_sum, mult = 0.0, 1.0
        for f in FLAG_NAMES:
            st = raw[f]
            active = (st == "true") if st != "unknown" else resolve_unknown(f, direction, rules)
            resolved[f] = active
            if active:
                s, m = rules.flag_effects[f][direction]
                shift_sum += max(s, 0.0)          # 第一版：<0 的門檻調整視為 0
                mult *= min(m, 1.0)               # 第一版：>1.0 的名額乘數視為 1.0
        shift_sum = min(shift_sum, rules.shift_cap_deciles)
        if data_insufficient:
            mult *= rules.insufficient_multiplier
        per_dir[direction] = {"active": resolved, "threshold_shift_deciles": shift_sum, "quota_multiplier": mult}
    return {"raw": raw, "high_vol_source": hv_src, "missing_causes": causes, "data_insufficient": data_insufficient,
            "by_direction": per_dir, "calibrated": rules.calibrated,
            "basic_state": inp.own_state or "undetermined"}
