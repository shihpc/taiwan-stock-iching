#!/usr/bin/env python3
"""§16.5 `:716` 被標組的**族組成診斷**（裁定 #65 ②，`docs/P3-CALIBRATION.md` §27）。

`score_stats.py` 只看得到爻分數；`:716` 標出「達邊界比例 > 20%」「單一值佔比 > 20%」等須解釋的組之後，
解釋**必須有實測依據**。本檔對報告 `summary.explain_716` 所列每一組，抽樣列、以**真實計分程式碼**重算該爻，
量出「缺了哪一族、剩下哪些子指標、是哪個子指標被截斷／達端點」。**只陳述量到的數字，不寫成因推測。**

## 重算怎麼做（為什麼不是整段重播）

整段重播要 12.6 小時（`docs/P2-KICKOFF.md` §5 #38）。本檔只做重播中**影響分數**的那幾件事，且全部呼叫原函式：

1. `ReplaySource.read_day` → `WindowCache.ingest`：自最早交易日逐日 ingest 到最晚抽樣日（視窗語意與重播相同，
   **不計分**）。
2. 大盤：只在「大盤列被抽到」或「個股上爻被抽到」的日子 `wc.market_inputs` → `score_market`（後者取當日大盤方向分數）。
   `MarketInputs` 唯一的跨日欄位 `line2_score_t_minus_5` 只進旗標（`market.py` 的 `flag_breadth`）、不進爻分數，
   故不重建大盤二爻鏈；遲滯、旗標、ADV 也都不影響爻分數，不重做。**這些省略若將來變得影響分數，parity 守門會擋。**
3. 個股四爻（短線／波段）要 T−9…T−1 的二爻分數：由 `scores.db` 查出該檔 T 之前最近 9 個**被計分日**
   （db 有列＝重播有 push），在那幾天以 `stock.line2_trend` 重算二爻、填回 `CrossDayState.stock_line2`。
   **歷史值是重算的，不是從 db 讀的**（db 只用來知道「哪幾天」）。
4. 抽樣列：`wc.stock_inputs(...)` → `score_stock`（個股，基本面取 `FundamentalsBridge.provider()`）或上一步的
   `MarketScores`（大盤），取出該爻的 `LineResult`。

攔截（同 `tests/test_n_once.py` 手法）：`stock.S_clip`／`market.S_clip`／`stock.sub_result`／`market.sub_result`
包一層，只記錄呼叫參數（x、c、d、direction、原生值域），**回傳值原樣不動**。族在場與否、缺值原因、子指標分數、
clip 是否生效、重配後權重，全部直接讀計分函式回傳的 `LineResult → FamilyResult → SubResult`。

## parity 守門（不過就不產出報告）

每個抽樣列重算出的爻分數必須與 db 該列相符（|差| ≤ 1e-9），且逐爻 `reweighted` 旗標相同；任一列不符 → rc=2、
印前幾筆、**不寫報告**（不相符的診斷沒有意義）。另守門：`export_dataset.check_params`（db 是現行碼算的）、
`score_stats.check_versions`（model_version 與登錄檔一致）、報告的 `data_version`／`params_sha`／樣本段與 db 及本檔寫死值相同、
報告各組的 n／登錄區間／`mode_value`／`mode_share`／`share_at_boundary` 與本檔由 db 重數的結果相同。

## 抽樣（分層）

樣本段寫死＝`SEGMENTS["train"][0]`～`SEGMENTS["valid"][1]`（同 `score_stats.py`，裁定 #64 ①，CLI 不開日期）。
每組母體＝該段內、`line_k_reweighted` 與 coverage 相符、分數非 NULL 的列。以「達邊界」（距登錄端點 ≤ 0.01）與
「單一值」（四捨五入 6 位＝該組最常出現值）兩個旗標切**四層**：**BM 兩者皆是**／**B 只達邊界**／**M 只是單一值**／**R 其餘**
（最常出現值可能就貼在邊界上，如 92.702703；切四層才能讓「邊界列數」與「單一值列數」的估計都**恰等於**母體值）。
母體 ≤ `--per-group` 時全取（普查、權重皆 1）；否則 BM、B、M 各配 `per_group // 4`（不足全取），其餘給 R，
R 不足再依 BM→B→M 回補。
層內以 `numpy.random.default_rng(--seed)` 不放回均勻抽樣。**權重＝該層母體列數 ÷ 該層抽樣列數**；報告中所有「比例」
一律是**加權後的母體估計值**（分層樣本本身的比例不是母體比例），並另列各層原始抽樣筆數。

rc：0 成功／2 中止（parity 不符、守門不過、讀不到檔、任何例外）。
"""
from __future__ import annotations

import argparse
import json
import resource
import sys
import time
from array import array
from collections import defaultdict
from pathlib import Path
from typing import Any

import numpy as np

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "src"))
sys.path.insert(0, str(REPO / "scripts"))

from iching import replay_io as RIO  # noqa: E402
from iching import replay_state as RS  # noqa: E402
from iching.features_io import params_fingerprint  # noqa: E402
from iching.run_common import build_params_payload  # noqa: E402
from iching.score import market as MKT  # noqa: E402
from iching.score import stock as STK  # noqa: E402
from iching.score.assemble import MARKET_STOCK_ID  # noqa: E402
from iching.score.params import HORIZONS, MARKETS, SCOPE_MARKET, SCOPE_STOCK, build_params  # noqa: E402
from iching.score.transform import S_HI, S_LO, S_RANGE  # noqa: E402
from iching.scores_io import ScoreStore  # noqa: E402
from iching.universe import is_financial  # noqa: E402

import score_stats as SS  # noqa: E402

