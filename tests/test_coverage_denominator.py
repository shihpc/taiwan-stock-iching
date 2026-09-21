"""coverage 分母排除「結構上還算不出來」的族（`docs/P3-CALIBRATION.md` §17 的 C1～C7）。

起因是一個每月復發的複合缺陷：大盤五爻族 C（VIX）因上游只有 2026-03 起而**長期**缺席（裁定 #26），
於是每月換月日族 B 一降級（規格要求的 `contract_rolled`），ratio 就從 0.7 掉到 0.4 < 0.5 → 整爻未知
→ 大盤方向分數 Missing → 全市場個股上爻連坐降級（回測訓練段 30／603 日、驗證段 18／368 日）。

本檔守的是修法的**邊界**，不是「有改到東西」：只認 `insufficient_history`、暖機期不得被排除洗白、
未跨越門檻的爻分數必須逐位不變、旗標關閉時與舊公式逐位相同。
"""
from __future__ import annotations

import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from iching.score.aggregate import FamilyResult, SubResult, line_score  # noqa: E402
from iching.score.params import Rules, build_params  # noqa: E402
from iching.score.transform import (  # noqa: E402
    REASON_CONTRACT_ROLLED, REASON_DENOM_ZERO, REASON_INSUFFICIENT, REASON_MISSING, Missing,
)

W5 = {"A": .40, "B": .30, "C": .30}          # 大盤五爻族權重（params.py:386）
UNKNOWN_BELOW = 0.5


def fam(name: str, score: float | None, reason: str | None = None) -> FamilyResult:
    miss = Missing(reason, "test") if reason is not None else None
    sub = SubResult(f"{name}_sub", score, score, score, False, miss, 1.0)
    return FamilyResult(name, score, (sub,), missing=miss)


def ls(families, *, exclude: bool, weights=None):
    return line_score("5", families, dict(weights or W5), UNKNOWN_BELOW, exclude_insufficient=exclude)


# C1：只有 insufficient_history 被排除；其餘缺值原因一律仍計入分母
def test_only_insufficient_history_is_excluded():
    for reason, expect_ratio in [(REASON_INSUFFICIENT, 1.0), (REASON_CONTRACT_ROLLED, 0.7),
                                 (REASON_MISSING, 0.7), (REASON_DENOM_ZERO, 0.7)]:
        r = ls([fam("A", 60.0), fam("B", 40.0), fam("C", None, reason)], exclude=True)
        assert round(r.coverage_ratio, 6) == expect_ratio, (reason, r.coverage_ratio)


# C2：暖機保護——排除後剩餘宣告權重佔比 < unknown_below 時不排除
def test_warmup_floor_blocks_exclusion():
    r = ls([fam("A", 60.0), fam("B", None, REASON_INSUFFICIENT), fam("C", None, REASON_INSUFFICIENT)], exclude=True)
    assert round(r.coverage_ratio, 6) == 0.4 and r.unknown is True and r.score is None
    # 邊界：剩餘恰為 0.5 要排除（>=），故構造 A .30 + B .20 可得、C .50 insufficient
    w = {"A": .30, "B": .20, "C": .50}
    r2 = line_score("x", [fam("A", 60.0), fam("B", 40.0), fam("C", None, REASON_INSUFFICIENT)], w,
                    UNKNOWN_BELOW, exclude_insufficient=True)
    assert round(r2.coverage_ratio, 6) == 1.0 and r2.unknown is False


# C3／C5：未跨越門檻的爻，分數逐位不變；reweighted 旗標不得被洗掉
def test_score_bitwise_unchanged_when_not_crossing():
    fams = [fam("A", 60.0), fam("B", 40.0), fam("C", None, REASON_INSUFFICIENT)]
    old, new = ls(fams, exclude=False), ls(fams, exclude=True)
    assert old.score == new.score                      # 逐位相同（分子 got 不受影響）
    assert round(old.coverage_ratio, 6) == 0.7 and round(new.coverage_ratio, 6) == 1.0
    assert old.unknown is False and new.unknown is False
    assert old.reweighted is True and new.reweighted is True    # 族確實缺席，訊號保留


# C4：換月日情境——0.4 → 0.571、有分數、值＝族 A
def test_rollover_day_becomes_scored():
    fams = [fam("A", 55.5), fam("B", None, REASON_CONTRACT_ROLLED), fam("C", None, REASON_INSUFFICIENT)]
    old, new = ls(fams, exclude=False), ls(fams, exclude=True)
    assert round(old.coverage_ratio, 6) == 0.4 and old.unknown is True and old.score is None
    assert round(new.coverage_ratio, 3) == 0.571 and new.unknown is False
    # 只剩族 A → 分數即其值；`sum(s*w)/got` 的除法留下 1e-14 級誤差（55.50000000000001），非本次修改引入
    assert abs(new.score - 55.5) < 1e-9
    assert new.reweighted is True


# C6：旗標關閉時與舊公式逐位相同（舊公式在此重寫一遍當參考實作，不呼叫被測程式）
def test_flag_off_matches_old_formula():
    rng = random.Random(7)
    reasons = [REASON_INSUFFICIENT, REASON_CONTRACT_ROLLED, REASON_MISSING, REASON_DENOM_ZERO]
    for _ in range(1000):
        names = ["A", "B", "C", "D"][: rng.choice([2, 3, 4])]
        raw = [rng.random() for _ in names]
        weights = {n: v / sum(raw) for n, v in zip(names, raw, strict=True)}
        fams = []
        for n in names:
            if rng.random() < 0.45:
                fams.append(fam(n, None, rng.choice(reasons)))
            else:
                fams.append(fam(n, rng.uniform(7.3, 92.7)))
        got = line_score("t", fams, dict(weights), UNKNOWN_BELOW, exclude_insufficient=False)
        # 參考實作（改動前的公式）
        by = {f.family: f for f in fams}
        expected = sum(weights.values())
        g = sum(w for n, w in weights.items() if by[n].score is not None)
        ratio = g / expected if expected > 0 else 0.0
        want = None if ratio < UNKNOWN_BELOW else sum(by[n].score * w for n, w in weights.items()
                                                      if by[n].score is not None) / g
        assert got.coverage_ratio == ratio
        assert got.score == want
        assert got.unknown is (want is None)


# C7：旗標進 Rules（因此進指紋），預設開啟；關掉時指紋必須不同
def test_flag_is_in_rules_and_fingerprint():
    assert Rules().coverage_excludes_insufficient is True
    mv_on = build_params("twse").model_version()
    assert mv_on != "p2-score-engine-1.b45aa4dac4dc", "本次修改後 model_version 必須與 §17 之前不同"
    import dataclasses
    ps = build_params("twse")
    off = dataclasses.replace(ps, rules=dataclasses.replace(ps.rules, coverage_excludes_insufficient=False))
    assert off.model_version() != mv_on, "旗標沒進指紋＝改了行為卻不換版本"
