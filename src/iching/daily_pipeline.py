"""每日班**流程層**（D-2b）：`Fetcher` → 原料包／waiting → pool／factors／fundamentals／日曆增量 → `daily_core.run_offline`。
只讀寫 repo 內檔案；網路全在 `daily_fetch`，計分全在 `daily_core`。設計正本 `docs/P2-DAILY-PLAN.md` §7.4.1。

一次執行可補跑多日（狀態 `last_date` 之後的每個交易日；超過 `max_days` 只跑前 N 日、其餘留下次，summary 的 `remaining` 列出）；任一日核心資料未齊 → 寫
`runs/collect/<d>-waiting.json` 並停止（之後的日子不處理，rc 由呼叫端決定＝0）。全部產出由 workflow 一個 commit 收（原子性）。
**未齊時 `data/pool.json` 仍可能已改寫**（`update_pool` 在 `fetch_day` 之前，讓新入池檔當日即進原料包；pool 是全域檔、
下次一樣算得出，故不回滾）——waiting 那次 commit 可能含 pool.json＋waiting 檔兩者。

**新入池檔側檔（§7.7 甲）**：原料包寫完後 `daily_core.load_bundles` 一次載入全部持有包（偵測與重建共用），`fetch_entrants`
對候選（現行池內、首次出現晚於最舊包、無側檔）逐檔 5 次 API 抓 `[d − entrants_window, d − 1]` 寫 `data/entrants/<sid>.json.gz`；
失敗只記 warnings。`prune_bundles` 末尾刪 `to` 早於最舊持有包的側檔。
"""
from __future__ import annotations

import datetime as dt
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from . import bundle_io as B
from . import calendar as CAL
from . import daily_core as DC
from . import replay_state as RS
from .daily_fetch import DayFetch, Fetcher, pool_rows_from_info
from .fundamentals import NEEDED_TYPES
from .run_common import load_state

CALENDAR_US_FILE = "data/calendar_us.json"


class DailyPipelineError(RuntimeError):
    pass


def waiting_path(root: Path, d: str) -> Path:
    return Path(root) / B.BUNDLE_DIR / f"{d}-waiting.json"


def write_waiting(root: Path, d: str, missing: Sequence[str], *, now: dt.datetime | None = None) -> Path:
    now = now or dt.datetime.now(dt.timezone(dt.timedelta(hours=8)))
    return DC.write_json(waiting_path(root, d), {"schema": DC.FILE_SCHEMA, "date": d, "missing": list(missing),
                                                 "at": now.isoformat(timespec="seconds")})


def clear_waiting(root: Path, d: str) -> None:
    p = waiting_path(root, d)
    if p.exists():
        p.unlink()


def last_dated(root: Path) -> tuple[str | None, str | None]:
    """最近一份原料包起往回找，美股／匯率序列各自的最後日期（增量抓取的游標）。"""
    last_us = last_fx = None
    for _, p in reversed(B.list_bundles(Path(root))):
        b = B.read_bundle(p)
        if last_us is None and b.us:
            last_us = str(b.us[-1][0])
        if last_fx is None and b.fx:
            last_fx = str(b.fx[-1][0])
        if last_us is not None and last_fx is not None:
            break
    return last_us, last_fx


# ---------------------------------------------------------------------------
# 增量更新（內容不變不寫檔）
def _write_if_changed(path: Path, payload: dict) -> bool:
    new = DC.dumps(payload)
    if path.exists() and path.read_text(encoding="utf-8") == new:
        return False
    DC.write_json(path, payload)
    return True


POOL_VOLATILE_KEYS = ("date", "n_rows", "same_date_multi")   # TaiwanStockInfo 的 date 每天＝抓取日，不是池的變動


def _pool_signature(pool: Mapping[str, Mapping[str, Any]]) -> dict:
    return {sid: {k: v for k, v in info.items() if k not in POOL_VOLATILE_KEYS} for sid, info in pool.items()}


