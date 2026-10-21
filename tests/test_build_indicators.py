"""`scripts/build_indicators.py`（裁定 #75 後端，`docs/P4-PREVIEW.md` §13／§13.8 產物契約）。免 token 免網路。

- **合成世界**（`tests/synth_db.py`，同 `test_daily_core.py` 的種子＋每日班鏈）：逐列比對守門（相同→輸出、不同→剔除、
  >1%／大盤任一列不同→整批拒絕且舊檔不動）、`params_sha`／狀態快照不是 T 一律拒絕、決定性（跑兩次位元組相同）、
  拼回的二爻歷史＝每日班 T−1 實際狀態快照（逐鍵相等）。
- **單元**：四爻情境序與引擎 `volume_scenario_day` 逐日一致、顯示用換算的分母（§13.3：ATR÷T 日收盤、C÷MA−1、
  MA÷n 日前 MA−1、C÷前日收盤−1）、缺值代碼、`assemble.py` detail 缺值原因修正（§13.4 第 3 項）且非 detail 列不變。
- **2330 實例**（`tests/fixtures/ind_2330_2026-10-08.json.gz`：以 repo 內 2026-10-08 原料包重建視窗後抄出的 2330 短線
  `StockInputs`；產生方式＝`build_indicators.pre_state`＋`daily_core.rebuild_from_bundles`＋`stock_inputs`，與腳本同一條路徑）：
  重算六爻＝`data/scores/2026-10-08.json` 逐位相同，原始值與輔助值＝§13.3 表。
- **真實資料整合**（repo 內 `runs/collect/` 全部原料包，約 50 秒）：最新分數日全體重算；T＝2026-10-08 時另斷言 mismatch 0、
  §13.3 的 2330 數字與分片量級，並與已 commit 的 `data/web/ind/` 逐位元組比對。
"""
from __future__ import annotations

import gzip
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import build_indicators as BI  # noqa: E402
import export_seed as ES  # noqa: E402
import replay_scores as RP  # noqa: E402
import scan_features as SF  # noqa: E402
from conftest import synth_market_inputs, synth_stock_inputs  # noqa: E402
from iching import bundle_io as B  # noqa: E402
from iching import calendar as CAL  # noqa: E402
from iching import daily_core as DC  # noqa: E402
from iching import replay_io as RIO  # noqa: E402
from iching import replay_state as RS  # noqa: E402
from iching.score import StockInputs, assemble_row, build_params, score_market, score_stock  # noqa: E402
from iching.score.indicators import atr14_prev, atr_series_prev  # noqa: E402
from iching.score.params import HORIZONS, SCOPE_STOCK  # noqa: E402
from iching.score.stock import volume_scenario_day  # noqa: E402
from iching.score.transform import Missing, S_clip  # noqa: E402
from synth_db import DAYS, DV, build_full  # noqa: E402

WINDOW = 30
K = 60
N_CHAIN = 12                       # 種子之後走 12 個每日班（分數檔 12 份，足夠補二爻歷史最舊一格）


# ---------------------------------------------------------------------------
# 合成世界
@pytest.fixture(scope="module")
def chain(tmp_path_factory) -> dict:
    base = tmp_path_factory.mktemp("ind")
    cache, repo = base / "cache", base / "repo"
    build_full(cache)
    assert SF.main(["--cache-dir", str(cache), "--quiet", "--allow-short-warmup", "--warmup-days", "0"]) == 0
    assert RP.main(["--cache-dir", str(cache), "--window", str(WINDOW), "--to", DAYS[K], "--quiet"]) == 0
    state_k = base / "state_k.json"
    shutil.copy(cache / "scores.db.state.json", state_k)
    src = RIO.ReplaySource(cache, DV, window=WINDOW)
    CAL.write_calendar_json(repo / DC.CALENDAR_TPE_FILE, CAL.calendar_payload("tpe", src.trading_dates(), DV))
    src.close()
    assert ES.main(["--cache-dir", str(cache), "--out", str(repo), "--window", str(WINDOW), "--state", str(state_k)]) == 0
    snaps: dict[str, str] = {}
    src = RIO.ReplaySource(cache, DV, window=WINDOW)
    last = K + N_CHAIN
    for i, d in enumerate(DAYS[: last + 1]):
        b = src.read_day(d)
        if i > K:
            B.write_bundle(repo, b)
            DC.run_offline(repo, d, window=WINDOW)
            snaps[d] = (repo / DC.STATE_FILE).read_text(encoding="utf-8")
    src.close()
    return {"repo": repo, "snaps": snaps, "T": DAYS[last], "T_prev": DAYS[last - 1]}


def _copy(chain, tmp_path) -> Path:
    dst = tmp_path / "repo"
    shutil.copytree(chain["repo"], dst)
    return dst


def _scores_path(repo: Path, T: str) -> Path:
    return repo / DC.SCORES_DIR / f"{T}.json"


def _tamper(repo: Path, T: str, pred) -> dict:
    p = _scores_path(repo, T)
    js = json.loads(p.read_text(encoding="utf-8"))
    for r in js["rows"]:
        if pred(r):
            k = next((k for k in range(1, 7) if r[f"line_{k}"] is not None), None)
            if k is None:
                r["line_1"] = 50.0                     # 全爻未知的列：補一個數也是「不同」
            else:
                r[f"line_{k}"] = r[f"line_{k}"] + 1e-9   # 最小擾動：逐位比對必須抓得到
            p.write_text(json.dumps(js, ensure_ascii=False), encoding="utf-8")
            return r
    raise AssertionError("找不到要擾動的列")


