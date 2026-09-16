"""P2 公告收集器（docs/P2-COLLECTOR.md §3 第 2 條 (a)～(g)）：免網路，`http` 注入假回應。"""
from __future__ import annotations

import gzip
import hashlib
import json
import sys
from datetime import datetime
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "src"))

import collect_events as CE  # noqa: E402
from iching import announce as A  # noqa: E402

WAF_HTML = ('<html><body>因為安全性考量，您所執行的頁面無法呈現。<BR> FOR SECURITY REASONS, THIS PAGE CAN NOT BE ACCESSED.<BR>'
            '</body></html>')
ZH_HDR = "出表日期,發言日期,發言時間,公司代號,公司名稱,主旨 ,符合條款,事實發生日,說明\r\n"
EN_HDR = "Date,發言日期,發言時間,SecuritiesCompanyCode,CompanyName,主旨 ,符合條款,事實發生日,說明\r\n"
NOW = datetime(2026, 9, 16, 15, 40, tzinfo=A.TPE)
NOW_ISO = A.now_iso(NOW)


def csv_row(sid="2330", name="台積電", sd="1150916", st="70003", subject="公告本公司董事會決議", clause="第 11 款",
            fact="1150916", body='1.事實發生日:115/09/16\r\n2.公司名稱:台積電\r\n3.說明:"引號"，逗號,也在'):
    return ",".join('"' + x.replace('"', '""') + '"' for x in ("1150916", sd, st, sid, name, subject, clause, fact, body)) + "\r\n"


def mopsov_tr(sid, name, d, t, subj):
    return (f"<tr><td>&nbsp;{sid}</td><td>&nbsp;{name}</td><td>&nbsp;{d}</td><td>&nbsp;{t}</td>"
            f"<td>&nbsp;{subj}</td><td><input type='button' value='詳細資料'></td></tr>")


def mopsov_html(rows):
    hdr = "<tr><th>公司代號</th><th>公司名稱</th><th>發言日期</th><th>發言時間</th><th>主旨</th><th></th></tr>"
    return f"<html><body><table>{hdr}{''.join(mopsov_tr(*r) for r in rows)}</table></body></html>"


class FakeHttp:
    """`(method, url|typek, day)` → (status, body)；缺鍵＝連線例外。"""

    def __init__(self, table: dict):
        self.table, self.calls = table, []

    def __call__(self, method, url, data):
        self.calls.append((method, url, data))
        if method == "POST":
            key = (data["TYPEK"], f"{int(data['year']) + 1911}-{data['month']}-{data['b_date']}")
        else:
            key = next(n for n, (_, u) in A.CSV_URLS.items() if u == url)
        if key not in self.table:
            raise ConnectionError(f"no fixture for {key}")
        status, body = self.table[key]
        return A.HttpResult(status, body.encode("utf-8-sig") if isinstance(body, str) else body,
                            "text/html; charset=UTF-8" if method == "POST" else "text/csv")


def good_http(extra=None):
    t = {"twse_csv": (200, ZH_HDR + csv_row() + csv_row(sid="2317", name="鴻海", st="93015", subject="鴻海主旨")),
         "tpex_csv": (200, ZH_HDR + csv_row(sid="6488", name="環球晶", st="181500", subject="環球晶主旨"))}
    t.update(extra or {})
    return FakeHttp(t)


def am_http(extra=None):
    """am 班會先核對 09-15：補上 mopsov 兩市的 fixture（sii 一列、otc 查無）。"""
    t = {("sii", "2026-09-15"): (200, mopsov_html([("2330", "台積電", "115/09/15", "07:00:03", "x")])),
         ("otc", "2026-09-15"): (200, "<html>資料庫中查無需求資料</html>")}
    t.update(extra or {})
    return good_http(t)


def read_gz(p: Path) -> dict:
    with gzip.open(p, "rb") as g:
        return json.loads(g.read().decode("utf-8"))


def sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


# ---------------------------------------------------------------------------
# (a) 三個陷阱樣本
def test_a_traps_70003_crlf_body_and_english_header():
    rows = A.parse_csv(ZH_HDR + csv_row(), "sii")
    assert len(rows) == 1
    r = rows[0]
    assert r["spoke_time"] == "07:00:03" and r["spoke_date"] == "2026-09-16" and r["fact_date"] == "2026-09-16"
    assert r["stock_id"] == "2330" and r["name"] == "台積電" and r["market"] == "sii" and r["clause"] == "第 11 款"
    assert r["body"] == '1.事實發生日:115/09/16\n2.公司名稱:台積電\n3.說明:"引號"，逗號,也在'   # \r\n 說明走 csv 模組、正規成 \n
    # TPEx 英文欄名（P0-A 修訂表第 13 列）：三個英文欄名命中、其餘退回九欄位置
    rows_en, kind, dropped = A.parse_csv_report("﻿" + EN_HDR + csv_row(sid="6488", name="環球晶", st="181500"), "otc")
    assert kind == "en" and dropped == 0 and rows_en[0]["stock_id"] == "6488" and rows_en[0]["name"] == "環球晶"
    assert rows_en[0]["spoke_time"] == "18:15:00" and rows_en[0]["market"] == "otc"
    assert A.parse_csv_report(ZH_HDR + csv_row(), "sii")[1] == "zh"
    # 其他時間形狀
    assert A.time_to_hms("93015") == "09:30:15" and A.time_to_hms("06:41:17") == "06:41:17" and A.time_to_hms("250000") is None
    assert A.roc_to_iso("115/09/15") == "2026-09-15" and A.roc_to_iso("") is None and A.roc_to_iso("1151340") is None
    # 缺發言時間的列丟棄並計數，不毀整個來源
    _, _, dropped2 = A.parse_csv_report(ZH_HDR + csv_row() + csv_row(sid="9999", st=""), "sii")
    assert dropped2 == 1
    # 非公告表頭 → AnnounceError；擋頁 → WafBlocked
    with pytest.raises(A.AnnounceError):
        A.parse_csv("a,b,c\n1,2,3\n", "sii")
    with pytest.raises(A.WafBlocked):
        A.parse_csv(WAF_HTML, "sii")


