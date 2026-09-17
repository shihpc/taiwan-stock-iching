"""PIT 池合成世界（`docs/P3-PIT-POOL.md` §2 #2～#7）：沿 `synth_db`／`test_daily_entrants` 的做法。

世界（`synth_db.add_pit_rows`）：在 `build_full` 七檔之上加 Z（tpex→twse，生效 DAYS[ZC]）、W（emerging→tpex，生效 DAYS[WC]，
自第 0 日就有成交列）、Y（twse，DAYS[YD] 起沒有列＝下市、快照仍留一列）。三個轉換點都落在種子日 K 之後，每日班路徑會跨過它們。
**參考**＝`scan_features`＋`replay_scores` 全程；**每日班**＝K 匯種子後以 `FakeFM` 逐日 `daily_run` 到底（同一份 cache、同一份快照）。

- 兩市 N 逐日對照獨立手算（§2 #4／#5）；Z 在 DAYS[ZC−1] 仍 tpex 桶（突變「生效日不 +1」會紅）；W 在 WC 前有成交列仍不在池
  （突變「members 拿掉 market∈{twse,tpex}」會紅）；Y 在 YD 前在池、YD 起不在（§2 #3 的**可判定半邊**——快照裡沒有的代號本版不進池，
  見 §6 未做項）。
- 兩條路徑逐日分數逐位相同（§2 #6）；`update_pool` 新殘留列＝池變（§1 第 5 點）；`pool_semantics` 版本綁定（§2 #7）。
入池側 X（§2 #2）由 `tests/test_daily_entrants.py` 既有世界守（參考快照含 X → PIT 池 T<E 也含 X；T≥E+5 逐位相同；parity ①連帶 rc 0），
本檔只加一條「T<E 參考含 X」的直接斷言，不重建那個世界。
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

import diff_scores as DS  # noqa: E402
import export_seed as ES  # noqa: E402
import pit_report as PR  # noqa: E402
import replay_scores as RP  # noqa: E402
import scan_features as SF  # noqa: E402
from iching import bundle_io as B  # noqa: E402
from iching import calendar as CAL  # noqa: E402
from iching import daily_core as DC  # noqa: E402
from iching import daily_pipeline as DP  # noqa: E402
from iching import feed as F  # noqa: E402
from iching import replay_io as RIO  # noqa: E402
from iching import replay_state as RS  # noqa: E402
from iching import universe as U  # noqa: E402
from iching.features_io import FeatureStore, params_fingerprint  # noqa: E402
from iching.liquidity import AdvTracker  # noqa: E402
from iching.run_common import build_params_payload, load_state, save_state  # noqa: E402
from iching.scan import DailyScanner  # noqa: E402
from iching.score.assemble import MARKET_STOCK_ID  # noqa: E402
from iching.score.params import MARKETS, build_params  # noqa: E402
from iching.scores_io import ScoreStore  # noqa: E402
from synth_db import (DAYS, DV, ENTRANT_INFO, INFO, LATE_I, MALFORMED_I, PIT_W, PIT_Y, PIT_Z, SUSPEND_I,  # noqa: E402
                      add_entrant_rows, add_pit_rows, build_full)
from test_daily_run import K, WINDOW, FakeFM, _run  # noqa: E402

WC, ZC, YD = K + 3, K + 6, K + 8                      # W 轉上櫃、Z 轉上市、Y 下市（皆在種子日 K 之後）
HIST = RS.MARKET_LINE2_HIST


def expected_n(i: int) -> dict[str, int]:
    """兩市廣度母體 N 的獨立手算（`synth_db` 七檔的既有事實 ＋ 三檔 PIT 情境）。"""
    twse = {"1101", "2330"}
    if i not in SUSPEND_I:
        twse.add("1102")
    if i >= LATE_I:
        twse.add("1103")
    if i >= ZC:
        twse.add(PIT_Z)
    if i < YD:
        twse.add(PIT_Y)
    tpex = set()
    if i != MALFORMED_I:
        tpex.add("6488")
    if i < ZC:
        tpex.add(PIT_Z)
    if i >= WC:
        tpex.add(PIT_W)
    return {"twse": len(twse), "tpex": len(tpex)}


@pytest.fixture(scope="module")
def world(tmp_path_factory) -> dict:
    base = tmp_path_factory.mktemp("pit")
    cache, repo = base / "cache", base / "repo"
    build_full(cache)
    add_pit_rows(cache, z_c=ZC, w_c=WC, y_d=YD)
    assert SF.main(["--cache-dir", str(cache), "--quiet", "--allow-short-warmup", "--warmup-days", "0"]) == 0
    assert RP.main(["--cache-dir", str(cache), "--window", str(WINDOW), "--to", DAYS[K], "--quiet"]) == 0
    state_k = base / "state_k.json"
    shutil.copy(cache / "scores.db.state.json", state_k)
    assert RP.main(["--cache-dir", str(cache), "--window", str(WINDOW), "--resume", "--quiet"]) == 0
    src = RIO.ReplaySource(cache, DV, window=WINDOW)
    CAL.write_calendar_json(repo / DC.CALENDAR_TPE_FILE, CAL.calendar_payload("tpe", src.trading_dates(), DV))
    CAL.write_calendar_json(repo / DP.CALENDAR_US_FILE,
                            CAL.calendar_payload("us", sorted({d for d, *_ in src.read_day(DAYS[K]).us}), DV))
    src.close()
    assert ES.main(["--cache-dir", str(cache), "--out", str(repo), "--window", str(WINDOW), "--state", str(state_k)]) == 0
    shutil.copytree(repo, base / "repo_seed")
    fm = FakeFM(cache)
    rcs = {DAYS[i]: _run(repo, cache, DAYS[i], fm) for i in range(K + 1, len(DAYS))}
    return {"cache": cache, "repo": repo, "seed": base / "repo_seed", "state_k": state_k, "rcs": rcs}


# ---------------------------------------------------------------------------
# §2 #1／#4／#5／#3：day_records 的市場桶與母體
def test_day_records_buckets_z_w_y_point_in_time(world):
    cache = world["cache"]
    prices, uni = F.open_ro(cache / "prices.db"), F.open_ro(cache / "universe.db")
    try:
        pool = F.load_pool(uni)
        factors, _ = F.load_factors(prices, DV)
        assert pool.transitions[PIT_Z] == ((None, "tpex"), (DAYS[ZC], "twse"))
        assert pool.transitions[PIT_W] == ((None, "emerging"), (DAYS[WC], "tpex"))
        assert pool.transitions[PIT_Y] == ((None, "twse"),) and PIT_Y in pool
        for i, (d, rows) in enumerate(F.iter_days(prices, DV, True)):
            recs, amounts = F.day_records(d, rows, pool, factors)
            by = {r.stock_id: r for r in recs}
            n = {m: sum(1 for r in recs if r.market == m and r.close_adj is not None) for m in MARKETS}
            assert n == expected_n(i), (d, n)
            assert by[PIT_Z].market == ("twse" if i >= ZC else "tpex"), d          # ZC−1 仍 tpex（突變「不 +1」會紅）
            assert (PIT_W in by) is (i >= WC), d                                    # 興櫃期有成交列仍不在池（突變「拿掉 market 門」會紅）
            assert (PIT_W in amounts) is (i >= WC), d
            assert (PIT_Y in by) is (i < YD), d                                     # 下市：沒有列就不在（快照仍留 Y 一列）
            assert {r.market for r in recs} <= set(MARKETS)
    finally:
        prices.close()
        uni.close()


def test_features_breadth_n_matches_hand_count(world):
    """落地的 `market_breadth.n_stocks` 逐日＝手算（含 Z 換桶、W 入池、Y 出池的那幾天）。"""
    with FeatureStore(world["cache"] / "features.db", readonly=True) as fs:
        for i, d in enumerate(DAYS):
            got = {m: fs.day_breadth(DV, m, d)["n_stocks"] for m in MARKETS}
            assert got == expected_n(i), (d, got)


def test_entrant_reference_contains_x_before_e_pit(tmp_path):
    """§2 #2 入池側：參考快照含 X → PIT 池對 T<E（甚至第 0 日）就含 X，成員由成交列決定、不是快照日期。"""
    pool = U.PitPool.from_snapshot_rows(INFO + [ENTRANT_INFO])
    X = ENTRANT_INFO["stock_id"]
    assert pool.transitions[X] == ((None, "twse"),) and X in pool.members(DAYS[0], [X])
    cache = tmp_path / "cx"
    build_full(cache)
    add_entrant_rows(cache, info=True)
    prices, uni = F.open_ro(cache / "prices.db"), F.open_ro(cache / "universe.db")
    try:
        p2 = F.load_pool(uni)
        d, rows = next(iter(F.iter_days(prices, DV, True)))
        recs, _ = F.day_records(d, rows, p2, {})
        assert d == DAYS[0] and X in {r.stock_id for r in recs}
    finally:
        prices.close()
        uni.close()


