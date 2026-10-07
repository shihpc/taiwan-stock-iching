"""`iching.stats.verdict`：參數矩陣（S1-7）。`n_blocks < 8` 一律 insufficient、與其他輸入無關。
突變對照（S1-9）：`BLOCKS_MIN` 改 4 → `test_insufficient_dominates` 紅（n_blocks=7 會變成 adopted）。"""
from __future__ import annotations

import itertools
import math

from iching.stats import constants
from iching.stats import verdict as V

NAN = float("nan")
GOOD = {"ic_mean": 0.05, "nw_t": 3.0, "pos_share": 0.7, "excess_mean": 0.01, "ci_lo": 0.002, "ci_hi": 0.02,
        "segments_same_sign": True, "years_same_sign": True, "cost_flips": False}


def test_constants_pinned():
    assert (constants.IC_MIN, constants.T_MIN, constants.POS_SHARE_MIN, constants.BLOCKS_MIN,
            constants.EMBARGO_DAYS) == (0.03, 2.0, 0.60, 8, 20)


def test_insufficient_dominates():
    for nb in (0.0, 2.57, 5.47, 7.0, 7.99, NAN):
        assert V.verdict(nb, **GOOD) == "insufficient", nb
        bad = dict(GOOD, ic_mean=-1.0, nw_t=-9.0, pos_share=0.0, cost_flips=True)
        assert V.verdict(nb, **bad) == "insufficient", nb
    assert V.verdict(8.0, **GOOD) == "adopted"
    assert V.verdict(11.27, **GOOD) == "adopted"


def test_boundaries_inclusive():
    assert V.verdict(8.0, **dict(GOOD, ic_mean=0.03, nw_t=2.0, pos_share=0.60)) == "adopted"
    assert V.verdict(8.0, **dict(GOOD, ic_mean=0.0299)) == "rejected"
    assert V.verdict(8.0, **dict(GOOD, nw_t=1.99)) == "rejected"
    assert V.verdict(8.0, **dict(GOOD, pos_share=0.59)) == "rejected"


def test_primary_pass_secondary_fail_is_rejected():
    assert V.verdict(10.0, **dict(GOOD, pos_share=0.5)) == "rejected"
    assert V.verdict(10.0, **dict(GOOD, ci_lo=-0.001)) == "rejected", "區間含 0"
    assert V.verdict(10.0, **dict(GOOD, excess_mean=-0.01, ci_lo=-0.02, ci_hi=-0.001)) == "rejected", "超額為負"
    assert V.verdict(10.0, **dict(GOOD, segments_same_sign=False)) == "rejected"
    assert V.verdict(10.0, **dict(GOOD, years_same_sign=False)) == "rejected"
    assert V.verdict(10.0, **dict(GOOD, cost_flips=True)) == "rejected"


def test_full_matrix_is_and_of_three_classes():
    """三類各兩態的 2³ 矩陣：只有三類全過才 adopted。"""
    prim = {True: {"ic_mean": 0.05, "nw_t": 3.0}, False: {"ic_mean": 0.05, "nw_t": 1.0}}
    sec = {True: {"pos_share": 0.7, "excess_mean": 0.01, "ci_lo": 0.002, "ci_hi": 0.02},
           False: {"pos_share": 0.7, "excess_mean": 0.01, "ci_lo": -0.002, "ci_hi": 0.02}}
    rob = {True: {"segments_same_sign": True, "years_same_sign": True, "cost_flips": False},
           False: {"segments_same_sign": True, "years_same_sign": True, "cost_flips": True}}
    for p, s, r in itertools.product((True, False), repeat=3):
        got = V.verdict(9.0, **prim[p], **sec[s], **rob[r])
        assert got == ("adopted" if (p and s and r) else "rejected"), (p, s, r)


def test_nan_never_passes():
    for k in ("ic_mean", "nw_t", "pos_share", "excess_mean", "ci_lo", "ci_hi"):
        assert V.verdict(9.0, **dict(GOOD, **{k: NAN})) == "rejected", k
    assert not V.primary_pass(NAN, 3.0) and not V.secondary_pass(0.7, 0.01, NAN, 0.02)
    assert math.isnan(NAN)
