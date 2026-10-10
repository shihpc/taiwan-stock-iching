"""裁定 #74 PR-74c（docs/P4-PREVIEW.md §12.9，驗收條件 C5）：總分排序分頁的逐期間揭露常數 `RANK_DISC_H` 必須與回測報告一致。

讀 `runs/backtest/valid_2026-10-07.json`（驗證段）與 `train_2026-10-07.json`（訓練段），對每個期間：
- 兩市場 verdict 必相同（否則一句話寫不下，要先改常數結構）；
- verdict `rejected` → 該段子句含「未通過門檻」、不含「證據不足」，且含「IC 為負」或「IC 亦為負」與兩市場 IC 數值；
  `insufficient` → 該段子句含「證據不足」、不含「未通過門檻」、不列 IC 數值。
- **數字四捨五入規則**：`ic.ic_mean` 以 Python `format(x, ".3f")`（小數 3 位；本批 12 格無 .xxx5 邊界值，故不涉及
  binary 表示下的進位歧義），負號改成 U+2212「−」，依「上市 X、上櫃 Y」順序出現在該段子句內。
- 每句都以「總分高不代表後續報酬較高。」結尾；不得出現「總分高者報酬較低」一類反向斷言或任何正向暗示字眼。
子句切法：以全形分號「；」與句號「。」切；「驗證段與訓練段皆…」這種一個子句同時涵蓋兩段者，兩段都以該子句判。

2026-10-10 版面整理後另有常駐短語 `RANK_DISC_S`（不放數字）：每期間必須提到驗證段、且提到的每一段 verdict 字樣都要與 json 一致；
「IC 為負」只在該段兩市場 IC 皆 < 0 時才可寫。短語以頓號「、」切子句（短語內不含括號外的頓號以外分隔）。
免 token、免網路、不需瀏覽器。
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
HTML = (ROOT / "index.html").read_text(encoding="utf-8")
SEG = {"valid": "驗證段", "train": "訓練段"}
VERDICT_WORD = {"rejected": "未通過門檻", "insufficient": "證據不足"}
MK_LABEL = {"twse": "上市", "tpex": "上櫃"}
TAIL = "總分高不代表後續報酬較高。"
BANNED = ["報酬較低", "較低者", "高者報酬", "推薦", "強勢", "候選", "選多空", "有效", "較佳", "看好", "看多", "勝率", "機率"]


def rank_disc(name: str = "RANK_DISC_H") -> dict[str, str]:
    m = re.search(rf"^const {name} = \{{\n(.*?)\n\}};$", HTML, re.MULTILINE | re.DOTALL)
    assert m, f"index.html 找不到 {name} 常數"
    d = dict(re.findall(r'^\s*(short|swing|mid): "([^"]*)",$', m.group(1), re.MULTILINE))
    assert set(d) == {"short", "swing", "mid"}, d
    return d


def cells(seg: str) -> dict[tuple[str, str], dict]:
    doc = json.loads((ROOT / "runs" / "backtest" / f"{seg}_2026-10-07.json").read_text(encoding="utf-8"))
    assert doc["segment"] == seg
    return {(c["horizon"], c["market"]): c for c in doc["cells"]}


def ic_txt(x: float) -> str:
    return format(x, ".3f").replace("-", "−")


def clause_for(text: str, seg_label: str) -> str:
    parts = [p for p in re.split(r"[；。]", text) if p]
    hits = [p for p in parts if seg_label in p]
    assert len(hits) == 1, (seg_label, text)
    return hits[0]


@pytest.mark.parametrize("h", ["short", "swing", "mid"])
@pytest.mark.parametrize("seg", ["valid", "train"])
def test_disclosure_matches_backtest_json(h, seg):
    text = rank_disc()[h]
    C = cells(seg)
    v = {C[(h, mk)]["verdict"] for mk in MK_LABEL}
    assert len(v) == 1, (seg, h, v)
    verdict = v.pop()
    assert verdict in VERDICT_WORD, verdict
    cl = clause_for(text, SEG[seg])
    other = VERDICT_WORD["insufficient" if verdict == "rejected" else "rejected"]
    assert VERDICT_WORD[verdict] in cl and other not in cl, (seg, h, cl)
    nums = [ic_txt(C[(h, mk)]["ic"]["ic_mean"]) for mk in MK_LABEL]
    if verdict == "rejected":
        assert all(C[(h, mk)]["ic"]["ic_mean"] < 0 for mk in MK_LABEL), (seg, h)   # 「IC 為負」的前提
        assert ("IC 為負" in cl or "IC 亦為負" in cl), cl
        assert f"上市 {nums[0]}、上櫃 {nums[1]}" in cl, (cl, nums)
    else:
        assert "IC" not in cl, cl       # 證據不足的段不列 IC 數值（避免把不足以下結論的數字讀成結論）


def test_disclosure_tail_and_banned_words():
    for h, text in rank_disc().items():
        assert text.endswith(TAIL), h
        assert text.count("。") == 2, h      # 期間主句＋固定尾句
        hits = [w for w in BANNED if w in text]
        assert not hits, (h, hits)
    xm = re.search(r'^const RANK_XMKT_TXT = "([^"]*)";', HTML, re.MULTILINE)
    assert xm and xm.group(1) == "IC 分市場計算，跨市場混排的可比性未經驗證。"


def test_disclosure_numbers_only_from_json():
    """常數裡出現的每個 IC 數字都必須能在兩份 json 該期間的 ic_mean 找到（防止手抄錯或殘留舊數字）。"""
    allowed = {h: {ic_txt(c["ic"]["ic_mean"]) for seg in SEG for (hh, _), c in cells(seg).items() if hh == h}
               for h in ("short", "swing", "mid")}
    for h, text in rank_disc().items():
        found = re.findall(r"−?\d+\.\d{3}", text)
        assert set(found) <= allowed[h], (h, found, allowed[h])


@pytest.mark.parametrize("h", ["short", "swing", "mid"])
def test_brief_phrase_matches_backtest_json(h):
    """常駐短語 RANK_DISC_S：以「期間：」起頭、必提驗證段；提到的每段 verdict 字樣與 json 一致；不含數字與禁用字。"""
    label = {"short": "短線", "swing": "波段", "mid": "中期"}[h]
    text = rank_disc("RANK_DISC_S")[h]
    assert text.startswith(label + "："), text
    assert SEG["valid"] in text, text
    assert not re.search(r"\d", text), text
    assert not [w for w in BANNED if w in text], text
    parts = [p for p in re.split(r"、", text) if p]
    for seg, seg_label in SEG.items():
        hits = [p for p in parts if seg_label in p]
        if not hits:
            continue                      # 短語可省略訓練段（完整句在 RANK_DISC_H，見上方測試）
        assert len(hits) == 1, (seg, text)
        C = cells(seg)
        v = {C[(h, mk)]["verdict"] for mk in MK_LABEL}
        assert len(v) == 1
        verdict = v.pop()
        other = VERDICT_WORD["insufficient" if verdict == "rejected" else "rejected"]
        assert VERDICT_WORD[verdict] in hits[0] and other not in hits[0], (seg, hits[0])
        if "IC 為負" in hits[0]:
            assert verdict == "rejected" and all(C[(h, mk)]["ic"]["ic_mean"] < 0 for mk in MK_LABEL), (seg, hits[0])


# ---- 逐字期望值（驗收建議：寫成測試檔內的字面量、不讀常數比對）——防止在常數裡追加未列舉的措辭（例：「，總分仍可作參考」）而測試仍綠。
# 改任何一句都必須同時改這裡，並重新送審措辭。
EXPECT_DISC_S = {
    "short": "短線：驗證段未通過門檻（IC 為負）",
    "swing": "波段：驗證段證據不足、訓練段未通過門檻",
    "mid": "中期：驗證段與訓練段皆證據不足",
}
EXPECT_DISC_H = {
    "short": "短線：驗證段 IC 為負（上市 −0.027、上櫃 −0.042），未通過門檻；訓練段 IC 亦為負（上市 −0.007、上櫃 −0.032），未通過門檻。總分高不代表後續報酬較高。",
    "swing": "波段：驗證段證據不足（樣本區塊數未達門檻，不作結論）；訓練段 IC 為負（上市 −0.018、上櫃 −0.056），未通過門檻。總分高不代表後續報酬較高。",
    "mid": "中期：驗證段與訓練段皆證據不足（樣本區塊數未達門檻），不作結論。總分高不代表後續報酬較高。",
}
EXPECT_DISC_MIN = "預覽版・非買賣訊號・AI 研判、非保證"   # 全站 #disc 常駐單行（使用者 2026-10-10 指示後）


def test_disclosure_constants_verbatim():
    assert rank_disc("RANK_DISC_S") == EXPECT_DISC_S
    assert rank_disc("RANK_DISC_H") == EXPECT_DISC_H


def test_disc_permanent_line_verbatim():
    m = re.search(r'<div class="disc" id="disc">\s*<span class="discmin">(.*?)</span>\s*<details id="discMore">', HTML)
    assert m, "找不到 #disc 常駐句"
    assert re.sub(r"<[^>]*>", "", m.group(1)) == EXPECT_DISC_MIN
