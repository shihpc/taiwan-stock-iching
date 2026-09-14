"""第 13 項 13a-3：`scripts/replay_scores.py` 驅動 ＋ `scripts/check_scores.py` 健檢（合成 DB 實跑整支腳本）。

守的事：全量落地日期完整；`--limit-days`；`--resume` 從快照續跑後**與一次跑完逐位相同**（中斷續跑的 parity）；
`--from` 沒有 `--state` 拒跑、快照日期對不上拒跑；已有資料未 `--rebuild` 拒跑；壞路徑 rc=2 不吐 traceback；
`check_scores.py` 對產物 rc=0；`ReplaySource` 缺 date 索引拒跑。
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

import check_scores as CK  # noqa: E402
import diff_scores as DF  # noqa: E402
import replay_scores as R  # noqa: E402
import scan_features as SF  # noqa: E402
from iching import replay_io as RIO  # noqa: E402
from iching.scores_io import ScoreStore  # noqa: E402
from synth_db import DAYS, DV, build_full  # noqa: E402


@pytest.fixture(scope="module")
def cache(tmp_path_factory) -> Path:
    c = tmp_path_factory.mktemp("drv") / "cache"
    build_full(c)
    assert SF.main(["--cache-dir", str(c), "--quiet", "--allow-short-warmup", "--warmup-days", "0"]) == 0
    return c


def _rows(db: Path, date: str):
    with ScoreStore(db, readonly=True) as s:
        return s.rows_for_day(DV, date)


def _all(db: Path):
    with ScoreStore(db, readonly=True) as s:
        return {d: s.rows_for_day(DV, d) for d in s.dates(DV)}


def test_full_run_lands_all_days_and_writes_state(cache, tmp_path, capsys):
    out = tmp_path / "s.db"
    rc = R.main(["--cache-dir", str(cache), "--out", str(out), "--window", "30", "--progress-every", "10"])
    text = capsys.readouterr().out
    assert rc == 0, text
    assert "日期完整性：預期 80 日，全部落地" in text and "RSS" in text
    st = Path(str(out) + ".state.json")
    assert st.exists() and '"last_date":"2020-04-20"' in st.read_text(encoding="utf-8")
    with ScoreStore(out, readonly=True) as s:
        assert s.dates(DV) == DAYS and len(s.versions()) == 2
    # 再跑一次不加 --rebuild → 拒
    assert R.main(["--cache-dir", str(cache), "--out", str(out), "--window", "30", "--quiet"]) == 2
    err = capsys.readouterr().err
    assert "--rebuild" in err and "Traceback" not in err
    # check_scores 對產物 rc=0
    assert CK.main([str(out)]) == 0
    rep = capsys.readouterr().out
    assert "versions（2 筆" in rep and "末日 2020-04-20 大盤列" in rep and "lines_formal 非 NULL" in rep


def test_limit_days_and_rebuild(cache, tmp_path, capsys):
    out = tmp_path / "l.db"
    assert R.main(["--cache-dir", str(cache), "--out", str(out), "--window", "30", "--quiet", "--limit-days", "5"]) == 0
    with ScoreStore(out, readonly=True) as s:
        assert s.dates(DV) == DAYS[:5]
    assert R.main(["--cache-dir", str(cache), "--out", str(out), "--window", "30", "--quiet", "--rebuild", "--limit-days", "3"]) == 0
    with ScoreStore(out, readonly=True) as s:
        assert s.dates(DV) == DAYS[:3]
    capsys.readouterr()


def test_resume_from_snapshot_matches_single_run(cache, tmp_path, capsys):
    """中斷續跑的 parity：跑 50 日 → --resume 跑完 → 與一次跑完 80 日的每一列逐位相同、快照逐字相同。"""
    full, part = tmp_path / "full.db", tmp_path / "part.db"
    assert R.main(["--cache-dir", str(cache), "--out", str(full), "--window", "30", "--quiet"]) == 0
    assert R.main(["--cache-dir", str(cache), "--out", str(part), "--window", "30", "--quiet", "--limit-days", "50", "--state-every", "7"]) == 0
    with ScoreStore(part, readonly=True) as s:
        assert s.dates(DV) == DAYS[:50]
    assert R.main(["--cache-dir", str(cache), "--out", str(part), "--window", "30", "--quiet", "--resume"]) == 0
    a, b = _all(full), _all(part)
    assert list(a) == list(b) == DAYS
    for d in DAYS:
        assert a[d] == b[d], d
    assert Path(str(full) + ".state.json").read_text(encoding="utf-8") == Path(str(part) + ".state.json").read_text(encoding="utf-8")
    # 再 --resume 一次：沒有新日期 → rc 0、不改動
    assert R.main(["--cache-dir", str(cache), "--out", str(part), "--window", "30", "--quiet", "--resume"]) == 0
    assert "沒有新日期" in capsys.readouterr().out
    assert _all(part) == a


def test_from_requires_matching_state(cache, tmp_path, capsys):
    out = tmp_path / "f.db"
    assert R.main(["--cache-dir", str(cache), "--out", str(out), "--window", "30", "--quiet", "--from", DAYS[40]]) == 2
    assert "--state" in capsys.readouterr().err
    # 用前 40 日的快照從第 41 日起跑 → 與全量逐位相同（這就是 --from 的正確用法）
    base = tmp_path / "base.db"
    assert R.main(["--cache-dir", str(cache), "--out", str(base), "--window", "30", "--quiet", "--limit-days", "40"]) == 0
    snap = Path(str(base) + ".state.json")
    snap_text0 = snap.read_text(encoding="utf-8")
    cont = tmp_path / "cont.db"
    assert R.main(["--cache-dir", str(cache), "--out", str(cont), "--window", "30", "--quiet", "--from", DAYS[40], "--state", str(snap)]) == 0
    full = tmp_path / "full.db"
    assert R.main(["--cache-dir", str(cache), "--out", str(full), "--window", "30", "--quiet"]) == 0
    with ScoreStore(cont, readonly=True) as s:
        assert s.dates(DV) == DAYS[40:]
    for d in DAYS[40:]:
        assert _rows(cont, d) == _rows(full, d), d
    assert snap.read_text(encoding="utf-8") == snap_text0                   # 輸入快照不被覆寫（13a-3 驗收抓到）
    assert Path(str(cont) + ".state.json").exists()
    # --window 與快照不符 → 拒（window 不在 replay_meta 之外任何地方，靠快照 meta 守）
    assert R.main(["--cache-dir", str(cache), "--out", str(tmp_path / "w.db"), "--window", "20", "--quiet", "--from", DAYS[40], "--state", str(snap)]) == 2
    assert "不符" in capsys.readouterr().err
    # 快照日期對不上（用第 40 日的快照從第 42 日起）→ 拒
    assert R.main(["--cache-dir", str(cache), "--out", str(tmp_path / "x.db"), "--window", "30", "--quiet", "--from", DAYS[42], "--state", str(snap)]) == 2
    assert "前一交易日" in capsys.readouterr().err
    # --resume 與 --from 互斥；快照與 DB 不同步 → 拒
    assert R.main(["--cache-dir", str(cache), "--out", str(full), "--window", "30", "--quiet", "--resume", "--from", DAYS[1]]) == 2
    bad = tmp_path / "bad.db"
    assert R.main(["--cache-dir", str(cache), "--out", str(bad), "--window", "30", "--quiet", "--limit-days", "3"]) == 0
    Path(str(bad) + ".state.json").write_text(snap.read_text(encoding="utf-8"), encoding="utf-8")   # 第 40 日的快照配 3 日的 DB
    assert R.main(["--cache-dir", str(cache), "--out", str(bad), "--window", "30", "--quiet", "--resume"]) == 2
    assert "不同步" in capsys.readouterr().err


def test_bad_paths_exit_2_without_traceback(tmp_path, capsys):
    assert R.main(["--cache-dir", str(tmp_path / "沒有"), "--quiet"]) == 2
    assert "Traceback" not in capsys.readouterr().err
    assert CK.main([str(tmp_path / "沒有.db")]) == 2


def test_replay_source_refuses_missing_date_index(cache, tmp_path):
    import shutil
    c2 = tmp_path / "noidx"
    shutil.copytree(cache, c2)
    conn = sqlite3.connect(c2 / "chips.db")
    names = [r[1] for r in conn.execute("PRAGMA index_list('raw_margin')") if r[3] == "c"]   # 只砍 CREATE INDEX 建的，PK 自動索引砍不掉
    assert names
    for n in names:
        conn.execute(f'DROP INDEX "{n}"')
    conn.commit()
    conn.close()
    with pytest.raises(RIO.ReplayIOError) as ei:
        RIO.ReplaySource(c2, DV)
    assert "raw_margin" in str(ei.value) and "reindex" in str(ei.value)
    # 驅動層對同一情況 rc=2
    assert R.main(["--cache-dir", str(c2), "--out", str(tmp_path / "n.db"), "--quiet"]) == 2


def test_fundamentals_bridge_feeds_line1_and_is_a_param(cache, tmp_path, capsys):
    """13b：預設接基本面橋 → 1101（fixture 有 2019-01 起月營收＋五期季報）在 2020-04-10（2020-03 營收可用日）起
    短線初爻有值；`--no-fundamentals` 則初爻整條缺、且參數指紋不同（不得混寫進同一個 scores.db）。"""
    on, off = tmp_path / "on.db", tmp_path / "off.db"
    assert R.main(["--cache-dir", str(cache), "--out", str(on), "--window", "30", "--quiet"]) == 0
    text = capsys.readouterr().out
    assert "基本面橋：2 檔有原料" in text and "有月營收 2 檔、有季報 1 檔" in text
    def l1(db, d, sid, h):
        return [r for r in _rows(db, d) if r["stock_id"] == sid and r["horizon"] == h][0]
    def first_non_none(db, sid, h):
        return next((d for d in DAYS if l1(db, d, sid, h)["line_1"] is not None), None)
    # PIT 邊界：短線族 A 是「最新單月 YoY」→ 2020-01 營收可用日 02-10 起有值、02-09 None；
    # 波段／中期是三月合計 YoY → 需 2020-01～03 ＋ 2019 同期 → 2020-03 營收可用日 04-10 起（13b 驗收更正：原寫短線也是 04-10）
    assert first_non_none(on, "1101", "short") == "2020-02-10" and first_non_none(on, "1101", "swing") == "2020-04-10"
    assert first_non_none(on, "1101", "mid") == "2020-04-10"
    assert l1(on, "2020-02-09", "1101", "short")["line_1"] is None and l1(on, "2020-04-09", "1101", "mid")["line_1"] is None
    late = "2020-04-10"
    r = l1(on, late, "1101", "short")
    assert r["line_1"] is not None and r["line_1_unknown"] == 0
    assert first_non_none(on, "2330", "short") is None                          # 2330 只有 2020-01 起 3 個月，無去年同期
    mid = l1(on, late, "1101", "mid")
    assert mid["line_1"] is not None                                            # 族 B（EPS YoY 1.4/1.0）＋族 A
    assert R.main(["--cache-dir", str(cache), "--out", str(off), "--window", "30", "--quiet", "--no-fundamentals"]) == 0
    assert l1(off, late, "1101", "short")["line_1"] is None
    with ScoreStore(on, readonly=True) as a, ScoreStore(off, readonly=True) as b:
        assert a.params_of(DV)["fundamentals"] is True and b.params_of(DV)["fundamentals"] is False
    # 同一個檔換開關 → 參數指紋不同 → 拒
    assert R.main(["--cache-dir", str(cache), "--out", str(on), "--window", "30", "--quiet", "--no-fundamentals", "--resume"]) == 2
    assert "已用不同參數寫過" in capsys.readouterr().err


def test_rebuild_start_intersects_index_days_and_resume_still_matches(cache, tmp_path):
    """13a-3 驗收抓到的方向性錯誤：缺一天指數列時，個股候選若不與指數日取交集，起點會太晚、視窗少一列。
    做法：砍掉 DAYS[40] 兩市場指數列 → 1102（第 30、31 日停牌）的第 30 個「有成交 ∩ 有指數」日＝DAYS[17]；
    `rebuild_start(DAYS[50], 30)` 必須 ≤ DAYS[17]，且 `--resume` 續跑仍與全量逐位相同。"""
    import shutil
    c2 = tmp_path / "gap"
    shutil.copytree(cache, c2)
    conn = sqlite3.connect(c2 / "prices.db")
    conn.execute("DELETE FROM raw_index_price WHERE date=?", (DAYS[40],))
    conn.commit()
    conn.close()
    src = RIO.ReplaySource(c2, DV, window=30)
    assert src.rebuild_start(DAYS[50], 30) <= DAYS[17]
    assert src.rebuild_start(DAYS[0], 30) is None
    src.close()
    full, part = tmp_path / "gfull.db", tmp_path / "gpart.db"
    assert R.main(["--cache-dir", str(c2), "--out", str(full), "--window", "30", "--quiet"]) == 0
    assert R.main(["--cache-dir", str(c2), "--out", str(part), "--window", "30", "--quiet", "--limit-days", "50"]) == 0
    assert R.main(["--cache-dir", str(c2), "--out", str(part), "--window", "30", "--quiet", "--resume"]) == 0
    a, b = _all(full), _all(part)
    assert list(a) == list(b) and all(a[d] == b[d] for d in a)
    with ScoreStore(full, readonly=True) as s:
        assert s.day_diag(DV, DAYS[40])["index_missing"] == "twse,tpex"       # 那天兩市場都沒算


def test_bad_period_end_in_fundamentals_exits_2_without_traceback(cache, tmp_path, capsys):
    """13b 驗收必修：`FundamentalsError`（非季末期別等）要走 rc=2，不吐 traceback。"""
    import shutil
    c2 = tmp_path / "badp"
    shutil.copytree(cache, c2)
    conn = sqlite3.connect(c2 / "fundamentals.db")
    conn.execute("INSERT INTO raw_financial_statements(cov_key, data_version, date, stock_id, type, origin_name, value, row_hash) "
                 "VALUES('x', ?, '2019-05-31', '1101', 'EPS', '基本每股盈餘', 1.0, 'h')", (DV,))
    conn.commit()
    conn.close()
    assert R.main(["--cache-dir", str(c2), "--out", str(tmp_path / "b.db"), "--window", "30", "--quiet"]) == 2
    err = capsys.readouterr().err
    assert "Traceback" not in err and "期別末日" in err


def test_diff_scores_tool(cache, tmp_path, capsys):
    """`scripts/diff_scores.py`：同原料兩次跑 → 逐位相同 rc=0；改一列 → rc=1 並指出鍵與欄；壞檔 rc=2。"""
    a, b = tmp_path / "da.db", tmp_path / "db.db"
    assert R.main(["--cache-dir", str(cache), "--out", str(a), "--window", "30", "--quiet", "--limit-days", "10"]) == 0
    assert R.main(["--cache-dir", str(cache), "--out", str(b), "--window", "30", "--quiet", "--limit-days", "12"]) == 0
    assert DF.main([str(a), str(b)]) == 0
    out = capsys.readouterr().out
    assert "比對日期 10 日" in out and "逐位相同" in out and "只在一邊有的日期 2 個" in out
    assert DF.main([str(a), str(b), "--strict-dates"]) == 1
    capsys.readouterr()
    conn = sqlite3.connect(b)
    # 改一個必非 NULL 的欄（line_2 在頭幾日可能 NULL，NULL+1 仍 NULL、改了等於沒改）
    conn.execute("UPDATE scores SET line_states = 'nnnnnn' WHERE stock_id='1101' AND horizon='short' AND date=?", (DAYS[3],))
    conn.commit()
    conn.close()
    assert DF.main([str(a), str(b), "--dates", DAYS[3], DAYS[4]]) == 1
    out = capsys.readouterr().out
    assert "不同 1" in out and "line_states" in out and "1101" in out
    assert DF.main([str(a), str(tmp_path / "沒有.db")]) == 2