def update_pool(root: Path, info_rows: Iterable[Mapping[str, Any]], data_version: str) -> tuple[bool, dict[str, dict]]:
    """只在**導出的池**（成員／type／industry_category／stock_name）變動時改寫 `data/pool.json`；`date` 這種每天都變的欄不算
    （run #3 實測：3,313 列只因 `date` 09-11→09-14 全部改寫，池成員零變動）。未變時沿用既有檔與其池。"""
    path = Path(root) / DC.POOL_FILE
    payload = DC.pool_payload(pool_rows_from_info(info_rows), data_version)
    pool = DC.pool_from_payload(payload)                       # 先驗能解出池，再落檔
    if path.exists():
        try:
            _, old_pool = DC.load_pool_file(path)
        except (DC.DailyCoreError, KeyError, TypeError):           # 舊檔壞掉（含 schema 對但缺 rows）→ 改寫新檔
            old_pool = None
        if old_pool is not None and _pool_signature(old_pool) == _pool_signature(pool):
            return False, old_pool
    DC.write_json(path, payload)
    return True, pool


def update_factors(root: Path, new_rows: Iterable[Sequence[Any]], data_version: str) -> int:
    """新 (stock_id, date) 追加；既有列不動（keep-first 語意與 `feed.load_factors` 同）。回追加筆數。"""
    path = Path(root) / DC.FACTORS_FILE
    d, _, _ = DC.load_factors_file(path)
    rows = list(d["rows"])
    seen = {(str(r[0]), str(r[1])) for r in rows}
    added = 0
    for r in sorted(new_rows, key=lambda r: (str(r[0]), str(r[1]))):
        key = (str(r[0]), str(r[1]))
        if key in seen or r[1] is None:
            continue
        rows.append([key[0], key[1], r[2], r[3]])
        seen.add(key)
        added += 1
    if added:
        DC.write_json(path, {"schema": DC.FILE_SCHEMA, "data_version": data_version, "rows": rows})
    return added


def price_at_period_end_from_bundles(root: Path, periods: Iterable[str]) -> dict[str, dict[str, float]]:
    """＝`replay_io.load_fundamentals` 的期末收盤規則：全市場 ≤P 最近原料包日、該檔 close>0 的**原始**收盤。"""
    files = B.list_bundles(Path(root))
    out: dict[str, dict[str, float]] = {}
    for p in sorted(set(periods)):
        cand = [(d, path) for d, path in files if d <= p]
        if not cand:
            continue
        b = B.read_bundle(cand[-1][1])
        for sid, r in b.stocks.items():
            c = r.get("close")
            if c is not None and float(c) > 0:
                out.setdefault(sid, {})[p] = float(c)
    return out


