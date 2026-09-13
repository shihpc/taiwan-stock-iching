#!/usr/bin/env python3
"""逐日掃描 → 落地 `features.db`（第 12 項）。在 Hetzner 上跑。

    讀 prices.db／universe.db（唯讀，`iching.feed`）
      → `liquidity.AdvTracker`（排名池，PIT）
      → `scan.DailyScanner`（廣度／產業聚合／P_cs）
      → `features_io.FeatureStore`（落地）

用法（**指令是 `python3`**，該機是系統 Python、無 venv）：

    python3 scripts/scan_features.py --limit-days 200        # 先小跑
    python3 scripts/scan_features.py                         # 全量
    python3 scripts/scan_features.py --rebuild               # 砍掉該 data_version 重寫
    python3 scripts/scan_features.py --from 2024-01-01 --warmup-days 130   # 部分區間（見下）

## 成本（**2026-09-13 實測各段，不是估計**；全量總時間未實跑，是三段相加的推估）

| 段 | 量測條件 | 每日 | 1,618 日 |
|---|---|---:|---:|
| `DailyScanner.push_day` | 1,900 檔 | 22.4 ms | ~36 s |
| `AdvTracker`（含每日 `eligible()`） | 2,000 檔 | 6.3 ms | ~10 s |
| `FeatureStore.write_day` | 3,187 列／日，**表已長到 200 日以上** | ~52 ms | ~85 s |

加上串流讀 `raw_price_daily`（約 300 萬列）的時間，**推估 2–4 分鐘**。

`write_day` 那格**要看表多大**：50 日的小表上是 25 ms/日，200 日與 400 日都穩定在 ~52 ms/日
（`idx_p_cs_stock` 這條隨機插入的次要索引隨表變大而變貴）。初版只寫 25 ms／~40 s，沒寫量測條件，
2026-09-13 驗收更正為大表值。

**磁碟：`features.db` 推估約 0.6 GB**（50 日實測 18.0 MB 外推）。`p_cs` 佔 85% 的列——
它是逐檔逐窗長的，本來就最大；計分引擎的 `p_cs_long_excess` 要它，不能省。
跑之前先確認 `--out` 所在磁碟有空間。

## PIT 靠呼叫順序，順序錯不會報錯

    pool = adv.eligible()          # ← 先取：此時 AdvTracker 只吃到 T−1
    ...用 pool 當 in_rank_pool 跑 T 日...
    adv.push_day(d, amounts)       # ← 後推：供 T+1 用

反過來就是「用 T 日自己的成交值決定 T 日進不進池」＝ look-ahead，**完全不會報錯**
（`liquidity.py` 模組 docstring 有同一段警告）。順序由 `tests/test_scan_features.py` 守。

## `--from` 的暖機陷阱

前向掃描的狀態全在 deque 裡。指定 `--from` 而不暖機，開頭那段的 MA60／新高低／ADV／騰落線
**全都算在半滿的視窗上**，產出的數字看起來很正常、但與全量跑出來的不一樣，而且**不會報錯**。

所以 `--from` 一律要配 `--warmup-days`：從 `--from` 往前多讀 N 個交易日**只掃不寫**。
需要的最小值是 **`WARMUP_MIN = 119`**＝`2 × 60 − 1`——那是 `ind_ad_line_dev` 對騰落線長度的
要求（`score/market.py:166` `if ad.size < 2 * n - 1`，中期 n=60 取自 `params.py` 的 `MKT_L2_WIN`），
比收盤 deque 的 61 與 ADV 的 60 都大。暖機不足時本腳本**拒絕執行**（要硬跑得明示
`--allow-short-warmup`，且會在報表標注）。

**兩點精確性（2026-09-13 驗收更正，初版兩處都講過頭）**：
- **119 是交易日，不是「該檔的有效收盤數」**。長期停牌股暖機 119 日後仍可能湊不到 60 筆有效
  收盤（`scan.py` 口徑第 1 條：視窗只取有效收盤）。「119 就夠」對大盤成立，對個別停牌股不是全稱。
- **暖機日只掃不寫，所以它不會增加 `features.db` 裡 `ad_line` 的列數**。`ind_ad_line_dev`
  要的 119 是**已落地列**的序列長度，與暖機日數是兩件事；初版把兩者混為一談。
  暖機保證的是「掃描器內部狀態是滿的」，不是「落地序列夠長」。

**`ad_line` 的絕對值隨掃描起點而不同**：消費端用 `AD − MA_n(AD)`，常數平移相消，所以不影響
分數——**前提是整段序列來自同一次掃描**。把 `--from` 的結果寫進已有更早資料的 DB 會破壞這個
前提，所以本腳本**直接拒絕**（`--allow-ad-seam` 才放行）。耐久的量是 `advance_count − decline_count`。
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "src"))

from iching import feed as F  # noqa: E402
from iching.features_io import FeatureStore, FeatureStoreError  # noqa: E402
from iching.liquidity import AdvTracker  # noqa: E402
from iching.scan import DailyScanner  # noqa: E402

WARMUP_MIN = 119          # 2 × 60 − 1，見模組 docstring


def build_params(scanner: DailyScanner, adv: AdvTracker) -> dict:
    """釘進 `scan_meta` 的參數集合。**任何會改變輸出的設定都要在這裡**，否則參數指紋形同虛設。"""
    return {
        "ma_windows": list(scanner.ma_windows), "hl_windows": list(scanner.hl_windows),
        "ret_windows": list(scanner.ret_windows), "p_cs_windows": list(scanner.p_cs_windows),
        "p_cs_tie": scanner.p_cs_tie,
        "adv_window": adv.window, "adv_threshold": adv.threshold,
    }


def run(args) -> int:
    if args.start and args.end and args.end < args.start:
        print(f"[scan 中止] --to {args.end} 早於 --from {args.start}，不會有任何日期落地",
              file=sys.stderr)
        return 2
    cache = Path(args.cache_dir)
    prices = uni = None
    fs = None
    try:
        prices, uni = F.open_ro(cache / "prices.db"), F.open_ro(cache / "universe.db")
        dv = F.resolve_dv(prices, F.PRICE_TABLE, args.data_version)
        pool = F.load_pool(uni)
        factors, fstat = F.load_factors(prices, F.resolve_dv(prices, F.DIV_TABLE, args.data_version))
        index_close = F.load_index(prices, F.resolve_dv(prices, F.INDEX_TABLE, args.data_version))

        # 暖機起點：從 --from 往前 warmup-days 個交易日
        all_dates = [r[0] for r in prices.execute(
            f'SELECT DISTINCT date FROM "{F.PRICE_TABLE}" WHERE data_version=? AND date IS NOT NULL ORDER BY date',
            (dv,))]
        if not all_dates:
            raise F.FeedError(f"{F.PRICE_TABLE} 在 data_version={dv} 下沒有任何日期")
        write_from = args.start or all_dates[0]
        warm = args.warmup_days
        if args.start:
            try:
                i = all_dates.index(args.start)
            except ValueError:
                i = next((k for k, d in enumerate(all_dates) if d >= args.start), len(all_dates))
            avail = i
            if warm > avail:
                print(f"[注意] --from {args.start} 之前只有 {avail} 個交易日可暖機（要求 {warm}）", flush=True)
                warm = avail
            if warm < WARMUP_MIN and not args.allow_short_warmup:
                raise FeatureStoreError(
                    f"暖機只有 {warm} 個交易日，少於 WARMUP_MIN={WARMUP_MIN}（＝2×60−1，騰落線長度要求）。\n"
                    f"  不暖機的話開頭那段的 MA60／新高低／ADV／騰落線都算在半滿視窗上，"
                    f"數字看起來正常但與全量跑不一樣、**不會報錯**。\n"
                    f"  請加大 --warmup-days；確定要這樣跑就加 --allow-short-warmup。")
            scan_from = all_dates[max(0, i - warm)]
        else:
            scan_from, warm = all_dates[0], 0

        scanner = DailyScanner()
        adv = AdvTracker()
        params = build_params(scanner, adv)
        fs = FeatureStore(args.out or (cache / "features.db"))
        if args.rebuild:
            deleted = fs.clear(dv)
            print(f"[rebuild] 已刪除 {dv}：{ {k: v for k, v in deleted.items() if v} }", flush=True)
        sha = fs.set_params(dv, params)
        # **AD 接縫守門**（2026-09-13 驗收抓到）：`ad_line` 從掃描起點累積，把 `--from` 的結果
        # 寫進已有更早資料的 DB，接縫兩側來自不同起點，`ad_line` 會跳一個無意義的差
        # （驗收實測 −165），rc=0、零警告。這正是本腳本 docstring 宣稱要防的那類錯。
        if args.start:
            earlier = fs.conn.execute(
                "SELECT COUNT(*), MIN(date), MAX(date) FROM scan_day WHERE data_version=? AND date<?",
                (dv, write_from)).fetchone()
            if earlier[0] and not args.allow_ad_seam:
                raise FeatureStoreError(
                    f"{fs.path} 裡已有 {earlier[0]} 個早於 {write_from} 的日期（{earlier[1]}~{earlier[2]}）。\n"
                    f"  `ad_line` 是**相對掃描起點**的累積量，接上去會在接縫留下無意義的跳動，"
                    f"而且不會報錯。\n"
                    f"  要重算整段請用 --rebuild；確定只要這段、且清楚 ad_line 會不連續，加 --allow-ad-seam。\n"
                    f"  （耐久的量是 advance_count − decline_count，本表已逐日存，重建 AD 用它 cumsum。）")
        already = set(fs.dates(dv)) if args.resume else set()
        if already:
            print(f"[resume] {dv} 已有 {len(already)} 日；**掃描仍從頭重播**（deque 需要歷史），只是不重寫", flush=True)

        print(f"data_version={dv} 參數指紋={sha} 池={len(pool)} 檔 除權息={fstat['stocks']} 檔\n"
              f"掃描起點={scan_from}（暖機 {warm} 日，只掃不寫） 落地起點={write_from} 出檔={fs.path}", flush=True)

        n_scan = n_write = 0
        totals: dict[str, int] = {}
        written_dates: list[str] = []
        t0 = time.time()
        for d, rows in F.iter_days(prices, dv, False, scan_from, args.end):
            rank_pool = adv.eligible()                       # ← PIT：先取（只吃到 T−1）
            recs, amounts = F.day_records(d, rows, pool, factors, rank_pool=rank_pool)
            out = scanner.push_day(d, recs, index_close.get(d, {}))
            n_scan += 1
            if d >= write_from and d not in already:
                n = fs.write_day(out, dv, rank_pool_size=len(rank_pool),
                                 adv_tracked=adv.n_tracked, adv_ready=adv.n_ready)
                for k, v in n.items():
                    totals[k] = totals.get(k, 0) + v
                n_write += 1
                written_dates.append(d)
            adv.push_day(d, amounts)                          # ← PIT：後推（供 T+1）
            if not args.quiet and n_scan % args.progress_every == 0:
                el = time.time() - t0
                print(f"  {n_scan} 日（{d}） 寫 {n_write}　{el:.0f}s　{el / n_scan * 1000:.1f} ms/日", flush=True)
            if args.limit_days and n_scan >= args.limit_days:
                break

        el = time.time() - t0
        print(f"\n掃 {n_scan} 日、寫 {n_write} 日，{el:.1f}s（{el / max(n_scan, 1) * 1000:.1f} ms/日）")
        print("落地列數：" + "　".join(f"{k}={v:,}" for k, v in sorted(totals.items())))
        expected = [d for d in all_dates if d >= write_from and (not args.end or d <= args.end)]
        if args.limit_days:
            expected = expected[:n_write] if written_dates else []
        miss = fs.missing_dates(dv, expected)
        if miss:
            print(f"[警告] 預期有但沒落地的日期 {len(miss)} 個，前 5 個＝{miss[:5]}")
            return 1
        if n_write == 0 and not already:
            # 「預期 0 日，全部落地」在寫 0 日時是誤導（驗收指出）。
            # **但 `--resume` 在已完整的 DB 上寫 0 日是正確的 no-op**，不能一起判錯——
            # 初版沒分這兩種，`test_rerun_is_idempotent_and_resume_skips` 立刻變紅，正是它該擋的。
            print(f"[警告] 一日都沒有落地（掃了 {n_scan} 日）。檢查 --from／--to／--limit-days 的組合。")
            return 1
        if n_write == 0:
            print(f"[resume] 要寫的 {len(expected)} 日都已存在，未新增。")
            return 0
        print(f"日期完整性：預期 {len(expected)} 日，全部落地。")
        if warm < WARMUP_MIN and args.start:
            print(f"[警告] 暖機僅 {warm} 日（< {WARMUP_MIN}），開頭區段的 MA60／騰落線與全量跑不可比。")
        return 0
    except (F.FeedError, FeatureStoreError, OSError, sqlite3.Error) as e:
        # `OSError`／`sqlite3.Error` 是為了 `--out` 指到不存在的目錄、既有目錄、唯讀掛載
        # **或磁碟寫滿**——`FeatureStore.__init__` 從那裡拋，原本不在 except 名單裡，
        # 於是吐 traceback、rc=1（2026-09-13 驗收抓到）。同型前例見 `probe_features.py` 的
        # 「open_ro 寫在 try 外」，那次是位置錯、這次是型別漏。
        print(f"[scan 中止] {e}", file=sys.stderr)
        return 2
    finally:
        for c in (prices, uni):
            if c is not None:
                c.close()
        if fs is not None:
            fs.close()


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description="逐日掃描並落地 features.db")
    ap.add_argument("--cache-dir", default=str(REPO / "cache"))
    ap.add_argument("--out", default=None, help="預設 <cache-dir>/features.db")
    ap.add_argument("--data-version", default=None)
    ap.add_argument("--from", dest="start", default=None)
    ap.add_argument("--to", dest="end", default=None)
    ap.add_argument("--warmup-days", type=int, default=WARMUP_MIN,
                    help=f"--from 之前多掃幾個交易日（只掃不寫）；最小 {WARMUP_MIN}")
    ap.add_argument("--allow-short-warmup", action="store_true")
    ap.add_argument("--allow-ad-seam", action="store_true",
                    help="允許把部分區間寫進已有更早資料的 DB（ad_line 會在接縫不連續）")
    ap.add_argument("--limit-days", type=int, default=None)
    ap.add_argument("--resume", action="store_true", help="已落地的日期不重寫（掃描仍從頭重播）")
    ap.add_argument("--rebuild", action="store_true", help="先刪掉該 data_version 的全部列")
    ap.add_argument("--progress-every", type=int, default=200)
    ap.add_argument("--quiet", action="store_true")
    return ap


def main(argv=None) -> int:
    return run(build_parser().parse_args(argv))


if __name__ == "__main__":
    raise SystemExit(main())
