"""個股六爻（B2.1–B2.7）：決定性、缺值不成 50、逐族公式數字例。"""
from __future__ import annotations

import numpy as np
import pytest

from conftest import synth_stock_inputs
from iching.score import score_stock
from iching.score.params import HORIZONS, RULES_START, SCOPE_STOCK
from iching.score.stock import (ind_continuation, ind_margin_scenario, ind_persistence, ind_structure, ind_volume_scenario,
                                revenue_yoy_3m, revenue_yoy_single, volume_scenario_day)
from iching.score.indicators import atr_series_prev
from iching.score.transform import Missing, N, S_clip, scenario_value_after_N

R = RULES_START
OVERHEAT_CAP = scenario_value_after_N(R.overheat_cap_native)
REVENUE_HIGH_FLOOR = scenario_value_after_N(R.revenue_high_floor_native)


@pytest.mark.parametrize("h", HORIZONS)
def test_deterministic(ps_twse, h):
    a = score_stock(synth_stock_inputs(), ps_twse, h)
    b = score_stock(synth_stock_inputs(), ps_twse, h)
    assert a.line_scores() == b.line_scores() and a.direction_score == b.direction_score
    assert all(s is not None for s in a.line_scores()) and a.coverage == "full"
    for h2 in HORIZONS:
        assert sum(ps_twse.line_weights[(SCOPE_STOCK, h2)].values()) == pytest.approx(1.0)
        for line in "123456":
            assert sum(ps_twse.family_weights[(SCOPE_STOCK, h2, line)].values()) == pytest.approx(1.0)


def test_market_mismatch_raises(ps_tpex):
    with pytest.raises(ValueError):
        score_stock(synth_stock_inputs(market="twse"), ps_tpex, "short")


# ---- B2.1
def test_line1_short_single_month_swing_3m_mid_full(ps_twse, stk):
    rev = {ym: v for ym, v in stk.monthly_revenue}
    latest = max(rev)
    s = score_stock(stk, ps_twse, "short").lines["1"]
    assert [f.family for f in s.families] == ["A"] and s.expected_weights == {"A": 1.0}
    assert s.family("A").subs[0].x == pytest.approx(revenue_yoy_single(rev, latest))
    assert ps_twse.get(SCOPE_STOCK, "short", "1", "A", "revenue_yoy").d == 20.0
    w = score_stock(stk, ps_twse, "swing").lines["1"]
    assert w.family("A").subs[0].x == pytest.approx(revenue_yoy_3m(rev, latest))
    assert ps_twse.get(SCOPE_STOCK, "swing", "1", "A", "revenue_yoy").d == 15.0
    m = score_stock(stk, ps_twse, "mid").lines["1"]
    assert [f.family for f in m.families] == ["A", "B", "C"] and m.expected_weights == {"A": .5, "B": .3, "C": .2}
    assert [s.indicator_id for s in m.family("B").subs] == ["eps_yoy", "gross_margin_qoq"]
    assert m.family("B").subs[0].x == pytest.approx((2.0 / 1.5 - 1) * 100)
    assert m.family("C").subs[0].x == pytest.approx(revenue_yoy_3m(rev, latest) - 5.0)


