# P3 第 3 項：c／d 校準與凍結——開工文件（2026-09-19 盤點；動手前寫成）

> 體例沿 `docs/P3-KICKOFF.md`。盤點由 fresh-context 子代理實查（引用附 `檔案:行號`），主對話抽驗。
> **本檔 §5 有待裁定題目，裁定前不動任何程式。**

## 0. 規格定義（逐字出處）

- **c／d**：`S(x)=100/(1+exp(−k(x−c)))`，c＝中性點，d＝「S 由 50 升到 70 所需的 x 變化」，`k=ln(7/3)/d`（`spec/stock-iching-plan-v1.2.2.md:143`）；
  送入 S 前 x 截在 `[c−3d, c+3d]`（`:151`），S 值域 [7.30, 92.70]（`:155`）；情境門檻與旗標用**未截斷原值**（`:157-158`）。
- **校準判準正本**（v1.2.2 §4 只是沿革，`P1-B2-params.md:9` 列九處衝突）：`spec/P1-B1-market.md:42`＝`spec/P1-B2-params.md:56` 第 3 點——
  取**訓練段**樣本 `|x−c|` 的 **p85**，令 `3d = p85`，即 **`d = p85 ÷ 3`**；暖機／驗證／保留段皆不進母體。
  例外：①持續性類（B2.5 族 C）不套 p85，`d＝原始值域上界÷3`（`P1-B1-market.md:44`）；②距離型 `(X−MA_n)/ATR14` 維持依 MA 窗長查表
  0.6／0.8／1.0／1.5，但「仍待以同一 p85 判準覆核」（`:45`）。
- **閘門**（`P1-B1-market.md:43`、`v1.2.2:718`）：訓練段截斷比例 >15% → d 過小、必須重定；樣本外 >15% → 只人工複核、**不得回頭改 d**。
  三期間、兩市場**各自**統計、逐項列表（`:714` 步驟 3 不得省略 `market` 維度）。
- **凍結**（`v1.2.2:601`、`:727-729`；`docs/pre-registration.md:17,:19,:317-319`）：順序＝c／d 校準（只用訓練段）→ 生成卦別排序表 → **一次凍結**
  （同一 commit 含校準後 `model_version` twse／tpex 各一、`calibrated=true` 參數集、§1.6 排序表、校準報告）；凍結 commit 早於任何結果 commit；
  保留段一經動用即消耗（`:718` 尾段）。排序表由**訓練段分組報酬**決定，樣本外只驗證凍結排序、不得重排（`:601`）。
- **改 d 的連鎖**（`src/iching/score/params.py:182-208`）：`fingerprint()` 對每個 `Param` 全欄位入 sha256 → `params_sha`／`model_version` 必變 →
  每日班 `check_snapshot_meta` 拒續算、`scores.db`／`data/scores`／`cross.json`／`data/backtest`（manifest `params_sha`）**整套作廢**；
  §16.5 `:712/:714/:716/:717`（合法範圍、可達邊界、分布、門檻行為八項）全部重跑。
- **統計層借用**（裁定 #2）：`taiwan-backtest/audit/run_research.py:117` `block_boot_ci(x, block, nboot, seed)`、`:131` `nw_se(x, lag)`，
  皆吃日序列；本專案 `block=max(21,3h)`、`nboot=1000`（`pre-registration.md:252`），非該 repo 預設。

## 1. 現況與缺口

