#!/usr/bin/env python3
"""三個「尚未量測」項的探測腳本——**唯讀**，在 Hetzner 上跑。

批一（`src/iching/scan.py`／`universe.py`）留下三個口徑判斷，當時只有推論、沒有數字。
本腳本把三個數字量出來，供 `docs/P2-KICKOFF.md` §5 第 31 列的裁定使用。

    probe 1  `is_traded_row()` 兩條件的落差   → 第 31 列未列，但 `universe.py` 有「尚未量測」
    probe 2  家數比分母用 `N_t` 的偏誤幅度     → 第 31 列 ②
    probe 3  後復權 vs 原始價的漲跌口徑差異     → 第 31 列 ①

**唯讀保證**：所有連線都以 `file:<path>?mode=ro` 開啟，SQLite 層級拒絕任何寫入
（實測 `INSERT` 得 `attempt to write a readonly database`）。本腳本**不建表、不寫 meta、
不碰 WAL**，因此不會動到回補產出，也不需要停掉其他 session。

用法（Hetzner，repo 根目錄）：

    python scripts/probe_features.py --probe all
    python scripts/probe_features.py --probe all --out-json cache/probe-$(date -u +%Y%m%d).json
    python scripts/probe_features.py --probe traded            # 只跑純 SQL 那支，數秒
    python scripts/probe_features.py --probe adjust --limit-days 60   # 先小跑確認跑得起來

預估耗時：probe 1 純 SQL 掃一次全表；probe 2／3 要逐日重播（`--probe all` 會跑**兩個**
`DailyScanner`，各約 22 ms/日，1,618 日合計約 1.5 分鐘，再加讀 DB 的時間）。

## 已知近似（不影響三個問題的答案，但要講清楚）

- **市場別取 `TaiwanStockInfo` 最新一列**（`universe.pool_from_info`），不是 T 日所屬市場
  ——後者在 `config.OUT_OF_SCOPE` 第 ③ 條、尚未實作。只影響轉板過的少數檔落在哪個市場桶，
  對「兩個口徑差多少」這個比較本身沒有影響（兩邊用同一份市場別）。
- **probe 3 的兩個掃描器連 MA／新高低都會不同**，不只漲跌——因為口徑的選擇是「整條價格序列
  用哪一種」，不是只換漲跌判定。報表把受影響欄位逐一列出，不要只看 `advance_ratio`。
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import time
from itertools import groupby
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "src"))

import numpy as np  # noqa: E402

from iching import universe as U  # noqa: E402
from iching.adjust import Event, cumulative_factors, factor_at  # noqa: E402
from iching.scan import DailyScanner, StockDay  # noqa: E402

PRICE_TABLE = "raw_price_daily"
INFO_TABLE = "raw_stock_info"
DIV_TABLE = "raw_dividend_result"
INDEX_TABLE = "raw_index_price"
INDEX_ID = {"twse": "TAIEX", "tpex": "TPEx"}          # 正本＝src/iching/score_io.py 的同一組對應
PRICE_SPREAD = "spread"


class ProbeError(RuntimeError):
    """資料形狀不如預期就大聲停下——探測腳本最不該做的事就是靜默回 0。"""


def open_ro(path: Path) -> sqlite3.Connection:
    if not path.exists():
        raise ProbeError(f"找不到 DB：{path}")
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    conn.execute("PRAGMA cache_size = -64000")
    conn.execute("PRAGMA temp_store = MEMORY")
    return conn


def columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {r[1] for r in conn.execute(f'PRAGMA table_info("{table}")')}


def require(conn: sqlite3.Connection, table: str, cols: set[str]) -> set[str]:
    have = columns(conn, table)
    if not have:
        raise ProbeError(f"表不存在或為空 schema：{table}")
    missing = cols - have
    if missing:
        raise ProbeError(f"{table} 缺欄位 {sorted(missing)}；實際欄位＝{sorted(have)}")
    return have


def resolve_dv(conn: sqlite3.Connection, table: str, wanted: str | None) -> str:
    """DB 裡若有多個 data_version，必須由呼叫端指定——混著算出來的數字沒有意義。"""
    got = [r[0] for r in conn.execute(f'SELECT DISTINCT data_version FROM "{table}" ORDER BY 1')]
    if not got:
        raise ProbeError(f"{table} 沒有任何列")
    if wanted:
        if wanted not in got:
            raise ProbeError(f"{table} 沒有 data_version={wanted}；有的是 {got}")
        return wanted
    if len(got) > 1:
        raise ProbeError(f"{table} 有多個 data_version {got}，請用 --data-version 指定")
    return got[0]


# ---------------------------------------------------------------------------
# 載入
# ---------------------------------------------------------------------------
def load_pool(conn: sqlite3.Connection) -> dict[str, dict]:
    """普通股池（含市場別與產業別），＝`universe.pool_from_info` 的結果。"""
    have = require(conn, INFO_TABLE, {"stock_id"})
    cols = [c for c in ("stock_id", "type", "industry_category", "stock_name", "date") if c in have]
    rows = [dict(zip(cols, r)) for r in conn.execute(f'SELECT {",".join(cols)} FROM "{INFO_TABLE}"')]
    pool = U.pool_from_info(rows)
    if not pool:
        raise ProbeError(f"{INFO_TABLE} 解不出任何池成員（{len(rows)} 列）")
    return pool


def load_factors(conn: sqlite3.Connection, dv: str) -> tuple[dict[str, tuple[list[str], list[float]]], dict]:
    """每檔的後復權累積係數。回 ({sid: (ex_dates, cum)}, 統計)。"""
    require(conn, DIV_TABLE, {"stock_id", "date", "before_price", "after_price"})
    by: dict[str, list[Event]] = {}
    seen: set[tuple[str, str]] = set()
    n_rows = n_dup = n_bad = 0
    q = (f'SELECT stock_id, date, before_price, after_price FROM "{DIV_TABLE}" '
         f"WHERE data_version=? AND date IS NOT NULL ORDER BY stock_id, date")
    for sid, d, b, a in conn.execute(q, (dv,)):
        n_rows += 1
        key = (str(sid), str(d))
        if key in seen:                       # 同一事件可能同時落在兩種 cov_key（store.py:7-9 的已知代價）
            n_dup += 1
            continue
        try:
            bf, af = float(b), float(a)
        except (TypeError, ValueError):
            n_bad += 1
            continue
        if af <= 0 or bf <= 0:
            n_bad += 1
            continue
        seen.add(key)
        by.setdefault(str(sid), []).append(Event(str(d), bf, af))
    out = {sid: cumulative_factors(evs) for sid, evs in by.items()}
    return out, {"rows": n_rows, "dup_skipped": n_dup, "bad_skipped": n_bad, "stocks": len(out)}


def load_index(conn: sqlite3.Connection, dv: str) -> dict[str, dict[str, float]]:
    require(conn, INDEX_TABLE, {"stock_id", "date", "close"})
    out: dict[str, dict[str, float]] = {}
    rev = {v: k for k, v in INDEX_ID.items()}
    q = f'SELECT date, stock_id, close FROM "{INDEX_TABLE}" WHERE data_version=?'
    for d, sid, c in conn.execute(q, (dv,)):
        mk = rev.get(str(sid))
        if mk is None or c is None:
            continue
        out.setdefault(str(d), {})[mk] = float(c)
    if not out:
        raise ProbeError(f"{INDEX_TABLE} 取不到 {sorted(INDEX_ID.values())} 的收盤")
    return out


def iter_days(conn: sqlite3.Connection, dv: str, have_spread: bool,
              start: str | None, end: str | None):
    """逐日吐 (date, [列])。**一次 ORDER BY date 串流**，不整表載入（§B3.2 第 4 點）。"""
    cols = ["date", "stock_id", U.PRICE_CLOSE, U.PRICE_VOLUME, U.PRICE_AMOUNT]
    if have_spread:
        cols.append(PRICE_SPREAD)
    where = ["data_version=?", "date IS NOT NULL"]
    params: list = [dv]
    if start:
        where.append("date>=?")
        params.append(start)
    if end:
        where.append("date<=?")
        params.append(end)
    q = f'SELECT {",".join(f_ for f_ in cols)} FROM "{PRICE_TABLE}" WHERE {" AND ".join(where)} ORDER BY date'
    cur = conn.execute(q, params)
    for d, grp in groupby(cur, key=lambda r: r[0]):
        yield str(d), [tuple(r) for r in grp]


# ---------------------------------------------------------------------------
# probe 1：is_traded_row 兩條件的落差
# ---------------------------------------------------------------------------
def probe_traded(conn: sqlite3.Connection, dv: str, pool: dict[str, dict]) -> dict:
    """四象限：close>0 × 量>0。只看池內普通股（ETF／權證／指數列不在母體）。"""
    require(conn, PRICE_TABLE, {"date", "stock_id", U.PRICE_CLOSE, U.PRICE_VOLUME})
    ids = set(pool)
    q = (f'SELECT substr(date,1,4), stock_id, "{U.PRICE_CLOSE}", "{U.PRICE_VOLUME}" '
         f'FROM "{PRICE_TABLE}" WHERE data_version=? AND date IS NOT NULL')
    per_year: dict[str, list[int]] = {}
    for y, sid, c, v in conn.execute(q, (dv,)):
        if str(sid) not in ids:
            continue
        try:
            cc, vv = float(c or 0), float(v or 0)
        except (TypeError, ValueError):
            cc = vv = 0.0
        i = (0 if cc > 0 else 2) + (0 if vv > 0 else 1)     # 0=both 1=僅close 2=僅量 3=兩者皆無
        per_year.setdefault(str(y), [0, 0, 0, 0])[i] += 1
    tot = [sum(v[i] for v in per_year.values()) for i in range(4)]
    n = sum(tot) or 1
    return {
        "rows_in_pool": sum(tot),
        "both": tot[0], "close_only": tot[1], "vol_only": tot[2], "neither": tot[3],
        "close_only_pct": 100.0 * tot[1] / n, "vol_only_pct": 100.0 * tot[2] / n,
        "per_year": {y: {"both": v[0], "close_only": v[1], "vol_only": v[2], "neither": v[3]}
                     for y, v in sorted(per_year.items())},
    }


# ---------------------------------------------------------------------------
# probe 2／3：逐日重播
# ---------------------------------------------------------------------------
def _pct(a: list[float], q: float) -> float:
    return float(np.percentile(np.asarray(a, dtype=float), q)) if a else float("nan")


def ratio_gap_pp(count: int, n_stocks: int, eligible: int) -> float | None:
    """同一個分子，分母用 `N_t` 與用 `eligible` 算出的比值差（百分點）。

    這就是第 31 列 ② 要量的東西：規格字面是 ÷`N_t`（`P1-B1-market.md:157`），
    但歷史不足 n 天的新股算在分母卻不可能在分子。兩個分母都為 0 時回 None（沒有可比的東西）。
    """
    if n_stocks <= 0 or eligible <= 0:
        return None
    return abs(count / n_stocks - count / eligible) * 100.0


def _stat(name: str, vals: list[float]) -> dict:
    if not vals:
        return {"name": name, "n": 0}
    a = np.asarray(vals, dtype=float)
    return {"name": name, "n": int(a.size), "median": float(np.median(a)),
            "p90": _pct(vals, 90), "max": float(a.max()), "mean": float(a.mean())}


def replay(conn_prices: sqlite3.Connection, dv: str, pool: dict[str, dict],
           factors: dict, index_close: dict, want_adjust: bool, want_denom: bool,
           start: str | None, end: str | None, limit_days: int | None, quiet: bool) -> dict:
    """一趟逐日重播，同時餵後復權與原始價兩個 `DailyScanner`（只在需要時才建第二個）。"""
    have = require(conn_prices, PRICE_TABLE, {"date", "stock_id", U.PRICE_CLOSE,
                                              U.PRICE_VOLUME, U.PRICE_AMOUNT})
    have_spread = PRICE_SPREAD in have
    sc_adj = DailyScanner()
    sc_raw = DailyScanner() if want_adjust else None

    denom_gap: dict[int, list[float]] = {}          # MA 窗長 → 逐日 |兩種分母算出的比值| 差
    denom_share: dict[int, list[float]] = {}        # MA 窗長 → 逐日 eligible/n_stocks
    adv_gap, upamt_gap, ma20_gap = [], [], []
    ad_last: dict[str, tuple[int, int]] = {}
    spread_adv_gap = []
    n_days = 0
    t0 = time.time()

    for d, rows in iter_days(conn_prices, dv, have_spread, start, end):
        recs_adj, recs_raw = [], []
        sp_adv: dict[str, int] = {}
        sp_n: dict[str, int] = {}
        for r in rows:
            sid = str(r[1])
            meta = pool.get(sid)
            if meta is None:
                continue
            row = {U.PRICE_CLOSE: r[2], U.PRICE_VOLUME: r[3]}
            traded = U.is_traded_row(row)
            raw_close = float(r[2]) if traded else None
            amt = float(r[4] or 0.0) if traded else None
            mk = "twse" if (meta.get("type") == "twse") else "tpex"
            ind = meta.get("industry_category") or None
            adj_close = None
            if raw_close is not None:
                dates_cum = factors.get(sid)
                adj_close = raw_close * factor_at(d, *dates_cum) if dates_cum else raw_close
            recs_adj.append(StockDay(sid, mk, ind, adj_close, amt, True))
            if sc_raw is not None:
                recs_raw.append(StockDay(sid, mk, ind, raw_close, amt, True))
            if have_spread and traded and r[5] is not None:
                sp_n[mk] = sp_n.get(mk, 0) + 1
                if float(r[5]) > 0:
                    sp_adv[mk] = sp_adv.get(mk, 0) + 1

        idx = index_close.get(d, {})
        out_adj = sc_adj.push_day(d, recs_adj, idx)
        out_raw = sc_raw.push_day(d, recs_raw, idx) if sc_raw is not None else None
        n_days += 1

        for mk, b in out_adj.breadth.items():
            if want_denom and b.n_stocks:
                for n, elig in b.ma_eligible.items():
                    denom_share.setdefault(n, []).append(elig / b.n_stocks)
                    gap = ratio_gap_pp(b.above_ma_count[n], b.n_stocks, elig)
                    if gap is not None:
                        denom_gap.setdefault(n, []).append(gap)
            if want_adjust and out_raw is not None and mk in out_raw.breadth:
                br = out_raw.breadth[mk]
                if b.advance_ratio is not None and br.advance_ratio is not None:
                    adv_gap.append(abs(b.advance_ratio - br.advance_ratio) * 100.0)
                if b.up_amount_ratio is not None and br.up_amount_ratio is not None:
                    upamt_gap.append(abs(b.up_amount_ratio - br.up_amount_ratio) * 100.0)
                r20a, r20b = b.above_ma_ratio.get(20), br.above_ma_ratio.get(20)
                if r20a is not None and r20b is not None:
                    ma20_gap.append(abs(r20a - r20b) * 100.0)
                ad_last[mk] = (b.ad_line, br.ad_line)
                if have_spread and sp_n.get(mk) and b.advance_ratio is not None:
                    spread_adv_gap.append(abs(b.advance_ratio - sp_adv.get(mk, 0) / sp_n[mk]) * 100.0)

        if not quiet and n_days % 200 == 0:
            print(f"  … {n_days} 日（{d}），{time.time() - t0:.0f}s", flush=True)
        if limit_days and n_days >= limit_days:
            break

    res: dict = {"days": n_days, "seconds": round(time.time() - t0, 1), "have_spread": have_spread}
    if want_denom:
        res["denominator"] = {
            "note": "百分點；同一日同一市場，分母用 N_t 與用 eligible 算出的 above_ma_ratio 差",
            "gap_pp": [_stat(f"MA{n}", v) for n, v in sorted(denom_gap.items())],
            "eligible_share": [_stat(f"MA{n}", v) for n, v in sorted(denom_share.items())],
        }
    if want_adjust:
        res["adjust"] = {
            "note": "百分點；後復權序列 vs 原始價序列，同一日同一市場的差",
            "advance_ratio_pp": _stat("advance_ratio", adv_gap),
            "up_amount_ratio_pp": _stat("up_amount_ratio", upamt_gap),
            "above_ma20_ratio_pp": _stat("above_ma20_ratio", ma20_gap),
            "ad_line_final": {mk: {"adjusted": a, "raw": b, "diff": a - b} for mk, (a, b) in sorted(ad_last.items())},
            "vs_official_spread_pp": _stat("後復權 vs spread 正負", spread_adv_gap) if have_spread else
                                     {"name": "spread 欄不存在，跳過", "n": 0},
        }
    return res


# ---------------------------------------------------------------------------
# 輸出
# ---------------------------------------------------------------------------
def render(res: dict) -> str:
    L: list[str] = []
    a = L.append
    a(f"data_version = {res['data_version']}   池 = {res['pool_size']} 檔"
      f"   除權息 = {res['factors']['stocks']} 檔／{res['factors']['rows']} 列"
      f"（重複略過 {res['factors']['dup_skipped']}、異常略過 {res['factors']['bad_skipped']}）")
    if "traded" in res:
        t = res["traded"]
        a("")
        a("── probe 1　is_traded_row 兩條件的落差（池內普通股的價格列）" + "─" * 12)
        a(f"{'總列數':<16}{t['rows_in_pool']:>12,}")
        a(f"{'兩條件都成立':<16}{t['both']:>12,}   ← 目前判為「有成交」")
        a(f"{'只有 close>0':<16}{t['close_only']:>12,}   ({t['close_only_pct']:.3f}%)  有參考價、零成交")
        a(f"{'只有 量>0':<16}{t['vol_only']:>12,}   ({t['vol_only_pct']:.3f}%)  畸形列")
        a(f"{'兩者皆無':<16}{t['neither']:>12,}")
        a("")
        a(f"  {'年':<6}{'both':>12}{'僅close':>10}{'僅量':>8}{'皆無':>8}")
        for y, v in t["per_year"].items():
            a(f"  {y:<6}{v['both']:>12,}{v['close_only']:>10,}{v['vol_only']:>8,}{v['neither']:>8,}")
        a("  判讀：『只有 close>0』＝單看量會少算的列；『只有 量>0』＝單看 close 會少算的列。")
        a("        兩欄都接近 0 → 兩條件其實等價，可簡化成單一條件；否則維持兩條件。")
    if "denominator" in res:
        d = res["denominator"]
        a("")
        a("── probe 2　家數比分母用 N_t 的偏誤（第 31 列 ②）" + "─" * 22)
        a(f"  {d['note']}")
        a(f"  {'窗長':<8}{'日數':>7}{'中位':>9}{'p90':>9}{'最大':>9}")
        for s in d["gap_pp"]:
            if s["n"]:
                a(f"  {s['name']:<8}{s['n']:>7,}{s['median']:>9.3f}{s['p90']:>9.3f}{s['max']:>9.3f}")
        a("  eligible / N_t（比例，1.0 ＝ 全部都有足夠歷史）：")
        for s in d["eligible_share"]:
            if s["n"]:
                a(f"  {s['name']:<8}{s['n']:>7,}{s['median']:>9.4f}{s['p90']:>9.4f}{s['max']:>9.4f}")
        a("  判讀：gap 的中位若 <0.5 個百分點，規格字面（÷N_t）造成的偏誤可忽略、維持現狀；")
        a("        若 p90／最大明顯偏大（例如 >2），暖機與新股上市潮的日子會被系統性拉低。")
    if "adjust" in res:
        j = res["adjust"]
        a("")
        a("── probe 3　後復權 vs 原始價（第 31 列 ①）" + "─" * 28)
        a(f"  {j['note']}")
        a(f"  {'欄位':<22}{'日數':>7}{'中位':>9}{'p90':>9}{'最大':>9}")
        for k in ("advance_ratio_pp", "up_amount_ratio_pp", "above_ma20_ratio_pp", "vs_official_spread_pp"):
            s = j[k]
            if s.get("n"):
                a(f"  {s['name']:<22}{s['n']:>7,}{s['median']:>9.3f}{s['p90']:>9.3f}{s['max']:>9.3f}")
            else:
                a(f"  {s['name']:<22}{'—':>7}")
        a("  騰落線（AD）期末累計值——**這欄會累積，看的是總差不是逐日差**：")
        for mk, v in j["ad_line_final"].items():
            a(f"    {mk:<6} 後復權 {v['adjusted']:>9,}   原始價 {v['raw']:>9,}   差 {v['diff']:>+8,}")
        a("  判讀：逐日差的中位數通常很小（只有除權息日會差），但 AD 是累積量——")
        a("        期末差若佔 AD 量級的相當比例，二爻族 D（騰落線偏離）會被口徑選擇實質影響。")
    return "\n".join(L)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="三個待量測項的探測（唯讀）")
    ap.add_argument("--cache-dir", default=str(REPO / "cache"))
    ap.add_argument("--data-version", default=None)
    ap.add_argument("--probe", choices=("traded", "denominator", "adjust", "all"), default="all")
    ap.add_argument("--from", dest="start", default=None)
    ap.add_argument("--to", dest="end", default=None)
    ap.add_argument("--limit-days", type=int, default=None, help="只跑前 N 個交易日（煙霧測試）")
    ap.add_argument("--out-json", default=None)
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args(argv)

    cache = Path(args.cache_dir)
    prices = uni = None
    try:
        # 開檔本身就會拋 ProbeError（DB 不存在），所以**必須在 try 內**——
        # 初版寫在 try 外，`--cache-dir` 指錯路徑時例外直接逃出 main()、吐 traceback 而不是 exit 2。
        prices = open_ro(cache / "prices.db")
        uni = open_ro(cache / "universe.db")
        dv = resolve_dv(prices, PRICE_TABLE, args.data_version)
        pool = load_pool(uni)
        factors, fstat = load_factors(prices, resolve_dv(prices, DIV_TABLE, args.data_version))
        res: dict = {"data_version": dv, "pool_size": len(pool), "factors": fstat,
                     "range": {"from": args.start, "to": args.end, "limit_days": args.limit_days}}
        if args.probe in ("traded", "all"):
            if not args.quiet:
                print("probe 1：掃 raw_price_daily …", flush=True)
            res["traded"] = probe_traded(prices, dv, pool)
        want_denom = args.probe in ("denominator", "all")
        want_adjust = args.probe in ("adjust", "all")
        if want_denom or want_adjust:
            if not args.quiet:
                print(f"probe 2/3：逐日重播（{'兩個' if want_adjust else '一個'}掃描器）…", flush=True)
            idx = load_index(prices, resolve_dv(prices, INDEX_TABLE, args.data_version))
            res.update(replay(prices, dv, pool, factors, idx, want_adjust, want_denom,
                              args.start, args.end, args.limit_days, args.quiet))
        print()
        print(render(res))
        if args.out_json:
            Path(args.out_json).write_text(json.dumps(res, ensure_ascii=False, indent=2), encoding="utf-8")
            print(f"\nJSON 已寫入 {args.out_json}")
        return 0
    except ProbeError as e:
        print(f"[probe 中止] {e}", file=sys.stderr)
        return 2
    finally:
        for c in (prices, uni):
            if c is not None:
                c.close()


if __name__ == "__main__":
    raise SystemExit(main())
