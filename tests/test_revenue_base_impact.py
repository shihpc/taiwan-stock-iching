"""`scripts/revenue_base_impact.py`：營收子指標基期影響面量測（唯讀，`docs/P3-CALIBRATION.md` §29）。

分析流程一律用**真實 replay 產出的 db**（`synth_db.build_full`＋本檔追加的月營收＋`replay_scores`）。
追加的月營收刻意做成手算得出的極端：1102 的 2019 年基期只有 1,000 元（且 2019-02 為 0）、6488 的近組 YoY 巨大
而加速度約 −25 pp（拿掉 YoY 會陰陽翻轉）、1103 的 2019-01 基期極小而加速度為 0（拿掉 YoY 會落進 [45, 55]）。
期待值以手算、原始 SQL 或逐列展開另算，不由被測函式產生。
"""
from __future__ import annotations

import dataclasses
import hashlib
import json
import math
import os
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import replay_scores as R  # noqa: E402
import revenue_base_impact as RB  # noqa: E402
import scan_features as SF  # noqa: E402
from iching import replay_state as RS  # noqa: E402
from iching.score import stock as STK  # noqa: E402
from iching.score.params import build_params  # noqa: E402
from iching.score.transform import S_clip  # noqa: E402
from iching.store import Store  # noqa: E402
from synth_db import DV, build_full  # noqa: E402

Y2020 = ("2020-01-01", "2020-12-31")
SCRIPT = ROOT / "scripts" / "hetzner_revbase.sh"
B_PREV = 1e8 / 100000.25            # 6488 前組基期：使前組 YoY 恰比近組大 25 pp


def _pub(y: int, m: int) -> str:
    return f"{y + (m == 12):04d}-{m % 12 + 1:02d}-01"


def add_revenue(cache: Path) -> None:
    rows = []
    for k in range(28):                                            # 2018-01 … 2020-04
        y, m = 2018 + k // 12, k % 12 + 1
        v1102 = 0.0 if (y, m) == (2019, 2) else (1e3 if y == 2019 else 1e8)
        v6488 = B_PREV if (y, m) in {(2018, 7), (2018, 8), (2018, 9)} else (
            1e3 if (y, m) in {(2018, 10), (2018, 11), (2018, 12)} else 1e8)
        v1103 = 1e3 if (y, m) == (2019, 1) else ((3e8 - 1e3) / 2 if (y, m) in {(2018, 11), (2018, 12)} else 1e8)
        for sid, v in (("1102", v1102), ("6488", v6488), ("1103", v1103)):
            rows.append({"date": _pub(y, m), "stock_id": sid, "revenue_year": y, "revenue_month": m, "revenue": v,
                         "create_time": ""})
    with Store(cache / "fundamentals.db") as f:
        f.record_success("month_revenue", "raw_month_revenue", "revbase-test", rows, DV, "TaiwanStockMonthRevenue")


@pytest.fixture(scope="module")
def world(tmp_path_factory) -> Path:
    c = tmp_path_factory.mktemp("revbase") / "cache"
    build_full(c)
    add_revenue(c)
    assert SF.main(["--cache-dir", str(c), "--quiet", "--allow-short-warmup", "--warmup-days", "0"]) == 0
    assert R.main(["--cache-dir", str(c), "--out", str(c / "scores.db"), "--window", "30", "--quiet", "--limit-days", "80"]) == 0
    return c


def _run(world: Path, out: Path, db: Path | None = None, **kw):
    kw.setdefault("per_group", 100000)
    return RB.run(db or world / "scores.db", out, cache_dir=world, start=Y2020[0], end=Y2020[1], quiet=True, **kw)


@pytest.fixture(scope="module")
def census(world, tmp_path_factory):
    d = tmp_path_factory.mktemp("census")
    return _run(world, d / "rep.json", keep_details=True), d


def _copy(world: Path, tmp_path: Path) -> Path:
    c = tmp_path / "cache"
    shutil.copytree(world, c)
    return c


def _row(det, sid: str, date: str, h: str) -> int:
    rows = det["rows"]
    for i in range(rows["n"]):
        if (rows["sid_of"][rows["sid"][i]], rows["dates"][rows["date"][i]], RB.HORIZONS[rows["h"][i]]) == (sid, date, h):
            return i
    raise AssertionError((sid, date, h))


def _S(x: float, d: float) -> float:
    """手算 S_clip（c＝0、direction＋1）：x 截在 ±3d，S＝100／(1＋exp(−ln(7/3)/d·x))。"""
    xc = min(max(x, -3 * d), 3 * d)
    return 100.0 / (1.0 + math.exp(-math.log(7 / 3) / d * xc))


# ---- A：手算 x／den／u ----

def test_hand_x_den_u_1102_short(census):
    """1102 於 2020-02-10（最新可用月 2020-01）：單月 YoY＝1e8／1e3、加速度＝兩組三月 YoY 相減；u＝x／(3d)。"""
    res, _ = census
    det = res["_details"]
    Rr = det["R"]
    run = det["run_of_row"][_row(det, "1102", "2020-02-10", "short")]
    d_yoy = 19.27401351928711                                   # calibrated.py：twse stock revenue_yoy short
    x = (1e8 / 1e3 - 1.0) * 100.0
    assert Rr["pres"]["revenue_yoy"][run] == 1
    assert Rr["x"]["revenue_yoy"][run] == x and Rr["den1"]["revenue_yoy"][run] == 1e3
    assert math.isclose(Rr["u"]["revenue_yoy"][run], x / (3 * d_yoy), rel_tol=1e-15)
    assert Rr["d"]["revenue_yoy"][run] == d_yoy and Rr["c"]["revenue_yoy"][run] == 0.0
    a = ((1e8 + 1e3 + 1e3) / (1e3 + 1e8 + 1e8) - 1.0) * 100.0     # 2020-01、2019-12、2019-11 ÷ 2019-01、2018-12、2018-11
    b = ((1e3 * 3) / (1e8 * 3) - 1.0) * 100.0                    # 2019-10～08 ÷ 2018-10～08
    d_acc = 13.739003499348959
    assert Rr["x"]["revenue_accel"][run] == a - b
    assert Rr["den1"]["revenue_accel"][run] == 1e3 + 1e8 + 1e8 and Rr["den2"]["revenue_accel"][run] == 3e8
    assert math.isclose(Rr["u"]["revenue_accel"][run], (a - b) / (3 * d_acc), rel_tol=1e-15)
    assert Rr["pres"]["revenue_yoy_vs_industry"][run] == -1       # 非中期：族 C 不適用


