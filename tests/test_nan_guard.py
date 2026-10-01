"""prereg-v2 PR-B（D4-①(a)，裁定 #71，`docs/P3-CALIBRATION.md` §37）：市場矩陣的 NaN 不再被當成「在場」。

守五件事（每個守門一正一突變——突變＝拿掉該守門時這裡的哪支會紅，列在各節註解）：
① **逐指標守門**（`market.py`）：視窗內 NaN → `Missing(missing, "<series> NaN in window")`；**視窗外**的 NaN 與乾淨輸入結果逐位相同
   （拿掉某支的 `_nan_in` → 該支的「視窗內」案例紅：B 類會退成底線的 `non-finite native`、A 類會吐 50／計數）。
② **`aggregate.sub_result` 底線**：`Ind.native` 非有限 → `Missing(missing, "non-finite native")`；合成「漏守一支」（把 `ind_ratio_L` 換成吐
   `Ind(native=nan)`）時爻分數仍不是 NaN（拿掉底線 → `test_sub_result_floor_*` 紅）。
③ **下游四處**：`lines_from_scores`／`hysteresis_step`／`replay_step._score_or_none`／`stock.ind_market_direction` 對 NaN 視同 None
   （拿掉任一處 → 對應案例紅；遲滯：狀態與 streak 原樣保留、不累加）。
④ **乾淨日零影響**：乾淨合成輸入下 v1（`tests/engine_v1.py` 逐字副本）與 v2 的 `score_market`／`score_stock`／`market_flags` 整個結果物件
   相等；合成世界（`tests/synth_db.build_full`）兩版重播，除 tpex 2020-02-16 起受 NaN 影響的大盤列外逐欄相同。
⑤ **乙案證據**：含 NaN 日的合成輸入，v1／v2 跑 `iching.xdump.XDump` 的 x dump 逐位相同（NaN 的 x 兩版都 `skipped`，`xdump.py` 的
   `math.isnan(x)` 判斷）；唯一已知的結構差異（`basis` 的滾動中位數視窗含 NaN、當日值有限）另立一支明寫，並以校準報告的 `basis` d 有限
   （`np.percentile` 遇 NaN 必回 NaN）證明訓練段 dump 沒出現過這個形狀。
另：`RULES_VERSION` 升 `p2-score-engine-3`、指紋釘值（不升 → 本檔與 §1.2 清單的釘值測試全紅）；合成世界普查 **v1 7 列／5 日 → v2 0 列**
（根因＝tpex 2020-02-16 `n_stocks=0`（6488 畸形列是唯一 tpex 股）→ `replay_state.market_inputs.ratio()` 分母 `np.where(nn>0, nn, nan)`
→ 廣度比值 NaN → `ind_ratio_L`／`ind_new_high_low` 吐 `Ind(native=nan)` → 大盤二爻 NaN 列；mid 的 `advance_ratio` 視窗 5 日再傳染到 02-20）。
"""
from __future__ import annotations

import json
import math
import sqlite3
import sys
from pathlib import Path

import numpy as np
import pytest

from conftest import ROOT, synth_market_inputs, synth_stock_inputs
from engine_v1 import (RULES_VERSION_V1, ind_buy_days_v1, ind_divergence_scenario_v1, sub_result_v1, v1_engine)
from iching import replay_state as RS
from iching import replay_step as ST
from iching.features_io import params_fingerprint
from iching.run_common import build_params_payload, load_state
from iching.score import build_params, score_market, score_stock
from iching.score import market as MK
from iching.score.aggregate import sub_result
from iching.score.calibrated import CALIBRATED_D
from iching.score.hexagram import YANG, YIN, hysteresis_step, lines_from_scores
from iching.score.market import (ind_amount_ratio, ind_basis, ind_buy_days, ind_divergence_scenario, ind_index_ma_distance,
                                 ind_index_ma_slope, ind_net_amount_ratio, ind_new_high_low, ind_oi_change, ind_period_return,
                                 ind_range_position, ind_ratio_L, market_flags)
from iching.score.params import HORIZONS, MARKETS, RULES_START, RULES_VERSION
from iching.score.stock import ind_market_direction
from iching.score.transform import (Ind, L_DECLARED_RANGE, Missing, PCT_RANGE, REASON_MISSING, S_RANGE)
from iching.scores_io import ScoreStore
from iching.xdump import XDump

sys.path.insert(0, str(ROOT / "scripts"))
import check_scores as CK  # noqa: E402
import replay_scores as R  # noqa: E402
import scan_features as SF  # noqa: E402
from synth_db import DAYS, DV, build_full  # noqa: E402

NAN = float("nan")
#: v2 指紋（原始碼實算，2026-10-01；§37 表）。v1（prereg-v1 凍結）：twse 01697576a7b0／tpex 83b5c5dfdb23／params_sha 8ca174ee8bc7、
#: --uncalibrated twse 18baea0222c0／tpex 05c3788311f8／ef44809db803。
V2_MV = {"twse": "p2-score-engine-3.4b5db7fc6f6d", "tpex": "p2-score-engine-3.15407a6adb13"}
V2_PARAMS_SHA = "cb3f2d905846"
V2_MV_UNCAL = {"twse": "p2-score-engine-3.9e9f7f575d1d", "tpex": "p2-score-engine-3.da65beda2e98"}
V2_PARAMS_SHA_UNCAL = "59d5ef0e36eb"
V1_MV = {"twse": "p2-score-engine-2.01697576a7b0", "tpex": "p2-score-engine-2.83b5c5dfdb23"}