PARITY_TOL = 1e-9
DEFAULT_PER_GROUP = 3000
DEFAULT_SEED = 20260924
HIST_N = RS.STOCK_LINE2_HIST                  # 9：T−9…T−1
SAMPLE_START, SAMPLE_END = SS.SAMPLE_START, SS.SAMPLE_END
STRATA = ("boundary_mode", "boundary", "mode", "rest")
STRATA_SHORT = {"boundary_mode": "BM", "boundary": "B", "mode": "M", "rest": "R"}
TOP_K = 12                                    # 每個分布表最多列幾種型態（其餘併為「其他」）


class DiagError(Exception):
    pass


class ParityError(DiagError):
    pass


# ---------------------------------------------------------------------------
# 攔截：只記錄、不改回傳值
# ---------------------------------------------------------------------------
class Capture:
    """`S_clip`／`sub_result` 的呼叫紀錄。`active=False` 時完全透明（大盤鏈、二爻歷史重算都不記）。

    配對規則：`sub_result(iid, out)` 的 `out` 若**就是**某次 `S_clip` 的回傳物件（`is`）→ `direct`（有些 Ind 先算好、
    隔了幾個 `sub_result` 才被包，如大盤上爻 `spx_ma_distance`，所以比對全部未配對者）。否則若 `out` 的值域是 S 值域、
    且自上一次 `sub_result` 以來**恰有一次**新的 `S_clip` 呼叫且 clip 旗標相同 → `indirect`（情境型內部呼叫一次 `S_clip`
    再重包，如 `margin_scenario`）。其餘不配（u／c／d 記 None），報告照實寫「非 S_clip 直出」。"""

    def __init__(self) -> None:
        self.active = False
        self.pending: list[tuple[Any, tuple, int]] = []   # (S_clip 回傳的 Ind, (x, c, d, direction), 序號)
        self.seq = 0
        self.last_sub_seq = 0
        self.info: dict[int, tuple[Any, dict]] = {}       # id(SubResult) → (SubResult 強參照, 紀錄)

    def reset(self) -> None:
        self.pending.clear()
        self.info = {}
        self.last_sub_seq = self.seq

    def wrap_sclip(self, orig):
        def s_clip(x, c, d, direction=1):
            out = orig(x, c, d, direction)
            if self.active:
                self.seq += 1
                self.pending.append((out, (float(x), float(c), float(d), int(direction)), self.seq))
            return out
        return s_clip

    def wrap_sub(self, orig):
        def sub(indicator_id, out, *a, **k):
            res = orig(indicator_id, out, *a, **k)
            if self.active:
                rec: dict[str, Any] = {"native_range": None, "sclip": None, "sclip_match": None}
                if hasattr(out, "native_range"):
                    rec["native_range"] = [float(v) for v in out.native_range]
                    hit = [i for i, p in enumerate(self.pending) if p[0] is out]
                    new = [p for p in self.pending if p[2] > self.last_sub_seq]
                    if hit:
                        rec["sclip"], rec["sclip_match"] = self.pending[hit[-1]][1], "direct"
                        del self.pending[hit[-1]]
                    elif (tuple(out.native_range) == S_RANGE and len(new) == 1
                          and bool(new[0][0].clipped) == bool(out.clipped)):
                        rec["sclip"], rec["sclip_match"] = new[0][1], "indirect"
                self.info[id(res)] = (res, rec)
                self.last_sub_seq = self.seq
            return res
        return sub


def install_capture() -> tuple[Capture, list[tuple[Any, str, Any]]]:
    cap = Capture()
    saved = []
    for mod in (STK, MKT):
        for name, wrap in (("S_clip", cap.wrap_sclip), ("sub_result", cap.wrap_sub)):
            orig = getattr(mod, name)
            saved.append((mod, name, orig))
            setattr(mod, name, wrap(orig))
    return cap, saved


def uninstall_capture(saved) -> None:
    for mod, name, orig in reversed(saved):
        setattr(mod, name, orig)


# ---------------------------------------------------------------------------
# 報告與守門
# ---------------------------------------------------------------------------
def _gkey(g: dict[str, Any]) -> tuple[str, str, str, str, str]:
    return (g["scope"], g["market"], g["horizon"], str(g["line"]), g["coverage"])


def load_report(path: Path, start: str, end: str) -> dict[str, Any]:
    rep = json.loads(Path(path).read_text(encoding="utf-8"))
    s = rep.get("sample") or {}
    if (s.get("start"), s.get("end")) != (start, end):
        raise DiagError(f"報告樣本段 {s.get('start')}～{s.get('end')} ≠ 本檔 {start}～{end}（裁定 #64 ①，寫死）")
    if "mode_share_max" not in rep:
        raise DiagError("報告沒有 mode_share_max（舊版 score_stats 產物）；請用現行 score_stats.py 重產")
    return rep


def check_report_vs_db(rep: dict[str, Any], store: ScoreStore, dv: str, reg: dict[str, Any]) -> tuple[str, dict]:
    import export_dataset as ED
    sha, params = ED.check_params(store, dv)                   # db 不是現行碼算的就拋錯
    SS.check_versions(store, dv, reg)                           # model_version 與登錄檔一致
    if rep.get("data_version") != dv:
        raise DiagError(f"報告 data_version={rep.get('data_version')} ≠ db {dv}")
    if rep.get("params_sha") != sha:
        raise DiagError(f"報告 params_sha={rep.get('params_sha')} ≠ db {sha}；報告不是這份 db 產的")
    want = {m: i["model_version"] for m, i in reg["markets"].items()}
    if rep.get("registry_model_versions") != want:
        raise DiagError(f"報告的 registry_model_versions {rep.get('registry_model_versions')} ≠ 登錄檔 {want}")
    # 重算用的 ParamSet／window／基本面開關必須就是這份 db 的：同一支 build_params_payload 重算指紋
    mv = {m: build_params(m).model_version() for m in MARKETS}
    payload = build_params_payload(mv, int(params["window"]), RS.CrossDayState().adv,
                                   fundamentals=bool(params.get("fundamentals", True)))
    if params_fingerprint(payload) != sha:
        raise DiagError(f"以現行 ParamSet／window={params['window']} 重建的參數指紋 {params_fingerprint(payload)} ≠ db {sha}")
    return sha, params


