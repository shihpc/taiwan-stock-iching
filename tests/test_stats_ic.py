"""`iching.stats.ic`：平均秩 Spearman 手算案例（S1-3）、逐日 IC 與已知答案（S1-8）。期待值獨立寫死。"""
from __future__ import annotations

import math

import numpy as np
import pytest

from iching.stats import ic

#: 合成 ret = 0.5·score + noise（兩者標準常態）：Pearson ρ = 0.5/√1.25，雙變量常態的 Spearman ＝ (6/π)·asin(ρ/2)
#: ＝ 0.43069887938…（手算；獨立於被測函式）。
SPEARMAN_ANALYTIC = 0.4306988793861188


def test_average_rank_ties():
    assert ic.average_rank([1, 2, 2, 3, 4]).tolist() == [1.0, 2.5, 2.5, 4.0, 5.0]
    assert ic.average_rank([5, 5, 5]).tolist() == [2.0, 2.0, 2.0]
    assert ic.average_rank([3, 1, 2]).tolist() == [3.0, 1.0, 2.0]
    assert ic.average_rank([]).size == 0
    with pytest.raises(ValueError):
        ic.average_rank([1.0, float("nan")])


def test_spearman_hand_cases():
    assert ic.spearman([1, 2, 3, 4, 5], [10, 20, 30, 40, 50]) == 1.0
    assert ic.spearman([1, 2, 3, 4, 5], [50, 40, 30, 20, 10]) == -1.0
    # 5 元素含 tie：a 秩 [1,2.5,2.5,4,5]、b 秩 [1..5] → 9.5/√(9.5×10) ＝ √0.95
    assert abs(ic.spearman([1, 2, 2, 3, 4], [10, 20, 30, 40, 50]) - 0.9746794344808963) < 1e-12
    # 無 tie：Σd² ＝ 36 → 1 − 6·36/(5·24) ＝ −0.8
    assert abs(ic.spearman([1, 2, 3, 4, 5], [5, 3, 4, 1, 2]) - (-0.8)) < 1e-12
    assert math.isnan(ic.spearman([1, 1, 1], [1, 2, 3]))      # 常數秩
    assert math.isnan(ic.spearman([1], [2]))
    with pytest.raises(ValueError):
        ic.spearman([1, 2], [1, 2, 3])


def test_daily_ic_known_answer_synthetic():
    rng = np.random.default_rng(20261007)
    n_days, n_per_day = 250, 400
    score = rng.standard_normal((n_days, n_per_day))
    ret = 0.5 * score + rng.standard_normal((n_days, n_per_day))
    dates = np.repeat(np.arange(n_days), n_per_day)
    r = ic.daily_ic(dates, score.ravel(), ret.ravel(), min_n=30)
    assert r.ic.size == n_days and r.n.tolist() == [n_per_day] * n_days
    assert r.dropped == {"nan_pairs": 0, "days_below_min_n": 0, "days_degenerate": 0}
    assert abs(r.mean - SPEARMAN_ANALYTIC) < 0.01
    # 日序列均值＝各日 Spearman 的等權均值（與 spearman 逐日一致）
    assert abs(r.ic[7] - ic.spearman(score[7], ret[7])) < 1e-15


def test_daily_ic_drops_nan_pairs_and_small_days():
    dates = np.array(["d1"] * 5 + ["d2"] * 2 + ["d3"] * 4 + ["d4"] * 3)
    score = np.array([1, 2, 3, 4, 5, 1, 2, 1, 2, 3, 4, 7, 7, 7], float)
    ret = np.array([1, 2, 3, 4, 5, 1, 2, 4, 3, 2, np.nan, 1, 2, 3], float)
    r = ic.daily_ic(dates, score, ret, min_n=3)
    assert r.dates.tolist() == ["d1", "d3"]
    assert r.ic.tolist() == [1.0, -1.0]
    assert r.n.tolist() == [5, 3]
    assert r.dropped == {"nan_pairs": 1, "days_below_min_n": 1, "days_degenerate": 1}


def test_daily_ic_counts_days_fully_eaten_by_nan():
    dates = ["a", "a", "a", "b", "b", "b"]
    r = ic.daily_ic(dates, [1, 2, 3, 1, 2, 3], [1, 2, 3, np.nan, np.nan, np.nan], min_n=2)
    assert r.dates.tolist() == ["a"] and r.dropped == {"nan_pairs": 3, "days_below_min_n": 1, "days_degenerate": 0}
    assert math.isnan(ic.daily_ic([], [], [], 1).mean)
    with pytest.raises(ValueError):
        ic.daily_ic(["a"], [1, 2], [1, 2], 1)