def test_denominator_zero_row(census):
    """1102 於 2020-03-10（最新 2020-02）：去年同月 2019-02＝0 → denominator_zero、基期記 0。"""
    res, _ = census
    det = res["_details"]
    Rr = det["R"]
    run = det["run_of_row"][_row(det, "1102", "2020-03-10", "short")]
    assert Rr["pres"]["revenue_yoy"][run] == 0
    assert Rr["reasons"][Rr["reason"]["revenue_yoy"][run]] == "denominator_zero（last-year 1M sum=0）"
    assert Rr["den1"]["revenue_yoy"][run] == 0.0


def test_population_matches_raw_sql(census, world):
    res, _ = census
    con = sqlite3.connect(world / "scores.db")
    n, pool = con.execute("SELECT COUNT(*), SUM(in_rank_pool=1) FROM scores WHERE scope='stock' AND date BETWEEN ? AND ?", Y2020).fetchone()
    by = dict(((m, h), c) for m, h, c in con.execute(
        "SELECT market, horizon, COUNT(*) FROM scores WHERE scope='stock' AND date BETWEEN ? AND ? GROUP BY 1, 2", Y2020))
    con.close()
    assert res["population"]["rows"] == n > 1000 and res["population"]["rows_in_pool"] == pool
    assert res["parity_a"]["rows"] == n and res["parity_a"]["max_abs_diff"] <= RB.PARITY_TOL
    assert res["parity_b"]["rows"] == n                         # 普查：全部列都走真實重播
    for g in res["a_groups"]:
        assert g["rows"] == by[(g["market"], g["horizon"])]
        assert g["present"] + sum(g["missing"].values()) == g["rows_applicable"] == g["rows"]


def test_a_distribution_equals_row_expansion(census):
    """分位數、門檻計數以逐列展開（np.repeat）另算，與報告相同；|u|>1 列數＝clip 旗標列數。"""
    res, _ = census
    det = res["_details"]
    Rr, w = det["R"], det["w_run"]
    for g in res["a_groups"]:
        s = g["sub"]
        m = (Rr["mk"] == RB.MK_CODE[g["market"]]) & (Rr["h"] == RB.H_CODE[g["horizon"]]) & (Rr["pres"][s] == 1)
        u = np.repeat(Rr["u"][s][m], w[m])
        assert u.size == g["present"]
        if not u.size:
            continue
        for q, k in ((0.5, "p50"), (0.9, "p90"), (0.99, "p99"), (0.999, "p99.9"), (1.0, "max")):
            assert math.isclose(g["abs_u"][k], float(np.quantile(np.abs(u), q)), rel_tol=1e-12)
        for t in RB.U_THRESHOLDS:
            assert g["abs_u_over"][f"{t:g}"]["rows"] == int((np.abs(u) > t).sum())
        assert g["abs_u_over"]["1"]["rows"] == g["clipped_flag_rows"]
    ext = sum(g["abs_u_over"]["10"]["rows"] for g in res["a_groups"])
    assert ext > 0 and any(g["abs_u_over"]["100"]["rows"] for g in res["a_groups"])


def test_base_ratio_equals_row_expansion(census):
    """每組 |u|>10 列的基期與「÷ 自身中位數」比值：以逐列展開、每檔 np.median 另算，全部分位數與無定義列數都相同。
    手算錨點：twse 短線 revenue_yoy 的極端列基期全為 1,000 元；1103 的中位數 1e8 → 比值 1e-5；1102 的中位數 1,000 → 比值 1。"""
    res, _ = census
    det = res["_details"]
    Rr, w = det["R"], det["w_run"]
    n_checked = 0
    for g in res["a_groups"]:
        s = g["sub"]
        gm = (Rr["mk"] == RB.MK_CODE[g["market"]]) & (Rr["h"] == RB.H_CODE[g["horizon"]]) & (Rr["pres"][s] >= 0)
        ext = gm & (Rr["pres"][s] == 1) & (np.abs(Rr["u"][s]) > RB.EXTREME_U)
        for tag, col in (("den", "den1"), ("den_prev", "den2")):
            if tag not in g["base_revenue_yuan"]:
                continue
            b = g["base_revenue_yuan"][tag]
            dv = Rr[col][s]
            e = ext & ~np.isnan(dv)
            assert b["extreme_rows"] == int(w[e].sum())
            ratios, undef = [], 0
            for r in np.flatnonzero(e).tolist():
                mm = gm & ~np.isnan(dv) & (Rr["sid"] == Rr["sid"][r])
                med = float(np.median(np.repeat(dv[mm], w[mm])))
                if med <= 0:
                    undef += int(w[r])
                else:
                    ratios += [dv[r] / med] * int(w[r])
            assert b["ratio_undefined_rows"] == undef
            for k, q in zip(b["ratio_to_own_median"], RB.DEN_QS):
                want = float(np.quantile(ratios, q)) if ratios else None
                assert (b["ratio_to_own_median"][k] is None) == (want is None)
                if want is not None:
                    assert math.isclose(b["ratio_to_own_median"][k], want, rel_tol=1e-12)
            n_checked += bool(ratios)
    assert n_checked >= 2
    g = next(x for x in res["a_groups"] if (x["market"], x["horizon"], x["sub"]) == ("twse", "short", "revenue_yoy"))
    b = g["base_revenue_yuan"]["den"]
    assert b["extreme"]["min"] == b["extreme"]["max"] == 1e3
    assert b["ratio_to_own_median"]["min"] == 1e3 / 1e8 and b["ratio_to_own_median"]["max"] == 1.0


