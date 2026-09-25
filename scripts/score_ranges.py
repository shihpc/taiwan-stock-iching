#!/usr/bin/env python3
"""§16.5 `:714` 可達邊界登錄（步驟 1～3）：由生產參數推算 72 組爻 × (甲)(乙) 共 144 個理論區間，
寫入 `docs/score-ranges.md` 與 `data/score_ranges.json`。**規格不列數字、以本程式產出為準**
（`spec/stock-iching-plan-v1.2.2.md:714`）。

## 判準出處

- 演算法：`spec/stock-iching-plan-v1.2.2.md:714` 步驟 1～3；登錄鍵 `spec/dimensions.json` 的 `line_reachable_range`
  （`market × horizon × line × scope × coverage`，144 筆）。
- 裁定 #64（2026-09-24，`docs/P3-CALIBRATION.md` §24）：
  （編號沿用 §24 裁定表；①～③ 是樣本段、保留段、逐爻 coverage，屬下一批的實測）
  ④x 的數學支撐放在本腳本（`SUPPORT`），**不進 ParamSet**，model_version／params_sha 不變；
  ⑤個股上爻族 A（沿用大盤方向分數）的上游取**大盤各爻所有可行狀態的聯集**；
  ⑥(乙)＝**只含該爻真的有重配的狀態**（`line_score(...).reweighted`，含族內子指標缺）；
  ⑦任何族都可能以 `insufficient_history` 缺（保守外界，不靠推論原因碼）。

## 做法

- **步驟 1**：逐子指標求可達輸出 [lo, hi]。**一律呼叫程式本身的轉換函式**（`S_clip`／`L`／`normalize`／
  `Rules` 情境值），不重抄公式：S 型把 x 的支撐端點送進 `S_clip`（`clip_3d` 自然與 [c−3d, c+3d] 取交集）；
  L 型把支撐端點送進 `L` 再 `normalize`；情境表取 `Rules` 值集合；P_hist 依 `Rules.phist_tie`／`phist_include_today`
  推 {0.5/n, …, (n−0.5)/n}；`market_direction` 取同市場同期間的大盤方向分數區間。
  離散集合（`foreign_buy_days` 等）只記極值——爻分是加權平均，極值只由各子指標極值決定。
- **步驟 2**：逐族枚舉狀態（全缺〔兩種原因〕／滿／各個非空真子集），逐爻枚舉族狀態組合，
  **可行性與是否重配直接呼叫 `aggregate.line_score` 判定**（連 `exclude_insufficient` 與浮點邊界都與生產一致）。
  區間＝Σ w·族區間 ÷ Σ w（只計有分的族）；族區間＝Σ sw·子區間 ÷ Σ sw。
- **特例**：個股初爻中期族 A 下限 84.16、個股三爻過熱封頂 79.89 只可能把分數拉**向區間內**——本程式逐狀態斷言
  「下限 ≤ 族 A 上界」「封頂 ≥ 爻下界」，不成立即中止（屆時區間需另算，不得沿用）。

## 誠實邊界

各子指標、各爻之間**不獨立**（例如大盤 L1 與 L3C 同讀指數收盤），依權重取極值得到的是**外界**，
不保證端點同時到得了。實測極值落在外界內是必要條件、不是充分條件（`:714` 步驟 4 只要求前者）。

rc：0 成功（或 `--check` 無差異）／1 `--check` 有差異／2 中止。
"""
from __future__ import annotations

import argparse
import itertools
import json
import math
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "src"))

from iching.score.aggregate import FamilyResult, line_score  # noqa: E402
from iching.score.params import SCOPE_MARKET, SCOPE_STOCK, build_params  # noqa: E402
from iching.score.transform import (  # noqa: E402
    REASON_INSUFFICIENT, REASON_MISSING, S_RANGE, L, Missing, N, S_clip, normalize, scenario_value_after_N)

MARKETS = ("twse", "tpex")
HORIZONS = ("short", "swing", "mid")
LINES = ("1", "2", "3", "4", "5", "6")
SCOPES = (SCOPE_MARKET, SCOPE_STOCK)
OUT_MD = REPO / "docs" / "score-ranges.md"
OUT_JSON = REPO / "data" / "score_ranges.json"
INF = math.inf


