"""臺股期貨（TX）近月判定、基差與換月旗標（B1.5 族 B）——**純函式**，不 import sqlite3。

與 `official_parse.py` 同樣的理由：兩層 parity 只能共用純函式那一半。

**資料形狀（2026-09-12 Hetzner 實查 `raw_futures_daily` 37,800 列）**：

- `contract_date` 有兩種形狀：單月 `"202001"`，與**跨月價差合約** `"202009/202010"`。
  價差合約佔 **49%**（18,422／37,800），其 `open`／`close` 是**價差點數、可為負**
  （樣本：`open -66.0`、`close -63.0`）。**不濾掉會算出負幾十點的假基差。**
- `trading_session` 有 `position`（一般交易時段）與 `after_market`（盤後）兩種。
  基差要求「同時點」比較，現貨收 13:30、期貨一般時段收 13:45，故**取 `position`**。

**近月判定**：`contract_date` 是 `YYYYMM`；該月契約的最後交易日＝**該月第三個星期三**
（期交所 TX 契約規格，2026-09-12 覆核）。近月＝最後交易日 **≥ 當日**的最小契約月。
結算日當天該契約仍交易到 13:30，故當日仍算近月（`>=` 而非 `>`）。
"""
from __future__ import annotations

import datetime as dt
import re

CONTRACT_RE = re.compile(r"^\d{6}$")
SESSION_REGULAR = "position"


def is_outright(contract_date: str | None) -> bool:
    """單月契約（`202001`）才是 True；跨月價差（`202009/202010`）與畸形值皆 False。"""
    return bool(CONTRACT_RE.match(str(contract_date or "")))


def third_wednesday(year: int, month: int) -> dt.date:
    """該月第三個星期三（TX 最後交易日／最後結算日）。"""
    first = dt.date(year, month, 1)
    # weekday(): 週一=0 … 週三=2
    offset = (2 - first.weekday()) % 7
    return first + dt.timedelta(days=offset + 14)


def contract_last_trading_day(contract_date: str) -> dt.date:
    if not is_outright(contract_date):
        raise ValueError(f"非單月契約：{contract_date!r}")
    return third_wednesday(int(contract_date[:4]), int(contract_date[4:6]))


def near_month(tpe_date: str, contracts: list[str]) -> str | None:
    """當日的近月契約：最後交易日 ≥ 當日的最小契約月。全不合格回 None。

    `contracts` 可含價差合約與畸形值，本函式自行濾掉。
    """
    d = dt.date.fromisoformat(str(tpe_date))
    cand = sorted({c for c in contracts if is_outright(c)})
    for c in cand:
        if contract_last_trading_day(c) >= d:
            return c
    return None


def basis_ratio(futures_close: float | None, spot_close: float | None) -> float | None:
    """基差比＝(近月期指 − 現貨) ÷ 現貨。任一缺值或現貨為 0 回 None（分母無效由呼叫端記 `denominator_zero`）。"""
    if futures_close is None or spot_close is None:
        return None
    try:
        f, s = float(futures_close), float(spot_close)
    except (TypeError, ValueError):
        return None
    if s == 0:
        return None
    return (f - s) / s


def rolled(prev_near: str | None, cur_near: str | None) -> bool:
    """換月旗標：近月契約與**前一交易日**不同即為 True（`P1-B1-market.md:239`）。

    首日（`prev_near` 為 None）回 False——沒有前一日可比，不是換月。
    """
    if prev_near is None or cur_near is None:
        return False
    return prev_near != cur_near
