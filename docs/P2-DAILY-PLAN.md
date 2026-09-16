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

## 4. 當日 API 清單（推估 22 次；FinMind 21 ＋ 官方 4，其中月表每月只變一次）【2026-09-15 起除權息改 8 次 → FinMind 28、合計 29，實測 run 記的 21／23 次呼叫為改前數字】

| 來源 | 次數 | 對應 DayBundle |
|---|---|---|
| TaiwanStockInfo | 1 | pool（變動才更新 `data/pool.json`，§5 Q4） |
| TaiwanStockPrice TAIEX／TPEx（單日） | 2 | `index` |
| TaiwanStockPrice 全市場單日切片 | 1 | `stocks` 價量 |
| 法人／融資／借券／集保 單日切片 | 4 | `stocks` 籌碼與發行股數 |
| TaiwanStockDividendResult（`[T−7, T]` **逐日單日切片**，2026-09-15 起；原 1 次區間查詢只回 start_date 當天，見 §7.6.3「第一輪對帳根因」） | 8 | `factors.json` 追加 |
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
  09-01～09-30）Hetzner 未實打，看 `counts.month_revenue` 應≈2×2,339、`financial_statements`≈2×38,691。【run #5 實測 `month_revenue 4,667`＝回列；2026-09-15 起本月窗改 `[月首, T]`，與回補層同形，見 §7.6.3「第一輪對帳分析：月營收查詢窗」】**已知漏網**：遲交逾一季的列
  （如年報 7/1 後才補申報）每日班永久漏、與回補層分歧，列 D-3 已知邊界。每日班改為
  `month_windows(T, 2)`＋`quarter_ends(T, 2)` 共 4 次呼叫（`daily_fetch.py` 常數區塊註解），`test_fundamentals_query_windows_are_period_aligned`
  守查詢形狀。另：2330 已有 `date=2026-09-01`（8 月營收）→ 09-10 起的日子補跑時會用到，parity 無虞。
- **run #5（`date=2026-09-12`，`max_days=3`；main `ed3a048`＝PR #13 合併後）：補 09-10／09-11（09-12 為週六）成功**——4 分 46 秒
  兩日、每日 21～23 次呼叫。**基本面查詢對齊期別後有列**：`month_revenue 4,667`（≈2×2,339，本月＋上月整月窗）、`financial_statements 69,552`
  （03-31＋06-30 兩個季末日）；`data/fundamentals.json` 由此新增 2026-08 月營收 1,964 檔（`monthly_rows 3,940`／`quarter_rows 15,616`
  合併、`changed 1`），個股任一爻未知由 77 降到 70（8 月營收可得）。`警示 無`。**首次修剪**：刪 1,147 份、留 480（最舊 2024-09-20），
  commit `b8e4f69`（1,157 檔：1,147 刪／2 新／1 改寫＝新最舊包併入美股／匯率序列）；修剪後 `step` 7.4 s、每日整體約 2.4 分。
  pool 簽章化後 `pool不變`。**至此 09-01～09-11 共 8 個交易日由每日班產出並進 main。**（完成定義 #5 的起算日後經使用者裁定改為 09-14，見 §7.5 首晚實證段。）

## 7.5 Worker dispatch 角色（另案 PR，2026-09-14 使用者裁定「開」；動手前寫）

**目標**：`taiwan-flow-live-v2` 的 Cloudflare Worker 加 scheduled 角色 `iching`——**台北 22:30 與 23:30、週一～五**各 dispatch 一次
`shihpc/taiwan-stock-iching` 的 `daily.yml`（`workflow_dispatch`、`ref: main`、inputs 空＝T 為台北今日、`max_days` 預設）。
23:30 那班不看 22:30 的結果（`daily_run` 冪等：已完成→`trading_days_since` 為空 no-op；未齊→waiting 後再試；週末／假日→no-op），
比裁定 Q3 甲「未齊才補叫」更簡單、少一個狀態。

**驗收條件**：
1. 比照 `news` 角色：`wrangler.toml` 新增 cron（UTC 14:30／15:30 週一～五，dow 依 Quartz 慣例）；**分流走 `dispatchRoleForCron`
   以 `ICHING_CRON` 精確攔截、不是 `scheduledRole`**（實作時更正：22:30 落在哨兵窗 `minute%5===0`、23:30 落在晚場班窗，
   靠時分分流會誤入 sentinel）；`dispatchIching`（週末守門、secret 缺失走 `alertSecretMissing`、dispatch 失敗走 `alertJob`）。
   **KV 去重 `iching:<YYYYMMDD>:<HHMM>` 刻意不做**（實作時裁定）：CF 每條 cron 每分鐘只發一個事件、`dispatchNews` 同例無去重，
   下游 `daily_run` 冪等＋`concurrency: iching-commit` 排隊，不會雙跑。
2. `worker/test/` 新增測試：cron 路由（`ICHING_CRON` 與 toml 逐字同、回 `iching`、同分醒的哨兵／晚場班不受影響）、週末零呼叫、
   dispatch 請求形狀（URL＝`/repos/shihpc/taiwan-stock-iching/actions/workflows/daily.yml/dispatches`、body 恰 `{ref:"main"}`）、
   secret 缺失有／無通道、失敗重試＋告警當日一則、flaky 重試成功。
   `node test/<新檔>.mjs` 綠；`worker-deploy.yml` 以 glob 跑全部測試（新檔自動納入）。
3. 既有角色零改動（`news`／`sentinel`／`evening`／`health`／`morning`／`frame` 的測試全綠）；`/status` 不動。
4. **前置（使用者）**：`GH_DISPATCH_TOKEN`（fine-grained PAT）的 repository access 必須含 `taiwan-stock-iching`（Actions: write），
   否則 dispatch 回 404／403 → `alertJob`。PAT 在 GitHub 端改 access 不需重新 `wrangler secret put`。**本 session 無法驗證，
   上線首晚看 `npx wrangler tail` 或 taiwan-stock-iching 的 Actions 頁有沒有 `workflow_dispatch` run。**
