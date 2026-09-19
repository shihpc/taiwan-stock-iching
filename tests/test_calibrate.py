"""P3 第 3 項第 1 步：子指標原始值 x 的出口（`replay_scores.py --dump-x`／`iching.xdump`）＋ `scripts/calibrate_d.py`。

守的事（`docs/P3-CALIBRATION.md` §2 第 1 步驗收）：(a) 開／關 dump 的 `scores.db` 與快照逐位相同；(b) dump 筆數＝該區間
`SubResult` 非缺值筆數（另走一次計分路徑自己數）；(c) 抽鍵 x 逐位＝`SubResult.x`（含 `basis` 的 x − 滾動 c）；
(d) `calibrate_d` 的 p85 與 numpy 直算相同、四類分類各至少一鍵、閘門清單正確、距離型合併 p85 正確；
(e) manifest `params_sha` 不符 rc 2（另：目錄缺／檔長不符 rc 2）；`--dump-only` 不寫 db、f32 逐位同一般跑；參數互斥 rc 2；
`hetzner_calib.sh` 結構。
"""
from __future__ import annotations

import json
import math
import shutil
import sys
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import calibrate_d as CD  # noqa: E402
import replay_scores as R  # noqa: E402
import scan_features as SF  # noqa: E402
from iching import replay_io as RIO  # noqa: E402
from iching import replay_state as RS  # noqa: E402
from iching import replay_step as ST  # noqa: E402
from iching import xdump as XD  # noqa: E402
from iching.run_common import TEXT_VERSION  # noqa: E402
from iching.score.aggregate import FamilyResult, LineResult, SubResult  # noqa: E402
from iching.score.market import MarketScores  # noqa: E402
from iching.score.params import MARKETS, SCOPE_MARKET, SCOPE_STOCK, build_params  # noqa: E402
from iching.scores_io import ScoreStore  # noqa: E402
from synth_db import DAYS, DV, build_full  # noqa: E402

WINDOW = "30"
D_FROM, D_TO = DAYS[20], DAYS[50]          # 區間內 31 個交易日；重播仍從 DAYS[0] 起算（狀態鏈不可跳日）
KEY_DIST = "stock__twse__short__2__A__dist_ma_short"
KEY_PERS = "stock__twse__short__5__C__foreign_persistence"
KEY_CAL = "stock__twse__short__3__A__excess_long"
KEY_NA = "market_index__twse__short__2__A__above_ma_short_ratio"
KEY_MKT = "market_index__twse__short__4__C__foreign_buy_days"


@pytest.fixture(scope="module")
def cache(tmp_path_factory) -> Path:
    c = tmp_path_factory.mktemp("calib") / "cache"
    build_full(c)
    assert SF.main(["--cache-dir", str(c), "--quiet", "--allow-short-warmup", "--warmup-days", "0"]) == 0
    return c


@pytest.fixture(scope="module")
def world(cache, tmp_path_factory) -> dict:
    """A＝不 dump；B＝dump（同一份合成 DB、同 window）。"""
    w = tmp_path_factory.mktemp("w")
    a, b, xd = w / "a.db", w / "b.db", w / "xd"
    assert R.main(["--cache-dir", str(cache), "--out", str(a), "--window", WINDOW, "--quiet"]) == 0
    assert R.main(["--cache-dir", str(cache), "--out", str(b), "--window", WINDOW, "--quiet",
                   "--dump-x", str(xd), "--dump-from", D_FROM, "--dump-to", D_TO]) == 0
    return {"a": a, "b": b, "xd": xd, "manifest": json.loads((xd / XD.MANIFEST).read_text(encoding="utf-8"))}


def _all(db: Path):
    with ScoreStore(db, readonly=True) as s:
        return {d: s.rows_for_day(DV, d) for d in s.dates(DV)}


# (a) 開／關 dump 分數與快照逐位相同
def test_dump_does_not_change_scores(world):
    assert _all(world["a"]) == _all(world["b"])
    assert Path(str(world["a"]) + ".state.json").read_text(encoding="utf-8") == Path(str(world["b"]) + ".state.json").read_text(encoding="utf-8")
    m = world["manifest"]
    assert m["schema"] == XD.SCHEMA and m["dump_from"] == D_FROM and m["dump_to"] == D_TO and m["days_dumped"] == 31
    assert m["data_version"] == DV and m["unknown_keys"] == {}
    with ScoreStore(world["b"], readonly=True) as s:
        assert m["params_sha"] == s.params_sha_of(DV)          # 與 replay_meta 同一支指紋
    assert len(m["keys"]) == sum(len(build_params(mk).params) for mk in MARKETS)
    assert all((world["xd"] / v["file"]).stat().st_size == 4 * v["n"] for v in m["keys"].values() if v["n"])


