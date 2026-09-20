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
- **零膨脹（2026-09-20 追加，供「退化鍵怎麼處理」裁定；`docs/P3-CALIBRATION.md` §2 第 1 步）**：Hetzner `d_report` 揭露規格未預期的
  情況——`trust_strength_long/short` 在上櫃有 6 鍵 `p85 = 0`（`d = p85/3 = 0` 不合法），上市對應鍵 d 由 5 掉到 0.287（幾乎成二值旗標）。
  要裁定就得先有「零比例」這個數字，故每個 `n > 0` **且需要 d** 的鍵（`calibrate`／`distance`／`persistence`）
  **一律**多報（不需旗標、不必跑第二次）：
  - `z_zero`＝`x` 恰為 0 的樣本比例。**零的定義是檔內值為 0**——`x_kind="x_minus_rolling_c"`（`basis`）的檔已是 `x − c_rolling`，
    其零即 `x − c_rolling == 0`；**與 `|x−c| == 0` 不是同一件事**（c ≠ 0 的鍵，x=0 的樣本 dev 是 `|c|` 而非 0）。
  - `n_nonzero`＝非零樣本數；`p85_nonzero`＝**只取非零樣本**的 `|x−c|` 分位（`n_nonzero=0` 時 null）；`d_nonzero = p85_nonzero ÷ 3`（null 傳遞）。
  - `clip_nonzero_pct`＝`d_nonzero` 下**全體樣本**（不是只有非零樣本）`|x−c| > 3·d_nonzero` 的比例——**閘門的分母定死為全體樣本**
    （`spec/P1-B1-market.md:44`），只拿非零樣本算是換掉分母、與閘門口徑不同。偏差方向視 `|c|` 是否落在 `3·d_nonzero` 之外而定：
    c=0（214 個需要 d 的鍵中有 208 個）時零樣本的 dev＝0、永不截斷，只算非零會**偏高**；c≠0 時才偏低。
  頂層 `zero_inflation` 兩份清單：`z_ge_85pct`（`z_zero ≥ 0.85`；**c=0 時**即 p85＝0＝`degenerate`，c≠0 時零樣本的 dev＝`|c|`、p85 不一定為 0）與
  `z_ge_50pct`（`z_zero ≥ 0.5`，前者的超集）。**非零版一律以額外欄位並存、不做 `--nonzero-only` 這種會改變主要輸出的旗標**：
  一次跑就拿到兩套數字，也不會有人搞混哪份是哪份。既有欄位一字不動（`tests/test_calibrate.py` 以「舊版 vs 新版逐欄相同」守）。
  `not_applicable`（`clip_policy=n/a`）沒有 c 也沒有 d，零膨脹對它零決策價值 → **五欄一律 null、`.f32` 也不讀**
  （讀了只會多出 I/O，還把「檔長 ≠ manifest n」從靜默略過變成 rc 2），`zero_inflation` 兩份清單亦只收需要 d 的三類。

輸出 `runs/calib/d_report_<dump_to>.json`＋`.txt`（人讀表）。rc：0＝報告已寫；2＝dump 目錄／manifest 缺、manifest 的
`params_sha` ≠ 現行碼指紋（`export_dataset.expected_params_sha`，同 `hetzner_adj.sh` 守門 b）、`.f32` 筆數與 manifest 不符。
閘門超標**不改 rc**（那是裁定 d 的輸入，不是工具失敗）。

分位由 `--percentile`（預設 85）決定，報告的 `percentile` 欄與 `.txt` 表頭照實寫；**欄名維持 `p85`／`p85_nonzero` 不改名**
（改名會讓歷次報告的欄位對不起來），所以讀報告前先看 `percentile` 欄。

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

PERCENTILE_DEFAULT = 85.0          # --percentile 的預設；報告 percentile 欄照實寫，欄名 p85／p85_nonzero 不隨之改名
ZERO_INFLATION_HI = 0.85           # z_zero ≥ 此值 ⇒ p85＝0（**僅 c=0 時**；c≠0 的鍵零樣本 dev＝|c|，p85 不一定為 0）
ZERO_INFLATION_LO = 0.5
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