class RangeError(Exception):
    pass


# ---------------------------------------------------------------------------
# 步驟 1：x 的數學支撐（裁定 #64 ④：放本腳本、不進 ParamSet）
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class Support:
    kind: str                 # "S"｜"L"｜"P_hist"｜"P_hist_rev"｜"scenario"｜"volume_scenario"｜"margin_scenario"｜"direction"
    lo: float = -INF
    hi: float = INF
    basis: str = ""           # 推定依據（算式出處）
    half_window: bool = False  # 支撐為 [−window/2, window/2]（買超天數類）


R = (-INF, INF)
_S_REAL = "差值／比值／斜率，算式無界"
SUPPORT: dict[str, Support] = {
    # 大盤
    "dist_ma_short": Support("S", *R, "(C−MA)/ATR14_{t−1}：當日 C 不受 ATR_{t−1} 約束（market.py ind_index_ma_distance；個股 stock.py ind_ma_distance）"),
    "dist_ma_long": Support("S", *R, "同 dist_ma_short"),
    "ma20_slope": Support("S", *R, "MA 的 n 日變化 ÷ ATR（market.py）"),
    "range_position": Support("L", 0.0, 1.0, "(I−min)/(max−min)，視窗含 T，兩端可達（market.py）"),
    "above_ma_short_ratio": Support("L", 0.0, 1.0, "家數比，分子為分母子集"),
    "above_ma_long_ratio": Support("L", 0.0, 1.0, "家數比，分子為分母子集"),
    "advance_ratio": Support("L", 0.0, 1.0, "家數比，分子為分母子集"),
    "new_high_low_ratio": Support("S", -100.0, 100.0, "(新高−新低)/N×100"),
    "ad_line_dev": Support("S", *R, "dev_T/std_n(dev)，未減平均，無界（market.py）"),
    "amount_ratio": Support("S", 0.0, INF, "成交額均值比，非負"),
    "up_amount_ratio": Support("L", 0.0, 1.0, "上漲股成交額 ÷ 總成交額"),
    "divergence_scenario": Support("scenario", basis="Rules.divergence_scores"),
    "foreign_net_ratio": Support("S", -100.0, 100.0, "Σ淨買 ÷ Σ成交 ×100（推定：淨買 ≤ 成交）"),
    "trust_net_ratio": Support("S", -100.0, 100.0, "同 foreign_net_ratio"),
    "foreign_buy_days": Support("S", basis="近 n 日買超天數 − n/2（market.py ind_buy_days）", half_window=True),
    "margin_change": Support("S", -100.0, INF, "(M_T/M_{T−n}−1)×100，餘額非負"),
    "foreign_net_oi_phist": Support("P_hist", basis="P_hist(n=window)"),
    "foreign_net_oi_change": Support("S", *R, "OI 差，整數無界"),
    "basis": Support("S", *R, "basis_T − 60 日滾動中位數，無界（c=None，以 x−c 計）"),
    "vix_phist_rev": Support("P_hist_rev", basis="100 − P_hist(n=window)"),
    "spx_return": Support("S", -100.0, INF, "報酬 ×100"),
    "spx_ma_distance": Support("S", *R, "同 dist_ma"),
    "sox_return": Support("S", -100.0, INF, "報酬 ×100"),
    "usdtwd_change": Support("S", -100.0, INF, "匯率變化 ×100"),
    # 個股
    "revenue_yoy": Support("S", *R, "營收年增率 %：分母＝去年同期合計，≤ 0 即缺值（裁定 #68）故在場時 > 0；"
                                     "但月營收可為負（§30 實測原始表 237 個負值月），分子 < 0 時 YoY < −100，算式無下界"),
    "revenue_accel": Support("S", *R, _S_REAL),
    "eps_yoy": Support("S", *R, "(E/E0−1)×100，E0>門檻、E 可負"),
    "eps_diff_over_price": Support("S", *R, _S_REAL),
    "gross_margin_qoq": Support("S", *R, _S_REAL),
    "pretax_income_yoy": Support("S", *R, _S_REAL),
    "equity_qoq": Support("S", *R, "（裁定 #36 目前恆缺；保守外界照樣列入）"),
    "revenue_yoy_vs_industry": Support("S", *R, _S_REAL),
    "structure": Support("scenario", basis="Rules.structure_scores"),
    "ma_long_slope": Support("S", *R, "同 ma20_slope"),
    "excess_long": Support("S", *R, "報酬差"),
    "excess_short": Support("S", *R, "報酬差"),
    "excess_vs_industry": Support("S", *R, "報酬差"),
    "excess_accel": Support("S", *R, "報酬差"),
    "volume_scenario": Support("volume_scenario", basis="Rules.vs_*（stock.py volume_scenario_day）"),
    "updown_volume_ratio": Support("S", *R, "ln(均量比)"),
    "obv_slope": Support("S", *R, "OLS 斜率 ÷ VMA20_{t−1}"),
    "close_position": Support("L", 0.0, 1.0, "(C−L)/(H−L) 的 n 日平均；前提 L≤C≤H（資料一致性，程式不檢查）"),
    "continuation": Support("scenario", basis="Rules.continuation_scores"),
    "foreign_strength_long": Support("S", -100.0, 100.0, "Σ淨買股 ÷ Σ成交股 ×100（推定：淨買 ≤ 成交）"),
    "foreign_strength_short": Support("S", -100.0, 100.0, "同上"),
    "trust_strength_long": Support("S", -100.0, 100.0, "同上"),
    "trust_strength_short": Support("S", -100.0, 100.0, "同上"),
    "foreign_persistence": Support("S", basis="近 n 日買超天數 − n/2（stock.py ind_persistence）", half_window=True),
    "margin_scenario": Support("margin_scenario", -100.0, INF, "r＝融資餘額 n 日變化率 %，餘額非負（stock.py ind_margin_scenario）"),
    "short_sale_change": Support("S", -100.0, 100.0, "(B_T−B_{T−n})/流通股 ×100，0 ≤ B ≤ 流通股"),
    "market_direction": Support("direction", basis="同市場同期間大盤方向分數（裁定 #64 ⑤）"),
    "industry_relative_return": Support("S", *R, "報酬差"),
    "industry_above_ma20_ratio": Support("L", 0.0, 1.0, "家數比"),
}

