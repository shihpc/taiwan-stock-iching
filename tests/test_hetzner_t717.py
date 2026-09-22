"""`scripts/hetzner_t717.sh` 的結構與守門測試（`docs/P3-CALIBRATION.md` §19／§20）。

這支腳本會跑 12.6 小時，且它的前側 db **不是生產資料**。所以守的重點是：
①後側 db 不是現行碼算的就別開跑（否則 12.6 小時比出來的差異不只是 d 縮放）
②前側一定要帶 `--uncalibrated`、一定要寫到非生產路徑
③推上去的分支**只放報告、不放 db**。
"""
from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SH = ROOT / "scripts" / "hetzner_t717.sh"
SCRIPT = SH


def text() -> str:
    return SH.read_text(encoding="utf-8")


def test_syntax_ok():
    subprocess.run(["bash", "-n", str(SH)], check=True)


def test_front_side_always_uncalibrated_and_never_production_path():
    t = text()
    calls = re.findall(r"python3 scripts/replay_scores\.py[^\n]*", t)
    assert len(calls) == 2, f"預期恰兩處重播呼叫（--rebuild／--resume），實得 {len(calls)}"
    for c in calls:
        assert "--uncalibrated" in c, f"前側重播沒帶 --uncalibrated：{c}"
        assert '--out "$BEFORE_DB"' in c, f"前側重播沒寫到 BEFORE_DB：{c}"
    # 預設路徑不得是生產 db
    assert 'BEFORE_DB=${HETZNER_T717_BEFORE_DB:-cache/scores_t717_before.db}' in t
    assert "cache/scores_t717_before.db" != "cache/scores.db"


def test_gates_after_side_with_check_params_before_burning_12_hours():
    t = text()
    assert "ED.check_params" in t, "後側沒有血統守門——12.6 小時可能比錯對象"
    assert t.index("ED.check_params") < t.index("== 2 前側重播"), "守門必須排在重播之前"


def test_pushes_report_only_not_db():
    t = text()
    m = re.search(r"^git add .*$", t, re.M)
    assert m, "找不到 git add"
    line = m.group(0)
    assert "runs/t717/report_" in line
    assert ".db" not in line, f"分支不得放 db：{line}"
    assert re.search(r'BR="hetzner/t717-\$\{TO\}"', t)


def test_expect_sha_uses_verify_and_quiet():
    """分支尚不存在於 origin 時 EXPECT 必須是 40 個 0（同另三支的既有教訓）。"""
    m = re.search(r"^EXPECT=\$\(.*\)$", text(), re.M)
    assert m and "--verify" in m.group(0) and "-q" in m.group(0), m.group(0) if m else "找不到 EXPECT"


def test_mark_file_binds_fingerprint():
    t = text()
    assert 'head -n 1 "$MARK"' in t and '"$sha"' in t, "標記檔沒綁指紋，換了參數會沿用錯的進度"


def test_report_not_pushed_when_replay_failed():
    t = text()
    assert 'if [ "$rc" != "0" ]; then' in t and "未推送報告" in t
    assert t.index('if [ "$rc" != "0" ]; then') < t.index("git checkout -q -B"), "失敗時仍會推分支"


# ---------------------------------------------------------------------------
# 行為測試：真的把腳本跑一遍（git 與 python3 都是本機的／假的），證明失敗時**不會**推報告。
#
# 為什麼不能只留上面那支文字斷言：`body` 跑在 pipeline 左側的子 shell，而外層為了拿
# `PIPESTATUS` 關掉了 errexit——於是 `replay_scores.py` 失敗時 `body` 會繼續走到步驟 3、
# 用半套資料產一份報告、rc 仍是 0，第 4 步照推。`if [ "$rc" != "0" ]` 那行從頭到尾都在，
# 文字斷言全綠（2026-09-22 驗收抓到）。
# ---------------------------------------------------------------------------
import os  # noqa: E402
import sys  # noqa: E402

import pytest  # noqa: E402

# **shebang 必須是真直譯器的絕對路徑**：stub 自己就叫 `python3` 又排在 PATH 最前面，
# 寫 `#!/usr/bin/env python3` 會讓它遞迴呼叫自己、整支測試掛住（2026-09-22 實測踩過）。
STUB_PY = r'''#!@@PY@@
"""假的 python3：依呼叫形狀分派，讓腳本走完全程而不需要真的 12.6 小時。"""
import os, pathlib, sys

a = sys.argv[1:]
if a and a[0] == "-":                                   # 步驟 1 的血統守門（heredoc）
    sys.stdin.read()
    print("== 後側 OK：data_version=dv params_sha=sha 已落地 1 日")
    raise SystemExit(0)
if a and a[0] == "-c":
    code = a[1]
    if "json.load" in code:
        print("320"); raise SystemExit(0)
    if "build_params" in code:
        print("twse=aaaaaaaaaaaa,tpex=bbbbbbbbbbbb calibrated=False"); raise SystemExit(0)
    if "ScoreStore" in code:
        print(os.environ.get("STUB_TO", "2026-09-19")); raise SystemExit(0)
    raise SystemExit(f"stub: 未預期的 -c：{code[:60]}")
if a and a[0].endswith("replay_scores.py"):
    raise SystemExit(int(os.environ.get("STUB_REPLAY_RC", "0")))
if a and a[0].endswith("revalidate_thresholds.py"):
    rc = int(os.environ.get("STUB_ANALYZE_RC", "0"))
    if rc == 0:
        out = pathlib.Path(a[a.index("--out") + 1])
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text('{"schema": 2}', encoding="utf-8")
        out.with_suffix(".txt").write_text("report\n", encoding="utf-8")
    raise SystemExit(rc)
raise SystemExit(f"stub: 未預期的呼叫：{a[:3]}")
'''


