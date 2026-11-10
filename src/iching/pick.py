"""P4-L 選多空的純函式（docs/P4-PREVIEW.md §24；裁定 #76）。

本模組只做「讀進來的數值 → 名單／等級」的計算，不讀檔、不連網；讀檔與抓取在 `scripts/build_pick.py`。
**計分引擎不改**：做多方向分數就是 `base_score`；做空分數由引擎已寫入的 `line_2`–`line_5` 反向加權（§24.3）。

凡標「主對話設計」者均未經回測（`calibrated=false`），畫面一律標「AI 研判，未經回測，非保證」。
"""
from __future__ import annotations

import math
from typing import Any, Iterable, Mapping, Sequence

import numpy as np

from iching.score.indicators import atr14_prev
from iching.score.transform import P_hist, Missing

HORIZONS = ("short", "swing", "mid")
MARKETS = ("twse", "tpex")
DIRECTIONS = ("long", "short")
STATES = ("S1", "S2", "S3", "S4")

# S1 §A1.2 N0（＝scripts/revalidate_thresholds.py N0_TABLE；tests/test_pick.py 交叉比對）
N0_TABLE: dict[tuple[str, str], int] = {
    ("S1", "long"): 20, ("S1", "short"): 5,
    ("S2", "long"): 12, ("S2", "short"): 10,
    ("S3", "long"): 10, ("S3", "short"): 10,
    ("S4", "long"): 5, ("S4", "short"): 20,
}

# §24.3 做空分數權重：沿 B2.7 只取二～五爻，使用時除以總和正規化
SHORT_WEIGHTS: dict[str, dict[int, float]] = {
    "short": {2: 25.0, 3: 20.0, 4: 20.0, 5: 20.0},
    "swing": {2: 25.0, 3: 20.0, 4: 15.0, 5: 20.0},
    "mid":   {2: 25.0, 3: 15.0, 4: 10.0, 5: 15.0},
}
SCORE_FLOOR = 7.30          # 爻分數尺度下限（N(v;0,100) 的 0 點）

# §24.3 條件扣分（主對話設計）
D1_RET60 = -0.30            # 60 日後復權報酬 ≤ −30% → −10
D1_PTS = 10.0
D2_SHORT_RATIO = 30.0       # 券資比（%）≥ 30 且 C > MA20 → −10
D2_PTS = 10.0
D3_PHIST = 10.0             # 融資餘額 250 日 P_hist ≤ 10 且 60 日報酬 < 0 → −5
D3_PTS = 5.0
RET_N = 60
MARGIN_PHIST_N = 250

# §24.4 風險量（主對話設計）
V_PCT = 80.0                # v ≥ 同市場池內 P80
E_ATR = 3.0                 # 做多 e ≥ 3、做空 e ≤ −3
G_ATR = 1.0                 # g ≥ 1
G_DAYS = 3
M_DD = 0.15                 # 20 日最大回撤 ≥ 15%
MA_N = 20
DD_N = 20
GRADE_PENALTY = {"A": 0.0, "B": 2.0, "C": 5.0}

# v1.2.2 :420 流動性（規格寫明的待回測起點）；無成交日數門檻為主對話細化
LIQ_N = 20
LIQ_MEDIAN_TWD = 3e7
LIQ_MAX_ZERO_DAYS = 0       # 近 20 交易日無成交 ≥ 1 日即不進候選

# 事件旗標（只顯示、不進分數；§24.2）
EVENT_DAYS = 10
LONG_BLACK_ATR = 2.0
REPORT_KEYWORDS = ("財務報告", "財報", "自結")


# ---------------------------------------------------------------------------
# 名額
def n0(basic_state: str | None, direction: str) -> int | None:
    """未判定或不在 S1–S4 → None（當日該方向不出名單）。"""
    return N0_TABLE.get((basic_state, direction)) if basic_state in STATES else None


