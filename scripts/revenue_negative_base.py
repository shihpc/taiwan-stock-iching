#!/usr/bin/env python3
"""營收子指標的**負值基期**量測（唯讀；`docs/P3-CALIBRATION.md` §30）。

§29 的生產量測量到 `revenue_yoy_3m` 的分母（去年同期合計，下稱 den）有負值，但沒量確切列數與股票。
`revenue_yoy_3m` 算 `(num/den − 1)×100`、只擋 `den == 0`；本檔量：負值出現在哪些列、哪些股票、原始月營收有多少負值，
以及候選式 `(num − den)／|den| × 100` 若套用在 den < 0 的列，初爻會怎麼變。

**只量測：`src/` 零改動、不寫 db、不提建議。** 候選式**未採用**，反事實只在記憶體內重算。
樣本段寫死＝`SEGMENTS["train"][0]`～`SEGMENTS["valid"][1]`（同 §29，裁定 #64 ①），CLI 不開日期參數。

## 重用 §29（`scripts/revenue_base_impact.py`，下稱 RB）

母體讀取（`RB.read_rows`）、db 守門（`RB.check_db`）、使用處掃描（`RB.usage_scan`）、A 路徑的最小 `StockInputs`
（`RB.line1_inputs`）、真實 `line1_operations`＋攔截與基期核對（`RB.line1_detail`／`RB.instrumented`）、run-length 表
（`RB.Runs`）、A 全母體 parity（`RB.parity_a`）、B 真實重播 parity（`RB.run_b`）、加權分位數（`RB.wquantiles`）。
本檔**不**複寫這些；新增的只有：num／den 的逐列記錄、候選式的攔截替身、產業中位數的候選版、彙總與報告。

## 口徑

- **num／den**：以 `RB.base_sums`（月份清單與加總式子逐字同 `revenue_yoy_3m`，§29 已逐次核對）取每個營收子指標所用的
  月份組：`revenue_yoy`（短線 1 個月、波段／中期 3 個月）、`revenue_accel` 的**近組**（offset 0）與**前組**（offset＝window）、
  中期 `revenue_yoy_vs_industry`（3 個月，與中期 `revenue_yoy` 同一組月份）。每列四類：den<0、num<0、兩者皆<0、den>0 且 num<0；
  另記 den＝0（現行即缺值）。分母＝該組（market × horizon）db 列數；run-length 展開（同 §29）。
- **候選式（未採用）**：在 `revenue_yoy_3m` 的呼叫處換成替身 `Candidate`——先呼叫原函式，缺值與 **den>0 原樣回傳**
  （＝現行式子），只有 den<0 改成 `(num − den)／|den| × 100`。den>0 走原式子是刻意的：數學上兩式相等，但浮點不必然逐位相同，
  本檔另外量「若 den>0 也用字面式子，有幾列不逐位相同」（`literal_den_pos`，只量不套用）。
- **兩個情境**：`sub_only`＝只替換 `score/stock.py` 的 `revenue_yoy_3m`（子指標），產業中位數維持現行；
  `with_median`＝另把 `fundamentals.py` 的 `revenue_yoy_3m` 也替換，以**真實** `FundamentalsBridge._industry_stats`
  重算一份候選產業中位數，中期列改用它（族 C 的輸入）。短線／波段沒有族 C，兩情境相同。
- **反事實**：替換後重跑**真實** `line1_operations`（真實 `S_clip`、同一 c／d／direction、真實族／爻聚合），比對現行：
  子指標 x 與分數變化、初爻分數變化、以 50 為界的陰陽翻轉（≥50 為陽，只計兩側皆已知）、變未知／變已知、進／出 [45, 55]
  （兩端含，只計兩側皆已知）。普查（run-length 展開後逐列計數）。
- **原始月營收負值**：直接讀 `fundamentals.db` 的 `raw_month_revenue`（重播讀的同一張表、同一個 `data_version`，數值經
  重播同一支 `F_num` 轉型）：全表／池內；以及重播實際看到的（`load_fundamentals` 的橋，池內、同月多列取最後）；
  各分全期間與「營收月落在樣本段內」兩種；另列「樣本段列的加總實際引用到的負值月」。

## 守門（任一不過 → rc=2、不寫報告）

§29 的全部 A 守門（db 血統、model_version、參數指紋、基本面開關、交易日軸、市場＝`pool.listed`、A 全母體 parity、
基期逐位核對、直接 `S_clip`、族歸屬、應有子指標集合）；另加：

1. **den>0 逐位不變**：某子指標所用的月份組沒有 den<0（且 `with_median` 下產業中位數未變）→ 替換前後該子指標的
   x、分數、缺值原因**逐位相同**；整列都沒有 → 初爻分數／reweighted／unknown 逐位相同。
2. **替身真的生效**：den<0 的在場子指標，替換後的 x＝由本檔 num／den 以候選式算出的值（逐位）；在場／缺值不變。
3. **替身有被呼叫**：`score/stock.py` 與 `fundamentals.py` 引用的是同一支 `revenue_yoy_3m`；替身呼叫次數 > 0。
4. **產業樣本數不變**：候選中位數與現行的 `industry_revenue_n` 逐日逐產業相同（候選式不改缺值與否）。
5. **定義核對**：den<0 或 num<0 的列，其加總月份裡必有負值月。
6. **B 抽樣 parity（縮小版）**：只抽「任一月份組 den<0 或 num<0」的列（每組至多 `--per-group`，預設 300），以真實重播路徑
   重算（沿用 `RB.run_b`）。縮小的理由：A 已是全母體 parity；B 的用途是確認這些列在真實重播路徑上收到的子指標輸入與 A 相同。

rc：0 成功／2 中止（任何守門、parity、例外）。報告只陳述數據（不含成因推測與規則）。
"""
from __future__ import annotations

import argparse
import json
import math
import sys
import time
from array import array
from collections import Counter
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import numpy as np

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "src"))
sys.path.insert(0, str(REPO / "scripts"))

from iching import fundamentals as FUND  # noqa: E402
from iching import replay_io as RIO  # noqa: E402
from iching.fundamentals import FundamentalsBridge  # noqa: E402
from iching.score import stock as STK  # noqa: E402
from iching.score.params import HORIZONS, MARKETS, build_params  # noqa: E402
from iching.score.transform import Missing  # noqa: E402
from iching.scores_io import ScoreStore  # noqa: E402
from iching.universe import is_financial  # noqa: E402

import revenue_base_impact as RB  # noqa: E402
import score_stats as SS  # noqa: E402

SAMPLE_START, SAMPLE_END = SS.SAMPLE_START, SS.SAMPLE_END
DEFAULT_PER_GROUP = 300
DEFAULT_SEED = 20260924
SUBS = RB.SUBS
SCEN = ("sub_only", "with_median")
GNAMES = ("revenue_yoy", "revenue_accel.near", "revenue_accel.prev", "revenue_yoy_vs_industry")
G_SUB = {"revenue_yoy": "revenue_yoy", "revenue_accel.near": "revenue_accel", "revenue_accel.prev": "revenue_accel",
         "revenue_yoy_vs_industry": "revenue_yoy_vs_industry"}
