"""族／爻／方向分數的聚合與缺值重配（B1.7「缺值時的權重重配（版本化）」；B2.0 同規則）。

- 族內單一子指標缺 → 該族其餘子指標**按權重重配**（等權時即算術平均；裁定 §5 #23，B1.7 字面已同步）
- 整族缺 → 該爻剩餘族權重按原比例正規化；記 `coverage_ratio`＝實得權重 ÷ 應有權重
- `coverage_ratio < 0.5` → 該爻「未知」（score=None），卦名標「待補」
- 缺值**不得**靜默成 50：`Missing` 只會讓權重重配或整爻未知，絕不產生數字

`coverage`（列舉，dimensions.json）：全爻六族皆滿覆蓋＝`full`，任一爻有重配＝`reweighted`。
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .transform import Ind, Missing, REASON_INSUFFICIENT, REASON_LINE_UNKNOWN, normalize


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


def family_score(family: str, subs: list[SubResult], policy: str, **meta) -> FamilyResult:
    """族分＝子指標依 `sub_weight` 加權。子指標缺時：
    裁定（2026-09-10，P2-KICKOFF §5 #23）：`policy="weighted"`＝其餘子指標**按權重重配**（等權時即算術平均，
    與 `coverage_ratio` 的權重口徑一致）；`"equal_mean"`＝其餘子指標算術平均（忽略 sub_weight，僅供比對）。
    由 `Rules.family_missing_policy` 決定並進指紋。"""
    present = [s for s in subs if s.score is not None]
    if not present:
        reason = subs[0].missing if subs else Missing("missing", "no sub-indicators")
        return FamilyResult(family, None, tuple(subs), missing=reason, meta=dict(meta))
    reweighted = len(present) < len(subs)
    if policy == "weighted" or not reweighted:
        w = sum(s.sub_weight for s in present)
        score = sum(s.score * s.sub_weight for s in present) / w
    elif policy == "equal_mean":
        score = sum(s.score for s in present) / len(present)
    else:
        raise ValueError(f"unknown family_missing_policy {policy!r}")
    return FamilyResult(family, float(score), tuple(subs), reweighted=reweighted, meta=dict(meta))


def line_score(line: str, families: list[FamilyResult], weights: dict[str, float], unknown_below: float,
               *, exclude_insufficient: bool = False, **meta) -> LineResult:
    """`weights`＝該爻該期間**應有**的族權重（不適用的族不在其中）；`unknown_below`＝`Rules.unknown_below`（0.5）。

    `exclude_insufficient`（`Rules.coverage_excludes_insufficient`，裁定 2026-09-21，`docs/P3-CALIBRATION.md` §17）：
    **只有** `missing.reason == REASON_INSUFFICIENT`（歷史長度還不夠算）的族從**分母**排除；`contract_rolled`
    （換月日刻意降級，規格 `P1-B1-market.md:239`）與其餘缺值原因一律仍計入分母，語意不得混為一談。
    排除有一道暖機保護：**剩餘宣告權重佔比 < `unknown_below` 時不排除**——重播暖機期大量族都是
    `insufficient_history`，無條件排除會讓「只剩一族」也變成 ratio 1.0、憑極少證據吐分數。
    起因：大盤五爻族 C（VIX）因上游只有 2026-03 起而長期缺席（裁定 #26），使每月換月日族 B 一降級就
    0.4 < 0.5 → 整爻未知 → 大盤方向分數 Missing → **全市場個股上爻**連坐降級（訓練段 30／603 日）。
    **分數值只在跨越 `unknown_below` 時才改變**：分子 `got` 不受本參數影響，故未跨門檻的爻逐位不變。"""
    fam_by = {f.family: f for f in families}
    for name in weights:
        if name not in fam_by:
            raise KeyError(f"line {line}: family {name} has weight but no result")
    expected = sum(weights.values())
    if exclude_insufficient and expected > 0:
        absent = {n for n in weights
                  if fam_by[n].score is None and fam_by[n].missing is not None
                  and fam_by[n].missing.reason == REASON_INSUFFICIENT}
        remain = sum(w for n, w in weights.items() if n not in absent)
        if absent and remain / expected >= unknown_below:
            expected = remain
    got = sum(w for name, w in weights.items() if fam_by[name].score is not None)
    ratio = got / expected if expected > 0 else 0.0
    reweighted = any(fam_by[n].score is None or fam_by[n].reweighted for n in weights)
    if ratio < unknown_below:
        return LineResult(line, None, ratio, True, tuple(families), dict(weights), reweighted, dict(meta))
    score = sum(fam_by[n].score * w for n, w in weights.items() if fam_by[n].score is not None) / got
    return LineResult(line, float(score), ratio, False, tuple(families), dict(weights), reweighted, dict(meta))


def direction_score(lines: dict[str, LineResult], weights: dict[str, float], policy: str) -> float | Missing:
    """六爻加權方向分數（B1.7／B2.7）。任一爻「未知」時：
    裁定（2026-09-10，P2-KICKOFF §5 #22）：`policy="missing"`＝方向分數缺值、不重配（保守）；`"reweight"`＝其餘爻
    按原比例正規化（僅供比對）。由 `Rules.direction_unknown_policy` 決定並進指紋。"""
    known = {n: w for n, w in weights.items() if lines.get(n) is not None and lines[n].score is not None}
    if len(known) < len(weights):
        if policy == "missing":
            missing = [n for n in weights if n not in known]
            return Missing(REASON_LINE_UNKNOWN, f"line {missing[0]} unknown")
        if policy != "reweight":
            raise ValueError(f"unknown direction_unknown_policy {policy!r}")
        if not known:
            return Missing(REASON_LINE_UNKNOWN, "all lines unknown")
    wsum = sum(known.values())
    return float(sum(lines[n].score * w for n, w in known.items()) / wsum)


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
