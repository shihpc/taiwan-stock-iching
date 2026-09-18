# P3 第 2 項：回測資料出口（Q3）——驗收條件與待裁定（2026-09-18）

裁定 #48 Q3：「(a) 統計腳本在 Hetzner 跑、結果 commit，每次一句話貼；(b) 只匯評估要用的最小集合供雲端複算」
（`docs/P3-KICKOFF.md` §5 Q3、§7 第 2 項）。本文件＝動手前的驗收條件正本。
**§3 九題已於 2026-09-18 裁定（`docs/P2-KICKOFF.md` 裁定 #50）**：Q15／Q20／Q22 照實測改寫（九欄、CSV.gz、段×horizon 六檔），其餘全照建議。

## 0. 目標與範圍

- **產物**：`data/backtest/<segment>_<horizon>.csv.gz` 六檔（`train`／`valid` × `short`／`swing`／`mid`；每檔 ≈13 MB、合計 ≈76 MB，
  2026-09-18 以 12 個真實分數檔外推的實測，遠低於 GitHub 單檔 50 MB 警告線）＋`data/backtest/manifest.json`，由 Hetzner 一句話貼產出、
  commit 到 `hetzner/dataset-<TO>` 分支；驗收後進 main（裁定 #50 Q22）。
- **一列＝一檔×一日×一 horizon**（與 `data/scores/<T>.json` 的 `rows` 同鍵：`date`／`market`／`stock_id`／`horizon`，滿足
  `docs/P3-KICKOFF.md` §3 #9「前向紀錄與回測評估樣本同鍵」）。
- **只匯訓練＋驗證 971 日**（603＋368，`docs/pre-registration.md:47-50`）；暖機段（2020）與保留段（2025-01 起）**不匯**
  ——保留段一經動用即消耗（`CLAUDE.md` 約定 5），匯出等於隨時可看。
- **不做的**：成本模型（評估層參數，三檔敏感度，`pre-registration.md:74`）、可交易性過濾（只帶旗標）、統計量本身。

## 1. 已定義的事實（盤點 2026-09-18，附出處）

| 項目 | 值 | 出處 |
|---|---|---|
| 三段切點 | 訓練 2021-01-01～2023-06-30（603）／驗證 2023-07-01～2024-12-31（368）／保留 2025-01-01～2026-08-31（402）；`calendar_tpe.json` 實算相符 | `docs/pre-registration.md:47-50` |
| h | short 10／swing 20／mid 40 交易日（**不是**計分用 L3 長視窗 10/20/60，`src/iching/scan.py:119`） | `pre-registration.md:55` |
| 進出場 | T+1 開盤進、T+1+h 收盤出 | `pre-registration.md:68-69` |
| 個股價格 | 後復權，自算係數 `src/iching/adjust.py`（`adj(t)=raw(t)×Π_{ex≤t} before/after`） | `pre-registration.md:72`、`adjust.py:1-14` |
| purge／embargo | `e=T+1`、`x=T+1+h`；驗證／保留段起點各 20 交易日 embargo | `pre-registration.md:249-250` |
| 分數來源 | Hetzner `cache/scores.db`（`scores` 45 欄、PK `(version_id,market,horizon,stock_id,date)`，PIT 版 `params_sha=804f05cddc6e`） | `src/iching/scores_io.py:43-73`、`docs/P3-PIT-POOL.md` §6.8 |
| 價格來源 | `cache/prices.db` 的 `raw_price_daily`（`open/max/min/close/spread/Trading_*`，依 `src/iching/config.py:280-288`，**本容器未實查**）；`features.db` **沒有任何逐日價格欄**（`features_io.py:65-109`） | — |
| 現有讀價路徑 | `feed.iter_days` 只取 `close/Trading_Volume/Trading_money`（`src/iching/feed.py:142-155`），**不取 `open`**——要新寫查詢 | — |
| Hetzner 環境 | Python 3.14 系統層、pandas 2.3.3、**無 pyarrow、PEP 668 擋 pip** → parquet 寫不出來 | `spec/P1-B3-replay.md:74`、`docs/BACKFILL-RUNBOOK.md:12` |
| repo 壓縮慣例 | gzip `mtime=0`（同內容同位元組）；JSONL.gz 已有前例 `parity_check.py --dump` | `src/iching/bundle_io.py:110-114`、`scripts/parity_check.py:57` |
| 量級實測（本容器，12 日 70,032 列外推） | 七欄 CSV.gz ≈68 MB／971 日；加六爻＋聚合分數十六欄 ≈254 MB；全欄 JSON ≈6 GB | 盤點 §5 |

