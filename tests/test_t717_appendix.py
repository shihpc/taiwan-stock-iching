"""`scripts/t717_appendix.py`：`:717` 八項重驗結果 → 登錄書附錄 B（裁定 #62）。

守的四件事：
① **附錄內容＝從報告重新產生的結果**（附錄與 JSON 不可能脫鉤）；
② `rank_table.py` 重寫附錄 A 時**不會吃掉**附錄 B；
③ 附錄裡會隨資料變真變假的定性句（十七道守門，見 P3-CALIBRATION §22）一旦被資料推翻就**中止**，
   不會留下一句被自己下面的表推翻的字（附錄 A 的 F1／G1 教訓）；
④ 確認狀態逐節綁定到正確的裁定編號（②⑤＝#63、⑥⑦⑧＝#62）；未確認時標「待確認」，不得被寫成「已確認」；
⑤ **附錄綁定到確切的報告版本**（2026-09-27 起）：報告檔的 sha256 全文與前後側 `params_sha` 獨立寫死，
   且附錄開頭的重確認狀態列（`RECONFIRM_NOTE`）必須出現、須寫明已依重生數字重確認（2026-09-27；2026-10-06 依 v2 數字沿用 #62／#63，PR-E），
   不得再出現「待使用者確認」。**2026-10-06 prereg-v2 PR-D2**：報告換成 Hetzner `bc516b9` 的 v2 報告 `report_2026-10-02.json`
   （比對 1,640 日迄 10-02；v1 `4294037` 的 `report_2026-09-14.json` 為 1,628 日迄 09-14——範圍不同、八項數字**不**逐位相同，
   釘值全部換新、舊值留註解），並守附錄開頭的「範圍差異」段（`V1_DAYS`／`V1_LAST`／`V2_DAYS`／`V2_LAST` 與報告互鎖）。

期待值一律在本檔**獨立寫死**，不由被測函式產生（§20.1 末的判準 ③）。
"""
from __future__ import annotations

import copy
import hashlib
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import rank_table as RT  # noqa: E402
import t717_appendix as TA  # noqa: E402

#: prereg-v2（2026-10-06 PR-D2）：v2 db（`p2-score-engine-3`）的 t717 報告；舊值 `report_2026-09-14.json`（v1，仍在 repo 供對照）。
REPORT = ROOT / "runs" / "t717" / "report_2026-10-02.json"
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
    """對真實報告的幾個關鍵數字（2026-09-27 從 `4294037` 的 JSON 手查、獨立寫死；#68／#69 換模型後重跑；
    **2026-10-06 prereg-v2 PR-D2 換成 `bc516b9` 的 `report_2026-10-02.json`**：比對範圍 1,628 日 → 1,640 日，七行釘值全部換新、
    行尾註解為 v1 09-14 報告的值；超標合計 60 與超標集合的鍵集合與 v1 相同（P3-CALIBRATION §37 PR-D2 節）。"""
    block = _block(PREREG.read_text(encoding="utf-8"))
    assert "個股 8,953,299 列＋大盤 9,840 列＝8,963,139 列" in block                      # v1：8,883,228＋9,768＝8,892,996
    assert "超標合計 **60** 處" in block                                                   # v1：60（鍵集合相同）
    assert "| market/tpex/mid | 272 | 316 | +44 | 16.18% |" in block                      # v1：268 | 313 | +45 | 16.79%
    assert "| stock/tpex/short | 11.66% |" in block and "| stock/tpex/swing | 10.79% |" in block   # v1：11.64%／10.77%
    assert "| stock/tpex/short/short | 72.42% | 39.97% | 1,537 | 40 | 75 |" in block      # v1：72.53% | 40.05% | 1,525 | 40 | 75
    assert "| stock/tpex/mid | `floor_applied` | 68.88% | 79.01% | +10.13 個百分點 | 170,212 | ● |" in block   # v1：68.98% | 79.03% | +10.05 | 168,429
    assert "| stock/twse/mid | `floor_applied` | 73.73% | 78.95% | +5.22 個百分點 | 210,905 |  |" in block      # v1：73.94% | 79.11% | +5.17 | 208,691


