"""#77 T1：期交所夜盤外資淨口數抓取／解析／歸屬日（免網路；fixture 自 2026-10-23 實抓頁面裁出，見 docs/P4-PREVIEW.md §18）。"""
from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

import pytest
import requests

from iching import taifex_night as T

FIX = Path(__file__).resolve().parent / "fixtures" / "taifex"
HOL = json.loads((Path(__file__).resolve().parent / "fixtures" / "twse_holidays_2026.json").read_text(encoding="utf-8"))


def html(name: str) -> str:
    return (FIX / name).read_text(encoding="utf-8")


# ---------------------------------------------------------------- parse_night

@pytest.mark.parametrize("name,qd,long_,short,net", [
    ("night_2026-10-16.html", "2026-10-16", 24854, 24082, 772),
    ("night_2026-10-21.html", "2026/10/21", 19927, 19207, 720),
    ("night_2026-10-22.html", dt.date(2026, 10, 22), 18496, 19414, -918),
])
def test_parse_real_pages(name, qd, long_, short, net):
    r = T.parse_night(html(name), qd)
    assert (r["long"], r["short"], r["net"]) == (long_, short, net)
    assert r["query_date"] == T.to_date(qd).isoformat()
    assert len(r["raw_sha256"]) == 64
    assert r["long"] - r["short"] == r["net"]


def test_parse_takes_foreign_row_of_txf_block_not_subtotal():
    # 10-22：臺股期貨外資 −918；自營商 +536；期貨小計自營商同為 +536——取到的必須是外資那列
    r = T.parse_night(html("night_2026-10-22.html"), "2026-10-22")
    assert r["net"] == -918 and r["net_amt_k"] == -8656996


def _foreign_row(long_: int, short: int, bg: str = "#D0E0F0") -> str:
    """合成一列「外資」（7 格：身份別＋6 數字），形狀同實抓頁面的非首列。"""
    net = long_ - short
    nums = [long_, long_ * 9500, short, short * 9500, net, net * 9500]
    tds = "".join(f'<TD align=right nowrap bgcolor=\'{bg}\'><div align="right"><span class="blue"> {n:,}</span></div></TD>'
                  for n in nums)
    return (f'<TR class="12bk"><td class="sheet-sticky serial-3" scope="row"><div align="center" class="fw-bold fs-sm">'
            f'外資</div></td>{tds}</TR>\n')


def test_subtotal_foreign_row_not_taken():
    # 合成：期貨小計區補一列外資（數字與臺股期貨外資列不同）；必須仍取臺股期貨的 −918、不得因此判成「不唯一」
    page = html("night_2026-10-22.html").replace("</TBODY>", _foreign_row(30000, 27000) + "</TBODY>")
    assert page.count("外資</div>") == 2
    r = T.parse_night(page, "2026-10-22")
    assert (r["long"], r["short"], r["net"]) == (18496, 19414, -918)


def test_duplicate_foreign_row_in_txf_block_is_missing_row():
    # 合成：臺股期貨區塊內出現第二列外資 → 不唯一 → missing_row（不得默默取第一列）
    base = html("night_2026-10-22.html")
    i = base.index("期貨<br>小計")
    i = base.rindex("<TR", 0, i)
    page = base[:i] + _foreign_row(100, 50, "#FFFFF0") + base[i:]
    with pytest.raises(T.NightFormatError) as e:
        T.parse_night(page, "2026-10-22")
    assert e.value.kind == "missing_row" and "不唯一" in str(e.value)


def test_no_data_returns_none():
    assert T.parse_night(html("night_2026-10-17_nodata.html"), "2026-10-17") is None


def test_future_page_without_table_is_format_error_not_no_data():
    with pytest.raises(T.NightFormatError) as e:
        T.parse_night(html("night_2026-12-31_future.html"), "2026-12-31")
    assert e.value.kind == "no_table"


def test_no_data_mark_only_counts_as_table_cell():
    page = html("night_2026-12-31_future.html").replace("</body>", "<p>說明：查無資料時請改查前一日</p></body>")
    with pytest.raises(T.NightFormatError) as e:
        T.parse_night(page, "2026-12-31")
    assert e.value.kind == "no_table"


