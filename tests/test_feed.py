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
from synth_db import DAYS, DV, EX_I, LATE_I, MALFORMED_I, SUSPEND_I, build as _build  # noqa: E402


@pytest.fixture(scope="module")
def db(tmp_path_factory):
    cache = tmp_path_factory.mktemp("feed") / "cache"
    _build(cache)
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
    assert pool["6488"]["type"] == "tpex"
    factors, stat = F.load_factors(prices, DV)
    assert stat["stocks"] == 1 and stat["bad_skipped"] == 0
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
