"""`iching.stats.boot`：借用本體逐字（S1-1）、區塊常數與九格區塊數（S1-2）、NW 標準誤（S1-3）。

期待值一律獨立寫死（手算或標準庫算一次），不得由被測函式產生。
突變對照（S1-9，實作者實測、不進 commit）：`block_len` 公式改 → `test_block_len_and_nine_cells` 紅；
`NBOOT` 改 2000 → `test_nboot_is_1000_and_wrapper_passes_it` 紅；`BLOCKS_MIN` 改 4 → `test_insufficient_threshold` 紅。
"""
from __future__ import annotations

import ast
import inspect
import json
import os
from pathlib import Path

import numpy as np
import pytest

from iching import config as C
from iching.stats import boot, constants

ROOT = Path(__file__).resolve().parents[1]      # `src` 已由 tests/conftest.py 進 sys.path

#: 借用本體（shihpc/taiwan-backtest `676c69b` `audit/run_research.py:118-129`／`:133-140`，def 行之後逐字）。
BORROWED_BLOCK_BOOT_BODY = """\
    x = np.asarray(x, float)
    n = len(x)
    if n < block * 2:
        return (np.nan, np.nan)
    rng = np.random.default_rng(seed)
    nb = int(np.ceil(n / block))
    means = np.empty(nboot)
    for i in range(nboot):
        starts = rng.integers(0, n, nb)
        idx = (starts[:, None] + np.arange(block)[None, :]).ravel() % n
        means[i] = x[idx[:n]].mean()
    return tuple(np.percentile(means, [2.5, 97.5]))
"""
BORROWED_NW_SE_BODY = """\
    x = np.asarray(x, float) - np.mean(x)
    n = len(x)
    g0 = np.mean(x * x)
    v = g0
    for l in range(1, lag + 1):
        w = 1 - l / (lag + 1)
        v += 2 * w * np.mean(x[l:] * x[:-l])
    return np.sqrt(max(v, 0) / n)
"""


def _body_after_docstring(fn) -> str:
    """函式原始碼去掉 def 行與 docstring，只剩本體（以 ast 找 docstring 結束行）。"""
    src = inspect.getsource(fn)
    node = ast.parse(src).body[0]
    first = node.body[0]
    has_doc = isinstance(first, ast.Expr) and isinstance(getattr(first, "value", None), ast.Constant) \
        and isinstance(first.value.value, str)
    start = (node.body[1].lineno if has_doc else first.lineno) - 1
    return "".join(line + "\n" for line in src.splitlines()[start:])


# ---------------------------------------------------------------- S1-1 借用本體逐字

def test_borrowed_bodies_verbatim():
    assert _body_after_docstring(boot.block_boot_ci) == BORROWED_BLOCK_BOOT_BODY
    assert _body_after_docstring(boot.nw_se) == BORROWED_NW_SE_BODY
    src = (ROOT / "src" / "iching" / "stats" / "boot.py").read_text(encoding="utf-8")
    assert "676c69b" in src and "audit/run_research.py:117-129" in src and "audit/run_research.py:132-140" in src
    assert "676c69b" in (boot.block_boot_ci.__doc__ or "") and "676c69b" in (boot.nw_se.__doc__ or "")


def test_borrowed_bodies_match_sibling_repo_if_present():
    """有 `TAIWAN_BACKTEST_DIR` 環境變數（指向 shihpc/taiwan-backtest checkout）才跑：對該檔 `:118-129`／`:133-140` 逐字。"""
    d = os.environ.get("TAIWAN_BACKTEST_DIR")
    if not d:
        pytest.skip("TAIWAN_BACKTEST_DIR 未設")
    lines = (Path(d) / "audit" / "run_research.py").read_text(encoding="utf-8").splitlines(keepends=True)
    assert "".join(lines[117:129]) == BORROWED_BLOCK_BOOT_BODY
    assert "".join(lines[132:140]) == BORROWED_NW_SE_BODY


# ---------------------------------------------------------------- S1-2 常數與九格

def test_block_len_and_nine_cells():
    assert [constants.block_len(h) for h in (1, 7, 10, 20, 40)] == [21, 21, 30, 60, 120]
    with pytest.raises(ValueError):
        constants.block_len(0)
    cal = json.loads((ROOT / "data" / "calendar_tpe.json").read_text(encoding="utf-8"))["dates"]
    days = {}
    for seg, (a, b) in C.SEGMENTS.items():
        days[seg] = sum(1 for d in cal if a <= d <= b)
    assert days == {"train": 603, "valid": 368, "holdout": 402}, "登錄書 §1.4 表頭"
    # 登錄書 `:295-297` 九格（有效日數＝n−h−embargo；訓練段 embargo 0、其餘 20），小數兩位
    want = {("train", 10): 19.77, ("train", 20): 9.72, ("train", 40): 4.69,
            ("valid", 10): 11.27, ("valid", 20): 5.47, ("valid", 40): 2.57,
            ("holdout", 10): 12.40, ("holdout", 20): 6.03, ("holdout", 40): 2.85}
    for (seg, h), v in want.items():
        emb = 0 if seg == "train" else constants.EMBARGO_DAYS
        assert round(boot.block_count(days[seg], h, emb), 2) == v, (seg, h)
    # 有效日數本身（`:295-297` 箭頭左側）
    assert days["valid"] - 10 - 20 == 338 and days["train"] - 40 == 563 and days["holdout"] - 20 - 20 == 362