#: 不當子指標列舉的 Param（有列、但不以子指標身分進族分）
NOT_A_SUB = {
    "revenue_high_12m": "只觸發個股初爻中期族 A 的下限（stock.py），不進族分",
    "put_call_ratio": "scored=False、族「—」不在 family_weights",
}

#: 族內子指標「實際可能同時出現」的組合（預設＝該族全部子指標為一組）。
#: 個股初爻中期族 B：非金融股 eps_yoy／eps_diff_over_price 二選一＋gross_margin_qoq；金融股 pretax_income_yoy＋equity_qoq（stock.py）。
VARIANTS: dict[tuple[str, str, str], list[tuple[str, ...]]] = {
    (SCOPE_STOCK, "1", "B"): [("eps_yoy", "gross_margin_qoq"), ("eps_diff_over_price", "gross_margin_qoq"),
                              ("pretax_income_yoy", "equity_qoq")],
}


def sub_range(p, sup: Support, rules, direction_range: tuple[float, float] | None) -> tuple[float, float]:
    """步驟 1：子指標套 N 之後的可達 [lo, hi]。"""
    k = sup.kind
    if k == "S":
        c = 0.0 if p.c is None else float(p.c)       # c=None（basis 的滾動中位數）：支撐以 x−c 計
        lo, hi = sup.lo, sup.hi
        if sup.half_window:
            if not isinstance(p.window, int):
                raise RangeError(f"{p.indicator_id}: half_window 需要 int window，得 {p.window!r}")
            lo, hi = -p.window / 2.0, p.window / 2.0
        a = normalize(S_clip(c + lo if p.c is None else lo, c, p.d, p.direction))
        b = normalize(S_clip(c + hi if p.c is None else hi, c, p.d, p.direction))
        return min(a, b), max(a, b)
    if k == "L":
        a = normalize(L(sup.lo, *p.anchors))
        b = normalize(L(sup.hi, *p.anchors))
        return min(a, b), max(a, b)
    if k in ("P_hist", "P_hist_rev"):
        if rules.phist_tie != "mid" or not rules.phist_include_today:
            raise RangeError(f"{p.indicator_id}: 只支援 phist_tie=mid 且含當日，得 {rules.phist_tie}/{rules.phist_include_today}")
        n = int(p.window)
        v_lo, v_hi = 100.0 * 0.5 / n, 100.0 * (n - 0.5) / n   # 最小值唯一 → (0+0.5)/n；最大值唯一 → (n−1+0.5)/n
        if k == "P_hist_rev":
            v_lo, v_hi = 100.0 - v_hi, 100.0 - v_lo
        return N(v_lo, 0.0, 100.0), N(v_hi, 0.0, 100.0)
    if k == "scenario":
        vals = {"divergence_scenario": rules.divergence_scores, "structure": rules.structure_scores,
                "continuation": rules.continuation_scores}[p.indicator_id]
        return scenario_value_after_N(min(vals)), scenario_value_after_N(max(vals))
    if k == "volume_scenario":
        # 單日原生值：序 1＝vs_scores[0]；序 2／3＝基底 ± half×(s−50)，s＝S_clip(量比−1; c, d)，條件量比 ≥ vs_high_ratio
        # （量比無上界）；其餘＝vs_scores[3]。多日均與短線加權都是凸組合 → 可達＝[單日最小, 單日最大]。
        s_lo = S_clip(rules.vs_high_ratio - 1.0, rules.vs_ratio_c, rules.vs_ratio_d).native
        s_hi = S_clip(INF, rules.vs_ratio_c, rules.vs_ratio_d).native
        pull, up, down, neutral = rules.vs_scores
        half, mid = rules.vs_formula_half, rules.hysteresis_first
        cand = [pull, neutral, up + half * (s_lo - mid), up + half * (s_hi - mid),
                down - half * (s_lo - mid), down - half * (s_hi - mid)]
        return scenario_value_after_N(min(cand)), scenario_value_after_N(max(cand))
    if k == "margin_scenario":
        # 序 1／2＝中性 50；序 3（r ≥ z）＝S_clip(−r)；序 4（r ≤ −z）＝50＋half×(S_clip(−r)−50)。r ∈ [−100, ∞)。
        z, mid, half = rules.margin_near_zero_pct, rules.hysteresis_first, rules.margin_half
        c = 0.0 if p.c is None else float(p.c)
        s3 = [S_clip(-r, c, p.d).native for r in (z, sup.hi)]              # r ∈ [z, ∞)
        s4 = [mid + half * (S_clip(-r, c, p.d).native - mid) for r in (-z, sup.lo)]   # r ∈ [−100, −z]
        cand = [mid, *s3, *s4]
        return min(cand), max(cand)                    # 原生值域即 S_RANGE，不套 N（程式回 Ind(…, S_RANGE)）
    if k == "direction":
        if direction_range is None:
            raise RangeError("market_direction 需要大盤方向分數區間")
        return direction_range
    raise RangeError(f"{p.indicator_id}: 未知 kind {k}")


