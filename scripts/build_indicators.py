#!/usr/bin/env python3
"""裁定 #75（`docs/P4-PREVIEW.md` §13）後端：逐股逐爻**子指標原始值** x 的分片 `data/web/ind/<代號前 2 字>.json`＋大盤 `market.json`。

    python3 scripts/build_indicators.py --root . [--out data/web/ind]

**描述性顯示**（CANON 鐵律 8：只需驗算式正確，不涉排序／訊號）。產物契約見 `docs/P4-PREVIEW.md` §13.8。

## 流程（§13.2 第 2 項「方案 B」）

1. T＝`data/scores/` 檔名最大者；讀 `data/scores/<T>.json`（參考分數）與 `data/state/cross.json`（每日班寫完 T 的狀態快照）。
2. 以 `runs/collect/` 中 ≤ T 的原料包重建 T 日視窗——**與 `daily_core.run_offline` 同一條路徑**（`rebuild_from_bundles`＋
   pool／factors／entrants／fundamentals 同一組讀檔函式）。
3. 重建「計分 T 那一刻」的跨日狀態（只有二爻歷史會影響分數，見下「二爻歷史」）。
4. 以**同一支** `score_market`／`score_stock`＋`assemble_row(detail=True)` 重算，**六爻分數逐列與參考分數 `==` 比對**：
   不同的列（含參考有、重算算不出的列，以及重算得出、參考檔卻缺的列）不輸出並計數；個股不同列 > 0 → stderr 警告、產物記 `mismatch`；
   **個股不同列 > 1%，或大盤任一列不同 → 整批不輸出**（舊檔不動、exit 2）。`params_sha` 重算值必須等於參考檔頂層值，
   否則亦整批拒絕（換參數後重算沒有意義）。
5. 只輸出原始值 x 與顯示用輔助值（§13.4 第 1 項：腳本端另算、不改引擎 meta）；**不輸出**子指標 0–100 分、`d`、權重、`clipped`。

計分程式、分數、`params_sha`／`model_version` 指紋、parity 路徑一律不動——本腳本只讀不寫上述任何一者。

## 二爻歷史（C1：照每日班實際狀態來源填齊，不留空碰運氣）

每日班計分 T 時吃的是 `data/state/cross.json` 在 T−1 收盤後的版本（`run_offline` 讀檔 → `step(T)` → 覆寫）。那一版不在磁碟上
（已被 T 覆寫），所以由兩個來源**拼回**：
- **個股** `stock_line2[<sid>|<h>]`（容量 9）：現行快照（last_date＝T）的串列＝「截至 T 的最後 ≤9 次 push」，去掉最後一格（＝T 當日，
  須等於參考分數 T 列的 `line_2`，否則判狀態不一致）得 T−8…T−1 的 ≤8 格；若快照已滿 9 格，最舊的那一格（第 9 次以前的 push）
  由 `data/scores/` 各日檔的 `line_2` 補（`step` 對當日計分的每一檔每一期間都 push 一次＝分數檔每一列，**停牌日不 push**，
  所以是「該檔最近 9 個有列的日子」而非「最近 9 個交易日」）；重疊的 8 格與快照逐格比對。分數檔不夠舊、補不到那一格的鍵
  記 `hist_uncertain`（該格留 None），交給逐列比對守門決定是否輸出。
- **大盤** `market_line2[<mk>|<h>]`（容量 5）：同法，T−4…T−1 取自快照、T−5 取自分數檔大盤列。
  **大盤二爻歷史不影響六爻分數**：`MarketInputs.line2_score_t_minus_5` 只被 `market.market_flags` 的 `flag_breadth`（F-廣度擴張／
  收縮）讀，`score_market` 的六爻與方向分數完全不讀它（`src/iching/score/market.py` `market_flags` 內 `flag_breadth(l2,
  inp.line2_score_t_minus_5.get(ms.horizon), rules)` 是全檔唯一讀取處）。這就是 §13.3 研究探測沒填大盤二爻歷史、10-08 大盤 6 組
  仍逐位相同的原因——不是巧合，是結構。本腳本仍照實填齊（旗標本檔不輸出，但不讓輸入與每日班不同）。
- 個股二爻歷史只影響四爻族 A（短線／波段 `volume_scenario` 序 1 的「前 9 日二爻分」），**不影響任何 x**：本產物輸出的
  原始值與二爻歷史無關，歷史只是讓逐列比對守門能用分數把關。

## 回傳碼

0 成功（含個股不同列 ≤ 1%：照常輸出其餘、stderr 警告）；2 中止（不同列 > 1%、大盤任一列不同、`params_sha` 不符、
狀態快照不是 T、缺原料包、讀檔失敗、out 目錄不可寫）——中止時**不寫任何檔、不刪任何舊檔**。
"""
from __future__ import annotations

import argparse
import json
import math
import os
import re
import sys
import time
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence

import numpy as np

ROOT_DEFAULT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT_DEFAULT / "src"))

from iching import bundle_io as B  # noqa: E402
from iching import daily_core as DC  # noqa: E402
from iching import replay_state as RS  # noqa: E402
from iching.features_io import params_fingerprint  # noqa: E402
from iching.run_common import TEXT_VERSION, build_params_payload, load_state  # noqa: E402
from iching.replay_step import _score_or_none  # noqa: E402
from iching.score import score_market, score_stock  # noqa: E402
from iching.score.assemble import MARKET_STOCK_ID, assemble_row  # noqa: E402
from iching.score.indicators import as_f, atr14_prev, atr_series_prev, sma_at, sma_last  # noqa: E402
from iching.score.params import HORIZONS, MARKETS, SCOPE_MARKET, SCOPE_STOCK, ParamSet, Rules, build_params  # noqa: E402
from iching.score.stock import MA20_N  # noqa: E402
from iching.universe import is_financial  # noqa: E402

