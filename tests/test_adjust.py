"""除權息還原（後復權，裁定甲）。

**所有案例取自 Hetzner `raw_dividend_result` 的真實列**（2026-09-12 dump，10,664 筆全數人工複核）。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from iching.adjust import (FACTOR_MAX, FACTOR_MIN, Event, adjust, anomalies,  # noqa: E402
                           cumulative_factors, event_factor, factor_at)

# 真實列（date, stock_id, before, after, dividend）
E_1101 = Event("2024-07-01", 34.2, 33.2)          # 1101 台泥，配息 1.0
E_2603 = Event("2023-06-30", 155.0, 85.0)         # 2603 長榮，配息 70（著名大配息）
E_5314 = Event("2026-08-14", 61.3, 14.75)         # 5314，配 46.55，全期係數最大
E_3563 = Event("2020-03-27", 237.0, 237.61)       # 3563，現金增資認購價高於市價 → 參考價上調


def test_factor_direction_and_gap_removal() -> None:
    """後復權：除權息日**起**乘上 before/after，假跌被消掉、與前一日銜接。"""
    assert event_factor(34.2, 33.2) == pytest.approx(1.0301205)
    d, c = cumulative_factors([E_1101])
    assert adjust("2024-06-28", 34.2, d, c) == pytest.approx(34.2)   # 事件前不動
    assert adjust("2024-07-01", 33.2, d, c) == pytest.approx(34.2)   # 事件當日已調整，銜接前一日


def test_only_past_events_affect_a_date_point_in_time() -> None:
    """後復權的 PIT 性質：T 日的係數只由 T 日**含當日**以前的事件決定。
    這是選後復權而非前復權的唯一理由——前復權下 T 日會被 T 之後的事件改寫。"""
    d, c = cumulative_factors([E_1101, Event("2025-07-01", 40.0, 38.0)])
    assert factor_at("2024-06-30", d, c) == pytest.approx(1.0)        # 兩個事件都還沒發生
    assert factor_at("2024-07-01", d, c) == pytest.approx(1.0301205)  # 只含第一個
    assert factor_at("2025-06-30", d, c) == pytest.approx(1.0301205)  # 仍只含第一個
    assert factor_at("2025-07-01", d, c) == pytest.approx(1.0301205 * 40 / 38)


def test_same_day_multiple_events_multiply() -> None:
    """同日除權又除息分成兩列時，係數相乘、只留一個日期項。"""
    d, c = cumulative_factors([Event("2024-07-01", 100.0, 90.0), Event("2024-07-01", 90.0, 81.0)])
    assert d == ["2024-07-01"]
    assert c[0] == pytest.approx((100 / 90) * (90 / 81))


def test_real_extremes_are_not_flagged_because_they_are_real() -> None:
    """10,664 筆全數複核：兩端極值都是真實事件，`before − dividend = after` 逐筆吻合。"""
    assert 155.0 - 70.0 == 85.0 and event_factor(*E_2603[1:]) == pytest.approx(1.8235294)
    assert 61.3 - 46.55 == pytest.approx(14.75) and event_factor(*E_5314[1:]) == pytest.approx(4.1559322)
    assert anomalies([E_2603, E_5314]) == []       # 大配息不是異常


def test_price_rising_on_ex_date_is_flagged_but_still_applied() -> None:
    """實查 5 筆 after > before，皆為現金增資認購價高於市價（`stock_and_cache_dividend` 為負）。
    機制正確、照套；`anomalies()` 只是標出來讓人看，不是過濾器。"""
    f = event_factor(*E_3563[1:])
    assert f == pytest.approx(0.9974327)
    assert [e for e, _ in anomalies([E_3563])] == [E_3563]
    d, c = cumulative_factors([E_3563])
    assert adjust("2020-03-27", 237.61, d, c) == pytest.approx(237.0)   # 照樣套用、照樣銜接


def test_the_band_is_not_inverted() -> None:
    """初版把上下限寫反（MIN 0.2／MAX 1.0），**每一筆正常事件都會被標成異常**。
    正常除權息價格下跌 → factor ≥ 1，故 MIN 才是 1.0。"""
    assert FACTOR_MIN == 1.0 and FACTOR_MAX == 5.0
    assert anomalies([E_1101]) == []                                    # 正常事件不得被標
    assert len(anomalies([E_3563])) == 1                                # 價格上漲的才標


def test_bad_after_price_raises() -> None:
    for bad in (0, -1.0):
        with pytest.raises(ValueError):
            event_factor(34.2, bad)


def test_none_price_passes_through() -> None:
    d, c = cumulative_factors([E_1101])
    assert adjust("2024-07-01", None, d, c) is None
