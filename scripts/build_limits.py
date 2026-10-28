#!/usr/bin/env python3
"""P4-E2 交易限制旗標：抓官方當日快照 → `data/limits/<T>.json`（每日累積）＋網頁用 `data/web/lim.json`。

    python3 scripts/build_limits.py --root .

依據：裁定 #76 §14.2 Q4（層 2 只顯示旗標、不進分數）；使用者 2026-10-28 決定（規格全套、從上線起每日累積、先探測）；
來源＝runner 實測 run 40720532027（docs/P4-PREVIEW.md §20）。解析在 `src/iching/limits.py`（純函式）。

## 流程

1. T＝`data/web/latest.json` 的 `date`（本站最新分數的資料日）；本站涵蓋代號＝其 `stocks` 的鍵與 `market`。
2. 依 SOURCES 逐支抓（官方端點 4 秒間隔、TPEx SSL 錯誤降級 verify=False 並記錄）；**每支獨立成敗**——失敗只讓
   該類別在該市場顯示「未檢查」（不默認為「無」），其餘照常。
3. 寫 `data/limits/<T>.json`：各來源狀態（ok／error＋原因、抓取時刻）與正規化內容（只含本站涵蓋代號）。同日重跑覆寫。
4. 讀 `data/limits/` 內 ≤T 的最近 10 份快照（依檔名日期）算「近 10 份快照中列入注意股的日數」，快照不足 10 份時照實記份數。
5. 寫 `data/web/lim.json`：只列有任何旗標的本站代號＋「非信用交易標的」（本站涵蓋但不在該市場融資融券名單；該名單失敗時不判）。

## 語意（主對話設計，使用者可改；docs §20.5）

- 平盤下／信用資格名單取 **T 日**那張（`date=T`），即「T 日當天適用的限制」，不是次一交易日。
- 處置：T 日生效中（起 ≤ T ≤ 迄）標「處置中」；只有將生效者標「將處置（自 起日）」。
- 停券預告（融券回補）：迄日 ≥ T 的列；上櫃欄名明寫「停券起日(最後回補日)」，上市只有一組起訖、「起日＝最後回補日」為推論。
- 停牌：上市 OpenAPI 只列最近一次公告區間，上櫃以本民國年事件序列推算；兩者都依「暫停日 ≤ T 且（無恢復日或恢復日 > T）」。
- 「得為融資融券」只代表公開規則上可以，**不代表一定有券可借**（v1.2.2 `:411`／`:414`）。

## 回傳碼

0 成功（含部分來源失敗）；2 中止（latest 讀不到、全部來源失敗、或寫檔失敗）。中止時不寫 `data/web/lim.json`。
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Callable, Mapping

import requests

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from iching import limits as L  # noqa: E402

TPE = dt.timezone(dt.timedelta(hours=8))
SCHEMA = 1
INTERVAL = 4.0
NOTICE_WINDOW = 10
DISPOSAL_LOOKBACK_DAYS = 45
HEADERS = {"User-Agent": "Mozilla/5.0", "Accept": "application/json, text/plain, */*"}
WAF_MARKERS = ("FOR SECURITY REASONS, THIS PAGE CAN NOT BE ACCESSED", "因為安全性考量")
TW_RWD = "https://www.twse.com.tw/rwd/zh"
TW_OA = "https://openapi.twse.com.tw/v1"
TP_OA = "https://www.tpex.org.tw/openapi/v1"
TP_WWW = "https://www.tpex.org.tw/www/zh-tw"
MARKETS = ("twse", "tpex")
CATS = ("margin", "notice", "disposal", "term", "halt", "altered")


class BuildLimitsError(Exception):
    pass


def sources(T: str) -> list[tuple[str, str, str, str, dict | None]]:
    """(類別, 市場, 鍵, URL, 參數)。"""
    t8 = T.replace("-", "")
    ts = T.replace("-", "/")
    d0 = (dt.date.fromisoformat(T) - dt.timedelta(days=DISPOSAL_LOOKBACK_DAYS)).isoformat()
    return [
        ("margin", "twse", "twse_twt92u", f"{TW_RWD}/marginTrading/TWT92U", {"date": t8, "response": "json"}),
        ("margin", "tpex", "tpex_margin_mark", f"{TP_WWW}/margin/mark", {"date": ts, "response": "json"}),
        ("notice", "twse", "twse_notice", f"{TW_RWD}/announcement/notice", {"startDate": t8, "endDate": t8, "response": "json"}),
        ("notice", "tpex", "tpex_attention", f"{TP_WWW}/bulletin/attention", {"startDate": ts, "endDate": ts, "response": "json"}),
        ("disposal", "twse", "twse_punish", f"{TW_RWD}/announcement/punish",
         {"startDate": d0.replace("-", ""), "endDate": t8, "querytype": "3", "response": "json"}),
        ("disposal", "tpex", "tpex_disposal", f"{TP_WWW}/bulletin/disposal",
         {"startDate": d0.replace("-", "/"), "endDate": ts, "response": "json"}),
        ("term", "twse", "twse_bfi84u", f"{TW_OA}/exchangeReport/BFI84U", None),
        ("term", "tpex", "tpex_term", f"{TP_OA}/tpex_margin_trading_term", None),
        ("halt", "twse", "twse_twtawu", f"{TW_OA}/exchangeReport/TWTAWU", None),
        ("halt", "tpex", "tpex_spendi", f"{TP_OA}/tpex_spendi_history", None),
        ("altered", "twse", "twse_twt85u", f"{TW_OA}/exchangeReport/TWT85U", None),
        ("altered", "tpex", "tpex_cmode", f"{TP_OA}/tpex_cmode", None),
    ]


def http_get(url: str, params: dict | None) -> tuple[Any, dict[str, Any]]:
    """回 (JSON body, meta)。非 200／WAF／非 JSON → 拋 BuildLimitsError。"""
    meta: dict[str, Any] = {}
    verify = True
    for _ in range(2):
        try:
            r = requests.get(url, params=params, headers=HEADERS, timeout=30, verify=verify, allow_redirects=False)
            break
        except requests.exceptions.SSLError:
            if "tpex.org.tw" in url and verify:
                verify, meta["tls_insecure"] = False, True
                continue
            raise
    if r.status_code != 200 or any(m in r.text for m in WAF_MARKERS):
        raise BuildLimitsError(f"HTTP {r.status_code}{'（WAF 擋頁）' if any(m in r.text for m in WAF_MARKERS) else ''}")
    try:
        body = r.json()
    except ValueError as e:
        raise BuildLimitsError("回應不是 JSON") from e
    meta["last_modified"] = r.headers.get("Last-Modified")
    return body, meta


def _keep(d: Mapping[str, Any], site: set[str]) -> dict[str, Any]:
    return {c: v for c, v in sorted(d.items()) if c in site}


def parse_source(key: str, body: Any, T: str, site: set[str]) -> Any:
    """依來源鍵呼叫對應解析，只留本站涵蓋代號。回可 JSON 化的內容。"""
    if key in ("twse_twt92u", "tpex_margin_mark"):
        m = L.parse_margin_list(body)
        if not m:
            raise BuildLimitsError("融資融券名單 0 列")
        return {"eligible": sorted(c for c in m if c in site),
                "flags": _keep({c: v for c, v in m.items() if v["ms"] or v["sbl"] or v["dn"]}, site), "n_all": len(m)}
    if key in ("twse_notice", "tpex_attention"):
        return _keep(L.parse_notice(body), site)
    if key in ("twse_punish", "tpex_disposal"):
        return _keep(L.parse_disposal(body), site)
    if key == "twse_bfi84u":
        return _keep(L.parse_twse_bfi84u(body), site)
    if key == "tpex_term":
        return _keep(L.parse_tpex_term(body), site)
    if key == "twse_twtawu":
        return _keep(L.halted_twse(body, T), site)
    if key == "tpex_spendi":
        return _keep(L.halted_tpex(body, T), site)
    if key == "twse_twt85u":
        return {"altered": sorted(c for c in L.parse_twse_altered(body) if c in site)}
    if key == "tpex_cmode":
        alt, sus = L.parse_tpex_cmode(body)
        return {"altered": sorted(c for c in alt if c in site), "suspended": sorted(c for c in sus if c in site)}
    raise BuildLimitsError(f"未知來源 {key}")


def load_latest(root: Path) -> tuple[str, dict[str, str]]:
    try:
        j = json.loads((root / "data" / "web" / "latest.json").read_text(encoding="utf-8"))
        T = j["date"]
        mk = {str(c): str(s.get("market")) for c, s in j["stocks"].items() if isinstance(s, dict)}
        dt.date.fromisoformat(T)
    except Exception as e:  # noqa: BLE001
        raise BuildLimitsError(f"data/web/latest.json 讀不到或形狀不對：{e}") from e
    return T, mk


def fetch_all(T: str, site: set[str], get: Callable = http_get, sleep: Callable = time.sleep,
              now: Callable = lambda: dt.datetime.now(TPE)) -> dict[str, Any]:
    snap: dict[str, Any] = {}
    for i, (cat, mk, key, url, params) in enumerate(sources(T)):
        if i:
            sleep(INTERVAL)
        ent: dict[str, Any] = {"cat": cat, "market": mk, "at": now().isoformat(timespec="seconds")}
        try:
            body, meta = get(url, params)
            ent.update(meta)
            ent["data"] = parse_source(key, body, T, site)
            ent["ok"] = True
        except Exception as e:  # noqa: BLE001
            ent["ok"] = False
            ent["error"] = f"{type(e).__name__}: {e}"[:200]
        snap[key] = ent
    return snap


def notice_days(root: Path, T: str, today_snap: Mapping[str, Any]) -> tuple[dict[str, list[str]], int]:
    """近 NOTICE_WINDOW 份快照（含今日）中各代號列入注意股的日期（任一市場來源 ok 才算該份有效）。"""
    snaps: dict[str, Mapping[str, Any]] = {}
    d = root / "data" / "limits"
    if d.is_dir():
        for p in sorted(d.glob("*.json")):
            day = p.stem
            if len(day) == 10 and day < T:
                try:
                    snaps[day] = json.loads(p.read_text(encoding="utf-8"))["sources"]
                except Exception:  # noqa: BLE001, S112
                    continue
    snaps[T] = today_snap
    days = sorted(snaps)[-NOTICE_WINDOW:]
    out: dict[str, list[str]] = {}
    n_valid = 0
    for day in days:
        src = snaps[day]
        ok = [k for k in ("twse_notice", "tpex_attention") if (src.get(k) or {}).get("ok")]
        if len(ok) < 2:
            continue
        n_valid += 1
        for k in ok:
            for c in (src[k].get("data") or {}):
                out.setdefault(c, []).append(day)
    return out, n_valid


def build_web(T: str, mkt: Mapping[str, str], snap: Mapping[str, Any], nd: Mapping[str, list[str]], n_valid: int) -> dict[str, Any]:
    site = set(mkt)
    ok = {k: bool(v.get("ok")) for k, v in snap.items()}
    key = {(v["cat"], v["market"]): k for k, v in snap.items()}

    def src(cat: str, mk: str) -> Any:
        k = key[(cat, mk)]
        return snap[k]["data"] if ok[k] else None

    stocks: dict[str, dict[str, Any]] = {}

    def put(c: str, k: str, v: Any) -> None:
        stocks.setdefault(c, {})[k] = v

    for mk in MARKETS:
        codes = {c for c, m in mkt.items() if m == mk}
        mg = src("margin", mk)
        if mg is not None:
            elig = set(mg["eligible"])
            for c in sorted(codes - elig):
                put(c, "noc", 1)                                   # 非信用交易標的
            for c, f in mg["flags"].items():
                if f["ms"]:
                    put(c, "ms", 1)
                if f["sbl"]:
                    put(c, "sbl", 1)
                if f["dn"]:
                    put(c, "dn", 1)
        nt = src("notice", mk)
        if nt is not None:
            for c, v in nt.items():
                put(c, "nt", {"d": v.get("d"), "cnt": v.get("cnt"), "info": v.get("info")})
        dp = src("disposal", mk)
        if dp is not None:
            for c, items in dp.items():
                st = L.disposal_state(items, T)
                if st:
                    put(c, "dp", st)
        tm = src("term", mk)
        if tm is not None:
            for c, v in tm.items():
                if v["to"] >= T:
                    put(c, "tm", {**v, "infer": mk == "twse"})
        ht = src("halt", mk)
        if ht is not None:
            for c, v in ht.items():
                put(c, "ht", v)
        al = src("altered", mk)
        if al is not None:
            for c in al.get("altered", []):
                put(c, "al", 1)
            for c in al.get("suspended", []):
                put(c, "ht", stocks.get(c, {}).get("ht") or {"from": None, "resume": None})
    for c, days in nd.items():
        if c in site:
            put(c, "n10", len(days))
    checked = {f"{cat}|{mk}": ok[key[(cat, mk)]] for cat in CATS for mk in MARKETS}
    at = {f"{cat}|{mk}": snap[key[(cat, mk)]]["at"] for cat in CATS for mk in MARKETS}
    return {"schema": SCHEMA, "date": T, "checked": checked, "at": at, "n10_snaps": n_valid, "n10_window": NOTICE_WINDOW,
            "stocks": dict(sorted(stocks.items()))}


def dumps(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n"


def write(path: Path, obj: Any) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(dumps(obj), encoding="utf-8")
        os.replace(tmp, path)
    except OSError as e:
        raise BuildLimitsError(f"寫不出 {path}: {e}") from e


def build(root: Path, get: Callable = http_get, sleep: Callable = time.sleep,
          now: Callable = lambda: dt.datetime.now(TPE)) -> dict[str, Any]:
    T, mkt = load_latest(root)
    snap = fetch_all(T, set(mkt), get=get, sleep=sleep, now=now)
    if not any(v["ok"] for v in snap.values()):
        raise BuildLimitsError("全部來源失敗：" + "；".join(f"{k} {v.get('error')}" for k, v in snap.items())[:600])
    write(root / "data" / "limits" / f"{T}.json", {"schema": SCHEMA, "date": T, "sources": snap})
    nd, n_valid = notice_days(root, T, snap)
    web = build_web(T, mkt, snap, nd, n_valid)
    write(root / "data" / "web" / "lim.json", web)
    return {"T": T, "snap": snap, "web": web}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="P4-E2：交易限制旗標 data/limits/<T>.json＋data/web/lim.json（docs/P4-PREVIEW.md §20）")
    ap.add_argument("--root", default=".")
    a = ap.parse_args(argv)
    try:
        res = build(Path(a.root))
    except BuildLimitsError as e:
        print(f"[build_limits] 中止：{e}", file=sys.stderr)
        return 2
    bad = [k for k, v in res["snap"].items() if not v["ok"]]
    for k in bad:
        print(f"::warning::build_limits 來源 {k} 失敗（該類該市場顯示「未檢查」）：{res['snap'][k].get('error')}")
    w = res["web"]
    print(f"[build_limits] T={res['T']} 來源 ok {len(res['snap']) - len(bad)}/{len(res['snap'])}；有旗標代號 {len(w['stocks'])}；"
          f"注意累積快照 {w['n10_snaps']} 份")
    return 0


if __name__ == "__main__":
    sys.exit(main())
