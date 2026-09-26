#!/usr/bin/env python3
"""新舊 `scores.db` 的**模型換版**比對（唯讀；`docs/P3-CALIBRATION.md` §33）。

    python3 scripts/model_diff.py --new cache/scores.db --old cache/scores_pre68.db \\
        --out runs/modeldiff/report_2026-09-27.json                  # 同名 .txt 一併寫出

用途：裁定 #68（營收年增率分母 ≤ 0 → 缺值）＋#69（換 25 個 d）後全量重播，要證明「**只有預期的爻與其下游變了**」並量化變化。
`scripts/diff_scores.py` 的鍵含 `model_version`，換版後兩邊鍵全不同、不能直接用；本檔的鍵**不含** `model_version`：
`(market, horizon, stock_id, date, data_version, text_version)`。

## 範圍（登錄書「保留段未動用」）

預設只看 `config.SEGMENTS` 訓練段起～驗證段迄；保留段與之後的日子、以及訓練段之前的日子**不讀 scores 列**
（`replay_day` 那張每日一列的診斷表會讀，用來數「略過 N 日」）。`--include-holdout` 才把保留段起的日子納入
（訓練段之前的日子一律不比，報告另列天數）。報告寫明是否納入。

## 不變式（任一違反 → rc=1，每條列前 `--show` 例）

- **C1** 範圍內兩側 `(data_version, date)` 集合相同、每日鍵集合相同。
- **C2** 大盤列（`stock_id='__MARKET__'`，`score.assemble.MARKET_STOCK_ID`）除 `model_version` 外全欄逐位相同。
- **C3** 個股列「非允許爻」全部逐位相同。允許爻＝`ALLOWED_LINES`（§32：兩市場初爻；twse 另加三爻、上爻）。
  逐爻欄＝`line_k`／`_unknown`／`_coverage_ratio`／`_reweighted`＋`lines_provisional`／`lines_formal`／`line_states`／
  `streaks` 的第 k 位。另加兩條本檔的解釋（§33 列明）：①`lines_provisional`／`lines_formal` 是**整串**可為 NULL 的衍生欄
  （任一爻分數缺／任一爻尚無狀態即整串 NULL，`hexagram.lines_from_scores`／`replay_state.advance_lines`），一側 NULL 時
  非允許爻的第 k 位不比，但要求那個 NULL **可歸因於允許爻**（NULL 側有允許爻分數缺／狀態為 `-`），否則仍算違反；
  ②非允許爻的「爻內中間量」（`LINE_META_COLS`：初爻 `floor_applied`、三爻 `overheated`／`overheat_cap_applied`）也要相同
  ——只加嚴、不放寬（C4 的前提不含它們）。
- **C4** 個股列若允許爻的逐爻欄全同，則整列（全欄）逐位相同。
- **C5** `replay_day` 的 `n_market_rows`／`n_stocks`／`n_in_pool`／`n_stock_rows`／`n_market_any_unknown`／`index_missing` 相同；
  `n_stock_any_unknown` 只報差、不判失敗；`model_version_twse／tpex` 報兩側值。
- **C6** 新側每市場恰一個 `model_version` 且＝現行碼（`build_params(m).model_version()`，同 `hetzner_replay.sh`），
  舊側與新側不得相同（防比到同一份）——這兩條不過是 **rc=2**（前置條件）。舊側值只報告（`--expect-old` 可驗）。

「逐位相同」＝Python `==`（`float` 的 `==` 在 SQLite 讀回值上等同逐位：SQLite 不存 NaN（寫成 NULL），整數值的 REAL
存成整數、`-0.0` 讀回為 `0.0`）；`flags` 比 JSON **原文**、不解析。

## 前置條件（任一不過 → rc=2、不寫報告）

開檔（`ScoreStore(readonly=True)`，含實體表結構守門）；範圍內新側 `model_version` 恰一個且＝現行碼；舊≠新；
每側每個鍵恰一列（同鍵多列＝多版本殘留）；每列的 `model_version`＝該側該日 `replay_day` 記的該市場值；
`scope` 與 `stock_id` 一致；`scores` 有日期但 `replay_day` 沒有；以及任何未預期例外。

## 記憶體

逐日串流：一次只載一個 `(data_version, date)` 的兩側列（約 1.5 萬列）；`|Δline_k|` 只存非零值（`array('d')`，
零另計數），分位數在收尾時逐組排序一次。

rc：0 全符合／1 不變式違反（報告照寫）／2 前置條件失敗或例外（不寫報告）。
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import math
import resource
import sys
import time
import traceback
from array import array
from pathlib import Path
from typing import Any, Iterable

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "src"))

from iching.config import SEGMENTS  # noqa: E402
from iching.score.assemble import MARKET_STOCK_ID  # noqa: E402
from iching.scores_io import SCALAR_COLS, ScoreStore, ScoreStoreError  # noqa: E402

SCHEMA = 1
MARKETS = ("twse", "tpex")
# §32：14 個營收鍵兩市場都在初爻；11 個 twse 鍵——excess_long／short／accel 在三爻、industry_relative_return 在上爻。
# 以 `tests/test_model_diff.py::test_allowed_lines_derive_from_ruling_69_keys` 對 `CHANGED_BY_RULING_69` 與 `build_params` 釘住。
ALLOWED_LINES: dict[str, tuple[int, ...]] = {"twse": (1, 3, 6), "tpex": (1,)}
LINE_META_COLS: dict[int, tuple[str, ...]] = {1: ("floor_applied",), 3: ("overheated", "overheat_cap_applied")}
C5_EQUAL = ("n_market_rows", "n_stocks", "n_in_pool", "n_stock_rows", "n_market_any_unknown", "index_missing")
DIAG_COLS = ("data_version", "date", "model_version_twse", "model_version_tpex", "n_market_rows", "n_stocks", "n_in_pool",
             "n_stock_rows", "n_stock_any_unknown", "n_market_any_unknown", "index_missing")
INVARIANTS = ("C1", "C2", "C3", "C4", "C5", "C6")

COL = {c: i for i, c in enumerate(SCALAR_COLS)}
I_SCOPE = COL["scope"]
I_PROV, I_FORMAL = COL["lines_provisional"], COL["lines_formal"]
I_STATES, I_STREAKS = COL["line_states"], COL["streaks"]
I_BASE, I_KW, I_KWP = COL["base_score"], COL["king_wen"], COL["king_wen_provisional"]
LINE_IDX = {k: COL[f"line_{k}"] for k in range(1, 7)}      # line_k、_unknown、_coverage_ratio、_reweighted 連續四欄
VIEW_NAMES = ("line_{k}", "line_{k}_unknown", "line_{k}_coverage_ratio", "line_{k}_reweighted",
              "lines_provisional[{k}]", "lines_formal[{k}]", "line_states[{k}]", "streaks[{k}]")


class PreconditionError(RuntimeError):
    """rc=2：開檔／結構／版本前置條件。"""


# ---------------------------------------------------------------------------
# 範圍、版本、雜湊
# ---------------------------------------------------------------------------
def date_range(include_holdout: bool) -> tuple[str, str | None]:
    """(起, 迄)；迄 None＝不設上限。起＝訓練段起，迄＝驗證段迄（`--include-holdout` 時不設迄）。"""
    return SEGMENTS["train"][0], (None if include_holdout else SEGMENTS["valid"][1])


def classify_date(d: str, start: str, end: str | None) -> str:
    if d < start:
        return "before"
    if end is not None and d > end:
        return "after"
    return "in"


def current_model_versions() -> dict[str, str]:
    """現行碼的 model_version（兩市場）——與 `scripts/hetzner_replay.sh` 守門、`replay_scores.py` 同一條：
    `build_params(m).model_version()`（預設 calibrated）。**不寫死字串。**"""
    from iching.score.params import build_params
    return {m: build_params(m).model_version() for m in MARKETS}


def sha256_file(path: Path, chunk: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            b = f.read(chunk)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


def quantile_linear(zeros: int, nonzero_sorted: Any, q: float) -> float | None:
    """樣本＝`zeros` 個 0 ＋ 已排序的非零值；線性內插分位數（同 numpy 預設 `linear`、同 `calibrate_d` 的 p85 method）。
    位置 h＝(N−1)q，值＝v[⌊h⌋]＋(h−⌊h⌋)(v[⌈h⌉]−v[⌊h⌋])。N＝0 回 None。"""
    n = zeros + len(nonzero_sorted)
    if n == 0:
        return None
    h = (n - 1) * q
    lo, hi = math.floor(h), math.ceil(h)

    def v(i: int) -> float:
        return 0.0 if i < zeros else float(nonzero_sorted[i - zeros])
    return v(lo) + (h - lo) * (v(hi) - v(lo))


# ---------------------------------------------------------------------------
# 讀取（唯讀、逐日）
# ---------------------------------------------------------------------------
def open_store(path: Path) -> ScoreStore:
    try:
        return ScoreStore(path, readonly=True)
    except (ScoreStoreError, OSError) as e:          # sqlite3.DatabaseError 另由 main 的總括例外接住
        raise PreconditionError(f"開不了 {path}：{e}") from e


def read_diag(store: ScoreStore) -> dict[tuple[str, str], dict]:
    """`replay_day` 全表（每日一列的小表，不是 scores 列）。"""
    out = {}
    for r in store.conn.execute(f"SELECT {','.join(DIAG_COLS)} FROM replay_day ORDER BY data_version, date"):
        d = dict(zip(DIAG_COLS, r))
        out[(d["data_version"], d["date"])] = d
    return out


def params_shas(store: ScoreStore) -> dict[str, str]:
    return {dv: sha for dv, sha in store.conn.execute("SELECT data_version, params_sha FROM replay_meta ORDER BY data_version")}


def scores_dates_in(store: ScoreStore, start: str, end: str | None) -> set[str]:
    """範圍內 `scores` 出現過的日期（走 `idx_scores_date`，只讀索引、不讀列內容；範圍外一律不碰）。"""
    if end is None:
        q, a = "SELECT DISTINCT date FROM scores WHERE date >= ?", (start,)
    else:
        q, a = "SELECT DISTINCT date FROM scores WHERE date >= ? AND date <= ?", (start, end)
    return {r[0] for r in store.conn.execute(q, a)}


_ROW_SQL = (f"SELECT s.market, s.horizon, s.stock_id, s.date, v.data_version, v.text_version, v.model_version, "
            f"{', '.join('s.' + c for c in SCALAR_COLS)} "
            f"FROM scores s JOIN versions v ON v.version_id = s.version_id WHERE v.data_version=? AND s.date=?")


def read_day_rows(store: ScoreStore, dv: str, date: str) -> dict[tuple, tuple[str, tuple]]:
    """一日一 data_version 的全部列：鍵（不含 model_version）→ (model_version, SCALAR_COLS 原值 tuple)。同鍵多列 → rc=2。"""
    out: dict[tuple, tuple[str, tuple]] = {}
    for r in store.conn.execute(_ROW_SQL, (dv, date)):
        key, mv, vals = r[:6], r[6], tuple(r[7:])
        if key in out:
            raise PreconditionError(f"{store.path}：鍵 {key} 有多列（model_version {out[key][0]} 與 {mv}）——多版本殘留")
        out[key] = (mv, vals)
    return out


# ---------------------------------------------------------------------------
# 逐爻檢視
# ---------------------------------------------------------------------------
def line_view(vals: tuple, k: int, streaks: list[str] | None) -> tuple:
    """第 k 爻的逐爻欄（依 `VIEW_NAMES` 順序）。整串 NULL 的位置欄在該位回 None。"""
    i = LINE_IDX[k]
    prov, formal, states = vals[I_PROV], vals[I_FORMAL], vals[I_STATES]
    return (vals[i], vals[i + 1], vals[i + 2], vals[i + 3],
            None if prov is None else prov[k - 1],
            None if formal is None else formal[k - 1],
            None if states is None else states[k - 1],
            None if streaks is None else streaks[k - 1])


def _streaks(vals: tuple) -> list[str] | None:
    s = vals[I_STREAKS]
    return None if s is None else s.split(",")


def _null_attributable(vals: tuple, pos_col: int, allowed: tuple[int, ...]) -> bool:
    """`vals` 那側的 `lines_provisional`／`lines_formal` 為 NULL，是否可歸因於允許爻：
    暫定串＝有允許爻分數為 NULL；正式串＝有允許爻的 `line_states` 為 `-`（或整串 line_states 為 NULL）。"""
    if pos_col == I_PROV:
        return any(vals[LINE_IDX[k]] is None for k in allowed)
    states = vals[I_STATES]
    return states is None or any(states[k - 1] == "-" for k in allowed)


# ---------------------------------------------------------------------------
# 累計
# ---------------------------------------------------------------------------
class LineAcc:
    __slots__ = ("score_changed", "unknown_flip", "formal_flip", "zeros", "nonzero")

    def __init__(self) -> None:
        self.score_changed = self.unknown_flip = self.formal_flip = self.zeros = 0
        self.nonzero = array("d")

    def delta(self, a: Any, b: Any) -> None:
        if a is None or b is None:
            return
        d = abs(float(b) - float(a))
        if d == 0.0:
            self.zeros += 1
        else:
            self.nonzero.append(d)

    def summary(self) -> dict:
        import numpy as np
        nz = np.sort(np.frombuffer(self.nonzero, dtype=np.float64)) if len(self.nonzero) else []
        n = self.zeros + len(nz)
        return {"score_changed": self.score_changed, "unknown_flip": self.unknown_flip, "formal_flip": self.formal_flip,
                "abs_delta_n": n, "abs_delta_nonzero": len(nz),
                "abs_delta_max": (float(nz[-1]) if len(nz) else (0.0 if n else None)),
                "abs_delta_p50": quantile_linear(self.zeros, nz, 0.50),
                "abs_delta_p99": quantile_linear(self.zeros, nz, 0.99)}


class GroupAcc:
    def __init__(self, market: str) -> None:
        self.allowed = ALLOWED_LINES[market]
        self.n_rows = self.n_diff_rows = 0
        self.lines = {k: LineAcc() for k in self.allowed}
        self.base_changed = 0
        self.base_max: float | None = None
        self.kw_changed = self.kwp_changed = 0

    def _base(self, a: Any, b: Any) -> None:
        if a is None or b is None:
            return
        d = abs(float(b) - float(a))
        self.base_max = d if self.base_max is None else max(self.base_max, d)

    def same_row(self, vals: tuple) -> None:
        self.n_rows += 1
        for k in self.allowed:
            if vals[LINE_IDX[k]] is not None:
                self.lines[k].zeros += 1
        self._base(vals[I_BASE], vals[I_BASE])

    def summary(self) -> dict:
        return {"n_rows": self.n_rows, "n_diff_rows": self.n_diff_rows,
                "lines": {str(k): acc.summary() for k, acc in self.lines.items()},
                "base_score_changed": self.base_changed, "base_score_max_abs_delta": self.base_max,
                "king_wen_changed": self.kw_changed,
                "king_wen_changed_ratio": (self.kw_changed / self.n_rows) if self.n_rows else None,
                "king_wen_provisional_changed": self.kwp_changed}


class Violations:
    def __init__(self, show: int) -> None:
        self.show = show
        self.n = {c: 0 for c in INVARIANTS}
        self.examples: dict[str, list[str]] = {c: [] for c in INVARIANTS}

    def add(self, code: str, msg: str) -> None:
        self.n[code] += 1
        if len(self.examples[code]) < self.show:
            self.examples[code].append(msg)

    def any(self) -> bool:
        return any(self.n.values())


def _first_diff(va: tuple, vb: tuple) -> str:
    for c, a, b in zip(SCALAR_COLS, va, vb):
        if a != b:
            return f"欄 {c}: 舊={a!r} 新={b!r}"
    return "?"


# ---------------------------------------------------------------------------
# 逐列比對
# ---------------------------------------------------------------------------
def compare_stock_row(key: tuple, va: tuple, vb: tuple, g: GroupAcc, vio: Violations) -> None:
    """個股列（va≠vb 已知）：C3、C4、各計數。"""
    market = key[0]
    allowed = g.allowed
    g.n_rows += 1
    g.n_diff_rows += 1
    sa, sb = _streaks(va), _streaks(vb)
    views_a = {k: line_view(va, k, sa) for k in range(1, 7)}
    views_b = {k: line_view(vb, k, sb) for k in range(1, 7)}
    tag = f"{key[3]} {market}/{key[1]}/{key[2]}"
    # -- C3：非允許爻 --
    for k in range(1, 7):
        if k in allowed:
            continue
        a, b = views_a[k], views_b[k]
        for j, (x, y) in enumerate(zip(a, b)):
            if x == y:
                continue
            pos_col = I_PROV if j == 4 else I_FORMAL if j == 5 else None
            if pos_col is not None and (va[pos_col] is None) != (vb[pos_col] is None):
                null_side = va if va[pos_col] is None else vb
                if _null_attributable(null_side, pos_col, allowed):
                    continue                                   # 整串 NULL 來自允許爻（§33 解釋①）
            vio.add("C3", f"{tag} 爻{k} {VIEW_NAMES[j].format(k=k)}: 舊={x!r} 新={y!r}")
        for c in LINE_META_COLS.get(k, ()):
            if va[COL[c]] != vb[COL[c]]:
                vio.add("C3", f"{tag} 爻{k} {c}: 舊={va[COL[c]]!r} 新={vb[COL[c]]!r}")
    # -- C4：允許爻逐爻欄全同 → 整列必同（va≠vb 已知 → 違反）--
    if all(views_a[k] == views_b[k] for k in allowed):
        vio.add("C4", f"{tag} 允許爻 {list(allowed)} 全同但整列不同：{_first_diff(va, vb)}")
    # -- 計數 --
    for k in allowed:
        a, b, acc = views_a[k], views_b[k], g.lines[k]
        if a[0] != b[0]:
            acc.score_changed += 1
        if a[1] != b[1]:
            acc.unknown_flip += 1
        if va[I_FORMAL] is not None and vb[I_FORMAL] is not None and a[5] != b[5]:
            acc.formal_flip += 1
        acc.delta(a[0], b[0])
    if va[I_BASE] != vb[I_BASE]:
        g.base_changed += 1
    g._base(va[I_BASE], vb[I_BASE])
    if va[I_KW] != vb[I_KW]:
        g.kw_changed += 1
    if va[I_KWP] != vb[I_KWP]:
        g.kwp_changed += 1


def _check_row_meta(side: str, key: tuple, mv: str, vals: tuple, diag: dict) -> None:
    market, sid = key[0], key[2]
    want = diag.get(f"model_version_{market}")
    if want is None or mv != want:
        raise PreconditionError(f"{side}：{key[3]} {market}/{key[1]}/{sid} 的 model_version={mv} ≠ 該日 replay_day 記的 {want}"
                                "——多版本殘留")
    scope = vals[I_SCOPE]
    if (sid == MARKET_STOCK_ID) != (scope == "market_index") or scope not in ("market_index", "stock"):
        raise PreconditionError(f"{side}：{key[3]} {market}/{key[1]}/{sid} 的 scope={scope!r} 與 stock_id 不一致")


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------
def run(old_path: Path, new_path: Path, *, include_holdout: bool = False, show: int = 20, do_hash: bool = True,
        expect_old: dict[str, str] | None = None, data_version: str | None = None, progress_every: int = 0) -> dict:
    """回報告 dict（`result_rc` 0／1）；前置條件不過拋 `PreconditionError`。"""
    t0 = time.perf_counter()
    start, end = date_range(include_holdout)
    cur = current_model_versions()
    old, new = open_store(old_path), open_store(new_path)
    try:
        return _run(old, new, old_path, new_path, start, end, cur, include_holdout=include_holdout, show=show,
                    do_hash=do_hash, expect_old=expect_old, data_version=data_version, progress_every=progress_every, t0=t0)
    finally:
        old.close()
        new.close()


def _run(old: ScoreStore, new: ScoreStore, old_path: Path, new_path: Path, start: str, end: str | None,
         cur: dict[str, str], *, include_holdout: bool, show: int, do_hash: bool, expect_old: dict[str, str] | None,
         data_version: str | None, progress_every: int, t0: float) -> dict:
    vio = Violations(show)
    diag = {"old": read_diag(old), "new": read_diag(new)}
    if data_version is not None:
        diag = {s: {k: v for k, v in d.items() if k[0] == data_version} for s, d in diag.items()}
    split: dict[str, dict[str, set]] = {}
    for side, d in diag.items():
        split[side] = {"before": set(), "in": set(), "after": set()}
        for (dv, date) in d:
            split[side][classify_date(date, start, end)].add((dv, date))
    in_old, in_new = split["old"]["in"], split["new"]["in"]
    if not in_new:
        raise PreconditionError(f"新側 {new_path} 在範圍 {start}～{end or '（不設上限）'} 內沒有任何 replay_day 列")

    # -- C6（前置條件；rc=2）--
    mvs = {side: {m: sorted({diag[side][k][f"model_version_{m}"] for k in split[side]["in"]} - {None}) for m in MARKETS}
           for side in ("old", "new")}
    for m in MARKETS:
        if mvs["new"][m] != [cur[m]]:
            raise PreconditionError(f"C6：新側 {m} 的 model_version＝{mvs['new'][m]}，應恰為現行碼的 {cur[m]}")
        if set(mvs["old"][m]) & set(mvs["new"][m]):
            raise PreconditionError(f"C6：舊側 {m} 的 model_version＝{mvs['old'][m]} 與新側相同——比到同一份模型")
        if expect_old is not None and mvs["old"][m] != [expect_old[m]]:
            raise PreconditionError(f"C6：舊側 {m} 的 model_version＝{mvs['old'][m]}，--expect-old 要求 {expect_old[m]}")

    # -- scores 與 replay_day 的日期一致（結構；rc=2）--
    for side, store in (("old", old), ("new", new)):
        have = scores_dates_in(store, start, end)
        listed = {date for (_dv, date) in split[side]["in"]}
        orphan = sorted(have - listed)
        if orphan and data_version is None:
            raise PreconditionError(f"{side}：scores 有 {len(orphan)} 日不在 replay_day（例 {orphan[:3]}）")

    # -- C1：日期集合 --
    for dv, date in sorted(in_old - in_new):
        vio.add("C1", f"{dv} {date}: 只在舊側")
    for dv, date in sorted(in_new - in_old):
        vio.add("C1", f"{dv} {date}: 只在新側")
    common = sorted(in_old & in_new)

    # -- C5 --
    unk = {"days_diff": 0, "sum_delta": 0, "max_abs_delta": 0}
    for k in common:
        a, b = diag["old"][k], diag["new"][k]
        for c in C5_EQUAL:
            if a[c] != b[c]:
                vio.add("C5", f"{k[0]} {k[1]} replay_day.{c}: 舊={a[c]!r} 新={b[c]!r}")
        if a["n_stock_any_unknown"] != b["n_stock_any_unknown"]:
            delta = (b["n_stock_any_unknown"] or 0) - (a["n_stock_any_unknown"] or 0)
            unk["days_diff"] += 1
            unk["sum_delta"] += delta
            unk["max_abs_delta"] = max(unk["max_abs_delta"], abs(delta))

    # -- 逐日串流：C1 鍵、C2、C3、C4、計數 --
    groups: dict[str, GroupAcc] = {}
    mrows: dict[str, dict[str, int]] = {}
    n_rows_total = 0
    for i, (dv, date) in enumerate(common, 1):
        ra, rb = read_day_rows(old, dv, date), read_day_rows(new, dv, date)
        for side, rows in (("old", ra), ("new", rb)):
            dg = diag[side][(dv, date)]
            for key, (mv, vals) in rows.items():
                _check_row_meta(side, key, mv, vals, dg)
        for key in sorted(ra.keys() - rb.keys()):
            vio.add("C1", f"{date} {key[0]}/{key[1]}/{key[2]}: 只在舊側")
        for key in sorted(rb.keys() - ra.keys()):
            vio.add("C1", f"{date} {key[0]}/{key[1]}/{key[2]}: 只在新側")
        for key in sorted(ra.keys() & rb.keys()):
            va, vb = ra[key][1], rb[key][1]
            gk = f"{key[0]}|{key[1]}"
            n_rows_total += 1
            if key[2] == MARKET_STOCK_ID:
                m = mrows.setdefault(gk, {"n_rows": 0, "n_diff_rows": 0})
                m["n_rows"] += 1
                if va != vb:
                    m["n_diff_rows"] += 1
                    vio.add("C2", f"{date} {key[0]}/{key[1]} 大盤列 {_first_diff(va, vb)}")
                continue
            g = groups.get(gk)
            if g is None:
                g = groups[gk] = GroupAcc(key[0])
            if va == vb:
                g.same_row(va)
            else:
                compare_stock_row(key, va, vb, g, vio)
        if progress_every and i % progress_every == 0:
            print(f"  … {i}/{len(common)} 日（{date}）列 {n_rows_total:,}", file=sys.stderr, flush=True)

    rc = 1 if vio.any() else 0
    order = {f"{m}|{h}": (i, j) for i, m in enumerate(MARKETS) for j, h in enumerate(("short", "swing", "mid"))}
    gkeys = sorted(set(groups) | set(mrows), key=lambda s: (*order.get(s, (9, 9)), s))
    return {
        "schema": SCHEMA, "tool": "scripts/model_diff.py", "result_rc": rc,
        "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "elapsed_s": round(time.perf_counter() - t0, 3),
        "rss_peak_mib": round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024, 1),
        "include_holdout": include_holdout, "range": {"start": start, "end": end},
        "data_version_filter": data_version,
        "allowed_lines": {m: list(v) for m, v in ALLOWED_LINES.items()},
        "current_model_versions": cur,
        "dbs": {side: {"path": str(p), "sha256": (sha256_file(p) if do_hash else None),
                       "params_sha": params_shas(s), "model_versions": mvs[side]}
                for side, p, s in (("old", old_path, old), ("new", new_path, new))},
        "days": {"compared": len(common),
                 "skipped_before_train": {s: len({d for _dv, d in split[s]["before"]}) for s in ("old", "new")},
                 "skipped_after_range": {s: len({d for _dv, d in split[s]["after"]}) for s in ("old", "new")}},
        "rows_compared": n_rows_total,
        "market_rows": {k: mrows[k] for k in gkeys if k in mrows},
        "groups": {k: groups[k].summary() for k in gkeys if k in groups},
        "replay_day": {"n_stock_any_unknown": unk,
                       "model_version_twse": {"old": mvs["old"]["twse"], "new": mvs["new"]["twse"]},
                       "model_version_tpex": {"old": mvs["old"]["tpex"], "new": mvs["new"]["tpex"]}},
        "invariants": {c: {"ok": vio.n[c] == 0, "n": vio.n[c], "examples": vio.examples[c]} for c in INVARIANTS},
    }


# ---------------------------------------------------------------------------
# 報告
# ---------------------------------------------------------------------------
def _f(x: Any, nd: int = 6) -> str:
    return "—" if x is None else (f"{x:.{nd}g}" if isinstance(x, float) else str(x))


def render_txt(rep: dict) -> str:
    L: list[str] = []
    rng = rep["range"]
    L.append("新舊 scores.db 模型換版比對（唯讀；docs/P3-CALIBRATION.md §33）")
    L.append(f"產生 {rep['generated_at']}｜耗時 {rep['elapsed_s']} s｜RSS 峰值 {rep['rss_peak_mib']} MiB")
    L.append(f"範圍 {rng['start']}～{rng['end'] or '（不設上限）'}｜保留段{'已納入（--include-holdout）' if rep['include_holdout'] else '未納入（預設；保留段未動用）'}")
    for side in ("old", "new"):
        d = rep["dbs"][side]
        L.append(f"{'舊' if side == 'old' else '新'}側 {d['path']}  sha256={d['sha256'] or '（--no-hash 省略）'}")
        L.append(f"    model_version twse={d['model_versions']['twse']} tpex={d['model_versions']['tpex']}  params_sha={d['params_sha']}")
    L.append(f"現行碼 model_version：{rep['current_model_versions']}")
    L.append(f"允許爻：{rep['allowed_lines']}")
    dd = rep["days"]
    L.append(f"比對 {dd['compared']} 日、{rep['rows_compared']:,} 列｜略過：訓練段前 舊 {dd['skipped_before_train']['old']}／新 "
             f"{dd['skipped_before_train']['new']} 日；範圍後 舊 {dd['skipped_after_range']['old']}／新 {dd['skipped_after_range']['new']} 日"
             "（範圍外不讀 scores 列）")
    L.append("")
    L.append("## 大盤列（C2）")
    for k, m in rep["market_rows"].items():
        L.append(f"  {k:<12} 比對 {m['n_rows']:,} 列、差異 {m['n_diff_rows']:,}")
    L.append("")
    L.append("## 個股列（依 market×horizon）")
    for k, g in rep["groups"].items():
        n = g["n_rows"]
        L.append(f"### {k}  比對 {n:,} 列、差異 {g['n_diff_rows']:,} 列")
        for ln, a in g["lines"].items():
            L.append(f"  爻{ln}: 分數變 {a['score_changed']:,}｜unknown 翻轉 {a['unknown_flip']:,}｜正式爻位翻轉 {a['formal_flip']:,}｜"
                     f"|Δ| n={a['abs_delta_n']:,}（非零 {a['abs_delta_nonzero']:,}） max={_f(a['abs_delta_max'])} "
                     f"p50={_f(a['abs_delta_p50'])} p99={_f(a['abs_delta_p99'])}")
        ratio = g["king_wen_changed_ratio"]
        rtxt = "—" if ratio is None else f"{ratio:.4%}"
        L.append(f"  base_score 變 {g['base_score_changed']:,}（max|Δ|={_f(g['base_score_max_abs_delta'])}）｜"
                 f"king_wen 變 {g['king_wen_changed']:,}（{rtxt}）｜"
                 f"king_wen_provisional 變 {g['king_wen_provisional_changed']:,}")
    L.append("")
    u = rep["replay_day"]["n_stock_any_unknown"]
    L.append(f"## replay_day（C5）n_stock_any_unknown 只報差：{u['days_diff']} 日不同、Σ(新−舊)={u['sum_delta']}、max|差|={u['max_abs_delta']}")
    L.append(f"   model_version_twse 舊 {rep['replay_day']['model_version_twse']['old']} → 新 {rep['replay_day']['model_version_twse']['new']}")
    L.append(f"   model_version_tpex 舊 {rep['replay_day']['model_version_tpex']['old']} → 新 {rep['replay_day']['model_version_tpex']['new']}")
    L.append("")
    L.append("## 不變式")
    for c, v in rep["invariants"].items():
        status = "OK" if v["ok"] else f"違反 {v['n']:,} 例"
        L.append(f"  {c}: {status}")
        for e in v["examples"]:
            L.append(f"      {e}")
    rc = rep["result_rc"]
    L.append("")
    L.append(f"結果：rc={rc}（{'全符合' if rc == 0 else '不變式違反'}）")
    return "\n".join(L) + "\n"


def write_report(rep: dict, out: Path) -> Path:
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(rep, ensure_ascii=False, indent=1, sort_keys=False) + "\n", encoding="utf-8")
    txt = out.with_suffix(".txt")
    txt.write_text(render_txt(rep), encoding="utf-8")
    return txt


def parse_expect_old(s: str | None) -> dict[str, str] | None:
    if s is None:
        return None
    out = {}
    for part in s.split(","):
        m, _, v = part.partition("=")
        if m not in MARKETS or not v:
            raise PreconditionError(f"--expect-old 格式應為 twse=<mv>,tpex=<mv>：{s!r}")
        out[m] = v
    if set(out) != set(MARKETS):
        raise PreconditionError(f"--expect-old 兩市場都要給：{s!r}")
    return out


def main(argv: Iterable[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="新舊 scores.db 模型換版比對（唯讀；鍵不含 model_version；rc 0 全符合／1 不變式違反／2 前置條件）")
    ap.add_argument("--new", default=str(REPO / "cache" / "scores.db"), help="新側（換版後重播）；預設 cache/scores.db")
    ap.add_argument("--old", default=str(REPO / "cache" / "scores_pre68.db"), help="舊側（換版前）；預設 cache/scores_pre68.db")
    ap.add_argument("--out", required=True, help="報告 JSON 路徑；同名 .txt 一併寫出")
    ap.add_argument("--include-holdout", action="store_true", help="納入保留段起的日子（預設不讀：登錄書保留段未動用）")
    ap.add_argument("--expect-old", default=None, help="驗舊側 model_version：twse=<mv>,tpex=<mv>")
    ap.add_argument("--data-version", default=None, help="只比這個 data_version（預設全部）")
    ap.add_argument("--no-hash", action="store_true", help="不算兩個 db 的 sha256")
    ap.add_argument("--show", type=int, default=20, help="每條不變式列前幾例（預設 20）")
    ap.add_argument("--progress-every", type=int, default=0, help="每 N 日印一次進度到 stderr（0＝不印）")
    args = ap.parse_args(list(argv) if argv is not None else None)
    try:
        rep = run(Path(args.old), Path(args.new), include_holdout=args.include_holdout, show=args.show,
                  do_hash=not args.no_hash, expect_old=parse_expect_old(args.expect_old),
                  data_version=args.data_version, progress_every=args.progress_every)
    except PreconditionError as e:
        print(f"[model_diff 中止 rc=2] {e}", file=sys.stderr)
        return 2
    except Exception as e:  # noqa: BLE001  未預期例外一律 rc=2（不得與 rc=1＝不變式違反混淆）
        traceback.print_exc()
        print(f"[model_diff 中止 rc=2] 未預期例外 {type(e).__name__}: {e}", file=sys.stderr)
        return 2
    txt = write_report(rep, Path(args.out))
    bad = [c for c, v in rep["invariants"].items() if not v["ok"]]
    print(f"== model_diff：比對 {rep['days']['compared']} 日、{rep['rows_compared']:,} 列｜"
          f"{'全符合' if not bad else '違反 ' + ','.join(bad)}｜報告 {args.out}、{txt}｜rc={rep['result_rc']}")
    return rep["result_rc"]


if __name__ == "__main__":
    raise SystemExit(main())
