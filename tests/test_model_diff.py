"""`scripts/model_diff.py`（`docs/P3-CALIBRATION.md` §33）的離線測試——合成 `scores.db`、小到能手算。

合成世界（`build()`）：一個 data_version、四個日子——`PRE`（訓練段前）、`D1`／`D2`（範圍內）、`HOLD`（保留段）；
兩市場各一個大盤列（`__MARKET__`）、twse 兩檔（1101／2330）、tpex 一檔（6488）；期間 short／mid。
每列的基準值見 `base_row()`：`line_k = 50+k`、`base_score 60`、`lines_* "111111"`、`line_states "yyyyyy"`……
各測試只在新側用 `edit` 改幾格，預期數字都寫在測試裡（每個數字附手算）。
新側 model_version＝現行碼（`MD.current_model_versions()`），舊側是假的 `p2-score-engine-1.*`。

驗收條目對照（accept_dbdiff.md「怎麼驗」2）：①test_identical ②test_only_twse_line3 ③test_only_tpex_line3
④test_market_row_change ⑤test_c4_base_score_only ⑥test_residue_* ⑦test_date_set_differs／test_key_set_differs
⑧test_holdout_* ⑨test_new_mv_not_current ⑩test_old_equals_new；D 的計數：test_report_numbers_hand_computed 等。
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "src"))

import model_diff as MD  # noqa: E402
from iching.config import SEGMENTS  # noqa: E402
from iching.score.assemble import MARKET_STOCK_ID  # noqa: E402
from iching.scores_io import ScoreStore  # noqa: E402

DV = "fm-20260911-01"
TV = "tv-test"
PRE, D1, D2, HOLD = "2020-12-31", "2021-01-04", "2021-01-05", "2025-01-02"
ALL_DATES = (PRE, D1, D2, HOLD)
HORIZONS = ("short", "mid")
STOCKS = (("twse", "1101"), ("twse", "2330"), ("tpex", "6488"))
CUR = MD.current_model_versions()
OLD_MV = {"twse": "p2-score-engine-1.aaaaaaaaaaaa", "tpex": "p2-score-engine-1.bbbbbbbbbbbb"}


def base_row(market: str, horizon: str, sid: str, date: str) -> dict:
    mk = sid == MARKET_STOCK_ID
    r = {"market": market, "horizon": horizon, "stock_id": sid, "date": date, "scope": "market_index" if mk else "stock"}
    for k in range(1, 7):
        r.update({f"line_{k}": 50.0 + k, f"line_{k}_unknown": 0, f"line_{k}_coverage_ratio": 1.0, f"line_{k}_reweighted": 0})
    r.update({"lines_provisional": "111111", "king_wen_provisional": 1, "hexagram_name_provisional": "乾",
              "lines_formal": "111111", "king_wen": 1, "hexagram_name": "乾", "base_score": 60.0,
              "inner_trigram_score": 55.0, "outer_trigram_score": 58.0, "coverage": "full", "calibrated": 1,
              "flags": '{"x": 1}' if mk else None, "line_states": "yyyyyy", "streaks": "0,0,0,0,0,0",
              "in_rank_pool": None if mk else 1, "floor_applied": None,
              "overheated": None if mk else 0, "overheat_cap_applied": None if mk else 0})
    return r


def build(path: Path, mv: dict, *, dates=ALL_DATES, edit=None, extra=(), diag_edit=None) -> Path:
    """`edit(row) -> row | None`（None＝不寫該列）；`extra`＝[(model_version, row)]；`diag_edit(date, diag) -> diag`。"""
    with ScoreStore(path) as s:
        s.set_params(DV, {"model": dict(mv)})
        for d in dates:
            rows = []
            for m, sid in [("twse", MARKET_STOCK_ID), ("tpex", MARKET_STOCK_ID), *STOCKS]:
                for h in HORIZONS:
                    r = base_row(m, h, sid, d)
                    if edit is not None:
                        r = edit(r)
                    if r is not None:
                        rows.append((mv[m], r))
            rows += [x for x in extra if x[1]["date"] == d]
            diag = {"text_version": TV, "model_version_twse": mv["twse"], "model_version_tpex": mv["tpex"],
                    "n_market_rows": 4, "n_stocks": 3, "n_in_pool": 3, "n_stock_rows": 6, "n_stock_any_unknown": 0,
                    "n_market_any_unknown": 0, "elapsed_ms": 1.0, "index_missing": []}
            if diag_edit is not None:
                diag = diag_edit(d, diag)
            s.write_day(DV, d, rows, diag)
    return path


def pair(tmp_path: Path, *, new_edit=None, new_extra=(), new_dates=ALL_DATES, new_diag=None, old_diag=None,
         new_mv=None, old_mv=None, old_edit=None):
    old = build(tmp_path / "old.db", old_mv or OLD_MV, edit=old_edit, diag_edit=old_diag)
    new = build(tmp_path / "new.db", new_mv or CUR, dates=new_dates, edit=new_edit, extra=new_extra, diag_edit=new_diag)
    return old, new


def run(old, new, **kw):
    kw.setdefault("do_hash", False)
    return MD.run(old, new, **kw)


def at(sid, date, horizon="short", market=None):
    """edit 工具：只挑中一列。"""
    def pick(r):
        return r["stock_id"] == sid and r["date"] == date and r["horizon"] == horizon and (market is None or r["market"] == market)
    return pick


def edits(*pairs):
    """[(pick, {欄: 值})...] → edit 函式。"""
    def f(r):
        for pick, ch in pairs:
            if pick(r):
                r = {**r, **ch}
        return r
    return f


def bad(rep):
    return {c for c, v in rep["invariants"].items() if not v["ok"]}


# ---------------------------------------------------------------------------
# 範圍與常數
# ---------------------------------------------------------------------------
def test_segments_bounds_used():
    assert MD.date_range(False) == (SEGMENTS["train"][0], SEGMENTS["valid"][1])
    assert MD.date_range(True) == (SEGMENTS["train"][0], None)
    assert PRE < SEGMENTS["train"][0] <= D1 < D2 <= SEGMENTS["valid"][1]
    assert SEGMENTS["valid"][1] < SEGMENTS["holdout"][0] <= HOLD


def test_allowed_lines_derive_from_ruling_69_keys():
    """ALLOWED_LINES 不是憑印象寫的：裁定 #69 換 d 的 25 鍵（`tests/test_apply_calibration.py` 的清單）在現行
    `build_params` 裡所屬的爻，逐市場取聯集，必須恰好等於 ALLOWED_LINES（14 營收鍵 → 初爻；twse excess_* → 三爻、
    industry_relative_return → 上爻）。#68 的語意變更只動營收鍵，同在初爻。"""
    from iching.score.params import build_params
    from test_apply_calibration import CHANGED_BY_RULING_69
    lines: dict[str, set[int]] = {"twse": set(), "tpex": set()}
    for m, scope, ind, h in CHANGED_BY_RULING_69:
        ps = build_params(m)
        hit = [k for k in ps.params if k[0] == scope and k[1] == h and k[4] == ind]
        assert len(hit) == 1, (m, scope, ind, h, hit)
        lines[m].add(int(hit[0][2]))
    assert {m: tuple(sorted(v)) for m, v in lines.items()} == MD.ALLOWED_LINES


