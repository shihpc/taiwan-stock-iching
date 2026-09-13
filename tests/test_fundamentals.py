"""第 13 項 13b：`iching.fundamentals`（純函式）——對應表、PIT 可得日、期別序、金融路徑、產業中位數。

對應表正本 `docs/P2-REPLAY-PLAN.md` §9；可得日規則 `src/iching/available_at.py`（法定期限，T ≥ 可用日才看得到）。
"""
from __future__ import annotations

import datetime as dt
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from iching import fundamentals as FU  # noqa: E402
from iching.available_at import financial_report_deadline, monthly_revenue_deadline  # noqa: E402
from iching.score.stock import revenue_yoy_3m  # noqa: E402

CAL = [d.isoformat() for d in (dt.date(2019, 1, 1) + dt.timedelta(days=i) for i in range(0, 900)) if d.weekday() < 5]


def _monthly(start_y: int, start_m: int, n: int, base: float = 100.0, step: float = 2.0):
    out = []
    for k in range(n):
        y, m = start_y + (start_m - 1 + k) // 12, (start_m - 1 + k) % 12 + 1
        out.append((y, m, base + step * k))
    return out


def _quarters(periods, eps0=1.0):
    rows = []
    for j, p in enumerate(periods):
        rows += [(p, "EPS", eps0 + 0.1 * j), (p, "GrossProfit", 30.0 + j), (p, "Revenue", 100.0),
                 (p, "PreTaxIncome", 10.0 + j), (p, "IncomeAfterTaxes", 8.0), (p, "OperatingExpenses", 1.0)]
    return rows


def test_period_index_and_rejects_non_quarter_end():
    assert FU.period_index("2019-12-31") - FU.period_index("2019-09-30") == 1
    assert FU.period_index("2020-06-30") - FU.period_index("2019-06-30") == 4
    with pytest.raises(FU.FundamentalsError):
        FU.period_index("2020-05-31")


def test_available_dates_follow_statutory_deadlines_and_calendar():
    sf = FU.build_stock("1101", "水泥工業", _monthly(2019, 1, 15), _quarters(["2019-03-31", "2019-06-30", "2019-12-31"]), {}, CAL)
    # 月營收：次月 10 日（一般業）；2019-01 → 2019-02-10 是週日 → 02-11
    assert monthly_revenue_deadline(2019, 1, is_financial=False) == dt.date(2019, 2, 10)
    assert sf.monthly_available[0] == "2019-02-11"
    # 季報：Q1 → 5/15；年報 → 3/31（2020-03-31 週二）
    assert financial_report_deadline("2019-03-31", is_financial=False) == dt.date(2019, 5, 15)
    assert sf.period_available == ["2019-05-15", "2019-08-14", "2020-03-31"]
    # PIT：可用日前一天看不到、當天看得到（訊號日＝T 收盤後）
    assert sf.period_asof("2019-05-14") is None and sf.period_asof("2019-05-15") == "2019-03-31"
    assert sf.period_asof("2020-03-30") == "2019-06-30" and sf.period_asof("2020-03-31") == "2019-12-31"
    assert [ym for ym, _ in sf.monthly_asof("2019-02-10")] == [] and [ym for ym, _ in sf.monthly_asof("2019-02-11")] == ["2019-01"]
    # 金融桶：15 日／2 個月／年報 3 個月
    ff = FU.build_stock("2882", "金融保險", _monthly(2019, 1, 3), _quarters(["2019-03-31", "2019-12-31"]), {}, CAL)
    assert ff.is_financial and ff.monthly_available[0] == "2019-02-15" and ff.period_available == ["2019-05-31", "2020-03-31"]


def test_fundamentals_dict_mapping_ly_prev_and_pretax_fallback():
    periods = ["2018-09-30", "2018-12-31", "2019-03-31", "2019-06-30", "2019-09-30"]
    q = _quarters(periods)
    px = {"2019-09-30": 55.0, "2019-06-30": 50.0}
    sf = FU.build_stock("1101", "水泥工業", [], q, px, CAL)
    f = FU.fundamentals_dict(sf, "2019-11-14")            # 2019-09-30 可用日＝11-14
    assert f["eps"] == pytest.approx(1.4) and f["eps_ly"] == pytest.approx(1.0)          # 前 4 期＝2018-09-30
    assert f["gross_margin"] == pytest.approx(34.0) and f["gross_margin_prev_q"] == pytest.approx(33.0)
    assert f["pretax_income"] == pytest.approx(14.0) and f["pretax_income_ly"] == pytest.approx(10.0)
    assert f["price_at_period_end"] == 55.0 and f["equity"] is None and f["equity_prev_q"] is None
    assert set(f) == set(FU.FUND_KEYS)
    f2 = FU.fundamentals_dict(sf, "2019-11-13")           # 前一天：最近可得期＝2019-06-30，其 ly（2018-06-30）不存在
    assert f2["eps"] == pytest.approx(1.3) and f2["eps_ly"] is None and f2["price_at_period_end"] == 50.0
    assert FU.fundamentals_dict(sf, "2018-12-31") is None   # 日曆起點之前：尚無任何可得期
    assert sf.period_available[0] == CAL[0]                  # 期限早於日曆起點的期別，可用日夾到日曆第一天（回測起點前的資料一開始就可得）
    # 金融業寫法：PreTaxIncome 缺、IncomeBeforeIncomeTax 有 → 退到後者；兩者同期並存取 PreTaxIncome
    q2 = [("2019-03-31", "IncomeBeforeIncomeTax", 7.0), ("2019-03-31", "EPS", 0.5),
          ("2019-06-30", "PreTaxIncome", 9.0), ("2019-06-30", "IncomeBeforeIncomeTax", 999.0), ("2019-06-30", "Revenue", 0.0),
          ("2019-06-30", "GrossProfit", 5.0)]
    ff = FU.build_stock("2882", "金融業", [], q2, {}, CAL)
    g1 = FU.fundamentals_dict(ff, "2019-05-31")
    assert g1["pretax_income"] == 7.0 and g1["gross_margin"] is None
    assert ff.period_available == ["2019-05-31", "2019-09-02"]                 # 8/31 週六 → 9/2
    assert FU.fundamentals_dict(ff, "2019-08-31")["pretax_income"] == 7.0       # 週末前一天仍看到 Q1
    g2 = FU.fundamentals_dict(ff, "2019-09-02")
    assert g2["pretax_income"] == 9.0 and g2["gross_margin"] is None            # Revenue ≤ 0 → None，不是除以零