def test_identical_rows_are_all_output(chain, tmp_path):
    out = tmp_path / "ind"
    paths, res = BI.build(chain["repo"], out)
    assert res["T"] == chain["T"] and res["mismatch"] == [] and res["n_ref"] > 0
    ref = json.loads(_scores_path(chain["repo"], chain["T"]).read_text(encoding="utf-8"))
    stock_rows = [r for r in ref["rows"] if r["stock_id"] != "__MARKET__"]
    mkt_rows = [r for r in ref["rows"] if r["stock_id"] == "__MARKET__"]
    assert res["n_ref"] == len(stock_rows)
    got = sum(1 for e in res["stocks"].values() for h in HORIZONS if e[h] is not None)
    assert got == len(stock_rows)
    m = json.loads((out / "market.json").read_text(encoding="utf-8"))
    assert sorted(m["rows"]) == sorted(f"{r['market']}|{r['horizon']}" for r in mkt_rows)
    for name in [p.name for p in paths]:
        d = json.loads((out / name).read_text(encoding="utf-8"))
        assert d["schema"] == BI.SCHEMA and d["date"] == chain["T"] and d["params_sha"] == ref["params_sha"]
        assert d["mismatch"] == 0 and d["rows_ref"] == len(stock_rows) and d["price_basis"] == "adjusted_back"
        assert set(d["miss"]) == set(BI.MISS_CODES.values()) | {BI.MISS_UNKNOWN}
        rows = d["rows"].items() if name == "market.json" else (
            (f"{e['m']}|{h}", e[h]) for e in d["stocks"].values() for h in HORIZONS if e[h] is not None)
        for key, (cid, xs, aux) in rows:
            cs = d["cols"][str(cid)]
            assert cs["h"] == key.split("|")[1]
            assert len(xs) == len(cs["x"]) and len(aux) == len(cs["a"])
            for col in cs["x"]:
                assert col in d["win"][key]
        # 禁止欄：不得出現子指標分數／d／權重／clipped（§13.2 第 3 項）
        blob = (out / name).read_text(encoding="utf-8")
        for bad in ('"score"', '"clipped"', '"sub_weight"', '"native"'):
            assert bad not in blob


def test_win_comes_from_paramset(chain, tmp_path):
    out = tmp_path / "ind"
    BI.build(chain["repo"], out)
    for f in out.iterdir():
        d = json.loads(f.read_text(encoding="utf-8"))
        for key, tbl in d["win"].items():
            mk, h = key.split("|")
            ps = build_params(mk)
            scope = "market_index" if f.name == "market.json" else SCOPE_STOCK
            for col, (w, unit, dp) in tbl.items():
                p = ps.params[(scope, h, col[0], col[1], col[3:])]
                assert w == BI._window_json(p.window) and unit == p.unit and dp == BI.x_dp(p.indicator_id, p.unit)


