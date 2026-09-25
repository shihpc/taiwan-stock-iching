"""個股池（使用者 2026-09-09 裁定 1）：

全市場 4 碼普通股＝`TaiwanStockInfo` 的 `type` ∈ {twse, tpex}、代號 4 碼純數字、非 `00` 開頭、
**且排除 DR**（2026-09-10 裁定甲，見末段；4 碼形狀含 11 檔存託憑證，「4 碼池 ≠ 普通股池」）；
point-in-time 池＝「當日**有成交**」者（`is_traded_row()`：`close > 0` 且 `Trading_Volume > 0`；
2026-09-12 修正，原寫「當日有價格列」會把停牌／零成交列算進母體）。裁定原文的「現為 3,060 檔」**是列數不是檔數**（2026-09-12 實測坐實，見下文）；
實際池＝不重複代號 2,150 − 11 檔 DR ＝ **2,139**。
流動性門檻不在本腳本處理。

P0-A §4.4：`TaiwanStockInfo` 會有殘留列（同一代號多列、市場轉換／產業重分類），故以「任一列符合」納入，
市場別／產業別取 **`date` 最大的那一列**；同 `date` 仍多列時走**決定性 tie-break**（2026-09-09 驗收更正：
原「後者覆蓋」取決於 FinMind 回列順序，例 3092 同日兩列 `電子零組件業`／`電子工業`）：
三層（2026-09-12 裁定 #28 由兩層擴為三層，順序不可調換，理由見 `_pick` docstring）：
①剔除 `NON_INDUSTRY_CATEGORIES`（板別等非產業軸，例 `創新板股票`）→ ②剔除 `UMBRELLA_CATEGORIES`
（母類，例 `電子工業`／`化學生技醫療`）若尚有更細者 → ③**優先 `type=="twse"`**，最後依
(industry_category, stock_name) 字串序取第一。①②各自保留「剔完為空就退回」的降級。
③ 是唯一沒有語意依據的一層；2026-09-12 Hetzner 實測 603 檔同日多列，落到 ③ 的是 **0 檔**。`same_date_multi=True` 標出這種代號，report 列出檔數供人工複核。
優先 twse 的理由：同代號同日殘留兩個市場時（2026-09-09 實查 11 檔跨 twse/tpex），台股轉板慣例是上櫃→上市，
取上市作為「較新狀態」的近似——**這是推測、不是查證**，所以只當 tie-break、不當市場判定（T 日所屬市場在
`config.OUT_OF_SCOPE`，由後續模組以殘留列 `date` 重建）。

**2026-09-09 本容器免 token 實打 `TaiwanStockInfo`（4,319 列）**：type∈{twse,tpex} 且 4 碼純數字非 00 開頭的
**列數**＝1,996＋1,064＝**3,060**、恰等於裁定寫的「現為 3,060 檔」；但**不重複代號只有 2,149**（835 檔有多列，
例 5348 兩列：2025-06-01 通信網路業／2026-09-09 運動休閒類）。本模組以不重複代號為池。

**2026-09-12 Hetzner 實測坐實「3,060 是列數」**（`raw_stock_info` 4,321 列，回補當班 09-11 抓的）：
同一條件的**不重複代號 2,150**、其中**存託憑證 11**、扣除後池＝**2,139**（`report` 實印 2,139，
`{'tpex': 926, 'twse': 1213}`）。既然合格代號**上限**就只有 2,150，「3,060 檔」在檔數的讀法下不可能成立，
**列數讀法是唯一自洽的解釋**。09-09 的 2,149 與今日的 2,150 差 1 檔，是兩份快照的差異
（4,319 列 vs 4,321 列，相隔兩日），**不是計數錯誤**。

**⚠ `config.POOL_SIZE_RULING = 3060` 仍是列數**：它只在 `raw_stock_info` 尚未落地時供 `plan` 估算
per_stock 請求數（`scripts/backfill_hetzner.py` grep `POOL_SIZE_RULING`），會把 2,139 高估成 3,060（+43%）。
高估使計畫偏保守、不會少抓，故**刻意未改**；要改屬裁定範圍（會變動 `plan` 的輸出數字）。

**「T 日所屬市場」不在本模組**（`config.OUT_OF_SCOPE`）：`pit_pool()` 只回「合格代號 ∩ 當日有成交」，
T 日屬 twse／tpex 需由殘留列的 `date` 重建轉換點，交後續 universe 模組。

**DR 不進個股池（使用者 2026-09-10 裁定甲）**：4 碼純數字非 `00` 的代號裡有 **11 檔存託憑證**
（`9101,9102,9103,9104,9105,9106,9110,9136,9151,9157,9188`，`industry_category='存託憑證'`），裁定 #6 的形狀條件
會把它們收進池。理由：與 `spec/P1-B1-market.md:157`「排除 ETF／權證／DR／特別股／興櫃」一致；DR 是境外公司存託憑證，
財報／營收口徑與本國股不同，B2 基本面族會算出無意義的值；廣度母體 `N` 一律排除 DR（spec 明文）。
`is_dr_code()` 以**兩條件取聯集**排除：①`industry_category == '存託憑證'`；②純形狀後備＝4 碼且以 `91` 開頭。
兩條並存的理由：①來自**今日快照**，已下市 DR 在 `TaiwanStockInfo` 查不到、只剩價格列，只能靠形狀②；
②單獨用則依賴「91xx 全是 DR」這個經驗規律，故以①為主、②為後備。
**實查（2026-09-10 本容器免 token 打 `TaiwanStockInfo`，4,321 列）**：4 碼純數字 `91xx` 恰 11 檔、全為 `存託憑證`；
`industry_category='存託憑證'` 共 36 檔（4 碼 11＋6 碼 25），**全部以 `91` 開頭**，無例外。
**殘餘風險**：日後若有非 DR 的 `91xx` 4 碼普通股掛牌，會被②誤殺——依上述實查目前不存在，但快照只能證明「現在」。
排除只發生在**讀取端的名單建構**（本函式）；落地仍保留 DR 原始列（4 碼與 6 碼皆是），與 `config.is_warrant_code`
（落地過濾，只砍權證）是兩件事、不混在同一函式。
"""
from __future__ import annotations

