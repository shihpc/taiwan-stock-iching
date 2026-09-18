"""`scripts/export_dataset.py`（`docs/P3-DATASET.md` §2 A7）＋`scripts/hetzner_dataset.sh`（§2 B1）的離線測試——合成世界、免 token。

世界＝`synth_db.build_full` 七檔 ＋ `add_pit_rows` 三檔（Z 轉市、W 入池、Y 在 DAYS[YD] 起沒有列＝下市），跑 `scan_features`＋
`replay_scores` 全程得 `scores.db`；日曆由 `ReplaySource.trading_dates()` 寫成 repo 的 `data/calendar_tpe.json`。三段切點常數
`SEGMENTS` 在 fixture 內改成合成日曆的兩半（train＝DAYS[0..49]、valid＝DAYS[50..79]），測完還原。

- ① 同鍵對帳：匯出列的 `(date, market, stock_id, horizon)` 與前九欄逐列＝`scores.db`（SQL 直讀）＝`export_scores.py` 匯的
  `data/scores/<T>.json`（該檔已與 `run_offline` 位元組相同，`tests/test_export_scores.py`）。
- ② `fwd_ret`／`mkt_ret_h` 與**獨立手算**（raw SQL dict ＋ `adjust.cumulative_factors`／`factor_at`）逐位相同，含正常、跨除權息
  （1101 第 40 日除息、係數 1.25）、halt（1102 第 30／31 日停牌）、no_entry、delist（Y）、末日截斷六種案例。
- ③ gzip 逐位可重現：兩個目錄各跑一次 sha256 相同；同目錄重跑「已相同」不動檔。
- ④ 突變全紅：h 改錯、少乘係數、用 T 收盤進場、四捨五入位數改——手算對帳的 mismatch 由 0 變非 0。
- ⑤ rc：目標存在且不同無 `--force` → rc 1 不覆蓋；`params_sha` 非現行碼／非 PIT → rc 2；db 缺／`open` 欄缺／日曆缺日 → rc 2。
"""
from __future__ import annotations

import csv
import gzip
import hashlib
import io
import json
import math
import re
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import adj_event_report as AER  # noqa: E402
import export_dataset as EXP  # noqa: E402
import export_scores as EX  # noqa: E402
import replay_scores as RP  # noqa: E402
import scan_features as SF  # noqa: E402
from iching import calendar as CAL  # noqa: E402
from iching import daily_core as DC  # noqa: E402
from iching import replay_io as RIO  # noqa: E402
from iching.adjust import Event, cumulative_factors, factor_at  # noqa: E402
from iching.score.assemble import MARKET_STOCK_ID  # noqa: E402
from iching.scores_io import ScoreStore  # noqa: E402
from synth_db import (CAPRED_I, DAYS, DV, EX_I, MALFORMED_I, PAR_I, PIT_Y, SPLIT_I, SUSPEND_I,  # noqa: E402
                      add_adjust_source_rows, add_pit_rows, build_full)

WINDOW = 30
WC, ZC, YD = 63, 66, 68                       # W 轉上櫃、Z 轉上市、Y 下市（同 test_pit_world 的相對位置）
SEG = {"train": (DAYS[0], DAYS[49]), "valid": (DAYS[50], DAYS[79])}
SCORE_DAYS = DAYS[40:43]                       # export_scores 匯三日分數檔供 ① 對帳
H = {"short": 10, "swing": 20, "mid": 40}


@pytest.fixture(scope="module")
def world(tmp_path_factory) -> dict:
    base = tmp_path_factory.mktemp("dataset")
    cache, repo = base / "cache", base / "repo"
    build_full(cache)
    add_pit_rows(cache, z_c=ZC, w_c=WC, y_d=YD)
    add_adjust_source_rows(cache)                                        # 裁定 #51：1101 減資 60／2330 分割 65／6488 面額 70（valid 段）
    assert SF.main(["--cache-dir", str(cache), "--quiet", "--allow-short-warmup", "--warmup-days", "0"]) == 0
    assert RP.main(["--cache-dir", str(cache), "--window", str(WINDOW), "--quiet"]) == 0
    src = RIO.ReplaySource(cache, DV, window=WINDOW)
    cal = src.trading_dates()
    CAL.write_calendar_json(repo / DC.CALENDAR_TPE_FILE, CAL.calendar_payload("tpe", cal, DV))
    src.close()
    assert cal == DAYS
    assert EX.main(["--cache-dir", str(cache), "--out", str(repo), "--from", SCORE_DAYS[0], "--to", SCORE_DAYS[-1]]) == 0
    saved = dict(EXP.SEGMENTS)
    EXP.SEGMENTS.clear()
    EXP.SEGMENTS.update(SEG)
    yield {"cache": cache, "repo": repo, "cal": cal}
    EXP.SEGMENTS.clear()
    EXP.SEGMENTS.update(saved)


def _export(world, out: Path, *extra: str) -> int:
    return EXP.main(["--cache-dir", str(world["cache"]), "--out", str(out), "--calendar", str(world["repo"] / DC.CALENDAR_TPE_FILE), *extra])