def _nan_at(a, *idx) -> np.ndarray:
    b = np.array(a, dtype=float).copy()
    for i in idx:
        b[i] = NAN
    return b


def _miss(out, series: str) -> None:
    assert isinstance(out, Missing), out
    assert (out.reason, out.detail) == (REASON_MISSING, f"{series} NaN in window"), out


def _same(a, b) -> None:
    """兩個 Ind 逐位相同（視窗外 NaN 不得改變任何欄）。"""
    assert isinstance(a, Ind) and isinstance(b, Ind) and a == b, (a, b)


# ---------------------------------------------------------------------------
# 指紋
# ---------------------------------------------------------------------------
def test_rules_version_bumped_to_3_and_fingerprints_pinned():
    assert RULES_VERSION == "p2-score-engine-3" and RULES_VERSION_V1 == "p2-score-engine-2"
    mv = {m: build_params(m).model_version() for m in MARKETS}
    assert mv == V2_MV and all(mv[m] != V1_MV[m] for m in MARKETS)
    cross = load_state(ROOT / "data" / "state" / "cross.json")
    assert params_fingerprint(build_params_payload(mv, 320, cross.adv, fundamentals=True)) == V2_PARAMS_SHA != "8ca174ee8bc7"
    mvu = {m: build_params(m, calibrated=False).model_version() for m in MARKETS}
    assert mvu == V2_MV_UNCAL
    assert params_fingerprint(build_params_payload(mvu, 320, cross.adv, fundamentals=True)) == V2_PARAMS_SHA_UNCAL != "ef44809db803"


# ---------------------------------------------------------------------------
# ① 逐指標守門（突變：拿掉該支的 `_nan_in` → 「視窗內」案例紅）
# ---------------------------------------------------------------------------
rng = np.random.default_rng(7)
CLOSE = 15000 + np.cumsum(rng.normal(0, 80, 120))
POS = rng.uniform(0.3, 0.7, 120)
AMT = rng.uniform(2e11, 4e11, 120)
NET = rng.normal(0, 5e9, 120)


def test_ind_index_ma_distance_nan_guard():
    clean = ind_index_ma_distance(CLOSE, 100.0, 20, 1.0)
    _miss(ind_index_ma_distance(_nan_at(CLOSE, -1), 100.0, 20, 1.0), "close")
    _miss(ind_index_ma_distance(_nan_at(CLOSE, -20), 100.0, 20, 1.0), "close")
    _miss(ind_index_ma_distance(CLOSE, NAN, 20, 1.0), "ATR14")
    _same(ind_index_ma_distance(_nan_at(CLOSE, -21), 100.0, 20, 1.0), clean)


def test_ind_index_ma_slope_nan_guard():
    clean = ind_index_ma_slope(CLOSE, 100.0, 20, 5, 1.0)
    _miss(ind_index_ma_slope(_nan_at(CLOSE, -25), 100.0, 20, 5, 1.0), "close")
    _miss(ind_index_ma_slope(CLOSE, NAN, 20, 5, 1.0), "ATR14")
    _same(ind_index_ma_slope(_nan_at(CLOSE, -26), 100.0, 20, 5, 1.0), clean)


def test_ind_range_position_nan_guard():
    clean = ind_range_position(CLOSE, 20, (0.0, 0.5, 1.0))
    _miss(ind_range_position(_nan_at(CLOSE, -20), 20, (0.0, 0.5, 1.0)), "close")
    _miss(ind_range_position(_nan_at(CLOSE, -1), 20, (0.0, 0.5, 1.0)), "close")
    _same(ind_range_position(_nan_at(CLOSE, -21), 20, (0.0, 0.5, 1.0)), clean)


def test_ind_ratio_L_nan_guard():
    clean = ind_ratio_L(POS, 5, (0.3, 0.5, 0.7))
    _miss(ind_ratio_L(_nan_at(POS, -5), 5, (0.3, 0.5, 0.7)), "series")
    _miss(ind_ratio_L(_nan_at(POS, -1), 1, (0.3, 0.5, 0.7)), "series")
    _same(ind_ratio_L(_nan_at(POS, -6), 5, (0.3, 0.5, 0.7)), clean)
    _same(ind_ratio_L(_nan_at(POS, -2), 1, (0.3, 0.5, 0.7)), ind_ratio_L(POS, 1, (0.3, 0.5, 0.7)))


def test_ind_new_high_low_nan_guard():
    clean = ind_new_high_low(POS - 0.5, 5.0)
    _miss(ind_new_high_low(_nan_at(POS - 0.5, -1), 5.0), "new_high_low_ratio")
    _same(ind_new_high_low(_nan_at(POS - 0.5, -2), 5.0), clean)