5. 文件：live-v2 `CLAUDE.md`「其他 scheduled 角色」加 `iching` 一行；`PROJECT_SUMMARY.md` 快速接手段加一句；本檔 §7.5 記交付；
   `docs/schedule-map.md`（claude-harness）另案同步。
6. fresh-context 驗收綁 commit；PR 由使用者 merge；push 到 main 觸發 `worker-deploy.yml` 自動部署；當晚觀察。

**交付（2026-09-14）**：live-v2 分支 `a617f41`（角色＋cron＋測試 `worker/test/iching.mjs` 26 例＋`tickdiag.mjs` 條數守門 20→21＋
CLAUDE.md／PROJECT_SUMMARY／wrangler 註解）＋後續 commit（`alertSecretMissing` 加可選尾句，iching 缺 secret 告警明示無 GH cron 兜底）；
fresh-context 驗收綁 `a617f41`：必修無（同分醒三條 cron 以 tomllib 逐分展開實算確認互不干擾；26 支 Worker 測試全綠）。
claude-harness `docs/schedule-map.md` 補 Worker #20（tick，09-09 漏記）／#21（iching）。
**已部署（2026-09-14 10:22Z）**：live-v2 PR #8 由使用者 merge 成 `a4a548e`（含 `a617f41`＋`ecc9c99` 兩個 commit），
`worker-deploy.yml` run #27 對該 merge commit 全綠——「跑全部離線測試」與「部署 Worker」兩步皆 success（GitHub Actions
job 步驟逐一查看，非只看 run 結論）；harness PR #6（schedule-map）同時段 merge。
**✅ 首晚實證（2026-09-14）**：使用者同日更新 fine-grained PAT 涵蓋本 repo 後，Worker 22:30 那班準時 dispatch——
run #6 `created_at` **14:30:39Z**（cron `30 14 * * 2-6` 的同一分鐘，`event: workflow_dispatch`），1.5 分跑完，
commit `8d6bd9a`「daily: 2026-09-14 2026-09-14 22:32」落地：原始列數 price 46,460／inst 108,338／stocks_in_pool 1,967／
month_revenue 4,667／financial_statements 69,552，**警示無**，pool 不變、除權息 +7、基本面 new_periods 28（changed 0）、
分數 5,826 列、修剪刪 1 留 480（最舊 2024-09-23）、`n_calls` 25。原排的 22:35 手動保險班**未觸發**（22:35 檢查時
Worker 那班已完成，無需代打），保險機制就此撤除。**PAT 涵蓋本 repo 由此直接證實**（dispatch 回 204 才會有這個 run）。
23:30 第二班預期 no-op（`pending` 空 → 「沒有產出變更」）。至此 `data/scores/` 有 09-01～09-14 共 10 個交易日的分數檔；
其中**只有 09-14 是當日由主觸發產出**，09-01～09-11 是 09-14 白天的補跑（run #3～#5）。**使用者裁定（2026-09-14）：
完成定義 #5「連續 10 個交易日」從 09-14 起算**——第 10 個交易日為 2026-09-25（週五，中間無國定假日；若遇臨時休市順延），
判準＝每個交易日的 `runs/collect/<T>-daily.json.gz` 與 `data/scores/<T>.json` 皆由當日 Worker 主觸發的 run 產出並進 main，
補跑產出的不計。

## 7.6 D-3 對帳儀式（2026-09-14 使用者裁定「開 D-3，然後一路做下去」；動手前寫，CANON 第 3 條）

**目標**：對真實日子證明「每日班（GitHub Actions，原料包路徑）」與「Hetzner 回補＋重播路徑」同一 T 的**分數與原料包**逐位相同。
現有證據只有合成 DB 測試（`tests/test_daily_run.py::test_chain_end_to_end_bitwise`、`tests/test_daily_core.py::test_daily_chain_bitwise_equals_reference`）
與種子匯出當時的 20 日重驗；每日班上線後真實資料上尚無任何實證。

### 7.6.0 盤點（2026-09-14，fresh-context 子代理實查，主對話核對關鍵處）

- `scripts/diff_scores.py`（`:52-59` argparse）只吃兩個 sqlite `scores.db`，無容差、整列 dict 相等；**不吃 `data/scores/*.json`**。
  JSON→`ScoreStore.write_day` 的載入範式已在 `tests/test_daily_run.py:196-199`。
- **原料包比對沒有現成腳本。** `export_bundles.py` 與每日班共用 `bundle_io.write_bundle`（gzip `mtime=0`、`sort_keys`），
  格式同、可位元組比；但 **`us`／`fx` 兩鍵兩路切分點不同**（`replay_io._dated` `:383-391` 游標是 ReplaySource 實例狀態；
  `daily_fetch.fetch_day` `:204-215` 取 `(last_us, T]`，`last_us` 由 `daily_pipeline.last_dated` 往回掃既有包），
  且 `prune_bundles`（`daily_pipeline.py:207-208`）會把被刪包的 `us`／`fx` 併進新最舊包——**所以 `us`／`fx` 一律比「區間內全部包的聯集」**，
  其餘 10 個頂層鍵（`schema`／`band`／`tpe_date`／`index`／`stocks`／`official`／`futures`／`total_margin`／`vix`／`foreign_net_oi`）逐日逐位比。
- `diag`：JSON 的 `diag` 多 `rank_pool_size`／`text_version` 兩欄，sqlite `replay_day` 沒有（`scores_io.py:62-67`）→ 比對時排除這兩欄。
- 版本三元組：JSON 頂層 `data_version`／`text_version`／`params_sha`；sqlite `replay_meta.params_sha`／`versions`。比對前先核 `params_sha` 與 dv 相同。
- 未實測的一點：DB 端 `_num` 把 NaN 原樣丟給 sqlite（`scores_io.py:111-117`），JSON 端寫 `null`；`rows_for_day` 讀回是否對稱**要在真實 db 上驗**。