# (b)(c) 另走一次計分路徑：逐鍵筆數／跳過數＝manifest；抽鍵 x 逐位＝SubResult.x（float32）
def test_dump_counts_and_values_match_independent_walk(cache, world):
    counts: dict[str, int] = {}
    skipped: dict[str, int] = {}
    vals: dict[str, list[float]] = {KEY_DIST: [], KEY_PERS: [], KEY_MKT: []}

    def collect(sc):
        scope = SCOPE_STOCK if hasattr(sc, "stock_id") else SCOPE_MARKET
        for line_id, lr in sc.lines.items():
            for fr in lr.families:
                for sub in fr.subs:
                    k = XD.key_name(scope, sc.market, sc.horizon, line_id, fr.family, sub.indicator_id)
                    if sub.x is None or (isinstance(sub.x, float) and math.isnan(sub.x)):
                        skipped[k] = skipped.get(k, 0) + 1
                        continue
                    counts[k] = counts.get(k, 0) + 1
                    if k in vals:
                        vals[k].append(float(sub.x))

    src = RIO.ReplaySource(cache, None, window=int(WINDOW))
    try:
        dates = src.trading_dates()
        ps = {m: build_params(m) for m in MARKETS}
        mv = {m: ps[m].model_version() for m in MARKETS}
        cross = RS.CrossDayState()
        wc = RS.WindowCache(src.pool, src.factors, window=int(WINDOW))
        provider = src.load_fundamentals(dates).provider()
        for T in dates:
            wc.ingest(src.read_day(T))
            ST.step(T, wc, cross, ps, data_version=DV, text_version=TEXT_VERSION, model_version=mv, fundamentals=provider,
                    on_scores=collect if D_FROM <= T <= D_TO else None)
    finally:
        src.close()
    m = world["manifest"]
    assert sum(counts.values()) == m["n_values"] > 0 and sum(skipped.values()) == m["n_skipped"] > 0
    for k, v in m["keys"].items():
        assert v["n"] == counts.get(k, 0) and v["skipped"] == skipped.get(k, 0), k
    for k, xs in vals.items():
        assert len(xs) == m["keys"][k]["n"] > 0
        got = np.fromfile(world["xd"] / f"{k}.f32", dtype="<f4")
        assert got.tobytes() == np.asarray(xs, dtype="<f4").tobytes(), k


# (c′) basis 的 x 是 c 每日滾動的 60 日中位數：檔寫 x − c_rolling_median；x 為 None／NaN 跳過；不在 ParamSet 的鍵計 unknown
def test_xdump_rolling_c_and_skips(tmp_path):
    ps = {m: build_params(m) for m in MARKETS}
    d = XD.XDump(tmp_path / "x", ps, dump_from="2021-01-04", dump_to="2021-01-05", data_version=DV, params_sha="abc", params={})
    k_basis = XD.key_name(SCOPE_MARKET, "twse", "short", "5", "B", "basis")
    assert d.snap[k_basis]["x_kind"] == XD.X_KIND_ROLLING and d.snap[k_basis]["c"] is None
    assert all(v["x_kind"] == XD.X_KIND_PLAIN for k, v in d.snap.items() if k != k_basis and not k.endswith("__basis"))

    def mk(subs, day):
        fam = FamilyResult("B", 50.0, tuple(subs))
        line = LineResult("5", 50.0, 1.0, False, (fam,), {"B": 1.0}, False)
        return MarketScores("twse", "short", day, {"5": line}, 50.0, 50.0, 50.0, "full")

    sr = lambda x, meta=None, iid="basis": SubResult(iid, 50.0, 50.0, x, False, None, 1.0, meta or {})  # noqa: E731
    d.on_scores(mk([sr(1.25, {"c_rolling_median": 0.25}), sr(float("nan")), sr(None), sr(0.5)], "2021-01-04"))
    d.on_scores(mk([sr(-0.5, {"c_rolling_median": 0.5}), sr(3.0, {"c_rolling_median": 1.0}, iid="not_a_param")], "2021-01-05"))
    d.on_scores(mk([sr(9.0, {"c_rolling_median": 0.0})], "2021-01-06"))          # 區間外：不寫
    d.finish()
    got = np.fromfile(tmp_path / "x" / f"{k_basis}.f32", dtype="<f4").tolist()
    assert got == [1.0, -1.0]                                                    # 1.25−0.25、−0.5−0.5
    m = XD.load_manifest(tmp_path / "x")
    assert m["keys"][k_basis]["n"] == 2 and m["keys"][k_basis]["skipped"] == 3      # NaN、None、無 c_rolling_median
    assert m["keys"][k_basis]["date_min"] == "2021-01-04" and m["keys"][k_basis]["date_max"] == "2021-01-05"
    assert m["days_dumped"] == 2 and m["unknown_keys"] == {XD.key_name(SCOPE_MARKET, "twse", "short", "5", "B", "not_a_param"): 1}
    # 非空目錄拒開；dump_to < dump_from 拒
    with pytest.raises(XD.XDumpError, match="不是空的"):
        XD.XDump(tmp_path / "x", ps, dump_from="2021-01-04", dump_to="2021-01-05", data_version=DV, params_sha="abc", params={})
    with pytest.raises(XD.XDumpError, match="早於"):
        XD.XDump(tmp_path / "y", ps, dump_from="2021-01-05", dump_to="2021-01-04", data_version=DV, params_sha="abc", params={})


