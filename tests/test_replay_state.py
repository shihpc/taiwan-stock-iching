"""第 13 項 13a-1：`iching.replay_state`（純函式層）＋ `iching.replay_io`（DB 讀取層）。

驗收條件（動手前寫，2026-09-13）：
1. `CrossDayState` 往返逐位相同；空狀態＝首日語意（遲滯 None／二爻歷史左補 None 至 9 或 5）。
2. `WindowCache` 個股 OHLC **後復權**，與 `feed.day_records` 同日同檔 `close_adj` 逐位相同；
   非成交列／無指數列不推進；法人無列補 0、餘額無列補 NaN。
3. 與 `score_io` 兩個 builder 逐欄比對（無除權息、全程有成交的 2330；後復權差異只在有事件的 1101）。
4. 靜態守門：`replay_state.py` 無 `sqlite`、`replay*.py` 無 `timedelta(days=`（AST／grep）。
5. 記憶體量級：`Ring` 每檔 = window×10×8 bytes，2,139×320 ＝ 54.8 MB。
6. `ad_line` 視窗內從 0 起算 → 只餵最後 `window` 天的快取與餵全段的快取，`market_inputs` 逐位相同（每日班 parity 的本體）。
"""
from __future__ import annotations

import ast
import json
import re
import sys
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import scan_features as SF  # noqa: E402
from iching import feed as F  # noqa: E402
from iching import replay_io as RIO  # noqa: E402
from iching import replay_state as RS  # noqa: E402
from iching import score_io  # noqa: E402
from iching.features_io import FeatureStore, FeatureStoreError  # noqa: E402
from iching.score import score_market, score_stock  # noqa: E402
from iching.score.hexagram import YANG, YIN  # noqa: E402
from iching.score.params import HORIZONS, build_params  # noqa: E402
from iching.store import Store  # noqa: E402
from synth_db import DAYS, DV, EX_I, build_full  # noqa: E402

SRC = ROOT / "src" / "iching"


# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------
@pytest.fixture(scope="module")
def cache(tmp_path_factory) -> Path:
    c = tmp_path_factory.mktemp("replay") / "cache"
    build_full(c)
    assert SF.main(["--cache-dir", str(c), "--quiet", "--allow-short-warmup", "--warmup-days", "0"]) == 0
    return c


def _replay(cache: Path, window: int = 60, start: str | None = None):
    src = RIO.ReplaySource(cache, DV, window=window)
    wc = RS.WindowCache(src.pool, src.factors, window=window)
    cross = RS.CrossDayState()
    for T in src.trading_dates(start=start):
        wc.ingest(src.read_day(T))
    return src, wc, cross


@pytest.fixture(scope="module")
def replayed(cache):
    src, wc, cross = _replay(cache)
    yield src, wc, cross
    src.close()


# ---------------------------------------------------------------------------
# 1. CrossDayState
# ---------------------------------------------------------------------------
def test_cross_day_state_empty_means_first_day():
    c = RS.CrossDayState()
    assert c.stock_line2_history("2330", "short") == [None] * 9
    assert c.market_line2_t_minus_5("twse", "mid") is None
    rules = build_params("twse").rules
    formal, streaks = c.advance_lines("market", "twse", "short", [60, 40, 50, 49.9, 55, 45], rules)
    assert formal == [1, 0, 1, 0, 1, 0] and streaks == [0] * 6          # 首日 50 分界、streak 0
    formal2, _ = c.advance_lines("stock", "2330", "short", [60, None, 50, 50, 50, 50], rules)
    assert formal2 is None                                               # 有一爻首日就缺 → formal None
    assert c.stock_lines["2330|short"][1] == (None, 0)