from typing import Iterable

POOL_TYPES = frozenset({"twse", "tpex"})
# FinMind `industry_category` 的**傘狀（母）類別**：同代號同日另有更細類別時不取它（2026-09-09 驗收所見：3092）。
# `化學生技醫療` 於 2026-09-12 裁定 #28 加入：Hetzner 實查 603 檔同日多列，其中 83 檔的候選是
# `{化學工業, 化學生技醫療}`(29) 或 `{化學生技醫療, 生技醫療業}`(54)，**從未出現 `{化學工業, 生技醫療業}`**
# ——母類拆成兩個子類的簽名。加入前這 54 檔靠字串序取到母類（`化學工業` < `化學生技醫療` < `生技醫療業`），
# 同一個標籤對上 `化學工業` 被丟掉、對上 `生技醫療業` 卻贏，內部不一致。
UMBRELLA_CATEGORIES = frozenset({"電子工業", "化學生技醫療"})
# **非產業標籤**（板別等，不是產業別）：優先於傘狀排除先剔除（2026-09-12 裁定 #28）。
# `創新板股票` 是上市**板別**，Hetzner 實查 29 檔全在 twse 且每一檔都另有真實產業可選，
# 加入前它靠字串序贏過真產業（汽車工業／半導體業／綠能環保…），會憑空生出一個 29 檔的假產業污染產業輪動。
# 與 `UMBRELLA_CATEGORIES` **刻意分成兩個集合**：排除的理由不同（母類 vs 非產業軸），
# 日後 FinMind 冒出新標籤才知道該加進哪一個。
# `創新版股票`（「版」）＝FinMind 標籤異體：`data/pool.json` 6423 的 2024-12-04 殘留列實查（2026-09-17 驗收退回），與「板」同一個板別。
NON_INDUSTRY_CATEGORIES = frozenset({"創新板股票", "創新版股票"})
# 存託憑證（DR）：FinMind `industry_category` 的字面值；4 碼 DR 的形狀前綴（實查見模組 docstring）
DR_CATEGORY = "存託憑證"
DR_PREFIX_4 = "91"


