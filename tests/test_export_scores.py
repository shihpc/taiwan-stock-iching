"""`scripts/export_scores.py`：從 `scores.db` 匯出的 `data/scores/<T>.json` 必須與每日班 `daily_core.run_offline` 寫的檔
**位元組相同**（去掉 `diag.elapsed_ms` 與每日班獨有的 `diag.rank_pool_size` 之後；兩欄的理由見 `export_scores.py` 檔頭表）。

合成世界（同 `test_daily_core.py`）：參考路徑 `scan_features`＋`replay_scores` 全量 → `scores.db`；第 K 日 `export_seed` 匯種子 →
之後逐日 `run_offline` 寫分數檔（每日班路徑）；再用 `export_scores.py` 從同一份 `scores.db` 匯到另一目錄逐日比對。
"""
from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import export_scores as EX  # noqa: E402
import export_seed as ES  # noqa: E402
import replay_scores as RP  # noqa: E402
import scan_features as SF  # noqa: E402
from iching import bundle_io as B  # noqa: E402
from iching import calendar as CAL  # noqa: E402
from iching import daily_core as DC  # noqa: E402
from iching import replay_io as RIO  # noqa: E402
from iching.scores_io import ScoreStore  # noqa: E402
from synth_db import DAYS, DV, build_full  # noqa: E402

WINDOW = 30
K = 60
N_DAYS = 4                                     # 每日班跑 DAYS[K+1..K+4]（≥3 個 T）
TARGET = DAYS[K + 1: K + 1 + N_DAYS]


@pytest.fixture(scope="module")
def world(tmp_path_factory) -> dict:
    base = tmp_path_factory.mktemp("exportscores")
    cache, repo = base / "cache", base / "repo"
    build_full(cache)
    assert SF.main(["--cache-dir", str(cache), "--quiet", "--allow-short-warmup", "--warmup-days", "0"]) == 0
    assert RP.main(["--cache-dir", str(cache), "--window", str(WINDOW), "--to", DAYS[K], "--quiet"]) == 0
    state_k = base / "state_k.json"
    shutil.copy(cache / "scores.db.state.json", state_k)
    assert RP.main(["--cache-dir", str(cache), "--window", str(WINDOW), "--resume", "--quiet"]) == 0
    src = RIO.ReplaySource(cache, DV, window=WINDOW)
    CAL.write_calendar_json(repo / DC.CALENDAR_TPE_FILE, CAL.calendar_payload("tpe", src.trading_dates(), DV))
    # 每日班路徑：種子＋原料包 → run_offline 逐日寫 data/scores/<T>.json
    assert ES.main(["--cache-dir", str(cache), "--out", str(repo), "--window", str(WINDOW), "--state", str(state_k)]) == 0
    for d in DAYS[: K + 1 + N_DAYS]:
        b = src.read_day(d)
        if d > DAYS[K]:
            B.write_bundle(repo, b)
    src.close()
    summary = DC.run_offline(repo, window=WINDOW)
    assert [x["date"] for x in summary["days"]] == TARGET
    return {"cache": cache, "repo": repo, "params_sha": summary["params_sha"]}


def _read(path: Path) -> tuple[bytes, dict]:
    raw = path.read_bytes()
    js = json.loads(raw.decode("utf-8"))
    assert DC.dumps(js).encode("utf-8") == raw, path          # 檔案就是 DC.dumps 的正規形；重 dump 比對＝位元組比對
    return raw, js


