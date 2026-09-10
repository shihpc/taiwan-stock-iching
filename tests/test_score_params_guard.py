"""參數守門（驗收必修 3／建議 C5-1；02bc754 重驗後據實改寫）：
1. 字面量守門：`src/iching/score/`（params.py 除外）內所有數值字面量，必須是 `ParamSet.numeric_values()` 的成員或列在
   `params.NON_PARAM_CONSTANTS` 白名單。**只攔「與任何參數值／白名單不重合的新字面量」**——把 `rules.vs_ratio_c`
   改回寫死 `0.3`、`rules.unknown_below` 改回 `0.5` 這種**同值寫死**攔不到（驗收實測：三處改回寫死仍全綠）。
2. 宣告參數＝實際使用：對每個 `Param`，突變 `window`／`anchors`／`c`／`d` 後至少一個輸出（含明細列）改變，否則是死參數。
3. **Rules 欄位＝實際使用（真守門）**：對 `Rules` 每個欄位逐欄突變，在能走到該分支的合成情境下至少一個輸出改變；
   某欄若被寫死取代，突變它就不會改變任何輸出 → 紅。走不到的欄位明列於 `RULES_UNREACHABLE`。
4. `Rules` 與 `ParamSet.calibrated` 進 `model_version` 指紋；白名單每條值仍在程式中出現。
"""
from __future__ import annotations

import ast
import dataclasses
from pathlib import Path

import pytest

from conftest import ROOT, synth_market_inputs, synth_stock_inputs
from iching.score import assemble_row, build_params, score_market, score_stock
from iching.score.params import NON_PARAM_CONSTANTS, RULES_START, SCOPE_MARKET, Rules

SCORE_DIR = ROOT / "src" / "iching" / "score"
DV, TV = "fm-20260909-01", "0.2"


def _numeric_literals(path: Path) -> list[tuple[float, int]]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    out = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)) and not isinstance(node.value, bool):
            out.append((float(node.value), node.lineno))
    return out


def test_no_hardcoded_numbers_outside_params_or_whitelist():
    allowed = build_params("twse").numeric_values() | build_params("tpex").numeric_values() | set(NON_PARAM_CONSTANTS)
    offenders = []
    for p in sorted(SCORE_DIR.glob("*.py")):
        if p.name == "params.py":
            continue
        for v, ln in _numeric_literals(p):
            if v not in allowed:
                offenders.append(f"{p.name}:{ln} = {v!r}")
    assert offenders == [], "寫死的數字不在參數字典也不在白名單：\n" + "\n".join(offenders)


def test_whitelist_entries_are_documented_and_not_parameters():
    params_vals = build_params("twse").numeric_values()
    for v, why in NON_PARAM_CONSTANTS.items():
        assert isinstance(why, str) and len(why) >= 4, v
    # 白名單裡若有值同時也是參數值，說明必須明講「同值」（避免把參數值當結構常數矇混）
    for v in sorted(set(NON_PARAM_CONSTANTS) & params_vals):
        assert "同值" in NON_PARAM_CONSTANTS[v], (v, NON_PARAM_CONSTANTS[v])


def test_whitelist_values_still_appear():
    """建議 3：白名單每條值至少在一處字面量出現，過時項要清掉。"""
    seen = set()
    for p in SCORE_DIR.glob("*.py"):
        if p.name != "params.py":
            seen |= {v for v, _ in _numeric_literals(p)}
    stale = sorted(v for v in NON_PARAM_CONSTANTS if v not in seen)
    assert stale == [], f"白名單過時項（程式已無此字面量）：{stale}"


def test_rules_and_calibrated_enter_fingerprint():
    ps = build_params("twse")
    for fld in dataclasses.fields(Rules):
        if fld.name == "calibrated":
            continue
        assert ps.with_rules(**{fld.name: RULES_MUTATIONS[fld.name]}).model_version() != ps.model_version(), fld.name
    assert ps.with_rules(calibrated=True).model_version() != ps.model_version()
    assert dataclasses.replace(ps, calibrated=True).model_version() != ps.model_version()
    assert RULES_START.calibrated is False and all(p.calibrated is False for p in ps.params.values())


