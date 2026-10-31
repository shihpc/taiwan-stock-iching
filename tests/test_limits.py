"""P4-E2 交易限制旗標：`src/iching/limits.py` 解析純函式＋`scripts/build_limits.py` 全流程（假 fetch、免網路）。
樣本形狀取自 2026-10-27／28 實測回應（欄名、民國日期、標記字元），數值為合成。"""
from __future__ import annotations

import importlib.util
import json
import socket
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
import sys  # noqa: E402

sys.path.insert(0, str(ROOT / "src"))
from iching import limits as L  # noqa: E402

_spec = importlib.util.spec_from_file_location("build_limits", ROOT / "scripts" / "build_limits.py")
BL = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(BL)

T = "2026-10-28"
TW92 = {"stat": "OK", "fields": ["證券代號", "證券名稱", "暫停融券賣出", "暫停借券賣出", "前一交易日收盤價跌停本日禁止平盤下融券、借券賣出"],
        "data": [["2330", "台積電", "", " ", " "], ["00689R", "反1 ", "*", " ", " "], ["2317", "鴻海", "", " ", "*"],
                 ["9999", "X", "", "*", ""]]}
TPMARK = {"tables": [{"fields": ["證券代號", "證券名稱", "暫停融券賣出", "暫停借券賣出", "前一交易日收盤價跌停本日禁止平盤下融(借)券賣出"],
                      "data": [["6488", "環球晶", "", "", ""], ["3105", "穩懋", "Ｙ", "", ""]]}]}
TWNOTICE = {"fields": ["編號", "證券代號", "證券名稱", "累計次數", "注意交易資訊", "日期", "收盤價", "本益比"],
            "data": [[1, "2317", "鴻海", "3", "漲幅達30%<br>週轉率", "115.10.28", "200", "10"]]}
TPATT = {"tables": [{"fields": ["編號", "證券代號", "證券名稱", "累計", "注意交易資訊", "公告日期", "收盤價", "本益比", "link"],
                     "data": [[1, "3105", "穩懋", 22, "沖銷比率", "115/10/28", "1", "N/A", ""]]}]}
TWPUNISH = {"fields": ["編號", "公布日期", "證券代號", "證券名稱", "累計", "處置條件", "處置起迄時間", "處置措施", "處置內容", "備註"],
            "data": [[1, "115/10/27", "2330", "台積電", 1, "連續3日", "115/10/28～115/11/03", "第一次處置", "x", ""],
                     [2, "115/10/28", "2317", "鴻海", 1, "連續3日", "115/10/29～115/11/04", "第一次處置", "x", ""],
                     [3, "115/09/01", "2317", "鴻海", 1, "舊", "115/09/02～115/09/08", "第一次處置", "x", ""],
                     [4, "115/10/28", "1101", "台泥", 1, "壞", "無", "第一次處置", "x", ""]]}
TPDISP = {"tables": [{"fields": ["編號", "公布日期", "證券代號", "證券名稱", "累計", "處置起訖時間", "處置原因", "處置措施", "處置內容", "收盤價", "本益比", " "],
                      "data": [[1, "115/10/22", "6488", "環球晶(../x?code=6488)", 4, "115/10/23~115/11/03", "連續5日", "再次處置", "x", "1", "1", ""]]}]}
BFI84U = [{"Code": "2330", "Name": "台積電", "StartDate": "1151102", "EndDate": "1151105", "Reason": "除息"},
          {"Code": "2317", "Name": "鴻海", "StartDate": "1151001", "EndDate": "1151005", "Reason": "股東會"}]
TPTERM = [{"SecuritiesCompanyCode": "6488", "ShortSaleSuspensionStartDate": "1151028", "ShortSaleSuspensionEndDate": "1151102", "Reason": ""}]
TWTAWU = [{"Code": "1101", "TradingHaltDate": "1151027", "TradingResumptionDate": ""}]
SPENDI = [{"Serial": "1", "SecuritiesCompanyCode": "3105", "DateOfSuspendedTrading": "1150618", "DateOfResumedTrading": ""},
          {"Serial": "2", "SecuritiesCompanyCode": "3105", "DateOfSuspendedTrading": "", "DateOfResumedTrading": "1150622"},
          {"Serial": "3", "SecuritiesCompanyCode": "6488", "DateOfSuspendedTrading": "1151020", "DateOfResumedTrading": ""},
          {"Serial": "4", "SecuritiesCompanyCode": "6488", "DateOfSuspendedTrading": "", "DateOfResumedTrading": "1151030"}]
