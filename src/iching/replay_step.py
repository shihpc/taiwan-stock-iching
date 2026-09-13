"""`step(T)`（第 13 項 13a-2）——**唯一的計分入口**，Hetzner 回補與每日班共用（`docs/P2-REPLAY-PLAN.md` §2、§4）。
純函式層：不做 DB 存取，吃 `WindowCache`（已 `ingest(T)`）＋ `CrossDayState`，吐當日所有列與診斷；
落地由 `scores_io.ScoreStore.write_day` 做。

## 同日順序（§4，逐字對應）

```
pool_T = cross.adv.eligible()                    # PIT：先取（T−1 為止的 60 日 ADV）
for mk: for h:  ms = score_market(inputs)         # 大盤 3 期間
                formal, streaks = cross.advance_lines(...)   # 遲滯
                own_state = basic_state(line1 state, line2 state)
for mk: for h:  flags = market_flags(ms, inputs with own_state / other_market_state)   # 兩市場互為對方
for sid: for h: ss = score_stock(inputs with market_direction_score)；遲滯；push line2
cross.adv.push_day(T, amounts)                    # PIT：後推
cross.market_line2 推進；cross.last_date = T
```

- 大盤二爻歷史在**建完三個 horizon 的輸入之後**才 push（`line2_score_t_minus_5` 讀的是 T−5，push 的是 T）。
- `other_market_state`：另一市場當日沒有指數列（未計分）時為 None。
- `market_direction_score` 可能是 `Missing` → 轉 None（`StockInputs` 的型別是 `float | None`）。
- 個股只算 `WindowCache.stock_ids_today()`（當日有成交且所屬市場有指數列）；**全 2,139 檔都算**（裁定 #34 Q1 乙），
  列上帶 `in_rank_pool`。
- `model_version` 每市場各一，**必須預先算好傳入**（`assemble_row` 每列重算指紋 5 ms）。
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping

from .replay_state import CrossDayState, WindowCache
from .score import score_market, score_stock
from .score.assemble import assemble_row
from .score.hexagram import basic_state
from .score.market import market_flags
from .score.params import HORIZONS, MARKETS, ParamSet
from .scores_io import flatten_row, states_text, streaks_text
from .universe import is_financial

FundamentalsProvider = Callable[[str, str], dict[str, Any]]
"""`(stock_id, T) → {"monthly_revenue", "industry_median_3m_yoy", "industry_revenue_n", "fundamentals"}`（13b 供給；缺省全 None）。"""


class ReplayStepError(RuntimeError):
    pass


@dataclass
class StepResult:
    tpe_date: str
    market_rows: list[tuple[str, dict[str, Any]]] = field(default_factory=list)   # (model_version, flatten_row)
    stock_rows: list[tuple[str, dict[str, Any]]] = field(default_factory=list)
    diag: dict[str, Any] = field(default_factory=dict)

    def all_rows(self) -> list[tuple[str, dict[str, Any]]]:
        return self.market_rows + self.stock_rows


def _score_or_none(v: Any) -> float | None:
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def step(T: str, wc: WindowCache, cross: CrossDayState, ps: Mapping[str, ParamSet], *, data_version: str,
         text_version: str, model_version: Mapping[str, str], fundamentals: FundamentalsProvider | None = None,
         detail: bool = False) -> StepResult:
    if wc.last_date != T:
        raise ReplayStepError(f"WindowCache 最後 ingest 的是 {wc.last_date}，不是 {T}（先 ingest 再 step）")
    if cross.last_date is not None and T <= cross.last_date:
        raise ReplayStepError(f"重播必須嚴格逐日單向：{T} ≤ CrossDayState.last_date={cross.last_date}")
    for m in MARKETS:
        if m not in ps or m not in model_version:
            raise ReplayStepError(f"缺 {m} 的 ParamSet／model_version")
    t0 = time.perf_counter()
    res = StepResult(tpe_date=T)
    pool_T = cross.adv.eligible()                                     # PIT：先取

    # -- 大盤 --
    scored: dict[str, dict[str, Any]] = {}                            # mk → h → MarketScores
    inputs: dict[str, Any] = {}
    formal: dict[tuple[str, str], list[int] | None] = {}
    streaks: dict[tuple[str, str], list[int]] = {}
    own_state: dict[tuple[str, str], str] = {}
    active = [m for m in MARKETS if wc.has_market(m) and wc.market_dates(m) and wc.market_dates(m)[-1] == T]
    for mk in active:
        mi = wc.market_inputs(mk, T, cross)
        inputs[mk] = mi
        scored[mk] = {}
        for h in HORIZONS:
            ms = score_market(mi, ps[mk], h)
            scored[mk][h] = ms
            f, s = cross.advance_lines("market", mk, h, ms.line_scores(), ps[mk].rules)
            formal[(mk, h)], streaks[(mk, h)] = f, s
            st = cross.market_lines[f"{mk}|{h}"]
            own_state[(mk, h)] = basic_state(st[0][0], st[1][0])
    n_market_unknown = 0
    for mk in active:
        other = [m for m in MARKETS if m != mk][0]
        mi = inputs[mk]
        for h in HORIZONS:
            ms = scored[mk][h]
            mi.own_state = own_state[(mk, h)]
            mi.other_market_state = own_state.get((other, h))
            fl = market_flags(ms, mi, ps[mk])
            row = assemble_row(ms, ps[mk], data_version, text_version, model_version=model_version[mk], flags=fl,
                               formal_lines=formal[(mk, h)], detail=detail)
            st = cross.market_lines[f"{mk}|{h}"]
            res.market_rows.append((model_version[mk], flatten_row(
                row, line_states=states_text(s for s, _ in st), streaks=streaks_text(streaks[(mk, h)]), in_rank_pool=None)))
            if any(row.get(f"line_{k}_unknown") for k in range(1, 7)):
                n_market_unknown += 1
    for mk in active:                                                 # 讀完 T−5 才 push T
        for h in HORIZONS:
            cross.push_market_line2(mk, h, scored[mk][h].line_scores()[1])
    direction = {mk: {h: _score_or_none(scored[mk][h].direction_score) for h in HORIZONS} for mk in active}

    # -- 個股 --
    n_stocks = n_in_pool = n_stock_unknown = 0
    for sid in wc.stock_ids_today():
        info = wc.pool[sid]
        mk = "twse" if info.get("type") == "twse" else "tpex"
        if mk not in active:
            continue
        fin = is_financial(info.get("industry_category"))
        extra = fundamentals(sid, T) if fundamentals is not None else {}
        n_stocks += 1
        in_pool = int(sid in pool_T)
        n_in_pool += in_pool
        any_unknown = False
        line2_today: dict[str, float | None] = {}
        for h in HORIZONS:
            si = wc.stock_inputs(sid, h, T, cross, direction[mk], is_financial=fin,
                                 monthly_revenue=extra.get("monthly_revenue"),
                                 industry_median_3m_yoy=extra.get("industry_median_3m_yoy"),
                                 industry_revenue_n=extra.get("industry_revenue_n"),
                                 fundamentals=extra.get("fundamentals"))
            ss = score_stock(si, ps[mk], h)
            f, s = cross.advance_lines("stock", sid, h, ss.line_scores(), ps[mk].rules)
            row = assemble_row(ss, ps[mk], data_version, text_version, model_version=model_version[mk],
                               formal_lines=f, detail=detail)
            st = cross.stock_lines[f"{sid}|{h}"]
            res.stock_rows.append((model_version[mk], flatten_row(
                row, line_states=states_text(x for x, _ in st), streaks=streaks_text(s), in_rank_pool=in_pool)))
            if any(row.get(f"line_{k}_unknown") for k in range(1, 7)):
                any_unknown = True
            line2_today[h] = ss.line_scores()[1]
        for h in HORIZONS:                                            # 三期間輸入都建完才 push
            cross.push_stock_line2(sid, h, line2_today[h])
        n_stock_unknown += int(any_unknown)

    cross.adv.push_day(T, wc.today_amounts)                           # PIT：後推
    cross.last_date = T
    res.diag = {
        "text_version": text_version,
        "model_version_twse": model_version["twse"], "model_version_tpex": model_version["tpex"],
        "n_market_rows": len(res.market_rows), "n_stocks": n_stocks, "n_in_pool": n_in_pool,
        "n_stock_rows": len(res.stock_rows), "n_stock_any_unknown": n_stock_unknown,
        "n_market_any_unknown": n_market_unknown, "elapsed_ms": round((time.perf_counter() - t0) * 1000.0, 3),
        "index_missing": [m for m in MARKETS if m not in active],
        "rank_pool_size": len(pool_T),
    }
    return res
