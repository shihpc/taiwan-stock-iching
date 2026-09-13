"""合成回補 DB：`probe_features`／`feed`／`scan_features` 三支測試共用的 fixture 建構器。

刻意做成**每個探測項都有手算得出的答案**：ETF 與 DR 不進池、一檔第 30 日才上市（製造分母偏誤）、
一檔停牌兩日（`close>0` 量=0）、一檔畸形列（有量無 `close`）、一檔全程上漲並在第 40 日除息跌 20%
（後復權抹平、原始價不抹）。

`amount_scale` 是為了**排名池**：`liquidity.ADV_THRESHOLD_TWD` 是 0.3 億元／日，預設 scale 下
成交值只有十萬量級、池永遠是空的——那會讓 `p_cs` 的落地路徑**看起來有跑其實沒資料**。
要測 `p_cs` 就把 scale 開大（`tests/test_scan_features.py` 用 1e6）。
"""
from __future__ import annotations

import shutil
from pathlib import Path

import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from iching.store import Store  # noqa: E402

DV = "fm-20260911-01"
DAYS = [f"2020-{1 + i // 20:02d}-{1 + i % 20:02d}" for i in range(80)]
EX_I = 40                      # 除權息日索引
LATE_I = 30                    # 1103 上市日索引
SUSPEND_I = (30, 31)           # 1102 停牌（有參考價、零成交）
MALFORMED_I = 35               # 6488 畸形列（有量無 close）

INFO = [
    {"stock_id": "1101", "type": "twse", "industry_category": "水泥工業", "stock_name": "甲", "date": "2026-09-11"},
    {"stock_id": "1102", "type": "twse", "industry_category": "水泥工業", "stock_name": "乙", "date": "2026-09-11"},
    {"stock_id": "1103", "type": "twse", "industry_category": "水泥工業", "stock_name": "丙", "date": "2026-09-11"},
    {"stock_id": "2330", "type": "twse", "industry_category": "半導體業", "stock_name": "丁", "date": "2026-09-11"},
    {"stock_id": "6488", "type": "tpex", "industry_category": "光電業", "stock_name": "戊", "date": "2026-09-11"},
    {"stock_id": "0050", "type": "twse", "industry_category": "ETF", "stock_name": "己", "date": "2026-09-11"},
    {"stock_id": "9101", "type": "twse", "industry_category": "存託憑證", "stock_name": "庚", "date": "2026-09-11"},
]


def _px(i: int) -> dict[str, float | None]:
    """決定性價格。1101 穩定上漲＋第 40 日除息跌 20%（後復權會把它抹平，原始價不會）。"""
    out: dict[str, float | None] = {
        "1101": 100.0 * (1.01 ** i) * (0.80 if i >= EX_I else 1.0),
        "1102": 50.0,                                   # 全程平盤：既不漲也不站上 MA
        "2330": 300.0 * (1.005 ** i),
        "6488": 60.0 * (1.002 ** i),
        "1103": 20.0 * (1.003 ** i) if i >= LATE_I else None,   # 第 30 日才上市
    }
    return out