SCHEMA = 1
DATE_FILE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}\.json$")
SHARD_RE = re.compile(r"^[0-9A-Za-z]{1,2}\.json$")
MARKET_FILE = "market.json"
MISMATCH_MAX_RATIO = 0.01          # C1：個股不同列 > 1% → 整批不輸出
PRICE_BASIS = "adjusted_back"      # 價格類換算（ATR%／距均線%／斜率%／報酬%）一律用引擎同一份後復權價；`c` 為原料包原始收盤

# 缺值代碼（依引擎 `Missing.reason`，`src/iching/score/transform.py` REASON_*）。x 欄為字串＝缺值；null＝該子指標本來就沒有 x
# （情境分：`structure`／`volume_scenario`／`continuation`，類別見輔助欄）。
MISS_CODES = {
    "insufficient_history": "H",   # 歷史不足
    "missing": "M",                # 缺資料
    "denominator_zero": "Z",       # 分母為零（含當日無成交、一價成交全窗等，依引擎判定）
    "not_eligible": "E",           # 無信用交易資格（融資）
    "stale": "S",                  # 美股資料過舊（stale_days ≥ 3）
    "contract_rolled": "R",        # 期貨換月日
    "spec_gap": "G",               # 規格缺口
    "line_unknown": "U",           # 爻未知
}
MISS_UNKNOWN = "?"                 # 引擎回了未登錄的原因碼（不應發生；出現即代表本表該補）
MISS_LEGEND = {**{v: k for k, v in MISS_CODES.items()}, MISS_UNKNOWN: "unknown_reason"}

# x 的小數位（固定，依 `Param.unit`；個別子指標覆寫）。只影響顯示精度，不影響守門（守門比的是未四捨五入的分數）。
UNIT_DP = {"ATR": 2, "pp": 2, "%": 2, "ratio": 3, "day": 1, "ln": 3, "sd": 2, "口": 0, "": 2}
X_DP_OVERRIDE = {
    "short_sale_change": 4,        # 借券餘額變化 ÷ 發行股數（pp），量級 1e-2～1e-4
    "eps_diff_over_price": 3,
    "foreign_net_oi_phist": 0,     # x＝外資台指期淨未平倉口數（原始值，P_hist 前）
    "vix_phist_rev": 2,            # x＝VIX 當日值
    "market_direction": 2,
    "range_position": 3,           # 以下三者為 0..1 比值（Param.unit 空字串）
    "close_position": 3,
    "industry_above_ma20_ratio": 3,
}
# 輔助欄小數位（名稱→dp；字串／整數欄不在此表）
AUX_DP = {
    "atr_pct": 2, "dev_s_pct": 2, "dev_l_pct": 2, "slope_pct": 2, "ret_l": 2, "idx_l": 2, "ret_s": 2, "idx_s": 2,
    "ind_med_l": 2, "vr": 2, "dchg": 2, "day_pct": 2, "f_l": 0, "f_s": 0, "t_l": 0, "t_s": 0, "v_l": 0, "v_s": 0,
    "m0": 0, "m1": 0, "mret": 2, "ss0": 0, "ss1": 0, "shares": 0, "ind_med": 2, "idx": 2, "ind_rev_med": 2,
    "close": 2, "basis_c": 3, "c": 2,
}


class BuildIndicatorsError(RuntimeError):
    pass


def warn(msg: str) -> None:
    print(f"[build_indicators] 警告：{msg}", file=sys.stderr)


# ---------------------------------------------------------------------------
# 數值小工具
def fnum(v: Any) -> float | None:
    """有限數 → float；None／NaN／inf／bool／非數 → None。"""
    if v is None or isinstance(v, bool):
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def rnd(v: Any, dp: int) -> float | int | None:
    """固定小數位；dp=0 回 int；−0.0 正規化成 0。"""
    f = fnum(v)
    if f is None:
        return None
    if dp <= 0:
        return int(round(f))
    r = round(f, dp)
    return 0.0 if r == 0 else r


def pct_change(cur: Any, base: Any) -> float | None:
    """(cur / base − 1) × 100；任一端缺或 base＝0 → None。"""
    c, b = fnum(cur), fnum(base)
    if c is None or b is None or b == 0:
        return None
    return (c / b - 1.0) * 100.0


def ret_n(a: Any, n: int) -> float | None:
    """n 日報酬 %（＝引擎 `stock._pct_ret(a, n)` 的端點：a[T] / a[T−n] − 1）。"""
    if a is None:
        return None
    arr = as_f(a)
    if arr.size < n + 1:
        return None
    return pct_change(arr[-1], arr[-1 - n])


def win_sum(a: Any, n: int) -> float | None:
    """最後 n 筆合計；不足 n 筆或窗內有 NaN → None（同引擎 `ind_net_strength` 的 NaN 守門）。"""
    if a is None:
        return None
    arr = as_f(a)
    if arr.size < n:
        return None
    w = arr[-n:]
    if np.isnan(w).any():
        return None
    return float(np.sum(w))


def at_back(a: Any, k: int) -> float | None:
    """a[T−k]（k=0 為 T）；不足或非有限 → None。"""
    if a is None:
        return None
    arr = as_f(a)
    if arr.size < k + 1:
        return None
    return fnum(arr[-1 - k])