# ---------------------------------------------------------------------------
# §2 #6：兩條路徑 parity（跨 WC／ZC／YD）
def test_two_paths_bitwise_across_transitions(world):
    repo, cache = world["repo"], world["cache"]
    assert all(rc == 0 for rc in world["rcs"].values()), world["rcs"]
    ref = ScoreStore(cache / "scores.db", readonly=True)
    got = ScoreStore(cache.parent / "daily_pit.db")
    src = RIO.ReplaySource(cache, DV, window=WINDOW)
    try:
        got.set_params(DV, ref.params_of(DV))
        assert ref.params_of(DV)["pool_semantics"] == U.POOL_SEMANTICS == "pit-1"
        for d in DAYS[: K + 1]:
            src.read_day(d)
        seen_z_twse = seen_w = False
        for i in range(K + 1, len(DAYS)):
            T = DAYS[i]
            refb = src.read_day(T)
            assert B.bundle_path(repo, T).read_bytes() == B.write_bundle(cache.parent / "refb", refb).read_bytes(), T
            js = json.loads((repo / DC.SCORES_DIR / f"{T}.json").read_text(encoding="utf-8"))
            rows = [(r["model_version"], {k: v for k, v in r.items() if k != "model_version"}) for r in js["rows"]]
            got.write_day(DV, T, rows, js["diag"])
            n_common, n_diff, msgs = DS.diff_day(ref, got, DV, T)
            assert n_diff == 0 and n_common == len(rows) > 0, (T, msgs[:5])
            mk = {r["stock_id"]: r["market"] for r in got.rows_for_day(DV, T) if r["stock_id"] != MARKET_STOCK_ID}
            assert mk.get(PIT_Z) == ("twse" if i >= ZC else "tpex"), T
            assert (PIT_W in mk) is (i >= WC) and (PIT_Y in mk) is (i < YD), T
            seen_z_twse |= mk.get(PIT_Z) == "twse"
            seen_w |= PIT_W in mk
        assert seen_z_twse and seen_w
        st_ref = json.loads((cache / "scores.db.state.json").read_text(encoding="utf-8"))
        st_got = json.loads((repo / DC.STATE_FILE).read_text(encoding="utf-8"))
        st_ref.pop("meta"), st_got.pop("meta")
        assert st_got == st_ref
    finally:
        ref.close()
        got.close()
        src.close()


