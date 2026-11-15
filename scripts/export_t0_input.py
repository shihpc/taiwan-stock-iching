#!/usr/bin/env python3
"""P4-L4 T0 校準輸入匯出（Hetzner 執行；docs/P4-PREVIEW.md §24.6／§27）：訓練段 scores.db → `runs/t0/t0_input.json.gz`。

    python3 scripts/export_t0_input.py --cache-dir cache --out . [--data-version DV]

只讀 `cache/scores.db`（`ScoreStore(readonly=True)`），不連網、不需 token、不寫庫。

## 內容

- 範圍＝訓練段 `SEGMENTS["train"]`（2021-01-01～2023-06-30，`export_dataset.py` 同一常數）；暖機（2020）、驗證／保留段不讀。
- 每日 6 個大盤列（市場×期間）的 `flags.basic_state`（S1–S4／undetermined）。
- 每日每（市場、期間）**排名池內**（`in_rank_pool=1`）個股的 `[stock_id, base_score, short_raw]`：
  `short_raw` 由 `src/iching/pick.py` 的 `short_raw`（與每日班 build_pick 同一支）以 `line_2..line_5` 算出，任一爻
  `line_k_unknown=1` 或值缺 → null；`base_score` 缺 → null。代號只供稽核，校準不讀。
- 守門：`params_sha` 必須等於現行碼指紋（`export_dataset.check_params`；非現行碼、非 PIT 池 → rc 2）。

## 輸出決定性

JSON 鍵排序、無空白；gzip `mtime=0`、`compresslevel=9`——同內容同位元組。先寫 `.tmp` 再 `replace`。
"""
from __future__ import annotations

import argparse
import gzip
import json
import os
import sys
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "src"))
sys.path.insert(0, str(REPO / "scripts"))

from export_dataset import SEGMENTS, ExportDatasetError, check_params  # noqa: E402
from export_scores import ExportScoresError, resolve_data_version  # noqa: E402
from iching import pick as P  # noqa: E402
from iching.score.assemble import MARKET_STOCK_ID  # noqa: E402
from iching.scores_io import ScoreStore, ScoreStoreError  # noqa: E402

SCHEMA = 1
OUT_REL = Path("runs") / "t0" / "t0_input.json.gz"


class ExportT0Error(RuntimeError):
    pass


def _num(x: Any) -> float | None:
    if x is None or isinstance(x, bool):
        return None
    try:
        f = float(x)
    except (TypeError, ValueError):
        return None
    return f if f == f and f not in (float("inf"), float("-inf")) else None


def collect(store: ScoreStore, dv: str) -> dict[str, Any]:
    f0, f1 = SEGMENTS["train"]
    states: dict[str, dict[str, Any]] = {}
    rows: dict[str, dict[str, list]] = {}
    mv: dict[str, set[str]] = {}
    sql_m = ("SELECT s.date, s.market, s.horizon, s.flags FROM scores s JOIN versions v ON v.version_id = s.version_id "
             "WHERE v.data_version=? AND s.date>=? AND s.date<=? AND s.stock_id=? ORDER BY s.date, s.market, s.horizon")
    for d, m, h, fl in store.conn.execute(sql_m, (dv, f0, f1, MARKET_STOCK_ID)):
        try:
            st = (json.loads(fl) if isinstance(fl, str) else {}).get("basic_state")
        except ValueError as e:
            raise ExportT0Error(f"{d} {m}|{h} 大盤列 flags 解析失敗：{e}") from e
        states.setdefault(d, {})[f"{m}|{h}"] = st if st in P.STATES else "undetermined"
    lines = ", ".join(f"s.line_{k}, s.line_{k}_unknown" for k in (2, 3, 4, 5))
    sql_s = (f"SELECT s.date, s.market, s.horizon, s.stock_id, s.base_score, {lines}, v.model_version "
             "FROM scores s JOIN versions v ON v.version_id = s.version_id "
             "WHERE v.data_version=? AND s.date>=? AND s.date<=? AND s.stock_id<>? AND s.in_rank_pool=1 "
             "ORDER BY s.date, s.market, s.horizon, s.stock_id")
    for r in store.conn.execute(sql_s, (dv, f0, f1, MARKET_STOCK_ID)):
        d, m, h, sid, bs = r[0], r[1], r[2], str(r[3]), _num(r[4])
        ln = {k: (None if r[6 + 2 * i] == 1 else r[5 + 2 * i]) for i, k in enumerate((2, 3, 4, 5))}
        sr = P.short_raw(ln, h)
        rows.setdefault(d, {}).setdefault(f"{m}|{h}", []).append([sid, bs, sr])
        mv.setdefault(m, set()).add(str(r[13]))
    if not states:
        raise ExportT0Error(f"訓練段 {f0}..{f1} 在 data_version={dv} 沒有任何大盤列")
    bad = [d for d in states if len(states[d]) != len(P.MARKETS) * len(P.HORIZONS)]
    if bad:
        raise ExportT0Error(f"{len(bad)} 個日期的大盤列不是 6 列：{bad[:5]}")
    if any(len(v) != 1 for v in mv.values()):
        raise ExportT0Error(f"訓練段 model_version 不唯一：{ {k: sorted(v) for k, v in mv.items()} }")
    return {"states": states, "rows": rows, "model_version": {k: next(iter(v)) for k, v in sorted(mv.items())},
            "train": [f0, f1]}


def write_gz(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "wb") as f, gzip.GzipFile(fileobj=f, mode="wb", compresslevel=9, mtime=0, filename="") as g:
        g.write(raw)
    os.replace(tmp, path)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="P4-L4：訓練段 T0 校準輸入匯出（docs/P4-PREVIEW.md §27）")
    ap.add_argument("--cache-dir", default="cache")
    ap.add_argument("--out", default=".", help="repo 根（輸出到 <out>/runs/t0/t0_input.json.gz）")
    ap.add_argument("--data-version")
    a = ap.parse_args(argv)
    cache = Path(a.cache_dir)
    try:
        store = ScoreStore(cache / "scores.db", readonly=True)
    except ScoreStoreError as e:
        print(f"!! {e}", file=sys.stderr)
        return 2
    try:
        dv = resolve_data_version(store, cache, a.data_version)
        sha, _ = check_params(store, dv)
        got = collect(store, dv)
    except (ExportScoresError, ExportDatasetError, ExportT0Error) as e:
        print(f"!! {e}", file=sys.stderr)
        return 2
    finally:
        store.conn.close()
    days = sorted(got["states"])
    doc = {"schema": SCHEMA, "params_sha": sha, "data_version": dv, "model_version": got["model_version"],
           "train": got["train"], "days": days, "states": got["states"], "rows": got["rows"],
           "row_cols": ["stock_id", "base_score", "short_raw"]}
    out = Path(a.out) / OUT_REL
    write_gz(out, doc)
    n = sum(len(v) for d in got["rows"].values() for v in d.values())
    print(f"export_t0_input: params_sha={sha} data_version={dv} 訓練段 {days[0]}..{days[-1]} {len(days)} 日 池內列 {n} → {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