DELTA_QS = RB.DELTA_QS
X_QS = (0.0, 0.05, 0.5, 0.95, 1.0)
BAND = RB.BAND
STOCK_LIST_MAX = 200
NEG_MONTHS_SHOWN = 6
INDUSTRY_TOP = 10
ORIG_YOY = STK.revenue_yoy_3m
B_EMPTY = "無負值列可抽、B 未執行"


class RevNegError(Exception):
    pass


# ---------------------------------------------------------------------------
# 候選式（未採用）
# ---------------------------------------------------------------------------
def cand_value(num: float, den: float) -> float:
    """候選式的值（den ≠ 0）：den<0 → (num − den)／|den| × 100；其餘 → 現行式子原樣（同 `Candidate`）。"""
    if not den < 0:
        return (num / den - 1.0) * 100.0
    return (num - den) / abs(den) * 100.0


def literal_value(num: float, den: float) -> float:
    """候選式的**字面**寫法（不分 den 正負）；只用來量 den>0 時與現行式子是否逐位相同，不套用。"""
    return (num - den) / abs(den) * 100.0


class Candidate:
    """`revenue_yoy_3m` 的候選替身：先呼叫原函式（缺值與 den>0 原樣回傳），只在 den<0 時改寫。"""

    def __init__(self, orig=ORIG_YOY) -> None:
        self.orig = orig
        self.calls = 0
        self.rewritten = 0

    def __call__(self, rev, latest, offset_months=0, months=3):
        self.calls += 1
        r = self.orig(rev, latest, offset_months, months)
        if isinstance(r, Missing):
            return r
        ns = RB.base_sums(rev, latest, offset_months, months)
        if ns is None:
            raise RevNegError("候選替身：revenue_yoy_3m 有值但本檔月份不齊（與 §29 基期核對矛盾）")
        num, den = ns
        if not den < 0:                  # den>0（以及非有限值）一律回傳現行式子的值；den＝0 已在上面回缺值
            return r
        self.rewritten += 1
        return (num - den) / abs(den) * 100.0


@contextmanager
def patched(mods, cand: Candidate):
    """在 `mods` 的 `revenue_yoy_3m` 名稱上換成 `cand`；離開時原樣還原。"""
    saved = [(m, m.revenue_yoy_3m) for m in mods]
    try:
        for m in mods:
            m.revenue_yoy_3m = cand
        yield cand
    finally:
        for m, orig in saved:
            m.revenue_yoy_3m = orig


# ---------------------------------------------------------------------------
# 逐 run 記錄
# ---------------------------------------------------------------------------
def group_specs(ps, h: str) -> list[tuple[str, int, int]]:
    """(月份組名, offset, months)；months＝該子指標的 Param.window（同 `line1_operations`）。"""
    w = RB._sub_windows(ps, h)
    out = [("revenue_yoy", 0, w["revenue_yoy"]), ("revenue_accel.near", 0, w["revenue_accel"]),
           ("revenue_accel.prev", w["revenue_accel"], w["revenue_accel"])]
    if h == "mid":
        out.append(("revenue_yoy_vs_industry", 0, w["revenue_yoy_vs_industry"]))
    return out


def _sums(rev: dict[str, float], latest: str, off: int, months: int) -> tuple[float, float] | None:
    """本檔記錄用的 (num, den)＝`RB.base_sums`（§29 已與 `revenue_yoy_3m` 逐位核對）；獨立一層只為測試能單獨竄改記錄路徑。"""
    return RB.base_sums(rev, latest, off, months)


def _subs_of(lr) -> dict[str, Any]:
    return {sr.indicator_id: sr for f in lr.families for sr in f.subs if sr.indicator_id in SUBS}


def _same(a, b) -> bool:
    """逐位相同（None 對 None、NaN 對 NaN 也算相同）。"""
    if a is None or b is None:
        return a is None and b is None
    if isinstance(a, float) and isinstance(b, float) and math.isnan(a) and math.isnan(b):
        return True
    return a == b


def _sub_same(s0, s1) -> bool:
    m0 = None if s0.missing is None else (s0.missing.reason, s0.missing.detail)
    m1 = None if s1.missing is None else (s1.missing.reason, s1.missing.detail)
    return _same(s0.x, s1.x) and _same(s0.score, s1.score) and m0 == m1


class NegRuns:
    """與 `RB.Runs` 同一個 run id 的並行陣列：各月份組 num／den、兩情境的反事實結果。"""

    def __init__(self) -> None:
        self.num = {g: array("d") for g in GNAMES}
        self.den = {g: array("d") for g in GNAMES}
        self.lit = {g: array("b") for g in GNAMES}                 # 1：den>0 且字面式子≠現行（逐位）；0：相同；−1：不適用
        self.med0, self.med1 = array("d"), array("d")
        self.x0 = {s: array("d") for s in SUBS}
        self.sc0 = {s: array("d") for s in SUBS}
        self.score = {sc: array("d") for sc in SCEN}
        self.rw = {sc: array("b") for sc in SCEN}
        self.unk = {sc: array("b") for sc in SCEN}
        self.aff = {sc: array("b") for sc in SCEN}                 # 任一子指標的輸入改變（den<0，或中位數改變）
        self.x1 = {(sc, s): array("d") for sc in SCEN for s in SUBS}
        self.sc1 = {(sc, s): array("d") for sc in SCEN for s in SUBS}
        self.ch = {(sc, s): array("b") for sc in SCEN for s in SUBS}   # 該子指標的輸入改變
        self.latest: list[str | None] = []
        self.negm: dict[int, tuple[tuple[str, float], ...]] = {}   # run → 加總引用到的負值月 (ym, 值)

    def np(self) -> dict[str, Any]:
        f = lambda a, t: np.frombuffer(a, dtype=t) if len(a) else np.zeros(0, dtype=t)  # noqa: E731
        return {"num": {g: f(self.num[g], "d") for g in GNAMES}, "den": {g: f(self.den[g], "d") for g in GNAMES},
                "lit": {g: f(self.lit[g], np.int8) for g in GNAMES},
                "med0": f(self.med0, "d"), "med1": f(self.med1, "d"),
                "x0": {s: f(self.x0[s], "d") for s in SUBS}, "sc0": {s: f(self.sc0[s], "d") for s in SUBS},
                "score": {k: f(v, "d") for k, v in self.score.items()}, "rw": {k: f(v, np.int8) for k, v in self.rw.items()},
                "unk": {k: f(v, np.int8) for k, v in self.unk.items()}, "aff": {k: f(v, np.int8) for k, v in self.aff.items()},
                "x1": {k: f(v, "d") for k, v in self.x1.items()}, "sc1": {k: f(v, "d") for k, v in self.sc1.items()},
                "ch": {k: f(v, np.int8) for k, v in self.ch.items()}, "latest": list(self.latest), "negm": dict(self.negm)}