| # | 項目 | 現況 | 依據 |
|---|---|---|---|
| 1 | 目前 c／d | **設計初值**，`calibrated=False` 硬寫 | `params.py:3`、`:506`；資料集 `calibrated` 全 0 來源即此 |
| 2 | **子指標原始值 x** | **無任何落地路徑**：`scores` 表 45 欄只有爻分；`features.db` 六表是廣度／產業聚合／`p_cs`；x 只在 `Ind`／`SubResult` 記憶體物件 | `scores_io.py:39-62`、`features_io.py:63-109`、`transform.py:59-62`、`aggregate.py:18-26` |
| 3 | 資料集 | 14 欄，不帶爻分、不帶 x、`fwd_ret` 不扣成本 | `manifest.columns`、`docs/P3-DATASET.md` Q16 |
| 4 | 訓練段卦別樣本 | 足夠：train_short 64 卦全覆蓋，最少 1,185 列（卦 29）、最多 64,205（卦 1）；空 1.85% | 實算 `train_short.csv.gz` |
| 5 | 排序表格式 | 規格未定義欄位與分組方式 | `v1.2.2:601`、`pre-registration.md:314-316` |
| 6 | 成本模型 | 登錄書已定：個股手續費 0.1425%×2、證交稅 0.3%、滑價每邊 0.2%（敏感度 0.1/0.2/0.3）；大盤 TX 口徑 §1.2.4。**程式零實作** | `pre-registration.md:74-75,:202-217` |
| 7 | `pre-registration.md:311` 引用錯誤 | 稱「§16.1 明文要求排序凍結」，實在 `:601`／`:727` | 順手更正 |

**第一個缺口就是 #2**：沒有 x 就無法算 p85。這是新工件，不在 `docs/P3-DATASET.md` §0 範圍內。

## 2. 施工序列（每步先寫驗收條件再動手）

