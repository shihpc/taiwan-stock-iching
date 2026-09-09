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
- **改既有 repo 的任何檔案** → 非 P2；本專案為獨立新 repo。
  **唯一解除（2026-09-09 裁定乙）**：`taiwan-flow-live-v2/worker/src/index.js` 加一個 dispatch 角色
  （比照既有 `news` 角色，只多一條 `workflow_dispatch` 目標），供每日班排程用。除此之外既有 repo 仍不改。

## 5. 使用者裁定紀錄（2026-09-09）

| # | 題目 | 裁定 |
|---|---|---|
| 1 | P0-B 授權、repo 名稱與可見性 | **`taiwan-stock-iching` · public**，已建立 |
| 2 | `shihpc/taiwan-backtest` 的關係 | 先盤點再決定 → 盤點結論：**各走各的**，單向借 `block_boot_ci`＋`nw_se`（約 25 行）、事前註冊流程、R5 延遲進場檢定 |
| 3 | 排程 | **先建好但停用**——workflow 不加 cron |
| 4 | 歷史回補執行環境 | **Claude 寫腳本、使用者在 Hetzner 執行**（token 不離開 Hetzner）|
| 5 | CANON 是否納入新 repo | **納入，並拿掉寫死的 repo 數量**——已執行（`claude-harness` `eebfd92` 起，八個 repo 各一 PR 待合併，見 §7）|
| 6 | 個股池（回補範圍） | **甲：全市場 4 碼普通股**（twse＋tpex、非 `00` 開頭），PIT 池＝當日有價格列者；流動性門檻留 P3 預先登錄。**規模更正（同日）**：裁決時我寫「3,060 檔」是 `TaiwanStockInfo` 的**列數**；不重複代號 **2,149**（835 檔有多列＝產業別／日期異動，**11 檔同代號同時出現在 twse 與 tpex**＝轉板，PIT 池須以 T 日所屬市場為準，P3 定義池時要處理） |
| 7 | 切分與資料截止 | **甲：照 v1.2.2 §13.2 候選**——訓練 2021-01～2023-06／驗證 2023-07～2024-12／保留 2025-01～2026-08-31；暖機價格類 2020-01、基本面類 2019-06 起 |
| 8 | 進出場時點 | **甲：T+1 開盤進、T+1+h 收盤出、h＝10／20／40**（`docs/pre-registration.md` §1.2.1 由提案轉為裁定；凍結仍待 P2 校準完） |
| 9 | 大盤開盤價來源 | **甲：以證據定**——回補腳本附證交所官方日 OHLC 比對，與官方一致者為準；皆不一致再回問 |
| 10 | 除權息 | **甲：自算**——落地 `TaiwanStockDividendResult` 原始列自算還原係數；`TaiwanStockPriceAdj`（Sponsor 級）只作交叉驗證、不依賴 |
| 11 | 每日更新班跑哪裡 | **乙：Cloudflare Worker 排程 → 本 repo GitHub Actions 執行 → 狀態進 git**。連帶：①解除 §4 對 Worker 的一項限制；②**B3.2 重裁**為兩層儲存（見 `spec/P1-B3-replay.md` §B3.2）；③本 repo 的 workflow 仍**不加 cron**（裁定 3 不變），只開 `workflow_dispatch` 給 Worker 叫 |
| 12 | Hetzner 產物怎麼回 repo | 隨乙：每日產物由 Actions commit，不需 Hetzner push 權限；回補產物中要進 git 的小檔（兩份交易日曆、coverage、品質報告）一次性回傳（使用者貼回或 Hetzner push 一次皆可） |

### 2026-09-09 稍早列為「尚未執行」的兩項——已執行

> 本節原文（同日稍早）寫「CANON 拿掉寫死數字：需跨七個 repo 的同步 commit（未執行）」與
> 「`TARGET_REPOS` 目前不含本 repo」。兩項已於同日執行：`claude-harness` 分支 `claude/dazzling-maxwell-serk13`
> 改 `CANON.md`、`TARGET_REPOS` 納入本 repo、新增 `tools/check_canon_remote.py`；八個 repo 各一份副本＋
> 守門 hash 同批更新。**截至本次改稿全部仍在 PR、未合併到 main**——合併前 main 上的守門仍是舊值。
> 保留原文而非抹去，理由同 §1。