def test_summarize_c_edges_hand():
    """翻轉以 50 為界（≥50 為陽）、[45, 55] 兩端含、變未知另列、未受影響的 run 不計；權重＝各 run 的列數。"""
    orig = np.array([50.0, 49.0, 44.9, 55.0, 60.0, 70.0, 52.0])
    cf = np.array([49.9, 50.0, 45.0, 55.1, np.nan, 71.0, 53.0])
    rw_o = np.array([0, 0, 1, 0, 0, 0, 0], dtype=np.int8)
    rw_c = np.array([1, 0, 1, 1, 1, 0, -1], dtype=np.int8)            # 最後一筆未受影響
    n = orig.size
    key = ("revenue_yoy", 3.0)
    R = {"mk": np.zeros(n, np.int8), "h": np.zeros(n, np.int8), "score": orig, "rw": rw_o,
         "cf_score": {k: np.full(n, np.nan) for k in [(sc, K) for sc in RB.SCENARIOS for K in RB.CF_K]},
         "cf_rw": {k: np.full(n, -1, np.int8) for k in [(sc, K) for sc in RB.SCENARIOS for K in RB.CF_K]}}
    R["cf_score"][key], R["cf_rw"][key] = cf, rw_c
    w = np.array([1, 2, 3, 4, 5, 6, 7], dtype=np.int64)
    c = next(x for x in RB.summarize_c(R, w) if (x["market"], x["horizon"], x["scenario"], x["K"]) == ("twse", "short", *key))
    assert c["group_rows"] == 28 and c["affected_rows"] == 21
    assert (c["flip_yang_to_yin"], c["flip_yin_to_yang"], c["flip_rows"]) == (1, 2, 3)
    assert c["became_unknown_rows"] == 5 and c["orig_unknown_rows"] == 0
    assert c["band_enter_rows"] == 3                                  # 44.9 → 45.0（下端含）
    assert c["band_exit_rows"] == 4                                   # 55.0 → 55.1（上端含，原在帶內）
    assert c["orig_in_band_rows"] == 1 + 2 + 4
    assert c["reweighted_0_to_1_rows"] == 1 + 4 + 5                   # 含變未知的那一筆
    assert c["delta"]["max"] == 1.0 and math.isclose(c["delta"]["min"], -0.1, abs_tol=1e-12)


def test_wquantiles_equals_numpy_repeat():
    rng = np.random.default_rng(1)
    for _ in range(50):
        n = int(rng.integers(1, 30))
        v = rng.normal(size=n) * 10 ** rng.integers(0, 6)
        w = rng.integers(0, 5, size=n)
        if w.sum() == 0:
            w[0] = 1
        qs = (0.0, 0.01, 0.37, 0.5, 0.9, 0.999, 1.0)
        got = RB.wquantiles(v, w, qs)
        want = np.quantile(np.repeat(v, w), qs)
        for a, b in zip(got, want):
            assert math.isclose(a, b, rel_tol=1e-12, abs_tol=1e-12)
    assert RB.wquantiles(np.array([]), np.array([], dtype=int), (0.5,)) == [None]


# ---- C：反事實 ----

V_PREV = 0.998003992                                           # _mid_inputs 前組基期（每月）


def _mid_inputs(med_offset: float = 10.0) -> tuple[STK.StockInputs, float, float]:
    """中期、最新月 2019-12：近組基期 3 元（YoY 巨大）、前組基期略小（加速度約 −20 pp）；族 C 的產業中位數＝YoY − `med_offset`；
    族 B 無季報 → 缺；2019-06 為 150（最新月非 12 月新高，不觸發中期下限）。回 (inputs, 近組 YoY, 前組 YoY)（手算）。"""
    rev = []
    for k in range(24):                                        # 2018-01 … 2019-12
        y, m = 2018 + k // 12, k % 12 + 1
        if y == 2019:
            v = 150.0 if m == 6 else 100.0
        else:
            v = 1.0 if m >= 10 else (V_PREV if m >= 7 else 100.0)
        rev.append((f"{y:04d}-{m:02d}", v))
    yoy = ((100.0 + 100.0 + 100.0) / (1.0 + 1.0 + 1.0) - 1.0) * 100.0
    prev = ((100.0 + 100.0 + 100.0) / (V_PREV + V_PREV + V_PREV) - 1.0) * 100.0
    si = STK.StockInputs(market="twse", stock_id="9999", tpe_date="2020-01-20", monthly_revenue=rev,
                         industry_median_3m_yoy=yoy - med_offset, industry_revenue_n=5, fundamentals=None)
    return si, yoy, prev


def test_counterfactual_equals_hand_reweighting():
    """子指標改缺後的初爻＝其餘子指標按權重重配（族 A 0.5、族 C 0.2、族 B 缺）；手算 S 與權重，不經聚合碼。"""
    ps = build_params("twse")
    si, yoy, prev = _mid_inputs()
    with RB.instrumented() as (cap, sw):
        det = RB.line1_detail(si, ps, "mid", cap)
        cf = RB.counterfactuals(det, si, ps, "mid", sw)
    subs = det["subs"]
    assert all(subs[s]["pres"] for s in RB.SUBS)
    d = {s: subs[s]["d"] for s in RB.SUBS}
    x_vs = yoy - (yoy - 10.0)
    assert subs["revenue_yoy"]["x"] == yoy and subs["revenue_accel"]["x"] == yoy - prev
    assert subs["revenue_yoy_vs_industry"]["x"] == x_vs and subs["revenue_yoy"]["den"] == (3.0, None)
    assert subs["revenue_accel"]["den"] == (3.0, V_PREV + V_PREV + V_PREV)
    assert math.isclose(yoy - prev, -20.0, abs_tol=1e-3)
    sy, sa, sc = _S(yoy, d["revenue_yoy"]), _S(yoy - prev, d["revenue_accel"]), _S(x_vs, d["revenue_yoy_vs_industry"])
    assert abs(subs["revenue_yoy"]["u"]) > 100 and abs(subs["revenue_accel"]["u"]) < 1 and abs(subs["revenue_yoy_vs_industry"]["u"]) < 1
    assert math.isclose(det["score"], (0.5 * (sy + sa) / 2 + 0.2 * sc) / 0.7, rel_tol=1e-12)
    want = (0.5 * sa + 0.2 * sc) / 0.7                          # 拿掉 revenue_yoy：族 A 只剩加速度
    for K in RB.CF_K:
        for sc_name in ("revenue_yoy", "joint"):
            assert math.isclose(cf[(sc_name, K)][0], want, rel_tol=1e-12) and cf[(sc_name, K)][1] == 1
    assert set(cf) == {(n, K) for n in ("revenue_yoy", "joint") for K in RB.CF_K}   # 其餘子指標未超過 K → 不受影響
    # 族 C 的唯一子指標超過 K：拿掉 → 族 C 缺 → 爻＝族 A（0.5／0.7 ≥ 0.5 仍已知）
    si2, _, _ = _mid_inputs(med_offset=1e5)
    with RB.instrumented() as (cap, sw):
        det2 = RB.line1_detail(si2, ps, "mid", cap)
        cf2 = RB.counterfactuals(det2, si2, ps, "mid", sw)
    assert math.isclose(cf2[("revenue_yoy_vs_industry", 100.0)][0], (sy + sa) / 2, rel_tol=1e-12)
    assert math.isclose(cf2[("joint", 100.0)][0], sa, rel_tol=1e-12)              # 兩個都拿掉：族 A＝加速度、族 C 缺
    # 單一子指標的短線：兩個族 A 子指標都拿掉 → 族 A 缺 → 覆蓋 0 → 未知
    rev3 = [(f"{2018 + k // 12:04d}-{k % 12 + 1:02d}", (1.0 if k % 12 >= 9 else 2.0) if k < 12 else 1e6) for k in range(24)]
    si3 = STK.StockInputs(market="twse", stock_id="9999", tpe_date="2020-01-20", monthly_revenue=rev3)
    with RB.instrumented() as (cap, sw):
        det3 = RB.line1_detail(si3, ps, "short", cap)
        cf3 = RB.counterfactuals(det3, si3, ps, "short", sw)
    assert abs(det3["subs"]["revenue_yoy"]["u"]) > 100 and abs(det3["subs"]["revenue_accel"]["u"]) > 100
    assert det3["score"] is not None and cf3[("joint", 3.0)] == (None, 1)
    assert cf3[("revenue_yoy", 3.0)][0] == cf3[("revenue_accel", 3.0)][0] == det3["score"]   # 兩者皆在上端點：拿掉任一不變
    # 反事實結束後開關復原：原函式歸位、再算一次原分數不變
    assert STK.sub_result.__name__ == "sub_result" and STK.S_clip.__name__ == "S_clip"
    with RB.instrumented() as (cap, _):
        assert RB.line1_detail(si, ps, "mid", cap)["score"] == det["score"]


