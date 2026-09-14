#!/usr/bin/env python3
"""D-3 parity 儀式（`docs/P2-DAILY-PLAN.md` §7.6）：**每日班（GitHub Actions，原料包路徑）vs Hetzner 回補＋重播路徑**，
同一 T 的原料包與分數逐位比對。**唯讀**：不寫 cache、不寫 repo（分數 JSON 只灌進暫存目錄裡的臨時 `ScoreStore`）。

    python3 scripts/parity_check.py --cache-dir cache --repo /path/to/taiwan-stock-iching
    python3 scripts/parity_check.py --cache-dir cache --repo . --from 2026-09-01 --to 2026-09-14 --show 20

在 Hetzner 上跑（`scores.db` 不搬）。`--repo` 讀 `data/scores/<T>.json`＋`runs/collect/<T>-daily.json.gz`＋`data/state/cross.json`
（`data_version`／`window` 預設取自它的 `meta`）＋`data/calendar_tpe.json`（歸類①的交易日軸，缺則用原料包日期）。

逐日三段：
- **原料包**：10 個頂層鍵（`schema`／`band`／`tpe_date`／`index`／`stocks`／`official`／`futures`／`total_margin`／`vix`／
  `foreign_net_oi`）以 `bundle_io.bundle_to_dict` 的子物件、同 `bundle_io.dumps` 的序列化參數逐位比；`stocks` 逐檔報第一個不同的欄。
  `us`／`fx` 兩路切分點不同（§7.6.0），一律比**區間內兩側全部包的聯集**（以列日期去重，只比兩側日期範圍的交集；交集內只在一側
  有的日期或列不同都算差異）。
- **分數**：JSON rows 灌臨時 `ScoreStore` → `diff_scores.diff_day`；`diag` 比 9 欄（排除 JSON 多的 `rank_pool_size`／`text_version`）。
  比對前先核 JSON 頂層 `params_sha`＝參考 `replay_meta.params_sha`、`data_version` 存在於參考，不符 rc 2。
- **差異歸類**（分數層，每個有差異的 `stock_id`，依序判①→②→③→④）：
  ①入池未滿 `window` 交易日（repo 原料包首次出現日到 T 的交易日數 < window，§7.4.0 第 3 點）；
  ②近 `window` 份原料包內有效收盤（`universe.is_traded_row`）少於 `SCAN_MAXLEN`（＝`scan.DailyScanner` deque 的 maxlen 61，
  §7.0 第 1 點）——**window < 61 時門檻取 window**（只有合成測試會這樣；不取則②對每檔恆真、把④全吃掉）；
  ③該檔的 `stocks[sid]` 在區間內任一 ≤T 的日子有差異（上游事後修訂）；④無法解釋。
  市場層鍵（`stocks` 以外的 9 個鍵）任一不同 → **自該日起**區間內每一日的分數都標「市場層原料不同，分數差異不歸類」
  （市場 ring 跨日，該日之後的市場列與個股列全會連帶，逐日各自歸類只會得到一片④）；`us`／`fx` 聯集有差異時，
  其最早差異日 x 之後的台北日（T > x，引擎取「截至 T 前一曆日已收盤的美股日」）同樣標記。
  大盤列（`stock_id='__MARKET__'`）的差異不屬個股邊界，未被市場層標記時一律④——**除了「①連帶」**（§7.7 第 4 點，2026-09-15）：
  比對區間內有檔的首次出現日 E 晚於區間起日（每日班的新入池檔），則 **E 之前各日的全部列**（大盤列與個股列，含該檔只在參考的列）
  差異另列「①連帶」（不算④、rc 0、印計數）——參考池是最新快照、對 E 前各日也算進該檔，那些日子每日班當時不可能知道它，是參考
  路徑非 PIT 的已知性質，不是 bug；個股列經大盤方向分數→個股上爻（`line_6`）連帶（合成世界實證），所以整日一起歸。
  另外 **E 起 `MARKET_LINE2_HIST`（5）個交易日內、且只差 `flags` 欄**的大盤列也歸「①連帶」：大盤旗標讀 `line2_score_t_minus_5`
  （狀態鏈裡 T−5 的二爻分數），E 前那幾日的污染值要 5 個交易日才滾出歷史（合成世界實測：E／E+1／E+4 只差 `flags`，E+5 起逐位相同）；
  差到 `flags` 以外的欄仍是④，這一段只有市場列適用。
  **已知限制**：①②的檔在每日班的 `DailyScanner` deque 較短，會經廣度比（`above_ma_ratio` 等）污染同日市場列、再經
  `market_direction_score` 污染全部個股列——若同一日同時出現①②與④，④可能是連帶而非獨立 bug，輸出會附提示但仍計④。

rc：0 全同或只有①②③；1 有④；2 版本／參數不符、開檔失敗、無日期可比、參考端缺日；3 市場層原料不同（含 `us`／`fx` 聯集差異）。
優先序 2 > 1 > 3 > 0（④只會落在市場層標記日之前，是獨立證據）。
"""
from __future__ import annotations