FINANCIAL_INDUSTRIES = frozenset({"金融保險", "金融業"})


def is_financial(industry_category: str | None) -> bool:
    """金融保險業替代規則的判定（`spec/P1-B2-params.md:148`，裁定 #28）：**明列** `{金融保險, 金融業}`，
    上市是 `金融保險`（46 檔）、上櫃是 `金融業`（10 檔）。不得改成「含『金融』」的模糊比對。"""
    return industry_category in FINANCIAL_INDUSTRIES


def is_dr_code(stock_id: str, industry_category: str | None) -> bool:
    """DR 判定（池過濾，2026-09-10 裁定甲）：`industry_category=='存託憑證'` **或** 4 碼且以 `91` 開頭（已下市 DR 的後備）。
    只用於個股池名單建構；不在落地路徑使用。"""
    sid = str(stock_id or "")
    return (industry_category or "") == DR_CATEGORY or (len(sid) == 4 and sid.isdigit() and sid.startswith(DR_PREFIX_4))


def is_pool_candidate(stock_id: str, type_: str | None) -> bool:
    sid = str(stock_id or "")
    return (
        (type_ or "") in POOL_TYPES
        and len(sid) == 4
        and sid.isdigit()
        and not sid.startswith("00")
    )


def _pick(rows: list[dict]) -> dict:
    """同一代號多列 → 取 date 最大；同 date 多列 → 決定性 tie-break。

    tie-break 三層，**順序不可調換**（2026-09-12 裁定 #28）：
    ① 剔除 `NON_INDUSTRY_CATEGORIES`（板別等非產業軸）→ ② 剔除 `UMBRELLA_CATEGORIES`（母類，取細不取粗）
    → ③ 優先 `type=="twse"`，再依 (industry_category, stock_name) 字串序取第一。
    ①②**各自**保留「剔完為空就退回上一步的集合」的降級——只掛板別或只掛母類的股票不能變成沒有分類
    （實查：`電子工業` 31 檔、`化學生技醫療` 8 檔沒有更細可選，那是資料限制、不是規則缺陷）。
    ③ 是**唯一沒有語意依據**的一層，只為決定性而存在；裁定 #28 後實測落到這一層的檔數應為 0。
    """
    max_date = max(str(r.get("date") or "") for r in rows)
    tied = [r for r in rows if str(r.get("date") or "") == max_date]
    real = [r for r in tied if (r.get("industry_category") or "") not in NON_INDUSTRY_CATEGORIES]
    base = real or tied
    finer = [r for r in base if (r.get("industry_category") or "") not in UMBRELLA_CATEGORIES]
    cands = finer or base
    cands = sorted(cands, key=lambda r: (0 if r.get("type") == "twse" else 1,
                                         str(r.get("industry_category") or ""), str(r.get("stock_name") or "")))
    return cands[0]


def pool_from_info(rows: Iterable[dict]) -> dict[str, dict]:
    """{stock_id: {"type","industry_category","stock_name","date","n_rows","same_date_multi"}}，只含合格代號。

    DR 排除（2026-09-10 裁定甲）看**該代號的任一列**：任一列 `industry_category=='存託憑證'`，或代號本身符合
    4 碼 `91` 開頭的形狀規則，整個代號不進池——不因殘留列（產業重分類）恰好不是 DR 而漏放。"""
    by_id: dict[str, list[dict]] = {}
    for r in rows:
        sid = str(r.get("stock_id") or "")
        if not is_pool_candidate(sid, r.get("type")):
            continue
        by_id.setdefault(sid, []).append(r)
    out: dict[str, dict] = {}
    for sid, rs in by_id.items():
        if any(is_dr_code(sid, r.get("industry_category")) for r in rs):
            continue
        best = _pick(rs)
        max_date = str(best.get("date") or "")
        n_tied = sum(1 for r in rs if str(r.get("date") or "") == max_date)
        out[sid] = {"type": best.get("type"), "industry_category": best.get("industry_category"),
                    "stock_name": best.get("stock_name"), "date": max_date, "n_rows": len(rs),
                    "same_date_multi": n_tied > 1}
    return out


