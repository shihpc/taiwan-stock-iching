"""把六爻結果＋版本三元組組成 `scores.db` 每列（`spec/dimensions.json` `targets.scores_db_row`）。

**鍵直接讀 `dimensions.json`、不另抄一份**（CLAUDE.md 約定 3：凡「per X」的鍵宣告一律以 dimensions.json 為唯一事實來源）。
dimensions.json 加一個鍵而呼叫端沒提供 → `assemble_row` 直接拋 `KeyError`（不會靜默留空）。
大盤列 `stock_id='__MARKET__'`（dimensions.json `stock_id` note）。
"""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

from .aggregate import LineResult
from .hexagram import SPEC_DIR, king_wen_from_lines, lines_from_scores, name_of
from .market import MarketScores
from .stock import StockScores
from .transform import Missing

DIMENSIONS_PATH = SPEC_DIR / "dimensions.json"
MARKET_STOCK_ID = "__MARKET__"
LINE_KEYS = ("1", "2", "3", "4", "5", "6")


@lru_cache(maxsize=4)
def scores_db_row_keys(path: str | None = None) -> tuple[str, ...]:
    p = Path(path) if path else DIMENSIONS_PATH
    d = json.loads(p.read_text(encoding="utf-8"))
    keys = d["targets"]["scores_db_row"]["key"]
    if not keys:
        raise ValueError("dimensions.json targets.scores_db_row.key is empty")
    return tuple(keys)


def _jsonable(v: Any) -> Any:
    if isinstance(v, Missing):
        return {"missing": v.reason, "detail": v.detail}
    if isinstance(v, float):
        return float(v)
    return v


def _line_payload(prefix: str, lr: LineResult, detail: bool) -> dict:
    out = {
        f"{prefix}": lr.score,
        f"{prefix}_unknown": lr.unknown,
        f"{prefix}_coverage_ratio": lr.coverage_ratio,
        f"{prefix}_reweighted": lr.reweighted,
    }
    if detail:
        fams = {}
        for f in lr.families:
            fams[f.family] = {
                "score": f.score,
                "missing": _jsonable(f.missing) if f.missing else None,
                "reweighted": f.reweighted,
                "subs": {s.indicator_id: {"score": s.score, "native": s.native, "x": s.x, "clipped": s.clipped,
                                          "missing": _jsonable(s.missing) if s.missing else None, "sub_weight": s.sub_weight,
                                          "meta": s.meta} for s in f.subs},
                "meta": f.meta,
            }
        out[f"{prefix}_families"] = fams
        out[f"{prefix}_meta"] = lr.meta
    return out


def assemble_row(scores: MarketScores | StockScores, model_version: str, data_version: str, text_version: str,
                 *, flags: dict | None = None, formal_lines: list[int] | None = None,
                 extra_keys: dict[str, Any] | None = None, detail: bool = False,
                 dimensions_path: str | None = None) -> dict:
    """組一列。鍵的**名單**來自 dimensions.json；可由分數物件導出的鍵自動填，其餘須由 `extra_keys` 提供，
    缺任一鍵即 `KeyError`。"""
    is_market = isinstance(scores, MarketScores)
    derivable = {
        "market": scores.market,
        "horizon": scores.horizon,
        "stock_id": MARKET_STOCK_ID if is_market else scores.stock_id,
        "tpe_trading_date": scores.tpe_date,
        "model_version": model_version,
        "data_version": data_version,
        "text_version": text_version,
    }
    supplied = {**derivable, **(extra_keys or {})}
    keys = scores_db_row_keys(dimensions_path)
    missing = [k for k in keys if k not in supplied]
    if missing:
        raise KeyError(f"scores_db_row 鍵 {missing} 未提供（dimensions.json 宣告 {list(keys)}）")
    row: dict[str, Any] = {k: supplied[k] for k in keys}
    row["scope"] = "market_index" if is_market else "stock"
    for k in LINE_KEYS:
        row.update(_line_payload(f"line_{k}", scores.lines[k], detail))
    line_scores = scores.line_scores()
    prov = lines_from_scores(line_scores)
    row["lines_provisional"] = prov
    row["king_wen_provisional"] = king_wen_from_lines(prov) if prov else None
    row["hexagram_name_provisional"] = name_of(row["king_wen_provisional"]) if prov else "待補"
    if formal_lines is not None:
        if len(formal_lines) != 6 or any(b not in (0, 1) for b in formal_lines):
            raise ValueError("formal_lines must be 6 bits")
        row["lines_formal"] = list(formal_lines)
        row["king_wen"] = king_wen_from_lines(formal_lines)
        row["hexagram_name"] = name_of(row["king_wen"])
    else:
        row["lines_formal"] = None
        row["king_wen"] = None
        row["hexagram_name"] = None
    row["base_score"] = _jsonable(scores.direction_score)
    row["inner_trigram_score"] = _jsonable(scores.inner_trigram_score)
    row["outer_trigram_score"] = _jsonable(scores.outer_trigram_score)
    row["coverage"] = scores.coverage
    row["calibrated"] = False
    if flags is not None:
        row["flags"] = flags
    return row


def row_key(row: dict, dimensions_path: str | None = None) -> tuple:
    return tuple(row[k] for k in scores_db_row_keys(dimensions_path))
