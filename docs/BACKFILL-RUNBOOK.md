# P2 歷史回補 Runbook（在 Hetzner 執行）

依使用者 2026-09-09 裁定：**Claude 寫腳本、使用者在 Hetzner 執行，token 不離開 Hetzner**。
本檔只有步驟與預估；**不含任何 token 值**，也請不要把 token 貼進任何會 commit 的檔或對話。

程式：`scripts/backfill_hetzner.py`（CLI）＋ `src/iching/{config,fm,store,calendar,universe,plan,twse}.py`。
規格依據：`spec/P1-B3-replay.md` §B3.1 重播清單／§B3.2 SQLite／§B3.4 data_version、
`spec/P1-B1-market.md` §B1.9、`docs/P0A-report.md` §1（Hetzner 為 UTC、無 pyarrow、SQLite WAL 可用）。

## 0. 前提（Hetzner 現況，P0-A 實測）

- Python 3.14.4 系統層、無 venv；pandas 2.3.3／numpy 2.3.5／requests 2.32.5 已裝；**不需要 pyarrow**，本腳本只用標準庫＋requests（pandas 只在 report 可選）。
- 主機時區 **UTC**：腳本內所有日期都顯式轉 `Asia/Taipei`，不用改主機時區。
- 磁碟餘 27G；SQLite 估 1–3G（未量測，見 §7）。
- 記憶體 available 3.2 GiB：腳本每請求即落地、不整表載入；RSS 峰值 > 1.5 GiB 會自行中止。

## 1. 取得程式

```bash
cd /root/projects/taiwan-stock-iching   # 若尚未 clone：git clone git@github.com:shihpc/taiwan-stock-iching.git
git fetch --all
git checkout <主對話告知的分支或 commit>   # 驗收綁確切 commit；勿用「最新」
git pull --ff-only
```

## 2. 放 token（只做一次）

```bash
# repo 根目錄（.gitignore 已排除 .env／*.env）
printf 'FINMIND_TOKEN=%s\n' '<貼上你的 token>' > /root/projects/taiwan-stock-iching/.env
chmod 600 /root/projects/taiwan-stock-iching/.env
```

或改用環境變數 `export FINMIND_TOKEN=...`（環境變數優先於 `.env`）。腳本讀取後只放進
`Authorization: Bearer` header，**不進 URL、不進 log、不進例外訊息**（`src/iching/fm.py` 的 `redact()` 另做第二層遮蔽）。
`git status` 不應看到 `.env`；看到就停。

## 3. 先 `plan`（免 token、免網路）

```bash
python3 scripts/backfill_hetzner.py plan                       # core 群組（含 TWSE/TPEx 官方法人與成交金額）
python3 scripts/backfill_hetzner.py plan --group core optional  # 加 TaiwanStockPriceAdj 交叉驗證
```
群組只有 `core`／`optional`（`check` 由 `taiex-open-check` 專用）；打錯名字會 exit 2，不會靜默當 core。

本容器 2026-09-09 實跑（尚無交易日曆時以平日數 1,739 為上限）：

| 群組 | 請求 | 估時 | 備註 |
|---|---:|---:|---|
| core／FinMind | **7,144** | 約 1.4 小時（0.7 s 間隔） | 4 個全市場單日切片各 1,739（實際交易日約 1,620 會更少）＋指數／期貨／美股／匯率／總融資整年區間查詢。**列數**：`TaiwanStockPrice` 單日切片原始約 22,478 列（2020-01-02 Hetzner 實測），其中權證約 20,200 列（≈90%）在落地前被落地過濾（現行 lf2）濾掉，**落地約 2,270 列／日**（§4「落地過濾」）；請求數不受影響 |
| core／TWSE+TPEx 官方（**必抓**：B1.5 法人 BFI82U＋TPEx summary 逐日 3,478 次、B1.3／B1.4 成交金額 FMTQIK＋tradingIndex 按月 160 次） | **3,638** | 約 4.0 小時（4 秒節流） | 不占 FinMind 額度；2026-09-09 驗收更正：P1-B1 明說官方法人是唯一合法口徑，不再是選配 |
| optional（`price_adj` 交叉驗證） | +2,138（不重複代號 2,149 再排除 11 檔 4 碼 DR，`docs/P2-KICKOFF.md` §5 #25；universe.db 未落地前 `plan` 以裁定上限 3,060 估） | +0.4 小時 | 需 Sponsor；失敗不擋 |
| `taiex-open-check` 第二候選 KBar（§5） | 約 246／年 | 2022-01~2026-08 約 1,140 次 ≈ 13 分 | 權限層級未實測 |

對照 `spec/P1-B1-market.md` §B1.9 的 28,050 次：本計畫 FinMind 部分是它的 25%，差在指數／期貨／美股／匯率／總融資
改成整年一請求（帶 `data_id` 可一次取多年，`taiwan-backtest/scripts/fetch_taiex.py` 取過 18 年）而非逐日；
若全市場切片被判定需 per_stock 退回（每股 1 請求＝3,060），單一資料集就會多 3,060 次。
SponsorYear 6,000 次／小時（P0-A §2），額度不是瓶頸。

## 4. `run`（實抓，可中斷、可續跑）

**選項位置（踩過）**：`--data-version`／`--cache-dir`／`--env-file`／`--interval`／`--quiet` 是**全域選項，
必須放在子命令 `run`／`plan`／`report` 之前**；`--dataset`／`--from`／`--to`／`--limit`／`--group` 放在子命令之後。
放錯位置會得到 `error: unrecognized arguments: --data-version`。