### 7.6.1 交付物

1. `scripts/parity_check.py`——**在 Hetzner 上跑**（`scores.db` 2.6 GB 不搬），輸入 `--cache-dir`（Hetzner cache：`scores.db`＋原料 sqlite）、
   `--repo`（本 repo 的 git checkout，讀 `data/scores/*.json`＋`runs/collect/*.json.gz`）、`--from/--to`（預設＝repo 內有分數檔的全部日期）。
   逐日輸出三段：
   - **原料包**：10 個鍵逐位（以 `bundle_io.dumps` 的字串比、差異報到「鍵／股票／欄」）；`us`／`fx` 聯集比（區間內兩側全部包的列
     以 `date` 去重，只比兩側日期範圍的交集）。
   - **分數**：JSON rows 灌臨時 `ScoreStore` → 沿用 `diff_scores.diff_day`；`diag` 比 9 欄（排除上述兩欄）；`params_sha` 不同直接 rc 2。
   - **差異歸類**（分數層）：每個有差異的 `stock_id` 歸入四類之一——①**入池未滿 320 交易日**（在 repo 原料包首次出現距 T 不足 `window` 日，
     §7.4.0 第 3 點）②**近 320 日有效收盤 <61**（§7.0 第 1 點同型邊界）③**該檔原料包本身有差異**（上游修訂：兩路抓取時刻不同，FinMind 事後修訂
     法人／持股／營收皆會造成，這是儀式必然會撞到的合法差異）④**無法解釋**。只有 ④ 讓 rc 為 1；①②③另列並印計數。市場層（`index`／
     `official`／`futures`／`total_margin`／`vix`／`foreign_net_oi`）任一鍵有差異 → 該日分數比對標「市場層原料不同，分數差異不歸類」、rc 3。
   - rc：0 全同或只有 ①②③；1 有 ④；2 版本／參數不符或開檔失敗；3 市場層原料不同。
2. `tests/test_parity_check.py`——合成 DB 世界（沿用 `tests/test_daily_core.py::world` 的建法：`build_full` → 重播到 K 存快照 →
   `export_seed` → 每日班跑完剩餘日）：①原封不動 → rc 0、四類計數全 0；②改一格分數（直接改 JSON 一列的 `score`）→ rc 1 且指到該股；
   ③把某日 `us` 列搬到隔日包（模擬切分點不同）→ 仍 rc 0；④對某檔在 repo 端刪掉入池前的列（模擬新入池）→ 該檔歸 ①、rc 0；
   ⑤改 `index` 一格 → rc 3。
3. 本節 7.6.2 記真實對帳結果（兩輪：09-01～09-14 一輪、09-25 第 10 日後一輪）。

### 7.6.2 怎樣算完成

- 上述測試 5 例綠、全套 pytest 綠、`ruff check` 改動檔乾淨；fresh-context 驗收綁 commit。
- **Hetzner 第一輪實跑**（使用者執行，指令由本節提供）：`backfill_hetzner.py run --from 2026-09-01 --to 2026-09-14`（沿用 dv，不帶 `--data-version`）
  → `replay_scores.py --resume` → `git pull` 本 repo main → `parity_check.py`。結果逐位相同或差異全部落在 ①②③且每筆有歸因，才算第一輪通過；
  出現 ④ 就是 bug，回頭修（修的是每日班或重播任一邊，修完兩邊都要重驗）。
- 第二輪在 09-25 之後同法再跑一次。兩輪都通過 → D-3 結案，parity 儀式改為每週例行（§3 所述）。

### 7.6.3 D-3 交付紀錄（2026-09-14～15）

- **交付**：`1caf494`（`scripts/parity_check.py`＋`tests/test_parity_check.py` 6 例＋`ScoreStore.params_sha_of`）→ `11baf98`
  （驗收補強：us／fx 聯集讀區間內全部原料包、②③diag 三個正向測試＋「刪分數檔保留包仍餵聯集」、`scripts/hetzner_round.sh`）。
- **fresh-context 驗收（綁 `1caf494`）**：必修無。8 個突變 6 個被測試打紅、2 個沒有（diag 比對、② 門檻——功能在、測試守不住）
  → `11baf98` 補上，四項突變逐一實測轉紅。§7.6.0 的未驗點「DB 端 NaN vs JSON null」驗收者實測 sqlite 3.45.1 寫入 NaN 讀回 `None`，
  與 JSON `null` 對稱（仍建議真 db 上看一眼）。實作對 spec 的兩處擴充經驗收者判定合理：市場層差異**自最早差異日起**每日不歸類
  （市場 ring 跨日，只標一日會讓其後全是假 ④）；us／fx 聯集差異視為市場層（rc 3）。rc 優先序 2＞1＞3＞0。
- **⚠ 設計缺口（驗收者實測證實，非推測；需使用者裁定）**：新入池的檔在參考路徑（`scan_features.py` 走 `feed.iter_days`
  無池過濾＋當時最新池）會餵入池前的全部歷史，每日班 `rebuild_from_bundles` 只餵包內池檔、只有入池日起的列 → 兩路 `DailyScanner`
  deque 長度不同 → `ma_eligible`／`hl_eligible`／漲跌計數不同 → `day_breadth` → 市場 ring → **大盤二爻分數整天不同**
  （合成世界 19／19 日 `line_2` 81.358 vs 71.350），持續約 61 個有效收盤日（MA60／HL60 資格）；個股列在合成世界未變，
  生產上若方向分數受 `line_2` 影響則個股列也會不同（推測、未驗）。§7.4.0 第 3 點「另列、不算差異」在實務上等於
  「一有新入池檔（含 twse↔tpex 轉板、暫停後恢復），每日班之後約兩個月的大盤分數與規格路徑不一致」——這是產品正確性問題，
  不只是對帳標籤。**目前實際影響為零**：main 上 `data/pool.json` 自種子（`d4a7788`）以來只改寫過一次（`1eb2284`），
  成員零增減，唯一差異是 8472 改名（夠麻吉→納維康），第一輪對帳不會撞到。處置三案待裁定：甲＝每日班偵測新入池檔時逐檔補抓
  近 320 交易日原料（5 次 API）存 `data/entrants/<sid>.json.gz` 側檔、重建時併入、逾 window 自動清（估半天）；
  乙＝只改對帳腳本把「有 ① 檔的日子」的大盤列差異另列（半小時，等於承認那兩個月每日班是錯的）；丙＝原料包改存全市場列
  （一天，每日包約 +10%，已存的 480 份無法補救）。
