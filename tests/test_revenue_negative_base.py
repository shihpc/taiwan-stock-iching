"""`scripts/revenue_negative_base.py`：營收子指標負值基期量測（唯讀，`docs/P3-CALIBRATION.md` §30）。

分析流程用**真實 replay 產出的 db**（`synth_db.build_full`＋本檔注入的月營收＋`replay_scores`）。注入的月營收刻意做成
手算得出的負值（其餘月份一律 10 元）：

- 1102（上市、水泥）：2019-01＝−10、2020-01＝5（短線 −10→+5）；2019-02＝−10、2020-02＝−2（−10→−2）；
  2019-03＝−2、2020-03＝−10（−2→−10）；2018-10＝−40（三月組的 den 在 2019-11／2019-12 與加速度前組為負）。
- 6488（上櫃、光電）：2020-01＝−5、2020-02＝−30（den>0 且 num<0）。
- 1103：1e8×(1＋0.037k)（全正；供「den>0 用字面式子不逐位相同」的列）。
- 另塞一檔池外代號 9999 的負值列（只進原始表計數，不進重播）。

產業中位數情境另以手造 `FundamentalsBridge`（同產業 5 檔，族 C 在場）驗。期待值以手算、原始 SQL 或逐列展開另算。
"""
from __future__ import annotations

import dataclasses
import hashlib
import json
import math
import os
import shutil
import sqlite3
import statistics
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import replay_scores as R
import revenue_base_impact as RB
import revenue_negative_base as RN
import scan_features as SF
from synth_db import DV, build_full

from iching import fundamentals as FUND
from iching.score import stock as STK
from iching.score.params import build_params
from iching.score.transform import REASON_DENOM_ZERO, Missing
from iching.store import Store

Y2020 = ("2020-01-01", "2020-12-31")
SCRIPT = ROOT / "scripts" / "hetzner_revneg.sh"
OVERRIDE = {"1102": {(2019, 1): -10.0, (2020, 1): 5.0, (2019, 2): -10.0, (2020, 2): -2.0, (2019, 3): -2.0, (2020, 3): -10.0,
                     (2018, 10): -40.0},
            "6488": {(2020, 1): -5.0, (2020, 2): -30.0}}


def _pub(y: int, m: int) -> str:
    return f"{y + (m == 12):04d}-{m % 12 + 1:02d}-01"


def add_revenue(cache: Path) -> None:
    rows = []
    for k in range(28):                                            # 2018-01 … 2020-04
        y, m = 2018 + k // 12, k % 12 + 1
        for sid in ("1102", "6488", "1103"):
            v = 1e8 * (1 + 0.037 * k) if sid == "1103" else OVERRIDE.get(sid, {}).get((y, m), 10.0)
            rows.append({"date": _pub(y, m), "stock_id": sid, "revenue_year": y, "revenue_month": m, "revenue": v,
                         "create_time": ""})
    rows.append({"date": _pub(2020, 1), "stock_id": "9999", "revenue_year": 2020, "revenue_month": 1, "revenue": -7.0,
                 "create_time": ""})                              # 池外代號：只進原始表
    with Store(cache / "fundamentals.db") as f:
        f.record_success("month_revenue", "raw_month_revenue", "revneg-test", rows, DV, "TaiwanStockMonthRevenue")


@pytest.fixture(scope="module")
def world(tmp_path_factory) -> Path:
    c = tmp_path_factory.mktemp("revneg") / "cache"
    build_full(c)
    add_revenue(c)
    assert SF.main(["--cache-dir", str(c), "--quiet", "--allow-short-warmup", "--warmup-days", "0"]) == 0
    assert R.main(["--cache-dir", str(c), "--out", str(c / "scores.db"), "--window", "30", "--quiet", "--limit-days", "80"]) == 0
    return c


def _run(world: Path, out: Path, db: Path | None = None, **kw):
    kw.setdefault("per_group", 100000)
    return RN.run(db or world / "scores.db", out, cache_dir=world, start=Y2020[0], end=Y2020[1], quiet=True, **kw)


@pytest.fixture(scope="module")
def census(world, tmp_path_factory):
    d = tmp_path_factory.mktemp("census")
    return _run(world, d / "rep.json", keep_details=True), d


def _copy(world: Path, tmp_path: Path) -> Path:
    c = tmp_path / "cache"
    shutil.copytree(world, c)
    return c


def _run_id(det, sid: str, date: str, h: str) -> int:
    rows = det["rows"]
    for i in range(rows["n"]):
        if (rows["sid_of"][rows["sid"][i]], rows["dates"][rows["date"][i]], RN.HORIZONS[rows["h"][i]]) == (sid, date, h):
            return int(det["run_of_row"][i])
    raise AssertionError((sid, date, h))


def _sql(world: Path, sql: str, args=()) -> int:
    con = sqlite3.connect(world / "scores.db")
    try:
        return int(con.execute(sql, args).fetchone()[0])
    finally:
        con.close()


def _cnt(world, sid: str, h: str, lo: str = "0000", hi: str = "9999") -> int:
    return _sql(world, "SELECT COUNT(*) FROM scores WHERE scope='stock' AND stock_id=? AND horizon=? AND date>=? AND date<?",
                (sid, h, lo, hi))


def _count(res, m, h, g):
    return next(c for c in res["counts"] if (c["market"], c["horizon"], c["group"]) == (m, h, g))


def _cf(res, m, h, sc):
    return next(c for c in res["counterfactual"] if (c["market"], c["horizon"], c["scenario"]) == (m, h, sc))


# ---------------------------------------------------------------------------
# 候選式：三個典型例（手算）
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("den,num,cand,cur", [(-10.0, 5.0, 150.0, -150.0), (-10.0, -2.0, 80.0, -80.0), (-2.0, -10.0, -400.0, 400.0)])
def test_typical_examples(den, num, cand, cur):
    """−10→+5 得 +150（現行 −150）、−10→−2 得 +80（現行 −80）、−2→−10 得 −400（現行 +400）。現行值取真實 revenue_yoy_3m。"""
    assert RN.cand_value(num, den) == cand
    rev = {"2019-01": den, "2020-01": num}
    assert STK.revenue_yoy_3m(rev, "2020-01", 0, 1) == cur
    with RN.patched((STK,), RN.Candidate()) as c:
        assert STK.revenue_yoy_3m(rev, "2020-01", 0, 1) == cand
    assert c.calls == 1 and c.rewritten == 1
    assert STK.revenue_yoy_3m is RN.ORIG_YOY                    # 離開即還原


def test_candidate_den_pos_and_zero_untouched():
    """den>0 回傳現行式子的值（同一物件路徑、逐位相同）；den＝0 仍是現行缺值；月份不齊仍缺。"""
    c = RN.Candidate()
    rev = {"2019-01": 3.0, "2020-01": -7.0}
    assert c(rev, "2020-01", 0, 1) == (-7.0 / 3.0 - 1.0) * 100.0 and c.rewritten == 0
    z = c({"2019-01": 0.0, "2020-01": 5.0}, "2020-01", 0, 1)
    assert isinstance(z, Missing) and z.reason == REASON_DENOM_ZERO
    assert isinstance(c({"2020-01": 5.0}, "2020-01", 0, 1), Missing)
    assert RN.literal_value(1.0, 3.0) != RN.cand_value(1.0, 3.0)    # 字面式子在 den>0 不必然逐位相同（本例 1/3）


