"""規格缺口裁決（2026-09-10 使用者裁定全甲；正本 docs/P2-KICKOFF.md §5 第 13–23 列）逐條實測：
現行行為 ＝ 裁定（用手算值比對，不是看註解），且可參數化的慣例皆為 Rules 欄位並進指紋。"""
from __future__ import annotations

import datetime as dt

import numpy as np
import pytest

from conftest import synth_market_inputs, synth_stock_inputs, weekdays
from iching.score import assemble_row, build_params, score_market, score_stock
from iching.score.aggregate import direction_score, family_score, sub_result
from iching.score.indicators import atr14_prev, swing_points, true_range
from iching.score.market import ind_ad_line_dev, ind_basis, stale_days
from iching.score.params import RULES_START, Rules
from iching.score.stock import ind_volume_scenario, volume_scenario_day
from iching.score.transform import Ind, Missing, P_hist, S_RANGE, percentile_threshold

R = RULES_START
DV, TV = "fm-20260910-01", "0.2"


def test_13_atr14_simple_mean_of_last_14_TR_to_t_minus_1():
    rng = np.random.default_rng(13)
    c = 100 + np.cumsum(rng.normal(0, 1, 40)); h = c + rng.uniform(0.5, 2, 40); l = c - rng.uniform(0.5, 2, 40)
    tr = true_range(h, l, c)                      # tr[j] ↔ index j+1
    hand = float(np.mean(tr[-15:-1]))             # 以 T−1 為終點的 14 個 TR 簡單平均
    assert atr14_prev(h, l, c, "simple") == pytest.approx(hand)
    assert R.atr_method == "simple"
    w = atr14_prev(h, l, c, "wilder")
    assert w is not None and w != pytest.approx(hand)
    assert build_params("twse").with_rules(atr_method="wilder").model_version() != build_params("twse").model_version()


def test_14_phist_include_today_midrank_linear_threshold():
    win = np.r_[np.arange(1.0, 250.0), 100.0]      # 當日 100 在 1..249 之間；100 出現兩次（含當日）
    r = P_hist(win, 250, R.phist_include_today, R.phist_tie)
    below, equal = 99, 2                            # 小於 100 的有 1..99；等於 100 的有兩筆（含當日）
    assert r.native == pytest.approx(100 * (below + 0.5 * equal) / 250)
    assert (R.phist_include_today, R.phist_tie, R.pct_interp) == (True, "mid", "linear")
    thr = percentile_threshold(np.arange(250.0), 80, 250, R.phist_include_today, R.pct_interp)
    assert thr == pytest.approx(0.8 * 249)          # 線性內插：(n−1)×q


def test_15_ad_line_dev_formula_ddof0():
    rng = np.random.default_rng(15)
    n = 10
    ad = np.cumsum(rng.integers(-300, 300, 40)).astype(float); N = np.full(40, 1800.0)
    r = ind_ad_line_dev(ad, N, n, 1.0, R.ad_std_ddof)
    ma = np.convolve(ad, np.ones(n) / n, mode="valid")           # MA_n(AD) 對齊尾端
    dev = (ad[n - 1:] - ma) / N[n - 1:]
    x_hand = dev[-1] / np.std(dev[-n:], ddof=0)
    assert r.x == pytest.approx(x_hand) and R.ad_std_ddof == 0
    assert ind_ad_line_dev(ad, N, n, 1.0, 1).x != pytest.approx(x_hand)


def test_16_basis_median_includes_today():
    b = np.r_[np.zeros(59), 5.0]                    # 含當日：中位數 0；不含當日只剩 59 筆 → insufficient
    r = ind_basis(b, False, 0.30, 60, R.basis_median_include_today)
    assert r.meta["c_rolling_median"] == 0.0 and R.basis_median_include_today is True
    b2 = np.r_[np.zeros(60), 5.0]
    assert ind_basis(b2, False, 0.30, 60, True).meta["c_rolling_median"] == 0.0
    assert isinstance(ind_basis(b, False, 0.30, 60, False), Missing)
    # 含當日時當日值會拉動中位數：59 個 0 ＋ 1 個 5 → 0；改成 30 個 0 ＋ 30 個 5（含當日 5）→ 2.5
    b3 = np.r_[np.zeros(30), np.full(30, 5.0)]
    assert ind_basis(b3, False, 0.30, 60, True).meta["c_rolling_median"] == 2.5


