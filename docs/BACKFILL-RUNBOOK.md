# P2 歷史回補 Runbook（在 Hetzner 執行）

依使用者 2026-09-09 裁定：**Claude 寫腳本、使用者在 Hetzner 執行，token 不離開 Hetzner**。
本檔只有步驟與預估；**不含任何 token 值**，也請不要把 token 貼進任何會 commit 的檔或對話。

程式：`scripts/backfill_hetzner.py`（CLI）＋ `src/iching/{config,fm,store,calendar,universe,plan,twse}.py`。
規格依據：`spec/P1-B3-replay.md` §B3.1 重播清單／§B3.2 SQLite／§B3.4 data_version、
`spec/P1-B1-market.md` §B1.9、`docs/P0A-report.md` §1（Hetzner 為 UTC、無 pyarrow、SQLite WAL 可用）。

## 0. 前提（Hetzner 現況，P0-A 實測）

- Python 3.14.4 系統層、無 venv；pandas 2.3.3／numpy 2.3.5／requests 2.32.5 已裝；**不需要 pyarrow**，本腳本只用標準庫＋requests（pandas 只在 report 可選）。
- 主機時區 **UTC**：腳本內所有日期都顯式轉 `Asia/Taipei`，不用改主機時區。
- 磁碟餘 27G；SQLite 估 1–3G（未量測，見 §7）。
- 記憶體 available 3.2 GiB：腳本每請求即落地、不整表載入；RSS 峰值 > 1.5 GiB 會自行中止。

## 1. 取得程式

```bash
cd /root/projects/taiwan-stock-iching   # 若尚未 clone：git clone git@github.com:shihpc/taiwan-stock-iching.git
git fetch --all
git checkout <主對話告知的分支或 commit>   # 驗收綁確切 commit；勿用「最新」
git pull --ff-only
```

## 2. 放 token（只做一次）

```bash
# repo 根目錄（.gitignore 已排除 .env／*.env）
printf 'FINMIND_TOKEN=%s\n' '<貼上你的 token>' > /root/projects/taiwan-stock-iching/.env
chmod 600 /root/projects/taiwan-stock-iching/.env
```

或改用環境變數 `export FINMIND_TOKEN=...`（環境變數優先於 `.env`）。腳本讀取後只放進
`Authorization: Bearer` header，**不進 URL、不進 log、不進例外訊息**（`src/iching/fm.py` 的 `redact()` 另做第二層遮蔽）。
`git status` 不應看到 `.env`；看到就停。

## 3. 先 `plan`（免 token、免網路）

```bash
python3 scripts/backfill_hetzner.py plan                       # core 群組（含 TWSE/TPEx 官方法人與成交金額）
python3 scripts/backfill_hetzner.py plan --group core optional  # 加 TaiwanStockPriceAdj 交叉驗證
```
群組只有 `core`／`optional`（`check` 由 `taiex-open-check` 專用）；打錯名字會 exit 2，不會靜默當 core。

本容器 2026-09-09 實跑（尚無交易日曆時以平日數 1,739 為上限）：

| 群組 | 請求 | 估時 | 備註 |
|---|---:|---:|---|
| core／FinMind | **7,144** | 約 1.4 小時（0.7 s 間隔） | 4 個全市場單日切片各 1,739（實際交易日約 1,620 會更少）＋指數／期貨／美股／匯率／總融資整年區間查詢 |
| core／TWSE+TPEx 官方（**必抓**：B1.5 法人 BFI82U＋TPEx summary 逐日 3,478 次、B1.3／B1.4 成交金額 FMTQIK＋tradingIndex 按月 160 次） | **3,638** | 約 4.0 小時（4 秒節流） | 不占 FinMind 額度；2026-09-09 驗收更正：P1-B1 明說官方法人是唯一合法口徑，不再是選配 |
| optional（`price_adj` 交叉驗證） | +2,149（不重複代號；universe.db 未落地前 `plan` 以裁定上限 3,060 估） | +0.4 小時 | 需 Sponsor；失敗不擋 |
| `taiex-open-check` 第二候選 KBar（§5） | 約 246／年 | 2022-01~2026-08 約 1,140 次 ≈ 13 分 | 權限層級未實測 |

