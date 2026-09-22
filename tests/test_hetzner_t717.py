"""`scripts/hetzner_t717.sh` 的結構與守門測試（`docs/P3-CALIBRATION.md` §19／§20）。

這支腳本會跑 12.6 小時，且它的前側 db **不是生產資料**。所以守的重點是：
①後側 db 不是現行碼算的就別開跑（否則 12.6 小時比出來的差異不只是 d 縮放）
②前側一定要帶 `--uncalibrated`、一定要寫到非生產路徑
③推上去的分支**只放報告、不放 db**。
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SH = ROOT / "scripts" / "hetzner_t717.sh"


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