**不需要設任何環境變數**：第一次跑會建立 `fm-<台北今日>-01`，之後所有指令（`run`／`report`／`plan`／
`taiex-open-check`／`calendar`）**自動沿用 cache 內那一個** `data_version`（程式讀 `coverage.data_version`）；
跨日、換 tmux 視窗、重開機都不受影響。每個指令開頭會印一行「沿用 cache 內既有 data_version=…」。
（沿革：2026-09-10 前需自行 `export DV=…` 並在每個指令帶 `--data-version "$DV"`，忘了帶的代價是整批重抓或被指紋守門擋下清 DB；
2026-09-11 改為自動沿用。）

```bash
cd /root/projects/taiwan-stock-iching
tmux new -s backfill              # 全程數小時，用 tmux 才不會因 SSH 斷線中止
```

建議順序（都可直接一次跑 `run`，腳本會自動先跑便宜的前置 `stock_info`／`index_price`）：

```bash
# 4.1 先落地股票池與指數（產生台北交易日曆；<1 分鐘）
python3 scripts/backfill_hetzner.py run --dataset stock_info index_price us_index fx_usd total_margin futures_inst futures_daily

# 4.2 看一眼日曆與池
python3 scripts/backfill_hetzner.py report | head -40
python3 scripts/backfill_hetzner.py plan          # 現在會用真實交易日曆算請求數

# 4.2b ⚠ 第一次以新的落地過濾版本（現行 lf2，`src/iching/config.py` LANDING_FILTER_VERSION）跑之前，**必須先清掉舊 DB**：
#     Hetzner 上已有一份 2020-01-02 未濾（22,478 列）的落地，lf1 時代的落地也一樣——規則變更＝raw 內容的**定義**變了，
#     不是 schema 遷移能解決的；舊列不會因換 data_version 而消失，混存後 report 的 rows／PIT 統計全部失真。
#     腳本會守門（該資料集 coverage 已有 ok 鍵、而 sources.landing_filter ≠ 現行版本或為 NULL → 中止並印出這條指令），
#     但別等它擋：先清再跑，然後從 4.1 重來（stock_info／index_price 很便宜）。
rm -f cache/*.db cache/*.db-wal cache/*.db-shm

# 4.2c 放量前先把次要索引拿掉（2026-09-11 起 `run` 本來就**不建**次要索引；這步只對「舊版程式建過的 DB」或「跑過 reindex 的 DB」有意義）：
#     回補只以 cov_key 走主鍵、用不到 idx_<t>_date／idx_<t>_stock_id_date，留著每筆 INSERT 都多維護兩棵 B-tree 且隨表變大惡化——
#     容器合成資料 2,270 列×400 日實測：有索引 137→192 ms/日且一路上升、無索引 63→65 ms/日平坦（2–3 倍，且只是退化來源之一）。
#     `run` 開頭偵測到既有索引會印一行建議，但**不會自動刪**（那是你的資料結構）；冪等，多跑無害。
python3 scripts/backfill_hetzner.py reindex --drop

# 4.3a 放量前先試打一日（§7 #13：Sponsor 全市場切片對 2020 年歷史日期是否回全市場，家族前例最遠只到約 100 日曆天）
python3 scripts/backfill_hetzner.py run --dataset price_daily --limit 1 --from 2020-01-02 --to 2020-01-02
python3 scripts/backfill_hetzner.py report | grep -E 'price_daily|落地過濾'   # rows 應約 2,270 列（濾後；原始約 22,478）；「落地過濾 lf2」那行已濾約 20,200；只有幾列或 0 → 停，回報
#     （--limit 1 只抓第一鍵；下一步同一 data_version 會跳過它、接著抓）

# 4.3 全市場切片（最久的一段；可分年跑，例：--from 2020-01-01 --to 2020-12-31）
python3 scripts/backfill_hetzner.py run --dataset price_daily inst_buysell margin short_sale_balance

# 4.4 其餘 core（除權息、月營收、財報、VIX，＋官方法人 BFI82U／TPEx summary 逐日、成交金額 FMTQIK／tradingIndex 按月；
#     官方端點 4 秒節流約 4 小時，可另開 tmux 視窗單獨跑：
#     python3 scripts/backfill_hetzner.py run --dataset twse_bfi82u tpex_inst_summary twse_fmtqik tpex_trading_index）
python3 scripts/backfill_hetzner.py run
#     TPEx **確定需要** --tpex-no-verify（2026-09-11 Hetzner 實測：不加則 tpex_inst_summary 每一鍵都 SSLError，
#     加了 3/3 成功；同批 twse_bfi82u 222/222 ok，證明是 tpex.org.tw 單一 host 的憑證問題，非本機 CA）。
#     該旗標只對 tpex.org.tw 關閉驗證（src/iching/twse.py 的 OfficialClient.get），TWSE 仍照驗。
#     ⚠ 關閉 TLS 驗證＝內容可被中間人替換：只在 TPEx 憑證鏈失敗時用，且該次落地的上櫃法人合計要與
#       FinMind 逐檔法人（raw_inst_buysell 加總）對照過才可採信。

# 4.5 選配
python3 scripts/backfill_hetzner.py run --group optional        # TaiwanStockPriceAdj 交叉驗證

# 4.6 全部 core（含 4.5 若有跑）**跑完之後**再把次要索引建回來（一次建比逐筆維護便宜得多；計分讀取按 date／stock_id 查沒有它會全表掃）：
#     逐表印建立了什麼與耗時；冪等。每次 `run` 摘要末尾只要索引還缺就會提醒這一步——回補期間可以先不理。
python3 scripts/backfill_hetzner.py reindex
```

**落地過濾 lf2（2026-09-10 裁定；同日驗收更正 lf1→lf2；`src/iching/config.py` `is_warrant_code`）**：