def test_counterfactual_only_when_over_K():
    ps = build_params("twse")
    rev = [(f"{2018 + k // 12:04d}-{k % 12 + 1:02d}", 100.0 if k < 12 else 110.0) for k in range(24)]
    si = STK.StockInputs(market="twse", stock_id="9999", tpe_date="2020-01-20", monthly_revenue=rev)
    with RB.instrumented() as (cap, sw):
        det = RB.line1_detail(si, ps, "short", cap)
        assert RB.counterfactuals(det, si, ps, "short", sw) == {}   # YoY 10%、加速度 0：|u| 都 < 3


def test_flip_and_band_counts_match_direct(census):
    """C 的翻轉／進出帶／變未知／rw 0→1 以逐列（原分數取 db 的 line_1）直接比對另算；合成資料設計成兩類都有。"""
    res, _ = census
    det = res["_details"]
    rows, Rr, ror = det["rows"], det["R"], det["run_of_row"]
    lo, hi = RB.BAND
    tot_flip = tot_enter = 0
    for c in res["c_groups"]:
        k = (c["scenario"], c["K"])
        want = dict(aff=0, flip=0, dn=0, up=0, unk=0, enter=0, exit=0, rw=0)
        for i in range(rows["n"]):
            if (RB.MARKETS[rows["mk"][i]], RB.HORIZONS[rows["h"][i]]) != (c["market"], c["horizon"]):
                continue
            r = ror[i]
            if Rr["cf_rw"][k][r] < 0:
                continue
            want["aff"] += 1
            want["rw"] += rows["rw"][i] == 0 and Rr["cf_rw"][k][r] == 1       # 含變未知的列
            o, n = float(rows["l1"][i]), float(Rr["cf_score"][k][r])
            if math.isnan(n):
                want["unk"] += not math.isnan(o)
                continue
            want["dn"] += o >= 50 > n
            want["up"] += o < 50 <= n
            want["enter"] += (not lo <= o <= hi) and lo <= n <= hi
            want["exit"] += lo <= o <= hi and not lo <= n <= hi
        want["flip"] = want["dn"] + want["up"]
        got = dict(aff=c["affected_rows"], flip=c["flip_rows"], dn=c["flip_yang_to_yin"], up=c["flip_yin_to_yang"],
                   unk=c["became_unknown_rows"], enter=c["band_enter_rows"], exit=c["band_exit_rows"], rw=c["reweighted_0_to_1_rows"])
        assert got == want, (c["market"], c["horizon"], k)
        tot_flip += c["flip_rows"]
        tot_enter += c["band_enter_rows"]
    assert tot_flip > 0 and tot_enter > 0


def test_counterfactual_flip_hand_6488(census):
    """6488（tpex）最新月 2019-12：YoY 巨大（S＝92.70）、加速度 −25 pp；拿掉 YoY → 初爻＝S(−25)，由陽翻陰。"""
    res, _ = census
    det = res["_details"]
    Rr = det["R"]
    i = _row(det, "6488", "2020-01-10", "swing")
    r = det["run_of_row"][i]
    d = 18.098927815755207
    sa = _S(Rr["x"]["revenue_accel"][r], d)
    assert math.isclose(Rr["x"]["revenue_accel"][r], -25.0, abs_tol=1e-6)
    assert math.isclose(det["rows"]["l1"][i], (S_clip(1e9, 0, 1).native + sa) / 2, rel_tol=1e-12)
    assert math.isclose(Rr["cf_score"][("revenue_yoy", 100.0)][r], sa, rel_tol=1e-12) and sa < 50 < det["rows"]["l1"][i]


# ---- B：分層抽樣 ----

def test_stratified_weights_restore_population(world, tmp_path):
    res = _run(world, tmp_path / "r.json", per_group=4, keep_details=True)
    det = res["_details"]
    n_sampled_groups = 0
    for st in res["b_strata"]:
        pop = sum(st[k]["population"] for k in RB.STRATA)
        assert sum(st[k]["sampled"] for k in RB.STRATA) == min(4, pop)
        for k in RB.STRATA:
            v = st[k]
            if v["sampled"]:
                assert math.isclose(v["weight"] * v["sampled"], v["population"], rel_tol=1e-12)
            else:
                assert v["weight"] is None
            if v["population"] and v["sampled"] < v["population"]:
                n_sampled_groups += 1
        mine = [s for s in det["samples"] if (s["market"], s["horizon"]) == (st["market"], st["horizon"])]
        est_ext = sum(st[s["stratum"]]["weight"] for s in mine if s["stratum"] == "extreme")
        assert math.isclose(est_ext, st["extreme"]["population"], abs_tol=1e-9)
        Rr = det["R"]
        for s in mine:
            is_ext = any(Rr["pres"][x][s["run"]] == 1 and abs(Rr["u"][x][s["run"]]) > RB.EXTREME_U for x in RB.SUBS)
            assert is_ext == (s["stratum"] == "extreme")
    assert n_sampled_groups >= 2 and res["parity_b"]["rows"] == len(det["samples"])
    assert any(st["extreme"]["population"] for st in res["b_strata"])


def test_allocate_hand_values():
    al = lambda x, r, t: RB.allocate({"extreme": x, "rest": r}, t)  # noqa: E731
    assert al(100, 100, 10) == {"extreme": 5, "rest": 5}
    assert al(2, 100, 10) == {"extreme": 2, "rest": 8}
    assert al(100, 3, 10) == {"extreme": 7, "rest": 3}          # rest 不足 → 回補 extreme
    assert al(3, 4, 10) == {"extreme": 3, "rest": 4}
    with pytest.raises(RB.RevBaseError):
        al(1, 1, 1)


