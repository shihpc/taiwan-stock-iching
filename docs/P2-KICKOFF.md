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
| 6 | 個股池（回補範圍） | **甲：全市場 4 碼普通股**（twse＋tpex、非 `00` 開頭），PIT 池＝當日有價格列者；流動性門檻留 P3 預先登錄。**規模更正（同日）**：裁決時我寫「3,060 檔」是 `TaiwanStockInfo` 的**列數**；不重複代號 **2,149**（835 檔有多列＝產業別／日期異動，**11 檔同代號同時出現在 twse 與 tpex**＝轉板，PIT 池須以 T 日所屬市場為準，P3 定義池時要處理）。**2026-09-10 改寫（依 Hetzner 實測，見 #24／#25）**：本裁定只定**個股池**，**不是落地範圍**。落地（`raw_price_daily` 等四個全市場切片）保留普通股＋ETF＋特別股＋DR／ETN／REIT＋指數列，只排除權證（#24）；「4 碼純數字非 `00`」這個形狀**含 11 檔 4 碼 DR**（`9101`–`9188`），故 **4 碼池 ≠ 普通股池，需另濾 DR**（#25）。另須分清**兩張不同名單**：①**可計分標的**（個股六爻的對象；**含 ETF**，使用者明確要保留 ETF）；②**廣度母體 `N`**（B1.2:157：**僅普通股**，排除 ETF／權證／DR／特別股／興櫃）。本次只定落地；兩張名單的實作留待計分接資料時 |
| 7 | 切分與資料截止 | **甲：照 v1.2.2 §13.2 候選**——訓練 2021-01～2023-06／驗證 2023-07～2024-12／保留 2025-01～2026-08-31；暖機價格類 2020-01、基本面類 2019-06 起 |
| 8 | 進出場時點 | **甲：T+1 開盤進、T+1+h 收盤出、h＝10／20／40**（`docs/pre-registration.md` §1.2.1 由提案轉為裁定；凍結仍待 P2 校準完） |
| 9 | 大盤開盤價來源 | **甲：以證據定**——回補腳本附證交所官方日 OHLC 比對，與官方一致者為準；皆不一致再回問 |
| 10 | 除權息 | **甲：自算**——落地 `TaiwanStockDividendResult` 原始列自算還原係數；`TaiwanStockPriceAdj`（Sponsor 級）只作交叉驗證、不依賴 |
| 11 | 每日更新班跑哪裡 | **乙：Cloudflare Worker 排程 → 本 repo GitHub Actions 執行 → 狀態進 git**。連帶：①解除 §4 對 Worker 的一項限制；②**B3.2 重裁**為兩層儲存（見 `spec/P1-B3-replay.md` §B3.2）；③本 repo 的 workflow 仍**不加 cron**（裁定 3 不變），只開 `workflow_dispatch` 給 Worker 叫 |
| 12 | Hetzner 產物怎麼回 repo | 隨乙：每日產物由 Actions commit，不需 Hetzner push 權限；回補產物中要進 git 的小檔（兩份交易日曆、coverage、品質報告）一次性回傳（使用者貼回或 Hetzner push 一次皆可） |
| 13 | ATR14 平均法 | **甲：簡單平均**（spec 字面「14 日平均」，非 Wilder） |
| 14 | `P_hist` 250 日百分位 | **甲：含當日、平手取中位名次（mid-rank）、門檻線性內插** |
| 15 | B1.2 族 D 騰落線讀法 | **甲：`dev=(AD−MA_n)/N`，`x=dev/std_n(dev)`，母體標準差（ddof=0）** |
| 16 | 基差 c＝60 日中位數 | **甲：含當日** |
| 17 | `stale_days` 計數單位 | **甲：台北交易日**（週一沿用上週五＝0） |
| 18 | 擺動點平手 | **甲：平手也計為波峰／波谷** |
| 19 | 多日情境分平均 | **甲：含當日、可得日平均；可得日不足一半 → 缺值** |
| 20 | 延續事件語意（B2.4 族 C） | **甲**：突破＝收盤 > 前 n 日最高收盤；跌破＝收盤 < 前 n 日最低收盤；「期間」＝近 n 日；每次新高（新低）＝新事件、重置確認窗；確認失敗→50；**事件掃描用完整可得歷史、不錨掃描起點** |
| 21 | USD/TWD（台灣日曆）對齊上爻美股曆 | **甲：取觀測日 ≤ 對齊美股日的最近一筆** |
| 22 | 任一爻未知時方向分數 | **甲：缺值、不重配**（保守） |
| 23 | 不等權族（B1.6 族 A .35/.30/.35）缺子指標 | **甲：按權重重配**（與 `coverage_ratio` 權重口徑一致）；**B1.7「取其餘平均」字面同步改為「按權重重配」** |
| 24 | **落地過濾（2026-09-10）**：全市場單日切片 `TaiwanStockPrice` 一日回 **22,478 列**（原估 ~2,000），Hetzner 實測 2020-01-02 組成＝權證 20,208／ETF 223（含 2 檔已下市）／4 碼普通股 1,967（含 48 檔已下市、不在 info）／在 info 的 6 碼 REIT・ETN・DR・指數 30／5 碼特別股＋`TAIEX`／`Other` 20／首字非數字的產業指數 ~20 | **不要權證，要 ETF（含 6 碼 `00987A` 型）**。排除**當且僅當** 6 碼 ∧ 首字 ASCII 數字 ∧ 非 `00` 開頭 ∧ 不在 `info_ids`（`config.is_warrant_code`；版本記進 `sources.landing_filter`）。**同日驗收更正 lf1→lf2**：實查 `TaiwanStockInfo`（4,321 列）發現「權證不在 info」的前提不成立——6 碼數字開頭非 `00` 且在 info 的 118 檔中，`industry_category='所有證券'` 的 36 檔全是上櫃權證（名稱含「購」／「售」，前兩碼 70／71／73），且全 info 的 `所有證券` 就只有這 36 檔；故 lf2 的 `info_ids`＝`raw_stock_info` 代號**減去** `所有證券`（等同視為不在 info），排除它們是執行本裁定而非改裁定。條件 2（首字數字）的作用是「6 碼字母開頭且不在 info」的保險帶，**不是**為 `Cement`／`Rubber` 而設（它們在 info，條件 4 已保護）。**絕不可**改成「只留在 info 的代號」（會丟 48 檔已下市普通股＝存活者偏誤、不可逆）。殘餘風險：①2020 後已下市的 DR／ETN／REIT 會被誤殺，可接受（不在池、不進指標）；②日後 `所有證券` 若用於非權證會被誤殺（今日 36/36 皆權證）。落地保留的不等於可計分：DR 4 碼與 6 碼皆照常落地，池另濾（#25）。實作：`scripts/backfill_hetzner.py` `run_dataset` 在 `record_success` 前套用、舊版本／未濾落地的 DB 守門中止、`info_ids` <3,000 中止、`raw_stock_info` 缺 `industry_category` 欄中止（不退化 lf1）、`sources.info_ids_sha` 名單指紋不一致中止、`price_daily` 濾後 <1,500 列記 `failures(too_few_rows)` 不寫 coverage、列缺 `stock_id` 中止、`report` 印「落地過濾 lf2：已濾 N 列（權證；累計）」；`docs/BACKFILL-RUNBOOK.md` §4 |
| 25 | **4 碼 DR 是否進個股池（2026-09-10）**：Hetzner 實查 `TaiwanStockInfo`，4 碼純數字非 `00` 的代號裡有 **11 檔存託憑證** `9101,9102,9103,9104,9105,9106,9110,9136,9151,9157,9188`（`industry_category='存託憑證'`）；DR 同時有 4 碼與 6 碼（`910322` 型）兩種，裁定 #6 的形狀條件會把 11 檔收進池 | **甲：4 碼 DR（`9101`–`9188`，11 檔）不進個股池；廣度母體 `N` 亦排除 DR（B1.2:157，spec 明文、無選擇空間）。落地仍保留 DR 原始列（4 碼與 6 碼皆是），排除只發生在讀取端的名單建構。** 理由：與 B1.2:157「排除 ETF／權證／DR／特別股／興櫃」一致；DR 是境外公司存託憑證，財報／營收口徑與本國股不同，B2 基本面族會算出無意義的值。實作：`universe.pool_from_info` 以 `industry_category=='存託憑證'` **或** 4 碼且 `91` 開頭（已下市 DR 不在今日快照的後備）兩條件聯集排除；本容器同日免 token 實打 4,321 列：4 碼 `91xx` 恰 11 檔全為存託憑證、36 檔存託憑證全以 `91` 開頭（`src/iching/universe.py` docstring、`tests/test_pool_dr.py`） |
| 26 | **VIX 只有 2026-03-02 起的歷史（2026-09-12）**：Hetzner 實查 `raw_vix` 143,766 列、`min(date)=2026-03-02`、`max=2026-08-31`，2020-01-01~2026-02-28 完全沒有（FinMind `TaiwanOptionVix` 以年為區間查詢，2020–2025 六鍵全回 200 空陣列＝`empty_unexpected`，非權限、非網路）。VIX 只進**一個**計分分量：大盤五爻族 C 波動率（`spec/P1-B1-market.md:230`，族權重 .30；五爻爻權重三期間皆 .10，`src/iching/score/params.py:265-267`）→ **佔大盤總分 3%**。缺它的出口是通用規則 `:313`（整族缺→剩餘族權重按原比例正規化，A .40／B .30 → .571／.429，`coverage_ratio=0.70`），0.70 ≥ `:314` 的 0.5 門檻，五爻仍為正式爻態。`F-高波動` 旗標另有明文降級（`:324` 第 ② 段：大盤 ATR÷I 的 250 日百分位），故旗標有值、不是 unknown，`P1-B5-dimensions.md:105` 的「VIX 與 ATR **皆缺**＝1 個缺因」不成立 → 名額 ×0.5 **不會**被觸發。**族 C 自己沒有降級路徑**（`:324` 那條 ATR 降級的主詞是旗標不是分量，全規格查無第二處），把 ATR 套到族 C 上沒有規格依據 | **丙：回測期一律不用 VIX（含 2026-03 之後），上線後 VIX 累積滿 250 個交易日再以新 `model_version` 啟用。** 理由：訓練／驗證／保留段內部一致、不引入 2026-03-02 的制度斷點（規格對「同一回測段內某分量中途上線」無任何一行規定），五爻全期恆為 reweighted、`coverage_ratio` 恆 0.70、`F-高波動` 恆走 ATR 路徑；VIX 不被永久丟棄，但啟用是一次**明示的模型變更**、要重新驗證，不是悄悄漂移——與 CLAUDE.md 約定 5「保留段一經動用即消耗」同一精神。**實作要求**：啟用與否必須是進`model_version` 指紋的欄位（比照現有 `Rules` 欄位一律進指紋的做法），**不得**用讀不讀得到資料來隱式決定——否則某天 VIX 補齊就會自動改變分數而 `model_version` 不變。`vix` 資料集**照常回補落地**（原始列是事實、要留著），排除只發生在計分讀取端 |
| 27 | **「歷史長度不足以算百分位」規格無明文（2026-09-12）**：規格只寫前置條件（`spec/P1-B3-replay.md:20`／`:30`、`stock-iching-S1-supplement.md:213`「須備妥 250 個交易日」），沒寫不滿足時怎麼辦；缺值原因碼只有二分（`denominator_zero`／`missing`，S1 §A2.3）。實作端 `src/iching/score/transform.py:32` 早已自造第三碼 `REASON_INSUFFICIENT = "insufficient_history"`（`:135` `P_hist`、`:160` 百分位門檻，另 `market.py` 十餘處），**無規格背書**。且 `spec/P1-B1-market.md:324` 的 `F-高波動` 降級表第 ① 段只寫「VIX」，未區分「序列存在但 <250 日」——照字面讀第 ① 段可用、走下去卻算不出門檻，規格沒把這種情形導向第 ② 段 | **甲：補第三個原因碼 `insufficient_history`，並在 `:324` 第 ① 段明文加「有效樣本 <250 → 落到 ②」。** 理由：`missing` 是上游沒給、`insufficient_history` 是時間會自己解決，混在一起會讓上線初期與暖機期的報表看起來像資料源故障，少一個診斷維度。**落點（刻意不改 S1）**：三分寫進 `spec/P1-B2-params.md` §B2.8 政策**第 3 點**（就地擴充、**不新增條目**，避免 `政策第 6 點`／`§B1.0 政策第 9 點` 這類既有交叉引用被renumber 打斷），並在第 1 點的出處括號同步；S1 §A2.3 是二分的原始出處、**保持不動**（基準文件不回頭改寫），依優先序 P1-B2 ＞ S1 以新條為準。字面值與實作常數一致。B5／`dimensions.json`／B3 無原因碼列舉，**不需重生成、無維度影響**（grep 實查零命中）。**範圍外記錄（本次不動）**：實作另有第四碼 `REASON_CONTRACT_ROLLED = "contract_rolled"`（`transform.py:34`）；`spec/P1-B1-market.md:239` 有寫「換月日標 `contract_rolled=true`、族 B 降級為缺值」，但沒說它是**原因碼**。屬敘述層級的不完全對齊，留待下批一併校訂。 |

