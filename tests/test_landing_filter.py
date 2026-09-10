"""落地過濾 lf1（使用者 2026-09-10 Hetzner 實測後裁定：不要權證、要 ETF）：免 token、免網路。

案例取自實測表（`config.is_warrant_code` docstring；2020-01-02 全市場切片 22,478 列的組成）每一列的真實代號：
權證 `030001` 型要自己造合法樣本（6 碼、首字數字、非 00、不在 info）。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

from iching import config as C  # noqa: E402
from iching.store import open_stores  # noqa: E402

import backfill_hetzner as B  # noqa: E402

# TaiwanStockInfo 的代號集合（測試用縮影；只放與四條件判定有關的代號）
INFO = frozenset({
    "1101", "2330", "8913x",            # 普通股（8913 刻意不放：模擬已下市、不在 info）
    "0050", "00636", "00631L", "006201", "00987A",   # ETF（4／5／6 碼）
    "01003T",                           # REIT
    "020000",                           # ETN
    "910322",                           # DR（6 碼）
    "9101",                             # DR（4 碼；落地保留，池另濾）
    "Cement", "Rubber",                 # 6 碼產業指數，首字非數字
    "Tourism", "Electric Machinery",    # 產業指數
    "2881A", "TAIEX", "Other",          # 特別股／指數
})
DV = "fm-20260910-01"

KEEP = [
    ("0050", "ETF 4 碼"), ("00636", "ETF 5 碼"), ("00631L", "ETF 6 碼含字母"), ("006201", "ETF 6 碼純數字"),
    ("00987A", "主動式 ETF 6 碼"), ("006204", "已下市 ETF（不在 info）——00 開頭即保留"),
    ("01003T", "REIT（在 info）"), ("020000", "ETN（在 info）"), ("910322", "DR 6 碼（在 info）"),
    ("Cement", "6 碼產業指數首字非數字"), ("Rubber", "同上"), ("Tourism", "產業指數"), ("Electric Machinery", "產業指數含空白"),
    ("2881A", "特別股 5 碼含字母"), ("TAIEX", "加權指數"), ("Other", "5 碼"),
    ("1101", "普通股"), ("2330", "普通股"), ("8913", "已下市 4 碼普通股（不在 info）——存活者偏誤禁區"),
    ("9101", "4 碼 DR：落地保留（池過濾另處理）"),
]
DROP = [
    ("030001", "權證（合法樣本）"), ("03651X", "權證含字母尾"), ("710001", "權證 7 開頭"), ("08001Q", "權證 Q 尾"),
    ("911699", "已下市 DR／ETN／REIT 型（6 碼 91 開頭、不在 info）——**已知殘餘風險**，判定可接受"),
]


@pytest.mark.parametrize("sid,why", KEEP, ids=[k[0] for k in KEEP])
def test_keep(sid, why):
    assert C.is_warrant_code(sid, INFO) is False, why


@pytest.mark.parametrize("sid,why", DROP, ids=[k[0] for k in DROP])
def test_drop(sid, why):
    assert C.is_warrant_code(sid, INFO) is True, why


def test_four_conditions_each_necessary():
    """四條件缺一不可：逐一破壞一個條件，結果都要翻成保留。"""
    assert C.is_warrant_code("030001", INFO)
    assert not C.is_warrant_code("03001", INFO)            # len != 6
    assert not C.is_warrant_code("X30001", INFO)           # 首字非數字
    assert not C.is_warrant_code("003001", INFO)           # 00 開頭
    assert not C.is_warrant_code("030001", INFO | {"030001"})   # 在 info
    assert not C.is_warrant_code("", INFO) and not C.is_warrant_code(None, INFO)


def test_registry_declares_filter_on_exactly_four_daily_slices():
    assert C.LANDING_FILTER_VERSION == "lf1"
    assert {d.key for d in C.DATASETS if d.apply_landing_filter} == {"price_daily", "inst_buysell", "margin", "short_sale_balance"}
    for d in C.DATASETS:
        if d.apply_landing_filter:
            assert d.strategy == "daily_slice" and "stock_info" in d.depends


def test_apply_landing_filter_counts():
    rows = [{"stock_id": s} for s, _ in KEEP] + [{"stock_id": s} for s, _ in DROP] + [{"stock_id": None}]
    kept, n = B.apply_landing_filter(rows, INFO)
    assert n == len(DROP) and len(kept) == len(KEEP) + 1
    assert {r["stock_id"] for r in kept if r["stock_id"]} == {s for s, _ in KEEP}


# ---------------------------------------------------------------------------
# run_dataset 整合：中止、n_rows、sources、快取
# ---------------------------------------------------------------------------
class _FakeFM:
    def __init__(self, table):
        self.table = table
        self.calls = 0

    def get(self, dataset, **p):
        self.calls += 1
        return list(self.table.get((dataset, p.get("start_date")), []))


def _args(*argv):
    return B.build_parser().parse_args(["run", "--no-fallback", *argv])


def _stores_with_calendar(tmp_path):
    stores = open_stores(tmp_path, C.DB_FILES)
    stores["prices"].record_success("index_price", "raw_index_price", "TAIEX:2022-01-01~2022-12-31",
                                    [{"date": f"2022-01-{d:02d}", "stock_id": "TAIEX"} for d in (3, 4, 5, 6)], DV, "TaiwanStockPrice")
    return stores


def _land_info(stores, ids=("1101", "2330", "0050", "910322", "01003T", "020000", "Cement")):
    stores["universe"].record_success("stock_info", "raw_stock_info", "all",
                                      [{"stock_id": s, "type": "twse"} for s in ids], DV, "TaiwanStockInfo", ("stock_id",))


DAY = [{"date": "2022-01-03", "stock_id": s} for s in
       ("2330", "1101", "8913", "0050", "00631L", "00987A", "006204", "01003T", "020000", "910322", "Cement", "TAIEX", "2881A",
        "030001", "03651X", "710001", "08001Q", "911699")]   # 13 保留＋5 濾掉


def test_run_aborts_without_stock_info_and_fires_no_request(tmp_path):
    B._LANDING_INFO_IDS.clear()
    stores = _stores_with_calendar(tmp_path)
    fm = _FakeFM({("TaiwanStockPrice", "2022-01-03"): DAY})
    spec = C.DATASET_BY_KEY["price_daily"]
    st = B.run_dataset(spec, "daily_slice", stores, fm, None, DV, _args("--dataset", "price_daily", "--from", "2022-01-01", "--to", "2022-01-10"))
    assert st["aborted"] and "raw_stock_info" in st["aborted"] and "run --dataset stock_info" in st["aborted"]
    assert fm.calls == 0 and st["planned"] == 0
    assert not stores["prices"].is_covered("price_daily", "2022-01-03", DV)
    # 同一 run 內稍後落地 stock_info → 不得沿用「空」的快取，要讀得到
    _land_info(stores)
    st2 = B.run_dataset(spec, "daily_slice", stores, fm, None, DV, _args("--dataset", "price_daily", "--from", "2022-01-01", "--to", "2022-01-10"))
    assert st2["aborted"] is None and st2["ok"] == 1 and st2["failed"] == 3   # 01-04~06 日曆上但假 FM 回空 → empty_on_trading_day
    for s_ in stores.values():
        s_.close()


def test_filtered_rows_and_bookkeeping(tmp_path):
    B._LANDING_INFO_IDS.clear()
    stores = _stores_with_calendar(tmp_path)
    _land_info(stores)
    fm = _FakeFM({("TaiwanStockPrice", "2022-01-03"): DAY, ("TaiwanStockPrice", "2022-01-04"): DAY})
    spec = C.DATASET_BY_KEY["price_daily"]
    st = B.run_dataset(spec, "daily_slice", stores, fm, None, DV, _args("--dataset", "price_daily", "--from", "2022-01-01", "--to", "2022-01-04"))
    assert st["ok"] == 2 and st["filtered"] == 10
    p = stores["prices"]
    # coverage.n_rows＝實際插入數（濾後 13），且等於 raw 表該鍵實列數
    for k in ("2022-01-03", "2022-01-04"):
        n_cov = p.conn.execute("SELECT n_rows FROM coverage WHERE dataset='price_daily' AND key=?", (k,)).fetchone()[0]
        n_raw = p.rows_for_key("raw_price_daily", k)
        assert n_cov == n_raw == 13, (k, n_cov, n_raw)
    ids = {r[0] for r in p.conn.execute("SELECT stock_id FROM raw_price_daily")}
    assert {"030001", "03651X", "710001", "08001Q", "911699"}.isdisjoint(ids)
    assert {"8913", "006204", "00987A", "910322", "Cement", "TAIEX", "2881A"} <= ids
    # sources 記本次落地套用的版本與濾掉列數（同 dv 累計）
    src = p.source_row("price_daily")
    assert src["landing_filter"] == "lf1" and src["n_filtered"] == 10 and src["n_rows"] == 26
    # 未宣告過濾的資料集 landing_filter 保持 NULL、n_filtered 0
    isrc = p.source_row("index_price")
    assert isrc["landing_filter"] is None and isrc["n_filtered"] == 0
    # data_version 語意不動：coverage／raw 列仍是 FinMind 批次號
    assert {r[0] for r in p.conn.execute("SELECT DISTINCT data_version FROM raw_price_daily")} == {DV}
    for s_ in stores.values():
        s_.close()


def test_info_ids_read_once_per_run_and_cleared_by_cmd_run(tmp_path, monkeypatch):
    B._LANDING_INFO_IDS.clear()
    stores = _stores_with_calendar(tmp_path)
    _land_info(stores)
    calls = {"n": 0}
    real = B.info_ids_from_store

    def counted(u):
        calls["n"] += 1
        return real(u)
    monkeypatch.setattr(B, "info_ids_from_store", counted)
    fm = _FakeFM({("TaiwanStockPrice", "2022-01-03"): DAY, ("TaiwanStockInstitutionalInvestorsBuySell", "2022-01-03"): DAY})
    a = _args("--dataset", "price_daily", "--from", "2022-01-01", "--to", "2022-01-03")
    B.run_dataset(C.DATASET_BY_KEY["price_daily"], "daily_slice", stores, fm, None, DV, a)
    B.run_dataset(C.DATASET_BY_KEY["inst_buysell"], "daily_slice", stores, fm, None, DV, a)
    assert calls["n"] == 1                       # 兩個資料集共用同一次讀取
    assert stores["chips"].source_row("inst_buysell")["n_filtered"] == 5
    for s_ in stores.values():
        s_.close()


def test_report_prints_landing_filter_line(tmp_path, capsys):
    B._LANDING_INFO_IDS.clear()
    stores = _stores_with_calendar(tmp_path)
    _land_info(stores)
    fm = _FakeFM({("TaiwanStockPrice", "2022-01-03"): DAY})
    B.run_dataset(C.DATASET_BY_KEY["price_daily"], "daily_slice", stores, fm, None, DV,
                  _args("--dataset", "price_daily", "--from", "2022-01-01", "--to", "2022-01-03"))
    line = B.landing_filter_report_line(stores)
    assert line.startswith("落地過濾 lf1：已濾 5 列（權證）") and "price_daily 5" in line and "⚠" not in line
    # 有人在沒套過濾的情況下落地（landing_filter NULL）→ report 要警示
    stores["chips"].record_success("margin", "raw_margin", "2022-01-03", [{"date": "2022-01-03", "stock_id": "030001"}], DV, "X")
    line2 = B.landing_filter_report_line(stores)
    assert "⚠" in line2 and "margin" in line2
    for s_ in stores.values():
        s_.close()
    rc = B.main(["--cache-dir", str(tmp_path), "report"])
    out = capsys.readouterr().out
    assert rc == 0 and "落地過濾 lf1：已濾 5 列（權證）" in out


def test_filter_not_applied_to_non_declared_datasets(tmp_path):
    """index_price（per_id）不宣告過濾：即使沒有 raw_stock_info 也不中止、列照落。"""
    B._LANDING_INFO_IDS.clear()
    stores = open_stores(tmp_path, C.DB_FILES)
    fm = _FakeFM({("TaiwanStockPrice", "2023-01-01"): [{"date": "2023-01-03", "stock_id": "TAIEX"}]})
    st = B.run_dataset(C.DATASET_BY_KEY["index_price"], "per_id", stores, fm, None, DV,
                       _args("--dataset", "index_price", "--from", "2023-01-01", "--to", "2023-01-31"))
    assert st["aborted"] is None and st["filtered"] == 0 and st["ok"] == 2
    for s_ in stores.values():
        s_.close()
