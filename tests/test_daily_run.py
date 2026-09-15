"""每日班端到端（D-2b：`daily_fetch`＋`daily_pipeline`＋`scripts/daily_run.py`＋`daily.yml`）——假 FinMind／假官方端點由合成 SQLite 供給。

驗收（`docs/P2-DAILY-PLAN.md` §7.4.2）：①原料包位元組＝`ReplaySource.read_day`、分數與參考 `scores.db` 0 差異、狀態鏈自接；
②waiting 路徑；③補跑與 `--max-days`；④pool 不變不改寫／除權息追加／基本面合併與參考相等；⑤日曆追加；⑥workflow 欄位。
"""
from __future__ import annotations

import json
import shutil
import sqlite3
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import daily_run as DR  # noqa: E402
import diff_scores as DS  # noqa: E402
import export_seed as ES  # noqa: E402
import replay_scores as RP  # noqa: E402
import scan_features as SF  # noqa: E402
from iching import bundle_io as B  # noqa: E402
from iching import calendar as CAL  # noqa: E402
from iching import daily_core as DC  # noqa: E402
from iching import daily_fetch as DF  # noqa: E402
from iching import daily_pipeline as DP  # noqa: E402
from iching import replay_io as RIO  # noqa: E402
from iching.fm import TransientError  # noqa: E402
from iching.scores_io import ScoreStore  # noqa: E402
from iching.store import Store  # noqa: E402
from synth_db import DAYS, DV, build_full  # noqa: E402

WINDOW, K = 30, 60
META_COLS = {"row_hash", "cov_key", "data_version", "extra"}
TABLES = {
    "TaiwanStockPrice": [("prices", "raw_index_price"), ("prices", "raw_price_daily")],
    "TaiwanStockInfo": [("universe", "raw_stock_info")],
    "TaiwanStockDividendResult": [("prices", "raw_dividend_result")],
    "TaiwanStockInstitutionalInvestorsBuySell": [("chips", "raw_inst_buysell")],
    "TaiwanStockMarginPurchaseShortSale": [("chips", "raw_margin")],
    "TaiwanStockShareholding": [("chips", "raw_shareholding")],
    "TaiwanDailyShortSaleBalances": [("chips", "raw_short_sale_balance")],
    "TaiwanStockTotalMarginPurchaseShortSale": [("market", "raw_total_margin")],
    "TaiwanFuturesInstitutionalInvestors": [("market", "raw_futures_inst")],
    "TaiwanFuturesDaily": [("market", "raw_futures_daily")],
    "TaiwanOptionVix": [("market", "raw_vix")],
    "USStockPrice": [("market", "raw_us_index")],
    "TaiwanExchangeRate": [("market", "raw_fx_usd")],
    "TaiwanStockMonthRevenue": [("fundamentals", "raw_month_revenue")],
    "TaiwanStockFinancialStatements": [("fundamentals", "raw_financial_statements")],
}
OFFICIAL_TABLES = {"BFI82U": ("raw_twse_bfi82u", "day"), "FMTQIK": ("raw_twse_fmtqik", "month"),
                   "insti/summary": ("raw_tpex_inst_summary", "day"), "tradingIndex": ("raw_tpex_trading_index", "month")}
# 合成 DB 刻意沒有借券／集保／TPEx 官方兩表 → 這些不列核心
REQUIRED = tuple(x for x in DF.CORE_REQUIRED if x not in ("shareholding", "short_sale", "official_inst:tpex", "official_amount:tpex"))