def test_ind_amount_ratio_nan_guard():
    clean = ind_amount_ratio(AMT, 5, 20, 1.0, 0.3)
    _miss(ind_amount_ratio(_nan_at(AMT, -1), 5, 20, 1.0, 0.3), "amount")      # 分子（含 T）
    _miss(ind_amount_ratio(_nan_at(AMT, -2), 5, 20, 1.0, 0.3), "amount")      # 分子 ∩ 分母
    _miss(ind_amount_ratio(_nan_at(AMT, -21), 5, 20, 1.0, 0.3), "amount")     # 分母最早一日（T−20）
    _same(ind_amount_ratio(_nan_at(AMT, -22), 5, 20, 1.0, 0.3), clean)
    _same(ind_amount_ratio(_nan_at(AMT, -22), 1, 20, 1.0, 0.3), ind_amount_ratio(AMT, 1, 20, 1.0, 0.3))


def test_ind_divergence_scenario_nan_guard_not_scenario_4():
    """A 類：v1 對 NaN 比較全 False → 情境 4＝50 定值（連 NULL 痕跡都沒有）；v2 → Missing。"""
    clean = ind_divergence_scenario(CLOSE, AMT, 20, RULES_START)
    for bad_close in (_nan_at(CLOSE, -1), _nan_at(CLOSE, -20)):
        _miss(ind_divergence_scenario(bad_close, AMT, 20, RULES_START), "close")
        old = ind_divergence_scenario_v1(bad_close, AMT, 20, RULES_START)
        assert isinstance(old, Ind) and old.native == RULES_START.divergence_scores[3] == 50.0 and old.meta == {"seq": 4}
    for bad_amt in (_nan_at(AMT, -1), _nan_at(AMT, -21)):
        _miss(ind_divergence_scenario(CLOSE, bad_amt, 20, RULES_START), "amount")
    _same(ind_divergence_scenario(_nan_at(CLOSE, -21), _nan_at(AMT, -22), 20, RULES_START), clean)


def test_ind_net_amount_ratio_nan_guard():
    clean = ind_net_amount_ratio(NET, AMT, 5, 1.0)
    _miss(ind_net_amount_ratio(_nan_at(NET, -5), AMT, 5, 1.0), "net_amount")
    _miss(ind_net_amount_ratio(NET, _nan_at(AMT, -1), 5, 1.0), "amount")
    _same(ind_net_amount_ratio(_nan_at(NET, -6), _nan_at(AMT, -6), 5, 1.0), clean)


def test_ind_buy_days_nan_guard_not_counted():
    """A 類：v1 `np.sum(nan > 0)` 把 NaN 當「非買超日」照計數；v2 → Missing。"""
    clean = ind_buy_days(NET, 10, 2.0)
    for bad in (_nan_at(NET, -1), _nan_at(NET, -10)):
        _miss(ind_buy_days(bad, 10, 2.0), "net")
        old = ind_buy_days_v1(bad, 10, 2.0)
        assert isinstance(old, Ind) and math.isfinite(old.native)
    _same(ind_buy_days(_nan_at(NET, -11), 10, 2.0), clean)


def test_ind_oi_change_nan_guard():
    clean = ind_oi_change(NET, 5, 3000.0)
    _miss(ind_oi_change(_nan_at(NET, -6), 5, 3000.0), "foreign_net_oi")
    _miss(ind_oi_change(_nan_at(NET, -3), 5, 3000.0), "foreign_net_oi")     # 視窗內（兩端點之間）同樣算缺值，體例同 ind_balance_change
    _same(ind_oi_change(_nan_at(NET, -7), 5, 3000.0), clean)


def test_ind_basis_nan_guard():
    b = rng.normal(0, 0.3, 120)
    clean = ind_basis(b, False, 0.1, 60, True)
    _miss(ind_basis(_nan_at(b, -60), False, 0.1, 60, True), "basis")
    _miss(ind_basis(_nan_at(b, -1), False, 0.1, 60, True), "basis")
    _same(ind_basis(_nan_at(b, -61), False, 0.1, 60, True), clean)
    clean_x = ind_basis(b, False, 0.1, 60, False)
    _miss(ind_basis(_nan_at(b, -61), False, 0.1, 60, False), "basis")
    _miss(ind_basis(_nan_at(b, -1), False, 0.1, 60, False), "basis")          # 當日值
    _same(ind_basis(_nan_at(b, -62), False, 0.1, 60, False), clean_x)
    assert isinstance(ind_basis(_nan_at(b, -1), True, 0.1, 60, True), Missing)  # 換月日優先


def test_ind_period_return_nan_guard():
    clean = ind_period_return(CLOSE, 20, 1.0)
    _miss(ind_period_return(_nan_at(CLOSE, -21), 20, 1.0), "series")
    _miss(ind_period_return(_nan_at(CLOSE, -1), 20, 1.0, -1), "series")
    _same(ind_period_return(_nan_at(CLOSE, -22), 20, 1.0), clean)


# ---------------------------------------------------------------------------
# ② sub_result 底線（突變：拿掉 `math.isfinite` 判斷 → 下兩支紅）
# ---------------------------------------------------------------------------
def test_sub_result_floor_non_finite_native_is_missing():
    for bad in (Ind(NAN, S_RANGE, NAN), Ind(NAN, L_DECLARED_RANGE, 0.5), Ind(float("inf"), PCT_RANGE, None), Ind(-float("inf"), S_RANGE, 1.0)):
        r = sub_result("x", bad, 0.4)
        assert r.score is None and r.native is None and r.x is None and r.clipped is False and r.sub_weight == 0.4
        assert r.missing == Missing(REASON_MISSING, "non-finite native")
    ok = Ind(60.0, S_RANGE, 1.5, True, {"k": 1})
    assert sub_result("x", ok, 0.4) == sub_result_v1("x", ok, 0.4)            # 有限值路徑與 v1 逐位相同
    m = Missing("missing", "why")
    assert sub_result("x", m) == sub_result_v1("x", m)
    with pytest.raises(TypeError):
        sub_result("x", 50.0)                                                     # type: ignore[arg-type]


