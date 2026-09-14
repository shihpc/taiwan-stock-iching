"""基本面 `available_at`（B2.1）——法定公告申報期限推算。

**對照組是證交所／金管會的實際公告日期**，不是我自己推的：
- 115 年 Q2：「金融保險業以外之本國上市公司為 115 年 8 月 14 日，金融保險業及第一上市公司為
  115 年 8 月 31 日」（TWSE 115/8/3 公告）
- 114 年度財報：一般 115/3/31（二）
初版用日曆日加法算「二個月」得 8/30，比公告早一天＝**前視方向的錯**，由這組對照抓出。
"""
from __future__ import annotations

import datetime as dt
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from iching import available_at as A  # noqa: E402

D = dt.date.fromisoformat


@pytest.mark.parametrize("period_end,general,financial", [
    ("2026-03-31", "2026-05-15", "2026-05-31"),
    ("2026-06-30", "2026-08-14", "2026-08-31"),   # ← 與 TWSE 115/8/3 公告逐字相符
    ("2026-09-30", "2026-11-14", "2026-11-30"),
    ("2026-12-31", "2027-03-31", "2027-03-31"),   # 年報兩類相同（例外都讓期限提前）
    ("2020-12-31", "2021-03-31", "2021-03-31"),
    ("2024-06-30", "2024-08-14", "2024-08-31"),
])
def test_financial_report_deadline_matches_official_announcements(period_end, general, financial) -> None:
    assert A.financial_report_deadline(period_end, is_financial=False) == D(general)
    assert A.financial_report_deadline(period_end, is_financial=True) == D(financial)


def test_two_month_uses_civil_code_counting_not_calendar_addition() -> None:
    """民法 120 II／121 II：始日不計入，末日＝相當日前一日。6/30 → 起算 7/1 → +2 月 9/1 → **8/31**。
    日曆日加法會得 8/30，早一天＝前視。這支專門釘住那個差別。"""
    assert A.financial_report_deadline("2026-06-30", is_financial=True) == D("2026-08-31")
    assert A._add_months(D("2026-06-30"), 2) == D("2026-08-30")   # 錯的算法長這樣


def test_leap_year_and_month_end_edge() -> None:
    assert A.financial_report_deadline("2019-12-31", is_financial=False) == D("2020-03-31")
    assert A._add_months(D("2020-01-31"), 1) == D("2020-02-29")   # 閏年
    assert A._add_months(D("2021-01-31"), 1) == D("2021-02-28")


def test_non_quarter_end_month_raises() -> None:
    """FinMind 的 date 只會是四個季末；出現別的月份代表資料形狀變了，要吵不要猜。"""
    with pytest.raises(ValueError):
        A.financial_report_deadline("2026-05-31", is_financial=False)


@pytest.mark.parametrize("y,m,general,financial", [
    (2020, 5, "2020-06-10", "2020-06-15"),
    (2020, 12, "2021-01-10", "2021-01-15"),   # 跨年
    (2026, 1, "2026-02-10", "2026-02-15"),
])
def test_monthly_revenue_deadline(y, m, general, financial) -> None:
    assert A.monthly_revenue_deadline(y, m, is_financial=False) == D(general)
    assert A.monthly_revenue_deadline(y, m, is_financial=True) == D(financial)


def test_first_trading_day_absorbs_holiday_rollover() -> None:
    """期限落在非交易日 → 下一個交易日才用得到。這一步同時吃掉「假日順延」。"""
    cal = ["2026-08-28", "2026-08-31", "2026-09-01", "2026-09-02"]
    assert A.first_trading_day_on_or_after(D("2026-08-31"), cal) == "2026-08-31"   # 當日就是交易日
    assert A.first_trading_day_on_or_after(D("2026-08-29"), cal) == "2026-08-31"   # 週六 → 下一個交易日
    assert A.first_trading_day_on_or_after(D("2026-08-30"), cal) == "2026-08-31"   # 週日
    assert A.first_trading_day_on_or_after(D("2026-09-03"), cal) is None           # 超出日曆範圍


def test_financial_bucket_is_never_earlier_than_general() -> None:
    """金融桶取該桶最晚的那一版——任何期別都不得早於一般股，否則就是對 56 檔金融股前視。"""
    for pe in ("2020-03-31", "2022-06-30", "2024-09-30", "2026-12-31"):
        assert (A.financial_report_deadline(pe, is_financial=True)
                >= A.financial_report_deadline(pe, is_financial=False))
    for y in (2020, 2023, 2026):
        for m in range(1, 13):
            assert (A.monthly_revenue_deadline(y, m, is_financial=True)
                    >= A.monthly_revenue_deadline(y, m, is_financial=False))