- **做什麼**：`price_daily`／`inst_buysell`／`margin`／`short_sale_balance` 四個全市場單日切片，在寫進 SQLite **之前**
  丟掉權證列。規則＝四條件**同時**成立才排除：6 碼 ∧ 首字 ASCII 數字 ∧ 非 `00` 開頭 ∧ 不在 `info_ids`；
  **`info_ids`＝`raw_stock_info` 的代號集合減去 `industry_category='所有證券'` 的代號**（lf2 與 lf1 的唯一差別）。
  其餘全部保留：普通股（**含已下市、不在 info 的 48 檔**）、ETF（含 `00631L`／`006201`／`00987A` 6 碼型與已下市 2 檔）、
  特別股、DR／ETN／REIT（在 info）、產業指數（`Cement`／`Tourism`…）、`TAIEX`／`Other`。
- **為何**：使用者 2026-09-10 於 Hetzner 實測 2020-01-02 切片 22,478 列，其中 20,208 列是權證（≈90%）；權證不進任何指標，
  落地只是白占磁碟與 I/O。ETF 使用者明確要保留。**lf1→lf2 的原因**：同日驗收實查 `TaiwanStockInfo`（4,321 列），
  6 碼數字開頭非 `00` 且在 info 的 118 檔裡，`所有證券` 那 36 檔**全是上櫃權證**（名稱含「購」／「售」，如 `711135 元太群益9B購01`，
  前兩碼 70／71／73），且全 info 的 `所有證券` 就只有這 36 檔——「權證不在 info」的前提不成立，lf1 會留下它們；
  排除它們是執行「不要權證」裁定，不是改裁定。
- **殘餘風險（已接受）**：①2020 後**已下市**的 DR／ETN／REIT 不在今日的 `TaiwanStockInfo`，會被當成權證濾掉——它們不在
  個股池、不進任何指標。②日後 `所有證券` 若用於非權證會被誤殺（今日 36/36 皆權證）。
  **絕不可**把規則改成「只留在 info 的代號」：會丟掉 48 檔已下市普通股（存活者偏誤，落地後不可逆）。
- **前置**：過濾需要 `universe.db` 的 `raw_stock_info`；未落地時該資料集**中止並報錯**（訊息叫你先跑 `run --dataset stock_info`），
  不會靜默不濾。直接 `run` 或 `run --dataset price_daily` 都會自動先跑 `stock_info`，只有 `stock_info` 本身失敗時才會碰到。
  代號集合每次 run 只讀一次；**少於 3,000 個代號也中止**（`config.LANDING_INFO_MIN_IDS`；今日 3,112——info 殘缺時 6 碼
  REIT／ETN／DR 會被靜默多殺，而「濾後為 0」的警告不會因此觸發，所以在讀到名單時就擋）。
- **舊落地不得混存（守門）**：該資料集 coverage 已有 ok 鍵、而 `sources.landing_filter` ≠ 現行版本（含 NULL＝未濾）→
  該資料集**中止**，訊息給出實際 cache 路徑的 `rm -f <cache>/*.db <cache>/*.db-wal <cache>/*.db-shm`。見 4.2b。
- **回補期間不得 `--force` 重抓 `stock_info`；做了就清 DB 重來**：過濾用的 info 名單指紋記在 `sources.info_ids_sha`
  （`report` 該行括號內的 `info xxxxxxxxxxxx`），同一資料集既有 ok 鍵的指紋與本次不同即中止——前後鍵的過濾基準不同、
  無法事後分辨哪幾天是用哪份名單濾的。`stock_info` 只在 4.1 落地一次。
- **`raw_stock_info` 沒有 `industry_category` 欄 → 中止**（不會退化成 lf1、也不會標假 lf2）：訊息叫你 `--force` 重抓 `stock_info`
  並確認欄位；那是 FinMind 回應形狀改變或落地不完整的訊號。
- **上游截斷偵測**：`price_daily` 濾後列數 < 1,500（`config.PRICE_DAILY_MIN_ROWS`；2020-01-02 濾後 2,270 的約 66%）→
  記 `failures(kind=too_few_rows)`、**不寫 coverage**、下次重抓——否則 HTTP 200 只回 3 列會被記成 ok、重跑永不再試。
  其餘三個切片列數常態未知，低於 1,500 只 log WARNING 不擋（§7 #20 對照）。回應任一列缺 `stock_id` 鍵 → 該資料集中止（形狀改變）。
- **怎麼確認生效**：`report` 頂部多一行 **「落地過濾 lf2：已濾 N 列（權證；同 data_version 內累計，--force 重抓同鍵會重複計）——price_daily N₁／…」**，
  N 來自各 DB `sources.n_filtered`、`sources.landing_filter` 記 `lf2`。**N 是累計值**：`--force` 重抓同一鍵會再加一次，
  拿它對照 §7 #20 時用「未 `--force` 的乾淨 run」。若某資料集有 `sources` 列但 `landing_filter` 不是 `lf2`，該行附 ⚠。
  run 摘要每列也印 `落地過濾 lf2 已濾=N`。**`data_version` 語意不動**：它仍是 FinMind 校正批次，不是我方過濾版本。
- **與個股池是兩件事**：落地過濾只砍權證；**4 碼 DR（`9101`–`9188`，11 檔）照常落地**，排除發生在讀取端的名單建構
  （`universe.pool_from_info`，`docs/P2-KICKOFF.md` §5 #25）。

行為要點：
- **回補期間不得 `--force` 重抓 `stock_info`**（見上「落地過濾」段；做了就清 `cache/*.db*` 從 4.1 重來）。
- **換 `data_version`、更新本腳本的表結構、或落地過濾版本變更（`LANDING_FILTER_VERSION`）前先刪舊 `cache/*.db`**：schema 不做遷移（唯一例外＝`sources` 的
  `landing_filter`／`n_filtered` 兩欄會自動補，§6）；過濾版本變更＝raw 內容定義變更，腳本會守門中止（4.2b），`CREATE TABLE IF NOT EXISTS` 不會改既有表的 PK／欄位；舊版本的列留在 raw 表會混進 report 的 rows 數。`rm cache/*.db cache/*.db-wal cache/*.db-shm`。
