#!/usr/bin/env python3
"""把 d 校準報告寫成程式碼常數（P3 第 3 項第 2 步，`docs/P3-CALIBRATION.md` §2 第 3 步／§8 設計）。

讀 `runs/calib/d_report_<TO>.json`（`scripts/calibrate_d.py` 的產物）→ 依裁定 #54／#55／#56 算出每個鍵的**採用 d**
→ 產生 `src/iching/score/calibrated.py`（`CALIBRATED_D`／`CALIBRATED_DISTANCE_D`／`CALIBRATED_SLOPE_D`／`CALIBRATION_META`）。
`params.py` 查這四個常數、查不到才用設計起點值。

**純函式、決定性、離線**：同一份報告跑兩次輸出逐位相同（`--check` 可驗）；不連網、不碰 `scores.db`、不讀 x dump
（x 的統計早就在報告裡）。**刻意不寫牆鐘時間**——`CALIBRATION_META` 的時間欄取報告自己的 `generated_at`，
否則同輸入同輸出這條就破了（H5）。

## 採用 d 的規則（逐條對應裁定，不是本腳本自創）

| 報告 `category` | 條件 | 採用 d | 出處 |
|---|---|---|---|
| `calibrate` | `z_zero < 0.50` | `d_new`＝`p85 ÷ 3` | `spec/P1-B1-market.md:42` 第 3 點；裁定 #54 Q1/Q5 |
| `calibrate` | `z_zero ≥ 0.50` | `d_nonzero`＝**非零樣本** `p85 ÷ 3` | 裁定 #56（§12）——零膨脹，規格未預期的第三種例外 |
| `distance` | 每個 `distance_d[n]` 格 | 該格**所有鍵** `p85/3` 的**最大值** | 裁定 #55（§9）——取代 #54 Q3 的 25% 容差與「合併樣本 p85」 |
| `persistence` | — | `原始值域上界 ÷ 3`（**不用 p85**） | `spec/P1-B1-market.md:48` 5a；裁定 #54 Q4；主對話裁決 ③ |
| `not_applicable`／`n = 0` | — | **不校準**，維持設計起點值 | 裁定 #26（`vix_phist_rev`）／#36 乙（`equity_qoq`） |

`median_flag`（裁定 #54 Q2：c 不動）只記進 `CALIBRATION_META`，**不改任何 c**。

**持續性族的「原始值域上界」目前只能反推**：`Param` 沒有任何欄位記錄 x 的原生值域（`native_range` 是**轉換後**
的分數值域，`params.py:44`），故上界取 `視窗 n ÷ 2`（指標本身是「近 n 日買超天數 − n/2」，值域 `[−n/2, +n/2]`），
與 `calibrate_d.py` 的 `range_upper` 同一套；此推導來源寫進 `CALIBRATION_META`（§7.3 C 要求）。

## 斜率族為什麼可以逐鍵校準

`market_slope_d[n]`／`stock_slope_d[n]` 雖是共用查表，但 `MKT_L1_WIN`／`STK_L2_WIN` 讓**每個視窗 n 只被一個期間引用**
（short→5、swing→10、mid→20），所以「一格 d ↔ 一個鍵」，逐鍵校準不衝突（本腳本會實查斷言，衝突即 rc 2）。
距離型則相反（一格最多 7 個鍵），故走裁定 #55 的「取該格最大」。

## `CALIBRATION_META` 的值一律寫成字串

`tests/test_score_params_guard.py` 守「`src/iching/score/`（`params.py` 除外）內的數值字面量必須是參數值或白名單」。
meta 是純紀錄、不進算式，若照原型別寫（`percentile: 85.0`、`days_dumped: 603`）會在計分套件裡引入非參數字面量。
一律字串既保住那道守門，也不必為了一份紀錄去放寬它。**d 本身當然是數值**——它們全部都是參數值。

rc：0＝已寫出（或 `--check` 通過）；1＝`--check` 下檔案內容與重新產生的不同；2＝報告缺／格式不符／規則算不出來。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parent.parent

GENERATOR = "scripts/apply_calibration.py"
GENERATOR_VERSION = "1"
# 報告檔的來源（`origin/hetzner/calib-2023-06-30`）。只進 meta 當紀錄，可用 --source-commit 覆寫；
# 預設是常數而不是去問 git，才有「同輸入同輸出」。
# 沿革：`4f2f378`（#68 前的報告，現由 `hetzner/calib-2023-06-30-pre68` 引用）→ 裁定 #69 起改為 #68 後重跑的報告
# （docs/P3-CALIBRATION.md §32）。寫完整 40 碼：分支已被覆寫過一次，短碼只在「還找得到」時才有意義。
REPORT_SOURCE_COMMIT = "288fd36d2c0f1ef7a06f11c13b24481e6120f784"
REPORT_SOURCE_BRANCH = "origin/hetzner/calib-2023-06-30"

ZERO_INFLATION_THRESHOLD = 0.50     # 裁定 #56：z_zero ≥ 此值改用非零樣本 p85
GATE_PCT = 15.0                     # spec/P1-B1-market.md:43；餘裕 100/n 見 calibrate_d.py 檔頭

CAT_CALIBRATE, CAT_PERSISTENCE, CAT_DISTANCE, CAT_NA = "calibrate", "persistence", "distance", "not_applicable"
DISTANCE_TABLE = "distance_d"
SLOPE_TABLES = ("market_slope_d", "stock_slope_d")

RULE_P85 = "p85/3"
RULE_NONZERO = "nonzero_p85/3"
RULE_SLOT_MAX = "slot_max(p85/3)"
RULE_FORMULA = "range_upper/3"
RULE_KEEP = "keep_start"

OUT_DEFAULT = REPO / "src" / "iching" / "score" / "calibrated.py"


class ApplyCalibrationError(RuntimeError):
    pass


# ---------------------------------------------------------------------------
# 規則
# ---------------------------------------------------------------------------
def key4(row: dict[str, Any]) -> tuple[str, str, str, str]:
    """`CALIBRATED_D` 的鍵＝`(market, scope, indicator_id, horizon)`（報告 304 列實查唯一）。"""
    return (str(row["market"]), str(row["scope"]), str(row["indicator_id"]), str(row["horizon"]))


def is_slope(row: dict[str, Any]) -> bool:
    return row.get("shared_d_table") in SLOPE_TABLES


def adopted_direct(row: dict[str, Any]) -> tuple[float, str]:
    """`calibrate`／`persistence` 單鍵的採用 d 與規則名。呼叫端要先濾掉 `n=0`。"""
    cat = row["category"]
    if cat == CAT_PERSISTENCE:
        d = row.get("d_formula")
        if d is None:
            raise ApplyCalibrationError(f"{row['key']}：persistence 但報告無 d_formula")
        return float(d), RULE_FORMULA
    if cat != CAT_CALIBRATE:
        raise ApplyCalibrationError(f"{row['key']}：category={cat} 不該走單鍵規則")
    z = row.get("z_zero")
    if z is None:
        raise ApplyCalibrationError(f"{row['key']}：calibrate 但報告無 z_zero（報告太舊，需帶零膨脹欄的版本）")
    if float(z) >= ZERO_INFLATION_THRESHOLD:
        d = row.get("d_nonzero")
        if d is None or float(d) <= 0.0:
            raise ApplyCalibrationError(f"{row['key']}：z_zero={z} 需非零樣本 p85，但 d_nonzero={d!r}")
        return float(d), RULE_NONZERO
    d = row.get("d_new")
    if d is None or float(d) <= 0.0:
        raise ApplyCalibrationError(f"{row['key']}：d_new={d!r} 不是合法 d（p85=0 退化但 z_zero<0.5？）")
    return float(d), RULE_P85


def expected_clip_pct(row: dict[str, Any], rule: str) -> float | None:
    """該鍵在採用 d 下的截斷比例（H6 用）。`slot_max` 只給得出上界，見 `gate_report`。"""
    if rule == RULE_NONZERO:
        return None if row.get("clip_nonzero_pct") is None else float(row["clip_nonzero_pct"])
    if rule == RULE_P85:
        return None if row.get("clip_new_pct") is None else float(row["clip_new_pct"])
    return None


# ---------------------------------------------------------------------------
# 計畫
# ---------------------------------------------------------------------------
@dataclass
class Plan:
    direct: dict[tuple[str, str, str, str], tuple[float, str, str]] = field(default_factory=dict)
    distance: dict[str, dict[int, float]] = field(default_factory=dict)
    distance_src: dict[tuple[str, int], list[str]] = field(default_factory=dict)
    slope: dict[str, dict[str, dict[int, float]]] = field(default_factory=dict)
    slope_src: dict[tuple[str, str, int], str] = field(default_factory=dict)
    not_calibrated: list[str] = field(default_factory=list)
    median_flags: list[str] = field(default_factory=list)
    gate_exceptions: list[str] = field(default_factory=list)
    zero_inflated: list[str] = field(default_factory=list)


def build_plan(report: dict[str, Any]) -> Plan:
    rows = report["rows"]
    plan = Plan()
    slot: dict[tuple[str, int], list[tuple[float, str]]] = {}
    for row in sorted(rows, key=lambda r: str(r["key"])):
        key, cat, n = str(row["key"]), row["category"], int(row["n"])
        if cat == CAT_NA:
            plan.not_calibrated.append(f"{key} — not_applicable（clip_policy=n/a，無 c 無 d）")
            continue
        if n == 0:
            plan.not_calibrated.append(f"{key} — n=0（訓練段無樣本，維持設計起點值）")
            continue
        if row.get("median_flag"):
            plan.median_flags.append(f"{key} — |median(x)−c| > d_new；裁定 #54 Q2：c 不動，只記錄")
        if cat == CAT_DISTANCE:
            d_new = row.get("d_new")
            if d_new is None or float(d_new) <= 0.0:
                raise ApplyCalibrationError(f"{key}：distance 但 d_new={d_new!r} 不合法")
            slot.setdefault((str(row["market"]), int(row["shared_d_n"])), []).append((float(d_new), key))
            continue
        d, rule = adopted_direct(row)
        if rule == RULE_NONZERO:
            plan.zero_inflated.append(f"{key} — z_zero={float(row['z_zero']):.4f}")
        clip = expected_clip_pct(row, rule)
        if clip is not None and clip > GATE_PCT + 100.0 / n:
            plan.gate_exceptions.append(f"{key} — 採用 d 下截斷 {clip:.4f}% > 15%+100/{n}")
        if is_slope(row):
            tbl, sn, market = str(row["shared_d_table"]), int(row["shared_d_n"]), str(row["market"])
            prev = plan.slope_src.get((tbl, market, sn))
            if prev is not None:
                raise ApplyCalibrationError(
                    f"{tbl}[{sn}]（{market}）同時被 {prev} 與 {key} 引用——斜率表不再是一格一鍵，"
                    f"逐鍵校準會衝突，必須先裁定取法")
            plan.slope.setdefault(tbl, {}).setdefault(market, {})[sn] = d
            plan.slope_src[(tbl, market, sn)] = key
            continue
        plan.direct[key4(row)] = (d, rule, key)
    for (market, sn), items in sorted(slot.items()):
        d = max(v for v, _ in items)
        plan.distance.setdefault(market, {})[sn] = d
        plan.distance_src[(market, sn)] = sorted(k for v, k in items if v == d)
    return plan


def gate_report(report: dict[str, Any], plan: Plan) -> list[str]:
    """距離型在採用 d（≥ 自身 `d_new`）下的截斷不高於自身 `clip_new_pct`（單調），故以它當上界判閘門。"""
    out: list[str] = []
    for row in sorted(report["rows"], key=lambda r: str(r["key"])):
        if row["category"] != CAT_DISTANCE or int(row["n"]) == 0:
            continue
        d = plan.distance[str(row["market"])][int(row["shared_d_n"])]
        if d < float(row["d_new"]) - 1e-12:
            raise ApplyCalibrationError(f"{row['key']}：採用 d {d} < 自身 d_new {row['d_new']}（取最大值錯了）")
        bound = float(row["clip_new_pct"])
        if bound > GATE_PCT + 100.0 / int(row["n"]):
            out.append(f"{row['key']} — 截斷上界 {bound:.4f}% > 15%+100/{row['n']}")
    return out


# ---------------------------------------------------------------------------
# 產生程式碼
# ---------------------------------------------------------------------------
def _f(v: float) -> str:
    return repr(float(v))


def render(report: dict[str, Any], plan: Plan, report_path: Path, report_sha: str, source_commit: str) -> str:
    meta = {
        "source_commit": source_commit,
        "source_branch": REPORT_SOURCE_BRANCH,
        "source_report": report_path.as_posix(),
        "report_sha256": report_sha,
        "report_schema": str(report.get("schema")),
        "report_generated_at": str(report.get("generated_at")),
        "data_version": str(report.get("data_version")),
        "dump_from": str(report.get("dump_from")),
        "dump_to": str(report.get("dump_to")),
        "days_dumped": str(report.get("days_dumped")),
        "percentile": str(report.get("percentile")),
        "percentile_method": str(report.get("method")),
        "params_sha_before": str(report.get("params_sha")),
        "generator": GENERATOR,
        "generator_version": GENERATOR_VERSION,
        "gate_pct": str(GATE_PCT),
        "gate_slack": "100/n（linear 內插的離散化餘裕，見 scripts/calibrate_d.py 檔頭）",
        "zero_inflation_threshold": str(ZERO_INFLATION_THRESHOLD),
        "rules": {
            CAT_CALIBRATE: f"z_zero < {ZERO_INFLATION_THRESHOLD} → d = {RULE_P85}（spec/P1-B1-market.md:42 第 3 點；裁定 #54）；"
                           f"z_zero ≥ {ZERO_INFLATION_THRESHOLD} → d = {RULE_NONZERO}（裁定 #56，零膨脹＝第三種例外）",
            CAT_DISTANCE: f"d = {RULE_SLOT_MAX}：每個 distance_d[n] 格取該格所有鍵 p85/3 的最大值（裁定 #55，"
                          f"取代 #54 Q3 的 25% 容差與合併樣本 p85）；查表結構保留（spec/P1-B1-market.md:49）",
            CAT_PERSISTENCE: f"d = {RULE_FORMULA}：原始值域上界 ÷ 3，不套 p85（spec/P1-B1-market.md:48 5a；裁定 #54 Q4）",
            CAT_NA: f"{RULE_KEEP}：not_applicable（clip_policy=n/a）與 n=0 的鍵不校準，維持 params.py 的設計起點值",
        },
        "persistence_upper_bound_source":
            "Param 沒有任何欄位記錄 x 的原生值域（native_range 是轉換後的分數值域，src/iching/score/params.py:44），"
            "故上界由『近 n 日買超天數 − n/2』反推為 視窗 n ÷ 2，與 scripts/calibrate_d.py 的 range_upper 同一套。"
            "登錄書引用本欄，不要再引口頭裁定（docs/P3-CALIBRATION.md §7.3 C）。",
        "c_unchanged": "裁定 #54 Q2：c 一律不動，只校 d。median_flags 只記錄、不改 c。",
        "slope_one_key_per_slot":
            "market_slope_d／stock_slope_d 雖是共用查表，但 MKT_L1_WIN／STK_L2_WIN 讓每個視窗 n 只被一個期間引用"
            "（short→5、swing→10、mid→20），故一格 d 對一個鍵，逐鍵校準不衝突；apply_calibration.py 產生時會實查斷言。",
        "distance_slot_sources": {f"{m}|{n}": ", ".join(ks) for (m, n), ks in sorted(plan.distance_src.items())},
        "zero_inflation_keys": list(plan.zero_inflated),
        "median_flags": list(plan.median_flags),
        "gate_exceptions": list(plan.gate_exceptions),
        "not_calibrated": list(plan.not_calibrated),
        "rulings": "docs/P3-CALIBRATION.md §6 裁定 #54／§9 裁定 #55／§12 裁定 #56；設計與驗收 §8 H1～H8；"
                   "報告出處：§32 裁定 #69（整份套用 #68 後重跑的報告）",
    }
    lines: list[str] = []
    ap = lines.append
    ap('"""d 校準值表——**本檔由 `scripts/apply_calibration.py` 產生，不要手改**。')
    ap("")
    ap("來源報告與逐類別規則見 `CALIBRATION_META`；設計與驗收條件見 `docs/P3-CALIBRATION.md` §8。")
    ap("`params.py` 的 `build_params()` 查這裡，查不到才用設計起點值（`*_START`／原字面量），")
    ap("所以「不校準的鍵」就是「不在這裡的鍵」。`CALIBRATION_META` 非空 ⇒ `ParamSet.calibrated=True`。")
    ap("")
    ap("重生方式：`python3 scripts/apply_calibration.py`（`--check` 只比對不寫，CI／測試用）。")
    ap('"""')
    ap("from __future__ import annotations")
    ap("")
    ap("# (market, scope, indicator_id, horizon) -> d")
    ap("CALIBRATED_D: dict[tuple[str, str, str, str], float] = {")
    for k in sorted(plan.direct):
        d, rule, rkey = plan.direct[k]
        ap(f'    ("{k[0]}", "{k[1]}", "{k[2]}", "{k[3]}"): {_f(d)},   # {rule}')
    ap("}")
    ap("")
    ap("# market -> MA 窗長 n -> d（裁定 #55：每格取該格所有鍵 p85/3 的最大值）")
    ap("CALIBRATED_DISTANCE_D: dict[str, dict[int, float]] = {")
    for m in sorted(plan.distance):
        inner = ", ".join(f"{n}: {_f(plan.distance[m][n])}" for n in sorted(plan.distance[m]))
        ap(f'    "{m}": {{{inner}}},')
    ap("}")
    ap("")
    ap("# 表名 -> market -> 斜率視窗 n -> d（兩張表各自一份；每格只被一個期間引用，故逐鍵校準）")
    ap("CALIBRATED_SLOPE_D: dict[str, dict[str, dict[int, float]]] = {")
    for tbl in sorted(plan.slope):
        ap(f'    "{tbl}": {{')
        for m in sorted(plan.slope[tbl]):
            inner = ", ".join(f"{n}: {_f(plan.slope[tbl][m][n])}" for n in sorted(plan.slope[tbl][m]))
            ap(f'        "{m}": {{{inner}}},')
        ap("    },")
    ap("}")
    ap("")
    ap("# 全部欄位一律字串（理由見 scripts/apply_calibration.py 檔頭「CALIBRATION_META 的值一律寫成字串」）")
    ap("CALIBRATION_META: dict[str, object] = " + json.dumps(meta, ensure_ascii=False, indent=4, sort_keys=True))
    ap("")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
