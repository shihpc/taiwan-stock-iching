"""測試專用：prereg-v1 引擎（`RULES_VERSION` `p2-score-engine-2`，PR-B 基底 `66fe824`）被 D4-①(a) NaN 守門改掉的那幾支函式的**逐字副本**。

供 `tests/test_nan_guard.py` 做兩件事：①乙案證據——同一組含 NaN 日的合成輸入，v1／v2 引擎跑 `iching.xdump.XDump` 的 x dump
逐位相同（NaN 的 x 在兩版都 `skipped`，`docs/P3-CALIBRATION.md` §37）；②合成世界普查「v1 7 列 → v2 0 列」在同一支測試內實跑。
體例同 `tests/pre68.py`：**不是生產碼**，只在 `v1_engine()` 之內以 monkeypatch 換進 `iching.score.market`／`stock`／`aggregate`／
`hexagram`／`assemble`、`iching.replay_state`／`replay_step` 的對應名稱，離開即還原（`tests/conftest.py` 的 session 末守門另斷言未洩漏）。
出處：`git show 66fe824:src/iching/score/{market,aggregate,hexagram,stock}.py`、`66fe824:src/iching/replay_step.py`——函式本體逐字照抄、
只改名加 `_v1` 後綴；呼叫到的共用件（`_arr`／`sma_last`／`sma_at`／`S_clip`／`L`／`scenario`／`normalize`／`Missing`／`Ind`）在兩版未變，直接引用現行。
"""
from __future__ import annotations

from contextlib import contextmanager
from typing import Any

import numpy as np

from iching import replay_state as RS
from iching import replay_step as ST
from iching.score import aggregate as AGG
from iching.score import assemble as ASM
from iching.score import hexagram as HX
from iching.score import market as MK
from iching.score import stock as STK
from iching.score.aggregate import SubResult
from iching.score.hexagram import YANG, YIN
from iching.score.indicators import as_f, ma_change, sma_at, sma_last
from iching.score.market import _arr
from iching.score.params import RULES_START, Rules
from iching.score.transform import (Ind, L, Missing, REASON_CONTRACT_ROLLED, REASON_DENOM_ZERO, REASON_INSUFFICIENT,
                                    REASON_MISSING, S_RANGE, S_clip, normalize, scenario)

RULES_VERSION_V1 = "p2-score-engine-2"


# ---- aggregate.py（66fe824） -------------------------------------------------------------------------------------------
def sub_result_v1(indicator_id: str, out: Ind | Missing, sub_weight: float = 1.0) -> SubResult:
    if isinstance(out, Missing):
        return SubResult(indicator_id, None, None, None, False, out, sub_weight)
    if not isinstance(out, Ind):
        raise TypeError(f"{indicator_id}: indicator must return Ind or Missing, got {type(out).__name__}")
    return SubResult(indicator_id, normalize(out), out.native, out.x, out.clipped, None, sub_weight, dict(out.meta))


# ---- market.py（66fe824） ----------------------------------------------------------------------------------------------
def ind_index_ma_distance_v1(close, atr_prev: float | None, n_ma: int, d: float) -> Ind | Missing:
    ma = sma_last(close, n_ma)
    if ma is None:
        return Missing(REASON_INSUFFICIENT, f"MA{n_ma}")
    if atr_prev is None:
        return Missing(REASON_INSUFFICIENT, "ATR14")
    if atr_prev == 0:
        return Missing(REASON_DENOM_ZERO, "ATR14=0")
    x = (float(as_f(close)[-1]) - ma) / atr_prev
    return S_clip(x, 0.0, d)


def ind_index_ma_slope_v1(close, atr_prev: float | None, n_ma: int, n_change: int, d: float) -> Ind | Missing:
    chg = ma_change(close, n_ma, n_change)
    if chg is None:
        return Missing(REASON_INSUFFICIENT, f"MA{n_ma} change {n_change}")
    if atr_prev is None:
        return Missing(REASON_INSUFFICIENT, "ATR14")
    if atr_prev == 0:
        return Missing(REASON_DENOM_ZERO, "ATR14=0")
    return S_clip(chg / atr_prev, 0.0, d)


def ind_range_position_v1(close, n: int, anchors: tuple[float, float, float]) -> Ind | Missing:
    c = as_f(close)
    if c.size < n:
        return Missing(REASON_INSUFFICIENT, f"range {n}")
    w = c[-n:]
    lo, hi = float(w.min()), float(w.max())
    if hi == lo:
        return Missing(REASON_DENOM_ZERO, "max=min")
    return L((float(c[-1]) - lo) / (hi - lo), *anchors)


