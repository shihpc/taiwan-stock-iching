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
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "src"))

from iching.score.hexagram import king_wen_from_lines, lines_from_king_wen  # noqa: E402
from iching.scores_io import ScoreStore, ScoreStoreError  # noqa: E402

NA = "n/a"                      # 裁定 #58：無方向維度的項目
FLAG_NAMES = ("F-分歧", "F-廣度擴張", "F-廣度收縮", "F-臨界", "F-高波動")
DIRECTIONS = ("long", "short")
BIG_DIFF = 0.10                 # 規格：任一項差異 > 10% 須在登錄文件說明原因


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


def future_king_wen(king_wen: int | None, prev: tuple[int, ...] | None, cur: tuple[int, ...] | None) -> int | None:
    """之卦：把**所有**動爻翻轉後的卦。零動爻 → 之卦＝主卦（不是 None）。

    `hexagram.to_king_wen` 一次只翻一爻，多動爻要自己翻完再轉回卦號；用 `lines_from_king_wen`
    取主卦位元（**不直接用 `cur`**——主卦欄與爻態欄若不一致，以主卦欄為準，那是落地的事實來源）。
    """
    if king_wen is None:
        return None
    mv = moving_positions(prev, cur)
    b = lines_from_king_wen(int(king_wen))
    for p in mv:
        b[p - 1] ^= 1
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
        self.rank_pairs: Counter = Counter()
        self.rank_same: Counter = Counter()
        # ⑧ binding 率（逐側）
        self.bind_hit: dict[str, Counter] = {"before": Counter(), "after": Counter()}
        self.bind_den: dict[str, Counter] = {"before": Counter(), "after": Counter()}
        self.days = 0
        self.rows_matched = 0


def _key(market: str, horizon: str, direction: str = NA) -> tuple[str, str, str]:
    return (market, horizon, direction)


def _index(rows: list[dict]) -> tuple[dict, list[dict]]:
    """把一天的列拆成 {(stock_id, market, horizon): row} 與大盤列清單。"""
    stocks, markets = {}, []
    for r in rows:
        if r.get("scope") == "market_index" or r.get("stock_id") == "__MARKET__":
            markets.append(r)
        else:
            stocks[(r["stock_id"], r["market"], r["horizon"])] = r
    return stocks, markets


def step_day(acc: Acc, b_rows: list[dict], a_rows: list[dict],
             prev_bits: dict[str, dict], quota: dict) -> None:
    """吃一天的兩側列，更新累加器。`prev_bits[side][(sid,mk,h)]` 由呼叫端跨日保存。"""
    acc.days += 1
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
                    k = _key(r["market"], r["horizon"], d)
                    if f in act:
                        acc.flag_den[side][(k, f)] += 1
                        if act[f]:
                            acc.flag_hit[side][(k, f)] += 1
                q = (bydir.get(d) or {}).get("quota_multiplier")
                if q is not None:
                    quota[(side, r["market"], r["horizon"], d)] = float(q)

    # 逐檔
    for key, br in b_stk.items():
        ar = a_stk.get(key)
        if ar is None:
            continue
        acc.rows_matched += 1
        sid, mk, h = key
        k = _key(mk, h)
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

        # ⑥ 主卦與之卦
        bkw, akw = br.get("king_wen"), ar.get("king_wen")
        if bkw is not None and akw is not None:
            acc.kw_total[k] += 1
            acc.kw_same[k] += int(int(bkw) == int(akw))
            bf = future_king_wen(bkw, prev_bits["before"].get(key), bb)
            af = future_king_wen(akw, prev_bits["after"].get(key), ab)
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

    # ⑦ 名單與排名（逐日、逐方向）
    # 每側每 (market, horizon) **只排序一次**，逐方向只做切片——原本每個方向都重掃全部列，
    # 1,628 日 × 12 組 × 7,500 列 ≈ 1.46 億次，光這一項就跑不完。
    b_sorted = _sorted_pool(b_stk)
    a_sorted = _sorted_pool(a_stk)
    for mh in set(b_sorted) | set(a_sorted):
        mk, h = mh
        bl_all, al_all = b_sorted.get(mh, []), a_sorted.get(mh, [])
        for d in DIRECTIONS:
            q = quota.get(("after", mk, h, d), quota.get(("before", mk, h, d)))
            bl, al = _cut(bl_all, q), _cut(al_all, q)
            if not bl and not al:
                continue
            k = _key(mk, h, d)
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


def _cut(pool: list[str], quota_mult: float | None) -> list[str]:
    """名額乘數縮放後的前 N（缺乘數 → 不縮）。"""
    if not pool:
        return []
    n = len(pool) if quota_mult is None else max(1, int(round(len(pool) * float(quota_mult))))
    return pool[:n]


