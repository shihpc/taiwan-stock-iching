"""P4-S `scripts/build_status.py`（docs/P4-PREVIEW.md §15）：合成摘要＋合成產物，逐情境斷言 status／degraded／note；決定性與「內容不變不重寫」。"""
from __future__ import annotations

import importlib.util
import json
import os
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("build_status", ROOT / "scripts" / "build_status.py")
BS = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(BS)

D = "2026-10-16"
NOW = "2026-10-16T23:40:00+08:00"
GEN_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\+08:00$")
CLEAN_SG = {"n_bundles": 480, "us_span": ["2024-01-02", D], "us_n": 400, "us_gaps": [], "us_extra": 0, "us_empty": False,
            "fx_span": ["2024-01-02", D], "fx_n": 400, "fx_gaps": [], "fx_extra": 3, "fx_empty": False}
COUNTS = {"short_sale": 1300, "short_sale_covered": 1900, "stocks_in_pool": 1942, "pool": 1942}


def ok_summary(**kw):
    s = {"data_version": "fm-x", "last_date": "2026-10-15", "upto": D, "pending": [D], "remaining": [], "status": "ok",
         "done": [{"date": D, "rows": 5826, "counts": COUNTS, "official_errors": [], "warnings": []}],
         "series_gaps": dict(CLEAN_SG), "n_calls": 40}
    s.update(kw)
    return s


@pytest.fixture
def root(tmp_path, monkeypatch):
    monkeypatch.delenv("WEB_OK", raising=False)
    monkeypatch.delenv("IND_OK", raising=False)
    r = tmp_path / "repo"
    (r / "data" / "web" / "ind").mkdir(parents=True)
    (r / "data" / "scores").mkdir(parents=True)
    (r / "data" / "web" / "latest.json").write_text(json.dumps({"schema": 1, "date": D, "stocks": {}}), encoding="utf-8")
    (r / "data" / "web" / "ind" / "market.json").write_text(json.dumps({"schema": 1, "date": D}), encoding="utf-8")
    (r / "data" / "scores" / "2026-10-15.json").write_text(json.dumps({"tpe_date": "2026-10-15", "diag": {"index_missing": []}}), encoding="utf-8")
    (r / "data" / "scores" / f"{D}.json").write_text(json.dumps({"tpe_date": D, "diag": {"index_missing": []}}), encoding="utf-8")
    return r


def run(root: Path, summary: dict | None, now: str = NOW, raw: str | None = None) -> dict:
    sp = root.parent / "summary.json"
    if summary is not None:
        sp.write_text(json.dumps(summary, ensure_ascii=False), encoding="utf-8")
    elif raw is not None:
        sp.write_text(raw, encoding="utf-8")
    elif sp.exists():
        sp.unlink()
    assert BS.main(["--root", str(root), "--summary", str(sp), "--now", now]) == 0
    out = root / "data" / "web" / "status.json"
    text = out.read_text(encoding="utf-8")
    assert len(text.encode("utf-8")) < 4096 and text.endswith("\n")
    j = json.loads(text)
    assert j["schema"] == 1 and GEN_RE.match(j["generated_at"]) and j["status"] in ("ok", "waiting", "degraded")   # S2
    assert text == BS.dumps(j)
    return j


def test_ok(root):
    j = run(root, ok_summary())
    assert j["status"] == "ok" and j["degraded"] == [] and j["date"] == D and j["scores_date"] == D
    assert j["run"] == {"result": "ok", "upto": D, "last_date": D, "remaining": [], "n_remaining": 0}
    assert j["checks"]["series_gaps"] == {"us": {"gaps": 0, "empty": False}, "fx": {"gaps": 0, "empty": False}, "error": None}
    assert j["checks"]["short_sale"] == {"covered": 1900, "stocks": 1942} and j["waiting"] is None
    assert j["products"] == {"web": True, "ind": True} and j["note"] == f"正常：資料日 {D}。"