# (d) calibrate_d：p85 與 numpy 直算相同、分類各至少一鍵、閘門清單、距離型合併 p85、持續性族 d_formula
def test_calibrate_report(world, tmp_path, capsys):
    out = tmp_path / "rep"
    assert CD.main(["--dump-dir", str(world["xd"]), "--out-dir", str(out), "--quiet"]) == 0
    text = capsys.readouterr().out
    assert "報告：" in text and "閘門超標" in text
    jp, tp = out / f"d_report_{D_TO}.json", out / f"d_report_{D_TO}.txt"
    rep = json.loads(jp.read_text(encoding="utf-8"))
    txt = tp.read_text(encoding="utf-8")
    assert rep["method"] == "linear" and rep["percentile"] == 85.0 and rep["gate_pct"] == 15.0 and rep["params_sha"] == world["manifest"]["params_sha"]
    rows = {r["key"]: r for r in rep["rows"]}
    assert len(rows) == len(world["manifest"]["keys"])
    assert rows[KEY_DIST]["category"] == "distance" and rows[KEY_DIST]["shared_d_table"] == "distance_d" and rows[KEY_DIST]["shared_d_n"] == 5
    assert rows[KEY_PERS]["category"] == "persistence" and rows[KEY_PERS]["d_formula"] == pytest.approx(2.5 / 3) and rows[KEY_PERS]["d_old"] == 1.0
    assert rows[KEY_PERS]["d_eff"] == 1.0 and rows[KEY_PERS]["d_formula_matches_old"] is False
    assert rows[KEY_CAL]["category"] == "calibrate" and rows[KEY_CAL]["shared_d_table"] is None
    assert rows[KEY_NA]["category"] == "not_applicable" and rows[KEY_NA]["p85"] is None and rows[KEY_NA]["n"] > 0
    assert rows[KEY_MKT]["category"] == "persistence" and rows[KEY_MKT]["range_upper"] == 5.0
    assert rows["market_index__twse__short__5__B__basis"]["c"] == 0.0 and rows["market_index__twse__short__5__B__basis"]["x_kind"] == XD.X_KIND_ROLLING
    assert {r["category"] for r in rep["rows"] if r["n"]} >= {"calibrate", "distance", "persistence", "not_applicable"}
    assert set(rep["counts"]) == {"calibrate", "distance", "persistence", "not_applicable"}
    # p85／d_new／截斷比例／中位數：對每個有樣本的鍵用 numpy 直算
    n_checked = 0
    exp_gate, exp_strict, exp_deg = [], [], []
    for k, r in rows.items():
        if r["n"] == 0 or r["category"] == "not_applicable":
            assert r["p85"] is None and r["gate_fail"] is False
            continue
        x = np.fromfile(world["xd"] / f"{k}.f32", dtype="<f4").astype(np.float64)
        dev = np.abs(x - r["c"])
        p85 = float(np.percentile(dev, 85, method="linear"))
        assert r["p85"] == p85 and r["d_new"] == p85 / 3.0 and r["median_x"] == float(np.median(x))
        assert r["clip_old_pct"] == float(np.mean(dev > 3 * r["d_old"]) * 100)
        assert r["median_flag"] == (abs(r["median_x"] - r["c"]) > r["d_new"])
        if p85 > 0:
            assert r["clip_new_pct"] == float(np.mean(dev > 3 * r["d_new"]) * 100)
        else:
            exp_deg.append(k)
        if r["category"] == "calibrate":
            d_eff = None if p85 <= 0 else r["d_new"]
        elif r["category"] == "distance":
            adopt = abs(r["d_new"] - r["d_old"]) / r["d_old"] > 0.25
            assert r["adopt_p85"] is adopt
            d_eff = (None if p85 <= 0 else r["d_new"]) if adopt else r["d_old"]
        else:
            d_eff = r["d_old"]
        assert r["d_eff"] == d_eff
        if d_eff is not None:
            ce = float(np.mean(dev > 3 * d_eff) * 100)
            assert r["clip_eff_pct"] == ce
            if ce > 15.0 + 100.0 / x.size:
                exp_gate.append(k)
            if ce > 15.0:
                exp_strict.append(k)
        n_checked += 1
    assert n_checked > 50
    assert rep["gate_failures"] == exp_gate and rep["gate_failures_strict"] == exp_strict and rep["degenerate"] == exp_deg
    assert exp_strict and set(exp_gate) <= set(exp_strict)               # 合成世界 n≈31 時嚴格版必有（離散化），餘裕版是其子集
    assert rep["median_flags"] == [k for k, r in rows.items() if r.get("median_flag")]
    # 距離型合併：twse × n=5 的 all＝大盤 dist_ma_short（short）＋SPX（無樣本則不計）＋個股 dist_ma_short（short）
    pooled = {(r["market"], r["scope"], r["n"]): r for r in rep["distance_pooled"]}
    parts = [np.abs(np.fromfile(world["xd"] / f"{k}.f32", dtype="<f4").astype(np.float64) - r["c"])
             for k, r in rows.items() if r["category"] == "distance" and r["market"] == "twse" and r["shared_d_n"] == 5 and r["n"]]
    assert len(parts) >= 2
    dev = np.concatenate(parts)
    p = pooled[("twse", "all", 5)]
    assert p["p85"] == float(np.percentile(dev, 85)) and p["d_table"] == 0.6 and p["n_keys"] == len(parts) and p["n_samples"] == dev.size
    assert p["adopt_p85"] is (abs(p["d_new"] - 0.6) / 0.6 > 0.25)
    # 人讀表：四段都在、閘門清單與 JSON 一致
    for h in ("## 閘門", "## 嚴格版", "## 退化", "## |median − c| > d_new", "## 距離型查表覆核", "## 持續性族"):
        assert h in txt
    for k in exp_gate:
        assert f"  {k}\n" in txt
    assert "B2.5 族 C" in txt and "B1.4 族 C" in txt
    # --gate 0：所有有生效 d 的鍵都超標（清單機制真的在動）
    out2 = tmp_path / "rep2"
    assert CD.main(["--dump-dir", str(world["xd"]), "--out-dir", str(out2), "--quiet", "--gate", "0", "--tag", "g0"]) == 0
    rep2 = json.loads((out2 / "d_report_g0.json").read_text(encoding="utf-8"))
    assert rep2["gate_failures"] == [r["key"] for r in rep2["rows"] if r["clip_eff_pct"] is not None and r["clip_eff_pct"] > 100.0 / r["n"]]
    assert len(rep2["gate_failures"]) > len(exp_gate)
    capsys.readouterr()


