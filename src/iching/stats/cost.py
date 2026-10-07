"""交易成本（§1.2.1 `:91` 個股、§1.2.4 `:219-263` 大盤 TX）。**只用標準庫**——`scripts/rank_table.py` 反向 import
本模組（該腳本的「只用標準庫」慣例，不得被拖進 numpy）。所有回傳值都是**比率（小數）**，0.0107% 寫成 1.07e-4。

個股費率（裁定 #54 Q6 定費率、#57 Q9 定乘法套用式；費率一個字都不得改）：
- `FEE` 0.1425% 進出各一次、`TAX` 0.3% 只在出場端、`SLIP_BASE` 0.2% 單邊（敏感度 `SLIP_GRID`）、
  空方借券費 `BORROW_BASE` 年化 2%（敏感度 `BORROW_GRID`）。
- `net_ret_long`：原 `scripts/rank_table.py:60-69` 的 `net_ret`，**算式逐字搬入**（浮點運算順序不變，
  `net_ret_long(0.0)` 仍＝ −0.009810371517992134＝該腳本的 `ZERO_FWD_NET`）。
- `net_ret_short`：登錄書只給費率、未給空方算式；本函式把 Q9 的乘法式兩端對調——進場端是出場端的鏡像
  （收 `(1−s)(1−f−t)`，證交稅在此端）、平倉端付 `(1+fwd)(1+s)(1+f)`，再扣借券費 `年化 × 曆日 / 365`
  （日數換算慣例登錄書未寫，依驗收條件 S1-5；`cal_days` 由呼叫端用日曆算好傳入，本模組不碰日期）。
  **這是本層對 Q9 的對稱延伸、不是登錄書字面**，報告須標明。

大盤 TX（§1.2.4）：稅 `TX_TAX` 0.002% 開平各課一次（法定）、乘數 `TX_MULT` 200（法定）、手續費 `C` 元／口／邊
（假設值，基準 100、`TX_C_GRID`）、滑價 `S` 點／邊（假設值，基準 1、`TX_S_GRID`）。**手續費是固定金額，佔比必須逐筆
除以當日契約金額 `P×200`**（`:253-258`）；轉倉＝再一次完整來回（`:247`），近次月價差不列成本。
試算 `:259-260`：P=45,000 → 單邊 480 元、0.00533%；來回 0.0107%。
"""
from __future__ import annotations

from collections.abc import Iterable

# ---- 個股（§1.2.1 `:91`；數值同 scripts/rank_table.py 原宣告）----
FEE = 0.001425          # 手續費，未打折公定價（偏保守），進出各一次
TAX = 0.003             # 證交稅，只在出場端
SLIP_BASE = 0.002       # 滑價，單邊基準；裁定 #57 Q12 一律 0.2% 不分層
SLIP_GRID = (0.001, 0.002, 0.003)
BORROW_BASE = 0.02      # 空方借券費，年化（情境佔位，需驗證）
BORROW_GRID = (0.01, 0.02, 0.04)

# ---- 大盤 TX（§1.2.4）----
TX_TAX = 0.00002        # 期貨交易稅 0.002%，開倉、平倉各課一次（法定）
TX_MULT = 200           # 契約乘數：1 點＝ 200 元（法定）
TX_C_BASE = 100.0       # 券商手續費 元／口／邊（假設值）
TX_C_GRID = (50.0, 100.0, 150.0)
TX_S_BASE = 1.0         # 滑價 點／邊（假設值；1 tick ＝ 1 點）
TX_S_GRID = (0.5, 1.0, 2.0, 4.0)
DAYS_PER_YEAR = 365     # 借券費年化→曆日換算（慣例，登錄書未寫）


def net_ret_long(fwd: float, slip: float = SLIP_BASE, fee: float = FEE, tax: float = TAX) -> float:
    """扣成本淨報酬（裁定 #57 Q9 的乘法式，逐字自 `scripts/rank_table.py:60-69` 搬入）。

        net = (1 + fwd) × (1−s)(1−f−t) / [(1+s)(1+f)] − 1

    乘法不是算術扣除：`fwd_ret=0` 時本式為 −0.00981037…。
    """
    return (1.0 + fwd) * (1.0 - slip) * (1.0 - fee - tax) / ((1.0 + slip) * (1.0 + fee)) - 1.0


#: `net_ret_long(0.0)` 的值；測試端獨立寫死（驗收 R1／S1-5），不得拿本函式自己驗自己。
ZERO_FWD_NET = net_ret_long(0.0)


def net_ret_short(fwd: float, slip: float = SLIP_BASE, borrow_annual: float = BORROW_BASE,
                  cal_days: int = 0, fee: float = FEE, tax: float = TAX) -> float:
    """空方扣成本淨報酬（以進場價為分母）：

        net = (1−s)(1−f−t) − (1 + fwd) × (1+s)(1+f) − borrow_annual × cal_days / 365

    `fwd` 仍是標的的前向報酬（價跌 → `fwd<0` → 本式為正）。`cal_days` ＝進場日到出場日的**曆日**數，由呼叫端算好。
    """
    if cal_days < 0:
        raise ValueError(f"net_ret_short：cal_days={cal_days}")
    return ((1.0 - slip) * (1.0 - fee - tax) - (1.0 + fwd) * (1.0 + slip) * (1.0 + fee)
            - borrow_annual * cal_days / DAYS_PER_YEAR)


def tx_side_ntd(p: float, c: float = TX_C_BASE, s: float = TX_S_BASE) -> float:
    """TX 單邊成本（元／口）＝ 稅 `P×200×0.002%` ＋ 手續費 `C` ＋ 滑價 `S×200`。"""
    if p <= 0:
        raise ValueError(f"tx_side_ntd：點位 p={p}")
    return p * TX_MULT * TX_TAX + c + s * TX_MULT


def tx_cost_pct(p: float, c: float = TX_C_BASE, s: float = TX_S_BASE) -> float:
    """TX 單邊成本佔契約金額比率＝ `tx_side_ntd / (P×200)`（`:253-257`）。"""
    return tx_side_ntd(p, c, s) / (p * TX_MULT)


def tx_roundtrip_pct(p_entry: float, p_exit: float, roll_prices: Iterable[float] = (),
                     c: float = TX_C_BASE, s: float = TX_S_BASE) -> float:
    """一次持有的全部成本佔**進場契約金額**比率：進場邊按 `p_entry`、平倉邊按 `p_exit`、每次轉倉按當日點位
    再加一次完整來回（兩邊）——逐筆用當日點位換算（`:258`）。無轉倉且 `p_entry == p_exit` 時＝ `2 × tx_cost_pct`。"""
    total = tx_side_ntd(p_entry, c, s) + tx_side_ntd(p_exit, c, s)
    for p in roll_prices:
        total += 2.0 * tx_side_ntd(p, c, s)
    return total / (p_entry * TX_MULT)
