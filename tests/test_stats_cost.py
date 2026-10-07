"""`iching.stats.cost`：個股成本釘值（S1-5）、TX 成本試算（S1-6）。期待值全部獨立寫死（標準庫手算一次）。"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

from iching.stats import cost

ROOT = Path(__file__).resolve().parents[1]

#: `fwd=0`、slip 0.2%：(1−0.002)(1−0.001425−0.003)/[(1.002)(1.001425)] − 1（與 `tests/test_rank_table.py` R1 同值）
ZERO_NET = -0.009810371517992134
ZERO_NET_SLIP01 = -0.007828005930709536
ZERO_NET_SLIP03 = -0.011788784232717564


def test_rates_pinned():
    assert (cost.FEE, cost.TAX, cost.SLIP_BASE) == (0.001425, 0.003, 0.002)
    assert cost.SLIP_GRID == (0.001, 0.002, 0.003) and cost.BORROW_BASE == 0.02 and cost.BORROW_GRID == (0.01, 0.02, 0.04)
    assert (cost.TX_TAX, cost.TX_MULT, cost.TX_C_BASE, cost.TX_S_BASE) == (0.00002, 200, 100.0, 1.0)
    assert cost.TX_C_GRID == (50.0, 100.0, 150.0) and cost.TX_S_GRID == (0.5, 1.0, 2.0, 4.0)


def test_net_ret_long_pinned():
    assert cost.net_ret_long(0.0) == ZERO_NET
    assert cost.net_ret_long(0.0, slip=0.002) == ZERO_NET
    assert cost.ZERO_FWD_NET == ZERO_NET
    assert cost.net_ret_long(0.0, slip=0.001) == ZERO_NET_SLIP01
    assert cost.net_ret_long(0.0, slip=0.003) == ZERO_NET_SLIP03
    for i in range(-10, 10):
        fwd = i * 0.37
        want = (1.0 + fwd) * 0.998 * (1.0 - 0.001425 - 0.003) / (1.002 * 1.001425) - 1.0
        assert cost.net_ret_long(fwd) == want, fwd
    assert abs(cost.net_ret_long(3.26) - 3.2182078173333535) < 1e-15


def test_rank_table_reexports_same_values():
    """`scripts/rank_table.py` 反向 import 後名稱與值一字不變。"""
    sys.path.insert(0, str(ROOT / "scripts"))
    import rank_table as RT
    assert RT.net_ret is not None and RT.net_ret(0.0) == ZERO_NET and RT.ZERO_FWD_NET == ZERO_NET
    assert (RT.FEE, RT.TAX, RT.SLIP) == (cost.FEE, cost.TAX, cost.SLIP_BASE)
    assert RT.FEE is cost.FEE and RT.TAX is cost.TAX
    assert RT.net_ret(1.234) == cost.net_ret_long(1.234)


def test_net_ret_short_pinned():
    # 無借券、fwd=0：(0.998)(0.995575) − (1.002)(1.001425)
    assert abs(cost.net_ret_short(0.0, 0.002, 0.0, 0) - (-0.009844000000000075)) < 1e-15
    # 年化 2%、30 曆日：借券費 0.02×30/365 ＝ 0.001643835616438356
    assert abs(cost.net_ret_short(0.0, 0.002, 0.02, 30) - (-0.011487835616438432)) < 1e-15
    assert abs((cost.net_ret_short(0.0, 0.002, 0.0, 0) - cost.net_ret_short(0.0, 0.002, 0.02, 30))
               - 0.001643835616438356) < 1e-15
    # 價跌 10%：正報酬 0.0888549493835615
    assert abs(cost.net_ret_short(-0.1, 0.002, 0.02, 30) - 0.0888549493835615) < 1e-15
    # 借券費隨年化率與曆日線性：4%×30 ＝ 2%×60
    assert abs(cost.net_ret_short(0.0, 0.002, 0.04, 30) - cost.net_ret_short(0.0, 0.002, 0.02, 60)) < 1e-15
    assert cost.DAYS_PER_YEAR == 365
    with pytest.raises(ValueError):
        cost.net_ret_short(0.0, cal_days=-1)


def test_tx_cost_p45000_matches_pre_registration():
    """§1.2.4 `:259-260`：契約金額 9,000,000；稅 180＋手續費 100＋滑價 200 ＝ 480 元／邊、0.00533%；來回 0.0107%。"""
    assert abs(cost.tx_side_ntd(45000) - 480.0) < 1e-9
    assert abs(cost.tx_cost_pct(45000) - 480 / 9_000_000) < 1e-18        # 5.3333e-05 ＝ 0.00533%
    assert abs(cost.tx_cost_pct(45000) - 5.333333333333333e-05) < 1e-18
    assert abs(cost.tx_roundtrip_pct(45000, 45000) - 960 / 9_000_000) < 1e-18  # 0.0107%
    assert round(cost.tx_roundtrip_pct(45000, 45000) * 100, 4) == 0.0107
    # 分項：稅 0.00200%、手續費 0.00111%、滑價 0.00222%
    assert abs(45000 * 200 * cost.TX_TAX - 180.0) < 1e-9
    assert round(100 / 9_000_000 * 100, 5) == 0.00111 and round(200 / 9_000_000 * 100, 5) == 0.00222
    # 敏感度端點：C=50／S=0.5 → 180+50+100=330；C=150／S=4 → 180+150+800=1130
    assert abs(cost.tx_side_ntd(45000, c=50.0, s=0.5) - 330.0) < 1e-9
    assert abs(cost.tx_side_ntd(45000, c=150.0, s=4.0) - 1130.0) < 1e-9


def test_tx_cost_pct_scales_with_price_and_rollover():
    # 手續費是固定金額：點位加倍，手續費佔比減半、稅率佔比不變（`:253-258`）
    hi, lo = cost.tx_cost_pct(90000, c=100.0, s=0.0), cost.tx_cost_pct(45000, c=100.0, s=0.0)
    assert abs((hi - cost.TX_TAX) * 2 - (lo - cost.TX_TAX)) < 1e-18
    # 轉倉＝再一次完整來回：同點位一次轉倉 → 1920/9e6
    assert abs(cost.tx_roundtrip_pct(45000, 45000, roll_prices=[45000]) - 1920 / 9_000_000) < 1e-18
    # 逐筆用當日點位：進場 45,000、平倉 50,000（稅 200 元）→ (480 + 500) / 9e6
    assert abs(cost.tx_roundtrip_pct(45000, 50000) - 980 / 9_000_000) < 1e-18
    with pytest.raises(ValueError):
        cost.tx_side_ntd(0)