# ---------------------------------------------------------------------------
# 母體讀取與分層抽樣
# ---------------------------------------------------------------------------
def read_populations(store: ScoreStore, dv: str, keys: list[tuple], start: str, end: str):
    """每個 (market, horizon) 掃一次，依 (scope, line, coverage) 分派。回 {key: (sid_idx int32, date_idx int32, value f8)}＋解碼表。"""
    by_mh: dict[tuple[str, str], list[tuple]] = defaultdict(list)
    for k in keys:
        by_mh[(k[1], k[2])].append(k)
    sid_of: list[str] = []
    sid_ix: dict[str, int] = {}
    date_of: list[str] = []
    date_ix: dict[str, int] = {}
    acc: dict[tuple, tuple[array, array, array]] = {k: (array("i"), array("i"), array("d")) for k in keys}
    for (m, h), ks in sorted(by_mh.items()):
        lines = sorted({k[3] for k in ks})
        cols = ", ".join(f"s.line_{k}, s.line_{k}_reweighted" for k in lines)
        sql = (f"SELECT s.scope, s.stock_id, s.date, {cols} FROM scores s JOIN versions v ON v.version_id = s.version_id "
               "WHERE v.data_version = ? AND s.market = ? AND s.horizon = ? AND +s.date >= ? AND +s.date <= ?")
        want = {(k[0], k[3], 1 if k[4] == "reweighted" else 0): k for k in ks}
        for row in store.conn.execute(sql, (dv, m, h, start, end)):
            scope, sid, d = row[0], row[1], row[2]
            for i, ln in enumerate(lines):
                v, rw = row[3 + 2 * i], row[4 + 2 * i]
                if v is None:
                    continue
                k = want.get((scope, ln, rw))
                if k is None:
                    continue
                si = sid_ix.get(sid)
                if si is None:
                    si = sid_ix[sid] = len(sid_of)
                    sid_of.append(sid)
                di = date_ix.get(d)
                if di is None:
                    di = date_ix[d] = len(date_of)
                    date_of.append(d)
                a = acc[k]
                a[0].append(si)
                a[1].append(di)
                a[2].append(float(v))
    pops = {k: (np.frombuffer(a[0], dtype=np.int32), np.frombuffer(a[1], dtype=np.int32), np.frombuffer(a[2], dtype="d"))
            for k, a in acc.items()}
    return pops, sid_of, date_of


def strata_masks(v: np.ndarray, reg_lo_hi: tuple[float, float] | None) -> tuple[np.ndarray, np.ndarray, float | None, float | None]:
    """回 (boundary 遮罩, mode 遮罩(含與邊界重疊者), mode_value, mode_share)。口徑與 score_stats.group_stats 相同。"""
    n = v.size
    if n == 0:
        return np.zeros(0, bool), np.zeros(0, bool), None, None
    r = np.round(v, SS.DISTINCT_DECIMALS)
    uniq, counts = np.unique(r, return_counts=True)
    im = int(np.argmax(counts))
    mv, ms = float(uniq[im]), int(counts[im]) / n
    bnd = np.zeros(n, bool)
    if reg_lo_hi is not None:
        lo, hi = reg_lo_hi
        bnd = (np.abs(v - lo) <= SS.TOL) | (np.abs(v - hi) <= SS.TOL)
    return bnd, r == mv, mv, ms


def allocate(sizes: dict[str, int], total: int) -> dict[str, int]:
    """BM、B、M 各配 total//4（不足全取），其餘給 R，R 不足再依 BM→B→M 回補。"""
    if total < len(STRATA):
        raise DiagError(f"--per-group 至少 {len(STRATA)}（得 {total}）")
    if sum(sizes.values()) <= total:
        return dict(sizes)
    q = total // 4
    n = {s: min(sizes[s], q) for s in STRATA[:3]}
    n["rest"] = min(sizes["rest"], total - sum(n.values()))
    left = total - sum(n.values())
    for s in STRATA[:3]:
        add = min(left, sizes[s] - n[s])
        n[s] += add
        left -= add
    return n


def draw(pop, reg_lo_hi, per_group: int, rng: np.random.Generator) -> dict[str, Any]:
    sid, date, v = pop
    bnd, mode_all, mv, ms = strata_masks(v, reg_lo_hi)
    idx = {"boundary_mode": np.flatnonzero(bnd & mode_all), "boundary": np.flatnonzero(bnd & ~mode_all),
           "mode": np.flatnonzero(mode_all & ~bnd), "rest": np.flatnonzero(~bnd & ~mode_all)}
    sizes = {s: int(idx[s].size) for s in STRATA}
    alloc = allocate(sizes, per_group)
    picked, weight = {}, {}
    for s in STRATA:
        k = alloc[s]
        picked[s] = np.sort(idx[s] if k >= sizes[s] else rng.choice(idx[s], size=k, replace=False))
        weight[s] = (sizes[s] / k) if k else None
    return {"n": int(v.size), "n_boundary": int(bnd.sum()), "n_mode": int(mode_all.sum()), "mode_value": mv, "mode_share": ms,
            "share_at_boundary": (float(bnd.sum()) / v.size if reg_lo_hi is not None else None) if v.size else None,
            "sizes": sizes, "alloc": alloc, "weight": weight, "picked": picked, "mode_mask": mode_all, "bnd_mask": bnd}