def _nanf(v) -> float:
    return math.nan if v is None else float(v)


def record_run(nr: NegRuns, rid: int, si: STK.StockInputs, si_med: STK.StockInputs, ps, h: str, det: dict[str, Any],
               cand: Candidate, tag: str) -> None:
    """一個新 run：num／den、現行與兩情境的真實 `line1_operations`，並做守門 1、2、5。"""
    if len(nr.latest) != rid:
        raise RevNegError(f"{tag}：run id 錯位（{len(nr.latest)} ≠ {rid}）")
    rev = {ym: float(v) for ym, v in (si.monthly_revenue or [])}
    latest = max(rev) if rev else None
    specs = group_specs(ps, h)
    sums: dict[str, tuple[float, float] | None] = {}
    negm: set[tuple[str, float]] = set()
    for g, off, w in specs:
        ns = _sums(rev, latest, off, w) if latest is not None else None
        sums[g] = ns
        if ns is not None:
            ms = [STK._ym_shift(latest, off + i) for i in range(w)]
            neg_here = {(m, rev[m]) for m in ms + [STK._ym_shift(m, STK.MONTHS_PER_YEAR) for m in ms] if rev[m] < 0}
            if (ns[0] < 0 or ns[1] < 0) and not neg_here:          # 守門 5
                raise RevNegError(f"{tag} {g}：num／den 為負（{ns}）但加總月份裡沒有負值月")
            negm |= neg_here
    # 與 §29 的基期紀錄一致（line1_detail 已對 revenue_yoy_3m 逐位核對過）
    for g, _, _ in specs:
        sub = G_SUB[g]
        want = det["subs"][sub]["den"][1 if g == "revenue_accel.prev" else 0]
        got = None if sums[g] is None else sums[g][1]
        if not _same(want, got):
            raise RevNegError(f"{tag} {g}：本檔基期 {got!r} ≠ §29 line1_detail 的基期 {want!r}")
    for g in GNAMES:
        ns = sums.get(g)
        nr.num[g].append(math.nan if ns is None else ns[0])
        nr.den[g].append(math.nan if ns is None else ns[1])
        if ns is None or not ns[1] > 0:
            nr.lit[g].append(-1)
        else:
            nr.lit[g].append(int(literal_value(*ns) != cand_value(*ns)))
    nr.latest.append(latest)
    if negm:
        nr.negm[rid] = tuple(sorted(negm))
    med0, med1 = si.industry_median_3m_yoy, si_med.industry_median_3m_yoy
    nr.med0.append(_nanf(med0))
    nr.med1.append(_nanf(med1))

    lr0 = STK.line1_operations(si, ps, h)
    if not (_same(lr0.score, det["score"]) and bool(lr0.reweighted) == det["rw"] and bool(lr0.unknown) == det["unknown"]):
        raise RevNegError(f"{tag}：現行 line1_operations 重跑 {lr0.score!r} ≠ line1_detail {det['score']!r}")
    s0 = _subs_of(lr0)
    for s in SUBS:
        sr = s0.get(s)
        nr.x0[s].append(math.nan if sr is None or sr.x is None else float(sr.x))
        nr.sc0[s].append(math.nan if sr is None or sr.score is None else float(sr.score))
    den_neg = {s: False for s in SUBS}
    for g, _, _ in specs:
        if sums[g] is not None and sums[g][1] < 0:
            den_neg[G_SUB[g]] = True
    med_changed = h == "mid" and not _same(med0, med1)
    with patched((STK,), cand):
        lr1 = STK.line1_operations(si, ps, h)
        lr2 = STK.line1_operations(si_med, ps, h) if h == "mid" else lr1
    for sc, lr, med in (("sub_only", lr1, med0), ("with_median", lr2, med1)):
        s1 = _subs_of(lr)
        if set(s1) != set(s0):
            raise RevNegError(f"{tag} {sc}：替換後的營收子指標集合 {sorted(s1)} ≠ 現行 {sorted(s0)}")
        any_ch = False
        for s in SUBS:
            a, b = s0.get(s), s1.get(s)
            # 輸入改變＝現行在場，且所用月份組 den<0（或 with_median 下族 C 的產業中位數改變）；現行缺值者替換後仍缺（守門 1 驗）
            ch = a is not None and a.score is not None and (
                den_neg[s] or (sc == "with_median" and s == "revenue_yoy_vs_industry" and med_changed))
            nr.ch[(sc, s)].append(-1 if a is None else int(ch))
            nr.x1[(sc, s)].append(math.nan if b is None or b.x is None else float(b.x))
            nr.sc1[(sc, s)].append(math.nan if b is None or b.score is None else float(b.score))
            if a is None:
                continue
            any_ch |= ch
            if not ch:                                                 # 守門 1
                if not _sub_same(a, b):
                    raise RevNegError(f"{tag} {sc} {s}：輸入未改變（den 皆非負、中位數未變或現行缺值）但替換前後不逐位相同："
                                      f"x {a.x!r}→{b.x!r}、分數 {a.score!r}→{b.score!r}")
                continue
            if (a.score is None) != (b.score is None):                 # 守門 2
                raise RevNegError(f"{tag} {sc} {s}：替換改變了在場／缺值（{a.missing} → {b.missing}）")
            if b.score is None:
                continue
            if s == "revenue_yoy":
                want = cand_value(*sums["revenue_yoy"])
            elif s == "revenue_accel":
                want = cand_value(*sums["revenue_accel.near"]) - cand_value(*sums["revenue_accel.prev"])
            else:
                want = cand_value(*sums["revenue_yoy_vs_industry"]) - med
            if want != b.x:
                raise RevNegError(f"{tag} {sc} {s}：替換後 x {b.x!r} ≠ 由 num／den 以候選式算出的 {want!r}（替身未生效？）")
        nr.aff[sc].append(int(any_ch))
        nr.score[sc].append(_nanf(lr.score))
        nr.rw[sc].append(int(lr.reweighted))
        nr.unk[sc].append(int(lr.unknown))
        if not any_ch and not (_same(lr.score, lr0.score) and lr.reweighted == lr0.reweighted and lr.unknown == lr0.unknown):
            raise RevNegError(f"{tag} {sc}：沒有任何子指標輸入改變，但初爻 {lr0.score!r} → {lr.score!r}")


