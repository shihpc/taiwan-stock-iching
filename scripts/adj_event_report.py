#!/usr/bin/env python3
"""`data/backtest/` 匯出後的「事件窗肉眼摘要」（`docs/P3-DATASET.md` §7.3 G；`scripts/hetzner_adj.sh` 第 4 步呼叫，**唯讀、不下判定**）。

    python3 scripts/adj_event_report.py --cache-dir cache --data-dir data/backtest --stocks 3095 6415 6763 2364

印三段：①manifest 摘要（六檔列數／大小／`exit_reason_counts`／`n_fwd_ret_missing`、`factor_sources` 每源筆數、`factor_anomaly_rows`）；
②每檔 `|fwd_ret| > 1` 的列數（§6 首次實跑為 4,945 列，還原係數接上減資／分割／面額變更後應大幅下降）；
③指定代號在**每一個還原事件**（四源：除權息／減資／分割／面額變更，由 `feed.load_factor_rows` 讀 `prices.db`）前後的 `fwd_ret`：
`short` 印事件窗內每一列（訊號日 T 使 T < ex ≤ x 的列＝視窗跨過事件日，另各多印前後一列），`swing`／`mid` 只印跨事件列的
筆數與極值，最後一行是該檔各 horizon 全期間的 `|fwd_ret|` 最大值。§6.2 的四檔（3095 減資 ×12、6415 分割 ÷4、6763 面額 ÷10、
2364 減資 ×6.85）在裁定 #51 前的 `fwd_ret` 是 +11.0／−0.785／−0.90／+5.85，接上後應回到 raw 連續價的量級。

只用標準庫＋`iching.feed`／`daily_core`（Hetzner Python 3.14 無 venv）。rc：0 印完／2 manifest、日曆或 DB 讀不到。
它不是驗證器——正確性由 `check_dataset.py` 守，這支只把人要看的那幾列撈出來。
"""
from __future__ import annotations

import argparse
import bisect
import csv
import gzip
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "src"))

from iching import daily_core as DC  # noqa: E402
from iching import feed as F  # noqa: E402

DEFAULT_STOCKS = ("3095", "6415", "6763", "2364")
FWD, EXIT, MKT = "fwd_ret", "exit_reason", "mkt_ret_h"


def _float(s: str) -> float | None:
    try:
        return float(s)
    except (TypeError, ValueError):
        return None


def scan_file(path: Path, watch: set[str]) -> tuple[int, int, dict[str, dict[str, tuple[str, str, str]]]]:
    """串流讀一檔：回 (列數, |fwd_ret|>1 列數, {stock_id: {date: (fwd_ret, exit_reason, mkt_ret_h)}})。"""
    n = n_gt1 = 0
    got: dict[str, dict[str, tuple[str, str, str]]] = {s: {} for s in watch}
    with gzip.open(path, "rt", encoding="utf-8", newline="") as g:
        for r in csv.DictReader(g):
            n += 1
            v = _float(r.get(FWD, ""))
            if v is not None and abs(v) > 1.0:
                n_gt1 += 1
            sid = r.get("stock_id", "")
            if sid in got:
                got[sid][r["date"]] = (r.get(FWD, ""), r.get(EXIT, ""), r.get(MKT, ""))
    return n, n_gt1, got


def manifest_summary(m: dict) -> None:
    print(f"manifest: data_version={m.get('data_version')} params_sha={m.get('params_sha')} pool_semantics={m.get('pool_semantics')} "
          f"window={m.get('window')} head={m.get('head')} data_end={m.get('calendar', {}).get('data_end')}")
    segs = m.get("segments", {})
    print("segments: " + "  ".join(f"{k}={v.get('from')}..{v.get('to')} ({v.get('n_score_days')} 日)" for k, v in segs.items()))
    total_bytes = total_rows = 0
    for name, f in sorted(m.get("files", {}).items()):
        total_bytes += int(f.get("bytes", 0))
        total_rows += int(f.get("n_rows", 0))
        print(f"  {name}: rows={f.get('n_rows')} bytes={f.get('bytes')} exit={f.get('exit_reason_counts')} "
              f"fwd_missing={f.get('n_fwd_ret_missing')} last_signal_with_fwd={f.get('last_signal_date_with_fwd_ret')}")
    print(f"  六檔合計 {total_rows} 列 {total_bytes} bytes（{total_bytes / 1e6:.1f} MB）")
    fs = m.get("factor_sources") or {}
    by = fs.get("by_source") or {}
    print("factor_sources: sources=" + str(fs.get("sources")) + "  " +
          "  ".join(f"{k}={v.get('kept')}/{v.get('rows')}(band外 {v.get('anomalies')})" for k, v in by.items()) +
          f"  split∪parvalue 去重={fs.get('cross_source_dup')}  missing_tables={fs.get('missing_tables')}")
    an = m.get("factor_anomaly_rows") or []
    print(f"factor_anomaly_rows: {len(an)} 筆（按源 band 外，只報不擋；人看）")
    for row in an[:12]:
        print(f"  {row}")
    if len(an) > 12:
        print(f"  …另 {len(an) - 12} 筆見 manifest.json")


