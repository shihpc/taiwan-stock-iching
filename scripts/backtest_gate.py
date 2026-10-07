"""§16.5「回測日期與洩漏」守門清單（v1.2.2 `spec/stock-iching-plan-v1.2.2.md:709-730`）逐列判定 → `runs/backtest/gate_<date>.txt`。

    scratchpad/venv312/bin/python scripts/backtest_gate.py --date 2026-10-07 [--check]

## 清單怎麼數

§16.5 的表在 v1.2.2 `:709` 表頭、`:710` 分隔列、**`:711`～`:730` 共 20 列**（本檔 `ROWS` 逐列釘住行號與條件名，
`tests/test_backtest_gate.py` 以 regex 對原文重數）。`docs/P3-KICKOFF.md` 完成定義 #7 寫的「十六列」＝扣掉 PR-D1 已由附錄 B／C
結案的 `:712`／`:714`／`:716`／`:717` 四列（規劃者推測，`plan_stats_layer.md` §F S2-8），本檔**不省略任何一列**：20 列全列，
每列三態——

- `PASS`：本層可機械驗，且驗過；證據寫在同列；
- `FAIL`：本層可機械驗，但不過（出現即 rc=1）；
- `N-A`：**不屬本層**（分數分布／截斷／維度／事件層／保留段）——標「另案」或「已有工具」並指到位置，不假裝驗過。

機械驗的輸入＝`runs/backtest/{train,valid}_<date>.json`（`scripts/backtest_stats.py` 產物）＋`data/calendar_tpe.json`＋
`data/backtest/manifest.json`＋`git`（`:728`）。本檔不讀任何 `csv.gz`、不重算統計。

rc：0 全部 PASS／N-A（或 `--check` 無差異）／1 任一 FAIL 或 `--check` 有差異／2 中止（檔案缺、任何例外）。
"""
from __future__ import annotations

import argparse
import inspect
import json
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "src"))
sys.path.insert(0, str(REPO / "scripts"))

import backtest_stats as BS

from iching.stats.constants import BLOCKS_MIN, EMBARGO_DAYS

SPEC = REPO / "spec" / "stock-iching-plan-v1.2.2.md"
SPEC_TABLE_FIRST, SPEC_TABLE_LAST = 711, 730
#: 登錄書凍結 commit／tag（`docs/pre-registration.md` §0）
PREREG_TAG = "prereg-v2"
PREREG_SHA = "59e03f1"
#: 登錄書 §1.4 `:295-297` 的區塊數量近似值（T5 公式，小數兩位）
BLOCKS_TABLE = {("train", "short"): 19.77, ("train", "swing"): 9.72, ("train", "mid"): 4.69,
                ("valid", "short"): 11.27, ("valid", "swing"): 5.47, ("valid", "mid"): 2.57}
SEG_DAYS = {"train": 603, "valid": 368}
SAMPLE_TOL = 0.05
#: 20 列：行號 → 條件名（與原文粗體／字面一致，測試對原文重數）
ROWS: list[tuple[int, str]] = [
    (711, "截斷政策一致性"), (712, "合法範圍"), (713, "shadow 值域"), (714, "可達邊界"), (715, "鍵維度涵蓋"),
    (716, "分布檢查"), (717, "門檻行為重驗"), (718, "截斷比例"), (719, "最後合格訊號日"), (720, "標籤不越界"),
    (721, "雙邊界 purge"), (722, "索引法而非近似"), (723, "embargo"), (724, "暖機隔離"), (725, "年度角色標示"),
    (726, "區塊數量近似值與證據不足"), (727, "卦序凍結"), (728, "登錄先於結果"), (729, "保留段一次性"), (730, "三期間樣本數"),
]
#: 非本層的八列：另案／已有工具與位置
NOT_THIS_LAYER = {
    711: "N-A（另案）：子指標 `clip_policy` 與值域屬計分層，`spec/P1-B2-params.md` §B2.0／`docs/P3-CALIBRATION.md`；本層不碰子指標",
    712: "N-A（已有工具）：`scripts/stats_appendix.py` → 登錄書附錄 C（`:712` 逸出 0 筆 PASS，`runs/stats/report_2026-10-02.json`）",
    713: "N-A（另案）：事件層 `E` 未上線（`docs/P3-KICKOFF.md` 完成定義 #10），無 shadow 分數可驗",
    714: "N-A（已有工具）：附錄 C `:714` 步驟 4 越界 0 組 PASS（同上報告）",
    715: "N-A（已有工具）：`spec/tools/check_dims.py`／`inject_test.py`／`tblcheck.py`／`gen_b5.py`，CI `.github/workflows/checks.yml` 每次 push 跑",
    716: "N-A（已有工具）：附錄 C `:716` 須解釋 6 組，裁定 #66／#70 已確認",
    717: "N-A（已有工具）：`scripts/t717_appendix.py` → 登錄書附錄 B（裁定 #62／#63）",
    718: "N-A（已有工具）：校準報告 `runs/calib/d_report_2023-06-30.*`、`docs/P3-CALIBRATION.md` §9／§12／§32；樣本外超標只人工複核（約定 4）",
}


