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

### 6.1 Hetzner 端 C1～C3 實跑（2026-09-18 16:1x UTC，`check_dataset.py @ 08387ec`，`--sample 300 --seed 7`）

**rc=0、mismatch 合計 0，耗時 231 秒**：C0 manifest 13 項 0 不符；C1 六檔列數＝db（1,078,145×3／669,841×3）、鍵集合＝db、
日期 603／368 全齊、同段三檔鍵序列相同；抽樣 2,339 列（每檔 390：halt／delist／no_entry／漲停各 20＋隨機 220＋跨除權息 30 檔 90 列）
C2 五個分數欄 0 不符、C3 `fwd_ret`／`mkt_ret_h`／`exit_reason`／兩旗標 0 不符。`raw_dividend_result` 欄名實查：
`before_price, after_price, stock_and_cache_dividend, stock_or_cache_dividend, max_price, min_price, open_price, reference_price`。
**出口工具對真實資料的驗收 C1～C4 全部通過；C5 量級 100.19 MB 待裁定。**

### 6.2 極端報酬成因已確認＝減資／分割／面額變更未還原（raw 價格實查）

| 檔 | 訊號日 T | 匯出 `fwd_ret` | raw 價格證據 |
|---|---|---|---|
| 3095 | 2022-10-14（short） | **+11.0** | 10-04～10-13 列全為 `halt`（出場日無成交）；10-19 起 `no_entry`；raw 10-14 open 2.55、10-19 close 2.77，**視窗內無除權息列**——出場價只能是停牌後恢復買賣的價格（≈30，＝減資後參考價），raw 價格在恢復日不連續 |
| 6415 | 2022-06-28（short） | **−0.785** | raw 06-28 open 2600 → 07-05 2485 正常；−78% 對應 07 月 1→4 分割（2,600→≈600） |
| 6763 | 2024-07-11～08-27 | −0.90～−0.91 | raw 全程 380～491 無跳空；除息列 07-15 before 436／after 426.9 正常；−90% 對應面額 10→1 變更（≈450→45） |
| 2364 | 2021-09-23（short） | **+5.85** | raw 3.0～3.7 正常，無除權息列；+585% 對應減資恢復買賣 |

**結論**：`fwd_ret` 算式與係數都對（C3 零不符），錯在**還原係數的事件源只有 `TaiwanStockDividendResult`**——減資／分割／面額變更的
恢復買賣參考價不在裡面，raw 價格在恢復日不連續，出口照實算出 ×12／÷4／÷10。**同一批事件也污染計分**（`feed.day_records` 用同一套
後復權收盤）。FinMind 三個對應資料集（§6）皆為 before／after 價對，可套同一支 `adjust.event_factor`。處置待裁定（甲／乙／丙見 §7）。

## 7. 減資／分割／面額變更納入還原係數（裁定 #51：甲，2026-09-18）

驗收條件（動手前寫；細項待盤點 agent 回報後補齊）：
- 三個事件源在 Hetzner `prices.db` 落地（各自 raw 表、`coverage`／`failures` 慣例同既有 DatasetSpec），欄名以 `PRAGMA table_info` 實查為準。
- 係數合併規則寫死並有測試：同一 `(stock_id, date)` 多源時的優先序；`FACTOR_MIN`／`FACTOR_MAX` 對減資（係數可到 10 以上）要重新裁定範圍；
  事件日語意（恢復買賣日 vs 除權息日）對齊 `adjust.py` 的 `ex_date ≤ t` 規則。
- `factors.json` 重建後與舊檔比對：只新增事件、既有除權息係數逐位不變。
- 每日班 collect 對三表做日切片抓取（同 dividend 的做法），測試含空日與非交易日。
- 全量重播（Hetzner 一句話貼）後：3095／6415／6763／2364 那幾列的 `fwd_ret` 回到 raw 連續價量級；重匯資料集 |`fwd_ret`|>1 的列數對比。
- 登錄書 §1.2 更新；`params_sha` 是否納入「事件源版本」待裁定（會讓舊 scores.db 被拒，與 PIT 切換同型）。

### 7.1 探測項 P1～P7 與一句話貼（動手寫 DatasetSpec 之前，在 Hetzner 跑；`scripts/probe_adjust_sources.py`）

三個資料集在本 repo **零實測**（家族管線也沒用過），DatasetSpec 的策略（區間切片 vs 逐日切片）、欄名、`date` 語意都不能憑印象寫。
先探七件事，結果貼回本節再定 spec：

