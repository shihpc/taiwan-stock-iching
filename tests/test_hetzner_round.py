"""`scripts/hetzner_round.sh` 第 2 步接 `scan_features --resume` 的 rc 4（裁定 #73 C，2026-10-09）。

rc 4＝輸入指紋守門發現已寫日的掃描輸入與基準不符、一列都沒寫。照跑下去 replay 與 parity 都建立在過期特徵上，
所以必須在第 3 步（replay）之前 exit 4，且**不得 push 任何分支**。臨時 git repo＋bare origin，scan／replay／parity
都是會記錄 argv 的假貨，第 1 步回補以 `HETZNER_ROUND_SKIP_BACKFILL=1` 跳過（全部離線）。
"""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = "hetzner_round.sh"
FROM, TO = "2026-09-01", "2026-09-14"


def _fake(name: str, rc: int, extra: str = "") -> str:
    return (
        "import pathlib, sys\n"
        f"p = pathlib.Path('cache/logs/{name}_calls.txt')\n"
        "p.parent.mkdir(parents=True, exist_ok=True)\n"
        "p.write_text(' '.join(sys.argv[1:]), encoding='utf-8')\n"
        f"print('fake {name}', *sys.argv[1:])\n"
        f"{extra}"
        f"sys.exit({rc})\n"
    )


def _world(tmp_path: Path, scan_rc: int) -> tuple[Path, Path]:
    work = tmp_path / "work"
    (work / "scripts").mkdir(parents=True)
    (work / "scripts" / SCRIPT).write_text((ROOT / "scripts" / SCRIPT).read_text(encoding="utf-8"), encoding="utf-8")
    (work / "scripts" / "scan_features.py").write_text(_fake("scan", scan_rc), encoding="utf-8")
    (work / "scripts" / "replay_scores.py").write_text(_fake("replay", 0), encoding="utf-8")
    (work / "scripts" / "parity_check.py").write_text(_fake("parity", 0, "print('結果：rc=0')\n"), encoding="utf-8")
    (work / "data" / "state").mkdir(parents=True)
    (work / "data" / "state" / "cross.json").write_text(json.dumps({"meta": {"window": 320}}), encoding="utf-8")
    bare = tmp_path / "origin.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(bare)], check=True)
    for a in (["init", "-q", "-b", "main"], ["config", "user.email", "t@t"], ["config", "user.name", "t"],
              ["remote", "add", "origin", str(bare)], ["add", "-A"], ["commit", "-qm", "w"],
              ["push", "-q", "-u", "origin", "main"]):
        subprocess.run(["git", *a], cwd=work, check=True, capture_output=True)
    return work, bare


def _run(work: Path) -> subprocess.CompletedProcess:
    env = {**os.environ, "HETZNER_ROUND_SKIP_BACKFILL": "1", "HETZNER_ROUND_REPLAY_STATE": ""}
    return subprocess.run(["bash", f"scripts/{SCRIPT}", FROM, TO], cwd=work, capture_output=True, text=True, env=env, check=False)


def _remote_branches(bare: Path) -> list[str]:
    out = subprocess.run(["git", "for-each-ref", "--format=%(refname:short)", "refs/heads"], cwd=bare,
                         capture_output=True, text=True, check=True).stdout
    return sorted(out.split())


def test_scan_rc4_stops_before_replay_and_push(tmp_path):
    work, bare = _world(tmp_path, scan_rc=4)
    r = _run(work)
    out = r.stdout + r.stderr
    assert r.returncode == 4, out
    assert (work / "cache/logs/scan_calls.txt").read_text(encoding="utf-8").startswith("--resume")
    assert not (work / "cache/logs/replay_calls.txt").exists()
    assert not (work / "cache/logs/parity_calls.txt").exists()
    assert _remote_branches(bare) == ["main"]                     # 沒有 push 任何分支
    assert "rc=4" in out and "HETZNER_REPLAY_SCAN=1 bash scripts/hetzner_replay.sh" in out
    log = next((work / "cache" / "logs").glob("parity-round-*.log")).read_text(encoding="utf-8")
    assert "HETZNER_REPLAY_SCAN=1 bash scripts/hetzner_replay.sh" in log   # 訊息也進了 log（session 只看得到 log）


@pytest.mark.parametrize("scan_rc", [1, 2])
def test_other_scan_failures_still_stop(tmp_path, scan_rc):
    work, bare = _world(tmp_path, scan_rc=scan_rc)
    r = _run(work)
    assert r.returncode == scan_rc, r.stdout + r.stderr
    assert not (work / "cache/logs/replay_calls.txt").exists()
    assert _remote_branches(bare) == ["main"]


def test_scan_ok_continues_to_replay_parity_and_push(tmp_path):
    """對照組：rc 0 時照舊走完 replay → parity → push（證明上面兩支的「沒呼叫」不是世界本身跑不動）。"""
    work, bare = _world(tmp_path, scan_rc=0)
    r = _run(work)
    assert r.returncode == 0, r.stdout + r.stderr
    assert (work / "cache/logs/replay_calls.txt").read_text(encoding="utf-8").startswith("--resume --window 320")
    assert (work / "cache/logs/parity_calls.txt").exists()
    assert _remote_branches(bare) == ["hetzner/parity-" + TO, "main"]