def test_export_equals_run_offline_bytewise(world, tmp_path):
    repo, cache = world["repo"], world["cache"]
    out = tmp_path / "out"
    assert EX.main(["--cache-dir", str(cache), "--out", str(out), "--from", TARGET[0], "--to", TARGET[-1]]) == 0
    assert sorted(p.name for p in (out / DC.SCORES_DIR).iterdir()) == [f"{T}.json" for T in TARGET]
    for T in TARGET:
        raw_daily, daily = _read(DC.scores_path(repo, T))
        raw_exp, exp = _read(DC.scores_path(out, T))
        # 每日班 diag 有 rank_pool_size／elapsed_ms；匯出檔省略前者、後者是重播班耗時 → 只有這兩欄允許不同
        assert "rank_pool_size" in daily["diag"] and "rank_pool_size" not in exp["diag"]
        assert set(daily["diag"]) - set(exp["diag"]) == {"rank_pool_size"}
        assert set(exp["diag"]) <= set(daily["diag"])
        a, b = EX.strip_volatile(daily), EX.strip_volatile(exp)
        assert DC.dumps(a).encode("utf-8") == DC.dumps(b).encode("utf-8"), T
        assert raw_daily != raw_exp or daily["diag"]["elapsed_ms"] == exp["diag"]["elapsed_ms"]
        # 內容有意義：市場列（帶 flags／in_rank_pool=None）與個股列（in_rank_pool 0/1）、旗標／狀態欄都在
        rows = exp["rows"]
        mkt = [r for r in rows if r["scope"] == "market_index"]
        stk = [r for r in rows if r["scope"] == "stock"]
        assert mkt and stk and len(rows) == exp["diag"]["n_market_rows"] + exp["diag"]["n_stock_rows"]
        assert all(isinstance(r["flags"], str) and r["in_rank_pool"] is None for r in mkt)
        assert all(r["flags"] is None and r["in_rank_pool"] in (0, 1) for r in stk) and any(r["in_rank_pool"] == 1 for r in stk)
        assert all(len(r["line_states"]) == 6 and r["streaks"].count(",") == 5 for r in rows)
        assert exp["params_sha"] == world["params_sha"] and exp["tpe_date"] == T and exp["data_version"] == DV
        assert exp["text_version"] == exp["diag"]["text_version"] == daily["text_version"]
        assert [(r["market"], r["stock_id"], r["horizon"], r["model_version"]) for r in rows] == \
               [(r["market"], r["stock_id"], r["horizon"], r["model_version"]) for r in daily["rows"]]


def test_params_sha_and_text_version_come_from_db(world):
    cache = world["cache"]
    ref = ScoreStore(cache / "scores.db", readonly=True)
    try:
        assert ref.params_sha_of(DV) == world["params_sha"]
        p = EX.export_payload(ref, DV, TARGET[0])
        assert p["params_sha"] == ref.params_sha_of(DV)
        assert p["diag"]["text_version"] == ref.params_of(DV)["text_version"]
        assert p["diag"]["elapsed_ms"] == ref.day_diag(DV, TARGET[0])["elapsed_ms"]
        assert p["diag"]["index_missing"] == []
    finally:
        ref.close()


def test_existing_file_not_overwritten_without_force(world, tmp_path):
    cache = world["cache"]
    out = tmp_path / "out"
    T = TARGET[1]
    assert EX.main(["--cache-dir", str(cache), "--out", str(out), "--from", T, "--to", T]) == 0
    path = DC.scores_path(out, T)
    good = path.read_bytes()
    # 再跑：位元組相同 → 略過、rc 0、mtime 不動
    m0 = path.stat().st_mtime_ns
    assert EX.main(["--cache-dir", str(cache), "--out", str(out), "--from", T, "--to", T]) == 0
    assert path.stat().st_mtime_ns == m0
    # 只差 diag 揮發欄 → 視為相同、不覆蓋、rc 0
    js = json.loads(good.decode("utf-8"))
    js["diag"]["elapsed_ms"], js["diag"]["rank_pool_size"] = 12345.678, 999
    DC.write_json(path, js)
    modulo = path.read_bytes()
    assert EX.main(["--cache-dir", str(cache), "--out", str(out), "--from", T, "--to", T]) == 0
    assert path.read_bytes() == modulo
    # 分數列不同 → 不覆蓋、rc 1；--force 才覆蓋回匯出內容
    js["rows"][0]["base_score"] = -1.0
    DC.write_json(path, js)
    tampered = path.read_bytes()
    assert EX.main(["--cache-dir", str(cache), "--out", str(out), "--from", T, "--to", T]) == 1
    assert path.read_bytes() == tampered
    assert EX.main(["--cache-dir", str(cache), "--out", str(out), "--from", T, "--to", T, "--force"]) == 0
    assert path.read_bytes() == good


def test_bad_range_and_missing_db_abort(world, tmp_path):
    cache = world["cache"]
    assert EX.main(["--cache-dir", str(cache), "--out", str(tmp_path / "a"), "--from", TARGET[1], "--to", TARGET[0]]) == 2
    assert EX.main(["--cache-dir", str(cache), "--out", str(tmp_path / "b"), "--from", "2031-01-01", "--to", "2031-12-31"]) == 2
    assert EX.main(["--cache-dir", str(tmp_path / "nodb"), "--out", str(tmp_path / "c")]) == 2
    assert EX.main(["--cache-dir", str(cache), "--out", str(tmp_path / "d"), "--data-version", "nope"]) == 2
    assert not (tmp_path / "a").exists() and not (tmp_path / "b").exists() and not (tmp_path / "d").exists()
