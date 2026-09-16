"""重播狀態（第 13 項 13a-1）——**純函式層，不做任何 DB 存取**。讀取在 `replay_io.py`。

設計正本：`docs/P2-REPLAY-PLAN.md` §2（`ReplayState = CrossDayState + WindowCache`）。本檔負責：

1. `CrossDayState`——**不可由原料重建**的跨日狀態：遲滯 `(state, streak)`、二爻分數歷史（個股 9 筆／
   大盤 5 筆）、排名池 ADV 視窗。可 JSON 序列化（`to_dict`／`from_dict` 往返逐位相同），每日班存進 git。
2. `WindowCache`——**可由原料重建**的視窗（個股 320 日後復權價量＋籌碼、大盤指數／官方金額／廣度／
   期貨／VIX／美股／匯率）。`ingest(bundle)` 吃一天的 `DayBundle`（plain data，由 `replay_io.read_day` 或
   每日班的 API 端產生），`market_inputs()`／`stock_inputs()` 吐 `MarketInputs`／`StockInputs`。
   **兩層（Hetzner 回補／每日班）共用同一個 `WindowCache`**，parity 的本體就是「餵同樣的 `DayBundle` 序列」。

## 視窗語意（與 `score_io` 對齊之處與刻意不同之處）

- 個股序列**只在「該股當日有成交（`universe.is_traded_row`）且所屬市場指數有列」的日子推進**；
  `index_close` 逐日對齊；法人無列補 0、餘額無列補 NaN、**視窗內完全沒有列則整欄 None**——後三項沿用
  `score_io.stock_inputs_from_stores` 的 `aligned()`。
  **刻意不同**：`score_io` 把停牌／畸形列（`close=0` 或量 0）也算進視窗；本檔不算，與 `feed.day_records`
  同一把尺（2026-09-13 實測 1.2% 的列屬此類）。
- 個股 OHLC **後復權**（`adjust.factor_at`，`adj(t)=raw(t)×Π_{ex≤t}(before/after)`）——這是
  `docs/P2-REPLAY-PLAN.md` §1 #6 的跨批次斷點；`score_io.load_stock_ohlcv` 仍是原始價、只供量測對照。
  後復權只放大除權息日**之後**的價，視窗內舊值不必回寫，逐日 append 即可。
- 大盤「對齊序列」（指數 OHLC、官方成交金額、官方法人金額、廣度計數）**以該市場指數有列的日子為軸**，
  當日缺的欄補 NaN；「自帶日期序列」（融資餘額、期貨 OI、VIX、美股、匯率）各走自己的日期，
  與 `score_io.market_inputs_from_stores` 的「各表最後 n 個不重複日期」語意相同。
- **`ad_line` 在視窗內從 0 起算**（`cumsum(advance_count − decline_count)` 只算視窗內的列）：
  `ind_ad_line_dev` 只用 `AD − MA_n(AD)`，對常數位移不變；但**逐位相同**要求兩層的浮點輸入完全一致，
  若沿用「從重播起點累積」，每日班（只重建 320 日）與全量跑會差一個常數、`(ad − mean)` 的浮點結果不逐位相等。
  視窗內從 0 起算讓兩層看到同一組數字。
- 廣度比值由計數÷`n_stocks` 於讀取端算（`features_io` 的「比值一概不存」原則）；分母一律 `N_t`（裁定 ②）。

## 記憶體

個股用 numpy 環形緩衝（`Ring`，`(320, 10)` float64 ＝ 25.6 KB／檔，2,139 檔 ≈ 55 MB）；
Python `deque` 存 float 每檔要 4 倍（float 物件 24 B ＋指標 8 B），不用。
大盤層只有兩市場、用 deque 無妨。
"""
from __future__ import annotations

import json
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

import numpy as np