class FakeFM:
    """`get(dataset, **params)`：從合成 raw_* 表依 data_id／日期區間取列，回 FinMind 原欄名 dict。`blackout` 模擬未落地。"""

    def __init__(self, cache: Path) -> None:
        self.cache = cache
        self.blackout: set[tuple[str, str]] = set()
        self.extra_info: list[dict] = []
        self.hide_info: set[str] = set()                 # TaiwanStockInfo 快照暫時**不含**這些代號（模擬尚未入池／已出池）
        self.fail_data_ids: set[str] = set()             # 帶 data_id 的 per-stock 查詢對這些代號丟例外（模擬抓取失敗）
        self.calls: list[tuple[str, dict]] = []

    def _rows(self, db: str, table: str) -> list[dict]:
        con = sqlite3.connect(f"file:{self.cache / (db + '.db')}?mode=ro", uri=True)
        try:
            if not con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone():
                return []
            con.row_factory = sqlite3.Row
            return [{k: v for k, v in dict(r).items() if k not in META_COLS} for r in con.execute(f'SELECT * FROM "{table}"')]
        finally:
            con.close()

    def get(self, dataset: str, **params):
        self.calls.append((dataset, dict(params)))
        did, s, e = params.get("data_id"), params.get("start_date"), params.get("end_date")
        if did is not None and did in self.fail_data_ids:
            raise TransientError(f"{dataset} {did}: simulated failure")
        out = []
        for db, table in TABLES[dataset]:
            for r in self._rows(db, table):
                if did is not None and did not in (r.get("stock_id"), r.get("futures_id"), r.get("currency")):
                    continue
                d = r.get("date")
                if s is not None and (d is None or str(d) < s):
                    continue
                if e is not None and (d is None or str(d) > e):
                    continue
                if (dataset, str(d)) in self.blackout:
                    continue
                out.append(r)
        if dataset == "TaiwanStockInfo":
            out += list(self.extra_info)
            out = [r for r in out if r.get("stock_id") not in self.hide_info]
        return out


class FakeOC:
    def __init__(self, cache: Path) -> None:
        self.cache = cache

    def get(self, url: str, params: dict):
        for frag, (table, kind) in OFFICIAL_TABLES.items():
            if frag in url:
                break
        else:
            raise AssertionError(url)
        if kind == "day":
            raw = params.get("dayDate") or params.get("date")
            key = f"{raw[:4]}-{raw[4:6]}-{raw[6:8]}" if "-" not in raw and "/" not in raw else raw.replace("/", "-")
            where, val = "date=?", key
        else:
            raw = params["date"].replace("/", "")
            where, val = "month=?", raw[:6]
        con = sqlite3.connect(f"file:{self.cache / 'market.db'}?mode=ro", uri=True)
        try:
            if not con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone():
                return 404, None, ""
            row = con.execute(f'SELECT body FROM "{table}" WHERE {where}', (val,)).fetchone()
        finally:
            con.close()
        if row is None:
            return 404, None, ""
        return 200, json.loads(row[0]), row[0]


def fetcher_for(cache: Path, fm: FakeFM | None = None, **kw) -> DF.Fetcher:
    kw.setdefault("price_min_rows", 1)                                   # 合成 DB 每日只有 7 列，真實門檻 1500 只在生產用
    return DF.Fetcher(fm or FakeFM(cache), FakeOC(cache), required=REQUIRED, **kw)


@pytest.fixture(scope="module")
def world(tmp_path_factory) -> dict:
    base = tmp_path_factory.mktemp("dailyrun")
    cache, repo = base / "cache", base / "repo"
    build_full(cache)
    # 參考層「事先知道」的除權息事件：2330 在 DAYS[K+2] 除息（每日班要在當天才抓到並追加）
    with Store(cache / "prices.db") as p:
        p.record_success("dividend_result", "raw_dividend_result", "2330:2020",
                         [{"date": DAYS[K + 2], "stock_id": "2330", "before_price": 400.0, "after_price": 396.0}], DV, "TaiwanStockDividendResult")
    assert SF.main(["--cache-dir", str(cache), "--quiet", "--allow-short-warmup", "--warmup-days", "0"]) == 0
    assert RP.main(["--cache-dir", str(cache), "--window", str(WINDOW), "--to", DAYS[K], "--quiet"]) == 0
    state_k = base / "state_k.json"
    shutil.copy(cache / "scores.db.state.json", state_k)
    assert RP.main(["--cache-dir", str(cache), "--window", str(WINDOW), "--resume", "--quiet"]) == 0
    src = RIO.ReplaySource(cache, DV, window=WINDOW)
    dates = src.trading_dates()
    CAL.write_calendar_json(repo / DC.CALENDAR_TPE_FILE, CAL.calendar_payload("tpe", dates, DV))
    us_dates = sorted({d for d, *_ in src.read_day(DAYS[K]).us})     # 種子前的美股日
    CAL.write_calendar_json(repo / DP.CALENDAR_US_FILE, CAL.calendar_payload("us", us_dates, DV))
    src.close()
    assert ES.main(["--cache-dir", str(cache), "--out", str(repo), "--window", str(WINDOW), "--state", str(state_k)]) == 0
    # 模擬「種子匯出時還不知道」：把 K+2 的除息列與 2330 的 2020-03 月營收列從種子拿掉
    fpath = repo / DC.FACTORS_FILE
    fd = json.loads(fpath.read_text(encoding="utf-8"))
    fd["rows"] = [r for r in fd["rows"] if not (r[0] == "2330" and r[1] == DAYS[K + 2])]
    DC.write_json(fpath, fd)
    upath = repo / DC.FUND_FILE
    ud = json.loads(upath.read_text(encoding="utf-8"))
    ud["monthly"]["2330"] = [r for r in ud["monthly"]["2330"] if r[:2] != [2020, 3]]
    DC.write_json(upath, ud)
    shutil.copytree(repo, base / "repo_seed")                          # 其他測試用的乾淨種子
    return {"cache": cache, "repo": repo, "seed": base / "repo_seed", "state_k": state_k}


