# P3 第 1 項：參考路徑池改 PIT——驗收條件（2026-09-16 動手前寫；§5 六題已裁定「全照建議」，裁定 #49）

出處：`docs/P3-KICKOFF.md` §3 #1、§7 第 1 項；裁定 #6／#44／#48 Q2；`docs/pre-registration.md` §1.1 兩份名單；`spec/P1-B3-replay.md` #5；
`spec/P1-B1-market.md:157`。盤點由 fresh-context 子代理實查（引用附 `檔案:行號`），主對話抽驗。

## 0. 現況（盤點事實）

| # | 事實 | 依據 |
|---|---|---|
| 1 | 參考路徑的池＝`feed.load_pool(universe)` 讀整張 `raw_stock_info` 快照經 `universe.pool_from_info` → **一次載入、1,618 日共用**（`scan_features.py:111`、`replay_io.py:89`） | `src/iching/feed.py:79-87`、`feed.py:17-18` 自陳「市場別取最新一列，不是 T 日所屬市場」 |
| 2 | 每日班的 320 日視窗重建 `rebuild_from_bundles` 用**同一支**過濾 `F.day_records(d, rows, pool, …)`（`daily_core.py:449`），池來自 `data/pool.json`（整張快照 5 欄原樣、過濾在讀取端） | `daily_core.py:113`「＝feed.load_pool 的同一步」 |
| 3 | 唯一已 PIT 的是排名池 `AdvTracker`（先 `eligible()` 再 `push_day`，`liquidity.py:13-22`）；廣度母體 vs 排名池已分家（`scan.py:10-20`）；**可計分標的（含 ETF）vs 廣度母體未分家**，ETF 計分另案（`docs/P2-REPLAY-PLAN.md:117`） | 同左 |
| 4 | `TaiwanStockInfo` 是單一快照（`config.py:263 strategy="single"`），**無上市日**、`date`＝最後被觀測日；市場轉換無專用資料集，只能由**殘留列**重建（162 檔同時有多個 `type`，殘留列 `date` ≈ 轉市生效日 +1～2 日，推測僅 2020 後） | `docs/P0A-report.md:157-164`、`config.py:30-33 OUT_OF_SCOPE` |
| 5 | `universe.pit_pool()`（合格代號 ∩ `is_traded_row`）已存在但**計分鏈零呼叫**，只有回補報表在用 | `universe.py:187-195`、`backfill_hetzner.py:1088` |
| 6 | `raw_price_daily` 無市場欄；`collect.stocks_from_rows` 不決定 market，market 一律由池 meta 決定（`feed.py:186`、`replay_state.py:493`） | 同左 |
| 7 | 池語意**不在** `model_version`（`score/params.py:183-196`）也不在 `params_sha`（`run_common.py:20-24`）指紋內 | 同左 |
| 8 | 今日快照：`data/pool.json` 4,323 列／3,149 個代號（含 ETF 520 列、DR 36 列、興櫃 549 列）；讀出的池 2,140 檔（twse 1,213／tpex 927）；**跨市場合格代號恰 11 檔**：3092／3652／4736／5236／6423／6426／6438／6446／6472／6589／8476 | 本次實跑 |
| 9 | 合成世界基礎：`tests/synth_db.py` `add_entrant_rows(info=…)` 可指定某檔在 E 日才進快照；`tests/test_daily_entrants.py` 23 例 | 同左 |

## 1. PIT 池的定義（提案）

以 T 為交易日，`PitPool` 由一份 `TaiwanStockInfo` 快照（含殘留列）建成，兩條路徑共用同一個物件：

1. **靜態屬性**（不隨 T 變）：`stock_id` 4 碼純數字、非 `00` 開頭、非 DR（`is_dr_code`）——沿 `universe.is_pool_candidate`／`is_dr_code`。
2. **市場轉換表**：對每個代號，把快照內所有列依 `date` 排序；`type` 改變的地方＝轉換點，**生效日＝較舊那列的 `date` +1**（誤差 1～2 日，揭露）。
   `market(sid, T)`＝T 當日生效的 `type`；T 早於最舊一列的 `date` 時取最舊那列的 `type`（假設更早無資料＝同市場）。`emerging` 也是一個 `type`。
3. **成員**：`members(T)` ＝ 靜態合格 ∧ `market(sid,T) ∈ {twse,tpex}` ∧ **T 日有成交列**（`is_traded_row`：`close>0 ∧ volume>0`，2026-09-12 修正的口徑）。
   已下市股在還有列的日子照常在池（48 檔，`pre-registration.md:343-345` 明文避免存活者偏誤）；上市日不用快照，由第一筆成交列決定（裁定 #6）。
4. **兩份名單**：廣度母體 `N(T, market)`＝`members(T)` 依 `market(sid,T)` 分桶；排名池＝`AdvTracker.eligible()` ∩ `members(T)`（不變）。
   **可計分標的含 ETF 仍另案**（不在本項）。
5. **兩條路徑同一支**：`feed.day_records` 改吃 `PitPool` 而非 `dict`；`scan_features.py`／`replay_io.ReplaySource`／`daily_core.rebuild_from_bundles`／`run_offline` 全部改呼叫它，不留第二份實作。
6. **指紋**：池語意寫進 `run_common.build_params_payload`（新增 `pool_semantics: "pit-1"`）→ `params_sha` 換版；`model_version` 不動（它是計分規則指紋）。**這與 `docs/P3-KICKOFF.md` §3 #1 寫的「`model_version` 換版」不同，見 §5 Q9。**

## 2. 怎樣算完成（每條附驗法）