# ---- ⑤ 附錄綁定到確切的報告版本 ----

#: `git show bc516b9:runs/t717/report_2026-10-02.json | sha256sum`（2026-10-06 實算、獨立寫死）。
#: v1：`git show 4294037:runs/t717/report_2026-09-14.json | sha256sum`＝`ce1a06e6a00d72d3c80289bfb54fc6ba20294b9b4b87139f74e2f8b78722717e`（2026-09-27）。
REPORT_SHA256 = "41a68416155211dbfda7d65cb8b810bb45698722bcb2c60524e696204188ed13"


def test_report_bound_to_bc516b9(rep):
    """報告檔＝Hetzner `bc516b9`（`hetzner/t717-2026-10-02`）那一份（sha256 全文）；前側 `--uncalibrated`／後側 `params_sha` 為
    裁定 #71（prereg-v2）後的值；比對 1,640 日 2020-01-02～2026-10-02。
    竄改報告任一數字 → sha 不符（另有 `test_check_mode_passes_on_repo` 抓附錄過期）。
    v1（`4294037`）：後側 `8ca174ee8bc7`／前側 `ef44809db803`／`days` 1628／`dates.last` 2026-09-14。"""
    assert hashlib.sha256(REPORT.read_bytes()).hexdigest() == REPORT_SHA256
    assert rep["after"]["params_sha"] == "cb3f2d905846"
    assert rep["before"]["params_sha"] == "59d5ef0e36eb"
    assert rep["data_version"] == "fm-20260911-01" and rep["days"] == 1640
    assert rep["dates"] == {"first": "2020-01-02", "last": "2026-10-02", "n": 1640}


def test_appendix_header_shows_new_params_sha():
    block = _block(PREREG.read_text(encoding="utf-8"))
    assert "`params_sha=59d5ef0e36eb`，`--uncalibrated`" in block and "`params_sha=cb3f2d905846`" in block
    assert "從 `runs/t717/report_2026-10-02.json` 生成" in block
    # #68 前（`c7385e78cb9f`／`b98325c61e70`）與 v1（`8ca174ee8bc7`／`ef44809db803`）兩側 sha、v1 的 09-14 檔名都不得殘留
    assert "c7385e78cb9f" not in block and "b98325c61e70" not in block
    assert "8ca174ee8bc7" not in block and "ef44809db803" not in block
    assert "report_2026-09-14" not in block


def test_reconfirm_note_present_and_pending(rep):
    """`RECONFIRM_NOTE` 出現在附錄開頭（第一個 `###` 之前）、寫明「已於 2026-10-06 依 v2 數字（1,640 日）沿用重確認」（使用者 2026-10-06 裁示
    「沿用 #62／#63，不另立號」：型態不變、超標 60 格鍵集合與 v1 相同），且「待使用者確認」不再出現；生成結果與 repo 內附錄都要有。
    拿掉常數或那一行 → 本測試紅（常數消失時 import／build 先炸）。
    舊釘值（2026-09-27～10-06，prereg-v2 PR-E 前）：「已於 2026-09-27 依重生數字重確認」／「超標集合 60 格與 #68 前報告相同」。"""
    assert "已於 2026-10-06 依 v2 數字（1,640 日）沿用重確認" in TA.RECONFIRM_NOTE
    assert "沿用 #62／#63，不另立號" in TA.RECONFIRM_NOTE
    assert "超標集合 60 格的鍵集合與 v1 報告（1,628 日）相同" in TA.RECONFIRM_NOTE
    assert "前次為 2026-09-27 依 #68／#69 重生數字重確認" in TA.RECONFIRM_NOTE
    assert "待使用者確認" not in TA.RECONFIRM_NOTE
    for text in (TA.build(rep), _block(PREREG.read_text(encoding="utf-8"))):
        assert TA.RECONFIRM_NOTE in text
        assert text.index(TA.RECONFIRM_NOTE) < text.index("\n### ")
        assert "> **重確認狀態**：" in text
        assert "待使用者確認" not in text


