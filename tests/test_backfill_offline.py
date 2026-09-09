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
    ]
    pool = pool_from_info(rows)
    assert set(pool) == {"2330", "6488"}
    assert pool["6488"]["n_rows"] == 2 and pool["6488"]["type"] == "twse"   # 最後一列


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
    tpe = ["2022-01-03", "2022-01-04", "2022-01-05"]
    plans = P.build_plan(tpe_dates=tpe, only=["price_daily"], start="2022-01-01", end="2022-01-31")
    assert len(plans) == 1
    p = plans[0]
    assert p.strategy == "daily_slice" and p.keys == tpe and p.basis == "交易日曆"
    assert p.alt_strategy == "per_stock" and p.alt_requests == C.POOL_SIZE_RULING
    # 無日曆 → 平日上限
    p2 = P.build_plan(only=["price_daily"], start="2022-01-01", end="2022-01-31")[0]
    assert p2.basis == "平日上限估計" and p2.n_requests == 21


def test_plan_per_stock_uses_universe_ids():
    p = P.build_plan(stock_ids=["2330", "2317"], only=["price_adj"])[0]
    assert p.keys == ["2330:2020-01-01~2026-08-31", "2317:2020-01-01~2026-08-31"]
    assert p.basis == "universe.db 個股池"


def test_plan_totals_and_groups():
    core = P.build_plan()
    assert all(p.spec.group == "core" for p in core)
    s = P.plan_summary(core, 0.7)
    assert s["finmind_requests"] == sum(p.n_requests for p in core) and s["official_requests"] == 0
    allp = P.build_plan(groups=("core", "optional", "official"))
    assert {p.key for p in allp} == set(C.DATASET_BY_KEY)
    assert P.plan_summary(allp, 0.7)["official_requests"] == 2 * 1739
    assert "28,050" in P.format_plan(core, 0.7)


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
