"""P4-L4 T0 校準：scripts/calib_t0.py（純計算）＋scripts/export_t0_input.py（合成 scores.db 端到端）（docs/P4-PREVIEW.md §27）。"""
from __future__ import annotations

import gzip
import importlib.util
import json
import sys
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))


def _load(name: str, rel: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / rel)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


CT = _load("calib_t0_t", "scripts/calib_t0.py")
from iching import pick as P  # noqa: E402

TR = CT.TRAIN


def test_train_matches_export_dataset_segments():
    import export_dataset as EXP
    assert CT.TRAIN == EXP.SEGMENTS["train"] == ("2021-01-01", "2023-06-30")


def _days(n):
    import datetime as dt
    out, d = [], dt.date(2021, 1, 4)
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d.isoformat())
        d += dt.timedelta(days=1)
    return out


def make_doc(n_days=40, n_stocks=60, state_of=lambda i, m, h: "S1", seed=0, short_none=False):
    rng = np.random.default_rng(seed)
    days = _days(n_days)
    states, rows = {}, {}
    for i, d in enumerate(days):
        states[d] = {f"{m}|{h}": state_of(i, m, h) for m in P.MARKETS for h in P.HORIZONS}
        rows[d] = {}
        for m in P.MARKETS:
            for h in P.HORIZONS:
                bs = rng.uniform(20, 80, n_stocks)
                sr = rng.uniform(20, 80, n_stocks)
                rows[d][f"{m}|{h}"] = [[float(bs[k]), None if short_none else float(sr[k])] for k in range(n_stocks)]
    return {"schema": 1, "params_sha": "abc", "data_version": "dv", "model_version": {"twse": "a", "tpex": "b"},
            "train": list(TR), "days": days, "states": states, "rows": rows, "row_cols": ["base_score", "short_raw"]}


def test_q_star_is_maximal_and_feasible():
    doc = make_doc()
    res = CT.calibrate(doc)
    q = res["q_star"]
    assert q is not None and 0 < q < 99.99
    cells = CT.build_cells(doc)
    assert CT.feasible(cells, q) and not CT.feasible(cells, q + 0.01)
    assert res["next_feasible"] is False
    # 單調：q* 以下抽查皆可行
    assert all(CT.feasible(cells, x) for x in (0.0, q / 2, q - 0.01))
    # 48 格齊全
    assert len(res["cells"]) == 48
    # S1 做多 N0=20 是 60 檔裡最嚴的格（做空 S1 只要 5）→ q* 約略落在 100×(1−20/60) 附近
    assert 55 < q < 72


@pytest.mark.parametrize("seed", [3, 11])
def test_brute_force_matches_binary_search(seed):
    """完整掃描全部 10,000 個格點：可行集＝[0, q*] 連續區間且最大值＝二分結果。"""
    doc = make_doc(n_days=6, n_stocks=24, seed=seed, state_of=lambda i, m, h: ("S1", "S4")[i % 2])
    cells = CT.build_cells(doc)
    q = CT.find_q_star(cells)
    feas = [i for i in range(0, 10000) if CT.feasible(cells, i / 100)]
    assert feas and feas == list(range(0, feas[-1] + 1))
    assert feas[-1] / 100 == q


def test_thresholds_shift_and_clamp():
    doc = make_doc()
    res = CT.calibrate(doc)
    q = res["q_star"]
    e = res["cells"]["twse|short|S1|long"]
    assert set(e["thresholds"]) == {"0", "5", "10", "15", "20"}
    th = [e["thresholds"][k] for k in ("0", "5", "10", "15", "20")]
    assert th == sorted(th)
    assert e["q_used"]["20"] == max(q, min(q + 20, 99.0)) and e["q_used"]["0"] == q
    if q + 20 > 99:
        assert e["q_used"]["20"] == 99.0
    pooled = np.sort(np.concatenate([[r[0] for r in doc["rows"][d]["twse|short"]] for d in doc["days"]]))
    assert e["thresholds"]["0"] == pytest.approx(float(np.percentile(pooled, q, method="linear")))


def test_short_cells_use_short_raw():
    doc = make_doc(seed=5)
    for d in doc["days"]:
        for k, rs in doc["rows"][d].items():
            for r in rs:
                r[1] = r[0] + 100.0          # 做空分數全體平移 +100 → 做空門檻應同步 +100、做多不變
    res = CT.calibrate(doc)
    e_l, e_s = res["cells"]["twse|mid|S1|long"], res["cells"]["twse|mid|S1|short"]
    assert e_s["n_obs"] == e_l["n_obs"]
    pooled_s = np.sort(np.concatenate([[r[1] for r in doc["rows"][d]["twse|mid"]] for d in doc["days"]]))
    assert e_s["thresholds"]["0"] == pytest.approx(float(np.percentile(pooled_s, res["q_star"], method="linear")))
    assert e_s["thresholds"]["0"] > 100 > e_l["thresholds"]["0"]


