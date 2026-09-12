"""除權息還原（**後復權**，2026-09-12 裁定甲）——純函式，不 import sqlite3。

**口徑**：保留舊價不動，除權息日**起**的價格乘上累積係數。
單一事件係數 ＝ `before_price / after_price`；某日的累積係數 ＝ 該日**當日或之前**所有事件的係數連乘。

    adj(t) = raw(t) × Π_{事件 ex_date ≤ t} (before_i / after_i)

驗算：`before=34.2`／`after=33.2`（配息 1.0 元）→ 係數 1.0301；除息日 raw 33.2 × 1.0301 ＝ 34.2，
與前一日的 34.2 銜接，假跌 −2.9% 被消掉。

**為什麼是後復權不是前復權**：後復權下 T 日的價格**只依賴 T 日以前的事件**，天生 point-in-time
安全；前復權（基準＝最新價）每發生一次新的除權息就改寫整段歷史，T 日價格依賴 T 日**之後**的事件。
多數子指標對尺度不敏感（`(C−MA)/ATR14`、斜率÷ATR、新高新低都是比較關係），兩者分數幾乎一樣，
但後復權不需要逐條辯解「這裡不受影響」。

**規格硬約束**（`P1-B2-params.md` §B2.0 政策第 2 點）：價格與 ATR 必須**同口徑**、不可混用。
本模組的輸出是整個技術面堆疊的價格來源。

**例外：B2.1 的「EPS 差額 ÷ 股價」分母用原始價**——它問的是「現在買一股要多少錢」，
用還原價會讓早年的股價被放大、比值失真。該處由呼叫端傳原始價，不走本模組。

**股利型態字串有兩套拼法**（2026-09-12 實查 10,664 列）：`息` 5,273／`除息` 3,696、
`權息` 431／`除權息` 425、`權` 422／`除權` 417——同一件事兩種寫法（與裁定 #28 的產業分類同型）。
**本模組不看型態字串**（只用 before／after 算係數），但日後若有邏輯要分「配息 vs 配股」，
**兩種拼法都要認**。
"""
from __future__ import annotations

import bisect
from typing import Iterable, NamedTuple

# 係數的合理範圍：超出即列入 anomalies 供人工複核（不靜默套用、也不硬失敗）。
# **注意方向**：`factor = before / after`，正常除權息價格下跌故 **factor ≥ 1**
# （34.2/33.2 ＝ 1.030）。初版把上下限寫反（MIN 0.2／MAX 1.0），結果**每一筆正常事件都被標成異常**、
# 真正該標的反而落在範圍內；我自己的煙霧測試就印出這個矛盾而未察覺，2026-09-12 修正。
FACTOR_MIN = 1.0      # 低於此＝除權息日價格「上漲」。實查 10,664 筆有 5 筆（0.9974~0.9998），
                      # 皆為現金增資認購價高於市價造成的參考價上調，機制正確、照套
FACTOR_MAX = 5.0      # 高於此＝單日跌逾 80%。實查最大 4.156（5314 於 2026-08-14 配 46.55 元），
                      # 全部 before − dividend = after 逐筆吻合，是真實大額配息、非資料錯


class Event(NamedTuple):
    ex_date: str
    before_price: float
    after_price: float


def event_factor(before_price: float, after_price: float) -> float:
    """單一事件的還原係數 ＝ before ÷ after。after 非正即 raise（分母無效）。"""
    b, a = float(before_price), float(after_price)
    if a <= 0:
        raise ValueError(f"after_price 必須為正，得到 {after_price!r}")
    return b / a


def anomalies(events: Iterable[Event]) -> list[tuple[Event, float]]:
    """係數落在 [FACTOR_MIN, FACTOR_MAX] 之外的事件，供報表列出、人工複核。

    **刻意不丟棄、不修正、也不失敗**：異常不一定是錯的。2026-09-12 對全部 10,664 筆人工複核，
    兩端的極值**全部是真實事件**——低端（factor < 1）5 筆是現金增資認購價高於市價；
    高端最大 4.156 是 5314 配 46.55 元，`before − dividend = after` 逐筆吻合，
    另有長榮 2603 於 2023-06-30 配 70 元（155 → 85）這種著名案例。**結論：全部照套，無一排除。**
    本函式的用途是讓日後新增的異常被看見，不是過濾器。
    """
    out = []
    for e in events:
        f = event_factor(e.before_price, e.after_price)
        if not (FACTOR_MIN <= f <= FACTOR_MAX):
            out.append((e, f))
    return out


def cumulative_factors(events: Iterable[Event]) -> tuple[list[str], list[float]]:
    """回傳 (ex_date 升序清單, 對應的累積係數)。累積係數＝該日**含當日**以前所有事件的連乘。

    同一天有多個事件（罕見但可能：同日除權又除息分成兩列）時，係數相乘、只留一個日期項。
    """
    by_date: dict[str, float] = {}
    for e in events:
        by_date[e.ex_date] = by_date.get(e.ex_date, 1.0) * event_factor(e.before_price, e.after_price)
    dates = sorted(by_date)
    cum, running = [], 1.0
    for d in dates:
        running *= by_date[d]
        cum.append(running)
    return dates, cum


def factor_at(date: str, dates: list[str], cum: list[float]) -> float:
    """某日適用的累積係數：最後一個 `ex_date ≤ date` 的累積值；都還沒發生則為 1.0。"""
    i = bisect.bisect_right(dates, str(date))
    return cum[i - 1] if i else 1.0


def adjust(date: str, raw_price: float | None, dates: list[str], cum: list[float]) -> float | None:
    """單一價格的後復權值。`raw_price` 為 None 時回 None。"""
    if raw_price is None:
        return None
    return float(raw_price) * factor_at(date, dates, cum)