def miss_code(m: Mapping[str, Any] | None) -> str:
    reason = (m or {}).get("missing")
    return MISS_CODES.get(reason, MISS_UNKNOWN)


def x_dp(indicator_id: str, unit: str) -> int:
    if indicator_id in X_DP_OVERRIDE:
        return X_DP_OVERRIDE[indicator_id]
    return UNIT_DP.get(unit, 2)


def _window_json(w: Any) -> Any:
    if isinstance(w, tuple):
        return [int(x) for x in w]
    if isinstance(w, (int, float)) and not isinstance(w, bool):
        return int(w)
    return None


# ---------------------------------------------------------------------------
# x 欄（取自 assemble_row(detail=True) 的族／子指標；依引擎族與子指標的固定順序）
def x_columns(row: Mapping[str, Any], ps: ParamSet, scope: str, horizon: str) -> tuple[list[str], list[Any]]:
    names: list[str] = []
    vals: list[Any] = []
    for k in "123456":
        fams = row.get(f"line_{k}_families") or {}
        for fam, fp in fams.items():
            for iid, sp in (fp.get("subs") or {}).items():
                names.append(f"{k}{fam}.{iid}")
                if sp.get("missing") is not None:
                    vals.append(miss_code(sp["missing"]))
                    continue
                x = sp.get("x")
                if x is None:
                    vals.append(None)                 # 情境分沒有 x（類別在輔助欄）
                    continue
                if fnum(x) is None:
                    vals.append(MISS_CODES["missing"])   # 理論上不會發生：非有限 x 引擎已轉 Missing（aggregate.sub_result）
                    continue
                p = ps.params.get((scope, horizon, k, fam, iid))
                vals.append(rnd(x, x_dp(iid, p.unit if p is not None else "")))
    return names, vals


def win_table(ps: ParamSet, scope: str, horizon: str, names: Iterable[str]) -> dict[str, list[Any]]:
    """`{col: [window, unit, dp]}`——窗長與單位取自 `ParamSet`（不寫死），dp＝本檔固定小數位。"""
    out: dict[str, list[Any]] = {}
    for name in names:
        k, rest = name[0], name[1:]
        fam, iid = rest.split(".", 1)
        p = ps.params.get((scope, horizon, k, fam, iid))
        if p is None:
            continue
        out[name] = [_window_json(p.window), p.unit, x_dp(iid, p.unit)]
    return out


def _sub_meta(row: Mapping[str, Any], line: str, fam: str, iid: str) -> dict:
    fp = (row.get(f"line_{line}_families") or {}).get(fam) or {}
    sp = (fp.get("subs") or {}).get(iid) or {}
    return dict(sp.get("meta") or {})


# ---------------------------------------------------------------------------
# 四爻族 A 情境序（輔助欄）：與 `stock.volume_scenario_day` 同一組條件、同一順序，只回「落在哪一序」而不回分數。
# `tests/test_build_indicators.py` 以序號重組分數、逐日對 `volume_scenario_day` 驗證兩者一致。
def volume_scenario_seq(close, volume, atr_prev_series, i: int, n_dd: int, line2_score: float | None,
                        rules: Rules) -> dict[str, Any]:
    c, v = as_f(close), as_f(volume)
    out: dict[str, Any] = {"seq": None, "vr": None, "dchg": None}
    if i < MA20_N or i < n_dd - 1 or i < 1:
        return out
    atr = float(atr_prev_series[i])
    vma = float(np.mean(v[i - MA20_N:i]))
    if v[i] == 0 or np.isnan(atr) or atr == 0 or vma == 0:
        return out                                   # 序 0：資料不足（引擎回缺值）
    vr = float(v[i] / vma)
    dchg = float((c[i] - c[i - 1]) / atr)
    out["vr"], out["dchg"] = vr, dchg
    dd = (float(np.max(c[i - n_dd + 1:i + 1])) - c[i]) / atr
    ma20 = float(np.mean(c[i - MA20_N + 1:i + 1]))
    seq1_rest = (0 < dd <= rules.vs_drawdown_max) and vr < rules.vs_low_ratio and c[i] >= ma20
    if seq1_rest:
        if line2_score is None:
            return out                               # 引擎：序 1 需二爻分 → 缺值
        if line2_score >= rules.vs_line2_min:
            out["seq"] = 1
            return out
    if dchg >= rules.vs_day_change and vr >= rules.vs_high_ratio:
        out["seq"] = 2
    elif dchg <= -rules.vs_day_change and vr >= rules.vs_high_ratio:
        out["seq"] = 3
    elif vr < rules.vs_low_ratio:
        out["seq"] = 4
    else:
        out["seq"] = 5
    return out