| # | 條件 | 驗法 |
|---|---|---|
| 1 | 純函式 `PitPool`：轉換表、`market(sid,T)`、`members(T)` | 單元測試：單一列／多列同市場／tpex→twse 轉換（生效日＝舊列 `date`+1）／emerging→tpex／殘留列 `date` 早於資料起點；11 檔真實跨市場代號用 `data/pool.json` 實跑列出各自轉換日 |
| 2 | **入池側**（合成世界，沿 `test_daily_entrants` 的 X）：X 自第 0 日有成交列、快照到 E 才含它 → 參考路徑 PIT 池 **T<E 也含 X**（成員由成交列決定，非快照）；每日班側檔機制不變 | 分數：參考 vs 每日班在 T≥E+5 逐位相同（既有對齊點）；`parity_check` 對 T<E 仍歸「①連帶」、rc 0 |
| 3 | **出池側**：Y 在 D 之後無成交列 → `members(T≥D)` 不含 Y，但 T<D 含 Y **即使今日快照沒有 Y**（下市股不從歷史消失） | 合成世界：把 Y 從快照拿掉，參考路徑 T<D 的廣度 N 仍計入 Y；改前（靜態快照）會漏 |
| 4 | **轉板**：Z 在 C 由 tpex 轉 twse（殘留列 `date`＝C+1）→ T<C 進 tpex 桶、T≥C 進 twse 桶；兩市 N 各自正確 | 合成世界；改前 Z 全期在 twse 桶 |
| 5 | **興櫃**：W 在 C 由 emerging 轉 tpex、C 之前有成交列 → T<C 不在池 | 合成世界；改前（成員只看成交列）會提前入池——這是靜態快照沒有、PIT 新引入的錯法，必須有測試 |
| 6 | 兩條路徑 parity（合成世界）：`scan_features`＋`replay_scores` vs `daily_core.run_offline` 對同一世界逐日分數逐位相同 | 沿 `tests/test_daily_run.py::test_chain_end_to_end_bitwise` 的形狀加 PIT 情境 |
| 7 | `params_sha` 換版且舊 `scores.db`／舊 `cross.json` 被 `replay_scores --resume`／每日班拒絕續算（版本綁定） | 既有版本綁定測試加 `pool_semantics` |
| 8 | 既有 pytest 全綠；`tests/test_pool_dr.py`／`test_pool_tiebreak.py` 語意不變（tie-break 只用於同 `date` 平手） | `pytest tests/ -q` |
| 9 | **Hetzner 全量**：`scan_features --rebuild`（5.4 分）＋ `replay_scores --rebuild`（約 12.6 h，多核平行另案）→ 新 `scores.db`、新種子匯出；一句話貼（`scripts/hetzner_pit.sh FROM TO`，比照 `hetzner_round.sh`：pull＋核 HEAD＋跑＋commit 分支） | 報告：與舊 `scores.db` 逐日比對，差異只落在 (a) 轉換過市場的檔與其影響的市場列 (b) 快照外的下市股 (c) 興櫃轉上櫃前的日子；其他日逐位相同 |
| 10 | **每日班切換**：合併後每日班以 PIT 語意重建視窗；09-01～T0 以新種子重算覆蓋（第三次覆蓋，先問）；D-3 對帳 `hetzner_round.sh 2026-09-01 T0` rc 0 | 綁確切 commit；fresh-context 驗收 |
| 11 | 文件：`spec/P1-B3-replay.md` #5 的「PIT」在本 repo 的實作口徑（含誤差揭露）寫進 `docs/pre-registration.md` §3 已知偏差；`feed.py:17-18`／`config.OUT_OF_SCOPE` 那段更新 | 對照 |

## 3. 不做

- ETF 計分（可計分標的含 ETF）——另案，`docs/P2-REPLAY-PLAN.md:117`。
- 上市日／轉換日的精確來源（無資料集）——用成交列與殘留列近似並揭露。
- 多核平行重播——另案；本項照單核 12.6 h。
- `parity_check` ①連帶邏輯拆除——入池側仍需要（每日班側檔補不到狀態鏈的 E+5 殘差），只改註解裡的理由。

## 4. 風險與已知偏差（會寫進登錄書 §3）

- 轉換日誤差 1～2 日：轉板前後 1～2 個交易日該檔可能落錯市場桶，影響兩市 N 各 ±1。
- 殘留列只涵蓋約 2020 年後；更早的轉換無法重建（暖機期 2020-01 起，影響有限，但要揭露）。
- 多次轉換是否完整保留未驗證（P0-A 待驗證第 6 列）：實作時對 162 檔逐檔列出轉換序列，異常者（>2 次、來回）人工看。
- 靜態快照本身隨抓取日漂移（2,139 vs 2,140）：PIT 化後成員不再依賴抓取日，但**靜態屬性**（DR 判定、代號合格）仍取自快照，需固定用同一份（`data_version` 綁定）。

## 5. 裁定紀錄（2026-09-16，全照建議＝全部「是」）

| Q | 問題 | 裁定 |
|---|---|---|
| Q9 | 池語意進 `params_sha`（不動 `model_version`）——與 P3-KICKOFF §3 #1 字面不同 | 是；`model_version` 留給計分規則 |
| Q10 | 成員口徑用 `is_traded_row`（close>0 ∧ volume>0，09-12 修正）而非「有價格列」 | 是；與廣度母體「有成交」一致 |
| Q11 | 轉換日＝舊列 `date`+1、誤差揭露、2020 前不重建 | 是 |
| Q12 | 興櫃時期一律不在池（即使有成交列） | 是；興櫃交易制度不同 |
| Q13 | 第三次重算覆蓋（新種子）預先授權，等 Hetzner 跑完直接開 PR | 是 |
| Q14 | Hetzner 一句話貼一次（含 scan＋replay 約 13 h，tmux） | 是 |
