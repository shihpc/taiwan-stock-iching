"""裁定 #68（2026-09-25，`docs/P3-CALIBRATION.md` §31）：營收年增率的分母（去年同期合計）≤ 0 一律視為缺值。

原因碼沿用 `denominator_zero`（`spec/P1-B2-params.md` 缺值三碼不擴充），detail 區分 `sum=0` 與 `sum<0`。
規則在 `stock.revenue_yoy_3m` 一處實作，因此對 `revenue_yoy`（短線單月／波段中期三月）、`revenue_accel`（兩組各自）、
中期族 C `revenue_yoy_vs_industry` 與產業中位數（`FundamentalsBridge._industry_stats` 跳過缺值）同時生效——本檔逐處驗。
期待值一律手算，不由被測函式產生。免 token、免網路。
"""
from __future__ import annotations

import datetime as dt

import pytest

from iching import fundamentals as FUND
from iching.score import stock as STK
from iching.score.params import RULES_VERSION, build_params
from iching.score.transform import REASON_DENOM_ZERO, REASON_MISSING, Missing, S_clip, normalize

NEG = "(base<=0 treated as missing, ruling #68)"


def _months(y0: int, m0: int, vals: list[float]) -> dict[str, float]:
    out, y, m = {}, y0, m0
    for v in vals:
        out[f"{y:04d}-{m:02d}"] = float(v)
        m += 1
        if m == 13:
            y, m = y + 1, 1
    return out


# ---------------------------------------------------------------------------
# revenue_yoy_3m 本身
# ---------------------------------------------------------------------------
def test_single_month_negative_base_is_missing():
    r = STK.revenue_yoy_3m({"2019-01": -10.0, "2020-01": 5.0}, "2020-01", 0, 1)
    assert isinstance(r, Missing) and r.reason == REASON_DENOM_ZERO
    assert r.detail == f"last-year 1M sum<0 {NEG}"


def test_single_month_zero_base_detail_unchanged():
    """den＝0 的 detail 與 #68 前逐字相同（§29 的報告與測試引用這個字串）。"""
    r = STK.revenue_yoy_3m({"2019-01": 0.0, "2020-01": 5.0}, "2020-01", 0, 1)
    assert isinstance(r, Missing) and r.reason == REASON_DENOM_ZERO and r.detail == "last-year 1M sum=0"


def test_tiny_positive_base_still_computed():
    """不設最小基期門檻（裁定 #67 不變）：den 再小，只要 > 0 就照算。"""
    assert STK.revenue_yoy_3m({"2019-01": 1e-9, "2020-01": 5.0}, "2020-01", 0, 1) == (5.0 / 1e-9 - 1.0) * 100.0


def test_negative_numerator_positive_base_is_not_in_rule():
    """分子 < 0、den > 0 不在 #68 範圍：照算，年增率可 < −100。"""
    assert STK.revenue_yoy_3m({"2019-01": 10.0, "2020-01": -5.0}, "2020-01", 0, 1) == -150.0


def test_three_month_sum_decides_not_single_month():
    """三月組看**合計**：含一個負值月但合計 > 0 → 照算；合計 < 0 → 缺值。"""
    base_pos = _months(2019, 1, [10, 10, -15]) | _months(2020, 1, [10, 10, 10])   # den＝5、num＝30
    assert STK.revenue_yoy_3m(base_pos, "2020-03", 0, 3) == (30.0 / 5.0 - 1.0) * 100.0
    base_neg = _months(2019, 1, [10, 10, -25]) | _months(2020, 1, [10, 10, 10])   # den＝−5
    r = STK.revenue_yoy_3m(base_neg, "2020-03", 0, 3)
    assert isinstance(r, Missing) and r.reason == REASON_DENOM_ZERO and r.detail == f"last-year 3M sum<0 {NEG}"
    base_zero = _months(2019, 1, [10, 10, -20]) | _months(2020, 1, [10, 10, 10])  # den＝0
    r0 = STK.revenue_yoy_3m(base_zero, "2020-03", 0, 3)
    assert isinstance(r0, Missing) and r0.detail == "last-year 3M sum=0"


def test_offset_group_negative_base():
    """加速度的前一組（offset＝3）den < 0 → 缺值；近組照算。"""
    rev = _months(2018, 10, [10, 10, -40]) | _months(2019, 1, [10] * 3) | _months(2019, 10, [10] * 6)
    assert STK.revenue_yoy_3m(rev, "2020-03", 0, 3) == 0.0                         # 近組 2020-01..03 vs 2019-01..03
    r = STK.revenue_yoy_3m(rev, "2020-03", 3, 3)                                   # 前組 2019-10..12 vs 2018-10..12＝−20
    assert isinstance(r, Missing) and r.reason == REASON_DENOM_ZERO and "sum<0" in r.detail