# ---------------------------------------------------------------------------
# 個股輔助欄
def stock_aux(si, ss, row: Mapping[str, Any], ps: ParamSet, horizon: str, *, eps_period: str | None) -> tuple[list[str], list[Any]]:
    """顯示用輔助值（§13.4 第 1 項：腳本端另算，不改引擎輸出）。價格類一律後復權價（引擎同一份）。"""
    g = lambda line, fam, iid: ps.get(SCOPE_STOCK, horizon, line, fam, iid)   # noqa: E731
    rules = ps.rules
    a: list[tuple[str, Any]] = []
    close = as_f(si.close) if si.close is not None else None
    # 初爻
    rev = dict(si.monthly_revenue or [])
    a.append(("1.rev_ym", max(rev) if rev else None))
    if horizon == "mid":
        a.append(("1.eps_p", eps_period))
        a.append(("1.ind_rev_med", rnd(si.industry_median_3m_yoy, AUX_DP["ind_rev_med"])))
        a.append(("1.ind_rev_n", si.industry_revenue_n))
        a.append(("1.floor", row.get("floor_applied")))
    # 二爻
    ma_s, ma_l = g("2", "A", "dist_ma_short").window, g("2", "A", "dist_ma_long").window
    slope_ma, slope_n = g("2", "B", "ma_long_slope").window
    atr = None
    if close is not None and si.high is not None and si.low is not None:
        atr = atr14_prev(si.high, si.low, si.close, rules.atr_method)
    C = at_back(close, 0)
    a.append(("2.atr_pct", rnd(None if (atr is None or C in (None, 0)) else atr / C * 100.0, AUX_DP["atr_pct"])))
    a.append(("2.dev_s_pct", rnd(pct_change(C, sma_last(close, ma_s)) if close is not None else None, AUX_DP["dev_s_pct"])))
    a.append(("2.dev_l_pct", rnd(pct_change(C, sma_last(close, ma_l)) if close is not None else None, AUX_DP["dev_l_pct"])))
    a.append(("2.slope_pct", rnd(pct_change(sma_at(close, slope_ma, 0), sma_at(close, slope_ma, slope_n))
                                 if close is not None else None, AUX_DP["slope_pct"])))
    a.append(("2.struct", _sub_meta(row, "2", "C", "structure").get("structure")))
    # 三爻
    wl, ws = g("3", "A", "excess_long").window, g("3", "A", "excess_short").window
    a.append(("3.ret_l", rnd(ret_n(close, wl), AUX_DP["ret_l"])))
    a.append(("3.idx_l", rnd(ret_n(si.index_close, wl), AUX_DP["idx_l"])))
    a.append(("3.ret_s", rnd(ret_n(close, ws), AUX_DP["ret_s"])))
    a.append(("3.idx_s", rnd(ret_n(si.index_close, ws), AUX_DP["idx_s"])))
    a.append(("3.ind_med_l", rnd((si.industry_median_return or {}).get(g("3", "B", "excess_vs_industry").window), AUX_DP["ind_med_l"])))
    a.append(("3.ind_n", si.industry_n))
    a.append(("3.cap", row.get("overheat_cap_applied")))
    # 四爻
    if horizon != "mid":
        seq = {"seq": None, "vr": None, "dchg": None}
        if close is not None and si.volume is not None and si.high is not None and si.low is not None:
            c, v, h, lo = close, as_f(si.volume), as_f(si.high), as_f(si.low)
            if c.size == v.size == h.size == lo.size and c.size >= 2:
                atrs = atr_series_prev(h, lo, c, rules.atr_method)
                seq = volume_scenario_seq(c, v, atrs, c.size - 1, g("4", "A", "volume_scenario").window,
                                          ss.lines["2"].score, rules)
        a.append(("4.seq", seq["seq"]))
        a.append(("4.vr", rnd(seq["vr"], AUX_DP["vr"])))
        a.append(("4.dchg", rnd(seq["dchg"], AUX_DP["dchg"])))
    a.append(("4.day_pct", rnd(ret_n(close, 1), AUX_DP["day_pct"])))
    cm = _sub_meta(row, "4", "C", "continuation")
    a.append(("4.cont", cm.get("continuation")))
    a.append(("4.cont_off", cm.get("event_day_offset")))
    # 五爻（量單位：成交量／法人淨額＝張，融資餘額＝張，借券餘額與發行股數＝股；見 §13.8）
    wl5, ws5 = g("5", "A", "foreign_strength_long").window, g("5", "A", "foreign_strength_short").window
    wn5 = g("5", "D", "margin_scenario").window
    a.append(("5.f_l", rnd(win_sum(si.foreign_net_shares, wl5), AUX_DP["f_l"])))
    a.append(("5.f_s", rnd(win_sum(si.foreign_net_shares, ws5), AUX_DP["f_s"])))
    a.append(("5.t_l", rnd(win_sum(si.trust_net_shares, wl5), AUX_DP["t_l"])))
    a.append(("5.t_s", rnd(win_sum(si.trust_net_shares, ws5), AUX_DP["t_s"])))
    a.append(("5.v_l", rnd(win_sum(si.volume, wl5), AUX_DP["v_l"])))
    a.append(("5.v_s", rnd(win_sum(si.volume, ws5), AUX_DP["v_s"])))
    wp = g("5", "C", "foreign_persistence").window
    fbuy = None
    if si.foreign_net_shares is not None:
        f = as_f(si.foreign_net_shares)
        if f.size >= wp:
            fbuy = int(np.sum(f[-wp:] > 0))
    a.append(("5.fbuy", fbuy))
    a.append(("5.m0", rnd(at_back(si.margin_balance, wn5), AUX_DP["m0"])))
    a.append(("5.m1", rnd(at_back(si.margin_balance, 0), AUX_DP["m1"])))
    a.append(("5.mret", rnd(ret_n(close, wn5), AUX_DP["mret"])))
    a.append(("5.mseq", _sub_meta(row, "5", "D", "margin_scenario").get("seq")))
    we = g("5", "E", "short_sale_change").window
    a.append(("5.ss0", rnd(at_back(si.short_sale_balance, we), AUX_DP["ss0"])))
    a.append(("5.ss1", rnd(at_back(si.short_sale_balance, 0), AUX_DP["ss1"])))
    a.append(("5.shares", rnd(si.shares_outstanding, AUX_DP["shares"])))
    # 上爻
    n6 = g("6", "B", "industry_relative_return").window
    a.append(("6.ind_med", rnd((si.industry_median_return or {}).get(n6), AUX_DP["ind_med"])))
    a.append(("6.idx", rnd(ret_n(si.index_close, n6), AUX_DP["idx"])))
    return [k for k, _ in a], [v for _, v in a]


