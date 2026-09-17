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
| 4 | **轉板**：Z 在 C 由 tpex 轉 twse（殘留 tpex 列 `date`＝C 的前一曆日，生效日＝該列 `date`+1＝C，同 §1 第 2 點；2026-09-17 更正原寫的「C+1」）→ T<C 進 tpex 桶、T≥C 進 twse 桶；兩市 N 各自正確 | 合成世界（`synth_db.pit_info_rows`）；改前 Z 全期在 twse 桶 |
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

- 轉換日誤差 1～2 日（方向，2026-09-17 對齊）：殘留列 `date` ≈ 我方生效日 −1（＝舊市場最後被觀測日），我方生效日＝殘留列 `date`+1；
  **官方掛牌日 vs 我方生效日**差 1～2 日（P0-A §4.4 抽查 6438／4736；6423 對 Yahoo 新聞差 1 日），轉板前後 1～2 個交易日該檔可能落錯市場桶，影響兩市 N 各 ±1。
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

## 6. 實作交付（2026-09-16，分支 `claude/dazzling-maxwell-serk13`；本容器離線部分，Hetzner 全量＝§2 #9／#10 未跑）

### 6.1 檔案清單

| 檔案 | 變更 |
|---|---|
| `src/iching/universe.py` | 新增 `POOL_SEMANTICS = "pit-1"`、`_next_calendar_day`、`_transition_seq`、**`PitPool`**（+162 行）；**`traded_ids()`＝兩條路徑共用的唯一成交門**（2026-09-17 退回後抽出）；`NON_INDUSTRY_CATEGORIES` 加異體 `創新版股票`；`pool_from_info`／`_pick`／`pit_pool` 一字未動 |
| `src/iching/feed.py` | `load_pool()` 回 `PitPool`；`day_records()` 先以 `universe.traded_ids` 算 `traded_sids` → `pool.members(T, traded_sids)`，不成交但 T 日在池者仍吐 `close_adj=None` 的列（形狀與改前同）；模組 docstring「已知近似」段改寫 |
| `src/iching/replay_state.py` | `WindowCache` 只收 `PitPool`；`ingest` 用 `pool.listed(sid, T)`＋`U.traded_ids(b.stocks.items())`（模組級引用，測試可 monkeypatch）、`stock_inputs` 用 `listed(sid, tpe_date)`＋`industry_of` |
| `src/iching/replay_step.py` | 個股迴圈市場別改 `wc.pool.listed(sid, T)` |
| `src/iching/replay_io.py` | `rebuild_start` 逐檔市場別改 `pool.listed(sid, before)`（`before` 當日不在池者跳過） |
| `src/iching/daily_core.py` | `pool_from_payload`／`load_pool_file` 回 `PitPool`；排名池斷言 `∩ pool` 改 `∩ pool.listed_ids(T)` |
| `src/iching/daily_pipeline.py` | `_pool_signature` ＝靜態 meta（去揮發欄）＋**轉換表**；新殘留列出現＝池變 |
| `src/iching/run_common.py`／`scripts/scan_features.py` | `build_params_payload`／`build_params` 加 `pool_semantics`（Q9） |
| `src/iching/config.py`、`src/iching/scan.py`、`scripts/probe_features.py`、`scripts/backfill_hetzner.py` | `OUT_OF_SCOPE` 第 ③ 條與各處註解改指 `PitPool`；回補報表改走 `PitPool`（`grep pool_from_info(` 在 `src`／`scripts` 只剩 `universe.py` 內部） |
| `scripts/pit_report.py`（新，2026-09-18 約 255 行） | `transitions`（轉換表＋異常）／`compare`（新舊 `scores.db` 逐日比對：sid∈(a)∪(b)∪(c) 直接歸類、市場列在有任一類的日子歸連帶、其他 sid 只有差異欄 ⊆ `POOL_DEPENDENT_COLS`（池依賴欄，程式碼路徑推導；2026-09-18 取代原 `LINKED_COLS` 三欄）才歸連帶，否則未解釋 rc 1；未解釋列另記差異欄直方圖） |
| `scripts/hetzner_pit.sh`（新，102 行→2026-09-17 加分數匯出後 110 行；push 走 `--force-with-lease=<BR>:<我方看到的 origin SHA>`，分支不存在＝0000000 等同直接 push） | 一句話貼：pull main＋核 HEAD＋核 `POOL_SEMANTICS`→ 備份舊 `scores.db` → `scan_features --rebuild` → `replay_scores --rebuild`（重貼走 `--resume`）→ `export_seed` → **`export_scores --force`（4b，第三個參數 `FROM_SCORES` 預設 `2026-09-01`，見 6.7）** → 兩份報告 → 種子＋`data/scores`＋報告 commit 到 `hetzner/pit-<TO>` 並 push；`bash -n` 通過，本容器未實跑 |
| `scripts/export_scores.py`（新，2026-09-17）、`tests/test_export_scores.py`（新，4 支） | `scores.db` → `data/scores/<T>.json`，與每日班 `run_offline` 產出位元組相同（`diag.elapsed_ms` 除外、`diag.rank_pool_size` 省略）；見 6.7 |
| `tests/test_pitpool.py`（新，10 支）、`tests/test_pit_world.py`（新，12 支）、`tests/synth_db.py`（加 `add_pit_rows`）、`tests/test_feed.py`（1 行改呼叫形狀） | 見 6.4 |
| `docs/pre-registration.md` §3、`scripts/parity_check.py` 檔頭、本節 | 文件 |