# FinMind `TaiwanStockPrice` 的欄位名（原樣，不改寫）
PRICE_CLOSE = "close"
PRICE_VOLUME = "Trading_Volume"
PRICE_AMOUNT = "Trading_money"


def is_traded_row(row: dict) -> bool:
    """該列是否為「當日有成交」（`P1-B1-market.md:157` 家數口徑：`N` ＝當日**有成交的**普通股家數）。

    判準＝`close > 0` **且** `Trading_Volume > 0`，兩條都要。基本理由：FinMind 對停牌／無量的個股
    仍會回一列，只看「有沒有列」會把這些算進 `N`，`N` 被灌水、所有家數比一起被稀釋，而且**不會報錯**。

    **實測（2026-09-13，Hetzner `scripts/probe_features.py --probe traded`，`data_version=fm-20260911-01`，
    池內普通股 3,056,594 列 ＝ 2,139 檔 × 1,618 日的 88.3%）——這組數字推翻了本函式原本寫的一半理由**：

    | 象限 | 列數 | 佔比 |
    |---|---:|---:|
    | `close>0` 且 量>0（判為有成交） | 3,020,396 | 98.81% |
    | **只有 `close>0`（有參考價、零成交）** | **0** | **0.000%** |
    | 只有 量>0（有量但 `close` 為 0，畸形列） | 14,454 | 0.473% |
    | 兩者皆無 | 21,744 | 0.712% |

    - **`close > 0` 是承重的那一條**：只用它收到的列與兩條件**完全相同**（因為「只有 close>0」恰為 0）。
    - **`Trading_Volume > 0` 目前是 no-op**：加不加，結果一模一樣。原註解寫的「只看 `close > 0`
      漏掉『有參考價、零成交』」在 2020-01 ~ 2026-08 這 6.7 年裡**一次都沒發生**，是未經查證的推論。
    - **只用量 > 0 才是真的會錯**：會多收 14,454 列 `close=0` 的畸形列，那些列進得了母體卻算不出
      任何指標（MA／新高低／漲跌全部要 `close`）。
    - 「兩者皆無」21,744 列＋畸形 14,454 列 ＝ **36,198 列**被排除，與 2026-09-12 另一次實查
      「池內零價列 36,198」對得上（獨立交叉驗證）。

    **刻意保留 `Trading_Volume > 0`**：它現在不做事，但成本為零，且擋的是「FinMind 日後改回
    參考價填值」這種上游形狀變動——那種變動會無聲地灌水 `N`。要拿掉屬裁定範圍，不是實作細節。
    **不可再宣稱「兩條件都必要」**：正確的說法是「一條承重、一條防未來」。
    """
    try:
        close = float(row.get(PRICE_CLOSE) or 0)
        vol = float(row.get(PRICE_VOLUME) or 0)
    except (TypeError, ValueError):
        return False
    return close > 0 and vol > 0


def traded_ids(items: Iterable[tuple[str, dict]]) -> set[str]:
    """**兩條路徑共用的唯一一道成交門**（2026-09-17 驗收退回後抽出）：`(stock_id, 價格列 dict)` → 當日有成交的代號集合，
    判準只有 `is_traded_row()`。`feed.day_records`（參考路徑／每日班重建）與 `replay_state.WindowCache.ingest`（重播／每日班 step）
    **都必須呼叫這一支**，不得各自再寫一次 `is_traded_row` 的呼叫——兩處各寫一次時突變只讓一側紅，parity 就變成「恰好一樣」。"""
    return {str(sid) for sid, row in items if is_traded_row(row)}


def pit_pool(pool_ids: Iterable[str], price_rows_for_day: Iterable[dict]) -> list[str]:
    """point-in-time 池：合格代號 ∩ 當日**有成交**（不分市場，見模組 docstring）。

    **2026-09-12 修**：原本只要求「當日有價格列」，把停牌／零成交列也算進池——那些列
    `close` 為 0 或量為 0，進了母體卻算不出任何指標。判準改用 `is_traded_row()`。
    """
    ids = set(pool_ids)
    have = {str(r.get("stock_id") or "") for r in price_rows_for_day if is_traded_row(r)}
    return sorted(ids & have)


