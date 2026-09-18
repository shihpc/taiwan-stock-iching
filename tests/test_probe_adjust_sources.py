"""`scripts/probe_adjust_sources.py` 離線測試：FakeFM 餵假回應，釘 P2 區間 vs 逐日判定、P3 並列輸出、P6 重疊判定、
token 不出現在任何輸出、呼叫上限。不碰網路、不碰真 DB。"""
from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

import probe_adjust_sources as PA  # noqa: E402
from iching.fm import PermissionRequired, TransientError  # noqa: E402

SECRET = "SECRET-TOKEN-XYZ-0123456789"
CAPRED, SPLIT, PARV = PA.DATASETS["capred"], PA.DATASETS["split"], PA.DATASETS["parvalue"]


def capred_row(sid: str, d: str, before: float, after: float) -> dict:
    return {"date": d, "stock_id": sid, "ClosingPriceonTheLastTradingDay": before, "PostReductionReferencePrice": after,
            "LimitUp": round(after * 1.1, 2), "LimitDown": round(after * 0.9, 2), "OpeningReferencePrice": after,
            "ExrightReferencePrice": after, "ReasonforCapitalReduction": "彌補虧損"}


def split_row(sid: str, d: str, before: float, after: float) -> dict:
    return {"date": d, "stock_id": sid, "type": "split", "before_price": before, "after_price": after,
            "max_price": after * 1.1, "min_price": after * 0.9, "open_price": after}


def parv_row(sid: str, d: str, before: float, after: float) -> dict:
    return {"date": d, "stock_id": sid, "stock_name": "X", "before_close": before, "after_ref_close": after,
            "after_ref_max": after * 1.1, "after_ref_min": after * 0.9, "after_ref_open": after}


class FakeFM:
    """`get(dataset, **params)`：依 data_id／日期區間濾 `rows[dataset]`。
    `first_day_only`：模擬 DividendResult 同型怪癖（全市場區間查詢只回 start_date 當天）。
    `permission`：該資料集一律 PermissionRequired。`transient_once`：第一次呼叫該資料集丟 TransientError（訊息含 token）。"""

    def __init__(self, rows: dict[str, list[dict]]):
        self.rows = rows
        self.first_day_only: set[str] = set()
        self.permission: set[str] = set()
        self.transient_once: set[str] = set()
        self.calls: list[tuple[str, dict]] = []
        self.n_requests = 0
        self.n_quota_waits = 0
        self._token = SECRET                      # 真 client 也會把 token 放在屬性上；輸出不得帶到它

    def get(self, dataset: str, **params):
        self.calls.append((dataset, dict(params)))
        self.n_requests += 1
        if dataset in self.permission:
            raise PermissionRequired(f"{dataset}: Your level is free. Please update your user level")
        if dataset in self.transient_once:
            self.transient_once.discard(dataset)
            raise TransientError(f"{dataset}: ConnectionError: https://api/x?token={SECRET}&dataset={dataset}")
        did, s, e = params.get("data_id"), params.get("start_date"), params.get("end_date")
        if did is None and dataset in self.first_day_only and s is not None:
            e = s
        out = []
        for r in self.rows.get(dataset, []):
            if did is not None and r["stock_id"] != did:
                continue
            if s is not None and r["date"] < s:
                continue
            if e is not None and r["date"] > e:
                continue
            out.append(dict(r))
        return out


def world_rows() -> dict[str, list[dict]]:
    return {
        CAPRED: [capred_row("3095", "2022-10-19", 2.55, 30.6), capred_row("2364", "2021-09-30", 3.2, 25.6),
                 capred_row("9999", "2022-03-08", 10.0, 20.0)],
        SPLIT: [split_row("6415", "2022-07-04", 2600.0, 650.0), split_row("8888", "2022-07-05", 100.0, 50.0)],
        PARV: [parv_row("6763", "2024-08-26", 450.0, 45.0), parv_row("7777", "2022-02-14", 60.0, 6.0)],
    }


