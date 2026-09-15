#!/usr/bin/env python3
"""從種子重算每日班分數與狀態鏈（離線、零網路）＋（可選）以 Hetzner 對帳傾印驗證重算結果。

背景（`docs/P2-DAILY-PLAN.md` §7.6.3「第一輪對帳根因」）：計分當下 `data/factors.json` 缺除權息事件，已產出的分數檔與跨日
狀態 `data/state/cross.json` 被污染且**不自癒**，只能從種子整段重算。RCA 已證：`d4a7788` 種子＋當時全部種子原料包＋補齊事件的
`factors.json`，`daily_core.run_offline` 重算 09-01 逐位等於 Hetzner 參考（EXP5）。本腳本把「建世界 → 重算 → 比對」做成可重複執行的一支。

    venv312/bin/python scripts/recompute_from_seed.py --out /tmp/recompute --python-check
    venv312/bin/python scripts/recompute_from_seed.py --out /tmp/recompute --from 2026-09-01 --to 2026-09-07 \\
        --dump scratchpad/diff2.jsonl.gz --python-check

步驟：①`--seed-commit` 的 `data/state/cross.json`＋`runs/collect/*-daily.json.gz`（種子原料包；`--seed-bundles N` 只取最後 N 份）
以 `git archive` 匯到 `--out`；②`data/pool.json`／`factors.json`／`fundamentals.json`／`calendar_*.json`（＋`data/entrants/*.json.gz`
若有）＝`--data-ref` 版本；③加入 `--from`～`--to` 各交易日的原料包（`--bundles-ref`）；④逐日 `daily_core.run_offline`（每日班同一支、
同一組 `dumps`／`write_json`，決定性）；⑤產出 `<out>/data/scores/<T>.json` 與最終 `<out>/data/state/cross.json`；⑥每日印列數與
「對 `--data-ref` 現行分數檔」的差異列數，並寫 `<out>/recompute-manifest.json`／`recompute-summary.json`。
**不動 repo 的 `data/`**——全部寫在 `--out`；`--from` 必須是種子 `last_date` 之後的第一個交易日（狀態鏈不可跳日）。

驗證模式（`--dump <parity_check --dump 的 jsonl[.gz]>`）：把傾印還原成「完整參考分數」——以 `--data-ref` 現行分數列為底，蓋上
dump 內 `kind=score` 的 `a`（參考值）；`col=null` 的列＝整列只在一側：`a` 為整列即補進參考、`a` 為 null 即自參考刪掉。再與重算列
逐欄比對，印每日「逐位相同／差異數＋前 20 筆」。傾印的值是 `ScoreStore.rows_for_day` 形狀（`lines_*` 為 list、`flags` 為 dict），
分數檔是 `flatten_row` 形狀（6 字元字串／JSON 字串），轉換走 `scores_io` 同一組函式（`bits_text`／`flatten_row`）。
**限制**：dump 只記參考與 repo 不同的欄，dump 外的欄只能證明「與現行分數檔相同」——與 RCA `cmp3.py` 同一套判準。

回傳碼：0 完成（驗證模式＝每一日逐位相同）；1 驗證模式有差異或有日子無法驗證；2 設定／資料錯誤（訊息在 stderr）。
Python：對帳／重現一律 ≥3.12（§7.6.3 附帶發現：CPython 3.12 起內建 `sum()` 對 float 改 Neumaier 補償加法，`scan.py` 的
「收盤恰等於 MA」邊界會隨版本變）；`--python-check` 在 <3.12 直接拒跑（rc 2），不帶時只印警告。
記憶體：全部 1,618 份種子包常駐約 2.8 GB（480 份實測 850 MB 線性推估）；`--seed-bundles N` 只匯最後 N 份——**N < 全部時最早幾日
（約 09-01～09-09）與參考可能有 §7.0 暖機邊界差異**，驗證時要看得出來。
"""
from __future__ import annotations

import argparse
import gzip
import json
import shutil
import subprocess
import sys
import tarfile
import time
from pathlib import Path
from typing import Any, Callable

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "src"))

