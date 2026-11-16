#!/usr/bin/env python3
"""P4-L4 方向門檻 T0 校準（`spec/P1-B1-market.md:101` 第 10 點；docs/P4-PREVIEW.md §24.2／§24.6／§27）。

    python3 scripts/calib_t0.py --input runs/t0/t0_input.json.gz --root . [--expect-params-sha SHA]

讀 `export_t0_input.py` 的訓練段匯出 → 寫 `data/pick/t0.json`（`build_pick.load_t0`／`pick.threshold_from_t0` 讀取端）
＋報告 `runs/t0/report.md`。純計算、不連網。

## 規則（逐條照 B1 第 10 點；括號內為本實作的落地方式）

1. 48 格＝市場 × 期間 × 基本狀態（S1–S4）× 方向。格的母體＝**該（市場、期間）大盤列 basic_state 等於該狀態的訓練段交易日**。
2. 條件分布＝母體日所有池內有效分數合併；做多用 `base_score`，做空用**扣分前 `short_raw`**（使用者決定，§24.2）。
3. 單一分位數 `q` 套 48 格：門檻 `T(q)`＝該格條件分布的第 q 百分位（numpy 內插慣例＝`Rules.pct_interp`，現行 linear）。
4. 可行：每格「通過數（分數 ≥ T(q)）≥ 該格 N0」的交易日佔母體日 ≥ 80%。取全部有母體的格皆可行的**最大 q＝q***
   （格點 0.01；通過數對 q 單調不增，二分搜尋後再以 q*、q*+0.01 兩點直接驗證）。
5. `q* = 0` 仍不可行 → 判定 N0 過大，**不寫 t0.json**（rc 3），報告列出卡住的格。
6. 位移門檻：`min(q* + {0,5,10,15,20}, 99)` 百分位對應分數（位移 0.5／1／1.5／2 個十分位 → +5／+10／+15／+20 百分位點）；
   若 q* 本身已 > 99，位移後的分位數取 `max(q*, …)`——夾 99 只為不超過 100，不得反過來比無位移時還寬鬆（主對話設計）。
7. 某狀態在訓練段從未出現的格＝`no_population`：不參與可行判定、門檻寫 null（該格當日不出候選），報告明列。

只用訓練段：輸入檔 `train` 必須等於 `export_dataset.SEGMENTS["train"]`，且所有日期落在其內，否則 rc 2。
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import math
import os
import sys
from bisect import bisect_left
from pathlib import Path
from typing import Any

import numpy as np

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "src"))

from iching import pick as P  # noqa: E402
from iching.score.params import Rules  # noqa: E402

SCHEMA = 1
TRAIN = ("2021-01-01", "2023-06-30")      # ＝ scripts/export_dataset.py SEGMENTS["train"]（tests 交叉比對）
OCCUPANCY = 0.80
GRID = 100                                 # q 以 1/100 百分位點為格（整數索引 0..9999 → q＝i/100）
Q_MAX_IDX = 9999
SHIFTS = (0, 5, 10, 15, 20)
Q_CAP = 99.0
PCT_METHOD = Rules().pct_interp            # 百分位內插慣例沿 Rules（現行 "linear"）；改 Rules 會改 params_sha、守門會擋舊輸入


class CalibT0Error(RuntimeError):
    pass


def cell_key(m: str, h: str, s: str, d: str) -> str:
    return f"{m}|{h}|{s}|{d}"


def build_cells(doc: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """{cell_key: {"days": [每日排序後分數陣列], "pooled": 排序後合併陣列, "n0": int}}；無母體格 days=[]。"""
    cells: dict[str, dict[str, Any]] = {}
    for m in P.MARKETS:
        for h in P.HORIZONS:
            for s in P.STATES:
                for di, d in enumerate(P.DIRECTIONS):
                    cells[cell_key(m, h, s, d)] = {"days": [], "n0": P.N0_TABLE[(s, d)], "col": di}   # row＝[base_score, short_raw]
    for day in doc["days"]:
        st = doc["states"][day]
        for m in P.MARKETS:
            for h in P.HORIZONS:
                s = st.get(f"{m}|{h}")
                if s not in P.STATES:
                    continue
                rows = (doc["rows"].get(day) or {}).get(f"{m}|{h}") or []
                for d in P.DIRECTIONS:
                    c = cells[cell_key(m, h, s, d)]
                    v = np.sort(np.asarray([r[c["col"]] for r in rows if isinstance(r[c["col"]], (int, float))
                                            and not isinstance(r[c["col"]], bool) and math.isfinite(r[c["col"]])], dtype=float))
                    c["days"].append(v)
    for c in cells.values():
        c["pooled"] = np.sort(np.concatenate(c["days"])) if c["days"] else np.array([])
    return cells


def threshold(pooled: np.ndarray, q: float) -> float | None:
    return float(np.percentile(pooled, q, method=PCT_METHOD)) if pooled.size else None


def occupancy(c: dict[str, Any], q: float) -> float | None:
    """母體日中「通過數 ≥ N0」的比例；無母體 → None。"""
    if not c["days"]:
        return None
    t = threshold(c["pooled"], q)
    if t is None:          # 有母體日但全無有效分數 → 通過數恆 0
        return 1.0 if c["n0"] <= 0 else 0.0
    ok = sum(1 for v in c["days"] if v.size - bisect_left(v, t) >= c["n0"])
    return ok / len(c["days"])


def feasible(cells: dict[str, dict[str, Any]], q: float) -> bool:
    return all(o is None or o >= OCCUPANCY for o in (occupancy(c, q) for c in cells.values()))


def find_q_star(cells: dict[str, dict[str, Any]]) -> float | None:
    """可行集＝[0, q*]（單調）；二分找最大可行索引。0 不可行 → None。"""
    if not feasible(cells, 0.0):
        return None
    lo, hi = 0, Q_MAX_IDX
    if feasible(cells, hi / GRID):
        return hi / GRID
    while hi - lo > 1:
        mid = (lo + hi) // 2
        if feasible(cells, mid / GRID):
            lo = mid
        else:
            hi = mid
    return lo / GRID


def validate(doc: Any) -> None:
    if not isinstance(doc, dict) or doc.get("schema") != 1:
        raise CalibT0Error("輸入檔 schema 不是 1")
    if tuple(doc.get("train") or ()) != TRAIN:
        raise CalibT0Error(f"輸入檔 train={doc.get('train')} 不是訓練段 {TRAIN}")
    days = doc.get("days")
    if not isinstance(days, list) or not days:
        raise CalibT0Error("輸入檔沒有日期")
    out = [d for d in days if not (TRAIN[0] <= str(d) <= TRAIN[1])]
    if out:
        raise CalibT0Error(f"輸入檔含訓練段外日期 {len(out)} 個：{out[:5]}")
    if doc.get("row_cols") != ["base_score", "short_raw"]:
        raise CalibT0Error(f"輸入檔 row_cols={doc.get('row_cols')} 不是 ['base_score', 'short_raw']（舊格式或錯檔）")
    for k in ("states", "rows"):
        if not isinstance(doc.get(k), dict):
            raise CalibT0Error(f"輸入檔 {k} 形狀不對")
    if set(doc["rows"]) - set(days) or set(doc["states"]) != set(days):
        raise CalibT0Error("輸入檔 states／rows 的日期與 days 不一致")


def calibrate(doc: dict[str, Any]) -> dict[str, Any]:
    validate(doc)
    cells = build_cells(doc)
    q_star = find_q_star(cells)
    res: dict[str, Any] = {"q_star": q_star, "cells": {}}
    for k, c in sorted(cells.items()):
        m, h, s, d = k.split("|")
        e: dict[str, Any] = {"market": m, "horizon": h, "state": s, "direction": d, "n0": c["n0"],
                             "n_days": len(c["days"]), "n_obs": int(c["pooled"].size)}
        if not c["days"]:
            e.update({"no_population": True, "thresholds": {str(x): None for x in SHIFTS}})
        elif q_star is not None:
            qu = {str(x): max(q_star, min(q_star + x, Q_CAP)) for x in SHIFTS}
            e["thresholds"] = {k: threshold(c["pooled"], v) for k, v in qu.items()}
            e["q_used"] = qu
            e["occupancy_at_q_star"] = occupancy(c, q_star)
            nxt = min(q_star + 1 / GRID, Q_MAX_IDX / GRID)
            e["occupancy_at_next"] = occupancy(c, nxt) if q_star < Q_MAX_IDX / GRID else None
        else:
            e["occupancy_at_0"] = occupancy(c, 0.0)
        res["cells"][k] = e
    if q_star is not None and q_star < Q_MAX_IDX / GRID:
        res["next_feasible"] = feasible(cells, q_star + 1 / GRID)   # 必為 False（最大性的直接驗證）
    return res


def dumps(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"


def report_md(doc: dict[str, Any], res: dict[str, Any], src_sha: str) -> str:
    L = ["# P4-L4 T0 校準報告", "",
         f"- 輸入：`runs/t0/t0_input.json.gz` sha256 `{src_sha[:16]}`；params_sha `{doc['params_sha']}`；data_version `{doc.get('data_version')}`",
         f"- 訓練段 {doc['train'][0]}～{doc['train'][1]}，實際 {len(doc['days'])} 個交易日（{doc['days'][0]}～{doc['days'][-1]}）",
         f"- 規則：B1 第 10 點；每格 ≥ {OCCUPANCY:.0%} 母體日通過數 ≥ N0；q 格點 0.01；做空格用扣分前 short_raw（使用者決定）",
         f"- **q\\* = {res['q_star']}**" + ("" if res["q_star"] is None else f"；q\\*+0.01 可行＝{res.get('next_feasible')}"), "",
         "| 格（市場｜期間｜狀態｜方向） | N0 | 母體日 | 樣本數 | q* 達成率 | q*+0.01 達成率 | T(q*) | T(+5) | T(+10) | T(+15) | T(+20) |",
         "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|"]
    f = lambda v: "—" if v is None else (f"{v:.3f}" if isinstance(v, float) else str(v))  # noqa: E731
    for k, e in sorted(res["cells"].items()):
        th = e.get("thresholds") or {}
        L.append(f"| {k} | {e['n0']} | {e['n_days']} | {e['n_obs']} | {f(e.get('occupancy_at_q_star'))} | "
                 f"{f(e.get('occupancy_at_next'))} | " + " | ".join(f(th.get(str(x))) for x in SHIFTS) + " |")
    und = {f"{m}|{h}": sum(1 for d in doc["days"] if doc["states"][d].get(f"{m}|{h}") not in P.STATES)
           for m in P.MARKETS for h in P.HORIZONS}
    L += ["", "- 基本狀態未判定（undetermined，不進任何格）的日數：" + "、".join(f"{k} {v}" for k, v in und.items())]
    nop = [k for k, e in res["cells"].items() if e.get("no_population")]
    L += ["", f"- 無母體（該狀態訓練段未出現）的格：{len(nop)} 個" + (f"：{', '.join(sorted(nop))}" if nop else "")]
    if res["q_star"] is None:
        bad = [k for k, e in res["cells"].items() if e.get("occupancy_at_0") is not None and e["occupancy_at_0"] < OCCUPANCY]
        L += [f"- **q=0 仍不可行**，N0 過大的格：{', '.join(sorted(bad))}；依 B1 第 10 點應回頭調 N0，未寫 t0.json。"]
    return "\n".join(L) + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="P4-L4：方向門檻 T0 校準（docs/P4-PREVIEW.md §27）")
    ap.add_argument("--input", default="runs/t0/t0_input.json.gz")
    ap.add_argument("--root", default=".")
    ap.add_argument("--expect-params-sha", help="要求輸入檔 params_sha 等於此值（預設比對 data/state/cross.json 的 meta.params_sha）")
    a = ap.parse_args(argv)
    root = Path(a.root)
    p = Path(a.input)
    try:
        raw = p.read_bytes()
        doc = json.loads(gzip.decompress(raw))
        want = a.expect_params_sha
        if want is None:
            want = ((json.loads((root / "data" / "state" / "cross.json").read_text(encoding="utf-8")).get("meta") or {})
                    .get("params_sha"))                                           # cross.json 的指紋在 meta 下（每日班狀態檔格式）
            if not want:
                raise CalibT0Error("data/state/cross.json 讀不到 meta.params_sha（請以 --expect-params-sha 指定）")
        if doc.get("params_sha") != want:
            raise CalibT0Error(f"輸入檔 params_sha={doc.get('params_sha')} ≠ 現行 {want}")
        res = calibrate(doc)
    except (OSError, ValueError, KeyError, CalibT0Error) as e:
        print(f"!! calib_t0: {e}", file=sys.stderr)
        return 2
    src_sha = hashlib.sha256(raw).hexdigest()
    rep = root / "runs" / "t0" / "report.md"
    rep.parent.mkdir(parents=True, exist_ok=True)
    rep.write_text(report_md(doc, res, src_sha), encoding="utf-8")
    if res["q_star"] is None:
        print(f"!! calib_t0: q=0 仍不可行（N0 過大），未寫 t0.json；見 {rep}", file=sys.stderr)
        return 3
    q = res["q_star"]
    t0 = {"schema": SCHEMA, "version": f"q{q:.2f}-{doc['params_sha']}", "calibrated": False, "params_sha": doc["params_sha"],
          "model_version": doc.get("model_version"), "data_version": doc.get("data_version"), "train": list(TRAIN),
          "train_days": [doc["days"][0], doc["days"][-1], len(doc["days"])], "rule": "spec/P1-B1-market.md:101 第 10 點",
          "occupancy": OCCUPANCY, "grid": 1 / GRID, "q_star": q, "q_cap": Q_CAP, "source_sha256": src_sha,
          "cells": {k: {kk: vv for kk, vv in e.items() if kk not in ("market", "horizon", "state", "direction")}
                    for k, e in res["cells"].items()}}
    out = root / "data" / "pick" / "t0.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_name(out.name + ".tmp")
    tmp.write_text(dumps(t0), encoding="utf-8")
    os.replace(tmp, out)
    print(f"calib_t0: q*={q} 格數 {len(res['cells'])}（無母體 {sum(1 for e in res['cells'].values() if e.get('no_population'))}）→ {out}、{rep}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
