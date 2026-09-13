#!/usr/bin/env python3
"""逐日重播計分 → 落地 `scores.db`（第 13 項 13a-3）。在 Hetzner 上跑。

    原料 DB（prices／universe／chips／market，唯讀）＋ features.db（唯讀）
      → `replay_io.ReplaySource.read_day(T)`   → `DayBundle`
      → `replay_state.WindowCache.ingest()`     → 視窗（後復權、對齊）
      ＋ `replay_io.ReplaySource.load_fundamentals()` → `fundamentals.FundamentalsBridge`（13b：月營收／季報 as-of T）
      → `replay_step.step(T)`                   → 當日全部列（大盤 6 列＋每檔 3 列）
      → `scores_io.ScoreStore.write_day()`      → scores.db
      ＋ `CrossDayState` 快照 JSON（`<out>.state.json`，每 `--state-every` 日與收尾各存一次）

用法（**指令是 `python3`**）：

    python3 scripts/replay_scores.py --limit-days 20                # 先量真實每日成本
    python3 scripts/replay_scores.py                                # 全量（從最早交易日起、逐日單向）
    python3 scripts/replay_scores.py --resume                       # 中斷後接續（讀 state.json，從 last_date 之後續跑）
    python3 scripts/replay_scores.py --rebuild                      # 砍掉該 data_version 重寫
    python3 scripts/replay_scores.py --from 2024-01-02 --state s.json   # 從指定日起跑，**必須**給前一交易日的狀態快照

## 為什麼沒有「從中間冷起跑」

遲滯要 T−1 的 state／streak、個股二爻要 T−9…T−1、大盤要 T−5（`docs/P2-REPLAY-PLAN.md` §1 #1）。
從中間用空的 `CrossDayState` 起跑，前 9 日的四爻族 A 缺值、正式爻態重新初始化——那是實作造成的假訊號。
所以 `--from` 一律要求 `--state`（前一交易日的快照），`--resume` 讀本腳本自己存的快照；
沒有快照就只能全量（`--rebuild` 或空 DB）。視窗（`WindowCache`）可由原料重建，開跑前會先從
`ReplaySource.rebuild_start()` 算出的日期起 ingest、不計分——**不是**往前 `--window` 個日曆交易日：視窗語意是
每檔最近 `window` 個**有成交**列，停牌過的檔用日曆倒推會少列（驅動測試以 `--resume` 對全量逐位比對守著）。

## 記憶體與成本

每 `--progress-every` 日印 RSS（`resource.getrusage`）；§B3.3 要求峰值 < 1.5 GiB。
成本估算見 `docs/P2-REPLAY-PLAN.md` §5（**分段量測相加會低估**，以 `--limit-days 20` 的實跑為準）。
"""
from __future__ import annotations

import argparse
import os
import resource
import sqlite3
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "src"))

from iching import feed as F  # noqa: E402
from iching import replay_io as RIO  # noqa: E402
from iching import replay_state as RS  # noqa: E402
from iching import replay_step as ST  # noqa: E402
from iching.features_io import FeatureStoreError  # noqa: E402
from iching.fundamentals import FundamentalsError  # noqa: E402
from iching.score.params import MARKETS, build_params  # noqa: E402
from iching.scores_io import ScoreStore, ScoreStoreError  # noqa: E402

TEXT_VERSION = "0.2"          # spec/P1-B4-hexagram-text.md:127


class ReplayDriverError(RuntimeError):
    pass


def rss_mib() -> float:
    ru = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return ru / 1024.0 if sys.platform != "darwin" else ru / (1024.0 * 1024.0)


def build_params_payload(mv: dict[str, str], window: int, adv, *, fundamentals: bool = True) -> dict:
    """釘進 `replay_meta` 的參數集合：任何會改變輸出的設定都要在這裡（含 13b 基本面橋開關）。"""
    return {"model_version": dict(mv), "text_version": TEXT_VERSION, "window": int(window),
            "adv_window": adv.window, "adv_threshold": adv.threshold, "state_schema": RS.STATE_SCHEMA,
            "fundamentals": bool(fundamentals)}


