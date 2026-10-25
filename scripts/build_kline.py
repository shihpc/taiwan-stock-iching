#!/usr/bin/env python3
"""P4-K 日 K：由 `runs/collect/` 原料包產網頁用分片 `data/web/k/<代號前 2 字>.json`＋觀大勢 `market.json`。

    python3 scripts/build_kline.py --root . [--out data/web/k]

依據：裁定 #76 §14.2 Q3（夜間管線自原料包產出、**原始價**＋標除權息日、120 個交易日）；
畫面內容（K 棒＋成交量＋除權息標記＋MA20／MA60、觀大勢放加權／櫃買指數日 K）＝使用者 2026-10-25 選擇。
契約與實測見 `docs/P4-PREVIEW.md` §19。**純標準庫、不碰網路、只讀**（原料包、`data/web/latest.json`、
`data/calendar_tpe.json`、`data/factors.json`）；不改計分、分數、指紋。描述性顯示：只整理原始 OHLCV，不判方向。

## 流程

1. T＝`data/web/latest.json` 的 `date`；本站涵蓋的代號＝其 `stocks` 的鍵。`runs/collect/<T>-daily.json.gz` 不存在 → 中止。
2. 取日曆（`data/calendar_tpe.json`）中 ≤T 的最近 `N_OUT + MA_LONG − 1`（179）個交易日；每一天都要有原料包，缺任何一天 → 中止
   （寧可不更新、讓前端以 date 不一致不畫，也不畫出少一根 K 棒卻看不出來的圖）。日曆不足 179 天時以實有為準。
3. 輸出最近 `N_OUT`（120）天；之前的 59 天只用來算均線。
4. 個股每天一列 `[o,h,l,c,v]`：價格為原料包原始價（**不還原**），v＝成交股數 ÷ 1000 四捨五入（張）。
   該日原料包無此股、或 open／high／low／close 任一為 null → 該日為 null。
5. `m20`／`m60`：原始收盤的簡單移動平均，窗內任一天收盤缺 → null；四捨五入到小數 2 位（指數同）。
   原始價在除權息日會跳空，均線在之後一段期間會失真（前端必須註明）。
6. `x`：`data/factors.json` 中該股 ex_date 落在輸出區間的列 `[ex_date, 除權息前參考價, 除權息後參考價]`，完全相同的列去重、依日期排序。
   該檔每列無事件類型（`src/iching/factor_sources.py`），故只標日期與參考價、不寫類型。
7. 個股分片：latest 每個代號前 2 字都寫一片（片內無資料＝`stocks:{}`），輸出區間內一天資料都沒有的代號不列；本次沒產的舊片刪除。
8. `market.json`：`idx.twse`／`idx.tpex`＝指數 OHLC＋成交值（`official.<mk>.amount_k` ÷ 1e5＝億元，小數 1 位）＋m20／m60。

序列化同 `build_events_web.dumps`；先寫全部 `.tmp` 再逐一 `os.replace`。

## 回傳碼

0 成功；2 中止（latest／日曆／factors 讀不到或形狀不對、T 不在日曆、缺原料包、out 目錄不可寫）。讀檔／計算階段的中止不寫任何檔、
不刪舊檔；寫檔階段若在逐一 `os.replace` 中途失敗，每日班會把整個 `data/web/k/` 還原成 HEAD。
"""
from __future__ import annotations

import argparse
import gzip
import json
import os
import re
import sys
from pathlib import Path
from typing import Any, Mapping, Sequence

SCHEMA = 1
N_OUT = 120                                      # 裁定 #76 Q3：120 個交易日
MA_SHORT, MA_LONG = 20, 60                       # 使用者 2026-10-25 選「再加 MA20／MA60 均線」
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
SHARD_RE = re.compile(r"^[0-9A-Za-z]{1,2}\.json$")
MARKET_FILE = "market.json"
COLS = ["o", "h", "l", "c", "v"]
MARKETS = ("twse", "tpex")


class BuildKlineError(Exception):
    pass


