"""策略 × 回應 → coverage／failures 的表驅動測試（2026-09-09 窮舉式驗收的 A＋D 表修正版）。

目的：同型缺口（「失敗被記成已涵蓋」）在本專案已復發四次；把期望寫成一張表、逐格用假 client 跑真正的
`run_dataset()`（FinMind 路徑走**真** `FinMind` client＋假 session，官方路徑走假 OfficialClient），任何一格變動都會紅。

期望表（C:<status>＝寫 coverage；F:<kind>＝進 failures、不寫 coverage）。**刻意**的格用 `!` 標：

| 策略／資料集              | http500 | nonjson | empty200                | stat_not_ok             | exception | ok    | ok_empty_data           | permission   | ok_all_filtered |
|---------------------------|---------|---------|-------------------------|-------------------------|-----------|-------|-------------------------|--------------|-----------------|
| daily_slice／price_daily  | F:error | F:error | F:empty_on_trading_day !| F:error                 | F:error   | C:ok  | —                       | F:permission | C:empty !       |
| range_slice／dividend     | F:error | F:error | F:empty_unexpected    ! | F:error                 | F:error   | C:ok  | —                       | F:permission | —               |
| per_id／index_price       | F:error | F:error | F:empty_unexpected    ! | F:error                 | F:error   | C:ok  | —                       | F:permission | —               |
| per_stock／price_adj      | F:error | F:error | C:empty               ! | F:error                 | F:error   | C:ok  | —                       | F:permission | —               |
| single／stock_info        | F:error | F:error | F:empty_unexpected    ! | F:error                 | F:error   | C:ok  | —                       | F:permission | —               |
| official／twse_bfi82u     | F:error | F:error | F:empty_on_trading_day !| F:empty_on_trading_day !| F:error   | C:ok  | F:empty_on_trading_day !| —            | —               |
| official_month／fmtqik    | F:error | F:error | F:bad_stat            ! | F:bad_stat            ! | F:error   | C:ok  | F:bad_stat            ! | —            | —               |

- `empty200`：FinMind＝HTTP 200＋`{"status":200,"data":[]}`；官方＝HTTP 200＋`{}`。
- `stat_not_ok`：FinMind＝HTTP 200＋`{"status":400,"msg":"date error"}`（非權限類；P0-A §4.5 的「HTTP 200 但 body 400」）；
  官方＝`{"stat":"很抱歉, 沒有符合條件的資料!"}`。
- `ok_empty_data`：官方 `{"stat":"OK","data":[]}`（stat OK 但沒資料）——只對官方有意義。
- `permission`：FinMind HTTP 400＋msg 含 level；官方端點無此概念。跑時帶 `--no-fallback` 以看原始分類。
- `ok_all_filtered`（2026-09-10 落地過濾後新增）：FinMind HTTP 200＋非空 `data`，但每一列都是權證（`030001` 型）→ 落地過濾
  lf2 濾到 0 列 → **`coverage=empty`**（上游有回資料，不是 `empty_on_trading_day`；只 log WARNING）。
  **daily_slice 的 `empty` 現在只剩這一種意思**：上游回空一律走 `empty_on_trading_day`（failures），永遠不會寫 `empty`。
  只對宣告 `apply_landing_filter` 的策略（daily_slice）有意義。
- 只有 `per_stock` 的空是合法 empty：由 `config.DatasetSpec.empty_ok_for` 宣告（tuple，因 fallback 會換策略跑）。
- `daily_slice`／`official` 的鍵一律是**同 data_version 交易日曆**上的日期，故其空／無資料＝`empty_on_trading_day`。
- `official_month` 月查不會真的沒資料，空或 stat 非 OK 一律 `bad_stat`。
"""
from __future__ import annotations

import inspect
import json
import logging
import re
import sys
from pathlib import Path

import pytest
import requests

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