def cut_n(n0_: int | None, quota_mult: float | None) -> int:
    """N＝floor(N0 × 名額乘數連乘)、下限 0（B1 :345；不得用 round）。"""
    if n0_ is None:
        return 0
    m = 1.0 if quota_mult is None else float(quota_mult)
    if not math.isfinite(m):
        return 0
    return max(0, math.floor(n0_ * m))


# ---------------------------------------------------------------------------
# 做空分數
def _finite(x: Any) -> float | None:
    if x is None or isinstance(x, bool):
        return None
    try:
        f = float(x)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def short_raw(lines: Mapping[int, Any], horizon: str) -> float | None:
    """§24.3：Σ w_k·(100 − L_k) ÷ Σ w_k，k＝二～五爻。任一爻未知（None／非有限數）→ None。"""
    w = SHORT_WEIGHTS[horizon]
    vals = {k: _finite(lines.get(k)) for k in w}
    if any(v is None for v in vals.values()):
        return None
    return sum(w[k] * (100.0 - vals[k]) for k in w) / sum(w.values())


def deductions(ret60: float | None, short_ratio: float | None, short_ratio_checked: bool,
               close: float | None, ma20: float | None, margin_phist: float | None) -> dict[str, Any]:
    """§24.3 條件扣分。回 {"D1","D2","D3": "hit"|"no"|"miss"|"na"|"unchecked", "pts": 扣分合計}。

    - 資料缺（D2 除外）→ "miss"，視為成立而扣分（S1a §3.3：缺值不得默認無風險）。
    - D2：融券餘額未查核 → "unchecked"、不扣；已查核但券資比（融資餘額 0）、收盤或 MA20 缺 → "na"、**也不扣**
      （§24.3「扣分項所需資料缺（D2 除外）→ 視為成立而扣分」的 D2 除外）。
    """
    out: dict[str, Any] = {}
    pts = 0.0
    if ret60 is None:
        out["D1"] = "miss"
    else:
        out["D1"] = "hit" if ret60 <= D1_RET60 else "no"
    if out["D1"] in ("hit", "miss"):
        pts += D1_PTS

    if not short_ratio_checked:
        out["D2"] = "unchecked"
    elif short_ratio is None or close is None or ma20 is None:
        out["D2"] = "na"
    else:
        out["D2"] = "hit" if (short_ratio >= D2_SHORT_RATIO and close > ma20) else "no"
        if out["D2"] == "hit":
            pts += D2_PTS

    if ret60 is None or margin_phist is None:
        out["D3"] = "miss"
    else:
        out["D3"] = "hit" if (margin_phist <= D3_PHIST and ret60 < 0) else "no"
    if out["D3"] in ("hit", "miss"):
        pts += D3_PTS
    out["pts"] = pts
    return out


def short_final(raw: float | None, pts: float) -> float | None:
    """扣分後做空分數（下限 7.30）；只用於門檻之後的排序與顯示（§24.2）。"""
    if raw is None:
        return None
    return max(SCORE_FLOOR, raw - pts)


# ---------------------------------------------------------------------------
# 價格序列衍生量（輸入皆為升冪 numpy 陣列、最後一個元素＝T；缺值為 NaN）
def ret_n(close_adj: np.ndarray, n: int = RET_N) -> float | None:
    """後復權 n 日報酬 C_T ÷ C_{T−n} − 1；需 n+1 筆、兩端皆有值。"""
    if close_adj.size < n + 1:
        return None
    a, b = close_adj[-n - 1], close_adj[-1]
    if not (np.isfinite(a) and np.isfinite(b)) or a <= 0:
        return None
    return float(b / a - 1.0)


def ma_last(close_adj: np.ndarray, n: int = MA_N) -> float | None:
    if close_adj.size < n:
        return None
    w = close_adj[-n:]
    if not np.isfinite(w).all():
        return None
    return float(np.mean(w))