| 項 | 問題 | 怎麼探（FinMind 呼叫數） |
|---|---|---|
| P1 | 現有 token 打三個資料集是否可打（`iching.fm.classify_response`：permission／quota／error 分開記） | 各 1 次 2022 全年全市場區間（3；P2／P6／P7 重用） |
| P2 | 全市場區間查詢是否有 `TaiwanStockDividendResult` 同型怪癖（只回 start_date 當天） | 三段已知事件窗（3095 減資 2022-10-10～10-31、6415 分割 2022-07-01～07-15、6763 面額 2024-08-01～09-02）各 1 次區間＋逐曆日 `start=end=d`（3＋70） |
| P3 | `date` 是「恢復買賣日」還是「最後交易日」 | 3095／6415／6763／2364 逐檔 `data_id` 查全期（12），與 `prices.db` `raw_price_daily`（唯讀 `mode=ro`）前一交易日／同日／後一交易日 close 並列，程式只給提示、定案由人看 |
| P4 | 欄名集合與型別樣本 | 不另打，彙總前面回列 |
| P5 | 無事件交易日／非交易日各回什麼（P2 逐日窗含週末與 2022-10-10 國慶日） | 不另打，取自 P2 |
| P6 | SplitPrice × ParValueChange 同 `(stock_id, date)` 是否各出一列 | 不另打：P3 逐檔結果＋2022 全年區間 join |
| P7 | 2020～2026 逐年區間列數 | 逐年 6×3（2022 重用 P1）；P2 未證明區間完整者標「不可信」，只能說「至少這麼多」 |

合計約 106 次、`--max-calls` 150 上限、預設節流 `config.DEFAULT_INTERVAL_SEC`（0.7 s）；全程唯讀、不寫 `prices.db`；token 走 `iching.fm`
lazy 載入、不印不寫。任一資料集 P1 失敗 → 後續各 P 對它標 `skipped`。離線測試 `tests/test_probe_adjust_sources.py`（FakeFM）。

```bash
cd ~/taiwan-stock-iching && git pull --ff-only
python3 scripts/probe_adjust_sources.py --out cache/logs/probe_adjust_sources.json 2>&1 | tee cache/logs/probe_adjust_sources.txt
```

跑完把 `cache/logs/probe_adjust_sources.txt` 全文貼回（JSON 留在 Hetzner，需要時再取）；**結果尚未回填**，DatasetSpec 等它。

### 7.2 探測結果（Hetzner 2026-09-18，`probe_adjust_sources.py @ 2a85d83`，106 次呼叫）與定案

| 項 | 結果 | 定案 |
|---|---|---|
| P1 權限 | 三源以現有 token 皆 200 | 可回補 |
| P2 區間 vs 逐日 | 三源皆「一致」（無 DividendResult 那種只回首日的怪癖） | 回補 `range_slice`（chunk=year）；每日班一次區間查詢（T−7～T）即可，不必逐日 |
| P3 `date` 語意 | 4/4 為**恢復買賣日**：3095 capred 2022-10-31（前一交易日 close 2.77＝`ClosingPriceonTheLastTradingDay`、當日 close 30.0≈`PostReductionReferencePrice` 30.27）；6415 split 2022-07-13（2,485→621.25，當日 560）；6763 split 2024-09-09（491→49.1，當日 46.6）；2364 capred 2021-10-08（3.04→24.01，當日 21.65） | `date` 直接當 `ex_date`，沿用 `adjust.factor_at` 的 `ex_date ≤ t`，**不需 next_trading_day** |
| P4 欄名 | 與官方文件相同；`SplitPrice.type` 值例 `面額變更` | before／after 對：capred `ClosingPriceonTheLastTradingDay`／`PostReductionReferencePrice`；split `before_price`／`after_price`；parvalue `before_close`／`after_ref_close` |
| P5 空日 | 無事件日與非交易日皆 HTTP 200 空陣列（`empty`） | `range_slice` 全年 `empty` 合法（2023 分割／面額為 0 列）；**不得**設 `empty_ok_partial` |
| P6 跨表重疊 | 分割 × 面額變更 2022 全年 5 筆全重疊（同一事件兩表各一列，值相同） | 合併時 **split∪parvalue 以 `(stock_id,date)` 去重（優先 split）**；dividend／capred 與之為不同事件、同日相乘 |
| P7 量級 | capred 254 列／split 33／parvalue 15（2020～2026-08） | 三表合計約 300 列，遠小於除權息 10,849 |
| 附帶 | `TaiwanStockParValueChange` **不接受 `data_id`**（HTTP 400 `parameter data_id don't provide`） | 該 spec 不得配 `fallback="per_stock"` |

**係數方向與 band（`before/after`）**：除權息 ≥1（既有）；分割／面額 ＝倍數（4、10）；**減資 <1**（3095 ≈0.0915、2364 ≈0.127）。
定案：band 改為按源分段——dividend `[0.99, 5.0]`、split／parvalue `[1.5, 12.0]`、capred `[0.02, 1.01]`；`anomalies()` 接進
`feed.load_factors`／`daily_core.factors_from_rows` 的 stat 並在 `scan_features`／`export_seed`／`daily_run` log 印出（**只報不擋**，
同 2026-09-12 人工複核 10,664 筆的做法）。

### 7.3 驗收條件（定案版，取代 §7 骨架）