# ---------------------------------------------------------------------------
# 逐列解析
# ---------------------------------------------------------------------------
def _fmt_v(x: float) -> str:
    return f"{x:.4f}"


def sub_status(sr, rec: dict | None) -> str:
    if sr.score is None:
        return f"缺:{sr.missing.reason if sr.missing is not None else '?'}"   # Missing 的 __bool__ 恒為 False
    if sr.clipped:
        return "clip↑" if sr.score > 50.0 else "clip↓"
    if abs(sr.score - S_HI) <= SS.TOL or abs(sr.score - S_LO) <= SS.TOL:
        return "端點↑(未clip)" if sr.score > 50.0 else "端點↓(未clip)"
    return f"={_fmt_v(sr.score)}"


def analyze_line(lr, cap: Capture, policy: str) -> dict[str, Any]:
    """一爻 → 族在場型態、子指標簽名、每個在場子指標的截斷位置與重配後權重。"""
    fams = {f.family: f for f in lr.families}
    names = list(lr.expected_weights)
    got = sum(w for n, w in lr.expected_weights.items() if fams[n].score is not None)
    pat_parts, sig_parts, subs = [], [], []
    for n in names:
        f = fams[n]
        if f.score is None:
            reason = f.missing.reason if f.missing is not None else "?"
            pat_parts.append(f"{n} 缺:{reason}")
            sig_parts.append(f"{n} 缺:{reason}")
            continue
        pat_parts.append(f"{n} 在場")
        present = [s for s in f.subs if s.score is not None]
        wsum = sum(s.sub_weight for s in present)
        inner = []
        for s in f.subs:
            rec = (cap.info.get(id(s)) or (None, None))[1]
            st = sub_status(s, rec)
            inner.append(f"{s.indicator_id}{'' if st.startswith('=') else ' '}{st}")
            if s.score is None:
                continue
            sub_eff = (1.0 / len(present)) if (policy == "equal_mean" and f.reweighted) else s.sub_weight / wsum
            u = c = d = None
            if rec and rec.get("sclip"):
                x, c, d, dr = rec["sclip"]
                u = dr * (x - c) / (3.0 * d)
            subs.append({"family": n, "id": s.indicator_id, "status": st, "clipped": bool(s.clipped),
                         "eff_weight": lr.expected_weights[n] / got * sub_eff, "u": u, "c": c, "d": d,
                         "sclip_match": rec.get("sclip_match") if rec else None,
                         "native_range": rec.get("native_range") if rec else None})
        tag = ""
        if f.meta.get("floor_applied"):
            tag = "［族下限生效］"
        sig_parts.append(f"{n}{{{'｜'.join(inner)}}}{tag}")
    sig = "；".join(sig_parts)
    if lr.meta.get("overheat_cap_applied"):
        sig += "；［過熱封頂生效］"
    return {"pattern": "；".join(pat_parts), "signature": sig, "subs": subs}


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------
def rss_mib() -> float:
    ru = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return ru / 1024.0 if sys.platform != "darwin" else ru / (1024.0 * 1024.0)


def _prev_scored_dates(store: ScoreStore, vids: list[int], sid: str) -> list[str]:
    ph = ",".join("?" for _ in vids)
    return [r[0] for r in store.conn.execute(
        f"SELECT DISTINCT date FROM scores WHERE stock_id=? AND version_id IN ({ph}) ORDER BY date", (sid, *vids))]