def _run(repo: Path, cache: Path, T: str, fm: FakeFM | None = None, extra: list[str] = (), **kw) -> int:
    return DR.main(["--root", str(repo), "--date", T, "--window", str(WINDOW), *extra], fetcher=fetcher_for(cache, fm, **kw))


def test_chain_end_to_end_bitwise(world):
    repo, cache = world["repo"], world["cache"]
    ref = ScoreStore(cache / "scores.db", readonly=True)
    got = ScoreStore(cache.parent / "daily.db")
    src = RIO.ReplaySource(cache, DV, window=WINDOW)
    for d in DAYS[: K + 1]:
        src.read_day(d)                                                # 讓參考 ReplaySource 的美股／匯率游標走到 K
    pool_bytes = (repo / DC.POOL_FILE).read_bytes()
    try:
        got.set_params(DV, ref.params_of(DV))
        # 第一次跑 --date K+2：狀態在 K → 一次補 K+1、K+2（順序）；之後逐日
        fm0 = FakeFM(cache)
        assert _run(repo, cache, DAYS[K + 2], fm0) == 0
        assert sorted(p.name for p in (repo / DC.SCORES_DIR).iterdir()) == [f"{DAYS[K + 1]}.json", f"{DAYS[K + 2]}.json"]
        # 月營收查詢：每個補跑日 d 的本月窗＝`[月首, d]`（部分窗、end_date 不在未來），上月為整月窗；起點一律月首
        mr_calls = [p for d_, p in fm0.calls if d_ == "TaiwanStockMonthRevenue"]
        assert mr_calls == [q for d_ in (DAYS[K + 1], DAYS[K + 2])
                            for q in ({"start_date": "2020-03-01", "end_date": "2020-03-31"}, {"start_date": "2020-04-01", "end_date": d_})]
        # 種子刻意拿掉的 2330 2020-03 月營收（date=2020-04-01＝本月 1 日公布）：T 當班就併進 fundamentals.json
        fj = json.loads((repo / DC.FUND_FILE).read_text(encoding="utf-8"))
        assert [2020, 3, 5e9] in fj["monthly"]["2330"]
        for i in range(K + 3, len(DAYS)):
            assert _run(repo, cache, DAYS[i]) == 0
        for i in range(K + 1, len(DAYS)):
            T = DAYS[i]
            refb = src.read_day(T)
            assert B.bundle_path(repo, T).read_bytes() == B.write_bundle(cache.parent / "refb", refb).read_bytes(), T
            js = json.loads((repo / DC.SCORES_DIR / f"{T}.json").read_text(encoding="utf-8"))
            rows = [(r["model_version"], {k: v for k, v in r.items() if k != "model_version"}) for r in js["rows"]]
            got.write_day(DV, T, rows, js["diag"])
            n_common, n_diff, msgs = DS.diff_day(ref, got, DV, T)
            assert n_diff == 0 and n_common == len(rows) > 0, (T, msgs[:5])
        # 狀態鏈終點＝參考
        st_ref = json.loads((cache / "scores.db.state.json").read_text(encoding="utf-8"))
        st_got = json.loads((repo / DC.STATE_FILE).read_text(encoding="utf-8"))
        st_ref.pop("meta"), st_got.pop("meta")
        assert st_got == st_ref
    finally:
        ref.close()
        got.close()
        src.close()
    # ④ pool 不變 → 位元組不變；除權息追加後＝參考 load_factors；基本面合併後＝參考
    assert (repo / DC.POOL_FILE).read_bytes() == pool_bytes
    src = RIO.ReplaySource(cache, DV, window=WINDOW)
    _, factors, fstat = DC.load_factors_file(repo / DC.FACTORS_FILE)
    assert factors == src.factors and fstat == src.factor_stats and "2330" in factors
    cal = DC.load_calendar_dates(repo / DC.CALENDAR_TPE_FILE)
    assert cal == src.trading_dates() == DAYS                          # ⑤ 台北日曆逐日追加到終點
    _, pool = DC.load_pool_file(repo / DC.POOL_FILE)
    _, bridge = DC.load_fundamentals_file(repo / DC.FUND_FILE, pool, cal)
    refbr = src.load_fundamentals(cal)
    for sid in refbr.stocks:
        assert bridge.inputs_for(sid, DAYS[-1]) == refbr.inputs_for(sid, DAYS[-1]), sid
    assert [2020, 3] in [r[:2] for r in json.loads((repo / DC.FUND_FILE).read_text(encoding="utf-8"))["monthly"]["2330"]]
    # 法定期限 2020-04-10 起的班次用得到它（as-of 可用日＝期限日起首個交易日），與參考同
    assert ("2020-03", 5e9) in bridge.inputs_for("2330", DAYS[-1])["monthly_revenue"]
    assert ("2020-03", 5e9) not in (bridge.inputs_for("2330", "2020-04-09")["monthly_revenue"] or [])
    us_cal = DC.load_calendar_dates(repo / DP.CALENDAR_US_FILE)
    assert us_cal[-1] == DAYS[-1] and us_cal == sorted(set(us_cal))
    src.close()
    assert not list((repo / B.BUNDLE_DIR).glob("*-waiting.json"))
    # 再跑一次同日＝no-op、rc 0、不改任何檔
    before = {p: p.read_bytes() for p in (repo / "data").rglob("*.json")}
    assert _run(repo, cache, DAYS[-1]) == 0
    assert {p: p.read_bytes() for p in (repo / "data").rglob("*.json")} == before