def test_rebuild_pool_assertion_uses_listed_ids_at_T(world):
    """T 當日排名池斷言的 `∩ pool` 走 `pool.listed_ids(T)`：W 在 WC 前不在 listed_ids，兩側都沒追蹤它；重建端 `adv` 在 WC 之前不含 W。"""
    repo = world["seed"]
    _, pool = DC.load_pool_file(repo / DC.POOL_FILE)
    assert PIT_W not in pool.listed_ids(DAYS[WC - 1]) and PIT_W in pool.listed_ids(DAYS[WC])
    _, factors, _ = DC.load_factors_file(repo / DC.FACTORS_FILE)
    files = [(d, p) for d, p in B.list_bundles(repo) if d <= DAYS[K]]
    _, adv, _ = DC.rebuild_from_bundles(files, pool, factors, data_version=DV, window=WINDOW)
    assert adv.history_of(PIT_W) == [] and adv.history_of(PIT_Z) and adv.history_of(PIT_Y)


# ---------------------------------------------------------------------------
# 成交門只有一支（2026-09-17 驗收退回）：feed.day_records 與 WindowCache.ingest 都走 universe.traded_ids
def test_traded_gate_is_single_function_used_by_both_paths(world, monkeypatch):
    """monkeypatch `universe.traded_ids` 成「全部算有成交」→ 兩條路徑同時改變：`day_records` 把停牌的 1102 當成交（進 amounts），
    `WindowCache.ingest` 也把它推進 `today_amounts`。若任一側自己再寫一次 `is_traded_row`，那一側不會跟著變、本測試紅。
    真突變（改 `traded_ids` 本體）由 `test_feed`／本檔 breadth 手算／兩路 parity 一起守。"""
    cache = world["cache"]
    _, pool = DC.load_pool_file(world["seed"] / DC.POOL_FILE)
    _, factors, _ = DC.load_factors_file(world["seed"] / DC.FACTORS_FILE)
    d = DAYS[SUSPEND_I[0]]
    prices = F.open_ro(cache / "prices.db")
    try:
        rows = [r for x, r in F.iter_days(prices, DV, True, d, d)][0]
    finally:
        prices.close()
    b = B.read_bundle(B.bundle_path(world["seed"], d)) if B.bundle_path(world["seed"], d).exists() else None
    if b is None:
        src = RIO.ReplaySource(cache, DV, window=WINDOW)
        try:
            b = src.read_day(d)
        finally:
            src.close()
    assert b.stocks["1102"]["Trading_Volume"] == 0.0
    _, amounts0 = F.day_records(d, rows, pool, factors)
    wc0 = RS.WindowCache(pool, factors, window=WINDOW)
    wc0.ingest(b)
    assert "1102" not in amounts0 and "1102" not in wc0.today_amounts
    monkeypatch.setattr(U, "traded_ids", lambda items: {str(s) for s, _ in items})
    _, amounts1 = F.day_records(d, rows, pool, factors)
    wc1 = RS.WindowCache(pool, factors, window=WINDOW)
    wc1.ingest(b)
    assert "1102" in amounts1 and "1102" in wc1.today_amounts              # 兩側同時跟著唯一那支變


