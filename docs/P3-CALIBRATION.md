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
   - **2026-09-20 追加（零膨脹決策所需數字；動機與全表證據見 §7.3 A，該節的「已動手」即本段）**：Hetzner 首份 `d_report`
     （`origin/hetzner/calib-2023-06-30`）揭露規格未預期的
     **零膨脹**——`trust_strength_long/short` 在上櫃有 6 鍵 `p85 = 0`（≥85% 樣本 x 恰為 0 ⇒ `d = p85/3 = 0` 不合法、列 `degenerate`），
     上市對應鍵雖未退化但 d 由 5 掉到 0.287（幾乎成二值旗標）；`short_sale_change` 疑似同型。**要裁定怎麼處理，得先有「零比例」
     這個數字，而原報告完全沒有。** 本批只讓 `calibrate_d.py` 多吐決策所需的數字，**不改 `params.py`、不改任何既有輸出**。
     - **新欄位（每個 `n>0` 且「需要 d」的鍵一律吐，不需旗標）**：`z_zero`＝`x` **恰為 0** 的樣本比例（`x_kind="x_minus_rolling_c"` 的鍵，
       檔內存的已是 `x − c_rolling`，其零即 `x − c_rolling == 0`；**與 `|x−c| = 0` 不是同一件事**——c≠0 的鍵，x=0 的樣本
       `|x−c|` 是 `|c|`）、`n_nonzero`、`p85_nonzero`（**只取非零樣本**的 `|x−c|` 分位，`n_nonzero=0` 時 null）、
       `d_nonzero = p85_nonzero ÷ 3`（null 傳遞）、`clip_nonzero_pct`（`d_nonzero` 下**全體樣本**的截斷比例，供閘門判定——
       **閘門的分母定死為全體**（`P1-B1-market.md:44`），換成非零樣本就不是同一個口徑；偏差方向視 `|c|` 而定——c=0
       （214 個需要 d 的鍵中有 208 個）時零樣本 dev＝0、永不截斷，只算非零會**偏高**，c≠0 時才偏低）。
       頂層 `zero_inflation` 兩份清單：`z_ge_85pct`（`z_zero ≥ 0.85`；**c=0 時** `p85`＝0＝退化，c≠0 的鍵零樣本 dev＝`|c|`、`p85` 不一定為 0）與
       `z_ge_50pct`（前者的超集），**只收需要 d 的三類**（`calibrate`／`distance`／`persistence`），各列 `key`／`category`／
       `z_zero`／`n`／`n_nonzero`／`p85`／`p85_nonzero`／`d_old`／`d_nonzero`／`clip_nonzero_pct`。
       **`not_applicable`（`clip_policy=n/a`）五欄一律 null，且刻意不讀它的 `.f32`**（主對話裁決，2026-09-20）：它沒有 c
       也沒有 d，零膨脹對它零決策價值，而讀了只會在真實 dump 上多出未實測的 I/O，還把「檔長 ≠ manifest `n`」從靜默略過
       變成 rc 2——為零價值引入新失效模式。
     - **刻意不做 `--nonzero-only`**：那種旗標會改變主要輸出——得跑兩次才拿得到兩套數字，事後還分不清手上那份是哪一套。
       非零版一律以**額外欄位並存**。本批唯一的新旗標是 `--percentile P`（預設 85，取代模組常數 `PERCENTILE`；報告 `percentile`
       欄與 `.txt` 表頭照實寫，**欄名 `p85`／`p85_nonzero` 刻意不改名**，改名會讓歷次報告的欄位對不起來）——它正是 §7.3 A
       說「數值一律求不出」的另一個選項（更高分位）的工具，`--percentile 90` 跑一次即得，同樣不影響預設輸出。
     - **硬約束：同一份 dump、無新旗標時，報告既有欄位逐位相同**。`tests/test_calibrate.py::`
       `test_report_existing_fields_unchanged_vs_head_version` 以 `git show HEAD:scripts/calibrate_d.py` 取出改動前版本
       （放 tmp、以 `PYTHONPATH` 補 `src`／`scripts` 讓它的 `REPO` 指錯也 import 得到），對同一份合成 dump 各跑一次，
       逐欄比對（只排除 `generated_at`）：頂層與**每一列**的既有欄位**值與型別**逐位相同、新增欄位只能是上述那組，且那組
       **每一列都在**（含 `n=0`／`n/a` 的 null）。`.txt` 另以前綴性質守——**比對邊界切在新段起點「## 零膨脹」**
       （2026-09-20 修正：原本拿 HEAD 全文當前綴，會把「修正新段自己的錯誤敘述」誤判成破壞既有輸出），
       即表頭與閘門／退化／median／距離型／持續性五個既有段一字不動、新段措辭可改。突變實測：改**既有**段落標題會紅、
       改**新**段措辭放行；另三個突變（改 `d_new` 算式、`n=0`／`n/a` 列漏補 null、n/a 鍵又去讀檔）亦實測會紅。
       **這支的效力邊界（fresh-context 驗收 V1 指出）**：它比的是工作樹 vs `HEAD`，commit 之後 HEAD 就是新版自己、
       會變成自我比對，**證不出「對 `520503a` 逐位不變」**；那個一次性硬約束由驗收者在 `d5942f0` 獨立重做實測成立
       （頂層 23 鍵、304 列逐欄值＋型別、`.txt` 前綴），本檔即為其紀錄。這支留著是擋未來的意外改動。
     - **重跑方式**：x dump 仍在 Hetzner `cache/xdump`，`HETZNER_CALIB_REUSE_DUMP=1 bash scripts/hetzner_calib.sh`
       沿用既有 dump（manifest 的 `params_sha`／`dump_from`／`dump_to` 三者相符才放行）、跳過 6.6h 重算，分鐘級重出報告。
       **`hetzner_calib.sh` 本身未改。**
     - **記憶體與 I/O**：維持**逐鍵讀檔、算完即釋放**（真實 dump 單鍵最大約 59 萬 float32、全表 6,870 萬值，不得整表
       同時載入）；新欄位只在同一鍵內多一個非零樣本切片 `dev[x != 0]`，**讀的檔一個都沒多**——哪些鍵讀 `.f32` 與改動前
       完全相同（`n=0` 與 `n/a` 照舊不讀），測試以「包一層計數器記下每次 `load_x` 讀的檔名、斷言 n/a 鍵的檔一次都沒被開過」守。
     - **測試**：`tests/test_calibrate.py` 7 → 11 支（回歸硬約束、五個新欄位對 numpy 直算逐鍵比、人工造的零膨脹鍵
       ＋全體零鍵、`--percentile 90`）。
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