def test_deterministic_two_runs_bytewise(chain, tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    BI.build(chain["repo"], a)
    BI.build(chain["repo"], b)
    na, nb = sorted(p.name for p in a.iterdir()), sorted(p.name for p in b.iterdir())
    assert na == nb and len(na) >= 2
    for n in na:
        assert (a / n).read_bytes() == (b / n).read_bytes()
        assert (a / n).read_bytes().endswith(b"\n")


def test_pre_state_equals_daily_snapshot_of_T_minus_1(chain):
    """C1：拼回的二爻歷史（個股＋大盤）＝每日班計分 T 那一刻吃的狀態（T−1 快照），逐鍵相等。"""
    repo, T = chain["repo"], chain["T"]
    files = BI.list_score_files(repo / DC.SCORES_DIR)
    ref = BI.load_scores(files[-1])
    post = RS.CrossDayState.from_json(chain["snaps"][T])
    prev = RS.CrossDayState.from_json(chain["snaps"][chain["T_prev"]])
    st, diag = BI.pre_state(post, ref["rows"], list(reversed(files[:-1])))
    assert diag["t_mismatch"] == [] and diag["overlap_mismatch"] == [] and diag["hist_uncertain"] == []
    n_full = 0
    for r in ref["rows"]:
        key = f"{r['market'] if r['stock_id'] == '__MARKET__' else r['stock_id']}|{r['horizon']}"
        if r["stock_id"] == "__MARKET__":
            assert st.market_line2.get(key, []) == prev.market_line2.get(key, [])
            assert st.market_line2_t_minus_5(r["market"], r["horizon"]) == prev.market_line2_t_minus_5(r["market"], r["horizon"])
            n_full += len(prev.market_line2.get(key, [])) == RS.MARKET_LINE2_HIST
        else:
            assert st.stock_line2.get(key, []) == prev.stock_line2.get(key, [])
            assert st.stock_line2_history(r["stock_id"], r["horizon"]) == prev.stock_line2_history(r["stock_id"], r["horizon"])
            n_full += len(prev.stock_line2.get(key, [])) == RS.STOCK_LINE2_HIST
    assert n_full > 0                                # 至少有滿格的鍵：最舊一格確實由分數檔補回


def test_mismatch_row_dropped_under_threshold(chain, tmp_path, monkeypatch):
    repo = _copy(chain, tmp_path)
    r = _tamper(repo, chain["T"], lambda r: r["stock_id"] != "__MARKET__" and r["horizon"] == "swing")
    monkeypatch.setattr(BI, "MISMATCH_MAX_RATIO", 1.0)       # 合成世界只有十來檔，1 列就 > 1%；放寬門檻驗「剔除＋照常輸出」
    out = tmp_path / "ind"
    _, res = BI.build(repo, out)
    assert res["mismatch"] == [f"{r['stock_id']}|swing"]
    d = json.loads((out / f"{r['stock_id'][:2]}.json").read_text(encoding="utf-8"))
    assert d["mismatch"] == 1
    e = d["stocks"][r["stock_id"]]
    assert e["swing"] is None and e["short"] is not None and e["mid"] is not None


def test_mismatch_over_threshold_refuses_and_keeps_old_files(chain, tmp_path, capsys):
    repo = _copy(chain, tmp_path)
    out = repo / "data" / "web" / "ind"
    assert BI.main(["--root", str(repo)]) == 0
    before = {p.name: p.read_bytes() for p in out.iterdir()}
    sentinel = out / "zz.json"                     # 已存在的舊分片：拒絕時也不得被刪
    sentinel.write_text("{}\n", encoding="utf-8")
    _tamper(repo, chain["T"], lambda r: r["stock_id"] != "__MARKET__")
    assert BI.main(["--root", str(repo)]) == 2
    assert "整批不輸出" in capsys.readouterr().err
    after = {p.name: p.read_bytes() for p in out.iterdir()}
    assert after == {**before, "zz.json": b"{}\n"}


def test_market_row_mismatch_refuses_whole_batch(chain, tmp_path):
    repo = _copy(chain, tmp_path)
    _tamper(repo, chain["T"], lambda r: r["stock_id"] == "__MARKET__")
    with pytest.raises(BI.BuildIndicatorsError, match="大盤"):
        BI.compute(repo)
    assert BI.main(["--root", str(repo)]) == 2
    assert not (repo / "data" / "web" / "ind").exists()


def _drop_row(repo: Path, T: str, pred) -> dict:
    p = _scores_path(repo, T)
    js = json.loads(p.read_text(encoding="utf-8"))
    i = next(i for i, r in enumerate(js["rows"]) if pred(r))
    gone = js["rows"].pop(i)
    p.write_text(json.dumps(js, ensure_ascii=False), encoding="utf-8")
    return gone


def test_same_lines_missing_reference_is_never_equal():
    row = {f"line_{k}": None for k in range(1, 7)}
    assert BI.same_lines(row, None) is False                    # 全 None 的重算列對「參考缺列」也不得判相同
    assert BI.same_lines(row, dict(row)) is True


def test_reference_missing_market_row_refuses_and_keeps_old_files(chain, tmp_path, capsys):
    repo = _copy(chain, tmp_path)
    out = repo / "data" / "web" / "ind"
    assert BI.main(["--root", str(repo)]) == 0
    before = {p.name: p.read_bytes() for p in out.iterdir()}
    gone = _drop_row(repo, chain["T"], lambda r: r["stock_id"] == "__MARKET__")
    with pytest.raises(BI.BuildIndicatorsError, match="參考無此列"):
        BI.compute(repo)
    assert BI.main(["--root", str(repo)]) == 2
    assert f"{gone['market']}|{gone['horizon']}（參考無此列）" in capsys.readouterr().err
    assert {p.name: p.read_bytes() for p in out.iterdir()} == before


def test_reference_missing_stock_row_counts_as_mismatch(chain, tmp_path, monkeypatch):
    repo = _copy(chain, tmp_path)
    gone = _drop_row(repo, chain["T"], lambda r: r["stock_id"] != "__MARKET__" and r["horizon"] == "mid")
    res = BI.compute(repo)
    assert res["mismatch"] == [f"{gone['stock_id']}|mid（參考無此列）"] and res["n_extra"] == 1
    assert res["stocks"][gone["stock_id"]]["mid"] is None and res["stocks"][gone["stock_id"]]["short"] is not None
    with pytest.raises(BI.BuildIndicatorsError, match="整批不輸出"):   # 合成世界 1 列即 > 1%
        BI.check_mismatch(res)
    monkeypatch.setattr(BI, "MISMATCH_MAX_RATIO", 1.0)
    BI.check_mismatch(res)


def test_params_sha_mismatch_refuses(chain, tmp_path):
    repo = _copy(chain, tmp_path)
    p = _scores_path(repo, chain["T"])
    js = json.loads(p.read_text(encoding="utf-8"))
    js["params_sha"] = "000000000000"
    p.write_text(json.dumps(js), encoding="utf-8")
    with pytest.raises(BI.BuildIndicatorsError, match="params_sha"):
        BI.compute(repo)


def test_state_snapshot_must_be_T(chain, tmp_path):
    repo = _copy(chain, tmp_path)
    (repo / DC.STATE_FILE).write_text(chain["snaps"][chain["T_prev"]], encoding="utf-8")
    with pytest.raises(BI.BuildIndicatorsError, match="last_date"):
        BI.compute(repo)


def test_stale_shards_removed_only_on_success(chain, tmp_path):
    out = tmp_path / "ind"
    out.mkdir()
    (out / "zz.json").write_text("{}\n", encoding="utf-8")
    (out / "keep.txt").write_text("x", encoding="utf-8")
    BI.build(chain["repo"], out)
    assert not (out / "zz.json").exists() and (out / "keep.txt").exists()


# ---------------------------------------------------------------------------
# pre_state 單元（不靠合成世界）
def _rows(entries):
    return [{"stock_id": sid, "market": mk, "horizon": h, "line_2": v} for sid, mk, h, v in entries]


def test_pre_state_short_chain_needs_no_files():
    post = RS.CrossDayState(last_date="T")
    for v in (10.0, 20.0, 30.0):
        post.push_stock_line2("1101", "short", v)
    st, diag = BI.pre_state(post, _rows([("1101", "twse", "short", 30.0)]), [], loader=lambda p: pytest.fail("不應讀檔"))
    assert st.stock_line2["1101|short"] == [10.0, 20.0] and diag["hist_uncertain"] == []


def test_pre_state_full_chain_takes_oldest_from_files_and_flags_gaps():
    post = RS.CrossDayState(last_date="T")
    hist = [float(i) for i in range(1, 11)]          # 10 次 push：1..10，快照只留 2..10
    for v in hist:
        post.push_stock_line2("1101", "mid", v)
        post.push_stock_line2("2330", "mid", v)
    days = {f"d{i}": [{"stock_id": "1101", "market": "twse", "horizon": "mid", "line_2": v}] for i, v in enumerate(hist[:-1])}
    # 2330 只在最近 3 個分數檔出現 → 補不到最舊一格
    for i in (6, 7, 8):
        days[f"d{i}"].append({"stock_id": "2330", "market": "twse", "horizon": "mid", "line_2": hist[i]})
    files_desc = [Path(f"d{i}") for i in range(8, -1, -1)]
    st, diag = BI.pre_state(post, _rows([("1101", "twse", "mid", 10.0), ("2330", "twse", "mid", 10.0)]), files_desc,
                            loader=lambda p: {"rows": days[p.name]})
    assert st.stock_line2["1101|mid"] == hist[:-1]    # T−9…T−1＝1..9
    assert diag["hist_uncertain"] == ["2330|mid"] and st.stock_line2_history("2330", "mid")[0] is None
    assert diag["overlap_mismatch"] == ["2330|mid"]   # 分數檔只有 3 格、與快照 8 格對不齊


def test_pre_state_detects_T_mismatch():
    post = RS.CrossDayState(last_date="T")
    post.push_market_line2("twse", "short", 50.0)
    _, diag = BI.pre_state(post, _rows([("__MARKET__", "twse", "short", 51.0)]), [])
    assert diag["t_mismatch"] == ["twse|short"]


# ---------------------------------------------------------------------------
# 四爻情境序 ＝ 引擎同一組條件
def _native_from_seq(info, rules):
    s_pull, s_up, s_dn, s_neu = rules.vs_scores
    if info["seq"] is None:
        return None
    if info["seq"] == 1:
        return s_pull
    s = S_clip(info["vr"] - 1.0, rules.vs_ratio_c, rules.vs_ratio_d).native
    if info["seq"] == 2:
        return s_up + rules.vs_formula_half * (s - rules.hysteresis_first)
    if info["seq"] == 3:
        return s_dn - rules.vs_formula_half * (s - rules.hysteresis_first)
    return s_neu


def test_volume_scenario_seq_matches_engine_day_by_day():
    rules = build_params("twse").rules
    seen = set()
    for seed in range(12):
        rng = np.random.default_rng(seed)
        n = 120
        c = 100 + np.cumsum(rng.normal(0.05 * (seed % 3 - 1), 1.2, n))
        h, lo = c + rng.uniform(0.1, 2, n), c - rng.uniform(0.1, 2, n)
        v = rng.uniform(300, 3000, n) * np.where(rng.uniform(0, 1, n) < 0.15, 3.0, 1.0)
        v[rng.integers(0, n, 3)] = 0.0                 # 無成交日 → 引擎缺值
        atrs = atr_series_prev(h, lo, c, rules.atr_method)
        for i in range(0, n):
            for l2 in (None, 40.0, 60.0):
                for n_dd in (5, 10):
                    eng = volume_scenario_day(c, v, atrs, i, n_dd, l2, rules)
                    info = BI.volume_scenario_seq(c, v, atrs, i, n_dd, l2, rules)
                    got = _native_from_seq(info, rules)
                    if isinstance(eng, Missing):
                        assert got is None, (seed, i, l2, n_dd, info)
                    else:
                        assert got == eng, (seed, i, l2, n_dd, info, eng)
                    seen.add(info["seq"])
    assert seen == {None, 1, 2, 3, 4, 5}


# ---------------------------------------------------------------------------
# 顯示用換算的分母（§13.3）與 x 的對應
def test_aux_denominators_and_x_consistency(ps_twse):
    si = synth_stock_inputs()
    ss = score_stock(si, ps_twse, "short")
    row = assemble_row(ss, ps_twse, DV, "t", detail=True)
    xs, xv = BI.x_columns(row, ps_twse, SCOPE_STOCK, "short")
    an, av = BI.stock_aux(si, ss, row, ps_twse, "short", eps_period=None)
    a = dict(zip(an, av))
    c = np.asarray(si.close, float)
    atr = atr14_prev(si.high, si.low, si.close, "simple")
    assert a["2.atr_pct"] == BI.rnd(atr / c[-1] * 100, 2)                                  # ATR14 ÷ T 日收盤
    assert a["2.dev_s_pct"] == BI.rnd((c[-1] / c[-5:].mean() - 1) * 100, 2)                # C ÷ MA5 − 1
    assert a["2.dev_l_pct"] == BI.rnd((c[-1] / c[-20:].mean() - 1) * 100, 2)               # C ÷ MA20 − 1
    assert a["2.slope_pct"] == BI.rnd((c[-20:].mean() / c[-25:-5].mean() - 1) * 100, 2)    # MA20 ÷ 5 日前 MA20 − 1
    assert a["4.day_pct"] == BI.rnd((c[-1] / c[-2] - 1) * 100, 2)                          # C ÷ 前日收盤 − 1
    assert a["5.mret"] == BI.rnd((c[-1] / c[-6] - 1) * 100, 2)                             # 融資同期（5 日）報酬
    f, v = np.asarray(si.foreign_net_shares, float), np.asarray(si.volume, float)
    assert a["5.f_l"] == BI.rnd(f[-5:].sum(), 0) and a["5.v_s"] == BI.rnd(v[-3:].sum(), 0)
    assert a["5.fbuy"] == int((f[-5:] > 0).sum())
    x = dict(zip(xs, xv))
    assert x["5C.foreign_persistence"] == BI.rnd(a["5.fbuy"] - 5 / 2, 1)                    # 「5 日中買超 m 日」＝x + n/2
    sub = row["line_2_families"]["A"]["subs"]["dist_ma_long"]["x"]                          # (C − MA20)/ATR ↔ 距均線 %
    assert abs(sub * atr / c[-20:].mean() * 100 - (c[-1] / c[-20:].mean() - 1) * 100) < 1e-9
    ind = row["line_5_families"]["A"]["subs"]["foreign_strength_long"]["x"]
    assert abs(ind - f[-5:].sum() / v[-5:].sum() * 100) < 1e-9
    assert a["1.rev_ym"] == max(ym for ym, _ in si.monthly_revenue)


def test_missing_codes_and_null_for_scenarios(ps_twse):
    si = synth_stock_inputs(margin_balance=None, margin_eligible=False, shares_outstanding=None, close=None)
    row = assemble_row(score_stock(si, ps_twse, "short"), ps_twse, DV, "t", detail=True)
    x = dict(zip(*BI.x_columns(row, ps_twse, SCOPE_STOCK, "short")))
    assert x["5D.margin_scenario"] == "E"                 # 無信用交易資格（ind_margin_scenario 先判 eligible）
    assert x["5E.short_sale_change"] == "M"               # shares_outstanding 缺
    assert x["2A.dist_ma_long"] == "M"                    # close 缺
    si2 = synth_stock_inputs(n=12)
    x2 = dict(zip(*BI.x_columns(assemble_row(score_stock(si2, ps_twse, "short"), ps_twse, DV, "t", detail=True),
                                 ps_twse, SCOPE_STOCK, "short")))
    assert x2["2A.dist_ma_long"] == "H"                   # 歷史不足
    ok = dict(zip(*BI.x_columns(assemble_row(score_stock(synth_stock_inputs(), ps_twse, "short"), ps_twse, DV, "t", detail=True),
                                 ps_twse, SCOPE_STOCK, "short")))
    assert ok["2C.structure"] is None and ok["4C.continuation"] is None and ok["4A.volume_scenario"] is None   # 情境分沒有 x


# ---------------------------------------------------------------------------
# C4：assemble.py detail 缺值原因（§13.4 第 3 項）
def test_detail_missing_reason_is_reported(ps_twse):
    si = synth_stock_inputs(margin_balance=None, margin_eligible=False)
    row = assemble_row(score_stock(si, ps_twse, "short"), ps_twse, DV, "t", detail=True)
    fam = row["line_5_families"]["D"]
    assert fam["score"] is None and fam["missing"] == {"missing": "not_eligible", "detail": "無信用交易資格"}
    assert fam["subs"]["margin_scenario"]["missing"] == {"missing": "not_eligible", "detail": "無信用交易資格"}
    ok = row["line_5_families"]["A"]["subs"]["foreign_strength_long"]
    assert ok["missing"] is None and isinstance(ok["x"], float)


def test_non_detail_row_unchanged_by_fix(ps_twse, ps_tpex):
    """非 detail 列＝detail 列去掉 `*_families`／`*_meta`（修正只動 detail 分支），市場與個股、三期間皆然。"""
    for ps, mk in ((ps_twse, "twse"), (ps_tpex, "tpex")):
        for h in HORIZONS:
            for sc in (score_market(synth_market_inputs(market=mk), ps, h),
                       score_stock(synth_stock_inputs(market=mk, margin_balance=None, margin_eligible=False), ps, h)):
                plain = assemble_row(sc, ps, DV, "t")
                det = assemble_row(sc, ps, DV, "t", detail=True)
                stripped = {k: v for k, v in det.items() if not (k.endswith("_families") or k.endswith("_meta"))}
                assert plain == stripped


# ---------------------------------------------------------------------------
# 2330 實例（§13.3 表）
def _load_2330():
    d = json.loads(gzip.decompress((ROOT / "tests" / "fixtures" / "ind_2330_2026-10-08.json.gz").read_bytes()))
    inp = dict(d["inputs"])
    arr = lambda v: None if v is None else np.array([np.nan if x is None else x for x in v], dtype=float)   # noqa: E731
    for k in ("open", "high", "low", "close", "volume", "index_close", "foreign_net_shares", "trust_net_shares",
              "margin_balance", "short_sale_balance"):
        inp[k] = arr(inp[k])
    for k in ("industry_median_return", "industry_above_ma_ratio"):
        inp[k] = {int(a): b for a, b in inp[k].items()}
    inp["monthly_revenue"] = [tuple(x) for x in inp["monthly_revenue"]]
    return d, StockInputs(**inp)


def test_2330_short_matches_scores_and_section_13_3():
    d, si = _load_2330()
    ps = build_params("twse")
    assert ps.model_version() == d["model_version"]
    ss = score_stock(si, ps, "short")
    row = assemble_row(ss, ps, DV, "t", detail=True)
    assert BI.lines_of(row) == d["ref_lines"]                                    # 與 data/scores/2026-10-08.json 逐位相同
    x = dict(zip(*BI.x_columns(row, ps, SCOPE_STOCK, "short")))
    a = dict(zip(*BI.stock_aux(si, ss, row, ps, "short", eps_period=None)))
    want_x = {"1A.revenue_yoy": 53.32, "1A.revenue_accel": 24.45, "2A.dist_ma_short": -0.24, "2A.dist_ma_long": 2.03,
              "2B.ma_long_slope": 0.96, "2C.structure": None, "3A.excess_long": 0.49, "3A.excess_short": -0.39,
              "3B.excess_vs_industry": 2.66, "3C.excess_accel": -1.27, "4A.volume_scenario": None, "4B.close_position": 0.581,
              "4C.continuation": None, "5A.foreign_strength_long": -10.90, "5A.foreign_strength_short": -24.86,
              "5B.trust_strength_long": 1.79, "5B.trust_strength_short": 1.67, "5C.foreign_persistence": -1.5,
              "5D.margin_scenario": 3.03, "5E.short_sale_change": -0.0085, "6A.market_direction": 51.74,
              "6B.industry_relative_return": -2.79, "6B.industry_above_ma20_ratio": 0.708}
    assert x == want_x
    want_a = {"1.rev_ym": "2026-08", "2.atr_pct": 1.46, "2.dev_s_pct": -0.35, "2.dev_l_pct": 3.04, "2.slope_pct": 1.47,
              "2.struct": "other", "3.ret_l": 3.66, "3.idx_l": 3.17, "3.ret_s": 1.59, "3.idx_s": 1.99, "3.ind_med_l": 0.99,
              "3.ind_n": 96, "3.cap": 0, "4.vr": 1.05, "4.dchg": -0.94, "4.day_pct": -1.35, "4.cont": "pending",
              "4.cont_off": 2, "5.f_l": -11282, "5.f_s": -15139, "5.t_l": 1857, "5.t_s": 1014, "5.v_l": 103481,
              "5.v_s": 60888, "5.fbuy": 1, "5.m0": 30658, "5.m1": 31586, "5.mret": 1.59, "5.mseq": 2, "5.ss0": 14198514,
              "5.ss1": 11985514, "5.shares": 25932370067, "6.ind_med": -0.8, "6.idx": 1.99}
    assert {k: a[k] for k in want_a} == want_a
    atr = atr14_prev(si.high, si.low, si.close, "simple")
    assert round(atr, 2) == 42.30 and round(float(si.close[-1]), 2) == 2903.93     # §13.3：ATR14＝42.30、後復權收盤 2,903.93
    assert d["raw_close"] == 2550.0                                                   # 原料包原始收盤（價格水準顯示用）


# ---------------------------------------------------------------------------
# 真實資料整合（repo 內原料包；約 50 秒）
@pytest.fixture(scope="module")
def real(tmp_path_factory):
    out = tmp_path_factory.mktemp("ind_real")
    res = BI.compute(ROOT)
    BI.check_mismatch(res)
    payloads = BI.build_payloads(res)
    BI.write_all(out, payloads)
    return res, out


def test_real_latest_day_rebuild_matches_scores(real):
    res, out = real
    assert len(res["mismatch"]) <= BI.MISMATCH_MAX_RATIO * res["n_ref"]
    assert len(res["market_rows"]) == 6
    files = sorted(p.name for p in out.iterdir())
    assert "market.json" in files and 40 <= len(files) <= 120
    sizes = [(out / f).stat().st_size for f in files]
    assert max(sizes) < 400_000 and sum(sizes) < 8_000_000


def test_real_2026_10_08_section_13_3_and_committed_bytes(real):
    res, out = real
    if res["T"] != "2026-10-08":
        pytest.skip(f"§13.3 實例只對 2026-10-08 有效（現在最新分數日 {res['T']}；同一組數字另由 2330 fixture 測試常駐守住）")
    assert res["mismatch"] == [] and res["n_ref"] == 5838 and len(res["stocks"]) == 1946
    d = json.loads((out / "23.json").read_text(encoding="utf-8"))
    cid, xs, aux = d["stocks"]["2330"]["short"]
    cs = d["cols"][str(cid)]
    x = dict(zip(cs["x"], xs))
    a = dict(zip(cs["a"], aux))
    assert x["1A.revenue_yoy"] == 53.32 and x["5E.short_sale_change"] == -0.0085 and x["6A.market_direction"] == 51.74
    assert a["2.atr_pct"] == 1.46 and a["5.m1"] == 31586 and a["5.ss1"] == 11985514 and d["stocks"]["2330"]["c"] == 2550.0
    committed = ROOT / "data" / "web" / "ind"
    if committed.is_dir() and json.loads((committed / "market.json").read_text(encoding="utf-8")).get("date") == res["T"]:
        assert sorted(p.name for p in committed.iterdir()) == sorted(p.name for p in out.iterdir())
        for p in out.iterdir():
            assert (committed / p.name).read_bytes() == p.read_bytes(), p.name


def test_daily_workflow_runs_indicators_before_web_without_blocking_commit():
    import yaml
    d = yaml.safe_load((ROOT / ".github/workflows/daily.yml").read_text(encoding="utf-8"))
    steps = d["jobs"]["daily"]["steps"]
    idx = {i: s for i, s in enumerate(steps)}
    i_ind = next(i for i, s in idx.items() if "build_indicators.py" in (s.get("run") or ""))
    i_web = next(i for i, s in idx.items() if "build_web.py" in (s.get("run") or ""))
    i_commit = next(i for i, s in idx.items() if "git add data runs/collect" in (s.get("run") or ""))
    i_red = next(i for i, s in idx.items() if "steps.ind.outputs.ok == '0'" in (s.get("if") or ""))
    i_web_red = next(i for i, s in idx.items() if "steps.web.outputs.ok == '0'" in (s.get("if") or ""))
    # build_web 與 build_indicators 同時失敗：web 紅燈步驟 exit 1 後，ind 紅燈仍要跑（!cancelled()），兩則都要出現；
    # PR-75e：不用 always()（run 被取消時也會跑），改 GitHub 文件建議的 !cancelled()（`!` 開頭須 ${{ }} 包住）
    # PR-B（§7.8.10）：再加 outcome=='success'——ind 被跳過時 outputs.ok 是 null，`null == '0'` 在 GitHub 表達式會轉數字成立
    assert steps[i_red]["if"].replace(" ", "") == "${{!cancelled()&&steps.ind.outcome=='success'&&steps.ind.outputs.ok=='0'}}"
    assert "always()" not in steps[i_red]["if"]
    assert i_commit < i_web_red < i_red == len(steps) - 2             # 步驟序 ind → web → commit → web 紅燈 → ind 紅燈 → notify
    ind = steps[i_ind]
    assert i_ind < i_web < i_commit < i_red < len(steps) - 1           # 在 build_web 之前、commit 之後才紅、notify 仍是最後一步
    assert ind.get("id") == "ind" and "continue-on-error" not in ind
    assert 'echo "ok=0"' in ind["run"] and 'echo "ok=1"' in ind["run"]
    assert "git checkout -- data/web/ind/" in ind["run"] and "git clean -fdq -- data/web/ind/" in ind["run"]
    assert "exit 1" in steps[i_red]["run"] and "continue-on-error" not in steps[i_red]
    # PR-75e：Commit & push 失敗時 ind 紅燈仍會跑，訊息不得無條件宣稱已 push——依 steps.commit.outcome 分兩種措辭
    assert steps[i_commit].get("id") == "commit"
    assert steps[i_red]["env"]["COMMIT_OUTCOME"].replace(" ", "") == "${{steps.commit.outcome}}"
    assert "已 commit 並 push" not in steps[i_red]["run"]

    def red(outcome):
        p = subprocess.run(["bash", "-eo", "pipefail", "-c", steps[i_red]["run"]], capture_output=True, text=True,
                           env={**os.environ, "COMMIT_OUTCOME": outcome})
        return p.returncode, p.stdout
    rc, out = red("success")
    assert rc == 1 and out.startswith("::error::") and "Commit & push 步驟成功" in out and "可能未進 main" not in out
    for oc in ("failure", "skipped", ""):
        rc, out = red(oc)
        assert rc == 1 and out.startswith("::error::") and "可能未進 main" in out and "步驟成功" not in out, oc
    # build_web 紅燈：if 無狀態函式＝隱含 success()（commit 成功才跑、取消不跑），故「已 commit 並 push」措辭成立、不需分支
    web_if = steps[i_web_red]["if"]
    assert not any(f in web_if for f in ("always()", "cancelled()", "failure()", "success()"))


# ---------------------------------------------------------------------------
# PR-75f：原料包集合＝當班計分（修剪前）那一組（`BI.scoring_bundles`；docs/P4-PREVIEW.md §13.8 第 8 點）
# 合成世界的 repo 在 `chain` 裡已逐日計分到 T（＝每日班「計分」那一步），之後才 `git commit`（＝本班開跑前的 HEAD 視角：
# 修剪前的原料包都在 git 裡）再呼叫真的 `daily_pipeline.prune_bundles`（＝每日班計分後那一步）——即 2026-10-13 的情境。
from iching import daily_pipeline as DP  # noqa: E402


def _g(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@example.invalid",
                           "-c", "commit.gpgsign=false", *args], check=True, capture_output=True, text=True).stdout