def test_same_seed_same_samples(world, tmp_path):
    a = _run(world, tmp_path / "a.json", per_group=4, keep_details=True)["_details"]["samples"]
    b = _run(world, tmp_path / "b.json", per_group=4, keep_details=True)["_details"]["samples"]
    c = _run(world, tmp_path / "c.json", per_group=4, seed=7, keep_details=True)["_details"]["samples"]
    key = lambda xs: [(s["sid"], s["date"], s["horizon"]) for s in xs]  # noqa: E731
    assert key(a) == key(b) and key(a) != key(c)


# ---- parity 守門：紅且紅得對 ----

def _tamper_db(world, tmp_path, sql, args=()) -> Path:
    c = _copy(world, tmp_path)
    con = sqlite3.connect(c / "scores.db")
    con.execute(sql, args)
    con.commit()
    con.close()
    return c


def test_db_score_tamper_hits_a_parity(world, tmp_path):
    c = _tamper_db(world, tmp_path, "UPDATE scores SET line_1 = line_1 + 0.5 WHERE stock_id='1102' AND date='2020-02-10' AND horizon='short'")
    with pytest.raises(RB.ParityError, match=r"A 全母體 parity 不符 1／") as e:
        _run(c, tmp_path / "r.json", db=c / "scores.db")
    assert "1102 2020-02-10" in str(e.value)


def _wrap_detail(monkeypatch, fn):
    orig = RB.line1_detail

    def wrapped(si, ps, h, cap):
        det = orig(si, ps, h, cap)
        if si.stock_id == "1102":
            fn(det)
        return det
    monkeypatch.setattr(RB, "line1_detail", wrapped)


def test_x_tamper_hits_b_parity(world, tmp_path, monkeypatch):
    def f(det):
        r = det["subs"]["revenue_yoy"]
        if r["pres"]:
            r["x"] += r["x"] * 1e-12 + 1e-6
    _wrap_detail(monkeypatch, f)
    with pytest.raises(RB.ParityError, match=r"B 抽樣 parity") as e:
        _run(world, tmp_path / "r.json")
    assert "revenue_yoy x 真實" in str(e.value) and "1102" in str(e.value)


def test_reason_tamper_hits_b_parity(world, tmp_path, monkeypatch):
    def f(det):
        r = det["subs"]["revenue_yoy"]
        if not r["pres"]:
            r["reason"] = "missing（竄改）"
    _wrap_detail(monkeypatch, f)
    with pytest.raises(RB.ParityError, match=r"revenue_yoy 真實計分缺值 .* ≠ A missing（竄改）"):
        _run(world, tmp_path / "r.json")


def test_present_tamper_hits_b_parity(world, tmp_path, monkeypatch):
    def f(det):
        r = det["subs"]["revenue_yoy"]
        if r["pres"]:
            r.update(pres=False, reason="missing（竄改）")
    _wrap_detail(monkeypatch, f)
    with pytest.raises(RB.ParityError, match=r"revenue_yoy 真實計分在場.*≠ A 缺值"):
        _run(world, tmp_path / "r.json")


def test_d_tamper_hits_b_parity(world, tmp_path, monkeypatch):
    def f(det):
        r = det["subs"]["revenue_accel"]
        if r["pres"]:
            r["d"] *= 2
    _wrap_detail(monkeypatch, f)
    with pytest.raises(RB.ParityError, match=r"revenue_accel d 真實"):
        _run(world, tmp_path / "r.json")


def _wrap_score_stock(monkeypatch, fn):
    orig = STK.score_stock

    def wrapped(inp, ps, h):
        ss = orig(inp, ps, h)
        if inp.stock_id == "1102":
            ss = dataclasses.replace(ss, lines={**ss.lines, "1": fn(ss.lines["1"])})
        return ss
    monkeypatch.setattr(STK, "score_stock", wrapped)


def test_b_line1_score_mismatch(world, tmp_path, monkeypatch):
    _wrap_score_stock(monkeypatch, lambda lr: dataclasses.replace(lr, score=None if lr.score is None else lr.score + 1e-6))
    with pytest.raises(RB.ParityError, match=r"B 抽樣 parity.*\n.*初爻 \|差\|"):
        _run(world, tmp_path / "r.json")


def test_b_line1_unknown_vs_known(world, tmp_path, monkeypatch):
    _wrap_score_stock(monkeypatch, lambda lr: dataclasses.replace(lr, score=None))
    with pytest.raises(RB.ParityError, match=r"1102 .*：初爻 db=.* 重算=None"):
        _run(world, tmp_path / "r.json")


def test_b_reweighted_mismatch(world, tmp_path, monkeypatch):
    _wrap_score_stock(monkeypatch, lambda lr: dataclasses.replace(lr, reweighted=not lr.reweighted))
    with pytest.raises(RB.ParityError, match=r"reweighted/unknown 重算"):
        _run(world, tmp_path / "r.json")


def test_b_sub_called_twice(world, tmp_path, monkeypatch):
    orig = STK.score_stock

    def twice(inp, ps, h):
        STK.line1_operations(inp, ps, h)
        return orig(inp, ps, h)
    monkeypatch.setattr(STK, "score_stock", twice)
    with pytest.raises(RB.ParityError, match=r"sub_result 呼叫 2 次"):
        _run(world, tmp_path / "r.json")


def test_b_not_scorable(world, tmp_path, monkeypatch):
    monkeypatch.setattr(RS.WindowCache, "stock_ids_today", lambda self: [])
    with pytest.raises(RB.ParityError, match=r"不在計分名單或市場不符"):
        _run(world, tmp_path / "r.json")


def test_b_sample_day_not_reached(world):
    """抽樣日不在原料交易日軸 → 走不到 → 不符（不得靜默略過）。"""
    src = RB.RIO.ReplaySource(world, DV, window=30)
    try:
        bridge = src.load_fundamentals(src.trading_dates())
        s = {"market": "twse", "horizon": "short", "stratum": "rest", "row": 0, "run": 0, "sid": "1102",
             "date": "2020-01-21", "db": 50.0, "rw": 0, "unk": 0}
        with pytest.raises(RB.ParityError, match=r"只走到 0 列"):
            RB.run_b(src, bridge, [s], {}, {m: build_params(m) for m in RB.MARKETS}, 30, quiet=True)
    finally:
        src.close()


# ---- 其他守門 ----

