"""重播驅動的 DB 讀取層（第 13 項 13a-1）：原料 SQLite ＋ `features.db` → 每日一個 `DayBundle`。

`replay_state.py` 是純函式層、不碰 DB；本檔是唯一 import sqlite3 的地方（`tests/test_replay_state.py`
以 AST 守）。讀取一律**唯讀**（`feed.open_ro`／`FeatureStore(readonly=True)`）、**逐日點查詢**
（`WHERE data_version=? AND date=?`，走各表的 date 索引；`docs/P2-REPLAY-PLAN.md` §2）。

## 每張表怎麼讀（欄名依 2026-09-09～13 Hetzner 實查；行為與 `score_io` 各 loader 對齊）

**列→欄的語意自 2026-09-14（每日班 D-1）起只定義在 `collect.py`**：本檔各 `_xxx()` 只跑 SQL 選欄、把 dict 列交給 `collect.*`，
每日班以 API 回應列走同一組函式。下表是 SQL 端「取哪些列」的說明，欄值怎麼變成 `DayBundle` 看 `collect`。

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

from . import collect as C
from . import feed as F
from . import official_parse as OP
from .features_io import FeatureStore, FeatureStoreError
from .fundamentals import NEEDED_TYPES, FundamentalsBridge, build_stock, extend_calendar
from .replay_state import DayBundle, WINDOW_N
from .score_io import INST_FOREIGN_NAMES, INST_TRUST_NAMES, TOTAL_MARGIN_NAME, VIX_COLUMN, VIX_TIME_COLUMN
from .score.params import MARKETS

INDEX_ID = C.INDEX_ID
US_SPX, US_SOX = C.US_SPX, C.US_SOX
TX = C.TX
FOREIGN_LABEL = C.FOREIGN_LABEL


class ReplayIOError(RuntimeError):
    pass


def _q(conn: sqlite3.Connection, sql: str, params: tuple = ()) -> list[tuple]:
    return conn.execute(sql, params).fetchall()


def _qd(conn: sqlite3.Connection, cols: list[str], table: str, where: str, params: tuple) -> list[dict[str, Any]]:
    """點查詢回 **dict 列**（鍵＝欄名），餵給 `collect.*` 純函式——列→`DayBundle` 的語意只在 `collect` 定義一次。"""
    sel = ", ".join(f'"{c}"' for c in cols)
    return [dict(zip(cols, row)) for row in conn.execute(f'SELECT {sel} FROM "{table}" WHERE {where}', params).fetchall()]


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
        self.fundamentals_db = F.open_ro(self.cache / "fundamentals.db") if (self.cache / "fundamentals.db").exists() else None
        self.dv = F.resolve_dv(self.prices, F.PRICE_TABLE, data_version)
        fp = Path(features_path) if features_path else self.cache / "features.db"
        try:
            self.features = FeatureStore(fp, readonly=True)
        except FeatureStoreError as e:
            self._close_raw()
            raise ReplayIOError(str(e)) from e
        self.pool = F.load_pool(self.universe)
        self.factors, self.factor_stats, self.factor_source_stats = F.load_factors_full(
            self.prices, F.resolve_dv(self.prices, F.DIV_TABLE, data_version))   # 四源同一 dv（以除權息表解析）；合併統計供 log
        self.missing_tables: set[str] = set()
        self.official_errors: list[tuple[str, str, str]] = []      # (date_or_month, table, reason)
        self._month_amounts: dict[tuple[str, str], dict[str, float]] = {}   # (market, YYYYMM) → {日期: 成交金額千元}
        self._last_us: str | None = None
        self._last_fx: str | None = None
        self._cols: dict[tuple[int, str], set[str]] = {}
        self.check_date_indexes()

    # -- 索引守門 --
    DATE_INDEXED_TABLES = (("prices", F.INDEX_TABLE), ("prices", F.PRICE_TABLE), ("chips", "raw_inst_buysell"),
                           ("chips", "raw_margin"), ("chips", "raw_short_sale_balance"), ("chips", "raw_shareholding"),
                           ("market", "raw_twse_bfi82u"), ("market", "raw_tpex_inst_summary"), ("market", "raw_futures_daily"),
                           ("market", "raw_futures_inst"), ("market", "raw_total_margin"), ("market", "raw_vix"),
                           ("market", "raw_us_index"), ("market", "raw_fx_usd"))

    def check_date_indexes(self) -> None:
        """設計正本 §2：逐日點查詢靠各表的 `date` 索引，**缺即拒跑**（回補後忘了 `reindex` 會讓 1,618 日 × 15 表
        全變成全表掃描，慢得像當機但不報錯）。只檢查存在的表；第一欄為 `date` 的索引才算數。"""
        conns = {"prices": self.prices, "chips": self.chips, "market": self.market}
        missing: list[str] = []
        for db, table in self.DATE_INDEXED_TABLES:
            conn = conns[db]
            if not F.columns(conn, table):
                continue
            ok = False
            for row in conn.execute(f'PRAGMA index_list("{table}")'):
                name = row[1]
                cols = [c[2] for c in conn.execute(f'PRAGMA index_info("{name}")')]
                if cols and cols[0] == "date":
                    ok = True
                    break
            if not ok:
                missing.append(f"{db}.db:{table}")
        if missing:
            self._close_raw()
            try:
                self.features.close()
            except Exception:
                pass
            raise ReplayIOError("下列表沒有以 date 為首欄的索引，逐日點查詢會退化成全表掃描：" + "、".join(missing)
                                + "\n  請先跑 `python3 scripts/backfill_hetzner.py reindex`")

    # -- 生命週期 --
    def close(self) -> None:
        self._close_raw()
        try:
            self.features.close()
        except Exception:
            pass

    def _close_raw(self) -> None:
        for c in (self.prices, self.universe, self.chips, self.market, self.fundamentals_db):
            if c is None:
                continue
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

    # -- 基本面橋（13b）--
    def load_fundamentals(self, tpe_dates: list[str] | None = None) -> FundamentalsBridge:
        """一次讀進池內全體的月營收與季報（只取 `fundamentals.NEEDED_TYPES`），建 `FundamentalsBridge`。
        `fundamentals.db` 不存在或兩表缺 → 空橋（全部缺值，`missing_tables` 記下）。
        `price_at_period_end`＝每個期別末日（或其前最近交易日）的**原始**收盤，逐期一次查詢。"""
        cal = extend_calendar(tpe_dates or self.trading_dates())
        stocks: dict = {}
        industry_of = {sid: info.get("industry_category") for sid, info in self.pool.items()}
        if self.fundamentals_db is None:
            self.missing_tables.update({"raw_month_revenue", "raw_financial_statements"})
            return FundamentalsBridge(stocks, industry_of)
        fdb = self.fundamentals_db
        monthly: dict[str, list[tuple[int, int, float]]] = {}
        if self._have(fdb, "raw_month_revenue", {"stock_id", "revenue_year", "revenue_month", "revenue"}):
            for sid, y, m, v in _q(fdb, 'SELECT stock_id, revenue_year, revenue_month, revenue FROM raw_month_revenue '
                                        'WHERE data_version=? ORDER BY stock_id, revenue_year, revenue_month', (self.dv,)):
                if str(sid) in self.pool and v is not None:
                    monthly.setdefault(str(sid), []).append((int(y), int(m), F_num(v)))
        quarters: dict[str, list[tuple[str, str, float]]] = {}
        periods: set[str] = set()
        if self._have(fdb, "raw_financial_statements", {"stock_id", "date", "type", "value"}):
            ph = ",".join("?" for _ in NEEDED_TYPES)
            for sid, p, t, v in _q(fdb, f'SELECT stock_id, date, type, value FROM raw_financial_statements '
                                        f'WHERE data_version=? AND type IN ({ph}) ORDER BY stock_id, date', (self.dv, *NEEDED_TYPES)):
                if str(sid) in self.pool and v is not None and p:
                    quarters.setdefault(str(sid), []).append((str(p), str(t), F_num(v)))
                    periods.add(str(p))
        # 期末原始收盤：每個期別一次查詢（期末日 ≤ P 的最近交易日）
        px: dict[str, dict[str, float]] = {}
        if periods and self._have(self.prices, F.PRICE_TABLE, {"date", "stock_id", "close"}):
            for p in sorted(periods):
                d = _q(self.prices, f'SELECT MAX(date) FROM "{F.PRICE_TABLE}" WHERE data_version=? AND date<=?', (self.dv, p))[0][0]
                if d is None:
                    continue
                for sid, c in _q(self.prices, f'SELECT stock_id, close FROM "{F.PRICE_TABLE}" WHERE data_version=? AND date=?', (self.dv, d)):
                    if c is not None and float(c) > 0:
                        px.setdefault(str(sid), {})[p] = float(c)
        for sid in sorted(set(monthly) | set(quarters)):
            stocks[sid] = build_stock(sid, industry_of.get(sid), monthly.get(sid, []), quarters.get(sid, []), px.get(sid, {}), cal)
        return FundamentalsBridge(stocks, industry_of)

    # -- 視窗重建起點 --
    def rebuild_start(self, before: str, window: int | None = None) -> str | None:
        """從 `before` 之前的原料倒推「要從哪一天開始 ingest，才能讓 `WindowCache` 與全量跑到 `before` 前一日時**逐位相同**」。

        視窗語意是「每檔最近 `window` 個**有成交**列」、「每條自帶日期序列最近 `window` 個日期」，不是最近 `window` 個
        日曆交易日——停牌過的檔用日曆倒推會少列（2026-09-13 驅動測試抓到：1102 停牌兩日，`--resume` 續跑第一日
        1102 的分數就與全量跑不同）。所以逐表算「第 `window` 個最近日期」（不足 `window` 列的取最早一列），取全體最早者。
        Ring 有上限，多 ingest 只會被擠掉、不會改變結果；少 ingest 才會錯，因此一律取**最早**。
        個股的「有成交」用 `close>0 AND Trading_Volume>0`（同 `universe.is_traded_row`），**且與所屬市場指數有列的日子取交集**
        ——`WindowCache` 只在交集日 push，交集的第 w 個日期比各自的第 w 個更早；13a-3 驗收實測缺一天指數列時，
        不取交集會讓 1102 少一列（29 vs 30）。只算池內個股（非池 id 的長停牌會把起點拉到數年前、白 ingest）。
        回 None ＝ 什麼資料都沒有。"""
        w = int(window or self.window)
        cands: list[str] = []

        def nth(conn: sqlite3.Connection, table: str, extra: str = "", params: tuple = ()) -> None:
            if not F.columns(conn, table):
                return
            sql = (f'SELECT date FROM (SELECT DISTINCT date FROM "{table}" WHERE data_version=? AND date<? {extra}) '
                   f'ORDER BY date DESC LIMIT 1 OFFSET ?')
            row = _q(conn, sql, (self.dv, before, *params, w - 1))
            if row:
                cands.append(row[0][0])
                return
            row = _q(conn, f'SELECT MIN(date) FROM "{table}" WHERE data_version=? AND date<? {extra}', (self.dv, before, *params))
            if row and row[0][0] is not None:
                cands.append(row[0][0])

        # 個股：池內每檔「有成交 ∩ 所屬市場有指數列」的第 w 個最近日（不足者取最早），全體最早。逐檔查（走 stock_id 索引）
        if self._have(self.prices, F.PRICE_TABLE, {"date", "stock_id", "close", "Trading_Volume"}) and \
                self._have(self.prices, F.INDEX_TABLE, {"date", "stock_id"}):
            sql = (f'SELECT date FROM "{F.PRICE_TABLE}" WHERE data_version=? AND stock_id=? AND date<? AND close>0 AND "Trading_Volume">0 '
                   f'AND date IN (SELECT date FROM "{F.INDEX_TABLE}" WHERE data_version=? AND stock_id=?) '
                   f'ORDER BY date DESC LIMIT 1 OFFSET ?')
            sql_min = (f'SELECT MIN(date) FROM "{F.PRICE_TABLE}" WHERE data_version=? AND stock_id=? AND date<? AND close>0 AND "Trading_Volume">0 '
                       f'AND date IN (SELECT date FROM "{F.INDEX_TABLE}" WHERE data_version=? AND stock_id=?)')
            for sid in self.pool:
                mk = self.pool.listed(sid, before)                     # PIT：`before` 當日不在池（興櫃期）的檔沒有 ring 要重建
                if mk is None:
                    continue
                idx_id = INDEX_ID[mk]
                row = _q(self.prices, sql, (self.dv, sid, before, self.dv, idx_id, w - 1))
                if row:
                    cands.append(row[0][0])
                    continue
                row = _q(self.prices, sql_min, (self.dv, sid, before, self.dv, idx_id))
                if row and row[0][0] is not None:
                    cands.append(row[0][0])
        for sid in INDEX_ID.values():
            nth(self.prices, F.INDEX_TABLE, "AND stock_id=?", (sid,))
        nth(self.market, "raw_total_margin", "AND name=?", (TOTAL_MARGIN_NAME,))
        nth(self.market, "raw_vix")
        nth(self.market, "raw_futures_inst", "AND futures_id=? AND institutional_investors=?", (TX, FOREIGN_LABEL))
        nth(self.market, "raw_futures_daily", "AND futures_id=?", (TX,))
        nth(self.market, "raw_fx_usd")
        nth(self.market, "raw_us_index", "AND stock_id=?", (US_SPX,))
        return min(cands) if cands else None

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

    # -- 逐表 SQL → dict 列 → collect.* ----------------------------------------------------------------
    def _index(self, T: str) -> dict[str, dict[str, float | None]]:
        if not self._have(self.prices, F.INDEX_TABLE, {"date", "stock_id", "close"}):
            return {}
        cols = self._cols[(id(self.prices), F.INDEX_TABLE)]
        sel = ["stock_id", "close"] + [c for c in ("open", "max", "min") if c in cols]
        return C.index_from_rows(_qd(self.prices, sel, F.INDEX_TABLE, "data_version=? AND date=?", (self.dv, T)))

    def _stocks(self, T: str) -> dict[str, dict[str, Any]]:
        need = {"date", "stock_id", "close", "Trading_Volume", "Trading_money"}
        if not self._have(self.prices, F.PRICE_TABLE, need):
            return {}
        cols = self._cols[(id(self.prices), F.PRICE_TABLE)]
        sel = ["stock_id", "close", "Trading_Volume", "Trading_money"] + [c for c in ("open", "max", "min") if c in cols]
        price_rows = _qd(self.prices, sel, F.PRICE_TABLE, "data_version=? AND date=?", (self.dv, T))
        if not any(str(r["stock_id"]) in self.pool for r in price_rows):
            return {}                                                  # 池內無價量列：籌碼表免查（與舊版同）
        inst_rows: list[dict] = []
        if self._have(self.chips, "raw_inst_buysell", {"date", "stock_id", "name", "buy", "sell"}):
            names = INST_FOREIGN_NAMES + INST_TRUST_NAMES
            ph = ", ".join("?" for _ in names)
            inst_rows = _qd(self.chips, ["stock_id", "name", "buy", "sell"], "raw_inst_buysell",
                            f"data_version=? AND date=? AND name IN ({ph})", (self.dv, T, *names))
        chip = {}
        for key, table, col in (("margin_rows", "raw_margin", "MarginPurchaseTodayBalance"),
                                ("short_rows", "raw_short_sale_balance", "SBLShortSalesCurrentDayBalance"),
                                ("shareholding_rows", "raw_shareholding", "NumberOfSharesIssued")):
            chip[key] = (_qd(self.chips, ["stock_id", col], table, "data_version=? AND date=?", (self.dv, T))
                         if self._have(self.chips, table, {"date", "stock_id", col}) else [])
        return C.stocks_from_rows(price_rows, self.pool, inst_rows=inst_rows, **chip)

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
        inst_table, month_table = C.OFFICIAL_INST_TABLE[market], C.OFFICIAL_MONTH_TABLE[market]
        inst_body = self._body(inst_table, "date=?", (T,)) if self._have(self.market, inst_table, {"date", "body"}) else None
        month = T[:4] + T[5:7]
        month_amounts: dict[str, float] | None = None
        if self._have(self.market, month_table, {"month", "body"}):
            key = (market, month)
            if key not in self._month_amounts:                       # 月表解析一次、快取整月
                body = self._body(month_table, "month=?", (month,))
                parsed: dict[str, float] = {}
                if body is not None:
                    try:
                        parsed = C.parse_month_body(market, body)
                    except OP.OfficialParseError as e:
                        self.official_errors.append((month, month_table, str(e)))
                self._month_amounts[key] = parsed
            month_amounts = self._month_amounts[key]
        out, errs = C.official_day(market, T, inst_body, month_amounts)
        self.official_errors.extend(errs)
        return out

    def _futures(self, T: str) -> dict[str, Any]:
        if not self._have(self.market, "raw_futures_daily", {"date", "futures_id", "contract_date", "close"}):
            return {}
        cols = self._cols[(id(self.market), "raw_futures_daily")]
        sel = ["futures_id", "contract_date", "close"] + (["trading_session"] if "trading_session" in cols else [])
        return C.futures_from_rows(_qd(self.market, sel, "raw_futures_daily", "data_version=? AND date=? AND futures_id=?", (self.dv, T, TX)))

    def _futures_oi(self, T: str) -> float | None:
        need = {"date", "futures_id", "institutional_investors", "long_open_interest_balance_volume", "short_open_interest_balance_volume"}
        if not self._have(self.market, "raw_futures_inst", need):
            return None
        return C.futures_oi_from_rows(_qd(self.market, ["futures_id", "institutional_investors", "long_open_interest_balance_volume",
                                                        "short_open_interest_balance_volume"], "raw_futures_inst",
                                          "data_version=? AND date=? AND futures_id=? AND institutional_investors=?", (self.dv, T, TX, FOREIGN_LABEL)))

    def _total_margin(self, T: str) -> float | None:
        if not self._have(self.market, "raw_total_margin", {"date", "name", "TodayBalance"}):
            return None
        return C.total_margin_from_rows(_qd(self.market, ["name", "TodayBalance"], "raw_total_margin",
                                            "data_version=? AND date=? AND name=?", (self.dv, T, TOTAL_MARGIN_NAME)))

    def _vix(self, T: str) -> float | None:
        if not self._have(self.market, "raw_vix", {"date", VIX_COLUMN}):
            return None
        cols = self._cols[(id(self.market), "raw_vix")]
        sel = [VIX_COLUMN] + ([VIX_TIME_COLUMN] if VIX_TIME_COLUMN in cols else [])
        return C.vix_from_rows(_qd(self.market, sel, "raw_vix", "data_version=? AND date=?", (self.dv, T)))

    def _dated(self, table: str, cols: list[str], extra_where: str, params: tuple, last: str | None, T: str) -> list[dict[str, Any]]:
        """自帶日期序列的增量讀取：首次取 ≤T 最後 `window` 個日期，之後取 `(last, T]`。回 dict 列（升冪）。"""
        if last is None:
            rows = _qd(self.market, cols, table, f"data_version=? AND date<=? {extra_where} ORDER BY date DESC LIMIT ?",
                       (self.dv, T, *params, self.window * 4))        # 美股每日 2 個 id → 4×window 列保證 ≥ window 個日期
            rows.reverse()
        else:
            rows = _qd(self.market, cols, table, f"data_version=? AND date>? AND date<=? {extra_where} ORDER BY date", (self.dv, last, T, *params))
        return rows

    def _us(self, T: str) -> list[tuple[str, float, float, float, float]]:
        if not self._have(self.market, "raw_us_index", {"date", "stock_id", "Close", "High", "Low"}):
            return []
        out = C.us_from_rows(self._dated("raw_us_index", ["date", "stock_id", "Close", "High", "Low"], "AND stock_id IN (?, ?)",
                                         (US_SPX, US_SOX), self._last_us, T))
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
        out = C.fx_from_rows(self._dated("raw_fx_usd", ["date", "spot_buy", "spot_sell"], "", (), self._last_fx, T))
        if self._last_fx is None:
            out = out[-self.window:]
        self._last_fx = out[-1][0] if out else (T if self._last_fx is None else max(self._last_fx, T))
        return out


F_num = C.num
