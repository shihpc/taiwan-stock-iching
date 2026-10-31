#!/usr/bin/env python3
"""裁定 #77 T2：晨間／休市日試算卦（展示用、不改正式卦）→ `data/web/trial.json`＋快照 `runs/trial/<T'>-<HHMM>.json.gz`。

    python3 scripts/build_trial.py --root . [--force]

依據：docs/P4-PREVIEW.md §16（裁定 #77）、§16.8（使用者 10-30 取數規則、10-31 班次 08:15／08:45）、§18（夜盤模組）。
純函式在 `src/iching/trial.py`；重建與二爻歷史拼回**重用** `scripts/build_indicators.py`（import、不改它）。

## 流程

1. 時窗：台北週二～六 08:00–09:59（`trial.in_window`）；窗外直接結束（rc 0、不寫檔）。`--force` 略過時窗（手動測試用）。
2. 基底＝最新正式卦 T（`data/scores/` 最新檔，須與 `cross.json` last_date、params_sha 一致）；T'＝`night_query_date(T)`。
   T' < 台北今日（正式卦落後，例前一晚 waiting）→ 結束（status formal_stale，不寫檔）。
3. 夜盤：抓 T' 那頁（期交所），ok → 推算值＝T 官方外資淨未平倉＋夜盤外資淨額；否則記狀態、五爻沿用正式值。
   上一版 trial.json 的 T／T'／params_sha 相同、generated_at 為台北今日、且夜盤 ok → 沿用該值（本班重抓只作對照，記入快照）。
4. 美股：FinMind `USStockPrice`（^GSPC／^SOX），取日期 > 正式視窗最後美股日、且 < 台北今日的列接上；
   上爻換不換看 us_asof（`trial.us_shift`）：us_asof(T')（含新列）晚於正式 us_asof(T) 才換——視窗本來就含美國 T 日那列時，不抓到新列也換。
5. 兩樣都沒換到 → 不產新試算（保留舊 trial.json，status no_new_inputs）。
6. 重建 T 日視窗（同 build_indicators），大盤：正式輸入算一次（須與 data/scores/<T>.json 逐位相同）→ 換輸入再算；
   試算的大盤 1–4 爻須與正式相同、未換的 5／6 爻亦同。個股：以試算大盤方向重算，1–5 爻須與正式相同（不同列 > 1% → 中止）。
7. 遲滯：只對被換的爻（大盤 5／6、個股 6）自 cross.json 走一步，不寫回；試算分數與正式逐位相同的爻不走。
8. 寫 `data/web/trial.json`（覆寫）與 `runs/trial/<T'>-<HHMM>.json.gz`（已存在則不覆寫）；兩檔先寫 .tmp，全成功才 replace。

**不寫** `data/scores/`、`data/state/cross.json`、`data/web/latest.json`；不改計分引擎、參數、指紋。

## 回傳碼

0 成功或依規則略過（窗外／formal_stale／no_new_inputs）；2 中止（重建或比對失敗、讀檔失敗、寫檔失敗、重建層的任何例外）——中止不寫任何檔
（唯一例外：兩檔 replace 之間失敗＝快照已落地、web 未換）。
"""
from __future__ import annotations

import argparse
import datetime as dt
import gzip
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Callable, Mapping

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from iching import daily_core as DC  # noqa: E402
from iching import taifex_night as TN  # noqa: E402
from iching import trial as TR  # noqa: E402
from iching.replay_step import _score_or_none  # noqa: E402
from iching.score import score_market, score_stock  # noqa: E402
from iching.score.assemble import MARKET_STOCK_ID, assemble_row  # noqa: E402
from iching.score.hexagram import king_wen_from_lines, name_of  # noqa: E402
from iching.universe import is_financial  # noqa: E402

_spec = importlib.util.spec_from_file_location("build_indicators", REPO / "scripts" / "build_indicators.py")
BI = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(BI)

TPE = dt.timezone(dt.timedelta(hours=8))
SCHEMA = 1
WEB_FILE = Path("data") / "web" / "trial.json"
SNAP_DIR = Path("runs") / "trial"
MAX_STOCK_MISMATCH = 0.01


class BuildTrialError(Exception):
    pass


# ---------------------------------------------------------------- 外部資料（可注入）