# (e) params_sha 不符 rc 2；目錄缺 rc 2；f32 檔長與 manifest 不符 rc 2；均不吐 traceback
def test_calibrate_refuses_bad_dump(world, tmp_path, capsys):
    assert CD.main(["--dump-dir", str(tmp_path / "nope"), "--out-dir", str(tmp_path / "o")]) == 2
    assert "不存在" in capsys.readouterr().err
    bad = tmp_path / "bad"
    shutil.copytree(world["xd"], bad)
    mp = bad / XD.MANIFEST
    m = json.loads(mp.read_text(encoding="utf-8"))
    m["params_sha"] = "000000000000"
    mp.write_text(json.dumps(m), encoding="utf-8")
    assert CD.main(["--dump-dir", str(bad), "--out-dir", str(tmp_path / "o")]) == 2
    err = capsys.readouterr().err
    assert "params_sha=000000000000 ≠ 現行碼指紋" in err and "Traceback" not in err
    assert not (tmp_path / "o").exists()
    # 同一份 params 但 window 改了 → 現行碼指紋不同 → rc 2（守的是「這份 dump 是現行碼＋同設定算的」）
    m2 = json.loads((world["xd"] / XD.MANIFEST).read_text(encoding="utf-8"))
    m2["params"]["window"] = 31
    mp.write_text(json.dumps(m2), encoding="utf-8")
    assert CD.main(["--dump-dir", str(bad), "--out-dir", str(tmp_path / "o")]) == 2
    capsys.readouterr()
    # 檔長不符
    mp.write_text(json.dumps(m2 | {"params": world["manifest"]["params"]}), encoding="utf-8")
    f = bad / f"{KEY_CAL}.f32"
    f.write_bytes(f.read_bytes()[:-4])
    assert CD.main(["--dump-dir", str(bad), "--out-dir", str(tmp_path / "o")]) == 2
    assert "≠ manifest n=" in capsys.readouterr().err