def test_parse_mopsov_nbsp_waf_empty():
    html = mopsov_html([("2330", "台積電", "115/09/15", "06:41:17", "公告&nbsp;本公司　董事會  決議")])
    rows = A.parse_mopsov(html, "sii")
    assert rows == [{"market": "sii", "stock_id": "2330", "name": "台積電", "spoke_date": "2026-09-15", "spoke_time": "06:41:17",
                     "subject": "公告 本公司 董事會 決議", "clause": None, "fact_date": None, "body": None}]
    assert A.parse_mopsov("<html><body>資料庫中查無需求資料</body></html>", "otc") == []
    with pytest.raises(A.WafBlocked):
        A.parse_mopsov(WAF_HTML, "sii")


def test_event_id_excludes_subject_and_content_hash_normalizes():
    r1 = A.parse_csv(ZH_HDR + csv_row(subject="A"), "sii")[0]
    r2 = A.parse_csv(ZH_HDR + csv_row(subject="B"), "sii")[0]
    assert A.event_id(r1) == A.event_id(r2) == hashlib.sha1(b"sii|2330|2026-09-16|07:00:03").hexdigest()[:16]
    assert A.row_hash(r1) != A.row_hash(r2)
    assert A.content_hash("公告&nbsp;本公司　x", None, None, None) == A.content_hash("公告 本公司 x", None, None, None)


# ---------------------------------------------------------------------------
# (b) 冪等
def test_b_idempotent_bytes_and_no_second_write(tmp_path):
    http = good_http()
    assert CE.main(["collect", "--band", "1530", "--root", str(tmp_path)], http=http, now=NOW) == 0
    p = tmp_path / "data/events/2026-09-16.json.gz"
    tp = tmp_path / "data/events/_timing.json"
    s1, t1 = sha(p), sha(tp)
    doc = read_gz(p)
    assert len(doc["events"]) == 3 and doc["date"] == "2026-09-16" and doc["schema"] == 1
    # 直接再合併一次：全部落在③，write_if_changed 回 False
    rows = A.parse_csv(http.table["twse_csv"][1], "sii") + A.parse_csv(http.table["tpex_csv"][1], "otc")
    doc2, st = A.merge(A.load_doc(tmp_path, "2026-09-16"), rows, A.now_iso(datetime(2026, 9, 16, 18, 40, tzinfo=A.TPE)), "csv")
    assert st == {"received": 3, "new_events": 0, "new_versions": 0, "filled": 0, "unchanged": 3, "source_added": 0}
    assert A.write_if_changed(p, doc2) is False
    # 整支再跑一次（另一個時鐘）：事件檔與 _timing 位元組相同，留痕記 written=False
    assert CE.main(["collect", "--band", "1830", "--root", str(tmp_path)], http=http, now=datetime(2026, 9, 16, 18, 40, tzinfo=A.TPE)) == 0
    assert sha(p) == s1 and sha(tp) == t1
    rec = json.loads((tmp_path / "runs/collect/2026-09-16-1830.json").read_text(encoding="utf-8"))
    assert rec["files"] == [{"date": "2026-09-16", "rows": 3, "written": False, "new_events": 0, "new_versions": 0, "filled": 0}]
    assert rec["new_events"] == 0 and rec["received"] == 3 and rec["errors"] == []
    # first_seen_at 不回填
    assert doc["events"][A.event_id(rows[0])]["versions"][0]["first_seen_at"] == NOW_ISO


# ---------------------------------------------------------------------------
# (c) 更正版只有主旨 → 新版本
def test_c_subject_only_revision_creates_version():
    full = A.parse_csv(ZH_HDR + csv_row(subject="原主旨"), "sii")
    doc, st = A.merge(None, full, NOW_ISO, "csv")
    assert st["new_events"] == 1
    eid = A.event_id(full[0])
    v1 = doc["events"][eid]["versions"][0]
    assert v1["version_no"] == 1 and v1["version_id"] == f"{eid}#1" and v1["fulltext_missing"] is False and v1["status"] == "active"
    later = "2026-09-17T08:35:00+08:00"
    rev = [{**full[0], "subject": "更正：原主旨", "body": None, "clause": None, "fact_date": None}]
    doc, st = A.merge(doc, rev, later, A.VERIFY_SOURCE)
    vs = doc["events"][eid]["versions"]
    assert st["new_versions"] == 1 and [v["version_no"] for v in vs] == [1, 2]
    assert vs[0]["status"] == "superseded" and vs[1]["status"] == "active"
    assert vs[1]["supersedes"] == f"{eid}#1" and vs[1]["version_id"] == f"{eid}#2"
    assert vs[1]["fulltext_missing"] is True and vs[1]["body"] is None and vs[1]["revised_at"] == later
    assert vs[1]["first_seen_at"] == later and vs[0]["first_seen_at"] == NOW_ISO and vs[0]["revised_at"] == NOW_ISO
    # 只有主旨且主旨相同 → 不開版（②的刻意收窄）
    doc, st = A.merge(doc, [{**rev[0]}], "2026-09-18T08:35:00+08:00", "mopsov-x")
    assert st["new_versions"] == 0 and len(doc["events"][eid]["versions"]) == 2 and "mopsov-x" in vs[1]["sources"]


