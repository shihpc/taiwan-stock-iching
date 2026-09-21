"""`scores.db`（第 13 項 13a-2）：重播分數的落地層。比照 `features_io.py`——四條 PRAGMA、複合 PK ＋
`WITHOUT ROWID` ＋ date 索引、逐日一交易、逐日先 DELETE 再 INSERT。

## 版本三元組正規化（裁定 #34 Q2 乙）

`scores` 存 `version_id INTEGER`，`versions(version_id, model_version, data_version, text_version)` 一張小表；
讀取端 `rows_for_day()` JOIN 還原 `spec/dimensions.json` 的 7 個邏輯鍵（`market, horizon, stock_id,
tpe_trading_date, model_version, data_version, text_version`）——**邏輯鍵不變，只是物理儲存正規化**
（第 12 項實測：14 字元字串進 `WITHOUT ROWID` PK 又被兩條索引各帶一份，每列 +43 bytes）。

## 欄位

`assemble_row(detail=False)` 的 43 欄攤平：純量進真欄；`lines_provisional`／`lines_formal` 存 6 字元 `"010010"`
（下爻在前，與 `assemble_row` 的 list 同序）或 NULL；`flags` 存 JSON TEXT（個股列 NULL）。
另加驅動端算的三欄：**`line_states`**（6 字元，`y`/`n`/`-`＝陽/陰/尚無狀態；遲滯 state 本體，
`lines_formal` 只在六爻皆有狀態時非 NULL，單看它會丟掉「五爻有狀態、一爻沒有」的資訊）、
**`streaks`**（6 個 int 逗號串）、**`in_rank_pool`**（個股列 0/1，大盤列 NULL）。

`replay_day`：每日診斷（算了幾檔、幾檔在池、幾檔任一爻未知、耗時、`index_missing`）。
`replay_meta`：`data_version` → 參數指紋（兩市場 `model_version`＋`text_version`＋視窗設定），不一致拒寫
（同 `features_io.set_params` 的理由：兩批口徑不同的列混在一起，parity 就是假的）。
"""
from __future__ import annotations

import datetime as dt
import json
import sqlite3
from pathlib import Path
from typing import Any, Iterable, Mapping

from .features_io import params_fingerprint

SCHEMA_VERSION = 2          # 2（2026-09-21，§18）：加 floor_applied／overheated／overheat_cap_applied 三欄
PRAGMAS = ("journal_mode = WAL", "synchronous = NORMAL", "cache_size = -64000", "temp_store = MEMORY")
LOGICAL_KEYS = ("market", "horizon", "stock_id", "tpe_trading_date", "model_version", "data_version", "text_version")
LINE_COLS = tuple(f"line_{k}{suf}" for k in range(1, 7) for suf in ("", "_unknown", "_coverage_ratio", "_reweighted"))
SCALAR_COLS = ("scope", *LINE_COLS, "lines_provisional", "king_wen_provisional", "hexagram_name_provisional",
               "lines_formal", "king_wen", "hexagram_name", "base_score", "inner_trigram_score", "outer_trigram_score",
               "coverage", "calibrated", "flags", "line_states", "streaks", "in_rank_pool",
               # §18（2026-09-21）：`:717` ⑧③ 要的 binding／旗標中間量；個股專屬，大盤列為 NULL。
               # 不進 `params_sha`（是輸出欄位不是計分規則），但 `SCHEMA_VERSION` 要 bump。
               "floor_applied", "overheated", "overheat_cap_applied")
SCORE_COLS = ("version_id", "market", "horizon", "stock_id", "date", *SCALAR_COLS)