- **一次 run 一個 `data_version`，程式自動決定**（`resolve_data_version` 優先序）：①不帶 `--data-version` 且 cache 內恰有一個
  版本 → **自動沿用**（續跑常態）；②不帶且 cache 空（無 coverage 列）→ `fm-<台北今日>-01`（新批次）；③帶 `--data-version X`
  而 cache 內已有別的版本 → **中止**並印清 cache 指令——只有明知要開新批次才加 `--new-version`（舊列仍留在 raw 表，report 會標混版本，
  正常做法是先清 cache）；④不帶但 cache 內有多個版本 → 中止（不應發生，清 cache 重來）；⑤`--data-version ""`（空字串）→ 報錯。
  **想確認目前用哪個版本就跑 `report`，第一行會印。** `report`／`plan` 是診斷工具、**任何情況都不中止**（多版本時取最新並印 ⚠ 列出全部；
  顯式帶衝突版本照你指定的算並警示）；③④只擋會寫資料的 `run`／`taiex-open-check`／`calendar`。 `--data-version`／`--new-version` 是全域選項、**位置在子命令之前**。
  （沿革：2026-09-10 前需自行 export `$DV`，忘帶會被當成新版本整批重抓、再被指紋守門擋下；已改為自動沿用，問題從根本消失。
  §B3.4「歷史一律重抓」仍以版本為單位。）
- 重跑同一指令會跳過已 `ok`／`empty` 的鍵；**失敗只進 `failures` 表、絕不寫進 coverage**，下次自動重抓。
  **全市場單日切片在（同一 `data_version` 的）交易日曆上卻回空**也算失敗（`failures.kind=empty_on_trading_day`）、
  不寫 coverage；只有非日曆型查詢（帶 `data_id` 的區間／逐股）的空回應才記 `empty`。
  官方端點同樣：連線例外／HTTP 非 200／非 JSON → 失敗；交易日曆日期回「無資料」（TWSE `stat` 非 OK 或 `data` 空／
  TPEx `tables` 空）→ `empty_on_trading_day`；按月的 FMTQIK／tradingIndex `stat` 非 OK 或 `data` 空 → `bad_stat` 失敗。
  **空回應只在資料集宣告的策略下才是合法 empty**（`config.DatasetSpec.empty_ok_for`，目前只有 `per_stock`）：
  指數／美股／匯率／總融資／期貨／全市場整年區間／`TaiwanStockInfo` 回 200 空陣列一律 `failures(empty_unexpected)`、
  不寫 coverage（否則同 dv 永不重抓）。完整的「策略 × 回應 → coverage／failures」期望表在 `tests/test_paths_matrix.py` 頂端。
- 混用策略（例如 `price_daily` 從 daily_slice 退回 per_stock）時，同一列會在兩個 coverage 鍵下各存一份
  （PK＝`(cov_key, row_hash)`），`report` 的 n_rows 必須等於底下實列數（§7 (c)）。
- 402／429 → 等 65 秒重試最多 8 次，仍失敗即中止（exit 3），稍後重跑同一指令續抓。
- 400「Your level…」＝需 Sponsor：記 `permission` 失敗；有 `fallback` 的資料集會自動改 `per_stock`（每股 1 請求）。
  不想自動退回加 `--no-fallback`；要指定策略用 `--strategy dividend_result=per_stock`。
- `--from/--to` 對**單日切片**（price_daily 等）就是日期範圍；對**區間型**資料集（指數／期貨／美股／匯率／月營收／財報）只是「選中哪些固定切塊（年／季／月）」、不改塊界——例如 `--from 2022-01-03 --to 2022-01-05` 會抓整個 2022 年的指數；`per_stock` 一律整段。這樣 coverage 鍵才穩定、不會與預設計畫的鍵重疊。
- 全市場切片需要**同一 `data_version`** 落地的台北交易日曆涵蓋請求區間（由 `index_price` 的 TAIEX 日期生成），不涵蓋會中止該資料集並提示。
- 交易日曆 JSON 只有涵蓋 `2020-01-01~2026-08-31` 全段（**每個月的日期數 ≥ 該月平日數 × 0.5**，整月缺或月內缺一半以上都不算；
  兩份日曆都只取本次 `data_version` 落地的列）才寫進 git 追蹤的 `data/calendar_*.json`；
  部分日曆一律寫 `cache/calendar_partial_*.json` 並 log 說明（`git status` 永遠不該因為半途的 run 出現 `data/calendar_*.json`）。
- Ctrl-C 安全：每個請求自成一個交易，中斷不留半套。
- 建議在 tmux 內跑並把輸出留檔：`... run 2>&1 | tee -a cache/logs/run-$(date -u +%Y%m%d).out`
  （`cache/logs/backfill-<data_version>.log` 也會自動寫）。
- **次要索引延後建立（2026-09-11）**：`run` 落地一律不建 `idx_<t>_date`／`idx_<t>_<index_cols>`（`store.ensure_raw_table(create_indexes=False)`），
  由 `reindex` 子命令事後一次建（`--drop` 刪）；`run` 開頭偵測到既有索引只建議 `reindex --drop`、結尾索引缺失只提醒 `reindex`，
  兩者都**不自動動手**。見 4.2c／4.6 與 §7 #23。

## 5. 裁定 4：大盤開盤價以證據定

```bash
python3 scripts/backfill_hetzner.py taiex-open-check                       # 預設 202201~202608
python3 scripts/backfill_hetzner.py taiex-open-check --month-from 202001   # 能多就多
```