# ---------------------------------------------------------------------------
# §1 第 5 點：update_pool 的 pool_changed 連轉換表一起比
def test_update_pool_changes_when_new_residual_row_appears(world, tmp_path):
    repo = tmp_path / "repo"
    shutil.copytree(world["seed"], repo)
    fm = FakeFM(world["cache"])
    base_rows = fm.get("TaiwanStockInfo")
    before = (repo / DC.POOL_FILE).read_bytes()
    dated = [dict(r, date="2030-01-01") if r["date"] == "2026-09-11" else r for r in base_rows]      # 只有最新列的 date 推進
    changed, pool = DP.update_pool(repo, dated, DV)
    assert changed is False and (repo / DC.POOL_FILE).read_bytes() == before and pool.transitions[PIT_Z][1][0] == DAYS[ZC]
    # 2330 冒出一列更早的 tpex 殘留列（＝發現它曾在上櫃）→ 成員／產業零變動，但轉換表變 → 池變、改寫
    plus = base_rows + [{"stock_id": "2330", "type": "tpex", "industry_category": "半導體業", "stock_name": "丁", "date": "2020-02-10"}]
    changed, pool = DP.update_pool(repo, plus, DV)
    assert changed is True and pool.transitions["2330"] == ((None, "tpex"), ("2020-02-11", "twse"))
    assert set(pool) == set(DC.load_pool_file(repo / DC.POOL_FILE)[1])