### 6.2 `PitPool` API（`src/iching/universe.py`）

- `PitPool.from_snapshot_rows(rows)`：快照列（5 欄，含殘留列）→ 靜態集合＋轉換表。靜態集合＝`pool_from_info(rows)` 的鍵
  （候選規則與 DR 排除**逐字重用**，`tests/test_pool_dr.py`／`test_pool_tiebreak.py` 語意不變）；`static[sid]`＝那份 meta
  **去掉 `type`**（`industry_category`／`stock_name`／`date`／`n_rows`／`same_date_multi`）。
- `market(sid, T) -> 'twse'|'tpex'|'emerging'|None`：轉換表 `((None, t0), (eff1, t1), …)`，取最後一個 `eff ≤ T` 的 type；T 早於
  `eff1` 取 `t0`（Q11）；不在快照回 None。`listed(sid, T)`：`market()`∈{twse,tpex} 才回，否則 None（Q12）。`listed_ids(T)`。
- `members(T, traded_sids) -> {sid: meta}`（代號升冪）：靜態合格 ∧ `listed(sid,T)` ∧ `sid∈traded_sids`；meta＝靜態 meta＋`type`＝T 日市場。
  `traded_sids` 由呼叫端用 `is_traded_row` 算（Q10）。
- Mapping 介面（`in`／`[]`／`len`／`iter`／`items`／`get`／`==`）一律指**靜態集合**，給只要名單＋產業別的呼叫端
  （`collect.stocks_from_rows`／基本面橋／entrants 偵測／`export_seed` 讀回比對）。
- `report_transitions()`：`{n_transitioned, transitions:{sid:[[eff,type],…]}, anomalies:{sid: 理由}}`，異常＝轉換 >2 次或來回；
  `scripts/pit_report.py transitions` 另加「上市→上櫃逆向」旗標與「跨 twse/tpex」清單。

### 6.3 轉換表規則實例（`_transition_seq`）

依 `date` 分組升冪；同 `date` 多列走 `_pick` 三層 tie-break（①非產業→②傘狀→③twse 優先，**順序不可調換**，只在平手時）取該組 type；
type 與前一組不同＝轉換點，生效日＝**前一組**的 `date` +1 曆日（`_next_calendar_day`，月底跨月正確；解析不了的字串原樣回傳）。

| 快照列 | 轉換表 | `market(T)` |
|---|---|---|
| `(tpex, 2021-05-15)`, `(twse, 2026-09-16)` | `(None,tpex), (2021-05-16,twse)` | 2021-05-15→tpex、2021-05-16→twse、2019-01-01→tpex |
| `(emerging, 2024-05-14)`, `(tpex, 2026-09-16)` | `(None,emerging), (2024-05-15,tpex)` | 2024-05-14→emerging（`listed`=None）、2024-05-15→tpex |
| `(tpex, 2025-06-01, 通信網路業)`, `(tpex, 2026-09-16, 運動休閒類)` | `(None,tpex)`（產業重分類不是轉市） | 全期 tpex |
| `(tpex, D0, 電子零組件業)`, `(twse, D0, 電子零組件業)` 同日 | `(None,twse)`（③ twse 優先） | 全期 twse |
| `(tpex, D0, 電子零組件業)`, `(twse, D0, 電子工業)` 同日 | `(None,tpex)`（② 傘狀先剔除 twse 那列，③ 沒機會） | 全期 tpex |
| `(tpex, 2019-06-30)`, `(twse, 2026-09-16)`（殘留列早於資料起點 2020-01） | `(None,tpex), (2019-07-01,twse)` | 資料期全 twse |

