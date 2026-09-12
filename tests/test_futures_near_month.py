"""期貨近月判定、基差與換月旗標（B1.5 族 B）。

背景（2026-09-12 Hetzner 實查 `raw_futures_daily` 37,800 列）：**49% 是跨月價差合約**，
`contract_date` 形如 `202009/202010`、價格是價差點數且可為負（樣本 close −63.0）。
不濾掉會算出負幾十點的假基差——這是本模組存在的主要理由。
"""
from __future__ import annotations

import datetime as dt
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from iching import futures as F  # noqa: E402


@pytest.mark.parametrize("ym,expect", [
    ((2020, 1), "2020-01-15"), ((2020, 9), "2020-09-16"),
    ((2021, 2), "2021-02-17"), ((2026, 8), "2026-08-19"), ((2026, 10), "2026-10-21"),
])
def test_third_wednesday(ym, expect) -> None:
    """TX 最後交易日＝交割月份第三個星期三（期交所契約規格，2026-09-12 逐字覆核）。"""
    assert F.third_wednesday(*ym).isoformat() == expect
    assert F.third_wednesday(*ym).weekday() == 2          # 週三
    assert 15 <= F.third_wednesday(*ym).day <= 21         # 第三個週三必落在 15–21 日


def test_spread_contracts_are_rejected() -> None:
    """跨月價差合約不是單月契約——這是 49% 的列，漏濾就是災難。"""
    assert F.is_outright("202001")
    assert not F.is_outright("202009/202010")
    assert not F.is_outright("") and not F.is_outright(None) and not F.is_outright("2020")
    with pytest.raises(ValueError):
        F.contract_last_trading_day("202009/202010")


def test_near_month_rolls_the_day_after_settlement() -> None:
    """結算日當天該契約仍交易到 13:30，故當日仍是近月；**隔一日**才換月。"""
    cs = ["202001", "202002", "202003"]
    assert F.near_month("2020-01-14", cs) == "202001"
    assert F.near_month("2020-01-15", cs) == "202001"     # 結算日當天
    assert F.near_month("2020-01-16", cs) == "202002"     # 隔天換月


def test_near_month_ignores_spreads_in_the_candidate_list() -> None:
    assert F.near_month("2020-09-10", ["202009/202010", "202009", "202010"]) == "202009"
    assert F.near_month("2020-09-10", ["202009/202010"]) is None


def test_basis_ratio_and_denominator_zero() -> None:
    assert F.basis_ratio(12100.0, 12000.0) == pytest.approx(100 / 12000)
    assert F.basis_ratio(11900.0, 12000.0) == pytest.approx(-100 / 12000)
    assert F.basis_ratio(None, 12000.0) is None
    assert F.basis_ratio(12100.0, None) is None
    assert F.basis_ratio(12100.0, 0) is None              # 分母無效，由呼叫端記 denominator_zero


def test_rolled_flag() -> None:
    assert F.rolled("202001", "202002") is True
    assert F.rolled("202001", "202001") is False
    assert F.rolled(None, "202001") is False              # 首日沒有前一日可比，不是換月
    assert F.rolled("202001", None) is False


def test_roll_happens_exactly_once_per_month_over_a_year() -> None:
    """把 2020 全年每個日曆日走一遍，換月次數必須恰為 **12**。

    初版把期望寫成 11（「1 月到 12 月共 11 次轉換」）——**那是我算錯，不是程式錯**：
    每個月的結算都在中旬（第三個星期三），12 月結算日 2020-12-16、隔天 12-17 就換到 202101，
    仍落在同一年內。故年內是 12 次：01-16→202002、02-20→202003、…、12-17→202101。
    每次換月的日期必須是「該月第三個星期三的次日」，本測試一併驗這點。"""
    cs = [f"2020{m:02d}" for m in range(1, 13)] + ["202101"]
    prev, rolls = None, 0
    d = dt.date(2020, 1, 1)
    while d <= dt.date(2020, 12, 31):
        cur = F.near_month(d.isoformat(), cs)
        if F.rolled(prev, cur):
            rolls += 1
            assert d - dt.timedelta(days=1) == F.contract_last_trading_day(prev), \
                f"{d} 換月，但前一日不是 {prev} 的最後交易日"
        prev = cur
        d += dt.timedelta(days=1)
    assert rolls == 12, f"2020 年換月 {rolls} 次，期望 12"
