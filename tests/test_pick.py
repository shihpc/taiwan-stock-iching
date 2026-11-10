"""P4-L2 選多空（排名版）：src/iching/pick.py 純函式＋scripts/build_pick.py 端到端（docs/P4-PREVIEW.md §24）。全部離線。"""
from __future__ import annotations

import gzip
import importlib.util
import json
import math
import socket
from pathlib import Path

import numpy as np
import pytest

from iching import pick as P
from iching.adjust import ADJUST_SOURCES

ROOT = Path(__file__).resolve().parents[1]


def _load(name: str, rel: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / rel)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


BP = _load("build_pick_t", "scripts/build_pick.py")
T = "2026-11-10"


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def boom(*a, **k):
        raise OSError("network disabled in tests")
    monkeypatch.setattr(socket, "create_connection", boom)


# ---------------------------------------------------------------------------
# 名額
def test_n0_table_matches_revalidate():
    RV = _load("rv_t", "scripts/revalidate_thresholds.py")
    assert P.N0_TABLE == RV.N0_TABLE
    assert P.n0("undetermined", "long") is None and P.n0(None, "short") is None
    assert P.n0("S4", "short") == 20 and P.n0("S1", "long") == 20


def test_cut_n_floor_and_bounds():
    assert P.cut_n(20, 0.375) == 7          # 7.5 → 7（floor，不是 round）
    assert P.cut_n(5, 0.5) == 2             # 2.5 → 2
    assert P.cut_n(5, 0.1) == 0             # 0.5 → 0：允許空名單
    assert P.cut_n(10, -1.0) == 0
    assert P.cut_n(10, None) == 10
    assert P.cut_n(None, 1.0) == 0
    assert P.cut_n(10, float("nan")) == 0


# ---------------------------------------------------------------------------
# 做空分數
def test_short_raw_weights_and_unknown():
    lines = {2: 40.0, 3: 50.0, 4: 60.0, 5: 30.0}
    exp = (25 * 60 + 20 * 50 + 20 * 40 + 20 * 70) / 85
    assert P.short_raw(lines, "short") == pytest.approx(exp)
    exp_mid = (25 * 60 + 15 * 50 + 10 * 40 + 15 * 70) / 65
    assert P.short_raw(lines, "mid") == pytest.approx(exp_mid)
    assert P.short_raw({**lines, 4: None}, "short") is None
    assert P.short_raw({**lines, 5: float("nan")}, "swing") is None
    # 不含初爻與上爻：改動它們不影響做空分數
    assert P.short_raw({**lines, 1: 0.0, 6: 100.0}, "short") == pytest.approx(exp)


def test_deductions_each_rule():
    d = P.deductions(-0.35, 40.0, True, 110.0, 100.0, 5.0)
    assert (d["D1"], d["D2"], d["D3"], d["pts"]) == ("hit", "hit", "hit", 25.0)
    d = P.deductions(0.05, 10.0, True, 90.0, 100.0, 50.0)
    assert (d["D1"], d["D2"], d["D3"], d["pts"]) == ("no", "no", "no", 0.0)
    # D2：券資比高但 C ≤ MA20 → 不成立
    assert P.deductions(0.0, 40.0, True, 100.0, 100.0, 50.0)["D2"] == "no"
    # D2 未查核 → 不扣
    d = P.deductions(0.0, None, False, 110.0, 100.0, 50.0)
    assert d["D2"] == "unchecked" and d["pts"] == 0.0
    # D2 已查核但缺收盤 → miss＝扣
    d = P.deductions(0.0, 40.0, True, None, 100.0, 50.0)
    assert d["D2"] == "miss" and d["pts"] == 10.0
    # 缺值視為成立（S1a §3.3）：ret60 缺 → D1、D3 皆 miss
    d = P.deductions(None, None, False, None, None, 50.0)
    assert (d["D1"], d["D3"], d["pts"]) == ("miss", "miss", 15.0)
    d = P.deductions(-0.01, None, False, None, None, None)
    assert d["D3"] == "miss" and d["pts"] == 5.0
    # D3 要兩條件都成立
    assert P.deductions(0.01, None, False, None, None, 5.0)["D3"] == "no"