## 7. 校準報告與 d 定案提案（2026-09-20，Hetzner `hetzner/calib-2023-06-30` commit `2da73c7`）

### 7.1 報告基本數字（主對話自核）

`runs/calib/d_report_2023-06-30.json`：`params_sha=a6a3f35cd1f0`（＝現行碼）、`dump_from/to=2021-01-01/2023-06-30`、`days_dumped=603`、
`n_values=68,701,750`、`n_skipped=10,089,667`、`n_keys_with_data=266/304`、`percentile=85`、`method=linear`。
**主對話逐列核過**：全表 `d_new == p85/3` 零不符；`gate_failures` 8 鍵（皆 `market_index` 的 `dist_ma_short/long`）、
`gate_failures_strict` 135、`degenerate` 6（皆 tpex `trust_strength_*`，p85＝0）、`median_flags` 3（twse `trust_net_ratio`）。

### 7.2 全表結論（子代理分析，主對話抽驗）

- 164 個可校準且有資料的鍵中 **d 變小 102／變大 62**，只有 49 鍵落在 0.8～1.25，**約七成的 d 要動**——起點值整體偏離實際尺度。
- **現行 d 明顯過小 30 鍵**（`clip_old_pct > 30%`），最嚴重 `market_index__twse__short__2__C__new_high_low_ratio` **67.66%**
  （d 3→11.16，校準後 15.09%）；其後 `foreign_net_ratio` 51.1%、`pretax_income_yoy` 53.1%、`eps_yoy` 48.0%、
  `gross_margin_qoq` 46.4%、`ma20_slope` 45.3%。**校準的主要價值在這裡**：這些爻過去有三到七成的日子被壓在分數邊界上。
- **現行 d 明顯過大 24 鍵**（`clip_old_pct < 2%` 且 `d_new/d_old < 0.5`），分兩種成因：零膨脹（見 §7.3 A）與分布本來就窄
  （`amount_ratio`／`industry_relative_return`／`obv_slope`）。
- `calibrate` 類校準後 `clip_eff_pct` 全在 14.61～15.09%，**閘門（15%＋1/n）全過**。

### 7.3 三個超出既有裁定的題目

#### A. 零膨脹指標：p85 落在零質量上（規格未預期的第三種例外）

`trust_strength_long/short` 共 12 鍵，tpex 6 鍵 **p85＝0**（`d_new=0` 不合法，報告列 `degenerate`）；twse 6 鍵未退化但
**d 由 5 降到 0.287**（3d＝0.86，投信淨買超只要達成交量 0.86% 分數就打滿 92.70，S 退化成準二值旗標）。`short_sale_change` 6 鍵疑似同型。
**機制證據（子代理）**：`trust_strength` 與 `foreign_strength` 是同一條公式同一個分母（`params.py:467-471`），p85 卻差 9～25 倍；
且外資版 p85 隨窗長變小、投信版 twse 反而隨窗長變大——零原子隨窗長縮小的指紋。
規格兩個例外（`P1-B1-market.md:48` 持續性、`:49` 距離型）都不涵蓋這種情況。

**卡住的地方**：判斷「哪些鍵算零膨脹、門檻定在哪」需要**零比例 z**，而現行報告完全沒有這個數字；
「非零樣本 p85」與「更高分位」兩個選項的數值也一律求不出（報告只有 p85 一個分位）。
**已動手**：`calibrate_d.py` 追加 `z_zero`／`n_nonzero`／`p85_nonzero`／`d_nonzero`／`clip_nonzero_pct` 欄與 `zero_inflation` 清單
（既有欄位輸出逐位不變，有回歸測試守），Hetzner 以 `HETZNER_CALIB_REUSE_DUMP=1` 重跑**分鐘級**、不必再等 6.6 小時。
**建議**：先取得 z 的全表分布再定門檻與取法，不要憑現有兩個極端點（0 與 0.287）拍板。

#### B. 距離型共用查表 d 與 15% 閘門硬衝突（30 鍵）

8 個閘門超標鍵全是 `market_index` 的 `dist_ma_short/long`：查表 d 下截斷 **18.24～28.03%**。
**真因是裁定 #54 Q3 的「差 >25% 才改」容差**——這 8 鍵逐鍵差只有 7.5～21.0%，沒跨過 25% 就維持查表值，而查表值截斷遠超 15%；
改採逐鍵 `p85/3` 立刻回到 15.09%（報告 `clip_new_pct` 直接有）。
**主對話裁決 ④（採合併樣本 p85）修不掉**：合併被個股壓倒（twse n=60：大盤 603 樣本 vs 個股 592,498），
`POOL/ST` 比值 1.000～1.001、`POOL/MI` 0.758～0.873，**合併實質等於個股**；子代理以單調性證明 n=20／60 四格在合併取法下**必不過閘門**。

**規格怎麼說（主對話實查逐字）**：
- `P1-B1-market.md:46`：「此閘門對**所有**使用 `clip_3d` 的子指標一體適用，**含本文件未逐一點名者**」——距離型不例外。
- `:47`（第 5 點）：「任何未被點名的同類缺陷（**視窗變動而 d 共用**、d 明顯偏離實際尺度）都會被第 4 點的閘門攔下」
  ——規格**自己把「d 共用」列為閘門要攔的缺陷類型**。
- `:49`（第 6 點）：距離型「維持依 MA 窗長查表…此查表值同樣是**未校準起點值**，**待 P2 以同一 p85 判準覆核**」
  ——查表**結構**要保留（一格一個 MA 窗長、與持有期間無關），**值**要由 p85 覆核改寫。

