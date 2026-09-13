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
                             "Trading_Volume": 1000.0, "Trading_money": round(px * 1000 * amount_scale, 2),
                             "spread": round(px - prev, 4) if prev else 0.0})
            rows.append({"date": d, "stock_id": "0050", "close": 150.0, "Trading_Volume": 9e3,
                         "Trading_money": 1.35e9 * amount_scale, "spread": 0.1})   # ETF：不得進池
            rows.append({"date": d, "stock_id": "9101", "close": 20.0, "Trading_Volume": 1e3,
                         "Trading_money": 2e7 * amount_scale, "spread": 0.0})      # DR：不得進池
            p.record_success("price_daily", "raw_price_daily", d, rows, DV, "TaiwanStockPrice")
            p.record_success("index_price", "raw_index_price", f"TAIEX:{d}",
                             [{"date": d, "stock_id": "TAIEX", "close": 10000.0 + i * 3}], DV, "TaiwanStockPrice")
            p.record_success("index_price", "raw_index_price", f"TPEx:{d}",
                             [{"date": d, "stock_id": "TPEx", "close": 200.0 + i * 0.1}], DV, "TaiwanStockPrice")
        p.record_success("dividend_result", "raw_dividend_result", "1101:2020",
                         [{"date": DAYS[EX_I], "stock_id": "1101",
                           "before_price": 100.0, "after_price": 80.0}], DV, "TaiwanStockDividendResult")