> 第 13–23 列＝2026-09-10 使用者對計分引擎 11 個真規格缺口的裁決（「全甲」），每條寫進 `params.py` 的
> SPEC-NOTE → 裁定、綁進 `model_version`，並於凍結前列入 `docs/pre-registration.md`。
> 第 24–25 列＝2026-09-10 Hetzner 實測後對**落地範圍**與**個股池**的兩項裁定；只動落地路徑與池名單建構，
> 計分引擎（`src/iching/score/`）與 `spec/` 未動。

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

## 8. 計分引擎驗收條件（2026-09-10 寫於動手前，CANON 第 3 條）

**目標路徑**：`/home/user/taiwan-stock-iching/src/iching/score/`（純函式）＋ `tests/test_score_*.py`。
**回補腳本已交付（PR #1 合併，`1d85352`）**，本項不等 Hetzner 回補跑完——計分是純函式，用合成資料就能測。

怎樣算完成（逐條可勾）：

| # | 條件 | 怎麼驗 |
|---|---|---|
| 1 | B1.1–B1.8 大盤六爻＋旗標、B2.1–B2.7 個股六爻，**每個子指標一個純函式**，設定物件含 `c`／`d`／`native_range`／`calibrated`（P1 起點值照抄、`calibrated=false`） | 對照 B1／B2 逐公式核對；設定檔與 B2.8 欄位一致 |
| 2 | `S(x;c,d)`＝`100/(1+exp(−(ln(7/3)/d)(x−c)))`、`clip_3d`、`N(v;lo,hi)=7.30+(v−lo)/(hi−lo)×85.40` 精確實作 | 單元測試用 spec 內數字例（`S(c)=50`、可達區間 7.30／92.70、`N` 中點＝50.0 等） |
| 3 | 輸出列的鍵＝`spec/dimensions.json` 的 `scores_db_row`，**程式直接讀該檔取鍵、不得另抄一份** | 測試：改 dimensions.json 加一鍵 → 輸出缺鍵即紅 |
| 4 | 六爻 → `lines_bottom_up` → `spec/hexagrams64.json` 查 `king_wen`；`flip_direction`／`from_king_wen`／`to_king_wen` 依 B4 | 64 卦全表往返測試 |
| 5 | **決定性**（B3.3）：同輸入同版本三元組重跑逐位相同；改 `model_version` 後輸出必須不同 | 測試實跑兩次比對 |
| 6 | 缺值語意依 B1.6／B2（`stale_days`、族降級）；**缺值不得靜默成 50** | 測試：抽掉一族輸入 → 該爻標缺值而非 50 |
| 7 | 上爻美股日對齊走 `calendar.us_session_closed_by()`，不另寫 | grep 唯一實作 |
| 8 | 計分函式**只吃純 dict／pandas，不碰 DB**；DB 讀取層另一個模組（供回補層），每日班層日後餵同一組函式（兩層 parity 前提） | `src/iching/score/` 內 grep `sqlite3` 零命中 |
| 9 | 全部測試免 token 免網路；`checks.yml` 加 `python -m pytest tests/ -q`（目前 CI 未跑 pytest） | Actions run 綠。**紀錄（2026-09-10）**：`79f929e` 的 CI run #6 **紅**（`requirements-dev.txt` 漏 `requests`，本容器本來就裝有、本地綠 CI 紅）；`7e4e962` 補列後 run #7 **綠** |
| 10 | **不做**：校準（c／d 維持 `calibrated=false`）、回測、網站、每日班 workflow | — |

