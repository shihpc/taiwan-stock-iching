"""P2 歷史回補的常數與資料集登錄表（唯一事實來源，`plan`／`run`／`report` 共用）。

依據：
- 切分／截止／暖機：使用者 2026-09-09 裁定（`docs/P2-KICKOFF.md` 之後的口頭裁定，本檔第 2 節）
  ＋ `spec/P1-B3-replay.md` §B3.0（價格類自 2020-01、基本面類自 2019-06 備妥）。
- 資料集清單：`spec/P1-B3-replay.md` §B3.1 重播清單 ＋ `spec/P1-B1-market.md` §B1.9
  ＋ `spec/P1-B2-params.md` 各節「來源」。
- 時區：Hetzner 為 UTC（`docs/P0A-report.md` §1.1），所有日期顯式 Asia/Taipei。

**「verified」欄是誠實標記**：free＝本容器 2026-09-09 免 token 實打成功；family＝家族 repo 生產管線
以 Sponsor token 在用（檔案:函式列於 note）；untested＝沒有任何實測，首次 run 要確認。
"""
from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass, field
from zoneinfo import ZoneInfo

TAIPEI = ZoneInfo("Asia/Taipei")

# ---------------------------------------------------------------------------
# 0. 規格覆蓋宣告（P1-B3 §B3.1 重播清單／B1／B2 提到、但**本腳本不負責**的項目；runbook §8 逐字同步）
# ---------------------------------------------------------------------------
OUT_OF_SCOPE: dict[str, str] = {
    "B3.1 #6 事件版本鏈（events.db，as-of T）":
        "本腳本不負責。由 P2 每日班的公告收集器（裁定乙：Worker→Actions；S1 §A4）落地；歷史公告無官方回補來源。",
    "B2.5 集保週頻 TaiwanStockHoldingSharesPer":
        "本腳本不抓。spec 明載首筆 2026-08-07、無歷史可回補、不進共同核心分數（P1-B1 §B1.9 表列）；由每日班逐週落地。",
    "B3.1 #5 PIT 池「T 日所屬市場」判定":
        "本腳本只落地原料：raw_stock_info（含殘留列）＋raw_price_daily（當日有價格列）。universe.pit_pool() 提供"
        "『合格代號 ∩ 當日有列』；**T 日屬 twse 或 tpex** 需以 TaiwanStockInfo 殘留列的 date 重建轉換點"
        "（P0-A §4.4，誤差 1–2 日），由後續 universe 模組負責。report 的每年 PIT 池只算檔數、不分市場。",
    "B3.1 #7／#8／#11 遲滯狀態、聚合中間結果、本管線歷史分數（scores.db）":
        "回測輸出，非原始資料；由 P2 重播模組負責。",
    "流動性門檻（裁定 1）／還原係數（裁定 5）／報酬計算（裁定 3）":
        "不在本腳本；本腳本只保證 open 與 TaiwanStockDividendResult 原始列落地。",
}

# ---------------------------------------------------------------------------
# 1. 日期常數（全部 ISO 字串，比較用字串序即可）
# ---------------------------------------------------------------------------
PRICE_WARMUP_START = "2020-01-01"   # 價格類暖機起點（B3.0：250 交易日 → 2020-01）
FUND_WARMUP_START = "2019-06-01"    # 基本面類暖機起點（B3.0：400 交易日 → 2019-06）
DATA_END = "2026-08-31"             # 資料截止（使用者 2026-09-09 裁定）

# 三段切分（使用者 2026-09-09 裁定）。暖機期 (< 2021-01-01) 不屬任何一段。
SEGMENTS: dict[str, tuple[str, str]] = {
    "train": ("2021-01-01", "2023-06-30"),
    "valid": ("2023-07-01", "2024-12-31"),
    "holdout": ("2025-01-01", DATA_END),
}
EVAL_START = SEGMENTS["train"][0]

# 進出場 h（本腳本不算報酬，只為 runbook／report 引用；`docs/pre-registration.md` §1.2.1）
HORIZONS_H = {"short": 10, "swing": 20, "mid": 40}

# 個股池規模（使用者 2026-09-09 裁定「現為 3,060 檔」）——只在 universe.db 尚未落地時供 plan 估算（上限）。
# ⚠ 2026-09-09 本容器實打 TaiwanStockInfo：符合條件的**列數**恰為 3,060，但**不重複代號 2,149**（見 universe.py
#   docstring）；裁定數字疑為列數，待使用者確認。落地後 plan 會改用 universe.db 的實際不重複代號數。
POOL_SIZE_RULING = 3060

