"""六爻 ↔ 64 卦對應（P1-B4 §B4.7：關聯鍵一律 `king_wen`；位元 `lines_bottom_up` 整數陣列、初→上、1 陽 0 陰）。

事實來源＝`spec/hexagrams64.json`（程式不得用中文名稱比對）。本模組另含：
- `lines_from_scores`：以 50 分界的**暫定**爻態（v1.2.2 §8「首次以 50 分界」）；缺值爻 → None（不補陰）
- `hysteresis_step`：正式爻態的遲滯（陰→陽連續 2 日 ≥55、陽→陰連續 2 日 ≤45；缺 → 不累加確認天數）
- `basic_state`：S1 §A1.1 基本狀態（只看正式爻態）
遲滯**狀態的儲存**（B3.1 #7）不在本模組——這裡只有一步純函式。
"""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Sequence

from .params import RULES_START, Rules

SPEC_DIR = Path(__file__).resolve().parents[3] / "spec"
HEXAGRAMS_PATH = SPEC_DIR / "hexagrams64.json"

YANG, YIN = "yang", "yin"
FLIP_YANG_TO_YIN, FLIP_YIN_TO_YANG = "yang_to_yin", "yin_to_yang"   # dimensions.json flip_direction


@lru_cache(maxsize=4)
def load_hexagrams(path: str | None = None) -> dict[int, dict]:
    p = Path(path) if path else HEXAGRAMS_PATH
    data = json.loads(p.read_text(encoding="utf-8"))
    if not isinstance(data, list) or len(data) != 64:
        raise ValueError(f"hexagrams64.json must be a list of 64, got {type(data).__name__}/{len(data) if isinstance(data, list) else '?'}")
    by_kw: dict[int, dict] = {}
    seen_bits: set[tuple[int, ...]] = set()
    seen_names: set[str] = set()
    for rec in data:
        kw = int(rec["king_wen"])
        bits = tuple(int(b) for b in rec["lines_bottom_up"])
        if len(bits) != 6 or any(b not in (0, 1) for b in bits):
            raise ValueError(f"king_wen {kw}: bad lines_bottom_up {bits}")
        if kw in by_kw or bits in seen_bits or rec["name"] in seen_names:
            raise ValueError(f"king_wen {kw}: duplicate king_wen/bits/name")
        by_kw[kw] = {"king_wen": kw, "name": rec["name"], "lines_bottom_up": list(bits),
                     "lower": rec.get("lower"), "upper": rec.get("upper")}
        seen_bits.add(bits)
        seen_names.add(rec["name"])
    if set(by_kw) != set(range(1, 65)):
        raise ValueError("king_wen must cover 1..64 exactly")
    return by_kw


@lru_cache(maxsize=4)
def _bits_index(path: str | None = None) -> dict[tuple[int, ...], int]:
    return {tuple(r["lines_bottom_up"]): kw for kw, r in load_hexagrams(path).items()}


def king_wen_from_lines(lines: Sequence[int], path: str | None = None) -> int:
    bits = tuple(int(b) for b in lines)
    if len(bits) != 6 or any(b not in (0, 1) for b in bits):
        raise ValueError(f"lines_bottom_up must be 6 bits of 0/1, got {lines!r}")
    return _bits_index(path)[bits]


def lines_from_king_wen(king_wen: int, path: str | None = None) -> list[int]:
    return list(load_hexagrams(path)[int(king_wen)]["lines_bottom_up"])


def name_of(king_wen: int, path: str | None = None) -> str:
    return load_hexagrams(path)[int(king_wen)]["name"]


def to_king_wen(king_wen: int, line: int, path: str | None = None) -> int:
    """之卦：翻轉第 `line`（1–6，初→上）爻後的 king_wen。"""
    if not 1 <= int(line) <= 6:
        raise ValueError(f"line must be 1..6, got {line}")
    bits = lines_from_king_wen(king_wen, path)
    bits[int(line) - 1] ^= 1
    return king_wen_from_lines(bits, path)