def test_current_model_versions_from_params_not_hardcoded():
    from iching.score.params import build_params
    assert CUR == {m: build_params(m).model_version() for m in ("twse", "tpex")}


def test_quantile_linear_hand():
    # 樣本 [0, 1, 3]：p50 位置 h=1 → 1；p99 h=1.98 → 1+0.98×(3−1)=2.96
    assert MD.quantile_linear(1, [1.0, 3.0], 0.5) == 1.0
    assert MD.quantile_linear(1, [1.0, 3.0], 0.99) == pytest.approx(2.96)
    # 樣本 [0, 0, 0, 10]：p50 h=1.5 → 0；p99 h=2.97 → 0+0.97×10=9.7
    assert MD.quantile_linear(3, [10.0], 0.5) == 0.0
    assert MD.quantile_linear(3, [10.0], 0.99) == pytest.approx(9.7)
    assert MD.quantile_linear(0, [], 0.5) is None
    # 與 numpy 預設 linear 一致
    import numpy as np
    assert MD.quantile_linear(2, [0.5, 2.0, 7.0], 0.37) == pytest.approx(float(np.quantile([0, 0, 0.5, 2.0, 7.0], 0.37)))


# ---------------------------------------------------------------------------
# ① 完全相同
# ---------------------------------------------------------------------------
def test_identical(tmp_path):
    old, new = pair(tmp_path)
    rep = run(old, new)
    assert rep["result_rc"] == 0 and bad(rep) == set()
    # 範圍內 D1、D2 兩日；每日 2 大盤列＋3 檔，各 2 期間 → 每日 10 列、共 20
    assert rep["days"]["compared"] == 2 and rep["rows_compared"] == 20
    assert rep["days"]["skipped_before_train"] == {"old": 1, "new": 1}      # PRE
    assert rep["days"]["skipped_after_range"] == {"old": 1, "new": 1}       # HOLD
    assert rep["market_rows"]["twse|short"] == {"n_rows": 2, "n_diff_rows": 0}
    g = rep["groups"]["twse|short"]                                          # 1101、2330 × D1、D2
    assert g["n_rows"] == 4 and g["n_diff_rows"] == 0
    assert set(g["lines"]) == {"1", "3", "6"} and set(rep["groups"]["tpex|short"]["lines"]) == {"1"}
    l1 = g["lines"]["1"]
    assert (l1["score_changed"], l1["abs_delta_n"], l1["abs_delta_nonzero"], l1["abs_delta_max"], l1["abs_delta_p50"]) == (0, 4, 0, 0.0, 0.0)
    assert g["base_score_changed"] == 0 and g["base_score_max_abs_delta"] == 0.0
    assert g["king_wen_changed"] == 0 and g["king_wen_changed_ratio"] == 0.0
    assert rep["dbs"]["new"]["model_versions"] == {m: [CUR[m]] for m in CUR}
    assert rep["dbs"]["old"]["model_versions"] == {m: [OLD_MV[m]] for m in OLD_MV}
    assert set(rep["dbs"]["old"]["params_sha"]) == {DV} and rep["dbs"]["old"]["params_sha"] != rep["dbs"]["new"]["params_sha"]
    assert rep["include_holdout"] is False