# data_version 格式（P1-B3 §B3.4：fm-YYYYMMDD-<批次>）
DATA_VERSION_RE = re.compile(r"^fm-\d{8}-[0-9A-Za-z]{1,16}$")

# 記憶體上限（P1-B3 §B3.2：單一批次 1.5 GiB，超過即中止不得靜默 swap）
MEMORY_LIMIT_BYTES = int(1.5 * 1024 ** 3)

# §B1.9／§B3.5 的估算，供 plan 對照
B19_REQ_PER_DAY = 17
B19_TRADING_DAYS = 1650
B19_TOTAL = B19_REQ_PER_DAY * B19_TRADING_DAYS  # 28,050

# FinMind 額度（P0-A §2：SponsorYear 6,000 次／小時）→ 預設間隔 0.7 秒 ≈ 5,140 次／小時留餘裕
FINMIND_LIMIT_PER_HOUR = 6000
DEFAULT_INTERVAL_SEC = 0.7
# TWSE／TPEx 官方端點：taiwan-flows 經驗＝連打約 6 次即被 IP 限流且不自動解除 → 4 秒全域間隔
OFFICIAL_INTERVAL_SEC = 4.0


def taipei_now() -> dt.datetime:
    return dt.datetime.now(TAIPEI)


def taipei_today_str() -> str:
    """台北日（禁用裸 date.today()：Hetzner 主機時區為 UTC）。"""
    return taipei_now().date().isoformat()


def default_data_version(batch: str = "01") -> str:
    return f"fm-{taipei_now().strftime('%Y%m%d')}-{batch}"


def validate_data_version(v: str) -> str:
    if not DATA_VERSION_RE.match(v or ""):
        raise ValueError(f"data_version 格式須為 fm-YYYYMMDD-<批次>（P1-B3 §B3.4），得到 {v!r}")
    return v


def segment_of(date_iso: str) -> str | None:
    """回 'train'/'valid'/'holdout'；暖機期或截止後回 None。"""
    for name, (a, b) in SEGMENTS.items():
        if a <= date_iso <= b:
            return name
    return None


# ---------------------------------------------------------------------------
# 2. 資料集登錄表
# ---------------------------------------------------------------------------
# strategy：
#   daily_slice  每個台北交易日 1 次，不帶 data_id（start_date=end_date=d）——全市場切片
#   range_slice  不帶 data_id，依 chunk 切區間（year/quarter/month）
#   per_id       data_ids 逐一，依 chunk 切區間
#   per_stock    個股池逐檔（來自 universe.db 的 raw_stock_info），chunk=all
#   single       不帶日期，一次請求（TaiwanStockInfo）
#   official       TWSE／TPEx 官方端點，每個台北交易日 1 次（原始 JSON 落地）
#   official_month TWSE／TPEx 官方端點，每月 1 次（key=YYYYMM；FMTQIK／tradingIndex 整月日列）
# tier：free（免 token 可）／sponsor（Sponsor 級）／unknown
# group：core（預設 run）／optional（`--group optional` 才抓）／check（只由 taiex-open-check 使用，run 不抓）
# 2026-09-09 驗收更正：B1.5 官方法人（BFI82U／TPEx summary）是**唯一合法**法人口徑（P1-B1 明說不用 FinMind Total），
# 由選配改為 core；成交金額（FMTQIK／tradingIndex，B1.3／B1.4、B1.9「官方法人與成交金額」）一併納入 core。

@dataclass(frozen=True)
class DatasetSpec:
    key: str
    dataset: str
    db: str
    strategy: str
    start: str
    end: str = DATA_END
    data_ids: tuple[str, ...] = ()
    chunk: str = "year"
    group: str = "core"
    tier: str = "unknown"
    verified: str = "untested"
    note: str = ""
    fallback: str | None = None          # 權限不足時自動改用的 strategy
    alt_strategy: str | None = None      # plan 要並列計算請求數的替代策略
    depends: tuple[str, ...] = ()
    source: str = "finmind"              # finmind / twse / tpex
    index_cols: tuple[str, ...] = ("stock_id", "date")

    @property
    def table(self) -> str:
        return f"raw_{self.key}"


