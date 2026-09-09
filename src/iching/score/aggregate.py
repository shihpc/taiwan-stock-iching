"""族／爻／方向分數的聚合與缺值重配（B1.7「缺值時的權重重配（版本化）」；B2.0 同規則）。

- 族內單一子指標缺 → 該族取其餘子指標**平均**（有 `sub_weight` 者按其比例重配；等權即算術平均）
- 整族缺 → 該爻剩餘族權重按原比例正規化；記 `coverage_ratio`＝實得權重 ÷ 應有權重
- `coverage_ratio < 0.5` → 該爻「未知」（score=None），卦名標「待補」
- 缺值**不得**靜默成 50：`Missing` 只會讓權重重配或整爻未知，絕不產生數字

`coverage`（列舉，dimensions.json）：全爻六族皆滿覆蓋＝`full`，任一爻有重配＝`reweighted`。
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .transform import Ind, Missing, REASON_LINE_UNKNOWN, normalize

UNKNOWN_BELOW = 0.5


@dataclass(frozen=True)
class SubResult:
    indicator_id: str
    score: float | None          # 套 N 之後
    native: float | None
    x: float | None
    clipped: bool
    missing: Missing | None
    sub_weight: float
    meta: dict = field(default_factory=dict)


@dataclass(frozen=True)
class FamilyResult:
    family: str
    score: float | None
    subs: tuple[SubResult, ...]
    missing: Missing | None = None     # 整族缺時的原因（取第一個子指標的原因）
    reweighted: bool = False           # 族內有子指標缺
    meta: dict = field(default_factory=dict)


@dataclass(frozen=True)
class LineResult:
    line: str
    score: float | None                # None ＝ 未知
    coverage_ratio: float
    unknown: bool
    families: tuple[FamilyResult, ...]
    expected_weights: dict
    reweighted: bool
    meta: dict = field(default_factory=dict)

    def family(self, name: str) -> FamilyResult | None:
        for f in self.families:
            if f.family == name:
                return f
        return None


def sub_result(indicator_id: str, out: Ind | Missing, sub_weight: float = 1.0) -> SubResult:
    if isinstance(out, Missing):
        return SubResult(indicator_id, None, None, None, False, out, sub_weight)
    if not isinstance(out, Ind):
        raise TypeError(f"{indicator_id}: indicator must return Ind or Missing, got {type(out).__name__}")
    return SubResult(indicator_id, normalize(out), out.native, out.x, out.clipped, None, sub_weight, dict(out.meta))


def family_score(family: str, subs: list[SubResult], **meta) -> FamilyResult:
    present = [s for s in subs if s.score is not None]
    if not present:
        reason = subs[0].missing if subs else Missing("missing", "no sub-indicators")
        return FamilyResult(family, None, tuple(subs), missing=reason, meta=dict(meta))
    w = sum(s.sub_weight for s in present)
    score = sum(s.score * s.sub_weight for s in present) / w
    return FamilyResult(family, float(score), tuple(subs), reweighted=(len(present) < len(subs)), meta=dict(meta))


def line_score(line: str, families: list[FamilyResult], weights: dict[str, float], **meta) -> LineResult:
    """`weights`＝該爻該期間**應有**的族權重（不適用的族不在其中）。"""
    fam_by = {f.family: f for f in families}
    for name in weights:
        if name not in fam_by:
            raise KeyError(f"line {line}: family {name} has weight but no result")
    expected = sum(weights.values())
    got = sum(w for name, w in weights.items() if fam_by[name].score is not None)
    ratio = got / expected if expected > 0 else 0.0
    reweighted = any(fam_by[n].score is None or fam_by[n].reweighted for n in weights)
    if ratio < UNKNOWN_BELOW:
        return LineResult(line, None, ratio, True, tuple(families), dict(weights), reweighted, dict(meta))
    score = sum(fam_by[n].score * w for n, w in weights.items() if fam_by[n].score is not None) / got
    return LineResult(line, float(score), ratio, False, tuple(families), dict(weights), reweighted, dict(meta))


def direction_score(lines: dict[str, LineResult], weights: dict[str, float]) -> float | Missing:
    """六爻加權方向分數（B1.7／B2.7）。
    # SPEC-GAP: 任一爻「未知」時方向分數如何處理未定（B1.7 只說該爻不參與正式爻態）；此處保守回缺值、不重配。"""
    for name in weights:
        lr = lines.get(name)
        if lr is None or lr.score is None:
            return Missing(REASON_LINE_UNKNOWN, f"line {name} unknown")
    return float(sum(lines[n].score * w for n, w in weights.items()))


def trigram_mean(lines: dict[str, LineResult], names: tuple[str, ...]) -> float | Missing:
    """內卦（初二三）／外卦（四五上）方向分＝算術平均（S1a §3.2）。任一爻未知 → 缺值。"""
    vals = []
    for n in names:
        lr = lines.get(n)
        if lr is None or lr.score is None:
            return Missing(REASON_LINE_UNKNOWN, f"line {n} unknown")
        vals.append(lr.score)
    return float(sum(vals) / len(vals))


def coverage_label(lines: dict[str, LineResult]) -> str:
    return "reweighted" if any(l.reweighted or l.unknown for l in lines.values()) else "full"
