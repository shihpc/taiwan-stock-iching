"""`scripts/check_dataset.py`（回測資料出口的獨立抽驗器）的離線測試——合成世界＝`tests/test_export_dataset.py` 的同一套
（`build_full`＋`add_pit_rows`、`scan_features`＋`replay_scores`、日曆寫成 `data/calendar_tpe.json`、`SEGMENTS` 改成 DAYS 兩半）。

- ① 先跑 `export_dataset.py` 產六檔，再跑 `check_dataset.py`：rc 0、C0～C3 全部 mismatch 0、`--sample` 大於檔內列數＝全檔逐列
  （`sample_is_full`）、抽樣組成含 halt／delist／no_entry、除權息強制層抽到 1101 且視窗跨過除權息日、報告寫出且含
  `raw_dividend_result` 欄名。
- ② 竄改某檔一列 `fwd_ret` 一位 → rc 1、C3 `fwd_ret` 的 mismatch 恰指到該鍵、其餘欄仍 0。
- ③ 把 h 表改錯（`--h short=9`／monkeypatch 模組常數）→ rc 1、short 檔大量 mismatch、swing／mid 檔零。
- ④ 刪掉一列 → C1 列數與鍵集合不符、三檔鍵序列不同；db 缺／檔案缺／欄序不對 → rc 2。
"""
from __future__ import annotations

import csv
import gzip
import io
import json
import shutil
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import check_dataset as CK  # noqa: E402
import export_dataset as EXP  # noqa: E402
import replay_scores as RP  # noqa: E402
import scan_features as SF  # noqa: E402
from iching import calendar as CAL  # noqa: E402
from iching import daily_core as DC  # noqa: E402
from iching import replay_io as RIO  # noqa: E402
from synth_db import DAYS, DV, EX_I, add_pit_rows, build_full  # noqa: E402

WINDOW = 30
WC, ZC, YD = 63, 66, 68
SEG = {"train": (DAYS[0], DAYS[49]), "valid": (DAYS[50], DAYS[79])}
SEG_ARG = ",".join(f"{k}={a}:{b}" for k, (a, b) in SEG.items())
FILES = [f"{s}_{hz}.csv.gz" for s in SEG for hz in CK.HORIZONS]


@pytest.fixture(scope="module")
def world(tmp_path_factory) -> dict:
    base = tmp_path_factory.mktemp("checkds")
    cache, repo = base / "cache", base / "repo"
    build_full(cache)
    add_pit_rows(cache, z_c=ZC, w_c=WC, y_d=YD)
    assert SF.main(["--cache-dir", str(cache), "--quiet", "--allow-short-warmup", "--warmup-days", "0"]) == 0
    assert RP.main(["--cache-dir", str(cache), "--window", str(WINDOW), "--quiet"]) == 0
    src = RIO.ReplaySource(cache, DV, window=WINDOW)
    cal = src.trading_dates()
    CAL.write_calendar_json(repo / DC.CALENDAR_TPE_FILE, CAL.calendar_payload("tpe", cal, DV))
    src.close()
    assert cal == DAYS
    saved = dict(EXP.SEGMENTS)
    EXP.SEGMENTS.clear()
    EXP.SEGMENTS.update(SEG)
    try:
        assert EXP.main(["--cache-dir", str(cache), "--out", str(repo), "--calendar", str(repo / DC.CALENDAR_TPE_FILE)]) == 0
    finally:
        EXP.SEGMENTS.clear()
        EXP.SEGMENTS.update(saved)
    return {"cache": cache, "repo": repo, "data": repo / EXP.OUT_DIR, "cal": cal}


def _check(world, data_dir: Path, out: Path, *extra: str, sample: int = 100000) -> tuple[int, dict]:
    rc = CK.main(["--cache-dir", str(world["cache"]), "--data-dir", str(data_dir), "--calendar", str(world["repo"] / DC.CALENDAR_TPE_FILE),
                  "--segments", SEG_ARG, "--sample", str(sample), "--seed", "1", "--out", str(out), *extra])
    return rc, json.loads(out.read_text(encoding="utf-8"))


def _copy_data(world, dst: Path) -> Path:
    shutil.copytree(world["data"], dst)
    return dst


def _rewrite(path: Path, edit) -> None:
    """讀 gz CSV → `edit(rows)` 就地改 → 寫回（同格式）。"""
    with gzip.open(path, "rt", encoding="utf-8", newline="") as g:
        rows = list(csv.reader(g))
    edit(rows)
    buf = io.StringIO()
    csv.writer(buf, lineterminator="\n").writerows(rows)
    with gzip.GzipFile(path, "wb", mtime=0) as g:
        g.write(buf.getvalue().encode("utf-8"))


def _c3_total(rep: dict) -> int:
    return rep["checks"]["C3_computed_columns"]["_total"]["n_mismatch"]


