"""逐日橫斷面掃描器（第 10／11 項）：一趟前向掃描同時產出**大盤廣度**、**產業聚合**與 **P_cs**。

**本模組不 import sqlite3**。規格（`spec/P1-B3-replay.md` §B3.2）要求的是「同一鍵同一版本三元組下
兩層分數逐位相同」，**沒有一個字說不准 import sqlite3**——「特徵／計分層全部寫成不碰 DB 的純函式，
DB 存取只留在驅動腳本」是本專案為達成那個要求自訂的實作手段，不是規格明文（2026-09-12 驗收更正措辭）。
狀態只有：每檔一條收盤 deque、每市場一條指數 deque、每市場一個 AD 累積整數、`last_date`。

輸入是呼叫端整理好的 `StockDay` 序列（每交易日一批），輸出 `ScanDay`。讀 DB、後復權、
名單建構全在呼叫端（`scripts/scan_features.py`）——本模組只做「同一批價格 → 橫斷面統計」。

## 兩份 PIT 名單（使用者 2026-09-12 裁定乙）

- **廣度母體**＝普通股全體（`universe.pool_from_info` ∩ 當日有成交，約 1,900），**不套流動性門檻**。
  `MarketInputs.n_stocks`／各家數比的分母都是它。
- **排名池**＝再套流動性門檻（`docs/pre-registration.md` §1.1，滾動 0.3 億）的可交易池，
  由 `StockDay.in_rank_pool` 標記。**只有 `P_cs` 用它**（`P_cs` 的消費端是排名層與過熱旗標，
  母體必須等於「可能被選進名單的股票」）。產業中位數**用廣度母體**——它是描述性統計，
  用全體普通股估產業的移動更有代表性，且不隨流動性門檻的滾動而跳動。

## 口徑（每一條都是實作選擇，全部可在一處改；`spec` 沒寫死的已標 SPEC-NOTE）

1. **有效價**＝`close_adj is not None`。呼叫端以 `universe.is_traded_row()` 判「當日有成交」，
   無成交不傳 `close_adj`（傳 None）。無效日**不進任何視窗**——視窗一律「最近 n 個**有效**收盤」
   （使用者 2026-09-12 裁定甲，與 MA／ATR 同一套）。
2. **後復權價**（`adjust.py`）。漲跌、MA、新高低**全部同一個口徑**——這是刻意的：
   若漲跌用原始價（官方 `spread`／含除息跳空）而 MA 用還原價，同一天同一檔會出現
   「被判下跌但站上 MA」這種自相矛盾，而且只在除權息日發生、極難察覺。
   **SPEC-NOTE**：官方「上漲家數」是原始價口徑，本模組刻意不同；差異只落在除權息日，
   影響 `advance_ratio`／`ad_line`／`up_amount_ratio`。**差異幅度尚未量測**（要在 Hetzner 跑）：
   第 12 項的掃描驅動腳本會同時算兩套並印出差異，量測後才裁定要不要改回官方口徑。
   **本模組目前沒有這個開關**——初版留了一個 `ADVANCE_ON_ADJUSTED` 常數與同名建構子參數，
   但沒有任何分支讀它，傳 `False` 與 `True` 輸出完全相同（2026-09-12 驗收抓到）。
   一個靜默無效的參數比沒有參數更糟，已整個移除；真要切換口徑得在 `StockDay` 加原始收盤欄再加分支。
3. **站上 MA_n**＝`close_adj > MA_n`（**嚴格大於**）。等於 MA 不算站上。固定為嚴格是為了讓兩層
   parity 不依賴平手行為。**「平手實際多常發生」未量測**——長期不動／漲停鎖死的個股會讓
   `close == MA` 成立，這是**推測**（浮點下需連續 n 日同價），實際筆數待 Hetzner 掃描時一併統計。
4. **n 日新高**＝`close_adj >` 前 n−1 個有效收盤的最大值（**嚴格**）；新低同理取 `<`。
   平盤序列因此既非新高也非新低——若用 `>=`／`<=`，一條水平線會同時被判新高與新低、
   淨值恰好 0，看起來「沒事」卻是兩個假訊號相消。
5. **家數比的分母一律是 `n_stocks`（N_t）**，照 `P1-B1-market.md:157`（「分子分母必須同一份名單」）。
   歷史不足 n 天的新股算在分母、不可能在分子——這是規格的讀法，會造成一個向下偏誤。
   **偏誤幅度未量測**（`ma_eligible` 等診斷欄就是為了量它；我預期穩態下很小，但那是**推測**，
   在 Hetzner 掃完之前不當事實用）。**所有原始計數都輸出**，日後改口徑不必重掃。
6. **`up_amount_ratio` 的分母＝漲跌可判定的子集**（`amount_ret_eligible`），與分子同一份名單；
   另輸出 `amount_total`（母體全體）供改口徑。規格只寫「總成交金額」未指明母體。
7. **`ad_line` 起點為 0**（首次掃描日）。消費端是 `(AD − MA_n(AD)) ÷ N`，常數平移會相消，
   故起點值不影響分數——但**序列必須從同一天起算**，兩層 parity 才成立。
8. **n 日報酬**＝`(P_t / P_{t−n} − 1) × 100`，`P_{t−n}` 取**第 n 個有效收盤之前**那一筆
   （與 `score/stock.py:_pct_ret` 對同一條有效價序列的位置語意相同）。指數報酬取 n 個
   **交易日**前（指數沒有停牌）。超額＝個股報酬 − 指數報酬（單位 pp）。
9. **`P_cs` 的平手規則＝`"mid"`**（`100 × (#小於 + 0.5 × #等於) ÷ N_pool`），與 `transform.P_hist`
   的 `Rules.phist_tie` 同一套。**SPEC-NOTE**：`Rules` 目前沒有 `p_cs_tie` 欄位，而 `P_cs`
   會經過熱旗標影響三爻分數，嚴格說該進 `model_version` 指紋——加欄位屬裁定範圍，本批不動。
10. **列的走訪順序固定為 `stock_id` 升序**。浮點加總不可交換：兩層若以不同順序累加
    `amount`，總和會差 1e-9，經比值與四捨五入可能放大成可見差異（家族前例：
    `taiwan-flows` 的次產業張數差 1）。本模組一律自己排序，不信呼叫端的順序。
"""
from __future__ import annotations