def _git_snapshot(repo: Path, *, exclude: tuple[str, ...] = ()) -> None:
    _g(repo, "init", "-q")
    _g(repo, "add", "-A")
    for rel in exclude:
        _g(repo, "rm", "-q", "--cached", rel)
    _g(repo, "commit", "-q", "-m", "snapshot")


def _bytes_of(held) -> list[tuple[str, bytes]]:
    return [(d, B.dumps(x if isinstance(x, RS.DayBundle) else B.read_bundle(x))) for d, x in held]


def _pruned(chain, tmp_path, *, exclude_T: bool = False):
    repo = _copy(chain, tmp_path)
    T = chain["T"]
    _git_snapshot(repo, exclude=(f"{B.BUNDLE_DIR}/{T}-{B.BAND}.json.gz",) if exclude_T else ())
    files = B.list_bundles(repo)
    pre = _bytes_of(files)                                        # 計分當下的原料包（＝修剪前）
    head_w1 = B.dumps(B.read_bundle(files[1][1]))
    pr = DP.prune_bundles(repo, keep=len(files) - 1, window=WINDOW)
    assert pr["deleted"] == 1 and pr["first"] == files[1][0]
    # 前提：修剪確實改寫了新最舊包（否則「改寫包用修剪前版本」的測試是空的）
    assert B.dumps(B.read_bundle(B.bundle_path(repo, files[1][0]))) != head_w1
    return repo, T, pre, files


