"""同日多產業 tie-break 三層（2026-09-12 裁定 #28）。

背景：Hetzner 實查 603 檔同日多列，490 檔由傘狀排除解決、113 檔落到**純字串序**那層
（唯一沒有語意依據的一層）。113 檔全部是兩個家族：`化學生技醫療` 母類殘留 83 檔、
`創新板股票` 板別標籤 30 檔。本裁定把前者加進傘狀清單、後者另立非產業清單。

守門重點：
- 兩個集合**各自**都要有測試，拿掉任一個都要有案例變紅（避免「守門守不住」）。
- 兩層的**降級**（剔完為空就退回）也要守——只掛板別／只掛母類的股票不能變成沒有分類。
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from iching import universe as U  # noqa: E402

D = "2026-09-09"


def _row(cat: str, *, type_: str = "twse", name: str = "某公司", date: str = D) -> dict:
    return {"stock_id": "1234", "date": date, "type": type_,
            "industry_category": cat, "stock_name": name}


def _pick_cat(*cats: str, type_: str = "twse") -> str:
    return U._pick([_row(c, type_=type_) for c in cats])["industry_category"]


# ---- ① 母類：化學生技醫療（裁定 #28 新增，實查 54 檔受影響）----------------------------
def test_biotech_umbrella_loses_to_finer() -> None:
    """實查 54 檔：加入前靠字串序取到母類，加入後應取子類。"""
    assert _pick_cat("化學生技醫療", "生技醫療業") == "生技醫療業"


def test_chemical_child_still_wins_unchanged() -> None:
    """實查 29 檔：加入前後都取 `化學工業`，本次不得改變它們。"""
    assert _pick_cat("化學工業", "化學生技醫療") == "化學工業"


def test_electronics_umbrella_still_loses() -> None:
    """原有的 `電子工業` 行為不得回歸（實查 490 檔靠它解決）。"""
    assert _pick_cat("電子工業", "半導體業") == "半導體業"


# ---- ② 非產業標籤：創新板股票 -------------------------------------------------------
def test_board_label_loses_to_real_industry() -> None:
    assert _pick_cat("創新板股票", "綠能環保") == "綠能環保"


def test_board_label_and_umbrella_both_excluded() -> None:
    """實查 7 檔：板別＋母類＋子類三者並存，兩層都要作用才會落到子類。"""
    assert _pick_cat("創新板股票", "化學生技醫療", "生技醫療業") == "生技醫療業"


def test_board_label_with_two_electronics_levels() -> None:
    """實查 1 檔：`其他電子業｜創新板股票｜電子工業`。"""
    assert _pick_cat("其他電子業", "創新板股票", "電子工業") == "其他電子業"


# ---- 降級：剔完為空要退回，不能變成沒有分類 -----------------------------------------
def test_board_label_alone_falls_back() -> None:
    assert U._pick([_row("創新板股票"), _row("創新板股票", name="乙公司")])["industry_category"] == "創新板股票"


def test_umbrella_alone_falls_back() -> None:
    """實查 `電子工業` 31 檔、`化學生技醫療` 8 檔只掛母類，是資料限制不是規則缺陷。"""
    assert _pick_cat("電子工業", "電子工業") == "電子工業"
    assert _pick_cat("化學生技醫療", "化學生技醫療") == "化學生技醫療"


def test_exclusion_order_matters_when_only_board_and_umbrella() -> None:
    """兩層的**順序**在這個退化組合下才看得出差別，`_pick` docstring 的「順序不可調換」靠它守。

    `{創新板股票, 電子工業}`（只有板別＋母類、沒有真產業）：
    - 先排非產業：剩 `電子工業` → 傘狀排完為空退回 → **電子工業**（粗，但確實是產業）
    - 先排傘狀：剩 `創新板股票` → 非產業排完為空退回 → **創新板股票**（根本不是產業）
    目前的實查資料沒有這個組合（29 檔創新板都另有真產業），但那是資料現況、不是保證。
    """
    assert _pick_cat("創新板股票", "電子工業") == "電子工業"


# ---- 集合內容本身（改了就要自覺）-----------------------------------------------------
def test_category_sets_are_pinned() -> None:
    assert U.UMBRELLA_CATEGORIES == frozenset({"電子工業", "化學生技醫療"})
    assert U.NON_INDUSTRY_CATEGORIES == frozenset({"創新板股票"})
    assert not (U.UMBRELLA_CATEGORIES & U.NON_INDUSTRY_CATEGORIES), "兩個集合不得重疊，否則排除順序失去意義"