1. **x 統計出口**（新）：`replay_scores.py` 加選項 `--dump-x <dir> --dump-from --dump-to`，在計分時把每個子指標的
   `(market, horizon, line, family, indicator_id) → x` 以 float32 追加寫入分鍵檔（只在訓練段日期；量級估 603 日 × ≈1,900 檔 × ≈45 指標 × 3 期間
   ≈ 1.5×10⁸ 值 ≈ 0.6 GB；Hetzner 磁碟可容）。另一支 `scripts/calibrate_d.py` 讀分鍵檔算每鍵 `|x−c|` 的 p85、`d_new=p85/3`、
   現行 d 下的截斷比例、新 d 下的截斷比例，輸出 `runs/calib/d_report_<TO>.json`＋可讀表。**不動 params.py**。
   驗收：合成世界下 dump 值逐位＝`SubResult.x`；p85 與 numpy 直算相同；持續性族與距離型分開標示；離線測試。

   **實作交付（2026-09-19，commit `2c3eb76`，fresh-context 驗收 A～G 全 PASS）**：
   - **檔案**：`src/iching/xdump.py`（新，`XDump`／`key_name`／`shared_d_of`／`load_manifest`）；`src/iching/replay_step.py`
     `step()` 多一個關鍵字參數 `on_scores: Callable[[MarketScores|StockScores], None] | None = None`（預設 None＝一字不多做；
     `daily_core.py` 的呼叫未動）；`scripts/replay_scores.py` 加 `--dump-x DIR`／`--dump-from`／`--dump-to`（預設
     `2021-01-01`～`2023-06-30`＝`export_dataset.SEGMENTS["train"]`）／`--dump-only`；`scripts/calibrate_d.py`（新）；
     `scripts/hetzner_calib.sh`（新）；`tests/test_calibrate.py`（新，7 支）＋`tests/test_pit_world.py` 的 `HETZNER_SH` 參數化多一組 `calib`
     ＋`test_hetzner_calib_refuses_without_scores_db`。**`params.py` 一字未動。**
   - **x 出口介面**：dump 檔＝`<dir>/<scope>__<market>__<horizon>__<line>__<family>__<indicator_id>.f32`（little-endian float32 平鋪，
     `numpy.fromfile(path, dtype="<f4")`），`<dir>/manifest.json`（`schema`／`data_version`／`params_sha`（＝`replay_meta` 同一支
     `params_fingerprint`）／`params`（重算指紋用）／`dump_from`／`dump_to`／`days_dumped`／`tables`（兩市場的 `distance_d`／
     `market_slope_d`／`stock_slope_d`）／每鍵 `n`／`skipped`／`date_min`／`date_max`＋`ParamSet` 快照 `c`／`d`／`transform`／
     `clip_policy`／`direction`／`window`／`shared_d_table`／`shared_d_n`／`x_kind`）。`x` 為 `None`／NaN 跳過並計 `skipped`。
     **`basis` 例外**：其 c 是每日滾動 60 日中位數（`Param.c=None`），檔寫 `x − c_rolling_median`（`x_kind="x_minus_rolling_c"`），
     校準以 c=0 算 `|x−c|`。鍵由 `ParamSet` 預建（304 鍵＝152×2 市場），一筆都沒出現的鍵 manifest 仍列 `n=0`。
     目錄非空拒開（append 會重複計數）。
   - **`--dump-only`**：計分但不寫 `scores.db`／快照，只寫 dump；`replay_scores.py` 原本沒有「只重算某區間但不寫 db」的模式
     （`--resume` 只能接在 db 末日之後、`--from` 要前一日快照且照樣寫 db），故新加；重播仍從最早交易日起（Q7），`--to` 未給時取 `--dump-to`。
   - **`calibrate_d.py`**：每鍵 `|x−c|` 的 p85（`numpy.percentile(..., 85, method="linear")`，method 寫進報告）、`d_new=p85/3`、`d_old`、
     舊／新 d 截斷比例、`median(x)`＋`|median−c|>d_new` 旗標（Q2）；分類 `calibrate`（S＋clip_3d 及 `margin_scenario` 的 scenario＋clip_3d；
     斜率族列 `shared_d_table` 供對照，每個視窗 n 只被一個期間引用故逐鍵校準不衝突）／`persistence`（不校準，報 `d_formula＝值域上界÷3`＝n/6）／
     `distance`（逐鍵 p85 與查表值、差 >25% 標 `adopt_p85`（Q3）＋**同一格 `distance_d[n]` 的合併樣本 p85**（market × n、另列各 scope），
     多鍵共用一格時兩者都給、不替人選）／`not_applicable`（clip n/a，只列筆數）。閘門清單見下「不確定處」。輸出
     `runs/calib/d_report_<dump_to>.json`＋`.txt`；rc 0／2（目錄或 manifest 缺、`params_sha`≠現行碼指紋（`export_dataset.expected_params_sha`）、
     `.f32` 筆數≠manifest）。**閘門超標不改 rc**。
   - **盤點（`build_params` 靜態數、每市場）**：`calibrate` 86 鍵（34 個 indicator_id：S＋clip_3d 的 33 個＋`margin_scenario`）、
     `distance` 15 鍵（`dist_ma_short`／`dist_ma_long` 大盤與個股各 3 期間＋`spx_ma_distance` 3 期間，共用 `distance_d[5/10/20/60]`）、
     `persistence` 6 鍵（`foreign_persistence` 3＋`foreign_buy_days` 3）、`not_applicable` 45 鍵（L 21／scenario 11／passthrough 7／
     P_hist 3／P_hist_rev 3）；兩市場合計 304。transform×clip 全表：`S+clip_3d` 208、`L` 42、`scenario` 22（另 6 個 `scenario+clip_3d`
     ＝`margin_scenario`）、`passthrough` 14、`P_hist` 6、`P_hist_rev` 6。
   - **估算量級（合成世界外推，未實測真實池）**：每個個股列（一檔×一期間×一日）子指標數 short 22／swing 22／mid 29（不含 passthrough），
     大盤 24；603 訓練日 × ≈1,900 檔 × 73 ≈ **8.4×10⁷ 值 ≈ 0.34 GB 上界**（缺值跳過後更少；大盤 ≈ 8.7×10⁴ 值可忽略）——本節原估 0.6 GB
     為上界。合成世界（5 檔、31 日）實跑 6,554 值／跳過 8,276、有無 `--dump-x` 每日成本同量級（單次量測皆 ≈22 ms/日，不足以下「零開銷」的結論；真實池要看 Hetzner log）。
     Hetzner `--dump-only` 時長＝從 2020-01-02 重算到 2023-06-30 ≈ 850／1628 日 × 12.6h ≈ **6.6h**（不寫 db 略快；本節第 2 步寫的
     4.7h 只算了 603 個訓練日，2020 年暖機段照樣要計分、只是不寫）。
   - **不確定處／待裁定**：①**閘門的離散化餘裕**——`d_new=p85/3` 之下訓練段 `|x−c|>3d_new` 的比例由構造即≈15%，linear 內插使比例
     可達 15%＋1/n（n=603 的大盤鍵＝15.09%），嚴格「>15%」會讓校準後的鍵系統性超標零點幾個百分點；實作取 `gate_fail`＝`>15%＋100/n`
     （一個樣本的餘裕）並另存 `gate_fail_strict`（`>15%`），兩份清單都在報告，**哪一份算閘門要裁定**。②`foreign_buy_days`（B1.4 族 C）
     歸 `persistence`：規格 5a 只點名 B2.5 族 C，此鍵同性質（3d=6≥5 截斷不觸發）故同歸類、報告標「歸類待裁定」。③持續性族現行 d
     **不等於**「值域上界÷3」：`foreign_persistence` 1.0／2.0／3.34 vs 公式 0.833／1.667／3.333，`foreign_buy_days` 2.0 vs 1.667——
     現值是為了 `3d ≥ 上界` 反推的整數位候選、不是 ÷3（`params.py` 註解「3d ≥ 值域上界，截斷永不觸發」），報告 `d_formula_matches_old=N`，裁定時要選一邊。④距離型多鍵共用一格 d 時逐鍵 p85 必不一致
     （大盤 vs 個股 vs SPX），Q3「差 >25% 改採 p85」沒說取哪個 p85——報告給逐鍵與合併兩種，**取法待裁定**。⑤`p85=0` 的退化鍵
     （≥85% 樣本恰等於 c，合成世界有 4 個）`d_new=0` 非法，列 `degenerate`、不算閘門。⑥dump 用 float32：對 `|x−c|` 的 p85 有 ≈1e-7
     相對誤差，對 d 無實質影響，但 `d_report` 的數字**不是**雙精度直算值。⑦`--dump-only` 的重算與既有 `scores.db` 是否逐位一致
     未在腳本內驗（守門只比 `params_sha`）；同一份原料同一份碼應相同（`test_dump_only` 在合成世界驗過 f32 與一般跑逐位相同）。
