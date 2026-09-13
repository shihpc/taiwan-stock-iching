"""基本面橋（第 13 項 13b）——**純函式層，不做任何 DB 存取**。原料由 `replay_io.ReplaySource.load_fundamentals()` 供給。

把 `raw_month_revenue`／`raw_financial_statements` 的列變成 `StockInputs` 的四個欄位：
`monthly_revenue`（`[('YYYY-MM', 元), …]` 升冪）、`fundamentals`（9 鍵 dict）、`industry_median_3m_yoy`、`industry_revenue_n`。

## 可得日（PIT）

一律走 `available_at.py` 的法定期限（不用 `create_time`，83% 空值；財報無公布日欄）：
月營收＝次月 10 日（金融桶 15 日）、季報＝季後 45 日（金融桶 2 個月）、年報＝3 個月，再取
`first_trading_day_on_or_after(期限)` ＝ 可用日 A；**T ≥ A 才看得到**（訊號日＝T 收盤後，期限日當天收盤後即可用，
與 `docs/pre-registration.md` §1.2.5 一致）。金融桶判定 `universe.is_financial()`（裁定 #28 明列集合）。

## 對應表（`docs/P2-REPLAY-PLAN.md` §9，依 2026-09-13 Hetzner 實查 68 組 `type`→`origin_name` 定）

| 鍵 | `type` | 期別 |
|---|---|---|
| `eps` | `EPS` | 最近可得期 P |
| `eps_ly` | `EPS` | P 的前 4 期（去年同季） |
| `gross_margin` | `GrossProfit` ÷ `Revenue` × 100 | P；`Revenue ≤ 0` → None |
| `gross_margin_prev_q` | 同上 | P 的前 1 期 |
| `pretax_income` | `PreTaxIncome`，缺則 `IncomeBeforeIncomeTax`（金融業寫法） | P |
| `pretax_income_ly` | 同上 | P 的前 4 期 |
| `price_at_period_end` | 期末日（或其前最近交易日）**原始**收盤價 | P（B2.0 政策 2 的明文例外）。取法＝**全市場** ≤P 的最近交易日，該檔當日無有效收盤（停牌／無列／close=0）即 None、**不逐檔回退更早一日**（只影響替代指標 EPS 差額÷股價） |
| `equity`／`equity_prev_q` | **無來源，固定 None**（裁定 #36 乙） | — |

「前 N 期」用**期別序**（3／6／9／12 月末日的序列）而非曆日；同 type 某期缺列即該鍵 None。
FinMind 季值是**單季**（2330 Q2 營收恰等於 4／5／6 月營收之和，P2-KICKOFF §5 #35 ③），不必差分。

## 產業中位數

`industry_median_3m_yoy`＝同產業（`industry_category`）各檔以**各自** as-of T 的最新月算 `revenue_yoy_3m(rev, latest, 0, 3)`
（`score/stock.py` 同一支函式），取非缺值者的中位數；`industry_revenue_n`＝非缺值檔數。母體＝池內全體普通股
（與第 12 項產業聚合同一母體）。
"""
from __future__ import annotations

import bisect
import datetime as dt
import statistics
from dataclasses import dataclass, field
from typing import Iterable, Mapping, Sequence

from .available_at import financial_report_deadline, first_trading_day_on_or_after, monthly_revenue_deadline
from .score.stock import revenue_yoy_3m
from .score.transform import Missing
from .universe import is_financial

EPS_TYPE = "EPS"
GROSS_TYPE = "GrossProfit"
REVENUE_TYPE = "Revenue"
PRETAX_TYPES = ("PreTaxIncome", "IncomeBeforeIncomeTax")      # 先後順序＝優先序
NEEDED_TYPES = (EPS_TYPE, GROSS_TYPE, REVENUE_TYPE, *PRETAX_TYPES)
YOY_MONTHS = 3
FUND_KEYS = ("eps", "eps_ly", "gross_margin", "gross_margin_prev_q", "price_at_period_end",
             "pretax_income", "pretax_income_ly", "equity", "equity_prev_q")


class FundamentalsError(RuntimeError):
    pass


