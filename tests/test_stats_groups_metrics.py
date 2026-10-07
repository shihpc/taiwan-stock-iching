"""`iching.stats.groups`／`metrics`：分位組頂減底與月度正比例（§D.3 `test_decile_spread_and_monthly_share`）、
名單超額、只報三項。期待值獨立寫死。"""
from __future__ import annotations

import math

import numpy as np
import pytest

from iching.stats import groups, metrics


def test_group_labels_ordinal():
    assert groups.group_labels([5, 1, 3, 2, 4], n_groups=5).tolist() == [4, 0, 2, 1, 3]
    g = groups.group_labels(np.arange(100), n_groups=10)
    assert g[:10].tolist() == [0] * 10 and g[90:].tolist() == [9] * 10 and g[57] == 5
    assert groups.group_labels([1, 1, 1, 1], n_groups=2).tolist() == [0, 0, 1, 1], "同分依輸入順序（stable）"
    with pytest.raises(ValueError):
        groups.group_labels([1.0, np.nan], 2)


def test_decile_spread_and_monthly_share():
    """3 個月、各 2 日、每日 20 列：月 1／月 2 頂組報酬高於底組、月 3 相反 → 正比例 2/3。"""
    dates, score, ret = [], [], []
    for m, sign in (("2023-07", 1.0), ("2023-08", 1.0), ("2023-09", -1.0)):
        for day in ("03", "17"):
            for k in range(20):
                dates.append(f"{m}-{day}")
                score.append(float(k))
                ret.append(sign * (0.01 if k >= 18 else (-0.01 if k < 2 else 0.0)))
    sp = groups.spread_series(dates, score, ret, n_groups=10)
    assert sp.dates.size == 6 and sp.dropped == {"nan_rows": 0, "days_dropped": 0}
    assert np.allclose(sp.value, [0.02, 0.02, 0.02, 0.02, -0.02, -0.02])
    share, pos, n = metrics.monthly_positive_share(sp.dates, sp.value)
    assert (pos, n) == (2, 3) and abs(share - 2 / 3) < 1e-15
    assert abs(sp.mean - 0.02 / 3) < 1e-15


def test_spread_series_drops():
    dates = ["a"] * 3 + ["b"] * 2
    sp = groups.spread_series(dates, [1, 2, 3, 1, 2], [0.1, np.nan, 0.3, 0.1, 0.2], n_groups=2, min_n=2)
    assert sp.dates.tolist() == ["a", "b"] and np.allclose(sp.value, [0.2, 0.1])
    assert sp.dropped == {"nan_rows": 1, "days_dropped": 0}
    sp = groups.spread_series(["a"] * 2, [1, 2], [0.1, 0.2], n_groups=10)   # 2 列切 10 組：頂組空
    assert sp.dates.size == 0 and sp.dropped["days_dropped"] == 1 and math.isnan(sp.mean)


def test_excess_series():
    dates = ["a"] * 4 + ["b"] * 4 + ["c"] * 2
    ret = [0.1, 0.2, 0.3, 0.4, -0.1, 0.0, 0.1, 0.2, 0.5, 0.5]
    member = [True, True, False, False, False, False, False, True, False, False]
    ex = groups.excess_series(dates, ret, member)
    assert ex.dates.tolist() == ["a", "b"] and np.allclose(ex.value, [0.15 - 0.25, 0.2 - 0.05])
    assert ex.dropped == {"nan_rows": 0, "days_dropped": 1}
    with pytest.raises(ValueError):
        groups.excess_series(dates, ret, member[:-1])


def test_sharpe_mdd_winrate():
    x = [0.01, -0.02, 0.03, 0.0]
    m, sd = 0.005, math.sqrt(((0.005) ** 2 + 0.025 ** 2 + 0.025 ** 2 + 0.005 ** 2) / 3)
    assert abs(metrics.sharpe(x) - m / sd) < 1e-15
    assert abs(metrics.sharpe(x, 245) - m / sd * math.sqrt(245)) < 1e-12
    assert math.isnan(metrics.sharpe([1.0])) and math.isnan(metrics.sharpe([2.0, 2.0]))
    # 權益 1 → 1.1 → 0.88 → 0.968 → 1.0648：最大回撤自 1.1 到 0.88 ＝ 20%
    assert abs(metrics.max_drawdown([0.1, -0.2, 0.1, 0.1]) - 0.2) < 1e-15
    assert abs(metrics.max_drawdown([0.1, -0.2, 0.1, 0.1], compound=False) - 0.2) < 1e-15
    assert metrics.max_drawdown([0.1, 0.1]) == 0.0 and math.isnan(metrics.max_drawdown([]))
    dates = ["2024-01-02", "2024-01-03", "2024-02-01", "2024-02-02", "2024-03-01"]
    assert abs(metrics.monthly_winrate(dates, [0.1, -0.05, -0.1, 0.05, 0.0]) - 1 / 3) < 1e-15
    months, vals = metrics.monthly_agg(dates, [1, 3, 5, 7, 9], "sum")
    assert months.tolist() == ["2024-01", "2024-02", "2024-03"] and vals.tolist() == [4.0, 12.0, 9.0]
    share, pos, n = metrics.monthly_positive_share([], [])
    assert math.isnan(share) and (pos, n) == (0, 0)
    with pytest.raises(ValueError):
        metrics.monthly_agg(dates, [1, 2], "mean")
