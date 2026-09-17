#!/usr/bin/env python3
"""PIT 池切換報告（P3 第 1 項，`docs/P3-PIT-POOL.md` §2 #1／#9）——**唯讀**，供 `scripts/hetzner_pit.sh` 與人工看異常。

    python3 scripts/pit_report.py transitions --cache-dir cache [--out runs/pit/transitions.json]
    python3 scripts/pit_report.py compare --old cache/scores_prepit.db --new cache/scores.db [--from D1 --to D2] [--show 20]

- `transitions`：`universe.PitPool.report_transitions()`——有市場轉換的代號與序列、異常（>2 次／來回／上市→上櫃逆向），
  另列 11 檔「跨 twse/tpex」的轉換日（人工對官方掛牌日抽查用）。
- `compare`：舊（靜態快照池）vs 新（PIT 池）`scores.db` 逐日比對摘要。**預期差異只落在三類**（§2 #9）：
  (a) 轉換過市場的檔與其影響的市場列（含同市場其他個股列——廣度母體變了，方向分數會連帶）
  (b) 快照外的下市股（**本版 PIT 池不含快照外代號**，此類應為 0，見 §6 未做項）
  (c) 興櫃轉上櫃前的日子（該檔改前在池、改後不在）。
  分不出的差異列進 `unexplained`，rc 1；兩個 DB 的 `params_sha` 本來就不同（`pool_semantics` 進指紋），**不當錯**。
  歸類（2026-09-17 驗收退回後收窄，原版「當日有任一 (a)/(c) 就整日連帶」在 153 檔興櫃期幾乎每天都成立、未解釋永遠不觸發）：
  個股列 sid ∈ (a)∪(b)∪(c)（含兩側皆有但值不同的列）→ 直接歸該類；市場列 `__MARKET__` 在當日有任一類的日子 → 連帶；
  **其他 sid 的個股列**只有在差異欄 ⊆ `POOL_DEPENDENT_COLS`（池成員集合會傳導到的欄，**由程式碼路徑推導**，見該常數上方）
  且當日有任一類時才歸連帶，否則 `unexplained`（rc 1）。`unexplained` 仍可能是真差異被遮（同日同時有傳導與獨立 bug 且只動
  池依賴欄）——報告印當日連帶列數與差異欄直方圖。
  **2026-09-18 由 `LINKED_COLS`＝{`line_6`, `outer_trigram_score`, `base_score`} 改成 `POOL_DEPENDENT_COLS`**：舊集合是合成世界
  觀測歸納的（幾檔、十幾日，T<E 的非入池檔差異恰為那三欄），Hetzner 真實 1,628 日一跑 1,618 日未解釋（`docs/P3-PIT-POOL.md`
  §6.8）——產業中位報酬（同市場×同產業桶）還餵 `line_3` 族 B，長視窗 10 日、合成世界根本沒跑到第 11 日。第三輪直方圖
  （369 萬列、83 種組合）出現過的欄恰為下列集合的子集，`line_1/2/4/5` 零出現。
  **未解釋的欄位直方圖（2026-09-17 首輪實跑後補）**：首輪 Hetzner 比對得 154 萬列未解釋（1,618 日）而報告沒記差異欄，
  分不出「PIT 池本來就會連動別的欄」還是真 bug。現在每日記 `unexplained_cols`（兩側皆有的未解釋列：差異欄組合→列數）與
  `unexplained_onesided`（只在單側的未解釋列：`old`／`new` 各幾列），全期間彙總 `unexplained_col_hist`／`unexplained_onesided_total`，
  文字報告印 top 10 欄位組合。純觀測欄位、不改歸類與 rc。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "src"))
sys.path.insert(0, str(REPO / "scripts"))

import diff_scores as DS  # noqa: E402
from iching import feed as F  # noqa: E402
from iching.score.assemble import MARKET_STOCK_ID  # noqa: E402
from iching.scores_io import ScoreStore  # noqa: E402
from iching.universe import POOL_TYPES, PitPool  # noqa: E402


def transitions_report(pool: PitPool) -> dict:
    rep = pool.report_transitions()
    cross: dict[str, list] = {}
    reverse: dict[str, str] = {}
    for sid, seq in rep["transitions"].items():
        types = [t for _, t in seq]
        if len(set(types) & POOL_TYPES) >= 2:
            cross[sid] = seq
        for (_, a), (_, b) in zip(seq, seq[1:]):
            if a == "twse" and b == "tpex":
                reverse[sid] = "上市→上櫃（逆向，通常不發生；請對官方公告）"
    anomalies = dict(rep["anomalies"])
    for sid, why in reverse.items():
        anomalies[sid] = (anomalies[sid] + "；" if sid in anomalies else "") + why
    return {"pool_size": len(pool), "n_transitioned": rep["n_transitioned"], "n_cross_market": len(cross),
            "cross_market": cross, "anomalies": anomalies, "transitions": rep["transitions"]}


def render_transitions(rep: dict) -> str:
    lines = [f"池 {rep['pool_size']} 檔；有市場轉換 {rep['n_transitioned']} 檔；跨 twse/tpex {rep['n_cross_market']} 檔；"
             f"異常 {len(rep['anomalies'])} 檔"]
    lines.append("跨 twse/tpex 轉換日（生效日＝較舊列 date+1，誤差 1～2 日）：")
    for sid, seq in rep["cross_market"].items():
        lines.append(f"  {sid}: " + " → ".join(f"{t}@{eff or '起點'}" for eff, t in seq))
    if rep["anomalies"]:
        lines.append("異常（人工看）：")
        for sid, why in rep["anomalies"].items():
            lines.append(f"  {sid}: {why}；序列 " + " → ".join(f"{t}@{eff or '起點'}" for eff, t in rep["transitions"][sid]))
    return "\n".join(lines)


# 池成員集合會傳導到的輸出欄（`scores_io.flatten_row` 的欄名；程式碼路徑推導，2026-09-17，`docs/P3-PIT-POOL.md` §6.8）：
#   ① 大盤廣度母體 → 大盤方向分數 → 個股 `line_6` 族 A（`score/stock.py` `ind_market_direction`）——全池
#   ② 產業中位報酬／產業內站上 MA20 比（同市場×同產業桶，`scan.py` 的 `ind_rets`／`IndustryBreadth`）→ `line_3` 族 B
#      `ind_excess_vs_industry`、`line_6` 族 B——整桶；`industry_n < industry_min_sample` 時整族缺 → `line_3_*` 附欄／`coverage`
#   ③ 排名池（`cross.adv.eligible()`）→ `in_rank_pool`；`P_cs` 母體 → `line_3` 過熱上限（`overheat_cap`）——邊界檔
#   ④ 上述兩爻的衍生：`base_score`／`inner_trigram_score`／`outer_trigram_score`／`coverage`、卦位（provisional／formal／king_wen／
#      hexagram_name）、遲滯狀態 `line_states`／`streaks`（路徑相依，一旦某日不同就整條分岔）
# **不在集合內＝不得因池改變而不同**：`line_1`（族 C 產業母體是靜態 FundamentalsBridge，不隨 T）、`line_2`、`line_4`、`line_5` 及其附欄、
# `market`／`horizon`／`calibrated`／`flags`（個股列恆 None）。這四爻只吃該檔自己的資料，兩版對非轉市檔必須逐位相同——這才是 compare 真正守的不變式。
POOL_DEPENDENT_COLS = frozenset({
    "line_3", "line_3_unknown", "line_3_coverage_ratio", "line_3_reweighted",
    "line_6", "line_6_unknown", "line_6_coverage_ratio", "line_6_reweighted",
    "base_score", "inner_trigram_score", "outer_trigram_score", "coverage",
    "lines_provisional", "king_wen_provisional", "hexagram_name_provisional",
    "lines_formal", "king_wen", "hexagram_name", "line_states", "streaks", "in_rank_pool",
})


def _sid(k: tuple) -> str:
    return k[2]


def _diff_cols(x: dict, y: dict) -> set[str]:
    return {c for c in set(x) | set(y) if x.get(c) != y.get(c)}


def compare(old: ScoreStore, new: ScoreStore, dv: str, pool: PitPool, *, start: str | None, end: str | None) -> dict:
    """逐日：只在單側的列依 stock_id 歸 (a)/(c)/(b)/未解釋；兩側同鍵值不同的列在當日有 (a)/(c) 異動時歸連帶。"""
    transitioned = set(pool.report_transitions()["transitions"])
    dates = [d for d in new.dates(dv) if (not start or d >= start) and (not end or d <= end)]
    old_dates = set(old.dates(dv))
    out = {"data_version": dv, "days": len(dates), "days_identical": 0, "days_only_new": 0, "per_day": [],
           "unexplained_days": [], "class_totals": {"a": 0, "b": 0, "c": 0, "linked": 0, "unexplained": 0},
           "unexplained_col_hist": {}, "unexplained_onesided_total": {"old": 0, "new": 0}}
    for d in dates:
        if d not in old_dates:
            out["days_only_new"] += 1
            continue
        ra = {DS._key(r): r for r in old.rows_for_day(dv, d)}
        rb = {DS._key(r): r for r in new.rows_for_day(dv, d)}
        only_old, only_new = set(ra) - set(rb), set(rb) - set(ra)
        listed = pool.listed_ids(d)
        cls = {"a": set(), "b": set(), "c": set(), "unexplained": set()}
        col_hist: dict[str, int] = {}
        onesided = {"old": 0, "new": 0}

        def classify(sid: str, only_old_row: bool) -> str | None:
            if sid in transitioned and sid not in listed and only_old_row:
                return "c"                                            # 改前在池（靜態最新 type）、改後 T 日不在（興櫃期）
            if sid in transitioned:
                return "a"
            if sid not in pool:
                return "b"
            return None

        for k in only_old | only_new:
            sid = _sid(k)
            if sid == MARKET_STOCK_ID:
                continue                                              # 大盤列只在單側＝該市場整日缺，歸連帶（下面）
            c1 = classify(sid, k in only_old)
            cls[c1 or "unexplained"].add(sid)
            if c1 is None:
                onesided["old" if k in only_old else "new"] += 1
        changed = {k for k in set(ra) & set(rb) if ra[k] != rb[k]}
        linked = 0
        others: list[tuple] = []
        for k in changed:
            sid = _sid(k)
            if sid == MARKET_STOCK_ID:
                continue
            c = classify(sid, False)
            if c is not None:
                cls[c].add(sid)
            else:
                others.append(k)
        any_class = bool(cls["a"] or cls["b"] or cls["c"])
        market_rows = [k for k in changed | only_old | only_new if _sid(k) == MARKET_STOCK_ID]
        if any_class:
            linked += len(market_rows)
        else:
            cls["unexplained"].update(_sid(k) for k in market_rows)
        for k in others:
            if any_class and _diff_cols(ra[k], rb[k]) <= POOL_DEPENDENT_COLS:
                linked += 1                                           # 其他檔只動池依賴欄＝池變動的傳導
            else:
                cls["unexplained"].add(_sid(k))
                cs = "+".join(sorted(_diff_cols(ra[k], rb[k])))
                col_hist[cs] = col_hist.get(cs, 0) + 1
        n_diff = len(only_old) + len(only_new) + len(changed)
        if n_diff == 0:
            out["days_identical"] += 1
        row = {"date": d, "n_diff": n_diff, "a": sorted(cls["a"]), "b": sorted(cls["b"]), "c": sorted(cls["c"]),
               "linked_rows": linked, "unexplained": sorted(cls["unexplained"]),
               "unexplained_cols": dict(sorted(col_hist.items(), key=lambda kv: -kv[1])), "unexplained_onesided": onesided}
        out["per_day"].append(row)
        for cs, n in col_hist.items():
            out["unexplained_col_hist"][cs] = out["unexplained_col_hist"].get(cs, 0) + n
        for side in ("old", "new"):
            out["unexplained_onesided_total"][side] += onesided[side]
        for key in ("a", "b", "c", "unexplained"):
            out["class_totals"][key] += len(cls[key])
        out["class_totals"]["linked"] += linked
        if cls["unexplained"]:
            out["unexplained_days"].append(d)
    return out


def render_compare(rep: dict, show: int) -> str:
    t = rep["class_totals"]
    lines = [f"data_version={rep['data_version']} 比對 {rep['days']} 日：逐位相同 {rep['days_identical']} 日、只在新 DB {rep['days_only_new']} 日",
             f"檔級歸類合計（每日計一次）：(a)轉市檔 {t['a']}　(b)快照外下市股 {t['b']}　(c)興櫃期 {t['c']}　連帶列 {t['linked']}　"
             f"未解釋檔 {t['unexplained']}（{len(rep['unexplained_days'])} 日）"]
    hist = sorted(rep.get("unexplained_col_hist", {}).items(), key=lambda kv: -kv[1])
    if hist:
        lines.append("未解釋（兩側皆有、值不同）的差異欄組合 top 10（全期間列數）：")
        lines += [f"  {n:>8}  {cs}" for cs, n in hist[:10]]
    os_ = rep.get("unexplained_onesided_total", {})
    if os_.get("old") or os_.get("new"):
        lines.append(f"未解釋（只在單側）的列數：只在舊 DB {os_.get('old', 0)}　只在新 DB {os_.get('new', 0)}")
    shown = 0
    for r in rep["per_day"]:
        if r["n_diff"] == 0:
            continue
        lines.append(f"  {r['date']}: 差 {r['n_diff']} 列　a={r['a'][:6]} c={r['c'][:6]} b={r['b'][:6]} 連帶 {r['linked_rows']}"
                     + (f"　未解釋 {r['unexplained'][:6]}" if r["unexplained"] else ""))
        shown += 1
        if shown >= show:
            lines.append("  …（--show 加大可看更多）")
            break
    lines.append(f"結果：rc={1 if rep['unexplained_days'] else 0}")
    return "\n".join(lines)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="PIT 池切換報告（唯讀）")
    sub = ap.add_subparsers(dest="cmd", required=True)
    t = sub.add_parser("transitions")
    t.add_argument("--cache-dir", default=str(REPO / "cache"))
    t.add_argument("--out", default=None, help="JSON 輸出路徑（省略只印）")
    c = sub.add_parser("compare")
    c.add_argument("--cache-dir", default=str(REPO / "cache"))
    c.add_argument("--old", required=True)
    c.add_argument("--new", required=True)
    c.add_argument("--data-version", default=None)
    c.add_argument("--from", dest="start", default=None)
    c.add_argument("--to", dest="end", default=None)
    c.add_argument("--show", type=int, default=20)
    c.add_argument("--out", default=None, help="JSON 輸出路徑（省略只印）")
    args = ap.parse_args(argv)
    uni = F.open_ro(Path(args.cache_dir) / "universe.db")
    try:
        pool = F.load_pool(uni)
    finally:
        uni.close()
    if args.cmd == "transitions":
        rep = transitions_report(pool)
        print(render_transitions(rep))
        if args.out:
            Path(args.out).parent.mkdir(parents=True, exist_ok=True)
            Path(args.out).write_text(json.dumps(rep, ensure_ascii=False, indent=1), encoding="utf-8")
        return 0
    with ScoreStore(args.old, readonly=True) as old, ScoreStore(args.new, readonly=True) as new:
        dvs = [r[0] for r in new.conn.execute("SELECT DISTINCT data_version FROM replay_day ORDER BY 1")]
        dv = args.data_version or (dvs[0] if len(dvs) == 1 else None)
        if dv is None:
            print(f"[compare 中止] 新 DB 有多個 data_version {dvs}，請 --data-version", file=sys.stderr)
            return 2
        rep = compare(old, new, dv, pool, start=args.start, end=args.end)
    print(render_compare(rep, args.show))
    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(json.dumps(rep, ensure_ascii=False, indent=1), encoding="utf-8")
    return 1 if rep["unexplained_days"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
