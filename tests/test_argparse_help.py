"""所有用 argparse 的 scripts/*.py 都要能 `--help`（rc 0）。

起因（2026-09-20 Hetzner 首跑 `hetzner_calib.sh`）：`calibrate_d.py` 的 `--gate` help 寫「（%）」，Python 3.14 的 argparse 在
`add_argument` 當下就檢查 help 字串格式（`_check_help` → `ValueError: badly formed help string`），3.12 只在印 help 時才炸——
CI／本機 3.12 全綠、Hetzner 3.14 第 2 步啟動即死，6.6 小時的 dump 白等一輪報告。`--help` 會走同一段 % 格式化，3.12 也抓得到。
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = sorted(p for p in (ROOT / "scripts").glob("*.py") if "argparse" in p.read_text(encoding="utf-8"))


@pytest.mark.parametrize("path", SCRIPTS, ids=[p.name for p in SCRIPTS])
def test_script_help_rc0(path: Path):
    r = subprocess.run([sys.executable, str(path), "--help"], capture_output=True, text=True, cwd=ROOT, timeout=120)
    assert r.returncode == 0, f"{path.name} --help rc={r.returncode}\n{r.stderr[-800:]}"
    assert "badly formed help string" not in r.stderr
