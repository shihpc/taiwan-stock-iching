"""§8 第 2 條：S／clip_3d／N／L 精確實作，用 spec 內數字例（B1.0／B2.0「轉換值域統一」、v1.2.2 §4.1a）。"""
from __future__ import annotations

import math

import numpy as np
import pytest

from iching.score.transform import (L, Ind, Missing, N, P_hist, S, S_LO, S_HI, S_RANGE, S_clip, clip_3d, normalize,
                                    OVERHEAT_CAP, REVENUE_HIGH_FLOOR, percentile_threshold, scenario,
                                    scenario_value_after_N)


def test_S_center_is_50_and_d_is_50_to_70():
    assert S(0.0, 0.0, 1.0) == 50.0
    assert S(2.5, 2.5, 0.7) == 50.0
    # d 的定義：分數由 50 升到 70 所需的 x 變化（v1.2.2 §4.1）
    assert S(1.0, 0.0, 1.0) == pytest.approx(70.0)
    assert S(0.0 + 0.3, 0.0, 0.3) == pytest.approx(70.0)
    assert S(-1.0, 0.0, 1.0) == pytest.approx(30.0)
    assert S(1.0, 0.0, 1.0) == pytest.approx(100.0 / (1.0 + math.exp(-math.log(7 / 3))))


def test_clip_3d_reachable_range_7_30_to_92_70():
    for c, d in ((0.0, 1.0), (1.0, 0.3), (0.0, 5000.0), (-2.0, 0.42)):
        assert S(clip_3d(c + 100 * d, c, d), c, d) == pytest.approx(92.70, abs=0.005)
        assert S(clip_3d(c - 100 * d, c, d), c, d) == pytest.approx(7.30, abs=0.005)
        assert clip_3d(c + 100 * d, c, d) == c + 3 * d
        assert clip_3d(c + 2 * d, c, d) == c + 2 * d
    # k·3d = 3·ln(7/3) ⇒ 100/(1+(3/7)^3) = 34300/370
    assert S(3.0, 0.0, 1.0) == pytest.approx(34300 / 370)


def test_S_clip_returns_Ind_with_flags_and_reverse():
    r = S_clip(10.0, 0.0, 1.0)
    assert isinstance(r, Ind) and r.clipped and r.native_range == S_RANGE and r.x == 10.0
    assert r.native == pytest.approx(92.70, abs=0.005)
    assert not S_clip(1.0, 0.0, 1.0).clipped
    # 反向：c=0 時 S(−x) = 100 − S(x)
    assert S_clip(1.0, 0.0, 1.0, direction=-1).native == pytest.approx(100 - S_clip(1.0, 0.0, 1.0).native)
    assert S_clip(2.0, 0.0, 2.0, direction=-1).native == pytest.approx(30.0)


def test_S_rejects_bad_d():
    with pytest.raises(ValueError):
        S(0.0, 0.0, 0.0)
    with pytest.raises(ValueError):
        S(0.0, 0.0, -1.0)


def test_N_midpoint_is_exactly_50_and_endpoints():
    assert N(50.0, 0.0, 100.0) == 50.0
    assert N(50.0, 10.0, 90.0) == 50.0
    assert N(0.0, 0.0, 100.0) == pytest.approx(7.30)
    assert N(100.0, 0.0, 100.0) == pytest.approx(92.70)
    assert 7.30 + 0.5 * 85.40 == 50.0


@pytest.mark.parametrize("v,expected", [(20, 24.38), (25, 28.65), (35, 37.19), (40, 41.46), (50, 50.00), (55, 54.27), (60, 58.54), (80, 75.62)])
def test_N_scenario_table(v, expected):
    assert scenario_value_after_N(v) == pytest.approx(expected, abs=0.005)


def test_N_of_L_anchors():
    assert N(20, 10, 90) == pytest.approx(17.98, abs=0.005)
    assert N(50, 10, 90) == pytest.approx(50.00, abs=1e-9)
    assert N(80, 10, 90) == pytest.approx(82.03, abs=0.005)


