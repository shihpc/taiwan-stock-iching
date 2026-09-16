# P2 公告收集器——驗收條件（2026-09-16，動手前寫；裁定 #43／#45）

規格來源：`spec/stock-iching-plan-v1.2.2.md` §7.2／§7.4／§12.1／§12.3／§12.4／§16.1、
`spec/stock-iching-S1-supplement.md` A3（事件版本鏈）、`docs/P0A-report.md` §5／修訂表第 7／11／12／13／15 列、
`docs/P2-KICKOFF.md` 完成定義 #5～#8。本文件只寫**P2 收集器**的範圍與驗法；AI 抽取／shadow 計分／事件探索屬 P3。

## 0. 前置：Actions 可達性（裁定 #45，失敗即停）

`.github/workflows/probe-mops.yml`（一次性、只 `workflow_dispatch`）跑 `scripts/probe_mops.py`：
mopsfin `t187ap04_L.csv`／`_O.csv` 與 mopsov `ajax_t05st01`（POST／GET 各試）三端點回 200、**非 WAF 擋頁**、
解析列數 > 0。任一失敗 → 停下來回報使用者，不改走 Worker 代抓。結果記在本文件 §5。

## 1. 範圍

| 項目 | 做 | 不做（本批） |
|---|---|---|
| 來源 | ①`t187ap04_L/_O.csv`（當日快照，含說明全文）②mopsov 全市場單日列表（只有主旨，供隔日核對與補主旨） | mopsov 詳情頁全文（參數未驗）、MOPS 新版 API、新聞 |
| 落地 | `data/events/<發言日 YYYY-MM-DD>.json.gz`，以**發言日期**為鍵（修訂表第 15 列：23:45 班延遲跨日不可用執行日） | 歷史回補（mopsov 隨時可回補，P3 需要時再做） |
| 事件模型 | A3.1 最小版本鏈：`event_id`／`version_no`／`version_id`／`content_hash`／`status`／`supersedes`／`fulltext_missing`／`first_seen_at`／`revised_at`＋原始欄位（市場、代號、名稱、發言日期時間、主旨、符合條款、事實發生日、說明） | `relates_to`、`type`／方向／級距（P3 抽取） |
| 留痕 | 每班 `runs/collect/<執行日>-<band>.json`：`scheduled_for`／`started_at`／`completed_at`／來源／收到／新增／更新版本／錯誤 | — |
| 核對 | 隔日首班以 mopsov 單日查詢（sii＋otc）對前一日事件庫比對，缺漏逐筆標原因碼（跨日發言／撤回／來源當時未出／WAF／晚於末班），寫 `runs/collect/<日>-verify.json` | — |
| 分布統計 | 每筆 `發言時間` 累積進 `data/events/_timing.json`（按日：各時段筆數、晚於 21:30 筆數），供 #7 三個月統計 | 門檻裁定（整體 vs 單日最差，P0-A 兩值判定相反，文件未裁） |
| 排程 | `collect-events.yml` 自帶 GH cron：台北 15:30／18:30／23:45 ＋隔日 08:30 補抓核對班；`workflow_dispatch` 帶 `band`／`date` 供補跑 | Worker dispatch 角色（另案） |
| commit | 沿用 `daily.yml` 的 inline 作法：`concurrency.group: iching-commit`／`cancel-in-progress: false`（完成定義 #8）、`pull --rebase` 重試、`notify-failure`（pipeline `iching-collect`）；**只寫 `data/events/` 與 `runs/collect/`**（§12.3 各 workflow 只寫自己的目錄） | Release 快照、manifest（§12.2，另案） |
| 執行地點 | 全部 Actions（判準 §6：資料不只在 Hetzner、非長跑） | Hetzner |

## 2. 固定的實作事實（P0-A 實測，改前先讀）

- 發言時間 `70003` 是無前導零 HHMMSS，補零至 6 碼（修訂表第 13 列）；「說明」含 `\r\n`，一律正規 CSV parser。
- TPEx CSV 欄名為英文（`Date`／`SecuritiesCompanyCode`／`CompanyName`…），TWSE 為中文（`出表日期`／`公司代號`／`公司名稱`…）；兩市各自映射，不得共用一張表頭。
- 民國年 7 碼 `1150907`；一律轉 `YYYY-MM-DD`。
- 當日 CSV 是滾動快照、**窗大小未驗**（P0-A 待驗證第 13 列）：第一週要記錄每班 CSV 的最早／最晚發言時間，決定 23:45 一班是否已足夠涵蓋全文。
- WAF 擋頁：HTTP 200／307 都見過、body 含「FOR SECURITY REASONS」；收集器對每個回應都要先判擋頁，擋頁＝該來源失敗（原因碼 `waf`），**不得當成空資料**。
- 冪等：同一輸入跑兩次事件檔逐位相同；內容未變不寫檔、不 commit（A3.3）。

## 3. 怎樣算完成（每條附驗法）

1. **probe 通過**（§0）——Actions run 的 step summary 三端點 OK；記錄 run id 與日期於 §5。
2. **離線測試**（`tests/test_collect_events.py`，免網路）：
   (a) 三個陷阱樣本（`70003`、`\r\n` 說明、TPEx 英文欄名）逐欄解析正確；
   (b) 同一輸入跑兩次 `data/events/<d>.json.gz` 位元組相同；第二次不產生任何寫入；
   (c) 同 `event_id` 更正版只有主旨 → `version_no` 遞增、舊版 `status=superseded`、新版 `fulltext_missing=true`（A3.2 規則 2）；
   (d) 執行時鐘為台北 00:10 而發言日期為前一日 → 落在前一日的檔（修訂表第 15 列）；
   (e) 來源回 WAF 擋頁 → 該班 `runs/collect` 記 `waf`、不寫事件、exit 非 0（觸發 notify-failure）；
   (f) 核對腳本對合成缺漏各給正確原因碼（五種各一例）；
   (g) `test_daily_run.py::test_daily_workflow_yaml` 不受影響；新增 `collect-events.yml` 的 yaml 測試釘 cron 四條、`concurrency`、`permissions`、notify-failure。
3. **線上**：合併後連續 **5 個交易日**每班都有 `runs/collect/<日>-<band>.json`，`data/events/<日>.json.gz` 每交易日一檔；任一班失敗有 issue。10 日由後續觀察累積（完成定義 #5 同型）。
4. **#6 核對**：第一週每日 verify 檔的缺漏率與原因碼分布列成表；≤ 1% 才算通過，**超過就照實回報、不硬過**。
5. **#7 分布**：`_timing.json` 從第一次實跑起累積；3 個月後統計整體與單日最差兩個值，門檻由使用者裁。
6. 全套 pytest 綠、ruff 乾淨、fresh-context 驗收綁 commit（T5 模板），改動者不自驗。

## 4. 未決（動手時遇到再問，不自行擴張）

- mopsov 單日查詢的參數形狀（POST／GET、`b_date` 格式）以 probe 實測為準，寫進 §5。
- 兩市同一公告在 CSV 與 mopsov 的主旨正規化規則（全形／空白／截斷），第一週實測後定。
- `_timing.json` 是否改進 `runs/`（不進事件檔）——先放 `data/events/`，理由是它是 #7 的交付物、不是留痕。

## 5. 交付紀錄

（待填：probe run、collect-events.yml 上線 commit、首週核對表）