def test_rule1_fill_same_version_keeps_revised_at():
    subj_only = [{**A.parse_csv(ZH_HDR + csv_row(), "sii")[0], "body": None, "clause": None, "fact_date": None}]
    doc, _ = A.merge(None, subj_only, NOW_ISO, A.VERIFY_SOURCE)
    eid = A.event_id(subj_only[0])
    assert doc["events"][eid]["versions"][0]["fulltext_missing"] is True
    full = A.parse_csv(ZH_HDR + csv_row(), "sii")
    doc, st = A.merge(doc, full, "2026-09-17T09:00:00+08:00", "csv")
    v = doc["events"][eid]["versions"]
    assert st["filled"] == 1 and len(v) == 1 and v[0]["fulltext_missing"] is False and v[0]["body"] == full[0]["body"]
    assert v[0]["clause"] == "第 11 款" and v[0]["fact_date"] == "2026-09-16" and v[0]["revised_at"] == NOW_ISO
    assert v[0]["first_seen_at"] == NOW_ISO and v[0]["version_no"] == 1 and v[0]["version_id"] == f"{eid}#1"   # ①補全不得動這些
    assert v[0]["sources"] == ["csv", A.VERIFY_SOURCE] and v[0]["content_hash"] == A.row_hash(full[0])
    # 有全文但內容（事實發生日）改了 → ② 新版本、帶全文
    changed = [{**full[0], "fact_date": "2026-09-15"}]
    doc, st = A.merge(doc, changed, "2026-09-17T10:00:00+08:00", "csv")
    assert st["new_versions"] == 1 and doc["events"][eid]["versions"][1]["fulltext_missing"] is False


def test_merge_rejects_other_date():
    rows = A.parse_csv(ZH_HDR + csv_row(sd="1150915"), "sii")
    with pytest.raises(A.AnnounceError):
        A.merge(A.new_doc("2026-09-16"), rows, NOW_ISO, "csv")


# ---------------------------------------------------------------------------
# (d) 台北 00:10 執行、發言日是前一日 → 落前一日的檔；留痕檔名用班次排程日
def test_d_cross_midnight_uses_spoke_date(tmp_path):
    http = good_http({"twse_csv": (200, ZH_HDR + csv_row(sd="1150916", st="233010") + csv_row(sid="2317", sd="1150917", st="1500"))})
    now = datetime(2026, 9, 17, 0, 10, tzinfo=A.TPE)
    assert CE.main(["collect", "--band", "auto", "--root", str(tmp_path)], http=http, now=now) == 0
    assert (tmp_path / "data/events/2026-09-16.json.gz").exists() and (tmp_path / "data/events/2026-09-17.json.gz").exists()
    d16, d17 = read_gz(tmp_path / "data/events/2026-09-16.json.gz"), read_gz(tmp_path / "data/events/2026-09-17.json.gz")
    assert {e["spoke_date"] for e in d16["events"].values()} == {"2026-09-16"} and len(d16["events"]) == 2
    assert {e["spoke_date"] for e in d17["events"].values()} == {"2026-09-17"} and len(d17["events"]) == 1
    rec = json.loads((tmp_path / "runs/collect/2026-09-16-2345.json").read_text(encoding="utf-8"))
    assert rec["band"] == "2345" and rec["scheduled_for"] == "2026-09-16T23:45:00+08:00" and rec["started_at"].startswith("2026-09-17T00:10")
    assert not (tmp_path / "runs/collect/2026-09-17-2345.json").exists()


@pytest.mark.parametrize("hm,band,day", [
    ((0, 10), "2345", "2026-09-15"), ((8, 29), "2345", "2026-09-15"), ((8, 30), "am", "2026-09-16"), ((15, 29), "am", "2026-09-16"),
    ((15, 30), "1530", "2026-09-16"), ((17, 50), "1530", "2026-09-16"), ((18, 30), "1830", "2026-09-16"), ((23, 44), "1830", "2026-09-16"),
    ((23, 45), "2345", "2026-09-16"), ((23, 59), "2345", "2026-09-16")])
def test_pick_band_table(hm, band, day):
    assert A.pick_band(datetime(2026, 9, 16, hm[0], hm[1], tzinfo=A.TPE)) == (band, day)


# ---------------------------------------------------------------------------
# (e) WAF：該班留痕 waf、不寫事件、exit 非 0；另一來源照常落地
def test_e_waf_source_fails_but_other_lands(tmp_path):
    http = good_http({"tpex_csv": (200, WAF_HTML)})
    assert CE.main(["collect", "--band", "1530", "--root", str(tmp_path)], http=http, now=NOW) != 0
    rec = json.loads((tmp_path / "runs/collect/2026-09-16-1530.json").read_text(encoding="utf-8"))
    src = {s["name"]: s for s in rec["sources"]}
    assert src["tpex_csv"]["waf"] is True and src["tpex_csv"]["status"] == "waf" and src["tpex_csv"]["rows"] == 0
    assert src["twse_csv"]["waf"] is False and src["twse_csv"]["status"] == "ok" and src["twse_csv"]["header_kind"] == "zh"
    assert rec["errors"] and rec["received"] == 2 and rec["completed_at"]
    doc = read_gz(tmp_path / "data/events/2026-09-16.json.gz")
    assert {e["market"] for e in doc["events"].values()} == {"sii"} and len(doc["events"]) == 2
    # 兩個都擋 → 不寫任何事件檔、留痕仍在
    http2 = FakeHttp({"twse_csv": (200, WAF_HTML), "tpex_csv": (307, WAF_HTML)})
    root2 = tmp_path / "r2"
    assert CE.main(["collect", "--band", "1530", "--root", str(root2)], http=http2, now=NOW) != 0
    assert not (root2 / "data/events").exists() and (root2 / "runs/collect/2026-09-16-1530.json").exists()
    # 連線例外／非 200 也是該來源失敗（不炸整班）
    http3 = FakeHttp({"twse_csv": (500, "oops")})
    root3 = tmp_path / "r3"
    assert CE.main(["collect", "--band", "1530", "--root", str(root3)], http=http3, now=NOW) != 0
    rec3 = json.loads((root3 / "runs/collect/2026-09-16-1530.json").read_text(encoding="utf-8"))
    st = {s["name"]: s for s in rec3["sources"]}
    assert st["twse_csv"]["error"] == "HTTP 500" and st["tpex_csv"]["error"].startswith("ConnectionError")


