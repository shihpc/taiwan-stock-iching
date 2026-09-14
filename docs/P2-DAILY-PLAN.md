# P2 每日班（Actions 每日計分）架構方案

2026-09-14 動手前寫（CANON 第 3 條）。狀態：**§5 五題已裁定（Q1 實測、Q2–Q5 全甲，2026-09-14，P2-KICKOFF §5 #39），D-1 已交付（`src/iching/collect.py`，見 §6 第 2 點）、D-2 待做**。設計依據：`spec/P1-B3-replay.md` §B3.1／§B3.2、
`docs/P2-KICKOFF.md` §5 #11／§7、第 13 項實跑結果（§5 #38）。實作細節以程式 docstring 為準，衝突時以本檔 §1–§3 為準。

## 0. 一句話

每日班＝**用 git 內最近 320 日的「原料包」重建視窗 ＋ 當日約 22 次 API 抓當日原料 → 同一個 `step(T)` → 當日分數與狀態進 git**。
Hetzner 回補層與每日班層吃的是同一種原料包、同一支 `step()`，parity 由構造保證（§B3.2 不變式）。

## 1. 已查證的約束

| # | 約束 | 出處 |
|---|---|---|
| 1 | 每日班在 Actions 只能靠「git 內狀態＋當日 API」算出當日分數；狀態要多小在設計時實算後定 | `spec/P1-B3-replay.md:69-72` |
| 2 | 每日 API 預算「分鐘級」（§B3.5 估 16–18 次；SponsorYear 6,000 次/小時） | `:149-158` |
| 3 | workflow 只開 `workflow_dispatch`、**無 cron**；`concurrency.group: iching-commit`＋`cancel-in-progress: false`；失敗走 `notify-failure`（pipeline `iching-daily`） | `P2-KICKOFF.md` §5 #3／#11、§3 #8；`.github/workflows/checks.yml:26-28` |
| 4 | 觸發端＝`taiwan-flow-live-v2` Worker 加一個 dispatch 角色（比照 `news`），**這是對既有 repo 的唯一解除**；要等本 repo workflow 存在、PR #5 合併後 | `P2-KICKOFF.md` §4、§7 |
| 5 | 完成定義 #5：連續 10 個交易日每班有 `runs/collect/<date>-<band>.json` | §3 #5 |
| 6 | `WindowCache` 的重建語意＝「每檔最近 320 個**有成交**列」；`DailyScanner` 內部只留 61 日；`AdvTracker`（60 日）已在 `CrossDayState` 內 | `replay_state.py`、`scan.py:380`、第 13 項 |
| 7 | 兩層必須同一 `data_version`，否則 7 鍵不同、parity 無從比 | `spec/dimensions.json` |
| 8 | 時區一律台北；禁 `date.today()`／裸 `datetime.now()`；美股窗用美股曆 | `P1-B3-replay.md:55`、§B3.1 #10 |

## 2. 三條路

| 路 | 做法 | 每日 git 增量 | 缺點 |
|---|---|---|---|
| A 視窗進 git | 把 `WindowCache`（numpy 55 MB）序列化 commit | 55 MB/日 | 一年 13 GB，不可行 |
| B 每日從 API 重建 | 320 日 × 8 個資料集逐日抓 | 0 | 2,500 次請求/日，違反約束 2 |
| **C 原料包進 git（提案）** | 每日只 commit **當日**原料包 `runs/collect/<T>-daily.json.gz`；視窗由最近 320 個原料包重建；初始 320 日由 Hetzner 一次性匯出 | **實測 99.3 KB/日**（§5 Q1） | git 一年約 25 MB；重建 320 日約 20–30 秒（推估） |

C 就是 §B3.2 說的「最小集合」：原料包＝`replay_state.DayBundle` 的 JSON（價量原始值、籌碼、官方金額、期貨、VIX、美股、匯率），
**不含**廣度／產業／P_cs（那三項由每日班自己用 `DailyScanner` 從原料包算，與第 12 項同一支程式）。

## 3. 每日班一次執行（`scripts/daily_run.py`，Actions 內跑）

```
1. 讀 data/calendar_*.json、data/pool.json、data/factors.json、CrossDayState(T−1)＝data/state/cross.json
2. 決定 T（台北時區；T 必須是 calendar_tpe 的下一個交易日且 > cross.last_date；否則 no-op 退出）
3. 抓當日原料（約 22 次；表見 §4）→ 組 DayBundle(T) → 完整性檢查（§5 Q3）→ 寫 runs/collect/<T>-daily.json.gz
4. 重建：讀最近 320 個原料包（含 T）→ WindowCache.ingest ×320；讀最近 61 個 → DailyScanner.push_day ×61 → features(T)
5. step(T) → data/scores/<T>.json（scores.db 同形的列）＋ CrossDayState(T) → data/state/cross.json
6. commit（concurrency 同組）；失敗 → notify-failure
```

- 第 4 步的重建每天從零做，**不存任何中間狀態**（除 `CrossDayState`），這是決定性與 parity 的保證；成本以 Hetzner 實測
  ingest 約 50 ms/日推估 320 日 ≈ 16 s，`step` ≈ 30 s，整班 2 分鐘級（Actions 免費額度無壓力）。
- `DailyScanner` 需 61 日 `StockDay`（`close_adj`／`amount`／industry）——由原料包＋`pool.json`＋`factors.json` 導出，
  `ad_line` 在 `WindowCache` 內從 0 起算（第 13 項已驗），61 日的 scanner 從零起算的 `advance/decline` 計數與全量跑相同。
- **parity 儀式**（每週或每次改參數）：Hetzner `backfill_hetzner.py run --from` 補新日 → `replay_scores.py --resume` →
  `scripts/export_bundles.py` 匯出同一段原料包與 `diff_scores.py` 比對 git 內的每日分數；原料包也逐位比對。

## 4. 當日 API 清單（推估 22 次；FinMind 21 ＋ 官方 4，其中月表每月只變一次）