import argparse
import bisect
import json
import sqlite3
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "src"))
sys.path.insert(0, str(REPO / "scripts"))

import diff_scores as DS  # noqa: E402
from iching import bundle_io as B  # noqa: E402
from iching import daily_core as DC  # noqa: E402
from iching import feed as F  # noqa: E402
from iching import replay_io as RIO  # noqa: E402
from iching import scan as SCAN  # noqa: E402
from iching import universe as U  # noqa: E402
from iching.features_io import FeatureStoreError  # noqa: E402
from iching.replay_state import MARKET_LINE2_HIST, DayBundle  # noqa: E402
from iching.run_common import ReplayDriverError, load_state  # noqa: E402
from iching.score import MARKET_STOCK_ID  # noqa: E402
from iching.scores_io import ScoreStore, ScoreStoreError  # noqa: E402

BUNDLE_KEYS = ("schema", "band", "tpe_date", "index", "stocks", "official", "futures", "total_margin", "vix", "foreign_net_oi")
MARKET_KEYS = tuple(k for k in BUNDLE_KEYS if k != "stocks")     # 市場層：stocks 以外全部（schema／band／tpe_date 不同＝根本不可比）
DATED_KEYS = ("us", "fx")
DIAG_COLS = ("model_version_twse", "model_version_tpex", "n_market_rows", "n_stocks", "n_in_pool", "n_stock_rows",
             "n_stock_any_unknown", "n_market_any_unknown", "index_missing")      # 9 欄；排除 rank_pool_size／text_version
SCAN_MAXLEN = max((*SCAN.MA_WINDOWS, *SCAN.HL_WINDOWS, *(n + 1 for n in SCAN.RET_WINDOWS)))   # ＝scan.py DailyScanner._maxlen 的同一算式（61）
CLASS_NEW, CLASS_SHORT, CLASS_BUNDLE, CLASS_UNEXPLAINED = 1, 2, 3, 4
CLASSES = (CLASS_NEW, CLASS_SHORT, CLASS_BUNDLE, CLASS_UNEXPLAINED)
CLASS_MARK = {CLASS_NEW: "①", CLASS_SHORT: "②", CLASS_BUNDLE: "③", CLASS_UNEXPLAINED: "④"}
CLASS_LABEL = {CLASS_NEW: "①入池未滿 window 日", CLASS_SHORT: "②近 window 日有效收盤不足", CLASS_BUNDLE: "③該檔原料包有差異",
               CLASS_UNEXPLAINED: "④無法解釋"}
SPILL_MARK = "①連帶"                                             # 新入池檔 E 之前整日（或 E 起 5 日內只差 flags 的大盤列）的連帶差異，不計④
RC_OK, RC_UNEXPLAINED, RC_SETUP, RC_MARKET = 0, 1, 2, 3
OPEN_ERRORS = (ScoreStoreError, RIO.ReplayIOError, FeatureStoreError, F.FeedError, ReplayDriverError, DC.DailyCoreError,
               B.BundleError, sqlite3.Error, OSError, ValueError, KeyError, TypeError)


class ParityError(RuntimeError):
    """設定／版本／開檔層級的中止（rc 2）。"""


