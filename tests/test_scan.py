"""`iching.scan` 逐日橫斷面掃描器（第 10／11 項）。免 token 免網路。

守的是**無聲錯誤**那一類：口徑（嚴格 vs 非嚴格）、母體（廣度 vs 排名池）、
視窗（有效收盤 vs 日曆日）、走訪順序（浮點加總不可交換）、deque 滿載後的邊界。
"""
from __future__ import annotations

import random
import statistics
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from iching.scan import (DailyScanner, IndustryAgg, IndustryBreadth, StockDay,  # noqa: E402
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
    sc = DailyScanner(ma_windows=(3,), hl_windows=(3,), ret_windows=(2,), p_cs_windows=(2,))
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
    sc = DailyScanner(ma_windows=(3,), hl_windows=(3,), ret_windows=(2,), p_cs_windows=(2,))
    for i, d in enumerate(["2020-01-02", "2020-01-03", "2020-01-06", "2020-01-07"]):
        out = sc.push_day(d, [sd("1101", 50.0), sd("1102", 50.0)], {"twse": 100.0})
    b = out.breadth["twse"]
    assert b.new_high_count[3] == 0 and b.new_low_count[3] == 0
    assert b.above_ma_count[3] == 0 and b.ma_eligible[3] == 2
    assert b.unchanged_count == 2 and b.ad_line == 0


def test_untraded_day_skipped_from_windows_and_universe():
    """無成交日：不進母體、不進視窗——MA 取的是「最近 n 個**有效**收盤」而非日曆日。"""
    sc = DailyScanner(ma_windows=(3,), hl_windows=(3,), ret_windows=(2,), p_cs_windows=(2,))
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
    sc = DailyScanner(ma_windows=(3,), hl_windows=(3,), ret_windows=(2,), p_cs_windows=(2,))
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
    sc = DailyScanner(ma_windows=(2,), hl_windows=(2,), ret_windows=(1,), p_cs_windows=(1,))
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
    sc = DailyScanner(ma_windows=(2,), hl_windows=(2,), ret_windows=(1,), p_cs_windows=(1,))
    a = [sd("1101", 10.0, industry="水泥"), sd("2330", 10.0, industry="半導體"), sd("9999", 10.0, industry=None)]
    b = [sd("1101", 11.0, industry="水泥"), sd("2330", 13.0, industry="半導體"), sd("9999", 15.0, industry=None)]
    sc.push_day("2020-01-02", a, {"twse": 100.0})
    out = sc.push_day("2020-01-03", b, {"twse": 100.0})
    got = {(x.industry, x.n, round(x.median_ret, 6)) for x in out.industry}
    assert got == {("水泥", 1, 10.0), ("半導體", 1, 30.0)}          # 9999 無產業別 → 不聚合
    assert all(isinstance(x, IndustryAgg) for x in out.industry)


def test_two_markets_are_independent():
    sc = DailyScanner(ma_windows=(2,), hl_windows=(2,), ret_windows=(1,), p_cs_windows=(1,))
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
    sc = DailyScanner(ma_windows=(2,), hl_windows=(2,), ret_windows=(1,), p_cs_windows=(1,))
    sc.push_day("2020-01-02", [sd("1101", 10.0)], {"twse": 100.0})
    out = sc.push_day("2020-01-03", [sd("1101", 11.0)], {"twse": None})
    assert out.index_missing == ["twse"]
    assert out.excess == {} and out.p_cs == {}      # 超額與 P_cs 需要指數
    assert out.breadth["twse"].n_stocks == 1        # 廣度不受指數缺值影響
    # 產業中位數是**原始**報酬的中位數 → 不依賴指數，缺指數的日子照樣要產得出來
    assert [(a.industry, a.n) for a in out.industry] == [("水泥工業", 1)]
    assert out.industry[0].median_ret == pytest.approx(10.0)
    assert [x.industry for x in out.industry_breadth] == ["水泥工業"]


def test_deque_full_boundary_60_day_return():
    """視窗滿載（第 62 天起每日 eviction）後，60 日報酬仍要對。

    守的是 **`_maxlen` 少算一筆**這類錯（60 日報酬需要 **61** 個收盤）。
    **真正被 `+1` 救到的是指數 deque**（2026-09-12 驗收插樁量到）：個股側因為 `closes = prior + [c]`
    多帶一筆、拿掉 `+1` 仍算得出來，指數側只有 60 筆就整個 `("twse", 60)` 消失。
    **不**守「eviction 分支」——突變實證 `prior[1:]+[c]` 與 `prior+[c]` 逐位相同
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


# ---------------------------------------------------------------------------
# 產業聚合：2026-09-12 驗收抓到的口徑錯（原版聚合的是超額報酬）
# ---------------------------------------------------------------------------
def test_industry_median_is_raw_return_not_excess():
    """`IndustryAgg.median_ret` 必須是**原始** n 日報酬的中位數。

    初版聚合超額報酬，兩個消費端都會多減一次指數報酬：
    - `ind_excess_vs_industry` 算 `raw_i − median`，
    - `ind_industry_relative` 算 `median − 指數報酬`（自己就減了）。

    **這條測試刻意讓指數報酬 ≠ 0**——初版的兩支產業測試指數兩日都是 100.0，`mret=0` 時
    超額恰等於原始報酬，錯的和對的長一樣，於是漏掉。
    """
    sc = DailyScanner(ma_windows=(2,), hl_windows=(2,), ret_windows=(1,), p_cs_windows=(1,))
    r0 = [sd(f"110{i}", 100.0, industry="半導體") for i in range(1, 6)]
    px = [104.0, 106.0, 108.0, 110.0, 112.0]                 # 原始報酬 4/6/8/10/12 %
    r1 = [sd(f"110{i}", px[i - 1], industry="半導體") for i in range(1, 6)]
    sc.push_day("2020-01-02", r0, {"twse": 1000.0})
    out = sc.push_day("2020-01-03", r1, {"twse": 1080.0})      # 指數 +8%
    agg = [a for a in out.industry if a.window == 1][0]
    assert agg.median_ret == pytest.approx(8.0)                # median(原始)；聚合超額會得 0.0
    assert agg.n == 5
    # 一致性：raw_i − median(raw) ≡ excess_i − median(excess)，兩邊都要對得上
    ex = out.excess[("twse", 1)]
    assert 8.0 - agg.median_ret == pytest.approx(ex["1103"] - statistics.median(sorted(ex.values())))


def test_industry_breadth_counts_and_ratio():
    """`IndustryBreadth`＝`StockInputs.industry_above_ma_ratio` 的來源，分母是產業當日有成交檔數。"""
    sc = DailyScanner(ma_windows=(3,), hl_windows=(3,), ret_windows=(1,), p_cs_windows=(1,))
    seq = {"1101": [10.0, 11.0, 12.0], "1102": [10.0, 9.0, 8.0], "2330": [50.0, 51.0, 52.0]}
    ind = {"1101": "水泥", "1102": "水泥", "2330": "半導體"}
    for i, d in enumerate(["2020-01-02", "2020-01-03", "2020-01-06"]):
        out = sc.push_day(d, [sd(s_, v[i], industry=ind[s_]) for s_, v in seq.items()], {"twse": 100.0})
    got = {x.industry: (x.n_stocks, x.above_ma_count[3], x.ma_eligible[3], x.above_ma_ratio[3])
           for x in out.industry_breadth}
    assert got == {"水泥": (2, 1, 2, 0.5), "半導體": (1, 1, 1, 1.0)}
    assert all(isinstance(x, IndustryBreadth) for x in out.industry_breadth)
    # 無產業別者不進產業廣度，但仍在大盤廣度母體
    out2 = sc.push_day("2020-01-07", [sd("9999", 5.0, industry=None)], {"twse": 100.0})
    assert out2.industry_breadth == [] and out2.breadth["twse"].n_stocks == 1


def test_ret_windows_cover_both_consumers_and_pcs_is_narrower():
    """產業中位報酬要 STK_L3 長視窗 ∪ STK_L6 視窗；`P_cs` 只用 STK_L3 長視窗。

    同一個窗長對不同消費端是不同 horizon（10：L3 短線／L6 波段；20：L3 波段／L6 中期），
    所以本模組一律以 window 當鍵。這條把兩張對照表釘住，改動時不能只改一邊。
    """
    from iching.scan import (HORIZON_BY_L3_LONG_WINDOW, HORIZON_BY_L6_WINDOW,
                             P_CS_WINDOWS, RET_WINDOWS)
    from iching.score.params import STK_L3_WIN, STK_L6_WIN
    need = {STK_L3_WIN[h][1] for h in STK_L3_WIN} | {STK_L6_WIN[h] for h in STK_L6_WIN}
    assert need <= set(RET_WINDOWS), f"RET_WINDOWS 缺 {sorted(need - set(RET_WINDOWS))}"
    assert set(P_CS_WINDOWS) == {STK_L3_WIN[h][1] for h in STK_L3_WIN}
    assert HORIZON_BY_L3_LONG_WINDOW == {STK_L3_WIN[h][1]: h for h in STK_L3_WIN}
    assert HORIZON_BY_L6_WINDOW == {STK_L6_WIN[h]: h for h in STK_L6_WIN}
    assert HORIZON_BY_L3_LONG_WINDOW[10] != HORIZON_BY_L6_WINDOW[10]     # 同窗長不同 horizon
    # MA 窗長要涵蓋 industry_above_ma20_ratio 宣告的 20
    from iching.scan import MA_WINDOWS
    assert 20 in MA_WINDOWS


def test_no_inert_switch_parameter():
    """不得留「傳了沒作用」的建構子參數／常數。

    初版的 `ADVANCE_ON_ADJUSTED` 與同名參數沒有任何分支讀取，傳 True／False 輸出完全相同
    ——靜默無效的旋鈕比沒有旋鈕更糟（2026-09-12 驗收抓到，已移除）。
    """
    import inspect

    import iching.scan as S
    assert not hasattr(S, "ADVANCE_ON_ADJUSTED")
    params = set(inspect.signature(S.DailyScanner.__init__).parameters) - {"self"}
    assert params == {"ma_windows", "hl_windows", "ret_windows", "p_cs_windows"}
    # 每個參數都要真的改變輸出（否則它就是下一個靜默旋鈕）
    rows = [sd(f"{1000 + j}", 10.0 + j, industry="X") for j in range(8)]
    def run(**kw):
        sc = S.DailyScanner(**kw)
        o = None
        for i in range(12):
            o = sc.push_day(f"2020-01-{1 + i:02d}",
                            [r._replace(close_adj=(r.close_adj or 0) * (1 + 0.01 * i)) for r in rows],
                            {"twse": 100.0 + i})
        return o
    base = dict(ma_windows=(3,), hl_windows=(3,), ret_windows=(2, 3), p_cs_windows=(2,))
    ref = run(**base)
    assert run(**{**base, "ma_windows": (4,)}) != ref
    assert run(**{**base, "hl_windows": (4,)}) != ref
    assert run(**{**base, "ret_windows": (2, 4)}) != ref
    assert run(**{**base, "p_cs_windows": (3,)}) != ref


def test_p_cs_windows_must_be_subset_of_ret_windows():
    """`p_cs_windows` 不是 `ret_windows` 的子集就要**拋例外**，不得靜默取交集。

    初版取交集，於是窄化 `ret_windows` 而忘了窄化 `p_cs_windows` 時，`excess` 與 `p_cs`
    整組無聲消失——而 `p_cs` 下游接過熱旗標（`score/stock.py:overheated`），缺了就一路變 None。
    2026-09-12 複驗抓到：這個陷阱**當時已經在自家測試上發作**（`ret_windows=(2,)` 配預設
    `p_cs_windows`，那幾支測試只驗廣度所以照樣綠）。
    """
    with pytest.raises(ValueError, match="子集"):
        DailyScanner(ma_windows=(3,), hl_windows=(3,), ret_windows=(2,))          # 預設 p_cs 10/20/60
    with pytest.raises(ValueError, match="子集"):
        DailyScanner(ret_windows=(5, 10), p_cs_windows=(10, 60))
    DailyScanner(ret_windows=(2, 3), p_cs_windows=(3,))                            # 子集 → 放行
    DailyScanner()                                                                 # 預設彼此相容


def test_narrowed_windows_still_emit_excess_and_p_cs():
    """窄化視窗後仍要產出 `excess`／`p_cs`——這是複驗抓到的迴歸本體。

    e416b70 的 `ret_windows=(2,)` 會產出 `('twse', 2)`；4d97c1e 的靜默交集讓它變空。
    """
    sc = DailyScanner(ma_windows=(3,), hl_windows=(3,), ret_windows=(2,), p_cs_windows=(2,))
    for i, d in enumerate(["2020-01-02", "2020-01-03", "2020-01-06"]):
        out = sc.push_day(d, [sd("1101", 10.0 + i), sd("1102", 20.0 - i)], {"twse": 100.0 + i})
    assert set(out.excess) == {("twse", 2)} and set(out.p_cs) == {("twse", 2)}
    assert set(out.p_cs[("twse", 2)]) == {"1101", "1102"}


def test_industry_survives_missing_index_but_excess_does_not():
    """指數缺值當天：產業中位／產業廣度照常，`excess`／`p_cs` 缺。

    釘住 `push_day` docstring 這一句與程式一致（複驗抓到敘述沿用改口徑前的舊語意）。
    """
    import inspect

    sc = DailyScanner(ma_windows=(2,), hl_windows=(2,), ret_windows=(1,), p_cs_windows=(1,))
    sc.push_day("2020-01-02", [sd("1101", 10.0), sd("1102", 20.0)], {"twse": 100.0})
    out = sc.push_day("2020-01-03", [sd("1101", 11.0), sd("1102", 21.0)], {"twse": None})
    assert out.excess == {} and out.p_cs == {}
    assert [a.n for a in out.industry] == [2] and len(out.industry_breadth) == 1
    # 敘述要跟著程式走（複驗抓到 docstring 沿用改口徑前的舊語意）。這裡釘**肯定句**而非
    # 「舊句不存在」——沿革註記本來就會原樣引用那句舊話，用否定式會把註記本身判成違規。
    doc = inspect.getdoc(DailyScanner.push_day)
    assert "產業中位數與產業廣度照常產出" in doc


# ---------------------------------------------------------------------------
# 窗長參數：把「不報錯、只安靜產出垃圾」的整類組合擋在建構時（2026-09-12 三驗）
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("kw, exc, why", [
    ({"ma_windows": "20"}, TypeError, "字串是 iterable，'20' 會被解析成 (0, 2)"),
    ({"hl_windows": "20"}, TypeError, "同上"),
    ({"ret_windows": "20", "p_cs_windows": ()}, TypeError, "同上"),
    ({"ma_windows": (-3,)}, ValueError, "負窗長 → sum([])/-3 = -0.0 → 每檔都判站上"),
    ({"ma_windows": (0,)}, ValueError, "0 窗長 → ZeroDivisionError，且要在建構時就炸"),
    ({"ma_windows": (1,)}, ValueError, "MA_1＝當日收盤 → above 恆 0，靜默無效"),
    ({"hl_windows": (1,)}, ValueError, "n≤1 沒有可比對象 → 計數恆 0 但鍵照樣輸出"),
    ({"ret_windows": (0,), "p_cs_windows": (0,)}, ValueError, "0 日報酬恆 0 → p_cs 全 50.0"),
    ({"ret_windows": (2.7,), "p_cs_windows": (2,)}, TypeError, "浮點被靜默截成 2，還會通過子集檢查"),
    ({"ma_windows": ()}, ValueError, "全空只會在 _maxlen 的 max() 才炸，訊息看不出是誰"),
    ({"ret_windows": (), "p_cs_windows": ()}, ValueError, "同上"),
])
def test_window_params_reject_silent_garbage(kw, exc, why):
    """這些值全都**不會報錯、只會安靜地產出垃圾或什麼都不產**——所以必須在建構時擋下。

    來源＝2026-09-12 第三輪複驗對 `ma_windows`／`hl_windows`／`ret_windows` 的窮舉實測；
    前兩輪只守住 `p_cs_windows ⊆ ret_windows` 一條。
    """
    base = {"ma_windows": (3,), "hl_windows": (3,), "ret_windows": (2,), "p_cs_windows": (2,)}
    with pytest.raises(exc):
        DailyScanner(**{**base, **kw})


def test_empty_p_cs_windows_is_the_explicit_way_to_skip():
    """`p_cs_windows=()` 是「這趟不算 P_cs」的明示寫法，允許；與靜默算成空集合不同。"""
    sc = DailyScanner(ma_windows=(3,), hl_windows=(3,), ret_windows=(2,), p_cs_windows=())
    for i, d in enumerate(["2020-01-02", "2020-01-03", "2020-01-06"]):
        out = sc.push_day(d, [sd("1101", 10.0 + i), sd("1102", 20.0 - i)], {"twse": 100.0 + i})
    assert out.p_cs == {} and out.excess == {}
    assert out.breadth["twse"].n_stocks == 2                 # 廣度照常
    assert [a.window for a in out.industry] == [2]           # 產業聚合照常（走 ret_windows）


def test_numpy_integer_windows_are_accepted():
    """numpy 整數要收（呼叫端從 numpy 算出窗長很自然）；bool 不收（True 會變成 1）。"""
    import numpy as np
    sc = DailyScanner(ma_windows=(np.int64(3),), hl_windows=(3,), ret_windows=(2,), p_cs_windows=(2,))
    assert sc.ma_windows == (3,) and all(isinstance(n, int) for n in sc.ma_windows)
    with pytest.raises(TypeError):
        DailyScanner(ma_windows=(True,), hl_windows=(3,), ret_windows=(2,), p_cs_windows=(2,))


def test_string_windows_says_it_is_a_string():
    """傳字串時，訊息要點名「字串」，不能只說「必須是整數」。

    型別檢查本來就會擋下逐字元拿到的 `'2'`（突變實證：只刪字串那一行，全套仍綠），
    所以這條守的是**可讀性**——第 12 項的驅動腳本從 argv 拿窗長，錯誤訊息說不說得出
    「你傳了字串」差很多。
    """
    base = {"ma_windows": (3,), "hl_windows": (3,), "ret_windows": (2,), "p_cs_windows": (2,)}
    for key in ("ma_windows", "hl_windows", "ret_windows"):
        with pytest.raises(TypeError, match="字串"):
            DailyScanner(**{**base, key: "20"})