# ---------------------------------------------------------------------------
# §2 #7：版本綁定——舊語意（payload 無 pool_semantics）的指紋一律拒續算
def _old_payload(cross) -> dict:
    ps = {m: build_params(m) for m in MARKETS}
    mv = {m: ps[m].model_version() for m in MARKETS}
    p = build_params_payload(mv, WINDOW, cross.adv, fundamentals=True)
    assert p.pop("pool_semantics") == "pit-1"
    return p


def test_daily_refuses_state_with_pre_pit_params_sha(world, tmp_path):
    """`cross.json` 的 `params_sha` 是舊語意（無 `pool_semantics`）算出來的 → `run_offline` 拒（不落任何檔）。
    突變：把 `pool_semantics` 從 `build_params_payload` 拿掉 → 新舊指紋相同 → 不拒 → 本測試紅。"""
    repo = tmp_path / "repo"
    shutil.copytree(world["seed"], repo)
    cross = load_state(repo / DC.STATE_FILE)
    old_sha = params_fingerprint(_old_payload(cross))
    assert old_sha != cross.meta["params_sha"]
    cross.meta = {**cross.meta, "params_sha": old_sha}
    save_state(repo / DC.STATE_FILE, cross)
    with pytest.raises(Exception, match="meta"):
        DC.run_offline(repo, DAYS[K + 1], window=WINDOW)
    assert not (repo / DC.SCORES_DIR).exists()


def test_replay_resume_and_scan_resume_refuse_pre_pit_db(world, tmp_path, capsys):
    """舊 `scores.db`（replay_meta 無 pool_semantics）→ `replay_scores --resume` rc 2；
    舊 `features.db`（scan_meta 無 pool_semantics）→ `scan_features`（不 --rebuild）rc 2。"""
    cache = tmp_path / "cache"
    shutil.copytree(world["cache"], cache)
    db = cache / "scores.db"
    cross = load_state(cache / "scores.db.state.json")
    old = _old_payload(cross)
    old_sha = params_fingerprint(old)
    with ScoreStore(db) as s:
        s.conn.execute("UPDATE replay_meta SET params_sha=?, params_json=? WHERE data_version=?",
                       (old_sha, json.dumps(old, sort_keys=True, ensure_ascii=False), DV))
        s.conn.commit()
    cross.meta = {**cross.meta, "params_sha": old_sha}
    save_state(cache / "scores.db.state.json", cross)
    assert RP.main(["--cache-dir", str(cache), "--window", str(WINDOW), "--resume", "--quiet"]) == 2
    assert "已用不同參數" in capsys.readouterr().err
    fdb = cache / "features.db"
    with FeatureStore(fdb) as fs:
        p = SF.build_params(DailyScanner(), AdvTracker())
        assert p.pop("pool_semantics") == "pit-1"
        fs.conn.execute("UPDATE scan_meta SET params_sha=?, params_json=? WHERE data_version=?",
                        (params_fingerprint(p), json.dumps(p, sort_keys=True, ensure_ascii=False), DV))
        fs.conn.commit()
    assert SF.main(["--cache-dir", str(cache), "--quiet", "--allow-short-warmup", "--warmup-days", "0", "--resume"]) == 2
    assert "已用不同參數" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# scripts/pit_report.py：轉換表報告與新舊 scores.db 比對（hetzner_pit.sh 第 5 步的離線煙霧）