def _read(out: Path, name: str) -> list[dict[str, str]]:
    with gzip.open(out / EXP.OUT_DIR / name, "rt", encoding="utf-8", newline="") as g:
        rows = list(csv.DictReader(g))
    return rows


def _all_rows(out: Path) -> dict[tuple, dict[str, str]]:
    got: dict[tuple, dict[str, str]] = {}
    for s in SEG:
        for hz in H:
            for r in _read(out, EXP.file_name(s, hz)):
                key = (r["date"], r["market"], r["stock_id"], r["horizon"])
                assert key not in got, key
                assert (s == "train") == (r["date"] <= SEG["train"][1]) and r["horizon"] == hz, (s, hz, key)
                got[key] = r
    return got


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


# ---------------------------------------------------------------------------
# 獨立手算（raw SQL dict；不用 export_dataset 的任何函式）
def _fmt(v: float) -> str:
    return repr(round(v, 6) + 0.0)


def hand_calc(cache: Path, cal: list[str]) -> dict[tuple, dict[str, str]]:
    """對 scores.db 每一個個股列鍵，用原始價格列手算五個欄位（規則照 export_dataset.py 檔頭表）。"""
    pc = sqlite3.connect(f"file:{cache / 'prices.db'}?mode=ro", uri=True)
    px: dict[str, dict[str, tuple[float | None, float | None, float | None]]] = {}
    for sid, d, o, c, v in pc.execute("SELECT stock_id, date, open, close, Trading_Volume FROM raw_price_daily WHERE data_version=?", (DV,)):
        px.setdefault(str(sid), {})[str(d)] = (o, c, v)
    idx: dict[str, dict[str, tuple[float, float]]] = {"twse": {}, "tpex": {}}
    for sid, d, o, c in pc.execute("SELECT stock_id, date, open, close FROM raw_index_price WHERE data_version=?", (DV,)):
        idx[{"TAIEX": "twse", "TPEx": "tpex"}[str(sid)]][str(d)] = (float(o), float(c))
    # 四源手算（裁定 #51；規則照 docs/P3-DATASET.md §7.3 C 文字，不用 factor_sources）：除權息／減資／分割各自一個事件，
    # 面額變更與分割同 (stock_id, date) 者只算分割那一列；同日多事件相乘（cumulative_factors 本來就相乘）
    events: dict[str, list[Event]] = {}
    for sid, d, b, a in pc.execute("SELECT stock_id, date, before_price, after_price FROM raw_dividend_result WHERE data_version=?", (DV,)):
        events.setdefault(str(sid), []).append(Event(str(d), float(b), float(a)))
    for sid, d, b, a in pc.execute("SELECT stock_id, date, ClosingPriceonTheLastTradingDay, PostReductionReferencePrice "
                                   "FROM raw_cap_reduction WHERE data_version=?", (DV,)):
        events.setdefault(str(sid), []).append(Event(str(d), float(b), float(a)))
    split_keys = set()
    for sid, d, b, a in pc.execute("SELECT stock_id, date, before_price, after_price FROM raw_split_price WHERE data_version=?", (DV,)):
        events.setdefault(str(sid), []).append(Event(str(d), float(b), float(a)))
        split_keys.add((str(sid), str(d)))
    for sid, d, b, a in pc.execute("SELECT stock_id, date, before_close, after_ref_close FROM raw_par_value_change WHERE data_version=?", (DV,)):
        if (str(sid), str(d)) not in split_keys:
            events.setdefault(str(sid), []).append(Event(str(d), float(b), float(a)))
    pc.close()
    fac = {sid: cumulative_factors(evs) for sid, evs in events.items()}
    data_end = min(cal[-1], max(idx["twse"]))
    n_end = cal.index(data_end)

    def traded(sid: str, d: str) -> bool:
        r = px.get(sid, {}).get(d)
        return bool(r) and (r[1] or 0) > 0 and (r[2] or 0) > 0

    def adj(sid: str, d: str, raw: float) -> float:
        return raw * factor_at(d, *fac[sid]) if sid in fac else raw

    def prev_close(sid: str, i: int) -> float | None:
        for j in range(i - 1, -1, -1):
            if traded(sid, cal[j]):
                return float(px[sid][cal[j]][1])
        return None

    def mkt(market: str, e: str, x: str) -> str:
        if e not in idx[market] or x not in idx[market]:
            return ""
        return _fmt(idx[market][x][1] / idx[market][e][0] - 1)

    out: dict[tuple, dict[str, str]] = {}
    sc = ScoreStore(cache / "scores.db", readonly=True)
    keys = [tuple(map(str, r)) for r in sc.conn.execute(
        "SELECT s.date, s.market, s.stock_id, s.horizon FROM scores s JOIN versions v ON v.version_id=s.version_id "
        "WHERE v.data_version=? AND s.stock_id<>?", (DV, MARKET_STOCK_ID))]
    sc.close()
    for d, market, sid, hz in keys:
        i = cal.index(d)
        e, x = i + 1, i + 1 + H[hz]
        r = {"fwd_ret": "", "mkt_ret_h": "", "exit_reason": "", "entry_limit_up": "", "exit_limit_down": ""}
        if e <= n_end:
            row_e = px.get(sid, {}).get(cal[e])
            ok = traded(sid, cal[e]) and row_e[0] is not None and float(row_e[0]) > 0
            if ok:
                pcl = prev_close(sid, e)
                r["entry_limit_up"] = "" if pcl is None else ("1" if float(row_e[0]) >= round(pcl * 1.1, 2) else "0")
            else:
                r["exit_reason"] = "no_entry"
            if x <= n_end:
                if not ok:
                    r["mkt_ret_h"] = mkt(market, cal[e], cal[x])
                else:
                    xp = next(j for j in range(x, e - 1, -1) if traded(sid, cal[j]))
                    if xp != x:
                        last = max(cal.index(dd) for dd in px[sid] if dd in cal)
                        r["exit_reason"] = "delist" if (last < x and last < n_end) else "halt"
                    r["fwd_ret"] = _fmt(adj(sid, cal[xp], float(px[sid][cal[xp]][1])) / adj(sid, cal[e], float(row_e[0])) - 1)
                    pcl = prev_close(sid, xp)
                    r["exit_limit_down"] = "" if pcl is None else ("1" if float(px[sid][cal[xp]][1]) <= round(pcl * 0.9, 2) else "0")
                    r["mkt_ret_h"] = mkt(market, cal[e], cal[xp])
        out[(d, market, sid, hz)] = r
    return out