- A `src/iching/config.py`：三個 `DatasetSpec`（`cap_reduction`／`split_price`／`par_value_change`，`db="prices"`、`range_slice`＋`chunk="year"`、
  `start=PRICE_WARMUP_START`、`depends=("stock_info",)`、不設 `apply_landing_filter`／`empty_ok_partial`；`par_value_change` 無 per_stock fallback）；
  `verified` 標明「2026-09-18 Hetzner probe」；`OUT_OF_SCOPE` 第 6 條改寫；`tests/test_backfill_offline.py`／`test_paths_matrix.py`／`test_landing_filter.py` 綠。
- B `src/iching/adjust.py`：`ADJUST_SOURCES = "div+capred+split+par-1"`；按源 band 常數與 `anomalies(events, source)`；`Event` 不擴欄。
- C 兩層同步（**五處同一套規則**，以 `export_seed` 的等值守門與 parity 為機器守門）：`feed.load_factors` 讀四表 → 正規化 `(sid, date, before, after)`
  → split∪parvalue 去重（優先 split）→ 與 dividend／capred 合併（同 `(sid,date)` 同源 keep-first；跨源同日相乘）；`daily_core.factors_from_rows`
  逐字對齊；`export_seed.export_factor_rows` 同序 UNION；`check_dataset._load_factors` 第三份同步；`factors.json` **維持 4 欄、不 bump `FILE_SCHEMA`**，
  頂層加 `"sources"` 新鍵記來源版本。
- D 每日班：`daily_fetch` 三源各一次區間查詢（T−7～T，不進 `CORE_REQUIRED`）、`shape` warning 比照 dividend；`daily_pipeline.update_factors` 接多源列。
- E `run_common.build_params_payload` 加 `adjust_sources`（舊 `scores.db`／`cross.json`／`data/scores` 全部作廢，同 PIT 切換）。
- F 測試：`test_adjust`（減資 <1／分割 4／面額 10／同日 dividend×capred 相乘／split×parvalue 去重）、`synth_db` 以「追加不動既有」方式加三源列、
  `test_feed`／`test_daily_core`／`test_daily_run`（三源當班進 factors.json、空回應）、`test_export_dataset`／`test_check_dataset` 抽樣涵蓋新事件日。
- G Hetzner 順序：回補三表 → `report` → 係數複核腳本（`anomalies` 清單＋每源筆數＋跨源重疊）人看 → `scan_features --rebuild` → `replay_scores --rebuild`
  （≈12.6 h）→ `check_scores` → 重匯資料集＋`check_dataset`（3095／6415／6763／2364 那幾列回到 raw 連續價量級、|`fwd_ret`|>1 列數對比 4,945）
  → `export_seed` → 雲端 `recompute_from_seed` 覆蓋 09-01 起分數與 cross.json → parity → push。
  **重播本身的一句話貼（`scripts/hetzner_replay.sh`，2026-09-20 補）**：`tmux new -d -s replay 'bash scripts/hetzner_replay.sh'`。
  本節 G 原本只寫「`scan_features --rebuild` → `replay_scores --rebuild`」，而 `hetzner_adj.sh` 守門 a 要的
  `== replay exit 0` 標記**在 repo 裡沒有產生端**（上一輪是人手打的 tmux 一句話、原文沒進版控），這支把它補起來：
  window 一律取自 `data/state/cross.json`（與 `hetzner_adj.sh` 守門 c 同一路徑，不再有人手打錯的機會）、
  標記保證是 log 的最後一行且 rc 是真的、開跑前既有 log 改名 `<log>.prev-<UTC>`（舊標記不得跨輪沿用）、
  中斷後重貼同一行以 `--resume` 續跑（標記檔綁 `model_version` 指紋，換了參數一律重新 `--rebuild`）。
  **刻意不跑 `scan_features`**（features.db 的參數指紋不含 `model_version`，見 `docs/P3-CALIBRATION.md` §15），
  要重掃設 `HETZNER_REPLAY_SCAN=1`。測試 `tests/test_hetzner_replay.py`。
  **重播完成後的一句話貼（`scripts/hetzner_adj.sh`，2026-09-18）**：`tmux new -d -s adj 'bash scripts/hetzner_adj.sh 2026-09-14'`
  （TO＝`scores.db` 末日；第二參數 FROM_SCORES 預設 2026-09-01）。骨架逐段照 `hetzner_pit.sh`（自我複製後執行、pull 後 HEAD 前進即以新版
  重跑、log `cache/logs/adj-round-*.log`、`--force-with-lease` 用 `rev-parse --verify -q`），做 `check_scores` → `export_seed`（window 取
  `data/state/cross.json`，須＝db 記的）→ `export_scores 09-01..TO --force` → `export_dataset --force`＋`check_dataset --sample 300 --seed 7`
  （rc 非 0 即停）＋`adj_event_report.py`（manifest 摘要、每檔 |`fwd_ret`|>1 列數、3095／6415／6763／2364 事件窗 `fwd_ret`，寫
  `runs/adj/`）→ commit 到 `hetzner/adj-<TO>` 並 push。**三道守門**：`cache/logs/replay-adj.log` 末行須含 `== replay exit 0`（否則拒跑並印末 3 行）；
  `replay_meta.params_sha`＝現行碼指紋（直接呼叫 `export_dataset.check_params`，含 pit-1 與 `adjust_sources`＝`adjust.ADJUST_SOURCES`）；
  守門會印 db 末日，**TO 晚於它拒跑**（不確定末日就先隨便給一個早的日期看它印什麼，再重貼）。雲端 `recompute_from_seed`／parity 仍另做。