def test_short_final_floor():
    assert P.short_final(20.0, 25.0) == P.SCORE_FLOOR == 7.30
    assert P.short_final(60.0, 10.0) == 50.0
    assert P.short_final(None, 0.0) is None


# ---------------------------------------------------------------------------
# 風險
def test_risk_grade_counts():
    p80 = 0.05
    base = {"v": 0.01, "e": 0.0, "g": 0.1, "m": 0.01}
    assert P.risk_grade(base, p80, "long") == ("A", [])
    assert P.risk_grade({**base, "g": 1.0}, p80, "long") == ("B", ["gap"])
    assert P.risk_grade({**base, "g": 2.0, "m": 0.2}, p80, "long")[0] == "C"
    assert P.risk_grade({**base, "g": 2.0, "m": 0.2, "v": 0.06}, p80, "long")[0] == "D"     # 3 項
    # 追價＋高波動 → D（只有 2 項也是 D）
    assert P.risk_grade({**base, "e": 3.0, "v": 0.05}, p80, "long") == ("D", ["vol", "chase"])
    # 追價依方向
    assert P.risk_grade({**base, "e": 3.5}, p80, "short") == ("A", [])
    assert P.risk_grade({**base, "e": -3.0}, p80, "short") == ("B", ["chase"])
    # 缺值視為違規
    g, vi = P.risk_grade({**base, "m": None}, p80, "long")
    assert g == "B" and vi == ["dd_na"]
    assert P.risk_grade({**base, "v": 0.01}, None, "long")[1] == ["vol_na"]


def _bars(n=40, c=100.0):
    c_ = np.full(n, c)
    return c_.copy(), c_ + 1.0, c_ - 1.0, c_.copy()


def test_risk_metrics_basic_and_gap_exclusion():
    o, h, lo, c = _bars()
    o[-1] = 105.0                                   # T 日跳空 5 元、ATR＝2
    ex = np.zeros(o.size, dtype=bool)
    m = P.risk_metrics(o, h, lo, c, ex, "simple")
    assert m["atr"] == pytest.approx(2.0) and m["v"] == pytest.approx(0.02)
    assert m["g"] == pytest.approx(2.5) and m["e"] == pytest.approx(0.0) and m["m"] == pytest.approx(0.0)
    ex[-1] = True                                   # 係數事件日排除
    assert P.risk_metrics(o, h, lo, c, ex, "simple")["g"] == pytest.approx(0.0)
    c2 = c.copy()
    c2[-5] = 120.0
    c2[-1] = 96.0
    assert P.risk_metrics(o, h, lo, c2, ex, "simple")["m"] == pytest.approx(0.2)
    # 太短 → 全部 None
    s = P.risk_metrics(o[:10], h[:10], lo[:10], c[:10], ex[:10], "simple")
    assert s["v"] is None and s["e"] is None and s["g"] is None and s["m"] is None


def test_v_threshold():
    assert P.v_threshold([None, 0.01, 0.02, 0.03, 0.04, 0.05]) == pytest.approx(0.042)
    assert P.v_threshold([None]) is None


def test_ret_ma_phist():
    c = np.arange(1.0, 62.0)
    assert P.ret_n(c) == pytest.approx(61 / 1 - 1)
    assert P.ret_n(c[:60]) is None
    c[0] = np.nan
    assert P.ret_n(c) is None
    assert P.ma_last(np.arange(20.0)) == pytest.approx(9.5)
    from iching.score.params import Rules
    r = Rules()
    mb = np.arange(250.0)
    assert P.margin_phist(mb, r) == pytest.approx(100.0 * 249.5 / 250)
    assert P.margin_phist(mb[:249], r) is None
    mb[3] = np.nan
    assert P.margin_phist(mb, r) is None


def test_liquidity():
    a = np.full(20, 5e7)
    v = np.full(20, 100.0)
    assert P.liquidity(a, v)["ok"]
    v[-3] = 0                                       # 1 日無成交即不過
    assert not P.liquidity(a, v)["ok"]
    a2 = np.full(20, 5e7)
    a2[:11] = np.nan                                # 停牌／缺日以 0 計 → 中位數 0
    r = P.liquidity(a2, np.full(20, 100.0))
    assert r["med"] == 0.0 and not r["ok"]
    assert not P.liquidity(np.full(20, 2.9e7), np.full(20, 1.0))["ok"]
    assert P.liquidity(np.full(5, 5e7), np.full(5, 1.0))["zero"] == 15   # 不足 20 日 → 補 0


