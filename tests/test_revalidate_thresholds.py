"""`scripts/revalidate_thresholds.py`：規格 §16.5 門檻行為重驗八項（`docs/P3-CALIBRATION.md` §20 的 F1～F6）。

本檔的重點是 **F2／F3 這一對**：F2 證明「沒有差異時八項必須全零」（自洽），F3 證明「動了某一項的來源
欄位時，只有那一項會變」（鑑別力）。少了 F3，一個永遠回 0 的實作也能通過 F2。
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import revalidate_thresholds as RT  # noqa: E402

FLAGS_ON = {"by_direction": {d: {"active": {f: False for f in RT.FLAG_NAMES},
                                "quota_multiplier": 1.0, "threshold_shift_deciles": 0.0}
                             for d in RT.DIRECTIONS}}


def mrow(market="twse", horizon="mid", flags=None):
    return {"scope": "market_index", "stock_id": "__MARKET__", "market": market, "horizon": horizon,
            "flags": flags if flags is not None else {"by_direction": {
                d: {"active": {f: False for f in RT.FLAG_NAMES}, "quota_multiplier": 1.0,
                    "threshold_shift_deciles": 0.0} for d in RT.DIRECTIONS}},
            "lines_formal": None, "king_wen": None, "inner_trigram_score": None,
            "outer_trigram_score": None, "base_score": None, "in_rank_pool": None,
            "floor_applied": None, "overheated": None, "overheat_cap_applied": None}


def srow(sid, *, bits="010101", kw=1, inner=60.0, outer=40.0, base=50.0, pool=1,
         floor=1, hot=0, cap=0, market="twse", horizon="mid"):
    return {"scope": "stock", "stock_id": sid, "market": market, "horizon": horizon,
            "lines_formal": [int(c) for c in bits], "king_wen": kw,
            "inner_trigram_score": inner, "outer_trigram_score": outer,
            "base_score": base, "in_rank_pool": pool, "flags": None,
            "floor_applied": floor, "overheated": hot, "overheat_cap_applied": cap}


def run_days(before_days, after_days):
    acc = RT.Acc()
    prev = {"before": {}, "after": {}}
    quota: dict = {}
    for b, a in zip(before_days, after_days, strict=True):
        RT.step_day(acc, b, a, prev, quota)
    return RT.build_report(acc)


def _day(**kw):
    return [mrow(), srow("1101", **kw), srow("2330", base=80.0, **{k: v for k, v in kw.items() if k != "base"})]


# F2：同一組列自己對自己 → 八項差異全零
def test_identical_inputs_give_zero_differences():
    days = [_day(bits="010101"), _day(bits="011101"), _day(bits="011100")]
    rep = run_days(days, [list(d) for d in days])
    assert rep["over_threshold"] == []
    for r in rep["items"]["1_line_state_diff_rate"]:
        assert r["diff_rate"] == 0.0
    for r in rep["items"]["5_trigram_state_diff_rate"]:
        assert r["diff_rate"] == 0.0
    for r in rep["items"]["6_hexagram_agreement"]:
        assert r["king_wen_same_rate"] == 1.0 and r["future_king_wen_same_rate"] == 1.0
    for r in rep["items"]["7_candidate_overlap"]:
        assert r["jaccard"] == 1.0 and r["same_rank_rate"] == 1.0
    for r in rep["items"]["2_hysteresis_flips"]:
        assert r["before"] == r["after"] and r["rel_diff"] == 0.0
    for r in rep["items"]["4_moving_line_count_dist"]:
        assert r["tv_distance"] == 0.0
    for r in rep["items"]["3_flag_hit_rate"] + rep["items"]["8_binding_rate"]:
        assert r["diff"] == 0.0


# F3：逐項擾動——只動某一項的來源欄位，只有該項該變
def test_each_item_reacts_to_its_own_perturbation():
    base_days = [_day(bits="010101"), _day(bits="011101")]

    def after_with(**kw):
        return [[mrow(), srow("1101", **kw), srow("2330", base=80.0, **{k: v for k, v in kw.items() if k != "base"})]
                for _ in base_days]

    # ① 爻態：改位元 → diff_rate 非零
    rep = run_days(base_days, [[mrow(), srow("1101", bits="111101"), srow("2330", bits="111101", base=80.0)]
                               for _ in base_days])
    assert any(r["diff_rate"] and r["diff_rate"] > 0 for r in rep["items"]["1_line_state_diff_rate"])

    # ⑤ 內外卦：60 → 50（ge55 → mid）
    rep = run_days(base_days, after_with(inner=50.0))
    assert any(r["diff_rate"] and r["diff_rate"] > 0 for r in rep["items"]["5_trigram_state_diff_rate"])

    # ⑥ 主卦：kw 1 → 2
    rep = run_days(base_days, after_with(kw=2))
    assert any(r["king_wen_same_rate"] == 0.0 for r in rep["items"]["6_hexagram_agreement"])

    # ⑧ binding：floor 1 → 0
    rep = run_days(base_days, after_with(floor=0))
    r8 = [r for r in rep["items"]["8_binding_rate"] if r["column"] == "floor_applied"]
    assert r8 and all(r["before"] == 1.0 and r["after"] == 0.0 for r in r8)

    # ③ 旗標：個股 overheated 0 → 1
    rep = run_days(base_days, after_with(hot=1))
    r3 = [r for r in rep["items"]["3_flag_hit_rate"] if r["flag"] == "overheated"]
    assert r3 and all(r["before"] == 0.0 and r["after"] == 1.0 for r in r3)


# F3（續）：⑦ 名單／排名——把兩檔的分數對調，名單相同但排名全變
def test_item7_detects_rank_swap_without_membership_change():
    b = [[mrow(), srow("1101", base=50.0), srow("2330", base=80.0)]]
    a = [[mrow(), srow("1101", base=80.0), srow("2330", base=50.0)]]
    rep = run_days(b, a)
    r = [x for x in rep["items"]["7_candidate_overlap"] if x["direction"] == "long"][0]
    assert r["jaccard"] == 1.0, "成員沒變，Jaccard 應為 1"
    assert r["same_rank_rate"] == 0.0, "名次全對調，同名次率應為 0"


# F4：⑧ 的分母排除 None（None 是「不適用」不是「沒觸發」）
def test_binding_denominator_excludes_none():
    b = [[mrow(), srow("1101", floor=1), srow("2330", floor=None, base=80.0)]]
    rep = run_days(b, [list(d) for d in b])
    r = [x for x in rep["items"]["8_binding_rate"] if x["column"] == "floor_applied"][0]
    assert r["n_before"] == 1, f"分母應只算非 None 的那一列，實得 {r['n_before']}"
    assert r["before"] == 1.0, "1/1 才對；若把 None 當 0 會變成 0.5"


# F5：之卦是現算的，且零動爻時之卦＝主卦
def test_future_hexagram_is_computed_from_moving_lines():
    from iching.score.hexagram import king_wen_from_lines, lines_from_king_wen
    prev = (0, 1, 0, 1, 0, 1)
    cur = (1, 1, 0, 1, 0, 1)                      # 初爻動
    assert RT.moving_positions(prev, cur) == (1,)
    kw = king_wen_from_lines(list(cur))
    want = king_wen_from_lines([cur[0] ^ 1, *cur[1:]])
    assert RT.future_king_wen(kw, prev, cur) == want
    assert RT.future_king_wen(kw, cur, cur) == kw, "零動爻時之卦應等於主卦"
    assert RT.future_king_wen(None, prev, cur) is None
    del lines_from_king_wen


# 比率的邊界：分母為 0 回 None，不得寫 0.0（「沒有母體」與「比率為零」是兩件事）
def test_rate_returns_none_on_empty_denominator():
    assert RT.rate(0, 0) is None and RT.rate(1, 2) == 0.5
    assert RT._rel(0, 5) is None and RT._rel(4, 2) == -0.5
    assert RT._tv({}, {}) is None


# 兩側 params_sha 相同要拒比——防的正是「前側漏了 --uncalibrated」產出一份全零假報告
def test_refuses_when_both_sides_have_same_params_sha(tmp_path):
    import scan_features as SF
    import replay_scores as RP
    from synth_db import build_full

    cache = tmp_path / "cache"
    build_full(cache)
    assert SF.main(["--cache-dir", str(cache), "--quiet", "--allow-short-warmup", "--warmup-days", "0"]) == 0
    a = tmp_path / "a.db"
    assert RP.main(["--cache-dir", str(cache), "--out", str(a), "--quiet"]) == 0
    assert RT.main(["--before", str(a), "--after", str(a), "--out", str(tmp_path / "r.json")]) == 2
    assert not (tmp_path / "r.json").exists()


# 端到端：真的兩份 db（舊 d vs 新 d），走完 run() 並讀回報告
def test_end_to_end_on_real_before_after_dbs(tmp_path):
    import json

    import scan_features as SF
    import replay_scores as RP
    from synth_db import build_full

    cache = tmp_path / "cache"
    build_full(cache)
    assert SF.main(["--cache-dir", str(cache), "--quiet", "--allow-short-warmup", "--warmup-days", "0"]) == 0
    before, after = tmp_path / "before.db", tmp_path / "after.db"
    assert RP.main(["--cache-dir", str(cache), "--out", str(before), "--quiet", "--uncalibrated"]) == 0
    assert RP.main(["--cache-dir", str(cache), "--out", str(after), "--quiet"]) == 0
    out = tmp_path / "rep.json"
    assert RT.main(["--before", str(before), "--after", str(after), "--out", str(out),
                    "--progress-every", "0"]) == 0
    rep = json.loads(out.read_text(encoding="utf-8"))
    assert rep["before"]["params_sha"] != rep["after"]["params_sha"]
    assert set(rep["items"]) == {"1_line_state_diff_rate", "2_hysteresis_flips", "3_flag_hit_rate",
                                 "4_moving_line_count_dist", "5_trigram_state_diff_rate",
                                 "6_hexagram_agreement", "7_candidate_overlap", "8_binding_rate"}
    # 裁定 #58：六項必須是 n/a，③⑦ 必須有 long／short
    for name in ("1_line_state_diff_rate", "2_hysteresis_flips", "4_moving_line_count_dist",
                 "5_trigram_state_diff_rate", "6_hexagram_agreement", "8_binding_rate"):
        assert rep["items"][name], f"{name} 是空的"
        assert {r["direction"] for r in rep["items"][name]} == {RT.NA}, name
    assert {r["direction"] for r in rep["items"]["7_candidate_overlap"]} <= set(RT.DIRECTIONS)
    assert out.with_suffix(".txt").exists()
