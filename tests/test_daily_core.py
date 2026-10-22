"""每日班離線核心（D-2a，`iching.daily_core`＋`scripts/export_seed.py`）：**兩層 parity 的合成 DB 證明**。

參考路徑（Hetzner）：`replay_scores.py` 全量 → `scores.db`。每日班路徑：第 k 日 `export_seed` 匯出種子（pool／factors／
fundamentals／狀態／原料包）→ 之後每一日只用 repo 內檔案 `run_offline`（狀態鏈由前一日每日班產出接續）→ 每日 rows 與
`ScoreStore.rows_for_day` 逐位相同（走 `diff_scores.diff_day` 同一支比對器）。另驗三檔讀回等價、保留期不改分數、T 當日排名池斷言的失敗路徑。
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
import replay_scores as RP  # noqa: E402
import scan_features as SF  # noqa: E402
from iching import bundle_io as B  # noqa: E402
from iching import calendar as CAL  # noqa: E402
from iching import daily_core as DC  # noqa: E402
from iching import feed as FEED  # noqa: E402
from iching.features_io import FeatureStore  # noqa: E402
from iching import replay_io as RIO  # noqa: E402
from iching.score.stock import revenue_is_12m_high, revenue_yoy_3m  # noqa: E402
from iching.scores_io import ScoreStore  # noqa: E402
from synth_db import DAYS, DV, build_full  # noqa: E402

WINDOW = 30
K = 60                                   # 種子日索引：DAYS[K] 為快照 last_date，之後 19 日走每日班


@pytest.fixture(scope="module")
def world(tmp_path_factory) -> dict:
    base = tmp_path_factory.mktemp("daily")
    cache, repo = base / "cache", base / "repo"
    build_full(cache)
    assert SF.main(["--cache-dir", str(cache), "--quiet", "--allow-short-warmup", "--warmup-days", "0"]) == 0
    # 參考：跑到第 K 日存快照 → 複製一份當種子 → 續跑到底
    assert RP.main(["--cache-dir", str(cache), "--window", str(WINDOW), "--to", DAYS[K], "--quiet"]) == 0
    state_k = base / "state_k.json"
    shutil.copy(cache / "scores.db.state.json", state_k)
    assert RP.main(["--cache-dir", str(cache), "--window", str(WINDOW), "--resume", "--quiet"]) == 0
    # repo：日曆（＝原料交易日）＋種子
    src = RIO.ReplaySource(cache, DV, window=WINDOW)
    dates = src.trading_dates()
    CAL.write_calendar_json(repo / DC.CALENDAR_TPE_FILE, CAL.calendar_payload("tpe", dates, DV))
    src.close()
    assert ES.main(["--cache-dir", str(cache), "--out", str(repo), "--window", str(WINDOW), "--state", str(state_k)]) == 0
    return {"cache": cache, "repo": repo, "state_k": state_k}


def test_seed_files_roundtrip_equal_feed_loaders(world):
    repo, cache = world["repo"], world["cache"]
    src = RIO.ReplaySource(cache, DV, window=WINDOW)
    _, pool = DC.load_pool_file(repo / DC.POOL_FILE)
    _, factors, fstat = DC.load_factors_file(repo / DC.FACTORS_FILE)
    assert pool == src.pool and factors == src.factors and fstat == src.factor_stats
    cal = DC.load_calendar_dates(repo / DC.CALENDAR_TPE_FILE)
    assert cal == src.trading_dates()
    ref = src.load_fundamentals(cal)
    _, got = DC.load_fundamentals_file(repo / DC.FUND_FILE, pool, cal)
    assert set(got.stocks) == set(ref.stocks)
    for sid in ref.stocks:
        assert got.inputs_for(sid, DAYS[-1]) == ref.inputs_for(sid, DAYS[-1]), sid
    assert got.coverage(DAYS[-1]) == ref.coverage(DAYS[-1])
    # 種子原料包 ≤ K 且與 ReplaySource 讀出逐位相同
    files = B.list_bundles(repo)
    assert files and files[-1][0] == DAYS[K] and files[0][0] == DAYS[0]      # 合成 80 日不足 window+ADV+1，起點退到第一天
    src2 = RIO.ReplaySource(cache, DV, window=WINDOW)
    for d, p in files[:3]:
        assert p.read_bytes() == B.write_bundle(world["cache"].parent / "tmpb", src2.read_day(d)).read_bytes()
    src2.close()
    src.close()
    # 狀態快照帶 data_version、與快照 K 同 last_date
    st = json.loads((repo / DC.STATE_FILE).read_text(encoding="utf-8"))
    assert st["last_date"] == DAYS[K] and st["meta"]["data_version"] == DV and st["meta"]["window"] == WINDOW


def _feed_bundle(cache: Path, repo: Path, upto_index: int) -> None:
    """模擬 D-2b 的抓取：以一個游標連續的 ReplaySource 產出 DAYS[K+1..upto] 的原料包（美股／匯率只帶增量）。"""
    src = RIO.ReplaySource(cache, DV, window=WINDOW)
    for d in DAYS[: upto_index + 1]:
        b = src.read_day(d)
        if d > DAYS[K]:
            B.write_bundle(repo, b)
    src.close()


def test_daily_chain_bitwise_equals_reference(world):
    repo, cache = world["repo"], world["cache"]
    ref = ScoreStore(cache / "scores.db", readonly=True)
    out_db = cache.parent / "daily.db"
    got = ScoreStore(out_db)
    try:
        params = ref.params_of(DV)
        got.set_params(DV, params)
        n_days = 0
        for i in range(K + 1, len(DAYS)):
            T = DAYS[i]
            _feed_bundle(cache, repo, i)
            summary = DC.run_offline(repo, T, window=WINDOW)
            assert [x["date"] for x in summary["days"]] == [T]
            assert summary["params_sha"] == params_sha_of(ref)
            js = json.loads((repo / DC.SCORES_DIR / f"{T}.json").read_text(encoding="utf-8"))
            assert js["schema"] == 1 and js["tpe_date"] == T and js["data_version"] == DV
            rows = [(r["model_version"], {k: v for k, v in r.items() if k != "model_version"}) for r in js["rows"]]
            got.write_day(DV, T, rows, js["diag"])
            n_common, n_diff, msgs = DS.diff_day(ref, got, DV, T)
            assert n_diff == 0 and n_common == len(rows) > 0, (T, msgs[:5])
            dd_ref, dd_got = ref.day_diag(DV, T), got.day_diag(DV, T)
            for k in ("model_version_twse", "model_version_tpex", "n_market_rows", "n_stocks", "n_in_pool", "n_stock_rows",
                      "n_stock_any_unknown", "n_market_any_unknown", "index_missing"):
                assert dd_ref[k] == dd_got[k], (T, k)
            n_days += 1
        assert n_days == len(DAYS) - K - 1
        # 狀態鏈終點與參考快照逐位相同（meta 除外：每日班多記 data_version）
        st_ref = json.loads((cache / "scores.db.state.json").read_text(encoding="utf-8"))
        st_got = json.loads((repo / DC.STATE_FILE).read_text(encoding="utf-8"))
        st_ref.pop("meta"), st_got.pop("meta")
        assert st_got == st_ref
        # 再叫一次：沒有待計分日＝no-op
        assert DC.run_offline(repo, window=WINDOW)["days"] == []
    finally:
        ref.close()
        got.close()


def _features_of(fs: FeatureStore, dates: list[str]) -> dict:
    return {(d, m, k): getattr(fs, f"day_{k}")(DV, m, d) for d in dates for m in ("twse", "tpex") for k in ("breadth", "industry", "p_cs")}


def test_daily_features_equal_reference_features_db(world, tmp_path, monkeypatch):
    """§7.0 第 2 點的**直接證據**：每日班逐日算出的 breadth／industry／P_cs 與參考 `features.db` 用同一組讀取函式讀出逐位相同
    （`P_cs` 只進 `lr.meta`、不進 scores 欄，分數 diff 看不到它——2026-09-14 驗收指出，故獨立驗）。並自證守門活著：
    把排名池換成空集合後，P_cs 必須不同。"""
    repo, cache = world["repo"], world["cache"]
    _feed_bundle(cache, repo, len(DAYS) - 1)
    _, pool = DC.load_pool_file(repo / DC.POOL_FILE)
    _, factors, _ = DC.load_factors_file(repo / DC.FACTORS_FILE)
    bundles = B.list_bundles(repo)
    assert [d for d, _ in bundles] == DAYS
    ref = FeatureStore(cache / "features.db", readonly=True)
    mine = FeatureStore(tmp_path / "daily_features.db")
    try:
        DC.rebuild_from_bundles(bundles, pool, factors, data_version=DV, window=WINDOW, features=mine)
        a, b = _features_of(ref, DAYS), _features_of(mine, DAYS)
        assert a == b
        assert any(v for (d, m, k), v in a.items() if k == "p_cs") and any(v for (d, m, k), v in a.items() if k == "industry")
        assert all(a[(d, m, "breadth")] is not None for d in DAYS for m in ("twse", "tpex"))
    finally:
        ref.close()
        mine.close()
    # 守門自證：排名池清空 → P_cs 消失 → 不再相等
    orig = FEED.day_records

    def no_pool(*args, **kw):
        kw["rank_pool"] = frozenset()
        return orig(*args, **kw)
    monkeypatch.setattr(FEED, "day_records", no_pool)
    broken = FeatureStore(tmp_path / "broken_features.db")
    ref = FeatureStore(cache / "features.db", readonly=True)
    try:
        DC.rebuild_from_bundles(bundles, pool, factors, data_version=DV, window=WINDOW, features=broken)
        a, b = _features_of(ref, DAYS), _features_of(broken, DAYS)
        assert a != b and all(not b[(d, m, "p_cs")] for d in DAYS for m in ("twse", "tpex"))
    finally:
        ref.close()
        broken.close()


def params_sha_of(store: ScoreStore) -> str:
    return store.conn.execute("SELECT params_sha FROM replay_meta WHERE data_version=?", (DV,)).fetchone()[0]


def test_pool_assertion_rejects_short_seed(world, tmp_path):
    """T 當日排名池必須與狀態快照相等：只留最近 20 份原料包（不足 ADV 60 日）→ 拒算、不落任何檔。"""
    repo = world["repo"]
    short = tmp_path / "short"
    shutil.copytree(repo, short)
    files = B.list_bundles(short)
    for _, p in files[:-20]:
        p.unlink()
    state = (short / DC.STATE_FILE).read_text(encoding="utf-8")
    st = json.loads(state)
    # 把快照倒回 DAYS[K]，讓 DAYS[K+1] 變成待計分（鏈上 scores 檔先清掉）
    shutil.rmtree(short / DC.SCORES_DIR, ignore_errors=True)
    src_state = json.loads(world["state_k"].read_text(encoding="utf-8"))
    src_state["meta"] = st["meta"]
    (short / DC.STATE_FILE).write_text(json.dumps(src_state), encoding="utf-8")
    with pytest.raises(DC.DailyCoreError, match="排名池不一致"):
        DC.run_offline(short, DAYS[K + 1], window=WINDOW)
    assert not (short / DC.SCORES_DIR).exists()
    assert json.loads((short / DC.STATE_FILE).read_text(encoding="utf-8"))["last_date"] == DAYS[K]
    # 其他拒絕：不是待計分日／window 不符
    with pytest.raises(DC.DailyCoreError, match="不是待計分日"):
        DC.run_offline(repo, DAYS[K], window=WINDOW)
    with pytest.raises(Exception, match="meta"):
        DC.run_offline(repo, window=WINDOW + 1)


def test_prune_keeps_engine_lookbacks_intact():
    """保留期（24 個月／8 期）不改變引擎用到的任何月營收算式；期末收盤只留保留期內期別。"""
    monthly = {"1101": [[2020 + k // 12, k % 12 + 1, 1e8 * (1 + 0.01 * k)] for k in range(60)]}     # 2020-01 … 2024-12
    quarters = {"1101": [[f"{y}-{m:02d}-{'31' if m in (3, 12) else '30'}", "EPS", 1.0] for y in range(2019, 2025) for m in (3, 6, 9, 12)]}
    px = {"1101": {p: 10.0 for p, _, _ in quarters["1101"]}}
    d = DC.prune_fundamentals(monthly, quarters, px)
    assert len(d["monthly"]["1101"]) == 24 and d["monthly"]["1101"][0][:2] == [2023, 1]
    assert len(d["quarters"]["1101"]) == 8 and d["quarters"]["1101"][0][0] == "2023-03-31"
    assert set(d["price_at_period_end"]["1101"]) == {p for p, _, _ in d["quarters"]["1101"]}
    full = {f"{y:04d}-{m:02d}": v for y, m, v in monthly["1101"]}
    kept = {f"{y:04d}-{m:02d}": v for y, m, v in d["monthly"]["1101"]}
    # as-of T 的 latest 可落後檔案最新月（尚未可得）：0～3 個月 lag 全部算式相同且非缺值
    for lag in range(0, 4):
        latest = sorted(full)[-1 - lag]
        for off, n in ((0, 1), (0, 3), (3, 3)):
            x, y = revenue_yoy_3m(full, latest, off, n), revenue_yoy_3m(kept, latest, off, n)
            assert x == y and isinstance(x, float), (lag, off, n)
        x, y = revenue_is_12m_high(full, latest, 12), revenue_is_12m_high(kept, latest, 12)
        assert x == y and isinstance(x, bool), lag
    # 空值與非 NEEDED_TYPES 被濾掉；沒有任何列的檔不出現
    d2 = DC.prune_fundamentals({"x": [[2020, 1, None]]}, {"x": [["2020-03-31", "IncomeAfterTaxes", 1.0]]}, {})
    assert d2["monthly"] == {} and d2["quarters"] == {}


def test_factors_file_requires_matching_sources_and_keeps_four_columns(world, tmp_path):
    """裁定 #51：`factors.json` 頂層 `sources`＝`adjust.ADJUST_SOURCES`，缺或不同一律拒讀（舊檔作廢、要求重匯種子）；
    每列仍 4 欄、`schema` 不 bump；`factors_payload` 收 5 欄列（合併輸出）只寫前 4 欄。"""
    from iching.adjust import ADJUST_SOURCES
    src_path = world["repo"] / DC.FACTORS_FILE
    d = json.loads(src_path.read_text(encoding="utf-8"))
    assert d["schema"] == DC.FILE_SCHEMA == 1 and d["sources"] == ADJUST_SOURCES == "div+capred+split+par-1"
    assert all(len(r) == 4 for r in d["rows"])
    p = tmp_path / "factors.json"
    DC.write_json(p, {k: v for k, v in d.items() if k != "sources"})
    with pytest.raises(DC.DailyCoreError, match="sources=None"):
        DC.load_factors_file(p)
    DC.write_json(p, {**d, "sources": "div-0"})
    with pytest.raises(DC.DailyCoreError, match="重匯種子"):
        DC.load_factors_file(p)
    pay = DC.factors_payload([("1101", "2020-03-20", 100.0, 80.0, "dividend"), ("1101", "2020-03-20", 100.0, 200.0, "capred"),
                              ("2330", None, 1.0, 1.0)], DV)
    assert pay == {"schema": 1, "data_version": DV, "sources": ADJUST_SOURCES,
                   "rows": [["1101", "2020-03-20", 100.0, 80.0], ["1101", "2020-03-20", 100.0, 200.0]]}
    DC.write_json(p, pay)
    _, fac, st = DC.load_factors_file(p)
    assert fac["1101"] == (["2020-03-20"], [pytest.approx(0.625)]) and st["rows"] == 2      # 同鍵兩列＝兩個事件相乘，不去重


# ---------------------------------------------------------------------------
# 上市轉換守門（PR-B，docs/P2-DAILY-PLAN.md §7.8.10）
def test_transition_suspects_criteria():
    """判準兩條：①轉換生效日落在 (最舊持有包, T]；②快照同一日跨市場平手。轉換早於區間／恆定市場／同日同市場（產業重分類）不算。"""
    rows = [
        {"stock_id": "1001", "type": "emerging", "industry_category": "x", "stock_name": "a", "date": "2026-10-20"},
        {"stock_id": "1001", "type": "twse", "industry_category": "x", "stock_name": "a", "date": "2026-10-21"},   # ① 10-21 生效
        {"stock_id": "1002", "type": "emerging", "industry_category": "x", "stock_name": "b", "date": "2026-10-20"},
        {"stock_id": "1002", "type": "twse", "industry_category": "x", "stock_name": "b", "date": "2026-10-20"},   # ② 同日平手
        {"stock_id": "1003", "type": "twse", "industry_category": "x", "stock_name": "c", "date": "2026-10-21"},   # 恆定
        {"stock_id": "1004", "type": "tpex", "industry_category": "x", "stock_name": "d", "date": "2020-01-01"},
        {"stock_id": "1004", "type": "twse", "industry_category": "x", "stock_name": "d", "date": "2026-10-21"},   # 轉換早於區間
        {"stock_id": "1005", "type": "twse", "industry_category": "x", "stock_name": "e", "date": "2026-10-21"},
        {"stock_id": "1005", "type": "twse", "industry_category": "y", "stock_name": "e", "date": "2026-10-21"},   # 同日同市場
        {"stock_id": "1006", "type": "tpex", "industry_category": "x", "stock_name": "f", "date": "2026-10-19"},
        {"stock_id": "1006", "type": "twse", "industry_category": "x", "stock_name": "f", "date": "2026-10-21"},   # 10-20 生效＝區間內
    ]
    pool = DC.PitPool.from_snapshot_rows(rows)
    held = ["2026-09-01", "2026-10-19", "2026-10-20", "2026-10-21"]
    assert DC.transition_suspects(rows, pool, held, "2026-10-21") == frozenset({"1001", "1002", "1006"})
    # 生效日＝最舊持有包當天：區間內市場恆定 → 不算；生效日晚於 T → 不算
    assert "1006" not in DC.transition_suspects(rows, pool, ["2026-10-20", "2026-10-21"], "2026-10-21")
    assert "1001" not in DC.transition_suspects(rows, pool, held[:2], "2026-10-19")


def _guard_world(world, tmp_path: Path, name: str) -> tuple[Path, str]:
    """把 world 的 repo 複製一份、狀態倒回 DAYS[K]（同 `test_pool_assertion_rejects_short_seed`），回 (repo, X)：
    X＝快照排名池內、只有一列 TaiwanStockInfo 的代號。"""
    repo = tmp_path / name
    shutil.copytree(world["repo"], repo)
    shutil.rmtree(repo / DC.SCORES_DIR, ignore_errors=True)
    st = json.loads((repo / DC.STATE_FILE).read_text(encoding="utf-8"))
    src_state = json.loads(world["state_k"].read_text(encoding="utf-8"))
    src_state["meta"] = st["meta"]
    (repo / DC.STATE_FILE).write_text(json.dumps(src_state), encoding="utf-8")
    for d, p in B.list_bundles(repo):
        if d > DAYS[K + 1]:
            p.unlink()
    if not B.bundle_path(repo, DAYS[K + 1]).exists():                     # 單獨跑本測試（chain 測試沒先餵包）
        _feed_bundle(world["cache"], repo, K + 1)
    cross = DC.load_state(repo / DC.STATE_FILE)
    rows = json.loads((repo / DC.POOL_FILE).read_text(encoding="utf-8"))["rows"]
    n = {}
    for r in rows:
        n[r["stock_id"]] = n.get(r["stock_id"], 0) + 1
    x = sorted(s for s in cross.adv.eligible() if n.get(s) == 1)[0]
    return repo, x


def _relist(repo: Path, x: str, em_date: str = DAYS[K]) -> None:
    """改寫 pool.json：X 的興櫃列日期 `em_date`、上市列日期 DAYS[K+1]——預設＝DAYS[K] 以前興櫃、DAYS[K+1] 起上市（＝6467 的 10-21 快照形狀）；
    `em_date` 較早時轉換生效日落在區間中段（T 前已有部分在池日 → 新池重算歷史非空）。"""
    d = json.loads((repo / DC.POOL_FILE).read_text(encoding="utf-8"))
    for r in d["rows"]:
        if r["stock_id"] == x:
            r["date"] = DAYS[K + 1]
            em = dict(r, type="emerging", date=em_date)
    d["rows"].append(em)
    DC.write_json(repo / DC.POOL_FILE, DC.pool_payload(d["rows"], d["data_version"]))


def test_guard_resets_transition_and_equals_manual_clean(world, tmp_path):
    """A1：池轉換表變動造成的不一致 → 不拒算、記 `pool_transition_reset`、重設該檔，結果與「#138 手工清法（狀態鏈先手動清掉該檔）」逐位相同。"""
    g, x = _guard_world(world, tmp_path, "guard")
    m, x2 = _guard_world(world, tmp_path, "manual")
    assert x == x2
    for r in (g, m):
        _relist(r, x)
    # 守門前的行為（pool_reset_ok 空）＝拒算：證明這是真的會中止的情境
    _, pool = DC.load_pool_file(g / DC.POOL_FILE)
    _, factors, _ = DC.load_factors_file(g / DC.FACTORS_FILE)
    cross = DC.load_state(g / DC.STATE_FILE)
    with pytest.raises(DC.DailyCoreError, match=f"只在快照 \\['{x}'\\]"):
        DC.rebuild_from_bundles(B.list_bundles(g), pool, factors, data_version=DV, window=WINDOW,
                                expect_pool_at=DAYS[K + 1], expect_pool=cross.adv.eligible())
    # 手工清法：adv／二爻／遲滯三處去掉 X
    cm = DC.load_state(m / DC.STATE_FILE)
    cm.adv.drop(x)
    for dct in (cm.stock_line2, cm.stock_lines):
        for k in [k for k in dct if k.startswith(f"{x}|")]:
            del dct[k]
    DC.save_state(m / DC.STATE_FILE, cm)
    sm = DC.run_offline(m, DAYS[K + 1], window=WINDOW)
    sg = DC.run_offline(g, DAYS[K + 1], window=WINDOW)
    assert sm["days"][0]["pool_transition_reset"] == []
    assert sg["days"][0]["pool_transition_reset"] == [x] and sg["days"][0]["rebuild"]["pool_transition_reset"] == [x]
    for f in (DC.STATE_FILE, f"{DC.SCORES_DIR}/{DAYS[K + 1]}.json"):
        a, b = json.loads((g / f).read_text(encoding="utf-8")), json.loads((m / f).read_text(encoding="utf-8"))
        if "diag" in a:
            a["diag"].pop("elapsed_ms", None), b["diag"].pop("elapsed_ms", None)
        assert a == b, f
    # 重設後 X 的 ADV 從 T 起算（新池下 T 前不在池）
    assert len(DC.load_state(g / DC.STATE_FILE).adv.history_of(x)) <= 1