def margin_phist(margin: np.ndarray, rules) -> float | None:
    """融資餘額 250 日 P_hist（視窗含當日與平手規則沿 `Rules`）；不足或含缺值 → None。"""
    r = P_hist(margin, MARGIN_PHIST_N, rules.phist_include_today, rules.phist_tie)
    return None if isinstance(r, Missing) else float(r.native)


def _clean_tail(arrs: Sequence[np.ndarray], n: int) -> list[np.ndarray] | None:
    """取最後 n 筆，要求全部有限。"""
    if any(a.size < n for a in arrs):
        return None
    tail = [a[-n:] for a in arrs]
    if not all(np.isfinite(t).all() for t in tail):
        return None
    return tail


def risk_metrics(o: np.ndarray, h: np.ndarray, lo: np.ndarray, c: np.ndarray, ex_mask: np.ndarray,
                 atr_method: str) -> dict[str, float | None]:
    """§24.4 四個量（後復權價）。任一量無法計算 → None（之後視為違規：缺值不得默認無風險）。

    - atr＝ATR14 取至 T−1（`Rules.atr_method`）；需最後 16 筆 H／L／C 全有值。
    - v＝atr ÷ C_T。
    - e＝(C_T − MA20) ÷ atr（帶正負號，方向在 `risk_grade` 判）。
    - g＝近 3 日 max |O_t − C_{t−1}| ÷ atr，除權息等係數事件日（`ex_mask`）排除；3 日都被排除 → 0。
    - m＝近 20 日後復權收盤峰到谷最大回撤（正數）。
    """
    out: dict[str, float | None] = {"atr": None, "v": None, "e": None, "g": None, "m": None}
    tail = _clean_tail([h, lo, c], 16)
    atr = atr14_prev(tail[0], tail[1], tail[2], atr_method) if tail else None
    if atr is not None and atr > 0:
        out["atr"] = atr
        cT = float(c[-1])
        out["v"] = atr / cT if cT > 0 else None
        ma = ma_last(c)
        out["e"] = (cT - ma) / atr if ma is not None else None
        if o.size >= G_DAYS + 1 and c.size >= G_DAYS + 1:
            gaps = []
            ok = True
            for i in range(1, G_DAYS + 1):
                t = -i
                if bool(ex_mask[t]):
                    continue
                ot, cp = o[t], c[t - 1]
                if not (np.isfinite(ot) and np.isfinite(cp)):
                    ok = False
                    break
                gaps.append(abs(float(ot) - float(cp)) / atr)
            if ok:
                out["g"] = max(gaps) if gaps else 0.0
    ct = _clean_tail([c], DD_N)
    if ct is not None:
        w = ct[0]
        peak = np.maximum.accumulate(w)
        out["m"] = float(np.max((peak - w) / peak)) if (peak > 0).all() else None
    return out


def v_threshold(vs: Iterable[float | None]) -> float | None:
    """同市場池內 v 的 P80（numpy 線性內插）；無有效值 → None。"""
    a = np.asarray([x for x in vs if x is not None and math.isfinite(x)], dtype=float)
    return float(np.percentile(a, V_PCT, method="linear")) if a.size else None


def risk_grade(m: Mapping[str, float | None], v_p80: float | None, direction: str) -> tuple[str, list[str]]:
    """違規 0＝A、1＝B、2＝C、≥3 或（追價且高波動）＝D。缺值的量視為違規並標 `*_na`（S1a §3.3）。"""
    viol: list[str] = []
    v = m.get("v")
    if v is None or v_p80 is None:
        viol.append("vol_na")
        hv = True
    elif v >= v_p80:
        viol.append("vol")
        hv = True
    else:
        hv = False
    e = m.get("e")
    if e is None:
        viol.append("chase_na")
        ch = True
    elif (direction == "long" and e >= E_ATR) or (direction == "short" and e <= -E_ATR):
        viol.append("chase")
        ch = True
    else:
        ch = False
    g = m.get("g")
    if g is None:
        viol.append("gap_na")
    elif g >= G_ATR:
        viol.append("gap")
    dd = m.get("m")
    if dd is None:
        viol.append("dd_na")
    elif dd >= M_DD:
        viol.append("dd")
    n = len(viol)
    if n >= 3 or (ch and hv):
        return "D", viol
    return ("A", "B", "C")[n], viol


