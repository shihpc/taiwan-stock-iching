"""證交所／櫃買官方端點（Hetzner 可達、本雲端容器被 WAF 擋——P0-A §3；本檔在此**無法實測**）。

1. `MI_5MINS_HIST`（裁定 4：大盤開盤價以證據定）：
   `https://www.twse.com.tw/rwd/zh/afterTrading/MI_5MINS_HIST?date=YYYYMM01&response=json`，按月。
   **欄位名與日期格式未實測**：程式對欄位做防禦——在 `fields` 找含「日期」「開盤」「收盤」的欄，
   日期接受民國 `111/01/03`、`2022/01/03`、`2022-01-03`；找不到就明確報錯、不靜默。
   回應形狀假設同 taiwan-flows src/totals.py 用過的 FMTQIK：`{"stat":"OK","fields":[...],"data":[[...]]}`
   （推測 TWSE rwd 端點共用此形狀；未實測）。
2. `BFI82U`／TPEx `insti/summary`（B1.5 法人口徑）、`FMTQIK`／TPEx `tradingIndex`（B1.3／B1.4 成交金額，按月）：
   原始 JSON 全文落地，不在此解析。
3. WAF 封鎖頁是 HTTP 200 的 HTML（`docs/pre-registration.md` §1.2.3 實測），故**非 JSON 一律視為失敗**。
4. 開盤價候選比對（裁定 9「與官方一致者為準；皆不一致再回問」）：`compare_candidates()` 對多個候選各算一致率、
   給出 verdict；第二候選 `TaiwanStockKBar` TAIEX 09:00 bar 的 `close` 由 `kbar_0900_row()` 取出
   （`taiwan-backtest/scripts/fetch_taiex.py:56-63` 取法前例；欄位名未在本容器實測）。
"""
from __future__ import annotations

import datetime as dt
import json
import re
import time
import warnings
from typing import Any, Callable

import requests

from .config import OFFICIAL_INTERVAL_SEC, TWSE_MI5MINS_HIST

HEADERS = {"User-Agent": "Mozilla/5.0", "Accept": "application/json, text/plain, */*"}
_ROC = re.compile(r"^\s*(\d{2,3})/(\d{1,2})/(\d{1,2})\s*$")
_YMD = re.compile(r"^\s*(\d{4})[/-](\d{1,2})[/-](\d{1,2})\s*$")


class TwseError(Exception):
    pass


def parse_roc_or_iso(s: Any) -> str | None:
    """'111/01/03' → '2022-01-03'；也接受 '2022/01/03'、'2022-01-03'。不合法回 None。"""
    s = str(s or "")
    m = _ROC.match(s)
    if m:
        y, mo, d = (int(x) for x in m.groups())
        y += 1911
    else:
        m = _YMD.match(s)
        if not m:
            return None
        y, mo, d = (int(x) for x in m.groups())
    try:
        return dt.date(y, mo, d).isoformat()
    except ValueError:
        return None


def to_number(s: Any) -> float | None:
    if s is None:
        return None
    if isinstance(s, (int, float)):
        return float(s)
    t = str(s).replace(",", "").strip()
    if t in ("", "--", "-"):
        return None
    try:
        return float(t)
    except ValueError:
        return None


def _find_field(fields: list[str], *needles: str) -> int | None:
    for i, f in enumerate(fields):
        if all(n in str(f) for n in needles):
            return i
    return None


def parse_index_hist(payload: Any) -> list[dict]:
    """把 MI_5MINS_HIST 的月 payload 解成 [{date, open, high, low, close}]。找不到必要欄位即 raise。"""
    if not isinstance(payload, dict):
        raise TwseError(f"payload 非 dict：{type(payload).__name__}")
    stat = payload.get("stat")
    if stat != "OK":
        raise TwseError(f"stat={stat!r}（非 OK；可能該月無資料或端點格式已變）")
    fields = payload.get("fields")
    data = payload.get("data")
    if not isinstance(fields, list) or not isinstance(data, list):
        raise TwseError(f"缺 fields/data：keys={sorted(payload.keys())}")
    i_date = _find_field(fields, "日期")
    i_open = _find_field(fields, "開盤")
    i_close = _find_field(fields, "收盤")
    i_high = _find_field(fields, "最高")
    i_low = _find_field(fields, "最低")
    if i_date is None or i_open is None:
        raise TwseError(f"fields 找不到「日期」或「開盤」欄：{fields}")
    out = []
    for row in data:
        if not isinstance(row, list) or len(row) <= max(i_date, i_open):
            continue
        d = parse_roc_or_iso(row[i_date])
        if not d:
            raise TwseError(f"日期欄無法解析：{row[i_date]!r}（fields={fields}）")
        out.append({
            "date": d,
            "open": to_number(row[i_open]),
            "high": to_number(row[i_high]) if i_high is not None and len(row) > i_high else None,
            "low": to_number(row[i_low]) if i_low is not None and len(row) > i_low else None,
            "close": to_number(row[i_close]) if i_close is not None and len(row) > i_close else None,
        })
    return out


