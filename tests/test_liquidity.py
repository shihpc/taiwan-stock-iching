"""`iching.liquidity`：60 日滾動 ADV 與可交易（排名）池。免 token 免網路。

規格＝`docs/pre-registration.md` §1.1（裁定 T11 乙，已凍結）。守的是三類無聲錯誤：
**呼叫順序造成的 look-ahead**、**停牌日怎麼算**、**滾動累加破壞兩層 parity**。
"""
from __future__ import annotations

import random
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from iching.liquidity import ADV_THRESHOLD_TWD, ADV_WINDOW, AdvTracker, adv  # noqa: E402


def test_frozen_constants_match_pre_registration():
    """門檻與窗長是**凍結值**（§2 候選表第 3 列「1（不掃描）」），改動＝改預先登錄書。"""
    assert ADV_WINDOW == 60
    assert ADV_THRESHOLD_TWD == 3e7                    # 0.3 億元／日
    text = (ROOT / "docs" / "pre-registration.md").read_text(encoding="utf-8")
    assert "0.3 億元／日" in text and "60 個交易日" in text


def test_adv_helper():
    assert adv([1.0, 2.0, 3.0]) == pytest.approx(2.0)
    assert adv([]) is None


# ---------------------------------------------------------------------------
# PIT：呼叫順序就是全部
# ---------------------------------------------------------------------------
def test_eligible_reflects_only_pushed_days():
    """`eligible()` 只吃到「已 push 的最後一天」。先取後推＝PIT；先推後取＝look-ahead。

    這條用一個**只在 T 日爆量**的股票來分辨：正確順序下它 T 日**不**在池裡（T−1 為止還很冷），
    反過來就會在。反了不會報錯，所以只有測試擋得住。
    """
    t = AdvTracker(window=3, threshold=100.0)
    cold = {"9999": 1.0}
    for d in ("2020-01-02", "2020-01-03", "2020-01-06"):
        t.push_day(d, cold)
    pool_for_t = t.eligible()                       # T＝01-07，此時只吃到 01-06
    assert pool_for_t == frozenset()
    t.push_day("2020-01-07", {"9999": 1e9})         # T 日爆量
    assert t.eligible() == frozenset({"9999"})      # 這是 T+1 該用的池，不是 T 的


def test_untraded_day_counts_as_zero():
    """停牌／零成交日**計入分母、成交值算 0**——與 `scan.py` 的「視窗只取有效收盤」刻意不同。

    若改成「只算有成交的日子」，一檔停牌大半年的股票會因為樣本只剩熱絡那幾天而看起來很好買。
    """
    t = AdvTracker(window=4, threshold=100.0)
    t.push_day("2020-01-02", {"1101": 400.0, "1102": 400.0})
    t.push_day("2020-01-03", {"1101": 400.0, "1102": 400.0})
    t.push_day("2020-01-06", {"1101": 400.0})           # 1102 停牌
    t.push_day("2020-01-07", {"1101": 400.0})           # 1102 停牌
    assert t.adv_of("1101") == pytest.approx(400.0)
    assert t.adv_of("1102") == pytest.approx(200.0)     # (400+400+0+0)/4，不是 400
    assert t.eligible() == frozenset({"1101", "1102"})  # 200 仍過 100
    t.push_day("2020-01-08", {"1101": 400.0})
    assert t.adv_of("1102") == pytest.approx(100.0)     # (400+0+0+0)/4
    t.push_day("2020-01-09", {"1101": 400.0})
    assert t.adv_of("1102") == pytest.approx(0.0)
    assert t.eligible() == frozenset({"1101"})


def test_partial_window_is_not_eligible():
    """視窗未滿一律不合格，**不是**用不足天數硬算平均（§1.1 快照「至少 60 個交易日者」）。"""
    t = AdvTracker(window=3, threshold=100.0)
    t.push_day("2020-01-02", {"1101": 1e9})
    assert t.adv_of("1101") is None and t.eligible() == frozenset()
    assert t.n_tracked == 1 and t.n_ready == 0
    t.push_day("2020-01-03", {"1101": 1e9})
    assert t.eligible() == frozenset() and t.n_ready == 0
    t.push_day("2020-01-06", {"1101": 1e9})
    assert t.eligible() == frozenset({"1101"}) and t.n_ready == 1


