"""請求計畫（純函式，免 token 免網路）。

對每個資料集依 strategy 展開「鍵清單」（＝coverage 的 key），並算兩種策略的請求數：
- 主策略（config 的 strategy）
- 替代策略（config 的 alt_strategy，多為 per_stock＝每股 1 請求；價格類主策略為全市場單日切片，
  taiwan-flows 的做法：1 天 1 請求拿全市場）

台北交易日數：有 `data/calendar_tpe.json` 就用實際交易日；沒有就以**平日數**當上限估計並標明。
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
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
    # --data-end 延伸後的鍵位移（見 key_shifts）：[(新鍵, 被取代舊鍵或 None)]；不帶 data_end 為空
    shifts: list[tuple[str, str | None]] = field(default_factory=list)

    @property
    def key(self) -> str:
        return self.spec.key

    @property
    def n_refetch(self) -> int:
        """延伸塊（整塊重抓並取代舊鍵）數。"""
        return sum(1 for _, old in self.shifts if old is not None)

    @property
    def n_new_blocks(self) -> int:
        """純新增塊數（不重抓舊塊）。"""
        return sum(1 for _, old in self.shifts if old is None)


def grid_end_for(spec: DatasetSpec, data_end: str | None) -> str:
    """本次鍵網格的迄日：`data_end` 有給就取代 `spec.end`（只允許延伸；早於 spec.end 一律拒絕，避免把塊界往回切
    而做出與既有 coverage 鍵重疊的短鍵）。不動 spec 物件本身、不動 config.DATA_END。"""
    if data_end is None:
        return spec.end
    dt.date.fromisoformat(data_end)          # 非 ISO 日期 → ValueError
    if data_end < spec.end:
        raise ValueError(f"data_end {data_end} 早於 {spec.key} 的 spec.end {spec.end}：只允許延伸，不允許縮短")
    return data_end


def keys_for(spec: DatasetSpec, strategy: str, *, tpe_dates: Sequence[str] | None,
             stock_ids: Sequence[str] | None, start: str | None = None, end: str | None = None,
             data_end: str | None = None) -> tuple[list[str], str]:
    """回 (keys, basis)。keys 的格式：
        daily_slice/official  'YYYY-MM-DD'
        official_month        'YYYYMM'
        range_slice           'YYYY-MM-DD~YYYY-MM-DD'
        per_id / per_stock    '<id>:YYYY-MM-DD~YYYY-MM-DD'
        single                'all'

    `data_end`（2026-09-15 D-3 對帳儀式補）：**只在本次呼叫**把鍵網格迄日由 `spec.end`（＝config.DATA_END）延伸到
    `data_end`；config.DATA_END 與回測切分一字不動。動機：`--from/--to` 只選塊不改塊界，超過 spec.end 的區間在固定網格上
    選不到任何塊 → index_price 永遠補不到新月份 → 日曆不涵蓋 → 全市場切片全部中止（2026-09-15 Hetzner 實跑）。
    **已知副作用（鍵搬家，取代語意）**：迄日延伸後，最後一個 year／quarter 塊的鍵由 `2026-01-01~2026-08-31` 變成
    `2026-01-01~<data_end>`（quarter 同理 `2026-07-01~<data_end>`；per_stock 的 all 塊變成 `<id>:<start>~<data_end>`）——
    新鍵不在 coverage 內 → **該塊整塊重抓，落地成功時在同一交易內刪掉舊鍵**（同 dataset、同 data_version 的原始列＋
    coverage＋failures；`Store.record_success(replaces=)`）。原始表 PK 是 (cov_key, row_hash)，不取代的話同一列會在
    新舊兩鍵下各存一份，所以才要刪；新鍵抓失敗則舊鍵原封不動。month 塊則只是新增 `2026-09-01~<data_end>`、
    08 月鍵不動。哪些鍵屬「延伸塊」由 `key_shifts()` 算（回 (新鍵, 被取代舊鍵)），`format_plan` 與 run 摘要都會標示。
    """
    s = start or spec.start
    grid_end = grid_end_for(spec, data_end)
    e = end or grid_end
    if strategy in ("daily_slice", "official"):
        if tpe_dates and calendar_covers(tpe_dates, s, e):
            return clip_dates(tpe_dates, s, e), "交易日曆"
        # 日曆缺席或**只涵蓋部分區間**（例如只落地了某一季的指數）→ 不採用，改平日上限；
        # 否則會靜默把 56 天當成全期（2026-09-09 自測踩到：部分日曆被誤當正式檔）
        return weekdays_between(s, e), "平日上限估計" + ("（日曆未涵蓋整段，未採用）" if tpe_dates else "")
    # 區間型鍵一律對齊 spec 全區間的固定切塊網格（year/quarter/month），--from/--to 只選塊、不改塊界：
    # 否則 `--from 2022-01-03 --to 2022-01-05` 會做出 `TAIEX:2022-01-03~2022-01-05` 這種與年鍵重疊的 coverage 鍵
    # （2026-09-09 自測踩到），重跑時同一列在兩個鍵之間搬家、coverage 語意變髒。
    # （網格迄日＝grid_end：不帶 data_end 時就是 spec.end，鍵逐字不變；帶了才延伸，見 docstring「鍵搬家」）
    grid = [(a, b) for a, b in chunk_ranges(spec.start, grid_end, spec.chunk) if a <= e and b >= s]
    if strategy == "official_month":
        months = [(a, b) for a, b in chunk_ranges(spec.start, grid_end, "month") if a <= e and b >= s]
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
        return [f"{i}:{spec.start}~{grid_end}" for i in ids], basis
    if strategy == "single":
        return ["all"], "固定"
    raise ValueError(strategy)


def key_shifts(spec: DatasetSpec, strategy: str, keys: Sequence[str], *, data_end: str | None,
               stock_ids: Sequence[str] | None = None) -> list[tuple[str, str | None]]:
    """`data_end` 延伸後，`keys` 中哪些是**不帶 data_end 時不存在**的鍵。回 [(新鍵, 被取代的舊鍵或 None)]：
    - 舊鍵非 None ＝「延伸塊（重抓並取代舊鍵）」：同起點的塊只是迄日往後挪（year／quarter／per_stock all），整塊重抓、
      落地成功即刪舊鍵（run_dataset 把它當 record_success 的 `replaces`）；
    - 舊鍵 None  ＝ 純新增塊（month 的新月份、official_month 的新 YYYYMM、daily_slice／official 的新日期），不重抓任何舊塊。
    不帶 data_end 一律回空清單。"""
    if data_end is None or data_end == spec.end:
        return []
    if strategy in ("daily_slice", "official"):
        return [(k, None) for k in keys if k > spec.end]
    if strategy == "official_month":
        return [(k, None) for k in keys if k > spec.end[:7].replace("-", "")]
    if strategy == "single":
        return []
    # 區間型：對照**不帶 data_end、不帶 --from/--to** 的完整舊網格（--from/--to 會把舊塊濾掉，對不到被取代者）
    old_full, _ = keys_for(spec, strategy, tpe_dates=None, stock_ids=stock_ids)
    old_by_prefix = {k.rsplit("~", 1)[0]: k for k in old_full}
    old_set = set(old_full)
    return [(k, old_by_prefix.get(k.rsplit("~", 1)[0])) for k in keys if k not in old_set]


def build_plan(*, tpe_dates: Sequence[str] | None = None, stock_ids: Sequence[str] | None = None,
               groups: Sequence[str] = ("core",), only: Sequence[str] | None = None,
               start: str | None = None, end: str | None = None,
               strategy_override: dict[str, str] | None = None, data_end: str | None = None) -> list[DatasetPlan]:
    """`data_end`：本次計畫的鍵網格迄日覆寫（見 keys_for docstring；不動 DATASETS／config.DATA_END）。"""
    plans: list[DatasetPlan] = []
    for spec in DATASETS:
        if only and spec.key not in only:
            continue
        if not only and spec.group not in groups:
            continue
        strat = (strategy_override or {}).get(spec.key, spec.strategy)
        keys, basis = keys_for(spec, strat, tpe_dates=tpe_dates, stock_ids=stock_ids, start=start, end=end, data_end=data_end)
        alt_n = None
        if spec.alt_strategy and spec.alt_strategy != strat:
            alt_keys, _ = keys_for(spec, spec.alt_strategy, tpe_dates=tpe_dates, stock_ids=stock_ids, start=start, end=end,
                                   data_end=data_end)
            alt_n = len(alt_keys)
        shifts = key_shifts(spec, strat, keys, data_end=data_end, stock_ids=stock_ids)
        plans.append(DatasetPlan(spec, strat, keys, len(keys), spec.alt_strategy if alt_n is not None else None, alt_n, basis,
                                 shifts))
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
    shifted = [p for p in plans if p.shifts]
    if shifted:
        lines.append("-" * len(hdr))
        lines.append("--data-end 鍵網格延伸（鍵搬家）：「延伸塊（重抓並取代舊鍵）」＝同起點的塊迄日往後挪、新鍵不在 coverage 內 → 整塊重抓，"
                     "落地成功即在同一交易內刪掉舊鍵（原始列＋coverage）；「新增塊」＝純新增、不重抓舊塊")
        for p in shifted:
            re_ = [(k, o) for k, o in p.shifts if o is not None]
            new = [k for k, o in p.shifts if o is None]
            parts = []
            if re_:
                ex = "；".join(f"{o} → {k}" for k, o in re_[:2]) + ("；…" if len(re_) > 2 else "")
                parts.append(f"延伸塊（重抓並取代舊鍵）{len(re_)} 鍵：{ex}")
            if new:
                ex = "、".join(new[:3]) + ("、…" if len(new) > 3 else "")
                parts.append(f"新增塊 {len(new)} 鍵：{ex}")
            lines.append(f"  {p.key:<22}" + "；".join(parts))
    return "\n".join(lines)
