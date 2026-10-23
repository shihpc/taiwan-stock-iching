"""#77 T1 探測：GitHub Actions runner 能否連期交所夜盤表、查詢日歸屬與公布時點（docs/P4-PREVIEW.md §18.5）。

只回報事實、不落地任何資料、不帶 secret：對每個查詢日印 HTTP 狀態、嘗試次數、耗時、位元組、解析狀態
（ok＋外資多方／空方／淨額口數／no_data／格式異常 kind／網路失敗），寫進 $GITHUB_STEP_SUMMARY（若有）。
預設查兩個日期：①`night_query_date(T)`（T＝`data/web/latest.json` 的 date，讀不到退 `data/calendar_tpe.json` 的 date）
＝推論中「T 日 15:00 起夜盤」的歸屬日；②T 本身（對照組：T 日 05:00 前結束的夜盤）。`--date` 指定時只查該日。
任一查詢日網路失敗或格式異常 → exit 1；「查無資料」是合法答案（exit 0）。
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from iching import taifex_night as T  # noqa: E402

TPE = dt.timezone(dt.timedelta(hours=8))


def latest_official_date() -> str | None:
    for p in (REPO / "data" / "web" / "latest.json", REPO / "data" / "calendar_tpe.json"):
        try:
            d = json.loads(p.read_text(encoding="utf-8")).get("date")
            if d:
                return str(T.to_date(d))
        except Exception:  # noqa: BLE001
            continue
    return None


def probe_one(qd: str) -> dict:
    out: dict = {"query_date": qd}
    try:
        r = T.fetch_raw(qd)
    except T.NightNetworkError as e:
        out.update(state="network_error", status=e.status, attempts=e.attempts, error=str(e))
        return out
    out.update(status=r.status, attempts=r.attempts, elapsed_sec=r.elapsed_sec, bytes=len(r.html.encode("utf-8")))
    try:
        p = T.parse_night(r.html, qd)
    except T.NightFormatError as e:
        out.update(state="format_error", kind=e.kind, error=str(e))
        return out
    if p is None:
        out.update(state="no_data")
    else:
        out.update(state="ok", **{k: p[k] for k in ("long", "short", "net", "net_amt_k", "raw_sha256")})
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="期交所夜盤外資淨口數探測（不落地、不帶 secret）")
    ap.add_argument("--date", default="", help="查詢日 YYYY-MM-DD 或 YYYY/MM/DD；空＝依最新正式卦日推算（另查該日作對照）")
    a = ap.parse_args(argv)
    now = dt.datetime.now(TPE).isoformat(timespec="seconds")
    hol = None
    if a.date:
        dates = [str(T.to_date(a.date))]
        base = None
    else:
        base = latest_official_date()
        if base is None:
            print("::error::讀不到最新正式卦日（data/web/latest.json／data/calendar_tpe.json）", file=sys.stderr)
            return 1
        hol = T.load_holidays()
        dates = [T.night_query_date(base, hol), base]
    head = {"now_taipei": now, "base_date": base,
            "holidays": None if a.date else ("loaded" if hol is not None else "unavailable (weekends only)")}
    print(json.dumps(head, ensure_ascii=False))
    results = []
    for qd in dates:
        res = probe_one(qd)
        results.append(res)
        print(json.dumps(res, ensure_ascii=False))
    summ = os.environ.get("GITHUB_STEP_SUMMARY")
    if summ:
        lines = ["## probe-taifex", "", f"- 台北時間：{now}", f"- 最新正式卦日 T：{base}", f"- 行事曆：{head['holidays']}", "",
                 "| 查詢日 | 狀態 | HTTP | 嘗試 | 秒 | 外資多 | 外資空 | 外資淨 |", "|---|---|---|---|---|---|---|---|"]
        for r in results:
            st = r["state"] + (f"（{r['kind']}）" if r.get("kind") else "")
            lines.append(f"| {r['query_date']} | {st} | {r.get('status')} | {r.get('attempts')} | {r.get('elapsed_sec', '')} | "
                         f"{r.get('long', '')} | {r.get('short', '')} | {r.get('net', '')} |")
        with open(summ, "a", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
    return 1 if any(r["state"] in ("network_error", "format_error") for r in results) else 0


if __name__ == "__main__":
    sys.exit(main())
