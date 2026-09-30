#!/usr/bin/env python3
"""每日班入口（GitHub Actions 內跑；D-2b）：當日 API → 原料包 → 增量檔 → `daily_core.run_offline` → 分數與狀態進 repo。

    python3 scripts/daily_run.py                       # T＝台北今日；補跑狀態 last_date 之後的每個交易日（上限 --max-days）
    python3 scripts/daily_run.py --date 2026-09-15     # 指定 T（補跑／重跑用；仍會先補中間缺的交易日）

回傳碼：0＝完成／no-op／核心資料未齊已寫 waiting；2＝設定或資料錯誤（訊息在 stderr）。
token：`FINMIND_TOKEN` 環境變數（Actions secret）或 `--env-file`。**不印 token、不寫進任何檔**。
產出檔（由 workflow 一個 commit 收）：`runs/collect/<T>-daily.json.gz`、`data/pool.json`／`factors.json`／`fundamentals.json`／
`calendar_*.json`、`data/scores/<T>.json`、`data/state/cross.json`；未齊時只有 `runs/collect/<T>-waiting.json`
（外加 `data/pool.json`——它在抓取前就依當日 TaiwanStockInfo 更新，有變即改寫、不回滾）。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "src"))

from iching import bundle_io as B  # noqa: E402
from iching import config as CFG  # noqa: E402
from iching import daily_core as DC  # noqa: E402
from iching import daily_fetch as DF  # noqa: E402
from iching import daily_pipeline as DP  # noqa: E402
from iching import replay_state as RS  # noqa: E402
from iching import twse as T  # noqa: E402
from iching.features_io import FeatureStoreError  # noqa: E402
from iching.fm import FinMind, FinMindError  # noqa: E402
from iching.fundamentals import FundamentalsError  # noqa: E402
from iching.run_common import ReplayDriverError  # noqa: E402


def build_fetcher(args) -> DF.Fetcher:
    fm = FinMind(env_file=Path(args.env_file) if args.env_file else None, min_interval=args.interval)
    oc = T.OfficialClient(interval=CFG.OFFICIAL_INTERVAL_SEC, tpex_verify=not args.tpex_no_verify)
    return DF.Fetcher(fm, oc)


def run(args, fetcher: DF.Fetcher | None = None) -> int:
    root = Path(args.root)
    upto = args.date or CFG.taipei_today_str()
    try:
        fetcher = fetcher or build_fetcher(args)
        summary = DP.run_pipeline(root, fetcher, upto=upto, window=args.window, max_days=args.max_days,
                                  fundamentals=not args.no_fundamentals, entrants_window=args.entrants_window)
        print(json.dumps({k: v for k, v in summary.items() if k != "done"}, ensure_ascii=False))
        for d in summary.get("done", []):
            print(f"  {d['date']}: rows={d['rows']} calls={d['n_calls']} factors+{d['factors_added']} pool_changed={d['pool_changed']}")
        gap_msg = DP.format_series_gaps(summary.get("series_gaps"), prefix="[daily 摘要] ")   # §7.8.6 us／fx 序列洞：只警示不擋班（rc 仍 0）
        if gap_msg:                                                     # `::warning::` annotation 已由 run_pipeline 的 log（stdout）印過，這裡換前綴、不重複 annotation
            print(gap_msg)
        return 0
    except (DP.DailyPipelineError, DC.DailyCoreError, DF.DailyFetchError, ReplayDriverError, RS.ReplayStateError,
            B.BundleError, FeatureStoreError, FundamentalsError, FinMindError, T.TwseError, OSError, ValueError) as e:
        print(f"[daily 中止] {type(e).__name__}: {e}", file=sys.stderr)
        return 2


def main(argv=None, fetcher: DF.Fetcher | None = None) -> int:
    ap = argparse.ArgumentParser(description="每日班：抓當日原料、增量更新、計分落地")
    ap.add_argument("--root", default=str(REPO))
    ap.add_argument("--date", default=None, help="T（預設台北今日）；狀態 last_date 之後到 T 的交易日全部補跑")
    ap.add_argument("--window", type=int, default=RS.WINDOW_N)
    ap.add_argument("--max-days", type=int, default=5)
    ap.add_argument("--env-file", default=None)
    ap.add_argument("--interval", type=float, default=0.7)
    ap.add_argument("--tpex-no-verify", action="store_true", help="TPEx 憑證鏈問題時關閉驗證（比照 backfill）")
    ap.add_argument("--no-fundamentals", action="store_true")
    ap.add_argument("--entrants-window", type=int, default=None,
                    help="新入池檔側檔回看交易日數（預設＝--window；docs/P2-DAILY-PLAN.md §7.7）")
    return run(ap.parse_args(argv), fetcher=fetcher)


if __name__ == "__main__":
    raise SystemExit(main())
