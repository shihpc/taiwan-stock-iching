"""參數守門（驗收必修 3／建議 C5-1）：
1. `src/iching/score/`（params.py 除外）內所有數值字面量，必須是 `ParamSet.numeric_values()` 的成員或列在
   `params.NON_PARAM_CONSTANTS` 白名單——「下一個寫死的門檻」在此紅。
2. 宣告參數＝實際使用：對每個 `Param`，突變 `window`／`anchors`／`c`／`d` 後至少一個輸出（含明細列）改變，否則是死參數。
3. `Rules` 與 `ParamSet.calibrated` 進 `model_version` 指紋。
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


def test_rules_and_calibrated_enter_fingerprint():
    ps = build_params("twse")
    for fld in dataclasses.fields(Rules):
        if fld.name in ("calibrated", "flag_effects"):
            continue
        cur = getattr(ps.rules, fld.name)
        if isinstance(cur, tuple):
            new = tuple(x + 1.0 for x in cur)
        elif isinstance(cur, int) and not isinstance(cur, bool):
            new = cur + 1
        else:
            new = cur + 1.0
        assert ps.with_rules(**{fld.name: new}).model_version() != ps.model_version(), fld.name
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
        return assemble_row(score_market(synth_market_inputs(), ps, h), mv, DV, TV, detail=True)
    return assemble_row(score_stock(synth_stock_inputs(**STOCK_OVERRIDES.get(iid, {})), ps, h), mv, DV, TV, detail=True)


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