def test_params_sha_tamper_aborts(world, tmp_path):
    c = _tamper_db(world, tmp_path, "UPDATE replay_meta SET params_sha='000000000000'")
    with pytest.raises(Exception, match="params_sha"):
        _run(c, tmp_path / "r.json", db=c / "scores.db")


def test_registry_model_version_mismatch_aborts(world, tmp_path):
    reg = json.loads(RB.SS.REGISTRY.read_text(encoding="utf-8"))
    reg["markets"]["twse"]["model_version"] = "p2-score-engine-1.000000000000"
    p = tmp_path / "reg.json"
    p.write_text(json.dumps(reg), encoding="utf-8")
    with pytest.raises(RB.SS.StatsError, match="model_version"):
        _run(world, tmp_path / "r.json", registry=p)


def test_fingerprint_rebuild_mismatch_aborts(world, tmp_path, monkeypatch):
    orig = RB.build_params_payload
    monkeypatch.setattr(RB, "build_params_payload", lambda mv, w, adv, **k: orig(mv, w + 1, adv, **k))
    with pytest.raises(RB.RevBaseError, match="重建的參數指紋"):
        _run(world, tmp_path / "r.json")


def test_fundamentals_disabled_aborts(world, tmp_path, monkeypatch):
    import export_dataset as ED
    orig = ED.check_params
    monkeypatch.setattr(ED, "check_params", lambda store, dv: (lambda r: (r[0], {**r[1], "fundamentals": False}))(orig(store, dv)))
    with pytest.raises(RB.RevBaseError, match="未啟用基本面"):
        _run(world, tmp_path / "r.json")


def test_empty_segment_aborts(world, tmp_path):
    with pytest.raises(RB.RevBaseError, match="沒有任何個股列"):
        RB.run(world / "scores.db", tmp_path / "r.json", cache_dir=world, start="2019-01-01", end="2019-12-31", quiet=True)


def test_db_date_off_calendar_aborts(world, tmp_path):
    c = _tamper_db(world, tmp_path, "UPDATE scores SET date='2020-01-21' WHERE stock_id='1102' AND date='2020-01-20' AND horizon='mid'")
    with pytest.raises(RB.RevBaseError, match="db 日期不在原料交易日軸"):
        _run(c, tmp_path / "r.json", db=c / "scores.db")


def test_db_market_vs_pool_listed_aborts(world, tmp_path):
    con = sqlite3.connect(world / "scores.db")
    vid = con.execute("SELECT DISTINCT version_id FROM scores WHERE market='tpex'").fetchone()[0]
    con.close()
    c = _tamper_db(world, tmp_path, "UPDATE scores SET market='tpex', version_id=? WHERE stock_id='1102' AND date='2020-01-20' "
                   "AND horizon='mid'", (vid,))
    with pytest.raises(RB.RevBaseError, match=r"1102 2020-01-20：pool.listed=twse ≠ db 列市場 tpex"):
        _run(c, tmp_path / "r.json", db=c / "scores.db")


def test_base_sum_mismatch_aborts(world, tmp_path, monkeypatch):
    orig = RB.base_sums
    monkeypatch.setattr(RB, "base_sums", lambda rev, latest, off, n: (lambda r: None if r is None else (r[0] * 1.5, r[1]))(orig(rev, latest, off, n)))
    with pytest.raises(RB.RevBaseError, match=r"推得的 YoY ≠ revenue_yoy_3m 回傳"):
        _run(world, tmp_path / "r.json")


def test_check_base_units():
    from iching.score.transform import Missing
    with pytest.raises(RB.RevBaseError, match="不是 0"):
        RB._check_base("t", (1.0, 2.0), Missing("denominator_zero", "x"))
    with pytest.raises(RB.RevBaseError, match="算得出基期"):
        RB._check_base("t", (1.0, 2.0), Missing("missing", "x"))
    RB._check_base("t", (1.0, 0.0), Missing("denominator_zero", "x"))
    RB._check_base("t", None, Missing("missing", "x"))
    RB._check_base("t", (3.0, 2.0), (3.0 / 2.0 - 1.0) * 100.0)


def test_x_relation_guard(world, tmp_path, monkeypatch):
    """`ind_revenue_yoy` 若送進 S_clip 的不是 revenue_yoy_3m 的值 → 中止（A 的 x 必須就是那支函式的輸出）。"""
    def ind(rev, latest, months, d):
        x = STK.revenue_yoy_3m(rev, latest, 0, months)
        return x if isinstance(x, RB.Missing) else STK.S_clip(x + 1.0, 0.0, d)
    monkeypatch.setattr(STK, "ind_revenue_yoy", ind)
    with pytest.raises(RB.RevBaseError, match=r"revenue_yoy：由 revenue_yoy_3m 推得的 x"):
        _run(world, tmp_path / "r.json")


def test_accel_relation_guard(world, tmp_path, monkeypatch):
    """`ind_revenue_accel` 送進 S_clip 的不是兩組 revenue_yoy_3m 相減 → 中止。"""
    def ind(rev, latest, months, d):
        a, b = STK.revenue_yoy_3m(rev, latest, 0, months), STK.revenue_yoy_3m(rev, latest, months, months)
        if isinstance(a, RB.Missing):
            return a
        if isinstance(b, RB.Missing):
            return b
        return STK.S_clip(a - b + 1.0, 0.0, d)
    monkeypatch.setattr(STK, "ind_revenue_accel", ind)
    with pytest.raises(RB.RevBaseError, match=r"revenue_accel：兩組 YoY 相減"):
        _run(world, tmp_path / "r.json")


def test_b_not_applicable_sub_appears(world, tmp_path, monkeypatch):
    """A 判族 C 不適用（非中期）而真實計分卻送出 revenue_yoy_vs_industry → B 不符。"""
    orig = STK.score_stock

    def extra(inp, ps, h):
        if h != "mid":
            STK.sub_result("revenue_yoy_vs_industry", RB.Missing("missing", "x"))
        return orig(inp, ps, h)
    monkeypatch.setattr(STK, "score_stock", extra)
    with pytest.raises(RB.ParityError, match=r"revenue_yoy_vs_industry A 判不適用、真實計分卻出現"):
        _run(world, tmp_path / "r.json")


def test_capture_must_be_direct(world, tmp_path, monkeypatch):
    orig = STK.ind_revenue_accel

    def ind(rev, latest, months, d):
        out = orig(rev, latest, months, d)
        return out if isinstance(out, RB.Missing) else dataclasses.replace(out, clipped=not out.clipped)
    monkeypatch.setattr(STK, "ind_revenue_accel", ind)
    with pytest.raises(RB.RevBaseError, match=r"revenue_accel：攔截不到直接的 S_clip"):
        _run(world, tmp_path / "r.json")