# ---------------------------------------------------------------------------
# 交易限制 × 方向（S1 §A5.2）
@pytest.mark.parametrize("flags,long_rm,short_rm,short_rs,tags", [
    ({"ht": {"from": T, "resume": None}}, ["halt"], ["halt"], [], []),
    ({"dp": {"st": "active"}}, ["disposal"], ["disposal"], [], []),
    ({"dp": {"st": "upcoming"}}, [], [], [], ["disposal_upcoming"]),
    ({"nt": {"d": T}}, [], [], [], ["notice"]),
    ({"al": 1}, [], [], [], ["altered"]),                          # 全額交割：只加旗標（使用者決定）
    ({"noc": 1}, [], ["noc"], [], []),
    ({"ms": 1, "sbl": 1}, [], ["ms_sbl"], [], []),
    ({"ms": 1}, [], [], ["ms"], []),
    ({"sbl": 1}, [], [], ["sbl"], []),
    ({"dn": 1}, [], [], ["dn"], []),
    ({"tm": {"from": "2026-11-06", "to": "2026-11-11", "infer": True}}, [], ["tm_active"], [], ["tm_inferred"]),
    ({"tm": {"from": "2026-11-12", "to": "2026-11-16", "infer": False}}, [], [], ["tm_upcoming"], []),
    (None, [], [], [], []),
])
def test_limit_effect_rows(flags, long_rm, short_rm, short_rs, tags):
    e = P.limit_effect(flags, T)
    assert e["long"]["remove"] == long_rm and e["long"]["research"] == []
    assert e["short"]["remove"] == short_rm and e["short"]["research"] == short_rs
    assert e["tags"] == tags


def test_lim_status_and_confirmed():
    chk = {f"{c}|{m}": True for c in P.LIM_CATS for m in P.MARKETS}
    lim = {"date": T, "checked": chk}
    st = P.lim_status(lim, T, "twse")
    assert st == {"ok": True, "date_ok": True, "unchecked": []}
    assert P.short_confirmed(None, st)
    for f in ({"noc": 1}, {"ms": 1}, {"sbl": 1}, {"dn": 1}, {"tm": {"from": "2026-11-20"}}):
        assert not P.short_confirmed(f, st)
    assert P.short_confirmed({"nt": {}, "al": 1}, st)                # 注意股、全額交割不影響資格
    st2 = P.lim_status({**lim, "checked": {**chk, "margin|tpex": False}}, T, "tpex")
    assert st2["unchecked"] == ["margin"] and not st2["ok"] and not P.short_confirmed(None, st2)
    assert P.short_confirmed(None, P.lim_status({**lim, "checked": {**chk, "notice|twse": False}}, T, "twse"))
    st3 = P.lim_status({**lim, "date": "2026-11-09"}, T, "twse")
    assert not st3["date_ok"] and st3["unchecked"] == list(P.LIM_CATS) and not P.short_confirmed(None, st3)
    assert not P.lim_status(None, T, "twse")["ok"]


# ---------------------------------------------------------------------------
# 排序與分級
def test_rank_rows_ties():
    assert P.rank_rows([("b", 5.0), ("a", 5.0), ("c", 7.0), ("d", 1.0)]) == \
        [(1, "c", 7.0), (2, "a", 5.0), (2, "b", 5.0), (4, "d", 1.0)]


def test_threshold_from_t0():
    t0 = {"cells": {"twse|short|S4|long": {"thresholds": {"0": 60.0, "5": 62.0, "10": 64.0, "15": 66.0, "20": 68.0}}}}
    assert P.threshold_from_t0(t0, "twse", "short", "S4", "long", 1.0) == 64.0
    assert P.threshold_from_t0(t0, "twse", "short", "S4", "long", None) == 60.0
    assert P.threshold_from_t0(t0, "twse", "short", "S4", "long", 2.5) is None
    assert P.threshold_from_t0(t0, "twse", "short", "S3", "long", 0.0) is None
    assert P.threshold_from_t0(t0, "twse", "short", "undetermined", "long", 0.0) is None
    assert P.threshold_from_t0(None, "twse", "short", "S4", "long", 0.0) is None


