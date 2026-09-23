"""`scripts/t717_appendix.py`：`:717` 八項重驗結果 → 登錄書附錄 B（裁定 #62）。

守的四件事：
① **附錄內容＝從報告重新產生的結果**（附錄與 JSON 不可能脫鉤）；
② `rank_table.py` 重寫附錄 A 時**不會吃掉**附錄 B；
③ 附錄裡會隨資料變真變假的定性句（十二道守門，見 P3-CALIBRATION §22）一旦被資料推翻就**中止**，
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
    bad = doc.replace("超標合計 **60** 處", "超標合計 **59** 格")
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
    assert once.count("### 未超標的 3 項") == 1


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
    assert "超標合計 **60** 處" in block
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
#
# 各守門測試都要讓報告**自洽**（改了原值就重列 over_threshold），否則最先中止的會是開頭的
# 「超標集合＝原值重算」那道，測不到想守的那一道（紅了要問紅的是不是那一支）。
# `_relist` 在本檔獨立重寫，不呼叫被測的 `recompute_over`。

def _relist(rep: dict) -> dict:
    thr, out = rep["big_diff_threshold"], []
    for name, rows in rep["items"].items():
        for r in rows:
            base = {"item": name, "scope": r["scope"], "market": r["market"],
                    "horizon": r["horizon"], "direction": r["direction"]}
            for f in ("diff_rate", "diff", "rel_diff", "tv_distance"):
                if r.get(f) is not None and abs(r[f]) > thr:
                    out.append({**base, "field": f, "value": r[f]})
            for f in ("jaccard", "same_rank_rate", "king_wen_same_rate", "future_king_wen_same_rate"):
                if r.get(f) is not None and 1.0 - r[f] > thr:
                    out.append({**base, "field": f, "value": r[f]})
    rep["over_threshold"] = out
    return rep


def test_relist_reproduces_real_list(rep):
    """測試用的 `_relist` 對真實報告重列出來的清單＝報告原本的清單（`_relist` 本身可信的前提）。"""
    key = lambda h: tuple(h[k] for k in TA._OVER_KEY)  # noqa: E731
    orig = sorted(map(key, rep["over_threshold"]))
    assert sorted(map(key, _relist(copy.deepcopy(rep))["over_threshold"])) == orig
    assert len(orig) == 60


def test_guard_over_list_matches_values(rep):
    """驗收 Q2-1：② 個股格原值 30% 但沒列進超標清單——附錄會同時寫「全在 market」與「個股 30%」。"""
    r = next(x for x in rep["items"]["2_hysteresis_flips"] if x["scope"] == "stock")
    r["rel_diff"] = 0.3
    with pytest.raises(TA.AppendixError, match="原值重算"):
        TA.build(rep)


def test_guard_over_list_extra_entry(rep):
    """反方向：清單多列一筆原值沒超標的（⑧ twse/mid 下限差其實只有幾個百分點）。"""
    rep["over_threshold"].append({"item": "8_binding_rate", "scope": "stock", "market": "twse",
                                  "horizon": "mid", "direction": "n/a", "field": "diff", "value": 0.2})
    with pytest.raises(TA.AppendixError, match="原值重算"):
        TA.build(rep)


def test_guard_params_sha_differ(rep):
    rep["after"]["params_sha"] = rep["before"]["params_sha"]
    with pytest.raises(TA.AppendixError, match="params_sha"):
        TA.build(rep)


def test_guard_hysteresis_stock_flag(rep):
    r = next(x for x in rep["items"]["2_hysteresis_flips"] if x["scope"] == "stock")
    r["rel_diff"] = 0.3
    with pytest.raises(TA.AppendixError, match="②"):
        TA.build(_relist(rep))


def test_guard_line_diff_over_threshold(rep):
    """① 超標：由「超標項目＝EXPLAINED」擋下（① 不在其中）。"""
    rep["items"]["1_line_state_diff_rate"][0]["diff_rate"] = 0.1001
    with pytest.raises(TA.AppendixError, match="逐項說明節"):
        TA.build(_relist(rep))


def test_guard_line_diff_at_threshold_is_ok(rep):
    """門檻是「> 10%」：恰等於不算超標（邊界）。

    其他守門（⑤ 須高於 ①、⑥ 須貼近 (1−①)^6）會被同一個改動牽動，所以一併把 ⑤⑥ 調成相容的值，
    讓這支只量 ① 的邊界。0.9^6＝0.531441（本檔手算）。⑥ 改值後 1−0.531441 仍 >10%，清單要重列。
    """
    r1 = rep["items"]["1_line_state_diff_rate"][0]
    r1["diff_rate"] = 0.10
    for r in rep["items"]["5_trigram_state_diff_rate"]:
        r["diff_rate"] = max(r["diff_rate"], 0.105)
    for r in rep["items"]["6_hexagram_agreement"]:
        if (r["scope"], r["market"], r["horizon"]) == (r1["scope"], r1["market"], r1["horizon"]):
            r["king_wen_same_rate"] = r["future_king_wen_same_rate"] = 0.531441
    TA.build(_relist(rep))


def test_guard_floor_denominator(rep):
    r = next(x for x in rep["items"]["8_binding_rate"] if x["column"] == "floor_applied")
    r["n_after"] += 1
    with pytest.raises(TA.AppendixError, match="⑧"):
        TA.build(rep)


def test_guard_floor_only_mid(rep):
    """驗收 Q2-2：出現短期的 floor_applied 列，「下限只在中期初爻」不成立。"""
    r = copy.deepcopy(next(x for x in rep["items"]["8_binding_rate"] if x["column"] == "floor_applied"))
    r["horizon"] = "short"
    rep["items"]["8_binding_rate"].append(r)
    with pytest.raises(TA.AppendixError, match="中期"):
        TA.build(_relist(rep))


def test_overheated_counts_stock_only(rep):
    """驗收 Q2-3：大盤的 overheated 列不得算進「個股 `overheated`（N 格）」。"""
    n_stock = sum(1 for x in rep["items"]["3_flag_hit_rate"] if x["flag"] == "overheated" and x["scope"] == "stock")
    assert n_stock == 6
    r = copy.deepcopy(next(x for x in rep["items"]["3_flag_hit_rate"] if x["flag"] == "overheated"))
    r["scope"] = "market"
    rep["items"]["3_flag_hit_rate"].append(r)
    assert "個股 `overheated`（6 格）" in TA.build(rep)


def test_guard_zero_diff_flags(rep):
    r = next(x for x in rep["items"]["3_flag_hit_rate"] if x["flag"] == "F-高波動")
    r["diff"] = 1e-12
    with pytest.raises(TA.AppendixError, match="F-高波動"):
        TA.build(rep)


def test_guard_trigram_above_line(rep):
    rep["items"]["5_trigram_state_diff_rate"][0]["diff_rate"] = 0.05
    with pytest.raises(TA.AppendixError, match="⑤"):
        TA.build(_relist(rep))


def test_guard_market_flips_increase(rep):
    r = next(x for x in rep["items"]["2_hysteresis_flips"] if x["scope"] == "market" and x["rel_diff"] <= 0.1)
    r["after"] = r["before"]
    r["rel_diff"] = 0.0
    with pytest.raises(TA.AppendixError, match="②"):
        TA.build(_relist(rep))


def test_guard_hexagram_gap(rep):
    """前瞻式之卦也要守：只守主卦時，之卦偏離 (1−①)^6 不會中止。"""
    r = rep["items"]["6_hexagram_agreement"][0]
    r["future_king_wen_same_rate"] -= 0.05
    with pytest.raises(TA.AppendixError, match="⑥"):
        TA.build(_relist(rep))


def test_guard_hexagram_gap_at_limit_is_ok(rep, monkeypatch):
    """差恰等於 HEX_GAP_MAX 不中止（邊界）。

    浮點下「pred − 0.03」減回去不會恰好是 0.03，所以反過來做：本檔自己算出真實報告 24 個值的最大
    |差|，把 HEX_GAP_MAX 設成**恰等於它**——`<=` 過、`<` 會中止，這支才分得開兩者。
    """
    line = {(x["scope"], x["market"], x["horizon"]): x["diff_rate"] for x in rep["items"]["1_line_state_diff_rate"]}
    worst = 0.0
    for r in rep["items"]["6_hexagram_agreement"]:
        pred = (1 - line[(r["scope"], r["market"], r["horizon"])]) ** 6
        worst = max(worst, abs(r["king_wen_same_rate"] - pred), abs(r["future_king_wen_same_rate"] - pred))
    assert 0.02 < worst < 0.03
    monkeypatch.setattr(TA, "HEX_GAP_MAX", worst)
    TA.build(rep)
    monkeypatch.setattr(TA, "HEX_GAP_MAX", worst * (1 - 1e-12))
    with pytest.raises(TA.AppendixError, match="⑥"):
        TA.build(rep)


def test_guard_floor_after_higher(rep):
    r = next(x for x in rep["items"]["8_binding_rate"]
             if x["column"] == "floor_applied" and (x["market"], x["horizon"]) == ("tpex", "mid"))
    r["after"] = r["before"] - 0.11
    r["diff"] = -0.11
    with pytest.raises(TA.AppendixError, match="⑧ 超標格的後側"):
        TA.build(_relist(rep))


def test_guard_structure_extra_item(rep):
    """③ 出現超標：「未超標」清單會與總覽矛盾（驗收 R2-1 實測過），必須中止。"""
    next(x for x in rep["items"]["3_flag_hit_rate"] if x["flag"] == "F-高波動")["diff"] = 0.2
    with pytest.raises(TA.AppendixError, match="逐項說明節"):
        TA.build(_relist(rep))


def test_guard_structure_missing_item(rep):
    """⑤ 零超標：⑤ 節會寫出空泛的「超標 0 處」說明，必須中止（⑤ 仍高於 ① 最大 8.35%）。"""
    for r in rep["items"]["5_trigram_state_diff_rate"]:
        r["diff_rate"] = 0.09
    with pytest.raises(TA.AppendixError, match="逐項說明節"):
        TA.build(_relist(rep))


def test_guard_binding_cap_over(rep):
    """封頂列超標（驗收 R2-2 實測過）：本節只說明下限，必須中止，不得把 ● 標錯列。"""
    r = next(x for x in rep["items"]["8_binding_rate"]
             if x["column"] == "overheat_cap_applied" and (x["market"], x["horizon"]) == ("tpex", "mid"))
    r["diff"] = 0.2
    with pytest.raises(TA.AppendixError, match="overheat_cap_applied"):
        TA.build(_relist(rep))


def test_binding_mark_single(rep):
    """⑧ 表只列 floor_applied，● 恰一個、落在 tpex/mid。

    （「依列 vs 依格」標 ● 在本表是等價的——表裡沒有封頂列；防標錯列靠的是封頂不得超標那道守門。）
    """
    out = TA.build(rep)
    rows = [ln for ln in out.splitlines() if ln.startswith("| stock/tpex/mid | `floor_applied`")]
    assert len(rows) == 1 and rows[0].endswith("| ● |")
    assert sum(ln.endswith("| ● |") for ln in out.splitlines()) == 1


def test_main_returns_2_on_guard(rep, tmp_path):
    rep["items"]["1_line_state_diff_rate"][0]["diff_rate"] = 0.5
    rp = tmp_path / "r.json"
    rp.write_text(json.dumps(rep), encoding="utf-8")
    p = tmp_path / "prereg.md"
    p.write_text(PREREG.read_text(encoding="utf-8"), encoding="utf-8")
    assert TA.main(["--report", str(rp), "--prereg", str(p)]) == 2


# ---- ④ 確認狀態 ----

def _section(block: str, title: str) -> str:
    i = block.index(f"### {title}")
    j = block.find("\n### ", i + 1)
    return block[i:] if j < 0 else block[i:j]


def test_unconfirmed_items_marked_pending():
    """綁定到**哪一節**，不只數次數（驗收 S1：⑤⑦ 對調後只數次數會全綠）。"""
    block = _block(PREREG.read_text(encoding="utf-8"))
    for title in ("② 遲滯翻爻次數", "⑤ 內外卦方向判定差異率"):
        sec = _section(block, title)
        assert "是否屬預期行為：待使用者確認" in sec and "已確認屬預期行為" not in sec, title
    for title in ("⑥ 主卦／前瞻式之卦一致率", "⑦ 候選名單與名次重疊", "⑧ 封頂／下限觸發率"):
        sec = _section(block, title)
        assert "已確認屬預期行為（裁定 #62）" in sec and "待使用者確認" not in sec, title
    assert block.count("是否屬預期行為：待使用者確認") == 2


def test_confirmed_flag_changes_text(rep, monkeypatch):
    conf = copy.deepcopy(TA.CONFIRMED)
    conf["2_hysteresis_flips"] = "#99"
    monkeypatch.setattr(TA, "CONFIRMED", conf)
    out = TA.build(rep)
    assert "已確認屬預期行為（裁定 #99）" in out
    assert out.count("是否屬預期行為：待使用者確認") == 1


def test_recompute_over_boundaries():
    """重算規則的邊界：差異型取 |值| > 門檻、一致率型取 1−值 > 門檻，恰等於都不算。

    門檻取 0.5：0.5 與 1−0.5 在浮點下都精確，邊界才測得到（0.1 下 1−0.9 不是 0.1）。期待值本檔寫死。
    """
    cell = {"scope": "stock", "market": "twse", "horizon": "short", "direction": "n/a"}
    items = {"x": [{**cell, "diff": 0.5}, {**cell, "diff": -0.75}, {**cell, "jaccard": 0.5},
                   {**cell, "same_rank_rate": 0.25}, {**cell, "tv_distance": None}]}
    got = TA.recompute_over(items, 0.5)
    assert got == {("x", "stock", "twse", "short", "n/a", "diff", -0.75): 1,
                   ("x", "stock", "twse", "short", "n/a", "same_rank_rate", 0.25): 1}
