#!/usr/bin/env python3
"""回測資料出口的**獨立抽驗器**（`docs/P3-DATASET.md` §2 C1～C4 的機器版）：對 `data/backtest/<segment>_<horizon>.csv.gz`
用**另一套實作**重算，抓 `scripts/export_dataset.py` 的系統性錯誤。

    python3 scripts/check_dataset.py --cache-dir cache --data-dir data/backtest --sample 300 --seed 7 --out cache/logs/check_dataset.json

**刻意不 import `export_dataset`、不複製它的任何函式**——本檔只認規格正本 `docs/P3-DATASET.md` §2 A4／A5 與 §5 的文字定義，
價格用 raw SQL 直讀、交易日序用 `data/calendar_tpe.json`、後復權係數只借 `src/iching/adjust.py` 的 `cumulative_factors`／`factor_at`
（規格指定的同一支）與 `src/iching/factor_sources.py` 的 `merge_factor_rows`（裁定 #51 四源合併規則的**唯一實作**，
`docs/P3-DATASET.md` §7.3 C 明定五處共用同一支純函式；本檔的獨立性是對 `export_dataset` 而言，不對 `adjust`／`factor_sources`），
每表內「同 (stock_id, date) 只取一列、內容衝突另計」仍自己寫。只用標準庫（Hetzner Python 3.14）。

## 檢查項

- **C0 manifest**（有 `manifest.json` 才做）：各檔 sha256／列數＝實檔；`h_by_horizon`／`segments`／`data_version`／`columns`／`data_end`
  ＝本檔獨立算出的值。
- **C1 列數與鍵**：每檔列數＝`scores.db` 該段×該 horizon 的個股列數（`scores` JOIN `versions` 取 `data_version`、
  `stock_id<>'__MARKET__'`）；鍵**集合**＝db（順序無關的 64 位雜湊和＋列數；另記與 db 排序序列第一個分歧的位置）；
  同段三個 horizon 檔的 `(date, market, stock_id)` 序列逐位相同（串流 sha256）；`date` 集合＝該段日曆日 ∩ 計分日。
- **C2 分數欄**：抽樣列的 `base_score`（`repr(float)`）／`in_rank_pool`／`coverage`／`king_wen`／`lines_formal` 對 `scores.db` 直讀。
- **C3 計算欄**：抽樣列的 `fwd_ret`／`mkt_ret_h`／`exit_reason`／`entry_limit_up`／`exit_limit_down` 自己重算（規則見 `recompute_row`）。

## 抽樣（每檔）

`--sample N` 列＝分層：`exit_reason ∈ {halt, delist, no_entry}` 各至少 `STRATA_MIN`（20）列、`entry_limit_up=1` 至少 20 列
（有多少抽多少），其餘由全檔隨機（水塘抽樣）補到 N；另**強制**抽該段內有還原事件（四源任一表的 `date` 落在段內）
的 `DIV_STOCKS`（30）檔，每檔最多 `DIV_ROWS_PER_STOCK`（3）列、優先取視窗跨過除權息日（T < ex ≤ x）的列。`--seed` 固定即可重現。
N ≥ 檔內列數時＝全檔逐列核對（報告 `sample_is_full=true`）。

## rc

0 全對／1 任一 mismatch（C0～C3）／2 中止（db 或檔案缺、`data_version` 解不出、欄序不對、日曆缺日…）。

## 已知的一處刻意分流（不算 mismatch、另計 `n_prev_close_before_window`）

漲跌停旗標的 `prev_close`＝該日前最後一個成交日原始收盤。出口只載入 `min(段起)` 起的價格列，所以「自段起到 e−1 都沒成交」的檔
它寫空字串；本檔用該檔**全部**歷史找前收（規格 §5 的字面定義）。兩邊在這種列會不同，但那是出口的載入區間，不是算式錯——
另計不混入 mismatch，報告裡列出鍵供人判斷。
"""
from __future__ import annotations

import argparse
import bisect
import csv
import gzip
import hashlib
import json
import random
import sqlite3
import sys
import time
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "src"))

from iching import factor_sources as FS  # noqa: E402
from iching.adjust import Event, cumulative_factors, factor_at  # noqa: E402

# 規格常數（docs/pre-registration.md:47-50、:55；docs/P3-DATASET.md §1）——自己寫、不從 export_dataset 取
SEGMENTS: dict[str, tuple[str, str]] = {"train": ("2021-01-01", "2023-06-30"), "valid": ("2023-07-01", "2024-12-31")}
H_BY_HORIZON: dict[str, int] = {"short": 10, "swing": 20, "mid": 40}
HORIZONS = ("short", "swing", "mid")
COLUMNS = ("date", "market", "stock_id", "horizon", "base_score", "in_rank_pool", "coverage", "king_wen", "lines_formal",
           "fwd_ret", "mkt_ret_h", "exit_reason", "entry_limit_up", "exit_limit_down")
CALC_COLS = COLUMNS[9:]
SCORE_CHECK_COLS = ("base_score", "in_rank_pool", "coverage", "king_wen", "lines_formal")
MARKET_STOCK_ID = "__MARKET__"
INDEX_ID = {"twse": "TAIEX", "tpex": "TPEx"}
PRICE_TABLE, INDEX_TABLE, DIV_TABLE = "raw_price_daily", "raw_index_price", "raw_dividend_result"
VOL_COL = "Trading_Volume"
ROUND_DIGITS = 6
LIMIT_UP, LIMIT_DOWN = 1.10, 0.90
STRATA_MIN = 20
DIV_STOCKS = 30
DIV_ROWS_PER_STOCK = 3
N_EXAMPLES = 10
STATE_FILE = "scores.db.state.json"
_MASK64 = (1 << 64) - 1