def test_waiting_when_core_dataset_missing(world, tmp_path):
    repo = tmp_path / "repo"
    shutil.copytree(world["seed"], repo)
    cache = world["cache"]
    fm = FakeFM(cache)
    T = DAYS[K + 1]
    fm.blackout.add(("TaiwanOptionVix", T))
    before = {p: p.read_bytes() for p in repo.rglob("*") if p.is_file()}
    assert _run(repo, cache, T, fm) == 0
    w = DP.waiting_path(repo, T)
    assert w.exists()
    wj = json.loads(w.read_text(encoding="utf-8"))
    assert wj["date"] == T and wj["missing"] == ["vix"]
    assert not B.bundle_path(repo, T).exists() and not (repo / DC.SCORES_DIR).exists()
    after = {p: p.read_bytes() for p in repo.rglob("*") if p.is_file()}
    assert {p for p in after if p != w} == set(before) and all(after[p] == before[p] for p in before if p.name != "pool.json")
    # 補齊後再叫一次：正常、waiting 刪除
    fm.blackout.clear()
    assert _run(repo, cache, T, fm) == 0
    assert not w.exists() and B.bundle_path(repo, T).exists() and (repo / DC.SCORES_DIR / f"{T}.json").exists()


def test_catch_up_limit_and_pool_change(world, tmp_path):
    repo = tmp_path / "repo"
    shutil.copytree(world["seed"], repo)
    cache = world["cache"]
    # 狀態在 K、要跑到 K+3＝3 個待補日 > --max-days 1 → 只跑 K+1、其餘留下次（rc 0）；pool 變動同批驗
    fm = FakeFM(cache)
    fm.extra_info = [{"stock_id": "2412", "type": "twse", "industry_category": "通信網路業", "stock_name": "新", "date": "2026-09-11"}]
    summary = DP.run_pipeline(repo, fetcher_for(cache, fm), upto=DAYS[K + 3], window=WINDOW, max_days=1, log=lambda *_: None)
    assert summary["status"] == "ok" and [x["date"] for x in summary["done"]] == [DAYS[K + 1]]
    assert summary["remaining"] == [DAYS[K + 2], DAYS[K + 3]]
    assert sorted(p.name for p in (repo / DC.SCORES_DIR).iterdir()) == [f"{DAYS[K + 1]}.json"]
    assert json.loads((repo / DC.STATE_FILE).read_text(encoding="utf-8"))["last_date"] == DAYS[K + 1]
    # pool 變動（TaiwanStockInfo 多一檔）→ 改寫 pool.json 且新檔入池
    _, pool = DC.load_pool_file(repo / DC.POOL_FILE)
    assert "2412" in pool
    # 下一次再叫：把剩下兩日補完
    assert _run(repo, cache, DAYS[K + 3], fm, extra=["--max-days", "5"]) == 0
    assert sorted(p.name for p in (repo / DC.SCORES_DIR).iterdir()) == [f"{DAYS[K + i]}.json" for i in (1, 2, 3)]
    # 已處理到 K+1 再叫同日：trading_days_since 為空 → no-op rc 0（週末／假日同一條路）
    assert DR.main(["--root", str(repo), "--date", DAYS[K + 1], "--window", str(WINDOW)], fetcher=fetcher_for(cache, fm)) == 0