抓證交所 `MI_5MINS_HIST`（按月，4 秒節流；56 個月約 4 分鐘）當**官方開盤**，與**兩個候選**逐日比對
（裁定 9：與官方一致者為準，皆不一致再回問；`docs/pre-registration.md` §1.2.3）：
①FinMind `TaiwanStockPrice/TAIEX` 的 `open`（宣告源）；②FinMind `TaiwanStockKBar/TAIEX` **09:00 分 K 的 `close`**
（`taiwan-backtest/scripts/fetch_taiex.py:56-63` 前例；逐日一請求，2022 全年約 246 次、2022-01~2026-08 約 1,140 次 ≈ 13 分；
**權限層級未實測**，P0-A 待驗證 4b 疑為 SponsorPro——若回 permission，報告只比對候選一並標明）。
輸出三欄（官方 open／FinMind open／KBar 09:00 close）、各候選一致率，並明講「一致的是哪一支」或「**皆不一致**」；
寫 `data/taiex_open_check.json`。試跑：`--kbar-limit 20`；不抓 KBar：`--no-kbar`。**本雲端容器被證交所擋是預期的**（P0-A §3），
只有在 Hetzner 跑才有結果。TWSE 該端點的欄位名與民國年日期格式沒實測過：程式找含「日期」「開盤」的欄、
接受 `111/01/03`／`2022/01/03`／`2022-01-03`，找不到會**明確報錯**（不會靜默回 0%）。

## 6. `report` 與產物

```bash
python3 scripts/backfill_hetzner.py report
```

| 產物 | 位置 | 進 git？ |
|---|---|---|
| `prices.db`／`chips.db`／`fundamentals.db`／`universe.db`／`market.db` | `/root/projects/taiwan-stock-iching/cache/` | **否**（`.gitignore`：`cache/`、`*.db*`） |
| log | `cache/logs/` | 否 |
| `data/calendar_tpe.json`／`data/calendar_us.json`（兩份交易日曆，P1-B3 §B3.1 #10） | `data/` | **是** |
| `data/taiex_open_check.json`（裁定 4 報告） | `data/` | **是** |
| `report` 的文字輸出 | 貼回對話／存 `runs/backfill/<data_version>-report.txt` | 是（若存檔） |

push 回 repo 的步驟（在 Hetzner）：

```bash
git status --short          # 只應看到 data/calendar_*.json、data/taiex_open_check.json（＋你存的 report）
git add data/calendar_tpe.json data/calendar_us.json data/taiex_open_check.json
git commit -m "data(p2): 歷史回補產物——兩份交易日曆＋TAIEX 開盤一致率（<data_version>）"
git fetch && git status     # CANON 第 7 條：遠端領先先看內容再 rebase
git push
```

`market.db` 內另有 `raw_twse_mi5mins_hist`（taiex-open-check 的原始月表）與 `sources` 表（每 dataset 的
抓取時間／請求數／筆數／日期範圍，§B3.4 第 4 點；2026-09-10 起另有 `landing_filter`／`n_filtered` 兩欄，見 §4「落地過濾」。
2026-09-10 前建的 DB 開啟時會自動補這兩欄，這是**唯一**的自動補欄，其餘 schema 仍不遷移）。

## 7. 首次 run 要確認的清單（未實測／不確定）

以下在本雲端容器**沒辦法**或**沒有**實測，第一次在 Hetzner 跑完請對照 `report` 與 log 逐項確認：

