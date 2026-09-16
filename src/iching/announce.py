"""P2 公告收集器核心（裁定 #43／#45；規格 `docs/P2-COLLECTOR.md` §1～§3、S1 A3 事件版本鏈）。

純函式為主：解析（CSV 兩組表頭／mopsov HTML）→ 正規化列 → `event_id`／`content_hash` → 事件檔合併（A3.2 三條規則）
→ 冪等寫檔 → `_timing.json` 全量重算 → 隔日核對 `verify_day`。網路只在 `fetch_csv`／`fetch_mopsov` 兩支，`http` 可注入
（`scripts/collect_events.py` 的 `RequestsHttp`／`FixtureHttp`）。

**事件檔** `data/events/<發言日>.json.gz`（以**發言日期**為鍵，不是執行日——修訂表第 15 列）：
`{schema:1, date, events:{event_id:{market, stock_id, name, spoke_date, spoke_time, versions:[…]}}}`；
每個版本 `{version_no, version_id, content_hash, status, supersedes, fulltext_missing, first_seen_at, revised_at,
subject, clause, fact_date, body, sources}`。序列化走 `bundle_io.dumps_json`（鍵排序、無空白、gzip mtime=0）→ 同輸入同位元組。

**`event_id` 刻意不含主旨**：`sha1("{market}|{stock_id}|{spoke_date}|{spoke_time}")[:16]`。同一公告的「更正版只有主旨」
（mopsov 補回）與「全文補齊」（CSV 晚到）才會落在同一鏈；MOPS 的更正公告通常是新的發言時間＝新事件，跨事件關聯
`relates_to` 留 P3。`content_hash`＝sha1(正規化 subject|clause|fact_date|body)。

**合併規則（A3.2）**，`merge()`：
① 同 `event_id`、最新版 `fulltext_missing` 且新列有 `body` 且主旨正規化相同 → **同版補全**（`revised_at` 不動）；
② `content_hash` 不同且非① → 新版本（舊版 `superseded`、新版 `supersedes`＝舊 `version_id`、`revised_at=now`、無 body 則 `fulltext_missing`）；
③ 相同 → 無變更（只把來源名併進 `sources`）。
**②的一個刻意收窄**：只有主旨的列（mopsov）若主旨與最新版相同，一律視為③而不是②——它的 `content_hash` 必然與有全文的
版本不同（沒有 body），照字面套②會讓每次隔日核對都替每個事件多開一版。A3.2 規則 1「同一版本可同時由 CSV（有全文）與
mopsov（只有主旨）取得 → 取較完整者」就是這個意思。
"""
from __future__ import annotations

import csv
import gzip
import hashlib
import html as html_mod
import io
import re
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping

from . import bundle_io as B
from . import daily_core as DC

TPE = timezone(timedelta(hours=8))
SCHEMA = 1
EVENTS_DIR = "data/events"
TIMING_FILE = "_timing.json"
RUNS_DIR = "runs/collect"

UA = "Mozilla/5.0 (X11; Linux x86_64) taiwan-stock-iching collector (+https://github.com/shihpc/taiwan-stock-iching)"
CSV_URLS = {
    "twse_csv": ("sii", "https://mopsfin.twse.com.tw/opendata/t187ap04_L.csv"),
    "tpex_csv": ("otc", "https://mopsfin.twse.com.tw/opendata/t187ap04_O.csv"),
}
MOPSOV_URL = "https://mopsov.twse.com.tw/mops/web/ajax_t05st01"
# TWSE WAF 擋頁：HTTP 200／307 都見過（probe_mops.WAF_MARKERS 同一組字串；收集器不 import probe 腳本，避免 scripts→src 反向依賴）
WAF_MARKERS = ("FOR SECURITY REASONS, THIS PAGE CAN NOT BE ACCESSED", "因為安全性考量")
MOPSOV_EMPTY = "資料庫中查無需求資料"