def test_cross_day_state_hysteresis_and_history_progression():
    rules = build_params("twse").rules
    c = RS.CrossDayState()
    c.advance_lines("market", "twse", "short", [40] * 6, rules)          # 全陰
    c.advance_lines("market", "twse", "short", [56] * 6, rules)          # 第 1 天 ≥55
    f, s = c.advance_lines("market", "twse", "short", [56] * 6, rules)   # 第 2 天確認 → 翻陽
    assert f == [1] * 6 and s == [0] * 6
    f, s = c.advance_lines("market", "twse", "short", [None] * 6, rules)  # 缺分數：狀態與 streak 原樣
    assert f == [1] * 6 and c.market_lines["twse|short"][0] == (YANG, 0)
    for i in range(7):
        c.push_market_line2("twse", "short", 40.0 + i)
        c.push_stock_line2("2330", "swing", 50.0 + i)
    assert c.market_line2["twse|short"] == [42.0, 43.0, 44.0, 45.0, 46.0] and c.market_line2_t_minus_5("twse", "short") == 42.0
    assert c.stock_line2_history("2330", "swing") == [None, None, 50.0, 51.0, 52.0, 53.0, 54.0, 55.0, 56.0]
    c.push_stock_line2("2330", "swing", None)
    assert c.stock_line2_history("2330", "swing")[-1] is None and len(c.stock_line2_history("2330", "swing")) == 9


def test_cross_day_state_roundtrip_bitwise():
    rules = build_params("tpex").rules
    c = RS.CrossDayState(last_date="2020-04-20")
    c.advance_lines("market", "tpex", "mid", [60, 40, 50, 50, 50, 50], rules)
    c.advance_lines("stock", "6488", "short", [60, None, 50, 50, 50, 50], rules)
    c.push_stock_line2("6488", "short", 51.123456789)
    c.push_market_line2("tpex", "mid", None)
    c.adv.push_day("2020-04-20", {"6488": 3.5e7, "1101": 1e7})
    j = c.to_json()
    c2 = RS.CrossDayState.from_json(j)
    assert c2.to_json() == j
    assert c2.stock_lines["6488|short"][1] == (None, 0) and c2.market_lines["tpex|mid"][0] == (YANG, 0)
    assert c2.market_lines["tpex|mid"][1] == (YIN, 0)
    assert c2.adv.adv_of("6488") == c.adv.adv_of("6488") and c2.adv.last_date == "2020-04-20"
    assert c2.stock_line2_history("6488", "short")[-1] == 51.123456789
    bad = json.loads(j)
    bad["schema"] = 99
    with pytest.raises(RS.ReplayStateError):
        RS.CrossDayState.from_dict(bad)
    with pytest.raises(RS.ReplayStateError):
        c.advance_lines("market", "tpex", "mid", [50] * 5, rules)


# ---------------------------------------------------------------------------
# 2. WindowCache：後復權、推進規則、缺值語意
# ---------------------------------------------------------------------------
def test_stock_window_is_back_adjusted_like_feed(replayed):
    src, wc, cross = replayed
    T = DAYS[-1]
    prices = F.open_ro(src.cache / "prices.db")
    rows = [r for d, r in F.iter_days(prices, DV, False, T, T)][0]
    recs, _ = F.day_records(T, rows, src.pool, src.factors)
    prices.close()
    by = {r.stock_id: r.close_adj for r in recs}
    a = wc.stock_window("1101")
    assert a[-1, 3] == by["1101"]                              # 逐位相同（同一個 factor_at）
    raw = 100.0 * (1.01 ** (len(DAYS) - 1)) * 0.8
    assert by["1101"] == pytest.approx(round(raw, 4) * 1.25) and a[-1, 3] != pytest.approx(raw)
    # 除息前一天（i=EX_I-1）係數 1.0、除息日起 1.25：視窗內既有值不回寫、只有之後的列被放大
    i0 = len(DAYS) - wc.window
    assert a[EX_I - 1 - i0, 3] == pytest.approx(round(100.0 * 1.01 ** (EX_I - 1), 4))
    assert a[EX_I - i0, 3] == pytest.approx(round(100.0 * 1.01 ** EX_I * 0.8, 4) * 1.25)
    assert wc.stock_window("2330")[-1, 3] == by["2330"]         # 無事件：與原始價相同