## 2. 驗收條件（動手前定案；驗收綁確切 commit）

**A 工具 `scripts/export_dataset.py`（Hetzner 執行）**
- A1 CLI：`--cache-dir`／`--out`／`--segment train|valid`（或 `--from/--to`）／`--data-version`；沿用 `export_scores.py` 的
  `resolve_data_version` 與 rc 約定（0 成功、1 目標已存在且內容不同、2 中止）。
- A2 開頭列印並寫進 manifest：`PRAGMA table_info(raw_price_daily)`、訓練＋驗證段 `open` 為 NULL／≤0 的列數與檔數、
  `replay_meta.params_sha`（必須是 PIT 版 `804f05cddc6e`，否則 rc 2）。
- A3 每列欄位（裁定 #50 Q15）：`date, market, stock_id, horizon, base_score, in_rank_pool, coverage, king_wen, lines_formal, fwd_ret,
  mkt_ret_h, exit_reason, entry_limit_up, exit_limit_down`——**`base_score` 全精度（`repr`，與 `data/scores/` 逐位可對）、`fwd_ret`／
  `mkt_ret_h` 四捨五入到 6 位小數**；缺值寫空字串；CSV（`csv` 模組、`\n` 換行、欄序固定如上）＋gzip `mtime=0`、compresslevel 9，
  同內容同位元組。**不帶六爻分數**（爻層假說要做時另匯）。
- A4 `fwd_ret` 定義：`adj_close(T+1+h) / adj_open(T+1) − 1`，後復權係數由 `raw_dividend_result`→`adjust.cumulative_factors`；
  交易日序取 `data/calendar_tpe.json`；`mkt_ret_h` 同窗、同市場指數（twse＝TAIEX、tpex＝TPEx，`raw_index_price`）。
- A5 邊界：視窗超出資料末日 → `fwd_ret=null`、列保留；視窗內缺成交列 → 出場價取最後有成交日的後復權收盤、`exit_reason="halt"`；
  下市 → `exit_reason="delist"`；正常 `exit_reason=null`。三類皆**不刪列**，manifest 記各類列數。
- A6 manifest：`data_version`／`model_version`（twse／tpex）／`text_version`／`params_sha`／`scores.db` 與 `prices.db` 的
  `PRAGMA user_version`（若有）／各檔 sha256／列數／每 h 最後可用訊號日／A2 的統計／產出時 HEAD。
- A7 離線測試（合成世界，免 token）：①與 `run_offline` 產出的 `data/scores/<T>.json` 同鍵逐列對得上 `base_score`；
  ②`fwd_ret` 對手算值逐位相同（含除權息跨窗、halt、delist、末日截斷四種案例）；③gzip 逐位可重現（跑兩次 `cmp`）；
  ④突變（h 改錯、少乘係數、用 T 收盤進場）全紅。

**B 一句話貼 `scripts/hetzner_dataset.sh`**
- B1 照 `hetzner_pit.sh` 骨架：自我複製＋pull 後重新執行、log、step 0 同步、產物目錄在 checkout 後建、`--force-with-lease`（`--verify -q`）。
- B2 產物 commit 到 `hetzner/dataset-<TO>`；log 末尾印六檔大小與 manifest 摘要。

**C 驗收（fresh-context，綁 Hetzner 分支 commit）**
- C1 六檔列數合計＝`scores.db` 該區間個股列 `SELECT COUNT(*)`（扣除大盤列，數量記在 manifest `n_market_rows_excluded`）；`date` 集合＝該段日曆。
- C2 抽 3 日與主線 `data/scores/`（若有重疊）或 `hetzner/pit-*` 匯出檔逐列核 `base_score`／`in_rank_pool`。
- C3 抽 10 檔×3 h 用 raw 價格與 `adjust.py` 手算 `fwd_ret` 逐位相同；其中含至少 1 檔跨除權息、1 檔 halt。
- C4 manifest 的 sha256 與檔案相符；`params_sha=804f05cddc6e`。
- C5 量級：每檔 ≤50 MB（GitHub 警告線）、六檔合計在 ≈76 MB 估算的 ±30% 內；超出就先回報再決定（Release 資產是備案）。
- C6 `pytest tests -q` 全綠、ruff 乾淨。

## 3. 裁定紀錄（2026-09-18，裁定 #50；Q15／Q20／Q22 改寫，其餘照建議）

| # | 題目 | 建議 |
|---|---|---|
| Q15 | 出口欄位：第一版帶不帶六爻分數 | **裁定：九欄**（七欄＋`king_wen`＋`lines_formal`，卦別排序表要用）、`fwd_ret` 取 6 位、按段×horizon 切六檔；六爻分數不帶。實測（CSV.gz、971 日）：七欄 93 MB／九欄 102 MB（浮點取 6 位 76 MB）／十七欄 477 MB——差異來源是六個 17 位小數的浮點欄 |
| Q16 | IC 用的前向報酬：扣不扣成本、走不走 §1.2.1 進出場 | **不扣成本**（成本是評估層參數），一律 T+1 開盤→T+1+h 收盤、後復權；分組報酬在統計層扣 |
| Q17 | 絕對 vs 相對報酬 | **主口徑絕對**；同檔多帶 `mkt_ret_h` 供相對計算 |
| Q18 | 停牌／下市／視窗不足 | 照 A5：帶旗標不刪列，統計層決定篩不篩（同裁定 #34 `in_rank_pool` 哲學） |
| Q19 | 漲停買不到／跌停賣不掉 | 第一版不過濾，只帶 `entry_limit_up`／`exit_limit_down` 旗標 |
| Q20 | 格式 | **裁定：CSV.gz（mtime=0）**——實測 JSONL.gz 因每列重複鍵名比 CSV.gz 大 1.1～1.5 倍；不用 parquet（Hetzner 無 pyarrow）；零新依賴 |
| Q21 | 範圍 | 只匯訓練＋驗證 971 日；暖機、保留不匯 |
| Q22 | 進 main 門檻 | **裁定：進 main**（六檔每檔 ≈13 MB，合計 ≈76 MB，一次性成本）。GitHub 硬限制是單檔 100 MB、50 MB 警告；留分支不會省 clone 成本（同 repo 物件預設一起 fetch），真要精簡 repo 是 Release 資產（備案） |
| Q23 | 統計層輸入形狀 | **已讀簽名（2026-09-18，`taiwan-backtest` `f2096e6` `audit/run_research.py:117-141`）**：`block_boot_ci(x, block, nboot, seed)` 對一維序列做 circular block bootstrap 回 2.5／97.5 百分位；`nw_se(x, lag)` 對一維序列回 Newey-West 標準誤（Bartlett 權重）。兩支都吃**每日一個值的日序列**（IC 日序列），橫斷面 Spearman 在上一層自算 → 出口做「每檔每日每 h 一列」正確，統計層先按日聚成 IC 再餵。借用時 `block` 要改成 max(21, 3h)、`nboot=1000`（登錄書），不是該 repo 的預設 |

## 4. 已知風險

- 個股 `open` 品質未實查（登錄書只驗過 TAIEX `open`）——A2 先量、C 再驗。
- `raw_dividend_result` 欄名「未在本容器親眼看到」（`config.py:295-296`）——A2 一併印 `PRAGMA table_info`。
- 登錄書尚未凍結（`pre-registration.md:17` TBD）——本文件綁 `d4770c7` 這版的切點與 h；凍結後重驗。
- PIT 池已切換但 D-3 對帳（09-25 後第二輪 parity）未做；出口的 `params_sha` 守門把「用錯 db」擋掉，不擋「PIT 本身有錯」。

## 5. 實作交付（2026-09-18；未在 Hetzner 實跑，C 段驗收待 `hetzner/dataset-<TO>` 分支）

**檔案**（本批新增三檔、改本文件；不動 `src/`）：

| 檔 | 內容 |
|---|---|
| `scripts/export_dataset.py` | 出口本體，**只用標準庫**（sqlite3／csv／gzip／array；不引入 pandas／pyarrow）。常數 `SEGMENTS`（`pre-registration.md:47-50`）、`H_BY_HORIZON`（`:55`）寫在檔頭 import 之後；規則全文在該檔 docstring |
| `scripts/hetzner_dataset.sh` | 一句話貼：`bash scripts/hetzner_dataset.sh <TO>`（`TO` 只命名分支 `hetzner/dataset-<TO>`）。照 `hetzner_pit.sh` 骨架：自我複製後 exec、日期驗證、log `cache/logs/dataset-*.log`、step 0 同步 main＋HEAD 前進即以新版重新執行、`POOL_SEMANTICS` 守門、產物目錄在 checkout 後建、`--force-with-lease` 用 `rev-parse --verify -q` |
| `tests/test_export_dataset.py` | 14 支離線測試（下表） |

**CLI**：`--cache-dir`（預設 repo/cache）／`--out`（repo 根，寫 `data/backtest/`）／`--segment train|valid|all`（預設 all）／
`--data-version`（直接 import `export_scores.resolve_data_version`）／`--calendar`（預設 `<out>/data/calendar_tpe.json`）／`--force`。
rc 0 成功／1 任一目標已存在且內容不同又未 `--force`（**六檔全部不寫**，先寫 `.tmp` 算完才比）／2 中止（db 缺、`params_sha`
≠ 現行碼指紋或 `pool_semantics≠pit-1`、日曆缺段內計分日或未涵蓋段末或與 TAIEX 指數列日期不一致、`raw_price_daily` 缺
`open/close/Trading_Volume`、同鍵重複列）。manifest 在成功路徑一律重寫（含 `head`，不參與 rc 1 比對）；rc 1／rc 2 早退時不寫。另 A2 的指紋守門實作為「＝現行碼算出的指紋」（本 commit 等於 `804f05cddc6e`），日後改 `model_version`／`TEXT_VERSION`／`POOL_SEMANTICS` 後舊 db 會被拒，這是刻意（同每日班版本綁定）。

**`fwd_ret` 與邊界的最終定義**（同 `export_dataset.py` 檔頭表；訊號日 T 在日曆位置 i，e＝i+1、x＝i+1+h，`data_end`＝日曆末日與
TAIEX 指數列末日的較小者）：
- `fwd_ret = round(adj_close(exit)/adj_open(e) − 1, 6)`，`adj_x(t)=raw_x(t)×factor_at(t)`，係數走 `feed.load_factors`（＝`adjust.cumulative_factors`
  同一支）；`mkt_ret_h = round(idx_close(exit)/idx_open(e) − 1, 6)`，指數不還原，**出場日跟個股實際出場日**（halt／delist 提前時同步提前；
  `no_entry` 時用名目 x）。`-0.0` 正規化為 `0.0`。
- e 或 x `> data_end` → `fwd_ret`／`mkt_ret_h` 空、`exit_reason` 空（e 在資料內但不能進場仍記 `no_entry`），列保留。
- e 日無列／`open` NULL 或 ≤0／非成交列（`universe.is_traded_row`）→ `no_entry`；旗標空。
- x 日非成交列 → 出場價＝[e, x] 內最後一個成交日的後復權收盤；該檔**最後一筆價格列 < x 且 < data_end** → `delist`，否則 `halt`。
  **delist 有做**，定義是「價格列永久消失」、不查 `raw_stock_info`（快照無下市日；且快照裡沒有的代號本來就不進池，生產環境這類列預期很少）。
- `entry_limit_up`／`exit_limit_down`（**近似**，A3 寫 0／1、缺值空字串）：`open(e) >= round(prev_close×1.1, 2)`／`close(exit) <= round(prev_close×0.9, 2)`，
  `prev_close`＝該日前最後一個成交日原始收盤。近似之處：不依 tick 取整、除權息日參考價未改用 `after_price`、不判鎖死。
- 只匯個股列（`stock_id ≠ '__MARKET__'`），大盤列數記在 manifest `n_market_rows_excluded`——C1 對 `SELECT COUNT(*)` 時要扣掉。

**manifest**（`bundle_io.dumps_json` 參數：鍵排序、無空白、`\n` 結尾）：`data_version`／`params_sha`／`model_version`{twse,tpex}／`text_version`／
`window`／`pool_semantics`／`segments`（各段起訖、日曆日數、計分日數、無分數的日曆日）／`h_by_horizon`／`columns`／`round_digits`／`calendar`
（首末日、`data_end`）／`user_version`{scores.db, prices.db}／`price_table_info`（`PRAGMA table_info(raw_price_daily)` 原列）／`open_quality`
（訓練＋驗證全段與各段：有列者與成交列兩口徑的 `open` NULL／≤0 列數與檔數）／`n_price_rows_off_calendar`／`factors`（load_factors 統計）／
`n_market_rows_excluded`／`files`{sha256、bytes、n_rows、exit_reason_counts、n_fwd_ret_missing、n_mkt_ret_missing、n_entry_limit_up、
n_exit_limit_down、last_signal_date_with_fwd_ret}／`head`（`git rev-parse HEAD`，非 git 目錄為 null）。

**測試**（`tests/test_export_dataset.py`，合成世界＝`build_full`＋`add_pit_rows`，`SEGMENTS` 在 fixture 內改成 DAYS[0..49]／[50..79]）：

| # | 測試 | 守什麼 |
|---|---|---|
| ① | `test_export_keys_and_score_columns_match_db_and_daily_files` | 鍵與前九欄逐列＝`scores.db` SQL 直讀＝`export_scores.py` 匯的 `data/scores/<T>.json`（三日）；manifest sha256／列數／大盤列排除數／`params_sha`／open 品質數字（163 列 4 檔／成交列 160 列 2 檔，手算） |
| ② | `test_fwd_ret_matches_hand_calc_with_all_edge_cases` | 全部 1,596 列與獨立手算（raw SQL dict＋`adjust`）零 mismatch；正常／跨除權息（1101 係數 1.25）／halt（1102）／no_entry／畸形列／delist（Y）／末日截斷逐一斷言值 |
| ③ | `test_gzip_bytes_reproducible_and_rerun_is_noop` | 兩目錄 sha256 相同、manifest 位元組相同；重跑不動 mtime、無 `.tmp`；gzip 標頭 mtime=0 |
| ④ | `test_mutations_turn_hand_check_red`（×4） | h 10→9：452 列紅（全在 short）；少乘係數：69 列紅（全是 1101、T ≤ DAYS[39]）；用 T 收盤進場：1,068 列紅；round 6→4：1,075 列紅；還原後 0（2026-09-18 實測） |
| ⑤ | `test_existing_different_target_blocks_without_force`／`test_rc2_on_non_pit_or_foreign_params_sha`／`test_rc2_on_missing_db_open_column_and_calendar_gap` | rc 1 不覆蓋且其他五檔不動、`--force` 還原；`params_sha` 假值／`pool_semantics=static-0`／`text_version` 改 → rc 2；db 缺／`DROP COLUMN open`／日曆少一日／日曆未到段末 → rc 2、不留產物 |
| — | `test_segment_train_only_writes_three_files`、`test_constants_match_pre_registration`（603／368 對 repo 日曆實算）、`test_hetzner_dataset_sh_syntax_and_expect_line`、`test_hetzner_dataset_sh_reexecs_new_script_after_pull`（含非法日期 rc 2） | |

`pytest tests -q`：877 passed／20 skipped（原 863＋14）；`ruff check scripts src tests` 錯誤數 65 → 65（新檔零錯）；`bash -n` 通過。

**未做／不確定**：
- **Hetzner 未實跑**：個股 `open` 品質、`raw_dividend_result` 欄名、六檔量級（C5）、耗時與記憶體（PriceBook 以 array 存，估 2,000 檔×1,630 日 ≈ 60 MB；
  5.5M 列 Python 迴圈估數分鐘——推測、未量）只能在 Hetzner 看 manifest 與 log。
- `hetzner_dataset.sh` 只驗到第 0 步（re-exec）與語法／結構；步驟 1～3 在本容器沒有可用的 git remote＋真實 cache，未端到端跑。
- 漲跌停旗標是 10% 近似（見上），統計層要用它做過濾前先在 Hetzner 抽樣核對 `entry_limit_up=1` 的列是否真是漲停。
- `mkt_ret_h` 對 halt／delist 列取個股實際出場日（同窗）是本批的裁量，若統計層要「名目窗」的市場報酬需另加欄。
- 只匯個股列是本批的裁量（大盤列無 `fwd_ret` 語意）；大盤側假說（TX 期貨）另匯。
- delist 在生產環境幾乎不會出現（已下市股不在 `raw_stock_info` 快照→不進池→無分數列），本批的 delist 路徑只在合成世界驗過。

## 6. Hetzner 首次實跑（2026-09-18，分支 `hetzner/dataset-2026-09-18` commit `60c867d`，`export_dataset.py @ a849a0b`）

**雲端可做的驗收（fresh-context，C1／C4／C5／欄值域）全數 PASS**：六檔 sha256／`n_rows`＝manifest；三 horizon 鍵集合逐段相同、無重複鍵；
`date` 集合＝日曆 603／368；每日列數 train 1,751～1,812／valid 1,789～1,858；`fwd_ret` 空值恰＝`no_entry`（8,678／3,475）、末日截斷 0；
各類計數＝manifest；gzip 標頭 mtime=0；`params_sha=804f05cddc6e`、`pool_semantics=pit-1`、`head=a849a0b`。
**C2／C3（對 `scores.db`／raw 價格手算）只能在 Hetzner 做**——見 `scripts/check_dataset.py`（獨立實作的抽驗器，不 import 出口程式）。

- **C5 量級**：六檔 20.76／20.48／20.54／12.88／12.74／12.77 MB，**合計 100.19 MB**（1e6），比 §0 估算 76 MB 多 31.8%——估算把六檔當等大，
  實際 train 段 603 日自然比 valid 368 日大；每列約 19.1 bytes（估算隱含 14.5）。單檔最大 20.8 MB，遠低於 50 MB 警告線。**是否照裁定進 main 待使用者確認。**
- **`open` 品質**：訓練＋驗證段成交列 `open` NULL／≤0 僅 309 列／307 檔（全列口徑 30,667 列，多為零成交日）——可接受；
  `raw_dividend_result` 欄名已在 manifest `price_table_info` 旁實查到位。
- **⚠ 極端報酬（待 C3 核對成因）**：train_mid |`fwd_ret`|>1 有 4,945 列（2021 年 3,057），集中少數檔——3095（2022-10 十日 +1,100%）、
  2364（2021-09 +585%）、6415（2022-06 −82%）、4803（2021-10/11 −82%）、6763（2024-07/08 −91%）、5278（2024-11 −89%）。樣態與
  **減資／股票分割／面額變更未還原**吻合：`src/iching/adjust.py` 的係數只來自 `TaiwanStockDividendResult`（登錄書 §1.2「還原權息」的口徑），
  減資恢復買賣參考價、分割、面額變更都不在裡面。FinMind 另有三個資料集（2026-09-18 查官方文件 `finmind.github.io/tutor/TaiwanMarket/Fundamental/`）：
  `TaiwanStockCapitalReductionReferencePrice`（`ClosingPriceonTheLastTradingDay`／`PostReductionReferencePrice`／…）、`TaiwanStockSplitPrice`
  （`before_price`／`after_price`）、`TaiwanStockParValueChange`（`before_close`／`after_ref_close`），皆為 before／after 價對，可套同一支
  `adjust.event_factor`。**這不只影響 `fwd_ret`——計分也吃後復權收盤（`feed.day_records`），那幾檔那幾天的 `line_2`／`line_4` 同樣被假跳空污染。**
  處置待裁定（甲：納入三個事件源重建係數→ factors.json／全量重播／重匯；乙：維持登錄書口徑、統計層 winsorize 並揭露）。
- **兩則觀察（非缺陷）**：①`|值|<1e-4` 的 `fwd_ret`／`mkt_ret_h` 以科學記號寫出（`-3.1e-05`），`float()` 可讀、regex 解析要留意；
  ②`in_rank_pool=1` 但 `base_score` 空的列（train 每 horizon 約 2,800～3,250、valid 3,470～4,088，皆 `coverage=reweighted`）——照裁定 #34
  帶旗標不刪列，統計層算 IC 時自然落掉，要在報告揭露。
- **合理性統計（只是 sanity）**：`fwd_ret`／`mkt_ret_h` 的 std 隨 h 單調擴大；halt／delist 列分布正常（無「全 −1」）；
  `base_score` 對 `fwd_ret` 的 Spearman 全檔 −0.015～−0.061、日 IC 均值 ±0.03 內——**不可當結論**。
