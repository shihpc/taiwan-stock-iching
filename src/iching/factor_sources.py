"""還原係數的**事件源對映與跨源合併**（裁定 #51 甲，2026-09-18）——純函式，不 import sqlite3。

四個 FinMind 資料集都是「before／after 價對」，係數算法同一支 `adjust.event_factor`；本模組負責兩件事：
1. **欄位對映**（`SOURCES`）：每源的 raw 表名與 before／after 欄名（Hetzner 2026-09-18 探測 P4，`docs/P3-DATASET.md` §7.2）。
2. **合併規則**（`merge_factor_rows`）：五個讀取端**同一支**——`feed.load_factor_rows`（Hetzner 回補 DB）、`daily_pipeline.update_factors`
   （每日班當日列）、`export_seed.export_factor_rows`（種子匯出）、`check_dataset._load_factors`（獨立抽驗器）、以及測試的手算。
   規則寫成一支純函式而不是「五處逐字對齊」，是因為對齊靠人眼、每次改都會漂（`feed.load_factors` 與 `daily_core.factors_from_rows`
   原本就是兩份逐字副本）。

## 合併規則（最終定義）

輸入 `{source: [(stock_id, date, before, after), …]}`，`source ∈ {dividend, capred, split, parvalue}`（缺的源＝0 列）。

1. **每源內**：`date` 為 None 跳過；同 `(stock_id, date)` **keep-first**（`dup_skipped`，同一事件可能同時落在兩種 cov_key，
   `store.py` 的已知代價）；before／after 非數或 ≤0 跳過（`bad_skipped`）。壞的首列**不佔鍵**，其後同鍵的好列仍可進
   ——與 2026-09-12 起的 `feed.load_factors` 逐字同一規則。
2. **split ∪ parvalue 以 `(stock_id, date)` 去重、優先 split**（`cross_source_dup`）：探測 P6 顯示分割與面額變更是**同一事件兩表各一列**
   （2022 全年 5 筆全重疊、值相同），兩表都算會把 4 倍變 16 倍。
3. **dividend／capred／(split∪parvalue) 是不同事件**，同 `(stock_id, date)` **各自保留** → `adjust.cumulative_factors` 同日相乘。
4. 輸出依 `(stock_id, date, 來源序)` 排序（來源序＝`SOURCES` 順序），決定性；每列 5 欄 `(stock_id, date, before, after, source)`。
5. 統計含每源筆數、跨源去重數、按源 band（`adjust.FACTOR_BAND`）的異常清單——**只報不擋**（同 2026-09-12 人工複核 10,664 筆的做法）。

## `factors.json` 的語意變化（每列仍 4 欄、`FILE_SCHEMA` 不 bump）

檔內 `rows` 是**合併後**的事件列：同 `(stock_id, date)` 不同源會是多列，讀回時**不得再做 keep-first**——去重已在合併層做完，
讀取端 `build_factors` 只把多列當多個事件相乘。舊檔（頂層無 `sources`、或值 ≠ `adjust.ADJUST_SOURCES`）一律拒讀、要求重匯種子。
"""
from __future__ import annotations

from typing import Any, Iterable, Mapping, NamedTuple, Sequence

from .adjust import ADJUST_SOURCES, FACTOR_BAND, FACTOR_BAND_ANY, Event, anomalies, cumulative_factors, event_factor

__all__ = ["ADJUST_SOURCES", "SOURCES", "SOURCE_BY_NAME", "SOURCE_BY_KEY", "SOURCE_ORDER", "ALIAS_PAIR",
           "normalize_rows", "merge_factor_rows", "build_factors", "format_source_stat"]


class SourceSpec(NamedTuple):
    source: str      # 短名（＝`adjust.FACTOR_BAND` 的鍵、每日班 extras 的鍵）
    key: str         # `config.DATASETS` 的 key
    table: str       # raw 表名（＝`DatasetSpec.table`）
    before: str      # before 價欄名
    after: str       # after 價欄名