_DDL = (
    """CREATE TABLE IF NOT EXISTS versions(
        version_id INTEGER PRIMARY KEY,
        model_version TEXT NOT NULL, data_version TEXT NOT NULL, text_version TEXT NOT NULL,
        UNIQUE(model_version, data_version, text_version))""",
    """CREATE TABLE IF NOT EXISTS scores(
        version_id INTEGER NOT NULL, market TEXT NOT NULL, horizon TEXT NOT NULL, stock_id TEXT NOT NULL, date TEXT NOT NULL,
        scope TEXT,
        line_1 REAL, line_1_unknown INTEGER, line_1_coverage_ratio REAL, line_1_reweighted INTEGER,
        line_2 REAL, line_2_unknown INTEGER, line_2_coverage_ratio REAL, line_2_reweighted INTEGER,
        line_3 REAL, line_3_unknown INTEGER, line_3_coverage_ratio REAL, line_3_reweighted INTEGER,
        line_4 REAL, line_4_unknown INTEGER, line_4_coverage_ratio REAL, line_4_reweighted INTEGER,
        line_5 REAL, line_5_unknown INTEGER, line_5_coverage_ratio REAL, line_5_reweighted INTEGER,
        line_6 REAL, line_6_unknown INTEGER, line_6_coverage_ratio REAL, line_6_reweighted INTEGER,
        lines_provisional TEXT, king_wen_provisional INTEGER, hexagram_name_provisional TEXT,
        lines_formal TEXT, king_wen INTEGER, hexagram_name TEXT,
        base_score REAL, inner_trigram_score REAL, outer_trigram_score REAL,
        coverage TEXT, calibrated INTEGER, flags TEXT,
        line_states TEXT, streaks TEXT, in_rank_pool INTEGER,
        -- §18（2026-09-21）：`:717` ⑧③ 的 binding／旗標中間量。三值語意（True／False／NULL＝不適用），
        -- **NULL 不等於 0**，算 binding 率時分母要排除 NULL；大盤列三欄皆 NULL。
        floor_applied INTEGER, overheated INTEGER, overheat_cap_applied INTEGER,
        PRIMARY KEY(version_id, market, horizon, stock_id, date)) WITHOUT ROWID""",
    """CREATE TABLE IF NOT EXISTS replay_day(
        data_version TEXT NOT NULL, date TEXT NOT NULL,
        model_version_twse TEXT, model_version_tpex TEXT,
        n_market_rows INTEGER, n_stocks INTEGER, n_in_pool INTEGER, n_stock_rows INTEGER,
        n_stock_any_unknown INTEGER, n_market_any_unknown INTEGER, elapsed_ms REAL, index_missing TEXT,
        PRIMARY KEY(data_version, date)) WITHOUT ROWID""",
    """CREATE TABLE IF NOT EXISTS replay_meta(
        data_version TEXT PRIMARY KEY, schema_version INTEGER NOT NULL, params_sha TEXT NOT NULL,
        params_json TEXT NOT NULL, first_written_at TEXT NOT NULL, last_written_at TEXT NOT NULL)""",
)
_INDEXES = (("idx_scores_date", "scores", "date"), ("idx_scores_stock", "scores", "stock_id"))
DATA_TABLES = ("scores", "replay_day")


class ScoreStoreError(RuntimeError):
    pass


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def bits_text(bits: Iterable[int] | None) -> str | None:
    if bits is None:
        return None
    s = "".join("1" if int(b) else "0" for b in bits)
    if len(s) != 6:
        raise ScoreStoreError(f"六爻位元需 6 位：{s!r}")
    return s


def bits_list(text: str | None) -> list[int] | None:
    return None if text is None else [int(ch) for ch in text]


def flags_text(fl: Any) -> str | None:
    """`flags` dict → `scores.flags` 的 JSON TEXT（鍵排序；讀回 `rows_for_day` 走 `json.loads`）。`flatten_row` 與
    `scripts/recompute_from_seed.py`（dump 值→檔案值）共用這一支，序列化參數只寫在這裡。"""
    return None if fl is None else json.dumps(fl, ensure_ascii=False, sort_keys=True, default=str)


def states_text(states: Iterable[str | None]) -> str:
    out = "".join("y" if s == "yang" else "n" if s == "yin" else "-" for s in states)
    if len(out) != 6:
        raise ScoreStoreError(f"爻狀態需 6 個：{out!r}")
    return out


def streaks_text(streaks: Iterable[int]) -> str:
    vals = [int(s) for s in streaks]
    if len(vals) != 6:
        raise ScoreStoreError(f"streaks 需 6 個：{vals}")
    return ",".join(str(v) for v in vals)


