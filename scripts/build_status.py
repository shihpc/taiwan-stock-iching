#!/usr/bin/env python3
"""P4-S 狀態／降級檔：由每日班摘要＋現有產物產 `data/web/status.json`（裁定 #76 §14.2 Q8／§14.5 第 2 點；契約＝`docs/P4-PREVIEW.md` §15）。

    python3 scripts/build_status.py --root . --summary "$RUNNER_TEMP/daily_summary.json" [--now 2026-10-16T23:40:00+08:00]

純標準庫。輸入：
- `--summary`：`scripts/daily_run.py --summary-out` 寫的完整摘要（＝`daily_pipeline.run_pipeline` 回傳值，含 `done`）；缺／壞 →
  `run`＝null、`status`＝degraded、`degraded` 含 `summary_missing`。
- `data/web/latest.json` 的 `date`（讀不到 → `date`＝null、`web_unreadable`）。
- `data/scores/` 檔名最大日（`scores_date`）與該檔 `diag.index_missing`。
- `data/web/ind/market.json` 的 `date`（本爻依據分片；與 `date` 不同 → `ind_stale`）。
- 環境變數 `WEB_OK`／`IND_OK`（daily.yml 的 `steps.web/ind.outputs.ok`）：值為 `"0"` 才算失敗；未設（本機手跑）＝視為成功。

輸出序列化同 `build_web.dumps`（sort_keys、`(",",":")`、ensure_ascii=False、末尾換行），先寫 `.tmp` 再 `os.replace`。
**只在「去掉 `generated_at` 後內容有變」才重寫**（D4）：內容沒變就不動檔（位元組與 mtime 皆不變），所以 `generated_at`
＝狀態內容最後一次改變的時刻（台北 +08:00）。回傳碼：0＝已寫或內容未變；2＝輸出寫不出去。
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import sys
from pathlib import Path
from typing import Any

SCHEMA = 1
TPE = dt.timezone(dt.timedelta(hours=8))
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
REMAINING_SHOW = 10        # run.remaining 只列前 N 個日期（全量個數在 n_remaining），保 <4KB
OFFICIAL_ERRORS_SHOW = 5   # checks.official_errors 只列前 N 筆，每欄截 120 字
STR_MAX = 120
# degraded 代碼（前端 index.html 的 ST_CODE_TXT 有逐一中文對照；新增代碼要兩邊一起改）
CODES = ("summary_missing", "web_unreadable", "web_stale", "scores_unreadable", "ind_stale",
         "series_gap", "series_check_error", "catching_up", "index_missing")


def dumps(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n"


def _read_json(p: Path) -> Any:
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _s(x: Any) -> str:
    return str(x)[:STR_MAX]


def latest_date(root: Path) -> str | None:
    j = _read_json(root / "data" / "web" / "latest.json")
    d = j.get("date") if isinstance(j, dict) else None
    return d if isinstance(d, str) and DATE_RE.match(d) else None


def scores_info(root: Path) -> tuple[str | None, list[str] | None]:
    """(scores_date, index_missing)；目錄空／讀不到 → (None, None)；最大日檔讀不到 → (date, None)。"""
    sd = root / "data" / "scores"
    try:
        dates = sorted(p.stem for p in sd.glob("*.json") if DATE_RE.match(p.stem))
    except OSError:
        dates = []
    if not dates:
        return None, None
    j = _read_json(sd / f"{dates[-1]}.json")
    diag = j.get("diag") if isinstance(j, dict) else None
    im = diag.get("index_missing") if isinstance(diag, dict) else None
    if not isinstance(im, list):
        return dates[-1], None
    return dates[-1], sorted(_s(x) for x in im)


def ind_date(root: Path) -> str | None:
    j = _read_json(root / "data" / "web" / "ind" / "market.json")
    d = j.get("date") if isinstance(j, dict) else None
    return d if isinstance(d, str) else None


def load_summary(p: Path | None) -> dict | None:
    if p is None:
        return None
    j = _read_json(p)
    if not isinstance(j, dict) or j.get("status") not in ("ok", "waiting", "noop"):
        return None
    return j


def _env_ok(name: str) -> bool:
    return os.environ.get(name, "1").strip() != "0"


def _series_check(sg: Any) -> dict | None:
    if not isinstance(sg, dict):
        return None
    if sg.get("error"):
        return {"us": None, "fx": None, "error": _s(sg["error"])}
    out: dict[str, Any] = {"error": None}
    for side in ("us", "fx"):
        gaps = sg.get(f"{side}_gaps")
        out[side] = {"gaps": len(gaps) if isinstance(gaps, list) else 0, "empty": bool(sg.get(f"{side}_empty"))}
    return out


def _short_sale(counts: Any) -> dict | None:
    if not isinstance(counts, dict) or "short_sale_covered" not in counts or "stocks_in_pool" not in counts:
        return None
    return {"covered": counts["short_sale_covered"], "stocks": counts["stocks_in_pool"]}


def build(root: Path, summary_path: Path | None, now: dt.datetime, old: dict | None = None) -> dict:
    root = Path(root)
    S = load_summary(summary_path)
    date = latest_date(root)
    scores_date, index_missing = scores_info(root)
    web_ok, ind_ok = _env_ok("WEB_OK"), _env_ok("IND_OK")
    degraded: list[str] = []
    run = waiting = None
    checks: dict[str, Any] = {"series_gaps": None, "short_sale": None, "index_missing": index_missing or [],
                              "official_errors": []}
    if S is None:
        degraded.append("summary_missing")
    else:
        done = [d for d in (S.get("done") or []) if isinstance(d, dict)]
        rem = [str(x) for x in (S.get("remaining") or [])]
        last = done[-1].get("date") if done else S.get("last_date")
        run = {"result": S["status"], "upto": S.get("upto"), "last_date": last,
               "remaining": rem[:REMAINING_SHOW], "n_remaining": len(rem)}
        if S["status"] == "waiting":
            waiting = {"date": S.get("waiting_date"), "missing": [_s(x) for x in (S.get("missing") or [])]}
            checks["short_sale"] = _short_sale(S.get("counts"))
        elif done:
            checks["short_sale"] = _short_sale(done[-1].get("counts"))
        checks["series_gaps"] = _series_check(S.get("series_gaps"))
        errs = []
        for d in done:
            for e in d.get("official_errors") or []:
                errs.append([_s(x) for x in (e if isinstance(e, (list, tuple)) else [e])])
        checks["official_errors"] = errs[:OFFICIAL_ERRORS_SHOW]
        # noop 且舊檔 date 與本次相同 → 沿用舊檔 checks（noop 沒跑抓取，摘要裡沒有這些檢查；不得把「沒檢查」寫成「乾淨」）
        if (S["status"] == "noop" and isinstance(old, dict) and old.get("schema") == SCHEMA and date is not None
                and old.get("date") == date and isinstance(old.get("checks"), dict)):
            checks = dict(old["checks"])
            checks["index_missing"] = index_missing or []
    if date is None:
        degraded.append("web_unreadable")
    elif not web_ok or (scores_date is not None and date != scores_date):
        degraded.append("web_stale")
    if scores_date is None or index_missing is None:
        degraded.append("scores_unreadable")
    if not ind_ok or (date is not None and ind_date(root) != date):
        degraded.append("ind_stale")
    sg = checks.get("series_gaps")
    if isinstance(sg, dict):
        if sg.get("error"):
            degraded.append("series_check_error")
        elif any(isinstance(sg.get(k), dict) and (sg[k].get("gaps") or sg[k].get("empty")) for k in ("us", "fx")):
            degraded.append("series_gap")
    if run and run["n_remaining"]:
        degraded.append("catching_up")
    if index_missing:
        degraded.append("index_missing")
    status = "waiting" if waiting is not None else ("degraded" if degraded else "ok")
    return {"schema": SCHEMA, "generated_at": now.astimezone(TPE).isoformat(timespec="seconds"), "date": date,
            "status": status, "run": run, "waiting": waiting, "scores_date": scores_date,
            "products": {"web": web_ok, "ind": ind_ok}, "checks": checks, "degraded": degraded,
            "note": make_note(status, date, waiting, degraded, run, scores_date)}


MISSING_TXT = {"index:twse": "上市加權指數", "index:tpex": "櫃買指數", "stocks": "個股價量", "inst": "個股法人買賣超",
               "margin": "個股融資融券", "shareholding": "外資持股", "short_sale": "借券賣出餘額", "total_margin": "大盤融資餘額",
               "futures_daily": "台指期行情", "futures_inst": "台指期法人未平倉", "vix": "臺指選擇權波動率指數",
               "official_inst:twse": "證交所三大法人", "official_inst:tpex": "櫃買三大法人",
               "official_amount:twse": "證交所成交金額", "official_amount:tpex": "櫃買成交金額"}
CODE_TXT = {"summary_missing": "每日班摘要讀不到、本次執行結果未知", "web_unreadable": "網頁資料檔讀不到",
            "web_stale": "網頁資料未更新", "scores_unreadable": "分數檔讀不到", "ind_stale": "本爻依據未更新",
            "series_gap": "美股／匯率序列有缺日", "series_check_error": "美股／匯率序列檢查失敗",
            "catching_up": "尚有交易日待補", "index_missing": "大盤指數缺值"}


def make_note(status: str, date: str | None, waiting: dict | None, degraded: list[str], run: dict | None,
              scores_date: str | None) -> str:
    """一句繁中（前端另依代碼自組說明；本句供 Worker／log 等不讀代碼的消費端）。"""
    if status == "waiting":
        miss = "、".join(MISSING_TXT.get(m, m) for m in waiting.get("missing") or []) or "核心資料"
        return f"等待資料發布：{waiting.get('date')} 尚缺{miss}，維持 {date or '—'} 的最後有效卦。"
    if status == "degraded":
        parts = []
        for c in degraded:
            t = CODE_TXT.get(c, c)
            if c == "catching_up" and run:
                t = f"尚有 {run['n_remaining']} 個交易日待補"
            elif c == "web_stale" and scores_date and date and scores_date != date:
                t = f"網頁資料停在 {date}（分數檔已到 {scores_date}）"
            parts.append(t)
        return "部分降級：" + "；".join(parts) + "。"
    return f"正常：資料日 {date}。"


def strip_gen(obj: Any) -> Any:
    return {k: v for k, v in obj.items() if k != "generated_at"} if isinstance(obj, dict) else obj


def write_if_changed(path: Path, obj: dict, old: Any) -> bool:
    if isinstance(old, dict) and strip_gen(old) == strip_gen(obj):
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    with tmp.open("w", encoding="utf-8", newline="\n") as f:
        f.write(dumps(obj))
    os.replace(tmp, path)
    return True


def parse_now(s: str | None) -> dt.datetime:
    if not s:
        return dt.datetime.now(TPE)
    t = dt.datetime.fromisoformat(s)
    return t if t.tzinfo else t.replace(tzinfo=TPE)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="產 data/web/status.json（P4-S 狀態／降級）")
    ap.add_argument("--root", default=str(Path(__file__).resolve().parent.parent))
    ap.add_argument("--summary", default=None, help="daily_run.py --summary-out 寫的摘要 JSON")
    ap.add_argument("--out", default=None, help="預設 <root>/data/web/status.json")
    ap.add_argument("--now", default=None, help="ISO 時刻（測試注入；無時區視為台北）")
    a = ap.parse_args(argv)
    root = Path(a.root)
    out = Path(a.out) if a.out else root / "data" / "web" / "status.json"
    old = _read_json(out)
    obj = build(root, Path(a.summary) if a.summary else None, parse_now(a.now), old=old)
    try:
        changed = write_if_changed(out, obj, old)
    except OSError as e:
        print(f"[status] 寫不出 {out}：{type(e).__name__}: {e}", file=sys.stderr)
        return 2
    print(f"[status] {obj['status']} date={obj['date']} degraded={obj['degraded']} "
          f"{'已寫' if changed else '內容未變、不重寫'} {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
