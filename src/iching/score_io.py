"""DB 讀取層：從回補 SQLite 的 `raw_<key>` 表組出計分函式的輸入（`MarketInputs`／`StockInputs`）。

**這是 `src/iching/` 內唯一碰 sqlite（透過 `store.Store`）的計分相關模組**；`src/iching/score/` 只吃純 dict／numpy。
每日班層日後以 git 內狀態＋當日 API 組出**同一組**輸入 dict 餵同一組計分函式（B3.2 兩層 parity 的前提）。

目前為**介面＋最小實作**：指數／美股／匯率／期貨淨未平倉／全市場融資／VIX／個股價量與籌碼可讀；
廣度聚合（B1.2）、上漲股成交占比（B1.3 B）、官方法人金額（B1.4）、基差（B1.5 B）、產業聚合與基本面（B2.1／B2.3 B／B2.6 B）
**尚未實作**（對應欄位留 None → 該族缺值、權重重配），列在 `NOT_YET_LOADED`。
欄位名依 `config.DatasetSpec.note` 記載的實測／家族用法；標 `(unverified)` 者未在本容器親眼看到列。
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Sequence

import numpy as np

from .store import Store
from .score.market import MarketInputs
from .score.stock import StockInputs

NOT_YET_LOADED = (
    "B1.2 廣度（above_ma_ratio／advance_ratio／new_high_low_ratio／ad_line／n_stocks；需全市場切片×PIT 名單聚合）",
    "B1.3 族 B 上漲股成交占比（up_amount_ratio）",
    "B1.4 官方法人金額（foreign_net_amount／trust_net_amount；raw_twse_bfi82u／raw_tpex_inst_summary 原始 JSON 待解析）",
    "B1.3／B1.4 市場成交金額（amount；規格來源 TWSE FMTQIK／TPEx tradingIndex，raw_twse_fmtqik／raw_tpex_trading_index 月表待解析。"
    "預設缺值；`amount_source='index_trading_money'` 才以指數列 Trading_money 暫代，見 market_inputs_from_stores）",
    "B1.5 族 B 基差（basis／contract_rolled；raw_futures_daily 近月判定）",
    "B2.1 基本面（monthly_revenue／fundamentals；available_at 過濾）",
    "B2.3 族 B／B2.6 族 B 產業聚合、P_cs 橫斷面百分位",
)
FOREIGN_LABEL = "外資"            # TaiwanFuturesInstitutionalInvestors.institutional_investors（taiwan-flows futures.py FOREIGN）
TX = "TX"
INST_FOREIGN_NAMES = ("Foreign_Investor", "Foreign_Dealer_Self")   # taiwan-flows CLAUDE.md：外資＝兩者相加
INST_TRUST_NAMES = ("Investment_Trust",)
TOTAL_MARGIN_NAME = "MarginPurchaseMoney"   # (unverified) TaiwanStockTotalMarginPurchaseShortSale.name 之一，實測前請確認
VIX_COLUMN = "VIX"                          # (unverified) TaiwanOptionVix 欄位名未實測（config note）


def _f(v) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return float("nan")


def series_by_date(store: Store, table: str, cols: Sequence[str], end_date: str, n: int,
                   where: str = "", params: tuple = ()) -> dict[str, list]:
    """取 `date <= end_date` 的最後 n 個**不重複日期**列（升冪）。同日多列（不同 cov_key 重複落地）取最後一筆。"""
    if not store.table_exists(table):
        return {"dates": [], **{c: [] for c in cols}}
    have = store.columns(table)
    for c in cols:
        if c not in have:
            return {"dates": [], **{c: [] for c in cols}}
    w = "date <= ?" + (f" AND ({where})" if where else "")
    rows = store.fetch_rows(table, w, (end_date, *params), cols="date, " + ", ".join(f'"{c}"' for c in cols))
    by: dict[str, tuple] = {}
    for r in rows:
        by[r[0]] = tuple(r[i + 1] for i in range(len(cols)))
    dates = sorted(by)[-n:]
    return {"dates": dates, **{c: [by[d][i] for d in dates] for i, c in enumerate(cols)}}


def load_index(stores: dict[str, Store], market: str, end_date: str, n: int) -> dict[str, Any]:
    """加權（TAIEX）／櫃買（TPEx）日 OHLC＋成交金額（raw_index_price：date/open/max/min/close/Trading_money）。"""
    data_id = {"twse": "TAIEX", "tpex": "TPEx"}[market]
    s = series_by_date(stores["prices"], "raw_index_price", ["open", "max", "min", "close", "Trading_money"], end_date, n,
                       "stock_id = ?", (data_id,))
    return {"dates": s["dates"], "open": [_f(x) for x in s["open"]], "high": [_f(x) for x in s["max"]],
            "low": [_f(x) for x in s["min"]], "close": [_f(x) for x in s["close"]], "amount": [_f(x) for x in s["Trading_money"]]}


def load_us_index(stores: dict[str, Store], symbol: str, end_date: str, n: int) -> dict[str, Any]:
    """raw_us_index：date/stock_id/Close/High/Low（USStockPrice）。"""
    s = series_by_date(stores["market"], "raw_us_index", ["Close", "High", "Low"], end_date, n, "stock_id = ?", (symbol,))
    return {"dates": s["dates"], "close": [_f(x) for x in s["Close"]], "high": [_f(x) for x in s["High"]], "low": [_f(x) for x in s["Low"]]}


def load_fx_usd(stores: dict[str, Store], end_date: str, n: int) -> dict[str, Any]:
    """raw_fx_usd：spot_buy／spot_sell 取中價（家族 taiwan-flow-live-v2 build_us.py 同法）。"""
    s = series_by_date(stores["market"], "raw_fx_usd", ["spot_buy", "spot_sell"], end_date, n)
    mid = [(_f(b) + _f(a)) / 2.0 for b, a in zip(s["spot_buy"], s["spot_sell"])]
    return {"dates": s["dates"], "usdtwd": mid}


def load_futures_foreign_net_oi(stores: dict[str, Store], end_date: str, n: int) -> dict[str, Any]:
    """raw_futures_inst：外資台指期 long − short 未平倉口數。"""
    s = series_by_date(stores["market"], "raw_futures_inst",
                       ["long_open_interest_balance_volume", "short_open_interest_balance_volume"], end_date, n,
                       "futures_id = ? AND institutional_investors = ?", (TX, FOREIGN_LABEL))
    return {"dates": s["dates"], "net_oi": [_f(l) - _f(sh) for l, sh in zip(s["long_open_interest_balance_volume"], s["short_open_interest_balance_volume"])]}


def load_total_margin(stores: dict[str, Store], end_date: str, n: int, name: str = TOTAL_MARGIN_NAME) -> dict[str, Any]:
    s = series_by_date(stores["market"], "raw_total_margin", ["TodayBalance"], end_date, n, "name = ?", (name,))
    return {"dates": s["dates"], "balance": [_f(x) for x in s["TodayBalance"]]}


def load_vix(stores: dict[str, Store], end_date: str, n: int, column: str = VIX_COLUMN) -> dict[str, Any]:
    s = series_by_date(stores["market"], "raw_vix", [column], end_date, n)
    return {"dates": s["dates"], "vix": [_f(x) for x in s[column]]}


def load_stock_ohlcv(stores: dict[str, Store], stock_id: str, end_date: str, n: int) -> dict[str, Any]:
    """raw_price_daily：open/max/min/close/Trading_Volume（股 → 張 ÷1000）。**原始價、未還原**（裁定 10 的還原係數另案）。"""
    s = series_by_date(stores["prices"], "raw_price_daily", ["open", "max", "min", "close", "Trading_Volume"], end_date, n,
                       "stock_id = ?", (stock_id,))
    return {"dates": s["dates"], "open": [_f(x) for x in s["open"]], "high": [_f(x) for x in s["max"]],
            "low": [_f(x) for x in s["min"]], "close": [_f(x) for x in s["close"]],
            "volume": [_f(x) / 1000.0 for x in s["Trading_Volume"]]}


def load_stock_inst_net(stores: dict[str, Store], stock_id: str, end_date: str, n: int, names: Sequence[str]) -> dict[str, Any]:
    """raw_inst_buysell（長格式 date/stock_id/name/buy/sell，單位股）→ 指定 name 加總的 (buy − sell) ÷ 1000 張。"""
    st = stores["chips"]
    table = "raw_inst_buysell"
    if not st.table_exists(table) or not {"name", "buy", "sell"} <= st.columns(table):
        return {"dates": [], "net": []}
    ph = ", ".join("?" for _ in names)
    rows = st.fetch_rows(table, f"stock_id = ? AND date <= ? AND name IN ({ph})", (stock_id, end_date, *names),
                         cols='date, name, "buy", "sell"')
    by: dict[str, dict[str, float]] = {}
    for r in rows:
        by.setdefault(r[0], {})[r[1]] = _f(r[2]) - _f(r[3])
    dates = sorted(by)[-n:]
    return {"dates": dates, "net": [sum(by[d].values()) / 1000.0 for d in dates]}


def load_stock_margin(stores: dict[str, Store], stock_id: str, end_date: str, n: int) -> dict[str, Any]:
    s = series_by_date(stores["chips"], "raw_margin", ["MarginPurchaseTodayBalance"], end_date, n, "stock_id = ?", (stock_id,))
    return {"dates": s["dates"], "balance": [_f(x) for x in s["MarginPurchaseTodayBalance"]]}


def load_stock_short_sale(stores: dict[str, Store], stock_id: str, end_date: str, n: int) -> dict[str, Any]:
    s = series_by_date(stores["chips"], "raw_short_sale_balance", ["SBLShortSalesCurrentDayBalance"], end_date, n, "stock_id = ?", (stock_id,))
    return {"dates": s["dates"], "balance": [_f(x) for x in s["SBLShortSalesCurrentDayBalance"]]}


def _or_none(xs: list) -> list | None:
    return xs if xs else None


AMOUNT_SOURCES = ("missing", "index_trading_money")


def market_inputs_from_stores(stores: dict[str, Store], market: str, tpe_date: str, tpe_dates: Sequence[str],
                              us_dates: Sequence[str], n: int = 320, *, amount_source: str = "missing") -> MarketInputs:
    """最小實作：只填目前可讀的來源；其餘留 None（該族缺值）。`tpe_dates`／`us_dates` 由 `calendar.load_calendar_json` 提供。

    `amount_source`（市場成交金額 AMT，B1.0／B1.3／B1.4 規格來源＝TWSE FMTQIK／TPEx tradingIndex，**尚未實作**）：
    - `"missing"`（預設，寧缺勿錯）：`amount=None` → 三爻族 A／C 與四爻族 A／B 缺值重配。
    - `"index_trading_money"`：**暫代來源**＝`raw_index_price` 指數列（TAIEX／TPEx）的 `Trading_money`。
      它是否等於官方市場成交金額**未驗**——Hetzner 首次 run 須對照 `raw_twse_fmtqik`／`raw_tpex_trading_index` 後才可採用。"""
    if amount_source not in AMOUNT_SOURCES:
        raise ValueError(f"amount_source must be one of {AMOUNT_SOURCES}, got {amount_source!r}")
    idx = load_index(stores, market, tpe_date, n)
    spx = load_us_index(stores, "^GSPC", tpe_date, n)
    sox = load_us_index(stores, "^SOX", tpe_date, n)
    fx = load_fx_usd(stores, tpe_date, n)
    oi = load_futures_foreign_net_oi(stores, tpe_date, n)
    mg = load_total_margin(stores, tpe_date, n)
    vx = load_vix(stores, tpe_date, n)
    # 美股序列以自身日期為索引，且必須與 us_dates 對齊：只取兩者都有的日期
    us_set = set(us_dates)
    keep = [i for i, d in enumerate(spx["dates"]) if d in us_set]
    us_d = [spx["dates"][i] for i in keep]
    sox_by = dict(zip(sox["dates"], sox["close"]))
    return MarketInputs(
        market=market, tpe_date=tpe_date, tpe_dates=list(tpe_dates),
        index_open=_or_none(idx["open"]), index_high=_or_none(idx["high"]), index_low=_or_none(idx["low"]),
        index_close=_or_none(idx["close"]),
        amount=(_or_none(idx["amount"]) if (amount_source == "index_trading_money" and any(not np.isnan(a) for a in idx["amount"])) else None),
        margin_balance=_or_none(mg["balance"]), foreign_net_oi=_or_none(oi["net_oi"]), vix=_or_none(vx["vix"]),
        us_dates=us_d or None,
        spx_close=[spx["close"][i] for i in keep] or None, spx_high=[spx["high"][i] for i in keep] or None,
        spx_low=[spx["low"][i] for i in keep] or None,
        sox_close=[sox_by.get(d, float("nan")) for d in us_d] or None,
        fx_dates=fx["dates"] or None, fx_usdtwd=_or_none(fx["usdtwd"]),
    )


def stock_inputs_from_stores(stores: dict[str, Store], market: str, stock_id: str, tpe_date: str, n: int = 320,
                             *, industry: str | None = None, is_financial: bool = False,
                             market_direction_score: dict[str, float | None] | None = None,
                             line2_score_history: dict[str, Sequence[float | None]] | None = None) -> StockInputs:
    """最小實作：價量（原始價）、指數、法人淨買賣、融資、借券；基本面／產業聚合／P_cs 留 None。
    價量與指數必須同日對齊：只取兩者交集日期。"""
    px = load_stock_ohlcv(stores, stock_id, tpe_date, n)
    idx = load_index(stores, market, tpe_date, n)
    idx_by = dict(zip(idx["dates"], idx["close"]))
    keep = [i for i, d in enumerate(px["dates"]) if d in idx_by]
    dates = [px["dates"][i] for i in keep]
    f = load_stock_inst_net(stores, stock_id, tpe_date, n, INST_FOREIGN_NAMES)
    t = load_stock_inst_net(stores, stock_id, tpe_date, n, INST_TRUST_NAMES)
    mg = load_stock_margin(stores, stock_id, tpe_date, n)
    ss = load_stock_short_sale(stores, stock_id, tpe_date, n)
    f_by, t_by, m_by, s_by = dict(zip(f["dates"], f["net"])), dict(zip(t["dates"], t["net"])), dict(zip(mg["dates"], mg["balance"])), dict(zip(ss["dates"], ss["balance"]))

    def aligned(by: dict, fill: float) -> list | None:
        if not by:
            return None
        return [by.get(d, fill) for d in dates]

    # 法人（長格式）無列＝當日淨額 0（FinMind 該日無買賣即不出列，屬**假設**，Hetzner 首次 run 對照 T86 確認）；
    # 融資／借券餘額無列＝**缺值 NaN**（餘額補 0 會製造假的 −100% 變化率或分母為零；引擎對視窗內 NaN 回 Missing）。
    nan = float("nan")
    return StockInputs(
        market=market, stock_id=stock_id, tpe_date=tpe_date, industry=industry, is_financial=is_financial,
        open=[px["open"][i] for i in keep] or None, high=[px["high"][i] for i in keep] or None,
        low=[px["low"][i] for i in keep] or None, close=[px["close"][i] for i in keep] or None,
        volume=[px["volume"][i] for i in keep] or None, index_close=[idx_by[d] for d in dates] or None,
        foreign_net_shares=aligned(f_by, 0.0), trust_net_shares=aligned(t_by, 0.0),
        margin_balance=aligned(m_by, nan), margin_eligible=bool(m_by), short_sale_balance=aligned(s_by, nan),
        market_direction_score=dict(market_direction_score or {}), line2_score_history=dict(line2_score_history or {}),
    )


def open_score_stores(cache_dir: Path) -> dict[str, Store]:
    """開啟計分需要的三個 DB（prices／chips／market）。"""
    return {n: Store(Path(cache_dir) / f"{n}.db") for n in ("prices", "chips", "market")}