def update_fundamentals(root: Path, monthly_rows: Iterable[Mapping[str, Any]], quarter_rows: Iterable[Mapping[str, Any]],
                        data_version: str) -> dict[str, int]:
    """月營收 (sid,y,m)／季報 (sid,period,type) 後者覆蓋；新期別的期末收盤由原料包算；再 `prune_fundamentals`。"""
    path = Path(root) / DC.FUND_FILE
    cur = DC._require_schema(DC.read_json(path, what="fundamentals"), path, "fundamentals") if path.exists() else {}
    mo: dict[str, dict[tuple[int, int], float]] = {}
    for sid, rows in (cur.get("monthly") or {}).items():
        mo[sid] = {(int(y), int(m)): v for y, m, v in rows}
    n_mo = 0
    for r in monthly_rows:
        sid, y, m, v = str(r.get("stock_id")), r.get("revenue_year"), r.get("revenue_month"), r.get("revenue")
        if not sid or y is None or m is None or v is None:
            continue
        mo.setdefault(sid, {})[(int(y), int(m))] = DC.num_or_none(v)
        n_mo += 1
    qu: dict[str, dict[tuple[str, str], float]] = {}
    for sid, rows in (cur.get("quarters") or {}).items():
        qu[sid] = {(str(p), str(t)): v for p, t, v in rows}
    n_qu = 0
    for r in quarter_rows:
        sid, p, t, v = str(r.get("stock_id")), r.get("date"), r.get("type"), r.get("value")
        if not sid or not p or t not in NEEDED_TYPES or v is None:
            continue
        qu.setdefault(sid, {})[(str(p), str(t))] = DC.num_or_none(v)
        n_qu += 1
    px: dict[str, dict[str, float]] = {sid: dict(m) for sid, m in (cur.get("price_at_period_end") or {}).items()}
    # 缺期末收盤以 **(檔, 期別)** 計，不是以期別計——同一期別 A 先申報、B 隔日申報，B 也要補到（2026-09-14 驗收抓到）
    need_pairs = {(sid, p) for sid, m in qu.items() for (p, _) in m if p not in px.get(sid, {})}
    new_periods = {p for _, p in need_pairs}
    if need_pairs:
        got = price_at_period_end_from_bundles(root, new_periods)
        for sid, p in need_pairs:
            c = got.get(sid, {}).get(p)
            if c is not None:
                px.setdefault(sid, {})[p] = c
    payload = DC.fundamentals_payload({sid: [[y, m, v] for (y, m), v in sorted(d.items())] for sid, d in mo.items()},
                                      {sid: [[p, t, v] for (p, t), v in sorted(d.items())] for sid, d in qu.items()},
                                      px, data_version)
    changed = _write_if_changed(path, payload)
    return {"monthly_rows": n_mo, "quarter_rows": n_qu, "new_periods": len(new_periods), "px_pairs": len(need_pairs), "changed": int(changed)}


def append_calendar(root: Path, name: str, new_dates: Iterable[str], data_version: str) -> int:
    path = Path(root) / (DC.CALENDAR_TPE_FILE if name == "tpe" else CALENDAR_US_FILE)
    dates = DC.load_calendar_dates(path) if path.exists() else []
    add = sorted({str(d) for d in new_dates} - set(dates))
    if not add:
        return 0
    merged = sorted(set(dates) | set(add))
    CAL.write_calendar_json(path, CAL.calendar_payload(name, merged, data_version))
    return len(add)


# ---------------------------------------------------------------------------
BUNDLE_KEEP = 480          # 保留最近 480 個交易日（≈2 年）：ring 需 320 個有成交列，留 160 日停牌／缺口餘裕；重建 ≈ 480×81 ms ≈ 40 s


def prune_bundles(root: Path, *, keep: int = BUNDLE_KEEP, window: int = RS.WINDOW_N) -> dict[str, Any]:
    """只留最近 `keep` 份原料包。**被刪的那些包裡的美股／匯率列不能跟著消失**（§7.3 約束①：第一份包承載整段序列，之後只帶增量）
    ——把「≤ 新最舊包日期」的美股／匯率序列最後 `window` 個日期併進新最舊的那一份再改寫它，`WindowCache` 的兩條 ring 才與未修剪時相同。
    改寫後那一份與回補層 `read_day` 的位元組不同（D-3 原料包比對對它只比美股／匯率以外的欄）。
    末尾順帶刪掉 `to` 早於最舊持有包日期的 entrants 側檔（§7.7 第 3 點；之後任何持有包都不缺它），回 `entrants_deleted`。"""
    files = B.list_bundles(Path(root))
    if len(files) <= keep:
        return {"deleted": 0, "kept": len(files), "first": files[0][0] if files else None,
                "entrants_deleted": DC.prune_entrants(Path(root), files[0][0] if files else None)}
    drop, first_d, first_p = files[:-keep], files[-keep][0], files[-keep][1]
    us: dict[str, tuple] = {}
    fx: dict[str, tuple] = {}
    for _, p in drop:
        b = B.read_bundle(p)
        for row in b.us:
            us[str(row[0])] = tuple(row)
        for row in b.fx:
            fx[str(row[0])] = tuple(row)
    nb = B.read_bundle(first_p)
    for row in nb.us:
        us[str(row[0])] = tuple(row)
    for row in nb.fx:
        fx[str(row[0])] = tuple(row)
    nb.us = [us[d] for d in sorted(us) if d <= first_d][-window:]
    nb.fx = [fx[d] for d in sorted(fx) if d <= first_d][-window:]
    B.write_bundle(Path(root), nb)
    for _, p in drop:
        p.unlink()
    return {"deleted": len(drop), "kept": keep, "first": first_d, "first_us": len(nb.us), "first_fx": len(nb.fx),
            "entrants_deleted": DC.prune_entrants(Path(root), first_d)}


