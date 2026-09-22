#!/usr/bin/env python3
"""規格 §16.5「門檻行為重驗」（`spec/stock-iching-plan-v1.2.2.md:717`）的八項差異報告。

    python3 scripts/revalidate_thresholds.py --before cache/scores_t717_before.db \
        --after cache/scores.db --out runs/t717/report.json

比對**同一段歷史**上、縮放（d 校準）前後的八項門檻行為。兩側的 db 都必須帶 §17（coverage 分母）
與 §18（三個出口欄），差異才只歸因於 d 縮放——設計與逐項定義見 `docs/P3-CALIBRATION.md` §20。

**裁定 #58（2026-09-22）**：規格要求八項按 `market × horizon × direction` 分組，但實作裡 `direction`
只存在於旗標解析層（`score/market.py` 的 `by_direction`），**沒有做空分數**。故只有 ③⑦ 有方向維度，
其餘六項一律寫 `direction="n/a"` 並在報告裡註明原因。**不得為了湊格式把同一個數字複製成 long／short
兩列**——那會讓讀的人以為是兩次獨立量測。

單趟掃描：逐日同時讀兩側的列，per-(stock, horizon) 只保留前一日的爻態位元（約 5,800 筆／側），
記憶體與 1,628 日的全量無關。
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "src"))

from iching.score.hexagram import king_wen_from_lines, lines_from_king_wen  # noqa: E402
from iching.score.market import FLAG_NAMES as _MARKET_FLAG_NAMES  # noqa: E402
from iching.score.params import Rules  # noqa: E402
from iching.scores_io import ScoreStore, ScoreStoreError  # noqa: E402

NA = "n/a"                      # 裁定 #58：無方向維度的項目（#59 另加 `scope`）
#: 直接用上游那份，**不自己寫死字面量**——上游增減旗標時字面量會靜默漂移，
#: 而 ③ 的「哪幾支旗標」正是本報告要交代的東西之一。
FLAG_NAMES = tuple(sorted(_MARKET_FLAG_NAMES))
DIRECTIONS = ("long", "short")
BIG_DIFF = 0.10                 # 規格：任一項差異 > 10% 須在登錄文件說明原因
#: 遲滯確認天數。**從 `Rules` 取、不寫死 2**——它不在校準範圍內、前後側必然相同，
#: 但若哪天改了，⑥ 的「待確認動爻」判準要跟著改，寫死會靜默失準。
CONFIRM_DAYS = Rules().hysteresis_confirm_days
if CONFIRM_DAYS < 2:                    # `>= CONFIRM_DAYS - 1` 在 1 之下恆真＝六爻全「待確認」，
    raise RuntimeError(               # 之卦會變成主卦的全反（實測：乾 1 → 坤 2），整項失去意義。
        f"hysteresis_confirm_days={CONFIRM_DAYS} < 2：⑥ 的「待確認動爻」判準不成立，先重新定義再跑")


class RevalidateError(Exception):
    pass


# ---------------------------------------------------------------------------
# 純函式（可離線測）
# ---------------------------------------------------------------------------
def bits_of(lines_formal: Any) -> tuple[int, ...] | None:
    """`lines_formal` → 6 個位元。落地型別是 list（`rows_for_day` 已還原）或 6 字元字串。"""
    if lines_formal is None:
        return None
    if isinstance(lines_formal, str):
        if len(lines_formal) != 6 or any(c not in "01" for c in lines_formal):
            return None
        return tuple(int(c) for c in lines_formal)
    b = tuple(int(x) for x in lines_formal)
    return b if len(b) == 6 and all(x in (0, 1) for x in b) else None


def moving_positions(prev: tuple[int, ...] | None, cur: tuple[int, ...] | None) -> tuple[int, ...]:
    """動爻位（1–6，初→上）＝相鄰兩日正式爻態改變的位置。任一側缺 → 空（不猜）。"""
    if prev is None or cur is None:
        return ()
    return tuple(i + 1 for i in range(6) if prev[i] != cur[i])


def pending_positions(streaks: str | None, confirm_days: int = CONFIRM_DAYS) -> tuple[int, ...] | None:
    """**待確認動爻**位（1–6）：`streaks[i] >= confirm_days - 1` 的爻。

    `streaks` 是遲滯的確認天數計數器（`hexagram.hysteresis_step`）：正式爻態為陰而分數 ≥55、
    或正式爻態為陽而分數 ≤45 時才累加，未達門檻立刻歸零，累到 `confirm_days` 就翻爻並歸零。
    所以 `streak == confirm_days - 1` ＝**再站穩一日就翻**，正是 v1.2.2 §8 的「候選變化」。

    解不出（欄位缺、格式壞、長度非 6）→ None，**不得當成「零待確認動爻」**——那會把
    「不知道」寫成「什麼都不會變」。
    """
    if not streaks:
        return None
    parts = str(streaks).split(",")
    if len(parts) != 6:
        return None
    try:
        vals = [int(x) for x in parts]
    except ValueError:
        return None
    return tuple(i + 1 for i, v in enumerate(vals) if v >= confirm_days - 1)


def pending_king_wen(king_wen: int | None, streaks: str | None,
                     confirm_days: int = CONFIRM_DAYS) -> int | None:
    """**前瞻式之卦**（裁定 #59）：把待確認動爻翻轉後的卦＝「若明日續站門檻另一側，卦會變成這個」。

    零待確認動爻 → 之卦＝主卦（沒有東西正要變，不是 None）。主卦位元取
    `lines_from_king_wen(king_wen)`——主卦欄是落地的事實來源，與爻態欄不一致時以它為準。

    **刻意不用「昨日→今日位元差」當動爻**（首版的做法）：那樣翻出來的是**昨日**的卦，
    「之卦一致率」會恆等於「主卦一致率」落後一日，既不前瞻也不提供新資訊（使用者裁定 #59）。
    """
    if king_wen is None:
        return None
    mv = pending_positions(streaks, confirm_days)
    if mv is None:
        return None
    b = lines_from_king_wen(int(king_wen))
    for pos in mv:
        b[pos - 1] ^= 1
    return king_wen_from_lines(b)


def trigram_state(score: float | None, hi: float = 55.0, lo: float = 45.0) -> str | None:
    """內外卦方向判定的三態：`ge55`／`le45`／`mid`。缺值 → None（不當成 mid）。"""
    if score is None:
        return None
    v = float(score)
    return "ge55" if v >= hi else ("le45" if v <= lo else "mid")


def rate(num: int, den: int) -> float | None:
    """比率；分母為 0 → None（**不寫 0.0**，「沒有母體」與「比率為零」是兩件事）。"""
    return None if den == 0 else num / den


def flagged(diff: float | None) -> bool:
    return diff is not None and abs(diff) > BIG_DIFF


# ---------------------------------------------------------------------------
# 累加器：一個 key 一格，key＝(market, horizon, direction)
# ---------------------------------------------------------------------------
class Acc:
    """八項的累加狀態。每一項各自記「前側」「後側」與「兩側比對」三種量。"""

    def __init__(self) -> None:
        # ① 陰陽態：逐爻比對兩側
        self.line_same: Counter = Counter()
        self.line_total: Counter = Counter()
        # ② 遲滯翻轉次數（逐側）
        self.flips: dict[str, Counter] = {"before": Counter(), "after": Counter()}
        # ③ 旗標觸發（逐側；大盤逐方向、個股 overheated 走 n/a）
        self.flag_hit: dict[str, Counter] = {"before": Counter(), "after": Counter()}
        self.flag_den: dict[str, Counter] = {"before": Counter(), "after": Counter()}
        # ④ 動爻數分布（逐側）
        self.moving: dict[str, Counter] = {"before": Counter(), "after": Counter()}
        # ⑤ 內外卦三態差異
        self.tri_same: Counter = Counter()
        self.tri_total: Counter = Counter()
        # ⑥ 主卦／之卦一致
        self.kw_same: Counter = Counter()
        self.kw_total: Counter = Counter()
        self.fkw_same: Counter = Counter()
        self.fkw_total: Counter = Counter()
        # ⑦ 名單重疊（逐日 Jaccard 累加）
        self.pool_inter: Counter = Counter()
        self.pool_union: Counter = Counter()
        self.pool_days: Counter = Counter()
        self.pool_undetermined: Counter = Counter()
        self.pool_state_mismatch: Counter = Counter()
        self.pool_quota_zero: Counter = Counter()
        self.rank_pairs: Counter = Counter()
        self.rank_same: Counter = Counter()
        # ⑧ binding 率（逐側）
        self.bind_hit: dict[str, Counter] = {"before": Counter(), "after": Counter()}
        self.bind_den: dict[str, Counter] = {"before": Counter(), "after": Counter()}
        self.days = 0
        self.rows_matched = 0
        self.market_rows_matched = 0


def _key(market: str, horizon: str, direction: str = NA,
         scope: str = "stock") -> tuple[str, str, str, str]:
    """分組鍵。`scope` ∈ {stock, market}——裁定 #59：①②④⑤⑥ 也吃大盤列，但**大盤另成一組**，
    不得混進個股平均（每市場每期間大盤只有 1 檔，混進去會被 7,500 檔稀釋到看不見）。"""
    return (scope, market, horizon, direction)


def _index(rows: list[dict]) -> tuple[dict, list[dict]]:
    """把一天的列拆成 {(stock_id, market, horizon): row} 與大盤列清單。"""
    stocks, markets = {}, []
    for r in rows:
        if r.get("scope") == "market_index" or r.get("stock_id") == "__MARKET__":
            markets.append(r)
        else:
            stocks[(r["stock_id"], r["market"], r["horizon"])] = r
    return stocks, markets


def _by_key(rows: list[dict]) -> dict[tuple[str, str, str], dict]:
    return {(str(r.get("stock_id")), r["market"], r["horizon"]): r for r in rows}


def _row_pair(acc: Acc, scope: str, key: tuple[str, str, str],
              br: dict, ar: dict, prev_bits: dict[str, dict]) -> None:
    """①②④⑤⑥⑧ 的單列（前後側成對）處理。個股列與大盤列共用，靠 `scope` 分組。"""
    _, mk, h = key
    k = _key(mk, h, scope=scope)
    bb, ab = bits_of(br.get("lines_formal")), bits_of(ar.get("lines_formal"))

    # ① 陰陽態逐爻
    if bb is not None and ab is not None:
        acc.line_total[k] += 6
        acc.line_same[k] += sum(1 for i in range(6) if bb[i] == ab[i])

    # ②④ 需要前一日
    for side, cur in (("before", bb), ("after", ab)):
        prev = prev_bits[side].get(key)
        if prev is not None and cur is not None:
            mv = moving_positions(prev, cur)
            acc.flips[side][k] += len(mv)
            acc.moving[side][(k, len(mv))] += 1
        if cur is not None:
            prev_bits[side][key] = cur

    # ⑤ 內外卦三態
    for col in ("inner_trigram_score", "outer_trigram_score"):
        bs, as_ = trigram_state(br.get(col)), trigram_state(ar.get(col))
        if bs is not None and as_ is not None:
            acc.tri_total[k] += 1
            acc.tri_same[k] += int(bs == as_)

    # ⑥ 主卦與（前瞻式）之卦——之卦只看今日的 `streaks`，**不吃昨日位元**，故與 ②④ 的寫回無關
    bkw, akw = br.get("king_wen"), ar.get("king_wen")
    if bkw is not None and akw is not None:
        acc.kw_total[k] += 1
        acc.kw_same[k] += int(int(bkw) == int(akw))
        bf = pending_king_wen(bkw, br.get("streaks"))
        af = pending_king_wen(akw, ar.get("streaks"))
        if bf is not None and af is not None:
            acc.fkw_total[k] += 1
            acc.fkw_same[k] += int(bf == af)

    # ⑧ binding 率（分母排除 None——§18：None 是「不適用」不是「沒觸發」）
    for side, r in (("before", br), ("after", ar)):
        for col in ("floor_applied", "overheat_cap_applied"):
            v = r.get(col)
            if v is not None:
                acc.bind_den[side][(k, col)] += 1
                if int(v):
                    acc.bind_hit[side][(k, col)] += 1
        hot = r.get("overheated")
        if hot is not None:
            acc.flag_den[side][(k, "overheated")] += 1
            if int(hot):
                acc.flag_hit[side][(k, "overheated")] += 1


def step_day(acc: Acc, b_rows: list[dict], a_rows: list[dict],
             prev_bits: dict[str, dict]) -> None:
    """吃一天的兩側列，更新累加器。`prev_bits[side][(sid,mk,h)]` 由呼叫端跨日保存。

    名額乘數與基本狀態**只在當日有意義**，故是日內區域變數。首版把它們放在呼叫端跨日共用的
    dict 裡，某日大盤列缺席時會靜默沿用昨日的乘數（2026-09-22 驗收抓到）。
    """
    acc.days += 1
    quota: dict = {}                 # (side, market, horizon, direction) -> 名額乘數
    state: dict = {}                 # (side, market, horizon)            -> 基本狀態 S1–S4
    b_stk, b_mkt = _index(b_rows)
    a_stk, a_mkt = _index(a_rows)

    # ③ 大盤旗標（逐方向）＋ ⑦ 用的名額乘數
    for side, mrows in (("before", b_mkt), ("after", a_mkt)):
        for r in mrows:
            fl = r.get("flags")
            if not isinstance(fl, dict):
                continue
            bydir = fl.get("by_direction") or {}
            for d in DIRECTIONS:
                act = (bydir.get(d) or {}).get("active") or {}
                for f in FLAG_NAMES:
                    k = _key(r["market"], r["horizon"], d, scope="market")
                    if f in act:
                        acc.flag_den[side][(k, f)] += 1
                        if act[f]:
                            acc.flag_hit[side][(k, f)] += 1
                q = (bydir.get(d) or {}).get("quota_multiplier")
                if q is not None:
                    quota[(side, r["market"], r["horizon"], d)] = float(q)
            bs = fl.get("basic_state")
            if bs is not None:
                state[(side, r["market"], r["horizon"])] = str(bs)

    # ①②④⑤⑥⑧ 逐列。裁定 #59：個股與大盤**各跑一遍、各成一組**。
    # ⑧ 與 ③ 的 `overheated` 在大盤列一律 NULL，自然不計數，不必特判。
    b_mkt_ix = _by_key(b_mkt)
    a_mkt_ix = _by_key(a_mkt)
    for scope, b_ix, a_ix in (("stock", b_stk, a_stk), ("market", b_mkt_ix, a_mkt_ix)):
        for key, br in b_ix.items():
            ar = a_ix.get(key)
            if ar is None:
                continue
            if scope == "stock":
                acc.rows_matched += 1
            else:
                acc.market_rows_matched += 1
            _row_pair(acc, scope, key, br, ar, prev_bits)


    # ⑦ 名單與排名（逐日、逐方向）
    # 每側每 (market, horizon) **只排序一次**，逐方向只做切片——原本每個方向都重掃全部列，
    # 1,628 日 × 12 組 × 7,500 列 ≈ 1.46 億次，光這一項就跑不完。
    b_sorted = _sorted_pool(b_stk)
    a_sorted = _sorted_pool(a_stk)
    for mh in set(b_sorted) | set(a_sorted):
        mk, h = mh
        bl_all, al_all = b_sorted.get(mh, []), a_sorted.get(mh, [])
        bs_b, bs_a = state.get(("before", mk, h)), state.get(("after", mk, h))
        for d in DIRECTIONS:
            k = _key(mk, h, d)
            # 基本狀態未定 → 該市場當日不出名單（S1 §A1.1 末句），兩側任一未定即整格跳過，
            # 並單獨記次數——**不可當成「名單相同」或「名單全空」灌進 Jaccard**。
            if _n0(bs_b, d) is None or _n0(bs_a, d) is None:
                acc.pool_undetermined[k] += 1
                continue
            if bs_b != bs_a:
                acc.pool_state_mismatch[k] += 1
            bl = _cut(bl_all, _n0(bs_b, d), quota.get(("before", mk, h, d)))
            al = _cut(al_all, _n0(bs_a, d), quota.get(("after", mk, h, d)))
            acc.pool_days[k] += 1
            if not bl and not al:
                # 名額被乘成 0（真實乘數落在 0.105~0.25，`floor(5 × 0.105)` 就是 0）與
                # 「有名單但零重疊」在報告上長得一樣，單獨記一筆才分得出來。
                if _cut_n(_n0(bs_b, d), quota.get(("before", mk, h, d))) == 0 \
                        and _cut_n(_n0(bs_a, d), quota.get(("after", mk, h, d))) == 0:
                    acc.pool_quota_zero[k] += 1
                continue
            sb, sa = set(bl), set(al)
            acc.pool_inter[k] += len(sb & sa)
            acc.pool_union[k] += len(sb | sa)
            ai = {s: i for i, s in enumerate(al)}          # O(1) 查名次，取代 list.index 的 O(n)
            for i, s in enumerate(bl):
                j = ai.get(s)
                if j is not None:
                    acc.rank_pairs[k] += 1
                    acc.rank_same[k] += int(i == j)


def _sorted_pool(stk: dict) -> dict[tuple[str, str], list[str]]:
    """每 (market, horizon) 的候選池依 `base_score` 降冪排好的代號序列。

    排序次鍵取 `stock_id`——同分時若不帶次鍵，兩側順序會因字典走訪序而不穩，⑦ 的排名重疊率
    就會量到假差異（與 `budget.py`／`sectors.py` 那條「排序一律帶次鍵」是同一個家族教訓）。
    """
    by: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for (sid, mk, h), r in stk.items():
        if r.get("in_rank_pool") and r.get("base_score") is not None:
            by[(mk, h)].append(r)
    out = {}
    for mh, rows in by.items():
        rows.sort(key=lambda r: (-float(r["base_score"]), str(r["stock_id"])))
        out[mh] = [r["stock_id"] for r in rows]
    return out


#: S1 §A1.2 的 `N0`（基本狀態 × 方向）。**`quota_multiplier` 乘的是 `N0`、不是池大小**——
#: 首版寫成 `round(池大小 × 乘數)`，量到的是「池縮放」而不是「名額上限」，差兩個數量級，
#: 且乘數為 1.0 時 Jaccard 恆等於 1（2026-09-22 驗收抓到）。
N0_TABLE: dict[tuple[str, str], int] = {
    ("S1", "long"): 20, ("S1", "short"): 5,
    ("S2", "long"): 12, ("S2", "short"): 10,
    ("S3", "long"): 10, ("S3", "short"): 10,
    ("S4", "long"): 5, ("S4", "short"): 20,
}


def _n0(basic_state: str | None, direction: str) -> int | None:
    """基本狀態 × 方向 → `N0`；未定／非 S1–S4 → None（當日不出名單）。"""
    if basic_state is None:
        return None
    return N0_TABLE.get((str(basic_state), direction))


def _cut_n(n0: int | None, quota_mult: float | None) -> int:
    """名額 N＝**`floor(N0 × 連乘)`、下限 0**（`spec/P1-B1-market.md`:345「連乘後無條件捨去，下限 0」）。

    **必須是 `floor` 不是 `round`**：真實的 `quota_multiplier` 落在 0.105~0.25 這個帶，
    兩者在八格 `N0` 上大量分歧（例：`N0=20`、mult=0.141 → floor 2 / round 3）。缺乘數 → 視為 1.0。
    """
    if n0 is None:
        return 0
    return max(0, math.floor(n0 * (1.0 if quota_mult is None else float(quota_mult))))


def _cut(pool: list[str], n0: int | None, quota_mult: float | None) -> list[str]:
    if not pool or n0 is None:
        return []
    return pool[:_cut_n(n0, quota_mult)]


# ---------------------------------------------------------------------------
# 報告
# ---------------------------------------------------------------------------
def build_report(acc: Acc) -> dict:
    """八項 → 逐格數字。六項寫 `direction="n/a"`（裁定 #58）。"""
    def rows(counter_keys):
        return sorted({k for k in counter_keys})

    out: dict[str, Any] = {"schema": 2, "days": acc.days, "rows_matched": acc.rows_matched,
                           "market_rows_matched": acc.market_rows_matched,
                           "scope_note": "裁定 #59：①②④⑤⑥ 個股列與大盤列各成一組（scope=stock／market），"
                                         "不得混算；③ 的五支大盤旗標本就只有 scope=market，"
                                         "③ 的 overheated 與 ⑧ 只有 scope=stock（大盤列該三欄一律 NULL）。",
                           "direction_note": "①②④⑤⑥⑧ 無方向維度（做空分數未實作，direction 只存在於旗標解析層）"
                                             "，故 direction 欄為 n/a；不得複製成 long／short 兩列。",
                           "big_diff_threshold": BIG_DIFF, "items": {}}

    # ① 陰陽態逐日差異率
    out["items"]["1_line_state_diff_rate"] = [
        {"scope": k[0], "market": k[1], "horizon": k[2], "direction": k[3],
         "diff_rate": _one_minus(rate(acc.line_same[k], acc.line_total[k])),
         "n_lines": acc.line_total[k]}
        for k in rows(acc.line_total)]

    # ② 遲滯翻轉次數
    out["items"]["2_hysteresis_flips"] = [
        {"scope": k[0], "market": k[1], "horizon": k[2], "direction": k[3],
         "before": acc.flips["before"][k], "after": acc.flips["after"][k],
         "rel_diff": _rel(acc.flips["before"][k], acc.flips["after"][k])}
        for k in rows(set(acc.flips["before"]) | set(acc.flips["after"]))]

    # ③ 旗標觸發率
    keys3 = set(acc.flag_den["before"]) | set(acc.flag_den["after"])
    out["items"]["3_flag_hit_rate"] = [
        {"scope": k[0][0], "market": k[0][1], "horizon": k[0][2], "direction": k[0][3], "flag": k[1],
         "before": rate(acc.flag_hit["before"][k], acc.flag_den["before"][k]),
         "after": rate(acc.flag_hit["after"][k], acc.flag_den["after"][k]),
         "diff": _sub(rate(acc.flag_hit["after"][k], acc.flag_den["after"][k]),
                      rate(acc.flag_hit["before"][k], acc.flag_den["before"][k])),
         "n_before": acc.flag_den["before"][k], "n_after": acc.flag_den["after"][k]}
        for k in sorted(keys3)]

    # ④ 動爻數分布
    keys4 = {k[0] for k in set(acc.moving["before"]) | set(acc.moving["after"])}
    out["items"]["4_moving_line_count_dist"] = [
        {"scope": k[0], "market": k[1], "horizon": k[2], "direction": k[3],
         "before": {str(n): acc.moving["before"][(k, n)] for n in range(7) if acc.moving["before"][(k, n)]},
         "after": {str(n): acc.moving["after"][(k, n)] for n in range(7) if acc.moving["after"][(k, n)]},
         "tv_distance": _tv({n: acc.moving["before"][(k, n)] for n in range(7)},
                            {n: acc.moving["after"][(k, n)] for n in range(7)})}
        for k in sorted(keys4)]

    # ⑤ 內外卦方向判定差異率
    out["items"]["5_trigram_state_diff_rate"] = [
        {"scope": k[0], "market": k[1], "horizon": k[2], "direction": k[3],
         "diff_rate": _one_minus(rate(acc.tri_same[k], acc.tri_total[k])), "n": acc.tri_total[k]}
        for k in rows(acc.tri_total)]

    # ⑥ 主卦與之卦一致率
    out["items"]["6_hexagram_agreement"] = [
        {"scope": k[0], "market": k[1], "horizon": k[2], "direction": k[3],
         "king_wen_same_rate": rate(acc.kw_same[k], acc.kw_total[k]),
         "future_king_wen_same_rate": rate(acc.fkw_same[k], acc.fkw_total[k]),
         "n": acc.kw_total[k]}
        for k in rows(acc.kw_total)]

    # ⑦ 名單與排名重疊
    out["items"]["7_candidate_overlap"] = [
        {"scope": k[0], "market": k[1], "horizon": k[2], "direction": k[3],
         "jaccard": rate(acc.pool_inter[k], acc.pool_union[k]),
         "same_rank_rate": rate(acc.rank_same[k], acc.rank_pairs[k]),
         "n_pairs": acc.rank_pairs[k],
         "n_days": acc.pool_days[k],
         "days_state_undetermined": acc.pool_undetermined[k],
         "days_basic_state_differs": acc.pool_state_mismatch[k],
         "days_quota_zero": acc.pool_quota_zero[k]}
        for k in rows(set(acc.pool_days) | set(acc.pool_undetermined))]

    # ⑧ binding 率
    keys8 = set(acc.bind_den["before"]) | set(acc.bind_den["after"])
    out["items"]["8_binding_rate"] = [
        {"scope": k[0][0], "market": k[0][1], "horizon": k[0][2], "direction": k[0][3], "column": k[1],
         "before": rate(acc.bind_hit["before"][k], acc.bind_den["before"][k]),
         "after": rate(acc.bind_hit["after"][k], acc.bind_den["after"][k]),
         "diff": _sub(rate(acc.bind_hit["after"][k], acc.bind_den["after"][k]),
                      rate(acc.bind_hit["before"][k], acc.bind_den["before"][k])),
         "n_before": acc.bind_den["before"][k], "n_after": acc.bind_den["after"][k]}
        for k in sorted(keys8)]

    out["over_threshold"] = _over(out["items"])
    return out