def test_17_stale_days_in_tpe_trading_days_monday_is_zero():
    tpe = weekdays(dt.date(2024, 3, 4), 15)                     # 03-04 週一 …
    us = [d for d in tpe if d != "2024-03-11"]                  # 美股 03-11（週一）休市
    assert stale_days("2024-03-11", tpe, us, R.stale_unit) == 0       # 週一：對齊上週五，週五自己對齊週四 → 0
    assert stale_days("2024-03-12", tpe, us, R.stale_unit) == 1       # 週二：美股週一休 → 沿用週五 → 1
    assert stale_days("2024-03-13", tpe, us, R.stale_unit) == 0
    assert R.stale_unit == "tpe_trading_days"
    assert stale_days("2024-03-11", tpe, us, "calendar_days") == 2    # 可選慣例：曆日（週六日）


def test_18_swing_tie_counts():
    h = np.array([1, 2, 3, 3, 2, 1, 2, 3, 3, 2, 1, 1, 1], dtype=float)   # 兩個平頂
    l = h - 1
    peaks, _ = swing_points(h, l, 2, 13, R.swing_tie_counts)
    assert peaks == [2, 3, 7, 8] and R.swing_tie_counts is True         # 平手兩根都算
    assert swing_points(h, l, 2, 13, False)[0] == []                     # 嚴格則平頂不算


def test_19_scenario_avg_includes_today_and_min_available_ratio():
    n = 60
    close = np.full(n, 100.0); high, low = close + 1.0, close - 1.0
    vol = np.full(n, 1000.0)
    close[-1] = 102.0; vol[-1] = 2000.0                    # 當日序 2（>50）；其餘日 50
    from iching.score.indicators import atr_series_prev
    atrs = atr_series_prev(high, low, close, "simple")
    today = volume_scenario_day(close, vol, atrs, n - 1, 5, 50.0, R)
    r = ind_volume_scenario(close, vol, high, low, 5, "short", [50.0] * 10, R)
    avg_incl = (today + 4 * 50.0) / 5                       # 含當日：5 天平均
    assert r.native == pytest.approx(0.6 * today + 0.4 * avg_incl) and R.avg_include_today is True
    r2 = ind_volume_scenario(close, vol, high, low, 5, "short", [50.0] * 10, Rules(avg_include_today=False))
    assert r2.native == pytest.approx(0.6 * today + 0.4 * 50.0)          # 不含當日：T−1…T−5 全 50
    # 可得日門檻：波段 10 日中 6 日無成交 → 4/10 < 0.5 → 缺值；3 日無成交 → 7/10 → 可算
    v6 = vol.copy(); v6[-7:-1] = 0.0
    assert isinstance(ind_volume_scenario(close, v6, high, low, 10, "swing", [50.0] * 10, R), Missing)
    v3 = vol.copy(); v3[-4:-1] = 0.0
    assert not isinstance(ind_volume_scenario(close, v3, high, low, 10, "swing", [50.0] * 10, R), Missing)
    assert R.avg_min_available_ratio == 0.5
    assert isinstance(ind_volume_scenario(close, v3, high, low, 10, "swing", [50.0] * 10, Rules(avg_min_available_ratio=0.8)), Missing)


def test_20_continuation_semantics_covered_elsewhere():
    """#20 的手算／回歸測試在 test_score_stock.py（test_line4_continuation_states、
    test_continuation_regression_no_drift_with_T、test_continuation_regression_events_before_scan_anchor）。"""
    assert R.continuation_scores == (80.0, 20.0, 50.0)


