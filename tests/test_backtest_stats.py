"""`scripts/backtest_stats.py`（PR-S2）：合成小資料集跑完整管線，驗 S2-1～S2-6／S2-9 的機械面。

**不讀 `data/backtest/`**（真資料一列都不碰）；日曆用 repo 的 `data/calendar_tpe.json`（純交易日序列、不是回測資料），
好讓 purge／embargo／區塊數的期待值與登錄書 `:295-297` 一致。合成列的屬性（池外／漲停／`no_entry`／空分數／halt／delist）
都放在固定的訊號日上，期待的計數在本檔**獨立寫死**，不由被測函式產生。
"""
from __future__ import annotations

import ast
import csv
import gzip
import hashlib
import json
import sys
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

import backtest_stats as BS

from iching.stats import windows

CAL = json.loads((ROOT / "data" / "calendar_tpe.json").read_text(encoding="utf-8"))["dates"]
N_STOCKS = 32            # 每市場每日池內檔數（≥ MIN_PAIRS=30，扣掉 5 檔漲停後 27 < 30 → 該日落掉）
N_OUT = 2                # 每市場每日池外檔數
LIMIT_UP_DAY, NO_ENTRY_DAY, NULL_SCORE_DAY, HALT_DAY = 25, 26, 27, 28   # 相對段起（都在 embargo 20 之後、purge 之前）
N_LIMIT_UP, N_HALT, N_DELIST, N_EXIT_DN = 5, 2, 1, 1


def make_rows(segment: str, horizon: str, rng: np.random.Generator) -> list[list[str]]:
    a, b = BS.SEGMENTS[segment]
    p0, p1 = windows.segment_bounds(CAL, a, b)
    h = BS.H_BY_HORIZON[horizon]
    rows = []
    for k, pos in enumerate(range(p0, p1)):
        d = CAL[pos]
        for mk in ("twse", "tpex"):
            for s in range(N_STOCKS + N_OUT):
                in_pool = s < N_STOCKS
                score = float(rng.uniform(20, 80))
                fwd = 0.02 * (score - 50) / 30 + float(rng.normal(0, 0.03))
                kw = 1 + (s * 7 + k) % 64
                exit_r, up, dn, fr, sc = "", "0", "0", f"{fwd:.6f}", repr(score)
                if in_pool and k == LIMIT_UP_DAY and mk == "twse" and s < N_LIMIT_UP:
                    up = "1"
                if in_pool and k == NO_ENTRY_DAY and mk == "twse" and s == 0:
                    exit_r, up, dn, fr = "no_entry", "", "", ""
                if in_pool and k == NULL_SCORE_DAY and mk == "twse" and s == 1:
                    sc = ""
                if in_pool and k == HALT_DAY and mk == "twse":
                    if s < N_HALT:
                        exit_r = "halt"
                    elif s < N_HALT + N_DELIST:
                        exit_r = "delist"
                    elif s < N_HALT + N_DELIST + N_EXIT_DN:
                        dn = "1"
                cov = "reweighted" if (not sc or s % 8 == 0) else "full"
                rows.append([d, mk, f"{1000 + s}", horizon, sc, "1" if in_pool else "0", cov, str(kw), "000000",
                             fr, f"{0.001 * h:.6f}", exit_r, up, dn])
    return rows