**建議（取代裁定 #54 Q3 與主對話裁決 ④）**：**每格取該格所有鍵逐鍵 `p85/3` 的最大值**。
理由：①保留規格要求的查表結構（不拆表、不偏離 `:49`）；②d 取最大 ⟹ 每個鍵的截斷都不高於它自己的 `clip_new_pct`（14.93～15.09%），
**閘門對 30 鍵全部可證成立**，不需要原始 x；③自動取消 25% 容差這個規格沒有的發明。
代價：p85 較小的鍵（個股、SPX）會得到比自身 p85 規則更鈍的 d，要記進登錄書。
**不建議拆表**（按 scope 分兩張）：偏離 `:49`，且子代理指出大盤格內仍混 TAIEX 與 SPX（twse n=20：TAIEX 自身 d 1.09 vs SPX 0.84），拆了還是不乾淨。

#### C. 持續性族：**主對話裁決 ③ 成立，我先前的反向建議作廢**

`P1-B1-market.md:48`（5a）**逐字**：「故此類**不套 p85 判準**，`d` 固定為 `原始值域上界 ÷ 3`」。規格明文，不是可選項。
現值 `foreign_persistence` 1.0／2.0／3.34 與 `foreign_buy_days` 2.0 之中，短／波段兩檔與 `foreign_buy_days` **不等於**上界÷3
（0.833／1.667／1.667），mid 的 3.34 則約等於 10/3。照規格改：k 變陡 **1.2 倍**（mid 1.002 倍），單指標最大分數差 **+4.04 分**、
攤到方向總分 **≤+0.13 分**（子代理算），仍不截斷（3d 恰等於上界，`clip_3d` 用 `min/max` 夾取、等號不算截斷，`transform.py:80-82,:92`）。
**要同時做的事**（5a 明寫「不得只改一邊」的反面）：§16.5「可達邊界」登錄要重跑，因為餘裕由 0.02～1.0 變成 0、卡在邊界。
**附帶發現**：`params.py` **沒有任何欄位記錄 x 的原生值域**（`native_range` 預設是 `S_RANGE`＝轉換後分數值域，`params.py:44`），
上界只能由 `window/2` 反推（`calibrate_d.py:126-129`）。**本批不加欄位**（CANON 5 一次只做一件事），但登錄書要寫明上界的推導來源。
`foreign_buy_days` 歸持續性族（而非 calibrate）確認採用：走 calibrate 會得到 d 0.667／1.0、截斷 14.76%／7.30%，
使 v1.2.2 §4.1a「持續性截斷永不觸發」失效，正是 5a 要避免的情況。

### 7.4 順帶更正與待辦

- **行號更正**：本檔 §0 把持續性例外記成 `P1-B1-market.md:44`、距離型記成 `:45`，**實查兩者都漂了 4 行**：5a 在 `:48`、第 6 點在 `:49`；
  `:44`／`:45` 現在指的是閘門的訓練段／樣本外兩個子條目。逐字副本 `P1-B2-params.md:56`／`:62` 的行號是對的。
- **資料品質（不阻擋校準，另案）**：`foreign_strength_*` 的 `x_min` 出現 **−3,532%**，而該值是淨買超佔成交比、結構上應在 [−100, 100]；
  `gross_margin_qoq` ±246,900 pp、`revenue_yoy` 7.59e6 pp 是分母近零所致。p85 對這些離群穩健（實證 `gross_margin_qoq` p85 僅 7.39／9.14），
  故不影響 d，但 `foreign_strength` 那個值**結構上不該出現**，要回原料庫查。
- **`pretax_income_yoy` 小樣本**：n 僅 4,607（tpex）／13,171（twse）＝金融股替代路徑，而 d 由 20→55.76／38.56（×2.79）。
  照 p85 判準處理，但登錄書要標小樣本，並列入驗證段人工複核名單（樣本外超標不回頭改 d，`:45`）。
- **`equity_qoq` 2 鍵**被歸在 calibrate 但永遠無資料（裁定 #36 乙），應標「不校準（無資料源）」；`vix_phist_rev` 6 鍵 n=0 屬裁定 #26 的預期狀態。

## 8. 第 3 步「把 d 寫回 `params.py`」的設計與驗收條件（2026-09-20 寫成，動手前）

### 8.1 結構障礙（主對話實查）

校準值不能逐處硬改，因為現行 d 的來源有三種形狀：

| 形狀 | 例 | 問題 |
|---|---|---|
| 逐期間查表 | `MKT_L4_D_FOREIGN[h]`／`MKT_L4_D_TRUST[h]`（`params.py:259-260`） | 已可逐期間給值，但**仍跨市場共用**（`build_params(market)` 兩市場跑同一段碼） |
| **裸字面量** | `foreign_buy_days` 的 `2.0`、`margin_change` 的 `2.0`（`params.py:347`／`:349`） | **一個字面量同時服務 3 期間 × 2 市場**，裁定 #54 Q5 要求各自一組 d ⟹ 必須改成查表 |
| 共用窗長表 | `distance_d[n]`（`:249`）、`market_slope_d[n]`／`stock_slope_d[n]`（`:250-251`） | 距離型跨 scope 共用＝§7.3 B 的衝突；斜率表**每個 n 只被一個期間引用**（`MKT_L1_WIN`／`STK_L2_WIN`），逐鍵校準不衝突 |

### 8.2 設計（主對話裁決，屬工程選擇非判準）

新增 `src/iching/score/calibrated.py`：**由報告產生、可重生、可 diff 的校準值表**，`build_params` 查它、查不到才用設計起點值。

- `CALIBRATED_D: dict[tuple[str, str, str, str], float]`，鍵＝`(market, scope, indicator_id, horizon)`；
  `CALIBRATED_DISTANCE_D: dict[str, dict[int, float]]`、`CALIBRATED_SLOPE_D`（市場／個股兩張）依 `(market, n)`。