def market_aux(mi, ms, row: Mapping[str, Any], ps: ParamSet, horizon: str) -> tuple[list[str], list[Any]]:
    g = lambda line, fam, iid: ps.get(SCOPE_MARKET, horizon, line, fam, iid)   # noqa: E731
    a: list[tuple[str, Any]] = []
    close = as_f(mi.index_close) if mi.index_close is not None else None
    C = at_back(close, 0)
    atr = None
    if close is not None and mi.index_high is not None and mi.index_low is not None:
        atr = atr14_prev(mi.index_high, mi.index_low, mi.index_close, ps.rules.atr_method)
    ma_s, ma_l = g("1", "A", "dist_ma_short").window, g("1", "A", "dist_ma_long").window
    slope_ma, slope_n = g("1", "B", "ma20_slope").window
    a.append(("1.close", rnd(C, AUX_DP["close"])))
    a.append(("1.atr_pct", rnd(None if (atr is None or C in (None, 0)) else atr / C * 100.0, AUX_DP["atr_pct"])))
    a.append(("1.dev_s_pct", rnd(pct_change(C, sma_last(close, ma_s)) if close is not None else None, AUX_DP["dev_s_pct"])))
    a.append(("1.dev_l_pct", rnd(pct_change(C, sma_last(close, ma_l)) if close is not None else None, AUX_DP["dev_l_pct"])))
    a.append(("1.slope_pct", rnd(pct_change(sma_at(close, slope_ma, 0), sma_at(close, slope_ma, slope_n))
                                 if close is not None else None, AUX_DP["slope_pct"])))
    nn = at_back(mi.n_stocks, 0)
    a.append(("2.n", None if nn is None else int(nn)))
    a.append(("3.div", _sub_meta(row, "3", "C", "divergence_scenario").get("seq")))
    wb = g("4", "C", "foreign_buy_days").window
    fbuy = None
    if mi.foreign_net_amount is not None:
        f = as_f(mi.foreign_net_amount)
        if f.size >= wb and not np.isnan(f[-wb:]).any():
            fbuy = int(np.sum(f[-wb:] > 0))
    a.append(("4.fbuy", fbuy))
    a.append(("5.basis_c", rnd(_sub_meta(row, "5", "B", "basis").get("c_rolling_median"), AUX_DP["basis_c"])))
    m6 = row.get("line_6_meta") or {}
    a.append(("6.us_asof", m6.get("us_asof")))
    a.append(("6.fx_asof", m6.get("fx_asof")))
    a.append(("6.stale", m6.get("stale_days")))
    return [k for k, _ in a], [v for _, v in a]


# ---------------------------------------------------------------------------
# 分數檔與二爻歷史
def list_score_files(scores_dir: Path) -> list[Path]:
    if not scores_dir.is_dir():
        return []
    return sorted(p for p in scores_dir.iterdir() if p.is_file() and DATE_FILE_RE.match(p.name))


def load_scores(path: Path) -> dict[str, Any]:
    try:
        with path.open("r", encoding="utf-8") as f:
            payload = json.load(f)
    except (OSError, ValueError) as e:
        raise BuildIndicatorsError(f"{path}: {e}") from e
    if not isinstance(payload, dict) or not isinstance(payload.get("rows"), list):
        raise BuildIndicatorsError(f"{path}: 形狀不對（需 dict 且 rows 為 list）")
    return payload


def _hist_key(row: Mapping[str, Any]) -> str | None:
    h = row.get("horizon")
    if h not in HORIZONS:
        return None
    sid = row.get("stock_id")
    if sid == MARKET_STOCK_ID:
        return f"M:{row.get('market')}|{h}"
    if isinstance(sid, str) and sid:
        return f"S:{sid}|{h}"
    return None


def line2_pushes(files_desc: Iterable[Path], need: Mapping[str, int],
                 loader: Callable[[Path], dict[str, Any]] = load_scores) -> dict[str, list[float | None]]:
    """由**新到舊**的分數檔收集各鍵的 `line_2`（＝`step` 當日 push 進 `CrossDayState` 的值），每鍵收到 `need[key]` 筆即停；
    全部鍵收滿就不再讀更舊的檔。回傳每鍵**升冪**（舊→新）的串列。"""
    got: dict[str, list[float | None]] = {k: [] for k in need}
    pending = {k for k, n in need.items() if n > 0}
    for p in files_desc:
        if not pending:
            break
        payload = loader(p)
        for r in payload["rows"]:
            if not isinstance(r, dict):
                continue
            k = _hist_key(r)
            if k in pending:
                got[k].append(fnum(r.get("line_2")))
                if len(got[k]) >= need[k]:
                    pending.discard(k)
    return {k: list(reversed(v)) for k, v in got.items()}


