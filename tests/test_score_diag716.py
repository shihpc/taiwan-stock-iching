"""`scripts/score_diag716.py`：§16.5 `:716` 被標組的族組成診斷（裁定 #65 ②，`docs/P3-CALIBRATION.md` §27）。

分析流程一律用**真實 replay 產出的 db**（`synth_db.build_full`＋`replay_scores`），報告用真實 `score_stats.run` 產出、
只改寫 `summary.explain_716` 指向要診斷的組。期待值（筆數、權重）以原始 SQL 或手算另算，不由被測函式產生。
"""
from __future__ import annotations

import json
import math
import shutil
import sqlite3
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import replay_scores as R  # noqa: E402
import scan_features as SF  # noqa: E402
import score_diag716 as D  # noqa: E402
import score_stats as ST  # noqa: E402
from synth_db import build_full  # noqa: E402

REGISTRY = ROOT / "data" / "score_ranges.json"
Y2020 = ("2020-01-01", "2020-12-31")


@pytest.fixture(scope="module")
def world(tmp_path_factory) -> Path:
    c = tmp_path_factory.mktemp("diag") / "cache"
    build_full(c)
    assert SF.main(["--cache-dir", str(c), "--quiet", "--allow-short-warmup", "--warmup-days", "0"]) == 0
    assert R.main(["--cache-dir", str(c), "--out", str(c / "scores.db"), "--window", "30", "--quiet", "--limit-days", "30"]) == 0
    return c


def _report(db: Path, out: Path, pick=None) -> tuple[Path, dict]:
    """真實 score_stats 報告；`pick(g)` 為真的已觀測組放進 explain_716（None＝全部已觀測組）。"""
    rep = ST.run(db, REGISTRY, out, start=Y2020[0], end=Y2020[1])
    rep["summary"]["explain_716"] = [{**ST._k(g), "reasons": g["explain_716"] or ["測試指定"]}
                                     for g in rep["groups"] if g["n"] and (pick is None or pick(g))]
    out.write_text(json.dumps(rep, ensure_ascii=False), encoding="utf-8")
    return out, rep


def _diag(world: Path, report: Path, out: Path, db: Path | None = None, **kw):
    return D.run(report, db or world / "scores.db", out, cache_dir=world, start=Y2020[0], end=Y2020[1], quiet=True, **kw)


def _copy_db(world: Path, tmp_path: Path) -> Path:
    bad = tmp_path / "cache"
    shutil.copytree(world, bad)
    return bad


def _sql_n(db: Path, g: dict) -> int:
    con = sqlite3.connect(db)
    k, rw = g["line"], 1 if g["coverage"] == "reweighted" else 0
    n = con.execute(f"SELECT COUNT(*) FROM scores WHERE scope=? AND market=? AND horizon=? AND line_{k} IS NOT NULL "
                    f"AND line_{k}_reweighted=? AND date BETWEEN ? AND ?", (g["scope"], g["market"], g["horizon"], rw, *Y2020)).fetchone()[0]
    con.close()
    return n


# ---- 普查：全部已觀測組、每列都重算，parity 全過 ----

@pytest.fixture(scope="module")
def census(world, tmp_path_factory):
    d = tmp_path_factory.mktemp("census")
    rp, rep = _report(world / "scores.db", d / "r.json")
    res = _diag(world, rp, d / "diag.json", keep_samples=True)
    return rep, res, d


def test_census_covers_every_scope_and_line(census):
    rep, res, _ = census
    got = {(g["scope"], g["line"]) for g in res["groups"]}
    assert {("stock", k) for k in "23456"} <= got and ("market_index", "1") in got   # 個股四爻（二爻歷史）、上爻（大盤方向）都在
    assert len(res["groups"]) == len(rep["summary"]["explain_716"]) > 50


def test_census_parity_passes_on_every_row(census, world):
    """每組母體 ≤ 3000 → 普查；parity 列數＝各組以原始 SQL 另數的列數總和；差 0。"""
    rep, res, _ = census
    want = sum(_sql_n(world / "scores.db", g) for g in res["groups"])
    assert res["parity"]["rows"] == want > 1000
    assert res["parity"]["max_abs_diff"] <= D.PARITY_TOL