def load_state(path: Path) -> RS.CrossDayState:
    try:
        return RS.CrossDayState.from_json(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, KeyError, RS.ReplayStateError) as e:
        raise ReplayDriverError(f"狀態快照讀取失敗 {path}：{e}") from e


def save_state(path: Path, cross: RS.CrossDayState) -> None:
    """同目錄 `.tmp` 再 `replace`（原子）＋ fsync：程序被殺或斷電都不會留半個檔。"""
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(cross.to_json())
        f.flush()
        os.fsync(f.fileno())
    tmp.replace(path)


def check_snapshot_meta(cross: RS.CrossDayState, *, window: int, params_sha: str, path: Path) -> None:
    """快照的 `meta`（驅動寫入的 window／params_sha）必須與本次相同——`--window` 不在 `replay_meta` 之外的任何地方，
    `--from --state` 進新 DB 時若不比對，會無聲產出既非舊 window 也非新 window 的列（13a-3 驗收實測）。
    舊快照沒有 meta 一律拒，不猜。"""
    m = cross.meta or {}
    if m.get("window") != window or m.get("params_sha") != params_sha:
        raise ReplayDriverError(f"快照 {path} 的 meta={m} 與本次 window={window}／參數指紋={params_sha} 不符；"
                                f"快照只能接續產生它的那組設定（不同 window／參數請 --rebuild 全量）")


