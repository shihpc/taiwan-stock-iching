"""`iching.feed`：從回補 DB 餵出逐日橫斷面。免 token 免網路（合成 DB 實跑）。

`day_records()` 是**量測腳本與落地腳本共用的唯一一處**記錄建構。它錯了，兩邊會一起錯，
而且兩邊的數字看起來都很合理——所以守門集中在這裡。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from iching import feed as F  # noqa: E402
from synth_db import (CAPRED_I, DAYS, DV, EX_I, LATE_I, MALFORMED_I, PAR_I, SPLIT_I, SUSPEND_I,  # noqa: E402
                      add_adjust_source_rows, build as _build)


@pytest.fixture(scope="module")
def db(tmp_path_factory):
    cache = tmp_path_factory.mktemp("feed") / "cache"
    _build(cache)
    add_adjust_source_rows(cache)                                        # 裁定 #51：三源事件（第 60／65／70 日，既有斷言窗口之外）
    prices, uni = F.open_ro(cache / "prices.db"), F.open_ro(cache / "universe.db")
    yield prices, uni, cache
    prices.close()
    uni.close()


def _day(prices, pool, factors, i, **kw):
    for d, rows in F.iter_days(prices, DV, True):
        if d == DAYS[i]:
            return F.day_records(d, rows, pool, factors, **kw)
    raise AssertionError(f"找不到 {DAYS[i]}")


def test_readonly_connection_refuses_writes(db):
    import sqlite3
    prices, _, _ = db
    with pytest.raises(sqlite3.OperationalError, match="readonly"):
        prices.execute("INSERT INTO raw_price_daily(cov_key,row_hash,data_version) VALUES('a','b','c')")


def test_loaders_and_loud_failures(db):
    prices, uni, cache = db
    pool = F.load_pool(uni)
    assert set(pool) == {"1101", "1102", "1103", "2330", "6488"}      # ETF 0050／DR 9101 不進池
    assert pool.market("6488", DAYS[-1]) == "tpex" and "type" not in pool["6488"]   # 市場別只問 PIT 介面（2026-09-16）
    factors, stat = F.load_factors(prices, DV)
    assert stat["stocks"] == 3 and stat["bad_skipped"] == 0             # 1101（除息＋減資）、2330（分割）、6488（面額變更）；裁定 #51 前為 1
    assert stat["rows"] == 4 and stat["anomalies"] == 0                  # 合併後 4 列（分割×面額變更同鍵去重成 1）
    idx = F.load_index(prices, DV)
    assert set(idx[DAYS[0]]) == {"twse", "tpex"}                       # TAIEX→twse、TPEx→tpex
    assert F.resolve_dv(prices, F.PRICE_TABLE, None) == DV             # 只有一個版本 → 免指定
    with pytest.raises(F.FeedError, match="沒有 data_version"):
        F.resolve_dv(prices, F.PRICE_TABLE, "fm-不存在")
    with pytest.raises(F.FeedError, match="缺欄位"):
        F.require(prices, F.PRICE_TABLE, {"沒這欄"})
    with pytest.raises(F.FeedError, match="找不到 DB"):
        F.open_ro(cache / "不存在.db")


def test_day_records_filters_pool_and_marks_untraded(db):
    prices, uni, _ = db
    pool, (factors, _) = F.load_pool(uni), F.load_factors(prices, DV)
    recs, amounts = _day(prices, pool, factors, SUSPEND_I[0])
    by = {r.stock_id: r for r in recs}
    assert set(by) == {"1101", "1102", "1103", "2330", "6488"}         # 池外（ETF／DR）不出現
    # 停牌：close_adj 與 amount 皆 None，且**不進成交值 dict**（AdvTracker 自己補 0）
    assert by["1102"].close_adj is None and by["1102"].amount is None
    assert "1102" not in amounts
    assert by["1101"].close_adj is not None and amounts["1101"] > 0
    # 畸形列（有量無 close）同樣算不成交
    recs2, amounts2 = _day(prices, pool, factors, MALFORMED_I)
    assert {r.stock_id: r.close_adj for r in recs2}["6488"] is None and "6488" not in amounts2


def test_day_records_not_listed_yet_is_absent_not_none(db):
    """尚未上市＝**完全沒有列**，與「有列但沒成交」不同——前者連 `StockDay` 都不該有。"""
    prices, uni, _ = db
    pool, (factors, _) = F.load_pool(uni), F.load_factors(prices, DV)
    before, _ = _day(prices, pool, factors, LATE_I - 1)
    after, _ = _day(prices, pool, factors, LATE_I)
    assert "1103" not in {r.stock_id for r in before}
    assert "1103" in {r.stock_id for r in after}


def test_day_records_adjusted_flag_changes_price_only_after_ex_date(db):
    """`adjusted=True` 套後復權係數；除權息日**之前**兩者必須相同（係數為 1.0）。"""
    prices, uni, _ = db
    pool, (factors, _) = F.load_pool(uni), F.load_factors(prices, DV)
    for i, same in ((EX_I - 1, True), (EX_I, False), (EX_I + 5, False)):
        adj = {r.stock_id: r.close_adj for r in _day(prices, pool, factors, i, adjusted=True)[0]}
        raw = {r.stock_id: r.close_adj for r in _day(prices, pool, factors, i, adjusted=False)[0]}
        assert (adj["1101"] == raw["1101"]) is same, f"{DAYS[i]} 的 1101"
        assert adj["2330"] == raw["2330"], "沒有除權息的股票兩種口徑必須相同"
    i = EX_I
    adj = {r.stock_id: r.close_adj for r in _day(prices, pool, factors, i, adjusted=True)[0]}
    raw = {r.stock_id: r.close_adj for r in _day(prices, pool, factors, i, adjusted=False)[0]}
    assert adj["1101"] == pytest.approx(raw["1101"] * (100.0 / 80.0))   # before/after


def test_day_records_rank_pool_gating(db):
    """`rank_pool` 決定 `in_rank_pool`；`None` ＝全 True，**只有量測用得到**。"""
    prices, uni, _ = db
    pool, (factors, _) = F.load_pool(uni), F.load_factors(prices, DV)
    recs, _ = _day(prices, pool, factors, LATE_I, rank_pool=frozenset({"1101", "2330"}))
    got = {r.stock_id: r.in_rank_pool for r in recs}
    assert got["1101"] is True and got["2330"] is True
    assert got["1103"] is False and got["6488"] is False
    recs_all, _ = _day(prices, pool, factors, LATE_I, rank_pool=None)
    assert all(r.in_rank_pool for r in recs_all)
    recs_none, _ = _day(prices, pool, factors, LATE_I, rank_pool=frozenset())
    assert not any(r.in_rank_pool for r in recs_none)


def test_iter_days_streams_in_ascending_order(db):
    """`DailyScanner`／`AdvTracker` 都要求嚴格升冪——順序由這裡保證。"""
    prices, _, _ = db
    dates = [d for d, _ in F.iter_days(prices, DV)]
    assert dates == sorted(dates) == DAYS
    assert len(set(dates)) == len(dates)                               # 每日恰一批，不得分裂
    sub = [d for d, _ in F.iter_days(prices, DV, start=DAYS[5], end=DAYS[9])]
    assert sub == DAYS[5:10]


def test_feed_is_the_only_sqlite_layer_in_the_chain():
    """`scan`／`liquidity`／`adjust` 必須維持純函式；DB 只在 `feed` 這一層（用 AST 驗）。"""
    import ast
    for name, want_sqlite in (("feed", True), ("scan", False), ("liquidity", False), ("adjust", False)):
        tree = ast.parse((ROOT / "src" / "iching" / f"{name}.py").read_text(encoding="utf-8"))
        mods = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                mods.update(a.name.split(".")[0] for a in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                mods.add(node.module.split(".")[0])
        assert ("sqlite3" in mods) is want_sqlite, f"{name}.py 的 sqlite3 匯入狀態不符預期"


def test_iter_days_order_by_survives_per_stock_fallback(tmp_path):
    """`ORDER BY date` 在 **per_stock 回退**的資料形狀下才看得出必要性。

    `raw_price_daily` 的實體順序是 PK `(cov_key, row_hash)`；daily_slice 落地時 `cov_key` 就是
    日期，實體序恰好等於日期序，**所以一般的合成 DB 分辨不出有沒有 ORDER BY**（前一版驗收
    就是卡在造不出反例）。但 `config.py` 的 `price_daily` 有 `fallback=per_stock`，那時
    `cov_key` 是 `<id>:<起>~<迄>`、一個鍵裝多日，兩種形狀混在一起實體序就不是日期序了。

    實測：拿掉 `ORDER BY date` 後，3 個日期會被 `groupby` 切成 6 組、每個日期各出現兩次
    （每組只有半天的列）。`DailyScanner.push_day` 會因為日期非升冪而拋錯——**這次是大聲的**，
    但每天的列被切一半仍是真缺陷。
    """
    from iching.store import Store

    dates = ("2020-01-02", "2020-01-03", "2020-01-06")
    with Store(tmp_path / "prices.db") as s:
        s.record_success("price_daily", "raw_price_daily", "9999:2020-01-01~2020-12-31",
                         [{"date": d, "stock_id": "9999", "close": 10.0,
                           "Trading_Volume": 1.0, "Trading_money": 1.0} for d in dates], "dv", "X")
        for d in dates:                                 # cov_key ＝日期，字典序排在 "9999:…" 之前
            s.record_success("price_daily", "raw_price_daily", d,
                             [{"date": d, "stock_id": "1101", "close": 10.0,
                               "Trading_Volume": 1.0, "Trading_money": 1.0}], "dv", "X")
    conn = F.open_ro(tmp_path / "prices.db")
    try:
        got = [(d, len(rows)) for d, rows in F.iter_days(conn, "dv")]
    finally:
        conn.close()
    assert [d for d, _ in got] == list(dates), "日期重複出現＝ORDER BY 沒了，每天的列被切開"
    assert all(n == 2 for _, n in got), "每天應有兩檔（daily_slice 的 1101 ＋ per_stock 的 9999）"


def test_load_factor_rows_merges_four_sources_and_reports_missing_tables(db, tmp_path):
    """裁定 #51：`load_factor_rows` 讀四表 → `merge_factor_rows`；split∪parvalue 同鍵去重（優先 split）、每源筆數可見；
    只建了除權息表的 DB（舊 `build()`）三表缺 → 視為 0 列、記 `missing_tables`、不 raise（回補尚未跑到時仍可計分）。"""
    prices, _, _ = db
    rows, st = F.load_factor_rows(prices, DV)
    assert [(r[0], r[1], r[4]) for r in rows] == [("1101", DAYS[EX_I], "dividend"), ("1101", DAYS[CAPRED_I], "capred"),
                                                  ("2330", DAYS[SPLIT_I], "split"), ("6488", DAYS[PAR_I], "parvalue")]
    assert st["cross_source_dup"] == 1 and st["missing_tables"] == [] and st["meta_only_tables"] == [] and st["anomalies"] == 0
    assert {k: v["kept"] for k, v in st["by_source"].items()} == {"dividend": 1, "capred": 1, "split": 1, "parvalue": 1}
    assert st["by_source"]["parvalue"]["rows"] == 2                     # 兩列進來、一列被 split 蓋掉
    cache2 = tmp_path / "cache_div_only"
    _build(cache2)
    p2 = F.open_ro(cache2 / "prices.db")
    try:
        rows2, st2 = F.load_factor_rows(p2, DV)
        assert [(r[0], r[4]) for r in rows2] == [("1101", "dividend")]
        assert st2["missing_tables"] == ["raw_cap_reduction", "raw_split_price", "raw_par_value_change"]
        f2, s2 = F.load_factors(p2, DV)
        assert s2["stocks"] == 1 and f2["1101"][0] == [DAYS[EX_I]]
    finally:
        p2.close()


def test_load_factor_rows_meta_only_table_is_zero_rows_and_dv_mismatch_raises(tmp_path):
    """2026-09-18 驗收後修正 (b)(c)：
    (b) `Store.record_success(spec, key, [], …)` 真實路徑建出的 **meta-only 表**（只有 cov_key／row_hash／data_version／date／
        stock_id／extra 六欄、零列——空年塊先落地、之後沒有非空塊）→ `load_factor_rows` 不炸、視為 0 列、記 `meta_only_tables`；
        表**有列**卻缺 before／after 欄 → 仍 `FeedError`（真的壞）。
    (c) 某表塞的是**另一個 data_version** 的列（表有列、本 dv 零列）→ `FeedError` 且訊息列出表內 dv 與期望 dv（原本只記 warning，
        係數會靜默少掉整個事件源）；表不存在／零列仍是 0 列不 raise。"""
    from iching import config as C
    from iching import factor_sources as FS
    from iching.store import Store
    cache = tmp_path / "cache"
    _build(cache)
    add_adjust_source_rows(cache)
    with Store(cache / "prices.db") as st_:
        st_.conn.execute('DROP TABLE "raw_split_price"')
        spec = C.DATASET_BY_KEY["split_price"]
        st_.record_success(spec.key, spec.table, "2023-01-01~2023-12-31", [], DV, spec.dataset, spec.index_cols, create_indexes=False)
        assert st_.columns("raw_split_price") == {"cov_key", "row_hash", "data_version", "date", "stock_id", "extra"}
    conn = F.open_ro(cache / "prices.db")
    try:
        rows, st = F.load_factor_rows(conn, DV)
        assert st["meta_only_tables"] == ["raw_split_price"] and st["missing_tables"] == []
        # split 源 0 列 → 2330 那筆改由 parvalue 獨有列補上（split∪parvalue 去重 0）
        assert [(r[0], r[4]) for r in rows] == [("1101", "dividend"), ("1101", "capred"), ("2330", "parvalue"), ("6488", "parvalue")]
        assert st["by_source"]["split"]["kept"] == 0 and st["cross_source_dup"] == 0
        assert "meta-only 空表視為 0 列：['raw_split_price']" in FS.format_source_stat(st)
        f, fs = F.load_factors(conn, DV)
        assert fs["stocks"] == 3 and f["2330"][1] == [4.0]
    finally:
        conn.close()
    # (b) 表有列卻缺欄 → 仍 raise
    with Store(cache / "prices.db") as st_:
        st_.record_success("split_price", "raw_split_price", "2024-01-01~2024-12-31",
                           [{"date": DAYS[SPLIT_I], "stock_id": "2330", "type": "面額變更"}], DV, "TaiwanStockSplitPrice", create_indexes=False)
    conn = F.open_ro(cache / "prices.db")
    try:
        with pytest.raises(F.FeedError, match=r"raw_split_price 缺欄位 \['after_price', 'before_price'\]"):
            F.load_factor_rows(conn, DV)
    finally:
        conn.close()
    # (c) 減資表只有另一個 dv 的列 → raise，訊息列出兩邊 dv
    with Store(cache / "prices.db") as st_:
        st_.conn.execute('DROP TABLE "raw_split_price"')
        st_.conn.execute('DELETE FROM "raw_cap_reduction"')
        st_.record_success("cap_reduction", "raw_cap_reduction", "1101:2020",
                           [{"date": DAYS[CAPRED_I], "stock_id": "1101", "ClosingPriceonTheLastTradingDay": 100.0,
                             "PostReductionReferencePrice": 200.0}], "fm-20990101-01", "TaiwanStockCapitalReductionReferencePrice")
    conn = F.open_ro(cache / "prices.db")
    try:
        with pytest.raises(F.FeedError, match=r"raw_cap_reduction 有列但沒有 data_version=" + DV + r" 的列；表內 data_version＝\['fm-20990101-01'\]"):
            F.load_factor_rows(conn, DV)
        with pytest.raises(F.FeedError):
            F.load_factors_full(conn, DV)
        # 對照：同表以本 dv 讀「另一個世界」——表零列的情況不 raise
        conn.close()
        with Store(cache / "prices.db") as st_:
            st_.conn.execute('DELETE FROM "raw_cap_reduction"')
        conn = F.open_ro(cache / "prices.db")
        rows3, st3 = F.load_factor_rows(conn, DV)
        assert st3["missing_tables"] == ["raw_split_price"] and st3["meta_only_tables"] == [] and st3["by_source"]["capred"]["kept"] == 0
        assert [(r[0], r[4]) for r in rows3] == [("1101", "dividend"), ("2330", "parvalue"), ("6488", "parvalue")]
    finally:
        conn.close()


def test_day_records_apply_capital_reduction_split_and_par_value(db):
    """減資日起 adj < raw（1.25×0.5＝0.625）；分割日起 ×4（不是 ×16：同事件兩表去重）；面額變更日起 ×10；事件前一日不變。"""
    prices, uni, _ = db
    pool, (factors, _) = F.load_pool(uni), F.load_factors(prices, DV)
    assert factors["1101"] == ([DAYS[EX_I], DAYS[CAPRED_I]], [pytest.approx(1.25), pytest.approx(0.625)])

    def pair(i, sid):
        adj = {r.stock_id: r.close_adj for r in _day(prices, pool, factors, i, adjusted=True)[0]}[sid]
        raw = {r.stock_id: r.close_adj for r in _day(prices, pool, factors, i, adjusted=False)[0]}[sid]
        return adj, raw
    a, r = pair(CAPRED_I - 1, "1101")
    assert a == pytest.approx(r * 1.25) and a > r
    a, r = pair(CAPRED_I, "1101")
    assert a == pytest.approx(r * 0.625) and a < r                      # 減資日起 adj < raw
    a, r = pair(SPLIT_I - 1, "2330")
    assert a == r
    a, r = pair(SPLIT_I, "2330")
    assert a == pytest.approx(r * 4.0)
    a, r = pair(PAR_I, "6488")
    assert a == pytest.approx(r * 10.0)