from .adjust import factor_at
from .futures import basis_pct, near_month, rolled
from .liquidity import AdvTracker
from .scan import HORIZON_BY_L3_LONG_WINDOW
from .score.hexagram import YANG, hysteresis_step
from .score.market import MarketInputs
from .score.params import HORIZONS, LINE2_SERIES_LEN, LINES, MARKETS, Rules
from .score.stock import StockInputs
from .universe import PitPool, is_traded_row

WINDOW_N = 320
STOCK_LINE2_HIST = LINE2_SERIES_LEN - 1          # 9：T−9…T−1（`stock.py:524-526` 長度不等於 9 整段視為缺）
MARKET_LINE2_HIST = 5                            # T−5（`MarketInputs.line2_score_t_minus_5`）
L3_WINDOW_BY_HORIZON = {h: w for w, h in HORIZON_BY_L3_LONG_WINDOW.items()}   # short→10, swing→20, mid→60
MA_WINDOWS = (5, 10, 20, 60)
HL_WINDOWS = (10, 20, 60)
INDUSTRY_MA_WINDOW = 20                          # `Param("industry_above_ma20_ratio", window=20)` 三期間共用
STOCK_COLS = ("open", "high", "low", "close", "volume", "index_close",
              "foreign_net", "trust_net", "margin_balance", "short_sale_balance")
LineState = tuple[str | None, int]

STATE_SCHEMA = 1


class ReplayStateError(RuntimeError):
    pass


class ReplayDateError(ReplayStateError):
    def __init__(self, asked: str, have: str | None) -> None:
        super().__init__(f"要求 {asked} 的輸入，但快取最後 ingest 的是 {have}（先 ingest 再取）")


# ---------------------------------------------------------------------------
# 一天的原料（plain data；由 replay_io 或每日班的 API 端建構）
# ---------------------------------------------------------------------------
@dataclass
class DayBundle:
    """`WindowCache.ingest()` 的唯一輸入。所有值都是「T 這一天」的，欄位缺就給 None／空 dict。

    - `index[market]`：`{"open","high","low","close"}`（`raw_index_price`；缺該市場＝當日無指數列）
    - `stocks[sid]`：`{"open","high","low","close","Trading_Volume"(股；`is_traded_row` 看它)，"volume"(股，可省、省略時取
      `Trading_Volume`),"amount"(元),"foreign_net"(張,None=無列),
      "trust_net","margin_balance","short_sale_balance","shares_outstanding"(股,None=無列)}`——**原始價**，還原在 ingest 內做；
      `shares_outstanding` 來自 `raw_shareholding.NumberOfSharesIssued`（裁定 #34 Q3），快取沿用**最近一次有值的申報**
    - `official[market]`：`{"amount_k","foreign_net_k","trust_net_k"}`（千元；缺＝None）
    - `breadth[market]`：`features_io.FeatureStore.day_breadth()` 的形狀（缺＝None）
    - `industry[market]`：`FeatureStore.day_industry()`；`p_cs[market]`：`FeatureStore.day_p_cs()`
    - `futures`：`{"contracts": [...], "close": {contract: close}}`（TX 一般交易時段）
    - `total_margin`／`vix`／`foreign_net_oi`：當日一個數；`us`：`[(us_date, spx_c, spx_h, spx_l, sox_c), …]`
      新到的美股列（升冪）；`fx`：`[(date, usdtwd), …]`
    """
    tpe_date: str
    index: dict[str, dict[str, float]] = field(default_factory=dict)
    stocks: dict[str, dict[str, Any]] = field(default_factory=dict)
    official: dict[str, dict[str, float | None]] = field(default_factory=dict)
    breadth: dict[str, dict | None] = field(default_factory=dict)
    industry: dict[str, dict[str, dict]] = field(default_factory=dict)
    p_cs: dict[str, dict[str, dict[int, float]]] = field(default_factory=dict)
    futures: dict[str, Any] = field(default_factory=dict)
    total_margin: float | None = None
    vix: float | None = None
    foreign_net_oi: float | None = None
    us: list[tuple[str, float, float, float, float]] = field(default_factory=list)
    fx: list[tuple[str, float]] = field(default_factory=list)