DATASETS: tuple[DatasetSpec, ...] = (
    # --- universe -----------------------------------------------------------
    DatasetSpec(
        key="stock_info", dataset="TaiwanStockInfo", db="universe", strategy="single",
        start=PRICE_WARMUP_START, tier="free",
        verified="family+P0A",
        note="P0-A §4.4 與 taiwan-stock-news build_pool_from_finmind() 在用；本容器 2026-09-09 實打撞 402（免 token 額度），"
             "欄位 industry_category/stock_id/stock_name/type/date 依家族用法（未在本容器親眼看到列）。"
             "point-in-time 池＝當日有價格列 ∩ 本表 4 碼普通股（type∈{twse,tpex}、非 00 開頭）。",
        index_cols=("stock_id",),
    ),
    # --- prices -------------------------------------------------------------
    DatasetSpec(
        key="index_price", dataset="TaiwanStockPrice", db="prices", strategy="per_id",
        start=PRICE_WARMUP_START, data_ids=("TAIEX", "TPEx"), chunk="year", tier="free",
        verified="free",
        note="2026-09-09 免 token 實打 2022-03-01~03 兩者皆 200；欄位 date/stock_id/Trading_Volume/Trading_money/"
             "open/max/min/close/spread/Trading_turnover。台北交易日曆由 TAIEX 有列的日期生成（calendar.py）。",
    ),
    DatasetSpec(
        key="price_daily", dataset="TaiwanStockPrice", db="prices", strategy="daily_slice",
        start=PRICE_WARMUP_START, tier="sponsor",
        verified="family",
        note="全市場單日切片：taiwan-flows src/pipeline.py 生產在用（SponsorYear）。本容器免 token 實打回 400"
             "「Your level is free」。每列含 open（T+1 開盤進場所需，裁定 3）。",
        fallback="per_stock", alt_strategy="per_stock", depends=("index_price", "stock_info"),
    ),
    DatasetSpec(
        key="dividend_result", dataset="TaiwanStockDividendResult", db="prices", strategy="range_slice",
        start=PRICE_WARMUP_START, chunk="year", tier="sponsor",
        verified="family(single-day)",
        note="裁定 5：落地原始列，還原係數由後續模組算。taiwan-flow-live-v2 src/build_morning.py 以全市場"
             "start_date=end_date=today 在用；**以年為區間的全市場查詢未實測**，失敗會自動退回 per_stock。"
             "欄位（使用者裁定所列）before_price/after_price/reference_price/stock_and_cache_dividend 未在本容器親眼看到。",
        fallback="per_stock", alt_strategy="per_stock", depends=("stock_info",),
    ),
    DatasetSpec(
        key="price_adj", dataset="TaiwanStockPriceAdj", db="prices", strategy="per_stock",
        start=PRICE_WARMUP_START, chunk="all", group="optional", tier="sponsor",
        verified="untested",
        note="裁定 5：只作交叉驗證、不依賴、失敗不擋。本容器免 token 實打回 400「Your level is free」。",
        depends=("stock_info",),
    ),
    # --- chips --------------------------------------------------------------
    DatasetSpec(
        key="inst_buysell", dataset="TaiwanStockInstitutionalInvestorsBuySell", db="chips",
        strategy="daily_slice", start=PRICE_WARMUP_START, tier="sponsor",
        verified="family",
        note="taiwan-flows src/pipeline.py 生產在用（長格式 date/stock_id/name/buy/sell，單位股）。",
        fallback="per_stock", alt_strategy="per_stock", depends=("index_price", "stock_info"),
    ),
    DatasetSpec(
        key="margin", dataset="TaiwanStockMarginPurchaseShortSale", db="chips",
        strategy="daily_slice", start=PRICE_WARMUP_START, tier="sponsor",
        verified="family",
        note="postmkt build_postmkt.py fetch_latest() 以全市場單日切片在用；家族只用到 MarginPurchaseTodayBalance，"
             "其餘欄位名未實測（動態建欄落地）。",
        fallback="per_stock", alt_strategy="per_stock", depends=("index_price", "stock_info"),
    ),
    DatasetSpec(
        key="short_sale_balance", dataset="TaiwanDailyShortSaleBalances", db="chips",
        strategy="daily_slice", start=PRICE_WARMUP_START, tier="sponsor",
        verified="family",
        note="postmkt build_postmkt.py fetch_latest() 在用；家族只用 SBLShortSalesCurrentDayBalance，其餘欄位未實測。",
        fallback="per_stock", alt_strategy="per_stock", depends=("index_price", "stock_info"),
    ),
    # --- market（指數以外的大盤／衍生品／外部；P1-B3 §B3.2 未指派檔名，本腳本新增 market.db）---
    DatasetSpec(
        key="total_margin", dataset="TaiwanStockTotalMarginPurchaseShortSale", db="market",
        strategy="range_slice", start=PRICE_WARMUP_START, chunk="year", tier="free",
        verified="free",
        note="2026-09-09 免 token 實打 2022-03-01~05 得 12 列；欄位 TodayBalance/YesBalance/buy/sell/Return/date/name。",
        index_cols=("date",),
    ),
    DatasetSpec(
        key="futures_inst", dataset="TaiwanFuturesInstitutionalInvestors", db="market",
        strategy="per_id", start=PRICE_WARMUP_START, data_ids=("TX",), chunk="year", tier="free",
        verified="free",
        note="2026-09-09 免 token 實打 200；欄位 futures_id/date/institutional_investors/long_*/short_*（未平倉）。",
        index_cols=("date",),
    ),
    DatasetSpec(
        key="futures_daily", dataset="TaiwanFuturesDaily", db="market",
        strategy="per_id", start=PRICE_WARMUP_START, data_ids=("TX",), chunk="year", tier="free",
        verified="free",
        note="2026-09-09 免 token 實打單日得 19 列（各 contract_date × trading_session）；近月判定由後續模組做。",
        index_cols=("date",),
    ),
    DatasetSpec(
        key="vix", dataset="TaiwanOptionVix", db="market",
        strategy="range_slice", start=PRICE_WARMUP_START, chunk="year", tier="sponsor",
        verified="untested",
        note="**未實測**：本容器免 token 回 400「Your level is free」；家族 repo 無任何呼叫。是否需要 data_id、欄位名皆未知。"
             "首次 run 若 400 非權限類，請改試 `--dataset vix --strategy per_id`（data_id 待查）。",
        index_cols=("date",),
    ),
    DatasetSpec(
        key="us_index", dataset="USStockPrice", db="market",
        strategy="per_id", start=PRICE_WARMUP_START, data_ids=("^GSPC", "^SOX"), chunk="year", tier="free",
        verified="free",
        note="2026-09-09 免 token 實打 200；欄位 date/stock_id/Adj_Close/Close/High/Low/Open/Volume。"
             "美股交易日曆由 ^GSPC 有列的日期生成（calendar.py）。",
    ),
    DatasetSpec(
        key="fx_usd", dataset="TaiwanExchangeRate", db="market",
        strategy="per_id", start=PRICE_WARMUP_START, data_ids=("USD",), chunk="year", tier="free",
        verified="free",
        note="2026-09-09 免 token 實打 200；欄位 date/currency/cash_buy/cash_sell/spot_buy/spot_sell（無 stock_id）。",
        index_cols=("date",),
    ),
    # --- fundamentals -------------------------------------------------------
    DatasetSpec(
        key="month_revenue", dataset="TaiwanStockMonthRevenue", db="fundamentals",
        strategy="range_slice", start=FUND_WARMUP_START, chunk="month", tier="sponsor",
        verified="family",
        note="postmkt src/build_diag.py 以全市場逐「公布月」區間查詢在用；欄位 revenue/revenue_year/revenue_month，"
             "`create_time` 是否存在於歷史列未實測（B2.1 available_at 規則依賴它；等於 2026-04-21 者為回填）。",
        fallback="per_stock", alt_strategy="per_stock", depends=("stock_info",),
    ),
    DatasetSpec(
        key="financial_statements", dataset="TaiwanStockFinancialStatements", db="fundamentals",
        strategy="range_slice", start=FUND_WARMUP_START, chunk="quarter", tier="unknown",
        verified="free(per-stock)",
        note="2026-09-09 免 token 實打 data_id=2330 單季 200（欄位 date/stock_id/type/value/origin_name，date＝期別末日）；"
             "**全市場逐季區間查詢未實測**，失敗自動退回 per_stock（3,060 次）。",
        fallback="per_stock", alt_strategy="per_stock", depends=("stock_info",),
    ),
    # --- official（B1.5 大盤法人口徑：TWSE BFI82U ＋ TPEx summary；原始 JSON 落地，解析交後續模組）---
    DatasetSpec(
        key="twse_bfi82u", dataset="https://www.twse.com.tw/rwd/zh/fund/BFI82U", db="market",
        strategy="official", start=PRICE_WARMUP_START, group="core", tier="free", source="twse",
        verified="family",
        note="B1.5 唯一合法法人口徑。taiwan-flows src/totals.py 在用（dayDate=YYYYMMDD&type=day&response=json）。"
             "本容器被 TWSE WAF 擋、Hetzner 可達（P0-A §3）。4 秒節流。",
        depends=("index_price",), index_cols=("date",),
    ),
    DatasetSpec(
        key="tpex_inst_summary", dataset="https://www.tpex.org.tw/www/zh-tw/insti/summary", db="market",
        strategy="official", start=PRICE_WARMUP_START, group="core", tier="free", source="tpex",
        verified="family",
        note="B1.5 唯一合法法人口徑（上櫃）。taiwan-flows src/totals.py 在用（type=Daily&date=YYYY/MM/DD&response=json）。"
             "taiwan-flows 註記 TPEx 部分端點 SSL 異常、必要時關閉驗證重試（本腳本預設驗證，`run --tpex-no-verify` 才關）。",
        depends=("index_price",), index_cols=("date",),
    ),
    DatasetSpec(
        key="twse_fmtqik", dataset="https://www.twse.com.tw/rwd/zh/afterTrading/FMTQIK", db="market",
        strategy="official_month", start=PRICE_WARMUP_START, group="core", tier="free", source="twse",
        verified="family",
        note="B1.3／B1.4 上市市場成交金額（＋發行量加權指數）。taiwan-flows src/totals.py fetch_fmtqik_month() 在用："
             "按月一請求（date=YYYYMM01&response=json），fields=[日期,成交股數,成交金額(元),成交筆數,發行量加權股價指數,漲跌點數]，"
             "民國年日期。原始 JSON 落地，解析交後續模組。",
        index_cols=("date",),
    ),
    DatasetSpec(
        key="tpex_trading_index", dataset="https://www.tpex.org.tw/www/zh-tw/afterTrading/tradingIndex", db="market",
        strategy="official_month", start=PRICE_WARMUP_START, group="core", tier="free", source="tpex",
        verified="family",
        note="B1.3／B1.4 上櫃市場成交金額。taiwan-flows src/totals.py fetch_otc_turnover_month() 在用："
             "date=YYYY/MM/01&response=json，回 {tables:[{data:[[民國日期,成交量(千股),成交金額(千元),筆數,指數,漲跌]]}]}。原始 JSON 落地。",
        index_cols=("date",),
    ),
    # --- check（只供 taiex-open-check 第二候選；run 不抓）---
    DatasetSpec(
        key="taiex_kbar_0900", dataset="TaiwanStockKBar", db="market",
        strategy="daily_slice", start=PRICE_WARMUP_START, group="check", tier="unknown",
        verified="family(taiwan-backtest)",
        note="裁定 9 第二候選：TAIEX 09:00 分 K 的 close（taiwan-backtest scripts/fetch_taiex.py:56-63 取法前例；"
             "`docs/pre-registration.md` §1.2.3）。逐日一請求（data_id=TAIEX, start_date=end_date=d）；"
             "**權限層級未實測**（P0-A 待驗證 4b：可能 SponsorPro）；欄位 minute/open/high/low/close 依前例，未在本容器親眼看到。"
             "只落地當日 09:00 那根 bar＋當日 bar 數。",
        depends=("index_price",), index_cols=("date",),
    ),
)

