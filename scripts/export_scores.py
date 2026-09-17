#!/usr/bin/env python3
"""從回補層 `scores.db` 匯出逐日分數檔 `data/scores/<T>.json`（P3 §2 #10、裁定 #49 Q13：Hetzner 以 PIT 池全量重播後，
主線既有的分數檔要用重播結果覆蓋；`hetzner_pit.sh` 只匯種子，逐日分數靠這支）。

    python3 scripts/export_scores.py --cache-dir cache --out . --from 2026-09-01 --to 2026-09-16 --force

產出＝每日班 `daily_core.run_offline` 寫的同一種檔（`scores_payload` 形狀、`DC.write_json`／`DC.dumps` 同一支序列化），
**目標是位元組相同**（`tests/test_export_scores.py` 在合成世界同時跑兩條路徑逐位比對）。能做到的依據：`scores` 表每一欄
就是 `flatten_row` 的輸出（列先攤平再各寫一份到 db 與檔），本檔對欄值**零轉換**、只做 `versions` JOIN 還原 `model_version`，
排序與 `scores_payload` 同一把鍵 `(market, stock_id, horizon, model_version)`。

## `diag` 欄位（與每日班相比）

每日班 `diag`＝`replay_step.step` 的 12 個鍵；`replay_day` 表只落地其中 10 個（`scores_io.py` `_DDL`）。逐欄交代：

| 欄 | 來源 | 說明 |
|---|---|---|
| `model_version_twse`／`model_version_tpex`／`n_market_rows`／`n_stocks`／`n_in_pool`／`n_stock_rows`／`n_stock_any_unknown`／`n_market_any_unknown` | `replay_day` 同名欄 | 原值 |
| `index_missing` | `replay_day.index_missing`（逗號串） | 還原成 list（空字串＝`[]`） |
| `elapsed_ms` | `replay_day.elapsed_ms` | **是重播那一班 `step()` 的耗時，不是每日班的**；兩條路徑必然不同，parity 一律排除此欄 |
| `text_version` | `replay_meta.params_json["text_version"]`，並核對該日 `versions.text_version` 一致 | 不在 `replay_day`，但 db 內另有兩處記著 |
| `rank_pool_size` | **db 沒有，省略** | ＝`len(cross.adv.eligible())`（T 當日排名池大小，含當日沒有列的池內檔），`n_in_pool` 只數有列且在池者，兩者不等（線上 2026-09-01：895 vs 891），無法由 `scores` 表重建，**不偽造**。`parity_check.py` 的 9 欄比對本來就不含它 |

回傳碼：0 全部寫出或已相同；1 有檔案內容不同且未給 `--force`（一個都不覆蓋，摘要列出）；2 中止（找不到 db／data_version／區間無日）。
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "src"))

from iching import daily_core as DC  # noqa: E402
from iching.scores_io import SCALAR_COLS, ScoreStore, ScoreStoreError  # noqa: E402

DIAG_DB_COLS = ("model_version_twse", "model_version_tpex", "n_market_rows", "n_stocks", "n_in_pool", "n_stock_rows",
                "n_stock_any_unknown", "n_market_any_unknown", "elapsed_ms")
DIAG_NOT_IN_DB = ("rank_pool_size",)                  # 每日班 diag 有、db 沒有 → 匯出檔省略（檔頭表）
DIAG_VOLATILE = ("elapsed_ms",)                       # 兩條路徑必然不同的欄；「除此之外相同」的比對用
STATE_FILE = "scores.db.state.json"


class ExportScoresError(RuntimeError):
    pass


def resolve_data_version(store: ScoreStore, cache: Path, wanted: str | None) -> str:
    """`--data-version` → 唯一的 `replay_meta.data_version` → `scores.db.state.json` 的 `meta.data_version`；都不成立就中止。"""
    have = [r[0] for r in store.conn.execute("SELECT data_version FROM replay_meta ORDER BY data_version")]
    if wanted is not None:
        if wanted not in have:
            raise ExportScoresError(f"scores.db 沒有 data_version={wanted}（有：{have}）")
        return wanted
    if len(have) == 1:
        return have[0]
    sp = cache / STATE_FILE
    if sp.exists():
        try:
            dv = json.loads(sp.read_text(encoding="utf-8")).get("meta", {}).get("data_version")
        except (OSError, ValueError, AttributeError):
            dv = None
        if dv in have:
            return str(dv)
    raise ExportScoresError(f"scores.db 的 replay_meta 有 {len(have)} 個 data_version（{have}），請以 --data-version 指定")


def day_rows(store: ScoreStore, dv: str, T: str) -> list[dict[str, Any]]:
    """`scores` JOIN `versions` → 分數檔 rows（欄值零轉換；`model_version` 由 versions 還原；排序＝`scores_payload`）。"""
    cols = ", ".join(f"s.{c}" for c in SCALAR_COLS)
    sql = (f"SELECT v.model_version, s.market, s.horizon, s.stock_id, s.date, {cols} "
           f"FROM scores s JOIN versions v ON v.version_id = s.version_id WHERE v.data_version=? AND s.date=?")
    names = ("model_version", "market", "horizon", "stock_id", "date", *SCALAR_COLS)
    rows = [dict(zip(names, r)) for r in store.conn.execute(sql, (dv, T))]
    rows.sort(key=lambda r: (str(r.get("market")), str(r.get("stock_id")), str(r.get("horizon")), str(r["model_version"])))
    return rows


def day_text_version(store: ScoreStore, dv: str, T: str) -> str:
    params = store.params_of(dv) or {}
    tvs = sorted({r[0] for r in store.conn.execute(
        "SELECT DISTINCT v.text_version FROM scores s JOIN versions v ON v.version_id = s.version_id WHERE v.data_version=? AND s.date=?",
        (dv, T))})
    tv = params.get("text_version")
    if tv is None:
        if len(tvs) != 1:
            raise ExportScoresError(f"{T}: replay_meta 無 text_version，且該日 versions.text_version 不唯一：{tvs}")
        return str(tvs[0])
    if tvs and tvs != [str(tv)]:
        raise ExportScoresError(f"{T}: replay_meta.text_version={tv!r} 與該日 versions.text_version={tvs} 不一致")
    return str(tv)


def day_diag(store: ScoreStore, dv: str, T: str, n_rows: int) -> dict[str, Any]:
    d = store.day_diag(dv, T)
    if d is None:
        raise ExportScoresError(f"{T}: replay_day 沒有這一天")
    out: dict[str, Any] = {"text_version": day_text_version(store, dv, T)}
    for c in DIAG_DB_COLS:
        out[c] = d.get(c)
    im = d.get("index_missing")
    out["index_missing"] = [] if not im else str(im).split(",")
    n_expect = (out["n_market_rows"] or 0) + (out["n_stock_rows"] or 0)
    if n_expect != n_rows:
        raise ExportScoresError(f"{T}: replay_day 記 {n_expect} 列（market {out['n_market_rows']}＋stock {out['n_stock_rows']}），"
                                f"scores 表實有 {n_rows} 列；db 不自洽，不匯")
    return out


def export_payload(store: ScoreStore, dv: str, T: str) -> dict[str, Any]:
    sha = store.params_sha_of(dv)
    if sha is None:
        raise ExportScoresError(f"replay_meta 沒有 data_version={dv} 的 params_sha")
    rows = day_rows(store, dv, T)
    diag = day_diag(store, dv, T, len(rows))
    return {"schema": DC.FILE_SCHEMA, "tpe_date": T, "data_version": dv, "text_version": diag["text_version"],
            "params_sha": sha, "rows": rows, "diag": diag}


def strip_volatile(payload: dict[str, Any]) -> dict[str, Any]:
    """比對用：去掉 `diag` 內兩條路徑必然不同／單側才有的欄（`elapsed_ms`、`rank_pool_size`）。"""
    p = json.loads(json.dumps(payload))
    diag = p.get("diag") if isinstance(p.get("diag"), dict) else {}
    for k in (*DIAG_VOLATILE, *DIAG_NOT_IN_DB):
        diag.pop(k, None)
    return p


def classify_existing(path: Path, new_bytes: bytes) -> str:
    """`same`＝位元組相同；`same-modulo-diag`＝只差 `elapsed_ms`／`rank_pool_size`；`differs`＝分數列或其他欄不同；`new`＝檔不存在。"""
    if not path.exists():
        return "new"
    old = path.read_bytes()
    if old == new_bytes:
        return "same"
    try:
        a = strip_volatile(json.loads(old.decode("utf-8")))
    except (ValueError, UnicodeDecodeError):
        return "differs"
    b = strip_volatile(json.loads(new_bytes.decode("utf-8")))
    return "same-modulo-diag" if DC.dumps(a) == DC.dumps(b) else "differs"


def _counts(rows: list[dict[str, Any]]) -> str:
    mk: dict[str, int] = {}
    hz: dict[str, int] = {}
    for r in rows:
        mk[str(r.get("market"))] = mk.get(str(r.get("market")), 0) + 1
        hz[str(r.get("horizon"))] = hz.get(str(r.get("horizon")), 0) + 1
    f = lambda d: "/".join(f"{k}={v}" for k, v in sorted(d.items()))  # noqa: E731
    return f"market[{f(mk)}] horizon[{f(hz)}]"


def run(args: argparse.Namespace) -> int:
    cache, out = Path(args.cache_dir), Path(args.out)
    t0 = time.perf_counter()
    try:
        store = ScoreStore(cache / "scores.db", readonly=True)
    except ScoreStoreError as e:
        print(f"[export_scores 中止] {e}", file=sys.stderr)
        return 2
    try:
        dv = resolve_data_version(store, cache, args.data_version)
        dates = store.dates(dv)
        frm, to = args.frm or (dates[0] if dates else ""), args.to or (dates[-1] if dates else "")
        if frm > to:
            raise ExportScoresError(f"--from {frm} 晚於 --to {to}")
        days = [d for d in dates if frm <= d <= to]
        if not days:
            raise ExportScoresError(f"data_version={dv} 在 {frm}..{to} 沒有 replay_day 已落地的日子（db 共 {len(dates)} 日，"
                                    f"{dates[0] if dates else '—'}..{dates[-1] if dates else '—'}）")
        sha = store.params_sha_of(dv)
        print(f"data_version={dv} params_sha={sha} 區間 {frm}..{to} 共 {len(days)} 日 → {out / DC.SCORES_DIR}"
              f"（diag 省略 {list(DIAG_NOT_IN_DB)}；elapsed_ms＝重播班耗時）", flush=True)
        tally: dict[str, int] = {}
        blocked: list[str] = []
        for T in days:
            payload = export_payload(store, dv, T)
            new_bytes = DC.dumps(payload).encode("utf-8")
            path = DC.scores_path(out, T)
            kind = classify_existing(path, new_bytes)
            if kind == "same":
                action = "已相同，略過"
            elif kind == "new" or args.force:
                DC.write_json(path, payload)
                action = "寫出" if kind == "new" else f"覆蓋（原檔 {kind}）"
            else:
                action = f"未覆蓋（原檔 {kind}；要覆蓋請 --force）"
                if kind == "differs":
                    blocked.append(T)
            tally[action.split("（")[0]] = tally.get(action.split("（")[0], 0) + 1
            print(f"  {T}: rows={len(payload['rows'])} {_counts(payload['rows'])} n_in_pool={payload['diag']['n_in_pool']} → {action}",
                  flush=True)
        print(f"完成：{', '.join(f'{k} {v}' for k, v in sorted(tally.items()))}；params_sha={sha}；"
              f"耗時 {time.perf_counter() - t0:.1f}s")
        if blocked:
            print(f"[export_scores] {len(blocked)} 日與現有檔內容不同且未 --force，未覆蓋：{blocked}", file=sys.stderr)
            return 1
        return 0
    except (ExportScoresError, ScoreStoreError, DC.DailyCoreError, OSError) as e:
        print(f"[export_scores 中止] {e}", file=sys.stderr)
        return 2
    finally:
        store.close()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="從 scores.db 匯出逐日分數檔 data/scores/<T>.json（與每日班 run_offline 同形）")
    ap.add_argument("--cache-dir", default=str(REPO / "cache"))
    ap.add_argument("--out", default=str(REPO), help="repo 根（寫 data/scores/<T>.json）")
    ap.add_argument("--data-version", default=None, help="預設：replay_meta 唯一者，否則取 scores.db.state.json 的 meta")
    ap.add_argument("--from", dest="frm", default=None, help="起日（含），預設 db 最早的 replay_day")
    ap.add_argument("--to", default=None, help="迄日（含），預設 db 最晚的 replay_day")
    ap.add_argument("--force", action="store_true", help="檔已存在且內容不同時覆蓋（預設不覆蓋、rc 1）")
    return run(ap.parse_args(argv))


if __name__ == "__main__":
    raise SystemExit(main())
