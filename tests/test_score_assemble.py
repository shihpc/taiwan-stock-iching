"""§8 第 3 條：輸出列的鍵＝dimensions.json 的 scores_db_row，程式直接讀該檔；改 dimensions.json 加一鍵 → 輸出缺鍵即紅。
§8 第 5 條：同輸入同版本三元組重跑逐位相同；改 model_version（參數）後輸出必須不同。"""
from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from conftest import ROOT, synth_market_inputs, synth_stock_inputs
from iching.score import MARKET_STOCK_ID, assemble_row, row_key, score_market, score_stock, scores_db_row_keys
from iching.score.assemble import DIMENSIONS_PATH
from iching.score.params import SCOPE_MARKET
from iching.score import build_params

DV, TV = "fm-20260909-01", "0.2"


def test_keys_come_from_dimensions_json():
    d = json.loads((ROOT / "spec" / "dimensions.json").read_text(encoding="utf-8"))
    assert DIMENSIONS_PATH == ROOT / "spec" / "dimensions.json"
    assert list(scores_db_row_keys()) == d["targets"]["scores_db_row"]["key"]
    assert set(scores_db_row_keys()) >= {"market", "horizon", "stock_id", "tpe_trading_date", "model_version", "data_version", "text_version"}


def test_added_key_in_dimensions_is_required(tmp_path, ps_twse, mkt):
    src = ROOT / "spec" / "dimensions.json"
    d = json.loads(src.read_text(encoding="utf-8"))
    d["targets"]["scores_db_row"]["key"].append("batch_id")
    p = tmp_path / "dimensions.json"
    p.write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")
    ms = score_market(mkt, ps_twse, "short")
    with pytest.raises(KeyError, match="batch_id"):
        assemble_row(ms, ps_twse, DV, TV, dimensions_path=str(p))
    row = assemble_row(ms, ps_twse, DV, TV, extra_keys={"batch_id": "b1"}, dimensions_path=str(p))
    assert row["batch_id"] == "b1" and row_key(row, str(p))[-1] == "b1" and "batch_id" not in scores_db_row_keys()


def test_market_row_shape(ps_twse, mkt):
    ms = score_market(mkt, ps_twse, "short")
    row = assemble_row(ms, ps_twse, DV, TV)
    assert row["stock_id"] == MARKET_STOCK_ID and row["scope"] == "market_index" and row["market"] == "twse"
    assert row["horizon"] == "short" and row["tpe_trading_date"] == mkt.tpe_date
    assert row["model_version"] == ps_twse.model_version() and row["data_version"] == DV and row["text_version"] == TV
    for k in "123456":
        assert isinstance(row[f"line_{k}"], float) and row[f"line_{k}_unknown"] is False
    assert len(row["lines_provisional"]) == 6 and 1 <= row["king_wen_provisional"] <= 64
    assert row["king_wen"] is None and row["calibrated"] is False and row["coverage"] == "full"
    assert isinstance(row["base_score"], float) and isinstance(row["inner_trigram_score"], float)
    assert row_key(row) == ("twse", "short", MARKET_STOCK_ID, mkt.tpe_date, ps_twse.model_version(), DV, TV)


def test_row_deterministic_and_json_serializable(ps_twse, stk):
    a = assemble_row(score_stock(synth_stock_inputs(), ps_twse, "mid"), ps_twse, DV, TV, detail=True)
    b = assemble_row(score_stock(synth_stock_inputs(), ps_twse, "mid"), ps_twse, DV, TV, detail=True)
    assert a == b
    assert json.dumps(a, ensure_ascii=False)
    assert "line_4_families" in a and "obv_slope" in a["line_4_families"]["A"]["subs"]


def test_model_version_binding_changes_row(ps_twse, mkt):
    ms = score_market(mkt, ps_twse, "short")
    r1 = assemble_row(ms, ps_twse, DV, TV)
    ps2 = ps_twse.with_param(SCOPE_MARKET, "short", "2", "C", "new_high_low_ratio", d=1.0)
    r2 = assemble_row(score_market(mkt, ps2, "short"), ps2, DV, TV)
    assert row_key(r1) != row_key(r2) and r1["model_version"] != r2["model_version"]
    assert r1["line_2"] != r2["line_2"]
    # 同版本重跑：鍵相同、內容逐位相同（B3.3 決定性，六位小數也相同）
    r3 = assemble_row(score_market(synth_market_inputs(), ps_twse, "short"), ps_twse, DV, TV)
    assert row_key(r1) == row_key(r3) and r1 == r3
    assert all(round(r1[f"line_{k}"], 6) == round(r3[f"line_{k}"], 6) for k in "123456")


def test_unknown_line_gives_pending_hexagram_and_missing_base(ps_twse):
    inp = synth_market_inputs(foreign_net_oi=None, basis=None, vix=None)
    row = assemble_row(score_market(inp, ps_twse, "mid"), ps_twse, DV, TV)
    assert row["line_5"] is None and row["line_5_unknown"] is True
    assert row["lines_provisional"] is None and row["king_wen_provisional"] is None and row["hexagram_name_provisional"] == "待補"
    assert row["base_score"] == {"missing": "line_unknown", "detail": "line 5 unknown"}
    assert row["coverage"] == "reweighted"


def test_provisional_lines_follow_rules_hysteresis_first(ps_twse, mkt):
    """必修 1：`assemble_row` 的暫定爻態走 `ps.rules.hysteresis_first`，改門檻 → lines_provisional／king_wen_provisional 改變。"""
    ms = score_market(mkt, ps_twse, "short")
    base = assemble_row(ms, ps_twse, DV, TV)
    hi = assemble_row(ms, ps_twse.with_rules(hysteresis_first=99.0), DV, TV)
    lo = assemble_row(ms, ps_twse.with_rules(hysteresis_first=0.0), DV, TV)
    assert hi["lines_provisional"] == [0] * 6 and hi["king_wen_provisional"] == 2
    assert lo["lines_provisional"] == [1] * 6 and lo["king_wen_provisional"] == 1
    assert base["lines_provisional"] not in ([0] * 6, [1] * 6) and base["king_wen_provisional"] not in (1, 2)
    assert hi["model_version"] != base["model_version"]
    with pytest.raises(ValueError):
        assemble_row(ms, build_params("tpex"), DV, TV)


def test_formal_lines_to_king_wen(ps_twse, mkt):
    ms = score_market(mkt, ps_twse, "short")
    row = assemble_row(ms, ps_twse, DV, TV, formal_lines=[1, 1, 1, 0, 0, 0])
    assert row["king_wen"] == 11 and row["hexagram_name"] == "地天泰"
    with pytest.raises(ValueError):
        assemble_row(ms, ps_twse, DV, TV, formal_lines=[1, 1, 1])
