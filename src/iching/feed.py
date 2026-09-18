"""從回補 DB 餵出逐日橫斷面——**唯讀**，是 `scan.DailyScanner`／`liquidity.AdvTracker` 的上游。

`scripts/probe_features.py`（量測）與 `scripts/scan_features.py`（落地）**共用這一份**。
分開寫兩份的話，「哪些列算有成交」「後復權怎麼套」「市場別怎麼判」會各自漂移，而兩邊的數字
看起來都很合理——那正是最難發現的一類錯。

**唯讀保證**：連線一律 `file:<path>?mode=ro`，SQLite 層級拒絕寫入（實測 `INSERT` 得
`attempt to write a readonly database`）。本模組不建表、不寫 meta、不碰 WAL。

**本模組 import sqlite3**，所以它**不是**兩層 parity 的純函式層——純函式在 `scan.py`／
`liquidity.py`／`adjust.py`。本模組只做「DB 列 → `StockDay`」的搬運，不做任何判定：
有成交與否走 `universe.is_traded_row()`、還原走 `adjust.factor_at()`、池走 `universe.PitPool`，
全部是既有的、已被測試守住的純函式。

## 池是 point-in-time 的（2026-09-16，P3 第 1 項；`docs/P3-PIT-POOL.md` §1）

`load_pool()` 回 `universe.PitPool`（一份 `TaiwanStockInfo` 快照含殘留列 → 靜態合格集合＋市場轉換表），
`day_records(T, …)` 對每一日呼叫 `pool.members(T, traded_sids)`：**T 日所屬市場由殘留列的 `date` 重建**
（轉換生效日＝較舊那列 `date`+1，誤差 1～2 日；興櫃時期不在池），不再是「最新一列的 type 套到全部歷史」。
每日班 `daily_core.rebuild_from_bundles` 走**同一支** `day_records`，兩條路徑共用同一個 `PitPool` 物件形狀。
已知偏差（轉換日誤差、2020 前殘留列不完整、快照裡沒有的下市股不在池）見 `docs/pre-registration.md` §3。
"""
from __future__ import annotations

import sqlite3
from itertools import groupby
from pathlib import Path

from . import factor_sources as FS
from . import universe as U
from .adjust import factor_at
from .scan import StockDay

PRICE_TABLE = "raw_price_daily"
INFO_TABLE = "raw_stock_info"
DIV_TABLE = "raw_dividend_result"
FACTOR_TABLES: tuple[str, ...] = tuple(s.table for s in FS.SOURCES)   # 四個事件源的 raw 表（裁定 #51；順序＝來源序）
INDEX_TABLE = "raw_index_price"
INDEX_ID = {"twse": "TAIEX", "tpex": "TPEx"}      # 正本＝`score_io.py` 的同一組對應（TPEx 大小寫混寫是 FinMind 原樣）
PRICE_SPREAD = "spread"


class FeedError(RuntimeError):
    """資料形狀不如預期就大聲停下——最不該做的事是靜默回 0／空。"""


def open_ro(path: Path) -> sqlite3.Connection:
    if not Path(path).exists():
        raise FeedError(f"找不到 DB：{path}")
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    conn.execute("PRAGMA cache_size = -64000")
    conn.execute("PRAGMA temp_store = MEMORY")
    return conn


def columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {r[1] for r in conn.execute(f'PRAGMA table_info("{table}")')}


def require(conn: sqlite3.Connection, table: str, cols: set[str]) -> set[str]:
    have = columns(conn, table)
    if not have:
        raise FeedError(f"表不存在或為空 schema：{table}")
    missing = cols - have
    if missing:
        raise FeedError(f"{table} 缺欄位 {sorted(missing)}；實際欄位＝{sorted(have)}")
    return have


def resolve_dv(conn: sqlite3.Connection, table: str, wanted: str | None) -> str:
    """DB 裡若有多個 `data_version`，必須由呼叫端指定——混著算出來的數字沒有意義。"""
    got = [r[0] for r in conn.execute(f'SELECT DISTINCT data_version FROM "{table}" ORDER BY 1')]
    if not got:
        raise FeedError(f"{table} 沒有任何列")
    if wanted:
        if wanted not in got:
            raise FeedError(f"{table} 沒有 data_version={wanted}；有的是 {got}")
        return wanted
    if len(got) > 1:
        raise FeedError(f"{table} 有多個 data_version {got}，請指定")
    return got[0]


def load_pool(conn: sqlite3.Connection) -> U.PitPool:
    """普通股池＝`universe.PitPool.from_snapshot_rows()`（整張 `raw_stock_info` 快照、不篩 data_version、含殘留列）。
    靜態集合（名單＋產業別）走 Mapping 介面；T 日市場別一律 `pool.listed(sid, T)`。"""
    have = require(conn, INFO_TABLE, {"stock_id"})
    cols = [c for c in ("stock_id", "type", "industry_category", "stock_name", "date") if c in have]
    rows = [dict(zip(cols, r)) for r in conn.execute(f'SELECT {",".join(cols)} FROM "{INFO_TABLE}"')]
    pool = U.PitPool.from_snapshot_rows(rows)
    if not len(pool):
        raise FeedError(f"{INFO_TABLE} 解不出任何池成員（{len(rows)} 列）")
    return pool