TWT85U = [{"Code": "1101", "Name": "台泥", "PeriodicCallAuctionTrading": "  "}]
CMODE = [{"SecuritiesCompanyCode": "3105", "AlteredTrading": "Ｙ", "SuspensionOfTrading": ""},
         {"SecuritiesCompanyCode": "6669", "AlteredTrading": "", "SuspensionOfTrading": "Ｙ"}]
BODIES = {"twse_twt92u": TW92, "tpex_margin_mark": TPMARK, "twse_notice": TWNOTICE, "tpex_attention": TPATT,
          "twse_punish": TWPUNISH, "tpex_disposal": TPDISP, "twse_bfi84u": BFI84U, "tpex_term": TPTERM,
          "twse_twtawu": TWTAWU, "tpex_spendi": SPENDI, "twse_twt85u": TWT85U, "tpex_cmode": CMODE}
SITE = {"2330": "twse", "2317": "twse", "1101": "twse", "6488": "tpex", "3105": "tpex", "6669": "tpex", "4444": "tpex"}


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("測試不得碰網路")
    monkeypatch.setattr(socket, "create_connection", boom)


def fake_get(fail=()):
    url2key = {u: k for (_, _, k, u, _) in BL.sources(T)}

    def get(url, params):
        k = url2key[url]
        if k in fail:
            raise BL.BuildLimitsError("HTTP 307（WAF 擋頁）")
        return BODIES[k], {"last_modified": "x"}
    return get


def repo(tmp_path, date=T):
    (tmp_path / "data" / "web").mkdir(parents=True)
    (tmp_path / "data" / "web" / "latest.json").write_text(
        json.dumps({"date": date, "stocks": {c: {"market": m} for c, m in SITE.items()}}), encoding="utf-8")
    return tmp_path


def test_roc_and_period():
    assert L.roc("1151027") == L.roc("115/10/27") == L.roc("115.10.27") == "2026-10-27"
    assert L.roc("1150230") is None and L.roc("") is None and L.roc(None) is None
    assert L.period("115/10/28～115/11/03") == ("2026-10-28", "2026-11-03") == L.period("1151028~1151103")
    assert L.period("無") is None


def test_parse_margin_list_and_notice():
    m = L.parse_margin_list(TW92)
    assert m["00689R"] == {"ms": True, "sbl": False, "dn": False} and m["2317"]["dn"] and m["2330"] == {"ms": False, "sbl": False, "dn": False}
    assert L.parse_margin_list(TPMARK)["3105"]["ms"]
    n = L.parse_notice(TWNOTICE)["2317"]
    assert n == {"d": "2026-10-28", "cnt": 3, "info": "漲幅達30%；週轉率"}
    assert L.parse_notice(TPATT)["3105"]["cnt"] == 22


def test_disposal_parse_and_state():
    d = L.parse_disposal(TWPUNISH)
    assert set(d) == {"2330", "2317"}                                       # 起訖不合的 1101 略過
    assert L.disposal_state(d["2330"], T)["st"] == "active"
    s = L.disposal_state(d["2317"], T)
    assert s["st"] == "upcoming" and s["from"] == "2026-10-29"
    assert L.parse_disposal(TPDISP)["6488"][0]["to"] == "2026-11-03"
    assert L.disposal_state([{"from": "2026-09-02", "to": "2026-09-08"}], T) is None


def test_halts():
    assert L.halted_twse(TWTAWU, T) == {"1101": {"from": "2026-10-27", "resume": None}}
    assert L.halted_twse(TWTAWU, "2026-10-26") == {}
    h = L.halted_tpex(SPENDI, T)
    assert "3105" not in h and h["6488"] == {"from": "2026-10-20", "resume": "2026-10-30"}
    assert L.parse_tpex_cmode(CMODE) == ({"3105"}, {"6669"})


