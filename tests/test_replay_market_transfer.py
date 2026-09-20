"""跨市場轉市（tpex↔twse）不得污染個股 ring 的指數欄（`docs/P3-CALIBRATION.md` §11／§13）。

缺陷：`replay_state.WindowCache.ingest` 逐日以 `pool.listed(sid, T)` 取**當日**所屬市場、把該市場指數推進
**同一個 ring**，而 ring 在轉市時不重置。跨越轉市日的視窗於是讓 `score/stock.py:_pct_ret(index_close, n)`
拿新市場指數（加權約 4.5 萬點）當分子、舊市場指數（櫃買約 380 點）當分母，得出上萬 pp 的假指數報酬。

修法：轉市時把 ring 內**既有列**的指數欄清成 NaN（該檔自己的價、量、籌碼一律不動），
由 `_pct_ret` 的 NaN 守門回 `Missing`，交給既有 coverage／重配權重機制處理。

本檔守的事：
1. 轉市後 `< n` 個交易日內，吃 `index_close` 的指標（`excess_long`／`excess_short`／`excess_accel`／
   `industry_relative_return`）一律 `Missing`；
2. 該檔**自身**的價格指標（`excess_vs_industry`＝只吃 close 與產業中位）不受影響；
3. **非轉市檔逐位不變**（有無轉市檔在場，其 ring 與指標逐位相同）；
4. 轉市 ≥ n 日後恢復有值且量級正常。

**突變守門**：把 `ingest` 裡「轉市清指數欄」那段拿掉 → 1、4 兩組會紅（污染值重現、上萬 pp）。
"""
from __future__ import annotations

import datetime as dt
import sys
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from iching import replay_state as RS  # noqa: E402
from iching.score import score_stock  # noqa: E402
from iching.score.params import SCOPE_STOCK, build_params  # noqa: E402
from iching.score.stock import (excess_return, ind_excess, ind_excess_accel,  # noqa: E402
                                ind_excess_vs_industry, ind_industry_relative)
from iching.score.transform import Missing  # noqa: E402
from iching.universe import PitPool  # noqa: E402

N_DAYS = 40
SWITCH_I = 20                       # 轉市生效日在 DATES 裡的位置
MOVER, STAYER = "1111", "2222"
TPEX_BASE, TWSE_BASE = 200.0, 20000.0        # 兩市場指數量級差 100 倍（真實約 380 vs 45,000）
DATES = [(dt.date(2024, 1, 1) + dt.timedelta(days=i)).isoformat() for i in range(N_DAYS)]
SWITCH_DATE = DATES[SWITCH_I]


def _info(sid: str, type_: str, date: str) -> dict:
    return {"stock_id": sid, "type": type_, "industry_category": "半導體業", "stock_name": "某", "date": date}


def _pool(mover_switches: bool) -> PitPool:
    """`mover_switches=True`：`MOVER` 於 `SWITCH_DATE` 由 tpex 轉 twse（殘留列 date ＋1 曆日＝生效日，`PitPool` Q11）。
    `False`：`MOVER` 全期間都在 tpex（對照組）。`STAYER` 兩種情況都恆為 twse。"""
    rows = [_info(STAYER, "twse", DATES[-1])]
    if mover_switches:
        rows += [_info(MOVER, "tpex", DATES[SWITCH_I - 1]), _info(MOVER, "twse", DATES[-1])]
    else:
        rows += [_info(MOVER, "tpex", DATES[-1])]
    return PitPool.from_snapshot_rows(rows)


def _bundle(i: int) -> RS.DayBundle:
    """兩市場都有指數列；兩檔都有成交。個股收盤走同一條溫和上升的價格序列（與所屬市場無關）。"""
    px = 100.0 + i
    stock = {"open": px, "high": px + 1, "low": px - 1, "close": px, "Trading_Volume": 1_000_000,
             "amount": px * 1_000_000, "foreign_net": 10.0, "trust_net": 5.0,
             "margin_balance": 1000.0, "short_sale_balance": 100.0, "shares_outstanding": 1e9}
    return RS.DayBundle(
        tpe_date=DATES[i],
        index={"twse": {"open": TWSE_BASE + i, "high": TWSE_BASE + i, "low": TWSE_BASE + i, "close": TWSE_BASE + i},
               "tpex": {"open": TPEX_BASE + i * 0.1, "high": TPEX_BASE + i * 0.1, "low": TPEX_BASE + i * 0.1,
                        "close": TPEX_BASE + i * 0.1}},
        stocks={MOVER: dict(stock), STAYER: dict(stock)},
    )


