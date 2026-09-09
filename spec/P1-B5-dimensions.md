# B5｜全域維度表（鍵宣告的唯一事實來源）

版本：P1-B5 v1　日期：2026-09-08

**優先序（R5d 新增，R5g 加入 B5；五份 P1 文件逐字相同）**：`P1-B5` ＞ `P1-B4` ＞ `P1-B3` ＞ `P1-B2` ＞ `P1-B1` ＞ `S1a` ＞ `S1` ＞ `v1.2.2`（右者為左者的基準，衝突時**以左者為準**）。**凡「per X」的鍵／維度宣告，一律以 `P1-B5-dimensions.md` 為唯一事實來源。**
v1.2.2 §4（參數表）、§5（權重）、**§9.2（環境×門檻×名額，已被 S1 §A1 全面取代——含「門檻加 10 分」、五陽條件、名單上限 20／5／10／20；R5g 補）**內凡與 P1 重疊者，**一律以 P1 為準**；該三節保留原文僅作沿革，**不得據以實作**。
已知會做出不同參數的衝突點（照 v1.2.2 實作即錯）：個股二爻距離（v1.2.2 只有 MA20／MA60、d 1／1.5 ← B2.2 為 MA5/10/20/60、0.6/0.8/1.0/1.5）、個股二爻斜率（v1.2.2 寫死 MA20 五日變化、d=0.5 ← B2.2 為 MA_長 n 日變化、d 待 P2 校準）、五爻外資（v1.2.2 為 20 日 d=2%／5 日 d=4% ← B2.5 為三期間各一組）、五爻持續性（v1.2.2 只有 10 日 d=2 ← B2.5 中期為 20 日 d=3.34）、四爻序 1 回撤條件（v1.2.2 `5日回撤 ≤ 2` ← S1 §A2.2 `0 < n日回撤 ≤ 2`）、**個股三爻**（v1.2.2 單一組 20日 d=5pp／5日 d=3pp、產業 d=5pp、加速度「近5−前5」d=3pp ← B2.3 三期間各一組：產業 4/5/8pp、加速度 3/4/5pp）、**個股四爻族 C 延續**（v1.2.2 寫死「突破後第 3 日」 ← B2.4 為 3/5/10 日、基準 20/60/120 日最高收盤）、**個股四爻族 A 中期**（v1.2.2 §4.3 三期間都用情境表 ← B2.4 中期改用 `ln(漲跌日均量比)`＋OBV 斜率；註：v1.2.2 §5.1 與 B2.4 一致，是 §4.3 與 §5.1 自己不一致）、**個股初爻短線**（v1.2.2 三月合計 YoY d=15pp ← B2.1 短線改最新單月 YoY d=20pp）。（R5e 補：前五點為 R5d 所列，後四點為 R5e 驗收另行找到；此清單**未必窮盡**，實作時以優先序為準、不得回頭引用 v1.2.2 §4／§5 的參數。）

## B5.0 為什麼有這份文件

「宣告的鍵少於實際的變動來源」這個缺陷在 R1–R5h 的驗收中**連續九次復發**：
①要求六爻可達區間一致（離散表取不到端點）②量法含缺值重配卻登錄滿覆蓋值 ③粒度只到「爻」漏掉族內轉換隨期間改變 ④把宣告值域當可達值域 ⑤可達邊界漏市場維度 ⑥個股側也需市場維度 ⑦`T0` 漏期間維度 ⑧參數字典無 `market` 欄 ⑨**本文件 R5g 版的「聚合量可達區間」列自己漏掉 `scope` 與 `direction`**——第九次就發生在為根治它而寫的表裡。

九次的根因相同：**每次都在個別位置補一個維度，而沒有一個地方宣告「維度的全集」**；第九次進一步證明，**光有「一個地方」不夠——那個地方若是人手維護的散文表格，它自己就會犯同樣的錯**。

故 R5h 起：**機器可讀正本＝`dimensions.json`**，本 md 由 `gen_b5.py` 生成、**不得手改**，規則由 `check_dims.py` 實作（v1.2.2 §16.5「鍵維度涵蓋」列即跑它）。