| # | 項目 | 為什麼沒驗 | 怎麼確認 |
|---|---|---|---|
| 1 | `TaiwanOptionVix`：是否需要 `data_id`、欄位名（`src/iching/config.py` key `vix`） | 免 token 回 400 level；家族 repo 無呼叫 | run 後 `report` 該列 ok>0；若 failures 為非權限類 400，改試 `--strategy vix=per_id`（data_id 待查 FinMind 文件） |
| 2 | `TaiwanStockDividendResult` 以**整年區間**全市場查詢 | 家族只用過單日全市場（`taiwan-flow-live-v2/src/build_morning.py`） | 失敗會自動退回 per_stock（+3,060 次）；看 run 摘要有無「由 range_slice 退回」 |
| 3 | `TaiwanStockFinancialStatements` 全市場逐季區間查詢 | 只實測過帶 `data_id` 的單季 | 同上（退回 per_stock） |
| 4 | `TaiwanStockMonthRevenue` 歷史列是否含 `create_time`（B2.1 available_at 規則依賴） | 免 token 回 400 level | `sqlite3` 或 Python 看 `fundamentals.db` `raw_month_revenue` 欄位（`sources.columns`） |
| 5 | `TaiwanStockMarginPurchaseShortSale`／`TaiwanDailyShortSaleBalances` 完整欄位名 | 家族只用 `MarginPurchaseTodayBalance`／`SBLShortSalesCurrentDayBalance` | 動態建欄會全部落地；看 `sources.columns` |
| 6 | `TaiwanStockInfo` 個股池：**裁定寫 3,060 檔，本容器 2026-09-09 免 token 實打得符合條件的列數恰 3,060、但不重複代號 2,149**（835 檔多列，多為產業重分類／市場轉換殘留）——裁定數字疑為列數，**請確認以哪一個為準** | 免 token 拿到的是否為完整名單未驗 | `report` 的「個股池」列（含多列代號數）與 Sponsor token 結果對照 |
| 7 | 全市場單日切片在**交易日曆上卻回空**的日期 | 未實測 | log 會 WARNING；`report` 的 empty 欄；對照 TWSE 休市公告 |
| 8 | TWSE `MI_5MINS_HIST` 欄位名／日期格式／回應形狀（`stat`/`fields`/`data`） | 本容器被 WAF 擋 | `taiex-open-check` 若報 `TwseError` 把訊息貼回對話 |
| 9 | BFI82U／TPEx summary 在 2020 年初的可用性與回應形狀 | 同上（只落地原始 JSON，未解析） | `report` 的 `twse_bfi82u`／`tpex_inst_summary` 列；`raw_*` 的 `stat` 欄 |
| 10 | `WITHOUT ROWID`＋動態欄的實際磁碟量 | §B3.2 明寫「未量測前不視為已驗證」 | `du -sh cache/` 貼回 |
| 11 | ^SOX 與 ^GSPC 的美股交易日是否一致（us 曆取 ^GSPC） | 只抓過 3 天 | `report` 的「美股交易日曆」列差集數 |
| 12 | Python 3.14 下 `sqlite3` 與本腳本相容（本容器 3.11） | 無 3.14 環境 | 第一個 run 成功即證 |
| 13 | **(a) Sponsor 全市場單日切片對 2020–2025 歷史日期是否回全市場**——家族前例最遠只到約 100 日曆天 | 無 token | **先** `run --dataset price_daily --limit 1 --from 2020-01-02 --to 2020-01-02` 看列數（**濾後**應約 2,270 列；2026-09-10 Hetzner 實測原始 22,478 列，此項 (a) 已由該次實測回答為「會」，留列供其他年份對照），再放量 |
| 14 | **(b) `USStockPrice.date` 是美國當地交易日而非台北日**——整個上爻對齊（`calendar.us_session_closed_by`）建立在此 | 只抓過 3 天、未與美國交易所行事曆對照 | 抽 2022-07-04（美國國慶）／2022-11-25（感恩節翌日半日）等日期看 ^GSPC 有無列；週一台北日不得出現同日美股列 |
| 15 | **(c) 混用策略後 `report` 的 n_rows 要與 raw 實列數對**（PK 已改 `(cov_key,row_hash)`） | 修法只有離線測試 | `report` 各資料集 rows 欄 vs `SELECT cov_key, COUNT(*) FROM raw_<key> GROUP BY cov_key` 逐鍵相等 |
| 16 | `TaiwanStockKBar` TAIEX 的權限層級與欄位（minute/open/high/low/close/volume）、`end_date` 是否被尊重 | 免 token 未打；P0-A 4b 未驗 | `taiex-open-check --kbar-limit 5` 看 `market.db` `raw_taiex_kbar_0900` 有無列；permission 即需回問 |
| 17 | `FMTQIK`／TPEx `tradingIndex` 2020 年初回應形狀（`stat`／`tables`）與 TPEx TLS | taiwan-flows 只用近月 | `report` 兩列 ok≈80；`raw_*` 的 `stat`／`body` 前 200 字 |
| 18 | 日曆完整度門檻「每月日期數 ≥ 平日數 × 0.5」（`calendar.MONTH_DENSITY`）在真實假期下不誤判——春節月（2 月）台股約休 6~9 天、平日約 20 天 | 只以推算，未用真實 2020–2026 日曆驗過 | `report` 的「台北日曆缺口」列應為 0 個月；若春節月被列為缺口，把該月日期數貼回、再議門檻 |
| 19 | `report` 頂部「DB 內 data_version 數」應為 1 | — | >1 代表舊版本列混在 raw 表：清 `cache/*.db` 重跑 |
| 21 | **info 名單規模**：`raw_stock_info` 不重複代號（扣 `所有證券`）今日實測 **3,112**（2026-09-10 免 token 快照 4,321 列／3,148 代號／`所有證券` 36）；下限 3,000、餘裕 112 | 只有一天的快照 | `report` 若印出「低於下限 3,000」中止，把當下代號數貼回：非權證代號淨減 >112 是誤觸（調門檻），遠低於 3,000 才是殘缺（重抓 stock_info） |
| 22 | **`price_daily` 濾後列數下限 1,500** 不誤擋早年／半日交易日 | 只依 2020-01-02 一日（濾後 2,270） | `report` 的 failures 若出現 `too_few_rows`：看該日原始列數與 TWSE 公告——真半日／小市場就把該日列數貼回再議門檻，不要直接調低 |
| 20 | **落地過濾 lf2 生效**：濾後列數約 **2,270／日**（權證約 20,200 列＝**約 90%** 被濾） | 只有 2020-01-02 一日的實測組成；規則以離線測試守（`tests/test_landing_filter.py`） | `report` 的「落地過濾 lf2：已濾 N 列（權證…）」行（累計值，用未 `--force` 的乾淨 run）：N ÷ 交易日數 ≈ 20,200、`price_daily` rows ÷ 交易日數 ≈ 2,270；差很多（例如濾掉 0、或濾後仍 >5,000）→ 停，把該行與 `SELECT stock_id FROM raw_price_daily WHERE date='2020-01-02' LIMIT 50` 貼回 |
| 23 | **進度列的 fetch／land／sleep／other 拆分怎麼讀**（2026-09-11 加，為診斷「每請求由 1.6s 退化到 3.4s」）：每條進度列 `[price_daily] 50/244 … 0.43 req/s  本段 fetch 1.10s land 0.52s sleep 0.70s other 0.00s  ETA …` 的四個數字是**上一條進度列之後這一段**（預設 50 鍵）的每鍵平均，**不是累計**——累計平均會把退化攤平、看不出趨勢。`fetch`＝發請求到拿到已解析 rows（網路＋JSON 解析，**已扣掉** client 內的節流／額度／退避等待）；`land`＝落地過濾＋`record_success`（失敗鍵則是 `record_failure`）；`sleep`＝client 等待（0.7s 節流常態就是 ≈0.70）；`other`＝其餘（記憶體檢查、迴圈開銷，常態 ≈0）。run 摘要每個資料集底下另印 `計時 N 鍵：fetch Σ／均 land Σ／均 sleep Σ／均` 的累計 | 本容器只有假 client 與合成資料，沒有真 FinMind 延遲可對照 | 逐段看哪一欄在漲：`land` 單調上升＝SQLite 寫入端（先確認 4.2c 已做、`du -sh cache/`、`PRAGMA wal_checkpoint` 情況）；`fetch` 單調上升＝FinMind 端（同一請求形狀、回應時間隨歷史日期／時段變化，與我方無關，把幾段數字貼回）；兩者都平坦但 `req/s` 仍掉＝`other`／`sleep` 異常（機器負載、swap） |