class MedStats:
    """逐日逐產業：現行 vs 候選產業中位數。"""

    def __init__(self) -> None:
        self.days = 0
        self.pairs = 0
        self.changed = 0
        self.days_changed = 0
        self.deltas: list[float] = []

    def add(self, T: str, cur: dict, cand: dict) -> None:
        if set(cur) != set(cand):
            raise RevNegError(f"{T}：候選產業中位數的產業集合 ≠ 現行（{sorted(set(cur) ^ set(cand))[:3]}）")
        self.days += 1
        any_ch = False
        for ind, (m0, n0) in cur.items():
            m1, n1 = cand[ind]
            if n0 != n1:                                               # 守門 4
                raise RevNegError(f"{T} {ind}：候選式改變了產業樣本數 {n0} → {n1}")
            self.pairs += 1
            if not _same(m0, m1):
                self.changed += 1
                any_ch = True
                self.deltas.append(float(m1) - float(m0))
        self.days_changed += int(any_ch)

    def summary(self) -> dict[str, Any]:
        d = np.array(self.deltas, dtype="d")
        return {"days": self.days, "days_with_change": self.days_changed, "industry_days": self.pairs,
                "industry_days_changed": self.changed,
                "delta": RB._q(d, np.ones(d.size, dtype=np.int64), DELTA_QS),
                "abs_delta": RB._q(np.abs(d), np.ones(d.size, dtype=np.int64), (0.5, 0.9, 1.0))}


def run_a(src, bridge, rows: dict[str, Any], ps: dict, cap, sw, *, quiet: bool):
    """逐日走 db 列（run-length 同 §29，鍵另加候選中位數）。回 (RB.Runs, NegRuns, run_of_row, MedStats, 候選替身兩支)。"""
    if FUND.revenue_yoy_3m is not STK.revenue_yoy_3m:                  # 守門 3（前半）
        raise RevNegError("fundamentals.revenue_yoy_3m 與 score.stock.revenue_yoy_3m 不是同一支函式")
    trading = set(src.trading_dates())
    bad_days = [d for d in rows["dates"] if d not in trading]
    if bad_days:
        raise RevNegError(f"db 日期不在原料交易日軸：{bad_days[:3]}")
    bridge_c = FundamentalsBridge(bridge.stocks, bridge.industry_of)
    cand_stk, cand_fund = Candidate(), Candidate()
    runs, nr, med = RB.Runs(), NegRuns(), MedStats()
    n = rows["n"]
    run_of_row = np.empty(n, dtype=np.int32)
    sid_a, date_a, mk_a, h_a = rows["sid"], rows["date"], rows["mk"], rows["h"]
    bounds = np.flatnonzero(np.diff(date_a)) + 1
    starts = np.concatenate(([0], bounds))
    ends = np.concatenate((bounds, [n]))
    prev: dict[tuple[int, int], tuple[Any, int]] = {}
    t0 = time.time()
    for n_day, (a, b) in enumerate(zip(starts.tolist(), ends.tolist()), start=1):
        T = rows["dates"][int(date_a[a])]
        bridge._industry_stats(T)
        with patched((FUND,), cand_fund):
            bridge_c._industry_stats(T)
        med.add(T, bridge._median, bridge_c._median)
        day: dict[int, tuple[Any, dict, dict, str | None]] = {}
        for i, si_ix, mk_c, h_c in zip(range(a, b), sid_a[a:b].tolist(), mk_a[a:b].tolist(), h_a[a:b].tolist()):
            ent = day.get(si_ix)
            mk = MARKETS[mk_c]
            if ent is None:
                sid = rows["sid_of"][si_ix]
                listed = src.pool.listed(sid, T)
                extra = bridge.inputs_for(sid, T)
                with patched((FUND,), cand_fund):
                    extra_c = bridge_c.inputs_for(sid, T)
                for k in ("monthly_revenue", "fundamentals", "industry_revenue_n"):
                    if extra_c.get(k) != extra.get(k):
                        raise RevNegError(f"{sid} {T}：候選橋的 {k} ≠ 現行（候選式只應改中位數）")
                ind = src.pool.industry_of(sid)
                mon, fund = extra.get("monthly_revenue"), extra.get("fundamentals")
                key = (listed, is_financial(ind), tuple(mon) if mon else None,
                       tuple(sorted(fund.items())) if fund else None,
                       extra.get("industry_median_3m_yoy"), extra.get("industry_revenue_n"),
                       extra_c.get("industry_median_3m_yoy"))
                ent = day[si_ix] = (key, extra, extra_c, ind)
            key, extra, extra_c, ind = ent
            if key[0] != mk:
                raise RevNegError(f"{rows['sid_of'][si_ix]} {T}：pool.listed={key[0]} ≠ db 列市場 {mk}")
            p = prev.get((si_ix, h_c))
            if p is not None and p[0] == key:
                run_of_row[i] = p[1]
                continue
            h = HORIZONS[h_c]
            sid = rows["sid_of"][si_ix]
            si = RB.line1_inputs(mk, sid, T, ind, extra)
            si_med = RB.line1_inputs(mk, sid, T, ind, extra_c)
            det = RB.line1_detail(si, ps[mk], h, cap)
            rid = runs.add(mk_c, h_c, si_ix, det, {})
            record_run(nr, rid, si, si_med, ps[mk], h, det, cand_stk, f"{mk} {h} {sid} {T}")
            prev[(si_ix, h_c)] = (key, rid)
            run_of_row[i] = rid
        if not quiet and n_day % RB.PROGRESS_EVERY == 0:
            print(f"  A {n_day} 日（{T}） run {len(runs):,} {time.time() - t0:.0f}s RSS {RB.rss_mib():.0f} MiB", flush=True)
    if cand_stk.calls == 0 or (bridge.stocks and cand_fund.calls == 0):     # 守門 3（後半）
        raise RevNegError(f"候選替身沒有被呼叫（子指標 {cand_stk.calls} 次、產業中位數 {cand_fund.calls} 次）")
    return runs, nr, run_of_row, med, (cand_stk, cand_fund)


# ---------------------------------------------------------------------------
# 原始月營收的負值
# ---------------------------------------------------------------------------
def _ym_in(ym: str, start: str, end: str) -> bool:
    return start[:7] <= ym <= end[:7]