def load_report(path: Path) -> tuple[dict[str, Any], str]:
    try:
        raw = path.read_bytes()
    except OSError as e:
        raise ApplyCalibrationError(f"讀不到報告 {path}：{e}") from e
    try:
        report = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as e:
        raise ApplyCalibrationError(f"報告 {path} 不是合法 JSON：{e}") from e
    if not isinstance(report, dict) or not isinstance(report.get("rows"), list) or not report["rows"]:
        raise ApplyCalibrationError(f"報告 {path} 缺 rows")
    return report, hashlib.sha256(raw).hexdigest()


def default_report(runs_dir: Path) -> Path:
    found = sorted(runs_dir.glob("d_report_*.json"))
    if len(found) != 1:
        raise ApplyCalibrationError(
            f"{runs_dir} 下有 {len(found)} 份 d_report_*.json，無法決定用哪一份——請用 --report 指定")
    return found[0]


def generate(report_path: Path, *, source_commit: str = REPORT_SOURCE_COMMIT,
             rel_to: Path | None = REPO) -> tuple[str, Plan, dict[str, Any]]:
    report, sha = load_report(report_path)
    plan = build_plan(report)
    plan.gate_exceptions.extend(gate_report(report, plan))
    plan.gate_exceptions.sort()
    shown = report_path
    if rel_to is not None:
        try:
            shown = report_path.resolve().relative_to(rel_to.resolve())
        except ValueError:
            shown = report_path
    return render(report, plan, Path(shown), sha, source_commit), plan, report


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="由 d 校準報告產生 src/iching/score/calibrated.py（決定性、離線）")
    ap.add_argument("--report", default=None, help="d_report_<TO>.json；預設取 --runs-dir 下唯一那份")
    ap.add_argument("--runs-dir", default=str(REPO / "runs" / "calib"), help="預設報告目錄")
    ap.add_argument("--out", default=str(OUT_DEFAULT), help="輸出 .py（預設 src/iching/score/calibrated.py）")
    ap.add_argument("--source-commit", default=REPORT_SOURCE_COMMIT, help="報告來源 commit，只進 CALIBRATION_META")
    ap.add_argument("--check", action="store_true", help="不寫檔，只比對現有檔案是否等於重新產生的內容（不同回 rc 1）")
    ap.add_argument("--stdout", action="store_true", help="印到 stdout、不寫檔")
    ap.add_argument("--quiet", action="store_true")
    a = ap.parse_args(argv)
    try:
        path = Path(a.report) if a.report else default_report(Path(a.runs_dir))
        text, plan, report = generate(path, source_commit=a.source_commit)
    except ApplyCalibrationError as e:
        print(f"[錯誤] {e}", file=sys.stderr)
        return 2
    if a.stdout:
        sys.stdout.write(text)
        return 0
    out = Path(a.out)
    if a.check:
        cur = out.read_text(encoding="utf-8") if out.is_file() else None
        if cur != text:
            print(f"[不一致] {out} 與由 {path} 重新產生的內容不同（重跑 {GENERATOR} 即可）", file=sys.stderr)
            return 1
        if not a.quiet:
            print(f"[OK] {out} ＝ 由 {path} 重新產生的內容")
        return 0
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text, encoding="utf-8")
    if not a.quiet:
        n_slope = sum(len(v) for m in plan.slope.values() for v in m.values())
        n_dist = sum(len(v) for v in plan.distance.values())
        print(f"[寫出] {out}")
        print(f"  CALIBRATED_D {len(plan.direct)} 鍵／距離 {n_dist} 格／斜率 {n_slope} 格／"
              f"不校準 {len(plan.not_calibrated)} 鍵／零膨脹 {len(plan.zero_inflated)} 鍵／"
              f"median_flag {len(plan.median_flags)} 鍵／閘門例外 {len(plan.gate_exceptions)} 鍵")
        print(f"  來源 {path}（{len(report['rows'])} 列，params_sha={report.get('params_sha')}）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