# ---------------------------------------------------------------------------
# (f) 核對：五種原因碼各一例
def test_f_verify_reason_codes(tmp_path):
    D = "2026-09-16"
    collected = A.parse_csv(ZH_HDR + csv_row(), "sii")                      # 2330 07:00:03 已收
    docs = {D: A.merge(None, collected, NOW_ISO, "csv")[0]}
    mrows = [
        {"market": "sii", "stock_id": "2330", "name": "台積電", "spoke_date": D, "spoke_time": "07:00:03", "subject": "公告本公司董事會決議", "clause": None, "fact_date": None, "body": None},
        {"market": "sii", "stock_id": "2317", "name": "鴻海", "spoke_date": "2026-09-15", "spoke_time": "23:59:01", "subject": "x", "clause": None, "fact_date": None, "body": None},
        {"market": "sii", "stock_id": "2454", "name": "聯發科", "spoke_date": D, "spoke_time": "10:00:00", "subject": "撤銷本公司公告", "clause": None, "fact_date": None, "body": None},
        {"market": "otc", "stock_id": "6488", "name": "環球晶", "spoke_date": D, "spoke_time": "23:50:00", "subject": "晚", "clause": None, "fact_date": None, "body": None},
        {"market": "otc", "stock_id": "3105", "name": "穩懋", "spoke_date": D, "spoke_time": "12:00:00", "subject": "當時未出", "clause": None, "fact_date": None, "body": None},
    ]
    rep = A.verify_day(D, mrows, docs, "2026-09-17T08:40:00+08:00")
    assert rep["mopsov_total"] == 5 and rep["matched"] == 1 and rep["missing_rate"] == 0.8
    assert {m["stock_id"]: m["reason"] for m in rep["missing"]} == {"2317": "cross_day", "2454": "withdrawn", "6488": "late_after_last_band", "3105": "source_missing_at_time"}
    assert rep["reasons"] == {"cross_day": 1, "withdrawn": 1, "late_after_last_band": 1, "waf": 0, "source_missing_at_time": 1}
    # waf 旗標 → 其餘缺漏標 waf（cross_day／withdrawn／late 優先序更前）
    rep_w = A.verify_day(D, mrows, {D: A.merge(None, collected, NOW_ISO, "csv")[0]}, "2026-09-17T08:40:00+08:00", waf=True)
    assert {m["stock_id"]: m["reason"] for m in rep_w["missing"]}["3105"] == "waf"
    # 缺漏補進事件庫：主旨層、fulltext_missing、source mopsov-verify、first_seen_at＝當下；跨日列進它自己那天
    assert "2026-09-15" in docs
    v = docs[D]["events"][A.event_id(mrows[4])]["versions"][0]
    assert v["fulltext_missing"] is True and v["sources"] == [A.VERIFY_SOURCE] and v["first_seen_at"] == "2026-09-17T08:40:00+08:00"
    assert A.event_id(mrows[1]) in docs["2026-09-15"]["events"]
    # 再核對一次：只由核對補回的事件仍算缺漏 → 報告冪等
    rep2 = A.verify_day(D, mrows, docs, "2026-09-18T08:40:00+08:00")
    assert rep2["matched"] == 1 and rep2["reasons"] == rep["reasons"]


def test_verify_cli_and_am_band_range(tmp_path):
    D = "2026-09-16"
    http = good_http({("sii", D): (200, mopsov_html([("2330", "台積電", "115/09/16", "07:00:03", "公告本公司董事會決議"),
                                                     ("2454", "聯發科", "115/09/16", "10:00:00", "新的")])),
                      ("otc", D): (200, "<html>資料庫中查無需求資料</html>")})
    assert CE.main(["collect", "--band", "1530", "--root", str(tmp_path)], http=http, now=NOW) == 0
    assert CE.main(["verify", "--date", D, "--root", str(tmp_path)], http=http, now=datetime(2026, 9, 17, 8, 40, tzinfo=A.TPE)) == 0
    rep = json.loads((tmp_path / f"runs/collect/{D}-verify.json").read_text(encoding="utf-8"))
    assert rep["mopsov_total"] == 2 and rep["matched"] == 1 and rep["missing"][0]["stock_id"] == "2454"
    assert rep["missing"][0]["reason"] == "source_missing_at_time" and rep["sources"][1]["empty"] is True and rep["files_written"] == [D]
    doc = read_gz(tmp_path / f"data/events/{D}.json.gz")
    assert len(doc["events"]) == 4
    # am 班：上次 verify 日＋1 到昨日（曆日）、上限 7；無紀錄只做昨日
    assert CE.verify_range(tmp_path, "2026-09-19") == ["2026-09-17", "2026-09-18"]
    assert CE.verify_range(tmp_path, "2026-09-17") == []
    assert CE.verify_range(tmp_path, "2026-10-01") == [f"2026-09-{d}" for d in range(24, 31)]
    assert CE.verify_range(tmp_path / "nothing", "2026-09-19") == ["2026-09-18"]
    # am 班實跑：先 verify 09-17／09-18（mopsov 缺 fixture＝連線失敗 → 留痕、rc≠0），再 collect
    now_am = datetime(2026, 9, 19, 8, 35, tzinfo=A.TPE)
    rc = CE.main(["collect", "--band", "auto", "--root", str(tmp_path)], http=http, now=now_am)
    assert rc != 0
    for d in ("2026-09-17", "2026-09-18"):
        r = json.loads((tmp_path / f"runs/collect/{d}-verify.json").read_text(encoding="utf-8"))
        assert r["mopsov_total"] is None and r["errors"]
    am_rec = json.loads((tmp_path / "runs/collect/2026-09-19-am.json").read_text(encoding="utf-8"))
    assert am_rec["verify_days"] == ["2026-09-17", "2026-09-18"] and len(am_rec["verify_errors"]) == 4
    assert CE.last_verified_date(tmp_path) == D          # 來源失敗的核對不算「驗過」，兜底班會回頭重驗
    assert CE.verify_range(tmp_path, "2026-09-19") == ["2026-09-17", "2026-09-18"]
    # 這班的 verify 若來源 WAF → 該日核對缺漏標 waf（waf_seen_for 讀留痕）
    http_w = good_http({"twse_csv": (200, WAF_HTML)})
    assert CE.main(["collect", "--band", "2345", "--root", str(tmp_path), "--date", "2026-09-18"], http=http_w, now=now_am) != 0
    assert CE.waf_seen_for(tmp_path, "2026-09-18") is True and CE.waf_seen_for(tmp_path, D) is False


