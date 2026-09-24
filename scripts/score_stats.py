#!/usr/bin/env python3
"""§16.5 `:712` 合法範圍／`:714` 步驟 4 實測比對／`:716` 分布檢查：讀生產 `scores.db`，一次產出三項報告。

## 判準出處

- `:712`（`spec/stock-iching-plan-v1.2.2.md:712`）：六爻分數最小／最大值，**所有爻分數落在 [7.30, 92.70]、零逸出**
  （容許浮點誤差 ±0.01）。「`N` 只套一次」那一半**不在本檔**（`scores.db` 不存子指標層資料），另案驗。
- `:714` 步驟 4（`:714`）：實測極值必須落在 `docs/score-ranges.md` 對應的登錄區間內（±0.01）；
  甲比甲、乙比乙。
- `:716`（`:716`）：五數綜合、偏態、落在 [45, 55] 的比例、達到可達邊界的比例、相異值數；
  **達邊界比例 > 20% 或相異值數 < 10 須解釋**。
- 裁定 #64（`docs/P3-CALIBRATION.md` §24）：①樣本**限訓練＋驗證段**（2021-01-01～2024-12-31，**寫死、不開參數**）；
  ③分組用**逐爻** `line_k_reweighted`（0＝甲、1＝乙），不用列級 `coverage`。

## 本檔自訂、需寫明的口徑

- **樣本＝該段內 `scores` 表所有列**（大盤列＋個股列，不限 `in_rank_pool`）：檢查的是計分函數的輸出行為，
  不是排名池。池內／池外列數另列在報告裡。
- **「達到可達邊界」**＝與該組登錄區間任一端點的距離 ≤ 0.01（規格的浮點容許）。
- **相異值數**以四捨五入到小數 6 位後計（避免浮點尾數把同一個離散值數成多個）。
- 五數用線性內插分位數（numpy 預設），偏態用母體 Fisher–Pearson（m3 ÷ m2^1.5）；6 位內只有 1 個值的組偏態記 None
  （m2 只剩浮點尾數，算出來是雜訊）。
- 未知爻（分數為 NULL）不進統計，另計筆數。

rc：0 成功／2 中止（守門不過、讀不到檔、任何例外）。三項檢查**不過不算中止**——結果照實寫進報告。
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from array import array
from pathlib import Path
from typing import Any

import numpy as np

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "src"))
sys.path.insert(0, str(REPO / "scripts"))

from iching.config import SEGMENTS  # noqa: E402
from iching.score.transform import S_HI, S_LO  # noqa: E402
from iching.scores_io import ScoreStore  # noqa: E402

REGISTRY = REPO / "data" / "score_ranges.json"
TOL = 0.01                         # 規格 :712／:714 的浮點容許
BAND = (45.0, 55.0)                # :716 中性帶
BOUNDARY_SHARE_MAX = 0.20          # :716 達邊界比例門檻（> 此值須解釋）
DISTINCT_MIN = 10                  # :716 相異值數門檻（< 此值須解釋）
DISTINCT_DECIMALS = 6
#: 裁定 #64 ①：訓練＋驗證段。**寫死**——開參數就不是守門。
SAMPLE_START, SAMPLE_END = SEGMENTS["train"][0], SEGMENTS["valid"][1]
LINES = ("1", "2", "3", "4", "5", "6")


class StatsError(Exception):
    pass


def load_registry(path: Path) -> dict[str, Any]:
    reg = json.loads(path.read_text(encoding="utf-8"))
    if len(reg.get("rows", [])) != 144:
        raise StatsError(f"{path} 不是完整的 144 筆登錄（得 {len(reg.get('rows', []))}）")
    return reg


def _registry_index(reg: dict[str, Any]) -> dict[tuple, tuple[float, float] | None]:
    out = {}
    for r in reg["rows"]:
        key = (r["scope"], r["market"], r["horizon"], r["line"], r["coverage"])
        out[key] = None if r["lo"] is None else (float(r["lo"]), float(r["hi"]))
    return out


def check_versions(store: ScoreStore, dv: str, reg: dict[str, Any]) -> dict[str, str]:
    """db 內每個市場的 model_version 必須與登錄檔產生時相同——否則登錄區間不是這份分數的區間。"""
    have: dict[str, set[str]] = {}
    for v in store.versions():
        if v["data_version"] != dv:
            continue
        for (m,) in store.conn.execute("SELECT DISTINCT market FROM scores WHERE version_id=?", (v["version_id"],)):
            have.setdefault(m, set()).add(v["model_version"])
    if set(have) != set(reg["markets"]):
        raise StatsError(f"scores.db 的市場 {sorted(have)} ≠ 登錄檔 {sorted(reg['markets'])}")
    for m, mvs in have.items():
        want = reg["markets"][m]["model_version"]
        if mvs != {want}:
            raise StatsError(f"{m}：scores.db 的 model_version {sorted(mvs)} ≠ 登錄檔 {want}；登錄區間不是這份分數的區間")
    return {m: next(iter(v)) for m, v in have.items()}


def collect(store: ScoreStore, dv: str, start: str, end: str) -> dict[str, Any]:
    """串流讀 `scores`，依 (scope, market, horizon, line, coverage) 累積爻分數。"""
    cols = ", ".join(f"s.line_{k}, s.line_{k}_reweighted" for k in LINES)
    sql = (f"SELECT s.scope, s.market, s.horizon, s.in_rank_pool, {cols} "
           "FROM scores s JOIN versions v ON v.version_id = s.version_id "
           "WHERE v.data_version = ? AND +s.date >= ? AND +s.date <= ?")
    # `+s.date`：刻意不走 idx_scores_date、依主鍵順序掃描——走索引每列要回主鍵再查一次，
    # 放大 db（980 萬列）熱快取實測 46 秒 → 14 秒（驗收 R2）；冷快取下隨機讀差距更大。
    vals: dict[tuple, array] = {}
    unknown: dict[tuple, int] = {}
    n_rows = {"stock_in_pool": 0, "stock_out_pool": 0, "market_index": 0}
    for row in store.conn.execute(sql, (dv, start, end)):
        scope, m, h, pool = row[0], row[1], row[2], row[3]
        if scope == "market_index":
            n_rows["market_index"] += 1
        else:
            n_rows["stock_in_pool" if pool else "stock_out_pool"] += 1
        for i, k in enumerate(LINES):
            v, rw = row[4 + 2 * i], row[5 + 2 * i]
            if v is None:
                key = (scope, m, h, k)
                unknown[key] = unknown.get(key, 0) + 1
                continue
            if rw not in (0, 1):
                raise StatsError(f"line_{k}_reweighted 不是 0／1（得 {rw!r}）：無法依裁定 #64 ③ 分甲乙")
            key = (scope, m, h, k, "reweighted" if rw else "full")
            vals.setdefault(key, array("d")).append(float(v))
    days = [r[0] for r in store.conn.execute("SELECT date FROM replay_day WHERE data_version=? AND date>=? AND date<=? ORDER BY date",
                                             (dv, start, end))]
    return {"vals": vals, "unknown": unknown, "n_rows": n_rows, "days": days}


def group_stats(a: np.ndarray, reg_lo_hi: tuple[float, float] | None) -> dict[str, Any]:
    n = int(a.size)
    q = np.quantile(a, [0.0, 0.25, 0.5, 0.75, 1.0])
    mean = float(a.mean())
    m2 = float(np.mean((a - mean) ** 2))
    m3 = float(np.mean((a - mean) ** 3))
    distinct = int(np.unique(np.round(a, DISTINCT_DECIMALS)).size)
    # 近乎常數的組（6 位內只有 1 個值）：m2 只剩浮點尾數，偏態是雜訊，記 None（驗收 R1）
    skew = None if distinct <= 1 or m2 == 0 else m3 / m2 ** 1.5
    in_band = int(np.count_nonzero((a >= BAND[0]) & (a <= BAND[1])))
    at_bound = None
    if reg_lo_hi is not None:
        lo, hi = reg_lo_hi
        at_bound = int(np.count_nonzero((np.abs(a - lo) <= TOL) | (np.abs(a - hi) <= TOL)))
    escape = int(np.count_nonzero((a < S_LO - TOL) | (a > S_HI + TOL)))
    return {"n": n, "min": float(q[0]), "q1": float(q[1]), "median": float(q[2]), "q3": float(q[3]), "max": float(q[4]),
            "mean": mean, "skew": skew, "share_45_55": in_band / n,
            "share_at_boundary": None if at_bound is None else at_bound / n, "distinct": distinct, "escape_712": escape}


def analyze(store: ScoreStore, dv: str, reg: dict[str, Any], start: str, end: str) -> dict[str, Any]:
    idx = _registry_index(reg)
    c = collect(store, dv, start, end)
    if not c["days"]:
        raise StatsError(f"{start}～{end} 在 data_version={dv} 下沒有任何落地日")
    groups = []
    for key in sorted(idx):
        scope, m, h, k, cov = key
        arr = c["vals"].get(key)
        g = {"scope": scope, "market": m, "horizon": h, "line": k, "coverage": cov,
             "registry": None if idx[key] is None else list(idx[key])}
        if arr is None or len(arr) == 0:
            g.update({"n": 0})
            groups.append(g)
            continue
        g.update(group_stats(np.frombuffer(arr, dtype="d"), idx[key]))
        reg_lo_hi = idx[key]
        g["within_registry_714"] = (reg_lo_hi is not None and g["min"] >= reg_lo_hi[0] - TOL and g["max"] <= reg_lo_hi[1] + TOL)
        g["explain_716"] = [r for r, bad in (
            ("達邊界比例 > 20%", g["share_at_boundary"] is not None and g["share_at_boundary"] > BOUNDARY_SHARE_MAX),
            ("相異值數 < 10", g["distinct"] < DISTINCT_MIN)) if bad]
        groups.append(g)
    extra = sorted(set(c["vals"]) - set(idx))
    if extra:
        raise StatsError(f"db 有登錄檔沒有的分組 {extra[:3]}…（共 {len(extra)}）")
    obs = [g for g in groups if g["n"]]
    return {
        "schema": 1, "sample": {"start": start, "end": end, "n_days": len(c["days"]),
                                "first_day": c["days"][0], "last_day": c["days"][-1], "rows": c["n_rows"]},
        "tolerance": TOL, "band": list(BAND), "boundary_share_max": BOUNDARY_SHARE_MAX, "distinct_min": DISTINCT_MIN,
        "groups": groups,
        "unknown": [{"scope": s, "market": m, "horizon": h, "line": k, "n": n} for (s, m, h, k), n in sorted(c["unknown"].items())],
        "summary": {
            "groups_total": len(groups), "groups_observed": len(obs),
            "escape_712": sum(g["escape_712"] for g in obs),
            "pass_712": all(g["escape_712"] == 0 for g in obs),
            "violations_714": [_k(g) for g in obs if not g["within_registry_714"]],
            "pass_714": all(g["within_registry_714"] for g in obs),
            "explain_716": [{**_k(g), "reasons": g["explain_716"]} for g in obs if g["explain_716"]],
        },
    }


def _k(g: dict[str, Any]) -> dict[str, str]:
    return {k: g[k] for k in ("scope", "market", "horizon", "line", "coverage")}


def _fmt(v: Any) -> str:
    if v is None:
        return "—"
    if isinstance(v, float):
        return f"{v:.4f}" if math.isfinite(v) else str(v)
    return str(v)


def as_text(rep: dict[str, Any]) -> str:
    s, sm = rep["sample"], rep["summary"]
    L = [f"§16.5 :712／:714 步驟4／:716｜樣本 {s['start']}～{s['end']}（落地 {s['n_days']} 日，{s['first_day']}～{s['last_day']}）",
         f"列數 {s['rows']}｜db {rep.get('db', '')}｜data_version {rep.get('data_version', '')}｜params_sha {rep.get('params_sha', '')}", "",
         f":712 合法範圍：逸出 {sm['escape_712']} 筆 → {'PASS' if sm['pass_712'] else 'FAIL'}",
         f":714 步驟4：{sm['groups_observed']}／{sm['groups_total']} 組有觀測；越界 {len(sm['violations_714'])} 組 → "
         f"{'PASS' if sm['pass_714'] else 'FAIL'}",
         f":716 須解釋 {len(sm['explain_716'])} 組", ""]
    hdr = ("scope", "mkt", "h", "爻", "cov", "n", "min", "q1", "med", "q3", "max", "skew", "[45,55]", "達邊界", "相異", "登錄", "714", "716")
    L.append(" | ".join(hdr))
    for g in rep["groups"]:
        if not g["n"]:
            L.append(f"{g['scope']} | {g['market']} | {g['horizon']} | {g['line']} | {g['coverage']} | 0")
            continue
        reg = "—" if g["registry"] is None else f"[{g['registry'][0]:.4f},{g['registry'][1]:.4f}]"
        L.append(" | ".join([g["scope"], g["market"], g["horizon"], g["line"], g["coverage"], f"{g['n']:,}",
                             *(_fmt(g[k]) for k in ("min", "q1", "median", "q3", "max", "skew", "share_45_55", "share_at_boundary")),
                             str(g["distinct"]), reg, "OK" if g["within_registry_714"] else "越界",
                             "、".join(g["explain_716"]) or "—"]))
    return "\n".join(L) + "\n"


def run(db: Path, registry: Path, out: Path, *, start: str = SAMPLE_START, end: str = SAMPLE_END) -> dict[str, Any]:
    """`start`／`end` 只供測試（合成資料在 2020 年）；CLI 一律用裁定 #64 ① 的寫死值。"""
    import export_dataset as ED
    import export_scores as EX
    reg = load_registry(registry)
    with ScoreStore(db, readonly=True) as store:
        dv = EX.resolve_data_version(store, db.parent, None)
        sha, _ = ED.check_params(store, dv)                      # 不是現行碼算的就拋錯
        check_versions(store, dv, reg)
        rep = analyze(store, dv, reg, start, end)
    rep.update({"db": str(db), "data_version": dv, "params_sha": sha, "registry": str(registry),
                "registry_model_versions": {m: i["model_version"] for m, i in reg["markets"].items()}})
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(rep, ensure_ascii=False, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    out.with_suffix(".txt").write_text(as_text(rep), encoding="utf-8")
    return rep


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="§16.5 :712／:714 步驟4／:716（樣本寫死為訓練＋驗證段，裁定 #64 ①）")
    ap.add_argument("--db", required=True)
    ap.add_argument("--registry", default=str(REGISTRY))
    ap.add_argument("--out", required=True, help="報告 JSON 路徑；同名 .txt 一併寫出")
    args = ap.parse_args(argv)
    try:
        rep = run(Path(args.db), Path(args.registry), Path(args.out))
        sm = rep["summary"]
        print(f"== :712 {'PASS' if sm['pass_712'] else 'FAIL'}｜:714 {'PASS' if sm['pass_714'] else 'FAIL'}｜"
              f":716 須解釋 {len(sm['explain_716'])} 組｜報告 {args.out}")
        return 0
    except Exception as e:  # noqa: BLE001  任何例外一律 rc=2
        print(f"[score_stats 中止] {type(e).__name__}: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
