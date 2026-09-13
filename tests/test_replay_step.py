"""第 13 項 13a-2：`iching.replay_step.step(T)` ＋ `iching.scores_io.ScoreStore`。

對應 `docs/P2-REPLAY-PLAN.md` §7（§B3.3）：決定性（全量兩次逐位相同）、**單步 parity**（`CrossDayState(T−1)` JSON
＋只用最後 window 天重建的 `WindowCache` → `step(T)` 與全量跑的第 T 日列逐位相同）、版本綁定（改一個 `Rules`
欄位同日列必須不同且兩版並存）、`P_cs` 不套 N、PIT（`in_rank_pool` 取 T−1 為止）、同日順序（大盤 T−5 二爻）、
交易日 vs 曆日靜態守門、記憶體不在此驗（見 test_replay_state）。
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import scan_features as SF  # noqa: E402
from iching import replay_io as RIO  # noqa: E402
from iching import replay_state as RS  # noqa: E402
from iching import replay_step as ST  # noqa: E402
from iching.features_io import FeatureStore  # noqa: E402
from iching.score import score_stock  # noqa: E402
from iching.score.params import HORIZONS, build_params  # noqa: E402
from iching.scores_io import ScoreStore, ScoreStoreError, flatten_row  # noqa: E402
from synth_db import DAYS, DV, build_full  # noqa: E402

TV = "0.2"
W = 30


@pytest.fixture(scope="module")
def cache(tmp_path_factory) -> Path:
    c = tmp_path_factory.mktemp("step") / "cache"
    build_full(c)
    assert SF.main(["--cache-dir", str(c), "--quiet", "--allow-short-warmup", "--warmup-days", "0"]) == 0
    return c


def _ps():
    ps = {m: build_params(m) for m in ("twse", "tpex")}
    return ps, {m: ps[m].model_version() for m in ps}


def _run(cache: Path, out: Path, *, ps=None, mv=None, window: int = W, until: str | None = None,
         start: str | None = None, cross: RS.CrossDayState | None = None, write: bool = True):
    """全量（或到 `until`）重播；回 (cross, wc, store_path)。"""
    if ps is None:
        ps, mv = _ps()
    src = RIO.ReplaySource(cache, DV, window=window)
    wc = RS.WindowCache(src.pool, src.factors, window=window)
    cross = cross or RS.CrossDayState()
    store = ScoreStore(out) if write else None
    try:
        if store:
            store.set_params(DV, {"model_version": mv, "text_version": TV, "window": window})
        for T in src.trading_dates(start=start, end=until):
            wc.ingest(src.read_day(T))
            if cross.last_date is not None and T <= cross.last_date:
                continue                                              # 重建視窗期間不重算（每日班路徑）
            r = ST.step(T, wc, cross, ps, data_version=DV, text_version=TV, model_version=mv)
            if store:
                store.write_day(DV, T, r.all_rows(), r.diag)
    finally:
        src.close()
        if store:
            store.close()
    return cross, wc


def _rows(out: Path, date: str):
    with ScoreStore(out, readonly=True) as s:
        return s.rows_for_day(DV, date)


# ---------------------------------------------------------------------------
def test_full_replay_lands_every_day_and_is_deterministic(cache, tmp_path):
    a, b = tmp_path / "a.db", tmp_path / "b.db"
    cross_a, _ = _run(cache, a)
    cross_b, _ = _run(cache, b)
    with ScoreStore(a, readonly=True) as s:
        assert s.dates(DV) == DAYS and s.missing_dates(DV, DAYS) == []
        c = s.counts(DV)
        assert c["replay_day"] == 80 and c["scores"] > 80 * 6
        d = s.day_diag(DV, DAYS[-1])
        assert d["n_market_rows"] == 6 and d["n_stocks"] == 5 and d["n_stock_rows"] == 15 and d["index_missing"] == ""
        assert len(s.versions()) == 2
    for T in DAYS:
        assert _rows(a, T) == _rows(b, T), T
    assert cross_a.to_json() == cross_b.to_json()


def test_single_step_from_serialized_state_matches_full_replay(cache, tmp_path):
    """每日班路徑＝parity 本體：T−1 的 CrossDayState JSON ＋ 只用最後 W 天重建的 WindowCache → step(T) 逐位相同。"""
    full = tmp_path / "full.db"
    _run(cache, full)
    T, T1 = DAYS[-1], DAYS[-2]
    cross_t1, _ = _run(cache, tmp_path / "upto.db", until=T1)
    js = cross_t1.to_json()
    ps, mv = _ps()
    cross = RS.CrossDayState.from_json(js)
    assert cross.last_date == T1
    src = RIO.ReplaySource(cache, DV, window=W)
    wc = RS.WindowCache(src.pool, src.factors, window=W)
    for d in src.trading_dates(start=DAYS[-W]):                          # 只重建視窗，不重算
        wc.ingest(src.read_day(d))
    src.close()
    r = ST.step(T, wc, cross, ps, data_version=DV, text_version=TV, model_version=mv)
    one = tmp_path / "one.db"
    with ScoreStore(one) as s:
        s.set_params(DV, {"model_version": mv, "text_version": TV, "window": W})
        s.write_day(DV, T, r.all_rows(), r.diag)
    ra, rb = _rows(full, T), _rows(one, T)
    assert len(ra) == len(rb) == 21
    for x, y in zip(ra, rb):
        assert x == y, (x["stock_id"], x["horizon"])
    # 兩邊推進後的跨日狀態也相同
    full_cross, _ = _run(cache, tmp_path / "full2.db")
    assert cross.to_json() == full_cross.to_json()


def test_version_binding_changed_rule_changes_rows_and_both_versions_coexist(cache, tmp_path):
    out = tmp_path / "v.db"
    ps, mv = _ps()
    _run(cache, out, ps=ps, mv=mv)
    # 挑一個在合成資料上**保證**改變數字的規則：個股初爻因基本面缺而 unknown，
    # `direction_unknown_policy` 由 missing 改 reweight 會讓 direction／base_score 從 None 變成數字
    ps2 = {m: ps[m].with_rules(direction_unknown_policy="reweight") for m in ps}
    mv2 = {m: ps2[m].model_version() for m in ps2}
    assert mv2 != mv
    out2 = tmp_path / "v2.db"
    _run(cache, out2, ps=ps2, mv=mv2)
    ra, rb = _rows(out, DAYS[-1]), _rows(out2, DAYS[-1])
    assert {r["model_version"] for r in ra} == set(mv.values()) and {r["model_version"] for r in rb} == set(mv2.values())
    strip = lambda rows: [{k: v for k, v in r.items() if k != "model_version"} for r in rows]  # noqa: E731
    assert strip(ra) != strip(rb)                                     # 同日結果不同
    # 兩版寫進同一個檔：邏輯鍵不撞，版本表兩筆各市場
    with ScoreStore(out) as s:
        with pytest.raises(ScoreStoreError):
            s.set_params(DV, {"model_version": mv2, "text_version": TV, "window": W})   # 參數指紋不同 → 拒
        n0 = s.counts(DV)["scores"]
        src = RIO.ReplaySource(cache, DV, window=W)
        wc = RS.WindowCache(src.pool, src.factors, window=W)
        cross = RS.CrossDayState()
        for d in src.trading_dates():
            wc.ingest(src.read_day(d))
            if d == DAYS[-1]:
                r = ST.step(d, wc, cross, ps2, data_version=DV, text_version=TV, model_version=mv2)
        src.close()
        # set_params 被拒後 write_day 也必須拒（參數指紋拒混寫不能只靠呼叫端自律；13a-2 驗收建議 #4）
        with pytest.raises(ScoreStoreError):
            s.write_day(DV, DAYS[-1], r.all_rows(), r.diag)
        assert len(s.versions()) == 2 and s.counts(DV)["scores"] == n0
        assert {x["model_version"] for x in s.rows_for_day(DV, DAYS[-1])} == set(mv.values())


def test_p_cs_passes_through_unscaled_and_by_horizon(cache, tmp_path):
    _, wc = _run(cache, tmp_path / "p.db", write=False)
    cross = RS.CrossDayState()
    ps, _ = _ps()
    fs = FeatureStore(cache / "features.db", readonly=True)
    pcs = fs.day_p_cs(DV, "twse", DAYS[-1])["1101"]
    fs.close()
    for h, w in (("short", 10), ("swing", 20), ("mid", 60)):
        si = wc.stock_inputs("1101", h, DAYS[-1], cross)
        ss = score_stock(si, ps["twse"], h)
        assert si.p_cs_long_excess == pcs[w]
        assert ss.lines["3"].meta["p_cs_long_excess"] == pcs[w]         # 原生 0–100 直達引擎，不套 N


def test_rank_pool_flag_is_point_in_time(cache, tmp_path):
    out = tmp_path / "pit.db"
    _run(cache, out)
    # ADV 60 日：第 60 日（index 59）取 eligible() 時只有 59 天成交值 → 全部 0；第 61 日起達門檻者為 1
    r59 = [r for r in _rows(out, DAYS[59]) if r["stock_id"] != "__MARKET__"]
    assert r59 and all(r["in_rank_pool"] == 0 for r in r59)
    r60 = {(r["stock_id"], r["horizon"]): r["in_rank_pool"] for r in _rows(out, DAYS[60]) if r["stock_id"] != "__MARKET__"}
    assert r60[("1101", "short")] == 1 and r60[("2330", "mid")] == 1 and r60[("1103", "short")] == 0   # 1103 第 30 日才上市
    assert all(r["in_rank_pool"] is None for r in _rows(out, DAYS[60]) if r["stock_id"] == "__MARKET__")
    with ScoreStore(out, readonly=True) as s:
        assert s.day_diag(DV, DAYS[59])["n_in_pool"] == 0 and s.day_diag(DV, DAYS[60])["n_in_pool"] == 4


def test_same_day_order_market_line2_t_minus_5_and_stock_history(cache, tmp_path):
    out = tmp_path / "o.db"
    cross, _ = _run(cache, out)
    for mk in ("twse", "tpex"):
        for h in HORIZONS:
            l2_5 = [r for r in _rows(out, DAYS[-5]) if r["stock_id"] == "__MARKET__" and r["market"] == mk and r["horizon"] == h][0]["line_2"]
            assert cross.market_line2_t_minus_5(mk, h) == l2_5
    hist = cross.stock_line2_history("1101", "short")
    stored = [[r for r in _rows(out, d) if r["stock_id"] == "1101" and r["horizon"] == "short"][0]["line_2"] for d in DAYS[-9:]]
    assert hist == stored
    # 大盤列的 own_state／other_market_state 都有填（flags.basic_state 非 undetermined 需兩爻皆有狀態）
    m = [r for r in _rows(out, DAYS[-1]) if r["stock_id"] == "__MARKET__"]
    assert len(m) == 6 and all(r["flags"]["basic_state"] in ("S1", "S2", "S3", "S4", "undetermined") for r in m)
    assert all(r["line_states"] and len(r["line_states"]) == 6 and r["streaks"].count(",") == 5 for r in m)


def test_market_line2_t_minus_5_is_read_before_today_is_pushed(cache, tmp_path):
    """同日順序 (c)：`line2_score_t_minus_5` 讀的是 T−5，**不是**推入 T 之後的 T−4（13a-2 驗收突變存活，補此守門）。
    第 5 個交易日（index 4）step 前歷史只有 4 筆 → 大盤旗標 `missing_causes` 含 `line2_t_minus_5_missing`；
    若先 push 再讀，歷史變 5 筆、缺值消失。第 6 日（index 5）起才可得，且值＝落地的 DAYS[i−5] 的 line_2。"""
    ps, mv = _ps()
    src = RIO.ReplaySource(cache, DV, window=W)
    wc = RS.WindowCache(src.pool, src.factors, window=W)
    cross = RS.CrossDayState()
    seen: dict[int, dict] = {}
    for i, T in enumerate(src.trading_dates()[:7]):
        wc.ingest(src.read_day(T))
        before = {h: cross.market_line2_t_minus_5("twse", h) for h in HORIZONS}
        r = ST.step(T, wc, cross, ps, data_version=DV, text_version=TV, model_version=mv)
        rows = {(x["market"], x["horizon"]): x for _, x in r.market_rows}
        seen[i] = {"before": before, "rows": rows, "line2": {h: rows[("twse", h)]["line_2"] for h in HORIZONS}}
    src.close()
    for h in HORIZONS:
        assert seen[4]["before"][h] is None
        fl = json.loads(seen[4]["rows"][("twse", h)]["flags"])
        assert "line2_t_minus_5_missing" in fl["missing_causes"]
        assert seen[5]["before"][h] == seen[0]["line2"][h] and seen[6]["before"][h] == seen[1]["line2"][h]
        fl5 = json.loads(seen[5]["rows"][("twse", h)]["flags"])
        assert "line2_t_minus_5_missing" not in fl5["missing_causes"]


def test_replay_day_is_deterministic_except_elapsed(cache, tmp_path):
    a, b = tmp_path / "a.db", tmp_path / "b.db"
    _run(cache, a)
    _run(cache, b)
    with ScoreStore(a, readonly=True) as sa, ScoreStore(b, readonly=True) as sb:
        for d in DAYS:
            x, y = sa.day_diag(DV, d), sb.day_diag(DV, d)
            x.pop("elapsed_ms")
            y.pop("elapsed_ms")
            assert x == y, d
            assert x["n_stock_rows"] == 3 * x["n_stocks"] and x["n_market_rows"] == 6


def test_step_refuses_out_of_order_calls(cache, tmp_path):
    ps, mv = _ps()
    src = RIO.ReplaySource(cache, DV, window=W)
    wc = RS.WindowCache(src.pool, src.factors, window=W)
    cross = RS.CrossDayState()
    wc.ingest(src.read_day(DAYS[0]))
    with pytest.raises(ST.ReplayStepError):
        ST.step(DAYS[1], wc, cross, ps, data_version=DV, text_version=TV, model_version=mv)   # 尚未 ingest
    ST.step(DAYS[0], wc, cross, ps, data_version=DV, text_version=TV, model_version=mv)
    with pytest.raises(ST.ReplayStepError):
        ST.step(DAYS[0], wc, cross, ps, data_version=DV, text_version=TV, model_version=mv)   # 同日重算
    with pytest.raises(ST.ReplayStepError):
        ST.step(DAYS[1], wc, cross, {"twse": ps["twse"]}, data_version=DV, text_version=TV, model_version=mv)
    src.close()


def test_score_store_replaces_whole_day_and_never_writes_50_for_missing(tmp_path):
    out = tmp_path / "s.db"
    from iching.score.transform import Missing
    base = {"market": "twse", "horizon": "short", "stock_id": "1101", "tpe_trading_date": "2020-01-01", "scope": "stock",
            "line_1": Missing("x"), "line_1_unknown": True, "line_1_coverage_ratio": 0.0, "line_1_reweighted": False,
            "lines_provisional": None, "lines_formal": [1, 0, 1, 0, 1, 0], "king_wen": 63, "hexagram_name": "水火既濟",
            "base_score": Missing("y"), "coverage": "unknown", "calibrated": False, "flags": None}
    r = flatten_row(base, line_states="ynyn-y", streaks="0,1,0,0,0,0", in_rank_pool=1)
    assert r["line_1"] is None and r["base_score"] is None and r["line_1_unknown"] == 1 and r["lines_formal"] == "101010"
    with pytest.raises(ScoreStoreError):
        flatten_row({**base, "lines_formal": [1, 0]}, line_states="ynyn-y", streaks="0,0,0,0,0,0", in_rank_pool=0)
    diag = {"text_version": TV, "model_version_twse": "m1", "model_version_tpex": "m2", "index_missing": []}
    with ScoreStore(out) as s:
        with pytest.raises(ScoreStoreError):
            s.write_day(DV, "2020-01-01", [("m1", r)], diag)                # 未 set_params → 拒
        s.set_params(DV, {"x": 1})
        with pytest.raises(ScoreStoreError):
            s.write_day(DV, "2020-01-01", [("m1", r), ("m1", dict(r))], diag)   # 同批撞鍵 → 拒，不 last-wins
        assert s.counts(DV)["scores"] == 0                                   # 且整日交易回滾
        r2 = dict(r, stock_id="2330")
        assert s.write_day(DV, "2020-01-01", [("m1", r), ("m1", r2)], diag) == 2
        assert s.write_day(DV, "2020-01-01", [("m1", r)], diag) == 1        # 重寫較少列 → 舊鍵不得殘留
        assert [x["stock_id"] for x in s.rows_for_day(DV, "2020-01-01")] == ["1101"]
        with pytest.raises(ScoreStoreError):
            s.write_day(DV, "2020-01-02", [("m1", r)], diag)                # 列日期 ≠ 寫入日
        assert s.clear(DV)["scores"] == 1 and s.counts(DV) == {"scores": 0, "replay_day": 0}
    with pytest.raises(ScoreStoreError):
        ScoreStore(tmp_path / "沒有.db", readonly=True)
    with ScoreStore(out, readonly=True) as s:
        with pytest.raises(Exception):
            s.conn.execute("DELETE FROM versions")


def test_schema_follows_b3_2(tmp_path):
    with ScoreStore(tmp_path / "x.db") as s:
        sqls = {n: q for n, q in s.conn.execute("SELECT name, sql FROM sqlite_master WHERE type='table'")}
        assert "WITHOUT ROWID" in sqls["scores"] and "WITHOUT ROWID" in sqls["replay_day"]
        assert "version_id INTEGER" in sqls["scores"] and "model_version" not in sqls["scores"]
        idx = {n for n, in s.conn.execute("SELECT name FROM sqlite_master WHERE type='index' AND sql IS NOT NULL")}
        assert {"idx_scores_date", "idx_scores_stock"} <= idx
        assert s.conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"


def test_static_gates():
    src = ROOT / "src" / "iching"
    st = (src / "replay_step.py").read_text(encoding="utf-8")
    assert not re.search(r"sqlite", st, re.I) and "timedelta(days=" not in st
    for p in src.glob("replay*.py"):
        assert "timedelta(days=" not in p.read_text(encoding="utf-8"), p.name
    assert "import sqlite3" in (src / "scores_io.py").read_text(encoding="utf-8")
    assert json.loads((ROOT / "spec" / "dimensions.json").read_text(encoding="utf-8"))["targets"]["scores_db_row"]["key"] == \
        ["market", "horizon", "stock_id", "tpe_trading_date", "model_version", "data_version", "text_version"]