def test_scoring_bundles_restores_pruned_set_bitwise(chain, tmp_path):
    repo, T, pre, files = _pruned(chain, tmp_path)
    held, ents, diag = BI.scoring_bundles(repo, T)
    assert diag["status"] == "restored" and diag["restored"] == [files[0][0]] and diag["first_replaced"] == files[1][0]
    assert _bytes_of(held) == pre                                 # 集合與內容＝修剪前逐位相同
    assert ents == {}
    # 整條重算：產物與「沒修剪」的同一個 repo 逐位元組相同、不同列 0
    out_pruned, out_ref = tmp_path / "o1", tmp_path / "o2"
    _, res = BI.build(repo, out_pruned)
    assert res["mismatch"] == [] and res["bundles"]["status"] == "restored"
    BI.build(chain["repo"], out_ref)
    assert sorted(p.name for p in out_pruned.iterdir()) == sorted(p.name for p in out_ref.iterdir())
    for p in out_ref.iterdir():
        assert (out_pruned / p.name).read_bytes() == p.read_bytes(), p.name


def test_scoring_bundles_keeps_collect_updates_over_git(chain, tmp_path):
    """collect 正當更新：本班新寫的包（git 沒有）與本班重寫的近期包（含只改 us／fx——外形與修剪改寫相同）一律用工作區版本。"""
    repo, T, _pre, files = _pruned(chain, tmp_path, exclude_T=True)
    d_recent = files[-2][0]
    b = B.read_bundle(files[-2][1])
    b.us = list(b.us) + [("2099-01-01",) + tuple(b.us[-1][1:])] if b.us else [("2099-01-01", 1.0)]
    B.write_bundle(repo, b)
    held, _ents, diag = BI.scoring_bundles(repo, T)
    assert diag["status"] == "restored"
    by = dict(held)
    assert by[T] == B.bundle_path(repo, T)                       # git 沒有的當日包：工作區
    assert by[d_recent] == B.bundle_path(repo, d_recent)         # 只改 us／fx 的近期包：仍是工作區（不是最舊包，不認為是修剪）
    assert isinstance(by[files[0][0]], RS.DayBundle) and isinstance(by[files[1][0]], RS.DayBundle)
    assert [d for d, _ in held] == [d for d, _ in files if d <= T]