# ---------------------------------------------------------------------------
# 列數（對原始 SQL 的日期區間手算）
# ---------------------------------------------------------------------------
#   最新可用月：2020-01-10 前＝2019-11、01-10 起＝2019-12、02-10 起＝2020-01、03-10 起＝2020-02、04-10 起＝2020-03

def test_counts_short_yoy_1102(census, world):
    """短線單月：2020-01（−10→5）den<0；2020-02（−10→−2）、2020-03（−2→−10）兩者皆負 → den<0 自 02-10、num<0 自 03-10。"""
    res, _ = census
    c = _count(res, "twse", "short", "revenue_yoy")
    assert c["den_neg"] == _cnt(world, "1102", "short", "2020-02-10") > 0
    assert c["num_neg"] == c["both_neg"] == _cnt(world, "1102", "short", "2020-03-10") > 0
    assert c["den_pos_num_neg"] == 0 and c["den_neg_sub_present"] == c["den_neg"]
    n = _sql(world, "SELECT COUNT(*) FROM scores WHERE scope='stock' AND market='twse' AND horizon='short'")
    assert c["group_rows"] == n and c["den_neg_share"] == c["den_neg"] / n and c["num_neg_share"] == c["num_neg"] / n
    assert c["den_min"] == -10.0


def test_counts_3m_and_accel_prev_1102(census, world):
    """三月組：den＝2018-11/10/09（含 −40）→ 2019-11、2019-12 為 −20；2020-01 為 10（>0）；2020-02 為 −10；2020-03 為 −22、num −7。
    加速度前組（offset 3）：2020-01、02、03 三個月的前組 den 都含 2018-10（−20）；2019-11、12 的前組不含。"""
    res, _ = census
    c = _count(res, "twse", "swing", "revenue_yoy")
    all_ = _cnt(world, "1102", "swing")
    assert c["den_neg"] == all_ - _cnt(world, "1102", "swing", "2020-02-10", "2020-03-10")
    assert c["num_neg"] == c["both_neg"] == _cnt(world, "1102", "swing", "2020-04-10") and c["den_min"] == -22.0
    p = _count(res, "twse", "swing", "revenue_accel.prev")
    assert p["den_neg"] == _cnt(world, "1102", "swing", "2020-02-10") and p["num_neg"] == 0 and p["den_min"] == -20.0
    near = _count(res, "twse", "mid", "revenue_accel.near")
    assert (near["den_neg"], near["num_neg"]) == (c["den_neg"], c["num_neg"])
    vs = _count(res, "twse", "mid", "revenue_yoy_vs_industry")
    assert (vs["den_neg"], vs["num_neg"], vs["den_neg_sub_present"]) == (c["den_neg"], c["num_neg"], 0)   # 產業樣本 < 5 → 族 C 缺


def test_counts_den_pos_num_neg_6488(census, world):
    """6488：短線 2020-01（10→−5）、2020-02（10→−30）num<0；三月組 2020-02、2020-03 num＝−25；den 恆為正。"""
    res, _ = census
    s = _count(res, "tpex", "short", "revenue_yoy")
    assert s["num_neg"] == s["den_pos_num_neg"] == _cnt(world, "6488", "short", "2020-02-10", "2020-04-10") > 0
    assert s["den_neg"] == s["both_neg"] == 0
    w = _count(res, "tpex", "swing", "revenue_yoy")
    assert w["num_neg"] == w["den_pos_num_neg"] == _cnt(world, "6488", "swing", "2020-03-10") > 0


def test_literal_den_pos_diff_present(census):
    """合成資料上 den>0 的字面式子確實有不逐位相同的列（守門 1 的突變才測得出來）；只量不套用。"""
    res, _ = census
    assert any(c["literal_den_pos"]["bitwise_diff_rows"] > 0 for c in res["counts"])
    for c in res["counts"]:
        assert c["literal_den_pos"]["rows"] <= c["base_rows"] - c["den_neg"] - c["den_zero"]


def test_counts_hand_table():
    """手造 run 表：四類計數、den＝0、比例分母＝組列數（非可算基期列數）、run-length 權重。"""
    n = 6
    nan = math.nan
    den = np.array([-10.0, -2.0, 5.0, 5.0, 0.0, nan])
    num = np.array([3.0, -1.0, -4.0, 2.0, 1.0, nan])
    w = np.array([1, 2, 3, 4, 5, 7], dtype=np.int64)
    Rh = {"mk": np.zeros(n, np.int8), "h": np.zeros(n, np.int8),
          "pres": {s: np.array([1, 1, 1, 1, 0, 0], np.int8) for s in RN.SUBS}}
    Nh = {"num": {g: num for g in RN.GNAMES}, "den": {g: den for g in RN.GNAMES},
          "lit": {g: np.array([-1, -1, 1, 0, -1, -1], np.int8) for g in RN.GNAMES}}
    c = next(x for x in RN.summarize_counts(Rh, Nh, w) if x["group"] == "revenue_yoy")
    assert c["group_rows"] == 22 and c["base_rows"] == 15
    assert (c["den_neg"], c["num_neg"], c["both_neg"], c["den_pos_num_neg"], c["den_zero"]) == (3, 5, 2, 3, 5)
    assert c["den_neg_share"] == 3 / 22 and c["num_neg_share"] == 5 / 22 and c["den_neg_sub_present"] == 3
    assert c["literal_den_pos"] == {"rows": 7, "bitwise_diff_rows": 3} and c["den_min"] == -10.0 and c["num_min"] == -4.0


# ---------------------------------------------------------------------------
# 反事實：x 的手算、den>0 不動
# ---------------------------------------------------------------------------

def test_cf_x_hand_1102(census):
    res, _ = census
    det = res["_details"]
    N = det["N"]
    for date, x0, x1 in (("2020-02-10", -150.0, 150.0), ("2020-03-10", -80.0, 80.0), ("2020-04-10", 400.0, -400.0)):
        r = _run_id(det, "1102", date, "short")
        assert N["x0"]["revenue_yoy"][r] == x0 and N["x1"][("sub_only", "revenue_yoy")][r] == x1
        assert N["ch"][("sub_only", "revenue_yoy")][r] == 1
    # 波段三月 YoY 2020-03-10（最新 2020-02）：num 13、den −10 → 現行 −230、候選 +230（浮點照式子算）
    r = _run_id(det, "1102", "2020-03-10", "swing")
    assert N["x0"]["revenue_yoy"][r] == (13.0 / -10.0 - 1.0) * 100.0 and N["x1"][("sub_only", "revenue_yoy")][r] == (13.0 + 10.0) / 10.0 * 100.0
    # 加速度 2020-02-10（最新 2020-01）：近組 25/10（>0，150 不動）、前組 30/−20 → 現行 −250、候選 +250
    r = _run_id(det, "1102", "2020-02-10", "short")
    assert N["num"]["revenue_accel.near"][r] == 25.0 and N["den"]["revenue_accel.prev"][r] == -20.0
    assert N["x0"]["revenue_accel"][r] == 150.0 - (-250.0) and N["x1"][("sub_only", "revenue_accel")][r] == 150.0 - 250.0


