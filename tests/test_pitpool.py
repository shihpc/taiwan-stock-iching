"""`universe.PitPool` 純函式（`docs/P3-PIT-POOL.md` §2 #1／#8；裁定 #49 Q10～Q12）。

守門重點：轉換生效日＝較舊那列 `date`+1 曆日（Q11）；T 早於最舊列取最舊列 type；`emerging` 是 type 但不算在池（Q12）；
成員＝靜態合格 ∧ T 日市場∈{twse,tpex} ∧ `traded_sids`（Q10，本類別不碰價格列）；`pool_from_info` 語意不變、同 date 平手才走 tie-break。
真實資料：`data/pool.json`（repo 內的 TaiwanStockInfo 快照）實跑 11 檔跨 twse/tpex 代號的轉換日。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from iching import universe as U  # noqa: E402
from iching.universe import PitPool  # noqa: E402

D0 = "2026-09-11"


def _r(sid: str, type_: str, date: str, cat: str = "半導體業", name: str = "某") -> dict:
    return {"stock_id": sid, "type": type_, "industry_category": cat, "stock_name": name, "date": date}


def test_single_row_has_no_transition_and_same_market_everywhere():
    p = PitPool.from_snapshot_rows([_r("2330", "twse", D0)])
    assert set(p) == {"2330"} and p.transitions["2330"] == ((None, "twse"),)
    for T in ("1990-01-01", "2020-01-02", D0, "2099-12-31"):
        assert p.market("2330", T) == "twse" == p.listed("2330", T)
    assert "type" not in p["2330"] and p["2330"]["industry_category"] == "半導體業" and p.industry_of("2330") == "半導體業"
    assert p.market("9999", D0) is None and p.listed("9999", D0) is None and "9999" not in p
    assert p.report_transitions() == {"n_transitioned": 0, "transitions": {}, "anomalies": {}}


def test_multi_rows_same_market_is_reclassification_not_transition():
    p = PitPool.from_snapshot_rows([_r("5348", "tpex", "2025-06-01", "通信網路業"), _r("5348", "tpex", D0, "運動休閒類")])
    assert p.transitions["5348"] == ((None, "tpex"),)
    assert p["5348"]["industry_category"] == "運動休閒類" and p["5348"]["n_rows"] == 2 and p["5348"]["same_date_multi"] is False
    assert p.market("5348", "2020-01-02") == "tpex" == p.market("5348", D0)


def test_tpex_to_twse_effective_is_older_row_date_plus_one():
    """Q11：殘留 tpex 列 date=2021-05-15 → 2021-05-15 仍 tpex、2021-05-16 起 twse；殘留列之前（含資料起點以前）一律 tpex。"""
    p = PitPool.from_snapshot_rows([_r("3092", "tpex", "2021-05-15", "電子零組件業"), _r("3092", "twse", D0, "電子零組件業")])
    assert p.transitions["3092"] == ((None, "tpex"), ("2021-05-16", "twse"))
    assert p.market("3092", "2021-05-15") == "tpex" and p.market("3092", "2021-05-16") == "twse"
    assert p.market("3092", "2019-01-01") == "tpex" and p.market("3092", D0) == "twse"
    m = p.members("2021-05-15", ["3092"])
    assert m["3092"]["type"] == "tpex" and m["3092"]["industry_category"] == "電子零組件業"
    assert p.members("2021-05-16", ["3092"])["3092"]["type"] == "twse"
    assert p.report_transitions()["transitions"] == {"3092": [[None, "tpex"], ["2021-05-16", "twse"]]}
    # 月底跨月：2020-01-31 +1 ＝ 2020-02-01（曆日，不是字串 +1）
    q = PitPool.from_snapshot_rows([_r("1234", "tpex", "2020-01-31"), _r("1234", "twse", D0)])
    assert q.transitions["1234"][1] == ("2020-02-01", "twse")


def test_emerging_to_tpex_not_in_pool_before_effective_even_if_traded():
    """Q12：興櫃期有成交列仍不在池；`market()` 仍回 `emerging`（它是 type），`listed()` 回 None。"""
    p = PitPool.from_snapshot_rows([_r("6423", "emerging", "2024-05-14"), _r("6423", "tpex", D0)])
    assert p.market("6423", "2024-05-14") == "emerging" and p.listed("6423", "2024-05-14") is None
    assert p.market("6423", "2024-05-15") == "tpex" == p.listed("6423", "2024-05-15")
    assert p.members("2024-05-14", ["6423"]) == {} and set(p.members("2024-05-15", ["6423"])) == {"6423"}
    assert p.listed_ids("2024-05-14") == frozenset() and p.listed_ids("2024-05-15") == frozenset({"6423"})


def test_emerging_only_code_is_not_static_member():
    """從未上市櫃（只有 emerging 列）→ 不在靜態集合（與 `pool_from_info` 同）；ETF／DR 亦然。"""
    p = PitPool.from_snapshot_rows([_r("7777", "emerging", D0), _r("0050", "twse", D0, "ETF"), _r("9101", "twse", D0, "存託憑證"),
                                    _r("2330", "twse", D0)])
    assert set(p) == {"2330"} and p.market("7777", D0) is None


def test_residual_row_older_than_data_start_and_members_gate_on_traded_sids():
    """殘留列 date 早於資料起點（2019）→ 整段資料期都是新市場；T 早於殘留列（假設更早無資料＝同市場）取舊市場。
    成員由 `traded_sids` 決定：不在 `traded_sids` 的合格代號不進 members（Q10 由呼叫端用 `is_traded_row` 算）。"""
    p = PitPool.from_snapshot_rows([_r("6438", "tpex", "2019-06-30"), _r("6438", "twse", D0), _r("2330", "twse", D0)])
    assert p.market("6438", "2020-01-02") == "twse" and p.market("6438", "2019-06-30") == "tpex" and p.market("6438", "2018-01-01") == "tpex"
    assert set(p.members("2020-01-02", ["6438", "2330", "0050"])) == {"6438", "2330"}
    assert set(p.members("2020-01-02", ["2330"])) == {"2330"}
    assert list(p.members("2020-01-02", ["6438", "2330"])) == ["2330", "6438"]      # 代號升冪


def test_same_date_multi_rows_use_pick_tiebreak_only_when_tied():
    """同 date 兩列不同市場＝平手才走 `_pick` 三層 tie-break（①非產業→②傘狀→③twse 優先，順序不可調換）；
    不同 date 的列由 date 決定、不看 twse 優先。"""
    p = PitPool.from_snapshot_rows([_r("3092", "tpex", D0, "電子零組件業"), _r("3092", "twse", D0, "電子零組件業")])
    assert p.transitions["3092"] == ((None, "twse"),) and p["3092"]["same_date_multi"] is True      # ③ twse 優先
    q2 = PitPool.from_snapshot_rows([_r("3092", "tpex", D0, "電子零組件業"), _r("3092", "twse", D0, "電子工業")])
    assert q2.transitions["3092"] == ((None, "tpex"),) and q2["3092"]["industry_category"] == "電子零組件業"   # ② 傘狀先剔除，③ 沒機會
    q = PitPool.from_snapshot_rows([_r("4444", "twse", "2021-01-01"), _r("4444", "tpex", D0)])   # 逆向（上市→上櫃）照 date 走、不被 twse 優先蓋掉
    assert q.transitions["4444"] == ((None, "twse"), ("2021-01-02", "tpex"))


def test_report_transitions_flags_anomalies():
    rows = [_r("1111", "emerging", "2020-01-01"), _r("1111", "tpex", "2021-01-01"), _r("1111", "twse", "2022-01-01"), _r("1111", "tpex", D0),
            _r("2222", "tpex", "2020-01-01"), _r("2222", "twse", D0)]
    rep = PitPool.from_snapshot_rows(rows).report_transitions()
    assert rep["n_transitioned"] == 2 and set(rep["anomalies"]) == {"1111"}
    assert "轉換 3 次" in rep["anomalies"]["1111"] and "來回" in rep["anomalies"]["1111"]


def test_pool_from_info_semantics_unchanged_and_mapping_equality():
    rows = [_r("3092", "tpex", "2021-05-15", "電子零組件業"), _r("3092", "twse", D0, "電子零組件業"), _r("2330", "twse", D0)]
    old = U.pool_from_info(rows)
    p = PitPool.from_snapshot_rows(rows)
    assert set(old) == set(p) and old["3092"]["type"] == "twse"                     # 舊函式仍取最新一列的 type
    assert {k: v for k, v in old["3092"].items() if k != "type"} == p["3092"]
    assert p == PitPool.from_snapshot_rows(list(reversed(rows))) and p != PitPool.from_snapshot_rows(rows[1:])
    assert len(p) == 2 and sorted(p.keys()) == ["2330", "3092"] and dict(p.items())["2330"]["stock_name"] == "某"
    with pytest.raises(ValueError, match="沒有轉換表"):
        PitPool({"2330": {}}, {})


def test_real_snapshot_eleven_cross_market_codes():
    """`data/pool.json` 實跑：成員數（快照最新日、traded＝全部合格代號）＝`pool_from_info` 的檔數；11 檔跨市場代號的轉換日；
    有轉換的代號數（含 emerging→x）與異常清單可印出。"""
    rows = json.loads((ROOT / "data/pool.json").read_text(encoding="utf-8"))["rows"]
    p = PitPool.from_snapshot_rows(rows)
    old = U.pool_from_info(rows)
    today = max(str(r.get("date") or "") for r in rows)
    mem = p.members(today, list(p))
    assert set(mem) == set(old) and {s: m["type"] for s, m in mem.items()} == {s: m["type"] for s, m in old.items()}
    eleven = "3092 3652 4736 5236 6423 6426 6438 6446 6472 6589 8476".split()
    seqs = {s: p.transitions[s] for s in eleven}
    for s in eleven:
        types = [t for _, t in seqs[s]]
        assert len(seqs[s]) >= 2 and {"twse", "tpex"} <= set(types), (s, seqs[s])
    assert seqs["3092"] == ((None, "tpex"), ("2021-05-16", "twse"))
    assert seqs["6438"] == ((None, "tpex"), ("2021-01-22", "twse"))
    rep = p.report_transitions()
    assert rep["n_transitioned"] >= 11