**規則（機檢，`check_dims.py`；v1.2.2 §16.5「鍵維度涵蓋」列）**：
1. 全案**任何**登錄檔、資料表索引、參數字典列、驗收條件的鍵，**必須引用 §B5.1 的維度名**，不得自創。
2. 任一鍵若**少於**其標的在 §B5.2 宣告的維度集，即為不合格。
3. **某維度在第一版塌縮為 1 時，仍須宣告並在值欄寫死**（例：`horizon='short'`），**不得省欄**。
4. 「兩市場實算相同」不是省略 `market` 的理由；「只出短線名單」不是省略 `horizon` 的理由。
5. **（R5h 新增）宣告筆數必須等於各維度基數乘積**（扣除顯式 `exclude`）。堵的是「鍵改成 48 格、同句話裡格數還寫 8」這類漂移——R5h 驗收在三處抓到此漂移，且本規則上線後**立刻抓到第四處**（`state` 補上第五個值 `undetermined` 後 T0 應為 60 而非 48，經裁定 `undetermined` 不出名單、以顯式 `exclude` 宣告後回到 48）。
6. **（R5h 新增）§B5.2 未登錄的標的一律判不合格。** 堵的是「標的層空轉」——規則 2 只比對已登錄者，新的登錄檔或資料表若沒被寫進 §B5.2，機檢沒有比對對象、照樣全綠。
7. **（R5h 新增）維度名不得語意碰撞。** 已解的兩對：`direction`（多空）vs `flip_direction`（爻變方向，B4.7 原也叫 direction）；`coverage`（滿覆蓋／缺值重配二值）vs `coverage_ratio`（連續比值，B1.7 <0.5 判未知）。碰撞比漏宣告更危險：**機檢會通過但語意錯**。
8. **（R5h 新增，R5j 實作）值域也要完整，不只維度名。** 少一個值同樣會讓實作漏格（`state` 的 `undetermined` 即為實例）。R5j 起以 `_frozen_values` 凍結所有封閉維度的值域：改名或增刪值即報錯——值域語意漂移（如 `direction` 由 `long/short` 改成 `up/down`，基數不變）不會被其他任何規則發現。
9. **（R5j 補記）程式另有三條未列於本清單的規則**，錯誤訊息會標號：`2r`（鍵不得為空或重複維度）、`3f`（五支旗標必帶 `market`／`horizon`／`direction`）、`9`（孤兒維度——宣告了卻無任何鍵引用，通常是「正本改名／新增、下游沒跟」的徵兆）。**規則的正本是 `check_dims.py`**，本清單為說明。
10. **（R5j）刻意不檢查的邊界**：「過度加鍵」（多宣告一個不必要的維度且 count 同步改對）**不判不合格**——連續九次復發的是**少**宣告，多宣告不會讓實作者做錯，判它不合格反而會擋掉正當的新增。此邊界由 `inject_test.py` 以斷言明示，不是遺漏。
 
## B5.1 維度全集

**本節與 §B5.2／§B5.3 由 `dimensions.json` 經 `gen_b5.py` 生成，`P1-B5-dimensions.md` 不得手改**——R5h 驗收指出：Markdown 表格夾散文無法穩定解析，機檢會退化回人工掃描（即已失敗八次的做法）。