對照 `spec/P1-B1-market.md` §B1.9 的 28,050 次：本計畫 FinMind 部分是它的 25%，差在指數／期貨／美股／匯率／總融資
改成整年一請求（帶 `data_id` 可一次取多年，`taiwan-backtest/scripts/fetch_taiex.py` 取過 18 年）而非逐日；
若全市場切片被判定需 per_stock 退回（每股 1 請求＝3,060），單一資料集就會多 3,060 次。
SponsorYear 6,000 次／小時（P0-A §2），額度不是瓶頸。

## 4. `run`（實抓，可中斷、可續跑）

建議順序（都可直接一次跑 `run`，腳本會自動先跑便宜的前置 `stock_info`／`index_price`）：

```bash
# 4.1 先落地股票池與指數（產生台北交易日曆；<1 分鐘）
python3 scripts/backfill_hetzner.py run --dataset stock_info index_price us_index fx_usd total_margin futures_inst futures_daily

# 4.2 看一眼日曆與池
python3 scripts/backfill_hetzner.py report | head -40
python3 scripts/backfill_hetzner.py plan          # 現在會用真實交易日曆算請求數

# 4.3a 放量前先試打一日（§7 #13：Sponsor 全市場切片對 2020 年歷史日期是否回全市場，家族前例最遠只到約 100 日曆天）
python3 scripts/backfill_hetzner.py run --dataset price_daily --limit 1 --from 2020-01-02 --to 2020-01-02
python3 scripts/backfill_hetzner.py report | grep price_daily     # rows 應近 2,000 檔上下；只有幾列或 0 → 停，回報
#     （--limit 1 只抓第一鍵；下一步同一 data_version 會跳過它、接著抓）

# 4.3 全市場切片（最久的一段；可分年跑，例：--from 2020-01-01 --to 2020-12-31）
python3 scripts/backfill_hetzner.py run --dataset price_daily inst_buysell margin short_sale_balance

# 4.4 其餘 core（除權息、月營收、財報、VIX，＋官方法人 BFI82U／TPEx summary 逐日、成交金額 FMTQIK／tradingIndex 按月；
#     官方端點 4 秒節流約 4 小時，可另開 tmux 視窗單獨跑：
#     python3 scripts/backfill_hetzner.py run --dataset twse_bfi82u tpex_inst_summary twse_fmtqik tpex_trading_index）
python3 scripts/backfill_hetzner.py run
#     TPEx 若出現 SSL 錯誤（taiwan-flows 有前例）才加 --tpex-no-verify（只對 tpex.org.tw 關閉驗證）。
#     ⚠ 關閉 TLS 驗證＝內容可被中間人替換：只在 TPEx 憑證鏈失敗時用，且該次落地的上櫃法人合計要與
#       FinMind 逐檔法人（raw_inst_buysell 加總）對照過才可採信。

# 4.5 選配
python3 scripts/backfill_hetzner.py run --group optional        # TaiwanStockPriceAdj 交叉驗證
```

行為要點：
- **換 `data_version`（或更新本腳本的表結構）前先刪舊 `cache/*.db`**：schema 不做遷移，`CREATE TABLE IF NOT EXISTS`
  不會改既有表的 PK／欄位；舊版本的列留在 raw 表會混進 report 的 rows 數。`rm cache/*.db cache/*.db-wal cache/*.db-shm`。
- **一次 run 一個 `data_version`**（預設 `fm-<台北今日>-01`；`--data-version fm-YYYYMMDD-xx` 覆寫）。
  跨日續跑請**明確帶同一個 `--data-version`**，否則隔天預設值會變、被視為新版本而整批重抓
  （§B3.4「歷史一律重抓」是以版本為單位）。
- 重跑同一指令會跳過已 `ok`／`empty` 的鍵；**失敗只進 `failures` 表、絕不寫進 coverage**，下次自動重抓。
  **全市場單日切片在（同一 `data_version` 的）交易日曆上卻回空**也算失敗（`failures.kind=empty_on_trading_day`）、
  不寫 coverage；只有非日曆型查詢（帶 `data_id` 的區間／逐股）的空回應才記 `empty`。
  官方端點同樣：連線例外／HTTP 非 200／非 JSON → 失敗；交易日曆日期回「無資料」（TWSE `stat` 非 OK 或 `data` 空／
  TPEx `tables` 空）→ `empty_on_trading_day`；按月的 FMTQIK／tradingIndex `stat` 非 OK 或 `data` 空 → `bad_stat` 失敗。
  **空回應只在資料集宣告的策略下才是合法 empty**（`config.DatasetSpec.empty_ok_for`，目前只有 `per_stock`）：
  指數／美股／匯率／總融資／期貨／全市場整年區間／`TaiwanStockInfo` 回 200 空陣列一律 `failures(empty_unexpected)`、
  不寫 coverage（否則同 dv 永不重抓）。完整的「策略 × 回應 → coverage／failures」期望表在 `tests/test_paths_matrix.py` 頂端。