def test_v2_scope_note_present_and_guarded(rep):
    """附錄開頭（第一個 `###` 之前）必須有「範圍差異」段：寫明 1,640 日（迄 10-02）vs v1 1,628 日（迄 09-14）、多 12 個交易日、
    不可與 v1 逐位比對；生成結果與 repo 內附錄都要有。常數與報告互鎖：報告的 `days`／`dates.n`／`dates.last` 任一不是常數寫的值
    → 中止（換了報告沒改常數時不會寫出一句與下方數字對不上的範圍說明）。期待值獨立寫死。"""
    assert (TA.V1_DAYS, TA.V1_LAST, TA.V2_DAYS, TA.V2_LAST) == (1628, "2026-09-14", 1640, "2026-10-02")
    for text in (TA.build(rep), _block(PREREG.read_text(encoding="utf-8"))):
        i = text.index("> **範圍差異（prereg-v2，")
        assert i < text.index("\n### ")
        para = text[i:text.index("\n", i)]
        assert "**1,640 日**（迄 2026-10-02）" in para and "1,628 日（迄 2026-09-14）" in para and "多 12 個交易日" in para
        assert "不可與 v1 直接逐位比對" in para and "docs/pre-registration-v1.md" in para and "§37" in para
        assert "逐位相同" not in para.split("附錄 A／C")[0]      # 對 v1 附錄 B 不得宣稱逐位相同
    for mutate in (lambda r: r.__setitem__("days", 1628),
                   lambda r: r["dates"].__setitem__("n", 1628),
                   lambda r: r["dates"].__setitem__("last", "2026-09-14")):
        r = copy.deepcopy(rep)
        mutate(r)
        with pytest.raises(TA.AppendixError, match="範圍註記"):
            TA.build(r)


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
    r = next(x for x in rep["items"]["3_flag_hit_rate"] if x["flag"] == "F-高波動")
    r["after"], r["diff"] = r["before"] + 0.2, 0.2
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
    r["after"], r["diff"] = r["before"] + 0.2, 0.2
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


def test_confirmation_bound_per_section():
    """綁定到**哪一節、哪一個裁定**，不只數次數（驗收 S1：⑤⑦ 對調後只數次數會全綠）。
    `CONFIRMED` 本身也獨立寫死比對——改任一組裁定號時，不只 `--check` 紅，這裡也直接紅。"""
    assert TA.CONFIRMED == {"2_hysteresis_flips": "#63", "5_trigram_state_diff_rate": "#63",
                            "6_hexagram_agreement": "#62", "7_candidate_overlap": "#62", "8_binding_rate": "#62"}
    block = _block(PREREG.read_text(encoding="utf-8"))
    for title, ruling in (("② 遲滯翻爻次數", "#63"), ("⑤ 內外卦方向判定差異率", "#63"),
                          ("⑥ 主卦／前瞻式之卦一致率", "#62"), ("⑦ 候選名單與名次重疊", "#62"),
                          ("⑧ 封頂／下限觸發率", "#62")):
        sec = _section(block, title)
        assert f"已確認屬預期行為（裁定 {ruling}）" in sec and "待使用者確認" not in sec, title
    # 各節的「是否屬預期行為：待使用者確認」一句都不得出現；附錄開頭的重確認狀態列（`RECONFIRM_NOTE`）另由
    # `test_reconfirm_note_present_and_pending` 守（2026-09-27 起也已是「已重確認」）。
    assert "是否屬預期行為：待使用者確認" not in block


def test_confirmed_flag_changes_text(rep, monkeypatch):
    """把 ② 改回未確認：只有 ② 那一節變「待使用者確認」。"""
    conf = copy.deepcopy(TA.CONFIRMED)
    conf["2_hysteresis_flips"] = None
    monkeypatch.setattr(TA, "CONFIRMED", conf)
    out = TA.build(rep)
    assert "是否屬預期行為：待使用者確認" in _section(out, "② 遲滯翻爻次數")
    assert out.count("是否屬預期行為：待使用者確認") == 1
    assert "已確認屬預期行為（裁定 #63）" in _section(out, "⑤ 內外卦方向判定差異率")


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