def mismatches(got: dict[tuple, dict[str, str]], hand: dict[tuple, dict[str, str]]) -> list[tuple]:
    assert set(got) == set(hand)
    bad = []
    for k, h in hand.items():
        g = {c: got[k][c] for c in h}
        if g != h:
            bad.append((k, g, h))
    return bad


# ---------------------------------------------------------------------------
def test_export_keys_and_score_columns_match_db_and_daily_files(world, tmp_path):
    out = tmp_path / "out"
    assert _export(world, out) == 0
    got = _all_rows(out)
    sc = ScoreStore(world["cache"] / "scores.db", readonly=True)
    try:
        rows = sc.conn.execute(
            "SELECT s.date, s.market, s.stock_id, s.horizon, s.base_score, s.in_rank_pool, s.coverage, s.king_wen, s.lines_formal "
            "FROM scores s JOIN versions v ON v.version_id=s.version_id WHERE v.data_version=? AND s.stock_id<>?", (DV, MARKET_STOCK_ID)).fetchall()
        n_mkt = sc.conn.execute("SELECT COUNT(*) FROM scores WHERE stock_id=?", (MARKET_STOCK_ID,)).fetchone()[0]
    finally:
        sc.close()
    assert len(rows) == len(got) and len(rows) > 1000
    for r in rows:
        g = got[tuple(map(str, r[:4]))]
        assert [g[c] for c in EXP.SCORE_COLS[4:]] == ["" if v is None else (repr(v) if isinstance(v, float) else str(v)) for v in r[4:]], r
    # 與 export_scores 匯的 data/scores/<T>.json（＝run_offline 同形）逐列同鍵、同值；個股列數＝分數檔 stock 列數
    for T in SCORE_DAYS:
        js = json.loads(DC.scores_path(world["repo"], T).read_text(encoding="utf-8"))
        stk = [r for r in js["rows"] if r["stock_id"] != MARKET_STOCK_ID]
        assert stk and {k for k in got if k[0] == T} == {(r["date"], r["market"], r["stock_id"], r["horizon"]) for r in stk}
        for r in stk:
            g = got[(r["date"], r["market"], r["stock_id"], r["horizon"])]
            assert g["base_score"] == ("" if r["base_score"] is None else repr(r["base_score"]))
            assert g["in_rank_pool"] == str(r["in_rank_pool"]) and g["coverage"] == r["coverage"]
            assert g["king_wen"] == ("" if r["king_wen"] is None else str(r["king_wen"]))
            assert g["lines_formal"] == ("" if r["lines_formal"] is None else r["lines_formal"])
    # manifest：sha256／列數與檔案相符、大盤列排除數、params_sha＝db、欄序
    m = json.loads((out / EXP.OUT_DIR / EXP.MANIFEST).read_text(encoding="utf-8"))
    assert set(m["files"]) == {EXP.file_name(s, hz) for s in SEG for hz in H} and m["n_market_rows_excluded"] == n_mkt > 0
    for name, f in m["files"].items():
        p = out / EXP.OUT_DIR / name
        assert f["sha256"] == _sha(p) and f["bytes"] == p.stat().st_size and f["n_rows"] == len(_read(out, name))
        assert sum(f["exit_reason_counts"].values()) == f["n_rows"]
    with ScoreStore(world["cache"] / "scores.db", readonly=True) as sc2:
        assert m["params_sha"] == sc2.params_sha_of(DV) and m["pool_semantics"] == "pit-1"
        assert m["model_version"] == sc2.params_of(DV)["model_version"]
    assert m["columns"] == list(EXP.COLUMNS) and m["h_by_horizon"] == H and m["calendar"]["data_end"] == DAYS[-1]
    assert m["factors"]["stocks"] == 3 and m["factor_sources"]["sources"] == "div+capred+split+par-1"    # 裁定 #51
    assert m["factor_sources"]["cross_source_dup"] == 1 and m["factor_sources"]["missing_tables"] == [] and m["factor_anomaly_rows"] == []
    assert {k: v["kept"] for k, v in m["factor_sources"]["by_source"].items()} == {"dividend": 1, "capred": 1, "split": 1, "parvalue": 1}
    assert m["segments"]["train"]["n_score_days"] == 50 and m["segments"]["valid"]["n_score_days"] == 30
    assert m["segments"]["train"]["calendar_days_without_scores"] == []
    assert [r[1] for r in m["price_table_info"]].count("open") == 1
    # open 品質（訓練＋驗證＝全 80 日）：0050／9101 每日有列但沒有 open（160 列、2 檔）＋1102 停牌兩列＋6488 畸形列 → 163 列 4 檔；
    # 成交列口徑：6488 畸形列 close=0 不算成交、1102 停牌量 0 不算 → 160 列 2 檔
    oq = m["open_quality"]["train_plus_valid"]
    assert oq["range"] == [DAYS[0], DAYS[-1]] and oq["open_null_or_nonpos"] == {"rows": 163, "stocks": 4}
    assert oq["open_null_or_nonpos_traded"] == {"rows": 160, "stocks": 2}
    with gzip.open(out / EXP.OUT_DIR / "train_short.csv.gz", "rb") as g:
        raw = g.read()
    assert raw.startswith(",".join(EXP.COLUMNS).encode() + b"\n") and b"\r" not in raw