def _run(mover_switches: bool) -> RS.WindowCache:
    wc = RS.WindowCache(_pool(mover_switches), {}, window=N_DAYS + 10)
    for i in range(N_DAYS):
        wc.ingest(_bundle(i))
    return wc


def _run_to(mover_switches: bool, i_end: int) -> RS.WindowCache:
    wc = RS.WindowCache(_pool(mover_switches), {}, window=N_DAYS + 10)
    for i in range(i_end + 1):
        wc.ingest(_bundle(i))
    return wc


@pytest.fixture(scope="module")
def switched() -> RS.WindowCache:
    return _run(True)


# ---------------------------------------------------------------------------
# 0. 前提：轉市真的發生了，且 `_pct_ret` 對 NaN 端點回 Missing（不是回 nan）
# ---------------------------------------------------------------------------
def test_pool_transfer_and_index_column_cleared(switched):
    pool = _pool(True)
    assert pool.listed(MOVER, DATES[SWITCH_I - 1]) == "tpex" and pool.listed(MOVER, SWITCH_DATE) == "twse"
    assert pool.listed(STAYER, SWITCH_DATE) == "twse"
    idx = switched.stock_window(MOVER)[:, RS.IDX_CLOSE_COL]
    assert idx.size == N_DAYS
    assert np.isnan(idx[:SWITCH_I]).all()                    # 轉市前的既有列被清成 NaN
    assert not np.isnan(idx[SWITCH_I:]).any()                # 轉市後逐日寫入新市場指數
    assert idx[SWITCH_I] == pytest.approx(TWSE_BASE + SWITCH_I)
    # 只清指數欄：該檔自己的價、量、籌碼一律不動（與對照組逐位相同）
    a, b = switched.stock_window(MOVER), _run(False).stock_window(MOVER)
    for c in range(len(RS.STOCK_COLS)):
        if c == RS.IDX_CLOSE_COL:
            continue
        assert np.array_equal(a[:, c], b[:, c], equal_nan=True), RS.STOCK_COLS[c]


def test_pct_ret_nan_endpoint_returns_missing_not_nan():
    """`base == 0` 對 NaN 不成立——少了 NaN 守門會吐出 `nan` 這個 float，一路變成 `native=nan` 的 `Ind`，
    缺值機制完全接不到。這是「清 NaN」能生效的前提。"""
    arr = np.array([np.nan, np.nan, 100.0, 101.0, 102.0, 103.0])
    close = np.array([1.0, 2.0, 3.0, 4.0, 5.0, 6.0])
    assert isinstance(excess_return(close, arr, 4), Missing)
    assert not isinstance(excess_return(close, arr, 2), Missing)
    assert isinstance(ind_excess(close, arr, 4, 5.0), Missing)
    assert isinstance(ind_industry_relative(1.0, arr, 4, 5.0), Missing)


# ---------------------------------------------------------------------------
# 1＋4. 跨轉市視窗 Missing、視窗完全落在新市場後恢復且量級正常
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("n", [5, 10, 20])
def test_index_metrics_missing_within_n_days_then_recover(n):
    d = 5.0
    for off in (0, 1, n - 1, n, n + 3):
        i_end = SWITCH_I + off
        if i_end >= N_DAYS:
            continue
        wc = _run_to(True, i_end)
        a = wc.stock_window(MOVER)
        close, idx = a[:, 3], a[:, RS.IDX_CLOSE_COL]
        got_excess = ind_excess(close, idx, n, d)
        got_rel = ind_industry_relative(1.0, idx, n, d)
        if off < n:                                            # 視窗跨越轉市日 → 分母屬舊市場，本來就沒有定義
            assert isinstance(got_excess, Missing) and got_excess.reason == "missing", (n, off)
            assert isinstance(got_rel, Missing) and got_rel.reason == "missing", (n, off)
        else:                                                  # 視窗完全落在新市場
            assert not isinstance(got_excess, Missing) and abs(got_excess.x) < 100, (n, off, got_excess)
            assert not isinstance(got_rel, Missing) and abs(got_rel.x) < 100, (n, off, got_rel)
        # 加速度要 2n 日才湊得齊（最近 n 日超額 − 其前 n 日超額）
        got_acc = ind_excess_accel(close, idx, n, d)
        if off < 2 * n:
            assert isinstance(got_acc, Missing), (n, off)
        else:
            assert not isinstance(got_acc, Missing) and abs(got_acc.x) < 100, (n, off)