# ---------------------------------------------------------------------------
# 報告
# ---------------------------------------------------------------------------
def build_report(acc: Acc) -> dict:
    """八項 → 逐格數字。六項寫 `direction="n/a"`（裁定 #58）。"""
    def rows(counter_keys):
        return sorted({k for k in counter_keys})

    out: dict[str, Any] = {"schema": 1, "days": acc.days, "rows_matched": acc.rows_matched,
                           "direction_note": "①②④⑤⑥⑧ 無方向維度（做空分數未實作，direction 只存在於旗標解析層）"
                                             "，故 direction 欄為 n/a；不得複製成 long／short 兩列。",
                           "big_diff_threshold": BIG_DIFF, "items": {}}

    # ① 陰陽態逐日差異率
    out["items"]["1_line_state_diff_rate"] = [
        {"market": k[0], "horizon": k[1], "direction": k[2],
         "diff_rate": _one_minus(rate(acc.line_same[k], acc.line_total[k])),
         "n_lines": acc.line_total[k]}
        for k in rows(acc.line_total)]

    # ② 遲滯翻轉次數
    out["items"]["2_hysteresis_flips"] = [
        {"market": k[0], "horizon": k[1], "direction": k[2],
         "before": acc.flips["before"][k], "after": acc.flips["after"][k],
         "rel_diff": _rel(acc.flips["before"][k], acc.flips["after"][k])}
        for k in rows(set(acc.flips["before"]) | set(acc.flips["after"]))]

    # ③ 旗標觸發率
    keys3 = set(acc.flag_den["before"]) | set(acc.flag_den["after"])
    out["items"]["3_flag_hit_rate"] = [
        {"market": k[0][0], "horizon": k[0][1], "direction": k[0][2], "flag": k[1],
         "before": rate(acc.flag_hit["before"][k], acc.flag_den["before"][k]),
         "after": rate(acc.flag_hit["after"][k], acc.flag_den["after"][k]),
         "diff": _sub(rate(acc.flag_hit["after"][k], acc.flag_den["after"][k]),
                      rate(acc.flag_hit["before"][k], acc.flag_den["before"][k]))}
        for k in sorted(keys3)]

    # ④ 動爻數分布
    keys4 = {k[0] for k in set(acc.moving["before"]) | set(acc.moving["after"])}
    out["items"]["4_moving_line_count_dist"] = [
        {"market": k[0], "horizon": k[1], "direction": k[2],
         "before": {str(n): acc.moving["before"][(k, n)] for n in range(7) if acc.moving["before"][(k, n)]},
         "after": {str(n): acc.moving["after"][(k, n)] for n in range(7) if acc.moving["after"][(k, n)]},
         "tv_distance": _tv({n: acc.moving["before"][(k, n)] for n in range(7)},
                            {n: acc.moving["after"][(k, n)] for n in range(7)})}
        for k in sorted(keys4)]

    # ⑤ 內外卦方向判定差異率
    out["items"]["5_trigram_state_diff_rate"] = [
        {"market": k[0], "horizon": k[1], "direction": k[2],
         "diff_rate": _one_minus(rate(acc.tri_same[k], acc.tri_total[k])), "n": acc.tri_total[k]}
        for k in rows(acc.tri_total)]

    # ⑥ 主卦與之卦一致率
    out["items"]["6_hexagram_agreement"] = [
        {"market": k[0], "horizon": k[1], "direction": k[2],
         "king_wen_same_rate": rate(acc.kw_same[k], acc.kw_total[k]),
         "future_king_wen_same_rate": rate(acc.fkw_same[k], acc.fkw_total[k]),
         "n": acc.kw_total[k]}
        for k in rows(acc.kw_total)]

    # ⑦ 名單與排名重疊
    out["items"]["7_candidate_overlap"] = [
        {"market": k[0], "horizon": k[1], "direction": k[2],
         "jaccard": rate(acc.pool_inter[k], acc.pool_union[k]),
         "same_rank_rate": rate(acc.rank_same[k], acc.rank_pairs[k]),
         "n_pairs": acc.rank_pairs[k]}
        for k in rows(set(acc.pool_union))]

    # ⑧ binding 率
    keys8 = set(acc.bind_den["before"]) | set(acc.bind_den["after"])
    out["items"]["8_binding_rate"] = [
        {"market": k[0][0], "horizon": k[0][1], "direction": k[0][2], "column": k[1],
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
    for name, rowlist in items.items():
        for r in rowlist:
            for field in ("diff_rate", "diff", "rel_diff", "tv_distance"):
                if field in r and flagged(r[field]):
                    hits.append({"item": name, **{k: r[k] for k in ("market", "horizon", "direction")},
                                 "field": field, "value": r[field]})
            for field, inv in (("jaccard", True), ("same_rank_rate", True),
                               ("king_wen_same_rate", True), ("future_king_wen_same_rate", True)):
                if field in r and r[field] is not None and inv and (1.0 - r[field]) > BIG_DIFF:
                    hits.append({"item": name, **{k: r[k] for k in ("market", "horizon", "direction")},
                                 "field": field, "value": r[field]})
    return hits


def as_text(rep: dict) -> str:
    """純文字版。**數字與 JSON 同源**（同一個 dict 渲染），不另算一次。"""
    L = [f"§16.5 門檻行為重驗｜比對日數 {rep['days']}　配對列數 {rep['rows_matched']:,}",
         f"（{rep['direction_note']}）", ""]
    for name in sorted(rep["items"]):
        L.append(f"== {name}")
        for r in rep["items"][name]:
            head = f"  {r['market']:5s} {r['horizon']:6s} {r['direction']:5s}"
            rest = "  ".join(f"{k}={_fmt(v)}" for k, v in r.items()
                             if k not in ("market", "horizon", "direction"))
            L.append(f"{head}  {rest}")
        L.append("")
    over = rep["over_threshold"]
    L.append(f"== 差異 > {rep['big_diff_threshold']:.0%} 的格子：{len(over)} 個"
             + ("（規格要求逐項在登錄文件說明原因並確認是預期行為）" if over else "（無）"))
    for h in over:
        L.append(f"  {h['item']}  {h['market']}/{h['horizon']}/{h['direction']}  {h['field']}={_fmt(h['value'])}")
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
            quota: dict = {}
            for i, d in enumerate(common, 1):
                step_day(acc, sb.rows_for_day(dv_b, d), sa.rows_for_day(dv_a, d), prev_bits, quota)
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