# ---------------------------------------------------------------------------
# ② 只改 twse 三爻 → 合格；③ 只改 tpex 三爻 → C3
# ---------------------------------------------------------------------------
def test_only_twse_line3(tmp_path):
    old, new = pair(tmp_path, new_edit=edits((at("1101", D1), {"line_3": 43.0, "lines_provisional": "110111",
                                                                "lines_formal": "110111", "line_states": "yynyyy",
                                                                "streaks": "0,0,2,0,0,0", "king_wen": 5, "king_wen_provisional": 5})))
    rep = run(old, new)
    assert rep["result_rc"] == 0, rep["invariants"]
    g = rep["groups"]["twse|short"]
    assert g["n_diff_rows"] == 1 and g["lines"]["3"]["score_changed"] == 1 and g["lines"]["3"]["formal_flip"] == 1
    assert g["lines"]["1"]["score_changed"] == 0


def test_only_tpex_line3(tmp_path):
    old, new = pair(tmp_path, new_edit=edits((at("6488", D1), {"line_3": 43.0})))
    rep = run(old, new)
    assert rep["result_rc"] == 1
    # 三爻對 tpex 是非允許爻 → C3；初爻（唯一允許爻）沒變而整列不同 → 依字面 C4 也成立，兩條都報
    assert bad(rep) == {"C3", "C4"}, rep["invariants"]
    assert rep["invariants"]["C3"]["n"] == 1 and rep["invariants"]["C4"]["n"] == 1
    assert "爻3 line_3" in rep["invariants"]["C3"]["examples"][0] and "6488" in rep["invariants"]["C3"]["examples"][0]


@pytest.mark.parametrize("col,val,label", [
    ("line_2_unknown", 1, "line_2_unknown"),
    ("line_4_coverage_ratio", 0.5, "line_4_coverage_ratio"),
    ("line_5_reweighted", 1, "line_5_reweighted"),
    ("line_states", "yyynyy", "line_states[4]"),
    ("streaks", "0,0,0,0,7,0", "streaks[5]"),
    ("lines_formal", "111011", "lines_formal[4]"),
    ("lines_provisional", "101111", "lines_provisional[2]"),
])
def test_c3_each_positional_and_scalar_col(tmp_path, col, val, label):
    """twse 非允許爻（2／4／5）的每一種逐爻欄都會被 C3 抓到——位置欄的「第 k 位」有真的取對位。"""
    old, new = pair(tmp_path, new_edit=edits((at("2330", D2, "mid"), {col: val})))
    rep = run(old, new)
    ex = rep["invariants"]["C3"]["examples"]
    assert rep["invariants"]["C3"]["n"] == 1 and label in ex[0], ex
    assert bad(rep) == {"C3", "C4"}                                          # 允許爻 1／3／6 沒動而整列不同


def test_c3_line_meta_tpex_overheated(tmp_path):
    """本檔加嚴的一條：tpex 三爻的中間量 `overheated` 變了也算 C3（只加嚴，C4 前提不含它）。"""
    old, new = pair(tmp_path, new_edit=edits((at("6488", D2), {"overheated": 1, "line_1": 50.5})))
    rep = run(old, new)
    assert bad(rep) == {"C3"} and "overheated" in rep["invariants"]["C3"]["examples"][0]


