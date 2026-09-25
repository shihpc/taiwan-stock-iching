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
    # mid 期間有營收資料且創高可判定 → 必須是 0／1，**不得是 None**。
    # （舊版寫 `in (True, False, None)` 是恆真斷言——`_i` 的值域恰為 {None,0,1}，三者都通過，等於沒測；
    #  而且上一行註解說「必須是 True/False 之一」卻把 None 放進 tuple，自相矛盾。2026-09-21 複驗抓到。）
    assert row_for(horizon="mid")["floor_applied"] in (0, 1)


# D2：兩條「可判定但沒生效」的分支必須各自有守門。
# 舊版這支用預設情境做條件式斷言，而且 `if hot is False:` 是**死碼**——落地值是 int 0／1，`0 is False` 永遠為假，
# 那條斷言從未執行過（驗收者實測）。更糟的是兩個「False 側」的突變（創高但未觸下限卻記 True、過熱即報封頂生效）
# 在全量 1040 支下都活著。現在改成明確造出三個情境，並一律用 `== 0/1`／`is None` 斷言。
def test_floor_not_applied_when_score_already_above():
    """創高為真、但族 A 分數已高於下限 84.16 → floor_applied 必須是 0（不是 1、也不是 None）。"""
    import numpy as np
    # 月營收逐月加速成長 → 當月必為 12 月新高，且 revenue_yoy／accel 都很高 → famA 分數頂到值域上緣
    rev = [(f"2024-{m:02d}", float(1e6 * np.exp(0.02 * m * m))) for m in range(1, 13)]
    rev += [(f"2025-{m:02d}", float(1e6 * np.exp(0.02 * (m + 12) ** 2))) for m in range(1, 13)]
    r = row_for(monthly_revenue=rev)
    assert r["floor_applied"] == 0, f"分數已高於下限，max() 沒生效，應記 0，實得 {r['floor_applied']!r}"


def test_overheated_true_but_cap_not_binding():
    """過熱為真、但三爻分數未超過封頂 79.89 → overheated=1 而 overheat_cap_applied 必須是 0。"""
    import numpy as np

    from conftest import synth_stock_inputs as _s
    base = _s()
    n = len(np.asarray(base.close))
    close = np.linspace(100.0, 140.0, n)          # 長度必須與 high／low 一致，否則 indicators 直接拋
    close[-1] += 20.0                             # 末日暴衝 → (C−MA20)/ATR14_prev > 3，滿足過熱的第二個條件
    # 指數同步暴衝 → 超額報酬不突出，三爻分數不會衝上封頂 79.89（要的就是「過熱但沒封頂」這一側）
    r = row_for(close=close, high=close + 0.5, low=close - 0.5,
                index_close=close * 300.0, p_cs_long_excess=99.0)
    assert r["overheated"] == 1, f"p_cs 99 應判過熱，實得 {r['overheated']!r}"
    assert r["overheat_cap_applied"] == 0, f"分數未超過封頂，不應記生效，實得 {r['overheat_cap_applied']!r}"


def test_cap_none_when_overheat_undecidable():
    """close 缺 → 過熱無法判定 → hot 與 cap 都必須是 None（不可當成沒觸發）。"""
    r = row_for(close=None)
    assert r["overheated"] is None and r["overheat_cap_applied"] is None


# D3：不進指紋——加輸出欄位不得改變 model_version（那是計分規則的指紋）
def test_output_columns_do_not_enter_fingerprint():
    # 現行指紋（§17 那批之後為 p2-score-engine-1.0bb386e9cf3b／…8eb4f29fec3a，裁定 #68 升 RULES_VERSION 後為下列值，
    # docs/P3-CALIBRATION.md §31）；加輸出欄位若動到它就是把輸出欄位混進了計分規則
    assert build_params("twse").model_version() == "p2-score-engine-2.8f81122a37ae"
    assert build_params("tpex").model_version() == "p2-score-engine-2.dfa55ced4a96"


# D3 的**半條**：這支只證明 `assemble_row` 沒在搬運途中弄壞值（同一次執行內比 `r["line_k"]` 與 `LineResult`，
# 是恆等式），**證不了「本批前後不變」**——計分引擎真的改了的話兩邊會一起動。跨版本那半由驗收者以
# `git show a4218d3:` 取舊版對跑 60 組完成（驗收綁 c24c323，複驗綁 56db422 另跑 186 組），repo 內沒有對應的常駐守門。
def test_assemble_row_does_not_corrupt_line_scores():
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
