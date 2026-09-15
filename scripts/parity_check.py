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
- **原料包以外的兩個輸入（§7.6.3 原已知限制，2026-09-15 補）**——這兩個檔不在原料包裡，第一輪 Hetzner 對帳「原料包 10 日逐位
  相同、分數每列不同」就是它們的嫌疑：
  ⑤**除權息係數**：repo `daily_core.load_factors_file(data/factors.json)` vs 參考 `ReplaySource.factors`（同一支 `feed.load_factors`
  →`adjust.cumulative_factors`），逐檔比「ex_date 序列與累積係數」（`bundle_io.dumps` 同參數逐位）。**只比池內檔、只比
  `ex_date ≤ 區間迄日` 的事件**（未來 ex_date 兩側本來就可能不同、池外檔（ETF 等）不進 `WindowCache`），兩者另計不算差異。
  每檔記「第一個不同的 ex_date」d5（只在一側／係數不同）；分數差異的檔在 T 日歸 ⑤ 的條件是 **d5 ≤ T**（累積係數是前綴連乘，
  d5 之後每一日都不同、之前逐位相同）。
  ⑥**基本面**：repo `daily_core.load_fundamentals_file` vs 參考 `ReplaySource.load_fundamentals(全部交易日)`（同一支
  `fundamentals.build_stock`），對每個 (sid, T) 走引擎同一個介面 `FundamentalsBridge.inputs_for(sid, T)` 比 as-of T 的
  `fundamentals`（9 鍵）與 `monthly_revenue` **最近 `revenue_lookback_months()`（18）個月**——repo 檔依 `FUND_MONTHS_KEEP` 只留
  24 個月、參考有全史，比整段必然不同；18＝引擎一爻的最長回看（`revenue_accel` window 3 → 3+3+12），由 `build_params` 算出、不手抄。
  產業中位數（`industry_median_3m_yoy`／`industry_revenue_n`）逐 (產業, T) 另比、另列（它由同產業各檔的營收長出，差異的源頭
  是某檔的 ⑥，本身不算一檔）。
  **歸類順序 ①→②→③→⑤→⑥→④**：⑤⑥優先於④、不優先於①②③（那三類是每日班路徑的結構性性質，先於輸入差異）。
  **⑤⑥連帶**：當日存在任何 ⑤／⑥ 檔時，其餘會落到④的列（大盤列與個股列）改列「⑤⑥連帶」——⑤改變的是該檔的還原價、⑥改變的
  是該檔的一爻，都經廣度比／產業聚合（`industry_median_return`、`industry_above_ma_ratio`、`industry_median_3m_yoy`）與
  `market_direction_score` 傳導到同日市場列與全部個股列。**代價：連帶會遮掉同日的真④**——只要當日有一檔 ⑤／⑥，同日任何
  獨立 bug 造成的④都會被列成連帶、rc 0；要驗證真④得先把 ⑤⑥ 清零（修 repo 的 factors／fundamentals 檔）再重跑。
  ⑤／⑥／⑤⑥連帶都不讓 rc 為 1，只有真④才 rc 1。
- **`--dump <path>`**：全部差異寫成 JSON Lines（副檔名 `.gz` 即 gzip）。分數列每個不同的欄一列 `{"kind":"score","date","market",
  "horizon","stock_id","col","a","b","class"}`（只在一側的列 `col` 為 null、`a`／`b` 為整列或 null）；`diag` 差異 `kind="diag"`；
  ⑤ 檔級差異 `kind="factor"`（`date`＝第一個不同的 ex_date；未來／池外的也寫、`class` 標「⑤未計」）；⑥ 每個 (sid, T)
  `kind="fund"`（`col`＝第一個不同的欄）、產業中位數 `kind="fund_industry"`。`a`＝參考、`b`＝repo，與報告同向。