- **Hetzner 回合改為「一句話貼」**（claude-harness `02-judgment.md` §6，2026-09-15 使用者裁定）：
  `bash scripts/hetzner_round.sh 2026-09-01 2026-09-14` 自己 `git pull --ff-only`＋印 HEAD → 回補 → `scan_features --resume`
  → `replay_scores --resume --window <cross.json 的 window>` → `parity_check` 寫 `runs/parity/<FROM>_<TO>.txt` → commit 到分支
  `hetzner/parity-<TO>` 並 push；session 自己 fetch 該分支讀報告，**使用者不必貼回輸出**。離線煙霧（合成世界＋本機 bare
  remote）全程 rc 0；煙霧實際抓到一個問題——初版 `replay --resume` 沒帶 `--window`，與快照參數不符即中止，已修。
  估時（真實資料）：回補 10 日約 2 分＋特徵掃描（全量重播）約 5 分＋重播 10 日約 5 分＋對帳約 1 分。
- **第一輪實跑（2026-09-15 台北 00:xx～08:xx，使用者三次貼指令，兩個真問題，皆為離線煙霧覆蓋不到的環境事實）**：
  ①第一趟 `run --from --to` 一趟跑，`daily_slice`／`official` 守門要求「同一 data_version 落地的 TAIEX 日曆涵蓋區間」，
  該守門在同一趟內先於 `index_price` 落地就評估 → 計畫＝0 中止（修法＝回補分兩趟，PR #16）。②分兩趟後 `index_price`
  仍計畫＝0：`plan.keys_for` 的區間型鍵對齊 `chunk_ranges(spec.start, spec.end)` 固定網格，`spec.end`＝`config.DATA_END`
  ＝**2026-08-31（使用者裁定的回測資料截止）**，`--from/--to` 只選塊不改塊界 → 超過截止的區間選不到任何塊 → 指數補不到 →
  日曆不涵蓋 → 全部切片中止。**§3 與 §7.6.2 寫的「`run --from` 補新日」從未在真環境驗過，是錯的前提。**
  修法＝`--data-end` 覆寫（只延伸該次 run 的鍵網格與守門，`config.DATA_END` 與回測切分不動；最後一個 year 塊鍵會由
  `~2026-08-31` 變 `~<data-end>`＝整塊重抓、冪等，`plan.py` 註解早警告過的「鍵搬家」，對對帳儀式可接受）。
  ③另一個小坑：失敗那趟的 `finally` 改寫了兩份日曆檔，使用者下一次貼的 `git pull` 被髒工作樹擋下（B1 預言成真），
  一行指令改為先 `git checkout -- data/calendar_*.json`。
- **第一輪對帳分析：月營收查詢窗（2026-09-15）**——分析起點是「T ≥ 09-10 兩側分數全部不同」，首要嫌疑為每日班的月營收窗
  `month_windows(T, 2)` 不含本月、8 月營收（`date=2026-09-01`，9 月 1～10 日公布）永遠抓不到。**核對 main `eebda5f` 後這個嫌疑不成立**：
  ①程式：`month_windows` 從 T 所在月起算，T=09-14 回 `[08-01～08-31, 09-01～09-30]`（舊 `test_fundamentals_query_windows_are_period_aligned`
  就是這樣斷言的）；②資料：`data/fundamentals.json` 種子 `d4a7788` 的 `(2026, 8)` 列＝**0**、main `b8e4f69` ＝**1,964 檔**
  （§7.4.4 run #5 也記了「新增 2026-08 月營收 1,964 檔」）。「總列數只多 127」是 `prune_fundamentals` 以每檔最新月往前
  `FUND_MONTHS_KEEP=24` 保留造成的——已滿 24 月的檔加一個月就掉最舊一個月，列數不變（1,909 檔已滿 24 月；有測試
  `test_update_fundamentals_merges_current_month_revenue_and_prunes_oldest` 鎖住這個機制）；「沒有 `date=2026-09-01` 的列」是
  看錯欄位——該檔存 `[revenue_year, revenue_month, revenue]`，不存 `date`。
  **仍做的一處對齊（不是修「抓不到」）**：本月窗由整月 `[月首, 月末]`（`end_date` 在未來，§7.4.4 原列為未實打的觀察點）改為部分窗
  `[月首, T]`——與回補層 `--data-end` 的本月部分塊同形（2026-09-15 Hetzner 實測 `2026-09-01～2026-09-14` range_slice ok），
  視窗起點仍一律月首（2026-09-14 實測 `07-18～09-01` → 0）。`REVENUE_MONTHS_BACK=2` 語意＝「上一公布月整月窗＋本月至今」，
  晚報者的追補視窗與改前相同（上月整月窗整個本月都在查），故不改 3。`quarter_ends` 不動。**T ≥ 09-10 分數差異的真因未定**，
  下一個可查的嫌疑（推測）：兩側月營收**集合**不同——每日班 09-14 抓、Hetzner 09-15 抓，逾期晚報者（含金融桶期限 15 日）
  只在後者；`FundamentalsBridge.inputs_for` 的 `industry_median_3m_yoy` 是**產業中位數**，一檔的差異會擴散到同產業全部個股列，
  「全部不同」與「少數檔差異」並不矛盾。要證實得把兩側 as-of T 的 `(sid, y, m)` 集合直接 diff（`parity_check` 目前不比基本面，
  見下一條已知限制）。