def _num(v: Any) -> float | None:
    """分數欄：float 或 None；`Missing`／非數字一律 None（**永不變 50**）。bool 不當數字。"""
    if v is None or isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    return None


def _int(v: Any) -> int | None:
    if v is None:
        return None
    if isinstance(v, bool):
        return int(v)
    if isinstance(v, (int, float)):
        return int(v)
    return None


def flatten_row(row: Mapping[str, Any], *, line_states: str, streaks: str, in_rank_pool: int | None) -> dict[str, Any]:
    """`assemble_row(detail=False)` 的 dict → `scores` 一列（不含 `version_id`，由 writer 補）。"""
    out: dict[str, Any] = {"market": row["market"], "horizon": row["horizon"], "stock_id": row["stock_id"],
                           "date": row["tpe_trading_date"], "scope": row.get("scope")}
    for k in range(1, 7):
        out[f"line_{k}"] = _num(row.get(f"line_{k}"))
        out[f"line_{k}_unknown"] = _int(row.get(f"line_{k}_unknown"))
        out[f"line_{k}_coverage_ratio"] = _num(row.get(f"line_{k}_coverage_ratio"))
        out[f"line_{k}_reweighted"] = _int(row.get(f"line_{k}_reweighted"))
    out["lines_provisional"] = bits_text(row.get("lines_provisional"))
    out["king_wen_provisional"] = _int(row.get("king_wen_provisional"))
    out["hexagram_name_provisional"] = row.get("hexagram_name_provisional")
    out["lines_formal"] = bits_text(row.get("lines_formal"))
    out["king_wen"] = _int(row.get("king_wen"))
    out["hexagram_name"] = row.get("hexagram_name")
    out["base_score"] = _num(row.get("base_score"))
    out["inner_trigram_score"] = _num(row.get("inner_trigram_score"))
    out["outer_trigram_score"] = _num(row.get("outer_trigram_score"))
    out["coverage"] = row.get("coverage")
    out["calibrated"] = _int(row.get("calibrated"))
    out["flags"] = flags_text(row.get("flags"))
    out["line_states"] = line_states
    out["streaks"] = streaks
    out["in_rank_pool"] = in_rank_pool
    # §18：三值語意（1／0／None＝不適用），`_int` 會把 None 原樣留著、不塌成 0。
    out["floor_applied"] = _int(row.get("floor_applied"))
    out["overheated"] = _int(row.get("overheated"))
    out["overheat_cap_applied"] = _int(row.get("overheat_cap_applied"))
    return out