def pre_state(cross_post: RS.CrossDayState, ref_rows: Sequence[Mapping[str, Any]], prior_files_desc: Sequence[Path],
              loader: Callable[[Path], dict[str, Any]] = load_scores) -> tuple[RS.CrossDayState, dict[str, Any]]:
    """拼回「計分 T 那一刻」的二爻歷史（見檔頭「二爻歷史」）。`cross_post`＝寫完 T 的快照；`ref_rows`＝參考分數 T 的列。"""
    caps = {"S": RS.STOCK_LINE2_HIST, "M": RS.MARKET_LINE2_HIST}
    post_tail: dict[str, list[float | None]] = {}
    need: dict[str, int] = {}
    diag = {"keys": 0, "t_mismatch": [], "overlap_mismatch": [], "hist_uncertain": []}
    for r in ref_rows:
        if not isinstance(r, dict):
            continue
        k = _hist_key(r)
        if k is None:
            continue
        kind, key = k.split(":", 1)
        table = cross_post.market_line2 if kind == "M" else cross_post.stock_line2
        post = list(table.get(key, []))
        diag["keys"] += 1
        if not post or post[-1] != fnum(r.get("line_2")):
            diag["t_mismatch"].append(key)
            post_tail[k] = post[:-1] if post else []
        else:
            post_tail[k] = post[:-1]
        # 快照滿格 → 最舊那一格在快照之外，要往分數檔找（重疊部分順便比對）；未滿格 → 鏈上本來就只有這幾次 push
        need[k] = caps[kind] if len(post) >= caps[kind] else 0
    pushes = line2_pushes(prior_files_desc, need, loader)
    st = RS.CrossDayState()
    for k, tail in post_tail.items():
        kind, key = k.split(":", 1)
        cap = caps[kind]
        hist = list(tail)
        if need[k]:
            got = pushes.get(k, [])
            if tail and got[-len(tail):] != tail:
                diag["overlap_mismatch"].append(key)
            if len(got) >= cap:
                hist = [got[-cap]] + hist
            else:
                hist = [None] + hist
                diag["hist_uncertain"].append(key)
        sid_or_mk, h = key.rsplit("|", 1)
        for v in hist[-cap:]:
            if kind == "M":
                st.push_market_line2(sid_or_mk, h, v)
            else:
                st.push_stock_line2(sid_or_mk, h, v)
    return st, diag


# ---------------------------------------------------------------------------
# 逐列比對
def lines_of(row: Mapping[str, Any]) -> list[float | None]:
    return [fnum(row.get(f"line_{k}")) for k in range(1, 7)]


def same_lines(new_row: Mapping[str, Any], ref_row: Mapping[str, Any] | None) -> bool:
    """六爻分數逐位相同（Python `==`；None 對 None 視為相同）。"""
    if ref_row is None:
        return False
    return lines_of(new_row) == lines_of(ref_row)


# ---------------------------------------------------------------------------
class Colsets:
    """欄位組合登錄：`(scope, horizon, x 欄, 輔助欄)` → 流水號（依首次出現順序，決定性）。"""

    def __init__(self) -> None:
        self.ids: dict[tuple, int] = {}

    def get(self, scope: str, horizon: str, xs: Sequence[str], aux: Sequence[str]) -> int:
        key = (scope, horizon, tuple(xs), tuple(aux))
        if key not in self.ids:
            self.ids[key] = len(self.ids)
        return self.ids[key]

    def payload(self, used: Iterable[int]) -> dict[str, dict[str, Any]]:
        used = set(used)
        return {str(i): {"h": k[1], "x": list(k[2]), "a": list(k[3])} for k, i in self.ids.items() if i in used}


