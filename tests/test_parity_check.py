"""D-3 parity 儀式（`scripts/parity_check.py`，`docs/P2-DAILY-PLAN.md` §7.6.1 第 2 點）：合成 DB 世界上的五個案例。

世界的建法沿用 `tests/test_daily_core.py::world`（`build_full` → 重播到 K 存快照 → `export_seed`），剩餘日子改走
`tests/test_daily_run.py` 的每日班路徑（假 FinMind／假官方端點 → `daily_run.main`），所以 repo 端的 `data/scores/*.json`
與 `runs/collect/*.json.gz` 都是每日班真正產出的檔。案例：①原封不動 rc 0、四類全 0；②改一格分數 rc 1 且指到該股（④）；
③把 `us` 列搬到隔日包（切分點不同）仍 rc 0；④對某檔刪掉入池前的列（新入池）→ 該檔歸①、rc 0；⑤改 `index` 一格 rc 3。
2026-09-15 補（§7.6.3 原料包以外的輸入）：(a) 改 repo `factors.json` 一個係數 → 該檔歸⑤、同日其餘差異列「⑤⑥連帶」、rc 0，
報告列出 ex_date；未來 ex_date／d5 > T 不算；(b) 改 `fundamentals.json` 某檔某期數值 → ⑥；改月營收另帶產業中位數差異；
(c) 原封不動時 ⑤⑥ 為 0；(d) `--dump` 檔可讀回、列數＝差異數。
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
import synth_db  # noqa: E402
from synth_db import DAYS, DV, MALFORMED_I, build_full  # noqa: E402
from test_daily_run import K, WINDOW, _run  # noqa: E402   # 每日班路徑：假端點＋ daily_run.main 的跑法

ZERO = {c: 0 for c in PC.CLASSES}                                   # ①②③④⑤⑥ 全 0


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


def _mutate_factor(repo: Path, sid: str, ex_date: str, after: float | None = None, add: tuple | None = None) -> None:
    """改 repo `data/factors.json`：`after` 給定＝改該 (sid, ex_date) 列的 after_price；`add` 給定＝追加一列。"""
    p = repo / DC.FACTORS_FILE
    js = json.loads(p.read_text(encoding="utf-8"))
    if after is not None:
        row = next(r for r in js["rows"] if r[0] == sid and r[1] == ex_date)
        row[3] = after
    if add is not None:
        js["rows"].append(list(add))
    DC.write_json(p, js)


def _mutate_fund(repo: Path, sid: str, *, quarter: tuple[str, str, float] | None = None, month: tuple[int, int, float] | None = None) -> None:
    """改 repo `data/fundamentals.json`：`quarter=(期別, type, 新值)` 改季報一列；`month=(年, 月, 新值)` 改月營收一列。"""
    p = repo / DC.FUND_FILE
    js = json.loads(p.read_text(encoding="utf-8"))
    if quarter is not None:
        row = next(r for r in js["quarters"][sid] if r[0] == quarter[0] and r[1] == quarter[1])
        row[2] = quarter[2]
    if month is not None:
        row = next(r for r in js["monthly"][sid] if int(r[0]) == month[0] and int(r[1]) == month[1])
        row[2] = month[2]
    DC.write_json(p, js)


def test_untouched_is_bitwise_identical(world):
    res, logs = _check(world, world["repo"])
    assert res.errors == [] and res.rc == 0
    assert res.dates == DAYS[K + 1:] and res.data_version == DV and res.window == WINDOW
    assert res.counts() == ZERO and res.market_layer_days == []
    assert res.factor_diffs == {} and res.factor_uncounted == {} and res.fund_diff_days == [] and res.spill56_days == []
    assert res.factor_note and res.fund_note and "1101" in res.industry_of                 # ⑤⑥ 真的比了（不是被跳過）
    assert any("⑤ 0 檔" in line for line in logs) and any("⑥ 0 (日,檔)" in line for line in logs)
    for d in res.days.values():
        assert not d.key_diffs and not d.stock_diffs and not d.diag_diffs and not d.ref_missing
        assert d.n_diff == 0 and d.n_common > 0 and d.classes == {} and d.spill56 == {} and d.fund_diffs == {} and d.fund_ind_diffs == {}
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


def test_factor_coefficient_changed_is_fifth_class(world, tmp_path):
    """(a) ⑤：repo `factors.json` 把 1101 於 DAYS[EX_I] 的 after_price 80→70（累積係數 1.25→1.4286），該檔分數差異歸⑤、
    同日其餘差異（2330 個股列、大盤列）列「⑤⑥連帶」、rc 0，報告印出 ex_date。"""
    repo = _fresh(world, tmp_path)
    T, sid, ex = DAYS[-1], "1101", DAYS[synth_db.EX_I]
    _mutate_factor(repo, sid, ex, after=70.0)
    _, factors, _ = DC.load_factors_file(repo / DC.FACTORS_FILE)
    assert factors[sid] == ([ex], [100.0 / 70.0])
    for s in (sid, "2330", PC.MARKET_STOCK_ID):
        _mutate_score(repo, T, s)
    res, logs = _check(world, repo)
    assert res.rc == 0 and res.errors == []
    assert res.counts() == {**ZERO, PC.CLASS_FACTOR: 1} and res.stocks_of(PC.CLASS_FACTOR) == [(T, sid)]
    fd = res.factor_diffs[sid]
    assert set(res.factor_diffs) == {sid} and fd.date == ex and fd.what == "累積係數不同" and (fd.a, fd.b) == (1.25, 100.0 / 70.0)
    day = res.days[T]
    assert day.factor_sids(res) == {sid} and set(day.spill56) == {"2330", PC.MARKET_STOCK_ID} and day.fund_diffs == {}
    assert ex in day.reasons[sid] and "⑤1 檔" in day.spill56["2330"]
    assert any(f"⑤ {sid}" in line and f"ex_date {ex}" in line for line in logs)
    assert any(f"factors {sid} ex_date {ex}" in line for line in logs)          # 總結段也列
    assert any("⑤⑥連帶2" in line for line in logs) and any("⑤ 1 檔" in line for line in logs)
    assert PC.main(["--cache-dir", str(world["cache"]), "--repo", str(repo), "--quiet"]) == 0
    # d5 ≤ T 才算：只在 repo 追加 2330 於 DAYS[-2] 的事件 → DAYS[-3] 的 2330 差異仍是真④（rc 1）、DAYS[-1] 的歸⑤
    repo2 = _fresh(world, tmp_path / "p2")
    _mutate_factor(repo2, "2330", "", add=("2330", DAYS[-2], 50.0, 40.0))
    _mutate_score(repo2, DAYS[-3], "2330")
    _mutate_score(repo2, DAYS[-1], "2330")
    res2, _ = _check(world, repo2)
    assert res2.rc == 1 and res2.counts() == {**ZERO, PC.CLASS_UNEXPLAINED: 1, PC.CLASS_FACTOR: 1}
    assert res2.stocks_of(PC.CLASS_UNEXPLAINED) == [(DAYS[-3], "2330")] and res2.stocks_of(PC.CLASS_FACTOR) == [(DAYS[-1], "2330")]
    assert res2.factor_diffs["2330"].what == "只在 repo" and res2.factor_diffs["2330"].date == DAYS[-2]
    # 未來 ex_date（> 區間迄日）不算：只列「不計」、該檔差異仍④、rc 1
    repo3 = _fresh(world, tmp_path / "p3")
    _mutate_factor(repo3, sid, "", add=(sid, "2031-01-01", 100.0, 90.0))
    _mutate_score(repo3, T, sid)
    res3, logs3 = _check(world, repo3)
    assert res3.rc == 1 and res3.factor_diffs == {} and set(res3.factor_uncounted) == {sid}
    assert res3.factor_uncounted[sid].date == "2031-01-01" and res3.counts() == {**ZERO, PC.CLASS_UNEXPLAINED: 1}
    assert any("（不計）" in line and "2031-01-01" in line for line in logs3)


def test_fundamentals_changed_is_sixth_class(world, tmp_path):
    """(b) ⑥：改 1101 季報 2019-09-30 的 GrossProfit → 區間內每個 T 的 as-of 9 鍵 dict 不同（T ≥ 2020-04-01 時 P=2019-12-31、
    它是 P−1 → `gross_margin_prev_q`），該檔差異歸⑥、同日其餘差異列連帶、rc 0；改月營收另帶產業中位數差異、同產業他檔的連帶理由註明。"""
    repo = _fresh(world, tmp_path)
    T, sid = DAYS[-1], "1101"
    _mutate_fund(repo, sid, quarter=("2019-09-30", "GrossProfit", 99.0))
    for s in (sid, "2330"):
        _mutate_score(repo, T, s)
    res, logs = _check(world, repo)
    assert res.rc == 0 and res.errors == []
    assert res.counts() == {**ZERO, PC.CLASS_FUND: 1} and res.stocks_of(PC.CLASS_FUND) == [(T, sid)]
    assert res.fund_diff_days == DAYS[K + 1:] and res.factor_diffs == {}
    for d in DAYS[K + 1:]:
        assert set(res.days[d].fund_diffs) == {sid} and "gross_margin_prev_q" in res.days[d].fund_diffs[sid] and not res.days[d].fund_ind_diffs
    day = res.days[T]
    assert set(day.spill56) == {"2330"} and "⑥1 檔" in day.spill56["2330"] and "水泥工業" not in day.spill56["2330"]
    assert any(f"⑥ {sid}" in line and "gross_margin_prev_q" in line for line in logs)
    assert any("基本面 as-of 不同 1 檔" in line for line in logs) and any("⑥ 19 (日,檔)／1 檔／19 日" in line for line in logs)
    # 月營收：1101 是水泥工業唯一有營收的檔 → 產業中位數跟著不同；同產業 1102 的分數差異列連帶並註明產業
    repo2 = _fresh(world, tmp_path / "p2")
    js = json.loads((repo2 / DC.FUND_FILE).read_text(encoding="utf-8"))
    y, m, v = next(r for r in js["monthly"][sid] if (int(r[0]), int(r[1])) == (2020, 2))   # 2 月營收 03-10 可得，區間內每個 T 都看得到
    _mutate_fund(repo2, sid, month=(int(y), int(m), float(v) * 2))
    for s in (sid, "1102"):
        _mutate_score(repo2, T, s)
    res2, _ = _check(world, repo2)
    assert res2.rc == 0 and res2.counts() == {**ZERO, PC.CLASS_FUND: 1}
    d2 = res2.days[T]
    assert "monthly_revenue" in d2.fund_diffs[sid] and set(d2.fund_ind_diffs) == {"水泥工業"}
    assert set(d2.spill56) == {"1102"} and "所屬產業 水泥工業" in d2.spill56["1102"]


def test_dump_roundtrip(world, tmp_path):
    """(d) `--dump`：JSON Lines（.gz 與純文字）可讀回、列數＝差異數；分數列每個不同欄一列、⑤／⑥ 檔級差異各一列、class 與報告同字。"""
    repo = _fresh(world, tmp_path)
    T, sid, ex = DAYS[-1], "1101", DAYS[synth_db.EX_I]
    _mutate_factor(repo, sid, ex, after=70.0)
    _mutate_factor(repo, "2330", "", add=("2330", "2031-01-01", 50.0, 40.0))
    _mutate_fund(repo, "2330", quarter=None)                          # 2330 無季報：不動（只驗 helper 不炸）
    for s in (sid, "2330"):
        _mutate_score(repo, T, s)
    dump = tmp_path / "out" / "diff.jsonl.gz"
    res, _ = _check(world, repo, dump=dump)
    assert res.rc == 0 and res.dump_path == dump and dump.exists()
    recs = PC.read_dump(dump)
    assert len(recs) == res.dump_count == 2 + 2                       # factor 2 列（⑤1101＋⑤未計 2330）＋ score 2 列（各只差 base_score）
    kinds = {r["kind"] for r in recs}
    assert kinds == {"factor", "score"} and all(r["kind"] in PC.DUMP_KINDS for r in recs)
    fac = {r["stock_id"]: r for r in recs if r["kind"] == "factor"}
    assert fac[sid]["date"] == ex and fac[sid]["class"] == "⑤" and (fac[sid]["a"], fac[sid]["b"]) == (1.25, 100.0 / 70.0)
    assert fac["2330"]["date"] == "2031-01-01" and fac["2330"]["class"] == PC.FACTOR_UNCOUNTED and fac["2330"]["a"] is None
    sc = {r["stock_id"]: r for r in recs if r["kind"] == "score"}
    assert sc[sid] == {"kind": "score", "date": T, "market": "twse", "horizon": sc[sid]["horizon"], "stock_id": sid, "col": "base_score",
                       "a": sc[sid]["a"], "b": sc[sid]["b"], "class": "⑤"} and sc[sid]["a"] != sc[sid]["b"]
    assert sc["2330"]["class"] == PC.SPILL56_MARK
    # 純文字檔＋CLI；差異數與報告一致（④ 一格 → 1 列）
    repo2 = _fresh(world, tmp_path / "p2")
    _mutate_score(repo2, T, sid)
    plain = tmp_path / "plain.jsonl"
    assert PC.main(["--cache-dir", str(world["cache"]), "--repo", str(repo2), "--quiet", "--dump", str(plain)]) == 1
    recs2 = PC.read_dump(plain)
    assert len(recs2) == 1 and recs2[0]["class"] == "④" and recs2[0]["col"] == "base_score" and recs2[0]["stock_id"] == sid
    # 原封不動 → 0 列（檔仍寫出）
    empty = tmp_path / "empty.jsonl"
    res3, _ = _check(world, world["repo"], dump=empty)
    assert res3.dump_count == 0 and PC.read_dump(empty) == []
