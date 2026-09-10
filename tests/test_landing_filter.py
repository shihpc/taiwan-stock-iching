"""落地過濾 lf2（使用者 2026-09-10 Hetzner 實測後裁定：不要權證、要 ETF；同日驗收更正 lf1→lf2）：免 token、免網路。

案例取自實測表（`config.is_warrant_code` docstring；2020-01-02 全市場切片 22,478 列的組成）每一列的真實代號：
權證 `030001` 型要自己造合法樣本（6 碼、首字數字、非 00、不在 info）。lf2 另加：TaiwanStockInfo 內
`industry_category='所有證券'` 的 36 檔上櫃權證（`711135`／`710534`／`73107P` 型）由 `info_ids_from_store` 扣除。
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
    """四條件逐一破壞都要翻成保留（條件 2 的真正作用＝「6 碼字母開頭且不在 info」的保險帶，非為 Cement／Rubber 而設）。"""
    assert C.is_warrant_code("030001", INFO)
    assert not C.is_warrant_code("03001", INFO)            # len != 6
    assert not C.is_warrant_code("X30001", INFO)           # 首字非數字（不在 info 也保留）
    assert not C.is_warrant_code("003001", INFO)           # 00 開頭
    assert not C.is_warrant_code("030001", INFO | {"030001"})   # 在 info
    assert not C.is_warrant_code("", INFO) and not C.is_warrant_code(None, INFO)


def test_first_char_digit_is_ascii_only():
    """全形 ０ 與其他 Unicode 數字 `.isdigit()` 為 True，但不是權證形狀——ASCII 判定才不會誤殺。"""
    assert "０".isdigit() and "٣".isdigit()
    assert not C.is_warrant_code("０30001", INFO)
    assert not C.is_warrant_code("٣30001", INFO)
    assert C.is_warrant_code("030001", INFO)


def test_registry_declares_filter_on_exactly_four_daily_slices():
    assert C.LANDING_FILTER_VERSION == "lf2"
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
# lf2：TaiwanStockInfo 內的 36 檔上櫃權證（industry_category='所有證券'）
# ---------------------------------------------------------------------------
# 2026-09-10 免 token 實查 TaiwanStockInfo（4,321 列）：6 碼數字開頭非 00 且在 info 共 118 檔；`所有證券` 36 檔全為權證
# （名稱含「購」／「售」、tpex、前兩碼 70/71/73、date 2020-11-15）。各類別取真實代號一檔：
INFO_ROWS_LF2 = [
    {"stock_id": "711135", "stock_name": "元太群益9B購01", "type": "tpex", "industry_category": "所有證券", "date": "2020-11-15"},
    {"stock_id": "710534", "stock_name": "鈺太元大9B購01", "type": "tpex", "industry_category": "所有證券", "date": "2020-11-15"},
    {"stock_id": "73107P", "stock_name": "原相國票9B售02", "type": "tpex", "industry_category": "所有證券", "date": "2020-11-15"},
    {"stock_id": "020000", "type": "twse", "industry_category": "ETN"},
    {"stock_id": "020001", "type": "twse", "industry_category": "指數投資證券(ETN)"},
    {"stock_id": "910322", "type": "twse", "industry_category": "存託憑證"},
    {"stock_id": "01003T", "type": "twse", "industry_category": "受益證券"},
    {"stock_id": "2887Z1", "type": "twse", "industry_category": "金融保險"},
    {"stock_id": "2330", "type": "twse", "industry_category": "半導體業"},
    {"stock_id": "Cement", "type": "twse", "industry_category": "水泥工業"},
]
WARRANTS_IN_INFO = ("711135", "710534", "73107P")


def _universe_with(tmp_path, rows):
    stores = open_stores(tmp_path, C.DB_FILES)
    stores["universe"].record_success("stock_info", "raw_stock_info", "all", rows, DV, "TaiwanStockInfo", ("stock_id",))
    return stores


def test_lf2_info_ids_exclude_all_securities_category(tmp_path):
    stores = _universe_with(tmp_path, INFO_ROWS_LF2)
    ids = B.info_ids_from_store(stores["universe"])
    assert set(WARRANTS_IN_INFO).isdisjoint(ids)
    assert {"020000", "020001", "910322", "01003T", "2887Z1", "2330", "Cement"} <= ids
    for w in WARRANTS_IN_INFO:
        assert C.is_warrant_code(w, ids), w                      # 在 info 卻是權證 → 排除
    for keep in ("020000", "020001", "910322", "01003T", "2887Z1", "Cement"):
        assert not C.is_warrant_code(keep, ids), keep            # ETN／存託憑證／受益證券／指數投資證券(ETN)／金融保險／指數 → 保留
    for s_ in stores.values():
        s_.close()


def test_missing_industry_category_column_aborts_not_degrades(tmp_path):
    """raw_stock_info 有列卻沒有 industry_category 欄 → lf2 規則無法執行 → **中止**、零請求、不落地、不標假 lf2
    （2026-09-10 驗收必修 2：原本靜默退化成 lf1 卻把 sources.landing_filter 標成 lf2，36 檔權證全落地無警告）。"""
    rows_no_cat = [{"stock_id": r["stock_id"], "type": r["type"]} for r in INFO_ROWS_LF2]
    B._LANDING_INFO_IDS.clear()
    stores = _stores_with_calendar(tmp_path)
    stores["universe"].record_success("stock_info", "raw_stock_info", "all", rows_no_cat, DV, "TaiwanStockInfo", ("stock_id",))
    with pytest.raises(B.LandingInfoError):
        B.info_ids_from_store(stores["universe"])
    fm = _FakeFM({("TaiwanStockPrice", "2022-01-03"): DAY})
    st = B.run_dataset(C.DATASET_BY_KEY["price_daily"], "daily_slice", stores, fm, None, DV,
                       _args("--dataset", "price_daily", "--from", "2022-01-01", "--to", "2022-01-03"))
    assert st["aborted"] and "industry_category" in st["aborted"] and "run --dataset stock_info --force" in st["aborted"]
    assert fm.calls == 0 and st["planned"] == 0
    assert not stores["prices"].is_covered("price_daily", "2022-01-03", DV) and stores["prices"].source_row("price_daily") is None
    for s_ in stores.values():
        s_.close()


def test_category_present_but_no_warrants_is_legit(tmp_path):
    """欄位在、只是沒有任何 `所有證券` 列（例：FinMind 某天把權證全拿掉）→ 合法，等同 lf1 行為：在 info 即保留。"""
    rows_other_cat = [dict(r, industry_category="其他") if r["industry_category"] == "所有證券" else r for r in INFO_ROWS_LF2]
    stores = _universe_with(tmp_path, rows_other_cat)
    ids = B.info_ids_from_store(stores["universe"])
    assert set(WARRANTS_IN_INFO) <= ids and not C.is_warrant_code("711135", ids)
    for s_ in stores.values():
        s_.close()


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
                                      [{"stock_id": s, "type": "twse", "industry_category": "x"} for s in ids], DV, "TaiwanStockInfo", ("stock_id",))


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
    assert src["landing_filter"] == "lf2" and src["n_filtered"] == 10 and src["n_rows"] == 26
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
    assert line.startswith("落地過濾 lf2：已濾 5 列（權證；同 data_version 內**累計**") and "price_daily 5" in line and "⚠" not in line
    # 有人在沒套過濾的情況下落地（landing_filter NULL）→ report 要警示
    stores["chips"].record_success("margin", "raw_margin", "2022-01-03", [{"date": "2022-01-03", "stock_id": "030001"}], DV, "X")
    line2 = B.landing_filter_report_line(stores)
    assert "⚠" in line2 and "margin" in line2
    for s_ in stores.values():
        s_.close()
    rc = B.main(["--cache-dir", str(tmp_path), "report"])
    out = capsys.readouterr().out
    assert rc == 0 and "落地過濾 lf2：已濾 5 列（權證" in out


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


# ---------------------------------------------------------------------------
# 必修 2（2026-09-10 驗收 B6）：舊未濾／舊版本落地的 DB 不得混存 → run_dataset 守門
# ---------------------------------------------------------------------------
def _old_landed_db(tmp_path, landing_filter):
    """模擬 Hetzner 上已有的一份落地：price_daily 2022-01-03 有 ok 鍵，sources.landing_filter＝給定值（None＝未濾）。"""
    stores = _stores_with_calendar(tmp_path)
    _land_info(stores)
    sha = C.info_ids_sha(B.info_ids_from_store(stores["universe"]))
    stores["prices"].record_success("price_daily", "raw_price_daily", "2022-01-03",
                                    [{"date": "2022-01-03", "stock_id": s} for s in ("2330", "030001", "711135")],
                                    DV, "TaiwanStockPrice", landing_filter=landing_filter, n_filtered=0, info_ids_sha=sha)
    return stores


@pytest.mark.parametrize("old_lf", [None, "lf1"], ids=["unfiltered", "lf1"])
def test_run_aborts_on_db_landed_with_other_filter_version(tmp_path, old_lf):
    B._LANDING_INFO_IDS.clear()
    stores = _old_landed_db(tmp_path, old_lf)
    assert B.landing_filter_conflict(stores["prices"], C.DATASET_BY_KEY["price_daily"])
    fm = _FakeFM({("TaiwanStockPrice", "2022-01-04"): DAY})
    st = B.run_dataset(C.DATASET_BY_KEY["price_daily"], "daily_slice", stores, fm, None, DV,
                       _args("--dataset", "price_daily", "--from", "2022-01-01", "--to", "2022-01-10"))
    assert st["aborted"] and f"rm -f {tmp_path}/*.db {tmp_path}/*.db-wal {tmp_path}/*.db-shm" in st["aborted"] and repr(old_lf) in st["aborted"]
    assert fm.calls == 0 and st["planned"] == 0
    # 舊列原封不動（不擅自刪）
    assert stores["prices"].rows_for_key("raw_price_daily", "2022-01-03") == 3
    # 換 data_version 也救不了（raw 表不因 dv 而清空）——仍中止
    st2 = B.run_dataset(C.DATASET_BY_KEY["price_daily"], "daily_slice", stores, fm, None, "fm-20260911-01",
                        _args("--dataset", "price_daily", "--from", "2022-01-01", "--to", "2022-01-10"))
    assert st2["aborted"] and f"rm -f {tmp_path}/" in st2["aborted"]
    for s_ in stores.values():
        s_.close()
    # 清空後可跑
    import shutil
    shutil.rmtree(tmp_path)
    B._LANDING_INFO_IDS.clear()
    stores = _stores_with_calendar(tmp_path)
    _land_info(stores)
    fm = _FakeFM({("TaiwanStockPrice", "2022-01-03"): DAY})
    st3 = B.run_dataset(C.DATASET_BY_KEY["price_daily"], "daily_slice", stores, fm, None, DV,
                        _args("--dataset", "price_daily", "--from", "2022-01-01", "--to", "2022-01-03"))
    assert st3["aborted"] is None and st3["ok"] == 1 and fm.calls == 1
    for s_ in stores.values():
        s_.close()


def test_same_version_rerun_not_blocked_and_other_datasets_untouched(tmp_path):
    B._LANDING_INFO_IDS.clear()
    stores = _old_landed_db(tmp_path, C.LANDING_FILTER_VERSION)
    assert B.landing_filter_conflict(stores["prices"], C.DATASET_BY_KEY["price_daily"]) is None
    fm = _FakeFM({("TaiwanStockPrice", "2022-01-03"): DAY, ("TaiwanStockPrice", "2022-01-04"): DAY})
    st = B.run_dataset(C.DATASET_BY_KEY["price_daily"], "daily_slice", stores, fm, None, DV,
                       _args("--dataset", "price_daily", "--from", "2022-01-01", "--to", "2022-01-04"))
    assert st["aborted"] is None and st["skipped"] == 1 and st["ok"] == 1
    # 另一個資料集（chips.db 的 inst_buysell）沒有舊列 → 不受 price_daily 的狀態影響
    assert B.landing_filter_conflict(stores["chips"], C.DATASET_BY_KEY["inst_buysell"]) is None
    # 只有 empty 鍵、沒有 ok 鍵 → 不算混存（沒有原始列）
    stores["chips"].record_success("margin", "raw_margin", "2022-01-03", [], DV, "X")
    assert B.landing_filter_conflict(stores["chips"], C.DATASET_BY_KEY["margin"]) is None
    for s_ in stores.values():
        s_.close()


# ---------------------------------------------------------------------------
# 建議 1：info_ids 規模下限
# ---------------------------------------------------------------------------
def test_production_guard_constants():
    """守門的守門（2026-09-10 驗收必修 1）：生產值在 conftest 打補丁**之前**存下，改壞 config.py 必紅。"""
    from conftest import ORIG_LANDING_INFO_MIN_IDS, ORIG_PRICE_DAILY_MIN_ROWS
    assert ORIG_LANDING_INFO_MIN_IDS == 3000          # 今日 3,112、餘裕 112（config 註解）
    assert ORIG_PRICE_DAILY_MIN_ROWS == 1500          # 2020-01-02 濾後 2,270 的約 66%
    # 矩陣「price_daily 的 daily_slice 永不寫 coverage=empty」的隱含前提：門檻 ≥1 時
    # 濾後 0 列一定先撞 too_few_rows。設 0 會讓 empty 這條路重新出現（驗收實測反例）。
    assert ORIG_PRICE_DAILY_MIN_ROWS >= 1
    assert C.LANDING_FILTER_VERSION == "lf2"
    assert C.WARRANT_INFO_CATEGORY == "所有證券"
    assert C.info_ids_sha({"b", "a"}) == C.info_ids_sha(["a", "b"]) and len(C.info_ids_sha({"a"})) == 12
    assert C.info_ids_sha({"a"}) != C.info_ids_sha({"a", "b"})


def test_info_ids_floor_aborts(tmp_path, monkeypatch):
    from conftest import ORIG_LANDING_INFO_MIN_IDS
    monkeypatch.setattr(C, "LANDING_INFO_MIN_IDS", ORIG_LANDING_INFO_MIN_IDS)   # 還原**生產值**（非自塞）
    B._LANDING_INFO_IDS.clear()
    stores = _stores_with_calendar(tmp_path)
    _land_info(stores)                                         # 7 個代號 < 3,000
    fm = _FakeFM({("TaiwanStockPrice", "2022-01-03"): DAY})
    st = B.run_dataset(C.DATASET_BY_KEY["price_daily"], "daily_slice", stores, fm, None, DV,
                       _args("--dataset", "price_daily", "--from", "2022-01-01", "--to", "2022-01-03"))
    assert st["aborted"] and "低於下限 3,000" in st["aborted"] and "只有 7 個代號" in st["aborted"] and fm.calls == 0
    for s_ in stores.values():
        s_.close()
    # 3,000 個以上（含被扣掉的 所有證券 不算）→ 通過
    B._LANDING_INFO_IDS.clear()
    import shutil; shutil.rmtree(tmp_path)
    stores = _stores_with_calendar(tmp_path)
    big = [{"stock_id": f"{1000 + i}", "type": "twse", "industry_category": "x"} for i in range(3000)] + \
          [{"stock_id": f"7{i:05d}", "type": "tpex", "industry_category": "所有證券"} for i in range(50)]
    stores["universe"].record_success("stock_info", "raw_stock_info", "all", big, DV, "TaiwanStockInfo", ("stock_id",))
    assert len(B.info_ids_from_store(stores["universe"])) == 3000
    st2 = B.run_dataset(C.DATASET_BY_KEY["price_daily"], "daily_slice", stores, fm, None, DV,
                        _args("--dataset", "price_daily", "--from", "2022-01-01", "--to", "2022-01-03"))
    assert st2["aborted"] is None and st2["ok"] == 1
    for s_ in stores.values():
        s_.close()


def test_store_alter_tolerates_duplicate_column(tmp_path, monkeypatch):
    """建議 3：兩個 process 同時首次開舊 DB，第二個 ALTER 撞 duplicate column → 忽略；其他 OperationalError 照拋。"""
    import sqlite3
    from iching.store import Store
    db = tmp_path / "old.db"
    c = sqlite3.connect(db)
    c.execute("""CREATE TABLE sources(dataset TEXT PRIMARY KEY, finmind_dataset TEXT, data_version TEXT,
        first_fetched_at TEXT, last_fetched_at TEXT, n_requests INTEGER NOT NULL DEFAULT 0,
        n_rows INTEGER NOT NULL DEFAULT 0, min_date TEXT, max_date TEXT, columns TEXT)""")
    c.commit(); c.close()
    real_columns = Store.columns

    def stale_columns(self, table):
        cols = real_columns(self, table)
        # 模擬「PRAGMA 之後、ALTER 之前」另一個 process 已補欄：回報缺欄，讓本 process 去 ALTER 而撞 duplicate
        if table == "sources":
            sqlite3.connect(db).execute("ALTER TABLE sources ADD COLUMN landing_filter TEXT").connection.commit()
            return cols - {"landing_filter"}
        return cols
    monkeypatch.setattr(Store, "columns", stale_columns)
    with Store(db) as s:
        assert {"landing_filter", "n_filtered"} <= real_columns(s, "sources")


# ---------------------------------------------------------------------------
# 放量前必補（2026-09-10 驗收 b／c／d）
# ---------------------------------------------------------------------------
def test_price_daily_too_few_rows(tmp_path, monkeypatch):
    """(c) 上游截斷：HTTP 200 只回幾列 → failures(too_few_rows)、不寫 coverage → 下次重抓；達標後 ok。其他切片只 WARNING。"""
    from conftest import ORIG_PRICE_DAILY_MIN_ROWS
    monkeypatch.setattr(C, "PRICE_DAILY_MIN_ROWS", ORIG_PRICE_DAILY_MIN_ROWS)
    B._LANDING_INFO_IDS.clear()
    stores = _stores_with_calendar(tmp_path)
    _land_info(stores)
    three = [{"date": "2022-01-03", "stock_id": s} for s in ("2330", "1101", "0050")]
    fm = _FakeFM({("TaiwanStockPrice", "2022-01-03"): three})
    a = _args("--dataset", "price_daily", "--from", "2022-01-01", "--to", "2022-01-03")
    st = B.run_dataset(C.DATASET_BY_KEY["price_daily"], "daily_slice", stores, fm, None, DV, a)
    p = stores["prices"]
    assert st["failed"] == 1 and st["ok"] == 0 and not p.is_covered("price_daily", "2022-01-03", DV)
    f = p.failures_list("price_daily")
    assert len(f) == 1 and f[0][2] == B.TOO_FEW_ROWS == "too_few_rows" and "濾後 3 列 < 下限 1500" in f[0][3]
    assert p.rows_for_key("raw_price_daily", "2022-01-03") == 0
    # 重跑會再試（不是 covered）；這次回 1,700 列（含 200 列權證要濾掉 → 濾後 1,500 恰達標）
    full = [{"date": "2022-01-03", "stock_id": f"{1000 + i}"} for i in range(1500)] + \
           [{"date": "2022-01-03", "stock_id": f"03{i:04d}"} for i in range(200)]
    fm.table[("TaiwanStockPrice", "2022-01-03")] = full
    st2 = B.run_dataset(C.DATASET_BY_KEY["price_daily"], "daily_slice", stores, fm, None, DV, a)
    assert st2["ok"] == 1 and st2["filtered"] == 200 and p.is_covered("price_daily", "2022-01-03", DV) and p.failures_list("price_daily") == []
    assert p.rows_for_key("raw_price_daily", "2022-01-03") == 1500
    # 1,499 列 → 仍擋（邊界）
    fm.table[("TaiwanStockPrice", "2022-01-04")] = [dict(r, date="2022-01-04") for r in full[:1499]]
    st3 = B.run_dataset(C.DATASET_BY_KEY["price_daily"], "daily_slice", stores, fm, None, DV,
                        _args("--dataset", "price_daily", "--from", "2022-01-04", "--to", "2022-01-04"))
    assert st3["failed"] == 1 and not p.is_covered("price_daily", "2022-01-04", DV)
    # 其他切片（inst_buysell）3 列 → 只 WARNING、照常 coverage=ok
    fm2 = _FakeFM({("TaiwanStockInstitutionalInvestorsBuySell", "2022-01-03"): three})
    st4 = B.run_dataset(C.DATASET_BY_KEY["inst_buysell"], "daily_slice", stores, fm2, None, DV,
                        _args("--dataset", "inst_buysell", "--from", "2022-01-01", "--to", "2022-01-03"))
    assert st4["ok"] == 1 and st4["failed"] == 0 and stores["chips"].is_covered("inst_buysell", "2022-01-03", DV)
    for s_ in stores.values():
        s_.close()


def test_row_without_stock_id_aborts(tmp_path):
    """(b) 回應任一列缺 stock_id 鍵 → 中止本資料集、此鍵不落地（原本靜默不濾、filtered=0、無警告）。"""
    B._LANDING_INFO_IDS.clear()
    stores = _stores_with_calendar(tmp_path)
    _land_info(stores)
    bad = DAY + [{"date": "2022-01-03", "code": "030001"}]
    fm = _FakeFM({("TaiwanStockPrice", "2022-01-03"): DAY, ("TaiwanStockPrice", "2022-01-04"): bad})
    st = B.run_dataset(C.DATASET_BY_KEY["price_daily"], "daily_slice", stores, fm, None, DV,
                       _args("--dataset", "price_daily", "--from", "2022-01-01", "--to", "2022-01-04"))
    p = stores["prices"]
    assert st["ok"] == 1 and st["aborted"] and "1/19 列缺 stock_id 鍵" in st["aborted"]
    assert p.is_covered("price_daily", "2022-01-03", DV) and not p.is_covered("price_daily", "2022-01-04", DV)
    assert p.rows_for_key("raw_price_daily", "2022-01-04") == 0 and fm.calls == 2
    for s_ in stores.values():
        s_.close()


def test_info_ids_sha_recorded_and_change_aborts(tmp_path):
    """(d) sources.info_ids_sha 記名單指紋；回補中途 stock_info 被重抓（名單變）→ 有 ok 鍵的資料集中止；同名單不受影響。"""
    B._LANDING_INFO_IDS.clear()
    stores = _stores_with_calendar(tmp_path)
    _land_info(stores)
    sha1 = C.info_ids_sha(B.info_ids_from_store(stores["universe"]))
    fm = _FakeFM({("TaiwanStockPrice", "2022-01-03"): DAY, ("TaiwanStockPrice", "2022-01-04"): DAY})
    a = _args("--dataset", "price_daily", "--from", "2022-01-01", "--to", "2022-01-04")
    st = B.run_dataset(C.DATASET_BY_KEY["price_daily"], "daily_slice", stores, fm, None, DV,
                       _args("--dataset", "price_daily", "--from", "2022-01-01", "--to", "2022-01-03"))
    p = stores["prices"]
    assert st["ok"] == 1 and p.source_row("price_daily")["info_ids_sha"] == sha1 and len(sha1) == 12
    # 同名單、另一個 process（快取清空後重讀）→ 不擋
    B._LANDING_INFO_IDS.clear()
    st2 = B.run_dataset(C.DATASET_BY_KEY["price_daily"], "daily_slice", stores, fm, None, DV, a)
    assert st2["aborted"] is None and st2["skipped"] == 1 and st2["ok"] == 1
    # 模擬 `run --dataset stock_info --force`：名單多一檔 → 指紋變 → 中止、零請求、既有列不動
    _land_info(stores, ids=("1101", "2330", "0050", "910322", "01003T", "020000", "Cement", "9999"))
    B._LANDING_INFO_IDS.clear()
    sha2 = C.info_ids_sha(B.info_ids_from_store(stores["universe"]))
    assert sha2 != sha1
    fm.calls = 0
    st3 = B.run_dataset(C.DATASET_BY_KEY["price_daily"], "daily_slice", stores, fm, None, DV,
                        _args("--dataset", "price_daily", "--from", "2022-01-05", "--to", "2022-01-05"))
    assert st3["aborted"] and sha1 in st3["aborted"] and sha2 in st3["aborted"] and "不得 `--force` 重抓 stock_info" in st3["aborted"]
    assert f"rm -f {tmp_path}/*.db" in st3["aborted"] and fm.calls == 0
    assert p.rows_for_key("raw_price_daily", "2022-01-03") == 13
    # 沒有 ok 鍵的資料集（chips.db inst_buysell）不受影響
    assert B.info_ids_conflict(stores["chips"], C.DATASET_BY_KEY["inst_buysell"], sha2) is None
    # report 行帶指紋
    assert f"（info {sha1}）" in B.landing_filter_report_line(stores)
    for s_ in stores.values():
        s_.close()


def test_record_success_rollbacks_on_keyboard_interrupt(tmp_path):
    """建議 7：Ctrl-C 落在 executemany 中途 → 顯式 ROLLBACK、不留半套、不寫 coverage、連線不懸在交易中。"""
    from iching.store import Store

    class _Conn:
        """sqlite3.Connection 的屬性不可 monkeypatch，包一層讓 executemany 丟 KeyboardInterrupt。"""
        def __init__(self, c):
            self._c = c

        def __getattr__(self, n):
            return getattr(self._c, n)

        def executemany(self, *a, **k):
            raise KeyboardInterrupt

    with Store(tmp_path / "t.db") as st:
        st.record_success("price_daily", "raw_price_daily", "2022-01-03", [{"date": "2022-01-03", "stock_id": "2330"}], DV, "X")
        st.conn = _Conn(st.conn)
        with pytest.raises(KeyboardInterrupt):
            st.record_success("price_daily", "raw_price_daily", "2022-01-04", [{"date": "2022-01-04", "stock_id": "2330"}], DV, "X")
        assert not st.conn.in_transaction
        assert not st.is_covered("price_daily", "2022-01-04", DV) and st.rows_for_key("raw_price_daily", "2022-01-04") == 0
        assert st.is_covered("price_daily", "2022-01-03", DV) and st.rows_for_key("raw_price_daily", "2022-01-03") == 1   # 舊鍵不受影響