def test_non_traded_rows_and_missing_index_do_not_advance(cache):
    src, wc, _ = _replay(cache, window=80)
    assert wc.stock_window("1102").shape[0] == 80 - 2            # 停牌兩日不推進
    assert wc.stock_window("6488").shape[0] == 80 - 1            # 畸形列不推進
    assert wc.stock_window("1103").shape[0] == 80 - 30           # 第 30 日才上市
    assert "0050" not in wc._stock and "9101" not in wc._stock   # 不在池
    # 指數缺列：手動餵一天沒有 twse 指數的 bundle → twse 個股不推進、tpex 推進
    b = RS.DayBundle(tpe_date="2020-05-01", index={"tpex": {"close": 210.0}},
                     stocks={"1101": {"close": 200.0, "Trading_Volume": 1000.0, "amount": 1.0},
                             "6488": {"close": 70.0, "Trading_Volume": 1000.0, "amount": 1.0}})
    wc.ingest(b)
    assert wc.stock_window("1101").shape[0] == 80 and wc.stock_window("6488").shape[0] == 80
    assert wc.stock_ids_today() == ["6488"] and wc.tpe_dates[-1] == "2020-05-01"
    assert wc.market_dates("twse")[-1] == DAYS[-1] and wc.market_dates("tpex")[-1] == "2020-05-01"
    # 兩市場都沒有指數列的一天：tpe_dates（line 6 stale_days 的軸）不得 append，市場軸也不動
    n_tpe = len(wc.tpe_dates)
    wc.ingest(RS.DayBundle(tpe_date="2020-05-02", stocks={"6488": {"close": 71.0, "Trading_Volume": 1000.0, "amount": 1.0}}))
    assert len(wc.tpe_dates) == n_tpe and wc.tpe_dates[-1] == "2020-05-01" and wc.stock_ids_today() == []
    assert wc.market_dates("tpex")[-1] == "2020-05-01" and wc.last_date == "2020-05-02"
    with pytest.raises(RS.ReplayStateError):
        wc.ingest(RS.DayBundle(tpe_date="2020-05-01"))          # 倒退
    with pytest.raises(RS.ReplayDateError):
        wc.stock_inputs("6488", "short", "2020-04-30", RS.CrossDayState())
    src.close()


def test_chip_missing_semantics(replayed):
    src, wc, cross = replayed
    T = DAYS[-1]
    s1 = wc.stock_inputs("1101", "short", T, cross)
    i = len(DAYS) - 1
    assert s1.foreign_net_shares[-1] == pytest.approx((5000 + i + 100 - 1000) / 1000)
    assert s1.trust_net_shares[-1] == pytest.approx(-0.5)
    assert s1.margin_balance[-1] == 100 + i and s1.margin_eligible is True
    assert s1.short_sale_balance is None                         # 表不存在 → 視窗內無任何列 → 整欄 None
    assert "raw_short_sale_balance" in src.missing_tables
    s2 = wc.stock_inputs("2330", "short", T, cross)
    assert s2.foreign_net_shares is None and s2.trust_net_shares is None                  # 完全無列 → None（同 score_io）
    assert s2.margin_balance is None and s2.margin_eligible is False
    # 部分缺列：手工 bundle 讓 1101 少一天法人與融資 → 法人補 0、餘額 NaN
    b = RS.DayBundle(tpe_date="2020-05-04", index={"twse": {"close": 10300.0}},
                     stocks={"1101": {"close": 180.0, "Trading_Volume": 1000.0, "amount": 1.0}})
    import copy
    wc2 = copy.deepcopy(wc)
    wc2.ingest(b)
    s3 = wc2.stock_inputs("1101", "short", "2020-05-04", cross)
    assert s3.foreign_net_shares[-1] == 0.0 and s3.foreign_net_shares[-2] == s1.foreign_net_shares[-1]
    assert np.isnan(s3.margin_balance[-1]) and s3.margin_eligible is True and s3.margin_balance[-2] == 100 + i
    assert s2.industry == "半導體業" and s2.market == "twse" and s2.volume[-1] == 1.0


