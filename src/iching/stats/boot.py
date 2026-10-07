"""循環區塊 bootstrap 與 Newey-West 標準誤（§1.3 `:268-270`、§1.4 `:278`）。

`block_boot_ci`／`nw_se` 兩支**借自 shihpc/taiwan-backtest `676c69b` `audit/run_research.py:117-129`／`:132-140`，
函式本體逐字、語意不改**；唯一改動＝拿掉該檔模組級預設常數（`SEED, B, BLOCK = 42, 2000, 10`，`:14`），
`block`／`nboot`／`lag` 改成必填、`seed` 預設 `DEFAULT_SEED`。登錄書要的 `block=max(21,3h)`、`nboot=1,000`
由下方包裝層 `boot_ci`／`nw_t` 傳入；守門（空陣列、全 NaN、`lag >= n`）也放包裝層、**不改借用本體**。

借用本體的兩個已知性質（寫進報告的方法欄，不是缺陷）：
- `nw_se` 的自協方差分母是 `n−l`（`np.mean(x[l:]*x[:-l])`），非教科書的 `n`；Bartlett 權重 `1−l/(L+1)`。
- `block_boot_ci` 的區間取 `np.percentile(means, [2.5, 97.5])`（線性內插）；`n < 2·block` 回 `(nan, nan)`——
  那是「不到 2 個區塊」的守門，**與登錄書 <8 的門檻不同層**（後者用 §1.4 T5 公式 `block_count` 算，不用 n）。
"""
from __future__ import annotations

import numpy as np

from .constants import BLOCKS_MIN, DEFAULT_SEED, NBOOT, block_len


def block_boot_ci(x, block, nboot, seed=DEFAULT_SEED):
    """借自 shihpc/taiwan-backtest `676c69b` `audit/run_research.py:117-129`，語意不改。

    循環區塊 bootstrap：`nb=ceil(n/block)` 個起點均勻抽於 `[0,n)`，每區塊取連續 `block` 個索引 `% n`
    （故為 circular），拼接截到 `n` 個取均值，重複 `nboot` 次，回 2.5／97.5 百分位。
    `n < 2·block` 回 `(nan, nan)`。`seed` 預設 42＝登錄書未定、沿借用來源 `:14`。
    """
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


def nw_se(x, lag):
    """借自 shihpc/taiwan-backtest `676c69b` `audit/run_research.py:132-140`，語意不改。

    Newey-West（Bartlett 權重）均值標準誤：`lag=0` 時退化為 `std(ddof=0)/√n`。
    無 `lag >= n` 守門（那會對空陣列取 mean）——守門在 `nw_t`。
    """
    x = np.asarray(x, float) - np.mean(x)
    n = len(x)
    g0 = np.mean(x * x)
    v = g0
    for l in range(1, lag + 1):
        w = 1 - l / (lag + 1)
        v += 2 * w * np.mean(x[l:] * x[:-l])
    return np.sqrt(max(v, 0) / n)


# ---------------------------------------------------------------- 包裝層（守門在這裡，不改借用本體）

def _clean_series(x, what: str) -> np.ndarray | None:
    """回 float 陣列；空陣列或全 NaN 回 `None`（呼叫端據此回 nan）；**部分** NaN 直接拒絕——
    日序列中間缺值會破壞區塊連續性，該由上游（`ic.daily_ic` 的丟棄計數）處理，不在這裡靜默跳過。"""
    arr = np.asarray(x, float).ravel()
    if arr.size == 0:
        return None
    finite = np.isfinite(arr)
    if not finite.any():
        return None
    if not finite.all():
        raise ValueError(f"{what}：序列含 {int((~finite).sum())} 個非有限值，請先在上游處理")
    return arr


def boot_ci(x, h: int, nboot: int = NBOOT, seed: int = DEFAULT_SEED) -> tuple[float, float]:
    """登錄書口徑的 bootstrap 95% 區間：`block=block_len(h)`、`nboot=NBOOT`（1,000）。空／全 NaN 回 `(nan, nan)`；
    `nboot <= 0` 拒收（PR-S1 審查遺留，2026-10-07 補）。"""
    if nboot <= 0:
        raise ValueError(f"boot_ci：nboot={nboot} 必須為正整數（登錄書 1,000；借用本體對 nboot=0 會 IndexError）")
    arr = _clean_series(x, "boot_ci")
    if arr is None:
        return (float("nan"), float("nan"))
    lo, hi = block_boot_ci(arr, block_len(h), nboot, seed)
    return (float(lo), float(hi))


def nw_se_guarded(x, lag: int) -> float:
    """`nw_se` 加守門：空／全 NaN 回 nan；`lag < 0` 或 `lag >= n` 拒絕（借用本體會對空切片取 mean）。"""
    arr = _clean_series(x, "nw_se")
    if arr is None:
        return float("nan")
    if lag < 0 or lag >= arr.size:
        raise ValueError(f"nw_se：lag={lag} 必須在 [0, n={arr.size}) 內")
    return float(nw_se(arr, lag))


def nw_t(x, lag: int) -> float:
    """NW t ＝ `mean(x) / nw_se(x, lag)`（§1.4 `:278`：lag 起點＝h，另報 2h）。標準誤為 0（常數序列）回 nan。"""
    arr = _clean_series(x, "nw_t")
    if arr is None:
        return float("nan")
    se = nw_se_guarded(arr, lag)
    if not (se > 0):
        return float("nan")
    return float(np.mean(arr) / se)


def block_count(n_days: int, h: int, embargo: int) -> float:
    """「區塊數量近似值」＝有效日數 ÷ `block_len(h)`，有效日數＝段交易日數 − h − embargo（§1.4 裁定 T5 `:288-289`）。

    **這是登錄書寫死的公式**，與 `windows.purge_mask` 的索引法實際排除數差 1 日（索引法排 h+1 個訊號日）；
    兩個數字報告都要列、都不可改（`plan_stats_layer.md` §A.2 #17）。判 `< BLOCKS_MIN` 用的是本函式。
    """
    if n_days < 0 or h <= 0 or embargo < 0:
        raise ValueError(f"block_count：n_days={n_days}, h={h}, embargo={embargo}")
    return (n_days - h - embargo) / block_len(h)


def insufficient(n_blocks: float) -> bool:
    """§1.3 `:270`：區塊數量近似值 < 8 → 一律「證據不足」。NaN 也視為不足（算不出就不能宣稱足夠）。"""
    return not (n_blocks >= BLOCKS_MIN)
