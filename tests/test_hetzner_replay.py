"""`scripts/hetzner_replay.sh`（校準後全量重播的一句話貼）的結構與行為測試——驗收條件見
`docs/P3-CALIBRATION.md` §15 的 V1～V6（V7 是全量測試與 ruff，不屬單檔範圍）。

這支腳本存在的理由是 `hetzner_adj.sh` 守門 a 要的 `== replay exit 0` 標記在 repo 裡本來**沒有產生端**
（上一輪是人手打的 tmux 一句話、原文沒進版控）。所以本檔的重點不是「腳本跑得動」，而是：
標記一定是 log 的最後一行、rc 是真的、舊 log 的舊標記不得跨輪沿用、以及四道開跑前守門真的會在
呼叫 `replay_scores` **之前**擋下來（那支在生產是 12.6 小時）。
"""
from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))

SCRIPT = "hetzner_replay.sh"
CALLS = "cache/logs/replay_calls.txt"          # 假 replay_scores 把 argv 寫進這裡
SCAN_CALLS = "cache/logs/scan_calls.txt"       # 假 scan_features 把 argv 寫進這裡（2026-10-09 起預設會跑，裁定 #73 C）

FAKE_UNIVERSE = 'POOL_SEMANTICS = "pit-1"\n'
FAKE_PARAMS = '''
class _P:
    calibrated = True
    def __init__(self, m): self.m = m
    def model_version(self): return "p2-score-engine-1.deadbeef" + self.m


def build_params(market):
    return _P(market)
'''


def _fake_replay(rc: int) -> str:
    return (
        "import pathlib, sys\n"
        f"p = pathlib.Path({CALLS!r})\n"
        "p.parent.mkdir(parents=True, exist_ok=True)\n"
        "p.write_text(' '.join(sys.argv[1:]), encoding='utf-8')\n"
        "print('fake replay_scores', *sys.argv[1:])\n"
        f"sys.exit({rc})\n"
    )


def _fake_scan(rc: int) -> str:
    return (
        "import pathlib, sys\n"
        f"p = pathlib.Path({SCAN_CALLS!r})\n"
        "p.parent.mkdir(parents=True, exist_ok=True)\n"
        "p.write_text(' '.join(sys.argv[1:]), encoding='utf-8')\n"
        "print('fake scan_features', *sys.argv[1:])\n"
        f"sys.exit({rc})\n"
    )


def _world(tmp_path: Path, *, rc: int = 0, features: bool = True, pool: str = "pit-1",
           window: int | None = 320, dirty: bool = False, scan_rc: int = 0) -> Path:
    """臨時 git repo：只放這支腳本跑得到的最小檔案集，replay_scores 是會記錄 argv 的假貨。"""
    work = tmp_path / "work"
    (work / "scripts").mkdir(parents=True)
    (work / "src" / "iching" / "score").mkdir(parents=True)
    (work / "src" / "iching" / "__init__.py").write_text("", encoding="utf-8")
    (work / "src" / "iching" / "score" / "__init__.py").write_text("", encoding="utf-8")
    (work / "src" / "iching" / "universe.py").write_text(f'POOL_SEMANTICS = "{pool}"\n', encoding="utf-8")
    (work / "src" / "iching" / "score" / "params.py").write_text(FAKE_PARAMS, encoding="utf-8")
    (work / "scripts" / SCRIPT).write_text((ROOT / "scripts" / SCRIPT).read_text(encoding="utf-8"), encoding="utf-8")
    (work / "scripts" / "replay_scores.py").write_text(_fake_replay(rc), encoding="utf-8")
    (work / "scripts" / "scan_features.py").write_text(_fake_scan(scan_rc), encoding="utf-8")
    (work / "data" / "state").mkdir(parents=True)
    meta: dict = {"data_version": "fm-x", "params_sha": "old"}
    if window is not None:
        meta["window"] = window
    (work / "data" / "state" / "cross.json").write_text(json.dumps({"meta": meta}), encoding="utf-8")
    (work / "cache" / "logs").mkdir(parents=True)
    if features:
        conn = sqlite3.connect(work / "cache" / "features.db")
        conn.execute("CREATE TABLE scan_meta(data_version TEXT, schema_version INT, params_sha TEXT)")
        conn.execute("INSERT INTO scan_meta VALUES('fm-x', 3, 'feat-sha')")
        conn.commit()
        conn.close()
    bare = tmp_path / "origin.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(bare)], check=True)
    for a in (["init", "-q", "-b", "main"], ["config", "user.email", "t@t"], ["config", "user.name", "t"],
              ["remote", "add", "origin", str(bare)], ["add", "-A"], ["commit", "-qm", "w"],
              ["push", "-q", "-u", "origin", "main"]):
        subprocess.run(["git", *a], cwd=work, check=True, capture_output=True)
    if dirty:
        (work / "data" / "state" / "cross.json").write_text("{}", encoding="utf-8")
    return work


