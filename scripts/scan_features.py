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

## 成本（**2026-09-13 Hetzner 全量實跑**，1,618 日 × 2,139 檔池）

    掃 1,618 日、寫 1,618 日，323.8s（200.1 ms/日）　→ **5.4 分鐘**
    落地 4,463,112 列（每日 2,758；p_cs 3,525,292 佔 79%）

**每日成本隨表變大而爬升**：125 ms（第 200 日）→ 159 → 172 → 177 → 184 → 191 → 196 → **200**
（第 1,600 日）。這是次要索引（尤其 `idx_p_cs_stock` 的隨機插入）隨表成長變貴。

**我先前的推估是錯的，記在這裡**：初版寫「推估 2–4 分鐘」，依據是三段分開量測
（`push_day` 22.4 ms ＋ `AdvTracker` 6.3 ms ＋ `write_day` 52 ms ≈ 81 ms/日）。實際末段 200 ms/日，
**未分解的約 119 ms/日** 是串流讀 `raw_price_daily`（約 300 萬列）、`feed.day_records`，
以及索引在 450 萬列規模下比 400 日小表更貴。**分段量測相加不等於全量**——
`write_day` 那格我是在 400 日的表上量到「穩定在 52 ms」就外推，而真正的表是它的 4 倍大。

**磁碟：實際 651 MiB（≈683 MB）**，約 **153 bytes/列**。跑之前先 `df -h`。

**先前推估 0.6 GB 是低估，原因很具體**：基準測試的 fixture 用 `data_version="dv"`（2 字元），
生產是 `"fm-20260911-01"`（14 字元）。隔離實測（同樣 50 日 × 3,187 列／日）：

| fixture | bytes/列 |
|---|---:|
| `dv`＝2 字元 ＋ ASCII 產業名 | 113 |
| `dv`＝2 字元 ＋ **中文**產業名 | 115 |
| **`dv`＝14 字元** ＋ ASCII 產業名 | **156** |
| `dv`＝14 字元 ＋ 中文產業名 | 158 |

**中文產業名只值 +2 bytes/列，`data_version` 值 +43**。機制：每張表都是 `WITHOUT ROWID`
且 `data_version` 是 PK 第一欄，而 `p_cs`（佔 79% 的列）有兩條次要索引、各自再帶一份完整 PK
——多出來的 12 bytes 被乘以 3。**教訓：schema 是 `WITHOUT ROWID` ＋ 字串進 PK 時，
基準 fixture 的字串長度必須比照生產**，否則量到的 bytes/列會系統性偏低。

（若日後要省空間：把 `data_version` 正規化成整數 id 可省約 26%。現階段 651 MiB 於 19 GB 可用
空間下不值得為它改 schema ＋ 重跑，列在此備查。）
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

## 輸入指紋守門（`scan_inputs`，裁定 #73 C，2026-10-09）

**起因**：例行輪（`scripts/hetzner_round.sh` 第 2 步）只跑 `--resume`＝只補寫新日。晚到的除權事件（6949 分割、1563 減資，
ex 2026-09-07）落地後，已寫日的後復權收盤變了、特徵卻不會重算，**rc 0、零訊號**；10-03 v2 全量重播（當時預設不重掃）
因此 09-07 起用了過期特徵。DB 裡查不到原料何時第一次落地，所以改記「掃描當時的輸入指紋」、續跑時拿現況比。

**記什麼**（`features_io` 的 `scan_inputs` 表；**不進參數指紋**，舊庫不會因此被拒寫）：
- ① 除權息事件：`F.load_factors_full` 回的**同一份** `{sid: (ex_dates, cum)}`（掃描實際用的那份），逐檔一列。
- ② 參考池：`PitPool` 的 `static`＋`transitions`，逐檔一列（另算整體指紋供列印）。
- ③ 逐日掃描輸入摘要：既有掃描迴圈內，對 `day_records` 吐出的 `StockDay`（代號／T 日市場／產業／**後復權**收盤／成交值）
  排序後＋當日兩個指數收盤，JSON（浮點走 `repr`）再 sha256。**刻意摘要掃描器真正吃進去的東西，而非 raw 全表**：
  ETF／權證／興櫃期等不進池的列改了不影響特徵，摘要 raw 全表會讓它們每次都誤判；反過來，事件係數、T 日池成員、
  產業別、指數任何一項變了，該日摘要必變。`in_rank_pool` 不納入——它是前幾日成交值導出的掃描狀態，不是輸入。

