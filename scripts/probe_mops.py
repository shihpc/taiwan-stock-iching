"""公告收集器前置：從 GitHub Actions runner 探測 MOPS 三個端點是否可達（P0-A 修訂表第 7 列「Actions 的 WAF 行為未驗證」）。

只回報事實、不落地任何資料：每個端點印 HTTP 狀態、位元組、content-type、猜測編碼、解析到的列數；
任一端點非 200 或列數為 0 → exit 1（使用者裁定：probe 失敗即停、不改走代抓）。
mopsov 的參數形狀在 spec §12.4 只記了名稱（TYPEK／year／month／b_date／e_date／co_id），
POST 與 GET 各試一次，兩者都失敗才算該端點失敗；回報時標明哪一種成功，供收集器實作採用。
不帶任何 secret；免 token。
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import os
import re
import sys
import time
from datetime import datetime, timedelta, timezone

import requests

TPE = timezone(timedelta(hours=8))
UA = "Mozilla/5.0 (X11; Linux x86_64) taiwan-stock-iching probe (+https://github.com/shihpc/taiwan-stock-iching)"
CSV_URLS = {
    "twse_csv": "https://mopsfin.twse.com.tw/opendata/t187ap04_L.csv",
    "tpex_csv": "https://mopsfin.twse.com.tw/opendata/t187ap04_O.csv",
}
MOPSOV = "https://mopsov.twse.com.tw/mops/web/ajax_t05st01"
# TWSE WAF 的擋頁：HTTP 200／307 都見過，body 固定是這段字（本雲端容器 2026-09-16 實測三個端點皆回它）。
# 沒有這個判定時 CSV 端點會被誤讀成「200 且 19 列」——擋頁的 HTML 行被 csv 模組當成資料列。
WAF_MARKERS = ("FOR SECURITY REASONS, THIS PAGE CAN NOT BE ACCESSED", "因為安全性考量")
CSV_EXPECT = {"twse_csv": "公司代號", "tpex_csv": "SecuritiesCompanyCode"}   # P0-A 修訂表第 13 列：兩市欄名不同


def is_waf_page(text: str) -> bool:
    return any(m in text for m in WAF_MARKERS)


def _decode(raw: bytes, ctype: str) -> tuple[str, str]:
    m = re.search(r"charset=([\w-]+)", ctype or "", re.I)
    cands = ([m.group(1)] if m else []) + ["utf-8-sig", "utf-8", "big5hkscs", "cp950"]
    for enc in cands:
        try:
            return raw.decode(enc), enc
        except (UnicodeDecodeError, LookupError):
            continue
    return raw.decode("utf-8", errors="replace"), "utf-8(replace)"


def _csv_rows(text: str) -> tuple[int, list[str]]:
    rows = list(csv.reader(io.StringIO(text)))
    if not rows:
        return 0, []
    header = rows[0]
    body = [r for r in rows[1:] if any(c.strip() for c in r)]
    return len(body), header


def probe_csv(name: str, url: str, timeout: float) -> dict:
    t0 = time.monotonic()
    out: dict = {"name": name, "url": url, "method": "GET"}
    try:
        r = requests.get(url, headers={"User-Agent": UA}, timeout=timeout)
        raw = r.content
        text, enc = _decode(raw, r.headers.get("content-type", ""))
        waf = is_waf_page(text)
        n, header = (0, []) if waf else _csv_rows(text)
        looks_csv = bool(header) and CSV_EXPECT[name] in ",".join(header) and "<html" not in text[:200].lower()
        out.update(status=r.status_code, bytes=len(raw), content_type=r.headers.get("content-type", ""),
                   encoding=enc, rows=n, header=header[:12], waf=waf, looks_csv=looks_csv,
                   ms=round((time.monotonic() - t0) * 1000))
        out["ok"] = r.status_code == 200 and not waf and looks_csv and n > 0
    except Exception as e:  # noqa: BLE001 — 探測要把例外當結果回報
        out.update(status=0, error=f"{type(e).__name__}: {e}", ok=False, ms=round((time.monotonic() - t0) * 1000))
    return out


def _roc(d: datetime) -> tuple[str, str, str]:
    return str(d.year - 1911), f"{d.month:02d}", f"{d.year - 1911}/{d.month:02d}/{d.day:02d}"


def probe_mopsov(typek: str, day: datetime, timeout: float) -> dict:
    year, month, roc_day = _roc(day)
    params = {"encodeURIComponent": "1", "step": "1", "firstin": "1", "off": "1",
              "TYPEK": typek, "year": year, "month": month, "b_date": roc_day, "e_date": roc_day, "co_id": ""}
    out: dict = {"name": f"mopsov_{typek}", "url": MOPSOV, "day": roc_day, "attempts": []}
    for method in ("POST", "GET"):
        t0 = time.monotonic()
        a: dict = {"method": method}
        try:
            if method == "POST":
                r = requests.post(MOPSOV, data=params, headers={"User-Agent": UA}, timeout=timeout)
            else:
                r = requests.get(MOPSOV, params=params, headers={"User-Agent": UA}, timeout=timeout)
            raw = r.content
            text, enc = _decode(raw, r.headers.get("content-type", ""))
            waf = is_waf_page(text)
            n_tr = len(re.findall(r"<tr[\s>]", text, re.I))
            a.update(status=r.status_code, bytes=len(raw), content_type=r.headers.get("content-type", ""),
                     encoding=enc, tr=n_tr, waf=waf, ms=round((time.monotonic() - t0) * 1000),
                     has_table="<table" in text.lower(), title=(re.search(r"<title>(.*?)</title>", text, re.I | re.S) or [None, ""])[1].strip()[:80])
            a["ok"] = r.status_code == 200 and not waf and n_tr > 1
        except Exception as e:  # noqa: BLE001
            a.update(status=0, error=f"{type(e).__name__}: {e}", ok=False, ms=round((time.monotonic() - t0) * 1000))
        out["attempts"].append(a)
        if a["ok"]:
            break
        time.sleep(1.0)
    out["ok"] = any(a["ok"] for a in out["attempts"])
    out["method_ok"] = next((a["method"] for a in out["attempts"] if a["ok"]), None)
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--day", default=None, help="mopsov 查詢日（YYYY-MM-DD，預設＝台北今日的前一個平日）")
    ap.add_argument("--timeout", type=float, default=30.0)
    ap.add_argument("--out", default=None, help="把 JSON 結果另寫到此路徑（預設只印）")
    args = ap.parse_args(argv)

    if args.day:
        day = datetime.strptime(args.day, "%Y-%m-%d").replace(tzinfo=TPE)
    else:
        day = datetime.now(TPE) - timedelta(days=1)
        while day.weekday() >= 5:
            day -= timedelta(days=1)

    results = [probe_csv(k, u, args.timeout) for k, u in CSV_URLS.items()]
    results.append(probe_mopsov("sii", day, args.timeout))
    results.append(probe_mopsov("otc", day, args.timeout))

    report = {"probed_at": datetime.now(TPE).isoformat(timespec="seconds"), "runner": os.environ.get("RUNNER_NAME", "local"),
              "mopsov_day": day.strftime("%Y-%m-%d"), "results": results, "ok": all(r["ok"] for r in results)}
    text = json.dumps(report, ensure_ascii=False, indent=1)
    print(text)
    if args.out:
        os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
        with open(args.out, "w", encoding="utf-8") as f:
            f.write(text + "\n")
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as f:
            f.write("| 端點 | 狀態 | 位元組 | 列數 | 方法 |\n|---|---|---|---|---|\n")
            for r in results:
                if "attempts" in r:
                    a = next((x for x in r["attempts"] if x["ok"]), r["attempts"][-1])
                    f.write(f"| {r['name']} ({r['day']}) | {a.get('status')}{' WAF' if a.get('waf') else ''} | {a.get('bytes')} | tr={a.get('tr')} | {r['method_ok'] or '—'} |\n")
                else:
                    f.write(f"| {r['name']} | {r.get('status')}{' WAF' if r.get('waf') else ''} | {r.get('bytes')} | {r.get('rows')} | GET |\n")
            f.write(f"\n**整體：{'OK' if report['ok'] else 'FAIL（使用者裁定：停下來回報，不改走代抓）'}**\n")
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