class CheckAbort(RuntimeError):
    pass


# ---------------------------------------------------------------------------
# 小工具
def _num(v: Any) -> float | None:
    """sqlite 值 → float；None／bool／非數 → None。"""
    if v is None or isinstance(v, bool):
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def fmt_ret(v: float) -> str:
    """§5：`round(v, 6)`、`-0.0` 正規化，字串＝`repr`。"""
    return repr(round(v, ROUND_DIGITS) + 0.0)


def score_cell(v: Any) -> str:
    """分數欄「零轉換」的字串形：None → ""、float → repr、其餘 str。"""
    if v is None:
        return ""
    if isinstance(v, float):
        return repr(v)
    return str(v)


def is_traded(close: Any, vol: Any) -> bool:
    """成交列＝`close > 0` 且 `Trading_Volume > 0`（`universe.is_traded_row` 的規則，含 `or 0`／非數即 False 的語意）。"""
    try:
        c = float(close or 0)
        v = float(vol or 0)
    except (TypeError, ValueError):
        return False
    return c > 0 and v > 0


def key_hash(date: str, market: str, sid: str) -> int:
    return int.from_bytes(hashlib.blake2b(f"{date}\x1f{market}\x1f{sid}".encode(), digest_size=8).digest(), "big")


def reservoir(res: list, cap: int, seen: int, item: Any, rng: random.Random) -> None:
    """Algorithm R：`seen` 是含本件在內的第幾件（1 起算）。"""
    if len(res) < cap:
        res.append(item)
    else:
        j = rng.randrange(seen)
        if j < cap:
            res[j] = item


def open_ro(path: Path) -> sqlite3.Connection:
    if not path.exists():
        raise CheckAbort(f"找不到 DB：{path}")
    return sqlite3.connect(f"file:{path}?mode=ro", uri=True)


def table_cols(conn: sqlite3.Connection, table: str) -> list[str]:
    return [r[1] for r in conn.execute(f'PRAGMA table_info("{table}")')]


def resolve_dv(conn: sqlite3.Connection, cache: Path, wanted: str | None) -> str:
    have = [str(r[0]) for r in conn.execute("SELECT data_version FROM replay_meta ORDER BY data_version")]
    if wanted is not None:
        if wanted not in have:
            raise CheckAbort(f"scores.db 沒有 data_version={wanted}（有：{have}）")
        return wanted
    if len(have) == 1:
        return have[0]
    sp = cache / STATE_FILE
    if sp.exists():
        try:
            dv = json.loads(sp.read_text(encoding="utf-8")).get("meta", {}).get("data_version")
        except (OSError, ValueError, AttributeError):
            dv = None
        if dv in have:
            return str(dv)
    raise CheckAbort(f"replay_meta 有 {len(have)} 個 data_version（{have}），請以 --data-version 指定")


# ---------------------------------------------------------------------------
class StockPx:
    """一檔的價格列，以日曆位置索引（與出口的 array 實作不同：dict＋bisect）。"""

    def __init__(self) -> None:
        self.rows: dict[int, tuple[float | None, float | None, Any]] = {}     # pos → (open, close, vol)
        self.traded_pos: list[int] = []
        self.row_pos: list[int] = []
        self.n_off_calendar = 0
        self.conflicts: set[int] = set()                                       # 同 (sid, date) 多列且內容不同

    def add(self, pos: int | None, o: Any, c: Any, v: Any) -> None:
        if pos is None:
            self.n_off_calendar += 1
            return
        item = (_num(o), _num(c), v)
        old = self.rows.get(pos)
        if old is not None:
            if (old[0], old[1], is_traded(old[1], old[2])) != (item[0], item[1], is_traded(item[1], item[2])):
                self.conflicts.add(pos)
            return
        self.rows[pos] = item

    def finalize(self) -> None:
        self.row_pos = sorted(self.rows)
        self.traded_pos = [p for p in self.row_pos if is_traded(self.rows[p][1], self.rows[p][2])]

    def traded(self, p: int) -> bool:
        i = bisect.bisect_left(self.traded_pos, p)
        return i < len(self.traded_pos) and self.traded_pos[i] == p

    def last_traded_lt(self, p: int) -> int | None:
        i = bisect.bisect_left(self.traded_pos, p)
        return self.traded_pos[i - 1] if i else None

    def last_traded_le(self, p: int) -> int | None:
        i = bisect.bisect_right(self.traded_pos, p)
        return self.traded_pos[i - 1] if i else None

    def last_row_le(self, p: int) -> int:
        i = bisect.bisect_right(self.row_pos, p)
        return self.row_pos[i - 1] if i else -1