def stats_for(x: np.ndarray, c: float, d_old: float | None, method: str, pct: float = PERCENTILE_DEFAULT) -> dict[str, Any]:
    dev = np.abs(x - c)
    p85 = float(np.percentile(dev, pct, method=method))
    d_new = p85 / 3.0
    med = float(np.median(x))
    out = {"p85": p85, "d_new": d_new, "median_x": med, "clip_new_pct": _clip_pct(dev, d_new),
           "median_flag": bool(abs(med - c) > d_new), "x_min": float(x.min()), "x_max": float(x.max())}
    out["clip_old_pct"] = _clip_pct(dev, d_old) if d_old is not None else None
    out.update(zero_stats(x, dev, pct, method))
    return out


def _clip_pct(dev: np.ndarray, d: float | None) -> float | None:
    if d is None or d <= 0:
        return None
    return float(np.mean(dev > 3.0 * d) * 100.0)


def zero_share(x: np.ndarray) -> tuple[float, int]:
    """`(z_zero, n_nonzero)`：零＝**檔內值**恰為 0（`x_kind=x_minus_rolling_c` 的鍵即 `x − c_rolling == 0`）。"""
    n_nonzero = int(np.count_nonzero(x))
    return float((x.size - n_nonzero) / x.size), n_nonzero


def zero_stats(x: np.ndarray, dev: np.ndarray, pct: float, method: str) -> dict[str, Any]:
    """零膨脹五欄（檔頭「零膨脹」段）。`p85_nonzero` 只取非零樣本算 `|x−c|` 的分位；
    `clip_nonzero_pct` 則刻意用**全體樣本**——閘門的分母定死為全體（`spec/P1-B1-market.md:44`），
    換成非零樣本就不是同一個口徑；偏差方向視 `|c|` 而定（c=0 時只算非零會偏高，不是偏低）。"""
    z_zero, n_nonzero = zero_share(x)
    p_nz = float(np.percentile(dev[x != 0.0], pct, method=method)) if n_nonzero else None
    d_nz = p_nz / 3.0 if p_nz is not None else None
    return {"z_zero": z_zero, "n_nonzero": n_nonzero, "p85_nonzero": p_nz, "d_nonzero": d_nz,
            "clip_nonzero_pct": _clip_pct(dev, d_nz)}


def build_rows(dump_dir: Path, m: dict[str, Any], *, method: str, gate_pct: float, dist_tol: float,
               pct: float = PERCENTILE_DEFAULT) -> tuple[list[dict], list[dict]]:
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
                        "gate_slack_pct": None, "degenerate": False,
                        "z_zero": None, "n_nonzero": None, "p85_nonzero": None, "d_nonzero": None, "clip_nonzero_pct": None})
            # n/a 鍵（clip_policy=n/a）沒有 c 也沒有 d，零膨脹對它零決策價值 → 五欄一律 null，且**刻意不讀它的 .f32**：
            # 讀了只會在真實 dump 上多出未實測的 I/O，還把「檔長 ≠ manifest n」從靜默略過變成 rc 2（為零價值引入新失效模式）
            rows.append(row)
            continue
        x = load_x(dump_dir, snap)
        d_old = float(snap["d"]) if snap.get("d") is not None else None
        st = stats_for(x, row["c"], d_old, method, pct)
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
        p85 = float(np.percentile(dev, pct, method=method))
        table_d = tables.get(mk, {}).get("distance_d", {}).get(str(n))
        table_d = float(table_d) if table_d is not None else None
        dist_rows.append({"market": mk, "scope": "all", "n": n, "n_samples": int(dev.size), "n_keys": len(parts), "p85": p85, "d_new": p85 / 3.0,
                          "d_table": table_d, "diff_pct": (abs(p85 / 3.0 - table_d) / table_d * 100.0) if table_d else None,
                          "adopt_p85": bool(table_d is None or abs(p85 / 3.0 - table_d) / table_d > dist_tol),
                          "clip_table_pct": _clip_pct(dev, table_d), "clip_new_pct": _clip_pct(dev, p85 / 3.0)})
    for (mk, sc, n), parts in sorted(pooled_scope.items()):
        dev = np.concatenate(parts)
        p85 = float(np.percentile(dev, pct, method=method))
        table_d = tables.get(mk, {}).get("distance_d", {}).get(str(n))
        table_d = float(table_d) if table_d is not None else None
        dist_rows.append({"market": mk, "scope": sc, "n": n, "n_samples": int(dev.size), "n_keys": len(parts), "p85": p85, "d_new": p85 / 3.0,
                          "d_table": table_d, "diff_pct": (abs(p85 / 3.0 - table_d) / table_d * 100.0) if table_d else None,
                          "adopt_p85": bool(table_d is None or abs(p85 / 3.0 - table_d) / table_d > dist_tol),
                          "clip_table_pct": _clip_pct(dev, table_d), "clip_new_pct": _clip_pct(dev, p85 / 3.0)})
    return rows, dist_rows


