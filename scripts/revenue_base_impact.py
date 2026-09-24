#!/usr/bin/env python3
"""營收子指標「沒有最小基期門檻」的影響面量測（唯讀；使用者裁定：先量影響面再決定，`docs/P3-CALIBRATION.md` §29）。

**只量測、不改任何計分程式、不寫 db、不提門檻規則。** 反事實（C）只為量「影響有多大」，只在記憶體內重算。
樣本段寫死＝`SEGMENTS["train"][0]`～`SEGMENTS["valid"][1]`（同 `score_stats.py`，裁定 #64 ①），CLI 不開日期。

## A. 全母體分布（便宜路徑：只讀營收／季報，不 ingest 價量）

母體＝`scores.db` 在樣本段內的**全部個股列**（`scope='stock'`，池內＋池外、兩市場、三期間）。
初爻只吃 `StockInputs` 的五個欄位（`monthly_revenue`／`fundamentals`／`industry_median_3m_yoy`／`industry_revenue_n`／
`is_financial`），所以 A 不重建價量視窗，而是：

1. 與重播**同一支**取數：`ReplaySource.load_fundamentals(trading_dates)` → `FundamentalsBridge.inputs_for(sid, T)`
   （＝重播餵給 `wc.stock_inputs` 的 `extra`；「T 日最新可用月份」由它內部的 `monthly_asof` 決定，本檔不複寫）；
   `is_financial` 取 `universe.is_financial(pool.industry_of(sid))`（同 `replay_step`）。
2. 以這五個欄位建 `StockInputs`，呼叫**真實** `stock.line1_operations`；攔截沿用 `score_diag716.Capture`
   （包 `stock.S_clip`／`stock.sub_result`，只記錄、回傳值不動），讀出每個營收子指標的 x、c、d、direction、clip 與缺值原因。
3. 同一檔同一期間在「五個欄位完全相同」的連續被計分日，初爻輸入相同 → 只算一次、按列數展開（run-length；
   鍵＝市場、金融別、`monthly_revenue` 全序列、`fundamentals` 全部鍵值、產業中位數、產業樣本數）。
4. **全母體 parity（A 自己的守門）**：每一列的初爻分數／`line_1_reweighted`／`line_1_unknown` 必須與 db 相符
   （|差| ≤ 1e-9），任一列不符 → rc=2、不出報告。run-length 鍵的完整性由這道（初爻分數）與 B（子指標 x／缺值原因）
   **共同**守：子指標被 clip 在端點時換了輸入分數也可能不變，只看初爻分數擋不全（自測突變「鍵漏掉 `monthly_revenue`」
   在合成資料上是 B 擋下的）。
5. 分母（基期營收）：本檔以 `stock._ym_shift`／`stock.MONTHS_PER_YEAR` 取與 `revenue_yoy_3m` 相同的月份清單、
   相同的 `sum(...)` 式子算出 num／den，並**逐次斷言** `(num/den − 1)×100` 與真實 `revenue_yoy_3m` 的回傳值**逐位相同**、
   且與攔截到的 x 逐位相同（加速度＝兩組 YoY 相減、產業相對＝YoY − 產業中位數）；分母為 0 的缺值列斷言 den＝0。
   不相符 → rc=2。**單位＝原始資料單位（元，`fundamentals.py` 檔頭）**。

u＝direction·(x−c)／(3d)，c、d、direction 取自該列實際傳給 `S_clip` 的參數（每列用自己 horizon 的 c、d）。
|u| > 1 ⇔ x 在截斷範圍 [c−3d, c+3d] 外、clip 生效（|u| 恰為 1 時不 clip，見 `transform.S_clip`）。

## B. 抽樣 parity（必須；沿用 `score_diag716.py` 的部分重播）

每組（market × horizon）以「任一營收子指標 |u| > 10」切兩層（X＝極端／R＝其餘），X 配 `per_group // 2`（不足全取）、
R 取餘、R 不足回補 X；母體 ≤ `--per-group` 時普查。層內 `numpy.random.default_rng(--seed)` 不放回均勻抽樣；
權重＝層母體列數 ÷ 層抽樣列數。抽到的列走真實重播路徑：自最早交易日逐日 `ReplaySource.read_day` →
`WindowCache.ingest`（不計分）；抽樣日 `wc.stock_inputs(...)`（基本面取 `FundamentalsBridge.provider()`）→ `score_stock`。
攔截 `stock.sub_result`（三個營收子指標收到的 `Ind`／`Missing`）與 `stock.ind_revenue_yoy`／`ind_revenue_accel`
（收到的月數與 d）。斷言：①重算初爻分數與 db 相符（|差| ≤ 1e-9）、`reweighted`／`unknown` 相同；②每個營收子指標的
在場／缺值原因（原因碼＋detail）與 A 相同、x 的 |差| ≤ 1e-9、d 相同。任一不符 → rc=2、不出報告。

## C. 反事實影響（只量不改、只在記憶體內）

對 |u| > K（K ∈ {3, 10, 100}）的列，把該子指標改為 `Missing(denominator_zero, "反事實…")` 後**重跑真實
`line1_operations`**（聚合走 `family_score`／`line_score`，中期族 A 的 12 月新高下限也照原碼套用）——做法是暫時包一層
`stock.sub_result`，只在目標子指標上換掉 `out`。原因碼只是反事實標記：`line_score` 的 coverage 分母只對
`insufficient_history` 特別處理，其餘原因碼（含 `denominator_zero`）聚合路徑相同。情境：三個子指標各自一套，另有
`joint`＝同一列所有 |u| > K 的營收子指標一起改缺。**普查**（run-length 展開後逐列計數，不抽樣、權重皆 1）。

## D. 使用處

以 `ast` 掃 `src/iching/` 全部 .py，列出 `revenue_yoy_3m`／`revenue_yoy_single`／`ind_revenue_yoy`／`ind_revenue_accel`
與字串 `"revenue_yoy"`／`"revenue_accel"`／`"revenue_yoy_vs_industry"` 的出現處與所在函式；出現在 `KNOWN_USES` 以外的
函式 → rc=2（表示有本檔沒涵蓋的使用處，量測不完整）。

## 其他守門

`export_dataset.check_params`（db 是現行碼算的）、`score_stats.check_versions`（model_version 與登錄檔一致）、
以現行 ParamSet＋db 的 window／基本面開關重建參數指紋＝db `params_sha`、db 未開基本面即中止、db 日期須在原料交易日軸內、
db 列的市場須等於 `pool.listed(sid, T)`。rc：0 成功／2 中止（任何守門、parity、例外）。
"""
from __future__ import annotations

import argparse
import ast
import json
import math
import sys
import time
from array import array
from collections import defaultdict
from contextlib import contextmanager
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
from iching.score import stock as STK  # noqa: E402
from iching.score.params import HORIZONS, MARKETS, SCOPE_STOCK, build_params  # noqa: E402
from iching.score.transform import REASON_DENOM_ZERO, Missing  # noqa: E402
from iching.scores_io import ScoreStore  # noqa: E402
from iching.universe import is_financial  # noqa: E402

import score_diag716 as DG  # noqa: E402
import score_stats as SS  # noqa: E402

PARITY_TOL = 1e-9
DEFAULT_PER_GROUP = 1000
DEFAULT_SEED = 20260924
SAMPLE_START, SAMPLE_END = SS.SAMPLE_START, SS.SAMPLE_END
SUBS = ("revenue_yoy", "revenue_accel", "revenue_yoy_vs_industry")
SUB_FAMILY = {"revenue_yoy": "A", "revenue_accel": "A", "revenue_yoy_vs_industry": "C"}
SCENARIOS = (*SUBS, "joint")
U_THRESHOLDS = (1.0, 3.0, 10.0, 100.0)
CF_K = (3.0, 10.0, 100.0)
EXTREME_U = 10.0                      # B 分層與 A「極端列基期」的門檻（量測分組用，不是規則）
U_QS = (0.5, 0.9, 0.99, 0.999)
DEN_QS = (0.0, 0.01, 0.05, 0.25, 0.5, 0.75, 0.95, 1.0)
DELTA_QS = (0.0, 0.05, 0.25, 0.5, 0.75, 0.95, 1.0)
BAND = SS.BAND                        # [45, 55]，兩端含（同 score_stats.group_stats）
STRATA = ("extreme", "rest")
MK_CODE = {m: i for i, m in enumerate(MARKETS)}
H_CODE = {h: i for i, h in enumerate(HORIZONS)}
PROGRESS_EVERY = 100