class World:
    """重算所需的全部上下文：日曆、data_end、指數、係數；價格列按需載入（只載抽到的檔）。"""

    def __init__(self, prices: sqlite3.Connection, dv: str, cal: list[str]) -> None:
        self.prices, self.dv, self.cal = prices, dv, cal
        self.pos = {d: i for i, d in enumerate(cal)}
        self.idx: dict[str, dict[int, tuple[float | None, float | None]]] = {m: {} for m in INDEX_ID}
        self.idx_conflicts: dict[str, int] = {m: 0 for m in INDEX_ID}
        rev = {v: k for k, v in INDEX_ID.items()}
        taiex_last: str | None = None
        for sid, d, o, c in prices.execute(f'SELECT stock_id, date, open, close FROM "{INDEX_TABLE}" WHERE data_version=?', (dv,)):
            m = rev.get(str(sid))
            if m is None:
                continue
            if m == "twse" and (taiex_last is None or str(d) > taiex_last):
                taiex_last = str(d)
            p = self.pos.get(str(d))
            if p is None:
                continue
            item = (_num(o), _num(c))
            if p in self.idx[m] and self.idx[m][p] != item:
                self.idx_conflicts[m] += 1
            self.idx[m].setdefault(p, item)
        if taiex_last is None:
            raise CheckAbort(f"{INDEX_TABLE} 沒有 {INDEX_ID['twse']}（data_version={dv}）的列")
        self.taiex_last = taiex_last
        self.data_end = min(cal[-1], taiex_last)
        if self.data_end not in self.pos:
            raise CheckAbort(f"data_end={self.data_end} 不在日曆裡（TAIEX 末日不是交易日？）")
        self.data_end_pos = self.pos[self.data_end]
        self.factors, self.factor_stats, self.div_events = self._load_factors()
        self.px: dict[str, StockPx] = {}

    def _load_factors(self) -> tuple[dict[str, tuple[list[str], list[float]]], dict[str, Any], dict[str, list[str]]]:
        """四個事件源（`factor_sources.SOURCES`）：每表內同 (stock_id, date) 只取一列（內容衝突另計）→ `merge_factor_rows`
        （split∪parvalue 去重優先 split、跨源同日各留）→ 非數或 ≤0 跳過 → `adjust.cumulative_factors`（同日相乘）。
        缺表視為 0 列（記 `missing_tables`）；`raw_dividend_result` 缺欄仍中止（它是主表）。"""
        by: dict[str, list] = {}
        tables: dict[str, list[str]] = {}
        missing: list[str] = []
        n_rows = n_conflict = 0
        for spec in FS.SOURCES:
            cols = table_cols(self.prices, spec.table)
            if not cols:
                missing.append(spec.table)
                by[spec.source] = []
                continue
            need = {"stock_id", "date", spec.before, spec.after} - set(cols)
            if need:
                raise CheckAbort(f"{spec.table} 缺欄位 {sorted(need)}；實際＝{cols}")
            tables[spec.table] = cols
            first: dict[tuple[str, str], tuple[Any, Any]] = {}
            q = (f'SELECT stock_id, date, "{spec.before}", "{spec.after}" FROM "{spec.table}" '
                 f"WHERE data_version=? AND date IS NOT NULL")
            for sid, d, b, a in self.prices.execute(q, (self.dv,)):
                n_rows += 1
                k = (str(sid), str(d))
                if k in first:
                    if first[k] != (b, a):
                        n_conflict += 1
                    continue
                first[k] = (b, a)
            by[spec.source] = [(sid, d, b, a) for (sid, d), (b, a) in sorted(first.items())]
        if DIV_TABLE in missing:
            raise CheckAbort(f"{DIV_TABLE} 不存在（data_version={self.dv}）")
        merged, mstat = FS.merge_factor_rows(by)
        evs: dict[str, list[Event]] = {}
        n_bad = 0
        for sid, d, b, a, _src in merged:
            bf, af = _num(b), _num(a)
            if bf is None or af is None or bf <= 0 or af <= 0:
                n_bad += 1
                continue
            evs.setdefault(sid, []).append(Event(d, bf, af))
        fac = {sid: cumulative_factors(e) for sid, e in evs.items()}
        ev_dates = {sid: sorted({e.ex_date for e in e_}) for sid, e_ in evs.items()}
        stats = {"rows": n_rows, "distinct_keys": sum(len(v) for v in by.values()), "conflicting_duplicates": n_conflict,
                 "bad_skipped": n_bad + sum(s["bad_skipped"] for s in mstat["by_source"].values()),
                 "stocks": len(fac), "table_info_columns": tables.get(DIV_TABLE, []), "tables": tables, "missing_tables": missing,
                 "sources": mstat["sources"], "by_source": mstat["by_source"], "cross_source_dup": mstat["cross_source_dup"],
                 "merged_rows": len(merged), "anomalies": mstat["anomalies"], "anomaly_rows": mstat["anomaly_rows"]}
        return fac, stats, ev_dates

    def load_stocks(self, sids: set[str]) -> None:
        need = sorted(s for s in sids if s not in self.px)
        for s in need:
            self.px[s] = StockPx()
        q = f'SELECT stock_id, date, open, close, "{VOL_COL}" FROM "{PRICE_TABLE}" WHERE data_version=? AND stock_id IN ({{}})'
        for i in range(0, len(need), 500):
            chunk = need[i:i + 500]
            for sid, d, o, c, v in self.prices.execute(q.format(",".join("?" * len(chunk))), (self.dv, *chunk)):
                self.px[str(sid)].add(self.pos.get(str(d)), o, c, v)
        for s in need:
            self.px[s].finalize()

    # -- 重算 --
    def adj(self, sid: str, pos: int, raw: float) -> float:
        fac = self.factors.get(sid)
        return raw if fac is None else raw * factor_at(self.cal[pos], fac[0], fac[1])

    def mkt_ret(self, market: str, e: int, xp: int) -> str:
        m = self.idx.get(market)
        if not m:
            return ""
        o, c = m.get(e, (None, None))[0], m.get(xp, (None, None))[1]
        if o is None or o <= 0 or c is None:
            return ""
        return fmt_ret(c / o - 1.0)

    def limit_flag(self, stk: StockPx, p: int, price: float | None, mult: float, up: bool) -> tuple[str, int | None]:
        """回 (旗標字串, 前收位置)；前收＝p 之前最後一個成交日的原始收盤。"""
        q = stk.last_traded_lt(p)
        if q is None or price is None:
            return "", q
        prev = stk.rows[q][1]
        if prev is None or prev <= 0:
            return "", q
        lim = round(prev * mult, 2)
        return ("1" if (price >= lim if up else price <= lim) else "0"), q

    def recompute_row(self, sid: str, market: str, t_pos: int, h: int) -> tuple[dict[str, str], dict[str, Any]]:
        """§5 的規則逐條：e=T+1、x=T+1+h；回 (五欄字串, 附註{prev_close 位置、衝突…})。"""
        out = {c: "" for c in CALC_COLS}
        note: dict[str, Any] = {}
        stk = self.px[sid]
        e, x = t_pos + 1, t_pos + 1 + h
        if e > self.data_end_pos:
            return out, note
        row_e = stk.rows.get(e)
        o_e = row_e[0] if row_e is not None else None
        can_enter = row_e is not None and stk.traded(e) and o_e is not None and o_e > 0
        if can_enter:
            out["entry_limit_up"], note["entry_prev_pos"] = self.limit_flag(stk, e, o_e, LIMIT_UP, up=True)
        else:
            out["exit_reason"] = "no_entry"
        if x > self.data_end_pos:
            return out, note
        if not can_enter:
            out["mkt_ret_h"] = self.mkt_ret(market, e, x)
            return out, note
        if stk.traded(x):
            xp = x
        else:
            xp = stk.last_traded_le(x)
            assert xp is not None and xp >= e
            last = stk.last_row_le(self.data_end_pos)
            out["exit_reason"] = "delist" if (last < x and last < self.data_end_pos) else "halt"
        c_xp = stk.rows[xp][1]
        ep, xv = self.adj(sid, e, o_e), self.adj(sid, xp, c_xp)
        if ep > 0:
            out["fwd_ret"] = fmt_ret(xv / ep - 1.0)
        out["exit_limit_down"], note["exit_prev_pos"] = self.limit_flag(stk, xp, c_xp, LIMIT_DOWN, up=False)
        out["mkt_ret_h"] = self.mkt_ret(market, e, xp)
        note["used_pos"] = [e, xp]
        return out, note


