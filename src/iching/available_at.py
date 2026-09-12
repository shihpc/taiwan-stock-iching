"""基本面資料的 `available_at`（B2.1）——**純函式**，不 import sqlite3。

**為什麼不用資料裡的時戳**（2026-09-12 裁定，實測驅動）：`raw_month_revenue.create_time`
83% 是空字串，非空者是**回填時戳**（2019-06 的營收列寫著 `2026-05-19`），不是公布日；
`raw_financial_statements` **完全沒有公布日欄位**，只有期別末日。故一律以**法定公告申報期限**
推算，並以「該期限之後第一個台北交易日」作為可用日。

**方向是保守的**：法定期限是「最晚」必須公布之日，實際多半更早，所以本規則只會讓訊號**延後**、
不會提前——不會產生前視偏誤。代價是基本面訊號系統性延遲，報告須揭露。

**法源與適用對象**（2026-09-12 查證，出處記於 `docs/pre-registration.md`）：

| | 一般股 | 金融股 |
|---|---|---|
| 月營收 | 次月 **10** 日（證交法 36 I③） | 次月 **15** 日 |
| Q1／Q2／Q3 | 季後 **45** 日（證交法 36 I②） | 季後 **2 個月** |
| 年報 | 年後 **3 個月**（證交法 36 I①） | 年後 **3 個月** |

**金融桶取該桶最晚的那一版**：`industry_category` 只有 `金融保險`（46 檔 twse）與 `金融業`
（10 檔 tpex）一個桶，**分不出保險／銀行／金控**，而三者期限不同（保險月營收 2026-02 起 15 日、
金控 Q1/Q3 兩個月）。取最晚是唯一不會前視的做法；銀行與金控因此被多延幾天，方向保守。
**年報兩類相同**：所有例外（≥100 億 75 日、金融保險業 3/16、保險業 FY2022 起 75 日）都讓期限
**提前**，3/31 本來就是最晚的。

**刻意未實作**（缺資料，一律退到上表的保守值，凍結時逐項記在登錄書）：
①實收資本額 ≥100 億者 FY2022 起年報 75 日——我們沒有實收資本額；
②保險業 vs 銀行／金控 的區分——`industry_category` 只有一個金融桶。
"""
from __future__ import annotations

import bisect
import datetime as dt

MONTHLY_DAY_GENERAL = 10
MONTHLY_DAY_FINANCIAL = 15
QUARTER_DAYS_GENERAL = 45          # 證交法 36 I②：季終了後 45 日
QUARTER_MONTHS_FINANCIAL = 2       # 金融桶最晚：季終了後 2 個月
ANNUAL_MONTHS = 3                  # 證交法 36 I①：會計年度終了後 3 個月


def _add_months(d: dt.date, n: int) -> dt.date:
    """加 n 個月，日超過該月天數時取該月最後一日（1 月 31 日 + 1 月 → 2 月 28/29 日）。"""
    y, m = divmod(d.year * 12 + (d.month - 1) + n, 12)
    m += 1
    # 該月最後一日
    last = (dt.date(y + (m == 12), 1 if m == 12 else m + 1, 1) - dt.timedelta(days=1)).day
    return dt.date(y, m, min(d.day, last))


def _months_after_period(period_end: dt.date, n: int) -> dt.date:
    """期別末日之後 n 個月的**法定末日**，依民法第 120／121 條的期間算法。

    始日不計入（民法 120 II）→ 起算日＝期別末日次日；以月定期間者，末日＝最後之月「與起算日
    相當日」之**前一日**（民法 121 II）。例：6/30 → 起算 7/1 → +2 月得 9/1 → 末日 **8/31**。

    **初版寫成日曆日加法（`6/30 + 2 月 = 8/30`），比實際早一天＝前視方向的錯。**
    2026-09-12 對照證交所公告抓出：115 年 Q2「金融保險業及第一上市公司為 115 年 8 月 31 日」。
    年報 12/31 → 起算 1/1 → +3 月得 4/1 → 末日 3/31，與公告相符（舊寫法碰巧也對，故未先暴露）。
    """
    start = period_end + dt.timedelta(days=1)
    return _add_months(start, n) - dt.timedelta(days=1)


def monthly_revenue_deadline(revenue_year: int, revenue_month: int, *, is_financial: bool) -> dt.date:
    """月營收的法定公告申報期限（次月 10 日／金融桶 15 日）。"""
    y, m = (revenue_year + 1, 1) if revenue_month == 12 else (revenue_year, revenue_month + 1)
    return dt.date(y, m, MONTHLY_DAY_FINANCIAL if is_financial else MONTHLY_DAY_GENERAL)


def financial_report_deadline(period_end: str | dt.date, *, is_financial: bool) -> dt.date:
    """季報／年報的法定公告申報期限。`period_end` 為期別末日（`2026-06-30` 這種）。

    以**月份**判斷期別：12 月＝年報（3 個月）；3／6／9 月＝季報。
    其他月份 raise——FinMind 的 `date` 只會是四個季末，出現別的值代表資料形狀變了。
    """
    d = period_end if isinstance(period_end, dt.date) else dt.date.fromisoformat(str(period_end))
    if d.month == 12:
        return _months_after_period(d, ANNUAL_MONTHS)
    if d.month not in (3, 6, 9):
        raise ValueError(f"期別末日的月份應為 3／6／9／12，得到 {period_end!r}")
    if is_financial:
        return _months_after_period(d, QUARTER_MONTHS_FINANCIAL)
    return d + dt.timedelta(days=QUARTER_DAYS_GENERAL)


def first_trading_day_on_or_after(deadline: dt.date, tpe_dates: list[str]) -> str | None:
    """該期限之後（含當日）的第一個台北交易日；超出日曆範圍回 None。

    這一步同時吃掉「假日順延」——期限落在週末或國定假日時，資料在下一個交易日才用得到。
    順延的**事實**有多個官方案例（FY2023 年報 3/31 週日→4/1、2025 Q2 8/31 週日→9/1），
    但公告未載明所依條文；「明文＝行政程序法 48 IV」屬推論，見登錄書。
    """
    i = bisect.bisect_left(tpe_dates, deadline.isoformat())
    return tpe_dates[i] if i < len(tpe_dates) else None