# 兩組 CSV 表頭（docs/P2-COLLECTOR.md §2）：中文九欄＝Actions 實測 _L／_O 都是它；英文＝P0-A 修訂表第 13 列記的 TPEx 舊欄名
# （只記了 Date／SecuritiesCompanyCode／CompanyName 三個，其餘「逐字相同」）。映射先比欄名，比不到的欄退回九欄的**位置**。
CSV_COLS = ("report_date", "spoke_date", "spoke_time", "stock_id", "name", "subject", "clause", "fact_date", "body")
CSV_ALIASES: dict[str, tuple[str, ...]] = {
    "report_date": ("出表日期", "Date"),
    "spoke_date": ("發言日期",),
    "spoke_time": ("發言時間",),
    "stock_id": ("公司代號", "SecuritiesCompanyCode"),
    "name": ("公司名稱", "CompanyName"),
    "subject": ("主旨",),
    "clause": ("符合條款",),
    "fact_date": ("事實發生日",),
    "body": ("說明",),
}
WITHDRAWN_MARKERS = ("撤銷", "撤回")
LAST_BAND_TIME = "23:45:00"
REASONS = ("cross_day", "withdrawn", "late_after_last_band", "waf", "source_missing_at_time")


class AnnounceError(Exception):
    pass


class WafBlocked(AnnounceError):
    """來源回 WAF 擋頁：該來源失敗，**不是空資料**（§2）。"""


def is_waf_page(text: str) -> bool:
    return any(m in text for m in WAF_MARKERS)


# ---------------------------------------------------------------------------
# 正規化
_WS_RE = re.compile(r"[\s\u3000]+")


def norm_text(s: str | None) -> str:
    """主旨／說明正規化：`&nbsp;`（含已 unescape 的 U+00A0）去掉、全形空白→半形、連續空白合併、首尾去空白。"""
    if s is None:
        return ""
    s = s.replace("&nbsp;", " ").replace("\u00a0", " ")
    return _WS_RE.sub(" ", s).strip()


def norm_body(s: str | None) -> str | None:
    """說明全文：換行統一 `\\n`、每行去尾空白、整體去首尾空行；空字串→None（＝沒有全文）。"""
    if s is None:
        return None
    s = s.replace("\r\n", "\n").replace("\r", "\n")
    lines = [ln.rstrip() for ln in s.split("\n")]
    out = "\n".join(lines).strip("\n").strip()
    return out or None


def roc_to_iso(s: str | None) -> str | None:
    """民國日期 → `YYYY-MM-DD`：`1150907`（7 碼）／`115/09/07`／`115-09-07`；已是 ISO 原樣回；空／解不出 → None。"""
    if s is None:
        return None
    t = s.strip().replace("\u00a0", "")
    if not t:
        return None
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", t):
        return t
    m = re.fullmatch(r"(\d{2,3})[/\-.](\d{1,2})[/\-.](\d{1,2})", t)
    if m:
        y, mo, d = (int(x) for x in m.groups())
    elif re.fullmatch(r"\d{7}", t):
        y, mo, d = int(t[:3]), int(t[3:5]), int(t[5:7])
    elif re.fullmatch(r"\d{6}", t):
        y, mo, d = int(t[:2]), int(t[2:4]), int(t[4:6])
    else:
        return None
    try:
        return datetime(y + 1911, mo, d).strftime("%Y-%m-%d")
    except ValueError:
        return None


def time_to_hms(s: str | None) -> str | None:
    """發言時間：`70003`（無前導零 HHMMSS，P0-A 最大坑）→ 補零至 6 碼 → `07:00:03`；`06:41:17` 原樣；解不出 → None。"""
    if s is None:
        return None
    t = s.strip().replace("\u00a0", "")
    m = re.fullmatch(r"(\d{1,2}):(\d{2}):(\d{2})", t)
    if m:
        h, mi, se = (int(x) for x in m.groups())
    elif re.fullmatch(r"\d{1,6}", t):
        t6 = t.zfill(6)
        h, mi, se = int(t6[:2]), int(t6[2:4]), int(t6[4:6])
    else:
        return None
    if h > 23 or mi > 59 or se > 59:
        return None
    return f"{h:02d}:{mi:02d}:{se:02d}"