- H 文件：登錄書 §1.2 口徑改「還原權息與減資／分割／面額變更」；`docs/BACKFILL-RUNBOOK.md` §4.4／§7／§8；`docs/P2-DAILY-PLAN.md` §7.4.1。

### 7.4 實作交付（2026-09-18，分支 `claude/dazzling-maxwell-serk13`；A～F、H 已做，G＝Hetzner 步驟未做）

**檔案**：

| 檔 | 內容 |
|---|---|
| `src/iching/factor_sources.py`（新） | 四源欄位對映 `SOURCES`（表名＝`config.DatasetSpec.table`、before／after 欄名＝探測 P4）＋**合併規則唯一實作** `merge_factor_rows`＋讀取端最後一步 `build_factors`（不去重）＋`normalize_rows`（FinMind dict 列 → 4 欄）＋`format_source_stat`（log 一行） |
| `src/iching/adjust.py` | `ADJUST_SOURCES = "div+capred+split+par-1"`；按源 band `FACTOR_BAND`（dividend `[0.99, 5.0]`／split・parvalue `[1.5, 12.0]`／capred `[0.02, 1.01]`）＋聯集 `FACTOR_BAND_ANY`；`anomalies(events, source="dividend")`（`None`＝聯集）；`FACTOR_MIN`／`FACTOR_MAX` **移除**；`Event` 不擴欄；檔頭口徑改四源 |
| `src/iching/config.py` | `cap_reduction`／`split_price`／`par_value_change` 三個 `DatasetSpec`（`prices`、`range_slice`、`chunk=year`、`start=PRICE_WARMUP_START`、`depends=("stock_info",)`、`tier=sponsor`、`verified="hetzner-probe(2026-09-18)"`、無 fallback、不設 `apply_landing_filter`／`empty_ok_partial`、**`empty_ok_for=("range_slice",)`**（驗收後修正 (a)，白名單 `EMPTY_OK_RANGE_SLICE_KEYS`）；`OUT_OF_SCOPE` 第 6 條改寫；`_check_registry` 加四源守門（表在 prices、`par_value_change` 無 fallback、三表 range_slice+year） |
| `src/iching/feed.py` | `load_factor_rows(conn, dv)`（四表同一 dv → 合併；**缺表視為 0 列**記 `missing_tables`、**meta-only 空表視為 0 列**記 `meta_only_tables`（`factor_table_state`）、**表有列但無本 dv → `FeedError`**（`require_dv_rows`；驗收後修正 (b)(c)，取代原 `dv_missing` warning））、`load_factors_full`（多回合併統計）、`load_factors`（介面不變，stat＝`build_factors` 的） |
| `src/iching/daily_core.py` | `factors_payload` 頂層加 `sources`（收 5 欄列只寫前 4 欄）；`factors_from_rows`＝`build_factors`（**不再 keep-first**）；`load_factors_file` 驗 `sources`（缺或不同 → `DailyCoreError` 要求重匯種子）、4 欄守門與 `FILE_SCHEMA=1` 不動 |
| `src/iching/daily_fetch.py` | `FACTOR_LOOKBACK_DAYS=7`、`FACTOR_RANGE_SOURCES=(capred, split, parvalue)`：三源各 1 次 `start=T−7, end=T`（不帶 data_id）；`extras[<source>]`＝`normalize_rows` 結果；`counts[<source>]`；窗外 date → `<source>:shape(...)`；不進 `CORE_REQUIRED` |
| `src/iching/daily_pipeline.py` | `update_factors(root, {source: rows}, dv) -> (added, merge_stat)`：合併 → 對檔內 `(stock_id, date)` keep-first 追加；log 印每源筆數，band 外事件另印一行 |
| `src/iching/run_common.py` | `build_params_payload` 加 `"adjust_sources": ADJUST_SOURCES` → 參數指紋變 |
| `src/iching/replay_io.py` | `ReplaySource.factor_source_stats`（合併統計） |
| `scripts/export_seed.py` | `export_factor_rows`＝`feed.load_factor_rows`（四表 UNION 同序、合併後列）；等值守門 `factors != src.factors` 不動；末尾印合併統計 |
| `scripts/check_dataset.py` | `_load_factors` 讀四表（每表內 keep-first＋衝突計數自己寫；meta-only 空表視為 0 列、表有列但無本 dv → 中止，與 `feed` 同一規則、不 import feed）→ 共用 `merge_factor_rows` → `cumulative_factors`；`div_events` 含四源事件日（強制抽樣層改「還原事件」）；報告 `dividend_table` 鍵名不變、內容加 `sources`／`by_source`／`cross_source_dup`／`merged_rows`／`anomalies`／`anomaly_rows`／`tables`／`missing_tables` |
| `scripts/scan_features.py`／`replay_scores.py`／`export_dataset.py`／`probe_features.py` | 開頭印「還原係數 事件源 …」一行（每源筆數／跨源去重／band 外，只報不擋）；`export_dataset` manifest 加 `factor_sources`／`factor_anomaly_rows` |
| `tests/` | 新 `test_factor_sources.py`（8 支）；`test_adjust`（減資 0.0915／分割 4／面額 10／同日 dividend×capred 相乘／按源 band／聯集 band）；`synth_db.add_adjust_source_rows`（**追加不動既有**，預設 1101 減資 DAYS[60]、2330 分割 DAYS[65]＋面額變更表同鍵副本、6488 面額 DAYS[70]）；`test_feed`（四表合併、缺表、減資日起 adj<raw、×4 非 ×16、×10）；`test_daily_core`（`sources` 守門、不去重）；`test_daily_run`（三源當班進 factors.json＋端到端逐位 parity、三源抓取形狀／窗外警告／空回應、`update_factors` 多源）；`test_export_dataset`／`test_check_dataset`（手算改四源、抽樣涵蓋新事件日、manifest 合併統計）；`test_backfill_offline` 鍵快照 +21 鍵 |
| 文件 | `docs/pre-registration.md` §1.2.1 價格口徑、`docs/BACKFILL-RUNBOOK.md` §4.4／§7 #24～#26／§8、`docs/P2-DAILY-PLAN.md` §4／§7.4.1、本節 |