def test_cf_affected_counts(census, world):
    """1102 每一列至少一個在場子指標的月份組 den<0 → 短線受影響列＝1102 短線全部列；族 C 在合成資料恆缺（產業樣本 < 5），
    所以中期 with_median 與 sub_only 的受影響列相同、族 C 輸入改變 0 列（中位數雖然變了）。"""
    res, _ = census
    assert _cf(res, "twse", "short", "sub_only")["affected_rows"] == _cnt(world, "1102", "short")
    a, b = _cf(res, "twse", "mid", "sub_only"), _cf(res, "twse", "mid", "with_median")
    assert a["affected_rows"] == b["affected_rows"] == _cnt(world, "1102", "mid")
    assert b["subs"]["revenue_yoy_vs_industry"]["input_changed_rows"] == 0
    assert res["industry_median"]["industry_days_changed"] > 0


def test_cf_den_pos_rows_unchanged(census):
    """den>0（含 num<0 的 6488）與 den＝全正的列：替換前後 x、子指標分數、初爻逐位相同（非受影響列）。"""
    res, _ = census
    det = res["_details"]
    N, Rr = det["N"], det["R"]
    for sc in RN.SCEN:
        un = N["aff"][sc] == 0
        assert un.any()
        s0, s1 = Rr["score"][un], N["score"][sc][un]
        assert np.array_equal(np.isnan(s0), np.isnan(s1)) and np.array_equal(s0[~np.isnan(s0)], s1[~np.isnan(s1)])
    for sc in RN.SCEN:                                          # 6488（den>0、num<0）一列都不受影響
        c = _cf(res, "tpex", "short", sc)
        assert c["affected_rows"] == 0 and c["flip_rows"] == 0
    r = _run_id(det, "6488", "2020-02-10", "short")
    assert N["x0"]["revenue_yoy"][r] == -150.0 == N["x1"][("sub_only", "revenue_yoy")][r]


def test_cf_line_counts_equal_row_expansion(census, world):
    """反事實彙總（翻轉／進出帶／分數改變）以逐列（原分數取 db 的 line_1）直接比對另算；合成資料上翻轉兩向都有。"""
    res, _ = census
    det = res["_details"]
    rows, N, ror = det["rows"], det["N"], det["run_of_row"]
    lo, hi = RN.BAND
    tot = {"dn": 0, "up": 0}
    for c in res["counterfactual"]:
        want = {"aff": 0, "chg": 0, "dn": 0, "up": 0, "enter": 0, "exit": 0, "unk": 0}
        for i in range(rows["n"]):
            if (RN.MARKETS[rows["mk"][i]], RN.HORIZONS[rows["h"][i]]) != (c["market"], c["horizon"]):
                continue
            r = ror[i]
            if N["aff"][c["scenario"]][r] != 1:
                continue
            want["aff"] += 1
            o, f = rows["l1"][i], N["score"][c["scenario"]][r]
            if math.isnan(o) or math.isnan(f):
                want["unk"] += int(not math.isnan(o) and math.isnan(f))
                continue
            want["chg"] += int(f != o)
            want["dn"] += int(o >= 50 > f)
            want["up"] += int(o < 50 <= f)
            want["enter"] += int(not lo <= o <= hi and lo <= f <= hi)
            want["exit"] += int(lo <= o <= hi and not lo <= f <= hi)
        got = {"aff": c["affected_rows"], "chg": c["score_changed_rows"], "dn": c["flip_yang_to_yin"], "up": c["flip_yin_to_yang"],
               "enter": c["band_enter_rows"], "exit": c["band_exit_rows"], "unk": c["became_unknown_rows"]}
        assert got == want, (c["market"], c["horizon"], c["scenario"])
        assert c["flip_rows"] == want["dn"] + want["up"] and c["flip_share_of_group"] == c["flip_rows"] / c["group_rows"]
        tot["dn"] += want["dn"]
        tot["up"] += want["up"]
    assert tot["dn"] > 0 and tot["up"] > 0


def test_summarize_cf_hand_edges():
    """50 為界（≥50 陽）、[45, 55] 兩端含（原分數恰 45.0 在帶內 → 出帶）、變未知只計原本已知、變已知另列、
    權重＝列數；子指標 x 符號翻轉與分數變化。"""
    orig = np.array([50.0, 49.0, 44.9, 55.0, 60.0, 70.0, 45.0, np.nan, np.nan])
    cf = np.array([49.9, 50.0, 45.0, 55.1, np.nan, 71.0, 44.0, np.nan, 50.0])
    aff = np.ones(orig.size, np.int8)
    w = np.arange(1, orig.size + 1, dtype=np.int64)
    n = orig.size
    x0 = np.array([-150.0, -80.0, 1.0, 2.0, 3.0, 400.0, 5.0, 6.0, 7.0])
    x1 = np.array([150.0, 80.0, 1.0, 2.0, 3.0, -400.0, 5.0, 6.0, 7.0])
    sc0 = np.array([10.0, 20.0, 30.0, 40.0, 50.0, 90.0, 60.0, 61.0, 62.0])
    sc1 = np.array([90.0, 70.0, 30.0, 40.0, 50.0, 10.0, 60.0, 61.0, 62.0])
    ch = np.array([1, 1, 0, 0, 0, 1, 0, 0, 0], np.int8)
    Rh = {"mk": np.zeros(n, np.int8), "h": np.zeros(n, np.int8), "score": orig}
    nanv = np.full(n, np.nan)
    Nh = {"aff": {sc: aff for sc in RN.SCEN}, "score": {sc: cf for sc in RN.SCEN},
          "x0": {s: (x0 if s == "revenue_yoy" else nanv) for s in RN.SUBS},
          "sc0": {s: (sc0 if s == "revenue_yoy" else nanv) for s in RN.SUBS},
          "x1": {(sc, s): (x1 if s == "revenue_yoy" else nanv) for sc in RN.SCEN for s in RN.SUBS},
          "sc1": {(sc, s): (sc1 if s == "revenue_yoy" else nanv) for sc in RN.SCEN for s in RN.SUBS},
          "ch": {(sc, s): (ch if s == "revenue_yoy" else np.full(n, -1, np.int8)) for sc in RN.SCEN for s in RN.SUBS}}
    c = next(x for x in RN.summarize_cf(Rh, Nh, w) if x["scenario"] == "sub_only")
    assert c["group_rows"] == 45 and c["affected_rows"] == 45
    assert (c["flip_yang_to_yin"], c["flip_yin_to_yang"], c["flip_rows"]) == (1, 2, 3)
    assert c["became_unknown_rows"] == 5 and c["orig_unknown_rows"] == 8 + 9 and c["became_known_rows"] == 9
    assert c["band_enter_rows"] == 3 and c["band_exit_rows"] == 4 + 7 and c["orig_in_band_rows"] == 1 + 2 + 4 + 7
    assert c["score_changed_rows"] == 1 + 2 + 3 + 4 + 6 + 7
    s = c["subs"]["revenue_yoy"]
    assert s["input_changed_rows"] == 1 + 2 + 6 and s["x_sign_flip_rows"] == 9 and s["sub_score_changed_rows"] == 9
    assert s["sub_score_delta"]["min"] == -80.0 and s["sub_score_delta"]["max"] == 80.0
    assert s["x_orig"]["min"] == -150.0 and s["x_cand"]["min"] == -400.0 and set(c["subs"]) == {"revenue_yoy"}


# ---------------------------------------------------------------------------
# 產業中位數情境（手造 bridge：同產業 5 檔、族 C 在場）
# ---------------------------------------------------------------------------

