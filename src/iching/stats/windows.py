"""purge／embargo／段界——**一律交易日索引法**（§1.3 `:266-267`、§16.5 `:722`「不得拿日曆天數當交易日視窗」）。

訊號日索引 `i`：進場 `e = i+1`、出場 `x = i+1+h`（§1.2.1 `:85-86`、§1.3 `:266`）。
- purge：`x ≥ boundary_pos` 的訊號日排除（出場落在下一段）。邊界緊接段末時排除恰 `h+1` 個訊號日
  （`i ≥ boundary − h − 1`）——比 §1.4 T5 公式的 `h` 多 1，**兩個數字都是凍結文字、報告都列、不互改**
  （`boot.block_count` 照 T5）。
- embargo：段起點後 `EMBARGO_DAYS`（20）個**交易日**的訊號不評估（§1.3 `:267`；訓練段不扣 `:288`）。

本檔不 import 任何日期時間模組；段界用 `bisect` 在排序好的 ISO 日期字串上找位置（字串序＝日期序）。
"""
from __future__ import annotations

from bisect import bisect_left, bisect_right
from collections.abc import Sequence

import numpy as np

from .constants import EMBARGO_DAYS


def segment_bounds(dates: Sequence[str], start: str, end: str) -> tuple[int, int]:
    """段在日曆 `dates`（已排序 ISO 字串）上的半開索引區間 `[pos0, pos1)`，`start`／`end` 皆含（`config.SEGMENTS` 口徑）。
    `end < start` 或段內零交易日（空段）都拒收——後者原本回 `(k, k)` 不拋（PR-S1 審查遺留，2026-10-07 補）。"""
    pos0 = bisect_left(dates, start)
    pos1 = bisect_right(dates, end)
    if pos1 < pos0:
        raise ValueError(f"segment_bounds：end {end} 早於 start {start}")
    if pos1 == pos0:
        raise ValueError(f"segment_bounds：{start}～{end} 在日曆上沒有任何交易日（空段）")
    return pos0, pos1


def segment_days(dates: Sequence[str], start: str, end: str) -> int:
    """段內交易日數（登錄書 §1.4 表頭 603／368／402 即此）。"""
    pos0, pos1 = segment_bounds(dates, start, end)
    return pos1 - pos0


def purge_mask(pos, h: int, boundary_pos: int) -> np.ndarray:
    """True ＝ 該訊號日因 purge 排除：`pos < boundary_pos` 且 `pos + 1 + h >= boundary_pos`。
    `pos >= boundary_pos` 的訊號屬下一段，不由本邊界處理（回 False）。"""
    if h <= 0:
        raise ValueError(f"purge_mask：h={h}")
    p = np.asarray(pos, int)
    return (p < boundary_pos) & (p + 1 + h >= boundary_pos)


def embargo_mask(pos, seg_start_pos: int, n: int = EMBARGO_DAYS) -> np.ndarray:
    """True ＝ 該訊號日落在段起點後前 `n` 個交易日內（`seg_start_pos <= pos < seg_start_pos + n`）。"""
    if n < 0:
        raise ValueError(f"embargo_mask：n={n}")
    p = np.asarray(pos, int)
    return (p >= seg_start_pos) & (p < seg_start_pos + n)


def eval_mask(pos, seg_start_pos: int, seg_end_pos: int, h: int, embargo: int) -> np.ndarray:
    """True ＝ 訊號日可進評估：在段內 `[seg_start_pos, seg_end_pos)`、未被段末邊界 purge、未落在 embargo。
    `embargo` 由呼叫端依段別傳（訓練段 0、驗證／保留段 `EMBARGO_DAYS`）。"""
    p = np.asarray(pos, int)
    in_seg = (p >= seg_start_pos) & (p < seg_end_pos)
    return in_seg & ~purge_mask(p, h, seg_end_pos) & ~embargo_mask(p, seg_start_pos, embargo)


def purged_signal_days_index(h: int) -> int:
    """索引法下，邊界緊接段末時被 purge 的訊號日數＝`h + 1`（供報告與 T5 公式的 `h` 並列揭露）。"""
    if h <= 0:
        raise ValueError(f"purged_signal_days_index：h={h}")
    return h + 1
