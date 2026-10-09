"""`scan_features --resume` 的輸入指紋守門（`scan_inputs`，裁定 #73 C，2026-10-09）。

起因：例行輪只跑 `--resume`（只補寫新日），晚到的除權事件（6949 分割／1563 減資，ex 2026-09-07）落地後，已寫日的特徵
不會重算、rc 0、零訊號。守門改記「掃描當時的輸入指紋」，續跑時拿現況比；有差異 → rc 4、**不寫任何列**。

全部離線：合成 DB（`synth_db`）實跑整支腳本。「已寫最後日」一律用 `--rebuild --to DAYS[59]` 造出來，
之後才讓原料變動、再 `--resume`（＝例行輪的形狀）。
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import scan_features as S  # noqa: E402
from synth_db import DAYS, DV, INFO, add_adjust_source_rows, build  # noqa: E402

from iching import feed as F  # noqa: E402
from iching.features_io import (  # noqa: E402
    DATA_TABLES,
    FeatureStore,
    params_fingerprint,
)
from iching.liquidity import AdvTracker  # noqa: E402
from iching.scan import DailyScanner  # noqa: E402
from iching.store import Store  # noqa: E402

SCALE = 1e6
CUT = 59                        # 「已寫最後日」＝DAYS[CUT]
# 改動前（base 3ec091e）在合成 DB 上的 features 參數指紋；scan_inputs 不得進指紋（R1）
BASE_PARAMS_SHA = "084c1b9b8348"


@pytest.fixture(autouse=True)
def _ticking_clock(monkeypatch):
    """`features_io._now()` 精度到秒：rebuild 與 resume 同秒完成時，「rc 4 卻動了 scan_meta 時戳」量不出來。
    改成每呼叫一次前進一秒的假時鐘，任何多出來的 `set_params`（會 UPDATE last_written_at）都會讓傾印不同。"""
    import itertools
    from datetime import datetime, timedelta, timezone

    from iching import features_io
    t0, tick = datetime(2026, 10, 9, tzinfo=timezone.utc), itertools.count()
    monkeypatch.setattr(features_io, "_now", lambda: (t0 + timedelta(seconds=next(tick))).isoformat(timespec="seconds"))


def _cache(tmp_path: Path) -> Path:
    cache = tmp_path / "cache"
    build(cache, amount_scale=SCALE)
    return cache


def _scan(cache: Path, *extra: str, out: Path | None = None) -> int:
    return S.main(["--cache-dir", str(cache), "--out", str(out or cache / "features.db"), "--quiet", *extra])


def _dump(db: Path, *, inputs: bool = False) -> dict:
    """特徵七表＋scan_meta（不含時戳）逐位傾印；`inputs=True` 連 scan_inputs 與 scan_meta 時戳一起（驗「什麼都沒寫」）。"""
    c = sqlite3.connect(db)
    try:
        out = {t: c.execute(f'SELECT * FROM "{t}"').fetchall() for t in DATA_TABLES}
        out = {t: sorted(v, key=repr) for t, v in out.items()}
        cols = "data_version, schema_version, params_sha, params_json" + (", first_written_at, last_written_at" if inputs else "")
        out["scan_meta"] = c.execute(f"SELECT {cols} FROM scan_meta").fetchall()
        if inputs:
            out["scan_inputs"] = sorted(c.execute("SELECT * FROM scan_inputs").fetchall())
        return out
    finally:
        c.close()


def _dates(db: Path) -> list[str]:
    with FeatureStore(db) as fs:
        return fs.dates(DV)


def _cut(tmp_path: Path) -> tuple[Path, Path]:
    cache = _cache(tmp_path)
    assert _scan(cache, "--rebuild", "--to", DAYS[CUT]) == 0
    db = cache / "features.db"
    assert _dates(db) == DAYS[:CUT + 1]
    return cache, db


def _full_rebuild_dump(cache: Path, tmp_path: Path) -> dict:
    """同一份（現況）原料從頭 --rebuild 的結果——--resume 通過守門後應與它逐位相同。"""
    ref = tmp_path / "ref.db"
    assert _scan(cache, "--rebuild", out=ref) == 0
    return _dump(ref)


def _set_info(cache: Path, rows: list[dict]) -> None:
    with Store(cache / "universe.db") as u:
        u.record_success("stock_info", "raw_stock_info", "all", rows, DV, "TaiwanStockInfo", ("stock_id",))


# ---------------------------------------------------------------------------
# R1：不改計分、features 指紋不變、scan_inputs 不進指紋
def test_params_fingerprint_and_model_version_unchanged(tmp_path):
    from iching.score.params import build_params as score_params
    assert score_params("twse").model_version() == "p2-score-engine-3.4b5db7fc6f6d"
    assert score_params("tpex").model_version() == "p2-score-engine-3.15407a6adb13"
    params = S.build_params(DailyScanner(), AdvTracker())
    assert sorted(params) == ["adv_threshold", "adv_window", "hl_windows", "ma_windows", "p_cs_tie",
                              "p_cs_windows", "pool_semantics", "ret_windows"]
    assert params_fingerprint(params) == BASE_PARAMS_SHA
    cache = _cache(tmp_path)
    assert _scan(cache, "--rebuild") == 0
    with FeatureStore(cache / "features.db") as fs:
        assert fs.conn.execute("SELECT params_sha FROM scan_meta").fetchone()[0] == BASE_PARAMS_SHA
        assert "scan_inputs" not in fs.conn.execute("SELECT params_json FROM scan_meta").fetchone()[0]


def test_rebuild_writes_baseline(tmp_path):
    cache = _cache(tmp_path)
    assert _scan(cache, "--rebuild") == 0
    with FeatureStore(cache / "features.db") as fs:
        base = fs.load_inputs(DV)
    assert base is not None and base["meta"]["mode"] == "rebuild" and base["meta"]["version"] == S.INPUTS_VERSION
    assert sorted(base["day"]) == DAYS                                     # ③ 每個掃描日一列
    assert set(base["factor"]) == {"1101"}                                 # ① 與掃描用的同一份（合成 DB 只有 1101 除息）
    assert set(base["pool"]) == {"1101", "1102", "1103", "2330", "6488"}   # ② PitPool 靜態集合（ETF／DR 不在）
    assert base["meta"]["scan_from"] == DAYS[0] and base["meta"]["last_scanned"] == DAYS[-1]


def test_rebuild_output_identical_to_plain_pipeline(tmp_path):
    """**改動前後 --rebuild 產出的特徵表逐位相同**：參照＝改動前 `run()` 的迴圈本體（`iter_days`→`eligible`→`day_records`
    →`push_day`→`write_day`→`adv.push_day`，無任何守門），直接以函式庫寫進另一個 features.db，逐位比七表＋scan_meta。"""
    cache = _cache(tmp_path)
    add_adjust_source_rows(cache)                        # 多幾種事件，讓後復權路徑真的有作用
    assert _scan(cache, "--rebuild") == 0
    ref = tmp_path / "plain.db"
    prices, uni = F.open_ro(cache / "prices.db"), F.open_ro(cache / "universe.db")
    try:
        pool = F.load_pool(uni)
        factors, _s, _m = F.load_factors_full(prices, DV)
        idx = F.load_index(prices, DV)
        sc, adv = DailyScanner(), AdvTracker()
        with FeatureStore(ref) as fs:
            fs.set_params(DV, S.build_params(sc, adv))
            for d, rows in F.iter_days(prices, DV, False, DAYS[0], None):
                rp = adv.eligible()
                recs, amounts = F.day_records(d, rows, pool, factors, rank_pool=rp)
                fs.write_day(sc.push_day(d, recs, idx.get(d, {})), DV, rank_pool_size=len(rp),
                             adv_tracked=adv.n_tracked, adv_ready=adv.n_ready)
                adv.push_day(d, amounts)
    finally:
        prices.close()
        uni.close()
    got, want = _dump(cache / "features.db"), _dump(ref)
    assert got == want
    assert sum(len(v) for v in got.values()) > 2000                       # 不是兩邊都空


# ---------------------------------------------------------------------------
# R3／R5：--resume 的各情境
def test_resume_without_changes_is_noop_and_matches_full_rebuild(tmp_path):
    cache, db = _cut(tmp_path)
    assert _scan(cache, "--resume") == 0                                    # 補寫 60..79
    assert _dates(db) == DAYS
    assert _dump(db) == _full_rebuild_dump(cache, tmp_path)                 # 與從頭重建逐位相同（＝改動前行為）
    before = _dump(db)
    assert _scan(cache, "--resume") == 0                                    # 已完整：no-op
    assert _dump(db) == before
    with FeatureStore(db) as fs:
        base = fs.load_inputs(DV)
    assert base["meta"]["mode"] == "resume" and sorted(base["day"]) == DAYS   # 合併：③ 補上新日


def test_late_event_before_last_written_is_rc4_and_writes_nothing(tmp_path, capsys):
    cache, db = _cut(tmp_path)
    before = _dump(db, inputs=True)
    add_adjust_source_rows(cache, capred_i=50, split_i=65, par_i=70)         # 1101 減資 ex DAYS[50] ≤ 已寫最後日
    capsys.readouterr()
    assert _scan(cache, "--resume") == S.RC_STALE_INPUTS
    out = capsys.readouterr().out
    assert f"① 除權息 1101 ex {DAYS[50]}" in out and S.REBUILD_CMD in out and "未寫入任何列" in out
    assert "2330" not in out.split("處置")[0] and "6488" not in out.split("處置")[0]   # ex 在已寫日之後的兩檔不算
    assert _dump(db, inputs=True) == before                                 # 連 scan_meta 時戳／基準都沒動
    assert _dates(db) == DAYS[:CUT + 1]


def test_late_event_after_last_written_resumes_normally(tmp_path):
    cache, db = _cut(tmp_path)
    add_adjust_source_rows(cache, capred_i=CUT + 3, split_i=CUT + 6, par_i=CUT + 9)   # 全部晚於已寫最後日
    assert _scan(cache, "--resume") == 0
    assert _dates(db) == DAYS
    assert _dump(db) == _full_rebuild_dump(cache, tmp_path)


def test_price_revision_on_written_day_is_rc4(tmp_path, capsys):
    cache, db = _cut(tmp_path)
    before = _dump(db, inputs=True)
    c = sqlite3.connect(cache / "prices.db")
    c.execute("UPDATE raw_price_daily SET close=close*1.01 WHERE date=? AND stock_id='2330'", (DAYS[30],))
    c.commit()
    c.close()
    capsys.readouterr()
    assert _scan(cache, "--resume") == S.RC_STALE_INPUTS
    out = capsys.readouterr().out
    assert f"③ {DAYS[30]}：掃描輸入摘要不同" in out and "①" not in out
    assert _dump(db, inputs=True) == before


def test_non_pool_row_revision_does_not_trip(tmp_path):
    """ETF（不進池）的列改了不影響特徵——③ 摘要的是掃描輸入，不是 raw 全表，所以不得判 rc 4。"""
    cache, db = _cut(tmp_path)
    c = sqlite3.connect(cache / "prices.db")
    c.execute("UPDATE raw_price_daily SET close=close*1.5 WHERE date=? AND stock_id='0050'", (DAYS[30],))
    c.commit()
    c.close()
    assert _scan(cache, "--resume") == 0
    assert _dates(db) == DAYS


def test_pool_change_affecting_written_days_is_rc4(tmp_path, capsys):
    cache, db = _cut(tmp_path)
    before = _dump(db, inputs=True)
    _set_info(cache, [dict(r, industry_category="其他電子業") if r["stock_id"] == "2330" else r for r in INFO])
    capsys.readouterr()
    assert _scan(cache, "--resume") == S.RC_STALE_INPUTS
    out = capsys.readouterr().out
    assert "② 池指紋" in out and "'2330'" in out                              # ② 列出變動代號
    assert f"③ {DAYS[0]}：掃描輸入摘要不同" in out                             # 由 ③ 判出影響已寫日
    assert _dump(db, inputs=True) == before


def test_pool_change_not_affecting_written_days_resumes(tmp_path, capsys):
    """新掛牌（舊日沒有價格列）讓池指紋變了，但已寫日的掃描輸入不變——不得判 rc 4（否則每逢新股掛牌就要全量重播）。"""
    cache, db = _cut(tmp_path)
    _set_info(cache, INFO + [{"stock_id": "7777", "type": "twse", "industry_category": "水泥工業",
                              "stock_name": "新", "date": "2026-09-11"}])
    capsys.readouterr()
    assert _scan(cache, "--resume") == 0
    out = capsys.readouterr().out
    assert "② 池指紋" in out and "'7777'" in out
    assert _dates(db) == DAYS
    with FeatureStore(db) as fs:
        assert "7777" in fs.load_inputs(DV)["pool"]                         # 基準換成現況


def test_vanished_trading_day_is_rc4(tmp_path, capsys):
    cache, db = _cut(tmp_path)
    c = sqlite3.connect(cache / "prices.db")
    c.execute("DELETE FROM raw_price_daily WHERE date=?", (DAYS[30],))
    c.commit()
    c.close()
    capsys.readouterr()
    assert _scan(cache, "--resume") == S.RC_STALE_INPUTS
    assert f"③ {DAYS[30]}：基準有此交易日，現況原料沒有" in capsys.readouterr().out
    assert _dates(db) == DAYS[:CUT + 1]


def test_old_db_without_inputs_is_rc4_until_adopt(tmp_path, capsys):
    cache, db = _cut(tmp_path)
    c = sqlite3.connect(db)
    c.execute("DROP TABLE scan_inputs")                                     # 2026-10-09 前建的舊庫：沒有這張表
    c.commit()
    c.close()
    before = _dump(db)
    capsys.readouterr()
    assert _scan(cache, "--resume") == S.RC_STALE_INPUTS
    out = capsys.readouterr().out
    assert "沒有 data_version=" in out and "--adopt-inputs" in out and S.REBUILD_CMD in out
    assert _dump(db) == before and _dates(db) == DAYS[:CUT + 1]
    assert _scan(cache, "--adopt-inputs") == 2                              # 只能配 --resume
    assert _scan(cache, "--resume", "--adopt-inputs") == 0
    assert "!! --adopt-inputs" in capsys.readouterr().out
    assert _dates(db) == DAYS
    with FeatureStore(db) as fs:
        meta = fs.load_inputs(DV)["meta"]
    assert meta["mode"] == "adopt" and meta["adopted"] is True
    assert _scan(cache, "--resume", "--adopt-inputs") == 2                   # 已有基準：拒絕（不得洗掉守門）
    assert _scan(cache, "--resume") == 0                                    # 之後照常續跑


def test_non_resume_run_replaces_baseline_and_failure_leaves_none(tmp_path, monkeypatch):
    """非 --resume 的全量掃描開寫前先作廢舊基準：中途失敗時寧可沒有基準（下次 --resume rc 4），也不留一份與特徵表不符的。"""
    cache, db = _cut(tmp_path)
    boom = RuntimeError("中途失敗")

    def bad_write(self, *a, **k):
        raise boom
    monkeypatch.setattr(FeatureStore, "write_day", bad_write)
    with pytest.raises(RuntimeError):
        _scan(cache)
    monkeypatch.undo()
    with FeatureStore(db) as fs:
        assert fs.load_inputs(DV) is None
    assert _scan(cache, "--resume") == S.RC_STALE_INPUTS
    assert _scan(cache, "--rebuild") == 0
    assert _scan(cache, "--resume") == 0
