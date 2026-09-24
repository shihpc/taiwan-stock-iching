"""`scripts/score_stats.py`＋`scripts/hetzner_stats.sh`：§16.5 `:712`／`:714` 步驟 4／`:716`（裁定 #64 ①③）。

期待值一律在本檔手算寫死，不由被測函式產生（§20.1 末的判準 ③）。
分析流程用**真實 replay 產出的 db**（`synth_db`），不是手寫的表——手寫的表會跟著程式一起錯。
"""
from __future__ import annotations

import json
import os
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import replay_scores as R  # noqa: E402
import scan_features as SF  # noqa: E402
import score_stats as ST  # noqa: E402
from synth_db import build_full  # noqa: E402

REGISTRY = ROOT / "data" / "score_ranges.json"
SCRIPT = ROOT / "scripts" / "hetzner_stats.sh"
Y2020 = ("2020-01-01", "2020-12-31")


# ---- 統計函式（手算） ----

def test_group_stats_hand_values():
    a = np.array([10.0, 20.0, 45.0, 50.0, 55.0, 90.0])
    g = ST.group_stats(a, (10.0, 90.0))
    assert (g["n"], g["min"], g["max"]) == (6, 10.0, 90.0)
    assert g["median"] == 47.5                      # (45+50)/2
    assert g["q1"] == 26.25 and g["q3"] == 53.75    # 線性內插：位置 1.25→20+0.25×25；3.75→50+0.75×5
    assert g["share_45_55"] == 3 / 6                # 45、50、55（含端點）
    assert g["share_at_boundary"] == 2 / 6          # 10 與 90 貼著登錄端點
    assert g["distinct"] == 6 and g["escape_712"] == 0


def test_group_stats_skew_sign_and_zero():
    assert ST.group_stats(np.array([1.0, 1.0, 1.0, 10.0]), None)["skew"] > 0
    assert ST.group_stats(np.array([50.0, 50.0]), None)["skew"] == 0.0     # 零變異不除以 0


def test_group_stats_escape_and_tolerance():
    """逸出以 [7.30, 92.70] ±0.01 判：7.29 不算、7.2899 算；92.71 不算、92.7101 算。"""
    g = ST.group_stats(np.array([7.29, 7.2899, 92.71, 92.7101, 50.0]), None)
    assert g["escape_712"] == 2


def test_boundary_tolerance_is_0_01():
    g = ST.group_stats(np.array([10.009, 10.011, 89.991, 89.989]), (10.0, 90.0))
    assert g["share_at_boundary"] == 2 / 4


def test_distinct_rounds_to_6_decimals():
    g = ST.group_stats(np.array([50.0, 50.0 + 1e-9, 50.000001, 51.0]), None)
    assert g["distinct"] == 3


def test_sample_segment_is_hardcoded():
    """裁定 #64 ①：訓練＋驗證段寫死，CLI 不接受改日期。"""
    assert (ST.SAMPLE_START, ST.SAMPLE_END) == ("2021-01-01", "2024-12-31")
    with pytest.raises(SystemExit):
        ST.main(["--db", "x", "--out", "y", "--start", "2025-01-01"])


# ---- 真實 replay db ----

@pytest.fixture(scope="module")
def db(tmp_path_factory) -> Path:
    c = tmp_path_factory.mktemp("stats") / "cache"
    build_full(c)
    assert SF.main(["--cache-dir", str(c), "--quiet", "--allow-short-warmup", "--warmup-days", "0"]) == 0
    out = c / "scores.db"
    assert R.main(["--cache-dir", str(c), "--out", str(out), "--window", "30", "--quiet", "--limit-days", "30"]) == 0
    return out


def test_run_on_real_replay_db(db, tmp_path):
    rep = ST.run(db, REGISTRY, tmp_path / "r.json", start=Y2020[0], end=Y2020[1])
    assert rep["sample"]["n_days"] == 30
    assert len(rep["groups"]) == 144
    assert rep["summary"]["pass_712"] is True and rep["summary"]["pass_714"] is True
    assert (tmp_path / "r.txt").read_text(encoding="utf-8").startswith("§16.5 :712")


