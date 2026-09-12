"""`iching.scan` 逐日橫斷面掃描器（第 10／11 項）。免 token 免網路。

守的是**無聲錯誤**那一類：口徑（嚴格 vs 非嚴格）、母體（廣度 vs 排名池）、
視窗（有效收盤 vs 日曆日）、走訪順序（浮點加總不可交換）、deque 滿載後的邊界。
"""
from __future__ import annotations

import random
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from iching.scan import (DailyScanner, IndustryAgg, StockDay,  # noqa: E402
                         cross_percentile, pct_return)


def sd(sid, close, *, market="twse", industry="水泥工業", amount=1000.0, in_pool=True):
    return StockDay(sid, market, industry, close, amount, in_pool)


# ---------------------------------------------------------------------------
# 純函式
# ---------------------------------------------------------------------------
def test_pct_return_boundaries():
    assert pct_return([10.0, 11.0, 12.0], 2) == pytest.approx(20.0)     # 12/10 − 1
    assert pct_return([10.0, 12.0], 2) is None                          # 不足 n+1 筆
    assert pct_return([0.0, 1.0, 2.0], 2) is None                       # 基期 0
    assert pct_return([10.0, 11.0], 1) == pytest.approx(10.0)


def test_cross_percentile_matches_p_hist_convention():
    """與 `transform.P_hist` 的 tie="mid" 同一套：常數序列 50、最大值 100 − 50/N。"""
    from iching.score.transform import P_hist
    vals = {"a": 1.0, "b": 2.0, "c": 3.0, "d": 3.0}
    got = cross_percentile(vals)
    assert got["a"] == pytest.approx(12.5)          # (0 + .5×1)/4
    assert got["b"] == pytest.approx(37.5)
    assert got["c"] == got["d"] == pytest.approx(75.0)   # (2 + .5×2)/4
    assert cross_percentile({"x": 5.0, "y": 5.0}) == {"x": 50.0, "y": 50.0}
    # 與 P_hist 同輸入同結果（同一組 tie 規則，不可各寫一套）
    window = [1.0, 2.0, 3.0, 4.0]
    assert P_hist(window, 4, True, "mid").native == pytest.approx(cross_percentile(
        {"a": 1.0, "b": 2.0, "c": 3.0, "d": 4.0})["d"])
    assert cross_percentile({}) == {}
    with pytest.raises(ValueError):
        cross_percentile({"a": 1.0}, tie="nope")


# ---------------------------------------------------------------------------
# 廣度：逐項手算
# ---------------------------------------------------------------------------
def _run_three_stock():
    sc = DailyScanner(ma_windows=(3,), hl_windows=(3,), ret_windows=(2,))
    px = {"1101": [10, 11, 12], "1102": [20, 19, 18], "1103": [30, 30, 30]}
    idx = [100.0, 101.0, 102.0]
    amt = {"1101": 1000.0, "1102": 2000.0, "1103": 3000.0}
    outs = []
    for i, d in enumerate(["2020-01-02", "2020-01-03", "2020-01-06"]):
        rows = [sd(s, float(v[i]), amount=amt[s]) for s, v in px.items()]
        outs.append(sc.push_day(d, rows, {"twse": idx[i]}))
    return sc, outs


def test_breadth_hand_computed():
    _, outs = _run_three_stock()
    b0, b1, b2 = (o.breadth["twse"] for o in outs)
    # 首日：沒有前一個有效收盤 → 漲跌全 0、up_amount_ratio 無母體
    assert (b0.n_stocks, b0.advance_count, b0.decline_count, b0.ret_eligible) == (3, 0, 0, 0)
    assert b0.up_amount_ratio is None and b0.ad_line == 0
    assert b0.ma_eligible[3] == 0 and b0.above_ma_count[3] == 0
    # 第三日：1101 漲、1102 跌、1103 平
    assert (b2.advance_count, b2.decline_count, b2.unchanged_count) == (1, 1, 1)
    assert b2.ad_line == 0                                   # (1−1) 累積三日
    assert b2.above_ma_count[3] == 1 and b2.ma_eligible[3] == 3      # 只有 1101 站上 MA3
    assert b2.new_high_count[3] == 1 and b2.new_low_count[3] == 1
    assert b2.advance_ratio == pytest.approx(1 / 3)
    assert b2.above_ma_ratio[3] == pytest.approx(1 / 3)
    assert b2.new_high_low_ratio[3] == pytest.approx(0.0)    # (1 − 1)/3
    assert b2.up_amount_ratio == pytest.approx(1000 / 6000)
    assert b2.amount_total == pytest.approx(6000.0)