def test_no_fake_index_return_survives_anywhere(switched):
    """全期間逐日、逐視窗掃一遍：`excess`／`industry_relative_return` 要嘛 Missing、要嘛量級正常。
    **這就是突變會踩到的那一條**——不清 NaN 時，跨轉市的視窗會吐出上萬 pp。"""
    for i_end in range(N_DAYS):
        wc = _run_to(True, i_end)
        a = wc.stock_window(MOVER)
        close, idx = a[:, 3], a[:, RS.IDX_CLOSE_COL]
        for n in (5, 10, 20, 60):
            for got in (ind_excess(close, idx, n, 5.0), ind_industry_relative(1.0, idx, n, 5.0),
                        ind_excess_accel(close, idx, n, 5.0)):
                if isinstance(got, Missing):
                    continue
                assert got.x is not None and not np.isnan(got.x), (i_end, n)
                assert abs(got.x) < 100, (i_end, n, got.x)


# ---------------------------------------------------------------------------
# 2. 只吃 close 的指標不受影響
# ---------------------------------------------------------------------------
def test_close_only_metric_unaffected_by_transfer(switched):
    """`excess_vs_industry` 不吃指數 → 轉市當日照樣有值，且與對照組（從未轉市）逐位相同。"""
    rules = build_params("twse").rules
    a, b = switched.stock_window(MOVER), _run(False).stock_window(MOVER)
    for n in (5, 10, 20):
        x = ind_excess_vs_industry(a[:, 3], 1.5, 50, n, 5.0, rules)
        y = ind_excess_vs_industry(b[:, 3], 1.5, 50, n, 5.0, rules)
        assert not isinstance(x, Missing) and not isinstance(y, Missing), n
        assert x.x == y.x and x.native == y.native, n


# ---------------------------------------------------------------------------
# 3. 非轉市檔逐位不變
# ---------------------------------------------------------------------------
def test_non_transfer_stock_is_bit_identical(switched):
    """`STAYER` 全期間都在 twse：有無轉市檔在場，其 ring 逐位相同、指數欄零 NaN、指標逐位相同。"""
    a, b = switched.stock_window(STAYER), _run(False).stock_window(STAYER)
    assert np.array_equal(a, b, equal_nan=True)
    assert not np.isnan(a[:, RS.IDX_CLOSE_COL]).any()
    for n in (5, 10, 20):
        x, y = ind_excess(a[:, 3], a[:, RS.IDX_CLOSE_COL], n, 5.0), ind_excess(b[:, 3], b[:, RS.IDX_CLOSE_COL], n, 5.0)
        assert not isinstance(x, Missing) and x.x == y.x and x.native == y.native, n


# ---------------------------------------------------------------------------
# 三爻覆蓋率：缺的那幾族走重配權重，不是靜默補 0
# ---------------------------------------------------------------------------
def test_line3_coverage_reweighted_then_full():
    """`short` 期間（`excess_long` n=10、`excess_short` n=5、`excess_accel` 2×5）逐日的三爻覆蓋率階梯：

    - 轉市後 0～4 日：族 A 兩子指標與族 C 皆缺 → 只剩族 B（權重 0.25）→ `coverage_ratio=0.25 < unknown_below`
      ⇒ **整爻 unknown**（這是誠實的結果：三爻四分之三的權重在跨轉市時本來就沒有定義）；
    - 轉市後 5～9 日：`excess_short` 已可得 → 族 A 有分（族內重配）、族 C 仍缺 → `coverage_ratio=0.75`、`reweighted`；
    - 轉市後 ≥10 日：全部恢復 → `coverage_ratio=1`、不 reweighted。
    """
    ps = build_params("twse")
    wl = ps.get(SCOPE_STOCK, "short", "3", "A", "excess_long").window
    ws = ps.get(SCOPE_STOCK, "short", "3", "A", "excess_short").window
    assert (wl, ws) == (10, 5)
    seen = {}
    for off in range(0, N_DAYS - SWITCH_I):
        wc = _run_to(True, SWITCH_I + off)
        inp = wc.stock_inputs(MOVER, "short", DATES[SWITCH_I + off], RS.CrossDayState())
        inp.industry_median_return = {wl: 1.5, ws: 1.0}
        inp.industry_n = 50
        l3 = score_stock(inp, ps, "short").lines["3"]
        seen[off] = (round(l3.coverage_ratio, 4), l3.reweighted, l3.unknown)
    for off in range(0, ws):
        assert seen[off] == (0.25, True, True), (off, seen[off])
    for off in range(ws, 2 * ws):
        assert seen[off] == (0.75, True, False), (off, seen[off])
    for off in range(2 * ws, N_DAYS - SWITCH_I):
        assert seen[off] == (1.0, False, False), (off, seen[off])