def test_counts_match_raw_sql_with_per_line_coverage(db, tmp_path):
    """裁定 #64 ③：逐爻 line_k_reweighted 分甲乙。以原始 SQL 另數一次，逐組比對 n。"""
    rep = ST.run(db, REGISTRY, tmp_path / "r.json", start=Y2020[0], end=Y2020[1])
    con = sqlite3.connect(db)
    want = {}
    for k in "123456":
        for scope, m, h, rw, n in con.execute(
                f"SELECT scope, market, horizon, line_{k}_reweighted, COUNT(*) FROM scores "
                f"WHERE line_{k} IS NOT NULL GROUP BY 1,2,3,4"):
            want[(scope, m, h, k, "reweighted" if rw else "full")] = n
    con.close()
    got = {(g["scope"], g["market"], g["horizon"], g["line"], g["coverage"]): g["n"] for g in rep["groups"] if g["n"]}
    assert got == want


def test_sample_window_filters_days(db, tmp_path):
    rep = ST.run(db, REGISTRY, tmp_path / "r.json", start="2020-01-01", end="2020-01-10")
    assert rep["sample"]["n_days"] == 10 and rep["sample"]["last_day"] == "2020-01-10"


def test_empty_window_aborts(db, tmp_path):
    with pytest.raises(ST.StatsError, match="沒有任何落地日"):
        ST.run(db, REGISTRY, tmp_path / "r.json", start="2021-01-01", end="2024-12-31")


def test_registry_model_version_mismatch_aborts(db, tmp_path):
    reg = json.loads(REGISTRY.read_text(encoding="utf-8"))
    reg["markets"]["twse"]["model_version"] = "p2-score-engine-1.000000000000"
    p = tmp_path / "reg.json"
    p.write_text(json.dumps(reg), encoding="utf-8")
    with pytest.raises(ST.StatsError, match="model_version"):
        ST.run(db, p, tmp_path / "r.json", start=Y2020[0], end=Y2020[1])


def test_db_not_from_current_code_aborts(db, tmp_path):
    """db 的 params_sha 被竄改（＝不是現行碼算的）→ 由 export_dataset.check_params 擋下。

    **必須指定 2020 年的樣本、並比對錯誤訊息**：走 `main()` 會用寫死的 2021～2024 樣本段，合成資料在 2020 年，
    rc=2 其實來自「樣本段沒資料」而不是這道守門——拿掉守門的突變照樣全綠（驗收前自查抓到）。"""
    bad = tmp_path / "bad.db"
    with sqlite3.connect(db) as a, sqlite3.connect(bad) as b:
        a.backup(b)
    con = sqlite3.connect(bad)
    con.execute("UPDATE replay_meta SET params_sha='000000000000'")
    con.commit()
    con.close()
    with pytest.raises(Exception, match="params_sha"):
        ST.run(bad, REGISTRY, tmp_path / "r.json", start=Y2020[0], end=Y2020[1])


def test_714_violation_reported_not_aborted(db, tmp_path):
    """登錄區間被收窄到觀測值之外 → 報告寫越界、rc 仍 0（檢查不過不算中止）。"""
    reg = json.loads(REGISTRY.read_text(encoding="utf-8"))
    for r in reg["rows"]:
        if (r["scope"], r["market"], r["horizon"], r["line"], r["coverage"]) == ("market_index", "twse", "short", "1", "full"):
            r["lo"], r["hi"] = 49.99, 50.01
    p = tmp_path / "reg.json"
    p.write_text(json.dumps(reg), encoding="utf-8")
    rep = ST.run(db, p, tmp_path / "r.json", start=Y2020[0], end=Y2020[1])
    assert {"scope": "market_index", "market": "twse", "horizon": "short", "line": "1",
            "coverage": "full"} in rep["summary"]["violations_714"]
    assert rep["summary"]["pass_714"] is False


# ---- hetzner_stats.sh：真的跑一遍（假 python3＋本機 bare repo），證明失敗時不推報告 ----

STUB_PY = r'''#!@@PY@@
import os, pathlib, sys
a = sys.argv[1:]
if a and a[0] == "-c":
    if "ScoreStore" in a[1]:
        print(os.environ.get("STUB_TO", "2026-09-19")); raise SystemExit(0)
    raise SystemExit("stub: 未預期的 -c")
if a and a[0].endswith("score_ranges.py"):
    raise SystemExit(int(os.environ.get("STUB_RANGES_RC", "0")))
if a and a[0].endswith("score_stats.py"):
    rc = int(os.environ.get("STUB_STATS_RC", "0"))
    if rc == 0:
        out = pathlib.Path(a[a.index("--out") + 1])
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text('{"schema": 1}', encoding="utf-8")
        out.with_suffix(".txt").write_text("report\n", encoding="utf-8")
    raise SystemExit(rc)
raise SystemExit(f"stub: 未預期的呼叫：{a[:3]}")
'''