**merge 規則最終定義**（`factor_sources.merge_factor_rows`，五處共用：`feed.load_factor_rows`／`daily_pipeline.update_factors`／
`export_seed.export_factor_rows`／`check_dataset._load_factors`／測試手算照文字另寫一份對帳）：
1. 每源內：`date` None 跳過；同 `(stock_id, date)` keep-first（`dup_skipped`）；before／after 非數（含 NaN）或 ≤0 跳過（`bad_skipped`），
   壞的首列不佔鍵（與 2026-09-12 起的 `feed.load_factors` 逐字同一規則）。
2. split ∪ parvalue 以 `(stock_id, date)` 去重、**優先 split**（`cross_source_dup`；parvalue 獨有者保留）。
3. dividend／capred／(split∪parvalue) 為不同事件，同 `(stock_id, date)` 各自保留 → `adjust.cumulative_factors` 同日相乘。
4. 輸出依 `(stock_id, date, 來源序 dividend<capred<split<parvalue)` 排序；每列 `(stock_id, date, before, after, source)`。
5. 統計：`by_source{rows, kept, dup_skipped, bad_skipped, anomalies}`、`cross_source_dup`、`anomalies`＋`anomaly_rows`（按源 band，**只報不擋**）。

**`factors.json` 語意變化**：每列仍 4 欄、`schema` 仍 1；頂層新增 `"sources": "div+capred+split+par-1"`，缺或不同一律拒讀。
`rows`＝合併後事件列，同 `(stock_id, date)` 多列是**不同事件**，讀回 `build_factors` 相乘、不再 keep-first（測試 `test_build_factors_does_not_dedupe_merged_rows`
與 `test_daily_core.test_factors_file_requires_matching_sources_and_keeps_four_columns` 釘住）。每日班 `update_factors` 對檔內 `(stock_id, date)` keep-first：
同一批合併輸出裡的同日雙事件都追加；**已知限制**＝某鍵已在檔內後才落地的另一源同日事件會被擋（檔內不帶來源、無從分辨改值與另一事件），
四源同窗抓取下極罕見，且 Hetzner 重匯種子即由 DB 全量重建。

**合成世界分數有變、原因**：`add_adjust_source_rows` 沒有進 `build()`／`build_full()`，只在四個世界明呼叫——`test_feed`（fixture）、
`test_daily_run`（事件放 K+3／K+4／K+5，種子刻意拿掉、由每日班當班抓回，兩條路徑仍逐位相同）、`test_export_dataset`／`test_check_dataset`
（事件在 valid 段 60／65／70）。這四個世界自 2026-09-18 起 1101（60 日起 ×0.5）、2330（65 日起 ×4）、6488（70 日起 ×10）的後復權收盤
與跨過事件日的 `fwd_ret` 都變了；受影響的斷言已更新且註明「裁定 #51 前為 N」：`test_feed` `stat["stocks"]` 1→3、`test_check_dataset`
`dividend_table.stocks` 1→3、`test_export_dataset` `no_factor` 突變的受影響代號 `{1101}`→`{1101, 2330, 6488}`。`test_pit_world`／`test_daily_core`／
`test_daily_entrants`／`test_parity_check`／`test_recompute_from_seed` 等世界**未加事件、分數不變**。`tests/test_backfill_offline.py` 的鍵快照
14,262→14,283（+21＝三個 range_slice 各 7 年塊），剔除三者後的子集 sha 與舊快照逐位相同（同日實算，測試新增這條斷言）。

