"""重播驅動的 DB 讀取層（第 13 項 13a-1）：原料 SQLite ＋ `features.db` → 每日一個 `DayBundle`。

`replay_state.py` 是純函式層、不碰 DB；本檔是唯一 import sqlite3 的地方（`tests/test_replay_state.py`
以 AST 守）。讀取一律**唯讀**（`feed.open_ro`／`FeatureStore(readonly=True)`）、**逐日點查詢**
（`WHERE data_version=? AND date=?`，走各表的 date 索引；`docs/P2-REPLAY-PLAN.md` §2）。

## 每張表怎麼讀（欄名依 2026-09-09～13 Hetzner 實查；行為與 `score_io` 各 loader 對齊）

| 來源 | 表 | 取法 |
|---|---|---|
| 指數 | `prices.raw_index_price` | `stock_id ∈ {TAIEX, TPEx}` 的 open/max/min/close |
| 個股價量 | `prices.raw_price_daily` | open/max/min/close/Trading_Volume(股)/Trading_money(元)；**原始價**，還原在 `WindowCache.ingest` |
| 法人 | `chips.raw_inst_buysell` | 長格式 name/buy/sell（股）→ 外資＝`INST_FOREIGN_NAMES` 加總、投信＝`INST_TRUST_NAMES`，÷1000 張 |
| 融資餘額 | `chips.raw_margin` | `MarginPurchaseTodayBalance` |
| 借券餘額 | `chips.raw_short_sale_balance` | `SBLShortSalesCurrentDayBalance` |
| 發行股數 | `chips.raw_shareholding` | `NumberOfSharesIssued`（股；裁定 #34 Q3 回補） |
| 官方法人金額 | `market.raw_twse_bfi82u`／`raw_tpex_inst_summary` | 當日 `body` JSON → `official_parse.parse_*` → `foreign_net_k`／`trust_net_k`（千元） |
| 官方成交金額 | `market.raw_twse_fmtqik`／`raw_tpex_trading_index` | **月表**：依 T 所在月取 `body` 解析一次、快取整月 → `amount_k`（千元） |
| 期貨 | `market.raw_futures_daily` | `futures_id='TX'` 且 `trading_session='position'` 的 contract_date→close；近月／基差在 `WindowCache` 算 |
| 期貨外資 OI | `market.raw_futures_inst` | `futures_id='TX'`、`institutional_investors='外資'`，long−short |
| 融資總餘額 | `market.raw_total_margin` | `name='MarginPurchaseMoney'` 的 `TodayBalance` |
| VIX | `market.raw_vix` | 盤中逐筆，取當日 `time` 最大列的 `vix`（同 `score_io.load_vix`） |
| 美股 | `market.raw_us_index` | `^GSPC`（Close/High/Low）＋`^SOX`（Close），**自帶日期**：首日取 ≤T 最後 `window` 個日期，之後只取 `(last, T]` |
| 匯率 | `market.raw_fx_usd` | (spot_buy+spot_sell)/2，自帶日期，同上 |
| 廣度／產業／P_cs | `features.db` | `FeatureStore.day_breadth／day_industry／day_p_cs` |

表不存在→該欄整段缺（記進 `missing_tables`，驅動端印一次）；官方 JSON 解析失敗→該日缺、記 `official_errors`。
單位：官方金額三者皆**千元**（`official_parse` 已轉），引擎端 `ind_amount_ratio`／`ind_net_amount_ratio` 只吃比值，
三者同單位即可（`docs/P2-REPLAY-PLAN.md` §1 #7）。
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from . import feed as F
from . import official_parse as OP
from .features_io import FeatureStore, FeatureStoreError
from .futures import SESSION_REGULAR
from .replay_state import DayBundle, WINDOW_N
from .score_io import INST_FOREIGN_NAMES, INST_TRUST_NAMES, TOTAL_MARGIN_NAME, VIX_COLUMN, VIX_TIME_COLUMN
from .score.params import MARKETS

INDEX_ID = F.INDEX_ID
US_SPX, US_SOX = "^GSPC", "^SOX"
TX = "TX"
FOREIGN_LABEL = "外資"


class ReplayIOError(RuntimeError):
    pass


def _q(conn: sqlite3.Connection, sql: str, params: tuple = ()) -> list[tuple]:
    return conn.execute(sql, params).fetchall()


class ReplaySource:
    """開一次、逐日 `read_day(T)`。`data_version` 對所有原料表一律相同（與 `scan_features` 的 `resolve_dv` 同）。"""

    def __init__(self, cache_dir: Path, data_version: str | None = None, *, features_path: Path | None = None,
                 window: int = WINDOW_N) -> None:
        self.cache = Path(cache_dir)
        self.window = int(window)
        self.prices = F.open_ro(self.cache / "prices.db")
        self.universe = F.open_ro(self.cache / "universe.db")
        self.chips = F.open_ro(self.cache / "chips.db")
        self.market = F.open_ro(self.cache / "market.db")
        self.dv = F.resolve_dv(self.prices, F.PRICE_TABLE, data_version)
        fp = Path(features_path) if features_path else self.cache / "features.db"
        try:
            self.features = FeatureStore(fp, readonly=True)
        except FeatureStoreError as e:
            self._close_raw()
            raise ReplayIOError(str(e)) from e
        self.pool = F.load_pool(self.universe)
        self.factors, self.factor_stats = F.load_factors(self.prices, F.resolve_dv(self.prices, F.DIV_TABLE, data_version))
        self.missing_tables: set[str] = set()
        self.official_errors: list[tuple[str, str, str]] = []      # (date_or_month, table, reason)
        self._fmtqik: dict[str, dict[str, dict]] = {}
        self._tpex_ti: dict[str, dict[str, int]] = {}
        self._last_us: str | None = None
        self._last_fx: str | None = None
        self._cols: dict[tuple[int, str], set[str]] = {}

    # -- 生命週期 --
    def close(self) -> None:
        self._close_raw()
        try:
            self.features.close()
        except Exception:
            pass

    def _close_raw(self) -> None:
        for c in (self.prices, self.universe, self.chips, self.market):
            try:
                c.close()
            except Exception:
                pass

    def __enter__(self) -> "ReplaySource":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # -- 工具 --
    def _have(self, conn: sqlite3.Connection, table: str, need: set[str]) -> bool:
        key = (id(conn), table)
        if key not in self._cols:
            self._cols[key] = F.columns(conn, table)
        cols = self._cols[key]
        if not cols or not need <= cols:
            self.missing_tables.add(table)
            return False
        return True

    def trading_dates(self, start: str | None = None, end: str | None = None) -> list[str]:
        """原料的交易日軸＝`raw_price_daily` 的不重複日期（與 `scan_features` 同）。"""
        w = ["data_version=?", "date IS NOT NULL"]
        p: list[Any] = [self.dv]
        if start:
            w.append("date >= ?")
            p.append(start)
        if end:
            w.append("date <= ?")
            p.append(end)
        return [r[0] for r in _q(self.prices, f'SELECT DISTINCT date FROM "{F.PRICE_TABLE}" WHERE {" AND ".join(w)} ORDER BY date', tuple(p))]

    # -- 逐日 --
    def read_day(self, T: str) -> DayBundle:
        b = DayBundle(tpe_date=T)
        b.index = self._index(T)
        b.stocks = self._stocks(T)
        b.official = {m: self._official(m, T) for m in MARKETS}
        for m in MARKETS:
            b.breadth[m] = self.features.day_breadth(self.dv, m, T)
            b.industry[m] = self.features.day_industry(self.dv, m, T)
            b.p_cs[m] = self.features.day_p_cs(self.dv, m, T)
        b.futures = self._futures(T)
        b.foreign_net_oi = self._futures_oi(T)
        b.total_margin = self._total_margin(T)
        b.vix = self._vix(T)
        b.us = self._us(T)
        b.fx = self._fx(T)
        return b

    def _index(self, T: str) -> dict[str, dict[str, float]]:
        out: dict[str, dict[str, float]] = {}
        if not self._have(self.prices, F.INDEX_TABLE, {"date", "stock_id", "close"}):
            return out
        cols = self._cols[(id(self.prices), F.INDEX_TABLE)]
        sel = ["stock_id", "close"] + [c for c in ("open", "max", "min") if c in cols]
        for row in _q(self.prices, f'SELECT {", ".join(sel)} FROM "{F.INDEX_TABLE}" WHERE data_version=? AND date=?', (self.dv, T)):
            r = dict(zip(sel, row))
            for m, sid in INDEX_ID.items():
                if r["stock_id"] == sid:
                    out[m] = {"open": r.get("open"), "high": r.get("max"), "low": r.get("min"), "close": r["close"]}
        return out

    def _stocks(self, T: str) -> dict[str, dict[str, Any]]:
        out: dict[str, dict[str, Any]] = {}
        need = {"date", "stock_id", "close", "Trading_Volume", "Trading_money"}
        if not self._have(self.prices, F.PRICE_TABLE, need):
            return out
        cols = self._cols[(id(self.prices), F.PRICE_TABLE)]
        sel = ["stock_id", "close", "Trading_Volume", "Trading_money"] + [c for c in ("open", "max", "min") if c in cols]
        for row in _q(self.prices, f'SELECT {", ".join(sel)} FROM "{F.PRICE_TABLE}" WHERE data_version=? AND date=?', (self.dv, T)):
            r = dict(zip(sel, row))
            sid = str(r["stock_id"])
            if sid not in self.pool:
                continue
            out[sid] = {"open": r.get("open"), "high": r.get("max"), "low": r.get("min"), "close": r["close"],
                        "Trading_Volume": r["Trading_Volume"], "volume": r["Trading_Volume"], "amount": r["Trading_money"],
                        "foreign_net": None, "trust_net": None, "margin_balance": None, "short_sale_balance": None,
                        "shares_outstanding": None}
        if not out:
            return out
        if self._have(self.chips, "raw_inst_buysell", {"date", "stock_id", "name", "buy", "sell"}):
            names = INST_FOREIGN_NAMES + INST_TRUST_NAMES
            ph = ", ".join("?" for _ in names)
            for sid, name, buy, sell in _q(self.chips, f'SELECT stock_id, name, "buy", "sell" FROM raw_inst_buysell '
                                            f'WHERE data_version=? AND date=? AND name IN ({ph})', (self.dv, T, *names)):
                r = out.get(str(sid))
                if r is None:
                    continue
                key = "foreign_net" if name in INST_FOREIGN_NAMES else "trust_net"
                net = F_num(buy) - F_num(sell)                     # 先以「股」加總、最後才 ÷1000（與 score_io 同一個浮點運算序）
                r[key] = net if r[key] is None else r[key] + net
            for r in out.values():
                for key in ("foreign_net", "trust_net"):
                    if r[key] is not None:
                        r[key] = r[key] / 1000.0
        if self._have(self.chips, "raw_margin", {"date", "stock_id", "MarginPurchaseTodayBalance"}):
            for sid, v in _q(self.chips, 'SELECT stock_id, "MarginPurchaseTodayBalance" FROM raw_margin WHERE data_version=? AND date=?', (self.dv, T)):
                r = out.get(str(sid))
                if r is not None:
                    r["margin_balance"] = F_num(v)
        if self._have(self.chips, "raw_short_sale_balance", {"date", "stock_id", "SBLShortSalesCurrentDayBalance"}):
            for sid, v in _q(self.chips, 'SELECT stock_id, "SBLShortSalesCurrentDayBalance" FROM raw_short_sale_balance WHERE data_version=? AND date=?', (self.dv, T)):
                r = out.get(str(sid))
                if r is not None:
                    r["short_sale_balance"] = F_num(v)
        if self._have(self.chips, "raw_shareholding", {"date", "stock_id", "NumberOfSharesIssued"}):
            for sid, v in _q(self.chips, 'SELECT stock_id, "NumberOfSharesIssued" FROM raw_shareholding WHERE data_version=? AND date=?', (self.dv, T)):
                r = out.get(str(sid))
                if r is not None and v is not None:
                    r["shares_outstanding"] = F_num(v)
        return out

    def _body(self, table: str, where: str, params: tuple) -> Any | None:
        rows = _q(self.market, f'SELECT body FROM "{table}" WHERE data_version=? AND {where}', (self.dv, *params))
        if not rows:
            return None
        try:
            return json.loads(rows[-1][0])
        except (TypeError, ValueError) as e:
            self.official_errors.append((str(params[0]), table, f"body 非 JSON：{e}"))
            return None

    def _official(self, market: str, T: str) -> dict[str, float | None]:
        out: dict[str, float | None] = {"amount_k": None, "foreign_net_k": None, "trust_net_k": None}
        inst_table, parse_inst = ("raw_twse_bfi82u", OP.parse_bfi82u) if market == "twse" else ("raw_tpex_inst_summary", OP.parse_tpex_inst_summary)
        if self._have(self.market, inst_table, {"date", "body"}):
            body = self._body(inst_table, "date=?", (T,))
            if body is not None:
                try:
                    d = parse_inst(body)
                    out["foreign_net_k"] = float(d["f_net_k"])      # official_parse 的 tag：f／t／d
                    out["trust_net_k"] = float(d["t_net_k"])
                except (OP.OfficialParseError, KeyError, TypeError, ValueError) as e:
                    self.official_errors.append((T, inst_table, str(e)))
        month = T[:4] + T[5:7]
        if market == "twse":
            if self._have(self.market, "raw_twse_fmtqik", {"month", "body"}):
                if month not in self._fmtqik:
                    body = self._body("raw_twse_fmtqik", "month=?", (month,))
                    parsed: dict = {}
                    if body is not None:
                        try:
                            parsed = OP.parse_fmtqik_month(body)
                        except OP.OfficialParseError as e:
                            self.official_errors.append((month, "raw_twse_fmtqik", str(e)))
                    self._fmtqik[month] = parsed
                rec = self._fmtqik[month].get(T)
                if rec and rec.get("turnover_k") is not None:
                    out["amount_k"] = float(rec["turnover_k"])
        else:
            if self._have(self.market, "raw_tpex_trading_index", {"month", "body"}):
                if month not in self._tpex_ti:
                    body = self._body("raw_tpex_trading_index", "month=?", (month,))
                    parsed2: dict = {}
                    if body is not None:
                        try:
                            parsed2 = OP.parse_tpex_trading_index_month(body)
                        except OP.OfficialParseError as e:
                            self.official_errors.append((month, "raw_tpex_trading_index", str(e)))
                    self._tpex_ti[month] = parsed2
                v = self._tpex_ti[month].get(T)
                if v is not None:
                    out["amount_k"] = float(v)
        return out

    def _futures(self, T: str) -> dict[str, Any]:
        if not self._have(self.market, "raw_futures_daily", {"date", "futures_id", "contract_date", "close"}):
            return {}
        cols = self._cols[(id(self.market), "raw_futures_daily")]
        w = "data_version=? AND date=? AND futures_id=?"
        p: tuple = (self.dv, T, TX)
        if "trading_session" in cols:
            w += " AND trading_session=?"
            p = (*p, SESSION_REGULAR)
        close: dict[str, float] = {}
        for c, v in _q(self.market, f'SELECT contract_date, "close" FROM raw_futures_daily WHERE {w}', p):
            if c is None or v is None:
                continue
            close[str(c)] = F_num(v)
        return {"contracts": sorted(close), "close": close} if close else {}

    def _futures_oi(self, T: str) -> float | None:
        need = {"date", "futures_id", "institutional_investors", "long_open_interest_balance_volume", "short_open_interest_balance_volume"}
        if not self._have(self.market, "raw_futures_inst", need):
            return None
        rows = _q(self.market, 'SELECT long_open_interest_balance_volume, short_open_interest_balance_volume FROM raw_futures_inst '
                               'WHERE data_version=? AND date=? AND futures_id=? AND institutional_investors=?', (self.dv, T, TX, FOREIGN_LABEL))
        if not rows:
            return None
        lo, sh = rows[-1]
        return F_num(lo) - F_num(sh)

    def _total_margin(self, T: str) -> float | None:
        if not self._have(self.market, "raw_total_margin", {"date", "name", "TodayBalance"}):
            return None
        rows = _q(self.market, 'SELECT "TodayBalance" FROM raw_total_margin WHERE data_version=? AND date=? AND name=?', (self.dv, T, TOTAL_MARGIN_NAME))
        return F_num(rows[-1][0]) if rows else None

    def _vix(self, T: str) -> float | None:
        if not self._have(self.market, "raw_vix", {"date", VIX_COLUMN}):
            return None
        cols = self._cols[(id(self.market), "raw_vix")]
        if VIX_TIME_COLUMN in cols:
            rows = _q(self.market, f'SELECT "{VIX_COLUMN}" FROM raw_vix WHERE data_version=? AND date=? ORDER BY "{VIX_TIME_COLUMN}" DESC LIMIT 1', (self.dv, T))
        else:
            rows = _q(self.market, f'SELECT "{VIX_COLUMN}" FROM raw_vix WHERE data_version=? AND date=?', (self.dv, T))
        return F_num(rows[-1][0]) if rows else None

    def _dated(self, table: str, sql_cols: str, extra_where: str, params: tuple, last: str | None, T: str) -> list[tuple]:
        """自帶日期序列的增量讀取：首次取 ≤T 最後 `window` 個日期，之後取 `(last, T]`。回升冪列。"""
        if last is None:
            rows = _q(self.market, f'SELECT {sql_cols} FROM "{table}" WHERE data_version=? AND date<=? {extra_where} '
                                   f'ORDER BY date DESC LIMIT ?', (self.dv, T, *params, self.window * 3))
            rows.reverse()
        else:
            rows = _q(self.market, f'SELECT {sql_cols} FROM "{table}" WHERE data_version=? AND date>? AND date<=? {extra_where} ORDER BY date',
                      (self.dv, last, T, *params))
        return rows

    def _us(self, T: str) -> list[tuple[str, float, float, float, float]]:
        if not self._have(self.market, "raw_us_index", {"date", "stock_id", "Close", "High", "Low"}):
            return []
        rows = self._dated("raw_us_index", 'date, stock_id, "Close", "High", "Low"', "AND stock_id IN (?, ?)", (US_SPX, US_SOX), self._last_us, T)
        by: dict[str, dict[str, tuple]] = {}
        for d, sid, c, h, lo in rows:
            by.setdefault(str(d), {})[str(sid)] = (c, h, lo)
        out: list[tuple[str, float, float, float, float]] = []
        for d in sorted(by):
            spx = by[d].get(US_SPX)
            if spx is None:
                continue                                   # 與 score_io 同：us_dates 以 ^GSPC 為軸，SOX 缺補 NaN
            sox = by[d].get(US_SOX)
            out.append((d, F_num(spx[0]), F_num(spx[1]), F_num(spx[2]), F_num(sox[0]) if sox else float("nan")))
        if self._last_us is None:
            out = out[-self.window:]
        if out:
            self._last_us = out[-1][0]
        elif self._last_us is None:
            self._last_us = T
        else:
            self._last_us = max(self._last_us, T)
        return out

    def _fx(self, T: str) -> list[tuple[str, float]]:
        if not self._have(self.market, "raw_fx_usd", {"date", "spot_buy", "spot_sell"}):
            return []
        rows = self._dated("raw_fx_usd", "date, spot_buy, spot_sell", "", (), self._last_fx, T)
        by: dict[str, float] = {}
        for d, b, s in rows:
            by[str(d)] = (F_num(b) + F_num(s)) / 2.0
        out = [(d, by[d]) for d in sorted(by)]
        if self._last_fx is None:
            out = out[-self.window:]
        self._last_fx = out[-1][0] if out else (T if self._last_fx is None else max(self._last_fx, T))
        return out


def F_num(v: Any) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return float("nan")