def test_c3_null_attributable_to_allowed_line_is_ok(tmp_path):
    """tpex 初爻變缺值 → 暫定串整串 NULL、初爻狀態 `-` → 正式串整串 NULL：非允許爻的第 k 位不比，歸因於初爻 → 合格。"""
    ch = {"line_1": None, "line_1_unknown": 1, "lines_provisional": None, "king_wen_provisional": None,
          "hexagram_name_provisional": "待補", "line_states": "-yyyyy", "lines_formal": None, "king_wen": None, "hexagram_name": None}
    old, new = pair(tmp_path, new_edit=edits((at("6488", D1), ch)))
    rep = run(old, new)
    assert rep["result_rc"] == 0, rep["invariants"]


def test_c3_null_not_attributable_is_violation(tmp_path):
    """同樣整串 NULL，但允許爻（tpex 初爻）分數仍在、狀態不是 `-` → NULL 無從歸因 → C3。"""
    old, new = pair(tmp_path, new_edit=edits((at("6488", D1), {"lines_provisional": None, "line_1": 50.5})))
    rep = run(old, new)
    assert bad(rep) == {"C3"} and rep["invariants"]["C3"]["n"] == 5          # 非允許爻 2..6 各一例
    old2, new2 = pair(tmp_path / "b", new_edit=edits((at("6488", D1), {"lines_formal": None, "line_1": 50.5})))
    rep2 = run(old2, new2)
    assert bad(rep2) == {"C3"} and all("lines_formal" in e for e in rep2["invariants"]["C3"]["examples"])


# ---------------------------------------------------------------------------
# ④ 大盤列 → C2；⑤ 允許爻同但 base_score 變 → C4
# ---------------------------------------------------------------------------
def test_market_row_change(tmp_path):
    old, new = pair(tmp_path, new_edit=edits((at(MARKET_STOCK_ID, D2, "mid", "tpex"), {"line_1": 40.0})))
    rep = run(old, new)
    assert rep["result_rc"] == 1 and bad(rep) == {"C2"}
    assert rep["market_rows"]["tpex|mid"] == {"n_rows": 2, "n_diff_rows": 1}
    assert "line_1" in rep["invariants"]["C2"]["examples"][0]


def test_market_row_flags_text(tmp_path):
    """flags 比 JSON 原文：語意相同但鍵序不同也算不同（逐位）。"""
    old, new = pair(tmp_path, new_edit=edits((at(MARKET_STOCK_ID, D1, "short", "twse"), {"flags": '{"x":1}'})))
    rep = run(old, new)
    assert bad(rep) == {"C2"}


def test_c4_base_score_only(tmp_path):
    old, new = pair(tmp_path, new_edit=edits((at("1101", D1, "mid"), {"base_score": 61.0})))
    rep = run(old, new)
    assert rep["result_rc"] == 1 and bad(rep) == {"C4"}
    assert rep["invariants"]["C4"]["n"] == 1 and "base_score" in rep["invariants"]["C4"]["examples"][0]


def test_c4_not_triggered_when_allowed_line_changed(tmp_path):
    old, new = pair(tmp_path, new_edit=edits((at("1101", D1, "mid"), {"base_score": 61.0, "line_6": 57.0})))
    assert run(old, new)["result_rc"] == 0


def test_c4_premise_includes_positional_cols(tmp_path):
    """允許爻只有 `streaks` 第 1 位變（歷史造成）＋ base_score 變 → 前提不成立、不算 C4。"""
    old, new = pair(tmp_path, new_edit=edits((at("6488", D1), {"streaks": "1,0,0,0,0,0", "base_score": 61.0})))
    assert run(old, new)["result_rc"] == 0


# ---------------------------------------------------------------------------
# ⑥ 多版本殘留 → rc=2
# ---------------------------------------------------------------------------
def test_residue_same_key_two_versions(tmp_path):
    extra = [(OLD_MV["twse"], base_row("twse", "short", "1101", D1))]
    old, new = pair(tmp_path, new_extra=extra)
    with pytest.raises(MD.PreconditionError, match="多版本殘留"):
        run(old, new)
    assert MD.main(["--old", str(old), "--new", str(new), "--out", str(tmp_path / "r.json"), "--no-hash"]) == 2
    assert not (tmp_path / "r.json").exists()


def test_residue_row_model_version_mismatch(tmp_path):
    old = build(tmp_path / "old.db", OLD_MV)
    new = tmp_path / "new.db"
    build(new, CUR)
    con = sqlite3.connect(new)            # 把 D2 的 6488 short 那一列改掛到另一個 model_version（replay_day 仍記現行）
    con.execute("INSERT INTO versions(model_version, data_version, text_version) VALUES('p2-x.residue', ?, ?)", (DV, TV))
    vid = con.execute("SELECT version_id FROM versions WHERE model_version='p2-x.residue'").fetchone()[0]
    con.execute("UPDATE scores SET version_id=? WHERE stock_id='6488' AND date=? AND horizon='short'", (vid, D2))
    con.commit()
    con.close()
    with pytest.raises(MD.PreconditionError, match="多版本殘留"):
        run(old, new)