**`pytest tests -q`**：914 passed／20 skipped（改前 897＋新增 17，2026-09-18 本容器實跑 95.7 s）；`ruff check` 本批新增／修改的 26 個 `.py` 檔零新錯（`tests/test_backfill_offline.py:119` 的 E702 是既有）。

**參數指紋變了（E）**：`build_params_payload` 多 `adjust_sources` → 舊 `cache/scores.db`（`params_sha=804f05cddc6e`）、`cache/scores.db.state.json`、
repo `data/state/cross.json`、`data/scores/*.json`、`data/factors.json`（無 `sources` 鍵）**全部作廢**：`replay_scores --resume`／`scan_features`（不 --rebuild）／
每日班 `run_offline` 都會被指紋或 `sources` 守門拒掉，只有 Hetzner 全量重跑＋重匯種子能接上（§7.3 G）。

**未做／不確定**：
- **G 全部未做**（本容器無 Hetzner、無 token）：三表回補 → `report` → 係數複核（`scan_features`／`export_seed` 開頭那一行＋ `anomaly_rows`）人看 →
  `scan_features --rebuild` → `replay_scores --rebuild`（≈12.6 h）→ `check_scores` → 重匯資料集＋`check_dataset`（3095／6415／6763／2364 那幾列回到 raw
  連續價量級、|`fwd_ret`|>1 列數對比 4,945）→ `export_seed` → 雲端 `recompute_from_seed` → parity → push。指令已寫進 `BACKFILL-RUNBOOK.md` §4.4。
- 三個 `DatasetSpec` 在 `range_slice` 下**零實跑**（探測是唯讀、未走 `run`／`Store.record_success`）；欄名／年塊列數要看首次 `report`（runbook §7 #24～#26）。
- 合成世界的價格沒有跳空，事件只改係數，等於在 60／65／70 日製造人造不連續——測的是機制與 parity，不是「還原後價格連續」；後者只有 Hetzner 真資料
  （3095 等四檔）看得到。
- `feed.load_factor_rows` 四表用**同一個 dv**（以 `raw_dividend_result` 解析）；三表若以不同批號回補，~~會落到 `dv_missing` 警示、係數缺這三源~~
  **→ 2026-09-18 驗收後修正 (c)：改成 `FeedError` 大聲停下**（見下方「驗收後修正」第 3 點）——
  `backfill_hetzner.py` 預設沿用 cache 內既有 dv（`resolve_data_version`），照 §4.4 指令跑不會發生。
- `update_factors` 的 keep-first 限制（見上）刻意不改成 5 欄檔——裁定要求維持 4 欄、不 bump `FILE_SCHEMA`。



**驗收後修正（2026-09-18，fresh-context 驗收綁 `8a64404` 指出的問題 1（中）與 Hetzner 風險 2；接續上方「未做／不確定」）**：

1. **(a) 三表空年塊原被記成 `empty_unexpected`**——`backfill_hetzner.run_dataset` 對 `not rows and strategy not in spec.empty_ok_for` 記
   `record_failure(EMPTY_UNEXPECTED)`、不寫 coverage，`cmd_run` 任一 failed → rc=6，且之後每次 `run` 都重打空塊（驗收以 FakeFM 只在 2022 回列實跑：
   cap_reduction planned=7 ok=1 failed=6）。但探測 P5 實證 2023 分割／面額變更整年 0 列是**真實情況**。修法：三個 spec 設
   `empty_ok_for=("range_slice",)`，`config._check_registry` 由「`empty_ok_for` 只准 per_stock」改成**白名單** `EMPTY_OK_RANGE_SLICE_KEYS=(cap_reduction, split_price, par_value_change)`
   恰宣告 `("range_slice",)`（其餘 spec 規則一字不變，且白名單不得配 fallback）。**代價**：`empty` coverage 在同 `data_version` 下是黏的
   （`store.py` 語意），FinMind 暫時回空要人工清 coverage 重抓——清法（`--from/--to --force` 或 SQL 刪 coverage 鍵）寫在 `docs/BACKFILL-RUNBOOK.md` §7 #25 附註。
   測試：`tests/test_backfill_offline.py::test_run_dataset_event_source_year_blocks_empty_is_legal`（三表 planned=7、empty=6／7、failed=0、重跑全跳過且零請求；
   dividend_result 的 range_slice 整年空仍 `empty_unexpected`）、`tests/test_paths_matrix.py::test_empty_ok_declared_only_for_per_stock`（白名單外守門不變）。
2. **(b) meta-only 表守門**——`record_success([])` 會先 `ensure_raw_table` 建出只有 meta 欄（`cov_key,row_hash,data_version,date,stock_id,extra`）的表；
   某表若空年塊先落地、之後沒有非空塊（或 run 中斷），原 `feed.load_factor_rows` 會 `FeedError: raw_par_value_change 缺欄位`。現改：表在、缺該源 before／after 欄、
   **且表內零列** → 視為 0 列並記 `stat["meta_only_tables"]`（`feed.factor_table_state`，`format_source_stat` 印「meta-only 空表視為 0 列」）；表**有列**卻缺欄 → 仍 raise（真的壞）。
   `check_dataset._load_factors` 同一規則（自己寫、不 import feed；`raw_dividend_result` meta-only 仍中止——主表）。
   測試：`tests/test_feed.py::test_load_factor_rows_meta_only_table_is_zero_rows_and_dv_mismatch_raises`（用 `Store.record_success(spec, key, [], …)` 真實路徑建 meta-only 表）、
   `tests/test_check_dataset.py::test_factor_table_meta_only_is_zero_rows_and_dv_mismatch_aborts`（同一組 DB 對 feed 與 check_dataset 各驗一次、結論相同）。