def test_21_fx_asof_is_last_observation_on_or_before_us_asof(ps_twse):
    inp = synth_market_inputs()
    cut = list(inp.us_dates)[:-5]                              # 美股停在 5 個交易日前（跨週末）
    inp2 = synth_market_inputs(us_dates=cut, spx_close=np.asarray(inp.spx_close)[:-5], spx_high=np.asarray(inp.spx_high)[:-5],
                               spx_low=np.asarray(inp.spx_low)[:-5], sox_close=np.asarray(inp.sox_close)[:-5])
    l6 = score_market(inp2, ps_twse, "short").lines["6"]
    assert l6.meta["fx_asof"] == l6.meta["us_asof"] and R.fx_asof_rule == "us_asof"
    fx = np.asarray(inp2.fx_usdtwd); idx = list(inp2.fx_dates).index(l6.meta["us_asof"])
    hand = (fx[idx] / fx[idx - 5] - 1.0) * 100.0
    assert l6.family("B").subs[0].x == pytest.approx(hand)
    alt = score_market(inp2, ps_twse.with_rules(fx_asof_rule="tpe_prev_day"), "short").lines["6"]
    assert alt.meta["fx_asof"] == inp2.tpe_dates[-2] and alt.family("B").subs[0].x != pytest.approx(hand)


def test_22_direction_score_missing_when_any_line_unknown(ps_twse):
    inp = synth_market_inputs(foreign_net_oi=None, basis=None, vix=None)
    ms = score_market(inp, ps_twse, "mid")
    assert ms.lines["5"].unknown and isinstance(ms.direction_score, Missing) and R.direction_unknown_policy == "missing"
    alt = score_market(inp, ps_twse.with_rules(direction_unknown_policy="reweight"), "mid")
    w = ps_twse.line_weights[("market_index", "mid")]
    known = {k: v for k, v in w.items() if k != "5"}
    hand = sum(alt.lines[k].score * v for k, v in known.items()) / sum(known.values())
    assert alt.direction_score == pytest.approx(hand)


def test_23_family_missing_reweights_by_weight():
    subs = [sub_result("a", Ind(70.0, S_RANGE), .35), sub_result("b", Missing("missing"), .30), sub_result("c", Ind(50.0, S_RANGE), .35)]
    assert family_score("A", subs, R.family_missing_policy).score == pytest.approx((70 * .35 + 50 * .35) / .70)
    subs2 = [sub_result("a", Ind(70.0, S_RANGE), .6), sub_result("b", Missing("missing"), .4), sub_result("c", Ind(50.0, S_RANGE), .1)]
    assert family_score("A", subs2, "weighted").score == pytest.approx((70 * .6 + 50 * .1) / .7)
    assert family_score("A", subs2, "equal_mean").score == pytest.approx(60.0)
    assert R.family_missing_policy == "weighted"
    # 上爻族 A（.35/.30/.35）缺 SOX：實跑＝按權重重配
    inp = synth_market_inputs(sox_close=None)
    fa = score_market(inp, build_params("twse"), "short").lines["6"].family("A")
    s = {x.indicator_id: x for x in fa.subs}
    assert s["sox_return"].score is None
    assert fa.score == pytest.approx((s["spx_return"].score * .35 + s["spx_ma_distance"].score * .30) / .65)


def test_rulings_are_in_fingerprint_and_grep_anchor():
    """每條裁定在 params.py 有「裁定（…§5 #N）」註記，且欄位皆進指紋。"""
    from pathlib import Path
    from conftest import ROOT
    src = (ROOT / "src" / "iching" / "score" / "params.py").read_text(encoding="utf-8")
    for n in range(13, 24):
        if n == 20:
            continue        # #20 的裁定註記在 stock.py ind_continuation（純算法、無可參數化慣例）
        assert f"§5 #{n}" in src, n
    stock_src = (ROOT / "src" / "iching" / "score" / "stock.py").read_text(encoding="utf-8")
    assert "§5 #20" in stock_src
    ps = build_params("twse")
    for fld, val in (("atr_method", "wilder"), ("phist_tie", "high"), ("ad_std_ddof", 1), ("basis_median_include_today", False),
                     ("stale_unit", "calendar_days"), ("swing_tie_counts", False), ("avg_include_today", False),
                     ("avg_min_available_ratio", 0.9), ("fx_asof_rule", "tpe_prev_day"), ("direction_unknown_policy", "reweight"),
                     ("family_missing_policy", "equal_mean"), ("phist_include_today", False), ("pct_interp", "nearest")):
        assert ps.with_rules(**{fld: val}).model_version() != ps.model_version(), fld