def _one_minus(v: float | None) -> float | None:
    return None if v is None else 1.0 - v


def _sub(a: float | None, b: float | None) -> float | None:
    return None if (a is None or b is None) else a - b


def _rel(before: int, after: int) -> float | None:
    """相對變化；前側為 0 時回 None（**不寫 0 也不寫無限大**，沒有母體就是算不出比例）。"""
    return None if before == 0 else (after - before) / before


def _tv(a: dict[int, int], b: dict[int, int]) -> float | None:
    """兩個動爻數分布的 total variation distance（0～1）。任一側無樣本 → None。"""
    sa, sb = sum(a.values()), sum(b.values())
    if sa == 0 or sb == 0:
        return None
    return 0.5 * sum(abs(a.get(n, 0) / sa - b.get(n, 0) / sb) for n in range(7))


def _over(items: dict) -> list[dict]:
    """差異 > 10% 的格子（規格：須在登錄文件說明原因並確認是預期行為）。"""
    hits = []
    # **分組鍵有四個維度，這裡一個都不能少**：裁定 #59 加了 `scope` 之後，大盤與個股在同一
    # (market, horizon) 同時超標會印出兩列逐字相同、分不出是誰——而這份清單正是登錄書要
    # 「逐項說明原因」的輸入。這是本 repo 已知坑 #1「宣告的鍵少於實際的變動來源」的同型復發。
    keys = ("scope", "market", "horizon", "direction")
    for name, rowlist in items.items():
        for r in rowlist:
            for field in ("diff_rate", "diff", "rel_diff", "tv_distance"):
                if field in r and flagged(r[field]):
                    hits.append({"item": name, **{k: r[k] for k in keys},
                                 "field": field, "value": r[field]})
            for field, inv in (("jaccard", True), ("same_rank_rate", True),
                               ("king_wen_same_rate", True), ("future_king_wen_same_rate", True)):
                if field in r and r[field] is not None and inv and (1.0 - r[field]) > BIG_DIFF:
                    hits.append({"item": name, **{k: r[k] for k in keys},
                                 "field": field, "value": r[field]})
    return hits