def _run(work: Path, **env_extra) -> subprocess.CompletedProcess:
    import os
    env = {**os.environ, "HETZNER_REPLAY_SELF": "", "HETZNER_REPLAY_REPO": "", "HETZNER_REPLAY_PULLED": "",
           "HETZNER_REPLAY_ROTATED": "", "HETZNER_REPLAY_LOG": "", "HETZNER_REPLAY_MARK": "", "HETZNER_REPLAY_SCAN": "",
           **env_extra}
    return subprocess.run(["bash", f"scripts/{SCRIPT}"], cwd=work, capture_output=True, text=True, env=env)


def _log(work: Path) -> str:
    return (work / "cache" / "logs" / "replay-adj.log").read_text(encoding="utf-8")


def _last_line(text: str) -> str:
    """守門 a 的取法：去掉空白行後的最後一行（`hetzner_adj.sh:73`）。"""
    return [ln for ln in text.splitlines() if ln.strip()][-1]


def _adj_gate_passes(last: str) -> bool:
    """用 `hetzner_adj.sh` 守門 a 的同一段 `case` 判（不複製條件，直接跑那支腳本裡的字面量）。"""
    text = (ROOT / "scripts" / "hetzner_adj.sh").read_text(encoding="utf-8")
    assert '*"== replay exit 0"*)' in text          # 條件字面量沒變才算這個模擬有效
    return subprocess.run(["bash", "-c", 'case "$1" in *"== replay exit 0"*) exit 0 ;; *) exit 2 ;; esac', "_", last]).returncode == 0


# V1：window 取自 cross.json 的 meta.window，與 hetzner_adj.sh 同一路徑同一算法（守門 c 因此結構上必過）
def test_window_source_identical_to_adj():
    expr = "json.load(open('data/state/cross.json'))['meta']['window']"
    mine = (ROOT / "scripts" / SCRIPT).read_text(encoding="utf-8")
    adj = (ROOT / "scripts" / "hetzner_adj.sh").read_text(encoding="utf-8")
    assert expr in mine and expr in adj
    # 傳給 replay_scores 的一律是那個變數，且腳本不收任何位置參數（沒有人手打錯 window 的機會）
    assert mine.count('--window "$window"') == 2 and "--window 320" not in mine
    assert "${1" not in mine and "$1" not in mine.split("body()")[0]


# V2：log 末行恰為標記、rc 是真的；成功的能過守門 a，失敗的不能
@pytest.mark.parametrize("rc", [0, 3])
def test_marker_is_last_line_and_rc_is_real(tmp_path, rc):
    work = _world(tmp_path, rc=rc)
    r = _run(work)
    assert r.returncode == rc, r.stdout + r.stderr
    last = _last_line(_log(work))
    assert last == f"== replay exit {rc}"
    assert _adj_gate_passes(last) is (rc == 0)
    assert (work / CALLS).read_text(encoding="utf-8").startswith("--rebuild --window 320")


