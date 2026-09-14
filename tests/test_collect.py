"""`iching.collect`（每日班 D-1）：原料列 → `DayBundle` 各欄的純函式建構器。

三層：①逐函式單元（含缺欄／None／重複列語意）②**與列序無關**（同一批列打亂後輸出逐位相同）
③**與 `ReplaySource.read_day` 逐位等價**——直接 `SELECT *` 全欄餵 `collect`（模擬每日班拿到的完整 API 列），
序列化後與回補層讀出的原料包位元組相同。官方 fixture 借 `test_official_parse.py` 的真實回應。
"""
from __future__ import annotations

import math
import random
import sqlite3
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import scan_features as SF  # noqa: E402

from iching import bundle_io as B  # noqa: E402
from iching import collect as C  # noqa: E402
from iching import replay_io as RIO  # noqa: E402
from iching.official_parse import OfficialParseError  # noqa: E402
from iching.replay_state import DayBundle  # noqa: E402
from synth_db import DAYS, DV, build_full  # noqa: E402
from test_official_parse import BFI82U, FMTQIK, TPEX_IDX, TPEX_INST  # noqa: E402

POOL = {"1101": {}, "2330": {}}


def _nan(x) -> bool:
    return isinstance(x, float) and math.isnan(x)


# ---------------------------------------------------------------------------
def test_index_from_rows_keeps_null_close_and_ignores_unknown_ids():
    rows = [{"stock_id": "TAIEX", "open": 1.0, "max": 3.0, "min": 0.5, "close": 2.0, "Trading_money": 9},
            {"stock_id": "TPEx", "close": None},
            {"stock_id": "0050", "close": 150.0}]
    out = C.index_from_rows(rows)
    assert out == {"twse": {"open": 1.0, "high": 3.0, "low": 0.5, "close": 2.0}, "tpex": {"open": None, "high": None, "low": None, "close": None}}
    assert C.index_from_rows([]) == {}
    # 同市場多列後者覆蓋
    assert C.index_from_rows([{"stock_id": "TAIEX", "close": 1.0}, {"stock_id": "TAIEX", "close": 2.0}])["twse"]["close"] == 2.0


def test_stocks_from_rows_pool_filter_and_chip_semantics():
    price = [{"stock_id": "1101", "open": 99.0, "max": 102.0, "min": 98.0, "close": 100.0, "Trading_Volume": 1000.0, "Trading_money": 1e5},
             {"stock_id": "2330", "close": None, "Trading_Volume": 500.0, "Trading_money": 0.0},   # 畸形列：保留、close None
             {"stock_id": "0050", "close": 150.0, "Trading_Volume": 1.0, "Trading_money": 1.0}]    # 不在池：丟
    inst = [{"stock_id": "1101", "name": "Foreign_Investor", "buy": 5000.0, "sell": 1000.0},
            {"stock_id": "1101", "name": "Foreign_Dealer_Self", "buy": 100.0, "sell": 0.0},
            {"stock_id": "1101", "name": "Investment_Trust", "buy": 2000.0, "sell": 2500.0},
            {"stock_id": "1101", "name": "Dealer_self", "buy": 9.0, "sell": 0.0},                  # 非外資／投信：忽略
            {"stock_id": "9999", "name": "Foreign_Investor", "buy": 1.0, "sell": 0.0}]             # 不在價量列：忽略
    out = C.stocks_from_rows(price, POOL, inst_rows=inst,
                             margin_rows=[{"stock_id": "1101", "MarginPurchaseTodayBalance": 100}, {"stock_id": "2330", "MarginPurchaseTodayBalance": None}],
                             short_rows=[{"stock_id": "1101", "SBLShortSalesCurrentDayBalance": "7"}],
                             shareholding_rows=[{"stock_id": "1101", "NumberOfSharesIssued": 1e9}, {"stock_id": "2330", "NumberOfSharesIssued": None}])
    assert set(out) == {"1101", "2330"}
    s = out["1101"]
    assert (s["open"], s["high"], s["low"], s["close"], s["Trading_Volume"], s["volume"], s["amount"]) == (99.0, 102.0, 98.0, 100.0, 1000.0, 1000.0, 1e5)
    assert s["foreign_net"] == (5000.0 - 1000.0 + 100.0) / 1000.0 and s["trust_net"] == -0.5
    assert s["margin_balance"] == 100.0 and s["short_sale_balance"] == 7.0 and s["shares_outstanding"] == 1e9
    t = out["2330"]
    assert t["close"] is None and t["open"] is None and t["Trading_Volume"] == 500.0
    assert t["foreign_net"] is None and t["trust_net"] is None            # 無法人列 → None（ingest 端才補 0）
    assert _nan(t["margin_balance"]) and t["short_sale_balance"] is None and t["shares_outstanding"] is None
    assert C.stocks_from_rows([], POOL, inst_rows=inst) == {}