def test_guard_binding_unknown_column(rep):
    """驗收第四輪反例：⑧ 多一種欄 other_cap 且超標、清單也列了（報告自洽）——舊版照印「超標 2 處」只標 1 個 ●。"""
    r = copy.deepcopy(next(x for x in rep["items"]["8_binding_rate"]
                           if x["column"] == "overheat_cap_applied" and (x["market"], x["horizon"]) == ("twse", "short")))
    r["column"], r["after"], r["diff"] = "other_cap", r["before"] + 0.2, 0.2
    rep["items"]["8_binding_rate"].append(r)
    with pytest.raises(TA.AppendixError, match="⑧ 的欄"):
        TA.build(_relist(rep))


def test_guard_binding_duplicate_row(rep):
    r = copy.deepcopy(next(x for x in rep["items"]["8_binding_rate"] if x["column"] == "floor_applied"))
    rep["items"]["8_binding_rate"].append(r)
    with pytest.raises(TA.AppendixError, match="重複列"):
        TA.build(_relist(rep))


def test_guard_row_consistency_hysteresis(rep):
    """驗收第五輪：② after 改成 before+1、rel_diff 不動——舊版照印「絕對差 +1 次就超過門檻」。"""
    r = next(x for x in rep["items"]["2_hysteresis_flips"] if (x["scope"], x["market"], x["horizon"]) == ("market", "tpex", "mid"))
    r["after"] = r["before"] + 1
    with pytest.raises(TA.AppendixError, match="衍生欄"):
        TA.build(rep)


def test_guard_row_consistency_binding(rep):
    """⑧ diff 改成 0.2、前後側不動（實際差約 5 個百分點），清單也重列成自洽。"""
    r = next(x for x in rep["items"]["8_binding_rate"]
             if x["column"] == "floor_applied" and (x["market"], x["horizon"]) == ("twse", "mid"))
    r["diff"] = 0.2
    with pytest.raises(TA.AppendixError, match="衍生欄"):
        TA.build(_relist(rep))


def test_guard_row_consistency_flag(rep):
    r = next(x for x in rep["items"]["3_flag_hit_rate"] if x["flag"] == "overheated")
    r["after"] += 0.01
    with pytest.raises(TA.AppendixError, match="衍生欄"):
        TA.build(rep)


def test_guard_binding_direction(rep):
    """⑧ 同格複製一列、只把 direction 改成 long：無重複鍵含 direction 所以會過，要靠「⑧ 無方向」擋。"""
    r = copy.deepcopy(next(x for x in rep["items"]["8_binding_rate"] if x["column"] == "floor_applied"))
    r["direction"] = "long"
    rep["items"]["8_binding_rate"].append(r)
    with pytest.raises(TA.AppendixError, match="方向維度"):
        TA.build(_relist(rep))


def test_guard_row_consistency_tolerance(rep):
    """容差要真的小：衍生欄只偏 1e-9 也要中止（放寬成 1e-2 的突變只有這支抓得到）。"""
    r = rep["items"]["2_hysteresis_flips"][0]
    r["rel_diff"] += 1e-9
    with pytest.raises(TA.AppendixError, match="衍生欄"):
        TA.build(_relist(rep))


# ---- 分析工具合法產出的 None（驗收第六輪 N1：9ebb4a5 的列內一致守門對它當掉、落到 rc=1）----

def test_none_row_from_zero_denominator_is_ok(rep, tmp_path):
    """③ 某格分母為 0：revalidate 的 `rate` 回 None、`_sub` 也回 None。這是合法形狀，要照常產出、rc=0。"""
    r = next(x for x in rep["items"]["3_flag_hit_rate"] if x["flag"] == "F-分歧")
    r["before"] = r["after"] = r["diff"] = None
    r["n_before"] = r["n_after"] = 0
    TA.build(rep)
    rp = tmp_path / "r.json"
    rp.write_text(json.dumps(rep), encoding="utf-8")
    p = tmp_path / "prereg.md"
    p.write_text(PREREG.read_text(encoding="utf-8"), encoding="utf-8")
    assert TA.main(["--report", str(rp), "--prereg", str(p)]) == 0


