"""`scripts/build_web.py`（P4 預覽版，`docs/P4-PREVIEW.md` §1 契約／§4 B 驗收）：合成 3 個分數檔（含 `lines_formal` null 列、
`in_rank_pool` 0 列、大盤列、某日壞 JSON、某日形狀不對）＋合成 `pool.json`（同代號兩列取日期最新、一檔缺股名）。

斷言：結構鍵、`n_rows`、`stocks` 代號數、**swing／mid 無 `bs`／`ti`／`to` 鍵而 short 有**（§13.3a／§6 F1）、`names` 只含出現且有股名的代號、
timeline 長度／null 填補／`--n` 截取、跑兩次位元組相同、無分數檔 rc 2、最新檔壞掉 rc 2。免 token 免網路。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import build_web as BW  # noqa: E402

DATES = ("2026-09-01", "2026-09-02", "2026-09-03", "2026-09-04", "2026-09-05")   # 09-03 壞 JSON、09-04 形狀不對
HORIZONS = ("short", "swing", "mid")


def _row(sid: str, market: str, h: str, date: str, *, kw: int | None = 34, formal: bool = True,
         in_pool: int | None = 1, calibrated: int = 0) -> dict:
    lines = [77.04, 75.32, 76.13, 61.21, 45.93, 41.74]
    row: dict = {"market": market, "horizon": h, "stock_id": sid, "date": date, "scope": "market_index" if sid == "__MARKET__" else "stock",
                 "model_version": "p2-score-engine-1.deadbeef", "base_score": 64.926431 if formal else None,
                 "inner_trigram_score": 76.2, "outer_trigram_score": 49.6, "coverage": "full" if formal else "reweighted",
                 "calibrated": calibrated, "flags": "{}" if sid == "__MARKET__" else None,
                 "lines_provisional": "111100", "king_wen_provisional": 34, "hexagram_name_provisional": "雷天大壯",
                 "lines_formal": "111100" if formal else None, "king_wen": kw if formal else None,
                 "hexagram_name": "雷天大壯" if formal else None,
                 "line_states": "yyyynn" if formal else "-----n", "streaks": "0,0,0,0,0,1", "in_rank_pool": in_pool}
    for k in range(1, 7):
        known = formal or k == 6
        row[f"line_{k}"] = lines[k - 1] if known else None
        row[f"line_{k}_unknown"] = 0 if known else 1
        row[f"line_{k}_coverage_ratio"] = 1.0 if known else 0.0
        row[f"line_{k}_reweighted"] = 0 if known else 1
    return row


def _payload(date: str, rows: list[dict]) -> dict:
    return {"schema": 1, "tpe_date": date, "data_version": "fm-test-01", "params_sha": "abc123", "text_version": "0.2",
            "diag": {"elapsed_ms": 1.0}, "rows": rows}


def _day_rows(date: str, *, with_9999: bool = True) -> list[dict]:
    rows = []
    for mk in ("twse", "tpex"):
        for h in HORIZONS:
            rows.append(_row("__MARKET__", mk, h, date, in_pool=None))
    for h in HORIZONS:
        rows.append(_row("2330", "twse", h, date, kw=34 if date != "2026-09-02" else 14))   # 09-02 換卦
        rows.append(_row("1259", "tpex", h, date, in_pool=0))                                # 不在排名池
        rows.append(_row("2938", "tpex", h, date, formal=False, in_pool=0))                  # 六爻未全確立
    if with_9999:
        rows.append(_row("9999", "twse", "short", date))                                     # 只有 short 一個期間
    return rows


@pytest.fixture()
def world(tmp_path: Path) -> Path:
    scores = tmp_path / "data" / "scores"
    scores.mkdir(parents=True)
    for d in DATES:
        p = scores / f"{d}.json"
        if d == "2026-09-03":
            p.write_text("{not json", encoding="utf-8")
        elif d == "2026-09-04":
            p.write_text(json.dumps({"schema": 1, "rows": "nope"}), encoding="utf-8")
        else:
            p.write_text(json.dumps(_payload(d, _day_rows(d, with_9999=(d != "2026-09-05"))), ensure_ascii=False), encoding="utf-8")
    (scores / "README.txt").write_text("ignored", encoding="utf-8")
    pool = {"schema": 1, "data_version": "fm-test-01", "rows": [
        {"date": "2024-12-30", "stock_id": "2330", "stock_name": "台積電(舊)", "industry_category": "半導體", "type": "twse"},
        {"date": "2026-09-16", "stock_id": "2330", "stock_name": "台積電", "industry_category": "半導體業", "type": "twse"},
        {"date": "2026-09-16", "stock_id": "1259", "stock_name": "安心", "industry_category": "觀光餐旅", "type": "tpex"},
        {"date": "2026-09-16", "stock_id": "2938", "stock_name": "", "industry_category": "x", "type": "tpex"},     # 缺股名
        {"date": "2026-09-16", "stock_id": "0050", "stock_name": "元大台灣50", "industry_category": "ETF", "type": "twse"},  # 不在 rows
    ]}
    (tmp_path / "data" / "pool.json").write_text(json.dumps(pool, ensure_ascii=False), encoding="utf-8")
    return tmp_path


def _run(root: Path, *extra: str) -> tuple[int, dict, dict]:
    rc = BW.main(["--root", str(root), *extra])
    out = root / "data" / "web"
    latest = json.loads((out / "latest.json").read_text(encoding="utf-8")) if (out / "latest.json").exists() else {}
    tl = json.loads((out / "timeline.json").read_text(encoding="utf-8")) if (out / "timeline.json").exists() else {}
    return rc, latest, tl


def test_latest_structure(world: Path, capsys):
    rc, L, _ = _run(world)
    assert rc == 0
    assert set(L) == {"schema", "date", "data_version", "params_sha", "text_version", "calibrated", "generated_from",
                      "n_rows", "names", "market", "stocks"}
    assert L["schema"] == 1 and L["date"] == "2026-09-05" and L["data_version"] == "fm-test-01"
    assert L["params_sha"] == "abc123" and L["text_version"] == "0.2"
    assert L["generated_from"] == "data/scores/2026-09-05.json"
    assert L["calibrated"] is False
    assert L["n_rows"] == 6 + 9                                   # 大盤 6 ＋ 3 檔 × 3 期間（09-05 無 9999）
    assert set(L["stocks"]) == {"2330", "1259", "2938"}
    assert set(L["market"]) == {f"{m}|{h}" for m in ("twse", "tpex") for h in HORIZONS}
    s = L["stocks"]["2330"]
    assert set(s) == {"market", "in_rank_pool", "short", "swing", "mid"}
    assert s["market"] == "twse" and s["in_rank_pool"] == 1
    assert L["stocks"]["1259"]["in_rank_pool"] == 0
    e = s["short"]
    assert set(e) == {"kw", "name", "kwp", "namep", "lf", "lp", "st", "sk", "l", "unk", "cov", "bs", "ti", "to"}
    assert e["kw"] == 34 and e["name"] == "雷天大壯" and e["kwp"] == 34 and e["lf"] == "111100" and e["lp"] == "111100"
    assert e["st"] == "yyyynn" and e["sk"] == "0,0,0,0,0,1" and e["cov"] == "full"
    assert e["l"] == [77.0, 75.3, 76.1, 61.2, 45.9, 41.7]          # 1 位小數
    assert e["unk"] == [0, 0, 0, 0, 0, 0]
    assert e["bs"] == 64.93
    assert e["ti"] == 76.2 and e["to"] == 49.6                      # §6 F1：1 位小數
    # 大盤列：同形，無 in_rank_pool；flags 不進檔
    m = L["market"]["twse|short"]
    assert "in_rank_pool" not in m and "flags" not in m and m["bs"] == 64.93 and m["ti"] == 76.2 and m["to"] == 49.6
    # 全檔不含 flags／adv／trigram 原鍵名（內外卦分數只以 ti／to 進 short）
    text = (world / "data" / "web" / "latest.json").read_text(encoding="utf-8")
    assert "flags" not in text and "trigram" not in text and '"adv"' not in text
    err = capsys.readouterr().err
    assert "pool.json" not in err and "中止" not in err            # 最新檔與 pool 都正常；timeline 兩個壞日的警告另測


def test_swing_mid_have_no_bs(world: Path):
    """§13.3a＋§6 F1：`bs`／`ti`／`to` 三個鍵只在 short；swing／mid 連鍵都不存在（不是 null）。"""
    _, L, _ = _run(world)
    for sid, s in L["stocks"].items():
        for h in ("swing", "mid"):
            assert s[h] is not None and not ({"bs", "ti", "to"} & set(s[h])), (sid, h)
        assert {"bs", "ti", "to"} <= set(s["short"]), sid
    for key, e in L["market"].items():
        assert ({"bs", "ti", "to"} <= set(e)) == key.endswith("|short"), key
        assert (({"bs", "ti", "to"} & set(e)) == set()) == (not key.endswith("|short")), key


def test_trigram_scores_null_when_missing_or_non_numeric(world: Path):
    """來源欄缺／null／非數（字串）→ `ti`／`to` 為 null，鍵仍在；1 位小數四捨五入。"""
    p = world / "data" / "scores" / "2026-09-05.json"
    rows = _day_rows("2026-09-05", with_9999=False)
    for r in rows:
        if r["stock_id"] == "2330" and r["horizon"] == "short":
            r["inner_trigram_score"] = None                      # null
            r["outer_trigram_score"] = "n/a"                     # 非數
        if r["stock_id"] == "1259" and r["horizon"] == "short":
            del r["inner_trigram_score"]                         # 缺鍵
            r["outer_trigram_score"] = 53.16796580708819         # 四捨五入到 53.2
    p.write_text(json.dumps(_payload("2026-09-05", rows), ensure_ascii=False), encoding="utf-8")
    _, L, _ = _run(world)
    a = L["stocks"]["2330"]["short"]
    assert "ti" in a and a["ti"] is None and "to" in a and a["to"] is None
    b = L["stocks"]["1259"]["short"]
    assert b["ti"] is None and b["to"] == 53.2
    # 合成列 2938 的 lines_formal null 列本身 trigram 欄仍是數字（合成資料），照樣帶值；null 規則只看來源欄
    assert L["stocks"]["2938"]["short"]["ti"] == 76.2


def test_lines_formal_null_row(world: Path):
    _, L, _ = _run(world)
    e = L["stocks"]["2938"]["mid"]
    assert e["kw"] is None and e["name"] is None and e["lf"] is None
    assert e["kwp"] == 34 and e["namep"] == "雷天大壯"             # 暫定卦仍在
    assert e["st"] == "-----n" and e["cov"] == "reweighted"
    assert e["l"] == [None, None, None, None, None, 41.7]
    assert e["unk"] == [1, 1, 1, 1, 1, 0]
    assert L["stocks"]["2938"]["short"]["bs"] is None
    assert "ti" not in e and "to" not in e                          # mid 無內外卦分數鍵


def test_names_only_present_and_named(world: Path):
    _, L, _ = _run(world)
    assert L["names"] == {"2330": ["台積電", "半導體業"], "1259": ["安心", "觀光餐旅"]}   # 取最新列；2938 缺股名；0050 不在 rows


def test_missing_horizon_is_explicit_null(world: Path):
    """09-01 的 9999 只有 short：其他兩期間鍵仍在、值為 null（明確為缺，不是省略）。"""
    (world / "data" / "scores" / "2026-09-05.json").unlink()
    (world / "data" / "scores" / "2026-09-04.json").unlink()
    (world / "data" / "scores" / "2026-09-03.json").unlink()
    (world / "data" / "scores" / "2026-09-02.json").unlink()
    _, L, _ = _run(world)
    assert L["date"] == "2026-09-01"
    s = L["stocks"]["9999"]
    assert s["short"] is not None and s["swing"] is None and s["mid"] is None
    assert "9999" not in L["names"]


def test_timeline_dates_nulls_and_keys(world: Path, capsys):
    rc, _, T = _run(world)
    assert rc == 0
    assert set(T) == {"schema", "dates", "series"}
    assert T["dates"] == list(DATES)                               # 5 個檔全取（N=20 > 5），升冪
    n = len(T["dates"])
    assert all(len(v) == n for v in T["series"].values())
    assert set(T["series"]) == ({f"{s}|{h}" for s in ("2330", "1259", "2938") for h in HORIZONS}
                                | {f"{m}|{h}" for m in ("twse", "tpex") for h in HORIZONS} | {"9999|short"})
    # 壞 JSON（09-03）與形狀不對（09-04）→ 該日全部 null；其他日有值
    for v in T["series"].values():
        assert v[2] is None and v[3] is None
    assert T["series"]["2330|short"] == [[34, "yyyynn"], [14, "yyyynn"], None, None, [34, "yyyynn"]]
    assert T["series"]["2938|mid"][0] == [None, "-----n"]            # 有列但 kw null → [null, st]，不是 null
    assert T["series"]["9999|short"][-1] is None                     # 09-05 無該列
    assert T["series"]["twse|mid"][0] == [34, "yyyynn"]
    err = capsys.readouterr().err
    assert "2026-09-03.json" in err and "2026-09-04.json" in err and "警告" in err


def test_timeline_n_clips_to_last(world: Path):
    _, _, T = _run(world, "--n", "2")
    assert T["dates"] == ["2026-09-04", "2026-09-05"]
    assert all(len(v) == 2 for v in T["series"].values())
    assert "9999|short" not in T["series"]                           # 只在 09-01／09-02 出現、被截掉


def test_deterministic_and_trailing_newline(world: Path):
    out = world / "data" / "web"
    assert BW.main(["--root", str(world)]) == 0
    a = [(out / f).read_bytes() for f in ("latest.json", "timeline.json")]
    assert BW.main(["--root", str(world)]) == 0
    b = [(out / f).read_bytes() for f in ("latest.json", "timeline.json")]
    assert a == b
    assert all(x.endswith(b"\n") and not x.endswith(b"\n\n") for x in a)
    assert "台積電".encode("utf-8") in a[0]                          # ensure_ascii=False
    assert not list(out.glob("*.tmp"))


def test_out_option_relative_to_root(world: Path):
    assert BW.main(["--root", str(world), "--out", "alt/web"]) == 0
    assert (world / "alt" / "web" / "latest.json").exists()
    assert not (world / "data" / "web").exists()


def test_no_score_files_rc2(tmp_path: Path, capsys):
    (tmp_path / "data" / "scores").mkdir(parents=True)
    assert BW.main(["--root", str(tmp_path)]) == 2
    assert not (tmp_path / "data" / "web").exists()
    assert "中止" in capsys.readouterr().err
    assert BW.main(["--root", str(tmp_path / "nothing")]) == 2


def test_latest_file_broken_rc2(world: Path, capsys):
    (world / "data" / "scores" / "2026-09-05.json").write_text("{", encoding="utf-8")
    assert BW.main(["--root", str(world)]) == 2
    assert not (world / "data" / "web").exists()
    assert "2026-09-05.json" in capsys.readouterr().err


def test_pool_missing_names_empty(world: Path, capsys):
    (world / "data" / "pool.json").unlink()
    rc, L, _ = _run(world)
    assert rc == 0 and L["names"] == {}
    assert "pool.json" in capsys.readouterr().err


def test_calibrated_true_only_when_all_rows_1(world: Path):
    p = world / "data" / "scores" / "2026-09-05.json"
    rows = _day_rows("2026-09-05", with_9999=False)
    for r in rows:
        r["calibrated"] = 1
    p.write_text(json.dumps(_payload("2026-09-05", rows), ensure_ascii=False), encoding="utf-8")
    assert _run(world)[1]["calibrated"] is True
    rows[-1]["calibrated"] = 0
    p.write_text(json.dumps(_payload("2026-09-05", rows), ensure_ascii=False), encoding="utf-8")
    assert _run(world)[1]["calibrated"] is False