# ---------------------------------------------------------------------------
# 解析
def _make_row(market: str, stock_id: str, name: str, spoke_date: str, spoke_time: str, subject: str,
              clause: str | None, fact_date: str | None, body: str | None) -> dict:
    return {"market": market, "stock_id": stock_id, "name": norm_text(name), "spoke_date": spoke_date,
            "spoke_time": spoke_time, "subject": norm_text(subject), "clause": (norm_text(clause) or None),
            "fact_date": fact_date, "body": norm_body(body)}


def csv_header_map(header: list[str]) -> tuple[dict[str, int], str | None]:
    """表頭 → 欄索引。回 (map, header_kind)：`zh`＝命中「公司代號」、`en`＝命中 `SecuritiesCompanyCode`、None＝兩者皆無（非本 CSV）。
    欄名比不到的欄退回九欄位置（§2：兩組除三個欄名外「逐字相同」）。"""
    cells = [norm_text(h).lstrip("\ufeff") for h in header]
    idx: dict[str, int] = {}
    for col, names in CSV_ALIASES.items():
        for i, c in enumerate(cells):
            if c in names:
                idx[col] = i
                break
    kind = "zh" if "公司代號" in cells else ("en" if "SecuritiesCompanyCode" in cells else None)
    if kind is None:
        return {}, None
    for i, col in enumerate(CSV_COLS):
        if col not in idx and i < len(cells):
            idx[col] = i
    return idx, kind


def parse_csv_report(text: str, market: str) -> tuple[list[dict], str | None, int]:
    """`(rows, header_kind, dropped)`。擋頁 → `WafBlocked`；表頭不是本 CSV → `AnnounceError`。
    缺 代號／發言日期／發言時間 任一（解不出）的列**丟棄並計數**，不讓一列壞資料毀掉整個來源。"""
    if is_waf_page(text):
        raise WafBlocked("CSV 來源回 WAF 擋頁")
    text = text.lstrip("\ufeff")
    rows = list(csv.reader(io.StringIO(text, newline="")))
    if not rows:
        raise AnnounceError("CSV 空白")
    idx, kind = csv_header_map(rows[0])
    if kind is None:
        raise AnnounceError(f"CSV 表頭不是公告表：{rows[0][:6]}")

    def cell(r: list[str], col: str) -> str | None:
        i = idx.get(col)
        return r[i] if i is not None and i < len(r) else None

    ncol = max(idx.values()) + 1        # 表頭應有的欄數（映射到的最後一欄）；欄數不足＝壞列，不得混進事件庫變成 fulltext_missing
    out: list[dict] = []
    dropped = 0
    for r in rows[1:]:
        if not any(c.strip() for c in r):
            continue
        if len(r) < ncol:
            dropped += 1
            continue
        sid = norm_text(cell(r, "stock_id"))
        sd, st = roc_to_iso(cell(r, "spoke_date")), time_to_hms(cell(r, "spoke_time"))
        if not sid or sd is None or st is None:
            dropped += 1
            continue
        out.append(_make_row(market, sid, cell(r, "name") or "", sd, st, cell(r, "subject") or "", cell(r, "clause"),
                             roc_to_iso(cell(r, "fact_date")), cell(r, "body")))
    return out, kind, dropped


def parse_csv(text: str, market: str) -> list[dict]:
    return parse_csv_report(text, market)[0]


_TR_OPEN_RE = re.compile(r"<tr(?:\s[^>]*)?>", re.I)
_TD_RE = re.compile(r"<t[dh][^>]*>(.*?)</t[dh]>", re.I | re.S)
_TAG_RE = re.compile(r"<[^>]+>")


def _cell_text(raw: str) -> str:
    return norm_text(html_mod.unescape(_TAG_RE.sub(" ", raw)))