def test_waiting_short_sale_partial(root):
    s = {"data_version": "fm-x", "last_date": D, "upto": "2026-10-19", "pending": ["2026-10-19"], "remaining": [], "done": [],
         "status": "waiting", "waiting_date": "2026-10-19", "missing": ["short_sale", "vix"],
         "warnings": ["short_sale:partial(1000/1942)"], "counts": {"short_sale": 1301, "short_sale_covered": 1000, "stocks_in_pool": 1942}}
    j = run(root, s)
    assert j["status"] == "waiting" and j["waiting"] == {"date": "2026-10-19", "missing": ["short_sale", "vix"]}
    assert j["checks"]["short_sale"] == {"covered": 1000, "stocks": 1942} and j["checks"]["series_gaps"] is None
    assert j["degraded"] == [] and j["run"]["last_date"] == D
    assert "等待資料發布" in j["note"] and "2026-10-19" in j["note"] and "借券賣出餘額" in j["note"] and f"維持 {D}" in j["note"]


def test_noop_same_date_reuses_checks(root):
    run(root, ok_summary(series_gaps={**CLEAN_SG, "us_gaps": ["2026-10-01"]}))
    j = run(root, {"data_version": "fm-x", "last_date": D, "upto": "2026-10-17", "pending": [], "done": [], "status": "noop"})
    assert j["run"]["result"] == "noop" and j["checks"]["series_gaps"]["us"]["gaps"] == 1
    assert j["checks"]["short_sale"] == {"covered": 1900, "stocks": 1942}
    assert j["status"] == "degraded" and j["degraded"] == ["series_gap"]


def test_noop_different_date_does_not_reuse(root):
    run(root, ok_summary(series_gaps={**CLEAN_SG, "us_gaps": ["2026-10-01"]}))
    (root / "data" / "web" / "latest.json").write_text(json.dumps({"date": "2026-10-19"}), encoding="utf-8")
    (root / "data" / "scores" / "2026-10-19.json").write_text(json.dumps({"diag": {"index_missing": []}}), encoding="utf-8")
    (root / "data" / "web" / "ind" / "market.json").write_text(json.dumps({"date": "2026-10-19"}), encoding="utf-8")
    j = run(root, {"last_date": "2026-10-19", "upto": "2026-10-20", "done": [], "status": "noop"})
    assert j["checks"]["series_gaps"] is None and j["checks"]["short_sale"] is None
    assert j["status"] == "ok" and j["degraded"] == []


def test_remaining_catching_up(root):
    rem = [f"2026-10-{d:02d}" for d in range(1, 15)]
    j = run(root, ok_summary(remaining=rem))
    assert j["status"] == "degraded" and j["degraded"] == ["catching_up"]
    assert j["run"]["n_remaining"] == 14 and j["run"]["remaining"] == rem[:10]
    assert "尚有 14 個交易日待補" in j["note"]


def test_series_gap_and_empty(root):
    j = run(root, ok_summary(series_gaps={**CLEAN_SG, "fx_gaps": ["2026-10-02", "2026-10-03"]}))
    assert j["degraded"] == ["series_gap"] and j["checks"]["series_gaps"]["fx"] == {"gaps": 2, "empty": False}
    j = run(root, ok_summary(series_gaps={**CLEAN_SG, "us_empty": True}))
    assert j["degraded"] == ["series_gap"] and j["checks"]["series_gaps"]["us"]["empty"] is True
    assert "美股／匯率序列有缺日" in j["note"]


def test_series_gaps_error(root):
    j = run(root, ok_summary(series_gaps={"error": "OSError: boom"}))
    assert j["status"] == "degraded" and j["degraded"] == ["series_check_error"]
    assert j["checks"]["series_gaps"] == {"us": None, "fx": None, "error": "OSError: boom"}
    assert "檢查失敗" in j["note"]


