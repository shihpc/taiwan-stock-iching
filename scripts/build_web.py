#!/usr/bin/env python3
"""P4 預覽版網頁的資料索引：由 `data/scores/<T>.json`（＋`data/pool.json` 股名）產兩份輕量檔到 `data/web/`。

    python3 scripts/build_web.py --root . [--n 20] [--out data/web]

契約出處＝`docs/P4-PREVIEW.md` §1（鍵名縮寫、`bs` 只有 short 帶、大盤列進 `market`）；§0 #1／§3 末條／§4 A・B・D 是驗收條件。
純標準庫、決定性：同一組輸入跑兩次位元組相同（`json.dump(sort_keys=True, separators=(",",":"), ensure_ascii=False)`＋末尾換行，
**不寫任何時戳**）。前端 `index.html` 只讀這兩檔，不碰 6 MB 的分數檔。

## `latest.json`

取 `data/scores/` 檔名（`YYYY-MM-DD.json`）最大者。頂層 `schema`／`date`／`data_version`／`params_sha`／`text_version`／
`calibrated`（所有列 `calibrated` 欄皆為 1 才 true；空列＝false）／`generated_from`／`n_rows`／`names`／`market`／`stocks`。
每筆期間物件＝`{kw, name, kwp, namep, lf, lp, st, sk, l, unk, cov[, bs]}`：
- `l`＝六爻分數各 1 位小數（null 保留）；`unk`＝六個 0/1（來源欄 null 視為 1＝未知）；`sk`／`st`／`lf`／`lp` 照分數檔字串原樣；
- **`bs`（base_score）只有 `short` 帶，swing／mid 一律沒有這個鍵**（規格 v1.2.2 §13.3a：波段／中期不顯示方向分數）；
  值取 2 位小數（展示用；分數檔原值仍在 `data/scores/`）。
- `stocks[<stock_id>]`＝`{market, in_rank_pool, short, swing, mid}`；某期間該日無列＝該鍵為 null（明確為缺，不是省略）。
- 大盤列（`stock_id == "__MARKET__"`）進 `market["<market>|<horizon>"]`，不進 `stocks`、不進 `names`。
- `names[<stock_id>]`＝`[stock_name, industry_category]`，只含 `stocks` 出現的代號；來源 `data/pool.json` 的 `rows`
  （同代號多列取 `date` 最新那列）；pool 缺該代號或 `stock_name` 空 → 不進 `names`。pool.json 讀不到＝`names` 空（stderr 警告）。
- 不含 `cross.json` 任何內容、不含 `flags`、不含 `adv`、不含 `inner/outer_trigram_score`。

## `timeline.json`

`dates`＝檔名排序後最後 N 個（升冪）；`series["<stock_id>|<horizon>"]`（大盤列用 `"<market>|<horizon>"`，與 `latest.market`
同一把鍵）與 `dates` 等長，每格 `[kw|null, line_states|null]`，該日無該列＝null。某日檔讀不到／壞掉／形狀不對 → 該日全部
series 填 null、stderr 印警告、**不中止**。

## 回傳碼

0 成功；2 中止（`data/scores/` 完全無分數檔、最新分數檔本身壞掉、或 out 目錄不可寫）。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path
from typing import Any

SCHEMA = 1
DEFAULT_N = 20
HORIZONS = ("short", "swing", "mid")
MARKET_ID = "__MARKET__"
DATE_FILE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}\.json$")
LINE_DECIMALS = 1
BS_DECIMALS = 2


class BuildWebError(RuntimeError):
    pass


def warn(msg: str) -> None:
    print(f"[build_web] 警告：{msg}", file=sys.stderr)


def list_score_files(scores_dir: Path) -> list[Path]:
    """`YYYY-MM-DD.json` 檔名升冪（其他檔名一律忽略）。"""
    if not scores_dir.is_dir():
        return []
    return sorted(p for p in scores_dir.iterdir() if p.is_file() and DATE_FILE_RE.match(p.name))


def load_scores(path: Path) -> dict[str, Any]:
    """讀一份分數檔；讀不到／非 JSON／`rows` 不是 list 一律拋 `BuildWebError`（呼叫端決定要中止還是填 null）。"""
    try:
        with path.open("r", encoding="utf-8") as f:
            payload = json.load(f)
    except (OSError, ValueError) as e:
        raise BuildWebError(f"{path}: {e}") from e
    if not isinstance(payload, dict) or not isinstance(payload.get("rows"), list):
        raise BuildWebError(f"{path}: 形狀不對（需 dict 且 rows 為 list）")
    return payload


def _round(v: Any, nd: int) -> float | None:
    if v is None or isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    return round(float(v), nd)


def _unk(v: Any) -> int:
    """`line_k_unknown`：null 視為未知（1）；其他照 0/1。"""
    return 1 if v is None else (1 if v else 0)


def entry_from_row(row: dict[str, Any]) -> dict[str, Any]:
    """分數列 → 期間物件（§1 的 `{...}`）。`bs` 只在 `horizon == "short"` 時寫入。"""
    out: dict[str, Any] = {
        "kw": row.get("king_wen"),
        "name": row.get("hexagram_name"),
        "kwp": row.get("king_wen_provisional"),
        "namep": row.get("hexagram_name_provisional"),
        "lf": row.get("lines_formal"),
        "lp": row.get("lines_provisional"),
        "st": row.get("line_states"),
        "sk": row.get("streaks"),
        "l": [_round(row.get(f"line_{k}"), LINE_DECIMALS) for k in range(1, 7)],
        "unk": [_unk(row.get(f"line_{k}_unknown")) for k in range(1, 7)],
        "cov": row.get("coverage"),
    }
    if row.get("horizon") == "short":
        out["bs"] = _round(row.get("base_score"), BS_DECIMALS)
    return out


def _stock_ok(sid: Any) -> bool:
    return isinstance(sid, str) and sid != "" and sid != MARKET_ID


def load_names(pool_path: Path, wanted: set[str]) -> dict[str, list[str | None]]:
    """`data/pool.json` → `{stock_id: [stock_name, industry_category]}`，只含 `wanted`；同代號多列取 `date` 最新。"""
    try:
        with pool_path.open("r", encoding="utf-8") as f:
            pool = json.load(f)
        rows = pool.get("rows") if isinstance(pool, dict) else None
        if not isinstance(rows, list):
            raise ValueError("形狀不對（需 dict 且 rows 為 list）")
    except (OSError, ValueError) as e:
        warn(f"pool.json 讀不到，names 留空：{pool_path}: {e}")
        return {}
    best: dict[str, tuple[str, dict[str, Any]]] = {}
    for r in rows:
        if not isinstance(r, dict):
            continue
        sid = r.get("stock_id")
        if sid not in wanted:
            continue
        d = r.get("date") or ""
        if not isinstance(d, str):
            d = ""
        cur = best.get(sid)
        if cur is None or d > cur[0]:
            best[sid] = (d, r)
    names: dict[str, list[str | None]] = {}
    for sid, (_, r) in best.items():
        nm = r.get("stock_name")
        if not isinstance(nm, str) or nm == "":
            continue
        ind = r.get("industry_category")
        names[sid] = [nm, ind if isinstance(ind, str) else None]
    return names


def build_latest(payload: dict[str, Any], generated_from: str, pool_path: Path) -> dict[str, Any]:
    rows = payload["rows"]
    market: dict[str, dict[str, Any]] = {}
    stocks: dict[str, dict[str, Any]] = {}
    calibrated_all = bool(rows)
    for row in rows:
        if not isinstance(row, dict):
            continue
        if row.get("calibrated") != 1:
            calibrated_all = False
        h = row.get("horizon")
        if h not in HORIZONS:
            continue
        sid = row.get("stock_id")
        mk = row.get("market")
        if sid == MARKET_ID:
            market[f"{mk}|{h}"] = entry_from_row(row)
            continue
        if not _stock_ok(sid):
            continue
        st = stocks.get(sid)
        if st is None:
            st = {"market": mk, "in_rank_pool": None, "short": None, "swing": None, "mid": None}
            stocks[sid] = st
        irp = row.get("in_rank_pool")
        if irp is not None and st["in_rank_pool"] is None:
            st["in_rank_pool"] = 1 if irp else 0
        st[h] = entry_from_row(row)
    names = load_names(pool_path, set(stocks))
    return {
        "schema": SCHEMA,
        "date": payload.get("tpe_date"),
        "data_version": payload.get("data_version"),
        "params_sha": payload.get("params_sha"),
        "text_version": payload.get("text_version"),
        "calibrated": calibrated_all,
        "generated_from": generated_from,
        "n_rows": len(rows),
        "names": names,
        "market": market,
        "stocks": stocks,
    }


def series_key(row: dict[str, Any]) -> str | None:
    h = row.get("horizon")
    if h not in HORIZONS:
        return None
    sid = row.get("stock_id")
    if sid == MARKET_ID:
        return f"{row.get('market')}|{h}"
    if not _stock_ok(sid):
        return None
    return f"{sid}|{h}"


def build_timeline(files: list[Path], n: int, latest_payload: dict[str, Any] | None = None) -> dict[str, Any]:
    """最後 N 個檔 → `{schema, dates, series}`。`latest_payload` 是已讀好的最新檔（省一次 6 MB 的重讀）。"""
    picked = files[-n:] if n > 0 else []
    dates = [p.name[:-5] for p in picked]
    per_day: list[dict[str, list[Any]] | None] = []
    for p in picked:
        try:
            if latest_payload is not None and p == files[-1]:
                payload = latest_payload
            else:
                payload = load_scores(p)
        except BuildWebError as e:
            warn(f"timeline 該日填 null：{e}")
            per_day.append(None)
            continue
        day: dict[str, list[Any]] = {}
        for row in payload["rows"]:
            if not isinstance(row, dict):
                continue
            key = series_key(row)
            if key is None:
                continue
            day[key] = [row.get("king_wen"), row.get("line_states")]
        per_day.append(day)
    keys: set[str] = set()
    for day in per_day:
        if day:
            keys.update(day)
    series = {k: [(day.get(k) if day else None) for day in per_day] for k in sorted(keys)}
    return {"schema": SCHEMA, "dates": dates, "series": series}


def dumps(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n"


def write_json(path: Path, obj: Any) -> None:
    """先寫 `<name>.tmp` 再 `os.replace`：不留半殘檔（daily.yml 失敗時會 checkout 還原，這裡再守一層）。"""
    tmp = path.with_name(path.name + ".tmp")
    with tmp.open("w", encoding="utf-8", newline="\n") as f:
        f.write(dumps(obj))
    os.replace(tmp, path)


def build(root: Path, out: Path, n: int) -> tuple[Path, Path]:
    scores_dir = root / "data" / "scores"
    files = list_score_files(scores_dir)
    if not files:
        raise BuildWebError(f"{scores_dir} 沒有任何 YYYY-MM-DD.json 分數檔")
    latest_path = files[-1]
    latest_payload = load_scores(latest_path)          # 最新檔壞掉＝中止（latest.json 沒有東西可寫）
    try:
        rel = latest_path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        rel = latest_path.as_posix()
    latest = build_latest(latest_payload, rel, root / "data" / "pool.json")
    timeline = build_timeline(files, n, latest_payload)
    try:
        out.mkdir(parents=True, exist_ok=True)
        p_latest = out / "latest.json"
        p_timeline = out / "timeline.json"
        write_json(p_latest, latest)
        write_json(p_timeline, timeline)
    except OSError as e:
        raise BuildWebError(f"out 目錄不可寫：{out}: {e}") from e
    return p_latest, p_timeline


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="P4 預覽版：由 data/scores/ 產 data/web/latest.json 與 timeline.json")
    ap.add_argument("--root", default=".", help="repo 根（讀 data/scores/、data/pool.json）")
    ap.add_argument("--n", type=int, default=DEFAULT_N, help=f"timeline 取最近幾個交易日（預設 {DEFAULT_N}）")
    ap.add_argument("--out", default="data/web", help="輸出目錄（相對路徑以 --root 為準）")
    args = ap.parse_args(argv)
    root = Path(args.root)
    out = Path(args.out)
    if not out.is_absolute():
        out = root / out
    try:
        p_latest, p_timeline = build(root, out, args.n)
    except BuildWebError as e:
        print(f"[build_web] 中止：{e}", file=sys.stderr)
        return 2
    print(f"[build_web] {p_latest} {p_latest.stat().st_size} bytes；{p_timeline} {p_timeline.stat().st_size} bytes")
    return 0


if __name__ == "__main__":
    sys.exit(main())