def build(cache: Path, *, amount_scale: float = 1.0) -> None:
    shutil.rmtree(cache, ignore_errors=True)
    cache.mkdir(parents=True)
    with Store(cache / "universe.db") as u:
        u.record_success("stock_info", "raw_stock_info", "all", INFO, DV, "TaiwanStockInfo", ("stock_id",))
    with Store(cache / "prices.db") as p:
        for i, d in enumerate(DAYS):
            rows = []
            for sid, px in _px(i).items():
                if px is None:
                    continue                                        # 尚未上市：完全沒有列
                if sid == "1102" and i in SUSPEND_I:
                    rows.append({"date": d, "stock_id": sid, "close": round(px, 2),
                                 "Trading_Volume": 0.0, "Trading_money": 0.0, "spread": 0.0})
                    continue
                if sid == "6488" and i == MALFORMED_I:
                    rows.append({"date": d, "stock_id": sid, "close": 0.0,
                                 "Trading_Volume": 500.0, "Trading_money": 0.0, "spread": 0.0})
                    continue
                prev = _px(i - 1).get(sid) if i else None
                rows.append({"date": d, "stock_id": sid, "close": round(px, 4),
                             "open": round(px * 0.99, 4), "max": round(px * 1.02, 4), "min": round(px * 0.98, 4),
                             "Trading_Volume": 1000.0, "Trading_money": round(px * 1000 * amount_scale, 2),
                             "spread": round(px - prev, 4) if prev else 0.0})
            rows.append({"date": d, "stock_id": "0050", "close": 150.0, "Trading_Volume": 9e3,
                         "Trading_money": 1.35e9 * amount_scale, "spread": 0.1})   # ETF：不得進池
            rows.append({"date": d, "stock_id": "9101", "close": 20.0, "Trading_Volume": 1e3,
                         "Trading_money": 2e7 * amount_scale, "spread": 0.0})      # DR：不得進池
            p.record_success("price_daily", "raw_price_daily", d, rows, DV, "TaiwanStockPrice")
            p.record_success("index_price", "raw_index_price", f"TAIEX:{d}",
                             [{"date": d, "stock_id": "TAIEX", "open": 9990.0 + i * 3, "max": 10010.0 + i * 3, "min": 9980.0 + i * 3,
                               "close": 10000.0 + i * 3, "Trading_money": 2.5e11 + i}], DV, "TaiwanStockPrice")
            p.record_success("index_price", "raw_index_price", f"TPEx:{d}",
                             [{"date": d, "stock_id": "TPEx", "open": 199.0 + i * 0.1, "max": 201.0 + i * 0.1, "min": 198.0 + i * 0.1,
                               "close": 200.0 + i * 0.1, "Trading_money": 5e10 + i}], DV, "TaiwanStockPrice")
        p.record_success("dividend_result", "raw_dividend_result", "1101:2020",
                         [{"date": DAYS[EX_I], "stock_id": "1101",
                           "before_price": 100.0, "after_price": 80.0}], DV, "TaiwanStockDividendResult")