# ---------------------------------------------------------------------------
# CrossDayState
# ---------------------------------------------------------------------------
def _k(a: str, b: str) -> str:
    return f"{a}|{b}"


@dataclass
class CrossDayState:
    """跨日狀態。鍵＝`"<market 或 stock_id>|<horizon>"`；`lines` 每鍵 6 個 `(state, streak)`。"""
    last_date: str | None = None
    market_lines: dict[str, list[LineState]] = field(default_factory=dict)
    market_line2: dict[str, list[float | None]] = field(default_factory=dict)
    stock_lines: dict[str, list[LineState]] = field(default_factory=dict)
    stock_line2: dict[str, list[float | None]] = field(default_factory=dict)
    adv: AdvTracker = field(default_factory=AdvTracker)
    meta: dict[str, Any] = field(default_factory=dict)      # 驅動端的環境標記（window／params_sha），載入時比對、不進計分

    # -- 二爻歷史 --
    def market_line2_t_minus_5(self, market: str, horizon: str) -> float | None:
        h = self.market_line2.get(_k(market, horizon), [])
        return h[-MARKET_LINE2_HIST] if len(h) >= MARKET_LINE2_HIST else None

    def market_line2_t_minus_5_all(self, market: str) -> dict[str, float | None]:
        return {h: self.market_line2_t_minus_5(market, h) for h in HORIZONS}

    def stock_line2_history(self, stock_id: str, horizon: str) -> list[float | None]:
        """恰 9 筆（T−9…T−1，升冪），不足者左補 None。"""
        h = self.stock_line2.get(_k(stock_id, horizon), [])
        return [None] * (STOCK_LINE2_HIST - len(h)) + list(h[-STOCK_LINE2_HIST:])

    def stock_line2_history_all(self, stock_id: str) -> dict[str, list[float | None]]:
        return {h: self.stock_line2_history(stock_id, h) for h in HORIZONS}

    def push_market_line2(self, market: str, horizon: str, score: float | None) -> None:
        _push(self.market_line2, _k(market, horizon), score, MARKET_LINE2_HIST)

    def push_stock_line2(self, stock_id: str, horizon: str, score: float | None) -> None:
        _push(self.stock_line2, _k(stock_id, horizon), score, STOCK_LINE2_HIST)

    # -- 遲滯 --
    def advance_lines(self, kind: str, key: str, horizon: str, scores: Sequence[float | None],
                      rules: Rules) -> tuple[list[int] | None, list[int]]:
        """六爻各套一次 `hysteresis_step`，寫回狀態，回 `(formal_lines 六位 0/1 或 None, streaks 六個)`。
        任一爻 state 仍為 None（首日就缺分數）→ `formal_lines=None`（`assemble_row` 接受 None）。"""
        if kind not in ("market", "stock"):
            raise ReplayStateError(f"kind 必須是 market/stock：{kind!r}")
        if len(scores) != len(LINES):
            raise ReplayStateError(f"scores 需 {len(LINES)} 個：{len(scores)}")
        table = self.market_lines if kind == "market" else self.stock_lines
        k = _k(key, horizon)
        prev = table.get(k) or [(None, 0)] * len(LINES)
        cur: list[LineState] = []
        for (st, streak), sc in zip(prev, scores):
            n_st, n_streak, _flip = hysteresis_step(st, streak, sc, rules)
            cur.append((n_st, int(n_streak)))
        table[k] = cur
        bits = [1 if st == YANG else 0 for st, _ in cur]
        formal = None if any(st is None for st, _ in cur) else bits
        return formal, [s for _, s in cur]

    # -- 序列化 --
    def to_dict(self) -> dict:
        return {
            "schema": STATE_SCHEMA,
            "last_date": self.last_date,
            "market_lines": {k: [[st, streak] for st, streak in v] for k, v in sorted(self.market_lines.items())},
            "market_line2": {k: list(v) for k, v in sorted(self.market_line2.items())},
            "stock_lines": {k: [[st, streak] for st, streak in v] for k, v in sorted(self.stock_lines.items())},
            "stock_line2": {k: list(v) for k, v in sorted(self.stock_line2.items())},
            "adv": self.adv.state(),
            "meta": dict(self.meta),
        }

    @classmethod
    def from_dict(cls, d: Mapping[str, Any]) -> "CrossDayState":
        if d.get("schema") != STATE_SCHEMA:
            raise ReplayStateError(f"CrossDayState schema 不符：{d.get('schema')!r} ≠ {STATE_SCHEMA}")
        def lines(m: Mapping[str, Any]) -> dict[str, list[LineState]]:
            out: dict[str, list[LineState]] = {}
            for k, v in m.items():
                if len(v) != len(LINES):
                    raise ReplayStateError(f"{k} 的爻狀態需 {len(LINES)} 個：{len(v)}")
                out[k] = [(st, int(streak)) for st, streak in v]
            return out
        def hist(m: Mapping[str, Any], cap: int) -> dict[str, list[float | None]]:
            return {k: [None if x is None else float(x) for x in v][-cap:] for k, v in m.items()}
        return cls(last_date=d.get("last_date"),
                   market_lines=lines(d.get("market_lines", {})),
                   market_line2=hist(d.get("market_line2", {}), MARKET_LINE2_HIST),
                   stock_lines=lines(d.get("stock_lines", {})),
                   stock_line2=hist(d.get("stock_line2", {}), STOCK_LINE2_HIST),
                   adv=AdvTracker.from_state(d["adv"]) if "adv" in d else AdvTracker(),
                   meta=dict(d.get("meta") or {}))

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, separators=(",", ":"), sort_keys=True)

    @classmethod
    def from_json(cls, s: str) -> "CrossDayState":
        return cls.from_dict(json.loads(s))