class _Pool:
    def __init__(self, ind):
        self.ind = ind

    def listed(self, sid, T):
        return "twse"

    def industry_of(self, sid):
        return self.ind.get(sid)

    def get(self, sid, default=None):
        return {"stock_name": f"名{sid}"} if sid in self.ind else default


class _Src:
    def __init__(self, days, pool):
        self.days, self.pool = days, pool

    def trading_dates(self):
        return list(self.days)


def _cal():
    import datetime as dt
    d, out = dt.date(2017, 1, 2), []
    while d <= dt.date(2021, 6, 30):
        if d.weekday() < 5:
            out.append(d.isoformat())
        d += dt.timedelta(days=1)
    return out


def _median_world(neg: bool = True, key_case: bool = False):
    """9001～9005 同產業；2019 各月＝10、11、12、13、14 元，2018 各月＝10 元（9001 的 2018-10 為 −50 → 三月 den −30）。
    最新月 2019-12：YoY＝(30/−30−1)×100＝−200（候選 +200）、10、20、30、40。

    `key_case`：9001 的 2018-11／12＝−25、2018-10＝−50，另有 2020-01＝−50（2020-02-10 起可用，其他四檔沒有這個月）。
    第一日（2020-01-20，最新 2019-12）：9001 YoY 現行 (30/−100−1)×100＝−130、候選 +130 → 中位數 20／30；
    第二日（2020-02-10，9001 最新 2020-01）：num −30、den −40 → 現行 −25、候選 +25 → 中位數 20／25。
    **現行中位數兩日相同、候選中位數改變**，其他四檔的現行輸入兩日完全相同——run 鍵漏掉候選中位數就會沿用第一日。"""
    cal = _cal()
    ind = {f"900{i}": "水泥工業" for i in range(1, 6)}
    stocks = {}
    for i in range(1, 6):
        sid = f"900{i}"
        rows = []
        for k in range(24):
            y, m = 2018 + k // 12, k % 12 + 1
            v = 10.0 if y == 2018 else 9.0 + i
            if neg and sid == "9001" and (y, m) == (2018, 10):
                v = -50.0
            if key_case and sid == "9001" and (y, m) in {(2018, 11), (2018, 12)}:
                v = -25.0
            rows.append((y, m, v))
        if key_case and sid == "9001":
            rows.append((2020, 1, -50.0))
        stocks[sid] = FUND.build_stock(sid, "水泥工業", rows, [], {}, cal)
    bridge = FUND.FundamentalsBridge(stocks, ind)
    days = ["2020-01-20", "2020-02-10"] if key_case else ["2020-01-20", "2020-01-21"]
    ps = {m: build_params(m) for m in RN.MARKETS}
    l1, rw, unk, hh, dd, ss = [], [], [], [], [], []
    for di, d in enumerate(days):
        for si_ix, sid in enumerate(sorted(ind)):
            for h in RN.HORIZONS:
                lr = STK.line1_operations(RB.line1_inputs("twse", sid, d, "水泥工業", bridge.inputs_for(sid, d)), ps["twse"], h)
                l1.append(math.nan if lr.score is None else lr.score)
                rw.append(int(lr.reweighted))
                unk.append(int(lr.unknown))
                hh.append(RN.RB.H_CODE[h])
                dd.append(di)
                ss.append(si_ix)
    n = len(l1)
    rows = {"n": n, "sid_of": sorted(ind), "dates": days, "sid": np.array(ss, np.int32), "date": np.array(dd, np.int32),
            "mk": np.zeros(n, np.int8), "h": np.array(hh, np.int8), "pool": np.ones(n, np.int8), "l1": np.array(l1),
            "rw": np.array(rw, np.int8), "unk": np.array(unk, np.int8)}
    return _Src(days, _Pool(ind)), FUND.FundamentalsBridge(stocks, ind), rows, ps


def _run_median_world(neg=True, key_case=False):
    src, bridge, rows, ps = _median_world(neg, key_case)
    with RB.instrumented() as (cap, sw):
        runs, nr, ror, med, cands = RN.run_a(src, bridge, rows, ps, cap, sw, quiet=True)
    Rr, N = runs.np(), nr.np()
    RB.parity_a(rows, Rr, ror)
    return src, bridge, rows, Rr, N, ror, med, cands


def test_median_scenario_hand():
    """現行中位數＝median(−200, 10, 20, 30, 40)＝20；候選＝median(200, 10, 20, 30, 40)＝30（真實 _industry_stats）。
    9002（den>0）的族 C：sub_only x＝10−20、with_median x＝10−30；9001 的族 C 兩情境都因 den<0 而改變。"""
    _, _, rows, Rr, N, ror, med, (c_stk, c_fund) = _run_median_world()
    y = [(30.0 / -30.0 - 1.0) * 100.0] + [((30.0 + 3 * i) / 30.0 - 1.0) * 100.0 for i in range(1, 5)]
    yc = [200.0] + y[1:]
    med0, med1 = statistics.median(y), statistics.median(yc)
    assert (round(med0, 9), round(med1, 9)) == (20.0, 30.0)
    s = med.summary()
    assert s["days"] == 2 and s["industry_days"] == 2 and s["industry_days_changed"] == 2 and s["days_with_change"] == 2
    assert s["delta"]["min"] == s["delta"]["max"] == med1 - med0
    assert c_fund.calls > 0 and c_fund.rewritten == 2 and c_stk.rewritten > 0
    mid = RN.RB.H_CODE["mid"]
    r2 = int(ror[next(i for i in range(rows["n"]) if rows["sid_of"][rows["sid"][i]] == "9002" and rows["h"][i] == mid)])
    vs = "revenue_yoy_vs_industry"
    assert N["med0"][r2] == med0 and N["med1"][r2] == med1
    assert N["x0"][vs][r2] == y[1] - med0 == N["x1"][("sub_only", vs)][r2]
    assert N["x1"][("with_median", vs)][r2] == y[1] - med1
    assert (N["ch"][("sub_only", vs)][r2], N["ch"][("with_median", vs)][r2]) == (0, 1)
    assert (N["aff"]["sub_only"][r2], N["aff"]["with_median"][r2]) == (0, 1)
    assert N["score"]["sub_only"][r2] == Rr["score"][r2] != N["score"]["with_median"][r2]
    r1 = int(ror[next(i for i in range(rows["n"]) if rows["sid_of"][rows["sid"][i]] == "9001" and rows["h"][i] == mid)])
    assert N["x1"][("sub_only", vs)][r1] == 200.0 - med0 and N["x1"][("with_median", vs)][r1] == 200.0 - med1
    # 彙總：中期 with_median 的受影響列＝全部 10 列、sub_only 只有 9001 的 2 列
    w = np.bincount(ror, minlength=Rr["score"].size).astype(np.int64)
    cf = {c["scenario"]: c for c in RN.summarize_cf(Rr, N, w) if c["horizon"] == "mid"}
    assert cf["sub_only"]["affected_rows"] == 2 and cf["with_median"]["affected_rows"] == 10
    assert cf["with_median"]["subs"][vs]["input_changed_rows"] == 10 and cf["sub_only"]["subs"][vs]["input_changed_rows"] == 2
    short = {c["scenario"]: c for c in RN.summarize_cf(Rr, N, w) if c["horizon"] == "short"}
    assert short["sub_only"]["affected_rows"] == short["with_median"]["affected_rows"]   # 非中期兩情境相同


