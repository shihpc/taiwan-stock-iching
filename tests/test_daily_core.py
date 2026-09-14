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
    latest = max(full)
    for off, n in ((0, 1), (0, 3), (3, 3)):
        assert revenue_yoy_3m(full, latest, off, n) == revenue_yoy_3m(kept, latest, off, n)
    assert revenue_is_12m_high(full, latest, 12) == revenue_is_12m_high(kept, latest, 12)
    # 空值與非 NEEDED_TYPES 被濾掉；沒有任何列的檔不出現
    d2 = DC.prune_fundamentals({"x": [[2020, 1, None]]}, {"x": [["2020-03-31", "IncomeAfterTaxes", 1.0]]}, {})
    assert d2["monthly"] == {} and d2["quarters"] == {}