def ind_ratio_L_v1(series, n_avg: int, anchors: tuple[float, float, float]) -> Ind | Missing:
    a = _arr(series)
    if a is None:
        return Missing(REASON_MISSING, "series")
    v = sma_last(a, n_avg)
    if v is None:
        return Missing(REASON_INSUFFICIENT, f"avg {n_avg}")
    return L(v, *anchors)


def ind_new_high_low_v1(series, d: float) -> Ind | Missing:
    a = _arr(series)
    if a is None:
        return Missing(REASON_MISSING, "new_high_low_ratio")
    return S_clip(float(a[-1]) * 100.0, 0.0, d)


def ind_amount_ratio_v1(amount, num_n: int, den_n: int, c: float, d: float) -> Ind | Missing:
    a = _arr(amount)
    if a is None:
        return Missing(REASON_MISSING, "amount")
    num = sma_at(a, num_n, 0)
    den = sma_at(a, den_n, 1)
    if num is None or den is None:
        return Missing(REASON_INSUFFICIENT, f"amount {num_n}/{den_n}")
    if den == 0:
        return Missing(REASON_DENOM_ZERO, "AMTMA=0")
    return S_clip(num / den, c, d)


def ind_divergence_scenario_v1(close, amount, n: int, rules: Rules) -> Ind | Missing:
    c, a = _arr(close), _arr(amount)
    if c is None or a is None:
        return Missing(REASON_MISSING, "close/amount")
    if c.size < n:
        return Missing(REASON_INSUFFICIENT, f"close {n}")
    ma = sma_at(a, n, 1)
    if ma is None:
        return Missing(REASON_INSUFFICIENT, f"AMTMA{n}")
    w = c[-n:]
    hi, lo = float(w.max()), float(w.min())
    if hi == lo:
        return Missing(REASON_DENOM_ZERO, "max=min")
    cur, amt = float(c[-1]), float(a[-1])
    new_high, new_low = cur >= hi, cur <= lo
    s1, s2, s3, s4 = rules.divergence_scores
    if new_high and amt < ma:
        return scenario(s1, seq=1)
    if new_low and amt >= rules.divergence_high_amt_mult * ma:
        return scenario(s2, seq=2)
    if new_low and amt < rules.divergence_low_amt_mult * ma:
        return scenario(s3, seq=3)
    return scenario(s4, seq=4)


def ind_net_amount_ratio_v1(net_amount, amount, n: int, d: float) -> Ind | Missing:
    x, a = _arr(net_amount), _arr(amount)
    if x is None or a is None:
        return Missing(REASON_MISSING, "net_amount/amount")
    if x.size < n or a.size < n:
        return Missing(REASON_INSUFFICIENT, f"window {n}")
    den = float(np.sum(a[-n:]))
    if den == 0:
        return Missing(REASON_DENOM_ZERO, "Σamount=0")
    return S_clip(float(np.sum(x[-n:])) / den * 100.0, 0.0, d)


def ind_buy_days_v1(net, n: int, d: float) -> Ind | Missing:
    x = _arr(net)
    if x is None:
        return Missing(REASON_MISSING, "net")
    if x.size < n:
        return Missing(REASON_INSUFFICIENT, f"window {n}")
    days = int(np.sum(x[-n:] > 0))
    return S_clip(days - n / 2.0, 0.0, d)


def ind_oi_change_v1(net_oi, n: int, d: float) -> Ind | Missing:
    x = _arr(net_oi)
    if x is None:
        return Missing(REASON_MISSING, "foreign_net_oi")
    if x.size < n + 1:
        return Missing(REASON_INSUFFICIENT, f"window {n}")
    return S_clip(float(x[-1] - x[-1 - n]), 0.0, d)


def ind_basis_v1(basis, contract_rolled: bool, d: float, median_n: int, include_today: bool) -> Ind | Missing:
    if contract_rolled:
        return Missing(REASON_CONTRACT_ROLLED, "換月日")
    b = _arr(basis)
    if b is None:
        return Missing(REASON_MISSING, "basis")
    need = median_n if include_today else median_n + 1
    if b.size < need:
        return Missing(REASON_INSUFFICIENT, f"basis {need}")
    c = float(np.median(b[-median_n:] if include_today else b[-median_n - 1:-1]))
    out = S_clip(float(b[-1]), c, d)
    return Ind(out.native, out.native_range, out.x, out.clipped, {"c_rolling_median": c})