- `CALIBRATION_META`：來源報告 commit 與 sha256、`dump_from/to`、`percentile`、逐類別採用的規則（`p85/3`／`slot_max`／`formula`／`keep_old`／
  `nonzero_p85/3`）、產生時間、產生腳本版本。**登錄書引用這個 meta，不是引用口頭裁定。**
- 產生腳本 `scripts/apply_calibration.py`：讀 `runs/calib/d_report_<TO>.json`＋規則設定 → 寫 `calibrated.py`；**純函式可離線測**，
  同輸入同輸出（決定性），不連網、不碰 `scores.db`。
- `params.py` 的改動限於：①裸字面量 d 改成 `cal_d(market, scope, indicator_id, h, default=<原字面量>)` ②三張共用表改成查 `CALIBRATED_*`、
  缺項回退 `*_START` ③`ParamSet.calibrated` 由 `CALIBRATION_META` 是否存在決定。**不動任何 c、不動權重、不動視窗。**

**為什麼不是「直接改字面量」**：172 個鍵分佈在跨期間跨市場共用的字面量上，硬改要先拆迴圈，diff 大且無法用測試逐項守；
**為什麼不是「讀 JSON 資料檔」**：`params_sha` 必須由程式碼本身決定（`fingerprint()` 對 `Param` 全欄位雜湊），
把值放進 `.py` 常數讓「參數＝程式碼」這個既有性質不變，也不新增執行期檔案相依。

### 8.3 驗收條件（綁 commit；修改者不得自驗）

- **H1 逐項相同**：對報告中每個 `calibrate`／`distance`／`persistence` 鍵，`build_params(market)` 產出的對應 `Param.d`
  （距離／斜率走 `ParamSet.*_d[n]`）**逐位等於**提案表 `d_proposed`；反向也要驗：`CALIBRATED_*` 沒有任何報告以外的鍵。測試讀報告與提案表，不寫死數字。
- **H2 只有 d 變**：對 **`05f4120`** 版 `build_params` 的輸出做欄位級 diff（基準釘死該 commit，不用 `HEAD`），**除 `d`（與 `ParamSet.calibrated`）外每個 `Param` 欄位逐位相同**
  （`c`／`native_range`／`clip_policy`／`direction`／`unit`／`window`／`formula`／權重／族權重全不動）。
- **H3 指紋如預期變**：`params_sha`／`model_version` 兩市場皆改變，且**新值寫進測試**（下次誰再動 d 就會紅）；
  同時確認舊指紋 `a6a3f35cd1f0` 不再出現在 `build_params` 輸出。
- **H4 未校準鍵維持起點值**：`not_applicable`、`n=0`（`vix_phist_rev` 6 鍵／`equity_qoq` 2 鍵，裁定 #26／#36 乙）逐鍵仍是 `*_START` 值。
- **H5 決定性**：`apply_calibration.py` 對同一份報告跑兩次輸出逐位相同；`calibrated.py` 的 commit 版本＝重跑版本（CI 或測試守）。
- **H6 閘門**：提案表每個鍵的 `clip_expected_pct` ≤ 15%＋1/n；不成立者必須在 `CALIBRATION_META` 的例外清單裡並附理由。
- **H7 全量測試綠**（基準 **`05f4120`＝987 passed／20 skipped**，只能增）；ruff 零新增項；fresh-context 驗收綁 PR head。
- **H8 下游一致**：`export_dataset.expected_params_sha`、`run_common.check_snapshot_meta` 的守門在新指紋下行為正確
  （舊 `scores.db`／`cross.json`／`data/scores`／`data/backtest` 一律被拒＝**預期**，不是 bug）。

### 8.4 套用後的連鎖（提醒，非本步驟）

全量重播 12.6h → `check_scores` → §16.5 `:712/:714/:716/:717` 四項重跑（`:717` 門檻行為八項因 d 改變屬「縮放」，差異 >10% 要逐項說明）
→ 種子／分數／資料集重匯（`hetzner_adj.sh` 同型）→ 排序表 → 一次凍結。**線上預覽版的卦會一夕全變**，凍結 PR 合併當日要在頁面頂列標
「已校準（`model_version` …）」——§4 已知風險第 1 條。

     - **驗收後修正（2026-09-20，fresh-context 驗收 `d5942f0` V8 的三項發現；V1～V7 全 PASS）**：
       ① **「只拿非零樣本算會低報」方向寫反**——閘門的分母定死為全體樣本（`P1-B1-market.md:44`），換成非零樣本是
       換掉分母、與閘門口徑不同；偏差方向視 `|c|` 而定，而 214 個需要 d 的鍵中 **208 個 `c=0`**（零樣本 dev＝0、永不截斷），
       對這批「只算非零」反而**偏高**。實作一直是對的（用全體），錯的是理由。
       ② **「`z_zero ≥ 85%` ⇒ `p85` 必為 0」只在 c=0 成立**——c≠0 的鍵零樣本的 dev＝`|c|`。這句原本無條件印在 `.txt`
       的段落標題上，而驗收者造的 c=1.0 反例就列在該標題底下、同列 `p85` 印 1.0000，**標題與資料互相打臉**。已改為標明只在 c=0 時成立。
       ③ `.txt` 前綴測試的基準與命名（見上段）。三項都不影響任何數值，只影響敘述與測試邊界。

## 9. 裁定 #55（2026-09-20，使用者：「同意」）：距離型改「每格取該格所有鍵 `p85/3` 的最大值」

**取代**裁定 #54 Q3 的「差 >25% 才改採 p85」容差（規格沒有這條、且它正是 8 個鍵超標的唯一成因）
與 2026-09-19 主對話裁決 ④ 的「合併樣本 p85」（合併被個股壓倒、可證修不掉大盤）。

**規格依據**（主對話實查逐字）：`P1-B1-market.md:46` 閘門「對**所有**使用 `clip_3d` 的子指標一體適用，含本文件未逐一點名者」；
`:47` 把「**視窗變動而 d 共用**」明列為閘門要攔的缺陷類型；`:49` 距離型查表值「同樣是未校準起點值，**待 P2 以同一 p85 判準覆核**」
——**結構保留（一格一個 MA 窗長）、值由 p85 覆核改寫**，正是本裁定。

