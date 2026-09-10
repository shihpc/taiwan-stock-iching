"""SQLite 落地層（P1-B3 §B3.2 裁定 D）。

每個 DB 檔（prices／chips／fundamentals／universe／market）各自含：
- `raw_<key>`  原始列：固定欄 `cov_key`（所屬 coverage 鍵）、`row_hash`（列內容 sha1）、**PK=(cov_key, row_hash)**
               （2026-09-09 驗收更正：原 PK 只有 row_hash＋INSERT OR REPLACE，fallback 讓同 dataset 混用 per_stock 與
               daily_slice 時同內容列在 cov_key 之間搬家、n_rows 失真；現在每個 cov_key 各自持有自己的列，
               混用策略會使同一列存兩份——那是 coverage 正確性的代價，report 的 n_rows 與底下實列數必須相等）、`data_version`、
               `date`、`stock_id`，其餘欄位**依首次收到的列動態 ALTER TABLE 新增**（欄名只允許 [A-Za-z0-9_]，
               不合法的鍵塞進 `extra` JSON）。動態建欄的理由：多個資料集的完整欄位名未實測（config 的 note），
               寫死 schema 會靜默丟欄；動態建欄是無損的。
- `coverage`   (dataset, key) → status ∈ {ok, empty}、n_rows、fetched_at、data_version。
               **失敗絕不寫進 coverage**（taiwan-stock-news CLAUDE.md 已知坑 2：失敗被記成「已涵蓋且沒資料」後，
               增量會沿用這個「沒有」，只有全量重抓沖得掉）。
- `failures`   (dataset, key) → kind、message、attempted_at、data_version；成功後自動刪除該鍵。
- `sources`    每 dataset 一列：抓取時間、請求數、筆數、日期範圍（§B3.4 第 4 點）；另記 `landing_filter`
               （落地過濾版本，`config.LANDING_FILTER_VERSION`；未套用者 NULL）與 `n_filtered`（同 data_version 內
               被濾掉的累計列數）——2026-09-10 加，讓日後看得出這份 DB 是濾過的、濾的是哪一版規則。

冪等：`is_covered(dataset, key, data_version)` 只在 status∈{ok,empty} 且 data_version 相同時為真；
換 data_version ＝ 整批重抓（§B3.4 第 1 點「歷史一律重抓」）。同一鍵重抓時先 DELETE 該 cov_key 的舊列再 INSERT，
同一交易內完成，中斷不會留半套。
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import re
import sqlite3
from pathlib import Path
from typing import Any, Iterable

from .config import TAIPEI

PRAGMAS = (
    "PRAGMA journal_mode = WAL",
    "PRAGMA synchronous = NORMAL",
    "PRAGMA cache_size = -64000",
    "PRAGMA temp_store = MEMORY",
)
_COL_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_FIXED_COLS = ("cov_key", "row_hash", "data_version", "date", "stock_id", "extra")


def _now_iso() -> str:
    return dt.datetime.now(TAIPEI).isoformat(timespec="seconds")


def row_hash(row: dict) -> str:
    s = json.dumps(row, sort_keys=True, ensure_ascii=False, separators=(",", ":"), default=str)
    return hashlib.sha1(s.encode("utf-8")).hexdigest()


def safe_col(name: str) -> str | None:
    """欄名合法且不與固定欄衝突才回原名，否則 None（→ extra）。"""
    if not isinstance(name, str) or not _COL_RE.match(name):
        return None
    if name.lower() in _FIXED_COLS or name.lower() in ("date", "stock_id"):
        return name if name in ("date", "stock_id") else None
    return name


class Store:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(self.path), isolation_level=None)  # 手動交易
        for p in PRAGMAS:
            self.conn.execute(p)
        self._init_meta()
        self._cols_cache: dict[str, set[str]] = {}

    def close(self) -> None:
        self.conn.close()

    def __enter__(self) -> "Store":
        return self

    def __exit__(self, *a: Any) -> None:
        self.close()

    # -- schema ---------------------------------------------------------------
    def _init_meta(self) -> None:
        c = self.conn
        c.execute("""CREATE TABLE IF NOT EXISTS coverage(
            dataset TEXT NOT NULL, key TEXT NOT NULL, status TEXT NOT NULL,
            n_rows INTEGER NOT NULL, fetched_at TEXT NOT NULL, data_version TEXT NOT NULL,
            PRIMARY KEY(dataset, key)) WITHOUT ROWID""")
        c.execute("""CREATE TABLE IF NOT EXISTS failures(
            dataset TEXT NOT NULL, key TEXT NOT NULL, kind TEXT NOT NULL, message TEXT,
            attempted_at TEXT NOT NULL, data_version TEXT NOT NULL, attempts INTEGER NOT NULL DEFAULT 1,
            PRIMARY KEY(dataset, key)) WITHOUT ROWID""")
        c.execute("""CREATE TABLE IF NOT EXISTS sources(
            dataset TEXT PRIMARY KEY, finmind_dataset TEXT, data_version TEXT,
            first_fetched_at TEXT, last_fetched_at TEXT, n_requests INTEGER NOT NULL DEFAULT 0,
            n_rows INTEGER NOT NULL DEFAULT 0, min_date TEXT, max_date TEXT, columns TEXT,
            landing_filter TEXT, n_filtered INTEGER NOT NULL DEFAULT 0)""")
        # 2026-09-10 加的兩欄：既有 DB（2026-09-10 前建的）補欄，冪等；其餘 schema 仍不做遷移（runbook §4）
        have = self.columns("sources")
        if "landing_filter" not in have:
            c.execute("ALTER TABLE sources ADD COLUMN landing_filter TEXT")
        if "n_filtered" not in have:
            c.execute("ALTER TABLE sources ADD COLUMN n_filtered INTEGER NOT NULL DEFAULT 0")

    def ensure_raw_table(self, table: str, index_cols: Iterable[str] = ("stock_id", "date")) -> None:
        self.conn.execute(f"""CREATE TABLE IF NOT EXISTS "{table}"(
            cov_key TEXT NOT NULL, row_hash TEXT NOT NULL, data_version TEXT NOT NULL,
            date TEXT, stock_id TEXT, extra TEXT, PRIMARY KEY(cov_key, row_hash)) WITHOUT ROWID""")
        self.conn.execute(f'CREATE INDEX IF NOT EXISTS "idx_{table}_date" ON "{table}"(date)')
        ic = tuple(index_cols)
        if ic and ic != ("date",):
            self.conn.execute(f'CREATE INDEX IF NOT EXISTS "idx_{table}_{"_".join(ic)}" ON "{table}"({", ".join(ic)})')
        self._cols_cache[table] = self.columns(table)

    def columns(self, table: str) -> set[str]:
        return {r[1] for r in self.conn.execute(f'PRAGMA table_info("{table}")')}

    def _ensure_columns(self, table: str, rows: list[dict]) -> list[str]:
        have = self._cols_cache.setdefault(table, self.columns(table))
        new: list[str] = []
        for r in rows:
            for k in r:
                sc = safe_col(k)
                if sc and sc not in have:
                    new.append(sc)
                    have.add(sc)
        for col in new:
            self.conn.execute(f'ALTER TABLE "{table}" ADD COLUMN "{col}"')
        return new

    # -- coverage ---------------------------------------------------------------
    def is_covered(self, dataset: str, key: str, data_version: str) -> bool:
        r = self.conn.execute(
            "SELECT status, data_version FROM coverage WHERE dataset=? AND key=?", (dataset, key)
        ).fetchone()
        return bool(r) and r[0] in ("ok", "empty") and r[1] == data_version

    def covered_keys(self, dataset: str, data_version: str | None = None) -> set[str]:
        if data_version is None:
            q = "SELECT key FROM coverage WHERE dataset=? AND status IN ('ok','empty')"
            return {r[0] for r in self.conn.execute(q, (dataset,))}
        q = "SELECT key FROM coverage WHERE dataset=? AND status IN ('ok','empty') AND data_version=?"
        return {r[0] for r in self.conn.execute(q, (dataset, data_version))}

    def record_success(self, dataset: str, table: str, key: str, rows: list[dict], data_version: str,
                       finmind_dataset: str = "", index_cols: Iterable[str] = ("stock_id", "date"),
                       landing_filter: str | None = None, n_filtered: int = 0) -> int:
        """一個交易：刪同鍵舊列 → 插新列 → 寫 coverage（ok/empty）→ 清 failures → 更新 sources。

        `rows` 是**已過濾**的列（過濾在呼叫端做，本層不知道規則）；`landing_filter`／`n_filtered` 只記進 sources，
        coverage.n_rows 一律是實際插入列數。"""
        self.ensure_raw_table(table, index_cols)
        rows = [r for r in rows if isinstance(r, dict)]
        self._ensure_columns(table, rows)
        cols = sorted(c for c in self._cols_cache[table] if c not in ("row_hash", "cov_key", "data_version", "extra"))
        now = _now_iso()
        status = "ok" if rows else "empty"
        c = self.conn
        c.execute("BEGIN")
        try:
            c.execute(f'DELETE FROM "{table}" WHERE cov_key=?', (key,))
            n_inserted = 0
            if rows:
                payload = []
                for r in rows:
                    vals = {}
                    extra = {}
                    for k, v in r.items():
                        sc = safe_col(k)
                        if sc:
                            vals[sc] = v if not isinstance(v, (dict, list)) else json.dumps(v, ensure_ascii=False)
                        else:
                            extra[str(k)] = v
                    payload.append(
                        (row_hash(r), key, data_version, json.dumps(extra, ensure_ascii=False) if extra else None,
                         *[vals.get(cn) for cn in cols])
                    )
                ph = ", ".join("?" for _ in range(4 + len(cols)))
                colnames = ", ".join(f'"{cn}"' for cn in cols)
                c.executemany(
                    f'INSERT OR REPLACE INTO "{table}"(row_hash, cov_key, data_version, extra{", " if cols else ""}{colnames}) VALUES({ph})',
                    payload,
                )
                # n_rows 記**實際落地列數**（同鍵內完全相同的列會被 PK 去重）
                n_inserted = c.execute(f'SELECT COUNT(*) FROM "{table}" WHERE cov_key=?', (key,)).fetchone()[0]
            c.execute(
                "INSERT OR REPLACE INTO coverage(dataset, key, status, n_rows, fetched_at, data_version) VALUES(?,?,?,?,?,?)",
                (dataset, key, status, n_inserted, now, data_version),
            )
            c.execute("DELETE FROM failures WHERE dataset=? AND key=?", (dataset, key))
            self._bump_sources(dataset, finmind_dataset, data_version, now, rows, cols, n_inserted,
                               landing_filter, int(n_filtered or 0))
            c.execute("COMMIT")
        except Exception:
            c.execute("ROLLBACK")
            raise
        return n_inserted

    def record_failure(self, dataset: str, key: str, kind: str, message: str, data_version: str) -> None:
        """只進 failures，**不碰 coverage**。訊息截 300 字（不含 token：fm.py 的例外訊息本來就不含）。"""
        now = _now_iso()
        self.conn.execute(
            """INSERT INTO failures(dataset, key, kind, message, attempted_at, data_version, attempts)
               VALUES(?,?,?,?,?,?,1)
               ON CONFLICT(dataset, key) DO UPDATE SET kind=excluded.kind, message=excluded.message,
                 attempted_at=excluded.attempted_at, data_version=excluded.data_version, attempts=attempts+1""",
            (dataset, key, kind, (message or "")[:300], now, data_version),
        )

    def _bump_sources(self, dataset: str, finmind_dataset: str, data_version: str, now: str,
                      rows: list[dict], cols: list[str], n_inserted: int,
                      landing_filter: str | None = None, n_filtered: int = 0) -> None:
        dates = [str(r.get("date")) for r in rows if r.get("date")]
        mn = min(dates) if dates else None
        mx = max(dates) if dates else None
        self.conn.execute(
            """INSERT INTO sources(dataset, finmind_dataset, data_version, first_fetched_at, last_fetched_at,
                                   n_requests, n_rows, min_date, max_date, columns, landing_filter, n_filtered)
               VALUES(?,?,?,?,?,1,?,?,?,?,?,?)
               ON CONFLICT(dataset) DO UPDATE SET
                 finmind_dataset=excluded.finmind_dataset,
                 data_version=excluded.data_version,
                 first_fetched_at=CASE WHEN sources.data_version=excluded.data_version THEN sources.first_fetched_at ELSE excluded.first_fetched_at END,
                 last_fetched_at=excluded.last_fetched_at,
                 n_requests=CASE WHEN sources.data_version=excluded.data_version THEN sources.n_requests+1 ELSE 1 END,
                 n_rows=CASE WHEN sources.data_version=excluded.data_version THEN sources.n_rows+excluded.n_rows ELSE excluded.n_rows END,
                 landing_filter=excluded.landing_filter,
                 n_filtered=CASE WHEN sources.data_version=excluded.data_version THEN sources.n_filtered+excluded.n_filtered ELSE excluded.n_filtered END,
                 min_date=CASE WHEN excluded.min_date IS NULL THEN sources.min_date
                               WHEN sources.min_date IS NULL OR sources.data_version<>excluded.data_version THEN excluded.min_date
                               ELSE MIN(sources.min_date, excluded.min_date) END,
                 max_date=CASE WHEN excluded.max_date IS NULL THEN sources.max_date
                               WHEN sources.max_date IS NULL OR sources.data_version<>excluded.data_version THEN excluded.max_date
                               ELSE MAX(sources.max_date, excluded.max_date) END,
                 columns=excluded.columns""",
            (dataset, finmind_dataset, data_version, now, now, n_inserted, mn, mx, json.dumps(cols),
             landing_filter, int(n_filtered or 0)),
        )

    # -- 查詢 -------------------------------------------------------------------
    def source_row(self, dataset: str) -> dict[str, Any] | None:
        """sources 表該 dataset 那一列（dict）；沒有回 None。report 用它讀 landing_filter／n_filtered。"""
        rows = self.fetch_rows("sources", "dataset=?", (dataset,))
        return dict(rows[0]) if rows else None

    def coverage_summary(self, dataset: str) -> dict[str, Any]:
        r = self.conn.execute(
            """SELECT SUM(status='ok'), SUM(status='empty'), SUM(n_rows), MIN(key), MAX(key),
                      COUNT(DISTINCT data_version), MAX(fetched_at)
               FROM coverage WHERE dataset=?""", (dataset,)).fetchone()
        f = self.conn.execute("SELECT COUNT(*) FROM failures WHERE dataset=?", (dataset,)).fetchone()[0]
        return {"ok": r[0] or 0, "empty": r[1] or 0, "rows": r[2] or 0, "min_key": r[3], "max_key": r[4],
                "versions": r[5] or 0, "last_fetched_at": r[6], "failures": f}

    def failures_list(self, dataset: str | None = None, limit: int = 50) -> list[tuple]:
        if dataset:
            q = "SELECT dataset, key, kind, message, attempted_at, attempts FROM failures WHERE dataset=? ORDER BY key LIMIT ?"
            return self.conn.execute(q, (dataset, limit)).fetchall()
        q = "SELECT dataset, key, kind, message, attempted_at, attempts FROM failures ORDER BY dataset, key LIMIT ?"
        return self.conn.execute(q, (limit,)).fetchall()

    def distinct_dates(self, table: str, stock_id: str | None = None) -> list[str]:
        if not self.table_exists(table):
            return []
        if stock_id is None:
            q = f'SELECT DISTINCT date FROM "{table}" WHERE date IS NOT NULL ORDER BY date'
            return [r[0] for r in self.conn.execute(q)]
        q = f'SELECT DISTINCT date FROM "{table}" WHERE stock_id=? AND date IS NOT NULL ORDER BY date'
        return [r[0] for r in self.conn.execute(q, (stock_id,))]

    def rows_for_key(self, table: str, key: str) -> int:
        if not self.table_exists(table):
            return 0
        return self.conn.execute(f'SELECT COUNT(*) FROM "{table}" WHERE cov_key=?', (key,)).fetchone()[0]

    def table_exists(self, table: str) -> bool:
        return bool(self.conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone())

    def fetch_rows(self, table: str, where: str = "", params: tuple = (), cols: str = "*") -> list[sqlite3.Row]:
        if not self.table_exists(table):
            return []
        old = self.conn.row_factory
        self.conn.row_factory = sqlite3.Row
        try:
            q = f'SELECT {cols} FROM "{table}"' + (f" WHERE {where}" if where else "")
            return self.conn.execute(q, params).fetchall()
        finally:
            self.conn.row_factory = old


def open_stores(cache_dir: Path, names: Iterable[str]) -> dict[str, Store]:
    return {n: Store(Path(cache_dir) / f"{n}.db") for n in names}