def parse_mopsov(html: str, market: str) -> list[dict]:
    """mopsov `ajax_t05st01` 單日全市場表：`公司代號｜公司名稱｜發言日期（115/09/15）｜發言時間（06:41:17）｜主旨`（§5.1）。
    擋頁 → `WafBlocked`；**解析不到任何資料列**且頁面含「資料庫中查無需求資料」→ `[]`（不是錯）；解析得到列就照列回
    （主旨含「查無」字樣不會讓整頁變空）；既無列也無「查無」→ `AnnounceError`。列的認定看**內容**（第 3 格是民國日期、
    第 4 格是時間），不靠表頭位置；多出來的欄（按鈕等）忽略；外層 wrapper `<tr>` 不會吞掉內層第一列。
    `body`／`clause`／`fact_date` 一律 None。"""
    if is_waf_page(html):
        raise WafBlocked("mopsov 回 WAF 擋頁")
    out: list[dict] = []
    # 以 <tr> 開標籤切段、每段只看到下一個 <tr> 或 </tr> 為止：外層 wrapper 列（<tr><td><table><tr>…）的段落只剩沒關閉的
    # <td>，取不到格、自然跳過；最內層的列才會有 ≥5 格。不用 `<tr>.*?</tr>`——那會從外層 <tr> 吃到內層第一個 </tr>，把第一列吞掉。
    for seg in _TR_OPEN_RE.split(html)[1:]:
        end = seg.lower().find("</tr>")
        cells = [_cell_text(c) for c in _TD_RE.findall(seg if end < 0 else seg[:end])]
        if len(cells) < 5:
            continue
        sid, name, sd, st, subject = cells[0], cells[1], roc_to_iso(cells[2]), time_to_hms(cells[3]), cells[4]
        if not sid or sd is None or st is None or not re.fullmatch(r"[0-9A-Za-z]{2,10}", sid):
            continue
        out.append(_make_row(market, sid, name, sd, st, subject, None, None, None))
    if not out and MOPSOV_EMPTY not in html:
        # 沒有資料列也沒有「查無」字樣＝不是空、是回了別的東西（表單錯誤頁、殼頁…），不得記成 0 筆 OK
        raise AnnounceError("mopsov 回應既無資料列也無「資料庫中查無需求資料」")
    return out


# ---------------------------------------------------------------------------
# 識別與雜湊
def event_id(row: Mapping[str, Any]) -> str:
    key = f"{row['market']}|{row['stock_id']}|{row['spoke_date']}|{row['spoke_time']}"
    return hashlib.sha1(key.encode("utf-8")).hexdigest()[:16]


def content_hash(subject: str | None, clause: str | None, fact_date: str | None, body: str | None) -> str:
    key = "|".join([norm_text(subject), norm_text(clause), fact_date or "", norm_body(body) or ""])
    return hashlib.sha1(key.encode("utf-8")).hexdigest()


def row_hash(row: Mapping[str, Any]) -> str:
    return content_hash(row.get("subject"), row.get("clause"), row.get("fact_date"), row.get("body"))


# ---------------------------------------------------------------------------
# 事件檔與合併
def new_doc(date: str) -> dict:
    return {"schema": SCHEMA, "date": date, "events": {}}


def events_path(root: Path, date: str) -> Path:
    return Path(root) / EVENTS_DIR / f"{date}.json.gz"


def load_doc(root: Path, date: str) -> dict:
    p = events_path(root, date)
    if not p.exists():
        return new_doc(date)
    d = B.read_json_gz(p)
    if not isinstance(d, dict) or d.get("schema") != SCHEMA or d.get("date") != date or not isinstance(d.get("events"), dict):
        raise AnnounceError(f"事件檔 {p} 形狀不對（schema／date／events）")
    return d


def _new_version(row: Mapping[str, Any], no: int, eid: str, now_iso: str, source: str, supersedes: str | None) -> dict:
    body = norm_body(row.get("body"))
    return {"version_no": no, "version_id": f"{eid}#{no}", "content_hash": row_hash(row), "status": "active",
            "supersedes": supersedes, "fulltext_missing": body is None, "first_seen_at": now_iso, "revised_at": now_iso,
            "subject": norm_text(row.get("subject")), "clause": norm_text(row.get("clause")) or None,
            "fact_date": row.get("fact_date"), "body": body, "sources": [source]}