# ---------------------------------------------------------------------------
# 流動性
def liquidity(amounts: np.ndarray, volumes: np.ndarray) -> dict[str, Any]:
    """近 20 交易日成交金額中位數（停牌／缺日以 0 計）與無成交日數；兩者都過才 ok。"""
    a = np.nan_to_num(np.asarray(amounts[-LIQ_N:], dtype=float), nan=0.0)
    v = np.nan_to_num(np.asarray(volumes[-LIQ_N:], dtype=float), nan=0.0)
    if a.size < LIQ_N:
        a = np.concatenate([np.zeros(LIQ_N - a.size), a])
        v = np.concatenate([np.zeros(LIQ_N - v.size), v])
    med = float(np.median(a))
    zero = int(np.sum(v <= 0))
    return {"med": med, "zero": zero, "ok": med >= LIQ_MEDIAN_TWD and zero <= LIQ_MAX_ZERO_DAYS}


# ---------------------------------------------------------------------------
# 交易限制 × 方向（S1 §A5.2；§24.2）
LIM_CATS = ("margin", "notice", "disposal", "term", "halt", "altered")


def limit_effect(flags: Mapping[str, Any] | None, T: str) -> dict[str, Any]:
    """一檔的 lim.json 旗標 → 各方向的處置。回
    {"long": {"remove": [...], "research": []}, "short": {"remove": [...], "research": [...]}, "tags": [...]}。

    - 停牌 `ht`、處置中 `dp.st=="active"` → 兩方向移除。
    - 將處置 `dp.st=="upcoming"`、注意股 `nt`、全額交割 `al`（使用者決定只加旗標）→ 只加旗標。
    - 做空移除：`noc`，或 `ms` 與 `sbl` 同時成立（主對話解讀），或 `tm` 生效中（from ≤ T ≤ to；主對話推論）。
    - 做空降研究候選：只 `ms` 或只 `sbl`、`dn`、`tm` 將生效（from > T）。
    """
    f = flags or {}
    res: dict[str, Any] = {"long": {"remove": [], "research": []}, "short": {"remove": [], "research": []}, "tags": []}
    if f.get("ht") is not None:            # 值可能是 {from:null,resume:null}（上櫃 cmode）→ 以鍵存在判斷
        for d in DIRECTIONS:
            res[d]["remove"].append("halt")
    dp = f.get("dp")
    if isinstance(dp, Mapping):
        if dp.get("st") == "active":
            for d in DIRECTIONS:
                res[d]["remove"].append("disposal")
        else:
            res["tags"].append("disposal_upcoming")
    if f.get("nt") is not None:
        res["tags"].append("notice")
    if f.get("al"):
        res["tags"].append("altered")
    ms, sbl = bool(f.get("ms")), bool(f.get("sbl"))
    if f.get("noc"):
        res["short"]["remove"].append("noc")
    if ms and sbl:
        res["short"]["remove"].append("ms_sbl")
    elif ms:
        res["short"]["research"].append("ms")
    elif sbl:
        res["short"]["research"].append("sbl")
    if f.get("dn"):
        res["short"]["research"].append("dn")
    tm = f.get("tm")
    if isinstance(tm, Mapping):
        fr, to = tm.get("from"), tm.get("to")
        if isinstance(fr, str) and fr <= T and (not isinstance(to, str) or T <= to):
            res["short"]["remove"].append("tm_active")
        elif isinstance(fr, str) and fr > T:
            res["short"]["research"].append("tm_upcoming")
        else:
            res["short"]["research"].append("tm_unknown")
        if tm.get("infer"):
            res["tags"].append("tm_inferred")
    return res