import backfill_hetzner as B  # noqa: E402
from iching import config as C  # noqa: E402
from iching import plan as P  # noqa: E402
from iching.fm import FinMind  # noqa: E402
from iching.store import open_stores  # noqa: E402

DV = "fm-20260909-01"
FAKE_TOKEN = "FAKE-TOKEN-7f3a"   # 可辨識的假 token：每格斷言它不出現在 failures 任何欄位與 log 全文

# (strategy, dataset key, --from, --to)
CASES = [
    ("daily_slice", "price_daily", "2022-01-01", "2022-01-10"),
    ("range_slice", "dividend_result", "2022-01-01", "2022-01-10"),
    ("per_id", "index_price", "2023-01-01", "2023-01-10"),      # 2023：避開日曆年 2022 的鍵
    ("per_stock", "price_adj", None, None),
    ("single", "stock_info", None, None),
    ("official", "twse_bfi82u", "2022-01-01", "2022-01-10"),
    ("official_month", "twse_fmtqik", "2022-02-01", "2022-02-28"),
]
FINMIND = {"daily_slice", "range_slice", "per_id", "per_stock", "single"}

EXPECT = {
    #  strategy        http500    nonjson    empty200                    stat_not_ok                 exception  ok      ok_empty_data               permission      ok_all_filtered
    "daily_slice":    ("F:error", "F:error", "F:empty_on_trading_day",   "F:error",                  "F:error", "C:ok", None,                       "F:permission", "C:empty"),
    "range_slice":    ("F:error", "F:error", "F:empty_unexpected",       "F:error",                  "F:error", "C:ok", None,                       "F:permission", None),
    "per_id":         ("F:error", "F:error", "F:empty_unexpected",       "F:error",                  "F:error", "C:ok", None,                       "F:permission", None),
    "per_stock":      ("F:error", "F:error", "C:empty",                  "F:error",                  "F:error", "C:ok", None,                       "F:permission", None),
    "single":         ("F:error", "F:error", "F:empty_unexpected",       "F:error",                  "F:error", "C:ok", None,                       "F:permission", None),
    "official":       ("F:error", "F:error", "F:empty_on_trading_day",   "F:empty_on_trading_day",   "F:error", "C:ok", "F:empty_on_trading_day",   None,           None),
    "official_month": ("F:error", "F:error", "F:bad_stat",               "F:bad_stat",               "F:error", "C:ok", "F:bad_stat",               None,           None),
}
COLS = ("http500", "nonjson", "empty200", "stat_not_ok", "exception", "ok", "ok_empty_data", "permission", "ok_all_filtered")


# ---- 假回應 -----------------------------------------------------------------
class _Resp:
    def __init__(self, code, body, text=""):
        self.status_code, self._body = code, body
        self.text = text or (json.dumps(body) if body is not None else "")

    def json(self):
        if self._body is None:
            raise ValueError("no json")
        return self._body


class _Session:
    """每次 get 回同一種回應（transient 重試會打多次）。"""
    def __init__(self, kind):
        self.kind = kind

    def get(self, url, params=None, headers=None, timeout=None):
        k = self.kind
        if k == "exception":
            raise requests.ConnectionError(f"boom url=/api/v4/data?dataset=X&token={FAKE_TOKEN}&start_date=2022")
        if k == "http500":
            return _Resp(500, {"status": 500, "msg": "server"})
        if k == "nonjson":
            return _Resp(200, None, "<html>blocked</html>")
        if k == "empty200":
            return _Resp(200, {"status": 200, "msg": "success", "data": []})
        if k == "stat_not_ok":
            return _Resp(200, {"status": 400, "msg": "date error"})
        if k == "permission":
            return _Resp(400, {"status": 400, "msg": "Your level is free. Please update your user level."})
        if k == "ok":
            return _Resp(200, {"status": 200, "msg": "success", "data": [{"date": "2022-01-03", "stock_id": "2330", "v": 1}]})
        if k == "ok_all_filtered":
            # 非空但全是權證（6 碼、首字數字、非 00、不在 info；030001 型）→ 落地過濾後 0 列
            return _Resp(200, {"status": 200, "msg": "success", "data": [{"date": "2022-01-03", "stock_id": "030001", "v": 1},
                                                                          {"date": "2022-01-03", "stock_id": "03651X", "v": 1}]})
        raise AssertionError(k)