def test_pattern_counts_equal_db_counts(census, world):
    """族型態的估計列數加總＝原始 SQL 的該組列數；普查下每層權重皆 1、抽樣筆數＝母體。"""
    _, res, _ = census
    for g in res["groups"]:
        n = _sql_n(world / "scores.db", g)
        assert g["population"]["n"] == n
        assert math.isclose(sum(p["est_rows"] for p in g["patterns"]), n, rel_tol=0, abs_tol=1e-9)
        assert sum(sum(p["n_sample"].values()) for p in g["patterns"]) == n
        for s in D.STRATA:
            st = g["strata"][s]
            assert st["sampled"] == st["population"] and st["weight"] in (1.0, None)


def test_reweighted_rows_always_show_a_missing_piece(census):
    """`line_k_reweighted=1` ⇔ 該爻有族缺或族內有子指標缺：reweighted 組每一列的簽名都必須含「缺」、full 組都不得含。"""
    _, res, _ = census
    rows = res["_samples"]
    assert rows
    for s in rows:
        assert ("缺" in s["ana"]["signature"]) == (s["coverage"] == "reweighted"), s


def test_clip_capture_consistent_with_u(census):
    """攔截到的 S_clip 參數算出的 u 與計分函式自己回報的 `clipped` 一致（|u|>1 ⇔ clip；u>1 ⇔ 高分端）。"""
    _, res, _ = census
    n = 0
    for s in res["_samples"]:
        for sb in s["ana"]["subs"]:
            if sb["sclip_match"] != "direct":
                continue
            n += 1
            u = sb["u"]
            if abs(abs(u) - 1.0) < 1e-9:
                continue
            assert sb["clipped"] == (abs(u) > 1.0), sb
            if sb["clipped"]:
                assert sb["status"] == ("clip↑" if u > 0 else "clip↓"), sb
    assert n > 500


def test_effective_weights_sum_to_one(census):
    _, res, _ = census
    for s in res["_samples"]:
        subs = s["ana"]["subs"]
        if subs and "族下限生效" not in s["ana"]["signature"]:
            assert math.isclose(sum(sb["eff_weight"] for sb in subs), 1.0, abs_tol=1e-12), s["ana"]


def test_text_report_shape(census):
    _, _, d = census
    txt = (d / "diag.txt").read_text(encoding="utf-8")
    assert txt.startswith("§16.5 :716 族組成診斷") and "量測結論：" in txt and "parity：" in txt
    saved = json.loads((d / "diag.json").read_text(encoding="utf-8"))
    assert "_samples" not in saved and saved["seed"] == D.DEFAULT_SEED and saved["per_group"] == D.DEFAULT_PER_GROUP


# ---- 分層抽樣：權重還原母體 ----

def test_stratified_weights_restore_population(world, tmp_path):
    rp, _ = _report(world / "scores.db", tmp_path / "r.json")
    res = _diag(world, rp, tmp_path / "d.json", per_group=5, keep_samples=True)
    rows = res["_samples"]
    n_strat = 0
    for g in res["groups"]:
        N = g["population"]["n"]
        assert math.isclose(sum(p["est_rows"] for p in g["patterns"]), N, abs_tol=1e-9)
        assert sum(v["population"] for v in g["strata"].values()) == N
        if N > 5:
            assert sum(v["sampled"] for v in g["strata"].values()) == 5
        for s, v in g["strata"].items():
            if v["population"]:
                assert v["sampled"] >= 1                         # 有邊界列／單一值列的組一定抽得到
                assert v["weight"] == v["population"] / v["sampled"]
            else:
                assert v["sampled"] == 0 and v["weight"] is None
        if g["strata"]["boundary"]["population"] and g["strata"]["boundary"]["sampled"] < g["strata"]["boundary"]["population"]:
            n_strat += 1
        key = tuple(g[k] for k in ("scope", "market", "horizon", "line", "coverage"))
        mine = [s for s in rows if s["gkey"] == key]
        want = {"boundary_mode": (True, True), "boundary": (True, False), "mode": (False, True), "rest": (False, False)}
        assert all((s["is_b"], s["is_m"]) == want[s["stratum"]] for s in mine)
        # 型態內達邊界比例（加權）× 型態列數加總＝母體達邊界列數（四層切法下是恆等式，不是估計）
        assert math.isclose(sum(p["boundary_share_within"] * p["est_rows"] for p in g["patterns"]),
                            g["population"]["n_boundary"], abs_tol=1e-9)
        assert math.isclose(sum(p["mode_share_within"] * p["est_rows"] for p in g["patterns"]),
                            g["population"]["n_mode"], abs_tol=1e-9)
    assert n_strat >= 1                                          # 至少有一組真的在邊界層做了抽樣（非普查）