def write_dataset(data_dir: Path, segments=("train", "valid"), *, tamper: dict | None = None) -> dict:
    """造六檔＋manifest（檔案層計數照實算，與 `check_file_counts` 的守門對得上）。`tamper` 覆寫 manifest 頂層鍵。"""
    data_dir.mkdir(parents=True, exist_ok=True)
    rng = np.random.default_rng(7)
    files = {}
    for seg in segments:
        for hz in BS.HORIZONS:
            rows = make_rows(seg, hz, rng)
            p = data_dir / f"{seg}_{hz}.csv.gz"
            with gzip.open(p, "wt", encoding="utf-8", newline="") as fh:
                w = csv.writer(fh)
                w.writerow(BS.COLUMNS)
                w.writerows(rows)
            er = [r[11] for r in rows]
            files[p.name] = {
                "sha256": hashlib.sha256(p.read_bytes()).hexdigest(), "n_rows": len(rows),
                "n_entry_limit_up": sum(r[12] == "1" for r in rows), "n_exit_limit_down": sum(r[13] == "1" for r in rows),
                "n_fwd_ret_missing": sum(r[9] == "" for r in rows),
                "exit_reason_counts": {"ok": er.count(""), "halt": er.count("halt"), "delist": er.count("delist"),
                                       "no_entry": er.count("no_entry")},
            }
    man = {"schema": 1, "params_sha": BS.EXPECTED_PARAMS_SHA, "pool_semantics": BS.EXPECTED_POOL_SEMANTICS,
           "h_by_horizon": dict(BS.H_BY_HORIZON), "columns": list(BS.COLUMNS), "files": files,
           "calendar": {"data_end": CAL[-1], "n": len(CAL)}, "n_market_rows_excluded": 0, "data_version": "synthetic",
           "head": "0" * 40, "model_version": {"twse": "x", "tpex": "y"}}
    if tamper:
        man.update(tamper)
    (data_dir / "manifest.json").write_text(json.dumps(man), encoding="utf-8")
    return man


@pytest.fixture(scope="module")
def synth(tmp_path_factory):
    """跑一次兩段（先 train 無 peer，再 valid 帶 peer，再 train 帶 peer），其餘測試共用。"""
    base = tmp_path_factory.mktemp("s2")
    data_dir, out_dir = base / "data", base / "runs"
    write_dataset(data_dir)
    common = ["--data-dir", str(data_dir), "--calendar", str(ROOT / "data" / "calendar_tpe.json"),
              "--rank-table", str(ROOT / "data" / "rank_table.json"), "--out-dir", str(out_dir), "--cache-dir", "", "--date", "2099-01-01"]
    assert BS.main(["--segment", "train", "--no-peer", *common]) == 0
    assert BS.main(["--segment", "valid", *common]) == 0
    assert BS.main(["--segment", "train", *common]) == 0
    train = json.loads((out_dir / "train_2099-01-01.json").read_text(encoding="utf-8"))
    valid = json.loads((out_dir / "valid_2099-01-01.json").read_text(encoding="utf-8"))
    return {"data_dir": data_dir, "out_dir": out_dir, "common": common, "train": train, "valid": valid}


def _cell(rep, mk, hz):
    return next(c for c in rep["cells"] if c["market"] == mk and c["horizon"] == hz)


# ---------------------------------------------------------------- S2-1 開跑守門

@pytest.mark.parametrize("tamper", [{"params_sha": "deadbeef0000"}, {"pool_semantics": "snapshot"},
                                    {"h_by_horizon": {"short": 5, "swing": 10, "mid": 20}}])
def test_gate_manifest_mismatch_rc2_and_no_output(tmp_path, tamper):
    data_dir, out_dir = tmp_path / "data", tmp_path / "runs"
    write_dataset(data_dir, segments=("valid",), tamper=tamper)
    rc = BS.main(["--segment", "valid", "--data-dir", str(data_dir), "--out-dir", str(out_dir), "--cache-dir", "",
                  "--calendar", str(ROOT / "data" / "calendar_tpe.json"), "--rank-table", str(ROOT / "data" / "rank_table.json")])
    assert rc == 2
    assert not out_dir.exists() or not list(out_dir.iterdir())


def test_gate_file_sha_mismatch_rc2(tmp_path):
    data_dir = tmp_path / "data"
    man = write_dataset(data_dir, segments=("valid",))
    man["files"]["valid_short.csv.gz"]["sha256"] = "0" * 64
    (data_dir / "manifest.json").write_text(json.dumps(man), encoding="utf-8")
    rc = BS.main(["--segment", "valid", "--data-dir", str(data_dir), "--out-dir", str(tmp_path / "runs"), "--cache-dir", "",
                  "--calendar", str(ROOT / "data" / "calendar_tpe.json"), "--rank-table", str(ROOT / "data" / "rank_table.json")])
    assert rc == 2


