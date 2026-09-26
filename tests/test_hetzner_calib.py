"""`scripts/hetzner_calib.sh` 守門放寬（2026-09-25，裁定 #68 後的重跑鏈，`docs/P3-CALIBRATION.md` §31）的行為測試。

放寬的是「`cache/scores.db` 必須存在且 params_sha＝現行碼」與「xdump 與 db 同 sha」這類**依賴 db** 的比對
（#68 的順序是先校準、使用者裁定 d、再 12.6 h 重播，校準時 db 必然是舊碼算的）。**不放寬**的：
①window 改取 repo 的 `data/state/cross.json`（同 `hetzner_replay.sh`）；②PARAMS_SHA 由現行碼以該 window 重算，
第 1 步 dump 的 manifest 必須等於它；③DUMP_FROM～DUMP_TO 必須落在訓練段內（校準母體限訓練段，CLAUDE.md 約定 4）；
④`calibrate_d.py` 自己對 manifest 與現行碼指紋的比對（`tests/test_calibrate.py` (e) 另守，本檔補「#68 前的 dump 被拒」）。

臨時 git repo 放**真的** `src/`（守門要 import 真實的 `build_params`／`SEGMENTS`），`replay_scores.py` 與 `calibrate_d.py` 是
記錄 argv 的假貨（前者在生產是 6.6 小時）；`python3` 以 PATH 指到本測試的直譯器。
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

import calibrate_d as CD  # noqa: E402
from export_dataset import expected_params_sha  # noqa: E402
from iching import xdump as XD  # noqa: E402
from iching.config import SEGMENTS  # noqa: E402

SCRIPT = "hetzner_calib.sh"
REPLAY_CALLS = "cache/logs/replay_calls.txt"
CALIB_CALLS = "cache/logs/calib_calls.txt"
PRE68_PARAMS_SHA = "c7385e78cb9f"              # #68 前、window 320 的指紋（runs/stats 等報告記的值）


def _want(window: int) -> dict:
    return {"window": window, "adv_window": 60, "adv_threshold": 30000000.0, "fundamentals": True}


FAKE_REPLAY = f'''import json, os, pathlib, sys
a = sys.argv[1:]
p = pathlib.Path({REPLAY_CALLS!r}); p.parent.mkdir(parents=True, exist_ok=True); p.write_text(" ".join(a), encoding="utf-8")
d = pathlib.Path(a[a.index("--dump-x") + 1]); d.mkdir(parents=True, exist_ok=True)
(d / "manifest.json").write_text(json.dumps({{"schema": 1, "keys": {{}}, "params_sha": os.environ["FAKE_SHA"],
    "dump_from": a[a.index("--dump-from") + 1], "dump_to": a[a.index("--dump-to") + 1]}}), encoding="utf-8")
sys.exit(int(os.environ.get("FAKE_REPLAY_RC", "0")))
'''
FAKE_CALIB = f'''import json, pathlib, sys
a = sys.argv[1:]
p = pathlib.Path({CALIB_CALLS!r}); p.parent.mkdir(parents=True, exist_ok=True); p.write_text(" ".join(a), encoding="utf-8")
out = pathlib.Path(a[a.index("--out-dir") + 1]); tag = a[a.index("--tag") + 1]
out.mkdir(parents=True, exist_ok=True)
(out / f"d_report_{{tag}}.json").write_text(json.dumps({{"gate_failures": [], "degenerate": [], "new": True}}), encoding="utf-8")
(out / f"d_report_{{tag}}.txt").write_text("new report\\n", encoding="utf-8")
'''


def _git(*a, cwd):
    return subprocess.run(["git", *a], cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()


def _world(tmp_path: Path, *, cross: dict | None = None) -> tuple[Path, Path, Path]:
    """回 (work, bare, bindir)。main 上已有舊的 runs/calib/d_report_2023-06-30.*（同生產現況），沒有 cache/scores.db。"""
    work = tmp_path / "work"
    shutil.copytree(ROOT / "src", work / "src", ignore=shutil.ignore_patterns("__pycache__"))
    (work / "scripts").mkdir()
    (work / "scripts" / SCRIPT).write_text((ROOT / "scripts" / SCRIPT).read_text(encoding="utf-8"), encoding="utf-8")
    (work / "scripts" / "replay_scores.py").write_text(FAKE_REPLAY, encoding="utf-8")
    (work / "scripts" / "calibrate_d.py").write_text(FAKE_CALIB, encoding="utf-8")
    (work / "data" / "state").mkdir(parents=True)
    if cross is not None:
        (work / "data" / "state" / "cross.json").write_text(json.dumps(cross), encoding="utf-8")
    (work / "runs" / "calib").mkdir(parents=True)
    (work / "runs" / "calib" / "d_report_2023-06-30.json").write_text('{"old": true}', encoding="utf-8")
    (work / "runs" / "calib" / "d_report_2023-06-30.txt").write_text("old report\n", encoding="utf-8")
    bare = tmp_path / "origin.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(bare)], check=True)
    for a in (["init", "-q", "-b", "main"], ["config", "user.email", "t@t"], ["config", "user.name", "t"],
              ["remote", "add", "origin", str(bare)], ["add", "-A"], ["commit", "-qm", "w"], ["push", "-q", "-u", "origin", "main"]):
        subprocess.run(["git", *a], cwd=work, check=True, capture_output=True)
    bindir = tmp_path / "bin"
    bindir.mkdir()
    shim = bindir / "python3"                                  # symlink 會讓 venv 失效（pyvenv.cfg 依 argv[0] 找），用轉呼叫
    shim.write_text(f'#!/bin/sh\nexec "{sys.executable}" "$@"\n', encoding="utf-8")
    shim.chmod(0o755)
    return work, bare, bindir


def _run(work: Path, bindir: Path, args: list[str], **env_extra) -> subprocess.CompletedProcess:
    tmpdir = work.parent / "tmp"
    tmpdir.mkdir(exist_ok=True)
    env = {**os.environ, "PATH": f"{bindir}{os.pathsep}{os.environ['PATH']}", "TMPDIR": str(tmpdir),
           "HETZNER_CALIB_LOG": "", "HETZNER_CALIB_SELF": "", "HETZNER_CALIB_PULLED": "", "HETZNER_CALIB_REPO": "",
           "HETZNER_CALIB_REUSE_DUMP": "", "PYTHONDONTWRITEBYTECODE": "1", **env_extra}
    return subprocess.run(["bash", f"scripts/{SCRIPT}", *args], cwd=work, capture_output=True, text=True, env=env)


@pytest.mark.parametrize("window", [320, 250])
def test_runs_without_scores_db_and_window_from_cross_json(tmp_path, window):
    """沒有 cache/scores.db 也過守門；window 取自 cross.json；PARAMS_SHA＝現行碼以該 window 重算；報告推到分支。"""
    work, bare, bindir = _world(tmp_path, cross={"meta": {"window": window, "params_sha": "whatever-old"}})
    want = expected_params_sha(_want(window))
    r = _run(work, bindir, ["2023-06-30"], FAKE_SHA=want)
    out = r.stdout + r.stderr
    assert r.returncode == 0, out
    assert not (work / "cache" / "scores.db").exists()
    calls = (work / REPLAY_CALLS).read_text(encoding="utf-8")
    assert calls == f"--cache-dir cache --window {window} --dump-only --dump-x cache/xdump --dump-from 2021-01-01 --dump-to 2023-06-30"
    assert (work / CALIB_CALLS).read_text(encoding="utf-8") == "--dump-dir cache/xdump --out-dir runs/calib --tag 2023-06-30"
    guard = (work / "cache" / "logs" / "calib-guard-2023-06-30.txt").read_text(encoding="utf-8")
    assert f"params_sha={want}\n" in guard and f"window={window}\n" in guard
    assert f"train={SEGMENTS['train'][0]}..{SEGMENTS['train'][1]}" in guard and "calibrated=True" in guard
    # 報告推到 hetzner/calib-2023-06-30，內容是新的（同名覆寫舊報告——§31 要求事先另存舊分支的理由）；本機回到 main
    assert json.loads(_git("show", "hetzner/calib-2023-06-30:runs/calib/d_report_2023-06-30.json", cwd=bare)) == \
        {"gate_failures": [], "degenerate": [], "new": True}
    assert _git("rev-parse", "--abbrev-ref", "HEAD", cwd=work) == "main"
    assert want in _git("log", "-1", "--format=%s", "hetzner/calib-2023-06-30", cwd=bare)
    if window == 320:
        assert want == "8ca174ee8bc7"                          # 與 tests/test_apply_calibration.py NEW_REPLAY_PARAMS_SHA 同值（裁定 #69 後）


@pytest.mark.parametrize("args,frag", [
    (["2023-06-30", "2020-12-31"], "DUMP_FROM=2020-12-31／DUMP_TO=2023-06-30 不在訓練段"),     # 暖機日進母體
    (["2023-07-03"], "DUMP_FROM=2021-01-01／DUMP_TO=2023-07-03 不在訓練段"),                  # 驗證段進母體
    (["2024-12-31", "2023-07-01"], "不在訓練段"),                                            # 整段在驗證段
])
def test_refuses_outside_training_segment(tmp_path, args, frag):
    work, _, bindir = _world(tmp_path, cross={"meta": {"window": 320}})
    r = _run(work, bindir, args, FAKE_SHA=expected_params_sha(_want(320)))
    out = r.stdout + r.stderr
    assert r.returncode == 2 and frag in out and "校準母體限訓練段" in out, out
    assert not (work / REPLAY_CALLS).exists() and not (work / CALIB_CALLS).exists()


@pytest.mark.parametrize("cross,frag", [(None, "data/state/cross.json 不存在"), ({"meta": {}}, "守門失敗：KeyError")])
def test_refuses_without_window(tmp_path, cross, frag):
    work, _, bindir = _world(tmp_path, cross=cross)
    r = _run(work, bindir, ["2023-06-30"], FAKE_SHA="x")
    out = r.stdout + r.stderr
    assert r.returncode == 2 and frag in out, out
    assert not (work / REPLAY_CALLS).exists()


def test_refuses_dump_with_other_fingerprint(tmp_path):
    """第 1 步產出的 manifest 指紋 ≠ 現行碼以 cross.json 的 window 重算的值 → rc 2、不跑 calibrate_d、不推分支。"""
    work, bare, bindir = _world(tmp_path, cross={"meta": {"window": 320}})
    r = _run(work, bindir, ["2023-06-30"], FAKE_SHA=PRE68_PARAMS_SHA)
    out = r.stdout + r.stderr
    assert r.returncode == 2 and f"xdump manifest 的 params_sha={PRE68_PARAMS_SHA} ≠ 現行碼以 window=320 重算的" in out, out
    assert not (work / CALIB_CALLS).exists()
    assert "hetzner/calib-2023-06-30" not in _git("branch", "--list", cwd=bare)


def test_reuse_dump_refuses_pre68_manifest(tmp_path):
    """`HETZNER_CALIB_REUSE_DUMP=1` 沿用既有 xdump：#68 前算的 dump（指紋不同）不得被沿用。"""
    work, _, bindir = _world(tmp_path, cross={"meta": {"window": 320}})
    xd = work / "cache" / "xdump"
    xd.mkdir(parents=True)
    (xd / "manifest.json").write_text(json.dumps({"schema": 1, "keys": {}, "params_sha": PRE68_PARAMS_SHA,
                                                  "dump_from": "2021-01-01", "dump_to": "2023-06-30"}), encoding="utf-8")
    r = _run(work, bindir, ["2023-06-30"], FAKE_SHA="x", HETZNER_CALIB_REUSE_DUMP="1")
    out = r.stdout + r.stderr
    assert r.returncode == 2 and "HETZNER_CALIB_REUSE_DUMP=1 但既有 xdump manifest 的 ['params_sha']" in out, out
    assert not (work / REPLAY_CALLS).exists() and not (work / CALIB_CALLS).exists()


