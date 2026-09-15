"""`scripts/recompute_from_seed.py`（從種子重算＋dump 驗證）的合成世界測試。

世界沿用 `tests/test_daily_core.py::world` 的建法（`build_full` → 重播到 K 存快照 → `export_seed`），接著每日班 `run_offline`
逐日跑到底（＝「每日班原產出」），再把「種子」與「跑完的 repo」各 commit 進一個本機 git repo，讓腳本走真正的
`git ls-tree`／`show`／`archive` 路徑。斷言：①重算＝每日班原產出逐位（rows／diag 除 elapsed_ms／最終 cross.json 位元組）；
②dump 驗證模式對「零差異 dump」（含 `lines_*` list／str 等價列）回逐位相同 rc 0；③dump 內一格參考值不同、只在一側的列都抓得到 rc 1；
④`--python-check` 在 <3.12 拒跑；⑤`--from` 跳日拒跑；⑥`--seed-bundles` < 全部＝部分種子：沒帶 `--allow-partial-seed` 拒跑（rc 2）、
帶了跑完 rc 3 且完成行印「部分種子、產物不得覆蓋」，不足 ADV 視窗時排名池斷言擋下（rc 2）；⑦main 分數檔一格被改、dump 為空 → rc 1
（逐欄全比，不只比 dump 內的格）；⑧`--out` 是 repo 本身或其子目錄 → rc 2。
"""
from __future__ import annotations

import gzip
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import export_seed as ES  # noqa: E402
import recompute_from_seed as RC  # noqa: E402
import replay_scores as RP  # noqa: E402
import scan_features as SF  # noqa: E402
from iching import bundle_io as B  # noqa: E402
from iching import calendar as CAL  # noqa: E402
from iching import daily_core as DC  # noqa: E402
from iching import replay_io as RIO  # noqa: E402
from iching import scores_io as SI  # noqa: E402
from synth_db import DAYS, DV, build_full  # noqa: E402

WINDOW = 30
K = 60
CHAIN_DAYS = DAYS[K + 1:]


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@example.invalid", *args],
                          capture_output=True, check=True).stdout.decode().strip()


def _copy_tree(src: Path, dst: Path) -> None:
    for p in src.rglob("*"):
        if p.is_file():
            q = dst / p.relative_to(src)
            q.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy(p, q)


@pytest.fixture(scope="module")
def world(tmp_path_factory) -> dict:
    base = tmp_path_factory.mktemp("recompute")
    cache, repo = base / "cache", base / "repo"
    build_full(cache)
    assert SF.main(["--cache-dir", str(cache), "--quiet", "--allow-short-warmup", "--warmup-days", "0"]) == 0
    assert RP.main(["--cache-dir", str(cache), "--window", str(WINDOW), "--to", DAYS[K], "--quiet"]) == 0
    state_k = base / "state_k.json"
    shutil.copy(cache / "scores.db.state.json", state_k)
    src = RIO.ReplaySource(cache, DV, window=WINDOW)
    dates = src.trading_dates()
    CAL.write_calendar_json(repo / DC.CALENDAR_TPE_FILE, CAL.calendar_payload("tpe", dates, DV))
    src.close()
    assert ES.main(["--cache-dir", str(cache), "--out", str(repo), "--window", str(WINDOW), "--state", str(state_k)]) == 0
    # 本機 git repo：commit 1＝種子（狀態＋原料包＋data 檔）
    git = base / "git"
    git.mkdir()
    _git(git, "init", "-q")
    _copy_tree(repo, git)
    _git(git, "add", "-A")
    _git(git, "commit", "-q", "-m", "seed")
    seed_sha = _git(git, "rev-parse", "HEAD")
    # 每日班：逐日餵原料包 → run_offline（＝每日班原產出）
    src = RIO.ReplaySource(cache, DV, window=WINDOW)          # 游標須從第一天連續讀（美股／匯率只帶增量），同 test_daily_core._feed_bundle
    for T in DAYS:
        b = src.read_day(T)
        if T <= DAYS[K]:
            continue
        B.write_bundle(repo, b)
        summary = DC.run_offline(repo, T, window=WINDOW)
        assert [x["date"] for x in summary["days"]] == [T]
    src.close()
    # commit 2＝main（跑完的 repo：全部原料包＋分數檔＋狀態鏈）
    _copy_tree(repo, git)
    _git(git, "add", "-A")
    _git(git, "commit", "-q", "-m", "main")
    main_sha = _git(git, "rev-parse", "HEAD")
    return {"cache": cache, "repo": repo, "git": git, "seed_sha": seed_sha, "main_sha": main_sha}