def fetch_entrants(root: Path, fetcher: Fetcher, *, d: str, pool: Mapping[str, Mapping[str, Any]],
                   bundles: Sequence[tuple[str, RS.DayBundle]], data_version: str, window: int) -> dict[str, Any]:
    """§7.7 第 2 點：對 `entrant_candidates`（現行池內、首次出現晚於最舊持有包、無側檔）逐檔 5 次 API 抓
    `[d − window 交易日, d − 1]`（repo 日曆）的歷史列，寫 `data/entrants/<sid>.json.gz`（空 `days` 也落檔）。
    抓取失敗只記 `warnings`、不寫側檔、不擋當日計分（下一班自然重試）。回 `{candidates, written, warnings, calls}`。"""
    have = [sid for sid, _ in DC.list_entrants(root)]
    cands = DC.entrant_candidates(pool, bundles, have)
    out: dict[str, Any] = {"candidates": cands, "written": [], "warnings": [], "calls": 0}
    if not cands:
        return out
    cal = DC.load_calendar_dates(Path(root) / DC.CALENDAR_TPE_FILE)
    rng = DC.entrant_range(cal, d, window)
    for sid in cands:
        n0 = fetcher.n_calls
        days: dict[str, dict[str, Any]] = {}
        try:
            if rng is not None:
                days = fetcher.fetch_entrant(sid, pool[sid], rng[0], rng[1])
        except Exception as e:                                          # noqa: BLE001 — 側檔是附帶工作，任何失敗都不得擋當日計分
            out["warnings"].append(f"entrant:{sid}:{type(e).__name__}:{str(e)[:120]}")
            out["calls"] += fetcher.n_calls - n0
            continue
        out["calls"] += fetcher.n_calls - n0
        frm, to = rng if rng is not None else (d, d)
        DC.write_entrant(root, DC.Entrant(stock_id=sid, data_version=data_version, frm=frm, to=to, days=days))
        out["written"].append(sid)
    return out


