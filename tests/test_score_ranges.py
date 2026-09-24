"""`scripts/score_ranges.py`：§16.5 `:714` 可達邊界登錄（步驟 1～3，裁定 #64）。

期待值一律在本檔**獨立手算**、寫死，不由被測函式產生（§20.1 末的判準 ③）：
- S_clip 端點：S(±3d) ＝ 100/(1+(7/3)^∓3) ＝ 34300/370、2700/370（與 d 無關）
- N(v; 10, 90)＝7.30＋(v−10)×1.0675；N(v; 0, 100)＝7.30＋v×0.854
"""
from __future__ import annotations

import copy
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

import score_ranges as SR  # noqa: E402

S_LO_EXACT = 2700 / 370          # 7.297297…
S_HI_EXACT = 34300 / 370         # 92.702702…
TOL = 1e-9


@pytest.fixture(scope="module")
def rep() -> dict:
    return SR.build()


def _row(rep, scope, market, h, line, cov):
    hits = [r for r in rep["rows"] if (r["scope"], r["market"], r["horizon"], r["line"], r["coverage"]) == (scope, market, h, line, cov)]
    assert len(hits) == 1
    return hits[0]


def _sub(rep, market, scope, h, line, iid):
    hits = [s for s in rep["subs"] if (s["market"], s["scope"], s["horizon"], s["line"], s["indicator_id"]) == (market, scope, h, line, iid)]
    assert len(hits) == 1
    return hits[0]


# ---- R1：鍵完整、144 筆、兩市場各存 ----

def test_144_rows_full_key_grid(rep):
    keys = {(r["scope"], r["market"], r["horizon"], r["line"], r["coverage"]) for r in rep["rows"]}
    assert len(rep["rows"]) == 144 == len(keys)
    assert {k[0] for k in keys} == {"market_index", "stock"}
    assert {k[1] for k in keys} == {"twse", "tpex"}
    assert {k[2] for k in keys} == {"short", "swing", "mid"}
    assert {k[3] for k in keys} == {"1", "2", "3", "4", "5", "6"}
    assert {k[4] for k in keys} == {"full", "reweighted"}


def test_all_bounds_within_s_range(rep):
    for r in rep["rows"]:
        if r["lo"] is not None:
            assert S_LO_EXACT - TOL <= r["lo"] <= r["hi"] <= S_HI_EXACT + TOL, r


def test_repo_files_match_generated():
    assert SR.main(["--check"]) == 0


def test_check_detects_hand_edit(tmp_path):
    md = (ROOT / "docs" / "score-ranges.md").read_text(encoding="utf-8").replace("10.5006", "10.5007", 1)
    pm, pj = tmp_path / "r.md", tmp_path / "r.json"
    pm.write_text(md, encoding="utf-8")
    pj.write_text((ROOT / "data" / "score_ranges.json").read_text(encoding="utf-8"), encoding="utf-8")
    assert SR.main(["--check", "--out-md", str(pm), "--out-json", str(pj)]) == 1


# ---- R2：子指標可達輸出（手算） ----

def test_s_sub_full_range(rep):
    s = _sub(rep, "twse", "market_index", "short", "1", "dist_ma_short")
    assert abs(s["lo"] - S_LO_EXACT) < TOL and abs(s["hi"] - S_HI_EXACT) < TOL


def test_l_sub_range_position():
    """L(0,.5,1) 在 [0,1]：原生 [20, 80] → N → 7.30＋10×1.0675＝17.975、7.30＋70×1.0675＝82.025。"""
    rep = SR.build(("twse",))
    s = _sub(rep, "twse", "market_index", "short", "1", "range_position")
    assert abs(s["lo"] - 17.975) < 1e-9 and abs(s["hi"] - 82.025) < 1e-9


def test_l_sub_clamped_to_declared(rep):
    """L(.3,.5,.7) 在 [0,1]：端點外推超出後夾在 [10, 90] → N → [7.30, 92.70]。"""
    s = _sub(rep, "twse", "market_index", "short", "2", "above_ma_short_ratio")
    assert abs(s["lo"] - 7.30) < 1e-9 and abs(s["hi"] - 92.70) < 1e-9


def test_phist_sub(rep):
    """P_hist(n=250，含當日、平手取中)：{0.2 … 99.8} → N → 7.30＋0.2×0.854、7.30＋99.8×0.854。"""
    s = _sub(rep, "twse", "market_index", "short", "5", "foreign_net_oi_phist")
    assert abs(s["lo"] - 7.4708) < 1e-9 and abs(s["hi"] - 92.5292) < 1e-9
    v = _sub(rep, "twse", "market_index", "short", "5", "vix_phist_rev")
    assert abs(v["lo"] - 7.4708) < 1e-9 and abs(v["hi"] - 92.5292) < 1e-9


def test_scenario_sub(rep):
    """divergence {35,25,55,50} → [25, 55] → N → [28.65, 54.27]；structure {80,20,50} → [24.38, 75.62]。"""
    d = _sub(rep, "twse", "market_index", "short", "3", "divergence_scenario")
    assert abs(d["lo"] - 28.65) < 1e-9 and abs(d["hi"] - 54.27) < 1e-9
    st = _sub(rep, "twse", "stock", "short", "2", "structure")
    assert abs(st["lo"] - 24.38) < 1e-9 and abs(st["hi"] - 75.62) < 1e-9