def line_flip_direction(king_wen: int, line: int, path: str | None = None) -> str:
    """**本卦**第 `line` 爻翻轉（本卦 → 之卦）的方向：本卦該爻為陽 → `yang_to_yin`，為陰 → `yin_to_yang`。
    ⚠ 與 `from_king_wen_paths()` 列內的 `flip_direction` 鍵**語意相反**：後者是 B4.7 `formation_paths` 的
    「前卦 → 本卦」方向（前卦該爻為陽 → `yang_to_yin`）。對同一 (卦, 爻)，兩者恰好互為相反；
    故本函式刻意不叫 `flip_direction`，避免被誤當成 formation_paths 的欄位。"""
    bits = lines_from_king_wen(king_wen, path)
    return FLIP_YANG_TO_YIN if bits[int(line) - 1] == 1 else FLIP_YIN_TO_YANG


def from_king_wen_paths(king_wen: int, path: str | None = None) -> list[dict]:
    """入向路徑（B4.7 `formation_paths`）：六個單爻前卦，每爻一列，`from_king_wen` 與本卦漢明距離恰 1。
    列內 `flip_direction`（dimensions.json 維度名）＝**前卦 → 本卦**的變向：前卦該爻為陽 → `yang_to_yin`。
    與 `line_flip_direction(king_wen, line)`（本卦 → 之卦）對同一 (卦, 爻) 恰相反。"""
    out = []
    for line in range(1, 7):
        frm = to_king_wen(king_wen, line, path)   # 對稱：前卦翻同一爻回到本卦
        frm_bits = lines_from_king_wen(frm, path)
        out.append({"line": line, "from_king_wen": frm,
                    "flip_direction": FLIP_YANG_TO_YIN if frm_bits[line - 1] == 1 else FLIP_YIN_TO_YANG})
    return out


# ---------------------------------------------------------------------------
# 爻態
# ---------------------------------------------------------------------------
def lines_from_scores(scores: Sequence[float | None], rules: Rules = RULES_START) -> list[int] | None:
    """暫定爻態：分數 ≥ 50 → 1（陽）、< 50 → 0（陰）。任一爻缺值（None）→ 整組 None（卦名「待補」，不補陰）。"""
    if len(scores) != 6:
        raise ValueError("need 6 line scores")
    if any(s is None for s in scores):
        return None
    return [1 if float(s) >= rules.hysteresis_first else 0 for s in scores]


def hysteresis_step(prev_state: str | None, prev_streak: int, score: float | None, rules: Rules = RULES_START) -> tuple[str | None, int, bool]:
    """一步遲滯（v1.2.2 §8）。回 (state, streak, flipped)。
    - `prev_state` None＝首次：以 50 分界，streak 0
    - 陰 → 陽：連續 2 交易日 ≥ 55；陽 → 陰：連續 2 日 ≤ 45；未達門檻 streak 歸零
    - `score` None（該爻缺值）：狀態與 streak **原樣保留、不累加**（B4.2「不補陰、不累加確認天數」）"""
    if score is None:
        return prev_state, prev_streak, False
    s = float(score)
    if prev_state is None:
        return (YANG if s >= rules.hysteresis_first else YIN), 0, False
    if prev_state == YIN:
        if s >= rules.hysteresis_up:
            streak = prev_streak + 1
            if streak >= rules.hysteresis_confirm_days:
                return YANG, 0, True
            return YIN, streak, False
        return YIN, 0, False
    if prev_state == YANG:
        if s <= rules.hysteresis_down:
            streak = prev_streak + 1
            if streak >= rules.hysteresis_confirm_days:
                return YIN, 0, True
            return YANG, streak, False
        return YANG, 0, False
    raise ValueError(f"bad prev_state {prev_state!r}")


def basic_state(line1: str | None, line2: str | None) -> str:
    """S1 §A1.1：初爻（趨勢）× 二爻（廣度）正式爻態 → S1–S4；任一未知 → undetermined。"""
    if line1 not in (YANG, YIN) or line2 not in (YANG, YIN):
        return "undetermined"
    return {(YANG, YANG): "S1", (YANG, YIN): "S2", (YIN, YANG): "S3", (YIN, YIN): "S4"}[(line1, line2)]
