"""`scripts/probe_features.py`：三個待量測項的探測腳本。免 token 免網路。

本容器沒有 Hetzner DB，所以這裡**用合成 DB 實跑整支腳本**（不是 mock），驗「跑得起來、算得對」；
真實資料上的**數字**要等使用者在 Hetzner 跑，那不是這支測試能證明的。

合成資料刻意做成每個探測項都有**手算得出**的答案（見 `_build` 的註解）。
"""
from __future__ import annotations

import shutil
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

import probe_features as P  # noqa: E402
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


def _build(cache: Path) -> None:
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
                             "Trading_Volume": 1000.0, "Trading_money": round(px * 1000, 2),
                             "spread": round(px - prev, 4) if prev else 0.0})
            rows.append({"date": d, "stock_id": "0050", "close": 150.0, "Trading_Volume": 9e3,
                         "Trading_money": 1.35e9, "spread": 0.1})   # ETF：不得進池
            rows.append({"date": d, "stock_id": "9101", "close": 20.0, "Trading_Volume": 1e3,
                         "Trading_money": 2e7, "spread": 0.0})      # DR：不得進池
            p.record_success("price_daily", "raw_price_daily", d, rows, DV, "TaiwanStockPrice")
            p.record_success("index_price", "raw_index_price", f"TAIEX:{d}",
                             [{"date": d, "stock_id": "TAIEX", "close": 10000.0 + i * 3}], DV, "TaiwanStockPrice")
            p.record_success("index_price", "raw_index_price", f"TPEx:{d}",
                             [{"date": d, "stock_id": "TPEx", "close": 200.0 + i * 0.1}], DV, "TaiwanStockPrice")
        p.record_success("dividend_result", "raw_dividend_result", "1101:2020",
                         [{"date": DAYS[EX_I], "stock_id": "1101",
                           "before_price": 100.0, "after_price": 80.0}], DV, "TaiwanStockDividendResult")


@pytest.fixture(scope="module")
def res(tmp_path_factory):
    cache = tmp_path_factory.mktemp("probe") / "cache"
    _build(cache)
    prices, uni = P.open_ro(cache / "prices.db"), P.open_ro(cache / "universe.db")
    try:
        pool = P.load_pool(uni)
        factors, fstat = P.load_factors(prices, DV)
        idx = P.load_index(prices, DV)
        out = {"data_version": DV, "pool_size": len(pool), "factors": fstat,
               "traded": P.probe_traded(prices, DV, pool)}
        out.update(P.replay(prices, DV, pool, factors, idx, True, True, None, None, None, True))
        return out
    finally:
        prices.close()
        uni.close()


# ---------------------------------------------------------------------------
# 純函式
# ---------------------------------------------------------------------------
def test_ratio_gap_pp_hand_computed():
    """同分子、兩種分母的比值差。`2/4` vs `2/3` ＝ 50% vs 66.67% ＝ 16.667 個百分點。"""
    assert P.ratio_gap_pp(2, 4, 3) == pytest.approx(100 * (2 / 3 - 2 / 4))
    assert P.ratio_gap_pp(2, 4, 4) == 0.0                  # 沒有新股 → 零偏誤
    assert P.ratio_gap_pp(0, 100, 50) == 0.0               # 分子 0 → 兩種分母都是 0
    assert P.ratio_gap_pp(5, 0, 3) is None                 # 沒有母體就沒有可比的東西
    assert P.ratio_gap_pp(5, 10, 0) is None                # 不得回 0.0（那會混成「沒差異」）


# ---------------------------------------------------------------------------
# probe 1：四象限逐格手算
# ---------------------------------------------------------------------------
def test_probe_traded_counts_are_hand_computable(res):
    t = res["traded"]
    assert res["pool_size"] == 5                            # ETF 0050 與 DR 9101 不進池
    expected_rows = 4 * len(DAYS) + (len(DAYS) - LATE_I)    # 四檔全程 ＋ 1103 從第 30 日起
    assert t["rows_in_pool"] == expected_rows == 370
    assert t["close_only"] == len(SUSPEND_I) == 2           # 1102 停牌：有參考價、零成交
    assert t["vol_only"] == 1                               # 6488 畸形列：有量無 close
    assert t["neither"] == 0
    assert t["both"] == expected_rows - 3
    assert sum(v["both"] for v in t["per_year"].values()) == t["both"]