def event_windows(sid: str, events: list[tuple[str, float, float, str]], cal: list[str], h_by: dict[str, int],
                  rows: dict[str, dict[str, tuple[str, str, str]]]) -> None:
    print(f"\n== {sid}：還原事件 {len(events)} 筆" + ("" if events else "（四源皆無此檔事件）"))
    for ex, before, after, source in events:
        factor = before / after if after else float("nan")
        pos_ex = bisect.bisect_left(cal, ex)
        in_cal = pos_ex < len(cal) and cal[pos_ex] == ex
        print(f"  事件 {ex} {source} before={before} after={after} factor={factor:.4f}" + ("" if in_cal else "（非交易日，取其後第一個交易日對位）"))
        for hz, h in h_by.items():
            by_date = rows.get(hz, {})
            lo, hi = max(0, pos_ex - h - 1), min(len(cal) - 1, pos_ex + 1)
            window = [(cal[i], i) for i in range(lo, hi + 1)]
            cross = [(d, by_date[d]) for d, i in window if d in by_date and i <= pos_ex - 1 and i + 1 + h >= pos_ex]
            if hz == "short":
                for d, i in window:
                    if d in by_date:
                        fwd, reason, mkt = by_date[d]
                        tag = "跨事件" if (i <= pos_ex - 1 and i + 1 + h >= pos_ex) else "      "
                        print(f"    short  T={d} {tag} fwd_ret={fwd or '—':>12} exit={reason or '—':<8} mkt={mkt or '—'}")
                if not any(d in by_date for d, _ in window):
                    print("    short  （事件窗內本檔無列——事件日不在匯出段，或該檔當時不在計分名單）")
                continue
            vals = [v for v in (_float(x[1][0]) for x in cross) if v is not None]
            miss = sum(1 for x in cross if x[1][0] == "")
            if cross:
                print(f"    {hz:<6} 跨事件 {len(cross)} 列：fwd_ret min={min(vals) if vals else '—'} max={max(vals) if vals else '—'} 缺值={miss}"
                      f"  exit={sorted({x[1][1] or '—' for x in cross})}")
            else:
                print(f"    {hz:<6} （跨事件列無——事件日不在匯出段，或該檔當時不在計分名單）")
    parts = []
    for hz in h_by:
        vals = [v for v in (_float(x[0]) for x in rows.get(hz, {}).values()) if v is not None]
        parts.append(f"{hz} n={len(rows.get(hz, {}))} max|fwd_ret|={max(map(abs, vals)):.4f}" if vals else f"{hz} n={len(rows.get(hz, {}))} —")
    print("  全期間 " + "  ".join(parts))


def run(args: argparse.Namespace) -> int:
    data_dir, cache = Path(args.data_dir), Path(args.cache_dir)
    try:
        m = json.loads((data_dir / "manifest.json").read_text(encoding="utf-8"))
        cal = DC.load_calendar_dates(Path(args.calendar))
    except (OSError, ValueError, DC.DailyCoreError) as e:
        print(f"[adj_event_report 中止] {type(e).__name__}: {e}", file=sys.stderr)
        return 2
    manifest_summary(m)
    dv = args.data_version or m.get("data_version")
    order = {"short": 0, "swing": 1, "mid": 2}
    h_by = {k: int((m.get("h_by_horizon") or {})[k]) for k in sorted(m.get("h_by_horizon") or {}, key=lambda k: order.get(k, 9))}
    watch = set(args.stocks)
    conn = None
    try:
        conn = F.open_ro(cache / "prices.db")
        frows, _ = F.load_factor_rows(conn, dv)
    except (F.FeedError, OSError, ValueError) as e:
        print(f"[adj_event_report 中止] {type(e).__name__}: {e}", file=sys.stderr)
        return 2
    finally:
        if conn is not None:
            conn.close()
    events: dict[str, list[tuple[str, float, float, str]]] = {s: [] for s in watch}
    for sid, d, b, a, src in frows:
        if sid in events:
            events[sid].append((str(d), float(b), float(a), str(src)))
    print("\n== 每檔 |fwd_ret| > 1 列數（§6 首次實跑合計 4,945）")
    rows_by: dict[str, dict[str, dict[str, tuple[str, str, str]]]] = {s: {} for s in watch}       # sid → hz → date → row
    total_gt1 = 0
    for name in sorted(m.get("files", {})):
        path = data_dir / name
        if not path.exists():
            print(f"  {name}: 檔案不存在")
            continue
        n, n_gt1, got = scan_file(path, watch)
        total_gt1 += n_gt1
        hz = name.rsplit(".csv.gz", 1)[0].split("_", 1)[1]
        print(f"  {name}: {n_gt1} / {n} 列")
        for sid, by_date in got.items():
            rows_by[sid].setdefault(hz, {}).update(by_date)
    print(f"  合計 {total_gt1} 列")
    for sid in args.stocks:
        event_windows(sid, sorted(events[sid]), cal, h_by, rows_by[sid])
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="回測資料出口的事件窗摘要：指定代號在還原事件前後的 fwd_ret（唯讀）")
    ap.add_argument("--cache-dir", default="cache")
    ap.add_argument("--data-dir", default="data/backtest")
    ap.add_argument("--calendar", default="data/calendar_tpe.json")
    ap.add_argument("--data-version", default=None, help="預設取 manifest.json 的 data_version")
    ap.add_argument("--stocks", nargs="+", default=list(DEFAULT_STOCKS))
    return run(ap.parse_args(argv))


if __name__ == "__main__":
    raise SystemExit(main())