def test_inst_duplicate_rows_override_not_sum():
    """同 (stock_id, name) 重送兩列＝後者覆蓋（同 `score_io.load_stock_inst_net`），不是相加。"""
    price = [{"stock_id": "1101", "close": 1.0, "Trading_Volume": 1.0, "Trading_money": 1.0}]
    inst = [{"stock_id": "1101", "name": "Foreign_Investor", "buy": 1000.0, "sell": 0.0},
            {"stock_id": "1101", "name": "Foreign_Investor", "buy": 3000.0, "sell": 0.0}]
    assert C.stocks_from_rows(price, POOL, inst_rows=inst)["1101"]["foreign_net"] == 3.0


def test_official_day_twse_and_tpex_with_real_fixtures():
    month = C.parse_month_body("twse", FMTQIK)
    assert month["2020-01-02"] == 142_714_556.0 and len(month) == 3
    out, errs = C.official_day("twse", "2020-01-02", BFI82U, month)
    assert errs == [] and out == {"amount_k": 142_714_556.0, "foreign_net_k": -3_432_622.0, "trust_net_k": -77_618.0}
    tmonth = C.parse_month_body("tpex", TPEX_IDX)
    out2, errs2 = C.official_day("tpex", "2020-01-03", TPEX_INST, tmonth)
    assert errs2 == [] and out2["amount_k"] == tmonth["2020-01-03"] and out2["foreign_net_k"] == -48_351.0 and out2["trust_net_k"] == 149_718.0
    # 缺：法人 body None → 兩欄 None；月表無該日 → amount None；月表 None 亦同
    out3, errs3 = C.official_day("twse", "2020-01-31", None, month)
    assert errs3 == [] and out3 == {"amount_k": None, "foreign_net_k": None, "trust_net_k": None}
    assert C.official_day("tpex", "2020-01-02", None, None)[0]["amount_k"] is None
    # 壞 body → 錯誤清單指名表、該日欄位 None、不炸
    out4, errs4 = C.official_day("twse", "2020-01-02", {"stat": "OK", "data": []}, month)
    assert out4["foreign_net_k"] is None and out4["amount_k"] == 142_714_556.0
    assert len(errs4) == 1 and errs4[0][:2] == ("2020-01-02", "raw_twse_bfi82u")
    with pytest.raises(OfficialParseError):
        C.parse_month_body("twse", {"stat": "OK"})


def test_futures_vix_margin_oi_builders():
    rows = [{"futures_id": "TX", "contract_date": "202003", "trading_session": "position", "close": 10005.0},
            {"futures_id": "TX", "contract_date": "202003", "trading_session": "after_market", "close": 1.0},
            {"futures_id": "TX", "contract_date": "202003/202004", "trading_session": "position", "close": -3.0},
            {"futures_id": "MTX", "contract_date": "202003", "trading_session": "position", "close": 9.0},
            {"futures_id": "TX", "contract_date": None, "trading_session": "position", "close": 1.0},
            {"futures_id": "TX", "contract_date": "202004", "trading_session": "position", "close": None}]
    assert C.futures_from_rows(rows) == {"contracts": ["202003", "202003/202004"], "close": {"202003": 10005.0, "202003/202004": -3.0}}
    assert C.futures_from_rows([{"futures_id": "TX", "contract_date": "202003", "close": 5.0}]) == {"contracts": ["202003"], "close": {"202003": 5.0}}  # 無 session 欄不濾
    assert C.futures_from_rows([]) == {}
    oi = [{"futures_id": "TX", "institutional_investors": "外資", "long_open_interest_balance_volume": 1000, "short_open_interest_balance_volume": 500},
          {"futures_id": "TX", "institutional_investors": "投信", "long_open_interest_balance_volume": 9, "short_open_interest_balance_volume": 0}]
    assert C.futures_oi_from_rows(oi) == 500.0 and C.futures_oi_from_rows(oi[1:]) is None
    tm = [{"name": "ShortSale", "TodayBalance": 7.0}, {"name": "MarginPurchaseMoney", "TodayBalance": 1e9}]
    assert C.total_margin_from_rows(tm) == 1e9 and C.total_margin_from_rows(tm[:1]) is None
    vix = [{"time": "09:00:00", "vix": 20.0}, {"time": "13:44:00", "vix": 21.5}, {"time": "10:00:00", "vix": 25.0}]
    assert C.vix_from_rows(vix) == 21.5
    assert C.vix_from_rows([{"vix": 1.0}, {"vix": 2.0}]) == 2.0                     # 無 time 欄：最後一列
    assert C.vix_from_rows([]) is None and C.vix_from_rows([{"time": "x"}]) is None