def test_line1_revenue_12m_high_is_floor_not_cap(ps_twse):
    # 營收持平 → base 50；最新月略高＝12 月新高 → 族 A 取 max(base, 84.16)
    rev = [(f"{2022 + i // 12}-{i % 12 + 1:02d}", 100.0) for i in range(29)] + [("2024-06", 101.0)]
    m = score_stock(synth_stock_inputs(monthly_revenue=rev), ps_twse, "mid").lines["1"].family("A")
    assert m.meta["revenue_high_12m"] is True and m.meta["floor_applied"] is True
    assert m.score == pytest.approx(REVENUE_HIGH_FLOOR)
    # base 已高於 84.16 → 不壓低
    rev2 = [(f"{2022 + i // 12}-{i % 12 + 1:02d}", 100.0 * (1.5 ** (i // 12))) for i in range(30)]
    m2 = score_stock(synth_stock_inputs(monthly_revenue=rev2), ps_twse, "mid").lines["1"].family("A")
    assert m2.meta["revenue_high_12m"] is True and m2.score >= REVENUE_HIGH_FLOOR


def test_line1_eps_alternative_and_financial_rule(ps_twse):
    f = {"eps": 0.5, "eps_ly": -0.2, "gross_margin": 50.0, "gross_margin_prev_q": 49.0, "price_at_period_end": 50.0}
    b = score_stock(synth_stock_inputs(fundamentals=f), ps_twse, "mid").lines["1"].family("B")
    assert b.subs[0].indicator_id == "eps_diff_over_price" and b.subs[0].x == pytest.approx(0.7 / 50 * 100)
    fin = score_stock(synth_stock_inputs(is_financial=True), ps_twse, "mid").lines["1"].family("B")
    assert [s.indicator_id for s in fin.subs] == ["pretax_income_yoy", "equity_qoq"]
    assert fin.subs[0].x == pytest.approx(20.0) and fin.subs[1].x == pytest.approx((1000 / 980 - 1) * 100)


def test_line1_missing_rules(ps_twse):
    l = score_stock(synth_stock_inputs(monthly_revenue=None), ps_twse, "short").lines["1"]
    assert l.unknown and l.score is None and l.family("A").missing.reason == "missing"
    m = score_stock(synth_stock_inputs(industry_revenue_n=3), ps_twse, "mid").lines["1"]
    assert m.family("C").score is None and not m.unknown and m.coverage_ratio == pytest.approx(0.8)
    # 缺季報只讓族 B 缺，不會變 50
    m2 = score_stock(synth_stock_inputs(fundamentals=None), ps_twse, "mid").lines["1"]
    assert m2.family("B").score is None and m2.reweighted


# ---- B2.2
def test_line2_structure_scenarios():
    i = np.arange(120)
    tri = np.abs((i % 8) - 4) * 3.0
    up = 100 + 0.5 * i + tri
    dn = 200 - 0.5 * i + tri
    assert ind_structure(up + 1, up - 1, 2, 20, R).native == 80 and N(80, 0, 100) == pytest.approx(75.62, abs=0.005)
    assert ind_structure(dn + 1, dn - 1, 2, 20, R).native == 20
    flat = 100 + tri
    assert ind_structure(flat + 1, flat - 1, 2, 20, R).native == 50
    ramp = np.arange(30.0)
    assert ind_structure(ramp + 1, ramp - 1, 2, 20, R).meta["structure"] == "insufficient_swings"   # 單調：無內部擺動點


def test_line2_uses_stock_slope_table_and_distance_table(ps_twse, stk):
    assert ps_twse.get(SCOPE_STOCK, "mid", "2", "B", "ma_long_slope").d == ps_twse.stock_slope_d[20]
    assert ps_twse.get(SCOPE_STOCK, "mid", "2", "A", "dist_ma_long").d == ps_twse.distance_d[60] == 1.5
    l2 = score_stock(stk, ps_twse, "mid").lines["2"]
    assert l2.meta["atr14_prev"] > 0 and l2.score is not None


# ---- B2.3
def test_line3_overheat_cap(ps_twse):
    n = 320
    close = np.full(n, 100.0)
    close[-1] = 130.0      # ATR14_{t−1} 由平靜歷史算出，跳空日不在其中
    high, low = close + 1.0, close - 1.0
    s = score_stock(synth_stock_inputs(close=close, high=high, low=low, p_cs_long_excess=99.0), ps_twse, "short").lines["3"]
    assert s.meta["overheated"] is True and s.meta.get("overheat_cap_applied") is True
    assert s.score == pytest.approx(OVERHEAT_CAP)
    s2 = score_stock(synth_stock_inputs(close=close, high=high, low=low, p_cs_long_excess=90.0), ps_twse, "short").lines["3"]
    assert s2.meta["overheated"] is False and s2.score > OVERHEAT_CAP
    s3 = score_stock(synth_stock_inputs(p_cs_long_excess=None), ps_twse, "short").lines["3"]
    assert s3.meta["overheated"] is None


def test_line3_industry_sample_rule(ps_twse):
    l = score_stock(synth_stock_inputs(industry_n=4), ps_twse, "swing").lines["3"]
    assert l.family("B").score is None and l.coverage_ratio == pytest.approx(0.75)


# ---- B2.4
def _base_ohlcv(n=60):
    close = np.full(n, 100.0)
    high, low = close + 1.0, close - 1.0
    vol = np.full(n, 1000.0)
    return close, high, low, vol


def test_line4_scenario_seq2_formula_and_seq1_needs_line2():
    close, high, low, vol = _base_ohlcv()
    close = close.copy(); vol = vol.copy()
    close[-1] = 102.0     # 日變動 = 2/ATR(=2) = 1 ≥ 0.5
    vol[-1] = 2000.0      # 量比 2 ≥ 1.3
    atrs = atr_series_prev(high, low, close, "simple")
    s = volume_scenario_day(close, vol, atrs, len(close) - 1, 5, 50.0, R)
    assert s == pytest.approx(60 + 0.5 * (S_clip(1.0, 0.3, 0.7).native - 50))
    # 序 1：回撤 (0,2]、量比 <0.8、C ≥ MA20 → 二爻分未知時不得判 50
    close2, high2, low2, vol2 = _base_ohlcv()
    close2 = close2.copy(); vol2 = vol2.copy()
    close2[-2] = 104.0; close2[-1] = 103.0; vol2[-1] = 500.0
    atrs2 = atr_series_prev(high2, low2, close2, "simple")
    r = volume_scenario_day(close2, vol2, atrs2, len(close2) - 1, 5, None, R)
    assert isinstance(r, Missing) and "line2" in r.detail
    assert volume_scenario_day(close2, vol2, atrs2, len(close2) - 1, 5, 56.0, R) == 60.0
    assert volume_scenario_day(close2, vol2, atrs2, len(close2) - 1, 5, 54.0, R) == 50.0
    # 當日無成交 → 缺值
    vol3 = vol.copy(); vol3[-1] = 0.0
    assert isinstance(volume_scenario_day(close, vol3, atrs, len(close) - 1, 5, 50.0, R), Missing)
    # 短線合成：當日缺 → 族缺（不是 50）
    assert isinstance(ind_volume_scenario(close, vol3, high, low, 5, "short", [50.0] * 10, R), Missing)


def test_line4_mid_uses_continuous_indicators(ps_twse, stk):
    a = score_stock(stk, ps_twse, "mid").lines["4"].family("A")
    assert [s.indicator_id for s in a.subs] == ["updown_volume_ratio", "obv_slope"]
    b = score_stock(stk, ps_twse, "short").lines["4"].family("A")
    assert [s.indicator_id for s in b.subs] == ["volume_scenario"]


def test_line4_continuation_states():
    """裁定 #20：手算案例（base_n=20、k=3）。"""
    base = np.full(40, 100.0)
    up = np.r_[base, 105.0, 104.0, 104.0, 104.0]          # 突破 b=40（>100），第 3 日 104 ≥ 100 守住 → 80
    r = ind_continuation(up, 20, 3, R)
    assert r.native == 80 and r.meta["continuation"] == "breakout_held" and r.meta["level"] == 100.0
    dn = np.r_[base, 95.0, 96.0, 96.0, 96.0]              # 跌破 b=40（<100），第 3 日 96 < 100 未收復 → 20
    r = ind_continuation(dn, 20, 3, R)
    assert r.native == 20 and r.meta["continuation"] == "breakdown_unrecovered"
    # 確認失敗／收復要用「回到區間內」的價格：平底 100 時任何 < 100 都是新跌破事件（裁定：每次新低＝新事件）
    band = 101.0 + (np.arange(40) % 2)                    # 101/102 交錯：max 102、min 101
    fail = np.r_[band, 103.0, 101.5, 101.5, 101.5]        # 突破 102 後第 3 日 101.5 < 102（且未跌破 101）→ 確認失敗 50
    r = ind_continuation(fail, 20, 3, R)
    assert r.meta["continuation"] == "breakout_failed" and r.native == 50
    rec = np.r_[band, 100.0, 101.5, 101.5, 101.5]         # 跌破 101 後第 3 日 101.5 ≥ 101（且未突破 102）→ 收復 50
    assert ind_continuation(rec, 20, 3, R).meta["continuation"] == "breakdown_recovered"
    newlow = np.r_[base, 105.0, 99.0, 99.0, 99.0]         # 平底：99 < 100 是新跌破事件 b=41 → T=43 < 44 → pending
    assert ind_continuation(newlow, 20, 3, R).meta["continuation"] == "pending"
    pend = np.r_[base, 105.0, 104.0]                      # b=40，T=41 < b+3 → 確認未完成 → 50
    r = ind_continuation(pend, 20, 3, R)
    assert r.meta["continuation"] == "pending" and r.native == 50
    reset = np.r_[base, 105.0, 106.0, 104.0, 104.0]       # 106 > 105 是新事件（b=41）、重置確認窗 → T=43 < 44 → pending
    assert ind_continuation(reset, 20, 3, R).meta["continuation"] == "pending"
    assert ind_continuation(np.full(60, 100.0), 20, 3, R).meta["continuation"] == "no_event"
    assert isinstance(ind_continuation(np.full(10, 100.0), 20, 3, R), Missing)


def test_continuation_regression_no_drift_with_T():
    """裁定 #20 回歸①：單調創高序列每天都是新事件 → T=40…80 每個 T 皆「確認未完成」50，不隨 T 漂移。"""
    c = 100.0 + np.arange(100) * 0.5
    outs = [(ind_continuation(c[: T + 1], 20, 3, R).native, ind_continuation(c[: T + 1], 20, 3, R).meta["continuation"]) for T in range(40, 81)]
    assert set(outs) == {(50.0, "pending")}, sorted(set(outs))
    # 同型走勢平移後結果相同（T 的絕對位置不影響）
    held = np.r_[np.full(40, 100.0), 105.0, 104.0, 104.0, 104.0]
    for pad in (0, 7, 33):
        seq = np.r_[np.full(pad, 100.0), held]
        assert ind_continuation(seq, 20, 3, R).meta["continuation"] == "breakout_held"


def test_continuation_regression_events_before_scan_anchor():
    """裁定 #20 回歸②：事件掃描用完整歷史——事件在 T−n（期間內）算得出；T−n−1 之外 → no_event。"""
    n, k = 20, 3
    # 突破 b=40，之後守住且不再創高：T = b+n 仍在期間內 → 80；T = b+n+1 → no_event
    seq = np.r_[np.full(40, 100.0), 105.0, np.full(30, 104.0)]
    T_in, T_out = 40 + n, 40 + n + 1
    assert ind_continuation(seq[: T_in + 1], n, k, R).meta["continuation"] == "breakout_held"
    assert ind_continuation(seq[: T_out + 1], n, k, R).meta["continuation"] == "no_event"
    # 舊實作錨在 T−n−k 掃描起點：T=b+n 時 T−n−k = b−k，事件仍在掃描範圍；T=b+n−k+1 時舊法基準列窗會錯——新法直接以整段歷史判事件
    for T in range(43, T_in + 1):
        assert ind_continuation(seq[: T + 1], n, k, R).meta["continuation"] == "breakout_held", T


# ---- B2.5
def test_line5_margin_scenarios_ordered():
    close_up = np.linspace(100, 110, 30)
    close_dn = np.linspace(110, 100, 30)
    flat = np.full(30, 1000.0)
    ms = lambda m, c, e: ind_margin_scenario(m, c, e, 5, 0.0, 5.0, R)  # noqa: E731
    assert ms(flat, close_up, True).meta["seq"] == 1
    up = flat.copy(); up[-1] = 1010.0            # r=+1%
    assert ms(up, close_up, True).meta["seq"] == 2 and ms(up, close_up, True).native == 50.0
    r3 = ms(up, close_dn, True)
    assert r3.meta["seq"] == 3 and 7.30 <= r3.native <= 47.88 + 1e-9 and r3.native == pytest.approx(S_clip(-1.0, 0, 5).native)
    dn = flat.copy(); dn[-1] = 990.0
    r4 = ms(dn, close_up, True)
    assert r4.meta["seq"] == 4 and 51.06 - 1e-9 <= r4.native <= 71.35 + 1e-9
    assert ms(flat, close_up, False).reason == "not_eligible"
    assert ms(None, close_up, True).reason == "missing"
    nanm = flat.copy(); nanm[-3] = float("nan")
    assert ms(nanm, close_up, True).reason == "missing"       # 缺列 NaN → 缺值，不是假的變化率


@pytest.mark.parametrize("n,d", [(5, 1.0), (10, 2.0), (20, 3.34)])
def test_line5_persistence_never_clipped(n, d):
    for k in range(n + 1):
        net = np.r_[np.ones(k), -np.ones(n - k)]
        r = ind_persistence(net, n, d)
        assert r.clipped is False and r.x == k - n / 2


def test_line5_whole_line_missing_unknown(ps_twse):
    inp = synth_stock_inputs(foreign_net_shares=None, trust_net_shares=None, margin_balance=None, short_sale_balance=None)
    ss = score_stock(inp, ps_twse, "short")
    assert ss.lines["5"].unknown and ss.lines["5"].score is None
    assert isinstance(ss.direction_score, Missing) and ss.direction_score.reason == "line_unknown"
    assert ss.coverage == "reweighted"


def test_line5_start_values(ps_twse):
    g = ps_twse.get
    assert g(SCOPE_STOCK, "short", "5", "A", "foreign_strength_short").window == 3 and g(SCOPE_STOCK, "short", "5", "A", "foreign_strength_short").d == 5.0
    assert g(SCOPE_STOCK, "mid", "5", "B", "trust_strength_long").d == 2.0 and g(SCOPE_STOCK, "mid", "5", "C", "foreign_persistence").d == 3.34
    assert g(SCOPE_STOCK, "swing", "5", "E", "short_sale_change").direction == -1 and g(SCOPE_STOCK, "swing", "5", "E", "short_sale_change").d == 0.3


# ---- B2.6
def test_line6_market_direction_passthrough(ps_twse):
    ss = score_stock(synth_stock_inputs(market_direction_score={"short": 61.234567}), ps_twse, "short")
    assert ss.lines["6"].family("A").score == 61.234567          # 恆等映射、不再套 N
    ss2 = score_stock(synth_stock_inputs(market_direction_score={}), ps_twse, "short")
    assert ss2.lines["6"].family("A").score is None and ss2.lines["6"].coverage_ratio == pytest.approx(0.5) and not ss2.lines["6"].unknown
    assert ps_twse.get(SCOPE_STOCK, "mid", "6", "B", "industry_relative_return").d == 5.0


def test_version_binding(ps_twse, stk):
    base = score_stock(stk, ps_twse, "swing")
    ps2 = ps_twse.with_param(SCOPE_STOCK, "swing", "3", "A", "excess_long", d=9.0)
    alt = score_stock(stk, ps2, "swing")
    assert ps2.model_version() != ps_twse.model_version()
    assert alt.lines["3"].score != base.lines["3"].score and alt.lines["2"].score == base.lines["2"].score