def _present(v: Any) -> bool:
    """比對用值是否存在：None 與 0 都視為缺值（指數開盤不可能為 0；0 多半是佔位）。"""
    try:
        return v is not None and float(v) != 0.0
    except (TypeError, ValueError):
        return False


def compare_open(twse_rows: list[dict], fm_rows: list[dict], tol: float = 0.005) -> dict:
    """逐日比對 TWSE 官方開盤 vs FinMind `open`。tol＝視為一致的絕對差（指數兩位小數 → 0.005）。
    `open` 為 None 或 0 的列視為缺值（不算不一致、不進共同日）。"""
    tw = {r["date"]: r for r in twse_rows if r.get("date") and _present(r.get("open"))}
    fm = {str(r.get("date")): r for r in fm_rows if r.get("date") is not None and _present(r.get("open"))}
    common = sorted(set(tw) & set(fm))
    diffs = []
    for d in common:
        a = float(tw[d]["open"])
        b = float(fm[d]["open"])
        diffs.append({"date": d, "twse_open": a, "finmind_open": b, "diff": round(b - a, 4),
                      "diff_pct": round((b - a) / a * 100, 6) if a else None})
    n = len(diffs)
    n_equal = sum(1 for x in diffs if abs(x["diff"]) <= tol)
    close_diffs = []
    for d in common:
        if tw[d].get("close") is not None and fm[d].get("close") is not None:
            close_diffs.append(abs(float(fm[d]["close"]) - float(tw[d]["close"])))
    worst = sorted(diffs, key=lambda x: -abs(x["diff"]))[:20]
    return {
        "n_twse": len(tw), "n_finmind": len(fm), "n_common": n,
        "only_twse": sorted(set(tw) - set(fm))[:50], "only_finmind": sorted(set(fm) - set(tw))[:50],
        "tol": tol, "n_equal": n_equal, "agree_rate": (n_equal / n) if n else None,
        "max_abs_diff": max((abs(x["diff"]) for x in diffs), default=None),
        "close_n": len(close_diffs), "close_n_equal": sum(1 for x in close_diffs if x <= tol),
        "worst": worst,
        "first_date": common[0] if common else None, "last_date": common[-1] if common else None,
    }


def compare_candidates(twse_rows: list[dict], candidates: dict[str, list[dict]], *, tol: float = 0.005,
                       agree_threshold: float = 0.99) -> dict:
    """多候選 vs 官方開盤。candidates={名稱: [{date, open}]}（值一律放在 `open` 鍵）。
    回 {"candidates": {名稱: compare_open 結果＋"agrees"}, "verdict": 名稱 | "both"/"all" | "none" | "no_data",
        "agree_threshold": ...}。verdict 只看一致率 ≥ agree_threshold；「皆不一致」＝none，交使用者裁決。"""
    res = {}
    agreeing = []
    for name, rows in candidates.items():
        r = compare_open(twse_rows, rows, tol=tol)
        r["agrees"] = bool(r["agree_rate"] is not None and r["agree_rate"] >= agree_threshold)
        res[name] = r
        if r["agrees"]:
            agreeing.append(name)
    if not any(r["n_common"] for r in res.values()):
        verdict = "no_data"
    elif not agreeing:
        verdict = "none"
    elif len(agreeing) == len(candidates) and len(candidates) >= 2:
        verdict = "both" if len(candidates) == 2 else "all"
    else:
        verdict = agreeing[0] if len(agreeing) == 1 else "+".join(agreeing)
    return {"candidates": res, "verdict": verdict, "agree_threshold": agree_threshold, "agreeing": agreeing}


def kbar_0900_row(rows: list[dict], date: str) -> dict | None:
    """由 `TaiwanStockKBar` 當日分 K 取 09:00 那根（minute 以 '09:00' 開頭者取 minute 最小），回
    {date, minute, open, high, low, close, volume, n_bars}；當日無 09:00 bar 回 None。
    欄位名（minute/open/high/low/close/volume）依 taiwan-backtest fetch_taiex.py 前例，**未在本容器實測**，
    取值全走 .get()。`date` 欄若存在則只認 == date 的列。"""
    day = [r for r in rows if isinstance(r, dict) and (r.get("date") in (None, date))]
    bars = sorted((r for r in day if str(r.get("minute") or "").startswith("09:00")), key=lambda r: str(r.get("minute")))
    if not bars:
        return None
    b = bars[0]
    return {"date": date, "minute": b.get("minute"), "open": to_number(b.get("open")), "high": to_number(b.get("high")),
            "low": to_number(b.get("low")), "close": to_number(b.get("close")), "volume": to_number(b.get("volume")),
            "n_bars": len(day)}