- 混用策略（例如 `price_daily` 從 daily_slice 退回 per_stock）時，同一列會在兩個 coverage 鍵下各存一份
  （PK＝`(cov_key, row_hash)`），`report` 的 n_rows 必須等於底下實列數（§7 (c)）。
- 402／429 → 等 65 秒重試最多 8 次，仍失敗即中止（exit 3），稍後重跑同一指令續抓。
- 400「Your level…」＝需 Sponsor：記 `permission` 失敗；有 `fallback` 的資料集會自動改 `per_stock`（每股 1 請求）。
  不想自動退回加 `--no-fallback`；要指定策略用 `--strategy dividend_result=per_stock`。
- `--from/--to` 對**單日切片**（price_daily 等）就是日期範圍；對**區間型**資料集（指數／期貨／美股／匯率／月營收／財報）只是「選中哪些固定切塊（年／季／月）」、不改塊界——例如 `--from 2022-01-03 --to 2022-01-05` 會抓整個 2022 年的指數；`per_stock` 一律整段。這樣 coverage 鍵才穩定、不會與預設計畫的鍵重疊。
- 全市場切片需要**同一 `data_version`** 落地的台北交易日曆涵蓋請求區間（由 `index_price` 的 TAIEX 日期生成），不涵蓋會中止該資料集並提示。
- 交易日曆 JSON 只有涵蓋 `2020-01-01~2026-08-31` 全段（**每個月的日期數 ≥ 該月平日數 × 0.5**，整月缺或月內缺一半以上都不算；
  兩份日曆都只取本次 `data_version` 落地的列）才寫進 git 追蹤的 `data/calendar_*.json`；
  部分日曆一律寫 `cache/calendar_partial_*.json` 並 log 說明（`git status` 永遠不該因為半途的 run 出現 `data/calendar_*.json`）。
- Ctrl-C 安全：每個請求自成一個交易，中斷不留半套。
- 建議在 tmux 內跑並把輸出留檔：`... run 2>&1 | tee -a cache/logs/run-$(date -u +%Y%m%d).out`
  （`cache/logs/backfill-<data_version>.log` 也會自動寫）。

## 5. 裁定 4：大盤開盤價以證據定

```bash
python3 scripts/backfill_hetzner.py taiex-open-check                       # 預設 202201~202608
python3 scripts/backfill_hetzner.py taiex-open-check --month-from 202001   # 能多就多
```

抓證交所 `MI_5MINS_HIST`（按月，4 秒節流；56 個月約 4 分鐘）當**官方開盤**，與**兩個候選**逐日比對
（裁定 9：與官方一致者為準，皆不一致再回問；`docs/pre-registration.md` §1.2.3）：
①FinMind `TaiwanStockPrice/TAIEX` 的 `open`（宣告源）；②FinMind `TaiwanStockKBar/TAIEX` **09:00 分 K 的 `close`**
（`taiwan-backtest/scripts/fetch_taiex.py:56-63` 前例；逐日一請求，2022 全年約 246 次、2022-01~2026-08 約 1,140 次 ≈ 13 分；
**權限層級未實測**，P0-A 待驗證 4b 疑為 SponsorPro——若回 permission，報告只比對候選一並標明）。
輸出三欄（官方 open／FinMind open／KBar 09:00 close）、各候選一致率，並明講「一致的是哪一支」或「**皆不一致**」；
寫 `data/taiex_open_check.json`。試跑：`--kbar-limit 20`；不抓 KBar：`--no-kbar`。**本雲端容器被證交所擋是預期的**（P0-A §3），
只有在 Hetzner 跑才有結果。TWSE 該端點的欄位名與民國年日期格式沒實測過：程式找含「日期」「開盤」的欄、
接受 `111/01/03`／`2022/01/03`／`2022-01-03`，找不到會**明確報錯**（不會靜默回 0%）。

## 6. `report` 與產物

```bash
python3 scripts/backfill_hetzner.py report
```

