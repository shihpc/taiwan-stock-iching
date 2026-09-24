"""`scores.db` 實體表結構檢查（2026-09-24，Hetzner A 重播事故的根因修正）。

事故：schema 1 的舊 `scores.db`（沒有 §18 的 floor_applied／overheated／overheat_cap_applied 三欄）跑
`replay_scores.py --rebuild` → `clear(dv)` 先把該 data_version 的列與 `replay_meta` 列刪光 → `set_params`
找不到舊列、照新版寫入（schema 守門只看 `replay_meta`，就此消失）→ `write_day` 才撞上
`table scores has no column named floor_applied`。舊列已刪，當時只能刪檔重跑。

本檔的舊版 db **由真實 replay 產出後再 DROP 三欄**造出，不是手寫 DDL——手寫的舊 schema 會跟著現行 DDL
一起被改掉，測不到「舊檔」。
"""
from __future__ import annotations

import hashlib
import sqlite3
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import replay_scores as R  # noqa: E402
import scan_features as SF  # noqa: E402
from iching import scores_io as SIO  # noqa: E402
from iching.scores_io import ScoreStore, ScoreStoreError  # noqa: E402
from synth_db import DV, build_full  # noqa: E402

#: §18 加的三欄（本檔獨立寫死，不由 DECLARED_COLUMNS 推得）。
S18 = ["floor_applied", "overheated", "overheat_cap_applied"]


@pytest.fixture(scope="module")
def cache(tmp_path_factory) -> Path:
    c = tmp_path_factory.mktemp("schema") / "cache"
    build_full(c)
    assert SF.main(["--cache-dir", str(c), "--quiet", "--allow-short-warmup", "--warmup-days", "0"]) == 0
    return c


@pytest.fixture(scope="module")
def current_db(cache, tmp_path_factory) -> Path:
    out = tmp_path_factory.mktemp("cur") / "s.db"
    assert R.main(["--cache-dir", str(cache), "--out", str(out), "--window", "30", "--quiet", "--limit-days", "3"]) == 0
    return out


def _copy(src: Path, dst: Path) -> Path:
    with sqlite3.connect(src) as a, sqlite3.connect(dst) as b:
        a.backup(b)
    return dst


def _settle(db: Path) -> None:
    c = sqlite3.connect(db)
    c.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    c.close()


def _sha(db: Path) -> str:
    return hashlib.sha256(db.read_bytes()).hexdigest()


def _n_scores(db: Path) -> int:
    c = sqlite3.connect(db)
    try:
        return c.execute("SELECT COUNT(*) FROM scores").fetchone()[0]
    finally:
        c.close()


@pytest.fixture()
def old_db(current_db, tmp_path) -> Path:
    """事故現場：schema 1 的舊檔（有資料列、缺 §18 三欄、replay_meta.schema_version=1）。"""
    db = _copy(current_db, tmp_path / "old.db")
    c = sqlite3.connect(db)
    for col in S18:
        c.execute(f"ALTER TABLE scores DROP COLUMN {col}")
    c.execute("UPDATE replay_meta SET schema_version=1")
    c.commit()
    c.close()
    _settle(db)
    assert _n_scores(db) > 0
    return db


# ---- K1／K2：舊檔開啟即拒，檔案不動 ----

def test_old_db_rw_refused_and_untouched(old_db):
    sha, n = _sha(old_db), _n_scores(old_db)
    with pytest.raises(ScoreStoreError, match=r"缺少 \['floor_applied', 'overheated', 'overheat_cap_applied'\]"):
        ScoreStore(old_db)
    _settle(old_db)
    assert _sha(old_db) == sha and _n_scores(old_db) == n


def test_old_db_ro_refused_with_store_error(old_db):
    """唯讀開啟也要是 ScoreStoreError（原本要到 rows_for_day 才炸 sqlite3.OperationalError）。"""
    with pytest.raises(ScoreStoreError, match="scores 表結構"):
        ScoreStore(old_db, readonly=True)


# ---- K3：事故路徑 --rebuild 不得先刪列 ----

def test_rebuild_on_old_db_aborts_before_clearing(cache, old_db, capsys):
    n = _n_scores(old_db)
    rc = R.main(["--cache-dir", str(cache), "--out", str(old_db), "--window", "30", "--quiet", "--rebuild", "--limit-days", "3"])
    err = capsys.readouterr().err
    assert rc == 2, err
    assert _n_scores(old_db) == n                     # 修正前：clear() 已把列刪光才炸
    assert "表結構" in err and "Traceback" not in err