def test_run_key_includes_candidate_median():
    """現行輸入兩日相同、只有候選中位數改變（30→25）：9002 中期第二日的 with_median x 必須用 25，不得沿用第一日的 run。"""
    _, _, rows, _, N, ror, med, _ = _run_median_world(key_case=True)
    y2 = (33.0 / 30.0 - 1.0) * 100.0
    y1c = [(30.0 + 100.0) / 100.0 * 100.0, y2, (36.0 / 30.0 - 1.0) * 100.0, (39.0 / 30.0 - 1.0) * 100.0, (42.0 / 30.0 - 1.0) * 100.0]
    y2c = [(-30.0 + 40.0) / 40.0 * 100.0, *y1c[1:]]
    mid = RN.RB.H_CODE["mid"]
    rid = {rows["dates"][rows["date"][i]]: int(ror[i]) for i in range(rows["n"])
           if rows["sid_of"][rows["sid"][i]] == "9002" and rows["h"][i] == mid}
    assert rid["2020-01-20"] != rid["2020-02-10"]
    assert N["med0"][rid["2020-01-20"]] == N["med0"][rid["2020-02-10"]]
    vs = ("with_median", "revenue_yoy_vs_industry")
    assert N["x1"][vs][rid["2020-01-20"]] == y2 - statistics.median(y1c)
    assert N["x1"][vs][rid["2020-02-10"]] == y2 - statistics.median(y2c)
    assert round(statistics.median(y2c), 9) == 25.0 and med.summary()["industry_days_changed"] == 2


def test_median_world_without_negative_is_identity():
    """沒有負值：兩情境全部列都不受影響、中位數一日都沒變。"""
    _, _, _, Rr, N, _, med, (c_stk, c_fund) = _run_median_world(neg=False)
    assert med.summary()["industry_days_changed"] == 0 and c_stk.rewritten == 0 and c_fund.rewritten == 0
    for sc in RN.SCEN:
        assert not N["aff"][sc].any() and np.array_equal(N["score"][sc], Rr["score"])


# ---------------------------------------------------------------------------
# 原始月營收的負值、股票清單
# ---------------------------------------------------------------------------

def test_raw_negative_counts(census):
    """1102 六個負值月（2018-10、2019-01/02/03、2020-02/03）、6488 兩個（2020-01/02）、池外 9999 一個（2020-01）。"""
    res, _ = census
    raw = res["raw_negative"]
    assert raw["raw_all"] == {"rows": 9, "stock_months": 9, "stocks": 3, "min": -40.0, "max": -2.0}
    assert raw["raw_pool"] == {"rows": 8, "stock_months": 8, "stocks": 2, "min": -40.0, "max": -2.0}
    assert raw["raw_all_sample"]["rows"] == 5 and raw["raw_pool_sample"] == {"rows": 4, "stock_months": 4, "stocks": 2,
                                                                             "min": -30.0, "max": -2.0}
    assert raw["bridge_all"] == {"stock_months": 8, "stocks": 2} and raw["bridge_sample"] == {"stock_months": 4, "stocks": 2}
    assert res["stocks"]["referenced_neg_stock_months"] == 8 and res["stocks"]["referenced_neg_stocks"] == 2


def test_stock_list(census, world):
    res, _ = census
    st = res["stocks"]
    assert st["total_stocks"] == 2 and not st["list_truncated"]
    a, b = st["listed"]
    # 1102：三個期間每一列都至少有一組 den<0（三月近組 2019-11／12、前組 2020-01～03、單月 2020-01～03）
    n1102 = _sql(world, "SELECT COUNT(*) FROM scores WHERE scope='stock' AND stock_id='1102'")
    assert (a["stock_id"], a["name"], a["industry"], a["is_financial"], a["markets"]) == ("1102", "乙", "水泥工業", False, ["twse"])
    assert a["rows"] == a["den_neg_rows"] == n1102
    assert a["num_neg_rows"] == _cnt(world, "1102", "short", "2020-03-10") + 2 * _cnt(world, "1102", "swing", "2020-04-10")
    assert (a["neg_month_first"], a["neg_month_last"], a["neg_months_referenced"]) == ("2018-10", "2020-03", 6)
    assert (a["neg_value_min"], a["neg_value_max"]) == (-40.0, -2.0)
    assert (a["asof_latest_month_min"], a["asof_latest_month_max"]) == ("2019-11", "2020-03")
    assert a["neg_months_in_history"] == 6 and a["neg_months_in_history_sample"] == 2
    # 6488：短線 02-10 起（單月 2020-01 −5）、波段／中期 03-10 起（三月組含 2020-02 −30）
    want = _cnt(world, "6488", "short", "2020-02-10") + _cnt(world, "6488", "swing", "2020-03-10") + _cnt(world, "6488", "mid", "2020-03-10")
    assert (b["stock_id"], b["markets"], b["rows"], b["den_neg_rows"], b["num_neg_rows"]) == ("6488", ["tpex"], want, 0, want)
    assert st["rows_total"] == a["rows"] + b["rows"]
    assert st["financial_split"] == {"financial": {"stocks": 0, "rows": 0},
                                     "non_financial": {"stocks": 2, "rows": a["rows"] + b["rows"]}}
    assert [x["industry"] for x in st["industry_top"]] == ["水泥工業", "光電業"]


def test_stock_list_truncates(monkeypatch, census):
    """> 上限時只列前 N 檔、另給總數（上限調成 1 驗行為）。"""
    res, _ = census
    det = res["_details"]
    monkeypatch.setattr(RN, "STOCK_LIST_MAX", 1)

    class _S:
        pool = _Pool({"1102": "水泥工業", "6488": "光電業"})
    st = RN.summarize_stocks(_S(), type("B", (), {"stocks": {}})(), det["rows"], det["R"], det["N"], det["w_run"], *Y2020)
    assert st["total_stocks"] == 2 and len(st["listed"]) == 1 and st["list_truncated"] and st["listed"][0]["stock_id"] == "1102"


# ---------------------------------------------------------------------------
# parity 與守門：紅且紅得對
# ---------------------------------------------------------------------------

def test_population_and_parity(census, world):
    res, _ = census
    n = _sql(world, "SELECT COUNT(*) FROM scores WHERE scope='stock' AND date BETWEEN ? AND ?", Y2020)
    assert res["population"]["rows"] == res["parity_a"]["rows"] == n
    assert res["parity_b"]["rows"] == res["stocks"]["rows_total"] == sum(s["population"] for s in res["b_strata"])
    assert all(s["sampled"] == s["population"] for s in res["b_strata"])


def test_b_samples_only_negative_rows(census):
    res, _ = census
    det = res["_details"]
    N = det["N"]
    for s in det["samples"]:
        r = s["run"]
        assert any((N["den"][g][r] < 0) or (N["num"][g][r] < 0) for g in RN.GNAMES)
    rng = np.random.default_rng(1)
    neg = np.zeros(det["R"]["score"].size, bool)
    for g in RN.GNAMES:
        neg |= (N["den"][g] < 0) | (N["num"][g] < 0)
    samples, strata = RN.draw_b(det["rows"], neg, det["run_of_row"], 5, rng)
    assert all(st["sampled"] == min(5, st["population"]) for st in strata) and len(samples) == sum(st["sampled"] for st in strata)
    with pytest.raises(RN.RevNegError, match="至少 1"):
        RN.draw_b(det["rows"], neg, det["run_of_row"], 0, rng)