def test_direct_constants_after_N():
    assert REVENUE_HIGH_FLOOR == pytest.approx(84.16, abs=0.005)   # B2.1 創高下限 90 → 84.16
    assert OVERHEAT_CAP == pytest.approx(79.89, abs=0.005)         # B2.3 過熱封頂 85 → 79.89


def test_L_anchors_and_clamp_and_declared_range():
    assert L(0.0, 0.0, 0.5, 1.0).native == 20.0
    assert L(0.5, 0.0, 0.5, 1.0).native == 50.0
    assert L(1.0, 0.0, 0.5, 1.0).native == 80.0
    assert L(0.25, 0.0, 0.5, 1.0).native == 35.0
    assert L(5.0, 0.0, 0.5, 1.0).native == 90.0     # 夾在 [10, 90]
    assert L(-5.0, 0.0, 0.5, 1.0).native == 10.0
    assert L(0.5, 0.0, 0.5, 1.0).native_range == (10.0, 90.0)
    with pytest.raises(ValueError):
        L(0.0, 0.5, 0.5, 1.0)


def test_normalize_identity_for_S_range_and_single_application():
    ind = S_clip(0.7, 0.0, 1.0)
    assert normalize(ind) == ind.native                    # 恆等映射（不重複套用）
    assert normalize(L(1.0, 0, .5, 1)) == pytest.approx(82.03, abs=0.005)
    assert normalize(scenario(80)) == pytest.approx(75.62, abs=0.005)


def test_reachable_ranges_of_formula_scenarios():
    """政策第 5 點對照表：B2.4 序 2 [60, 81.35]→[58.54, 76.77]、序 3 [18.65, 40]→[23.23, 41.46]；
    B2.5 族 D 序 3 [7.30, 47.88]、序 4 [51.06, 71.35]（恆等）。"""
    s_max = S_clip(100.0, 0.3, 0.7).native
    s_at_13 = S_clip(0.3, 0.3, 0.7).native            # 序 2／3 的條件為量比 ≥ 1.3 → S ≥ 50
    assert s_at_13 == 50.0
    assert 60 + 0.5 * (s_max - 50) == pytest.approx(81.35, abs=0.005)
    assert 60 + 0.5 * (s_at_13 - 50) == 60.0
    assert 40 - 0.5 * (s_max - 50) == pytest.approx(18.65, abs=0.005)
    assert 40 - 0.5 * (s_at_13 - 50) == 40.0
    assert N(81.35, 0, 100) == pytest.approx(76.77, abs=0.005)
    assert N(18.65, 0, 100) == pytest.approx(23.23, abs=0.005)
    # 融資：r=0.5% → S(−0.5; 0, 5)
    assert S_clip(-0.5, 0.0, 5.0).native == pytest.approx(47.88, abs=0.005)
    assert 50 + 0.5 * (S_clip(0.5, 0.0, 5.0).native - 50) == pytest.approx(51.06, abs=0.005)
    assert 50 + 0.5 * (S_clip(100.0, 0.0, 5.0).native - 50) == pytest.approx(71.35, abs=0.005)


def test_P_hist_conventions():
    assert isinstance(P_hist(np.ones(249), 250), Missing)
    const = P_hist(np.ones(250), 250)
    assert const.native == 50.0 and const.native_range == (0.0, 100.0)
    ramp = P_hist(np.arange(250.0), 250)
    assert ramp.native == pytest.approx(100 - 50 / 250)
    assert P_hist(np.r_[np.arange(249.0) + 1, 0.0], 250).native == pytest.approx(50 / 250)
    thr = percentile_threshold(np.arange(250.0), 80, 250)
    assert thr == pytest.approx(np.percentile(np.arange(250.0), 80))


def test_Missing_is_falsy_and_not_numeric():
    m = Missing("denominator_zero", "ATR=0")
    assert not m
    with pytest.raises(TypeError):
        _ = m + 1.0
    with pytest.raises(TypeError):
        _ = 0.5 * m