rc：0 全同或只有①②③⑤⑥（含各種連帶）；1 有④；2 版本／參數不符、開檔失敗、無日期可比、參考端缺日；3 市場層原料不同
（含 `us`／`fx` 聯集差異）。優先序 2 > 1 > 3 > 0（④只會落在市場層標記日之前，是獨立證據）。
"""
from __future__ import annotations

import argparse
import bisect
import gzip
import json
import sqlite3
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Mapping

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
from iching.fundamentals import YOY_MONTHS, FundamentalsBridge  # noqa: E402
from iching.replay_state import MARKET_LINE2_HIST, DayBundle  # noqa: E402
from iching.run_common import ReplayDriverError, load_state  # noqa: E402
from iching.score import MARKET_STOCK_ID  # noqa: E402
from iching.score.params import HORIZONS, MARKETS, SCOPE_STOCK, build_params  # noqa: E402
from iching.score.stock import MONTHS_PER_YEAR  # noqa: E402
from iching.scores_io import ScoreStore, ScoreStoreError  # noqa: E402

BUNDLE_KEYS = ("schema", "band", "tpe_date", "index", "stocks", "official", "futures", "total_margin", "vix", "foreign_net_oi")
MARKET_KEYS = tuple(k for k in BUNDLE_KEYS if k != "stocks")     # 市場層：stocks 以外全部（schema／band／tpe_date 不同＝根本不可比）
DATED_KEYS = ("us", "fx")
DIAG_COLS = ("model_version_twse", "model_version_tpex", "n_market_rows", "n_stocks", "n_in_pool", "n_stock_rows",
             "n_stock_any_unknown", "n_market_any_unknown", "index_missing")      # 9 欄；排除 rank_pool_size／text_version
SCAN_MAXLEN = max((*SCAN.MA_WINDOWS, *SCAN.HL_WINDOWS, *(n + 1 for n in SCAN.RET_WINDOWS)))   # ＝scan.py DailyScanner._maxlen 的同一算式（61）
CLASS_NEW, CLASS_SHORT, CLASS_BUNDLE, CLASS_UNEXPLAINED, CLASS_FACTOR, CLASS_FUND = 1, 2, 3, 4, 5, 6
CLASSES = (CLASS_NEW, CLASS_SHORT, CLASS_BUNDLE, CLASS_UNEXPLAINED, CLASS_FACTOR, CLASS_FUND)
CLASS_MARK = {CLASS_NEW: "①", CLASS_SHORT: "②", CLASS_BUNDLE: "③", CLASS_UNEXPLAINED: "④", CLASS_FACTOR: "⑤", CLASS_FUND: "⑥"}
CLASS_LABEL = {CLASS_NEW: "①入池未滿 window 日", CLASS_SHORT: "②近 window 日有效收盤不足", CLASS_BUNDLE: "③該檔原料包有差異",
               CLASS_UNEXPLAINED: "④無法解釋", CLASS_FACTOR: "⑤除權息係數不同", CLASS_FUND: "⑥基本面 as-of 不同"}
SPILL_MARK = "①連帶"                                             # 新入池檔 E 之前整日（或 E 起 5 日內只差 flags 的大盤列）的連帶差異，不計④
SPILL56_MARK = "⑤⑥連帶"                                         # 當日有 ⑤／⑥ 檔時，其餘本會落到④的列（大盤列與個股列），不計④
FACTOR_UNCOUNTED = "⑤未計"                                       # --dump 用：未來 ex_date／池外檔的係數差異，不進 ⑤
RC_OK, RC_UNEXPLAINED, RC_SETUP, RC_MARKET = 0, 1, 2, 3
DUMP_KINDS = ("score", "diag", "factor", "fund", "fund_industry")


def revenue_lookback_months() -> int:
    """引擎對月營收的最長回看月數（一爻族 A／C 與產業中位數，`score/stock.py`）：`revenue_yoy` window w → w+12、
    `revenue_accel` w → 2w+12、`revenue_high_12m` w → w、`revenue_yoy_vs_industry` w → w+12、產業中位數 `YOY_MONTHS`+12。
    取兩市場 × 三 horizon 的最大值（現行參數＝18）。⑥只比 as-of T 的最近這麼多個月：repo 檔依 `FUND_MONTHS_KEEP` 修剪、
    參考有全史，比整段每檔必然不同。"""
    out = YOY_MONTHS + MONTHS_PER_YEAR
    for m in MARKETS:
        ps = build_params(m)
        for h in HORIZONS:
            for fam, iid, k in (("A", "revenue_yoy", 1), ("A", "revenue_accel", 2), ("A", "revenue_high_12m", 0), ("C", "revenue_yoy_vs_industry", 1)):
                try:
                    w = int(ps.get(SCOPE_STOCK, h, "1", fam, iid).window)
                except KeyError:
                    continue                                          # 該 horizon 沒有這個指標（如 revenue_high_12m 只有 mid）
                out = max(out, k * w + MONTHS_PER_YEAR if k else w)
    if out > DC.FUND_MONTHS_KEEP:
        raise ParityError(f"引擎月營收回看 {out} 個月 > daily_core.FUND_MONTHS_KEEP={DC.FUND_MONTHS_KEEP}，repo 基本面檔本來就不夠")
    return out
OPEN_ERRORS = (ScoreStoreError, RIO.ReplayIOError, FeatureStoreError, F.FeedError, ReplayDriverError, DC.DailyCoreError,
               B.BundleError, sqlite3.Error, OSError, ValueError, KeyError, TypeError)


class ParityError(RuntimeError):
    """設定／版本／開檔層級的中止（rc 2）。"""


def _ser(v: Any) -> str:
    """與 `bundle_io.dumps` 同一組 json 參數（`dumps_json`：NaN→null、tuple→list、鍵排序、無空白）。原料包子物件已是
    `bundle_to_dict` 清過的，再清一次是恆等；⑤⑥的值直接來自 `feed`／`FundamentalsBridge`，靠它把 NaN 與 tuple 統一。"""
    return B.dumps_json(v).decode("utf-8")


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
    classes: dict[str, int] = field(default_factory=dict)        # stock_id → 1..6
    reasons: dict[str, str] = field(default_factory=dict)
    spill: dict[str, str] = field(default_factory=dict)          # stock_id → ①連帶的理由（E 前整日；E 起 5 日內只有大盤列）
    spill56: dict[str, str] = field(default_factory=dict)        # stock_id → ⑤⑥連帶的理由（當日有 ⑤／⑥ 檔、本列本會落到④）
    fund_diffs: dict[str, str] = field(default_factory=dict)     # ⑥：stock_id → as-of T 第一個不同的欄與兩側值
    fund_ind_diffs: dict[str, str] = field(default_factory=dict) # 產業 → as-of T 中位數／樣本數不同（不算一檔；連帶的理由）
    diff_rows: list[tuple] = field(default_factory=list)      # --dump：(kind, 鍵, 欄或 None, 參考值, repo 值)；kind ∈ DUMP_KINDS
    hint: str = ""

    def factor_sids(self, res: "ParityResult") -> set[str]:
        """本日 ⑤ 集合＝第一個不同的 ex_date ≤ T 的檔。"""
        return {sid for sid, fd in res.factor_diffs.items() if fd.date <= self.date}

    def any56(self, res: "ParityResult") -> bool:
        return bool(self.factor_sids(res) or self.fund_diffs)

    @property
    def market_layer(self) -> bool:
        return self.market_layer_since is not None

    def class_counts(self) -> dict[int, int]:
        out = {c: 0 for c in CLASSES}
        for c in self.classes.values():
            out[c] += 1
        return out


@dataclass
class FactorDiff:
    """一檔除權息係數的第一個差異：`date`＝ex_date，`what`＝只在參考／只在 repo／係數不同，`a`／`b`＝兩側值（缺＝None）。"""
    stock_id: str
    date: str
    what: str
    a: float | None
    b: float | None

    def text(self) -> str:
        return f"factors {self.stock_id} ex_date {self.date}: {self.what}（參考={self.a!r} repo={self.b!r}）"


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
    factor_diffs: dict[str, FactorDiff] = field(default_factory=dict)           # ⑤：池內、第一個差異 ex_date ≤ 區間迄日的檔
    factor_uncounted: dict[str, FactorDiff] = field(default_factory=dict)       # 未來 ex_date／池外檔的係數差異（不計、只列）
    factor_note: str = ""                                          # 兩側係數檔的檔數摘要
    fund_note: str = ""                                            # 兩側基本面橋的檔數摘要（"" ＝ 未比，參考參數 fundamentals=False）
    industry_of: dict[str, str | None] = field(default_factory=dict)   # 池內 sid → 產業（兩側池聯集；連帶理由用）
    dump_path: Path | None = None
    dump_count: int = 0                                            # --dump 實際寫出的列數
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
    def spill56_days(self) -> list[str]:
        return [d.date for d in self.days.values() if d.spill56]

    @property
    def fund_diff_days(self) -> list[str]:
        return [d.date for d in self.days.values() if d.fund_diffs]

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
# ⑤ 除權息係數：逐檔「ex_date 序列＋累積係數」，只看 ex_date ≤ end 的事件、第一個不同處
Factors = Mapping[str, tuple[list[str], list[float]]]


def _first_factor_diff(sid: str, ref: tuple[list[str], list[float]] | None, got: tuple[list[str], list[float]] | None,
                       end: str | None) -> FactorDiff | None:
    """兩側該檔 (ex_dates, cum) 截到 `end`（None＝不截）後逐位比；回第一個差異或 None。累積係數是前綴連乘，第一個不同的
    ex_date 之後全部不同、之前逐位相同，所以一檔只需記一個日期。"""
    da, ca = ref or ([], [])
    db, cb = got or ([], [])
    if end is not None:
        na, nb = bisect.bisect_right(da, end), bisect.bisect_right(db, end)
        da, ca, db, cb = da[:na], ca[:na], db[:nb], cb[:nb]
    if _ser([da, ca]) == _ser([db, cb]):
        return None
    xa, xb = dict(zip(da, ca)), dict(zip(db, cb))
    for d in sorted(set(xa) | set(xb)):
        if d not in xa:
            return FactorDiff(sid, d, "只在 repo", None, xb[d])
        if d not in xb:
            return FactorDiff(sid, d, "只在參考", xa[d], None)
        if _ser(xa[d]) != _ser(xb[d]):
            return FactorDiff(sid, d, "累積係數不同", xa[d], xb[d])
    raise ParityError(f"factors {sid}: 序列化不同但逐日皆同（比對器不一致，程式 bug）")


def compare_factors(ref: Factors, got: Factors, pool_sids: set[str], end: str) -> tuple[dict[str, FactorDiff], dict[str, FactorDiff], str]:
    """回 (⑤ 集合, 不計的差異, 摘要)。⑤＝池內且第一個差異的 ex_date ≤ `end`；池外檔（不進 `WindowCache`）與只差在 `end` 之後的事件
    （未來 ex_date，兩側抓取時點不同本來就可能不同）列進「不計」。"""
    counted: dict[str, FactorDiff] = {}
    uncounted: dict[str, FactorDiff] = {}
    for sid in sorted(set(ref) | set(got)):
        fd = _first_factor_diff(sid, ref.get(sid), got.get(sid), None)
        if fd is None:
            continue
        if sid in pool_sids and fd.date <= end:
            counted[sid] = fd
        else:
            uncounted[sid] = FactorDiff(sid, fd.date, fd.what + ("" if sid in pool_sids else "（池外）"), fd.a, fd.b)
    n_in = sum(1 for s in ref if s in pool_sids), sum(1 for s in got if s in pool_sids)
    note = f"參考 {len(ref)} 檔（池內 {n_in[0]}）／repo {len(got)} 檔（池內 {n_in[1]}）"
    return counted, uncounted, note


# ---------------------------------------------------------------------------
# ⑥ 基本面：每個 (sid, T) 走引擎同一個介面 `inputs_for`，比 as-of T 的 9 鍵 dict 與最近 `months` 個月營收
def _ym_index(ym: str) -> int:
    return int(ym[:4]) * MONTHS_PER_YEAR + int(ym[5:7]) - 1


def _monthly_tail(rows: list | None, months: int) -> dict[str, float] | None:
    """`monthly_revenue`（升冪 `[(YYYY-MM, 元)]`）→ 以**該側自己的最新月**往回 `months` 個曆月的 {月: 值}；None／空 → None。
    引擎以 `max(rev)` 為 latest、按月鍵查表，所以「最新月不同」或「回看窗內任一月不同／缺」才是真差異，更早的月不影響任何指標。"""
    if not rows:
        return None
    latest = max(str(ym) for ym, _ in rows)
    lo = _ym_index(latest) - (months - 1)
    return {str(ym): float(v) for ym, v in rows if _ym_index(str(ym)) >= lo}


def _leaf_val(s: str) -> Any:
    """`_diff_leaf` 回的序列化葉值 → 物件（「（缺）」→ None），--dump 的 a／b 用。"""
    return None if s == "（缺）" else json.loads(s)


def _fund_view(br: FundamentalsBridge, sid: str, T: str, months: int) -> tuple[dict, dict]:
    """(該檔自己的 as-of 視圖, 產業層視圖)。前者＝該檔的 ⑥ 判準；後者由同產業各檔長出、逐 (產業, T) 另比。"""
    x = br.inputs_for(sid, T)
    own = {"monthly_revenue": _monthly_tail(x.get("monthly_revenue"), months), "fundamentals": x.get("fundamentals")}
    ind = {"industry_median_3m_yoy": x.get("industry_median_3m_yoy"), "industry_revenue_n": x.get("industry_revenue_n")}
    return own, ind


def compare_fundamentals(ref: FundamentalsBridge, got: FundamentalsBridge, dates: list[str], months: int,
                         days: Mapping[str, DayResult]) -> str:
    """逐 T 填 `days[T].fund_diffs`（sid → 第一個不同的欄）與 `days[T].fund_ind_diffs`（產業 → 中位數／樣本數不同）；回摘要。
    比對母體＝兩側 `industry_of`（＝池）的聯集：一側橋內沒有該檔＝`inputs_for` 回 None，與另一側有值即不同。"""
    sids = sorted(set(ref.industry_of) | set(got.industry_of))
    for T in dates:                                                # T 在外層：`inputs_for` 的產業統計每換一次 T 才重算一次
        day = days[T]
        seen_ind: set[str] = set()
        for sid in sids:
            oa, ia = _fund_view(ref, sid, T, months)
            ob, ib = _fund_view(got, sid, T, months)
            if _ser(oa) != _ser(ob):
                path, x, y = _diff_leaf(oa, ob)
                day.fund_diffs[sid] = f"fundamentals {sid} @{T} 欄 {path}: 參考={x[:160]} repo={y[:160]}"
                day.diff_rows.append(("fund", (sid, T), path, _leaf_val(x), _leaf_val(y)))
            ind = ref.industry_of.get(sid) or got.industry_of.get(sid)
            if ind is not None and ind not in seen_ind:
                seen_ind.add(ind)
                if _ser(ia) != _ser(ib):
                    path, x, y = _diff_leaf(ia, ib)
                    day.fund_ind_diffs[ind] = f"產業 {ind} @{T} 欄 {path}: 參考={x} repo={y}"
                    day.diff_rows.append(("fund_industry", (ind, T), path, _leaf_val(x), _leaf_val(y)))
    return (f"參考 {len(ref.stocks)} 檔／repo {len(got.stocks)} 檔有月營收或季報（池 {len(sids)} 檔）；"
            f"月營收只比 as-of 最新月往回 {months} 個月")


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


def _attribute(ref: ScoreStore, got: ScoreStore, dv: str, T: str) -> tuple[dict[str, int], int, dict[str, set[str]], list[tuple]]:
    """有差異列（只在一側／同鍵不同）→ {stock_id: 列數}、總數、{stock_id: 有差異的欄}、--dump 用的 (鍵, 欄, 參考值, repo 值) 列
    （只在一側的列一筆、欄 None、值＝整列或 None；同鍵不同的每個不同欄一筆）；鍵與相等判準與 `diff_scores.diff_day` 同一組。"""
    ra = {DS._key(r): r for r in ref.rows_for_day(dv, T)}
    rb = {DS._key(r): r for r in got.rows_for_day(dv, T)}
    per: dict[str, int] = {}
    cols: dict[str, set[str]] = {}
    recs: list[tuple] = []
    n = 0
    for k in sorted(set(ra) ^ set(rb)):
        per[k[2]] = per.get(k[2], 0) + 1
        recs.append((k, None, ra.get(k), rb.get(k)))
        n += 1
    for k in sorted(set(ra) & set(rb)):
        if ra[k] != rb[k]:
            per[k[2]] = per.get(k[2], 0) + 1
            diff = sorted(c for c in set(ra[k]) | set(rb[k]) if ra[k].get(c) != rb[k].get(c))
            cols.setdefault(k[2], set()).update(diff)
            recs.extend((k, c, ra[k].get(c), rb[k].get(c)) for c in diff)
            n += 1
    return per, n, cols, recs


def compare_scores(ref: ScoreStore, got: ScoreStore, repo: Path, dv: str, T: str, params_sha: str, day: DayResult) -> None:
    js = load_scores_json(repo, T, dv=dv, params_sha=params_sha)
    rows = [(r["model_version"], {k: v for k, v in r.items() if k != "model_version"}) for r in js["rows"]]
    got.write_day(dv, T, rows, js["diag"])
    day.n_common, day.n_diff, day.msgs = DS.diff_day(ref, got, dv, T)
    day.diff_sids, n, day.diff_cols, recs = _attribute(ref, got, dv, T)
    day.diff_rows.extend(("score", *r) for r in recs)
    if n != day.n_diff:
        raise ParityError(f"{T}: 差異歸戶列數 {n} ≠ diff_day 的 {day.n_diff}（比對器不一致，程式 bug）")
    dr, dg = ref.day_diag(dv, T), got.day_diag(dv, T)
    if dr is None:
        day.ref_missing.append("diag")
        return
    for c in DIAG_COLS:
        if dr.get(c) != (dg or {}).get(c):
            day.diag_diffs[c] = (dr.get(c), (dg or {}).get(c))
            day.diff_rows.append(("diag", (T,), c, dr.get(c), (dg or {}).get(c)))


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


def spill56_reason(day: DayResult, f5: set[str]) -> str | None:
    """本日有 ⑤／⑥ 檔 → 其餘本會落到④的列的連帶理由；沒有 → None。"""
    n5, n6 = len(f5), len(day.fund_diffs)
    if not n5 and not n6:
        return None
    ex = sorted(f5 | set(day.fund_diffs))[:4]
    return (f"本日有 ⑤{n5} 檔／⑥{n6} 檔（例 {'、'.join(ex)}），還原價／一爻經廣度比與產業聚合、大盤方向分數傳導到市場列與"
            f"全部個股列（會遮掉同日真④）")


def spill56_industry_note(day: DayResult, res: ParityResult, sid: str) -> str:
    ind = res.industry_of.get(sid)
    return f"；所屬產業 {ind} 的營收中位數 as-of {day.date} 不同" if ind is not None and ind in day.fund_ind_diffs else ""


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
        f5 = day.factor_sids(res)
        why56 = spill56_reason(day, f5)
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
            if c == CLASS_UNEXPLAINED:                                # ①②③ 先於輸入差異；⑤⑥ 只接手本會落到④的
                if sid in f5:
                    c, why = CLASS_FACTOR, res.factor_diffs[sid].text()
                elif sid in day.fund_diffs:
                    c, why = CLASS_FUND, day.fund_diffs[sid]
                elif why56 is not None:
                    day.spill56[sid] = why56 + spill56_industry_note(day, res, sid)
                    continue
            day.classes[sid], day.reasons[sid] = c, why
        if day.diag_diffs and not day.diff_sids:
            if why56 is not None:
                day.spill56["diag"] = "列全同但 replay_day 診斷欄不同（" + "／".join(sorted(day.diag_diffs)) + "）；" + why56
            else:
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
        window: int | None = None, log: Callable[[str], None] = print, show: int = 10, quiet: bool = False,
        dump: Path | None = None) -> ParityResult:
    cache, repo = Path(cache_dir), Path(repo)
    res = ParityResult()
    res.dump_path = Path(dump) if dump else None
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
        # ⑤⑥：原料包以外的兩個輸入。repo 端讀法＝`daily_core.run_offline` 逐字（pool→factors→fundamentals 用 repo 日曆），
        # 參考端＝`replay_scores`（`src.factors`／`src.load_fundamentals(src.trading_dates())`）
        _, repo_pool = DC.load_pool_file(repo / DC.POOL_FILE)
        pool_sids = set(map(str, repo_pool)) | set(map(str, src.pool))
        res.industry_of = {**{s: i.get("industry_category") for s, i in src.pool.items()},
                           **{s: i.get("industry_category") for s, i in repo_pool.items()}}
        _, repo_factors, _ = DC.load_factors_file(repo / DC.FACTORS_FILE)
        res.factor_diffs, res.factor_uncounted, res.factor_note = compare_factors(src.factors, repo_factors, pool_sids, dates[-1])
        use_fund = bool((ref.params_of(dv) or {}).get("fundamentals", True))
        fund_pair: tuple[FundamentalsBridge, FundamentalsBridge] | None = None
        if use_fund:
            _, repo_bridge = DC.load_fundamentals_file(repo / DC.FUND_FILE, repo_pool, calendar)
            fund_pair = (src.load_fundamentals(src.trading_dates()), repo_bridge)
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
        if fund_pair is not None:
            res.fund_note = compare_fundamentals(fund_pair[0], fund_pair[1], res.dates, revenue_lookback_months(), res.days)
        classify(res, calendar=calendar, bundle_dates=bundle_dates, first_seen=first_seen, valid=valid)
        if res.dump_path is not None:
            res.dump_count = write_dump(res, res.dump_path)
        for T in res.dates:
            report_day(res.days[T], res, log=log, show=show, quiet=quiet)
        report_summary(res, log=log, show=show)
    finally:
        got.close()
        if src is not None:
            src.close()


# ---------------------------------------------------------------------------
# --dump：全部差異 → JSON Lines（.gz 即 gzip）。每列 a＝參考、b＝repo；class 與報告的歸類同字
def _row_class(day: DayResult, sid: str) -> str:
    if day.market_layer:
        return "市場層"
    if sid in day.classes:
        return CLASS_MARK[day.classes[sid]]
    if sid in day.spill:
        return SPILL_MARK
    if sid in day.spill56:
        return SPILL56_MARK
    return "?"


def dump_records(res: ParityResult):
    """依日期序吐出 dict 列（`kind` ∈ DUMP_KINDS）。"""
    for fd in list(res.factor_diffs.values()) + list(res.factor_uncounted.values()):
        yield {"kind": "factor", "date": fd.date, "stock_id": fd.stock_id, "col": fd.what, "a": fd.a, "b": fd.b,
               "class": CLASS_MARK[CLASS_FACTOR] if fd.stock_id in res.factor_diffs else FACTOR_UNCOUNTED}
    for T in res.dates:
        day = res.days[T]
        for kind, key, col, a, b in day.diff_rows:
            if kind == "score":
                yield {"kind": kind, "date": T, "market": key[0], "horizon": key[1], "stock_id": key[2], "col": col, "a": a, "b": b,
                       "class": _row_class(day, key[2])}
            elif kind == "diag":
                yield {"kind": kind, "date": T, "col": col, "a": a, "b": b, "class": _row_class(day, "diag")}
            elif kind == "fund":
                yield {"kind": kind, "date": T, "stock_id": key[0], "col": col, "a": a, "b": b, "class": CLASS_MARK[CLASS_FUND]}
            else:
                yield {"kind": kind, "date": T, "industry": key[0], "col": col, "a": a, "b": b, "class": SPILL56_MARK}


def write_dump(res: ParityResult, path: Path) -> int:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    opener = gzip.open if path.suffix == ".gz" else open
    n = 0
    with opener(path, "wt", encoding="utf-8") as f:
        for rec in dump_records(res):
            f.write(_ser(rec) + "\n")
            n += 1
    return n


def read_dump(path: Path) -> list[dict]:
    path = Path(path)
    opener = gzip.open if path.suffix == ".gz" else open
    with opener(path, "rt", encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


# ---------------------------------------------------------------------------
# 輸出
def _fmt_counts(cc: dict[int, int]) -> str:
    return " ".join(f"{CLASS_MARK[c]}{cc[c]}" for c in CLASSES)


def report_day(day: DayResult, res: ParityResult, *, log: Callable[[str], None], show: int, quiet: bool) -> None:
    seg = []
    f5 = day.factor_sids(res)
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
    elif day.n_diff or day.classes or day.spill56:
        seg.append("歸類 " + _fmt_counts(day.class_counts()) + (f" {SPILL_MARK}{len(day.spill)}" if day.spill else "")
                   + (f" {SPILL56_MARK}{len(day.spill56)}（⑤{len(f5)}＋⑥{len(day.fund_diffs)} 檔傳導）" if day.spill56 else ""))
    if day.fund_diffs or day.fund_ind_diffs:
        seg.append(f"基本面 as-of 不同 {len(day.fund_diffs)} 檔／產業中位數不同 {len(day.fund_ind_diffs)} 產業")
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
    lines += [day.fund_ind_diffs[i] for i in sorted(day.fund_ind_diffs)]
    lines += [f"{SPILL56_MARK} {sid}: {why}" for sid, why in sorted(day.spill56.items())]
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
    log(f"除權息係數：{res.factor_note}；⑤ {len(res.factor_diffs)} 檔（池內、第一個差異 ex_date ≤ {res.dates[-1] if res.dates else '?'}）"
        f"；不計 {len(res.factor_uncounted)} 檔（未來 ex_date／池外）")
    for fd in list(res.factor_diffs.values())[:show]:
        log("    " + fd.text())
    for fd in list(res.factor_uncounted.values())[:show]:
        log(f"    （不計）{fd.text()}")
    if res.fund_note:
        n6 = sum(len(d.fund_diffs) for d in res.days.values())
        sids6 = {sid for d in res.days.values() for sid in d.fund_diffs}
        log(f"基本面：{res.fund_note}；⑥ {n6} (日,檔)／{len(sids6)} 檔／{len(res.fund_diff_days)} 日；"
            f"產業中位數不同 {sum(len(d.fund_ind_diffs) for d in res.days.values())} (日,產業)")
    else:
        log("基本面：參考參數 fundamentals=False，未比")
    cc = res.counts()
    n56 = sum(len(d.spill56) for d in res.days.values())
    src56 = {sid for d in res.days.values() if d.spill56 for sid in (d.factor_sids(res) | set(d.fund_diffs))}
    log(f"分數：不同列 {sum(d.n_diff for d in res.days.values()):,}；歸類 {_fmt_counts(cc)}（(日,檔) 對數）；"
        f"{SPILL_MARK} {sum(len(d.spill) for d in res.days.values())} (日,檔)／{len(res.spill_days)} 日；"
        f"{SPILL56_MARK} {n56} (日,檔)／{len(res.spill56_days)} 日（由 {len(src56)} 檔 ⑤⑥ 傳導）；"
        f"市場層原料不同而未歸類 {len(res.market_layer_days)} 日／{res.unclassified_rows:,} 列")
    if res.dump_path is not None:
        log(f"差異明細已寫 {res.dump_path}（{res.dump_count:,} 列 JSON Lines）")


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
    ap.add_argument("--dump", default=None, help="全部差異寫成 JSON Lines（副檔名 .gz 即 gzip）")
    args = ap.parse_args(argv)
    res = run(Path(args.cache_dir), Path(args.repo), start=args.start, end=args.end, data_version=args.data_version,
              window=args.window, show=args.show, quiet=args.quiet, dump=Path(args.dump) if args.dump else None)
    for e in res.errors:
        print(f"[parity 中止] {e}", file=sys.stderr)
    verdict = {RC_OK: "逐位相同或差異全部落在①②③⑤⑥（含連帶）", RC_UNEXPLAINED: "有④無法解釋的差異",
               RC_SETUP: "版本／參數不符、開檔失敗或無日期可比", RC_MARKET: "市場層原料不同"}[res.rc]
    print(f"結果：rc={res.rc}（{verdict}）")
    return res.rc


if __name__ == "__main__":
    raise SystemExit(main())