def test_sub_result_floor_catches_an_unguarded_indicator(monkeypatch, ps_twse):
    """合成「漏守一支」：把 `ind_ratio_L` 換成直接吐 `Ind(native=nan)`（＝v1 的行為）→ 二爻族 A／B 缺、分數不得是 NaN。"""
    monkeypatch.setattr(MK, "ind_ratio_L", lambda series, n_avg, anchors: Ind(NAN, L_DECLARED_RANGE, NAN))
    for h in HORIZONS:
        ms = score_market(synth_market_inputs(), ps_twse, h)
        l2 = ms.lines["2"]
        assert l2.family("A").score is None and l2.family("B").score is None
        assert all(s.missing == Missing(REASON_MISSING, "non-finite native") for f in ("A", "B") for s in l2.family(f).subs)
        assert l2.reweighted and l2.coverage_ratio < 1.0 and (l2.score is None or math.isfinite(l2.score))
        assert all(s is None or math.isfinite(s) for s in ms.line_scores())
        assert ms.coverage == "reweighted"


# ---------------------------------------------------------------------------
# ③ 下游四處
# ---------------------------------------------------------------------------
def test_lines_from_scores_nan_is_absent_not_yin():
    assert lines_from_scores([50.0, NAN, 60, 60, 60, 60]) is None
    assert lines_from_scores([50.0, np.float64(NAN), 60, 60, 60, 60]) is None
    assert lines_from_scores([50.0, 49.9, 60, 60, 60, 60]) == [1, 0, 1, 1, 1, 1]
    with pytest.MonkeyPatch.context() as mp, v1_engine(mp):                       # v1 對照：NaN 記成陰
        from iching.score import hexagram as HX
        assert HX.lines_from_scores([50.0, NAN, 60, 60, 60, 60]) == [1, 0, 1, 1, 1, 1]


def test_hysteresis_nan_keeps_state_and_streak():
    assert hysteresis_step(YANG, 1, NAN) == (YANG, 1, False)
    assert hysteresis_step(YIN, 1, np.float64(NAN)) == (YIN, 1, False)
    assert hysteresis_step(None, 0, NAN) == (None, 0, False)
    assert hysteresis_step(YANG, 1, None) == (YANG, 1, False)                   # None 路徑逐字不變
    assert hysteresis_step(YIN, 1, 55.0) == (YANG, 0, True)
    with pytest.MonkeyPatch.context() as mp, v1_engine(mp):                       # v1 對照：NaN 使 streak 歸零／首日記陰
        from iching.score import hexagram as HX
        assert HX.hysteresis_step(YANG, 1, NAN) == (YANG, 0, False)
        assert HX.hysteresis_step(None, 0, NAN) == (YIN, 0, False)


def test_advance_lines_nan_keeps_formal_and_streaks():
    cross = RS.CrossDayState()
    cross.market_lines["twse|short"] = [(YANG, 1), (YIN, 1), (YANG, 0), (YIN, 0), (YANG, 1), (YIN, 1)]
    before = [tuple(x) for x in cross.market_lines["twse|short"]]
    formal, streaks = cross.advance_lines("market", "twse", "short", [NAN] * 6, RULES_START)
    assert formal == [1, 0, 1, 0, 1, 0] and streaks == [1, 1, 0, 0, 1, 1]
    assert [tuple(x) for x in cross.market_lines["twse|short"]] == before
    with pytest.MonkeyPatch.context() as mp, v1_engine(mp):
        c1 = RS.CrossDayState()
        c1.market_lines["twse|short"] = list(before)
        assert c1.advance_lines("market", "twse", "short", [NAN] * 6, RULES_START)[1] == [0] * 6


def test_score_or_none_nan_is_none():
    assert ST._score_or_none(NAN) is None and ST._score_or_none(np.float64(NAN)) is None
    assert ST._score_or_none(Missing("line_unknown", "x")) is None and ST._score_or_none(None) is None and ST._score_or_none(True) is None
    assert ST._score_or_none(51.0) == 51.0 and ST._score_or_none(np.float64(51.5)) == 51.5 and ST._score_or_none(7) == 7.0


def test_ind_market_direction_nan_is_missing(ps_twse):
    assert ind_market_direction(NAN) == Missing(REASON_MISSING, "market direction score NaN")
    assert ind_market_direction(None) == Missing(REASON_MISSING, "market direction score")
    assert ind_market_direction(61.25) == Ind(61.25, S_RANGE, 61.25)
    ss = score_stock(synth_stock_inputs(market_direction_score={"short": NAN}), ps_twse, "short")
    l6 = ss.lines["6"]
    assert l6.family("A").score is None and l6.score is not None and math.isfinite(l6.score) and l6.reweighted
    with pytest.MonkeyPatch.context() as mp, v1_engine(mp):
        old = score_stock(synth_stock_inputs(market_direction_score={"short": NAN}), ps_twse, "short").lines["6"]
        assert math.isnan(old.score) and not old.unknown and old.coverage_ratio == 1.0 and not old.reweighted


