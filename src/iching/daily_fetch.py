"""每日班**抓取層**（D-2b）：當日 API（FinMind＋官方端點）→ dict 列 → `collect.*` → `DayBundle`。**只做呼叫與組裝，不碰檔案**。

設計正本 `docs/P2-DAILY-PLAN.md` §7.4.1。資料集名一律取自 `config.DATASETS`（與回補層同一份登錄表），
官方參數建構器 `twse.OFFICIAL_PARAMS`（與 `backfill_hetzner.py` 同一份）。

`fm` 只需有 `get(dataset, **params) -> list[dict]`（`fm.FinMind`），`oc` 只需有 `get(url, params) -> (status, body, text)`
（`twse.OfficialClient`）——測試以合成 SQLite 當假端點。

**美股／匯率是增量**（`last_us`／`last_fx` 之後到 T），22:30 抓不到美股 T 日收盤屬正常（引擎上爻只用 ≤T−1，§7.4.0 第 1 點）。
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping, Sequence

from . import collect as C
from . import config as CFG
from . import official_parse as OP
from . import twse as T
from .fundamentals import NEEDED_TYPES
from .replay_state import DayBundle
from .score.params import MARKETS

SPEC = {s.key: s for s in CFG.DATASETS}
Row = Mapping[str, Any]
REVENUE_MONTHS_BACK = 2             # 月營收：「上一公布月**整月**窗」＋「本公布月**部分**窗 [月首, T]」（各 1 次；見 month_windows）
STATEMENT_QUARTERS_BACK = 2         # 季報：查最近兩個**期末日**（各 1 次，start=end=期末日）
# 2026-09-14 Hetzner 實測（同一支 client）：全市場不帶 data_id 的查詢**視窗起點必須是期別邊界**——
#   TaiwanStockMonthRevenue 08-01～08-31 → 2,339 列；07-18～09-01 → 0 列。
#   TaiwanStockFinancialStatements 06-30～06-30 → 38,691 列；05-04～09-01 → 0 列。
#   帶 data_id 的跨月／跨季區間則正常。回補層本來就用「月首～月末」「季首～季末」，所以抓得到；每日班首版用 45／120 曆日窗
#   → 六天全 0（§7.4.4 run #3／#4）。
# 2026-09-15 Hetzner 實測（回補層 `--data-end 2026-09-14`）：起點為月首的**部分**窗 `2026-09-01～2026-09-14` 回列（range_slice ok）
#   ——本月窗自 2026-09-15 起改用 `[月首, T]`（與回補層本月部分塊同一形狀，且 end_date 不再落在未來）。
DIVIDEND_LOOKBACK_DAYS = 7          # 除息列回看（keep-first 冪等，晚落地的列 7 日內仍補得到）
CORE_REQUIRED = ("index:twse", "index:tpex", "stocks", "inst", "margin", "shareholding", "short_sale", "total_margin",
                 "futures_daily", "futures_inst", "vix", "official_inst:twse", "official_inst:tpex",
                 "official_amount:twse", "official_amount:tpex")


class DailyFetchError(RuntimeError):
    pass


def ds(key: str) -> str:
    return SPEC[key].dataset


def next_day(iso: str) -> str:
    return (dt.date.fromisoformat(iso) + dt.timedelta(days=1)).isoformat()


def days_before(iso: str, n: int) -> str:
    return (dt.date.fromisoformat(iso) - dt.timedelta(days=n)).isoformat()


def month_windows(iso: str, n: int) -> list[tuple[str, str]]:
    """T 所在月往前 n−1 個**整月**窗＋T 所在月的**部分**窗 `[月首, T]`，升冪 `[(月首, 月末), …, (本月首, T)]`；`n<=0` 回空。

    月營收 `date`＝公布月 1 日（7 月營收 date=08-01，8 月營收 date=09-01）：本月 1～10 日陸續公布的上月營收落在本月部分窗，
    上月整月窗負責追補晚報者。**視窗起點一律月首**——2026-09-14 Hetzner 實測全市場不帶 data_id 的查詢起點不在月首回 0 列
    （`07-18～09-01` → 0）；部分窗 `[月首, T]` 可回列（2026-09-15 Hetzner 實測 `2026-09-01～2026-09-14` range_slice ok），
    與回補層 `--data-end` 的本月部分塊同一形狀。舊版本月窗為整月 `[月首, 月末]`（end_date 在未來；run #5 實測亦回列，
    §7.4.4），改部分窗是把兩側查詢形狀對齊、不是修「抓不到」。"""
    y, m = int(iso[:4]), int(iso[5:7])
    out = []
    for i in range(n):
        first = dt.date(y, m, 1)
        last = (dt.date(y + (m == 12), m % 12 + 1, 1) - dt.timedelta(days=1))
        out.append((first.isoformat(), iso[:10] if i == 0 else last.isoformat()))   # i==0＝T 所在月 → 截到 T
        y, m = (y - 1, 12) if m == 1 else (y, m - 1)
    return out[::-1]


def quarter_ends(iso: str, n: int) -> list[str]:
    """≤T 的最近 n 個季末日（3／6／9／12 月末，升冪）。季報 `date`＝期末日，查 start=end=期末日。"""
    d = dt.date.fromisoformat(iso)
    out = []
    y, q = d.year, (d.month - 1) // 3          # q＝T 所在季（0..3）；先退到上一個已結束的季末
    while len(out) < n:
        q -= 1
        if q < 0:
            y, q = y - 1, 3
        m = 3 * (q + 1)
        last = (dt.date(y + (m == 12), m % 12 + 1, 1) - dt.timedelta(days=1))
        out.append(last.isoformat())
    return out[::-1]


def prev_weekday(iso: str) -> str:
    """T 之前最近的週一～五（曆日）。美股 T−1 列「應已落地」的粗判：只看週末、不看美國假日。"""
    d = dt.date.fromisoformat(iso) - dt.timedelta(days=1)
    while d.weekday() >= 5:
        d -= dt.timedelta(days=1)
    return d.isoformat()


@dataclass
class DayFetch:
    bundle: DayBundle
    missing: list[str] = field(default_factory=list)        # 核心資料集為空／截斷者（名稱見 CORE_REQUIRED）→ waiting
    warnings: list[str] = field(default_factory=list)       # 不擋班、但要看得見（美股 T−1 未到等）
    counts: dict[str, int] = field(default_factory=dict)    # 各資料集原始列數（log 用；季報全市場查詢是否被支援第一天就看得到）
    extras: dict[str, list] = field(default_factory=dict)   # dividend／month_revenue／financial_statements 列
    official_errors: list[tuple[str, str, str]] = field(default_factory=list)
    n_calls: int = 0


class Fetcher:
    def __init__(self, fm: Any, oc: Any, *, required: Sequence[str] = CORE_REQUIRED,
                 price_min_rows: int = CFG.PRICE_DAILY_MIN_ROWS, pool_min_cover: float = 0.5) -> None:
        """`price_min_rows`／`pool_min_cover`＝上游截斷守門（回補層 2026-09-10 事故：price_daily 200 只回 3 列）：
        全市場切片原始列數 < `price_min_rows`，或池內有列的檔數 < 池 × `pool_min_cover`，一律列 `stocks` 缺 → waiting、不寫包。"""
        self.fm, self.oc = fm, oc
        self.required = tuple(required)
        self.price_min_rows, self.pool_min_cover = int(price_min_rows), float(pool_min_cover)
        self.n_calls = 0
        self._month_cache: dict[tuple[str, str], dict[str, float] | None] = {}

    # -- 低階 --
    def _get(self, key: str, **params: Any) -> list[dict]:
        self.n_calls += 1
        return list(self.fm.get(ds(key), **params))

    def _official_body(self, key: str, param_key: str) -> Any | None:
        self.n_calls += 1
        code, body, _text = self.oc.get(ds(key), T.OFFICIAL_PARAMS[key](param_key))
        if code != 200 or body is None:
            return None
        ok, _why = T.official_body_ok(body, "twse" if key.startswith("twse") else "tpex")
        return body if ok else None

    # -- 交易日軸 --
    def trading_days_since(self, last_date: str, upto: str) -> list[str]:
        """`last_date` 之後到 `upto`（含）的台北交易日＝TAIEX 有列的日子（1 次呼叫）。"""
        if upto <= last_date:
            return []
        rows = self._get("index_price", data_id=C.INDEX_ID["twse"], start_date=next_day(last_date), end_date=upto)
        return sorted({str(r["date"]) for r in rows if r.get("date") and str(r["date"]) <= upto and r.get("close") is not None})

    def stock_info(self) -> list[dict]:
        return self._get("stock_info")

    # -- 新入池檔歷史（§7.7 甲）--
    def fetch_entrant(self, stock_id: str, info: Mapping[str, Any], start: str, end: str) -> dict[str, dict[str, Any]]:
        """該檔 5 個資料集的 `data_id=<sid>` 區間查詢（`ENTRANT_DATASETS`，各 1 次）→ `entrant_days_from_rows`。
        失敗以例外表達（呼叫端記 warnings、不寫側檔）；`n_calls` 只計實際送出的次數。"""
        sid = str(stock_id)
        got = [self._get(key, data_id=sid, start_date=start, end_date=end) for key in ENTRANT_DATASETS]
        price, inst, margin, short, sh = got
        return entrant_days_from_rows(sid, info, price, inst=inst, margin=margin, short=short, sh=sh)

    # -- 當日 --
    def fetch_day(self, T_: str, pool: Mapping[str, Any], *, last_us: str | None, last_fx: str | None,
                  extras: bool = True) -> DayFetch:
        n0 = self.n_calls
        b = DayBundle(tpe_date=T_)
        miss: list[str] = []
        warn: list[str] = []
        counts: dict[str, int] = {}
        errors: list[tuple[str, str, str]] = []

        def need(name: str, ok: bool) -> None:
            if not ok and name in self.required:
                miss.append(name)

        idx_rows: list[dict] = []
        for m in MARKETS:
            idx_rows += self._get("index_price", data_id=C.INDEX_ID[m], start_date=T_, end_date=T_)
        b.index = C.index_from_rows(idx_rows)
        for m in MARKETS:
            need(f"index:{m}", (b.index.get(m) or {}).get("close") is not None)
        price = self._get("price_daily", start_date=T_, end_date=T_)
        inst = self._get("inst_buysell", start_date=T_, end_date=T_)
        margin = self._get("margin", start_date=T_, end_date=T_)
        sh = self._get("shareholding", start_date=T_, end_date=T_)
        short = self._get("short_sale_balance", start_date=T_, end_date=T_)
        b.stocks = C.stocks_from_rows(price, pool, inst_rows=inst, margin_rows=margin, short_rows=short, shareholding_rows=sh)
        counts.update(price=len(price), inst=len(inst), margin=len(margin), shareholding=len(sh), short_sale=len(short),
                      stocks_in_pool=len(b.stocks), pool=len(pool))
        truncated = len(price) < self.price_min_rows or len(b.stocks) < len(pool) * self.pool_min_cover
        if truncated and b.stocks:
            warn.append(f"stocks:truncated(price_rows={len(price)},pool_cover={len(b.stocks)}/{len(pool)})")
        need("stocks", bool(b.stocks) and not truncated)
        need("inst", bool(inst))
        need("margin", bool(margin))
        need("shareholding", bool(sh))
        need("short_sale", bool(short))
        for m in MARKETS:
            inst_key = "twse_bfi82u" if m == "twse" else "tpex_inst_summary"
            month_key = "twse_fmtqik" if m == "twse" else "tpex_trading_index"
            inst_body = self._official_body(inst_key, T_)
            month = T_[:4] + T_[5:7]
            if (m, month) not in self._month_cache:                     # 月表每月解析一次、同一次執行內快取
                body = self._official_body(month_key, month)
                amounts = None
                if body is not None:
                    try:
                        amounts = C.parse_month_body(m, body)
                    except OP.OfficialParseError as e:
                        raise DailyFetchError(f"月表解析失敗 ({month}, {C.OFFICIAL_MONTH_TABLE[m]}): {e}") from e
                self._month_cache[(m, month)] = amounts
            out, errs = C.official_day(m, T_, inst_body, self._month_cache[(m, month)])
            b.official[m] = out
            errors.extend(errs)                                          # 法人 body 解析失敗＝該欄 None → 下面判缺
            need(f"official_inst:{m}", out["foreign_net_k"] is not None and out["trust_net_k"] is not None)
            need(f"official_amount:{m}", out["amount_k"] is not None)
        fut = self._get("futures_daily", data_id=C.TX, start_date=T_, end_date=T_)
        counts.update(futures_daily=len(fut))
        b.futures = C.futures_from_rows(fut)
        need("futures_daily", bool(b.futures))
        oi = self._get("futures_inst", data_id=C.TX, start_date=T_, end_date=T_)
        b.foreign_net_oi = C.futures_oi_from_rows(oi)
        need("futures_inst", b.foreign_net_oi is not None)
        tm = self._get("total_margin", start_date=T_, end_date=T_)
        b.total_margin = C.total_margin_from_rows(tm)
        need("total_margin", b.total_margin is not None)
        vix = self._get("vix", start_date=T_, end_date=T_)
        b.vix = C.vix_from_rows(vix)
        need("vix", b.vix is not None)
        us_start = next_day(last_us) if last_us else days_before(T_, 500)
        us_rows: list[dict] = []
        for sid in (C.US_SPX, C.US_SOX):
            us_rows += self._get("us_index", data_id=sid, start_date=us_start, end_date=T_)
        b.us = [x for x in C.us_from_rows(us_rows) if x[0] <= T_ and (last_us is None or x[0] > last_us)]
        latest_us = b.us[-1][0] if b.us else last_us
        if latest_us is None or latest_us < prev_weekday(T_):
            warn.append(f"us:lag(latest={latest_us},expected>={prev_weekday(T_)})")     # 美國假日也會觸發，只警示不擋
        counts.update(us=len(us_rows))
        fx_start = next_day(last_fx) if last_fx else days_before(T_, 500)
        fx_rows = self._get("fx_usd", data_id="USD", start_date=fx_start, end_date=T_)
        b.fx = [x for x in C.fx_from_rows(fx_rows) if x[0] <= T_ and (last_fx is None or x[0] > last_fx)]
        ex: dict[str, list] = {}
        if extras:
            div = self._get("dividend_result", start_date=days_before(T_, DIVIDEND_LOOKBACK_DAYS), end_date=T_)
            ex["dividend"] = [(str(r["stock_id"]), str(r["date"]), r.get("before_price"), r.get("after_price"))
                              for r in div if r.get("stock_id") and r.get("date")]
            mr: list[dict] = []
            for s_, e_ in month_windows(T_, REVENUE_MONTHS_BACK):
                mr += self._get("month_revenue", start_date=s_, end_date=e_)
            ex["month_revenue"] = [r for r in mr if str(r.get("stock_id")) in pool]
            fs_rows: list[dict] = []
            for pe in quarter_ends(T_, STATEMENT_QUARTERS_BACK):
                fs_rows += self._get("financial_statements", start_date=pe, end_date=pe)
            ex["financial_statements"] = [r for r in fs_rows if str(r.get("stock_id")) in pool and r.get("type") in NEEDED_TYPES]
            counts.update(dividend=len(div), month_revenue=len(mr), financial_statements=len(fs_rows))
        return DayFetch(bundle=b, missing=sorted(set(miss)), warnings=warn, counts=counts, extras=ex, official_errors=errors,
                        n_calls=self.n_calls - n0)


ENTRANT_DATASETS = ("price_daily", "inst_buysell", "margin", "short_sale_balance", "shareholding")   # 每檔 5 次（§7.7 第 2 點）


def entrant_days_from_rows(stock_id: str, info: Mapping[str, Any], price: Iterable[Row], inst: Iterable[Row] = (),
                           margin: Iterable[Row] = (), short: Iterable[Row] = (), sh: Iterable[Row] = ()) -> dict[str, dict[str, Any]]:
    """`data_id=<sid>` 區間查詢的 5 個資料集列 → `{date: 與 bundle.stocks[sid] 同形的列}`。**逐日呼叫同一支
    `collect.stocks_from_rows`**（把該日的列餵進去、池只放這一檔），與 `fetch_day` 對全市場切片的建法逐字同一條路徑
    （parity by construction，不另寫欄位映射）；`stock_id` 不符的列一律忽略。"""
    sid = str(stock_id)
    by: dict[str, dict[str, list]] = {}

    def put(kind: str, rows: Iterable[Row]) -> None:
        for r in rows:
            if str(r.get("stock_id")) != sid or not r.get("date"):
                continue
            by.setdefault(str(r["date"]), {}).setdefault(kind, []).append(r)
    put("price", price)
    put("inst", inst)
    put("margin", margin)
    put("short", short)
    put("sh", sh)
    pool_one = {sid: dict(info)}
    out: dict[str, dict[str, Any]] = {}
    for d in sorted(by):
        g = by[d]
        st = C.stocks_from_rows(g.get("price", []), pool_one, inst_rows=g.get("inst", []), margin_rows=g.get("margin", []),
                                short_rows=g.get("short", []), shareholding_rows=g.get("sh", []))
        if sid in st:                                                   # 沒有價量列的日子不成列（與原料包同：籌碼列掛在價量列上）
            out[d] = st[sid]
    return out


def pool_rows_from_info(rows: Iterable[Mapping[str, Any]]) -> list[dict]:
    """TaiwanStockInfo 列 → pool 檔的 5 欄列（＝`feed.load_pool` 讀的欄）。"""
    cols = ("stock_id", "type", "industry_category", "stock_name", "date")
    return [{c: r.get(c) for c in cols} for r in rows]