def test_gate_pins_match_frozen_values():
    """釘值互核：params_sha＝真 manifest、rank_table sha＝`tests/test_prereg_frozen.FROZEN["v2"]`（只讀 manifest 的指紋欄，不讀資料檔）。"""
    sys.path.insert(0, str(ROOT / "tests"))
    import test_prereg_frozen as TPF
    assert BS.RANK_TABLE_SHA256 == TPF.FROZEN["v2"]["rank_sha256"]
    assert hashlib.sha256((ROOT / "data" / "rank_table.json").read_bytes()).hexdigest() == BS.RANK_TABLE_SHA256
    man = json.loads((ROOT / "data" / "backtest" / "manifest.json").read_text(encoding="utf-8"))
    assert man["params_sha"] == BS.EXPECTED_PARAMS_SHA == "cb3f2d905846"
    assert man["pool_semantics"] == BS.EXPECTED_POOL_SEMANTICS == "pit-1"
    assert man["h_by_horizon"] == {"short": 10, "swing": 20, "mid": 40}


# ---------------------------------------------------------------- S2-2 無 holdout

def test_no_holdout_anywhere():
    assert set(BS.SEGMENTS) == {"train", "valid"}
    with pytest.raises(SystemExit) as e:
        BS.main(["--segment", "holdout"])
    assert e.value.code == 2
    tree = ast.parse((ROOT / "scripts" / "backtest_stats.py").read_text(encoding="utf-8"))
    tree.body = [n for n in tree.body if not (isinstance(n, ast.Expr) and isinstance(getattr(n, "value", None), ast.Constant))]
    assert "holdout" not in ast.dump(tree).lower(), "程式碼（扣除模組 docstring）出現 holdout"
    from iching.config import SEGMENTS as CFG
    assert "holdout" in CFG and BS.SEGMENTS == {k: tuple(CFG[k]) for k in ("train", "valid")}


# ---------------------------------------------------------------- S2-3 列處理計數

def test_row_processing_counts(synth):
    v = synth["valid"]
    for hz in BS.HORIZONS:
        h = BS.H_BY_HORIZON[hz]
        c = _cell(v, "twse", hz)
        r = c["rows"]
        n_days = 368
        assert r["in_file"] == n_days * (N_STOCKS + N_OUT) and r["in_pool"] == n_days * N_STOCKS
        assert r["out_of_pool"] == n_days * N_OUT
        assert r["purged_rows"] == (h + 1) * N_STOCKS and r["embargo_rows"] == 20 * N_STOCKS
        assert r["in_window"] == (n_days - (h + 1) - 20) * N_STOCKS
        assert r["excluded_entry_limit_up"] == N_LIMIT_UP == r["raw"]["entry_limit_up"]
        assert r["skipped_null_fwd_ret"] == 1 == r["raw"]["null_fwd_ret"] == r["raw"]["no_entry"]
        assert r["skipped_null_base_score"] == 1 == r["raw"]["null_base_score"]
        assert r["counted"] == r["in_window"] - N_LIMIT_UP - 1 - 1
        assert r["kept_halt"] == N_HALT and r["kept_delist"] == N_DELIST and r["kept_exit_limit_down"] == N_EXIT_DN
        assert 0 < r["reweighted_share"] < 1 and r["reweighted"] == round(r["reweighted_share"] * r["counted"])
        assert c["ic"]["days_below_min_n"] == 1, "漲停日剩 27 對 < 30 → 落掉一天"
        assert c["ic"]["n_days_used"] == c["window"]["n_eff_index"] - 1
        assert c["ic"]["nan_pairs"] == 0
        t = _cell(v, "tpex", hz)
        assert t["rows"]["excluded_entry_limit_up"] == 0 and t["ic"]["days_below_min_n"] == 0
        assert t["ic"]["n_days_used"] == t["window"]["n_eff_index"]
    # 訓練段：無 embargo
    for hz in BS.HORIZONS:
        c = _cell(synth["train"], "twse", hz)
        assert c["rows"]["embargo_rows"] == 0 and c["window"]["embargo_days"] == 0 and c["embargo"] == 0