def test_fwd_ret_matches_hand_calc_with_all_edge_cases(world, tmp_path):
    out = tmp_path / "out"
    assert _export(world, out) == 0
    got = _all_rows(out)
    hand = hand_calc(world["cache"], world["cal"])
    assert mismatches(got, hand) == []
    # 六種案例都真的在樣本裡，且值與最直接的算式相符
    pc = sqlite3.connect(f"file:{world['cache'] / 'prices.db'}?mode=ro", uri=True)
    P = {(str(s), str(d)): (o, c) for s, d, o, c in pc.execute("SELECT stock_id, date, open, close FROM raw_price_daily WHERE data_version=?", (DV,))}
    IX = {(str(s), str(d)): (o, c) for s, d, o, c in pc.execute("SELECT stock_id, date, open, close FROM raw_index_price WHERE data_version=?", (DV,))}
    pc.close()
    # 正常：2330 T=DAYS[10] short → e=11、x=21
    r = got[(DAYS[10], "twse", "2330", "short")]
    assert r["exit_reason"] == "" and r["fwd_ret"] == _fmt(P["2330", DAYS[21]][1] / P["2330", DAYS[11]][0] - 1)
    assert r["mkt_ret_h"] == _fmt(IX["TAIEX", DAYS[21]][1] / IX["TAIEX", DAYS[11]][0] - 1) and r["entry_limit_up"] == "0" and r["exit_limit_down"] == "0"
    # 跨除權息：1101 T=DAYS[30] short → e=31（係數 1）、x=41（≥ EX_I=40，係數 100/80=1.25）
    assert EX_I == 40
    r = got[(DAYS[30], "twse", "1101", "short")]
    assert r["exit_reason"] == "" and r["fwd_ret"] == _fmt(P["1101", DAYS[41]][1] * 1.25 / P["1101", DAYS[31]][0] - 1)
    assert r["fwd_ret"] != _fmt(P["1101", DAYS[41]][1] / P["1101", DAYS[31]][0] - 1)      # 不乘係數會是另一個數
    # halt：1102 T=DAYS[19] short → x=DAYS[30] 停牌（SUSPEND_I）、出場取 DAYS[29] 收盤 50；tpex 指數同窗到 DAYS[29]
    assert SUSPEND_I == (30, 31)
    r = got[(DAYS[19], "twse", "1102", "short")]
    assert r["exit_reason"] == "halt" and r["fwd_ret"] == _fmt(50.0 / P["1102", DAYS[20]][0] - 1) == "0.010101"
    assert r["mkt_ret_h"] == _fmt(IX["TAIEX", DAYS[29]][1] / IX["TAIEX", DAYS[20]][0] - 1)
    # no_entry：1102 T=DAYS[29] → e=DAYS[30] 停牌（無 open、量 0）；mkt_ret_h 取名目窗 e..x
    r = got[(DAYS[29], "twse", "1102", "short")]
    assert r["exit_reason"] == "no_entry" and r["fwd_ret"] == "" and r["entry_limit_up"] == "" and r["exit_limit_down"] == ""
    assert r["mkt_ret_h"] == _fmt(IX["TAIEX", DAYS[40]][1] / IX["TAIEX", DAYS[30]][0] - 1)
    # 畸形列（6488 DAYS[35] close=0）不算成交：T=DAYS[24] short → x=35 → halt、出場取 DAYS[34]
    r = got[(DAYS[24], "tpex", "6488", "short")]
    assert r["exit_reason"] == "halt" and r["fwd_ret"] == _fmt(P["6488", DAYS[34]][1] / P["6488", DAYS[25]][0] - 1)
    # delist：Y 自 DAYS[YD] 起無列；T=DAYS[YD-8] short → x=YD+3 > 最後一列 YD-1 < data_end → delist、出場取 DAYS[YD-1]
    r = got[(DAYS[YD - 8], "twse", PIT_Y, "short")]
    assert r["exit_reason"] == "delist" and r["fwd_ret"] == _fmt(P[PIT_Y, DAYS[YD - 1]][1] / P[PIT_Y, DAYS[YD - 7]][0] - 1)
    # 裁定 #51 三源（valid 段）：1101 T=DAYS[50] short → x=61 ≥ CAPRED_I=60：出場乘 1.25×0.5、進場乘 1.25 → 淨 ×0.5（減資日起 adj < raw）
    assert (CAPRED_I, SPLIT_I, PAR_I) == (60, 65, 70)
    r = got[(DAYS[50], "twse", "1101", "short")]
    assert r["exit_reason"] == "" and r["fwd_ret"] == _fmt(P["1101", DAYS[61]][1] * 0.5 / P["1101", DAYS[51]][0] - 1)
    # 2330 T=DAYS[55] short → x=66 ≥ SPLIT_I=65：×4（分割與面額變更表同鍵去重，**不是** ×16）
    r = got[(DAYS[55], "twse", "2330", "short")]
    assert r["fwd_ret"] == _fmt(P["2330", DAYS[66]][1] * 4.0 / P["2330", DAYS[56]][0] - 1)
    assert r["fwd_ret"] != _fmt(P["2330", DAYS[66]][1] * 16.0 / P["2330", DAYS[56]][0] - 1)
    # 6488 T=DAYS[60] short → x=71 ≥ PAR_I=70：×10（只在面額變更表）
    r = got[(DAYS[60], "tpex", "6488", "short")]
    assert r["fwd_ret"] == _fmt(P["6488", DAYS[71]][1] * 10.0 / P["6488", DAYS[61]][0] - 1)
    # 末日截斷：T=DAYS[75] short（x=86 > 79）→ 空、列保留、exit_reason 空；T=DAYS[79]（e 也超出）→ 全空
    r = got[(DAYS[75], "twse", "2330", "short")]
    assert r == {**r, "fwd_ret": "", "mkt_ret_h": "", "exit_reason": "", "entry_limit_up": "0", "exit_limit_down": ""}
    r = got[(DAYS[79], "twse", "2330", "short")]
    assert (r["fwd_ret"], r["mkt_ret_h"], r["exit_reason"], r["entry_limit_up"], r["exit_limit_down"]) == ("", "", "", "", "")
    # 每類都有樣本；mid（h=40）在 80 日世界仍有 39 個可算的 T
    reasons = {}
    for k, v in got.items():
        reasons[v["exit_reason"]] = reasons.get(v["exit_reason"], 0) + 1
    assert reasons[""] > reasons["halt"] > 0 and reasons["delist"] > 0 and reasons["no_entry"] > 0
    assert sum(1 for k, v in got.items() if k[3] == "mid" and v["fwd_ret"]) > 0
    assert MALFORMED_I == 35


