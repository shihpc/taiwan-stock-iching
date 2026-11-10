#!/usr/bin/env python3
"""P4-L2 選多空（排名版）：`data/scores/<T>.json`＋原料包＋交易限制 → 每日快照 `data/pick/<T>.json`＋網頁 `data/web/pick.json`。

    python3 scripts/build_pick.py --root . [--date YYYY-MM-DD] [--no-fetch]

依據：裁定 #76（docs/P4-PREVIEW.md §14）；設計與使用者決定 §24（P4-L1）。純函式在 `src/iching/pick.py`。

## 流程

1. T＝`data/web/latest.json` 的 `date`（`--date` 可覆寫，供本機量測）；讀 `data/scores/<T>.json`（大盤列 `flags` 與個股列）。
2. 母體＝個股列 `in_rank_pool==1`。做多方向分數＝`base_score`；做空 `short_raw`＝二～五爻反向加權（§24.3）。
3. 讀 `runs/collect/` ≤T 最近 250 個交易日（`data/calendar_tpe.json`）的原料包，以 `data/factors.json` 後復權 → 60 日報酬、MA20、
   ATR14、風險量 v／e／g／m、近 20 日成交金額中位數；融資餘額 250 日 P_hist。
4. 融券餘額：FinMind `TaiwanStockMarginPurchaseShortSale` 全市場 T 日切片，券資比＝`ShortSaleTodayBalance ÷ MarginPurchaseTodayBalance`
   ×100（欄位名依 FinMind 文件、**待 runner 實測**）。抓不到／無 token／欄位不符 → D2「未檢查」、不扣；同一 T 重跑時本次失敗、
   前次成功則沿用前次（同 build_limits）。**不寫進原料包**（§24.8）。
5. 交易限制：`data/web/lim.json`（P4-E2）；`date≠T` → 全部「交易限制未檢查」、旗標不套用。
6. T0：`data/pick/t0.json` 存在且 `params_sha` 與分數檔相同才啟用候選分級；否則 `mode="rank"` 只出方向排名（使用者決定）。
7. 寫檔：內容與既有檔相同就不重寫。輸出決定性（`sort_keys`、無時戳欄）。

## 回傳碼

0 成功（含 D2 未檢查、限制未檢查）；2 中止（latest／分數檔／T 日原料包讀不到、形狀不對、寫檔失敗）。計算階段中止時不寫任何檔；
寫檔階段（先快照、後網頁）第二檔失敗時第一檔可能已寫入，由 daily.yml 失敗分支還原 `data/pick/` 與 `data/web/pick.json`。
"""
from __future__ import annotations

import argparse
import datetime as dt
import gzip
import json
import math
import os
import sys
from pathlib import Path
from typing import Any, Callable, Mapping

import numpy as np

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from iching import pick as P  # noqa: E402
from iching.adjust import factor_at  # noqa: E402
from iching.daily_core import load_factors_file  # noqa: E402
from iching.score.params import Rules  # noqa: E402

SCHEMA = 1
HIST_N = 250            # 融資 P_hist 視窗；也涵蓋 60 日報酬與風險量所需
WEB_TOP = 30
MARGIN_DATASET = "TaiwanStockMarginPurchaseShortSale"
F_SHORT = "ShortSaleTodayBalance"
F_MARGIN = "MarginPurchaseTodayBalance"
DATE_LEN = 10


class BuildPickError(RuntimeError):
    pass


def _read_json(p: Path, what: str) -> Any:
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise BuildPickError(f"讀不到 {what} {p}: {e}") from e


def _num(x: Any) -> float | None:
    if x is None or isinstance(x, bool):
        return None
    try:
        f = float(x)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def r4(x: float | None) -> float | None:
    return None if x is None else round(float(x), 4)


