"""`iching.factor_sources`：四源欄位對映與跨源合併（裁定 #51，2026-09-18）。純函式、免 DB。

合併規則的機器守門（`docs/P3-DATASET.md` §7.3 C）：每源 keep-first／壞值跳過、split∪parvalue 去重優先 split、
dividend／capred／(split∪parvalue) 同日各留（→ `build_factors` 相乘）、決定性排序、按源 band 異常只報不擋。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from iching import factor_sources as FS  # noqa: E402
from iching.adjust import ADJUST_SOURCES, FACTOR_BAND  # noqa: E402


def test_source_table_and_column_mapping_matches_probe():
    """欄名＝Hetzner 2026-09-18 探測 P4；表名＝`config.DatasetSpec.table`；來源集合＝`adjust.FACTOR_BAND` 的鍵。"""
    from iching import config as C
    assert [s.source for s in FS.SOURCES] == ["dividend", "capred", "split", "parvalue"]
    assert FS.SOURCE_BY_NAME["capred"][3:] == ("ClosingPriceonTheLastTradingDay", "PostReductionReferencePrice")
    assert FS.SOURCE_BY_NAME["split"][3:] == ("before_price", "after_price")
    assert FS.SOURCE_BY_NAME["parvalue"][3:] == ("before_close", "after_ref_close")
    for s in FS.SOURCES:
        assert C.DATASET_BY_KEY[s.key].table == s.table and C.DATASET_BY_KEY[s.key].db == "prices"
    assert set(FS.SOURCE_BY_NAME) == set(FACTOR_BAND) and FS.ALIAS_PAIR == ("split", "parvalue")
    assert C.DATASET_BY_KEY["par_value_change"].fallback is None        # 不接受 data_id → 不得配 per_stock


def test_normalize_rows_maps_columns_and_drops_keyless():
    rows = [{"stock_id": "3095", "date": "2022-10-31", "ClosingPriceonTheLastTradingDay": 2.77, "PostReductionReferencePrice": 30.27},
            {"stock_id": None, "date": "2022-10-31", "ClosingPriceonTheLastTradingDay": 1, "PostReductionReferencePrice": 1},
            {"stock_id": "3095", "date": "", "ClosingPriceonTheLastTradingDay": 1, "PostReductionReferencePrice": 1},
            {"stock_id": "3095", "date": "2022-10-31", "ClosingPriceonTheLastTradingDay": 9.9, "PostReductionReferencePrice": 30.27}]
    assert FS.normalize_rows("capred", rows) == [("3095", "2022-10-31", 9.9, 30.27)]      # 同鍵後者覆蓋（同每日班除權息）
    assert FS.normalize_rows("parvalue", [{"stock_id": "6763", "date": "2024-09-09", "before_close": 491, "after_ref_close": 49.1}]) \
        == [("6763", "2024-09-09", 491, 49.1)]


def test_merge_keep_first_and_bad_values_per_source():
    """每源內同 (stock_id, date) keep-first；非數／≤0／NaN 跳過且**不佔鍵**（其後同鍵的好列仍進），與舊 `feed.load_factors` 同。"""
    rows, st = FS.merge_factor_rows({"dividend": [("1101", "2024-07-01", 34.2, 33.2), ("1101", "2024-07-01", 99.0, 1.0),
                                                  ("2330", "2024-06-13", None, 500.0), ("2330", "2024-06-13", 510.0, 500.0),
                                                  ("2330", "2024-06-14", 0, 500.0), ("2330", "2024-06-15", "x", 500.0),
                                                  ("2330", "2024-06-16", float("nan"), 500.0), ("2330", None, 1.0, 1.0)]})
    assert rows == [("1101", "2024-07-01", 34.2, 33.2, "dividend"), ("2330", "2024-06-13", 510.0, 500.0, "dividend")]
    assert st["by_source"]["dividend"] == {"rows": 7, "kept": 2, "dup_skipped": 1, "bad_skipped": 4, "anomalies": 0}
    assert st["rows"] == 7 and st["kept"] == 2 and st["cross_source_dup"] == 0 and st["sources"] == ADJUST_SOURCES
    assert all(st["by_source"][s]["rows"] == 0 for s in ("capred", "split", "parvalue"))


def test_split_and_parvalue_same_event_deduped_preferring_split():
    """探測 P6：分割與面額變更是同一事件兩表各一列。同 (stock_id, date) 只留 split 的值；parvalue 獨有的事件保留。"""
    rows, st = FS.merge_factor_rows({"split": [("6415", "2022-07-13", 2485.0, 621.25)],
                                     "parvalue": [("6415", "2022-07-13", 2485.0, 621.0), ("6763", "2024-09-09", 491.0, 49.1)]})
    assert rows == [("6415", "2022-07-13", 2485.0, 621.25, "split"), ("6763", "2024-09-09", 491.0, 49.1, "parvalue")]
    assert st["cross_source_dup"] == 1 and st["by_source"]["parvalue"] == {"rows": 2, "kept": 1, "dup_skipped": 0, "bad_skipped": 0, "anomalies": 0}
    # 反向：parvalue 表沒有、split 有 → 照留；只有 parvalue → 照留（不因為「應該有 split」而丟）
    rows, _ = FS.merge_factor_rows({"parvalue": [("6763", "2024-09-09", 491.0, 49.1)]})
    assert rows == [("6763", "2024-09-09", 491.0, 49.1, "parvalue")]


def test_dividend_and_capred_same_day_both_kept_and_multiply():
    """dividend／capred／(split∪parvalue) 是不同事件：同 (stock_id, date) 各留一列，`build_factors` 相乘（1.25×0.5＝0.625）。
    輸出序＝(stock_id, date, 來源序)，決定性。"""
    rows, st = FS.merge_factor_rows({"capred": [("1101", "2020-03-20", 100.0, 200.0)],
                                     "dividend": [("1101", "2020-03-20", 100.0, 80.0), ("1101", "2020-03-01", 50.0, 49.0)],
                                     "parvalue": [("1101", "2020-03-20", 100.0, 10.0)]})
    assert rows == [("1101", "2020-03-01", 50.0, 49.0, "dividend"), ("1101", "2020-03-20", 100.0, 80.0, "dividend"),
                    ("1101", "2020-03-20", 100.0, 200.0, "capred"), ("1101", "2020-03-20", 100.0, 10.0, "parvalue")]
    assert st["kept"] == 4 and st["cross_source_dup"] == 0
    fac, bst = FS.build_factors(rows)
    assert fac["1101"][0] == ["2020-03-01", "2020-03-20"]
    assert fac["1101"][1][1] == pytest.approx((50 / 49) * 1.25 * 0.5 * 10.0)
    assert bst == {"rows": 4, "bad_skipped": 0, "stocks": 1, "anomalies": 0}


def test_build_factors_does_not_dedupe_merged_rows():
    """`factors.json` 的列是合併後的事件列——同 (stock_id, date) 兩列＝兩個事件、必須相乘。
    突變：把舊的 keep-first 加回 `build_factors` → 這裡的 0.625 會變成 1.25、本測試紅。"""
    fac, st = FS.build_factors([["1101", "2020-03-20", 100.0, 80.0], ["1101", "2020-03-20", 100.0, 200.0]])
    assert fac["1101"] == (["2020-03-20"], [pytest.approx(0.625)]) and st["rows"] == 2
    # 4 欄與 5 欄都收；壞值跳過；聯集 band 外計數（0.01）
    fac, st = FS.build_factors([("1101", "2020-03-20", 100.0, 80.0, "dividend"), ("1101", "2020-03-21", None, 1.0),
                                ("2330", "2020-03-22", 1.0, 100.0), ("2330", None, 1.0, 1.0)])
    assert set(fac) == {"1101", "2330"} and st == {"rows": 3, "bad_skipped": 1, "stocks": 2, "anomalies": 1}


def test_anomalies_use_band_of_own_source_and_are_reported_not_dropped():
    """減資 0.0915 對 capred 正常、面額 10 對 parvalue 正常；除權息 0.5 對 dividend 異常——列仍保留（只報不擋）。"""
    rows, st = FS.merge_factor_rows({"dividend": [("9999", "2021-01-04", 50.0, 100.0)],
                                     "capred": [("3095", "2022-10-31", 2.77, 30.27)],
                                     "parvalue": [("6763", "2024-09-09", 491.0, 49.1)]})
    assert len(rows) == 3 and st["anomalies"] == 1
    assert st["anomaly_rows"] == [("9999", "2021-01-04", 50.0, 100.0, "dividend", 0.5)]
    assert st["by_source"]["dividend"]["anomalies"] == 1 and st["by_source"]["capred"]["anomalies"] == 0
    line = FS.format_source_stat({**st, "missing_tables": ["raw_split_price"], "meta_only_tables": ["raw_par_value_change"]})
    assert "dividend 1／capred 1／split 0／parvalue 1" in line and "band 外 1 筆" in line and "9999 2021-01-04 dividend 0.5000" in line
    assert "缺表視為 0 列：['raw_split_price']" in line and "meta-only 空表視為 0 列：['raw_par_value_change']" in line
    assert "band 外 0 筆" in FS.format_source_stat(FS.merge_factor_rows({})[1])


def test_unknown_source_raises():
    with pytest.raises(ValueError, match="未知的事件源"):
        FS.merge_factor_rows({"dividend": [], "bogus": [("1", "2020-01-01", 1.0, 1.0)]})