# V3：上一輪的舊 log 不得被誤當成這一輪的結果——開跑前既有 log 改名為 <log>.prev-<UTC>
def test_old_log_rotated_so_stale_marker_cannot_pass(tmp_path):
    work = _world(tmp_path, rc=1)
    old = work / "cache" / "logs" / "replay-adj.log"
    old.write_text("上一輪\n== replay exit 0\n", encoding="utf-8")
    r = _run(work)
    assert r.returncode == 1
    last = _last_line(_log(work))
    assert last == "== replay exit 1" and _adj_gate_passes(last) is False
    prev = sorted((work / "cache" / "logs").glob("replay-adj.log.prev-*"))
    assert len(prev) == 1 and "== replay exit 0" in prev[0].read_text(encoding="utf-8")   # 舊的留著、但不在守門讀的那個路徑


# V4：標記檔綁 model_version 指紋——不存在→--rebuild、相同→--resume、不同→--rebuild（不得沿用別輪的進度）
@pytest.mark.parametrize("mark,flag", [(None, "--rebuild"), ("same", "--resume"), ("別的指紋", "--rebuild")])
def test_mark_file_decides_rebuild_or_resume(tmp_path, mark, flag):
    work = _world(tmp_path)
    m = work / "cache" / "logs" / "replay.started"
    if mark == "same":
        m.write_text("twse=p2-score-engine-1.deadbeeftwse,tpex=p2-score-engine-1.deadbeeftpex calibrated=True\n", encoding="utf-8")
    elif mark is not None:
        m.write_text(mark + "\n", encoding="utf-8")
    r = _run(work)
    assert r.returncode == 0, r.stdout + r.stderr
    assert (work / CALLS).read_text(encoding="utf-8").split()[0] == flag
    assert not m.exists()                      # 成功後標記檔刪除


def test_mark_file_survives_failed_run(tmp_path):
    """失敗時標記檔必須留著，否則下一次重貼會從頭 --rebuild（12.6 小時重來）。"""
    work = _world(tmp_path, rc=4)
    assert _run(work).returncode == 4
    assert (work / "cache" / "logs" / "replay.started").exists()


# V5：四道開跑前守門任一不過 → rc 2，且**沒有呼叫過 replay_scores**（features.db 存在檢查 2026-10-09 起只在 opt-out 時擋）
@pytest.mark.parametrize("kw,env,msg", [
    ({"dirty": True}, {}, "工作樹不乾淨"),
    ({"pool": "static"}, {}, "POOL_SEMANTICS 不是 pit-1"),
    ({"features": False}, {"HETZNER_REPLAY_SCAN": "0"}, "cache/features.db 不存在"),
    ({"window": None}, {}, "取不到 meta.window"),
])
def test_preflight_gates_block_before_replay(tmp_path, kw, env, msg):
    work = _world(tmp_path, **kw)
    r = _run(work, **env)
    out = r.stdout + r.stderr
    assert r.returncode == 2, out
    assert msg in out and msg in _log(work)
    assert _last_line(_log(work)) == "== replay exit 2" and not (work / CALLS).exists()
    assert not (work / SCAN_CALLS).exists()            # 守門在 scan 之前（scan 也要 5 分鐘，且會清掉 features.db 重寫）