# ---------------------------------------------------------------------------
# 步驟 2：族狀態 × 爻組合
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class FamState:
    present: bool
    reason: str | None        # 全缺時的原因
    reweighted: bool
    lo: float = 0.0
    hi: float = 0.0


def family_states(subs: dict[str, tuple[float, float, float]], variants: list[tuple[str, ...]]) -> list[FamState]:
    """`subs`：indicator_id → (lo, hi, sub_weight)。回傳全部可能狀態（全缺兩種原因＋各組合的非空子集）。"""
    out = [FamState(False, REASON_INSUFFICIENT, False), FamState(False, REASON_MISSING, False)]
    for var in variants:
        for r in range(1, len(var) + 1):
            for sel in itertools.combinations(var, r):
                w = sum(subs[i][2] for i in sel)
                lo = sum(subs[i][0] * subs[i][2] for i in sel) / w
                hi = sum(subs[i][1] * subs[i][2] for i in sel) / w
                out.append(FamState(True, None, len(sel) < len(var), lo, hi))
    return out


def line_ranges(fams: dict[str, list[FamState]], weights: dict[str, float], rules) -> dict[str, Any]:
    """逐組合呼叫 `line_score` 判可行與重配；回傳甲／乙／全部（不含未知）的 [lo, hi] 與組合數。"""
    names = list(weights)
    acc = {"full": None, "reweighted": None, "any": None}
    n = {"full": 0, "reweighted": 0, "unknown": 0}
    for combo in itertools.product(*(fams[f] for f in names)):
        frs = [FamilyResult(f, 50.0 if st.present else None, (),
                            missing=None if st.present else Missing(st.reason), reweighted=st.reweighted)
               for f, st in zip(names, combo)]
        lr = line_score("x", frs, weights, rules.unknown_below, exclude_insufficient=rules.coverage_excludes_insufficient)
        if lr.unknown:
            n["unknown"] += 1
            continue
        got = sum(weights[f] for f, st in zip(names, combo) if st.present)
        lo = sum(weights[f] * st.lo for f, st in zip(names, combo) if st.present) / got
        hi = sum(weights[f] * st.hi for f, st in zip(names, combo) if st.present) / got
        key = "reweighted" if lr.reweighted else "full"
        n[key] += 1
        for k in (key, "any"):
            acc[k] = (lo, hi) if acc[k] is None else (min(acc[k][0], lo), max(acc[k][1], hi))
    return {"full": acc["full"], "reweighted": acc["reweighted"], "any": acc["any"], "n": n}