### 6.4 實跑結果（本容器，`data/pool.json`＝`fm-20260911-01` 快照 4,323 列、最新 `date` 2026-09-16）

- 成員數：`members(2026-09-16, 全部合格代號)`＝**2,140 檔（twse 1,213／tpex 927）**，與 `pool_from_info` 的 2,140 逐檔、逐市場相同（`test_real_snapshot_eleven_cross_market_codes`）。
- 有市場轉換 **164 檔**（§0 #4 寫 162 是 09-11 快照；型態：emerging→tpex 83、emerging→twse 70、tpex→twse 10、emerging→twse→tpex 1）；
  跨 twse/tpex **恰 11 檔**，轉換日：3092 `2021-05-16`／3652 `2022-09-24`／4736 `2023-12-24`／5236 `2026-07-19`／6426 `2021-03-27`／
  6438 `2021-01-22`／6446 `2024-01-28`／6472 `2023-12-22`／6589 `2025-07-24`／8476 `2023-11-03`（皆 tpex→twse）；
  **6423**：emerging@起點 → twse@`2024-05-15` → tpex@`2026-01-23`——唯一異常（上市→上櫃逆向），且 emerging 殘留列 `date`
  2024-05-14 與下一列 2024-12-04 相距 7 個月，「生效日＝舊列 +1」在這檔可能差很多，**請對官方公告人工核**（規則不為單檔改）。
  **主對話 WebFetch 查證（2026-09-17）**：6423＝億而得，Yahoo 2026-01-22「轉上櫃」新聞與轉換表生效日 2026-01-23 差 1 日，
  上市→上櫃逆向**為真**（不是資料錯）；2024-12-04 那列 `industry_category='創新版股票'`（「版」異體）已加進 `NON_INDUSTRY_CATEGORIES`。
  >2 次或來回：0 檔。
- 合成世界（`tests/test_pit_world.py`）：Z（tpex→twse）、W（emerging→tpex）、Y（下市）兩市 N 逐日＝獨立手算（`day_records` 與
  落地 `features.db` 兩處）；兩條路徑（`scan_features`＋`replay_scores` vs 每日班 `daily_run`）跨三個轉換點**逐日逐位相同**、
  狀態鏈終點相同；`update_pool` 新殘留列＝池變、只有最新列 `date` 變＝不變；舊語意指紋的 `cross.json`／`scores.db`／`features.db`
  分別被 `run_offline`／`replay_scores --resume`／`scan_features` 拒。
- 突變自測（各自還原）：①`members` 拿掉 `listed` 門 → W 測試紅；②生效日不 +1 → Z 桶測試與 `_transition_seq` 測試紅；
  ③payload 拿掉 `pool_semantics` → 兩支版本綁定測試紅；④（2026-09-17）`traded_ids` 改成「全部算有成交」→ **兩側都紅**：
  `test_feed::test_day_records_filters_pool_and_marks_untraded`（feed 側）、`test_replay_state::test_non_traded_rows_and_missing_index_do_not_advance`
  （WindowCache 側）、本檔 Z/W/Y 桶測試＋breadth 手算＋`test_traded_gate_is_single_function_used_by_both_paths` 共 5 支；
  **兩路 parity 測試 `test_two_paths_bitwise_across_transitions` 仍綠**——兩側走同一支函式，一起錯所以仍逐位相同，那正是「同一支」的意思，
  不是它抓不到；抓錯的是母體手算那幾支。
- 既有測試：`pytest tests/ -q` 全綠（見交付回報的數字）；`test_pool_dr`／`test_pool_tiebreak`／`test_daily_entrants`／`test_scan_features`
  零改動，`test_feed` 一行由 `pool["6488"]["type"]` 改 `pool.market("6488", T)`。

### 6.5 與舊語意（靜態最新快照）的差異會出現在哪些情況

1. **轉板過的檔**（今日快照 11 檔）：舊語意全期在最新市場桶；PIT 在生效日前落舊市場桶 → 兩市 `N`、產業聚合、`P_cs` 母體、
   該檔自己的 `market` 鍵與大盤方向分數連帶的所有個股列，在生效日前每一日都不同（§2 #9 (a)）。
