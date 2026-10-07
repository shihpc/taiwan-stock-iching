"""統計層常數——**全部抄自 `docs/pre-registration.md`（凍結 tag `prereg-v2`），本檔不得自創數字**。只用標準庫。

| 常數 | 值 | 出處 |
|---|---|---|
| `block_len(h)` | `max(21, 3h)` | §1.3 `:269`、§1.4 T5 `:289` |
| `NBOOT` | 1,000 | v1.2.2 §13 `:573`（§0 `:21` 宣告採用）；`docs/P3-KICKOFF.md` 完成定義 #5 |
| `BLOCKS_MIN` | 8 | §1.3 `:270`「區塊數量近似值 < 8 → 一律結論『證據不足』」 |
| `IC_MIN`／`T_MIN` | 0.03／2.0 | §1.4 `:277`（名目值，§1.5 T8） |
| `POS_SHARE_MIN` | 0.60 | §1.4 `:281` 次要① |
| `EMBARGO_DAYS` | 20 | §1.3 `:267`（驗證段／保留段起點後 20 **交易日**；訓練段不扣 `:288`） |
| `H_BY_HORIZON` | short 10／swing 20／mid 40 | §1.2.1 `:87`；與 `scripts/export_dataset.py` 的 `H_BY_HORIZON` 互核（測試守） |
| `DEFAULT_SEED` | 42 | **登錄書未定、沿借用來源** `taiwan-backtest@676c69b audit/run_research.py:14`（`SEED = 42`） |

`EMBARGO_DAYS` 是**交易日索引數**，不是日曆天（§16.5 `:722`）；本套件任何地方都不得用日曆天數換算（grep 日期時間模組名零命中，`tests/test_stats_hygiene.py` 守）。
"""
from __future__ import annotations

NBOOT = 1000
BLOCKS_MIN = 8
IC_MIN = 0.03
T_MIN = 2.0
POS_SHARE_MIN = 0.60
EMBARGO_DAYS = 20
H_BY_HORIZON: dict[str, int] = {"short": 10, "swing": 20, "mid": 40}
DEFAULT_SEED = 42


def block_len(h: int) -> int:
    """循環區塊長度 `max(21, 3h)`（§1.3 `:269`）：h=10→30、20→60、40→120。"""
    if h <= 0:
        raise ValueError(f"h 必須為正整數，得到 {h!r}")
    return max(21, 3 * h)
