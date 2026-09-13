"""流動性門檻與可交易（排名）池——**純函式＋只含 deque 的有狀態類別，不 import sqlite3**。

`scan.StockDay.in_rank_pool` 的來源。規格＝`docs/pre-registration.md` §1.1（裁定 T11 乙，已凍結）：

> 日均成交值（`Trading_money`）**≥ 0.3 億元／日**，以 **point-in-time 滾動**方式套用——
> 每個 T 日取「到 **T−1** 為止的 **60 個交易日** ADV」判定，名單逐日變動。
> **不得用訓練段算一次的靜態名單**：那會把「2021 冷門、2025 才放量」的股票永久排除、
> 把「2021 熱門、後來變殭屍」的一直留著，兩個方向都是 look-ahead。

門檻值 0.3 億**已凍結、不掃描**（§2 候選表第 3 列），所以本模組不提供「調門檻掃一輪」的路徑；
`threshold` 是參數只為了測試與敏感度分析，正式跑一律用 `ADV_THRESHOLD_TWD`。

## 呼叫順序是 PIT 的全部（用錯不會報錯，只會 look-ahead）

    for d in 交易日:
        pool = tracker.eligible()        # ← 先取，此時內部只吃到 T−1
        ... 用 pool 當 in_rank_pool 跑 T 日 ...
        tracker.push_day(d, amounts)     # ← 後推，把 T 日納入，供 T+1 用

反過來（先 push 再 eligible）就變成「用 T 日自己的成交值決定 T 日進不進池」＝ look-ahead，
**而且完全不會報錯**。`push_day` 會擋重複／回頭的日期，但擋不了這個順序錯——
由 `scripts/scan_features.py` 的順序與 `tests/test_liquidity.py` 的順序測試守住。

## 口徑（規格沒寫死的，標 SPEC-NOTE）

1. **視窗是「市場交易日」不是「該檔有成交的日子」**：停牌／零成交日**計入分母、成交值算 0**。
   **SPEC-NOTE**：§1.1 只寫「60 個交易日 ADV」未指明停牌怎麼算。取 0 是因為 ADV 要回答的是
   「這檔買不買得進去」——一檔停牌半年的股票不該因為「只算有成交的日子」而看起來很好買。
   這與 `scan.py` 的「視窗只取有效收盤」**刻意不同**：那邊量的是價格行為（停牌不代表沒漲跌），
   這邊量的是可交易性（停牌就是不可交易）。
2. **要滿 60 筆才判定**：不足 60 個交易日的新股一律**不合格**（不是「用現有天數算平均」）。
   §1.1 的訓練段快照寫「**至少 60 個交易日者**共 1,906 檔」，即以滿窗為前提。
   暖機期（掃描起點後的頭 60 個交易日）排名池會很小甚至是空的——那是正確行為，
   由 `n_tracked`／`n_ready` 兩個診斷數字讓它**看得見**，不要當成 bug。
3. **一律用當窗 60 筆重新加總，不用滾動累加**：滾動累加（加新值減舊值）的浮點誤差會**隨掃描
   起點而異**——Hetzner 從 2020 掃到今天，每日班只重算近期視窗，兩層的累積誤差不同，
   落在門檻線上的股票可能一邊進池一邊不進，兩層 parity 就破了（`spec/P1-B3-replay.md` §B3.2）。
   重新加總只依賴當窗那 60 個值，與歷史長度無關。**實測成本**（2026-09-13，2,000 檔 × 300 日、
   每日含一次 `eligible()`）：**6.3 ms/日**，1,618 日推估 **10 秒**，記憶體淨增 **5.5 MB**
   ——相對 `scan.DailyScanner` 的 22 ms/日 可忽略，不值得為它冒 parity 的險。
"""
from __future__ import annotations

from collections import deque
from typing import Iterable, Mapping

ADV_WINDOW = 60                    # 交易日（§1.1 凍結）
ADV_THRESHOLD_TWD = 3e7            # 0.3 億元／日（§1.1 凍結，§2 候選表第 3 列「不掃描」）


def adv(amounts: Iterable[float]) -> float | None:
    """視窗平均成交值；空視窗回 None。呼叫端自己確保視窗已滿（見模組口徑第 2 條）。"""
    vals = list(amounts)
    if not vals:
        return None
    return sum(vals) / len(vals)


class AdvTracker:
    """逐日推進的 60 日 ADV。狀態＝每檔一條長度 `window` 的成交值 deque。

    記憶體與 `scan.DailyScanner` 同量級（2,000 檔 × 60 個 float）。
    """

    def __init__(self, window: int = ADV_WINDOW, threshold: float = ADV_THRESHOLD_TWD) -> None:
        if not isinstance(window, int) or isinstance(window, bool) or window < 2:
            raise ValueError(f"window 必須是 ≥2 的整數，得到 {window!r}")
        if not (threshold > 0):
            raise ValueError(f"threshold 必須為正，得到 {threshold!r}")
        self.window = window
        self.threshold = float(threshold)
        self._amt: dict[str, deque[float]] = {}
        self.last_date: str | None = None

    # -- 讀（不改狀態） ---------------------------------------------------
    def adv_of(self, stock_id: str) -> float | None:
        """該檔目前的 ADV；視窗未滿回 None（不用不足天數硬算，見口徑第 2 條）。"""
        dq = self._amt.get(str(stock_id))
        if dq is None or len(dq) < self.window:
            return None
        return sum(dq) / self.window

    def eligible(self) -> frozenset[str]:
        """**以已推入的日子（呼叫時點＝T−1）** 判定的可交易池。滿窗且 ADV ≥ 門檻者。"""
        out = set()
        for sid in sorted(self._amt):                      # 決定性走訪序
            dq = self._amt[sid]
            if len(dq) >= self.window and sum(dq) / self.window >= self.threshold:
                out.add(sid)
        return frozenset(out)

    @property
    def n_tracked(self) -> int:
        """已見過至少一天的檔數。"""
        return len(self._amt)

    @property
    def n_ready(self) -> int:
        """視窗已滿、可被判定的檔數。暖機期它會遠小於 `n_tracked`。"""
        return sum(1 for dq in self._amt.values() if len(dq) >= self.window)

    # -- 寫 ---------------------------------------------------------------
    def push_day(self, tpe_date: str, amounts: Mapping[str, float]) -> None:
        """推進一個交易日。`amounts`＝{stock_id: 當日成交值（元）}，**只放當日有成交者**。

        已在追蹤、但今日不在 `amounts` 裡的檔一律補 **0.0**（停牌／零成交計入分母，口徑第 1 條）。
        日期必須嚴格升冪——重複或回頭會讓某一天被算兩次，無聲地墊高 ADV。
        """
        d = str(tpe_date)
        if self.last_date is not None and d <= self.last_date:
            raise ValueError(f"push_day 必須依日期升冪：last={self.last_date} 收到 {d}")
        seen = {str(k): float(v) for k, v in amounts.items()}
        for sid in sorted(self._amt):                      # 決定性走訪序
            self._amt[sid].append(seen.pop(sid, 0.0))
        for sid in sorted(seen):                           # 今日首見
            dq = self._amt[sid] = deque(maxlen=self.window)
            dq.append(seen[sid])
        self.last_date = d
