# P2 每日班（Actions 每日計分）架構方案

2026-09-14 動手前寫（CANON 第 3 條）。狀態：**§5 五題待裁定**。設計依據：`spec/P1-B3-replay.md` §B3.1／§B3.2、
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

## 5. 待裁定

| Q | 題目 | 甲 | 乙 | 我的建議 |
|---|---|---|---|---|
| 1 | **原料包大小與存法** | 先由 Hetzner 匯出一日實測（`scripts/export_bundles.py`，本批交付），>500 KB/日再談 `actions/cache` | 不實測、直接採 C | **甲，已實測（2026-09-14 Hetzner）：2026-08-31 一日 1,970 檔＋320 列美股＝99.3 KB**（首日含美股回補，常態日會略小）→ 種子 320 日約 31 MB、每年約 25 MB，路 C 成立 |
| 2 | **每日班的 `data_version`** | 沿用回補批號 `fm-20260911-01`（同一原料血統；重新回補才換號） | 每日各給新號 `fm-<T>-daily` | **甲**：乙會讓每日班與 Hetzner 的 7 鍵永不相同，parity 無從比；血統語意寫進 `docs/data-contract` |
| 3 | **觸發時點與完整性** | Worker 台北 22:30 單一班；資料未齊（任一核心資料集當日為空）→ 寫 `runs/collect/<T>-waiting.json`、rc=0 不計分，Worker 23:30 再叫一次 | 沿用哨兵法：Worker 逐一探測落地才 dispatch | **甲**：集保 21:00 後才更新，22:30 一班＋一次補叫最簡單；哨兵法要改更多 Worker 程式 |
| 4 | **股票池與還原係數的來源** | 每日班每天抓 TaiwanStockInfo／DividendResult，**變動才**改寫 `data/pool.json`／`data/factors.json`；Hetzner parity 時以 git 內這兩檔為準 | 每日班用當天 API 結果、不落檔 | **甲**：池與係數是兩層共同輸入，不落檔就無法重現 |
| 5 | **基本面** | git 保留 `data/fundamentals/`（月營收 15 個月、季報 6 期，池內全體，推估 <3 MB），每日 2 次 API 增量更新 | 每天抓全歷史 | **甲** |

**不列為裁定、但要記錄**：①`runs/collect/<date>-<band>.json` 的 `band` 取 `daily`（本專案只有一班）；②`DATA_END`／日曆延伸：每日班自己
把 T 追加進 `data/calendar_*.json`（`write_calendars` 的 `full_end` 是回補用的常數，不動）；③`backfill_hetzner.py` 寫日曆時內容不變也改
`generated_at`（每次跑都製造假 diff，2026-09-14 實測），一併修成內容相同不寫檔。

## 6. 交付切分

1. **本批（動手前）**：本檔＋`scripts/export_bundles.py`（Hetzner：`ReplaySource.read_day` → `runs/collect/<T>-daily.json.gz`，
   序列化決定性、可讀回成 `DayBundle` 逐位相同；順便量大小）。
2. 裁定後 D-1：`src/iching/collect.py`（純函式：API 回應 → `DayBundle`；與 `replay_io.read_day` 逐欄同語意）＋ `src/iching/bundle_io.py`（讀寫原料包）。
3. D-2：`scripts/daily_run.py`（§3 流程）＋ `.github/workflows/daily.yml`＋ Hetzner 種子匯出（320 日）。
4. D-3：parity 儀式腳本與測試（合成 DB：Hetzner 路徑 vs 原料包路徑同一 T 逐位相同）。
5. Worker dispatch 角色（另 PR，需你核准）→ 連續 10 個交易日觀察（完成定義 #5）。