def build_full(cache: Path, *, amount_scale: float = 1e6) -> None:
    """`build()` ＋ 籌碼／市場層最小資料（重播驅動 `replay_io`／`replay_state` 測試用）。

    - 1101：法人三個 name 逐日有列（外資淨＝(5000+i+100−1000)/1000 張、投信淨＝−0.5 張）、融資餘額 100+i；
      **借券餘額表刻意不建**（驗「表不存在→整欄 NaN、記 missing_tables」）。
    - 2330：**無任何籌碼列**（驗「法人無列補 0、餘額無列補 NaN」）。
    - 市場層：融資總餘額、VIX（同日兩筆、13:44 那筆才是日值）、TX 外資 OI、TX 近月＋一筆價差合約、
      ^GSPC／^SOX、USD 匯率、TWSE BFI82U（含避險列）＋ FMTQIK 四個月（每日成交金額 2e11+i+月）；
      **TPEx 官方兩表刻意不建**（tpex 的 amount／法人金額為 None）。
    """
    import json
    build(cache, amount_scale=amount_scale)
    with Store(cache / "chips.db") as c:
        for i, d in enumerate(DAYS):
            c.record_success("inst_buysell", "raw_inst_buysell", d, [
                {"date": d, "stock_id": "1101", "name": "Foreign_Investor", "buy": 5000.0 + i, "sell": 1000.0},
                {"date": d, "stock_id": "1101", "name": "Foreign_Dealer_Self", "buy": 100.0, "sell": 0.0},
                {"date": d, "stock_id": "1101", "name": "Investment_Trust", "buy": 2000.0, "sell": 2500.0},
                {"date": d, "stock_id": "1101", "name": "Dealer_self", "buy": 9.0, "sell": 0.0}], DV, "TaiwanStockInstitutionalInvestorsBuySell")
            c.record_success("margin", "raw_margin", d, [{"date": d, "stock_id": "1101", "MarginPurchaseTodayBalance": 100 + i}], DV, "TaiwanStockMarginPurchaseShortSale")
    with Store(cache / "market.db") as m:
        for i, d in enumerate(DAYS):
            m.record_success("total_margin", "raw_total_margin", d, [{"date": d, "stock_id": "x", "name": "MarginPurchaseMoney", "TodayBalance": 1e9 + i},
                                                                   {"date": d, "stock_id": "x", "name": "ShortSale", "TodayBalance": 7.0}], DV, "X")
            m.record_success("vix", "raw_vix", d, [{"date": d, "stock_id": "VIX", "time": "13:44:00", "vix": 21.0 + i * 0.01},
                                                    {"date": d, "stock_id": "VIX", "time": "09:00:00", "vix": 20.0}], DV, "TaiwanOptionVix")
            m.record_success("futures_inst", "raw_futures_inst", d, [{"date": d, "stock_id": "TX", "futures_id": "TX", "institutional_investors": "外資",
                                                                      "long_open_interest_balance_volume": 1000 + i, "short_open_interest_balance_volume": 500}], DV, "X")
            # 近月：最後交易日＝第三個週三（03-18／04-15）；過了才換月 → 03-19 與 04-16 各換一次
            near = "202003" if d <= "2020-03-18" else ("202004" if d <= "2020-04-15" else "202005")
            m.record_success("futures_daily", "raw_futures_daily", d, [
                {"date": d, "stock_id": "TX", "futures_id": "TX", "contract_date": near, "trading_session": "position", "close": 10000.0 + i * 3 + 5},
                {"date": d, "stock_id": "TX", "futures_id": "TX", "contract_date": near, "trading_session": "after_market", "close": 1.0},
                {"date": d, "stock_id": "TX", "futures_id": "TX", "contract_date": "202003/202004", "trading_session": "position", "close": -3.0}], DV, "X")
            m.record_success("us_index", "raw_us_index", d, [{"date": d, "stock_id": "^GSPC", "Close": 4000.0 + i, "High": 4010.0 + i, "Low": 3990.0 + i},
                                                              {"date": d, "stock_id": "^SOX", "Close": 3000.0 + i, "High": 1, "Low": 1}], DV, "USStockPrice")
            m.record_success("fx_usd", "raw_fx_usd", d, [{"date": d, "stock_id": "USD", "spot_buy": 31.0, "spot_sell": 31.1 + i * 0.001}], DV, "X")
            m.record_success("twse_bfi82u", "raw_twse_bfi82u", d, [{"date": d, "stock_id": "x", "http_status": 200, "stat": "OK",
                "body": json.dumps({"stat": "OK", "data": [["自營商(自行買賣)", "1000", "500", "500"], ["自營商(避險)", "10", "5", "5"],
                                                          ["投信", "2000000", "1000000", "1000000"],
                                                          ["外資及陸資(不含外資自營商)", str(5000000 + i * 1000), "1000000", "0"],
                                                          ["外資自營商", "0", "0", "0"]]})}], DV, "X")
        for mo in range(1, 5):
            m.record_success("twse_fmtqik", "raw_twse_fmtqik", f"2020{mo:02d}", [{"date": f"2020-{mo:02d}-01", "month": f"2020{mo:02d}", "http_status": 200, "stat": "OK",
                "body": json.dumps({"stat": "OK", "data": [[f"109/{mo:02d}/{i + 1:02d}", "1", str((2e11 + i + mo) * 1000), "3", str(10000 + i * 3)] for i in range(20)]})}],
                DV, "X")
    with Store(cache / "fundamentals.db") as f:
        # 月營收：1101 自 2019-01 起每月 1e8×(1+0.02·k)（YoY 明確為正）、2330 只有 2020-01 起（三月 YoY 到 2021 才算得出）
        rows = []
        for k in range(0, 16):                                        # 2019-01 … 2020-04
            y, m = 2019 + k // 12, k % 12 + 1
            rows.append({"date": f"{y}-{m:02d}-01", "stock_id": "1101", "revenue_year": y, "revenue_month": m,
                         "revenue": 1e8 * (1 + 0.02 * k), "create_time": ""})
        for k in range(0, 4):
            rows.append({"date": f"2020-{k + 1:02d}-01", "stock_id": "2330", "revenue_year": 2020, "revenue_month": k + 1,
                         "revenue": 5e9, "create_time": ""})
        f.record_success("month_revenue", "raw_month_revenue", "all", rows, DV, "TaiwanStockMonthRevenue")
        # 季報：1101 2018-12 … 2019-12 五期（EPS 遞增、毛利率遞增、稅前淨利遞增）
        qrows = []
        for j, p in enumerate(("2018-12-31", "2019-03-31", "2019-06-30", "2019-09-30", "2019-12-31")):
            qrows += [{"date": p, "stock_id": "1101", "type": "EPS", "origin_name": "基本每股盈餘", "value": 1.0 + 0.1 * j},
                      {"date": p, "stock_id": "1101", "type": "GrossProfit", "origin_name": "營業毛利（毛損）", "value": 30.0 + j},
                      {"date": p, "stock_id": "1101", "type": "Revenue", "origin_name": "營業收入", "value": 100.0},
                      {"date": p, "stock_id": "1101", "type": "PreTaxIncome", "origin_name": "稅前淨利（淨損）", "value": 10.0 + j},
                      {"date": p, "stock_id": "1101", "type": "IncomeAfterTaxes", "origin_name": "本期淨利（淨損）", "value": 8.0}]
        f.record_success("financial_statements", "raw_financial_statements", "all", qrows, DV, "TaiwanStockFinancialStatements")