def test_timing_recomputed_from_files(tmp_path):
    http = good_http()
    CE.main(["collect", "--band", "1530", "--root", str(tmp_path)], http=http, now=NOW)
    t = json.loads((tmp_path / "data/events/_timing.json").read_text(encoding="utf-8"))
    d = t["days"]["2026-09-16"]
    assert d["total"] == 3 and d["by_market"] == {"sii": 2, "otc": 1} and d["after_2130"] == 0
    assert d["hour_counts"]["07"] == 1 and d["hour_counts"]["09"] == 1 and d["hour_counts"]["18"] == 1 and len(d["hour_counts"]) == 24
    # 手動放一個 22:00 的事件檔 → 重算納入（非增量）
    doc, _ = A.merge(None, A.parse_csv(ZH_HDR + csv_row(sd="1150915", st="220000"), "sii"), NOW_ISO, "csv")
    A.write_if_changed(tmp_path / "data/events/2026-09-15.json.gz", doc)
    t2, written = A.timing_update(tmp_path)
    assert written and t2["days"]["2026-09-15"]["after_2130"] == 1 and set(t2["days"]) == {"2026-09-15", "2026-09-16"}
    assert A.timing_update(tmp_path)[1] is False


def test_fixture_dir_cli(tmp_path):
    fx = tmp_path / "fx"
    fx.mkdir()
    (fx / "twse_csv.csv").write_text(ZH_HDR + csv_row(), encoding="utf-8-sig")
    (fx / "tpex_csv.csv").write_text(EN_HDR + csv_row(sid="6488", name="環球晶"), encoding="utf-8")
    root = tmp_path / "root"
    assert CE.main(["collect", "--band", "1530", "--root", str(root), "--fixture-dir", str(fx)], now=NOW) == 0
    rec = json.loads((root / "runs/collect/2026-09-16-1530.json").read_text(encoding="utf-8"))
    assert [s["header_kind"] for s in rec["sources"]] == ["zh", "en"] and rec["new_events"] == 2


# ---------------------------------------------------------------------------
# (g) yaml：cron 四條、concurrency、permissions、notify-failure、git add 只有兩個目錄
def test_collect_workflow_yaml():
    d = yaml.safe_load((ROOT / ".github/workflows/collect-events.yml").read_text(encoding="utf-8"))
    on = d.get("on") or d.get(True)
    # 班次改制甲（§5.4）：只剩 08:30 主班＋09:30 兜底，兩條逐字＝腳本 CRON_BANDS 的鍵、都對到 am
    crons = [c["cron"] for c in on["schedule"]]
    assert crons == ["30 0 * * 1-5", "30 1 * * 1-5"] and sorted(CE.CRON_BANDS) == sorted(crons)
    assert set(CE.CRON_BANDS.values()) == {"am"}
    assert set(on["workflow_dispatch"]["inputs"]) == {"band", "date"}
    assert d["concurrency"] == {"group": "iching-commit", "cancel-in-progress": False}
    assert d["permissions"] == {"contents": "write", "issues": "write"}
    job = d["jobs"]["collect"]
    assert job["timeout-minutes"] == 15
    steps = job["steps"]
    assert any(s.get("uses", "").startswith("actions/setup-python") and s["with"]["python-version"] == "3.12" for s in steps)
    run_step = next(s for s in steps if "collect_events.py" in (s.get("run") or ""))
    assert "secrets." not in yaml.safe_dump(d) and "env" in run_step and "--band" in run_step["run"]
    # band 由 github.event.schedule 經 env CRON_EXPR 進腳本；所有 run: 區塊零 ${{
    assert run_step["env"]["CRON_EXPR"] == "${{ github.event.schedule }}"
    assert all("${{" not in (s.get("run") or "") for s in steps)
    commit = next(s for s in steps if "git add" in (s.get("run") or ""))
    # 只 add 兩個目錄，且逐一 add（首次 run 兩來源皆失敗時 data/events 不存在，合併 add 會 128）；data/events 另有 .gitkeep 進 git
    assert "git add runs/collect\n" in commit["run"] and "if [ -d data/events ]; then git add data/events; fi\n" in commit["run"]
    assert commit["run"].count("git add") == 2 and "git add data/events runs/collect" not in commit["run"] and "git add data " not in commit["run"]
    assert (ROOT / "data/events/.gitkeep").exists()
    # step output 只能經 env 進 shell，不得內插進 run:
    assert "steps.collect.outputs" not in commit["run"] and "${{" not in commit["run"]
    assert commit["env"]["RUN_DATE"] == "${{ steps.collect.outputs.run_date }}" and commit["env"]["RUN_BAND"] == "${{ steps.collect.outputs.band }}"
    assert '"$RUN_DATE' in commit["run"] or "${RUN_DATE" in commit["run"]
    assert "pull --rebase" in commit["run"] and "for i in 1 2 3" in commit["run"] and "collect: " in commit["run"]
    fail = next(s for s in steps if "rc" in (s.get("if") or ""))
    assert steps.index(fail) > steps.index(commit)                       # 留痕先 commit，再讓 job 紅
    notify = steps[-1]
    assert notify["uses"] == "./.github/actions/notify-failure" and notify["with"]["pipeline"] == "iching-collect"
    assert notify["if"] == "failure() || cancelled()"