# ---------------------------------------------------------------------------
# ④ 乾淨日零影響（單元層）＋ 髒日的整體形狀
# ---------------------------------------------------------------------------
def _flags(ms, inp, ps):
    inp.own_state, inp.other_market_state = "S1", "S2"
    return market_flags(ms, inp, ps)


@pytest.mark.parametrize("market", MARKETS)
def test_clean_inputs_v1_v2_identical(market):
    ps = build_params(market)
    for h in HORIZONS:
        inp_a, inp_b = synth_market_inputs(market=market), synth_market_inputs(market=market)
        new_m = score_market(inp_a, ps, h)
        new_s = score_stock(synth_stock_inputs(market=market), ps, h)
        new_f = _flags(new_m, inp_a, ps)
        with pytest.MonkeyPatch.context() as mp, v1_engine(mp):
            old_m = score_market(inp_b, ps, h)
            old_s = score_stock(synth_stock_inputs(market=market), ps, h)
            old_f = _flags(old_m, inp_b, ps)
        assert new_m == old_m and new_s == old_s and new_f == old_f
        assert new_m.coverage == "full" and all(s is not None for s in new_m.line_scores())


def test_dirty_amount_day_v1_nan_row_vs_v2_missing(ps_twse):
    """T 日 `amount` NaN：v1 三／四爻分數 NaN、unknown=False、coverage full、方向 NaN、卦有陰爻（handback_D4 ①(a) 的實測形狀）；
    v2 三／四爻族 A／C（含情境 C 與買超天數 C）Missing「… NaN in window」、爻重配或未知、方向 Missing、無任何 NaN。"""
    inp = synth_market_inputs()
    inp.amount = _nan_at(inp.amount, -1)
    for h in HORIZONS:
        ms = score_market(synth_market_inputs(amount=inp.amount), ps_twse, h)
        l3, l4 = ms.lines["3"], ms.lines["4"]
        _miss(l3.family("A").subs[0].missing, "amount")
        _miss(l3.family("C").subs[0].missing, "amount")
        _miss(l4.family("A").subs[0].missing, "amount")
        _miss(l4.family("B").subs[0].missing, "amount")
        assert l4.family("C").score is not None                                    # foreign_buy_days 不吃 amount：照算
        assert all(s is None or math.isfinite(s) for s in ms.line_scores())
        assert l3.reweighted and l4.reweighted and ms.coverage == "reweighted"
        assert isinstance(ms.direction_score, Missing) or math.isfinite(ms.direction_score)
        assert lines_from_scores(ms.line_scores()) is None or all(b in (0, 1) for b in lines_from_scores(ms.line_scores()))
        with pytest.MonkeyPatch.context() as mp, v1_engine(mp):
            old = score_market(synth_market_inputs(amount=inp.amount), ps_twse, h)
        o3, o4 = old.lines["3"], old.lines["4"]
        assert math.isnan(o3.score) and math.isnan(o4.score) and not o3.unknown and not o4.unknown
        assert o3.coverage_ratio == 1.0 and not o3.reweighted and old.coverage == "full" and math.isnan(old.direction_score)


def test_dirty_foreign_t_minus_1_contaminates_window(ps_twse):
    """T−1 日外資淨額 NaN：族 A（Σ_n 視窗含 T−1）與族 C（買超天數視窗含 T−1）皆缺，v1 的族 C 會給出一個計數分數。"""
    inp = synth_market_inputs()
    fn = _nan_at(inp.foreign_net_amount, -2)
    for h in HORIZONS:
        l4 = score_market(synth_market_inputs(foreign_net_amount=fn), ps_twse, h).lines["4"]
        _miss(l4.family("A").subs[0].missing, "net_amount")
        _miss(l4.family("C").subs[0].missing, "net")
        assert l4.family("B").score is not None and l4.family("D").score is not None
        with pytest.MonkeyPatch.context() as mp, v1_engine(mp):
            o4 = score_market(synth_market_inputs(foreign_net_amount=fn), ps_twse, h).lines["4"]
        assert math.isnan(o4.family("A").score) and math.isfinite(o4.family("C").score)


def test_dirty_breadth_day_line2_unknown(ps_tpex):
    """T 日廣度母體 N=0 的形狀（合成世界 tpex 2020-02-16）：四個比值序列在 T 日 NaN → 二爻族 A／B／C 缺、整爻未知；v1 二爻 NaN。"""
    inp = synth_market_inputs(market="tpex")
    kw = dict(above_ma_ratio={k: _nan_at(v, -1) for k, v in inp.above_ma_ratio.items()},
              advance_ratio=_nan_at(inp.advance_ratio, -1),
              new_high_low_ratio={k: _nan_at(v, -1) for k, v in inp.new_high_low_ratio.items()},
              up_amount_ratio=_nan_at(inp.up_amount_ratio, -1))
    for h in HORIZONS:
        ms = score_market(synth_market_inputs(market="tpex", **kw), ps_tpex, h)
        l2 = ms.lines["2"]
        _miss(l2.family("A").subs[0].missing, "series")
        _miss(l2.family("B").subs[0].missing, "series")
        _miss(l2.family("C").subs[0].missing, "new_high_low_ratio")
        assert l2.unknown and l2.score is None and l2.family("D").score is not None
        _miss(ms.lines["3"].family("B").subs[0].missing, "series")                   # up_amount_ratio 也是比值
        assert lines_from_scores(ms.line_scores()) is None
        with pytest.MonkeyPatch.context() as mp, v1_engine(mp):
            old = score_market(synth_market_inputs(market="tpex", **kw), ps_tpex, h)
        assert math.isnan(old.lines["2"].score) and not old.lines["2"].unknown and old.lines["2"].coverage_ratio == 1.0


