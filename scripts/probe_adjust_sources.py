#!/usr/bin/env python3
"""裁定 #51 前置探測：FinMind 三個「減資／分割／面額變更」事件源的實況（在 Hetzner 跑，**唯讀**）。

    python3 scripts/probe_adjust_sources.py --out cache/logs/probe_adjust_sources.json 2>&1 | tee cache/logs/probe_adjust_sources.txt

背景（`docs/P3-DATASET.md` §6.2／§7、`docs/P2-KICKOFF.md` #51）：`factors.json` 的事件源只有 `TaiwanStockDividendResult`，
減資／分割／面額變更的恢復買賣參考價不在裡面，3095／6415／6763／2364 的 `fwd_ret` 出現 ×12／÷4／÷10。要把
`TaiwanStockCapitalReductionReferencePrice`／`TaiwanStockSplitPrice`／`TaiwanStockParValueChange` 寫成 DatasetSpec 之前，
**不探就只能猜**的七件事：

  P1 權限／可打性：現有 token 打三個資料集是不是 200（走 `iching.fm` 的分類：permission／quota／error 分開記）。
  P2 全市場區間查詢怪癖：不帶 data_id 的區間查詢 vs 逐日 start=end=d 合計（列數＋distinct date）。
     `TaiwanStockDividendResult` 已知**全市場區間查詢只回 start_date 當天**（2026-09-15 RCA），這三個表不能假設沒有同型怪癖。
  P3 `date` 語意：逐檔查全期，把回列與 `cache/prices.db` `raw_price_daily` 的前一交易日／同日／後一交易日 close 並列，
     讓人判斷 `date` 是「恢復買賣日」還是「最後交易日」（`adjust.py` 的 `ex_date ≤ t` 規則要對齊它）。
  P4 欄名／型別實況：每個資料集回應的欄名集合與各欄型別樣本（DatasetSpec 的欄位不憑印象寫）。
  P5 空日行為：逐日查詢中無事件的日子與非交易日各回什麼（fm 分類 empty＝HTTP 200 且 data 空）。
  P6 跨表重疊：SplitPrice 與 ParValueChange 對同一 (stock_id, date) 是否各出一列（係數合併規則要不要去重）。
  P7 量級：三個資料集 2020～2026 逐年區間列數（P2 證明區間查詢有怪癖時，逐年數字標「不可信」）。

呼叫上限（`--max-calls`，預設 150）：P1 3（＝2022 全年區間，P2／P6／P7 重用）＋P2 三段逐日約 70＋三段區間 3＋P3 4 檔×3 表 12
＋P7 逐年 18（2022 重用 P1）≈ 106；超過上限立即中止並寫出已得結果。預設節流 `config.DEFAULT_INTERVAL_SEC`（0.7 s）全程約 3～5 分鐘。

唯讀保證：`prices.db` 以 `file:…?mode=ro` 開；不建表、不寫 DB；只寫 `--out` 這一份 JSON。
token：由 `iching.fm.FinMind` 自 `FINMIND_TOKEN`／`--env-file` lazy 載入、走 Authorization header；本腳本**不印、不寫、不碰**它
（例外訊息一律過 `fm.redact`；CANON 第 1 條）。任一資料集 P1 失敗（permission／quota／error）→ 後續各 P 對它跳過並標記。
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import math
import sqlite3
import sys
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "src"))

from iching import config as C  # noqa: E402
from iching.fm import FinMind, FinMindError, PermissionRequired, QuotaExceeded, redact  # noqa: E402

# 三個事件源；key 是本腳本內的短名（日後 DatasetSpec.key 可沿用）
DATASETS: dict[str, str] = {
    "capred": "TaiwanStockCapitalReductionReferencePrice",
    "split": "TaiwanStockSplitPrice",
    "parvalue": "TaiwanStockParValueChange",
}
# 每表「before／after 價」欄名的**假設**（P3 並列用；P4 印出實際欄名，假設錯了就照 P4 改）
PRICE_PAIR: dict[str, tuple[str, str]] = {
    "capred": ("ClosingPriceonTheLastTradingDay", "PostReductionReferencePrice"),
    "split": ("before_price", "after_price"),
    "parvalue": ("before_close", "after_ref_close"),
}
# P2 逐日窗（各自對應 §6.2 已知事件；含 2022-10-10 國慶日與週末＝P5 的非交易日樣本）
DAILY_WINDOWS: tuple[tuple[str, str, str, str], ...] = (
    ("capred", "3095", "2022-10-10", "2022-10-31"),
    ("split", "6415", "2022-07-01", "2022-07-15"),
    ("parvalue", "6763", "2024-08-01", "2024-09-02"),
)
YEAR_RANGE = ("2022-01-01", "2022-12-31")            # P1／P2 全市場區間；P6 join；P7 的 2022 列
P3_STOCKS: tuple[str, ...] = ("3095", "6415", "6763", "2364")
P7_YEARS: tuple[int, ...] = tuple(range(2020, 2027))
ALL_PROBES: tuple[str, ...] = ("P1", "P2", "P3", "P4", "P5", "P6", "P7")
DEFAULT_MAX_CALLS = 150
PRICE_TOL = 0.15                                       # P3 價格相符容差（|log(a/b)|，約 ±16%）


class BudgetExceeded(RuntimeError):
    """呼叫次數超過 --max-calls：立即中止（探測不該把小時額度吃掉）。"""


class Client:
    """把 `FinMind.get` 包成「永不拋、回 (kind, rows, msg)」＋計數＋上限＋結果快取（同一查詢不重打）。"""

    def __init__(self, fm: Any, max_calls: int) -> None:
        self.fm = fm
        self.max_calls = max_calls
        self.n_calls = 0
        self.log: list[dict] = []
        self._cache: dict[tuple, tuple[str, list[dict], str]] = {}

    def get(self, dataset: str, *, data_id: str | None = None, start_date: str, end_date: str) -> tuple[str, list[dict], str]:
        key = (dataset, data_id, start_date, end_date)
        if key in self._cache:
            return self._cache[key]
        if self.n_calls >= self.max_calls:
            raise BudgetExceeded(f"已達呼叫上限 {self.max_calls}（下一個查詢：{dataset} {data_id or '全市場'} {start_date}~{end_date}）")
        self.n_calls += 1
        try:
            rows = self.fm.get(dataset, data_id=data_id, start_date=start_date, end_date=end_date)
            kind, msg = ("ok", "") if rows else ("empty", "")
        except PermissionRequired as e:
            kind, rows, msg = "permission", [], redact(str(e))
        except QuotaExceeded as e:
            kind, rows, msg = "quota", [], redact(str(e))
        except FinMindError as e:
            kind, rows, msg = "error", [], redact(str(e))
        out = (kind, [dict(r) for r in rows], msg)
        self._cache[key] = out
        self.log.append({"dataset": dataset, "data_id": data_id, "start": start_date, "end": end_date, "kind": kind, "n": len(rows)})
        return out

    def rows_for(self, dataset: str) -> list[dict]:
        """到目前為止該資料集所有查詢回的列（P4 彙總欄名用；不另打 API）。"""
        return [r for (d, *_), (_, rs, _) in self._cache.items() if d == dataset for r in rs]


# ---------------------------------------------------------------------------
# 純函式（可離線測）
# ---------------------------------------------------------------------------
def calendar_days(start: str, end: str) -> list[str]:
    d0, d1 = dt.date.fromisoformat(start), dt.date.fromisoformat(end)
    return [(d0 + dt.timedelta(days=i)).isoformat() for i in range((d1 - d0).days + 1)]


def to_float(v: Any) -> float | None:
    try:
        if v is None or v == "":
            return None
        return float(v)
    except (TypeError, ValueError):
        return None


def row_key(r: dict) -> tuple[str, str]:
    return (str(r.get("stock_id", "")), str(r.get("date", ""))[:10])


def judge_range_vs_daily(range_rows: list[dict], daily_rows: list[dict], start: str) -> dict:
    """P2 判定：區間查詢 vs 逐日合計（同一窗）。回 verdict 與差集，供人讀也供測試釘。"""
    rk, dk = {row_key(r) for r in range_rows}, {row_key(r) for r in daily_rows}
    rdates, ddates = {k[1] for k in rk}, {k[1] for k in dk}
    if len(range_rows) == len(daily_rows) and rk == dk:
        verdict = "一致"
    elif daily_rows and rdates and rdates <= {start} and ddates - {start}:
        verdict = "區間只回首日"                      # 與 TaiwanStockDividendResult 同型怪癖
    elif not range_rows and daily_rows:
        verdict = "區間回空但逐日有列"
    elif len(range_rows) < len(daily_rows) or (dk - rk):
        verdict = "區間少於逐日"
    elif len(range_rows) > len(daily_rows) or (rk - dk):
        verdict = "區間多於逐日"
    else:
        verdict = "列數相同但鍵不同"
    return {
        "verdict": verdict, "n_range": len(range_rows), "n_daily": len(daily_rows),
        "range_dates": sorted(rdates), "daily_dates": sorted(ddates),
        "only_in_range": sorted(rk - dk)[:20], "only_in_daily": sorted(dk - rk)[:20],
    }


def range_query_trusted(p2: dict | None, key: str) -> bool:
    """P7 用：該資料集所有逐日窗的 P2 判定都是「一致」才信任區間查詢。沒有 P2 結果＝未驗證＝不信任。"""
    if not p2:
        return False
    ws = [w for w in p2.get("windows", []) if w["key"] == key and "verdict" in w]
    return bool(ws) and all(w["verdict"] == "一致" for w in ws)


def date_semantics_hint(before: float | None, after: float | None,
                        prev_close: float | None, same_close: float | None, next_close: float | None) -> str:
    """P3 提示：`date` 當日 close 落在 after 附近（而非 before）→ 疑似恢復買賣日（date 是新價第一天）；
    當日 close 落在 before 附近→ 疑似最後交易日。容差 `PRICE_TOL`（log 比，約 ±16%，涵蓋恢復日的漲跌幅）；
    before 與 after 本身差不到容差就分不出。只是提示，定案由人看並列數字。"""
    def near(a: float | None, b: float | None) -> bool:
        return a is not None and b is not None and a > 0 and b > 0 and abs(math.log(a / b)) <= PRICE_TOL

    if before is None or after is None or before <= 0 or after <= 0:
        return "無法判定（before/after 缺值或非正）"
    if same_close is None:
        return "無法判定（當日無價格列＝停牌／非交易日？）"
    if near(after, before):
        return "無法判定（before≈after，區分不出）"
    same_after, same_before = near(same_close, after), near(same_close, before)
    if same_after and not same_before:
        return "疑似恢復買賣日（當日 close≈after" + ("、前一交易日≈before）" if near(prev_close, before) else "）")
    if same_before and not same_after:
        return "疑似最後交易日（當日 close≈before" + ("、後一交易日≈after）" if near(next_close, after) else "）")
    return "無法判定"


def overlap_keys(a: list[dict], b: list[dict]) -> list[tuple[str, str]]:
    return sorted({row_key(r) for r in a} & {row_key(r) for r in b})


def column_profile(rows: list[dict]) -> dict[str, dict]:
    """P4：欄名 → {types: 出現過的 Python 型別名, sample: 第一個非空值}。"""
    prof: dict[str, dict] = {}
    for r in rows:
        for k, v in r.items():
            p = prof.setdefault(k, {"types": [], "sample": None, "n_null": 0})
            t = type(v).__name__
            if v is None or v == "":
                p["n_null"] += 1
            elif p["sample"] is None:
                p["sample"] = v
            if t not in p["types"]:
                p["types"].append(t)
    return prof


# ---------------------------------------------------------------------------
# prices.db（唯讀）
# ---------------------------------------------------------------------------
class Prices:
    """`raw_price_daily` 的 close 查詢＋交易日曆（TAIEX 有列的日）；DB／表不存在時所有查詢回 None、`available=False`。"""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.conn: sqlite3.Connection | None = None
        self.available = False
        self.note = ""
        self._cal: set[str] | None = None
        if not self.path.exists():
            self.note = f"找不到 {self.path}（P3 只印回列、不並列 close）"
            return
        try:
            conn = sqlite3.connect(f"file:{self.path}?mode=ro", uri=True)
            have = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            self.conn = conn
            if "raw_price_daily" not in have:
                self.note = f"{self.path} 沒有 raw_price_daily（本機合成 DB？）——P3 只印回列、不並列 close"
                return
            cols = {r[1] for r in conn.execute('PRAGMA table_info("raw_price_daily")')}
            if not {"date", "stock_id", "close"} <= cols:
                self.note = f"raw_price_daily 缺 date/stock_id/close（實際欄位 {sorted(cols)}）"
                return
            self.available = True
            if "raw_index_price" in have:
                self._cal = {r[0] for r in conn.execute("SELECT DISTINCT date FROM raw_index_price WHERE stock_id='TAIEX'")}
        except sqlite3.Error as e:
            self.note = f"開 {self.path} 失敗：{e}"

    def close(self) -> None:
        if self.conn is not None:
            self.conn.close()

    def is_trading_day(self, d: str) -> bool | None:
        """TAIEX 日曆有列→True／無列→False；沒有日曆→None（呼叫端退回週末判斷）。"""
        if self._cal is None:
            return None
        return d in self._cal

    def closes_around(self, stock_id: str, d: str) -> dict[str, Any]:
        out = {"prev": None, "prev_date": None, "same": None, "next": None, "next_date": None}
        if not self.available or self.conn is None:
            return out
        q = self.conn.execute
        r = q("SELECT close FROM raw_price_daily WHERE stock_id=? AND date=? LIMIT 1", (stock_id, d)).fetchone()
        out["same"] = to_float(r[0]) if r else None
        r = q("SELECT date, close FROM raw_price_daily WHERE stock_id=? AND date<? ORDER BY date DESC LIMIT 1", (stock_id, d)).fetchone()
        if r:
            out["prev_date"], out["prev"] = r[0], to_float(r[1])
        r = q("SELECT date, close FROM raw_price_daily WHERE stock_id=? AND date>? ORDER BY date ASC LIMIT 1", (stock_id, d)).fetchone()
        if r:
            out["next_date"], out["next"] = r[0], to_float(r[1])
        return out


# ---------------------------------------------------------------------------
# 各 P
# ---------------------------------------------------------------------------
def probe_p1(cl: Client, keys: list[str]) -> dict:
    """三個資料集各打一次 2022 全年全市場區間（P2／P6／P7 重用同一份回應）。"""
    out: dict[str, Any] = {"datasets": {}}
    print("━━ P1 權限／可打性（2022 全年全市場區間查詢，各 1 次）")
    for k in keys:
        ds = DATASETS[k]
        kind, rows, msg = cl.get(ds, start_date=YEAR_RANGE[0], end_date=YEAR_RANGE[1])
        out["datasets"][k] = {"dataset": ds, "kind": kind, "n_rows": len(rows), "msg": msg}
        flag = {"ok": "可打", "empty": "可打但 2022 全年回空（怪）", "permission": "權限不足", "quota": "額度用盡", "error": "錯誤"}[kind]
        print(f"  {ds:<48} {kind:<10} {len(rows):>6} 列  {flag}{('：' + msg) if msg else ''}")
    usable = [k for k in keys if out["datasets"][k]["kind"] in ("ok", "empty")]
    skipped = {k: out["datasets"][k]["kind"] for k in keys if k not in usable}
    out["usable"], out["skipped"] = usable, skipped
    print(f"  結論：可用 {usable}；跳過 {skipped or '無'}（跳過者後續各 P 一律標 skipped）")
    return out


def probe_p2(cl: Client, usable: list[str], prices: Prices) -> dict:
    out: dict[str, Any] = {"windows": [], "year_range_check": []}
    print("━━ P2 全市場區間查詢 vs 逐日 start=end=d（每段各 1 次區間＋逐曆日各 1 次）")
    for k, sid, s, e in DAILY_WINDOWS:
        ds = DATASETS[k]
        if k not in usable:
            out["windows"].append({"key": k, "dataset": ds, "window": [s, e], "skipped": True})
            print(f"  [{ds}] {s}~{e}：skipped（P1 未通過）")
            continue
        kind_r, range_rows, msg_r = cl.get(ds, start_date=s, end_date=e)
        days: list[dict] = []
        daily_rows: list[dict] = []
        for d in calendar_days(s, e):
            kind, rows, msg = cl.get(ds, start_date=d, end_date=d)
            wk = dt.date.fromisoformat(d).weekday() >= 5
            td = prices.is_trading_day(d)
            days.append({"date": d, "kind": kind, "n": len(rows), "weekend": wk,
                         "trading_day": (not wk) if td is None else td, "msg": msg,
                         "stock_ids": sorted({str(r.get("stock_id")) for r in rows})[:10]})
            daily_rows.extend(rows)
        j = judge_range_vs_daily(range_rows, daily_rows, s)
        w = {"key": k, "dataset": ds, "window": [s, e], "expect_stock": sid, "range_kind": kind_r, "range_msg": msg_r,
             "days": days, **j,
             "expect_stock_in_daily": any(str(r.get("stock_id")) == sid for r in daily_rows),
             "expect_stock_in_range": any(str(r.get("stock_id")) == sid for r in range_rows)}
        out["windows"].append(w)
        print(f"  [{ds}] {s}~{e}（預期含 {sid}）：區間 {j['n_range']} 列/{len(j['range_dates'])} 日 vs 逐日合計 "
              f"{j['n_daily']} 列/{len(j['daily_dates'])} 日 → **{j['verdict']}**；{sid} 在逐日={w['expect_stock_in_daily']} 在區間={w['expect_stock_in_range']}")
        if j["only_in_daily"]:
            print(f"      只在逐日：{j['only_in_daily'][:8]}")
        if j["only_in_range"]:
            print(f"      只在區間：{j['only_in_range'][:8]}")
        # 2022 全年區間（P1 那份）是否涵蓋本窗逐日抓到的列
        if s.startswith("2022"):
            _, year_rows, _ = cl.get(ds, start_date=YEAR_RANGE[0], end_date=YEAR_RANGE[1])
            yk = {row_key(r) for r in year_rows}
            miss = sorted({row_key(r) for r in daily_rows} - yk)
            chk = {"key": k, "n_year_rows": len(year_rows), "n_year_dates": len({x[1] for x in yk}),
                   "daily_keys_missing_in_year_range": miss[:20], "n_missing": len(miss)}
            out["year_range_check"].append(chk)
            print(f"      2022 全年區間 {len(year_rows)} 列/{chk['n_year_dates']} 日；本窗逐日鍵不在全年區間內：{len(miss)}")
    verdicts = {w["key"]: w.get("verdict", "skipped") for w in out["windows"]}
    out["verdicts"] = verdicts
    print(f"  結論：{verdicts}（「一致」才可用區間查詢回補；「區間只回首日」＝與 DividendResult 同型怪癖，必須逐日切片）")
    return out


def probe_p3(cl: Client, usable: list[str], prices: Prices) -> dict:
    out: dict[str, Any] = {"stocks": {}, "prices_note": prices.note, "prices_available": prices.available}
    print("━━ P3 `date` 語意：逐檔全期回列 vs raw_price_daily 前一交易日／同日／後一交易日 close")
    if prices.note:
        print(f"  ⚠ {prices.note}")
    for sid in P3_STOCKS:
        out["stocks"][sid] = {}
        for k in DATASETS:
            ds = DATASETS[k]
            if k not in usable:
                out["stocks"][sid][k] = {"skipped": True}
                continue
            kind, rows, msg = cl.get(ds, data_id=sid, start_date=C.PRICE_WARMUP_START, end_date=C.DATA_END)
            bcol, acol = PRICE_PAIR[k]
            items = []
            for r in sorted(rows, key=lambda x: str(x.get("date", ""))):
                d = str(r.get("date", ""))[:10]
                before, after = to_float(r.get(bcol)), to_float(r.get(acol))
                ca = prices.closes_around(sid, d)
                hint = date_semantics_hint(before, after, ca["prev"], ca["same"], ca["next"]) if prices.available else "（無 raw_price_daily）"
                items.append({"date": d, "before": before, "after": after, "before_col": bcol, "after_col": acol,
                              "close_prev": ca["prev"], "close_prev_date": ca["prev_date"], "close_same": ca["same"],
                              "close_next": ca["next"], "close_next_date": ca["next_date"], "hint": hint, "row": r})
            out["stocks"][sid][k] = {"kind": kind, "msg": msg, "n": len(rows), "rows": items}
            if kind in ("permission", "quota", "error"):
                print(f"  {sid} [{ds}] {kind}：{msg}")
            elif not rows:
                print(f"  {sid} [{ds}] 全期無列")
            for it in items:
                print(f"  {sid} [{ds}] date={it['date']} {bcol}={it['before']} {acol}={it['after']} | "
                      f"close 前({it['close_prev_date']})={it['close_prev']} 同日={it['close_same']} 後({it['close_next_date']})={it['close_next']} → {it['hint']}")
    hints = [it["hint"] for s in out["stocks"].values() for x in s.values() for it in x.get("rows", [])]
    n_resume = sum(h.startswith("疑似恢復") for h in hints)
    n_last = sum(h.startswith("疑似最後") for h in hints)
    out["summary"] = {"n_rows": len(hints), "n_resume_hint": n_resume, "n_last_hint": n_last}
    print(f"  結論：{len(hints)} 列中 疑似恢復買賣日 {n_resume}、疑似最後交易日 {n_last}、其餘無法判定——"
          f"請人看數字定案（`adjust.py` 的 ex_date≤t 規則要對齊）")
    return out


def probe_p4(cl: Client, usable: list[str]) -> dict:
    """不另打 API：把到目前為止所有回列（Client.log 之外的快取內容）按資料集彙總欄名與型別。"""
    out: dict[str, Any] = {}
    print("━━ P4 欄名／型別實況（彙總前面各 P 的回列，不另打 API）")
    for k, ds in DATASETS.items():
        if k not in usable:
            out[k] = {"skipped": True}
            continue
        rows = cl.rows_for(ds)
        prof = column_profile(rows)
        out[k] = {"dataset": ds, "n_rows": len(rows), "columns": sorted(prof), "profile": prof}
        print(f"  [{ds}] {len(rows)} 列，欄位 {sorted(prof)}")
        for c, p in sorted(prof.items()):
            print(f"      {c:<36} types={p['types']} null={p['n_null']} sample={p['sample']!r}")
        exp = set(PRICE_PAIR[k])
        if rows and not exp <= set(prof):
            print(f"      ⚠ 假設的 before/after 欄 {sorted(exp)} 不全在回應裡——P3 並列用的欄名要照這裡改")
    print("  結論：DatasetSpec 的欄位以上表為準；本檔頂端 PRICE_PAIR 假設若不符要改。")
    return out


def probe_p5(p2: dict | None) -> dict:
    out: dict[str, Any] = {"by_dataset": {}}
    print("━━ P5 空日行為（取自 P2 逐日結果，不另打 API）")
    if not p2:
        out["note"] = "需 P2 結果"
        print("  skipped（需 P2）")
        return out
    for w in p2["windows"]:
        if w.get("skipped"):
            continue
        days = w["days"]
        kinds_trading_empty = sorted({d["kind"] for d in days if d["trading_day"] and d["n"] == 0})
        kinds_nontrading = sorted({d["kind"] for d in days if not d["trading_day"]})
        nontrading_with_rows = [d["date"] for d in days if not d["trading_day"] and d["n"] > 0]
        errs = [(d["date"], d["kind"], d["msg"]) for d in days if d["kind"] in ("error", "quota", "permission")]
        rec = {"n_days": len(days), "n_trading_empty": sum(1 for d in days if d["trading_day"] and d["n"] == 0),
               "kinds_on_empty_trading_day": kinds_trading_empty, "n_nontrading": sum(1 for d in days if not d["trading_day"]),
               "kinds_on_nontrading_day": kinds_nontrading, "nontrading_days_with_rows": nontrading_with_rows, "errors": errs}
        out["by_dataset"][w["key"]] = rec
        print(f"  [{w['dataset']}] {len(days)} 曆日：無事件交易日 {rec['n_trading_empty']} 天回 {kinds_trading_empty}；"
              f"非交易日 {rec['n_nontrading']} 天回 {kinds_nontrading}"
              + (f"；⚠ 非交易日有列：{nontrading_with_rows}" if nontrading_with_rows else "")
              + (f"；錯誤 {errs}" if errs else ""))
    print("  結論：fm 分類 empty＝HTTP 200 且 data 空；若非交易日出現 ok（有列），`date` 可能不是交易日、切片策略要另想。")
    return out


def probe_p6(cl: Client, usable: list[str], p3: dict | None) -> dict:
    out: dict[str, Any] = {}
    print("━━ P6 跨表重疊：SplitPrice × ParValueChange 同一 (stock_id, date)（用 P3 逐檔結果＋2022 全年區間，不另打 API）")
    if p3:
        per = {}
        for sid, byk in p3["stocks"].items():
            rows_s = [it["row"] for it in byk.get("split", {}).get("rows", [])]
            rows_p = [it["row"] for it in byk.get("parvalue", {}).get("rows", [])]
            rows_c = [it["row"] for it in byk.get("capred", {}).get("rows", [])]
            per[sid] = {"split_x_parvalue": overlap_keys(rows_s, rows_p), "split_x_capred": overlap_keys(rows_s, rows_c),
                        "parvalue_x_capred": overlap_keys(rows_p, rows_c),
                        "dates": {"split": sorted({row_key(r)[1] for r in rows_s}), "parvalue": sorted({row_key(r)[1] for r in rows_p}),
                                  "capred": sorted({row_key(r)[1] for r in rows_c})}}
            print(f"  {sid}: split={per[sid]['dates']['split']} parvalue={per[sid]['dates']['parvalue']} capred={per[sid]['dates']['capred']}"
                  f" → split×parvalue 重疊 {per[sid]['split_x_parvalue'] or '無'}")
        out["per_stock"] = per
    else:
        out["per_stock_note"] = "需 P3"
        print("  逐檔：skipped（需 P3）")
    if {"split", "parvalue"} <= set(usable):
        _, ys, _ = cl.get(DATASETS["split"], start_date=YEAR_RANGE[0], end_date=YEAR_RANGE[1])
        _, yp, _ = cl.get(DATASETS["parvalue"], start_date=YEAR_RANGE[0], end_date=YEAR_RANGE[1])
        ov = overlap_keys(ys, yp)
        out["year2022_join"] = {"n_split": len(ys), "n_parvalue": len(yp), "n_overlap": len(ov), "overlap": ov[:50]}
        print(f"  2022 全年區間 join：split {len(ys)} 列 × parvalue {len(yp)} 列 → 重疊 {len(ov)} 筆 {ov[:10]}"
              "（區間查詢若有 P2 怪癖，此數字只是下界）")
    else:
        out["year2022_join"] = {"skipped": True}
    n_any = sum(len(v["split_x_parvalue"]) for v in out.get("per_stock", {}).values()) + out.get("year2022_join", {}).get("n_overlap", 0)
    out["any_overlap"] = n_any > 0
    print(f"  結論：{'有重疊——同一事件在兩表各出一列，係數合併必須去重（同鍵只套一次）' if n_any else '未見重疊——但只證明這幾檔／2022 沒有，合併規則仍要防'}")
    return out


def probe_p7(cl: Client, usable: list[str], p2: dict | None) -> dict:
    out: dict[str, Any] = {"by_dataset": {}}
    print("━━ P7 量級：逐年全市場區間列數（2022 重用 P1；區間查詢未被 P2 證明可信者標「不可信」）")
    for k, ds in DATASETS.items():
        if k not in usable:
            out["by_dataset"][k] = {"skipped": True}
            continue
        trusted = range_query_trusted(p2, k)
        years = []
        for y in P7_YEARS:
            s, e = f"{y}-01-01", (C.DATA_END if y == P7_YEARS[-1] else f"{y}-12-31")
            kind, rows, msg = cl.get(ds, start_date=s, end_date=e)
            years.append({"year": y, "kind": kind, "n_rows": len(rows), "n_dates": len({row_key(r)[1] for r in rows}),
                          "n_stocks": len({row_key(r)[0] for r in rows}), "msg": msg})
        total = sum(x["n_rows"] for x in years)
        out["by_dataset"][k] = {"dataset": ds, "range_query_trusted": trusted, "years": years, "total_rows": total}
        tag = "可信" if trusted else "不可信（P2 未證明區間查詢完整；真實量級無法由此估，需逐日切片或逐檔 data_id 抽樣另估）"
        print(f"  [{ds}] 合計 {total} 列 — {tag}")
        for x in years:
            print(f"      {x['year']}: {x['kind']:<6} {x['n_rows']:>6} 列 / {x['n_dates']:>4} 日 / {x['n_stocks']:>4} 檔{('  ' + x['msg']) if x['msg'] else ''}")
    print("  結論：可信者的合計即回補量級；不可信者只能說「至少這麼多」。")
    return out


# ---------------------------------------------------------------------------
def estimate_calls(only: list[str]) -> int:
    n = len(DATASETS)                                     # P1 一律跑
    if "P2" in only:
        n += 3 + sum(len(calendar_days(s, e)) for _, _, s, e in DAILY_WINDOWS)
    if "P3" in only:
        n += len(P3_STOCKS) * len(DATASETS)
    if "P7" in only:
        n += (len(P7_YEARS) - 1) * len(DATASETS)          # 2022 重用 P1
    return n


def parse_only(s: str | None) -> list[str]:
    if not s:
        return list(ALL_PROBES)
    got = [x.strip().upper() for x in s.split(",") if x.strip()]
    bad = [x for x in got if x not in ALL_PROBES]
    if bad:
        raise SystemExit(f"--only 含未知探測項 {bad}（可用 {ALL_PROBES}）")
    return [p for p in ALL_PROBES if p in got]


def main(argv: list[str] | None = None, fm: Any = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--env-file", default=str(REPO / ".env"), help="含 FINMIND_TOKEN=… 的 .env（不進 git；本腳本不印 token）")
    ap.add_argument("--cache-dir", default=str(REPO / "cache"), help="prices.db 位置（唯讀開）")
    ap.add_argument("--interval", type=float, default=C.DEFAULT_INTERVAL_SEC, help="FinMind 請求最小間隔秒（fm 慣例 0.7）")
    ap.add_argument("--out", default=str(REPO / "cache" / "logs" / "probe_adjust_sources.json"), help="結果 JSON")
    ap.add_argument("--only", default=None, help="逗號分隔的探測項，如 P1,P2（P1 一律先跑；P4/P5/P6 由其他 P 的回列推導）")
    ap.add_argument("--max-calls", type=int, default=DEFAULT_MAX_CALLS, help="FinMind 呼叫上限，超過立即中止")
    args = ap.parse_args(argv)
    only = parse_only(args.only)
    est = estimate_calls(only)
    print(f"probe_adjust_sources：探測項 {only}；預估 FinMind 呼叫 {est} 次（上限 {args.max_calls}）；"
          f"節流 {args.interval}s → 純節流時間約 {est * args.interval / 60:.1f} 分（不含回應延遲）")
    if est > args.max_calls:
        print(f"  ⚠ 預估 {est} 已超過上限 {args.max_calls}，跑到上限會中止並寫出已得結果")
    if fm is None:
        fm = FinMind(env_file=Path(args.env_file), min_interval=args.interval)
        try:
            if not fm.has_token():
                print("找不到 FINMIND_TOKEN（環境變數或 --env-file）", file=sys.stderr)
                return 2
        except PermissionRequired as e:
            print(f"{e}（放在 {args.env_file} 或環境變數；本腳本不會印出 token）", file=sys.stderr)
            return 2
    cl = Client(fm, args.max_calls)
    prices = Prices(Path(args.cache_dir) / "prices.db")
    res: dict[str, Any] = {"generated_at": C.taipei_now().isoformat(timespec="seconds"), "only": only,
                           "interval": args.interval, "max_calls": args.max_calls, "estimated_calls": est,
                           "datasets": DATASETS, "price_pair_assumed": PRICE_PAIR}
    rc = 0
    try:
        p1 = probe_p1(cl, list(DATASETS))
        res["P1"] = p1
        usable = p1["usable"]
        if p1["skipped"]:
            rc = 1
        p2 = p3 = None
        if "P2" in only:
            p2 = res["P2"] = probe_p2(cl, usable, prices)
        if "P3" in only:
            p3 = res["P3"] = probe_p3(cl, usable, prices)
        if "P4" in only:
            res["P4"] = probe_p4(cl, usable)
        if "P5" in only:
            res["P5"] = probe_p5(p2)
        if "P6" in only:
            res["P6"] = probe_p6(cl, usable, p3)
        if "P7" in only:
            res["P7"] = probe_p7(cl, usable, p2)
    except BudgetExceeded as e:
        res["aborted"] = str(e)
        print(f"\n[中止] {e}", file=sys.stderr)
        rc = 2
    except KeyboardInterrupt:
        res["aborted"] = "KeyboardInterrupt"
        rc = 2
    finally:
        prices.close()
        res["n_calls"] = cl.n_calls
        res["calls"] = cl.log
        res["fm_n_requests"] = getattr(fm, "n_requests", None)
        res["fm_n_quota_waits"] = getattr(fm, "n_quota_waits", None)
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(res, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        print(f"\n實際 FinMind 呼叫 {cl.n_calls} 次（fm.n_requests={res['fm_n_requests']}，額度等待 {res['fm_n_quota_waits']} 次）；JSON 已寫 {out}")
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
