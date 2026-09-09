# P2 開工前置：目標、完成定義、驗收方式

依 CANON 第 3 條「先寫驗收條件再動手」。本檔在動任何一行程式前寫成。

## 0. P2 的規格定義（v1.2.2 §14，逐字）

| 項目 | 內容 |
|---|---|
| **交付** | 歷史回補（Hetzner）、每日更新、收集器、持久狀態、品質報告、公告時間分布實測 |
| **授權邊界** | 需核准：**啟用 workflow 排程**、**Release 上傳**、**付費 AI**（可先關閉）|
| **驗收** | §16.1（公告漏收）、§16.2（並行寫入）、§16.3 |

## 1. 前置：P0-B 已完成（2026-09-09）

§14 的 **P0-B 初始化**是 P2 的硬前置，**已於 2026-09-09 完成**：
使用者授權並建立 `shihpc/taiwan-stock-iching`（public），骨架 commit `24657c0`，
`canon` 與 `checks` 兩支 workflow 首跑皆 success，fresh-context 子代理驗收確認
§14 的兩項驗收條件（canon.yml 綠燈、目錄與資料契約符合家族規範）皆成立。

> **本節原文（2026-09-09 稍早）寫的是「P0-B 尚未授權…`shihpc/` 下無任何名稱含 `iching`
> 的 repo，P0-B 確未執行」。** 那在寫下的當刻為真，但這份檔案隨骨架 commit 進了
> P0-B 建立的 repo，就地變成不實敘述。保留這段更正紀錄而非直接抹去，
> 是因為它正是 CANON 第 4 條（區分已驗證事實與推測、不憑印象當確定講）
> 在**文件會過期**這個面向的漏網——寫作當下正確 ≠ 永遠正確。

## 2. 本 session 的執行限制（必須先講清楚）

- **本 session 是雲端容器，無 Hetzner SSH**。P0-A 是使用者自己在 Hetzner 上跑腳本、把輸出貼回來完成的。
  P2 的「歷史回補（Hetzner）」**我無法在此直接執行**，只能：
  ①寫好腳本交給使用者在 Hetzner 跑；或②把回補改成在 GitHub Actions 跑（需 P2 的排程授權）。
- **`FINMIND_TOKEN` 不在本環境**（依 CANON 第 1 條，token 走 .env／Actions secret，且不得出現在對話輸出）。
  故任何需要真實 FinMind 資料的步驟，我在這裡都跑不了。
- **推論**：P2 若要在本 session 推進，實際可做的是「**寫出可交付的程式與 workflow**」，
  「**實跑**」必須落在 Hetzner（使用者執行）或 Actions（需授權）。CANON 第 6 條「沒實跑過不算完成」
  因此在 P2 會卡在授權與執行環境，**不是寫完程式就算 P2 完成**。

## 3. 完成定義（逐條可勾）

P2 視為完成，須全部成立：

| # | 條件 | 怎麼驗 |
|---|---|---|
| 1 | repo 建立且 `canon.yml` 綠燈 | Actions run 頁面 |
| 2 | 目錄與資料契約符合家族規範（`claude-harness/docs/data-contract.md`）| fresh-context 子代理比對 |
| 3 | `check_dims.py`／`tblcheck.py`／`inject_test.py` 納入 CI 且綠燈 | Actions run |
| 4 | 歷史回補**實跑完成**、產物落地、`coverage` 正確 | 子代理讀產物；抽日重播 |
| 5 | 每日更新班連續 **10 個交易日**每班有 `runs/collect/<date>-<band>.json` | §16.1 第 1 列 |
| 6 | 公告主旨層級缺漏率 ≤ 1%，每筆缺漏有原因碼 | §16.1 第 2 列，子代理跑比對腳本 |
| 7 | 公告發布時間分布實測（3 個月），晚於 21:30 占比 > 3% 即須加班次並重測 | §16.1 第 3 列 |
| 8 | 所有會 commit 的 workflow 宣告同一 `concurrency.group` 且 `cancel-in-progress: false` | §16.2 |
| 9 | 決定性重播測試通過（以 B3.1 清單重播 T，三個 horizon 各驗一次）| `P1-B3-replay.md` §B3.3 |
| 10 | **校準前**所有 c／d／權重／門檻仍標 `calibrated=false`；校準後才可改 true，且母體限訓練段 | `P1-B2-params.md` §B2.8 |

## 4. 不在 P2 範圍（避免範圍蔓延）

- 三期間回測、成本敏感度、事件探索 → **P3**
- 網站四入口、入口站卡、Worker 整合 → **P4**
- 盤中候選池 → **P5**
- **改既有五個 repo 的任何檔案** → 非 P2；本專案為獨立新 repo

## 5. 使用者裁定紀錄（2026-09-09）

| # | 題目 | 裁定 |
|---|---|---|
| 1 | P0-B 授權、repo 名稱與可見性 | **`taiwan-stock-iching` · public**，已建立 |
| 2 | `shihpc/taiwan-backtest` 的關係 | 先盤點再決定 → 盤點結論：**各走各的**，單向借 `block_boot_ci`＋`nw_se`（約 25 行）、事前註冊流程、R5 延遲進場檢定 |
| 3 | 排程 | **先建好但停用**——workflow 不加 cron |
| 4 | 歷史回補執行環境 | **Claude 寫腳本、使用者在 Hetzner 執行**（token 不離開 Hetzner）|
| 5 | CANON 是否納入新 repo | **納入，並拿掉寫死的 repo 數量**（未執行，見下）|

### 尚未執行

- **CANON 拿掉寫死數字**：需一個跨**七個** repo 的同步 commit（改 `claude-harness/CANON.md`
  ＋`tools/sync_canon.py` 的 `TARGET_REPOS`，跑 `sync_canon.py` 同步七份、更新七個守門 hash）。
- **`sync_canon.py` 的 `TARGET_REPOS` 目前不含本 repo**（P0-B 驗收發現）。後果：下次改 CANON 時
  本 repo 會被跳過、副本與 `EXPECT` 一起留在舊值 → **canon.yml 照樣綠燈卻已與正本分歧**，
  `--check` 也抓不到。這會打穿「CANON 守門」這個交付項的目的，須在 harness 端修。

## 6. P2 開工前必須先做的一件事（2026-09-09 新增）

盤點 `taiwan-backtest` 時實測發現：**TAIEX 的「開盤價」在 2005-2022 的 4440 個交易日中，
100% 精確等於前一日收盤**（隔夜跳空標準差 0.013%，當日開→收標準差 1.138%，差 87 倍）。
機制是指數由成分股計算，開盤瞬間多數成分股尚未成交、沿用昨收。

**後果**：P3 若假設「訊號出現後隔日開盤進場」且用 TAIEX 計算大盤側，**那個進場價是虛構的**。
個股有真實開盤集合競價、不受影響；大盤側會。

**行動**：「進出場時點定義」與「TAIEX 開盤不可用」必須寫進 `docs/pre-registration.md`
**並在凍結之前**——凍結後不得修改。