def test_daily_workflow_yaml():
    d = yaml.safe_load((ROOT / ".github/workflows/daily.yml").read_text(encoding="utf-8"))
    on = d.get("on") or d.get(True)
    assert list(on) == ["workflow_dispatch"] and "schedule" not in on
    assert d["concurrency"] == {"group": "iching-commit", "cancel-in-progress": False}
    assert d["permissions"] == {"contents": "write", "issues": "write"}
    steps = d["jobs"]["daily"]["steps"]
    run_step = next(s for s in steps if "daily_run.py" in (s.get("run") or ""))
    assert "FINMIND_TOKEN" in run_step["env"] and "secrets.FINMIND_TOKEN" in run_step["env"]["FINMIND_TOKEN"]
    assert run_step["run"].count("--tpex-no-verify") == 2                  # run #2：runner CA 缺 TPEx 中繼憑證
    notify = steps[-1]
    assert notify["uses"] == "./.github/actions/notify-failure" and notify["with"]["pipeline"] == "iching-daily"
    assert notify["if"] == "failure() || cancelled()"
    commit = next(s for s in steps if "git add data runs/collect" in (s.get("run") or ""))
    assert "pull --rebase" in commit["run"]


def test_truncation_guard_writes_waiting(world, tmp_path):
    """上游截斷（200 但只回幾列；回補層 2026-09-10 事故）：列數低於門檻或池覆蓋不足 → `stocks` 列缺 → waiting、不寫包不推進狀態。"""
    repo = tmp_path / "repo"
    shutil.copytree(world["seed"], repo)
    cache = world["cache"]
    T = DAYS[K + 1]
    assert _run(repo, cache, T, price_min_rows=1500) == 0                 # 生產門檻：合成切片 7 列 → 截斷
    w = DP.waiting_path(repo, T)
    assert w.exists() and json.loads(w.read_text(encoding="utf-8"))["missing"] == ["stocks"]
    assert not B.bundle_path(repo, T).exists()
    assert json.loads((repo / DC.STATE_FILE).read_text(encoding="utf-8"))["last_date"] == DAYS[K]
    # 池覆蓋率：只回 1 檔（1/5 < 0.5）也算截斷
    fm = FakeFM(cache)
    orig = fm.get

    def one_stock(dataset, **params):
        rows = orig(dataset, **params)
        return [r for r in rows if r.get("stock_id") == "1101"] if dataset == "TaiwanStockPrice" and "data_id" not in params else rows
    fm.get = one_stock
    assert _run(repo, cache, T, fm, price_min_rows=1) == 0
    assert json.loads(w.read_text(encoding="utf-8"))["missing"] == ["stocks"]
    # 正常門檻下通過、waiting 刪除
    assert _run(repo, cache, T, price_min_rows=1) == 0
    assert not w.exists() and B.bundle_path(repo, T).exists()


def test_price_at_period_end_filled_per_stock_period(world, tmp_path):
    """同一期別 A 先申報、B 隔日申報：B 的期末收盤也要補到（以 (檔, 期別) 計缺，不是以期別計；2026-09-14 驗收抓到）。"""
    repo = tmp_path / "repo"
    shutil.copytree(world["seed"], repo)
    P = "2020-03-31"                                                      # 合法期別（月為 3/6/9/12），種子原料包涵蓋 ≤P
    row = lambda sid: {"stock_id": sid, "date": P, "type": "EPS", "value": 1.0}  # noqa: E731
    DP.update_fundamentals(repo, [], [row("1101")], DV)
    d1 = json.loads((repo / DC.FUND_FILE).read_text(encoding="utf-8"))
    assert P in d1["price_at_period_end"]["1101"] and P not in d1["price_at_period_end"].get("2330", {})
    DP.update_fundamentals(repo, [], [row("2330")], DV)
    d2 = json.loads((repo / DC.FUND_FILE).read_text(encoding="utf-8"))
    assert P in d2["price_at_period_end"]["2330"]                        # 隔日申報者補到
    # 值＝全市場 ≤P 最近原料包日、該檔原始 close（與 replay_io.load_fundamentals 規則同）
    files = [(d, p) for d, p in B.list_bundles(repo) if d <= P]
    ref = B.read_bundle(files[-1][1]).stocks["2330"]["close"]
    assert d2["price_at_period_end"]["2330"][P] == ref