def _st(bs, sraw, sfin=None, *, liq=True, gl="A", gs="A", lim=None, confirmed=True, unchecked=False):
    return {"bs": bs, "sraw": sraw, "sfin": sraw if sfin is None else sfin, "liq_ok": liq, "grade": {"long": gl, "short": gs},
            "lim": P.limit_effect(lim, T), "confirmed": confirmed, "lim_unchecked": unchecked}


def test_grade_block_undetermined():
    r = P.grade_block(state="undetermined", n_long=5, n_short=5, t_long=0, t_short=0, stocks={"a": _st(90, 90)})
    assert r["reason"] == "basic_state_undetermined" and r["long"] == [] and r["short_confirmed"] == []


def test_grade_block_long_filters_and_short_list():
    stocks = {"a": _st(90, 10), "b": _st(85, 10, gl="D"), "c": _st(80, 10, liq=False),
              "d": _st(75, 10, lim={"ht": {}}), "e": _st(70, 10), "f": _st(50, 10)}
    r = P.grade_block(state="S1", n_long=5, n_short=0, t_long=60, t_short=None, stocks=stocks)
    assert r["long"] == ["a", "e"]                       # D 級、流動性、停牌移除；f 未達門檻
    assert r["short_list"]["long"] is True and r["watch"]["long"] == 4
    r = P.grade_block(state="S1", n_long=1, n_short=0, t_long=60, t_short=None, stocks=stocks)
    assert r["long"] == ["a"] and r["short_list"]["long"] is False
    r = P.grade_block(state="S1", n_long=0, n_short=0, t_long=60, t_short=None, stocks=stocks)
    assert r["long"] == [] and r["short_list"]["long"] is False      # N＝0：空名單、不算不足
    r = P.grade_block(state="S1", n_long=5, n_short=5, t_long=None, t_short=None, stocks=stocks)
    assert r["long"] == [] and r["short_list"] == {"long": False, "short": False}   # T0 未就緒：不出候選


def test_grade_block_risk_penalty_reorders():
    # 40 檔：a 池內百分位 100、C 級 −5 → 95；b 97.5、A 級 → 97.5 → b 排在 a 前（§24.4 等級減項）
    stocks = {f"s{i:02d}": _st(float(i), 0) for i in range(38)}
    stocks["a"] = _st(90.0, 0, gl="C")
    stocks["b"] = _st(89.0, 0)
    r = P.grade_block(state="S1", n_long=2, n_short=0, t_long=0, t_short=None, stocks=stocks)
    assert r["long"] == ["b", "a"]
    stocks["a"] = _st(90.0, 0, gl="B")                  # B 級 −2 → 98 > 97.5 → a 仍在前
    r = P.grade_block(state="S1", n_long=2, n_short=0, t_long=0, t_short=None, stocks=stocks)
    assert r["long"] == ["a", "b"]
    assert P.pool_pct(90, [90, 88, 87, 5]) == 100.0 and P.pool_pct(1, []) == 0.0


def test_grade_block_short_threshold_uses_raw_and_confirmation():
    # x：扣分前 70 ≥ 門檻 60，扣分後 40 → 仍過門檻（門檻比較用扣分前）
    stocks = {"x": _st(10, 70, 40), "y": _st(10, 65, 65, lim={"dn": 1}), "z": _st(10, 62, 62, lim={"noc": 1}),
              "w": _st(10, 61, 61, unchecked=True, confirmed=False), "v": _st(10, 55, 90)}
    r = P.grade_block(state="S4", n_long=0, n_short=10, t_long=None, t_short=60, stocks=stocks)
    picked = r["short_confirmed"] + r["short_research"]
    assert set(picked) == {"x", "y", "w"}               # z：noc 移除；v：扣分前 55 未達門檻
    assert r["short_confirmed"] == ["x"]                 # y：dn → 研究候選；w：限制未查核 → 研究候選
    assert set(r["short_research"]) == {"y", "w"} and r["short_list"]["short"] is True
    # 排序依扣分後：y(65) 先於 w(61) 先於 x(40)
    assert r["short_research"] == ["y", "w"]