- **已知限制**：③ 只看比對區間內的包差異，區間外但仍在 ring 內的上游修訂會落成 ④（第一輪不會發生——兩側區間外資料都不重抓；
  例行化後 `--from` 要拉夠早或另判）；基本面（`data/fundamentals.json`）不在原料包內，晚報的季報／營收修訂造成的分數差異
  會落成 ④，第一輪若出現以此為首要嫌疑；分數檔早於現存原料包（>480 日被修剪）的日子只比分數。

- **第一輪對帳根因（2026-09-15 定案；RCA 筆記 `scratchpad/rca/NOTES.txt`，不進 repo）**——上一條「T ≥ 09-10 分數差異的真因未定」
  與「月營收集合不同」的嫌疑**都不是**。真因在除權息：
  - **機制**：`daily_fetch.fetch_day` 對除權息打**一次**全市場 `TaiwanStockDividendResult`、`start=T−7, end=T`（改前 `:236`）。
    FinMind 該 dataset 的全市場（不帶 `data_id`）區間查詢**把區間當單日切片、只回 `start_date` 當天的列**。於是 ex_date=T 的事件
    要到 **T+7 那班**才進 `data/factors.json`；計分當下 `load_factors` 沒有該事件 → 除息日的價格跳空被當成真跌 → 該檔 `line_2`
    → 漲跌／廣度計數 → 大盤 `line_2` → 全體 `line_6`。且事件是在 T+7 才補進檔、既寫出的分數檔與**跨日狀態（`cross.json`）**
    不會回頭重算——**污染跨日、不自癒**，之後每一日都在被污染的狀態上續算。
  - **證據一（十組計數，Actions run #3／#4／#5／#6 的 `原始列數` 行 vs `data/factors.json` 各 ex_date 列數）**：每個每日班視窗
    `[T−7, T]` 回的 `dividend` 列數**恰等於 T−7 那一天**的 ex_date 列數——T=09-01→25(=08-25)、09-02→17(=08-26)、
    09-03→33(=08-27)、09-04→16(=08-28)、09-07→13(=08-31)、09-08→21(=09-01)、09-09→20(=09-02)、09-10→15(=09-03)、
    **09-11→0**（09-04 無 ex_date 列；而窗內 09-07 起明明有 ≥7 筆，區間語意若成立不可能回 0）、09-14→7(=09-07)。
    同一批 log 的 `除權息+N`（`update_factors` 追加數）在 09-08／09-09／09-10 分別 +21／+20／+15＝整批新列，
    就是「ex 09-01／09-02／09-03 的事件到 09-08／09／10 才進檔」的直接紀錄。（免 token 打 FinMind 只回 400 free level，
    無法直接驗證視窗語意；上述是行為證據，不是 API 文件。）
  - **證據二（EXP4，每日班路徑可重現）**：以種子 `d4a7788`（`cross.json` last_date 08-31）＋1,618 份種子原料包＋`1eb2284` 的
    09-01 原料包／pool／fundamentals／日曆組成離線世界，`factors.json` 取 `1eb2284`（**無** ex 09-01 事件），
    `daily_core.run_offline(root, '2026-09-01', window=320)` 在 Python 3.12 下**逐位等於** `1eb2284:data/scores/2026-09-01.json`
    （262,575 欄，diag 只差 `elapsed_ms`）——每日班當時算出來的就是缺事件的結果，可離線復現。
  - **證據三（EXP5，決定性）**：同一世界**只**把 21 筆 ex 09-01 事件（＋1 筆 9105 08-31，取自現行 `data/factors.json`）補進
    `factors.json`，其餘一字不動，重算 09-01 即**逐位等於 Hetzner 參考**（dump 內 19,913 欄全同；dump 外 242k 欄全同；
    僅 `lines_provisional` 的 list/str 表示差）。一個變因、差異歸零。
  - **影響面**：①09-01～09-14 **十日全部**受影響（每日都有前 7 日內的 ex_date 事件缺席，且狀態鏈污染累積）；②跨日狀態污染
    **不自癒**——即使之後事件補齊，已寫出的分數檔與 `cross.json` 不會回頭重算，只有重算整段才乾淨；③`parity_check` ⑤（除權息
    係數比對）**只比兩側「現行」`factors.json`**——第一輪跑對帳時每日班的檔已在 T+7 補齊、與 Hetzner 一致，所以 ⑤ 全綠、
    看不到「計分當下缺事件」，這是 ⑤ 的盲點（as-of T 的事件集合才是該比的東西）。
  - **修法（本批，分支 `claude/dazzling-maxwell-serk13`）**：`daily_fetch.py` 除權息改**逐日單日切片**——對
    `dividend_days(T, DIVIDEND_LOOKBACK_DAYS)`＝`[T−7, T]` 每個曆日 d 各打一次 `start_date=end_date=d`（8 次；單日形狀是回補層
    已驗證的形狀），合併去重（同 `(stock_id, date)` 後者覆蓋，再交 `update_factors` keep-first），`counts.dividend`＝8 次原始列合計、
    `n_calls` 隨之 +7（§4 表已改）；任一次回列的 `date` ≠ 該 d 即記 warning `dividend:shape(start=…,got_dates=…)`（列仍照自己的
    date 收），FinMind 日後改行為不會靜默。測試：`tests/test_daily_run.py` 的 `FakeFM` 改成**模擬 FinMind 實況、只回 start_date
    當天**（舊 mock 回整段視窗，所以舊碼一直全綠——這正是沒抓到的原因），`test_chain_end_to_end_bitwise` 加斷言「ex_date=T 事件
    當班進 factors.json」＋「每日 8 次、start=end」，新增 `test_dividend_fetch_is_per_day_slices_and_flags_shape_drift`
    （去重／計數／`dividend:shape`／全空）；**突變實測**：把抓取改回單次區間查詢 → 「當班進 factors.json」那條先紅
    （`tests/test_daily_run.py:206`），已還原。
  - **重算覆蓋交付（2026-09-15 使用者裁定「覆蓋」，本批）**：以 `scripts/recompute_from_seed.py` 從種子 `d4a7788` 重算
    09-01～09-15 共 11 日並**整批覆蓋** `data/scores/2026-09-{01..15}.json`＋`data/state/cross.json`；`data/factors.json` 同批
    補進 Hetzner 逐檔抓到的 25 筆事件（ex_date 09-09～09-14，主線 10,742 列 → 10,767 列、無刪除、`data_version` 不變）。
    被覆蓋的原始版本＝每日班 commit `1eb2284`（09-01）… `5f1cbcb`（09-15，`caae76f`／`b8e4f69`／`8d6bd9a` 為其間各班）。
    重算世界：seed `d4a7788836be`（1,618 份原料包全匯入、非部分種子）、data-ref＝主線 `factors.json` 合併 25 筆後的臨時 commit
    `54af75595e60`（pool／fundamentals／日曆／59 份 entrants 側檔與 `5f1cbcb` 相同）、bundles-ref `5f1cbcb1f43e`；Python 3.12.3；
    完整 manifest 在 `recompute-summary.json`（scratchpad，不進 repo）。**驗證**：以第二輪對帳傾印 `diff2.jsonl.gz`（Hetzner 參考
    `hetzner/parity-2026-09-14`，210,163 格、10 日）還原參考，**09-01～09-14 十日全部逐位相同**（各日 5,817～5,883 列，dump 覆蓋
    19,913～22,142 格）；**09-15 無 Hetzner 參考**（第二輪只到 09-14），只記 vs 現行 `5f1cbcb` 分數檔的差異：5,841 列中
    788 列同、5,053 列不同（16,461 格）、無只在單側的列、diag 無差欄——差異方向與前十日一致（現行版是缺事件＋污染鏈的結果）。
    `cross.json` 的 `adv`／`market_lines`／`meta` 與現行相同，`market_line2`／`stock_line2`／`stock_lines` 換成乾淨鏈的值，
    `last_date` 仍 2026-09-15，下一班（09-16）從它續算。**已知限制**：①09-15 當日的 ex_date 事件（若有）不在合併檔內
    （Hetzner 逐檔匯出只到 09-14），09-15 分數仍缺那一天的事件，明晚新碼那班會抓到但 keep-first＋不回算（見待裁定③）；
    ②本批只覆蓋分數與狀態，原料包一個位元組不動（D-3 已證與參考逐位相同）。
  - **待裁定**：①重算覆蓋已交付（上一條）；②**已分辨（2026-09-16 每日班 run 35109018899，逐日切片新碼第一晚）**：8 次單日
    切片、無 `dividend:shape` 警示、原始列數 92、`factors.json` 新增 67 筆——**ex_date 09-16（當天）54 筆當班就查到**、
    09-15 12 筆、09-14 1 筆。「端點只回 start_date 當天」成立、「落後 7 天」不成立，**甲（逐檔抓）不需要**，
    `scratchpad/patches/jia-per-stock-dividend.README.md` 作廢。09-16 分數是含當日事件算的。**但同時證實③是真的會發生**：
    那 12 筆 ex 09-15 在 09-15 那班（舊碼）沒抓到、重算覆蓋時 Hetzner 逐檔匯出又只到 09-14，所以主線 09-15 分數缺這 12 筆、
    09-16 從那條鏈續算——第一次真實的「晚到／漏抓不回算」；處置見 §7.6.4。③晚到事件（T+1 之後才落地）的**回捲重算設計另案**——現行 keep-first＋不回算的結構下，任何晚到事件都是
    同型的靜默污染，只是機率較低。
  - **附帶發現（環境，影響對帳與重現）**：**Python ≥3.12 的內建 `sum()` 對 float 改用 Neumaier 補償加法**
    （CPython 3.12 changelog），`src/iching/scan.py:482` 的 `over = c > sum(closes[nc - n:]) / n`「收盤恰等於 MA」的邊界判定會隨
    Python 版本變——RCA 的 world1／world2（3.11）對參考有 ~0.03–0.07 的 `line_2` 殘差，world3 把 `scan.sum` monkeypatch 成
    Neumaier 後歸零；world4／world5（3.12）直接逐位。Actions `setup-python` 3.12（實裝 3.12.14）與 Hetzner 一致，本機 3.11
    不一致。**對帳／重現一律用 ≥3.12**；本機快速自證：`sum([0.1]*10+[1e16,1.0,-1e16])` 3.11 得 `0.0`、3.12 得 `2.0`。
    這不是 bug 修復項，是「兩層 parity」的環境前提，記在這裡免得下次又追一輪。

