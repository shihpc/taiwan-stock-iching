"""價格還原係數（**後復權**，2026-09-12 裁定甲；2026-09-18 裁定 #51 由「除權息」擴為「除權息＋減資／分割／面額變更」）
——純函式，不 import sqlite3。

**口徑**：保留舊價不動，事件日**起**的價格乘上累積係數。
單一事件係數 ＝ `before_price / after_price`；某日的累積係數 ＝ 該日**當日或之前**所有事件的係數連乘。

    adj(t) = raw(t) × Π_{事件 ex_date ≤ t} (before_i / after_i)

驗算：`before=34.2`／`after=33.2`（配息 1.0 元）→ 係數 1.0301；除息日 raw 33.2 × 1.0301 ＝ 34.2，
與前一日的 34.2 銜接，假跌 −2.9% 被消掉。

**四個事件源、同一支算式**（`ADJUST_SOURCES`，`docs/P3-DATASET.md` §7.2）：除權息（`TaiwanStockDividendResult`）、
減資恢復買賣（`TaiwanStockCapitalReductionReferencePrice`：before＝停牌前最後成交日收盤、after＝減資後參考價，係數 **<1**，
3095 ≈0.0915）、分割（`TaiwanStockSplitPrice`，係數＝倍數 4）、面額變更（`TaiwanStockParValueChange`，倍數 10）。
四源的 `date` 皆為**事件生效日**（除權息日／恢復買賣日，Hetzner 2026-09-18 探測 P3 4/4），直接當 `ex_date`、
沿用 `factor_at` 的 `ex_date ≤ t`。來源列的欄位對映與跨源合併規則在 `factor_sources.py`；本模組只算係數。

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

# 事件源版本（裁定 #51 甲）：進 `run_common.build_params_payload`（參數指紋）與 `data/factors.json` 頂層 `sources`。
# 改事件源集合＝改分數 → 換字串 → 舊 scores.db／cross.json／factors.json 全部被拒（同 PIT 切換的版本綁定）。
ADJUST_SOURCES = "div+capred+split+par-1"

# 係數的合理範圍：超出即列入 anomalies 供人工複核（不靜默套用、也不硬失敗）。**按源分段**（2026-09-18 定案）：
# **注意方向**：`factor = before / after`。除權息價格下跌故 factor ≥ 1（34.2/33.2 ＝ 1.030）；初版把上下限寫反
# （MIN 0.2／MAX 1.0），結果**每一筆正常事件都被標成異常**、真正該標的反而落在範圍內，2026-09-12 修正。
# - dividend `[0.99, 5.0]`：實查 10,664 筆最小 0.9974（現金增資認購價高於市價→參考價上調，機制正確、照套）、
#   最大 4.156（5314 於 2026-08-14 配 46.55 元，before − dividend = after 逐筆吻合）。
# - split／parvalue `[1.5, 12.0]`：分割與面額變更係數＝倍數（探測見 4、10）；1 拆 1.5 以下罕見，12 以上（面額 10→<1）未見。
# - capred `[0.02, 1.01]`：減資後參考價**高於**停牌前收盤，係數 <1（3095 ≈0.0915、2364 ≈0.127）；0.02＝減資 98%。
FACTOR_BAND: dict[str, tuple[float, float]] = {
    "dividend": (0.99, 5.0),
    "split": (1.5, 12.0),
    "parvalue": (1.5, 12.0),
    "capred": (0.02, 1.01),
}
# 四源聯集 `[0.02, 12.0]`：只有「不知道來源」的路徑（`factors.json` 的 4 欄列）用它，見 `factor_sources.build_factors`
FACTOR_BAND_ANY: tuple[float, float] = (min(lo for lo, _ in FACTOR_BAND.values()), max(hi for _, hi in FACTOR_BAND.values()))


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


def anomalies(events: Iterable[Event], source: str | None = "dividend") -> list[tuple[Event, float]]:
    """係數落在該源 band（`FACTOR_BAND[source]`；`source=None`＝四源聯集 `FACTOR_BAND_ANY`）之外的事件，供報表列出、人工複核。

    **刻意不丟棄、不修正、也不失敗**：異常不一定是錯的。2026-09-12 對全部 10,664 筆除權息人工複核，
    兩端的極值**全部是真實事件**——低端（factor < 1）5 筆是現金增資認購價高於市價；
    高端最大 4.156 是 5314 配 46.55 元，`before − dividend = after` 逐筆吻合，
    另有長榮 2603 於 2023-06-30 配 70 元（155 → 85）這種著名案例。**結論：全部照套，無一排除。**
    本函式的用途是讓日後新增的異常被看見，不是過濾器。未知的 `source` 直接 raise（不得靜默用錯 band）。
    """
    if source is None:
        lo, hi = FACTOR_BAND_ANY
    else:
        if source not in FACTOR_BAND:
            raise ValueError(f"未知的事件源 {source!r}；可用 {sorted(FACTOR_BAND)}")
        lo, hi = FACTOR_BAND[source]
    out = []
    for e in events:
        f = event_factor(e.before_price, e.after_price)
        if not (lo <= f <= hi):
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