from iching import bundle_io as B  # noqa: E402
from iching import daily_core as DC  # noqa: E402
from iching import replay_state as RS  # noqa: E402
from iching import scores_io as SI  # noqa: E402
from iching.features_io import FeatureStoreError  # noqa: E402
from iching.fundamentals import FundamentalsError  # noqa: E402
from iching.run_common import ReplayDriverError  # noqa: E402

DEFAULT_SEED_COMMIT = "d4a7788"
DEFAULT_REF = "origin/main"
CALENDAR_US_FILE = "data/calendar_us.json"
DATA_FILES_REQUIRED = (DC.POOL_FILE, DC.FACTORS_FILE, DC.FUND_FILE, DC.CALENDAR_TPE_FILE)
DATA_FILES_OPTIONAL = (CALENDAR_US_FILE,)
BUNDLE_SUFFIX = f"-{B.BAND}.json.gz"
MANIFEST_FILE = "recompute-manifest.json"
SUMMARY_FILE = "recompute-summary.json"
ROW_KEY = ("market", "stock_id", "horizon")
PY_MIN = (3, 12)
SHOW_DIFFS = 20
RC_OK, RC_DIFF, RC_SETUP = 0, 1, 2
RUN_ERRORS = (DC.DailyCoreError, ReplayDriverError, RS.ReplayStateError, B.BundleError, FeatureStoreError, FundamentalsError,
              SI.ScoreStoreError, OSError, ValueError, KeyError, TypeError)


class RecomputeError(RuntimeError):
    pass


# ---------------------------------------------------------------------------
# git（只讀：rev-parse／ls-tree／show／archive）
def _git(repo: Path, *args: str) -> bytes:
    r = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, check=False)
    if r.returncode != 0:
        raise RecomputeError(f"git {' '.join(args[:2])} 失敗：{r.stderr.decode('utf-8', 'replace').strip()[:300]}")
    return r.stdout


def git_rev(repo: Path, ref: str) -> str:
    return _git(repo, "rev-parse", "--verify", f"{ref}^{{commit}}").decode().strip()


def git_ls(repo: Path, ref: str, path: str) -> list[str]:
    """`ref` 下 `path` 的全部檔案路徑（遞迴、升冪）；`path` 不存在回空。"""
    out = _git(repo, "ls-tree", "-r", "--name-only", ref, "--", path).decode("utf-8")
    return sorted(line for line in out.splitlines() if line)


def git_show(repo: Path, ref: str, path: str) -> bytes:
    return _git(repo, "show", f"{ref}:{path}")