# ---------------------------------------------------------------- S2-4 purge／embargo 對日曆實算

def test_purge_embargo_against_calendar(synth):
    pv0, pv1 = windows.segment_bounds(CAL, *BS.SEGMENTS["valid"])
    _pt0, pt1 = windows.segment_bounds(CAL, *BS.SEGMENTS["train"])
    assert CAL[pv1] == "2025-01-02" and CAL[pt1] == "2023-07-03"
    want_first = {"short": "2024-12-17", "swing": "2024-12-03", "mid": "2024-11-05"}
    for hz, h in BS.H_BY_HORIZON.items():
        for mk in ("twse", "tpex"):
            w = _cell(synth["valid"], mk, hz)["window"]
            assert w["purge_days"] == h + 1 and w["purge_first_day"] == want_first[hz] == CAL[pv1 - h - 1]
            assert w["purge_last_day"] == "2024-12-31" and w["boundary_date"] == "2025-01-02" and w["boundary_pos"] == pv1
            assert w["embargo_days"] == 20 and w["embargo_last_day"] == CAL[pv0 + 19] and w["first_day_eligible"] == CAL[pv0 + 20]
            assert w["last_day_eligible"] == CAL[pv1 - h - 2] and w["n_eff_index"] == 368 - (h + 1) - 20
            assert w["n_eff_t5"] == 368 - h - 20
            wt = _cell(synth["train"], mk, hz)["window"]
            assert wt["purge_days"] == h + 1 and wt["purge_first_day"] == CAL[pt1 - h - 1] and wt["embargo_days"] == 0
            assert wt["first_day_eligible"] == "2021-01-04" and wt["n_eff_index"] == 603 - (h + 1) and wt["n_eff_t5"] == 603 - h


# ---------------------------------------------------------------- S2-5／S2-6 schema、區塊數、verdict、cost_grid

