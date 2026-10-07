"""採用判定（§1.4 `:274-286`）：主要 → 次要 → 穩健，三類 AND；**先查區塊數量近似值**，`< BLOCKS_MIN`（8）一律
「證據不足」、與其他輸入無關（§1.3 `:270`、裁定 T3 `:299-302`）。只用標準庫。

- 主要（`:277`）：IC 均值 ≥ `IC_MIN`（0.03）且 NW t ≥ `T_MIN`（2.0）。
- 次要（`:281-282`）：①扣成本後頂減底分位組月度為正比例 ≥ `POS_SHARE_MIN`（0.60）；
  ②候選名單對全池等權超額 > 0 且 bootstrap 95% 區間不含 0。
- 穩健（`:284`）：三段主要指標同號、未參與選擇的年度同號、成本敏感度不翻轉——三者由上層算成布林傳入。

任一比較遇 NaN 一律不過（`float('nan') >= x` 為 False）；缺值不能當通過。
"""
from __future__ import annotations

import math

from .constants import BLOCKS_MIN, IC_MIN, POS_SHARE_MIN, T_MIN

INSUFFICIENT = "insufficient"
ADOPTED = "adopted"
REJECTED = "rejected"


def primary_pass(ic_mean: float, nw_t: float) -> bool:
    return ic_mean >= IC_MIN and nw_t >= T_MIN


def secondary_pass(pos_share: float, excess_mean: float, ci_lo: float, ci_hi: float) -> bool:
    if any(math.isnan(v) for v in (pos_share, excess_mean, ci_lo, ci_hi)):
        return False
    ci_excludes_zero = ci_lo > 0.0 or ci_hi < 0.0
    return pos_share >= POS_SHARE_MIN and excess_mean > 0.0 and ci_excludes_zero


def robust_pass(segments_same_sign: bool, years_same_sign: bool, cost_flips: bool) -> bool:
    return bool(segments_same_sign) and bool(years_same_sign) and not cost_flips


def verdict(n_blocks: float, ic_mean: float, nw_t: float, pos_share: float, excess_mean: float,
            ci_lo: float, ci_hi: float, segments_same_sign: bool, years_same_sign: bool,
            cost_flips: bool) -> str:
    """回 `"insufficient"`／`"adopted"`／`"rejected"` 三者之一。"""
    if not (n_blocks >= BLOCKS_MIN):
        return INSUFFICIENT
    ok = (primary_pass(ic_mean, nw_t)
          and secondary_pass(pos_share, excess_mean, ci_lo, ci_hi)
          and robust_pass(segments_same_sign, years_same_sign, cost_flips))
    return ADOPTED if ok else REJECTED