def _al(bm, b, m, r, total):
    return D.allocate({"boundary_mode": bm, "boundary": b, "mode": m, "rest": r}, total)


def test_allocate_hand_values():
    assert _al(100, 100, 100, 100, 40) == {"boundary_mode": 10, "boundary": 10, "mode": 10, "rest": 10}
    assert _al(0, 2, 0, 100, 40) == {"boundary_mode": 0, "boundary": 2, "mode": 0, "rest": 38}
    # R 只有 3：BM、B、M 各 10 → R 3 → 剩 7 依 BM→B→M 回補（BM 只剩 0 可補、B 補 7）
    assert _al(10, 100, 50, 3, 40) == {"boundary_mode": 10, "boundary": 17, "mode": 10, "rest": 3}
    assert _al(1, 1, 1, 1, 40) == {"boundary_mode": 1, "boundary": 1, "mode": 1, "rest": 1}
    with pytest.raises(D.DiagError):
        _al(1, 1, 1, 1, 3)


def test_same_seed_same_report(world, tmp_path):
    rp, _ = _report(world / "scores.db", tmp_path / "r.json")
    a = _diag(world, rp, tmp_path / "a.json", per_group=5)
    b = _diag(world, rp, tmp_path / "b.json", per_group=5)
    for x in (a, b):
        x.pop("elapsed_s"), x.pop("rss_peak_mib")
    a.pop("db"), b.pop("db")
    assert json.dumps(a, sort_keys=True, ensure_ascii=False) == json.dumps(b, sort_keys=True, ensure_ascii=False)


def test_empty_explain_writes_empty_report(world, tmp_path):
    rp, _ = _report(world / "scores.db", tmp_path / "r.json", pick=lambda g: False)
    res = _diag(world, rp, tmp_path / "d.json")
    assert res["groups"] == [] and res["parity"]["rows"] == 0 and (tmp_path / "d.txt").exists()


# ---- parity 守門：必須會紅、且紅得對 ----

def _tamper_first_row(db: Path, g: dict, *, col_delta: float | None = None, set_rw: int | None = None) -> tuple[str, str]:
    con = sqlite3.connect(db)
    k, rw = g["line"], 1 if g["coverage"] == "reweighted" else 0
    sid, d = con.execute(f"SELECT stock_id, date FROM scores WHERE scope=? AND market=? AND horizon=? AND line_{k} IS NOT NULL "
                         f"AND line_{k}_reweighted=? ORDER BY date, stock_id LIMIT 1",
                         (g["scope"], g["market"], g["horizon"], rw)).fetchone()
    if col_delta is not None:
        con.execute(f"UPDATE scores SET line_{k}=line_{k}+? WHERE stock_id=? AND date=? AND market=? AND horizon=?",
                    (col_delta, sid, d, g["market"], g["horizon"]))
    if set_rw is not None:
        con.execute(f"UPDATE scores SET line_{k}_reweighted=? WHERE stock_id=? AND date=? AND market=? AND horizon=?",
                    (set_rw, sid, d, g["market"], g["horizon"]))
    con.commit()
    con.close()
    return sid, d


TARGET = {"scope": "stock", "market": "twse", "horizon": "short", "line": "4", "coverage": "reweighted"}


def _is(g, t):
    return all(g[k] == v for k, v in t.items())


