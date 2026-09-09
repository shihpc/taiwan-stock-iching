"""§8 第 8 條：計分函式不碰 DB（`src/iching/score/` grep `sqlite` 零命中）；DB 讀取層 `score_io` 用暫存 SQLite 驗最小實作。
§8 第 7 條：`us_session_closed_by` 全 repo 只有一個定義。"""
from __future__ import annotations

import re
from pathlib import Path

import numpy as np

from conftest import ROOT, weekdays
from iching.score import score_market
from iching.score.params import build_params
from iching import score_io
from iching.store import Store
import datetime as dt

SCORE_DIR = ROOT / "src" / "iching" / "score"


def test_score_package_never_touches_sqlite():
    hits = [p.name for p in SCORE_DIR.glob("*.py") if re.search(r"sqlite", p.read_text(encoding="utf-8"), re.I)]
    assert hits == []
    assert re.search(r"sqlite", (ROOT / "src" / "iching" / "score_io.py").read_text(encoding="utf-8"), re.I)


def test_us_session_closed_by_single_implementation():
    defs = [p for p in (ROOT / "src").rglob("*.py") if "def us_session_closed_by" in p.read_text(encoding="utf-8")]
    assert [p.name for p in defs] == ["calendar.py"]
    assert "us_session_closed_by" in (SCORE_DIR / "market.py").read_text(encoding="utf-8")


def test_no_timedelta_windows_in_score_package():
    for p in SCORE_DIR.glob("*.py"):
        assert "timedelta(" not in p.read_text(encoding="utf-8"), p.name


def _stores(tmp_path):
    return {n: Store(tmp_path / f"{n}.db") for n in ("prices", "chips", "market")}


def test_series_by_date_dedupes_and_orders(tmp_path):
    st = _stores(tmp_path)
    rows = [{"date": d, "stock_id": "TAIEX", "open": 1.0 + i, "max": 2.0 + i, "min": 0.5 + i, "close": 1.5 + i, "Trading_money": 100.0 + i}
            for i, d in enumerate(["2024-01-03", "2024-01-02", "2024-01-04"])]
    st["prices"].record_success("index_price", "raw_index_price", "TAIEX:2024", rows, "fm-20260909-01", "TaiwanStockPrice")
    st["prices"].record_success("index_price", "raw_index_price", "TAIEX:2024b", rows[:1], "fm-20260909-01", "TaiwanStockPrice")   # 同列重複落地
    idx = score_io.load_index(st, "twse", "2024-01-04", 10)
    assert idx["dates"] == ["2024-01-02", "2024-01-03", "2024-01-04"]
    assert idx["close"] == [2.5, 1.5, 3.5] and idx["amount"] == [101.0, 100.0, 102.0]
    assert score_io.load_index(st, "twse", "2024-01-03", 1)["dates"] == ["2024-01-03"]
    assert score_io.load_index(st, "tpex", "2024-01-04", 10)["dates"] == []
    assert score_io.load_vix(st, "2024-01-04", 10)["dates"] == []      # 表不存在 → 空