def test_head_date_mismatch():
    with pytest.raises(T.NightFormatError) as e:
        T.parse_night(html("night_2026-10-22.html"), "2026-10-21")
    assert e.value.kind == "date_mismatch"


def test_missing_foreign_row():
    page = html("night_2026-10-22.html").replace("外資</div>", "外國人</div>")
    with pytest.raises(T.NightFormatError) as e:
        T.parse_night(page, "2026-10-22")
    assert e.value.kind == "missing_row"


def test_missing_txf_block():
    page = html("night_2026-10-22.html").replace("臺股期貨</div>", "小型臺指</div>")
    with pytest.raises(T.NightFormatError) as e:
        T.parse_night(page, "2026-10-22")
    assert e.value.kind == "missing_row"


def test_long_short_not_closed():
    page = html("night_2026-10-22.html").replace("<span class=\"blue\"> -918</span>", "<span class=\"blue\"> -917</span>")
    assert page != html("night_2026-10-22.html")
    with pytest.raises(T.NightFormatError) as e:
        T.parse_night(page, "2026-10-22")
    assert e.value.kind == "not_closed"


def test_bad_number():
    page = html("night_2026-10-22.html").replace("<span class=\"blue\"> 18,496</span>", "<span class=\"blue\"> --</span>")
    with pytest.raises(T.NightFormatError) as e:
        T.parse_night(page, "2026-10-22")
    assert e.value.kind == "bad_number"


# ---------------------------------------------------------------- fetch（mock）

class _Resp:
    def __init__(self, status: int, text: str = ""):
        self.status_code, self.text, self.encoding = status, text, None


class _Sess:
    def __init__(self, script):
        self.script, self.calls = list(script), []

    def post(self, url, data=None, headers=None, timeout=None):
        self.calls.append({"url": url, "data": data, "headers": headers, "timeout": timeout})
        x = self.script.pop(0)
        if isinstance(x, Exception):
            raise x
        return x


@pytest.fixture
def clock(monkeypatch):
    monkeypatch.setattr(T, "_last_request_at", [None])
    st = {"t": 1000.0, "sleeps": []}

    def now():
        return st["t"]

    def sleep(s):
        st["sleeps"].append(s)
        st["t"] += s
    return st, now, sleep


def test_fetch_posts_form_and_ua(clock):
    st, now, sleep = clock
    s = _Sess([_Resp(200, "ok")])
    assert T.fetch_night("2026-10-22", session=s, clock=now, sleep=sleep) == "ok"
    c = s.calls[0]
    assert c["url"] == T.URL
    assert c["data"] == {"queryType": "1", "doQuery": "1", "queryDate": "2026/10/22", "commodityId": "TXF"}
    assert c["headers"]["User-Agent"].startswith("Mozilla/5.0 (")
    assert c["timeout"] == 30


def test_fetch_retries_timeout_then_succeeds_with_backoff(clock):
    st, now, sleep = clock
    s = _Sess([requests.Timeout("t1"), requests.ConnectionError("c2"), _Resp(200, "ok")])
    r = T.fetch_raw("2026-10-22", session=s, clock=now, sleep=sleep)
    assert r.html == "ok" and r.attempts == 3 and r.status == 200
    assert T.BACKOFF_SEC in st["sleeps"] and 2 * T.BACKOFF_SEC in st["sleeps"]


def test_fetch_gives_up_after_retries(clock):
    st, now, sleep = clock
    s = _Sess([requests.Timeout("a"), requests.Timeout("b"), requests.Timeout("c")])
    with pytest.raises(T.NightNetworkError) as e:
        T.fetch_night("2026-10-22", session=s, clock=now, sleep=sleep, retries=2)
    assert e.value.status is None and e.value.attempts == 3 and len(s.calls) == 3


def test_fetch_5xx_retried_4xx_not(clock):
    st, now, sleep = clock
    s = _Sess([_Resp(503), _Resp(500), _Resp(200, "ok")])
    assert T.fetch_night("2026-10-22", session=s, clock=now, sleep=sleep) == "ok"
    s2 = _Sess([_Resp(403), _Resp(200, "never")])
    with pytest.raises(T.NightNetworkError) as e:
        T.fetch_night("2026-10-22", session=s2, clock=now, sleep=sleep)
    assert e.value.status == 403 and len(s2.calls) == 1