def test_spearman():
    assert P.spearman([1, 2, 3, 4], [10, 20, 30, 40]) == pytest.approx(1.0)
    assert P.spearman([1, 2, 3, 4], [4, 3, 2, 1]) == pytest.approx(-1.0)
    assert P.spearman([1, 2, float("nan"), 4], [1, 2, 3, 4]) == pytest.approx(1.0)
    assert P.spearman([1, 1, 1], [1, 2, 3]) is None
    assert P.spearman([1, 2], [1, 2]) is None


def test_event_flags():
    o = np.full(12, 100.0)
    c = np.full(12, 100.0)
    win = [f"2026-11-{d:02d}" for d in range(1, 11)]
    assert P.event_flags([], win, [], o, c, 2.0) == []
    assert P.event_flags(["2026-11-05"], win, [], o, c, 2.0) == ["ex"]
    assert P.event_flags([], win, [("2026-11-09", "公告本公司第三季財務報告")], o, c, 2.0) == ["report"]
    assert P.event_flags([], win, [("2026-10-01", "財務報告")], o, c, 2.0) == []
    c[-2] = 95.0
    assert P.event_flags([], win, [], o, c, 2.0) == ["long_black"]
    assert P.event_flags([], win, [], o, c, None) == []


# ---------------------------------------------------------------------------
# build_pick 端到端（合成 repo）
import datetime as _dt  # noqa: E402
CAL = [(_dt.date(2025, 10, 1) + _dt.timedelta(days=i)).isoformat() for i in range(0, 500)
       if (_dt.date(2025, 10, 1) + _dt.timedelta(days=i)).weekday() < 5
       and (_dt.date(2025, 10, 1) + _dt.timedelta(days=i)).isoformat() <= T]


def _flags(state="S4", lm=0.5, sm=0.375, ls=1.0, ss=1.5):
    return json.dumps({"basic_state": state, "by_direction": {
        "long": {"active": {}, "quota_multiplier": lm, "threshold_shift_deciles": ls},
        "short": {"active": {}, "quota_multiplier": sm, "threshold_shift_deciles": ss}},
        "calibrated": False, "data_insufficient": False, "missing_causes": [], "raw": {}}, sort_keys=True)


SIDS = {"1101": "twse", "1102": "twse", "2330": "twse", "6488": "tpex", "8069": "tpex", "9999": "twse"}


def make_repo(tmp: Path, *, state="S4", t_bundle=True, n_days=None) -> Path:
    root = tmp / "repo"
    (root / "data" / "scores").mkdir(parents=True)
    (root / "data" / "web" / "ev").mkdir(parents=True)
    (root / "runs" / "collect").mkdir(parents=True)
    rows = []
    for m in P.MARKETS:
        for h in P.HORIZONS:
            rows.append({"scope": "market_index", "stock_id": "__MARKET__", "market": m, "horizon": h, "flags": _flags(state),
                         "in_rank_pool": None, "base_score": 50.0})
    for i, (sid, m) in enumerate(sorted(SIDS.items())):
        for h in P.HORIZONS:
            r = {"scope": "stock", "stock_id": sid, "market": m, "horizon": h, "flags": None,
                 "in_rank_pool": 0 if sid == "9999" else 1, "base_score": 40.0 + 7 * i,
                 "model_version": f"mv-{m}"}
            for k in range(1, 7):
                r[f"line_{k}"] = 30.0 + 5 * i + k
                r[f"line_{k}_unknown"] = 0
            if sid == "8069" and h == "mid":
                r["line_3_unknown"] = 1
            rows.append(r)
    (root / "data" / "scores" / f"{T}.json").write_text(json.dumps({"schema": 1, "params_sha": "abc123", "rows": rows}))
    (root / "data" / "web" / "latest.json").write_text(json.dumps({"date": T, "stocks": {s: {"market": m} for s, m in SIDS.items()}}))
    (root / "data" / "calendar_tpe.json").write_text(json.dumps({"dates": CAL}))
    (root / "data" / "factors.json").write_text(json.dumps({"schema": 1, "sources": ADJUST_SOURCES,
                                                             "rows": [["1101", CAL[-3], 100.0, 95.0]]}))
    days = CAL if n_days is None else CAL[-n_days:]
    for j, d in enumerate(days):
        if d == T and not t_bundle:
            continue
        st = {}
        for i, sid in enumerate(sorted(SIDS)):
            px = 50.0 + i * 10 + (j % 7)
            st[sid] = {"open": px, "high": px + 1, "low": px - 1, "close": px, "amount": 1e8 if sid != "2330" else 1e6,
                       "volume": 1000.0, "margin_balance": 1000.0 + j + i}
        with gzip.open(root / "runs" / "collect" / f"{d}-daily.json.gz", "wt", encoding="utf-8") as f:
            json.dump({"tpe_date": d, "stocks": st}, f)
    chk = {f"{c}|{m}": True for c in P.LIM_CATS for m in P.MARKETS}
    (root / "data" / "web" / "lim.json").write_text(json.dumps({"date": T, "checked": chk, "stocks": {
        "1102": {"ms": 1, "sbl": 1}, "6488": {"ht": {"from": T, "resume": None}}}}))
    (root / "data" / "web" / "ev" / "11.json").write_text(json.dumps({"date": T, "cols": ["id", "d", "t", "s"], "stocks": {
        "1101": [["x", T, "10:00", "自結損益公告"]]}}))
    return root