class _OC:
    def __init__(self, kind):
        self.kind = kind

    def get(self, url, params):
        k = self.kind
        if k == "exception":
            # 官方端點本來不帶 token，但例外訊息仍走 redact；故意塞 ?token= 讓官方路徑的 redact 也被考驗
            raise requests.ConnectionError(f"boom url={url}?token={FAKE_TOKEN}")
        if k == "http500":
            return 500, {"stat": "OK", "data": [[1]]}, "{}"
        if k == "nonjson":
            return 200, None, "<html>blocked</html>"
        if k == "empty200":
            return 200, {}, "{}"
        if k == "stat_not_ok":
            return 200, {"stat": "很抱歉, 沒有符合條件的資料!"}, "{}"
        if k == "ok":
            return 200, {"stat": "OK", "fields": ["x"], "data": [["1"]]}, "{}"
        if k == "ok_empty_data":
            return 200, {"stat": "OK", "data": []}, "{}"
        raise AssertionError(k)


def _stores(tmp_path):
    stores = open_stores(tmp_path, C.DB_FILES)
    # 同 dv 的台北交易日曆（2022-01-03~01-06；01-01~01-10 平日 6 → 門檻 3）＋ 個股池
    stores["prices"].record_success("index_price", "raw_index_price", "TAIEX:2022-01-01~2022-12-31",
                                    [{"date": f"2022-01-{d:02d}", "stock_id": "TAIEX"} for d in (3, 4, 5, 6)], DV, "TaiwanStockPrice")
    # 個股池以**舊 dv** 落地：raw 列供 per_stock 取池用，但鍵 "all" 對本 DV 未 covered，single 列才會真的去抓
    stores["universe"].record_success("stock_info", "raw_stock_info", "all", [{"stock_id": "2330", "type": "twse"}], "fm-20260101-01", "TaiwanStockInfo", ("stock_id",))
    return stores


def _args(key, start, end):
    argv = ["run", "--dataset", key, "--no-fallback"]
    if start:
        argv += ["--from", start, "--to", end]
    return B.build_parser().parse_args(argv)