def fetch_night_parsed(query_date: str) -> dict[str, Any]:
    """回 {status, query_date, net?, long?, short?, raw_sha256?, kind?, error?}；不 raise。"""
    try:
        html = TN.fetch_night(query_date)
    except TN.NightNetworkError as e:
        return {"status": "network_error", "query_date": query_date, "error": str(e)[:200]}
    try:
        p = TN.parse_night(html, query_date)
    except TN.NightFormatError as e:
        return {"status": "format_error", "query_date": query_date, "kind": getattr(e, "kind", None) or str(e.args[0] if e.args else ""),
                "error": str(e)[:200]}
    if p is None:
        return {"status": "no_data", "query_date": query_date}
    return {"status": "ok", "query_date": query_date, "net": p["net"], "long": p["long"], "short": p["short"],
            "raw_sha256": p["raw_sha256"][:12]}


def fetch_us_rows(start: str, end: str) -> list[tuple]:
    """FinMind USStockPrice（^GSPC／^SOX）→ `[(date, spx_c, spx_h, spx_l, sox_c)]`；失敗 raise（呼叫端轉成狀態）。"""
    from iching import collect as C
    from iching.fm import FinMind
    fm = FinMind()
    rows: list[dict] = []
    for sid in (C.US_SPX, C.US_SOX):
        rows += fm.get("USStockPrice", data_id=sid, start_date=start, end_date=end)
    return C.us_from_rows(rows)