| 來源 | 次數 | 對應 DayBundle |
|---|---|---|
| TaiwanStockInfo | 1 | pool（變動才更新 `data/pool.json`，§5 Q4） |
| TaiwanStockPrice TAIEX／TPEx（單日） | 2 | `index` |
| TaiwanStockPrice 全市場單日切片 | 1 | `stocks` 價量 |
| 法人／融資／借券／集保 單日切片 | 4 | `stocks` 籌碼與發行股數 |
| TaiwanStockDividendResult（當日） | 1 | `factors.json` 追加 |
| TotalMargin／FuturesInst／FuturesDaily／VIX／^GSPC／^SOX／USD | 7 | 對應欄 |
| MonthRevenue（最新月）／FinancialStatements（最新季） | 2 | `data/fundamentals/` 增量（§5 Q5） |
| BFI82U／TPEx summary（當日）；FMTQIK／tradingIndex（當月） | 4 | `official` |

## 5. 裁定（2026-09-14 使用者裁定 Q2–Q5 全甲；Q1 為實測）

| Q | 題目 | 甲 | 乙 | 我的建議 |
|---|---|---|---|---|
| 1 | **原料包大小與存法** | 先由 Hetzner 匯出一日實測（`scripts/export_bundles.py`，本批交付），>500 KB/日再談 `actions/cache` | 不實測、直接採 C | **甲，已實測（2026-09-14 Hetzner）：2026-08-31 一日 1,970 檔＋320 列美股＝99.3 KB**（首日含美股回補，常態日會略小）→ 種子 320 日約 31 MB、每年約 25 MB，路 C 成立 |
| 2 | **每日班的 `data_version`** | 沿用回補批號 `fm-20260911-01`（同一原料血統；重新回補才換號） | 每日各給新號 `fm-<T>-daily` | **甲**：乙會讓每日班與 Hetzner 的 7 鍵永不相同，parity 無從比；血統語意寫進 `docs/data-contract` |
| 3 | **觸發時點與完整性** | Worker 台北 22:30 單一班；資料未齊（任一核心資料集當日為空）→ 寫 `runs/collect/<T>-waiting.json`、rc=0 不計分，Worker 23:30 再叫一次 | 沿用哨兵法：Worker 逐一探測落地才 dispatch | **甲**：集保 21:00 後才更新，22:30 一班＋一次補叫最簡單；哨兵法要改更多 Worker 程式 |
| 4 | **股票池與還原係數的來源** | 每日班每天抓 TaiwanStockInfo／DividendResult，**變動才**改寫 `data/pool.json`／`data/factors.json`；Hetzner parity 時以 git 內這兩檔為準 | 每日班用當天 API 結果、不落檔 | **甲**：池與係數是兩層共同輸入，不落檔就無法重現 |
| 5 | **基本面** | git 保留 `data/fundamentals/`（月營收 15 個月、季報 6 期，池內全體，推估 <3 MB），每日 2 次 API 增量更新 | 每天抓全歷史 | **甲**。**保留期數字於 D-2a 更正為 24 個月／8 期**（單檔 `data/fundamentals.json`）：引擎最長回看 18 個月（`revenue_accel`＝3+3+12），15 不夠；見 §7.3 |

**不列為裁定、但要記錄**：①`runs/collect/<date>-<band>.json` 的 `band` 取 `daily`（本專案只有一班）；②`DATA_END`／日曆延伸：每日班自己
把 T 追加進 `data/calendar_*.json`（`write_calendars` 的 `full_end` 是回補用的常數，不動）；③`backfill_hetzner.py` 寫日曆時內容不變也改
`generated_at`（每次跑都製造假 diff，2026-09-14 實測），一併修成內容相同不寫檔。

## 6. 交付切分

1. **本批（動手前）**：本檔＋`scripts/export_bundles.py`（Hetzner：`ReplaySource.read_day` → `runs/collect/<T>-daily.json.gz`，
   序列化決定性、可讀回成 `DayBundle` 逐位相同；順便量大小）。
2. **D-1 已交付（2026-09-14）**：`src/iching/collect.py`（純函式：dict 列 → `DayBundle` 各欄）——**不是「與 `replay_io` 同語意的第二份實作」，
   而是 `replay_io.ReplaySource` 改成只跑 SQL、把 dict 列交給同一組 `collect.*`**（`_qd()` 選欄→dict），列→欄的語意全站只在 `collect` 定義一次，
   兩層 parity 由構造保證。`tests/test_collect.py`：單元（缺欄／None／重複列）＋列序無關（打亂 5 次逐位同）＋**全欄 `SELECT *` 餵 `collect`
   與 `ReplaySource.read_day` 序列化逐位相同（80 日）**。兩處**刻意的語意變更**（相對 13a 的 `replay_io`）：①法人同 (stock_id, name) 重送多列
   改「後者覆蓋」（原為相加；對齊 `score_io.load_stock_inst_net`，Store 以 row_hash 去重故實務上只在「同鍵不同內容」時有差）；
   ②輸出 dict 固定序（市場依 `MARKETS`、個股代號升冪、期貨合約升冪），原為 SQL 列序；
   ③（驗收補列）`index`／`stocks` 的數值欄一律經 `_opt()` 轉 `float`，原版直接放 SQLite 原值——raw 表動態欄無型別 affinity，FinMind 的
   `Trading_Volume`／`Trading_money` 原生是 int，故 Hetzner 真實資料**必然**觸發：分數不受影響（`WindowCache.ingest` 全走 `_f()`），但
   `bundle_io.dumps` 位元組會變（`1000` → `1000.0`），**D-1 前用 `export_bundles.py` 匯出的原料包不可再與新版逐位比對**（種子匯出一律用 D-1 後的程式）；
   ④（驗收補列）VIX 同 `time` 多筆改「取最後列」（原 `ORDER BY time DESC LIMIT 1` 的 tie 順序由 SQLite 決定、未定義），Store 以 row_hash 去重，
   只在「同 time 不同值」時有差。**四者對第 13 項 Hetzner 產出的影響須以 `--limit-days 20` 重跑＋`diff_scores.py` 對 `cache/scores.db`
   實證為零**——**已於 2026-09-14 Hetzner 實跑通過**（HEAD `f4796b2`，`replay_scores.py --out cache/scores-check.db --limit-days 20 --rebuild`
   → 20 日 105,888 列、6,052 ms/計分日、RSS 400 MiB；`diff_scores.py cache/scores.db cache/scores-check.db` → 共同列 105,888、不同 0、「逐位相同」。
   第一次嘗試因 `cd` 路徑錯導致 `git pull` 未執行、用舊程式自比，結果作廢；第二次確認 HEAD 後重跑才採計）。
   `src/iching/bundle_io.py`（讀寫原料包）已於前批交付。