def test_strict_comparisons_flat_series():
    """平盤序列：既非新高也非新低、也不算站上 MA。

    若改用 `>=`／`<=`，一條水平線會**同時**被判新高與新低（淨值 0，看起來沒事），
    且每一檔都「站上」自己的 MA，`above_ma_ratio` 恆為 1.0。
    """
    sc = DailyScanner(ma_windows=(3,), hl_windows=(3,), ret_windows=(2,))
    for i, d in enumerate(["2020-01-02", "2020-01-03", "2020-01-06", "2020-01-07"]):
        out = sc.push_day(d, [sd("1101", 50.0), sd("1102", 50.0)], {"twse": 100.0})
    b = out.breadth["twse"]
    assert b.new_high_count[3] == 0 and b.new_low_count[3] == 0
    assert b.above_ma_count[3] == 0 and b.ma_eligible[3] == 2
    assert b.unchanged_count == 2 and b.ad_line == 0


def test_untraded_day_skipped_from_windows_and_universe():
    """無成交日：不進母體、不進視窗——MA 取的是「最近 n 個**有效**收盤」而非日曆日。"""
    sc = DailyScanner(ma_windows=(3,), hl_windows=(3,), ret_windows=(2,))
    seq = [10.0, None, 20.0, 30.0]        # 第二日停牌
    outs = []
    for i, d in enumerate(["2020-01-02", "2020-01-03", "2020-01-06", "2020-01-07"]):
        outs.append(sc.push_day(d, [sd("1101", seq[i]), sd("1102", 5.0)], {"twse": 100.0 + i}))
    assert outs[1].breadth["twse"].n_stocks == 1                  # 停牌日母體只剩 1102
    assert sc.history_len("1101") == 3                            # 只累積 3 個有效收盤
    b = outs[3].breadth["twse"]
    assert b.ma_eligible[3] == 2                                  # 1101 第 3 個有效收盤才夠
    assert b.above_ma_count[3] == 1                               # MA3=(10+20+30)/3=20 < 30
    # 漲跌是對「前一個有效收盤」：停牌後復牌的 20 對 10 算漲
    assert outs[2].breadth["twse"].advance_count == 1


def test_ad_line_accumulates_across_days_from_zero():
    sc = DailyScanner(ma_windows=(3,), hl_windows=(3,), ret_windows=(2,))
    seq = [[10.0, 10.0], [11.0, 9.0], [12.0, 8.0], [13.0, 9.0]]
    ads = []
    for i, d in enumerate(["2020-01-02", "2020-01-03", "2020-01-06", "2020-01-07"]):
        o = sc.push_day(d, [sd("1101", seq[i][0]), sd("1102", seq[i][1])], {"twse": 100.0})
        ads.append(o.breadth["twse"].ad_line)
    assert ads == [0, 0, 0, 2]      # 首日 0；之後每日 (1漲−1跌)=0；末日兩檔皆漲 +2


# ---------------------------------------------------------------------------
# 母體：廣度 vs 排名池
# ---------------------------------------------------------------------------
def test_rank_pool_only_gates_p_cs():
    """`in_rank_pool=False` 的股票**仍在**廣度母體與產業中位數裡，**只**不進 `P_cs`。"""
    sc = DailyScanner(ma_windows=(2,), hl_windows=(2,), ret_windows=(1,))
    rows_a = [sd("1101", 10.0), sd("1102", 10.0, in_pool=False), sd("1103", 10.0)]
    rows_b = [sd("1101", 12.0), sd("1102", 20.0, in_pool=False), sd("1103", 11.0)]
    sc.push_day("2020-01-02", rows_a, {"twse": 100.0})
    out = sc.push_day("2020-01-03", rows_b, {"twse": 100.0})
    assert out.breadth["twse"].n_stocks == 3                       # 廣度母體含非排名池
    assert set(out.excess[("twse", 1)]) == {"1101", "1102", "1103"}
    assert set(out.p_cs[("twse", 1)]) == {"1101", "1103"}          # P_cs 只有排名池
    assert out.p_cs[("twse", 1)]["1101"] == pytest.approx(75.0)    # 兩檔母體、1101 較高
    ind = [a for a in out.industry if a.window == 1][0]
    assert ind.n == 3                                              # 產業中位數用廣度母體
    assert ind.median_ret == pytest.approx(20.0)                   # median(20, 100, 10)


def test_industry_split_and_small_sample_not_filtered():
    """小樣本照實輸出（`industry_min_sample` 由計分端判），無產業別者不進聚合。"""
    sc = DailyScanner(ma_windows=(2,), hl_windows=(2,), ret_windows=(1,))
    a = [sd("1101", 10.0, industry="水泥"), sd("2330", 10.0, industry="半導體"), sd("9999", 10.0, industry=None)]
    b = [sd("1101", 11.0, industry="水泥"), sd("2330", 13.0, industry="半導體"), sd("9999", 15.0, industry=None)]
    sc.push_day("2020-01-02", a, {"twse": 100.0})
    out = sc.push_day("2020-01-03", b, {"twse": 100.0})
    got = {(x.industry, x.n, round(x.median_ret, 6)) for x in out.industry}
    assert got == {("水泥", 1, 10.0), ("半導體", 1, 30.0)}          # 9999 無產業別 → 不聚合
    assert all(isinstance(x, IndustryAgg) for x in out.industry)