def make_prices_db(path: Path) -> None:
    """合成 raw_price_daily：3095 停牌 10-04～10-18、10-19 恢復買賣 close≈after；6415 分割日 close≈after；
    6763 面額變更 date 當日 close≈before（模擬「最後交易日」語意）。另放 TAIEX 日曆（含 2022-10-10 非交易日）。"""
    con = sqlite3.connect(path)
    con.execute('CREATE TABLE raw_price_daily(cov_key TEXT, row_hash TEXT, data_version TEXT, date TEXT, stock_id TEXT, close REAL, PRIMARY KEY(cov_key,row_hash))')
    con.execute('CREATE TABLE raw_index_price(cov_key TEXT, row_hash TEXT, data_version TEXT, date TEXT, stock_id TEXT, close REAL, PRIMARY KEY(cov_key,row_hash))')
    rows = [("3095", "2022-10-03", 2.55), ("3095", "2022-10-19", 31.2), ("3095", "2022-10-20", 33.0),
            ("6415", "2022-07-01", 2600.0), ("6415", "2022-07-04", 640.0), ("6415", "2022-07-05", 655.0),
            ("6763", "2024-08-23", 452.0), ("6763", "2024-08-26", 449.0), ("6763", "2024-08-27", 44.8)]
    con.executemany("INSERT INTO raw_price_daily VALUES(?,?,?,?,?,?)",
                    [(f"{s}:{d}", f"{s}{d}", "dv", d, s, c) for s, d, c in rows])
    cal = [d for d in PA.calendar_days("2022-07-01", "2022-10-31") + PA.calendar_days("2024-08-01", "2024-09-02")
           if PA.dt.date.fromisoformat(d).weekday() < 5 and d != "2022-10-10"]
    con.executemany("INSERT INTO raw_index_price VALUES(?,?,?,?,?,?)", [(d, d, "dv", d, "TAIEX", 1.0) for d in cal])
    con.commit()
    con.close()


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("FINMIND_TOKEN", SECRET)
    cache = tmp_path / "cache"
    cache.mkdir()
    make_prices_db(cache / "prices.db")
    return {"cache": cache, "out": tmp_path / "out.json"}


def run(env, fm: FakeFM, extra: list[str] = (), capsys=None) -> tuple[int, dict, str]:
    rc = PA.main(["--cache-dir", str(env["cache"]), "--out", str(env["out"]), "--interval", "0", *extra], fm=fm)
    res = json.loads(env["out"].read_text(encoding="utf-8"))
    text = capsys.readouterr().out if capsys else ""
    return rc, res, text


# ---------------------------------------------------------------------------
# 純函式
# ---------------------------------------------------------------------------
def test_judge_range_vs_daily_verdicts():
    daily = [capred_row("3095", "2022-10-19", 2.55, 30.6), capred_row("9999", "2022-10-11", 1, 2)]
    assert PA.judge_range_vs_daily(list(daily), daily, "2022-10-10")["verdict"] == "一致"
    j = PA.judge_range_vs_daily([capred_row("8", "2022-10-10", 1, 2)], daily + [capred_row("8", "2022-10-10", 1, 2)], "2022-10-10")
    assert j["verdict"] == "區間只回首日" and j["only_in_daily"] == [("3095", "2022-10-19"), ("9999", "2022-10-11")]
    assert PA.judge_range_vs_daily([], daily, "2022-10-10")["verdict"] == "區間回空但逐日有列"
    assert PA.judge_range_vs_daily(daily[:1], daily, "2022-10-10")["verdict"] == "區間少於逐日"
    assert PA.judge_range_vs_daily(daily + [capred_row("1", "2022-10-12", 1, 2)], daily, "2022-10-10")["verdict"] == "區間多於逐日"
    assert PA.judge_range_vs_daily([], [], "2022-10-10")["verdict"] == "一致"