3. D-2：`scripts/daily_run.py`（§3 流程）＋ `.github/workflows/daily.yml`＋ Hetzner 種子匯出（320 日）。
4. D-3：parity 儀式腳本與測試（合成 DB：Hetzner 路徑 vs 原料包路徑同一 T 逐位相同）。
5. Worker dispatch 角色（另 PR，需你核准）→ 連續 10 個交易日觀察（完成定義 #5）。

## 7. D-2 驗收條件與切分（2026-09-14 動手前寫，CANON 第 3 條）

### 7.0 盤點後對 §3 的兩處更正

1. **`DailyScanner` 不是「只餵 61 日」，而是餵每日班手上全部原料包（≥320 日）**。`scan.py:380` 的 deque maxlen＝
   `max(MA∪HL∪{ret+1})`＝61 是「每檔 61 個**有效收盤**」，不是 61 個交易日——停牌／零成交日不推 deque，只餵 61 日會讓有缺口的檔
   deque 比全量跑短、`ma_eligible`／`hl_eligible` 計數就不同。餵全部持有的原料包後，只剩「近 320 日內有效收盤 <61 的檔」這一類
   殘餘邊界（與 `WindowCache` ring 的同型邊界一樣），由 D-3 parity 儀式抓、不在 D-2 內解。
2. **每日班要對每個 ingest 的日子都算 features**（不只 T）：原料包刻意不帶廣度／產業／P_cs，`WindowCache` 市場 ring 的廣度欄
   靠 `DayBundle.breadth` 逐日填，只填 T 會讓視窗內其餘 319 日全 NaN。做法＝逐日 `DailyScanner.push_day` → 寫進 **`FeatureStore(":memory:")`**
   → 用同一支 `day_breadth／day_industry／day_p_cs` 讀回塞進該日 bundle 再 ingest——與 `scan_features.py`＋`replay_io.read_day` 走**同一條程式路徑**
   （含 sqlite 的型別／排序），不另寫一份 ScanDay→dict 轉換。`in_rank_pool` 只影響當日 `P_cs`（`scan.py:16`），早期日子用從第一個原料包
   起算的新 `AdvTracker`（前 60 日池為空、離 T 逾 250 日，不影響 T 的任何視窗）；**T 當日的池必須等於 `CrossDayState.adv.eligible()`**，
   兩者不等即中止（這是每日班內建的一致性斷言）。

### 7.1 檔案格式（全部 JSON、`sort_keys`、決定性；schema 欄位版本 1）

| 檔 | 內容 | 讀取端走的既有程式 |
|---|---|---|
| `data/pool.json` | `raw_stock_info` 的 5 欄列（`stock_id/type/industry_category/stock_name/date`）原樣 | `universe.pool_from_info(rows)`（＝`feed.load_pool` 的同一步） |
| `data/factors.json` | 每檔除權息事件 `[[ex_date, before_price, after_price], …]` | `adjust.cumulative_factors(Event…)`（＝`feed.load_factors` 的同一步，含同樣的去重／壞值規則） |
| `data/fundamentals.json` | `monthly{sid:[[y,m,v]]}`／`quarters{sid:[[period,type,value]]}`（只 `NEEDED_TYPES`）／`price_at_period_end{sid:{period:close}}`；保留期＝月營收 **24 個月**、季報 **8 期**（以每檔自己的最新月／期為基準；取代裁定 #39 Q5 的 15 個月／6 期，理由見 §7.3） | `fundamentals.build_stock`（＝`replay_io.load_fundamentals` 的同一步）；日曆＝`data/calendar_tpe.json`＋`extend_calendar` |
| `data/state/cross.json` | `CrossDayState.to_json()`（含 `meta.window`／`meta.params_sha`） | `replay_scores.py` 同一支 `load_state`／`check_snapshot_meta` |
| `data/scores/<T>.json` | `{schema, tpe_date, data_version, text_version, params_sha, rows:[{model_version, …flatten_row}], diag}`；rows 依 `(market, stock_id, horizon, model_version)` 排序 | 與 `scores.db` 的列同欄，`diff` 走 `ScoreStore.rows_for_day` 同一組鍵 |
| `runs/collect/<T>-daily.json.gz` | 原料包（已定，§6 第 1 點） | `bundle_io` |

### 7.2 切分與驗收

- **D-2a（離線核心，零網路）——已交付 2026-09-14，見 7.3**：`src/iching/run_common.py`（`TEXT_VERSION`／`build_params_payload`／`load_state`／`save_state`／
  `check_snapshot_meta` 從 `replay_scores.py` 搬出、該腳本改 import，行為不變）；`src/iching/daily_core.py`（讀上表各檔 → 依 §7.0 逐日重建
  → `step(T)` → 寫 `data/scores/<T>.json`＋`data/state/cross.json`）；`scripts/export_seed.py`（Hetzner：從 cache 匯出 pool／factors／
  fundamentals／state＋最近 K 份原料包，並檢查 `trading_dates()` 與 `data/calendar_tpe.json` 一致，不一致拒匯）。
  **驗收**：①合成 DB 上 `replay_scores.py` 全量（含 `--to k` 存快照、`--resume` 到底）得參考 `scores.db`；在第 k 日 `export_seed`，
  之後每一日只用 repo 內檔案跑 `daily_core`（狀態鏈由前一日每日班產出接續、不再碰 Hetzner 狀態），每日 rows 與 `ScoreStore.rows_for_day`
  **逐位相同**、`diag` 除 `elapsed_ms` 外相同；②pool／factors／fundamentals 三檔讀回與 `feed.load_pool`／`load_factors`／
  `load_fundamentals` 的產物相等；③T 當日池斷言（§7.0 第 2 點）在測試中至少觸發一次成功路徑與一次失敗路徑；④全套測試綠、改動檔 ruff 乾淨；
  ⑤fresh-context 驗收綁 commit。