### 採用值（由 `d_report_2023-06-30.json` 逐鍵 `p85/3` 取每格最大，主對話實算）

| market | n | 查表起點 d | 採用 d | 倍數 | 取自 | 同格其他鍵的 `p85/3` |
|---|---:|---:|---:|---:|---|---|
| tpex | 5 | 0.60 | **0.4207** | 0.70 | `market_index｜tpex｜short｜1｜A｜dist_ma_short` | 0.386, 0.355 |
| tpex | 10 | 0.80 | **0.7122** | 0.89 | `market_index｜tpex｜swing｜1｜A｜dist_ma_short` | 0.567, 0.543 |
| tpex | 20 | 1.00 | **1.0746** | 1.07 | **三鍵平手**（`mid｜dist_ma_short`、`short｜dist_ma_long`、`swing｜dist_ma_long`，皆 MA20） | 0.841, 0.804×3 |
| tpex | 60 | 1.50 | **1.8149** | 1.21 | `market_index｜tpex｜mid｜1｜A｜dist_ma_long` | 1.374 |
| twse | 5 | 0.60 | **0.4365** | 0.73 | `market_index｜twse｜short｜1｜A｜dist_ma_short` | 0.386, 0.350 |
| twse | 10 | 0.80 | **0.7074** | 0.88 | `market_index｜twse｜swing｜1｜A｜dist_ma_short` | 0.567, 0.535 |
| twse | 20 | 1.00 | **1.0923** | 1.09 | **三鍵平手**（同上三鍵，皆 MA20） | 0.841, 0.793×3 |
| twse | 60 | 1.50 | **1.7549** | 1.17 | `market_index｜twse｜mid｜1｜A｜dist_ma_long` | 1.355 |

**八格的最大值全部來自 `market_index` 鍵**——這就是共用表在大盤失效的機制：大盤指數的距離分布比個股寬，而合併樣本被
個股（59 萬 vs 603）壓倒。

**閘門（可證，不需原始 x）**：d 取該格最大 ⇒ 每個鍵的截斷都不高於它自己的 `clip_new_pct`。全 30 鍵 `clip_new_pct` 最大
**15.09%**（`market_index｜tpex｜mid｜1｜A｜dist_ma_long`），閘門為 15%＋100/n＝**15.17%**，**30 鍵全過**。
對照查表起點值：`clip_eff_pct` 最大 **28.03%**、超標 8 鍵。

**代價（登錄書要寫）**：p85 較小的鍵拿到比自身 p85 規則更鈍的 d。最大一筆是 n=20 那兩格的個股鍵（`p85/3`＝0.804／0.793 →
採用 1.0746／1.0923，鈍化約 34%～38%），其截斷因此降到 15% 以下。MA5／MA10 兩格則**反而變靈敏**（0.60→0.44、0.80→0.71），
因為起點查表值對兩個市場都偏大。

## 10. 零膨脹定案提案（2026-09-20，Hetzner 重跑 `4f2f378` 取得零比例後）

`HETZNER_CALIB_REUSE_DUMP=1` 沿用既有 x dump 重跑，報告 `generated_at 2026-09-20T02:30:58Z` 已帶 `z_zero` 等五欄。

**零膨脹只有一個指標家族，且門檻沒有模糊地帶**（主對話實算，212 個有 z 的鍵）：

| z_zero | 鍵 |
|---|---|
| 85.10～90.83% | `trust_strength_long/short` **tpex 6 鍵**（＝報告的 `degenerate`，p85＝0） |
| 58.79～71.21% | `trust_strength_long/short` **twse 6 鍵**（p85 僅 0.86～1.29 ⇒ d 0.29～0.43，近二值） |
| **← 斷層 →** | |
| 42.33% 以下 | 其餘 200 鍵（次高者 `short_sale_change` tpex short 42.33%） |

**建議（待裁定）：`z_zero ≥ 50%` 的鍵改以「非零樣本的 p85 ÷ 3」定 d**，恰好涵蓋這 12 個 `trust_strength` 鍵、不多不少。

理由：
1. **恰好達成規格意圖**。規格 `d = p85/3` 的設計意圖是「約 15% 落在截斷區」。零膨脹下 85% 樣本恰在 c 上，規則退化（tpex 直接 d＝0）。
   改用非零母體後，**實測非零樣本的截斷比例恰為 15.00%**（`clip_nonzero_pct ÷ (1−z_zero)`，四鍵逐一驗算皆 15.00%）——
   規格的意圖原封不動地套在**有資訊量的樣本**上。
2. **結果落在合理範圍**：`d_nonzero` 為 1.34～3.29，是設計起點值的 **0.486～0.785 倍**（2026-09-20 驗收實算更正，原寫 0.55～0.67 兩端都錯）；對照原 p85 規則在 twse 給出的 0.29（起點值的 1/17，
   投信淨買超只要達成交量 0.86% 分數就打滿）。
3. **閘門全過**：`clip_nonzero_pct` 1.38～6.18%，遠低於 15%。
4. **兩市場一致處理**：只修 tpex 的 6 個退化鍵會讓 twse 留著近二值的 d，機制相同卻兩套處理，說不通。

**規格偏離（要寫進登錄書）**：`P1-B1-market.md:42` 的母體定死為「訓練段樣本」，本例改為「訓練段的非零樣本」，屬**第三種例外**
（前兩種是 `:48` 持續性、`:49` 距離型）。規格 `:47` 的政策「未被點名的同類缺陷都會被閘門攔下」在此**不適用**——
零膨脹不會讓閘門超標（d 太小反而截斷更少），是**閘門攔不到的缺陷類型**，這點要在登錄書明說。

## 11. 確認的計分缺陷：跨市場轉市污染個股 ring 的指數欄（凍結前必須修）

**現象**：`excess_long/short`／`excess_accel`／`industry_relative_return` 在 twse 出現 −8,613～−9,741 pp，而報酬率結構下界是 −100%。