def test_date_semantics_hint():
    assert PA.date_semantics_hint(2.55, 30.6, 2.55, 31.2, 33.0).startswith("疑似恢復買賣日（當日 close≈after、前一交易日≈before）")
    assert PA.date_semantics_hint(450.0, 45.0, 452.0, 449.0, 44.8).startswith("疑似最後交易日（當日 close≈before、後一交易日≈after）")
    assert PA.date_semantics_hint(2.55, 30.6, 2.55, None, 33.0).startswith("無法判定（當日無價格列")
    assert PA.date_semantics_hint(10.0, 10.5, 10, 10.2, 10.4).startswith("無法判定（before≈after")
    assert PA.date_semantics_hint(None, 30.6, 2.55, 31.2, 33.0).startswith("無法判定（before/after")
    assert PA.date_semantics_hint(2.55, 30.6, 2.55, 12.0, 33.0) == "無法判定"


def test_overlap_and_column_profile():
    a = [split_row("6763", "2024-08-26", 450, 45), split_row("1", "2020-01-02", 1, 2)]
    b = [parv_row("6763", "2024-08-26", 450, 45)]
    assert PA.overlap_keys(a, b) == [("6763", "2024-08-26")]
    assert PA.overlap_keys(a, []) == []
    prof = PA.column_profile([{"x": "1", "y": None}, {"x": 2, "y": 3.0}])
    assert prof["x"]["types"] == ["str", "int"] and prof["x"]["sample"] == "1"
    assert prof["y"]["n_null"] == 1 and prof["y"]["sample"] == 3.0


def test_estimate_and_only():
    assert PA.estimate_calls(list(PA.ALL_PROBES)) == 3 + 3 + (22 + 15 + 33) + 12 + 18 == 106
    assert PA.estimate_calls(["P1"]) == 3
    assert PA.parse_only("p3, P1") == ["P1", "P3"]
    with pytest.raises(SystemExit):
        PA.parse_only("P9")


# ---------------------------------------------------------------------------
# 端到端（FakeFM）
# ---------------------------------------------------------------------------
def test_full_run_consistent(env, capsys):
    fm = FakeFM(world_rows())
    rc, res, text = run(env, fm, capsys=capsys)
    assert rc == 0 and res["aborted"] if "aborted" in res else rc == 0
    assert res["P1"]["usable"] == ["capred", "split", "parvalue"] and res["P1"]["skipped"] == {}
    # P2：三段都一致；預期檔在逐日與區間都出現；非交易日（週末＋2022-10-10）回 empty
    assert res["P2"]["verdicts"] == {"capred": "一致", "split": "一致", "parvalue": "一致"}
    w = {x["key"]: x for x in res["P2"]["windows"]}
    assert w["capred"]["expect_stock_in_daily"] and w["capred"]["expect_stock_in_range"]
    d = {x["date"]: x for x in w["capred"]["days"]}
    assert d["2022-10-10"]["trading_day"] is False and d["2022-10-10"]["kind"] == "empty"
    assert d["2022-10-19"]["kind"] == "ok" and d["2022-10-19"]["stock_ids"] == ["3095"]
    assert all(x["n_missing"] == 0 for x in res["P2"]["year_range_check"])
    # P3 並列：3095 恢復日 close 與前後交易日 close 都在，提示為恢復買賣日；6763 為最後交易日；2364 無 DB 列→無法判定
    r3095 = res["P3"]["stocks"]["3095"]["capred"]["rows"][0]
    assert (r3095["date"], r3095["before"], r3095["after"]) == ("2022-10-19", 2.55, 30.6)
    assert (r3095["close_prev_date"], r3095["close_prev"], r3095["close_same"], r3095["close_next"]) == ("2022-10-03", 2.55, 31.2, 33.0)
    assert r3095["hint"].startswith("疑似恢復買賣日")
    assert res["P3"]["stocks"]["6763"]["parvalue"]["rows"][0]["hint"].startswith("疑似最後交易日")
    assert res["P3"]["stocks"]["2364"]["capred"]["rows"][0]["hint"].startswith("無法判定（當日無價格列")
    assert res["P3"]["stocks"]["6415"]["capred"]["n"] == 0 and res["P3"]["summary"] == {"n_rows": 4, "n_resume_hint": 2, "n_last_hint": 1}
    assert "3095 [TaiwanStockCapitalReductionReferencePrice] date=2022-10-19" in text and "close 前(2022-10-03)=2.55 同日=31.2 後(2022-10-20)=33.0" in text
    # P4：欄名集合＝假回應欄名
    assert res["P4"]["capred"]["columns"] == sorted(capred_row("x", "d", 1, 2))
    assert res["P4"]["split"]["profile"]["type"]["types"] == ["str"]
    # P5：無事件交易日 empty、非交易日 empty、無非交易日有列
    p5 = res["P5"]["by_dataset"]["capred"]
    assert p5["kinds_on_empty_trading_day"] == ["empty"] and p5["kinds_on_nontrading_day"] == ["empty"]
    assert p5["n_nontrading"] == 7 and p5["nontrading_days_with_rows"] == [] and p5["errors"] == []
    # P6：無重疊
    assert res["P6"]["any_overlap"] is False and res["P6"]["year2022_join"]["n_overlap"] == 0
    # P7：區間查詢可信；逐年列數；2022 重用 P1（不多打）
    assert res["P7"]["by_dataset"]["capred"]["range_query_trusted"] is True
    assert {x["year"]: x["n_rows"] for x in res["P7"]["by_dataset"]["capred"]["years"]}[2022] == 2
    assert res["n_calls"] == res["estimated_calls"] == 106 == len(fm.calls)
    assert sum(1 for ds, p in fm.calls if ds == CAPRED and p.get("start_date") == "2022-01-01" and p.get("data_id") is None) == 1


