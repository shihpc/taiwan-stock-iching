"""每日班新入池檔歷史對齊（`docs/P2-DAILY-PLAN.md` §7.7 甲：entrants 側檔）——合成世界驗收。

世界：股票 X（`synth_db.ENTRANT_INFO`，1104）自第 0 日就有價量／法人／融資列，但 `TaiwanStockInfo` 快照到 `DAYS[E]`（E＝K+4）才含它；
種子在 K 匯出時池不含 X（`cache_nox`：原料表有 X、快照沒有）。**參考**＝含 X 的最新池重播全程（`cache_x`）。每日班以 `FakeFM`
逐日跑到底，E 之前 `hide_info` 藏住 X。驗收條目見各測試 docstring；spec 模糊處與證不出來的項目寫在對應測試的說明裡。

`--entrants-window 80`（> 80 日合成世界）而不是預設的 `window`（30）：側檔要讓 X 的 `DailyScanner` deque（maxlen 61）與 ADV
（60 日）在 E 當日就滿，30 日不夠（實測：預設 30 時 E 之後 `mid` 大盤二爻與 X 的 `in_rank_pool` 持續不同）。生產 `WINDOW_N`＝320
夠用的推導見 `test_production_window_covers_scanner_and_adv_lookback`。
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
import parity_check as PC  # noqa: E402
import replay_scores as RP  # noqa: E402
import scan_features as SF  # noqa: E402
from iching import bundle_io as B  # noqa: E402
from iching import calendar as CAL  # noqa: E402
from iching import collect as C  # noqa: E402
from iching import daily_core as DC  # noqa: E402
from iching import daily_fetch as DF  # noqa: E402
from iching import daily_pipeline as DP  # noqa: E402
from iching import replay_io as RIO  # noqa: E402
from iching import replay_state as RS  # noqa: E402
from iching.liquidity import ADV_WINDOW, AdvTracker  # noqa: E402
from iching.score import MARKET_STOCK_ID  # noqa: E402
from iching.score.params import MKT_L2_WIN  # noqa: E402
from iching.scores_io import ScoreStore  # noqa: E402
from iching.store import Store  # noqa: E402
from synth_db import DAYS, DV, ENTRANT_INFO, INFO, add_entrant_rows, build_full  # noqa: E402
from test_daily_run import K, WINDOW, FakeFM, _run, fetcher_for  # noqa: E402

X = ENTRANT_INFO["stock_id"]
E = K + 4                                 # X 入池日索引（§7.7：E≈K+4）
EW = 80                                   # 側檔回看交易日數（≥ 合成世界長度 → 區間起點夾到 DAYS[0]）
HIST = RS.MARKET_LINE2_HIST               # 5：大盤旗標讀 T−5 二爻歷史


def _scores_rows(repo: Path, T: str) -> tuple[list, dict]:
    js = json.loads((repo / DC.SCORES_DIR / f"{T}.json").read_text(encoding="utf-8"))
    return [(r["model_version"], {k: v for k, v in r.items() if k != "model_version"}) for r in js["rows"]], js["diag"]


def _diff(ref: ScoreStore, got: ScoreStore, repo: Path, T: str) -> tuple[int, int, list[str]]:
    rows, diag = _scores_rows(repo, T)
    got.write_day(DV, T, rows, diag)
    return DS.diff_day(ref, got, DV, T)


def _diff_cols(ref: ScoreStore, got: ScoreStore, T: str) -> dict[tuple, set[str]]:
    """同鍵兩側皆有的列 → 不同的欄集合；只在一側的列 → {'<only>'}。"""
    ra = {DS._key(r): r for r in ref.rows_for_day(DV, T)}
    rb = {DS._key(r): r for r in got.rows_for_day(DV, T)}
    out: dict[tuple, set[str]] = {}
    for k in set(ra) ^ set(rb):
        out[k] = {"<only>"}
    for k in set(ra) & set(rb):
        cols = {c for c in set(ra[k]) | set(rb[k]) if ra[k].get(c) != rb[k].get(c)}
        if cols:
            out[k] = cols
    return out


def _seed_repo(cache_seed: Path, repo: Path, *, to_index: int) -> None:
    """種子：`cache_seed` 重播到 DAYS[to_index] 存快照 → 日曆（tpe／us）→ `export_seed`。"""
    assert SF.main(["--cache-dir", str(cache_seed), "--quiet", "--allow-short-warmup", "--warmup-days", "0"]) == 0
    assert RP.main(["--cache-dir", str(cache_seed), "--window", str(WINDOW), "--to", DAYS[to_index], "--quiet"]) == 0
    src = RIO.ReplaySource(cache_seed, DV, window=WINDOW)
    CAL.write_calendar_json(repo / DC.CALENDAR_TPE_FILE, CAL.calendar_payload("tpe", src.trading_dates(), DV))
    CAL.write_calendar_json(repo / DP.CALENDAR_US_FILE,
                            CAL.calendar_payload("us", sorted({d for d, *_ in src.read_day(DAYS[to_index]).us}), DV))
    src.close()
    assert ES.main(["--cache-dir", str(cache_seed), "--out", str(repo), "--window", str(WINDOW)]) == 0


def _reference(cache: Path) -> None:
    assert SF.main(["--cache-dir", str(cache), "--quiet", "--allow-short-warmup", "--warmup-days", "0"]) == 0
    assert RP.main(["--cache-dir", str(cache), "--window", str(WINDOW), "--quiet"]) == 0


@pytest.fixture(scope="module")
def world(tmp_path_factory) -> dict:
    base = tmp_path_factory.mktemp("entrants")
    cx, cn, repo = base / "cache_x", base / "cache_nox", base / "repo"
    build_full(cx)
    add_entrant_rows(cx, info=True)                      # 參考：原料＋快照都有 X
    _reference(cx)
    build_full(cn)
    add_entrant_rows(cn, info=False)                     # 種子：原料有 X、快照沒有（K 時池不含 X）
    _seed_repo(cn, repo, to_index=K)
    _, pool0 = DC.load_pool_file(repo / DC.POOL_FILE)
    assert X not in pool0 and not (repo / DC.ENTRANTS_DIR).exists()
    shutil.copytree(repo, base / "repo_seed")
    fm = FakeFM(cx)
    fm.hide_info = {X}
    snap_before_e: dict[str, bytes] = {}
    for i in range(K + 1, len(DAYS)):
        if i == E:
            fm.hide_info = set()                         # X 自 DAYS[E] 起出現在 TaiwanStockInfo
            snap_before_e = {p.name: p.read_bytes() for _, p in B.list_bundles(repo)}
            shutil.copytree(repo, base / "repo_before_E")
        assert _run(repo, cx, DAYS[i], fm, extra=["--entrants-window", str(EW)]) == 0, DAYS[i]
    return {"cache_x": cx, "cache_nox": cn, "repo": repo, "seed": base / "repo_seed", "before_E": base / "repo_before_E",
            "snap_before_E": snap_before_e, "fm": fm}


@pytest.fixture(scope="module")
def stores(world) -> dict:
    ref = ScoreStore(world["cache_x"] / "scores.db", readonly=True)
    got = ScoreStore(world["cache_x"].parent / "daily_entrants.db")
    got.set_params(DV, ref.params_of(DV))
    yield {"ref": ref, "got": got}
    ref.close()
    got.close()


# ---------------------------------------------------------------------------
def test_scores_align_with_reference_from_e(world, stores):
    """§7.7 ①：**T ≥ E+5 的分數與參考逐位相同**（含大盤列與 X 自己的列，`diag` 9 欄亦同）。

    **spec 寫 T ≥ E；實測只能證到 T ≥ E+`MARKET_LINE2_HIST`（5）**：E～E+4 唯一的差異是大盤列的 `flags` 欄——
    `market_flags` 讀狀態鏈裡 T−5 的二爻分數（`line2_score_t_minus_5`），E 前那幾日每日班算出的二爻是污染值（X 不在
    scanner 內），要 5 個交易日才滾出歷史；側檔補的是原料、補不到狀態鏈。E 起二爻**分數本身**已與參考相同（這正是甲要修的
    「大盤二爻整天不同」）。E 之前：大盤二爻與全部個股六爻不同、X 列只在參考（＝§7.6.3 的設計缺口，本測試順帶記錄其形狀）。"""
    ref, got = stores["ref"], stores["got"]
    for i in range(K + 1, len(DAYS)):
        T = DAYS[i]
        n_common, n_diff, msgs = _diff(ref, got, world["repo"], T)
        cols = _diff_cols(ref, got, T)
        if i < E:
            assert n_diff > 0 and all(k[2] == X for k, c in cols.items() if c == {"<only>"}), (T, msgs[:3])
            assert any(k[2] == MARKET_STOCK_ID and "line_2" in c for k, c in cols.items()), (T, msgs[:3])
            continue
        if i < E + HIST:
            # 只允許大盤列、只允許 flags 欄；X 自己的列在 E 當日起就相同
            assert all(k[2] == MARKET_STOCK_ID and c == {"flags"} for k, c in cols.items()), (T, msgs[:5])
            assert not any(k[2] == X for k in cols), T
            continue
        assert n_diff == 0 and n_common > 0, (T, msgs[:5])
        assert any(k[2] == X for k in {DS._key(r) for r in got.rows_for_day(DV, T)}), T       # X 的列真的在算
        dr, dg = ref.day_diag(DV, T), got.day_diag(DV, T)
        for c in PC.DIAG_COLS:
            assert dr[c] == dg[c], (T, c)
    # E 當日與 E+1～E+4 之間至少有一日「只差 flags」真的出現（否則上面的寬容分支是死的）
    n_flag_days = sum(1 for i in range(E, E + HIST) if _diff(ref, got, world["repo"], DAYS[i])[1])
    assert n_flag_days >= 1


def test_sidefile_matches_reference_read_day(world):
    """§7.7 ②：`data/entrants/X.json.gz` 存在、`from`／`to` 正確、`days` 逐日逐位＝參考 `ReplaySource.read_day(d).stocks[X]`；
    寫檔決定性（同內容再寫一次位元組相同）；1103（第 30 日才上市、在最舊包之後首見）依定義也是候選、側檔對原料包補不到任何列。"""
    repo, cx = world["repo"], world["cache_x"]
    ents = DC.load_entrants(repo)
    assert set(ents) == {X, "1103"}
    e = ents[X]
    assert (e.frm, e.to) == (DAYS[0], DAYS[E - 1]) and e.data_version == DV
    assert sorted(e.days) == DAYS[:E]                                # X 自第 0 日起每天都有價量列
    src = RIO.ReplaySource(cx, DV, window=WINDOW)
    try:
        for d in DAYS[:E]:
            assert B.dumps_json(e.days[d]) == B.dumps_json(src.read_day(d).stocks[X]), d
        assert e.days[DAYS[10]]["foreign_net"] is not None and e.days[DAYS[10]]["margin_balance"] is not None   # 籌碼真的併進來
    finally:
        src.close()
    p = DC.entrant_path(repo, X)
    raw = p.read_bytes()
    assert B.write_json_gz(p, DC.entrant_payload(e)).read_bytes() == raw
    js = B.read_json_gz(p)
    assert js["schema"] == DC.ENTRANT_SCHEMA and js["stock_id"] == X and set(js) == {"schema", "stock_id", "data_version", "from", "to", "days"}
    e2 = ents["1103"]
    assert e2.to == DAYS[K] and all(d >= DAYS[30] for d in e2.days)


def test_bundles_untouched_and_pool_semantics(world):
    """§7.7 ③：原料包位元組與改動前相同（E 之前寫好的包一個位元組都沒被併入改到）；E 之前的包沒有 X、E 起的包＝參考
    `read_day` 逐位（池含 X 後當日即進原料包，D-1 語意）。"""
    repo, cx = world["repo"], world["cache_x"]
    files = {name: p for _, p in B.list_bundles(repo) for name in [p.name]}
    for name, raw in world["snap_before_E"].items():
        assert files[name].read_bytes() == raw, name
    src = RIO.ReplaySource(cx, DV, window=WINDOW)
    try:
        for i, d in enumerate(DAYS):
            refb = src.read_day(d)                                   # 游標連續（美股／匯率增量才對齊）
            b = B.read_bundle(B.bundle_path(repo, d))
            if i < E:
                assert X not in b.stocks, d
            else:
                assert X in b.stocks, d
                assert B.bundle_path(repo, d).read_bytes() == B.write_bundle(cx.parent / "refb", refb).read_bytes(), d
    finally:
        src.close()


def test_mutation_without_merge_breaks_alignment(world, stores, monkeypatch, tmp_path):
    """§7.7 ④ 突變：拿掉併入（`merge_entrants` 一律不補）→ E 當日大盤二爻分數與參考不同；對照組（未突變）重算 E 的分數檔
    與每日班產出逐位相同、與參考只差 flags。"""
    ref = stores["ref"]
    src_repo, final = world["before_E"], world["repo"]
    T = DAYS[E]

    def prepared(name: str) -> Path:
        r = tmp_path / name
        shutil.copytree(src_repo, r)
        for rel in (Path(B.BUNDLE_DIR) / B.bundle_path(final, T).name, Path(DC.POOL_FILE), Path(DC.CALENDAR_TPE_FILE),
                    Path(DC.ENTRANTS_DIR) / DC.entrant_path(final, X).name):
            shutil.copy(final / rel, r / rel) if (r / rel).parent.exists() else (r / rel).parent.mkdir(parents=True) or shutil.copy(final / rel, r / rel)
        return r

    def scores_sans_elapsed(repo: Path) -> dict:
        js = json.loads(DC.scores_path(repo, T).read_text(encoding="utf-8"))
        js["diag"].pop("elapsed_ms", None)
        return js

    ctrl = prepared("ctrl")
    assert [x["date"] for x in DC.run_offline(ctrl, T, window=WINDOW)["days"]] == [T]
    assert scores_sans_elapsed(ctrl) == scores_sans_elapsed(final)
    orig = DC.merge_entrants
    monkeypatch.setattr(DC, "merge_entrants", lambda b, ents: orig(b, {}))
    mut = prepared("mut")
    res = DC.run_offline(mut, T, window=WINDOW)
    assert res["days"][0]["rebuild"]["entrants_merged"] == 0
    got = ScoreStore(tmp_path / "mut.db")
    try:
        got.set_params(DV, ref.params_of(DV))
        rows, diag = _scores_rows(mut, T)
        got.write_day(DV, T, rows, diag)
        _, n_diff, _ = DS.diff_day(ref, got, DV, T)
        cols = _diff_cols(ref, got, T)
        assert n_diff > 0 and any(k[2] == MARKET_STOCK_ID and "line_2" in c for k, c in cols.items())
    finally:
        got.close()


def test_parity_check_spill_before_e_rc0(world):
    """§7.7 ⑤（2026-09-15 主對話裁定擴到整日）：T < E 的**全部列**（大盤列、個股列、X 只在參考的列）歸「①連帶」；
    E～E+4 只差 flags 的大盤列同歸①連帶；E+5 起零差異；整段區間 rc 0、印計數。個股列 T < E 也不同（1101／1102／1103 的
    `line_6`：大盤方向分數由被污染的二爻而來、再進個股上爻——§7.6.3 標「推測、未驗」那條，本世界證實）正是擴到整日的理由。"""
    logs: list[str] = []
    res = PC.run(world["cache_x"], world["repo"], log=logs.append, show=100)      # 預設 --show 10 會截掉 E 前每日十幾行明細
    assert res.errors == [] and res.rc == 0 and res.market_layer_days == []
    assert res.counts() == {c: 0 for c in PC.CLASSES}
    assert res.spill_days == DAYS[K + 1:E] + [d for d in DAYS[E:E + HIST] if res.days[d].n_diff]
    for d in DAYS[K + 1:E]:
        day = res.days[d]
        assert day.n_diff > 0 and day.classes == {} and set(day.spill) == set(day.diff_sids)
        assert {MARKET_STOCK_ID, X, "1101"} <= set(day.spill) and all("入池前" in why and "整日" in why for why in day.spill.values())
        assert day.stock_diffs[X].endswith("只在參考") and not day.hint
    for d in DAYS[E:E + HIST]:
        if res.days[d].n_diff:
            assert set(res.days[d].spill) == {MARKET_STOCK_ID} and res.days[d].spill[MARKET_STOCK_ID].count("flags") == 1
            assert res.days[d].classes == {}
    for d in DAYS[E + HIST:]:
        assert res.days[d].n_diff == 0 and not res.days[d].spill and not res.days[d].stock_diffs, d
    assert any(f"{PC.SPILL_MARK} {MARKET_STOCK_ID}" in line for line in logs) and any(f"{PC.SPILL_MARK} {X}" in line for line in logs)
    assert any(f"{PC.SPILL_MARK} " in line and "(日,檔)" in line for line in logs)
    assert PC.main(["--cache-dir", str(world["cache_x"]), "--repo", str(world["repo"]), "--quiet"]) == 0
    # 區間自 E 起：只剩 flags 連帶 → rc 0（X 首見＝區間起日，flags 窗仍要認得它）
    sub = PC.run(world["cache_x"], world["repo"], start=DAYS[E], log=lambda s: None)
    assert sub.rc == 0 and sub.counts() == {c: 0 for c in PC.CLASSES} and sub.spill_days == [d for d in DAYS[E:E + HIST] if sub.days[d].n_diff]


def test_parity_check_spill_does_not_hide_real_diff(world, tmp_path):
    """連帶不吃真差異：E 之後的日子——E+2（flags 連帶窗內）改掉大盤一格 `base_score`、區間末改一格個股——兩者皆④、rc 1；
    E 前整日連帶照舊、不影響④的計數。"""
    repo = tmp_path / "repo"
    shutil.copytree(world["repo"], repo)
    for T, sid in ((DAYS[E + 2], MARKET_STOCK_ID), (DAYS[-1], "1101")):
        p = DC.scores_path(repo, T)
        js = json.loads(p.read_text(encoding="utf-8"))
        row = next(r for r in js["rows"] if r["stock_id"] == sid)
        row["base_score"] = 99.0
        DC.write_json(p, js)
    res = PC.run(world["cache_x"], repo, log=lambda s: None)
    assert res.rc == 1 and res.stocks_of(PC.CLASS_UNEXPLAINED) == [(DAYS[E + 2], MARKET_STOCK_ID), (DAYS[-1], "1101")]
    assert MARKET_STOCK_ID not in res.days[DAYS[E + 2]].spill and res.spill_days[: E - K - 1] == DAYS[K + 1:E]
    assert PC.main(["--cache-dir", str(world["cache_x"]), "--repo", str(repo), "--quiet"]) == 1


def test_fetch_failure_records_warning_and_retries_next_day(world):
    """抓取失敗案例：E 當日 X 的 per-stock 查詢丟例外 → 當日照常計分（rc 0、分數檔在）、無側檔、`warnings` 有記；
    下一班成功 → 側檔落地（`to`＝新的 T−1）。"""
    cx = world["cache_x"]
    repo = world["seed"].parent / "repo_fail"
    shutil.copytree(world["seed"], repo)
    fm = FakeFM(cx)
    fm.hide_info = {X}
    for i in range(K + 1, E):
        assert _run(repo, cx, DAYS[i], fm, extra=["--entrants-window", str(EW)]) == 0
    fm.hide_info = set()
    fm.fail_data_ids = {X}
    summary = DP.run_pipeline(repo, fetcher_for(cx, fm), upto=DAYS[E], window=WINDOW, entrants_window=EW, log=lambda *_: None)
    assert summary["status"] == "ok" and [x["date"] for x in summary["done"]] == [DAYS[E]]
    done = summary["done"][0]
    assert done["entrants"]["candidates"] == [X] and done["entrants"]["written"] == []
    assert any(w.startswith(f"entrant:{X}:TransientError") for w in done["warnings"])
    assert not DC.entrant_path(repo, X).exists() and DC.scores_path(repo, DAYS[E]).exists()
    assert json.loads((repo / DC.STATE_FILE).read_text(encoding="utf-8"))["last_date"] == DAYS[E]
    calls_e = [c for c in fm.calls if c[1].get("data_id") == X]
    assert len(calls_e) == 1                                         # 第一個資料集就炸，後面 4 次不送
    fm.fail_data_ids = set()
    summary = DP.run_pipeline(repo, fetcher_for(cx, fm), upto=DAYS[E + 1], window=WINDOW, entrants_window=EW, log=lambda *_: None)
    done = summary["done"][0]
    assert done["entrants"] == {"candidates": [X], "written": [X], "calls": 5, "merged": ["1103", X], "bad": []} and done["warnings"] == []
    e = DC.load_entrants(repo)[X]
    assert (e.frm, e.to) == (DAYS[0], DAYS[E]) and sorted(e.days) == DAYS[:E + 1]
    # 側檔落地後：再叫一次不再是候選（0 次呼叫）
    summary = DP.run_pipeline(repo, fetcher_for(cx, fm), upto=DAYS[E + 2], window=WINDOW, entrants_window=EW, log=lambda *_: None)
    assert summary["done"][0]["entrants"] == {"candidates": [], "written": [], "calls": 0, "merged": ["1103", X], "bad": []}


def test_bad_sidefile_is_skipped_warned_and_self_heals(world):
    """壞側檔（2026-09-15 驗收後補）：故意把 X 的 `data/entrants/X.json.gz` 寫成壞 gzip →
    當班（重抓被擋）rc 0、warnings 有記（含檔名與例外類別）、壞檔不刪不覆蓋、其他側檔（1103）照常併入、X 的歷史沒併進去
    （分數與參考的差異不只 flags）；下一班（FakeFM 正常）偵測把它視為無側檔 → 重抓、tmp+replace 覆蓋 → 檔案變好、
    warnings 消失、分數回到只差 flags／零差異；再壞一次且抓得到 → 同一班就自癒。"""
    cx = world["cache_x"]
    repo = world["seed"].parent / "repo_badfile"
    shutil.copytree(world["seed"], repo)
    fm = FakeFM(cx)
    fm.hide_info = {X}
    for i in range(K + 1, E):
        assert _run(repo, cx, DAYS[i], fm, extra=["--entrants-window", str(EW)]) == 0
    fm.hide_info = set()
    assert _run(repo, cx, DAYS[E], fm, extra=["--entrants-window", str(EW)]) == 0
    p = DC.entrant_path(repo, X)
    good_bytes = p.read_bytes()
    assert set(DC.load_entrants(repo)) == {X, "1103"}
    ref = ScoreStore(cx / "scores.db", readonly=True)
    got = ScoreStore(repo.parent / "daily_badfile.db")
    try:
        got.set_params(DV, ref.params_of(DV))
        # 當班：壞 gzip ＋ 重抓被擋 → 壞檔留在原位
        p.write_bytes(b"not a gzip at all")
        fm.fail_data_ids = {X}
        summary = DP.run_pipeline(repo, fetcher_for(cx, fm), upto=DAYS[E + 1], window=WINDOW, entrants_window=EW, log=lambda *_: None)
        assert summary["status"] == "ok" and [x["date"] for x in summary["done"]] == [DAYS[E + 1]]
        done = summary["done"][0]
        bad_w = [w for w in done["warnings"] if w.startswith(f"entrant:{X}:bad-sidefile:")]
        assert len(bad_w) == 1 and "BundleError" in bad_w[0] and p.name in bad_w[0] and "BadGzipFile" in bad_w[0]
        assert any(w.startswith(f"entrant:{X}:TransientError") for w in done["warnings"])
        assert done["entrants"] == {"candidates": [X], "written": [], "calls": 1, "merged": ["1103"], "bad": [X]}
        assert p.read_bytes() == b"not a gzip at all" and DC.scores_path(repo, DAYS[E + 1]).exists()
        st = json.loads((repo / DC.STATE_FILE).read_text(encoding="utf-8"))
        assert len(st["adv"]["amt"][X]) == ADV_WINDOW and X in AdvTracker.from_state(st["adv"]).eligible()   # 鏈上 ADV 未被壞檔那班改動
        good, bad = DC.read_entrants(repo)
        assert set(good) == {"1103"} and set(bad) == {X} and bad[X] == bad_w[0]
        _, n_diff, _ = _diff(ref, got, repo, DAYS[E + 1])
        cols = _diff_cols(ref, got, DAYS[E + 1])
        assert n_diff > 0 and any(k[2] == MARKET_STOCK_ID and "line_2" in c for k, c in cols.items())   # X 歷史沒併入 → 二爻污染
        # 下一班：抓得到 → 視為無側檔重抓、覆蓋壞檔、warnings 消失
        fm.fail_data_ids = set()
        summary = DP.run_pipeline(repo, fetcher_for(cx, fm), upto=DAYS[E + 2], window=WINDOW, entrants_window=EW, log=lambda *_: None)
        done = summary["done"][0]
        assert done["entrants"] == {"candidates": [X], "written": [X], "calls": 5, "merged": ["1103", X], "bad": [X]}
        assert [w for w in done["warnings"] if w.startswith("entrant:")] == [w for w in done["warnings"] if "bad-sidefile" in w]
        e = DC.load_entrants(repo)[X]
        assert (e.frm, e.to) == (DAYS[0], DAYS[E + 1]) and sorted(e.days) == DAYS[:E + 2] and DC.read_entrants(repo)[1] == {}
        assert p.read_bytes() != good_bytes and not list(p.parent.glob("*.tmp"))        # 內容變（to 前進）、無 tmp 殘留
        _, n_diff, _ = _diff(ref, got, repo, DAYS[E + 2])
        cols = _diff_cols(ref, got, DAYS[E + 2])
        assert all(k[2] == MARKET_STOCK_ID and c == {"flags"} for k, c in cols.items())   # 回到只差 flags（或零差異）
        # 再下一班：全好 → 零 entrant warnings、不再是候選
        summary = DP.run_pipeline(repo, fetcher_for(cx, fm), upto=DAYS[E + 3], window=WINDOW, entrants_window=EW, log=lambda *_: None)
        done = summary["done"][0]
        assert done["entrants"] == {"candidates": [], "written": [], "calls": 0, "merged": ["1103", X], "bad": []}
        assert not any(w.startswith("entrant:") for w in done["warnings"])
        # 截斷的 gzip、且抓得到 → 同一班自癒（偵測在重建之前）
        p.write_bytes(good_bytes[:-7])
        summary = DP.run_pipeline(repo, fetcher_for(cx, fm), upto=DAYS[E + 4], window=WINDOW, entrants_window=EW, log=lambda *_: None)
        done = summary["done"][0]
        assert done["entrants"]["bad"] == [X] and done["entrants"]["written"] == [X] and done["entrants"]["merged"] == ["1103", X]
        assert any("bad-sidefile" in w and "EOFError" in w for w in done["warnings"]) and DC.read_entrants(repo)[1] == {}
    finally:
        ref.close()
        got.close()


def test_read_entrants_skips_every_bad_shape_and_prune_keeps_them(tmp_path):
    """壞側檔的全部形狀都只是 warning：非 gzip／截斷／壓縮流損壞／非 JSON／空檔／schema 不符／缺鍵／days 非物件／檔名與內容代號不符；
    好檔照常讀回；`prune_entrants` 不刪壞檔；`load_entrants` 只回好檔。"""
    import gzip
    root = tmp_path
    DC.write_entrant(root, DC.Entrant("1101", DV, DAYS[0], DAYS[3], {DAYS[1]: {"close": 1.0}}))
    ok_gz = gzip.compress(b'{"a":1}')
    cases = {"2001": b"not a gzip", "2002": ok_gz[:-6], "2003": ok_gz[:12] + b"\xff\xfe\x00" + ok_gz[15:], "2004": gzip.compress(b"nope"),
             "2005": b"", "2006": B.dumps_json({"schema": 2, "stock_id": "2006", "from": "x", "to": "y", "days": {}}),
             "2007": B.dumps_json({"schema": 1, "stock_id": "2007", "from": "x", "days": {}}),
             "2008": B.dumps_json({"schema": 1, "stock_id": "2008", "from": "x", "to": "y", "days": [1]}),
             "2009": B.dumps_json({"schema": 1, "stock_id": "9999", "from": "x", "to": "y", "days": {}}),
             "2010": B.dumps_json({"schema": 1, "stock_id": "2010", "from": "x", "to": "y", "days": {"d": 5}})}
    for sid, raw in cases.items():
        path = DC.entrant_path(root, sid)
        path.write_bytes(raw if sid in ("2001", "2002", "2003", "2004", "2005") else gzip.compress(raw))
    good, bad = DC.read_entrants(root)
    assert set(good) == {"1101"} and set(bad) == set(cases)
    for sid, w in bad.items():
        assert w.startswith(f"entrant:{sid}:bad-sidefile:") and f"{sid}.json.gz" in w
    assert "BadGzipFile" in bad["2001"] and "EOFError" in bad["2002"] and "error" in bad["2003"] and "JSONDecodeError" in bad["2004"]
    assert "DailyCoreError" in bad["2006"] and "DailyCoreError" in bad["2007"] and "DailyCoreError" in bad["2008"] and "DailyCoreError" in bad["2009"]
    assert set(DC.load_entrants(root)) == {"1101"}
    assert DC.prune_entrants(root, DAYS[10]) == 1 and sorted(s for s, _ in DC.list_entrants(root)) == sorted(cases)   # 好檔 to<最舊包→刪；壞檔留
    # 覆寫壞檔＝tmp+replace（自癒路徑）
    DC.write_entrant(root, DC.Entrant("2001", DV, DAYS[0], DAYS[3], {}))
    assert "2001" in DC.read_entrants(root)[0] and not list((root / DC.ENTRANTS_DIR).glob("*.tmp"))


def test_log_line_reports_entrants_and_calls(world, tmp_path):
    repo = tmp_path / "repo"
    shutil.copytree(world["seed"], repo)
    fm = FakeFM(world["cache_x"])
    fm.hide_info = {X}
    lines: list[str] = []
    DP.run_pipeline(repo, fetcher_for(world["cache_x"], fm), upto=DAYS[K + 1], window=WINDOW, entrants_window=EW, log=lines.append)
    assert any("entrants=1 calls=5" in ln for ln in lines)          # 1103（第 30 日上市）依定義是候選：5 次
    DP.run_pipeline(repo, fetcher_for(world["cache_x"], fm), upto=DAYS[K + 2], window=WINDOW, entrants_window=EW, log=lines.append)
    assert any("entrants=0 calls=0" in ln for ln in lines)


# ---------------------------------------------------------------------------
# 出池側（§7.7 第一段的主張：「這一側已對齊、不需動」）——重建過濾本來就對齊，但排名池斷言會卡死每日班，2026-09-15 修法後成立
@pytest.fixture(scope="module")
def world_exit(tmp_path_factory) -> dict:
    """Y＝2330 於 DAYS[E2] 從快照消失。種子＝池含 Y 跑到 K；參考＝最新池不含 Y 全程重播。"""
    base = tmp_path_factory.mktemp("exit")
    cf, cn, repo = base / "cache_full", base / "cache_noy", base / "repo"
    Y, E2 = "2330", K + 4
    build_full(cf)
    _seed_repo(cf, repo, to_index=K)
    build_full(cn)
    with Store(cn / "universe.db") as u:
        u.record_success("stock_info", "raw_stock_info", "all", [r for r in INFO if r["stock_id"] != Y], DV, "TaiwanStockInfo", ("stock_id",))
    _reference(cn)
    fm = FakeFM(cf)
    rcs: dict[str, int] = {}
    for i in range(K + 1, len(DAYS)):
        if i == E2:
            fm.hide_info = {Y}
        rcs[DAYS[i]] = _run(repo, cf, DAYS[i], fm)
    return {"cache_full": cf, "cache_noy": cn, "repo": repo, "rcs": rcs, "Y": Y, "E2": E2}


def test_exit_side_scores_align_with_reference_without_y(world_exit, stores_exit):
    """§7.7 出池側：Y 出池後每日班 T ≥ E2+5 的分數與「最新池不含 Y」的參考逐位相同（`diag` 9 欄亦同）；E2～E2+4 只差大盤 `flags`
    （同入池側：T−5 二爻歷史仍讀到 E2 前含 Y 的值）；E2 之前每日班含 Y、參考不含 → 差異存在（Y 列只在每日班、大盤二爻不同）。

    2026-09-15 實測原本 E2 起每班 rc 2（排名池斷言：鏈上 `cross.adv` 仍含 Y、重建從未追蹤 Y），主對話裁定最小修法＝斷言兩側
    各取 ∩ 現行 pool 再比（`daily_core.rebuild_from_bundles`），鏈上 Y 的 deque 不動、自然衰減。"""
    Y, E2, repo = world_exit["Y"], world_exit["E2"], world_exit["repo"]
    assert all(rc == 0 for rc in world_exit["rcs"].values()), world_exit["rcs"]
    assert Y not in DC.load_pool_file(repo / DC.POOL_FILE)[1]
    ref, got = stores_exit["ref"], stores_exit["got"]
    n_flag_days = 0
    for i in range(K + 1, len(DAYS)):
        T = DAYS[i]
        n_common, n_diff, msgs = _diff(ref, got, repo, T)
        cols = _diff_cols(ref, got, T)
        if i < E2:
            assert n_diff > 0 and all(k[2] == Y for k, c in cols.items() if c == {"<only>"}), (T, msgs[:3])
            assert any(k[2] == MARKET_STOCK_ID and "line_2" in c for k, c in cols.items()), (T, msgs[:3])
            continue
        assert not any(k[2] == Y for k in {DS._key(r) for r in got.rows_for_day(DV, T)}), T       # Y 出池後不再計分
        if i < E2 + HIST:
            assert all(k[2] == MARKET_STOCK_ID and c == {"flags"} for k, c in cols.items()), (T, msgs[:5])
            n_flag_days += int(n_diff > 0)
            continue
        assert n_diff == 0 and n_common > 0, (T, msgs[:5])
        dr, dg = ref.day_diag(DV, T), got.day_diag(DV, T)
        for c in PC.DIAG_COLS:
            assert dr[c] == dg[c], (T, c)
    assert n_flag_days >= 1


def test_exit_side_old_failure_mode_gone_and_chain_decays(world_exit):
    """修法前的失效模式（E2 起 rc 2、分數檔停在 E2−1）**已不再發生**的反向斷言：每一班 rc 0、每一日都有分數檔；
    鏈上 `cross.adv` 仍含 Y 的 deque（刻意不動）且尾端是出池後補的 0.0（自然衰減）——這正是斷言必須取 ∩ pool 的原因；
    重建端從未追蹤 Y。保留為反向斷言而非刪除：釘住「鏈上 deque 不動、只在比對時取交集」這個修法形狀，日後若改成
    直接把出池檔從狀態鏈剔除，這裡會先紅。"""
    Y, E2, repo, rcs = world_exit["Y"], world_exit["E2"], world_exit["repo"], world_exit["rcs"]
    assert all(rcs[DAYS[i]] == 0 for i in range(K + 1, len(DAYS)))
    assert sorted(p.stem for p in (repo / DC.SCORES_DIR).glob("*.json")) == DAYS[K + 1:]
    st = json.loads((repo / DC.STATE_FILE).read_text(encoding="utf-8"))
    dq = st["adv"]["amt"][Y]
    n_after = len(DAYS) - E2
    assert st["last_date"] == DAYS[-1] and len(dq) == ADV_WINDOW and dq[-n_after:] == [0.0] * n_after and dq[-n_after - 1] > 0
    _, pool = DC.load_pool_file(repo / DC.POOL_FILE)
    _, factors, _ = DC.load_factors_file(repo / DC.FACTORS_FILE)
    _, adv, _ = DC.rebuild_from_bundles(B.list_bundles(repo), pool, factors, data_version=DV, window=WINDOW)
    assert adv.history_of(Y) == [] and Y in AdvTracker.from_state(st["adv"]).eligible()      # 鏈上仍合格、重建端沒有它
    # 再叫一次 run_offline：沒有待計分日＝no-op（不會再撞斷言）
    assert DC.run_offline(repo, window=WINDOW)["days"] == []


@pytest.fixture(scope="module")
def stores_exit(world_exit) -> dict:
    ref = ScoreStore(world_exit["cache_noy"] / "scores.db", readonly=True)
    got = ScoreStore(world_exit["cache_noy"].parent / "daily_exit.db")
    got.set_params(DV, ref.params_of(DV))
    yield {"ref": ref, "got": got}
    ref.close()
    got.close()


# ---------------------------------------------------------------------------
# 單元：候選定義／區間／建構器 parity／修剪／狀態鏈採用／生產 window 夠不夠
def _bundle(d: str, sids: list[str]) -> RS.DayBundle:
    return RS.DayBundle(tpe_date=d, stocks={s: {"close": 1.0, "Trading_Volume": 1.0} for s in sids})


def test_entrant_candidates_definition():
    bundles = [(DAYS[0], _bundle(DAYS[0], ["1101"])), (DAYS[1], _bundle(DAYS[1], ["1101", "1103"])), (DAYS[2], _bundle(DAYS[2], ["1101", "1103", "1104"]))]
    pool = {"1101": {}, "1103": {}, "1104": {}, "2412": {}}
    assert DC.entrant_candidates(pool, bundles, have=[]) == ["1103", "1104"]     # 最舊包就在→否；晚於最舊包首見→是；從未出現（2412）→否
    assert DC.entrant_candidates(pool, bundles, have=["1104"]) == ["1103"]         # 已有側檔→否
    assert DC.entrant_candidates({"1103": {}}, bundles, have=[]) == ["1103"]
    assert DC.entrant_candidates(pool, [], have=[]) == []
    assert DC.first_seen(bundles) == {"1101": DAYS[0], "1103": DAYS[1], "1104": DAYS[2]}


def test_entrant_range_uses_repo_calendar():
    cal = DAYS
    assert DC.entrant_range(cal, DAYS[10], 3) == (DAYS[7], DAYS[9])
    assert DC.entrant_range(cal, DAYS[10], 30) == (DAYS[0], DAYS[9])           # 夾到日曆首日
    assert DC.entrant_range(cal, DAYS[0], 30) is None                          # 沒有更早的交易日
    with pytest.raises(DC.DailyCoreError, match="不在日曆內"):
        DC.entrant_range(cal, "2019-12-31", 30)


def test_entrant_days_from_rows_is_stocks_from_rows_per_day():
    """側檔列＝同一支 `collect.stocks_from_rows` 逐日餵出的結果（parity by construction）；別檔／無日期／無價量列的列被忽略。"""
    info = {"type": "twse", "industry_category": "水泥工業"}
    price = [{"date": DAYS[0], "stock_id": X, "close": 10.0, "Trading_Volume": 100.0, "Trading_money": 1000.0},
             {"date": DAYS[1], "stock_id": X, "close": 11.0, "Trading_Volume": 100.0, "Trading_money": 1100.0},
             {"date": DAYS[1], "stock_id": "1101", "close": 99.0, "Trading_Volume": 1.0, "Trading_money": 1.0},
             {"stock_id": X, "close": 12.0}]
    inst = [{"date": DAYS[0], "stock_id": X, "name": "Foreign_Investor", "buy": 3000.0, "sell": 1000.0},
            {"date": DAYS[2], "stock_id": X, "name": "Foreign_Investor", "buy": 1.0, "sell": 0.0}]      # 第 2 日無價量列→不成列
    margin = [{"date": DAYS[1], "stock_id": X, "MarginPurchaseTodayBalance": 7.0}]
    got = DF.entrant_days_from_rows(X, info, price, inst=inst, margin=margin)
    assert sorted(got) == [DAYS[0], DAYS[1]]
    for d in got:
        exp = C.stocks_from_rows([r for r in price if r.get("date") == d], {X: info}, inst_rows=[r for r in inst if r["date"] == d],
                                 margin_rows=[r for r in margin if r["date"] == d])[X]
        assert got[d] == exp
    assert got[DAYS[0]]["foreign_net"] == 2.0 and got[DAYS[1]]["margin_balance"] == 7.0 and got[DAYS[1]]["foreign_net"] is None
    # Fetcher.fetch_entrant：5 個資料集各 1 次、都帶 data_id／區間
    seen: list[tuple[str, dict]] = []

    class Spy:
        def get(self, dataset, **params):
            seen.append((dataset, params))
            return price if dataset == "TaiwanStockPrice" else []
    f = DF.Fetcher(Spy(), None, required=())
    days = f.fetch_entrant(X, info, DAYS[0], DAYS[5])
    assert sorted(days) == [DAYS[0], DAYS[1]] and f.n_calls == 5
    assert [d for d, _ in seen] == [DF.ds(k) for k in DF.ENTRANT_DATASETS]
    assert all(p == {"data_id": X, "start_date": DAYS[0], "end_date": DAYS[5]} for _, p in seen)


def test_merge_entrants_only_fills_missing_and_keeps_input(world):
    e = DC.load_entrants(world["repo"])[X]
    b = B.read_bundle(B.bundle_path(world["repo"], DAYS[5]))
    raw = B.dumps(b)
    m = DC.merge_entrants(b, {X: e})
    assert X in m.stocks and X not in b.stocks and list(m.stocks) == sorted(m.stocks) and B.dumps(b) == raw
    assert m.stocks[X] == e.days[DAYS[5]] and m.stocks[X] is not e.days[DAYS[5]]
    b2 = B.read_bundle(B.bundle_path(world["repo"], DAYS[E]))       # 該日原料包已有 X → 不覆蓋
    m2 = DC.merge_entrants(b2, {X: DC.Entrant(X, DV, DAYS[0], DAYS[E], {DAYS[E]: {"close": -1.0}})})
    assert m2.stocks[X] == b2.stocks[X]
    m3 = DC.merge_entrants(b, {X: DC.Entrant(X, DV, DAYS[0], DAYS[3], {})})   # 該日側檔無列 → 不補
    assert X not in m3.stocks and m3.breadth == {} and m3.industry == {} and m3.p_cs == {}


def test_prune_bundles_drops_stale_sidefiles(world, tmp_path):
    repo = tmp_path / "repo"
    shutil.copytree(world["repo"], repo)
    DC.write_entrant(repo, DC.Entrant("2412", DV, DAYS[-5], DAYS[-2], {}))     # to 仍 ≥ 未來的最舊包 → 留
    r = DP.prune_bundles(repo, keep=10, window=WINDOW)
    assert r["deleted"] == len(DAYS) - 10 and r["first"] == DAYS[-10]
    assert r["entrants_deleted"] == 2 and sorted(s for s, _ in DC.list_entrants(repo)) == ["2412"]   # X（to=E−1）與 1103（to=K）皆早於最舊包
    assert DP.prune_bundles(repo, keep=10, window=WINDOW)["entrants_deleted"] == 0
    assert DP.prune_bundles(repo, keep=500, window=WINDOW) == {"deleted": 0, "kept": 10, "first": DAYS[-10], "entrants_deleted": 0}


def test_adv_adopt_and_state_chain(world):
    """狀態鏈採用重算的成交值 deque：E 當日 X 由狀態鏈**無**變成與參考路徑同一組值；之後逐日 push 與參考同步（終點相等）。"""
    t = AdvTracker(window=3)
    t.push_day(DAYS[0], {"a": 1.0})
    t.adopt("b", [5.0, 6.0, 7.0, 8.0])
    assert t.history_of("b") == [6.0, 7.0, 8.0] and t.history_of("a") == [1.0] and t.history_of("zz") == []
    assert t.eligible() == frozenset({"b"}) if 7.0 >= t.threshold else t.eligible() == frozenset()
    st = json.loads((world["repo"] / DC.STATE_FILE).read_text(encoding="utf-8"))
    ref = json.loads((world["cache_x"] / "scores.db.state.json").read_text(encoding="utf-8"))
    assert st["adv"]["amt"][X] == ref["adv"]["amt"][X]                # 60 日成交值 deque 與含 X 全程重播終點逐位相同
    assert st["stock_line2"] == ref["stock_line2"] and st["market_line2"] == ref["market_line2"]


def test_production_window_covers_scanner_and_adv_lookback():
    """生產 `WINDOW_N`（320）當側檔回看：X 的 scanner deque 滿（61）＋大盤二爻最長回看（騰落線 2n−1＝119）＝180 ≤ 320，
    ADV 60 ≤ 320——所以 E 當日大盤列與 X 列的輸入都與參考相同；合成世界 window 30 不滿足，測試才另給 `--entrants-window`。"""
    scan_maxlen = PC.SCAN_MAXLEN
    breadth_lookback = max(2 * MKT_L2_WIN[h][3] - 1 for h in MKT_L2_WIN)
    assert scan_maxlen == 61 and breadth_lookback == 119
    assert RS.WINDOW_N >= (scan_maxlen - 1) + breadth_lookback and RS.WINDOW_N >= ADV_WINDOW
    assert WINDOW < scan_maxlen                                          # 合成世界的 window 不夠，故本檔用 EW