def _args(world: dict, out: Path, *extra: str) -> list[str]:
    return ["--repo", str(world["git"]), "--seed-commit", world["seed_sha"], "--data-ref", world["main_sha"],
            "--bundles-ref", world["main_sha"], "--out", str(out), "--window", str(WINDOW), *extra]


def _scores(root: Path, T: str) -> dict:
    return json.loads((root / DC.SCORES_DIR / f"{T}.json").read_text(encoding="utf-8"))


def _write_dump(path: Path, recs: list[dict]) -> Path:
    with gzip.open(path, "wt", encoding="utf-8") as f:
        for r in recs:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    return path


def _rec(T: str, row: dict, col, a, b=None) -> dict:
    return {"kind": "score", "date": T, "market": row["market"], "horizon": row["horizon"], "stock_id": row["stock_id"],
            "col": col, "a": a, "b": b, "class": "④"}


def _fmt(v) -> str:
    return json.dumps(v, ensure_ascii=False, sort_keys=True)


def _db_shape(row: dict, dv: str, tv: str) -> dict:
    """分數檔一列 → `ScoreStore.rows_for_day` 形狀（dump 內只在參考側的整列長這樣）。"""
    d = dict(row)
    d["tpe_trading_date"] = d.pop("date")
    d["data_version"], d["text_version"] = dv, tv
    d["lines_provisional"] = SI.bits_list(d["lines_provisional"])
    d["lines_formal"] = SI.bits_list(d["lines_formal"])
    d["flags"] = None if d["flags"] is None else json.loads(d["flags"])
    return d


def test_recompute_equals_daily_chain_bitwise(world, tmp_path, capsys):
    out = tmp_path / "out"
    assert RC.main(_args(world, out)) == RC.RC_OK
    done = next(line for line in capsys.readouterr().out.splitlines() if line.startswith("完成："))
    assert f"seed={world['seed_sha'][:12]} data={world['main_sha'][:12]} bundles={world['main_sha'][:12]}" in done
    assert "未驗證（無 --dump）" in done and "部分種子" not in done
    manifest = json.loads((out / RC.MANIFEST_FILE).read_text(encoding="utf-8"))
    assert manifest["days"] == CHAIN_DAYS and manifest["seed_last_date"] == DAYS[K]
    assert manifest["seed_bundles"] == manifest["seed_bundles_total"] == K + 1 and manifest["seed_last"] == DAYS[K]
    repo = world["repo"]
    for T in CHAIN_DAYS:
        a, b = _scores(repo, T), _scores(out, T)
        assert a["rows"] == b["rows"] and len(b["rows"]) > 0, T
        assert RC.diag_diff(a["diag"], b["diag"]) == [], T
        assert {k: v for k, v in a.items() if k not in ("rows", "diag")} == {k: v for k, v in b.items() if k not in ("rows", "diag")}
    assert (out / DC.STATE_FILE).read_bytes() == (repo / DC.STATE_FILE).read_bytes()
    summary = json.loads((out / RC.SUMMARY_FILE).read_text(encoding="utf-8"))
    assert [d["date"] for d in summary["days"]] == CHAIN_DAYS and summary["state_last_date"] == DAYS[-1]
    for d in summary["days"]:
        vm = d["vs_main"]
        assert vm["diff_rows"] == vm["only_ref"] == vm["only_got"] == 0 and vm["equal"] == d["rows"] > 0 and vm["diag_diff_cols"] == []
        assert d["verify"] is None if "verify" in d else True
    # 沒碰 git repo 的工作樹
    assert _git(world["git"], "status", "--porcelain") == ""
    # --out 非空拒跑；--force 可重跑
    assert RC.main(_args(world, out)) == RC.RC_SETUP
    assert RC.main(_args(world, out, "--force")) == RC.RC_OK