def test_guard_still_rejects_non_transition_mismatch(world, tmp_path):
    """A2：與池轉換表無關的不一致（狀態鏈多一檔滿窗 ADV、該檔轉換表恆定、快照無平手）→ 照舊 DailyCoreError、不落檔。"""
    r, _x = _guard_world(world, tmp_path, "reject")
    cross = DC.load_state(r / DC.STATE_FILE)
    _, pool = DC.load_pool_file(r / DC.POOL_FILE)
    y = sorted(s for s in pool.listed_ids(DAYS[K + 1]) if s not in cross.adv.eligible())[0]
    rows = json.loads((r / DC.POOL_FILE).read_text(encoding="utf-8"))["rows"]
    held = [d for d, _ in B.list_bundles(r)]
    assert y not in DC.transition_suspects(rows, pool, held, DAYS[K + 1])
    cross.adv.adopt(y, [1e12] * cross.adv.window)
    DC.save_state(r / DC.STATE_FILE, cross)
    with pytest.raises(DC.DailyCoreError, match=f"只在快照 \\['{y}'\\]"):
        DC.run_offline(r, DAYS[K + 1], window=WINDOW)
    assert not (r / DC.SCORES_DIR).exists()
    # 同時有一檔可歸因、一檔不可歸因：仍拒算（守門不吞掉其他原因）
    _relist(r, _x)
    with pytest.raises(DC.DailyCoreError, match=f"只在快照 \\['{y}'\\]"):
        DC.run_offline(r, DAYS[K + 1], window=WINDOW)