def dumps(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n"


def _read_json(p: Path) -> Any:
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise BuildKlineError(f"讀不到 {p}: {e}") from e


def _num(v: Any) -> float | None:
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    f = float(v)
    return f if f == f and f not in (float("inf"), float("-inf")) else None


def load_latest(root: Path) -> tuple[str, set[str]]:
    j = _read_json(root / "data" / "web" / "latest.json")
    if not isinstance(j, dict) or not isinstance(j.get("date"), str) or not DATE_RE.match(j["date"]) \
            or not isinstance(j.get("stocks"), dict):
        raise BuildKlineError("data/web/latest.json 形狀不對（date／stocks）")
    return j["date"], {str(k) for k in j["stocks"]}


def load_calendar(root: Path) -> list[str]:
    j = _read_json(root / "data" / "calendar_tpe.json")
    dates = j.get("dates") if isinstance(j, dict) else None
    if not isinstance(dates, list) or not all(isinstance(d, str) and DATE_RE.match(d) for d in dates):
        raise BuildKlineError("data/calendar_tpe.json 形狀不對（dates）")
    return sorted(set(dates))


def load_factors(root: Path) -> dict[str, list[list[Any]]]:
    """{代號: [[ex_date, before, after], ...]}（完全相同的列去重）。"""
    j = _read_json(root / "data" / "factors.json")
    rows = j.get("rows") if isinstance(j, dict) else None
    if not isinstance(rows, list):
        raise BuildKlineError("data/factors.json 形狀不對（rows）")
    out: dict[str, set[tuple]] = {}
    for r in rows:
        if not (isinstance(r, list) and len(r) == 4 and isinstance(r[0], str) and isinstance(r[1], str) and DATE_RE.match(r[1])):
            raise BuildKlineError(f"data/factors.json 列形狀不對：{r!r}")
        out.setdefault(r[0], set()).add((r[1], _num(r[2]), _num(r[3])))
    return {k: [list(t) for t in sorted(v, key=lambda t: (t[0], -1 if t[1] is None else t[1], -1 if t[2] is None else t[2]))]
            for k, v in out.items()}


def read_bundle(root: Path, d: str) -> dict[str, Any]:
    p = root / "runs" / "collect" / f"{d}-daily.json.gz"
    try:
        with gzip.open(p, "rt", encoding="utf-8") as f:
            b = json.load(f)
    except (OSError, ValueError) as e:
        raise BuildKlineError(f"讀不到原料包 {p}: {e}") from e
    if not isinstance(b, dict) or b.get("tpe_date") != d or not isinstance(b.get("stocks"), dict):
        raise BuildKlineError(f"原料包形狀不對或日期不符：{p}")
    return b


def pick_dates(cal: Sequence[str], T: str) -> list[str]:
    if T not in cal:
        raise BuildKlineError(f"T={T} 不在 data/calendar_tpe.json")
    upto = [d for d in cal if d <= T]
    return upto[-(N_OUT + MA_LONG - 1):]


def bar(o: Any, h: Any, l: Any, c: Any, v: float | None) -> list[Any] | None:
    px = [_num(o), _num(h), _num(l), _num(c)]
    if any(x is None for x in px):
        return None
    return px + [v]


def sma(closes: Sequence[float | None], n: int, n_out: int) -> list[float | None]:
    """closes 的最後 n_out 天各自的 n 日簡單均線；窗不足或窗內有 None → None。"""
    out: list[float | None] = []
    start = len(closes) - n_out
    for i in range(start, len(closes)):
        w = closes[i - n + 1:i + 1] if i - n + 1 >= 0 else None
        out.append(round(sum(w) / n, 2) if w is not None and len(w) == n and all(x is not None for x in w) else None)
    return out


def series(bars: Sequence[list[Any] | None], n_out: int) -> dict[str, Any]:
    closes = [b[3] if b else None for b in bars]
    return {"k": list(bars[-n_out:]), "m20": sma(closes, MA_SHORT, n_out), "m60": sma(closes, MA_LONG, n_out)}


def collect(root: Path) -> dict[str, Any]:
    T, site = load_latest(root)
    cal = load_calendar(root)
    days = pick_dates(cal, T)
    missing = [d for d in days if not (root / "runs" / "collect" / f"{d}-daily.json.gz").is_file()]
    if missing:
        raise BuildKlineError(f"缺原料包 {len(missing)} 天（例 {missing[:3]}）")
    factors = load_factors(root)
    n_out = min(N_OUT, len(days))
    out_dates = days[-n_out:]
    stock_bars: dict[str, list[list[Any] | None]] = {sid: [] for sid in site}
    mk_bars: dict[str, list[list[Any] | None]] = {mk: [] for mk in MARKETS}
    for d in days:
        b = read_bundle(root, d)
        st = b["stocks"]
        for sid in site:
            r = st.get(sid)
            if isinstance(r, dict):
                vol = _num(r.get("volume"))
                stock_bars[sid].append(bar(r.get("open"), r.get("high"), r.get("low"), r.get("close"),
                                           None if vol is None else round(vol / 1000)))
            else:
                stock_bars[sid].append(None)
        idx = b.get("index") if isinstance(b.get("index"), dict) else {}
        off = b.get("official") if isinstance(b.get("official"), dict) else {}
        for mk in MARKETS:
            r = idx.get(mk) if isinstance(idx.get(mk), dict) else {}
            o = off.get(mk) if isinstance(off.get(mk), dict) else {}
            amt = _num(o.get("amount_k"))
            mk_bars[mk].append(bar(r.get("open"), r.get("high"), r.get("low"), r.get("close"),
                                   None if amt is None else round(amt / 1e5, 1)))
    stocks: dict[str, dict[str, Any]] = {}
    for sid in sorted(site):
        bars = stock_bars[sid]
        if not any(bars[-n_out:]):
            continue
        s = series(bars, n_out)
        s["x"] = [r for r in factors.get(sid, []) if out_dates[0] <= r[0] <= T]
        stocks[sid] = s
    return {"T": T, "dates": out_dates, "site": site, "stocks": stocks,
            "idx": {mk: series(mk_bars[mk], n_out) for mk in MARKETS}, "n_days_read": len(days)}


def build_payloads(res: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    head = {"schema": SCHEMA, "date": res["T"], "dates": list(res["dates"]), "cols": list(COLS), "price_basis": "raw",
            "ma": [MA_SHORT, MA_LONG]}
    groups: dict[str, dict[str, Any]] = {sid[:2]: {} for sid in res["site"]}
    for sid, s in res["stocks"].items():
        groups[sid[:2]][sid] = s
    out = {f"{g}.json": {**head, "unit": {"v": "張"}, "stocks": dict(sorted(st.items()))} for g, st in groups.items()}
    out[MARKET_FILE] = {**head, "unit": {"v": "億元"}, "idx": dict(res["idx"])}
    return out


def write_all(out_dir: Path, payloads: Mapping[str, Any]) -> list[Path]:
    """先全部寫 `.tmp` 再逐一 `os.replace`；成功後刪掉本次沒有的舊分片（只刪符合分片／market 檔名者）。"""
    try:
        out_dir.mkdir(parents=True, exist_ok=True)
        tmps = []
        for name in sorted(payloads):
            p = out_dir / name
            tmp = p.with_name(p.name + ".tmp")
            with tmp.open("w", encoding="utf-8", newline="\n") as f:
                f.write(dumps(payloads[name]))
            tmps.append((tmp, p))
        for tmp, p in tmps:
            os.replace(tmp, p)
        for p in out_dir.iterdir():
            if p.is_file() and p.name not in payloads and (SHARD_RE.match(p.name) or p.name == MARKET_FILE):
                p.unlink()
    except OSError as e:
        raise BuildKlineError(f"out 目錄不可寫：{out_dir}: {e}") from e
    return [out_dir / n for n in sorted(payloads)]


def build(root: Path, out: Path) -> tuple[list[Path], dict[str, Any]]:
    res = collect(root)
    return write_all(out, build_payloads(res)), res


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="P4-K：日 K 網頁分片 data/web/k/（docs/P4-PREVIEW.md §19）")
    ap.add_argument("--root", default=".", help="repo 根（讀 data/web/latest.json、data/calendar_tpe.json、data/factors.json、runs/collect/）")
    ap.add_argument("--out", default="data/web/k", help="輸出目錄（相對路徑以 --root 為準）")
    args = ap.parse_args(argv)
    root = Path(args.root)
    out = Path(args.out)
    if not out.is_absolute():
        out = root / out
    try:
        paths, res = build(root, out)
    except BuildKlineError as e:
        print(f"[build_kline] 中止（讀檔／計算階段中止＝未寫任何檔；寫檔階段中止可能留下部分新片）：{e}", file=sys.stderr)
        return 2
    total = sum(p.stat().st_size for p in paths)
    print(f"[build_kline] T={res['T']} 輸出 {res['dates'][0]}～{res['dates'][-1]}（{len(res['dates'])} 天，讀 {res['n_days_read']} 份原料包）；"
          f"個股 {len(res['stocks'])} 檔；檔數 {len(paths)}（含 {MARKET_FILE}）共 {total} bytes")
    return 0


if __name__ == "__main__":
    sys.exit(main())