def _tamper_db(world, tmp_path, sql, args=()) -> Path:
    c = _copy(world, tmp_path)
    con = sqlite3.connect(c / "scores.db")
    con.execute(sql, args)
    con.commit()
    con.close()
    return c


def test_db_score_tamper_hits_a_parity(world, tmp_path):
    c = _tamper_db(world, tmp_path, "UPDATE scores SET line_1 = line_1 + 0.5 WHERE stock_id='1102' AND date='2020-02-10' AND horizon='short'")
    with pytest.raises(RB.ParityError, match=r"A 全母體 parity 不符 1／") as e:
        _run(c, tmp_path / "r.json", db=c / "scores.db")
    assert "1102 2020-02-10" in str(e.value)


def test_b_parity_on_negative_rows(world, tmp_path, monkeypatch):
    """B（真實重播路徑）初爻偏 1e-6 → 抽到的負值列不符 → 中止。"""
    orig = STK.score_stock

    def wrapped(inp, ps, h):
        ss = orig(inp, ps, h)
        if inp.stock_id == "1102":
            lr = ss.lines["1"]
            ss = dataclasses.replace(ss, lines={**ss.lines, "1": dataclasses.replace(lr, score=lr.score + 1e-6)})
        return ss
    monkeypatch.setattr(STK, "score_stock", wrapped)
    with pytest.raises(RB.ParityError, match=r"B 抽樣 parity.*\n.*1102.*初爻 \|差\|"):
        _run(world, tmp_path / "r.json")


def test_guard1_den_pos_literal_mutation(world, tmp_path, monkeypatch):
    """突變：候選式在 den>0 也改成字面式子 → den>0 的列不逐位相同 → 守門 1 中止（訊息指向「輸入未改變」）。"""
    def call(self, rev, latest, offset_months=0, months=3):
        self.calls += 1
        r = self.orig(rev, latest, offset_months, months)
        if isinstance(r, Missing):
            return r
        num, den = RB.base_sums(rev, latest, offset_months, months)
        return (num - den) / abs(den) * 100.0
    monkeypatch.setattr(RN.Candidate, "__call__", call)
    with pytest.raises(RN.RevNegError, match=r"輸入未改變（den 皆非負.*不逐位相同"):
        _run(world, tmp_path / "r.json")


def test_guard2_candidate_not_applied(world, tmp_path, monkeypatch):
    """突變：替身不改寫（den<0 也回現行值）→ 替換後 x ≠ 候選式 → 守門 2 中止。"""
    monkeypatch.setattr(RN.Candidate, "__call__", lambda self, rev, latest, o=0, m=3: (setattr(self, "calls", self.calls + 1),
                                                                                         self.orig(rev, latest, o, m))[1])
    with pytest.raises(RN.RevNegError, match=r"替身未生效"):
        _run(world, tmp_path / "r.json")


def test_guard2_presence_changed(world, tmp_path, monkeypatch):
    """突變：替身把 den<0 改成缺值 → 在場／缺值改變（中位數那一道先擋：產業樣本數改變）。"""
    orig = RN.Candidate.__call__

    def call(self, rev, latest, offset_months=0, months=3):
        r = orig(self, rev, latest, offset_months, months)
        ns = RB.base_sums(rev, latest, offset_months, months)
        return Missing(REASON_DENOM_ZERO, "x") if ns is not None and ns[1] < 0 else r
    monkeypatch.setattr(RN.Candidate, "__call__", call)
    with pytest.raises(RN.RevNegError, match=r"候選式改變了產業樣本數"):
        _run(world, tmp_path / "r.json")
    monkeypatch.setattr(RN, "patched", _patched_stk_only(call))
    with pytest.raises(RN.RevNegError, match=r"替換改變了在場／缺值"):
        _run(world, tmp_path / "r.json")


def _patched_stk_only(call):
    """產業中位數那一側不替換（跳過守門 4），子指標那一側照替換，讓守門 2 的在場檢查能單獨被測。"""
    from contextlib import contextmanager
    real = RN.patched

    @contextmanager
    def p(mods, cand):
        if mods == (RN.FUND,):
            yield cand
            return
        with real(mods, cand):
            yield cand
    return p


def test_guard3_same_function_and_calls(world, tmp_path, monkeypatch):
    monkeypatch.setattr(FUND, "revenue_yoy_3m", lambda *a, **k: STK.revenue_yoy_3m(*a, **k))
    with pytest.raises(RN.RevNegError, match=r"不是同一支函式"):
        _run(world, tmp_path / "r.json")


def test_guard3_never_called():
    """替換沒有真的裝上（無負值世界、patched 失效）→ 替身 0 次呼叫 → 中止。"""
    from contextlib import contextmanager

    @contextmanager
    def noop(mods, cand):
        yield cand
    src, bridge, rows, ps = _median_world(neg=False)
    orig = RN.patched
    RN.patched = noop
    try:
        with RB.instrumented() as (cap, sw), pytest.raises(RN.RevNegError, match=r"候選替身沒有被呼叫"):
            RN.run_a(src, bridge, rows, ps, cap, sw, quiet=True)
    finally:
        RN.patched = orig


def test_guard5_negative_sum_without_negative_month(world, tmp_path, monkeypatch):
    orig = RN._sums
    def tampered(r, lt, o, m):
        x = orig(r, lt, o, m)
        return None if x is None else (x[0], -abs(x[1]) - 1.0)
    monkeypatch.setattr(RN, "_sums", tampered)
    with pytest.raises(RN.RevNegError, match=r"num／den 為負.*沒有負值月"):
        _run(world, tmp_path / "r.json")


def test_record_base_must_match_line1_detail(world, tmp_path, monkeypatch):
    """本檔記錄的 den 與 §29 line1_detail 的 den 不同 → 中止（記錄路徑不能與已核對的基期脫鉤）。"""
    orig = RN._sums
    def tampered(r, lt, o, m):
        x = orig(r, lt, o, m)
        return None if x is None else (x[0], x[1] + 1.0)
    monkeypatch.setattr(RN, "_sums", tampered)
    with pytest.raises(RN.RevNegError, match=r"本檔基期 .* ≠ §29 line1_detail 的基期"):
        _run(world, tmp_path / "r.json")


def _patched_line1(fn):
    """替換期間另把 `line1_operations` 的結果交給 `fn` 改寫（模擬「子指標不變、聚合卻變了」之類的失效）。"""
    from contextlib import contextmanager
    real = RN.patched

    @contextmanager
    def p(mods, cand):
        with real(mods, cand):
            if mods != (RN.STK,):
                yield cand
                return
            orig = RN.STK.line1_operations
            RN.STK.line1_operations = lambda si, ps, h: fn(orig(si, ps, h), h)
            try:
                yield cand
            finally:
                RN.STK.line1_operations = orig
    return p


def test_guard1_line_changed_without_input_change(world, tmp_path, monkeypatch):
    """子指標全都沒變、初爻卻變了（+1e-9）→ 中止。"""
    monkeypatch.setattr(RN, "patched", _patched_line1(
        lambda lr, h: dataclasses.replace(lr, score=None if lr.score is None else lr.score + 1e-9)))
    with pytest.raises(RN.RevNegError, match=r"沒有任何子指標輸入改變，但初爻"):
        _run(world, tmp_path / "r.json")