# ---------------------------------------------------------------------------
class FileScan:
    """一個檔的第一趟：列數、鍵雜湊、與 db 鍵序列 lockstep、date 集合、分層水塘。"""

    def __init__(self, name: str, seg: str, hz: str, n_sample: int, rng: random.Random, div_sids: set[str]) -> None:
        self.name, self.seg, self.hz, self.rng, self.div_sids = name, seg, hz, rng, div_sids
        self.n_sample = n_sample
        self.n_rows = 0
        self.seq_sha = hashlib.sha256()
        self.set_hash = 0
        self.dates: set[str] = set()
        self.exit_counts: dict[str, int] = {}
        self.res: dict[str, list[list[str]]] = {"halt": [], "delist": [], "no_entry": [], "limit_up": [], "random": []}
        self.seen: dict[str, int] = {k: 0 for k in self.res}
        self.div_res: dict[str, dict[str, list[list[str]]]] = {s: {"straddle": [], "any": []} for s in div_sids}
        self.div_seen: dict[str, dict[str, int]] = {s: {"straddle": 0, "any": 0} for s in div_sids}
        self.first_divergence: dict[str, Any] | None = None
        self.header_ok = True

    def offer(self, stratum: str, row: list[str]) -> None:
        self.seen[stratum] += 1
        cap = self.n_sample if stratum == "random" else STRATA_MIN
        reservoir(self.res[stratum], cap, self.seen[stratum], row, self.rng)

    def offer_div(self, sid: str, kind: str, row: list[str]) -> None:
        self.div_seen[sid][kind] += 1
        reservoir(self.div_res[sid][kind], DIV_ROWS_PER_STOCK, self.div_seen[sid][kind], row, self.rng)

    def picked(self) -> tuple[list[list[str]], dict[str, int]]:
        """合併各層（同鍵去重）；隨機層補到 N；除權息強制層另加。"""
        out: dict[tuple, list[str]] = {}
        comp: dict[str, int] = {}
        for k in ("halt", "delist", "no_entry", "limit_up"):
            for r in self.res[k]:
                out.setdefault(tuple(r[:4]), r)
            comp[k] = len(self.res[k])
        n_before = len(out)
        for r in self.res["random"]:
            if len(out) >= self.n_sample:
                break
            out.setdefault(tuple(r[:4]), r)
        comp["random_fill"] = len(out) - n_before
        n_before = len(out)
        for sid in sorted(self.div_res):
            rows = self.div_res[sid]["straddle"] or self.div_res[sid]["any"][:1]
            for r in rows:
                out.setdefault(tuple(r[:4]), r)
        comp["dividend_forced"] = len(out) - n_before
        comp["div_stocks_with_rows"] = sum(1 for s in self.div_res if self.div_res[s]["straddle"] or self.div_res[s]["any"])
        comp["div_stocks_straddling"] = sum(1 for s in self.div_res if self.div_res[s]["straddle"])
        return list(out.values()), comp