def test_capture_x_must_equal_subresult_x(world, tmp_path, monkeypatch):
    orig = STK.ind_revenue_yoy

    def ind(rev, latest, months, d):
        out = orig(rev, latest, months, d)
        if not isinstance(out, RB.Missing):
            object.__setattr__(out, "x", out.x + 1.0)
        return out
    monkeypatch.setattr(STK, "ind_revenue_yoy", ind)
    with pytest.raises(RB.RevBaseError, match=r"S_clip 收到的 x .* ≠ SubResult.x"):
        _run(world, tmp_path / "r.json")


def test_family_guard(world, tmp_path, monkeypatch):
    monkeypatch.setitem(RB.SUB_FAMILY, "revenue_accel", "B")
    with pytest.raises(RB.RevBaseError, match=r"revenue_accel 出現在族 A（預期 B）"):
        _run(world, tmp_path / "r.json")


def test_expected_subs_guard(world, tmp_path, monkeypatch):
    orig = RB._sub_windows
    monkeypatch.setattr(RB, "_sub_windows", lambda ps, h: {**orig(ps, h), "revenue_foo": 3})
    with pytest.raises(RB.RevBaseError, match=r"≠ 預期"):
        _run(world, tmp_path / "r.json")


def test_per_group_min(world, tmp_path):
    with pytest.raises(RB.RevBaseError, match="至少"):
        _run(world, tmp_path / "r.json", per_group=1)


# ---- D：使用處 ----

def test_usage_scan_current_tree():
    uses = RB.usage_scan()
    fns = {(u["file"], u["function"]) for u in uses}
    assert fns == set(RB.KNOWN_USES)
    line_fns = {fn for f, fn in fns if f == "score/stock.py" and fn.startswith("line")}
    assert line_fns == {"line1_operations"}                   # 只有初爻用到營收子指標


def test_usage_scan_flags_new_use(tmp_path, world):
    src = tmp_path / "iching"
    shutil.copytree(ROOT / "src" / "iching", src, ignore=shutil.ignore_patterns("__pycache__"))
    p = src / "score" / "stock.py"
    p.write_text(p.read_text(encoding="utf-8") + "\n\ndef line3_extra(rev, latest):\n    return revenue_yoy_3m(rev, latest)\n",
                 encoding="utf-8")
    with pytest.raises(RB.RevBaseError, match=r"未涵蓋的使用處.*score/stock.py:line3_extra"):
        RB.usage_scan(src)
    with pytest.raises(RB.RevBaseError, match="line3_extra"):
        _run(world, tmp_path / "r.json", src_root=src)


# ---- 產出、唯讀、CLI ----

def test_report_files_and_readonly(world, tmp_path):
    h = hashlib.sha256((world / "scores.db").read_bytes()).hexdigest()
    res = _run(world, tmp_path / "out" / "rep.json")
    assert hashlib.sha256((world / "scores.db").read_bytes()).hexdigest() == h     # 反事實只在記憶體內
    saved = json.loads((tmp_path / "out" / "rep.json").read_text(encoding="utf-8"))
    assert "_details" not in saved and saved["seed"] == RB.DEFAULT_SEED and saved["per_group"] == 100000
    assert saved["elapsed_s"] >= 0 and saved["rss_peak_mib"] > 0
    txt = (tmp_path / "out" / "rep.txt").read_text(encoding="utf-8")
    assert txt.startswith("營收子指標基期影響面量測") and "A 全母體 parity" in txt and "== C 反事實" in txt
    assert "== D 使用處" in txt and res["population"]["rows"] > 0
    for bad in ("建議", "門檻規則", "可能是因為", "推測"):
        assert bad not in txt


def test_cli_defaults_and_hardcoded_segment(world, tmp_path, capsys):
    """CLI 不開日期：寫死的 2021～2024 樣本段在 2020 年合成資料上 0 列 → rc=2（訊息指向空樣本段）。"""
    rc = RB.main(["--db", str(world / "scores.db"), "--cache-dir", str(world), "--out", str(tmp_path / "r.json"), "--quiet"])
    assert rc == 2 and "沒有任何個股列" in capsys.readouterr().err
    assert (RB.SAMPLE_START, RB.SAMPLE_END) == ("2021-01-01", "2024-12-31")
    with pytest.raises(SystemExit):
        RB.main(["--db", "x", "--out", "y", "--start", "2020-01-01"])


def test_cli_rc2_on_parity(world, tmp_path, monkeypatch, capsys):
    c = _tamper_db(world, tmp_path, "UPDATE scores SET line_1 = line_1 + 0.5 WHERE stock_id='1102' AND date='2020-02-10' AND horizon='short'")
    monkeypatch.setattr(RB, "SAMPLE_START", Y2020[0])
    monkeypatch.setattr(RB, "SAMPLE_END", Y2020[1])
    rc = RB.main(["--db", str(c / "scores.db"), "--cache-dir", str(c), "--out", str(tmp_path / "r.json"), "--quiet"])
    assert rc == 2 and "ParityError" in capsys.readouterr().err and not (tmp_path / "r.json").exists()
    rc = RB.main(["--db", str(world / "scores.db"), "--cache-dir", str(world), "--out", str(tmp_path / "ok.json"), "--quiet"])
    assert rc == 0 and (tmp_path / "ok.json").exists() and (tmp_path / "ok.txt").exists()


# ---- hetzner_revbase.sh：真的跑一遍（假 python3＋本機 bare repo） ----

STUB_PY = r'''#!@@PY@@
import os, pathlib, sys
a = sys.argv[1:]
log = os.environ.get("STUB_LOG")
if log:
    with open(log, "a", encoding="utf-8") as f:
        f.write(" ".join(a[:1] if a and a[0] != "-c" else ["-c"]) + "\n")
if a and a[0] == "-c":
    if "ScoreStore" in a[1]:
        print(os.environ.get("STUB_TO", "2026-09-19")); raise SystemExit(0)
    raise SystemExit(f"stub: 未預期的 -c：{a[1][:40]}")
if a and a[0].endswith("score_ranges.py"):
    raise SystemExit(int(os.environ.get("STUB_RANGES_RC", "0")))
if a and a[0].endswith("revenue_base_impact.py"):
    rc = int(os.environ.get("STUB_RB_RC", "0"))
    assert a[a.index("--db") + 1] == "cache/scores.db"
    out = pathlib.Path(a[a.index("--out") + 1])
    assert str(out).startswith("runs/revbase/report_")
    if rc == 0 and not os.environ.get("STUB_RB_EMPTY"):
        out.write_text('{"schema": 1}', encoding="utf-8")
        out.with_suffix(".txt").write_text("revbase\n", encoding="utf-8")
    raise SystemExit(rc)
raise SystemExit(f"stub: 未預期的呼叫：{a[:3]}")
'''


