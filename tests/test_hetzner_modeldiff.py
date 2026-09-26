"""`scripts/hetzner_modeldiff.sh` 的結構與乾跑測試（`docs/P3-CALIBRATION.md` §33；寫法同 `tests/test_hetzner_t717.py`）。

守的重點：①重播沒完成（log 末行不是 `== replay exit 0`、或 `cache/logs/replay.started` 還在）就不跑，
②rc=1（不變式違反）**也要推報告**且 log 末行標明，rc=2 不推，③推上去的分支只放報告、不放 db，
④pull 後 HEAD 前進即改用新版重新執行。git 是本機真的、python3 是假的（依呼叫形狀分派）。
"""
from __future__ import annotations

import datetime as dt
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SH = ROOT / "scripts" / "hetzner_modeldiff.sh"


def text() -> str:
    return SH.read_text(encoding="utf-8")


def test_syntax_ok():
    subprocess.run(["bash", "-n", str(SH)], check=True)


def test_static_structure():
    t = text()
    # 自我複製、log 輪替、工作樹乾淨、pull --ff-only、HEAD 前進重執行（同 hetzner_revneg.sh 骨架）
    assert 'cp "$0" "$_self"' in t and 'exec bash "$_self"' in t
    assert 'mv "$LOG" "$LOG.prev-' in t
    assert "git status --porcelain --untracked-files=no" in t
    assert "git pull -q --ff-only origin main" in t
    assert 'HETZNER_MODELDIFF_PULLED=1 HETZNER_MODELDIFF_SELF= exec bash "$REPO_DIR/scripts/hetzner_modeldiff.sh"' in t
    # 守門排在比對之前
    call = t.index("python3 scripts/model_diff.py")
    for g in ('"$last" != "== replay exit 0"', '[ -e "$REPLAY_MARK" ]', '[ -f "$OLD_DB" ]'):
        assert g in t and t.index(g) < call, g
    assert "REPLAY_LOG=${HETZNER_MODELDIFF_REPLAY_LOG:-cache/logs/replay-adj.log}" in t
    assert "REPLAY_MARK=${HETZNER_MODELDIFF_REPLAY_MARK:-cache/logs/replay.started}" in t
    assert "OLD_DB=${HETZNER_MODELDIFF_OLD_DB:-cache/scores_pre68.db}" in t


def test_pushes_report_only_not_db():
    m = re.search(r"^\s*git add .*$", text(), re.M)
    assert m and "runs/modeldiff/report_" in m.group(0) and ".db" not in m.group(0), m.group(0) if m else "找不到 git add"
    assert 'br="hetzner/modeldiff-$1"' in text()


def test_expect_sha_uses_verify_and_quiet():
    m = re.search(r"expect=\$\(.*\)$", text(), re.M)
    assert m and "--verify" in m.group(0) and "-q" in m.group(0)


# ---------------------------------------------------------------------------
# 乾跑
# ---------------------------------------------------------------------------
STUB_PY = r'''#!@@PY@@
"""假的 python3：只接 model_diff.py 一種呼叫。"""
import os, pathlib, sys
a = sys.argv[1:]
if a and a[0].endswith("model_diff.py"):
    pathlib.Path(os.environ["STUB_CALLED"]).write_text(" ".join(a), encoding="utf-8")
    rc = int(os.environ.get("STUB_RC", "0"))
    if rc in (0, 1):
        out = pathlib.Path(a[a.index("--out") + 1])
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text('{"result_rc": %d}' % rc, encoding="utf-8")
        trc = int(os.environ.get("STUB_TXT_RC", str(rc)))
        out.with_suffix(".txt").write_text("report\n結果：rc=%d（x）\n" % trc, encoding="utf-8")
    raise SystemExit(rc)
raise SystemExit(f"stub: 未預期的呼叫：{a[:3]}")
'''