# 裁定用的最小欄位集（另帶 category／n_nonzero：前者讓人一眼看出該鍵是不是 n/a 類、後者省去由 n×(1−z_zero) 回推）
ZI_FIELDS = ("key", "category", "z_zero", "n", "n_nonzero", "p85", "p85_nonzero", "d_old", "d_nonzero", "clip_nonzero_pct")


ZI_CATEGORIES = (CAT_CALIBRATE, CAT_DISTANCE, CAT_PERSISTENCE)      # 只有這三類需要 d；n/a 不入列（也不讀檔、沒有 z_zero）


def zero_inflated(rows: list[dict[str, Any]], thr: float) -> list[dict[str, Any]]:
    """`z_zero ≥ thr` 且**需要 d** 的鍵清單（零比例高到低、同比例照鍵名）。`n=0` 與 `n/a` 沒有 z_zero，本來就不入列，
    這裡另以 `ZI_CATEGORIES` 明確過濾——清單是拿來裁定 d 的，只該出現「有 d 可裁」的鍵。"""
    hit = [r for r in rows if r["category"] in ZI_CATEGORIES and r.get("z_zero") is not None and r["z_zero"] >= thr]
    return [{f: r.get(f) for f in ZI_FIELDS} for r in sorted(hit, key=lambda r: (-r["z_zero"], r["key"]))]


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
    pc = f"p{rep['percentile']:g}"          # 分位由 --percentile 決定；欄名固定 p85，表頭照實寫實際分位
    L.append(f"# d 校準報告  dump {rep['dump_from']}～{rep['dump_to']}（{rep['days_dumped']} 日） data_version={rep['data_version']} "
             f"params_sha={rep['params_sha']}  {pc} method={rep['method']}  閘門 {rep['gate_pct']}%  距離型容差 {rep['distance_tol'] * 100:.0f}%")
    L.append(f"# 鍵數：{rep['counts']}；有樣本的鍵 {rep['n_keys_with_data']}／{len(rep['rows'])}；樣本合計 {rep['n_values']:,}、跳過 {rep['n_skipped']:,}")
    L.append(f"# clip_old%／clip_new%＝舊 d／新 d（{pc}÷3）下 |x−c|>3d 的比例；clip_eff%＝生效 d（見檔頭）下的比例；med!＝|median−c|>d_new；"
             f"adopt＝距離型 |d_new−d_old|/d_old 超容差；gate＝clip_eff% > 閘門＋100/n（一個樣本的離散化餘裕；嚴格版見下方清單）；{pc}=0 者 d_eff 留空＝退化")
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
    L.append(f"## 退化：{pc} = 0（≥{rep['percentile']:g}% 樣本恰等於 c，d_new=0 非法）的鍵（{len(rep['degenerate'])} 個；要人看）")
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
    # 以下為 2026-09-20 追加段，**接在既有內容之後**（既有各段一字不動，回歸測試以「新 .txt 以舊 .txt 開頭」守）
    L.append("")
    L.append(f"## 零膨脹（{pc}_nz／d_nz＝只取非零樣本的分位與 d；clipNZ%＝**全體**樣本在 d_nz 下的截斷比例，閘門要看全體）")
    L.append(f"# z_zero＝x 恰為 0 的樣本比例（x_kind=x_minus_rolling_c 的鍵＝x−c_rolling=0，不是 |x−c|=0）；"
             f"z_zero ≥ {ZERO_INFLATION_HI:.0%} 且 c=0 ⇒ {pc}＝0＝退化；c≠0 的鍵零樣本的 dev＝|c|，{pc} 不一定為 0（看同列 {pc} 欄）。"
             f"**只列需要 d 的鍵**（{'／'.join(ZI_CATEGORIES)}）——n/a 類無 c 無 d，五個零膨脹欄一律空、其 .f32 也不讀")
    zi = rep.get("zero_inflation") or {}
    hdr2 = (f"{'category':<14} {'key':<58} {'n':>8} {'z_zero%':>8} {'n_nz':>8} {'p85':>9} {'p85_nz':>9} {'d_old':>8} {'d_nz':>9} {'clipNZ%':>8}")
    for lbl, kk in ((f"≥{ZERO_INFLATION_HI:.0%}（c=0 者 {pc}＝0）", "z_ge_85pct"), (f"≥{ZERO_INFLATION_LO:.0%}（含上表）", "z_ge_50pct")):
        lst = zi.get(kk) or []
        L.append(f"### z_zero {lbl}：{len(lst)} 個")
        if not lst:
            L.append("  （無）")
            continue
        L.append(hdr2)
        for r in lst:
            L.append(f"{r['category']:<14} {r['key']:<58} {r['n']:>8} {r['z_zero'] * 100:>8.2f} {_f(r['n_nonzero'], 8)} {_f(r['p85'])} "
                     f"{_f(r['p85_nonzero'])} {_f(r['d_old'], 8)} {_f(r['d_nonzero'])} {_f(r['clip_nonzero_pct'], 8, 2)}")
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
        rows, dist_rows = build_rows(dump_dir, m, method=args.method, gate_pct=args.gate, dist_tol=args.distance_tol,
                                     pct=args.percentile)
    except (CalibrateError, XDumpError, OSError, ValueError, KeyError) as e:
        print(f"[calibrate_d 中止] {e}", file=sys.stderr)
        return 2
    counts: dict[str, int] = {}
    for r in rows:
        counts[r["category"]] = counts.get(r["category"], 0) + 1
    rep = {"schema": 1, "generated_at": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "dump_dir": str(dump_dir),
           "dump_from": m["dump_from"], "dump_to": m["dump_to"], "days_dumped": m.get("days_dumped"), "data_version": m["data_version"],
           "params_sha": m["params_sha"], "percentile": float(args.percentile), "method": args.method, "gate_pct": args.gate,
           "distance_tol": args.distance_tol, "counts": counts, "n_keys_with_data": sum(1 for r in rows if r["n"]),
           "n_values": int(m.get("n_values", 0)), "n_skipped": int(m.get("n_skipped", 0)),
           "gate_failures": [r["key"] for r in rows if r["gate_fail"]],
           "gate_failures_strict": [r["key"] for r in rows if r["gate_fail_strict"]],
           "degenerate": [r["key"] for r in rows if r.get("degenerate")],
           "median_flags": [r["key"] for r in rows if r.get("median_flag")],
           "zero_inflation": {"z_ge_85pct": zero_inflated(rows, ZERO_INFLATION_HI),
                              "z_ge_50pct": zero_inflated(rows, ZERO_INFLATION_LO)},
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
    ap.add_argument("--percentile", type=float, default=PERCENTILE_DEFAULT,
                    help="|x−c| 取第幾百分位當 3d（%%），預設 85（規格判準）；改它會改 p85／p85_nonzero 兩欄的語意，"
                         "欄名不改名、報告 percentile 欄與 .txt 表頭照實寫")
    ap.add_argument("--gate", type=float, default=15.0, help="截斷比例閘門（%%），預設 15")
    ap.add_argument("--distance-tol", type=float, default=0.25, help="距離型 |d_new−d_old|/d_old 超過即標 adopt_p85，預設 0.25")
    ap.add_argument("--method", default="linear", help="numpy.percentile 的 method，預設 linear")
    ap.add_argument("--quiet", action="store_true", help="不把 .txt 印到 stdout")
    return ap


def main(argv=None) -> int:
    return run(build_parser().parse_args(argv))


if __name__ == "__main__":
    raise SystemExit(main())
