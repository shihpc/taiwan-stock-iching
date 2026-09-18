"""價格還原（後復權，裁定甲；裁定 #51 擴為四源）。

**除權息案例取自 Hetzner `raw_dividend_result` 的真實列**（2026-09-12 dump，10,664 筆全數人工複核）；
減資／分割／面額變更案例取自 2026-09-18 Hetzner 探測（`docs/P3-DATASET.md` §7.2 P3）。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from iching.adjust import (ADJUST_SOURCES, FACTOR_BAND, FACTOR_BAND_ANY, Event, adjust,  # noqa: E402
                           anomalies, cumulative_factors, event_factor, factor_at)

# 真實列（date, stock_id, before, after, dividend）
E_1101 = Event("2024-07-01", 34.2, 33.2)          # 1101 台泥，配息 1.0
E_2603 = Event("2023-06-30", 155.0, 85.0)         # 2603 長榮，配息 70（著名大配息）
E_5314 = Event("2026-08-14", 61.3, 14.75)         # 5314，配 46.55，全期係數最大
E_3563 = Event("2020-03-27", 237.0, 237.61)       # 3563，現金增資認購價高於市價 → 參考價上調
# 裁定 #51 三源（Hetzner 2026-09-18 探測 P3：date＝恢復買賣日，前一交易日 close＝before、當日 close≈after）
E_3095_CAPRED = Event("2022-10-31", 2.77, 30.27)  # 3095 減資：ClosingPriceonTheLastTradingDay／PostReductionReferencePrice → 0.0915
E_2364_CAPRED = Event("2021-10-08", 3.04, 24.01)  # 2364 減資 → 0.127
E_6415_SPLIT = Event("2022-07-13", 2485.0, 621.25)   # 6415 1→4 分割（before_price／after_price）→ 4
E_6763_PAR = Event("2024-09-09", 491.0, 49.1)     # 6763 面額 10→1（before_close／after_ref_close）→ 10


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


def test_price_rising_on_ex_date_is_applied_and_only_flagged_beyond_band() -> None:
    """實查 5 筆 after > before（0.9974~0.9998），皆為現金增資認購價高於市價（`stock_and_cache_dividend` 為負）。
    機制正確、照套。2026-09-18 裁定 #51 把 dividend band 下限由 1.0 放到 0.99（這 5 筆已人工複核為真實事件，
    不必每次 log 再列一次），**再往下**（除權息日漲逾 1%）才標；`anomalies()` 只是標出來讓人看，不是過濾器。"""
    f = event_factor(*E_3563[1:])
    assert f == pytest.approx(0.9974327)
    assert anomalies([E_3563]) == []
    assert [e for e, _ in anomalies([Event("2020-03-27", 237.0, 240.0)])] == [Event("2020-03-27", 237.0, 240.0)]
    d, c = cumulative_factors([E_3563])
    assert adjust("2020-03-27", 237.61, d, c) == pytest.approx(237.0)   # 照樣套用、照樣銜接


def test_the_band_is_not_inverted() -> None:
    """初版把上下限寫反（MIN 0.2／MAX 1.0），**每一筆正常事件都會被標成異常**。
    正常除權息價格下跌 → factor ≥ 1，故 dividend 的下限在 1 附近（0.99 容納 5 筆現金增資參考價上調）。"""
    assert FACTOR_BAND["dividend"] == (0.99, 5.0)
    assert anomalies([E_1101]) == []                                    # 正常事件不得被標
    assert anomalies([E_3563]) == []                                    # 0.9974 落在 0.99 內：實查 5 筆皆真實，不再標
    assert len(anomalies([Event("2020-01-02", 90.0, 100.0)])) == 1      # 除權息日漲逾 1% 才標
    assert len(anomalies([Event("2020-01-02", 100.0, 10.0)])) == 1      # 跌逾 80%（係數 10）才標


def test_bands_per_source_and_sources_version() -> None:
    """裁定 #51：按源 band——減資係數 <1（`[0.02, 1.01]`）、分割／面額變更＝倍數（`[1.5, 12.0]`）；四源聯集 `[0.02, 12.0]`。"""
    assert ADJUST_SOURCES == "div+capred+split+par-1"
    assert set(FACTOR_BAND) == {"dividend", "capred", "split", "parvalue"}
    assert FACTOR_BAND["capred"] == (0.02, 1.01) and FACTOR_BAND["split"] == FACTOR_BAND["parvalue"] == (1.5, 12.0)
    assert FACTOR_BAND_ANY == (0.02, 12.0)
    # 減資 0.0915／0.127：capred 正常、dividend 會標（拿錯 band 就會誤報，這正是按源分段的理由）
    assert event_factor(*E_3095_CAPRED[1:]) == pytest.approx(0.091510, abs=1e-6)
    assert event_factor(*E_2364_CAPRED[1:]) == pytest.approx(0.126614, abs=1e-6)
    assert anomalies([E_3095_CAPRED, E_2364_CAPRED], "capred") == []
    assert len(anomalies([E_3095_CAPRED], "dividend")) == 1
    # 分割 4／面額 10：split／parvalue 正常、dividend band（上限 5）對面額變更會標
    assert event_factor(*E_6415_SPLIT[1:]) == pytest.approx(4.0) and event_factor(*E_6763_PAR[1:]) == pytest.approx(10.0)
    assert anomalies([E_6415_SPLIT], "split") == [] and anomalies([E_6763_PAR], "parvalue") == []
    assert len(anomalies([E_6763_PAR], "dividend")) == 1
    assert anomalies([E_6763_PAR, E_3095_CAPRED], None) == []           # 聯集 band：不知來源時只抓離譜值
    assert len(anomalies([Event("2020-01-02", 1.0, 100.0)], None)) == 1  # 0.01 < 0.02
    with pytest.raises(ValueError, match="未知的事件源"):
        anomalies([E_1101], "bogus")