- **D-2b（網路層）——已交付 2026-09-14，見 7.4.3；覆驗（綁 `c523531`）必修無、CI #74 綠**：`src/iching/daily_fetch.py`（`fm.FinMind.get`＋`twse.OfficialClient` 抓當日 §4 清單 → dict 列 → `collect.*` → 原料包；
  pool／factors／fundamentals 增量）、`scripts/daily_run.py`（決定 T、未齊→`<T>-waiting.json`、齊→D-2a 核心、日曆追加）、
  `.github/workflows/daily.yml`。測試以 mock `get` 餵 fixture。
- **D-2c（上線）**：使用者在 Hetzner 跑 `export_seed`、commit 種子；手動 `workflow_dispatch` 一次；`backfill_hetzner.py` 日曆 `generated_at` 假 diff 修正。

### 7.3 D-2a 交付紀錄（2026-09-14）

- `src/iching/run_common.py`：`TEXT_VERSION`／`build_params_payload`／`load_state`／`save_state`／`check_snapshot_meta`／`ReplayDriverError`
  自 `scripts/replay_scores.py` 搬出，該腳本改 import（行為不變，`tests/test_replay_scores.py` 10 例照過）。
- `src/iching/daily_core.py`：§7.1 五種檔的讀寫（`pool_payload`／`factors_from_rows`（逐字對齊 `feed.load_factors` 去重／壞值規則）／
  `prune_fundamentals`＋`bridge_from_payload`）、`rebuild_from_bundles`（§7.0：逐日 `DailyScanner` → `FeatureStore(":memory:")` →
  `day_*` 讀回塞 bundle → `ingest`；T 當日排名池 ≠ `CrossDayState.adv.eligible()` 即 `DailyCoreError`）、`run_offline(root, T)`
  （待計分日逐日：重建 ≤ 該日全部原料包 → `step` → `data/scores/<日>.json` → 覆寫 `data/state/cross.json`）。
- `scripts/export_seed.py`（Hetzner）：pool／factors／fundamentals（三段 SQL 與 `replay_io.load_fundamentals` 同）／狀態（補 `meta.data_version`）／
  原料包（起點＝`rebuild_start(last_date)` 與 last_date 往前 `window+ADV_WINDOW+1` 日取早者）；日曆 ≤ last_date 與 `trading_dates()`
  不一致拒匯；寫完讀回 pool／factors 與 `load_pool`／`load_factors` 不同即 rc=2。
- `tests/test_daily_core.py` 5 例（修正批加第 5 例，見驗收補列）：①種子三檔讀回＝feed loaders、基本面橋 `inputs_for` 全檔相等、種子原料包位元組＝`read_day`；
  ②**19 日每日班鏈**（第 60 日種子、之後每日只用 repo 檔＋當日原料包，狀態鏈自接）rows 經 `diff_scores.diff_day` 與參考
  `scores.db` **0 差異**、`day_diag` 八欄相等、終點狀態快照（meta 除外）逐位相同、再叫一次為 no-op；③排名池斷言失敗路徑
  （只留 20 份原料包→拒算、不落檔）＋「不是待計分日」＋ window 不符；④保留期不改引擎算式（`revenue_yoy_3m` 三組偏移、
  `revenue_is_12m_high`）。**合成 DB 只有 80 日，種子起點退到第一天**——「種子起點晚於全量起點」的 ring／US 序列 parity **只有間接推論**
  （`tests/test_bundle_io.py` 兩邊都從第一天 ingest；13a-3 的 `--resume` 走 `read_day` 不走原料包），真實資料上由 D-3 parity 儀式直接證明。
- **兩個留給 D-2b／2c 的約束**（本批發現、未實作）：①**原料包不可任意修剪**——`ReplaySource` 首次 `read_day` 的美股／匯率
  帶「≤該日最後 window 個日期」整段，之後只帶增量；種子的第一份原料包因此承載整段序列，刪掉它會讓 `WindowCache` 的美股／匯率
  ring 變短。修剪規則要嘛保留第一份、要嘛在新的最舊一份補回整段（D-2c 定）。②月營收保留 24 個月／季報 8 期是以「每檔自己的
  最新月／期」為基準，每日班增量更新後要再跑一次 `prune_fundamentals`，且 `price_at_period_end` 對新期別要由原料包算（同「全市場
  ≤P 最近交易日、該檔 close>0」規則，D-2b 實作）。

**驗收補列（2026-09-14，fresh-context 驗收綁 `84e6e88`，修正批見 §7.3 末）**：
- **保留期改裁定值**：裁定 #39 Q5 寫「月營收 15 個月、季報 6 期」，程式取 **24／8**。計算：月營收最長回看＝`revenue_accel`（window 3）
  的前一組 `revenue_yoy_3m(…, 3, 3)` 需 latest−3..−5 與去年同期 latest−15..−17 → 18 個月；`revenue_yoy` 3→15、`revenue_high_12m`→12；
  季報 `fundamentals_dict` 用 P／P−1／P−4 → 5 期。檔案基準是每檔**最新月／期**，as-of T 的 latest 常落後 1～2 個月／期，故 24 ≥ 18+2、8 ≥ 5+2。
  15／6 會讓 `revenue_accel` 無聲缺值——**這是裁定值的變更**，不是筆誤。
- **`P_cs` 對分數 diff 不可觀測**：`P_cs` 唯一消費者 `score/stock.py` 的 `overheated` 只寫進 `lr.meta`，不在 `scores_io.SCALAR_COLS`；
  驗收實測把排名池換成空集合、或把指數收盤全清掉，19 日鏈 diff 仍 0。因此補 `test_daily_features_equal_reference_features_db`：
  每日班逐日 features 與參考 `features.db` 用同一組 `day_*` 讀出逐位相同，並自證排名池清空後 P_cs 消失。