def as_text(rep: dict) -> str:
    """純文字版。**數字與 JSON 同源**（同一個 dict 渲染），不另算一次。"""
    L = [f"§16.5 門檻行為重驗｜比對日數 {rep['days']}　配對個股列 {rep['rows_matched']:,}"
         f"　配對大盤列 {rep['market_rows_matched']:,}",
         f"（{rep['direction_note']}）", f"（{rep['scope_note']}）", ""]
    for name in sorted(rep["items"]):
        L.append(f"== {name}")
        for r in rep["items"][name]:
            head = f"  {r['scope']:6s} {r['market']:5s} {r['horizon']:6s} {r['direction']:5s}"
            rest = "  ".join(f"{k}={_fmt(v)}" for k, v in r.items()
                             if k not in ("scope", "market", "horizon", "direction"))
            L.append(f"{head}  {rest}")
        L.append("")
    over = rep["over_threshold"]
    L.append(f"== 差異 > {rep['big_diff_threshold']:.0%} 的格子：{len(over)} 個"
             + ("（規格要求逐項在登錄文件說明原因並確認是預期行為）" if over else "（無）"))
    for h in over:
        L.append(f"  {h['item']}  {h['scope']}/{h['market']}/{h['horizon']}/{h['direction']}"
                 f"  {h['field']}={_fmt(h['value'])}")
    return "\n".join(L) + "\n"