def test_tampered_score_gives_parity_error(world, tmp_path):
    """db 某列分數被改 0.5（報告由被改的 db 重產，所以報告與 db 自洽、只有重算抓得到）→ ParityError，指名那一列。"""
    c = _copy_db(world, tmp_path)
    sid, d = _tamper_first_row(c / "scores.db", TARGET, col_delta=0.5)
    rp, _ = _report(c / "scores.db", tmp_path / "r.json", pick=lambda g: _is(g, TARGET))
    with pytest.raises(D.ParityError, match="parity 不符 1／") as ei:
        _diag(c, rp, tmp_path / "d.json", db=c / "scores.db")
    assert f"{sid} {d}" in str(ei.value) and "|差|=5.000e-01" in str(ei.value)
    assert not (tmp_path / "d.json").exists() and not (tmp_path / "d.txt").exists()


def test_tampered_score_cli_rc2(world, tmp_path, capsys, monkeypatch):
    """走 `main()`：rc=2、stderr 指出 parity、不留報告檔。**必須讓 main 真的走到 parity**：CLI 用寫死的 2021～2024，
    合成資料在 2020 年，不改樣本段的話 rc=2 會來自「報告樣本段不符」或「母體 0 列 ≠ 報告」，拿掉 parity 照樣綠
    （本批自測突變 M3 抓到的正是這個）。故只在本測試把模組常數換成 2020，並比對 stderr 是 parity 訊息。"""
    c = _copy_db(world, tmp_path)
    _tamper_first_row(c / "scores.db", TARGET, col_delta=1e-6)                       # 遠小於報告精度、仍遠大於 1e-9
    rp, _ = _report(c / "scores.db", tmp_path / "r.json", pick=lambda g: _is(g, TARGET))
    monkeypatch.setattr(D, "SAMPLE_START", Y2020[0])
    monkeypatch.setattr(D, "SAMPLE_END", Y2020[1])
    rc = D.main(["--report", str(rp), "--db", str(c / "scores.db"), "--cache-dir", str(c), "--out", str(tmp_path / "d.json"), "--quiet"])
    err = capsys.readouterr().err
    assert rc == 2 and "score_diag716 中止" in err and "ParityError" in err and "parity 不符 1／" in err
    assert not (tmp_path / "d.json").exists()


def test_line4_history_is_recomputed_and_matches_db(census, world):
    """個股四爻（短線／波段）用的 T−9…T−1 二爻歷史：由重算得來，逐值等於 db 中該檔前 9 個被計分日的 line_2。
    （合成資料裡歷史恰好不影響四爻分數——序 1 情境沒觸發——所以 parity 抓不到歷史錯，要在這裡直接驗。）"""
    _, res, _ = census
    rows = [s for s in res["_samples"] if s["scope"] == "stock" and s["line"] == "4" and s["horizon"] in ("short", "swing")]
    assert len(rows) > 20
    con = sqlite3.connect(world / "scores.db")
    n_full = 0
    for s in rows:
        prev = con.execute("SELECT date, line_2 FROM scores WHERE stock_id=? AND horizon=? AND date<? ORDER BY date DESC LIMIT 9",
                           (s["sid"], s["horizon"], s["date"])).fetchall()[::-1]
        assert s["hist"] == [v for _, v in prev], s
        n_full += len(prev) == 9
    con.close()
    assert n_full > 5


def test_market_direction_reaches_stock_line6(world, tmp_path, monkeypatch):
    """個股上爻族 A 吃「當日大盤方向分數」。合成資料的大盤方向恆為缺（六爻未齊，db 的 base_score 全 NULL），
    parity 驗不到這條管線，所以直接驗：①普通跑時餵進個股的方向＝db 同日大盤列 base_score（皆 None）；
    ②把 `score_market` 的方向換成 42.0 → 個股上爻的輸入必須跟著變成 42.0，且重算分數與 db 不符 → ParityError。"""
    rp, _ = _report(world / "scores.db", tmp_path / "r.json", pick=lambda g: g["scope"] == "stock" and g["line"] == "6")
    res = _diag(world, rp, tmp_path / "a.json", keep_samples=True)
    con = sqlite3.connect(world / "scores.db")
    for s in res["_samples"]:
        (bs,) = con.execute("SELECT base_score FROM scores WHERE scope='market_index' AND market=? AND horizon=? AND date=?",
                            (s["market"], s["horizon"], s["date"])).fetchone()
        assert s["mdir"] == bs
    con.close()
    import dataclasses
    orig = D.MKT.score_market
    monkeypatch.setattr(D.MKT, "score_market", lambda *a, **k: dataclasses.replace(orig(*a, **k), direction_score=42.0))
    with pytest.raises(D.ParityError, match="爻6"):
        _diag(world, rp, tmp_path / "b.json")


