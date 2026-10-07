"""統計層的結構守門（S1-10／S1-11 ＋ `H_BY_HORIZON` 互核）：
不 import `iching.score`；不讀 `data/backtest/`；`cost`／`constants`／`verdict` 在沒有 numpy 時也能 import
（`scripts/rank_table.py` 反向依賴的前提）；程式與測試無交易方向字樣；`H_BY_HORIZON` 與 `scripts/export_dataset.py` 相同。"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

from iching.stats import constants

ROOT = Path(__file__).resolve().parents[1]

PKG = ROOT / "src" / "iching" / "stats"
PKG_FILES = sorted(PKG.glob("*.py"))
#: 只守本層的測試檔；`tests/test_stats_appendix.py` 是 §16.5 分數分布量測附錄（PR-D1 既有），同前綴但不屬統計層。
TEST_FILES = sorted(p for p in ROOT.glob("tests/test_stats_*.py") if p.name != "test_stats_appendix.py")
FORBIDDEN = ("買", "賣", "做多", "做空")


def test_package_files_present():
    names = {p.name for p in PKG_FILES}
    assert {"__init__.py", "boot.py", "ic.py", "windows.py", "cost.py", "groups.py", "metrics.py",
            "verdict.py", "constants.py"} <= names
    assert len(TEST_FILES) >= 7


def test_no_score_import_and_no_backtest_data_path():
    for p in PKG_FILES:
        txt = p.read_text(encoding="utf-8")
        assert not re.search(r"^\s*(from|import)\s+iching\.score", txt, re.MULTILINE), p.name
        assert not re.search(r"^\s*from\s+iching\s+import\s+score", txt, re.MULTILINE), p.name
        assert "data/backtest" not in txt and "backtest/" not in txt, p.name
    for p in TEST_FILES:
        if p.name != "test_stats_hygiene.py":          # 本檔自己寫了這個字串
            assert "data/backtest" not in p.read_text(encoding="utf-8"), p.name
    # 子套件只依賴 numpy／標準庫／iching.config
    for p in PKG_FILES:
        for m in re.findall(r"^\s*(?:from|import)\s+([\w.]+)", p.read_text(encoding="utf-8"), re.MULTILINE):
            top = m.split(".")[0]
            assert top in {"numpy", "iching", "__future__", "dataclasses", "collections", "math", "bisect"} or m.startswith("."), (p.name, m)
            if top == "iching":
                assert m.startswith(("iching.config", "iching.stats")), (p.name, m)


def test_no_trade_direction_wording():
    for p in PKG_FILES + TEST_FILES:
        txt = p.read_text(encoding="utf-8")
        if p.name == "test_stats_hygiene.py":
            txt = txt.replace('FORBIDDEN = ("買", "賣", "做多", "做空")', "")
        for w in FORBIDDEN:
            assert w not in txt, (p.name, w)


def test_h_by_horizon_matches_export_dataset():
    sys.path.insert(0, str(ROOT / "scripts"))
    import export_dataset
    assert constants.H_BY_HORIZON == export_dataset.H_BY_HORIZON == {"short": 10, "swing": 20, "mid": 40}
    assert constants.H_BY_HORIZON is not export_dataset.H_BY_HORIZON


def test_cost_constants_verdict_import_without_numpy():
    """`scripts/rank_table.py` 只用標準庫：它反向依賴的 `iching.stats.cost` 鏈在 numpy 缺席時仍要能 import。"""
    src = str(ROOT / "src")
    code = (f"import sys; sys.modules['numpy'] = None; sys.path.insert(0, {src!r}); "
            "from iching.stats import cost, constants, verdict; import iching.stats; "
            "print(repr(cost.net_ret_long(0.0)), constants.NBOOT, verdict.verdict(7, 1, 1, 1, 1, 1, 1, True, True, False))")
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True)
    assert out.stdout.strip() == "-0.009810371517992134 1000 insufficient"
    code2 = f"import sys; sys.modules['numpy'] = None; sys.path.insert(0, {src!r}); import iching.stats.boot"
    assert subprocess.run([sys.executable, "-c", code2], capture_output=True, text=True, check=False).returncode != 0, \
        "boot 應需要 numpy（確認上面那條 subprocess 的 numpy 封鎖真的生效）"