def lim_status(lim: Mapping[str, Any] | None, T: str, market: str) -> dict[str, Any]:
    """該市場的查核狀態：`ok`＝lim.date==T 且六類皆已查核；`unchecked`＝未查核的類別（日期不符時全部）。"""
    if not isinstance(lim, Mapping) or lim.get("date") != T:
        return {"ok": False, "date_ok": False, "unchecked": list(LIM_CATS)}
    chk = lim.get("checked") or {}
    un = [c for c in LIM_CATS if not chk.get(f"{c}|{market}")]
    return {"ok": not un, "date_ok": True, "unchecked": un}


def short_confirmed(flags: Mapping[str, Any] | None, st: Mapping[str, Any]) -> bool:
    """§24.5 第 4 步「放空公開資格已確認」：lim.date==T；margin／term／halt／disposal／altered 皆已查核；
    非 noc；ms、sbl、dn 皆未標；無生效中或將生效的 tm。"""
    if not st.get("date_ok"):
        return False
    need = ("margin", "term", "halt", "disposal", "altered")
    if any(c in st.get("unchecked", []) for c in need):
        return False
    f = flags or {}
    return not (f.get("noc") or f.get("ms") or f.get("sbl") or f.get("dn") or f.get("tm") is not None)


# ---------------------------------------------------------------------------
# 排序與分級
def pool_pct(score: float, pool_scores: Sequence[float]) -> float:
    """池內百分位＝100 × #(≤ score) ÷ n。"""
    n = len(pool_scores)
    if n == 0:
        return 0.0
    return 100.0 * sum(1 for s in pool_scores if s <= score) / n


def rank_rows(rows: Iterable[tuple[str, float]]) -> list[tuple[int, str, float]]:
    """方向排名：分數高到低、次鍵代號；同分並列名次（1,1,3）。"""
    srt = sorted(rows, key=lambda r: (-r[1], r[0]))
    out: list[tuple[int, str, float]] = []
    prev, rk = None, 0
    for i, (sid, s) in enumerate(srt, 1):
        if s != prev:
            rk, prev = i, s
        out.append((rk, sid, s))
    return out


def threshold_from_t0(t0: Mapping[str, Any] | None, market: str, horizon: str, state: str | None,
                      direction: str, shift_deciles: float | None) -> float | None:
    """T0 表查門檻：格鍵 `market|horizon|state|direction`，`thresholds` 以位移百分位點（0／5／10／15／20，
    ＝shift_deciles×10）為鍵，值為對應分數（L4 產生，已夾 99）。表缺、格缺、位移不在表上 → None（不出候選）。"""
    if not isinstance(t0, Mapping) or state not in STATES:
        return None
    cell = (t0.get("cells") or {}).get(f"{market}|{horizon}|{state}|{direction}")
    if not isinstance(cell, Mapping):
        return None
    s = 0.0 if shift_deciles is None else float(shift_deciles)
    if not math.isfinite(s):
        return None
    key = str(int(round(s * 10)))
    return _finite((cell.get("thresholds") or {}).get(key))