def test_volume_scenario_sub(rep):
    """單日原生：序 3 最低 40 − 0.5×(S_HI − 50)、序 2 最高 60 + 0.5×(S_HI − 50)；再 N(·; 0, 100)。"""
    lo_n = 40 - 0.5 * (S_HI_EXACT - 50)
    hi_n = 60 + 0.5 * (S_HI_EXACT - 50)
    s = _sub(rep, "twse", "stock", "short", "4", "volume_scenario")
    assert abs(s["lo"] - (7.30 + lo_n * 0.854)) < 1e-9 and abs(s["hi"] - (7.30 + hi_n * 0.854)) < 1e-9


def test_margin_scenario_sub(rep):
    """序 3 最低＝S(−3d)＝S_LO；序 4 最高＝50＋0.5×(S_HI−50)；原生即 S 值域、不套 N。"""
    s = _sub(rep, "twse", "stock", "mid", "5", "margin_scenario")
    assert abs(s["lo"] - S_LO_EXACT) < TOL and abs(s["hi"] - (50 + 0.5 * (S_HI_EXACT - 50))) < TOL


def test_half_window_discrete(rep):
    """foreign_persistence（n=5）：x ∈ [−2.5, 2.5]，d＝5/2÷3 → 3d＝2.5 恰好到端點 → 全幅。"""
    s = _sub(rep, "twse", "stock", "short", "5", "foreign_persistence")
    assert abs(s["lo"] - S_LO_EXACT) < 1e-6 and abs(s["hi"] - S_HI_EXACT) < 1e-6


# ---- R3：族／爻聚合（手算） ----

def test_market_line1_full(rep):
    """大盤初爻甲：A(.4) 全幅 S、B(.3) 全幅 S、C(.3) L(0,.5,1)＝[17.975, 82.025]。"""
    r = _row(rep, "market_index", "twse", "short", "1", "full")
    assert abs(r["lo"] - (0.7 * S_LO_EXACT + 0.3 * 17.975)) < TOL
    assert abs(r["hi"] - (0.7 * S_HI_EXACT + 0.3 * 82.025)) < TOL


def test_market_line3_full(rep):
    """大盤三爻甲：A(.4) 全幅 S、B(.3) L(.35,.5,.65)→[7.30, 92.70]、C(.3) divergence [28.65, 54.27]。"""
    r = _row(rep, "market_index", "twse", "short", "3", "full")
    assert abs(r["lo"] - (0.4 * S_LO_EXACT + 0.3 * 7.30 + 0.3 * 28.65)) < TOL
    assert abs(r["hi"] - (0.4 * S_HI_EXACT + 0.3 * 92.70 + 0.3 * 54.27)) < TOL


def test_reweighted_reaches_single_family(rep):
    """大盤三爻乙：任何族都可因 insufficient_history 被排除（裁定 #64 ④）→ 只剩族 A（全幅）也可成爻。"""
    r = _row(rep, "market_index", "twse", "short", "3", "reweighted")
    assert abs(r["lo"] - S_LO_EXACT) < TOL and abs(r["hi"] - S_HI_EXACT) < TOL


def test_stock_line1_short_states(rep):
    """個股初爻短線只有族 A（revenue_yoy＋revenue_accel）：甲 1 種；乙＝兩子指標各缺一，共 2 種；整族缺即未知。"""
    assert _row(rep, "stock", "twse", "short", "1", "full")["n_states"] == 1
    assert _row(rep, "stock", "twse", "short", "1", "reweighted")["n_states"] == 2


def test_stock_line6_uses_market_direction_union(rep):
    """個股上爻族 A＝大盤方向分數的聯集（裁定 #64 ②），此處為全幅 → 乙可到全幅。"""
    d = rep["markets"]["twse"]["direction"]["short"]
    assert abs(d[0] - S_LO_EXACT) < 1e-6 and abs(d[1] - S_HI_EXACT) < 1e-6
    r = _row(rep, "stock", "twse", "short", "6", "reweighted")
    assert abs(r["lo"] - S_LO_EXACT) < 1e-6 and abs(r["hi"] - S_HI_EXACT) < 1e-6


# ---- 守門 ----

def test_missing_support_aborts(monkeypatch):
    sup = dict(SR.SUPPORT)
    del sup["dist_ma_short"]
    monkeypatch.setattr(SR, "SUPPORT", sup)
    assert SR.main(["--check"]) == 2


def test_special_floor_guard():
    fam = [SR.FamState(True, None, False, 10.0, 80.0)]            # 上界 80 < 下限 84.16 → 下限會擴張區間
    with pytest.raises(SR.RangeError, match="下限"):
        SR._check_special("stock", "mid", "1", {"A": fam}, SR.build_params("twse").rules)


def test_special_cap_guard():
    fam = [SR.FamState(True, None, False, 85.0, 90.0)]            # 下界 85 > 封頂 79.89 → 封頂會擴張區間
    with pytest.raises(SR.RangeError, match="封頂"):
        SR._check_special("stock", "short", "3", {"A": fam}, SR.build_params("twse").rules)


def test_json_is_valid_and_complete():
    js = json.loads((ROOT / "data" / "score_ranges.json").read_text(encoding="utf-8"))
    assert len(js["rows"]) == 144 and set(js["markets"]) == {"twse", "tpex"}
    assert all(set(SR.SUPPORT) >= {s["indicator_id"]} for s in js["subs"])
    assert copy.deepcopy(js)["source"] == "spec/stock-iching-plan-v1.2.2.md:714"


def test_stock_line1_mid_family_b_variants(rep):
    """個股初爻中期族 B 有三種互斥組合（非金融 eps_yoy／eps_diff_over_price 二選一＋毛利；金融 稅前＋淨值）：
    甲＝三種組合各自全到齊，共 3 種狀態（族 A、C 各只有 1 種滿狀態）。"""
    assert _row(rep, "stock", "twse", "mid", "1", "full")["n_states"] == 3