# ---------------------------------------------------------------------------
# ⑤ 乙案：x dump 逐位相同
# ---------------------------------------------------------------------------
def _dirty_days():
    """六個合成日（n 不同 → tpe_date 不同），各在不同序列塞 NaN；外加一個乾淨日與一檔個股（方向分數 NaN）。"""
    out = []
    for k in range(6):
        inp = synth_market_inputs(seed=k, n=300 + k)
        if k == 1:
            inp.amount = _nan_at(inp.amount, -1)
        elif k == 2:
            inp.foreign_net_amount, inp.trust_net_amount = _nan_at(inp.foreign_net_amount, -2), _nan_at(inp.trust_net_amount, -1)
        elif k == 3:
            inp.advance_ratio = _nan_at(inp.advance_ratio, -1)
            inp.above_ma_ratio = {w: _nan_at(v, -1) for w, v in inp.above_ma_ratio.items()}
            inp.new_high_low_ratio = {w: _nan_at(v, -1) for w, v in inp.new_high_low_ratio.items()}
        elif k == 4:
            inp.index_close, inp.foreign_net_oi = _nan_at(inp.index_close, -3), _nan_at(inp.foreign_net_oi, -1)
        elif k == 5:
            inp.spx_close, inp.fx_usdtwd, inp.vix = _nan_at(inp.spx_close, -1), _nan_at(inp.fx_usdtwd, -2), _nan_at(inp.vix, -1)
        out.append(inp)
    return out


def _dump(tmp: Path, tag: str, ps) -> tuple[dict[str, bytes], dict]:
    days = _dirty_days()
    dates = sorted(d.tpe_date for d in days)
    xd = XDump(tmp / tag, ps, dump_from=dates[0], dump_to=dates[-1], data_version="synth", params_sha="x", params={})
    for inp in days:
        for h in HORIZONS:
            xd.on_scores(score_market(inp, ps["twse"], h))
            xd.on_scores(score_stock(synth_stock_inputs(n=inp.index_close.size, tpe_date=inp.tpe_date,
                                                        market_direction_score={h: NAN}), ps["twse"], h))
    xd.finish()
    files = {p.name: p.read_bytes() for p in sorted((tmp / tag).glob("*.f32"))}
    man = json.loads((tmp / tag / "manifest.json").read_text(encoding="utf-8"))
    return files, {k: (v["n"], v["skipped"], v["date_min"], v["date_max"]) for k, v in man["keys"].items()}


def test_xdump_identical_between_v1_and_v2_on_nan_days(tmp_path):
    """x 為 NaN 的鍵（B 類全部＋`market_direction`）兩版都 skipped → 檔案位元組與 n／skipped 逐鍵相同。
    **唯一例外＝`foreign_buy_days`**（A 類 `ind_buy_days`）：v1 在視窗含 NaN 時仍吐**有限的** x（把 NaN 當非買超日計數），dump 多寫一筆偏誤樣本；
    v2 → Missing → skipped。這是 x dump 在 NaN 日唯一的結構差異，此處把它切出來逐一斷言（另一個已知差異 `basis` 見下一支）。
    乙案成立的依據不是「結構上零差異」，而是訓練＋驗證段**沒有 NaN 日**（`test_backtest_dataset_has_no_nan_shaped_rows`）。"""
    ps = {m: build_params(m) for m in MARKETS}
    new_files, new_keys = _dump(tmp_path, "v2", ps)
    with pytest.MonkeyPatch.context() as mp, v1_engine(mp):
        old_files, old_keys = _dump(tmp_path, "v1", ps)
    fbd = {k for k in new_keys if k.endswith("__4__C__foreign_buy_days") and "__twse__" in k}      # 只餵了 twse；tpex 鍵兩版皆 n=0
    assert len(fbd) == 3 and set(new_files) == set(old_files) and set(new_keys) == set(old_keys)
    assert {k: v for k, v in new_files.items() if k[:-4] not in fbd} == {k: v for k, v in old_files.items() if k[:-4] not in fbd}
    assert {k: v for k, v in new_keys.items() if k not in fbd} == {k: v for k, v in old_keys.items() if k not in fbd}
    for k in fbd:                                                  # 第 2 日（k=2）T−1 外資 NaN：v1 多 1 筆有限樣本、v2 多 1 筆 skipped
        assert (old_keys[k][0], old_keys[k][1]) == (new_keys[k][0] + 1, new_keys[k][1] - 1) == (6, 0)
        a, b = np.frombuffer(old_files[k + ".f32"], dtype="<f4"), np.frombuffer(new_files[k + ".f32"], dtype="<f4")
        assert a.size == 6 and b.size == 5 and np.isfinite(a).all() and sorted(np.delete(a, 2)) == sorted(b)
    skipped = {k: v[1] for k, v in new_keys.items() if v[1]}
    assert skipped and any("amount_ratio" in k for k in skipped) and any("market_direction" in k for k in skipped)
    assert all(not any(np.isnan(np.frombuffer(b, dtype="<f4"))) for b in new_files.values())