| 維度 | 值域 | 說明 |
|---|---|---|
| `market` | `twse`／`tpex` | 加權／櫃買。兩市場 d 各自校準，凡受 d 影響者皆帶此維度 |
| `horizon` | `short`／`swing`／`mid` | 三期間 |
| `line` | `1`／`2`／`3`／`4`／`5`／`6` | 爻位，初→上 |
| `family` | `A`／`B`／`C`／`D`／`E` | 族 |
| `indicator_id` | （開放值域） | 子指標識別碼（開放值域） |
| `scope` | `market_index`／`stock` | 大盤／個股兩套權重與參數（B1.7 vs B2.7） |
| `stock_id` | （開放值域） | 個股代號；大盤列以 __MARKET__ 佔位，不得留空 |
| `industry` | （開放值域） | 產業。B3.1 #8 產業聚合值、B2.3 族 B、B2.6 族 B 皆吃它（R5h 補） |
| `coverage` | `full`／`reweighted` | (甲) 滿覆蓋／(乙) 含缺值重配。與連續量 coverage_ratio 不同名 |
| `coverage_ratio` | （開放值域） | 連續比值＝該爻實得權重÷應有權重，<0.5 判未知（B1.7）。**非鍵維度、是欄位名**：R5h 改名以解與列舉型 `coverage` 的碰撞（同一張表同一欄名兩種型別＝機檢會通過但語意錯）。R5i：下游 `P1-B1-market.md` 與 `P1-B2-params.md` 已同步改名。 |
| `direction` | `long`／`short` | 多空。做空分數獨立計算、非 100 減做多（v1.2.2 §9.3） |
| `flip_direction` | `yang_to_yin`／`yin_to_yang` | 爻變方向（B4.7 formation_paths）。R5h 改名以解與 direction 的碰撞 |
| `state` | `S1`／`S2`／`S3`／`S4`／`undetermined` | 大盤基本狀態；undetermined＝初爻或二爻未知（S1 §A1.1） |
| `segment` | `train`／`valid`／`holdout` | 訓練／驗證／保留段（R5h 補：B5.2 原已使用但未宣告） |
| `aggregate_name` | `base_score`／`inner_trigram_score`／`outer_trigram_score`／`event_shadow_score` | 聚合量名（R5h 補） |
| `market_type` | `stock`／`index` | 卦文用；B4.1 輸入、B4.5 兩獨立字串、B4.8 A6（R5h 補） |
| `model_version` | （開放值域） | 規則與權重版本 |
| `data_version` | （開放值域） | 含 FinMind 校正批次 |
| `text_version` | （開放值域） | 卦文 v0.2 |
| `event_id` | （開放值域） | 事件識別（首見時凍結） |
| `version_no` | （開放值域） | 事件版本序號 |
| `tpe_trading_date` | （開放值域） | 台北交易日 |
| `us_trading_date` | （開放值域） | 美股交易日。上爻整族走此曆（B1.6），R5h 補：原只登錄台北曆，上爻視窗無曆可查 |
| `calendar` | `tpe`／`us` | 交易日曆別。上爻整族走美股曆（B1.6），其餘走台北曆（R5i 補：原 us_trading_date 為孤兒，無標的引用） |
| `king_wen` | `1`／`2`／`3`／`4`／`5`／`6`／`7`／`8`／`9`／`10`／`11`／`12`／`13`／`14`／`15`／`16`／`17`／`18`／`19`／`20`／`21`／`22`／`23`／`24`／`25`／`26`／`27`／`28`／`29`／`30`／`31`／`32`／`33`／`34`／`35`／`36`／`37`／`38`／`39`／`40`／`41`／`42`／`43`／`44`／`45`／`46`／`47`／`48`／`49`／`50`／`51`／`52`／`53`／`54`／`55`／`56`／`57`／`58`／`59`／`60`／`61`／`62`／`63`／`64` | 卦序 1–64（王序）。R5j 補：B4 的 S2／§B4.7 關聯鍵／A3／A4 全以它為鍵，原只在 hexagram_text.note 用散文帶過，違反 B5.0 規則 1。值以字串列舉（`"1"`…`"64"`）與 `line` 同慣例，僅為值域表示法；產物欄位型別以 `P1-B4-hexagram-text.md` 為準（整數）。 |

## B5.2 逐標的的必要維度