2. **Hetzner 跑 x 出口**：不重播全段，只跑訓練段 2021-01-01～2023-06-30（需含暖機，由 `--dump-from` 控制寫出）。約 603/1628 × 12.6h ≈ 4.7h。
3. **裁定 d**（見 §5 Q3～Q5）→ 寫入 `params.py`（`calibrated=True`）、`RULES_VERSION` bump、登錄 `model_version`。
4. **全量重播**（12.6h）→ 新 `scores.db` → `check_scores` → §16.5 `:712/:714/:716/:717` 重跑 → 種子／分數／資料集重匯（`hetzner_adj.sh` 同型腳本）。
5. **排序表**：`scripts/rank_table.py` 讀校準後 `train_*.csv.gz`，依 §5 Q6 格式與成本模型算分組報酬，寫 `docs/pre-registration.md` §1.6 附錄＋`data/rank_table.json`。
6. **一次凍結 commit**：登錄書（`model_version`、`calibrated=true` 參數集、排序表、校準報告、Python 3.12、`data_version`）＋覆蓋 PR
   （分數／種子／資料集）。凍結後每日班改吃新指紋。
7. 第 4 項統計層（IC／NW／block bootstrap／分組報酬／成本）另開工文件。

## 3. 完成定義

- [ ] `d_report`：每個 `clip_3d` 子指標 × 3 期間 × 2 市場的 p85、d_new、舊／新截斷比例；訓練段新 d 下截斷比例 **≤15%** 逐項成立；持續性族標「不校準」；距離型附覆核結論。
- [ ] `params.py` 校準值與 `d_report` 逐項相同（測試守）；`calibrated=True`；`model_version` 記入登錄書。
- [ ] 全量重播綠、`check_scores` 合理、§16.5 四項報告附登錄書。
- [ ] 排序表由校準後訓練段生成、含成本、格式＝§5 Q6 裁定；驗證段程式不重排（測試守）。
- [ ] 凍結 commit 單一、早於任何驗證段結果；`pre-registration.md:17,:19` 由 TBD 改為實值。
- [ ] fresh-context 驗收綁 commit；每日班下一班綠。

## 4. 已知風險