def test_backtest_dataset_has_no_nan_shaped_rows():
    """乙案的經驗證據：`data/backtest/{train,valid}_*.csv.gz`（Hetzner v1 db 匯出的訓練＋驗證段個股列）**沒有任何 NaN 形狀的列**。
    NaN 大盤爻 → 方向 NaN → 全市場個股上爻族 A NaN → `base_score` NaN → sqlite 讀回 NULL → CSV 空字串、但 `coverage` 仍是 `full`
    （真 Missing 必伴隨 `reweighted`）。所以「`base_score` 空且 `coverage=full`」只可能來自 NaN；0 列 ⇒ 校準 dump 的訓練段沒有 NaN 視窗
    ⇒ v1／v2 的 x dump 逐位相同（含上一支切出來的 `foreign_buy_days`）。資料集換成 v2 匯出後此不變式照樣成立（守門後 NaN 列不可能產生）。"""
    import csv
    import gzip
    files = sorted((ROOT / "data" / "backtest").glob("*.csv.gz"))
    assert {f.name for f in files} >= {"train_short.csv.gz", "train_swing.csv.gz", "train_mid.csv.gz", "valid_short.csv.gz", "valid_swing.csv.gz", "valid_mid.csv.gz"}
    seen = 0
    for f in files:
        with gzip.open(f, "rt", encoding="utf-8") as fh:
            for row in csv.DictReader(fh):
                seen += 1
                bs = row["base_score"]
                if bs == "":
                    assert row["coverage"] == "reweighted", (f.name, row)        # 真 Missing
                else:
                    assert math.isfinite(float(bs)), (f.name, row)               # 不得是 nan／inf 字面
    assert seen > 3_000_000


def test_xdump_known_structural_difference_basis_rolling_median(tmp_path):
    """唯一已知的差異：`basis` 的 x（當日值）有限、滾動中位數視窗含 NaN。v1 吐 `Ind(native=nan, x=有限, c_rolling_median=nan)`，
    `XDump` 以 `x − c_roll`＝NaN **寫進** buffer（n+1）；v2 → Missing → skipped。訓練段 dump 若出現過這個形狀，`calibrate_d.py` 的
    `np.percentile` 會得 NaN、報告的 basis d 不可能是有限值——現行 `CALIBRATED_D` 的 basis d 有限 ⇒ 訓練段沒出現過，乙案成立。"""
    ps = {m: build_params(m) for m in MARKETS}
    inp = synth_market_inputs(seed=11)
    inp.basis = _nan_at(inp.basis, -30)
    def run(tag):
        xd = XDump(tmp_path / tag, ps, dump_from=inp.tpe_date, dump_to=inp.tpe_date, data_version="synth", params_sha="x", params={})
        xd.on_scores(score_market(inp, ps["twse"], "short"))
        xd.finish()
        man = json.loads((tmp_path / tag / "manifest.json").read_text(encoding="utf-8"))
        k = next(k for k in man["keys"] if k.endswith("__5__B__basis") and "__twse__short__" in k)
        f = tmp_path / tag / f"{k}.f32"
        return man["keys"][k]["n"], man["keys"][k]["skipped"], (np.frombuffer(f.read_bytes(), dtype="<f4") if f.exists() else np.array([]))
    n2, s2, b2 = run("v2")
    with pytest.MonkeyPatch.context() as mp, v1_engine(mp):
        n1, s1, b1 = run("v1")
    assert (n2, s2, b2.size) == (0, 1, 0)
    assert (n1, s1, b1.size) == (1, 0, 1) and np.isnan(b1[0])
    assert all(math.isfinite(CALIBRATED_D[k]) for k in CALIBRATED_D if k[2] == "basis") and len([k for k in CALIBRATED_D if k[2] == "basis"]) == 6
    assert math.isnan(float(np.percentile(np.array([1.0, NAN]), 85)))


# ---------------------------------------------------------------------------
# 合成世界普查：v1 7 列／5 日 → v2 0 列；其餘列逐欄相同
# ---------------------------------------------------------------------------
@pytest.fixture(scope="module")
def cache(tmp_path_factory) -> Path:
    c = tmp_path_factory.mktemp("nan") / "cache"
    build_full(c)
    assert SF.main(["--cache-dir", str(c), "--quiet", "--allow-short-warmup", "--warmup-days", "0"]) == 0
    return c


def _census(db: Path) -> dict:
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        return CK.nan_census(con, tuple(v[0] for v in con.execute("SELECT version_id FROM versions")))
    finally:
        con.close()


def _all_rows(db: Path) -> dict[tuple, dict]:
    out = {}
    with ScoreStore(db, readonly=True) as s:
        for d in s.dates(DV):
            for r in s.rows_for_day(DV, d):
                out[(d, r["market"], r["stock_id"], r["horizon"])] = r
    return out