# ---------------------------------------------------------------------------
# 驗收退回（19203f3）補測
@pytest.mark.parametrize("bad", ["2026/09/16", "20260916", "2026-13-01", "16-09-2026", "x; rm -rf", "2026-09-16 "])
def test_bad_date_rejected_before_running(tmp_path, bad):
    """`--date` 會經 step output 進 commit 訊息：不合 YYYY-MM-DD 或不是真日期 → argparse error（rc≠0）、什麼都不寫。"""
    http = good_http()
    for argv in (["collect", "--band", "1530", "--date", bad], ["verify", "--date", bad]):
        with pytest.raises(SystemExit) as e:
            CE.main(argv + ["--root", str(tmp_path)], http=http, now=NOW)
        assert e.value.code != 0
    assert http.calls == [] and not (tmp_path / "runs").exists()
    assert CE.iso_date("2026-09-16") == "2026-09-16"


def test_fill_keeps_first_seen_at_guarded():
    """①同版補全只動內容欄；first_seen_at／revised_at／version_no 一律不動，且 merge 內有守門（改到就拋）。"""
    subj_only = [{**A.parse_csv(ZH_HDR + csv_row(), "sii")[0], "body": None, "clause": None, "fact_date": None}]
    doc, _ = A.merge(None, subj_only, "2026-09-16T15:31:00+08:00", A.VERIFY_SOURCE)
    eid = A.event_id(subj_only[0])
    before = dict(doc["events"][eid]["versions"][0])
    doc, st = A.merge(doc, A.parse_csv(ZH_HDR + csv_row(), "sii"), "2026-09-17T09:00:00+08:00", "csv")
    after = doc["events"][eid]["versions"][0]
    assert st["filled"] == 1
    for k in ("first_seen_at", "revised_at", "version_no", "version_id", "status", "supersedes"):
        assert after[k] == before[k], k
    assert after["first_seen_at"] == "2026-09-16T15:31:00+08:00" and after["fulltext_missing"] is False
    # 守門本身：模擬有人在補全路徑覆寫 first_seen_at → AnnounceError
    doc2, _ = A.merge(None, subj_only, "2026-09-16T15:31:00+08:00", A.VERIFY_SOURCE)
    orig = A.row_hash

    def poisoned(row):
        doc2["events"][eid]["versions"][0]["first_seen_at"] = "2099-01-01T00:00:00+08:00"
        return orig(row)
    A.row_hash = poisoned
    try:
        with pytest.raises(A.AnnounceError, match="first_seen_at"):
            A.merge(doc2, A.parse_csv(ZH_HDR + csv_row(), "sii"), "2026-09-17T09:00:00+08:00", "csv")
    finally:
        A.row_hash = orig


def test_short_row_dropped_not_fulltext_missing():
    """欄數少於表頭的壞列（如少了「說明」欄）→ 丟棄並計入 dropped，不得混進事件庫變成 fulltext_missing。"""
    short = '"1150916","1150916","70003","2330","台積電","主旨","第11款","1150916"\r\n'      # 8 欄
    rows, kind, dropped = A.parse_csv_report(ZH_HDR + csv_row(sid="2317") + short, "sii")
    assert kind == "zh" and dropped == 1 and [r["stock_id"] for r in rows] == ["2317"]
    assert all(r["body"] is not None for r in rows)
    # 表頭若比九欄短（英文舊表頭只有三欄映射到位置）：ncol 依表頭而定
    rows2, _, dropped2 = A.parse_csv_report(EN_HDR + csv_row(sid="6488"), "otc")
    assert dropped2 == 0 and rows2[0]["body"] is not None