def test_population_is_state_days_only_and_no_population_cells():
    st = lambda i, m, h: "S2" if i % 2 else "S4"   # noqa: E731
    doc = make_doc(state_of=st)
    res = CT.calibrate(doc)
    assert res["cells"]["tpex|swing|S2|long"]["n_days"] == 20 and res["cells"]["tpex|swing|S4|short"]["n_days"] == 20
    for s in ("S1", "S3"):
        e = res["cells"][f"twse|short|{s}|long"]
        assert e["no_population"] is True and e["n_days"] == 0 and set(e["thresholds"].values()) == {None}
    # no_population 格的門檻經讀取端回 None（當日不出候選）
    t0 = {"cells": res["cells"]}
    assert P.threshold_from_t0(t0, "twse", "short", "S1", "long", 0.0) is None
    assert P.threshold_from_t0(t0, "twse", "short", "S2", "long", 1.0) == res["cells"]["twse|short|S2|long"]["thresholds"]["10"]


def test_undetermined_days_excluded():
    st = lambda i, m, h: "undetermined" if i < 10 else "S3"   # noqa: E731
    res = CT.calibrate(make_doc(state_of=st))
    assert res["cells"]["twse|short|S3|long"]["n_days"] == 30


def test_q_zero_infeasible_when_n0_too_large():
    doc = make_doc(n_stocks=8)        # S1 做多 N0=20 > 每日 8 檔
    res = CT.calibrate(doc)
    assert res["q_star"] is None
    assert res["cells"]["twse|short|S1|long"]["occupancy_at_0"] == 0.0


def test_missing_scores_reduce_pass_counts():
    doc = make_doc(short_none=True)
    res = CT.calibrate(doc)
    assert res["q_star"] is None      # 做空分數全缺 → 做空格通過數 0 < N0 → 不可行
    assert res["cells"]["twse|short|S1|short"]["n_obs"] == 0


def test_validate_rejects_out_of_train():
    doc = make_doc()
    doc["days"].append("2023-07-03")
    doc["states"]["2023-07-03"] = doc["states"][doc["days"][0]]
    with pytest.raises(CT.CalibT0Error, match="訓練段外"):
        CT.calibrate(doc)
    doc = make_doc()
    doc["train"] = ["2021-01-01", "2024-12-31"]
    with pytest.raises(CT.CalibT0Error, match="不是訓練段"):
        CT.calibrate(doc)


def _write(tmp: Path, doc) -> Path:
    p = tmp / "in.json.gz"
    p.write_bytes(gzip.compress(json.dumps(doc).encode(), mtime=0))
    return p


def test_main_writes_t0_and_report_deterministic(tmp_path):
    root = tmp_path / "repo"
    (root / "data" / "state").mkdir(parents=True)
    (root / "data" / "state" / "cross.json").write_text(json.dumps({"params_sha": "abc"}))
    inp = _write(tmp_path, make_doc())
    assert CT.main(["--input", str(inp), "--root", str(root)]) == 0
    t0p = root / "data" / "pick" / "t0.json"
    b1 = t0p.read_bytes()
    assert CT.main(["--input", str(inp), "--root", str(root)]) == 0
    assert t0p.read_bytes() == b1
    t0 = json.loads(b1)
    assert t0["calibrated"] is False and t0["params_sha"] == "abc" and t0["train"] == list(TR) and len(t0["cells"]) == 48
    assert t0["version"].startswith("q") and t0["version"].endswith("-abc")
    assert "q\\* =" in (root / "runs" / "t0" / "report.md").read_text()
    # build_pick 讀取端：params_sha 相符才啟用
    BP = _load("build_pick_t0", "scripts/build_pick.py")
    j, st = BP.load_t0(root, "abc")
    assert st == "ok" and j["q_star"] == t0["q_star"]
    assert BP.load_t0(root, "other")[1] == "params_sha_mismatch"


def test_main_rejects_params_sha_mismatch_and_q_none(tmp_path):
    root = tmp_path / "repo"
    (root / "data" / "state").mkdir(parents=True)
    (root / "data" / "state" / "cross.json").write_text(json.dumps({"params_sha": "zzz"}))
    assert CT.main(["--input", str(_write(tmp_path, make_doc())), "--root", str(root)]) == 2
    assert not (root / "data" / "pick" / "t0.json").exists()
    assert CT.main(["--input", str(_write(tmp_path, make_doc(n_stocks=8))), "--root", str(root), "--expect-params-sha", "abc"]) == 3
    assert not (root / "data" / "pick" / "t0.json").exists()
    assert "N0 過大" in (root / "runs" / "t0" / "report.md").read_text()