# ---------------------------------------------------------------------------
# 子指標：ind_revenue_yoy（短線 window 1／三月 window 3）、ind_revenue_accel（兩組各自）
# ---------------------------------------------------------------------------
def test_ind_revenue_yoy_short_window_1():
    r = STK.ind_revenue_yoy({"2019-06": -5.0, "2020-06": 10.0}, "2020-06", 1, 20.0)
    assert isinstance(r, Missing) and r.reason == REASON_DENOM_ZERO and r.detail == f"last-year 1M sum<0 {NEG}"


def test_ind_revenue_yoy_three_months():
    rev = _months(2019, 4, [10, 10, -25]) | _months(2020, 4, [10, 10, 10])
    r = STK.ind_revenue_yoy(rev, "2020-06", 3, 15.0)
    assert isinstance(r, Missing) and r.detail == f"last-year 3M sum<0 {NEG}"


def test_ind_revenue_accel_prev_group_negative():
    """近組照算（den＝30），前組 den＝−20 → 整個子指標缺（原因碼取前組的）。"""
    rev = _months(2018, 10, [10, 10, -40]) | _months(2019, 1, [10] * 3) | _months(2019, 10, [10] * 6)
    r = STK.ind_revenue_accel(rev, "2020-03", 3, 10.0)
    assert isinstance(r, Missing) and r.reason == REASON_DENOM_ZERO and r.detail == f"last-year 3M sum<0 {NEG}"


def test_ind_revenue_accel_near_group_negative():
    rev = _months(2018, 10, [10] * 6) | _months(2019, 4, [10, 10, -40]) | _months(2019, 10, [10] * 3) | _months(2020, 4, [10] * 3)
    r = STK.ind_revenue_accel(rev, "2020-06", 3, 10.0)
    assert isinstance(r, Missing) and "sum<0" in r.detail


# ---------------------------------------------------------------------------
# 初爻：短線單月 den<0 而三月組 den>0 → 族 A 只剩加速度（reweighted），分數＝加速度的 S 值
# ---------------------------------------------------------------------------
def test_line1_short_single_month_negative_base_reweights():
    ps = build_params("twse")
    vals = {f"2019-{m:02d}": 10.0 for m in range(1, 13)} | {f"2020-{m:02d}": 12.0 for m in range(1, 7)}
    vals["2019-06"] = -5.0                                   # 單月 den＝−5；近組 den＝10+10−5＝15、前組 den＝30
    si = STK.StockInputs(market="twse", stock_id="9001", tpe_date="2020-07-15", monthly_revenue=sorted(vals.items()))
    lr = STK.line1_operations(si, ps, "short")
    fa = lr.family("A")
    subs = {s.indicator_id: s for s in fa.subs}
    assert subs["revenue_yoy"].missing is not None and subs["revenue_yoy"].missing.reason == REASON_DENOM_ZERO
    assert subs["revenue_yoy"].missing.detail == f"last-year 1M sum<0 {NEG}"
    # 加速度＝近組 YoY − 前組 YoY＝(36/15 − 1)×100 − (36/30 − 1)×100＝140 − 20＝120（手算）
    x = (36.0 / 15.0 - 1.0) * 100.0 - (36.0 / 30.0 - 1.0) * 100.0
    assert subs["revenue_accel"].x == pytest.approx(120.0) and subs["revenue_accel"].x == x
    d = ps.get("stock", "short", "1", "A", "revenue_accel").d
    assert lr.score == normalize(S_clip(x, 0.0, d)) and lr.reweighted and not lr.unknown and fa.reweighted


def test_line1_all_revenue_subs_negative_base_is_unknown():
    """短線兩個族 A 子指標都因 den<0 缺 → 族 A 缺、初爻未知（短線只有族 A）。"""
    ps = build_params("twse")
    vals = {f"2019-{m:02d}": -10.0 for m in range(1, 13)} | {f"2020-{m:02d}": 12.0 for m in range(1, 7)}
    si = STK.StockInputs(market="twse", stock_id="9001", tpe_date="2020-07-15", monthly_revenue=sorted(vals.items()))
    lr = STK.line1_operations(si, ps, "short")
    assert lr.unknown and lr.score is None
    assert all(s.missing is not None and s.missing.reason == REASON_DENOM_ZERO for s in lr.family("A").subs)