import bisect
import statistics
from collections import deque
from dataclasses import dataclass, field
from typing import Iterable, NamedTuple

MA_WINDOWS: tuple[int, ...] = (5, 10, 20, 60)          # MarketInputs.above_ma_ratio 的鍵（MKT_L2_WIN 的 MA_短／MA_長）；
#                                                        另含 StockInputs.industry_above_ma_ratio 要的 20
HL_WINDOWS: tuple[int, ...] = (10, 20, 60)             # MarketInputs.new_high_low_ratio 的鍵（＝MKT_L2_WIN 的 n）
RET_WINDOWS: tuple[int, ...] = (5, 10, 20, 60)         # 產業中位報酬要的全部窗長＝STK_L3_WIN 長視窗 ∪ STK_L6_WIN
P_CS_WINDOWS: tuple[int, ...] = (10, 20, 60)           # p_cs_long_excess 只用 STK_L3_WIN 的長視窗

# **同一個窗長對不同消費端是不同 horizon**——這是本模組一律以 `window` 當鍵、不以 `horizon` 當鍵的理由。
# 若把產業聚合直接存成 `horizon` 鍵，swing 需要的 L3 長視窗 20 與 L6 視窗 10 會在同一列打架；
# `spec/dimensions.json` 的 `industry_aggregate` 宣告鍵是 `market × horizon × industry × date`，
# 那是**重播清單**的鍵，每列底下的 `industry_median_return` 本來就是 `n → 值` 的 dict
# （`score/stock.py:39`）。落地表怎麼擺屬第 12 項，本模組只輸出不失真的 window 形式。
HORIZON_BY_L3_LONG_WINDOW = {10: "short", 20: "swing", 60: "mid"}    # excess_long／excess_vs_industry／p_cs
HORIZON_BY_L6_WINDOW = {5: "short", 10: "swing", 20: "mid"}          # industry_relative_return
P_CS_TIE = "mid"                                       # 同 transform.P_hist 的 tie 規則