# ---------------------------------------------------------------------------
# ⑦ 日期／鍵集合不同 → C1
# ---------------------------------------------------------------------------
def test_date_set_differs(tmp_path):
    old, new = pair(tmp_path, new_dates=(PRE, D1, D2, "2021-01-06", HOLD))
    rep = run(old, new)
    assert rep["result_rc"] == 1 and bad(rep) == {"C1"}
    assert rep["invariants"]["C1"]["n"] == 1 and "2021-01-06: 只在新側" in rep["invariants"]["C1"]["examples"][0]
    assert rep["days"]["compared"] == 2


def test_key_set_differs(tmp_path):
    old, new = pair(tmp_path, new_edit=lambda r: None if (r["stock_id"] == "2330" and r["date"] == D1) else r)
    rep = run(old, new)
    assert bad(rep) == {"C1"} and rep["invariants"]["C1"]["n"] == 2        # 2330 × short／mid
    assert rep["groups"]["twse|short"]["n_rows"] == 3


# ---------------------------------------------------------------------------
# ⑧ 保留段：預設不讀
# ---------------------------------------------------------------------------
def test_holdout_difference_not_read_by_default(tmp_path, monkeypatch):
    ch = edits((at("6488", HOLD), {"line_3": 43.0}), (at("6488", PRE), {"line_3": 43.0}))
    old, new = pair(tmp_path, new_edit=ch)
    seen: list[tuple[str, str]] = []
    orig = MD.read_day_rows

    def spy(store, dv, date):
        seen.append((store.path.name, date))
        return orig(store, dv, date)
    monkeypatch.setattr(MD, "read_day_rows", spy)
    rep = run(old, new)
    assert rep["result_rc"] == 0 and rep["include_holdout"] is False
    assert sorted({d for _n, d in seen}) == [D1, D2], "範圍外的日子不得讀 scores 列"
    assert len(seen) == 4                                                    # 逐日串流：每日每側各一次
    rep2 = run(old, new, include_holdout=True)
    assert rep2["result_rc"] == 1 and rep2["include_holdout"] is True
    assert rep2["invariants"]["C3"]["n"] == 1 and HOLD in rep2["invariants"]["C3"]["examples"][0]   # PRE 仍不比
    assert rep2["days"]["skipped_after_range"] == {"old": 0, "new": 0}


def test_holdout_residue_would_abort_if_read(tmp_path):
    """保留段那天放一筆殘留版本列（讀到必 rc=2）；預設下 rc=0＝真的沒讀那天的 scores 列。"""
    extra = [(OLD_MV["twse"], base_row("twse", "short", "1101", HOLD))]
    old, new = pair(tmp_path, new_extra=extra)
    assert run(old, new)["result_rc"] == 0
    with pytest.raises(MD.PreconditionError):
        run(old, new, include_holdout=True)


def test_orphan_scores_date(tmp_path):
    old, new = pair(tmp_path)
    con = sqlite3.connect(new)
    con.execute("DELETE FROM replay_day WHERE date=?", (D2,))
    con.commit()
    con.close()
    with pytest.raises(MD.PreconditionError, match="不在 replay_day"):
        run(old, new)


# ---------------------------------------------------------------------------
# ⑨ ⑩ C6
# ---------------------------------------------------------------------------
def test_new_mv_not_current(tmp_path):
    fake = {"twse": CUR["twse"], "tpex": "p2-score-engine-2.000000000000"}
    old, new = pair(tmp_path, new_mv=fake)
    with pytest.raises(MD.PreconditionError, match="C6：新側 tpex"):
        run(old, new)


def test_old_equals_new(tmp_path):
    old, new = pair(tmp_path, old_mv=CUR)
    with pytest.raises(MD.PreconditionError, match="比到同一份"):
        run(old, new)
    assert MD.main(["--old", str(old), "--new", str(new), "--out", str(tmp_path / "r.json"), "--no-hash"]) == 2


def test_new_two_mvs_in_range(tmp_path):
    """新側範圍內某日 replay_day 記的是另一個 model_version → 新側不是「恰一個」→ rc=2。"""
    def dg(d, diag):
        return {**diag, "model_version_tpex": "p2-score-engine-2.111111111111"} if d == D2 else diag
    old = build(tmp_path / "old.db", OLD_MV)
    new = build(tmp_path / "new.db", CUR, diag_edit=dg)
    with pytest.raises(MD.PreconditionError, match="C6"):
        run(old, new)