def test_pool_rewritten_only_when_derived_pool_changes(world, tmp_path):
    """TaiwanStockInfo 的 `date` 每天＝抓取日（run #3：3,313 列全改寫、池零變動）→ 不算變動；產業／成員變才改寫。"""
    repo = tmp_path / "repo"
    shutil.copytree(world["seed"], repo)
    cache = world["cache"]
    fm = FakeFM(cache)
    base_rows = fm.get("TaiwanStockInfo")
    before = (repo / DC.POOL_FILE).read_bytes()
    dated = [dict(r, date="2030-01-01") for r in base_rows]
    changed, pool = DP.update_pool(repo, dated, DV)
    assert changed is False and (repo / DC.POOL_FILE).read_bytes() == before and set(pool) == set(DC.load_pool_file(repo / DC.POOL_FILE)[1])
    reclass = [dict(r, industry_category="半導體業") if r["stock_id"] == "1101" else r for r in base_rows]
    changed, pool = DP.update_pool(repo, reclass, DV)
    assert changed is True and pool["1101"]["industry_category"] == "半導體業" and (repo / DC.POOL_FILE).read_bytes() != before
    gone = [r for r in base_rows if r["stock_id"] != "2330"]
    changed, pool = DP.update_pool(repo, gone, DV)
    assert changed is True and "2330" not in pool


def test_prune_bundles_keeps_window_rings_identical(world, tmp_path):
    """修剪到最近 keep 份後，WindowCache 的個股 ring／市場輸入（含美股／匯率序列）與未修剪逐位相同；第一份包承載整段美股／匯率。"""
    import numpy as np
    from iching import replay_state as RS
    full = world["seed"]
    pruned = tmp_path / "pruned"
    shutil.copytree(full, pruned)
    all_files = B.list_bundles(full)
    cut = all_files[-40][0]
    us_dates = {x[0] for _, p in all_files for x in B.read_bundle(p).us if x[0] <= cut}
    r = DP.prune_bundles(pruned, keep=40, window=WINDOW)
    files = B.list_bundles(pruned)
    assert r["deleted"] == len(all_files) - 40 and len(files) == 40 and files[0][0] == cut
    assert r["first_us"] == min(WINDOW, len(us_dates)) and r["first_fx"] == r["first_us"]      # 合成 DB 美股日＝台北日，≤cut 只有 22 個
    assert DP.prune_bundles(pruned, keep=40, window=WINDOW)["deleted"] == 0             # 冪等
    _, pool = DC.load_pool_file(full / DC.POOL_FILE)
    _, factors, _ = DC.load_factors_file(full / DC.FACTORS_FILE)
    wa, _, _ = DC.rebuild_from_bundles(B.list_bundles(full), pool, factors, data_version=DV, window=WINDOW)
    wb, _, _ = DC.rebuild_from_bundles(files, pool, factors, data_version=DV, window=WINDOW)
    T = files[-1][0]
    cross = RS.CrossDayState()
    for sid in wa.stock_ids_today():
        assert np.array_equal(wa.stock_window(sid), wb.stock_window(sid), equal_nan=True), sid
    for m in ("twse", "tpex"):
        a, b = wa.market_inputs(m, T, cross), wb.market_inputs(m, T, cross)
        assert a.us_dates == b.us_dates and a.fx_dates == b.fx_dates and len(a.us_dates) == WINDOW   # T 的 ring 仍滿（後段包補足）
        for k in ("index_close", "amount", "spx_close", "sox_close", "fx_usdtwd", "vix", "foreign_net_oi", "margin_balance", "n_stocks"):
            x, y = getattr(a, k), getattr(b, k)
            assert (x is None) == (y is None), (m, k)
            if x is not None:
                assert np.array_equal(np.asarray(x), np.asarray(y), equal_nan=True), (m, k)
    # 美股／匯率游標仍找得到（新最舊包帶整段）
    assert DP.last_dated(pruned) == DP.last_dated(full)
    # keep 小於剩餘美股日：真的走到 [-window:] 截斷，且兩條 ring（美股／匯率）仍與未修剪逐位相同（stock ring 因 keep<WINDOW 本就不同、不比）
    pruned2 = tmp_path / "pruned2"
    shutil.copytree(full, pruned2)
    r2 = DP.prune_bundles(pruned2, keep=20, window=WINDOW)
    assert r2["first_us"] == WINDOW and r2["first_fx"] == WINDOW
    wc2, _, _ = DC.rebuild_from_bundles(B.list_bundles(pruned2), pool, factors, data_version=DV, window=WINDOW)
    for m in ("twse", "tpex"):
        a, b = wa.market_inputs(m, T, cross), wc2.market_inputs(m, T, cross)
        assert a.us_dates == b.us_dates and a.fx_dates == b.fx_dates
        for k in ("spx_close", "sox_close", "fx_usdtwd"):
            assert np.array_equal(np.asarray(getattr(a, k)), np.asarray(getattr(b, k)), equal_nan=True), (m, k)
    # 舊檔 schema 對但缺 rows → update_pool 改寫而非 crash
    bad = tmp_path / "badpool"
    shutil.copytree(full, bad)
    (bad / DC.POOL_FILE).write_text('{"schema": 1, "data_version": "x"}', encoding="utf-8")
    fm = FakeFM(world["cache"])
    changed, pool2 = DP.update_pool(bad, fm.get("TaiwanStockInfo"), DV)
    assert changed is True and pool2 == pool