def test_schema_blocks_and_verdict(synth):
    want = {("train", "short"): 19.77, ("train", "swing"): 9.72, ("train", "mid"): 4.69,
            ("valid", "short"): 11.27, ("valid", "swing"): 5.47, ("valid", "mid"): 2.57}
    for seg in ("train", "valid"):
        rep = synth[seg]
        assert rep["schema"] == 1 and rep["segment"] == seg and rep["peer"]["segment"] == ("valid" if seg == "train" else "train")
        for k in ("elapsed_sec", "max_rss_kb", "numpy_version", "python_version", "cache_used"):
            assert k in rep["meta"]
        assert rep["meta"]["numpy_version"] == np.__version__
        assert set(rep["rulings"]) == {f"Q{i}" for i in range(9, 18)}
        assert rep["params"]["seed"] == 42 and rep["params"]["nboot"] == 1000 and rep["params"]["min_pairs"] == 30
        for c in rep["cells"]:
            for k in ("n_days_used", "ic_mean", "series", "nw_se_h", "nw_t_h", "nw_se_2h", "nw_t_2h", "boot_ci",
                      "boot_block", "boot_nboot", "boot_seed", "dates", "pairs"):
                assert k in c["ic"], k
            assert c["ic"]["boot_block"] == max(21, 3 * c["h"]) and c["ic"]["boot_nboot"] == 1000 and c["ic"]["boot_seed"] == 42
            assert len(c["ic"]["dates"]) == len(c["ic"]["series"]) == c["ic"]["n_days_used"]
            assert abs(float(np.mean(c["ic"]["series"])) - c["ic"]["ic_mean"]) < 1e-12
            w = c["window"]
            assert round(w["n_blocks_t5"], 2) == want[(seg, c["horizon"])]
            assert {"pos_share", "pos_months", "n_months"} <= set(c["secondary1"])
            assert {"excess_mean", "boot_ci"} <= set(c["secondary2"])
            assert {"bottom_short_mean", "boot_ci"} <= set(c["short_side"])
            g = c["cost_grid"]
            assert set(g["slip"]) == {"0.001", "0.002", "0.003"} and set(g["borrow"]) == {"0.01", "0.02", "0.04"}
            assert all({"pos_share", "excess_mean", "boot_ci", "secondary_pass", "flip_vs_base"} <= set(x) for x in g["slip"].values())
            assert all({"bottom_short_mean", "flip_vs_base"} <= set(x) for x in g["borrow"].values())
            assert g["slip"]["0.002"]["flip_vs_base"] is False and g["borrow"]["0.02"]["flip_vs_base"] is False
            assert all({"year", "role", "ic_mean", "sign"} <= set(y) for y in c["yearly"])
            assert {"sharpe_spread_daily", "max_drawdown_excess_cumsum", "monthly_winrate_excess"} <= set(c["report_only"])
            assert set(c["hexagram_groups"]) >= {"high", "mid", "low"}
            if c["h"] == 40:
                assert c["ic"]["boot_block_sensitivity"] == 126 and len(c["ic"]["boot_ci_block_sensitivity"]) == 2
            if w["n_blocks_t5"] < 8:
                assert c["verdict"] == "insufficient" and c["insufficient"] is True
            else:
                assert c["verdict"] in ("adopted", "rejected") and c["robust"]["segments_same_sign"] is not None
    # 波段／中期四格（驗證段）與訓練段中期兩格必為 insufficient，與 IC 大小無關
    for hz in ("swing", "mid"):
        assert all(_cell(synth["valid"], mk, hz)["verdict"] == "insufficient" for mk in ("twse", "tpex"))
    assert all(_cell(synth["train"], mk, "mid")["verdict"] == "insufficient" for mk in ("twse", "tpex"))
    # 合成資料是 fwd = β·score + 雜訊、β>0：短線 IC 必為正
    assert _cell(synth["valid"], "tpex", "short")["ic"]["ic_mean"] > 0.05


def test_without_peer_verdict_left_blank(tmp_path, synth):
    out = tmp_path / "runs"
    common = synth["common"][:]
    common[common.index("--out-dir") + 1] = str(out)
    assert BS.main(["--segment", "valid", "--no-peer", *common]) == 0
    rep = json.loads((out / "valid_2099-01-01.json").read_text(encoding="utf-8"))
    assert rep["peer"] is None
    for c in rep["cells"]:
        if c["window"]["n_blocks_t5"] >= 8:
            assert c["verdict"] is None and "同儕" in c["verdict_note"]
        else:
            assert c["verdict"] == "insufficient"


def test_txt_numbers_match_json(synth):
    txt = (synth["out_dir"] / "valid_2099-01-01.txt").read_text(encoding="utf-8")
    c = _cell(synth["valid"], "tpex", "short")
    assert f"{c['ic']['ic_mean']:.4f}" in txt and f"{c['ic']['nw_t_h']:.2f}" in txt
    assert f"{c['rows']['counted']}" in txt and "2024-12-17" in txt


