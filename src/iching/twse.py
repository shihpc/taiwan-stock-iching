"""證交所／櫃買官方端點（Hetzner 可達、本雲端容器被 WAF 擋——P0-A §3；本檔在此**無法實測**）。

1. `MI_5MINS_HIST`（裁定 4：大盤開盤價以證據定）：
   `https://www.twse.com.tw/rwd/zh/afterTrading/MI_5MINS_HIST?date=YYYYMM01&response=json`，按月。
   **欄位名與日期格式未實測**：程式對欄位做防禦——在 `fields` 找含「日期」「開盤」「收盤」的欄，
   日期接受民國 `111/01/03`、`2022/01/03`、`2022-01-03`；找不到就明確報錯、不靜默。
   回應形狀假設同 taiwan-flows src/totals.py 用過的 FMTQIK：`{"stat":"OK","fields":[...],"data":[[...]]}`
   （推測 TWSE rwd 端點共用此形狀；未實測）。
2. `BFI82U`／TPEx `insti/summary`（B1.5 法人口徑）：原始 JSON 全文落地，不在此解析。
3. WAF 封鎖頁是 HTTP 200 的 HTML（`docs/pre-registration.md` §1.2.3 實測），故**非 JSON 一律視為失敗**。
"""
from __future__ import annotations

import datetime as dt
import json
import re
import time
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


def compare_open(twse_rows: list[dict], fm_rows: list[dict], tol: float = 0.005) -> dict:
    """逐日比對 TWSE 官方開盤 vs FinMind `open`。tol＝視為一致的絕對差（指數兩位小數 → 0.005）。"""
    tw = {r["date"]: r for r in twse_rows if r.get("date") and r.get("open") is not None}
    fm = {str(r.get("date")): r for r in fm_rows if r.get("date") is not None and r.get("open") is not None}
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


class OfficialClient:
    """TWSE／TPEx 官方端點的節流 GET。4 秒全域間隔（taiwan-flows 經驗：連打約 6 次即被 IP 限流且不自動解除）。
    回 (status_code, body_json_or_None, text)。"""

    def __init__(self, *, interval: float = OFFICIAL_INTERVAL_SEC, session: requests.Session | None = None,
                 sleep: Callable[[float], None] = time.sleep, clock: Callable[[], float] = time.monotonic,
                 timeout: float = 30.0) -> None:
        self.interval = interval
        self.session = session or requests.Session()
        self._sleep = sleep
        self._clock = clock
        self._last = 0.0
        self.timeout = timeout
        self.n_requests = 0

    def get(self, url: str, params: dict[str, Any]) -> tuple[int, Any, str]:
        wait = self.interval - (self._clock() - self._last)
        if wait > 0:
            self._sleep(wait)
        self._last = self._clock()
        self.n_requests += 1
        r = self.session.get(url, params=params, headers=HEADERS, timeout=self.timeout)
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