def test_guard_sub_set_changed(world, tmp_path, monkeypatch):
    """替換後少了一個營收子指標（中期族 C 整族拿掉）→ 中止。"""
    monkeypatch.setattr(RN, "patched", _patched_line1(
        lambda lr, h: dataclasses.replace(lr, families=tuple(f for f in lr.families if f.family != "C"))))
    with pytest.raises(RN.RevNegError, match=r"替換後的營收子指標集合"):
        _run(world, tmp_path / "r.json")


def test_guard_cand_bridge_other_inputs(world, tmp_path, monkeypatch):
    """候選橋除了中位數之外還改了別的輸入（樣本數）→ 中止。"""
    class Bad(FUND.FundamentalsBridge):
        def inputs_for(self, sid, T):
            d = super().inputs_for(sid, T)
            return {**d, "industry_revenue_n": (d["industry_revenue_n"] or 0) + 1}
    monkeypatch.setattr(RN, "FundamentalsBridge", Bad)
    with pytest.raises(RN.RevNegError, match=r"候選橋的 industry_revenue_n ≠ 現行"):
        _run(world, tmp_path / "r.json")


def test_guard_det_rerun_mismatch(world, tmp_path, monkeypatch):
    """line1_detail 的初爻與本檔重跑的現行初爻不同 → 中止。"""
    orig = RB.line1_detail

    def wrapped(si, ps, h, cap):
        det = orig(si, ps, h, cap)
        if det["score"] is not None:
            det["score"] += 1e-9
        return det
    monkeypatch.setattr(RB, "line1_detail", wrapped)
    with pytest.raises(RN.RevNegError, match=r"現行 line1_operations 重跑"):
        _run(world, tmp_path / "r.json")


def test_guard_run_id_alignment():
    with pytest.raises(RN.RevNegError, match="run id 錯位"):
        RN.record_run(RN.NegRuns(), 3, None, None, None, "short", {}, RN.Candidate(), "t")


def test_guard_db_date_and_market(world, tmp_path):
    c = _tamper_db(world, tmp_path, "UPDATE scores SET date='2020-01-21' WHERE stock_id='1102' AND date='2020-01-20' AND horizon='mid'")
    with pytest.raises(RN.RevNegError, match="db 日期不在原料交易日軸"):
        _run(c, tmp_path / "r.json", db=c / "scores.db")
    con = sqlite3.connect(world / "scores.db")
    vid = con.execute("SELECT DISTINCT version_id FROM scores WHERE market='tpex'").fetchone()[0]
    con.close()
    c2 = _tamper_db(world, tmp_path / "b", "UPDATE scores SET market='tpex', version_id=? WHERE stock_id='1102' "
                    "AND date='2020-01-20' AND horizon='mid'", (vid,))
    with pytest.raises(RN.RevNegError, match=r"1102 2020-01-20：pool.listed=twse ≠ db 列市場 tpex"):
        _run(c2, tmp_path / "r.json", db=c2 / "scores.db")


def test_guard_raw_table_missing():
    class S:
        fundamentals_db = None
    with pytest.raises(RN.RevNegError, match="沒有 fundamentals.db"):
        RN.raw_negatives(S(), None, *Y2020)
    S.fundamentals_db = sqlite3.connect(":memory:")
    with pytest.raises(RN.RevNegError, match="raw_month_revenue"):
        RN.raw_negatives(S(), None, *Y2020)


def test_guard_candidate_months_inconsistent(monkeypatch):
    monkeypatch.setattr(RB, "base_sums", lambda *a: None)
    with pytest.raises(RN.RevNegError, match="月份不齊"):
        RN.Candidate()({"2019-01": 3.0, "2020-01": 5.0}, "2020-01", 0, 1)


def test_guard_median_industry_set(monkeypatch):
    m = RN.MedStats()
    with pytest.raises(RN.RevNegError, match="產業集合"):
        m.add("2020-01-02", {"A": (1.0, 5)}, {"B": (1.0, 5)})
    with pytest.raises(RN.RevNegError, match="產業樣本數"):
        m.add("2020-01-02", {"A": (1.0, 5)}, {"A": (1.0, 4)})


def test_other_guards_reused(world, tmp_path):
    """§29 的 db 守門沿用：params_sha 竄改、空樣本段、--per-group 下限。"""
    c = _tamper_db(world, tmp_path, "UPDATE replay_meta SET params_sha='000000000000'")
    with pytest.raises(Exception, match="params_sha"):
        _run(c, tmp_path / "r.json", db=c / "scores.db")
    with pytest.raises(RB.RevBaseError, match="沒有任何個股列"):
        RN.run(world / "scores.db", tmp_path / "r.json", cache_dir=world, start="2019-01-01", end="2019-06-30", quiet=True)
    with pytest.raises(RN.RevNegError, match="至少 1.*開跑前檢查"):
        _run(world, tmp_path / "r.json", per_group=0)


# ---------------------------------------------------------------------------
# 報告檔、CLI
# ---------------------------------------------------------------------------

def test_report_files_and_readonly(world, tmp_path):
    h = hashlib.sha256((world / "scores.db").read_bytes()).hexdigest()
    res = _run(world, tmp_path / "out" / "rep.json")
    assert hashlib.sha256((world / "scores.db").read_bytes()).hexdigest() == h
    saved = json.loads((tmp_path / "out" / "rep.json").read_text(encoding="utf-8"))
    assert "_details" not in saved and saved["schema"] == 1 and saved["per_group"] == 100000
    txt = (tmp_path / "out" / "rep.txt").read_text(encoding="utf-8")
    assert txt.startswith("營收子指標負值基期量測") and "A 全母體 parity" in txt and "== 反事實" in txt
    assert "1102 乙 水泥工業" in txt and "== 原始月營收的負值" in txt and res["population"]["rows"] > 0
    for bad in ("建議", "可能是因為", "推測", "應該"):
        assert bad not in txt


def test_cli(world, tmp_path, capsys):
    rc = RN.main(["--db", str(world / "scores.db"), "--cache-dir", str(world), "--out", str(tmp_path / "r.json"), "--quiet"])
    assert rc == 2 and "沒有任何個股列" in capsys.readouterr().err and not (tmp_path / "r.json").exists()
    assert (RN.SAMPLE_START, RN.SAMPLE_END) == ("2021-01-01", "2024-12-31")
    with pytest.raises(SystemExit):
        RN.main(["--db", "x", "--out", "y", "--start", "2020-01-01"])