# ---------------------------------------------------------------------------
# probe 2：新股造成的分母偏誤
# ---------------------------------------------------------------------------
def test_probe_denominator_detects_late_listing(res):
    """1103 第 30 日才上市 → 它進得了 `N_t`、進不了 `above_ma_count` → 兩種分母一定有差。

    若新股被漏掉（例如母體誤用 eligible），gap 會整排變 0，這條就紅。
    """
    gaps = {s["name"]: s for s in res["denominator"]["gap_pp"] if s["n"]}
    assert gaps["MA5"]["max"] > 0, "偵測不到新股造成的分母偏誤"
    assert gaps["MA60"]["max"] > 0
    assert gaps["MA5"]["median"] == 0.0                     # 多數日子沒有新股 → 中位為 0
    share = {s["name"]: s for s in res["denominator"]["eligible_share"] if s["n"]}
    assert share["MA5"]["max"] == pytest.approx(1.0)        # 穩態下全部都有足夠歷史
    assert 0.0 <= share["MA60"]["median"] <= 1.0


# ---------------------------------------------------------------------------
# probe 3：兩種價格口徑
# ---------------------------------------------------------------------------
def test_probe_adjust_shows_ex_dividend_divergence(res):
    """除息日 raw 跌 20%、後復權抹平 → 漲跌、上漲股成交占比、站上 MA20 三者都要看得出差。

    `above_ma20_ratio` 特別重要：初版合成資料太弱（該檔在兩種口徑下都已在 MA20 之下），
    量出來是 0.000 而**看起來像沒差**。現在 1101 全程上漲、除息跌 20% 才會真的跨過 MA20。
    """
    j = res["adjust"]
    assert j["advance_ratio_pp"]["max"] > 0
    assert j["up_amount_ratio_pp"]["max"] > 0
    assert j["above_ma20_ratio_pp"]["max"] > 0, "站上 MA20 在兩種口徑下完全沒差＝合成資料沒測到這條路徑"
    assert j["advance_ratio_pp"]["median"] == 0.0            # 只有除息日會差
    # AD 是累積量：除息日後復權判漲(+1)、原始價判跌(−1)，期末差恰 2
    twse = j["ad_line_final"]["twse"]
    assert twse["diff"] == 2, f"AD 期末差應為 2，得到 {twse}"
    assert j["vs_official_spread_pp"]["n"] > 0              # spread 欄存在就要真的比


def test_replay_is_readonly_and_scanners_stay_in_sync(res):
    assert res["days"] == len(DAYS)
    assert res["have_spread"] is True
    assert res["factors"]["stocks"] == 1 and res["factors"]["bad_skipped"] == 0


def test_render_covers_all_three_probes(res):
    out = P.render(res)
    for must in ("probe 1", "probe 2", "probe 3", "騰落線", "eligible / N_t", "判讀"):
        assert must in out
    assert "nan" not in out.lower()


# ---------------------------------------------------------------------------
# 形狀不對就要大聲停下（探測腳本最不該做的事是靜默回 0）
# ---------------------------------------------------------------------------
def test_loud_failures(tmp_path):
    cache = tmp_path / "cache"
    _build(cache)
    with P.open_ro(cache / "prices.db") as _c:
        pass
    prices = P.open_ro(cache / "prices.db")
    try:
        with pytest.raises(P.ProbeError, match="沒有 data_version"):
            P.resolve_dv(prices, P.PRICE_TABLE, "fm-不存在")
        with pytest.raises(P.ProbeError, match="表不存在"):
            P.require(prices, "raw_不存在", {"date"})
        with pytest.raises(P.ProbeError, match="缺欄位"):
            P.require(prices, P.PRICE_TABLE, {"date", "沒這欄"})
    finally:
        prices.close()
    with pytest.raises(P.ProbeError, match="找不到 DB"):
        P.open_ro(tmp_path / "不存在.db")


def test_main_runs_end_to_end(tmp_path, capsys):
    """真的跑 `main()`——含 argparse、三個 probe、報表與 JSON 輸出。"""
    cache = tmp_path / "cache"
    _build(cache)
    out_json = tmp_path / "probe.json"
    rc = P.main(["--cache-dir", str(cache), "--probe", "all", "--quiet",
                 "--out-json", str(out_json)])
    assert rc == 0
    text = capsys.readouterr().out
    assert "probe 1" in text and "probe 3" in text
    import json
    payload = json.loads(out_json.read_text(encoding="utf-8"))
    assert payload["pool_size"] == 5 and payload["days"] == len(DAYS)


def test_main_exits_2_on_bad_shape(tmp_path, capsys):
    rc = P.main(["--cache-dir", str(tmp_path / "沒有這個目錄"), "--probe", "traded"])
    assert rc == 2
    assert "probe 中止" in capsys.readouterr().err