def _add_source(v: dict, source: str) -> bool:
    if source in v["sources"]:
        return False
    v["sources"] = sorted(set(v["sources"]) | {source})
    return True


def merge(existing: dict | None, rows: Iterable[Mapping[str, Any]], now_iso: str, source: str, date: str | None = None) -> tuple[dict, dict]:
    """把同一發言日的列併進事件檔（`existing` 為 None＝新檔）。回 `(doc, stats)`，
    stats＝`{received, new_events, new_versions, filled, unchanged, source_added}`。列的 `spoke_date` 必須＝檔的 `date`。
    冪等：同輸入再跑一次 → 全部落在③、doc 不變。`first_seen_at` 只在版本建立時寫、永不回填。"""
    rows = list(rows)
    if existing is None:
        if date is None:
            if not rows:
                raise AnnounceError("merge：沒有 existing 也沒有 rows，無從得知 date")
            date = str(rows[0]["spoke_date"])
        doc = new_doc(date)
    else:
        doc = existing
        date = str(doc["date"])
    stats = {"received": len(rows), "new_events": 0, "new_versions": 0, "filled": 0, "unchanged": 0, "source_added": 0}
    for row in rows:
        if str(row["spoke_date"]) != date:
            raise AnnounceError(f"merge：列的 spoke_date {row['spoke_date']} ≠ 檔的 date {date}")
        eid = event_id(row)
        ev = doc["events"].get(eid)
        if ev is None:
            doc["events"][eid] = {"market": row["market"], "stock_id": row["stock_id"], "name": norm_text(row.get("name")),
                                  "spoke_date": row["spoke_date"], "spoke_time": row["spoke_time"],
                                  "versions": [_new_version(row, 1, eid, now_iso, source, None)]}
            stats["new_events"] += 1
            continue
        latest = ev["versions"][-1]
        new_body = norm_body(row.get("body"))
        same_subject = norm_text(row.get("subject")) == norm_text(latest.get("subject"))
        if new_body is not None and latest.get("fulltext_missing") and same_subject:
            # ① 同版補全：只動內容欄，first_seen_at／revised_at／version_no／version_id 一律不動（守門：改完比對）
            keep = {k: latest[k] for k in ("first_seen_at", "revised_at", "version_no", "version_id", "status", "supersedes")}
            latest.update(fulltext_missing=False, body=new_body, clause=norm_text(row.get("clause")) or None,
                          fact_date=row.get("fact_date"), content_hash=row_hash(row))
            if any(latest[k] != v for k, v in keep.items()):
                raise AnnounceError(f"merge ①補全改到了不該動的欄：{[k for k, v in keep.items() if latest[k] != v]}")
            _add_source(latest, source)
            stats["filled"] += 1
        elif (new_body is None and same_subject) or row_hash(row) == latest.get("content_hash"):
            # ③ 無變更（只有主旨的列與最新版主旨相同，也算同版——見檔頭）
            stats["source_added" if _add_source(latest, source) else "unchanged"] += 1
        else:
            # ② 新版本
            latest["status"] = "superseded"
            no = int(latest["version_no"]) + 1
            ev["versions"].append(_new_version(row, no, eid, now_iso, source, latest["version_id"]))
            stats["new_versions"] += 1
        if not ev.get("name") and row.get("name"):
            ev["name"] = norm_text(row.get("name"))
    return doc, stats


def group_by_date(rows: Iterable[Mapping[str, Any]]) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {}
    for r in rows:
        out.setdefault(str(r["spoke_date"]), []).append(dict(r))
    return dict(sorted(out.items()))


def write_if_changed(path: Path, doc: dict) -> bool:
    """事件檔（gz）內容未變不寫檔（A3.3；沿 `daily_pipeline._write_if_changed` 的做法，比的是解壓後的位元組）。"""
    path = Path(path)
    new = B.dumps_json(doc)
    if path.exists():
        try:
            with gzip.open(path, "rb") as g:
                if g.read() == new:
                    return False
        except (OSError, EOFError):
            pass
    B.write_json_gz(path, doc)
    return True