import numpy as np

# 合成資料預設走不到的分支，用覆寫讓該子指標真的被算到（否則測不出死參數）
_FIN = {"eps": 2.0, "eps_ly": 1.5, "gross_margin": 50.0, "gross_margin_prev_q": 49.0, "price_at_period_end": 100.0,
        "pretax_income": 120.0, "pretax_income_ly": 100.0, "equity": 1000.0, "equity_prev_q": 980.0}
STOCK_OVERRIDES = {
    "eps_diff_over_price": dict(fundamentals={**_FIN, "eps_ly": 0.05}),          # 前期 EPS ≤ 0.1 → 走替代指標
    "pretax_income_yoy": dict(is_financial=True),                                  # 金融保險業替代規則
    "equity_qoq": dict(is_financial=True),
    "margin_scenario": dict(margin_balance=5000.0 * (1 + 0.01 * np.arange(320)),  # 資增 ≥0.5% 且期間報酬 ≤0 → 序 3（用到 c/d）
                            close=200.0 - 0.1 * np.arange(320)),
}


def _row(ps, key, mv):
    scope, h, line, fam, iid = key
    if scope == SCOPE_MARKET:
        return assemble_row(score_market(synth_market_inputs(), ps, h), ps, DV, TV, model_version=mv, detail=True)
    return assemble_row(score_stock(synth_stock_inputs(**STOCK_OVERRIDES.get(iid, {})), ps, h), ps, DV, TV, model_version=mv, detail=True)


def _bump_window(w):
    """視窗放大 1000 倍：合成序列只有 320 筆，必然 insufficient_history／查無此窗 → 輸出改變。"""
    if isinstance(w, tuple):
        return tuple(x * 1000 for x in w)
    return w * 1000


@pytest.mark.parametrize("key", sorted(build_params("twse").params))
def test_every_declared_param_field_is_consumed(key):
    ps = build_params("twse")
    p = ps.params[key]
    if not p.scored:
        pytest.skip("scored=False（只顯示）")
    mv = ps.model_version()
    base = _row(ps, key, mv)
    scope, h, line, fam, iid = key
    checked = 0
    if p.window is not None:
        alt = _row(ps.with_param(*key, window=_bump_window(p.window)), key, mv)
        assert alt != base, f"{key}: window 是死參數"
        checked += 1
    if p.anchors is not None:
        alt = _row(ps.with_param(*key, anchors=tuple(a + 100.0 for a in p.anchors)), key, mv)
        assert alt != base, f"{key}: anchors 是死參數"
        checked += 1
    if p.d is not None:
        changes = {"d": p.d * 3.0}
        if p.c is not None:
            changes["c"] = p.c + p.d * 0.5
        alt = _row(ps.with_param(*key, **changes), key, mv)
        assert alt != base, f"{key}: c/d 是死參數"
        checked += 1
    if checked == 0:
        assert p.transform == "passthrough", f"{key}: 無可突變欄位（window/anchors/c/d 皆 None）卻不是 passthrough"
        pytest.skip("passthrough 且無參數欄位（大盤方向分數直接沿用）")


# ---------------------------------------------------------------------------
# 3. Rules 欄位＝實際使用（必修 2 ②）
# ---------------------------------------------------------------------------
from iching.score import market_flags
from iching.score.hexagram import YANG, YIN, hysteresis_step
from iching.score.params import LINE2_SERIES_LEN, HORIZONS

N_ = 320


