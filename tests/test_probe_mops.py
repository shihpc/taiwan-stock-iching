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


def test_mopsov_probe_rejects_waf_and_reports_method(monkeypatch):
    monkeypatch.setattr(pm.requests, "post", lambda *a, **k: _Resp(307, WAF_HTML.encode("utf-8")))
    monkeypatch.setattr(pm.requests, "get", lambda *a, **k: _Resp(307, WAF_HTML.encode("utf-8")))
    monkeypatch.setattr(pm.time, "sleep", lambda s: None)
    out = pm.probe_mopsov("sii", pm.datetime(2026, 9, 15, tzinfo=pm.TPE), 5)
    assert out["ok"] is False and out["method_ok"] is None and [a["waf"] for a in out["attempts"]] == [True, True]
    assert out["day"] == "115/09/15"
    assert "FOR SECURITY REASONS" in out["attempts"][0]["body_text"]
    html = "<html><table><tr><td>h</td></tr><tr><td>2330</td></tr><tr><td>2317</td></tr></table></html>"
    monkeypatch.setattr(pm.requests, "post", lambda *a, **k: _Resp(200, html.encode("utf-8")))
    out = pm.probe_mopsov("otc", pm.datetime(2026, 9, 15, tzinfo=pm.TPE), 5)
    assert out["ok"] is True and out["method_ok"] == "POST" and out["attempts"][0]["tr"] == 3


def test_main_exit_code_reflects_overall(monkeypatch, tmp_path):
    monkeypatch.setattr(pm.requests, "get", lambda *a, **k: _Resp(200, WAF_HTML.encode("utf-8")))
    monkeypatch.setattr(pm.requests, "post", lambda *a, **k: _Resp(307, WAF_HTML.encode("utf-8")))
    monkeypatch.setattr(pm.time, "sleep", lambda s: None)
    monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)
    out = tmp_path / "r.json"
    assert pm.main(["--day", "2026-09-15", "--out", str(out)]) == 1
    assert '"ok": false' in out.read_text(encoding="utf-8")
