"""`scripts/t717_appendix.py`：`:717` 八項重驗結果 → 登錄書附錄 B（裁定 #62）。

守的四件事：
① **附錄內容＝從報告重新產生的結果**（附錄與 JSON 不可能脫鉤）；
② `rank_table.py` 重寫附錄 A 時**不會吃掉**附錄 B；
③ 附錄裡的定性句（「全部在 market」「① 全部低於門檻」「分母相同」）一旦被資料推翻就**中止**，
   不會留下一句被自己下面的表推翻的字（附錄 A 的 F1／G1 教訓）；
④ ②⑤ 未經使用者確認時標「待確認」，不得被寫成「已確認」。

期待值一律在本檔**獨立寫死**，不由被測函式產生（§20.1 末的判準 ③）。
"""
from __future__ import annotations

import copy
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import rank_table as RT  # noqa: E402
import t717_appendix as TA  # noqa: E402

REPORT = ROOT / "runs" / "t717" / "report_2026-09-14.json"
PREREG = ROOT / "docs" / "pre-registration.md"


@pytest.fixture()
def rep() -> dict:
    return json.loads(REPORT.read_text(encoding="utf-8"))


def _block(doc: str) -> str:
    i, j = doc.index(TA.BEGIN), doc.index(TA.END)
    return doc[i:j + len(TA.END)]


# ---- ① 附錄＝重新產生 ----

def test_check_mode_passes_on_repo():
    assert TA.main(["--check"]) == 0


def test_check_mode_detects_hand_edit(tmp_path):
    doc = PREREG.read_text(encoding="utf-8")
    bad = doc.replace("超標合計 **60** 格", "超標合計 **59** 格")
    assert bad != doc
    p = tmp_path / "prereg.md"
    p.write_text(bad, encoding="utf-8")
    assert TA.main(["--check", "--prereg", str(p)]) == 1
    assert p.read_text(encoding="utf-8") == bad  # --check 不寫入


def test_write_is_idempotent_and_single_marker(tmp_path):
    p = tmp_path / "prereg.md"
    p.write_text(PREREG.read_text(encoding="utf-8"), encoding="utf-8")
    assert TA.main(["--prereg", str(p)]) == 0
    once = p.read_text(encoding="utf-8")
    assert TA.main(["--prereg", str(p)]) == 0
    assert p.read_text(encoding="utf-8") == once
    assert once.count(TA.BEGIN) == 1 and once.count(TA.END) == 1
    assert once.count("### 未超標的三項") == 1


def test_numbers_follow_report(rep):
    """數字由 JSON 算出：改報告，附錄跟著變（期待值獨立寫死）。"""
    rep["rows_matched"] = 1_000_000
    rep["market_rows_matched"] = 2_345
    out = TA.build(rep)
    assert "個股 1,000,000 列＋大盤 2,345 列＝1,002,345 列" in out


def test_hexagram_prediction_is_one_minus_line_diff_to_sixth(rep):
    """⑥ 的 (1−①)^6：0.95^6＝0.735091890625 → 73.51%（本檔手算，不呼叫被測函式）。"""
    for r in rep["items"]["1_line_state_diff_rate"]:
        if (r["scope"], r["market"], r["horizon"]) == ("stock", "twse", "short"):
            r["diff_rate"] = 0.05
    out = TA.build(rep)
    row = next(ln for ln in out.splitlines() if ln.startswith("| stock/twse/short | 5.00% |"))
    assert "| 73.51% |" in row


def test_real_report_key_numbers():
    """對真實報告的幾個關鍵數字（2026-09-23 從 JSON 手查、獨立寫死）。"""
    block = _block(PREREG.read_text(encoding="utf-8"))
    assert "個股 8,883,228 列＋大盤 9,768 列＝8,892,996 列" in block
    assert "超標合計 **60** 格" in block
    assert "| market/tpex/mid | 268 | 313 | +45 | 16.79% |" in block
    assert "| stock/tpex/mid | `floor_applied` | 69.04% | 79.13% | +10.08 個百分點 | 168,736 | ● |" in block


# ---- ② 附錄 A 重寫不吃掉附錄 B ----

def test_rank_table_splice_preserves_appendix_b():
    doc = PREREG.read_text(encoding="utf-8")
    before = _block(doc)
    new = RT.splice_markdown(doc, "## 附錄 A：佔位\n")
    assert _block(new) == before
    assert new.index(RT.END) < new.index(TA.BEGIN)


def test_splice_half_marker_refuses():
    with pytest.raises(TA.AppendixError):
        TA.splice(f"x\n{TA.BEGIN}\ny\n", "z\n")
    with pytest.raises(TA.AppendixError):
        TA.splice(f"x\n{TA.END}\n", "z\n")


# ---- ③ 定性句被資料推翻就中止 ----

def test_guard_hysteresis_stock_flag(rep):
    rep["over_threshold"].append({"item": "2_hysteresis_flips", "scope": "stock", "market": "twse",
                                  "horizon": "short", "direction": "n/a", "field": "rel_diff", "value": 0.2})
    with pytest.raises(TA.AppendixError, match="②"):
        TA.build(rep)


def test_guard_line_diff_over_threshold(rep):
    rep["items"]["1_line_state_diff_rate"][0]["diff_rate"] = 0.1001
    with pytest.raises(TA.AppendixError, match="①"):
        TA.build(rep)


def test_guard_line_diff_at_threshold_is_ok(rep):
    """門檻是「> 10%」：恰等於不算超標（邊界）。"""
    rep["items"]["1_line_state_diff_rate"][0]["diff_rate"] = 0.10
    TA.build(rep)


def test_guard_floor_denominator(rep):
    r = next(x for x in rep["items"]["8_binding_rate"] if x["column"] == "floor_applied")
    r["n_after"] += 1
    with pytest.raises(TA.AppendixError, match="⑧"):
        TA.build(rep)


def test_main_returns_2_on_guard(rep, tmp_path):
    rep["items"]["1_line_state_diff_rate"][0]["diff_rate"] = 0.5
    rp = tmp_path / "r.json"
    rp.write_text(json.dumps(rep), encoding="utf-8")
    p = tmp_path / "prereg.md"
    p.write_text(PREREG.read_text(encoding="utf-8"), encoding="utf-8")
    assert TA.main(["--report", str(rp), "--prereg", str(p)]) == 2


# ---- ④ 確認狀態 ----

def test_unconfirmed_items_marked_pending():
    block = _block(PREREG.read_text(encoding="utf-8"))
    assert block.count("是否屬預期行為：待使用者確認") == 2
    assert block.count("已確認屬預期行為（裁定 #62）") == 3


def test_confirmed_flag_changes_text(rep, monkeypatch):
    conf = copy.deepcopy(TA.CONFIRMED)
    conf["2_hysteresis_flips"] = "#99"
    monkeypatch.setattr(TA, "CONFIRMED", conf)
    out = TA.build(rep)
    assert "已確認屬預期行為（裁定 #99）" in out
    assert out.count("是否屬預期行為：待使用者確認") == 1