- 校準後卦象全變，線上預覽版的卦與說明會一夕不同——凍結 PR 合併當日要在頁面頂列標「已校準（model_version …）」。
- 距離型 d 若照 p85 覆核後大改，`:717` 門檻行為八項可能 >10% 差異，登錄書要逐項說明。
- 兩市場各自校準 → 同一檔股票轉市（PIT）前後 d 不同，屬設計內（`:714` 步驟 3）。
- x 出口若寫在 `replay_scores` 熱路徑，要確認不改變分數（dump 只讀 `SubResult`），以「開／關 dump 分數逐位相同」守。

## 5. 待裁定

- **Q1 x 出口形式**：(a) 只在訓練段把 x 落地成分鍵 float32 檔（≈0.6 GB，Hetzner 本地、不進 git；精確 p85）【建議】 (b) 串流分位數近似（t-digest，不落地）。
- **Q2 c 是否動**：判準只定 d（`|x−c|` 用現行 c）。建議 **c 不動**，只校 d；若某指標訓練段 x 中位數離 c 很遠（|median−c| > d_new）列入報告供人看，不自動改。
- **Q3 距離型**：p85 覆核結果與查表值差 >25% 時，(a) 改採 p85【建議】 (b) 維持查表只記錄。
- **Q4 持續性族**：照規格不校準（`d＝值域上界÷3`）。確認。
- **Q5 兩市場各自校準**：照 `:714`。確認；並確認同一子指標三期間各自一組 d（現行結構即如此）。
- **Q6 排序表格式**：建議 `market × horizon × king_wen`：n、訓練段淨報酬均值（扣成本：手續費 0.1425%×2＋證交稅 0.3%＋滑價 0.2%×2）、中位數、排名（1～64）；
  高組＝排名前 1/3、低組＝後 1/3、其餘中組；**n < 500 的卦不參與排名、歸中組並標示**。空 `king_wen` 列不計。
- **Q7 訓練段 x 出口的暖機**：`--dump-from 2021-01-01`，但重播仍從 2020-01-02 起算（狀態鏈不可跳日）；只寫出、不縮短重播。確認。
- **Q8 順序與成本**：接受一次 ≈4.7h 的 x 出口跑批＋一次 12.6h 全量重播＋一次覆蓋 PR（第五次）。確認。

## 6. 裁定紀錄

- **裁定 #54（2026-09-19，使用者：「全照建議」）**：Q1 (a) 訓練段 x 落地分鍵 float32 檔（Hetzner 本地、不進 git、精確 p85）；Q2 c 不動只校 d，
  `|median−c| > d_new` 者列報告不自動改；Q3 距離型 p85 覆核與查表值差 >25% 改採 p85；Q4 持續性族不校準（`d＝值域上界÷3`）；
  Q5 兩市場各自、三期間各一組 d；Q6 排序表 `market × horizon × king_wen`（n、扣成本淨報酬均值／中位數／排名，高組前 1/3、低組後 1/3，
  n<500 歸中組並標示，空 `king_wen` 不計）；Q7 x 出口只寫訓練段日期、重播仍自 2020-01-02 起；Q8 接受 ≈4.7h＋12.6h 兩次跑批與第五次覆蓋 PR。
- **主對話裁決（2026-09-19，實作交付後）**：①閘門以規格 15% 為準，`d_new=p85/3` 下截斷比例由構造 ≈15%，linear 內插的離散化可使其達
  15%＋1/n——**差距 ≤1/n 視為離散化誤差、不算超標**（報告 `gate_fail`＝15%＋1/n 版為判定、`gate_fail_strict` 並列供人看）；
  ②`foreign_buy_days` 暫歸 persistence（與規格點名的 B2.5 族 C 同性質），凍結前由使用者確認；③持續性族現行 d ≠「值域上界÷3」
  （1.0／2.0／3.34 vs 0.833／1.667／3.333）——校準時**改為規格公式值**並記入登錄書（屬「縮放」，§16.5 `:717` 要重驗）；
  ④距離型共用一格 d：採**合併樣本 p85**（同一 n 的所有鍵合併）作覆核值，逐鍵 p85 只列報告；⑤Hetzner 時長估算更正為 ≈6.6h（含 2020 暖機段）。