### 7.6.4 第二次重算覆蓋：09-15～09-16（2026-09-16 使用者裁定「覆蓋」）

**起因**（§7.6.3 待裁定②③）：09-16 每日班（逐日切片新碼）抓到 12 筆 ex 09-15 事件，而 09-15 分數（09-15 重算覆蓋版）缺它們、
09-16 從那條鏈續算——第一次真實的「晚到／漏抓不回算」。

**方法**：①`scripts/recompute_from_seed.py`，seed `d4a7788`、**data-ref＝臨時 commit `250f80c`（＝`54af755` 的池／基本面／日曆／
59 份 entrants ＋ 主線 `127de74` 的 `factors.json`）**、bundles-ref `127de74`、`--to 2026-09-15`；**不能直接用主線當 data-ref**——
主線的池今晚已改（+7947、entrant 2938），用它重算 09-01 就與 Hetzner 參考差 2,601 列（非 PIT 池的入池效應，裁定 #44 那型），
第一次就是這樣配錯而停掉。②09-16 由 rc_out3 世界（狀態鏈到 09-15）覆上主線 `127de74` 的池／基本面／日曆／60 份 entrants／factors
與 09-16 原料包，呼叫 `daily_core.run_offline(root, "2026-09-16")` 接算（腳本 `step_0916.py`，scratchpad）。
**方法自驗**：從主線自己開跑前的狀態接算 09-16，與主線 09-16 分數 5,841／5,844 列逐位相同，僅 8077 三格 1e-14 浮點順序差
（原料包串流 vs 記憶體載入的加總順序，非語意差）。