def factor_table_state(conn: sqlite3.Connection, table: str, need: set[str]) -> tuple[str, set[str]]:
    """事件源 raw 表的三態（2026-09-18 驗收後修正 (b)，`feed.load_factor_rows` 與 `check_dataset._load_factors` 共用同一規則）：
    `missing`＝表不存在；`meta_only`＝表在、**缺該源的 before／after 欄**、且**表內零列**——`Store.record_success(rows=[])`
    會先 `ensure_raw_table` 建出只有 meta 欄（cov_key／row_hash／data_version／date／stock_id／extra）的表，某表若空年塊先落地、
    之後沒有非空塊（或 run 中斷）就長這樣，語意是 0 列、不是壞表；`ok`＝欄位齊。表**有列**卻缺欄 → `FeedError`（真的壞）。
    回 (state, 實際欄位集合)。"""
    have = columns(conn, table)
    if not have:
        return "missing", have
    lack = need - have
    if not lack:
        return "ok", have
    if conn.execute(f'SELECT 1 FROM "{table}" LIMIT 1').fetchone() is None:
        return "meta_only", have
    raise FeedError(f"{table} 缺欄位 {sorted(lack)}；實際欄位＝{sorted(have)}（表內有列，不是 meta-only 空表）")


def require_dv_rows(conn: sqlite3.Connection, table: str, dv: str) -> None:
    """表**有列**但沒有本 `data_version` 的列 → `FeedError`（2026-09-18 驗收後修正 (c)，取代原本只記 `dv_missing` warning）：
    四表 dv 不一致是配置錯誤（某表以另一批號回補），不 raise 的話係數會**靜默少掉整個事件源**。表零列或不存在＝0 列，不在此 raise。
    判準看 `DISTINCT data_version`（不看 `date IS NOT NULL` 過濾後的列數），本 dv 的列全是 NULL date 不算 dv 缺席。"""
    got = [str(r[0]) for r in conn.execute(f'SELECT DISTINCT data_version FROM "{table}" ORDER BY 1')]
    if got and dv not in got:
        raise FeedError(f"{table} 有列但沒有 data_version={dv} 的列；表內 data_version＝{got}。"
                        f"四個事件源必須同一 data_version（以同批號重跑 `run --dataset <key>`，或改 --data-version）")


def load_factor_rows(conn: sqlite3.Connection, dv: str) -> tuple[list[FS.Row5], dict]:
    """四個事件源的原始列（`factor_sources.SOURCES`；同一個 `data_version`）→ `factor_sources.merge_factor_rows`。
    回 (合併後 5 欄列, 合併統計)。**缺表視為 0 列**並記進 `stat["missing_tables"]`（回補尚未跑到該表時仍可計分，
    log 會印出）；**meta-only 空表**（`factor_table_state`）同樣視為 0 列、記 `stat["meta_only_tables"]`；表有列但缺欄 →
    `FeedError`；**表有列但沒有本 dv 的列 → `FeedError`**（`require_dv_rows`；2026-09-18 起不再只是 warning）。
    每表 SQL 皆 `ORDER BY stock_id, date`（keep-first 的「first」由此決定）。"""
    by: dict[str, list] = {}
    missing: list[str] = []
    meta_only: list[str] = []
    for spec in FS.SOURCES:
        state, _have = factor_table_state(conn, spec.table, {"stock_id", "date", spec.before, spec.after})
        if state != "ok":
            (missing if state == "missing" else meta_only).append(spec.table)
            by[spec.source] = []
            continue
        q = (f'SELECT stock_id, date, "{spec.before}", "{spec.after}" FROM "{spec.table}" '
             f"WHERE data_version=? AND date IS NOT NULL ORDER BY stock_id, date")
        rows = [tuple(r) for r in conn.execute(q, (dv,))]
        if not rows:
            require_dv_rows(conn, spec.table, dv)
        by[spec.source] = rows
    merged, stat = FS.merge_factor_rows(by)
    stat["missing_tables"] = missing
    stat["meta_only_tables"] = meta_only
    return merged, stat


def load_factors_full(conn: sqlite3.Connection, dv: str) -> tuple[dict[str, tuple[list[str], list[float]]], dict, dict]:
    """每檔的後復權累積係數。回 ({sid: (ex_dates, cum)}, 係數統計, 合併統計)。
    係數統計＝`factor_sources.build_factors` 的（與 `daily_core.load_factors_file` 讀 `factors.json` 得到的**同形同值**，parity 靠它）；
    合併統計＝`load_factor_rows` 的（每源筆數／跨源去重／按源 band 異常，只有 DB 這一側有）。"""
    rows, mstat = load_factor_rows(conn, dv)
    factors, stat = FS.build_factors(rows)
    return factors, stat, mstat