def write_json_if_changed(path: Path, payload: dict) -> bool:
    new = DC.dumps(payload)
    if path.exists() and path.read_text(encoding="utf-8") == new:
        return False
    DC.write_json(path, payload)
    return True


# ---------------------------------------------------------------------------
# 發言時間分布（#7）：由事件檔**全量重算**（非增量 → 冪等）
def timing_from_docs(docs: Iterable[Mapping[str, Any]]) -> dict:
    days: dict[str, dict] = {}
    for doc in docs:
        hc = {f"{h:02d}": 0 for h in range(24)}
        after, total, by = 0, 0, {"sii": 0, "otc": 0}
        for ev in doc.get("events", {}).values():
            t = str(ev.get("spoke_time") or "")
            if not re.fullmatch(r"\d{2}:\d{2}:\d{2}", t):
                continue
            hc[t[:2]] += 1
            total += 1
            if t > "21:30:00":
                after += 1
            m = ev.get("market")
            if m in by:
                by[m] += 1
        days[str(doc["date"])] = {"hour_counts": hc, "after_2130": after, "total": total, "by_market": by}
    return {"schema": SCHEMA, "days": dict(sorted(days.items()))}


def list_event_files(root: Path) -> list[tuple[str, Path]]:
    d = Path(root) / EVENTS_DIR
    if not d.exists():
        return []
    out = []
    for p in sorted(d.iterdir()):
        m = re.fullmatch(r"(\d{4}-\d{2}-\d{2})\.json\.gz", p.name)
        if m:
            out.append((m.group(1), p))
    return out


def timing_update(root: Path) -> tuple[dict, bool]:
    """重算並寫 `data/events/_timing.json`；回 `(timing, written)`。"""
    docs = [B.read_json_gz(p) for _, p in list_event_files(root)]
    timing = timing_from_docs(docs)
    written = write_json_if_changed(Path(root) / EVENTS_DIR / TIMING_FILE, timing)
    return timing, written


# ---------------------------------------------------------------------------
# 隔日核對（§3 第 4 條、§16.1「當日覆蓋率」）
VERIFY_SOURCE = "mopsov-verify"


def _collected_by_collector(ev: Mapping[str, Any]) -> bool:
    """任一版本有非 `mopsov-verify` 的來源＝收集器自己收到過。只由核對補回的事件在下一次核對仍算缺漏（核對報告才冪等）。"""
    return any(s != VERIFY_SOURCE for v in ev.get("versions", []) for s in v.get("sources", []))


def missing_reason(row: Mapping[str, Any], date: str, waf: bool) -> str:
    if row["spoke_date"] != date:
        return "cross_day"
    if any(m in norm_text(row.get("subject")) for m in WITHDRAWN_MARKERS):
        return "withdrawn"
    if row["spoke_time"] > LAST_BAND_TIME:
        return "late_after_last_band"
    if waf:
        return "waf"
    return "source_missing_at_time"