**何時寫**：`--rebuild` 與一般全量（非 `--resume`）在**開寫前先作廢**舊基準、掃完寫新基準（中途失敗＝沒有基準，
下次 `--resume` 走 rc 4，不會留一份與特徵表不符的）。`--resume` 守門通過時合併更新（①②換成現況、③補上新日）。

**`--resume` 怎麼比**（L＝已寫最後日）：
- 開掃前（不寫任何東西）：已寫日有沒有基準摘要、① 有沒有 ex_date ≤ L 的事件新增／消失／係數改變
  （比對器重用 `parity_check.compare_factors`；只差在 L 之後的事件不算，那只影響尚未寫的日子）。
- 掃描中：③ 逐日比，**寫第一個新日之前**（掃描走過 L 的那一刻）收斂；已寫區間內的空缺日先暫存不寫，守門通過才寫。
- ② 池指紋變了**本身不判 rc 4**，只列出變動代號：池變動影響已寫日時，該日的 `StockDay` 必然不同、由 ③ 判；
  每次 `refresh-info` 的新掛牌（舊日無列）不影響已寫日，若也判 rc 4，例行輪每逢新股掛牌就要 12.6 h 全量重播。
- 任一項有差異 → **rc 4、不寫任何列**（連 `scan_meta.last_written_at` 都不動），列前 10 筆並指示整庫重建：
  `HETZNER_REPLAY_SCAN=1 bash scripts/hetzner_replay.sh`（2026-10-09 起預設即重建，環境變數可省）。
- 舊庫（沒有基準）→ 預設 rc 4；`--adopt-inputs` 一次性把現況寫成基準（前提：自上次 `--rebuild` 後**沒有新原料落地**，
  否則等於把過期特徵洗成合法）；已有基準時 `--adopt-inputs` 拒絕（rc 2），避免洗掉守門。
- 範圍：只守特徵層。籌碼／市場／基本面等其他晚到原料讓 `scores.db` 的 `replay_scores --resume` 不回頭重算，**本版不守**。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "src"))

from iching import factor_sources as FS  # noqa: E402
from iching import feed as F  # noqa: E402
from iching import universe as U  # noqa: E402
from iching.features_io import FeatureStore, FeatureStoreError  # noqa: E402
from iching.liquidity import AdvTracker  # noqa: E402
from iching.scan import DailyScanner  # noqa: E402

WARMUP_MIN = 119          # 2 × 60 − 1，見模組 docstring
RC_STALE_INPUTS = 4       # 輸入指紋守門：已寫日的原料變了，要整庫重建（見模組 docstring「輸入指紋守門」）
INPUTS_VERSION = 1        # scan_inputs 的摘要算法版本；改算法要遞增（舊基準一律視為不符）
GUARD_SHOW = 10
REBUILD_CMD = "HETZNER_REPLAY_SCAN=1 bash scripts/hetzner_replay.sh"


def build_params(scanner: DailyScanner, adv: AdvTracker) -> dict:
    """釘進 `scan_meta` 的參數集合。**任何會改變輸出的設定都要在這裡**，否則參數指紋形同虛設。"""
    return {
        "ma_windows": list(scanner.ma_windows), "hl_windows": list(scanner.hl_windows),
        "ret_windows": list(scanner.ret_windows), "p_cs_windows": list(scanner.p_cs_windows),
        "p_cs_tie": scanner.p_cs_tie,
        "adv_window": adv.window, "adv_threshold": adv.threshold,
        "pool_semantics": U.POOL_SEMANTICS,          # 池語意（Q9，2026-09-16）：舊 features.db 的指紋不符 → 拒混寫，要 --rebuild
    }


def _dumps(v) -> str:
    """決定性 JSON：鍵排序、無空白、浮點走 `repr`（json 內建行為，可逐位還原）、NaN 照寫成 `NaN`（不與 None 混同）。"""
    return json.dumps(v, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=True)


def day_digest(recs, index_day: dict) -> str:
    """③ 某日的掃描輸入摘要：`StockDay` 去掉 `in_rank_pool`（掃描狀態、非輸入）後依代號排序，加上當日指數收盤。"""
    stocks = sorted(([r.stock_id, r.market, r.industry, r.close_adj, r.amount] for r in recs),
                    key=lambda x: (str(x[0]), repr(x)))           # 同代號重複列（不該有）時以 repr 定序，摘要仍決定性
    payload = [INPUTS_VERSION, stocks, sorted([str(k), v] for k, v in index_day.items())]
    return hashlib.sha256(_dumps(payload).encode("utf-8")).hexdigest()[:20]