def test_two_markets_are_independent():
    sc = DailyScanner(ma_windows=(2,), hl_windows=(2,), ret_windows=(1,))
    a = [sd("1101", 10.0), sd("6488", 10.0, market="tpex")]
    b = [sd("1101", 11.0), sd("6488", 9.0, market="tpex")]
    sc.push_day("2020-01-02", a, {"twse": 100.0, "tpex": 200.0})
    out = sc.push_day("2020-01-03", b, {"twse": 100.0, "tpex": 200.0})
    assert out.breadth["twse"].ad_line == 1 and out.breadth["tpex"].ad_line == -1
    assert set(out.p_cs) == {("twse", 1), ("tpex", 1)}
    assert out.excess[("twse", 1)]["1101"] == pytest.approx(10.0)
    assert out.excess[("tpex", 1)]["6488"] == pytest.approx(-10.0)


# ---------------------------------------------------------------------------
# 邊界與不變式
# ---------------------------------------------------------------------------
def test_push_day_must_be_ascending():
    sc = DailyScanner()
    sc.push_day("2020-01-03", [sd("1101", 10.0)], {"twse": 100.0})
    with pytest.raises(ValueError, match="升冪"):
        sc.push_day("2020-01-02", [sd("1101", 10.0)], {"twse": 100.0})
    with pytest.raises(ValueError, match="升冪"):
        sc.push_day("2020-01-03", [sd("1101", 10.0)], {"twse": 100.0})


def test_missing_index_is_reported_not_swallowed():
    sc = DailyScanner(ma_windows=(2,), hl_windows=(2,), ret_windows=(1,))
    sc.push_day("2020-01-02", [sd("1101", 10.0)], {"twse": 100.0})
    out = sc.push_day("2020-01-03", [sd("1101", 11.0)], {"twse": None})
    assert out.index_missing == ["twse"]
    assert out.excess == {} and out.p_cs == {} and out.industry == []
    assert out.breadth["twse"].n_stocks == 1        # 廣度不受指數缺值影響


def test_deque_full_boundary_60_day_return():
    """視窗滿載（第 62 天起每日 eviction）後，60 日報酬仍要對。

    守的是 **`_maxlen` 少算一筆**這類錯（60 日報酬需要 **61** 個收盤）。
    **不**守「eviction 分支」——2026-09-12 突變實證 `prior[1:]+[c]` 與 `prior+[c]` 逐位相同
    （取用一律從尾端切），該分支已因此移除。
    """
    sc = DailyScanner()
    price = 100.0
    hist = []
    for i in range(80):
        price *= 1.01
        hist.append(price)
        d = f"2020-{1 + i // 28:02d}-{1 + i % 28:02d}"
        out = sc.push_day(d, [sd("1101", price)], {"twse": 1000.0})
    assert sc.history_len("1101") == 61                           # maxlen
    expect = (hist[-1] / hist[-61] - 1.0) * 100.0                 # 60 個交易日前
    assert out.excess[("twse", 60)]["1101"] == pytest.approx(expect, rel=1e-12)


def test_row_order_does_not_change_any_output():
    """走訪順序不得影響任何輸出——浮點加總不可交換，兩層 parity 靠這條。

    家族前例：`taiwan-flows` 的次產業張數因兩條路徑的走訪順序不同差 1e-9，
    經 `Math.round` 放大成可見的「差 1 張」。
    """
    random.seed(1234)
    base = []
    for i in range(60):
        base.append([sd(f"{1000 + j}", 10.0 + random.random() * 90, amount=random.random() * 1e7,
                        market="twse" if j % 2 else "tpex", industry=f"IND{j % 5}",
                        in_pool=(j % 3 != 0)) for j in range(40)])

    def run(shuffle: bool):
        sc = DailyScanner()
        outs = []
        for i, rows in enumerate(base):
            rows = list(rows)
            if shuffle:
                random.Random(i).shuffle(rows)
            outs.append(sc.push_day(f"2020-{1 + i // 28:02d}-{1 + i % 28:02d}", rows,
                                    {"twse": 10000.0 + i, "tpex": 200.0 + i}))
        return outs

    for a, b in zip(run(False), run(True)):
        assert a == b, f"{a.tpe_date} 走訪順序改變了輸出"


def test_scan_module_never_imports_sqlite3():
    """兩層 parity 硬約束：計分／特徵路徑的純函式模組不得碰 DB（用 AST 驗，不用 grep——
    docstring 裡寫著「不 import sqlite3」會讓 grep 假陽性，前例已踩過）。"""
    import ast
    tree = ast.parse((ROOT / "src" / "iching" / "scan.py").read_text(encoding="utf-8"))
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module.split(".")[0])
    assert "sqlite3" not in names, f"scan.py 匯入了 {sorted(names)}"