| 產物 | 位置 | 進 git？ |
|---|---|---|
| `prices.db`／`chips.db`／`fundamentals.db`／`universe.db`／`market.db` | `/root/projects/taiwan-stock-iching/cache/` | **否**（`.gitignore`：`cache/`、`*.db*`） |
| log | `cache/logs/` | 否 |
| `data/calendar_tpe.json`／`data/calendar_us.json`（兩份交易日曆，P1-B3 §B3.1 #10） | `data/` | **是** |
| `data/taiex_open_check.json`（裁定 4 報告） | `data/` | **是** |
| `report` 的文字輸出 | 貼回對話／存 `runs/backfill/<data_version>-report.txt` | 是（若存檔） |

push 回 repo 的步驟（在 Hetzner）：

```bash
git status --short          # 只應看到 data/calendar_*.json、data/taiex_open_check.json（＋你存的 report）
git add data/calendar_tpe.json data/calendar_us.json data/taiex_open_check.json
git commit -m "data(p2): 歷史回補產物——兩份交易日曆＋TAIEX 開盤一致率（<data_version>）"
git fetch && git status     # CANON 第 7 條：遠端領先先看內容再 rebase
git push
```

`market.db` 內另有 `raw_twse_mi5mins_hist`（taiex-open-check 的原始月表）與 `sources` 表（每 dataset 的
抓取時間／請求數／筆數／日期範圍，§B3.4 第 4 點）。

## 7. 首次 run 要確認的清單（未實測／不確定）

以下在本雲端容器**沒辦法**或**沒有**實測，第一次在 Hetzner 跑完請對照 `report` 與 log 逐項確認：

| # | 項目 | 為什麼沒驗 | 怎麼確認 |
|---|---|---|---|
| 1 | `TaiwanOptionVix`：是否需要 `data_id`、欄位名（`src/iching/config.py` key `vix`） | 免 token 回 400 level；家族 repo 無呼叫 | run 後 `report` 該列 ok>0；若 failures 為非權限類 400，改試 `--strategy vix=per_id`（data_id 待查 FinMind 文件） |
| 2 | `TaiwanStockDividendResult` 以**整年區間**全市場查詢 | 家族只用過單日全市場（`taiwan-flow-live-v2/src/build_morning.py`） | 失敗會自動退回 per_stock（+3,060 次）；看 run 摘要有無「由 range_slice 退回」 |
| 3 | `TaiwanStockFinancialStatements` 全市場逐季區間查詢 | 只實測過帶 `data_id` 的單季 | 同上（退回 per_stock） |
| 4 | `TaiwanStockMonthRevenue` 歷史列是否含 `create_time`（B2.1 available_at 規則依賴） | 免 token 回 400 level | `sqlite3` 或 Python 看 `fundamentals.db` `raw_month_revenue` 欄位（`sources.columns`） |
| 5 | `TaiwanStockMarginPurchaseShortSale`／`TaiwanDailyShortSaleBalances` 完整欄位名 | 家族只用 `MarginPurchaseTodayBalance`／`SBLShortSalesCurrentDayBalance` | 動態建欄會全部落地；看 `sources.columns` |
| 6 | `TaiwanStockInfo` 個股池：**裁定寫 3,060 檔，本容器 2026-09-09 免 token 實打得符合條件的列數恰 3,060、但不重複代號 2,149**（835 檔多列，多為產業重分類／市場轉換殘留）——裁定數字疑為列數，**請確認以哪一個為準** | 免 token 拿到的是否為完整名單未驗 | `report` 的「個股池」列（含多列代號數）與 Sponsor token 結果對照 |
| 7 | 全市場單日切片在**交易日曆上卻回空**的日期 | 未實測 | log 會 WARNING；`report` 的 empty 欄；對照 TWSE 休市公告 |
| 8 | TWSE `MI_5MINS_HIST` 欄位名／日期格式／回應形狀（`stat`/`fields`/`data`） | 本容器被 WAF 擋 | `taiex-open-check` 若報 `TwseError` 把訊息貼回對話 |
| 9 | BFI82U／TPEx summary 在 2020 年初的可用性與回應形狀 | 同上（只落地原始 JSON，未解析） | `report` 的 `twse_bfi82u`／`tpex_inst_summary` 列；`raw_*` 的 `stat` 欄 |
| 10 | `WITHOUT ROWID`＋動態欄的實際磁碟量 | §B3.2 明寫「未量測前不視為已驗證」 | `du -sh cache/` 貼回 |
| 11 | ^SOX 與 ^GSPC 的美股交易日是否一致（us 曆取 ^GSPC） | 只抓過 3 天 | `report` 的「美股交易日曆」列差集數 |
| 12 | Python 3.14 下 `sqlite3` 與本腳本相容（本容器 3.11） | 無 3.14 環境 | 第一個 run 成功即證 |
| 13 | **(a) Sponsor 全市場單日切片對 2020–2025 歷史日期是否回全市場**——家族前例最遠只到約 100 日曆天 | 無 token | **先** `run --dataset price_daily --limit 1 --from 2020-01-02 --to 2020-01-02` 看列數（應近 2,000 檔上下），再放量 |
| 14 | **(b) `USStockPrice.date` 是美國當地交易日而非台北日**——整個上爻對齊（`calendar.us_session_closed_by`）建立在此 | 只抓過 3 天、未與美國交易所行事曆對照 | 抽 2022-07-04（美國國慶）／2022-11-25（感恩節翌日半日）等日期看 ^GSPC 有無列；週一台北日不得出現同日美股列 |
| 15 | **(c) 混用策略後 `report` 的 n_rows 要與 raw 實列數對**（PK 已改 `(cov_key,row_hash)`） | 修法只有離線測試 | `report` 各資料集 rows 欄 vs `SELECT cov_key, COUNT(*) FROM raw_<key> GROUP BY cov_key` 逐鍵相等 |
| 16 | `TaiwanStockKBar` TAIEX 的權限層級與欄位（minute/open/high/low/close/volume）、`end_date` 是否被尊重 | 免 token 未打；P0-A 4b 未驗 | `taiex-open-check --kbar-limit 5` 看 `market.db` `raw_taiex_kbar_0900` 有無列；permission 即需回問 |
| 17 | `FMTQIK`／TPEx `tradingIndex` 2020 年初回應形狀（`stat`／`tables`）與 TPEx TLS | taiwan-flows 只用近月 | `report` 兩列 ok≈80；`raw_*` 的 `stat`／`body` 前 200 字 |
| 18 | 日曆完整度門檻「每月日期數 ≥ 平日數 × 0.5」（`calendar.MONTH_DENSITY`）在真實假期下不誤判——春節月（2 月）台股約休 6~9 天、平日約 20 天 | 只以推算，未用真實 2020–2026 日曆驗過 | `report` 的「台北日曆缺口」列應為 0 個月；若春節月被列為缺口，把該月日期數貼回、再議門檻 |
| 19 | `report` 頂部「DB 內 data_version 數」應為 1 | — | >1 代表舊版本列混在 raw 表：清 `cache/*.db` 重跑 |

