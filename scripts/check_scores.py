#!/usr/bin/env python3
"""`scores.db` 落地後的健檢報表（**唯讀**）。

`replay_scores.py` 自己只保證「日期完整」；這支看的是**數字合不合理**。

    python3 scripts/check_scores.py                     # 預設 cache/scores.db
    python3 scripts/check_scores.py path/to/scores.db [--data-version fm-…]

判讀要點：
- **版本表**：每個 data_version 應恰有 2 筆（twse／tpex 各一個 model_version）；多於 2 筆＝混了不同參數的列。
- **列數**：`scores` ≈ 日數 × (6 ＋ 3 × 每日檔數)；`replay_day` ＝ 日數。
- **排名池**：頭 60 個交易日 `n_in_pool` 應為 0（ADV 暖機），之後穩定在數百檔（`docs/pre-registration.md` §1.1 訓練段快照 901）。
- **未知爻**：`n_stock_any_unknown` 在 13b 未接前 ≈ `n_stocks`（初爻整條缺）；接上後應大幅下降。
  頭 250 個交易日 `P_hist(250)` 缺是預先登錄接受的（大盤五爻族 C／個股部分族），之後仍高就要查。
- **`lines_formal` 非 NULL 比例**：六爻皆有狀態才有正式卦；比例低＝某爻長期未知（看「各爻未知率」那段）。
- **卦分布**：末日 64 卦不應集中在 1–2 個卦；`hexagram_name` 為 NULL 的列＝`lines_formal` NULL。
- **耗時**：`elapsed_ms` 的 p50／p90／max，估全量時間用它而不是分段相加（第 12 項的教訓）。
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from collections import Counter


def pct(a: float, b: float) -> str:
    return f"{a / b * 100:.1f}%" if b else "—"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="scores.db 健檢（唯讀）")
    ap.add_argument("db", nargs="?", default="cache/scores.db")
    ap.add_argument("--data-version", default=None)
    args = ap.parse_args(argv)
    try:
        c = sqlite3.connect(f"file:{args.db}?mode=ro", uri=True)
        c.execute("SELECT 1 FROM replay_meta LIMIT 1")
    except sqlite3.Error as e:
        print(f"[check 中止] 開不了 {args.db}：{e}", file=sys.stderr)
        return 2
    metas = c.execute("SELECT data_version, params_sha, params_json FROM replay_meta ORDER BY data_version").fetchall()
    if not metas:
        print("[check 中止] replay_meta 是空的", file=sys.stderr)
        return 2
    dv = args.data_version or metas[-1][0]
    meta = next((m for m in metas if m[0] == dv), None)
    if meta is None:
        print(f"[check 中止] replay_meta 沒有 data_version={dv}（有：{[m[0] for m in metas]}）", file=sys.stderr)
        return 2
    print(f"replay_meta: {dv} 參數指紋={meta[1]}\nparams: {meta[2]}")
    vers = c.execute("SELECT version_id, model_version, text_version FROM versions WHERE data_version=? ORDER BY version_id", (dv,)).fetchall()
    print(f"\nversions（{len(vers)} 筆，應為 2）:")
    for v in vers:
        print(f"  #{v[0]}  {v[1]}  text={v[2]}")
    vids = tuple(v[0] for v in vers)
    if not vids:
        print("[check 中止] 沒有版本列", file=sys.stderr)
        return 2
    ph = ",".join("?" for _ in vids)
    n_scores = c.execute(f"SELECT COUNT(*) FROM scores WHERE version_id IN ({ph})", vids).fetchone()[0]
    days = c.execute("SELECT MIN(date), MAX(date), COUNT(*) FROM replay_day WHERE data_version=?", (dv,)).fetchone()
    print(f"\n列數: scores={n_scores:,}  replay_day={days[2]:,}  日期 {days[0]} ~ {days[1]}")
    if not days[2]:
        print("[check 中止] replay_day 是空的", file=sys.stderr)
        return 2
    print("\n每日診斷（每 400 日取樣）:")
    rows = c.execute("SELECT date, n_stocks, n_in_pool, n_stock_any_unknown, n_market_any_unknown, elapsed_ms, index_missing "
                     "FROM replay_day WHERE data_version=? ORDER BY date", (dv,)).fetchall()
    for r in rows[::400] + ([rows[-1]] if len(rows) % 400 != 1 else []):
        print(f"  {r[0]}  檔 {r[1]:>5}  池 {r[2]:>5}  個股任一爻未知 {r[3]:>5}  大盤任一爻未知 {r[4]:>2}  {r[5]:>8.0f} ms  缺指數={r[6] or '—'}")
    el = sorted(r[5] for r in rows if r[5] is not None)
    if el:
        q = lambda p: el[min(len(el) - 1, int(p * len(el)))]  # noqa: E731
        print(f"  step 耗時 p50 {q(0.5):.0f} / p90 {q(0.9):.0f} / max {el[-1]:.0f} ms；總計 {sum(el) / 1000:.0f}s")
    print("\n缺指數的日子:", sum(1 for r in rows if r[6]))
    print("\n各爻未知率（個股列，全期間）:")
    tot = c.execute(f"SELECT COUNT(*) FROM scores WHERE version_id IN ({ph}) AND stock_id<>'__MARKET__'", vids).fetchone()[0]
    for k in range(1, 7):
        u = c.execute(f"SELECT COUNT(*) FROM scores WHERE version_id IN ({ph}) AND stock_id<>'__MARKET__' AND line_{k}_unknown=1", vids).fetchone()[0]
        print(f"  line_{k}: {pct(u, tot)}")
    mf = c.execute(f"SELECT COUNT(*) FROM scores WHERE version_id IN ({ph}) AND lines_formal IS NOT NULL", vids).fetchone()[0]
    print(f"\nlines_formal 非 NULL: {pct(mf, n_scores)}（{mf:,}/{n_scores:,}）")
    last = days[1]
    print(f"\n末日 {last} 大盤列:")
    for r in c.execute(f"SELECT market, horizon, line_1, line_2, line_3, line_4, line_5, line_6, lines_formal, hexagram_name, coverage, line_states, streaks "
                       f"FROM scores WHERE version_id IN ({ph}) AND stock_id='__MARKET__' AND date=? ORDER BY market, horizon", (*vids, last)):
        ls = " ".join("—" if x is None else f"{x:5.1f}" for x in r[2:8])
        print(f"  {r[0]:<5}{r[1]:<6} [{ls}]  formal={r[8] or '—'} {r[9] or '—':<6} {r[10]:<10} states={r[11]} streaks={r[12]}")
    print(f"\n末日 {last} 個股卦分布（short，前 8）:")
    cnt = Counter(r[0] or "（無正式卦）" for r in c.execute(
        f"SELECT hexagram_name FROM scores WHERE version_id IN ({ph}) AND stock_id<>'__MARKET__' AND horizon='short' AND date=?", (*vids, last)))
    for name, n in cnt.most_common(8):
        print(f"  {name:<8}{n:>6}")
    cov = Counter(r[0] for r in c.execute(
        f"SELECT coverage FROM scores WHERE version_id IN ({ph}) AND stock_id<>'__MARKET__' AND date=?", (*vids, last)))
    print(f"\n末日個股 coverage 分布: {dict(cov)}")
    pool = c.execute(f"SELECT SUM(in_rank_pool), COUNT(*) FROM scores WHERE version_id IN ({ph}) AND stock_id<>'__MARKET__' AND horizon='short' AND date=?", (*vids, last)).fetchone()
    print(f"末日排名池: {pool[0]}/{pool[1]}")
    c.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