def verify_day(date: str, mopsov_rows: Iterable[Mapping[str, Any]], docs: dict[str, dict], now_iso: str, *, waf: bool = False) -> dict:
    """對 mopsov 單日列表逐列找 `event_id`；缺漏標原因碼並**補進事件庫**（主旨層、`fulltext_missing=True`、source `mopsov-verify`、
    `first_seen_at`＝當下＝真實補收時間、不回填）。`docs`＝發言日→事件檔（可變；跨日列補進它自己那天的檔，呼叫端負責寫回）。
    回 `{date, mopsov_total, matched, missing:[{event_id, market, stock_id, spoke_date, spoke_time, subject, reason}], missing_rate, reasons}`。"""
    rows = list(mopsov_rows)
    matched, missing = 0, []
    for r in rows:
        eid = event_id(r)
        doc = docs.get(r["spoke_date"])
        ev = doc["events"].get(eid) if doc else None
        if ev is not None and _collected_by_collector(ev):
            matched += 1
            continue
        reason = missing_reason(r, date, waf)
        missing.append({"event_id": eid, "market": r["market"], "stock_id": r["stock_id"], "spoke_date": r["spoke_date"],
                        "spoke_time": r["spoke_time"], "subject": norm_text(r.get("subject")), "reason": reason})
    missing_ids = {x["event_id"] for x in missing}
    for d, group in group_by_date(m for m in rows if event_id(m) in missing_ids).items():
        docs[d], _ = merge(docs.get(d) or new_doc(d), group, now_iso, VERIFY_SOURCE)
    reasons = {k: sum(1 for m in missing if m["reason"] == k) for k in REASONS}
    total = len(rows)
    return {"schema": SCHEMA, "date": date, "mopsov_total": total, "matched": matched, "missing": missing,
            "missing_rate": (round(len(missing) / total, 4) if total else 0.0), "reasons": reasons}


# ---------------------------------------------------------------------------
# 網路層（唯一會打外面的兩支；`http` 可注入）
class HttpResult:
    __slots__ = ("status", "content", "content_type")

    def __init__(self, status: int, content: bytes, content_type: str = ""):
        self.status, self.content, self.content_type = status, content, content_type


Http = Callable[[str, str, dict | None], HttpResult]   # (method, url, data) → HttpResult；例外由呼叫端接


def decode_body(raw: bytes, ctype: str) -> str:
    m = re.search(r"charset=([\w-]+)", ctype or "", re.I)
    for enc in ([m.group(1)] if m else []) + ["utf-8-sig", "utf-8", "big5hkscs", "cp950"]:
        try:
            return raw.decode(enc)
        except (UnicodeDecodeError, LookupError):
            continue
    return raw.decode("utf-8", errors="replace")


def mopsov_form(typek: str, day: str) -> dict:
    """§5.1 實查的表單參數：`year` 民國 3 碼、`month` 兩位、`b_date`／`e_date` 只是「日」。`firstin=ture` 照表單原樣。"""
    d = datetime.strptime(day, "%Y-%m-%d")
    return {"encodeURIComponent": "1", "step": "1", "firstin": "ture", "off": "1", "keyword4": "", "code1": "",
            "TYPEK2": "", "checkbtn": "", "queryName": "co_id", "inpuType": "co_id", "TYPEK": typek, "co_id": "",
            "year": str(d.year - 1911), "month": f"{d.month:02d}", "b_date": f"{d.day:02d}", "e_date": f"{d.day:02d}"}


def _span(rows: list[dict]) -> dict | None:
    if not rows:
        return None
    ks = sorted(f"{r['spoke_date']} {r['spoke_time']}" for r in rows)
    return {"earliest": ks[0], "latest": ks[-1]}


def fetch_csv(http: Http, name: str) -> tuple[list[dict], dict]:
    """回 `(rows, source_record)`；`source_record`＝留痕用 `{name, market, status, http_status, bytes, rows, header_kind, waf, dropped, span, error}`。
    擋頁＝`status:"waf"`（不寫事件）；非 200／例外／表頭不對＝`status:"error"`。永不拋出。"""
    market, url = CSV_URLS[name]
    rec: dict[str, Any] = {"name": name, "market": market, "status": "error", "http_status": 0, "bytes": 0, "rows": 0,
                           "header_kind": None, "waf": False, "dropped": 0, "span": None, "error": None}
    try:
        r = http("GET", url, None)
        rec.update(http_status=r.status, bytes=len(r.content))
        text = decode_body(r.content, r.content_type)
        if is_waf_page(text):
            rec.update(status="waf", waf=True, error="WAF 擋頁")
            return [], rec
        if r.status != 200:
            rec["error"] = f"HTTP {r.status}"
            return [], rec
        rows, kind, dropped = parse_csv_report(text, market)
        rec.update(status="ok", rows=len(rows), header_kind=kind, dropped=dropped, span=_span(rows))
        return rows, rec
    except WafBlocked as e:
        rec.update(status="waf", waf=True, error=str(e))
    except Exception as e:  # noqa: BLE001 — 來源失敗要留痕、不炸整班
        rec["error"] = f"{type(e).__name__}: {e}"
    return [], rec