def test_gzip_bytes_reproducible_and_rerun_is_noop(world, tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    assert _export(world, a) == 0 and _export(world, b) == 0
    for s in SEG:
        for hz in H:
            name = EXP.file_name(s, hz)
            assert _sha(a / EXP.OUT_DIR / name) == _sha(b / EXP.OUT_DIR / name), name
    assert (a / EXP.OUT_DIR / EXP.MANIFEST).read_bytes() == (b / EXP.OUT_DIR / EXP.MANIFEST).read_bytes()
    p = a / EXP.OUT_DIR / "valid_mid.csv.gz"
    m0 = p.stat().st_mtime_ns
    assert _export(world, a) == 0
    assert p.stat().st_mtime_ns == m0 and not list((a / EXP.OUT_DIR).glob("*.tmp"))
    # gzip 標頭 mtime=0
    assert p.read_bytes()[4:8] == b"\x00\x00\x00\x00"


@pytest.mark.parametrize("mutation", ["h_wrong", "no_factor", "entry_at_t_close", "round_digits"])
def test_mutations_turn_hand_check_red(world, tmp_path, monkeypatch, mutation):
    if mutation == "h_wrong":
        monkeypatch.setitem(EXP.H_BY_HORIZON, "short", 9)
    elif mutation == "no_factor":
        monkeypatch.setattr(EXP, "adj_price", lambda raw, date, fac: raw)
    elif mutation == "entry_at_t_close":
        monkeypatch.setattr(EXP, "entry_price", lambda book, sid, e_pos, fac: EXP.adj_price(book.close_at(sid, e_pos - 1), book.cal[e_pos - 1], fac))
    else:
        monkeypatch.setattr(EXP, "ROUND_DIGITS", 4)
    out = tmp_path / "out"
    assert _export(world, out) == 0
    bad = mismatches(_all_rows(out), hand_calc(world["cache"], world["cal"]))
    assert bad, mutation
    if mutation == "no_factor":                                   # 只有視窗跨過事件日的列會變：1101（除息 40／減資 60）、2330（分割 65）、6488（面額 70）
        assert {k[2] for k, *_ in bad} == {"1101", "2330", "6488"}
        last = {"1101": DAYS[CAPRED_I - 1], "2330": DAYS[SPLIT_I - 1], "6488": DAYS[PAR_I - 1]}
        assert all(k[0] <= last[k[2]] for k, *_ in bad)
        assert any(k[2] == "1101" and DAYS[EX_I] <= k[0] for k, *_ in bad)   # 除息後、減資前的 1101 列也在（跨減資日）
    if mutation == "h_wrong":
        assert {k[3] for k, *_ in bad} == {"short"}


def test_segment_train_only_writes_three_files(world, tmp_path):
    out = tmp_path / "out"
    assert _export(world, out, "--segment", "train") == 0
    names = sorted(p.name for p in (out / EXP.OUT_DIR).iterdir())
    assert names == sorted([EXP.file_name("train", hz) for hz in H] + [EXP.MANIFEST])
    m = json.loads((out / EXP.OUT_DIR / EXP.MANIFEST).read_text(encoding="utf-8"))
    assert set(m["files"]) == {EXP.file_name("train", hz) for hz in H} and list(m["segments"]) == ["train"]
    assert m["open_quality"]["train_plus_valid"]["range"] == [DAYS[0], DAYS[-1]]     # A2 統計固定量全段


def test_existing_different_target_blocks_without_force(world, tmp_path):
    out = tmp_path / "out"
    assert _export(world, out) == 0
    p = out / EXP.OUT_DIR / "train_short.csv.gz"
    good = p.read_bytes()
    with gzip.GzipFile(fileobj=(buf := io.BytesIO()), mode="wb", mtime=0) as g:
        g.write(b"date,market\n2020-01-01,twse\n")
    p.write_bytes(buf.getvalue())
    tampered = p.read_bytes()
    others = {q.name: q.read_bytes() for q in (out / EXP.OUT_DIR).glob("*.csv.gz") if q != p}
    assert _export(world, out) == 1
    assert p.read_bytes() == tampered and not list((out / EXP.OUT_DIR).glob("*.tmp"))
    assert {q.name: q.read_bytes() for q in (out / EXP.OUT_DIR).glob("*.csv.gz") if q != p} == others
    assert _export(world, out, "--force") == 0
    assert p.read_bytes() == good


def test_rc2_on_non_pit_or_foreign_params_sha(world, tmp_path, capsys):
    cache2 = tmp_path / "cache2"
    shutil.copytree(world["cache"], cache2)
    with ScoreStore(cache2 / "scores.db") as s:
        s.conn.execute("UPDATE replay_meta SET params_sha='deadbeef0000' WHERE data_version=?", (DV,))
    assert _export(world, tmp_path / "o1", "--cache-dir", str(cache2)) == 2
    assert "params_sha" in capsys.readouterr().err
    # params_json 自洽但 pool_semantics 不是 pit-1（舊池語意的 db）
    with ScoreStore(cache2 / "scores.db") as s:
        p = s.params_of(DV)
        p["pool_semantics"] = "static-0"
        from iching.features_io import params_fingerprint
        s.conn.execute("UPDATE replay_meta SET params_sha=?, params_json=? WHERE data_version=?",
                       (params_fingerprint(p), json.dumps(p, sort_keys=True, ensure_ascii=False), DV))
    assert _export(world, tmp_path / "o2", "--cache-dir", str(cache2)) == 2
    assert "pool_semantics" in capsys.readouterr().err
    # 自洽、pit-1、但 window 之外的欄位與現行碼不同（例如 text_version）→ 非現行碼指紋 → rc 2
    with ScoreStore(cache2 / "scores.db") as s:
        p = s.params_of(DV)
        p["pool_semantics"], p["text_version"] = "pit-1", "9.9"
        s.conn.execute("UPDATE replay_meta SET params_sha=?, params_json=? WHERE data_version=?",
                       (params_fingerprint(p), json.dumps(p, sort_keys=True, ensure_ascii=False), DV))
    assert _export(world, tmp_path / "o3", "--cache-dir", str(cache2)) == 2
    assert "現行碼指紋" in capsys.readouterr().err
    assert not (tmp_path / "o1").exists() and not (tmp_path / "o2").exists() and not (tmp_path / "o3").exists()


def test_rc2_on_missing_db_open_column_and_calendar_gap(world, tmp_path, capsys):
    assert _export(world, tmp_path / "a", "--cache-dir", str(tmp_path / "nodb")) == 2
    # 價格表沒有 open 欄
    cache3 = tmp_path / "cache3"
    shutil.copytree(world["cache"], cache3)
    c = sqlite3.connect(cache3 / "prices.db")
    c.execute("ALTER TABLE raw_price_daily DROP COLUMN open")
    c.commit()
    c.close()
    assert _export(world, tmp_path / "b", "--cache-dir", str(cache3)) == 2
    assert "open" in capsys.readouterr().err
    # 日曆少了段內一個計分日
    cal_gap = tmp_path / "cal_gap.json"
    CAL.write_calendar_json(cal_gap, CAL.calendar_payload("tpe", [d for d in DAYS if d != DAYS[45]], DV))
    assert EXP.main(["--cache-dir", str(world["cache"]), "--out", str(tmp_path / "c"), "--calendar", str(cal_gap)]) == 2
    assert "日曆" in capsys.readouterr().err
    # 日曆沒涵蓋到段末
    cal_short = tmp_path / "cal_short.json"
    CAL.write_calendar_json(cal_short, CAL.calendar_payload("tpe", DAYS[:70], DV))
    assert EXP.main(["--cache-dir", str(world["cache"]), "--out", str(tmp_path / "d"), "--calendar", str(cal_short)]) == 2
    assert "未涵蓋段末" in capsys.readouterr().err
    for n in "abcd":
        assert not (tmp_path / n / EXP.OUT_DIR).exists() or not list((tmp_path / n / EXP.OUT_DIR).glob("*.csv.gz"))


def test_constants_match_pre_registration():
    """常數釘死登錄書：三段切點（pre-registration.md:47-50）、h（:55）；合成世界改的是 fixture 內的副本，這裡看原值。"""
    src = (ROOT / "scripts" / "export_dataset.py").read_text(encoding="utf-8")
    assert '"train": ("2021-01-01", "2023-06-30"), "valid": ("2023-07-01", "2024-12-31")' in src
    assert '{"short": 10, "swing": 20, "mid": 40}' in src
    cal = DC.load_calendar_dates(ROOT / DC.CALENDAR_TPE_FILE)
    assert sum(1 for d in cal if "2021-01-01" <= d <= "2023-06-30") == 603
    assert sum(1 for d in cal if "2023-07-01" <= d <= "2024-12-31") == 368
    assert math.isnan(EXP.NAN)


# ---------------------------------------------------------------------------
# scripts/adj_event_report.py（hetzner_adj.sh 第 4c 步，§7.3 G）：manifest 摘要＋指定代號在四源事件窗的 fwd_ret，唯讀、rc 0；
# 印出的 short 列逐格等於 csv.gz 的值，跨事件標記＝T < ex ≤ x；四源無事件的代號明說；缺 manifest rc 2
def test_adj_event_report_prints_event_windows_from_csv(world, tmp_path, capsys):
    out = tmp_path / "o"
    assert _export(world, out) == 0
    cal = world["cal"]
    args = ["--cache-dir", str(world["cache"]), "--data-dir", str(out / EXP.OUT_DIR), "--calendar", str(world["repo"] / DC.CALENDAR_TPE_FILE)]
    assert AER.main([*args, "--stocks", "1101", "2330", "6488", "9999"]) == 0
    text = capsys.readouterr().out
    assert "manifest: data_version=" in text and "六檔合計" in text and "factor_anomaly_rows:" in text
    assert "== 每檔 |fwd_ret| > 1 列數" in text and all(EXP.file_name(s, hz) + ":" in text for s in SEG for hz in H)
    assert "== 1101：還原事件" in text and f"事件 {DAYS[CAPRED_I]} capred before=100.0 after=200.0 factor=0.5000" in text
    assert f"事件 {DAYS[SPLIT_I]} split before=400.0 after=100.0 factor=4.0000" in text       # parvalue 同鍵副本已被 split 蓋掉
    assert "parvalue before=400.0" not in text
    assert f"事件 {DAYS[PAR_I]} parvalue before=60.0 after=6.0 factor=10.0000" in text
    assert "== 9999：還原事件 0 筆（四源皆無此檔事件）" in text
    # short 事件窗逐列＝csv：6488 面額變更在 DAYS[PAR_I]（valid 段），T ∈ [ex−11, ex+1]，跨事件＝T ≤ ex−1 且 T+1+10 ≥ ex
    rows = {r["date"]: r for r in _read(out, EXP.file_name("valid", "short")) if r["stock_id"] == "6488"}
    ex = PAR_I
    block = text.split("== 6488：")[1].split("\n== ")[0]
    short_lines = [ln for ln in block.splitlines() if ln.strip().startswith("short  T=")]
    assert short_lines, block
    for ln in short_lines:
        d = ln.split("T=")[1].split()[0]
        i = cal.index(d)
        assert ex - 11 <= i <= ex + 1, (d, ln)
        assert d in rows, (d, list(rows)[:3])
        assert f"fwd_ret={rows[d]['fwd_ret'] or '—':>12}" in ln, (ln, rows[d])
        assert ("跨事件" in ln) == (i <= ex - 1 and i + 1 + 10 >= ex), ln
    assert "swing  跨事件" in block and "mid    跨事件" in block and "全期間 short n=" in block
    # 缺 manifest → rc 2
    assert AER.main(["--cache-dir", str(world["cache"]), "--data-dir", str(tmp_path / "nowhere"), "--calendar", str(world["repo"] / DC.CALENDAR_TPE_FILE)]) == 2


# ---------------------------------------------------------------------------
# scripts/hetzner_dataset.sh（§2 B1）：語法、EXPECT 的 --verify -q、pull 後 HEAD 前進即以新版重新執行（比照 test_pit_world 的兩支）
def test_hetzner_dataset_sh_syntax_and_expect_line(tmp_path):
    subprocess.run(["bash", "-n", str(ROOT / "scripts" / "hetzner_dataset.sh")], check=True)
    script = (ROOT / "scripts" / "hetzner_dataset.sh").read_text(encoding="utf-8")
    m = re.search(r"^EXPECT=\$\(.*\)$", script, re.M)
    assert m and "--verify" in m.group(0) and "-q" in m.group(0), m
    assert "export_dataset.py --cache-dir cache --out . --segment all --force" in script
    assert "mkdir -p data/backtest" in script and script.index("git checkout -q main") < script.index("mkdir -p data/backtest")
    repo = tmp_path / "r"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    out = subprocess.run(["bash", "-c", f'BR=hetzner/dataset-none; {m.group(0)}; printf "%s" "$EXPECT"'],
                         cwd=repo, check=True, capture_output=True, text=True).stdout
    assert out == "0" * 40, repr(out)


def test_hetzner_dataset_sh_reexecs_new_script_after_pull(tmp_path):
    def git(*a, cwd):
        return subprocess.run(["git", *a], cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()

    src = (ROOT / "scripts" / "hetzner_dataset.sh").read_text(encoding="utf-8")
    anchor = "git log -1 --format='HEAD %h %ci %s'\n"
    assert src.count(anchor) == 1
    reexec_end = "fi\n"
    head, tail = src.split(anchor)
    before, after = tail.split(reexec_end, 1)
    v2 = head + anchor + before + reexec_end + 'echo "V2-MARKER"; exit 0\n' + after
    v1 = head + anchor + before + reexec_end + 'echo "V1-CONTINUED"; exit 0\n' + after
    bare = tmp_path / "origin.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(bare)], check=True)
    work = tmp_path / "work"
    subprocess.run(["git", "clone", "-q", str(bare), str(work)], check=True)
    git("config", "user.email", "t@t", cwd=work)
    git("config", "user.name", "t", cwd=work)
    (work / "scripts").mkdir()
    (work / "scripts" / "hetzner_dataset.sh").write_text(v1, encoding="utf-8")
    git("add", "-A", cwd=work)
    git("commit", "-qm", "v1", cwd=work)
    git("push", "-q", "-u", "origin", "main", cwd=work)
    (work / "scripts" / "hetzner_dataset.sh").write_text(v2, encoding="utf-8")
    git("commit", "-qam", "v2", cwd=work)
    git("push", "-q", "origin", "main", cwd=work)
    git("reset", "-q", "--hard", "HEAD~1", cwd=work)
    assert "V1-CONTINUED" in (work / "scripts" / "hetzner_dataset.sh").read_text(encoding="utf-8")
    tmpdir = tmp_path / "tmp"
    tmpdir.mkdir()
    import os
    r = subprocess.run(["bash", "scripts/hetzner_dataset.sh", "2024-12-31"], cwd=work, capture_output=True, text=True,
                       env={**os.environ, "TMPDIR": str(tmpdir), "HETZNER_DS_LOG": "", "HETZNER_DS_SELF": "",
                            "HETZNER_DS_PULLED": "", "HETZNER_DS_REPO": ""})
    out = r.stdout + r.stderr
    assert r.returncode == 0, out
    assert "改用新版 scripts/hetzner_dataset.sh 重新執行" in out and "V2-MARKER" in out and "V1-CONTINUED" not in out, out
    assert git("rev-parse", "HEAD", cwd=work) == git("rev-parse", "origin/main", cwd=work)
    assert list(tmpdir.iterdir()) == []
    logs = sorted((work / "cache" / "logs").glob("dataset-*.log"))
    assert len(logs) == 1 and "V2-MARKER" in logs[0].read_text(encoding="utf-8")
    # 參數驗證：非法日期 rc 2
    r2 = subprocess.run(["bash", "scripts/hetzner_dataset.sh", "2024-13-99"], cwd=work, capture_output=True, text=True,
                        env={**os.environ, "TMPDIR": str(tmpdir), "HETZNER_DS_LOG": "", "HETZNER_DS_SELF": "", "HETZNER_DS_PULLED": "", "HETZNER_DS_REPO": ""})
    assert r2.returncode == 2 and "合法的 YYYY-MM-DD" in r2.stdout + r2.stderr
    assert list(tmpdir.iterdir()) == []
