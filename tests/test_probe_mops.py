"""probe_mops：WAF 擋頁不得被誤判成資料（本雲端容器實測三個端點都回擋頁，CSV 端點還是 HTTP 200）。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import probe_mops as pm  # noqa: E402

WAF_HTML = ('<html> <head> <meta http-equiv="Content-Type" content="text/html; charset=utf-8"> </head> <body>  '
            '因為安全性考量，您所執行的頁面無法呈現。<BR> FOR SECURITY REASONS, THIS PAGE CAN NOT BE ACCESSED.<BR>')


class _Resp:
    def __init__(self, status, body: bytes, ctype="text/html; charset=utf-8"):
        self.status_code, self.content, self.headers = status, body, {"content-type": ctype}


def test_waf_page_is_detected():
    assert pm.is_waf_page(WAF_HTML)
    assert not pm.is_waf_page("出表日期,發言日期,發言時間,公司代號\n1150916,1150916,70003,2330\n")


def test_csv_probe_rejects_waf_page_even_with_200(monkeypatch):
    monkeypatch.setattr(pm.requests, "get", lambda *a, **k: _Resp(200, WAF_HTML.encode("utf-8")))
    out = pm.probe_csv("twse_csv", "https://x/L.csv", 5)
    assert out["status"] == 200 and out["waf"] is True and out["ok"] is False and out["rows"] == 0


def test_csv_probe_accepts_real_csv(monkeypatch):
    body = "出表日期,發言日期,發言時間,公司代號,公司名稱,主旨 \n1150916,1150916,70003,2330,台積電,公告\n"
    monkeypatch.setattr(pm.requests, "get", lambda *a, **k: _Resp(200, body.encode("utf-8-sig"), "text/csv"))
    out = pm.probe_csv("twse_csv", "https://x/L.csv", 5)
    assert out["ok"] is True and out["rows"] == 1 and out["encoding"] == "utf-8-sig"
    # TPEx：P0-A 記英文欄名、Actions 實測中文欄名，兩種都收、並回報命中哪一組
    out2 = pm.probe_csv("tpex_csv", "https://x/O.csv", 5)
    assert out2["ok"] is True and out2["header_kind"] == "公司代號"
    body_en = "Date,SecuritiesCompanyCode,CompanyName\n1150916,6488,環球晶\n"
    monkeypatch.setattr(pm.requests, "get", lambda *a, **k: _Resp(200, body_en.encode("utf-8"), "text/csv"))
    out3 = pm.probe_csv("tpex_csv", "https://x/O.csv", 5)
    assert out3["ok"] is True and out3["header_kind"] == "SecuritiesCompanyCode"
    out4 = pm.probe_csv("twse_csv", "https://x/L.csv", 5)
    assert out4["ok"] is False and out4["header_kind"] is None


def test_mopsov_probe_rejects_waf_and_reports_variant(monkeypatch):
    monkeypatch.setattr(pm.requests, "post", lambda *a, **k: _Resp(307, WAF_HTML.encode("utf-8")))
    monkeypatch.setattr(pm.time, "sleep", lambda s: None)
    out = pm.probe_mopsov("sii", pm.datetime(2026, 9, 15, tzinfo=pm.TPE), 5)
    n = len(pm.mopsov_variants("sii", pm.datetime(2026, 9, 15, tzinfo=pm.TPE)))
    assert out["ok"] is False and out["variant_ok"] is None and [a["waf"] for a in out["attempts"]] == [True] * n
    assert all(a["b_date"] == "15" for a in out["attempts"]) and n == 3
    assert [a["endpoint"] for a in out["attempts"]] == ["ajax_t05st01"] * 3
    assert "FOR SECURITY REASONS" in out["attempts"][0]["body_text"]
    # 第二個形狀才回表格：只該試到第二個就停，並回報該形狀名
    calls = []

    def post(url, data=None, **k):
        calls.append(data["b_date"])
        if len(calls) < 2:
            return _Resp(200, "<html>起始日輸入錯誤,請檢查</html>".encode("utf-8"))
        html = "<html><table><tr><td>h</td></tr><tr><td>2330</td></tr><tr><td>2317</td></tr></table></html>"
        return _Resp(200, html.encode("utf-8"))
    monkeypatch.setattr(pm.requests, "post", post)
    out = pm.probe_mopsov("otc", pm.datetime(2026, 9, 15, tzinfo=pm.TPE), 5)
    assert out["ok"] is True and out["variant_ok"] == "ajax_t05st01:form-day-firstin1" and len(out["attempts"]) == 2
    assert out["attempts"][1]["tr"] == 3 and len(out["attempts"][1]["sample_rows"]) == 2


def test_main_exit_code_reflects_overall(monkeypatch, tmp_path):
    monkeypatch.setattr(pm.requests, "get", lambda *a, **k: _Resp(200, WAF_HTML.encode("utf-8")))
    monkeypatch.setattr(pm.requests, "post", lambda *a, **k: _Resp(307, WAF_HTML.encode("utf-8")))
    monkeypatch.setattr(pm.time, "sleep", lambda s: None)
    monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)
    out = tmp_path / "r.json"
    assert pm.main(["--day", "2026-09-15", "--out", str(out)]) == 1
    assert '"ok": false' in out.read_text(encoding="utf-8")


def test_autoform_shell_page_is_followed(monkeypatch):
    shell = ('<html><form name="autoForm" action="ajax_t05st01" method="post"><input type="hidden" name="step" value="2">'
             '<input type="hidden" name="run" value=""><input name="TYPEK" value="sii"/><input type=hidden name=b_date value=1150915></form></html>')
    assert pm.autoform_fields(shell) == ("ajax_t05st01", {"step": "2", "run": "", "TYPEK": "sii", "b_date": "1150915"})
    assert pm.autoform_fields("<html>nothing</html>") == (None, {})
    posts = []

    def post(url, data=None, **k):
        posts.append((url, dict(data)))
        if len(posts) == 1:
            return _Resp(200, shell.encode("utf-8"))
        return _Resp(200, "<table><tr><td>h</td></tr><tr><td>2330</td></tr></table>".encode("utf-8"))
    monkeypatch.setattr(pm.requests, "post", post)
    monkeypatch.setattr(pm.time, "sleep", lambda s: None)
    out = pm.probe_mopsov("sii", pm.datetime(2026, 9, 15, tzinfo=pm.TPE), 5)
    assert out["ok"] is True and out["variant_ok"] == "ajax_t05st01:form-day" and out["attempts"][0]["via"] == "autoform"
    assert posts[1] == ("https://mopsov.twse.com.tw/mops/web/ajax_t05st01", {"step": "2", "run": "", "TYPEK": "sii", "b_date": "1150915"})
