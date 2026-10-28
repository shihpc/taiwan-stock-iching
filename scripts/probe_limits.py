"""P4-E2 探測：GitHub Actions runner 能否取得交易限制旗標的官方來源（docs/P4-PREVIEW.md §20）。

只回報事實、不落地任何資料：對每個端點印 HTTP 狀態、是否被 WAF 擋（證交所「FOR SECURITY REASONS」頁）、筆數、
欄位名、一筆樣本的日期類欄位、Last-Modified 標頭，寫進 $GITHUB_STEP_SUMMARY（若有）。
每個端點另印一行 `::notice`／`::warning title=limits <名稱>::<JSON>`，成為 check-run annotation、可經 REST 讀取
（雲端 session 讀不到 Actions log）。FinMind 三個資料集只在環境變數 FINMIND_TOKEN 存在時探測，token 走
`iching.fm.FinMind`（Authorization header、訊息經 redact），**輸出不含 token**。

日期：T＝`data/calendar_tpe.json` 的最後一個交易日（`--date` 可指定 YYYY-MM-DD）；歷史可查性另以 `HIST`（2025-01-02）測。
官方端點節流 4 秒（同 `iching.twse.OfficialClient`）。任一官方端點非 200／被 WAF 擋／0 列 → 結束碼 1（仍印完全部結果）；
FinMind 權限不足不算失敗（那正是要回答的問題）。
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import sys
import time
from pathlib import Path
from typing import Any

import requests

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

TPE = dt.timezone(dt.timedelta(hours=8))
HEADERS = {"User-Agent": "Mozilla/5.0", "Accept": "application/json, text/plain, */*"}
WAF_MARKERS = ("FOR SECURITY REASONS, THIS PAGE CAN NOT BE ACCESSED", "因為安全性考量")
INTERVAL = 4.0
HIST = "2025-01-02"
TW_RWD = "https://www.twse.com.tw/rwd/zh"
TW_OA = "https://openapi.twse.com.tw/v1"
TP_OA = "https://www.tpex.org.tw/openapi/v1"
TP_WWW = "https://www.tpex.org.tw/www/zh-tw"
DATE_KEY_RE = re.compile(r"(date|日期|期間|period|起|迄|Date)", re.I)


def probes(T: str, P: str) -> list[tuple[str, str, dict[str, str] | None]]:
    """(名稱, URL, 查詢參數)。T＝最近交易日、P＝其前一交易日（YYYY-MM-DD）。"""
    t8, p8, h8 = T.replace("-", ""), P.replace("-", ""), HIST.replace("-", "")
    ts, hs = T.replace("-", "/"), HIST.replace("-", "/")
    return [
        # 上市：信用資格＋放空限制（平盤下）、處置、注意、停牌、全額交割、停資停券預告、融資融券註記
        ("twse_twt92u_today", f"{TW_RWD}/marginTrading/TWT92U", {"response": "json"}),
        ("twse_twt92u_T", f"{TW_RWD}/marginTrading/TWT92U", {"date": t8, "response": "json"}),
        ("twse_twt92u_P", f"{TW_RWD}/marginTrading/TWT92U", {"date": p8, "response": "json"}),
        ("twse_twt92u_hist", f"{TW_RWD}/marginTrading/TWT92U", {"date": h8, "response": "json"}),
        ("twse_punish_range", f"{TW_RWD}/announcement/punish", {"startDate": p8, "endDate": t8, "querytype": "3", "response": "json"}),
        ("twse_punish_hist", f"{TW_RWD}/announcement/punish", {"startDate": h8, "endDate": h8, "querytype": "3", "response": "json"}),
        ("twse_notice_T", f"{TW_RWD}/announcement/notice", {"startDate": t8, "endDate": t8, "response": "json"}),
        ("twse_notice_range", f"{TW_RWD}/announcement/notice", {"startDate": p8, "endDate": t8, "response": "json"}),
        ("twse_notice_hist", f"{TW_RWD}/announcement/notice", {"startDate": h8, "endDate": h8, "response": "json"}),
        ("twse_twtawu_range", f"{TW_RWD}/afterTrading/TWTAWU", {"startDate": p8, "endDate": t8, "response": "json"}),
        ("twse_mi_margn_T", f"{TW_RWD}/marginTrading/MI_MARGN", {"date": t8, "selectType": "ALL", "response": "json"}),
        ("twse_oa_punish", f"{TW_OA}/announcement/punish", None),
        ("twse_oa_notice", f"{TW_OA}/announcement/notice", None),
        ("twse_oa_twtawu", f"{TW_OA}/exchangeReport/TWTAWU", None),
        ("twse_oa_twt85u", f"{TW_OA}/exchangeReport/TWT85U", None),
        ("twse_oa_bfi84u", f"{TW_OA}/exchangeReport/BFI84U", None),
        # 上櫃
        ("tpex_oa_margin_mark", f"{TP_OA}/tpex_margin_trading_margin_mark", None),
        ("tpex_www_margin_mark_T", f"{TP_WWW}/margin/mark", {"date": ts, "response": "json"}),
        ("tpex_www_margin_mark_hist", f"{TP_WWW}/margin/mark", {"date": hs, "response": "json"}),
        ("tpex_oa_margin_term", f"{TP_OA}/tpex_margin_trading_term", None),
        ("tpex_www_disposal", f"{TP_WWW}/bulletin/disposal", {"startDate": ts, "endDate": ts, "response": "json"}),
        ("tpex_www_disposal_hist", f"{TP_WWW}/bulletin/disposal", {"startDate": hs, "endDate": hs, "response": "json"}),
        ("tpex_www_attention", f"{TP_WWW}/bulletin/attention", {"startDate": ts, "endDate": ts, "response": "json"}),
        ("tpex_oa_disposal", f"{TP_OA}/tpex_disposal_information", None),
        ("tpex_oa_warning", f"{TP_OA}/tpex_trading_warning_information", None),
        ("tpex_oa_spendi_history", f"{TP_OA}/tpex_spendi_history", None),
        ("tpex_oa_cmode", f"{TP_OA}/tpex_cmode", None),
    ]


def rows_of(body: Any) -> tuple[list, list[str]]:
    """回 (列, 欄位名)。OpenAPI＝list[dict]；TWSE rwd＝{fields, data} 或 {tables:[{fields,data}]}；TPEx www＝{tables:[...]}。"""
    if isinstance(body, list):
        return body, list(body[0].keys()) if body and isinstance(body[0], dict) else []
    if not isinstance(body, dict):
        return [], []
    if isinstance(body.get("data"), list):
        return body["data"], [str(x) for x in body.get("fields") or []]
    rows, fields = [], []
    for t in body.get("tables") or []:
        if isinstance(t, dict) and isinstance(t.get("data"), list):
            rows += t["data"]
            fields = fields or [str(x) for x in t.get("fields") or []]
    return rows, fields


def is_blank_row(row: Any) -> bool:
    """全部欄位為空字串／None／"0" 的佔位列（TWSE OpenAPI 無資料時的回應形狀）。"""
    vals = list(row.values()) if isinstance(row, dict) else list(row) if isinstance(row, list) else [row]
    return all(v in (None, "", "0", 0) for v in vals)


def mask(text: str) -> str:
    """把 FINMIND_TOKEN 字面值整段遮掉（fm.redact 只遮 token=… 樣式；伺服器若回寫 token 也不外洩）。"""
    tok = os.environ.get("FINMIND_TOKEN") or ""
    return text.replace(tok, "***") if len(tok) >= 4 else text


def sample_dates(row: Any, fields: list[str]) -> dict[str, Any]:
    if isinstance(row, dict):
        return {k: row[k] for k in row if DATE_KEY_RE.search(k)}
    if isinstance(row, list):
        return {f: v for f, v in zip(fields, row) if DATE_KEY_RE.search(f)}
    return {}


def probe_one(sess: requests.Session, name: str, url: str, params: dict | None) -> dict[str, Any]:
    t0 = time.perf_counter()
    res: dict[str, Any] = {"name": name, "url": url, "params": params or {}}
    verify = True
    for _ in range(2):
        try:
            r = sess.get(url, params=params, headers=HEADERS, timeout=30, verify=verify, allow_redirects=False)
            break
        except requests.exceptions.SSLError as e:
            if "tpex.org.tw" in url and verify:
                verify = False
                res["tls_insecure"] = True
                continue
            res.update(state="network_error", error=mask(f"{type(e).__name__}: {e}")[:160])
            return res
        except requests.RequestException as e:
            res.update(state="network_error", error=mask(f"{type(e).__name__}: {e}")[:160])
            return res
    text = r.text
    res.update(http=r.status_code, ms=int((time.perf_counter() - t0) * 1000), bytes=len(r.content),
               last_modified=r.headers.get("Last-Modified"), location=r.headers.get("Location"))
    res["waf"] = any(m in text for m in WAF_MARKERS)
    try:
        body = r.json()
    except ValueError:
        body = None
    rows, fields = rows_of(body)
    rows = [r for r in rows if not is_blank_row(r)]          # OpenAPI 無資料時回一筆全空佔位列（例 announcement/notice 早上產檔）
    res["n"] = len(rows)
    res["fields"] = fields[:12]
    if isinstance(body, dict):
        res["stat"] = body.get("stat")
        res["body_date"] = body.get("date")
    if rows:
        res["sample_dates"] = sample_dates(rows[0], fields)
    ok = r.status_code == 200 and not res["waf"] and body is not None and len(rows) > 0
    res["state"] = "ok" if ok else ("waf" if res["waf"] else ("empty" if r.status_code == 200 and body is not None else "bad"))
    return res


def probe_finmind(T: str) -> list[dict[str, Any]]:
    """有 FINMIND_TOKEN 才跑：三個資料集全市場單日切片（不帶 data_id）。回報 ok 筆數／empty／permission／error，不含 token。"""
    if not os.environ.get("FINMIND_TOKEN"):
        return [{"name": "finmind", "state": "skipped", "note": "無 FINMIND_TOKEN"}]
    from iching import fm   # noqa: PLC0415
    cli = fm.FinMind(token=os.environ["FINMIND_TOKEN"])
    out = []
    start = (dt.date.fromisoformat(T) - dt.timedelta(days=30)).isoformat()
    for ds, s in (("TaiwanStockSuspended", start), ("TaiwanStockDispositionSecuritiesPeriod", start),
                  ("TaiwanStockMarginShortSaleSuspension", start)):
        res: dict[str, Any] = {"name": f"finmind_{ds}", "params": {"start_date": s, "end_date": T}}
        try:
            rows = cli.get(ds, start_date=s, end_date=T)
            res.update(state="ok" if rows else "empty", n=len(rows), fields=list(rows[0].keys())[:12] if rows else [],
                       sample=rows[0] if rows else None,
                       ids=sorted({str(x.get("stock_id")) for x in rows})[:40])
        except fm.PermissionRequired as e:
            res.update(state="permission", error=mask(fm.redact(str(e)))[:200])
        except Exception as e:  # noqa: BLE001
            res.update(state="error", error=mask(fm.redact(f"{type(e).__name__}: {e}"))[:200])
        out.append(res)
    return out


def _esc(s: str, prop: bool = False) -> str:
    s = s.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")
    return s.replace(":", "%3A").replace(",", "%2C") if prop else s


def annotation(res: dict[str, Any]) -> str:
    level = "notice" if res.get("state") in ("ok", "skipped", "permission", "empty") else "warning"
    payload = {k: v for k, v in res.items() if k not in ("url",)}
    body = mask(json.dumps(payload, ensure_ascii=False, default=str))
    return f"::{level} title={_esc('limits ' + res['name'], True)}::{_esc(body)}"


def last_trading_days(cal_path: Path, date: str | None) -> tuple[str, str]:
    dates = sorted(json.loads(cal_path.read_text(encoding="utf-8"))["dates"])
    if date:
        prev = [d for d in dates if d < date]
        return date, (prev[-1] if prev else date)
    return dates[-1], dates[-2]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="P4-E2 交易限制來源探測（不落地資料）")
    ap.add_argument("--date", help="T（YYYY-MM-DD）；預設 data/calendar_tpe.json 最後一個交易日")
    a = ap.parse_args(argv)
    T, P = last_trading_days(REPO / "data" / "calendar_tpe.json", a.date)
    now = dt.datetime.now(TPE).isoformat(timespec="seconds")
    print(f"probe_limits：T={T} P={P} HIST={HIST} 台北現在 {now}")
    sess = requests.Session()
    results, last = [], 0.0
    for name, url, params in probes(T, P):
        wait = INTERVAL - (time.monotonic() - last)
        if wait > 0:
            time.sleep(wait)
        last = time.monotonic()
        res = probe_one(sess, name, url, params)
        res["at"] = dt.datetime.now(TPE).isoformat(timespec="seconds")
        results.append(res)
        print(annotation(res), flush=True)
    for res in probe_finmind(T):
        results.append(res)
        print(annotation(res), flush=True)
    summ = os.environ.get("GITHUB_STEP_SUMMARY")
    if summ:
        with open(summ, "a", encoding="utf-8") as f:
            f.write(f"## probe-limits T={T} P={P}（台北 {now}）\n\n| 名稱 | 狀態 | HTTP | WAF | 筆數 | Last-Modified |\n|---|---|---|---|---|---|\n")
            for r in results:
                f.write(mask(f"| {r['name']} | {r.get('state')} | {r.get('http', '')} | {r.get('waf', '')} | {r.get('n', '')} | {r.get('last_modified') or ''} |\n"))
    bad = [r["name"] for r in results if not r["name"].startswith("finmind") and r.get("state") != "ok"]
    n_off = sum(1 for r in results if not r["name"].startswith("finmind"))
    print(f"probe_limits：官方端點 {n_off} 支，非 ok {len(bad)} 支：{bad}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