2. **興櫃轉上市櫃的檔**（153 檔 emerging→x）：舊語意只要有成交列就在池（興櫃期也算進 `N`）；PIT 興櫃期不在池 → 那些日子 `N`
   少一檔、該檔無列（§2 #9 (c)）。**這是 PIT 新引入、舊語意沒有的差異，量級比 (a) 大**（153 檔 × 各自的興櫃期）。
3. **快照裡沒有的下市股**：兩版都不在池（§2 #9 (b) 應為 0），見 6.6。
4. **`WindowCache.rebuild_start`**：`before` 當日不在池（興櫃期）的檔不再參與起點計算 → `--resume` 的視窗重建起點只會更晚不會更早；
   該檔在 `before` 前沒有 ring（ingest 也跳過），不影響 parity。
5. **每日班 `pool_changed`**：新殘留列出現（轉板／興櫃轉上櫃當日 FinMind 多一列）會觸發 `pool.json` 改寫，舊版只看成員／產業。

### 6.6 未做／待裁定（不要當成已完成）

- **§2 #3 的「即使今日快照沒有 Y」未做**：FinMind 會把下市股從 `TaiwanStockInfo` 拿掉（裁定 #24：2020-01-02 切片 48 檔已下市普通股
  「不在 info」），沒有任何列就沒有市場別，本版**不憑代號形狀猜市場**（4 碼非 00 非 91 也可能是下市的興櫃股）。
  要納入需一個帶市場別的來源（`TaiwanStockDelisting` 725 筆，P0-A §4.4 查過存在但**未落地**），屬另案。合成世界只驗了可判定的
  半邊（Y 快照仍留一列、`YD` 起沒有價格列）。**副作用**：§2 #9 的差異類 (b) 在 Hetzner 報告應為 0；若非 0 要回頭查。
  注意這也與 `tests/test_daily_entrants.py` 出池側世界（Y 從快照消失＝出池）的既有語意一致，若改成形狀入池那組測試會翻。
- **§2 #9／#10 Hetzner 全量與每日班切換**：本容器無 Hetzner、無 token；`scripts/hetzner_pit.sh` 只 `bash -n`，
  `scripts/pit_report.py` 只在合成 DB 上煙霧（`test_pit_report_transitions_and_compare`）。
- **`tests/test_daily_entrants.py` 的 parity ①連帶 rc 0**：既有測試照舊通過，理由已改寫進 `parity_check.py` 檔頭（連帶仍因側檔補不到狀態鏈）。
- `docs/P2-DAILY-PLAN.md` §7.7 開頭「參考路徑的池是靜態的最新快照」是 2026-09-15 的盤點紀錄，未改（歷史文件），本節為現況正本。
- `scripts/hetzner_round.sh:77` 仍是 `git push -q -f`（同型問題），本批不動、另案；`hetzner_pit.sh` 已改 `--force-with-lease`。
- ~~`pit_report.py compare` 的 `LINKED_COLS` 三欄是合成世界實測出來的傳導形狀；Hetzner 真資料若有其他欄會落「未解釋」rc 1——那時要先看
  明細再決定要不要擴集合，不得為了 rc 0 直接加。~~ **2026-09-18 已做**：真資料確實落 rc 1（§6.8），依程式碼路徑推導改成
  `POOL_DEPENDENT_COLS`（`line_3`／`line_6` 及其附欄、三個聚合分數、`coverage`、卦位與遲滯狀態欄；**`line_1/2/4/5` 與 `in_rank_pool` 刻意不在**（後者是逐檔自家 ADV 對絕對門檻，非轉市檔不隨池變；首版誤列、驗收退回）），
  用第三輪報告的每日直方圖離線重歸類：1,628 日全部歸連帶、0 日未解釋（rc 會是 0）。證據鏈＝程式碼路徑（常數上方註解）＋第三輪直方圖
  ＋雲端 09-15／16 對照，不是為 rc 0 硬加。

### 6.7 分數匯出 `scripts/export_scores.py`（2026-09-17，§2 #10 的工具）

Hetzner 全量重播只產 `cache/scores.db`；`export_seed` 匯的是種子（狀態鏈＋原料包），**逐日分數檔 `data/scores/<T>.json` 不在裡面**。
要以 PIT 重播結果覆蓋主線 09-01 起的分數檔（裁定 #49 Q13），就用這支從 db 匯：

