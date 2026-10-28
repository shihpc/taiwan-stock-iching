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