def test_mopsov_nested_tr_and_empty_marker_semantics():
    inner = mopsov_tr("2330", "台積電", "115/09/15", "06:41:17", "第一列") + mopsov_tr("2317", "鴻海", "115/09/15", "07:00:00", "第二列")
    # (a) 外層 wrapper 列 <tr><td>標題</td><td><table><tr>…：`<tr>.*?</tr>` 會從外層 <tr> 吃到內層第一個 </tr>，
    #     格序被 wrapper 的前導格推歪 → 第一列遺失；兩種 wrapper 形狀（有／無前導格）都不得漏
    for wrap in ("<tr><td>標題</td><td><table>{}</table></td></tr>", "<tr><td><table>{}</table></td></tr>",
                 "<tr class='x'>\n<td colspan='2'>公告列表</td>\n<td><table>{}</table></td></tr>"):
        nested = f"<html><body><table>{wrap.format(inner)}</table></body></html>"
        assert [r["stock_id"] for r in A.parse_mopsov(nested, "sii")] == ["2330", "2317"], wrap
    flat = mopsov_html([("2330", "台積電", "115/09/15", "06:41:17", "第一列"), ("2317", "鴻海", "115/09/15", "07:00:00", "第二列")])
    assert A.parse_mopsov(nested, "sii") == A.parse_mopsov(flat, "sii")
    # (b) 主旨含「資料庫中查無需求資料」字樣 → 照列回，不是整頁空
    html = mopsov_html([("2330", "台積電", "115/09/15", "06:41:17", "更正：資料庫中查無需求資料一案說明")])
    rows = A.parse_mopsov(html, "sii")
    assert len(rows) == 1 and "查無" in rows[0]["subject"]
    # 無列且含「查無」→ []；無列也無「查無」（表單錯誤頁等）→ AnnounceError，fetch 層記 status=error
    assert A.parse_mopsov("<html><body><table><tr><td>資料庫中查無需求資料</td></tr></table></body></html>", "otc") == []
    with pytest.raises(A.AnnounceError):
        A.parse_mopsov("<html><body>起始日輸入錯誤,請檢查</body></html>", "sii")
    http = FakeHttp({("sii", "2026-09-15"): (200, "<html><body>起始日輸入錯誤,請檢查</body></html>")})
    rows, rec = A.fetch_mopsov(http, "sii", "2026-09-15")
    assert rows == [] and rec["status"] == "error" and "AnnounceError" in rec["error"]


def test_html_entity_in_csv_subject_matches_decoded_mopsov_subject():
    """2026-09-16 線上首班實例：6239 的主旨 mopsov 給「⾦」（U+2FA6），CSV 給字面 `&#12198;`（同一字的 HTML 實體）。
    修前被當成更正開了 v2；修後 `norm_text` unescape → 同版補全、不開版；儲存字串不含實體、全形標點保留。"""
    from iching import announce as A
    m = "公告本公司買回股份之⾦額達新台幣三億元以上（更正）"
    c = "公告本公司買回股份之&#12198;額達新台幣三億元以上（更正）"
    assert A.norm_text(c) == m and A.norm_text("S&P 500 &amp; x") == "S&P 500 & x"
    assert A.content_hash(m, None, None, None) == A.content_hash(c, None, None, None)
    base = {"market": "sii", "stock_id": "6239", "name": "力成", "spoke_date": "2026-09-15", "spoke_time": "18:08:26",
            "clause": None, "fact_date": None}
    doc = A.new_doc("2026-09-15")
    A.merge(doc, [{**base, "subject": m, "body": None}], "2026-09-16T10:00:00+08:00", "mopsov-verify")
    A.merge(doc, [{**base, "subject": c, "body": "1.說明&amp;全文", "clause": "第4款", "fact_date": "2026-09-15"}],
            "2026-09-16T10:57:00+08:00", "csv")
    (ev,) = doc["events"].values()
    assert [v["version_no"] for v in ev["versions"]] == [1]
    v = ev["versions"][0]
    assert v["fulltext_missing"] is False and v["body"] == "1.說明&全文" and v["first_seen_at"] == "2026-09-16T10:00:00+08:00"
    assert sorted(v["sources"]) == ["csv", "mopsov-verify"]


def test_merge_compares_recomputed_hash_not_stored_string():
    """正規化算式調整後，既有檔內的 content_hash 是舊算式——比對必須重算儲存欄位，否則同一列重餵會開假版本
    （2026-09-16 覆驗實測 440f750：09-15 檔 162 則餵回自己 → 144 個假版本）。模擬：把儲存的 hash 改成任意舊值，
    同列再餵必須 unchanged；儲存主旨帶字面實體（真檔 2546／2438 的形狀）、新列已解碼，也必須 unchanged。"""
    from iching import announce as A
    base = {"market": "otc", "stock_id": "2546", "name": "x", "spoke_date": "2026-09-15", "spoke_time": "09:00:00",
            "clause": "第4款", "fact_date": "2026-09-15", "subject": "公告&#29670;事", "body": "說明（全形）"}
    doc = A.new_doc("2026-09-15")
    A.merge(doc, [base], "2026-09-16T10:00:00+08:00", "csv")
    (ev,) = doc["events"].values()
    ev["versions"][0]["content_hash"] = "0000deadbeef00000000deadbeef00000000deadbeef"   # 舊算式殘留
    ev["versions"][0]["subject"] = "公告&#29670;事"                                     # 舊版存了字面實體
    decoded = {**base, "subject": "公告珦事"}   # &#29670; ＝ 珦
    _, stats = A.merge(doc, [decoded], "2026-09-16T15:30:00+08:00", "csv")
    assert stats["new_versions"] == 0 and stats["new_events"] == 0 and len(ev["versions"]) == 1
    # 真的有變（body 不同）仍要開版——比對不是被關掉
    _, stats2 = A.merge(doc, [{**decoded, "body": "更正後說明"}], "2026-09-16T18:30:00+08:00", "csv")
    assert stats2["new_versions"] == 1 and len(ev["versions"]) == 2


# ---------------------------------------------------------------------------
# 班次改制甲（§5.4）：band 由 CRON_EXPR 查表；留痕撞名不覆蓋
@pytest.mark.parametrize("cron", ["30 0 * * 1-5", "30 1 * * 1-5"])
def test_cron_expr_maps_to_am_not_clock(tmp_path, monkeypatch, cron):
    """cron 觸發：band 由 CRON_EXPR 對照表決定，**不看時鐘**——即使時鐘落在 15:30～18:29（時鐘表會判 1530）也是 am。"""
    monkeypatch.setenv("CRON_EXPR", cron)
    now = datetime(2026, 9, 16, 17, 50, tzinfo=A.TPE)          # 延遲 5 小時後的時刻
    assert A.pick_band(now)[0] == "1530"                          # 時鐘表會判錯，證明查表生效
    assert CE.main(["collect", "--band", "auto", "--root", str(tmp_path)], http=am_http(), now=now) == 0
    assert (tmp_path / "runs/collect/2026-09-16-am.json").exists() and not (tmp_path / "runs/collect/2026-09-16-1530.json").exists()
    assert CE.resolve_band("auto", now, cron) == ("am", "2026-09-16")