# ---------------------------------------------------------------------------
# 產業中位數：`_industry_stats` 跳過 den ≤ 0 的檔（樣本數相應減少；< 5 → 族 C 整族缺）
# ---------------------------------------------------------------------------
def _cal() -> list[str]:
    d, out = dt.date(2017, 1, 2), []
    while d <= dt.date(2021, 6, 30):
        if d.weekday() < 5:
            out.append(d.isoformat())
        d += dt.timedelta(days=1)
    return out


def _bridge(n_stocks: int) -> FUND.FundamentalsBridge:
    """900i 同產業（水泥工業，非金融）。2018 各月＝10、2019 各月＝9＋i ⇒ 三月 YoY＝(i−1)×10（i≥2）。
    9001 的 2018-10＝−50 ⇒ 最新月 2019-12 的三月組 den＝−50＋10＋10＝−30 < 0（#68 前的值＝(30／−30 − 1)×100＝−200）。"""
    cal = _cal()
    ind = {f"900{i}": "水泥工業" for i in range(1, n_stocks + 1)}
    stocks = {}
    for i in range(1, n_stocks + 1):
        sid = f"900{i}"
        rows = []
        for k in range(24):
            y, m = 2018 + k // 12, k % 12 + 1
            v = 10.0 if y == 2018 else 9.0 + i
            if sid == "9001" and (y, m) == (2018, 10):
                v = -50.0
            rows.append((y, m, v))
        stocks[sid] = FUND.build_stock(sid, "水泥工業", rows, [], {}, cal)
    return FUND.FundamentalsBridge(stocks, ind)


T = "2020-01-20"                                              # 2019-12 營收已可用（次月 10 日收盤後）


def test_industry_stats_skips_negative_base_stock():
    """6 檔：9001 den<0 被跳過 → 樣本 5、中位數＝{10,20,30,40,50} 的 30（#68 前會是 6 檔含 −200、中位數 25）。"""
    b = _bridge(6)
    for sid in ("9001", "9002"):
        inp = b.inputs_for(sid, T)
        assert inp["industry_revenue_n"] == 5 and inp["industry_median_3m_yoy"] == pytest.approx(30.0, abs=1e-9)
        assert max(ym for ym, _ in inp["monthly_revenue"]) == "2019-12"
    ps = build_params("twse")
    inp = b.inputs_for("9002", T)
    si = STK.StockInputs(market="twse", stock_id="9002", tpe_date=T, industry="水泥工業", **inp)
    fc = STK.line1_operations(si, ps, "mid").family("C")
    sub = fc.subs[0]
    assert sub.indicator_id == "revenue_yoy_vs_industry" and sub.missing is None
    assert sub.x == pytest.approx(10.0 - 30.0)               # 9002 三月 YoY 10 − 產業中位數 30


def test_industry_sample_below_min_drops_family_c():
    """5 檔：9001 被跳過 → 樣本 4 < 5 → 所有同業的族 C 整族缺（REASON_MISSING，產業樣本不足）。"""
    b = _bridge(5)
    ps = build_params("twse")
    for sid in ("9001", "9003"):
        inp = b.inputs_for(sid, T)
        assert inp["industry_revenue_n"] == 4 and inp["industry_median_3m_yoy"] == pytest.approx(25.0, abs=1e-9)   # {10,20,30,40}
        si = STK.StockInputs(market="twse", stock_id=sid, tpe_date=T, industry="水泥工業", **inp)
        fc = STK.line1_operations(si, ps, "mid").family("C")
        assert fc.score is None and fc.subs[0].missing.reason == REASON_MISSING
        assert "industry sample < 5" in fc.subs[0].missing.detail


def test_negative_base_stock_own_family_c_missing_with_denominator_zero():
    """9001 自己在 6 檔世界：產業樣本夠（5），但自身三月 YoY den<0 → 族 C 子指標缺、原因碼 denominator_zero。"""
    b = _bridge(6)
    ps = build_params("twse")
    inp = b.inputs_for("9001", T)
    si = STK.StockInputs(market="twse", stock_id="9001", tpe_date=T, industry="水泥工業", **inp)
    lr = STK.line1_operations(si, ps, "mid")
    c = lr.family("C").subs[0]
    assert c.missing is not None and c.missing.reason == REASON_DENOM_ZERO and c.missing.detail == f"last-year 3M sum<0 {NEG}"


# ---------------------------------------------------------------------------
# 指紋：缺值規則的變更只有 RULES_VERSION 會帶進 model_version
# ---------------------------------------------------------------------------
def test_rules_version_bumped_for_ruling_68():
    assert RULES_VERSION == "p2-score-engine-2"
    for m in ("twse", "tpex"):
        assert build_params(m).model_version().startswith("p2-score-engine-2.")
