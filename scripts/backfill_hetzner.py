#!/usr/bin/env python3
"""股市易經 P2 歷史回補（在 Hetzner 執行；token 不離開 Hetzner）。

子命令：
  plan               免 token 免網路：列出每個資料集的請求計畫與總請求數，對照 §B1.9
  run                實抓；可 --dataset／--group／--from／--to 分段，隨時 Ctrl-C，重跑跳過已 covered
  taiex-open-check   裁定 4：證交所 MI_5MINS_HIST 官方指數開盤 vs FinMind TaiwanStockPrice/TAIEX open 逐日比對
  report             coverage 統計、每年 PIT 池檔數、失敗清單
  calendar           只由 DB 重生 data/calendar_tpe.json／calendar_us.json

規範：
  - 所有日期顯式 Asia/Taipei（config.taipei_now），禁用裸 date.today()。
  - 失敗絕不寫進 coverage（store.record_failure 只進 failures）；**交易日曆上的全市場切片回空**也記 failures
    （kind=empty_on_trading_day）、不寫 coverage，重跑會再試（2026-09-09 驗收更正）。
  - 一次 run 一個 data_version（fm-YYYYMMDD-<批次>），寫進每筆 coverage／原始列。
  - SQLite 落在 <repo>/cache/（不進 git），PRAGMA 依 P1-B3 §B3.2。
  - 記憶體：ru_maxrss 超過 1.5 GiB 立即中止（§B3.2）。
用法見 docs/BACKFILL-RUNBOOK.md。
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import logging
import resource
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "src"))

from iching import calendar as cal  # noqa: E402
from iching import config as C  # noqa: E402
from iching import plan as P  # noqa: E402
from iching import twse as T  # noqa: E402
from iching.fm import FinMind, PermissionRequired, QuotaExceeded, TransientError, redact  # noqa: E402
from iching.store import Store, open_stores  # noqa: E402
from iching.universe import pit_pool, pool_from_info  # noqa: E402

log = logging.getLogger("backfill")

# 官方端點的 dataset key（原始 JSON 全文落地）
OFFICIAL_PARAMS = {
    "twse_bfi82u": lambda d: {"dayDate": d.replace("-", ""), "type": "day", "response": "json"},
    "tpex_inst_summary": lambda d: {"type": "Daily", "date": d.replace("-", "/"), "response": "json"},
    # official_month：key=YYYYMM（taiwan-flows src/totals.py fetch_fmtqik_month／fetch_otc_turnover_month 的參數形狀）
    "twse_fmtqik": lambda m: {"date": f"{m}01", "response": "json"},
    "tpex_trading_index": lambda m: {"date": f"{m[:4]}/{m[4:6]}/01", "response": "json"},
}
EMPTY_ON_TRADING_DAY = "empty_on_trading_day"
MI5_DATASET_KEY = "twse_mi5mins_hist"   # taiex-open-check 落地用（market.db）


# ---------------------------------------------------------------------------
# 共用
# ---------------------------------------------------------------------------
def setup_logging(cache_dir: Path, data_version: str, quiet: bool = False) -> None:
    fmt = "%(asctime)s %(levelname)s %(message)s"
    handlers: list[logging.Handler] = [logging.StreamHandler(sys.stdout)]
    try:
        (cache_dir / "logs").mkdir(parents=True, exist_ok=True)
        handlers.append(logging.FileHandler(cache_dir / "logs" / f"backfill-{data_version}.log", encoding="utf-8"))
    except OSError:
        pass
    logging.basicConfig(level=logging.WARNING if quiet else logging.INFO, format=fmt, handlers=handlers, force=True)


def check_memory() -> None:
    rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * 1024  # Linux: KB
    if rss > C.MEMORY_LIMIT_BYTES:
        raise MemoryError(f"RSS 峰值 {rss / 1024**3:.2f} GiB 超過上限 {C.MEMORY_LIMIT_BYTES / 1024**3:.1f} GiB（P1-B3 §B3.2），中止")


def tpe_calendar_from_store(prices: Store, start: str | None = None, end: str | None = None,
                            data_version: str | None = None) -> list[str]:
    """台北交易日曆＝raw_index_price 內 TAIEX 有列的日期；帶 data_version 時只認該版本落地的列。"""
    if data_version is None:
        dates = cal.build_calendar(prices.distinct_dates("raw_index_price", "TAIEX"))
    else:
        rows = prices.fetch_rows("raw_index_price", "stock_id='TAIEX' AND data_version=?", (data_version,), cols="date")
        dates = cal.build_calendar(r["date"] for r in rows)
    return P.clip_dates(dates, start or "0000-00-00", end or "9999-99-99")


def us_calendar_from_store(market: Store) -> list[str]:
    return cal.build_calendar(market.distinct_dates("raw_us_index", "^GSPC"))


def pool_ids_from_store(universe: Store) -> list[str]:
    rows = universe.fetch_rows("raw_stock_info")
    return sorted(pool_from_info([dict(r) for r in rows]))


def parse_key(strategy: str, key: str) -> dict:
    """coverage key → FinMind 參數（與 plan.keys_for 的格式互為反函式）。"""
    if strategy in ("daily_slice", "official"):
        return {"start_date": key, "end_date": key}
    if strategy == "official_month":
        return {"month": key}
    if strategy == "range_slice":
        a, b = key.split("~", 1)
        return {"start_date": a, "end_date": b}
    if strategy in ("per_id", "per_stock"):
        i, rng = key.split(":", 1)
        a, b = rng.split("~", 1)
        return {"data_id": i, "start_date": a, "end_date": b}
    if strategy == "single":
        return {}
    raise ValueError(strategy)


def write_calendars(stores: dict[str, Store], data_version: str, data_dir: Path, cache_dir: Path) -> dict[str, dict]:
    """完整日曆才進 data/（git）；部分日曆寫 cache/calendar_partial_*.json（見 calendar.write_calendars）。"""
    tpe = tpe_calendar_from_store(stores["prices"])
    us = us_calendar_from_store(stores["market"])
    res = cal.write_calendars(tpe, us, data_version, data_dir, cache_dir)
    for name, r in res.items():
        if r["path"] is None:
            log.warning("calendar_%s：DB 尚無資料，未寫出", name)
        elif r["full"]:
            log.info("寫出完整日曆 %s（%d 日）", r["path"], r["n"])
        else:
            log.warning("calendar_%s 只涵蓋部分期間（%d 日）→ 寫到 %s，**不進 data/**；"
                        "落地完整 %s~%s 的指數後才會寫 data/calendar_%s.json",
                        name, r["n"], r["path"], C.PRICE_WARMUP_START, C.DATA_END, name)
    return res


# ---------------------------------------------------------------------------
# plan
# ---------------------------------------------------------------------------
def select_keys(args) -> tuple[list[str] | None, tuple[str, ...]]:
    only = list(args.dataset) if getattr(args, "dataset", None) else None
    groups = tuple(args.group) if getattr(args, "group", None) else ("core",)
    if only:
        bad = [k for k in only if k not in C.DATASET_BY_KEY]
        if bad:
            sys.exit(f"未知資料集 key：{bad}；可用：{list(C.DATASET_BY_KEY)}")
    return only, groups


def cmd_plan(args) -> int:
    only, groups = select_keys(args)
    tpe_dates = cal.load_calendar_json(REPO / "data" / "calendar_tpe.json") or None
    if tpe_dates and not P.calendar_covers(tpe_dates, C.PRICE_WARMUP_START, C.DATA_END):
        print(f"# ⚠ data/calendar_tpe.json 只涵蓋 {tpe_dates[0]}~{tpe_dates[-1]}，未涵蓋 {C.PRICE_WARMUP_START}~{C.DATA_END}："
              f"全市場切片改以平日數估計（該檔可能是部分回補的產物，勿當正式日曆）")
    stock_ids = None
    upath = Path(args.cache_dir) / "universe.db"
    if upath.exists():
        with Store(upath) as u:
            ids = pool_ids_from_store(u)
            stock_ids = ids or None
    overrides = dict(kv.split("=", 1) for kv in (args.strategy or []))
    plans = P.build_plan(tpe_dates=tpe_dates, stock_ids=stock_ids, groups=groups, only=only,
                         start=args.start, end=args.end, strategy_override=overrides)
    print(f"# 請求計畫（{'交易日曆 data/calendar_tpe.json' if tpe_dates else '尚無交易日曆 → 平日數為上限估計'}；"
          f"{'universe.db 個股池' if stock_ids else f'個股池以裁定 {C.POOL_SIZE_RULING} 檔估計'}）")
    print(f"# 區間：價格類自 {C.PRICE_WARMUP_START}、基本面類自 {C.FUND_WARMUP_START}，截止 {C.DATA_END}"
          + (f"；本次限 {args.start or '…'} ~ {args.end or '…'}" if (args.start or args.end) else ""))
    print(P.format_plan(plans, args.interval))
    if args.show_keys:
        for p in plans:
            print(f"\n[{p.key}] {len(p.keys)} keys: {p.keys[:5]} … {p.keys[-2:] if len(p.keys) > 5 else ''}")
    return 0


# ---------------------------------------------------------------------------
# run
# ---------------------------------------------------------------------------
def fetch_finmind(spec: C.DatasetSpec, strategy: str, key: str, fm: FinMind) -> list[dict]:
    return fm.get(spec.dataset, **parse_key(strategy, key))


def land_finmind(spec: C.DatasetSpec, strategy: str, key: str, fm: FinMind, store: Store, dv: str) -> tuple[str, int]:
    """抓一鍵並落地。回 (status, n_rows)；失敗以例外傳出（呼叫端記 failure）。"""
    rows = fetch_finmind(spec, strategy, key, fm)
    n = store.record_success(spec.key, spec.table, key, rows, dv, spec.dataset, spec.index_cols)
    return ("ok" if n else "empty"), n


def land_official(spec: C.DatasetSpec, key: str, oc: T.OfficialClient, store: Store, dv: str) -> tuple[str, int]:
    params = OFFICIAL_PARAMS[spec.key](key)
    code, body, text = oc.get(spec.dataset, params)
    if body is None:
        raise TransientError(f"{spec.key} {key}: HTTP {code} 非 JSON（可能為 WAF 封鎖頁）：{text[:60]!r}")
    stat = body.get("stat") if isinstance(body, dict) else None
    row = {"date": key, "http_status": code, "stat": stat, "body": json.dumps(body, ensure_ascii=False)}
    n = store.record_success(spec.key, spec.table, key, [row], dv, spec.dataset, spec.index_cols)
    return "ok", n


def run_dataset(spec: C.DatasetSpec, strategy: str, stores: dict[str, Store], fm: FinMind | None,
                oc: T.OfficialClient | None, dv: str, args) -> dict:
    store = stores[spec.db]
    stats = {"key": spec.key, "strategy": strategy, "planned": 0, "skipped": 0, "ok": 0, "empty": 0,
             "failed": 0, "aborted": None}
    tpe_dates = None
    stock_ids = None
    if strategy in ("daily_slice", "official"):
        # 只認**同一 data_version** 落地的 TAIEX 日期：日曆與切片綁同一版本，才能說「日曆上卻空」是異常
        tpe_dates = tpe_calendar_from_store(stores["prices"], data_version=dv)
        want_s, want_e = args.start or spec.start, args.end or spec.end
        if not tpe_dates or not P.calendar_covers(tpe_dates, want_s, want_e):
            span = f"{tpe_dates[0]}~{tpe_dates[-1]}" if tpe_dates else "無"
            stats["aborted"] = (f"台北交易日曆（data_version={dv}）未涵蓋 {want_s}~{want_e}（DB 內 TAIEX 只有 {span}）："
                                f"請先跑 `run --dataset index_price`（同一 --data-version；或本次加同樣的 --from/--to）")
            log.error("[%s] %s", spec.key, stats["aborted"])
            return stats
    if strategy == "per_stock":
        stock_ids = pool_ids_from_store(stores["universe"])
        if not stock_ids:
            stats["aborted"] = "需要個股池：請先跑 `run --dataset stock_info`"
            log.error("[%s] %s", spec.key, stats["aborted"])
            return stats

    keys, basis = P.keys_for(spec, strategy, tpe_dates=tpe_dates, stock_ids=stock_ids, start=args.start, end=args.end)
    covered = store.covered_keys(spec.key, dv)
    pending = [k for k in keys if k not in covered] if not args.force else list(keys)
    stats["planned"] = len(keys)
    stats["skipped"] = len(keys) - len(pending)
    log.info("[%s] %s 策略=%s 鍵數=%d（基準：%s）已涵蓋=%d 待抓=%d", spec.key, spec.dataset if spec.source == "finmind" else spec.source,
             strategy, len(keys), basis, stats["skipped"], len(pending))
    if args.limit:
        pending = pending[: args.limit]
    t0 = time.monotonic()
    tpe_set = set(tpe_dates or ())
    for i, key in enumerate(pending, 1):
        try:
            if spec.source == "finmind":
                assert fm is not None
                rows = fetch_finmind(spec, strategy, key, fm)
                if strategy == "daily_slice" and not rows and key in tpe_set:
                    # 交易日曆（同 data_version）上的日期卻回 200 空陣列：**不寫 coverage**，記 failures 讓重跑再試
                    # （taiwan-stock-news 已知坑 2 的同型：空被記成「已涵蓋」就永遠補不回）
                    store.record_failure(spec.key, key, EMPTY_ON_TRADING_DAY,
                                         f"{spec.dataset} {key}: 交易日曆上但全市場切片回空", dv)
                    stats["failed"] += 1
                    log.warning("[%s] %s 在交易日曆上但全市場切片為空 → failures(%s)，未寫 coverage", spec.key, key, EMPTY_ON_TRADING_DAY)
                    continue
                n = store.record_success(spec.key, spec.table, key, rows, dv, spec.dataset, spec.index_cols)
                status = "ok" if n else "empty"
            else:
                assert oc is not None
                status, n = land_official(spec, key, oc, store, dv)
            stats[status] += 1
        except PermissionRequired as e:
            msg = redact(str(e))
            store.record_failure(spec.key, key, "permission", msg, dv)
            stats["failed"] += 1
            if spec.fallback and strategy != spec.fallback and not args.no_fallback:
                log.warning("[%s] 權限不足（%s）→ 自動改用 %s 策略重跑本資料集", spec.key, msg[:80], spec.fallback)
                sub = run_dataset(spec, spec.fallback, stores, fm, oc, dv, args)
                sub["fallback_from"] = strategy
                return sub
            stats["aborted"] = f"需 Sponsor 或權限不足：{msg[:120]}"
            log.error("[%s] %s；本資料集其餘鍵略過", spec.key, stats["aborted"])
            return stats
        except QuotaExceeded as e:
            store.record_failure(spec.key, key, "quota", redact(str(e)), dv)
            stats["failed"] += 1
            stats["aborted"] = "額度用盡（等待後仍 402/429）；稍後重跑同一指令即可續抓"
            log.error("[%s] %s", spec.key, stats["aborted"])
            raise
        except TransientError as e:
            store.record_failure(spec.key, key, "error", redact(str(e)), dv)
            stats["failed"] += 1
            log.warning("[%s] %s 失敗：%s", spec.key, key, redact(str(e))[:160])
        check_memory()
        if i % args.progress_every == 0 or i == len(pending):
            el = time.monotonic() - t0
            rate = i / el if el > 0 else 0
            eta = (len(pending) - i) / rate / 60 if rate else float("inf")
            log.info("[%s] %d/%d ok=%d empty=%d failed=%d  %.2f req/s  ETA %.0f 分", spec.key, i, len(pending),
                     stats["ok"], stats["empty"], stats["failed"], rate, eta)
    return stats


def resolve_run_list(args) -> list[tuple[C.DatasetSpec, str]]:
    only, groups = select_keys(args)
    overrides = dict(kv.split("=", 1) for kv in (args.strategy or []))
    wanted = [k for k in C.RUN_ORDER if (only and k in only) or (not only and C.DATASET_BY_KEY[k].group in groups)]
    chk = [k for k in wanted if C.DATASET_BY_KEY[k].group == "check"]
    if chk:
        sys.exit(f"{chk} 屬 group=check，只由 `taiex-open-check` 子命令抓取，run 不處理")
    # 自動補上便宜的前置（stock_info／index_price）——只在未被選入時加在最前
    out: list[str] = []
    for k in wanted:
        for dep in C.DATASET_BY_KEY[k].depends:
            if dep in ("stock_info", "index_price") and dep not in out and dep not in wanted:
                out.append(dep)
    out += [k for k in wanted if k not in out]
    return [(C.DATASET_BY_KEY[k], overrides.get(k, C.DATASET_BY_KEY[k].strategy)) for k in out]


def cmd_run(args) -> int:
    dv = C.validate_data_version(args.data_version or C.default_data_version(args.batch))
    cache_dir = Path(args.cache_dir)
    setup_logging(cache_dir, dv, args.quiet)
    log.info("data_version=%s cache_dir=%s interval=%.2fs", dv, cache_dir, args.interval)
    run_list = resolve_run_list(args)
    need_fm = any(s.source == "finmind" for s, _ in run_list)
    need_oc = any(s.source != "finmind" for s, _ in run_list)
    fm = None
    if need_fm:
        fm = FinMind(env_file=Path(args.env_file), min_interval=args.interval, allow_no_token=args.no_token)
        try:
            has = fm.has_token()
        except PermissionRequired as e:
            log.error("%s（放在 %s 或環境變數；本腳本不會印出 token）", e, args.env_file)
            return 2
        if not has:
            log.warning("以 --no-token 執行：只有 tier=free 的資料集會成功，Sponsor 資料集會被記成 permission 失敗")
    oc = T.OfficialClient(interval=C.OFFICIAL_INTERVAL_SEC, tpex_verify=not args.tpex_no_verify) if need_oc else None
    if need_oc and args.tpex_no_verify:
        log.warning("--tpex-no-verify：對 tpex.org.tw 關閉 TLS 驗證（僅在 Hetzner 實際碰到 SSL 異常時使用）")
    stores = open_stores(cache_dir, C.DB_FILES)
    results = []
    rc = 0
    try:
        for spec, strat in run_list:
            results.append(run_dataset(spec, strat, stores, fm, oc, dv, args))
    except QuotaExceeded:
        rc = 3
    except KeyboardInterrupt:
        log.warning("使用者中斷；已落地的鍵皆已 commit，重跑同一指令續抓")
        rc = 130
    except MemoryError as e:
        log.error("%s", e)
        rc = 4
    finally:
        try:
            write_calendars(stores, dv, REPO / "data", cache_dir)
        except Exception as e:  # noqa: BLE001
            log.error("寫交易日曆失敗：%s", e)
        for s in stores.values():
            s.close()
    print("\n== run 摘要 ==")
    for r in results:
        line = (f"{r['key']:<22} 策略={r['strategy']:<12} 計畫={r['planned']:>6} 跳過={r['skipped']:>6} "
                f"ok={r['ok']:>6} empty={r['empty']:>5} failed={r['failed']:>5}")
        if r.get("fallback_from"):
            line += f"  （由 {r['fallback_from']} 退回）"
        if r.get("aborted"):
            line += f"  ✗ {r['aborted']}"
        print(line)
    if fm:
        print(f"FinMind 請求 {fm.n_requests} 次，額度等待 {fm.n_quota_waits} 次")
    return rc


# ---------------------------------------------------------------------------
# taiex-open-check（裁定 4）
# ---------------------------------------------------------------------------
def cmd_taiex_open_check(args) -> int:
    dv = C.validate_data_version(args.data_version or C.default_data_version(args.batch))
    cache_dir = Path(args.cache_dir)
    setup_logging(cache_dir, dv, args.quiet)
    stores = open_stores(cache_dir, ("prices", "market"))
    prices, market = stores["prices"], stores["market"]
    oc = T.OfficialClient(interval=C.OFFICIAL_INTERVAL_SEC)
    months = T.months_between(args.month_from, args.month_to)
    start = f"{args.month_from[:4]}-{args.month_from[4:6]}-01"
    end_month = dt.date(int(args.month_to[:4]), int(args.month_to[4:6]), 1)
    end = (dt.date(end_month.year + (end_month.month == 12), 1 if end_month.month == 12 else end_month.month + 1, 1)
           - dt.timedelta(days=1)).isoformat()
    log.info("taiex-open-check %s ~ %s（%d 個月；TWSE 4 秒節流約 %.0f 分）", start, end, len(months), len(months) * 4 / 60)

    # 1) TWSE 官方（落地 market.db raw_twse_mi5mins_hist，coverage key=YYYYMM）
    twse_rows: list[dict] = []
    tw_fail = []
    for m in months:
        if market.is_covered(MI5_DATASET_KEY, m, dv) and not args.force:
            twse_rows += [dict(r) for r in market.fetch_rows(f"raw_{MI5_DATASET_KEY}", "cov_key=?", (m,))]
            continue
        try:
            rows = oc.index_hist_month(m)
            market.record_success(MI5_DATASET_KEY, f"raw_{MI5_DATASET_KEY}", m, rows, dv, C.TWSE_MI5MINS_HIST, ("date",))
            twse_rows += rows
            log.info("TWSE %s：%d 列", m, len(rows))
        except (T.TwseError, Exception) as e:  # noqa: BLE001
            msg = redact(str(e))
            market.record_failure(MI5_DATASET_KEY, m, "error", msg, dv)
            tw_fail.append((m, msg))
            log.error("TWSE %s 失敗：%s", m, msg[:200])
    if not twse_rows:
        print("TWSE 端一筆都拿不到——不能產出一致率。失敗原因：")
        for m, msg in tw_fail[:5]:
            print(f"  {m}: {msg[:200]}")
        print("（本雲端容器被證交所擋是預期的；請在 Hetzner 執行。）")
        return 5

    # 2) 候選一：FinMind TaiwanStockPrice/TAIEX open（優先讀 prices.db；缺的年度直接抓並落地，鍵格式同 index_price 計畫）
    spec = C.DATASET_BY_KEY["index_price"]
    fm = FinMind(env_file=Path(args.env_file), min_interval=args.interval, allow_no_token=True)
    for a, b in P.chunk_ranges(spec.start, spec.end, "year"):
        if b < start or a > end:
            continue
        key = f"TAIEX:{a}~{b}"
        if prices.is_covered(spec.key, key, dv) and not args.force:
            continue
        try:
            land_finmind(spec, "per_id", key, fm, prices, dv)
        except (PermissionRequired, QuotaExceeded, TransientError) as e:
            prices.record_failure(spec.key, key, "error", redact(str(e)), dv)
            log.error("FinMind TAIEX %s 失敗：%s", key, redact(str(e))[:160])
    fm_rows = [dict(r) for r in prices.fetch_rows("raw_index_price", "stock_id='TAIEX' AND date BETWEEN ? AND ?", (start, end))]

    # 3) 候選二：TaiwanStockKBar TAIEX 09:00 bar 的 close（逐日一請求；落地 market.db raw_taiex_kbar_0900，key=date）
    kspec = C.DATASET_BY_KEY["taiex_kbar_0900"]
    kbar_rows: list[dict] = []
    kbar_note = ""
    if not args.no_kbar:
        tpe_days = tpe_calendar_from_store(prices, start, end)
        pending = [d for d in tpe_days if args.force or not market.is_covered(kspec.key, d, dv)]
        if args.kbar_limit:
            pending = pending[: args.kbar_limit]
        log.info("KBar 09:00：交易日 %d、待抓 %d（0.7s 間隔約 %.0f 分）", len(tpe_days), len(pending), len(pending) * args.interval / 60)
        for i, d in enumerate(pending, 1):
            try:
                rows = fm.get(kspec.dataset, data_id="TAIEX", start_date=d, end_date=d)
                bar = T.kbar_0900_row(rows, d)
                if bar is None:
                    market.record_failure(kspec.key, d, EMPTY_ON_TRADING_DAY, f"TaiwanStockKBar TAIEX {d}: 無 09:00 bar（{len(rows)} 列）", dv)
                else:
                    market.record_success(kspec.key, kspec.table, d, [bar], dv, kspec.dataset, ("date",))
            except PermissionRequired as e:
                market.record_failure(kspec.key, d, "permission", redact(str(e)), dv)
                kbar_note = f"KBar 權限不足（{redact(str(e))[:100]}）→ 第二候選無法取得，只比對候選一"
                log.error("%s", kbar_note)
                break
            except QuotaExceeded as e:
                market.record_failure(kspec.key, d, "quota", redact(str(e)), dv)
                kbar_note = "KBar 額度用盡，稍後重跑同指令續抓"
                log.error("%s", kbar_note)
                break
            except TransientError as e:
                market.record_failure(kspec.key, d, "error", redact(str(e)), dv)
            if i % 50 == 0:
                log.info("KBar %d/%d", i, len(pending))
        kbar_rows = [{"date": r["date"], "open": r["close"], "minute": r["minute"]}   # 比對值放 open 鍵（compare_open 契約）
                     for r in (dict(x) for x in market.fetch_rows(kspec.table, "date BETWEEN ? AND ?", (start, end)))
                     if r.get("close") is not None]
    else:
        kbar_note = "--no-kbar：未比對第二候選"

    candidates = {"finmind_open": [{"date": r["date"], "open": r["open"]} for r in fm_rows]}
    if kbar_rows:
        candidates["kbar_0900_close"] = kbar_rows
    res = T.compare_candidates(twse_rows, candidates, tol=args.tol, agree_threshold=args.agree_threshold)
    twse_map = {r["date"]: r for r in twse_rows}
    fm_map = {r["date"]: r for r in fm_rows}
    kb_map = {r["date"]: r for r in kbar_rows}
    common = sorted(set(twse_map) & set(fm_map))
    sample = [{"date": d, "twse_open": twse_map[d].get("open"), "finmind_open": fm_map[d].get("open"),
               "kbar_0900_close": kb_map.get(d, {}).get("open")} for d in common[-10:]]
    payload = {
        "schema": 1,
        "generated_at": C.taipei_now().isoformat(timespec="seconds"),
        "date": res["candidates"]["finmind_open"]["last_date"],
        "status": "ok" if res["verdict"] != "no_data" else "empty",
        "data_version": dv,
        "range": {"from": start, "to": end},
        "twse_source": C.TWSE_MI5MINS_HIST,
        "candidates_source": {"finmind_open": "TaiwanStockPrice data_id=TAIEX open",
                              "kbar_0900_close": "TaiwanStockKBar data_id=TAIEX 09:00 bar close"},
        "twse_failed_months": tw_fail,
        "kbar_note": kbar_note,
        "tol": args.tol,
        "last_10_days": sample,
        **res,
    }
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")

    print("\n== 加權指數開盤價一致率（官方 TWSE MI_5MINS_HIST 開盤 vs 兩個候選；裁定 9）==")
    print(f"區間 {start} ~ {end}；TWSE {len(twse_map)} 日；一致＝|diff| ≤ {args.tol}；候選視為一致的門檻＝一致率 ≥ {args.agree_threshold:.0%}")
    print(f"{'候選':<18}{'共同日':>8}{'一致日':>8}{'一致率':>10}{'最大|diff|':>12}  判定")
    for name, r in res["candidates"].items():
        rate = f"{r['agree_rate']:.2%}" if r["agree_rate"] is not None else "-"
        mx = f"{r['max_abs_diff']:.2f}" if r["max_abs_diff"] is not None else "-"
        print(f"{name:<18}{r['n_common']:>8}{r['n_equal']:>8}{rate:>10}{mx:>12}  {'一致' if r['agrees'] else '不一致'}")
    if "kbar_0900_close" not in res["candidates"]:
        print(f"kbar_0900_close   （無資料：{kbar_note or '未取得'}）")
    v = res["verdict"]
    if v == "no_data":
        print("結論：無共同日，無法判定")
    elif v == "none":
        print("結論：**皆不一致**——依裁定 9 回問使用者，凍結前不得擇一")
    elif v in ("both", "all"):
        print("結論：兩個候選都與官方一致（差異在容差內）——任一可用；建議採宣告源 TaiwanStockPrice open")
    else:
        print(f"結論：與官方一致的是 **{v}**，以它為準")
    print("最近 10 個共同日：日期 / 官方 open / FinMind open / KBar 09:00 close")
    for x in sample:
        print(f"  {x['date']}  {x['twse_open']}  {x['finmind_open']}  {x['kbar_0900_close']}")
    if tw_fail:
        print(f"TWSE 失敗月份 {len(tw_fail)} 個（見 JSON twse_failed_months）")
    if kbar_note:
        print(f"備註：{kbar_note}")
    print(f"已寫 {out}")
    for st in stores.values():
        st.close()
    return 0


# ---------------------------------------------------------------------------
# report
# ---------------------------------------------------------------------------
def pit_pool_by_year(prices: Store, universe: Store) -> list[dict]:
    """每年 PIT 池統計：逐日呼叫 universe.pit_pool()（合格代號 ∩ 當日有價格列；不分市場，見 config.OUT_OF_SCOPE）。
    逐年讀 (date, stock_id) 兩欄、逐日分組，不整表載入。"""
    if not prices.table_exists("raw_price_daily") or not universe.table_exists("raw_stock_info"):
        return []
    ids = pool_ids_from_store(universe)
    if not ids:
        return []
    years = [r[0] for r in prices.conn.execute("SELECT DISTINCT substr(date,1,4) FROM raw_price_daily WHERE date IS NOT NULL ORDER BY 1")]
    out = []
    for y in years:
        by_day: dict[str, list[dict]] = {}
        for d, sid in prices.conn.execute("SELECT date, stock_id FROM raw_price_daily WHERE date BETWEEN ? AND ?", (f"{y}-01-01", f"{y}-12-31")):
            by_day.setdefault(d, []).append({"stock_id": sid})
        sizes = []
        distinct: set[str] = set()
        for d in sorted(by_day):
            pool = pit_pool(ids, by_day[d])
            sizes.append(len(pool))
            distinct.update(pool)
        if sizes:
            out.append({"year": y, "trading_days": len(sizes), "mean_daily_pool": round(sum(sizes) / len(sizes), 1),
                        "min_daily_pool": min(sizes), "max_daily_pool": max(sizes), "distinct_ids": len(distinct)})
    return out


def cmd_report(args) -> int:
    cache_dir = Path(args.cache_dir)
    stores = open_stores(cache_dir, C.DB_FILES)
    print(f"# coverage 報告  cache_dir={cache_dir}  台北 {C.taipei_now().isoformat(timespec='seconds')}")
    hdr = f"{'key':<22}{'db':<13}{'ok':>7}{'empty':>7}{'rows':>11}{'fail':>6}  {'min_key':<24}{'max_key':<24}ver 最後抓取"
    print(hdr)
    print("-" * len(hdr))
    total_fail = 0
    for spec in C.DATASETS:
        s = stores[spec.db].coverage_summary(spec.key)
        total_fail += s["failures"]
        print(f"{spec.key:<22}{spec.db:<13}{s['ok']:>7}{s['empty']:>7}{s['rows']:>11,}{s['failures']:>6}  "
              f"{str(s['min_key'] or ''):<24}{str(s['max_key'] or ''):<24}{s['versions']} {s['last_fetched_at'] or ''}")
    s = stores["market"].coverage_summary(MI5_DATASET_KEY)
    if s["ok"] or s["failures"]:
        print(f"{MI5_DATASET_KEY:<22}{'market':<13}{s['ok']:>7}{s['empty']:>7}{s['rows']:>11,}{s['failures']:>6}  {s['min_key'] or ''}~{s['max_key'] or ''}")

    tpe = tpe_calendar_from_store(stores["prices"])
    us = us_calendar_from_store(stores["market"])
    sox = cal.build_calendar(stores["market"].distinct_dates("raw_us_index", "^SOX"))
    print(f"\n台北交易日曆：{len(tpe)} 日 {tpe[0] if tpe else ''} ~ {tpe[-1] if tpe else ''}"
          f"（訓練 {sum(1 for d in tpe if C.segment_of(d)=='train')}／驗證 {sum(1 for d in tpe if C.segment_of(d)=='valid')}"
          f"／保留 {sum(1 for d in tpe if C.segment_of(d)=='holdout')}／暖機 {sum(1 for d in tpe if C.segment_of(d) is None)}）")
    print(f"美股交易日曆（^GSPC）：{len(us)} 日 {us[0] if us else ''} ~ {us[-1] if us else ''}；^SOX 與 ^GSPC 日期差集："
          f"只在 SOX {len(set(sox)-set(us))}、只在 GSPC {len(set(us)-set(sox))}")

    info_rows = [dict(r) for r in stores["universe"].fetch_rows("raw_stock_info")]
    if info_rows:
        pool = pool_from_info(info_rows)
        multi = sum(1 for v in pool.values() if v["n_rows"] > 1)
        same_day = sum(1 for v in pool.values() if v["same_date_multi"])
        by_type = {}
        for v in pool.values():
            by_type[v["type"]] = by_type.get(v["type"], 0) + 1
        print(f"\n個股池（TaiwanStockInfo 4 碼普通股）：{len(pool)} 檔 {by_type}；多列代號 {multi} 檔（市場轉換／產業重分類殘留，P0-A §4.4）"
              f"（裁定口徑現為 {C.POOL_SIZE_RULING} 檔）")
        print(f"同日多產業代號數：{same_day} 檔（同 date 多列，已以決定性 tie-break 取值——universe.UMBRELLA_CATEGORIES；請人工複核）")
    py = pit_pool_by_year(stores["prices"], stores["universe"])
    if py:
        print("\n每年 point-in-time 池（當日有價格列 ∩ 合格代號）：")
        print(f"{'年':<6}{'交易日':>7}{'日均池':>9}{'最小':>7}{'最大':>7}{'不重複代號':>11}")
        for r in py:
            print(f"{r['year']:<6}{r['trading_days']:>7}{r['mean_daily_pool']:>9}{r['min_daily_pool']:>7}{r['max_daily_pool']:>7}{r['distinct_ids']:>11}")
    else:
        print("\n每年 PIT 池：raw_price_daily 或 raw_stock_info 尚未落地，無法計算")

    print(f"\n失敗清單（合計 {total_fail}；每 DB 最多列 {args.max_failures}）：")
    any_fail = False
    for name, st in stores.items():
        for row in st.failures_list(limit=args.max_failures):
            any_fail = True
            print(f"  {name:<12} {row[0]:<22} {row[1]:<28} {row[2]:<10} ×{row[5]} {row[4]}  {str(row[3])[:90]}")
    if not any_fail:
        print("  （無）")
    for st in stores.values():
        st.close()
    return 0


def cmd_calendar(args) -> int:
    dv = C.validate_data_version(args.data_version or C.default_data_version(args.batch))
    setup_logging(Path(args.cache_dir), dv, args.quiet)
    stores = open_stores(Path(args.cache_dir), ("prices", "market"))
    write_calendars(stores, dv, REPO / "data", Path(args.cache_dir))
    for s in stores.values():
        s.close()
    return 0


# ---------------------------------------------------------------------------
def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--cache-dir", default=str(REPO / "cache"), help="SQLite 位置（不進 git；預設 <repo>/cache）")
    ap.add_argument("--env-file", default=str(REPO / ".env"), help="含 FINMIND_TOKEN=… 的 .env（不進 git）")
    ap.add_argument("--data-version", help="fm-YYYYMMDD-<批次>；預設 fm-<台北今日>-<batch>")
    ap.add_argument("--batch", default="01", help="data_version 的批次尾碼（預設 01）")
    ap.add_argument("--interval", type=float, default=C.DEFAULT_INTERVAL_SEC, help="FinMind 請求最小間隔秒")
    ap.add_argument("--quiet", action="store_true")
    sub = ap.add_subparsers(dest="cmd", required=True)

    def common(sp):
        sp.add_argument("--dataset", nargs="*", help="只處理這些 key（見 plan 輸出第一欄）")
        sp.add_argument("--group", nargs="*", help="core／optional／official（預設 core）")
        sp.add_argument("--from", dest="start", help="覆寫起日 YYYY-MM-DD")
        sp.add_argument("--to", dest="end", help="覆寫迄日 YYYY-MM-DD")
        sp.add_argument("--strategy", nargs="*", help="key=strategy 覆寫，如 dividend_result=per_stock")

    sp = sub.add_parser("plan", help="免 token 免網路的請求計畫")
    common(sp)
    sp.add_argument("--show-keys", action="store_true")
    sp.set_defaults(fn=cmd_plan)

    sp = sub.add_parser("run", help="實抓（可中斷續跑）")
    common(sp)
    sp.add_argument("--limit", type=int, default=0, help="每個資料集最多抓 N 鍵（試跑用）")
    sp.add_argument("--force", action="store_true", help="忽略 coverage 重抓")
    sp.add_argument("--no-fallback", action="store_true", help="權限不足時不自動改用 per_stock")
    sp.add_argument("--no-token", action="store_true", help="免 token 執行（只有 free 資料集會成功；本容器自測用）")
    sp.add_argument("--progress-every", type=int, default=50)
    sp.add_argument("--tpex-no-verify", action="store_true", help="對 tpex.org.tw 關閉 TLS 驗證（taiwan-flows 註記部分端點 SSL 異常時才用）")
    sp.set_defaults(fn=cmd_run)

    sp = sub.add_parser("taiex-open-check", help="裁定 4：TWSE 官方指數開盤 vs FinMind TAIEX open")
    sp.add_argument("--month-from", default="202201", help="YYYYMM（預設 202201）")
    sp.add_argument("--month-to", default="202608", help="YYYYMM（預設 202608）")
    sp.add_argument("--tol", type=float, default=0.005, help="視為一致的絕對差（預設 0.005）")
    sp.add_argument("--out", default=str(REPO / "data" / "taiex_open_check.json"))
    sp.add_argument("--force", action="store_true")
    sp.add_argument("--no-kbar", action="store_true", help="不抓第二候選 TaiwanStockKBar")
    sp.add_argument("--kbar-limit", type=int, default=0, help="KBar 最多抓 N 個交易日（試跑用）")
    sp.add_argument("--agree-threshold", type=float, default=C.OPEN_CHECK_AGREE_THRESHOLD,
                    help="候選一致率 ≥ 此值視為與官方一致（預設 0.99）")
    sp.set_defaults(fn=cmd_taiex_open_check)

    sp = sub.add_parser("report", help="coverage／PIT 池／失敗清單")
    sp.add_argument("--max-failures", type=int, default=30)
    sp.set_defaults(fn=cmd_report)

    sp = sub.add_parser("calendar", help="由 DB 重生 data/calendar_*.json")
    sp.set_defaults(fn=cmd_calendar)
    return ap


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