#: D：營收子指標相關識別字的已知使用處（相對 `src/iching/` 的檔案, 所在函式）。
#: 新增使用處 → 本表沒有 → 中止（量測可能沒涵蓋）。
USE_NAMES = ("revenue_yoy_3m", "revenue_yoy_single", "ind_revenue_yoy", "ind_revenue_accel")
KNOWN_USES: dict[tuple[str, str], str] = {
    ("score/stock.py", "revenue_yoy_single"): "revenue_yoy_3m 的單月包裝（src 內無呼叫者）",
    ("score/stock.py", "ind_revenue_yoy"): "初爻族 A revenue_yoy",
    ("score/stock.py", "ind_revenue_accel"): "初爻族 A revenue_accel",
    ("score/stock.py", "line1_operations"): "初爻（族 A 兩個子指標；中期族 C revenue_yoy_vs_industry 直接呼叫 revenue_yoy_3m）",
    ("fundamentals.py", "<module>"): "import revenue_yoy_3m",
    ("fundamentals.py", "FundamentalsBridge._industry_stats"): "產業中位數（初爻中期族 C 的輸入 industry_median_3m_yoy）",
    ("score/params.py", "_mk_stock"): "參數宣告（c／d／window）",
    ("score/calibrated.py", "<module>"): "校準後 d 值表",
}


class RevBaseError(Exception):
    pass


class ParityError(RevBaseError):
    pass


def rss_mib() -> float:
    return DG.rss_mib()


