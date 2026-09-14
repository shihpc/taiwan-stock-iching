"""D-3 parity 儀式（`scripts/parity_check.py`，`docs/P2-DAILY-PLAN.md` §7.6.1 第 2 點）：合成 DB 世界上的五個案例。

世界的建法沿用 `tests/test_daily_core.py::world`（`build_full` → 重播到 K 存快照 → `export_seed`），剩餘日子改走
`tests/test_daily_run.py` 的每日班路徑（假 FinMind／假官方端點 → `daily_run.main`），所以 repo 端的 `data/scores/*.json`
與 `runs/collect/*.json.gz` 都是每日班真正產出的檔。案例：①原封不動 rc 0、四類全 0；②改一格分數 rc 1 且指到該股（④）；
③把 `us` 列搬到隔日包（切分點不同）仍 rc 0；④對某檔刪掉入池前的列（新入池）→ 該檔歸①、rc 0；⑤改 `index` 一格 rc 3。
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

import export_seed as ES  # noqa: E402
import parity_check as PC  # noqa: E402
import replay_scores as RP  # noqa: E402
import scan_features as SF  # noqa: E402
from iching import bundle_io as B  # noqa: E402
from iching import calendar as CAL  # noqa: E402
from iching import daily_core as DC  # noqa: E402
from iching import daily_pipeline as DP  # noqa: E402
from iching import replay_io as RIO  # noqa: E402
from synth_db import DAYS, DV, MALFORMED_I, build_full  # noqa: E402
from test_daily_run import K, WINDOW, _run  # noqa: E402   # 每日班路徑：假端點＋ daily_run.main 的跑法

ZERO = {PC.CLASS_NEW: 0, PC.CLASS_SHORT: 0, PC.CLASS_BUNDLE: 0, PC.CLASS_UNEXPLAINED: 0}


@pytest.fixture(scope="module")
def world(tmp_path_factory) -> dict:
    base = tmp_path_factory.mktemp("parity")
    cache, repo = base / "cache", base / "repo"
    build_full(cache)
    assert SF.main(["--cache-dir", str(cache), "--quiet", "--allow-short-warmup", "--warmup-days", "0"]) == 0
    assert RP.main(["--cache-dir", str(cache), "--window", str(WINDOW), "--to", DAYS[K], "--quiet"]) == 0
    state_k = base / "state_k.json"
    shutil.copy(cache / "scores.db.state.json", state_k)
    assert RP.main(["--cache-dir", str(cache), "--window", str(WINDOW), "--resume", "--quiet"]) == 0
    src = RIO.ReplaySource(cache, DV, window=WINDOW)
    dates = src.trading_dates()
    CAL.write_calendar_json(repo / DC.CALENDAR_TPE_FILE, CAL.calendar_payload("tpe", dates, DV))
    us_dates = sorted({d for d, *_ in src.read_day(DAYS[K]).us})
    CAL.write_calendar_json(repo / DP.CALENDAR_US_FILE, CAL.calendar_payload("us", us_dates, DV))
    src.close()
    assert ES.main(["--cache-dir", str(cache), "--out", str(repo), "--window", str(WINDOW), "--state", str(state_k)]) == 0
    # 每日班一次補完 K+1～末日（原料包與分數檔皆由每日班路徑產出）
    assert _run(repo, cache, DAYS[-1], extra=["--max-days", str(len(DAYS))]) == 0
    assert [d for d, _ in B.list_bundles(repo)] == DAYS
    assert sorted(p.stem for p in (repo / DC.SCORES_DIR).glob("*.json")) == DAYS[K + 1:]
    return {"cache": cache, "repo": repo}


def _fresh(world: dict, tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    shutil.copytree(world["repo"], repo)
    return repo


def _check(world: dict, repo: Path, **kw) -> tuple[PC.ParityResult, list[str]]:
    logs: list[str] = []
    res = PC.run(world["cache"], repo, log=logs.append, **kw)
    return res, logs


def _mutate_score(repo: Path, T: str, sid: str) -> None:
    """直接改 JSON 一列的分數欄（`base_score`），以 `daily_core.dumps` 同一支序列化寫回。"""
    p = DC.scores_path(repo, T)
    js = json.loads(p.read_text(encoding="utf-8"))
    row = next(r for r in js["rows"] if r["stock_id"] == sid)
    row["base_score"] = 99.0 if row.get("base_score") != 99.0 else 98.0
    DC.write_json(p, js)


def test_untouched_is_bitwise_identical(world):
    res, logs = _check(world, world["repo"])
    assert res.errors == [] and res.rc == 0
    assert res.dates == DAYS[K + 1:] and res.data_version == DV and res.window == WINDOW
    assert res.counts() == ZERO and res.market_layer_days == []
    for d in res.days.values():
        assert not d.key_diffs and not d.stock_diffs and not d.diag_diffs and not d.ref_missing
        assert d.n_diff == 0 and d.n_common > 0 and d.classes == {}
    assert res.dated_diffs == {"us": [], "fx": []} and res.dated_range["us"] is not None
    assert any("原料包 相同" in line and "不同 0" in line for line in logs)
    # CLI 與 --from/--to 子區間
    assert PC.main(["--cache-dir", str(world["cache"]), "--repo", str(world["repo"]), "--quiet"]) == 0
    sub, _ = _check(world, world["repo"], start=DAYS[K + 3], end=DAYS[K + 5])
    assert sub.rc == 0 and sub.dates == DAYS[K + 3:K + 6]


def test_one_score_cell_changed_is_unexplained(world, tmp_path):
    repo = _fresh(world, tmp_path)
    T, sid = DAYS[-1], "1101"
    _mutate_score(repo, T, sid)
    res, logs = _check(world, repo)
    assert res.rc == 1 and res.errors == []
    assert res.counts() == {**ZERO, PC.CLASS_UNEXPLAINED: 1}
    assert res.stocks_of(PC.CLASS_UNEXPLAINED) == [(T, sid)]
    day = res.days[T]
    assert day.n_diff == 1 and day.diff_sids == {sid: 1} and not day.market_layer and not day.stock_diffs
    assert any(f"④ {sid}" in line for line in logs) and any(f"{T}: ('twse', " in m and sid in m for m in day.msgs)
    assert all(res.days[d].n_diff == 0 for d in DAYS[K + 1:-1])
    assert PC.main(["--cache-dir", str(world["cache"]), "--repo", str(repo), "--quiet"]) == 1


def test_us_rows_moved_to_next_bundle_still_identical(world, tmp_path):
    repo = _fresh(world, tmp_path)
    d0, d1 = DAYS[K + 4], DAYS[K + 5]
    b0, b1 = B.read_bundle(B.bundle_path(repo, d0)), B.read_bundle(B.bundle_path(repo, d1))
    assert b0.us and b0.fx
    b1.us, b0.us = b0.us[-1:] + b1.us, b0.us[:-1]                   # 美股最後一列搬到隔日包
    b1.fx, b0.fx = b0.fx[-1:] + b1.fx, b0.fx[:-1]                   # 匯率同法
    before = B.bundle_path(repo, d0).read_bytes()
    B.write_bundle(repo, b0)
    B.write_bundle(repo, b1)
    assert B.bundle_path(repo, d0).read_bytes() != before          # 位元組確實不同（切分點變了）
    res, _ = _check(world, repo)
    assert res.rc == 0 and res.errors == [] and res.counts() == ZERO
    assert res.dated_diffs == {"us": [], "fx": []}
    assert not res.days[d0].key_diffs and not res.days[d1].key_diffs
    # 對照：真的改一個美股收盤值 → 聯集有差、其後台北日標市場層、rc 3
    b1 = B.read_bundle(B.bundle_path(repo, d1))
    x = b1.us[0]
    b1.us[0] = (x[0], x[1] + 1.0, *x[2:])
    B.write_bundle(repo, b1)
    res2, _ = _check(world, repo)
    assert res2.rc == 3 and len(res2.dated_diffs["us"]) == 1 and res2.dated_diffs["fx"] == []
    assert x[0] in res2.dated_diffs["us"][0]
    assert res2.market_layer_days == [d for d in DAYS[K + 1:] if d > x[0]] and res2.counts() == ZERO


def test_new_entrant_is_classified_first_class(world, tmp_path):
    repo = _fresh(world, tmp_path)
    sid, E, T = "2330", DAYS[-3], DAYS[-1]
    for d, p in B.list_bundles(repo):                                 # repo 端刪掉入池前的列（模擬 D-1 語意的新入池）
        if d < E:
            b = B.read_bundle(p)
            assert b.stocks.pop(sid, None) is not None
            B.write_bundle(repo, b)
    _mutate_score(repo, T, sid)                                       # 分數是全歷史算的，不會自己不同；製造一筆差異看歸類
    res, logs = _check(world, repo)
    assert res.rc == 0 and res.errors == []
    assert res.counts() == {**ZERO, PC.CLASS_NEW: 1} and res.stocks_of(PC.CLASS_NEW) == [(T, sid)]
    assert "首見" in res.days[T].reasons[sid] and E in res.days[T].reasons[sid]
    assert any(f"① {sid}" in line for line in logs)
    # 原料包比對照實：E 之前區間內每日 stocks[2330] 只在參考（不影響 rc）
    for d in DAYS[K + 1:]:
        assert (sid in res.days[d].stock_diffs) == (d < E), d
        assert not res.days[d].key_diffs
    assert res.days[DAYS[K + 1]].stock_diffs[sid].endswith("只在參考")


def test_index_cell_changed_marks_market_layer(world, tmp_path):
    repo = _fresh(world, tmp_path)
    T = DAYS[-1]
    b = B.read_bundle(B.bundle_path(repo, T))
    b.index["twse"]["close"] = float(b.index["twse"]["close"]) + 1.0
    B.write_bundle(repo, b)
    res, logs = _check(world, repo)
    assert res.rc == 3 and res.errors == []
    day = res.days[T]
    assert day.market_layer and day.market_layer_since == T and "index" in day.key_diffs and "twse" in day.key_diffs["index"]
    assert res.market_layer_days == [T] and res.counts() == ZERO and day.classes == {}
    assert any("市場層原料不同" in line for line in logs)
    assert PC.main(["--cache-dir", str(world["cache"]), "--repo", str(repo), "--quiet"]) == 3
    # 傳播：市場層差異在 T−2，T 的分數差異不歸類（ring 跨日）、rc 仍 3
    repo2 = _fresh(world, tmp_path / "p2")
    b = B.read_bundle(B.bundle_path(repo2, DAYS[-3]))
    b.vix = (b.vix or 0.0) + 1.0
    B.write_bundle(repo2, b)
    _mutate_score(repo2, T, "1101")
    res2, _ = _check(world, repo2)
    assert res2.rc == 3 and res2.market_layer_days == DAYS[-3:] and res2.unclassified_rows == 1 and res2.counts() == ZERO
    assert res2.days[T].market_layer_since == DAYS[-3] and "vix" in res2.days[DAYS[-3]].key_diffs


def test_short_history_is_classified_second_class(world, tmp_path):
    """② 正向：6488 於 DAYS[MALFORMED_I] 有畸形列（close=0，`is_traded_row` 為假），T=DAYS[K+1] 的近 WINDOW 份包只有 29 個有效收盤。"""
    repo = _fresh(world, tmp_path)
    T, sid = DAYS[K + 1], "6488"
    assert K + 1 - WINDOW < MALFORMED_I <= K + 1                     # 畸形列落在 T 的近 WINDOW 份原料包內
    _mutate_score(repo, T, sid)
    res, logs = _check(world, repo)
    assert res.rc == 0 and res.errors == []
    assert res.counts() == {**ZERO, PC.CLASS_SHORT: 1} and res.stocks_of(PC.CLASS_SHORT) == [(T, sid)]
    assert f"有效收盤 {WINDOW - 1} < {WINDOW}" in res.days[T].reasons[sid]
    assert any(f"② {sid}" in line for line in logs) and not res.days[T].stock_diffs


def test_stock_bundle_revision_is_classified_third_class(world, tmp_path):
    """③ 正向：同一日 repo 包的 `stocks[sid].close` 與該檔一格分數都改（模擬上游事後修訂）。"""
    repo = _fresh(world, tmp_path)
    d, sid = DAYS[K + 3], "1101"
    b = B.read_bundle(B.bundle_path(repo, d))
    b.stocks[sid]["close"] = float(b.stocks[sid]["close"]) + 1.0
    B.write_bundle(repo, b)
    _mutate_score(repo, d, sid)
    res, logs = _check(world, repo)
    assert res.rc == 0 and res.errors == []
    assert res.counts() == {**ZERO, PC.CLASS_BUNDLE: 1} and res.stocks_of(PC.CLASS_BUNDLE) == [(d, sid)]
    assert res.days[d].stock_diffs[sid].startswith(f"stocks {sid} 欄 close") and d in res.days[d].reasons[sid]
    assert not res.days[d].key_diffs and any(f"③ {sid}" in line for line in logs)


def test_diag_only_difference_is_unexplained(world, tmp_path):
    """列全同、只有 JSON `diag` 的 9 欄之一不同 → 標示為 diag 差異、④、rc 1；`rank_pool_size` 不在 9 欄內、改它不算。"""
    repo = _fresh(world, tmp_path)
    T = DAYS[-2]
    p = DC.scores_path(repo, T)
    js = json.loads(p.read_text(encoding="utf-8"))
    js["diag"]["n_stock_rows"] = int(js["diag"]["n_stock_rows"]) + 1
    DC.write_json(p, js)
    res, logs = _check(world, repo)
    assert res.rc == 1 and res.errors == []
    day = res.days[T]
    assert day.n_diff == 0 and set(day.diag_diffs) == {"n_stock_rows"}
    assert day.diag_diffs["n_stock_rows"][1] == day.diag_diffs["n_stock_rows"][0] + 1
    assert day.classes == {"diag": PC.CLASS_UNEXPLAINED} and res.counts() == {**ZERO, PC.CLASS_UNEXPLAINED: 1}
    assert any("diag 不同 n_stock_rows" in line for line in logs)
    js["diag"]["n_stock_rows"] -= 1
    js["diag"]["rank_pool_size"] = 999
    DC.write_json(p, js)
    assert _check(world, repo)[0].rc == 0


def test_repo_bundle_without_scores_still_feeds_dated_union(world, tmp_path):
    """有包但無分數檔的日子：其 us／fx 增量仍要進 repo 側聯集（讀區間內全部包），否則聯集缺日、誤報 rc 3。"""
    repo = _fresh(world, tmp_path)
    gone = DAYS[K + 2]
    DC.scores_path(repo, gone).unlink()
    assert B.read_bundle(B.bundle_path(repo, gone)).us              # 該日包確實帶美股增量
    res, _ = _check(world, repo)
    assert res.rc == 0 and res.errors == [] and res.dates == [d for d in DAYS[K + 1:] if d != gone]
    assert res.dated_diffs == {"us": [], "fx": []} and res.counts() == ZERO


def test_version_mismatch_and_missing_are_rc2(world, tmp_path):
    repo = _fresh(world, tmp_path)
    assert PC.run(world["cache"], repo, data_version="no-such-dv", log=lambda s: None).rc == 2
    assert PC.run(world["cache"], repo, start="2031-01-01", log=lambda s: None).rc == 2
    p = DC.scores_path(repo, DAYS[-1])
    js = json.loads(p.read_text(encoding="utf-8"))
    js["params_sha"] = "deadbeef0000"
    DC.write_json(p, js)
    res = PC.run(world["cache"], repo, log=lambda s: None)
    assert res.rc == 2 and any("params_sha" in e for e in res.errors)
    assert PC.run(tmp_path / "nowhere", repo, log=lambda s: None).rc == 2
