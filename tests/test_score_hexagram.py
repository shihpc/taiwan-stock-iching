"""§8 第 4 條：六爻 → lines_bottom_up → hexagrams64.json 查 king_wen；flip_direction／from／to 依 B4；64 卦全表往返。"""
from __future__ import annotations

import pytest

from iching.score.hexagram import (YANG, YIN, basic_state, line_flip_direction, from_king_wen_paths, hysteresis_step,
                                   king_wen_from_lines, lines_from_king_wen, lines_from_scores, load_hexagrams, name_of,
                                   to_king_wen)


def test_64_roundtrip_unique_names_and_bits():
    hx = load_hexagrams()
    assert set(hx) == set(range(1, 65))
    assert len({r["name"] for r in hx.values()}) == 64
    assert len({tuple(r["lines_bottom_up"]) for r in hx.values()}) == 64
    for kw in range(1, 65):
        assert king_wen_from_lines(lines_from_king_wen(kw)) == kw
    assert king_wen_from_lines([1, 1, 1, 1, 1, 1]) == 1
    assert king_wen_from_lines([0, 0, 0, 0, 0, 0]) == 2
    assert name_of(32) == "雷風恆" and "恆" in name_of(32)   # B4.7：第 32 卦主名一律「恆」U+6046


def test_to_king_wen_flip_and_symmetry_384():
    total = 0
    for kw in range(1, 65):
        bits = lines_from_king_wen(kw)
        for line in range(1, 7):
            to = to_king_wen(kw, line)
            tb = lines_from_king_wen(to)
            assert sum(a != b for a, b in zip(bits, tb)) == 1 and bits[line - 1] != tb[line - 1]   # A3：漢明距離 1，差異位＝line
            assert to_king_wen(to, line) == kw                                                          # A4：對稱
            assert line_flip_direction(kw, line) == ("yang_to_yin" if bits[line - 1] == 1 else "yin_to_yang")
            total += 1
    assert total == 384
    paths = [p for kw in range(1, 65) for p in from_king_wen_paths(kw)]
    assert len(paths) == 384                                        # A2：384 條入向路徑
    for kw in range(1, 65):
        ps = from_king_wen_paths(kw)
        assert [p["line"] for p in ps] == [1, 2, 3, 4, 5, 6]
        for p in ps:
            assert to_king_wen(p["from_king_wen"], p["line"]) == kw
            assert p["flip_direction"] in ("yang_to_yin", "yin_to_yang")
            # 前卦→本卦（formation_paths）與本卦→之卦（line_flip_direction）對同一 (卦, 爻) 恰相反
            assert p["flip_direction"] != line_flip_direction(kw, p["line"])


def test_bad_inputs_raise():
    with pytest.raises(ValueError):
        king_wen_from_lines([1, 1, 1])
    with pytest.raises(ValueError):
        king_wen_from_lines([2, 0, 0, 0, 0, 0])
    with pytest.raises(ValueError):
        to_king_wen(1, 7)


def test_lines_from_scores_threshold_and_missing_not_yin():
    assert lines_from_scores([50.0, 49.999, 92.7, 7.3, 50.0001, 0.0]) == [1, 0, 1, 0, 1, 0]
    assert lines_from_scores([50.0, None, 60, 60, 60, 60]) is None   # 缺值 → 待補，不補陰
    with pytest.raises(ValueError):
        lines_from_scores([50.0] * 5)


def test_hysteresis_two_consecutive_days_and_missing_does_not_accumulate():
    st, streak, flipped = hysteresis_step(None, 0, 52.0)
    assert (st, streak, flipped) == (YANG, 0, False)
    st, streak, flipped = hysteresis_step(YIN, 0, 56.0)
    assert (st, streak, flipped) == (YIN, 1, False)          # 第一天 ≥55：仍陰
    st, streak, flipped = hysteresis_step(YIN, 1, None)
    assert (st, streak, flipped) == (YIN, 1, False)          # 缺值：不累加
    st, streak, flipped = hysteresis_step(YIN, 1, 55.0)
    assert (st, streak, flipped) == (YANG, 0, True)          # 連續第二天 → 翻陽
    st, streak, flipped = hysteresis_step(YIN, 1, 54.9)
    assert (st, streak, flipped) == (YIN, 0, False)          # 中斷歸零
    st, streak, flipped = hysteresis_step(YANG, 1, 45.0)
    assert (st, streak, flipped) == (YIN, 0, True)
    assert hysteresis_step(YANG, 0, 45.1) == (YANG, 0, False)


def test_hysteresis_thresholds_come_from_rules():
    from iching.score.params import Rules
    r = Rules(hysteresis_up=60.0, hysteresis_confirm_days=3)
    assert hysteresis_step(YIN, 0, 56.0, r) == (YIN, 0, False)
    assert hysteresis_step(YIN, 1, 61.0, r) == (YIN, 2, False)
    assert hysteresis_step(YIN, 2, 61.0, r) == (YANG, 0, True)
    assert lines_from_scores([50.0] * 6, Rules(hysteresis_first=51.0)) == [0] * 6


def test_basic_state():
    assert basic_state(YANG, YANG) == "S1"
    assert basic_state(YANG, YIN) == "S2"
    assert basic_state(YIN, YANG) == "S3"
    assert basic_state(YIN, YIN) == "S4"
    assert basic_state(None, YANG) == "undetermined"