def test_fundamentals_query_windows_are_period_aligned():
    """2026-09-14 Hetzner 實測：全市場查詢視窗起點必須對齊期別（月首／單一期末日），跨月跨季回 0；
    2026-09-15 Hetzner 實測：起點為月首的部分窗 `2026-09-01～2026-09-14` 回列 → 本月窗改 `[月首, T]`（與回補層本月部分塊同形）。"""
    assert DF.month_windows("2026-09-14", 2) == [("2026-08-01", "2026-08-31"), ("2026-09-01", "2026-09-14")]
    assert DF.month_windows("2026-01-05", 2) == [("2025-12-01", "2025-12-31"), ("2026-01-01", "2026-01-05")]
    assert DF.month_windows("2024-03-10", 1) == [("2024-03-01", "2024-03-10")]
    assert DF.month_windows("2026-09-01", 2) == [("2026-08-01", "2026-08-31"), ("2026-09-01", "2026-09-01")]   # 月首當天＝單日窗
    assert DF.month_windows("2026-09-30", 2) == [("2026-08-01", "2026-08-31"), ("2026-09-01", "2026-09-30")]   # 月末當天＝整月窗
    assert DF.month_windows("2026-09-14", 3) == [("2026-07-01", "2026-07-31"), ("2026-08-01", "2026-08-31"), ("2026-09-01", "2026-09-14")]
    assert DF.month_windows("2026-09-14", 0) == []
    for T_ in ("2026-09-14", "2026-01-05", "2024-02-29", "2025-12-31"):
        ws = DF.month_windows(T_, 3)
        assert all(s_.endswith("-01") for s_, _ in ws) and ws[-1][1] == T_ and all(e_ <= T_ for _, e_ in ws) and ws == sorted(ws)
    assert DF.quarter_ends("2026-09-14", 2) == ["2026-03-31", "2026-06-30"]
    assert DF.quarter_ends("2026-01-05", 2) == ["2025-09-30", "2025-12-31"]
    assert DF.quarter_ends("2026-10-01", 2) == ["2026-06-30", "2026-09-30"]
    assert DF.quarter_ends("2024-02-29", 1) == ["2023-12-31"]
    # fetch_day 實際送出的查詢形狀：月營收＝上月整月窗＋本月 [月首, T]、季報兩個 start=end=期末日
    cache_calls = []

    class Spy:
        def get(self, dataset, **params):
            cache_calls.append((dataset, params))
            return []
    f = DF.Fetcher(Spy(), None, required=())
    f._official_body = lambda key, pk: None                                  # 不打官方端點
    f.fetch_day("2026-09-14", {"2330": {}}, last_us="2026-09-11", last_fx="2026-09-11")
    mr = [p for d, p in cache_calls if d == "TaiwanStockMonthRevenue"]
    fs_ = [p for d, p in cache_calls if d == "TaiwanStockFinancialStatements"]
    assert mr == [{"start_date": "2026-08-01", "end_date": "2026-08-31"}, {"start_date": "2026-09-01", "end_date": "2026-09-14"}]
    assert fs_ == [{"start_date": "2026-03-31", "end_date": "2026-03-31"}, {"start_date": "2026-06-30", "end_date": "2026-06-30"}]
    assert all("data_id" not in p for p in mr + fs_)