def test_scoring_bundles_not_pure_prune_falls_back_to_worktree(chain, tmp_path, capsys):
    repo, T, _pre, files = _pruned(chain, tmp_path)
    w0 = files[1][0]
    b = B.read_bundle(B.bundle_path(repo, w0))
    b.vix = (b.vix or 0.0) + 1.0                                  # us／fx 以外的欄位也變了＝不是 prune 的改寫
    B.write_bundle(repo, b)
    held, ents, diag = BI.scoring_bundles(repo, T)
    assert diag["status"] == "not-pure-prune" and ents == {}
    assert held == [(d, p) for d, p in B.list_bundles(repo) if d <= T]
    assert "不是單純修剪改寫" in capsys.readouterr().err


def test_scoring_bundles_git_unavailable_warns_and_uses_worktree(chain, tmp_path, capsys, monkeypatch):
    repo, T, _pre, _files = _pruned(chain, tmp_path)
    work = [(d, p) for d, p in B.list_bundles(repo) if d <= T]
    # ① ref 不存在
    held, _e, diag = BI.scoring_bundles(repo, T, git_ref="no-such-ref")
    assert diag["status"] == "git-unavailable" and held == work
    assert "無法自 git no-such-ref" in capsys.readouterr().err
    # ② 物件讀不到（淺 clone／partial clone 的形狀）：git show 失敗
    real_git = BI._git

    def no_show(root, *args):
        if args[0] == "show":
            raise BI.GitUnavailable("git show exit 128：fatal: missing blob")
        return real_git(root, *args)
    monkeypatch.setattr(BI, "_git", no_show)
    held, _e, diag = BI.scoring_bundles(repo, T)
    assert diag["status"] == "git-unavailable" and held == work
    assert "missing blob" in capsys.readouterr().err
    monkeypatch.undo()
    # ③ 非 git 目錄
    plain = tmp_path / "plain"
    shutil.copytree(repo, plain, ignore=shutil.ignore_patterns(".git"))
    held, _e, diag = BI.scoring_bundles(plain, T)
    assert diag["status"] == "git-unavailable" and held == [(d, p) for d, p in B.list_bundles(plain) if d <= T]
    assert "照舊以工作區" in capsys.readouterr().err