def test_stock_inputs_pick_window_by_horizon_from_features(replayed):
    src, wc, cross = replayed
    T = DAYS[-1]
    fs = FeatureStore(src.cache / "features.db", readonly=True)
    pcs = fs.day_p_cs(DV, "twse", T)["1101"]
    ind = fs.day_industry(DV, "twse", T)["水泥工業"]
    fs.close()
    for h, w in (("short", 10), ("swing", 20), ("mid", 60)):
        s = wc.stock_inputs("1101", h, T, cross, {"short": 55.0, "swing": 55.0, "mid": 55.0})
        assert s.p_cs_long_excess == pcs[w] and s.industry_n == ind["n"][w]
        assert s.industry_median_return == ind["median"]
        cnt, n = ind["above_ma"][20]
        assert s.industry_above_ma_ratio == {20: cnt / n}
        assert s.market_direction_score == {"short": 55.0, "swing": 55.0, "mid": 55.0}
        assert len(s.line2_score_history[h]) == 9
    with pytest.raises(RS.ReplayStateError):
        wc.stock_inputs("0050", "short", T, cross)


# ---------------------------------------------------------------------------
# 3. 與 score_io builder 逐欄比對
# ---------------------------------------------------------------------------
def test_market_inputs_match_score_io_where_semantics_overlap(replayed):
    src, wc, cross = replayed
    T = DAYS[-1]
    stores = score_io.open_score_stores(src.cache)
    ref = score_io.market_inputs_from_stores(stores, "twse", T, list(DAYS), list(DAYS), n=wc.window)
    mi = wc.market_inputs("twse", T, cross)
    for k in ("index_open", "index_high", "index_low", "index_close", "margin_balance", "foreign_net_oi", "vix",
              "spx_close", "spx_high", "spx_low", "sox_close", "fx_usdtwd"):
        a, b = getattr(mi, k), getattr(ref, k)
        assert a is not None and b is not None, k
        assert np.array_equal(np.asarray(a, dtype=float), np.asarray(b, dtype=float)), k
    assert mi.us_dates == list(ref.us_dates) and mi.fx_dates == list(ref.fx_dates)
    assert ref.amount is None and ref.foreign_net_amount is None and ref.basis is None   # score_io 尚未接的
    i = len(DAYS) - 1
    assert mi.amount[-1] == (2e11 + 19 + 4) and mi.foreign_net_amount[-1] == pytest.approx((5000000 + i * 1000 - 1000000) / 1000)
    assert mi.trust_net_amount[-1] == pytest.approx(1000.0)
    assert mi.basis[-1] == pytest.approx(5.0 / (10000.0 + i * 3) * 100.0)             # 近月 close − 現貨
    assert mi.vix[-1] == pytest.approx(21.0 + i * 0.01)                                 # 13:44 那筆，不是 09:00
    assert mi.n_stocks[-1] == 4 and mi.ad_line.shape == mi.n_stocks.shape
    assert mi.line2_score_t_minus_5 == {h: None for h in HORIZONS} and mi.own_state is None and mi.other_market_state is None
    tp = wc.market_inputs("tpex", T, cross)
    assert tp.amount is None and tp.foreign_net_amount is None                          # TPEx 官方兩表刻意不建
    assert tp.basis is not None and tp.index_close[-1] == pytest.approx(200.0 + i * 0.1)
    for s in stores.values():
        s.close()


def test_stock_inputs_match_score_io_for_event_free_stock(replayed):
    src, wc, cross = replayed
    T = DAYS[-1]
    stores = score_io.open_score_stores(src.cache)
    for sid in ("2330", "1101"):
        ref = score_io.stock_inputs_from_stores(stores, "twse", sid, T, n=wc.window)
        mine = wc.stock_inputs(sid, "short", T, cross)
        for k in ("volume", "index_close", "foreign_net_shares", "trust_net_shares", "open", "high", "low"):
            if sid == "1101" and k in ("open", "high", "low"):
                continue
            x, y = getattr(mine, k), getattr(ref, k)
            assert (x is None) == (y is None), (sid, k)
            if x is not None:
                assert np.array_equal(np.asarray(x), np.asarray(y, dtype=float)), (sid, k)
        assert (mine.margin_balance is None) == (ref.margin_balance is None)
        if ref.margin_balance is not None:
            assert np.array_equal(np.asarray(mine.margin_balance), np.asarray(ref.margin_balance, dtype=float), equal_nan=True)
        assert mine.margin_eligible == ref.margin_eligible and mine.short_sale_balance is None and ref.short_sale_balance is None
        if sid == "2330":
            assert np.array_equal(mine.close, ref.close)                                  # 無事件：逐位相同
        else:
            assert not np.array_equal(mine.close, ref.close)                              # 後復權 vs 原始價：刻意不同
            assert np.array_equal(mine.close[: EX_I - (len(DAYS) - wc.window)], ref.close[: EX_I - (len(DAYS) - wc.window)])
    for s in stores.values():
        s.close()


