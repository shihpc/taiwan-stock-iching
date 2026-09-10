"""大盤六爻（B1.1–B1.8）：決定性（§8 #5）、缺值不成 50（§8 #6）、上爻美股對齊（§8 #7）、兩市場設定分離、旗標。"""
from __future__ import annotations

import datetime as dt

import numpy as np
import pytest

from conftest import synth_market_inputs, weekdays
from iching.calendar import us_session_closed_by
from iching.score import build_params, score_market
from iching.score.market import (flag_breadth, flag_critical, flag_divergence, ind_divergence_scenario,
                                 ind_range_position, market_flags, stale_days)
from iching.score.params import HORIZONS, RULES_START, SCOPE_MARKET
from iching.score.transform import Missing, N


@pytest.mark.parametrize("h", HORIZONS)
def test_deterministic_same_input_same_output(ps_twse, h):
    a = score_market(synth_market_inputs(), ps_twse, h)
    b = score_market(synth_market_inputs(), ps_twse, h)
    assert a.line_scores() == b.line_scores()
    assert a.direction_score == b.direction_score
    assert all(s is not None for s in a.line_scores())
    assert a.coverage == "full"
    assert 7.30 <= a.direction_score <= 92.70


def test_two_markets_have_separate_param_objects():
    a, b = build_params("twse"), build_params("tpex")
    assert a is not b and a.distance_d is not b.distance_d and a.params is not b.params
    assert a.market_slope_d is not a.stock_slope_d          # 大盤斜率族與個股斜率族各一份
    assert a.model_version() != b.model_version()           # market 進指紋
    with pytest.raises(ValueError):
        score_market(synth_market_inputs(market="tpex"), a, "short")


def test_all_params_uncalibrated_and_start_values(ps_twse):
    assert all(p.calibrated is False for p in ps_twse.params.values()) and ps_twse.calibrated is False
    assert ps_twse.distance_d == {5: 0.6, 10: 0.8, 20: 1.0, 60: 1.5}
    assert ps_twse.market_slope_d == {5: 0.5, 10: 0.7, 20: 1.0}
    g = ps_twse.get
    assert g(SCOPE_MARKET, "short", "1", "A", "dist_ma_short").d == 0.6 and g(SCOPE_MARKET, "mid", "1", "A", "dist_ma_long").d == 1.5
    assert g(SCOPE_MARKET, "mid", "1", "B", "ma20_slope").d == 1.0
    assert g(SCOPE_MARKET, "short", "4", "A", "foreign_net_ratio").d == 1.0 and g(SCOPE_MARKET, "mid", "4", "B", "trust_net_ratio").d == 0.10
    assert g(SCOPE_MARKET, "swing", "6", "A", "spx_return").d == 2.1 and g(SCOPE_MARKET, "mid", "6", "B", "usdtwd_change").d == 0.60
    assert g(SCOPE_MARKET, "mid", "6", "A", "spx_ma_distance").d == ps_twse.distance_d[20]
    assert g(SCOPE_MARKET, "short", "5", "—", "put_call_ratio").scored is False
    for h in HORIZONS:
        assert sum(ps_twse.line_weights[(SCOPE_MARKET, h)].values()) == pytest.approx(1.0)
        for line in "123456":
            assert sum(ps_twse.family_weights[(SCOPE_MARKET, h, line)].values()) == pytest.approx(1.0)


def test_missing_family_reweights_not_50(ps_twse):
    """§8 #6：抽掉一族輸入 → 該族標缺值、爻重配，不得變 50。"""
    inp = synth_market_inputs(foreign_net_oi=None)
    ms = score_market(inp, ps_twse, "short")
    l5 = ms.lines["5"]
    famA = l5.family("A")
    assert famA.score is None and famA.missing.reason == "missing"
    assert all(s.score is None and s.missing is not None for s in famA.subs)
    assert l5.reweighted and l5.coverage_ratio == pytest.approx(0.6) and not l5.unknown
    b, c = l5.family("B").score, l5.family("C").score
    assert l5.score == pytest.approx((b * .30 + c * .30) / .60)
    assert ms.coverage == "reweighted"
    # 對照：完整輸入下該爻分數不同且族 A 有值
    full = score_market(synth_market_inputs(), ps_twse, "short").lines["5"]
    assert full.family("A").score is not None and full.score != l5.score


def test_whole_line_missing_is_unknown_not_50(ps_twse):
    inp = synth_market_inputs(foreign_net_oi=None, basis=None, vix=None)
    ms = score_market(inp, ps_twse, "mid")
    l5 = ms.lines["5"]
    assert l5.unknown and l5.score is None and l5.coverage_ratio == 0.0
    assert isinstance(ms.direction_score, Missing) and ms.direction_score.reason == "line_unknown"
    assert isinstance(ms.outer_trigram_score, Missing)
    assert not isinstance(ms.inner_trigram_score, Missing)