class StockDay(NamedTuple):
    """某交易日、某一檔的掃描輸入。

    `close_adj`＝後復權收盤，`None`＝當日無成交（不進母體、不進視窗）。
    `amount`＝成交金額（元，FinMind `Trading_money` 原樣），`None` 視為 0 但仍在母體內。
    `industry`＝`universe.pool_from_info()` 決定的產業別（None／空字串＝不進產業聚合）。
    `in_rank_pool`＝是否在流動性池（只影響 `P_cs`）。
    """
    stock_id: str
    market: str
    industry: str | None
    close_adj: float | None
    amount: float | None
    in_rank_pool: bool


@dataclass
class MarketBreadth:
    """單一市場、單日的廣度。比值欄可為 None（母體為 0）；計數欄一律有值。"""
    market: str
    tpe_date: str
    n_stocks: int
    above_ma_count: dict[int, int] = field(default_factory=dict)
    ma_eligible: dict[int, int] = field(default_factory=dict)       # 有 n 個有效收盤的檔數（診斷）
    advance_count: int = 0
    decline_count: int = 0
    unchanged_count: int = 0
    ret_eligible: int = 0                                          # 漲跌可判定的檔數（有前一個有效收盤）
    new_high_count: dict[int, int] = field(default_factory=dict)
    new_low_count: dict[int, int] = field(default_factory=dict)
    hl_eligible: dict[int, int] = field(default_factory=dict)
    ad_line: int = 0                                               # 累積 Σ(漲 − 跌)，整數
    amount_up: float = 0.0
    amount_ret_eligible: float = 0.0                               # up_amount_ratio 的分母
    amount_total: float = 0.0                                      # 母體全體（含漲跌不可判定者）

    # -- MarketInputs 對應欄（比值） ---------------------------------------
    @property
    def above_ma_ratio(self) -> dict[int, float | None]:
        return {n: self._over_n(c) for n, c in sorted(self.above_ma_count.items())}

    @property
    def advance_ratio(self) -> float | None:
        return self._over_n(self.advance_count)

    @property
    def new_high_low_ratio(self) -> dict[int, float | None]:
        return {n: self._over_n(self.new_high_count[n] - self.new_low_count[n])
                for n in sorted(self.new_high_count)}

    @property
    def up_amount_ratio(self) -> float | None:
        return self.amount_up / self.amount_ret_eligible if self.amount_ret_eligible > 0 else None

    def _over_n(self, c: int) -> float | None:
        return c / self.n_stocks if self.n_stocks > 0 else None


@dataclass(frozen=True)
class IndustryAgg:
    """產業中位報酬（`StockInputs.industry_median_return[n]`／`industry_n`）。

    **`median_ret` 是「原始」n 日報酬的中位數，不是超額報酬的中位數**（2026-09-12 驗收抓到，
    初版錯成超額）。兩個消費端都證實要原始值：
    - `score/stock.py:ind_excess_vs_industry` 算 `_pct_ret(close, n) − industry_median_ret`，
      左邊是原始報酬；
    - `score/stock.py:ind_industry_relative` 算 `industry_median_ret − _pct_ret(index_close, n)`，
      **自己減指數報酬**——若傳超額進去等於減兩次。

    餵超額會讓兩者都多出一整個指數報酬 `mret`（全市場同號偏移）。**測試盲區**：初版兩支產業
    測試的指數兩日都是 100.0，`mret=0` 時超額恰等於原始報酬，錯的和對的長一樣。

    `n` 是**有 window 日報酬的檔數**，不是產業檔數——`Rules.industry_min_sample`(5) 的閘門
    由計分端判，本模組照實輸出小樣本（含 n=1）。
    """
    market: str
    tpe_date: str
    window: int
    industry: str
    n: int
    median_ret: float