def test_update_fundamentals_merges_current_month_revenue_and_prunes_oldest(world, tmp_path):
    """本月公布的上月營收（date=本月 1 日）併入 `data/fundamentals.json`：(sid, y, m) 新鍵追加、既有鍵覆蓋、`revenue` 為 None 的列不計；
    `prune_fundamentals` 以每檔最新月往前 `FUND_MONTHS_KEEP` 保留 → 已滿 24 個月的檔再加一個月就掉最舊一個月、**列數不變**
    （main `b8e4f69` 實況：種子 47,698 列 → run #5 併入 1,964 檔 2026-08 後 47,825 列，淨 +127＝未滿 24 月的檔）。
    月營收不需期末收盤（`px_pairs` 只對季報期別）。"""
    repo = tmp_path / "repo"
    shutil.copytree(world["seed"], repo)
    path = repo / DC.FUND_FILE
    before = json.loads(path.read_text(encoding="utf-8"))
    rows = before["monthly"]["1101"]
    assert 0 < len(rows) < DC.FUND_MONTHS_KEEP                                         # 合成世界 1101 為 2019-01 起 16 月

    def ym_after(y: int, m: int, k: int) -> tuple[int, int]:
        idx = y * 12 + (m - 1) + k
        return idx // 12, idx % 12 + 1

    def row(sid: str, y: int, m: int, v) -> dict:
        return {"stock_id": sid, "revenue_year": y, "revenue_month": m, "revenue": v, "date": f"{ym_after(y, m, 1)[0]:04d}-{ym_after(y, m, 1)[1]:02d}-01"}
    ly, lm = rows[-1][0], rows[-1][1]
    ny, nm = ym_after(ly, lm, 1)
    base = DP.update_fundamentals(repo, [], [], DV)                                   # 空輸入＝不寫檔；px_pairs 為種子裡本就無價的
    assert base["changed"] == 0                                                        # (檔, 期別)（§7.4.4「每日白讀」那批），當基準
    # ① 未滿 24 月：新月追加＝+1 列；revenue None 不計不寫；季報／期末收盤不動；月營收不新增任何缺價 (檔, 期別)
    st = DP.update_fundamentals(repo, [row("1101", ny, nm, 123.0), row("9999", ny, nm, None)], [], DV)
    assert st["monthly_rows"] == 1 and st["changed"] == 1
    assert (st["px_pairs"], st["new_periods"]) == (base["px_pairs"], base["new_periods"])
    after = json.loads(path.read_text(encoding="utf-8"))
    assert after["monthly"]["1101"] == rows + [[ny, nm, 123.0]] and "9999" not in after["monthly"]
    assert after["quarters"] == before["quarters"] and after["price_at_period_end"] == before["price_at_period_end"]
    # ② 同鍵再來（值改）＝覆蓋；內容不變的第三次＝不寫檔
    assert DP.update_fundamentals(repo, [row("1101", ny, nm, 456.0)], [], DV)["changed"] == 1
    assert json.loads(path.read_text(encoding="utf-8"))["monthly"]["1101"][-1] == [ny, nm, 456.0]
    assert DP.update_fundamentals(repo, [row("1101", ny, nm, 456.0)], [], DV)["changed"] == 0
    # ③ 補到恰 24 月後再加一個月：掉最舊、列數仍 24（這就是生產上「加 1,964 檔卻只淨 +127 列」的機制）
    fill = [row("1101", *ym_after(ny, nm, k), 1.0) for k in range(1, DC.FUND_MONTHS_KEEP - len(rows))]
    DP.update_fundamentals(repo, fill, [], DV)
    full = json.loads(path.read_text(encoding="utf-8"))["monthly"]["1101"]
    assert len(full) == DC.FUND_MONTHS_KEEP
    fy, fm_ = ym_after(full[-1][0], full[-1][1], 1)
    DP.update_fundamentals(repo, [row("1101", fy, fm_, 2.0)], [], DV)
    got = json.loads(path.read_text(encoding="utf-8"))["monthly"]["1101"]
    assert len(got) == DC.FUND_MONTHS_KEEP and got == full[1:] + [[fy, fm_, 2.0]]
