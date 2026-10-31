"""裁定 #77 T2 試算卦：`src/iching/trial.py` 純函式＋`scripts/build_trial.py`（真實資料整合，免 token 免網路）。

依據：docs/P4-PREVIEW.md §16／§16.8／§21。真實資料段以 repo 內最新正式卦為基底，夜盤與美股一律注入（不連外）；
重建約 1 分鐘、每次試算約 20 秒，故重建結果以 module fixture 共用。
"""
from __future__ import annotations

import copy
import dataclasses
import datetime as dt
import gzip
import importlib.util
import json
import sys
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from iching import trial as TR  # noqa: E402
from iching.score.hexagram import YANG, YIN  # noqa: E402
from iching.score.params import Rules  # noqa: E402

_spec = importlib.util.spec_from_file_location("build_trial", ROOT / "scripts" / "build_trial.py")
B = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(B)

TPE = dt.timezone(dt.timedelta(hours=8))


def tpe(s: str) -> dt.datetime:
    return dt.datetime.fromisoformat(s).replace(tzinfo=TPE)


# ---------------------------------------------------------------------------
# 純函式

@pytest.mark.parametrize("s,ok", [
    ("2026-11-03 08:00", True), ("2026-11-03 07:59", False), ("2026-11-03 09:59", True), ("2026-11-03 10:00", False),
    ("2026-11-03 08:15", True), ("2026-11-03 08:45", True), ("2026-11-03 07:10", False),   # 07:10 早晨補叫班不跑試算
    ("2026-10-31 08:15", True),                                                               # 週六（補週五那場）
    ("2026-11-01 08:15", False), ("2026-11-02 08:15", False),                                 # 週日、週一
])
def test_in_window(s, ok):
    assert TR.in_window(tpe(s)) is ok


def test_new_us_rows_filters_sorts_dedups():
    rows = [("2026-10-30", 2, 2, 2, 2), ("2026-10-29", 1, 1, 1, 1), ("2026-10-31", 3, 3, 3, 3),
            ("2026-10-30", 9, 9, 9, 9), ("2026-10-28", 0, 0, 0, 0)]
    out = TR.new_us_rows(rows, "2026-10-29", "2026-10-31")
    assert out == [("2026-10-30", 9, 9, 9, 9)]                      # > 視窗末、< 今日、同日取後者
    assert TR.new_us_rows(rows, None, "2026-10-30") == [("2026-10-28", 0, 0, 0, 0), ("2026-10-29", 1, 1, 1, 1)]


def test_oi_estimate():
    assert TR.oi_estimate(-92241.0, -1500) == -93741.0
    assert TR.oi_estimate(None, -1500) is None
    assert TR.oi_estimate(-1.0, None) is None
    assert TR.oi_estimate(float("nan"), 3) is None


@dataclasses.dataclass(frozen=True)
class _MI:   # MarketInputs 的最小替身：只要 trial_market_inputs 碰到的欄位
    tpe_date: str
    tpe_dates: list
    us_dates: list | None
    spx_close: np.ndarray | None
    spx_high: np.ndarray | None
    spx_low: np.ndarray | None
    sox_close: np.ndarray | None
    foreign_net_oi: np.ndarray | None


def _mi(**kw):
    base = dict(tpe_date="2026-10-30", tpe_dates=["2026-10-29", "2026-10-30"], us_dates=["2026-10-28", "2026-10-29"],
                spx_close=np.array([1.0, 2.0]), spx_high=np.array([1.5, 2.5]), spx_low=np.array([0.5, 1.5]),
                sox_close=np.array([10.0, 20.0]), foreign_net_oi=np.array([-5.0, -6.0]))
    base.update(kw)
    return _MI(**base)


def test_trial_market_inputs_no_change_is_identity():
    mi = _mi()
    out = TR.trial_market_inputs(mi, "2026-11-02", [], None)
    assert out == mi or all(np.array_equal(getattr(out, f.name), getattr(mi, f.name)) if isinstance(getattr(mi, f.name), np.ndarray)
                            else getattr(out, f.name) == getattr(mi, f.name) for f in dataclasses.fields(mi))
    assert out.tpe_date == "2026-10-30"                              # 沒有美股新列 → 不改台北日期（stale_days 不平白 +1）