## 8. 不在本腳本範圍（與 `src/iching/config.py` 頂端 `OUT_OF_SCOPE` 逐項同步）

| 項目 | 本腳本 | 由誰負責 |
|---|---|---|
| B3.1 #6 事件版本鏈（events.db，as-of T） | 不負責 | P2 每日班的公告收集器（裁定乙：Worker→Actions；S1 §A4）；歷史公告無官方回補來源 |
| B2.5 集保週頻 `TaiwanStockHoldingSharesPer` | 不抓 | spec 明載首筆 2026-08-07、無歷史、不進共同核心分數（B1.9 表列）；每日班逐週落地 |
| B3.1 #5 PIT 池「T 日所屬市場」判定 | 只落地原料（`raw_stock_info` 含殘留列＋`raw_price_daily`）；`universe.pit_pool()`＝合格代號 ∩ 當日有列，**不分市場** | 後續 universe 模組以殘留列 `date` 重建轉換點（P0-A §4.4，誤差 1–2 日） |
| B3.1 #7／#8／#11 遲滯狀態、聚合中間結果、本管線歷史分數（scores.db） | 不負責（回測輸出） | P2 重播模組 |
| B3.1 #9 版本三元組（model_version／data_version／text_version） | 只產生並寫入 `data_version`（`fm-YYYYMMDD-<批次>`，進每筆 coverage 與原始列） | `model_version`／`text_version` 由計分（`scores.db`）模組綁定 |
| 流動性門檻（裁定 1）／還原係數（裁定 5）／報酬計算（裁定 3） | 不負責；只保證 `open` 與 `TaiwanStockDividendResult` 原始列落地 | 後續模組 |
| B1.5 官方法人、B1.3／B1.4 市場成交金額 | **已納入 core**（`twse_bfi82u`／`tpex_inst_summary`／`twse_fmtqik`／`tpex_trading_index`，原始 JSON 落地） | 解析交後續模組 |