- **排名池斷言的兩個 tracker 餵料不對稱（已修）**：features 的 `P_cs` 池吃 `feed.day_records` 的成交值（有成交即收），`CrossDayState.adv`
  吃 `WindowCache.today_amounts`（所屬市場有指數列且 amount 非 None）——參考路徑本來就是兩個獨立 tracker。原版拿前者與快照比，
  某市場缺指數列（#38：真實 1,618 日為 0 日）或 amount 為 None 的日子會誤報「排名池不一致」而拒算。現改為兩個 tracker 各餵各的，
  斷言只比 `adv_score`（`daily_core.rebuild_from_bundles` docstring）。
- **中途失敗與 git 原子性**：`run_offline` 逐日「寫 scores → 覆寫 state」，某日 `step` 拋錯時前幾日已落地且互相一致；scores 寫成、state
  沒寫成 → 下次重算該日覆寫（決定性）→ 自癒。**D-2b 必須把 `data/scores/<T>.json`＋`data/state/cross.json`＋原料包放同一個 commit**。
- 其他：`_clean` 把 ±inf 寫 null 而 SQLite 存 inf（現行算式有界、無實際路徑產 inf，記一筆）；`export_fundamentals` 補表／欄守門、
  `sqlite3.Error` 納入 rc=2；`bridge_from_payload` 對 null 值＝SQL 端 `v is not None`；`write_json` 加 fsync。

## 7.4 D-2b 設計與驗收條件（2026-09-14 動手前寫）

### 7.4.0 盤點後確認的三件事

1. **美股／匯率 T 日列不進 T 日計分**：引擎上爻取「截至台北 T 08:00 已收盤的最近美股日」（`score/market.py` `us_asof` →
   `calendar.us_session_closed_by`，cutoff＝T−1 曆日），匯率走 `fx_asof_rule="us_asof"` 同一個日期。所以每日班 22:30 抓不到美股 T
   日收盤**不影響 T 的分數**；那些列會落在 T+1 的原料包。**代價：兩層的原料包不再逐檔位元組相同**（回補層 `_dated` 把 ≤T 的美股／
   匯率列放在 T 包），D-3 的原料包比對要改比「美股／匯率序列的聯集」，其餘欄位仍逐檔逐位比。
2. **除權息係數是前向累積**（`adjust.factor_at`＝最後一個 `ex_date ≤ date` 的累積值），T 日才加入的事件不改 T 之前列的係數，
   每日班在 T 追加事件與回補層事先知道該事件，ring 逐位相同。
3. **pool 每日刷新（裁定 Q4）的已知限制**：原料包只含抓取當時池內檔（D-1 語意）。新上市／新入池的檔在每日班只有入池後的列，
   回補層（全市場切片）有更早的列；該檔頭 320 日兩層可能不同。**D-3 parity 儀式對「入池未滿 320 日」的檔另列、不算差異**；
   若實際發生頻繁再改原料包語意（改存池候選全集）。

### 7.4.1 元件

- `src/iching/daily_fetch.py`：`Fetcher(fm, oc)`——`fm` 有 `get(dataset, **params) -> list[dict]`（`fm.FinMind`），`oc` 有
  `get(url, params) -> (status, body, text)`（`twse.OfficialClient`）；**只做「呼叫 → dict 列 → `collect.*`」**，不碰檔案。
  - `trading_days_since(last_date, upto)`：`TaiwanStockPrice data_id=TAIEX start=last_date+1 end=upto` → 升冪日期（1 次）。
  - `fetch_day(T, pool, last_us, last_fx) -> DayFetch(bundle, missing, warnings, counts, extras)`：§4 清單；`missing`＝核心資料集為空者
    （index 兩市場、stocks、inst、margin、shareholding、short_sale、total_margin、futures_daily、futures_inst、vix、官方法人兩市場、
    月表當日金額兩市場），美股／匯率為增量、不列核心；`extras`＝`dividend` 列（`T−7d..T`，keep-first 冪等）、`month_revenue` 列（**本公布月＋上一公布月兩個整月窗**，各 1 次）、
    `financial_statements` 列（**最近兩個季末日各 start=end=期末日**，只 `NEEDED_TYPES`；2026-09-14 實測全市場查詢視窗須對齊期別，見 §7.4.4）；`stock_info()` 另為獨立呼叫（先於 `fetch_day`）。官方參數建構器 `OFFICIAL_PARAMS` 搬到 `iching/twse.py`，
    `backfill_hetzner.py` 改 import（同一份）。
- `scripts/daily_run.py`：`--root`／`--date`（預設台北今日）／`--window`／`--max-days 5`（超過只跑前 N 日、其餘留下次；**2026-09-14 首次 dispatch 前是拒跑 rc 2，run #1 因此失敗、issue #10**）（`data_version` 取自狀態快照 `meta`，不另給）。流程：
  讀狀態 → `trading_days_since(last_date, T)` → 逐日：**先** `stock_info` → 更新 `data/pool.json`（內容變才寫；讓新入池檔當日即進原料包）
  → `fetch_day` → 有 `missing` 就寫 `runs/collect/<d>-waiting.json`（`{date, missing, at}`）並停止（rc 0，之後的日子不處理；
  **此時 pool.json 若有變已改寫、不回滾**）→ 寫原料包、刪 waiting → 更新
  `data/factors.json`（新 (stock_id,date) 追加；既有列不動＝keep-first）、`data/fundamentals.json`（(sid,y,m)／(sid,period,type)
  後者覆蓋、缺期末收盤的 **(檔, 期別)** 由原料包算：全市場 ≤P 最近原料包日、該檔 close>0；再 `prune_fundamentals`）、
  `data/calendar_tpe.json` 追加 d、`data/calendar_us.json` 追加新美股日 → `daily_core.run_offline(root, d)`。任一步例外 rc 2。
  **同一次執行內全部檔案由 workflow 一個 commit 收**（§7.3 原子性）。