def factor_entries(factors) -> dict[str, str]:
    """① `{sid: (ex_dates, cum)}` → `{sid: JSON}`（與掃描用的是同一份物件）。"""
    return {str(sid): _dumps([list(dc[0]), list(dc[1])]) for sid, dc in factors.items()}


def pool_entries(pool: U.PitPool) -> dict[str, str]:
    """② `PitPool` 的 static＋transitions，逐檔一份 JSON。"""
    return {sid: _dumps({"static": pool.static[sid], "transitions": [list(t) for t in pool.transitions.get(sid, ())]})
            for sid in pool.static}


def entries_sha(entries: dict[str, str]) -> str:
    return hashlib.sha256(_dumps(sorted(entries.items())).encode("utf-8")).hexdigest()[:12]


def factor_diffs(base: dict[str, str], cur: dict[str, str], pool_sids: set[str], last: str) -> list[str]:
    """① 池內、第一個差異的 ex_date ≤ `last` 的檔（新事件／事件消失／累積係數改變）。逐字相同的檔不碰；
    有差異才載入 `parity_check` 的比對器（`compare_factors`／`_first_factor_diff`，與 D-3 對帳 ⑤ 同一套判準）。"""
    changed = sorted(sid for sid in set(base) | set(cur) if base.get(sid) != cur.get(sid))
    if not changed:
        return []
    # 延遲載入：無差異時例行輪不背這條 import 鏈
    if str(REPO / "scripts") not in sys.path:
        sys.path.append(str(REPO / "scripts"))
    import parity_check as PC
    ref = {s: tuple(json.loads(base[s])) for s in changed if s in base}
    got = {s: tuple(json.loads(cur[s])) for s in changed if s in cur}
    counted, _uncounted, _note = PC.compare_factors(ref, got, pool_sids, last)
    label = {"只在參考": "事件已不在原料（基準有）", "只在 repo": "新事件（基準沒有）"}
    return [f"① 除權息 {fd.stock_id} ex {fd.date}：{label.get(fd.what, fd.what)}（該 ex 日累積係數 基準={fd.a!r} 現況={fd.b!r}）"
            for _sid, fd in sorted(counted.items(), key=lambda kv: (kv[1].date, kv[0]))]


class InputGuard:
    """`--resume` 的 ③ 逐日比對。`last`＝已寫最後日；`lo`＝基準與本次掃描都涵蓋的起點（更早的日子兩邊口徑不同，不比）。"""

    def __init__(self, base: dict, last: str, scan_from: str) -> None:
        self.base_days: dict[str, str] = base["day"]
        self.last = last
        self.lo = max(str(base["meta"].get("scan_from") or ""), scan_from)
        self.diffs: list[str] = []
        self.seen: set[str] = set()

    def check_day(self, d: str, digest: str) -> None:
        if d > self.last:
            return
        self.seen.add(d)
        old = self.base_days.get(d)
        if old is None:
            if d >= self.lo:
                self.diffs.append(f"③ {d}：已寫區間內出現基準沒有的交易日（整日原料晚到；之後各日的視窗都跟著變）")
        elif old != digest:
            self.diffs.append(f"③ {d}：掃描輸入摘要不同（基準 {old} → 現況 {digest}；價格修訂／事件／池／指數其一）")

    def finish(self, upto: str | None) -> list[str]:
        """`upto`＝本次實際掃到的最後一日（`--to`／`--limit-days` 可能早於 `last`；None＝一日都沒掃到，無從比「消失的日」）。"""
        if upto is None:
            return self.diffs
        hi = min(self.last, upto)
        for d in sorted(self.base_days):
            if self.lo <= d <= hi and d not in self.seen:
                self.diffs.append(f"③ {d}：基準有此交易日，現況原料沒有")
        return self.diffs


