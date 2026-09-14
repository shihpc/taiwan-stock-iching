"""原料列 → `DayBundle` 各欄的**純函式**建構器（每日班 D-1）——回補層與每日班共用。

輸入一律是「一日、一個資料集」的 **dict 列**（鍵＝FinMind／官方回應的欄名，也就是 `raw_*` 表的欄名），
不管它來自 SQLite（`replay_io.ReplaySource`）還是當日 API（`daily_run`）。**兩層都走這裡**，
`DayBundle` 的語意只有一份定義，parity 由構造保證（`docs/P2-DAILY-PLAN.md` §0）。

設計原則：
- **與列序無關**：輸出 dict 固定序（市場依 `MARKETS`、個股代號升冪）；同鍵多列一律「後者覆蓋」用 dict 收斂；法人淨額先以「股」在**固定 name 序**加總、最後才 ÷1000
  （兩個 double 相加滿足交換律，故與 `score_io` 的加總逐位相同）。
- 缺欄／非數字 → NaN 或 None，不炸；表整個缺由呼叫端給空列表。
- 不 import sqlite3、不做任何 I/O。
"""
from __future__ import annotations

from typing import Any, Iterable, Mapping

from . import official_parse as OP
from .feed import INDEX_ID
from .futures import SESSION_REGULAR
from .score.params import MARKETS
from .score_io import INST_FOREIGN_NAMES, INST_TRUST_NAMES, TOTAL_MARGIN_NAME, VIX_COLUMN, VIX_TIME_COLUMN

US_SPX, US_SOX = "^GSPC", "^SOX"
TX = "TX"
FOREIGN_LABEL = "外資"
OFFICIAL_INST_TABLE = {"twse": "raw_twse_bfi82u", "tpex": "raw_tpex_inst_summary"}
OFFICIAL_MONTH_TABLE = {"twse": "raw_twse_fmtqik", "tpex": "raw_tpex_trading_index"}
Row = Mapping[str, Any]


def num(v: Any) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return float("nan")


def _opt(v: Any) -> float | None:
    """None／非數字 → None（給 `DayBundle` 裡「缺就 None」的欄）。"""
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------------------
def index_from_rows(rows: Iterable[Row]) -> dict[str, dict[str, float | None]]:
    """`raw_index_price` 當日列（`stock_id` ∈ TAIEX／TPEx）→ `DayBundle.index`。同市場多列後者覆蓋；
    `close` 缺不跳列（`WindowCache.ingest` 才判「close None＝該市場當日無指數」）。"""
    out: dict[str, dict[str, float | None]] = {}
    by_id = {v: k for k, v in INDEX_ID.items()}
    for r in rows:
        m = by_id.get(str(r.get("stock_id")))
        if m is None:
            continue
        out[m] = {"open": _opt(r.get("open")), "high": _opt(r.get("max")), "low": _opt(r.get("min")), "close": _opt(r.get("close"))}
    return {m: out[m] for m in MARKETS if m in out}                 # 固定市場序，與輸入列序無關