def ind_period_return_v1(series, n: int, d: float, direction: int = 1) -> Ind | Missing:
    a = _arr(series)
    if a is None:
        return Missing(REASON_MISSING, "series")
    if a.size < n + 1:
        return Missing(REASON_INSUFFICIENT, f"window {n}")
    base = float(a[-1 - n])
    if base == 0:
        return Missing(REASON_DENOM_ZERO, "base=0")
    return S_clip((float(a[-1]) / base - 1.0) * 100.0, 0.0, d, direction)


# ---- hexagram.py（66fe824） --------------------------------------------------------------------------------------------
def lines_from_scores_v1(scores, rules: Rules = RULES_START) -> list[int] | None:
    if len(scores) != 6:
        raise ValueError("need 6 line scores")
    if any(s is None for s in scores):
        return None
    return [1 if float(s) >= rules.hysteresis_first else 0 for s in scores]


def hysteresis_step_v1(prev_state: str | None, prev_streak: int, score: float | None, rules: Rules = RULES_START) -> tuple[str | None, int, bool]:
    if score is None:
        return prev_state, prev_streak, False
    s = float(score)
    if prev_state is None:
        return (YANG if s >= rules.hysteresis_first else YIN), 0, False
    if prev_state == YIN:
        if s >= rules.hysteresis_up:
            streak = prev_streak + 1
            if streak >= rules.hysteresis_confirm_days:
                return YANG, 0, True
            return YIN, streak, False
        return YIN, 0, False
    if prev_state == YANG:
        if s <= rules.hysteresis_down:
            streak = prev_streak + 1
            if streak >= rules.hysteresis_confirm_days:
                return YIN, 0, True
            return YANG, streak, False
        return YANG, 0, False
    raise ValueError(f"bad prev_state {prev_state!r}")


# ---- stock.py／replay_step.py（66fe824） -------------------------------------------------------------------------------
def ind_market_direction_v1(score: float | None) -> Ind | Missing:
    if score is None:
        return Missing(REASON_MISSING, "market direction score")
    return Ind(float(score), S_RANGE, float(score))


def _score_or_none_v1(v: Any) -> float | None:
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else None


#: (模組, 名稱, v1 函式)：`v1_engine()` 換掉的全部綁定名（含被 `from … import` 進去的副本）。
PATCHES: tuple[tuple[object, str, object], ...] = (
    (AGG, "sub_result", sub_result_v1), (MK, "sub_result", sub_result_v1), (STK, "sub_result", sub_result_v1),
    (MK, "ind_index_ma_distance", ind_index_ma_distance_v1), (MK, "ind_index_ma_slope", ind_index_ma_slope_v1),
    (MK, "ind_range_position", ind_range_position_v1), (MK, "ind_ratio_L", ind_ratio_L_v1),
    (MK, "ind_new_high_low", ind_new_high_low_v1), (MK, "ind_amount_ratio", ind_amount_ratio_v1),
    (MK, "ind_divergence_scenario", ind_divergence_scenario_v1), (MK, "ind_net_amount_ratio", ind_net_amount_ratio_v1),
    (MK, "ind_buy_days", ind_buy_days_v1), (MK, "ind_oi_change", ind_oi_change_v1), (MK, "ind_basis", ind_basis_v1),
    (MK, "ind_period_return", ind_period_return_v1),
    (HX, "lines_from_scores", lines_from_scores_v1), (ASM, "lines_from_scores", lines_from_scores_v1),
    (HX, "hysteresis_step", hysteresis_step_v1), (RS, "hysteresis_step", hysteresis_step_v1),
    (STK, "ind_market_direction", ind_market_direction_v1),
    (ST, "_score_or_none", _score_or_none_v1),
)

#: 現行（v2）那一組，供 session 末守門斷言未洩漏。
CURRENT: dict[tuple[int, str], object] = {(id(mod), name): getattr(mod, name) for mod, name, _ in PATCHES}


@contextmanager
def v1_engine(mp):
    """`mp`＝`pytest.MonkeyPatch`（函式層的 `monkeypatch` 或 `MonkeyPatch.context()`）；離開時由它還原。"""
    for mod, name, fn in PATCHES:
        mp.setattr(mod, name, fn)
    yield