- `.github/workflows/daily.yml`：只 `workflow_dispatch`（input `date` 可選）、`concurrency.group: iching-commit`／`cancel-in-progress: false`、
  `permissions: contents: write, issues: write`、Python 3.12、`pip install -r requirements-dev.txt`、`FINMIND_TOKEN` 走 secret →
  `python scripts/daily_run.py` → `git add data runs/collect`＋commit＋`pull --rebase`＋push（重試 3 次）→ `notify-failure`（`iching-daily`）。

### 7.4.2 驗收

①**端到端 parity**：測試以合成 SQLite 當假 FinMind／假官方端點（`get(dataset, **params)` 從 `raw_*` 表依 `data_id`／日期區間取列、
官方 `get(url, params)` 依日期／月份回存好的 body），第 k 日種子後逐日跑 `daily_run.main(["--date", d])`：原料包位元組＝
`ReplaySource.read_day(d)`（合成 DB 美股列 ≤T 皆在，故可逐位比）、`data/scores/<d>.json` 經 `diff_scores.diff_day` 與參考 0 差異、
狀態鏈自接；②**waiting 路徑**：抽掉某日 VIX → 寫 `<d>-waiting.json`、rc 0、無原料包／分數／狀態變動；補回後再跑 → 正常且 waiting 檔被刪；
③**補跑**：狀態落後 2 個交易日時一次跑完兩日、順序正確；超過 `--max-days` rc 2；④**三檔增量**：pool 內容不變不改寫（mtime／位元組不變）、
新除權息事件追加後 `factors_from_rows` 與參考相等、月營收／季報增量合併後 `bridge.inputs_for` 與參考相等；⑤日曆兩檔正確追加；
⑥`daily.yml` 以 `yaml.safe_load` 檢查上述欄位；⑦全套綠、改動檔 ruff 乾淨；⑧fresh-context 驗收綁 commit。

### 7.4.3 D-2b 交付紀錄（2026-09-14）

- `src/iching/twse.py`：`OFFICIAL_PARAMS` 由 `scripts/backfill_hetzner.py` 搬入（該腳本改 `T.OFFICIAL_PARAMS`），兩層同一份。
- `src/iching/daily_fetch.py`：`Fetcher(fm, oc, required=CORE_REQUIRED)`——`trading_days_since`（TAIEX 1 次）、`stock_info`、
  `fetch_day(T, pool, last_us, last_fx)`（§4 清單；月表同一次執行內快取；核心資料集為空→`missing`；美股／匯率只帶 `(last, T]`；
  extras＝除權息當日、月營收 45 日窗、季報 120 日窗只 `NEEDED_TYPES`）。資料集名全取 `config.DATASETS`。
- `src/iching/daily_pipeline.py`：`run_pipeline`（補跑上限 `max_days`；未齊寫 `<d>-waiting.json` 停止）、`update_pool`（內容不變不寫）、
  `update_factors`（新 (sid,date) 追加＝keep-first）、`update_fundamentals`（後者覆蓋、新期別期末收盤由原料包算、`prune`）、
  `append_calendar`（tpe 追加 d、us 追加新美股日）、`last_dated`（美股／匯率游標＝往回找最近一份有列的原料包）。
- `scripts/daily_run.py`：CLI（`--root/--date/--window/--max-days/--env-file/--tpex-no-verify/--no-fundamentals`），rc 0／2。
- `.github/workflows/daily.yml`：只 `workflow_dispatch`（input `date`）、`iching-commit` 同組不取消、`contents: write`、
  `FINMIND_TOKEN` secret、產出一個 commit（`git add data runs/collect`＋`pull --rebase`＋push 重試 3 次）、`notify-failure iching-daily`。
- `tests/test_daily_run.py` 6 例（修正批後）：假 FinMind／假官方端點＝合成 SQLite（`raw_*` 依 data_id／日期區間取列、官方 body 依日期／月份）。
  ①第 60 日種子（並從種子拿掉 K+2 的除息列與 2330 的一筆月營收，模擬匯出時未知）後：先 `--date K+2` 一次補兩日、再逐日到終點，
  **原料包位元組＝`ReplaySource.read_day`、分數經 `diff_scores.diff_day` 0 差異、終點狀態逐位相同**；pool 不變位元組不變、
  除權息追加後＝`load_factors`、基本面合併後 `inputs_for` 全檔相等、兩份日曆正確、重跑同日 no-op 不改檔；②抽掉 VIX → waiting 檔、rc 0、
  無原料包／分數／狀態變動，補回後正常且 waiting 刪除；③3 個待補日 > `--max-days 1` → rc 2 不寫檔；pool 多一檔 → 改寫且入池；
  ④`daily.yml` 以 `yaml.safe_load` 驗欄位。
- **未做（D-2c）**：Hetzner 種子匯出＋commit、首次手動 dispatch、原料包修剪規則（§7.3 約束①）、`backfill_hetzner.py` 日曆假 diff。
- **未驗證（只能上線觀察；2026-09-14 驗收補列）**：①FinMind 各資料集 22:30 的落地時點（Q3 甲的 23:30 補叫是保險）；②【**已於 §7.4.4 2026-09-14 實測解答：支援，視窗須對齊期別**】季報
  `TaiwanStockFinancialStatements` **全市場無 `data_id` 區間查詢是否被支援**（`config.py` note 明寫未實測；回補層有 per_stock
  fallback、每日班沒有——400 會 rc 2 看得到，200 空陣列則只在 `counts.financial_statements=0` 看得到，**首跑要看這個數字**）與回應大小；
  ③除息列 `TaiwanStockDividendResult` 的落地時點（已改回看 7 日、keep-first 冪等）；④美股 T−1 列 22:30 是否已到（不列核心；
  `warnings` 出現 `us:lag` 要看——美國假日也會觸發，只警示不擋；若真未到，T 以 T−2 美股計分且狀態推進，對回補層永久分歧）；
  ⑤平日 `trading_days_since` 為空＝「假日」或「TAIEX 未落地」不可區分（log 標 `weekday_no_taiex`，靠 23:30 補叫／隔日 catch-up 自癒）；
  ⑥合成 DB 月營收 `date` 已改「公布月 1 日」（真語意），45 日窗在此語意下驗過；⑦上游截斷守門＝`price_min_rows`（`config.PRICE_DAILY_MIN_ROWS`
  =1500）與池覆蓋 ≥50%，門檻是否合適要看首跑 `counts.price`。
