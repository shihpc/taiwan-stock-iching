"""PR-S1 審查遺留的三個守門（S2-12）＋裁定 #72 Q17 的平均秩切組（PR-S2 新增純函式）。期待值獨立寫死。"""
from __future__ import annotations

import numpy as np
import pytest

from iching.stats import boot, groups, metrics, windows


def test_monthly_agg_rejects_non_month_keys():
    with pytest.raises(ValueError, match="YYYY-MM"):
        metrics.monthly_agg(["20240101", "20240102"], [1.0, 2.0])
    with pytest.raises(ValueError):
        metrics.monthly_agg([5, 6], [1.0, 2.0])                       # 整數日曆索引不是月桶鍵
    months, vals = metrics.monthly_agg(["2024-01-03", "2024-01-04", "2024-02-01"], [1.0, 3.0, 5.0])
    assert months.tolist() == ["2024-01", "2024-02"] and vals.tolist() == [2.0, 5.0]


def test_boot_ci_rejects_nonpositive_nboot():
    for n in (0, -1):
        with pytest.raises(ValueError, match="nboot"):
            boot.boot_ci(np.arange(100.0), h=10, nboot=n)
    lo, hi = boot.boot_ci(np.full(80, 0.02), h=10, nboot=5)
    assert lo == hi == pytest.approx(0.02)


def test_segment_bounds_rejects_empty_segment():
    cal = ["2024-01-02", "2024-01-03", "2024-01-08"]
    with pytest.raises(ValueError, match="空段"):
        windows.segment_bounds(cal, "2024-01-04", "2024-01-05")       # 兩日期之間沒有交易日
    with pytest.raises(ValueError):
        windows.segment_bounds(cal, "2024-01-09", "2024-01-01")        # end < start 仍拒收
    assert windows.segment_bounds(cal, "2024-01-03", "2024-01-08") == (1, 3)


def test_group_labels_avg_rank_ties_same_group():
    # 無同分：與 group_labels 逐位相同
    x = np.array([5.0, 1.0, 3.0, 2.0, 4.0])
    assert groups.group_labels_avg_rank(x, 5).tolist() == groups.group_labels(x, 5).tolist() == [4, 0, 2, 1, 3]
    # 四個同分、切兩組：group_labels 依列序切成 [0,0,1,1]（PR-S1 既有行為），平均秩版本四列同組
    t = [1.0, 1.0, 1.0, 1.0]
    assert groups.group_labels(t, 2).tolist() == [0, 0, 1, 1]
    assert len(set(groups.group_labels_avg_rank(t, 2).tolist())) == 1
    # 同分跨越邊界：[1,2,2,3]、切 2 組 → 兩個 2 的平均秩 2.5 → floor((2.5−1)×2/4)=0 → 同組（不由列序決定）
    assert groups.group_labels_avg_rank([1.0, 2.0, 2.0, 3.0], 2).tolist() == [0, 0, 0, 1]
    assert groups.group_labels_avg_rank([3.0, 2.0, 1.0, 2.0], 2).tolist() == [1, 0, 0, 0]
    assert groups.group_labels_avg_rank([], 10).size == 0
    with pytest.raises(ValueError):
        groups.group_labels_avg_rank([1.0, np.nan], 2)
    # 100 個不同值切 10 組：每組 10 個
    g = groups.group_labels_avg_rank(np.random.default_rng(1).permutation(100).astype(float), 10)
    assert np.bincount(g).tolist() == [10] * 10


def test_tie_share_and_spread_series_label_fn():
    assert groups.tie_share([1.0, 2.0, 2.0, 3.0]) == pytest.approx(0.5)
    assert groups.tie_share([1.0, 2.0, 3.0]) == 0.0
    assert np.isnan(groups.tie_share([]))
    dates = ["2024-01-02"] * 4
    score, ret = [1.0, 2.0, 2.0, 3.0], [0.1, 0.2, 0.3, 0.4]
    a = groups.spread_series(dates, score, ret, n_groups=2)
    b = groups.spread_series(dates, score, ret, n_groups=2, label_fn=groups.group_labels_avg_rank)
    # 列序版：頂組 {2(第二個),3}、底組 {1,2(第一個)} → 0.35−0.15；平均秩版：頂組 {3}、底組 {1,2,2} → 0.4−0.2
    assert a.value.tolist() == pytest.approx([0.35 - 0.15]) and b.value.tolist() == pytest.approx([0.4 - 0.2])