def fetch_mopsov(http: Http, typek: str, day: str) -> tuple[list[dict], dict]:
    rec: dict[str, Any] = {"name": f"mopsov_{typek}", "market": typek, "day": day, "status": "error", "http_status": 0,
                           "bytes": 0, "rows": 0, "waf": False, "empty": False, "error": None}
    try:
        r = http("POST", MOPSOV_URL, mopsov_form(typek, day))
        rec.update(http_status=r.status, bytes=len(r.content))
        text = decode_body(r.content, r.content_type)
        if is_waf_page(text):
            rec.update(status="waf", waf=True, error="WAF 擋頁")
            return [], rec
        if r.status != 200:
            rec["error"] = f"HTTP {r.status}"
            return [], rec
        rows = parse_mopsov(text, typek)
        rec.update(status="ok", rows=len(rows), empty=(not rows and MOPSOV_EMPTY in text))   # 與 parse_mopsov 的「查無」判定同義
        return rows, rec
    except WafBlocked as e:
        rec.update(status="waf", waf=True, error=str(e))
    except Exception as e:  # noqa: BLE001
        rec["error"] = f"{type(e).__name__}: {e}"
    return [], rec


class RequestsHttp:
    """生產用：`requests`＋probe 同款 UA，兩次請求間隔 ≥ `interval` 秒。"""

    def __init__(self, interval: float = 1.0, timeout: float = 30.0):
        self.interval, self.timeout, self._last = interval, timeout, 0.0

    def __call__(self, method: str, url: str, data: dict | None) -> HttpResult:
        import requests
        wait = self.interval - (time.monotonic() - self._last)
        if wait > 0:
            time.sleep(wait)
        try:
            if method == "POST":
                r = requests.post(url, data=data, headers={"User-Agent": UA}, timeout=self.timeout)
            else:
                r = requests.get(url, headers={"User-Agent": UA}, timeout=self.timeout)
        finally:
            self._last = time.monotonic()
        return HttpResult(r.status_code, r.content, r.headers.get("content-type", ""))


# ---------------------------------------------------------------------------
# 班次
BANDS = ("1530", "1830", "2345", "am")
BAND_TIME = {"1530": (15, 30), "1830": (18, 30), "2345": (23, 45), "am": (8, 30)}


def pick_band(now: datetime) -> tuple[str, str]:
    """`--band auto`：依台北時鐘取**最近一個已到點**的班（GitHub cron 常延遲 1～2 小時，班以「已過的最近排程」認）。
    回 `(band, scheduled_date)`；00:00～08:29 落在**前一日**的 2345 班（修訂表第 15 列：跨日延遲不可用執行日）。
    | 台北時刻 | band | scheduled_for 日 |
    | 08:30～15:29 | am | 當日 |  | 15:30～18:29 | 1530 | 當日 |  | 18:30～23:44 | 1830 | 當日 |
    | 23:45～23:59 | 2345 | 當日 |  | 00:00～08:29 | 2345 | 前一日 |"""
    tp = now.astimezone(TPE)
    hm = (tp.hour, tp.minute)
    d = tp.strftime("%Y-%m-%d")
    if hm < (8, 30):
        return "2345", (tp - timedelta(days=1)).strftime("%Y-%m-%d")
    if hm < (15, 30):
        return "am", d
    if hm < (18, 30):
        return "1530", d
    if hm < (23, 45):
        return "1830", d
    return "2345", d


def scheduled_for(band: str, date: str) -> str:
    h, m = BAND_TIME[band]
    return f"{date}T{h:02d}:{m:02d}:00+08:00"


def now_iso(now: datetime | None = None) -> str:
    return (now or datetime.now(TPE)).astimezone(TPE).isoformat(timespec="seconds")