def _push(table: dict[str, list[float | None]], k: str, v: float | None, cap: int) -> None:
    h = table.setdefault(k, [])
    h.append(None if v is None else float(v))
    if len(h) > cap:
        del h[:-cap]


# ---------------------------------------------------------------------------
# WindowCache
# ---------------------------------------------------------------------------
class Ring:
    """固定容量的列環形緩衝：`push(row)`、`view()` 回**升冪、連續**的 `(n, ncol)` 複本。"""
    __slots__ = ("buf", "cap", "n", "pos")

    def __init__(self, cap: int, ncol: int) -> None:
        self.buf = np.empty((cap, ncol), dtype=np.float64)
        self.cap, self.n, self.pos = cap, 0, 0

    def push(self, row: Sequence[float]) -> None:
        self.buf[self.pos] = row
        self.pos = (self.pos + 1) % self.cap
        self.n = min(self.n + 1, self.cap)

    def view(self) -> np.ndarray:
        if self.n < self.cap:
            return self.buf[:self.n].copy()
        return np.concatenate((self.buf[self.pos:], self.buf[:self.pos]))

    def last(self, col: int) -> float | None:
        if self.n == 0:
            return None
        return float(self.buf[(self.pos - 1) % self.cap, col])


def _f(v: Any) -> float:
    if v is None:
        return float("nan")
    try:
        return float(v)
    except (TypeError, ValueError):
        return float("nan")


class _DatedSeries:
    """自帶日期的序列（融資餘額、期貨 OI、VIX、匯率）。"""
    __slots__ = ("dates", "vals")

    def __init__(self, cap: int) -> None:
        self.dates: deque[str] = deque(maxlen=cap)
        self.vals: deque[float] = deque(maxlen=cap)

    def push(self, d: str, v: float) -> None:
        if self.dates and d <= self.dates[-1]:
            if d == self.dates[-1]:                  # 同日重送：後者覆蓋（與 series_by_date「取最後一筆」同）
                self.vals[-1] = v
                return
            raise ReplayStateError(f"序列日期倒退：{d} ≤ {self.dates[-1]}")
        self.dates.append(d)
        self.vals.append(v)

    def array(self) -> np.ndarray | None:
        return np.asarray(self.vals, dtype=np.float64) if self.vals else None


