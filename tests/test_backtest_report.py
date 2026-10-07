"""`scripts/backtest_report.py`：`docs/P3-BACKTEST-VALID.md` 由 json＋守門 txt 整檔生成。

守：①現檔＝重新產生（`--check` rc=0）；②`--check` rc 語意 0／1／2；③定性句的守門——T3 四格改成非 insufficient、區塊數改錯、
守門 txt 出現 FAIL、兩段指紋不同 → rc=2；④字樣：無交易方向字樣、必要句存在、「區塊數量近似值」用語、tag／commit 記載；
⑤tblcheck 0 問題。期待字串在本檔獨立寫死。
"""
from __future__ import annotations

import copy
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import backtest_report as BR

DATE = "2026-10-07"
RUNS = ROOT / "runs" / "backtest"
MD = ROOT / "docs" / "P3-BACKTEST-VALID.md"


@pytest.fixture(scope="module")
def inputs():
    train = json.loads((RUNS / f"train_{DATE}.json").read_text(encoding="utf-8"))
    valid = json.loads((RUNS / f"valid_{DATE}.json").read_text(encoding="utf-8"))
    gate = (RUNS / f"gate_{DATE}.txt").read_text(encoding="utf-8")
    return train, valid, gate


def test_report_equals_regenerated(inputs):
    train, valid, gate = inputs
    assert MD.read_text(encoding="utf-8") == BR.build(train, valid, gate, DATE)
    assert BR.main(["--date", DATE, "--check"]) == 0


def test_check_rc_semantics(tmp_path):
    out = tmp_path / "x.md"
    out.write_text("stale\n", encoding="utf-8")
    assert BR.main(["--date", DATE, "--check", "--out", str(out)]) == 1
    assert BR.main(["--date", "1999-01-01", "--check"]) == 2


def test_wording_and_required_sentences():
    md = MD.read_text(encoding="utf-8")
    assert md.startswith(BR.MARKER) and "不得手改" in md
    for w in (*("買", "賣", "做多", "做空"), "獨立區塊數"):
        assert w not in md, w
    for s in ("IC ≥ 0.03、NW t ≥ 2.0 是未經多重比較校正的名目值", "波段／中期證據不足是裁定 T3 預期結果",
              "候選名單（§1.4 次要②，頂十分位代理）", "`prereg-v2`", "`59e03f1`", "`c1240f2`", "區塊數量近似值",
              "K＝216", "本次 0 格實算", "不在 K 內", "（代理）", "20 列", "seed 42", "1,000 次", "這不是保留段結果"):
        assert s in md, s
    # 六格 verdict 首頁表：驗證段波段／中期四格 insufficient
    for mk in ("上市 twse", "上櫃 tpex"):
        for hz in ("波段（20）", "中期（40）"):
            assert f"| 驗證段 | {mk} | {hz} |" in md
    assert md.count("**insufficient**") == 6         # 驗證段 4 格＋訓練段中期 2 格


def test_tblcheck_zero_problems():
    r = subprocess.run([sys.executable, str(ROOT / "spec" / "tools" / "tblcheck.py"), str(MD)], capture_output=True, text=True, check=False)
    assert r.returncode == 0, r.stdout


@pytest.mark.parametrize("mutate, msg", [
    (lambda t, v, g: v["cells"][2].__setitem__("verdict", "rejected"), "verdict 與區塊數不一致"),
    (lambda t, v, g: v["cells"][2]["window"].__setitem__("n_blocks_t5", 8.5), "區塊數 ≠ 登錄書表"),
    (lambda t, v, g: v["cells"][0]["window"].__setitem__("purge_days", 10), "purge 日數"),
    (lambda t, v, g: v["gate"].__setitem__("params_sha", "ffffffffffff"), "params_sha"),
    (lambda t, v, g: v["params"].__setitem__("seed", 7), "seed"),
])
def test_assert_guards_rc2(inputs, mutate, msg):
    train, valid, gate = inputs
    t, v = copy.deepcopy(train), copy.deepcopy(valid)
    mutate(t, v, gate)
    with pytest.raises(BR.ReportError, match=msg):
        BR.build(t, v, gate, DATE)


def test_gate_fail_blocks_report(inputs):
    train, valid, gate = inputs
    bad = gate.replace(":721 雙邊界 purge | PASS |", ":721 雙邊界 purge | FAIL |")
    assert bad != gate
    with pytest.raises(BR.ReportError, match="FAIL"):
        BR.build(train, valid, bad, DATE)