def recompute(cache: Path, features: Path | None, dv: str, params: dict, samples: list[dict], store: ScoreStore,
              *, quiet: bool = False) -> None:
    """就地把重算結果寫進每個 sample 的 `got`（分數）／`got_rw`／`ana`，或 `fail`（不能重算的原因）。"""
    window = int(params["window"])
    use_fund = bool(params.get("fundamentals", True))
    ps = {m: build_params(m) for m in MARKETS}
    policy = {m: ps[m].rules.family_missing_policy for m in MARKETS}
    market_days = {s["date"] for s in samples if s["scope"] == SCOPE_MARKET or s["line"] == "6"}
    by_date: dict[str, list[dict]] = defaultdict(list)
    for s in samples:
        by_date[s["date"]].append(s)
    last = max(by_date)
    # 個股四爻（短線／波段）需要 T−9…T−1 的二爻：查該檔被計分日，排出要在哪幾天重算誰的二爻
    need_l2: dict[str, set[tuple[str, str]]] = defaultdict(set)
    hist_dates: dict[tuple[str, str, str], list[str]] = {}
    l4 = [s for s in samples if s["scope"] == SCOPE_STOCK and s["line"] == "4"]
    if l4:
        vids = [r[0] for r in store.conn.execute("SELECT version_id FROM versions WHERE data_version=?", (dv,))]
        by_sid: dict[str, list[dict]] = defaultdict(list)
        for s in l4:
            by_sid[s["sid"]].append(s)
        for sid, ss in sorted(by_sid.items()):                 # 逐檔查一次、用完即丟（不把全部日期串留在記憶體）
            scored_days = _prev_scored_dates(store, vids, sid)
            for s in ss:
                T, h = s["date"], s["horizon"]
                ds = [d for d in scored_days if d < T][-HIST_N:]
                hist_dates[(sid, T, h)] = ds
                for d in ds:
                    need_l2[d].add((sid, h))
    first = min([*by_date, *need_l2])
    l2val: dict[tuple[str, str, str], float | None] = {}

    src = RIO.ReplaySource(cache, dv, features_path=features, window=window)
    cap, saved = install_capture()
    try:
        all_dates = src.trading_dates()
        missing_days = sorted(set(by_date) - set(all_dates))
        if missing_days:
            raise DiagError(f"抽樣日不在原料交易日軸：{missing_days[:3]}")
        wc = RS.WindowCache(src.pool, src.factors, window=window)
        bridge = src.load_fundamentals(all_dates) if use_fund else None
        provider = bridge.provider() if bridge is not None else None
        empty = RS.CrossDayState()                                      # 唯讀用；本迴圈從不寫入它
        t0 = time.time()
        n_day = 0
        for T in all_dates:
            if T > last:
                break
            wc.ingest(src.read_day(T))
            n_day += 1
            if T < first:
                continue
            active = [m for m in MARKETS if wc.has_market(m) and wc.market_dates(m) and wc.market_dates(m)[-1] == T]
            direction: dict[str, dict[str, float | None]] = {m: {} for m in MARKETS}
            if T in market_days:
                # 大盤：只在需要的日子算（大盤列被抽到、或個股上爻要當日大盤方向分數）。`MarketInputs` 唯一的跨日欄位
                # `line2_score_t_minus_5` 只進旗標（`market.py` 的 `flag_breadth`），不進任何爻分數，故以空的
                # CrossDayState 餵入、不重建大盤二爻鏈；將來若它進了爻分數，parity 守門會擋下（rc=2）。
                todays_market = [s for s in by_date.get(T, ()) if s["scope"] == SCOPE_MARKET]
                for mk in active:
                    mi = wc.market_inputs(mk, T, empty)
                    for h in HORIZONS:
                        cap.reset()
                        cap.active = True
                        ms = MKT.score_market(mi, ps[mk], h)
                        cap.active = False
                        ds = ms.direction_score
                        direction[mk][h] = float(ds) if isinstance(ds, (int, float)) and not isinstance(ds, bool) else None
                        for s in todays_market:
                            if s["market"] == mk and s["horizon"] == h:
                                lr = ms.lines[s["line"]]
                                s["got"], s["got_rw"] = lr.score, int(lr.reweighted)
                                s["ana"] = analyze_line(lr, cap, policy[mk])
                cap.reset()
            today = set(wc.stock_ids_today())
            for sid, h in sorted(need_l2.get(T, ())):
                mk = wc.pool.listed(sid, T)
                if sid not in today or mk not in active:
                    l2val[(sid, h, T)] = "unscorable"
                    continue
                si = wc.stock_inputs(sid, h, T, empty)          # 二爻不讀任何跨日狀態
                l2val[(sid, h, T)] = STK.line2_trend(si, ps[mk], h).score
            stock_samples = [s for s in by_date.get(T, ()) if s["scope"] == SCOPE_STOCK]
            done: dict[tuple[str, str], Any] = {}
            for s in sorted(stock_samples, key=lambda x: (x["sid"], x["horizon"], x["line"])):
                sid, h = s["sid"], s["horizon"]
                mk = wc.pool.listed(sid, T)
                if sid not in today or mk is None or mk not in active:
                    s["fail"] = "重算時該檔當日不在計分名單（stock_ids_today／市場無指數列）"
                    continue
                if mk != s["market"]:
                    s["fail"] = f"重算時所屬市場 {mk} ≠ db 列市場 {s['market']}"
                    continue
                key = (sid, h)
                if key not in done:
                    hc = RS.CrossDayState()
                    hk = (sid, T, h)
                    if hk in hist_dates:
                        vals = [l2val.get((sid, h, d), "unscorable") for d in hist_dates[hk]]
                        if any(v == "unscorable" for v in vals):
                            s["fail"] = "二爻歷史日中有重算時不在計分名單的日子"
                            continue
                        hc.stock_line2[RS._k(sid, h)] = list(vals)
                    extra = provider(sid, T) if provider is not None else {}
                    si = wc.stock_inputs(sid, h, T, hc, direction[mk], is_financial=is_financial(wc.pool.industry_of(sid)),
                                         monthly_revenue=extra.get("monthly_revenue"),
                                         industry_median_3m_yoy=extra.get("industry_median_3m_yoy"),
                                         industry_revenue_n=extra.get("industry_revenue_n"),
                                         fundamentals=extra.get("fundamentals"))
                    cap.reset()
                    cap.active = True
                    ss = STK.score_stock(si, ps[mk], h)
                    cap.active = False
                    # 同一 (檔, 期間) 被多組抽到時只算一次；另記下**實際餵進計分**的二爻歷史與大盤方向（測試核對用）
                    done[key] = (ss, dict(cap.info), list(si.line2_score_history.get(h) or []),
                                 si.market_direction_score.get(h))
                ss, info, hist, mdir = done[key]
                cap.info = dict(info)
                lr = ss.lines[s["line"]]
                s["got"], s["got_rw"] = lr.score, int(lr.reweighted)
                s["ana"] = analyze_line(lr, cap, policy[mk])
                s["hist"], s["mdir"] = hist, mdir
            cap.reset()
            if not quiet and n_day % 100 == 0:
                print(f"  {n_day} 日（{T}） {time.time() - t0:.0f}s RSS {rss_mib():.0f} MiB", flush=True)
    finally:
        uninstall_capture(saved)
        src.close()