def _git(cwd, *args):
    subprocess.run(["git", *args], cwd=cwd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def _sandbox(tmp_path, *, with_db=True):
    origin = tmp_path / "origin.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(origin)], check=True)
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.email", "t@example.invalid")
    _git(repo, "config", "user.name", "t")
    (repo / "README.md").write_text("x\n", encoding="utf-8")
    _git(repo, "add", "README.md")
    _git(repo, "commit", "-qm", "init")
    _git(repo, "remote", "add", "origin", str(origin))
    _git(repo, "push", "-q", "-u", "origin", "main")
    (repo / "scripts").mkdir()
    (repo / "scripts" / "hetzner_stats.sh").write_bytes(SCRIPT.read_bytes())
    (repo / "cache").mkdir()
    if with_db:
        (repo / "cache" / "scores.db").write_text("db", encoding="utf-8")
    stub = tmp_path / "stub"
    stub.mkdir()
    (stub / "python3").write_text(STUB_PY.replace("@@PY@@", sys.executable), encoding="utf-8")
    (stub / "python3").chmod(0o755)
    return repo, origin, stub


def _run(repo, stub, **env):
    e = dict(os.environ, PATH=f"{stub}{os.pathsep}{os.environ['PATH']}", **env)
    return subprocess.run(["bash", "scripts/hetzner_stats.sh"], cwd=repo, env=e, capture_output=True, text=True, timeout=180)


def _branches(origin):
    out = subprocess.run(["git", "ls-remote", "--heads", str(origin)], capture_output=True, text=True, check=True).stdout
    return sorted(ln.split("refs/heads/")[-1] for ln in out.splitlines() if ln.strip())


def test_script_syntax_and_no_replay():
    subprocess.run(["bash", "-n", str(SCRIPT)], check=True)
    t = SCRIPT.read_text(encoding="utf-8")
    assert "replay_scores.py" not in t and "--rebuild" not in t     # 只讀生產 db，不重播
    assert "  set -e\n" in t                                         # body 子 shell 重開 errexit


@pytest.mark.skipif(not shutil.which("git"), reason="需要 git")
def test_happy_path_pushes_report_only(tmp_path):
    repo, origin, stub = _sandbox(tmp_path)
    r = _run(repo, stub)
    assert r.returncode == 0, r.stdout + r.stderr
    assert _branches(origin) == ["hetzner/stats-2026-09-19", "main"]
    files = subprocess.run(["git", "ls-tree", "-r", "--name-only", "hetzner/stats-2026-09-19"], cwd=origin,
                           capture_output=True, text=True, check=True).stdout.split()
    assert "runs/stats/report_2026-09-19.json" in files and not any(f.endswith(".db") for f in files)


@pytest.mark.skipif(not shutil.which("git"), reason="需要 git")
def test_registry_out_of_date_gives_rc2_no_push(tmp_path):
    repo, origin, stub = _sandbox(tmp_path)
    r = _run(repo, stub, STUB_RANGES_RC="1")
    assert r.returncode == 2 and "未推送報告" in r.stdout
    assert _branches(origin) == ["main"]


@pytest.mark.skipif(not shutil.which("git"), reason="需要 git")
def test_missing_db_gives_rc2_no_push(tmp_path):
    repo, origin, stub = _sandbox(tmp_path, with_db=False)
    r = _run(repo, stub)
    assert r.returncode == 2 and _branches(origin) == ["main"]


@pytest.mark.skipif(not shutil.which("git"), reason="需要 git")
def test_stats_failure_gives_rc3_no_push(tmp_path):
    repo, origin, stub = _sandbox(tmp_path)
    r = _run(repo, stub, STUB_STATS_RC="2")
    assert r.returncode == 3 and _branches(origin) == ["main"]


@pytest.mark.skipif(not shutil.which("git"), reason="需要 git")
def test_git_step_failure_stops_before_stats(tmp_path):
    """步驟 0 的 git 指令只靠 errexit：origin 拿掉後 fetch 失敗，必須停、不得推。"""
    repo, origin, stub = _sandbox(tmp_path)
    _git(repo, "remote", "set-url", "origin", str(tmp_path / "nope.git"))
    r = _run(repo, stub)
    assert r.returncode != 0 and "== 2" not in r.stdout
    assert _branches(origin) == ["main"]