import datetime as _dt  # noqa: E402

NIGHT = lambda: _dt.datetime(2026, 10, 28, 22, 40, tzinfo=BL.TPE)  # noqa: E731


def test_build_full(tmp_path):
    root = repo(tmp_path)
    res = BL.build(root, get=fake_get(), sleep=lambda s: None, now=NIGHT)
    w = json.loads((root / "data" / "web" / "lim.json").read_text(encoding="utf-8"))
    assert w["date"] == T and all(w["checked"].values()) and w == res["web"]
    st = w["stocks"]
    assert st["2330"]["dp"]["st"] == "active" and st["2330"]["tm"] == {"from": "2026-11-02", "to": "2026-11-05", "reason": "除息", "infer": True}
    assert st["2317"]["dn"] == 1 and st["2317"]["dp"]["st"] == "upcoming" and "tm" not in st["2317"]   # 已過期的停券預告不列
    assert st["2317"]["nt"]["cnt"] == 3 and st["2317"]["n10"] == 1
    assert st["1101"]["noc"] == 1 and st["1101"]["ht"]["from"] == "2026-10-27" and st["1101"]["al"] == 1
    assert st["3105"]["ms"] == 1 and st["3105"]["al"] == 1 and "ht" not in st["3105"]
    assert st["6488"]["ht"]["resume"] == "2026-10-30" and st["6488"]["tm"]["infer"] is False
    assert st["6669"]["ht"] == {"from": None, "resume": None} and st["4444"] == {"noc": 1}
    assert "9999" not in st and "00689R" not in st                          # 只列本站涵蓋代號
    snap = json.loads((root / "data" / "limits" / f"{T}.json").read_text(encoding="utf-8"))
    assert all(v["ok"] for v in snap["sources"].values())


def test_partial_failure_marks_unchecked(tmp_path):
    root = repo(tmp_path)
    BL.build(root, get=fake_get(fail={"twse_twt92u", "twse_punish"}), sleep=lambda s: None, now=NIGHT)
    w = json.loads((root / "data" / "web" / "lim.json").read_text(encoding="utf-8"))
    assert w["checked"]["margin|twse"] is False and w["checked"]["disposal|twse"] is False and w["checked"]["margin|tpex"] is True
    assert "noc" not in w["stocks"].get("1101", {}) and "dp" not in w["stocks"].get("2330", {})


def test_all_fail_aborts_without_web(tmp_path):
    root = repo(tmp_path)
    with pytest.raises(BL.BuildLimitsError):
        BL.build(root, get=fake_get(fail=set(BODIES)), sleep=lambda s: None, now=NIGHT)
    assert not (root / "data" / "web" / "lim.json").exists()


def test_notice_accumulation(tmp_path):
    root = repo(tmp_path)
    d = root / "data" / "limits"
    d.mkdir(parents=True)
    for day, codes in (("2026-10-26", ["2317"]), ("2026-10-27", ["2317", "2330"]), ("2026-10-29", ["2330"])):
        src = {"twse_notice": {"ok": True, "data": {c: {} for c in codes}}, "tpex_attention": {"ok": True, "data": {}}}
        (d / f"{day}.json").write_text(json.dumps({"sources": src}), encoding="utf-8")
    (d / "2026-10-25.json").write_text(json.dumps({"sources": {"twse_notice": {"ok": False}, "tpex_attention": {"ok": True, "data": {}}}}), encoding="utf-8")
    BL.build(root, get=fake_get(), sleep=lambda s: None, now=NIGHT)
    w = json.loads((root / "data" / "web" / "lim.json").read_text(encoding="utf-8"))
    assert w["n10_snaps"] == 3                                               # 10-26、10-27、10-28（10-25 來源失敗不計；10-29 晚於 T）
    assert w["stocks"]["2317"]["n10"] == 3 and w["stocks"]["2330"]["n10"] == 1