def official_body_ok(body: Any, source: str) -> tuple[bool, str]:
    """官方端點 JSON 是否「有資料」。判法取自 taiwan-flows/src/totals.py：
    - twse（BFI82U／FMTQIK／MI_5MINS_HIST）：`stat == "OK"` 且 `data` 非空（totals.py:48／:148）
    - tpex（insti/summary／tradingIndex）：`tables` 非空且首表 `data` 非空（totals.py:82-85／:159-162）；
      若另帶 `stat` 且不是 ok（不分大小寫）亦視為無資料
    回 (ok, 說明)。非 dict 一律 False。"""
    if not isinstance(body, dict):
        return False, f"body 非 dict：{type(body).__name__}"
    if source == "twse":
        st = body.get("stat")
        if st != "OK":
            return False, f"stat={st!r}"
        data = body.get("data")
        return bool(data), ("data 空" if not data else f"data {len(data)} 列")
    if source == "tpex":
        st = body.get("stat")
        if st is not None and str(st).lower() != "ok":
            return False, f"stat={st!r}"
        tables = body.get("tables")
        if not tables:
            return False, "tables 空"
        tbl = tables[0] if isinstance(tables, list) else tables
        data = tbl.get("data") if isinstance(tbl, dict) else None
        return bool(data), ("tables[0].data 空" if not data else f"tables[0].data {len(data)} 列")
    return False, f"未知 source {source!r}"


def silence_insecure_warnings() -> None:
    """壓掉 urllib3 的 InsecureRequestWarning。

    關閉驗證時它對**每一個請求**印一次（tpex_inst_summary 有 1,618 鍵＝1,618 次，
    每次 3 行），會把進度列整個淹掉、tee 出來的 log 也沒法看。降級本身已由
    `run --tpex-no-verify` 在起頭以 WARNING 明示一次，這裡只壓掉逐請求的重複噪音，
    **不改變是否驗證**。urllib3 缺席時靜默略過（本模組只硬相依 requests）。"""
    try:
        from urllib3.exceptions import InsecureRequestWarning
    except ImportError:  # pragma: no cover - requests 一定帶 urllib3，留給精簡環境
        return
    warnings.simplefilter("ignore", InsecureRequestWarning)


class OfficialClient:
    """TWSE／TPEx 官方端點的節流 GET。4 秒全域間隔（taiwan-flows 經驗：連打約 6 次即被 IP 限流且不自動解除）。
    回 (status_code, body_json_or_None, text)。`tpex_verify=False` 只對 tpex.org.tw 關閉 TLS 驗證
    （taiwan-flows 註記 TPEx 部分端點 SSL 異常；預設開啟驗證，Hetzner 實際碰到才用 `run --tpex-no-verify`）。"""

    def __init__(self, *, interval: float = OFFICIAL_INTERVAL_SEC, session: requests.Session | None = None,
                 sleep: Callable[[float], None] = time.sleep, clock: Callable[[], float] = time.monotonic,
                 timeout: float = 30.0, tpex_verify: bool = True) -> None:
        self.interval = interval
        self.session = session or requests.Session()
        self._sleep = sleep
        self._clock = clock
        self._last = 0.0
        self.timeout = timeout
        self.tpex_verify = tpex_verify
        if not tpex_verify:
            silence_insecure_warnings()
        self.n_requests = 0
        self.sleep_s = 0.0   # 累計節流等待秒數（perf_counter 實測），同 fm.FinMind.sleep_s；只供進度列拆分

    def get(self, url: str, params: dict[str, Any]) -> tuple[int, Any, str]:
        wait = self.interval - (self._clock() - self._last)
        if wait > 0:
            t0 = time.perf_counter()
            self._sleep(wait)
            self.sleep_s += time.perf_counter() - t0
        self._last = self._clock()
        self.n_requests += 1
        verify = self.tpex_verify if "tpex.org.tw" in url else True
        r = self.session.get(url, params=params, headers=HEADERS, timeout=self.timeout, verify=verify)
        text = r.text
        try:
            body = r.json()
        except (ValueError, json.JSONDecodeError):
            body = None
        return r.status_code, body, text

    def index_hist_month(self, yyyymm: str) -> list[dict]:
        code, body, text = self.get(TWSE_MI5MINS_HIST, {"date": f"{yyyymm}01", "response": "json"})
        if body is None:
            raise TwseError(f"MI_5MINS_HIST {yyyymm}: HTTP {code} 非 JSON（可能為 WAF 封鎖頁）：{text[:60]!r}")
        if code != 200:
            raise TwseError(f"MI_5MINS_HIST {yyyymm}: HTTP {code}（JSON 但非 200）：{json.dumps(body, ensure_ascii=False)[:80]}")
        return parse_index_hist(body)


def months_between(start_yyyymm: str, end_yyyymm: str) -> list[str]:
    y, m = int(start_yyyymm[:4]), int(start_yyyymm[4:6])
    ey, em = int(end_yyyymm[:4]), int(end_yyyymm[4:6])
    out = []
    while (y, m) <= (ey, em):
        out.append(f"{y:04d}{m:02d}")
        m += 1
        if m == 13:
            y, m = y + 1, 1
    return out