def stocks_from_rows(price_rows: Iterable[Row], pool: Mapping[str, Any], *, inst_rows: Iterable[Row] = (),
                     margin_rows: Iterable[Row] = (), short_rows: Iterable[Row] = (),
                     shareholding_rows: Iterable[Row] = ()) -> dict[str, dict[str, Any]]:
    """價量＋籌碼 → `DayBundle.stocks`（只收池內；**原始價**）。

    籌碼無列 → 該欄 None（`WindowCache` 讀取端再決定補 0／NaN／整欄缺）；法人淨額＝Σ(buy−sell) 股 ÷ 1000 張，
    外資＝`INST_FOREIGN_NAMES` 兩個 name 依固定序相加；同 (stock_id, name) 多列**後者覆蓋**（同 `score_io.load_stock_inst_net`）。
    價量列 `close` 缺不跳列（`is_traded_row` 看 `Trading_Volume`，與舊 `replay_io._stocks` 同語意）。"""
    out: dict[str, dict[str, Any]] = {}
    for r in price_rows:
        sid = str(r.get("stock_id"))
        if sid not in pool:
            continue
        out[sid] = {"open": _opt(r.get("open")), "high": _opt(r.get("max")), "low": _opt(r.get("min")), "close": _opt(r.get("close")),
                    "Trading_Volume": _opt(r.get("Trading_Volume")), "volume": _opt(r.get("Trading_Volume")),
                    "amount": _opt(r.get("Trading_money")),
                    "foreign_net": None, "trust_net": None, "margin_balance": None, "short_sale_balance": None,
                    "shares_outstanding": None}
    if not out:
        return out
    # 法人：先收斂成 (sid, name) → net 股（後者覆蓋），再依固定 name 序加總
    net_by: dict[tuple[str, str], float] = {}
    for r in inst_rows:
        sid, name = str(r.get("stock_id")), str(r.get("name"))
        if sid in out and (name in INST_FOREIGN_NAMES or name in INST_TRUST_NAMES):
            net_by[(sid, name)] = num(r.get("buy")) - num(r.get("sell"))
    for sid in out:
        for key, names in (("foreign_net", INST_FOREIGN_NAMES), ("trust_net", INST_TRUST_NAMES)):
            acc: float | None = None
            for name in names:
                v = net_by.get((sid, name))
                if v is None:
                    continue
                acc = v if acc is None else acc + v
            if acc is not None:
                out[sid][key] = acc / 1000.0
    for r in margin_rows:
        s = out.get(str(r.get("stock_id")))
        if s is not None and "MarginPurchaseTodayBalance" in r:
            s["margin_balance"] = num(r.get("MarginPurchaseTodayBalance"))
    for r in short_rows:
        s = out.get(str(r.get("stock_id")))
        if s is not None and "SBLShortSalesCurrentDayBalance" in r:
            s["short_sale_balance"] = num(r.get("SBLShortSalesCurrentDayBalance"))
    for r in shareholding_rows:
        s = out.get(str(r.get("stock_id")))
        if s is not None and r.get("NumberOfSharesIssued") is not None:
            s["shares_outstanding"] = num(r.get("NumberOfSharesIssued"))
    return {sid: out[sid] for sid in sorted(out)}                    # 代號升冪，與輸入列序無關


def official_inst(market: str, body: Any) -> tuple[float, float]:
    """當日官方法人 JSON（已 `json.loads`；twse＝BFI82U、tpex＝TPEx summary）→ `(foreign_net_k, trust_net_k)`。
    解析失敗 raise（`OfficialParseError`／`KeyError`／`TypeError`／`ValueError`），由呼叫端記錄。"""
    parse = OP.parse_bfi82u if market == "twse" else OP.parse_tpex_inst_summary
    d = parse(body)
    return float(d["f_net_k"]), float(d["t_net_k"])          # official_parse 的 tag：f／t／d


def parse_month_body(market: str, body: Any) -> dict[str, float]:
    """月表整月解析（twse＝FMTQIK、tpex＝tradingIndex）→ `{ISO 日期: 成交金額千元}`；失敗 raise `OfficialParseError`。
    回補層對同月快取一次、每日班只解析當月一次。"""
    if market == "twse":
        return {d: float(r["turnover_k"]) for d, r in OP.parse_fmtqik_month(body).items() if r.get("turnover_k") is not None}
    return {d: float(v) for d, v in OP.parse_tpex_trading_index_month(body).items()}