**根因（主對話讀碼確認）**：`src/iching/replay_state.py:357` 逐日問 `m = self.pool.listed(sid, T)` 取該檔**當日所屬市場**，
`:370` 把 `idx_close[m]` 推進**同一個 ring**，而 **ring 在轉市時不重置**。於是同一個視窗內，分子是轉市後的加權指數（約 4.5 萬點）、
分母是轉市前的櫃買指數（約 380 點），`_pct_ret(index_close, n)` 得出 +11,000 pp 的假指數報酬，超額報酬因此變成 −11,000 pp。

**四條互相印證的證據（子代理，主對話抽驗）**：①只有 twse 越界，tpex 六鍵全乾淨；②四個受害指標**全部**呼叫 `_pct_ret(index_close, n)`，
唯一不受害的 `excess_vs_industry` 正是指數項會對消的那一個；③`market_index` scope 的指數指標完全乾淨 ⇒ 壞的不是指數序列本身；
④離線複現：bundle 期間「指數欄單日跳 >5 倍」的**恰 3 檔**，全部對得上 `PitPool` 的轉市日（5236／6589／6423），
最極端 10 筆的 `idx[T−n]` 全是三位數（櫃買）、`idx[T]` 全是五位數（加權），**而個股報酬本身正常**（−11～+17 pp）。
**校準期恰 4 筆轉市、方向全是 tpex→twse**（6438／6426／3092／3652），正是「只有 twse 越界」的直接解釋。

**線上正在發生**（主對話實查 `data/scores/2026-09-18.json`）：5236（2026-07-19 tpex→twse）的 `line_3` 中期 **35.58**、波段 32.50，
低於橫斷面中位數 41.84／42.87；而短線 **47.50** 高於中位數——**短線視窗（n=5/10）沒跨過轉市日，正好是乾淨的那一組**。

**影響與處置**：
- **不影響 d、不擋凍結數值**：污染 ≤0.04% 且全落在最極端處，p85 對它免疫（子代理實測剔除後 p85 差 <1e-4）。
- **但它改變個股分數與排序**，屬**輸入建構缺陷、不動任何 `Param` ⇒ 不改 `params_sha`**，只需重算分數。
- **順序**（本檔 §2 施工序列據此修正）：**先修 `replay_state` 的跨市場污染 → 再套用 d → 一次全量重播 → 才凍結、才動用保留段**。
  理由：保留段一經動用即消耗（`v1.2.2:718`），而修不修會改變保留段裡受影響個股的分數；d 在兩種情況下相同，
  所以**凍結時程不必為此往後推**，但重播必須排在修正之後（反正校準本來就要重播一次，合併做零額外成本）。
- **順帶（同批或另案）**：`revenue_yoy_3m`（`stock.py:101`）只擋 `den == 0`，近零／負的去年同期合計無守門，
  造成 `revenue_yoy` 出現 ±300～760 萬 pp；`ind_net_strength`（`stock.py:546`）的 `den == 0` 守門因入池條件已保證成交量 >0 而**永不觸發**，缺近零守門。

**一則過程紀錄（誠實原則）**：同一個子代理的**第一版報告把根因判成「6684 面額變更造成還原基準不一致」並引用了一筆
`data/factors.json` 的資料列——主對話實查該檔，6684 只有 `2022-07-20` 與 `2023-07-25` 兩筆，它引用的那一筆不存在**。
第二版自行推翻並給出可複現的正確根因。這是「subagent 回報不等於事實、要核原始證據」的又一個實例。

## 12. 裁定 #56（2026-09-20，使用者：「同意」）：零膨脹鍵改用非零樣本 p85

**規則**：`z_zero ≥ 0.50` 的鍵，`d = (非零樣本的 |x−c| p85) ÷ 3`。訓練段實測恰涵蓋 12 個 `trust_strength_long/short` 鍵
（tpex 6＋twse 6），不多不少；次高的非成員是 `short_sale_change` tpex short 的 42.33%，斷層明確。

| 市場 | 鍵 | z_zero | 起點 d | 採用 d（`d_nonzero`） | 全體樣本截斷 |
|---|---|---:|---:|---:|---:|
| tpex | `short｜trust_strength_short` | 90.83% | 5.00 | **3.289** | 1.38% |
| tpex | `short｜trust_strength_long`／`swing｜..._short` | 89.35% | 4.00 | **2.686** | 1.60% |
| tpex | `mid｜..._short`／`swing｜..._long` | 87.26% | 3.00 | **2.047** | 1.91% |
| tpex | `mid｜trust_strength_long` | 85.10% | 2.00 | **1.570** | 2.24% |
| twse | `short｜trust_strength_short` | 71.21% | 5.00 | **2.430** | 4.32% |
| twse | `short｜..._long`／`swing｜..._short` | 67.78% | 4.00 | **2.066** | 4.83% |
| twse | `mid｜..._short`／`swing｜..._long` | 63.31% | 3.00 | **1.664** | 5.50% |
| twse | `mid｜trust_strength_long` | 58.79% | 2.00 | **1.344** | 6.18% |

**非零母體的截斷比例實測恰為 15.00%**（`clip_nonzero_pct ÷ (1−z_zero)`，逐鍵驗算），即規格「約 15% 落在截斷區」的意圖
原封不動套在有資訊量的樣本上。採用 d 是起點值的 **0.486～0.785 倍**（2026-09-20 驗收實算更正，原寫 0.55～0.67 兩端都錯）（對照原 p85 規則在 twse 給出 0.29，是起點值的 1/17）。

**登錄書要寫的規格偏離**：`P1-B1-market.md:42` 的母體由「訓練段樣本」改為「訓練段的**非零**樣本」，是繼 `:48` 持續性、
`:49` 距離型之後的**第三種例外**。`:47` 的政策「未被點名的同類缺陷都會被閘門攔下」對本類**不適用**——零膨脹使 d 偏小、
截斷反而更少，**閘門攔不到**，這點必須明寫。

## 13. `replay_state` 跨市場污染的修法（設計，動手前）

§11 已確認根因。三種修法評估：