| 標的 | 必要維度 | 宣告筆數 | 備註 |
|---|---|---:|---|
| `indicator_params`　子指標參數（c／d／native_range／窗長） | `market`　×　`scope`　×　`horizon`　×　`line`　×　`family`　×　`indicator_id` | — | 參數字典每列必備。**R5i 補 `scope`**：`scope` 的 note 自陳「大盤／個股兩套權重與參數」，但原鍵沒有它，大盤二爻族 A 與個股二爻族 A 在原鍵下同格，只能靠「indicator_id 全域唯一」硬撐，而全案沒有任何地方規定它唯一。 |
| `line_score`　爻分數 | `market`　×　`horizon`　×　`line`　×　`stock_id`　×　`tpe_trading_date` | — |  |
| `line_reachable_range`　爻可達區間 | `market`　×　`horizon`　×　`line`　×　`scope`　×　`coverage` | 144 |  |
| `aggregate_reachable_range`　聚合量可達區間 | `market`　×　`horizon`　×　`scope`　×　`direction`　×　`coverage`　×　`aggregate_name` | 192 | R5h 更正：原 48 漏掉 scope 與 direction（第九次復發） |
| `hysteresis_state`　遲滯／連續次數狀態 | `market`　×　`horizon`　×　`line`　×　`stock_id`　×　`tpe_trading_date` | — | R5h 補 date：B3.1 #7 要的是 T−1 的狀態，無 date 只能存『現在』 |
| `formation_path`　形成路徑 | `market`　×　`horizon`　×　`stock_id`　×　`tpe_trading_date`　×　`line`　×　`flip_direction` | — | R5i 補 `line` 與 `flip_direction`：形成路徑是逐爻逐變向的（B4.3）；同時解掉 flip_direction 的孤兒狀態。 |
| `industry_aggregate`　產業聚合值 | `market`　×　`horizon`　×　`industry`　×　`tpe_trading_date` | — | R5h 補：B3.1 #8 明列為重播必備，原整列缺席 |
| `event_version_chain`　事件版本鏈 | `event_id`　×　`version_no` | — | R5h 補：S1 §A3／S1a 補註 2；重播須取 available_at ≤ T 的最新版本 |
| `threshold_T0_N0`　T0／N0 | `state`　×　`direction`　×　`horizon`　×　`market` | 48（排除 state=undetermined） | R5h：`state=undetermined`（初爻或二爻未知，S1 §A1.1 的第五個狀態）**不出名單**——最保守處置，故無該格；此排除為顯式宣告，由 check_dims.py 規則 5 驗算。48 ＝ 4×2×3×2。 |
| `flag`　旗標 | 見 §B5.3 | — | 五支不同構，見 flags 段 |
| `clip_ratio_stat`　截斷比例統計 | `market`　×　`horizon`　×　`indicator_id`　×　`segment` | — |  |
| `scores_db_row`　scores.db 每列 | `market`　×　`horizon`　×　`stock_id`　×　`tpe_trading_date`　×　`model_version`　×　`data_version`　×　`text_version` | — | R5h 補版本三元組：B3.3『版本綁定』測試要求改 model_version 後同日結果不同，無版本欄兩份會撞鍵、存不下來比 |
| `hexagram_text`　卦文靜態文本 | `king_wen`　×　`market_type`　×　`line` | 768 | count ＝ king_wen 64 × market_type 2 × line 6 ＝ 768（R5j 更正：原 12 漏掉 king_wen，宣告數不是實際列數） |
| `trading_calendar`　交易日曆 | `calendar` | 2 | R5i 補：`P1-B3-replay.md` #10 明列「兩份交易日曆」為重播必備，原整列缺席（B5.0 規則 6 判不合格，但規則 6 當時無程式） |
| `upper_line_asof`　上爻資料日對應 | `market`　×　`horizon`　×　`tpe_trading_date`　×　`us_trading_date` | — | 每個台北交易日，上爻實際採用的美股交易日（B1.6「截至台北 T 日 08:00 已收盤的最近一個美股交易日」）＋`stale_days`。R5i 補：原 us_trading_date 無任何鍵引用 |
| `p_cs`　P_cs 橫斷面百分位 | `market`　×　`horizon`　×　`stock_id`　×　`tpe_trading_date` | — | R5i 補：消費端＝排名層與過熱旗標（`P_cs ≥ 95`）。**不套 N、保持原生 0–100**（政策第 6a 點） |
| `threshold_revalidation`　門檻行為重驗（八項差異） | `market`　×　`horizon`　×　`direction` | 12 | R5i 補：v1.2.2 §16.5「門檻行為重驗」列要求按此鍵分組呈現，屬 B5.0 規則 1 明文涵蓋的「驗收條件的鍵」 |