@pytest.mark.parametrize("status", [301, 304, 101])
def test_fetch_non_5xx_non_200_not_retried(clock, status):
    st, now, sleep = clock
    s = _Sess([_Resp(status), _Resp(200, "never")])
    with pytest.raises(T.NightNetworkError) as e:
        T.fetch_night("2026-10-22", session=s, clock=now, sleep=sleep)
    assert e.value.status == status and len(s.calls) == 1


@pytest.mark.parametrize("exc", [requests.exceptions.InvalidURL("x"), requests.exceptions.TooManyRedirects("y"),
                                 requests.exceptions.ChunkedEncodingError("z")])
def test_fetch_other_request_exceptions_not_retried(clock, exc):
    st, now, sleep = clock
    s = _Sess([exc, _Resp(200, "never")])
    with pytest.raises(T.NightNetworkError) as e:
        T.fetch_night("2026-10-22", session=s, clock=now, sleep=sleep)
    assert e.value.status is None and e.value.attempts == 1 and len(s.calls) == 1


def test_fetch_read_timeout_and_connect_timeout_retried(clock):
    st, now, sleep = clock
    s = _Sess([requests.exceptions.ReadTimeout("r"), requests.exceptions.ConnectTimeout("c"), _Resp(200, "ok")])
    assert T.fetch_night("2026-10-22", session=s, clock=now, sleep=sleep) == "ok" and len(s.calls) == 3


def test_throttle_min_interval_between_requests(clock):
    st, now, sleep = clock
    s = _Sess([_Resp(200, "a"), _Resp(200, "b")])
    T.fetch_night("2026-10-21", session=s, clock=now, sleep=sleep)
    T.fetch_night("2026-10-22", session=s, clock=now, sleep=sleep)
    assert st["sleeps"] == [pytest.approx(T.MIN_INTERVAL_SEC)]
    assert T.MIN_INTERVAL_SEC >= 2


# ---------------------------------------------------------------- night_query_date

CAL = T.parse_holidays(HOL)


def test_holidays_parse_contract():
    assert CAL is not None and 2026 in CAL.years
    assert T.parse_holidays({**HOL, "schema": True}) is None
    assert T.parse_holidays({**HOL, "years": []}) is None
    assert T.parse_holidays({**HOL, "closed": ["2026/10/26"]}) is None
    assert T.parse_holidays([]) is None


def test_load_holidays_fail_open():
    def boom(url, timeout):
        raise OSError("down")
    assert T.load_holidays(boom) is None
    assert T.load_holidays(lambda u, t: b"not json") is None
    assert T.load_holidays(lambda u, t: json.dumps(HOL).encode()) == CAL


@pytest.mark.parametrize("base,want", [
    ("2026-10-21", "2026-10-22"),   # 週三 → 週四
    ("2026-10-15", "2026-10-16"),   # 週四 → 週五
    ("2026-10-16", "2026-10-19"),   # 週五 → 下週一
    ("2026-10-08", "2026-10-12"),   # 週四 → 10-09／10-10 國慶連假、週末 → 週一
    ("2026-10-23", "2026-10-27"),   # 週五 → 10-26（週一）休市 → 週二
    ("2026-09-24", "2026-09-29"),   # 中秋 09-25、週末、教師節 09-28
])
def test_night_query_date_with_calendar(base, want):
    assert T.night_query_date(base, CAL) == want


def test_night_query_date_fail_open_weekends_only():
    assert T.night_query_date("2026-10-23", None) == "2026-10-26"     # 行事曆缺：只排週末（國定假日前推錯一天，已知）
    assert T.night_query_date("2026-10-16", None) == "2026-10-19"
    uncovered = T.Holidays(frozenset({2025}), frozenset({"2026-10-26"}))
    assert T.night_query_date("2026-10-23", uncovered) == "2026-10-26"  # 年度不在 years＝未知、只排週末
