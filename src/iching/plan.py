"""請求計畫（純函式，免 token 免網路）。

對每個資料集依 strategy 展開「鍵清單」（＝coverage 的 key），並算兩種策略的請求數：
- 主策略（config 的 strategy）
- 替代策略（config 的 alt_strategy，多為 per_stock＝每股 1 請求；價格類主策略為全市場單日切片，
  taiwan-flows 的做法：1 天 1 請求拿全市場）

台北交易日數：有 `data/calendar_tpe.json` 就用實際交易日；沒有就以**平日數**當上限估計並標明。
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from typing import Sequence

from .calendar import calendar_covers  # noqa: F401  （re-export：scripts／tests 以 P.calendar_covers 取用）
from .config import (B19_TOTAL, DATASETS, POOL_SIZE_RULING, DatasetSpec, FINMIND_LIMIT_PER_HOUR)


def weekdays_between(start: str, end: str) -> list[str]:
    a = dt.date.fromisoformat(start)
    b = dt.date.fromisoformat(end)
    out = []
    while a <= b:
        if a.weekday() < 5:
            out.append(a.isoformat())
        a += dt.timedelta(days=1)
    return out


def chunk_ranges(start: str, end: str, chunk: str) -> list[tuple[str, str]]:
    """把 [start, end] 依 year/quarter/month/all 切成 (s, e) 區間（含端點、不重疊）。"""
    if chunk == "all":
        return [(start, end)]
    s = dt.date.fromisoformat(start)
    e = dt.date.fromisoformat(end)
    out: list[tuple[str, str]] = []
    cur = s
    while cur <= e:
        if chunk == "year":
            nxt = dt.date(cur.year + 1, 1, 1)
        elif chunk == "quarter":
            q_end_month = ((cur.month - 1) // 3 + 1) * 3
            nxt = dt.date(cur.year + (q_end_month == 12), 1 if q_end_month == 12 else q_end_month + 1, 1)
        elif chunk == "month":
            nxt = dt.date(cur.year + (cur.month == 12), 1 if cur.month == 12 else cur.month + 1, 1)
        else:
            raise ValueError(chunk)
        last = min(e, nxt - dt.timedelta(days=1))
        out.append((cur.isoformat(), last.isoformat()))
        cur = nxt
    return out


def clip_dates(dates: Sequence[str], start: str, end: str) -> list[str]:
    return [d for d in dates if start <= d <= end]


@dataclass
class DatasetPlan:
    spec: DatasetSpec
    strategy: str
    keys: list[str]
    n_requests: int
    alt_strategy: str | None
    alt_requests: int | None
    basis: str          # 「交易日曆」或「平日上限估計」或「固定」

    @property
    def key(self) -> str:
        return self.spec.key


def keys_for(spec: DatasetSpec, strategy: str, *, tpe_dates: Sequence[str] | None,
             stock_ids: Sequence[str] | None, start: str | None = None, end: str | None = None) -> tuple[list[str], str]:
    """回 (keys, basis)。keys 的格式：
        daily_slice/official  'YYYY-MM-DD'
        official_month        'YYYYMM'
        range_slice           'YYYY-MM-DD~YYYY-MM-DD'
        per_id / per_stock    '<id>:YYYY-MM-DD~YYYY-MM-DD'
        single                'all'
    """
    s = start or spec.start
    e = end or spec.end
    if strategy in ("daily_slice", "official"):
        if tpe_dates and calendar_covers(tpe_dates, s, e):
            return clip_dates(tpe_dates, s, e), "交易日曆"
        # 日曆缺席或**只涵蓋部分區間**（例如只落地了某一季的指數）→ 不採用，改平日上限；
        # 否則會靜默把 56 天當成全期（2026-09-09 自測踩到：部分日曆被誤當正式檔）
        return weekdays_between(s, e), "平日上限估計" + ("（日曆未涵蓋整段，未採用）" if tpe_dates else "")
    # 區間型鍵一律對齊 spec 全區間的固定切塊網格（year/quarter/month），--from/--to 只選塊、不改塊界：
    # 否則 `--from 2022-01-03 --to 2022-01-05` 會做出 `TAIEX:2022-01-03~2022-01-05` 這種與年鍵重疊的 coverage 鍵
    # （2026-09-09 自測踩到），重跑時同一列在兩個鍵之間搬家、coverage 語意變髒。
    grid = [(a, b) for a, b in chunk_ranges(spec.start, spec.end, spec.chunk) if a <= e and b >= s]
    if strategy == "official_month":
        months = [(a, b) for a, b in chunk_ranges(spec.start, spec.end, "month") if a <= e and b >= s]
        return [a[:7].replace("-", "") for a, _ in months], "固定"
    if strategy == "range_slice":
        return [f"{a}~{b}" for a, b in grid], "固定"
    if strategy == "per_id":
        return [f"{i}:{a}~{b}" for i in spec.data_ids for a, b in grid], "固定"
    if strategy == "per_stock":
        if stock_ids:
            ids = list(stock_ids)
            basis = "universe.db 個股池"
        else:
            ids = [f"<stock{i:04d}>" for i in range(POOL_SIZE_RULING)]
            basis = f"裁定池規模 {POOL_SIZE_RULING} 檔估計"
        # per_stock 一律整段一請求、忽略 --from/--to（FinMind 帶 data_id 可一次取多年：
        # taiwan-backtest fetch_taiex.py 取 18 年），鍵才會穩定
        return [f"{i}:{spec.start}~{spec.end}" for i in ids], basis
    if strategy == "single":
        return ["all"], "固定"
    raise ValueError(strategy)


def build_plan(*, tpe_dates: Sequence[str] | None = None, stock_ids: Sequence[str] | None = None,
               groups: Sequence[str] = ("core",), only: Sequence[str] | None = None,
               start: str | None = None, end: str | None = None,
               strategy_override: dict[str, str] | None = None) -> list[DatasetPlan]:
    plans: list[DatasetPlan] = []
    for spec in DATASETS:
        if only and spec.key not in only:
            continue
        if not only and spec.group not in groups:
            continue
        strat = (strategy_override or {}).get(spec.key, spec.strategy)
        keys, basis = keys_for(spec, strat, tpe_dates=tpe_dates, stock_ids=stock_ids, start=start, end=end)
        alt_n = None
        if spec.alt_strategy and spec.alt_strategy != strat:
            alt_keys, _ = keys_for(spec, spec.alt_strategy, tpe_dates=tpe_dates, stock_ids=stock_ids, start=start, end=end)
            alt_n = len(alt_keys)
        plans.append(DatasetPlan(spec, strat, keys, len(keys), spec.alt_strategy if alt_n is not None else None, alt_n, basis))
    return plans


def plan_summary(plans: Sequence[DatasetPlan], interval_sec: float) -> dict:
    fm = [p for p in plans if p.spec.source == "finmind"]
    off = [p for p in plans if p.spec.source != "finmind"]
    n_fm = sum(p.n_requests for p in fm)
    n_off = sum(p.n_requests for p in off)
    return {
        "finmind_requests": n_fm,
        "official_requests": n_off,
        "finmind_hours_at_limit": n_fm / FINMIND_LIMIT_PER_HOUR,
        "finmind_hours_at_interval": n_fm * interval_sec / 3600,
        "official_hours_at_4s": n_off * 4.0 / 3600,
        "b19_total": B19_TOTAL,
    }


def format_plan(plans: Sequence[DatasetPlan], interval_sec: float) -> str:
    lines = []
    hdr = f"{'key':<22}{'dataset':<44}{'策略':<13}{'請求數':>8}  {'替代策略':<11}{'替代請求數':>10}  基準"
    lines.append(hdr)
    lines.append("-" * len(hdr))
    for p in plans:
        alt = p.alt_strategy or "-"
        altn = "" if p.alt_requests is None else f"{p.alt_requests:,}"
        ds = p.spec.dataset if p.spec.source == "finmind" else f"[{p.spec.source}] {p.spec.dataset.split('/')[-1]}"
        lines.append(f"{p.key:<22}{ds:<44}{p.strategy:<13}{p.n_requests:>8,}  {alt:<11}{altn:>10}  {p.basis}"
                     f"  tier={p.spec.tier} verified={p.spec.verified}")
    s = plan_summary(plans, interval_sec)
    lines.append("-" * len(hdr))
    lines.append(f"FinMind 請求合計 {s['finmind_requests']:,} 次 → 額度上限 {FINMIND_LIMIT_PER_HOUR}/hr 純額度 "
                 f"{s['finmind_hours_at_limit']:.1f} 小時；以間隔 {interval_sec}s 估 {s['finmind_hours_at_interval']:.1f} 小時")
    if s["official_requests"]:
        lines.append(f"TWSE/TPEx 官方請求合計 {s['official_requests']:,} 次 → 4 秒節流估 {s['official_hours_at_4s']:.1f} 小時")
    lines.append(f"對照 spec/P1-B1-market.md §B1.9：17 次/交易日 × 1,650 日 ≈ {B19_TOTAL:,} 次"
                 f"（本計畫 FinMind 部分為 {s['finmind_requests'] / B19_TOTAL:.0%}；差異來源：指數／期貨／美股／匯率／"
                 f"總融資改整年區間查詢而非逐日；官方法人與成交金額走 TWSE/TPEx 不占 FinMind 額度）")
    return "\n".join(lines)