def grade_block(*, state: str | None, n_long: int, n_short: int, t_long: float | None, t_short: float | None,
                stocks: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
    """一個（市場、期間）的候選分級（§24.5；T0 校準後才由 build_pick 啟用）。

    `stocks[sid]`：{"bs", "sraw", "sfin", "liq_ok", "grade": {long, short}, "lim": limit_effect 結果,
    "confirmed": bool（short_confirmed）, "lim_unchecked": bool}。只放 `in_rank_pool` 且方向分數有限的檔。
    回 {"long", "short_confirmed", "short_research", "short_list": {long, short}, "watch": {long, short}, "reason"}。
    """
    out: dict[str, Any] = {"long": [], "short_confirmed": [], "short_research": [],
                           "short_list": {"long": False, "short": False}, "watch": {"long": 0, "short": 0}, "reason": None}
    if state not in STATES:
        out["reason"] = "basic_state_undetermined"
        return out

    def hard_ok(s: Mapping[str, Any], d: str) -> bool:
        return bool(s.get("liq_ok")) and s["grade"][d] != "D" and not s["lim"][d]["remove"]

    # 做多
    lp = [s["bs"] for s in stocks.values() if s.get("bs") is not None]
    if t_long is not None:
        el = [(sid, s) for sid, s in stocks.items() if s.get("bs") is not None and s["bs"] >= t_long and hard_ok(s, "long")]
        el.sort(key=lambda x: (-(pool_pct(x[1]["bs"], lp) - GRADE_PENALTY[x[1]["grade"]["long"]]), -x[1]["bs"], x[0]))
        out["long"] = [sid for sid, _ in el[:n_long]]
        out["short_list"]["long"] = len(out["long"]) < n_long
    out["watch"]["long"] = len(lp) - len(out["long"])

    # 做空：門檻比較用扣分前 short_raw，排序用扣分後
    sp = [s["sfin"] for s in stocks.values() if s.get("sfin") is not None]
    picked: list[str] = []
    if t_short is not None:
        es = [(sid, s) for sid, s in stocks.items()
              if s.get("sraw") is not None and s["sraw"] >= t_short and hard_ok(s, "short")]
        es.sort(key=lambda x: (-(pool_pct(x[1]["sfin"], sp) - GRADE_PENALTY[x[1]["grade"]["short"]]), -x[1]["sfin"], x[0]))
        for sid, s in es[:n_short]:
            picked.append(sid)
            ok = s.get("confirmed") and not s["lim"]["short"]["research"]      # 資格條件＝§24.5 第 4 步五類（short_confirmed）
            (out["short_confirmed"] if ok else out["short_research"]).append(sid)
        out["short_list"]["short"] = len(picked) < n_short
    out["watch"]["short"] = len(sp) - len(picked)
    return out


def event_flags(ex_dates: Sequence[str], window_dates: Sequence[str], subjects: Iterable[tuple[str, str]],
                o: np.ndarray, c: np.ndarray, atr: float | None) -> list[str]:
    """§24.2 事件旗標（只顯示）：近 10 交易日內 係數事件（除權息／減資等，factors 不分類型）、財報類公告（主旨關鍵字）、
    異常長黑（(O−C)÷ATR14 ≥ 2）。`window_dates`＝最近 10 個交易日（升冪）。"""
    w = set(window_dates)
    out: list[str] = []
    if any(d in w for d in ex_dates):
        out.append("ex")
    if any(d in w and any(k in s for k in REPORT_KEYWORDS) for d, s in subjects):
        out.append("report")
    if atr is not None and atr > 0:
        n = min(EVENT_DAYS, o.size, c.size)
        for i in range(1, n + 1):
            ot, ct = o[-i], c[-i]
            if np.isfinite(ot) and np.isfinite(ct) and (ot - ct) / atr >= LONG_BLACK_ATR:
                out.append("long_black")
                break
    return out


def _avg_rank(a: np.ndarray) -> np.ndarray:
    order = np.argsort(a, kind="mergesort")
    r = np.empty(a.size, dtype=float)
    r[order] = np.arange(1, a.size + 1, dtype=float)
    # 同值取平均名次
    vals, inv = np.unique(a, return_inverse=True)
    sums = np.bincount(inv, weights=r)
    cnts = np.bincount(inv)
    return (sums / cnts)[inv]


def spearman(x: Sequence[float], y: Sequence[float]) -> float | None:
    """Spearman 等級相關（同值平均名次）；有效樣本 < 3 或任一側常數 → None。"""
    a, b = np.asarray(x, dtype=float), np.asarray(y, dtype=float)
    ok = np.isfinite(a) & np.isfinite(b)
    a, b = a[ok], b[ok]
    if a.size < 3:
        return None
    ra, rb = _avg_rank(a), _avg_rank(b)
    if np.std(ra) == 0 or np.std(rb) == 0:
        return None
    return float(np.corrcoef(ra, rb)[0, 1])