def test_daily_workflow_limits_step():
    import yaml
    steps = yaml.safe_load((ROOT / ".github/workflows/daily.yml").read_text(encoding="utf-8"))["jobs"]["daily"]["steps"]
    idx = lambda pred: next(i for i, s in enumerate(steps) if pred(s))  # noqa: E731
    i_k, i_l, i_st, i_c = (idx(lambda s, x=x: s.get("id") == x) for x in ("kline", "limits", "status", "commit"))
    assert i_k < i_l < i_st < i_c and "continue-on-error" not in steps[i_l]
    run = steps[i_l]["run"]
    assert "python scripts/build_limits.py --root ." in run and 'echo "ok=0" >> "$GITHUB_OUTPUT"' in run
    restore = [ln.strip() for ln in run.splitlines() if "git checkout" in ln or "git clean" in ln]
    assert len(restore) == 2 and all("data/limits/ data/web/lim.json" in ln for ln in restore)
    i_red = idx(lambda s: "steps.limits.outputs.ok" in str(s.get("if") or ""))
    assert i_red > i_c and steps[i_red]["if"] == "${{ !cancelled() && steps.limits.outcome == 'success' && steps.limits.outputs.ok == '0' }}"


def test_halt_boundaries_on_T():
    """停牌或恢復剛好落在 T：暫停日＝T 算停牌中；恢復日＝T 算已恢復（兩市場）。"""
    assert "1101" in L.halted_twse([{"Code": "1101", "TradingHaltDate": "1151028", "TradingResumptionDate": ""}], T)
    assert L.halted_twse([{"Code": "1101", "TradingHaltDate": "1151027", "TradingResumptionDate": "1151028"}], T) == {}
    ev = [{"Serial": "1", "SecuritiesCompanyCode": "6488", "DateOfSuspendedTrading": "1151028", "DateOfResumedTrading": ""}]
    assert L.halted_tpex(ev, T)["6488"]["from"] == T
    ev += [{"Serial": "2", "SecuritiesCompanyCode": "6488", "DateOfSuspendedTrading": "", "DateOfResumedTrading": "1151028"}]
    assert L.halted_tpex([{**ev[0], "DateOfSuspendedTrading": "1151020"}, ev[1]], T) == {}


def test_morning_run_skips_and_keeps_snapshot(tmp_path):
    root = repo(tmp_path)
    BL.build(root, get=fake_get(), sleep=lambda s: None, now=NIGHT)
    p = root / "data" / "limits" / f"{T}.json"
    before = p.read_bytes()
    res = BL.build(root, get=fake_get(fail=set(BODIES)), sleep=lambda s: None,
                   now=lambda: _dt.datetime(2026, 10, 29, 7, 12, tzinfo=BL.TPE))
    assert res["skipped"] == "off_day" and p.read_bytes() == before
    for when in ((10, 29, 22, 30), (10, 29, 12, 30), (10, 30, 3, 0), (10, 27, 22, 30)):   # T+1 晚班（假日／TAIEX 晚到）、T+1 補跑、T+2、T 前一天
        r = BL.build(root, get=fake_get(fail=set(BODIES)), sleep=lambda s: None,
                     now=lambda w=when: _dt.datetime(2026, w[0], w[1], w[2], w[3], tzinfo=BL.TPE))
        assert r["skipped"] == "off_day" and p.read_bytes() == before, when
    r = BL.build(root, get=fake_get(), sleep=lambda s: None, now=lambda: _dt.datetime(2026, 10, 29, 4, 59, tzinfo=BL.TPE))
    assert r.get("skipped") != "off_day"                                     # 延遲跨午夜、05:00 前照寫（界線前一分鐘）
    r = BL.build(root, get=fake_get(), sleep=lambda s: None, now=lambda: _dt.datetime(2026, 10, 29, 5, 0, tzinfo=BL.TPE))
    assert r["skipped"] == "off_day" and r["missing"] is False             # 05:00 起略過