def test_pit_report_transitions_and_compare(world, tmp_path, capsys):
    cache = world["cache"]
    assert PR.main(["transitions", "--cache-dir", str(cache), "--out", str(tmp_path / "t.json")]) == 0
    out = capsys.readouterr().out
    assert f"{PIT_Z}: tpex@起點 → twse@{DAYS[ZC]}" in out and "跨 twse/tpex 1 檔" in out
    rep = json.loads((tmp_path / "t.json").read_text(encoding="utf-8"))
    assert set(rep["cross_market"]) == {PIT_Z} and rep["n_transitioned"] == 2 and rep["anomalies"] == {}
    # 同一個 DB 自比：全部逐位相同、rc 0；把「舊 DB」少掉 Z 的列（模擬舊語意）→ 歸 (a)、rc 0；砍掉 1101 的列 → 未解釋、rc 1
    same = tmp_path / "same.db"
    shutil.copy(cache / "scores.db", same)
    assert PR.main(["compare", "--cache-dir", str(cache), "--old", str(same), "--new", str(cache / "scores.db"), "--show", "3"]) == 0
    assert f"逐位相同 {len(DAYS)} 日" in capsys.readouterr().out
    with ScoreStore(same) as s:
        s.conn.execute("DELETE FROM scores WHERE stock_id=? AND date>=?", (PIT_Z, DAYS[ZC]))
        s.conn.commit()
    assert PR.main(["compare", "--cache-dir", str(cache), "--old", str(same), "--new", str(cache / "scores.db"), "--out", str(tmp_path / "c.json")]) == 0
    c = json.loads((tmp_path / "c.json").read_text(encoding="utf-8"))
    assert c["class_totals"]["a"] == len(DAYS) - ZC and c["unexplained_days"] == [] and c["days_identical"] == ZC
    with ScoreStore(same) as s:
        s.conn.execute("DELETE FROM scores WHERE stock_id=? AND date=?", ("1101", DAYS[5]))
        s.conn.commit()
    assert PR.main(["compare", "--cache-dir", str(cache), "--old", str(same), "--new", str(cache / "scores.db")]) == 1
    assert "未解釋 ['1101']" in capsys.readouterr().out
    # 收窄（2026-09-17）：有 (a) 的日子，其他 sid 的列只動池依賴欄 → 連帶；動到 `line_2` → 未解釋 rc 1
    narrow = tmp_path / "narrow.db"
    shutil.copy(cache / "scores.db", narrow)
    with ScoreStore(narrow) as s:
        s.conn.execute("DELETE FROM scores WHERE stock_id=? AND date>=?", (PIT_Z, DAYS[ZC]))
        s.conn.execute("UPDATE scores SET line_6=COALESCE(line_6,0)+1, base_score=COALESCE(base_score,0)+1 WHERE stock_id=? AND date=?",
                       ("1101", DAYS[ZC + 1]))
        s.conn.commit()
    assert PR.main(["compare", "--cache-dir", str(cache), "--old", str(narrow), "--new", str(cache / "scores.db"), "--out", str(tmp_path / "n.json")]) == 0
    n = json.loads((tmp_path / "n.json").read_text(encoding="utf-8"))
    day = next(r for r in n["per_day"] if r["date"] == DAYS[ZC + 1])
    assert day["linked_rows"] >= 3 and day["unexplained"] == [] and day["a"] == [PIT_Z]
    with ScoreStore(narrow) as s:
        s.conn.execute("UPDATE scores SET line_2=COALESCE(line_2,0)+1 WHERE stock_id=? AND date=?", ("2330", DAYS[ZC + 2]))
        s.conn.commit()
    assert PR.main(["compare", "--cache-dir", str(cache), "--old", str(narrow), "--new", str(cache / "scores.db"), "--out", str(tmp_path / "n1.json")]) == 1
    out = capsys.readouterr().out
    assert f"{DAYS[ZC + 2]}" in out and "差異欄組合 top 10" in out and "line_2" in out
    n1 = json.loads((tmp_path / "n1.json").read_text(encoding="utf-8"))
    d2 = next(r for r in n1["per_day"] if r["date"] == DAYS[ZC + 2])
    assert list(d2["unexplained_cols"]) == ["line_2"] and n1["unexplained_col_hist"] == d2["unexplained_cols"]
    assert d2["unexplained_cols"]["line_2"] >= 1                    # 2330 該日的列數（每 horizon 一列）
    assert d2["unexplained_onesided"] == {"old": 0, "new": 0}
    # 只在單側的未解釋列（前面把 same.db 的 1101@DAYS[5] 砍掉 → 那些列只在新 DB）要記在 onesided.new，不進欄位直方圖
    assert PR.main(["compare", "--cache-dir", str(cache), "--old", str(same), "--new", str(cache / "scores.db"), "--out", str(tmp_path / "s.json")]) == 1
    capsys.readouterr()
    sj = json.loads((tmp_path / "s.json").read_text(encoding="utf-8"))
    assert sj["unexplained_onesided_total"]["old"] == 0 and sj["unexplained_onesided_total"]["new"] >= 1
    assert sj["unexplained_col_hist"] == {}
    # 池依賴欄集合（2026-09-18）：動 `line_3`（產業中位報酬路徑）→ 連帶 rc 0；動 `line_4`（只吃自己資料）→ 未解釋 rc 1
    dep = tmp_path / "dep.db"
    shutil.copy(cache / "scores.db", dep)
    with ScoreStore(dep) as s:
        s.conn.execute("DELETE FROM scores WHERE stock_id=? AND date>=?", (PIT_Z, DAYS[ZC]))
        s.conn.execute("UPDATE scores SET line_3=COALESCE(line_3,0)+1, inner_trigram_score=COALESCE(inner_trigram_score,0)+1, "
                       "line_states='yyyyyy' WHERE stock_id=? AND date=?", ("1101", DAYS[ZC + 1]))
        s.conn.commit()
    assert PR.main(["compare", "--cache-dir", str(cache), "--old", str(dep), "--new", str(cache / "scores.db"), "--out", str(tmp_path / "d.json")]) == 0
    capsys.readouterr()
    dj = json.loads((tmp_path / "d.json").read_text(encoding="utf-8"))
    assert next(r for r in dj["per_day"] if r["date"] == DAYS[ZC + 1])["unexplained"] == []
    with ScoreStore(dep) as s:
        s.conn.execute("UPDATE scores SET line_4=COALESCE(line_4,0)+1 WHERE stock_id=? AND date=?", ("1101", DAYS[ZC + 1]))
        s.conn.commit()
    assert PR.main(["compare", "--cache-dir", str(cache), "--old", str(dep), "--new", str(cache / "scores.db"), "--out", str(tmp_path / "d2.json")]) == 1
    capsys.readouterr()
    d2j = json.loads((tmp_path / "d2.json").read_text(encoding="utf-8"))
    assert list(d2j["unexplained_col_hist"]) and all("line_4" in k for k in d2j["unexplained_col_hist"])
    assert {"line_1", "line_2", "line_4", "line_5", "market", "horizon"}.isdisjoint(PR.POOL_DEPENDENT_COLS)
    # 同日無任何 (a)/(b)/(c)：其他 sid 只動池依賴欄也不得歸連帶（沒有傳導源）
    with ScoreStore(narrow) as s:
        s.conn.execute("UPDATE scores SET line_6=COALESCE(line_6,0)+1 WHERE stock_id=? AND date=?", ("1101", DAYS[3]))
        s.conn.commit()
    assert PR.main(["compare", "--cache-dir", str(cache), "--old", str(narrow), "--new", str(cache / "scores.db"), "--out", str(tmp_path / "n2.json")]) == 1
    n2 = json.loads((tmp_path / "n2.json").read_text(encoding="utf-8"))
    assert DAYS[3] in n2["unexplained_days"]