def run_pipeline(root: Path, fetcher: Fetcher, *, upto: str, window: int, max_days: int = 5, fundamentals: bool = True,
                 entrants_window: int | None = None, log=print) -> dict[str, Any]:
    """`entrants_window`：新入池檔側檔的回看交易日數，預設＝`window`（§7.7：`[T − window, T − 1]`）。"""
    root = Path(root)
    ew = int(entrants_window) if entrants_window is not None else int(window)
    cross = load_state(root / DC.STATE_FILE)
    dv = str(cross.meta.get("data_version") or "")
    if not cross.last_date or not dv:
        raise DailyPipelineError(f"狀態快照 {root / DC.STATE_FILE} 缺 last_date 或 meta.data_version")
    days = fetcher.trading_days_since(cross.last_date, upto)
    summary: dict[str, Any] = {"data_version": dv, "last_date": cross.last_date, "upto": upto, "pending": days, "done": [], "status": "noop"}
    if not days:
        weekday = dt.date.fromisoformat(upto).weekday() < 5
        log(f"[daily] 快照 last_date={cross.last_date}，{upto} 之前沒有新的交易日（TAIEX 無列）→ no-op"
            + ("；注意 upto 是平日：可能是國定假日，也可能是 TAIEX 尚未落地（23:30 補叫／隔日 catch-up 會自癒）" if weekday else ""))
        summary["weekday_no_taiex"] = weekday
        return summary
    remaining: list[str] = []
    if len(days) > max_days:                                     # 只跑前 N 日、其餘留給下一次（2026-09-14 首次 dispatch 教訓：拒跑會卡住補跑）
        days, remaining = days[:max_days], days[max_days:]
        log(f"[daily] 待補 {len(days) + len(remaining)} 個交易日，本次只跑前 {max_days} 日（{days[0]}～{days[-1]}），"
            f"其餘 {len(remaining)} 日（{remaining[0]} 起）留下次")
    summary["pending"] = days
    summary["remaining"] = remaining
    for d in days:
        changed, pool = update_pool(root, fetcher.stock_info(), dv)
        last_us, last_fx = last_dated(root)
        df: DayFetch = fetcher.fetch_day(d, pool, last_us=last_us, last_fx=last_fx)
        log(f"[daily] {d} 原始列數 {df.counts} 警示 {df.warnings or '無'}")
        if df.missing:
            write_waiting(root, d, df.missing)
            log(f"[daily] {d} 核心資料未齊：{df.missing} → 寫 {waiting_path(root, d).name}，本次停止"
                f"（pool.json{'已依今日 TaiwanStockInfo 改寫' if changed else '未變'}）")
            summary.update(status="waiting", waiting_date=d, missing=df.missing, warnings=df.warnings, counts=df.counts,
                           pool_changed=changed, n_calls=fetcher.n_calls)
            return summary
        bp = B.write_bundle(root, df.bundle)
        clear_waiting(root, d)
        n_fac = update_factors(root, df.extras.get("dividend", []), dv)
        fstat = update_fundamentals(root, df.extras.get("month_revenue", []), df.extras.get("financial_statements", []), dv)
        n_cal = append_calendar(root, "tpe", [d], dv)
        n_us = append_calendar(root, "us", [x[0] for x in df.bundle.us], dv)
        # 原料包全部載入一次（含剛寫的 d）：entrants 偵測與重建共用，不多讀任何一份包（§7.7 第 2 點）
        bundles = DC.load_bundles(root)
        ent = fetch_entrants(root, fetcher, d=d, pool=pool, bundles=bundles, data_version=dv, window=ew)
        log(f"[daily] {d} entrants={len(ent['candidates'])} calls={ent['calls']}"
            + (f" 寫側檔 {ent['written']}" if ent["written"] else "")
            + (f" 失敗 {ent['warnings']}" if ent["warnings"] else ""))
        res = DC.run_offline(root, d, window=window, fundamentals=fundamentals, bundles=bundles)
        day = res["days"][0]
        log(f"[daily] {d} 原料包 {bp.stat().st_size / 1024:.1f} KB（{len(df.bundle.stocks)} 檔、{df.n_calls} 次呼叫）"
            f" pool{'改寫' if changed else '不變'} 除權息+{n_fac} 基本面 {fstat} 日曆+{n_cal}/us+{n_us}"
            f" → 分數 {day['rows']} 列 step {day['elapsed_ms']} ms")
        summary["done"].append({"date": d, "rows": day["rows"], "n_calls": df.n_calls + ent["calls"], "pool_changed": changed,
                                "factors_added": n_fac, "fundamentals": fstat, "official_errors": df.official_errors,
                                "warnings": df.warnings + ent["warnings"], "counts": df.counts,
                                "entrants": {"candidates": ent["candidates"], "written": ent["written"], "calls": ent["calls"]}})
    summary["prune"] = prune_bundles(root, window=window)
    if summary["prune"]["deleted"] or summary["prune"].get("entrants_deleted"):
        log(f"[daily] 原料包修剪：刪 {summary['prune']['deleted']} 份，留 {summary['prune']['kept']}（最舊 {summary['prune']['first']}）"
            f"；entrants 側檔刪 {summary['prune'].get('entrants_deleted', 0)}")
    summary.update(status="ok", n_calls=fetcher.n_calls)
    return summary