def test_nboot_is_1000_and_wrapper_passes_it(monkeypatch):
    assert constants.NBOOT == 1000 and boot.NBOOT == 1000
    seen = {}

    def spy(x, block, nboot, seed=None):
        seen.update(block=block, nboot=nboot, seed=seed, n=len(x))
        return (0.0, 0.0)

    monkeypatch.setattr(boot, "block_boot_ci", spy)
    boot.boot_ci(np.arange(300.0), h=20)
    assert seen == {"block": 60, "nboot": 1000, "seed": 42, "n": 300}
    assert constants.DEFAULT_SEED == 42


def test_insufficient_threshold():
    assert constants.BLOCKS_MIN == 8
    assert boot.insufficient(7.99) and not boot.insufficient(8.0) and boot.insufficient(float("nan"))


# ---------------------------------------------------------------- S1-3 NW 標準誤與 t

def test_nw_se_lag0_equals_classic_and_lag1_hand_case():
    x = [1.0, 2.0, 3.0, 4.0, 5.0]
    # lag=0：std(ddof=0)/√n ＝ √2/√5 ＝ √0.4
    assert abs(boot.nw_se(x, 0) - 0.6324555320336759) < 1e-12
    assert abs(boot.nw_se_guarded(x, 0) - 0.6324555320336759) < 1e-12
    # lag=1 手算：去均值 [-2,-1,0,1,2]，g0=2，w=0.5，自協方差 (2+0+0+2)/4=1 → v=3 → √(3/5)
    assert abs(boot.nw_se(x, 1) - 0.7745966692414834) < 1e-12
    rng = np.random.default_rng(7)
    z = rng.standard_normal(500)
    assert abs(boot.nw_se(z, 0) - np.std(z, ddof=0) / np.sqrt(500)) < 1e-14


def test_nw_se_ar1_positive_autocorr_inflates_se():
    rng = np.random.default_rng(11)
    e = rng.standard_normal(2000)
    x = np.empty(2000)
    x[0] = e[0]
    for t in range(1, 2000):
        x[t] = 0.5 * x[t - 1] + e[t]
    for h in (10, 20, 40):
        assert boot.nw_se(x, h) > boot.nw_se(x, 0) * 1.3, h


def test_nw_t_definition_and_guards():
    rng = np.random.default_rng(3)
    x = 1.0 + 1e-3 * rng.standard_normal(200)
    assert boot.nw_t(x, 10) > 100.0 and abs(boot.nw_t(x, 10) - np.mean(x) / boot.nw_se(x, 10)) < 1e-9
    z = rng.standard_normal(5000)
    assert abs(boot.nw_t(z - z.mean(), 10)) < 1e-9
    assert np.isnan(boot.nw_t([2.0, 2.0, 2.0], 1))             # 常數序列 se=0 → nan
    assert np.isnan(boot.nw_t([], 0)) and np.isnan(boot.nw_t([np.nan, np.nan], 0))
    with pytest.raises(ValueError):
        boot.nw_se_guarded([1.0, 2.0, 3.0], 3)                 # lag >= n
    with pytest.raises(ValueError):
        boot.nw_t([1.0, np.nan, 3.0], 0)                        # 部分 NaN 拒收


# ---------------------------------------------------------------- bootstrap 行為

def test_block_boot_ci_constant_circular_small_n_and_seed():
    assert boot.block_boot_ci(np.full(100, 3.5), 30, 50) == (3.5, 3.5)
    x = np.arange(1.0, 51.0)                                   # n=50，均值 25.5
    # 循環：block=n/2 時 nb=2、起點可落在 [25,50)，非循環實作會越界；`% n` 在借用本體裡（S1-1 逐字守）
    lo, hi = boot.block_boot_ci(x, 25, 300)
    assert 1.0 <= lo <= 25.5 <= hi <= 50.0
    assert np.isnan(boot.block_boot_ci(x, 26, 20)[0]), "block > n/2 → n < 2·block → nan"
    lo, hi = boot.block_boot_ci(x, 60, 20)
    assert np.isnan(lo) and np.isnan(hi)
    rng = np.random.default_rng(5)
    z = rng.standard_normal(400)
    a = boot.block_boot_ci(z, 30, 300, seed=42)
    b = boot.block_boot_ci(z, 30, 300, seed=42)
    c = boot.block_boot_ci(z, 30, 300, seed=43)
    assert a == b and a != c
    assert a[0] < 0 < a[1], "零均值白噪音 95% 區間應含 0"


def test_boot_ci_wrapper_guards():
    assert all(np.isnan(v) for v in boot.boot_ci([], 10))
    assert all(np.isnan(v) for v in boot.boot_ci([np.nan] * 5, 10))
    assert all(np.isnan(v) for v in boot.boot_ci(np.arange(59.0), 10)), "n=59 < 2×30"
    with pytest.raises(ValueError):
        boot.boot_ci([1.0, np.nan], 10)
    lo, hi = boot.boot_ci(np.full(80, 0.02), 10)
    assert abs(lo - 0.02) < 1e-15 and abs(hi - 0.02) < 1e-15 and lo == hi


def test_block_count_rejects_bad_inputs():
    with pytest.raises(ValueError):
        boot.block_count(100, 0, 0)
    with pytest.raises(ValueError):
        boot.block_count(100, 10, -1)