def period_index(period_end: str) -> int:
    """期別序：`YYYY-MM-DD`（月為 3／6／9／12）→ 年×4＋季。前 4 期＝去年同季、前 1 期＝上一季。"""
    y, m = int(period_end[:4]), int(period_end[5:7])
    if m not in (3, 6, 9, 12):
        raise FundamentalsError(f"期別末日月份需為 3／6／9／12：{period_end!r}")
    return y * 4 + (m // 3 - 1)


@dataclass
class StockFundamentals:
    """一檔的原料（已排序）。`quarters[p] = {type: value}`；`price_at_period_end[p] = 原始收盤`。"""
    stock_id: str
    is_financial: bool
    monthly: list[tuple[str, float]] = field(default_factory=list)          # [('YYYY-MM', 元)] 升冪
    monthly_available: list[str] = field(default_factory=list)              # 與 monthly 同長，可用日（None→'9999-12-31'）
    periods: list[str] = field(default_factory=list)                        # 期別末日升冪
    period_available: list[str] = field(default_factory=list)
    quarters: dict[str, dict[str, float]] = field(default_factory=dict)
    price_at_period_end: dict[str, float] = field(default_factory=dict)

    def monthly_asof(self, T: str) -> list[tuple[str, float]]:
        n = bisect.bisect_right(self.monthly_available, T)
        return self.monthly[:n]

    def period_asof(self, T: str) -> str | None:
        n = bisect.bisect_right(self.period_available, T)
        return self.periods[n - 1] if n else None


def _val(q: Mapping[str, Mapping[str, float]], period: str | None, types: Sequence[str]) -> float | None:
    if period is None:
        return None
    row = q.get(period)
    if not row:
        return None
    for t in types:
        v = row.get(t)
        if v is not None:
            return float(v)
    return None


def _gross_margin(q: Mapping[str, Mapping[str, float]], period: str | None) -> float | None:
    gp, rev = _val(q, period, (GROSS_TYPE,)), _val(q, period, (REVENUE_TYPE,))
    if gp is None or rev is None or rev <= 0:
        return None
    return gp / rev * 100.0


def fundamentals_dict(sf: StockFundamentals, T: str) -> dict[str, float | None] | None:
    """as-of T 的 9 鍵 dict；沒有任何可得期別 → None（`StockInputs.fundamentals=None` → 族 B 整族缺）。"""
    p = sf.period_asof(T)
    if p is None:
        return None
    by_idx = {period_index(x): x for x in sf.periods}
    i = period_index(p)
    p_ly, p_prev = by_idx.get(i - 4), by_idx.get(i - 1)
    return {
        "eps": _val(sf.quarters, p, (EPS_TYPE,)), "eps_ly": _val(sf.quarters, p_ly, (EPS_TYPE,)),
        "gross_margin": _gross_margin(sf.quarters, p), "gross_margin_prev_q": _gross_margin(sf.quarters, p_prev),
        "price_at_period_end": sf.price_at_period_end.get(p),
        "pretax_income": _val(sf.quarters, p, PRETAX_TYPES), "pretax_income_ly": _val(sf.quarters, p_ly, PRETAX_TYPES),
        "equity": None, "equity_prev_q": None,
    }


def build_stock(stock_id: str, industry: str | None, monthly_rows: Iterable[tuple[int, int, float]],
                quarter_rows: Iterable[tuple[str, str, float]], price_at_period_end: Mapping[str, float],
                tpe_dates: list[str]) -> StockFundamentals:
    """把一檔的原料列整理成 `StockFundamentals`。
    `monthly_rows`＝`(revenue_year, revenue_month, revenue)`；`quarter_rows`＝`(period_end, type, value)`；
    `tpe_dates`＝台北交易日曆（升冪，含回測全段＋之後至少數月，否則可用日落在日曆外＝永遠不可用）。"""
    fin = is_financial(industry)
    sf = StockFundamentals(stock_id=stock_id, is_financial=fin)
    mon: dict[str, float] = {}
    for y, m, v in monthly_rows:
        if v is None:
            continue
        mon[f"{int(y):04d}-{int(m):02d}"] = float(v)          # 同月多列（重送）取最後
    for ym in sorted(mon):
        y, m = int(ym[:4]), int(ym[5:7])
        a = first_trading_day_on_or_after(monthly_revenue_deadline(y, m, is_financial=fin), tpe_dates)
        sf.monthly.append((ym, mon[ym]))
        sf.monthly_available.append(a or "9999-12-31")
    q: dict[str, dict[str, float]] = {}
    for p, t, v in quarter_rows:
        if v is None or t not in NEEDED_TYPES:
            continue
        q.setdefault(str(p), {})[str(t)] = float(v)
    for p in sorted(q, key=period_index):
        a = first_trading_day_on_or_after(financial_report_deadline(p, is_financial=fin), tpe_dates)
        sf.periods.append(p)
        sf.period_available.append(a or "9999-12-31")
        sf.quarters[p] = q[p]
        if p in price_at_period_end:
            sf.price_at_period_end[p] = float(price_at_period_end[p])
    # 可用日必須隨期別單調不減，否則 bisect 失效（金融／一般同一檔不會變，期限函式對期別單調）
    for seq, name in ((sf.monthly_available, "monthly"), (sf.period_available, "period")):
        if any(seq[i] > seq[i + 1] for i in range(len(seq) - 1)):
            raise FundamentalsError(f"{stock_id} 的 {name} 可用日非單調：{seq}")
    return sf


class FundamentalsBridge:
    """所有池內個股的 `StockFundamentals`，加上逐日的產業中位數快取。`provider(T)` 回 `replay_step.FundamentalsProvider`。"""

    def __init__(self, stocks: Mapping[str, StockFundamentals], industry_of: Mapping[str, str | None]) -> None:
        self.stocks = dict(stocks)
        self.industry_of = dict(industry_of)
        self._day: str | None = None
        self._median: dict[str, tuple[float | None, int]] = {}

    def _industry_stats(self, T: str) -> None:
        if self._day == T:
            return
        by: dict[str, list[float]] = {}
        for sid, sf in self.stocks.items():
            ind = self.industry_of.get(sid)
            if ind is None:
                continue
            rev = dict(sf.monthly_asof(T))
            if not rev:
                continue
            y = revenue_yoy_3m(rev, max(rev), 0, YOY_MONTHS)
            if isinstance(y, Missing):
                continue
            by.setdefault(ind, []).append(float(y))
        self._median = {ind: (statistics.median(v), len(v)) for ind, v in by.items()}
        self._day = T

    def inputs_for(self, stock_id: str, T: str) -> dict:
        self._industry_stats(T)
        sf = self.stocks.get(stock_id)
        ind = self.industry_of.get(stock_id)
        med, n = self._median.get(ind, (None, 0)) if ind is not None else (None, 0)
        if sf is None:
            return {"monthly_revenue": None, "industry_median_3m_yoy": med, "industry_revenue_n": n or None, "fundamentals": None}
        mon = sf.monthly_asof(T)
        return {"monthly_revenue": mon or None, "industry_median_3m_yoy": med, "industry_revenue_n": n or None,
                "fundamentals": fundamentals_dict(sf, T)}

    def provider(self):
        return self.inputs_for

    def coverage(self, T: str) -> dict[str, int]:
        """診斷：as-of T 有月營收／有季報的檔數。"""
        return {"with_monthly": sum(1 for sf in self.stocks.values() if sf.monthly_asof(T)),
                "with_quarter": sum(1 for sf in self.stocks.values() if sf.period_asof(T) is not None),
                "stocks": len(self.stocks)}


def extend_calendar(tpe_dates: Sequence[str], months_ahead: int = 6) -> list[str]:
    """交易日曆之後補**平日**（週一～五）到 `months_ahead` 個月後，讓最後幾期的可用日不會落在日曆外。
    補的是平日不是交易日（國定假日不處理），只影響「尚未到來」的可用日，回測範圍內一律用真實日曆。"""
    out = list(tpe_dates)
    if not out:
        return out
    d = dt.date.fromisoformat(out[-1])
    end = d + dt.timedelta(days=31 * months_ahead)
    while d < end:
        d += dt.timedelta(days=1)
        if d.weekday() < 5:
            out.append(d.isoformat())
    return out