# --dump-only：不寫 scores.db／快照；f32 與一般跑逐位相同；--to 未給取 --dump-to（只算到區間末）；互斥組合 rc 2
def test_dump_only(cache, world, tmp_path, capsys):
    xd2 = tmp_path / "xd2"
    never = tmp_path / "never.db"
    assert R.main(["--cache-dir", str(cache), "--out", str(never), "--window", WINDOW, "--dump-only",
                   "--dump-x", str(xd2), "--dump-from", D_FROM, "--dump-to", D_TO]) == 0
    text = capsys.readouterr().out
    assert "出檔＝無（--dump-only）" in text and "計分 51 日" in text and "全部計分（--dump-only 不落地）" in text
    assert not never.exists() and not Path(str(never) + ".state.json").exists()
    files = sorted(p.name for p in world["xd"].glob("*.f32"))
    assert files == sorted(p.name for p in xd2.glob("*.f32")) and files
    assert all((world["xd"] / n).read_bytes() == (xd2 / n).read_bytes() for n in files)
    m2 = json.loads((xd2 / XD.MANIFEST).read_text(encoding="utf-8"))
    m1 = world["manifest"]
    assert {k: (v["n"], v["skipped"]) for k, v in m2["keys"].items()} == {k: (v["n"], v["skipped"]) for k, v in m1["keys"].items()}
    assert m2["params_sha"] == m1["params_sha"]
    # 互斥／缺參數
    for extra in (["--dump-only"], ["--dump-only", "--dump-x", str(tmp_path / "x3"), "--resume"],
                  ["--dump-only", "--dump-x", str(tmp_path / "x3"), "--rebuild"],
                  ["--dump-x", str(tmp_path / "x3"), "--dump-from", D_TO, "--dump-to", D_FROM]):
        assert R.main(["--cache-dir", str(cache), "--out", str(tmp_path / "z.db"), "--window", WINDOW, *extra]) == 2
        err = capsys.readouterr().err
        assert "[replay 中止]" in err and "Traceback" not in err
    # 非空 dump 目錄拒（rc 2，不吐 traceback），且不寫 db
    assert R.main(["--cache-dir", str(cache), "--out", str(tmp_path / "z.db"), "--window", WINDOW, "--dump-x", str(xd2),
                   "--dump-from", D_FROM, "--dump-to", D_TO]) == 2
    err = capsys.readouterr().err
    assert "不是空的" in err and "Traceback" not in err


# Hetzner 一句話貼：骨架同 hetzner_adj.sh（自我複製 exec／pull 後 re-exec／dirty 拒跑，參數化測試在 tests/test_pit_world.py），
# 這裡守步驟指令：守門（scores.db params_sha＝現行碼、DUMP_TO ≤ db 末日）→ replay --dump-only → calibrate_d → commit runs/calib
def test_hetzner_calib_sh_structure():
    text = (ROOT / "scripts" / "hetzner_calib.sh").read_text(encoding="utf-8")
    assert "check_params" in text and "cache/scores.db" in text
    assert ('python3 scripts/replay_scores.py --cache-dir cache --window "$WINDOW" --dump-only --dump-x "$XDUMP" '
            '--dump-from "$DUMP_FROM" --dump-to "$DUMP_TO"') in text
    assert 'python3 scripts/calibrate_d.py --dump-dir "$XDUMP" --out-dir runs/calib --tag "$DUMP_TO"' in text
    assert "mkdir -p runs/calib" in text and text.index("git checkout -q main") < text.index("mkdir -p runs/calib")
    assert "git add runs/calib" in text and 'BR="hetzner/calib-${DUMP_TO}"' in text
    code = "\n".join(ln for ln in text.splitlines() if not ln.lstrip().startswith("#"))
    assert "--rebuild" not in code and "--resume" not in code               # 只重算、不動 scores.db（註解裡的說明不算）
    assert "HETZNER_ADJ" not in text and "HETZNER_PIT" not in text and "HETZNER_DS" not in text
    assert "cache/logs/calib-" in text