@dataclass
class IndustryBreadth:
    """產業內均線廣度（`StockInputs.industry_above_ma_ratio[n]`，`score/stock.py:ind_industry_above_ma20`）。

    消費端目前只用 `window=20`（`Param("industry_above_ma20_ratio", window=20)`，三期間共用），
    但每檔的站上判定本來就每個 MA 窗長都算了，全部輸出不多花成本。
    分母＝該產業當日**有成交**的檔數（`n_stocks`），與大盤廣度同一套口徑。
    """
    market: str
    tpe_date: str
    industry: str
    n_stocks: int
    above_ma_count: dict[int, int]
    ma_eligible: dict[int, int]

    @property
    def above_ma_ratio(self) -> dict[int, float | None]:
        return {n: (c / self.n_stocks if self.n_stocks > 0 else None)
                for n, c in sorted(self.above_ma_count.items())}


@dataclass
class ScanDay:
    tpe_date: str
    breadth: dict[str, MarketBreadth]
    industry: list[IndustryAgg]
    industry_breadth: list[IndustryBreadth]
    excess: dict[tuple[str, int], dict[str, float]]        # (market, window) → {stock_id: 超額報酬 pp}
    p_cs: dict[tuple[str, int], dict[str, float]]          # (market, window) → {stock_id: 0–100}
    index_missing: list[str] = field(default_factory=list)
    """**該日**缺指數收盤的市場。指數 deque 因此不推進，於是其後最多 n 天的 n 日指數報酬會跨越
    多於 n 個交易日。**本欄只標缺值當天，不標被波及的後續各天**——那幾天的 `index_missing` 是空的，
    要由呼叫端自己往前推 n 天判定。這樣設計是因為「被波及」取決於呼叫端在意哪個窗長；
    但別把本欄讀成「扭曲日都標出來了」。"""


# ---------------------------------------------------------------------------
# 純函式
# ---------------------------------------------------------------------------
def pct_return(closes: list[float], n: int) -> float | None:
    """最近 n 個有效收盤區間的報酬（%）。`closes` 升冪、最後一筆＝T。不足 n+1 筆或基期為 0 → None。"""
    if len(closes) < n + 1:
        return None
    base = closes[-1 - n]
    if base == 0:
        return None
    return (closes[-1] / base - 1.0) * 100.0


def cross_percentile(values: dict[str, float], tie: str = P_CS_TIE) -> dict[str, float]:
    """橫斷面百分位（0–100），與 `transform.P_hist` 同一套平手規則。

    母體＝`values` 自身（含被評分的那一檔）。`tie="mid"`：`100 × (#小於 + 0.5 × #等於) ÷ N`
    ——全體同值得 50、最大值得 `100 − 50/N`。母體為空回空 dict。
    """
    n = len(values)
    if n == 0:
        return {}
    ordered = sorted(values.values())          # 決定性：值排序，不依 dict 插入序
    out: dict[str, float] = {}
    for sid in sorted(values):                 # 決定性：代號升序
        v = values[sid]
        below = _count_lt(ordered, v)
        equal = _count_le(ordered, v) - below
        if tie == "mid":
            rank = below + 0.5 * equal
        elif tie == "low":
            rank = float(below)
        elif tie == "high":
            rank = float(below + equal)
        else:
            raise ValueError(f"unknown tie rule {tie!r}")
        out[sid] = 100.0 * rank / float(n)
    return out


def _count_lt(ordered: list[float], v: float) -> int:
    return bisect.bisect_left(ordered, v)


def _count_le(ordered: list[float], v: float) -> int:
    return bisect.bisect_right(ordered, v)