class ScoreStore:
    def __init__(self, path: str | Path, *, readonly: bool = False) -> None:
        self.path = Path(path)
        self.readonly = readonly
        if readonly:
            if not self.path.exists():
                raise ScoreStoreError(f"scores.db 不存在：{self.path}")
            self.conn = sqlite3.connect(f"file:{self.path}?mode=ro", uri=True, isolation_level=None)
            self.conn.execute("PRAGMA query_only=1")
            self._vid: dict[tuple[str, str, str], int] = {}
            self._params_ok: set[str] = set()
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path, isolation_level=None)
        for p in PRAGMAS:
            self.conn.execute(f"PRAGMA {p}")
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
        self._vid: dict[tuple[str, str, str], int] = {}
        self._params_ok: set[str] = set()                      # 本連線已通過 set_params 的 data_version

    def __enter__(self) -> "ScoreStore":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def close(self) -> None:
        self.conn.close()

    # -- 版本 --
    def version_id(self, model_version: str, data_version: str, text_version: str) -> int:
        key = (model_version, data_version, text_version)
        if key in self._vid:
            return self._vid[key]
        row = self.conn.execute("SELECT version_id FROM versions WHERE model_version=? AND data_version=? AND text_version=?", key).fetchone()
        if row is None:
            self.conn.execute("INSERT INTO versions(model_version, data_version, text_version) VALUES(?,?,?)", key)
            row = self.conn.execute("SELECT version_id FROM versions WHERE model_version=? AND data_version=? AND text_version=?", key).fetchone()
        self._vid[key] = int(row[0])
        return self._vid[key]

    def versions(self) -> list[dict]:
        return [dict(zip(("version_id", "model_version", "data_version", "text_version"), r))
                for r in self.conn.execute("SELECT version_id, model_version, data_version, text_version FROM versions ORDER BY version_id")]

    # -- 參數 --
    def set_params(self, data_version: str, params: dict) -> str:
        sha = params_fingerprint(params)
        row = self.conn.execute("SELECT schema_version, params_sha, params_json FROM replay_meta WHERE data_version=?", (data_version,)).fetchone()
        if row is None:
            self.conn.execute("INSERT INTO replay_meta VALUES(?,?,?,?,?,?)",
                              (data_version, SCHEMA_VERSION, sha, json.dumps(params, sort_keys=True, ensure_ascii=False), _now(), _now()))
            self._params_ok.add(data_version)
            return sha
        old_schema, old_sha, old_json = row
        if old_schema != SCHEMA_VERSION:
            raise ScoreStoreError(f"{self.path} 的 schema_version={old_schema}，本程式是 {SCHEMA_VERSION}；不做遷移")
        if old_sha != sha:
            raise ScoreStoreError(f"data_version={data_version} 已用不同參數寫過：舊 {old_sha} 新 {sha}。\n  舊參數＝{old_json}\n"
                                  f"  新參數＝{json.dumps(params, sort_keys=True, ensure_ascii=False)}\n要重跑請先 clear()。")
        self.conn.execute("UPDATE replay_meta SET last_written_at=? WHERE data_version=?", (_now(), data_version))
        self._params_ok.add(data_version)
        return sha

    def params_of(self, data_version: str) -> dict | None:
        row = self.conn.execute("SELECT params_json FROM replay_meta WHERE data_version=?", (data_version,)).fetchone()
        return None if row is None else json.loads(row[0])

    def params_sha_of(self, data_version: str) -> str | None:
        """`replay_meta.params_sha`（唯讀）：D-3 parity 儀式核對 `data/scores/<T>.json` 頂層 `params_sha` 用；沒寫過回 None。"""
        row = self.conn.execute("SELECT params_sha FROM replay_meta WHERE data_version=?", (data_version,)).fetchone()
        return None if row is None else str(row[0])

    def clear(self, data_version: str) -> dict[str, int]:
        c = self.conn
        n: dict[str, int] = {}
        c.execute("BEGIN")
        try:
            vids = [r[0] for r in c.execute("SELECT version_id FROM versions WHERE data_version=?", (data_version,))]
            n["scores"] = 0
            for vid in vids:
                n["scores"] += c.execute("DELETE FROM scores WHERE version_id=?", (vid,)).rowcount
            n["replay_day"] = c.execute("DELETE FROM replay_day WHERE data_version=?", (data_version,)).rowcount
            n["versions"] = c.execute("DELETE FROM versions WHERE data_version=?", (data_version,)).rowcount
            n["replay_meta"] = c.execute("DELETE FROM replay_meta WHERE data_version=?", (data_version,)).rowcount
            c.execute("COMMIT")
        except BaseException:
            c.execute("ROLLBACK")
            raise
        self._vid = {}
        self._params_ok.discard(data_version)
        return n

    # -- 寫 --
    def write_day(self, data_version: str, date: str, rows: Iterable[tuple[str, dict[str, Any]]], diag: dict[str, Any]) -> int:
        """寫一個交易日。`rows`＝`(model_version, flatten_row(...))` 序列（`text_version` 從 diag 取）；
        同一 `(data_version, date)` 整日取代（先 DELETE 該日該 data_version 所有版本的列）。"""
        if data_version not in self._params_ok:
            raise ScoreStoreError(f"寫入前必須先對 {data_version} 呼叫 set_params()（參數指紋拒混寫是靠它）")
        tv = str(diag["text_version"])
        c = self.conn
        c.execute("BEGIN")
        try:
            vids = [r[0] for r in c.execute("SELECT version_id FROM versions WHERE data_version=?", (data_version,))]
            for vid in vids:
                c.execute("DELETE FROM scores WHERE version_id=? AND date=?", (vid, date))
            c.execute("DELETE FROM replay_day WHERE data_version=? AND date=?", (data_version, date))
            ph = ",".join("?" for _ in SCORE_COLS)
            sql = f'INSERT INTO scores({",".join(SCORE_COLS)}) VALUES({ph})'     # 同批撞鍵＝驅動端 bug，要炸不要 last-wins
            n = 0
            for mv, r in rows:
                vid = self.version_id(mv, data_version, tv)
                if r["date"] != date:
                    raise ScoreStoreError(f"列日期 {r['date']} ≠ 寫入日 {date}")
                try:
                    c.execute(sql, (vid, *[r.get(k) for k in SCORE_COLS[1:]]))
                except sqlite3.IntegrityError as e:
                    raise ScoreStoreError(f"同批重複鍵 {(r['market'], r['horizon'], r['stock_id'], date)}：{e}") from e
                n += 1
            c.execute("INSERT OR REPLACE INTO replay_day VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                      (data_version, date, diag.get("model_version_twse"), diag.get("model_version_tpex"),
                       diag.get("n_market_rows"), diag.get("n_stocks"), diag.get("n_in_pool"), diag.get("n_stock_rows"),
                       diag.get("n_stock_any_unknown"), diag.get("n_market_any_unknown"), diag.get("elapsed_ms"),
                       ",".join(diag.get("index_missing") or [])))
            c.execute("COMMIT")
        except BaseException:
            c.execute("ROLLBACK")
            self._vid = {}          # 交易內新增的 versions 列一起回滾了，快取不能留
            raise
        return n

    # -- 讀 --
    def dates(self, data_version: str) -> list[str]:
        return [r[0] for r in self.conn.execute("SELECT date FROM replay_day WHERE data_version=? ORDER BY date", (data_version,))]

    def counts(self, data_version: str) -> dict[str, int]:
        vids = [r[0] for r in self.conn.execute("SELECT version_id FROM versions WHERE data_version=?", (data_version,))]
        n = 0
        for vid in vids:
            n += self.conn.execute("SELECT COUNT(*) FROM scores WHERE version_id=?", (vid,)).fetchone()[0]
        d = self.conn.execute("SELECT COUNT(*) FROM replay_day WHERE data_version=?", (data_version,)).fetchone()[0]
        return {"scores": n, "replay_day": d}

    def rows_for_day(self, data_version: str, date: str) -> list[dict[str, Any]]:
        """JOIN `versions` 還原 7 個邏輯鍵；`lines_*` 還原成 list；`flags` 還原成 dict。決定性排序。"""
        cols = ", ".join(f"s.{c}" for c in SCALAR_COLS)
        sql = (f"SELECT s.market, s.horizon, s.stock_id, s.date, v.model_version, v.data_version, v.text_version, {cols} "
               f"FROM scores s JOIN versions v ON v.version_id = s.version_id "
               f"WHERE v.data_version=? AND s.date=? ORDER BY s.market, s.stock_id, s.horizon, v.model_version, v.text_version")
        out = []
        for r in self.conn.execute(sql, (data_version, date)):
            d = dict(zip((*LOGICAL_KEYS, *SCALAR_COLS), r))
            d["lines_provisional"] = bits_list(d["lines_provisional"])
            d["lines_formal"] = bits_list(d["lines_formal"])
            d["flags"] = None if d["flags"] is None else json.loads(d["flags"])
            out.append(d)
        return out

    def missing_dates(self, data_version: str, expected: Iterable[str]) -> list[str]:
        have = set(self.dates(data_version))
        return [d for d in expected if d not in have]

    def day_diag(self, data_version: str, date: str) -> dict | None:
        cols = ("data_version", "date", "model_version_twse", "model_version_tpex", "n_market_rows", "n_stocks", "n_in_pool",
                "n_stock_rows", "n_stock_any_unknown", "n_market_any_unknown", "elapsed_ms", "index_missing")
        r = self.conn.execute(f"SELECT {','.join(cols)} FROM replay_day WHERE data_version=? AND date=?", (data_version, date)).fetchone()
        return None if r is None else dict(zip(cols, r))
