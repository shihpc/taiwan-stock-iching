"""`scripts/revalidate_thresholds.py`：規格 §16.5 門檻行為重驗八項（`docs/P3-CALIBRATION.md` §20 的 F1～F8）。

本檔的重點是 **F2／F3 這一對**：F2 證明「沒有差異時八項必須全零」（自洽），F3 證明「動了某一項的來源
欄位時，只有那一項會變」（鑑別力）。少了 F3，一個永遠回 0 的實作也能通過 F2。

**突變守門**（2026-09-22 驗收退回後補）：F2／F3 擋不住五種「量錯但仍自洽」的實作——②只算單側、
③ 大盤旗標命中數不累加、④ 分布桶錯置、⑦ 把名額乘數乘在池大小上（Jaccard 恆為 1）、⑥ 之卦退化成
昨日卦。每一種各有一支測試，測試名以 `test_guard_` 開頭。
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import revalidate_thresholds as RT  # noqa: E402

ZERO_STREAKS = "0,0,0,0,0,0"


def mrow(market="twse", horizon="mid", *, basic_state="S1", mult=1.0, active=None,
         bits=None, kw=None, inner=None, outer=None, streaks=ZERO_STREAKS):
    """大盤列。`flags` 帶 `basic_state`（⑦ 的 `N0` 由它決定）與逐方向的 `active`／`quota_multiplier`。"""
    act = {f: False for f in RT.FLAG_NAMES} if active is None else dict(active)
    return {"scope": "market_index", "stock_id": "__MARKET__", "market": market, "horizon": horizon,
            "flags": {"basic_state": basic_state,
                      "by_direction": {d: {"active": act, "quota_multiplier": mult,
                                           "threshold_shift_deciles": 0.0} for d in RT.DIRECTIONS}},
            "lines_formal": None if bits is None else [int(c) for c in bits], "king_wen": kw,
            "inner_trigram_score": inner, "outer_trigram_score": outer, "streaks": streaks,
            "base_score": None, "in_rank_pool": None,
            "floor_applied": None, "overheated": None, "overheat_cap_applied": None}


def srow(sid, *, bits="010101", kw=1, inner=60.0, outer=40.0, base=50.0, pool=1,
         floor=1, hot=0, cap=0, market="twse", horizon="mid", streaks=ZERO_STREAKS):
    return {"scope": "stock", "stock_id": sid, "market": market, "horizon": horizon,
            "lines_formal": [int(c) for c in bits], "king_wen": kw,
            "inner_trigram_score": inner, "outer_trigram_score": outer, "streaks": streaks,
            "base_score": base, "in_rank_pool": pool, "flags": None,
            "floor_applied": floor, "overheated": hot, "overheat_cap_applied": cap}


def run_days(before_days, after_days):
    acc = RT.Acc()
    prev = {"before": {}, "after": {}}
    for b, a in zip(before_days, after_days, strict=True):
        RT.step_day(acc, b, a, prev)
    return RT.build_report(acc)


def _day(**kw):
    return [mrow(), srow("1101", **kw), srow("2330", base=80.0, **{k: v for k, v in kw.items() if k != "base"})]


def _pick(rows, **eq):
    return [r for r in rows if all(r.get(k) == v for k, v in eq.items())]


# ---------------------------------------------------------------- F2 / F3 / F4

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
    r8 = _pick(rep["items"]["8_binding_rate"], column="floor_applied")
    assert r8 and all(r["before"] == 1.0 and r["after"] == 0.0 for r in r8)

    # ③ 旗標：個股 overheated 0 → 1
    rep = run_days(base_days, after_with(hot=1))
    r3 = _pick(rep["items"]["3_flag_hit_rate"], flag="overheated")
    assert r3 and all(r["before"] == 0.0 and r["after"] == 1.0 for r in r3)


# F3（續）：⑦ 名單／排名——把兩檔的分數對調，名單相同但排名全變
def test_item7_detects_rank_swap_without_membership_change():
    b = [[mrow(), srow("1101", base=50.0), srow("2330", base=80.0)]]
    a = [[mrow(), srow("1101", base=80.0), srow("2330", base=50.0)]]
    rep = run_days(b, a)
    r = _pick(rep["items"]["7_candidate_overlap"], direction="long")[0]
    assert r["jaccard"] == 1.0, "成員沒變，Jaccard 應為 1"
    assert r["same_rank_rate"] == 0.0, "名次全對調，同名次率應為 0"


# F4：⑧ 的分母排除 None（None 是「不適用」不是「沒觸發」）
def test_binding_denominator_excludes_none():
    b = [[mrow(), srow("1101", floor=1), srow("2330", floor=None, base=80.0)]]
    rep = run_days(b, [list(d) for d in b])
    r = _pick(rep["items"]["8_binding_rate"], column="floor_applied")[0]
    assert r["n_before"] == 1, f"分母應只算非 None 的那一列，實得 {r['n_before']}"
    assert r["before"] == 1.0, "1/1 才對；若把 None 當 0 會變成 0.5"


# ---------------------------------------------------------------- ⑥ 前瞻式之卦

# F5：之卦＝翻「待確認動爻」（裁定 #59），純函式層
def test_pending_king_wen_flips_only_pending_lines():
    from iching.score.hexagram import king_wen_from_lines, lines_from_king_wen

    assert RT.CONFIRM_DAYS == 2
    assert RT.pending_positions("0,1,0,0,1,0") == (2, 5)
    assert RT.pending_positions(ZERO_STREAKS) == ()
    for bad in (None, "", "0,0,0", "a,b,c,d,e,f"):
        assert RT.pending_positions(bad) is None, f"{bad!r} 應回 None，不得當成零待確認動爻"

    kw = 1                                            # 乾
    assert RT.pending_king_wen(kw, ZERO_STREAKS) == kw, "零待確認動爻 → 之卦＝主卦"
    want = king_wen_from_lines([b ^ (1 if i == 0 else 0) for i, b in enumerate(lines_from_king_wen(kw))])
    assert RT.pending_king_wen(kw, "1,0,0,0,0,0") == want
    assert RT.pending_king_wen(None, "1,0,0,0,0,0") is None
    assert RT.pending_king_wen(kw, None) is None, "streaks 解不出 → 不下判斷"


def test_guard_item6_is_forward_looking_not_yesterday():
    """⑥ 必須只看**今日的 `streaks`**。

    突變守門：若之卦改回「翻昨日→今日的位元差」（首版做法），改動昨日位元就會改動今日的之卦。
    本測試把兩側的**昨日**爻態造得不同、今日完全相同，斷言 ⑥ 的之卦一致率仍為 1。
    """
    b = [[mrow(), srow("1101", bits="000000")], [mrow(), srow("1101", bits="111111", streaks="1,0,0,0,0,0")]]
    a = [[mrow(), srow("1101", bits="111000")], [mrow(), srow("1101", bits="111111", streaks="1,0,0,0,0,0")]]
    rep = run_days(b, a)
    r = _pick(rep["items"]["6_hexagram_agreement"], scope="stock")[0]
    assert r["future_king_wen_same_rate"] == 1.0, "昨日不同、今日相同 → 前瞻式之卦必須一致"


def test_guard_item6_independent_of_item1():
    """主卦相同、只有 `streaks` 不同 → 主卦一致率 1、之卦一致率 0。

    這證明 ⑥ 的後半提供**獨立於前半**的資訊（首版退化成「主卦一致率落後一日」時做不到）。
    """
    b = [[mrow(), srow("1101", kw=1, streaks=ZERO_STREAKS)]]
    a = [[mrow(), srow("1101", kw=1, streaks="1,0,0,0,0,0")]]
    rep = run_days(b, a)
    r = _pick(rep["items"]["6_hexagram_agreement"], scope="stock")[0]
    assert r["king_wen_same_rate"] == 1.0
    assert r["future_king_wen_same_rate"] == 0.0, "待確認動爻不同 → 之卦必須不同"


# ---------------------------------------------------------------- ⑦ 名額口徑

def _pool_day(bases, *, basic_state="S4", mult=1.0):
    rows = [mrow(basic_state=basic_state, mult=mult)]
    rows += [srow(f"{9000 + i}", base=v) for i, v in enumerate(bases)]
    return rows


def test_guard_item7_quota_multiplies_n0_not_pool_size():
    """名額＝`floor(N0 × 連乘)`（S1 §A1.2），**不是** `池大小 × 連乘`。

    突變守門：造 8 檔的池、基本狀態 S4（做多 `N0`＝5），把第 5、6 名的分數對調。
    正確實作只取前 5 名 → 名單成員變動 → Jaccard < 1；若切法是「池大小 × 1.0」＝8，
    兩側名單都是全部 8 檔，Jaccard 恆為 1，這支就會紅。
    """
    before = [_pool_day([80, 79, 78, 77, 76, 75, 74, 73])]
    after = [_pool_day([80, 79, 78, 77, 75, 76, 74, 73])]     # 第 5、6 名對調
    rep = run_days(before, after)
    r = _pick(rep["items"]["7_candidate_overlap"], direction="long")[0]
    assert r["n_days"] == 1
    assert r["jaccard"] == 4 / 6, f"前 5 名只有 4 檔重疊，Jaccard 應為 4/6，實得 {r['jaccard']}"
    short = _pick(rep["items"]["7_candidate_overlap"], direction="short")[0]
    assert short["jaccard"] == 1.0, "做空 N0＝20 > 池大小 8，兩側名單皆為全池"


def test_guard_item7_quota_multiplier_actually_cuts():
    """`quota_multiplier` 0.5 → `floor(5 × 0.5)`＝2；把第 2、3 名對調即可看出。"""
    before = [_pool_day([80, 79, 78, 77, 76, 75, 74, 73], mult=0.5)]
    after = [_pool_day([80, 78, 79, 77, 76, 75, 74, 73], mult=0.5)]
    rep = run_days(before, after)
    r = _pick(rep["items"]["7_candidate_overlap"], direction="long")[0]
    assert r["jaccard"] == 1 / 3, f"前 2 名只有 1 檔重疊，實得 {r['jaccard']}"


def test_item7_skips_undetermined_basic_state():
    """基本狀態未定 → 當日不出名單（S1 §A1.1），整格跳過並單獨記次數，不得灌進 Jaccard。"""
    days = [_pool_day([80, 79, 78], basic_state="undetermined")]
    rep = run_days(days, [list(d) for d in days])
    r = _pick(rep["items"]["7_candidate_overlap"], direction="long")[0]
    assert r["n_days"] == 0 and r["days_state_undetermined"] == 1
    assert r["jaccard"] is None, "沒有母體時必須是 None，不得寫 1.0"


def test_guard_quota_not_carried_across_days():
    """名額乘數與基本狀態**不得跨日沿用**：第二日沒有大盤列 → 該日應判「未定」而不是沿用昨日。"""
    d1 = [mrow(basic_state="S4"), srow("1101", base=80.0)]
    d2 = [srow("1101", base=80.0)]                            # 大盤列缺席
    rep = run_days([d1, d2], [list(d1), list(d2)])
    r = _pick(rep["items"]["7_candidate_overlap"], direction="long")[0]
    assert r["n_days"] == 1 and r["days_state_undetermined"] == 1


# ---------------------------------------------------------------- ②③④ 突變守門

def test_guard_item2_counts_each_side_independently():
    """② 前後側各自累計翻轉數。突變守門：只算一側、或兩側共用一個計數器都會讓 before==after。"""
    b = [[mrow(), srow("1101", bits="000000")], [mrow(), srow("1101", bits="100000")]]
    a = [[mrow(), srow("1101", bits="000000")], [mrow(), srow("1101", bits="111000")]]
    rep = run_days(b, a)
    r = _pick(rep["items"]["2_hysteresis_flips"], scope="stock")[0]
    assert (r["before"], r["after"]) == (1, 3), f"前側 1 爻、後側 3 爻翻轉，實得 {r}"


def test_guard_item3_market_flag_hit_counter():
    """③ 的大盤旗標命中數要真的累加。突變守門：只累加分母（`flag_den`）不累加分子也能通過 F2。"""
    on = {f: (f == "F-臨界") for f in RT.FLAG_NAMES}
    b = [[mrow(), srow("1101")], [mrow(), srow("1101")]]
    a = [[mrow(active=on), srow("1101")], [mrow(active=on), srow("1101")]]
    rep = run_days(b, a)
    r = _pick(rep["items"]["3_flag_hit_rate"], flag="F-臨界", direction="long")[0]
    assert r["scope"] == "market"
    assert (r["before"], r["after"], r["n_before"]) == (0.0, 1.0, 2)


def test_guard_item4_buckets_are_not_mangled():
    """④ 的桶是「動爻數」本身。突變守門：桶值 off-by-one 或與側別錯置，TV 距離會不同。"""
    b = [[mrow(), srow("1101", bits="000000")], [mrow(), srow("1101", bits="100000")]]
    a = [[mrow(), srow("1101", bits="000000")], [mrow(), srow("1101", bits="110000")]]
    rep = run_days(b, a)
    r = _pick(rep["items"]["4_moving_line_count_dist"], scope="stock")[0]
    assert r["before"] == {"1": 1} and r["after"] == {"2": 1}, r
    assert r["tv_distance"] == 1.0


# ---------------------------------------------------------------- 大盤分組（裁定 #59）

def test_market_rows_form_their_own_group():
    """①②④⑤⑥ 也吃大盤列，但 `scope="market"` **另成一組**，不得混進個股平均。"""
    b = [[mrow(bits="000000", kw=2, inner=60.0, outer=40.0), srow("1101", bits="010101", kw=1)]]
    a = [[mrow(bits="111111", kw=1, inner=60.0, outer=40.0), srow("1101", bits="010101", kw=1)]]
    rep = run_days(b, a)
    assert rep["market_rows_matched"] == 1 and rep["rows_matched"] == 1
    mk = _pick(rep["items"]["1_line_state_diff_rate"], scope="market")[0]
    st = _pick(rep["items"]["1_line_state_diff_rate"], scope="stock")[0]
    assert mk["diff_rate"] == 1.0, "大盤六爻全不同"
    assert st["diff_rate"] == 0.0, "個股完全相同——證明兩組沒有互相污染"
    assert _pick(rep["items"]["6_hexagram_agreement"], scope="market")[0]["king_wen_same_rate"] == 0.0
    assert _pick(rep["items"]["6_hexagram_agreement"], scope="stock")[0]["king_wen_same_rate"] == 1.0


def test_market_rows_do_not_get_stock_only_items():
    """⑧ 與 ③ 的 `overheated` 在大盤列一律 NULL，不得出現 `scope="market"` 的列。"""
    days = [[mrow(bits="000000", kw=2), srow("1101")]]
    rep = run_days(days, [list(d) for d in days])
    assert _pick(rep["items"]["8_binding_rate"], scope="market") == []
    assert _pick(rep["items"]["3_flag_hit_rate"], scope="market", flag="overheated") == []


# ---------------------------------------------------------------- 邊界與端到端

# 比率的邊界：分母為 0 回 None，不得寫 0.0（「沒有母體」與「比率為零」是兩件事）
def test_rate_returns_none_on_empty_denominator():
    assert RT.rate(0, 0) is None and RT.rate(1, 2) == 0.5
    assert RT._rel(0, 5) is None and RT._rel(4, 2) == -0.5
    assert RT._tv({}, {}) is None


# 兩側 params_sha 相同要拒比——防的正是「前側漏了 --uncalibrated」產出一份全零假報告
def test_refuses_when_both_sides_have_same_params_sha(tmp_path):
    import replay_scores as RP
    import scan_features as SF
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

    import replay_scores as RP
    import scan_features as SF
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
    # 裁定 #59：大盤列真的被算進 ①②④⑤⑥
    assert rep["market_rows_matched"] > 0
    assert _pick(rep["items"]["1_line_state_diff_rate"], scope="market")
    assert out.with_suffix(".txt").exists()