def test_expect_old(tmp_path):
    old, new = pair(tmp_path)
    assert run(old, new, expect_old=dict(OLD_MV))["result_rc"] == 0
    with pytest.raises(MD.PreconditionError, match="--expect-old"):
        run(old, new, expect_old={"twse": OLD_MV["twse"], "tpex": "p2-score-engine-1.cccccccccccc"})
    with pytest.raises(MD.PreconditionError):
        MD.parse_expect_old("twse=x")


# ---------------------------------------------------------------------------
# C5
# ---------------------------------------------------------------------------
def test_c5_diag_mismatch(tmp_path):
    old, new = pair(tmp_path, new_diag=lambda d, g: {**g, "n_in_pool": 2} if d == D1 else g)
    rep = run(old, new)
    assert bad(rep) == {"C5"} and rep["invariants"]["C5"]["n"] == 1 and "n_in_pool" in rep["invariants"]["C5"]["examples"][0]
    old2, new2 = pair(tmp_path / "b", new_diag=lambda d, g: {**g, "index_missing": ["tpex"]} if d == D2 else g)
    assert bad(run(old2, new2)) == {"C5"}


def test_c5_stock_any_unknown_only_reported(tmp_path):
    # 新側 D1 2、D2 1（舊側皆 0）；PRE／HOLD 也改但在範圍外 → 2 日不同、Σ=3、max=2，不判失敗
    old, new = pair(tmp_path, new_diag=lambda d, g: {**g, "n_stock_any_unknown": {D1: 2, D2: 1, PRE: 9, HOLD: 9}[d]})
    rep = run(old, new)
    assert rep["result_rc"] == 0
    assert rep["replay_day"]["n_stock_any_unknown"] == {"days_diff": 2, "sum_delta": 3, "max_abs_delta": 2}
    assert rep["replay_day"]["model_version_twse"] == {"old": [OLD_MV["twse"]], "new": [CUR["twse"]]}


# ---------------------------------------------------------------------------
# D：報告數字（手算）
# ---------------------------------------------------------------------------
def _d_world(tmp_path):
    """twse short：
      1101 D1：line_1 51→52（Δ1）、line_3 53→43（Δ10）且正式／暫定串第 3 位 1→0、king_wen 1→5、king_wen_provisional 1→5、
               base 60→61.5（Δ1.5）
      1101 D2：line_1 51→54（Δ3）、base 60→59（Δ1）
      2330 D1：不變
      2330 D2：line_1 51→None（unknown 0→1，暫定串整串 NULL、king_wen_provisional→None）、base 60→None
    tpex short：6488 D1 line_1 51→50.5（Δ0.5）"""
    e = edits(
        (at("1101", D1), {"line_1": 52.0, "line_3": 43.0, "lines_provisional": "110111", "lines_formal": "110111",
                          "line_states": "yynyyy", "king_wen": 5, "king_wen_provisional": 5, "base_score": 61.5}),
        (at("1101", D2), {"line_1": 54.0, "base_score": 59.0}),
        (at("2330", D2), {"line_1": None, "line_1_unknown": 1, "lines_provisional": None, "king_wen_provisional": None,
                          "hexagram_name_provisional": "待補", "base_score": None}),
        (at("6488", D1), {"line_1": 50.5}),
    )
    return pair(tmp_path, new_edit=e)