# ---- K4：現行結構照常開 ----

def test_current_db_opens_rw_and_ro(current_db, tmp_path):
    db = _copy(current_db, tmp_path / "c.db")
    with ScoreStore(db) as s:
        assert s.dates(DV)
    with ScoreStore(db, readonly=True) as s:
        assert s.dates(DV)


def test_fresh_and_empty_file_rw_ok(tmp_path):
    with ScoreStore(tmp_path / "new.db") as s:
        assert s.versions() == []
    empty = tmp_path / "empty.db"
    sqlite3.connect(empty).close()                    # 0 張表的 sqlite 檔：讀寫模式照建
    with ScoreStore(empty) as s:
        assert s.versions() == []


def test_empty_file_ro_refused(tmp_path):
    empty = tmp_path / "empty.db"
    sqlite3.connect(empty).close()
    with pytest.raises(ScoreStoreError, match="整張表不存在"):
        ScoreStore(empty, readonly=True)


# ---- K5：多欄、順序不同也拒 ----

def test_extra_column_refused(current_db, tmp_path):
    """新版程式寫的檔被舊版程式開：多出未知欄也要拒，不得當沒看到。"""
    db = _copy(current_db, tmp_path / "x.db")
    c = sqlite3.connect(db)
    c.execute("ALTER TABLE scores ADD COLUMN future_col INTEGER")
    c.commit()
    c.close()
    with pytest.raises(ScoreStoreError, match=r"多出 \['future_col'\]"):
        ScoreStore(db)


def test_column_order_refused(tmp_path):
    """replay_meta 的 INSERT 依位置寫入，欄位集合相同但順序不同也要拒。"""
    db = tmp_path / "o.db"
    c = sqlite3.connect(db)
    c.execute("""CREATE TABLE replay_meta(
        schema_version INTEGER NOT NULL, data_version TEXT PRIMARY KEY, params_sha TEXT NOT NULL,
        params_json TEXT NOT NULL, first_written_at TEXT NOT NULL, last_written_at TEXT NOT NULL)""")
    c.commit()
    c.close()
    with pytest.raises(ScoreStoreError, match="欄位相同但順序不同"):
        ScoreStore(db)
    # 拒開要整筆 ROLLBACK：不得替這個檔補建其他三張表（驗收 N3：檢查移到 COMMIT 之後的突變原本全綠）
    c = sqlite3.connect(db)
    try:
        assert [r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")] == ["replay_meta"]
    finally:
        c.close()


@pytest.mark.parametrize("table", ["versions", "replay_day", "replay_meta"])
def test_extra_column_refused_other_tables(current_db, tmp_path, table):
    """四張表都要驗（驗收 N4：跳過 versions 的突變原本全綠）。"""
    db = _copy(current_db, tmp_path / f"{table}.db")
    c = sqlite3.connect(db)
    c.execute(f"ALTER TABLE {table} ADD COLUMN junk INTEGER")
    c.commit()
    c.close()
    for ro in (False, True):
        with pytest.raises(ScoreStoreError, match=rf"{table} 表結構.*不符：多出 \['junk'\]。"):
            ScoreStore(db, readonly=ro)


def test_message_is_generic_and_accurate(old_db):
    """訊息由共用 check_schema 發出，唯讀消費端也會看到：補救方法不得只對 replay_scores 成立（驗收 N1），
    也不得宣稱「不會動這個檔」（WAL 寫回、journal_mode 會變位元組，驗收 N2）；空清單不印（N6）。"""
    with pytest.raises(ScoreStoreError) as ei:
        ScoreStore(old_db, readonly=True)
    msg = str(ei.value)
    assert "重播產生新的 scores.db" in msg and "拒開時不改動表與資料列" in msg
    assert "不會動這個檔" not in msg and "多出 []" not in msg


# ---- 宣告的一致性 ----

def test_declared_scores_columns_match_insert_list():
    """由 DDL 讀回的 scores 欄＝寫入用的 SCORE_COLS（兩份各自維護，對不上時 write_day 與檢查會打架）。"""
    assert SIO.DECLARED_COLUMNS["scores"] == SIO.SCORE_COLS
    assert set(S18) <= set(SIO.DECLARED_COLUMNS["scores"])
    assert set(SIO.DECLARED_COLUMNS) == {"versions", "scores", "replay_day", "replay_meta"}
