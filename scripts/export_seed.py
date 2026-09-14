#!/usr/bin/env python3
"""每日班種子匯出（D-2a，Hetzner 執行）：把回補層的 pool／factors／fundamentals／狀態快照＋最近一段原料包，
以 `docs/P2-DAILY-PLAN.md` §7.1 的檔案格式寫進 repo，讓 Actions 每日班能從 `state.last_date` 的下一交易日接續計分。

    python3 scripts/export_seed.py --cache-dir cache --out .            # 狀態＝cache/scores.db.state.json
    python3 scripts/export_seed.py --cache-dir cache --out . --start 2025-05-01

原料包起點＝`ReplaySource.rebuild_start(last_date)` 與「last_date 往前 window＋ADV_WINDOW＋1 個交易日」兩者取早者
（前者保 `WindowCache` ring 逐位相同，後者保 T 當日排名池與快照相等），`--start` 可再往前。
拒匯條件：`data/calendar_tpe.json` 的日期與原料 `trading_dates()` 在 ≤ last_date 範圍內不一致（基本面可得日靠它）；
狀態快照缺 `meta` 或與本次參數指紋／window 不符。回傳碼：0 成功、2 中止。
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "src"))

from iching import bundle_io as B  # noqa: E402
from iching import daily_core as DC  # noqa: E402
from iching import feed as F  # noqa: E402
from iching import replay_io as RIO  # noqa: E402
from iching import replay_state as RS  # noqa: E402
from iching.features_io import FeatureStoreError, params_fingerprint  # noqa: E402
from iching.fundamentals import NEEDED_TYPES  # noqa: E402
from iching.liquidity import ADV_WINDOW  # noqa: E402
from iching.run_common import ReplayDriverError, build_params_payload, check_snapshot_meta, load_state, save_state  # noqa: E402
from iching.score.params import MARKETS, build_params  # noqa: E402


def _q(conn, sql, params=()):
    return conn.execute(sql, params).fetchall()


def export_pool_rows(src: RIO.ReplaySource) -> list[dict]:
    """＝`feed.load_pool` 讀的同一組列（5 欄、不篩 data_version）。"""
    have = F.columns(src.universe, F.INFO_TABLE)
    cols = [c for c in DC.POOL_COLS if c in have]
    return [dict(zip(cols, r)) for r in _q(src.universe, f'SELECT {",".join(cols)} FROM "{F.INFO_TABLE}"')]


def export_factor_rows(src: RIO.ReplaySource, dv: str) -> list[tuple]:
    """＝`feed.load_factors` 的 SQL（同序），去重／壞值規則留給讀取端 `daily_core.factors_from_rows`。"""
    return _q(src.prices, f'SELECT stock_id, date, before_price, after_price FROM "{F.DIV_TABLE}" '
                          f"WHERE data_version=? AND date IS NOT NULL ORDER BY stock_id, date", (dv,))


def export_fundamentals(src: RIO.ReplaySource, dv: str) -> tuple[dict, dict, dict]:
    """＝`replay_io.load_fundamentals` 的三段 SQL（月營收／季報／期末原始收盤），只收池內。"""
    monthly: dict[str, list] = {}
    quarters: dict[str, list] = {}
    px: dict[str, dict[str, float]] = {}
    fdb = src.fundamentals_db
    if fdb is None:
        return monthly, quarters, px
    for sid, y, m, v in _q(fdb, 'SELECT stock_id, revenue_year, revenue_month, revenue FROM raw_month_revenue '
                                'WHERE data_version=? ORDER BY stock_id, revenue_year, revenue_month', (dv,)):
        if str(sid) in src.pool and v is not None:
            monthly.setdefault(str(sid), []).append([int(y), int(m), RIO.F_num(v)])
    ph = ",".join("?" for _ in NEEDED_TYPES)
    periods: set[str] = set()
    for sid, p, t, v in _q(fdb, f'SELECT stock_id, date, type, value FROM raw_financial_statements '
                                f'WHERE data_version=? AND type IN ({ph}) ORDER BY stock_id, date', (dv, *NEEDED_TYPES)):
        if str(sid) in src.pool and v is not None and p:
            quarters.setdefault(str(sid), []).append([str(p), str(t), RIO.F_num(v)])
            periods.add(str(p))
    for p in sorted(periods):
        d = _q(src.prices, f'SELECT MAX(date) FROM "{F.PRICE_TABLE}" WHERE data_version=? AND date<=?', (dv, p))[0][0]
        if d is None:
            continue
        for sid, c in _q(src.prices, f'SELECT stock_id, close FROM "{F.PRICE_TABLE}" WHERE data_version=? AND date=?', (dv, d)):
            if c is not None and float(c) > 0:
                px.setdefault(str(sid), {})[p] = float(c)
    return monthly, quarters, px


def run(args) -> int:
    cache, out = Path(args.cache_dir), Path(args.out)
    state_path = Path(args.state) if args.state else cache / "scores.db.state.json"
    src = None
    try:
        cross = load_state(state_path)
        if not cross.last_date:
            raise ReplayDriverError(f"{state_path} 沒有 last_date")
        src = RIO.ReplaySource(cache, args.data_version, window=args.window)
        dv = src.dv
        ps = {m: build_params(m) for m in MARKETS}
        mv = {m: ps[m].model_version() for m in MARKETS}
        sha = params_fingerprint(build_params_payload(mv, args.window, cross.adv, fundamentals=not args.no_fundamentals))
        check_snapshot_meta(cross, window=args.window, params_sha=sha, path=state_path)
        last = cross.last_date
        dates = src.trading_dates(end=last)
        if not dates or dates[-1] != last:
            raise ReplayDriverError(f"原料交易日到 {dates[-1] if dates else None}，與快照 last_date={last} 不符")
        # 日曆一致性（基本面可得日靠 data/calendar_tpe.json）
        cal_path = out / DC.CALENDAR_TPE_FILE
        cal = DC.load_calendar_dates(cal_path)
        cal_upto = [d for d in cal if d <= last]
        if cal_upto != dates:
            a, b = set(cal_upto), set(dates)
            raise ReplayDriverError(f"{cal_path} 與原料交易日不一致（≤{last}）：只在日曆 {sorted(a - b)[:5]}／只在原料 {sorted(b - a)[:5]}")
        # 原料包起點
        i_last = dates.index(last)
        by_count = dates[max(0, i_last - (args.window + ADV_WINDOW + 1))]
        rs = src.rebuild_start(last, args.window) or by_count
        start = min(rs, by_count)
        if args.start:
            start = min(start, args.start)
        seed_dates = [d for d in dates if d >= start]
        # 寫檔
        t0 = time.time()
        DC.write_json(out / DC.POOL_FILE, DC.pool_payload(export_pool_rows(src), dv))
        DC.write_json(out / DC.FACTORS_FILE, DC.factors_payload(export_factor_rows(src, dv), dv))
        mo, qu, px = export_fundamentals(src, dv)
        DC.write_json(out / DC.FUND_FILE, DC.fundamentals_payload(mo, qu, px, dv))
        cross.meta = {**cross.meta, "data_version": dv}
        save_state(out / DC.STATE_FILE, cross)
        total = 0
        for T in seed_dates:
            p = B.write_bundle(out, src.read_day(T))
            total += p.stat().st_size
        el = time.time() - t0
        _, pool = DC.load_pool_file(out / DC.POOL_FILE)
        _, factors, fstat = DC.load_factors_file(out / DC.FACTORS_FILE)
        print(f"data_version={dv} 快照 last_date={last} 參數指紋={sha} window={args.window}\n"
              f"pool {len(pool)} 檔（原料 {len(src.pool)}）　factors {fstat['stocks']} 檔（原料 {src.factor_stats['stocks']}）　"
              f"fundamentals 月營收 {len(mo)} 檔／季報 {len(qu)} 檔\n"
              f"原料包 {len(seed_dates)} 日（{seed_dates[0]}～{seed_dates[-1]}；rebuild_start={rs}、計數起點={by_count}）"
              f"合計 {total / 1024 / 1024:.2f} MB　{el:.1f}s\n"
              f"寫入：{DC.POOL_FILE} {DC.FACTORS_FILE} {DC.FUND_FILE} {DC.STATE_FILE} {B.BUNDLE_DIR}/")
        if pool != src.pool:
            print("[中止] pool 檔讀回與原料 load_pool 不同", file=sys.stderr)
            return 2
        if factors != src.factors:
            print("[中止] factors 檔讀回與原料 load_factors 不同", file=sys.stderr)
            return 2
        if src.missing_tables:
            print(f"[注意] 讀不到的表：{sorted(src.missing_tables)}")
        return 0
    except (F.FeedError, FeatureStoreError, ReplayDriverError, RIO.ReplayIOError, DC.DailyCoreError, B.BundleError,
            RS.ReplayStateError, ValueError) as e:
        print(f"[export_seed 中止] {e}", file=sys.stderr)
        return 2
    finally:
        if src is not None:
            src.close()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="每日班種子匯出（pool／factors／fundamentals／狀態／原料包）")
    ap.add_argument("--cache-dir", default=str(REPO / "cache"))
    ap.add_argument("--out", default=str(REPO), help="repo 根（會寫 data/ 與 runs/collect/）")
    ap.add_argument("--state", default=None, help="預設 <cache-dir>/scores.db.state.json")
    ap.add_argument("--data-version", default=None)
    ap.add_argument("--window", type=int, default=RS.WINDOW_N)
    ap.add_argument("--start", default=None, help="原料包起點再往前到此日（只能更早）")
    ap.add_argument("--no-fundamentals", action="store_true", help="快照若是 --no-fundamentals 跑出來的，這裡也要給")
    return run(ap.parse_args(argv))


if __name__ == "__main__":
    raise SystemExit(main())