def subs_of(ps, scope: str, horizon: str, line: str, family: str, direction_range) -> dict[str, tuple[float, float, float]]:
    out = {}
    for p in ps.family(scope, horizon, line, family):
        if p.indicator_id in NOT_A_SUB:
            continue
        sup = SUPPORT.get(p.indicator_id)
        if sup is None:
            raise RangeError(f"{scope}/{horizon}/{line}/{family}/{p.indicator_id} 沒有登錄 x 的支撐（SUPPORT）——新子指標要先補")
        lo, hi = sub_range(p, sup, ps.rules, direction_range)
        out[p.indicator_id] = (lo, hi, float(p.sub_weight))
    return out


def compute_market(ps) -> dict[str, Any]:
    rules = ps.rules
    # 方向分數＝固定爻權重的加權和，外界＝各爻外界的加權和——只在「任一爻未知即缺值、不重配」時成立。
    # policy 若改成 "reweight"，分母會隨未知爻變動，本推算不成立（驗收第一輪記錄項）。
    if rules.direction_unknown_policy != "missing":
        raise RangeError(f"direction_unknown_policy={rules.direction_unknown_policy!r}：方向分數外界的推算只對 'missing' 成立")
    rows: dict[tuple[str, str, str], dict[str, Any]] = {}
    subs_table: dict[tuple[str, str, str, str, str], tuple[float, float, float]] = {}
    direction: dict[str, tuple[float, float]] = {}
    for scope in SCOPES:                              # 大盤先算：個股上爻族 A 要用大盤方向分數
        for h in HORIZONS:
            for line in LINES:
                weights = ps.family_weights[(scope, h, line)]
                fams = {}
                for fam in weights:
                    subs = subs_of(ps, scope, h, line, fam, direction.get(h))
                    for iid, v in subs.items():
                        subs_table[(scope, h, line, fam, iid)] = v
                    variants = VARIANTS.get((scope, line, fam)) if h == "mid" else None
                    if variants is None:
                        variants = [tuple(subs)]
                    missing = {i for v in variants for i in v} - set(subs)
                    if missing:
                        raise RangeError(f"{scope}/{h}/{line}/{fam}：VARIANTS 列了不存在的子指標 {sorted(missing)}")
                    fams[fam] = family_states(subs, variants)
                _check_special(scope, h, line, fams, rules)
                rows[(scope, h, line)] = line_ranges(fams, weights, rules)
            if scope == SCOPE_MARKET:
                lw = ps.line_weights[(SCOPE_MARKET, h)]
                if any(rows[(scope, h, ln)]["any"] is None for ln in lw):
                    raise RangeError(f"大盤 {h} 有爻沒有任何可行狀態，方向分數不可達")
                wsum = sum(lw.values())
                direction[h] = (sum(w * rows[(scope, h, ln)]["any"][0] for ln, w in lw.items()) / wsum,
                                sum(w * rows[(scope, h, ln)]["any"][1] for ln, w in lw.items()) / wsum)
    return {"rows": rows, "subs": subs_table, "direction": direction}


