#!/usr/bin/env python3
"""P4-E1 事件層 1（公告事件展示）：由 `data/events/` 產網頁用分片 `data/web/ev/<代號前 2 字>.json`＋觀大勢 `market.json`。

    python3 scripts/build_events_web.py --root . [--out data/web/ev]

依據：裁定 #76 §14.2 Q4（層 1 做）＝v1.2.2 §7.1 路徑 1「事件展示」；契約與實測見 `docs/P4-PREVIEW.md` §17。
**純標準庫、不碰網路、只讀 `data/events/`**（不寫、不改；collect-events.yml 那支 workflow 才是寫入者）。
描述性顯示：不分類、不判方向、不給分數，只把官方公告的主旨／條款／原文整理成前端可按需讀取的分片。

## 流程

1. T＝`data/web/latest.json` 的 `date`；本站涵蓋的代號＝其 `stocks` 的鍵。
2. 保留窗＝`data/calendar_tpe.json` 中 T 往前第 20 個台北交易日（**不含**）的隔一個曆日 ～ T（含中間的週末／休市日）。
   日曆不足 20 個先行交易日時，窗起點＝日曆第一個交易日（含）。T 不在日曆 → 中止。
3. 讀窗內每個發言日的 `data/events/<日>.json.gz`；每事件取 `status=="active"` 中 `version_no` 最大的版本（沒有 active 版本的事件略過並計數）。
4. 個股分片：只收 `latest.json` 涵蓋的代號；同一檔依 發言日↓、發言時間↓、event_id↑ 排序。latest 每個代號前 2 字都寫一片
   （窗內無公告＝空片 `stocks:{}`），所以前端拿到 404 一定是「讀取失敗」、不會被誤讀成「沒有公告」。本次沒產的舊片刪除。
5. 觀大勢 `market.json`（使用者 2026-10-19 決定「最近交易日＋之後週末」）：U＝min(T, events_upto)；D＝≤U 的最近一個台北交易日（窗內）；
   清單＝發言日 D～U（含其後週末／休市日）的**全市場**公告主旨（含本站未涵蓋的代號，以 `in` 標記），每則帶發言日。
   D～U 無任何事件 → 改以前一個交易日 D' 為起點、迄日＝D 的前一曆日再試（只在窗內往前找）；全窗無事件 → 空清單（`from`／`to`＝null）。
   依 發言日↓、發言時間↓、代號↑、event_id↑ 排序；另附窗內各發言日 × 市場的公告數與全文缺失數。窗內完全沒有事件 → D＝null、清單空。

序列化同 `build_indicators.dumps`（sort_keys、`(",",":")`、ensure_ascii=False、末尾換行），先寫全部 `.tmp` 再逐一 `os.replace`。

## 回傳碼

0 成功；2 中止（latest／日曆讀不到或形狀不對、T 不在日曆、事件檔形狀不對、out 目錄不可寫）。讀檔／計算階段的中止不寫任何檔、不刪舊檔；
寫檔階段（`write_all`）若在逐一 `os.replace` 中途失敗，可能已留下部分新片——每日班此時會把整個 `data/web/ev/` 還原成 HEAD。
"""
from __future__ import annotations

import argparse
import datetime as dt
import gzip
import json
import os
import re
import sys
from pathlib import Path
from typing import Any, Mapping

SCHEMA = 1
WINDOW_TD = 20                                   # 保留窗：20 個台北交易日（主對話預設 D3）
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
EVENT_FILE_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})\.json\.gz$")
SHARD_RE = re.compile(r"^[0-9A-Za-z]{1,2}\.json$")
MARKET_FILE = "market.json"
# 個股分片每則的欄位（位置陣列，省體積）：event_id、發言日、發言時間、主旨、符合條款、事實發生日、全文缺失(0/1)、版本號、原文
STOCK_COLS = ["id", "d", "t", "s", "c", "f", "m", "v", "b"]
# 觀大勢清單每則的欄位：發言日、代號、名稱、市場（事件檔原值 sii／otc）、發言時間、主旨、符合條款、全文缺失(0/1)、本站涵蓋(0/1)
MARKET_COLS = ["d", "sid", "n", "mk", "t", "s", "c", "m", "in"]


class BuildEventsError(Exception):
    pass