3. **(c) Hetzner 風險 2：四表 dv 不一致靜默少源**——原 `load_factor_rows` 對「表有列但無本 `data_version` 的列」只記 `dv_missing` warning，係數會靜默缺整個事件源。
   現改 **raise `FeedError`**（`feed.require_dv_rows`，訊息列出表內 `DISTINCT data_version` 與期望 dv；判準是 `dv not in got`，不看 `date IS NOT NULL` 過濾後的列數）；
   表零列或不存在仍是 0 列不 raise。`check_dataset._load_factors` 同步（→ `CheckAbort`、rc 2）。`stat["dv_missing"]` 鍵移除（不再有「有列但無本 dv 仍繼續」的狀態）。
   測試同上兩支（某表塞另一個 dv 的列 → raise／abort）。
   全套 `pytest tests -q -p no:cacheprovider` 914 → **917 passed**（+3）；改動檔 ruff 零新增項。

### 7.5 Hetzner 重播與第四次覆蓋（2026-09-19；G 段實跑＋雲端重算＋進 main）

**Hetzner（`hetzner_adj.sh @ d78293d`，分支 `hetzner/adj-2026-09-14` commit `850a947`，log `cache/logs/adj-round-20260919T075344Z.log`）**：
`scan_features --rebuild` 1,628 日 412s → `replay_scores --rebuild --window 320` 約 12.3 h（`== replay exit 0`）→ 三道守門全過
（`params_sha=a6a3f35cd1f0`、`db_adjust_sources=div+capred+split+par-1`、db 末日 2026-09-14＝TO、window 320）→ `check_scores` 末日
coverage full 4,992／reweighted 828、排名池 850/1,940 → `export_seed --window 320`（factors 11,035 列含 `sources`）→ `export_scores`
09-01～09-14 十檔全「覆蓋（原檔 differs）」→ `export_dataset` 六檔 100,185,081 bytes → **`check_dataset --sample 300 --seed 7` rc=0**
（C0 manifest 13 項 0 不符；C1 六檔列數＝db、鍵集合＝db、train 603 日／valid 368 日無缺日、三檔鍵序列相同；C2 分數欄 2,340 列 0 不符；
C3 `fwd_ret`／`mkt_ret_h`／`exit_reason`／兩個 limit 旗標各 2,340 列 0 不符；耗時 439.6s）→ `adj_event_report` → commit／push rc=0。

**極端報酬對比（雲端 fresh-context 子代理，舊 `60c867d` vs 新 `850a947`；腳本在 scratchpad、不進 repo）**：
- `|fwd_ret|>1`：train_mid **4,945 → 4,038**（§6 的 4,945 是 train_mid 單檔，不是六檔合計）；六檔合計 9,864 → 8,168；
  新版 max 4.196（舊 13.96＝3095 減資未還原，已修）；exit ok 8,166／halt 2。年份 2021 3,996→3,337、2022 1,061→629、2023 2,266→1,777、2024 2,541→2,425。
- 依檔：舊有新無 21 檔 545 列（3321／1438／2321／1512／3043／6225／1472／6404／4131／5701／8077…，每檔集中單一 40 交易日窗＝典型減資跨窗，全被 capred 源清掉）；
  **新有舊無 0 檔**；列級「修掉」1,697 列／57 檔（2364 old max 6.33→−0.07、3095 13.96→0.37、5314 4.12→0.25）。列級「新冒出」只有 1 列
  （6763 valid_mid 2024-08-22，old −0.795→new +1.051，比值恰 10＝面額 10→1 的 split，鄰列連續 0.75／0.95／1.05／0.82＝還原正確後露出的真實 +100% 行情）。
  → 新三源**零新冒出列、方向全對、未發現係數用反或誤併**。
- 殘留判讀：兩版都有的 top15（5314／2609／3228／2615／6550／5484／2364／2465／8374／2636／8054／5475／2743／6442／6419）mid 序列全是「漸入 >1 區、峰在段中央、漸出」的鐘形，
  相鄰 signal 比值 0.756～1.39 全在漲跌幅可及範圍、無跨日平台形；2021 前段＝航運（2609／2615／2636／2642／5608／2614／2603）＋鋼鐵（2014）＋當年妖股（5475／6550），
  同期 `mkt_ret_h` 中位 +2.5%～+9%。物理上不可能的跳階（相鄰比值 >1.5 或 <0.667）且無事件者只剩 5 檔 20 列，分布與**興櫃期／掛牌首週無漲跌幅**吻合
  （TPEx 側 9/9 以 `mopsfin_t187ap03_O` 上櫃日查證；TWSE 側 17 檔因 openapi.twse 對本沙箱封鎖**未查證、屬推測**）。
  **結論：殘留列主要是真實行情（子代理信心約 85%）**；偵測邊界＝係數 1.10～1.23 的錯置事件藏在 ±10% 內偵測不到，「無其他錯置」不能斷言。