def parity(samples: list[dict]) -> dict[str, Any]:
    bad = []
    max_diff = 0.0
    for s in samples:
        why = s.get("fail")
        if why is None:
            if "got" not in s:
                why = "未重算（抽樣日未走到）"
            elif s["got"] is None:
                why = "重算結果為未知爻"
            else:
                diff = abs(s["got"] - s["db"])
                max_diff = max(max_diff, diff)
                if not diff <= PARITY_TOL:
                    why = f"|差|={diff:.3e}"
                elif s["got_rw"] != s["rw"]:
                    why = f"reweighted 重算 {s['got_rw']} ≠ db {s['rw']}"
        if why is not None:
            bad.append({**{k: s[k] for k in ("scope", "market", "horizon", "line", "coverage", "sid", "date", "db")},
                        "got": s.get("got"), "why": why})
    if bad:
        lines = [f"  {b['scope']} {b['market']} {b['horizon']} 爻{b['line']} {b['coverage']} {b['sid']} {b['date']} "
                 f"db={b['db']!r} 重算={b['got']!r}：{b['why']}" for b in bad[:8]]
        raise ParityError(f"parity 不符 {len(bad)}／{len(samples)} 列（容許 {PARITY_TOL}），不產出報告：\n" + "\n".join(lines))
    return {"rows": len(samples), "max_abs_diff": max_diff, "tol": PARITY_TOL}


def _wshare(items: list[tuple[str, float]]) -> list[dict[str, Any]]:
    tot = sum(w for _, w in items)
    by: dict[str, list[float]] = defaultdict(lambda: [0.0, 0])
    for k, w in items:
        by[k][0] += w
        by[k][1] += 1
    rows = sorted(({"key": k, "est_rows": v[0], "share": v[0] / tot if tot else None, "n_sample": v[1]}
                   for k, v in by.items()), key=lambda r: (-r["est_rows"], r["key"]))
    return rows


def _u_summary(rows: list[dict]) -> dict[str, Any]:
    """在場子指標的截斷位置 u＝direction·(x−c)／(3d)（|u|≥1＝x 落在 [c−3d, c+3d] 外、clip 生效）。樣本內未加權。"""
    per: dict[str, dict[str, Any]] = {}
    for r in rows:
        for sb in r["ana"]["subs"]:
            k = f"{sb['family']}.{sb['id']}"
            e = per.setdefault(k, {"n": 0, "clipped": 0, "u": [], "c": set(), "d": set(), "match": set(), "status": defaultdict(int)})
            e["n"] += 1
            e["clipped"] += int(sb["clipped"])
            e["status"][sb["status"] if not sb["status"].startswith("=") else "內部"] += 1
            if sb["u"] is not None:
                e["u"].append(sb["u"])
                e["c"].add(round(sb["c"], 12))
                e["d"].add(round(sb["d"], 12))
            if sb["sclip_match"]:
                e["match"].add(sb["sclip_match"])
    out = {}
    for k, e in sorted(per.items()):
        u = np.array(e["u"]) if e["u"] else None
        out[k] = {"n_sample": e["n"], "clipped_share": e["clipped"] / e["n"], "status": dict(sorted(e["status"].items())),
                  "u_min": None if u is None else float(u.min()), "u_median": None if u is None else float(np.median(u)),
                  "u_max": None if u is None else float(u.max()),
                  "c": sorted(e["c"])[:3] if len(e["c"]) <= 3 else f"{len(e['c'])} 個相異值（滾動 c）",
                  "d": sorted(e["d"])[:3] if len(e["d"]) <= 3 else f"{len(e['d'])} 個相異值",
                  "sclip_match": sorted(e["match"])}
    return out


def summarize(gkey: tuple, g: dict[str, Any], dr: dict[str, Any], samples: list[dict]) -> dict[str, Any]:
    w = dr["weight"]
    items = [(s, w[s["stratum"]]) for s in samples]
    N = dr["n"]
    pat: dict[str, dict[str, float]] = defaultdict(lambda: {"est": 0.0, "est_b": 0.0, "est_m": 0.0,
                                                            **{f"n_{st}": 0 for st in STRATA}})
    for s, wt in items:
        p = pat[s["ana"]["pattern"]]
        p["est"] += wt
        p["est_b"] += wt * s["is_b"]
        p["est_m"] += wt * s["is_m"]
        p[f"n_{s['stratum']}"] += 1
    patterns = sorted(({"pattern": k, "est_rows": v["est"], "est_share": v["est"] / N,
                        "boundary_share_within": v["est_b"] / v["est"], "mode_share_within": v["est_m"] / v["est"],
                        "n_sample": {st: v[f"n_{st}"] for st in STRATA}}
                       for k, v in pat.items()), key=lambda r: (-r["est_rows"], r["pattern"]))
    b_rows = [s for s, _ in items if s["is_b"]]
    m_rows = [s for s, _ in items if s["is_m"]]
    b_dist = _wshare([(s["ana"]["signature"], w[s["stratum"]]) for s in b_rows])
    m_dist = _wshare([(s["ana"]["signature"], w[s["stratum"]]) for s in m_rows])
    return {
        "scope": gkey[0], "market": gkey[1], "horizon": gkey[2], "line": gkey[3], "coverage": gkey[4],
        "reasons": g["reasons"], "registry": g.get("registry"),
        "population": {"n": N, "n_boundary": dr["n_boundary"], "share_at_boundary": dr["share_at_boundary"],
                       "mode_value": dr["mode_value"], "n_mode": dr["n_mode"], "mode_share": dr["mode_share"]},
        "strata": {s: {"population": dr["sizes"][s], "sampled": dr["alloc"][s], "weight": dr["weight"][s]} for s in STRATA},
        "n_sampled": len(samples),
        "patterns": patterns,
        "boundary_rows": {"signatures": b_dist, "subs": _u_summary(b_rows)},
        "mode_rows": {"signatures": m_dist, "subs": _u_summary(m_rows)},
        "conclusion": conclusion(dr, b_dist, m_dist, len(b_rows), len(m_rows)),
    }