```bash
python3 scripts/export_scores.py --cache-dir cache --out . --from 2026-09-01 --to 2026-09-16 --force
```

- **產出＝每日班同一種檔**：`daily_core.scores_payload` 形狀、`DC.write_json`／`DC.dumps` 同一支序列化、同一把排序鍵
  `(market, stock_id, horizon, model_version)`；欄值**零轉換**（`scores` 表每一欄本來就是 `flatten_row` 的輸出，列先攤平再各寫一份到
  db 與檔），只 JOIN `versions` 還原 `model_version`。`params_sha`＝`replay_meta.params_sha`；`text_version`＝`replay_meta.params_json`
  並核對該日 `versions.text_version`。
- **決定性測試** `tests/test_export_scores.py::test_export_equals_run_offline_bytewise`：合成世界同時跑參考路徑（`scan_features`＋
  `replay_scores` → `scores.db`）與每日班路徑（`export_seed`＋`run_offline` → 4 個 T 的分數檔，含市場列／旗標／狀態欄），
  再由 `export_scores.py` 匯到另一目錄，**去掉下列兩欄後位元組相同**。突變自測（各自還原）：市場列 `flags` 一律 None／漏 `line_states`
  欄／排序鍵改錯／`index_missing` 不還原成 list／`in_rank_pool` 改 bool → 5 種全紅。
- **與 `run_offline` 產出的差異，只有 `diag` 兩欄**（`replay_step.step` 的 12 個鍵，`replay_day` 表落地 10 個）：
  | 欄 | 匯出檔 | 理由 |
  |---|---|---|
  | `diag.elapsed_ms` | 取 `replay_day.elapsed_ms`＝**重播那一班** `step()` 的耗時 | 兩條路徑必然不同；parity（`parity_check.py` 9 欄）本來就不比它 |
  | `diag.rank_pool_size` | **省略** | ＝`len(cross.adv.eligible())`（T 當日排名池大小，含當日沒有列的池內檔），`n_in_pool` 只數有列且在池者，兩者不等（線上 `data/scores/2026-09-01.json`：895 vs 891），`scores.db` 沒有任何表存它，**不偽造**；`parity_check.py` 的 9 欄與 `P2-DAILY-PLAN.md` §7 早已把它列為「JSON 多、sqlite 沒有」而排除 |
  其餘 10 個 diag 鍵與頂層 `schema`／`tpe_date`／`data_version`／`text_version`／`params_sha`／`rows` 逐位相同。
- **覆蓋規則**：檔已存在且位元組相同→略過；只差上述兩欄（`same-modulo-diag`）→不覆蓋、rc 0；分數列或其他欄不同→**預設不覆蓋、rc 1**，
  `--force` 才覆蓋。`hetzner_pit.sh` 4b 帶 `--force`，因為主線 09-01 起的檔就是要被覆蓋的對象。`--data-version` 省略時取
  `replay_meta` 唯一者，否則取 `cache/scores.db.state.json` 的 `meta.data_version`，都不成立 rc 2。
- **未做**：`rank_pool_size` 若日後要回填，可能的來源是 `features.db` 的 `scan_day.rank_pool_size`（同樣是「到 T−1 為止」的池），
  但兩者是否逐日相等**未驗證**，本批不接。

### 6.8 Hetzner 全量實跑與第三次覆蓋（2026-09-17～18，§2 #9／#10）

**三輪 `hetzner_pit.sh`（都是 `2020-01-01 2026-09-16`）**：