- **已確認的殘留錯誤 1 筆（範圍外，本批不修）**：2429 銘旺科 `TaiwanStockDividendResult` 2024-07-02 before 38.9／after 29.15（配股 9.74），
  但 `TaiwanStockPrice` 07-01 close 38.9 → 07-02 open 41.5／high 42.75（＝漲停，若除權生效參考價應為 29.15）——**主對話以 FinMind 公開 API 獨立核實**。
  除權當日實際未生效、真正生效日未查，係數照套後 07-02 起整段被乘 1.3345＝還原程序自己製造的 +33% 假斷層，約 32 列假 >1（8,168 的 0.4%）。
  舊版（純除權息）就有，非本輪三源引入。**候選修法（另案裁定）**：報告層加「除權息係數 vs 事件日 raw 價缺口」的一致性檢查（係數 f 但 raw close(ex−1)/open(ex) 遠離 f
  且落在漲跌幅內 → 標「事件日存疑」），先只報不擋；要擋要先量誤報率。

**雲端重算 09-15～09-18（`recompute_from_seed.py @ d78293d`，Python 3.12.3）**：
- 種子＝合成 commit `5895d31`＝`850a947` 的樹去掉 `runs/collect/2026-09-15..18-daily.json.gz`（同 §6.8／P3-PIT-POOL 第三次覆蓋做法；1,628 份＝db 日數）。
- **`--data-ref` 不能直接用分支**（第一版重算即如此，四日「只在現行 3」、diag 差 `n_stock_any_unknown`／`n_stock_rows`／`n_stocks`）：
  主線 `factors.json` 比分支多 **101 列** dividend（20 列 ex_date ≤09-14 幾乎全 ETF＋9105／9941A；81 列 09-15～18 含 2330／1517／1599／3675／4763／5426／6830／6924 等 12 檔個股），
  是每日班 09-15～18 的 `[T−7,T]` 帶抓補進的、Hetzner 09-11 快照沒有；主線 `pool.json`／`fundamentals.json` 也較新。與 P3-PIT-POOL「`--data-ref` 必須是主線」同一理由。
  做法：以每日班**同一支** `daily_pipeline.update_factors(root, {"dividend": 主線多的 101 列}, dv)` 併進分支四源檔（keep-first、`sources` 保留）→ 11,136 列；
  合成 data-ref commit `b824c59`＝`origin/main`（`d78293d`）的樹只換 `data/factors.json`。第二版重算：四日 **「只在重算 0／只在現行 0、diag 差欄 無」**，
  rows 5,841／5,844／5,838／5,841＝主線現行列數；不同列 2,838／5,844／1,411／4,349（復權收盤變了，分數必變）。無 `--dump`（Hetzner 沒跑 09-15～18），完成行「未驗證」，以上述形狀檢查把關。
- 兩版重算產物皆留 scratchpad（`adj_recompute`／`adj_recompute2`），只有第二版進 main。

**第四次覆蓋範圍（分支 `claude/dazzling-maxwell-serk13`）**：`data/scores/2026-09-01..09-14.json`（`850a947` 原樣）＋`09-15..09-18.json`＋`data/state/cross.json`
（第二版重算，`last_date=2026-09-18`）＋`data/factors.json`（合併版 11,136 列、`sources=div+capred+split+par-1`）＋`data/backtest/` 六檔＋`manifest.json`（裁定 #50 Q22）
＋`runs/adj/` 三份報告。**不動**：`data/pool.json`／`fundamentals.json`（主線較新）、`runs/collect/`（分支多出的 1,152 份 2020～2024-09-26 原料包與 `2024-09-27` 首包差異
同 P3-PIT-POOL「320 窗之外、不動」；`2026-09-14` 包兩邊已相同）。manifest 的 `factors.rows=11035` 是 db 端事件列數、不綁 `factors.json` 雜湊，主線換合併版不衝突。
**驗收條件**：①14 檔分數頂層 `params_sha=a6a3f35cd1f0`、`cross.json` meta 同；②09-01..14 逐位＝`850a947`；③09-15..18 與 `cross.json` 逐位＝第二版重算產物；
④`factors.json` 頂層 `sources`＝現行值、列集合＝分支 ∪ 主線、`load_factors_file` 讀得過；⑤`data/backtest` 七檔 sha256＝分支；⑥`pytest tests -q` 全綠；⑦fresh-context 驗收綁 PR head；
⑧合併後 2026-09-21 22:30 每日班綠（主線舊 `factors.json` 無 `sources`，`load_factors_file` 會拒讀——**週一班前必須合併**）。
