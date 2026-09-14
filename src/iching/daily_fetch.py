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
REVENUE_LOOKBACK_DAYS = 45          # 月營收：每月 10 日前後公布，45 曆日窗必含最近一個公布月
STATEMENT_LOOKBACK_DAYS = 120       # 季報：年報期限＝期末後 3 個月，120 曆日窗含遲交緩衝
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


@dataclass
class DayFetch:
    bundle: DayBundle
    missing: list[str] = field(default_factory=list)        # 核心資料集為空者（名稱見 CORE_REQUIRED）
    extras: dict[str, list] = field(default_factory=dict)   # dividend／month_revenue／financial_statements 列
    official_errors: list[tuple[str, str, str]] = field(default_factory=list)
    n_calls: int = 0


class Fetcher:
    def __init__(self, fm: Any, oc: Any, *, required: Sequence[str] = CORE_REQUIRED) -> None:
        self.fm, self.oc = fm, oc
        self.required = tuple(required)
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

    # -- 當日 --
    def fetch_day(self, T_: str, pool: Mapping[str, Any], *, last_us: str | None, last_fx: str | None,
                  extras: bool = True) -> DayFetch:
        n0 = self.n_calls
        b = DayBundle(tpe_date=T_)
        miss: list[str] = []
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
        need("stocks", bool(b.stocks))
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
        fx_start = next_day(last_fx) if last_fx else days_before(T_, 500)
        fx_rows = self._get("fx_usd", data_id="USD", start_date=fx_start, end_date=T_)
        b.fx = [x for x in C.fx_from_rows(fx_rows) if x[0] <= T_ and (last_fx is None or x[0] > last_fx)]
        ex: dict[str, list] = {}
        if extras:
            ex["dividend"] = [(str(r["stock_id"]), str(r["date"]), r.get("before_price"), r.get("after_price"))
                              for r in self._get("dividend_result", start_date=T_, end_date=T_) if r.get("stock_id") and r.get("date")]
            ex["month_revenue"] = [r for r in self._get("month_revenue", start_date=days_before(T_, REVENUE_LOOKBACK_DAYS), end_date=T_)
                                   if str(r.get("stock_id")) in pool]
            ex["financial_statements"] = [r for r in self._get("financial_statements", start_date=days_before(T_, STATEMENT_LOOKBACK_DAYS),
                                                               end_date=T_)
                                          if str(r.get("stock_id")) in pool and r.get("type") in NEEDED_TYPES]
        return DayFetch(bundle=b, missing=sorted(set(miss)), extras=ex, official_errors=errors, n_calls=self.n_calls - n0)


def pool_rows_from_info(rows: Iterable[Mapping[str, Any]]) -> list[dict]:
    """TaiwanStockInfo 列 → pool 檔的 5 欄列（＝`feed.load_pool` 讀的欄）。"""
    cols = ("stock_id", "type", "industry_category", "stock_name", "date")
    return [{c: r.get(c) for c in cols} for r in rows]
