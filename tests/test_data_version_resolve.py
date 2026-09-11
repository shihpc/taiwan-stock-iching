"""data_version 自動沿用（2026-09-11；使用者：每次回 tmux 都要重設 $DV 是壞設計）：免 token、免網路。

優先序見 scripts/backfill_hetzner.py resolve_data_version docstring。
"""
from __future__ import annotations

import datetime as dt
import sqlite3
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

from iching import config as C  # noqa: E402
from iching.store import open_stores  # noqa: E402

import backfill_hetzner as B  # noqa: E402

DV1 = "fm-20260910-01"


def _args(*argv):
    return B.build_parser().parse_args([*argv, "run"])


def _seed(cache_dir, *dvs):
    stores = open_stores(cache_dir, C.DB_FILES)
    for i, dv in enumerate(dvs):
        stores["market"].record_success("fx_usd", "raw_fx_usd", f"USD:202{i}-01-01~202{i}-12-31",
                                        [{"date": f"202{i}-01-04", "currency": "USD"}], dv, "TaiwanExchangeRate", ("date",))
    for s_ in stores.values():
        s_.close()


def test_1_empty_cache_uses_today_default_and_says_new_batch(tmp_path, capsys):
    dv = B.resolve_data_version(_args(), tmp_path)
    assert dv == C.default_data_version("01") and C.DATA_VERSION_RE.match(dv)
    out = capsys.readouterr().out
    assert "新批次" in out and dv in out


def test_2_single_dv_in_cache_reused_even_across_midnight(tmp_path, capsys, monkeypatch):
    _seed(tmp_path, DV1)
    assert B.data_versions_in_cache(tmp_path) == [DV1]
    assert B.resolve_data_version(_args(), tmp_path) == DV1
    assert "沿用 cache 內既有 data_version=" + DV1 in capsys.readouterr().out
    # 模擬跨日：台北今日變成隔天（甚至隔年）→ 仍沿用，不變成新版本
    tomorrow = dt.datetime(2027, 3, 1, 8, 0, tzinfo=C.TAIPEI)
    monkeypatch.setattr(C, "taipei_now", lambda: tomorrow)
    assert C.default_data_version("01") == "fm-20270301-01"
    assert B.resolve_data_version(_args(), tmp_path) == DV1


def test_3_explicit_different_dv_aborts_with_rm_and_zero_requests(tmp_path, monkeypatch):
    _seed(tmp_path, DV1)
    with pytest.raises(SystemExit) as ei:
        B.resolve_data_version(_args("--data-version", "fm-20260911-01"), tmp_path)
    msg = str(ei.value)
    assert DV1 in msg and "fm-20260911-01" in msg and f"rm -f {tmp_path}/*.db {tmp_path}/*.db-wal {tmp_path}/*.db-shm" in msg
    assert "不要帶" in msg and "--new-version" in msg

    class _NoFM:                       # 走 main 也一樣：在建立 FinMind client（＝任何請求）之前就中止
        def __init__(self, *a, **k):
            raise AssertionError("不該建立 FinMind client")
    monkeypatch.setattr(B, "FinMind", _NoFM)
    with pytest.raises(SystemExit):
        B.main(["--cache-dir", str(tmp_path), "--data-version", "fm-20260911-01", "run", "--dataset", "fx_usd", "--no-token"])


def test_3b_new_version_flag_allows_with_warning(tmp_path, capsys):
    _seed(tmp_path, DV1)
    dv = B.resolve_data_version(_args("--data-version", "fm-20260911-01", "--new-version"), tmp_path)
    assert dv == "fm-20260911-01"
    out = capsys.readouterr().out
    assert "⚠" in out and DV1 in out and "新批次" in out
    with pytest.raises(SystemExit):     # --new-version 不帶 --data-version → 報錯（新批次要明示版本號）
        B.resolve_data_version(_args("--new-version"), tmp_path)


def test_4_explicit_same_dv_is_fine(tmp_path, capsys):
    _seed(tmp_path, DV1)
    assert B.resolve_data_version(_args("--data-version", DV1), tmp_path) == DV1
    assert "續跑" in capsys.readouterr().out
    assert B.resolve_data_version(_args("--data-version", DV1), tmp_path / "empty") == DV1   # cache 空＋顯式 → 新批次


def test_5_multiple_dvs_abort(tmp_path):
    _seed(tmp_path, DV1, "fm-20260911-01")
    assert B.data_versions_in_cache(tmp_path) == [DV1, "fm-20260911-01"]
    with pytest.raises(SystemExit) as ei:
        B.resolve_data_version(_args(), tmp_path)
    assert DV1 in str(ei.value) and "fm-20260911-01" in str(ei.value) and f"rm -f {tmp_path}/" in str(ei.value)
    with pytest.raises(SystemExit):     # 顯式指定其中之一但沒 --new-version → 仍中止（cache 內有別的版本）
        B.resolve_data_version(_args("--data-version", DV1), tmp_path)


def test_6_empty_string_still_exits(tmp_path):
    _seed(tmp_path, DV1)
    with pytest.raises(SystemExit) as ei:
        B.resolve_data_version(_args("--data-version", ""), tmp_path)
    assert "空字串" in str(ei.value)
    with pytest.raises(SystemExit):
        B.resolve_data_version(_args("--data-version", "   "), tmp_path / "empty")


def test_7_missing_dir_or_corrupt_db_treated_as_empty(tmp_path, capsys):
    assert B.data_versions_in_cache(tmp_path / "nope") == []
    d = tmp_path / "c"
    d.mkdir()
    (d / "junk.db").write_bytes(b"this is not a sqlite file" * 100)     # 名字不撞 DB_FILES（_seed 會開它們），但仍在 *.db 掃描範圍
    (d / "notes.txt").write_text("ignored")
    assert B.data_versions_in_cache(d) == []
    c = sqlite3.connect(d / "other.db"); c.execute("CREATE TABLE x(a)"); c.commit(); c.close()   # 有效檔但無 coverage 表
    _seed(d, DV1)
    assert B.data_versions_in_cache(d) == [DV1]                       # 壞檔／無表的檔被略過，只取有效的
    assert B.resolve_data_version(_args(), tmp_path / "nope") == C.default_data_version("01")
    assert "新批次" in capsys.readouterr().out


def test_8_plan_and_report_resolve_same_dv_without_args(tmp_path, capsys):
    _seed(tmp_path, DV1)
    assert B.main(["--cache-dir", str(tmp_path), "plan", "--dataset", "fx_usd"]) == 0
    assert "沿用 cache 內既有 data_version=" + DV1 in capsys.readouterr().out
    assert B.main(["--cache-dir", str(tmp_path), "report"]) == 0
    out_rep = capsys.readouterr().out
    assert out_rep.splitlines()[0].startswith("沿用 cache 內既有 data_version=" + DV1)
    assert "DB 內 data_version 數＝1" in out_rep