def test_guard_rejects_suspect_with_nonempty_history(world, tmp_path):
    """收窄（約定 7）：嫌疑檔但新池重算歷史非空（轉換日落在區間中段，T 前已有部分在池日）→ 不重設、拒算交人工，不落任何檔；
    同一檔改成 T 前完全不在池（歷史為空）→ 重設放行。"""
    r, x = _guard_world(world, tmp_path, "nonempty")
    _relist(r, x, em_date=DAYS[K - 10])
    rows = json.loads((r / DC.POOL_FILE).read_text(encoding="utf-8"))["rows"]
    _, pool = DC.load_pool_file(r / DC.POOL_FILE)
    assert x in DC.transition_suspects(rows, pool, [d for d, _ in B.list_bundles(r)], DAYS[K + 1])
    with pytest.raises(DC.DailyCoreError, match=f"\\['{x}'\\] 上市轉換嫌疑但重算歷史非空，需人工處理"):
        DC.run_offline(r, DAYS[K + 1], window=WINDOW)
    assert not (r / DC.SCORES_DIR).exists()
    assert json.loads((r / DC.STATE_FILE).read_text(encoding="utf-8"))["last_date"] == DAYS[K]
    _relist(r, x)                                                         # 興櫃列改回 DAYS[K]：T 前不在池 → 歷史為空 → 重設
    s = DC.run_offline(r, DAYS[K + 1], window=WINDOW)
    assert s["days"][0]["pool_transition_reset"] == [x]