def official_day(market: str, T: str, inst_body: Any | None, month_amounts: Mapping[str, float] | None
                 ) -> tuple[dict[str, float | None], list[tuple[str, str, str]]]:
    """→ `DayBundle.official[market]`＋錯誤清單 `[(日期, 表, 原因)]`。`inst_body` 缺→法人金額 None；
    `month_amounts`（`parse_month_body` 結果）缺或無 T→`amount_k` None。"""
    out: dict[str, float | None] = {"amount_k": None, "foreign_net_k": None, "trust_net_k": None}
    errs: list[tuple[str, str, str]] = []
    if inst_body is not None:
        try:
            out["foreign_net_k"], out["trust_net_k"] = official_inst(market, inst_body)
        except (OP.OfficialParseError, KeyError, TypeError, ValueError) as e:
            errs.append((T, OFFICIAL_INST_TABLE[market], str(e)))
    if month_amounts:
        v = month_amounts.get(T)
        if v is not None:
            out["amount_k"] = float(v)
    return out, errs


def futures_from_rows(rows: Iterable[Row]) -> dict[str, Any]:
    """`raw_futures_daily` 當日列 → `DayBundle.futures`：只收 `TX`、一般交易時段（無 `trading_session` 欄則不濾）。"""
    close: dict[str, float] = {}
    for r in rows:
        if str(r.get("futures_id")) != TX:
            continue
        if "trading_session" in r and r.get("trading_session") != SESSION_REGULAR:
            continue
        c, v = r.get("contract_date"), r.get("close")
        if c is None or v is None:
            continue
        close[str(c)] = num(v)
    contracts = sorted(close)
    return {"contracts": contracts, "close": {c: close[c] for c in contracts}} if close else {}


def futures_oi_from_rows(rows: Iterable[Row]) -> float | None:
    """`raw_futures_inst` 當日列 → 外資 TX 淨未平倉（多−空）；多列後者覆蓋。"""
    out: float | None = None
    for r in rows:
        if str(r.get("futures_id")) == TX and str(r.get("institutional_investors")) == FOREIGN_LABEL:
            out = num(r.get("long_open_interest_balance_volume")) - num(r.get("short_open_interest_balance_volume"))
    return out


def total_margin_from_rows(rows: Iterable[Row]) -> float | None:
    out: float | None = None
    for r in rows:
        if str(r.get("name")) == TOTAL_MARGIN_NAME:
            out = num(r.get("TodayBalance"))
    return out


def vix_from_rows(rows: Iterable[Row]) -> float | None:
    """盤中逐筆取 `time` 最大者；無 `time` 欄則取最後一列。"""
    best: tuple[str, float] | None = None
    for r in rows:
        if VIX_COLUMN not in r:
            continue
        t = "" if r.get(VIX_TIME_COLUMN) is None else str(r.get(VIX_TIME_COLUMN))
        if best is None or t >= best[0]:
            best = (t, num(r.get(VIX_COLUMN)))
    return None if best is None else best[1]


def us_from_rows(rows: Iterable[Row]) -> list[tuple[str, float, float, float, float]]:
    """`raw_us_index` 列（任意日期範圍）→ `[(date, spx_c, spx_h, spx_l, sox_c)]` 升冪；以 ^GSPC 為軸，SOX 缺補 NaN。"""
    by: dict[str, dict[str, Row]] = {}
    for r in rows:
        by.setdefault(str(r.get("date")), {})[str(r.get("stock_id"))] = r
    out = []
    for d in sorted(by):
        spx = by[d].get(US_SPX)
        if spx is None:
            continue
        sox = by[d].get(US_SOX)
        out.append((d, num(spx.get("Close")), num(spx.get("High")), num(spx.get("Low")), num(sox.get("Close")) if sox else float("nan")))
    return out


def fx_from_rows(rows: Iterable[Row]) -> list[tuple[str, float]]:
    """`raw_fx_usd` 列 → `[(date, (spot_buy+spot_sell)/2)]` 升冪；同日多列後者覆蓋。"""
    by: dict[str, float] = {}
    for r in rows:
        by[str(r.get("date"))] = (num(r.get("spot_buy")) + num(r.get("spot_sell"))) / 2.0
    return [(d, by[d]) for d in sorted(by)]