def _check_special(scope: str, h: str, line: str, fams: dict[str, list[FamState]], rules) -> None:
    """兩個特例只會把分數拉向區間內；逐狀態斷言，不成立即中止（區間需另算）。"""
    if scope == SCOPE_STOCK and line == "1" and h == "mid":
        floor = scenario_value_after_N(rules.revenue_high_floor_native)
        bad = [s for s in fams["A"] if s.present and s.hi < floor]
        if bad:
            raise RangeError(f"個股初爻中期族 A 有狀態上界 < 下限 {floor}：下限會擴張區間，本程式的聯集不成立")
    if scope == SCOPE_STOCK and line == "3":
        cap = scenario_value_after_N(rules.overheat_cap_native)
        # 封頂作用在爻分；爻的每個可行狀態下界 ≤ 封頂即可（各族下界的加權平均 ≤ 最大族下界）
        if any(s.present and s.lo > cap for f in fams.values() for s in f):
            raise RangeError(f"個股三爻有族狀態下界 > 封頂 {cap}：封頂會擴張區間，本程式的聯集不成立")


# ---------------------------------------------------------------------------
# 輸出
# ---------------------------------------------------------------------------
def build(markets: tuple[str, ...] = MARKETS) -> dict[str, Any]:
    out: dict[str, Any] = {"schema": 1, "generated_by": "scripts/score_ranges.py", "source": "spec/stock-iching-plan-v1.2.2.md:714",
                           "rulings": ["#64"], "s_range": list(S_RANGE), "markets": {}, "rows": [], "subs": [], "support": {}}
    for m in markets:
        ps = build_params(m)
        res = compute_market(ps)
        out["markets"][m] = {"model_version": ps.model_version(),
                             "direction": {h: list(v) for h, v in res["direction"].items()}}
        for (scope, h, line), r in sorted(res["rows"].items()):
            for cov, key in (("full", "full"), ("reweighted", "reweighted")):
                rng = r[key]
                out["rows"].append({"scope": scope, "market": m, "horizon": h, "line": line, "coverage": cov,
                                    "lo": None if rng is None else rng[0], "hi": None if rng is None else rng[1],
                                    "n_states": r["n"][key]})
        for (scope, h, line, fam, iid), (lo, hi, sw) in sorted(res["subs"].items()):
            out["subs"].append({"scope": scope, "market": m, "horizon": h, "line": line, "family": fam,
                                "indicator_id": iid, "lo": lo, "hi": hi, "sub_weight": sw})
    for iid, s in sorted(SUPPORT.items()):
        out["support"][iid] = {"kind": s.kind, "lo": _j(s.lo), "hi": _j(s.hi), "half_window": s.half_window, "basis": s.basis}
    expected = len(SCOPES) * len(markets) * len(HORIZONS) * len(LINES) * 2
    if len(out["rows"]) != expected:
        raise RangeError(f"登錄筆數 {len(out['rows'])} ≠ {expected}")
    return out


def _j(v: float) -> float | str:
    return v if math.isfinite(v) else ("inf" if v > 0 else "-inf")


def _f(v: float | None) -> str:
    return "—" if v is None else f"{v:.4f}"