class GateError(Exception):
    pass


def _assert(cond: bool, what: str) -> None:
    if not cond:
        raise GateError(what)


def spec_rows(text: str) -> list[tuple[int, str]]:
    """從 v1.2.2 原文重數 §16.5 表格列（供測試與本檔開跑互核）。"""
    lines = text.split("\n")
    out = []
    for ln in range(SPEC_TABLE_FIRST, SPEC_TABLE_LAST + 1):
        row = lines[ln - 1]
        _assert(row.startswith("| "), f"v1.2.2:{ln} 不是表格列")
        name = row.split("|")[1].strip()
        name = re.sub(r"\*\*", "", name)
        name = re.split(r"[（(]", name)[0].strip()
        out.append((ln, name))
    _assert(not lines[SPEC_TABLE_LAST].startswith("|"), f"v1.2.2:{SPEC_TABLE_LAST + 1} 仍是表格列——§16.5 列數變了，請更新 ROWS")
    return out


def _git(*args: str) -> str:
    return subprocess.run(["git", *args], cwd=REPO, check=True, capture_output=True, text=True).stdout.strip()


def evaluate(train: dict[str, Any], valid: dict[str, Any], cal: list[str], man: dict[str, Any]) -> list[dict[str, Any]]:
    """回 20 列 `{line, name, status, evidence}`。"""
    segs = {"train": train, "valid": valid}
    for seg, rep in segs.items():
        _assert(rep["segment"] == seg, f"{seg} json 的 segment={rep['segment']!r}")
        _assert(rep["gate"]["params_sha"] == BS.EXPECTED_PARAMS_SHA, f"{seg} json params_sha 不符")
    cal_pos = {d: i for i, d in enumerate(cal)}
    data_end_pos = cal_pos[man["calendar"]["data_end"]]
    cells = [(seg, c) for seg, rep in segs.items() for c in rep["cells"]]
    res: dict[int, tuple[str, str]] = {}
    for ln, note in NOT_THIS_LAYER.items():
        res[ln] = ("N-A", note)

    # :719 最後合格訊號日
    ev, ok = [], True
    for seg, c in cells:
        w = c["window"]
        last_ok = cal[w["boundary_pos"] - c["h"] - 2]
        ok &= w["last_day_eligible"] == last_ok == c["ic"]["last_day"]
        ev.append(f"{seg}/{c['market']}/{c['horizon']} h={c['h']} 最後合格 {w['last_day_eligible']}（段末 {w['seg_end']}、"
                  f"段日 {w['seg_days']}、合格日 {w['n_eff_index']}）")
    res[719] = ("PASS" if ok else "FAIL", "；".join(ev) + "。股票數見各 json rows.in_pool。§13.1 估算的是保留段資料截止，train/valid 無對應、此列只驗最後合格訊號日")

    # :720 標籤不越界：x = last_pos_eligible+1+h < boundary_pos ≤ data_end 索引
    ev, ok = [], True
    for seg, c in cells:
        w = c["window"]
        x = w["last_pos_eligible"] + 1 + c["h"]
        this = x < w["boundary_pos"] <= data_end_pos and c["ic"]["last_day"] == w["last_day_eligible"]
        ok &= this
        ev.append(f"{seg}/{c['market']}/{c['horizon']} 最大 x 索引 {x} < 邊界 {w['boundary_pos']} ≤ 資料末日索引 {data_end_pos}")
    res[720] = ("PASS" if ok else "FAIL", "；".join(ev))

    # :721 雙邊界 purge
    ev, ok = [], True
    b_train = cal_pos[valid["segment_info"]["first_trading_day"]]
    b_valid = cal_pos[valid["segment_info"]["next_segment_first_day"]]
    for seg, c in cells:
        w = c["window"]
        want_b = b_train if seg == "train" else b_valid
        this = (w["purge_days"] == c["h"] + 1 and w["boundary_pos"] == want_b
                and cal_pos[w["purge_first_day"]] == want_b - c["h"] - 1 and cal_pos[c["ic"]["last_day"]] < want_b - c["h"] - 1)
        ok &= this
        ev.append(f"{seg}/{c['market']}/{c['horizon']} 邊界 {cal[want_b]}（索引 {want_b}）排除訊號日 {w['purge_days']}"
                  f"（{w['purge_first_day']}～{w['purge_last_day']}）／列 {c['rows']['purged_rows']}")
    res[721] = ("PASS" if ok else "FAIL", "訓練→驗證與驗證→保留兩邊界：" + "；".join(ev))

    # :722 索引法而非近似
    files = sorted((REPO / "src" / "iching" / "stats").glob("*.py")) + [REPO / "scripts" / "backtest_stats.py"]
    hits = [f"{p.relative_to(REPO)}" for p in files for w in ("timedelta", "datetime") if w in p.read_text(encoding="utf-8")]
    src = (REPO / "scripts" / "backtest_stats.py").read_text(encoding="utf-8")
    uses = all(k in src for k in ("windows.purge_mask", "windows.embargo_mask", "windows.eval_mask"))
    ok = not hits and uses
    res[722] = ("PASS" if ok else "FAIL",
                (f"`timedelta`／`datetime` 於 src/iching/stats/*.py＋scripts/backtest_stats.py 命中 {len(hits)} 處{hits or ''}；"
                f"purge／embargo 走 `iching.stats.windows` 索引法（`e=i+1`、`x=i+1+h`）{'✓' if uses else '✗'}"))

    # :723 embargo
    ev, ok = [], True
    for seg, c in cells:
        w = c["window"]
        want = EMBARGO_DAYS if seg == "valid" else 0
        p0 = cal_pos[w["seg_start"]]
        this = w["embargo_days"] == want and w["first_pos_eligible"] == p0 + want and c["ic"]["first_day"] == w["first_day_eligible"]
        ok &= this
        ev.append(f"{seg}/{c['market']}/{c['horizon']} 排除 {w['embargo_days']} 日"
                  + (f"（{w['seg_start']}～{w['embargo_last_day']}）／列 {c['rows']['embargo_rows']}" if want else "（訓練段不扣，§1.4 `:288`）")
                  + f"，首個評估日 {w['first_day_eligible']}")
    res[723] = ("PASS" if ok else "FAIL", "；".join(ev) + "；保留段未跑")

    # :724 暖機隔離
    ev, ok = [], True
    for seg, rep in segs.items():
        first = min(c["ic"]["first_day"] for c in rep["cells"])
        this = first >= BS.SEGMENTS[seg][0] and rep["segment_info"]["first_trading_day"] >= BS.SEGMENTS[seg][0]
        ok &= this
        ev.append(f"{seg} 最早評估日 {first} ≥ 段起 {BS.SEGMENTS[seg][0]}")
    res[724] = ("PASS" if ok else "FAIL", "；".join(ev) + "（2020 價格軌／2019-06 基本面軌只在資料集產製端作暖機，本層讀到的最早日即段起）")

    # :725 年度角色標示
    ok = True
    years = {}
    for seg, c in cells:
        for y in c["yearly"]:
            years.setdefault(y["year"], set()).add(y["role"])
            ok &= ("訓練" in y["role"]) if seg == "train" else ("調參" in y["role"])
    res[725] = ("PASS" if ok else "FAIL", "逐年角色：" + "、".join(f"{y}＝{'／'.join(sorted(r))}" for y, r in sorted(years.items()))
                + "；2025 起＝未見（保留段，未跑）；調參年度不稱獨立證據")

    # :726 區塊數量近似值與證據不足
    ev, ok = [], True
    for seg, c in cells:
        nb = c["window"]["n_blocks_t5"]
        want = BLOCKS_TABLE[(seg, c["horizon"])]
        this = round(nb, 2) == want and ((nb < BLOCKS_MIN) == (c["verdict"] == "insufficient")) and c["window"]["seg_days"] == SEG_DAYS[seg]
        ok &= this
        ev.append(f"{seg}/{c['market']}/{c['horizon']} {nb:.2f}{'<' if nb < BLOCKS_MIN else '≥'}{BLOCKS_MIN} → {c['verdict']}")
    res[726] = ("PASS" if ok else "FAIL", "；".join(ev) + "；用語「區塊數量近似值」（相除不證明獨立）")

    # :727 卦序凍結
    sha_ok = all(rep["gate"]["rank_table_sha256"] == BS.RANK_TABLE_SHA256 for rep in segs.values())
    body = inspect.getsource(BS.rank_groups)
    no_sort = "sort" not in body.replace("不重排", "")
    res[727] = ("PASS" if sha_ok and no_sort else "FAIL",
                (f"兩段 json 的 `data/rank_table.json` sha256＝釘值 {BS.RANK_TABLE_SHA256[:12]}… {'✓' if sha_ok else '✗'}；"
                f"`backtest_stats.rank_groups` 只查表、無排序呼叫 {'✓' if no_sort else '✗'}"))

    # :728 登錄先於結果
    try:
        tag_sha = _git("rev-parse", f"{PREREG_TAG}^{{commit}}")
        anc = subprocess.run(["git", "merge-base", "--is-ancestor", tag_sha, "HEAD"], cwd=REPO, check=False).returncode == 0
        tag_time = _git("log", "-1", "--format=%cI", tag_sha)
        ok = tag_sha.startswith(PREREG_SHA) and anc
        res[728] = ("PASS" if ok else "FAIL",
                    (f"tag `{PREREG_TAG}`＝{tag_sha[:7]}（{tag_time}）為 HEAD 的祖先 {'✓' if anc else '✗'}（不印 HEAD sha：本檔要在 commit 後仍可 --check）；"
                    "結果檔 `runs/backtest/*` 與本報告只能 commit 在其後裔上，時間戳必晚於凍結；保留段未跑（門檻凍結早於保留段結果這一半待保留段）"))
    except (subprocess.CalledProcessError, FileNotFoundError) as e:
        res[728] = ("FAIL", f"git 不可用：{e}")

    # :729 保留段一次性
    hold = sorted(p.name for d in (REPO / "runs" / "backtest", REPO / "data" / "backtest") if d.exists()
                  for p in d.glob("holdout*"))
    res[729] = ("N-A（未跑）" if not hold else "FAIL",
                (f"保留段本批不跑；`runs/backtest/`＋`data/backtest/` 無 `holdout*` 檔 {'✓' if not hold else hold}；"
                "`backtest_stats.py --segment` 只收 train／valid"))

    # :730 三期間樣本數
    ev, ok = [], True
    for seg, c in cells:
        r, w = c["rows"], c["window"]
        exp = r["in_pool"] / w["seg_days"] * w["n_eff_index"]
        diff = r["counted"] / exp - 1
        ok &= abs(diff) <= SAMPLE_TOL
        ev.append(f"{seg}/{c['market']}/{c['horizon']} 計入 {r['counted']}（預估 池內{r['in_pool']}×{w['n_eff_index']}/{w['seg_days']}"
                  f"＝{exp:,.0f}，差 {diff * 100:+.2f}%）")
    res[730] = ("PASS" if ok else "FAIL", f"預估式（池內列數×合格日數÷段日數）為本層自定、非登錄書字面，容差 {SAMPLE_TOL:.0%}：" + "；".join(ev))

    out = []
    for ln, name in ROWS:
        st, evd = res[ln]
        out.append({"line": ln, "name": name, "status": st, "evidence": evd})
    return out