# ---------------------------------------------------------------------------
# D：使用處掃描
# ---------------------------------------------------------------------------
def usage_scan(src_root: Path = REPO / "src" / "iching") -> list[dict[str, Any]]:
    """回 [{file, function, lines, what, note}]（依所在函式彙整）；出現在 `KNOWN_USES` 以外者 → RevBaseError。"""
    hits: list[dict[str, Any]] = []
    for path in sorted(Path(src_root).rglob("*.py")):
        rel = path.relative_to(src_root).as_posix()
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))

        def visit(node: ast.AST, scope: list[str]) -> None:
            for ch in ast.iter_child_nodes(node):
                if isinstance(ch, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                    visit(ch, [*scope, ch.name])
                    continue
                what = None
                if isinstance(ch, ast.Name) and ch.id in USE_NAMES:
                    what = ch.id
                elif isinstance(ch, ast.Attribute) and ch.attr in USE_NAMES:
                    what = ch.attr
                elif isinstance(ch, ast.Constant) and isinstance(ch.value, str) and ch.value in SUBS:
                    what = repr(ch.value)
                elif isinstance(ch, ast.ImportFrom):
                    for a in ch.names:
                        if a.name in USE_NAMES:
                            hits.append({"file": rel, "function": ".".join(scope) or "<module>", "line": ch.lineno,
                                         "what": f"import {a.name}"})
                if what is not None:
                    hits.append({"file": rel, "function": ".".join(scope) or "<module>", "line": ch.lineno, "what": what})
                visit(ch, scope)
        visit(tree, [])
    unknown = sorted({(h["file"], h["function"]) for h in hits} - set(KNOWN_USES))
    if unknown:
        raise RevBaseError("營收子指標出現在未涵蓋的使用處（量測可能不完整，先確認再更新 KNOWN_USES）："
                           + "、".join(f"{f}:{fn}" for f, fn in unknown))
    by: dict[tuple[str, str], dict[str, set]] = defaultdict(lambda: {"lines": set(), "what": set()})
    for h in hits:
        e = by[(h["file"], h["function"])]
        e["lines"].add(h["line"])
        e["what"].add(h["what"])
    return [{"file": f, "function": fn, "lines": sorted(e["lines"]), "what": sorted(e["what"]), "note": KNOWN_USES[(f, fn)]}
            for (f, fn), e in sorted(by.items())]


# ---------------------------------------------------------------------------
# 守門
# ---------------------------------------------------------------------------
def check_db(store: ScoreStore, dv: str, reg: dict[str, Any]) -> tuple[str, dict]:
    import export_dataset as ED
    sha, params = ED.check_params(store, dv)                   # db 不是現行碼算的就拋錯
    SS.check_versions(store, dv, reg)                           # model_version 與登錄檔一致
    if not bool(params.get("fundamentals", True)):
        raise RevBaseError("db 未啟用基本面（params.fundamentals=False）：營收子指標恆缺，本量測無對象")
    mv = {m: build_params(m).model_version() for m in MARKETS}
    payload = build_params_payload(mv, int(params["window"]), RS.CrossDayState().adv,
                                   fundamentals=bool(params.get("fundamentals", True)))
    if params_fingerprint(payload) != sha:
        raise RevBaseError(f"以現行 ParamSet／window={params['window']} 重建的參數指紋 {params_fingerprint(payload)} ≠ db {sha}")
    return sha, params


# ---------------------------------------------------------------------------
# 分母（基期營收）：與 revenue_yoy_3m 同月份清單、同式子
# ---------------------------------------------------------------------------
def base_sums(rev: dict[str, float], latest: str, offset: int, months: int) -> tuple[float, float] | None:
    """(num, den)；月份不齊 → None。月份清單與加總式子逐字同 `stock.revenue_yoy_3m`（呼叫端逐次核對結果逐位相同）。"""
    ms = [STK._ym_shift(latest, offset + i) for i in range(months)]
    ly = [STK._ym_shift(m, STK.MONTHS_PER_YEAR) for m in ms]
    if any(m not in rev for m in ms + ly):
        return None
    num = sum(rev[m] for m in ms)
    den = sum(rev[m] for m in ly)
    return num, den


def _yoy_of(ns: tuple[float, float]) -> float:
    return (ns[0] / ns[1] - 1.0) * 100.0


def _check_base(tag: str, ns: tuple[float, float] | None, real: Any) -> None:
    """`ns`（本檔的 num/den）與真實 `revenue_yoy_3m` 的回傳 `real` 必須一致。"""
    if isinstance(real, Missing):
        if real.reason == REASON_DENOM_ZERO:
            if ns is None or ns[1] != 0:
                raise RevBaseError(f"{tag}：revenue_yoy_3m 回 denominator_zero，但本檔的基期 {ns} 不是 0")
        elif ns is not None:
            raise RevBaseError(f"{tag}：revenue_yoy_3m 回缺值 {real}，但本檔算得出基期 {ns}")
        return
    if ns is None or ns[1] == 0 or _yoy_of(ns) != real:
        raise RevBaseError(f"{tag}：本檔 num/den={ns} 推得的 YoY ≠ revenue_yoy_3m 回傳 {real!r}")


# ---------------------------------------------------------------------------
# 初爻：真實 line1_operations ＋攔截
# ---------------------------------------------------------------------------
class CFSwitch:
    """反事實：`targets` 內的子指標在 `sub_result` 入口換成 Missing；targets 空時完全透明。"""

    def __init__(self) -> None:
        self.targets: frozenset[str] = frozenset()
        self.detail = ""

    def wrap(self, orig):
        def sub(indicator_id, out, *a, **k):
            if indicator_id in self.targets:
                out = Missing(REASON_DENOM_ZERO, self.detail)
            return orig(indicator_id, out, *a, **k)
        return sub


def line1_inputs(mk: str, sid: str, T: str, industry: str | None, extra: dict) -> STK.StockInputs:
    """A 路徑的 StockInputs：只填初爻會讀的五個欄位（其餘預設 None）。parity 守門擋「初爻其實還讀了別的」。"""
    return STK.StockInputs(market=mk, stock_id=sid, tpe_date=T, industry=industry, is_financial=is_financial(industry),
                           monthly_revenue=extra.get("monthly_revenue"),
                           industry_median_3m_yoy=extra.get("industry_median_3m_yoy"),
                           industry_revenue_n=extra.get("industry_revenue_n"), fundamentals=extra.get("fundamentals"))


def _sub_windows(ps, h: str) -> dict[str, int]:
    w = {"revenue_yoy": ps.get(SCOPE_STOCK, h, "1", "A", "revenue_yoy").window,
         "revenue_accel": ps.get(SCOPE_STOCK, h, "1", "A", "revenue_accel").window}
    if h == "mid":
        w["revenue_yoy_vs_industry"] = ps.get(SCOPE_STOCK, h, "1", "C", "revenue_yoy_vs_industry").window
    return {k: int(v) for k, v in w.items()}


def line1_detail(si: STK.StockInputs, ps, h: str, cap: DG.Capture) -> dict[str, Any]:
    """真實 `line1_operations` → 初爻分數＋三個營收子指標的 x／c／d／u／clip／缺值原因／基期。"""
    cap.reset()
    cap.active = True
    try:
        lr = STK.line1_operations(si, ps, h)
    finally:
        cap.active = False
    rev = {ym: float(v) for ym, v in (si.monthly_revenue or [])}
    latest = max(rev) if rev else None
    win = _sub_windows(ps, h)
    subs: dict[str, dict[str, Any]] = {}
    for f in lr.families:
        for sr in f.subs:
            if sr.indicator_id not in SUBS:
                continue
            iid = sr.indicator_id
            if f.family != SUB_FAMILY[iid] or iid in subs:
                raise RevBaseError(f"{iid} 出現在族 {f.family}（預期 {SUB_FAMILY[iid]}）或重複出現")
            rec: dict[str, Any] = {"pres": sr.score is not None, "reason": None, "x": None, "u": None, "c": None, "d": None,
                                   "clipped": bool(sr.clipped), "den": (None, None)}
            if sr.score is None:
                m = sr.missing
                rec["reason"] = f"{m.reason}（{m.detail}）" if m is not None else "?"
            else:
                info = cap.info.get(id(sr))
                if info is None or info[1].get("sclip_match") != "direct":
                    raise RevBaseError(f"{iid}：攔截不到直接的 S_clip 呼叫（{info[1] if info else None}）")
                x, c, d, dr = info[1]["sclip"]
                if x != sr.x:
                    raise RevBaseError(f"{iid}：S_clip 收到的 x {x!r} ≠ SubResult.x {sr.x!r}")
                rec.update(x=x, c=c, d=d, u=dr * (x - c) / (3.0 * d))
            # 基期：與 revenue_yoy_3m 同月份清單，並逐次核對
            if latest is not None:
                w = win[iid]
                if iid == "revenue_accel":
                    na, nb = base_sums(rev, latest, 0, w), base_sums(rev, latest, w, w)
                    ra, rb = STK.revenue_yoy_3m(rev, latest, 0, w), STK.revenue_yoy_3m(rev, latest, w, w)
                    _check_base(f"{iid} 近組", na, ra)
                    _check_base(f"{iid} 前組", nb, rb)              # 近組缺值時 den_prev 照樣記下，所以一律核對
                    if rec["pres"] and ra - rb != rec["x"]:
                        raise RevBaseError(f"{iid}：兩組 YoY 相減 {ra - rb!r} ≠ 攔截到的 x {rec['x']!r}")
                    rec["den"] = (None if na is None else na[1], None if nb is None else nb[1])
                else:
                    ns = base_sums(rev, latest, 0, w)
                    ry = STK.revenue_yoy_3m(rev, latest, 0, w)
                    _check_base(iid, ns, ry)
                    if rec["pres"]:
                        want = ry if iid == "revenue_yoy" else ry - si.industry_median_3m_yoy
                        if want != rec["x"]:
                            raise RevBaseError(f"{iid}：由 revenue_yoy_3m 推得的 x {want!r} ≠ 攔截到的 x {rec['x']!r}")
                    rec["den"] = (None if ns is None else ns[1], None)
            subs[iid] = rec
    want_ids = set(win)
    if set(subs) != want_ids:
        raise RevBaseError(f"{h} 初爻的營收子指標 {sorted(subs)} ≠ 預期 {sorted(want_ids)}")
    return {"score": lr.score, "rw": bool(lr.reweighted), "unknown": bool(lr.unknown), "subs": subs}


@contextmanager
def instrumented():
    """A 的攔截：反事實開關包在 `stock.sub_result` 最內層、`score_diag716.Capture` 包在外層；離開時原樣還原。"""
    sw = CFSwitch()
    orig_sub = STK.sub_result
    STK.sub_result = sw.wrap(orig_sub)
    saved: list = []
    try:
        cap, saved = DG.install_capture()
        yield cap, sw
    finally:
        DG.uninstall_capture(saved)
        STK.sub_result = orig_sub


def line1_counterfactual(si: STK.StockInputs, ps, h: str, sw: CFSwitch, targets: frozenset[str], K: float):
    sw.targets, sw.detail = targets, f"反事實：|u|>{K:g} 改缺（非真實缺值）"
    try:
        return STK.line1_operations(si, ps, h)
    finally:
        sw.targets, sw.detail = frozenset(), ""


# ---------------------------------------------------------------------------
# A：母體讀取
# ---------------------------------------------------------------------------
def read_rows(store: ScoreStore, dv: str, start: str, end: str) -> dict[str, Any]:
    """樣本段內全部個股列 → 緊湊陣列（依日期排序）。`l1` 的 NULL 以 NaN 表示。"""
    sql = ("SELECT s.market, s.horizon, s.stock_id, s.date, s.in_rank_pool, s.line_1, s.line_1_reweighted, s.line_1_unknown "
           "FROM scores s JOIN versions v ON v.version_id = s.version_id "
           "WHERE v.data_version = ? AND s.scope = ? AND +s.date >= ? AND +s.date <= ?")
    sid_of: list[str] = []
    sid_ix: dict[str, int] = {}
    date_of: list[str] = []
    date_ix: dict[str, int] = {}
    a_sid, a_date = array("i"), array("i")
    a_mk, a_h, a_pool, a_rw, a_unk = array("b"), array("b"), array("b"), array("b"), array("b")
    a_l1 = array("d")
    for mk, h, sid, d, pool, l1, rw, unk in store.conn.execute(sql, (dv, SCOPE_STOCK, start, end)):
        si = sid_ix.get(sid)
        if si is None:
            si = sid_ix[sid] = len(sid_of)
            sid_of.append(sid)
        di = date_ix.get(d)
        if di is None:
            di = date_ix[d] = len(date_of)
            date_of.append(d)
        a_sid.append(si)
        a_date.append(di)
        a_mk.append(MK_CODE[mk])
        a_h.append(H_CODE[h])
        a_pool.append(-1 if pool is None else int(pool))
        a_l1.append(math.nan if l1 is None else float(l1))
        a_rw.append(-1 if rw is None else int(rw))
        a_unk.append(-1 if unk is None else int(unk))
    n = len(a_sid)
    if n == 0:
        raise RevBaseError(f"樣本段 {start}～{end} 在 db 內沒有任何個股列")
    dates_sorted = sorted(date_of)
    pos = {d: i for i, d in enumerate(dates_sorted)}
    rank = np.array([pos[d] for d in date_of], dtype=np.int32)
    date_rank = rank[np.frombuffer(a_date, dtype=np.int32)]
    order = np.argsort(date_rank, kind="stable")
    return {"n": n, "sid_of": sid_of, "dates": dates_sorted,
            "sid": np.frombuffer(a_sid, dtype=np.int32)[order], "date": date_rank[order],
            "mk": np.frombuffer(a_mk, dtype=np.int8)[order], "h": np.frombuffer(a_h, dtype=np.int8)[order],
            "pool": np.frombuffer(a_pool, dtype=np.int8)[order], "l1": np.frombuffer(a_l1, dtype="d")[order],
            "rw": np.frombuffer(a_rw, dtype=np.int8)[order], "unk": np.frombuffer(a_unk, dtype=np.int8)[order]}


# ---------------------------------------------------------------------------
# run 表（run-length：同一 (檔, 期間) 連續輸入相同的列共用一筆）
# ---------------------------------------------------------------------------
class Runs:
    def __init__(self) -> None:
        self.mk, self.h, self.sid = array("b"), array("b"), array("i")
        self.score, self.rw, self.unk = array("d"), array("b"), array("b")
        self.pres = {s: array("b") for s in SUBS}             # 1 在場／0 缺／−1 不適用（非中期的族 C）
        self.reason = {s: array("h") for s in SUBS}           # 原因索引（−1＝在場或不適用）
        self.x = {s: array("d") for s in SUBS}
        self.u = {s: array("d") for s in SUBS}
        self.c = {s: array("d") for s in SUBS}
        self.d = {s: array("d") for s in SUBS}
        self.clip = {s: array("b") for s in SUBS}
        self.den1 = {s: array("d") for s in SUBS}
        self.den2 = {s: array("d") for s in SUBS}
        self.cf_score = {(sc, k): array("d") for sc in SCENARIOS for k in CF_K}   # NaN＝未知或未受影響
        self.cf_rw = {(sc, k): array("b") for sc in SCENARIOS for k in CF_K}      # −1＝未受影響
        self.reasons: list[str] = []
        self._reason_ix: dict[str, int] = {}

    def __len__(self) -> int:
        return len(self.sid)

    def add(self, mk: int, h: int, sid: int, det: dict[str, Any], cf: dict[tuple[str, float], tuple[float, int]]) -> int:
        nan = math.nan
        self.mk.append(mk)
        self.h.append(h)
        self.sid.append(sid)
        self.score.append(nan if det["score"] is None else float(det["score"]))
        self.rw.append(int(det["rw"]))
        self.unk.append(int(det["unknown"]))
        for s in SUBS:
            r = det["subs"].get(s)
            if r is None:
                self.pres[s].append(-1)
                self.reason[s].append(-1)
                vals = (nan,) * 6
                clip = 0
            else:
                self.pres[s].append(1 if r["pres"] else 0)
                if r["reason"] is None:
                    self.reason[s].append(-1)
                else:
                    ix = self._reason_ix.get(r["reason"])
                    if ix is None:
                        ix = self._reason_ix[r["reason"]] = len(self.reasons)
                        self.reasons.append(r["reason"])
                    self.reason[s].append(ix)
                d1, d2 = r["den"]
                vals = tuple(nan if v is None else float(v) for v in (r["x"], r["u"], r["c"], r["d"], d1, d2))
                clip = int(r["clipped"])
            for arr, v in zip((self.x[s], self.u[s], self.c[s], self.d[s], self.den1[s], self.den2[s]), vals):
                arr.append(v)
            self.clip[s].append(clip)
        for key in self.cf_score:
            v = cf.get(key)
            self.cf_score[key].append(nan if v is None or v[0] is None else float(v[0]))
            self.cf_rw[key].append(-1 if v is None else int(v[1]))
        return len(self.sid) - 1

    def np(self) -> dict[str, Any]:
        f = lambda a, t: np.frombuffer(a, dtype=t) if len(a) else np.zeros(0, dtype=t)  # noqa: E731
        return {"mk": f(self.mk, np.int8), "h": f(self.h, np.int8), "sid": f(self.sid, np.int32),
                "score": f(self.score, "d"), "rw": f(self.rw, np.int8), "unk": f(self.unk, np.int8),
                **{k: {s: f(getattr(self, k)[s], t) for s in SUBS}
                   for k, t in (("pres", np.int8), ("reason", np.int16), ("x", "d"), ("u", "d"), ("c", "d"), ("d", "d"),
                                ("clip", np.int8), ("den1", "d"), ("den2", "d"))},
                "cf_score": {k: f(v, "d") for k, v in self.cf_score.items()},
                "cf_rw": {k: f(v, np.int8) for k, v in self.cf_rw.items()},
                "reasons": list(self.reasons)}


def counterfactuals(det: dict[str, Any], si, ps, h: str, sw: CFSwitch) -> dict[tuple[str, float], tuple[float | None, int]]:
    """對每個 K、每個情境：受影響 → (反事實初爻分數, reweighted)。相同目標集合只算一次。"""
    out: dict[tuple[str, float], tuple[float | None, int]] = {}
    memo: dict[frozenset[str], tuple[float | None, int]] = {}
    for K in CF_K:
        over = frozenset(s for s, r in det["subs"].items() if r["pres"] and abs(r["u"]) > K)
        if not over:
            continue
        for sc in SCENARIOS:
            tg = over if sc == "joint" else (frozenset({sc}) if sc in over else frozenset())
            if not tg:
                continue
            if tg not in memo:
                lr = line1_counterfactual(si, ps, h, sw, tg, K)
                memo[tg] = (lr.score, int(lr.reweighted))
            out[(sc, K)] = memo[tg]
    return out


def run_a(src, bridge, rows: dict[str, Any], ps: dict, cap: DG.Capture, sw: CFSwitch, *, quiet: bool) -> tuple[Runs, np.ndarray]:
    """逐日走 db 列：同一 (檔, 期間) 輸入鍵沒變就沿用上一筆 run，否則以真實 line1 重算並做反事實。回 (runs, run_of_row)。"""
    trading = set(src.trading_dates())
    bad_days = [d for d in rows["dates"] if d not in trading]
    if bad_days:
        raise RevBaseError(f"db 日期不在原料交易日軸：{bad_days[:3]}")
    runs = Runs()
    n = rows["n"]
    run_of_row = np.empty(n, dtype=np.int32)
    sid_a, date_a, mk_a, h_a = rows["sid"], rows["date"], rows["mk"], rows["h"]
    bounds = np.flatnonzero(np.diff(date_a)) + 1
    starts = np.concatenate(([0], bounds))
    ends = np.concatenate((bounds, [n]))
    prev: dict[tuple[int, int], tuple[Any, int]] = {}         # (sid, h) → (輸入鍵, run id)
    t0 = time.time()
    for n_day, (a, b) in enumerate(zip(starts.tolist(), ends.tolist()), start=1):
        T = rows["dates"][int(date_a[a])]
        day: dict[int, tuple[Any, dict, str | None]] = {}
        for i, si_ix, mk_c, h_c in zip(range(a, b), sid_a[a:b].tolist(), mk_a[a:b].tolist(), h_a[a:b].tolist()):
            ent = day.get(si_ix)
            mk = MARKETS[mk_c]
            if ent is None:
                sid = rows["sid_of"][si_ix]
                listed = src.pool.listed(sid, T)
                extra = bridge.inputs_for(sid, T)
                ind = src.pool.industry_of(sid)
                mon, fund = extra.get("monthly_revenue"), extra.get("fundamentals")
                key = (listed, is_financial(ind), tuple(mon) if mon else None,
                       tuple(sorted(fund.items())) if fund else None,
                       extra.get("industry_median_3m_yoy"), extra.get("industry_revenue_n"))
                ent = day[si_ix] = (key, extra, ind)
            key, extra, ind = ent
            if key[0] != mk:                                   # 逐列：db 列的市場＝重播的 pool.listed(sid, T)
                raise RevBaseError(f"{rows['sid_of'][si_ix]} {T}：pool.listed={key[0]} ≠ db 列市場 {mk}")
            p = prev.get((si_ix, h_c))
            if p is not None and p[0] == key:
                run_of_row[i] = p[1]
                continue
            h = HORIZONS[h_c]
            si = line1_inputs(mk, rows["sid_of"][si_ix], T, ind, extra)
            det = line1_detail(si, ps[mk], h, cap)
            cf = counterfactuals(det, si, ps[mk], h, sw)
            rid = runs.add(mk_c, h_c, si_ix, det, cf)
            prev[(si_ix, h_c)] = (key, rid)
            run_of_row[i] = rid
        if not quiet and n_day % PROGRESS_EVERY == 0:
            print(f"  A {n_day} 日（{T}） run {len(runs):,} {time.time() - t0:.0f}s RSS {rss_mib():.0f} MiB", flush=True)
    return runs, run_of_row


def parity_a(rows: dict[str, Any], R: dict[str, Any], run_of_row: np.ndarray) -> dict[str, Any]:
    """全母體：每列的 A 初爻分數／reweighted／unknown 必須與 db 相符。"""
    got = R["score"][run_of_row]
    db = rows["l1"]
    both_nan = np.isnan(got) & np.isnan(db)
    diff = np.where(both_nan, 0.0, np.abs(got - db))
    diff = np.where(np.isnan(diff), np.inf, diff)
    bad = (diff > PARITY_TOL) | (R["rw"][run_of_row] != rows["rw"]) | (R["unk"][run_of_row] != rows["unk"])
    if bad.any():
        idx = np.flatnonzero(bad)
        lines = []
        for i in idx[:8].tolist():
            lines.append(f"  {MARKETS[rows['mk'][i]]} {HORIZONS[rows['h'][i]]} {rows['sid_of'][rows['sid'][i]]} "
                         f"{rows['dates'][rows['date'][i]]} db={rows['l1'][i]!r}/rw{rows['rw'][i]}/unk{rows['unk'][i]} "
                         f"A={got[i]!r}/rw{R['rw'][run_of_row[i]]}/unk{R['unk'][run_of_row[i]]}")
        raise ParityError(f"A 全母體 parity 不符 {idx.size:,}／{rows['n']:,} 列（容許 {PARITY_TOL}），不產出報告：\n" + "\n".join(lines))
    finite = diff[np.isfinite(diff)]
    return {"rows": int(rows["n"]), "runs": int(R["score"].size), "max_abs_diff": float(finite.max()) if finite.size else 0.0,
            "tol": PARITY_TOL}


# ---------------------------------------------------------------------------
# 加權分位數（＝ np.quantile(np.repeat(v, w), q)，線性內插）
# ---------------------------------------------------------------------------
def wquantiles(v: np.ndarray, w: np.ndarray, qs) -> list[float | None]:
    v = np.asarray(v, dtype="d")
    w = np.asarray(w, dtype=np.int64)
    keep = w > 0
    v, w = v[keep], w[keep]
    if v.size == 0:
        return [None for _ in qs]
    o = np.argsort(v, kind="stable")
    v, cw = v[o], np.cumsum(w[o])
    N = int(cw[-1])
    out: list[float | None] = []
    for q in qs:
        p = q * (N - 1)
        lo = int(math.floor(p))
        hi = min(lo + 1, N - 1)
        a = v[int(np.searchsorted(cw, lo, side="right"))]
        b = v[int(np.searchsorted(cw, hi, side="right"))]
        out.append(float(a + (b - a) * (p - lo)))
    return out


def group_wmedian(keys: np.ndarray, v: np.ndarray, w: np.ndarray) -> dict[int, float | None]:
    """每個 key 的列加權中位數（＝該 key 的列展開後 `np.quantile(…, 0.5)`）。"""
    out: dict[int, float | None] = {}
    if keys.size == 0:
        return out
    o = np.argsort(keys, kind="stable")
    k, v, w = keys[o], v[o], w[o]
    cut = np.flatnonzero(np.diff(k)) + 1
    for a, b in zip(np.concatenate(([0], cut)).tolist(), np.concatenate((cut, [k.size])).tolist()):
        out[int(k[a])] = wquantiles(v[a:b], w[a:b], (0.5,))[0]
    return out


def _q(v, w, qs, names=None) -> dict[str, float | None]:
    vals = wquantiles(v, w, qs)
    names = names or [_qname(q) for q in qs]
    return dict(zip(names, vals))


def _qname(q: float) -> str:
    if q == 0.0:
        return "min"
    if q == 1.0:
        return "max"
    s = f"{q * 100:.1f}".rstrip("0").rstrip(".")
    return f"p{s}"


# ---------------------------------------------------------------------------
# A／C 彙總
# ---------------------------------------------------------------------------
def _groups(R: dict[str, Any]):
    for m in MARKETS:
        for h in HORIZONS:
            yield m, h, (R["mk"] == MK_CODE[m]) & (R["h"] == H_CODE[h])


def summarize_a(rows: dict[str, Any], R: dict[str, Any], w_run: np.ndarray, pool_run: np.ndarray) -> list[dict[str, Any]]:
    out = []
    for m, h, gm in _groups(R):
        n_rows = int(w_run[gm].sum())
        n_pool = int(pool_run[gm].sum())
        for s in SUBS:
            pres = R["pres"][s]
            if not (gm & (pres >= 0)).any():
                continue
            g = gm & (pres >= 0)
            miss: dict[str, int] = defaultdict(int)
            for ix, cnt in zip(R["reason"][s][g & (pres == 0)].tolist(), w_run[g & (pres == 0)].tolist()):
                miss[R["reasons"][ix]] += int(cnt)
            p = g & (pres == 1)
            n_p = int(w_run[p].sum())
            au = np.abs(R["u"][s][p])
            wp = w_run[p]
            thr = {}
            for t in U_THRESHOLDS:
                c = int(wp[au > t].sum())
                thr[f"{t:g}"] = {"rows": c, "share_of_rows": c / n_rows if n_rows else None,
                                 "share_of_present": c / n_p if n_p else None}
            ext = p & (np.abs(R["u"][s]) > EXTREME_U)
            base = {}
            for tag, col in (("den", "den1"), ("den_prev", "den2")):
                if s != "revenue_accel" and tag == "den_prev":
                    continue
                dv = R[col][s]
                have = g & ~np.isnan(dv)
                # 該檔在本組（market × horizon）樣本內、基期可算列的**列加權**中位數
                med = group_wmedian(R["sid"][have], dv[have], w_run[have])
                e = ext & ~np.isnan(dv)
                ratio_v, ratio_w, nonpos = [], [], 0
                for v, sid, wt in zip(dv[e].tolist(), R["sid"][e].tolist(), w_run[e].tolist()):
                    md = med.get(sid)
                    if md is None or md <= 0:
                        nonpos += int(wt)
                        continue
                    ratio_v.append(v / md)
                    ratio_w.append(wt)
                base[tag] = {"extreme_rows": int(w_run[e].sum()), "extreme": _q(dv[e], w_run[e], DEN_QS),
                             "all_present": _q(dv[p & ~np.isnan(dv)], w_run[p & ~np.isnan(dv)], DEN_QS),
                             "ratio_to_own_median": _q(np.array(ratio_v), np.array(ratio_w, dtype=np.int64), DEN_QS),
                             "ratio_undefined_rows": nonpos}
            out.append({
                "market": m, "horizon": h, "sub": s, "rows": n_rows, "rows_in_pool": n_pool, "rows_out_pool": n_rows - n_pool,
                "rows_applicable": int(w_run[g].sum()), "present": n_p, "missing": dict(sorted(miss.items())),
                "clipped_flag_rows": int(w_run[p & (R["clip"][s] == 1)].sum()),
                "abs_u": _q(au, wp, (*U_QS, 1.0)),
                "u_min": float(R["u"][s][p].min()) if n_p else None, "u_max": float(R["u"][s][p].max()) if n_p else None,
                "abs_u_over": thr, "c": sorted({float(v) for v in R["c"][s][p].tolist()})[:3],
                "d": sorted({float(v) for v in R["d"][s][p].tolist()})[:3], "base_revenue_yuan": base})
    return out


def summarize_c(R: dict[str, Any], w_run: np.ndarray) -> list[dict[str, Any]]:
    out = []
    lo, hi = BAND
    for m, h, gm in _groups(R):
        n_rows = int(w_run[gm].sum())
        for sc in SCENARIOS:
            if sc == "revenue_yoy_vs_industry" and h != "mid":
                continue
            for K in CF_K:
                aff = gm & (R["cf_rw"][(sc, K)] >= 0)
                wa = w_run[aff]
                n_aff = int(wa.sum())
                orig, cf = R["score"][aff], R["cf_score"][(sc, K)][aff]
                o_rw, c_rw = R["rw"][aff], R["cf_rw"][(sc, K)][aff]
                known = ~np.isnan(orig) & ~np.isnan(cf)
                delta = cf[known] - orig[known]
                wk = wa[known]
                flip_dn = known & (orig >= 50.0) & (cf < 50.0)
                flip_up = known & (orig < 50.0) & (cf >= 50.0)
                o_in = (orig >= lo) & (orig <= hi)
                c_in = (cf >= lo) & (cf <= hi)
                s = lambda msk: int(wa[msk].sum())  # noqa: E731
                out.append({
                    "market": m, "horizon": h, "scenario": sc, "K": K, "group_rows": n_rows,
                    "affected_rows": n_aff, "affected_share": n_aff / n_rows if n_rows else None,
                    "orig_unknown_rows": s(np.isnan(orig)),
                    "became_unknown_rows": s(~np.isnan(orig) & np.isnan(cf)),
                    "delta": _q(delta, wk, DELTA_QS), "abs_delta": _q(np.abs(delta), wk, (0.5, 0.9, 1.0)),
                    "flip_rows": s(flip_dn | flip_up), "flip_yang_to_yin": s(flip_dn), "flip_yin_to_yang": s(flip_up),
                    "flip_share_of_affected": s(flip_dn | flip_up) / n_aff if n_aff else None,
                    "band_enter_rows": s(known & ~o_in & c_in), "band_exit_rows": s(known & o_in & ~c_in),
                    "orig_in_band_rows": s(~np.isnan(orig) & o_in),
                    "reweighted_0_to_1_rows": s((o_rw == 0) & (c_rw == 1)),
                })
    return out


# ---------------------------------------------------------------------------
# B：分層抽樣＋部分重播
# ---------------------------------------------------------------------------
def allocate(sizes: dict[str, int], total: int) -> dict[str, int]:
    """X 配 total//2（不足全取），R 取餘，R 不足回補 X。母體 ≤ total → 全取。"""
    if total < len(STRATA):
        raise RevBaseError(f"--per-group 至少 {len(STRATA)}（得 {total}）")
    if sum(sizes.values()) <= total:
        return dict(sizes)
    n = {"extreme": min(sizes["extreme"], total // 2)}
    n["rest"] = min(sizes["rest"], total - n["extreme"])
    n["extreme"] += min(total - n["extreme"] - n["rest"], sizes["extreme"] - n["extreme"])
    return n


def draw_b(rows: dict[str, Any], R: dict[str, Any], run_of_row: np.ndarray, per_group: int,
           rng: np.random.Generator) -> tuple[list[dict], list[dict]]:
    ext_run = np.zeros(R["score"].size, dtype=bool)
    for s in SUBS:
        ext_run |= (R["pres"][s] == 1) & (np.abs(R["u"][s]) > EXTREME_U)
    ext_row = ext_run[run_of_row]
    samples, strata = [], []
    for m in MARKETS:
        for h in HORIZONS:
            gm = (rows["mk"] == MK_CODE[m]) & (rows["h"] == H_CODE[h])
            idx = {"extreme": np.flatnonzero(gm & ext_row), "rest": np.flatnonzero(gm & ~ext_row)}
            sizes = {k: int(v.size) for k, v in idx.items()}
            if not sum(sizes.values()):
                continue
            alloc = allocate(sizes, per_group)
            st = {"market": m, "horizon": h}
            for k in STRATA:
                pick = idx[k] if alloc[k] >= sizes[k] else np.sort(rng.choice(idx[k], size=alloc[k], replace=False))
                st[k] = {"population": sizes[k], "sampled": int(pick.size),
                         "weight": (sizes[k] / pick.size) if pick.size else None}
                for i in pick.tolist():
                    samples.append({"market": m, "horizon": h, "stratum": k, "row": i, "run": int(run_of_row[i]),
                                    "sid": rows["sid_of"][rows["sid"][i]], "date": rows["dates"][rows["date"][i]],
                                    "db": float(rows["l1"][i]), "rw": int(rows["rw"][i]), "unk": int(rows["unk"][i])})
            strata.append(st)
    return samples, strata


class BCapture:
    """B：記錄真實計分時三個營收子指標 `sub_result` 收到的 out，以及 `ind_revenue_*` 收到的 (months, d)。"""

    def __init__(self) -> None:
        self.active = False
        self.subs: dict[str, list[Any]] = defaultdict(list)
        self.args: dict[str, list[tuple[int, float]]] = defaultdict(list)

    def reset(self) -> None:
        self.subs = defaultdict(list)
        self.args = defaultdict(list)

    def install(self):
        saved = []

        def w_sub(orig):
            def sub(indicator_id, out, *a, **k):
                if self.active and indicator_id in SUBS:
                    self.subs[indicator_id].append(out)
                return orig(indicator_id, out, *a, **k)
            return sub

        def w_ind(name, orig):
            def ind(rev, latest, months, d):
                if self.active:
                    self.args[name].append((int(months), float(d)))
                return orig(rev, latest, months, d)
            return ind

        for name, wrap in (("sub_result", w_sub), ("ind_revenue_yoy", lambda o: w_ind("revenue_yoy", o)),
                           ("ind_revenue_accel", lambda o: w_ind("revenue_accel", o))):
            orig = getattr(STK, name)
            saved.append((name, orig))
            setattr(STK, name, wrap(orig))
        return saved

    @staticmethod
    def uninstall(saved) -> None:
        for name, orig in reversed(saved):
            setattr(STK, name, orig)


def _missing_text(m: Missing) -> str:
    return f"{m.reason}（{m.detail}）"


def run_b(src, bridge, samples: list[dict], R: dict[str, Any], ps: dict, window: int, *, quiet: bool) -> dict[str, Any]:
    if not samples:
        return {"rows": 0, "max_abs_diff": 0.0, "max_abs_x_diff": 0.0, "tol": PARITY_TOL}
    by_date: dict[str, list[dict]] = defaultdict(list)
    for s in samples:
        by_date[s["date"]].append(s)
    last = max(by_date)
    provider = bridge.provider()
    bc = BCapture()
    saved = bc.install()
    bad: list[str] = []
    max_diff = max_x = 0.0
    n_seen = 0
    try:
        wc = RS.WindowCache(src.pool, src.factors, window=window)
        empty = RS.CrossDayState()
        t0 = time.time()
        for n_day, T in enumerate(src.trading_dates(), start=1):
            if T > last:
                break
            wc.ingest(src.read_day(T))
            todays = by_date.get(T)
            if todays:
                today = set(wc.stock_ids_today())
                for s in sorted(todays, key=lambda x: (x["sid"], x["horizon"])):
                    n_seen += 1
                    tag = f"{s['market']} {s['horizon']} {s['sid']} {T}"
                    sid, h = s["sid"], s["horizon"]
                    mk = wc.pool.listed(sid, T)
                    if sid not in today or mk != s["market"]:
                        bad.append(f"  {tag}：重算時該檔當日不在計分名單或市場不符（listed={mk}）")
                        continue
                    extra = provider(sid, T)
                    si = wc.stock_inputs(sid, h, T, empty, None, is_financial=is_financial(wc.pool.industry_of(sid)),
                                         monthly_revenue=extra.get("monthly_revenue"),
                                         industry_median_3m_yoy=extra.get("industry_median_3m_yoy"),
                                         industry_revenue_n=extra.get("industry_revenue_n"),
                                         fundamentals=extra.get("fundamentals"))
                    bc.reset()
                    bc.active = True
                    try:
                        ss = STK.score_stock(si, ps[mk], h)
                    finally:
                        bc.active = False
                    lr = ss.lines["1"]
                    got = math.nan if lr.score is None else lr.score
                    if math.isnan(got) != math.isnan(s["db"]):
                        bad.append(f"  {tag}：初爻 db={s['db']!r} 重算={lr.score!r}")
                    elif not math.isnan(got):
                        dd = abs(got - s["db"])
                        max_diff = max(max_diff, dd)
                        if not dd <= PARITY_TOL:
                            bad.append(f"  {tag}：初爻 |差|={dd:.3e}")
                    if int(lr.reweighted) != s["rw"] or int(lr.unknown) != s["unk"]:
                        bad.append(f"  {tag}：reweighted/unknown 重算 {int(lr.reweighted)}/{int(lr.unknown)} ≠ db {s['rw']}/{s['unk']}")
                    # 子指標：B 真實計分收到的 vs A
                    rid = s["run"]
                    for sub in SUBS:
                        a_pres = int(R["pres"][sub][rid])
                        outs = bc.subs.get(sub, [])
                        if a_pres == -1:
                            if outs:
                                bad.append(f"  {tag}：{sub} A 判不適用、真實計分卻出現")
                            continue
                        if len(outs) != 1:
                            bad.append(f"  {tag}：{sub} 真實計分 sub_result 呼叫 {len(outs)} 次（預期 1）")
                            continue
                        out = outs[0]
                        if isinstance(out, Missing):
                            a_reason = R["reasons"][R["reason"][sub][rid]] if a_pres == 0 else None
                            if a_pres != 0 or a_reason != _missing_text(out):
                                bad.append(f"  {tag}：{sub} 真實計分缺值 {_missing_text(out)} ≠ A {a_reason if a_pres == 0 else '在場'}")
                            continue
                        if a_pres != 1:
                            bad.append(f"  {tag}：{sub} 真實計分在場（x={out.x!r}）≠ A 缺值")
                            continue
                        dx = abs(float(out.x) - float(R["x"][sub][rid]))
                        max_x = max(max_x, dx)
                        if not dx <= PARITY_TOL:
                            bad.append(f"  {tag}：{sub} x 真實 {out.x!r} ≠ A {R['x'][sub][rid]!r}（|差|={dx:.3e}）")
                        if sub in bc.args:
                            ds = {d for _, d in bc.args[sub]}
                            if ds != {float(R["d"][sub][rid])}:
                                bad.append(f"  {tag}：{sub} d 真實 {sorted(ds)} ≠ A {R['d'][sub][rid]!r}")
            if not quiet and n_day % PROGRESS_EVERY == 0:
                print(f"  B {n_day} 日（{T}） {time.time() - t0:.0f}s RSS {rss_mib():.0f} MiB", flush=True)
    finally:
        BCapture.uninstall(saved)
    if n_seen != len(samples):
        bad.append(f"  抽樣 {len(samples)} 列只走到 {n_seen} 列（抽樣日不在原料交易日軸）")
    if bad:
        raise ParityError(f"B 抽樣 parity 不符 {len(bad)} 處／{len(samples)} 列（容許 {PARITY_TOL}），不產出報告：\n"
                          + "\n".join(bad[:8]))
    return {"rows": len(samples), "max_abs_diff": max_diff, "max_abs_x_diff": max_x, "tol": PARITY_TOL}


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------
def run(db: Path, out: Path, *, cache_dir: Path, features: Path | None = None, registry: Path = SS.REGISTRY,
        per_group: int = DEFAULT_PER_GROUP, seed: int = DEFAULT_SEED, start: str | None = None, end: str | None = None,
        quiet: bool = False, keep_details: bool = False, src_root: Path | None = None) -> dict[str, Any]:
    """`start`／`end` 只供測試（合成資料在 2020 年）；CLI 一律用裁定 #64 ① 的寫死值。
    `keep_details=True` 時回傳值另帶 `_details`（run 表、逐列陣列、B 抽樣列；**不寫進報告檔**）。"""
    t0 = time.time()
    start = SAMPLE_START if start is None else start
    end = SAMPLE_END if end is None else end
    allocate({k: 0 for k in STRATA}, per_group)                # --per-group 下限只在 allocate 守一次（開跑前先驗）
    uses = usage_scan(src_root) if src_root is not None else usage_scan()
    reg = SS.load_registry(registry)
    with ScoreStore(db, readonly=True) as store:
        import export_scores as EX
        dv = EX.resolve_data_version(store, db.parent, None)
        sha, params = check_db(store, dv, reg)
        rows = read_rows(store, dv, start, end)
    window = int(params["window"])
    ps = {m: build_params(m) for m in MARKETS}
    src = RIO.ReplaySource(Path(cache_dir), dv, features_path=features, window=window)
    try:
        bridge = src.load_fundamentals(src.trading_dates())
        with instrumented() as (cap, sw):
            runs, run_of_row = run_a(src, bridge, rows, ps, cap, sw, quiet=quiet)
        R = runs.np()
        del runs
        par_a = parity_a(rows, R, run_of_row)
        w_run = np.bincount(run_of_row, minlength=R["score"].size).astype(np.int64)
        pool_run = np.bincount(run_of_row, weights=(rows["pool"] == 1).astype("d"), minlength=R["score"].size).astype(np.int64)
        rng = np.random.default_rng(seed)
        samples, strata = draw_b(rows, R, run_of_row, per_group, rng)
        par_b = run_b(src, bridge, samples, R, ps, window, quiet=quiet)
    finally:
        src.close()
    a_groups = summarize_a(rows, R, w_run, pool_run)
    c_groups = summarize_c(R, w_run)
    res = {"schema": 1, "db": str(db), "data_version": dv, "params_sha": sha,
           "model_versions": {m: ps[m].model_version() for m in MARKETS},
           "sample": {"start": start, "end": end}, "per_group": per_group, "seed": seed,
           "population": {"rows": int(rows["n"]), "rows_in_pool": int((rows["pool"] == 1).sum()),
                          "rows_out_pool": int((rows["pool"] != 1).sum()), "days": len(rows["dates"]),
                          "stocks": len(rows["sid_of"]), "runs": int(R["score"].size)},
           "u_definition": "u＝direction·(x−c)／(3d)，c、d、direction 取自該列實際傳給 S_clip 的參數；|u|>1 ⇔ clip 生效",
           "base_unit": "元（raw_month_revenue.revenue 原單位）",
           "base_definition": "revenue_yoy／revenue_yoy_vs_industry：revenue_yoy_3m 的分母（去年同期合計）；"
                              "revenue_accel：den＝近組 YoY 的分母、den_prev＝前一組 YoY 的分母。"
                              "ratio_to_own_median＝該列基期 ÷ 同一檔在同組（market × horizon）樣本內基期可算列的列加權中位數；"
                              "中位數 ≤ 0 的列另計 ratio_undefined_rows",
           "counterfactual_note": f"反事實只在記憶體內重算、不寫 db：|u|>K 的子指標改為 Missing({REASON_DENOM_ZERO})"
                                  "後重跑真實 line1_operations。原因碼只是反事實標記，不代表真實缺值。普查（逐列計數，權重皆 1）。"
                                  "翻轉以 50 為界（原 ≥50 變 <50 或反之），只計兩側皆已知的列；變未知另列。"
                                  f"進／出帶以 [{BAND[0]:g}, {BAND[1]:g}]（兩端含）為界，只計兩側皆已知的列；"
                                  "reweighted 0→1 計全部受影響列（含變未知的列）",
           "b_strata_rule": f"每組（market × horizon）以任一營收子指標 |u|>{EXTREME_U:g} 切 extreme／rest；母體 ≤ per_group 全取；"
                            "否則 extreme 配 per_group//2（不足全取）、rest 取餘、rest 不足回補 extreme。權重＝層母體÷層抽樣",
           "parity_a": par_a, "parity_b": par_b, "b_strata": strata,
           "a_groups": a_groups, "c_groups": c_groups, "usage": uses,
           "elapsed_s": round(time.time() - t0, 1), "rss_peak_mib": round(rss_mib(), 1)}
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(res, ensure_ascii=False, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    out.with_suffix(".txt").write_text(as_text(res), encoding="utf-8")
    if keep_details:
        res["_details"] = {"R": R, "rows": rows, "run_of_row": run_of_row, "w_run": w_run, "samples": samples}
    return res


# ---------------------------------------------------------------------------
# 文字報告
# ---------------------------------------------------------------------------
def _f(v: float | None, nd: int = 3) -> str:
    if v is None:
        return "—"
    if v != 0 and (abs(v) >= 1e6 or abs(v) < 1e-3):
        return f"{v:.{nd}e}"
    return f"{v:,.{nd}f}"


def _pct(v: float | None) -> str:
    return "—" if v is None else f"{v * 100:.3f}%"


def as_text(res: dict[str, Any]) -> str:
    pa, pb, pop = res["parity_a"], res["parity_b"], res["population"]
    L = [f"營收子指標基期影響面量測（唯讀，§29）｜樣本 {res['sample']['start']}～{res['sample']['end']}｜"
         f"B 每組 {res['per_group']} 列、seed {res['seed']}",
         f"db {res['db']}｜data_version {res['data_version']}｜params_sha {res['params_sha']}｜"
         f"model_version {res['model_versions']}",
         f"母體：個股列 {pop['rows']:,}（池內 {pop['rows_in_pool']:,}／池外 {pop['rows_out_pool']:,}）｜{pop['days']} 日｜"
         f"{pop['stocks']} 檔｜run {pop['runs']:,}",
         f"A 全母體 parity：{pa['rows']:,} 列初爻分數／reweighted／unknown 與 db 全數相符（max |差| {pa['max_abs_diff']:.3e} ≤ {pa['tol']}）",
         f"B 抽樣 parity：{pb['rows']:,} 列以真實重播路徑重算，初爻與 db、營收子指標 x／缺值原因／d 與 A 全數相符"
         f"（初爻 max |差| {pb['max_abs_diff']:.3e}、x max |差| {pb['max_abs_x_diff']:.3e}）",
         f"耗時 {res['elapsed_s']}s｜RSS 峰值 {res['rss_peak_mib']} MiB",
         f"位置：{res['u_definition']}",
         f"基期單位：{res['base_unit']}；{res['base_definition']}",
         f"反事實：{res['counterfactual_note']}",
         "本報告只陳述量測數字（不含成因與規則）。", "",
         "== A 全母體分布（比例的分母：rows＝該組全部列；present＝該子指標在場列）"]
    for g in res["a_groups"]:
        L.append(f"-- {g['market']} {g['horizon']} {g['sub']}｜列 {g['rows']:,}（池內 {g['rows_in_pool']:,}／池外 {g['rows_out_pool']:,}）｜"
                 f"在場 {g['present']:,}｜c {g['c']}｜d {g['d']}")
        L.append("   缺值：" + ("；".join(f"{k}×{v:,}" for k, v in g["missing"].items()) or "無"))
        if not g["present"]:
            continue
        au = g["abs_u"]
        L.append("   |u| " + "／".join(f"{k} {_f(v)}" for k, v in au.items()) + f"｜u 最小 {_f(g['u_min'])}、最大 {_f(g['u_max'])}")
        L.append("   |u|>" + "；>".join(f"{k}：{v['rows']:,}（{_pct(v['share_of_rows'])}／在場 {_pct(v['share_of_present'])}）"
                                       for k, v in g["abs_u_over"].items()) + f"｜clip 旗標列 {g['clipped_flag_rows']:,}")
        for tag, b in g["base_revenue_yuan"].items():
            if not b["extreme_rows"]:
                L.append(f"   |u|>{EXTREME_U:g} 列 0（{tag}）")
                continue
            L.append(f"   |u|>{EXTREME_U:g} 列 {b['extreme_rows']:,}｜{tag}（元）" +
                     "／".join(f"{k} {_f(v)}" for k, v in b["extreme"].items()))
            L.append(f"      {tag} 相對自身中位數比值 " + "／".join(f"{k} {_f(v)}" for k, v in b["ratio_to_own_median"].items())
                     + f"｜比值無定義 {b['ratio_undefined_rows']:,}")
            L.append(f"      對照：{tag} 全部在場列（元）" + "／".join(f"{k} {_f(v)}" for k, v in b["all_present"].items()))
    L += ["", "== C 反事實（|u|>K 的子指標改缺後重算初爻；普查）",
          "market horizon 情境 K | 受影響列 | 佔組 | Δ p5/p50/p95 | |Δ| max | 翻轉(陽→陰/陰→陽) | 變未知 | 進帶 | 出帶 | rw 0→1"]
    for c in res["c_groups"]:
        if not c["affected_rows"]:
            continue
        d = c["delta"]
        L.append(f"{c['market']} {c['horizon']} {c['scenario']} {c['K']:g} | {c['affected_rows']:,} | {_pct(c['affected_share'])} | "
                 f"{_f(d['p5'])}/{_f(d['p50'])}/{_f(d['p95'])} | {_f(c['abs_delta']['max'])} | "
                 f"{c['flip_rows']:,}({c['flip_yang_to_yin']:,}/{c['flip_yin_to_yang']:,}) | {c['became_unknown_rows']:,} | "
                 f"{c['band_enter_rows']:,} | {c['band_exit_rows']:,} | {c['reweighted_0_to_1_rows']:,}")
    zero = [f"{c['market']} {c['horizon']} {c['scenario']} {c['K']:g}" for c in res["c_groups"] if not c["affected_rows"]]
    if zero:
        L.append(f"受影響 0 列：{len(zero)} 組（見 JSON）")
    L += ["", "== B 分層（母體/抽/權重）"]
    for st in res["b_strata"]:
        L.append(f"{st['market']} {st['horizon']}：" + "；".join(
            f"{k} {st[k]['population']:,}/{st[k]['sampled']}/{'—' if st[k]['weight'] is None else format(st[k]['weight'], '.3f')}"
            for k in STRATA))
    L += ["", "== D 使用處（src/iching，ast 掃描）"]
    for u in res["usage"]:
        L.append(f"{u['file']} {u['function']}（行 {','.join(map(str, u['lines']))}）：{'、'.join(u['what'])}——{u['note']}")
    return "\n".join(L) + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="營收子指標基期影響面量測（唯讀；樣本段寫死為訓練＋驗證段；parity 不符即中止）")
    ap.add_argument("--db", default=str(REPO / "cache" / "scores.db"))
    ap.add_argument("--cache-dir", default=str(REPO / "cache"), help="原料 DB 目錄（同 replay_scores.py）")
    ap.add_argument("--features", default=None, help="預設 <cache-dir>/features.db")
    ap.add_argument("--registry", default=str(SS.REGISTRY))
    ap.add_argument("--out", required=True, help="報告 JSON 路徑（runs/revbase/report_<TO>.json）；同名 .txt 一併寫出")
    ap.add_argument("--per-group", type=int, default=DEFAULT_PER_GROUP, help=f"B 抽樣每組列數（預設 {DEFAULT_PER_GROUP}）")
    ap.add_argument("--seed", type=int, default=DEFAULT_SEED, help=f"B 抽樣種子（預設 {DEFAULT_SEED}）")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args(argv)
    try:
        res = run(Path(args.db), Path(args.out), cache_dir=Path(args.cache_dir),
                  features=Path(args.features) if args.features else None, registry=Path(args.registry),
                  per_group=args.per_group, seed=args.seed, quiet=args.quiet)
        print(f"== 營收基期影響面：母體 {res['population']['rows']:,} 列｜A parity {res['parity_a']['rows']:,} 列、"
              f"B parity {res['parity_b']['rows']:,} 列相符｜{res['elapsed_s']}s RSS {res['rss_peak_mib']} MiB｜報告 {args.out}")
        return 0
    except Exception as e:  # noqa: BLE001  任何例外一律 rc=2
        print(f"[revenue_base_impact 中止] {type(e).__name__}: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
