#!/usr/bin/env python3
"""c／d 校準報告（P3 第 3 項第 1 步，`docs/P3-CALIBRATION.md` §2；裁定 #54）。**只讀 x dump、只出報告，不動 `params.py`。**

讀 `replay_scores.py --dump-x` 的目錄（`<dir>/<鍵>.f32`＋`manifest.json`，見 `iching.xdump`），對每個鍵算：

- `p85`＝訓練段樣本 `|x − c|` 的第 85 百分位（`numpy.percentile(..., 85, method=<--method，預設 linear>)`；
  `spec/P1-B1-market.md:42`＝`P1-B2-params.md:56` 第 3 點）、`d_new = p85 ÷ 3`、`d_old`＝現行 `ParamSet` 的 d
- 舊／新 d 下的**截斷比例**＝`|x − c| > 3d` 的樣本佔比（v1.2.2 §4.1a 的截斷邊界 `c ± 3d`）
- `median(x)`＋旗標 `|median − c| > d_new`（裁定 #54 Q2：c 不動、離很遠的列報告供人看，不自動改）
- **分類**（裁定 #54 Q3／Q4；每鍵各自、兩市場各自、三期間各自——Q5）：
  - `calibrate`：`clip_policy=clip_3d` 且非下兩類（含 `transform=S` 與 `margin_scenario` 這種 scenario＋clip_3d；斜率族雖共用
    `market_slope_d`／`stock_slope_d` 查表，但每個視窗 n 只被一個期間引用，逐鍵校準不衝突，列出 `shared_d_table` 供對照）
  - `persistence`：持續性族（`foreign_persistence`＝B2.5 族 C，規格 5a 點名；`foreign_buy_days`＝B1.4 族 C，同性質、規格未點名，
    **歸類待裁定**）——**不校準**，報 `d_formula＝原始值域上界 ÷ 3`（上界＝視窗 n ÷ 2）與現行 d 對照
  - `distance`：`(X − MA_n)/ATR14` 距離型，d 共用 `ParamSet.distance_d[n]`（`dist_ma_short`／`dist_ma_long`／`spx_ma_distance`）——
    報 p85 與查表值，逐鍵 `|d_new − d_old| / d_old > --distance-tol`（0.25）標 `adopt_p85`；另出**同一查表格（market × n）合併樣本**的 p85
    （多鍵共用一格 d，逐鍵 p85 不一致時要人裁定取哪個，本報告兩者都給、不替人選）
  - `not_applicable`：`clip_policy=n/a`（L／P_hist／scenario／passthrough），只列筆數
- **閘門**（`P1-B1-market.md:43`）：生效 d（calibrate→d_new；distance→adopt 則 d_new 否則 d_old；persistence→d_old）下截斷比例
  > `--gate`（15%）的鍵印成清單並寫進 `gate_failures`。**訓練段超標＝d 過小必須重定**；本工具只報不改。
  **離散化餘裕（實作決定，待裁定）**：`d_new = p85 ÷ 3` 之下，訓練段 `|x−c| > 3d_new` 的比例由構造即為 15% 左右——linear 內插的
  p85 落在兩個樣本之間，嚴格大於它的樣本數是 `floor(0.15·(n−1))` 到 `ceil` 之間，比例可到 **15% ＋ 1/n**（n=603 的大盤鍵＝15.09%）。
  按「> 15%」嚴格判會讓校準後的鍵**系統性**超標零點幾個百分點，那不是「d 過小」。故 `gate_fail`＝`clip_eff% > gate + 100/n`
  （放一個樣本的餘裕），另存 `gate_fail_strict`（`> gate`，不放餘裕）供對照；兩個清單都寫進報告。
- **退化**：`p85 = 0`（訓練段 ≥85% 樣本恰等於 c，`d_new=0` 不是合法 d）的鍵列 `degenerate`，不給 d_eff、不算閘門——要人看。

輸出 `runs/calib/d_report_<dump_to>.json`＋`.txt`（人讀表）。rc：0＝報告已寫；2＝dump 目錄／manifest 缺、manifest 的
`params_sha` ≠ 現行碼指紋（`export_dataset.expected_params_sha`，同 `hetzner_adj.sh` 守門 b）、`.f32` 筆數與 manifest 不符。
閘門超標**不改 rc**（那是裁定 d 的輸入，不是工具失敗）。

只用標準庫＋numpy（Hetzner Python 3.14 無 pandas）。
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from pathlib import Path
from typing import Any

import numpy as np

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "src"))
sys.path.insert(0, str(REPO / "scripts"))

from export_dataset import expected_params_sha  # noqa: E402
from iching.xdump import X_KIND_ROLLING, XDumpError, load_manifest  # noqa: E402

PERCENTILE = 85.0
CAT_CALIBRATE, CAT_PERSISTENCE, CAT_DISTANCE, CAT_NA = "calibrate", "persistence", "distance", "not_applicable"
# 持續性族：值域有界、`3d ≥ 上界` 使截斷永不觸發（spec 5a）；上界＝視窗 n ÷ 2（「買超天數 − n/2」）
PERSISTENCE_IDS: dict[str, str] = {
    "foreign_persistence": "B2.5 族 C（規格 P1-B2-params.md:62 第 5a 點點名：不套 p85，d＝值域上界÷3）",
    "foreign_buy_days": "B1.4 族 C（同性質：近 10 日買超天數−5、3d=6≥5 截斷不觸發；規格 5a 未點名，歸類待裁定）",
}


class CalibrateError(RuntimeError):
    pass


def classify(snap: dict[str, Any]) -> str:
    if snap.get("clip_policy") != "clip_3d":
        return CAT_NA
    if snap["indicator_id"] in PERSISTENCE_IDS:
        return CAT_PERSISTENCE
    if snap.get("shared_d_table") == "distance_d":
        return CAT_DISTANCE
    return CAT_CALIBRATE


def c_used(snap: dict[str, Any]) -> float:
    """`|x − c|` 用的 c：`basis` 的檔已是 `x − 滾動 c`（x_kind），以 0 計；其餘取 Param.c。"""
    if snap.get("x_kind") == X_KIND_ROLLING:
        return 0.0
    c = snap.get("c")
    if c is None:
        raise CalibrateError(f"{snap['indicator_id']}：clip_3d 但 c 為 None 且不是 rolling-c 鍵")
    return float(c)


def load_x(dump_dir: Path, snap: dict[str, Any]) -> np.ndarray:
    f = dump_dir / str(snap["file"])
    if not f.is_file():
        raise CalibrateError(f"manifest 列了 {f.name} 但檔案不存在")
    arr = np.fromfile(f, dtype="<f4").astype(np.float64)
    if arr.size != int(snap["n"]):
        raise CalibrateError(f"{f.name}：檔內 {arr.size} 筆 ≠ manifest n={snap['n']}（dump 沒跑完或被 append 過）")
    return arr


def stats_for(x: np.ndarray, c: float, d_old: float | None, method: str) -> dict[str, Any]:
    dev = np.abs(x - c)
    p85 = float(np.percentile(dev, PERCENTILE, method=method))
    d_new = p85 / 3.0
    med = float(np.median(x))
    out = {"p85": p85, "d_new": d_new, "median_x": med, "clip_new_pct": _clip_pct(dev, d_new),
           "median_flag": bool(abs(med - c) > d_new), "x_min": float(x.min()), "x_max": float(x.max())}
    out["clip_old_pct"] = _clip_pct(dev, d_old) if d_old is not None else None
    return out


def _clip_pct(dev: np.ndarray, d: float | None) -> float | None:
    if d is None or d <= 0:
        return None
    return float(np.mean(dev > 3.0 * d) * 100.0)


def build_rows(dump_dir: Path, m: dict[str, Any], *, method: str, gate_pct: float, dist_tol: float) -> tuple[list[dict], list[dict]]:
    rows: list[dict[str, Any]] = []
    pooled: dict[tuple[str, int], list[np.ndarray]] = {}
    pooled_scope: dict[tuple[str, str, int], list[np.ndarray]] = {}
    for key in sorted(m["keys"]):
        snap = m["keys"][key]
        cat = classify(snap)
        row: dict[str, Any] = {"key": key, "category": cat, "n": int(snap["n"]), "skipped": int(snap["skipped"]),
                               "date_min": snap["date_min"], "date_max": snap["date_max"], "x_kind": snap["x_kind"]}
        for f in ("scope", "market", "horizon", "line", "family", "indicator_id", "transform", "clip_policy", "direction", "unit",
                  "window", "shared_d_table", "shared_d_n", "shared_d_value"):
            row[f] = snap.get(f)
        row["d_old"] = snap.get("d")
        row["c"] = None if cat == CAT_NA else c_used(snap)
        row["persistence_note"] = PERSISTENCE_IDS.get(snap["indicator_id"]) if cat == CAT_PERSISTENCE else None
        if cat == CAT_PERSISTENCE:
            n_win = int(snap["window"]) if not isinstance(snap["window"], list) else int(snap["window"][0])
            row["range_upper"] = n_win / 2.0
            row["d_formula"] = row["range_upper"] / 3.0
            row["d_formula_matches_old"] = bool(snap.get("d") is not None and abs(float(snap["d"]) - row["d_formula"]) < 1e-9)
        if row["n"] == 0 or cat == CAT_NA:
            row.update({"p85": None, "d_new": None, "clip_old_pct": None, "clip_new_pct": None, "median_x": None, "median_flag": None,
                        "d_eff": None, "clip_eff_pct": None, "adopt_p85": None, "gate_fail": False, "gate_fail_strict": False,
                        "gate_slack_pct": None, "degenerate": False})
            rows.append(row)
            continue
        x = load_x(dump_dir, snap)
        d_old = float(snap["d"]) if snap.get("d") is not None else None
        st = stats_for(x, row["c"], d_old, method)
        row.update(st)
        dev = np.abs(x - row["c"])
        if cat == CAT_CALIBRATE:
            row["adopt_p85"] = None
            row["d_eff"] = st["d_new"]
        elif cat == CAT_DISTANCE:
            adopt = d_old is None or abs(st["d_new"] - d_old) / d_old > dist_tol
            row["adopt_p85"] = bool(adopt)
            row["d_eff"] = st["d_new"] if adopt else d_old
            pooled.setdefault((snap["market"], int(snap["shared_d_n"])), []).append(dev)
            pooled_scope.setdefault((snap["market"], snap["scope"], int(snap["shared_d_n"])), []).append(dev)
        else:  # persistence：不校準，d 維持現值
            row["adopt_p85"] = None
            row["d_eff"] = d_old
        row["degenerate"] = bool(st["p85"] <= 0.0)
        if row["degenerate"] and cat != CAT_PERSISTENCE and not (cat == CAT_DISTANCE and not row["adopt_p85"]):
            row["d_eff"] = None                                           # d_new=0 不是合法 d，不算閘門
        row["clip_eff_pct"] = _clip_pct(dev, row["d_eff"])
        row["gate_slack_pct"] = 100.0 / x.size
        row["gate_fail_strict"] = bool(row["clip_eff_pct"] is not None and row["clip_eff_pct"] > gate_pct)
        row["gate_fail"] = bool(row["clip_eff_pct"] is not None and row["clip_eff_pct"] > gate_pct + row["gate_slack_pct"])
        rows.append(row)
    dist_rows: list[dict[str, Any]] = []
    tables = m.get("tables", {})
    for (mk, n), parts in sorted(pooled.items()):
        dev = np.concatenate(parts)
        p85 = float(np.percentile(dev, PERCENTILE, method=method))
        table_d = tables.get(mk, {}).get("distance_d", {}).get(str(n))
        table_d = float(table_d) if table_d is not None else None
        dist_rows.append({"market": mk, "scope": "all", "n": n, "n_samples": int(dev.size), "n_keys": len(parts), "p85": p85, "d_new": p85 / 3.0,
                          "d_table": table_d, "diff_pct": (abs(p85 / 3.0 - table_d) / table_d * 100.0) if table_d else None,
                          "adopt_p85": bool(table_d is None or abs(p85 / 3.0 - table_d) / table_d > dist_tol),
                          "clip_table_pct": _clip_pct(dev, table_d), "clip_new_pct": _clip_pct(dev, p85 / 3.0)})
    for (mk, sc, n), parts in sorted(pooled_scope.items()):
        dev = np.concatenate(parts)
        p85 = float(np.percentile(dev, PERCENTILE, method=method))
        table_d = tables.get(mk, {}).get("distance_d", {}).get(str(n))
        table_d = float(table_d) if table_d is not None else None
        dist_rows.append({"market": mk, "scope": sc, "n": n, "n_samples": int(dev.size), "n_keys": len(parts), "p85": p85, "d_new": p85 / 3.0,
                          "d_table": table_d, "diff_pct": (abs(p85 / 3.0 - table_d) / table_d * 100.0) if table_d else None,
                          "adopt_p85": bool(table_d is None or abs(p85 / 3.0 - table_d) / table_d > dist_tol),
                          "clip_table_pct": _clip_pct(dev, table_d), "clip_new_pct": _clip_pct(dev, p85 / 3.0)})
    return rows, dist_rows


def _f(v: Any, w: int = 9, nd: int = 4) -> str:
    if v is None:
        return "—".rjust(w)
    if isinstance(v, bool):
        return ("Y" if v else "").rjust(w)
    if isinstance(v, float):
        return f"{v:{w}.{nd}f}"
    return str(v).rjust(w)


def render_txt(rep: dict[str, Any]) -> str:
    L: list[str] = []
    L.append(f"# d 校準報告  dump {rep['dump_from']}～{rep['dump_to']}（{rep['days_dumped']} 日） data_version={rep['data_version']} "
             f"params_sha={rep['params_sha']}  p85 method={rep['method']}  閘門 {rep['gate_pct']}%  距離型容差 {rep['distance_tol'] * 100:.0f}%")
    L.append(f"# 鍵數：{rep['counts']}；有樣本的鍵 {rep['n_keys_with_data']}／{len(rep['rows'])}；樣本合計 {rep['n_values']:,}、跳過 {rep['n_skipped']:,}")
    L.append("# clip_old%／clip_new%＝舊 d／新 d（p85÷3）下 |x−c|>3d 的比例；clip_eff%＝生效 d（見檔頭）下的比例；med!＝|median−c|>d_new；"
             "adopt＝距離型 |d_new−d_old|/d_old 超容差；gate＝clip_eff% > 閘門＋100/n（一個樣本的離散化餘裕；嚴格版見下方清單）；p85=0 者 d_eff 留空＝退化")
    hdr = f"{'category':<12} {'key':<58} {'n':>8} {'c':>7} {'d_old':>8} {'p85':>9} {'d_new':>9} {'clipO%':>7} {'clipN%':>7} {'d_eff':>8} {'clipE%':>7} {'median':>10} med! adopt gate"
    L.append(hdr)
    for cat in (CAT_CALIBRATE, CAT_DISTANCE, CAT_PERSISTENCE, CAT_NA):
        for r in rep["rows"]:
            if r["category"] != cat:
                continue
            L.append(f"{r['category']:<12} {r['key']:<58} {r['n']:>8} {_f(r['c'], 7, 2)} {_f(r['d_old'], 8)} {_f(r['p85'])} {_f(r['d_new'])} "
                     f"{_f(r['clip_old_pct'], 7, 2)} {_f(r['clip_new_pct'], 7, 2)} {_f(r['d_eff'], 8)} {_f(r['clip_eff_pct'], 7, 2)} "
                     f"{_f(r['median_x'], 10)} {_f(r['median_flag'], 4)} {_f(r['adopt_p85'], 5)} {_f(r['gate_fail'], 4)}")
    L.append("")
    L.append(f"## 閘門：生效 d 下截斷比例 > {rep['gate_pct']}%＋100/n 的鍵（{len(rep['gate_failures'])} 個；訓練段超標＝d 過小、必須重定）")
    L.extend(f"  {k}" for k in rep["gate_failures"]) if rep["gate_failures"] else L.append("  （無）")
    strict_only = [k for k in rep["gate_failures_strict"] if k not in rep["gate_failures"]]
    L.append(f"## 嚴格版（> {rep['gate_pct']}%、不放餘裕）另多出 {len(strict_only)} 個：{strict_only or '（無）'}")
    L.append("")
    L.append(f"## 退化：p85 = 0（≥85% 樣本恰等於 c，d_new=0 非法）的鍵（{len(rep['degenerate'])} 個；要人看）")
    L.extend(f"  {k}" for k in rep["degenerate"]) if rep["degenerate"] else L.append("  （無）")
    L.append("")
    L.append(f"## |median − c| > d_new 的鍵（{len(rep['median_flags'])} 個；裁定 Q2：只列不改）")
    L.extend(f"  {k}" for k in rep["median_flags"]) if rep["median_flags"] else L.append("  （無）")
    L.append("")
    L.append("## 距離型查表覆核（同一格 distance_d[n] 的合併樣本；all＝大盤＋個股＋SPX 三處合併、另列各 scope）")
    L.append(f"{'market':<6} {'scope':<13} {'n':>3} {'keys':>4} {'samples':>10} {'p85':>9} {'d_new':>9} {'d_table':>8} {'diff%':>7} {'clipT%':>7} {'clipN%':>7} adopt")
    for r in rep["distance_pooled"]:
        L.append(f"{r['market']:<6} {r['scope']:<13} {r['n']:>3} {r['n_keys']:>4} {r['n_samples']:>10} {_f(r['p85'])} {_f(r['d_new'])} {_f(r['d_table'], 8)} "
                 f"{_f(r['diff_pct'], 7, 1)} {_f(r['clip_table_pct'], 7, 2)} {_f(r['clip_new_pct'], 7, 2)} {_f(r['adopt_p85'], 5)}")
    L.append("")
    L.append("## 持續性族（不校準；d_formula＝值域上界÷3，與現行 d 對照）")
    for r in rep["rows"]:
        if r["category"] == CAT_PERSISTENCE:
            L.append(f"  {r['key']:<58} n={r['n']:>7} upper={_f(r.get('range_upper'), 5, 1)} d_formula={_f(r.get('d_formula'), 7)} d_old={_f(r['d_old'], 6)} "
                     f"same={'Y' if r.get('d_formula_matches_old') else 'N'} clip_old%={_f(r['clip_old_pct'], 6, 2)}  {r['persistence_note']}")
    return "\n".join(L) + "\n"


def run(args) -> int:
    dump_dir = Path(args.dump_dir)
    try:
        if not dump_dir.is_dir():
            raise CalibrateError(f"dump 目錄 {dump_dir} 不存在")
        m = load_manifest(dump_dir)
        want = expected_params_sha(m["params"])
        if m["params_sha"] != want:
            raise CalibrateError(f"manifest.params_sha={m['params_sha']} ≠ 現行碼指紋 {want}（這份 dump 不是現行碼算的；重跑 --dump-x）")
        rows, dist_rows = build_rows(dump_dir, m, method=args.method, gate_pct=args.gate, dist_tol=args.distance_tol)
    except (CalibrateError, XDumpError, OSError, ValueError, KeyError) as e:
        print(f"[calibrate_d 中止] {e}", file=sys.stderr)
        return 2
    counts: dict[str, int] = {}
    for r in rows:
        counts[r["category"]] = counts.get(r["category"], 0) + 1
    rep = {"schema": 1, "generated_at": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "dump_dir": str(dump_dir),
           "dump_from": m["dump_from"], "dump_to": m["dump_to"], "days_dumped": m.get("days_dumped"), "data_version": m["data_version"],
           "params_sha": m["params_sha"], "percentile": PERCENTILE, "method": args.method, "gate_pct": args.gate,
           "distance_tol": args.distance_tol, "counts": counts, "n_keys_with_data": sum(1 for r in rows if r["n"]),
           "n_values": int(m.get("n_values", 0)), "n_skipped": int(m.get("n_skipped", 0)),
           "gate_failures": [r["key"] for r in rows if r["gate_fail"]],
           "gate_failures_strict": [r["key"] for r in rows if r["gate_fail_strict"]],
           "degenerate": [r["key"] for r in rows if r.get("degenerate")],
           "median_flags": [r["key"] for r in rows if r.get("median_flag")],
           "distance_pooled": dist_rows, "rows": rows}
    tag = args.tag or m["dump_to"]
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    jpath, tpath = out_dir / f"d_report_{tag}.json", out_dir / f"d_report_{tag}.txt"
    txt = render_txt(rep)
    jpath.write_text(json.dumps(rep, ensure_ascii=False, indent=1, sort_keys=True), encoding="utf-8")
    tpath.write_text(txt, encoding="utf-8")
    if not args.quiet:
        print(txt, end="")
    print(f"報告：{jpath}　{tpath}　鍵 {counts}　閘門超標 {len(rep['gate_failures'])} 個（嚴格版 {len(rep['gate_failures_strict'])}）："
          f"{rep['gate_failures'] or '（無）'}　退化 {len(rep['degenerate'])} 個")
    return 0


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description="讀 x dump 算每鍵 |x−c| 的 p85 → d_new=p85/3 與截斷比例；只出報告，不動 params.py")
    ap.add_argument("--dump-dir", default=str(REPO / "cache" / "xdump"))
    ap.add_argument("--out-dir", default=str(REPO / "runs" / "calib"))
    ap.add_argument("--tag", default=None, help="輸出檔名 d_report_<tag>；預設 manifest 的 dump_to")
    ap.add_argument("--gate", type=float, default=15.0, help="截斷比例閘門（%），預設 15")
    ap.add_argument("--distance-tol", type=float, default=0.25, help="距離型 |d_new−d_old|/d_old 超過即標 adopt_p85，預設 0.25")
    ap.add_argument("--method", default="linear", help="numpy.percentile 的 method，預設 linear")
    ap.add_argument("--quiet", action="store_true", help="不把 .txt 印到 stdout")
    return ap


def main(argv=None) -> int:
    return run(build_parser().parse_args(argv))


if __name__ == "__main__":
    raise SystemExit(main())