# ---------------------------------------------------------------------------
def test_clean_export_passes_full_check(world, tmp_path, capsys):
    rc, rep = _check(world, world["data"], tmp_path / "rep.json")
    assert rc == 0 and rep["rc"] == 0
    assert rep["data_version"] == DV and rep["data_end"] == DAYS[-1] and rep["h_by_horizon"] == CK.H_BY_HORIZON
    assert set(rep["files"]) == set(FILES)
    assert rep["checks"]["C0_manifest"]["n_mismatch"] == 0 and rep["checks"]["C0_manifest"]["n_checked"] >= 6 + 4 + 1 + 2
    assert rep["checks"]["C1_rows_and_keys"]["n_mismatch"] == 0
    assert rep["checks"]["C2_score_columns"]["n_mismatch"] == 0 and rep["checks"]["C2_score_columns"]["n_checked"] > 1000
    assert _c3_total(rep) == 0
    for c in CK.CALC_COLS:
        assert rep["checks"]["C3_computed_columns"][c]["n_checked"] == rep["checks"]["C2_score_columns"]["n_checked"]
    assert rep["n_prev_close_before_window"]["n"] == 0
    # 每檔列數＝db、鍵集合＝db、全檔逐列；exit_reason 三類都在抽樣裡
    total = 0
    for name, f in rep["files"].items():
        assert f["n_rows"] == f["db_rows"] > 0 and f["key_set_equal_db"] and f["dates_equal_db"] and f["first_divergence_vs_db_order"] is None
        assert f["sample"]["sample_is_full"] and f["sample"]["n"] == f["n_rows"]
        assert f["sample"]["exit_reason_counts"] == f["exit_reason_counts_all"]
        total += f["n_rows"]
    assert total == rep["checks"]["C2_score_columns"]["n_checked"]
    reasons = {k for f in rep["files"].values() for k in f["sample"]["exit_reason_counts"]}
    assert {"", "halt", "delist", "no_entry"} <= reasons
    # 除權息強制層：1101 第 EX_I 日除息落在 train 段，train 三檔都抽到它且有跨窗列
    for hz in CK.HORIZONS:
        comp = rep["files"][f"train_{hz}.csv.gz"]["sample"]["composition"]
        assert comp["div_stocks_with_rows"] == 1 and comp["div_stocks_straddling"] == 1 and comp["dividend_forced"] >= 0
    assert DAYS[EX_I] <= SEG["train"][1]
    # 報告含 raw_dividend_result 欄名與耗時；stdout 有摘要
    assert {"stock_id", "date", "before_price", "after_price"} <= set(rep["dividend_table"]["table_info_columns"])
    assert rep["dividend_table"]["stocks"] == 1 and rep["dividend_table"]["conflicting_duplicates"] == 0
    assert rep["elapsed_s"] >= 0 and rep["price_rows_loaded"]["conflicting_duplicates"] == 0
    out = capsys.readouterr().out
    assert "rc=0" in out and "raw_dividend_result" in out


def test_sampling_is_stratified_and_seeded(world, tmp_path):
    rc, a = _check(world, world["data"], tmp_path / "a.json", sample=40)
    assert rc == 0
    _, b = _check(world, world["data"], tmp_path / "b.json", sample=40)
    for name in FILES:
        sa, sb = a["files"][name]["sample"], b["files"][name]["sample"]
        assert not sa["sample_is_full"] and sa == sb, name                 # 同 seed 同抽樣
        comp, allc = sa["composition"], a["files"][name]["exit_reason_counts_all"]
        for k in ("halt", "delist", "no_entry"):
            assert comp[k] == min(CK.STRATA_MIN, allc.get(k, 0)), (name, k)   # 有多少抽多少、至少 20
        assert sa["n"] >= 40


def test_tampered_fwd_ret_is_caught_at_that_key(world, tmp_path):
    data = _copy_data(world, tmp_path / "data")
    target: list[list[str]] = []

    def edit(rows):
        i = next(i for i, r in enumerate(rows) if i and r[9] and r[11] == "")
        r = rows[i]
        r[9] = r[9][:-1] + ("1" if r[9][-1] != "1" else "2")
        target.append(r[:4])

    _rewrite(data / "valid_swing.csv.gz", edit)
    rc, rep = _check(world, data, tmp_path / "rep.json")
    assert rc == 1
    c3 = rep["checks"]["C3_computed_columns"]
    assert c3["fwd_ret"]["n_mismatch"] == 1 and c3["fwd_ret"]["examples"][0]["key"] == target[0]
    assert all(c3[c]["n_mismatch"] == 0 for c in CK.CALC_COLS if c != "fwd_ret")
    assert rep["checks"]["C1_rows_and_keys"]["n_mismatch"] == 0 and rep["checks"]["C2_score_columns"]["n_mismatch"] == 0
    # manifest 的 sha256 也抓得到（C0）
    assert rep["checks"]["C0_manifest"]["n_mismatch"] == 1 and rep["checks"]["C0_manifest"]["examples"][0]["name"] == "valid_swing.csv.gz"