def _ser(v: Any) -> str:
    """與 `bundle_io.dumps` 同一組 json 參數（輸入已是 `bundle_to_dict` 清過的子物件）。"""
    return json.dumps(v, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _first_diff_field(a: Any, b: Any) -> str:
    if isinstance(a, dict) and isinstance(b, dict):
        for k in sorted(set(a) | set(b)):
            if k not in a or k not in b or _ser(a[k]) != _ser(b[k]):
                return k
    return "?"


def _diff_leaf(a: Any, b: Any) -> tuple[str, str, str]:
    """沿 dict 往下找到第一個不同的葉：回 (路徑, 參考值, repo 值)；非 dict 或缺鍵就在該層停。"""
    path: list[str] = []
    while isinstance(a, dict) and isinstance(b, dict):
        k = _first_diff_field(a, b)
        if k == "?":
            break
        path.append(k)
        if k not in a or k not in b:
            return ".".join(path), "（缺）" if k not in a else _ser(a[k]), "（缺）" if k not in b else _ser(b[k])
        a, b = a[k], b[k]
    return ".".join(path), _ser(a), _ser(b)


# ---------------------------------------------------------------------------
@dataclass
class DayResult:
    date: str
    ref_missing: list[str] = field(default_factory=list)          # 參考端缺什麼（"scores"／"bundle"）
    repo_bundle_missing: bool = False                            # repo 有分數檔但原料包已被修剪／不存在
    key_diffs: dict[str, str] = field(default_factory=dict)      # 市場層鍵 → 說明
    stock_diffs: dict[str, str] = field(default_factory=dict)    # stock_id → 說明（`stocks` 逐檔）
    n_common: int = 0
    n_diff: int = 0
    msgs: list[str] = field(default_factory=list)                # diff_scores.diff_day 的訊息
    diff_sids: dict[str, int] = field(default_factory=dict)      # 有差異列的 stock_id → 列數（大盤列鍵＝MARKET_STOCK_ID）
    diag_diffs: dict[str, tuple[Any, Any]] = field(default_factory=dict)   # 欄 → (參考, repo)
    diff_cols: dict[str, set[str]] = field(default_factory=dict)  # stock_id → 有差異的欄（同鍵兩側皆有的列；只在一側的列不計）
    market_layer_since: str | None = None                        # 非 None＝自該日起市場層原料不同，本日分數不歸類
    market_layer_note: str = ""
    classes: dict[str, int] = field(default_factory=dict)        # stock_id → 1..4
    reasons: dict[str, str] = field(default_factory=dict)
    spill: dict[str, str] = field(default_factory=dict)          # stock_id → ①連帶的理由（E 前整日；E 起 5 日內只有大盤列）
    hint: str = ""

    @property
    def market_layer(self) -> bool:
        return self.market_layer_since is not None

    def class_counts(self) -> dict[int, int]:
        out = {c: 0 for c in CLASSES}
        for c in self.classes.values():
            out[c] += 1
        return out


@dataclass
class ParityResult:
    data_version: str = ""
    window: int = 0
    params_sha: str = ""
    dates: list[str] = field(default_factory=list)               # 實際比對的日期（升冪）
    days: dict[str, DayResult] = field(default_factory=dict)
    dated_range: dict[str, tuple[str, str] | None] = field(default_factory=dict)   # us／fx 的交集範圍
    dated_diffs: dict[str, list[str]] = field(default_factory=dict)              # us／fx 的差異訊息
    dated_diff_dates: dict[str, list[str]] = field(default_factory=dict)         # us／fx 有差異的列日期（""＝無法定位）
    errors: list[str] = field(default_factory=list)              # rc 2 類的錯誤
    rc: int = RC_SETUP

    def counts(self) -> dict[int, int]:
        out = {c: 0 for c in CLASSES}
        for d in self.days.values():
            for c, n in d.class_counts().items():
                out[c] += n
        return out

    def stocks_of(self, cls: int) -> list[tuple[str, str]]:
        return [(d.date, sid) for d in self.days.values() for sid, c in sorted(d.classes.items()) if c == cls]

    @property
    def spill_days(self) -> list[str]:
        """大盤列差異歸「①連帶」的日子（升冪）。"""
        return [d.date for d in self.days.values() if d.spill]

    @property
    def market_layer_days(self) -> list[str]:
        return [d.date for d in self.days.values() if d.market_layer]

    @property
    def unclassified_rows(self) -> int:
        return sum(d.n_diff for d in self.days.values() if d.market_layer)


# ---------------------------------------------------------------------------
# repo 側掃描：全部原料包讀一次 → (日期升冪, 首次出現日, 每檔有效收盤日)
def scan_repo_bundles(repo: Path) -> tuple[list[str], dict[str, str], dict[str, list[str]]]:
    dates: list[str] = []
    first_seen: dict[str, str] = {}
    valid: dict[str, list[str]] = {}
    for d, p in B.list_bundles(repo):
        b = B.read_bundle(p)
        if b.tpe_date != d:
            raise ParityError(f"原料包 {p} 內容日期 {b.tpe_date} ≠ 檔名日期 {d}")
        dates.append(d)
        for sid, r in b.stocks.items():
            first_seen.setdefault(sid, d)
            if U.is_traded_row({U.PRICE_CLOSE: r.get("close"), U.PRICE_VOLUME: r.get("Trading_Volume")}):
                valid.setdefault(sid, []).append(d)
    return dates, first_seen, valid


def scores_dates(repo: Path, start: str | None, end: str | None) -> list[str]:
    d = Path(repo) / DC.SCORES_DIR
    if not d.exists():
        return []
    out = []
    for p in sorted(d.glob("*.json")):
        t = p.stem
        if len(t) == 10 and (start is None or t >= start) and (end is None or t <= end):
            out.append(t)
    return out


def _load_meta(repo: Path) -> dict:
    return dict(load_state(Path(repo) / DC.STATE_FILE).meta or {})


# ---------------------------------------------------------------------------
# 原料包比對
def compare_bundles(ref: DayBundle, got: DayBundle, day: DayResult) -> None:
    a, b = B.bundle_to_dict(ref), B.bundle_to_dict(got)
    for k in MARKET_KEYS:
        if _ser(a.get(k)) != _ser(b.get(k)):
            path, x, y = _diff_leaf(a.get(k), b.get(k))
            day.key_diffs[k] = f"{k}{'.' + path if path else ''}: 參考={x[:120]} repo={y[:120]}"
    sa_, sb_ = a.get("stocks") or {}, b.get("stocks") or {}
    for sid in sorted(set(sa_) | set(sb_)):
        if sid not in sa_:
            day.stock_diffs[sid] = f"stocks {sid}: 只在 repo"
        elif sid not in sb_:
            day.stock_diffs[sid] = f"stocks {sid}: 只在參考"
        elif _ser(sa_[sid]) != _ser(sb_[sid]):
            path, x, y = _diff_leaf(sa_[sid], sb_[sid])
            day.stock_diffs[sid] = f"stocks {sid} 欄 {path}: 參考={x} repo={y}"


def dated_union(rows_by_bundle: list[list], key: str) -> dict[str, str]:
    """區間內全部包的列 → {日期: 序列化列}（同日期後者覆蓋；正常情況同一日期只會出現一次）。
    列先過 `bundle_to_dict`（NaN→null、tuple→list）再序列化，與逐日鍵的比法同一套。"""
    out: dict[str, str] = {}
    for rows in rows_by_bundle:
        for row in B.bundle_to_dict(DayBundle(tpe_date="x", **{key: list(rows)}))[key]:
            out[str(row[0])] = _ser(row)
    return out


def compare_dated(key: str, ref: dict[str, str], repo: dict[str, str]) -> tuple[tuple[str, str] | None, list[tuple[str, str]]]:
    """回 (交集範圍, [(差異日期, 訊息)])；一側全空／範圍無交集時日期欄為 ""（無法定位到哪一天起）。"""
    if not ref and not repo:
        return None, []
    if not ref or not repo:
        return None, [("", f"{key}: 一側沒有任何列（參考 {len(ref)} 日／repo {len(repo)} 日）")]
    lo, hi = max(min(ref), min(repo)), min(max(ref), max(repo))
    if lo > hi:
        return (lo, hi), [("", f"{key}: 兩側日期範圍無交集（參考 {min(ref)}～{max(ref)}／repo {min(repo)}～{max(repo)}）")]
    out: list[tuple[str, str]] = []
    for d in sorted(set(ref) | set(repo)):
        if d < lo or d > hi:
            continue
        x, y = ref.get(d), repo.get(d)
        if x is None:
            out.append((d, f"{key} {d}: 只在 repo"))
        elif y is None:
            out.append((d, f"{key} {d}: 只在參考"))
        elif x != y:
            out.append((d, f"{key} {d}: 參考={x} repo={y}"))
    return (lo, hi), out


# ---------------------------------------------------------------------------
# 分數比對
def load_scores_json(repo: Path, T: str, *, dv: str, params_sha: str) -> dict:
    p = DC.scores_path(repo, T)
    js = DC.read_json(p, what="scores")
    if not isinstance(js, dict) or js.get("schema") != DC.FILE_SCHEMA:
        raise ParityError(f"{p} 的 schema 不是 {DC.FILE_SCHEMA}")
    if js.get("tpe_date") != T:
        raise ParityError(f"{p} 的 tpe_date={js.get('tpe_date')!r} ≠ {T}")
    if js.get("data_version") != dv:
        raise ParityError(f"{p} 的 data_version={js.get('data_version')!r} ≠ 參考 {dv}")
    if js.get("params_sha") != params_sha:
        raise ParityError(f"{p} 的 params_sha={js.get('params_sha')!r} ≠ 參考 replay_meta {params_sha}")
    if not isinstance(js.get("rows"), list) or not isinstance(js.get("diag"), dict):
        raise ParityError(f"{p} 缺 rows／diag")
    return js


def _attribute(ref: ScoreStore, got: ScoreStore, dv: str, T: str) -> tuple[dict[str, int], int, dict[str, set[str]]]:
    """有差異列（只在一側／同鍵不同）→ {stock_id: 列數}、總數、{stock_id: 有差異的欄}；鍵與相等判準與 `diff_scores.diff_day` 同一組。"""
    ra = {DS._key(r): r for r in ref.rows_for_day(dv, T)}
    rb = {DS._key(r): r for r in got.rows_for_day(dv, T)}
    per: dict[str, int] = {}
    cols: dict[str, set[str]] = {}
    n = 0
    for k in sorted(set(ra) ^ set(rb)):
        per[k[2]] = per.get(k[2], 0) + 1
        n += 1
    for k in sorted(set(ra) & set(rb)):
        if ra[k] != rb[k]:
            per[k[2]] = per.get(k[2], 0) + 1
            cols.setdefault(k[2], set()).update(c for c in set(ra[k]) | set(rb[k]) if ra[k].get(c) != rb[k].get(c))
            n += 1
    return per, n, cols


def compare_scores(ref: ScoreStore, got: ScoreStore, repo: Path, dv: str, T: str, params_sha: str, day: DayResult) -> None:
    js = load_scores_json(repo, T, dv=dv, params_sha=params_sha)
    rows = [(r["model_version"], {k: v for k, v in r.items() if k != "model_version"}) for r in js["rows"]]
    got.write_day(dv, T, rows, js["diag"])
    day.n_common, day.n_diff, day.msgs = DS.diff_day(ref, got, dv, T)
    day.diff_sids, n, day.diff_cols = _attribute(ref, got, dv, T)
    if n != day.n_diff:
        raise ParityError(f"{T}: 差異歸戶列數 {n} ≠ diff_day 的 {day.n_diff}（比對器不一致，程式 bug）")
    dr, dg = ref.day_diag(dv, T), got.day_diag(dv, T)
    if dr is None:
        day.ref_missing.append("diag")
        return
    for c in DIAG_COLS:
        if dr.get(c) != (dg or {}).get(c):
            day.diag_diffs[c] = (dr.get(c), (dg or {}).get(c))


# ---------------------------------------------------------------------------
# 歸類
def classify_stock(sid: str, T: str, *, window: int, calendar: list[str], bundle_dates: list[str], first_seen: dict[str, str],
                   valid: dict[str, list[str]], stock_diff_days: dict[str, list[str]]) -> tuple[int, str]:
    if sid == MARKET_STOCK_ID:
        return CLASS_UNEXPLAINED, "大盤列不同（市場層原料同、仍不同）"
    first = first_seen.get(sid)
    if first is None:
        return CLASS_NEW, "repo 原料包從未出現此檔"
    n_days = bisect.bisect_right(calendar, T) - bisect.bisect_left(calendar, first)
    if n_days < window:
        return CLASS_NEW, f"首見 {first}，到 T 共 {n_days} 交易日 < {window}"
    hi = bisect.bisect_right(bundle_dates, T)                    # 最近 window 份 ≤T 的原料包
    lo = bundle_dates[max(0, hi - window)] if hi else T
    vd = valid.get(sid, [])
    n_valid = bisect.bisect_right(vd, T) - bisect.bisect_left(vd, lo)
    thr = min(SCAN_MAXLEN, window)
    if n_valid < thr:
        return CLASS_SHORT, f"近 {hi - max(0, hi - window)} 份原料包（{lo}～{T}）有效收盤 {n_valid} < {thr}"
    days = [d for d in stock_diff_days.get(sid, []) if d <= T]
    if days:
        return CLASS_BUNDLE, f"原料包 stocks[{sid}] 於 {days[0]}{'…' if len(days) > 1 else ''}（共 {len(days)} 日）不同"
    return CLASS_UNEXPLAINED, ""


def pre_entrant_spill(T: str, entrants: dict[str, str]) -> str | None:
    """E 前整日「①連帶」（§7.7 第 4 點）：`entrants`＝{sid: E}，T 早於任一 E → 本日全部列的差異都歸連帶。回理由或 None。"""
    before = [sid for sid, e in entrants.items() if T < e]
    if not before:
        return None
    sid = min(before, key=lambda s: (entrants[s], s))
    return f"新入池檔 {sid} 首見 {entrants[sid]}，本日在其入池前（參考池含其入池前歷史、每日班當時不可能知道；整日全部列一起歸）"


def market_spill(T: str, *, entrants: dict[str, str], calendar: list[str], diff_cols: set[str] | None) -> str | None:
    """大盤列「①連帶」判定的第二段：E ≤ T < E+`MARKET_LINE2_HIST`（交易日）且差異只在 `flags`——T−5 二爻歷史仍讀到 E 前的
    污染值。（E 之前整日的連帶由 `pre_entrant_spill` 處理，不分列別。）回理由字串或 None。"""
    if not entrants:
        return None
    if diff_cols is not None and diff_cols <= {"flags"}:
        it = bisect.bisect_right(calendar, T)
        for sid, e in sorted(entrants.items(), key=lambda x: (x[1], x[0])):
            ie = bisect.bisect_left(calendar, e)
            if 0 <= it - ie <= MARKET_LINE2_HIST and it - ie >= 1:            # T 在 [E, E+HIST)（T≥E 已由上段排除 T<E）
                return (f"新入池檔 {sid} 首見 {e}，本日距 E 不足 {MARKET_LINE2_HIST} 個交易日、只差 flags 欄"
                        f"（大盤旗標讀 T−{MARKET_LINE2_HIST} 二爻歷史，E 前污染值尚未滾出）")
    return None


def classify(res: ParityResult, *, calendar: list[str], bundle_dates: list[str], first_seen: dict[str, str],
             valid: dict[str, list[str]]) -> None:
    # us／fx 最早差異日 x → T > x 的台北日起標記；差異無法定位日期（一側全空／無交集）時從區間第一日起標
    xs = [d for k in DATED_KEYS for d in res.dated_diff_dates.get(k, [])]
    dated_since: str | None = min(xs) if xs else None
    dated_mark = next((T for T in res.dates if dated_since is not None and T > dated_since), None)
    market_since: str | None = None
    market_note = ""
    stock_diff_days: dict[str, list[str]] = {}
    # 區間內才首見的檔（§7.7 第 4 點）。取 ≥ 區間起日：E＝起日時沒有 T<E 的日子可歸連帶，但 flags 窗（E～E+4）仍要認得它
    entrants = {sid: e for sid, e in first_seen.items() if res.dates and e >= res.dates[0]}
    for T in res.dates:
        day = res.days[T]
        for sid in day.stock_diffs:
            stock_diff_days.setdefault(sid, []).append(T)
        if market_since is None:
            notes = []
            if day.key_diffs:
                notes.append("市場層鍵 " + "／".join(sorted(day.key_diffs)) + " 不同")
            if dated_mark is not None and T >= dated_mark:
                notes.append(f"us／fx 聯集自 {dated_since} 起不同")
            if notes:
                market_since, market_note = T, "；".join(notes)
        if market_since is not None:
            day.market_layer_since, day.market_layer_note = market_since, market_note
            continue
        pre = pre_entrant_spill(T, entrants)
        for sid in sorted(day.diff_sids):
            if pre is not None:                                       # E 前整日：大盤列與個股列全部歸連帶
                day.spill[sid] = pre
                continue
            if sid == MARKET_STOCK_ID:
                why = market_spill(T, entrants=entrants, calendar=calendar, diff_cols=day.diff_cols.get(sid))
                if why is not None:
                    day.spill[sid] = why
                    continue
            c, why = classify_stock(sid, T, window=res.window, calendar=calendar, bundle_dates=bundle_dates,
                                    first_seen=first_seen, valid=valid, stock_diff_days=stock_diff_days)
            day.classes[sid], day.reasons[sid] = c, why
        if day.diag_diffs and not day.diff_sids:
            day.classes["diag"], day.reasons["diag"] = CLASS_UNEXPLAINED, "列全同但 replay_day 診斷欄不同：" + "／".join(sorted(day.diag_diffs))
        cc = day.class_counts()
        if cc[CLASS_UNEXPLAINED] and (cc[CLASS_NEW] or cc[CLASS_SHORT]):
            day.hint = "本日另有①②：其 deque 較短會經廣度比污染市場列與全部個股列（§7.0 第 1 點），④ 可能是連帶而非獨立 bug"


def _rc(res: ParityResult) -> int:
    if res.errors:
        return RC_SETUP
    if res.counts()[CLASS_UNEXPLAINED]:
        return RC_UNEXPLAINED
    if res.market_layer_days:
        return RC_MARKET
    return RC_OK


# ---------------------------------------------------------------------------
def run(cache_dir: Path, repo: Path, *, start: str | None = None, end: str | None = None, data_version: str | None = None,
        window: int | None = None, log: Callable[[str], None] = print, show: int = 10, quiet: bool = False) -> ParityResult:
    cache, repo = Path(cache_dir), Path(repo)
    res = ParityResult()
    try:
        meta = _load_meta(repo)
        dv = data_version or str(meta.get("data_version") or "")
        w = int(window or meta.get("window") or 0)
        if not dv or not w:
            raise ParityError(f"{repo / DC.STATE_FILE} 的 meta 缺 data_version／window（{meta}），請用 --data-version／--window 指定")
        res.data_version, res.window = dv, w
        dates = scores_dates(repo, start, end)
        if not dates:
            raise ParityError(f"{repo / DC.SCORES_DIR} 在 [{start or '-∞'}, {end or '+∞'}] 內沒有分數檔")
        ref = ScoreStore(cache / "scores.db", readonly=True)
    except OPEN_ERRORS + (ParityError,) as e:
        res.errors.append(f"{type(e).__name__}: {e}")
        res.rc = RC_SETUP
        return res
    try:
        with tempfile.TemporaryDirectory(prefix="parity-") as tmp:
            _run_inner(res, ref, cache, repo, dates, Path(tmp), log=log, show=show, quiet=quiet)
    except OPEN_ERRORS + (ParityError,) as e:
        res.errors.append(f"{type(e).__name__}: {e}")
    finally:
        ref.close()
    res.rc = _rc(res)
    return res


def _run_inner(res: ParityResult, ref: ScoreStore, cache: Path, repo: Path, dates: list[str], tmp: Path, *,
               log: Callable[[str], None], show: int, quiet: bool) -> None:
    dv, w = res.data_version, res.window
    sha = ref.params_sha_of(dv)
    if sha is None:
        raise ParityError(f"參考 scores.db 沒有 data_version={dv}（有的是 {sorted({v['data_version'] for v in ref.versions()})}）")
    res.params_sha = sha
    got = ScoreStore(tmp / "parity.db")
    src: RIO.ReplaySource | None = None
    try:
        got_sha = got.set_params(dv, ref.params_of(dv))
        if got_sha != sha:
            raise ParityError(f"參數指紋重算 {got_sha} ≠ replay_meta 的 {sha}（features_io.params_fingerprint 變了？）")
        src = RIO.ReplaySource(cache, dv, window=w)
        ref_dates = set(ref.dates(dv))
        bundle_dates, first_seen, valid = scan_repo_bundles(repo)
        cal_path = repo / DC.CALENDAR_TPE_FILE
        calendar = DC.load_calendar_dates(cal_path) if cal_path.exists() else list(bundle_dates)
        # repo 側 us／fx 聯集＝比對區間內**全部**原料包（不限有分數檔的日子）：有包但無分數檔的日子（補跑中／計分失敗）
        # 其 us／fx 增量仍在那份包裡，只讀有分數檔的包會讓聯集缺日、誤報 rc 3。逐日 10 鍵仍只比有分數檔的日子。
        repo_in_range = {d: B.read_bundle(p) for d, p in B.list_bundles(repo) if dates[0] <= d <= dates[-1]}
        walk = set(src.trading_dates(dates[0], dates[-1]))
        want = set(dates)
        ref_us: list[list] = []
        ref_fx: list[list] = []
        repo_us = [b.us for _, b in sorted(repo_in_range.items())]
        repo_fx = [b.fx for _, b in sorted(repo_in_range.items())]
        log(f"data_version={dv} params_sha={sha} window={w} 比對 {len(dates)} 日（{dates[0]}～{dates[-1]}）"
            f"；repo 原料包 {len(bundle_dates)} 份（區間內 {len(repo_in_range)} 份）、參考交易日 {len(walk)} 日")
        for T in sorted(want | set(walk)):
            day = DayResult(T)
            if T in walk:
                refb = src.read_day(T)                       # 游標連續：區間內每個參考交易日都走到（us／fx 增量才對）
                ref_us.append(refb.us)
                ref_fx.append(refb.fx)
            else:
                refb = None
            if T not in want:
                continue
            res.dates.append(T)
            res.days[T] = day
            if refb is None:
                day.ref_missing.append("bundle")
            gotb = repo_in_range.get(T)
            if gotb is None:
                day.repo_bundle_missing = True
            elif refb is not None:
                compare_bundles(refb, gotb, day)
            if T not in ref_dates:
                day.ref_missing.append("scores")
            else:
                compare_scores(ref, got, repo, dv, T, sha, day)
            if day.ref_missing:
                res.errors.append(f"{T}: 參考端缺 {'／'.join(day.ref_missing)}（Hetzner 回補／重播尚未跑到這天？）")
        for key, a, b in (("us", ref_us, repo_us), ("fx", ref_fx, repo_fx)):
            res.dated_range[key], diffs = compare_dated(key, dated_union(a, key), dated_union(b, key))
            res.dated_diff_dates[key], res.dated_diffs[key] = [d for d, _ in diffs], [m for _, m in diffs]
        classify(res, calendar=calendar, bundle_dates=bundle_dates, first_seen=first_seen, valid=valid)
        for T in res.dates:
            report_day(res.days[T], log=log, show=show, quiet=quiet)
        report_summary(res, log=log, show=show)
    finally:
        got.close()
        if src is not None:
            src.close()


# ---------------------------------------------------------------------------
# 輸出
def _fmt_counts(cc: dict[int, int]) -> str:
    return " ".join(f"{CLASS_MARK[c]}{cc[c]}" for c in CLASSES)


def report_day(day: DayResult, *, log: Callable[[str], None], show: int, quiet: bool) -> None:
    seg = []
    if day.repo_bundle_missing:
        seg.append("原料包 repo 無此包")
    elif "bundle" in day.ref_missing:
        seg.append("原料包 參考端無此日")
    elif not day.key_diffs and not day.stock_diffs:
        seg.append("原料包 相同")
    else:
        parts = []
        if day.key_diffs:
            parts.append("市場層 " + "／".join(sorted(day.key_diffs)) + " 不同")
        if day.stock_diffs:
            parts.append(f"stocks {len(day.stock_diffs)} 檔不同")
        seg.append("原料包 " + "、".join(parts))
    if "scores" in day.ref_missing:
        seg.append("分數 參考端無此日")
    else:
        seg.append(f"分數 共同 {day.n_common:,} 列 不同 {day.n_diff:,}")
        seg.append("diag 相同" if not day.diag_diffs else "diag 不同 " + "／".join(sorted(day.diag_diffs)))
    if day.market_layer:
        seg.append(f"市場層原料不同（{day.market_layer_note}，自 {day.market_layer_since} 起），分數差異不歸類")
    elif day.n_diff or day.classes:
        seg.append("歸類 " + _fmt_counts(day.class_counts()) + (f" {SPILL_MARK}{len(day.spill)}" if day.spill else ""))
    log(f"{day.date}  " + " | ".join(seg))
    if quiet:
        return
    lines: list[str] = []
    lines += list(day.key_diffs.values())
    lines += [day.stock_diffs[s] for s in sorted(day.stock_diffs)]
    lines += day.msgs
    lines += [f"diag 欄 {c}: 參考={a!r} repo={b!r}" for c, (a, b) in sorted(day.diag_diffs.items())]
    lines += [f"{CLASS_MARK[c]} {sid}: {day.reasons.get(sid) or CLASS_LABEL[c]}" for sid, c in sorted(day.classes.items())]
    lines += [f"{SPILL_MARK} {sid}: {why}" for sid, why in sorted(day.spill.items())]
    if day.hint:
        lines.append("提示：" + day.hint)
    for m in lines[:show]:
        log("    " + m)
    if len(lines) > show:
        log(f"    …另 {len(lines) - show} 筆（--show 放大）")


def report_summary(res: ParityResult, *, log: Callable[[str], None], show: int) -> None:
    n_stock_b = sum(len(d.stock_diffs) for d in res.days.values())
    n_market_b = sum(1 for d in res.days.values() if d.key_diffs)
    dated = []
    for k in DATED_KEYS:
        rng = res.dated_range.get(k)
        n = len(res.dated_diffs.get(k, []))
        dated.append(f"{k} 聯集" + (f"[{rng[0]}～{rng[1]}]" if rng else "") + ("相同" if not n else f" {n} 處不同"))
    log(f"原料包：市場層鍵不同 {n_market_b} 日；stocks 逐檔不同 {n_stock_b} (日,檔)；" + "；".join(dated))
    for k in DATED_KEYS:
        for m in res.dated_diffs.get(k, [])[:show]:
            log("    " + m)
    cc = res.counts()
    log(f"分數：不同列 {sum(d.n_diff for d in res.days.values()):,}；歸類 {_fmt_counts(cc)}（(日,檔) 對數）；"
        f"{SPILL_MARK} {sum(len(d.spill) for d in res.days.values())} (日,檔)／{len(res.spill_days)} 日；"
        f"市場層原料不同而未歸類 {len(res.market_layer_days)} 日／{res.unclassified_rows:,} 列")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="D-3 parity 儀式：每日班產物 vs Hetzner 重播，原料包＋分數逐位比對（唯讀）")
    ap.add_argument("--cache-dir", default=str(REPO / "cache"), help="Hetzner cache（scores.db＋原料 sqlite＋features.db）")
    ap.add_argument("--repo", default=str(REPO), help="本 repo 的 git checkout（data/scores、runs/collect、data/state/cross.json）")
    ap.add_argument("--from", dest="start", default=None, help="起日（預設＝repo 內最早的分數檔）")
    ap.add_argument("--to", dest="end", default=None, help="迄日（預設＝repo 內最晚的分數檔）")
    ap.add_argument("--data-version", default=None, help="預設取 data/state/cross.json 的 meta")
    ap.add_argument("--window", type=int, default=None, help="預設取 data/state/cross.json 的 meta")
    ap.add_argument("--show", type=int, default=10, help="每日／每段最多印幾筆明細")
    ap.add_argument("--quiet", action="store_true", help="只印逐日摘要與總結")
    args = ap.parse_args(argv)
    res = run(Path(args.cache_dir), Path(args.repo), start=args.start, end=args.end, data_version=args.data_version,
              window=args.window, show=args.show, quiet=args.quiet)
    for e in res.errors:
        print(f"[parity 中止] {e}", file=sys.stderr)
    verdict = {RC_OK: "逐位相同或差異全部落在①②③", RC_UNEXPLAINED: "有④無法解釋的差異",
               RC_SETUP: "版本／參數不符、開檔失敗或無日期可比", RC_MARKET: "市場層原料不同"}[res.rc]
    print(f"結果：rc={res.rc}（{verdict}）")
    return res.rc


if __name__ == "__main__":
    raise SystemExit(main())