def scan_file(path: Path, fs: FileScan, db_keys, world: World, h: int) -> None:
    with gzip.open(path, "rt", encoding="utf-8", newline="") as g:
        rd = csv.reader(g)
        header = next(rd, None)
        if header is None or tuple(header) != COLUMNS:
            raise CheckAbort(f"{path.name} 欄序不對：{header}")
        for row in rd:
            if len(row) != len(COLUMNS):
                raise CheckAbort(f"{path.name} 第 {fs.n_rows + 2} 行欄數 {len(row)} ≠ {len(COLUMNS)}")
            fs.n_rows += 1
            d, market, sid, hz = row[0], row[1], row[2], row[3]
            if hz != fs.hz:
                raise CheckAbort(f"{path.name} 第 {fs.n_rows + 1} 行 horizon={hz!r} 不是 {fs.hz!r}")
            fs.seq_sha.update(f"{d},{market},{sid}\n".encode())
            fs.set_hash = (fs.set_hash + key_hash(d, market, sid)) & _MASK64
            fs.dates.add(d)
            fs.exit_counts[row[11]] = fs.exit_counts.get(row[11], 0) + 1
            if fs.first_divergence is None:
                dbk = next(db_keys, None)
                if dbk is None or (str(dbk[0]), str(dbk[1]), str(dbk[2])) != (d, market, sid):
                    fs.first_divergence = {"row": fs.n_rows, "file": [d, market, sid],
                                           "db": None if dbk is None else [str(v) for v in dbk]}
            reason = row[11]
            if reason in ("halt", "delist", "no_entry"):
                fs.offer(reason, row)
            if row[12] == "1":
                fs.offer("limit_up", row)
            fs.offer("random", row)
            if sid in fs.div_sids:
                t = world.pos.get(d)
                straddle = False
                if t is not None:
                    x_date = world.cal[t + 1 + h] if t + 1 + h < len(world.cal) else "9999-99-99"
                    straddle = any(d < ex <= x_date for ex in world.div_events.get(sid, ()))
                fs.offer_div(sid, "straddle" if straddle else "any", row)
        if fs.first_divergence is None:
            dbk = next(db_keys, None)
            if dbk is not None:
                fs.first_divergence = {"row": fs.n_rows + 1, "file": None, "db": [str(v) for v in dbk]}


# ---------------------------------------------------------------------------
def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


class Tally:
    def __init__(self) -> None:
        self.n_checked = 0
        self.n_mismatch = 0
        self.examples: list[dict[str, Any]] = []

    def add(self, ok: bool, example: dict[str, Any] | None = None) -> None:
        self.n_checked += 1
        if not ok:
            self.n_mismatch += 1
            if example is not None and len(self.examples) < N_EXAMPLES:
                self.examples.append(example)

    def as_dict(self) -> dict[str, Any]:
        return {"n_checked": self.n_checked, "n_mismatch": self.n_mismatch, "examples": self.examples}


def parse_kv(spec: str | None, base: dict, conv) -> dict:
    out = dict(base)
    if not spec:
        return out
    for part in spec.split(","):
        if not part.strip():
            continue
        k, _, v = part.partition("=")
        if not _:
            raise CheckAbort(f"無法解析 {part!r}（要 key=value）")
        out[k.strip()] = conv(v.strip())
    return out


def _seg_range(v: str) -> tuple[str, str]:
    a, _, b = v.partition(":")
    if not _ or not a or not b:
        raise CheckAbort(f"段範圍要 from:to，得到 {v!r}")
    return a, b