def test_tampered_reweighted_flag_gives_parity_error(world, tmp_path):
    """把一列 full 改標 reweighted：它被歸進 reweighted 組，分數本身沒改，重算的 reweighted 旗標不符 → ParityError。"""
    c = _copy_db(world, tmp_path)
    full = {**TARGET, "coverage": "full"}
    sid, d = _tamper_first_row(c / "scores.db", full, set_rw=1)
    rp, _ = _report(c / "scores.db", tmp_path / "r.json", pick=lambda g: _is(g, TARGET))
    with pytest.raises(D.ParityError) as ei:
        _diag(c, rp, tmp_path / "d.json", db=c / "scores.db")
    assert "reweighted 重算 0 ≠ db 1" in str(ei.value) and f"{sid} {d}" in str(ei.value)


# ---- 其餘守門 ----

def test_report_db_mismatch_aborts(world, tmp_path):
    rp, rep = _report(world / "scores.db", tmp_path / "r.json", pick=lambda g: _is(g, TARGET))
    for g in rep["groups"]:
        if _is(g, TARGET):
            g["mode_share"] = g["mode_share"] + 0.01
    rp.write_text(json.dumps(rep, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(D.DiagError, match="mode_share.*報告與 db 不一致"):
        _diag(world, rp, tmp_path / "d.json")


def test_report_params_sha_mismatch_aborts(world, tmp_path):
    rp, rep = _report(world / "scores.db", tmp_path / "r.json", pick=lambda g: _is(g, TARGET))
    rep["params_sha"] = "000000000000"
    rp.write_text(json.dumps(rep, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(D.DiagError, match="報告 params_sha"):
        _diag(world, rp, tmp_path / "d.json")


def test_db_not_from_current_code_aborts(world, tmp_path):
    """db 的 params_sha 被竄改 → `export_dataset.check_params` 擋下（訊息比對，不是別的守門先炸）。"""
    c = _copy_db(world, tmp_path)
    rp, _ = _report(c / "scores.db", tmp_path / "r.json", pick=lambda g: _is(g, TARGET))
    con = sqlite3.connect(c / "scores.db")
    con.execute("UPDATE replay_meta SET params_sha='000000000000'")
    con.commit()
    con.close()
    with pytest.raises(Exception, match="replay_meta 不自洽"):
        _diag(c, rp, tmp_path / "d.json", db=c / "scores.db")


def test_registry_model_version_mismatch_aborts(world, tmp_path):
    rp, _ = _report(world / "scores.db", tmp_path / "r.json", pick=lambda g: _is(g, TARGET))
    reg = json.loads(REGISTRY.read_text(encoding="utf-8"))
    reg["markets"]["twse"]["model_version"] = "p2-score-engine-1.000000000000"
    p = tmp_path / "reg.json"
    p.write_text(json.dumps(reg), encoding="utf-8")
    with pytest.raises(ST.StatsError, match="model_version"):
        _diag(world, rp, tmp_path / "d.json", registry=p)


def test_old_report_without_mode_rejected(world, tmp_path):
    rp, rep = _report(world / "scores.db", tmp_path / "r.json", pick=lambda g: _is(g, TARGET))
    del rep["mode_share_max"]
    rp.write_text(json.dumps(rep, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(D.DiagError, match="舊版 score_stats"):
        _diag(world, rp, tmp_path / "d.json")


def test_sample_segment_is_hardcoded(world, tmp_path):
    """CLI 不開日期；報告樣本段（合成 2020）≠ 寫死段 → 中止。"""
    assert (D.SAMPLE_START, D.SAMPLE_END) == ("2021-01-01", "2024-12-31")
    with pytest.raises(SystemExit):
        D.main(["--report", "x", "--out", "y", "--start", "2020-01-01"])
    rp, _ = _report(world / "scores.db", tmp_path / "r.json", pick=lambda g: _is(g, TARGET))
    with pytest.raises(D.DiagError, match="報告樣本段"):
        D.run(rp, world / "scores.db", tmp_path / "d.json", cache_dir=world, quiet=True)