def run(args) -> int:
    if args.start and args.end and args.end < args.start:
        print(f"[replay 中止] --to {args.end} 早於 --from {args.start}", file=sys.stderr)
        return 2
    cache = Path(args.cache_dir)
    out = Path(args.out) if args.out else cache / "scores.db"
    state_out = Path(str(out) + ".state.json")                               # 輸出快照永遠在這裡
    state_path = Path(args.state) if args.state else state_out              # 輸入快照（--from 用；--resume 讀輸出檔）
    src = None
    store = None
    try:
        src = RIO.ReplaySource(cache, args.data_version, features_path=Path(args.features) if args.features else None,
                               window=args.window)
        dv = src.dv
        all_dates = src.trading_dates()
        if not all_dates:
            raise F.FeedError(f"{F.PRICE_TABLE} 在 data_version={dv} 下沒有任何日期")
        ps = {m: build_params(m) for m in MARKETS}
        mv = {m: ps[m].model_version() for m in MARKETS}
        cross = RS.CrossDayState()
        use_fund = not args.no_fundamentals
        params = build_params_payload(mv, args.window, cross.adv, fundamentals=use_fund)
        store = ScoreStore(out)
        if args.rebuild:
            deleted = store.clear(dv)
            print(f"[rebuild] 已刪除 {dv}：{ {k: v for k, v in deleted.items() if v} }", flush=True)
            if state_out.exists():
                state_out.unlink()
        sha = store.set_params(dv, params)

        # -- 起跑點與跨日狀態 --
        if args.resume:
            if args.start:
                raise ReplayDriverError("--resume 與 --from 不可同時給")
            if args.state:
                raise ReplayDriverError("--resume 讀的是 <out>.state.json，不接受 --state")
            if not state_out.exists():
                raise ReplayDriverError(f"--resume 找不到狀態快照 {state_out}；沒有快照不能從中間續跑（見 docstring）")
            cross = load_state(state_out)
            check_snapshot_meta(cross, window=args.window, params_sha=sha, path=state_out)
            if cross.last_date is None:
                raise ReplayDriverError(f"{state_out} 沒有 last_date")
            landed = set(store.dates(dv))
            if cross.last_date not in landed:
                raise ReplayDriverError(f"快照停在 {cross.last_date}，但 scores.db 沒有那一天的列；快照與 DB 不同步，請 --rebuild")
            write_from = next((d for d in all_dates if d > cross.last_date), None)
            if write_from is None:
                print(f"[resume] 快照已到最後一個交易日 {cross.last_date}，沒有新日期。")
                return 0
        elif args.start:
            if not args.state:
                raise ReplayDriverError("--from 必須搭配 --state（前一交易日的 CrossDayState 快照）；重播不可從中間冷起跑")
            cross = load_state(state_path)
            check_snapshot_meta(cross, window=args.window, params_sha=sha, path=state_path)
            prev = next((d for d in reversed(all_dates) if d < args.start), None)
            if cross.last_date != prev:
                raise ReplayDriverError(f"--from {args.start} 的前一交易日是 {prev}，但快照 last_date={cross.last_date}")
            write_from = next((d for d in all_dates if d >= args.start), None)
            if write_from is None:
                raise ReplayDriverError(f"--from {args.start} 之後沒有交易日")
        else:
            if store.dates(dv) and not args.rebuild:
                raise ReplayDriverError(f"{out} 已有 {dv} 的 {len(store.dates(dv))} 日；全量重跑請 --rebuild，接續請 --resume")
            write_from = all_dates[0]
        i = all_dates.index(write_from)
        if i == 0:
            ingest_from = all_dates[0]
        else:
            # 視窗重建起點由資料決定（每檔最近 window 個**有成交**日、各序列最近 window 個日期，取最早），
            # 不是「往前 window 個日曆交易日」——停牌過的檔會少列、續跑第一日就與全量跑不同
            rs = src.rebuild_start(write_from, args.window)
            ingest_from = next((d for d in all_dates if rs is not None and d >= rs), all_dates[0]) if rs else all_dates[0]
            if ingest_from > all_dates[max(0, i - args.window)]:
                ingest_from = all_dates[max(0, i - args.window)]
        if cross.adv.state()["amt"] == {} and i > 0:
            # 冷狀態卻不是從頭跑：只有 --from/--state 給了空快照才會到這裡；PIT 池會與全量跑不同
            print("[注意] 跨日狀態的 ADV 視窗是空的，前 60 日的排名池旗標會與全量跑不同", flush=True)
        params = build_params_payload(mv, args.window, cross.adv, fundamentals=use_fund)
        if store.set_params(dv, params) != sha:
            raise ScoreStoreError("參數指紋在載入快照後改變（快照的 ADV 設定與本次不同）")
        cross.meta = {"window": int(args.window), "params_sha": sha}

        wc = RS.WindowCache(src.pool, src.factors, window=args.window)
        bridge = src.load_fundamentals(all_dates) if use_fund else None       # 13b：月營收／季報 as-of T（法定期限）
        provider = bridge.provider() if bridge is not None else None
        fund_note = (f"基本面橋：{len(bridge.stocks)} 檔有原料" if bridge is not None else "基本面橋：關閉（--no-fundamentals）")
        print(f"data_version={dv} 參數指紋={sha} 池={len(src.pool)} 檔 除權息={src.factor_stats['stocks']} 檔 {fund_note}\n"
              f"model_version twse={mv['twse']} tpex={mv['tpex']} text_version={TEXT_VERSION}\n"
              f"視窗重建起點={ingest_from}（{i - all_dates.index(ingest_from)} 日，只 ingest 不計分） 計分起點={write_from} "
              f"出檔={out} 狀態快照={state_out}" + (f"（輸入快照 {state_path}）" if args.state else ""), flush=True)

        n_ingest = n_step = 0
        t0 = time.time()
        last_written = None
        rows_total = 0
        for T in all_dates[all_dates.index(ingest_from):]:
            if args.end and T > args.end:
                break
            wc.ingest(src.read_day(T))
            n_ingest += 1
            if T < write_from:
                continue
            res = ST.step(T, wc, cross, ps, data_version=dv, text_version=TEXT_VERSION, model_version=mv, fundamentals=provider)
            rows_total += store.write_day(dv, T, res.all_rows(), res.diag)
            n_step += 1
            last_written = T
            if args.state_every and n_step % args.state_every == 0:
                save_state(state_out, cross)
            if not args.quiet and n_step % args.progress_every == 0:
                el = time.time() - t0
                d = res.diag
                print(f"  {n_step} 日（{T}） 檔 {d['n_stocks']} 池 {d['n_in_pool']} 列 {rows_total:,}　"
                      f"{el:.0f}s　{el / n_step * 1000:.0f} ms/日　step {d['elapsed_ms']:.0f} ms　RSS {rss_mib():.0f} MiB", flush=True)
            if args.limit_days and n_step >= args.limit_days:
                break
        if last_written is not None:
            save_state(state_out, cross)
        el = time.time() - t0
        print(f"\ningest {n_ingest} 日、計分 {n_step} 日、落地 {rows_total:,} 列，{el:.1f}s"
              f"（{el / max(n_step, 1) * 1000:.0f} ms/計分日） RSS 峰值 {rss_mib():.0f} MiB")
        if bridge is not None and last_written is not None:
            cov = bridge.coverage(last_written)
            print(f"基本面覆蓋（as-of {last_written}）：有月營收 {cov['with_monthly']} 檔、有季報 {cov['with_quarter']} 檔／有原料 {cov['stocks']} 檔／池 {len(src.pool)} 檔")
        if src.missing_tables:
            print(f"[注意] 讀不到的表（對應欄整段缺值）：{sorted(src.missing_tables)}")
        if src.official_errors:
            print(f"[注意] 官方 JSON 解析失敗 {len(src.official_errors)} 次，前 3 筆＝{src.official_errors[:3]}")
        expected = [d for d in all_dates if d >= write_from and (not args.end or d <= args.end)]
        if args.limit_days:
            expected = expected[:n_step]
        miss = store.missing_dates(dv, expected)
        if miss:
            print(f"[警告] 預期有但沒落地的日期 {len(miss)} 個，前 5 個＝{miss[:5]}")
            return 1
        if n_step == 0:
            print("[警告] 一日都沒有計分。檢查 --from／--to／--limit-days 的組合。")
            return 1
        print(f"日期完整性：預期 {len(expected)} 日，全部落地。狀態快照 last_date={cross.last_date}")
        return 0
    except (F.FeedError, FeatureStoreError, ScoreStoreError, ReplayDriverError, RIO.ReplayIOError, FundamentalsError,
            RS.ReplayStateError, ST.ReplayStepError, OSError, sqlite3.Error) as e:
        print(f"[replay 中止] {e}", file=sys.stderr)
        return 2
    finally:
        if src is not None:
            src.close()
        if store is not None:
            store.close()


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description="逐日重播計分並落地 scores.db")
    ap.add_argument("--cache-dir", default=str(REPO / "cache"))
    ap.add_argument("--out", default=None, help="預設 <cache-dir>/scores.db")
    ap.add_argument("--features", default=None, help="預設 <cache-dir>/features.db")
    ap.add_argument("--data-version", default=None)
    ap.add_argument("--from", dest="start", default=None, help="計分起點（需 --state）")
    ap.add_argument("--to", dest="end", default=None)
    ap.add_argument("--state", default=None, help="--from 用的輸入快照（前一交易日）；輸出一律寫 <out>.state.json，不覆寫輸入")
    ap.add_argument("--state-every", type=int, default=100, help="每 N 個計分日存一次快照（0＝只在收尾存）")
    ap.add_argument("--window", type=int, default=RS.WINDOW_N)
    ap.add_argument("--limit-days", type=int, default=None)
    ap.add_argument("--resume", action="store_true", help="讀快照、從 last_date 之後續跑")
    ap.add_argument("--rebuild", action="store_true", help="先刪掉該 data_version 的全部列與快照")
    ap.add_argument("--no-fundamentals", action="store_true", help="不接 13b 基本面橋（初爻族 A/B/C 整段缺值；進參數指紋）")
    ap.add_argument("--progress-every", type=int, default=200)
    ap.add_argument("--quiet", action="store_true")
    return ap


def main(argv=None) -> int:
    return run(build_parser().parse_args(argv))


if __name__ == "__main__":
    raise SystemExit(main())
