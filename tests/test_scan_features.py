"""`scripts/scan_features.py` ＋ `iching.features_io`：逐日掃描落地 `features.db`。

合成 DB **實跑整支腳本**（不是 mock）。守三類無聲錯誤：
**PIT 呼叫順序**（反了不會報錯）、**落地值與掃描器不一致**、**暖機不足卻照跑**。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import scan_features as S  # noqa: E402
from iching import feed as F  # noqa: E402
from iching.features_io import FeatureStore, FeatureStoreError, params_fingerprint  # noqa: E402
from iching.liquidity import AdvTracker  # noqa: E402
from iching.scan import DailyScanner  # noqa: E402
from synth_db import DAYS, DV, build  # noqa: E402

SCALE = 1e6          # 讓成交值跨過 0.3 億門檻，排名池才不會永遠是空的（p_cs 路徑才測得到）


@pytest.fixture(scope="module")
def ran(tmp_path_factory):
    cache = tmp_path_factory.mktemp("scanfeat") / "cache"
    build(cache, amount_scale=SCALE)
    out = cache / "features.db"
    rc = S.main(["--cache-dir", str(cache), "--out", str(out), "--quiet"])
    assert rc == 0
    return cache, out


def test_landed_shape_and_completeness(ran):
    _, out = ran
    with FeatureStore(out) as fs:
        assert fs.dates(DV) == DAYS
        c = fs.counts(DV)
        assert c["market_breadth"] == 2 * len(DAYS)              # 兩市場 × 80 日
        assert c["scan_day"] == len(DAYS)
        assert c["p_cs"] > 0, "排名池全空 → p_cs 路徑等於沒測到"
        assert fs.missing_dates(DV, DAYS) == []
        assert fs.params_of(DV)["p_cs_tie"] == "mid"


def test_landed_values_match_the_scanner(ran):
    """落地的每一個計數都必須等於直接跑 `DailyScanner` 的值——寫入層不得偷偷變形。"""
    cache, out = ran
    prices, uni = F.open_ro(cache / "prices.db"), F.open_ro(cache / "universe.db")
    try:
        pool = F.load_pool(uni)
        factors, _ = F.load_factors(prices, DV)
        idx = F.load_index(prices, DV)
        sc, adv = DailyScanner(), AdvTracker()
        expect, scanstate = {}, {}
        for d, rows in F.iter_days(prices, DV):
            rp = adv.eligible()
            recs, amounts = F.day_records(d, rows, pool, factors, rank_pool=rp)
            expect[d] = sc.push_day(d, recs, idx.get(d, {}))
            scanstate[d] = (len(rp), adv.n_tracked, adv.n_ready)   # ← 取值時點必須在 push 之前
            adv.push_day(d, amounts)
    finally:
        prices.close()
        uni.close()
    with FeatureStore(out) as fs:
        for d in (DAYS[0], DAYS[40], DAYS[-1]):
            for mk, b in expect[d].breadth.items():
                row = fs.breadth_row(DV, mk, d)
                assert row is not None, (mk, d)
                for f in ("n_stocks", "advance_count", "decline_count", "unchanged_count",
                          "ret_eligible", "ad_line"):
                    assert row[f] == getattr(b, f), (d, mk, f)
                for f in ("amount_up", "amount_ret_eligible", "amount_total"):
                    assert row[f] == pytest.approx(getattr(b, f)), (d, mk, f)
                got = fs.window_rows(DV, mk, d, "above_ma")
                assert got == {w: (cnt, b.ma_eligible[w]) for w, cnt in b.above_ma_count.items()}
                lo = fs.window_rows(DV, mk, d, "new_low")
                assert lo == {w: (cnt, b.hl_eligible[w]) for w, cnt in b.new_low_count.items()}
                hi = fs.window_rows(DV, mk, d, "new_high")
                assert hi == {w: (cnt, b.hl_eligible[w]) for w, cnt in b.new_high_count.items()}
                assert row["index_missing"] == int(mk in expect[d].index_missing)
            # 產業均線廣度整張表（驗收指出它的值原本零覆蓋——兩欄對調也全綠）
            got_ib = {(m, ind, w): (cnt, el) for m, ind, w, cnt, el in fs.conn.execute(
                "SELECT market, industry, window, above_ma_count, ma_eligible FROM industry_breadth_window "
                "WHERE data_version=? AND date=?", (DV, d))}
            want_ib = {(x.market, x.industry, w): (cnt, x.ma_eligible[w])
                       for x in expect[d].industry_breadth for w, cnt in x.above_ma_count.items()}
            assert got_ib == want_ib, d
            assert {(x.market, x.industry): x.n_stocks for x in expect[d].industry_breadth} == {
                (m, i): n for m, i, n in fs.conn.execute(
                    "SELECT market, industry, n_stocks FROM industry_breadth WHERE data_version=? AND date=?",
                    (DV, d))}
            # p_cs 的 excess 欄（原本零覆蓋——寫成 0.0 也全綠）
            got_ex = {(m, w, sid): ex for m, w, sid, ex in fs.conn.execute(
                "SELECT market, window, stock_id, excess FROM p_cs WHERE data_version=? AND date=?", (DV, d))}
            want_ex = {(mk, w, sid): expect[d].excess[(mk, w)][sid]
                       for (mk, w), per in expect[d].p_cs.items() for sid in per}
            assert got_ex == pytest.approx(want_ex), d
            # scan_day 的三個診斷數字（驗收指出 adv_tracked／adv_ready 原本零覆蓋）
            assert fs.conn.execute(
                "SELECT rank_pool_size, adv_tracked, adv_ready FROM scan_day WHERE data_version=? AND date=?",
                (DV, d)).fetchone() == scanstate[d], d


def test_pit_call_order_rank_pool_reflects_t_minus_1(ran):
    """`scan_day.rank_pool_size` 必須是「到 T−1 為止」算出來的。

    **反過來（先 push 再 eligible）不會報錯**，只會讓整條序列往前位移一天。這條測試獨立重算
    兩種順序的序列，斷言落地的是 PIT 那一條、且兩條真的不同（否則等於沒分辨力）。
    """
    cache, out = ran
    prices, uni = F.open_ro(cache / "prices.db"), F.open_ro(cache / "universe.db")
    try:
        pool = F.load_pool(uni)
        factors, _ = F.load_factors(prices, DV)
        days = [(d, rows) for d, rows in F.iter_days(prices, DV)]
    finally:
        prices.close()
        uni.close()

    def sizes(pit: bool):
        adv, out_ = AdvTracker(), []
        for d, rows in days:
            _, amounts = F.day_records(d, rows, pool, factors)
            if pit:
                out_.append(len(adv.eligible()))
                adv.push_day(d, amounts)
            else:
                adv.push_day(d, amounts)          # look-ahead：先把 T 日算進去
                out_.append(len(adv.eligible()))
        return out_

    pit, lookahead = sizes(True), sizes(False)
    assert pit != lookahead, "兩種順序結果相同 → 這份合成資料分辨不出 PIT，測試沒有意義"
    with FeatureStore(out) as fs:
        landed = [r[0] for r in fs.conn.execute(
            "SELECT rank_pool_size FROM scan_day WHERE data_version=? ORDER BY date", (DV,))]
    assert landed == pit, "落地的排名池不是 PIT——先 push 再 eligible 就是 look-ahead"
    assert landed != lookahead


def test_rerun_is_idempotent_and_resume_skips(ran):
    cache, out = ran
    with FeatureStore(out) as fs:
        before = fs.counts(DV)
    assert S.main(["--cache-dir", str(cache), "--out", str(out), "--quiet"]) == 0
    with FeatureStore(out) as fs:
        assert fs.counts(DV) == before          # INSERT OR REPLACE → 重跑不長列
    assert S.main(["--cache-dir", str(cache), "--out", str(out), "--quiet", "--resume"]) == 0
    with FeatureStore(out) as fs:
        assert fs.counts(DV) == before


def test_params_mismatch_is_refused_not_silently_mixed(tmp_path):
    """同一個 `data_version` 用不同參數寫過就拒絕——混進兩批口徑不同的列，兩層 parity 是假的。"""
    fs = FeatureStore(tmp_path / "f.db")
    try:
        a = {"ma_windows": [5, 20], "p_cs_tie": "mid"}
        sha = fs.set_params(DV, a)
        assert sha == params_fingerprint(a) == fs.set_params(DV, dict(reversed(list(a.items()))))  # 不認順序
        with pytest.raises(FeatureStoreError, match="已用不同參數寫過"):
            fs.set_params(DV, {"ma_windows": [5, 20], "p_cs_tie": "low"})
        assert fs.params_of(DV) == a            # 被拒的那次不得留下任何痕跡
        fs.clear(DV)
        assert fs.params_of(DV) is None
        assert fs.set_params(DV, {"ma_windows": [5, 20], "p_cs_tie": "low"})   # clear 後可換參數
    finally:
        fs.close()


def test_rebuild_clears_then_writes(ran):
    cache, out = ran
    with FeatureStore(out) as fs:
        before = fs.counts(DV)
    assert S.main(["--cache-dir", str(cache), "--out", str(out), "--quiet", "--rebuild"]) == 0
    with FeatureStore(out) as fs:
        assert fs.counts(DV) == before          # 砍掉重寫，列數應回到一樣


def test_short_warmup_is_refused_by_default(ran):
    """`--from` 不暖機會讓開頭那段算在半滿視窗上，數字看起來正常、**不會報錯**——所以預設拒跑。"""
    cache, _ = ran
    out = cache / "warm.db"
    rc = S.main(["--cache-dir", str(cache), "--out", str(out), "--quiet",
                 "--from", DAYS[70], "--warmup-days", "5"])
    assert rc == 2
    rc = S.main(["--cache-dir", str(cache), "--out", str(out), "--quiet",
                 "--from", DAYS[70], "--warmup-days", "5", "--allow-short-warmup"])
    assert rc == 0
    with FeatureStore(out) as fs:
        assert fs.dates(DV) == DAYS[70:]        # 暖機那幾日只掃不寫
    assert S.WARMUP_MIN == 119                  # ＝2×60−1，騰落線長度要求（見模組 docstring）


def test_missing_dates_detects_holes(tmp_path):
    with FeatureStore(tmp_path / "f.db") as fs:
        fs.set_params(DV, {"x": 1})
        fs.conn.execute("INSERT INTO scan_day VALUES(?,?,?,?,?,?)", (DV, "2020-01-02", 0, 0, 0, ""))
        assert fs.missing_dates(DV, ["2020-01-02", "2020-01-03"]) == ["2020-01-03"]
        assert fs.missing_dates(DV, ["2020-01-02"]) == []


def test_loud_failure_on_missing_db(tmp_path, capsys):
    assert S.main(["--cache-dir", str(tmp_path / "沒有"), "--quiet"]) == 2
    assert "scan 中止" in capsys.readouterr().err


def test_rewriting_a_day_updates_every_table(tmp_path):
    """同一日重寫必須**覆蓋**（`INSERT OR REPLACE`），不能保留舊列。

    這不是假想情境：`store.record_success()` 允許同一個 `data_version` 下修正上游列
    （回補重跑／`verify` 修復），修完重掃特徵時舊值必須被蓋掉。
    突變測試抓到的漏洞——把 `p_cs` 寫成 `INSERT OR IGNORE` 時，原本九支測試**全綠**，
    因為它們只跑「同樣的輸入重跑一次」，值本來就一樣、蓋不蓋掉看不出來。
    """
    from iching.scan import IndustryAgg, IndustryBreadth, MarketBreadth, ScanDay

    def day(bump: int) -> ScanDay:
        b = MarketBreadth(market="twse", tpe_date="2020-01-02", n_stocks=10 + bump,
                          above_ma_count={5: 1 + bump}, ma_eligible={5: 9},
                          new_high_count={10: 2 + bump}, new_low_count={10: 3},
                          hl_eligible={10: 8}, advance_count=4 + bump, decline_count=5,
                          unchanged_count=1, ret_eligible=9, ad_line=7 + bump,
                          amount_up=1.0 + bump, amount_ret_eligible=2.0, amount_total=3.0)
        return ScanDay(tpe_date="2020-01-02", breadth={"twse": b},
                       industry=[IndustryAgg("twse", "2020-01-02", 10, "水泥", 5, 1.5 + bump)],
                       industry_breadth=[IndustryBreadth("twse", "2020-01-02", "水泥", 5 + bump,
                                                         {5: 2 + bump}, {5: 4})],
                       excess={("twse", 10): {"1101": 0.5 + bump}},
                       p_cs={("twse", 10): {"1101": 60.0 + bump}})

    with FeatureStore(tmp_path / "f.db") as fs:
        fs.set_params(DV, {"x": 1})
        first = fs.write_day(day(0), DV, rank_pool_size=1, adv_tracked=1, adv_ready=1)
        second = fs.write_day(day(1), DV, rank_pool_size=2, adv_tracked=2, adv_ready=2)
        assert first == second                                   # 列數不變＝覆蓋而非追加
        assert fs.counts(DV)["p_cs"] == 1
        assert fs.breadth_row(DV, "twse", "2020-01-02")["n_stocks"] == 11
        assert fs.window_rows(DV, "twse", "2020-01-02", "above_ma") == {5: (2, 9)}
        assert fs.window_rows(DV, "twse", "2020-01-02", "new_high") == {10: (3, 8)}
        got = dict(fs.conn.execute("SELECT stock_id, p_cs FROM p_cs WHERE data_version=?", (DV,)))
        assert got == {"1101": 61.0}, "p_cs 沒被覆蓋——舊值留著會讓修復過的上游算出陳舊特徵"
        assert dict(fs.conn.execute(
            "SELECT industry, median_ret FROM industry_agg WHERE data_version=?", (DV,))) == {"水泥": 2.5}
        assert dict(fs.conn.execute(
            "SELECT industry, n_stocks FROM industry_breadth WHERE data_version=?", (DV,))) == {"水泥": 6}
        assert dict(fs.conn.execute(
            "SELECT date, rank_pool_size FROM scan_day WHERE data_version=?", (DV,))) == {"2020-01-02": 2}


def test_schema_follows_b3_2(tmp_path):
    """`spec/P1-B3-replay.md` §B3.2 的三項結構要求要有守門。

    驗收實測：拿掉全部索引、拿掉全部 `WITHOUT ROWID`、四條 PRAGMA 全改，**原本測試都是全綠**
    ——等於那三項「照辦」只是宣稱。這支把它們釘住。
    """
    from iching.features_io import _INDEXES, DATA_TABLES, FeatureStore as FS
    from iching.store import PRAGMAS as STORE_PRAGMAS
    from iching.features_io import PRAGMAS as F_PRAGMAS

    # ① 四條 PRAGMA 的內容與 store 逐字相同（字面量差一個 "PRAGMA " 前綴）
    assert tuple(f"PRAGMA {p}" for p in F_PRAGMAS) == tuple(STORE_PRAGMAS)
    with FS(tmp_path / "f.db") as fs:
        got = {k: fs.conn.execute(f"PRAGMA {k}").fetchone()[0]
               for k in ("journal_mode", "synchronous", "cache_size", "temp_store")}
        assert got == {"journal_mode": "wal", "synchronous": 1, "cache_size": -64000, "temp_store": 2}
        # ② 每張資料表都是複合 PK ＋ WITHOUT ROWID
        sql = {n: (s_ or "") for n, s_ in fs.conn.execute(
            "SELECT name, sql FROM sqlite_master WHERE type='table'")}
        for t in DATA_TABLES:
            assert "WITHOUT ROWID" in sql[t].upper(), t
            assert "PRIMARY KEY(" in sql[t], t
        # ③ 宣告的索引都建起來了
        have = {n for n, in fs.conn.execute("SELECT name FROM sqlite_master WHERE type='index' AND sql IS NOT NULL")}
        assert {name for name, _, _ in _INDEXES} <= have
        assert "idx_scan_day_date" not in have          # 刻意沒有：PK 已是 (data_version, date)


def test_rewriting_a_day_with_fewer_keys_removes_the_old_ones(tmp_path):
    """重寫成**更少**的列時，舊鍵必須消失——`INSERT OR REPLACE` 是逐鍵覆蓋，擋不住這個。

    驗收實測的原始缺陷：`p_cs` 3 檔重寫成 1 檔，仍是 3 列。而「鍵集合縮小」正是上游修正後
    會發生的事（某檔掉出排名池、某產業最後一檔停牌）。
    """
    from iching.scan import IndustryAgg, IndustryBreadth, MarketBreadth, ScanDay

    def day(stocks: list[str], inds: list[str]) -> ScanDay:
        b = MarketBreadth(market="twse", tpe_date="2020-01-02", n_stocks=len(stocks),
                          above_ma_count={5: 1}, ma_eligible={5: 1}, new_high_count={10: 0},
                          new_low_count={10: 0}, hl_eligible={10: 1}, ad_line=0,
                          amount_up=1.0, amount_ret_eligible=1.0, amount_total=1.0)
        return ScanDay(tpe_date="2020-01-02", breadth={"twse": b},
                       industry=[IndustryAgg("twse", "2020-01-02", 10, i, 1, 0.5) for i in inds],
                       industry_breadth=[IndustryBreadth("twse", "2020-01-02", i, 1, {5: 1}, {5: 1})
                                         for i in inds],
                       excess={("twse", 10): {s_: 0.1 for s_ in stocks}},
                       p_cs={("twse", 10): {s_: 50.0 for s_ in stocks}})

    with FeatureStore(tmp_path / "f.db") as fs:
        fs.set_params(DV, {"x": 1})
        fs.write_day(day(["1101", "2330", "2454"], ["水泥", "半導體"]), DV,
                     rank_pool_size=3, adv_tracked=3, adv_ready=3)
        assert fs.counts(DV)["p_cs"] == 3
        fs.write_day(day(["1101"], ["水泥"]), DV, rank_pool_size=1, adv_tracked=1, adv_ready=1)
        assert [r[0] for r in fs.conn.execute(
            "SELECT stock_id FROM p_cs WHERE data_version=?", (DV,))] == ["1101"]
        assert [r[0] for r in fs.conn.execute(
            "SELECT industry FROM industry_agg WHERE data_version=?", (DV,))] == ["水泥"]
        assert [r[0] for r in fs.conn.execute(
            "SELECT industry FROM industry_breadth_window WHERE data_version=?", (DV,))] == ["水泥"]
        # 別的日期不得被波及——逐日 DELETE 的 WHERE 少了 date 條件就會把整個 dv 清光
        import dataclasses
        other = day(["9999"], ["其他"])
        other = dataclasses.replace(
            other, tpe_date="2020-01-03",
            breadth={"twse": dataclasses.replace(other.breadth["twse"], tpe_date="2020-01-03")})
        fs.write_day(other, DV, rank_pool_size=1, adv_tracked=1, adv_ready=1)
        assert sorted(fs.dates(DV)) == ["2020-01-02", "2020-01-03"]
        assert fs.counts(DV)["p_cs"] == 2                 # 01-02 的 1101 ＋ 01-03 的 9999


def test_partial_range_into_existing_db_is_refused(ran):
    """`--from` 寫進已有更早資料的 DB 會讓 `ad_line` 在接縫跳一個無意義的差，預設拒絕。

    驗收實測那個跳動是 −165，而且 rc=0、零警告——正是腳本自己宣稱要防的那類錯。
    """
    cache, out = ran
    rc = S.main(["--cache-dir", str(cache), "--out", str(out), "--quiet",
                 "--from", DAYS[70], "--allow-short-warmup", "--warmup-days", "5"])
    assert rc == 2
    rc = S.main(["--cache-dir", str(cache), "--out", str(out), "--quiet",
                 "--from", DAYS[70], "--allow-short-warmup", "--warmup-days", "5", "--allow-ad-seam"])
    assert rc == 0                                    # 明示才放行
    # 寫進空 DB 不受影響
    assert S.main(["--cache-dir", str(cache), "--out", str(cache / "fresh.db"), "--quiet",
                   "--from", DAYS[70], "--allow-short-warmup", "--warmup-days", "5"]) == 0


def test_empty_range_is_an_error_not_a_success(ran):
    """`--to` 早於 `--from`、或 `--limit-days` 在暖機期就用完——寫 0 日不得回報「全部落地」。"""
    cache, _ = ran
    out = cache / "empty.db"
    assert S.main(["--cache-dir", str(cache), "--out", str(out), "--quiet",
                   "--from", DAYS[40], "--to", DAYS[10], "--allow-short-warmup"]) == 2
    assert S.main(["--cache-dir", str(cache), "--out", str(out), "--quiet",
                   "--from", DAYS[70], "--warmup-days", "5", "--allow-short-warmup",
                   "--limit-days", "3"]) == 1


def test_unwritable_out_exits_2_not_traceback(tmp_path, capsys):
    """`--out` 指到不存在的目錄／既有目錄（磁碟寫滿同一條路徑）要 exit 2，不是 traceback。"""
    cache = tmp_path / "cache"
    build(cache, amount_scale=SCALE)
    (tmp_path / "adir").mkdir()
    assert S.main(["--cache-dir", str(cache), "--out", str(tmp_path / "adir"), "--quiet"]) == 2
    assert "scan 中止" in capsys.readouterr().err


def test_index_missing_is_landed_in_both_places(tmp_path):
    """指數缺值要同時落在 `market_breadth.index_missing`（旗標）與 `scan_day.index_missing`（清單）。

    合成 DB 兩個指數都齊全，所以那兩欄在端對端測試裡**恆為 0／空**、寫死也看不出來
    （驗收實測：兩個突變都全綠）。這支直接餵一個缺指數的 `ScanDay`。
    """
    from iching.scan import MarketBreadth, ScanDay

    def b(mk):
        return MarketBreadth(market=mk, tpe_date="2020-01-02", n_stocks=1, above_ma_count={5: 0},
                             ma_eligible={5: 0}, new_high_count={10: 0}, new_low_count={10: 0},
                             hl_eligible={10: 0})

    with FeatureStore(tmp_path / "f.db") as fs:
        fs.set_params(DV, {"x": 1})
        fs.write_day(ScanDay("2020-01-02", {"twse": b("twse"), "tpex": b("tpex")}, [], [], {}, {},
                             index_missing=["tpex"]),
                     DV, rank_pool_size=0, adv_tracked=0, adv_ready=0)
        got = dict(fs.conn.execute(
            "SELECT market, index_missing FROM market_breadth WHERE data_version=?", (DV,)))
        assert got == {"twse": 0, "tpex": 1}
        assert fs.conn.execute(
            "SELECT index_missing FROM scan_day WHERE data_version=?", (DV,)).fetchone()[0] == "tpex"