- **CANON 拿掉寫死數字**：需一個跨**七個** repo 的同步 commit（改 `claude-harness/CANON.md`
  ＋`tools/sync_canon.py` 的 `TARGET_REPOS`，跑 `sync_canon.py` 同步七份、更新七個守門 hash）。
- **`sync_canon.py` 的 `TARGET_REPOS` 目前不含本 repo**（P0-B 驗收發現）。後果：下次改 CANON 時
  本 repo 會被跳過、副本與 `EXPECT` 一起留在舊值 → **canon.yml 照樣綠燈卻已與正本分歧**，
  `--check` 也抓不到。這會打穿「CANON 守門」這個交付項的目的，須在 harness 端修。

## 7. 裁定乙的落地清單（2026-09-09）

**使用者要做的**（我做不到、也不該碰的）：
1. `GH_DISPATCH_TOKEN`（Worker 持有的 fine-grained PAT）加 `shihpc/taiwan-stock-iching` 的 Actions 讀寫。
2. `FINMIND_TOKEN` 放進本 repo 的 Actions secret。
3. 合併八個 CANON PR（順序無關；已試合併無衝突、合併後守門 hash 一致）。`taiwan-flow-live-v2` 的
   PR #5 要**先**合併，Worker 的 dispatch 角色才好另開乾淨的 commit，不與 CANON 改動混在同一個 PR。

**我要做的**（依序，每步各自驗收）：
1. 回補腳本（Hetzner，一次性）——起草中。
2. 本 repo 每日班 workflow：`workflow_dispatch` 觸發、無 cron、`concurrency.group` 同一組且
   `cancel-in-progress: false`（完成定義 #8）、產物 commit 進 `data/`，失敗走 `notify-failure`。
3. **兩層儲存的狀態格式與 parity 測試**（B3.2 重裁的實作）：每日班在 Actions 只能靠「git 內狀態＋當日 API」
   算出當日六爻分數，狀態要多小、含什麼，在收集器設計時定；Hetzner SQLite 與 git 狀態對同一日
   必須算出逐位相同的分數（比照 `taiwan-flows/tests/parity.py`）。
4. `taiwan-flow-live-v2` Worker 加 dispatch 角色（待 PR #5 合併後、待第 2 步的 workflow 存在後）。

## 6. P2 開工前必須先做的一件事（2026-09-09 新增；同日更正）

> **⚠ 2026-09-09 更正：本節原本的結論是錯的，已撤回。** 原文寫「TAIEX 的開盤價 100% 等於
> 前一日收盤 … 機制是指數由成分股計算、開盤瞬間沿用昨收」，並據此斷言大盤側不能用開盤進場。
> 三處都不成立：①100% 的母體是 `taiwan-backtest/data/taiex_daily_agg.csv` 的 `o0900` **一個欄位**，
> 不是「TAIEX 的開盤價」；②真成因是 `scripts/fetch_taiex.py:60-63` 取了 09:00 分 K 的 `open`
> （**推論**：該值看起來是開盤前參考價，無 FinMind 文件佐證），不是「指數的計算機制」——
> 同一根 bar 的 `close` 已是開盤**後**的指數值、可作近似，但**不等於官方開盤指數**，何者為官方未查證；
> ③本專案宣告的資料源 `TaiwanStockPrice`／`data_id=TAIEX`（`spec/P1-B1-market.md:137`）
> **有真實開盤價**（實打 2022-12-20~30，8 組相鄰日中 open==前收 為 0 組）。
> 更正後的完整敘述與實測數字見 `docs/pre-registration.md` §1.2.2／§1.2.3。

**仍然成立的部分**：那份 CSV 的 `o0900` 欄不可當開盤價，任何讀它的流程都會拿到 look-ahead。

**行動（已完成）**：「進出場時點定義」已寫進 `docs/pre-registration.md` §1.2.1，
標為**提案、待使用者確認**，凍結前不得當定案。大盤開盤價的來源另列 §1.2.3 待決
（兩條候選路徑在 2022-12-30 相差 61.74 點，以宣告源 `open` 為分母 ＝ 0.4353%；單日樣本）。