驗收：fresh-context subagent 綁確切 commit，逐公式對照 B1／B2、實跑決定性測試與缺值測試。

**驗收紀錄（2026-09-10）**：`79f929e` 初稿 → 4 必修＋11 真規格缺口；`02bc754` → 2 必修；
`a93fb1a` **必修為空、可合併**（權重／窗長／c／d 逐格對表一致；重構前後 145 格子指標數值 0 差異；
Rules 41 欄逐欄消費測試實測「改回寫死」各自紅）。**唯一未結**：B2.4 族 C 延續指標的事件格點錨在
掃描起點（同一走勢在不同 T 得系統性不同分數），重寫等使用者裁決事件語意（§5 缺口第 8 題）。
11 個規格缺口清單見 `79f929e` 驗收報告；裁決後寫進 `params.py` 的 SPEC-NOTE → 裁定，並綁進 `model_version`。
**`16a9c85`（2026-09-10）：11 條裁決全部落實、延續指標重寫，驗收必修為空、可合併**——11 條各以獨立合成序列手算相符；
延續指標以 10 組邊界序列＋週期走勢 110 個 T 驗「同相位不同 T 輸出相同」；13 個新 `Rules` 欄位全進 `model_version`。
§8 十條至此**全部達成**（第 9 條 CI 綠自 `7e4e962` 起）。

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