def dumps(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n"


def _read_json(p: Path) -> Any:
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise BuildEventsError(f"讀不到 {p}: {e}") from e


def load_latest(root: Path) -> tuple[str, set[str]]:
    j = _read_json(root / "data" / "web" / "latest.json")
    if not isinstance(j, dict) or not isinstance(j.get("date"), str) or not DATE_RE.match(j["date"]) \
            or not isinstance(j.get("stocks"), dict):
        raise BuildEventsError("data/web/latest.json 形狀不對（date／stocks）")
    return j["date"], {str(k) for k in j["stocks"]}


def load_calendar(root: Path) -> list[str]:
    j = _read_json(root / "data" / "calendar_tpe.json")
    dates = j.get("dates") if isinstance(j, dict) else None
    if not isinstance(dates, list) or not all(isinstance(d, str) and DATE_RE.match(d) for d in dates):
        raise BuildEventsError("data/calendar_tpe.json 形狀不對（dates）")
    return sorted(set(dates))


def window_of(T: str, cal: list[str], n: int = WINDOW_TD) -> tuple[str, str]:
    """(起, 迄)，兩端皆含；起＝T 往前第 n 個交易日的隔一個曆日（日曆不足 → 日曆第一日）。"""
    if T not in cal:
        raise BuildEventsError(f"T={T} 不在 data/calendar_tpe.json")
    i = cal.index(T)
    if i - n < 0:
        return cal[0], T
    start = dt.date.fromisoformat(cal[i - n]) + dt.timedelta(days=1)
    return start.isoformat(), T


def list_event_files(root: Path) -> list[tuple[str, Path]]:
    d = root / "data" / "events"
    if not d.is_dir():
        return []
    out = []
    for p in d.iterdir():
        m = EVENT_FILE_RE.match(p.name)
        if m and p.is_file():
            out.append((m.group(1), p))
    return sorted(out)


def load_events(p: Path, date: str) -> dict:
    try:
        with gzip.open(p, "rt", encoding="utf-8") as f:
            d = json.load(f)
    except (OSError, ValueError, EOFError) as e:
        raise BuildEventsError(f"讀不到事件檔 {p}: {e}") from e
    if not isinstance(d, dict) or d.get("schema") != 1 or d.get("date") != date or not isinstance(d.get("events"), dict):
        raise BuildEventsError(f"事件檔 {p} 形狀不對（schema／date／events）")
    return d["events"]


def pick_version(versions: Any) -> dict | None:
    """status=='active' 中 version_no 最大者；沒有就 None。"""
    if not isinstance(versions, list):
        return None
    act = [v for v in versions if isinstance(v, dict) and v.get("status") == "active" and isinstance(v.get("version_no"), int)]
    return max(act, key=lambda v: v["version_no"]) if act else None


def _str_or_none(x: Any) -> str | None:
    return x if isinstance(x, str) and x != "" else None


def collect(root: Path) -> dict[str, Any]:
    T, site = load_latest(root)
    cal = load_calendar(root)
    w0, w1 = window_of(T, cal)
    files = list_event_files(root)
    events_upto = files[-1][0] if files else None
    stocks: dict[str, list[list[Any]]] = {}
    by_day: dict[str, list[list[Any]]] = {}
    counts: dict[str, dict[str, list[int]]] = {}
    n = {"events": 0, "site": 0, "not_site": 0, "no_active": 0}
    for date, p in files:
        if not (w0 <= date <= w1):
            continue
        evs = load_events(p, date)
        for eid in sorted(evs):
            ev = evs[eid]
            if not isinstance(ev, dict):
                raise BuildEventsError(f"事件檔 {p} 的 {eid} 不是物件")
            v = pick_version(ev.get("versions"))
            if v is None:
                n["no_active"] += 1
                continue
            sid, sd, st = str(ev.get("stock_id") or ""), ev.get("spoke_date"), _str_or_none(ev.get("spoke_time"))
            if sd != date or not sid:
                raise BuildEventsError(f"事件檔 {p} 的 {eid} 缺 stock_id 或 spoke_date≠檔日")
            miss = 1 if v.get("fulltext_missing") else 0
            mk = str(ev.get("market") or "")
            n["events"] += 1
            c = counts.setdefault(date, {}).setdefault(mk, [0, 0])
            c[0] += 1
            c[1] += miss
            insite = sid in site
            by_day.setdefault(date, []).append([date, sid, _str_or_none(ev.get("name")), mk, st, _str_or_none(v.get("subject")),
                                                _str_or_none(v.get("clause")), miss, 1 if insite else 0, eid])
            if insite:
                n["site"] += 1
                stocks.setdefault(sid, []).append([eid, date, st, _str_or_none(v.get("subject")), _str_or_none(v.get("clause")),
                                                   _str_or_none(v.get("fact_date")), miss, v["version_no"],
                                                   None if miss else _str_or_none(v.get("body"))])
            else:
                n["not_site"] += 1
    for sid in stocks:
        # 發言日↓、發言時間↓（None 最後）、event_id↑
        stocks[sid].sort(key=lambda r: r[0])
        stocks[sid].sort(key=lambda r: (r[1], r[2] or ""), reverse=True)
    span, rows = market_span(by_day, cal, T, events_upto, w0)
    return {"T": T, "site": site, "window": [w0, w1], "events_upto": events_upto, "stocks": stocks,
            "span": span, "day_rows": rows, "counts": counts, "n": n}


def _prev_day(d: str) -> str:
    return (dt.date.fromisoformat(d) - dt.timedelta(days=1)).isoformat()


def market_span(by_day: Mapping[str, list], cal: list[str], T: str, events_upto: str | None, w0: str
                ) -> tuple[tuple[str, str] | None, list[list[Any]]]:
    """觀大勢清單的發言日區間 (D, U) 與列（使用者 2026-10-19 決定）。U＝min(T, events_upto)；D＝≤U 的最近交易日（≥ 窗起點）；
    D～U 無事件 → D 改前一交易日、U 改 D 的前一曆日再試；全窗無事件 → (None, [])。"""
    if events_upto is None:
        return None, []
    hi = min(T, events_upto)
    for D in sorted((d for d in cal if w0 <= d <= hi), reverse=True):
        days = [d for d in by_day if D <= d <= hi]
        if days:
            rows = [r for d in days for r in by_day[d]]
            rows.sort(key=lambda r: r[1] + "\0" + r[9])                      # 代號↑、event_id↑
            rows.sort(key=lambda r: (r[0], r[4] or ""), reverse=True)        # 發言日↓、發言時間↓（None 最後）
            return (D, hi), [r[:9] for r in rows]
        hi = _prev_day(D)
    return None, []


def build_payloads(res: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    head = {"schema": SCHEMA, "date": res["T"], "events_upto": res["events_upto"], "window": list(res["window"])}
    out: dict[str, dict[str, Any]] = {}
    groups: dict[str, dict[str, Any]] = {sid[:2]: {} for sid in res["site"]}
    for sid in sorted(res["stocks"]):
        groups[sid[:2]][sid] = res["stocks"][sid]
    for g, st in groups.items():
        out[f"{g}.json"] = {**head, "cols": list(STOCK_COLS), "stocks": st}
    span = res["span"]
    days = sorted({r[0] for r in res["day_rows"]})
    out[MARKET_FILE] = {**head, "cols": list(MARKET_COLS), "from": span[0] if span else None, "to": span[1] if span else None,
                        "days": days, "rows": list(res["day_rows"]),
                        "counts": res["counts"], "n": dict(res["n"])}
    return out


def write_all(out_dir: Path, payloads: Mapping[str, Any]) -> list[Path]:
    """先全部寫 `.tmp` 再逐一 `os.replace`；成功後刪掉本次沒有的舊分片（只刪符合分片／market 檔名者）。
    `.tmp` 階段失敗時舊檔不動；`os.replace` 中途失敗則可能已有部分新片取代舊片（不回滾）——daily.yml 的 ev 步驟此時會把整個
    `data/web/ev/` 還原成 HEAD（已追蹤 checkout、新檔 clean）。"""
    try:
        out_dir.mkdir(parents=True, exist_ok=True)
        tmps = []
        for name in sorted(payloads):
            p = out_dir / name
            tmp = p.with_name(p.name + ".tmp")
            with tmp.open("w", encoding="utf-8", newline="\n") as f:
                f.write(dumps(payloads[name]))
            tmps.append((tmp, p))
        for tmp, p in tmps:
            os.replace(tmp, p)
        for p in out_dir.iterdir():
            if p.is_file() and p.name not in payloads and (SHARD_RE.match(p.name) or p.name == MARKET_FILE):
                p.unlink()
    except OSError as e:
        raise BuildEventsError(f"out 目錄不可寫：{out_dir}: {e}") from e
    return [out_dir / n for n in sorted(payloads)]


def build(root: Path, out: Path) -> tuple[list[Path], dict[str, Any]]:
    res = collect(root)
    return write_all(out, build_payloads(res)), res


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="P4-E1：公告事件網頁分片 data/web/ev/（docs/P4-PREVIEW.md §17）")
    ap.add_argument("--root", default=".", help="repo 根（讀 data/web/latest.json、data/calendar_tpe.json、data/events/）")
    ap.add_argument("--out", default="data/web/ev", help="輸出目錄（相對路徑以 --root 為準）")
    args = ap.parse_args(argv)
    root = Path(args.root)
    out = Path(args.out)
    if not out.is_absolute():
        out = root / out
    try:
        paths, res = build(root, out)
    except BuildEventsError as e:
        print(f"[build_events_web] 中止（讀檔／計算階段中止＝未寫任何檔；寫檔階段中止可能留下部分新片）：{e}", file=sys.stderr)
        return 2
    total = sum(p.stat().st_size for p in paths)
    n = res["n"]
    print(f"[build_events_web] T={res['T']} 窗 {res['window'][0]}～{res['window'][1]}；收錄截至 {res['events_upto']}；"
          f"檔數 {len(paths)}（含 {MARKET_FILE}）共 {total} bytes；窗內事件 {n['events']}（本站涵蓋 {n['site']}、未涵蓋 {n['not_site']}、"
          f"無 active 版本 {n['no_active']}）；觀大勢 {res['span']} 共 {len(res['day_rows'])} 則")
    return 0


if __name__ == "__main__":
    sys.exit(main())