### 7a. 2026-09-11 Hetzner 首次放量的實測基準（供日後對照）

| 項目 | 實測值 |
|---|---|
| 2020 全年（245 交易日 × 4 切片＋前置） | **979 請求／29 分鐘**，`rc=0`、零失敗 |
| `price_daily` | 244 請求、**平均 3.58 s／請求**，段均由 2.32 → 5.52 s **年內單調惡化** |
| `inst_buysell` | 245 請求、**1.89 s／請求，完全平坦** |
| `margin`／`short_sale_balance` | 各 245 請求、**0.83 s／請求**（≈0.7 s 節流地板；兩者 `已濾=0`——權證無融資券與借券餘額，回應裡本就沒有要濾的） |
| 濾後列數 | `price_daily` 2,270／日（2020-01-02）；`已濾` 全年 5,223,680 ÷ 244 ≈ **21,408／日** |
| 2021 首段（拿掉索引後） | `fetch 2.82s  land 0.12s  sleep 0.00s  other 0.00s` |
| 2021 全年（244 交易日 × 4 切片） | **976 請求／約 22 分鐘**，`rc=0`、零失敗、額度等待 0 |
| 2021 `price_daily` | 244 請求、**fetch 均 2.36 s**（首段 2.82 → 全年均 2.36，**未延續 2020 的年內單調惡化**）、`land` 均 0.12 s；`已濾` 6,823,237 ÷ 244 ≈ **27,963／日** |
| 2021 其餘三個 | `inst_buysell` fetch 均 1.14 s（`land` 0.28）、`margin` 0.41 s、`short_sale_balance` 0.42 s；後兩者 `sleep` 0.18–0.19 s＝fetch 已低於 0.7 s 節流地板，等待重新出現 |

**退化的歸因（2026-09-11 判定）**：瓶頸是 **`fetch`（FinMind 回應時間）**，`land` 僅 0.12 s ≈ 4%。
三項證據：①同一批的 `inst_buysell` 每日落地與濾除列數都**更多**卻完全平坦，故非「索引維護隨表變大」
（該假說一度被提出，已被此數據否定）；②換行程、換年份後 `fetch` 由 2020 初的約 1.1 s 接續到 2021 初的
2.82 s，**不隨重啟重置**，故非記憶體／連線／DB 大小；③`sleep 0.00` 表示 0.7 s 節流已被 fetch 完全吸收、
額度等待 0 次（約 1,200 請求/小時，遠低於 6,000 上限）。
**未解釋的部分**：權證逐年增加使每日回應由 20,680 列長到 25,981 列（+26%），但 `fetch` 是 2.5 倍
——payload 成長撐不起時間成長。剩下的推測是上游對持續使用的伺服器端節流，**屬推測、無證據**，
且即使證實我方亦無從改善。**結論：不是本專案的問題，不要再往 SQLite／索引方向找。**

**2021 全年後的修正（2026-09-11）**：上段「年內單調惡化」只在 2020 觀察到，**2021 沒有重演**
——2021 首段 2.82 s、全年均 2.36 s，方向相反。故「退化會一路累積」是**過度推論**，正確的說法是
「`fetch` 在 1.1~5.5 s 之間依日期／時段波動，我方無法預測也無從改善」。`land` 在兩年都穩定在
0.12 s，索引假說仍然被否定，結論不變。

**時間規劃基準（2026-09-11 依 2021 實測下修）**：每年約 **22–30 分鐘**（2020 實測 29、2021 實測 22），
2022–2026/8 約 4.7 個年份 ≈ **2 小時**（原估 3 小時是以 2020 的壞情況外推）；
官方端點 3,638 請求 × 4 s 節流 ≈ **4 小時**且**不佔 FinMind 額度**，故應**另開 tmux 視窗平行跑**
——它才是關鍵路徑，序列跑會變成 7 小時。

### 7c. 2026-09-12 回補完成後的逐項核對（`report` 實測）

`data_version=fm-20260911-01`，DB 內版本數 **1**。台北日曆 **1,618 日**（2020-01-02 ~ 2026-08-31），
以下「應等於 1,618」的資料集**全部等於 1,618 且 fail=0**：`price_daily`／`inst_buysell`／`margin`／
`short_sale_balance`／`twse_bfi82u`／`tpex_inst_summary`。

| §7 項 | 判準 | 實測 | 結果 |
|---|---|---|---|
| #18 | 台北日曆缺口 0 個月（春節月不誤判） | 0 個月 | **PASS** |
| #19 | DB 內 `data_version` 數＝1 | 1（`fm-20260911-01`） | **PASS** |
| #20 後半 | `price_daily` rows ÷ 交易日數 ≈ 2,270，「濾掉 0 或濾後 >5,000」才停 | 3,991,311 ÷ 1,618 ＝ **2,466** | **PASS**（高於 2,270 是上市檔數逐年增加，PIT 池 2020 日均 1,805 → 2026 1,975） |
| #20 前半 | 已濾 ÷ 交易日數 ≈ 20,200 | 56,094,358 ÷ 1,618 ＝ **34,668** | **判準過時，非資料問題**（見下） |
| #22 | `price_daily` 濾後列數下限 1,500 不誤擋 | `failures(too_few_rows)` **0 筆** | **PASS** |
| #9 | BFI82U／TPEx summary 在 2020 年初可用 | 各 1,618 鍵全 ok | **PASS** |
| #1 | `TaiwanOptionVix` 是否需 `data_id`、欄位名 | 不需 `data_id`，但**上游只有 2026-03-02 起**（143,766 列）；2020–2025 六鍵回 200 空陣列 | **已答，見裁定 #26** |