def test_verify_zero_diff_dump_is_bitwise_equal(world, tmp_path):
    """零差異 dump（只有非 score 的 kind ＋ `lines_*` 以 list 表示的同值列）→ 每日逐位相同、rc 0。"""
    T = CHAIN_DAYS[3]
    js = _scores(world["repo"], T)
    row = next(r for r in js["rows"] if r["lines_provisional"] is not None and r["lines_formal"] is not None)
    recs = [{"kind": "factor", "date": T, "stock_id": row["stock_id"], "col": "x", "a": 1, "b": 2, "class": "⑤未計"},
            _rec(T, row, "lines_provisional", SI.bits_list(row["lines_provisional"]), [0, 0, 0, 0, 0, 0]),
            _rec(T, row, "lines_formal", SI.bits_list(row["lines_formal"]), [0, 0, 0, 0, 0, 0])]
    dump = _write_dump(tmp_path / "zero.jsonl.gz", recs)
    out = tmp_path / "out"
    assert RC.main(_args(world, out, "--dump", str(dump))) == RC.RC_OK
    summary = json.loads((out / RC.SUMMARY_FILE).read_text(encoding="utf-8"))
    for d in summary["days"]:
        v = d["verify"]
        assert v["diff_rows"] == v["only_ref"] == v["only_got"] == 0 and v["equal"] == d["rows"] == v["ref_rows"] > 0, d["date"]
        assert v["dump_records"] == (2 if d["date"] == T else 0)


def test_verify_detects_reference_differences(world, tmp_path, capsys):
    """dump 一格參考值不同 → 該日 1 列差異；`col=null` 且 `a=null`（參考沒有這列）→ 只在重算；`a` 為整列（參考多一列）→ 只在參考。rc 1。"""
    T = CHAIN_DAYS[2]
    js = _scores(world["repo"], T)
    rows = [r for r in js["rows"] if r["line_2"] is not None]
    r1, r2, r3 = rows[0], rows[1], dict(rows[2])
    r3["stock_id"] = "9999"
    recs = [_rec(T, r1, "line_2", r1["line_2"] + 1.0, r1["line_2"]),
            _rec(T, r2, None, None, _db_shape(r2, js["data_version"], js["text_version"])),
            _rec(T, r3, None, _db_shape(r3, js["data_version"], js["text_version"]), None)]
    dump = _write_dump(tmp_path / "diff.jsonl.gz", recs)
    out = tmp_path / "out"
    assert RC.main(_args(world, out, "--dump", str(dump))) == RC.RC_DIFF
    printed = capsys.readouterr().out
    key = lambda r: f"{r['market']}/{r['stock_id']}/{r['horizon']}"  # noqa: E731
    assert f"  驗證 {T}: ✗ 差異 1 列（1 格）／只在參考 1／只在重算 1" in printed
    assert f"    {key(r1)}  line_2: 參考={_fmt(r1['line_2'] + 1.0)} 重算={_fmt(r1['line_2'])}" in printed
    assert f"    {key(r2)}  只在重算" in printed and f"    {key(r3)}  只在參考" in printed
    assert "驗證結果：逐位相同 " in printed and f"有差異／無法驗證 1 日 ['{T}']" in printed
    summary = json.loads((out / RC.SUMMARY_FILE).read_text(encoding="utf-8"))
    by = {d["date"]: d["verify"] for d in summary["days"]}
    v = by[T]
    assert v["diff_rows"] == 1 and v["diff_cells"] == 1 and v["only_got"] == 1 and v["only_ref"] == 1 and v["ref_rows"] == len(js["rows"])
    for d, v in by.items():
        if d != T:
            assert v["diff_rows"] == v["only_ref"] == v["only_got"] == 0, d
    # 重算產物本身不受 dump 影響（仍＝每日班原產出）
    assert _scores(out, T)["rows"] == js["rows"]