# ---------------------------------------------------------------------------
# point-in-time 池（P3 第 1 項，`docs/P3-PIT-POOL.md` §1；裁定 #49 Q9～Q14）
# ---------------------------------------------------------------------------
POOL_SEMANTICS = "pit-1"        # 池語意指紋（Q9）：進 `run_common.build_params_payload` 與 `scan_features.build_params`，不進 model_version
_STATIC_META_KEYS = ("industry_category", "stock_name", "date", "n_rows", "same_date_multi")


def _next_calendar_day(d: str) -> str:
    """`YYYY-MM-DD` 的下一個曆日（Q11：轉換生效日＝較舊那列 `date` +1）。解析不了的字串原樣回傳（只會在快照 `date` 壞掉時發生，
    比對仍走字串序、不靜默吞掉）。"""
    from datetime import date, timedelta
    try:
        return (date.fromisoformat(d) + timedelta(days=1)).isoformat()
    except ValueError:
        return d


def _transition_seq(rows: list[dict]) -> tuple[tuple[str | None, str], ...]:
    """某代號的快照列 → `((生效日, type), …)`，首項生效日為 `None`（＝資料起點以前就是這個市場，Q11）。

    依 `date` 分組升冪走訪；同 `date` 多列走 `_pick` 三層 tie-break（只在平手時），取其 `type`；`type` 與前一組不同
    ＝轉換點，生效日＝**前一組**（較舊那列）的 `date` +1 曆日。同 type 連續的組不產生轉換（產業重分類殘留列不是轉市）。"""
    by_date: dict[str, list[dict]] = {}
    for r in rows:
        by_date.setdefault(str(r.get("date") or ""), []).append(r)
    seq: list[tuple[str | None, str]] = []
    prev_date: str | None = None
    for d in sorted(by_date):
        t = str(_pick(by_date[d]).get("type") or "")
        if not seq:
            seq.append((None, t))
        elif t != seq[-1][1]:
            seq.append((_next_calendar_day(prev_date) if prev_date else None, t))
        prev_date = d
    return tuple(seq)