# ---------------------------------------------------------------------------
# export_t0_input：合成 scores.db 端到端（同 tests/test_export_dataset.py 的 world 做法）
@pytest.fixture(scope="module")
def sdb(tmp_path_factory):
    import export_dataset as EXP
    import replay_scores as RP
    import scan_features as SF
    from synth_db import DAYS, add_pit_rows, build_full
    base = tmp_path_factory.mktemp("t0db")
    cache = base / "cache"
    build_full(cache)
    add_pit_rows(cache, z_c=66, w_c=63, y_d=68)
    assert SF.main(["--cache-dir", str(cache), "--quiet", "--allow-short-warmup", "--warmup-days", "0"]) == 0
    assert RP.main(["--cache-dir", str(cache), "--window", "30", "--quiet"]) == 0
    saved = dict(EXP.SEGMENTS)
    EXP.SEGMENTS.clear()
    EXP.SEGMENTS.update({"train": (DAYS[60], DAYS[79]), "valid": ("2099-01-01", "2099-12-31")})   # 池內列只在後段（ADV 60 日視窗）
    yield {"cache": cache, "base": base, "days": DAYS[60:80]}
    EXP.SEGMENTS.clear()
    EXP.SEGMENTS.update(saved)


def test_export_t0_input_matches_db(sdb):
    ET = _load("export_t0_t", "scripts/export_t0_input.py")
    import export_dataset as EXP
    ET.SEGMENTS = EXP.SEGMENTS                      # 模組載入時已綁定同一 dict；保險起見再指一次
    out = sdb["base"] / "out"
    assert ET.main(["--cache-dir", str(sdb["cache"]), "--out", str(out)]) == 0
    p = out / "runs" / "t0" / "t0_input.json.gz"
    b1 = p.read_bytes()
    assert ET.main(["--cache-dir", str(sdb["cache"]), "--out", str(out)]) == 0
    assert p.read_bytes() == b1                     # 決定性
    doc = json.loads(gzip.decompress(b1))
    assert doc["days"] == sdb["days"] and doc["train"] == [sdb["days"][0], sdb["days"][-1]]
    from iching.scores_io import ScoreStore
    with ScoreStore(sdb["cache"] / "scores.db", readonly=True) as st:
        dv = doc["data_version"]
        assert st.params_sha_of(dv) == doc["params_sha"]
        n_pool = 0
        want_rows = {}
        for d in doc["days"]:
            rows = st.rows_for_day(dv, d)
            for r in rows:
                key = f"{r['market']}|{r['horizon']}"
                if r["stock_id"] == "__MARKET__":
                    want = r["flags"].get("basic_state")
                    assert doc["states"][d][key] == (want if want in P.STATES else "undetermined")
                    continue
                if r["in_rank_pool"] != 1:
                    continue
                n_pool += 1
                want_rows.setdefault((d, key), []).append((r["stock_id"], r))
        for (d, key), lst in want_rows.items():
            got = doc["rows"][d][key]
            assert len(got) == len(lst)
            for g, (_, r) in zip(got, sorted(lst, key=lambda x: x[0])):       # 匯出依代號排序
                assert g[0] == (None if r["base_score"] is None else round(r["base_score"], 6))
                lines = {k: (None if r[f"line_{k}_unknown"] == 1 else r[f"line_{k}"]) for k in (2, 3, 4, 5)}
                sr = P.short_raw(lines, r["horizon"])
                assert g[1] == (None if sr is None else round(sr, 6))
        assert n_pool == sum(len(v) for x in doc["rows"].values() for v in x.values()) and n_pool > 0


def test_export_t0_input_rejects_bad_params(sdb, monkeypatch):
    ET = _load("export_t0_t2", "scripts/export_t0_input.py")
    monkeypatch.setattr(ET, "check_params", lambda s, d: (_ for _ in ()).throw(ET.ExportDatasetError("非現行碼")))
    assert ET.main(["--cache-dir", str(sdb["cache"]), "--out", str(sdb["base"] / "o2")]) == 2
    assert not (sdb["base"] / "o2" / "runs").exists()


def test_hetzner_t0_script_static():
    """一句話腳本：語法、呼叫兩支腳本、推到 hetzner/t0-<TO>、force-with-lease 綁預期 SHA、calib rc 3 不中止。"""
    import subprocess
    p = ROOT / "scripts" / "hetzner_t0.sh"
    assert subprocess.run(["bash", "-n", str(p)]).returncode == 0
    s = p.read_text(encoding="utf-8")
    assert "python3 scripts/export_t0_input.py --cache-dir cache --out ." in s
    assert "python3 scripts/calib_t0.py --input runs/t0/t0_input.json.gz --root ." in s
    assert 'BR="hetzner/t0-${TO}"' in s and '--force-with-lease="$BR:$EXPECT"' in s
    assert '[ "$RC" = 0 ] || [ "$RC" = 3 ]' in s and "git rm -q --cached --ignore-unmatch data/pick/t0.json" in s and "dataset" not in s.replace("export_dataset", "")