def test_main_scores_cell_changed_with_empty_dump_is_caught(world, tmp_path, capsys):
    """守「逐欄全比、不只比 dump 內的格」：把 --data-ref 上某日分數檔一格改掉（另 commit），dump 為空 → 該日 1 列差異、rc 1，
    印出的差異指到那一格。"""
    T = CHAIN_DAYS[5]
    git = world["git"]
    path = git / DC.SCORES_DIR / f"{T}.json"
    js = json.loads(path.read_text(encoding="utf-8"))
    i, row = next((i, r) for i, r in enumerate(js["rows"]) if r["base_score"] is not None)
    orig = row["base_score"]
    js["rows"][i]["base_score"] = orig + 0.5
    path.write_text(DC.dumps(js), encoding="utf-8")
    _git(git, "add", "-A")
    _git(git, "commit", "-q", "-m", "mutate one cell")
    mut_sha = _git(git, "rev-parse", "HEAD")
    dump = _write_dump(tmp_path / "empty.jsonl.gz", [])
    out = tmp_path / "out"
    args = _args(world, out, "--dump", str(dump))
    args[args.index("--data-ref") + 1] = mut_sha
    assert RC.main(args) == RC.RC_DIFF
    printed = capsys.readouterr().out
    assert f"    {row['market']}/{row['stock_id']}/{row['horizon']}  base_score: 參考={_fmt(orig + 0.5)} 重算={_fmt(orig)}" in printed
    summary = json.loads((out / RC.SUMMARY_FILE).read_text(encoding="utf-8"))
    by = {d["date"]: d for d in summary["days"]}
    assert by[T]["verify"]["diff_rows"] == 1 and by[T]["verify"]["diff_cells"] == 1 and by[T]["verify"]["dump_records"] == 0
    assert by[T]["vs_main"]["diff_rows"] == 1
    assert all(d["verify"]["diff_rows"] == 0 and d["vs_main"]["diff_rows"] == 0 for d in summary["days"] if d["date"] != T)
    # 重算產物本身不受 --data-ref 分數檔影響（仍＝每日班原產出）
    assert _scores(out, T)["rows"] == _scores(world["repo"], T)["rows"]


def test_db_row_roundtrip_matches_file_row(world):
    T = CHAIN_DAYS[0]
    js = _scores(world["repo"], T)
    for r in js["rows"][:50]:
        assert RC.db_row_to_file_row(_db_shape(r, js["data_version"], js["text_version"])) == r
        for c in ("lines_provisional", "lines_formal", "flags", "base_score", "streaks"):
            assert RC.db_value_to_file(c, _db_shape(r, js["data_version"], js["text_version"])[c]) == r[c]
    # flags 的序列化必須與 `flatten_row` 同一支（鍵**未排序**的 dict 才分得出「抄一行 json.dumps」與「共用函式」）
    fl = {"raw": {"b": "x", "a": "y"}, "by_direction": {"short": {"active": {}}, "long": {"active": {}}}, "basic_state": "S2"}
    base = {"market": "twse", "horizon": "mid", "stock_id": "__MARKET__", "tpe_trading_date": T, "flags": fl}
    expect = SI.flatten_row(base, line_states="------", streaks="0,0,0,0,0,0", in_rank_pool=None)["flags"]
    assert RC.db_value_to_file("flags", fl) == expect == SI.flags_text(fl) and expect.index('"basic_state"') < expect.index('"by_direction"')