def run(args: argparse.Namespace) -> int:
    t0 = time.perf_counter()
    cache, data_dir = Path(args.cache_dir), Path(args.data_dir)
    segments = parse_kv(args.segments, SEGMENTS, _seg_range)
    h_by = parse_kv(args.h, H_BY_HORIZON, int)
    report: dict[str, Any] = {"argv": sys.argv[1:], "segments": segments, "h_by_horizon": h_by, "sample": args.sample, "seed": args.seed,
                              "files": {}, "checks": {}, "notes": []}
    scores = prices = None
    try:
        scores = open_ro(cache / "scores.db")
        prices = open_ro(cache / "prices.db")
        dv = resolve_dv(scores, cache, args.data_version)
        row = scores.execute("SELECT params_sha FROM replay_meta WHERE data_version=?", (dv,)).fetchone()
        report["data_version"], report["params_sha"] = dv, (None if row is None else str(row[0]))
        pcols = table_cols(prices, PRICE_TABLE)
        missing = {"stock_id", "date", "open", "close", VOL_COL} - set(pcols)
        if missing:
            raise CheckAbort(f"{PRICE_TABLE} 缺欄位 {sorted(missing)}；實際＝{pcols}")
        if not prices.execute(f'SELECT 1 FROM "{PRICE_TABLE}" WHERE data_version=? LIMIT 1', (dv,)).fetchone():
            raise CheckAbort(f"{PRICE_TABLE} 沒有 data_version={dv} 的列")
        cal_path = Path(args.calendar)
        if not cal_path.exists():
            raise CheckAbort(f"找不到日曆：{cal_path}")
        cal = [str(x) for x in (json.loads(cal_path.read_text(encoding="utf-8")).get("dates") or [])]
        if not cal or cal != sorted(set(cal)):
            raise CheckAbort(f"日曆 {cal_path} 空或非嚴格遞增")
        world = World(prices, dv, cal)
        report["calendar"] = {"path": str(cal_path), "first": cal[0], "last": cal[-1], "n": len(cal)}
        report["data_end"], report["taiex_last"] = world.data_end, world.taiex_last
        report["dividend_table"] = world.factor_stats
        report["index_conflicting_duplicates"] = world.idx_conflicts
        print(f"data_version={dv} params_sha={report['params_sha']} data_end={world.data_end}（日曆末 {cal[-1]}／TAIEX 末 {world.taiex_last}）")
        print(f"PRAGMA table_info({DIV_TABLE}) 欄名：{world.factor_stats['table_info_columns']}；"
              f"係數 {{rows:{world.factor_stats['rows']}, stocks:{world.factor_stats['stocks']}, "
              f"conflicting_duplicates:{world.factor_stats['conflicting_duplicates']}, bad_skipped:{world.factor_stats['bad_skipped']}}}")
        print(FS.format_source_stat(world.factor_stats))
        if world.factor_stats["missing_tables"]:
            report["notes"].append(f"還原事件源缺表（視為 0 列）：{world.factor_stats['missing_tables']}")
        if world.factor_stats["conflicting_duplicates"]:
            report["notes"].append(f"還原事件源四表有 {world.factor_stats['conflicting_duplicates']} 組同 (stock_id,date) 而 before/after 不同的重複列："
                                   "出口「取第一列」的結果取決於 SQL 掃描順序，該檔的係數不具決定性")

        # -- 有哪些檔 --
        present = [(s, hz) for s in segments for hz in HORIZONS if (data_dir / f"{s}_{hz}.csv.gz").exists()]
        if not present:
            raise CheckAbort(f"{data_dir} 沒有任何 <segment>_<horizon>.csv.gz")
        absent = [f"{s}_{hz}.csv.gz" for s in segments for hz in HORIZONS if (s, hz) not in present]
        if absent:
            report["notes"].append(f"缺檔（未檢查）：{absent}")
        n_mismatch_total = 0

        # -- C0 manifest --
        mpath = data_dir / "manifest.json"
        c0 = Tally()
        if mpath.exists():
            m = json.loads(mpath.read_text(encoding="utf-8"))
            for name in [f"{s}_{hz}.csv.gz" for s, hz in present]:
                f = (m.get("files") or {}).get(name)
                if f is None:
                    c0.add(False, {"field": "files", "name": name, "manifest": None, "actual": "exists"})
                    continue
                p = data_dir / name
                c0.add(f.get("sha256") == sha256_file(p), {"field": "sha256", "name": name, "manifest": f.get("sha256"), "actual": sha256_file(p)})
            for field, mine in (("h_by_horizon", h_by), ("data_version", dv), ("columns", list(COLUMNS)), ("round_digits", ROUND_DIGITS)):
                c0.add(m.get(field) == mine, {"field": field, "manifest": m.get(field), "actual": mine})
            c0.add((m.get("calendar") or {}).get("data_end") == world.data_end,
                   {"field": "calendar.data_end", "manifest": (m.get("calendar") or {}).get("data_end"), "actual": world.data_end})
            for s in {s for s, _ in present}:
                ms = (m.get("segments") or {}).get(s) or {}
                c0.add((ms.get("from"), ms.get("to")) == tuple(segments[s]),
                       {"field": f"segments.{s}", "manifest": [ms.get("from"), ms.get("to")], "actual": list(segments[s])})
            report["checks"]["C0_manifest"] = c0.as_dict()
            n_mismatch_total += c0.n_mismatch
            print(f"C0 manifest：{c0.n_checked} 項核對、{c0.n_mismatch} 項不符")
        else:
            report["notes"].append("沒有 manifest.json，C0 略過")
            print("C0 manifest：略過（無 manifest.json）")

        # -- 除權息候選檔（每段） --
        rng_master = random.Random(args.seed)
        div_by_seg: dict[str, set[str]] = {}
        for s in segments:
            f0, f1 = segments[s]
            cands = sorted({sid for sid, evs in world.div_events.items() if any(f0 <= ex <= f1 for ex in evs)})
            div_by_seg[s] = set(rng_master.sample(cands, min(DIV_STOCKS, len(cands))))

        # -- C1：每檔第一趟 --
        scans: dict[tuple[str, str], FileScan] = {}
        c1 = Tally()
        count_sql = ("SELECT COUNT(*) FROM scores s JOIN versions v ON v.version_id = s.version_id "
                     "WHERE v.data_version=? AND s.horizon=? AND s.date>=? AND s.date<=? AND s.stock_id<>?")
        keys_sql = ("SELECT s.date, s.market, s.stock_id FROM scores s JOIN versions v ON v.version_id = s.version_id "
                    "WHERE v.data_version=? AND s.horizon=? AND s.date>=? AND s.date<=? AND s.stock_id<>? ORDER BY s.date, s.market, s.stock_id")
        for s, hz in present:
            name = f"{s}_{hz}.csv.gz"
            f0, f1 = segments[s]
            if hz not in h_by:
                raise CheckAbort(f"horizon {hz!r} 沒有 h（--h）")
            fs = FileScan(name, s, hz, args.sample, random.Random(f"{args.seed}:{name}"), div_by_seg[s])
            db_n = scores.execute(count_sql, (dv, hz, f0, f1, MARKET_STOCK_ID)).fetchone()[0]
            db_set = 0
            db_dates: set[str] = set()

            def db_keys_iter():
                nonlocal db_set
                for r in scores.execute(keys_sql, (dv, hz, f0, f1, MARKET_STOCK_ID)):
                    db_set = (db_set + key_hash(str(r[0]), str(r[1]), str(r[2]))) & _MASK64
                    db_dates.add(str(r[0]))
                    yield r

            it = db_keys_iter()
            scan_file(data_dir / name, fs, it, world, h_by[hz])
            for _ in it:                                                   # 檔比 db 短時把 db 的雜湊算完
                pass
            scans[(s, hz)] = fs
            cal_days = {d for d in cal if f0 <= d <= f1}
            info = {"n_rows": fs.n_rows, "db_rows": db_n, "key_set_equal_db": fs.set_hash == db_set and fs.n_rows == db_n,
                    "first_divergence_vs_db_order": fs.first_divergence, "key_seq_sha256": fs.seq_sha.hexdigest(),
                    "n_dates": len(fs.dates), "n_calendar_days_in_segment": len(cal_days),
                    "dates_not_in_calendar": sorted(fs.dates - cal_days)[:N_EXAMPLES],
                    "calendar_days_without_rows": sorted(cal_days - fs.dates)[:N_EXAMPLES],
                    "dates_equal_db": fs.dates == db_dates, "exit_reason_counts_all": fs.exit_counts}
            report["files"][name] = info
            c1.add(fs.n_rows == db_n, {"name": name, "field": "n_rows", "file": fs.n_rows, "db": db_n})
            c1.add(info["key_set_equal_db"], {"name": name, "field": "key_set", "first_divergence": fs.first_divergence})
            c1.add(not info["dates_not_in_calendar"], {"name": name, "field": "dates_not_in_calendar", "file": info["dates_not_in_calendar"]})
            c1.add(info["dates_equal_db"], {"name": name, "field": "dates_vs_db", "file_only": sorted(fs.dates - db_dates)[:5],
                                            "db_only": sorted(db_dates - fs.dates)[:5]})
            if fs.first_divergence is not None:
                report["notes"].append(f"{name} 與 db 排序序列在第 {fs.first_divergence['row']} 列分歧（集合仍以雜湊和另判）")
            print(f"C1 {name}：{fs.n_rows} 列（db {db_n}）鍵集合{'＝' if info['key_set_equal_db'] else '≠'}db；"
                  f"date {len(fs.dates)} 日（段內日曆 {len(cal_days)} 日、無列 {len(cal_days - fs.dates)} 日）；exit={fs.exit_counts}")
        for s in segments:
            shas = {hz: scans[(s, hz)].seq_sha.hexdigest() for hz in HORIZONS if (s, hz) in scans}
            if len(shas) > 1:
                same = len(set(shas.values())) == 1
                c1.add(same, {"name": s, "field": "key_seq_across_horizons", "sha256": shas})
                print(f"C1 {s}：三檔鍵序列{'相同' if same else '不同'}")
        report["checks"]["C1_rows_and_keys"] = c1.as_dict()
        n_mismatch_total += c1.n_mismatch

        # -- 抽樣 → 載價格 --
        picked: dict[tuple[str, str], tuple[list[list[str]], dict[str, int]]] = {k: fs.picked() for k, fs in scans.items()}
        sids = {r[2] for rows, _ in picked.values() for r in rows}
        world.load_stocks(sids)
        n_conflict_px = sum(len(world.px[s].conflicts) for s in sids)
        n_off = sum(world.px[s].n_off_calendar for s in sids)
        report["price_rows_loaded"] = {"stocks": len(sids), "rows": sum(len(world.px[s].rows) for s in sids),
                                       "conflicting_duplicates": n_conflict_px, "off_calendar": n_off}
        if n_conflict_px:
            report["notes"].append(f"{PRICE_TABLE} 抽到的 {len(sids)} 檔中有 {n_conflict_px} 個 (stock_id,date) 多列且內容不同：出口「後寫覆蓋」不具決定性")

        # -- C2／C3 --
        c2, c3 = Tally(), {c: Tally() for c in CALC_COLS}
        n_prev_window = 0
        prev_window_examples: list[list[str]] = []
        export_load_from = min(v[0] for v in segments.values())
        score_sql = ("SELECT s.base_score, s.in_rank_pool, s.coverage, s.king_wen, s.lines_formal FROM scores s "
                     "JOIN versions v ON v.version_id = s.version_id WHERE v.data_version=? AND s.date=? AND s.market=? AND s.stock_id=? AND s.horizon=?")
        vids = [r[0] for r in scores.execute("SELECT version_id FROM versions WHERE data_version=?", (dv,))]
        for (s, hz), (rows, comp) in picked.items():
            name = f"{s}_{hz}.csv.gz"
            comp_exit: dict[str, int] = {}
            for row in rows:
                key = row[:4]
                d, market, sid = key[0], key[1], key[2]
                comp_exit[row[11]] = comp_exit.get(row[11], 0) + 1
                # C2
                got = scores.execute(score_sql, (dv, d, market, sid, hz)).fetchall()
                if len(got) != 1:
                    c2.add(False, {"key": key, "field": "db_rows", "file": "1 列", "db": f"{len(got)} 列（versions {vids}）"})
                else:
                    want = [score_cell(v) for v in got[0]]
                    have = [row[COLUMNS.index(c)] for c in SCORE_CHECK_COLS]
                    bad = [(c, h_, w) for c, h_, w in zip(SCORE_CHECK_COLS, have, want) if h_ != w]
                    c2.add(not bad, {"key": key, "fields": [{"field": c, "file": h_, "db": w} for c, h_, w in bad]} if bad else None)
                # C3
                t_pos = world.pos.get(d)
                if t_pos is None:
                    for c in CALC_COLS:
                        c3[c].add(False, {"key": key, "field": c, "file": row[COLUMNS.index(c)], "recomputed": "（T 不在日曆）"})
                    continue
                stk = world.px[sid]
                mine, note = world.recompute_row(sid, market, t_pos, h_by[hz])
                used = set(note.get("used_pos", ())) | {t_pos + 1}
                ambiguous = bool(stk.conflicts & used)
                for c in CALC_COLS:
                    have_v = row[COLUMNS.index(c)]
                    ok = have_v == mine[c]
                    if not ok and c in ("entry_limit_up", "exit_limit_down") and have_v == "" and mine[c] != "":
                        q = note.get("entry_prev_pos" if c == "entry_limit_up" else "exit_prev_pos")
                        if q is not None and world.cal[q] < export_load_from:
                            n_prev_window += 1
                            if len(prev_window_examples) < N_EXAMPLES:
                                prev_window_examples.append([*key, c, world.cal[q]])
                            continue
                    ex = {"key": key, "field": c, "file": have_v, "recomputed": mine[c]}
                    if ambiguous:
                        ex["note"] = "價格列有內容不同的重複列，出口取哪列不確定"
                    c3[c].add(ok, ex)
            report["files"][name]["sample"] = {"n": len(rows), "sample_is_full": len(rows) >= scans[(s, hz)].n_rows,
                                               "composition": comp, "exit_reason_counts": comp_exit}
            print(f"抽樣 {name}：{len(rows)} 列 {comp}；exit={comp_exit}")
        report["checks"]["C2_score_columns"] = c2.as_dict()
        report["checks"]["C3_computed_columns"] = {c: t.as_dict() for c, t in c3.items()}
        report["checks"]["C3_computed_columns"]["_total"] = {"n_checked": sum(t.n_checked for t in c3.values()),
                                                            "n_mismatch": sum(t.n_mismatch for t in c3.values())}
        report["n_prev_close_before_window"] = {"n": n_prev_window, "examples": prev_window_examples,
                                                "export_load_from": export_load_from}
        n_mismatch_total += c2.n_mismatch + sum(t.n_mismatch for t in c3.values())
        print(f"C2 分數欄：{c2.n_checked} 列、{c2.n_mismatch} 列不符")
        for c, t in c3.items():
            print(f"C3 {c}：{t.n_checked} 列、{t.n_mismatch} 列不符" + (f"；例 {t.examples[0]}" if t.examples else ""))
        if n_prev_window:
            print(f"（另 {n_prev_window} 格旗標：前收落在出口載入區間 {export_load_from} 之前，出口寫空、本檔有值；不計 mismatch）")
        rc = 1 if n_mismatch_total else 0
    except CheckAbort as e:
        print(f"[check_dataset 中止] {e}", file=sys.stderr)
        report["abort"] = str(e)
        rc = 2
    except (sqlite3.Error, OSError, ValueError, KeyError) as e:
        print(f"[check_dataset 中止] {type(e).__name__}: {e}", file=sys.stderr)
        report["abort"] = f"{type(e).__name__}: {e}"
        rc = 2
    finally:
        for c in (scores, prices):
            if c is not None:
                c.close()
    report["elapsed_s"] = round(time.perf_counter() - t0, 2)
    report["rc"] = rc
    if args.out:
        op = Path(args.out)
        op.parent.mkdir(parents=True, exist_ok=True)
        op.write_text(json.dumps(report, ensure_ascii=False, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    total = sum(v.get("n_mismatch", 0) for k, v in report["checks"].items() if k != "C3_computed_columns") + \
        report["checks"].get("C3_computed_columns", {}).get("_total", {}).get("n_mismatch", 0)
    print(f"結果：rc={rc}（mismatch 合計 {total}）耗時 {report['elapsed_s']}s" + (f"；報告 {args.out}" if args.out else ""))
    return rc


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="回測資料出口抽驗器：獨立重算 data/backtest/*.csv.gz 對 scores.db／prices.db")
    ap.add_argument("--cache-dir", default="cache")
    ap.add_argument("--data-dir", default="data/backtest")
    ap.add_argument("--calendar", default="data/calendar_tpe.json")
    ap.add_argument("--data-version", default=None)
    ap.add_argument("--sample", type=int, default=300, help="每檔抽驗列數（≥ 檔內列數＝全檔）")
    ap.add_argument("--seed", type=int, default=20260918)
    ap.add_argument("--out", default=None, help="JSON 報告路徑")
    ap.add_argument("--segments", default=None, help="覆寫段範圍：train=2021-01-01:2023-06-30,valid=...（測試用）")
    ap.add_argument("--h", default=None, help="覆寫 h：short=10,swing=20,mid=40（測試用）")
    return run(ap.parse_args(argv))


if __name__ == "__main__":
    raise SystemExit(main())