def compute(root: Path, *, log: Callable[[str], None] = lambda s: print(s, file=sys.stderr)) -> dict[str, Any]:
    """重建＋重算＋比對；回傳產物內容（未寫檔）與統計。任何會使整批不可信的狀況一律拋 `BuildIndicatorsError`。"""
    t0 = time.perf_counter()
    files = list_score_files(root / DC.SCORES_DIR)
    if not files:
        raise BuildIndicatorsError(f"{root / DC.SCORES_DIR} 沒有任何 YYYY-MM-DD.json 分數檔")
    T = files[-1].name[:-5]
    ref = load_scores(files[-1])
    if ref.get("tpe_date") != T:
        raise BuildIndicatorsError(f"{files[-1]} 的 tpe_date={ref.get('tpe_date')!r} ≠ 檔名 {T}")
    ref_sha = ref.get("params_sha")
    cross_post = load_state(root / DC.STATE_FILE)
    if cross_post.last_date != T:
        raise BuildIndicatorsError(f"狀態快照 last_date={cross_post.last_date} ≠ 最新分數日 {T}（二爻歷史無法照每日班狀態拼回）")
    window = int(cross_post.meta.get("window") or 0)
    dv = str(cross_post.meta.get("data_version") or "")
    ps = {m: build_params(m) for m in MARKETS}
    mv = {m: ps[m].model_version() for m in MARKETS}
    sha = params_fingerprint(build_params_payload(mv, window, cross_post.adv, fundamentals=True))
    if sha != ref_sha or cross_post.meta.get("params_sha") != ref_sha:
        raise BuildIndicatorsError(f"params_sha 不符：重算 {sha}／快照 {cross_post.meta.get('params_sha')}／分數檔 {ref_sha}")
    ref_rows = [r for r in ref["rows"] if isinstance(r, dict)]
    ref_by = {(r.get("market"), r.get("stock_id"), r.get("horizon")): r for r in ref_rows}

    cross, sdiag = pre_state(cross_post, ref_rows, list(reversed(files[:-1])))
    if sdiag["t_mismatch"] or sdiag["overlap_mismatch"]:
        warn(f"狀態快照與分數檔不一致：T 當格不符 {len(sdiag['t_mismatch'])} 鍵、重疊格不符 {len(sdiag['overlap_mismatch'])} 鍵"
             f"（例 {(sdiag['t_mismatch'] + sdiag['overlap_mismatch'])[:5]}）；以快照為準，由逐列比對守門把關")
    log(f"[build_indicators] T={T} 二爻歷史：{sdiag['keys']} 鍵、補不到最舊一格 {len(sdiag['hist_uncertain'])} 鍵")

    _, pool = DC.load_pool_file(root / DC.POOL_FILE)
    _, factors, _ = DC.load_factors_file(root / DC.FACTORS_FILE)
    held = [(d, p) for d, p in B.list_bundles(root) if d <= T]
    if not held or held[-1][0] != T:
        raise BuildIndicatorsError(f"缺 {T} 的原料包（{B.bundle_path(root, T)}）")
    good, _bad = DC.read_entrants(root)
    entrants = {sid: e for sid, e in good.items() if sid in pool}
    wc, _adv, rdiag = DC.rebuild_from_bundles(held, pool, factors, data_version=dv, window=window, entrants=entrants)
    t_rebuild = time.perf_counter() - t0
    log(f"[build_indicators] 重建 {len(held)} 份原料包 {t_rebuild:.1f}s")
    raw_T = B.read_bundle(held[-1][1])
    cal = DC.load_calendar_dates(root / DC.CALENDAR_TPE_FILE)
    _, bridge = DC.load_fundamentals_file(root / DC.FUND_FILE, pool, cal)
    provider = bridge.provider()

    colsets = Colsets()
    # -- 大盤 --
    market_rows: dict[str, list[Any]] = {}
    market_win: dict[str, dict[str, list[Any]]] = {}
    market_mismatch: list[str] = []
    direction: dict[str, dict[str, float | None]] = {}
    active = [m for m in MARKETS if wc.has_market(m) and wc.market_dates(m) and wc.market_dates(m)[-1] == T]
    for mk in MARKETS:
        for h in HORIZONS:
            if (mk, MARKET_STOCK_ID, h) in ref_by and mk not in active:
                market_mismatch.append(f"{mk}|{h}（重建無指數列）")
    for mk in active:
        mi = wc.market_inputs(mk, T, cross)
        direction[mk] = {}
        for h in HORIZONS:
            ms = score_market(mi, ps[mk], h)
            direction[mk][h] = _score_or_none(ms.direction_score)
            row = assemble_row(ms, ps[mk], dv, TEXT_VERSION, model_version=mv[mk], detail=True)
            rr = ref_by.get((mk, MARKET_STOCK_ID, h))
            if rr is None:                                   # 參考檔缺這列＝不同（不得當作相同），大盤 → 整批拒絕
                market_mismatch.append(f"{mk}|{h}（參考無此列）")
                continue
            if not same_lines(row, rr) or rr.get("model_version") != mv[mk]:
                market_mismatch.append(f"{mk}|{h}")
                continue
            xs, xv = x_columns(row, ps[mk], SCOPE_MARKET, h)
            an, av = market_aux(mi, ms, row, ps[mk], h)
            cid = colsets.get(SCOPE_MARKET, h, xs, an)
            market_rows[f"{mk}|{h}"] = [cid, xv, av]
            market_win[f"{mk}|{h}"] = win_table(ps[mk], SCOPE_MARKET, h, xs)
    if market_mismatch:
        raise BuildIndicatorsError(f"大盤列與參考分數不同：{market_mismatch}（大盤任一列不同即整批不輸出）")

    # -- 個股 --
    ref_stock = {(r.get("stock_id"), r.get("horizon")): r for r in ref_rows
                 if r.get("stock_id") != MARKET_STOCK_ID and r.get("horizon") in HORIZONS}
    done: set[tuple[str, str]] = set()
    stocks: dict[str, dict[str, Any]] = {}
    stock_win: dict[str, dict[str, list[Any]]] = {}
    mism: list[str] = []
    n_extra = 0
    t1 = time.perf_counter()
    for sid in wc.stock_ids_today():
        mk = wc.pool.listed(sid, T)
        if mk is None or mk not in active:
            continue
        fin = is_financial(wc.pool.industry_of(sid))
        extra = provider(sid, T) or {}
        sf = bridge.stocks.get(sid)
        eps_p = sf.period_asof(T) if sf is not None else None
        ent: dict[str, Any] = {}
        for h in HORIZONS:
            rr = ref_stock.get((sid, h))
            if rr is None:
                # 重算得出、參考檔卻沒有這列＝與每日班不一致（`step` 對當日每一檔每一期間都落一列）→ 記入 mismatch 並剔除；
                # 計入比例時分母仍是參考列數（保守：只會讓比例變大）
                n_extra += 1
                mism.append(f"{sid}|{h}（參考無此列）")
                ent[h] = None
                continue
            done.add((sid, h))
            si = wc.stock_inputs(sid, h, T, cross, direction[mk], is_financial=fin,
                                 monthly_revenue=extra.get("monthly_revenue"),
                                 industry_median_3m_yoy=extra.get("industry_median_3m_yoy"),
                                 industry_revenue_n=extra.get("industry_revenue_n"),
                                 fundamentals=extra.get("fundamentals"))
            ss = score_stock(si, ps[mk], h)
            row = assemble_row(ss, ps[mk], dv, TEXT_VERSION, model_version=mv[mk], detail=True)
            if rr.get("market") != mk or rr.get("model_version") != mv[mk] or not same_lines(row, rr):
                mism.append(f"{sid}|{h}")
                ent[h] = None
                continue
            xs, xv = x_columns(row, ps[mk], SCOPE_STOCK, h)
            an, av = stock_aux(si, ss, row, ps[mk], h, eps_period=eps_p)
            cid = colsets.get(SCOPE_STOCK, h, xs, an)
            ent[h] = [cid, xv, av]
            wk = f"{mk}|{h}"
            tbl = stock_win.setdefault(wk, {})
            for k2, v2 in win_table(ps[mk], SCOPE_STOCK, h, xs).items():
                tbl.setdefault(k2, v2)
        if any(v is not None for v in ent.values()):
            for h in HORIZONS:
                ent.setdefault(h, None)
            raw = (raw_T.stocks.get(sid) or {}).get("close")
            ent["m"] = mk
            ent["c"] = rnd(raw, AUX_DP["c"])
            stocks[sid] = ent
    for key in ref_stock:
        if key not in done:
            mism.append(f"{key[0]}|{key[1]}（重算無此列）")
    n_ref = len(ref_stock)
    t_score = time.perf_counter() - t1
    return {"T": T, "params_sha": ref_sha, "data_version": ref.get("data_version"), "colsets": colsets,
            "market_rows": market_rows, "market_win": market_win, "stocks": stocks, "stock_win": stock_win,
            "mismatch": sorted(mism), "n_ref": n_ref, "n_extra": n_extra, "state_diag": sdiag,
            "elapsed": {"rebuild_s": round(t_rebuild, 1), "score_s": round(t_score, 1),
                        "total_s": round(time.perf_counter() - t0, 1)}, "rebuild": rdiag}