**#20 前半的判準要改**：原值 20,200 只取自 **2020-01-02 單日**，而權證逐年變多——分年實測
每日濾除數 **2020 21,408 → 2021 27,963 → 2022~2026/8 38,997**。分年帳加總 5,223,680＋6,823,237＋
44,027,233 ＝ 56,074,150，與報告的 56,094,358 差 **20,208**，正好是 2020-01-02 那一天（早期試跑
重複計；該行本來就註明「`--force` 重抓同鍵會重複計」）。**新判準**：`已濾 ÷ 交易日數` 落在
**20,000 ~ 40,000** 且**逐年遞增**即正常；掉到 0 或暴增到 10 萬才停。

**`report` 的失敗清單會永久留下 37 筆「策略被取代」的殘影**（`financial_statements` 30 ＋
`dividend_result` 7）：這兩個資料集的 `range_slice` 全市場查詢回 200 空陣列而失敗，改走 `per_stock`
後成功，但 `record_success` 清 failures 是**按 cov_key 清**的——per_stock 的鍵是股票代號、range_slice
的鍵是日期區間，兩組不重疊，所以舊失敗列沒有東西去清它。**看到這 37 筆不代表回補沒做完**
（實際 `financial_statements` 2,051 ok／88 empty、`dividend_result` 1,849 ok／290 empty）。
另 6 筆 `vix` 2020–2025 是**上游真的沒有資料**的事實紀錄，**應該留著**。

**回補完成後仍未解決的四件事**（都不擋 P2 下一步，但凍結前要處理）：
1. `vix` 只有 2026-03-02 起 → 裁定 #26（回測期一律不用）。
2. `taiex-open-check` 的比對區間自 **2022-01-01** 起，**2020–2021 未比對**（`--month-from 202001` 可補，約 8 分鐘）。
3. `report` 印「同日多產業代號 **603 檔**…請人工複核」——決定性 tie-break 已取值，但**沒有人看過那個取法對不對**。
4. 個股池實際 **2,139 檔**，`src/iching/universe.py:5` 的推算是 **2,138**（2,149 − 11 DR），差 1 檔未解釋；
   同處「裁定的 3,060 疑為列數而非檔數」也仍**待使用者確認**。

### 7b. 首次放量已回答的 §7 項目

- **#13 (a)**：Sponsor 全市場單日切片對 2020 歷史日期**回全市場**（原始 22,478 列）——7,144 請求的計畫成立，不必退回逐股。
- **#20**：落地過濾 lf2 生效——單日濾 20,208／22,478 ＝ **89.9%**，濾後 2,270 列，與預估逐位相符。
- **#12**：Python 3.14 下本腳本正常（2020 全年 979 請求 `rc=0`）。
- **#19**：`data_version` 數＝1（自動沿用，全程未帶 `--data-version`）。
- **#14（官方端點不佔 FinMind 額度）**：官方端點視窗的 run 摘要印 `FinMind 請求 0 次`，成立。
- **#9（BFI82U 在 2020 年初可用）**：`twse_bfi82u` 1,618 鍵全數 `ok`、`failed=0`，2020-01 起回應形狀正常。
- **TPEx TLS（新增）**：`tpex.org.tw` 需 `--tpex-no-verify`；失敗鍵不寫 coverage 故自動重抓，
  且 `record_success` 同一交易內清 failures（`src/iching/store.py:207`），重抓成功後 `report` 不留殘影。
  **降級說明**：抓的是公開免認證盤後統計、請求不含任何憑證或身分，範圍限該 host；姊妹站 `taiwan-flows`
  同端點亦為 `verify=False`（`src/foreign_backfill.py:53`）／先試後退（`src/totals.py:73`）。

## 8. 不在本腳本範圍（與 `src/iching/config.py` 頂端 `OUT_OF_SCOPE` 逐項同步）

| 項目 | 本腳本 | 由誰負責 |
|---|---|---|
| B3.1 #6 事件版本鏈（events.db，as-of T） | 不負責 | P2 每日班的公告收集器（裁定乙：Worker→Actions；S1 §A4）；歷史公告無官方回補來源 |
| B2.5 集保週頻 `TaiwanStockHoldingSharesPer` | 不抓 | spec 明載首筆 2026-08-07、無歷史、不進共同核心分數（B1.9 表列）；每日班逐週落地 |
| B3.1 #5 PIT 池「T 日所屬市場」判定 | 只落地原料（`raw_stock_info` 含殘留列＋`raw_price_daily`）；`universe.pit_pool()`＝合格代號 ∩ 當日有列，**不分市場** | 後續 universe 模組以殘留列 `date` 重建轉換點（P0-A §4.4，誤差 1–2 日） |
| B3.1 #7／#8／#11 遲滯狀態、聚合中間結果、本管線歷史分數（scores.db） | 不負責（回測輸出） | P2 重播模組 |
| B3.1 #9 版本三元組（model_version／data_version／text_version） | 只產生並寫入 `data_version`（`fm-YYYYMMDD-<批次>`，進每筆 coverage 與原始列） | `model_version`／`text_version` 由計分（`scores.db`）模組綁定 |
| 流動性門檻（裁定 1）／還原係數（裁定 5）／報酬計算（裁定 3） | 不負責；只保證 `open` 與 `TaiwanStockDividendResult` 原始列落地 | 後續模組 |
| B1.5 官方法人、B1.3／B1.4 市場成交金額 | **已納入 core**（`twse_bfi82u`／`tpex_inst_summary`／`twse_fmtqik`／`tpex_trading_index`，原始 JSON 落地） | 解析交後續模組 |