def fake_getter(rows=None, exc=None):
    calls = []

    def get(dataset, **kw):
        calls.append((dataset, kw))
        if exc:
            raise exc
        return rows if rows is not None else [
            {"date": T, "stock_id": "1101", "ShortSaleTodayBalance": 400, "MarginPurchaseTodayBalance": 1000},
            {"date": T, "stock_id": "1102", "ShortSaleTodayBalance": 10, "MarginPurchaseTodayBalance": 1000},
            {"date": T, "stock_id": "6488", "ShortSaleTodayBalance": 5, "MarginPurchaseTodayBalance": 0}]
    get.calls = calls
    return get


def run(root, getter=None, extra=()):
    return BP.main(["--root", str(root), *extra], getter_factory=lambda: getter)


def test_build_rank_mode_end_to_end(tmp_path, capsys):
    root = make_repo(tmp_path)
    g = fake_getter()
    assert run(root, g) == 0
    assert g.calls == [("TaiwanStockMarginPurchaseShortSale", {"start_date": T, "end_date": T})]
    s = json.loads((root / "data" / "pick" / f"{T}.json").read_text())
    w = json.loads((root / "data" / "web" / "pick.json").read_text())
    assert s["mode"] == w["mode"] == "rank" and s["t0"]["status"] == "absent"
    assert set(s["stocks"]) == {"1101", "1102", "2330", "6488", "8069"}         # 9999 不在排名池
    b = s["blocks"]["twse|short"]
    assert b["basic_state"] == "S4" and b["n"] == {"long": 2, "short": 7} and b["n0"] == {"long": 5, "short": 20}
    assert b["threshold"] == {"long": None, "short": None} and "lists" not in b
    # 8069 mid 的三爻未知 → 做空分數缺值、不進做空排名
    assert s["stocks"]["8069"]["h"]["mid"][1] is None and s["blocks"]["tpex|mid"]["n_rank"]["short"] == 1
    # D2：1101 券資比 40%、6488 融資 0 → 已查核但值缺
    assert s["d2"] == {"status": "ok", "n_checked": 3}
    assert s["stocks"]["1101"]["sr"] == [40.0, 1] and s["stocks"]["6488"]["sr"] == [None, 1]
    assert s["stocks"]["8069"]["sr"] == [None, 0] and s["stocks"]["8069"]["d"]["D2"] == "unchecked"
    # 交易限制
    assert s["stocks"]["1102"]["lim"]["rm"]["short"] == ["ms_sbl"] and s["stocks"]["6488"]["lim"]["rm"]["long"] == ["halt"]
    assert s["stocks"]["2330"]["liq"]["ok"] == 0
    assert "report" in s["stocks"]["1101"]["ev"] and "ex" in s["stocks"]["1101"]["ev"]
    # web：前 30 名、排名依分數
    wl = w["blocks"]["twse|short"]["long"]
    assert [r["id"] for r in wl] == ["2330", "1102", "1101"] and wl[0]["r"] == 1
    assert "raw" in w["blocks"]["twse|short"]["short"][0] and "d" in w["blocks"]["twse|short"]["short"][0]
    out = capsys.readouterr().out
    assert "mode=rank" in out