def as_markdown(rep: dict[str, Any]) -> str:
    L_ = ["# 爻分數可達邊界登錄（§16.5 `:714`）", "",
          "**本檔由 `scripts/score_ranges.py` 產生，不得手改**（`--check` 守門）。規格 `spec/stock-iching-plan-v1.2.2.md:714` "
          "不列任何區間數字，**以本檔為準**；機器可讀版為 `data/score_ranges.json`（全精度）。", "",
          "- 登錄鍵：`scope × market × horizon × line × coverage`（`spec/dimensions.json` 的 `line_reachable_range`）。"
          f"本檔共 **{len(rep['rows'])}** 筆；兩市場數值相同也各存一筆。",
          "- **甲**＝該爻所有族、族內所有子指標都有值；**乙**＝該爻有重配（整族缺或族內子指標缺，"
          "即 `line_k_reweighted=1`），取所有可行組合的聯集。可行性由 `aggregate.line_score` 判定（任何族都允許以 "
          "`insufficient_history` 缺，裁定 #64 ⑦）。",
          "- 個股上爻族 A 沿用同市場同期間大盤方向分數，上游取大盤各爻**所有可行狀態的聯集**（裁定 #64 ⑤）。",
          "- **外界、非緊界**：子指標與爻之間不獨立，端點未必同時到得了。`—`＝該覆蓋狀態不可能出現。", ""]
    for m, info in rep["markets"].items():
        L_ += [f"## {m}（`model_version={info['model_version']}`）", "",
               "大盤方向分數（個股上爻族 A 的上游）：" + "；".join(f"{h} [{_f(v[0])}, {_f(v[1])}]" for h, v in info["direction"].items()), ""]
        for scope in SCOPES:
            L_ += [f"### {m} / {scope}", "", "| 期間 | 爻 | 甲 下界 | 甲 上界 | 乙 下界 | 乙 上界 | 甲組合數 | 乙組合數 |",
                   "|---|---|---:|---:|---:|---:|---:|---:|"]
            idx = {(r["horizon"], r["line"], r["coverage"]): r for r in rep["rows"] if r["market"] == m and r["scope"] == scope}
            for h in HORIZONS:
                for line in LINES:
                    a, b = idx[(h, line, "full")], idx[(h, line, "reweighted")]
                    L_.append(f"| {h} | {line} | {_f(a['lo'])} | {_f(a['hi'])} | {_f(b['lo'])} | {_f(b['hi'])} | "
                              f"{a['n_states']:,} | {b['n_states']:,} |")
            L_.append("")
    L_ += ["## 子指標 x 的數學支撐（步驟 1 的輸入，裁定 #64 ④：放本腳本、不進 ParamSet）", "",
           "| 子指標 | 類型 | 支撐 | 依據 |", "|---|---|---|---|"]
    for iid, s in rep["support"].items():
        sup = "[−n/2, n/2]（n＝window）" if s["half_window"] else (
            "—" if s["kind"] in ("scenario", "volume_scenario", "P_hist", "P_hist_rev", "direction") else f"[{s['lo']}, {s['hi']}]")
        L_.append(f"| `{iid}` | {s['kind']} | {sup} | {s['basis']} |")
    L_ += ["", "不當子指標列舉：" + "；".join(f"`{k}`（{v}）" for k, v in NOT_A_SUB.items()) + "。", ""]
    return "\n".join(L_)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="§16.5 :714 可達邊界登錄（步驟 1～3）")
    ap.add_argument("--out-md", default=str(OUT_MD))
    ap.add_argument("--out-json", default=str(OUT_JSON))
    ap.add_argument("--check", action="store_true", help="只比對、不寫入；內容會變就 rc=1")
    args = ap.parse_args(argv)
    try:
        rep = build()
        md, js = as_markdown(rep), json.dumps(rep, ensure_ascii=False, indent=1, sort_keys=True) + "\n"
        pm, pj = Path(args.out_md), Path(args.out_json)
        if args.check:
            same = pm.exists() and pj.exists() and pm.read_text(encoding="utf-8") == md and pj.read_text(encoding="utf-8") == js
            if not same:
                print("[score_ranges] 登錄檔與程式實算不一致，請重跑本腳本", file=sys.stderr)
                return 1
            print("== 可達邊界登錄與程式實算一致")
            return 0
        pm.parent.mkdir(parents=True, exist_ok=True)
        pj.parent.mkdir(parents=True, exist_ok=True)
        pm.write_text(md, encoding="utf-8")
        pj.write_text(js, encoding="utf-8")
        print(f"== 已寫入 {pm} 與 {pj}（{len(rep['rows'])} 筆）")
        return 0
    except Exception as e:  # noqa: BLE001  任何例外一律 rc=2，不與 --check 的 rc=1 混淆
        print(f"[score_ranges 中止] {type(e).__name__}: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
