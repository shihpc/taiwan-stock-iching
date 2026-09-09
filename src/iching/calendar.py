"""兩份交易日曆（P1-B3 §B3.1 第 10 項；`spec/dimensions.json` targets.trading_calendar：calendar ∈ {tpe, us}）。

- tpe：`TaiwanStockPrice` data_id=TAIEX 有列的日期（raw_index_price）。
- us ：上爻用的美股序列有列的日期——取 `USStockPrice` data_id=^GSPC（raw_us_index）。
        （^SOX 同為上爻資料，兩者理論上同曆；report 會列出兩者日期集合的差異供人工確認。）

輸出 `data/calendar_tpe.json`／`data/calendar_us.json`（進 git），頂層依家族資料契約含
`schema`／`generated_at`（+08:00 產出時刻）／`date`（資料日＝最後一個交易日）／`status`。

美股時間對齊（P1-B1 §B1.6）：上爻取「截至台北 T 日 08:00 已收盤的最近一個美股交易日」。
推導：美股常規盤收於美東 16:00 ＝ 台北翌日 04:00（夏令）或 05:00（冬令），兩者皆早於 08:00；
故台北 T 日 08:00 時「已收盤」的美股交易日 ＝ 日期 ≤ T−1（曆日）的最近一個美股交易日。
此處的 −1 曆日是**跨時區對齊**、不是視窗長度（§16.5 禁的是拿 timedelta 當交易日視窗）。
"""
from __future__ import annotations

import datetime as dt
import json
from pathlib import Path
from typing import Iterable, Sequence

from .config import DATA_END, PRICE_WARMUP_START, TAIPEI

CALENDAR_SCHEMA = 1
SOURCE = {
    "tpe": {"dataset": "TaiwanStockPrice", "data_id": "TAIEX", "table": "raw_index_price"},
    "us": {"dataset": "USStockPrice", "data_id": "^GSPC", "table": "raw_us_index"},
}


def build_calendar(dates: Iterable[str]) -> list[str]:
    """去重、只留 YYYY-MM-DD、排序。"""
    out = set()
    for d in dates:
        if not d:
            continue
        s = str(d)[:10]
        try:
            dt.date.fromisoformat(s)
        except ValueError:
            continue
        out.add(s)
    return sorted(out)


def calendar_payload(calendar: str, dates: Sequence[str], data_version: str,
                     now: dt.datetime | None = None) -> dict:
    if calendar not in SOURCE:
        raise ValueError(f"calendar 須為 tpe/us，得到 {calendar!r}")
    now = now or dt.datetime.now(TAIPEI)
    dates = list(dates)
    return {
        "schema": CALENDAR_SCHEMA,
        "generated_at": now.astimezone(TAIPEI).isoformat(timespec="seconds"),
        "date": dates[-1] if dates else None,
        "status": "ok" if dates else "empty",
        "calendar": calendar,
        "source_dataset": SOURCE[calendar]["dataset"],
        "source_data_id": SOURCE[calendar]["data_id"],
        "data_version": data_version,
        "start": dates[0] if dates else None,
        "end": dates[-1] if dates else None,
        "n": len(dates),
        "dates": dates,
    }


def write_calendar_json(path: Path, payload: dict) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps(payload, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")


def load_calendar_json(path: Path) -> list[str]:
    p = Path(path)
    if not p.exists():
        return []
    try:
        j = json.loads(p.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return []
    if not isinstance(j, dict) or j.get("schema") != CALENDAR_SCHEMA or not isinstance(j.get("dates"), list):
        return []
    return build_calendar(j["dates"])


def months_in(start: str, end: str) -> list[str]:
    """[start, end] 涵蓋的 'YYYY-MM' 月份序列（含端點所在月）。"""
    y, m = int(start[:4]), int(start[5:7])
    ey, em = int(end[:4]), int(end[5:7])
    out = []
    while (y, m) <= (ey, em):
        out.append(f"{y:04d}-{m:02d}")
        m += 1
        if m == 13:
            y, m = y + 1, 1
    return out


def calendar_gaps(dates: Sequence[str], start: str, end: str) -> list[str]:
    """[start, end] 內**沒有任何日期**的月份（'YYYY-MM'）。"""
    have = {d[:7] for d in dates}
    return [mo for mo in months_in(start, end) if mo not in have]


def calendar_covers(dates: Sequence[str], start: str, end: str) -> bool:
    """日曆是否涵蓋 [start, end]：**每個月至少 1 個日期**（2026-09-09 驗收更正：原只看首尾，
    `["2020-01-02","2020-12-31","2026-01-05","2026-08-31"]` 會被判涵蓋、缺五年靜默寫進 data/）。
    月底假期不會讓整月為空，故以月為粒度既擋得住中間缺口、又不被假期誤判。"""
    if not dates:
        return False
    return not calendar_gaps(dates, start, end)


def write_calendars(tpe_dates: Sequence[str], us_dates: Sequence[str], data_version: str,
                    data_dir: Path, cache_dir: Path, *, full_start: str = PRICE_WARMUP_START,
                    full_end: str = DATA_END, now: dt.datetime | None = None) -> dict[str, dict]:
    """把兩份日曆寫出。**只有涵蓋 [full_start, full_end] 的才寫進 git 追蹤的 data/**；
    部分日曆一律寫到 cache/calendar_partial_<name>.json（2026-09-09 驗收更正：原本每次 run 都寫 data/，
    56 天的 smoke-test 殘缺日曆就是這樣進了 git）。回 {name: {"path", "full", "n"}}；無資料者 path=None。"""
    out: dict[str, dict] = {}
    for name, dates in (("tpe", list(tpe_dates)), ("us", list(us_dates))):
        if not dates:
            out[name] = {"path": None, "full": False, "n": 0}
            continue
        full = calendar_covers(dates, full_start, full_end)
        path = Path(data_dir) / f"calendar_{name}.json" if full else Path(cache_dir) / f"calendar_partial_{name}.json"
        write_calendar_json(path, calendar_payload(name, dates, data_version, now))
        out[name] = {"path": path, "full": full, "n": len(dates)}
    return out


def us_session_closed_by(tpe_date: str, us_dates: Sequence[str]) -> str | None:
    """截至台北 `tpe_date` 08:00 已收盤的最近一個美股交易日（見模組 docstring 的推導）。
    `us_dates` 須已排序。找不到（序列起點之後）回 None。"""
    cutoff = (dt.date.fromisoformat(tpe_date) - dt.timedelta(days=1)).isoformat()
    best = None
    for d in us_dates:
        if d <= cutoff:
            best = d
        else:
            break
    return best


def window_dates(cal: Sequence[str], end_date: str, n: int) -> list[str]:
    """交易日視窗：以曆內 ≤ end_date 的最後 n 個交易日（供後續模組；不得用 timedelta）。"""
    idx = [i for i, d in enumerate(cal) if d <= end_date]
    if not idx:
        return []
    j = idx[-1] + 1
    return list(cal[max(0, j - n):j])