**驗證**：09-01～09-14 十日仍與 Hetzner 參考逐位相同（含新抓到 1 筆 ex 09-14 事件的 09-14——該筆沒改變任何分數）。
**影響**：09-15 **876／5,841 列**（870 檔）不同，幾乎全是上爻（12 筆事件 → 大盤二爻 → 全體上爻），另 3675／5426／6924 二爻本身變；
09-16 只有 8077 三列 7 格（二爻 3 格＋衍生的內卦分 2 格、基礎分 2 格）≤4.3e-14；`cross.json` 差 13 個歷史項（`market_line2` tpex|short、四檔 `stock_line2`）。被覆蓋版本：
09-15＝`3838ec6`（第一次覆蓋）、09-16＝`11168f0`（今晚每日班）。**必須在 09-17 22:30 那班前合併**，否則再從污染鏈續算。

**結構性結論**：③「晚到不回算」現在有了第一個實例與一套可重複的處置（重算工具＋接算腳本），但仍是手動；自動回捲設計另案。

## 7.7 甲：新入池檔歷史對齊（entrants 側檔）——驗收條件（2026-09-15 使用者裁定甲後、動手前寫）

**盤點後的事實（主對話實查）**：參考路徑的池是**靜態的最新快照**、套用到全部歷史——`scripts/scan_features.py`
一次 `load_pool(universe)` 後對每一日 `feed.day_records(d, rows, pool, …)`（`feed.py:172-175` 只留 `pool.get(sid)` 非 None 的列），
`replay_io.ReplaySource.pool` 亦同（`replay_io.py:89`）；種子匯出走 `read_day(T)`＝同一個靜態池，所以種子與參考一致。
每日班的池每天由 `TaiwanStockInfo` 刷新（`daily_pipeline.update_pool`），原料包只含抓取當時池內檔（D-1 語意）。因此兩路只在
**池成員隨時間改變**時分岔：**入池**（參考有入池前全部列、每日班只有入池日起的列）與**出池**（參考整段不含該檔；每日班
重建時以現行 `pool.json` 過濾——`rebuild_from_bundles` 走 `feed.day_records(…, pool)` 同一支過濾，出池後舊列自然被濾掉，
**這一側已對齊、不需動**；實作者要以測試證明這句話，證不出來就回報）。甲只處理入池側。

**交付物**
1. `data/entrants/<sid>.json.gz`：`{schema:1, stock_id, data_version, from, to, days:{date: <與 bundle.stocks[sid] 同形的列>}}`，
   列由 `collect.stocks_from_rows` 同一支建構器從該檔 5 個資料集（price／inst／margin／short_sale／shareholding）的
   `data_id=<sid>` 區間查詢產出（parity by construction，不另寫欄位映射）。區間＝`[T − window 交易日, T − 1]`（以 repo 日曆）；
   FinMind 沒列的日子就沒有（真新上市自然是空檔）。空 `days` 也要落檔（＝「查過了、沒有」的標記，避免每日重抓）。
2. 偵測（每日班 `run_pipeline` 內、原料包已全部載入後，**不多讀任何一份包**）：候選＝現行池內、且在持有原料包中首次出現的日期
   **晚於最舊那份包的日期**、且無側檔的 sid。（種子期就在池內的檔在最舊包就出現 → 不是候選；真新上市 → 抓到空檔 → 不再抓。）
   每檔 5 次 API；抓取失敗只記 `warnings`、**不寫側檔、不擋當日計分**，下一班自然重試。log 一行報 `entrants=N calls=5N`。
3. 併入：`rebuild_from_bundles` 在 ingest 每一日前，把該日缺 sid 的 `stocks` 補上側檔列（**只補缺、不覆蓋**）；磁碟上的原料包
   一個位元組都不改。`prune_bundles` 末尾刪掉 `to` 早於最舊持有包日期的側檔（之後任何持有包都不缺它）。
4. 對帳配套（`scripts/parity_check.py`）：比對區間內某檔的首次出現日 E 若晚於區間起日，則 **E 之前各日的大盤列差異**歸為
   「①連帶」另列（rc 0、印計數），E 起照常歸類。理由：參考池是最新快照、對 E 前各日也算進該檔，那些日子每日班當時本來就不可能
   知道它——這不是 bug，是參考路徑非 PIT 的已知性質（本節開頭）。**只有市場列適用**；個股列差異照常。
5. 文件：本節記交付與驗收；`docs/P2-KICKOFF.md` #44 已記裁定。

**怎樣算完成**
- 合成世界新案例：股票 X 於 `DAYS[E]`（E≈K+4）才進 `TaiwanStockInfo` 快照、但 raw 表自始就有 X 的價量與籌碼；種子在 K 匯出時
  池不含 X。**參考**＝以含 X 的最新池重播全程。**每日班**逐日跑到最後：①`T ≥ E` 的分數與參考**逐位相同**（含大盤列與 X 自己的
  列）②`data/entrants/X.json.gz` 存在、`from/to` 正確、內容與參考 `read_day` 的 `stocks[X]` 逐日逐位相同③原料包位元組與改動前
  相同④拿掉併入邏輯（突變）→ ① 轉紅⑤`T < E` 的日子 `parity_check` 對大盤列差異歸「①連帶」、rc 0，其餘日子 rc 0。