def test_zero_before_rel_none_is_ok(rep):
    """② 個股格前側為 0：`_rel` 回 None。合法形狀，照常產出。"""
    r = next(x for x in rep["items"]["2_hysteresis_flips"] if x["scope"] == "stock")
    r["before"], r["after"], r["rel_diff"] = 0, 5, None
    TA.build(rep)


def test_zero_before_rel_not_none_aborts(rep):
    """三態的另一側：前側為 0 卻有 rel_diff，衍生欄與原始欄對不上。"""
    r = next(x for x in rep["items"]["2_hysteresis_flips"] if x["scope"] == "stock")
    r["before"], r["after"], r["rel_diff"] = 0, 5, 0.05
    with pytest.raises(TA.AppendixError, match="衍生欄"):
        TA.build(rep)


def test_none_in_zero_diff_flag_aborts(rep):
    """F-高波動 差為 None：「差異為零」無從斷言，中止（不是 TypeError 當掉）。"""
    r = next(x for x in rep["items"]["3_flag_hit_rate"] if x["flag"] == "F-高波動")
    r["before"] = r["after"] = r["diff"] = None
    with pytest.raises(TA.AppendixError, match="None"):
        TA.build(rep)


def test_none_in_binding_aborts(rep):
    r = next(x for x in rep["items"]["8_binding_rate"] if x["column"] == "overheat_cap_applied")
    r["before"] = r["after"] = r["diff"] = None
    with pytest.raises(TA.AppendixError, match="None"):
        TA.build(rep)


def test_main_crash_is_rc2_not_rc1(monkeypatch):
    """任何未預期的例外一律 rc=2，不得與 --check 的「附錄過期」rc=1 混淆（第七輪構造出 AttributeError）。"""
    for exc in (TypeError, ZeroDivisionError, AttributeError, RuntimeError):
        def boom(rep, exc=exc):
            raise exc("x")
        monkeypatch.setattr(TA, "build", boom)
        assert TA.main(["--check"]) == 2


# ---- None 格的計數不得過度宣稱（驗收第七輪 X4）----

def test_none_cell_trigram_count(rep):
    rep["items"]["5_trigram_state_diff_rate"][-1]["diff_rate"] = None
    rep["over_threshold"] = [h for h in rep["over_threshold"]
                             if not (h["item"] == "5_trigram_state_diff_rate"
                                     and all(h[k] == rep["items"]["5_trigram_state_diff_rate"][-1][k]
                                             for k in ("scope", "market", "horizon", "direction")))]
    out = TA.build(rep)
    assert "有值的 11 格（另 1 格算不出）範圍" in out
    assert "全部 12 格範圍" not in out and "全部 有值的" not in out


def test_none_cell_unflagged_count(rep):
    rep["items"]["4_moving_line_count_dist"][0]["tv_distance"] = None
    out = TA.build(rep)
    assert "有值的 11 格（另 1 格算不出）全部未超標" in out
    line = next(ln for ln in out.splitlines() if ln.startswith("- ④ 動爻數分布"))
    assert "12 格" not in line


@pytest.mark.parametrize("item,field", [("1_line_state_diff_rate", "diff_rate"),
                                        ("6_hexagram_agreement", "future_king_wen_same_rate"),
                                        ("7_candidate_overlap", "same_rank_rate")])
def test_unsupported_none_aborts_cleanly(rep, item, field):
    """①⑥⑦ 的 None 目前不支援：明確 AppendixError，不是 TypeError。"""
    rep["items"][item][0][field] = None
    with pytest.raises(TA.AppendixError, match="尚未支援"):
        TA.build(_relist(rep))