def test_script_has_no_scores_db_dependency_but_keeps_calibrate_d():
    text = (ROOT / "scripts" / SCRIPT).read_text(encoding="utf-8")
    code = "\n".join(ln for ln in text.splitlines() if not ln.lstrip().startswith("#"))
    assert "check_params" not in code and "ScoreStore" not in code and "cache/scores.db" not in code
    assert "json.load(open(\"data/state/cross.json\", encoding=\"utf-8\"))[\"meta\"][\"window\"]" in code
    assert 'SEGMENTS["train"]' in code
    assert 'python3 scripts/calibrate_d.py --dump-dir "$XDUMP" --out-dir runs/calib --tag "$DUMP_TO"' in code


def test_calibrate_d_still_rejects_pre68_dump(tmp_path, capsys):
    """`calibrate_d.py` 的指紋比對保留：#68 前（RULES_VERSION -1）算的 dump，manifest 自洽也被拒——
    因為比的是**現行碼**以 manifest 記的 window／adv／fundamentals 重算的指紋。"""
    d = tmp_path / "xd"
    d.mkdir()
    (d / XD.MANIFEST).write_text(json.dumps({"schema": XD.SCHEMA, "keys": {}, "params_sha": PRE68_PARAMS_SHA, "params": _want(320),
                                             "dump_from": "2021-01-01", "dump_to": "2023-06-30", "data_version": "x"}),
                                 encoding="utf-8")
    assert CD.main(["--dump-dir", str(d), "--out-dir", str(tmp_path / "o")]) == 2
    err = capsys.readouterr().err
    assert f"params_sha={PRE68_PARAMS_SHA} ≠ 現行碼指紋 {expected_params_sha(_want(320))}" in err
    assert not (tmp_path / "o").exists()