| 修法 | 問題 |
|---|---|
| 轉市時重置整個 ring | 連該檔**自己的價格歷史**一起丟掉，而價格序列跨轉市是連續的（同一檔、同一組價格），代價無謂 |
| 讀取時以「當前市場指數」重算整個視窗 | 等於假設該檔轉市前就在新市場，是編造的反事實；且指數序列要另外傳進來 |
| **轉市時把 ring 內既有列的指數欄清成 NaN**（採用） | 只讓**指數相關**指標在跨轉市的視窗回 `Missing`，該檔自身價格、量、籌碼等欄完全不動 |

**採用理由**：「個股報酬 − 所屬市場指數報酬」在視窗跨越轉市時**本來就沒有定義**（所屬市場不唯一），
誠實的答案是回 `Missing`、讓既有的 coverage／重配權重機制處理，而不是生一個數字出來。
清 NaN 使 `_pct_ret` 的既有缺值路徑自然接手，不需新欄位、不動 `Ring` 結構、不影響 `state.json` 序列化。

**驗收條件（綁 commit）**：
- **R1 只有受影響的檔會變**：對 `runs/collect` 建的世界跑修改前後，**除轉市檔外每一檔每一日的分數逐位相同**
  （bundle 期間的轉市檔恰 3 檔：5236／6589／6423，由 `PitPool` 轉市日驗證）。
- **R2 跨轉市視窗回 Missing**：受影響檔在轉市後 `< n` 個交易日內，`excess_long/short`／`excess_accel`／
  `industry_relative_return` 皆為 `Missing`（理由碼明確），而 `excess_vs_industry`（不吃指數）**不受影響**。
- **R3 覆蓋率連動**：該爻 `line_3_coverage_ratio < 1` 且 `line_3_reweighted = 1`，不是靜默補 0 也不是整爻 unknown。
- **R4 轉市後 ≥ n 日恢復正常**：視窗完全落在新市場後，指標恢復有值且量級正常（`|x| < 100`）。
- **R5 越界消失**：修改後對 bundle 全期間重算，`|excess| > 100` 的樣本數由 247 降為 **0**。
- **R6 不改 `params_sha`**：`build_params` 輸出逐位不變（本修法不碰任何 `Param`）。
- **R7 全量測試綠**、ruff 零新增項、fresh-context 驗收綁 PR head。

## 14. §13 修法的三項更正（2026-09-20，實作後主對話裁決）

**更正 1：§13「清 NaN 使 `_pct_ret` 的既有缺值路徑自然接手」是錯的，必須同時補讀取端守門。**
實測 `05f4120` 原碼：`_pct_ret` 只有 `if base == 0`，而 `nan == 0` 為**假**，於是 `nan/nan−1` 吐出 `nan`（一個 float），
經 `S_clip` 變成 `native=nan` 的 `Ind` ——**不是 `Missing`**。只清 NaN 不補守門，等於把「上萬 pp 的假值」換成
「`nan` 分數」，缺值機制完全接不到，**比原缺陷更糟**。故 `stock.py` 的 `_pct_ret` 補兩端點 `math.isfinite` 守門
（體例同既有的 `ind_margin_price_divergence` 的 "NaN in window"）。**兩道缺一不可**，突變實測：拿掉任一道各有 6 支測試變紅。
對既有結果零影響——修法前 `index_close` 結構上不可能為 NaN（`ingest` 只在 `m in idx_close` 時才推進），
480 份原料包 909,573 筆實際推進的個股列中 `close` 非數值者 **0 筆**。

**更正 2：R3 的「不是整爻 unknown」不成立，主對話裁決＝接受。**
實測是**三段階梯**（`ws`＝`excess_short` 視窗）：轉市後第 0～`ws−1` 日 `coverage_ratio=0.25`、
**`unknown=True`、`score=None`**（低於 `unknown_below=0.5`）；第 `ws`～`2ws−1` 日 0.75、`unknown=False`；第 `2ws` 日起 1.0。
**接受的理由**：三爻四分之三的權重在跨轉市時本來就沒有定義，而修法前那幾天是「滿覆蓋但填的是上萬 pp 的垃圾」、
分數被釘在地板。unknown 是誠實的答案，且既有機制照常運作（不補 0、`reweighted` 為真、卦名走「待補」）。
唯一的替代是回到「用當前市場指數重算整個視窗」那條編造反事實的路，§13 已否決。
**代價**：中期在轉市後約 20 個交易日該爻 unknown、再 20 日部分覆蓋；校準期 2.5 年只有 4 檔轉市，影響面極小。

**更正 3：R5「`|excess|>100` 由 247 降為 0」兩半都錯，改用下列指標。**
①247 這個數字我沒有量出來——最接近的 ingest-only 口徑實測是「消失 257 筆」，口徑略有出入；
②**「降為 0」在任何口徑下都不可能成立**：修法後仍有 10,938 筆 `|x|>100`，分布在 388 檔，最大 **616.2 pp**
（2026-06-17 `3026` 中期）——那是**真實的 60 日 +616% 漲幅**，不是污染。
**改用的驗收指標**：落在 3 檔轉市股的 `|x|>100` 樣本 **621 → 0**；`|x|>1000` 樣本 **445 → 0**；
全體 `max|x|` **12,011.9 → 616.2**；R1 逐位比對差異 **255 列且全部落在 3 檔轉市股**。
**一個重要的偵測面更正**：`6423` 是 twse→tpex（反方向），污染後的指數報酬約 **−99.2%**、超額約 `+99 + 個股報酬`，
修法前 `max|x|` 只有 155 ——**「`|excess|>100`」這個篩子抓不到它落在 100 以內的那些污染**。
**R1 的逐位比對才是完整的偵測面，R5 的門檻只能當輔助**。
**差異列數更正為 256**（驗收者以**全欄位**列雜湊重算）：多出來的那一列是 6589 波段第 21 天，分數已恢復相同、
只差 `line_states` 與 `streaks`——遲滯狀態機比分數多帶一天的尾巴。原寫 255 是比對欄位集較小所致。