def test_scoring_bundles_no_prune_is_unchanged(chain, tmp_path):
    """離線重算（未修剪、工作區＝git）：回傳與舊實作（`list_bundles` ≤ T 的路徑）完全相同的集合。"""
    repo = _copy(chain, tmp_path)
    _git_snapshot(repo)
    T = chain["T"]
    held, ents, diag = BI.scoring_bundles(repo, T)
    assert diag["status"] == "no-prune" and ents == {} and diag["restored"] == []
    assert held == [(d, p) for d, p in B.list_bundles(repo) if d <= T]


def test_scoring_bundles_restores_pruned_entrants_only(chain, tmp_path):
    repo = _copy(chain, tmp_path)
    T = chain["T"]
    files = B.list_bundles(repo)
    w1 = files[1][0]
    DC.write_entrant(repo, DC.Entrant(stock_id="9901", data_version=DV, frm=files[0][0], to=files[0][0], days={}))
    DC.write_entrant(repo, DC.Entrant(stock_id="9902", data_version=DV, frm=files[0][0], to=w1, days={}))
    _git_snapshot(repo)
    pr = DP.prune_bundles(repo, keep=len(files) - 1, window=WINDOW)
    assert pr["entrants_deleted"] == 1                            # 9901（to 早於新最舊包）被 prune_entrants 刪
    (repo / DC.ENTRANTS_DIR / "9902.json.gz").unlink()            # 9902 不是修剪刪的（to ＝ 新最舊包）
    _held, ents, diag = BI.scoring_bundles(repo, T)
    assert diag["status"] == "restored" and sorted(ents) == ["9901"] and diag["entrants_restored"] == ["9901"]
    assert ents["9901"].to == files[0][0]