def _git(cwd, *args):
    subprocess.run(["git", *args], cwd=cwd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def _sandbox(tmp_path, *, replay_last="== replay exit 0", mark=False, old_db=True):
    origin = tmp_path / "origin.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(origin)], check=True)
    repo = tmp_path / "repo"
    (repo / "scripts").mkdir(parents=True)
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.email", "t@example.invalid")
    _git(repo, "config", "user.name", "t")
    (repo / "README.md").write_text("x\n", encoding="utf-8")
    (repo / "scripts" / "hetzner_modeldiff.sh").write_bytes(SH.read_bytes())
    _git(repo, "add", "README.md", "scripts/hetzner_modeldiff.sh")
    _git(repo, "commit", "-qm", "init")
    _git(repo, "remote", "add", "origin", str(origin))
    _git(repo, "push", "-q", "-u", "origin", "main")
    (repo / "cache" / "logs").mkdir(parents=True)
    (repo / "cache" / "scores.db").write_text("db", encoding="utf-8")
    if old_db:
        (repo / "cache" / "scores_pre68.db").write_text("db", encoding="utf-8")
    if replay_last is not None:
        (repo / "cache" / "logs" / "replay-adj.log").write_text(f"== 2 replay\nstuff\n{replay_last}\n\n", encoding="utf-8")
    if mark:
        (repo / "cache" / "logs" / "replay.started").write_text("sha\n", encoding="utf-8")
    stub = tmp_path / "stub"
    stub.mkdir()
    (stub / "python3").write_text(STUB_PY.replace("@@PY@@", sys.executable), encoding="utf-8")
    (stub / "python3").chmod(0o755)
    # git 包一層：記下每次呼叫的子命令再交給真的 git（驗「重播未完成時不做任何 git fetch／pull／checkout」）
    (stub / "git").write_text(f'#!/bin/sh\nprintf \'%s\\n\' "$*" >> "{tmp_path}/git.calls"\nexec {shutil.which("git")} "$@"\n',
                              encoding="utf-8")
    (stub / "git").chmod(0o755)
    return repo, origin, stub


def _run(repo, stub, tmp_path, **env):
    e = dict(os.environ, PATH=f"{stub}{os.pathsep}{os.environ['PATH']}", STUB_CALLED=str(tmp_path / "called"), **env)
    for k in [k for k in e if k.startswith("HETZNER_MODELDIFF_")]:
        if k not in env:
            del e[k]
    return subprocess.run(["bash", "scripts/hetzner_modeldiff.sh"], cwd=repo, env=e, capture_output=True, text=True, timeout=180)


def _branches(origin):
    out = subprocess.run(["git", "ls-remote", "--heads", str(origin)], capture_output=True, text=True, check=True).stdout
    return sorted(ln.split("refs/heads/")[-1] for ln in out.splitlines() if ln.strip())


def _git_calls(tmp_path):
    f = tmp_path / "git.calls"
    return f.read_text(encoding="utf-8").splitlines() if f.exists() else []


def _days():
    return {dt.datetime.now(dt.timezone.utc).date().isoformat()}


def _log_last(repo):
    lines = [ln for ln in (repo / "cache" / "logs" / "modeldiff.log").read_text(encoding="utf-8").splitlines() if ln.strip()]
    return lines[-1]


needs_git = pytest.mark.skipif(not shutil.which("git"), reason="需要 git")


@needs_git
def test_happy_path_rc0_pushes_report(tmp_path):
    days = _days()
    repo, origin, stub = _sandbox(tmp_path)
    r = _run(repo, stub, tmp_path)
    days |= _days()
    assert r.returncode == 0, r.stdout + r.stderr
    br = [b for b in _branches(origin) if b != "main"]
    assert len(br) == 1 and br[0] in {f"hetzner/modeldiff-{d}" for d in days}
    files = subprocess.run(["git", "ls-tree", "-r", "--name-only", br[0]], cwd=origin, capture_output=True, text=True).stdout.split()
    assert all(not f.endswith(".db") for f in files) and any(f.startswith("runs/modeldiff/report_") for f in files)
    assert _log_last(repo).startswith("== modeldiff exit 0")
    assert "--new cache/scores.db --old cache/scores_pre68.db" in (tmp_path / "called").read_text(encoding="utf-8")


@needs_git
def test_violation_rc1_still_pushes_and_marks_log(tmp_path):
    repo, origin, stub = _sandbox(tmp_path)
    r = _run(repo, stub, tmp_path, STUB_RC="1")
    assert r.returncode == 1, r.stdout + r.stderr
    assert any(b.startswith("hetzner/modeldiff-") for b in _branches(origin)), "rc=1 必須推報告"
    last = _log_last(repo)
    assert last.startswith("== modeldiff exit 1") and "不變式違反" in last


@needs_git
def test_tool_rc2_pushes_nothing(tmp_path):
    repo, origin, stub = _sandbox(tmp_path)
    r = _run(repo, stub, tmp_path, STUB_RC="2")
    assert r.returncode == 2 and _branches(origin) == ["main"]
    assert "未推送報告" in _log_last(repo)


@needs_git
def test_report_result_line_mismatch_rc3(tmp_path):
    repo, origin, stub = _sandbox(tmp_path)
    r = _run(repo, stub, tmp_path, STUB_RC="1", STUB_TXT_RC="0")
    assert r.returncode == 3 and _branches(origin) == ["main"]


@needs_git
@pytest.mark.parametrize("kw", [
    {"replay_last": "== replay exit 1"},
    {"replay_last": "== replay exit 0 but not really"},
    {"replay_last": "== 2 replay_scores --resume"},
    {"replay_last": None},                              # 沒有 log
    {"mark": True},                                     # 標記檔仍在＝重播未完成
    {"old_db": False},                                  # #68 前備份不在
])
def test_guards_stop_before_running(tmp_path, kw):
    repo, origin, stub = _sandbox(tmp_path, **kw)
    r = _run(repo, stub, tmp_path)
    assert r.returncode == 2, r.stdout + r.stderr
    assert not (tmp_path / "called").exists(), "守門不過卻跑了 model_diff"
    assert _branches(origin) == ["main"]
    assert _log_last(repo).startswith("== modeldiff exit 2")
    if "old_db" not in kw:
        # 重播未完成：第 0 步之前就要停——一次 git 都不准叫（尤其 fetch／pull／checkout）
        assert _git_calls(tmp_path) == [], _git_calls(tmp_path)
        assert "== 0 同步 main" not in r.stdout


@needs_git
def test_replay_guard_rechecked_after_pull(tmp_path):
    """重播已完成才會進第 0 步（git 有被叫到）；pull 之後守門再跑一次（輸出兩次「重播已完成」）。"""
    repo, origin, stub = _sandbox(tmp_path)
    r = _run(repo, stub, tmp_path)
    assert r.returncode == 0, r.stdout + r.stderr
    calls = _git_calls(tmp_path)
    assert any(c.startswith("pull") for c in calls) and any(c.startswith("fetch") for c in calls)
    assert r.stdout.count("== 重播已完成：") == 2
    assert r.stdout.index("== 0a 重播已完成守門") < r.stdout.index("== 0 同步 main")


def test_static_replay_guard_before_any_git():
    t = text()
    body = t[t.index("body() {"):]
    first_guard = body.index("replay_done_guard || return 2")
    for g in ("git reset", "git fetch", "git checkout", "git pull"):
        assert first_guard < body.index(g), g


@needs_git
def test_git_failure_stops_before_running(tmp_path):
    """同 test_hetzner_t717 的理由：`body` 開頭的 `set -e` 拿掉，`git pull --ff-only` 失敗會被無視。"""
    repo, origin, stub = _sandbox(tmp_path)
    other = tmp_path / "other"
    subprocess.run(["git", "clone", "-q", str(origin), str(other)], check=True)
    _git(other, "config", "user.email", "o@example.invalid")
    _git(other, "config", "user.name", "o")
    (other / "b.txt").write_text("b\n", encoding="utf-8")
    _git(other, "add", "b.txt")
    _git(other, "commit", "-qm", "origin side")
    _git(other, "push", "-q", "origin", "main")
    (repo / "c.txt").write_text("c\n", encoding="utf-8")
    _git(repo, "add", "c.txt")
    _git(repo, "commit", "-qm", "local side")
    r = _run(repo, stub, tmp_path)
    assert r.returncode != 0 and not (tmp_path / "called").exists()
    assert _branches(origin) == ["main"]


@needs_git
def test_head_advance_reexecs_new_version(tmp_path):
    repo, origin, stub = _sandbox(tmp_path)
    other = tmp_path / "other"
    subprocess.run(["git", "clone", "-q", str(origin), str(other)], check=True)
    _git(other, "config", "user.email", "o@example.invalid")
    _git(other, "config", "user.name", "o")
    p = other / "scripts" / "hetzner_modeldiff.sh"
    p.write_text(p.read_text(encoding="utf-8").replace('  echo "== 1 開跑前守門"', '  echo "== NEWVERSION"\n  echo "== 1 開跑前守門"', 1),
                 encoding="utf-8")
    _git(other, "commit", "-qam", "newer script")
    _git(other, "push", "-q", "origin", "main")
    r = _run(repo, stub, tmp_path)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "改用新版重新執行" in r.stdout and "== NEWVERSION" in r.stdout
    assert r.stdout.count("== 1 開跑前守門") == 1, "舊版不得在前進後繼續跑守門"