class PitPool:
    """point-in-time 池：由**一份** `TaiwanStockInfo` 快照（含殘留列）建成，參考路徑（Hetzner 全量）與每日班共用同一個物件。

    - **靜態屬性**（不隨 T 變）＝ `pool_from_info()` 的候選規則（4 碼純數字、非 `00`、任一列 type∈{twse,tpex}、排除 DR）＋名稱／產業
      取最新列（同 date 走三層 tie-break）。`self.static[sid]` 是那份 meta **去掉 `type`**——市場別一律問 `market()`／`listed()`，
      不留「最新一列的 type」這個舊語意給人誤用。
    - **市場轉換表** `self.transitions[sid]`＝`_transition_seq()`；`market(sid, T)`＝T 當日生效的 type，T 早於最舊一列取最舊列的
      type（Q11）。`emerging` 也是一個 type，`listed()` 只認 {twse, tpex}（Q12：興櫃時期一律不在池，即使有成交列）。
    - **成員** `members(T, traded_sids)`＝靜態合格 ∧ `market(sid,T)∈{twse,tpex}` ∧ `sid∈traded_sids`；`traded_sids` 由呼叫端用
      `is_traded_row()` 算（Q10），本類別不碰價格列。meta 形狀與 `pool_from_info` 的值相同、但 `type`＝T 日市場。
    - **快照裡沒有的代號不在池**（含已從 `TaiwanStockInfo` 消失的下市股）：沒有任何列就沒有市場別可分桶，本類別不憑代號形狀猜市場
      （`docs/P3-PIT-POOL.md` §6 記為未做項，待裁定）。

    Mapping 介面（`in`／`[]`／`len`／`iter`／`items`）**一律指靜態集合**——給只要「名單＋產業別」的呼叫端
    （`collect.stocks_from_rows`／基本面橋／entrants 偵測）；任何要市場別的地方必須帶 T 問 `listed()`。
    """

    __slots__ = ("static", "transitions")

    def __init__(self, static: dict[str, dict], transitions: dict[str, tuple[tuple[str | None, str], ...]]) -> None:
        self.static = {str(k): dict(v) for k, v in static.items()}
        self.transitions = {str(k): tuple(tuple(x) for x in v) for k, v in transitions.items()}
        missing = set(self.static) - set(self.transitions)
        if missing:
            raise ValueError(f"PitPool：{len(missing)} 個靜態代號沒有轉換表（例 {sorted(missing)[:3]}）")

    @classmethod
    def from_snapshot_rows(cls, rows: Iterable[dict]) -> "PitPool":
        rows = list(rows)
        base = pool_from_info(rows)
        by_id: dict[str, list[dict]] = {}
        for r in rows:
            sid = str(r.get("stock_id") or "")
            if sid in base:
                by_id.setdefault(sid, []).append(r)         # 含 emerging 等非池 type 的列：轉換表要看到它們
        static = {sid: {k: v for k, v in meta.items() if k in _STATIC_META_KEYS} for sid, meta in base.items()}
        return cls(static, {sid: _transition_seq(by_id[sid]) for sid in base})

    # -- Mapping（靜態集合） ------------------------------------------------
    def __contains__(self, sid: object) -> bool:
        return str(sid) in self.static

    def __getitem__(self, sid: str) -> dict:
        return self.static[str(sid)]

    def __iter__(self):
        return iter(self.static)

    def __len__(self) -> int:
        return len(self.static)

    def keys(self):
        return self.static.keys()

    def items(self):
        return self.static.items()

    def values(self):
        return self.static.values()

    def get(self, sid: str, default=None):
        return self.static.get(str(sid), default)

    def __eq__(self, other: object) -> bool:
        return isinstance(other, PitPool) and self.static == other.static and self.transitions == other.transitions

    __hash__ = None  # type: ignore[assignment]

    def industry_of(self, sid: str) -> str | None:
        m = self.static.get(str(sid))
        return m.get("industry_category") if m else None

    # -- point-in-time ------------------------------------------------------
    def market(self, sid: str, tpe_date: str) -> str | None:
        """T 當日生效的 type（`twse`／`tpex`／`emerging`…）；不在快照回 None。T 早於首個轉換生效日＝最舊列的 type。"""
        seq = self.transitions.get(str(sid))
        if not seq:
            return None
        T = str(tpe_date)
        cur = seq[0][1]
        for eff, t in seq[1:]:
            if eff is not None and eff <= T:
                cur = t
            else:
                break
        return cur or None

    def listed(self, sid: str, tpe_date: str) -> str | None:
        """T 日在池的市場桶：`market()`∈{twse,tpex} 才回，否則 None（興櫃期／不在快照）。"""
        m = self.market(sid, tpe_date)
        return m if m in POOL_TYPES else None

    def listed_ids(self, tpe_date: str) -> frozenset[str]:
        return frozenset(sid for sid in self.static if self.listed(sid, tpe_date) is not None)

    def members(self, tpe_date: str, traded_sids: Iterable[str]) -> dict[str, dict]:
        """`{sid: meta}`（代號升冪）；meta＝靜態 meta ＋ `type`＝T 日市場。"""
        out: dict[str, dict] = {}
        for sid in sorted(str(s) for s in traded_sids):
            m = self.static.get(sid)
            if m is None:
                continue
            mk = self.listed(sid, tpe_date)
            if mk is None:
                continue
            out[sid] = {"type": mk, **m}
        return out

    # -- 報表 ------------------------------------------------------------
    def report_transitions(self) -> dict:
        """有轉換的代號與序列（供 Hetzner 報告與人工看異常）。`anomalies`＝轉換 >2 次、或同一 type 再度出現（來回）。"""
        trans = {sid: [list(x) for x in seq] for sid, seq in sorted(self.transitions.items()) if len(seq) > 1}
        anomalies: dict[str, str] = {}
        for sid, seq in trans.items():
            types = [t for _, t in seq]
            why = []
            if len(seq) - 1 > 2:
                why.append(f"轉換 {len(seq) - 1} 次")
            if len(set(types)) < len(types):
                why.append("來回（同一市場再度出現）")
            if why:
                anomalies[sid] = "；".join(why)
        return {"n_transitioned": len(trans), "transitions": trans, "anomalies": anomalies}
