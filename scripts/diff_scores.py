#!/usr/bin/env python3
"""兩個 `scores.db` 的逐列比對（**唯讀**）——決定性／parity 的通用工具。

    python3 scripts/diff_scores.py cache/scores.db cache/scores-check.db            # 共同日期全比
    python3 scripts/diff_scores.py a.db b.db --dates 2020-01-02 2020-01-03          # 指定日期
    python3 scripts/diff_scores.py a.db b.db --data-version fm-20260911-01

比什麼：兩邊共同的日期，`ScoreStore.rows_for_day()` 還原的每一列（7 個邏輯鍵＋全部欄位）**逐位相同**；
只在一邊有的日期列出、不算差異（用 `--strict-dates` 才算）。差異列印前 `--show` 筆（鍵＋第一個不同的欄）。

用途：①同一原料重跑兩次（決定性）②`--resume`／`--from` 續跑 vs 一次跑完 ③日後每日班（Actions）產物 vs Hetzner
回補（§B3.2 parity 不變式）。回傳碼：0 全同、1 有差異、2 開檔失敗。
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "src"))

from iching.scores_io import ScoreStore, ScoreStoreError  # noqa: E402


def _key(r: dict) -> tuple:
    return (r["market"], r["horizon"], r["stock_id"], r["tpe_trading_date"], r["model_version"], r["data_version"], r["text_version"])


def diff_day(a: ScoreStore, b: ScoreStore, dv: str, d: str) -> tuple[int, int, list[str]]:
    """回 (共同鍵數, 不同列數, 訊息)。"""
    ra = {_key(r): r for r in a.rows_for_day(dv, d)}
    rb = {_key(r): r for r in b.rows_for_day(dv, d)}
    msgs: list[str] = []
    only_a, only_b = sorted(set(ra) - set(rb)), sorted(set(rb) - set(ra))
    if only_a:
        msgs.append(f"{d}: 只在 A 的列 {len(only_a)}（例 {only_a[0][:3]}）")
    if only_b:
        msgs.append(f"{d}: 只在 B 的列 {len(only_b)}（例 {only_b[0][:3]}）")
    n_diff = len(only_a) + len(only_b)
    common = sorted(set(ra) & set(rb))
    for k in common:
        x, y = ra[k], rb[k]
        if x != y:
            n_diff += 1
            col = next((c for c in x if x.get(c) != y.get(c)), "?")
            msgs.append(f"{d}: {k[:3]} 欄 {col}: A={x.get(col)!r} B={y.get(col)!r}")
    return len(common), n_diff, msgs


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="兩個 scores.db 逐列比對（唯讀）")
    ap.add_argument("a")
    ap.add_argument("b")
    ap.add_argument("--data-version", default=None)
    ap.add_argument("--dates", nargs="*", default=None)
    ap.add_argument("--strict-dates", action="store_true", help="只在一邊有的日期也算差異")
    ap.add_argument("--show", type=int, default=10)
    args = ap.parse_args(argv)
    try:
        a, b = ScoreStore(args.a, readonly=True), ScoreStore(args.b, readonly=True)
    except ScoreStoreError as e:
        print(f"[diff 中止] {e}", file=sys.stderr)
        return 2
    try:
        dvs_a = {v["data_version"] for v in a.versions()}
        dvs_b = {v["data_version"] for v in b.versions()}
        dv = args.data_version or (sorted(dvs_a & dvs_b) or [None])[-1]
        if dv is None:
            print(f"[diff 中止] 兩邊沒有共同的 data_version：A={sorted(dvs_a)} B={sorted(dvs_b)}", file=sys.stderr)
            return 2
        da, db = set(a.dates(dv)), set(b.dates(dv))
        dates = sorted(set(args.dates) if args.dates else da & db)
        missing = [d for d in dates if d not in da or d not in db]
        only = sorted(da ^ db) if not args.dates else []
        n_common = n_diff = 0
        msgs: list[str] = []
        for d in dates:
            if d in missing:
                continue
            c, n, m = diff_day(a, b, dv, d)
            n_common += c
            n_diff += n
            msgs += m
        print(f"data_version={dv}  比對日期 {len(dates) - len(missing)} 日  共同列 {n_common:,}  不同 {n_diff:,}")
        if only:
            print(f"只在一邊有的日期 {len(only)} 個（例 {only[:3]}）" + ("——計入差異（--strict-dates）" if args.strict_dates else "——不計"))
        if missing:
            print(f"指定但缺的日期 {len(missing)} 個：{missing[:5]}")
        for m in msgs[: args.show]:
            print("  " + m)
        if len(msgs) > args.show:
            print(f"  …另 {len(msgs) - args.show} 筆")
        bad = n_diff > 0 or bool(missing) or (args.strict_dates and bool(only))
        print("結果：" + ("有差異" if bad else "逐位相同"))
        return 1 if bad else 0
    finally:
        a.close()
        b.close()


if __name__ == "__main__":
    raise SystemExit(main())