@pytest.mark.parametrize("col", COLS)
@pytest.mark.parametrize("strategy,key,start,end", CASES, ids=[c[0] for c in CASES])
def test_matrix(tmp_path, caplog, strategy, key, start, end, col):
    expect = EXPECT[strategy][COLS.index(col)]
    if expect is None:
        pytest.skip("該格不適用")
    spec = C.DATASET_BY_KEY[key]
    stores = _stores(tmp_path)
    fm = oc = None
    if strategy in FINMIND:
        fm = FinMind(FAKE_TOKEN, session=_Session(col), sleep=lambda s: None, clock=lambda: 0.0, min_interval=0)
    else:
        oc = _OC(col)
    caplog.set_level(logging.DEBUG)
    st = B.run_dataset(spec, strategy, stores, fm, oc, DV, _args(key, start, end))
    # token 不得洩漏：failures 任何欄位（含 message）與 log 全文（CANON 第 1 條）
    fail_rows = store_dump = json.dumps(stores[spec.db].conn.execute("SELECT * FROM failures").fetchall(), ensure_ascii=False)
    assert FAKE_TOKEN not in fail_rows, (strategy, col)
    assert FAKE_TOKEN not in caplog.text, (strategy, col)
    assert FAKE_TOKEN not in json.dumps(st, ensure_ascii=False)
    store = stores[spec.db]
    keys, _ = P.keys_for(spec, strategy, tpe_dates=["2022-01-03", "2022-01-04", "2022-01-05", "2022-01-06"],
                         stock_ids=["2330"], start=start, end=end)
    assert keys, "測試案例沒有鍵"
    k0 = keys[0]
    kind, val = expect.split(":")
    if kind == "C":
        assert store.is_covered(spec.key, k0, DV), (strategy, col)
        status = store.conn.execute("SELECT status FROM coverage WHERE dataset=? AND key=?", (spec.key, k0)).fetchone()[0]
        assert status == val and st["failed"] == 0, (strategy, col, status, st)
        assert store.failures_list(spec.key) == []
        if col == "ok_all_filtered":
            n_rows = store.conn.execute("SELECT n_rows FROM coverage WHERE dataset=? AND key=?", (spec.key, k0)).fetchone()[0]
            # 本案例每個鍵（4 個交易日）都回同一份 2 列全權證回應：每鍵濾 2、記 empty
            assert n_rows == 0 and store.rows_for_key(spec.table, k0) == 0
            assert st["empty"] == st["planned"] and st["filtered"] == 2 * st["planned"]
            assert store.source_row(spec.key)["n_filtered"] == 2 * st["planned"]
    else:
        assert not store.is_covered(spec.key, k0, DV), (strategy, col)
        rows_f = store.failures_list(spec.key, limit=500)
        fails = {r[1]: r[2] for r in rows_f}          # key → kind
        msgs = {r[1]: r[3] for r in rows_f}           # key → message
        assert fails.get(k0) == val, (strategy, col, fails, st)
        # 失敗不落地：該鍵下不得有**本 dv** 的列（single 列的 raw_stock_info 有舊 dv 的池列，失敗不會抹掉它——那是刻意的）
        n_dv = store.conn.execute(f'SELECT COUNT(*) FROM "{spec.table}" WHERE cov_key=? AND data_version=?', (k0, DV)).fetchone()[0] \
            if store.table_exists(spec.table) else 0
        assert n_dv == 0, (strategy, col)
        if col == "exception":
            # 例外路徑的訊息確實經過 redact（不是因為訊息被截掉才沒出現）
            assert "token=<redacted>" in msgs[k0], (strategy, col, msgs[k0])
    for s_ in stores.values():
        s_.close()


def test_matrix_rows_cover_every_strategy():
    """新策略漏格必紅：登錄表用到的策略、config._check_registry 允許的策略、矩陣列集合三者相等。"""
    rows = set(EXPECT)
    assert {d.strategy for d in C.DATASETS} == rows
    src = inspect.getsource(C._check_registry)
    m = re.search(r'assert d\.strategy in \(([^)]*)\)', src)
    assert m, "找不到 _check_registry 的策略白名單斷言"
    allowed = set(re.findall(r'"([a-z_]+)"', m.group(1)))
    assert allowed == rows, (allowed ^ rows)
    assert {c[0] for c in CASES} == rows


def test_expect_table_matches_docstring():
    """docstring 那張人讀的表與 EXPECT 對得上（列數／欄數／每格），避免兩邊各改一份。"""
    doc = __doc__
    rows = [ln for ln in doc.splitlines() if ln.startswith("| ") and "／" in ln and not ln.startswith("| 策略")]
    assert len(rows) == len(EXPECT)
    for ln in rows:
        cells = [c.strip() for c in ln.strip("|").split("|")]
        strat = cells[0].split("／")[0].strip()
        got = tuple(None if c == "—" else c.rstrip(" !").strip() for c in cells[1:])
        assert got == EXPECT[strat], (strat, got, EXPECT[strat])


def test_empty_ok_declared_only_for_per_stock():
    for d in C.DATASETS:
        assert set(d.empty_ok_for) <= {"per_stock"}
        if d.strategy == "per_stock" or d.fallback == "per_stock":
            assert d.empty_ok_for == ("per_stock",), d.key
        else:
            assert d.empty_ok_for == (), d.key