def _git(cwd, *args):
    subprocess.run(["git", *args], cwd=cwd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def _sandbox(tmp_path, *, with_db=True):
    origin = tmp_path / "origin.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(origin)], check=True)
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.email", "t@example.invalid")
    _git(repo, "config", "user.name", "t")
    (repo / "README.md").write_text("x\n", encoding="utf-8")
    _git(repo, "add", "README.md")
    _git(repo, "commit", "-qm", "init")
    _git(repo, "remote", "add", "origin", str(origin))
    _git(repo, "push", "-q", "-u", "origin", "main")
    (repo / "scripts").mkdir()
    (repo / "scripts" / "hetzner_revbase.sh").write_bytes(SCRIPT.read_bytes())
    (repo / "cache").mkdir()
    if with_db:
        (repo / "cache" / "scores.db").write_text("db", encoding="utf-8")
    stub = tmp_path / "stub"
    stub.mkdir()
    (stub / "python3").write_text(STUB_PY.replace("@@PY@@", sys.executable), encoding="utf-8")
    (stub / "python3").chmod(0o755)
    return repo, origin, stub


def _sh(repo, stub, **env):
    e = dict(os.environ, PATH=f"{stub}{os.pathsep}{os.environ['PATH']}", **env)
    return subprocess.run(["bash", "scripts/hetzner_revbase.sh"], cwd=repo, env=e, capture_output=True, text=True, timeout=180)


def _branches(origin):
    out = subprocess.run(["git", "ls-remote", "--heads", str(origin)], capture_output=True, text=True, check=True).stdout
    return sorted(ln.split("refs/heads/")[-1] for ln in out.splitlines() if ln.strip())


def _tree(origin, br):
    return subprocess.run(["git", "ls-tree", "-r", "--name-only", br], cwd=origin,
                          capture_output=True, text=True, check=True).stdout.split()


def test_script_syntax_and_readonly():
    subprocess.run(["bash", "-n", str(SCRIPT)], check=True)
    t = SCRIPT.read_text(encoding="utf-8")
    assert "replay_scores.py" not in t and "--rebuild" not in t     # 只讀生產 db，不重播
    assert "  set -e\n" in t                                         # body 子 shell 重開 errexit
    assert "score_ranges.py --check" in t and "--force-with-lease" in t
    assert "tmux new -d -s revbase 'bash scripts/hetzner_revbase.sh'" in t


@pytest.mark.skipif(not shutil.which("git"), reason="需要 git")
def test_happy_path_pushes_report_only(tmp_path):
    repo, origin, stub = _sandbox(tmp_path)
    log = tmp_path / "calls.log"
    r = _sh(repo, stub, STUB_LOG=str(log))
    assert r.returncode == 0, r.stdout + r.stderr
    assert _branches(origin) == ["hetzner/revbase-2026-09-19", "main"]
    files = _tree(origin, "hetzner/revbase-2026-09-19")
    assert {"runs/revbase/report_2026-09-19.json", "runs/revbase/report_2026-09-19.txt"} <= set(files)
    assert not any(f.endswith(".db") for f in files)
    calls = log.read_text(encoding="utf-8").split("\n")
    assert calls.index("scripts/score_ranges.py") < calls.index("scripts/revenue_base_impact.py")   # 先守門再量測
    # 再跑一次（遠端分支已存在）：force-with-lease 以遠端現值為預期，照樣推得上去
    r2 = _sh(repo, stub, STUB_TO="2026-09-19")
    assert r2.returncode == 0, r2.stdout + r2.stderr


@pytest.mark.skipif(not shutil.which("git"), reason="需要 git")
def test_measure_failure_gives_rc3_no_push(tmp_path):
    repo, origin, stub = _sandbox(tmp_path)
    r = _sh(repo, stub, STUB_RB_RC="2")
    assert r.returncode == 3 and "營收基期影響面量測失敗" in r.stdout and "未推送報告" in r.stdout
    assert _branches(origin) == ["main"]


@pytest.mark.skipif(not shutil.which("git"), reason="需要 git")
def test_empty_output_gives_rc3_no_push(tmp_path):
    repo, origin, stub = _sandbox(tmp_path)
    r = _sh(repo, stub, STUB_RB_EMPTY="1")
    assert r.returncode == 3 and "沒產出或是空的" in r.stdout
    assert _branches(origin) == ["main"]


@pytest.mark.skipif(not shutil.which("git"), reason="需要 git")
def test_registry_out_of_date_gives_rc2_no_measure(tmp_path):
    repo, origin, stub = _sandbox(tmp_path)
    log = tmp_path / "calls.log"
    r = _sh(repo, stub, STUB_RANGES_RC="1", STUB_LOG=str(log))
    assert r.returncode == 2 and "未推送報告" in r.stdout
    assert "revenue_base_impact.py" not in log.read_text(encoding="utf-8")
    assert _branches(origin) == ["main"]


@pytest.mark.skipif(not shutil.which("git"), reason="需要 git")
def test_missing_db_gives_rc2_no_push(tmp_path):
    repo, origin, stub = _sandbox(tmp_path, with_db=False)
    r = _sh(repo, stub)
    assert r.returncode == 2 and "不存在" in r.stdout and _branches(origin) == ["main"]


@pytest.mark.skipif(not shutil.which("git"), reason="需要 git")
def test_dirty_tree_gives_rc2(tmp_path):
    repo, origin, stub = _sandbox(tmp_path)
    (repo / "README.md").write_text("changed\n", encoding="utf-8")
    r = _sh(repo, stub)
    assert r.returncode == 2 and "工作樹不乾淨" in r.stdout and _branches(origin) == ["main"]


@pytest.mark.skipif(not shutil.which("git"), reason="需要 git")
def test_git_step_failure_stops_before_measure(tmp_path):
    """步驟 0 的 git 指令只靠 errexit：origin 拿掉後 fetch 失敗，必須停、不得量測、不得推。"""
    repo, origin, stub = _sandbox(tmp_path)
    log = tmp_path / "calls.log"
    _git(repo, "remote", "set-url", "origin", str(tmp_path / "nope.git"))
    r = _sh(repo, stub, STUB_LOG=str(log))
    assert r.returncode != 0 and "== 2" not in r.stdout
    assert not log.exists() or "revenue_base_impact.py" not in log.read_text(encoding="utf-8")
    assert _branches(origin) == ["main"]
