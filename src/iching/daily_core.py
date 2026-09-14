"""每日班**離線核心**（D-2a）：只讀 repo 內檔案（原料包＋pool／factors／fundamentals／狀態快照）→ 重建視窗 → `step(T)`
→ 寫 `data/scores/<T>.json`＋`data/state/cross.json`。**零網路、零 Hetzner**；抓資料在 `daily_fetch`（D-2b）。

設計正本 `docs/P2-DAILY-PLAN.md` §7。parity 由構造保證的三個支點：
1. 原料列→`DayBundle` 只在 `collect`（D-1）；本檔只讀原料包。
2. pool／factors／fundamentals 三檔讀回時走 `feed.load_pool`／`load_factors`／`replay_io.load_fundamentals` **同一組下游函式**
   （`universe.pool_from_info`／`adjust.cumulative_factors`／`fundamentals.build_stock`），檔案只是把 SQL 的列搬進 JSON，
   去重／壞值規則在本檔逐字對齊 `feed.load_factors`（`factors_from_rows`）。
3. 廣度／產業／P_cs 逐日用 `DailyScanner` 算、寫進 `FeatureStore(":memory:")`、再用 `day_breadth／day_industry／day_p_cs` 讀回
   塞進當日 bundle（§7.0 第 2 點）——與 `scan_features.py`＋`replay_io.read_day` 同一條路徑。

T 當日的排名池由「從第一份原料包起算的新 `AdvTracker`」與 `CrossDayState.adv` 各算一次，**不相等即拒算**（`DailyCoreError`）
——這是每日班對「種子夠不夠長、狀態鏈有沒有斷」的內建斷言。
"""
from __future__ import annotations

import json
import math
import os
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from . import bundle_io as B
from . import feed as F
from . import replay_state as RS
from . import replay_step as ST
from .adjust import Event, cumulative_factors
from .features_io import FeatureStore, params_fingerprint
from .fundamentals import NEEDED_TYPES, FundamentalsBridge, build_stock, extend_calendar, period_index
from .liquidity import AdvTracker
from .run_common import TEXT_VERSION, build_params_payload, check_snapshot_meta, load_state, save_state
from .scan import DailyScanner
from .score.params import MARKETS, build_params
from .universe import pool_from_info

FILE_SCHEMA = 1
POOL_FILE = "data/pool.json"
FACTORS_FILE = "data/factors.json"
FUND_FILE = "data/fundamentals.json"
STATE_FILE = "data/state/cross.json"
SCORES_DIR = "data/scores"
CALENDAR_TPE_FILE = "data/calendar_tpe.json"
POOL_COLS = ("stock_id", "type", "industry_category", "stock_name", "date")
FUND_MONTHS_KEEP = 24          # 月營收保留：每檔自己最新月往前 24 個曆月（引擎最長回看 18 個月：revenue_accel 3+3+12）
FUND_QUARTERS_KEEP = 8         # 季報保留：每檔自己最新期往前 8 期（引擎用 P／P−1／P−4 共跨 5 期＋可得日落後 1～2 期）


class DailyCoreError(RuntimeError):
    pass