def raw_negatives(src, bridge, start: str, end: str) -> dict[str, Any]:
    fdb = src.fundamentals_db
    if fdb is None:
        raise RevNegError("原料目錄沒有 fundamentals.db：無法數原始月營收")
    cols = {r[1] for r in fdb.execute('PRAGMA table_info("raw_month_revenue")')}
    if not {"stock_id", "revenue_year", "revenue_month", "revenue"} <= cols:
        raise RevNegError("fundamentals.db 沒有 raw_month_revenue 或欄位不齊")
    acc: dict[str, dict[str, Any]] = {k: {"rows": 0, "months": set(), "stocks": set(), "min": None, "max": None}
                                      for k in ("all", "all_sample", "pool", "pool_sample")}
    total = 0
    for sid, y, m, v in fdb.execute("SELECT stock_id, revenue_year, revenue_month, revenue FROM raw_month_revenue "
                                    "WHERE data_version=?", (src.dv,)):
        if v is None:
            continue
        total += 1
        x = RIO.F_num(v)
        if not x < 0:
            continue
        sid = str(sid)
        ym = f"{int(y):04d}-{int(m):02d}"
        for k in ("all", "all_sample", "pool", "pool_sample"):
            if k.startswith("pool") and sid not in src.pool:
                continue
            if k.endswith("sample") and not _ym_in(ym, start, end):
                continue
            e = acc[k]
            e["rows"] += 1
            e["months"].add((sid, ym))
            e["stocks"].add(sid)
            e["min"] = x if e["min"] is None else min(e["min"], x)
            e["max"] = x if e["max"] is None else max(e["max"], x)
    out: dict[str, Any] = {"raw_rows_total": total}
    for k, e in acc.items():
        out[f"raw_{k}"] = {"rows": e["rows"], "stock_months": len(e["months"]), "stocks": len(e["stocks"]),
                           "min": e["min"], "max": e["max"]}
    br: dict[str, dict[str, Any]] = {k: {"months": 0, "stocks": set()} for k in ("all", "sample")}
    for sid, sf in bridge.stocks.items():
        for ym, v in sf.monthly:
            if v < 0:
                for k in ("all", "sample"):
                    if k == "sample" and not _ym_in(ym, start, end):
                        continue
                    br[k]["months"] += 1
                    br[k]["stocks"].add(sid)
    for k, e in br.items():
        out[f"bridge_{k}"] = {"stock_months": e["months"], "stocks": len(e["stocks"])}
    return out


# ---------------------------------------------------------------------------
# 彙總
# ---------------------------------------------------------------------------
def _share(c: int, n: int) -> float | None:
    return c / n if n else None


def summarize_counts(R: dict[str, Any], N: dict[str, Any], w: np.ndarray) -> list[dict[str, Any]]:
    out = []
    for m, h, gm in RB._groups(R):
        n_rows = int(w[gm].sum())
        if not n_rows:
            continue
        for g in GNAMES:
            if g == "revenue_yoy_vs_industry" and h != "mid":
                continue
            num, den = N["num"][g], N["den"][g]
            have = gm & ~np.isnan(den)
            s = lambda msk: int(w[msk].sum())  # noqa: E731
            pres = R["pres"][G_SUB[g]] == 1
            dn, nn = have & (den < 0), have & (num < 0)
            lit = gm & (N["lit"][g] >= 0)
            c = {"market": m, "horizon": h, "group": g, "sub": G_SUB[g], "group_rows": n_rows, "base_rows": s(have),
                 "den_neg": s(dn), "num_neg": s(nn), "both_neg": s(dn & nn), "den_pos_num_neg": s(have & (den > 0) & nn),
                 "den_zero": s(have & (den == 0)), "den_neg_sub_present": s(dn & pres),
                 "literal_den_pos": {"rows": s(lit), "bitwise_diff_rows": s(lit & (N["lit"][g] == 1))}}
            for k in ("den_neg", "num_neg", "both_neg", "den_pos_num_neg"):
                c[f"{k}_share"] = _share(c[k], n_rows)
            c["den_min"] = float(den[have].min()) if c["base_rows"] else None
            c["num_min"] = float(num[have].min()) if c["base_rows"] else None
            out.append(c)
    return out


def summarize_cf(R: dict[str, Any], N: dict[str, Any], w: np.ndarray) -> list[dict[str, Any]]:
    out = []
    lo, hi = BAND
    for m, h, gm in RB._groups(R):
        n_rows = int(w[gm].sum())
        if not n_rows:
            continue
        for sc in SCEN:
            aff = gm & (N["aff"][sc] == 1)
            s = lambda msk: int(w[msk].sum())  # noqa: E731
            orig, cf = R["score"], N["score"][sc]
            known = aff & ~np.isnan(orig) & ~np.isnan(cf)
            delta = cf[known] - orig[known]
            o_in = (orig >= lo) & (orig <= hi)
            c_in = (cf >= lo) & (cf <= hi)
            dn = known & (orig >= 50.0) & (cf < 50.0)
            up = known & (orig < 50.0) & (cf >= 50.0)
            n_aff = s(aff)
            rec = {"market": m, "horizon": h, "scenario": sc, "group_rows": n_rows, "affected_rows": n_aff,
                   "affected_share": _share(n_aff, n_rows),
                   "score_changed_rows": s(known & (cf != orig)),
                   "delta": RB._q(delta, w[known], DELTA_QS), "abs_delta": RB._q(np.abs(delta), w[known], (0.5, 0.9, 1.0)),
                   "flip_rows": s(dn | up), "flip_yang_to_yin": s(dn), "flip_yin_to_yang": s(up),
                   "flip_share_of_group": _share(s(dn | up), n_rows),
                   "orig_unknown_rows": s(aff & np.isnan(orig)), "became_unknown_rows": s(aff & ~np.isnan(orig) & np.isnan(cf)),
                   "became_known_rows": s(aff & np.isnan(orig) & ~np.isnan(cf)),
                   "band_enter_rows": s(known & ~o_in & c_in), "band_exit_rows": s(known & o_in & ~c_in),
                   "orig_in_band_rows": s(aff & ~np.isnan(orig) & o_in), "subs": {}}
            for sub in SUBS:
                chm = gm & (N["ch"][(sc, sub)] == 1)
                if not (gm & (N["ch"][(sc, sub)] >= 0)).any():
                    continue
                x0, x1 = N["x0"][sub], N["x1"][(sc, sub)]
                s0, s1 = N["sc0"][sub], N["sc1"][(sc, sub)]
                pk = chm & ~np.isnan(x0) & ~np.isnan(x1)
                sd = s1[pk] - s0[pk]
                rec["subs"][sub] = {
                    "input_changed_rows": s(chm), "present_rows": s(pk), "x_changed_rows": s(pk & (x1 != x0)),
                    "x_sign_flip_rows": s(pk & (np.sign(x0) * np.sign(x1) < 0)),
                    "x_orig": RB._q(x0[pk], w[pk], X_QS), "x_cand": RB._q(x1[pk], w[pk], X_QS),
                    "sub_score_changed_rows": s(pk & (s1 != s0)),
                    "sub_score_delta": RB._q(sd, w[pk], DELTA_QS),
                    "sub_score_abs_delta_max": float(np.abs(sd).max()) if sd.size else None}
            out.append(rec)
    return out


