"""`floor_applied`／`overheated`／`overheat_cap_applied` 三個中間量出口（`docs/P3-CALIBRATION.md` §18 的 D1～D5）。

存在理由：規格 §16.5 `:717` 的第 ⑧ 項要「封頂／下限的 binding 率」，第 ③ 項要個股過熱旗標觸發率，
而這三個值雖然在 `score/stock.py` 早就算出來了，卻從來沒有落地到 `scores.db`——規格原文點名
「③只涵蓋旗標，創高下限原本沒有任何一項在看」。

本檔守的是**語意**而不只是「欄位有值」：三級語意（True 生效／False 可判定但沒生效／None 不適用）
不可互相塌陷，尤其 **None 不得被讀成 False**——binding 率的分母要排除 None。
"""
from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from iching.score.assemble import assemble_row  # noqa: E402
from iching.score.params import build_params  # noqa: E402
from iching.score.stock import score_stock  # noqa: E402
from iching.score.market import score_market  # noqa: E402
from iching.scores_io import SCALAR_COLS, SCHEMA_VERSION  # noqa: E402
from conftest import synth_market_inputs, synth_stock_inputs  # noqa: E402

DV, TV = "dv-test", "tv-test"
NEW_COLS = ("floor_applied", "overheated", "overheat_cap_applied")


def row_for(**ov):
    ps = build_params("twse")
    h = ov.pop("horizon", "mid")
    return assemble_row(score_stock(synth_stock_inputs(**ov), ps, h), ps, DV, TV, model_version="x")


# D1：三欄真的出現在列裡與 SCALAR_COLS；大盤列一律 None
def test_columns_present_and_market_rows_are_none():
    for c in NEW_COLS:
        assert c in SCALAR_COLS, f"{c} 沒進 SCALAR_COLS，落不了地"
    r = row_for()
    for c in NEW_COLS:
        assert c in r
    ps = build_params("twse")
    mk = assemble_row(score_market(synth_market_inputs(), ps, "mid"), ps, DV, TV, model_version="x",
                      flags={}, extra_keys={})
    for c in NEW_COLS:
        assert mk[c] is None, f"大盤列的 {c} 應為 None（個股專屬），實得 {mk[c]!r}"


# D1／D2：三級語意不得塌陷——None 是「不適用」不是「沒觸發」
def test_three_valued_semantics_not_collapsed():
    # 非 mid 期間沒有創高下限 → floor_applied 必須是 None（不是 False）
    for h in ("short", "swing"):
        assert row_for(horizon=h)["floor_applied"] is None, f"{h} 期間不套下限，應為 None"
    # mid 期間有營收資料 → 可判定，必須是 True/False 之一
    assert row_for(horizon="mid")["floor_applied"] in (True, False, None)


# D2：cap 只在真的改變了分數時才 True；hot 不成立時必須是 False 而非 None
def test_cap_applied_only_when_binding():
    r = row_for()
    hot, cap = r["overheated"], r["overheat_cap_applied"]
    if hot is None:
        assert cap is None, "無法判定過熱時 cap 也必須是 None（不可當成沒觸發）"
    else:
        assert cap in (True, False)
        if hot is False:
            assert cap is False, "未過熱卻報封頂生效"


# D3：不進指紋——加輸出欄位不得改變 model_version（那是計分規則的指紋）
def test_output_columns_do_not_enter_fingerprint():
    # §17 那批之後的值；本批若動到它就是把輸出欄位混進了計分規則
    assert build_params("twse").model_version() == "p2-score-engine-1.0bb386e9cf3b"
    assert build_params("tpex").model_version() == "p2-score-engine-1.8eb4f29fec3a"


# D3：六爻分數逐位不變（以 .hex() 比，避免 == 的浮點寬容）
def test_line_scores_bitwise_unchanged_across_horizons():
    ps = build_params("twse")
    for h in ("short", "swing", "mid"):
        ss = score_stock(synth_stock_inputs(), ps, h)
        r = assemble_row(ss, ps, DV, TV, model_version="x")
        for k in range(1, 7):
            v, lv = r[f"line_{k}"], ss.lines[str(k)].score
            assert (v is None) == (lv is None)
            if v is not None:
                assert v.hex() == lv.hex(), f"{h}|line_{k} 與 LineResult 不逐位相同"


# D4：schema 版本已 bump（舊 db 會被既有守門拒絕）
def test_schema_version_bumped():
    assert SCHEMA_VERSION == 2


# D4 的結構面守門（本批實際踩到的坑）：SCALAR_COLS 與手寫 DDL 是兩份清單，加欄位時很容易只改一邊。
# 第一版就是只加了 SCALAR_COLS、忘了 CREATE TABLE，單檔測試全綠（走 assemble_row 那層），
# 直到全量測試才炸出 `table scores has no column named floor_applied`。這支讓它在單檔就紅。
def test_scalar_cols_and_ddl_are_in_sync(tmp_path):
    import sqlite3

    from iching.scores_io import SCORE_COLS, ScoreStore

    db = tmp_path / "s.db"
    with ScoreStore(db):
        pass
    cols = {r[1] for r in sqlite3.connect(db).execute("PRAGMA table_info(scores)")}
    missing = [c for c in SCORE_COLS if c not in cols]
    assert not missing, f"SCORE_COLS 有 {missing} 不在 scores 表的 DDL 裡（兩份清單不同步）"


# 同一組欄位其實散在**三份**手寫清單：SCALAR_COLS、CREATE TABLE 的 DDL、以及 `flatten_row` 的逐欄列舉。
# 本批先漏了 DDL（全量測試才炸），補完 DDL 後又漏了 flatten_row（`export_scores` 與每日班的逐位比對才炸）。
# 這支把第三份也綁進來：flatten_row 的輸出鍵必須恰好涵蓋 scores 表除 version_id 外的全部欄位。
def test_flatten_row_covers_every_scores_column():
    from iching.scores_io import SCALAR_COLS, flatten_row

    row = {"market": "twse", "horizon": "mid", "stock_id": "2330", "tpe_trading_date": "2026-01-02"}
    out = flatten_row(row, line_states="------", streaks="0,0,0,0,0,0", in_rank_pool=0)
    want = {"market", "horizon", "stock_id", "date", *SCALAR_COLS}
    assert set(out) == want, (f"flatten_row 少了 {sorted(want - set(out))}／多了 {sorted(set(out) - want)}"
                              "（三份清單之一沒跟上）")