def test_python_check_refuses_below_3_12(world, tmp_path, monkeypatch):
    out = tmp_path / "out"
    monkeypatch.setattr(sys, "version_info", (3, 11, 9, "final", 0))
    assert RC.main(_args(world, out, "--python-check")) == RC.RC_SETUP
    assert not out.exists()


def test_from_must_be_first_day_after_seed(world, tmp_path):
    assert RC.main(_args(world, tmp_path / "a", "--from", DAYS[K + 2])) == RC.RC_SETUP
    assert RC.main(_args(world, tmp_path / "b", "--to", DAYS[K])) == RC.RC_SETUP
    assert RC.main(_args(world, tmp_path / "c", "--seed-commit", "0000000")) == RC.RC_SETUP
    assert not (tmp_path / "a" / DC.SCORES_DIR).exists()


def test_out_must_not_be_repo_or_inside_it(world, tmp_path):
    """`--out` 是 --repo 本身或其子目錄 → rc 2、不建目錄、不動工作樹（`--force` 帶著也一樣，因為守門在 rmtree 之前）。"""
    git = world["git"]
    assert RC.main(_args(world, git, "--force")) == RC.RC_SETUP
    assert RC.main(_args(world, git / "sub" / "out", "--force")) == RC.RC_SETUP
    assert RC.main(_args(world, git / ".." / git.name / "x")) == RC.RC_SETUP
    assert not (git / "sub").exists() and not (git / "x").exists() and _git(git, "status", "--porcelain") == ""


def test_partial_seed_requires_flag_and_returns_rc3(world, tmp_path, capsys):
    """`--seed-bundles N` < 全部＝部分種子：①沒帶 `--allow-partial-seed` → rc 2、連世界都不建；②帶了但只 5 份（＜ADV 60 日）→
    T 當日排名池與狀態快照不一致 → `DailyCoreError` → rc 2、manifest 記 partial、不落分數檔；③帶了且 60 份 → 跑完 rc 3、
    完成行明印「部分種子、產物不得覆蓋」（分數檔有落、但 rc 非 0 擋覆蓋）；④同樣 60 份＋零差異 dump → 仍 rc 3（部分種子優先於驗證結果）。"""
    out = tmp_path / "out"
    assert RC.main(_args(world, out, "--seed-bundles", "5")) == RC.RC_SETUP
    assert not out.exists()
    assert RC.main(_args(world, out, "--seed-bundles", "5", "--allow-partial-seed")) == RC.RC_SETUP
    manifest = json.loads((out / RC.MANIFEST_FILE).read_text(encoding="utf-8"))
    assert manifest["seed_bundles"] == 5 and manifest["seed_bundles_total"] == K + 1 and manifest["seed_first"] == DAYS[K - 4]
    assert manifest["partial_seed"] is True and not (out / DC.SCORES_DIR).exists()
    capsys.readouterr()
    assert RC.main(_args(world, out, "--seed-bundles", str(K), "--allow-partial-seed", "--force")) == RC.RC_PARTIAL
    printed = capsys.readouterr().out
    done = next(line for line in printed.splitlines() if line.startswith("完成："))
    assert f"⚠ 部分種子（{K}/{K + 1} 份）、產物不得覆蓋" in done and "未驗證（無 --dump）" in done
    assert (out / DC.SCORES_DIR / f"{CHAIN_DAYS[-1]}.json").exists()
    assert json.loads((out / RC.MANIFEST_FILE).read_text(encoding="utf-8"))["partial_seed"] is True
    dump = _write_dump(tmp_path / "empty.jsonl.gz", [])
    assert RC.main(_args(world, out, "--seed-bundles", str(K), "--allow-partial-seed", "--force", "--dump", str(dump))) == RC.RC_PARTIAL
    assert "⚠ 部分種子、產物不得覆蓋" in capsys.readouterr().out.splitlines()[-1]