def test_engine_accepts_inputs_end_to_end(replayed):
    src, wc, cross = replayed
    T = DAYS[-1]
    ps = {m: build_params(m) for m in ("twse", "tpex")}
    for m in ("twse", "tpex"):
        for h in HORIZONS:
            ms = score_market(wc.market_inputs(m, T, cross), ps[m], h)
            assert ms.tpe_date == T and len(ms.line_scores()) == 6
    for sid in wc.stock_ids_today():
        for h in HORIZONS:
            ss = score_stock(wc.stock_inputs(sid, h, T, cross), ps["twse" if sid != "6488" else "tpex"], h)
            formal, streaks = cross.advance_lines("stock", sid, h, ss.line_scores(), ps["twse"].rules)
            assert len(streaks) == 6


# ---------------------------------------------------------------------------
# 6. ad_line 視窗內起算：只餵最後 window 天 ＝ 餵全段（每日班重建路徑）
# ---------------------------------------------------------------------------
def test_window_rebuilt_from_last_n_days_is_bitwise_identical(cache):
    w = 30
    src_a, wc_a, _ = _replay(cache, window=w)
    src_b, wc_b, _ = _replay(cache, window=w, start=DAYS[-w])
    T = DAYS[-1]
    cross = RS.CrossDayState()
    for m in ("twse", "tpex"):
        a, b = wc_a.market_inputs(m, T, cross), wc_b.market_inputs(m, T, cross)
        for k in ("index_close", "amount", "foreign_net_amount", "n_stocks", "ad_line", "advance_ratio", "up_amount_ratio",
                  "margin_balance", "foreign_net_oi", "vix", "basis", "spx_close", "fx_usdtwd"):
            x, y = getattr(a, k), getattr(b, k)
            if x is None:
                assert y is None, k
            else:
                assert np.array_equal(np.asarray(x), np.asarray(y), equal_nan=True), (m, k)
        for wn in a.above_ma_ratio:
            assert np.array_equal(a.above_ma_ratio[wn], b.above_ma_ratio[wn], equal_nan=True)
        assert a.contract_rolled == b.contract_rolled
    for sid in wc_a.stock_ids_today():
        assert np.array_equal(wc_a.stock_window(sid), wc_b.stock_window(sid), equal_nan=True), sid
    src_a.close()
    src_b.close()


def test_ad_line_starts_at_zero_within_window(replayed):
    src, wc, cross = replayed
    mi = wc.market_inputs("twse", DAYS[-1], cross)
    fs = FeatureStore(src.cache / "features.db", readonly=True)
    b0 = fs.day_breadth(DV, "twse", DAYS[-wc.window])
    fs.close()
    assert mi.ad_line[0] == b0["advance_count"] - b0["decline_count"]