- 出池側：合成世界讓 Y 於 `DAYS[E2]` 從快照消失 → 每日班 `T ≥ E2` 分數與「最新池不含 Y」的參考逐位相同（證明「已對齊、不需動」）。
- 抓取失敗案例：FakeFM 對 X 的 per-stock 查詢丟例外 → 當日照常計分、無側檔、`warnings` 有記；下一班成功後側檔落地。
- 全套 pytest 綠、ruff 乾淨、fresh-context 驗收綁 commit；PR 進 main 後下一班 daily run 成功（本 repo 的線上驗證）。
- **不做**：不改原料包語意（丙）、不改參考路徑的池語意（那是 P2 裁定 #6 的範圍，PIT 化屬另案）。

### 7.7.1 甲交付紀錄（2026-09-15）

- **交付**：`6784287`（10 檔）＋後續小修（壞側檔不擋計分，見下）。fresh-context 驗收綁 `6784287`：**程式必修無**；760 passed／20 skipped；
  五個突變（拿掉併入／拿掉 adopt／連帶條件反轉／斷言拿掉交集／斷言恆真）各自有測試紅、無存活。
- **與 §7.7 spec 的四處實測偏差（驗收者獨立重建世界印出差異欄確認，非實作者自述）**：
  1. **對齊點是 T ≥ E+5，不是 T ≥ E**：E～E+4 唯一差異是大盤列 `flags`——`market_flags` 讀狀態鏈 `market_line2` 的 T−5 歷史
     （`score/market.py` `line2_score_t_minus_5`），E 前那幾格是每日班當時無 X 算出的值，側檔只進 `WindowCache` 重建、不重跑 `step`，
     補不到狀態鏈。E 起大盤二爻**分數**已相同（甲要修的正是這項）。
  2. **X 自身的遲滯狀態（`stock_lines`）與 `stock_line2` 9 日歷史同理補不到**；合成世界 X 列自 E 起逐位相同是巧合（量恆定使
     `seq1_rest` 不觸發、參考在 E 的狀態恰等於首判）。生產上 X 列可能自 E 起在 `line_4`／`line_states` 不同一段時間——會落對帳的 ①
     （入池未滿 window）、不會紅；「T ≥ E+5 逐位相同**含 X 列**」對生產是推測。
  3. **第 4 點（對帳配套）由「只有市場列」改為「E 前整日」**：實測 E 前的個股列也不同（大盤方向分數→個股 `line_6`），且參考池是最新
     快照、E 前各日對每日班本來就不可比，故整日全部差異列歸「①連帶」（rc 0、印計數）；E～E+4 只差大盤 `flags` 亦歸連帶。
     **代價**：E 前整日的真 ④ 會被一併蓋掉（驗收者建議可改為「該日差異含『只在參考』的入池檔才整日連帶」，記為待辦、本批不做）。
  4. **出池側**：spec 原主張「重建過濾已對齊、不需動」——**過濾對齊為真，但排名池斷言會卡死每日班**：Y 出池後鏈上 `cross.adv`
     仍含 Y、重算端從未追蹤 Y → 每班 `DailyCoreError: 排名池不一致` rc 2，最長 59 個交易日（生產任何下市都會踩到；main 至今無
     下市所以沒發生）。修法＝兩側 `eligible()` 各取 ∩ 現行池再比，鏈上 deque 不動、自然衰減（突變證實池內不一致仍擋得住）。
     衰減中的出池檔只影響 `diag.rank_pool_size`（不在對帳 9 欄內），不影響任何分數列（`step` 只在 `in_pool` 用到、Y 已被 pool
     過濾）。修後 E2+5 起逐位相同（E2～E2+4 同樣只差大盤 flags）。
- **生產風險（驗收者以 main 真實 480 份包實跑）**：首次上線那班候選 **59 檔 → 295 次 API**（一次性）；組成多為回補期最舊幾份包的
  池缺口（首見 09-25～09-30，`2489`／`4946`…）加真 IPO（`3718`、`7835`…），多數側檔補不到任何列、約 480 日後修剪。
  `load_bundles` 480 份常駐實測 **+849 MB**（VmRSS 40→890，11.7 s），`merge_entrants` 只補列不複製（+23 MB），runner 7 GB 內。
  側檔在 `daily.yml` 的 `git add data` 範圍內，會隨每日班進 main。
- **記錄、不改**：候選定義照字面會把「最舊包恰好缺列」的檔也當候選（每次修剪邊界推進可能再觸發，5 次／檔一次性）。
- **壞側檔不擋計分（`9b2d6f1`，fresh-context 驗收必修無、兩個突變皆有測試紅）**：`read_json_gz` 對五種壞形狀（非 gzip／截斷／
  壓縮流損壞／非 JSON／空檔）一律 `BundleError`；`read_entrants` 回 (好檔, {sid: warning})，warning 格式
  `entrant:<sid>:bad-sidefile:<例外類別>:<檔名>:<訊息>`；`run_offline` 只併好檔，壞側檔且在池內的 sid **豁免排名池斷言但不 adopt**
  ——該班 X 的 `in_rank_pool` 沿用狀態鏈上早已 adopt 的合格值（驗收者實測與參考一致），只有視窗特徵因少了側檔歷史而不同；
  `fetch_entrants` 把壞側檔 sid 視為無側檔 → 下一班重抓、tmp+replace 覆蓋自癒；壞檔本身不刪、`prune_entrants` 一律留。
  `done["entrants"]` 多 `merged`／`bad` 兩鍵。待辦（驗收者建議）：測試補一條 X 列 `in_rank_pool` 與參考相同的直接斷言；
  `ENTRANT_READ_ERRORS` 含 `TypeError`／`KeyError` 偏寬，`days` 列形狀可在 `entrant_from_payload` 明確驗。
