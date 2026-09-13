"""從回補 DB 餵出逐日橫斷面——**唯讀**，是 `scan.DailyScanner`／`liquidity.AdvTracker` 的上游。

`scripts/probe_features.py`（量測）與 `scripts/scan_features.py`（落地）**共用這一份**。
分開寫兩份的話，「哪些列算有成交」「後復權怎麼套」「市場別怎麼判」會各自漂移，而兩邊的數字
看起來都很合理——那正是最難發現的一類錯。

**唯讀保證**：連線一律 `file:<path>?mode=ro`，SQLite 層級拒絕寫入（實測 `INSERT` 得
`attempt to write a readonly database`）。本模組不建表、不寫 meta、不碰 WAL。

**本模組 import sqlite3**，所以它**不是**兩層 parity 的純函式層——純函式在 `scan.py`／
`liquidity.py`／`adjust.py`。本模組只做「DB 列 → `StockDay`」的搬運，不做任何判定：
有成交與否走 `universe.is_traded_row()`、還原走 `adjust.factor_at()`、池走 `universe.pool_from_info()`，
全部是既有的、已被測試守住的純函式。

## 已知近似（不影響搬運本身，但呼叫端要知道）

- **市場別取 `TaiwanStockInfo` 最新一列**（`universe.pool_from_info`），不是 T 日所屬市場
  ——後者在 `config.OUT_OF_SCOPE` 第 ③ 條、尚未實作。影響的是轉板過的少數檔落在哪個市場桶。
"""
from __future__ import annotations

import sqlite3
from itertools import groupby
from pathlib import Path

from . import universe as U
from .adjust import Event, cumulative_factors, factor_at
from .scan import StockDay

PRICE_TABLE = "raw_price_daily"
INFO_TABLE = "raw_stock_info"
DIV_TABLE = "raw_dividend_result"
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


def load_pool(conn: sqlite3.Connection) -> dict[str, dict]:
    """普通股池（含市場別與產業別）＝`universe.pool_from_info()` 的結果。"""
    have = require(conn, INFO_TABLE, {"stock_id"})
    cols = [c for c in ("stock_id", "type", "industry_category", "stock_name", "date") if c in have]
    rows = [dict(zip(cols, r)) for r in conn.execute(f'SELECT {",".join(cols)} FROM "{INFO_TABLE}"')]
    pool = U.pool_from_info(rows)
    if not pool:
        raise FeedError(f"{INFO_TABLE} 解不出任何池成員（{len(rows)} 列）")
    return pool


def load_factors(conn: sqlite3.Connection, dv: str) -> tuple[dict[str, tuple[list[str], list[float]]], dict]:
    """每檔的後復權累積係數。回 ({sid: (ex_dates, cum)}, 統計)。"""
    require(conn, DIV_TABLE, {"stock_id", "date", "before_price", "after_price"})
    by: dict[str, list[Event]] = {}
    seen: set[tuple[str, str]] = set()
    n_rows = n_dup = n_bad = 0
    q = (f'SELECT stock_id, date, before_price, after_price FROM "{DIV_TABLE}" '
         f"WHERE data_version=? AND date IS NOT NULL ORDER BY stock_id, date")
    for sid, d, b, a in conn.execute(q, (dv,)):
        n_rows += 1
        key = (str(sid), str(d))
        if key in seen:                    # 同一事件可能同時落在兩種 cov_key（`store.py` 的已知代價）
            n_dup += 1
            continue
        try:
            bf, af = float(b), float(a)
        except (TypeError, ValueError):
            n_bad += 1
            continue
        if af <= 0 or bf <= 0:
            n_bad += 1
            continue
        seen.add(key)
        by.setdefault(str(sid), []).append(Event(str(d), bf, af))
    out = {sid: cumulative_factors(evs) for sid, evs in by.items()}
    return out, {"rows": n_rows, "dup_skipped": n_dup, "bad_skipped": n_bad, "stocks": len(out)}


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


def day_records(tpe_date: str, rows: list[tuple], pool: dict[str, dict],
                factors: dict[str, tuple[list[str], list[float]]],
                rank_pool: frozenset[str] | set[str] | None = None,
                adjusted: bool = True) -> tuple[list[StockDay], dict[str, float]]:
    """把某日的價格列轉成 `(StockDay 清單, {stock_id: 成交值})`。

    - 只保留池內代號（ETF／權證／DR／指數列自然被濾掉）。
    - 「當日有成交」走 `universe.is_traded_row()`；不成交者 `close_adj=None`、`amount=None`，
      且**不進**成交值 dict（`AdvTracker` 會自己補 0，見 `liquidity` 口徑第 1 條）。
    - `adjusted=False` 走原始價，供量測用（`probe_features.py --probe adjust` 的對照組）。
    - `rank_pool=None` 時 `in_rank_pool` 一律 True——**只有量測用得到**；落地一定要傳
      `AdvTracker.eligible()` 的結果，且必須在 `push_day` **之前**取（PIT，見 `liquidity` docstring）。
    """
    recs: list[StockDay] = []
    amounts: dict[str, float] = {}
    for r in rows:
        sid = str(r[1])
        meta = pool.get(sid)
        if meta is None:
            continue
        traded = U.is_traded_row({U.PRICE_CLOSE: r[2], U.PRICE_VOLUME: r[3]})
        close = amt = None
        if traded:
            close = float(r[2])
            amt = float(r[4] or 0.0)
            amounts[sid] = amt
            if adjusted:
                dc = factors.get(sid)
                if dc:
                    close *= factor_at(tpe_date, *dc)
        recs.append(StockDay(sid, "twse" if meta.get("type") == "twse" else "tpex",
                             meta.get("industry_category") or None, close, amt,
                             True if rank_pool is None else sid in rank_pool))
    return recs, amounts