def test_report_numbers_hand_computed(tmp_path):
    old, new = _d_world(tmp_path)
    rep = run(old, new)
    assert rep["result_rc"] == 0, rep["invariants"]
    g = rep["groups"]["twse|short"]
    assert g["n_rows"] == 4 and g["n_diff_rows"] == 3                        # 1101D1、1101D2、2330D2
    l1 = g["lines"]["1"]
    assert l1["score_changed"] == 3                                          # 1101D1、1101D2、2330D2（值→None 也算變）
    assert l1["unknown_flip"] == 1                                           # 2330D2
    assert l1["formal_flip"] == 0
    assert l1["abs_delta_n"] == 3 and l1["abs_delta_nonzero"] == 2           # 兩側皆有值：1101D1 1、1101D2 3、2330D1 0
    assert l1["abs_delta_max"] == 3.0
    assert l1["abs_delta_p50"] == 1.0                                        # [0,1,3] h=1
    assert l1["abs_delta_p99"] == pytest.approx(2.96)                        # h=1.98 → 1+0.98×2
    l3 = g["lines"]["3"]
    assert (l3["score_changed"], l3["unknown_flip"], l3["formal_flip"]) == (1, 0, 1)
    assert l3["abs_delta_n"] == 4 and l3["abs_delta_max"] == 10.0            # [0,0,0,10]
    assert l3["abs_delta_p50"] == 0.0 and l3["abs_delta_p99"] == pytest.approx(9.7)
    l6 = g["lines"]["6"]
    assert (l6["score_changed"], l6["abs_delta_n"], l6["abs_delta_max"], l6["abs_delta_p99"]) == (0, 4, 0.0, 0.0)
    assert g["base_score_changed"] == 3                                      # 1101D1、1101D2、2330D2（→None）
    assert g["base_score_max_abs_delta"] == 1.5                              # max(1.5, 1)；2330 兩日：D1 0、D2 不算
    assert g["king_wen_changed"] == 1 and g["king_wen_changed_ratio"] == 0.25
    assert g["king_wen_provisional_changed"] == 2                            # 1101D1（1→5）、2330D2（1→None）
    t = rep["groups"]["tpex|short"]
    assert t["n_rows"] == 2 and t["n_diff_rows"] == 1
    tl = t["lines"]["1"]
    assert tl["score_changed"] == 1 and tl["abs_delta_max"] == 0.5
    assert tl["abs_delta_p50"] == pytest.approx(0.25) and tl["abs_delta_p99"] == pytest.approx(0.495)   # [0,0.5]
    assert t["base_score_changed"] == 0 and t["base_score_max_abs_delta"] == 0.0
    for k in ("twse|mid", "tpex|mid"):
        assert rep["groups"][k]["n_diff_rows"] == 0
    assert rep["groups"]["twse|mid"]["n_rows"] == 4 and rep["groups"]["tpex|mid"]["n_rows"] == 2
    assert rep["rows_compared"] == 20
    assert list(rep["groups"]) == ["twse|short", "twse|mid", "tpex|short", "tpex|mid"]


def test_formal_flip_needs_both_formal_nonnull(tmp_path):
    """正式爻位翻轉＝兩側 lines_formal 皆非 NULL 且第 k 位不同；一側 NULL 不算翻轉（但 score_changed 照算）。"""
    e = edits((at("1101", D1), {"line_1": 40.0, "lines_formal": "011111", "line_states": "nyyyyy"}),
              (at("1101", D2), {"line_1": None, "line_1_unknown": 1, "lines_provisional": None, "line_states": "-yyyyy",
                                "lines_formal": None}))
    old, new = pair(tmp_path, new_edit=e)
    rep = run(old, new)
    assert rep["result_rc"] == 0, rep["invariants"]
    l1 = rep["groups"]["twse|short"]["lines"]["1"]
    assert (l1["score_changed"], l1["unknown_flip"], l1["formal_flip"]) == (2, 1, 1)


def test_sha256_and_no_hash(tmp_path):
    old, new = pair(tmp_path)
    rep = MD.run(old, new, do_hash=True)
    assert rep["dbs"]["old"]["sha256"] == hashlib.sha256(old.read_bytes()).hexdigest()
    assert rep["dbs"]["new"]["sha256"] == hashlib.sha256(new.read_bytes()).hexdigest()
    assert run(old, new)["dbs"]["new"]["sha256"] is None


def test_readonly_db_bytes_unchanged(tmp_path):
    old, new = _d_world(tmp_path)
    before = {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in (old, new)}
    run(old, new)
    assert {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in (old, new)} == before


# ---------------------------------------------------------------------------
# CLI、報告檔
# ---------------------------------------------------------------------------
def test_cli_writes_json_and_txt(tmp_path, capsys):
    old, new = _d_world(tmp_path)
    out = tmp_path / "runs" / "report_x.json"
    assert MD.main(["--old", str(old), "--new", str(new), "--out", str(out)]) == 0
    rep = json.loads(out.read_text(encoding="utf-8"))
    txt = out.with_suffix(".txt").read_text(encoding="utf-8")
    assert rep["result_rc"] == 0 and rep["elapsed_s"] >= 0
    assert "保留段未納入" in txt and txt.rstrip().endswith("結果：rc=0（全符合）")
    assert "爻1: 分數變 3｜unknown 翻轉 1｜正式爻位翻轉 0" in txt
    assert "king_wen 變 1（25.0000%）" in txt


def test_cli_violation_rc1_writes_report(tmp_path):
    old, new = pair(tmp_path, new_edit=edits((at("6488", D1), {"line_3": 43.0})))
    out = tmp_path / "r.json"
    assert MD.main(["--old", str(old), "--new", str(new), "--out", str(out), "--no-hash"]) == 1
    txt = out.with_suffix(".txt").read_text(encoding="utf-8")
    assert "C3: 違反 1 例" in txt and txt.rstrip().endswith("結果：rc=1（不變式違反）")


def test_cli_missing_db_rc2(tmp_path):
    old, _new = pair(tmp_path)
    assert MD.main(["--old", str(old), "--new", str(tmp_path / "nope.db"), "--out", str(tmp_path / "r.json")]) == 2