def _fmt(v: Any) -> str:
    if v is None:
        return "—"
    if isinstance(v, float):
        return f"{v:.4f}"
    if isinstance(v, dict):
        return "{" + ",".join(f"{k}:{x}" for k, x in sorted(v.items())) + "}"
    return str(v)


def run(args: argparse.Namespace) -> int:
    before, after = Path(args.before), Path(args.after)
    for p in (before, after):
        if not p.exists():
            print(f"[revalidate 中止] 找不到 {p}", file=sys.stderr)
            return 2
    try:
        with ScoreStore(before, readonly=True) as sb, ScoreStore(after, readonly=True) as sa:
            dv_b = args.data_version or _only_dv(sb)
            dv_a = args.data_version or _only_dv(sa)
            if dv_b != dv_a:
                raise RevalidateError(f"兩側 data_version 不同（{dv_b} vs {dv_a}）；不是同一段歷史，拒比")
            sha_b, sha_a = sb.params_sha_of(dv_b), sa.params_sha_of(dv_a)
            if sha_b == sha_a:
                raise RevalidateError(
                    f"兩側 params_sha 相同（{sha_b}）——這不是「縮放前後」而是同一份參數，"
                    "多半是前側那次重播漏了 --uncalibrated。拒比，免得產出一份全零的假報告。")
            db, da = sb.dates(dv_b), sa.dates(dv_a)
            common = [d for d in db if d in set(da)]
            if not common:
                raise RevalidateError("兩側沒有共同日期")
            print(f"前側 {before.name} params_sha={sha_b}　後側 {after.name} params_sha={sha_a}", flush=True)
            print(f"共同日期 {len(common)} 日（{common[0]}..{common[-1]}）；"
                  f"前側獨有 {len(db) - len(common)} 日、後側獨有 {len(da) - len(common)} 日", flush=True)
            acc = Acc()
            prev_bits = {"before": {}, "after": {}}
            for i, d in enumerate(common, 1):
                step_day(acc, sb.rows_for_day(dv_b, d), sa.rows_for_day(dv_a, d), prev_bits)
                if args.progress_every and i % args.progress_every == 0:
                    print(f"  {i}/{len(common)} 日（{d}）", flush=True)
            rep = build_report(acc)
            rep["before"] = {"path": str(before), "params_sha": sha_b}
            rep["after"] = {"path": str(after), "params_sha": sha_a}
            rep["data_version"] = dv_b
            rep["dates"] = {"n": len(common), "first": common[0], "last": common[-1]}
    except (ScoreStoreError, RevalidateError) as e:
        print(f"[revalidate 中止] {e}", file=sys.stderr)
        return 2
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(rep, ensure_ascii=False, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    txt = out.with_suffix(".txt")
    txt.write_text(as_text(rep), encoding="utf-8")
    print(as_text(rep))
    print(f"寫出 {out} 與 {txt}", flush=True)
    return 0


def _only_dv(store: ScoreStore) -> str:
    dvs = [r[0] for r in store.conn.execute("SELECT DISTINCT data_version FROM replay_meta ORDER BY data_version")]
    if len(dvs) != 1:
        raise RevalidateError(f"db 有 {len(dvs)} 個 data_version（{dvs}），請用 --data-version 指定")
    return dvs[0]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="§16.5 門檻行為重驗：縮放（d 校準）前後的八項差異")
    ap.add_argument("--before", required=True, help="前側 scores.db（舊 d；以 replay_scores --uncalibrated 產）")
    ap.add_argument("--after", required=True, help="後側 scores.db（新 d；生產用那份）")
    ap.add_argument("--data-version", help="兩側共用；省略時各自取 db 內唯一的那個")
    ap.add_argument("--out", default="runs/t717/report.json", help="輸出 JSON（同名 .txt 一併寫出）")
    ap.add_argument("--progress-every", type=int, default=100, help="每 N 日印一次進度（0＝不印）")
    return run(ap.parse_args(argv))


if __name__ == "__main__":
    raise SystemExit(main())
