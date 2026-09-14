#!/usr/bin/env python3
"""Hetzner：把回補層的每日原料匯出成每日班用的原料包（`runs/collect/<T>-daily.json.gz`）。

    python3 scripts/export_bundles.py --from 2026-08-31 --to 2026-08-31          # 先匯一天量大小
    python3 scripts/export_bundles.py --last 320                                  # 種子：最後 320 個交易日
    python3 scripts/export_bundles.py --from 2025-05-01 --out /tmp/collect

來源＝`replay_io.ReplaySource.read_day(T)`（與重播驅動同一條路），只是不 ingest、不計分；美股／匯率的
「首日回補、之後增量」語意在匯出時**改為每日只放該日新到的列**（`--last`／`--from` 起點那天會含前 320 個美股日，
與驅動的首日行為一致）。輸出經 `bundle_io`（決定性、gzip mtime=0）。收尾印每檔大小與合計，
這就是 `docs/P2-DAILY-PLAN.md` §5 Q1 要的實測數字。
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "src"))

from iching import bundle_io as B  # noqa: E402
from iching import replay_io as RIO  # noqa: E402
from iching.feed import FeedError  # noqa: E402


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="匯出每日原料包（唯讀原料 DB）")
    ap.add_argument("--cache-dir", default=str(REPO / "cache"))
    ap.add_argument("--out", default=str(REPO), help="原料包根目錄（會建 runs/collect/）；預設 repo 根")
    ap.add_argument("--data-version", default=None)
    ap.add_argument("--from", dest="start", default=None)
    ap.add_argument("--to", dest="end", default=None)
    ap.add_argument("--last", type=int, default=None, help="只匯最後 N 個交易日（與 --from 互斥）")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args(argv)
    if args.last and args.start:
        print("[export 中止] --last 與 --from 互斥", file=sys.stderr)
        return 2
    src = None
    try:
        src = RIO.ReplaySource(Path(args.cache_dir), args.data_version)
        dates = src.trading_dates(start=args.start, end=args.end)
        if args.last:
            dates = dates[-args.last:]
        if not dates:
            print("[export 中止] 沒有符合的交易日", file=sys.stderr)
            return 2
        # 起點前的日子只為了讓美股／匯率的增量游標對齊，不匯出
        t0 = time.time()
        total = 0
        sizes: list[int] = []
        for T in dates:
            b = src.read_day(T)
            p = B.write_bundle(Path(args.out), b)
            n = p.stat().st_size
            sizes.append(n)
            total += n
            if not args.quiet:
                print(f"  {T}  檔 {len(b.stocks):>5}  美股列 {len(b.us):>3}  {n / 1024:8.1f} KB  → {p}", flush=True)
        el = time.time() - t0
        sizes.sort()
        print(f"\n匯出 {len(dates)} 日，{el:.1f}s；每檔 min {sizes[0] / 1024:.1f} / 中位 {sizes[len(sizes) // 2] / 1024:.1f} / "
              f"max {sizes[-1] / 1024:.1f} KB；合計 {total / 1024 / 1024:.2f} MB；data_version={src.dv}")
        if src.missing_tables:
            print(f"[注意] 讀不到的表：{sorted(src.missing_tables)}")
        if src.official_errors:
            print(f"[注意] 官方 JSON 解析失敗 {len(src.official_errors)} 次，前 3 筆＝{src.official_errors[:3]}")
        return 0
    except (FeedError, RIO.ReplayIOError, B.BundleError, OSError) as e:
        print(f"[export 中止] {e}", file=sys.stderr)
        return 2
    finally:
        if src is not None:
            src.close()


if __name__ == "__main__":
    raise SystemExit(main())