# V6：pull 後 HEAD 前進時必須改用新版腳本重新執行（同另三支的自我複製骨架；body 在子殼層，exec 只換得掉子殼層，
# 所以本腳本改以旗標檔 .replay-reexec 把重新執行拉回頂層做）
def test_reexecs_new_script_after_pull(tmp_path):
    from test_pit_world import _clone_with_v1_v2

    work, tmpdir = _clone_with_v1_v2(tmp_path, SCRIPT, {
        "src/iching/__init__.py": "", "src/iching/universe.py": FAKE_UNIVERSE,
        "src/iching/score/__init__.py": "", "src/iching/score/params.py": FAKE_PARAMS,
    })
    import os
    env = {**os.environ, "TMPDIR": str(tmpdir), "HETZNER_REPLAY_SELF": "", "HETZNER_REPLAY_REPO": "",
           "HETZNER_REPLAY_PULLED": "", "HETZNER_REPLAY_ROTATED": "", "HETZNER_REPLAY_LOG": "", "HETZNER_REPLAY_MARK": ""}
    r = subprocess.run(["bash", f"scripts/{SCRIPT}"], cwd=work, capture_output=True, text=True, env=env)
    out = r.stdout + r.stderr
    assert r.returncode == 0, out
    assert f"改用新版 scripts/{SCRIPT} 重新執行" in out and "V2-MARKER" in out and "V1-CONTINUED" not in out, out
    assert list(tmpdir.iterdir()) == []                       # 自我複製的暫存檔已清掉
    assert not (work / "cache" / "logs" / ".replay-reexec").exists()
    logs = sorted((work / "cache" / "logs").glob("replay-adj.log"))
    assert len(logs) == 1 and _last_line(logs[0].read_text(encoding="utf-8")) == "== replay exit 0"


# 裁定 #73 C（2026-10-09）：預設先 scan_features --rebuild 再重播（原「預設不重掃」斷言翻轉）
def test_scan_features_rebuild_runs_by_default_before_replay(tmp_path):
    work = _world(tmp_path)
    r = _run(work)
    assert r.returncode == 0, r.stdout + r.stderr
    assert (work / SCAN_CALLS).read_text(encoding="utf-8").startswith("--rebuild")
    assert (work / CALLS).read_text(encoding="utf-8").startswith("--rebuild --window 320")
    log = _log(work)
    assert log.index("fake scan_features") < log.index("fake replay_scores")
    assert _last_line(log) == "== replay exit 0"


def test_opt_out_skips_scan_with_warning(tmp_path):
    work = _world(tmp_path)
    r = _run(work, HETZNER_REPLAY_SCAN="0")
    assert r.returncode == 0, r.stdout + r.stderr
    assert not (work / SCAN_CALLS).exists()
    assert "!! HETZNER_REPLAY_SCAN=0" in _log(work)
    assert (work / CALLS).read_text(encoding="utf-8").startswith("--rebuild --window 320")


def test_scan_failure_stops_before_replay(tmp_path):
    work = _world(tmp_path, scan_rc=2)
    r = _run(work)
    assert r.returncode == 2, r.stdout + r.stderr
    assert (work / SCAN_CALLS).exists() and not (work / CALLS).exists()
    log = _log(work)
    assert "scan_features --rebuild 失敗 rc=2" in log
    assert _last_line(log) == "== replay exit 2" and _adj_gate_passes(_last_line(log)) is False
    assert not (work / "cache" / "logs" / "replay.started").exists()   # 標記檔沒寫：重貼同一行從 scan 重來


def test_resume_path_does_not_rescan(tmp_path):
    work = _world(tmp_path)
    (work / "cache" / "logs" / "replay.started").write_text(
        "twse=p2-score-engine-1.deadbeeftwse,tpex=p2-score-engine-1.deadbeeftpex calibrated=True\n", encoding="utf-8")
    r = _run(work)
    assert r.returncode == 0, r.stdout + r.stderr
    assert not (work / SCAN_CALLS).exists()
    assert (work / CALLS).read_text(encoding="utf-8").startswith("--resume --window 320")
    assert "續跑" in _log(work) and "不重掃" in _log(work)


def test_missing_features_db_is_fine_by_default(tmp_path):
    """預設會先重建，features.db 不存在不擋（只在 HETZNER_REPLAY_SCAN=0 時擋，見 V5）。"""
    work = _world(tmp_path, features=False)
    r = _run(work)
    assert r.returncode == 0, r.stdout + r.stderr
    assert (work / SCAN_CALLS).exists() and (work / CALLS).exists()
    assert _last_line(_log(work)) == "== replay exit 0"