def shard_of(sid: str) -> str:
    return sid[:2]


def build_payloads(res: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    """檔名 → 內容。每檔自帶 `schema`／`date`／`params_sha`／`cols`（只含本檔用到的組合）／`win`（只含本檔出現的市場×期間）。"""
    cs: Colsets = res["colsets"]
    head = {"schema": SCHEMA, "date": res["T"], "params_sha": res["params_sha"], "data_version": res["data_version"],
            "price_basis": PRICE_BASIS, "miss": MISS_LEGEND, "mismatch": len(res["mismatch"]), "rows_ref": res["n_ref"]}
    out: dict[str, dict[str, Any]] = {}
    out[MARKET_FILE] = {**head, "cols": cs.payload(v[0] for v in res["market_rows"].values()),
                        "win": dict(res["market_win"]), "rows": dict(res["market_rows"])}
    groups: dict[str, dict[str, Any]] = {}
    for sid in sorted(res["stocks"]):
        groups.setdefault(shard_of(sid), {})[sid] = res["stocks"][sid]
    for g, st in groups.items():
        used = [e[h][0] for e in st.values() for h in HORIZONS if e.get(h) is not None]
        keys = sorted({f"{e['m']}|{h}" for e in st.values() for h in HORIZONS if e.get(h) is not None})
        out[f"{g}.json"] = {**head, "cols": cs.payload(used), "win": {k: res["stock_win"][k] for k in keys}, "stocks": st}
    return out


def dumps(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n"


def write_all(out_dir: Path, payloads: Mapping[str, Any]) -> list[Path]:
    """先全部寫 `.tmp` 再逐一 `os.replace`；成功後刪掉本次沒有的舊分片（只刪符合分片／大盤檔名者）。"""
    try:
        out_dir.mkdir(parents=True, exist_ok=True)
        tmps = []
        for name in sorted(payloads):
            p = out_dir / name
            tmp = p.with_name(p.name + ".tmp")
            with tmp.open("w", encoding="utf-8", newline="\n") as f:
                f.write(dumps(payloads[name]))
            tmps.append((tmp, p))
        for tmp, p in tmps:
            os.replace(tmp, p)
        for p in out_dir.iterdir():
            if p.is_file() and p.name not in payloads and (SHARD_RE.match(p.name) or p.name == MARKET_FILE):
                p.unlink()
    except OSError as e:
        raise BuildIndicatorsError(f"out 目錄不可寫：{out_dir}: {e}") from e
    return [out_dir / n for n in sorted(payloads)]


def check_mismatch(res: Mapping[str, Any]) -> None:
    n, m = res["n_ref"], len(res["mismatch"])
    if n == 0:
        raise BuildIndicatorsError("參考分數沒有任何個股列")
    if m / n > MISMATCH_MAX_RATIO:
        raise BuildIndicatorsError(f"個股不同列 {m}/{n}＝{m / n:.2%} > {MISMATCH_MAX_RATIO:.0%}（例 {res['mismatch'][:5]}），整批不輸出")
    if m:
        warn(f"個股不同列 {m}/{n}（不輸出這些列；產物 mismatch={m}）：{res['mismatch'][:10]}")


def build(root: Path, out: Path) -> tuple[list[Path], dict[str, Any]]:
    res = compute(root)
    check_mismatch(res)
    payloads = build_payloads(res)
    return write_all(out, payloads), res


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="裁定 #75：逐股逐爻子指標原始值分片 data/web/ind/（docs/P4-PREVIEW.md §13）")
    ap.add_argument("--root", default=".", help="repo 根（讀 data/scores/、data/state/cross.json、runs/collect/ 等）")
    ap.add_argument("--out", default="data/web/ind", help="輸出目錄（相對路徑以 --root 為準）")
    args = ap.parse_args(argv)
    root = Path(args.root)
    out = Path(args.out)
    if not out.is_absolute():
        out = root / out
    try:
        paths, res = build(root, out)
    except BuildIndicatorsError as e:
        print(f"[build_indicators] 中止（未寫任何檔、舊檔保留）：{e}", file=sys.stderr)
        return 2
    total = sum(p.stat().st_size for p in paths)
    print(f"[build_indicators] T={res['T']} 檔數 {len(paths)}（含 {MARKET_FILE}）共 {total} bytes；個股 {len(res['stocks'])} 檔、"
          f"參考列 {res['n_ref']}、不同列 {len(res['mismatch'])}；耗時 {res['elapsed']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