def conclusion(dr, b_dist, m_dist, nb, nm) -> str:
    """量測結論：只陳述數字（哪一型態佔幾成），不寫成因。"""
    parts = []
    if dr["n_boundary"]:
        top = b_dist[0]
        parts.append(f"邊界列 {dr['n_boundary']:,} 列（抽 {nb} 列）中 {top['share'] * 100:.1f}% 為『{top['key']}』")
    else:
        parts.append("無達邊界列")
    if dr["n_mode"]:
        top = m_dist[0]
        parts.append(f"最常出現值 {dr['mode_value']:.6f} 的列 {dr['n_mode']:,} 列（佔 {dr['mode_share'] * 100:.1f}%，抽 {nm} 列）中 "
                     f"{top['share'] * 100:.1f}% 為『{top['key']}』")
    return "；".join(parts) + "。"


def run(report: Path, db: Path, out: Path, *, cache_dir: Path, features: Path | None = None,
        registry: Path = SS.REGISTRY, per_group: int = DEFAULT_PER_GROUP, seed: int = DEFAULT_SEED,
        start: str | None = None, end: str | None = None, quiet: bool = False,
        keep_samples: bool = False) -> dict[str, Any]:
    """`start`／`end` 只供測試（合成資料在 2020 年）；CLI 一律用裁定 #64 ① 的寫死值（None＝取模組常數，
    於呼叫時解析）。`keep_samples=True` 時回傳值另帶 `_samples`（逐列重算明細，供測試核對；**不寫進報告檔**）。"""
    t0 = time.time()
    start = SAMPLE_START if start is None else start
    end = SAMPLE_END if end is None else end
    if per_group < len(STRATA):
        raise DiagError(f"--per-group 至少 {len(STRATA)}（得 {per_group}）")
    rep = load_report(report, start, end)
    reg = SS.load_registry(registry)
    idx = SS._registry_index(reg)
    groups_by_key = {_gkey(g): g for g in rep["groups"]}
    targets = []
    for e in rep["summary"]["explain_716"]:
        k = _gkey(e)
        if k not in groups_by_key:
            raise DiagError(f"explain_716 的 {k} 不在報告 groups 內")
        targets.append((k, {**groups_by_key[k], "reasons": list(e.get("reasons", []))}))
    rng = np.random.default_rng(seed)
    samples: list[dict] = []
    draws: dict[tuple, dict] = {}
    with ScoreStore(db, readonly=True) as store:
        import export_scores as EX
        dv = EX.resolve_data_version(store, db.parent, rep.get("data_version"))
        sha, params = check_report_vs_db(rep, store, dv, reg)
        pops, sid_of, date_of = read_populations(store, dv, [k for k, _ in targets], start, end)
        for k, g in targets:
            reg_lo_hi = idx[k]
            if (None if reg_lo_hi is None else list(reg_lo_hi)) != g.get("registry"):
                raise DiagError(f"{k}：報告登錄區間 {g.get('registry')} ≠ 登錄檔 {reg_lo_hi}")
            dr = draw(pops[k], reg_lo_hi, per_group, rng)
            for name, have, want in (("n", dr["n"], g.get("n")), ("mode_value", dr["mode_value"], g.get("mode_value")),
                                     ("mode_share", dr["mode_share"], g.get("mode_share")),
                                     ("share_at_boundary", dr["share_at_boundary"], g.get("share_at_boundary"))):
                same = (have == want) if (have is None or want is None or name == "n") else abs(have - want) <= 1e-12
                if not same:
                    raise DiagError(f"{k}：db 重數的 {name}={have!r} ≠ 報告 {want!r}；報告與 db 不一致")
            draws[k] = dr
            sid_a, date_a, v = pops[k]
            for st in STRATA:
                for i in dr["picked"][st]:
                    samples.append({"gkey": k, "scope": k[0], "market": k[1], "horizon": k[2], "line": k[3], "coverage": k[4],
                                    "rw": 1 if k[4] == "reweighted" else 0, "stratum": st,
                                    "sid": sid_of[sid_a[i]], "date": date_of[date_a[i]], "db": float(v[i]),
                                    "is_b": bool(dr["bnd_mask"][i]), "is_m": bool(dr["mode_mask"][i])})
            del dr["picked"], dr["bnd_mask"], dr["mode_mask"]
        pops.clear()
        for s in samples:
            if s["scope"] == SCOPE_MARKET and s["sid"] != MARKET_STOCK_ID:
                raise DiagError(f"大盤列的 stock_id 不是 {MARKET_STOCK_ID}：{s['sid']}")
        if samples:
            recompute(Path(cache_dir), features, dv, params, samples, store, quiet=quiet)
    par = parity(samples)
    out_groups = [summarize(k, g, draws[k], [s for s in samples if s["gkey"] == k]) for k, g in targets]
    res = {"schema": 1, "report": str(report), "db": str(db), "data_version": dv, "params_sha": sha,
           "sample": {"start": start, "end": end}, "per_group": per_group, "seed": seed,
           "strata_rule": "達邊界＝距登錄端點 ≤ 0.01；單一值＝四捨五入 6 位＝最常出現值。"
                          "四層 BM（兩者皆是）／B／M／R。母體 ≤ per_group 全取；否則 BM、B、M 各配 per_group//4，R 取餘，"
                          "R 不足回補 BM→B→M。權重＝層母體列數÷層抽樣列數。",
           "u_definition": "u＝direction·(x−c)／(3d)；|u|≥1 ⇔ clip 生效（u≥1 為高分端、u≤−1 為低分端）",
           "parity": par, "groups": out_groups,
           "elapsed_s": round(time.time() - t0, 1), "rss_peak_mib": round(rss_mib(), 1)}
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(res, ensure_ascii=False, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    out.with_suffix(".txt").write_text(as_text(res), encoding="utf-8")
    if keep_samples:
        res["_samples"] = samples
    return res


def _pct(v: float | None) -> str:
    return "—" if v is None else f"{v * 100:.1f}%"


def _w(v: float | None) -> str:
    return "—" if v is None else (f"{v:.0f}" if float(v).is_integer() else f"{v:.3f}")


def as_text(res: dict[str, Any]) -> str:
    p = res["parity"]
    L = [f"§16.5 :716 族組成診斷（裁定 #65 ②）｜樣本 {res['sample']['start']}～{res['sample']['end']}｜"
         f"每組 {res['per_group']} 列、seed {res['seed']}",
         f"db {res['db']}｜data_version {res['data_version']}｜params_sha {res['params_sha']}｜報告 {res['report']}",
         f"parity：{p['rows']} 列全數相符（max |差| {p['max_abs_diff']:.3e} ≤ {p['tol']}）｜"
         f"耗時 {res['elapsed_s']}s｜RSS 峰值 {res['rss_peak_mib']} MiB",
         f"分層：{res['strata_rule']}", f"位置：{res['u_definition']}",
         "比例一律為加權後母體估計；「抽」為原始抽樣筆數。", ""]
    for g in res["groups"]:
        pp = g["population"]
        L.append(f"== {g['scope']} {g['market']} {g['horizon']} 爻{g['line']} {g['coverage']}｜須解釋：{'、'.join(g['reasons'])}")
        L.append(f"母體 {pp['n']:,} 列｜達邊界 {pp['n_boundary']:,}（{_pct(pp['share_at_boundary'])}）｜"
                 f"最常出現值 {pp['mode_value'] if pp['mode_value'] is None else format(pp['mode_value'], '.6f')} "
                 f"{pp['n_mode']:,}（{_pct(pp['mode_share'])}）｜登錄 {g['registry']}")
        L.append("分層（母體/抽/權重）：" + "；".join(
            f"{STRATA_SHORT[s]} {v['population']:,}/{v['sampled']}/{_w(v['weight'])}" for s, v in g["strata"].items()))
        L.append(f"量測結論：{g['conclusion']}")
        L.append("族缺值型態 | 估計列數 | 佔母體 | 型態內達邊界 | 型態內單一值 | 抽(BM/B/M/R)")
        for r in g["patterns"][:TOP_K]:
            ns = r["n_sample"]
            L.append(f"{r['pattern']} | {r['est_rows']:,.0f} | {_pct(r['est_share'])} | {_pct(r['boundary_share_within'])} | "
                     f"{_pct(r['mode_share_within'])} | {'/'.join(str(ns[st]) for st in STRATA)}")
        if len(g["patterns"]) > TOP_K:
            L.append(f"…另 {len(g['patterns']) - TOP_K} 種型態（見 JSON）")
        for title, blk in (("邊界列", g["boundary_rows"]), ("單一值列", g["mode_rows"])):
            if not blk["signatures"]:
                L.append(f"{title}：無")
                continue
            L.append(f"{title}：子指標簽名 | 佔{title} | 抽")
            for r in blk["signatures"][:TOP_K]:
                L.append(f"  {r['key']} | {_pct(r['share'])} | {r['n_sample']}")
            if len(blk["signatures"]) > TOP_K:
                L.append(f"  …另 {len(blk['signatures']) - TOP_K} 種簽名（見 JSON）")
            L.append(f"{title}：在場子指標 | 抽 | clip 生效 | 狀態 | u 最小/中位/最大 | c | d")
            for k, e in blk["subs"].items():
                u = "—" if e["u_min"] is None else f"{e['u_min']:.3f}/{e['u_median']:.3f}/{e['u_max']:.3f}"
                st = "、".join(f"{a}×{b}" for a, b in e["status"].items())
                L.append(f"  {k} | {e['n_sample']} | {_pct(e['clipped_share'])} | {st} | {u} | {e['c']} | {e['d']}")
        L.append("")
    return "\n".join(L) + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="§16.5 :716 被標組的族組成診斷（樣本段寫死為訓練＋驗證段；parity 不符即中止）")
    ap.add_argument("--report", required=True, help="score_stats.py 產出的 runs/stats/report_<TO>.json")
    ap.add_argument("--db", default=str(REPO / "cache" / "scores.db"))
    ap.add_argument("--cache-dir", default=str(REPO / "cache"), help="原料 DB 目錄（同 replay_scores.py）")
    ap.add_argument("--features", default=None, help="預設 <cache-dir>/features.db")
    ap.add_argument("--registry", default=str(SS.REGISTRY))
    ap.add_argument("--out", required=True, help="診斷 JSON 路徑；同名 .txt 一併寫出")
    ap.add_argument("--per-group", type=int, default=DEFAULT_PER_GROUP)
    ap.add_argument("--seed", type=int, default=DEFAULT_SEED)
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args(argv)
    try:
        res = run(Path(args.report), Path(args.db), Path(args.out), cache_dir=Path(args.cache_dir),
                  features=Path(args.features) if args.features else None, registry=Path(args.registry),
                  per_group=args.per_group, seed=args.seed, quiet=args.quiet)
        print(f"== :716 族組成診斷 {len(res['groups'])} 組｜parity {res['parity']['rows']} 列相符｜"
              f"{res['elapsed_s']}s RSS {res['rss_peak_mib']} MiB｜報告 {args.out}")
        return 0
    except Exception as e:  # noqa: BLE001  任何例外一律 rc=2
        print(f"[score_diag716 中止] {type(e).__name__}: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
