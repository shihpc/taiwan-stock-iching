#!/usr/bin/env python3
"""第 13b（基本面橋）與幾個「unverified」loader 假設的探測——**唯讀**，在 Hetzner 跑。

    python3 scripts/probe_fundamentals.py
    python3 scripts/probe_fundamentals.py --out-json cache/probe-fund-$(date -u +%Y%m%d).json

要回答的問題（每一題都是「不探就只能猜」的）：

1. `raw_financial_statements` 的 `type`／`origin_name` **實際有哪些值**——`StockInputs.fundamentals` 要 9 個鍵
   （eps／eps_ly／gross_margin／gross_margin_prev_q／price_at_period_end／pretax_income／pretax_income_ly／
   equity／equity_prev_q），FinMind 的 `type` 是它自己的英文代碼、`origin_name` 是中文科目名，對應表
   **不能憑印象寫**（`config.py` 註明只免 token 打過 2330 單季 200 列）。
2. `raw_month_revenue` 有哪些欄、`create_time` 存不存在、值長什麼樣（B2.1 `available_at` 原設計依賴它，
   後改法定期限，這裡只確認欄位）。
3. `raw_total_margin.name` 的實際值——`score_io.TOTAL_MARGIN_NAME = "MarginPurchaseMoney"` 標 unverified。
4. `raw_vix` 的欄名——`score_io.VIX_COLUMN = "VIX"` 標 unverified。
5. `raw_shareholding`（若已回補）的欄名與 `NumberOfSharesIssued` 樣本。

**唯讀保證**：`file:<path>?mode=ro`，不建表、不寫 meta。形狀不對就大聲停下，不吐一份空報表。
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "src"))

from iching.feed import FeedError, columns, open_ro  # noqa: E402


def q(conn: sqlite3.Connection, sql: str, params=()) -> list[tuple]:
    return conn.execute(sql, params).fetchall()


def table_or_none(conn: sqlite3.Connection, table: str) -> set[str] | None:
    cols = columns(conn, table)
    return cols or None


def probe_financial(conn: sqlite3.Connection, sample_id: str) -> dict:
    t = "raw_financial_statements"
    cols = table_or_none(conn, t)
    if not cols:
        return {"missing": t}
    out: dict = {"columns": sorted(cols)}
    for c in ("type", "origin_name"):
        if c in cols:
            rows = q(conn, f'SELECT "{c}", COUNT(*) FROM "{t}" GROUP BY 1 ORDER BY 2 DESC')
            out[f"distinct_{c}"] = [(str(v), n) for v, n in rows]
    if {"type", "origin_name"} <= cols:
        out["type_to_origin_name"] = [(str(a), str(b), n) for a, b, n in q(
            conn, f'SELECT type, origin_name, COUNT(*) FROM "{t}" GROUP BY 1,2 ORDER BY 1,2')]
    if {"date", "stock_id", "type", "value"} <= cols:
        last = q(conn, f'SELECT MAX(date) FROM "{t}" WHERE stock_id=?', (sample_id,))[0][0]
        out["sample"] = {"stock_id": sample_id, "date": last,
                         "rows": [(str(a), str(b), v) for a, b, v in q(
                             conn, f'SELECT type, origin_name, value FROM "{t}" WHERE stock_id=? AND date=? ORDER BY type',
                             (sample_id, last))]}
        out["date_range"] = q(conn, f'SELECT MIN(date), MAX(date), COUNT(DISTINCT date), COUNT(DISTINCT stock_id) FROM "{t}"')[0]
    return out


def probe_revenue(conn: sqlite3.Connection, sample_id: str) -> dict:
    t = "raw_month_revenue"
    cols = table_or_none(conn, t)
    if not cols:
        return {"missing": t}
    out: dict = {"columns": sorted(cols), "has_create_time": "create_time" in cols}
    if "create_time" in cols:
        out["create_time_null_share"] = q(conn, f'SELECT SUM(create_time IS NULL OR create_time=""), COUNT(*) FROM "{t}"')[0]
        out["create_time_samples"] = [r[0] for r in q(conn, f'SELECT DISTINCT create_time FROM "{t}" WHERE create_time IS NOT NULL LIMIT 5')]
    sel = [c for c in ("date", "revenue_year", "revenue_month", "revenue", "create_time") if c in cols]
    if sel and "stock_id" in cols:
        out["sample"] = {"stock_id": sample_id, "cols": sel,
                         "rows": q(conn, f'SELECT {",".join(sel)} FROM "{t}" WHERE stock_id=? ORDER BY date DESC LIMIT 4', (sample_id,))}
    out["date_range"] = q(conn, f'SELECT MIN(date), MAX(date), COUNT(DISTINCT stock_id) FROM "{t}"')[0]
    return out


def probe_simple(conn: sqlite3.Connection, table: str, name_col: str | None = None, sample_cols: tuple = ()) -> dict:
    cols = table_or_none(conn, table)
    if not cols:
        return {"missing": table}
    out: dict = {"columns": sorted(cols)}
    if name_col and name_col in cols:
        out[f"distinct_{name_col}"] = [(str(v), n) for v, n in q(conn, f'SELECT "{name_col}", COUNT(*) FROM "{table}" GROUP BY 1 ORDER BY 2 DESC')]
    sel = [c for c in sample_cols if c in cols]
    if sel:
        out["sample"] = {"cols": sel, "rows": q(conn, f'SELECT {",".join(sel)} FROM "{table}" ORDER BY date DESC LIMIT 3')}
    if "date" in cols:
        out["date_range"] = q(conn, f'SELECT MIN(date), MAX(date), COUNT(*) FROM "{table}"')[0]
    return out


def render(res: dict) -> str:
    L: list[str] = []
    a = L.append
    f = res["financial_statements"]
    a("── 1. raw_financial_statements ──────────────────────────────")
    if "missing" in f:
        a("  表不存在")
    else:
        a(f"  欄位: {f['columns']}")
        a(f"  日期 {f['date_range'][0]} ~ {f['date_range'][1]}，{f['date_range'][2]} 個期別，{f['date_range'][3]} 檔")
        a(f"  type → origin_name 對應（共 {len(f.get('type_to_origin_name', []))} 組）：")
        for t, o, n in f.get("type_to_origin_name", []):
            a(f"    {t:<40} {o:<24} {n:>9,}")
        s = f.get("sample")
        if s:
            a(f"  樣本 {s['stock_id']} @ {s['date']}：")
            for t, o, v in s["rows"]:
                a(f"    {t:<40} {o:<24} {v}")
    r = res["month_revenue"]
    a("\n── 2. raw_month_revenue ─────────────────────────────────────")
    if "missing" in r:
        a("  表不存在")
    else:
        a(f"  欄位: {r['columns']}")
        a(f"  create_time 存在: {r['has_create_time']}" + (f"，空值 {r['create_time_null_share'][0]}/{r['create_time_null_share'][1]}，樣本 {r['create_time_samples']}" if r["has_create_time"] else ""))
        a(f"  日期 {r['date_range'][0]} ~ {r['date_range'][1]}，{r['date_range'][2]} 檔")
        if r.get("sample"):
            a(f"  樣本 {r['sample']['stock_id']} {r['sample']['cols']}：")
            for row in r["sample"]["rows"]:
                a(f"    {row}")
    for key, title in (("total_margin", "3. raw_total_margin（score_io 假設 name='MarginPurchaseMoney'）"),
                       ("vix", "4. raw_vix（score_io 假設欄名 'VIX'）"),
                       ("shareholding", "5. raw_shareholding（Q3 乙 回補後才有）")):
        x = res[key]
        a(f"\n── {title} ──")
        if "missing" in x:
            a("  表不存在" + ("（尚未回補，預期）" if key == "shareholding" else "（！）"))
            continue
        a(f"  欄位: {x['columns']}")
        for k, v in x.items():
            if k.startswith("distinct_"):
                a(f"  {k}: {v}")
        if x.get("sample"):
            a(f"  樣本 {x['sample']['cols']}：")
            for row in x["sample"]["rows"]:
                a(f"    {row}")
        if x.get("date_range"):
            a(f"  日期 {x['date_range'][0]} ~ {x['date_range'][1]}，{x['date_range'][2]:,} 列")
    return "\n".join(L)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="基本面橋／unverified loader 假設的探測（唯讀）")
    ap.add_argument("--cache-dir", default=str(REPO / "cache"))
    ap.add_argument("--sample-id", default="2330")
    ap.add_argument("--out-json", default=None)
    args = ap.parse_args(argv)
    cache = Path(args.cache_dir)
    conns: dict[str, sqlite3.Connection] = {}
    try:
        for name in ("fundamentals", "market", "chips"):
            conns[name] = open_ro(cache / f"{name}.db")
        res = {
            "financial_statements": probe_financial(conns["fundamentals"], args.sample_id),
            "month_revenue": probe_revenue(conns["fundamentals"], args.sample_id),
            "total_margin": probe_simple(conns["market"], "raw_total_margin", "name", ("date", "name", "TodayBalance", "YesBalance")),
            "vix": probe_simple(conns["market"], "raw_vix", None, ("date", "VIX", "vix", "close", "value")),
            "shareholding": probe_simple(conns["chips"], "raw_shareholding", None,
                                         ("date", "stock_id", "NumberOfSharesIssued", "ForeignInvestmentShares")),
        }
        print(render(res))
        if args.out_json:
            Path(args.out_json).write_text(json.dumps(res, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
            print(f"\nJSON 已寫入 {args.out_json}")
        return 0
    except FeedError as e:
        print(f"[probe 中止] {e}", file=sys.stderr)
        return 2
    finally:
        for c in conns.values():
            c.close()


if __name__ == "__main__":
    raise SystemExit(main())