def test_trial_market_inputs_appends_without_mutating():
    mi = _mi()
    out = TR.trial_market_inputs(mi, "2026-11-02", [("2026-10-30", 3.0, 3.5, 2.5, 30.0)], -7.5)
    assert out.tpe_date == "2026-11-02" and out.tpe_dates[-1] == "2026-11-02" and len(out.tpe_dates) == 3
    assert out.us_dates[-1] == "2026-10-30"
    assert list(out.spx_close) == [1.0, 2.0, 3.0] and list(out.spx_high) == [1.5, 2.5, 3.5]
    assert list(out.spx_low) == [0.5, 1.5, 2.5] and list(out.sox_close) == [10.0, 20.0, 30.0]
    assert list(out.foreign_net_oi) == [-5.0, -6.0, -7.5]           # 多接一天，不是替換最後一天
    assert mi.tpe_dates == ["2026-10-29", "2026-10-30"] and list(mi.foreign_net_oi) == [-5.0, -6.0]


def test_trial_market_inputs_missing_series_raises():
    with pytest.raises(ValueError):
        TR.trial_market_inputs(_mi(foreign_net_oi=None), "2026-11-02", [], -1.0)
    with pytest.raises(ValueError):
        TR.trial_market_inputs(_mi(us_dates=None), "2026-11-02", [("2026-10-30", 1, 1, 1, 1)], None)


def test_step_changed_only_steps_changed_lines():
    rules = Rules()
    prev = [(YANG, 3), (YIN, 2), (YANG, 1), (YIN, 4), (YIN, 1), (YANG, 2)]
    lo = [0.0] * 6                                                    # 全部極陰：若六爻都走就會翻好幾爻
    r = TR.step_changed(prev, lo, [6], rules)
    assert r["st"][:5] == [s for s, _ in prev[:5]] and r["sk"][:5] == [k for _, k in prev[:5]]
    assert prev == [(YANG, 3), (YIN, 2), (YANG, 1), (YIN, 4), (YIN, 1), (YANG, 2)]   # 不改輸入
    assert r["lf"] is not None and len(r["lf"]) == 6
    assert TR.step_changed(None, lo, [6], rules)["lf"] is None       # 前狀態未知 → 正式爻未定


def test_trial_market_inputs_shift_date_only():
    out = TR.trial_market_inputs(_mi(), "2026-11-02", [], None, shift_date=True)
    assert out.tpe_date == "2026-11-02" and out.tpe_dates[-1] == "2026-11-02"
    assert out.us_dates == ["2026-10-28", "2026-10-29"] and list(out.foreign_net_oi) == [-5.0, -6.0]


def test_us_shift_uses_us_asof_not_window_tail():
    # 視窗已含美國 T 日（10-30）那列（T 的原料包由隔晨補叫班抓），正式 us_asof(T)=10-29 → 不必抓新列也要換
    assert TR.us_shift("2026-10-30", "2026-11-02", ["2026-10-28", "2026-10-29", "2026-10-30"], []) == \
        ("2026-10-29", "2026-10-30", True)
    # 視窗末＝us_asof(T)，抓到新列 → 換
    assert TR.us_shift("2026-10-30", "2026-11-02", ["2026-10-28", "2026-10-29"], ["2026-10-30"]) == \
        ("2026-10-29", "2026-10-30", True)
    # 沒有更新的美股日 → 不換、試算 asof＝正式
    assert TR.us_shift("2026-10-30", "2026-11-02", ["2026-10-28", "2026-10-29"], []) == ("2026-10-29", "2026-10-29", False)
    assert TR.us_shift("2026-10-30", "2026-11-02", [], []) == (None, None, False)


def test_saturday_trial_stale_days_is_zero():
    """週六 08:15：T＝週五、T'＝週一，新美股列＝週五 → stale_days 0（不平白 +1）。"""
    from iching.score.market import stale_days
    tpe_dates = ["2026-10-28", "2026-10-29", "2026-10-30", "2026-11-02"]
    us = ["2026-10-27", "2026-10-28", "2026-10-29", "2026-10-30"]
    assert stale_days("2026-11-02", tpe_dates, us, "tpe_trading_days") == 0
    assert stale_days("2026-10-30", tpe_dates[:3], us[:3], "tpe_trading_days") == 0   # 對照：正式 T


