"""個股池排除 DR（使用者 2026-09-10 裁定甲；`docs/P2-KICKOFF.md` §5 #25）：免 token、免網路。

兩條件取聯集：`industry_category=='存託憑證'` 或 4 碼且以 `91` 開頭（已下市 DR 不在今日快照的後備）。
與落地過濾 `config.is_warrant_code`（只砍權證）是兩件事——本檔只測池，落地在 tests/test_landing_filter.py。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from iching.universe import DR_CATEGORY, is_dr_code, is_pool_candidate, pool_from_info  # noqa: E402

# 使用者 2026-09-10 於 Hetzner 實查 `TaiwanStockInfo`：4 碼純數字非 00 的存託憑證恰 11 檔（本容器同日免 token 實打相符）
DR_4 = ("9101", "9102", "9103", "9104", "9105", "9106", "9110", "9136", "9151", "9157", "9188")


@pytest.mark.parametrize("sid", DR_4)
def test_eleven_four_digit_dr_excluded_by_category(sid):
    rows = [{"stock_id": sid, "type": "twse", "industry_category": DR_CATEGORY, "stock_name": "x-DR", "date": "2026-09-10"}]
    assert is_pool_candidate(sid, "twse")            # 形狀上本來符合裁定 #6（4 碼、非 00、twse）——正是問題所在
    assert is_dr_code(sid, DR_CATEGORY)
    assert pool_from_info(rows) == {}


@pytest.mark.parametrize("sid", DR_4)
def test_dr_shape_fallback_when_category_missing(sid):
    """已下市 DR 不在今日快照：industry_category 查不到（None／其他殘留值）仍由形狀規則排除。"""
    assert is_dr_code(sid, None)
    assert is_dr_code(sid, "電子工業")
    rows = [{"stock_id": sid, "type": "twse", "industry_category": None, "date": "2020-01-02"}]
    assert pool_from_info(rows) == {}


def test_dr_excluded_if_any_residual_row_is_dr():
    """同代號多列（殘留）只要任一列是存託憑證，整個代號不進池。以非 91 形狀的假代號測，確保走的是類別條件。"""
    rows = [{"stock_id": "2999", "type": "twse", "industry_category": DR_CATEGORY, "date": "2025-01-01"},
            {"stock_id": "2999", "type": "twse", "industry_category": "其他", "date": "2026-01-01"}]
    assert pool_from_info(rows) == {}


def test_ordinary_four_digit_stocks_unaffected():
    rows = [{"stock_id": "2330", "type": "twse", "industry_category": "半導體業", "date": "2026-09-10"},
            {"stock_id": "9802", "type": "twse", "industry_category": "其他", "date": "2026-09-10"},   # 9 開頭非 91
            {"stock_id": "9958", "type": "twse", "industry_category": "航運業", "date": "2026-09-10"},
            {"stock_id": "1101", "type": "twse", "industry_category": "水泥工業", "date": "2026-09-10"},
            {"stock_id": "6488", "type": "tpex", "industry_category": "半導體業", "date": "2026-09-10"}]
    assert set(pool_from_info(rows)) == {"2330", "9802", "9958", "1101", "6488"}
    for sid in ("2330", "9802", "9958", "1101", "6488"):
        assert not is_dr_code(sid, "半導體業")


def test_six_digit_dr_never_reaches_pool_and_shape_rule_is_four_digit_only():
    """6 碼 DR（910322 型）本來就不符 4 碼條件；形狀規則只管 4 碼——6 碼由 is_pool_candidate 擋，不靠 is_dr_code。"""
    assert not is_pool_candidate("910322", "twse")
    assert is_dr_code("910322", DR_CATEGORY)          # 類別條件仍為真（供其他讀取端使用）
    assert not is_dr_code("910322", None)             # 形狀後備限 4 碼
    assert not is_dr_code("91", None) and not is_dr_code("9101A", None)