def load_factors(conn: sqlite3.Connection, dv: str) -> tuple[dict[str, tuple[list[str], list[float]]], dict]:
    """＝`load_factors_full` 的前兩個回傳值（既有呼叫端介面不變）。"""
    factors, stat, _ = load_factors_full(conn, dv)
    return factors, stat


def load_index(conn: sqlite3.Connection, dv: str) -> dict[str, dict[str, float]]:
    """{date: {market: 指數收盤}}。"""
    require(conn, INDEX_TABLE, {"stock_id", "date", "close"})
    out: dict[str, dict[str, float]] = {}
    rev = {v: k for k, v in INDEX_ID.items()}
    q = f'SELECT date, stock_id, close FROM "{INDEX_TABLE}" WHERE data_version=?'
    for d, sid, c in conn.execute(q, (dv,)):
        mk = rev.get(str(sid))
        if mk is None or c is None:
            continue
        out.setdefault(str(d), {})[mk] = float(c)
    if not out:
        raise FeedError(f"{INDEX_TABLE} 取不到 {sorted(INDEX_ID.values())} 的收盤")
    return out


def iter_days(conn: sqlite3.Connection, dv: str, have_spread: bool = False,
              start: str | None = None, end: str | None = None):
    """逐日吐 `(date, [列])`。**一次 `ORDER BY date` 串流**，不整表載入（§B3.2 第 4 點）。

    列的順序＝`(date, stock_id, close, Trading_Volume, Trading_money[, spread])`。
    """
    cols = ["date", "stock_id", U.PRICE_CLOSE, U.PRICE_VOLUME, U.PRICE_AMOUNT]
    if have_spread:
        cols.append(PRICE_SPREAD)
    where = ["data_version=?", "date IS NOT NULL"]
    params: list = [dv]
    if start:
        where.append("date>=?")
        params.append(start)
    if end:
        where.append("date<=?")
        params.append(end)
    q = f'SELECT {",".join(cols)} FROM "{PRICE_TABLE}" WHERE {" AND ".join(where)} ORDER BY date'
    for d, grp in groupby(conn.execute(q, params), key=lambda r: r[0]):
        yield str(d), [tuple(r) for r in grp]


def day_records(tpe_date: str, rows: list[tuple], pool: U.PitPool,
                factors: dict[str, tuple[list[str], list[float]]],
                rank_pool: frozenset[str] | set[str] | None = None,
                adjusted: bool = True) -> tuple[list[StockDay], dict[str, float]]:
    """把某日的價格列轉成 `(StockDay 清單, {stock_id: 成交值})`。

    - 池是 point-in-time 的：先用 `universe.is_traded_row()` 算出當日有成交的代號集合，`pool.members(T, traded_sids)`
      決定母體（靜態合格 ∧ T 日市場∈{twse,tpex} ∧ 有成交），市場桶取自 meta 的 `type`（＝T 日市場）。
      ETF／權證／DR／指數列／興櫃期的列／不在快照的代號自然被濾掉。
    - 有列但**不成交**、且 T 日在池（`pool.listed`）的檔仍吐一筆 `close_adj=None`、`amount=None` 的 `StockDay`，
      且**不進**成交值 dict（`AdvTracker` 會自己補 0，見 `liquidity` 口徑第 1 條；`DailyScanner` 對 None 不進母體、不動狀態）
      ——與改 PIT 之前的形狀相同，兩層 parity 不因此多一個變因。
    - `adjusted=False` 走原始價，供量測用（`probe_features.py --probe adjust` 的對照組）。
    - `rank_pool=None` 時 `in_rank_pool` 一律 True——**只有量測用得到**；落地一定要傳
      `AdvTracker.eligible()` 的結果，且必須在 `push_day` **之前**取（PIT，見 `liquidity` docstring）。
    """
    traded_sids = U.traded_ids((str(r[1]), {U.PRICE_CLOSE: r[2], U.PRICE_VOLUME: r[3]}) for r in rows)   # 唯一成交門（與 WindowCache 同一支）
    members = pool.members(tpe_date, traded_sids)
    recs: list[StockDay] = []
    amounts: dict[str, float] = {}
    for r in rows:
        sid = str(r[1])
        meta = members.get(sid)
        if meta is None:
            mk = None if sid in traded_sids else pool.listed(sid, tpe_date)
            if mk is None:
                continue
            recs.append(StockDay(sid, mk, pool.industry_of(sid) or None, None, None,
                                 True if rank_pool is None else sid in rank_pool))
            continue
        close = float(r[2])
        amt = float(r[4] or 0.0)
        amounts[sid] = amt
        if adjusted:
            dc = factors.get(sid)
            if dc:
                close *= factor_at(tpe_date, *dc)
        recs.append(StockDay(sid, meta["type"], meta.get("industry_category") or None, close, amt,
                             True if rank_pool is None else sid in rank_pool))
    return recs, amounts