def test_step_changed_skips_line_equal_to_formal():
    rules = Rules()
    prev = [(YANG, 3), (YIN, 2), (YANG, 1), (YIN, 4), (YIN, 1), (YANG, 2)]
    lo = [0.0] * 6
    r = TR.step_changed(prev, lo, [6], rules, formal=[1.0, 1.0, 1.0, 1.0, 1.0, 0.0])
    assert r["st"][5] == YANG and r["sk"][5] == 2 and r["flip"] == []     # 上爻分數與正式相同 → 不走
    r2 = TR.step_changed(prev, [None] * 6, [6], rules, formal=[None] * 6)
    assert r2["sk"][5] == 2


def _git(cwd, *a):
    import subprocess
    subprocess.run(["git", "-C", str(cwd), *a], check=True, capture_output=True,
                   env={"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t",
                        "GIT_COMMITTER_EMAIL": "t@t", "PATH": "/usr/bin:/bin", "HOME": str(cwd)})


def test_scores_ref(tmp_path):
    assert B.scores_ref(tmp_path, "2026-10-30") == "HEAD"               # 不是 git repo
    _git(tmp_path, "init", "-q")
    (tmp_path / "a.txt").write_text("x")
    _git(tmp_path, "add", "a.txt")
    _git(tmp_path, "commit", "-q", "-m", "c1")
    assert B.scores_ref(tmp_path, "2026-10-30") == "HEAD"               # 分數檔尚未 commit（本班剛產）
    p = tmp_path / "data" / "scores" / "2026-10-30.json"
    p.parent.mkdir(parents=True)
    p.write_text("{}")
    _git(tmp_path, "add", ".")
    _git(tmp_path, "commit", "-q", "-m", "c2")
    (tmp_path / "b.txt").write_text("y")
    _git(tmp_path, "add", ".")
    _git(tmp_path, "commit", "-q", "-m", "c3")
    import subprocess
    sha = subprocess.run(["git", "-C", str(tmp_path), "rev-parse", "HEAD~1"], capture_output=True, text=True).stdout.strip()
    assert B.scores_ref(tmp_path, "2026-10-30") == f"{sha}^"            # 寫入 T 的那個 commit 之父


# ---------------------------------------------------------------------------
# 真實資料整合

@pytest.fixture(scope="module")
def ctx():
    files = B.BI.list_score_files(ROOT / B.DC.SCORES_DIR)
    T = files[-1].name[:-5]
    T2 = B.TN.night_query_date(T, None)
    c = B.prepare(ROOT, now=tpe(f"{T2} 08:15"), holidays=None)
    assert c["early"] is None
    return c


def _night(net):
    return lambda q: {"status": "ok", "query_date": q, "net": net, "long": 10, "short": 20, "raw_sha256": "abc"}


def _no_night(q):
    return {"status": "no_data", "query_date": q}


def _us_row(ctx):
    """視窗最後美股日的下一個日曆日（< T'），收盤略動。"""
    wc = ctx["wc"]
    d = (dt.date.fromisoformat(wc.us_dates[-1]) + dt.timedelta(days=1)).isoformat()
    assert d < ctx["T2"]
    return d, lambda s, e: [(wc.us_dates[-1], 1, 1, 1, 1), (d, 6900.0, 6950.0, 6850.0, 7300.0), (ctx["T2"], 1, 1, 1, 1)]


@pytest.fixture(scope="module")
def both(ctx, tmp_path_factory):
    mp = pytest.MonkeyPatch()
    mp.setattr(B, "WEB_FILE", tmp_path_factory.mktemp("trial_none") / "trial.json")   # 無上一版
    d, us = _us_row(ctx)
    try:
        return d, B.run(ctx, night_fetch=_night(-1500), us_fetch=us)
    finally:
        mp.undo()


def test_real_both_changed_keeps_lines_1_to_4(ctx, both):
    d, res = both
    assert res["status"] == "ok" and res["changed_market_lines"] == [5, 6]
    assert res["us"]["rows_added"] == [d] and res["us"]["status"] == "ok" and res["us"]["asof_trial"] == d
    assert res["night"]["oi_est"] == res["night"]["oi_official_T"] - 1500
    assert len(res["market"]) == len(ctx["active"]) * 3
    for key, ent in res["market"].items():
        mk, h = key.split("|")
        ref = ctx["ref_by"][(mk, B.MARKET_STOCK_ID, h)]
        assert ent["l"][:4] == [B.BI.rnd(ref[f"line_{k}"], 1) for k in range(1, 5)]
        assert ent["us_asof"] == d
        assert ent["lf"] is None or len(ent["lf"]) == 6
    assert res["stock_mismatch"] == []