# 順序＝合併輸出的來源序；欄名＝Hetzner 2026-09-18 探測 P4（與 FinMind 官方文件相同）
SOURCES: tuple[SourceSpec, ...] = (
    SourceSpec("dividend", "dividend_result", "raw_dividend_result", "before_price", "after_price"),
    SourceSpec("capred", "cap_reduction", "raw_cap_reduction", "ClosingPriceonTheLastTradingDay", "PostReductionReferencePrice"),
    SourceSpec("split", "split_price", "raw_split_price", "before_price", "after_price"),
    SourceSpec("parvalue", "par_value_change", "raw_par_value_change", "before_close", "after_ref_close"),
)
SOURCE_BY_NAME: dict[str, SourceSpec] = {s.source: s for s in SOURCES}
SOURCE_BY_KEY: dict[str, SourceSpec] = {s.key: s for s in SOURCES}
SOURCE_ORDER: dict[str, int] = {s.source: i for i, s in enumerate(SOURCES)}
ALIAS_PAIR: tuple[str, str] = ("split", "parvalue")     # 同一事件兩表各一列 → 以 (stock_id, date) 去重，優先前者
assert set(SOURCE_BY_NAME) == set(FACTOR_BAND), "factor_sources.SOURCES 與 adjust.FACTOR_BAND 的來源集合不一致"

Row4 = tuple[str, str, Any, Any]
Row5 = tuple[str, str, float, float, str]
Factors = dict[str, tuple[list[str], list[float]]]


def _num(v: Any) -> float | None:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if f == f else None          # NaN 也視為壞值


def normalize_rows(source: str, rows: Iterable[Mapping[str, Any]]) -> list[Row4]:
    """FinMind 原欄名 dict 列 → `(stock_id, date, before, after)`；缺 `stock_id`／`date` 的列丟掉（每日班 `daily_fetch` 用）。
    同 `(stock_id, date)` **後者覆蓋**（與每日班除權息逐日切片的既有做法同），輸出依鍵排序。"""
    spec = SOURCE_BY_NAME[source]
    by: dict[tuple[str, str], Row4] = {}
    for r in rows:
        sid, d = r.get("stock_id"), r.get("date")
        if not sid or not d:
            continue
        by[(str(sid), str(d))] = (str(sid), str(d), r.get(spec.before), r.get(spec.after))
    return [by[k] for k in sorted(by)]


def merge_factor_rows(rows_by_source: Mapping[str, Iterable[Sequence[Any]]]) -> tuple[list[Row5], dict[str, Any]]:
    """跨源合併（規則見模組 docstring）。回 (合併後 5 欄列, 統計)。未知的來源名直接 raise。

    統計：`rows`（總輸入列）、`kept`（輸出列）、`by_source{source: {rows, kept, dup_skipped, bad_skipped, anomalies}}`、
    `cross_source_dup`（parvalue 被 split 蓋掉的列數）、`anomalies`（按源 band 外的總筆數）、
    `anomaly_rows`（`[(stock_id, date, before, after, source, factor), …]`，供 log／人工複核）、`sources`（＝`ADJUST_SOURCES`）。
    """
    unknown = sorted(set(rows_by_source) - set(SOURCE_BY_NAME))
    if unknown:
        raise ValueError(f"未知的事件源 {unknown}；可用 {[s.source for s in SOURCES]}")
    kept: dict[str, dict[tuple[str, str], Row5]] = {}
    by_source: dict[str, dict[str, int]] = {}
    n_rows = 0
    for spec in SOURCES:
        src = spec.source
        st = {"rows": 0, "kept": 0, "dup_skipped": 0, "bad_skipped": 0, "anomalies": 0}
        out: dict[tuple[str, str], Row5] = {}
        for r in rows_by_source.get(src, ()):
            sid, d, b, a = r[0], r[1], r[2], r[3]
            if d is None:
                continue
            st["rows"] += 1
            n_rows += 1
            key = (str(sid), str(d))
            if key in out:
                st["dup_skipped"] += 1
                continue
            bf, af = _num(b), _num(a)
            if bf is None or af is None or bf <= 0 or af <= 0:
                st["bad_skipped"] += 1
                continue
            out[key] = (key[0], key[1], bf, af, src)
        st["kept"] = len(out)
        kept[src] = out
        by_source[src] = st
    # ② split ∪ parvalue：同 (stock_id, date) 只留 split
    primary, alias = ALIAS_PAIR
    cross = 0
    for key in list(kept[alias]):
        if key in kept[primary]:
            del kept[alias][key]
            cross += 1
    by_source[alias]["kept"] = len(kept[alias])
    # ③ 其餘各自保留；④ 決定性排序
    merged: list[Row5] = []
    for src in kept:
        merged.extend(kept[src].values())
    merged.sort(key=lambda r: (r[0], r[1], SOURCE_ORDER[r[4]]))
    # ⑤ 按源 band 的異常（只報不擋）
    anomaly_rows: list[tuple[str, str, float, float, str, float]] = []
    for src, out in kept.items():
        evs = [Event(d, b, a) for (_sid, d, b, a, _s) in out.values()]
        flagged = {(e.ex_date, e.before_price, e.after_price): f for e, f in anomalies(evs, src)}
        by_source[src]["anomalies"] = len(flagged)
        for sid, d, b, a, _s in out.values():
            f = flagged.get((d, b, a))
            if f is not None:
                anomaly_rows.append((sid, d, b, a, src, f))
    anomaly_rows.sort(key=lambda r: (r[0], r[1], SOURCE_ORDER[r[4]]))
    stat = {"sources": ADJUST_SOURCES, "rows": n_rows, "kept": len(merged), "by_source": by_source,
            "cross_source_dup": cross, "anomalies": len(anomaly_rows), "anomaly_rows": anomaly_rows}
    return merged, stat