def test_rerun_keeps_previous_ok_and_unchanged_not_rewritten(tmp_path):
    root = repo(tmp_path)
    BL.build(root, get=fake_get(), sleep=lambda s: None, now=NIGHT)
    p, w = root / "data" / "limits" / f"{T}.json", root / "data" / "web" / "lim.json"
    b1, w1 = p.read_bytes(), w.read_bytes()
    later = lambda: _dt.datetime(2026, 10, 28, 23, 35, tzinfo=BL.TPE)  # noqa: E731
    res = BL.build(root, get=fake_get(), sleep=lambda s: None, now=later)
    assert res["skipped"] == "unchanged" and p.read_bytes() == b1 and w.read_bytes() == w1
    res = BL.build(root, get=fake_get(fail={"twse_twt92u"}), sleep=lambda s: None, now=later)
    kept = res["snap"]["twse_twt92u"]
    assert kept["ok"] and kept["kept"] and "WAF" in kept["retry_error"]
    assert res["skipped"] == "unchanged" and p.read_bytes() == b1           # 沿用後內容與前次相同 → 不重寫、不多一個 commit
    assert json.loads(w.read_text(encoding="utf-8"))["checked"]["margin|twse"] is True


def test_unchanged_ignores_volatile_and_requires_web_date(tmp_path):
    root = repo(tmp_path)
    BL.build(root, get=fake_get(), sleep=lambda s: None, now=NIGHT)
    w = root / "data" / "web" / "lim.json"

    def get_lm(url, params):
        body, meta = fake_get()(url, params)
        return body, {**meta, "last_modified": "changed"}
    later = lambda: _dt.datetime(2026, 10, 28, 23, 35, tzinfo=BL.TPE)  # noqa: E731
    assert BL.build(root, get=get_lm, sleep=lambda s: None, now=later)["skipped"] == "unchanged"
    w.write_text(json.dumps({"date": "2026-10-27"}), encoding="utf-8")       # web 檔日期不是 T → 必須重寫
    res = BL.build(root, get=fake_get(), sleep=lambda s: None, now=later)
    assert "skipped" not in res and json.loads(w.read_text(encoding="utf-8"))["date"] == T


def test_previous_fail_now_ok_uses_new(tmp_path):
    root = repo(tmp_path)
    BL.build(root, get=fake_get(fail={"tpex_cmode"}), sleep=lambda s: None, now=NIGHT)
    BL.build(root, get=fake_get(), sleep=lambda s: None, now=lambda: _dt.datetime(2026, 10, 28, 23, 35, tzinfo=BL.TPE))
    src = json.loads((root / "data" / "limits" / f"{T}.json").read_text(encoding="utf-8"))["sources"]["tpex_cmode"]
    assert src["ok"] and "kept" not in src


def test_main_notices_kept_sources(tmp_path, monkeypatch, capsys):
    root = repo(tmp_path)
    BL.build(root, get=fake_get(), sleep=lambda s: None, now=NIGHT)
    orig = BL.build
    monkeypatch.setattr(BL, "build", lambda r: orig(r, get=fake_get(fail={"twse_twt92u"}), sleep=lambda s: None,
                                                   now=lambda: _dt.datetime(2026, 10, 28, 23, 35, tzinfo=BL.TPE)))
    assert BL.main(["--root", str(root)]) == 0
    assert "::notice::build_limits 來源 twse_twt92u 本班失敗、沿用" in capsys.readouterr().out


def test_off_day_warns_when_T_has_no_snapshot(tmp_path, monkeypatch, capsys):
    root = repo(tmp_path)
    orig = BL.build
    monkeypatch.setattr(BL, "build", lambda r: orig(r, get=fake_get(), sleep=lambda s: None,
                                                   now=lambda: _dt.datetime(2026, 10, 29, 7, 12, tzinfo=BL.TPE)))
    assert BL.main(["--root", str(root)]) == 0
    assert "::warning::build_limits：資料日 2026-10-28 沒有任何交易限制快照" in capsys.readouterr().out


def test_unchanged_ignores_tls_flag(tmp_path):
    root = repo(tmp_path)
    BL.build(root, get=fake_get(), sleep=lambda s: None, now=NIGHT)

    def get_tls(url, params):
        body, meta = fake_get()(url, params)
        return body, {**meta, "tls_insecure": True}
    assert BL.build(root, get=get_tls, sleep=lambda s: None,
                    now=lambda: _dt.datetime(2026, 10, 28, 23, 35, tzinfo=BL.TPE))["skipped"] == "unchanged"