def test_real_stocks_cover_reference_and_keep_formal_1_to_5_state(ctx, both):
    _, res = both
    ref_sids = {sid for (mk, sid, h) in ctx["ref_by"] if sid != B.MARKET_STOCK_ID}
    assert set(res["stocks"]) == ref_sids
    cp = ctx["cross_post"]
    sid = sorted(res["stocks"])[0]
    for h, row in res["stocks"][sid].items():
        l6, bs, kw, kwp, lf, lp = row
        prev = cp.stock_lines.get(f"{sid}|{h}")
        if lf is not None and prev is not None and all(s is not None for s, _ in prev):
            assert lf[:5] == "".join("1" if s == YANG else "0" for s, _ in prev[:5])   # 1–5 爻沿用正式狀態


def test_real_unchanged_inputs_give_formal_values(ctx, tmp_path, monkeypatch):
    """夜盤＋美股都沒換到 → no_new_inputs，不計分。"""
    monkeypatch.setattr(B, "WEB_FILE", tmp_path / "trial.json")
    res = B.run(ctx, night_fetch=_no_night, us_fetch=lambda s, e: [])
    assert res["status"] == "no_new_inputs" and res["changed_market_lines"] == []
    assert "market" not in res


def test_real_us_fetch_error_is_status_not_crash(ctx, tmp_path, monkeypatch):
    monkeypatch.setattr(B, "WEB_FILE", tmp_path / "trial.json")

    def boom(s, e):
        raise RuntimeError("FinMind 402")
    res = B.run(ctx, night_fetch=_no_night, us_fetch=boom)
    assert res["us"]["status"] == "error" and "402" in res["us"]["error"] and res["status"] == "no_new_inputs"


def test_real_night_value_kept_from_earlier_band(ctx, both, tmp_path, monkeypatch):
    """08:45 班：同一組 T／T' 的上一版夜盤 ok → 沿用上一版的值，本班重抓只作對照。"""
    _, first = both
    p = tmp_path / "trial.json"
    chained = {**first, "band": "0845", "night": {**first["night"], "kept_from": "0815"}}   # 第三班：記最初那一班
    p.write_text(B.dumps({k: v for k, v in chained.items() if k not in ("night_compare", "stock_mismatch", "git_ref")}),
                 encoding="utf-8")
    monkeypatch.setattr(B, "WEB_FILE", p)
    res = B.run(ctx, night_fetch=_night(-999), us_fetch=lambda s, e: [])
    assert res["status"] == "ok" and res["changed_market_lines"] == [5]
    assert res["night"]["net"] == -1500 and res["night"]["kept_from"] == "0815"
    assert res["night_compare"]["net"] == -999
    assert res["night"]["oi_est"] == first["night"]["oi_est"]


def test_real_previous_band_for_other_target_is_not_kept(ctx, both, tmp_path, monkeypatch):
    _, first = both
    old = {**first, "target_date": "1999-01-01"}
    p = tmp_path / "trial.json"
    p.write_text(B.dumps({k: v for k, v in old.items() if k not in ("night_compare", "stock_mismatch", "git_ref")}),
                 encoding="utf-8")
    monkeypatch.setattr(B, "WEB_FILE", p)
    res = B.run(ctx, night_fetch=_no_night, us_fetch=lambda s, e: [])
    assert res["status"] == "no_new_inputs" and "kept_from" not in res["night"]


@pytest.mark.parametrize("patch", [{"generated_at": "1999-01-01T08:15:00+08:00"}, {"params_sha": "other"}])
def test_real_previous_band_other_day_or_params_is_not_kept(ctx, both, tmp_path, monkeypatch, patch):
    _, first = both
    p = tmp_path / "trial.json"
    p.write_text(B.dumps({k: v for k, v in {**first, **patch}.items() if k not in ("night_compare", "stock_mismatch", "git_ref")}),
                 encoding="utf-8")
    monkeypatch.setattr(B, "WEB_FILE", p)
    res = B.run(ctx, night_fetch=_no_night, us_fetch=lambda s, e: [])
    assert res["status"] == "no_new_inputs" and "kept_from" not in res["night"]