def summarize_stocks(src, bridge, rows: dict[str, Any], R: dict[str, Any], N: dict[str, Any], w: np.ndarray,
                     start: str, end: str) -> dict[str, Any]:
    den_neg = np.zeros(R["score"].size, dtype=bool)
    num_neg = np.zeros(R["score"].size, dtype=bool)
    for g in GNAMES:
        d, n = N["den"][g], N["num"][g]
        den_neg |= ~np.isnan(d) & (d < 0)
        num_neg |= ~np.isnan(n) & (n < 0)
    flag = den_neg | num_neg
    per: dict[int, dict[str, Any]] = {}
    for rid in np.flatnonzero(flag).tolist():
        sid_ix = int(R["sid"][rid])
        e = per.setdefault(sid_ix, {"rows": 0, "den_neg_rows": 0, "num_neg_rows": 0, "markets": set(),
                                    "latest": set(), "negm": set()})
        wt = int(w[rid])
        e["rows"] += wt
        e["den_neg_rows"] += wt * int(den_neg[rid])
        e["num_neg_rows"] += wt * int(num_neg[rid])
        if wt:
            e["markets"].add(MARKETS[int(R["mk"][rid])])
            e["latest"].add(N["latest"][rid])
            e["negm"] |= set(N["negm"].get(rid, ()))
    lst = []
    for sid_ix, e in per.items():
        if not e["rows"]:
            continue
        sid = rows["sid_of"][sid_ix]
        ind = src.pool.industry_of(sid)
        meta = src.pool.get(sid) or {}
        negm = sorted(e["negm"])
        sf = bridge.stocks.get(sid)
        hist = [(ym, v) for ym, v in (sf.monthly if sf is not None else []) if v < 0]
        lst.append({"stock_id": sid, "name": meta.get("stock_name"), "industry": ind, "is_financial": is_financial(ind),
                    "markets": sorted(e["markets"]), "rows": e["rows"], "den_neg_rows": e["den_neg_rows"],
                    "num_neg_rows": e["num_neg_rows"],
                    "asof_latest_month_min": min(e["latest"]), "asof_latest_month_max": max(e["latest"]),
                    "neg_months_referenced": len(negm), "neg_months_shown": [ym for ym, _ in negm[:NEG_MONTHS_SHOWN]],
                    "neg_month_first": negm[0][0] if negm else None, "neg_month_last": negm[-1][0] if negm else None,
                    "neg_value_min": min(v for _, v in negm) if negm else None,
                    "neg_value_max": max(v for _, v in negm) if negm else None,
                    "neg_months_in_history": len(hist),
                    "neg_months_in_history_sample": sum(1 for ym, _ in hist if _ym_in(ym, start, end))})
    lst.sort(key=lambda r: (-r["rows"], r["stock_id"]))
    fin = {k: {"stocks": 0, "rows": 0} for k in ("financial", "non_financial")}
    ind_c: Counter = Counter()
    ind_r: Counter = Counter()
    for r in lst:
        k = "financial" if r["is_financial"] else "non_financial"
        fin[k]["stocks"] += 1
        fin[k]["rows"] += r["rows"]
        ind_c[r["industry"] or "（無）"] += 1
        ind_r[r["industry"] or "（無）"] += r["rows"]
    top = sorted(ind_c, key=lambda k: (-ind_c[k], -ind_r[k], k))[:INDUSTRY_TOP]
    ref_all = set()
    for rid, ms in N["negm"].items():
        if w[rid]:
            ref_all |= {(rows["sid_of"][int(R["sid"][rid])], ym) for ym, _ in ms}
    return {"total_stocks": len(lst), "listed": lst[:STOCK_LIST_MAX], "list_truncated": len(lst) > STOCK_LIST_MAX,
            "rows_total": int(w[flag].sum()), "den_neg_rows_total": int(w[den_neg].sum()),
            "num_neg_rows_total": int(w[num_neg].sum()), "financial_split": fin,
            "industry_top": [{"industry": k, "stocks": ind_c[k], "rows": ind_r[k]} for k in top],
            "referenced_neg_stock_months": len(ref_all), "referenced_neg_stocks": len({s for s, _ in ref_all})}