def test_coverage_ratio_below_half_is_unknown(ps_twse):
    # 二爻：抽掉 A(.4)+B(.2) → 剩 .4 < .5 → 未知；只抽 A → .6 → 重配
    ms = score_market(synth_market_inputs(above_ma_ratio={}, advance_ratio=None), ps_twse, "short")
    assert ms.lines["2"].unknown and ms.lines["2"].score is None
    ms2 = score_market(synth_market_inputs(above_ma_ratio={}), ps_twse, "short")
    assert not ms2.lines["2"].unknown and ms2.lines["2"].coverage_ratio == pytest.approx(0.6)


def test_denominator_zero_is_separate_reason(ps_twse):
    n = 320
    flat = np.full(n, 15000.0)
    ms = score_market(synth_market_inputs(index_open=flat, index_high=flat, index_low=flat, index_close=flat), ps_twse, "short")
    l1 = ms.lines["1"]
    assert all(s.missing.reason == "denominator_zero" for s in l1.family("A").subs)
    assert l1.family("B").subs[0].missing.reason == "denominator_zero"
    assert l1.family("C").subs[0].missing.reason == "denominator_zero"
    assert l1.unknown


def test_range_position_at_max_gives_L_after_N():
    c = np.r_[np.linspace(100, 110, 30), 120.0]
    r = ind_range_position(c, 20, (0.0, 0.5, 1.0))
    assert r.native == 80.0 and N(r.native, 10, 90) == pytest.approx(82.03, abs=0.005)


def test_divergence_scenario_table():
    R = RULES_START
    amt = np.full(40, 100.0)
    up = np.r_[np.linspace(100, 110, 39), 120.0]
    assert ind_divergence_scenario(up, np.r_[amt[:-1], 90.0], 20, R).native == 35     # 新高 ∧ AMT<AMTMA → 35
    dn = np.r_[np.linspace(110, 100, 39), 90.0]
    assert ind_divergence_scenario(dn, np.r_[amt[:-1], 160.0], 20, R).native == 25    # 新低 ∧ AMT ≥ 1.5× → 25
    assert ind_divergence_scenario(dn, np.r_[amt[:-1], 70.0], 20, R).native == 55     # 新低 ∧ AMT < 0.8× → 55
    assert ind_divergence_scenario(dn, np.r_[amt[:-1], 100.0], 20, R).native == 50    # 其他
    assert ind_divergence_scenario(np.full(40, 100.0), amt, 20, R).reason == "denominator_zero"


def test_line6_uses_calendar_us_session_closed_by_and_stale(ps_twse):
    inp = synth_market_inputs()
    ms = score_market(inp, ps_twse, "short")
    asof = ms.lines["6"].meta["us_asof"]
    assert asof == us_session_closed_by(inp.tpe_date, list(inp.us_dates))
    assert asof < inp.tpe_date and ms.lines["6"].meta["stale_days"] == 0
    # 週一：對齊上週五
    T = inp.tpe_date
    assert dt.date.fromisoformat(T).weekday() == 4  # 合成資料最後一天為週五
    mon = weekdays(dt.date.fromisoformat(T) + dt.timedelta(days=1), 1)[0]
    assert dt.date.fromisoformat(mon).weekday() == 0
    inp2 = synth_market_inputs(tpe_date=mon, tpe_dates=list(inp.tpe_dates) + [mon])
    l6m = score_market(inp2, ps_twse, "short").lines["6"]
    assert l6m.meta["us_asof"] == T and l6m.meta["stale_days"] == 0      # 週五 bar 在週一才拿到 → 週一對齊週五、非 stale
    # 美股連續缺 3 個台北交易日 → stale ≥ 3 → 族 A 降級、族 B 照算
    cut = list(inp.us_dates)[:-4]
    inp3 = synth_market_inputs(us_dates=cut, spx_close=np.asarray(inp.spx_close)[:-4], spx_high=np.asarray(inp.spx_high)[:-4],
                               spx_low=np.asarray(inp.spx_low)[:-4], sox_close=np.asarray(inp.sox_close)[:-4])
    l6 = score_market(inp3, ps_twse, "short").lines["6"]
    assert l6.meta["stale_days"] >= 3
    assert l6.family("A").missing.reason == "stale" and l6.family("B").score is not None
    assert stale_days(T, inp.tpe_dates, None) is None


def test_no_us_calendar_makes_line6_unknown(ps_twse):
    l6 = score_market(synth_market_inputs(us_dates=None), ps_twse, "mid").lines["6"]
    assert l6.unknown and l6.score is None