def report_stale(diffs: list[str], extra: list[str] | None = None) -> int:
    print(f"\n[scan 守門 rc={RC_STALE_INPUTS}] 已寫日的掃描輸入與基準不符，共 {len(diffs)} 筆（前 {GUARD_SHOW} 筆）：", flush=True)
    for line in diffs[:GUARD_SHOW]:
        print("  " + line, flush=True)
    if len(diffs) > GUARD_SHOW:
        print(f"  …另 {len(diffs) - GUARD_SHOW} 筆", flush=True)
    for line in extra or []:
        print("  " + line, flush=True)
    print("  --resume 只補寫新日、不會回頭重算已寫日；本次**未寫入任何列**。\n"
          f"  處置：整庫重建 `{REBUILD_CMD}`（2026-10-09 起預設即重建；會先 scan_features --rebuild 再全量重播 ≈12.6 h）。",
          flush=True)
    return RC_STALE_INPUTS


def run(args) -> int:
    if args.adopt_inputs and not args.resume:
        print("[scan 中止] --adopt-inputs 只能配 --resume（--rebuild／一般全量本來就會寫新基準）", file=sys.stderr)
        return 2
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
        factors, fstat, fsrc = F.load_factors_full(prices, F.resolve_dv(prices, F.DIV_TABLE, args.data_version))
        if not args.quiet:
            print("還原係數 " + FS.format_source_stat(fsrc), flush=True)        # 每源筆數／跨源去重／band 外（只報不擋）
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
        # --resume：參數先唯讀比對，`set_params`（會寫 scan_meta）延到輸入指紋守門通過、要寫第一列前才呼叫
        sha = fs.check_params(dv, params) if args.resume else fs.set_params(dv, params)
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

        # ---- 輸入指紋（見模組 docstring「輸入指紋守門」）----
        f_ent, p_ent = factor_entries(factors), pool_entries(pool)
        base = fs.load_inputs(dv)
        guard: InputGuard | None = None
        adopting = False
        if args.resume:
            if base is not None and args.adopt_inputs:
                print(f"[scan 中止] {fs.path} 已有 data_version={dv} 的輸入基準"
                      f"（{base['meta'].get('mode')} @ {base['meta'].get('written_at')}），拒絕 --adopt-inputs：那會洗掉守門。\n"
                      f"  要重設基準請整庫重建：{REBUILD_CMD}", file=sys.stderr)
                return 2
            if already and base is None:
                if not args.adopt_inputs:
                    print(f"\n[scan 守門 rc={RC_STALE_INPUTS}] {fs.path} 沒有 data_version={dv} 的輸入基準（scan_inputs；"
                          f"2026-10-09 之前建的舊庫，或上次全量掃描中途失敗）：無法確認已寫的 {len(already)} 日原料沒變。"
                          f"本次**未寫入任何列**。\n"
                          f"  處置①（建議）整庫重建：{REBUILD_CMD}\n"
                          f"  處置②僅當能證明自上次 --rebuild 後**沒有新原料落地**：scan_features.py --resume --adopt-inputs "
                          f"一次性把現況寫成基準", flush=True)
                    return RC_STALE_INPUTS
                adopting = True
                print(f"!! --adopt-inputs：把現況原料當成已寫 {len(already)} 日的基準。前提＝自上次 --rebuild 後沒有新原料落地"
                      f"（晚到事件／價格修訂／池變動）；前提不成立等於把過期特徵洗成合法、之後守門再也看不出來。", flush=True)
            elif already:
                last = max(already)
                pre = [f"已寫日 {d} 沒有基準摘要（基準涵蓋 {min(base['day'], default='—')}～{max(base['day'], default='—')}）"
                       for d in sorted(already - set(base["day"]))]
                pre += factor_diffs(base["factor"], f_ent, set(base["pool"]) | set(p_ent), last)
                info = []
                if entries_sha(base["pool"]) != entries_sha(p_ent):
                    moved = sorted(s_ for s_ in set(base["pool"]) | set(p_ent) if base["pool"].get(s_) != p_ent.get(s_))
                    info.append(f"② 池指紋 {entries_sha(base['pool'])} → {entries_sha(p_ent)}，{len(moved)} 檔變動"
                                f"（前 {GUARD_SHOW}：{moved[:GUARD_SHOW]}）；是否影響已寫日由 ③ 判")
                if pre:
                    return report_stale(pre, info)
                for line in info:
                    print("[scan 守門] " + line, flush=True)
                guard = InputGuard(base, last, scan_from)
        if guard is None:
            if args.resume:
                fs.set_params(dv, params)                      # 沒有守門（空庫或 --adopt-inputs）：照舊在寫入前釘參數
            else:
                fs.clear_inputs(dv)                            # 非 --resume：開寫前作廢舊基準（中途失敗＝沒有基準）

        print(f"data_version={dv} 參數指紋={sha} 池={len(pool)} 檔 除權息={fstat['stocks']} 檔\n"
              f"掃描起點={scan_from}（暖機 {warm} 日，只掃不寫） 落地起點={write_from} 出檔={fs.path}", flush=True)

        n_scan = n_write = 0
        totals: dict[str, int] = {}
        written_dates: list[str] = []
        digests: dict[str, str] = {}
        pending: list[tuple] = []                            # 守門收斂前要寫的日（已寫區間內的空缺日），通過才寫

        def write(out, d: str, rp_size: int, tracked: int, ready: int) -> None:
            nonlocal n_write
            n = fs.write_day(out, dv, rank_pool_size=rp_size, adv_tracked=tracked, adv_ready=ready)
            for k, v in n.items():
                totals[k] = totals.get(k, 0) + v
            n_write += 1
            written_dates.append(d)

        def release(upto: str | None) -> bool:
            """守門收斂：有差異回 False（呼叫端 rc 4，什麼都沒寫）；通過則釘參數、補寫暫存日。"""
            nonlocal guard
            diffs = guard.finish(upto)
            if diffs:
                return False
            guard = None
            fs.set_params(dv, params)
            for item in pending:
                write(*item)
            pending.clear()
            return True

        t0 = time.time()
        last_d = None
        for d, rows in F.iter_days(prices, dv, False, scan_from, args.end):
            if guard is not None and d > guard.last and not release(guard.last):
                return report_stale(guard.diffs)               # 寫第一個新日之前收斂
            rank_pool = adv.eligible()                       # ← PIT：先取（只吃到 T−1）
            recs, amounts = F.day_records(d, rows, pool, factors, rank_pool=rank_pool)
            digests[d] = dg = day_digest(recs, index_close.get(d, {}))
            if guard is not None:
                guard.check_day(d, dg)
            out = scanner.push_day(d, recs, index_close.get(d, {}))
            n_scan += 1
            last_d = d
            if d >= write_from and d not in already:
                item = (out, d, len(rank_pool), adv.n_tracked, adv.n_ready)
                if guard is not None:
                    pending.append(item)
                else:
                    write(*item)
            adv.push_day(d, amounts)                          # ← PIT：後推（供 T+1）
            if not args.quiet and n_scan % args.progress_every == 0:
                el = time.time() - t0
                print(f"  {n_scan} 日（{d}） 寫 {n_write}　{el:.0f}s　{el / n_scan * 1000:.1f} ms/日", flush=True)
            if args.limit_days and n_scan >= args.limit_days:
                break
        if guard is not None and not release(last_d):
            return report_stale(guard.diffs)

        # 基準：非 --resume／空庫／--adopt-inputs 整份取代；--resume 守門通過則合併（①② 換現況、③ 補新日，未掃到的舊日保留）
        merge = bool(args.resume and already and not adopting)
        scan_lo = min(digests, default=None)
        if merge and base["meta"].get("scan_from"):
            scan_lo = min(filter(None, (scan_lo, base["meta"]["scan_from"])))
        mode = "adopt" if adopting else ("resume" if args.resume else ("rebuild" if args.rebuild else "full"))
        fs.write_inputs(dv, meta={
            "version": INPUTS_VERSION, "mode": mode, "adopted": adopting or bool(merge and base["meta"].get("adopted")),
            "scan_from": scan_lo, "last_scanned": max([*digests, *(base["day"] if merge else ())], default=None),
            "factor_sha": entries_sha(f_ent), "pool_sha": entries_sha(p_ent),
            "written_at": datetime.now(timezone.utc).isoformat(timespec="seconds")},
            factor=f_ent, pool=p_ent, day=digests, keep_other_days=merge)

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
    ap.add_argument("--adopt-inputs", action="store_true",
                    help="（只配 --resume）舊庫沒有輸入基準時，一次性把現況寫成基準；前提＝自上次 --rebuild 後沒有新原料落地。"
                         "已有基準時拒絕")
    ap.add_argument("--progress-every", type=int, default=200)
    ap.add_argument("--quiet", action="store_true")
    return ap


def main(argv=None) -> int:
    return run(build_parser().parse_args(argv))


if __name__ == "__main__":
    raise SystemExit(main())