def scores_ref(root: Path, T: str) -> str:
    """計分 T 當下的原料包集合要從哪個 git ref 補回被修剪的包：最後一次寫 `data/scores/<T>.json` 的 commit 之父；
    該檔尚未 commit（本班剛產）→ HEAD。"""
    try:
        sha = subprocess.run(["git", "-C", str(root), "log", "-1", "--format=%H", "--", f"data/scores/{T}.json"],
                             capture_output=True, text=True, check=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return "HEAD"
    return f"{sha}^" if sha else "HEAD"


# ---------------------------------------------------------------- 主計算

def _row_entry(row: Mapping[str, Any], hy: Mapping[str, Any]) -> dict[str, Any]:
    lf = hy["lf"]
    kw = king_wen_from_lines(lf) if lf else None
    return {"kw": kw, "name": name_of(kw) if kw else None, "kwp": row.get("king_wen_provisional"),
            "namep": row.get("hexagram_name_provisional"), "lf": "".join(map(str, lf)) if lf else None,
            "lp": row.get("lines_provisional"), "l": [BI.rnd(row.get(f"line_{k}"), 1) for k in range(1, 7)],
            "bs": BI.rnd(row.get("base_score"), 2), "flip": hy["flip"]}


def prepare(root: Path, *, now: dt.datetime, holidays: Any = "load", git_ref: str | None = None) -> dict[str, Any]:
    """讀正式卦 T、驗一致性、重建 T 視窗（慢：約 1 分鐘）。回 ctx；正式卦落後時 ctx["early"]＝formal_stale 結果。"""
    today = now.date().isoformat()
    files = BI.list_score_files(root / DC.SCORES_DIR)
    if not files:
        raise BuildTrialError("沒有正式分數檔")
    T = files[-1].name[:-5]
    hol = TN.load_holidays() if holidays == "load" else holidays
    T2 = TN.night_query_date(T, hol)
    base: dict[str, Any] = {"schema": SCHEMA, "base_date": T, "target_date": T2,
                            "generated_at": now.isoformat(timespec="seconds"), "band": now.strftime("%H%M"),
                            "holidays_loaded": hol is not None}
    if T2 < today:
        return {"early": {**base, "status": "formal_stale"}}

    ref = BI.load_scores(files[-1])
    cross_post = BI.load_state(root / DC.STATE_FILE)
    if cross_post.last_date != T or ref.get("tpe_date") != T:
        raise BuildTrialError(f"cross.json last_date={cross_post.last_date}／分數檔 tpe_date={ref.get('tpe_date')} ≠ {T}")
    window = int(cross_post.meta.get("window") or 0)
    dv = str(cross_post.meta.get("data_version") or "")
    ps = {m: BI.build_params(m) for m in BI.MARKETS}
    mv = {m: ps[m].model_version() for m in BI.MARKETS}
    sha = BI.params_fingerprint(BI.build_params_payload(mv, window, cross_post.adv, fundamentals=True))
    if sha != ref.get("params_sha") or cross_post.meta.get("params_sha") != sha:
        raise BuildTrialError(f"params_sha 不符：重算 {sha}／快照 {cross_post.meta.get('params_sha')}／分數檔 {ref.get('params_sha')}")
    base.update(params_sha=sha, model_version=mv)

    # -- 重建 T 視窗（同 build_indicators.compute）--
    ref_rows = [r for r in ref["rows"] if isinstance(r, dict)]
    ref_by = {(r.get("market"), r.get("stock_id"), r.get("horizon")): r for r in ref_rows}
    cross, _sdiag = BI.pre_state(cross_post, ref_rows, list(reversed(files[:-1])))
    _, pool = DC.load_pool_file(root / DC.POOL_FILE)
    _, factors, _ = DC.load_factors_file(root / DC.FACTORS_FILE)
    gref = git_ref or scores_ref(root, T)
    held, ents_restored, _bdiag = BI.scoring_bundles(root, T, git_ref=gref)
    if not held or held[-1][0] != T:
        raise BuildTrialError(f"缺 {T} 的原料包")
    good, _bad = DC.read_entrants(root)
    good = {**ents_restored, **good}
    entrants = {sid: e for sid, e in good.items() if sid in pool}
    wc, _adv, _rdiag = DC.rebuild_from_bundles(held, pool, factors, data_version=dv, window=window, entrants=entrants)
    cal = DC.load_calendar_dates(root / DC.CALENDAR_TPE_FILE)
    _, bridge = DC.load_fundamentals_file(root / DC.FUND_FILE, pool, cal)
    provider = bridge.provider()

    active = [m for m in BI.MARKETS if wc.has_market(m) and wc.market_dates(m) and wc.market_dates(m)[-1] == T]
    oi_T = None
    if active:
        oi_arr = wc.market_inputs(active[0], T, cross).foreign_net_oi
        oi_T = float(oi_arr[-1]) if oi_arr is not None and len(oi_arr) else None
    return {"early": None, "root": root, "today": today, "T": T, "T2": T2, "base": base, "ref_by": ref_by,
            "cross_post": cross_post, "cross": cross, "wc": wc, "provider": provider, "ps": ps, "mv": mv, "dv": dv,
            "active": active, "oi_T": oi_T, "gref": gref}


def run(ctx: Mapping[str, Any], *, night_fetch: Callable[[str], dict] = fetch_night_parsed,
        us_fetch: Callable[[str, str], list] = fetch_us_rows,
        log: Callable[[str], None] = lambda s: print(s, file=sys.stderr)) -> dict[str, Any]:
    """取夜盤／美股、換輸入、計分、守門（不寫檔）。ctx 不被修改，可重複呼叫。"""
    root, today, T, T2 = ctx["root"], ctx["today"], ctx["T"], ctx["T2"]
    ref_by, cross_post, cross, wc, provider = ctx["ref_by"], ctx["cross_post"], ctx["cross"], ctx["wc"], ctx["provider"]
    ps, mv, dv, active, oi_T, gref = ctx["ps"], ctx["mv"], ctx["dv"], ctx["active"], ctx["oi_T"], ctx["gref"]
    base = dict(ctx["base"])

    # -- 夜盤（同 T／T' 的上一版 ok 值沿用，本班重抓只作對照）--
    prev = None
    try:
        prev = json.loads((root / WEB_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        prev = None
    night_now = night_fetch(T2)
    night = night_now
    night_cmp = None
    if (isinstance(prev, dict) and prev.get("base_date") == T and prev.get("target_date") == T2
            and prev.get("params_sha") == base.get("params_sha")
            and str(prev.get("generated_at") or "")[:10] == today
            and (prev.get("night") or {}).get("status") == "ok"):
        night = {k: v for k, v in prev["night"].items() if k not in ("oi_official_T", "oi_est")}
        night["kept_from"] = prev["night"].get("kept_from") or prev.get("band")   # 記最初取得夜盤值的那一班
        night_cmp = night_now
    # -- 美股新列 --
    last_us = wc.us_dates[-1] if wc.us_dates else None
    us_state: dict[str, Any] = {"window_last": last_us}
    try:
        start = (dt.date.fromisoformat(last_us) + dt.timedelta(days=1)).isoformat() if last_us else T
        us_new = TR.new_us_rows(us_fetch(start, today), last_us, today) if (last_us or "") < today else []
        us_state["status"] = "ok" if us_new else "no_new"
    except Exception as e:  # noqa: BLE001
        us_new = []
        us_state.update(status="error", error=f"{type(e).__name__}: {str(e)[:160]}")
    us_state["rows_added"] = [r[0] for r in us_new]
    asof_formal, asof_trial, us_shift = TR.us_shift(T, T2, list(wc.us_dates or []), [r[0] for r in us_new])
    us_state.update(asof_formal=asof_formal, asof_trial=asof_trial)

    oi_est = TR.oi_estimate(oi_T, night.get("net") if night.get("status") == "ok" else None)
    night = {**night, "oi_official_T": oi_T, "oi_est": oi_est}
    changed_m = ([5] if oi_est is not None else []) + ([6] if us_shift else [])
    base.update(night=night, us=us_state, changed_market_lines=changed_m)
    if not changed_m:
        return {**base, "status": "no_new_inputs", "night_compare": night_cmp}

    # -- 大盤 --
    market: dict[str, Any] = {}
    direction: dict[str, dict[str, float | None]] = {}
    for mk in active:
        mi = wc.market_inputs(mk, T, cross)
        mi2 = TR.trial_market_inputs(mi, T2, us_new, oi_est, shift_date=us_shift)
        direction[mk] = {}
        for h in BI.HORIZONS:
            ms0 = score_market(mi, ps[mk], h)
            row0 = assemble_row(ms0, ps[mk], dv, BI.TEXT_VERSION, model_version=mv[mk])
            if not BI.same_lines(row0, ref_by.get((mk, MARKET_STOCK_ID, h))):
                raise BuildTrialError(f"大盤 {mk}|{h} 正式重算與分數檔不同（重建不可信）")
            ms = score_market(mi2, ps[mk], h)
            s0, s1 = ms0.line_scores(), ms.line_scores()
            same = [k for k in range(1, 7) if k not in changed_m]
            if any(s0[k - 1] != s1[k - 1] for k in same):
                raise BuildTrialError(f"大盤 {mk}|{h} 未換的爻 {same} 與正式不同（換輸入影響到其他爻）")
            hy = TR.step_changed(cross_post.market_lines.get(f"{mk}|{h}"), s1, changed_m, ps[mk].rules, formal=s0)
            row = assemble_row(ms, ps[mk], dv, BI.TEXT_VERSION, model_version=mv[mk], formal_lines=hy["lf"], detail=True)
            ent = _row_entry(row, hy)
            l6 = ms.lines["6"]
            ent["us_asof"] = (l6.meta or {}).get("us_asof") if hasattr(l6, "meta") else None
            market[f"{mk}|{h}"] = ent
            direction[mk][h] = _score_or_none(ms.direction_score)

    # -- 個股（只換大盤方向）--
    stocks: dict[str, Any] = {}
    n_ref = n_mis = 0
    mis: list[str] = []
    for sid in wc.stock_ids_today():
        mk = wc.pool.listed(sid, T)
        if mk is None or mk not in active:
            continue
        fin = is_financial(wc.pool.industry_of(sid))
        extra = provider(sid, T) or {}
        ent: dict[str, Any] = {}
        for h in BI.HORIZONS:
            rr = ref_by.get((mk, sid, h))
            if rr is None:
                continue
            n_ref += 1
            si = wc.stock_inputs(sid, h, T, cross, direction[mk], is_financial=fin,
                                 monthly_revenue=extra.get("monthly_revenue"),
                                 industry_median_3m_yoy=extra.get("industry_median_3m_yoy"),
                                 industry_revenue_n=extra.get("industry_revenue_n"),
                                 fundamentals=extra.get("fundamentals"))
            ss = score_stock(si, ps[mk], h)
            sc = ss.line_scores()
            if BI.lines_of(rr)[:5] != [BI.fnum(x) for x in sc[:5]]:
                n_mis += 1
                mis.append(f"{sid}|{h}")
                continue
            hy = TR.step_changed(cross_post.stock_lines.get(f"{sid}|{h}"), sc, [6], ps[mk].rules, formal=BI.lines_of(rr))
            row = assemble_row(ss, ps[mk], dv, BI.TEXT_VERSION, model_version=mv[mk], formal_lines=hy["lf"])
            e = _row_entry(row, hy)
            ent[h] = [e["l"][5], e["bs"], e["kw"], e["kwp"], e["lf"], e["lp"]]
        if ent:
            stocks[sid] = ent
    if n_ref == 0 or n_mis / n_ref > MAX_STOCK_MISMATCH:
        raise BuildTrialError(f"個股 1–5 爻與正式不同 {n_mis}/{n_ref}（> {MAX_STOCK_MISMATCH:.0%}，例 {mis[:5]}）")
    log(f"[build_trial] T={T} T'={T2} 換爻 {changed_m}；個股 {len(stocks)} 檔、不同列 {n_mis}/{n_ref}")
    return {**base, "status": "ok", "market": market, "stock_cols": ["l6", "bs", "kw", "kwp", "lf", "lp"],
            "stocks": stocks, "night_compare": night_cmp, "stock_mismatch": mis[:50], "git_ref": gref}


def compute(root: Path, *, now: dt.datetime, night_fetch: Callable[[str], dict] = fetch_night_parsed,
            us_fetch: Callable[[str, str], list] = fetch_us_rows, holidays: Any = "load", git_ref: str | None = None,
            log: Callable[[str], None] = lambda s: print(s, file=sys.stderr)) -> dict[str, Any]:
    ctx = prepare(root, now=now, holidays=holidays, git_ref=git_ref)
    return ctx["early"] if ctx["early"] else run(ctx, night_fetch=night_fetch, us_fetch=us_fetch, log=log)


def dumps(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n"


def write_outputs(root: Path, res: Mapping[str, Any]) -> list[Path]:
    """兩個檔都先寫成 .tmp，全部成功才 `os.replace`（快照先、web 後）；任何一步失敗清掉 .tmp、拋 BuildTrialError。
    快照已存在則不重寫（寫入後不覆寫）。"""
    web = {k: v for k, v in res.items() if k not in ("night_compare", "stock_mismatch", "git_ref")}
    p = root / WEB_FILE
    snap = root / SNAP_DIR / f"{res['target_date']}-{res['band']}.json.gz"
    plan: list[tuple[Path, Path]] = []
    try:
        if not snap.exists():
            snap.parent.mkdir(parents=True, exist_ok=True)
            st = snap.with_name(snap.name + ".tmp")
            plan.append((st, snap))                                   # 先登記，寫到一半失敗也清得到
            with gzip.open(st, "wt", encoding="utf-8") as f:
                f.write(dumps({**res, "github_sha": os.environ.get("GITHUB_SHA")}))
        p.parent.mkdir(parents=True, exist_ok=True)
        pt = p.with_name(p.name + ".tmp")
        plan.append((pt, p))
        pt.write_text(dumps(web), encoding="utf-8")
        for t, dst in plan:
            os.replace(t, dst)
    except (OSError, ValueError) as e:
        for t, _ in plan:
            t.unlink(missing_ok=True)
        raise BuildTrialError(f"寫檔失敗：{e}") from e
    return [dst for _, dst in plan][::-1]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="裁定 #77 T2：試算卦 data/web/trial.json（docs/P4-PREVIEW.md §16／§21）")
    ap.add_argument("--root", default=".")
    ap.add_argument("--force", action="store_true", help="略過時窗守門（手動測試用）")
    a = ap.parse_args(argv)
    root = Path(a.root)
    now = dt.datetime.now(TPE)
    if not a.force and not TR.in_window(now):
        print(f"[build_trial] 台北 {now:%a %H:%M} 不在試算時窗（週二～六 08:00–09:59）：略過")
        return 0
    try:
        res = compute(root, now=now)
        if res["status"] == "ok":
            paths = write_outputs(root, res)
            print(f"[build_trial] 已寫 {[str(p.relative_to(root)) for p in paths]}")
        else:
            print(f"[build_trial] status={res['status']}（不寫檔）：T={res.get('base_date')} T'={res.get('target_date')} "
                  f"夜盤={(res.get('night') or {}).get('status')} 美股={(res.get('us') or {}).get('status')}")
    except BuildTrialError as e:
        print(f"[build_trial] 中止（未寫任何檔）：{e}", file=sys.stderr)
        return 2
    except Exception as e:  # noqa: BLE001 — 讀檔／重建層的例外（BuildIndicatorsError、ReplayDriverError…）一律視為中止
        print(f"[build_trial] 中止（未寫任何檔）：{type(e).__name__}: {e}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