# ---------------------------------------------------------------------------
# 有狀態掃描器（只有 deque／dict，無 DB）
# ---------------------------------------------------------------------------
class DailyScanner:
    """單趟前向掃描。`push_day()` 必須**依日期升冪**呼叫，重複或回頭的日期會 raise。

    狀態＝每檔一條有效收盤 deque（maxlen＝`max(MA∪HL∪{ret+1})`）＋每市場一條指數收盤 deque
    ＋每市場一個 AD 累積值。**實測 2,000 檔滿載後 `tracemalloc` 淨增 6.0 MB**（2026-09-12）
    ——不是「2,000 × 61 × 8 bytes ≈ 1 MB」，那個算式只算裸浮點的位元組，漏了 deque 容器本身
    與每個 float 物件的表頭，實際約 6 倍。
    """

    def __init__(self, ma_windows: Iterable[int] = MA_WINDOWS, hl_windows: Iterable[int] = HL_WINDOWS,
                 ret_windows: Iterable[int] = RET_WINDOWS, p_cs_windows: Iterable[int] = P_CS_WINDOWS) -> None:
        self.ma_windows = tuple(sorted(set(int(n) for n in ma_windows)))
        self.hl_windows = tuple(sorted(set(int(n) for n in hl_windows)))
        self.ret_windows = tuple(sorted(set(int(n) for n in ret_windows)))
        self.p_cs_windows = tuple(n for n in self.ret_windows if n in set(int(x) for x in p_cs_windows))
        self._maxlen = max((*self.ma_windows, *self.hl_windows, *(n + 1 for n in self.ret_windows)))
        self._closes: dict[str, deque[float]] = {}
        self._idx: dict[str, deque[float]] = {}
        self._ad: dict[str, int] = {}
        self.last_date: str | None = None

    # -- 讀狀態（診斷／續掃用） ------------------------------------------
    def history_len(self, stock_id: str) -> int:
        return len(self._closes.get(stock_id, ()))

    def push_day(self, tpe_date: str, rows: Iterable[StockDay],
                 index_close: dict[str, float | None] | None = None) -> ScanDay:
        """吃一個交易日的全市場切片，回該日的廣度／產業／P_cs，並推進內部狀態。

        `index_close`＝{market: 指數收盤}（`TaiwanStockPrice` 的 TAIEX／TPEx），缺市場或缺值時
        該市場該日**不產出超額報酬與 `P_cs`**（產業中位數同樣缺——它也是超額的函數）。
        """
        d = str(tpe_date)
        if self.last_date is not None and d <= self.last_date:
            raise ValueError(f"push_day 必須依日期升冪：last={self.last_date} 收到 {d}")
        rows = sorted(rows, key=lambda r: str(r.stock_id))          # 決定性走訪序（見 docstring 第 10 點）
        index_close = index_close or {}

        breadth: dict[str, MarketBreadth] = {}
        rets: dict[tuple[str, int], dict[str, float]] = {}
        rank_rets: dict[tuple[str, int], dict[str, float]] = {}
        ind_b: dict[tuple[str, str], IndustryBreadth] = {}

        for r in rows:
            mk = str(r.market)
            b = breadth.get(mk)
            if b is None:
                b = breadth[mk] = MarketBreadth(
                    market=mk, tpe_date=d, n_stocks=0,
                    above_ma_count={n: 0 for n in self.ma_windows},
                    ma_eligible={n: 0 for n in self.ma_windows},
                    new_high_count={n: 0 for n in self.hl_windows},
                    new_low_count={n: 0 for n in self.hl_windows},
                    hl_eligible={n: 0 for n in self.hl_windows},
                    ad_line=self._ad.get(mk, 0))
            if r.close_adj is None:
                continue                                            # 無成交：不進母體、不動狀態
            c = float(r.close_adj)
            hist = self._closes.get(r.stock_id)
            if hist is None:
                hist = self._closes[r.stock_id] = deque(maxlen=self._maxlen)
            prev = hist[-1] if hist else None
            amt = float(r.amount or 0.0)

            b.n_stocks += 1
            b.amount_total += amt

            # 漲跌（對前一個有效收盤）
            if prev is not None:
                b.ret_eligible += 1
                b.amount_ret_eligible += amt
                if c > prev:
                    b.advance_count += 1
                    b.amount_up += amt
                elif c < prev:
                    b.decline_count += 1
                else:
                    b.unchanged_count += 1

            # 新高／新低（對前 n−1 個有效收盤，嚴格）
            prior = list(hist)                                      # 熱路徑：每檔每日只複製兩次（見下）
            np_ = len(prior)
            for n in self.hl_windows:
                if n >= 2 and np_ >= n - 1:
                    w = prior[np_ - (n - 1):]
                    b.hl_eligible[n] += 1
                    if c > max(w):
                        b.new_high_count[n] += 1
                    elif c < min(w):
                        b.new_low_count[n] += 1

            hist.append(c)                                          # 納入今日後才算 MA 與報酬
            # `prior` 是 append **前**的快照；deque 滿載時 append 會擠掉最舊一筆，但下面所有取用
            # **一律從尾端切**（`closes[nc-n:]`／`closes[-1-n]`），多留在頭部的那一筆永遠切不到，
            # 故不需要為 eviction 分支——2026-09-12 突變測試實證：寫成 `prior[1:]+[c]` 與
            # `prior+[c]` 兩版對每一個輸出逐位相同，那個分支只是**看起來嚴謹**的死程式碼。
            closes = prior + [c]
            nc = len(closes)

            ind_name = (r.industry or "")
            ib = None
            if ind_name:
                ib = ind_b.get((mk, ind_name))
                if ib is None:
                    ib = ind_b[(mk, ind_name)] = IndustryBreadth(
                        market=mk, tpe_date=d, industry=ind_name, n_stocks=0,
                        above_ma_count={n: 0 for n in self.ma_windows},
                        ma_eligible={n: 0 for n in self.ma_windows})
                ib.n_stocks += 1

            # 站上 MA_n（視窗含今日）
            for n in self.ma_windows:
                if nc >= n:
                    b.ma_eligible[n] += 1
                    over = c > sum(closes[nc - n:]) / n
                    if over:
                        b.above_ma_count[n] += 1
                    if ib is not None:
                        ib.ma_eligible[n] += 1
                        if over:
                            ib.above_ma_count[n] += 1

            # n 日報酬（供超額／產業中位／P_cs）
            for n in self.ret_windows:
                rv = pct_return(closes, n)
                if rv is not None:
                    rets.setdefault((mk, n), {})[r.stock_id] = rv

        # 指數推進 → 超額報酬
        for mk, b in breadth.items():
            self._ad[mk] = b.ad_line = b.ad_line + b.advance_count - b.decline_count
        index_missing: list[str] = []
        for mk in sorted(set(list(breadth) + list(index_close))):
            iv = index_close.get(mk)
            dq = self._idx.get(mk)
            if dq is None:
                dq = self._idx[mk] = deque(maxlen=self._maxlen)
            if iv is None:
                index_missing.append(mk)
                continue                                            # 指數缺值：該日不推進，也不產超額
            dq.append(float(iv))
            ic = list(dq)
            for n in self.p_cs_windows:
                mret = pct_return(ic, n)
                if mret is None:
                    continue
                for sid, sret in sorted(rets.get((mk, n), {}).items()):
                    self._excess_put(rank_rets, mk, n, sid, sret - mret)

        # 產業中位數：母體＝廣度母體、值＝**原始** n 日報酬（不是超額，見 IndustryAgg docstring）。
        # 因此它**不依賴指數**——指數缺值的日子照樣產得出來。
        industry_of = {str(r.stock_id): (r.industry or "") for r in rows}
        ind_rets: dict[tuple[str, int, str], list[float]] = {}
        for (mk, n), per in sorted(rets.items()):
            for sid, rv in sorted(per.items()):
                ind = industry_of.get(sid) or ""
                if ind:
                    ind_rets.setdefault((mk, n, ind), []).append(rv)
        industry = [IndustryAgg(market=mk, tpe_date=d, window=n, industry=ind,
                                n=len(v), median_ret=statistics.median(sorted(v)))
                    for (mk, n, ind), v in sorted(ind_rets.items())]
        # P_cs：母體＝排名池
        in_pool = {str(r.stock_id) for r in rows if r.in_rank_pool}
        p_cs = {k: cross_percentile({sid: v for sid, v in per.items() if sid in in_pool})
                for k, per in sorted(rank_rets.items())}

        self.last_date = d
        return ScanDay(tpe_date=d, breadth=breadth, industry=industry,
                       industry_breadth=[ind_b[k] for k in sorted(ind_b)],
                       excess={k: dict(sorted(v.items())) for k, v in sorted(rank_rets.items())},
                       p_cs=p_cs, index_missing=index_missing)

    @staticmethod
    def _excess_put(store: dict[tuple[str, int], dict[str, float]], mk: str, n: int, sid: str, ex: float) -> None:
        store.setdefault((mk, n), {})[sid] = ex