def _mkt_scenarios():
    """能走到各 Rules 分支的大盤情境（名稱 → MarketInputs 覆寫）。"""
    base = synth_market_inputs()
    idx = np.asarray(base.index_close)
    amt = np.full(N_, 3e11)
    up = np.r_[np.linspace(15000, 16000, N_ - 1), 16500.0]          # T 日創 60 日新高
    dn = np.r_[np.linspace(16000, 15000, N_ - 1), 14500.0]          # T 日創 60 日新低
    return {
        "default": {},
        "missing_oi": dict(foreign_net_oi=None),                                        # 五爻重配（unknown_below）
        "t5_missing": dict(line2_score_t_minus_5={}),                                   # 1 個缺因
        "t5_vix_missing": dict(line2_score_t_minus_5={}, vix=None,                      # 2 個缺因 → 名額 ×0.5
                               **{k: np.asarray(getattr(base, k))[-100:] for k in ("index_open", "index_high", "index_low", "index_close")}),
        "stale": dict(us_dates=list(base.us_dates)[:-4], spx_close=np.asarray(base.spx_close)[:-4],
                      spx_high=np.asarray(base.spx_high)[:-4], spx_low=np.asarray(base.spx_low)[:-4],
                      sox_close=np.asarray(base.sox_close)[:-4]),                         # stale_days ≥ 3
        "div_seq1": dict(index_close=up, index_high=up + 50, index_low=up - 50, amount=np.r_[amt[:-1], 1.5e11]),
        "div_seq2": dict(index_close=dn, index_high=dn + 50, index_low=dn - 50, amount=np.r_[amt[:-1], 4.8e11]),
        "div_seq3": dict(index_close=dn, index_high=dn + 50, index_low=dn - 50, amount=np.r_[amt[:-1], 2.1e11]),
    }


def _stk_scenarios():
    n = N_
    flat = np.full(n, 100.0)
    upvol_c, upvol_v = flat.copy(), np.full(n, 1000.0)
    upvol_c[-1] = 102.0; upvol_v[-1] = 2000.0                        # 序 2 上漲放量
    pb_c, pb_v = flat.copy(), np.full(n, 1000.0)
    pb_c[-3] = 104.0; pb_c[-2] = 103.0; pb_c[-1] = 103.0; pb_v[-2] = 500.0; pb_v[-1] = 500.0   # 序 1 縮量回檔
    jump = flat.copy(); jump[-1] = 130.0                             # 過熱
    rev_flat = [(f"{2022 + i // 12}-{i % 12 + 1:02d}", 100.0) for i in range(29)] + [("2024-06", 101.0)]
    brk = np.r_[np.full(n - 4, 100.0), 105.0, 106.0, 106.0, 107.0]   # 突破後第 3 日守住（短線）
    return {
        "default": {},
        "upvol": dict(close=upvol_c, high=upvol_c + 1, low=upvol_c - 1, volume=upvol_v),
        "pullback": dict(close=pb_c, high=pb_c + 1, low=pb_c - 1, volume=pb_v,
                         line2_score_history={h: [56.0] * (LINE2_SERIES_LEN - 1) for h in HORIZONS}),
        "overheat": dict(close=jump, high=jump + 1, low=jump - 1, p_cs_long_excess=99.0),
        "rev_high": dict(monthly_revenue=rev_flat),
        "eps_alt": dict(fundamentals={"eps": 0.5, "eps_ly": 0.05, "gross_margin": 50.0, "gross_margin_prev_q": 49.0, "price_at_period_end": 50.0}),
        "margin_up_pricedown": dict(margin_balance=5000.0 * (1 + 0.01 * np.arange(n)), close=200.0 - 0.1 * np.arange(n)),   # 序 3
        "margin_down": dict(margin_balance=5000.0 * (1 - 0.01 * np.arange(n) / n * 50)),                                     # 序 4
        "breakout": dict(close=brk, high=brk + 1, low=brk - 1),
    }


def _outputs(ps) -> list:
    out = []
    for name, ov in _mkt_scenarios().items():
        inp = synth_market_inputs(**ov)
        for h in HORIZONS:
            ms = score_market(inp, ps, h)
            out.append((name, h, assemble_row(ms, ps, DV, TV, model_version="x", detail=True), market_flags(ms, inp, ps)))
    for name, ov in _stk_scenarios().items():
        inp = synth_stock_inputs(**ov)
        for h in HORIZONS:
            out.append((name, h, assemble_row(score_stock(inp, ps, h), ps, DV, TV, model_version="x", detail=True)))
    # 遲滯一步（正式爻態）：陰→陽需連兩日 ≥55、陽→陰連兩日 ≤45、首次 50 分界、缺值不累加
    seq = [None, 52.0, 56.0, 56.0, 44.0, 44.0, 44.0, 58.0, None, 58.0]   # 58：hysteresis_up 55→60 時翻轉結果不同
    st, streak, trace = None, 0, []
    for s_ in seq:
        st, streak, flipped = hysteresis_step(st, streak, s_, ps.rules)
        trace.append((st, streak, flipped))
    out.append(("hysteresis", trace))
    return out


