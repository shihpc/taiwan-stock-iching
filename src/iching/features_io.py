"""`features.db`——`scan.DailyScanner` 逐日產出的落地層（第 12 項）。

**為什麼另開一個 DB、不進 `config.DB_FILES`**：`DB_FILES` 的五個是**原料** DB，每個都對應
`DatasetSpec`；`features.db` 是**衍生產出**，沒有 `DatasetSpec`，硬塞進去會讓
`config._check_registry()` 的 `d.db in DB_FILES` 斷言失去意義。

**會憑空多開一個空 DB 的是 `run` 與 `report`**（`backfill_hetzner.py` 的 `open_stores(cache_dir, C.DB_FILES)`
會 `Store(path)` 建檔）——**`reindex` 與 `data_versions_in_cache` 不會**（兩者都先 `is_file()` 過濾，
後者還走 `mode=ro`）。2026-09-13 驗收更正：本段原本三個都點名，兩個不成立。

`spec/P1-B3-replay.md` §B3.2 的 DB 切分表也沒有 `features.db`（同 `market.db` 的處境，
`config.py` 對後者註明「§B3.2 未指派檔名」）。**`score_io.open_score_stores` 不是這件事的前例**
（同批更正）：它開的三個 DB 全在 `DB_FILES` 裡，只是「另開一組連線」，不是「衍生產出另立新檔」。

§B3.2 的其餘三項照辦：四條 PRAGMA 的**內容**與 `store.PRAGMAS` 逐字相同（字面量差一個
`PRAGMA ` 前綴，本檔在 f-string 補）、複合 PK ＋ `WITHOUT ROWID` ＋ date 索引（`scan_day`
每日一列、PK 已是 `(data_version, date)`，**刻意不另建 date 索引**）、以日為外層迴圈逐日寫入。
這三項由 `tests/test_scan_features.py::test_schema_follows_b3_2` 守（2026-09-13 補——
驗收指出拿掉全部索引／全部 `WITHOUT ROWID`／四條 PRAGMA 全改，原本測試都是全綠）。

## 落地原則：**原始計數全部落地，比值一概不存**

`above_ma_count` 與 `ma_eligible` 分開存、`amount_up`／`amount_ret_eligible`／`amount_total`
三個都存。比值（`above_ma_ratio`／`up_amount_ratio`…）由讀取端用 `MarketBreadth` 的 property 算。
理由：`docs/P2-KICKOFF.md` §5 第 31 列那五個口徑選擇**改過一次了**（③ 分母從子集改母體全體），
只存比值的話每改一次口徑就得重掃 1,618 天。存計數則改讀取端即可。

## `ad_line` 是**相對掃描起點**的量，不是絕對值

騰落線從掃描起點累積，所以同一天的 `ad_line` 會隨「這次掃描從哪天開始」而不同。
消費端（`score/market.py:ind_ad_line_dev`）算的是 `(AD − MA_n(AD)) ÷ N`，**常數平移相消**，
所以這不影響分數——但**前提是整段序列來自同一次掃描**。把一段 `--from` 的結果寫進已有資料的
DB，接縫兩側來自不同起點，`ad_line` 就會跳一個無意義的差（2026-09-13 驗收實測 −165），
而且完全沒有訊號。`scan_features.py` 因此**拒絕**把部分區間寫進已有更早資料的 DB。

**真正耐久的量是 `advance_count − decline_count`**（本表已逐日存），消費端要重建 AD 序列
應該用它做 cumsum：起點不同只差一個常數，而那個常數在 `AD − MA_n(AD)` 裡相消。

## 參數必須釘住，否則兩層 parity 是假的

同一個 `data_version` 下，窗長集合與 `p_cs_tie` 變了就不是同一份特徵。`set_params()` 在首次
寫入時記下參數指紋，之後每次開啟都比對；不一致**直接拒絕寫入**，不會默默混進兩批不同口徑的列。
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

from .scan import ScanDay

SCHEMA_VERSION = 1
# 逐字同 `store.PRAGMAS`（`spec/P1-B3-replay.md` §B3.2 必備四條）
PRAGMAS = ("journal_mode = WAL", "synchronous = NORMAL", "cache_size = -64000", "temp_store = MEMORY")

KIND_ABOVE_MA = "above_ma"
KIND_NEW_HIGH = "new_high"
KIND_NEW_LOW = "new_low"

_DDL = (
    # 大盤廣度的純量欄（每日每市場一列）
    """CREATE TABLE IF NOT EXISTS market_breadth(
        data_version TEXT NOT NULL, market TEXT NOT NULL, date TEXT NOT NULL,
        n_stocks INTEGER NOT NULL,
        advance_count INTEGER NOT NULL, decline_count INTEGER NOT NULL, unchanged_count INTEGER NOT NULL,
        ret_eligible INTEGER NOT NULL, ad_line INTEGER NOT NULL,
        amount_up REAL NOT NULL, amount_ret_eligible REAL NOT NULL, amount_total REAL NOT NULL,
        index_missing INTEGER NOT NULL,
        PRIMARY KEY(data_version, market, date)) WITHOUT ROWID""",
    # 窗長相關的計數走長格式：窗長集合是規格決定的，寫成欄位會讓 schema 跟著 params 漂
    """CREATE TABLE IF NOT EXISTS market_breadth_window(
        data_version TEXT NOT NULL, market TEXT NOT NULL, date TEXT NOT NULL,
        kind TEXT NOT NULL, window INTEGER NOT NULL,
        count INTEGER NOT NULL, eligible INTEGER NOT NULL,
        PRIMARY KEY(data_version, market, date, kind, window)) WITHOUT ROWID""",
    """CREATE TABLE IF NOT EXISTS industry_agg(
        data_version TEXT NOT NULL, market TEXT NOT NULL, date TEXT NOT NULL,
        window INTEGER NOT NULL, industry TEXT NOT NULL,
        n INTEGER NOT NULL, median_ret REAL NOT NULL,
        PRIMARY KEY(data_version, market, date, window, industry)) WITHOUT ROWID""",
    """CREATE TABLE IF NOT EXISTS industry_breadth(
        data_version TEXT NOT NULL, market TEXT NOT NULL, date TEXT NOT NULL,
        industry TEXT NOT NULL, n_stocks INTEGER NOT NULL,
        PRIMARY KEY(data_version, market, date, industry)) WITHOUT ROWID""",
    """CREATE TABLE IF NOT EXISTS industry_breadth_window(
        data_version TEXT NOT NULL, market TEXT NOT NULL, date TEXT NOT NULL,
        industry TEXT NOT NULL, window INTEGER NOT NULL,
        above_ma_count INTEGER NOT NULL, ma_eligible INTEGER NOT NULL,
        PRIMARY KEY(data_version, market, date, industry, window)) WITHOUT ROWID""",
    """CREATE TABLE IF NOT EXISTS p_cs(
        data_version TEXT NOT NULL, market TEXT NOT NULL, date TEXT NOT NULL,
        window INTEGER NOT NULL, stock_id TEXT NOT NULL,
        p_cs REAL NOT NULL, excess REAL NOT NULL,
        PRIMARY KEY(data_version, market, date, window, stock_id)) WITHOUT ROWID""",
    # 每日一列的掃描狀態：排名池大小、ADV 暖機進度、指數缺哪些市場。
    # 這些是「為什麼那天長這樣」的唯一線索，不存的話事後只能重跑才知道。
    """CREATE TABLE IF NOT EXISTS scan_day(
        data_version TEXT NOT NULL, date TEXT NOT NULL,
        rank_pool_size INTEGER NOT NULL, adv_tracked INTEGER NOT NULL, adv_ready INTEGER NOT NULL,
        index_missing TEXT NOT NULL,
        PRIMARY KEY(data_version, date)) WITHOUT ROWID""",
    """CREATE TABLE IF NOT EXISTS scan_meta(
        data_version TEXT PRIMARY KEY, schema_version INTEGER NOT NULL,
        params_sha TEXT NOT NULL, params_json TEXT NOT NULL,
        first_written_at TEXT NOT NULL, last_written_at TEXT NOT NULL) WITHOUT ROWID""",
)
# 橫斷面查詢（「給我某一天的全部列」）走 date 索引；PK 已覆蓋時間序列查詢（§B3.2 的理由逐字如此）
_INDEXES = (
    ("idx_market_breadth_date", "market_breadth", "date"),
    ("idx_market_breadth_window_date", "market_breadth_window", "date"),
    ("idx_industry_agg_date", "industry_agg", "date"),
    ("idx_industry_breadth_date", "industry_breadth", "date"),
    ("idx_industry_breadth_window_date", "industry_breadth_window", "date"),
    ("idx_p_cs_date", "p_cs", "date"),
    ("idx_p_cs_stock", "p_cs", "stock_id"),
)
DATA_TABLES = ("market_breadth", "market_breadth_window", "industry_agg",
               "industry_breadth", "industry_breadth_window", "p_cs", "scan_day")


class FeatureStoreError(RuntimeError):
    pass


def params_fingerprint(params: dict) -> str:
    """參數指紋：排序後的 JSON 的 sha256 前 12 碼。**只認內容不認順序**。"""
    blob = json.dumps(params, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:12]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class FeatureStore:
    """`features.db` 的寫入端。`with FeatureStore(path) as fs:` 可用。"""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path, isolation_level=None)
        for p in PRAGMAS:
            self.conn.execute(f"PRAGMA {p}")
        self._ensure_schema()

    def __enter__(self) -> "FeatureStore":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def close(self) -> None:
        self.conn.close()

    def _ensure_schema(self) -> None:
        c = self.conn
        c.execute("BEGIN")
        try:
            for ddl in _DDL:
                c.execute(ddl)
            for name, table, col in _INDEXES:
                c.execute(f'CREATE INDEX IF NOT EXISTS "{name}" ON "{table}"("{col}")')
            c.execute("COMMIT")
        except BaseException:
            c.execute("ROLLBACK")
            raise

    # -- 參數釘選 ---------------------------------------------------------
    def set_params(self, data_version: str, params: dict) -> str:
        """首次寫入記下參數指紋；之後比對，不一致即拒絕。回傳指紋。

        **不一致時不寫任何東西也不自動清庫**——自動清掉別人跑了兩小時的結果比報錯更糟。
        要重跑就自己先呼叫 `clear()`。
        """
        sha = params_fingerprint(params)
        row = self.conn.execute("SELECT schema_version, params_sha, params_json FROM scan_meta WHERE data_version=?",
                                (data_version,)).fetchone()
        if row is None:
            self.conn.execute(
                "INSERT INTO scan_meta(data_version, schema_version, params_sha, params_json, "
                "first_written_at, last_written_at) VALUES(?,?,?,?,?,?)",
                (data_version, SCHEMA_VERSION, sha,
                 json.dumps(params, sort_keys=True, ensure_ascii=False), _now(), _now()))
            return sha
        old_schema, old_sha, old_json = row
        if old_schema != SCHEMA_VERSION:
            raise FeatureStoreError(
                f"{self.path} 的 schema_version={old_schema}，本程式是 {SCHEMA_VERSION}；"
                f"不做遷移，請用新檔或先 clear()")
        if old_sha != sha:
            raise FeatureStoreError(
                f"data_version={data_version} 已用不同參數寫過：舊 {old_sha} 新 {sha}。\n"
                f"  舊參數＝{old_json}\n  新參數＝{json.dumps(params, sort_keys=True, ensure_ascii=False)}\n"
                f"兩批口徑不同的列混在一起，兩層 parity 就是假的。要重跑請先 clear()。")
        self.conn.execute("UPDATE scan_meta SET last_written_at=? WHERE data_version=?", (_now(), data_version))
        return sha

    def params_of(self, data_version: str) -> dict | None:
        row = self.conn.execute("SELECT params_json FROM scan_meta WHERE data_version=?", (data_version,)).fetchone()
        return json.loads(row[0]) if row else None

    def clear(self, data_version: str) -> dict[str, int]:
        """砍掉某個 `data_version` 的全部列（含 meta）。回傳各表刪除列數。"""
        out: dict[str, int] = {}
        c = self.conn
        c.execute("BEGIN")
        try:
            for t in (*DATA_TABLES, "scan_meta"):
                cur = c.execute(f'DELETE FROM "{t}" WHERE data_version=?', (data_version,))
                out[t] = cur.rowcount
            c.execute("COMMIT")
        except BaseException:
            c.execute("ROLLBACK")
            raise
        return out

    # -- 寫入 -------------------------------------------------------------
    def write_day(self, day: ScanDay, data_version: str, *, rank_pool_size: int,
                  adv_tracked: int, adv_ready: int) -> dict[str, int]:
        """寫一個交易日。**同一個 `(data_version, 日期)` 重寫是「整日取代」**——先 `DELETE`
        該日在七張表的全部列，再插入新的，**同一個交易內**。

        為什麼不只靠 `INSERT OR REPLACE`：那是逐鍵覆蓋，**鍵集合縮小時舊鍵原地不動**。
        上游修正後某檔掉出排名池、某產業最後一檔停牌，舊列會完整殘留且沒有任何訊號
        （2026-09-13 驗收實測：`p_cs` 3 檔重寫成 1 檔，仍是 3 列）。`OR REPLACE` 仍保留，
        擋的是同一次寫入內的重複鍵。

        整日一個交易：中途失敗不會留半天的資料，也不會刪掉舊的卻沒寫新的。
        """
        n: dict[str, int] = {t: 0 for t in DATA_TABLES}
        c = self.conn
        c.execute("BEGIN")
        try:
            # **先刪該日再插，不能只靠 INSERT OR REPLACE**（2026-09-13 驗收抓到）：
            # `OR REPLACE` 是**逐鍵**覆蓋，鍵集合縮小時舊鍵原地不動。而「鍵集合縮小」正是
            # 本函式 docstring 原本用來論證 `OR REPLACE` 必要性的那個情境——上游修正後
            # 某檔掉出排名池（`p_cs` 少一個 `stock_id`）、某產業最後一檔停牌（`industry_agg`
            # 少一個產業）——舊列會完整殘留且**沒有任何訊號**。實測：3 檔重寫成 1 檔，
            # `p_cs` 仍是 3 列。所以修正只做了一半：值會被蓋掉、消失的鍵不會被刪掉。
            for t in DATA_TABLES:
                c.execute(f'DELETE FROM "{t}" WHERE data_version=? AND date=?', (data_version, day.tpe_date))
            for mk, b in sorted(day.breadth.items()):
                c.execute(
                    "INSERT OR REPLACE INTO market_breadth VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (data_version, mk, day.tpe_date, b.n_stocks, b.advance_count, b.decline_count,
                     b.unchanged_count, b.ret_eligible, b.ad_line, b.amount_up,
                     b.amount_ret_eligible, b.amount_total, int(mk in day.index_missing)))
                n["market_breadth"] += 1
                rows = [(data_version, mk, day.tpe_date, KIND_ABOVE_MA, w, cnt, b.ma_eligible[w])
                        for w, cnt in sorted(b.above_ma_count.items())]
                rows += [(data_version, mk, day.tpe_date, KIND_NEW_HIGH, w, cnt, b.hl_eligible[w])
                         for w, cnt in sorted(b.new_high_count.items())]
                rows += [(data_version, mk, day.tpe_date, KIND_NEW_LOW, w, cnt, b.hl_eligible[w])
                         for w, cnt in sorted(b.new_low_count.items())]
                c.executemany("INSERT OR REPLACE INTO market_breadth_window VALUES(?,?,?,?,?,?,?)", rows)
                n["market_breadth_window"] += len(rows)

            rows = [(data_version, a.market, a.tpe_date, a.window, a.industry, a.n, a.median_ret)
                    for a in day.industry]
            c.executemany("INSERT OR REPLACE INTO industry_agg VALUES(?,?,?,?,?,?,?)", rows)
            n["industry_agg"] = len(rows)

            ib = [(data_version, x.market, x.tpe_date, x.industry, x.n_stocks) for x in day.industry_breadth]
            c.executemany("INSERT OR REPLACE INTO industry_breadth VALUES(?,?,?,?,?)", ib)
            n["industry_breadth"] = len(ib)
            ibw = [(data_version, x.market, x.tpe_date, x.industry, w, cnt, x.ma_eligible[w])
                   for x in day.industry_breadth for w, cnt in sorted(x.above_ma_count.items())]
            c.executemany("INSERT OR REPLACE INTO industry_breadth_window VALUES(?,?,?,?,?,?,?)", ibw)
            n["industry_breadth_window"] = len(ibw)

            pcs = [(data_version, mk, day.tpe_date, w, sid, v, day.excess[(mk, w)][sid])
                   for (mk, w), per in sorted(day.p_cs.items()) for sid, v in sorted(per.items())]
            c.executemany("INSERT OR REPLACE INTO p_cs VALUES(?,?,?,?,?,?,?)", pcs)
            n["p_cs"] = len(pcs)

            c.execute("INSERT OR REPLACE INTO scan_day VALUES(?,?,?,?,?,?)",
                      (data_version, day.tpe_date, rank_pool_size, adv_tracked, adv_ready,
                       ",".join(sorted(day.index_missing))))
            n["scan_day"] = 1
            c.execute("COMMIT")
        except BaseException:
            c.execute("ROLLBACK")
            raise
        return n

    # -- 讀（給驗收與下游用） ----------------------------------------------
    def counts(self, data_version: str) -> dict[str, int]:
        return {t: self.conn.execute(f'SELECT COUNT(*) FROM "{t}" WHERE data_version=?',
                                     (data_version,)).fetchone()[0] for t in DATA_TABLES}

    def dates(self, data_version: str) -> list[str]:
        return [r[0] for r in self.conn.execute(
            "SELECT date FROM scan_day WHERE data_version=? ORDER BY date", (data_version,))]

    def breadth_row(self, data_version: str, market: str, date: str) -> dict | None:
        cur = self.conn.execute("SELECT * FROM market_breadth WHERE data_version=? AND market=? AND date=?",
                                (data_version, market, date))
        row = cur.fetchone()
        return dict(zip([d[0] for d in cur.description], row)) if row else None

    def window_rows(self, data_version: str, market: str, date: str, kind: str) -> dict[int, tuple[int, int]]:
        """**空 dict 不可分辨「查無此日」與「該日該 kind 沒有列」**（與 `breadth_row` 回 `None` 不同）。
        後者在正常產出下不會發生（窗長集合固定），但呼叫端若要分辨，先用 `breadth_row` 確認該日存在。"""
        return {w: (cnt, elig) for w, cnt, elig in self.conn.execute(
            "SELECT window, count, eligible FROM market_breadth_window "
            "WHERE data_version=? AND market=? AND date=? AND kind=? ORDER BY window",
            (data_version, market, date, kind))}

    def missing_dates(self, data_version: str, expected: Iterable[str]) -> list[str]:
        """預期有、但 `scan_day` 沒有的日期——**掃描完一定要檢查這個**。"""
        have = set(self.dates(data_version))
        return [d for d in expected if d not in have]