# ---------------------------------------------------------------------------
# JSON（決定性；NaN 一律寫 null）
def _clean(v: Any) -> Any:
    if isinstance(v, float):
        return None if math.isnan(v) or math.isinf(v) else v
    if isinstance(v, dict):
        return {str(k): _clean(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [_clean(x) for x in v]
    if isinstance(v, (set, frozenset)):
        return sorted(_clean(x) for x in v)
    return v


def dumps(obj: Any) -> str:
    return json.dumps(_clean(obj), ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n"


def write_json(path: Path, obj: Any) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(dumps(obj))
        f.flush()
        os.fsync(f.fileno())
    tmp.replace(path)
    return path


def read_json(path: Path, *, what: str) -> Any:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise DailyCoreError(f"{what} 讀取失敗 {path}：{e}") from e


def _require_schema(d: Any, path: Path, what: str) -> dict:
    if not isinstance(d, dict) or d.get("schema") != FILE_SCHEMA:
        raise DailyCoreError(f"{what} {path} 的 schema 不是 {FILE_SCHEMA}：{None if not isinstance(d, dict) else d.get('schema')!r}")
    return d


# ---------------------------------------------------------------------------
# pool：raw_stock_info 的 5 欄列原樣 → universe.pool_from_info（＝feed.load_pool 的同一步）
def pool_payload(rows: Iterable[Mapping[str, Any]], data_version: str) -> dict:
    out = [{c: r.get(c) for c in POOL_COLS} for r in rows]
    out.sort(key=lambda r: tuple("" if r.get(c) is None else str(r.get(c)) for c in POOL_COLS))
    return {"schema": FILE_SCHEMA, "data_version": data_version, "rows": out}


def pool_from_payload(d: dict) -> dict[str, dict]:
    pool = pool_from_info(d["rows"])
    if not pool:
        raise DailyCoreError(f"pool 檔解不出任何池成員（{len(d['rows'])} 列）")
    return pool


def load_pool_file(path: Path) -> tuple[dict, dict[str, dict]]:
    d = _require_schema(read_json(path, what="pool"), path, "pool")
    return d, pool_from_payload(d)


# ---------------------------------------------------------------------------
# factors：raw_dividend_result 的 (stock_id, date, before_price, after_price) 列，**依 SQL 序**（stock_id, date）
def factors_payload(rows: Iterable[Sequence[Any]], data_version: str) -> dict:
    return {"schema": FILE_SCHEMA, "data_version": data_version,
            "rows": [[str(r[0]), str(r[1]), r[2], r[3]] for r in rows if r[1] is not None]}


def factors_from_rows(rows: Iterable[Sequence[Any]]) -> tuple[dict[str, tuple[list[str], list[float]]], dict]:
    """逐字對齊 `feed.load_factors`：同 (stock_id, date) 只取第一列、非數或 ≤0 跳過、再 `cumulative_factors`。"""
    by: dict[str, list[Event]] = {}
    seen: set[tuple[str, str]] = set()
    n_rows = n_dup = n_bad = 0
    for sid, d, b, a in rows:
        n_rows += 1
        key = (str(sid), str(d))
        if key in seen:
            n_dup += 1
            continue
        try:
            bf, af = float(b), float(a)
        except (TypeError, ValueError):
            n_bad += 1
            continue
        if af <= 0 or bf <= 0:
            n_bad += 1
            continue
        seen.add(key)
        by.setdefault(str(sid), []).append(Event(str(d), bf, af))
    out = {sid: cumulative_factors(evs) for sid, evs in by.items()}
    return out, {"rows": n_rows, "dup_skipped": n_dup, "bad_skipped": n_bad, "stocks": len(out)}


def load_factors_file(path: Path) -> tuple[dict, dict[str, tuple[list[str], list[float]]], dict]:
    d = _require_schema(read_json(path, what="factors"), path, "factors")
    rows = d["rows"]
    if any(len(r) != 4 for r in rows):
        raise DailyCoreError(f"factors 檔 {path} 有非 4 欄的列")
    f, st = factors_from_rows(rows)
    return d, f, st


# ---------------------------------------------------------------------------
# fundamentals：月營收／季報／期末原始收盤，讀回走 fundamentals.build_stock（＝replay_io.load_fundamentals 的同一步）
def _ym_index(y: int, m: int) -> int:
    return int(y) * 12 + int(m) - 1


def prune_fundamentals(monthly: Mapping[str, list], quarters: Mapping[str, list], px: Mapping[str, Mapping[str, float]],
                       *, months_keep: int = FUND_MONTHS_KEEP, quarters_keep: int = FUND_QUARTERS_KEEP) -> dict:
    """保留期以**每檔自己的最新月／最新期**為基準（曆月／期別序），不看牆鐘；期末收盤只留仍在保留期內的期別。"""
    mo: dict[str, list] = {}
    for sid, rows in monthly.items():
        rs = sorted(((int(y), int(m), float(v)) for y, m, v in rows if v is not None), key=lambda r: (r[0], r[1]))
        if not rs:
            continue
        lo = _ym_index(rs[-1][0], rs[-1][1]) - (months_keep - 1)
        mo[sid] = [[y, m, v] for y, m, v in rs if _ym_index(y, m) >= lo]
    qu: dict[str, list] = {}
    pe: dict[str, dict[str, float]] = {}
    for sid, rows in quarters.items():
        rs = [(str(p), str(t), float(v)) for p, t, v in rows if v is not None and t in NEEDED_TYPES]
        if not rs:
            continue
        latest = max(period_index(p) for p, _, _ in rs)
        keep = [[p, t, v] for p, t, v in rs if period_index(p) > latest - quarters_keep]
        keep.sort(key=lambda r: (period_index(r[0]), r[1]))
        qu[sid] = keep
        periods = {p for p, _, _ in keep}
        got = {p: float(c) for p, c in (px.get(sid) or {}).items() if p in periods}
        if got:
            pe[sid] = got
    return {"schema": FILE_SCHEMA, "monthly": mo, "quarters": qu, "price_at_period_end": pe}


def fundamentals_payload(monthly, quarters, px, data_version: str, **kw) -> dict:
    d = prune_fundamentals(monthly, quarters, px, **kw)
    d["data_version"] = data_version
    return d


def bridge_from_payload(d: dict, pool: Mapping[str, Mapping[str, Any]], tpe_dates: Sequence[str]) -> FundamentalsBridge:
    """與 `replay_io.load_fundamentals` 尾段逐字同：只收池內、`build_stock`、`extend_calendar`；`null` 值＝SQL 端的
    `v is not None` 過濾（匯出端 NaN 經 `dumps` 已寫成 null）。"""
    cal = extend_calendar(list(tpe_dates))
    industry_of = {sid: info.get("industry_category") for sid, info in pool.items()}
    monthly, quarters, px = d.get("monthly") or {}, d.get("quarters") or {}, d.get("price_at_period_end") or {}
    stocks: dict = {}
    for sid in sorted(set(monthly) | set(quarters)):
        if sid not in pool:
            continue
        stocks[sid] = build_stock(sid, industry_of.get(sid),
                                  [(int(y), int(m), float(v)) for y, m, v in monthly.get(sid, []) if v is not None],
                                  [(str(p), str(t), float(v)) for p, t, v in quarters.get(sid, []) if v is not None],
                                  {str(p): float(c) for p, c in (px.get(sid) or {}).items() if c is not None}, cal)
    return FundamentalsBridge(stocks, industry_of)


def load_fundamentals_file(path: Path, pool: Mapping[str, Mapping[str, Any]], tpe_dates: Sequence[str]) -> tuple[dict, FundamentalsBridge]:
    d = _require_schema(read_json(path, what="fundamentals"), path, "fundamentals")
    return d, bridge_from_payload(d, pool, tpe_dates)


def load_calendar_dates(path: Path) -> list[str]:
    d = read_json(path, what="calendar")
    dates = d.get("dates") if isinstance(d, dict) else None
    if not dates:
        raise DailyCoreError(f"日曆 {path} 沒有 dates")
    return [str(x) for x in dates]


# ---------------------------------------------------------------------------
# 重建：原料包 → features（記憶體 FeatureStore）→ WindowCache
def _index_close(b: RS.DayBundle) -> dict[str, float]:
    """＝`feed.load_index` 的當日切片：close 為 None 的市場不出現。"""
    return {m: float(v["close"]) for m, v in b.index.items() if v and v.get("close") is not None}


def _price_rows(b: RS.DayBundle) -> list[tuple]:
    """＝`feed.iter_days` 的列形狀 `(date, stock_id, close, Trading_Volume, Trading_money)`（原料包只含池內檔）。"""
    return [(b.tpe_date, sid, r.get("close"), r.get("Trading_Volume"), r.get("amount")) for sid, r in b.stocks.items()]


def rebuild_from_bundles(bundles: Sequence[tuple[str, Path]], pool: Mapping[str, dict],
                         factors: Mapping[str, tuple[list[str], list[float]]], *, data_version: str, window: int,
                         expect_pool_at: str | None = None, expect_pool: frozenset[str] | None = None,
                         features: FeatureStore | None = None) -> tuple[RS.WindowCache, AdvTracker, dict[str, Any]]:
    """依日序 ingest 全部 `bundles`（升冪、不可重複）。

    兩個 `AdvTracker`，各餵各的、**不可混**（參考路徑本來就是兩個獨立 tracker）：
    - `adv_feat`：餵 `feed.day_records` 的成交值（有成交即收）＝`scan_features.py` 的 tracker，供 features 的 `P_cs` 池；
    - `adv_score`：餵 `WindowCache.ingest` 的 `today_amounts`（所屬市場有指數列且 amount 非 None）＝`replay_step.step` 內
      `cross.adv.push_day(T, wc.today_amounts)` 的口徑。**`expect_pool` 只與 `adv_score` 比**——`expect_pool_at`＝T 時，
      T 的 `adv_score.eligible()`（只吃到 T−1）必須等於 `expect_pool`（`CrossDayState.adv.eligible()`），否則 `DailyCoreError`。
      拿 `adv_feat` 比會在「某市場缺指數列」或 `amount` 為 None 的日子誤報（2026-09-14 驗收指出）。
    `features` 給定時逐日 features 寫進它（呼叫端持有、可讀回比對）；省略則用記憶體 FeatureStore、結束即丟。"""
    wc = RS.WindowCache(pool, factors, window=window)
    scanner, adv_feat, adv_score = DailyScanner(), AdvTracker(), AdvTracker()
    fs = features if features is not None else FeatureStore(Path(":memory:"))
    diag: dict[str, Any] = {"n_bundles": len(bundles), "first": bundles[0][0] if bundles else None,
                            "last": bundles[-1][0] if bundles else None, "index_missing_days": 0}
    last = None
    try:
        for d, path in bundles:
            if last is not None and d <= last:
                raise DailyCoreError(f"原料包日期未嚴格升冪：{d} ≤ {last}")
            last = d
            b = B.read_bundle(path)
            if b.tpe_date != d:
                raise DailyCoreError(f"原料包 {path} 內容日期 {b.tpe_date} ≠ 檔名日期 {d}")
            rank_pool = adv_feat.eligible()                             # PIT：先取（只吃到 d−1）
            if expect_pool_at is not None and d == expect_pool_at:
                got = adv_score.eligible()
                if got != expect_pool:
                    exp = expect_pool or frozenset()
                    raise DailyCoreError(f"{d} 的排名池不一致：原料包重算 {len(got)} 檔 vs 狀態快照 {len(exp)} 檔"
                                         f"（只在重算 {sorted(got - exp)[:5]}／只在快照 {sorted(exp - got)[:5]}）"
                                         f"——種子原料包不足 {adv_score.window}+1 日，或狀態鏈已斷")
            recs, amounts = F.day_records(d, _price_rows(b), pool, factors, rank_pool=rank_pool)
            out = scanner.push_day(d, recs, _index_close(b))
            if out.index_missing:
                diag["index_missing_days"] += 1
            fs.write_day(out, data_version, rank_pool_size=len(rank_pool), adv_tracked=adv_feat.n_tracked, adv_ready=adv_feat.n_ready)
            for m in MARKETS:
                b.breadth[m] = fs.day_breadth(data_version, m, d)
                b.industry[m] = fs.day_industry(data_version, m, d)
                b.p_cs[m] = fs.day_p_cs(data_version, m, d)
            wc.ingest(b)
            adv_feat.push_day(d, amounts)                               # PIT：後推
            adv_score.push_day(d, wc.today_amounts)
    finally:
        if features is None:
            fs.close()
    if wc.last_date is None:
        raise DailyCoreError("沒有任何原料包可重建")
    return wc, adv_score, diag


# ---------------------------------------------------------------------------
# 計分＋落地
def scores_payload(res: ST.StepResult, *, data_version: str, params_sha: str) -> dict:
    rows = [{"model_version": mv, **flat} for mv, flat in res.all_rows()]
    rows.sort(key=lambda r: (str(r.get("market")), str(r.get("stock_id")), str(r.get("horizon")), str(r["model_version"])))
    return {"schema": FILE_SCHEMA, "tpe_date": res.tpe_date, "data_version": data_version, "text_version": TEXT_VERSION,
            "params_sha": params_sha, "rows": rows, "diag": dict(res.diag)}


def scores_path(root: Path, T: str) -> Path:
    return Path(root) / SCORES_DIR / f"{T}.json"


def pending_dates(root: Path, cross: RS.CrossDayState) -> list[tuple[str, Path]]:
    """狀態快照之後、已有原料包但尚未計分的日子（升冪）。"""
    last = cross.last_date or ""
    return [(d, p) for d, p in B.list_bundles(Path(root)) if d > last]


def run_offline(root: Path, T: str | None = None, *, window: int = RS.WINDOW_N, fundamentals: bool = True) -> dict[str, Any]:
    """對 `root` 內狀態快照之後的原料包逐日計分到 `T`（省略＝全部待計分日）。每日：重建（ingest ≤ 該日全部原料包）→
    `step` → 寫 `data/scores/<日>.json` → 覆寫 `data/state/cross.json`。回傳摘要（各日列數、視窗診斷）。"""
    root = Path(root)
    _, pool = load_pool_file(root / POOL_FILE)
    _, factors, fstat = load_factors_file(root / FACTORS_FILE)
    cross = load_state(root / STATE_FILE)
    dv = str(cross.meta.get("data_version") or "")
    if not dv:
        raise DailyCoreError(f"狀態快照 {root / STATE_FILE} 的 meta 沒有 data_version（種子須由 export_seed 產出）")
    ps = {m: build_params(m) for m in MARKETS}
    mv = {m: ps[m].model_version() for m in MARKETS}
    params = build_params_payload(mv, window, cross.adv, fundamentals=fundamentals)
    sha = params_fingerprint(params)
    check_snapshot_meta(cross, window=window, params_sha=sha, path=root / STATE_FILE)
    pend = pending_dates(root, cross)
    if T is not None:
        if T not in {d for d, _ in pend}:
            raise DailyCoreError(f"{T} 不是待計分日（快照 last_date={cross.last_date}，待計分={[d for d, _ in pend]}）")
        pend = [(d, p) for d, p in pend if d <= T]
    if not pend:
        return {"data_version": dv, "days": [], "note": f"快照 last_date={cross.last_date}，沒有待計分的原料包"}
    provider = None
    if fundamentals:
        cal = load_calendar_dates(root / CALENDAR_TPE_FILE)
        _, bridge = load_fundamentals_file(root / FUND_FILE, pool, cal)
        provider = bridge.provider()
    all_bundles = B.list_bundles(root)
    days: list[dict[str, Any]] = []
    for d, _ in pend:
        held = [(x, p) for x, p in all_bundles if x <= d]
        wc, adv, rdiag = rebuild_from_bundles(held, pool, factors, data_version=dv, window=window,
                                              expect_pool_at=d, expect_pool=cross.adv.eligible())
        res = ST.step(d, wc, cross, ps, data_version=dv, text_version=TEXT_VERSION, model_version=mv, fundamentals=provider)
        out = write_json(scores_path(root, d), scores_payload(res, data_version=dv, params_sha=sha))
        cross.meta = {**cross.meta, "window": int(window), "params_sha": sha, "data_version": dv}
        save_state(root / STATE_FILE, cross)
        days.append({"date": d, "rows": len(res.all_rows()), "scores_file": str(out), "rebuild": rdiag,
                     "elapsed_ms": res.diag.get("elapsed_ms")})
    return {"data_version": dv, "days": days, "factors": fstat, "params_sha": sha}
