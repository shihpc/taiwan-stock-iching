#!/usr/bin/env python3
"""PIT 池切換報告（P3 第 1 項，`docs/P3-PIT-POOL.md` §2 #1／#9）——**唯讀**，供 `scripts/hetzner_pit.sh` 與人工看異常。

    python3 scripts/pit_report.py transitions --cache-dir cache [--out runs/pit/transitions.json]
    python3 scripts/pit_report.py compare --old cache/scores_prepit.db --new cache/scores.db [--from D1 --to D2] [--show 20]

- `transitions`：`universe.PitPool.report_transitions()`——有市場轉換的代號與序列、異常（>2 次／來回／上市→上櫃逆向），
  另列 11 檔式「跨 twse/tpex」的轉換日（人工對官方掛牌日抽查用）。
- `compare`：舊（靜態快照池）vs 新（PIT 池）`scores.db` 逐日比對摘要。**預期差異只落在三類**（§2 #9）：
  (a) 轉換過市場的檔與其影響的市場列（含同市場其他個股列——廣度母體變了，方向分數會連帶）
  (b) 快照外的下市股（**本版 PIT 池不含快照外代號**，此類應為 0，見 §6 未做項）
  (c) 興櫃轉上櫃前的日子（該檔改前在池、改後不在）。
  分不出的差異列進 `unexplained`，rc 1；兩個 DB 的 `params_sha` 本來就不同（`pool_semantics` 進指紋），**不當錯**。
  「只在單側的列」以 `stock_id` 歸類；兩側都有但值不同的列若當日有任何 (a)/(c) 檔異動即歸「連帶」（母體變了整日都會動），
  否則 `unexplained`。這與 `parity_check` 的連帶邏輯同型、同樣會遮掉同日的真差異——報告有印當日連帶檔數，讀的人要知道。
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


def _sid(k: tuple) -> str:
    return k[2]


def compare(old: ScoreStore, new: ScoreStore, dv: str, pool: PitPool, *, start: str | None, end: str | None) -> dict:
    """逐日：只在單側的列依 stock_id 歸 (a)/(c)/(b)/未解釋；兩側同鍵值不同的列在當日有 (a)/(c) 異動時歸連帶。"""
    transitioned = set(pool.report_transitions()["transitions"])
    dates = [d for d in new.dates(dv) if (not start or d >= start) and (not end or d <= end)]
    old_dates = set(old.dates(dv))
    out = {"data_version": dv, "days": len(dates), "days_identical": 0, "days_only_new": 0, "per_day": [],
           "unexplained_days": [], "class_totals": {"a": 0, "b": 0, "c": 0, "linked": 0, "unexplained": 0}}
    for d in dates:
        if d not in old_dates:
            out["days_only_new"] += 1
            continue
        ra = {DS._key(r): r for r in old.rows_for_day(dv, d)}
        rb = {DS._key(r): r for r in new.rows_for_day(dv, d)}
        only_old, only_new = set(ra) - set(rb), set(rb) - set(ra)
        listed = pool.listed_ids(d)
        cls = {"a": set(), "b": set(), "c": set(), "unexplained": set()}
        for k in only_old | only_new:
            sid = _sid(k)
            if sid == MARKET_STOCK_ID:
                continue                                              # 大盤列只在單側＝該市場整日缺，歸連帶（下面）
            if sid in transitioned and sid not in listed and k in only_old:
                cls["c"].add(sid)                                     # 改前在池（靜態最新 type）、改後 T 日不在（興櫃期）
            elif sid in transitioned:
                cls["a"].add(sid)
            elif sid not in pool:
                cls["b"].add(sid)
            else:
                cls["unexplained"].add(sid)
        changed = {k for k in set(ra) & set(rb) if ra[k] != rb[k]}
        linked = len(changed) + sum(1 for k in only_old | only_new if _sid(k) == MARKET_STOCK_ID)
        if changed and not (cls["a"] or cls["c"] or cls["b"]):
            cls["unexplained"].update(_sid(k) for k in changed)
            linked = 0
        n_diff = len(only_old) + len(only_new) + len(changed)
        if n_diff == 0:
            out["days_identical"] += 1
        row = {"date": d, "n_diff": n_diff, "a": sorted(cls["a"]), "b": sorted(cls["b"]), "c": sorted(cls["c"]),
               "linked_rows": linked, "unexplained": sorted(cls["unexplained"])}
        out["per_day"].append(row)
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