def test_types_outside_mapping_are_ignored_and_duplicates_last_wins():
    q = [("2019-03-31", "EPS", 1.0), ("2019-03-31", "EPS", 2.0), ("2019-03-31", "OTHNOE", 5.0)]
    sf = FU.build_stock("1101", None, [(2019, 1, 10.0), (2019, 1, 11.0)], q, {}, CAL)
    assert sf.quarters["2019-03-31"] == {"EPS": 2.0} and sf.monthly == [("2019-01", 11.0)]
    assert sf.is_financial is False and FU.is_financial(None) is False


def test_industry_median_and_n_use_each_stocks_own_asof_latest_month():
    a = FU.build_stock("1101", "水泥工業", _monthly(2019, 1, 15, 100.0, 2.0), [], {}, CAL)
    b = FU.build_stock("1102", "水泥工業", _monthly(2019, 1, 15, 100.0, 5.0), [], {}, CAL)
    c = FU.build_stock("2330", "半導體業", _monthly(2020, 1, 3, 500.0, 0.0), [], {}, CAL)   # 不足 6 個月 → Missing
    br = FU.FundamentalsBridge({"1101": a, "1102": b, "2330": c}, {"1101": "水泥工業", "1102": "水泥工業", "2330": "半導體業"})
    T = "2020-04-10"                                               # 2020-03 營收可用日
    ya = revenue_yoy_3m(dict(a.monthly_asof(T)), "2020-03", 0, 3)
    yb = revenue_yoy_3m(dict(b.monthly_asof(T)), "2020-03", 0, 3)
    r = br.inputs_for("1101", T)
    assert r["industry_revenue_n"] == 2 and r["industry_median_3m_yoy"] == pytest.approx((ya + yb) / 2)
    assert r["monthly_revenue"][-1] == ("2020-03", 100.0 + 2.0 * 14) and r["fundamentals"] is None
    r2 = br.inputs_for("2330", T)
    assert r2["industry_revenue_n"] is None and r2["industry_median_3m_yoy"] is None and len(r2["monthly_revenue"]) == 3
    assert br.inputs_for("9999", T) == {"monthly_revenue": None, "industry_median_3m_yoy": None, "industry_revenue_n": None, "fundamentals": None}
    assert br.coverage(T) == {"with_monthly": 3, "with_quarter": 0, "stocks": 3}
    # 同一天第二次查不重算（快取 day）
    assert br._day == T and br.inputs_for("1102", T)["industry_revenue_n"] == 2


def test_extend_calendar_appends_weekdays_only_after_last_date():
    cal = ["2020-04-20"]
    out = FU.extend_calendar(cal, months_ahead=1)
    assert out[0] == "2020-04-20" and out[1] == "2020-04-21" and all(dt.date.fromisoformat(d).weekday() < 5 for d in out)
    assert out[-1] >= "2020-05-18" and FU.extend_calendar([]) == []
    # 可用日落在日曆之外 → '9999-12-31'（永遠不可得），不是 None 炸掉 bisect
    sf = FU.build_stock("1101", None, [(2030, 1, 1.0)], [], {}, ["2020-01-02"])
    assert sf.monthly_available == ["9999-12-31"] and sf.monthly_asof("2031-01-01") == []


def test_static_gate_no_sqlite_no_timedelta_days():
    src = (ROOT / "src" / "iching" / "fundamentals.py").read_text(encoding="utf-8")
    assert not re.search(r"sqlite", src, re.I)
    # `timedelta(days=` 只准出現在 extend_calendar（補**日曆**平日，不是交易日視窗）；視窗一律用期別序／月序
    body = src.split("def extend_calendar")[0]
    assert "timedelta(days=" not in body
