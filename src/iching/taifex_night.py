"""期交所「三大法人－區分各期貨契約－夜盤」外資多空淨額口數（裁定 #77 試算卦五爻的推算來源；`docs/P4-PREVIEW.md` §16／§18）。

本模組只負責**抓取與嚴格解析**，不碰 `data/`、不改計分、不產任何檔（T1）。推算值（前一交易日官方淨未平倉＋夜盤淨額）
與試算卦產出屬 T2 之後的 PR。

端點（2026-10-18／10-22／10-23 經本機 proxy 實查，非 GitHub runner）：
    POST https://www.taifex.com.tw/cht/3/futContractsDateAh
    form：queryType=1&doQuery=1&queryDate=YYYY/MM/DD&commodityId=TXF
頁面形狀（實查）：
- 有資料：`<span class="right">日期YYYY/MM/DD</span>` 表頭＋表格；表格第一組 rowgroup 為「臺股期貨」三列（自營商／投信／外資），
  每列 6 個數字欄＝多方口數、多方契約金額、空方口數、空方契約金額、多空淨額口數、多空淨額契約金額（千元）；之後是「期貨小計」。
- 「查無資料」：同一頁框、無資料表格、有一個文字恰為 `查無資料` 的儲存格（例：2026/10/17 週六；2026/10/23 於 10/23 15:07 查）。
- 未來日期：頁框完整但既無表頭日期也無「查無資料」（例：2026/12/31 於 10/23 查，約 100KB）——歸「格式異常 no_table」，
  不讀成「無資料」。
- 每頁頁尾都有「本站限制直接存取主機IP」字樣（不是被擋的訊號）；被擋的真實形狀未觀測到。

狀態分三類（呼叫端必須分開處理）：
- 無資料：`parse_night` 回 None。
- 格式異常：`NightFormatError`（`.kind`＝no_table／date_mismatch／missing_row／not_closed／bad_number）。
- 網路失敗：`NightNetworkError`（逾時／連線失敗／5xx 重試用盡，或其他請求例外／非 200 的非 5xx 狀態不重試；`.status` 為最後一次 HTTP 狀態，連線失敗為 None）。
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import re
import sys
import time
from dataclasses import dataclass, field
from html.parser import HTMLParser
from typing import Any, Callable

import requests

URL = "https://www.taifex.com.tw/cht/3/futContractsDateAh"
COMMODITY_ID = "TXF"
PRODUCT = "臺股期貨"
IDENTITY = "外資"
# 一般瀏覽器字串（期交所頁尾寫「本站限制直接存取主機IP，請使用網址連結瀏覽」；用一般 UA 降低被當成程式存取的機率，未實測差異）
USER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")
MIN_INTERVAL_SEC = 2.0      # 兩次請求最短間隔（節流）
BACKOFF_SEC = 5.0           # 重試退避：第 n 次重試前等 BACKOFF_SEC × n
NO_DATA_MARK = "查無資料"
_HEAD_DATE = re.compile(r"日期\s*(\d{4})/(\d{1,2})/(\d{1,2})")

HOLIDAYS_URL = "https://raw.githubusercontent.com/shihpc/taiwan-flow-live-v2/main/data/twse_holidays.json"
HOLIDAYS_TIMEOUT_SEC = 10
_ISO = re.compile(r"^\d{4}-\d{2}-\d{2}$")


class NightError(Exception):
    pass


class NightFormatError(NightError):
    def __init__(self, kind: str, msg: str):
        super().__init__(f"{kind}: {msg}")
        self.kind = kind


class NightNetworkError(NightError):
    def __init__(self, msg: str, status: int | None = None, attempts: int = 0):
        super().__init__(msg)
        self.status, self.attempts = status, attempts


# ---------------------------------------------------------------- 日期

def to_date(x: Any) -> dt.date:
    """接受 date、'YYYY-MM-DD'、'YYYY/MM/DD'。"""
    if isinstance(x, dt.datetime):
        return x.date()
    if isinstance(x, dt.date):
        return x
    s = str(x).strip().replace("/", "-")
    return dt.date.fromisoformat(s)


def form_date(x: Any) -> str:
    return to_date(x).strftime("%Y/%m/%d")


# ---------------------------------------------------------------- 抓取

_last_request_at: list[float | None] = [None]   # 模組層節流狀態（monotonic 秒；None＝本行程尚未請求）


def _throttle(clock: Callable[[], float], sleep: Callable[[float], None]) -> None:
    prev = _last_request_at[0]
    if prev is not None:
        wait = prev + MIN_INTERVAL_SEC - clock()
        if wait > 0:
            sleep(wait)
    _last_request_at[0] = clock()


@dataclass
class FetchResult:
    query_date: str          # YYYY-MM-DD
    status: int
    html: str
    attempts: int
    elapsed_sec: float


def fetch_raw(query_date: Any, *, session: requests.Session | None = None, timeout: float = 30,
              retries: int = 2, clock: Callable[[], float] = time.monotonic,
              sleep: Callable[[float], None] = time.sleep) -> FetchResult:
    """POST 一次查詢。只有 `requests.Timeout`／`requests.ConnectionError` 與 HTTP ≥ 500 會重試（最多 1+retries 次、退避）；
    其他 `RequestException`（例：InvalidURL、TooManyRedirects）與 200 以外的非 5xx 狀態（1xx／3xx／4xx）不重試、立即 raise。
    `requests` 預設跟隨重導，最終回應仍為 3xx 屬異常、不重試。重試用盡或不重試的失敗 → NightNetworkError。"""
    qd = to_date(query_date)
    sess = session or requests.Session()
    data = {"queryType": "1", "doQuery": "1", "queryDate": form_date(qd), "commodityId": COMMODITY_ID}
    headers = {"User-Agent": USER_AGENT, "Referer": URL,
               "Accept": "text/html,application/xhtml+xml;q=0.9,*/*;q=0.8"}
    status: int | None = None
    last = ""
    for attempt in range(1, retries + 2):
        if attempt > 1:
            sleep(BACKOFF_SEC * (attempt - 1))
        _throttle(clock, sleep)
        t0 = clock()
        try:
            r = sess.post(URL, data=data, headers=headers, timeout=timeout)
        except (requests.Timeout, requests.ConnectionError) as e:
            status, last = None, f"{type(e).__name__}: {e}"
            continue
        except requests.RequestException as e:
            raise NightNetworkError(f"期交所夜盤查詢失敗（{qd.isoformat()}）：{type(e).__name__}: {e}", None, attempt) from e
        status = r.status_code
        if status == 200:
            r.encoding = "utf-8"   # 實查頁面為 UTF-8（file(1) 判定）；不信任 requests 的猜測
            return FetchResult(qd.isoformat(), status, r.text, attempt, round(clock() - t0, 3))
        last = f"HTTP {status}"
        if status < 500:
            break
    raise NightNetworkError(f"期交所夜盤查詢失敗（{qd.isoformat()}）：{last}", status, attempt)


def fetch_night(query_date: Any, *, session: requests.Session | None = None, timeout: float = 30,
                retries: int = 2, **kw) -> str:
    """回 HTML 字串（200）；失敗 raise NightNetworkError。"""
    return fetch_raw(query_date, session=session, timeout=timeout, retries=retries, **kw).html


# ---------------------------------------------------------------- 解析

class _Rows(HTMLParser):
    """收集 <table> 內每個 <tr> 的儲存格純文字（th／td 皆收）。"""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.rows: list[list[str]] = []
        self._depth = 0
        self._row: list[str] | None = None
        self._cell: list[str] | None = None

    def handle_starttag(self, tag, attrs):
        if tag == "table":
            self._depth += 1
        elif self._depth and tag == "tr":
            self._row = []
        elif self._depth and tag in ("td", "th") and self._row is not None:
            self._cell = []
        elif tag == "br" and self._cell is not None:
            self._cell.append("")

    def handle_endtag(self, tag):
        if tag in ("td", "th") and self._cell is not None and self._row is not None:
            self._row.append("".join("".join(self._cell).split()))
            self._cell = None
        elif tag == "tr" and self._row is not None:
            self.rows.append(self._row)
            self._row = None
        elif tag == "table" and self._depth:
            self._depth -= 1

    def handle_data(self, data):
        if self._cell is not None:
            self._cell.append(data)


def _int(s: str) -> int:
    t = s.replace(",", "").strip()
    if not re.fullmatch(r"-?\d+", t):
        raise NightFormatError("bad_number", f"數字欄無法解析：{s!r}")
    return int(t)


def parse_night(html: str, query_date: Any) -> dict | None:
    """嚴格解析；無資料回 None，格式不符 raise NightFormatError。

    回傳 {query_date, long, short, net, long_amt_k, short_amt_k, net_amt_k, raw_sha256}（口數；金額千元）。
    驗：表頭日期＝query_date；「臺股期貨」區塊有且只有一列「外資」；多方口數−空方口數＝多空淨額口數。
    """
    qd = to_date(query_date)
    sha = hashlib.sha256(html.encode("utf-8")).hexdigest()
    p = _Rows()
    p.feed(html)
    m = _HEAD_DATE.search(html)
    if m is None:
        # 「查無資料」須是表格儲存格的完整文字（實查：<td class="13red">&nbsp;查無資料</td>），註解或說明文字裡的同字不算
        if any(c == NO_DATA_MARK for row in p.rows for c in row):
            return None
        raise NightFormatError("no_table", "頁面無表頭日期、也無「查無資料」儲存格（未來日期或頁面改版）")
    head = dt.date(*(int(x) for x in m.groups()))
    if head != qd:
        raise NightFormatError("date_mismatch", f"表頭日期 {head.isoformat()} ≠ 查詢日 {qd.isoformat()}")
    product = None
    hits = []
    for row in p.rows:
        if len(row) >= 9:                     # rowgroup 首列：序號、商品名稱、身份別、6 數字
            product = row[1]
        elif row and "小計" in row[0]:         # 期貨小計／總計 rowgroup 首列
            product = row[0]
        if len(row) < 7 or product != PRODUCT:
            continue
        if row[-7] == IDENTITY:
            hits.append(row[-6:])
    if not hits:
        raise NightFormatError("missing_row", f"找不到「{PRODUCT}」區塊的「{IDENTITY}」列")
    if len(hits) > 1:
        raise NightFormatError("missing_row", f"「{PRODUCT}」區塊的「{IDENTITY}」列不唯一（{len(hits)} 列）")
    long_, long_amt, short, short_amt, net, net_amt = (_int(x) for x in hits[0])
    if long_ - short != net:
        raise NightFormatError("not_closed", f"多方 {long_} − 空方 {short} ≠ 淨額 {net}")
    return {"query_date": qd.isoformat(), "long": long_, "short": short, "net": net,
            "long_amt_k": long_amt, "short_amt_k": short_amt, "net_amt_k": net_amt, "raw_sha256": sha}


# ---------------------------------------------------------------- 夜盤歸屬日（休市行事曆）

@dataclass(frozen=True)
class Holidays:
    """家族休市行事曆（taiwan-flow-live-v2 `data/twse_holidays.json`；契約見該 repo `docs/holiday-calendar.md` §1）。
    驗證規則比照 taiwan-flow-live-v2／taiwan-flows 的 `src/twse_holidays.py`（schema 必須等於 1 且非 bool）。"""
    years: frozenset
    closed: frozenset
    names: dict = field(default_factory=dict)

    def covers(self, day: dt.date) -> bool:
        return day.year in self.years

    def is_holiday(self, day: dt.date) -> bool:
        return self.covers(day) and day.isoformat() in self.closed


def parse_holidays(doc: Any) -> Holidays | None:
    if not isinstance(doc, dict):
        return None
    schema = doc.get("schema")
    if isinstance(schema, bool) or schema != 1:
        return None
    years, closed, names = doc.get("years"), doc.get("closed"), doc.get("names") or {}
    if not isinstance(years, list) or not years or \
            not all(isinstance(y, int) and not isinstance(y, bool) for y in years):
        return None
    if not isinstance(closed, list) or not all(isinstance(d, str) and _ISO.match(d) for d in closed):
        return None
    if not isinstance(names, dict):
        names = {}
    return Holidays(frozenset(years), frozenset(closed), {str(k): str(v) for k, v in names.items()})


def load_holidays(fetch: Callable[[str, float], Any] | None = None) -> Holidays | None:
    """讀 raw URL；任何失敗回 None（呼叫端 fail-open＝只排週末），不拋。"""
    try:
        if fetch is None:
            r = requests.get(HOLIDAYS_URL, timeout=HOLIDAYS_TIMEOUT_SEC)
            if r.status_code != 200:
                raise RuntimeError(f"HTTP {r.status_code}")
            raw: Any = r.content
        else:
            raw = fetch(HOLIDAYS_URL, HOLIDAYS_TIMEOUT_SEC)
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8")
        cal = parse_holidays(json.loads(raw))
        if cal is not None:
            return cal
        print("::warning::休市行事曆形狀不合契約 → 只排週末（fail-open）", file=sys.stderr)
    except Exception as e:  # noqa: BLE001
        print(f"::warning::休市行事曆讀不到（{type(e).__name__}）→ 只排週末（fail-open）", file=sys.stderr)
    return None


def is_closed(day: dt.date, holidays: Holidays | None) -> bool:
    return day.weekday() >= 5 or (holidays is not None and holidays.is_holiday(day))


def night_query_date(base_date: Any, holidays: Holidays | None) -> str:
    """base_date（最新正式卦日 T）之下一個台北交易日（YYYY-MM-DD）＝T 日 15:00 起夜盤的歸屬日（推論，見 §18.4）。

    休市＝週末 ∪ 行事曆 `closed`；行事曆為 None 或該年不在 `years` → 只排週末（fail-open，國定假日前可能推錯一天）。
    """
    d = to_date(base_date) + dt.timedelta(days=1)
    for _ in range(31):
        if not is_closed(d, holidays):
            return d.isoformat()
        d += dt.timedelta(days=1)
    raise ValueError(f"{base_date} 之後 31 天內找不到交易日（行事曆異常）")