def dumps(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"


# ---------------------------------------------------------------------------
# 讀入
def load_scores(root: Path, T: str) -> dict[str, Any]:
    j = _read_json(root / "data" / "scores" / f"{T}.json", "分數檔")
    rows = j.get("rows") if isinstance(j, dict) else None
    if not isinstance(rows, list):
        raise BuildPickError("分數檔形狀不對（rows）")
    mkt: dict[tuple[str, str], dict] = {}
    stocks: dict[str, dict[str, Any]] = {}
    mv: dict[str, set[str]] = {}
    for r in rows:
        if not isinstance(r, dict):
            raise BuildPickError("分數檔列不是 dict")
        m, h = r.get("market"), r.get("horizon")
        if m not in P.MARKETS or h not in P.HORIZONS:
            raise BuildPickError(f"分數檔列 market／horizon 不對：{m!r}／{h!r}")
        if r.get("scope") == "market_index":
            fl = r.get("flags")
            try:
                fl = json.loads(fl) if isinstance(fl, str) else fl
            except ValueError as e:
                raise BuildPickError(f"大盤列 flags 解析失敗 {m}|{h}: {e}") from e
            if not isinstance(fl, dict):
                raise BuildPickError(f"大盤列 flags 形狀不對 {m}|{h}")
            mkt[(m, h)] = fl
        elif r.get("scope") == "stock":
            sid = str(r.get("stock_id"))
            s = stocks.setdefault(sid, {"mk": m, "pool": False, "h": {}})
            if s["mk"] != m:
                raise BuildPickError(f"{sid} 市場不一致")
            if r.get("in_rank_pool") == 1:
                s["pool"] = True
            lines = {k: (None if r.get(f"line_{k}_unknown") == 1 else r.get(f"line_{k}")) for k in (2, 3, 4, 5)}
            s["h"][h] = {"bs": _num(r.get("base_score")), "lines": lines, "pool": r.get("in_rank_pool") == 1}
            if r.get("model_version"):
                mv.setdefault(m, set()).add(str(r["model_version"]))
    missing = [f"{m}|{h}" for m in P.MARKETS for h in P.HORIZONS if (m, h) not in mkt]
    if missing:
        raise BuildPickError(f"分數檔缺大盤列：{missing}")
    if any(len(v) > 1 for v in mv.values()):
        raise BuildPickError(f"同市場 model_version 不唯一：{ {k: sorted(v) for k, v in mv.items()} }")
    return {"params_sha": j.get("params_sha"), "market": mkt, "stocks": stocks,
            "model_version": {k: next(iter(v)) for k, v in sorted(mv.items())}}


def load_calendar(root: Path, T: str) -> list[str]:
    j = _read_json(root / "data" / "calendar_tpe.json", "交易日曆")
    dates = j.get("dates") if isinstance(j, dict) else None
    if not isinstance(dates, list) or T not in dates:
        raise BuildPickError(f"T={T} 不在 data/calendar_tpe.json")
    return [d for d in sorted(set(dates)) if d <= T][-HIST_N:]


def read_bundle(root: Path, d: str) -> dict[str, Any] | None:
    p = root / "runs" / "collect" / f"{d}-daily.json.gz"
    if not p.exists():
        return None
    try:
        with gzip.open(p, "rt", encoding="utf-8") as f:
            b = json.load(f)
    except (OSError, ValueError) as e:
        raise BuildPickError(f"讀不到原料包 {p}: {e}") from e
    if not isinstance(b, dict) or b.get("tpe_date") != d or not isinstance(b.get("stocks"), dict):
        raise BuildPickError(f"原料包形狀不對或日期不符：{p}")
    return b


FIELDS = ("open", "high", "low", "close", "amount", "volume", "margin_balance")


def load_series(root: Path, dates: list[str], ids: set[str]) -> tuple[dict[str, dict[str, np.ndarray]], list[str]]:
    """{代號: {欄位: 原始值陣列}}（缺日／缺欄為 NaN）；回傳缺原料包的日期。T 日缺包 → 中止。"""
    n = len(dates)
    out = {sid: {f: np.full(n, np.nan) for f in FIELDS} for sid in ids}
    miss: list[str] = []
    for i, d in enumerate(dates):
        b = read_bundle(root, d)
        if b is None:
            if i == n - 1:
                raise BuildPickError(f"T 日原料包不存在：runs/collect/{d}-daily.json.gz")
            miss.append(d)
            continue
        st = b["stocks"]
        for sid in ids:
            x = st.get(sid)
            if not isinstance(x, dict):
                continue
            for f in FIELDS:
                v = _num(x.get(f))
                if v is not None and (f in ("amount", "volume", "margin_balance") or v > 0):
                    out[sid][f][i] = v
    return out, miss


def adjusted(ser: Mapping[str, np.ndarray], dates: list[str], fac: tuple[list[str], list[float]] | None) -> dict[str, np.ndarray]:
    if fac is None:
        k = np.ones(len(dates))
    else:
        k = np.asarray([factor_at(d, fac[0], fac[1]) for d in dates], dtype=float)
    return {f: ser[f] * k for f in ("open", "high", "low", "close")}


# ---------------------------------------------------------------------------
# 融券餘額（D2）
def fetch_short_ratio(T: str, ids: set[str], getter: Callable[..., list[dict]] | None) -> dict[str, Any]:
    """回 {"status": str, "ratio": {sid: float|None}}；status=="ok" 時 ratio 內的代號才算「已查核」。"""
    if getter is None:
        return {"status": "no_fetch", "ratio": {}}
    try:
        rows = getter(MARGIN_DATASET, start_date=T, end_date=T)
    except Exception as e:  # noqa: BLE001 — 抓不到只讓 D2 未檢查，不擋整步
        return {"status": f"error:{type(e).__name__}", "ratio": {}}
    if not rows:
        return {"status": "empty", "ratio": {}}
    if not any(F_SHORT in r and F_MARGIN in r for r in rows if isinstance(r, dict)):
        keys = sorted({k for r in rows[:5] if isinstance(r, dict) for k in r})
        return {"status": "fields_missing:" + ",".join(keys)[:300], "ratio": {}}
    ratio: dict[str, float | None] = {}
    for r in rows:
        if not isinstance(r, dict) or str(r.get("date", ""))[:DATE_LEN] != T:
            continue
        sid = str(r.get("stock_id"))
        if sid not in ids:
            continue
        s, m = _num(r.get(F_SHORT)), _num(r.get(F_MARGIN))
        ratio[sid] = (100.0 * s / m) if (s is not None and m is not None and m > 0) else None
    return {"status": "ok", "ratio": ratio}


def finmind_getter() -> Callable[..., list[dict]] | None:
    from iching.fm import FinMind, load_token
    tok = load_token(required=False)
    if not tok:
        return None
    return FinMind(tok).get


def previous_short(root: Path, T: str) -> dict[str, Any] | None:
    p = root / "data" / "pick" / f"{T}.json"
    try:
        j = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if j.get("date") != T or j.get("d2", {}).get("status") != "ok":
        return None
    return {"status": "ok", "kept": True,
            "ratio": {sid: v["sr"][0] for sid, v in j.get("stocks", {}).items() if v.get("sr") and v["sr"][1] == 1}}


# ---------------------------------------------------------------------------
def load_events(root: Path, T: str, ids: set[str]) -> tuple[bool, dict[str, list[tuple[str, str]]]]:
    out: dict[str, list[tuple[str, str]]] = {}
    ok = True
    for pre in sorted({s[:2] for s in ids}):
        p = root / "data" / "web" / "ev" / f"{pre}.json"
        try:
            j = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            ok = False
            continue
        if j.get("date") != T:
            ok = False
            continue
        cols = j.get("cols") or []
        if "d" not in cols or "s" not in cols:
            ok = False
            continue
        di, si = cols.index("d"), cols.index("s")
        for sid, rows in (j.get("stocks") or {}).items():
            if sid in ids:
                out[sid] = [(str(r[di]), str(r[si])) for r in rows if isinstance(r, list) and len(r) > max(di, si)]
    return ok, out


def load_t0(root: Path, params_sha: str | None) -> tuple[dict | None, str]:
    p = root / "data" / "pick" / "t0.json"
    if not p.exists():
        return None, "absent"
    try:
        j = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None, "unreadable"
    if not isinstance(j, dict) or j.get("params_sha") != params_sha:
        return None, "params_sha_mismatch"
    return j, "ok"


def build(root: Path, T: str | None = None, getter: Callable[..., list[dict]] | None = None) -> dict[str, Any]:
    if T is None:
        lj = _read_json(root / "data" / "web" / "latest.json", "latest")
        T = lj.get("date") if isinstance(lj, dict) else None
    if not isinstance(T, str) or len(T) != DATE_LEN:
        raise BuildPickError(f"資料日不對：{T!r}")
    sc = load_scores(root, T)
    rules = Rules()
    pool = {sid for sid, s in sc["stocks"].items() if s["pool"]}
    dates = load_calendar(root, T)
    ser, miss = load_series(root, dates, pool)
    _, fac, _ = load_factors_file(root / "data" / "factors.json")
    lim = None
    lp = root / "data" / "web" / "lim.json"
    if lp.exists():
        try:
            lim = json.loads(lp.read_text(encoding="utf-8"))
        except ValueError:
            lim = None
    lim_stocks = (lim or {}).get("stocks") or {}
    ev_ok, evs = load_events(root, T, pool)
    t0, t0_status = load_t0(root, sc["params_sha"])

    d2 = fetch_short_ratio(T, pool, getter)
    if d2["status"] != "ok":
        prev = previous_short(root, T)
        if prev:
            d2 = {**prev, "retry_status": d2["status"]}
    d2_ok = d2["status"] == "ok"

    win10 = dates[-P.EVENT_DAYS:]
    per: dict[str, dict[str, Any]] = {}
    for sid in sorted(pool):
        s = sc["stocks"][sid]
        raw = ser[sid]
        adj = adjusted(raw, dates, fac.get(sid))
        exd = fac[sid][0] if sid in fac else []
        exs = set(exd)
        ex_mask = np.asarray([d in exs for d in dates])
        rm = P.risk_metrics(adj["open"], adj["high"], adj["low"], adj["close"], ex_mask, rules.atr_method)
        ret60 = P.ret_n(adj["close"])
        ma20 = P.ma_last(adj["close"])
        cT = _num(adj["close"][-1])
        mph = P.margin_phist(raw["margin_balance"], rules)
        sr_checked = d2_ok and sid in d2["ratio"]
        sr = d2["ratio"].get(sid) if sr_checked else None
        ded = P.deductions(ret60, sr, sr_checked, cT, ma20, mph)
        liq = P.liquidity(raw["amount"], raw["volume"])
        lst = P.lim_status(lim, T, s["mk"])
        flags = lim_stocks.get(sid) if lst["date_ok"] else None
        eff = P.limit_effect(flags, T)
        ev = P.event_flags(exd, win10, evs.get(sid, []), adj["open"], adj["close"], rm["atr"])
        h = {}
        for hz in P.HORIZONS:
            x = s["h"].get(hz) or {}
            sraw = P.short_raw(x.get("lines") or {}, hz)
            h[hz] = {"bs": x.get("bs"), "sraw": sraw, "sfin": P.short_final(sraw, ded["pts"]), "pool": bool(x.get("pool"))}
        per[sid] = {"mk": s["mk"], "rm": rm, "ret60": ret60, "mph": mph, "sr": (sr, sr_checked), "ded": ded, "liq": liq,
                    "lst": lst, "flags": flags, "eff": eff, "ev": ev, "h": h,
                    "confirmed": P.short_confirmed(flags, lst)}

    vthr = {m: P.v_threshold(p["rm"]["v"] for p in per.values() if p["mk"] == m) for m in P.MARKETS}
    for p in per.values():
        p["grade"] = {d: P.risk_grade(p["rm"], vthr[p["mk"]], d) for d in P.DIRECTIONS}

    blocks: dict[str, Any] = {}
    web_blocks: dict[str, Any] = {}
    mode = "grade" if t0 is not None else "rank"
    for m in P.MARKETS:
        for hz in P.HORIZONS:
            fl = sc["market"][(m, hz)]
            state = fl.get("basic_state")
            bd = fl.get("by_direction") or {}
            shift = {d: _num((bd.get(d) or {}).get("threshold_shift_deciles")) for d in P.DIRECTIONS}
            mult = {d: _num((bd.get(d) or {}).get("quota_multiplier")) for d in P.DIRECTIONS}
            n0 = {d: P.n0(state, d) for d in P.DIRECTIONS}
            nn = {d: P.cut_n(n0[d], mult[d]) for d in P.DIRECTIONS}
            ids = [sid for sid, p in per.items() if p["mk"] == m and p["h"][hz]["pool"]]
            # §24.2：undetermined → 該市場該期間兩方向都不出名單（排名版亦同，只寫原因）
            undet = state not in P.STATES
            long_rank = [] if undet else P.rank_rows((sid, per[sid]["h"][hz]["bs"]) for sid in ids if per[sid]["h"][hz]["bs"] is not None)
            short_rank = [] if undet else P.rank_rows((sid, per[sid]["h"][hz]["sfin"]) for sid in ids if per[sid]["h"][hz]["sfin"] is not None)
            thr = {d: P.threshold_from_t0(t0, m, hz, state, d, shift[d]) for d in P.DIRECTIONS}
            blk: dict[str, Any] = {
                "basic_state": state, "data_insufficient": bool(fl.get("data_insufficient")),
                "calibrated_flags": bool(fl.get("calibrated")),
                "shift_deciles": shift, "quota_multiplier": mult, "n0": n0, "n": nn, "threshold": thr,
                "n_pool": len(ids), "n_rank": {"long": len(long_rank), "short": len(short_rank)},
                "reason": None if state in P.STATES else "basic_state_undetermined",
            }
            bs_all = [per[s]["h"][hz]["bs"] for s in ids]
            blk["spearman_100mbs"] = {
                "short_raw": r4(P.spearman([per[s]["h"][hz]["sraw"] if per[s]["h"][hz]["sraw"] is not None else np.nan for s in ids],
                                           [100 - b if b is not None else np.nan for b in bs_all])),
                "short_final": r4(P.spearman([per[s]["h"][hz]["sfin"] if per[s]["h"][hz]["sfin"] is not None else np.nan for s in ids],
                                             [100 - b if b is not None else np.nan for b in bs_all])),
            }
            if mode == "grade":
                gstocks = {sid: {"bs": per[sid]["h"][hz]["bs"], "sraw": per[sid]["h"][hz]["sraw"], "sfin": per[sid]["h"][hz]["sfin"],
                                 "liq_ok": per[sid]["liq"]["ok"], "grade": {d: per[sid]["grade"][d][0] for d in P.DIRECTIONS},
                                 "lim": per[sid]["eff"], "confirmed": per[sid]["confirmed"],
                                 "lim_unchecked": not per[sid]["lst"]["ok"]} for sid in ids}
                blk["lists"] = P.grade_block(state=state, n_long=nn["long"], n_short=nn["short"],
                                             t_long=thr["long"], t_short=thr["short"], stocks=gstocks)
            blocks[f"{m}|{hz}"] = blk

            def row(rk: int, sid: str, score: float, d: str) -> dict[str, Any]:
                p = per[sid]
                g, vi = p["grade"][d]
                out = {"r": rk, "id": sid, "s": r4(score), "g": g, "vi": vi, "lq": int(p["liq"]["ok"]),
                       "rm": p["eff"][d]["remove"], "rs": p["eff"][d]["research"], "tg": p["eff"]["tags"],
                       "lu": int(not p["lst"]["ok"]), "ev": p["ev"]}
                if d == "short":
                    out["raw"] = r4(p["h"][hz]["sraw"])
                    out["d"] = {k: p["ded"][k] for k in ("D1", "D2", "D3")}
                    if mode == "grade":           # 排名版不輸出資格欄，避免前端誤標候選級（§24.2 校準前不標任何候選級）
                        out["cf"] = int(p["confirmed"])
                return out
            web_blocks[f"{m}|{hz}"] = {
                **{k: blk[k] for k in ("basic_state", "data_insufficient", "shift_deciles", "quota_multiplier", "n0", "n",
                                       "threshold", "n_pool", "reason")},
                "long": [row(rk, sid, s, "long") for rk, sid, s in long_rank[:WEB_TOP]],
                "short": [row(rk, sid, s, "short") for rk, sid, s in short_rank[:WEB_TOP]],
                **({"lists": blk["lists"]} if "lists" in blk else {}),
            }

    lim_date = (lim or {}).get("date")
    head = {"schema": SCHEMA, "date": T, "params_sha": sc["params_sha"], "model_version": sc["model_version"], "mode": mode,
            "t0": {"status": t0_status, "version": (t0 or {}).get("version")},
            "lim": {"date": lim_date, "date_ok": lim_date == T},
            "d2": {"status": d2["status"], "n_checked": len(d2["ratio"]) if d2_ok else 0,
                   **({"kept": True} if d2.get("kept") else {})},
            "ev_ok": ev_ok, "missing_bundles": miss, "v_p80": {m: r4(v) for m, v in vthr.items()}}
    snap = {**head, "blocks": blocks, "stocks": {
        sid: {"mk": p["mk"], "risk": {k: r4(p["rm"][k]) for k in ("v", "e", "g", "m")},
              "grade": {d: p["grade"][d][0] for d in P.DIRECTIONS}, "viol": {d: p["grade"][d][1] for d in P.DIRECTIONS},
              "liq": {"med": r4(p["liq"]["med"]), "zero": p["liq"]["zero"], "ok": int(p["liq"]["ok"])},
              "ret60": r4(p["ret60"]), "mph": r4(p["mph"]), "sr": [r4(p["sr"][0]), int(p["sr"][1])],
              "d": {k: p["ded"][k] for k in ("D1", "D2", "D3")}, "ev": p["ev"],
              "lim": {"rm": {d: p["eff"][d]["remove"] for d in P.DIRECTIONS}, "rs": p["eff"]["short"]["research"],
                      "tg": p["eff"]["tags"], "un": p["lst"]["unchecked"]}, "cf": int(p["confirmed"]),
              "h": {hz: [r4(p["h"][hz]["bs"]), r4(p["h"][hz]["sraw"]), r4(p["h"][hz]["sfin"])] for hz in P.HORIZONS}}
        for sid, p in sorted(per.items())}}
    web = {**head, "top": WEB_TOP, "blocks": web_blocks}
    return {"T": T, "snap": snap, "web": web}


def write_if_changed(path: Path, text: str) -> bool:
    try:
        if path.exists() and path.read_text(encoding="utf-8") == text:
            return False
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(text, encoding="utf-8")
        os.replace(tmp, path)
        return True
    except OSError as e:
        raise BuildPickError(f"寫不出 {path}: {e}") from e


def main(argv: list[str] | None = None, getter_factory: Callable[[], Any] = finmind_getter) -> int:
    ap = argparse.ArgumentParser(description="P4-L2：選多空排名 data/pick/<T>.json＋data/web/pick.json（docs/P4-PREVIEW.md §24）")
    ap.add_argument("--root", default=".")
    ap.add_argument("--date", help="資料日（預設 data/web/latest.json 的 date；本機量測用）")
    ap.add_argument("--no-fetch", action="store_true", help="不抓融券餘額（D2 一律未檢查）")
    ap.add_argument("--no-web", action="store_true", help="不寫 data/web/pick.json（補量歷史日用）")
    a = ap.parse_args(argv)
    root = Path(a.root).resolve()
    try:
        getter = None if a.no_fetch else getter_factory()
        res = build(root, a.date, getter)
        T = res["T"]
        w1 = write_if_changed(root / "data" / "pick" / f"{T}.json", dumps(res["snap"]))
        w2 = False if a.no_web else write_if_changed(root / "data" / "web" / "pick.json", dumps(res["web"]))
    except BuildPickError as e:
        print(f"::error::build_pick: {e}")
        return 2
    s = res["snap"]
    print(f"build_pick T={T} mode={s['mode']} t0={s['t0']['status']} d2={s['d2']['status']} lim={s['lim']} "
          f"pool={len(s['stocks'])} 寫入 snapshot={w1} web={w2}")
    if s["d2"]["status"] != "ok":
        print(f"::notice::融券餘額未取得（{s['d2']['status']}）→ D2 標未檢查、不扣分")
    if s["missing_bundles"]:
        print(f"::warning::缺原料包 {len(s['missing_bundles'])} 日：{s['missing_bundles'][:5]}")
    for k, b in s["blocks"].items():
        print(f"  {k} {b['basic_state']} N={b['n']} rank={b['n_rank']} spearman(100-bs)={b['spearman_100mbs']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
