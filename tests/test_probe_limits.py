"""`scripts/probe_limits.py`（P4-E2 來源探測）純函式與 workflow 結構；免網路。"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("probe_limits", ROOT / "scripts" / "probe_limits.py")
PL = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(PL)


def test_rows_of_shapes():
    assert PL.rows_of([{"Date": "1151027", "Code": "1"}]) == ([{"Date": "1151027", "Code": "1"}], ["Date", "Code"])
    assert PL.rows_of({"fields": ["日期", "代號"], "data": [["115.10.27", "1"]]}) == ([["115.10.27", "1"]], ["日期", "代號"])
    rows, f = PL.rows_of({"tables": [{"fields": ["a"], "data": [[1], [2]]}, {"fields": ["b"], "data": [[3]]}]})
    assert rows == [[1], [2], [3]] and f == ["a"]
    assert PL.rows_of({"stat": "ok"}) == ([], []) and PL.rows_of(None) == ([], [])


def test_sample_dates_picks_date_like_fields():
    assert PL.sample_dates(["115.10.27", "2330", "115/10/28～115/11/03"], ["日期", "代號", "處置期間"]) == \
        {"日期": "115.10.27", "處置期間": "115/10/28～115/11/03"}
    assert PL.sample_dates({"Date": "1151027", "Code": "2330"}, []) == {"Date": "1151027"}


def test_annotation_escapes_and_levels():
    line = PL.annotation({"name": "x,y:z", "state": "waf", "url": "u", "http": 307, "n": 0})
    assert line.startswith("::warning title=limits x%2Cy%3Az::")
    assert json.loads(line.split("::", 2)[2])["http"] == 307 and "url" not in json.loads(line.split("::", 2)[2])
    assert PL.annotation({"name": "a", "state": "permission"}).startswith("::notice ")


def test_last_trading_days(tmp_path):
    p = tmp_path / "cal.json"
    p.write_text(json.dumps({"dates": ["2026-10-21", "2026-10-22", "2026-10-27"]}), encoding="utf-8")
    assert PL.last_trading_days(p, None) == ("2026-10-27", "2026-10-22")
    assert PL.last_trading_days(p, "2026-10-22") == ("2026-10-22", "2026-10-21")


def test_finmind_skipped_without_token(monkeypatch):
    monkeypatch.delenv("FINMIND_TOKEN", raising=False)
    assert PL.probe_finmind("2026-10-27") == [{"name": "finmind", "state": "skipped", "note": "無 FINMIND_TOKEN"}]


def test_workflow_shape():
    wf = yaml.safe_load((ROOT / ".github/workflows/probe-limits.yml").read_text(encoding="utf-8"))
    on = wf.get("on", wf.get(True))
    assert set(on) == {"workflow_dispatch"} and wf["permissions"] == {"contents": "read"}
    run = wf["jobs"]["probe"]["steps"][-1]
    assert "${{" not in run["run"] and run["env"]["PROBE_DATE"] == "${{ inputs.date }}"
    assert run["env"]["FINMIND_TOKEN"] == "${{ secrets.FINMIND_TOKEN }}"


# ---- 驗收補測（probe_one／main／probe_finmind；mock、免網路）----
import requests  # noqa: E402


class _Resp:
    def __init__(self, status=200, body=None, text=None, headers=None):
        self.status_code = status
        self._body = body
        self.text = text if text is not None else (json.dumps(body, ensure_ascii=False) if body is not None else "")
        self.content = self.text.encode("utf-8")
        self.headers = headers or {}

    def json(self):
        if self._body is None:
            raise ValueError("no json")
        return self._body


class _Sess:
    def __init__(self, seq):
        self.seq, self.calls = list(seq), []

    def get(self, url, **kw):
        self.calls.append(kw)
        x = self.seq.pop(0)
        if isinstance(x, Exception):
            raise x
        return x


def test_probe_one_waf_redirect_visible():
    s = _Sess([_Resp(307, None, "<html>FOR SECURITY REASONS, THIS PAGE CAN NOT BE ACCESSED</html>", {"Location": "/x"})])
    r = PL.probe_one(s, "t", "https://www.twse.com.tw/rwd/zh/x", {"a": "1"})
    assert r["state"] == "waf" and r["http"] == 307 and r["location"] == "/x" and s.calls[0]["allow_redirects"] is False


def test_probe_one_tpex_ssl_downgrade_and_twse_no_downgrade():
    s = _Sess([requests.exceptions.SSLError("bad cert"), _Resp(200, [{"Date": "1151028", "Code": "1"}])])
    r = PL.probe_one(s, "t", "https://www.tpex.org.tw/openapi/v1/x", None)
    assert r["state"] == "ok" and r["tls_insecure"] is True and [c["verify"] for c in s.calls] == [True, False]
    s2 = _Sess([requests.exceptions.SSLError("bad cert")])
    r2 = PL.probe_one(s2, "t", "https://openapi.twse.com.tw/v1/x", None)
    assert r2["state"] == "network_error" and len(s2.calls) == 1


def test_probe_one_blank_placeholder_is_empty():
    s = _Sess([_Resp(200, [{"Date": "", "Code": "", "Number": "0"}])])
    assert PL.probe_one(s, "t", "https://openapi.twse.com.tw/v1/announcement/notice", None)["state"] == "empty"


def test_finmind_permission_and_token_never_in_output(monkeypatch, capsys):
    tok = "test-secret-XYZ-123"
    monkeypatch.setenv("FINMIND_TOKEN", tok)
    from iching import fm

    calls = {"n": 0}

    def fake_get(self, dataset, **kw):
        calls["n"] += 1
        if calls["n"] == 1:
            raise fm.PermissionRequired(f"{dataset}: HTTP 400 msg=Your level is free")
        if calls["n"] == 2:
            raise RuntimeError(f"echo Bearer {tok} https://x/?token={tok}")
        return [{"stock_id": "2330", "date": "2026-10-27", "note": tok}]
    monkeypatch.setattr(fm.FinMind, "get", fake_get)
    out = PL.probe_finmind("2026-10-27")
    assert [r["state"] for r in out] == ["permission", "error", "ok"]
    lines = [PL.annotation(r) for r in out]
    assert all(tok not in ln for ln in lines), lines


def test_main_exit_code_ignores_finmind(monkeypatch, tmp_path):
    monkeypatch.setattr(PL, "probes", lambda T, P: [("a", "https://openapi.twse.com.tw/v1/a", None)])
    monkeypatch.setattr(PL, "INTERVAL", 0)
    monkeypatch.setattr(PL, "probe_finmind", lambda T: [{"name": "finmind_x", "state": "permission"}])
    summ = tmp_path / "s.md"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summ))
    monkeypatch.setattr(PL, "probe_one", lambda s, n, u, p: {"name": n, "state": "ok", "http": 200, "n": 3})
    assert PL.main([]) == 0 and "| a | ok | 200 |" in summ.read_text(encoding="utf-8")
    monkeypatch.setattr(PL, "probe_one", lambda s, n, u, p: {"name": n, "state": "waf", "http": 307})
    assert PL.main([]) == 1