DATASET_BY_KEY: dict[str, DatasetSpec] = {d.key: d for d in DATASETS}
DB_FILES: tuple[str, ...] = ("prices", "chips", "fundamentals", "universe", "market")

# run 的預設順序：先 universe／指數（產交易日曆），再全市場切片
RUN_ORDER: tuple[str, ...] = tuple(
    [d.key for d in DATASETS if d.strategy in ("single", "per_id")]
    + [d.key for d in DATASETS if d.strategy not in ("single", "per_id")]
)

# 證交所指數日 OHLC（裁定 4，taiex-open-check 用）；按月，date=YYYYMM01
TWSE_MI5MINS_HIST = "https://www.twse.com.tw/rwd/zh/afterTrading/MI_5MINS_HIST"
# taiex-open-check：候選一致率 ≥ 此值視為「與官方一致」（可 --agree-threshold 覆寫）
OPEN_CHECK_AGREE_THRESHOLD = 0.99


def _check_registry() -> None:
    keys = [d.key for d in DATASETS]
    assert len(keys) == len(set(keys)), "資料集 key 重複"
    for d in DATASETS:
        assert d.db in DB_FILES, f"{d.key}: db {d.db} 不在 DB_FILES"
        assert d.strategy in ("daily_slice", "range_slice", "per_id", "per_stock", "single", "official", "official_month"), d.key
        assert d.group in ("core", "optional", "check"), d.key
        assert d.chunk in ("year", "quarter", "month", "all"), d.key
        for dep in d.depends:
            assert dep in DATASET_BY_KEY, f"{d.key} 依賴不存在的 {dep}"
        if d.strategy == "per_id":
            assert d.data_ids, f"{d.key}: per_id 需 data_ids"


_check_registry()
