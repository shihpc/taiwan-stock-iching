"""計分引擎測試共用：sys.path、合成資料（免 token 免網路、決定性——固定 seed）。"""
from __future__ import annotations

import datetime as dt
import sys
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from iching.score import MarketInputs, StockInputs, build_params  # noqa: E402

N_DAYS = 320


def weekdays(start: dt.date, n: int) -> list[str]:
    out, d = [], start
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d.isoformat())
        d += dt.timedelta(days=1)
    return out


def synth_market_inputs(seed: int = 1, n: int = N_DAYS, market: str = "twse", **override) -> MarketInputs:
    rng = np.random.default_rng(seed)
    tpe = weekdays(dt.date(2023, 1, 2), n)
    us = weekdays(dt.date(2023, 1, 2), n)
    c = 15000 + np.cumsum(rng.normal(0, 80, n))
    h = c + rng.uniform(10, 120, n)
    l = c - rng.uniform(10, 120, n)
    spx = 4000 + np.cumsum(rng.normal(0, 30, n))
    kw = dict(
        market=market, tpe_date=tpe[-1], tpe_dates=tpe,
        index_open=c + rng.normal(0, 30, n), index_high=h, index_low=l, index_close=c,
        amount=rng.uniform(2e11, 4e11, n), n_stocks=np.full(n, 1800.0),
        above_ma_ratio={k: rng.uniform(0.3, 0.7, n) for k in (5, 10, 20, 60)},
        advance_ratio=rng.uniform(0.3, 0.7, n),
        new_high_low_ratio={k: rng.uniform(-0.05, 0.05, n) for k in (10, 20, 60)},
        ad_line=np.cumsum(rng.integers(-300, 300, n)).astype(float),
        up_amount_ratio=rng.uniform(0.3, 0.7, n),
        foreign_net_amount=rng.normal(0, 5e9, n), trust_net_amount=rng.normal(0, 1e9, n),
        margin_balance=2e11 + np.cumsum(rng.normal(0, 1e9, n)),
        foreign_net_oi=np.cumsum(rng.normal(0, 2000, n)), basis=rng.normal(0, 0.3, n), contract_rolled=False,
        vix=rng.uniform(12, 30, n), put_call_ratio=1.1,
        us_dates=us, spx_close=spx, spx_high=spx + 20, spx_low=spx - 20,
        sox_close=3000 + np.cumsum(rng.normal(0, 40, n)),
        fx_dates=tpe, fx_usdtwd=31 + np.cumsum(rng.normal(0, 0.05, n)),
        line2_score_t_minus_5={"short": 48.0, "swing": 48.0, "mid": 48.0},
    )
    kw.update(override)
    return MarketInputs(**kw)


def synth_stock_inputs(seed: int = 2, n: int = N_DAYS, market: str = "twse", **override) -> StockInputs:
    rng = np.random.default_rng(seed)
    tpe = weekdays(dt.date(2023, 1, 2), n)
    sc = 100 + np.cumsum(rng.normal(0, 1.5, n))
    idx = 15000 + np.cumsum(rng.normal(0, 80, n))
    rev = [(f"{2022 + i // 12}-{i % 12 + 1:02d}", 1e6 * (1 + 0.02 * i)) for i in range(30)]
    kw = dict(
        market=market, stock_id="2330", tpe_date=tpe[-1], industry="半導體", is_financial=False,
        open=sc + rng.normal(0, 0.5, n), high=sc + rng.uniform(0.2, 3, n), low=sc - rng.uniform(0.2, 3, n), close=sc,
        volume=rng.uniform(500, 3000, n), index_close=idx,
        industry_median_return={5: 0.3, 10: 0.5, 20: 1.0, 60: 2.0}, industry_n=30, industry_above_ma20_ratio=0.55,
        p_cs_long_excess=60.0, monthly_revenue=rev, industry_median_3m_yoy=5.0, industry_revenue_n=20,
        fundamentals={"eps": 2.0, "eps_ly": 1.5, "gross_margin": 50.0, "gross_margin_prev_q": 49.0, "price_at_period_end": 100.0,
                      "pretax_income": 120.0, "pretax_income_ly": 100.0, "equity": 1000.0, "equity_prev_q": 980.0},
        foreign_net_shares=rng.normal(0, 200, n), trust_net_shares=rng.normal(0, 50, n),
        margin_balance=5000 + np.cumsum(rng.normal(0, 50, n)), margin_eligible=True,
        short_sale_balance=1000 + np.cumsum(rng.normal(0, 10, n)), shares_outstanding=2.5e7,
        line2_score_history={h: [50.0] * 9 for h in ("short", "swing", "mid")},
        market_direction_score={"short": 51.0, "swing": 55.0, "mid": 52.0},
    )
    kw.update(override)
    return StockInputs(**kw)


@pytest.fixture
def ps_twse():
    return build_params("twse")


@pytest.fixture
def ps_tpex():
    return build_params("tpex")


@pytest.fixture
def mkt():
    return synth_market_inputs()


@pytest.fixture
def stk():
    return synth_stock_inputs()