def test_first_day_quirk_marks_range_untrusted(env, capsys):
    rows = world_rows()
    rows[SPLIT].append(split_row("5555", "2022-07-01", 10.0, 5.0))          # 窗首日有事件，怪癖下區間只回這一列
    fm = FakeFM(rows)
    fm.first_day_only = {SPLIT}
    rc, res, text = run(env, fm, capsys=capsys)
    assert res["P2"]["verdicts"] == {"capred": "一致", "split": "區間只回首日", "parvalue": "一致"}
    w = {x["key"]: x for x in res["P2"]["windows"]}["split"]
    assert w["n_range"] == 1 and w["n_daily"] == 3 and w["expect_stock_in_daily"] and not w["expect_stock_in_range"]
    assert w["range_dates"] == ["2022-07-01"] and w["only_in_daily"] == [["6415", "2022-07-04"], ["8888", "2022-07-05"]]
    chk = {x["key"]: x for x in res["P2"]["year_range_check"]}["split"]
    assert chk["n_missing"] == 3 and chk["n_year_rows"] == 0        # 2022 全年區間（01-01 無事件）回空、三列全漏
    # 首日沒事件的窗則是「區間回空但逐日有列」（另一種形狀，也要能分辨）
    fm2 = FakeFM(world_rows())
    fm2.first_day_only = {SPLIT}
    rc2, res2, _ = run(env, fm2, ["--only", "P2"], capsys=capsys)
    assert res2["P2"]["verdicts"]["split"] == "區間回空但逐日有列"
    assert res["P7"]["by_dataset"]["split"]["range_query_trusted"] is False
    assert res["P7"]["by_dataset"]["capred"]["range_query_trusted"] is True
    assert "**區間只回首日**" in text and "不可信" in text