def test_real_window_already_has_newer_us_row_shifts_date_only(ctx, tmp_path, monkeypatch):
    """視窗本來就含比 us_asof(T) 新的美股列（T 原料包由隔晨補叫班抓）→ 不抓到新列也換上爻、1–5 爻＝正式。"""
    monkeypatch.setattr(B, "WEB_FILE", tmp_path / "trial.json")
    wc = ctx["wc"]
    d = (dt.date.fromisoformat(wc.us_dates[-1]) + dt.timedelta(days=1)).isoformat()

    class Proxy:
        def __init__(self, w):
            self._w = w

        def __getattr__(self, k):
            return getattr(self._w, k)

        @property
        def us_dates(self):
            return list(self._w.us_dates) + [d]

        def market_inputs(self, mk, t, cross):
            mi = self._w.market_inputs(mk, t, cross)
            ap = lambda a, v: np.concatenate([np.asarray(a, float), [v]])  # noqa: E731
            c = float(mi.spx_close[-1])
            return dataclasses.replace(mi, us_dates=list(mi.us_dates) + [d], spx_close=ap(mi.spx_close, c * 1.01),
                                       spx_high=ap(mi.spx_high, c * 1.015), spx_low=ap(mi.spx_low, c * 0.995),
                                       sox_close=ap(mi.sox_close, float(mi.sox_close[-1]) * 1.02))

    c2 = dict(ctx)
    c2["wc"] = Proxy(wc)
    calls = []
    res = B.run(c2, night_fetch=_no_night, us_fetch=lambda s, e: calls.append((s, e)) or [])
    assert calls and calls[0][0] > d                                   # 抓取從視窗已有那列之後起
    assert res["status"] == "ok" and res["changed_market_lines"] == [6]
    assert res["us"]["rows_added"] == [] and res["us"]["window_last"] == d
    assert res["us"]["asof_trial"] == d and res["us"]["asof_formal"] < d
    for key, ent in res["market"].items():
        mk, h = key.split("|")
        ref = ctx["ref_by"][(mk, B.MARKET_STOCK_ID, h)]
        assert ent["l"][:5] == [B.BI.rnd(ref[f"line_{k}"], 1) for k in range(1, 6)] and ent["us_asof"] == d
    assert res["stock_mismatch"] == []


def test_real_market_reference_mismatch_refuses(ctx, tmp_path, monkeypatch):
    monkeypatch.setattr(B, "WEB_FILE", tmp_path / "trial.json")
    bad = dict(ctx)
    bad["ref_by"] = dict(ctx["ref_by"])
    key = next(k for k in bad["ref_by"] if k[1] == B.MARKET_STOCK_ID)
    r = copy.deepcopy(bad["ref_by"][key])
    r["line_1"] = r["line_1"] + 1e-9
    bad["ref_by"][key] = r
    with pytest.raises(B.BuildTrialError, match="正式重算與分數檔不同"):
        B.run(bad, night_fetch=_night(-1500), us_fetch=lambda s, e: [])