- **驗收修正（綁 `a3b7543` 的 fresh-context 驗收，4 項必修）**：①`requirements-dev.txt` 補 `pyyaml`（CI run #73 整套沒跑，issue #8）；
  ②`update_fundamentals` 的期末收盤改以 **(檔, 期別)** 計缺——原以期別計，同期別 A 先申報後 B 申報者永久缺 `price_at_period_end`
  （`eps_diff_over_price` 會與回補層分歧），補 `test_price_at_period_end_filled_per_stock_period`；③每日班加上游截斷守門（同回補層
  2026-09-10 事故），補 `test_truncation_guard_writes_waiting`；④文件三處「未齊時只有 waiting 檔」改正（pool.json 可能已改寫）。
  另採：除息回看 7 日、`us:lag` 警示、`counts`／`warnings` 進 log 與 summary、`daily.yml` commit 訊息依 staged 檔判定（waiting 標明、
  `pipefail` 下無命中不炸）、`inputs.date` 走 `env`。

### 7.4.4 首跑觀察（2026-09-14，main `d43c6a4`，種子 last_date 2026-08-31）

- **離線實測（本容器，拉下種子後）**：`rebuild_from_bundles` 讀 1,618 份原料包 **131 s（81 ms/份）、RSS 877 MiB**；T 日 `adv_score` 排名池
  895 檔＝`CrossDayState.adv` 895 檔（斷言會過）。單日估 ≈ 131 s ＋ `step` ≈ 30 s ＋ 抓取 ≈ 20 s ≈ **3 分鐘／日**，9 日補跑約 27 分鐘、
  貼近 40 分鐘上限——**原料包修剪（D-2c）不是可選項**：留 400 日可降到 ≈ 35 s/日。
- **run #1**（`max_days=1`，待補 9 日）：拒跑 rc 2（issue #10，已關）。教訓：`max_days` 應「只跑前 N 日」，已改（`13f3db4`）。
- **run #2**（`date=2026-09-01`）：FinMind 約 10 次呼叫成功後，TPEx `insti/summary` **TLS 驗證失敗**（runner CA 缺中繼憑證；issue #11）。
  修法＝`daily.yml` 帶 `--tpex-no-verify`（回補層同一處理）。**尚未看到**任何一天完整跑完的 `原始列數`／`警示`。
- **run #3（`date=2026-09-01`，帶 `--tpex-no-verify`）：首次跑通**——每日班 step 3 分 05 秒（`step` 11.9 s），21 次呼叫，原料包 93.3 KB
  （1,970 檔），分數 5,835 列（6 市場＋1,943 檔×3）、排名池 895、`n_in_pool` 891、個股任一爻未知 74 檔，commit `1eb2284`
  `daily: 2026-09-01`（7 檔：原料包／scores／state／pool／factors／兩份日曆）。`警示 無`（美股 09-01 列已到）。原始列數：
  `price 45,050（未濾池的全市場切片）、inst 123,279、margin 2,217、shareholding 2,371、short_sale 2,232、futures_daily 24、us 2、
  dividend 25（7 日窗）`。**兩個問題**：
  ① **`month_revenue 0`、`financial_statements 0`**——§7.4.3 預警②成真的可能性很高：全市場不帶 `data_id` 的區間查詢對這兩個資料集
  很可能回 200 空陣列。**尚未證實**（可能是查詢形狀、也可能是 token 等級），要在 Hetzner 用同一支 client 對照「不帶 data_id 區間」
  vs「帶 data_id」兩種查詢。在解決前**不補 09-10（8 月營收公布日）以後的日子**，否則每日班的月營收會落後回補層、parity 破。
  ② **`pool.json` 每日假改寫**：TaiwanStockInfo 的 `date` 欄＝抓取日，3,313 列只因 09-11→09-14 全改寫，池成員零變動。已改為
  「導出的池（成員／type／industry_category／stock_name）變才改寫」（`daily_pipeline.update_pool`，`POOL_VOLATILE_KEYS`）。
  另：`fundamentals` 的 `px_pairs 372`／`new_periods 28` 是種子裡 2020 年前期別本來就無價（原料自 2020-01 起）、每日白讀 28 份包，
  成本 <1 s，暫不處理。
- **原料包修剪（已實作，`daily_pipeline.prune_bundles`，每次成功執行末尾）**：留最近 `BUNDLE_KEEP=480` 個交易日；被刪包的美股／匯率
  序列併進新最舊那一份（最後 `window` 個日期）再改寫，`WindowCache` 兩條 ring 與未修剪逐位相同（`test_prune_bundles_keeps_window_rings_identical`）。
  代價：那一份與回補層 `read_day` 位元組不同（D-3 比對對它只比美股／匯率以外的欄）；停牌逾 160 個交易日的檔 ring 可能比回補層短
  （§7.0 第 1 點同型邊界，D-3 另列）。首次生效會刪約 1,150 份（種子 1,618＋補跑日 − 480；最舊留到 2024-09 上旬），重建由 131 s 降到約 40 s。**中斷自癒**：先改寫新最舊包、
  再逐一刪舊包；若刪到一半中斷，殘留舊包在新最舊包之前，下次重建會以「美股序列日期倒退」大聲失敗，重跑一次 `prune_bundles` 即自癒
  （Actions 上 step 失敗不 commit、工作副本丟棄，不會汙染 main）。