# ---------------------------------------------------------------------------
# B：只抽涉及負值的列（沿用 RB.run_b）
# ---------------------------------------------------------------------------
def draw_b(rows: dict[str, Any], neg_run: np.ndarray, run_of_row: np.ndarray, per_group: int,
           rng: np.random.Generator) -> tuple[list[dict], list[dict]]:
    if per_group < 1:
        raise RevNegError(f"--per-group 至少 1（得 {per_group}）")
    neg_row = neg_run[run_of_row]
    samples, strata = [], []
    for m in MARKETS:
        for h in HORIZONS:
            gm = (rows["mk"] == RB.MK_CODE[m]) & (rows["h"] == RB.H_CODE[h])
            idx = np.flatnonzero(gm & neg_row)
            pick = idx if idx.size <= per_group else np.sort(rng.choice(idx, size=per_group, replace=False))
            strata.append({"market": m, "horizon": h, "population": int(idx.size), "sampled": int(pick.size)})
            for i in pick.tolist():
                samples.append({"market": m, "horizon": h, "stratum": "neg", "row": i, "run": int(run_of_row[i]),
                                "sid": rows["sid_of"][rows["sid"][i]], "date": rows["dates"][rows["date"][i]],
                                "db": float(rows["l1"][i]), "rw": int(rows["rw"][i]), "unk": int(rows["unk"][i])})
    return samples, strata


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------
def run(db: Path, out: Path, *, cache_dir: Path, features: Path | None = None, registry: Path = SS.REGISTRY,
        per_group: int = DEFAULT_PER_GROUP, seed: int = DEFAULT_SEED, start: str | None = None, end: str | None = None,
        quiet: bool = False, keep_details: bool = False, src_root: Path | None = None) -> dict[str, Any]:
    """`start`／`end` 只供測試（合成資料在 2020 年）；CLI 一律用裁定 #64 ① 的寫死值。"""
    t0 = time.time()
    start = SAMPLE_START if start is None else start
    end = SAMPLE_END if end is None else end
    if per_group < 1:
        raise RevNegError(f"--per-group 至少 1（得 {per_group}；開跑前檢查）")
    uses = RB.usage_scan(src_root) if src_root is not None else RB.usage_scan()
    reg = SS.load_registry(registry)
    with ScoreStore(db, readonly=True) as store:
        import export_scores as EX
        dv = EX.resolve_data_version(store, db.parent, None)
        sha, params = RB.check_db(store, dv, reg)
        rows = RB.read_rows(store, dv, start, end)
    window = int(params["window"])
    ps = {m: build_params(m) for m in MARKETS}
    src = RIO.ReplaySource(Path(cache_dir), dv, features_path=features, window=window)
    try:
        bridge = src.load_fundamentals(src.trading_dates())
        raw = raw_negatives(src, bridge, start, end)
        with RB.instrumented() as (cap, sw):
            runs, nr, run_of_row, med, (c_stk, c_fund) = run_a(src, bridge, rows, ps, cap, sw, quiet=quiet)
        R = runs.np()
        N = nr.np()
        del runs, nr
        par_a = RB.parity_a(rows, R, run_of_row)
        w_run = np.bincount(run_of_row, minlength=R["score"].size).astype(np.int64)
        neg_run = np.zeros(R["score"].size, dtype=bool)
        for g in GNAMES:
            neg_run |= (~np.isnan(N["den"][g]) & (N["den"][g] < 0)) | (~np.isnan(N["num"][g]) & (N["num"][g] < 0))
        rng = np.random.default_rng(seed)
        samples, strata = draw_b(rows, neg_run, run_of_row, per_group, rng)
        par_b = RB.run_b(src, bridge, samples, R, ps, window, quiet=quiet)
        par_b["executed"] = bool(samples)
        if not samples:
            par_b["note"] = B_EMPTY
        for st in strata:
            if not st["population"]:
                st["note"] = B_EMPTY
        stocks = summarize_stocks(src, bridge, rows, R, N, w_run, start, end)
    finally:
        src.close()
    res = {"schema": 1, "db": str(db), "data_version": dv, "params_sha": sha,
           "model_versions": {m: ps[m].model_version() for m in MARKETS},
           "sample": {"start": start, "end": end}, "per_group": per_group, "seed": seed,
           "population": {"rows": int(rows["n"]), "days": len(rows["dates"]), "stocks": len(rows["sid_of"]),
                          "runs": int(R["score"].size)},
           "definitions": {
               "num_den": "num＝當期月份合計、den＝去年同期合計（元，raw_month_revenue.revenue 原單位），月份清單同 revenue_yoy_3m；"
                          "revenue_accel 分近組（offset 0）與前組（offset＝window）",
               "current": "現行 revenue_yoy_3m＝(num/den − 1)×100，den＝0 缺值",
               "candidate": "候選式（未採用）：den<0 → (num − den)／|den| × 100；den>0 → 現行式子原樣；den＝0 → 現行缺值",
               "literal_den_pos": "若 den>0 也用字面式子 (num − den)／|den| × 100，與現行式子不逐位相同的列數（只量不套用）",
               "scenarios": "sub_only＝只替換子指標的 revenue_yoy_3m；with_median＝另以真實 _industry_stats 重算候選產業中位數，中期族 C 改用它",
               "flip": "以 50 為界（≥50 陽），只計兩側皆已知的列；進／出帶 [45, 55] 兩端含，只計兩側皆已知的列",
               "sample_months": f"「樣本段內」的營收月＝{start[:7]}～{end[:7]}（營收所屬年月，不是公布日）",
               "affected": "affected＝該列任一**在場**營收子指標的輸入被替換改變（所用月份組 den<0，或 with_median 下產業中位數改變）；"
                           "現行缺值的子指標替換後仍缺值（守門）"},
           "raw_negative": raw, "industry_median": med.summary(),
           "candidate_calls": {"sub": c_stk.calls, "sub_rewritten": c_stk.rewritten,
                               "median": c_fund.calls, "median_rewritten": c_fund.rewritten},
           "counts": summarize_counts(R, N, w_run), "counterfactual": summarize_cf(R, N, w_run), "stocks": stocks,
           "parity_a": par_a, "parity_b": par_b, "b_strata": strata, "usage": uses,
           "elapsed_s": round(time.time() - t0, 1), "rss_peak_mib": round(RB.rss_mib(), 1)}
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(res, ensure_ascii=False, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    out.with_suffix(".txt").write_text(as_text(res), encoding="utf-8")
    if keep_details:
        res["_details"] = {"R": R, "N": N, "rows": rows, "run_of_row": run_of_row, "w_run": w_run, "samples": samples}
    return res


# ---------------------------------------------------------------------------
# 文字報告
# ---------------------------------------------------------------------------
_f, _pct = RB._f, RB._pct


def as_text(res: dict[str, Any]) -> str:
    pa, pb, pop, raw = res["parity_a"], res["parity_b"], res["population"], res["raw_negative"]
    st = res["stocks"]
    L = [(f"營收子指標負值基期量測（唯讀，§30）｜樣本 {res['sample']['start']}～{res['sample']['end']}｜"
          f"B 每組至多 {res['per_group']} 列、seed {res['seed']}"),
         f"db {res['db']}｜data_version {res['data_version']}｜params_sha {res['params_sha']}｜model_version {res['model_versions']}",
         f"母體：個股列 {pop['rows']:,}｜{pop['days']} 日｜{pop['stocks']} 檔｜run {pop['runs']:,}",
         f"A 全母體 parity：{pa['rows']:,} 列初爻分數／reweighted／unknown 與 db 全數相符（max |差| {pa['max_abs_diff']:.3e} ≤ {pa['tol']}）",
         (f"B 抽樣 parity（只抽涉及負值的列）：{pb['rows']:,} 列以真實重播路徑重算，初爻與 db、營收子指標與 A 全數相符"
          + (f"（{B_EMPTY}的組：" + "、".join(f"{s['market']} {s['horizon']}" for s in res["b_strata"] if s.get("note")) + "）"
             if any(s.get("note") for s in res["b_strata"]) else "")
          if pb.get("executed") else f"B 抽樣 parity：{B_EMPTY}（0 列；各組皆無 den<0 或 num<0 的列）"),
         f"耗時 {res['elapsed_s']}s｜RSS 峰值 {res['rss_peak_mib']} MiB",
         *(f"{k}：{v}" for k, v in res["definitions"].items()),
         "本報告只陳述量測數字（不含成因與規則）。", "",
         "== 原始月營收的負值（raw_month_revenue，同 data_version）",
         f"全表非空列 {raw['raw_rows_total']:,}"]
    for k, lab in (("raw_all", "全表・全期間"), ("raw_all_sample", "全表・樣本段內營收月"), ("raw_pool", "池內・全期間"),
                   ("raw_pool_sample", "池內・樣本段內營收月")):
        e = raw[k]
        L.append(f"  {lab}：負值列 {e['rows']:,}／(檔,月) {e['stock_months']:,}／檔 {e['stocks']:,}｜值 {_f(e['min'])}～{_f(e['max'])}")
    for k, lab in (("bridge_all", "重播所見（池內、同月取最後）・全期間"), ("bridge_sample", "重播所見・樣本段內營收月")):
        e = raw[k]
        L.append(f"  {lab}：負值 (檔,月) {e['stock_months']:,}／檔 {e['stocks']:,}")
    L.append(f"  樣本段列的加總實際引用到的負值月：(檔,月) {st['referenced_neg_stock_months']:,}／檔 {st['referenced_neg_stocks']:,}")
    L += ["", "== 列數（run-length 展開；比例分母＝該組 db 列數）",
          "market horizon 月份組 | 組列 | 可算基期 | den<0 | num<0 | 兩者<0 | den>0且num<0 | den=0 | den<0且子指標在場 | den 最小 | 字面式 den>0 不逐位"]
    for c in res["counts"]:
        L.append(f"{c['market']} {c['horizon']} {c['group']} | {c['group_rows']:,} | {c['base_rows']:,} | "
                 f"{c['den_neg']:,}({_pct(c['den_neg_share'])}) | {c['num_neg']:,}({_pct(c['num_neg_share'])}) | "
                 f"{c['both_neg']:,} | {c['den_pos_num_neg']:,} | {c['den_zero']:,} | {c['den_neg_sub_present']:,} | "
                 f"{_f(c['den_min'])} | {c['literal_den_pos']['bitwise_diff_rows']:,}／{c['literal_den_pos']['rows']:,}")
    fs = st["financial_split"]
    L += ["", f"== 股票（出現 den<0 或 num<0 的檔）：共 {st['total_stocks']} 檔、{st['rows_total']:,} 列"
              f"（den<0 {st['den_neg_rows_total']:,}／num<0 {st['num_neg_rows_total']:,}）"
              + ("；以下只列前 200 檔" if st["list_truncated"] else ""),
          (f"金融 {fs['financial']['stocks']} 檔／{fs['financial']['rows']:,} 列；非金融 {fs['non_financial']['stocks']} 檔／"
           f"{fs['non_financial']['rows']:,} 列"),
          "產業前 10（檔數）：" + "；".join(f"{x['industry']} {x['stocks']} 檔／{x['rows']:,} 列" for x in st["industry_top"]),
          "代號 名稱 產業 金融 市場 | 列 | den<0 列 | num<0 列 | as-of 最新月 | 引用負值月數 首～末 | 負值 最小～最大 | 前幾個負值月 | 歷史負值月(樣本段)"]
    for r in st["listed"]:
        L.append(f"{r['stock_id']} {r['name'] or '—'} {r['industry'] or '—'} {'Y' if r['is_financial'] else 'N'} "
                 f"{'/'.join(r['markets'])} | {r['rows']:,} | {r['den_neg_rows']:,} | {r['num_neg_rows']:,} | "
                 f"{r['asof_latest_month_min']}～{r['asof_latest_month_max']} | {r['neg_months_referenced']} "
                 f"{r['neg_month_first']}～{r['neg_month_last']} | {_f(r['neg_value_min'])}～{_f(r['neg_value_max'])} | "
                 f"{','.join(r['neg_months_shown'])} | {r['neg_months_in_history']}({r['neg_months_in_history_sample']})")
    md = res["industry_median"]
    L += ["", "== 產業中位數（現行 vs 候選；逐日逐產業，真實 _industry_stats）",
          f"{md['days']} 日、產業日 {md['industry_days']:,}；中位數改變的產業日 {md['industry_days_changed']:,}"
          f"（{md['days_with_change']} 日）｜Δ " + "／".join(f"{k} {_f(v)}" for k, v in md["delta"].items()),
          "", "== 反事實（候選式替換後重跑真實 line1_operations；普查）",
          "market horizon 情境 | 受影響列 | 佔組 | 分數改變 | Δ min/p50/max | 翻轉(陽→陰/陰→陽) 佔組 | 變未知/變已知 | 進帶 | 出帶"]
    for c in res["counterfactual"]:
        d = c["delta"]
        L.append(f"{c['market']} {c['horizon']} {c['scenario']} | {c['affected_rows']:,} | {_pct(c['affected_share'])} | "
                 f"{c['score_changed_rows']:,} | {_f(d['min'])}/{_f(d['p50'])}/{_f(d['max'])} | "
                 f"{c['flip_rows']:,}({c['flip_yang_to_yin']:,}/{c['flip_yin_to_yang']:,}) {_pct(c['flip_share_of_group'])} | "
                 f"{c['became_unknown_rows']:,}/{c['became_known_rows']:,} | {c['band_enter_rows']:,} | {c['band_exit_rows']:,}")
        for sub, s in c["subs"].items():
            if not s["input_changed_rows"]:
                continue
            L.append(f"   {sub}：輸入改變 {s['input_changed_rows']:,}、在場 {s['present_rows']:,}、x 符號翻轉 {s['x_sign_flip_rows']:,}｜"
                     f"x 現行 {_f(s['x_orig']['min'])}～{_f(s['x_orig']['max'])} → 候選 {_f(s['x_cand']['min'])}～{_f(s['x_cand']['max'])}｜"
                     f"子指標分數改變 {s['sub_score_changed_rows']:,}、Δ min/p50/max "
                     f"{_f(s['sub_score_delta']['min'])}/{_f(s['sub_score_delta']['p50'])}/{_f(s['sub_score_delta']['max'])}")
    cc = res["candidate_calls"]
    L += ["", f"候選替身呼叫：子指標 {cc['sub']:,} 次（改寫 {cc['sub_rewritten']:,}）；產業中位數 {cc['median']:,} 次（改寫 {cc['median_rewritten']:,}）",
          "", "== B 抽樣（涉及負值的列；母體/抽）"]
    L.append("；".join(f"{s['market']} {s['horizon']} " + (s["note"] if s.get("note") else f"{s['population']:,}/{s['sampled']}")
                      for s in res["b_strata"]))
    L += ["", "== D 使用處（src/iching，ast 掃描；同 §29）"]
    for u in res["usage"]:
        L.append(f"{u['file']} {u['function']}（行 {','.join(map(str, u['lines']))}）：{'、'.join(u['what'])}")
    return "\n".join(L) + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="營收子指標負值基期量測（唯讀；樣本段寫死為訓練＋驗證段；任何守門不過即中止）")
    ap.add_argument("--db", default=str(REPO / "cache" / "scores.db"))
    ap.add_argument("--cache-dir", default=str(REPO / "cache"), help="原料 DB 目錄（同 replay_scores.py）")
    ap.add_argument("--features", default=None, help="預設 <cache-dir>/features.db")
    ap.add_argument("--registry", default=str(SS.REGISTRY))
    ap.add_argument("--out", required=True, help="報告 JSON 路徑（runs/revneg/report_<TO>.json）；同名 .txt 一併寫出")
    ap.add_argument("--per-group", type=int, default=DEFAULT_PER_GROUP, help=f"B 每組至多抽幾列（預設 {DEFAULT_PER_GROUP}）")
    ap.add_argument("--seed", type=int, default=DEFAULT_SEED, help=f"B 抽樣種子（預設 {DEFAULT_SEED}）")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args(argv)
    try:
        res = run(Path(args.db), Path(args.out), cache_dir=Path(args.cache_dir),
                  features=Path(args.features) if args.features else None, registry=Path(args.registry),
                  per_group=args.per_group, seed=args.seed, quiet=args.quiet)
        print(f"== 營收負值基期：母體 {res['population']['rows']:,} 列｜涉及負值 {res['stocks']['total_stocks']} 檔、"
              f"{res['stocks']['rows_total']:,} 列｜A parity {res['parity_a']['rows']:,} 列、B parity {res['parity_b']['rows']:,} 列相符｜"
              f"{res['elapsed_s']}s RSS {res['rss_peak_mib']} MiB｜報告 {args.out}")
        return 0
    except Exception as e:  # noqa: BLE001  任何例外一律 rc=2
        print(f"[revenue_negative_base 中止] {type(e).__name__}: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