| 輪 | 結果 | 修正 |
|---|---|---|
| 1 | scan＋replay 全量完成（1,628 日、`last_date=2026-09-14`）、種子匯出、compare **rc=1**；第 6 步 push 失敗：`git rev-parse "origin/$BR"` 在分支不存在時把名字照印進 `EXPECT`，`cannot parse expected object name` | 使用者手動 `git push -u` 出 `217aa48`；PR #34 改 `--verify -q`＋回歸測試 |
| 2 | **4b 漏跑**：從 `217aa48` 的 checkout（舊版腳本）起跑，第 0 步 pull 換了檔但 bash 已把整份舊腳本讀進緩衝；且 `mkdir -p runs/pit` 在 checkout 之前做，切回 main 時 git 連空目錄移掉 → 5a 的 `tee` 失敗、`pipefail` 靜默結束（log 停在轉換表、無 `!!`） | PR #36：啟動先自我複製再 `exec`、pull 後 HEAD 前進即改用新版重新執行（`HETZNER_PIT_PULLED`／`HETZNER_PIT_LOG`）、`mkdir runs/pit` 移到 5a 前；回歸測試以臨時 bare origin 驗 |
| 3 | 分支 `aa5c0b4`（基底 main `e6ce1de`；跑的是 `2783fe5` 版腳本——bash 在 pull 前已讀入，#36 的重新執行邏輯下一輪才生效）：scan／replay `--resume`、種子重匯（`cross.json`／`pool`／`factors`／`fundamentals`／1,630 份原料包與 `217aa48` **逐位相同**）、**4b 匯出 09-01～09-14 十檔**（`params_sha=804f05cddc6e`、無 `rank_pool_size`）、compare rc=1 但**直方圖坐實了解讀**：未解釋 3,695,148 列、83 種欄位組合，出現過的欄只有 `line_3`／`line_6` 與其衍生（`base_score`／`inner`／`outer_trigram_score`／`coverage`／`line_3_coverage_ratio`／`line_3_reweighted`／`line_states`／`streaks`／`lines_*`／`king_wen*`／`hexagram_name*`），**`line_1/2/4/5` 零出現、onesided 未解釋 0 列、`in_rank_pool` 0 列**；最大宗 `base_score+inner_trigram_score+line_3+line_6+outer_trigram_score` 3,182,921 列 | `pit_report` 的連帶欄集合 2026-09-18 改成「池依賴欄」`POOL_DEPENDENT_COLS`（見 6.6），第三輪報告離線重歸類 0 日未解釋 |

**第一輪 compare rc=1 的解讀（程式碼調查，2026-09-17）**：1,628 日中 1,618 日有「未解釋」檔，合計 1,540,294 檔·日（`class_totals.unexplained` 是每日 sid 集合累加，不是列數；列數見第三輪直方圖 3,695,148）；
**前 10 個交易日為 0、第 11 日（2020-01-16）起出現**，高頻檔多為上櫃小型股（4107／4111／1813／4126／1777／4105／4120
全在 `tpex×生技醫療業` 桶）。池成員集合進計分有四條管線，`scripts/pit_report.py` 原 `LINKED_COLS`＝{`line_6`,
`outer_trigram_score`, `base_score`} 只涵蓋「大盤方向分數→個股上爻」那條，**漏了「產業中位報酬（同市場×同產業桶）→
`line_3` 族 B」**（`src/iching/score/stock.py` 的 `ind_excess_vs_industry`），其長視窗 `STK_L3_WIN["short"]=(5, 10)`
（`score/params.py`）需要 11 個收盤才首次可得——與第 11 日吻合；前 10 日兩邊都因 `industry_n` 缺而同樣缺。
另有 `P_cs` 母體（排名池）→ `line_3` 過熱上限、產業內站上 MA20 比→`line_6` 族 B 兩條，皆為整桶／全池效應。
**不依賴池的欄**：`line_1`（族 C 產業母體是靜態、不隨 T）、`line_2`、`line_4`、`line_5`——兩版對非轉市檔必須逐位相同，
這才是 compare 真正該守的不變式。

**雲端獨立佐證（09-15／16 重算 vs 主線現行檔，見下）**：差異欄以 `base_score+line_6+outer_trigram_score` 為主（09-15 1,530 列、
09-16 3,261 列），其次 `line_3`＋`inner_trigram_score`；**`line_1/2/4/5` 的差異只落在轉市檔（09-15：72 檔中 70 檔，09-16：
70 檔中 68 檔）**——PIT 下興櫃期價量不進鏈，這些檔的均線／結構視窗起點不同，屬預期；例外 2881／2883 是 `line_1` 基本面
（主線 09-16 班才補進 2026-08 月營收，資料到達時點差、非池效應）、09-16 大盤列 `line_2`（廣度母體），以及 09-16 的 2938
（主線 `pool.json` 才有它 09-16 的 `tpex` 列＝真轉市檔，`aa5c0b4` 的池快照較舊、其 transitions.json 沒列它——日後拿 Hetzner 那份
transitions 核對會再出現同一個假例外）。