# ---------------------------------------------------------------------------
# replay_io 細節
# ---------------------------------------------------------------------------
def test_replay_io_official_and_rolled(replayed):
    src, wc, cross = replayed
    assert src.official_errors == []
    b = src.read_day("2020-03-17")
    assert b.futures["contracts"] == ["202003", "202003/202004"] and b.official["twse"]["amount_k"] == 2e11 + 16 + 3
    assert b.futures["close"]["202003"] == 10000.0 + 56 * 3 + 5                        # position 時段，不是 after_market 的 1.0
    assert b.official["tpex"] == {"amount_k": None, "foreign_net_k": None, "trust_net_k": None}
    assert {"raw_tpex_inst_summary", "raw_tpex_trading_index"} <= src.missing_tables
    # 換月：03-18（最後交易日）近月仍 202003、03-19 起 202004、04-16 起 202005 → 兩個換月日 True，其餘 False
    src2, wc2, _ = _replay(src.cache, window=10, start="2020-03-01")
    flags = {}
    src3 = RIO.ReplaySource(src.cache, DV, window=10)
    wc3 = RS.WindowCache(src3.pool, src3.factors, window=10)
    for T in src3.trading_dates(start="2020-03-01"):
        wc3.ingest(src3.read_day(T))
        flags[T] = wc3.contract_rolled
    assert [d for d, f in flags.items() if f] == ["2020-03-19", "2020-04-16"]
    src2.close()
    src3.close()


def test_replay_io_refuses_missing_features_and_records_parse_errors(tmp_path, cache):
    with pytest.raises(RIO.ReplayIOError):
        RIO.ReplaySource(cache, DV, features_path=tmp_path / "沒有.db")
    with pytest.raises(FeatureStoreError):
        FeatureStore(tmp_path / "沒有.db", readonly=True)
    # 壞掉的官方 body：記錯不炸、該日金額缺
    import shutil
    c2 = tmp_path / "cache2"
    shutil.copytree(cache, c2)
    with Store(c2 / "market.db") as m:
        m.record_success("twse_bfi82u", "raw_twse_bfi82u", "2020-01-05",
                         [{"date": "2020-01-05", "stock_id": "x", "http_status": 200, "stat": "OK", "body": "{\"stat\": \"OK\", \"data\": []}"}], DV, "X")
    src = RIO.ReplaySource(c2, DV)
    b = src.read_day("2020-01-05")
    assert b.official["twse"]["foreign_net_k"] is None and b.official["twse"]["amount_k"] is not None
    assert src.official_errors and src.official_errors[0][:2] == ("2020-01-05", "raw_twse_bfi82u")
    src.close()


def test_feature_store_readers_match_written_day(cache):
    fs = FeatureStore(cache / "features.db", readonly=True)
    T = DAYS[-1]
    b = fs.day_breadth(DV, "twse", T)
    assert b["n_stocks"] == 4 and set(b["above_ma"]) == {5, 10, 20, 60} and set(b["new_high"]) == {10, 20, 60}
    assert fs.day_breadth(DV, "twse", "1999-01-01") is None
    ind = fs.day_industry(DV, "twse", T)
    assert set(ind) == {"水泥工業", "半導體業"} and set(ind["水泥工業"]["median"]) == {5, 10, 20, 60}
    assert ind["水泥工業"]["above_ma"][20][1] == 3                    # 產業內有成交檔數
    p = fs.day_p_cs(DV, "twse", T)
    assert "1101" in p and set(p["1101"]) == {10, 20, 60}
    with pytest.raises(Exception):
        fs.conn.execute("DELETE FROM scan_day")                          # 唯讀
    fs.close()


# ---------------------------------------------------------------------------
# 4. 靜態守門　5. 記憶體
# ---------------------------------------------------------------------------
def test_static_gates():
    st = (SRC / "replay_state.py").read_text(encoding="utf-8")
    assert not re.search(r"sqlite", st, re.I)
    tree = ast.parse(st)
    imported = {n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)} | \
               {a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
    assert not any("sqlite" in (m or "") for m in imported)
    for p in SRC.glob("replay*.py"):
        assert "timedelta(days=" not in p.read_text(encoding="utf-8"), p.name
    assert "import sqlite3" in (SRC / "replay_io.py").read_text(encoding="utf-8")


def test_memory_order_of_magnitude(replayed):
    src, wc, _ = replayed
    per_stock = wc.window * len(RS.STOCK_COLS) * 8
    assert wc._stock["2330"].buf.nbytes == per_stock
    assert 2139 * 320 * len(RS.STOCK_COLS) * 8 / 2**20 < 60               # 54.8 MB
    assert wc.nbytes() < 5 * 2**20