**更正 4（我的事實錯誤）**：§13 派工說 `runs/collect` 有「1,640 包」——實查 main 上是 **480 份** `*-daily.json.gz`
（2024-09-27～2026-09-18）；1,632 份那個數字是 `hetzner/adj-2026-09-14` 分支上的，第四次覆蓋刻意沒把 `runs/collect` 併進 main。

**範圍外、已查證未動**：`scan.py` 的 features 層**沒有同型缺陷**——個股收盤歷史以代號為 key（跨轉市連續）、
指數 deque 每市場一條、超額的分子分母同屬當日那個市場（`scan.py:426`／`:500`／`:509-514`）。
`revenue_yoy_3m` 的近零／負分母與 `ind_net_strength` 的 `den == 0` 永不觸發，屬另案。


## 15. 第 4 步「校準後全量重播」的一句話貼（2026-09-20 寫成，動手前）

PR #50 合併後 `params_sha` 已變，`cache/scores.db`／`data/scores`／`data/state/cross.json`／`data/backtest`
全部作廢，下一步是在 Hetzner 跑全量重播。**動手前先確認三件事**（皆已實查）：

1. **`scan_features` 不必重跑，`cache/features.db` 原地沿用。** 特徵層的參數指紋
   （`scripts/scan_features.py:92-99` 的 `build_params`）只含 `ma_windows`／`hl_windows`／`ret_windows`／
   `p_cs_windows`／`p_cs_tie`／`adv_window`／`adv_threshold`／`pool_semantics`，**不含 `model_version`**；
   而 scan 路徑對計分層的唯一依賴是 `src/iching/scan.py:104` 的 `from .score.params import Rules`，
   本輪 `class Rules` 區塊在 `c1fc988`→`7c1103a` 之間**逐字未動**（實測兩版該區塊字串相等；
   長度值依抽取邊界而異，不同量法會得到不同數字，**相等**才是主張本身）。
   跨市場修正落在 `replay_state`／`score.stock`，那是特徵層的下游。
2. **`--resume` 這次一定失敗、只能 `--rebuild`。** `cache/scores.db.state.json` 帶的是舊 `params_sha`，
   `run_common.check_snapshot_meta`（`src/iching/run_common.py:52-59`）會拒；`replay_scores.py:138`／`:152` 呼叫它。
   （但**這一輪自己**的重播一旦開跑，寫出的快照就是新指紋，所以中斷後續跑走 `--resume` 是對的。）
3. **`hetzner_adj.sh` 守門 a 要的那行標記，repo 裡沒有任何東西會印。** 該守門
   （`scripts/hetzner_adj.sh:70-77`）要求重播 log 末行含 `== replay exit 0`，但全 repo grep
   `== replay exit` 只命中消費端（`hetzner_adj.sh`）、測試與文件，**沒有產生端**——上一輪是人手打的
   tmux 一句話，原文沒進版控。

### 因此補一支 `scripts/hetzner_replay.sh`（本節的交付物）

手打那一句有兩個已知會付出 12 小時代價的坑：①`--window` 靠人輸入，打錯要到
`hetzner_adj.sh:123` 比對 `cross.json` 時才擋得下來；②`echo "== replay exit $?"` 若漏在 `tee` 之外，
log 裡就沒有標記、守門 a 永遠不過。腳本把這兩件事變成程式的責任。

**驗收條件（先寫，改的人不得自驗；驗收綁確切 commit）**

| # | 條件 | 怎麼驗 |
|---|------|--------|
| V1 | `--window` 取自 `data/state/cross.json` 的 `meta.window`，與 `hetzner_adj.sh:121` **同一路徑同一算法** | 讀碼比對兩行；守門 c 因此結構上必過 |
| V2 | log 末行（去空白行）恰為 `== replay exit <rc>`，rc ＝ `replay_scores` 的真實退出碼 | 以假 `replay_scores`（成功／失敗各一）實跑，再把 log 餵進 `hetzner_adj.sh` 守門 a 的同一段 `case` 比對 |
| V3 | **上一輪的舊 log 不得被誤當成這一輪的結果**：開跑前既有 log 改名為 `<log>.prev-<UTC>` | 先放一份末行為 `== replay exit 0` 的舊 log，再讓本次失敗，驗守門 a 讀到的是 `exit 1` |
| V4 | 中斷後重貼同一行走 `--resume` 而非從頭 `--rebuild`；標記檔綁 **`model_version` 指紋**（不是 `params_sha`——後者由前者加 window 等導出，兩者 1:1 相關但不是同一個字串），**不同指紋不得沿用** | 標記檔存在／不存在／內容為別的 sha 三種情形各跑一次，比對實際傳給 `replay_scores` 的旗標 |
| V5 | 開跑前守門：工作樹不乾淨／`POOL_SEMANTICS` 非 `pit-1`／`features.db` 不存在／`cross.json` 取不到 window，四者任一即 rc 2 **且不呼叫 `replay_scores`** | 四種情形各跑一次，斷言 rc＝2 且假 `replay_scores` 的呼叫紀錄為空 |
| V6 | 同步 main 後 HEAD 前進即改用新版重新執行（同另三支的自我複製骨架） | 沿用 `tests/test_pit_world.py` 既有的 v1／v2 臨時 origin 手法 |
| V7 | 既有全量測試維持綠、`ruff` 零新增項 | `python -m pytest tests/ -q`（本容器預設 `python` 是 3.11，而 repo 需要 3.12——`src/iching/daily_pipeline.py:321` 用了 3.12 才合法的巢狀引號 f-string，要另建 3.12 venv）；ruff 比對**對 parent 連行號都相同**，對 `c1fc988` 要先去掉行號（PR #50 動過 `score/stock.py` 造成位移） |

**不在本節範圍**：`hetzner_adj.sh` 一個字都不動（它的分支名 `hetzner/adj-<TO>` 與 commit 路徑清單
被 `tests/test_pit_world.py:404`／`:473` 釘住）。校準這一輪仍沿用 `hetzner/adj-<TO>` 分支名——
名字不準確，但改它要同步動測試與文件，屬另一批。