def test_us_and_fx_builders():
    us = [{"date": "2020-01-03", "stock_id": "^SOX", "Close": 3001.0, "High": 1, "Low": 1},
          {"date": "2020-01-03", "stock_id": "^GSPC", "Close": 4001.0, "High": 4011.0, "Low": 3991.0},
          {"date": "2020-01-02", "stock_id": "^GSPC", "Close": 4000.0, "High": 4010.0, "Low": 3990.0},
          {"date": "2020-01-06", "stock_id": "^SOX", "Close": 1.0, "High": 1, "Low": 1}]              # 只有 SOX：整日不出
    out = C.us_from_rows(us)
    assert [x[0] for x in out] == ["2020-01-02", "2020-01-03"]
    assert out[1] == ("2020-01-03", 4001.0, 4011.0, 3991.0, 3001.0)
    assert out[0][:4] == ("2020-01-02", 4000.0, 4010.0, 3990.0) and _nan(out[0][4])
    fx = [{"date": "2020-01-03", "spot_buy": 31.0, "spot_sell": 31.2}, {"date": "2020-01-02", "spot_buy": 30.0, "spot_sell": 30.0},
          {"date": "2020-01-03", "spot_buy": 31.0, "spot_sell": 31.4}]
    assert C.fx_from_rows(fx) == [("2020-01-02", 30.0), ("2020-01-03", 31.2)]


# ---------------------------------------------------------------------------
@pytest.fixture(scope="module")
def cache(tmp_path_factory) -> Path:
    c = tmp_path_factory.mktemp("collect") / "cache"
    build_full(c)
    assert SF.main(["--cache-dir", str(c), "--quiet", "--allow-short-warmup", "--warmup-days", "0"]) == 0   # ReplaySource 需要 features.db
    return c


def _all_rows(db: Path, table: str, where: str, params: tuple) -> list[dict]:
    """`SELECT *`：模擬每日班拿到的完整 API 列（含 collect 不認識的欄）。表不存在回空。"""
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        if not con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone():
            return []
        con.row_factory = sqlite3.Row
        return [dict(r) for r in con.execute(f'SELECT * FROM "{table}" WHERE data_version=? AND {where}', (DV, *params)).fetchall()]
    finally:
        con.close()