def as_text(rows: list[dict[str, Any]], date_tag: str, train: dict[str, Any], valid: dict[str, Any]) -> str:
    n_pass = sum(r["status"] == "PASS" for r in rows)
    n_fail = sum(r["status"] == "FAIL" for r in rows)
    n_na = len(rows) - n_pass - n_fail
    L = [(f"§16.5 回測日期與洩漏守門（v1.2.2 :{SPEC_TABLE_FIRST}-{SPEC_TABLE_LAST}，{len(rows)} 列）｜日期 {date_tag}｜"
         f"輸入 train_{train['date']}.json／valid_{valid['date']}.json（params_sha {valid['gate']['params_sha']}）"),
         f"結果：PASS {n_pass}／FAIL {n_fail}／N-A {n_na}（N-A＝不屬本層，標另案或已有工具；保留段未跑）", ""]
    for r in rows:
        L.append(f":{r['line']} {r['name']} | {r['status']} | {r['evidence']}")
    return "\n".join(L) + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="§16.5 守門清單逐列判定")
    ap.add_argument("--date", default=time.strftime("%Y-%m-%d"))
    ap.add_argument("--train", default=None)
    ap.add_argument("--valid", default=None)
    ap.add_argument("--calendar", default=str(REPO / "data" / "calendar_tpe.json"))
    ap.add_argument("--manifest", default=str(REPO / "data" / "backtest" / "manifest.json"))
    ap.add_argument("--out", default=None, help="預設 runs/backtest/gate_<date>.txt")
    ap.add_argument("--check", action="store_true", help="只比對、不寫入；內容會變就 rc=1")
    args = ap.parse_args(argv)
    try:
        out_dir = REPO / "runs" / "backtest"
        tp = Path(args.train) if args.train else out_dir / f"train_{args.date}.json"
        vp = Path(args.valid) if args.valid else out_dir / f"valid_{args.date}.json"
        train = json.loads(tp.read_text(encoding="utf-8"))
        valid = json.loads(vp.read_text(encoding="utf-8"))
        cal = json.loads(Path(args.calendar).read_text(encoding="utf-8"))["dates"]
        man = json.loads(Path(args.manifest).read_text(encoding="utf-8"))
        got = spec_rows(SPEC.read_text(encoding="utf-8"))
        _assert(got == ROWS, f"§16.5 原文列與 ROWS 不符：{[(a, b) for a, b in zip(got, ROWS) if a != b]}")
        rows = evaluate(train, valid, cal, man)
        text = as_text(rows, args.date, train, valid)
        out = Path(args.out) if args.out else out_dir / f"gate_{args.date}.txt"
        n_fail = sum(r["status"] == "FAIL" for r in rows)
        if args.check:
            if not out.exists() or out.read_text(encoding="utf-8") != text:
                print("[backtest_gate] 守門輸出與現檔不一致，請重跑", file=sys.stderr)
                return 1
            print(f"== 守門輸出一致（FAIL {n_fail}）")
            return 1 if n_fail else 0
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text, encoding="utf-8")
        print(text)
        print(f"== 已寫 {out}")
        return 1 if n_fail else 0
    except Exception as e:  # noqa: BLE001 — 任何例外一律 rc=2
        print(f"[backtest_gate 中止] {type(e).__name__}: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