def test_warmup_is_visible_not_silent():
    """暖機期排名池會是空的——那是正確行為，但必須**看得見**。"""
    t = AdvTracker(window=60, threshold=1.0)
    for i in range(59):
        t.push_day(f"2020-{1 + i // 28:02d}-{1 + i % 28:02d}", {f"{1000 + j}": 1e9 for j in range(5)})
    assert t.eligible() == frozenset()
    assert t.n_tracked == 5 and t.n_ready == 0          # 兩個數字分得出「沒資料」與「還沒滿窗」


def test_threshold_is_inclusive():
    """§1.1 寫「**≥** 0.3 億元／日」——剛好等於門檻要算合格。"""
    t = AdvTracker(window=2, threshold=100.0)
    t.push_day("2020-01-02", {"1101": 100.0, "1102": 99.999999})
    t.push_day("2020-01-03", {"1101": 100.0, "1102": 99.999999})
    assert t.eligible() == frozenset({"1101"})


# ---------------------------------------------------------------------------
# 兩層 parity：ADV 不得依賴掃描起點
# ---------------------------------------------------------------------------
def test_adv_is_independent_of_scan_start():
    """同樣的最後 60 天，**不管前面推過多少天**，ADV 必須逐位相同。

    這是兩層 parity 的要害（`spec/P1-B3-replay.md` §B3.2）：Hetzner 從 2020 一路掃，
    每日班只重算近期視窗。若 ADV 用滾動累加（加新值減舊值），浮點誤差會隨掃描起點而異，
    **落在門檻線上的股票可能一邊進池一邊不進**。改用「當窗 60 筆重新加總」就與歷史長度無關。
    """
    random.seed(20260913)
    early = [random.random() * 1e9 for _ in range(140)]
    tail = [random.random() * 1e9 for _ in range(60)]

    def run(vals):
        t = AdvTracker(window=60, threshold=ADV_THRESHOLD_TWD)
        for i, v in enumerate(vals):
            t.push_day(f"2020-{1 + i // 28:02d}-{1 + i % 28:02d}", {"1101": v})
        return t.adv_of("1101")

    long_hist, short_hist = run(early + tail), run(tail)
    assert long_hist == short_hist, f"ADV 依賴掃描起點：{long_hist!r} vs {short_hist!r}"


def test_push_day_must_be_ascending():
    """重複或回頭的日期會讓某一天被算兩次，無聲地墊高 ADV。"""
    t = AdvTracker(window=3)
    t.push_day("2020-01-03", {"1101": 1.0})
    with pytest.raises(ValueError, match="升冪"):
        t.push_day("2020-01-03", {"1101": 1.0})
    with pytest.raises(ValueError, match="升冪"):
        t.push_day("2020-01-02", {"1101": 1.0})


def test_input_order_does_not_matter():
    """`amounts` 的插入序不得影響結果（dict 有序，浮點加總不可交換）。"""
    random.seed(7)
    ids = [f"{1000 + j}" for j in range(30)]
    days = [{s: random.random() * 1e9 for s in ids} for _ in range(70)]

    def run(reverse):
        t = AdvTracker(window=60, threshold=ADV_THRESHOLD_TWD)
        for i, day in enumerate(days):
            items = sorted(day.items(), reverse=reverse)
            t.push_day(f"2020-{1 + i // 28:02d}-{1 + i % 28:02d}", dict(items))
        return {s: t.adv_of(s) for s in ids}, t.eligible()

    assert run(False) == run(True)


@pytest.mark.parametrize("kw", [{"window": 1}, {"window": 0}, {"window": -3}, {"window": 2.5},
                                {"window": True}, {"threshold": 0}, {"threshold": -1.0}])
def test_constructor_rejects_degenerate_config(kw):
    with pytest.raises(ValueError):
        AdvTracker(**kw)


def test_liquidity_module_never_imports_sqlite3():
    """兩層 parity 硬約束的實作手段：特徵層純函式不碰 DB（用 AST，不用 grep）。"""
    import ast
    tree = ast.parse((ROOT / "src" / "iching" / "liquidity.py").read_text(encoding="utf-8"))
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module.split(".")[0])
    assert "sqlite3" not in names, f"liquidity.py 匯入了 {sorted(names)}"
