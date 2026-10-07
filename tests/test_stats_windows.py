"""`iching.stats.windows`：purge／embargo 索引法（S1-4）。合成 100 日日曆、邊界 60。
突變對照（S1-9）：purge 關掉（`purge_mask` 回全 False）→ `test_purge_indices_h10_boundary60`／`test_eval_mask_combines` 紅。"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

from iching import config as C
from iching.stats import constants, windows

ROOT = Path(__file__).resolve().parents[1]

POS = np.arange(100)


def test_purge_indices_h10_boundary60():
    ex = np.flatnonzero(windows.purge_mask(POS, 10, 60))
    assert ex.tolist() == list(range(49, 60)), "x=i+1+h ≥ 60 ⇔ i ≥ 49；共 11 日"
    assert ex.size == 11 == windows.purged_signal_days_index(10)
    assert np.flatnonzero(windows.purge_mask(POS, 20, 60)).tolist() == list(range(39, 60))
    assert np.flatnonzero(windows.purge_mask(POS, 40, 60)).tolist() == list(range(19, 60))
    assert not windows.purge_mask(POS, 10, 60)[60:].any(), "邊界之後屬下一段，不由本邊界 purge"
    assert not windows.purge_mask(POS, 10, 60)[48], "i=48 → x=59 < 60，可用"
    with pytest.raises(ValueError):
        windows.purge_mask(POS, 0, 60)


def test_embargo_indices_start60():
    assert constants.EMBARGO_DAYS == 20
    ex = np.flatnonzero(windows.embargo_mask(POS, 60))
    assert ex.tolist() == list(range(60, 80))
    assert np.flatnonzero(windows.embargo_mask(POS, 60, n=0)).size == 0
    with pytest.raises(ValueError):
        windows.embargo_mask(POS, 60, n=-1)


def test_eval_mask_combines():
    # 段 [60,100)、h=10、embargo 20、段末邊界 100：embargo 砍 60..79，purge 砍 89..99 → 剩 80..88（9 日）
    keep = np.flatnonzero(windows.eval_mask(POS, 60, 100, 10, constants.EMBARGO_DAYS))
    assert keep.tolist() == list(range(80, 89))
    # 訓練段 [0,60)、h=10、embargo 0：只 purge 49..59 → 剩 0..48（49 日）
    keep = np.flatnonzero(windows.eval_mask(POS, 0, 60, 10, 0))
    assert keep.tolist() == list(range(49))


def test_segment_days_on_repo_calendar():
    cal = json.loads((ROOT / "data" / "calendar_tpe.json").read_text(encoding="utf-8"))["dates"]
    got = {seg: windows.segment_days(cal, a, b) for seg, (a, b) in C.SEGMENTS.items()}
    assert got == {"train": 603, "valid": 368, "holdout": 402}
    p0, p1 = windows.segment_bounds(cal, *C.SEGMENTS["valid"])
    assert cal[p0] == "2023-07-03" and cal[p1 - 1] == "2024-12-31" and cal[p1] == "2025-01-02"
    # 驗證→保留 purge 以索引法：h=10 排除驗證段末 11 個訊號日，首個排除日
    pv = np.arange(p0, p1)
    purged = pv[windows.purge_mask(pv, 10, p1)]
    assert purged.size == 11 and cal[purged[0]] == "2024-12-17"
    with pytest.raises(ValueError):
        windows.segment_bounds(cal, "2024-01-01", "2023-01-01")


def test_no_calendar_day_arithmetic_in_package():
    """§16.5 `:722`：統計層不得用日曆天數；grep 兩個日期時間模組名於 `src/iching/stats/` 零命中（含註解與 docstring）。"""
    for p in sorted((ROOT / "src" / "iching" / "stats").glob("*.py")):
        txt = p.read_text(encoding="utf-8")
        for word in ("timedelta", "datetime"):
            assert word not in txt, (p.name, word)