def test_independent_spearman_recompute_one_day(synth):
    """純標準庫重算合成資料某一天的 Spearman（驗收驗法的縮影）：與 json 序列逐位相符到 1e-12。"""
    c = _cell(synth["valid"], "tpex", "short")
    day = c["ic"]["dates"][40]
    rows = []
    with gzip.open(synth["data_dir"] / "valid_short.csv.gz", "rt", encoding="utf-8", newline="") as fh:
        r = csv.reader(fh)
        next(r)
        for row in r:
            if row[0] == day and row[1] == "tpex" and row[5] == "1" and row[12] != "1" and row[9] and row[4]:
                rows.append((float(row[4]), float(row[9])))

    def rank(xs):
        order = sorted(range(len(xs)), key=lambda i: xs[i])
        rk = [0.0] * len(xs)
        i = 0
        while i < len(order):
            j = i
            while j + 1 < len(order) and xs[order[j + 1]] == xs[order[i]]:
                j += 1
            for k in range(i, j + 1):
                rk[order[k]] = (i + j) / 2 + 1
            i = j + 1
        return rk

    ra, rb = rank([x for x, _ in rows]), rank([y for _, y in rows])
    ma, mb = sum(ra) / len(ra), sum(rb) / len(rb)
    cov = sum((a - ma) * (b - mb) for a, b in zip(ra, rb))
    va = sum((a - ma) ** 2 for a in ra)
    vb = sum((b - mb) ** 2 for b in rb)
    rho = cov / (va * vb) ** 0.5
    assert abs(rho - c["ic"]["series"][40]) < 1e-12 and len(rows) == c["ic"]["pairs"][40]


# ---------------------------------------------------------------- S2-9 附錄 A 只套用不重排

def test_rank_table_applied_not_resorted(tmp_path, synth, monkeypatch):
    """注入一份把高／低組對調的排序表：輸出的高組列數必須＝原低組列數——程式只查表、不重排。"""
    rt = json.loads((ROOT / "data" / "rank_table.json").read_text(encoding="utf-8"))
    swap = {"high": "low", "low": "high", "mid": "mid"}
    for r in rt["rows"]:
        r["group"] = swap[r["group"]]
    p = tmp_path / "rank_swapped.json"
    p.write_text(json.dumps(rt), encoding="utf-8")
    monkeypatch.setattr(BS, "RANK_TABLE_SHA256", hashlib.sha256(p.read_bytes()).hexdigest())
    out = tmp_path / "runs"
    common = synth["common"][:]
    common[common.index("--out-dir") + 1] = str(out)
    common[common.index("--rank-table") + 1] = str(p)
    assert BS.main(["--segment", "valid", "--no-peer", *common]) == 0
    rep = json.loads((out / "valid_2099-01-01.json").read_text(encoding="utf-8"))
    for mk in ("twse", "tpex"):
        for hz in BS.HORIZONS:
            a, b = _cell(synth["valid"], mk, hz)["hexagram_groups"], _cell(rep, mk, hz)["hexagram_groups"]
            assert b["high"]["n_rows"] == a["low"]["n_rows"] and b["low"]["n_rows"] == a["high"]["n_rows"]
            assert b["high"]["n_hexagrams"] == a["low"]["n_hexagrams"]
    src = ast.dump(ast.parse(__import__("inspect").getsource(BS.rank_groups)))
    assert "sort" not in src.lower()


def test_rank_table_sha_mismatch_rc2(tmp_path, synth):
    rt = json.loads((ROOT / "data" / "rank_table.json").read_text(encoding="utf-8"))
    p = tmp_path / "rank_touched.json"
    p.write_text(json.dumps(rt, indent=2), encoding="utf-8")        # 內容同、位元組不同 → sha 不同
    common = synth["common"][:]
    common[common.index("--out-dir") + 1] = str(tmp_path / "runs")
    common[common.index("--rank-table") + 1] = str(p)
    assert BS.main(["--segment", "valid", "--no-peer", *common]) == 2


# ---------------------------------------------------------------- 字樣

def test_no_trade_direction_wording_in_s2_files():
    for rel in ("scripts/backtest_stats.py", "scripts/backtest_gate.py", "scripts/backtest_report.py",
                "tests/test_backtest_stats.py", "tests/test_backtest_gate.py", "tests/test_backtest_report.py"):
        txt = (ROOT / rel).read_text(encoding="utf-8")
        for w in ("買", "賣", "做多", "做空"):
            assert w not in txt.replace('("買", "賣", "做多", "做空")', ""), (rel, w)