# 每欄的突變值：必須是「與預設不同且合法」的值
RULES_MUTATIONS = {
    "calibrated": True, "unknown_below": 0.7, "stale_degrade_at": 10,
    "hysteresis_first": 99.0, "hysteresis_up": 60.0, "hysteresis_down": 40.0, "hysteresis_confirm_days": 3,
    "divergence_high_amt_mult": 1.7, "divergence_low_amt_mult": 0.6, "divergence_scores": (36.0, 26.0, 56.0, 51.0),
    "eps_yoy_min_base": 2.0, "revenue_high_floor_native": 95.0, "industry_min_sample": 100,
    "structure_scores": (81.0, 21.0, 51.0), "overheat_cap_native": 60.0, "p_cs_overheat": 100.0, "overheat_dist_atr": 100.0,
    "vs_ratio_c": 0.9, "vs_ratio_d": 2.1, "vs_low_ratio": 0.3, "vs_high_ratio": 3.0, "vs_day_change": 5.0,
    "vs_drawdown_max": 0.1, "vs_line2_min": 0.0, "vs_scores": (61.0, 61.0, 41.0, 51.0), "vs_formula_half": 0.9,
    "vs_today_weight": 0.9, "vs_avg_days_short": 3, "vs_avg_days_swing": 5, "continuation_scores": (81.0, 21.0, 51.0),
    "margin_near_zero_pct": 2.0, "margin_half": 0.25,
    # 突變一支在合成情境下**會成立**的旗標（F-廣度擴張，預設 T−5=48 → 擴張 true）；F-臨界在合成資料下不成立、改它看不到
    "flag_effects": {**{k: dict(v) for k, v in RULES_START.flag_effects.items()}, "F-廣度擴張": {"long": (1.5, 0.5), "short": (1.5, 0.5)}},
    "breadth_change_threshold": 20.0, "shift_cap_deciles": 1.0, "high_vol_pct": 0.0, "critical_band": (0.0, 100.0),
    "trigram_hi": 50.0, "trigram_lo": 50.0, "insufficient_causes": 1, "insufficient_multiplier": 0.25,
}
# 明列走不到的欄位（合成情境下突變不會改變任何輸出）＋理由；目前為空——若日後某欄真的走不到，
# 必須在此登錄理由而不是把它從 Rules 拿掉
RULES_UNREACHABLE: dict[str, str] = {}


@pytest.fixture(scope="module")
def rules_baseline():
    ps = build_params("twse")
    return ps, _outputs(ps)


@pytest.mark.parametrize("field", [f.name for f in dataclasses.fields(Rules)])
def test_every_rules_field_is_consumed(field, rules_baseline):
    ps, base = rules_baseline
    assert field in RULES_MUTATIONS, f"Rules.{field} 沒有登錄突變值"
    if field in RULES_UNREACHABLE:
        pytest.skip(f"走不到：{RULES_UNREACHABLE[field]}")
    mutated = ps.with_rules(**{field: RULES_MUTATIONS[field]})
    assert getattr(mutated.rules, field) != getattr(ps.rules, field)
    assert _outputs(mutated) != base, f"Rules.{field} 突變後所有情境輸出不變（被寫死取代或無消費者）"


def test_rules_post_init_guards():
    with pytest.raises(ValueError):
        Rules(vs_avg_days_swing=LINE2_SERIES_LEN + 1)
    with pytest.raises(ValueError):
        Rules(vs_avg_days_short=0)
    with pytest.raises(ValueError):
        build_params("twse").with_rules(vs_avg_days_swing=11)
    assert Rules(vs_avg_days_swing=LINE2_SERIES_LEN).vs_avg_days_swing == LINE2_SERIES_LEN