def test_unknown_cron_expr_is_error_not_guess(tmp_path, monkeypatch):
    monkeypatch.setenv("CRON_EXPR", "30 7 * * 1-5")             # 舊的 15:30 班：已從對照表拿掉
    http = good_http()
    assert CE.main(["collect", "--band", "auto", "--root", str(tmp_path)], http=http, now=NOW) != 0
    assert http.calls == [] and not (tmp_path / "runs").exists()   # 沒猜、沒抓、沒留痕
    assert CE.resolve_band("auto", NOW, "30 7 * * 1-5") is None
    # 明給 band 時 CRON_EXPR 不管；沒有 CRON_EXPR（dispatch）走時鐘
    assert CE.resolve_band("am", NOW, "garbage") == ("am", "2026-09-16")
    monkeypatch.delenv("CRON_EXPR")
    assert CE.resolve_band("auto", NOW, None) == A.pick_band(NOW)
    assert CE.main(["collect", "--band", "auto", "--root", str(tmp_path)], http=http, now=NOW) == 0
    assert (tmp_path / "runs/collect/2026-09-16-1530.json").exists()


def test_run_record_collision_seq_not_overwrite(tmp_path):
    """同日同 band 跑兩次：內容不同 → `-2` 並存、第一檔位元組不變；內容相同（時戳除外）→ 不寫第二檔。"""
    D = "2026-09-16"
    base = tmp_path / f"runs/collect/{D}-am.json"
    # 第一次：兩來源正常
    assert CE.main(["collect", "--band", "am", "--root", str(tmp_path)], http=am_http(), now=NOW) == 0
    b1 = base.read_bytes()
    # 第二次：tpex 回擋頁 → 內容不同 → -2，第一檔不動
    now2 = datetime(2026, 9, 16, 9, 40, tzinfo=A.TPE)
    assert CE.main(["collect", "--band", "am", "--root", str(tmp_path)], http=am_http({"tpex_csv": (200, WAF_HTML)}), now=now2) != 0
    seq2 = tmp_path / f"runs/collect/{D}-am-2.json"
    assert base.read_bytes() == b1 and seq2.exists()
    r2 = json.loads(seq2.read_text(encoding="utf-8"))
    assert r2["errors"] and r2["started_at"].startswith("2026-09-16T09:40") and "record" not in r2   # record 只在回傳值、不進檔
    assert [p.name for p in CE.run_records(tmp_path, D, "am")] == [f"{D}-am.json", f"{D}-am-2.json"]
    # 第三次：與第二次同樣的失敗（只差時戳）→ 內容相同 → 不寫 -3
    now3 = datetime(2026, 9, 16, 9, 50, tzinfo=A.TPE)
    assert CE.main(["collect", "--band", "am", "--root", str(tmp_path)], http=am_http({"tpex_csv": (200, WAF_HTML)}), now=now3) != 0
    assert not (tmp_path / f"runs/collect/{D}-am-3.json").exists() and base.read_bytes() == b1 and seq2.read_text(encoding="utf-8") == json.dumps(r2, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
    # 第四次：來源恢復、但事件已在 → 無寫入無錯誤；最近一份留痕（-2）有錯誤 → 仍要寫 -3 讓恢復看得見
    assert CE.main(["collect", "--band", "am", "--root", str(tmp_path)], http=am_http(), now=datetime(2026, 9, 16, 10, 0, tzinfo=A.TPE)) == 0
    seq3 = tmp_path / f"runs/collect/{D}-am-3.json"
    assert seq3.exists() and json.loads(seq3.read_text(encoding="utf-8"))["errors"] == []
    # 第五次：再一次無寫入無錯誤、最近一份也無錯誤 → 冪等命中，不另寫（兜底班的常態）
    rec, rc = CE.do_collect(tmp_path, "am", D, am_http(), datetime(2026, 9, 16, 10, 10, tzinfo=A.TPE))
    assert rc == 0 and rec["record"] is None and not (tmp_path / f"runs/collect/{D}-am-4.json").exists()
    assert CE.waf_seen_for(tmp_path, "2026-09-15") is True      # 序號檔裡的 waf 也看得到（am 班是隔日）


def test_backup_band_idempotent_no_second_record(tmp_path, monkeypatch):
    """08:30 主班成功後 09:30 兜底同 band：事件檔位元組不變、留痕不另寫、data/ 與 runs/ 沒有任何新檔＝沒東西可 commit。"""
    http = am_http()
    monkeypatch.setenv("CRON_EXPR", "30 0 * * 1-5")
    assert CE.main(["collect", "--band", "auto", "--root", str(tmp_path)], http=http, now=datetime(2026, 9, 16, 13, 5, tzinfo=A.TPE)) == 0
    snap = {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    assert {p.name for p in snap} == {"2026-09-16.json.gz", "2026-09-15.json.gz", "_timing.json", "2026-09-16-am.json", "2026-09-15-verify.json"}
    monkeypatch.setenv("CRON_EXPR", "30 1 * * 1-5")
    assert CE.main(["collect", "--band", "auto", "--root", str(tmp_path)], http=http, now=datetime(2026, 9, 16, 14, 20, tzinfo=A.TPE)) == 0
    assert {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()} == snap
