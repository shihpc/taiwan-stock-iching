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


def fetcher_for(cache: Path, fm: FakeFM | None = None) -> DF.Fetcher:
    return DF.Fetcher(fm or FakeFM(cache), FakeOC(cache), required=REQUIRED)


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


def _run(repo: Path, cache: Path, T: str, fm: FakeFM | None = None, extra: list[str] = ()) -> int:
    return DR.main(["--root", str(repo), "--date", T, "--window", str(WINDOW), *extra], fetcher=fetcher_for(cache, fm))


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
        assert _run(repo, cache, DAYS[K + 2]) == 0
        assert sorted(p.name for p in (repo / DC.SCORES_DIR).iterdir()) == [f"{DAYS[K + 1]}.json", f"{DAYS[K + 2]}.json"]
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
    # 狀態在 K、要跑到 K+3＝3 個待補日 > --max-days 1 → rc 2、不寫任何檔
    before = {p: p.read_bytes() for p in repo.rglob("*") if p.is_file()}
    assert _run(repo, cache, DAYS[K + 3], extra=["--max-days", "1"]) == 2
    assert {p: p.read_bytes() for p in repo.rglob("*") if p.is_file()} == before
    # pool 變動（TaiwanStockInfo 多一檔）→ 改寫 pool.json 且新檔入池；分數照算
    fm = FakeFM(cache)
    fm.extra_info = [{"stock_id": "2412", "type": "twse", "industry_category": "通信網路業", "stock_name": "新", "date": "2026-09-11"}]
    assert _run(repo, cache, DAYS[K + 1], fm) == 0
    _, pool = DC.load_pool_file(repo / DC.POOL_FILE)
    assert "2412" in pool and (repo / DC.SCORES_DIR / f"{DAYS[K + 1]}.json").exists()
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
    notify = steps[-1]
    assert notify["uses"] == "./.github/actions/notify-failure" and notify["with"]["pipeline"] == "iching-daily"
    assert notify["if"] == "failure() || cancelled()"
    commit = next(s for s in steps if "git add data runs/collect" in (s.get("run") or ""))
    assert "pull --rebase" in commit["run"]