def build_factors(rows: Iterable[Sequence[Any]]) -> tuple[Factors, dict[str, Any]]:
    """合併後的事件列（4 或 5 欄，只看前 4 欄）→ `{stock_id: (ex_dates, cum)}`（`adjust.cumulative_factors`）。

    **不去重**：同 `(stock_id, date)` 多列＝多個事件（dividend×capred 同日）、相乘——去重已在 `merge_factor_rows` 做完，
    這裡再 keep-first 會把第二個事件吃掉（那正是 2026-09-18 前 `daily_core.factors_from_rows` 的規則，本批**刻意移除**）。
    非數或 ≤0 仍跳過（`bad_skipped`；檔案可能被手改）。`anomalies` 以四源**聯集** band（`adjust.FACTOR_BAND_ANY`）計——
    4 欄列不帶來源，按源 band 的異常清單在合併層（`merge_factor_rows` 的統計）。

    這支是 `feed.load_factors`（DB）與 `daily_core.load_factors_file`（`factors.json`）**共用**的最後一步，兩層 parity 由此保證。
    """
    by: dict[str, list[Event]] = {}
    n_rows = n_bad = n_anom = 0
    lo, hi = FACTOR_BAND_ANY
    for r in rows:
        sid, d, b, a = r[0], r[1], r[2], r[3]
        if d is None:
            continue
        n_rows += 1
        bf, af = _num(b), _num(a)
        if bf is None or af is None or bf <= 0 or af <= 0:
            n_bad += 1
            continue
        f = event_factor(bf, af)
        if not (lo <= f <= hi):
            n_anom += 1
        by.setdefault(str(sid), []).append(Event(str(d), bf, af))
    out = {sid: cumulative_factors(evs) for sid, evs in by.items()}
    return out, {"rows": n_rows, "bad_skipped": n_bad, "stocks": len(out), "anomalies": n_anom}


def format_source_stat(stat: Mapping[str, Any], *, max_rows: int = 20) -> str:
    """合併統計的一行摘要（scan_features／export_seed／replay_scores／daily_run 的 log 用）。"""
    bs = stat.get("by_source", {})
    parts = [f"{s.source} {bs.get(s.source, {}).get('kept', 0):,}" for s in SOURCES]
    line = (f"事件源 {stat.get('sources', ADJUST_SOURCES)}：" + "／".join(parts)
            + f"（split∪parvalue 去重 {stat.get('cross_source_dup', 0)}；合併後 {stat.get('kept', 0):,} 列）")
    missing = stat.get("missing_tables") or []
    if missing:
        line += f"；缺表視為 0 列：{missing}"
    meta_only = stat.get("meta_only_tables") or []
    if meta_only:
        line += f"；meta-only 空表視為 0 列：{meta_only}"
    rows = stat.get("anomaly_rows") or []
    if rows:
        ex = "、".join(f"{sid} {d} {src} {f:.4f}({b}/{a})" for sid, d, b, a, src, f in rows[:max_rows])
        line += f"；band 外 {len(rows)} 筆（只報不擋）：{ex}" + ("、…" if len(rows) > max_rows else "")
    else:
        line += "；band 外 0 筆"
    return line
