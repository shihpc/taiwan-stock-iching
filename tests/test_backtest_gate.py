"""`scripts/backtest_gate.py`：§16.5 守門清單（v1.2.2 `:711-730`，20 列）。

守：①列數與條件名由原文重數＝`ROWS`；②對已 commit 的兩段 json 跑 `evaluate` 零 FAIL、`--check` 與現檔一致；
③突變（purge 日數改錯、embargo 改錯、verdict 與區塊數矛盾、rank sha 改掉）→ 對應那一列 FAIL；④`--check` rc 語意。
本檔不讀 `data/backtest/*.csv.gz`，只讀 manifest（指紋欄）。
"""
from __future__ import annotations

import copy
import json
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import backtest_gate as BG

DATE = "2026-10-07"
RUNS = ROOT / "runs" / "backtest"
SPEC = ROOT / "spec" / "stock-iching-plan-v1.2.2.md"


@pytest.fixture(scope="module")
def inputs():
    train = json.loads((RUNS / f"train_{DATE}.json").read_text(encoding="utf-8"))
    valid = json.loads((RUNS / f"valid_{DATE}.json").read_text(encoding="utf-8"))
    cal = json.loads((ROOT / "data" / "calendar_tpe.json").read_text(encoding="utf-8"))["dates"]
    man = json.loads((ROOT / "data" / "backtest" / "manifest.json").read_text(encoding="utf-8"))
    return train, valid, cal, man


def test_spec_rows_recounted_from_source():
    text = SPEC.read_text(encoding="utf-8")
    rows = BG.spec_rows(text)
    assert len(rows) == 20 and rows == BG.ROWS
    lines = text.split("\n")
    assert lines[BG.SPEC_TABLE_FIRST - 5].startswith("### 16.5"), "表格起點前第四行（:707）應是 §16.5 標題"
    assert re.match(r"^\|---", lines[BG.SPEC_TABLE_FIRST - 2])
    # 表前（:710）是分隔列、表後（:731）不是表格列 → 20 列是完整計數
    assert not lines[BG.SPEC_TABLE_LAST].startswith("|")
    names = [n for _, n in rows]
    assert names[:2] == ["截斷政策一致性", "合法範圍"] and names[-1] == "三期間樣本數"


def test_evaluate_on_committed_jsons_no_fail(inputs):
    rows = BG.evaluate(*inputs)
    assert [(r["line"], r["name"]) for r in rows] == BG.ROWS
    assert not [r for r in rows if r["status"] == "FAIL"]
    st = {r["line"]: r["status"] for r in rows}
    assert all(st[ln] == "PASS" for ln in (719, 720, 721, 722, 723, 724, 725, 726, 727, 728, 730))
    assert all(st[ln] == "N-A" for ln in range(711, 719)) and st[729].startswith("N-A")
    assert "另案" in rows[0]["evidence"] or "已有工具" in rows[0]["evidence"]


def test_check_matches_committed_txt_and_rc():
    assert BG.main(["--date", DATE, "--check"]) == 0
    assert BG.main(["--date", "1999-01-01", "--check"]) == 2          # 檔案缺 → rc=2，不是 1


def test_check_rc1_when_stale(tmp_path):
    out = tmp_path / "gate.txt"
    out.write_text("stale\n", encoding="utf-8")
    assert BG.main(["--date", DATE, "--check", "--out", str(out)]) == 1


@pytest.mark.parametrize("mutate, line", [
    (lambda v: v["cells"][0]["window"].__setitem__("purge_days", 10), 721),
    (lambda v: v["cells"][0]["window"].__setitem__("embargo_days", 19), 723),
    (lambda v: v["cells"][2].__setitem__("verdict", "rejected"), 726),            # valid/twse/swing 5.47 < 8 卻非 insufficient
    (lambda v: v["gate"].__setitem__("rank_table_sha256", "0" * 64), 727),
    (lambda v: v["cells"][0]["rows"].__setitem__("counted", 1000), 730),
    (lambda v: v["cells"][0]["yearly"][0].__setitem__("role", "未見"), 725),
])
def test_mutation_turns_exactly_that_row_red(inputs, mutate, line):
    train, valid, cal, man = inputs
    v = copy.deepcopy(valid)
    mutate(v)
    rows = BG.evaluate(train, v, cal, man)
    bad = [r["line"] for r in rows if r["status"] == "FAIL"]
    assert bad == [line], bad


def test_segment_mismatch_rejected(inputs):
    _train, valid, cal, man = inputs
    with pytest.raises(BG.GateError):
        BG.evaluate(valid, valid, cal, man)