def _git(cwd, *args):
    subprocess.run(["git", *args], cwd=cwd, check=True,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def _sandbox(tmp_path):
    """造一個有 origin 的小 repo，放進真正的腳本與假的 python3。"""
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
    (repo / "scripts" / "hetzner_t717.sh").write_bytes(SCRIPT.read_bytes())
    (repo / "cache").mkdir()
    (repo / "cache" / "scores.db").write_text("db", encoding="utf-8")
    (repo / "cache" / "features.db").write_text("db", encoding="utf-8")
    (repo / "data" / "state").mkdir(parents=True)
    (repo / "data" / "state" / "cross.json").write_text('{"meta":{"window":320}}', encoding="utf-8")

    stub = tmp_path / "stub"
    stub.mkdir()
    (stub / "python3").write_text(STUB_PY.replace("@@PY@@", sys.executable), encoding="utf-8")
    (stub / "python3").chmod(0o755)
    return repo, origin, stub


def _run(repo, stub, **env):
    e = dict(os.environ, PATH=f"{stub}{os.pathsep}{os.environ['PATH']}", **env)
    return subprocess.run(["bash", "scripts/hetzner_t717.sh"], cwd=repo, env=e,
                          capture_output=True, text=True, timeout=180)


def _remote_branches(origin):
    out = subprocess.run(["git", "ls-remote", "--heads", str(origin)],
                         capture_output=True, text=True, check=True).stdout
    return [ln.split("refs/heads/")[-1] for ln in out.splitlines() if ln.strip()]


@pytest.mark.skipif(not shutil.which("git"), reason="需要 git")
def test_happy_path_pushes_report_branch(tmp_path):
    """先證明這條合法路徑本來就會成功——否則下面兩支「失敗不推」會因為別的理由通過。"""
    repo, origin, stub = _sandbox(tmp_path)
    r = _run(repo, stub)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "hetzner/t717-2026-09-19" in _remote_branches(origin)


@pytest.mark.skipif(not shutil.which("git"), reason="需要 git")
def test_replay_failure_gives_rc3_and_pushes_nothing(tmp_path):
    repo, origin, stub = _sandbox(tmp_path)
    r = _run(repo, stub, STUB_REPLAY_RC="1")
    assert r.returncode == 3, f"前側重播失敗必須是 rc=3，實得 {r.returncode}\n{r.stdout}"
    assert "未推送報告" in r.stdout
    assert _remote_branches(origin) == ["main"], "重播失敗卻推了報告分支"


@pytest.mark.skipif(not shutil.which("git"), reason="需要 git")
def test_analysis_failure_gives_rc4_and_pushes_nothing(tmp_path):
    repo, origin, stub = _sandbox(tmp_path)
    r = _run(repo, stub, STUB_ANALYZE_RC="1")
    assert r.returncode == 4, f"分析失敗必須是 rc=4，實得 {r.returncode}\n{r.stdout}"
    assert _remote_branches(origin) == ["main"], "分析失敗卻推了報告分支"

@pytest.mark.skipif(not shutil.which("git"), reason="需要 git")
def test_git_step_failure_stops_before_replay(tmp_path):
    """**這支守的是 `body` 開頭那行 `set -e`**（2026-09-22 二次驗收補）。

    步驟 0 的 git 指令都沒有 `|| return`，全靠 errexit；而 `body` 跑在 pipeline 左側的子 shell、
    外層為了拿 `PIPESTATUS` 先 `set +e`。少了那行 `set -e`，`git pull --ff-only` 失敗會被無視，
    腳本照走步驟 1→4 **把報告推出去**。上一版三支行為測試走的兩條路徑本來就有 `|| return 3/4`，
    與 `set -e` 無關——拿掉它十支全綠（實測），等於這個失效模式零守門。

    測資＝讓本地 main 與 origin/main 分歧（`--ff-only` 必失敗）。
    """
    repo, origin, stub = _sandbox(tmp_path)
    # origin 上多推一個 commit，再把本地 main 重設到另一條路徑 → 兩邊分歧
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

    r = _run(repo, stub)
    assert r.returncode != 0, f"git 步驟失敗必須讓整支非零，實得 {r.returncode}\n{r.stdout}"
    assert "未推送報告" in r.stdout
    assert _remote_branches(origin) == ["main"], "git 步驟失敗卻推了報告分支"