- **run #4（`date=2026-09-09`，`max_days=6`）：補 09-02／03／04／07／08／09 六個交易日一次成功**——19 分 33 秒（≈3.25 分／日）、
  123 次呼叫（每日 19～21）、原料包各 93～96 KB、分數每日 5,820～5,883 列、排名池 891→874（逐日縮）、個股任一爻未知 75～77 檔、
  市場列未知 0；除權息 +42（09-08／09 各 20 餘，除息旺季）；`pool不變`（run #3 已把 `date` 寫成 09-14，同日再抓不變——`update_pool`
  簽章化修正在分支 `f3cc8cf`，尚未上 main）。commit `caae76f`（16 檔）。`month_revenue`／`financial_statements` **六日皆 0**，
  待 Hetzner 對照查詢形狀。**09-10 起暫停補跑**（8 月營收公布日）。
- **基本面查詢形狀（2026-09-14 Hetzner 對照，同一支 `fm.FinMind`）——問題解決**：`TaiwanStockMonthRevenue` `08-01～08-31` → 2,339 列
  （`date=2026-08-01`＝7 月營收公布月）、`07-18～09-01` → **0**；`TaiwanStockFinancialStatements` `06-30～06-30` → 38,691 列、
  `05-04～09-01` → **0**；帶 `data_id=2330` 的跨月／跨季區間正常（月營收 2 列 `08-01`／`09-01`，季報 17 列 `06-30`）。結論：
  全市場查詢**支援但視窗必須對齊期別邊界**（整月／期末日）——**這是推測**：4 筆觀測同樣符合「只回 `date == start_date` 的列」
  這個替代假說，兩者下新形狀都成立；回補層的「月首～月末」「季首～季末」正是如此。**首跑觀察點**：本月窗 `end_date` 在未來（如 T=09-14 查
  09-01～09-30）Hetzner 未實打，看 `counts.month_revenue` 應≈2×2,339、`financial_statements`≈2×38,691。**已知漏網**：遲交逾一季的列
  （如年報 7/1 後才補申報）每日班永久漏、與回補層分歧，列 D-3 已知邊界。每日班改為
  `month_windows(T, 2)`＋`quarter_ends(T, 2)` 共 4 次呼叫（`daily_fetch.py` 常數區塊註解），`test_fundamentals_query_windows_are_period_aligned`
  守查詢形狀。另：2330 已有 `date=2026-09-01`（8 月營收）→ 09-10 起的日子補跑時會用到，parity 無虞。
- **run #5（`date=2026-09-12`，`max_days=3`；main `ed3a048`＝PR #13 合併後）：補 09-10／09-11（09-12 為週六）成功**——4 分 46 秒
  兩日、每日 21～23 次呼叫。**基本面查詢對齊期別後有列**：`month_revenue 4,667`（≈2×2,339，本月＋上月整月窗）、`financial_statements 69,552`
  （03-31＋06-30 兩個季末日）；`data/fundamentals.json` 由此新增 2026-08 月營收 1,964 檔（`monthly_rows 3,940`／`quarter_rows 15,616`
  合併、`changed 1`），個股任一爻未知由 77 降到 70（8 月營收可得）。`警示 無`。**首次修剪**：刪 1,147 份、留 480（最舊 2024-09-20），
  commit `b8e4f69`（1,157 檔：1,147 刪／2 新／1 改寫＝新最舊包併入美股／匯率序列）；修剪後 `step` 7.4 s、每日整體約 2.4 分。
  pool 簽章化後 `pool不變`。**至此 09-01～09-11 共 8 個交易日由每日班產出並進 main；完成定義 #5（連續 10 個交易日）的計數從 09-01 起算。**

## 7.5 Worker dispatch 角色（另案 PR，2026-09-14 使用者裁定「開」；動手前寫）

**目標**：`taiwan-flow-live-v2` 的 Cloudflare Worker 加 scheduled 角色 `iching`——**台北 22:30 與 23:30、週一～五**各 dispatch 一次
`shihpc/taiwan-stock-iching` 的 `daily.yml`（`workflow_dispatch`、`ref: main`、inputs 空＝T 為台北今日、`max_days` 預設）。
23:30 那班不看 22:30 的結果（`daily_run` 冪等：已完成→`trading_days_since` 為空 no-op；未齊→waiting 後再試；週末／假日→no-op），
比裁定 Q3 甲「未齊才補叫」更簡單、少一個狀態。

**驗收條件**：
1. 比照 `news` 角色：`wrangler.toml` 新增 cron（UTC 14:30／15:30 週一～五，dow 依 Quartz 慣例）、`scheduledRole` 分流、
   `dispatchIching`（secret 缺失走 `alertSecretMissing`、KV 去重 `iching:<YYYYMMDD>:<HHMM>` 當班一次、dispatch 失敗走 `alertJob`）。
2. `worker/test/` 新增測試：分流（22:30／23:30 週一～五回 `iching`、週末與其他時刻不回）、dispatch 請求形狀（URL＝
   `/repos/shihpc/taiwan-stock-iching/actions/workflows/daily.yml/dispatches`、body `{ref:"main"}`）、KV 去重、secret 缺失有／無通道。
   `node test/<新檔>.mjs` 綠；`worker-deploy.yml` 以 glob 跑全部測試（新檔自動納入）。
3. 既有角色零改動（`news`／`sentinel`／`evening`／`health`／`morning`／`frame` 的測試全綠）；`/status` 不動。
4. **前置（使用者）**：`GH_DISPATCH_TOKEN`（fine-grained PAT）的 repository access 必須含 `taiwan-stock-iching`（Actions: write），
   否則 dispatch 回 404／403 → `alertJob`。PAT 在 GitHub 端改 access 不需重新 `wrangler secret put`。**本 session 無法驗證，
   上線首晚看 `npx wrangler tail` 或 taiwan-stock-iching 的 Actions 頁有沒有 `workflow_dispatch` run。**
5. 文件：live-v2 `CLAUDE.md`「其他 scheduled 角色」加 `iching` 一行；`PROJECT_SUMMARY.md` 快速接手段加一句；本檔 §7.5 記交付；
   `docs/schedule-map.md`（claude-harness）另案同步。
6. fresh-context 驗收綁 commit；PR 由使用者 merge；push 到 main 觸發 `worker-deploy.yml` 自動部署；當晚觀察。