**09-15／16 從新種子重算（雲端，2026-09-17 16:0x UTC）**：`scripts/recompute_from_seed.py --seed-commit 5fee4d4c18a8
--data-ref origin/main(2783fe5) --bundles-ref origin/main --from 2026-09-15 --to 2026-09-16`。
- 種子 `5fee4d4` 是**合成 commit**＝`217aa48` 的樹去掉 `runs/collect/2026-09-15／16-daily.json.gz`（該分支的 `cross.json`
  `last_date=2026-09-14` 卻帶著兩份更晚的原料包，`recompute_from_seed` 會以「不是乾淨的種子」拒收；`--seed-bundles` 取的是
  最後 N 份、幫不上）。種子只提供 `cross.json`＋1,628 份 ≤09-14 原料包；`pool／factors／fundamentals／calendar／entrants`
  一律取 `--data-ref`。
- `--data-ref` **必須是主線**：主線 `factors.json` 比 PIT 種子多 8 筆 09-15／16 池內除權息（09-15：3675／5426／6924；09-16：
  1517／1599／2330／4763／6830）與 5 檔金融股 2026-08 月營收；PIT 語意下主線池多出的 2938（生效日 09-16）／7947（只有興櫃列、
  進不了靜態集合）不再污染歷史——`listed_ids` 09-14／09-15 兩側完全相同（上一輪 250f80c 那型混樹不再需要）。
- 結果：rows 5,841／5,844、只在重算 0／只在現行 0；diag 差欄 `n_in_pool`／`rank_pool_size`／`n_stock_any_unknown`
  （後者是 `line_3` 產業樣本閘門的連動，72 vs 70）；新 `cross.json` `last_date=2026-09-16`、`params_sha=804f05cddc6e`、
  `adv.amt` 2,047 檔、`stock_lines` 6,141；兩檔頂層 `params_sha=804f05cddc6e`。**無 `--dump`（沒有 09-15／16 的 Hetzner
  參考），完成行印「未驗證」**，以上述形狀檢查把關。
- 原料包 `runs/collect/2026-09-14-daily.json.gz` 主線版 `us=[]`（09-14 班當時美股列未到，09-15 包才帶 `2026-09-14` 列）、
  Hetzner 版含該列；種子世界（＝Hetzner 全量重播）在 09-14 就 ingest 它，09-15 包再帶到時 `replay_state` 去重跳過。
  第三次覆蓋把主線該包換成 Hetzner 版，讓主線自成種子時能重現同一條鏈。`2024-09-25` 包的差異是舊種子的「首包預載 320 列」
  結構（主線 `us`/`fx` 各 320 列 vs 全量匯出 1 列），在 320 窗之外、不動。

**第三次覆蓋（裁定 #49 Q13 預先授權）範圍**：`data/scores/2026-09-01..09-14.json`（第三輪 4b 匯出，diag 無 `rank_pool_size`、
`elapsed_ms` 為重播耗時，見 6.7）＋`data/scores/2026-09-15／16.json`＋`data/state/cross.json`（上述雲端重算）＋
`runs/collect/2026-09-14-daily.json.gz`（Hetzner 版）。`data/pool.json`／`factors.json`／`fundamentals.json` 一個位元組不動。
**組裝（2026-09-17 17:4x UTC，分支 `claude/dazzling-maxwell-serk13`）**：09-01～14 十檔 `git show aa5c0b4:data/scores/…` 原樣、09-15／16 與 `cross.json` 為上述雲端重算產物（`cmp` 逐位）、09-14 原料包取 `aa5c0b4` 版；12 檔（09-01～09-16 的 12 個交易日）頂層 `params_sha` 全為 `804f05cddc6e`。
**驗收條件**：①09-01..09-16 每檔頂層 `params_sha=804f05cddc6e`；②09-15／16 與 `cross.json` 逐位＝重算產物；③09-01..14 逐位＝
第三輪分支的檔；④第三輪 compare 直方圖不含 `line_1/2/4/5`（含 `_unknown/_coverage_ratio/_reweighted`），onesided 未解釋＝0；
⑤`pytest tests -q` 全綠；⑥fresh-context 驗收綁 PR head；⑦合併後下一班每日班（09-18 22:30 台北）綠、issue #32 關閉。
**時限**：主線 `cross.json` 的 `params_sha` 仍是舊指紋 `6a1bb7f46402`，`run_common.check_snapshot_meta` 會讓每日班拒跑
（09-17 那班已如此紅、issue #32），09-18 22:30 台北前必須合併。