def test_web_ok_0(root, monkeypatch):
    monkeypatch.setenv("WEB_OK", "0")
    j = run(root, ok_summary())
    assert j["products"]["web"] is False and j["status"] == "degraded" and j["degraded"] == ["web_stale"]
    assert "網頁資料未更新" in j["note"]
    # web 失敗被還原成前一版（date 落後分數檔）：同一代碼、note 帶兩個日期
    (root / "data" / "web" / "latest.json").write_text(json.dumps({"date": "2026-10-15"}), encoding="utf-8")
    (root / "data" / "web" / "ind" / "market.json").write_text(json.dumps({"date": "2026-10-15"}), encoding="utf-8")
    j = run(root, ok_summary())
    assert j["date"] == "2026-10-15" and j["degraded"] == ["web_stale"] and "停在 2026-10-15（分數檔已到 2026-10-16）" in j["note"]


def test_ind_ok_0(root, monkeypatch):
    monkeypatch.setenv("IND_OK", "0")
    j = run(root, ok_summary())
    assert j["products"]["ind"] is False and j["degraded"] == ["ind_stale"] and "本爻依據未更新" in j["note"]
    monkeypatch.setenv("IND_OK", "1")
    (root / "data" / "web" / "ind" / "market.json").write_text(json.dumps({"date": "2026-10-15"}), encoding="utf-8")
    assert run(root, ok_summary())["degraded"] == ["ind_stale"]


def test_summary_missing_and_bad(root):
    j = run(root, None)
    assert j["status"] == "degraded" and j["run"] is None and j["degraded"] == ["summary_missing"]
    assert "摘要讀不到" in j["note"]
    for raw in ("{not json", json.dumps({"status": "weird"}), json.dumps([1, 2])):
        j = run(root, None, raw=raw)
        assert j["run"] is None and j["degraded"] == ["summary_missing"]


def test_latest_unreadable(root):
    (root / "data" / "web" / "latest.json").unlink()
    j = run(root, ok_summary())
    assert j["date"] is None and j["status"] == "degraded" and "web_unreadable" in j["degraded"]
    (root / "data" / "web" / "latest.json").write_text("{broken", encoding="utf-8")
    assert run(root, ok_summary())["date"] is None


def test_index_missing(root):
    (root / "data" / "scores" / f"{D}.json").write_text(json.dumps({"diag": {"index_missing": ["tpex"]}}), encoding="utf-8")
    j = run(root, ok_summary())
    assert j["checks"]["index_missing"] == ["tpex"] and j["degraded"] == ["index_missing"] and j["status"] == "degraded"
    assert "大盤指數缺值" in j["note"]


def test_official_errors_kept_but_not_degraded(root):
    errs = [["twse", D, "parse error " + "x" * 300]] * 7
    j = run(root, ok_summary(done=[{"date": D, "counts": COUNTS, "official_errors": errs}]))
    assert len(j["checks"]["official_errors"]) == 5 and all(len(x) <= 120 for e in j["checks"]["official_errors"] for x in e)
    assert j["status"] == "ok"


def test_waiting_beats_degraded(root, monkeypatch):
    monkeypatch.setenv("WEB_OK", "0")
    j = run(root, {"last_date": D, "upto": "2026-10-19", "done": [], "status": "waiting", "waiting_date": "2026-10-19",
                   "missing": ["vix"], "remaining": []})
    assert j["status"] == "waiting" and j["degraded"] == ["web_stale"]


def test_deterministic_and_no_rewrite(root):
    """S4：固定 --now 兩次位元組相同；只改 now 時不重寫（bytes 與 mtime 不變）；內容變才重寫（generated_at 跟著變）。"""
    out = root / "data" / "web" / "status.json"
    run(root, ok_summary())
    b1 = out.read_bytes()
    out.unlink()
    run(root, ok_summary())
    assert out.read_bytes() == b1
    os.utime(out, ns=(1_000_000_000, 1_000_000_000))
    j = run(root, ok_summary(), now="2026-10-17T09:00:00+08:00")
    assert out.read_bytes() == b1 and out.stat().st_mtime_ns == 1_000_000_000 and j["generated_at"] == "2026-10-16T23:40:00+08:00"
    j = run(root, ok_summary(remaining=["2026-10-19"]), now="2026-10-17T09:00:00Z")
    assert j["generated_at"] == "2026-10-17T17:00:00+08:00" and out.read_bytes() != b1    # UTC 輸入一律轉 +08:00
    assert not (root / "data" / "web" / "status.json.tmp").exists()
