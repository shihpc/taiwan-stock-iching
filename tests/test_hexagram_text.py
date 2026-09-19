"""裁定 #52（docs/P4-PREVIEW.md §7）：`data/hexagram_text.json` 守門——64 卦齊、king_wen／name 與 spec 一致、每卦 6 爻、
爻題九／六與 `lines_bottom_up` 位元一致、只有乾／坤有 `extra`、無空字串、正體字、標點全形、摘義 ≤40 字。免 token 免網路。"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data" / "hexagram_text.json"
SPEC = ROOT / "spec" / "hexagrams64.json"

POS = ["初", "二", "三", "四", "五", "上"]
GLOSS_MAX = 40
# 明確簡體字表：只挑不會出現在正體經文的字。**刻意不列** 无／于／后／云／里／几／系／並（經文通假或正體本字），
# 也不列「於」（賁六五「賁於丘園」是經文原字）。
SIMPLIFIED = "说这为义阴阳见龙变动势应时则与从却发东车门问闻国长张开关军战当尽处术业产内况区决兴权举万经书体过还进远运点电对丰乐"
# 全形標點白名單（經文實查只有 ，。；？；摘義另用 、「」）
PUNCT_OK = set("，。；：？！、「」")


def _load():
    return json.loads(DATA.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def doc():
    return _load()


@pytest.fixture(scope="module")
def spec():
    return json.loads(SPEC.read_text(encoding="utf-8"))


def _all_texts(h):
    """一卦內所有古文欄（卦辭／六爻／extra）。"""
    yield "judgment", h["judgment"]
    for ln in h["lines"]:
        yield ln["title"], ln["text"]
    if h["extra"] is not None:
        yield h["extra"]["title"], h["extra"]["text"]


def _all_glosses(h):
    yield "gloss_judgment", h["gloss_judgment"]
    for i, g in enumerate(h["gloss_lines"]):
        yield f"gloss_lines[{i}]", g


def test_top_level_shape(doc):
    assert doc["schema"] == 1
    src = doc["source"]
    assert src["primary"].startswith("https://zh.wikisource.org/")
    assert src["secondary"] is None or src["secondary"].startswith("https://")
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", src["fetched_at"])
    assert doc["gloss_note"] == "白話摘義由 AI 撰寫，非學術譯注"
    assert set(doc) == {"schema", "source", "gloss_note", "hexagrams"}


def test_64_hexagrams_ascending_and_names_match_spec(doc, spec):
    hx = doc["hexagrams"]
    assert len(hx) == 64
    assert [h["king_wen"] for h in hx] == list(range(1, 65))
    assert [s["king_wen"] for s in spec] == list(range(1, 65))
    for h, s in zip(hx, spec):
        assert h["name"] == s["name"], (h["king_wen"], h["name"], s["name"])
    assert hx[31]["name"] == "雷風恆"   # B4.7：第 32 卦主名一律「恆」U+6046


def test_each_hexagram_keys(doc):
    keys = {"king_wen", "name", "judgment", "lines", "extra", "gloss_judgment", "gloss_lines"}
    for h in doc["hexagrams"]:
        assert set(h) == keys, h["king_wen"]
        assert len(h["lines"]) == 6, h["king_wen"]
        assert len(h["gloss_lines"]) == 6, h["king_wen"]
        for ln in h["lines"]:
            assert set(ln) == {"title", "text"}, (h["king_wen"], ln)


def test_line_titles_match_bits(doc, spec):
    """爻題＝位名（初／二／三／四／五／上）×陰陽（1→九、0→六）；初與上位名在前，二～五位名在後。"""
    for h, s in zip(doc["hexagrams"], spec):
        bits = s["lines_bottom_up"]
        assert len(bits) == 6
        for i, (ln, bit) in enumerate(zip(h["lines"], bits)):
            yy = "九" if bit == 1 else "六"
            expected = (POS[i] + yy) if i in (0, 5) else (yy + POS[i])
            assert ln["title"] == expected, (h["king_wen"], i, ln["title"], expected)


def test_extra_only_qian_kun(doc):
    for h in doc["hexagrams"]:
        if h["king_wen"] == 1:
            assert h["extra"]["title"] == "用九" and h["extra"]["text"]
        elif h["king_wen"] == 2:
            assert h["extra"]["title"] == "用六" and h["extra"]["text"]
        else:
            assert h["extra"] is None, h["king_wen"]


def test_no_empty_strings(doc):
    for h in doc["hexagrams"]:
        assert h["name"].strip()
        for lab, t in list(_all_texts(h)) + list(_all_glosses(h)):
            assert isinstance(t, str) and t.strip(), (h["king_wen"], lab)


def test_traditional_and_fullwidth_punct(doc):
    for h in doc["hexagrams"]:
        for lab, t in list(_all_texts(h)) + list(_all_glosses(h)):
            for ch in t:
                assert ch not in SIMPLIFIED, (h["king_wen"], lab, ch, t)
                assert ch not in ",.;:?!()\"' \t", (h["king_wen"], lab, repr(ch), t)   # 半形標點／空白
                assert ("一" <= ch <= "鿿") or ch in PUNCT_OK, (h["king_wen"], lab, repr(ch), t)
            assert t[-1] in "。？！", (h["king_wen"], lab, t)   # 句末收句號


def test_gloss_is_baihua_not_market(doc):
    """摘義 ≤40 字；不寫股市／買賣；不用經文通假「无」（摘義是白話，用「無」）。"""
    for h in doc["hexagrams"]:
        for lab, g in _all_glosses(h):
            assert len(g) <= GLOSS_MAX, (h["king_wen"], lab, len(g), g)
            for w in ("股", "買", "賣", "漲", "跌", "无"):
                assert w not in g, (h["king_wen"], lab, w, g)


def test_texts_are_distinct_from_glosses(doc):
    """摘義不得照抄古文（至少不能整句相同）。"""
    for h in doc["hexagrams"]:
        assert h["gloss_judgment"] != h["judgment"], h["king_wen"]
        for ln, g in zip(h["lines"], h["gloss_lines"]):
            assert g != ln["text"], (h["king_wen"], ln["title"])


def test_known_anchors(doc):
    """幾個逐字錨點（維基文庫《周易》），防止整檔被錯位或錯抓。"""
    hx = {h["king_wen"]: h for h in doc["hexagrams"]}
    assert hx[1]["judgment"] == "元亨。利貞。"
    assert hx[1]["lines"][0]["text"] == "潛龍勿用。"
    assert hx[1]["lines"][5]["text"] == "亢龍，有悔。"
    assert hx[2]["lines"][0]["text"] == "履霜，堅冰至。"
    assert hx[32]["lines"][2]["text"] == "不恆其德，或承之羞，貞吝。"
    assert hx[63]["judgment"] == "亨小。利貞。初吉終亂。"
    assert hx[64]["lines"][5]["text"] == "有孚于飲酒，无咎，濡其首，有孚失是。"