def _bundle_via_collect(cache: Path, pool, T: str, window: int, us_last: str | None, fx_last: str | None) -> DayBundle:
    import json
    pr, ch, mk = cache / "prices.db", cache / "chips.db", cache / "market.db"
    b = DayBundle(tpe_date=T)
    b.index = C.index_from_rows(_all_rows(pr, "raw_index_price", "date=?", (T,)))
    b.stocks = C.stocks_from_rows(_all_rows(pr, "raw_price_daily", "date=?", (T,)), pool,
                                  inst_rows=_all_rows(ch, "raw_inst_buysell", "date=?", (T,)),
                                  margin_rows=_all_rows(ch, "raw_margin", "date=?", (T,)),
                                  short_rows=_all_rows(ch, "raw_short_sale_balance", "date=?", (T,)),
                                  shareholding_rows=_all_rows(ch, "raw_shareholding", "date=?", (T,)))
    for m in ("twse", "tpex"):
        inst = _all_rows(mk, C.OFFICIAL_INST_TABLE[m], "date=?", (T,))
        mon = _all_rows(mk, C.OFFICIAL_MONTH_TABLE[m], "month=?", (T[:4] + T[5:7],))
        amounts = C.parse_month_body(m, json.loads(mon[-1]["body"])) if mon else None
        b.official[m], errs = C.official_day(m, T, json.loads(inst[-1]["body"]) if inst else None, amounts)
        assert errs == []
    b.futures = C.futures_from_rows(_all_rows(mk, "raw_futures_daily", "date=?", (T,)))
    b.foreign_net_oi = C.futures_oi_from_rows(_all_rows(mk, "raw_futures_inst", "date=?", (T,)))
    b.total_margin = C.total_margin_from_rows(_all_rows(mk, "raw_total_margin", "date=?", (T,)))
    b.vix = C.vix_from_rows(_all_rows(mk, "raw_vix", "date=?", (T,)))
    us = C.us_from_rows(_all_rows(mk, "raw_us_index", "date<=?", (T,)))
    b.us = [x for x in us if x[0] > us_last] if us_last else us[-window:]
    fx = C.fx_from_rows(_all_rows(mk, "raw_fx_usd", "date<=?", (T,)))
    b.fx = [x for x in fx if x[0] > fx_last] if fx_last else fx[-window:]
    return b


def test_collect_on_full_rows_equals_replay_source_bytewise(cache):
    """每日班路徑（全欄 dict 列 → collect）與回補層 `ReplaySource.read_day`（SQL 選欄 → 同一組 collect）序列化逐位相同。"""
    src = RIO.ReplaySource(cache, DV, window=30)
    us_last = fx_last = None
    n_checked = 0
    for T in DAYS:
        a = src.read_day(T)
        b = _bundle_via_collect(cache, src.pool, T, 30, us_last, fx_last)
        assert B.dumps(a) == B.dumps(b), T
        us_last, fx_last = a.us[-1][0] if a.us else us_last, a.fx[-1][0] if a.fx else fx_last
        n_checked += 1
    last = a
    src.close()
    assert n_checked == len(DAYS)
    # 有內容的抽查（不是兩邊都空）
    assert last.stocks["1101"]["foreign_net"] is not None and last.official["twse"]["amount_k"] is not None
    assert last.vix is not None and last.futures and last.foreign_net_oi is not None and last.us and last.fx


def test_builders_are_row_order_independent(cache):
    """同一日全部原料列打亂 5 次，每個建構器輸出逐位相同（每日班 API 回列順序無保證；合成 DB 無重複鍵，
    「後者覆蓋」類的表也一併打亂）。"""
    T = DAYS[-1]
    pr, ch, mk = cache / "prices.db", cache / "chips.db", cache / "market.db"
    inputs = {
        "index": _all_rows(pr, "raw_index_price", "date=?", (T,)),
        "price": _all_rows(pr, "raw_price_daily", "date=?", (T,)),
        "inst": _all_rows(ch, "raw_inst_buysell", "date=?", (T,)),
        "margin": _all_rows(ch, "raw_margin", "date=?", (T,)),
        "oi": _all_rows(mk, "raw_futures_inst", "date=?", (T,)),
        "tm": _all_rows(mk, "raw_total_margin", "date=?", (T,)),
        "fut": _all_rows(mk, "raw_futures_daily", "date=?", (T,)),
        "vix": _all_rows(mk, "raw_vix", "date=?", (T,)),
        "us": _all_rows(mk, "raw_us_index", "date<=?", (T,)),
        "fx": _all_rows(mk, "raw_fx_usd", "date<=?", (T,)),
    }
    src = RIO.ReplaySource(cache, DV, window=30)
    pool = src.pool
    src.close()

    def run(x):
        return (C.index_from_rows(x["index"]), C.stocks_from_rows(x["price"], pool, inst_rows=x["inst"], margin_rows=x["margin"]),
                C.futures_from_rows(x["fut"]), C.futures_oi_from_rows(x["oi"]), C.total_margin_from_rows(x["tm"]),
                C.vix_from_rows(x["vix"]), C.us_from_rows(x["us"]), C.fx_from_rows(x["fx"]))
    base = repr(run(inputs))
    rng = random.Random(7)
    for _ in range(5):
        shuffled = {k: rng.sample(v, len(v)) for k, v in inputs.items()}
        assert repr(run(shuffled)) == base
