"""裁定 #77 試算卦（展示用、不改正式卦）的純函式（docs/P4-PREVIEW.md §16／§21）。

試算＝以正式卦 T 的完整輸入為基底，只換兩處：
- **上爻**：美股序列接上 T 之後已收盤的新列（台北日期 < 試算當天），台北日期改標 T'＝`night_query_date(T)`，
  使 `us_asof(T')`＝試算當下已收盤的最新美股日；**沒有新列時不換**（日期仍是 T、上爻＝正式值，避免 stale_days 平白 +1）。
- **大盤五爻**：外資台指期淨未平倉序列**多接一天**＝T 官方值＋T' 歸屬夜盤外資多空淨額口數（推算值）；
  不是替換最後一天（替換會丟掉 T 的官方值，變化量語意變成「推算 − T−n」）。夜盤取不到 → 不接（五爻＝正式值）。
其餘爻沿用正式輸入；個股只換 `market_direction_score`（個股上爻族 A 吃大盤方向）。

遲滯：以 `cross.json`（寫完 T 的狀態）為起點，**只對被換掉的爻**走一步 `hysteresis_step`、不寫回；
沒被換的爻沿用正式狀態（再走一步會把 T 的分數重複當成第二天確認）。

本模組不碰網路、不讀寫檔；計分仍走同一支 `score_market`／`score_stock`（引擎、參數、指紋不變）。
"""
from __future__ import annotations

import dataclasses
import datetime as dt
from typing import Any, Sequence

import numpy as np

from .score.hexagram import YANG, hysteresis_step
from .score.market import MarketInputs
from .score.params import Rules

WINDOW_DOW = (2, 3, 4, 5, 6)            # 台北週二～六（isoweekday）
WINDOW_HOURS = (6, 12)                  # 06:00 ≤ 時 < 12:00


def in_window(now_tpe: dt.datetime) -> bool:
    """試算時窗：台北週二～六 06:00–11:59（07:10 早晨班與 08:45 班；§16.6 加註）。"""
    return now_tpe.isoweekday() in WINDOW_DOW and WINDOW_HOURS[0] <= now_tpe.hour < WINDOW_HOURS[1]


def new_us_rows(rows: Sequence[Sequence[Any]], last_us: str | None, today: str) -> list[tuple]:
    """`[(date, spx_c, spx_h, spx_l, sox_c)]` 中日期 > 視窗最後美股日、且 < 台北今日（防盤中半成品 bar）者，升冪去重。"""
    out: dict[str, tuple] = {}
    for r in rows:
        d = str(r[0])
        if (last_us is None or d > last_us) and d < today:
            out[d] = tuple(r)
    return [out[d] for d in sorted(out)]


def oi_estimate(oi_official_T: float | None, night_net: int | None) -> float | None:
    """推算值＝T 一般時段官方外資淨未平倉＋T' 歸屬夜盤外資多空淨額口數；任一缺 → None。"""
    if oi_official_T is None or night_net is None:
        return None
    v = float(oi_official_T)
    if v != v:
        return None
    return v + float(night_net)


def _append(a: Any, vals: Sequence[float]) -> np.ndarray | None:
    if a is None:
        return None
    return np.concatenate([np.asarray(a, dtype=float), np.asarray(vals, dtype=float)])


def trial_market_inputs(mi: MarketInputs, target: str, us_new: Sequence[tuple], oi_est: float | None) -> MarketInputs:
    """回一份新的 `MarketInputs`（原物件不動）。`us_new` 非空才改 `tpe_date`→target 並接美股列；`oi_est` 非 None 才接外資淨未平倉。"""
    kw: dict[str, Any] = {}
    if us_new:
        if mi.us_dates is None or mi.spx_close is None:
            raise ValueError("正式輸入沒有美股序列，無法接新列")
        kw.update(
            tpe_date=target,
            tpe_dates=list(mi.tpe_dates or []) + [target],
            us_dates=list(mi.us_dates) + [r[0] for r in us_new],
            spx_close=_append(mi.spx_close, [r[1] for r in us_new]),
            spx_high=_append(mi.spx_high, [r[2] for r in us_new]),
            spx_low=_append(mi.spx_low, [r[3] for r in us_new]),
            sox_close=_append(mi.sox_close, [r[4] for r in us_new]),
        )
    if oi_est is not None:
        if mi.foreign_net_oi is None:
            raise ValueError("正式輸入沒有外資淨未平倉序列，無法接推算值")
        kw["foreign_net_oi"] = _append(mi.foreign_net_oi, [oi_est])
    return dataclasses.replace(mi, **kw)


def step_changed(prev: Sequence[tuple[str | None, int]] | None, scores: Sequence[float | None],
                 changed: Sequence[int], rules: Rules) -> dict[str, Any]:
    """`prev`＝cross.json 該鍵六爻 [(state, streak)]；只對 `changed`（1-based 爻號）走一步遲滯。
    回 {lf: 六位 0/1 或 None, st: 六個 state, sk: 六個 streak, flip: 被翻的爻號}；不改 `prev`。"""
    cur = [tuple(x) for x in (prev or [(None, 0)] * 6)]
    flip: list[int] = []
    for k in changed:
        st, sk = cur[k - 1]
        n_st, n_sk, fl = hysteresis_step(st, int(sk), scores[k - 1], rules)
        cur[k - 1] = (n_st, int(n_sk))
        if fl:
            flip.append(k)
    states = [s for s, _ in cur]
    lf = None if any(s is None for s in states) else [1 if s == YANG else 0 for s in states]
    return {"lf": lf, "st": states, "sk": [int(k) for _, k in cur], "flip": flip}