def test_build_idempotent_bytes(tmp_path):
    root = make_repo(tmp_path)
    assert run(root, fake_getter()) == 0
    p1, p2 = root / "data" / "pick" / f"{T}.json", root / "data" / "web" / "pick.json"
    b1, b2 = p1.read_bytes(), p2.read_bytes()
    m1, m2 = p1.stat().st_mtime_ns, p2.stat().st_mtime_ns
    assert run(root, fake_getter()) == 0
    assert p1.read_bytes() == b1 and p2.read_bytes() == b2
    assert p1.stat().st_mtime_ns == m1 and p2.stat().st_mtime_ns == m2      # 內容不變不重寫


def test_build_d2_failures_and_keep_previous(tmp_path):
    root = make_repo(tmp_path)
    assert run(root, None) == 0                                          # 無 token
    s = json.loads((root / "data" / "pick" / f"{T}.json").read_text())
    assert s["d2"]["status"] == "no_fetch" and all(v["d"]["D2"] == "unchecked" for v in s["stocks"].values())
    root2 = make_repo(tmp_path / "b")
    assert run(root2, fake_getter(rows=[{"date": T, "stock_id": "1101", "foo": 1}])) == 0
    assert json.loads((root2 / "data" / "pick" / f"{T}.json").read_text())["d2"]["status"].startswith("fields_missing:")
    root3 = make_repo(tmp_path / "c")
    assert run(root3, fake_getter()) == 0
    assert run(root3, fake_getter(exc=RuntimeError("boom"))) == 0       # 同一 T 重跑失敗 → 沿用前次
    s = json.loads((root3 / "data" / "pick" / f"{T}.json").read_text())
    assert s["d2"]["status"] == "ok" and s["d2"].get("kept") is True and s["stocks"]["1101"]["sr"] == [40.0, 1]
    root4 = make_repo(tmp_path / "d")
    assert run(root4, fake_getter(rows=[])) == 0
    assert json.loads((root4 / "data" / "pick" / f"{T}.json").read_text())["d2"]["status"] == "empty"


def test_build_lim_date_mismatch_marks_unchecked(tmp_path):
    root = make_repo(tmp_path)
    lp = root / "data" / "web" / "lim.json"
    j = json.loads(lp.read_text())
    lp.write_text(json.dumps({**j, "date": "2026-11-09"}))
    assert run(root, fake_getter()) == 0
    s = json.loads((root / "data" / "pick" / f"{T}.json").read_text())
    assert s["lim"] == {"date": "2026-11-09", "date_ok": False}
    assert s["stocks"]["1102"]["lim"]["rm"]["short"] == [] and s["stocks"]["1102"]["cf"] == 0   # 舊日期旗標不套用
    assert s["stocks"]["1101"]["lim"]["un"] == list(P.LIM_CATS)
    w = json.loads((root / "data" / "web" / "pick.json").read_text())
    assert all(r["lu"] == 1 for r in w["blocks"]["twse|short"]["long"])