def test_unexpected_exception_is_rc2_not_rc1(tmp_path, monkeypatch):
    """未預期例外不得以 rc=1 結束（launcher 會把 rc=1 當成「不變式違反、推報告」）。"""
    old, new = pair(tmp_path)
    monkeypatch.setattr(MD, "read_day_rows", lambda *a: (_ for _ in ()).throw(ValueError("boom")))
    assert MD.main(["--old", str(old), "--new", str(new), "--out", str(tmp_path / "r.json"), "--no-hash"]) == 2


# ---------------------------------------------------------------------------
# 真引擎：合成原料（tests/synth_db.py）以真的 replay_scores 重播兩份，驗 C3／C4 的前提在真計分碼上成立
# ---------------------------------------------------------------------------
# 舊側＝「#68 前的營收語意（tests/pre68.py）＋裁定 #69 那 25 鍵的 d 各 ×1.05」、新側＝現行碼。
# 合成日期落在 2020（訓練段之前），所以把 MD.SEGMENTS 換成涵蓋合成日期的範圍——只換範圍，比對邏輯不動。
# 這支守的是規格假設：「只有允許爻與其下游會變」在真的 score_stock／assemble_row／遲滯狀態上成立
# （C4 的前提——下游欄只依賴爻——若被日後的計分碼打破，這支會紅）。
@pytest.fixture(scope="module")
def synth_cache(tmp_path_factory):
    import scan_features as SF
    from synth_db import build_full
    c = tmp_path_factory.mktemp("mdreal") / "cache"
    build_full(c)
    with pytest.MonkeyPatch.context() as mp:
        import iching.config as C
        mp.setattr(C, "LANDING_INFO_MIN_IDS", 1)
        mp.setattr(C, "PRICE_DAILY_MIN_ROWS", 1)
        assert SF.main(["--cache-dir", str(c), "--quiet", "--allow-short-warmup", "--warmup-days", "0"]) == 0
    return c


def _real_pair(cache, tmp_path, extra_keys=()):
    import replay_scores as R
    from pre68 import pre68_semantics
    from test_apply_calibration import CHANGED_BY_RULING_69
    from iching.score import calibrated as CAL
    new, old = tmp_path / "new.db", tmp_path / "old.db"
    assert R.main(["--cache-dir", str(cache), "--out", str(new), "--window", "30", "--quiet"]) == 0
    with pytest.MonkeyPatch.context() as mp:
        for k in [*CHANGED_BY_RULING_69, *extra_keys]:
            mp.setitem(CAL.CALIBRATED_D, k, CAL.CALIBRATED_D[k] * 1.05)
        with pre68_semantics(mp):
            assert R.main(["--cache-dir", str(cache), "--out", str(old), "--window", "30", "--quiet"]) == 0
    return old, new


def test_real_engine_ruling69_like_change_passes(synth_cache, tmp_path, monkeypatch):
    from synth_db import DAYS
    old, new = _real_pair(synth_cache, tmp_path)
    monkeypatch.setattr(MD, "SEGMENTS", {"train": (DAYS[0], "-"), "valid": ("-", DAYS[-1])})
    rep = run(old, new)
    assert rep["result_rc"] == 0, {c: v for c, v in rep["invariants"].items() if not v["ok"]}
    assert rep["days"]["compared"] == len(DAYS)
    assert all(m["n_diff_rows"] == 0 for m in rep["market_rows"].values())
    # 擾動真的有進到分數：twse 三爻有變（否則這支等於比了兩份相同的 db）
    assert rep["groups"]["twse|short"]["lines"]["3"]["score_changed"] > 0
    assert rep["dbs"]["old"]["model_versions"]["twse"] != rep["dbs"]["new"]["model_versions"]["twse"]


def test_real_engine_non_allowed_change_caught(synth_cache, tmp_path, monkeypatch):
    """另擾動 tpex 三爻的 excess_long（非允許）→ C3 必紅；另擾動 twse 大盤鍵 → C2 必紅。"""
    from synth_db import DAYS
    from iching.score.calibrated import CALIBRATED_D
    mkt = [k for k in CALIBRATED_D if k[0] == "twse" and k[1] == "market_index"]
    old, new = _real_pair(synth_cache, tmp_path, extra_keys=[("tpex", "stock", "excess_long", "short"), *mkt])
    monkeypatch.setattr(MD, "SEGMENTS", {"train": (DAYS[0], "-"), "valid": ("-", DAYS[-1])})
    rep = run(old, new)
    assert rep["result_rc"] == 1
    assert {"C2", "C3"} <= bad(rep)
    assert all("tpex/short/6488 爻3 line_3" in e for e in rep["invariants"]["C3"]["examples"])