def test_market_inputs_from_stores_minimal(tmp_path):
    st = _stores(tmp_path)
    n = 300
    tpe = weekdays(dt.date(2023, 1, 2), n)
    rng = np.random.default_rng(3)
    c = 15000 + np.cumsum(rng.normal(0, 80, n))
    idx_rows = [{"date": d, "stock_id": "TAIEX", "open": float(c[i]), "max": float(c[i] + 50), "min": float(c[i] - 50),
                 "close": float(c[i]), "Trading_money": 3e11} for i, d in enumerate(tpe)]
    st["prices"].record_success("index_price", "raw_index_price", "TAIEX:all", idx_rows, "fm-1", "TaiwanStockPrice")
    spx = 4000 + np.cumsum(rng.normal(0, 30, n))
    us_rows = [{"date": d, "stock_id": "^GSPC", "Close": float(spx[i]), "High": float(spx[i] + 10), "Low": float(spx[i] - 10)} for i, d in enumerate(tpe)]
    us_rows += [{"date": d, "stock_id": "^SOX", "Close": float(spx[i] * 0.7), "High": 0, "Low": 0} for i, d in enumerate(tpe)]
    st["market"].record_success("us_index", "raw_us_index", "us:all", us_rows, "fm-1", "USStockPrice")
    fx_rows = [{"date": d, "currency": "USD", "spot_buy": 31.0, "spot_sell": 31.1} for d in tpe]
    st["market"].record_success("fx_usd", "raw_fx_usd", "fx:all", fx_rows, "fm-1", "TaiwanExchangeRate", index_cols=("date",))
    oi_rows = [{"date": d, "futures_id": "TX", "institutional_investors": "外資", "long_open_interest_balance_volume": 50000 + i,
                "short_open_interest_balance_volume": 40000} for i, d in enumerate(tpe)]
    oi_rows += [{"date": tpe[0], "futures_id": "TX", "institutional_investors": "投信", "long_open_interest_balance_volume": 1, "short_open_interest_balance_volume": 9}]
    st["market"].record_success("futures_inst", "raw_futures_inst", "tx:all", oi_rows, "fm-1", "TaiwanFuturesInstitutionalInvestors", index_cols=("date",))
    mg_rows = [{"date": d, "name": "MarginPurchaseMoney", "TodayBalance": 2e11 + i * 1e8} for i, d in enumerate(tpe)]
    st["market"].record_success("total_margin", "raw_total_margin", "tm:all", mg_rows, "fm-1", "X", index_cols=("date",))
    inp = score_io.market_inputs_from_stores(st, "twse", tpe[-1], tpe, tpe)
    assert inp.fx_usdtwd[0] == 31.05 and len(inp.index_close) == n and inp.foreign_net_oi[-1] == 50000 + n - 1 - 40000
    assert inp.us_dates == tpe and len(inp.sox_close) == n and inp.vix is None and inp.advance_ratio is None
    ms = score_market(inp, build_params("twse"), "short")
    assert ms.lines["1"].score is not None and ms.lines["6"].score is not None
    assert ms.lines["2"].unknown and ms.lines["4"].coverage_ratio == 0.2       # 廣度未載入 → 未知；四爻只有融資族
    assert ms.lines["5"].family("A").score is not None and ms.lines["5"].family("C").score is None


def test_stock_inputs_from_stores_minimal(tmp_path):
    st = _stores(tmp_path)
    tpe = weekdays(dt.date(2024, 1, 1), 30)
    px = [{"date": d, "stock_id": "2330", "open": 100.0, "max": 101.0, "min": 99.0, "close": 100.0 + i, "Trading_Volume": 1000000} for i, d in enumerate(tpe)]
    st["prices"].record_success("price_daily", "raw_price_daily", "d", px, "fm-1", "TaiwanStockPrice")
    idx = [{"date": d, "stock_id": "TAIEX", "open": 1, "max": 1, "min": 1, "close": 15000.0, "Trading_money": 1} for d in tpe[:-1]]   # 少最後一天
    st["prices"].record_success("index_price", "raw_index_price", "i", idx, "fm-1", "TaiwanStockPrice")
    inst = [{"date": tpe[0], "stock_id": "2330", "name": "Foreign_Investor", "buy": 5000, "sell": 2000},
            {"date": tpe[0], "stock_id": "2330", "name": "Foreign_Dealer_Self", "buy": 1000, "sell": 0},
            {"date": tpe[0], "stock_id": "2330", "name": "Investment_Trust", "buy": 0, "sell": 3000}]
    st["chips"].record_success("inst_buysell", "raw_inst_buysell", "c", inst, "fm-1", "X")
    inp = score_io.stock_inputs_from_stores(st, "twse", "2330", tpe[-1])
    assert len(inp.close) == 29 and inp.volume[0] == 1000.0 and inp.index_close == [15000.0] * 29
    assert inp.foreign_net_shares[0] == 4.0 and inp.trust_net_shares[0] == -3.0 and inp.foreign_net_shares[1] == 0.0
    assert inp.margin_balance is None and inp.margin_eligible is False and inp.monthly_revenue is None
