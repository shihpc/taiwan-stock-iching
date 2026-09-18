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
- B2 產物 commit 到 `hetzner/dataset-<TO>`；log 末尾印兩檔大小與 manifest 摘要。

**C 驗收（fresh-context，綁 Hetzner 分支 commit）**
- C1 兩檔列數＝各段交易日數 × 當日可計分列數合計，與 `scores.db` 該區間 `SELECT COUNT(*)` 相符；`date` 集合＝該段日曆。
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