def test_flags_unknown_resolution_and_causes(ps_twse):
    inp = synth_market_inputs(line2_score_t_minus_5={})
    ms = score_market(inp, ps_twse, "short")
    fl = market_flags(ms, inp, ps_twse)
    assert fl["raw"]["F-廣度擴張"] == "unknown" and fl["raw"]["F-廣度收縮"] == "unknown"
    assert fl["missing_causes"] == ["line2_t_minus_5_missing"] and fl["data_insufficient"] is False   # 一個缺因不觸發 ×0.5
    assert fl["by_direction"]["long"]["active"]["F-廣度收縮"] is True     # 多方收緊 → unknown 視為成立
    assert fl["by_direction"]["long"]["active"]["F-廣度擴張"] is False    # 多方放寬 → 視為不成立
    assert fl["by_direction"]["short"]["active"]["F-廣度擴張"] is True
    assert fl["by_direction"]["short"]["active"]["F-廣度收縮"] is False
    assert fl["calibrated"] is False
    # 第二個獨立缺因（VIX 與 ATR 皆缺）→ ×0.5；只 VIX 缺但指數夠長 → 走 ATR 降級，不算缺因
    short_idx = {k: np.asarray(getattr(inp, k))[-100:] for k in ("index_open", "index_high", "index_low", "index_close")}
    inp2 = synth_market_inputs(line2_score_t_minus_5={}, vix=None, **short_idx)
    ms2 = score_market(inp2, ps_twse, "short")
    fl2 = market_flags(ms2, inp2, ps_twse)
    assert fl2["raw"]["F-高波動"] == "unknown" and fl2["data_insufficient"] is True
    assert fl2["by_direction"]["long"]["active"]["F-高波動"] is True
    inp3 = synth_market_inputs(vix=None)
    fl3 = market_flags(score_market(inp3, ps_twse, "short"), inp3, ps_twse)
    assert fl3["high_vol_source"] == "atr_ratio" and fl3["raw"]["F-高波動"] in ("true", "false")


def test_flags_first_version_only_tightens(ps_twse):
    inp = synth_market_inputs()
    ms = score_market(inp, ps_twse, "short")
    l2 = ms.lines["2"].score
    inp.line2_score_t_minus_5 = {"short": l2 - ps_twse.rules.breadth_change_threshold - 1}     # 擴張成立
    fl = market_flags(ms, inp, ps_twse)
    assert fl["raw"]["F-廣度擴張"] == "true" and fl["raw"]["F-廣度收縮"] == "false"
    long = fl["by_direction"]["long"]
    assert long["active"]["F-廣度擴張"] is True
    others = [f for f, a in long["active"].items() if a and f != "F-廣度擴張"]
    expected = 1.0
    for f in others:
        expected *= {"F-臨界": .75, "F-高波動": .5, "F-分歧": .75, "F-廣度收縮": .75}[f]
    assert long["quota_multiplier"] == pytest.approx(expected)      # ×1.25 視為 ×1.0
    assert long["threshold_shift_deciles"] <= 2.0


def test_flag_helpers():
    R = RULES_START
    assert flag_critical(45.0, 70.0, R) == "true" and flag_critical(44.9, 55.1, R) == "false" and flag_critical(None, 50.0, R) == "unknown"
    assert flag_breadth(60.0, 54.8, R) == ("true", "false") and flag_breadth(50.0, 55.2, R) == ("false", "true")
    assert flag_breadth(50.0, None, R) == ("unknown", "unknown")
    assert flag_divergence("S1", "S4", Missing("x"), Missing("x"), R) == "true"
    assert flag_divergence(None, None, 56.0, 44.0, R) == "true"
    assert flag_divergence(None, None, 50.0, 50.0, R) == "false"          # 狀態未定 → 只用內外卦條件（B1.8 降級）
    assert flag_divergence("S1", "S1", 50.0, 50.0, R) == "false"
    assert flag_divergence(None, "S1", Missing("x"), Missing("x"), R) == "unknown"
    assert flag_divergence("S1", "S1", Missing("x"), Missing("x"), R) == "unknown"   # A-2：狀態相同 ∧ 內外卦不可得 → unknown，不得默認無風險


def test_flags_and_rules_enter_model_version(ps_twse):
    assert ps_twse.with_rules(breadth_change_threshold=5.0).model_version() != ps_twse.model_version()
    fe = {k: dict(v) for k, v in ps_twse.rules.flag_effects.items()}
    fe["F-臨界"]["long"] = (1.0, 0.75)
    assert ps_twse.with_rules(flag_effects=fe).model_version() != ps_twse.model_version()
    import dataclasses
    alt = dataclasses.replace(ps_twse, calibrated=True)
    assert alt.model_version() != ps_twse.model_version()
    assert ps_twse.with_rules().model_version() == ps_twse.model_version()


def test_version_binding_changed_param_changes_version_and_score(ps_twse):
    inp = synth_market_inputs()
    base = score_market(inp, ps_twse, "short")
    ps2 = ps_twse.with_param(SCOPE_MARKET, "short", "1", "A", "dist_ma_short", d=0.9)
    alt = score_market(inp, ps2, "short")
    assert ps2.model_version() != ps_twse.model_version()
    assert alt.lines["1"].score != base.lines["1"].score
    assert alt.lines["2"].score == base.lines["2"].score
    assert ps_twse.get(SCOPE_MARKET, "short", "1", "A", "dist_ma_short").d == 0.6   # 原物件未被改動