class WindowCache:
    """視窗快取。`ingest(bundle)` 必須**嚴格升冪逐日**呼叫。"""

    def __init__(self, pool: PitPool, factors: Mapping[str, tuple[list[str], list[float]]],
                 *, window: int = WINDOW_N, ma_windows: Sequence[int] = MA_WINDOWS,
                 hl_windows: Sequence[int] = HL_WINDOWS) -> None:
        """`pool`：`feed.load_pool()` 回的 `universe.PitPool`（point-in-time：市場別一律 `pool.listed(sid, T)`，
        產業別 `pool.industry_of(sid)`）；`factors`：`feed.load_factors()` 第一個回傳值（`sid → (ex_dates, cum)`）。"""
        if window < 1:
            raise ReplayStateError(f"window 需 ≥1：{window}")
        if not isinstance(pool, PitPool):
            raise ReplayStateError(f"pool 必須是 universe.PitPool（point-in-time），得到 {type(pool).__name__}")
        self.window = int(window)
        self.pool = pool
        self.factors = factors
        self.ma_windows = tuple(int(w) for w in ma_windows)
        self.hl_windows = tuple(int(w) for w in hl_windows)
        self.last_date: str | None = None
        self.tpe_dates: deque[str] = deque(maxlen=self.window)     # 任一市場有指數列的日子（給 line 6 stale_days）
        self._stock: dict[str, Ring] = {}
        # 大盤對齊序列：每市場一個 Ring，欄＝ open high low close amount_k foreign_k trust_k n_stocks adv dec
        #   ＋ above_ma×len(ma) ＋ new_high×len(hl) ＋ new_low×len(hl) ＋ amount_up amount_total
        self._mk_cols = ["open", "high", "low", "close", "amount", "foreign", "trust", "n_stocks", "advance", "decline"]
        self._mk_cols += [f"above_ma_{w}" for w in self.ma_windows]
        self._mk_cols += [f"new_high_{w}" for w in self.hl_windows] + [f"new_low_{w}" for w in self.hl_windows]
        self._mk_cols += ["amount_up", "amount_total"]
        self._mk_idx = {c: i for i, c in enumerate(self._mk_cols)}
        self._mk: dict[str, Ring] = {m: Ring(self.window, len(self._mk_cols)) for m in MARKETS}
        self._mk_dates: dict[str, deque[str]] = {m: deque(maxlen=self.window) for m in MARKETS}
        self.margin = _DatedSeries(self.window)
        self.foreign_oi = _DatedSeries(self.window)
        self.vix = _DatedSeries(self.window)
        self.fx = _DatedSeries(self.window)
        self.basis = _DatedSeries(self.window)
        self.us_dates: deque[str] = deque(maxlen=self.window)
        self.us = Ring(self.window, 4)                              # spx_close spx_high spx_low sox_close
        self.prev_near: str | None = None
        self.contract_rolled = False
        self.today_industry: dict[str, dict[str, dict]] = {}
        self.today_p_cs: dict[str, dict[str, dict[int, float]]] = {}
        self.today_amounts: dict[str, float] = {}                  # 當日有成交檔的成交金額（給 AdvTracker.push_day）
        self._today_ids: list[str] = []
        self.shares: dict[str, float] = {}                         # 最近一次申報的發行股數（無列則沿用前值）

    # -- ingest --
    def ingest(self, b: DayBundle) -> None:
        T = str(b.tpe_date)
        if self.last_date is not None and T <= self.last_date:
            raise ReplayStateError(f"ingest 必須嚴格升冪：{T} ≤ {self.last_date}")
        idx_close: dict[str, float] = {}
        for m in MARKETS:
            row = b.index.get(m)
            if not row or row.get("close") is None:
                continue
            idx_close[m] = float(row["close"])
            off = b.official.get(m) or {}
            br = b.breadth.get(m)
            vals = [_f(row.get("open")), _f(row.get("high")), _f(row.get("low")), _f(row.get("close")),
                    _f(off.get("amount_k")), _f(off.get("foreign_net_k")), _f(off.get("trust_net_k"))]
            if br:
                vals += [_f(br.get("n_stocks")), _f(br.get("advance_count")), _f(br.get("decline_count"))]
                vals += [_f((br.get("above_ma") or {}).get(w)) for w in self.ma_windows]
                vals += [_f((br.get("new_high") or {}).get(w)) for w in self.hl_windows]
                vals += [_f((br.get("new_low") or {}).get(w)) for w in self.hl_windows]
                vals += [_f(br.get("amount_up")), _f(br.get("amount_total"))]
            else:
                vals += [float("nan")] * (len(self._mk_cols) - len(vals))
            self._mk[m].push(vals)
            self._mk_dates[m].append(T)
        if idx_close:
            self.tpe_dates.append(T)
        # 個股：有成交且所屬市場有指數列才推進
        self.today_amounts = {}
        self._today_ids = []
        for sid, r in b.stocks.items():
            m = self.pool.listed(str(sid), T)                        # PIT：T 日不在池（興櫃期／不在快照）就不推進
            if m is None:
                continue
            if m not in idx_close or not is_traded_row(r):
                continue
            fac = 1.0
            ev = self.factors.get(str(sid))
            if ev:
                fac = factor_at(T, ev[0], ev[1])
            ring = self._stock.get(sid)
            if ring is None:
                ring = self._stock[sid] = Ring(self.window, len(STOCK_COLS))
            vol = r.get("volume", r.get("Trading_Volume"))                 # 兩鍵擇一（驗收建議 #2）
            ring.push([_f(r.get("open")) * fac, _f(r.get("high")) * fac, _f(r.get("low")) * fac, _f(r.get("close")) * fac,
                       _f(vol) / 1000.0, idx_close[m],
                       _f(r.get("foreign_net")), _f(r.get("trust_net")),          # 無列先存 NaN，讀取端再決定補 0 或整欄缺
                       _f(r.get("margin_balance")), _f(r.get("short_sale_balance"))])
            self._today_ids.append(sid)
            so = r.get("shares_outstanding")
            if so is not None:
                try:
                    if float(so) > 0:
                        self.shares[sid] = float(so)
                except (TypeError, ValueError):
                    pass
            amt = r.get("amount")
            if amt is not None:
                self.today_amounts[sid] = float(amt)
        # 自帶日期序列
        if b.total_margin is not None:
            self.margin.push(T, float(b.total_margin))
        if b.foreign_net_oi is not None:
            self.foreign_oi.push(T, float(b.foreign_net_oi))
        if b.vix is not None:
            self.vix.push(T, float(b.vix))
        for d, v in b.fx:
            self.fx.push(str(d), float(v))
        for d, c, h, lo, sox in b.us:
            d = str(d)
            if self.us_dates and d <= self.us_dates[-1]:
                if d == self.us_dates[-1]:
                    self.us.buf[(self.us.pos - 1) % self.us.cap] = [_f(c), _f(h), _f(lo), _f(sox)]
                    continue
                raise ReplayStateError(f"美股序列日期倒退：{d} ≤ {self.us_dates[-1]}")
            self.us_dates.append(d)
            self.us.push([_f(c), _f(h), _f(lo), _f(sox)])
        # 期貨基差（TX 近月 vs 加權指數現貨）
        fut = b.futures or {}
        contracts = list(fut.get("contracts") or [])
        near = near_month(T, contracts) if contracts else None
        self.contract_rolled = rolled(self.prev_near, near)
        if near is not None:
            bp = basis_pct((fut.get("close") or {}).get(near), idx_close.get("twse"))
            if bp is not None:
                self.basis.push(T, bp)
            self.prev_near = near
        self.today_industry = b.industry or {}
        self.today_p_cs = b.p_cs or {}
        self.last_date = T

    # -- 輸出 --
    def has_market(self, market: str) -> bool:
        return self._mk[market].n > 0

    def market_dates(self, market: str) -> list[str]:
        """該市場對齊序列的日期軸（＝有指數列的日子），升冪。"""
        return list(self._mk_dates[market])

    def market_inputs(self, market: str, tpe_date: str, cross: CrossDayState) -> MarketInputs:
        """`own_state`／`other_market_state` 留 None，由 `step()` 在兩市場遲滯後填。"""
        if market not in MARKETS:
            raise ReplayStateError(f"未知市場：{market}")
        if tpe_date != self.last_date:
            raise ReplayDateError(tpe_date, self.last_date)
        a = self._mk[market].view()
        c = self._mk_idx
        n = a.shape[0]
        def col(name: str) -> np.ndarray | None:
            return a[:, c[name]] if n else None
        nn = col("n_stocks")
        ad = None
        if n:
            diff = a[:, c["advance"]] - a[:, c["decline"]]
            ad = np.cumsum(np.where(np.isnan(diff), 0.0, diff))
        def ratio(name: str) -> np.ndarray | None:
            if not n:
                return None
            den = np.where(nn > 0, nn, np.nan)
            return a[:, c[name]] / den
        us = self.us.view()
        fx = self.fx.array()
        return MarketInputs(
            market=market, tpe_date=tpe_date, tpe_dates=list(self.tpe_dates),
            index_open=col("open"), index_high=col("high"), index_low=col("low"), index_close=col("close"),
            amount=col("amount") if n and not np.isnan(a[:, c["amount"]]).all() else None,
            n_stocks=nn,
            above_ma_ratio={w: ratio(f"above_ma_{w}") for w in self.ma_windows},
            advance_ratio=ratio("advance"),
            new_high_low_ratio={w: (a[:, c[f"new_high_{w}"]] - a[:, c[f"new_low_{w}"]]) / np.where(nn > 0, nn, np.nan)
                                if n else None for w in self.hl_windows},
            ad_line=ad,
            up_amount_ratio=(a[:, c["amount_up"]] / np.where(a[:, c["amount_total"]] > 0, a[:, c["amount_total"]], np.nan)) if n else None,
            foreign_net_amount=col("foreign") if n and not np.isnan(a[:, c["foreign"]]).all() else None,
            trust_net_amount=col("trust") if n and not np.isnan(a[:, c["trust"]]).all() else None,
            margin_balance=self.margin.array(),
            foreign_net_oi=self.foreign_oi.array(),
            basis=self.basis.array(), contract_rolled=self.contract_rolled,
            vix=self.vix.array(), put_call_ratio=None,
            us_dates=list(self.us_dates) if self.us_dates else None,
            spx_close=us[:, 0] if us.shape[0] else None, spx_high=us[:, 1] if us.shape[0] else None,
            spx_low=us[:, 2] if us.shape[0] else None, sox_close=us[:, 3] if us.shape[0] else None,
            fx_dates=list(self.fx.dates) if fx is not None else None, fx_usdtwd=fx,
            line2_score_t_minus_5=cross.market_line2_t_minus_5_all(market),
        )

    def stock_ids_today(self) -> list[str]:
        """當日有推進（有成交且所屬市場有指數列）的個股，決定性排序。"""
        return sorted(self._today_ids)

    def stock_window(self, stock_id: str) -> np.ndarray | None:
        r = self._stock.get(stock_id)
        return r.view() if r is not None and r.n else None

    def stock_inputs(self, stock_id: str, horizon: str, tpe_date: str, cross: CrossDayState,
                     market_direction_score: Mapping[str, float | None] | None = None,
                     *, is_financial: bool = False, shares_outstanding: float | None = None,
                     monthly_revenue: Sequence[tuple[str, float]] | None = None,
                     industry_median_3m_yoy: float | None = None, industry_revenue_n: int | None = None,
                     fundamentals: dict | None = None) -> StockInputs:
        """一檔一期間的 `StockInputs`。`p_cs_long_excess`／`industry_n` 依 `horizon` 取 L3 長視窗
        （short→10、swing→20、mid→60，`scan.HORIZON_BY_L3_LONG_WINDOW`）；基本面四項由 13b 供給、預設 None；
        `shares_outstanding` 未指定時取快取內最近一次申報值（無則 None → 五爻族 E 缺值）。"""
        if horizon not in HORIZONS:
            raise ReplayStateError(f"未知期間：{horizon}")
        if tpe_date != self.last_date:
            raise ReplayDateError(tpe_date, self.last_date)
        market = self.pool.listed(stock_id, tpe_date)
        if market is None:
            raise ReplayStateError(f"{stock_id} 於 {tpe_date} 不在池內")
        industry = self.pool.industry_of(stock_id)
        a = self.stock_window(stock_id)
        wl = L3_WINDOW_BY_HORIZON[horizon]
        ind = (self.today_industry.get(market) or {}).get(industry) if industry else None
        med = dict((ind or {}).get("median", {}))
        ind_n = (ind or {}).get("n", {}).get(wl)
        ab = (ind or {}).get("above_ma", {}).get(INDUSTRY_MA_WINDOW)
        ab_ratio = (ab[0] / ab[1]) if ab and ab[1] > 0 else None
        pcs = ((self.today_p_cs.get(market) or {}).get(stock_id) or {}).get(wl)
        def col(i: int) -> np.ndarray | None:
            return a[:, i] if a is not None else None
        def chip(i: int, fill: float | None) -> np.ndarray | None:
            """與 `score_io.stock_inputs_from_stores` 的 `aligned()` 同語意：視窗內**完全沒有列**→ None（整欄缺，
            引擎回 Missing）；部分缺列→法人補 0.0（該日無買賣即不出列，屬假設）、餘額保留 NaN（補 0 會製造假變化率）。"""
            if a is None:
                return None
            v = a[:, i]
            if np.isnan(v).all():
                return None
            return np.where(np.isnan(v), fill, v) if fill is not None else v
        fnet, tnet, mbal, sbal = chip(6, 0.0), chip(7, 0.0), chip(8, None), chip(9, None)
        return StockInputs(
            market=market, stock_id=stock_id, tpe_date=tpe_date, industry=industry, is_financial=is_financial,
            open=col(0), high=col(1), low=col(2), close=col(3), volume=col(4), index_close=col(5),
            industry_median_return={int(k): v for k, v in med.items()}, industry_n=ind_n,
            industry_above_ma_ratio={INDUSTRY_MA_WINDOW: ab_ratio}, p_cs_long_excess=pcs,
            monthly_revenue=monthly_revenue, industry_median_3m_yoy=industry_median_3m_yoy,
            industry_revenue_n=industry_revenue_n, fundamentals=fundamentals,
            foreign_net_shares=fnet, trust_net_shares=tnet,
            margin_balance=mbal, margin_eligible=mbal is not None,
            short_sale_balance=sbal, shares_outstanding=self.shares.get(stock_id) if shares_outstanding is None else shares_outstanding,
            line2_score_history=cross.stock_line2_history_all(stock_id),
            market_direction_score=dict(market_direction_score or {}),
        )

    def nbytes(self) -> int:
        """numpy 緩衝的位元組數（記憶體量級驗收用；不含 deque 與 dict 開銷）。"""
        return sum(r.buf.nbytes for r in self._stock.values()) + sum(r.buf.nbytes for r in self._mk.values()) + self.us.buf.nbytes