# hetzner_pit.sh 第 6 步：分支尚不存在於 origin 時 EXPECT 必須是 40 個 0（2026-09-17 首輪實跑：
# 不帶 --verify 的 rev-parse 把原字串照印到 stdout，EXPECT 變兩行，push 以 cannot parse expected object name 失敗）
def test_hetzner_pit_expect_sha_when_remote_branch_absent(tmp_path):
    import re
    import subprocess

    script = (ROOT / "scripts" / "hetzner_pit.sh").read_text(encoding="utf-8")
    m = re.search(r"^EXPECT=\$\(.*\)$", script, re.M)
    assert m, "hetzner_pit.sh 找不到 EXPECT= 那一行"
    line = m.group(0)
    assert "--verify" in line and "-q" in line, line
    repo = tmp_path / "r"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    out = subprocess.run(["bash", "-c", f'BR=hetzner/pit-none; {line}; printf "%s" "$EXPECT"'],
                         cwd=repo, check=True, capture_output=True, text=True).stdout
    assert out == "0" * 40, repr(out)


# hetzner_pit.sh 第 0 步：pull 後 main 前進時必須改用新版腳本重新執行（2026-09-17 第二輪實跑：bash 已把舊版整份讀進緩衝，
# pull 換檔無效、漏跑 4b）。用臨時 bare origin 模擬：本機 checkout 停在 v1（pull 後多一行 V1-CONTINUED），origin/main 是 v2
# （第 0 步後印 V2-MARKER 就退出）。期望：log 有「改用新版」與 V2-MARKER、沒有 V1-CONTINUED，且暫存的自我複製檔被清掉。
def test_hetzner_pit_reexecs_new_script_after_pull(tmp_path):
    import subprocess

    def git(*a, cwd):
        return subprocess.run(["git", *a], cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()

    src = (ROOT / "scripts" / "hetzner_pit.sh").read_text(encoding="utf-8")
    anchor = "git log -1 --format='HEAD %h %ci %s'\n"
    assert src.count(anchor) == 1
    # v2：第 0 步 pull＋re-exec 判斷之後立刻印記號退出（不需要 python／cache）
    reexec_end = "fi\n"
    head, tail = src.split(anchor)
    tail_after_reexec = tail.split(reexec_end, 1)[1]
    v2 = head + anchor + tail.split(reexec_end, 1)[0] + reexec_end + 'echo "V2-MARKER"; exit 0\n' + tail_after_reexec
    v1 = head + anchor + tail.split(reexec_end, 1)[0] + reexec_end + 'echo "V1-CONTINUED"; exit 0\n' + tail_after_reexec
    bare = tmp_path / "origin.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(bare)], check=True)
    work = tmp_path / "work"
    subprocess.run(["git", "clone", "-q", str(bare), str(work)], check=True)
    git("config", "user.email", "t@t", cwd=work)
    git("config", "user.name", "t", cwd=work)
    (work / "scripts").mkdir()
    (work / "scripts" / "hetzner_pit.sh").write_text(v1, encoding="utf-8")
    git("add", "-A", cwd=work)
    git("commit", "-qm", "v1", cwd=work)
    git("push", "-q", "-u", "origin", "main", cwd=work)
    (work / "scripts" / "hetzner_pit.sh").write_text(v2, encoding="utf-8")
    git("commit", "-qam", "v2", cwd=work)
    git("push", "-q", "origin", "main", cwd=work)
    git("reset", "-q", "--hard", "HEAD~1", cwd=work)          # 本機停在 v1，origin/main 是 v2
    assert "V1-CONTINUED" in (work / "scripts" / "hetzner_pit.sh").read_text(encoding="utf-8")
    tmpdir = tmp_path / "tmp"
    tmpdir.mkdir()
    r = subprocess.run(["bash", "scripts/hetzner_pit.sh", "2026-09-01", "2026-09-02"], cwd=work, capture_output=True, text=True,
                       env={**__import__("os").environ, "TMPDIR": str(tmpdir), "HETZNER_PIT_LOG": "", "HETZNER_PIT_SELF": "",
                            "HETZNER_PIT_PULLED": "", "HETZNER_PIT_REPO": ""})
    out = r.stdout + r.stderr
    assert r.returncode == 0, out
    assert "改用新版 scripts/hetzner_pit.sh 重新執行" in out and "V2-MARKER" in out and "V1-CONTINUED" not in out, out
    assert git("rev-parse", "HEAD", cwd=work) == git("rev-parse", "origin/main", cwd=work)
    assert list(tmpdir.iterdir()) == [], list(tmpdir.iterdir())     # 自我複製的暫存檔已清掉
    logs = sorted((work / "cache" / "logs").glob("pit-round-*.log"))
    assert len(logs) == 1 and "V2-MARKER" in logs[0].read_text(encoding="utf-8")