def test_overlap_detected(env, capsys):
    rows = world_rows()
    rows[SPLIT].append(split_row("6763", "2024-08-26", 450.0, 45.0))          # 同一事件兩表各一列
    rows[PARV].append(parv_row("8888", "2022-07-05", 100.0, 50.0))            # 2022 全年 join 也撞一筆
    fm = FakeFM(rows)
    rc, res, text = run(env, fm, capsys=capsys)
    assert res["P6"]["per_stock"]["6763"]["split_x_parvalue"] == [["6763", "2024-08-26"]]
    assert res["P6"]["year2022_join"]["n_overlap"] == 1 and res["P6"]["year2022_join"]["overlap"] == [["8888", "2022-07-05"]]
    assert res["P6"]["any_overlap"] is True and "有重疊" in text


def test_permission_skips_dataset(env, capsys):
    fm = FakeFM(world_rows())
    fm.permission = {CAPRED}
    rc, res, text = run(env, fm, capsys=capsys)
    assert rc == 1
    assert res["P1"]["skipped"] == {"capred": "permission"} and res["P1"]["usable"] == ["split", "parvalue"]
    assert {x["key"]: x.get("skipped") for x in res["P2"]["windows"]}["capred"] is True
    assert res["P3"]["stocks"]["3095"]["capred"] == {"skipped": True} and res["P4"]["capred"] == {"skipped": True}
    assert res["P7"]["by_dataset"]["capred"] == {"skipped": True} and "capred" not in res["P5"]["by_dataset"]
    assert not any(ds == CAPRED for ds, _ in fm.calls[1:])          # 只在 P1 打過一次
    assert res["n_calls"] == 106 - 22 - 1 - 4 - 6


def test_token_never_in_output_or_json(env, capsys):
    fm = FakeFM(world_rows())
    fm.transient_once = {PARV}                                       # 例外訊息帶 token=…，必須被 redact
    rc, res, text = run(env, fm, capsys=capsys)
    dumped = env["out"].read_text(encoding="utf-8")
    assert SECRET not in text and SECRET not in dumped
    assert res["P1"]["datasets"]["parvalue"]["kind"] == "error" and "token=<redacted>" in res["P1"]["datasets"]["parvalue"]["msg"]
    assert res["P1"]["skipped"] == {"parvalue": "error"} and rc == 1


def test_call_cap_aborts_and_still_writes(env, capsys):
    fm = FakeFM(world_rows())
    rc, res, text = run(env, fm, ["--max-calls", "10"], capsys=capsys)
    assert rc == 2 and len(fm.calls) == 10 and res["n_calls"] == 10
    assert res["aborted"].startswith("已達呼叫上限 10") and "P1" in res and "P2" not in res
    assert "預估 106 已超過上限 10" in text


def test_only_subset_and_missing_prices_table(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("FINMIND_TOKEN", SECRET)
    cache = tmp_path / "cache"
    cache.mkdir()
    con = sqlite3.connect(cache / "prices.db")
    con.execute("CREATE TABLE raw_index_price(date TEXT, stock_id TEXT)")
    con.commit()
    con.close()
    env = {"cache": cache, "out": tmp_path / "o.json"}
    fm = FakeFM(world_rows())
    rc, res, text = run(env, fm, ["--only", "P3,P6"], capsys=capsys)
    assert rc == 0 and set(res) >= {"P1", "P3", "P6"} and "P2" not in res and "P7" not in res
    assert res["P3"]["prices_available"] is False and "沒有 raw_price_daily" in res["P3"]["prices_note"]
    assert res["P3"]["stocks"]["3095"]["capred"]["rows"][0]["hint"] == "（無 raw_price_daily）"
    assert res["n_calls"] == 3 + 12 and res["estimated_calls"] == 15


def test_no_token_exits_2(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("FINMIND_TOKEN", raising=False)
    rc = PA.main(["--env-file", str(tmp_path / "nope.env"), "--out", str(tmp_path / "o.json"), "--cache-dir", str(tmp_path)])
    assert rc == 2 and not (tmp_path / "o.json").exists()
    assert "FINMIND_TOKEN" in capsys.readouterr().err
