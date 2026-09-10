"""P2 回補腳本的離線測試：免 token、免網路。

覆蓋：日期切分、PIT 池過濾、coverage 不被失敗污染、交易日曆生成、美股時間對齊、
請求計畫、FinMind 回應分類、token 遮蔽、TWSE 月表解析與開盤比對、data_version 格式。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from iching import calendar as cal  # noqa: E402
from iching import config as C  # noqa: E402
from iching import plan as P  # noqa: E402
from iching import twse as T  # noqa: E402
from iching.fm import FinMind, PermissionRequired, QuotaExceeded, TransientError, classify_response, load_token, redact  # noqa: E402
from iching.store import Store, row_hash, safe_col  # noqa: E402
from iching.universe import is_pool_candidate, pit_pool, pool_from_info  # noqa: E402


# ---------------------------------------------------------------------------
# config：切分／暖機／data_version
# ---------------------------------------------------------------------------
def test_segments_ruling_2026_09_09():
    assert C.SEGMENTS["train"] == ("2021-01-01", "2023-06-30")
    assert C.SEGMENTS["valid"] == ("2023-07-01", "2024-12-31")
    assert C.SEGMENTS["holdout"] == ("2025-01-01", "2026-08-31")
    assert C.PRICE_WARMUP_START == "2020-01-01"
    assert C.FUND_WARMUP_START == "2019-06-01"
    assert C.HORIZONS_H == {"short": 10, "swing": 20, "mid": 40}


def test_segment_of_boundaries():
    assert C.segment_of("2020-12-31") is None          # 暖機
    assert C.segment_of("2021-01-01") == "train"
    assert C.segment_of("2023-06-30") == "train"
    assert C.segment_of("2023-07-01") == "valid"
    assert C.segment_of("2024-12-31") == "valid"
    assert C.segment_of("2025-01-01") == "holdout"
    assert C.segment_of("2026-08-31") == "holdout"
    assert C.segment_of("2026-09-01") is None          # 截止後


def test_data_version_format():
    assert C.validate_data_version("fm-20260909-01") == "fm-20260909-01"
    assert C.validate_data_version("fm-20260909-b2") == "fm-20260909-b2"
    for bad in ("fm-2026-09-09-01", "20260909-01", "fm-20260909", "fm-20260909-", "x"):
        with pytest.raises(ValueError):
            C.validate_data_version(bad)
    assert C.DATA_VERSION_RE.match(C.default_data_version("01"))


def test_registry_sane():
    keys = [d.key for d in C.DATASETS]
    assert len(keys) == len(set(keys))
    assert set(C.RUN_ORDER) == set(keys)
    # 前置（stock_info／index_price）必須排在依賴者之前
    for d in C.DATASETS:
        for dep in d.depends:
            assert C.RUN_ORDER.index(dep) < C.RUN_ORDER.index(d.key), (d.key, dep)
    # 重播清單 §B3.1 必備的資料集都在
    ds = {d.dataset for d in C.DATASETS}
    for must in ("TaiwanStockPrice", "TaiwanStockInstitutionalInvestorsBuySell", "TaiwanStockMarginPurchaseShortSale",
                 "TaiwanDailyShortSaleBalances", "TaiwanStockMonthRevenue", "TaiwanStockFinancialStatements",
                 "TaiwanStockInfo", "USStockPrice", "TaiwanExchangeRate", "TaiwanStockDividendResult",
                 "TaiwanFuturesInstitutionalInvestors", "TaiwanFuturesDaily", "TaiwanOptionVix",
                 "TaiwanStockTotalMarginPurchaseShortSale"):
        assert must in ds, must
    # 基本面類自 2019-06-01
    assert C.DATASET_BY_KEY["month_revenue"].start == C.FUND_WARMUP_START
    assert C.DATASET_BY_KEY["financial_statements"].start == C.FUND_WARMUP_START
    assert C.DATASET_BY_KEY["price_daily"].start == C.PRICE_WARMUP_START


# ---------------------------------------------------------------------------
# universe：PIT 池
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("sid,typ,expect", [
    ("2330", "twse", True), ("6488", "tpex", True),
    ("0050", "twse", False), ("0056", "twse", False),        # ETF：00 開頭
    ("00631L", "twse", False), ("2330A", "twse", False),     # 非 4 碼純數字
    ("911608", "twse", False), ("1101B", "twse", False),
    ("2330", "emerging", False), ("2330", None, False), ("", "twse", False),
    ("0912", "twse", True),   # 0 開頭但非 00：依裁定字面（4 碼純數字、非 00 開頭）納入
])
def test_is_pool_candidate(sid, typ, expect):
    assert is_pool_candidate(sid, typ) is expect


def test_pool_from_info_dedupes_and_counts_multi_rows():
    rows = [
        {"stock_id": "2330", "type": "twse", "industry_category": "半導體業", "stock_name": "台積電"},
        {"stock_id": "0050", "type": "twse", "industry_category": "ETF", "stock_name": "元大台灣50"},
        {"stock_id": "6488", "type": "tpex", "industry_category": "半導體業", "stock_name": "環球晶"},
        {"stock_id": "6488", "type": "twse", "industry_category": "半導體業", "stock_name": "環球晶"},  # 殘留列
        {"stock_id": "1234", "type": "emerging", "industry_category": "x", "stock_name": "y"},
        {"stock_id": "5348", "type": "tpex", "industry_category": "運動休閒類", "date": "2026-09-09"},
        {"stock_id": "5348", "type": "tpex", "industry_category": "通信網路業", "date": "2025-06-01"},  # 較舊列排在後
    ]
    pool = pool_from_info(rows)
    assert set(pool) == {"2330", "6488", "5348"}
    assert pool["6488"]["n_rows"] == 2 and pool["6488"]["type"] == "twse"   # 無 date → 同日 tie → 優先 twse（上櫃→上市慣例，推測）
    assert pool["6488"]["same_date_multi"] is True
    assert pool["5348"]["industry_category"] == "運動休閒類"                 # 有 date → 取最大 date 那列
    assert pool["5348"]["same_date_multi"] is False and pool["2330"]["same_date_multi"] is False


def test_pool_tie_break_is_deterministic_and_skips_umbrella():
    a = {"stock_id": "3092", "type": "twse", "industry_category": "電子零組件業", "stock_name": "鴻碩", "date": "2026-09-09"}
    b = {"stock_id": "3092", "type": "twse", "industry_category": "電子工業", "stock_name": "鴻碩", "date": "2026-09-09"}
    p1 = pool_from_info([a, b])["3092"]
    p2 = pool_from_info([b, a])["3092"]
    assert p1 == p2                                             # 不依 FinMind 回列順序
    tw = dict(a, type="tpex"); assert pool_from_info([tw, a])["3092"]["type"] == "twse" == pool_from_info([a, tw])["3092"]["type"]
    assert p1["industry_category"] == "電子零組件業" and p1["same_date_multi"] is True
    # 只有傘狀類別時仍取它
    assert pool_from_info([b])["3092"]["industry_category"] == "電子工業"
    # 兩個細類同日：字串序取第一
    c = dict(a, industry_category="半導體業")
    assert pool_from_info([a, c])["3092"]["industry_category"] == "半導體業" == pool_from_info([c, a])["3092"]["industry_category"]


def test_pit_pool_is_intersection():
    ids = ["2330", "2317", "6488"]
    day = [{"stock_id": "2330"}, {"stock_id": "0050"}, {"stock_id": "6488"}, {"stock_id": "9999"}]
    assert pit_pool(ids, day) == ["2330", "6488"]
    assert pit_pool(ids, []) == []


# ---------------------------------------------------------------------------
# store：coverage 不被失敗污染、冪等、動態欄
# ---------------------------------------------------------------------------
def test_store_failure_never_covered(tmp_path):
    with Store(tmp_path / "t.db") as s:
        s.record_failure("price_daily", "2022-01-03", "error", "HTTP 500 token=SECRET", "fm-20260909-01")
        assert not s.is_covered("price_daily", "2022-01-03", "fm-20260909-01")
        assert s.covered_keys("price_daily") == set()
        assert s.coverage_summary("price_daily")["failures"] == 1
        # 同鍵再失敗 → attempts+1，仍不 covered
        s.record_failure("price_daily", "2022-01-03", "error", "again", "fm-20260909-01")
        assert s.failures_list("price_daily")[0][5] == 2
        assert not s.is_covered("price_daily", "2022-01-03", "fm-20260909-01")
        # 成功後 failures 清掉、coverage=ok
        rows = [{"date": "2022-01-03", "stock_id": "2330", "open": 600.0, "close": 610.0}]
        n = s.record_success("price_daily", "raw_price_daily", "2022-01-03", rows, "fm-20260909-01", "TaiwanStockPrice")
        assert n == 1
        assert s.is_covered("price_daily", "2022-01-03", "fm-20260909-01")
        assert s.failures_list("price_daily") == []


def test_store_empty_is_covered_but_distinct_from_failure(tmp_path):
    with Store(tmp_path / "t.db") as s:
        s.record_success("inst", "raw_inst", "2022-01-01", [], "fm-20260909-01", "X", ("date",))
        assert s.is_covered("inst", "2022-01-01", "fm-20260909-01")
        assert s.coverage_summary("inst") == pytest.approx(
            {"ok": 0, "empty": 1, "rows": 0, "min_key": "2022-01-01", "max_key": "2022-01-01", "versions": 1,
             "last_fetched_at": s.coverage_summary("inst")["last_fetched_at"], "failures": 0})


def test_store_new_data_version_forces_refetch(tmp_path):
    with Store(tmp_path / "t.db") as s:
        s.record_success("d", "raw_d", "k", [{"date": "2022-01-03", "stock_id": "1"}], "fm-20260909-01", "X")
        assert s.is_covered("d", "k", "fm-20260909-01")
        assert not s.is_covered("d", "k", "fm-20260910-01")      # 換版本＝重抓
        assert s.covered_keys("d", "fm-20260910-01") == set()
        # 重抓同鍵：舊列被刪、新列進來
        s.record_success("d", "raw_d", "k", [{"date": "2022-01-03", "stock_id": "1", "v": 2}], "fm-20260910-01", "X")
        rows = s.fetch_rows("raw_d")
        assert len(rows) == 1 and rows[0]["v"] == 2 and rows[0]["data_version"] == "fm-20260910-01"


def test_store_dynamic_columns_and_extra(tmp_path):
    with Store(tmp_path / "t.db") as s:
        s.record_success("d", "raw_d", "a", [{"date": "2022-01-03", "stock_id": "1", "Trading_Volume": 10}], "fm-20260909-01", "X")
        # 第二批多了新欄與不合法鍵 → 動態 ALTER、不合法鍵進 extra
        s.record_success("d", "raw_d", "b", [{"date": "2022-01-04", "stock_id": "1", "Trading_Volume": 11,
                                              "NewCol": "x", "weird key!": 5, "nested": {"a": 1}}], "fm-20260909-01", "X")
        cols = s.columns("raw_d")
        assert {"Trading_Volume", "NewCol", "nested"} <= cols
        r = [dict(x) for x in s.fetch_rows("raw_d", "cov_key=?", ("b",))][0]
        assert json.loads(r["extra"]) == {"weird key!": 5}
        assert json.loads(r["nested"]) == {"a": 1}
        assert r["NewCol"] == "x"
        first = [dict(x) for x in s.fetch_rows("raw_d", "cov_key=?", ("a",))][0]
        assert first["NewCol"] is None
        assert s.distinct_dates("raw_d", "1") == ["2022-01-03", "2022-01-04"]


def test_store_mixed_strategies_keep_n_rows_consistent(tmp_path):
    """fallback 讓同 dataset 混用 per_stock 與 daily_slice：同內容列不得在 cov_key 之間搬家（2026-09-09 驗收實測發生）。"""
    rows_2330 = [{"date": "2022-01-03", "stock_id": "2330", "close": 1.0}, {"date": "2022-01-04", "stock_id": "2330", "close": 2.0}]
    day_0103 = [{"date": "2022-01-03", "stock_id": "2330", "close": 1.0}, {"date": "2022-01-03", "stock_id": "2317", "close": 9.0}]
    with Store(tmp_path / "t.db") as s:
        n1 = s.record_success("price_daily", "raw_price_daily", "2330:2020-01-01~2026-08-31", rows_2330, "fm-20260909-01", "X")
        n2 = s.record_success("price_daily", "raw_price_daily", "2022-01-03", day_0103, "fm-20260909-01", "X")
        assert n1 == 2 and n2 == 2
        for key, n in (("2330:2020-01-01~2026-08-31", 2), ("2022-01-03", 2)):
            cov = s.conn.execute("SELECT n_rows FROM coverage WHERE dataset='price_daily' AND key=?", (key,)).fetchone()[0]
            assert cov == n == s.rows_for_key("raw_price_daily", key)
        assert s.conn.execute("SELECT COUNT(*) FROM raw_price_daily").fetchone()[0] == 4   # 同內容列兩鍵各持一份
        assert s.conn.execute("SELECT n_rows FROM sources WHERE dataset='price_daily'").fetchone()[0] == 4
        # 同鍵內完全重複的列被去重，n_rows 記實插數
        n3 = s.record_success("d", "raw_d", "k", [{"date": "2022-01-03", "stock_id": "1"}] * 3, "fm-20260909-01", "X")
        assert n3 == 1 and s.rows_for_key("raw_d", "k") == 1


def test_safe_col_and_row_hash():
    assert safe_col("Trading_Volume") == "Trading_Volume"
    assert safe_col("date") == "date" and safe_col("stock_id") == "stock_id"
    assert safe_col("row_hash") is None and safe_col("cov_key") is None and safe_col("extra") is None
    assert safe_col("bad-name") is None and safe_col("1abc") is None
    assert row_hash({"a": 1, "b": 2}) == row_hash({"b": 2, "a": 1})
    assert row_hash({"a": 1}) != row_hash({"a": 2})


def test_store_pragmas_wal(tmp_path):
    with Store(tmp_path / "t.db") as s:
        assert s.conn.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"
        assert s.conn.execute("PRAGMA synchronous").fetchone()[0] == 1   # NORMAL


# ---------------------------------------------------------------------------
# calendar
# ---------------------------------------------------------------------------
def test_build_calendar_dedupes_sorts_and_drops_bad():
    assert cal.build_calendar(["2022-01-04", "2022-01-03", "2022-01-03", "", None, "bad", "2022-01-05 00:00:00"]) == \
        ["2022-01-03", "2022-01-04", "2022-01-05"]


def test_calendar_payload_contract(tmp_path):
    p = cal.calendar_payload("tpe", ["2022-01-03", "2022-01-04"], "fm-20260909-01")
    for k in ("schema", "generated_at", "date", "status"):
        assert k in p
    assert p["date"] == "2022-01-04" and p["status"] == "ok" and p["calendar"] == "tpe"
    assert p["generated_at"].endswith("+08:00")
    assert p["source_data_id"] == "TAIEX"
    assert cal.calendar_payload("us", [], "fm-20260909-01")["status"] == "empty"
    with pytest.raises(ValueError):
        cal.calendar_payload("jp", [], "fm-20260909-01")
    path = tmp_path / "calendar_tpe.json"
    cal.write_calendar_json(path, p)
    assert cal.load_calendar_json(path) == ["2022-01-03", "2022-01-04"]
    assert cal.load_calendar_json(tmp_path / "nope.json") == []


def test_write_calendars_partial_goes_to_cache_not_data(tmp_path):
    data_dir, cache_dir = tmp_path / "data", tmp_path / "cache"
    partial = ["2022-01-03", "2022-01-04", "2022-03-31"]
    full = _weekdays()
    r = cal.write_calendars(partial, [], "fm-20260909-01", data_dir, cache_dir)
    assert r["tpe"]["full"] is False and r["tpe"]["path"] == cache_dir / "calendar_partial_tpe.json"
    assert r["us"] == {"path": None, "full": False, "n": 0}
    assert not (data_dir / "calendar_tpe.json").exists() and (cache_dir / "calendar_partial_tpe.json").exists()
    r2 = cal.write_calendars(full, full, "fm-20260909-01", data_dir, cache_dir)
    assert r2["tpe"]["full"] and r2["tpe"]["path"] == data_dir / "calendar_tpe.json" and r2["us"]["full"]
    assert cal.load_calendar_json(data_dir / "calendar_us.json") == full
    assert cal.calendar_covers is P.calendar_covers
    # 首尾對但缺 2023 整年 → 不得寫進 data/
    gap = [d for d in full if not d.startswith("2023")]
    r3 = cal.write_calendars(gap, [], "fm-20260909-01", tmp_path / "d2", tmp_path / "c2")
    assert r3["tpe"]["full"] is False and not (tmp_path / "d2" / "calendar_tpe.json").exists()


def test_us_session_closed_by_taipei_0800():
    us = ["2022-12-27", "2022-12-28", "2022-12-29", "2022-12-30", "2023-01-03"]   # 2023-01-02 美股休市
    assert cal.us_session_closed_by("2022-12-29", us) == "2022-12-28"   # 台北 12-29 08:00：12-28 美股已收
    assert cal.us_session_closed_by("2022-12-30", us) == "2022-12-29"
    assert cal.us_session_closed_by("2023-01-03", us) == "2022-12-30"   # 美國假日 → 更早那一天（B1.6：不是「前一夜」）
    assert cal.us_session_closed_by("2023-01-04", us) == "2023-01-03"
    assert cal.us_session_closed_by("2022-12-27", us) is None


def test_window_dates_uses_trading_days_not_calendar_days():
    c = ["2022-01-03", "2022-01-04", "2022-01-05", "2022-01-06", "2022-01-07", "2022-01-10"]
    assert cal.window_dates(c, "2022-01-10", 3) == ["2022-01-06", "2022-01-07", "2022-01-10"]
    assert cal.window_dates(c, "2022-01-09", 2) == ["2022-01-06", "2022-01-07"]   # 週日 → 取 ≤ 的最後一個交易日
    assert cal.window_dates(c, "2021-12-31", 2) == []


# ---------------------------------------------------------------------------
# plan
# ---------------------------------------------------------------------------
def test_chunk_ranges():
    assert P.chunk_ranges("2020-01-01", "2026-08-31", "year")[0] == ("2020-01-01", "2020-12-31")
    assert P.chunk_ranges("2020-01-01", "2026-08-31", "year")[-1] == ("2026-01-01", "2026-08-31")
    assert len(P.chunk_ranges("2020-01-01", "2026-08-31", "year")) == 7
    q = P.chunk_ranges("2019-06-01", "2019-12-31", "quarter")
    assert q == [("2019-06-01", "2019-06-30"), ("2019-07-01", "2019-09-30"), ("2019-10-01", "2019-12-31")]
    m = P.chunk_ranges("2019-06-01", "2026-08-31", "month")
    assert len(m) == 87 and m[0] == ("2019-06-01", "2019-06-30") and m[-1] == ("2026-08-01", "2026-08-31")
    assert P.chunk_ranges("2020-01-01", "2020-03-01", "all") == [("2020-01-01", "2020-03-01")]


def test_weekdays_between():
    assert P.weekdays_between("2022-01-01", "2022-01-09") == ["2022-01-03", "2022-01-04", "2022-01-05", "2022-01-06", "2022-01-07"]
    assert len(P.weekdays_between("2020-01-01", "2026-08-31")) == 1739


def test_plan_daily_slice_uses_calendar_when_given():
    tpe = [d for d in P.weekdays_between("2022-01-03", "2022-01-31") if d not in ("2022-01-27", "2022-01-28", "2022-01-31")]  # 1 月扣春節假
    plans = P.build_plan(tpe_dates=tpe, only=["price_daily"], start="2022-01-01", end="2022-01-31")
    assert len(plans) == 1
    p = plans[0]
    assert p.strategy == "daily_slice" and p.keys == tpe and p.basis == "交易日曆"
    assert p.alt_strategy == "per_stock" and p.alt_requests == C.POOL_SIZE_RULING
    # 無日曆 → 平日上限
    p2 = P.build_plan(only=["price_daily"], start="2022-01-01", end="2022-01-31")[0]
    assert p2.basis == "平日上限估計" and p2.n_requests == 21
    # 日曆只涵蓋部分區間 → 不採用（否則 56 天會被當成全期）
    p3 = P.build_plan(tpe_dates=tpe, only=["price_daily"], start="2022-01-01", end="2022-12-31")[0]
    assert p3.basis.startswith("平日上限估計") and "未涵蓋" in p3.basis and p3.n_requests == 260


def _monthly(start="2020-01", end="2026-08", day="15"):
    return [f"{mo}-{day}" for mo in cal.months_in(f"{start}-01", f"{end}-01")]


def _weekdays(start="2020-01-01", end="2026-08-31"):
    return P.weekdays_between(start, end)


def test_calendar_covers_requires_month_density():
    full = _weekdays()
    assert P.calendar_covers(full, "2020-01-01", "2026-08-31")
    # 首尾對、中間缺一年 → False（2026-09-09 驗收案例：只看首尾會放行）
    gap = [d for d in full if not d.startswith("2023")]
    assert not P.calendar_covers(gap, "2020-01-01", "2026-08-31")
    assert cal.calendar_gaps(gap, "2020-01-01", "2026-08-31") == [f"2023-{m:02d}" for m in range(1, 13)]
    assert not P.calendar_covers(["2020-01-02", "2020-12-31", "2026-01-05", "2026-08-31"], "2020-01-01", "2026-08-31")
    # 月內缺口：2023-04 只剩 1 日 → False（每月至少 1 日的版本擋不住）
    thin = [d for d in full if not d.startswith("2023-04")] + ["2023-04-03"]
    assert not P.calendar_covers(thin, "2020-01-01", "2026-08-31")
    assert cal.calendar_gaps(thin, "2020-01-01", "2026-08-31") == ["2023-04"]
    # 假期不誤判：春節月拿掉 9 個平日仍 True（2 月平日 20 → 11 ≥ 10）
    feb = [d for d in full if d.startswith("2024-02")]
    holiday = [d for d in full if d not in feb[:9]]
    assert P.calendar_covers(holiday, "2020-01-01", "2026-08-31")
    # 每月只有 1 日 → False；門檻＝平日數×0.5
    assert not P.calendar_covers(_monthly(day="03"), "2020-01-01", "2026-08-31")
    assert cal.weekdays_in_month("2022-01", "2022-01-01", "2022-01-10") == 6 and cal.weekdays_in_month("2024-02", "2020-01-01", "2026-08-31") == 21
    assert not P.calendar_covers(full, "2019-06-01", "2026-08-31")
    assert not P.calendar_covers(["2022-01-03", "2022-03-31"], "2020-01-01", "2026-08-31")
    assert not P.calendar_covers([], "2020-01-01", "2026-08-31")
    assert cal.months_in("2022-11-05", "2023-02-01") == ["2022-11", "2022-12", "2023-01", "2023-02"]


def test_plan_per_stock_uses_universe_ids():
    p = P.build_plan(stock_ids=["2330", "2317"], only=["price_adj"])[0]
    assert p.keys == ["2330:2020-01-01~2026-08-31", "2317:2020-01-01~2026-08-31"]
    assert p.basis == "universe.db 個股池"


def test_plan_totals_and_groups():
    core = P.build_plan()
    assert all(p.spec.group == "core" for p in core)
    keys = {p.key for p in core}
    # B1.5 官方法人與 B1.3/B1.4 成交金額是 core 必抓（2026-09-09 驗收更正）
    assert {"twse_bfi82u", "tpex_inst_summary", "twse_fmtqik", "tpex_trading_index"} <= keys
    assert "price_adj" not in keys and "taiex_kbar_0900" not in keys
    s = P.plan_summary(core, 0.7)
    fm_n = sum(p.n_requests for p in core if p.spec.source == "finmind")
    assert s["finmind_requests"] == fm_n
    assert s["official_requests"] == 2 * 1739 + 2 * 80          # 逐日兩支（平日上限）＋按月兩支（2020-01~2026-08＝80 月）
    allp = P.build_plan(groups=("core", "optional", "check"))
    assert {p.key for p in allp} == set(C.DATASET_BY_KEY)
    assert "28,050" in P.format_plan(core, 0.7)


def test_plan_official_month_keys():
    p = P.build_plan(only=["twse_fmtqik"])[0]
    assert p.strategy == "official_month" and p.keys[0] == "202001" and p.keys[-1] == "202608" and len(p.keys) == 80
    q = P.build_plan(only=["tpex_trading_index"], start="2022-01-15", end="2022-02-01")[0]
    assert q.keys == ["202201", "202202"]


def test_out_of_scope_declared_and_synced_with_runbook():
    keys = list(C.OUT_OF_SCOPE)
    assert any("#9" in k and "model_version" in k for k in keys)      # 版本三元組（2026-09-09 驗收補）
    text = (ROOT / "docs" / "BACKFILL-RUNBOOK.md").read_text(encoding="utf-8")
    i = text.index("## 8.")
    j = text.find("\n## ", i + 1)
    sec = text[i:] if j < 0 else text[i:j]
    sec = sec.replace("`", "").replace("**", "")
    missing = [k for k in keys if k not in sec]
    assert not missing, f"runbook §8 缺 OUT_OF_SCOPE 鍵：{missing}"


def test_plan_range_keys_align_to_grid_regardless_of_from_to():
    # --from/--to 只選塊不改塊界：鍵必須與預設計畫的鍵相同，才不會做出重疊的 coverage 鍵
    p = P.build_plan(only=["index_price"], start="2022-01-03", end="2022-01-05")[0]
    assert p.keys == ["TAIEX:2022-01-01~2022-12-31", "TPEx:2022-01-01~2022-12-31"]
    full = P.build_plan(only=["index_price"])[0].keys
    assert set(p.keys) <= set(full) and len(full) == 14
    m = P.build_plan(only=["month_revenue"], start="2019-06-15", end="2019-07-01")[0]
    assert m.keys == ["2019-06-01~2019-06-30", "2019-07-01~2019-07-31"]
    ps = P.build_plan(only=["price_adj"], stock_ids=["2330"], start="2022-01-01", end="2022-01-31")[0]
    assert ps.keys == ["2330:2020-01-01~2026-08-31"]


def test_plan_strategy_override():
    p = P.build_plan(only=["dividend_result"], strategy_override={"dividend_result": "per_stock"})[0]
    assert p.strategy == "per_stock" and p.n_requests == C.POOL_SIZE_RULING and p.alt_strategy is None


# ---------------------------------------------------------------------------
# fm：回應分類／token 遮蔽／重試
# ---------------------------------------------------------------------------
def test_classify_response_kinds():
    assert classify_response(200, {"status": 200, "msg": "success", "data": [{"a": 1}]})[0] == "ok"
    assert classify_response(200, {"status": 200, "msg": "success", "data": []})[0] == "empty"     # 非交易日
    assert classify_response(200, {"msg": "success", "data": []})[0] == "empty"                    # 無 status 欄
    assert classify_response(400, {"status": 400, "msg": "Your level is free. Please update your user level. Detail in", "data": []})[0] == "permission"
    assert classify_response(402, {"status": 402, "msg": "Requests reach the upper limit. https://finmindtrade.com/"})[0] == "quota"
    assert classify_response(429, None, "")[0] == "quota"
    assert classify_response(200, {"status": 400, "msg": "something"})[0] == "error"   # HTTP 200 但 body 400（P0-A §4.5）
    assert classify_response(400, {"status": 400, "msg": "date error"})[0] == "error"
    assert classify_response(500, None, "<html>")[0] == "error"
    assert classify_response(200, None, "<html>blocked</html>")[0] == "error"
    assert classify_response(200, {"status": 200, "data": {"not": "list"}})[0] == "error"


def test_redact_token_in_messages():
    s = "HTTPSConnectionPool: url: /api/v4/data?dataset=X&token=abc.def_123&start_date=2022"
    r = redact(s)
    assert "abc.def_123" not in r and "token=<redacted>" in r


def test_load_token_from_env_file_and_env(tmp_path, monkeypatch):
    monkeypatch.delenv("FINMIND_TOKEN", raising=False)
    env = tmp_path / ".env"
    env.write_text("# comment\nOTHER=1\nexport FINMIND_TOKEN='tok-from-file'\n", encoding="utf-8")
    assert load_token(env) == "tok-from-file"
    monkeypatch.setenv("FINMIND_TOKEN", "tok-from-env")
    assert load_token(env) == "tok-from-env"
    monkeypatch.delenv("FINMIND_TOKEN")
    with pytest.raises(PermissionRequired):
        load_token(tmp_path / "missing.env")
    assert load_token(tmp_path / "missing.env", required=False) is None


class _Resp:
    def __init__(self, code, body, text=""):
        self.status_code = code
        self._body = body
        self.text = text if text else (json.dumps(body) if body is not None else "")

    def json(self):
        if self._body is None:
            raise ValueError("no json")
        return self._body


class _Session:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def get(self, url, params=None, headers=None, timeout=None):
        self.calls.append((url, dict(params or {}), dict(headers or {})))
        return self.responses.pop(0)


def _client(responses, token="T"):
    sleeps = []
    fm = FinMind(token, session=_Session(responses), sleep=sleeps.append, clock=lambda: 0.0, min_interval=0)
    return fm, sleeps


def test_fm_token_goes_to_header_not_query():
    fm, _ = _client([_Resp(200, {"status": 200, "data": [{"x": 1}]})], token="SECRET")
    assert fm.get("TaiwanStockPrice", data_id="TAIEX", start_date="2022-01-03", end_date="2022-01-03") == [{"x": 1}]
    url, params, headers = fm.session.calls[0]
    assert "token" not in params and headers["Authorization"] == "Bearer SECRET"
    assert params["dataset"] == "TaiwanStockPrice"


def test_fm_permission_raises_without_retry():
    fm, sleeps = _client([_Resp(400, {"status": 400, "msg": "Your level is free"})])
    with pytest.raises(PermissionRequired):
        fm.get("TaiwanStockPrice", start_date="2022-01-03", end_date="2022-01-03")
    assert fm.n_requests == 1 and sleeps == []


def test_fm_quota_waits_then_succeeds():
    fm, sleeps = _client([_Resp(402, {"status": 402, "msg": "Requests reach the upper limit"}),
                          _Resp(200, {"status": 200, "data": []})])
    assert fm.get("X", start_date="2022-01-03", end_date="2022-01-03") == []
    assert sleeps == [65] and fm.n_quota_waits == 1


def test_fm_quota_exhausted_raises():
    fm, sleeps = _client([_Resp(402, {"status": 402, "msg": "limit"})] * 20)
    with pytest.raises(QuotaExceeded):
        fm.get("X")
    assert len(sleeps) == 8 and fm.n_requests == 9


def test_fm_transient_retries_then_raises_and_never_returns_empty():
    fm, sleeps = _client([_Resp(500, None, "boom")] * 10)
    with pytest.raises(TransientError):
        fm.get("X")
    assert sleeps == [5.0, 15.0, 45.0] and fm.n_requests == 4


def test_fm_no_token_allowed_only_when_flagged(tmp_path, monkeypatch):
    monkeypatch.delenv("FINMIND_TOKEN", raising=False)
    fm = FinMind(env_file=tmp_path / ".env", allow_no_token=True, session=_Session([]), sleep=lambda s: None)
    assert fm.has_token() is False
    fm2 = FinMind(env_file=tmp_path / ".env", session=_Session([]), sleep=lambda s: None)
    with pytest.raises(PermissionRequired):
        fm2.has_token()


# ---------------------------------------------------------------------------
# twse：月表解析（欄位防禦）與開盤比對
# ---------------------------------------------------------------------------
def test_parse_roc_or_iso():
    assert T.parse_roc_or_iso("111/01/03") == "2022-01-03"
    assert T.parse_roc_or_iso(" 115/8/29 ") == "2026-08-29"
    assert T.parse_roc_or_iso("2022/01/03") == "2022-01-03"
    assert T.parse_roc_or_iso("2022-01-03") == "2022-01-03"
    assert T.parse_roc_or_iso("111/13/03") is None
    assert T.parse_roc_or_iso("abc") is None and T.parse_roc_or_iso(None) is None


def test_parse_index_hist_defensive_fields():
    payload = {"stat": "OK", "fields": ["日期", "開盤指數", "最高指數", "最低指數", "收盤指數"],
               "data": [["111/01/03", "18,260.24", "18,526.35", "18,260.24", "18,526.35"],
                        ["111/01/04", "18,590.47", "18,619.61", "18,509.15", "18,526.35"]]}
    rows = T.parse_index_hist(payload)
    assert rows[0] == {"date": "2022-01-03", "open": 18260.24, "high": 18526.35, "low": 18260.24, "close": 18526.35}
    # 欄位順序打亂也能解（找含「開盤」的欄）
    payload2 = {"stat": "OK", "fields": ["收盤指數", "日期", "開盤指數"], "data": [["1", "111/02/07", "2.5"]]}
    assert T.parse_index_hist(payload2)[0]["open"] == 2.5 and T.parse_index_hist(payload2)[0]["close"] == 1.0
    with pytest.raises(T.TwseError):
        T.parse_index_hist({"stat": "OK", "fields": ["日期", "收盤"], "data": [["111/01/03", "1"]]})   # 無「開盤」
    with pytest.raises(T.TwseError):
        T.parse_index_hist({"stat": "查詢日期大於今日", "fields": [], "data": []})
    with pytest.raises(T.TwseError):
        T.parse_index_hist("<html>因為安全性考量</html>")
    with pytest.raises(T.TwseError):
        T.parse_index_hist({"stat": "OK", "fields": ["日期", "開盤指數"], "data": [["not-a-date", "1"]]})


def test_compare_open():
    tw = [{"date": "2022-12-29", "open": 14000.0, "close": 14085.02}, {"date": "2022-12-30", "open": 14183.52, "close": 14137.69},
          {"date": "2022-12-28", "open": 1.0}]
    fm = [{"date": "2022-12-29", "open": 14000.0, "close": 14085.02}, {"date": "2022-12-30", "open": 14183.5, "close": 14137.69},
          {"date": "2023-01-03", "open": 5.0}]
    r = T.compare_open(tw, fm, tol=0.005)
    assert r["n_common"] == 2 and r["n_equal"] == 1 and r["agree_rate"] == 0.5
    assert r["max_abs_diff"] == pytest.approx(0.02)
    assert r["worst"][0]["date"] == "2022-12-30"
    assert r["only_twse"] == ["2022-12-28"] and r["only_finmind"] == ["2023-01-03"]
    assert r["close_n"] == 2 and r["close_n_equal"] == 2
    assert T.compare_open([], fm)["agree_rate"] is None
    # open == 0 視為缺值：不進共同日、不算不一致
    z = T.compare_open([{"date": "2022-12-29", "open": 14000.0}, {"date": "2022-12-30", "open": 0}],
                       [{"date": "2022-12-29", "open": 14000.0}, {"date": "2022-12-30", "open": 14183.52}])
    assert z["n_common"] == 1 and z["agree_rate"] == 1.0 and z["only_finmind"] == ["2022-12-30"]


def test_compare_candidates_verdicts():
    tw = [{"date": f"2022-01-{d:02d}", "open": 100.0 + d} for d in range(3, 8)]
    good = [{"date": f"2022-01-{d:02d}", "open": 100.0 + d} for d in range(3, 8)]
    bad = [{"date": f"2022-01-{d:02d}", "open": 100.0 + d + 0.5} for d in range(3, 8)]
    r = T.compare_candidates(tw, {"finmind_open": good, "kbar_0900_close": bad})
    assert r["candidates"]["finmind_open"]["agrees"] and not r["candidates"]["kbar_0900_close"]["agrees"]
    assert r["verdict"] == "finmind_open" and r["agreeing"] == ["finmind_open"]
    assert T.compare_candidates(tw, {"finmind_open": good, "kbar_0900_close": good})["verdict"] == "both"
    assert T.compare_candidates(tw, {"finmind_open": bad, "kbar_0900_close": bad})["verdict"] == "none"
    assert T.compare_candidates(tw, {"finmind_open": []})["verdict"] == "no_data"
    # 一致率門檻：5 日中 4 日一致＝80% < 99% → 不一致
    mixed = good[:4] + [dict(bad[4])]
    assert T.compare_candidates(tw, {"finmind_open": mixed})["verdict"] == "none"
    assert T.compare_candidates(tw, {"finmind_open": mixed}, agree_threshold=0.8)["verdict"] == "finmind_open"


def test_kbar_0900_row():
    rows = [{"date": "2022-12-30", "minute": "09:01:00", "open": 14245.07, "close": 14250.0},
            {"date": "2022-12-30", "minute": "09:00:00", "open": 14085.02, "high": 14250.0, "low": 14080.0, "close": "14,245.26", "volume": 10},
            {"date": "2022-12-29", "minute": "09:00:00", "open": 1.0, "close": 1.0}]
    b = T.kbar_0900_row(rows, "2022-12-30")
    assert b["minute"] == "09:00:00" and b["close"] == 14245.26 and b["open"] == 14085.02 and b["n_bars"] == 2
    assert T.kbar_0900_row([{"minute": "09:05:00", "close": 1}], "2022-12-30") is None
    assert T.kbar_0900_row([], "2022-12-30") is None


def test_months_between_and_to_number():
    assert T.months_between("202211", "202302") == ["202211", "202212", "202301", "202302"]
    assert T.to_number("18,260.24") == 18260.24 and T.to_number("--") is None and T.to_number(None) is None


# ---------------------------------------------------------------------------
# CLI：key 反解與 plan 入口
# ---------------------------------------------------------------------------
def test_cli_parse_key_roundtrip():
    sys.path.insert(0, str(ROOT / "scripts"))
    import backfill_hetzner as B
    assert B.parse_key("daily_slice", "2022-01-03") == {"start_date": "2022-01-03", "end_date": "2022-01-03"}
    assert B.parse_key("range_slice", "2020-01-01~2020-12-31") == {"start_date": "2020-01-01", "end_date": "2020-12-31"}
    assert B.parse_key("per_id", "^GSPC:2020-01-01~2020-12-31") == {"data_id": "^GSPC", "start_date": "2020-01-01", "end_date": "2020-12-31"}
    assert B.parse_key("per_stock", "2330:2020-01-01~2026-08-31")["data_id"] == "2330"
    assert B.parse_key("single", "all") == {}
    assert B.OFFICIAL_PARAMS["twse_bfi82u"]("2022-01-03") == {"dayDate": "20220103", "type": "day", "response": "json"}
    assert B.OFFICIAL_PARAMS["tpex_inst_summary"]("2022-01-03")["date"] == "2022/01/03"


class _FakeFM:
    """依 (dataset, data_id, start_date) 回固定列；缺鍵回空。"""
    def __init__(self, table):
        self.table = table
        self.calls = []

    def get(self, dataset, **p):
        self.calls.append((dataset, dict(p)))
        return list(self.table.get((dataset, p.get("data_id"), p.get("start_date")), []))


def _args(**kw):
    sys.path.insert(0, str(ROOT / "scripts"))
    import backfill_hetzner as B
    a = B.build_parser().parse_args(["run"] + kw.pop("argv", []))
    for k, v in kw.items():
        setattr(a, k, v)
    return a


def test_run_dataset_empty_on_trading_day_not_covered(tmp_path):
    sys.path.insert(0, str(ROOT / "scripts"))
    import backfill_hetzner as B
    from iching.store import open_stores
    dv = "fm-20260909-01"
    stores = open_stores(tmp_path, C.DB_FILES)
    # 同 data_version 的台北交易日曆：TAIEX 2022-01-03～01-06（01-01~01-10 平日 6 天，門檻 3）
    stores["prices"].record_success("index_price", "raw_index_price", "TAIEX:2022-01-01~2022-12-31",
                                    [{"date": f"2022-01-{d:02d}", "stock_id": "TAIEX", "open": d} for d in (3, 4, 5, 6)],
                                    dv, "TaiwanStockPrice")
    # 2026-09-10 起 price_daily 落地過濾需要 raw_stock_info（沒有會中止，見 tests/test_landing_filter.py）→ 先落地
    stores["universe"].record_success("stock_info", "raw_stock_info", "all", [{"stock_id": "2330", "type": "twse"}], dv, "TaiwanStockInfo", ("stock_id",))
    fm = _FakeFM({("TaiwanStockPrice", None, "2022-01-03"): [{"date": "2022-01-03", "stock_id": "2330", "close": 1}],
                  # 2022-01-04 在日曆上但回空 → 不得 covered
                  })
    spec = C.DATASET_BY_KEY["price_daily"]
    # 日曆守門容許前後 10 天，故請求區間取 01-01~01-10（日曆 01-03、01-04 涵蓋）
    args = _args(argv=["--dataset", "price_daily", "--from", "2022-01-01", "--to", "2022-01-10", "--no-fallback"])
    fm.table.update({("TaiwanStockPrice", None, "2022-01-05"): [{"date": "2022-01-05", "stock_id": "2330"}],
                     ("TaiwanStockPrice", None, "2022-01-06"): [{"date": "2022-01-06", "stock_id": "2330"}]})
    st = B.run_dataset(spec, "daily_slice", stores, fm, None, dv, args)
    assert st["planned"] == 4 and st["ok"] == 3 and st["empty"] == 0 and st["failed"] == 1
    p = stores["prices"]
    assert p.is_covered("price_daily", "2022-01-03", dv)
    assert not p.is_covered("price_daily", "2022-01-04", dv)
    f = p.failures_list("price_daily")
    assert len(f) == 1 and f[0][1] == "2022-01-04" and f[0][2] == B.EMPTY_ON_TRADING_DAY
    # 重跑：01-03 跳過、01-04 再試（這次有資料）→ covered、failures 清空
    fm.table[("TaiwanStockPrice", None, "2022-01-04")] = [{"date": "2022-01-04", "stock_id": "2330", "close": 2}]
    st2 = B.run_dataset(spec, "daily_slice", stores, fm, None, dv, args)
    assert st2["skipped"] == 3 and st2["ok"] == 1 and p.is_covered("price_daily", "2022-01-04", dv) and p.failures_list("price_daily") == []
    # 日曆是**另一個** data_version 的 → 對本版本而言沒有日曆 → 中止而非亂抓
    st3 = B.run_dataset(spec, "daily_slice", stores, fm, None, "fm-20260910-01", args)
    assert st3["aborted"] and "未涵蓋" in st3["aborted"]
    # per_id 的空回應是異常（index_price 某年空 → 日曆缺年）：failures(empty_unexpected)、不 covered
    ispec = C.DATASET_BY_KEY["index_price"]
    fm2 = _FakeFM({})
    st4 = B.run_dataset(ispec, "per_id", stores, fm2, None, dv, _args(argv=["--dataset", "index_price", "--from", "2023-01-01", "--to", "2023-01-31"]))
    assert st4["failed"] == 2 and st4["empty"] == 0 and not p.is_covered("index_price", "TPEx:2023-01-01~2023-12-31", dv)
    assert {r[2] for r in p.failures_list("index_price")} == {B.EMPTY_UNEXPECTED}
    # per_stock（宣告 empty_ok_for）的空回應才是合法 empty
    aspec = C.DATASET_BY_KEY["price_adj"]
    st5 = B.run_dataset(aspec, "per_stock", stores, fm2, None, dv, _args(argv=["--dataset", "price_adj"]))
    assert st5["empty"] == 1 and p.is_covered("price_adj", "2330:2020-01-01~2026-08-31", dv)
    for s_ in stores.values():
        s_.close()


def test_official_body_ok():
    assert T.official_body_ok({"stat": "OK", "data": [[1]]}, "twse") == (True, "data 1 列")
    assert T.official_body_ok({"stat": "OK", "data": []}, "twse")[0] is False        # stat=OK 但 data 空 → 無資料
    assert T.official_body_ok({"stat": "OK"}, "twse")[0] is False
    assert T.official_body_ok({"stat": "很抱歉, 沒有符合條件的資料!"}, "twse")[0] is False
    assert T.official_body_ok({"tables": [{"data": [[1, 2]]}]}, "tpex")[0] is True
    assert T.official_body_ok({"tables": [{"data": []}]}, "tpex")[0] is False
    assert T.official_body_ok({"tables": []}, "tpex")[0] is False
    assert T.official_body_ok({"stat": "查無資料", "tables": [{"data": [[1]]}]}, "tpex")[0] is False
    assert T.official_body_ok({"stat": "ok", "tables": [{"data": [[1]]}]}, "tpex")[0] is True
    assert T.official_body_ok("<html>", "twse")[0] is False


class _FakeOC:
    def __init__(self, table):
        self.table = table

    def get(self, url, params):
        key = params.get("dayDate") or params.get("date")
        return self.table.get(key, (200, None, "<html>blocked</html>"))


def test_run_dataset_official_failure_classification(tmp_path):
    sys.path.insert(0, str(ROOT / "scripts"))
    import backfill_hetzner as B
    from iching.store import open_stores
    dv = "fm-20260909-01"
    stores = open_stores(tmp_path, C.DB_FILES)
    stores["prices"].record_success("index_price", "raw_index_price", "TAIEX:2022-01-01~2022-12-31",
                                    [{"date": f"2022-01-{d:02d}", "stock_id": "TAIEX", "open": 1} for d in (3, 4, 5, 6)],
                                    dv, "TaiwanStockPrice")
    oc = _FakeOC({
        "20220103": (500, {"stat": "OK", "data": [[1]]}, "{}"),                          # HTTP 500＋JSON → error
        "20220104": (200, {"stat": "很抱歉, 沒有符合條件的資料!"}, "{}"),                  # 交易日 stat 非 OK → empty_on_trading_day
        "20220105": (200, {"stat": "OK", "fields": ["x"], "data": [["1"]]}, "{}"),        # ok
        # 20220106：非 JSON → error
    })
    spec = C.DATASET_BY_KEY["twse_bfi82u"]
    args = _args(argv=["--dataset", "twse_bfi82u", "--from", "2022-01-01", "--to", "2022-01-10"])
    st = B.run_dataset(spec, "official", stores, None, oc, dv, args)
    m = stores["market"]
    assert st == {**st, "planned": 4, "ok": 1, "empty": 0, "failed": 3}
    assert m.is_covered("twse_bfi82u", "2022-01-05", dv)
    kinds = {row[1]: row[2] for row in m.failures_list("twse_bfi82u")}
    assert kinds == {"2022-01-03": "error", "2022-01-04": B.EMPTY_ON_TRADING_DAY, "2022-01-06": "error"}
    assert not any(m.is_covered("twse_bfi82u", d, dv) for d in ("2022-01-03", "2022-01-04", "2022-01-06"))
    row = dict(m.fetch_rows("raw_twse_bfi82u")[0])
    assert row["date"] == "2022-01-05" and row["stat"] == "OK" and json.loads(row["body"])["data"] == [["1"]]
    # TPEx：tables 空在交易日 → empty_on_trading_day；tables 有資料 → ok
    oc2 = _FakeOC({"2022/01/03": (200, {"tables": [{"data": []}]}, "{}"), "2022/01/04": (200, {"tables": [{"data": [[1]]}]}, "{}")})
    st2 = B.run_dataset(C.DATASET_BY_KEY["tpex_inst_summary"], "official", stores, None, oc2, dv,
                        _args(argv=["--dataset", "tpex_inst_summary", "--from", "2022-01-01", "--to", "2022-01-04"]))
    assert st2["ok"] == 1 and st2["failed"] == 1 and m.failures_list("tpex_inst_summary")[0][2] == B.EMPTY_ON_TRADING_DAY
    # official_month：stat 非 OK → bad_stat；OK → date=ISO 月首、month=YYYYMM
    oc3 = _FakeOC({"20220101": (200, {"stat": "查詢日期大於今日"}, "{}"), "20220201": (200, {"stat": "OK", "data": [["111/02/07", "1"]]}, "{}")})
    st3 = B.run_dataset(C.DATASET_BY_KEY["twse_fmtqik"], "official_month", stores, None, oc3, dv,
                        _args(argv=["--dataset", "twse_fmtqik", "--from", "2022-01-01", "--to", "2022-02-28"]))
    assert st3["ok"] == 1 and st3["failed"] == 1 and m.failures_list("twse_fmtqik")[0][2] == "bad_stat"
    r = dict(m.fetch_rows("raw_twse_fmtqik")[0])
    assert r["date"] == "2022-02-01" and r["month"] == "202202" and r["cov_key"] == "202202"
    assert m.conn.execute("SELECT min_date FROM sources WHERE dataset='twse_fmtqik'").fetchone()[0] == "2022-02-01"
    for s_ in stores.values():
        s_.close()


def test_write_calendars_filters_by_data_version(tmp_path):
    """DB 混兩個 dv：舊 dv 的 2020 不得與新 dv 的 2021–2026 拼成 full（2026-09-09 驗收實測）。"""
    sys.path.insert(0, str(ROOT / "scripts"))
    import backfill_hetzner as B
    from iching.store import open_stores
    stores = open_stores(tmp_path / "cache", C.DB_FILES)
    old, new = "fm-20260901-01", "fm-20260909-01"
    wk = P.weekdays_between("2020-01-01", "2026-08-31")
    stores["prices"].record_success("index_price", "raw_index_price", "TAIEX:2020-01-01~2020-12-31",
                                    [{"date": d, "stock_id": "TAIEX"} for d in wk if d < "2021"], old, "TaiwanStockPrice")
    for y in range(2021, 2027):
        stores["prices"].record_success("index_price", "raw_index_price", f"TAIEX:{y}-01-01~{y}-12-31",
                                        [{"date": d, "stock_id": "TAIEX"} for d in wk if d.startswith(str(y))], new, "TaiwanStockPrice")
    # 未過濾 dv 看起來是 full；過濾後缺 2020 → partial
    assert P.calendar_covers(B.tpe_calendar_from_store(stores["prices"]), C.PRICE_WARMUP_START, C.DATA_END)
    r = B.write_calendars(stores, new, tmp_path / "data", tmp_path / "cache")
    assert r["tpe"]["full"] is False and not (tmp_path / "data" / "calendar_tpe.json").exists()
    dates = cal.load_calendar_json(tmp_path / "cache" / "calendar_partial_tpe.json")
    assert dates and dates[0].startswith("2021") and not any(d.startswith("2020") for d in dates)
    assert B.data_versions_in(stores) == [old, new]
    for s_ in stores.values():
        s_.close()


def test_unknown_group_exits_2(capsys):
    sys.path.insert(0, str(ROOT / "scripts"))
    import backfill_hetzner as B
    with pytest.raises(SystemExit) as e:
        B.select_keys(B.build_parser().parse_args(["plan", "--group", "core", "official"]))
    assert e.value.code == 2 and "official" in capsys.readouterr().err
    only, groups = B.select_keys(B.build_parser().parse_args(["plan", "--group", "core", "optional"]))
    assert groups == ("core", "optional")


def test_report_pit_pool_by_year_uses_pit_pool(tmp_path):
    sys.path.insert(0, str(ROOT / "scripts"))
    import backfill_hetzner as B
    from iching.store import open_stores
    dv = "fm-20260909-01"
    stores = open_stores(tmp_path, C.DB_FILES)
    stores["universe"].record_success("stock_info", "raw_stock_info", "all",
        [{"stock_id": "2330", "type": "twse"}, {"stock_id": "2317", "type": "twse"}, {"stock_id": "0050", "type": "twse"}],
        dv, "TaiwanStockInfo", ("stock_id",))
    stores["prices"].record_success("price_daily", "raw_price_daily", "2022-01-03",
        [{"date": "2022-01-03", "stock_id": "2330"}, {"date": "2022-01-03", "stock_id": "0050"}, {"date": "2022-01-03", "stock_id": "9999"}],
        dv, "TaiwanStockPrice")
    stores["prices"].record_success("price_daily", "raw_price_daily", "2022-01-04",
        [{"date": "2022-01-04", "stock_id": "2330"}, {"date": "2022-01-04", "stock_id": "2317"}], dv, "TaiwanStockPrice")
    stores["prices"].record_success("price_daily", "raw_price_daily", "2023-01-03",
        [{"date": "2023-01-03", "stock_id": "2317"}], dv, "TaiwanStockPrice")
    r = B.pit_pool_by_year(stores["prices"], stores["universe"])
    assert r == [{"year": "2022", "trading_days": 2, "mean_daily_pool": 1.5, "min_daily_pool": 1, "max_daily_pool": 2, "distinct_ids": 2},
                 {"year": "2023", "trading_days": 1, "mean_daily_pool": 1.0, "min_daily_pool": 1, "max_daily_pool": 1, "distinct_ids": 1}]
    for s_ in stores.values():
        s_.close()


def test_run_refuses_check_group_and_parses_official_month():
    sys.path.insert(0, str(ROOT / "scripts"))
    import backfill_hetzner as B
    assert B.parse_key("official_month", "202201") == {"month": "202201"}
    assert B.OFFICIAL_PARAMS["twse_fmtqik"]("202201") == {"date": "20220101", "response": "json"}
    assert B.OFFICIAL_PARAMS["tpex_trading_index"]("202201")["date"] == "2022/01/01"
    with pytest.raises(SystemExit):
        B.resolve_run_list(B.build_parser().parse_args(["run", "--dataset", "taiex_kbar_0900"]))
    order = [s.key for s, _ in B.resolve_run_list(B.build_parser().parse_args(["run"]))]
    assert {"twse_bfi82u", "tpex_inst_summary", "twse_fmtqik", "tpex_trading_index"} <= set(order) and "price_adj" not in order


def test_cli_plan_runs_offline(capsys, tmp_path):
    sys.path.insert(0, str(ROOT / "scripts"))
    import backfill_hetzner as B
    rc = B.main(["--cache-dir", str(tmp_path), "plan", "--dataset", "index_price", "us_index"])
    out = capsys.readouterr().out
    assert rc == 0 and "index_price" in out and "us_index" in out and "28,050" in out
    # run 的前置解析：選 price_daily 會自動補 index_price／stock_info 在前
    args = B.build_parser().parse_args(["run", "--dataset", "price_daily"])
    order = [s.key for s, _ in B.resolve_run_list(args)]
    assert order.index("index_price") < order.index("price_daily") and order.index("stock_info") < order.index("price_daily")