> **筆數由 `check_dims.py` 規則 5 驗算**＝各維度基數乘積（扣除顯式 `exclude`）。不符即機檢不過。

## B5.3 五支旗標的鍵

| 旗標 | 必要維度 | 備註 |
|---|---|---|
| `F-臨界` | `market`　×　`horizon`　×　`direction` |  |
| `F-廣度擴張` | `market`　×　`horizon`　×　`direction` |  |
| `F-廣度收縮` | `market`　×　`horizon`　×　`direction` |  |
| `F-高波動` | `market`　×　`horizon`　×　`direction` | R5h 撤回原『無 market』豁免：降級路徑用大盤 ATR÷I 的 250 日百分位，TAIEX／TPEx 各一條，走降級時即逐市場 |
| `F-分歧` | `market`　×　`horizon`　×　`direction` | R5h 撤回原『無 market』豁免：判定式第二個 disjunct（內卦≥55 且外卦≤45）是該市場自己的六爻，加權可能觸發而櫃買不觸發 |

**R5h 撤回 R5g 的兩個豁免**：原宣稱 `F-高波動` 無 `market`（VIX 單一序列）、`F-分歧` 無 `market`（條件跨市場），**兩個都錯**——前者的降級路徑用大盤 ATR÷I 的 250 日百分位、TAIEX／TPEx 各一條；後者判定式的第二個 disjunct（內卦 ≥55 且外卦 ≤45）是該市場自己的六爻，加權可能觸發而櫃買不觸發。五支旗標現皆帶 `market`。

## B5.4 旗標 `unknown` 的計數口徑（R5h 改寫）

S1a §3.3 改為按方向判定後，五支旗標的 `unknown` **全部**會被解析成「該方向視為成立／不成立」，於是「該方向仍為 `unknown` 的支數」**恆為 0**，B1.8 規則 3 與 S1a 的「同時有兩個以上旗標 `unknown` → 名額再 ×0.5」變成**永不觸發的死碼**——連「`F-高波動` 為 `unknown` → ×0.5」都被一併吃掉。這與 S1a §3.3 的標題「旗標缺值**不得默認為沒有風險**」直接相反：

> **×0.5 是「資料不足」的額外懲罰，疊在保守預設之上，不是保守預設的替代品。**

**正確口徑＝按「獨立缺因」計數**（R5h 裁定，取代 R5g 的「按方向的 unknown 支數」）：
1. 一個**缺因**＝一個獨立的資料缺失來源，不是一支旗標。例：T−5 state 不存在＝**1 個缺因**（它同時使 `F-廣度擴張` 與 `F-廣度收縮` 為 `unknown`，兩支同根同源、永遠成對）；VIX 與大盤 ATR 皆缺＝**另 1 個缺因**。
2. **`F-高波動` 為 `unknown`（VIX 與大盤 ATR 皆缺）**，**或** **獨立缺因 ≥ 2** → 名額 ×0.5。（R5i 補回前一個 disjunct：R5h 版只寫「獨立缺因 ≥ 2」，把 `P1-B1-market.md` §B1.8 規則 3 與 S1a §3.3 都有的「`F-高波動` 為 `unknown` 即 ×0.5」刪掉了；依 §B5.4 自己的計數「VIX 與 ATR 皆缺＝**1** 個缺因」，**只缺 VIX＋ATR、T−5 state 正常**時正本判不觸發、兩個下游判觸發，而優先序 `P1-B5 > P1-B1 > S1a` 讓正本贏→ 實作者會漏掉一個收緊條件，方向正是 §3.3 標題明令禁止的「旗標缺值默認為沒有風險」。）
3. 保守預設（按方向解析成成立／不成立）**照常套用**，兩者疊加、不互相取代。
4. 效果：上線頭 5 個交易日只有 T−5 state 一個缺因 → **不觸發** ×0.5（避免 R5g 指出的「每天必觸發」）；但若同時再缺 VIX 與 ATR → 兩個缺因 → **觸發**（避免 R5g 版本的「從不觸發」死碼）。

**須同步的下游**：`P1-B1-market.md` §B1.8 套用規則 3、`stock-iching-S1a-annotations.md` §3.3。