def test_wrong_h_table_turns_short_files_red(world, tmp_path, monkeypatch):
    rc, rep = _check(world, world["data"], tmp_path / "a.json", "--h", "short=9")
    assert rc == 1 and rep["h_by_horizon"]["short"] == 9
    assert _c3_total(rep) > 100 and rep["checks"]["C0_manifest"]["n_mismatch"] == 1     # manifest 的 h 表也對不上
    keys = {tuple(e["key"]) for c in CK.CALC_COLS for e in rep["checks"]["C3_computed_columns"][c]["examples"]}
    assert keys and all(k[3] == "short" for k in keys)
    assert rep["checks"]["C3_computed_columns"]["fwd_ret"]["n_mismatch"] > 100
    # 模組常數也能 monkeypatch（不靠 CLI）
    monkeypatch.setitem(CK.H_BY_HORIZON, "mid", 39)
    rc2, rep2 = _check(world, world["data"], tmp_path / "b.json")
    assert rc2 == 1 and _c3_total(rep2) > 0
    assert all(k[3] == "mid" for c in CK.CALC_COLS for e in rep2["checks"]["C3_computed_columns"][c]["examples"] for k in [tuple(e["key"])])


def test_missing_row_fails_c1_and_abort_paths_rc2(world, tmp_path):
    data = _copy_data(world, tmp_path / "data")
    _rewrite(data / "train_mid.csv.gz", lambda rows: rows.pop(5))
    rc, rep = _check(world, data, tmp_path / "rep.json")
    assert rc == 1
    c1 = rep["checks"]["C1_rows_and_keys"]
    f = rep["files"]["train_mid.csv.gz"]
    assert f["n_rows"] == f["db_rows"] - 1 and not f["key_set_equal_db"] and f["first_divergence_vs_db_order"]["row"] == 5
    assert {e["field"] for e in c1["examples"]} >= {"n_rows", "key_set", "key_seq_across_horizons"}
    # 欄序不對 → rc 2
    _rewrite(data / "train_short.csv.gz", lambda rows: rows[0].reverse())
    assert _check(world, data, tmp_path / "r2.json")[0] == 2
    # 檔案全缺 → rc 2；db 缺 → rc 2
    assert _check(world, tmp_path / "nowhere", tmp_path / "r3.json")[0] == 2
    empty_cache = tmp_path / "empty_cache"
    empty_cache.mkdir()
    rc4 = CK.main(["--cache-dir", str(empty_cache), "--data-dir", str(world["data"]), "--calendar", str(world["repo"] / DC.CALENDAR_TPE_FILE),
                   "--segments", SEG_ARG, "--out", str(tmp_path / "r4.json")])
    assert rc4 == 2 and "abort" in json.loads((tmp_path / "r4.json").read_text(encoding="utf-8"))


def test_constants_match_spec():
    assert CK.SEGMENTS == {"train": ("2021-01-01", "2023-06-30"), "valid": ("2023-07-01", "2024-12-31")}
    assert CK.H_BY_HORIZON == {"short": 10, "swing": 20, "mid": 40} and CK.COLUMNS == EXP.COLUMNS
    src = (ROOT / "scripts" / "check_dataset.py").read_text(encoding="utf-8")
    assert "import export_dataset" not in src and "from export_dataset" not in src


@pytest.mark.parametrize("mutation", ["no_factor", "entry_at_t_close"])
def test_mutated_export_is_caught(world, tmp_path, monkeypatch, mutation):
    """出口端的突變（不是檔案竄改）：少乘係數／用 T 收盤進場 → 重新匯出 → 抽驗器 rc 1、只有 fwd_ret 紅。"""
    if mutation == "no_factor":
        monkeypatch.setattr(EXP, "adj_price", lambda raw, date, fac: raw)
    else:
        monkeypatch.setattr(EXP, "entry_price", lambda book, sid, e_pos, fac: EXP.adj_price(book.close_at(sid, e_pos - 1), book.cal[e_pos - 1], fac))
    monkeypatch.setattr(EXP, "SEGMENTS", dict(SEG))
    out = tmp_path / "out"
    assert EXP.main(["--cache-dir", str(world["cache"]), "--out", str(out), "--calendar", str(world["repo"] / DC.CALENDAR_TPE_FILE)]) == 0
    rc, rep = _check(world, out / EXP.OUT_DIR, tmp_path / "rep.json")
    assert rc == 1 and rep["checks"]["C0_manifest"]["n_mismatch"] == 0 and rep["checks"]["C1_rows_and_keys"]["n_mismatch"] == 0
    c3 = rep["checks"]["C3_computed_columns"]
    assert c3["fwd_ret"]["n_mismatch"] > 0 and all(c3[c]["n_mismatch"] == 0 for c in CK.CALC_COLS if c != "fwd_ret")
    if mutation == "no_factor":                                   # 只有跨除權息的 1101 列會變
        assert {e["key"][2] for e in c3["fwd_ret"]["examples"]} == {"1101"}
    else:
        assert c3["fwd_ret"]["n_mismatch"] > 1000