def test_capital_reduction_factor_removes_resumption_jump() -> None:
    """減資恢復買賣日 raw 由 2.77 跳到 30.0（3095 2022-10-31）：後復權從恢復日起乘 0.0915，30.0 → 2.745 與停牌前 2.77 銜接
    （差的是參考價 30.27 與當日實際收盤 30.0 之間的真實漲跌）；恢復日之前不動。"""
    d, c = cumulative_factors([E_3095_CAPRED])
    assert adjust("2022-10-13", 2.77, d, c) == pytest.approx(2.77)
    assert adjust("2022-10-31", 30.0, d, c) == pytest.approx(30.0 * 2.77 / 30.27)
    assert adjust("2022-10-31", 30.27, d, c) == pytest.approx(2.77)


def test_split_and_par_value_factors_are_multiples() -> None:
    d, c = cumulative_factors([E_6415_SPLIT])
    assert adjust("2022-07-13", 621.25, d, c) == pytest.approx(2485.0) and adjust("2022-07-12", 2485.0, d, c) == pytest.approx(2485.0)
    d, c = cumulative_factors([E_6763_PAR])
    assert adjust("2024-09-09", 49.1, d, c) == pytest.approx(491.0)


def test_same_day_dividend_and_capred_multiply() -> None:
    """同日除息（1.25）與減資（0.5）是兩個事件 → `cumulative_factors` 相乘＝0.625、只留一個日期項。"""
    d, c = cumulative_factors([Event("2020-03-20", 100.0, 80.0), Event("2020-03-20", 100.0, 200.0)])
    assert d == ["2020-03-20"] and c[0] == pytest.approx(0.625)


def test_bad_after_price_raises() -> None:
    for bad in (0, -1.0):
        with pytest.raises(ValueError):
            event_factor(34.2, bad)


def test_none_price_passes_through() -> None:
    d, c = cumulative_factors([E_1101])
    assert adjust("2024-07-01", None, d, c) is None