def test_real_stock_mismatch_over_1pct_refuses(ctx, tmp_path, monkeypatch):
    monkeypatch.setattr(B, "WEB_FILE", tmp_path / "trial.json")
    bad = dict(ctx)
    bad["ref_by"] = dict(ctx["ref_by"])
    keys = sorted(k for k, r in bad["ref_by"].items()
                  if k[1] != B.MARKET_STOCK_ID and any(r[f"line_{i}"] is not None for i in range(1, 6)))
    for k in keys[: len(keys) // 50 + 2]:                             # 2% 以上
        r = copy.deepcopy(bad["ref_by"][k])
        j = next(i for i in range(1, 6) if r[f"line_{i}"] is not None)
        r[f"line_{j}"] = r[f"line_{j}"] + 1e-9
        bad["ref_by"][k] = r
    with pytest.raises(B.BuildTrialError, match="個股 1–5 爻與正式不同"):
        B.run(bad, night_fetch=_night(-1500), us_fetch=lambda s, e: [])


def test_formal_stale_when_target_before_today():
    files = B.BI.list_score_files(ROOT / B.DC.SCORES_DIR)
    T = files[-1].name[:-5]
    T2 = B.TN.night_query_date(T, None)
    later = (dt.date.fromisoformat(T2) + dt.timedelta(days=1)).isoformat()
    c = B.prepare(ROOT, now=tpe(f"{later} 08:15"), holidays=None)
    assert c["early"]["status"] == "formal_stale" and c["early"]["target_date"] == T2


# ---------------------------------------------------------------------------
# 寫檔與入口

def test_write_outputs_snapshot_written_once(both, tmp_path):
    _, res = both
    paths = B.write_outputs(tmp_path, res)
    web = json.loads((tmp_path / B.WEB_FILE).read_text(encoding="utf-8"))
    assert "night_compare" not in web and "git_ref" not in web and web["status"] == "ok"
    snap = tmp_path / B.SNAP_DIR / f"{res['target_date']}-{res['band']}.json.gz"
    assert snap in paths
    first = snap.read_bytes()
    with gzip.open(snap, "rt", encoding="utf-8") as f:
        assert json.load(f)["git_ref"] == res["git_ref"]
    paths2 = B.write_outputs(tmp_path, {**res, "generated_at": "changed"})
    assert snap not in paths2 and snap.read_bytes() == first          # 快照寫入後不覆寫
    assert json.loads((tmp_path / B.WEB_FILE).read_text(encoding="utf-8"))["generated_at"] == "changed"


def test_main_outside_window_writes_nothing(tmp_path, monkeypatch, capsys):
    class FakeDT(dt.datetime):
        @classmethod
        def now(cls, tz=None):
            return tpe("2026-11-02 08:15")                             # 週一 → 窗外
    monkeypatch.setattr(B.dt, "datetime", FakeDT)
    assert B.main(["--root", str(tmp_path)]) == 0
    assert "不在試算時窗" in capsys.readouterr().out
    assert not (tmp_path / "data").exists()


def test_main_other_exceptions_also_return_2(tmp_path, capsys):
    p = tmp_path / "data" / "scores" / "2026-10-30.json"
    p.parent.mkdir(parents=True)
    p.write_text("not json", encoding="utf-8")
    assert B.main(["--root", str(tmp_path), "--force"]) == 2
    assert "中止" in capsys.readouterr().err
    assert not (tmp_path / "data" / "web").exists() and not (tmp_path / "runs").exists()


def test_write_outputs_failure_leaves_nothing(both, tmp_path):
    _, res = both
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "web").write_text("我是檔案，不是目錄")             # web 那步 mkdir 會失敗
    with pytest.raises(B.BuildTrialError, match="寫檔失敗"):
        B.write_outputs(tmp_path, res)
    assert list((tmp_path / B.SNAP_DIR).glob("*")) == []                   # 快照的 .tmp 已清、正式檔未落地


def test_write_outputs_partial_write_failure_cleans_tmp(both, tmp_path):
    _, res = both
    bad = {**res, "night_compare": {"x": float("nan")}}               # 快照 dumps(allow_nan=False) 在 gzip 建檔後失敗
    with pytest.raises(B.BuildTrialError, match="寫檔失敗"):
        B.write_outputs(tmp_path, bad)
    assert list((tmp_path / B.SNAP_DIR).glob("*")) == []
    assert not (tmp_path / B.WEB_FILE).exists()


MINI = {"schema": 1, "status": "ok", "base_date": "2026-10-30", "target_date": "2026-11-02", "band": "0815"}


def test_write_outputs_web_partial_write_failure_cleans_tmp(tmp_path, monkeypatch):
    real = Path.write_text

    def half(self, data, *a, **k):
        if self.name.endswith(".tmp"):
            real(self, data[:5], *a, **k)
            raise OSError("disk full")
        return real(self, data, *a, **k)
    monkeypatch.setattr(Path, "write_text", half)
    with pytest.raises(B.BuildTrialError, match="寫檔失敗"):
        B.write_outputs(tmp_path, MINI)
    assert [p for p in tmp_path.rglob("*") if p.is_file()] == []           # 快照 .tmp 與 web .tmp 都清掉


def test_write_outputs_unserializable_cleans_tmp(tmp_path):
    with pytest.raises(B.BuildTrialError, match="TypeError"):
        B.write_outputs(tmp_path, {**MINI, "night_compare": {"x": object()}})
    assert [p for p in tmp_path.rglob("*") if p.is_file()] == []


def test_main_refusal_returns_2_and_writes_nothing(tmp_path, capsys):
    assert B.main(["--root", str(tmp_path), "--force"]) == 2           # 空目錄：沒有正式分數檔
    assert "中止" in capsys.readouterr().err
    assert not (tmp_path / "data" / "web").exists()