NAN_DAY = "2020-02-16"
V1_NAN_ROWS = [(NAN_DAY, "tpex", h) for h in ("mid", "short", "swing")] + [(d, "tpex", "mid") for d in ("2020-02-17", "2020-02-18", "2020-02-19", "2020-02-20")]
#: v1 NaN 列的下游痕跡只允許落在這些欄：二爻本身、三爻的 coverage（`up_amount_ratio` 同為 N=0 的比值：v1 NaN 當在場、v2 Missing，
#: 兩版三爻皆未知只差 ratio）、卦象／正式爻態／streak、coverage、旗標（F-臨界吃二爻）
TPEX_DIFF_COLS_ALLOWED = {"line_2", "line_2_unknown", "line_2_coverage_ratio", "line_2_reweighted", "line_3_coverage_ratio", "line_3_reweighted",
                          "lines_provisional", "lines_formal", "coverage", "line_states", "streaks", "king_wen", "hexagram_name",
                          "king_wen_provisional", "name_provisional", "king_wen_formal", "name_formal", "base_score", "direction_score", "flags"}


def test_synth_world_v1_seven_nan_rows_become_zero(cache, tmp_path, monkeypatch):
    assert DAYS[35] == NAN_DAY                                                        # synth_db.MALFORMED_I=35：6488 畸形列＝唯一 tpex 股 → N=0
    new_db, old_db = tmp_path / "v2.db", tmp_path / "v1.db"
    assert R.main(["--cache-dir", str(cache), "--out", str(new_db), "--window", "30", "--quiet"]) == 0
    with v1_engine(monkeypatch):
        assert R.main(["--cache-dir", str(cache), "--out", str(old_db), "--window", "30", "--quiet"]) == 0
    monkeypatch.undo()
    c_new, c_old = _census(new_db), _census(old_db)
    assert (c_new["n_market_rows"], c_new["n_stock_rows"], c_new["dates"]) == (0, 0, [])
    assert (c_old["n_market_rows"], c_old["n_stock_rows"]) == (7, 0) and c_old["dates"] == sorted({d for d, _, _ in V1_NAN_ROWS})
    assert [(d, m, h) for d in c_old["dates"] for (m, h, _) in c_old["market"][d]] == sorted(V1_NAN_ROWS)
    assert all(ls == [2] for d in c_old["dates"] for (_, _, ls) in c_old["market"][d])
    new, old = _all_rows(new_db), _all_rows(old_db)
    assert set(new) == set(old) and len(new) == 1581
    # v2：02-16（四個比值在 T 日全 NaN）三列二爻**未知**；02-17～02-20 mid 只有 advance_ratio（5 日均）仍含 NaN → 族 B 缺、二爻**重配**成有限分數
    for d, m, h in V1_NAN_ROWS:
        r, o = new[(d, m, "__MARKET__", h)], old[(d, m, "__MARKET__", h)]
        assert r["line_2_reweighted"] == 1 and r["line_2_coverage_ratio"] < o["line_2_coverage_ratio"]
        if d == NAN_DAY:
            assert r["line_2"] is None and r["line_2_unknown"] == 1 and r["line_2_coverage_ratio"] < 0.5 and r["lines_provisional"] is None
        else:
            assert r["line_2"] is not None and math.isfinite(r["line_2"]) and r["line_2_unknown"] == 0
        assert o["line_2"] is None and o["line_2_unknown"] == 0                   # v1：NaN 當在場（NULL 且 unknown=0 的 NaN 形狀）
    # 乾淨列逐欄相同：twse 大盤列與全部個股列、tpex 02-16 之前；tpex 02-16 起只允許二爻／卦象／遲滯欄不同
    diff_other, diff_tpex = {}, {}
    for k in new:
        d, m, sid, h = k
        cols = tuple(c for c in new[k] if c != "model_version" and new[k][c] != old[k][c])
        if not cols:
            continue
        (diff_tpex if (sid == "__MARKET__" and m == "tpex" and d >= NAN_DAY) else diff_other)[k] = cols
    assert diff_other == {}, diff_other
    assert set().union(*diff_tpex.values()) <= TPEX_DIFF_COLS_ALLOWED, diff_tpex
    assert {k[:2] + (k[3],) for k in diff_tpex} >= set(V1_NAN_ROWS)
    # 傳染窗（§4 風險⑧的合成實例）：02-16 的 NaN 比值 → mid 三爻族 B `up_amount_ratio` 20 日均含 NaN 到 03-15（＝DAYS[35:55]）、
    # F-廣度擴張／收縮讀 T−5 的二爻（v1 推進的是 NaN → 比較全 False 成「false」；v2 推進 None → unknown）到 03-05；02-20 之後只剩這兩欄
    i = DAYS.index(NAN_DAY)
    assert {k[0] for k in diff_tpex} <= set(DAYS[i:i + 20]), sorted({k[0] for k in diff_tpex})
    assert set().union(*(c for k, c in diff_tpex.items() if k[0] > "2020-02-20")) <= {"flags", "line_3_coverage_ratio"}
    assert max(k[0] for k in diff_tpex) == DAYS[i + 19]