def test_cli_ok(world, tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(RN, "SAMPLE_START", Y2020[0])
    monkeypatch.setattr(RN, "SAMPLE_END", Y2020[1])
    rc = RN.main(["--db", str(world / "scores.db"), "--cache-dir", str(world), "--out", str(tmp_path / "ok.json"), "--quiet"])
    assert rc == 0 and (tmp_path / "ok.json").exists() and (tmp_path / "ok.txt").exists()
    assert "涉及負值 2 檔" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# hetzner_revneg.sh：真的跑一遍（假 python3＋本機 bare repo）
# ---------------------------------------------------------------------------

STUB_PY = r'''#!@@PY@@
import os, pathlib, sys
a = sys.argv[1:]
log = os.environ.get("STUB_LOG")
if log:
    with open(log, "a", encoding="utf-8") as f:
        f.write(" ".join(a[:1] if a and a[0] != "-c" else ["-c"]) + "\n")
if a and a[0] == "-c":
    if "ScoreStore" in a[1]:
        print(os.environ.get("STUB_TO", "2026-09-19")); raise SystemExit(0)
    raise SystemExit(f"stub: 未預期的 -c：{a[1][:40]}")
if a and a[0].endswith("score_ranges.py"):
    raise SystemExit(int(os.environ.get("STUB_RANGES_RC", "0")))
if a and a[0].endswith("revenue_negative_base.py"):
    rc = int(os.environ.get("STUB_RN_RC", "0"))
    assert a[a.index("--db") + 1] == "cache/scores.db"
    out = pathlib.Path(a[a.index("--out") + 1])
    assert str(out).startswith("runs/revneg/report_")
    if (rc == 0 or os.environ.get("STUB_RN_WRITE_ON_FAIL")) and not os.environ.get("STUB_RN_EMPTY"):
        out.write_text('{"schema": 1}', encoding="utf-8")
        out.with_suffix(".txt").write_text("revneg\n", encoding="utf-8")
    raise SystemExit(rc)
raise SystemExit(f"stub: 未預期的呼叫：{a[:3]}")
'''


def _git(cwd, *args):
    subprocess.run(["git", *args], cwd=cwd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def _sandbox(tmp_path, *, with_db=True):
    origin = tmp_path / "origin.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(origin)], check=True)
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.email", "t@example.invalid")
    _git(repo, "config", "user.name", "t")
    (repo / "README.md").write_text("x\n", encoding="utf-8")
    _git(repo, "add", "README.md")
    _git(repo, "commit", "-qm", "init")
    _git(repo, "remote", "add", "origin", str(origin))
    _git(repo, "push", "-q", "-u", "origin", "main")
    (repo / "scripts").mkdir()
    (repo / "scripts" / "hetzner_revneg.sh").write_bytes(SCRIPT.read_bytes())
    (repo / "cache").mkdir()
    if with_db:
        (repo / "cache" / "scores.db").write_text("db", encoding="utf-8")
    stub = tmp_path / "stub"
    stub.mkdir()
    (stub / "python3").write_text(STUB_PY.replace("@@PY@@", sys.executable), encoding="utf-8")
    (stub / "python3").chmod(0o755)
    return repo, origin, stub


def _sh(repo, stub, **env):
    e = dict(os.environ, PATH=f"{stub}{os.pathsep}{os.environ['PATH']}", **env)
    return subprocess.run(["bash", "scripts/hetzner_revneg.sh"], cwd=repo, env=e, capture_output=True, text=True, timeout=180,
                          check=False)


def _branches(origin):
    out = subprocess.run(["git", "ls-remote", "--heads", str(origin)], capture_output=True, text=True, check=True).stdout
    return sorted(ln.split("refs/heads/")[-1] for ln in out.splitlines() if ln.strip())


def _tree(origin, br):
    return subprocess.run(["git", "ls-tree", "-r", "--name-only", br], cwd=origin,
                          capture_output=True, text=True, check=True).stdout.split()


def test_script_syntax_and_readonly():
    subprocess.run(["bash", "-n", str(SCRIPT)], check=True)
    t = SCRIPT.read_text(encoding="utf-8")
    assert "replay_scores.py" not in t and "--rebuild" not in t
    assert "  set -e\n" in t and "score_ranges.py --check" in t and "--force-with-lease" in t
    assert "tmux new -d -s revneg 'bash scripts/hetzner_revneg.sh'" in t
    assert "revbase" not in t.replace("hetzner_revbase.sh", "")        # 仿本不殘留舊名（註解提到仿本檔名除外）


@pytest.mark.skipif(not shutil.which("git"), reason="需要 git")
def test_sh_happy_path_pushes_report_only(tmp_path):
    repo, origin, stub = _sandbox(tmp_path)
    log = tmp_path / "calls.log"
    r = _sh(repo, stub, STUB_LOG=str(log))
    assert r.returncode == 0, r.stdout + r.stderr
    assert _branches(origin) == ["hetzner/revneg-2026-09-19", "main"]
    files = _tree(origin, "hetzner/revneg-2026-09-19")
    assert {"runs/revneg/report_2026-09-19.json", "runs/revneg/report_2026-09-19.txt"} <= set(files)
    assert not any(f.endswith(".db") for f in files)
    calls = log.read_text(encoding="utf-8").split("\n")
    assert calls.index("scripts/score_ranges.py") < calls.index("scripts/revenue_negative_base.py")
    assert (repo / "cache" / "logs" / "revneg.log").exists()
    r2 = _sh(repo, stub, STUB_TO="2026-09-19")
    assert r2.returncode == 0, r2.stdout + r2.stderr


@pytest.mark.skipif(not shutil.which("git"), reason="需要 git")
def test_sh_measure_failure_rc3_no_push(tmp_path):
    """量測 rc≠0 即 rc=3、不推送——即使量測程式留下了（部分）產物也一樣（不能靠「產物為空」那道補位）。"""
    repo, origin, stub = _sandbox(tmp_path)
    r = _sh(repo, stub, STUB_RN_RC="2", STUB_RN_WRITE_ON_FAIL="1")
    assert r.returncode == 3 and "營收負值基期量測失敗" in r.stdout and "未推送報告" in r.stdout
    assert "沒產出或是空的" not in r.stdout and _branches(origin) == ["main"]


@pytest.mark.skipif(not shutil.which("git"), reason="需要 git")
def test_sh_empty_output_rc3_no_push(tmp_path):
    repo, origin, stub = _sandbox(tmp_path)
    r = _sh(repo, stub, STUB_RN_EMPTY="1")
    assert r.returncode == 3 and "沒產出或是空的" in r.stdout and _branches(origin) == ["main"]


@pytest.mark.skipif(not shutil.which("git"), reason="需要 git")
def test_sh_registry_out_of_date_rc2_no_measure(tmp_path):
    repo, origin, stub = _sandbox(tmp_path)
    log = tmp_path / "calls.log"
    r = _sh(repo, stub, STUB_RANGES_RC="1", STUB_LOG=str(log))
    assert r.returncode == 2 and "未推送報告" in r.stdout
    assert "revenue_negative_base.py" not in log.read_text(encoding="utf-8") and _branches(origin) == ["main"]


@pytest.mark.skipif(not shutil.which("git"), reason="需要 git")
def test_sh_missing_db_rc2(tmp_path):
    repo, origin, stub = _sandbox(tmp_path, with_db=False)
    r = _sh(repo, stub)
    assert r.returncode == 2 and "不存在" in r.stdout and _branches(origin) == ["main"]


@pytest.mark.skipif(not shutil.which("git"), reason="需要 git")
def test_sh_dirty_tree_rc2(tmp_path):
    repo, origin, stub = _sandbox(tmp_path)
    (repo / "README.md").write_text("changed\n", encoding="utf-8")
    r = _sh(repo, stub)
    assert r.returncode == 2 and "工作樹不乾淨" in r.stdout and _branches(origin) == ["main"]


@pytest.mark.skipif(not shutil.which("git"), reason="需要 git")
def test_sh_git_failure_stops_before_measure(tmp_path):
    repo, origin, stub = _sandbox(tmp_path)
    log = tmp_path / "calls.log"
    _git(repo, "remote", "set-url", "origin", str(tmp_path / "nope.git"))
    r = _sh(repo, stub, STUB_LOG=str(log))
    assert r.returncode != 0 and "== 2" not in r.stdout
    assert not log.exists() or "revenue_negative_base.py" not in log.read_text(encoding="utf-8")
    assert _branches(origin) == ["main"]