def test_build_undetermined_and_grade_mode(tmp_path):
    root = make_repo(tmp_path, state="undetermined")
    (root / "data" / "pick").mkdir(parents=True)
    cells = {f"{m}|{h}|{s}|{d}": {"thresholds": {k: 0.0 for k in ("0", "5", "10", "15", "20")}}
             for m in P.MARKETS for h in P.HORIZONS for s in P.STATES for d in P.DIRECTIONS}
    (root / "data" / "pick" / "t0.json").write_text(json.dumps({"params_sha": "abc123", "version": "t", "cells": cells}))
    assert run(root, fake_getter()) == 0
    s = json.loads((root / "data" / "pick" / f"{T}.json").read_text())
    assert s["mode"] == "grade" and s["t0"] == {"status": "ok", "version": "t"}
    b = s["blocks"]["twse|short"]
    assert b["reason"] == "basic_state_undetermined" and b["n"] == {"long": 0, "short": 0}
    assert b["lists"]["long"] == [] and b["lists"]["reason"] == "basic_state_undetermined"

    root2 = make_repo(tmp_path / "b", state="S1")
    (root2 / "data" / "pick").mkdir(parents=True)
    (root2 / "data" / "pick" / "t0.json").write_text(json.dumps({"params_sha": "abc123", "version": "t", "cells": cells}))
    assert run(root2, fake_getter()) == 0
    s = json.loads((root2 / "data" / "pick" / f"{T}.json").read_text())
    lst = s["blocks"]["twse|short"]["lists"]
    assert "2330" not in lst["long"]                     # 流動性未達不進候選
    assert "1102" not in lst["short_confirmed"] + lst["short_research"]    # ms＋sbl → 做空移除

    root3 = make_repo(tmp_path / "c", state="S1")
    (root3 / "data" / "pick").mkdir(parents=True)
    (root3 / "data" / "pick" / "t0.json").write_text(json.dumps({"params_sha": "other", "cells": cells}))
    assert run(root3, fake_getter()) == 0
    s = json.loads((root3 / "data" / "pick" / f"{T}.json").read_text())
    assert s["mode"] == "rank" and s["t0"]["status"] == "params_sha_mismatch"


def test_build_aborts_without_writing(tmp_path):
    root = make_repo(tmp_path, t_bundle=False)
    assert run(root, fake_getter()) == 2
    assert not (root / "data" / "pick").exists() and not (root / "data" / "web" / "pick.json").exists()
    root2 = make_repo(tmp_path / "b")
    (root2 / "data" / "scores" / f"{T}.json").write_text(json.dumps({"rows": "x"}))
    assert run(root2, fake_getter()) == 2


def test_build_short_history_counts_missing(tmp_path):
    """原料包不足 250 日：融資 P_hist 缺 → D3 視為成立而扣分（S1a §3.3）。"""
    root = make_repo(tmp_path, n_days=80)
    assert run(root, fake_getter()) == 0
    s = json.loads((root / "data" / "pick" / f"{T}.json").read_text())
    assert all(v["d"]["D3"] == "miss" and v["mph"] is None for v in s["stocks"].values())
    assert len(s["missing_bundles"]) > 0


def test_daily_workflow_pick_step():
    import yaml
    steps = yaml.safe_load((ROOT / ".github/workflows/daily.yml").read_text(encoding="utf-8"))["jobs"]["daily"]["steps"]
    idx = lambda pred: next(i for i, s in enumerate(steps) if pred(s))  # noqa: E731
    i_l, i_p, i_t, i_st, i_c = (idx(lambda s, x=x: s.get("id") == x) for x in ("limits", "pick", "trial", "status", "commit"))
    assert i_l < i_p < i_t < i_st < i_c and "continue-on-error" not in steps[i_p] and "if" not in steps[i_p]
    assert steps[i_p]["env"]["FINMIND_TOKEN"] == "${{ secrets.FINMIND_TOKEN }}"
    run_ = steps[i_p]["run"]
    assert "python scripts/build_pick.py --root ." in run_ and 'echo "ok=0" >> "$GITHUB_OUTPUT"' in run_
    restore = [ln.strip() for ln in run_.splitlines() if "git checkout" in ln or "git clean" in ln]
    assert len(restore) == 2 and all("data/pick/ data/web/pick.json" in ln for ln in restore)
    i_red = idx(lambda s: "steps.pick.outputs.ok" in str(s.get("if") or ""))
    assert i_red > i_c and steps[i_red]["if"] == "${{ !cancelled() && steps.pick.outcome == 'success' && steps.pick.outputs.ok == '0' }}"


def test_score_engine_untouched():
    """§24.8：P4-L2 不 import 計分引擎的寫入路徑；pick.py 只用 indicators／transform 的純函式。"""
    src = (ROOT / "src" / "iching" / "pick.py").read_text(encoding="utf-8")
    imports = [ln for ln in src.splitlines() if ln.startswith(("import ", "from "))]
    assert all(("iching.score.indicators" in ln or "iching.score.transform" in ln or "iching" not in ln) for ln in imports)
    assert math.isclose(P.SCORE_FLOOR, 7.30)