def git_extract(repo: Path, ref: str, paths: list[str], dest: Path) -> int:
    """`git archive ref <paths>` 串流解到 `dest`（相對路徑原樣）。回檔案數。"""
    if not paths:
        return 0
    dest = Path(dest)
    dest.mkdir(parents=True, exist_ok=True)
    p = subprocess.Popen(["git", "-C", str(repo), "archive", "--format=tar", ref, *paths],
                         stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    n = 0
    try:
        assert p.stdout is not None
        with tarfile.open(fileobj=p.stdout, mode="r|") as tf:
            for m in tf:
                if not m.isfile():
                    continue
                tf.extract(m, dest, filter="data")
                n += 1
    finally:
        err = p.stderr.read().decode("utf-8", "replace") if p.stderr else ""
        rc = p.wait()
    if rc != 0:
        raise RecomputeError(f"git archive {ref} 失敗：{err.strip()[:300]}")
    if n != len(paths):
        raise RecomputeError(f"git archive {ref} 解出 {n} 檔 ≠ 要求的 {len(paths)} 檔")
    return n


def bundle_date(name: str) -> str | None:
    base = Path(name).name
    return base[: -len(BUNDLE_SUFFIX)] if base.endswith(BUNDLE_SUFFIX) else None


def bundles_at(repo: Path, ref: str) -> dict[str, str]:
    """`ref` 的 `runs/collect/` 內原料包 {日期: 路徑}。"""
    out: dict[str, str] = {}
    for n in git_ls(repo, ref, B.BUNDLE_DIR):
        d = bundle_date(n)
        if d:
            out[d] = n
    return out


def scores_dates_at(repo: Path, ref: str) -> list[str]:
    return sorted(Path(n).name[: -len(".json")] for n in git_ls(repo, ref, DC.SCORES_DIR) if n.endswith(".json"))


# ---------------------------------------------------------------------------
# 世界
def resolve_range(calendar: list[str], seed_last: str, frm: str | None, to: str | None, score_dates: list[str]) -> list[str]:
    """重算日序列：`--from` 預設＝種子 `last_date` 之後第一個交易日（給了也必須等於它——狀態鏈不可跳日）；
    `--to` 預設＝`--data-ref` 上最後一個有分數檔的日期。"""
    nxt = next((d for d in calendar if d > seed_last), None)
    if nxt is None:
        raise RecomputeError(f"日曆最後一日 {calendar[-1]} 不晚於種子 last_date={seed_last}，沒有可重算的交易日")
    frm = frm or nxt
    if frm != nxt:
        raise RecomputeError(f"--from={frm} 必須是種子 last_date={seed_last} 之後的第一個交易日 {nxt}（狀態鏈不可跳日）")
    if to is None:
        if not score_dates:
            raise RecomputeError("--data-ref 上沒有任何 data/scores/<T>.json，無法決定 --to")
        to = score_dates[-1]
    if to < frm:
        raise RecomputeError(f"--to={to} 早於 --from={frm}")
    days = [d for d in calendar if frm <= d <= to]
    if not days or days[0] != frm:
        raise RecomputeError(f"--from={frm} 不在日曆內（日曆 {calendar[0]}～{calendar[-1]}）")
    if to not in days:
        raise RecomputeError(f"--to={to} 不在日曆內（日曆最後一日 {calendar[-1]}；--data-ref 的日曆尚未涵蓋該日？）")
    return days


def build_world(repo: Path, out: Path, *, seed_commit: str, data_ref: str, bundles_ref: str, frm: str | None, to: str | None,
                seed_bundles: int | None, log: Callable[[str], None]) -> dict[str, Any]:
    """①種子（狀態＋原料包）②data 檔＝`--data-ref` ③區間原料包＝`--bundles-ref`。回 manifest（也寫進 `<out>/recompute-manifest.json`）。"""
    out = Path(out)
    seed_sha, data_sha, bundles_sha = git_rev(repo, seed_commit), git_rev(repo, data_ref), git_rev(repo, bundles_ref)
    seed_all = sorted(bundles_at(repo, seed_sha).items())
    if not seed_all:
        raise RecomputeError(f"種子 {seed_commit}（{seed_sha[:7]}）沒有任何 {B.BUNDLE_DIR}/*{BUNDLE_SUFFIX}")
    if seed_bundles is not None:
        if seed_bundles < 1:
            raise RecomputeError("--seed-bundles 必須 ≥1")
        seed_sel = seed_all[-seed_bundles:]
    else:
        seed_sel = seed_all
    cross = RS.CrossDayState.from_json(git_show(repo, seed_sha, DC.STATE_FILE).decode("utf-8"))
    seed_last = str(cross.last_date or "")
    if not seed_last:
        raise RecomputeError(f"種子 {seed_commit} 的 {DC.STATE_FILE} 沒有 last_date")
    late = [d for d, _ in seed_sel if d > seed_last]
    if late:
        raise RecomputeError(f"種子 {seed_commit} 有 {len(late)} 份原料包晚於 last_date={seed_last}（例 {late[0]}），不是乾淨的種子")
    calendar = [str(x) for x in (json.loads(git_show(repo, data_sha, DC.CALENDAR_TPE_FILE).decode("utf-8")).get("dates") or [])]
    if not calendar:
        raise RecomputeError(f"{data_ref} 的 {DC.CALENDAR_TPE_FILE} 沒有 dates")
    days = resolve_range(calendar, seed_last, frm, to, scores_dates_at(repo, data_sha))
    have = bundles_at(repo, bundles_sha)
    missing = [d for d in days if d not in have]
    if missing:
        raise RecomputeError(f"{bundles_ref} 缺 {len(missing)} 日原料包：{missing[:5]}{'…' if len(missing) > 5 else ''}"
                             f"（那些日子每日班沒跑完？縮小 --to）")
    log(f"種子 {seed_commit}={seed_sha[:12]}：last_date={seed_last}，原料包 {len(seed_sel)}/{len(seed_all)} 份"
        f"（{seed_sel[0][0]}～{seed_sel[-1][0]}）")
    git_extract(repo, seed_sha, [DC.STATE_FILE, *(p for _, p in seed_sel)], out)
    for f in DATA_FILES_REQUIRED:
        (out / f).parent.mkdir(parents=True, exist_ok=True)
        (out / f).write_bytes(git_show(repo, data_sha, f))
    optional_got = []
    for f in DATA_FILES_OPTIONAL:
        if git_ls(repo, data_sha, f):
            (out / f).write_bytes(git_show(repo, data_sha, f))
            optional_got.append(f)
    entrants = [n for n in git_ls(repo, data_sha, DC.ENTRANTS_DIR) if n.endswith(".json.gz")]
    git_extract(repo, data_sha, entrants, out)
    log(f"data 檔＝{data_ref}={data_sha[:12]}：{', '.join(DATA_FILES_REQUIRED + tuple(optional_got))}；entrants 側檔 {len(entrants)} 份")
    git_extract(repo, bundles_sha, [have[d] for d in days], out)
    log(f"區間原料包＝{bundles_ref}={bundles_sha[:12]}：{days[0]}～{days[-1]} 共 {len(days)} 日")
    manifest = {"schema": 1, "seed_commit": seed_commit, "seed_sha": seed_sha, "seed_last_date": seed_last,
                "seed_bundles": len(seed_sel), "seed_bundles_total": len(seed_all), "seed_first": seed_sel[0][0], "seed_last": seed_sel[-1][0],
                "data_ref": data_ref, "data_sha": data_sha, "bundles_ref": bundles_ref, "bundles_sha": bundles_sha,
                "days": days, "entrants": len(entrants), "python": sys.version.split()[0]}
    DC.write_json(out / MANIFEST_FILE, manifest)
    return manifest


# ---------------------------------------------------------------------------
# 分數列比對（檔案形狀；鍵 (market, stock_id, horizon)，model_version 當一般欄比）
def rows_by_key(js: dict, what: str) -> dict[tuple, dict]:
    out: dict[tuple, dict] = {}
    for r in js.get("rows") or []:
        k = tuple(str(r.get(c)) for c in ROW_KEY)
        if k in out:
            raise RecomputeError(f"{what} 同鍵重複列 {k}")
        out[k] = r
    return out


def compare_rows(ref: dict[tuple, dict], got: dict[tuple, dict]) -> dict[str, Any]:
    """回 {equal, only_ref, only_got, diff_rows, diff_cells, diffs:[(key, col, ref, got)…]}；相等判準＝Python `==`（同 `diff_scores.diff_day`）。"""
    only_ref, only_got = sorted(set(ref) - set(got)), sorted(set(got) - set(ref))
    equal = diff_rows = diff_cells = 0
    diffs: list[tuple] = []
    for k in sorted(set(ref) & set(got)):
        a, b = ref[k], got[k]
        if a == b:
            equal += 1
            continue
        diff_rows += 1
        for c in sorted(set(a) | set(b)):
            if a.get(c) != b.get(c):
                diff_cells += 1
                diffs.append((k, c, a.get(c), b.get(c)))
    diffs = [(k, None, ref[k], None) for k in only_ref] + [(k, None, None, got[k]) for k in only_got] + diffs
    return {"equal": equal, "only_ref": len(only_ref), "only_got": len(only_got), "diff_rows": diff_rows, "diff_cells": diff_cells,
            "diffs": diffs}


def diag_diff(a: dict | None, b: dict | None) -> list[str]:
    """diag 差異欄（`elapsed_ms` 除外——那是牆鐘）。"""
    a, b = dict(a or {}), dict(b or {})
    a.pop("elapsed_ms", None)
    b.pop("elapsed_ms", None)
    return sorted(c for c in set(a) | set(b) if a.get(c) != b.get(c))


def _fmt(v: Any) -> str:
    s = json.dumps(v, ensure_ascii=False, sort_keys=True, default=str)
    return s if len(s) <= 80 else s[:77] + "…"


def print_diffs(diffs: list[tuple], log: Callable[[str], None], *, ref_name: str, got_name: str, show: int = SHOW_DIFFS) -> None:
    for k, c, a, b in diffs[:show]:
        if c is None:
            side = f"只在{ref_name}" if b is None else f"只在{got_name}"
            log(f"    {'/'.join(k)}  {side}")
        else:
            log(f"    {'/'.join(k)}  {c}: {ref_name}={_fmt(a)} {got_name}={_fmt(b)}")
    if len(diffs) > show:
        log(f"    …另 {len(diffs) - show} 筆")


# ---------------------------------------------------------------------------
# 驗證模式：dump（rows_for_day 形狀）→ 檔案形狀
def db_value_to_file(col: str, v: Any) -> Any:
    """`rows_for_day` 的欄值 → `flatten_row` 的欄值：`lines_*` list → 6 字元字串（`bits_text`）、`flags` dict → JSON 字串（同 `flatten_row`）。"""
    if col in ("lines_provisional", "lines_formal") and isinstance(v, (list, tuple)):
        return SI.bits_text(v)
    if col == "flags" and isinstance(v, dict):
        return json.dumps(v, ensure_ascii=False, sort_keys=True, default=str)
    return v


def db_row_to_file_row(row: dict) -> dict:
    """`rows_for_day` 整列 → 分數檔一列（`model_version` 保留為欄；`data_version`／`text_version` 是檔頂層、拿掉）。走 `flatten_row` 同一支。"""
    r = dict(row)
    mv = r.pop("model_version", None)
    r.pop("data_version", None)
    r.pop("text_version", None)
    flat = SI.flatten_row(r, line_states=r.get("line_states"), streaks=r.get("streaks"), in_rank_pool=r.get("in_rank_pool"))
    return {"model_version": mv, **flat}


def load_dump_scores(path: Path) -> dict[str, list[dict]]:
    """`parity_check --dump` 的 JSON Lines（`.gz` 即 gzip）→ {date: [kind=score 的列…]}（其餘 kind 略過）。"""
    path = Path(path)
    opener = gzip.open if path.suffix == ".gz" else open
    out: dict[str, list[dict]] = {}
    try:
        with opener(path, "rt", encoding="utf-8") as f:
            for line in f:
                if not line.strip():
                    continue
                rec = json.loads(line)
                if rec.get("kind") == "score":
                    out.setdefault(str(rec["date"]), []).append(rec)
    except (OSError, EOFError, ValueError, KeyError) as e:
        raise RecomputeError(f"dump {path} 讀取失敗：{type(e).__name__}: {e}") from e
    return out


def reference_rows(main_rows: dict[tuple, dict], recs: list[dict]) -> dict[tuple, dict]:
    """以現行分數列為底蓋上 dump 的 `a`（參考值）→ 完整參考列。`col=null`：`a` 為整列即補、為 null 即刪。"""
    ref = {k: dict(r) for k, r in main_rows.items()}
    for rec in recs:
        k = tuple(str(rec.get(c)) for c in ROW_KEY)
        col = rec.get("col")
        if col is None:
            if rec.get("a") is None:
                ref.pop(k, None)
            else:
                ref[k] = db_row_to_file_row(rec["a"])
            continue
        if col in ("data_version", "text_version"):
            raise RecomputeError(f"dump 的 {k} 在 {col} 有差異（{rec.get('a')!r} vs {rec.get('b')!r}）＝版本三元組不同，不是可蓋回的欄")
        col = "date" if col == "tpe_trading_date" else col
        if k not in ref:
            raise RecomputeError(f"dump 的 {k} 欄 {col} 在現行分數檔找不到該列——dump 與 --data-ref 的分數檔不是同一版？")
        ref[k][col] = db_value_to_file(col, rec.get("a"))
    return ref


# ---------------------------------------------------------------------------
# 重算
def python_line() -> tuple[str, bool]:
    v = sys.version.split()[0]
    ok = tuple(sys.version_info[:2]) >= PY_MIN
    return (f"Python {v}" + ("" if ok else f" ＜ {PY_MIN[0]}.{PY_MIN[1]} ⚠ 內建 sum() 非 Neumaier，MA 相等邊界與 Hetzner／Actions 不同，"
                                          f"結果不可拿來對帳（§7.6.3）")), ok


def recompute(out: Path, days: list[str], *, window: int, main_scores: Callable[[str], dict | None],
              dump: dict[str, list[dict]] | None, log: Callable[[str], None]) -> list[dict[str, Any]]:
    """逐日 `run_offline`（原料包一次載入記憶體），每日對現行分數檔比一次；`dump` 給定時另做參考還原比對。"""
    out = Path(out)
    t0 = time.time()
    bundles = DC.load_bundles(out)
    log(f"原料包載入 {len(bundles)} 份（{bundles[0][0]}～{bundles[-1][0]}），{time.time() - t0:.1f}s")
    results: list[dict[str, Any]] = []
    for T in days:
        t1 = time.time()
        res = DC.run_offline(out, T, window=window, bundles=bundles)
        done = [x["date"] for x in res["days"]]
        if done != [T]:
            raise RecomputeError(f"run_offline({T}) 計了 {done}，不是只有 {T}（世界裡有多餘的原料包？）")
        js = DC.read_json(DC.scores_path(out, T), what="scores")
        got = rows_by_key(js, f"重算 {T}")
        rd = res["days"][0]["rebuild"]
        day: dict[str, Any] = {"date": T, "rows": len(got), "elapsed_s": round(time.time() - t1, 1), "n_bundles": rd.get("n_bundles"),
                               "entrants_merged": rd.get("entrants_merged"), "entrants_adopted": rd.get("entrants_adopted")}
        main_js = main_scores(T)
        line = f"{T}: rows={len(got)} 重建 {rd.get('n_bundles')} 份包 {day['elapsed_s']}s"
        if main_js is None:
            day["vs_main"] = None
            line += "；--data-ref 無此日分數檔（不比）"
        else:
            main_rows = rows_by_key(main_js, f"現行 {T}")
            cm = compare_rows(main_rows, got)
            dd = diag_diff(main_js.get("diag"), js.get("diag"))
            day["vs_main"] = {k: v for k, v in cm.items() if k != "diffs"} | {"diag_diff_cols": dd}
            line += (f"；vs 現行分數檔：同 {cm['equal']}／不同列 {cm['diff_rows']}（{cm['diff_cells']} 格）／只在重算 {cm['only_got']}"
                     f"／只在現行 {cm['only_ref']}；diag 差欄 {dd or '無'}")
        log(line)
        if dump is not None:
            recs = dump.get(T, [])
            if main_js is None:
                day["verify"] = None
                log(f"  驗證 {T}: ✗ 無法驗證（--data-ref 無此日分數檔，參考列還原不了）")
            else:
                ref = reference_rows(main_rows, recs)
                cv = compare_rows(ref, got)
                day["verify"] = {k: v for k, v in cv.items() if k != "diffs"} | {"dump_records": len(recs), "ref_rows": len(ref)}
                bad = cv["diff_rows"] + cv["only_ref"] + cv["only_got"]
                if bad == 0:
                    log(f"  驗證 {T}: ✅ 逐位相同（參考 {len(ref)} 列＝重算 {len(got)} 列；dump 覆蓋 {len(recs)} 格）")
                else:
                    log(f"  驗證 {T}: ✗ 差異 {cv['diff_rows']} 列（{cv['diff_cells']} 格）／只在參考 {cv['only_ref']}／只在重算 {cv['only_got']}"
                        f"（參考 {len(ref)} 列、重算 {len(got)} 列；dump 覆蓋 {len(recs)} 格）")
                    print_diffs(cv["diffs"], log, ref_name="參考", got_name="重算")
        results.append(day)
    return results


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="從種子重算每日班分數與狀態鏈（離線），可選以 Hetzner 對帳傾印驗證")
    ap.add_argument("--repo", default=str(REPO), help="git repo（預設本 repo）")
    ap.add_argument("--seed-commit", default=DEFAULT_SEED_COMMIT)
    ap.add_argument("--from", dest="frm", default=None, help="起日（預設＝種子 last_date 之後第一個交易日；給了也必須等於它）")
    ap.add_argument("--to", default=None, help="迄日（預設＝--data-ref 上最後一個有 data/scores/<T>.json 的日期）")
    ap.add_argument("--data-ref", default=DEFAULT_REF, help="pool／factors／fundamentals／calendar／entrants 與現行分數檔的 git ref")
    ap.add_argument("--bundles-ref", default=DEFAULT_REF, help="區間原料包的 git ref")
    ap.add_argument("--out", required=True, help="重算世界（會寫 data/ 與 runs/collect/；須為空目錄或不存在，--force 先清空）")
    ap.add_argument("--window", type=int, default=RS.WINDOW_N)
    ap.add_argument("--dump", default=None, help="parity_check --dump 的傾印（.jsonl 或 .jsonl.gz）；給了就做參考還原驗證")
    ap.add_argument("--seed-bundles", type=int, default=None, help="只匯入種子最後 N 份原料包（省記憶體；N<全部時最早幾日可能有暖機邊界差異）")
    ap.add_argument("--python-check", action="store_true", help=f"Python <{PY_MIN[0]}.{PY_MIN[1]} 直接拒跑")
    ap.add_argument("--force", action="store_true", help="--out 非空時先整個刪掉")
    args = ap.parse_args(argv)

    def log(s: str) -> None:
        print(s, flush=True)

    pyline, py_ok = python_line()
    log(pyline)
    if args.python_check and not py_ok:
        print(f"[recompute 中止] --python-check：{pyline}", file=sys.stderr)
        return RC_SETUP
    out = Path(args.out)
    try:
        if out.exists() and any(out.iterdir()):
            if not args.force:
                raise RecomputeError(f"--out {out} 非空；換目錄或加 --force")
            shutil.rmtree(out)
        repo = Path(args.repo)
        manifest = build_world(repo, out, seed_commit=args.seed_commit, data_ref=args.data_ref, bundles_ref=args.bundles_ref,
                               frm=args.frm, to=args.to, seed_bundles=args.seed_bundles, log=log)
        if args.seed_bundles is not None and manifest["seed_bundles"] < manifest["seed_bundles_total"]:
            log(f"⚠ 只匯入種子最後 {manifest['seed_bundles']}/{manifest['seed_bundles_total']} 份原料包：最早幾日與參考可能有 §7.0 暖機邊界差異")
        dump = load_dump_scores(Path(args.dump)) if args.dump else None
        if dump is not None:
            log(f"dump {args.dump}：kind=score 共 {sum(len(v) for v in dump.values())} 格、{len(dump)} 日")
        data_sha = manifest["data_sha"]
        score_dates = set(scores_dates_at(repo, data_sha))

        def main_scores(T: str) -> dict | None:
            if T not in score_dates:
                return None
            return json.loads(git_show(repo, data_sha, f"{DC.SCORES_DIR}/{T}.json").decode("utf-8"))

        results = recompute(out, manifest["days"], window=args.window, main_scores=main_scores, dump=dump, log=log)
    except (RecomputeError, *RUN_ERRORS) as e:
        print(f"[recompute 中止] {type(e).__name__}: {e}", file=sys.stderr)
        return RC_SETUP
    cross = json.loads((out / DC.STATE_FILE).read_text(encoding="utf-8"))
    summary = {"schema": 1, "manifest": manifest, "days": results, "state_last_date": cross.get("last_date"), "dump": args.dump,
               "window": args.window, "python": sys.version.split()[0]}
    DC.write_json(out / SUMMARY_FILE, summary)
    log(f"完成：{len(results)} 日 → {out / DC.SCORES_DIR}/，狀態鏈 last_date={cross.get('last_date')}；摘要 {out / SUMMARY_FILE}")
    if dump is None:
        return RC_OK
    ok_days = [d["date"] for d in results if d.get("verify") and not (d["verify"]["diff_rows"] + d["verify"]["only_ref"] + d["verify"]["only_got"])]
    bad_days = [d["date"] for d in results if d["date"] not in ok_days]
    log(f"驗證結果：逐位相同 {len(ok_days)} 日 {ok_days}；有差異／無法驗證 {len(bad_days)} 日 {bad_days}")
    return RC_OK if not bad_days else RC_DIFF


if __name__ == "__main__":
    raise SystemExit(main())
