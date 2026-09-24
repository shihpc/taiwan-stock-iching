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
| 2 | **子指標原始值 x** | **無任何落地路徑**：`scores` 表 48 欄（§18 前為 45）只有爻分與旗標；`features.db` 六表是廣度／產業聚合／`p_cs`；x 只在 `Ind`／`SubResult` 記憶體物件 | `scores_io.py:39-62`、`features_io.py:63-109`、`transform.py:59-62`、`aggregate.py:18-26` |
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
5. **排序表**：`scripts/rank_table.py` 讀校準後 `train_*.csv.gz`，依 §5 Q6 格式與成本模型（套用方式見 §16 裁定 #57）算分組報酬，寫 `docs/pre-registration.md` §1.6 附錄＋`data/rank_table.json`。
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
- **Q9 成本怎麼扣**（Q6 只給了費率、沒說套用方式）：(a) 算術 `fwd_ret − 0.985%` (b) **乘法，買賣兩端分開**【建議】。
- **Q10 排名 1～64 依均值還是中位數**（Q6 兩個都要列，但沒說排名依哪一個——這是規格本身的縫）。
- **Q11 漲停買不到／跌停賣不掉的列怎麼算**：資料集有 `entry_limit_up`／`exit_limit_down` 兩旗標與 `exit_reason`。
- **Q12 滑價要不要按流動性分層**（0.2% 對冷門股明顯低估）。

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


## 16. 裁定 #57（2026-09-21，使用者：「全照建議」）：卦別排序表的成本套用方式（Q9～Q12）

**先更正一則主對話的錯誤敘述**：先前對使用者說「排序表還缺一個成本模型要先問你」是**錯的**，成本模型在裁定 #54 Q6
就定了（手續費 0.1425%×2 ＋ 證交稅 0.3% ＋ 滑價 0.2%×2，`:162`／`:171`），我沒查就順口說的。本節定的是**套用方式**，
不是費率本身；費率一個字都沒改。兩個常見分歧點也不存在：池子**不含 ETF**（`src/iching/universe.py:41` 依
`spec/P1-B1-market.md:157` 排除 ETF／權證／DR／特別股／興櫃）故不必處理 0.1% 稅率；手續費採**未打折公定價**，偏保守。

前提：`fwd_ret` ＝ `adj_close(T+1+h) / adj_open(T+1) − 1`（`docs/P3-DATASET.md:45`）——**隔日開盤進、第 h 日收盤出**。

### Q9 成本用乘法扣、買賣兩端分開

令 `f=0.001425`（手續費）、`t=0.003`（證交稅，只在賣出）、`s=0.002`（滑價，單邊）：

```
net_ret = (1 + fwd_ret) × (1−s)(1−f−t) / [(1+s)(1+f)] − 1
```

**理由**：成本是比例不是固定值，而 mid horizon 實測 `max|fwd_ret|` 達 **3.26（＋326%）**，算術扣除在大報酬上失真——
實算：`fwd_ret=3.26` 時乘法得 3.218208、算術得 3.250150，**差 3.19 個百分點**；`fwd_ret=0.05` 時只差 0.045 個百分點。
`fwd_ret=0` 時本式為 **−0.981%**，與 Q6 那個 0.985% 的一階估算差 0.004 個百分點（二階項）——**是同一個成本模型的精確化，
不是改口徑**，排序表要把這 0.981% 明寫出來以免日後被當成兩套數字。

### Q10 排名依**均值**，中位數並列

均值才是等權持有真正拿到的報酬；中位數同列作穩健性對照。**均值與中位數方向不一致（一正一負）的卦另標示、
不進高／低組**（與 `n<500` 歸中組是兩條獨立規則，標示要分得開）。

### Q11 進場與出場不對稱處理

- `entry_limit_up=true` → **整列排除**：那筆本來就買不到，計入等於把拿不到的報酬算進去。
- `exit_limit_down=true`、`exit_reason` 為 `halt`／`delist` → **保留**：賣不掉、停牌、下市都是真實損失，排除等於美化。
- `fwd_ret=null`（視窗超出資料末日）→ 不計入 n（同 Q6「空 `king_wen` 不計」的立場）。

排除與保留的列數都要寫進表下的揭露行，**不得只給淨報酬不給母體**。

### Q12 滑價維持一律 0.2%、不分層

在表下註明「對低流動性個股偏樂觀」。分層等於引入一組沒有回測依據的新參數（鐵律 8），這裡刻意不做。

### 驗收條件（寫在 `scripts/rank_table.py` 動手前）

| # | 條件 | 怎麼驗 |
|---|------|--------|
| R1 | `net_ret` 的算式逐位＝上面那條乘法式 | 抽 20 列以 `fwd_ret` 手算對比；另驗 `fwd_ret=0` 時恰為 −0.00981…（釘住常數） |
| R2 | `entry_limit_up` 列排除、`exit_limit_down`／`halt`／`delist` 列保留、`fwd_ret=null` 不計入 n | 以合成資料各造一列，斷言 n 與均值的變化方向 |
| R3 | 排名依均值；均值與中位數異號的卦不進高／低組且有標示 | 合成一個「均值正、中位數負」的卦，斷言它落在中組並帶標示 |
| R4 | `n<500` 歸中組並標示，與 R3 的標示**可分辨** | 兩種情形各造一卦，斷言兩個標示欄位不同 |
| R5 | 只讀訓練段 `train_*.csv.gz`，**驗證段一列都不讀** | 測試注入一份驗證段檔，斷言未被開啟 |
| R6 | 表下揭露行含：排除列數、保留列數、成本率（0.981%）、滑價的偏樂觀註記 | 字串斷言 |


## 17. 換月日全市場上爻降級的根治：coverage 分母排除「結構上不可得」的族（2026-09-21，使用者裁定「改 coverage 分母（根治）」）

### 現象與根因（皆為實查，非推測）

`data/scores/2026-09-17.json` **全部 5,838 列** `coverage` 都是 `reweighted`（09-16 為 855／5,844、09-18 為 847／5,841）。
逐爻拆解：異常集中在**上爻**（09-17 有 5,832 列 reweighted，鄰日只有 6～8 列）。再上一層，大盤六列的
`line_5_coverage_ratio` 在 09-17 是 **0.4 且 `unknown=True`**，鄰日是 **0.7 且 `reweighted=True`**。

兩個缺口疊加而成，**單獨任一個都不會出事**：

| 缺口 | 性質 | 證據 |
|---|---|---|
| 族 C `vix_phist_rev`（權重 .30）**長期缺席** | FinMind `TaiwanOptionVix` 上游只有 2026-03-02 起，視窗內 140 筆 < `P_hist` 要的 250 筆 → `insufficient_history` | `docs/BACKFILL-RUNBOOK.md:328`／`:344`（裁定 #26）；`runs/calib/d_report_2023-06-30.txt` 該鍵 6 組 `n=0` 標 `not_applicable`；`docs/P2-KICKOFF.md:104` 觀察名單第 ① 條 |
| 族 B `basis`（權重 .30）**換月日降級** | `src/iching/score/market.py:327-328` 第一行即 `if contract_rolled: return Missing(REASON_CONTRACT_ROLLED, "換月日")`，是規格 `spec/P1-B1-market.md:239` 明文要求 | TX 近月由 `202609`→`202610`，`iching/futures.py:39-42 third_wednesday` 實算最後交易日＝2026-09-16 |

族權重 `A .40／B .30／C .30`（`src/iching/score/params.py:386`），`unknown_below = 0.5`（`:71`）：
平時 ratio ＝ (A+B)/1.0 ＝ **0.7**；換月日只剩 A ＝ **0.4 < 0.5 → 整爻未知**。
五爻未知 → `direction_score` 依 `direction_unknown_policy="missing"` 整個 Missing（`aggregate.py:99-107`）→
個股上爻族 A（沿用所屬市場的大盤方向分數，權重 .50）缺 → **全市場個股上爻 coverage 0.5、被重配**。

**這是每月一次的結構事件，不是偶發**。回測資料集實測「全列 reweighted」的日子：
**train 30／603 日（5.0%）、valid 18／368 日（4.9%）**，日期即每月換月日（2021-01-21、02-18、03-18…）。
失真那天還會**貢獻一天遲滯確認**：09-18 上爻翻面的 176 筆，`streaks` 顯示 176／176 在 09-17 的上爻 streak 都是 1。

**先前敘述的更正**：主對話一度說「有一個子指標長期缺席但沒有人發現」——**錯的**，族 C 缺席早有裁定 #26
與觀察名單記載。沒被寫下來的是**複合效應**（C 長期缺席使每月換月日把整個五爻打成未知）。

### 修法（兩條規則，窄）

`src/iching/score/aggregate.py` 的 `line_score()` 目前 `expected = sum(weights.values())`。改為：

1. **只排除 `missing.reason == REASON_INSUFFICIENT` 的族**。`insufficient_history`＝「還沒累積到能算」，
   與 `contract_rolled`（當日刻意降級）、`missing`／`denominator_zero`（當日真的沒有）性質不同。
   **`contract_rolled` 仍計入分母**——規格 `:239` 要求換月日族 B 降級，那個意圖必須保留。
2. **排除後剩餘的宣告權重必須 ≥ `unknown_below`(0.5) 才排除**，否則維持原分母。
   理由：重播暖機期有大量族是 `insufficient_history`，無條件排除會讓「第 5 天只剩一族」也變成 ratio 1.0、
   憑極少證據吐分數。0.5 **不是新參數**，就是規格既有的 `unknown_below`。

**`reweighted` 旗標維持 True**：族真的缺席了，那個訊號不該被本次修改洗掉。`ratio=1.0` 配 `reweighted=True`
不矛盾——前者是「在調整後分母下拿到多少」，後者是「有族缺席」。

**分數值只在跨越 `unknown_below` 時才改變**：`line_score` 的分數是 `sum(fam.score*w)/got`，`got` 不受本次修改
影響，所以未跨越門檻的爻**逐位不變**。這是本修法可被精確驗證的關鍵性質。
**但「逐位不變」是分數值的性質、不是產物的性質**（驗收補記）：未跨門檻的爻 `coverage_ratio` 本來就會由 0.7 變 1.0
（那正是 C5 的內容），所以 `scores.db`／`data/scores/*.json` 的 `line_N_coverage_ratio` 欄**會變**，產物並非逐位相同。
指紋已變、舊 db 本來就作廢，實務無影響，但敘述不可被讀成「產物不變」。

**進指紋**：由 `Rules` 新欄位控制（比照 `family_missing_policy`／`direction_unknown_policy`），
因此 `model_version`／`params_sha` 必變、舊 `scores.db` 作廢——這是預期，本來就要重播。

### 驗收條件（先寫，改的人不得自驗；驗收綁確切 commit）

| # | 條件 | 怎麼驗 |
|---|------|--------|
| C1 | 只有 `insufficient_history` 被排除；`contract_rolled`／`missing`／`denominator_zero` 一律仍計入分母 | 四種 reason 各造一個族，斷言 `expected` 的變化 |
| C2 | 剩餘宣告權重 < 0.5 時**不排除**（暖機保護） | 造「A .40 可得、B/C 皆 insufficient」→ 斷言 ratio 仍為 0.4、`unknown=True` |
| C3 | 未跨越 `unknown_below` 的爻，分數**逐位不變** | 以合成族組合對跑新舊 `line_score`，斷言 `score` 相等、只有 `ratio` 改變 |
| C4 | 五爻換月日情境：ratio 0.4 → **0.571**、`unknown` False、分數＝族 A 之值 | 直接以 A .40 present／B contract_rolled／C insufficient 構造 |
| C5 | 平時情境：ratio 0.7 → **1.0**、`reweighted` 仍為 True、分數逐位不變 | 同上，B present |
| C6 | 旗標關閉（舊行為）時，`line_score` 輸出與改動前**逐位相同** | 以隨機族組合 1,000 組對跑，斷言全等 |
| C7 | `model_version`／`params_sha` 確實改變，且新值可由原始碼重算重現 | `build_params(m).model_version()` 與 `params_fingerprint` 實算 |
| C8 | 全量重播後：train／valid 的「全列 reweighted 日」由 30／18 降為 **0**；其餘日子的 reweighted 比例分布不得大幅位移 | 對跑新舊 `data/backtest/*_mid.csv.gz` 逐日統計 |
| C9 | 既有全量測試維持綠；`spec/tools` 四支機檢（`check_dims`／`tblcheck` 八個 md／`inject_test`／`gen_b5` sha256）維持綠 | 實跑 |

### 規格同步（必做，否則程式與正本不一致）

`spec/P1-B1-market.md` §B1.7 的 `coverage_ratio` 定義要加註本裁定（已做，`:315-326`）。
**`spec/stock-iching-plan-v1.2.2.md` 無對應條文、不需同步**（驗收實查：全檔 grep `coverage_ratio`／`unknown_below`／
`應有權重` 零命中；最接近的 `:370`「必要資料缺 → 該爻未知……權重重配規則版本化」夠概括，未因本次修改變假，
而且「規則版本化」正好被「旗標進指紋」兌現）。本句原寫「兩份都要改」，是動手前未查證就列的待辦。
`docs/P2-KICKOFF.md:104` 的「大盤暖機後『任一爻未知』全期 0」與實測矛盾（1,632 日中 117 日五爻未知，
逐年 2021~2025 各 12 次＝每月換月日），**該句需查證後更正**——列為本批的附帶項，不得因為「不是我寫的」而略過。

### 對 §16.5 `:717` 的影響（順序不可顛倒）

`:717` 是「比對**縮放**前後」的八項。本修法是**語意變更**不是縮放，兩者混在一起會讓 `:717` 的差異無法歸因。
故順序固定為：**先完成本節修法與重播 → 再以新基準做 `:717` 的兩次重播對跑**。


## 18. `:717` ⑧③ 的中間量出口（2026-09-21 寫成，動手前）

§16.5 `:717` 的八項裡有兩項在**任何產物裡都算不出來**：

- **⑧ 封頂／下限的 binding 率**——`max(base, 84.16)`（初爻族 A 近 12 月創高的下限，`src/iching/score/stock.py:180`／`:193-194`）
  與封頂 `79.89`（三爻過熱，`:367`／`:370-371`）。規格原文：「**不是尺度換算而是實質改門檻**；③只涵蓋旗標，
  創高下限原本沒有任何一項在看」。
- **③ 的個股側**——`overheated`（`:369`）。`scores_io.py` 的 `SCALAR_COLS` 裡沒有它，大盤 `flags` 也只有大盤列有值。

**但這三個值早就算好了**，只是沒落地：`floor_applied` 在 `line1` 的 `famA.meta`（`:194`），
`overheated`／`overheat_cap_applied` 在 `line3` 的 `lr.meta`（`:368`／`:371`）。
所以**不需要新寫 `--dump-x` 那種出口**，把既有 meta 持久化即可——這也讓 binding 率日後可持續監看，
不是為了這一次分析而生的拋棄式程式碼。

### 設計

1. `line1_operations` 把 `floor_applied` 一併放進該爻的 `lr.meta`（現在只在 `famA.meta`，非 detail 模式取不到）。
2. `assemble_row` 在逐爻迴圈之後加三個**列級**純量鍵：`floor_applied`／`overheated`／`overheat_cap_applied`，
   值取自 `scores.lines[1].meta`／`scores.lines[3].meta`；**大盤列一律 `None`**（這三個是個股專屬）。
   沿用 `_line_payload` 既有模式——逐爻四個鍵本來就不在 `dimensions.json` 宣告名單裡、由程式直接 `row.update()` 加，
   故**不必動 `spec/dimensions.json`**。
3. `scores_io.SCALAR_COLS` 加這三欄，`SCHEMA_VERSION` 1 → 2（舊 db 會被拒、強制重建——本來就要重播）。

**不進 `params_sha`**：這是輸出欄位不是計分規則，分數逐位不變。**這一點必須被證明**，見 D3。

### 驗收條件（先寫，改的人不得自驗）

| # | 條件 | 怎麼驗 |
|---|------|--------|
| D1 | 三欄值與 meta 逐列一致；大盤列一律 `None` | 合成個股情境（含下限生效／封頂生效／皆不生效三種）逐列比對 |
| D2 | `floor_applied` 只有在 `max()` **真的改變了值**時才為 True（`famA.score < 84.16` 且創高為真）；封頂同理 | 造「創高為真但分數已高於下限」的案例，斷言 False |
| D3 | **分數逐位不變、`params_sha`／`model_version` 不變** | 以本批前後的 `build_params` 算指紋斷言相等；再以合成情境對跑全部六爻分數斷言 `.hex()` 相同 |
| D4 | `SCHEMA_VERSION` 由 1 變 2，舊 db 被明確拒絕（不是靜默沿用） | 以舊 schema 的臨時 db 實跑，斷言拋錯且訊息指名 schema |
| D5 | `data/scores/*.json` 的列帶得出這三欄（匯出路徑沒漏） | `export_scores` 對合成 db 實跑後讀回 |
| D6 | 既有全量測試綠、`spec/tools` 四支綠、ruff 零新增項 | 實跑 |


## 19. `:717` 前側怎麼跑：`build_params(calibrated=False)`（2026-09-21 寫成，動手前）

`:717` 要比對**縮放前後**，縮放＝d 校準。前側不能用「checkout 校準前的 commit」來跑——那份沒有 §17
（coverage 分母）與 §18（三個出口欄），差異會混進語意變更與欄位差，那八項就無法歸因。
**前側必須是「舊 d ＋ §17 ＋ §18」**。

### 設計（call site 零改動）

`cal_d(market, scope, indicator_id, horizon, default)` 的既有語意就是「查不到鍵 → 回退設計起點值
`default`」（`src/iching/score/params.py:264-270`），而**起點值就是校準前的 d**。33 個呼叫點全部在
`_mk_market`／`_mk_stock` 內，所以只要在那兩個函式開頭做一次**區域名稱遮蔽**：

```python
cal_d = _CAL_D if calibrated else start_d      # start_d 一律回 default
```

Python 會把整個函式裡的 `cal_d` 當區域名稱，33 個呼叫點**一個字都不用改**。三個查表
（`distance_d`／`market_slope_d`／`stock_slope_d`）在 `build_params` 內以同樣方式退回 `*_START`。
`ParamSet.calibrated` 在該模式為 `False`。

`replay_scores.py` 加 `--uncalibrated` 旗標串到 `build_params`。**它必然改變 `model_version` 與
`params_sha`**（d 進指紋），所以那份 db 與生產 db **結構上不可能混用**——既有的指紋守門會擋。

### 這份產物不是生產資料（寫死在三個地方，避免日後被誤用）

分支名 `hetzner/t717-before-<TO>`、腳本檔頭、以及 `--uncalibrated` 的 help 字串都要明寫
「只供 `:717` 前側統計，不得匯出成 `data/scores`／`data/backtest`／種子」。

### 驗收條件（先寫，改的人不得自驗）

| # | 條件 | 怎麼驗 |
|---|------|--------|
| E1 | `build_params(m, calibrated=False)` 的每個 `Param.d`，與**校準前的 commit**（`c1fc988`，PR #50 的 parent）`build_params(m)` 的同鍵 d **逐位相同** | `git show c1fc988:` 取舊版 `params.py` 當獨立模組載入，逐鍵 `.hex()` 比；兩市場都比 |
| E2 | 預設（`calibrated=True`）行為**逐位不變**：`model_version` 仍為 twse `0bb386e9cf3b`／tpex `8eb4f29fec3a` | 由原始碼重算 |
| E3 | `calibrated=False` 時 `ParamSet.calibrated is False`，且 `model_version`／`params_sha` 與預設**不同** | 實算 |
| E4 | 三個查表在 `calibrated=False` 時逐格等於 `DISTANCE_D_START`／`MARKET_SLOPE_D_START`／`STOCK_SLOPE_D_START` | 逐格比 |
| E5 | `replay_scores.py --uncalibrated` 寫出的 db，其 `replay_meta.params_sha` 與生產不同；且**現有指紋守門會拒絕把它當生產 db 用**（`export_dataset.check_params` 會報不符） | 以小型合成 db 實跑 |
| E6 | 全量測試綠、`spec/tools` 四支綠、ruff 對 `origin/main` 零新增項 | 實跑（ruff 用 worktree 比，不得用 `git stash`） |

**E1 是最關鍵的一條**：它證明「不校準模式」吐出的真的是校準前那組 d，而不是我另外編了一組數字。


## 20. `:717` 八項的分析工具（2026-09-22 寫成，動手前）

前側（舊 d）與後側（新 d）兩份 `scores.db` 都在 Hetzner；本節的工具讀這兩份、產出八項差異報告。
兩側都帶 §17（coverage 分母）與 §18（三個出口欄），所以差異**只歸因於 d 縮放**。

### 裁定 #58（2026-09-22，使用者：「寫 direction="n/a" 並註明原因」）

規格 `:717` 要求八項均按 `market × horizon × direction` 分組（`spec/dimensions.json` 的
`threshold_revalidation` 宣告 **12 筆**）。但實作裡 **`direction` 只存在於旗標解析層**
（`src/iching/score/market.py:595` 的 `by_direction`，產出逐方向的旗標成立與否、門檻位移、名額乘數），
**沒有任何「做空分數」**——`dimensions.json` 的 `direction` note 寫「做空分數獨立計算、非 100 減做多」，
但那個分數在 P2 從未實作。因此：

| 項目 | 有 direction？ | 資料來源 |
|---|---|---|
| ③ 的**大盤五支旗標** | **有** | 大盤列 `flags.by_direction[dir].active[flag]` |
| ⑦ 候選名單與排名重疊率 | **有** | `base_score`／`in_rank_pool` ＋ 大盤 `by_direction` 的 `quota_multiplier` |
| ③ 的**個股 `overheated`** | **無** | §18 的 `overheated` 欄。**2026-09-22 更正**：原本把它併進上面「③ 有 direction」那一列，是錯的——`overheated` 是個股層的單一旗標，`flags.by_direction` 只存在於大盤列，個股列的 `flags` 是 NULL。同段 `p_cs_long_excess` 的 `long` 指的是**長視窗**不是做多（`src/iching/scan.py:110`），不可當成方向維度的證據 |
| ①陰陽態 ②遲滯翻轉 ④動爻數 ⑤內外卦方向判定 ⑥主卦與之卦 ⑧binding 率 | **無** | 單一分數體系，多空共用 |

無 direction 的六項一律寫 `direction="n/a"`，並在報告與登錄書寫明
「**該項無方向維度，因為做空分數未實作**」。**不得為了湊格式把同一個數字複製成 long／short 兩列**——
那會讓讀的人以為是兩次獨立量測。

**連帶的規格正本問題（本節不做，列為凍結前待辦）**：`threshold_revalidation` 宣告 12 筆與上表不符。
**不可在 `direction` 維度加第三個值 `n/a`**——`threshold_T0_N0` 的 48 筆正是 `4×2×3×2`，加值會讓
`check_dims.py` 規則 5 的乘積驗算爆掉。正確做法是把 `threshold_revalidation` 拆成兩個標的
（③⑦ 走 `market × horizon × direction`＝12；其餘六項走 `market × horizon`＝6），
並重跑 `gen_b5.py`。那是動正本，另案。

### 八項的定義與資料來源（逐項寫死，避免實作時各自解讀）

| # | 名稱 | 量法 | 需要的欄位 |
|---|---|---|---|
| ① | 陰陽態逐日差異率 | 同 (date, stock_id, horizon) 下 `lines_formal` 的 6 個位元，逐爻比對前後側；差異率＝不同的爻數 ÷ 總爻數 | `lines_formal` |
| ② | 遲滯翻轉次數 | 逐 (stock_id, horizon, 爻位) 掃日期序列，計 `lines_formal` 位元改變的次數；前後側各一個總數 | `lines_formal`（逐日） |
| ③ | 各旗標觸發率 | 大盤五支旗標逐方向的 `active` 為真的日數 ÷ 總日數；個股 `overheated` 為 1 的列數 ÷ 可判定列數（**排除 None**） | `flags`、`overheated` |
| ④ | 動爻數分布 | 相鄰兩日 `lines_formal` 的位元差個數（0～6）的次數分布 | `lines_formal` |
| ⑤ | 內外卦方向判定差異率 | `inner/outer_trigram_score` 各自依 ≥55／≤45／其間 分三態，前後側比對差異率 | `inner_trigram_score`、`outer_trigram_score` |
| ⑥ | 主卦與**前瞻式**之卦一致率 | 主卦＝`king_wen`；之卦＝把**待確認動爻**（`streaks[i] >= CONFIRM_DAYS − 1`，即「再站穩一日就翻」；`CONFIRM_DAYS` 取自 `Rules()`，<2 直接拒跑）翻轉後的卦，現算、非落地欄。零待確認動爻 → 之卦＝主卦。**裁定 #59**，見下方「⑥ 為什麼不用昨日位元差」 | `king_wen`、`streaks` |
| ⑦ | 候選名單與排名重疊率 | 逐日取 `in_rank_pool=1` 的列依 `base_score` 降冪（次鍵 `stock_id`）排序，取前 **N＝`floor(N0 × 名額連乘)`**（`N0` 取自 S1 §A1.2 的基本狀態 × 方向表：S1 20/5、S2 12/10、S3 10/10、S4 5/20；基本狀態未定 → 當日不出名單、整格跳過並記 `days_state_undetermined`；名額被乘成 0 另記 `days_quota_zero`——真實乘數落在 0.105~0.25，`floor(5 × 0.105)` 就是 0，與「有名單但零重疊」在報告上長得一樣）；前後側名單的 Jaccard ＋ **同名次率**（不是 Spearman） | `base_score`、`in_rank_pool`、大盤 `flags.basic_state`／`by_direction.quota_multiplier` |
| ⑧ | 封頂／下限 binding 率 | `floor_applied=1` 的列數 ÷ 非 None 列數；`overheat_cap_applied` 同理。**分母排除 None**（§18 已記：`floor_applied` 的 None 混四種成因，分母實為「創高日 ∩ 族 A 有分數日」） | §18 三欄 |

**門檻**：任一項差異 > 10% 須在登錄文件說明原因並確認是預期行為（規格原文）。

### 驗收條件（先寫，改的人不得自驗）

| # | 條件 | 怎麼驗 |
|---|------|--------|
| F1 | 八項都真的算得出來，且每項的分組鍵與上表一致（六項 `direction="n/a"`） | 對合成的兩份小 db 實跑，逐項檢查輸出形狀 |
| F2 | **同一份 db 自己對自己比 → 八項差異全為 0**（②④ 的「次數／分布」則兩側相同） | 拿同一個 db 當前後側跑一次，斷言全零；這是最基本的自洽檢查 |
| F3 | 每一項都能被對應的人工擾動打出非零 | 逐項造一個只動該項來源欄位的合成差異，斷言只有該項變動 |
| F4 | ⑧ 的分母**排除 None**（不得把 None 當 0） | 造含 None 的列，斷言分母與 §18 的定義一致 |
| F5 | ⑥ 的之卦是現算而非讀欄；動爻位取自**今日的 `streaks`**（待確認動爻），不得回到「昨日位元差」 | 以已知卦例手算對照＋突變測試（改昨日爻態時 ⑥ 必須不動） |
| F6 | 報告同時輸出 JSON 與純文字，數字一致；>10% 的項目自動標記 | 實跑後比對兩份 |
| F7 | 全量測試綠、ruff 零新增項、`spec/tools` 四支綠 | 實跑（ruff 用 `origin/main` worktree 比） |
| F8 | 五類「量錯但仍自洽」的實作要被擋下（②只算單側／③命中數不累加／④桶錯置／⑦名額乘在池大小上／⑥退回昨日卦） | 逐類做一次突變，斷言**只有**對應的那支測試會紅 |

## §20.1 `:717` 首版驗收退回與裁定 #59（2026-09-22）

`bc366bd` 的 fresh-context 驗收判**不可合併**，四類問題。以下逐條記錄**實際的錯在哪**，
而不只是記「後來改成什麼」——這幾個都是「測試全綠但量到的是別的東西」。

### 必修一：⑥ 讀到的是今日位元，不是昨日（首版最嚴重的一個）

`step_day` 的 ②④ 區塊在迴圈裡就 `prev_bits[side][key] = cur` 寫回，⑥ 才去
`prev_bits["before"].get(key)` 讀——**讀到的已經是今日**。於是
`moving_positions(cur, cur) == ()`、之卦恆等於主卦，⑥ 的後半**整項退化成恆等於前半**，
而 F2（自己對自己全零）與 F3（逐項擾動）**兩支都照樣綠**。

### 必修二：⑥ 就算讀對了昨日，語意也是**向後看**的（裁定 #59）

首版的「動爻」＝昨日→今日位元差，把今日卦在那些位翻回去＝**昨日的卦**。
所以「之卦一致率」數學上恆等於「主卦一致率**落後一日**」，既不前瞻、也不提供新資訊。
**使用者裁定：之卦要用來推測日後發展，不能是落後一日的資料。**

**現行定義＝待確認之卦**：動爻取**今日 `streaks[i] >= hysteresis_confirm_days − 1`** 的爻
——`streaks` 是遲滯的確認天數計數器，正式爻態為陰而分數 ≥55（或為陽而分數 ≤45）才累加、
未達門檻立刻歸零、累到 2 就翻爻並歸零，所以 `streak == 1` ＝**再站穩一日就翻**，
正是 v1.2.2 §8 的「候選變化」與「條件式之卦」。之卦＝把這些爻翻轉後的卦
＝「若明日續站門檻另一側，卦會變成這個」。零待確認動爻 → 之卦＝主卦（不是 None）。

這個定義對 `d` 的改動**比主卦更敏感**：尚未確認的穿越就看得見，不必等到真的翻爻。
`streaks` 解不出（欄位缺、長度非 6、非整數）→ 回 None，**不得當成「零待確認動爻」**。
`CONFIRM_DAYS` 從 `Rules()` 取、不寫死 2。

### 必修三：⑦ 的 `N` 差兩個數量級，而且量的不是「名額上限」

`quota_multiplier` 乘的是 **`N0`**（S1 §A1.2 的基本狀態 × 方向表），**不是池大小**。
首版寫成 `round(池大小 × 連乘)`：①量到的是「池縮放」不是名額上限 ②`quota_multiplier == 1.0`
時兩側名單都是全池、**Jaccard 恆為 1**。現行＝`floor(N0 × 連乘)`，下限 0（`P1-B1-market.md`:345）；
`N0` 由當日大盤列的 `flags.basic_state` 決定（S1 20/5、S2 12/10、S3 10/10、S4 5/20），
**基本狀態未定（S1 §A1.1 末句：該市場當日不輸出候選名單）→ 整格跳過**並單獨記
`days_state_undetermined`，不得當成「名單相同」或「名單全空」灌進 Jaccard。
報告另出 `days_basic_state_differs`（兩側基本狀態本身就不同的日數）。

**`T0` 入場門檻這次量不到，列為待驗**（使用者裁定）：`threshold_shift_deciles` 是**分位數位移**，
要套在 `T0` 上，而 `T0` 依 P2 政策第 10 點是「48 格共用一個 `q`」、**`q` 還沒定**。
沒有 `q` 就沒有可套的基準，任何代用值都是我方自己挑的、不是規格的。
故 ⑦ 這次只量 **`N0` 名額層**；登錄書須明寫「**T0 入場條件待 `T0` 校準後另行重驗**」，
不得讓讀的人以為 ⑦ 已經涵蓋入場門檻。

### 必修四：`hetzner_t717.sh` 的 errexit 在 `body` 內不生效

`body` 跑在 `body 2>&1 | tee` 的左側＝子 shell，而外層為了拿 `PIPESTATUS` 先 `set +e`。
於是 `replay_scores.py` 失敗時 `body` 會**若無其事往下走到步驟 3**，用半套資料產一份報告、
`rc` 仍是 0，第 4 步就把它推上去了。`if [ "$rc" != "0" ]` 那行從頭到尾都在，
**原本那支純文字斷言的測試全綠**。

現行：`body` 開頭 `set -e`（在子 shell 內、不外洩）＋兩支長跑各給可分辨的 rc
（前側重播 3、分析 4）＋報告檔存在性檢查。測試改成**真的把腳本跑一遍**
（stub `python3`、本機 bare repo 當 origin），先證明合法路徑 rc=0 且分支推得出去，
再證明兩種失敗各自 rc=3／4 且 `git ls-remote` 只剩 `main`。
> stub 自己就叫 `python3` 又排在 `PATH` 最前面，shebang 寫 `#!/usr/bin/env python3`
> 會遞迴呼叫自己、整支測試掛住（實測踩過，現寫真直譯器絕對路徑）。

### 裁定 #59 第三項：①②④⑤⑥ 納入大盤列，但**另成一組**

首版只有 ③⑦ 用到大盤列，而大盤同樣有六爻與 `king_wen`，門檻行為完全沒被重驗。
現行：分組鍵加一個 `scope` 維度（`stock`／`market`），①②④⑤⑥ 兩組各跑一遍。
**不得混算**——每市場每期間大盤只有 1 檔，混進 7,500 檔個股會被稀釋到看不見。
⑧ 與 ③ 的 `overheated` 在大盤列一律 NULL，自然不會產生 `scope="market"` 的列。

### 一併修掉的兩個靜默問題

- **名額乘數與基本狀態跨日沿用**：首版由呼叫端持有一個 dict 傳進 `step_day`，某日大盤列缺席時
  會**沿用昨日的乘數**。現改為日內區域變數。
- **③ 補上 `n_before`／`n_after`**：原本只有比率沒有母體大小，看不出那個比率是幾天算出來的。

### 規格正本待辦（本節仍不做）

**新增一條：⑥ 的口徑已與 `:717` 的字面不同，正本要補記。**
`spec/stock-iching-plan-v1.2.2.md:717` 的 ⑥ 原文是「主卦與之卦 `king_wen` 的逐日一致率
（**④只比動爻「數」，動爻位置換了卦就換了**）」——那個括號把 ⑥ 的動爻**綁到 ④ 的動爻**，
而 ④ 就是「相鄰兩日位元差」，所以**正本字面要的是「已確認動爻」的之卦**。裁定 #59 改成
前瞻式（條件式之卦）在 `v1.2.2:372`「已確認動爻／候選變化／條件式之卦分開」裡有名分，
但**正本沒有改**，日後有人拿 `:717` 對報告會對不上。凍結前要在正本補一句、或在登錄書
明記此處依裁定 #59 覆蓋。
> 附帶澄清（**不是**量測破洞）：⑥ 後半因此不再服務正本說的「補 ④ 的位置盲區」這個目的，
> 但前後側的**動爻位置差異**已由 ① 的逐爻比對與 ⑥ 前半的主卦一致率涵蓋，沒有留下空白。

`threshold_revalidation` 宣告 12 筆與實際不符的問題依舊（見上一節）。**裁定 #59 之後
維度組成又變了**——③ 的大盤旗標與 ⑦ 是 `market × horizon × direction`，③ 的 `overheated`
與 ①②④⑤⑥⑧ 是 `scope × market × horizon`。拆標的時要照這個新組成，
且**仍不可在 `direction` 維度加第三個值 `n/a`**（會讓 `check_dims.py` 規則 5 的乘積驗算爆掉）。

### 二次驗收（`6a4b0dc`）退回的三項與六個存活突變

第一次修完仍判不可合併，**退回的不是量錯，而是「守門看起來有、實際沒有」**：

1. **`_over()` 漏了 `scope`**：八項都帶了新維度，但 >10% 標記清單的 `hits.append` 只抄
   `(market, horizon, direction)`，於是大盤與個股在同一 (market, horizon) 同時超標時
   **印出兩列逐字相同**、分不出是誰——而那份清單正是登錄書「逐項說明原因」的輸入。
   **這是已知坑 #1「宣告的鍵少於實際的變動來源」的第十次同型復發**，且就發生在為裁定 #59
   新增維度的同一批裡。`as_text` 的逐項列與 over 行一併補上。
2. **必修四的 `set -e` 零守門**：新增的三支行為測試走的兩條失敗路徑本來就有 `|| return 3/4`，
   與 `set -e` 無關——只拿掉 `set -e` **十支全綠**（實測）。現補
   `test_git_step_failure_stops_before_replay`：讓本地 main 與 origin/main 分歧使
   `git pull --ff-only` 失敗（步驟 0 的 git 指令全都沒有 `|| return`、只靠 errexit），
   現行版停在步驟 0 且不推分支，拿掉 `set -e` 則 rc=0 並把報告推出去。
3. **`docs:788` 把 `CONFIRM_DAYS` 寫死成 2**，與 §20.1 自己寫的「從 `Rules()` 取」矛盾。

**六個存活突變**（驗收者自行設計 15 個，這 6 個沒被擋下）已全部處理。**其中五個逐個實測會紅；
「拿掉下限 0」那個是等價突變、殺不掉，改以直接斷言釘住**（三次驗收的訂正，見本節末）：

| 突變 | 為什麼原本殺不掉 |
|---|---|
| `_cut` 的 `floor` → `round` | 原測資用 `quota_multiplier` 1.0 與 0.5，在 `N0=5` 下兩者**恰好同值**。真實乘數是 {0.105, 0.141, 0.188, 0.25}，交叉八格 `N0` 後 **81% 會分歧**（例：`N0=20`×0.141 → floor 2／round 3）。上一版正是死在這一格的口徑 |
| `_cut` 拿掉下限 0 | 規格明寫「下限 0」，無守門。**但這是等價突變**——見本節末的訂正 |
| `N0_TABLE` 的 S2／S3 四格 | 測資只用到 S1 與 S4 |
| `trigram_state` 的 `>=55/<=45` → 嚴格不等 | ⑤ 的**門檻邊界**沒有任何測試——而這整份報告的主題就是「45／55 的判斷是否等價」 |
| `_sorted_pool` 拿掉次鍵 `stock_id` | 註解自己說這是 `budget.py`／`sectors.py` 的家族教訓，卻**沒有同分測資** |
| `BIG_DIFF` 0.10 → 0.99 | 只驗了反面（全零時 over 為空），沒驗正面（>10% 會被標記）＝F6 只過一半 |

同批另補：`CONFIRM_DAYS < 2` 直接拒跑（`>= CONFIRM_DAYS − 1` 在 1 之下恆真＝六爻全「待確認」，
之卦會變成主卦的全反，實測乾 1 → 坤 2）；`FLAG_NAMES` 改 `import` 上游
`score/market.py` 那份、不自己寫死字面量（上游增減旗標時會靜默漂移）；
`as_text` 補印 `market_rows_matched` 與 `scope_note`。

**驗收者實測確認、本批未動的兩點**（留作紀錄）：①`streaks` 與 `lines_bottom_up` 的索引順序
一致——用真的 `CrossDayState.advance_lines` 逐爻造待確認，`pending_king_wen` 算出的卦與隔日
真翻爻後的卦 6/6 全中（1→44、2→13、3→10、4→9、5→14、6→43）；400 日序列裡**實際翻爻 179 次
全部落在昨日 `streaks>=1` 的集合內、違例 0**。②`_index` 在真實 db 上不撞號：大盤列 `scope`
恆為 `market_index`、`stock_id` 恆為 `__MARKET__`，且大盤列的 §18 三欄與個股列的 `flags`
實測皆為 NULL——`test_market_rows_do_not_get_stock_only_items` 依賴的前提在真實資料上成立。

**驗收的誠實邊界**：上述「真實 db」是用 `tests/synth_db.build_full` ＋ 真的
`scan_features.py`／`replay_scores.py` 產的 80 日 × 5 檔小 db，**schema／型別／`flags` 結構
走的是生產程式路徑，但資料規模與分布不是生產的**；1,628 日 × 7,500 檔上的效能與 ⑥⑦ 的實際
數值沒有驗到，那要等 Hetzner 那兩次重播跑完。

### 三次驗收：我自己的突變測錯了東西，還把它寫成了事實

上一節寫「六個存活突變…**逐個實測會紅**」。**這句話是假的**，而且錯得很典型：

我給「拿掉下限 0」下的突變是 `max(0, …)` → `max(1, …)`——**動的是下限的「值」，不是
「有沒有下限」**。那當然會紅（`_cut_n(5, 0.105)` 從 0 變 1），於是我以為守住了。
驗收者下的是真正的突變（整個拿掉 `max(0, …)`），結果 **26 passed、殺不掉**（我事後自己複現）。

它殺不掉是**有原因**的：`Rules.flag_effects` 的十組乘數與 `insufficient_multiplier` 全為正，
`score/market.py` 又只做 `min(m, 1.0)` 連乘，所以 `n0 × mult >= 0` 在今日可達的輸入上恆成立，
`max(0, …)` 是**等價突變**。但那道防護不是白寫的：乘數來自 `flags` JSON，哪天上游或壞資料
給出負值，`math.floor` 為負會讓 `pool[:-3]` **靜默砍掉名單的最後 3 名**、而不是回空。
所以改以直接斷言釘住（`_cut_n(5, -1.0) == 0` 與 `_cut(["a","b","c"], 5, -1.0) == []`）。

**教訓（與上一輪的「複合突變」是同一族）**：突變測試的價值全在「突變下得對不對」。
下歪了的突變會給出**與守住一模一樣的紅燈**，而紅燈會讓人停止追問。
兩個具體判準：①**突變要打在你聲稱要守的那個性質上**；
②突變紅了之後要問一句「紅的是不是**我想守的那支**、理由對不對」，而不是看到紅就收工。

判準 ① 的兩個對照（**第一版把它寫成「不能換常數」，那是錯的**——它會否定本節上表裡
`N0_TABLE` 12→11 與 `BIG_DIFF` 0.10→0.99 這兩個完全正確、也確實殺得掉的突變。
會被後人抄走的判準寫錯，比原本那句假話影響更久，所以單獨訂正）：

| 守門聲稱的性質 | 對的突變 | 錯的突變 |
|---|---|---|
| `max(0, …)`＝「**有沒有下限**」 | 整個刪掉 `max(0, …)` | `max(1, …)`——打到的是「下限的值」，是另一個性質 |
| `N0_TABLE`＝「**這些數值就是 §A1.2 的值**」 | 把某一格換成別的數 | 刪掉整張表（那會直接 KeyError，測不出東西） |

**判準 ③（修這一節時當場又踩到，所以補上）：期待值不得由被測函式自己產生。**
把 F6 的比對從「只比浮點」擴成「浮點＋整數＋④ 的分布 dict」時，我圖省事用
`RT._fmt(v)` 當期待值——於是 `_fmt` 被突變時，報告文字與期待值**一起變**、恆相等：
P2（整數 +1）與 P3（dict ×10）存活是預期內的沒守到，但**連原本擋得住的 N4（浮點 ×2）
都跟著失效了**（實測）。格式契約的測試必須在測試端**獨立重寫一次**格式化
（`tests/test_revalidate_thresholds.py` 的 `_render`），否則就是循環論證。
這也是本節主旨的第三個實例：**這一次是在修「守門假綠」的過程中新造出一個假綠的守門。**
**並且：殺不掉的突變不等於守門沒用**——要判斷它是等價突變還是真破洞，判斷完照實寫，
不能為了讓表格好看而宣稱「逐個實測會紅」。

同批另補**驗收者新設計、存活的四個突變**的守門（都落在這批新增的程式碼上）：

| 突變 | 守門 |
|---|---|
| `days_quota_zero` 改成無條件累加／後側誤用前側乘數 | `test_guard_quota_zero_distinguishes_empty_pool`——測資是「前側名額被乘成 0、後側池裡根本沒人」，兩側名單都空但成因不同 |
| `_fmt` 把浮點值 ×2／整數 +1／④ 的分布值 ×10（純文字與 JSON 不一致） | `test_guard_text_numbers_match_json`——把 JSON 裡**每一個浮點、整數與 ④ 的分布 dict** 格式化後回比 `.txt`，F6 的正面原本零守門（只斷言 `.txt` 存在）。首版只比浮點，整數欄（`n_lines`／`n_before`／**② 的翻轉次數**）與 ④ 的分布 dict 實測突變存活，已補 |
| `as_text` 逐列表頭拿掉 `scope` | `test_guard_text_rows_carry_scope`——`_over()` 那個缺陷在另一個位置復活；原本的 `in txt` 斷言打的是 over 清單那幾行，涵蓋不到逐列 |
| `FLAG_NAMES` 只取前 4 支 | `test_guard_flag_names_track_upstream`——否則 ③ 會靜默只涵蓋 5 支大盤旗標中的 4 支（③ 的既有測試只用到 `F-臨界`） |

`FLAG_NAMES` 同批由 `tuple(sorted(_MARKET_FLAG_NAMES))` 改成**純別名** `_MARKET_FLAG_NAMES`：
③ 的輸出順序由 `build_report` 的 `sorted(keys3)` 決定、與本元組的迭代順序無關
（驗收者以 120 種排列實測輸出逐位相同），`sorted()` 買不到確定性，只會讓
`RT.FLAG_NAMES is market.FLAG_NAMES` 永遠為假、漂移斷言寫不乾淨。

**已知、刻意留給下一批的兩個報表語意邊界**（驗收者指出，本批不動）：
①`days_quota_zero` 只計「**兩側皆 0**」，前側 0／後側 3 的混合日不計入（`jaccard=0.0`
已正確反映，但欄名容易被讀成「任一側」）；②某 (market, horizon) 當日**完全沒有池內個股**時
`_sorted_pool` 不產生該鍵，⑦ 連 `n_days` 都不會 +1，所以 `n_days` 不等於觀測日數、
且 `n_days − days_state_undetermined − days_quota_zero` **推不出**「真的有名單的日數」
（缺一個計數器）。

## 21. 卦別排序表：裁定 #60／#61 與 `scripts/rank_table.py`（2026-09-23）

§16（裁定 #57）定了**成本的套用方式**，但動手寫 `scripts/rank_table.py` 時發現規格還有**三個縫**
——查遍 `docs/`、`spec/`、程式與測試都找不到（不是沒找到，是空白）。逐一裁定如下。

### 裁定 #60（使用者裁定三項）

| # | 縫 | 裁定 |
|---|---|---|
| 1 | 「高組＝前 1/3」但 64÷3＝21.33，取整沒規定；且 `n<500` 的卦「不參與排名」會讓分母 < 64 | **對「實際參與排名數 `M`」取 `floor(M/3)`**，不是對 64 取。`M=64` 時高低各 21、中組 22 |
| 2 | `data/rank_table.json` 的 schema 全 repo 只有路徑、零規定 | 先提一版、跑出**真實產出**給使用者看再定（→ 裁定 #61 加分位數欄） |
| 3 | `:134` 說「§1.6 附錄」、`:315` 說「本書附錄」，而 `docs/pre-registration.md` **沒有任何名為「附錄」的章節** | **檔尾新增「附錄 A」**；§1.6 的「尚未執行」改指過去 |

### 裁定 #61（使用者看過真實產出後改）

**異號卦改回規格字面**：`:564-565` 只寫「不進高／低組」，`:163` 的「不參與排名」是給 `n<500` 的。
裁定 #60 原本把異號卦一併排除在 `M` 之外，**實測顯示兩種讀法差很多且方向不一致**：

| 格 | 排除版（#60）高組 | 字面版（#61）高組 |
|---|---:|---:|
| twse/short | 18 | **11** |
| tpex/short | 15 | **3** |
| twse/mid | 13 | **18** |
| tpex/mid | 10 | **16** |

short 是排除版多、mid 是字面版多——不是單純的鬆緊差別，所以不能憑直覺選。使用者看過實證後
裁定走字面版：**64 卦都排名，異號者強制歸中組**。同批 JSON **加 `p25`／`p75` 分位數欄**。

### 真實產出揭露的結構：高組不足額、低組滿額（**不是訊號強弱**）

`floor(M/3)` 六格都是 21，但高組實得 3～18、低組六格有四格滿額 21。原因是結構性的：
**異號卦系統性集中在排名前段**（twse/short 的前 6 名全是異號卦）。報酬分布右偏
（少數大漲把均值拉正、中位數仍負）正是「均值高」與「異號」的**共同成因**，所以前 21 名裡
大量異號卦被拉回中組；而均值最低那一端多是普遍下跌、中位數同號，不觸發這條規則。

這段已逐字寫進附錄 A 的「⚠ 高組與低組不對稱」節，並明寫
「**把高組卦數讀成『這個市場／期間的強訊號較少』是錯的**」——凍結一份「高組 3 卦、低組 21 卦」
的表而不註明原因，日後必被誤讀成訊號強度（誠實原則）。

**`n<500` 在真實訓練段一次都沒觸發**（最小 `n=682`、中位數 **5,903.5**），所以驗收 R4 只能靠
合成測資驗。（原寫 5,922 是 `statistics.median_high` 的值、不是中位數——384 是偶數筆，
中位數要取兩個中間值的平均。2026-09-23 驗收實測更正。）

### 實作上三件先實查才敢寫的事

1. **`exit_reason` 在 CSV 裡沒有 `ok`**：真值只有 `""`／`no_entry`／`halt`／`delist`
   （`train_short` 全檔實測 1,060,702／8,678／8,502／263）。`ok` 只是 `export_dataset.py:435`
   寫 manifest 統計時把空字串 map 出來的標籤。判 halt／delist 時寫 `== "ok"` 會全錯。
2. **`no_entry` 的 8,678 列，`entry_limit_up`／`exit_limit_down`／`fwd_ret` 三者皆空字串**
   （列數精確相等）→ 布林欄一律 `== "1"` 比對，`int(v)` 會 `ValueError`。
3. **segment 寫死、不給 CLI 參數**（驗收 R5「驗證段一列都不讀」）——給了參數就不是守門。

### 又一次「測資讓兩種錯誤實作同值」

`floor` → `round` 的突變在 `M=64` 與 `M=60` 下**殺不掉**（兩者同值）。這與 `:717` ⑦ 的
`quota_multiplier` 用 1.0／0.5（在 `N0=5` 下同值）是**同型錯誤**，`_cut` 的 `floor`/`round`
那次也是。分歧的 `M` 只有 {50, 53, 56, 59, 62}，補 `M=62` 的測資才真正守住。
**這是第三次踩同一個坑，但第一次是在寫測試時就先算分歧點、而不是等驗收抓。**
判準見 §20.1 末的三條（突變要打在聲稱要守的性質上／紅了要問紅的是不是那一支／
期待值不得由被測函式自己產生）。

### 冪等性

對真實資料連跑三次，`docs/pre-registration.md` 與 `data/rank_table.json` **逐位相同**、
marker 只有一組。附錄以 `<!-- BEGIN rank_table -->`／`<!-- END rank_table -->` 包住，
重跑整段覆寫；marker 只剩一半時**拒絕寫入**（要人看），不猜。

## 22. `:717` 重驗結果：裁定 #62／#63 與附錄 B（2026-09-23）

### 實跑

Hetzner `scripts/hetzner_t717.sh` 以 A 重播（前側 `--uncalibrated`）對照現行校準後產出，報告推上
`hetzner/t717-2026-09-14` 分支（`4cbd632`），本批把 `runs/t717/report_2026-09-14.{json,txt}` 原檔拷入
（`cmp` 逐位相同）。比對列數與八項數字一律見登錄書**附錄 B**，本節不另抄。

報告可信度的兩項核對已寫進附錄 B：不經過 `d` 的量（`F-高波動`、個股 `overheated`）前後側差異應為零
——若分析工具把不相干的東西算進去，這兩項不會是零。實測值見附錄 B；不為零時生成腳本會中止。

### 裁定 #62（使用者「照你建議」）

| 項目 | 裁定 |
|---|---|
| ⑥ 主卦／前瞻式之卦一致率 | **改以 ① 單爻差異判定**。一卦六爻、一爻不同整卦就不同，實測 ⑥ ≈ (1−①)^6（逐格差距見附錄 B）；10% 門檻套在卦上等於要求單爻差異約 1−0.9^(1/6)≈1.74% 以下（只由規格門檻推得、不隨報告變），與 ① 的門檻不相容 |
| ⑦ 候選名單與名次重疊 | **接受為 c／d 校準的預期效果**；`T0` 未納入（裁定 #59），`T0` 定案後另行重驗 |
| ⑧ 封頂／下限觸發率 | **接受**（超標格見附錄 B） |
| ② 遲滯翻爻次數、⑤ 內外卦方向判定差異率 | 裁定 #62 時**未請使用者確認**（附錄 B 當時標「待使用者確認」，不替使用者認定）→ 見下方裁定 #63 |

### 裁定 #63（2026-09-23，使用者看過附錄 B 的解讀後同意）

**② 與 ⑤ 接受為 c／d 校準的預期效果。** 使用者確認時看到的依據（皆寫在附錄 B）：

- ②：超標格全在 `scope=market`；大盤每個市場×期間只有一條序列、前側翻爻次數基數小，數十次的絕對差即超過門檻；
  大盤各格校準後翻爻全部變多，**成因未量測**。個股層相對差遠低於門檻。
- ⑤：內外卦判定是三態（≥55／≤45／其間），比單爻多一條切線，這是 ⑤ 高於 ① 的**推測**原因，未另行量測。

兩項的「成因未量測／推測」標記**不因確認而拿掉**：確認的是「屬預期行為、不阻擋凍結」，不是成因已證實。
實作＝`scripts/t717_appendix.py` 的 `CONFIRMED` 把 ②⑤ 設為 `#63` 後重跑，附錄文字由程式產生；
登錄書 §3 那條同步改為「五項皆已確認」。

### 附錄 B 為什麼由程式生成

附錄 A 連退兩輪（F1／G1）的失效模式是「手寫的定性句緊貼著計算出來的數字，被自己下面的表推翻」。
所以附錄 B：①每個數字（含格數）由 f-string 從 JSON 算出；②附錄裡會隨資料變真變假的**定性斷言**都配一道
守門，資料推翻它就 `AppendixError` 中止、rc=2，不會寫出一句錯的字；③`--check` 由
`tests/test_t717_appendix.py` 守「附錄＝重新產生」。

守門共十七道（`_assert`／`raise`）：
- **報告自洽**：`over_threshold` 必須等於從各項原值重算的超標集合（`recompute_over`，本檔獨立重寫、
  不 import 分析工具的 `_over`；多重集合比對，因為 ③⑧ 同格多列、條目不帶 flag／column）／前後側
  `params_sha` 必須不同／**列內一致**（三態）：原始欄算得出時，② 的 `rel_diff`＝(後−前)/前、③⑧ 的 `diff`＝後−前
  （容差 `ROW_TOL`＝1e-12；真實報告實測誤差皆為 0.0）；算不出時（前側為 0 或任一側 None——分析工具的
  `_rel`／`rate` 此時**合法**回 None）衍生欄也須為 None——超標判定看衍生欄、附錄印的絕對差與前後側看原始欄，兩者對不上就會寫錯；
- **尚未支援的 None**：①`diff_rate`、⑥ 兩個一致率、⑦ `jaccard`／`same_rank_rate` 出現 None（分母為 0 時分析工具合法
  產出，真實報告目前沒有）→ 明確中止，不以 TypeError 當掉；
- **節結構**：超標項目集合必須恰等於有逐項說明的五項（`EXPLAINED`＝②⑤⑥⑦⑧）——少了會寫出空泛的
  「超標格…」句，多了會讓「未超標的 N 項」與總覽表矛盾；
- ② 超標全在 `scope=market`／② 大盤翻爻全部變多；
- 不經 `d` 的兩項差不得為 None／差為零（`overheated` 只取 `scope=stock`，與句中「個股」一致）／⑤ 整段高於 ①；
- ⑥ 主卦與前瞻式之卦（12 格×2 欄）與 (1−①)^6 的差都 ≤ `HEX_GAP_MAX`；
- ⑧ 各列差不得為 None／⑧ 的欄恰為 `floor_applied`／`overheat_cap_applied` 兩種、同格同欄無重複列、且無方向維度（報告 `direction_note`：
  ⑧ 不得複製成 long／short 兩列）／`floor_applied` 只出現在中期個股／
  前後分母相同／封頂列全部未超標／超標列後側較高。

「① 全部不超過 10%」與「⑧ 每個超標格對得到一列 `floor_applied`」**不另設守門**：前者由「報告自洽＋
節結構」保證（① 若超標必在重算集合裡，而 ① 不在 `EXPLAINED`），後者由「報告自洽＋⑧ 只有兩種欄且無重複＋封頂不得超標」保證；
第三版曾各設一道，第四版以「走不到的死碼」為由移除。**① 那道的論證成立；⑧ 那道當時不成立**——
第四版漏了「只有兩種欄」這個前提，驗收構造出 `column="other_cap"` 超標且清單有列的自洽報告，
照印「超標 2 處」只標 1 個 ●（見下方第 4 次退回）。第五版補上欄集合守門後，推論才成立。

`HEX_GAP_MAX`（3 個百分點）是**附錄自訂的說明門檻，不是規格常數**。

**這段敘述被驗收退回七次**：
1. 首版（`fdfe33e`）寫「每一句定性斷言都配一道守門」，當時只有三道。
   「前後差異為零」「⑤ 高於 ①」「大盤翻爻變多」「後側較高」「⑥ 是 ① 帶出來的」五句沒有守門。
   另有一句無依據的解讀已刪除：「⑦ 是校準影響最大的一項」——實際上 ⑥ 的 1−一致率最大值高於 ⑦ 的 1−Jaccard。
   ⑥ 的說明原本只比主卦；前瞻式之卦的差距範圍更寬，卻沒列出，結論還涵蓋全部 24 處。現已並列。
   同批把「待確認」測試從「數次數」改成「逐節綁定」：原本 ⑤⑦ 的確認狀態對調後，只數次數仍會全綠。
2. 補到八道後（`6887b98`）仍有三類漏網，驗收都以實測寫出了錯句：
   - 「未超標的三項」的標題與清單是寫死的：③ 一超標，就會同時寫出總覽「③ 超標 1」與「③ 全部未超標」。
   - ⑧ 的 ● 用「格」比對：封頂超標時，● 會標到同格的下限列上，或者整筆漏掉。
   - 「數十次的絕對差」與「全部低於 10%」是寫死的字（守門是 `>`，恰等於 10% 時字面不成立）。
   
   第三版（`fe7bac8`）：節結構由 `EXPLAINED` 守，⑧ 逐筆對到列，② 的絕對差由資料印出，① 改寫「全部不超過」。
3. 第三版仍全盤採信 `over_threshold`、不與原值交叉核對：清單漏列一格時，② 會同時寫「超標全在 market」
   與「個股相對差到 30%」，⑧ 標題仍寫「超標 1 處」（驗收實測）。另有 ⑧「只在中期」、「個股 `overheated`」
   兩句無守門（篩選沒限 scope）、前後側 `params_sha` 相同也照產。第四版補「報告自洽」與上列兩道。
4. 第四版（`ddbe03a`）移除 ⑧ 逐筆對列守門時，論證依賴一個沒守門的前提（⑧ 只有兩種欄）；「● 依格＝依列」的
   等價突變同樣依賴「同格同欄只有一列」。第五版把兩個前提合成一道守門。
5. 第五版（`4bd1bc0`）的自洽守門只核對「清單 vs 衍生欄」，不核對「衍生欄 vs 原始欄」：② `after` 改成 `before`+1、
   `rel_diff` 不動時照印「絕對差 +1 次就超過門檻」（驗收實測）。第六版補列內一致與 ⑧ 無方向維度。
6. 第六版（`9ebb4a5`）的列內一致**引入回歸**：沒處理分析工具合法產出的 None 與前側為 0，驗收以這兩種形狀
   實測 `4bd1bc0` rc=0、`9ebb4a5` 以未捕捉的 TypeError／ZeroDivisionError 當掉且 rc=1——撞上 `--check` 的
   「附錄過期」代碼。第七版改三態判斷、對會取 `abs` 的兩處先守 None，並讓 `main` 把這兩種例外一律收成 rc=2。
7. 第七版（`189dd68`）：`rng()` 跳過 None、格數卻用 `len()`，③④⑤ 會照印「全部 12 格…」「66 格全部未超標」把
   算不出的格也算進去；①⑥⑦ 的 None 仍以 TypeError 中止；型別錯的報告拋 `AttributeError` 仍落到 rc=1。
   第八版：格數改「有值的 N 格（另 M 格算不出）」（無 None 時字句不變）、①⑥⑦ None 明確中止、`main` 改
   `except Exception` 一律 rc=2。

附錄 B 放在附錄 A 的 marker 之後、有自己的 marker；`rank_table.py` 的 splice 只換自己兩個 marker
之間，測試另守「重寫附錄 A 不吃掉附錄 B」。

突變實測（首版 9 個＋第二版 7 個＋第三版 4 個＋第四版 17 個＋第五版 2 個＋第六版 5 個＋第七版 5 個＋第八版 4 個，除明列的等價突變外全數被預期的那支測試抓到）：拿掉 ②／⑧／半 marker 三道守門、`**6`→`**5`、
① 守門 `>`→`>=`（邊界測試抓）、⑤ 改成已確認、`--check` 失效、`rank_table` splice 吃到檔尾、
列數加總少加大盤列；補守門後逐一拿掉四道新守門（零差／⑤ 高於 ①／② 變多／⑧ 較高）、⑥ 只守主卦、⑥ 門檻 `<=`→`<`、⑤⑦ 確認狀態對調並重新生成；第三版拿掉節結構、⑧ 封頂、⑧ 逐筆對列三道，以及 ⑧ 對列不看門檻。
「未超標清單寫死」是**等價突變**：節結構守門已把它固定成 ①③④，寫死與否產出相同，所以不算漏抓。
第四版把十二道守門逐一拿掉（各由對應測試抓到）、`overheated` 篩選拿掉 scope、重算規則拿掉 `abs`、兩類比較 `>`→`>=`（一致率型的邊界在門檻 0.1 下浮點碰不到，改以門檻 0.5 直接測 `recompute_over`）。
等價突變另一個：⑧ 的 ● 依格或依列標產出相同（表裡只列 `floor_applied`、且每格只有一列——後者第五版才有守門），
防標錯列靠的是欄集合與封頂兩道守門。第五版把欄集合守門的兩半（欄集合、無重複）各拿掉一次，各由對應測試抓到。
第六版：列內一致的 ②／③／⑧ 三段各拿掉一次、⑧ 無方向拿掉、容差放寬到 1e-2——最後一個起初**沒被抓到**
（既有測試只用大幅不一致），補一支偏 1e-9 的測試後才紅。
第七版：三態的「None 側」改成一律放行、`_expect_rel` 拿掉前側為 0 的判斷、兩道 None 守門各拿掉、`main` 不接
TypeError／ZeroDivisionError，各由對應測試抓到。
第八版：`_cells` 無視 None、①⑥⑦ None 守門拿掉、`main` 退回列舉例外、⑤ 句一律加「全部」，各由對應測試抓到。

### ⑧ 文字的一次自我更正（寫作時）

初稿把下限寫成套在 `base_score` 上。實查 `src/iching/score/stock.py` 的 `revenue_high_floor`：
下限只在**中期初爻**、套在**族 A（月營收 YoY＋加速度）分數**上。附錄 B 已依程式改寫——但這只證明
程式這樣寫；分布為什麼這樣移動，附錄明寫**未量測**。

### 範圍外、記下不做

Hetzner 重播曾因 `scores` 表缺 `floor_applied` 欄中止（schema 1 的舊 db、`SCHEMA_VERSION 2` 無 migration，
且 `clear(dv)` 在 schema 驗證之前執行）。當時以唯讀確認 0 列後刪 db 重跑繞過；**根因已於 2026-09-24 修正，見 §23**。

## 23. `scores.db` 開檔即驗實體表結構（2026-09-24，Hetzner A 重播事故的根因修正）

### 事故與根因

§22 末記的 Hetzner A 重播中止：schema 1 的舊 `scores.db`（沒有 §18 的 `floor_applied`／`overheated`／
`overheat_cap_applied` 三欄）跑 `replay_scores.py --rebuild`，錯誤是 `table scores has no column named floor_applied`。

根因有兩層：
1. **schema 只在 `replay_meta` 的列上比對**，實體表結構從來沒被檢查過；`CREATE TABLE IF NOT EXISTS`
   又不會替既有的舊表補欄。
2. **`--rebuild` 先 `clear(dv)` 再 `set_params`**：`clear` 把該 `data_version` 的 `replay_meta` 列刪掉，
   `set_params` 找不到舊列就照新版寫入，守門就此消失；直到 `write_day` 才撞上缺欄，而那時舊列**已經刪光**。
   測試實證：拿掉本次的檢查，事故路徑下 54 列 → 0 列（`tests/test_scores_schema.py` 的 rebuild 測試，突變實測）。

測試全部只建新檔，所以兩層都沒被抓到。

### 修正

`src/iching/scores_io.py` 的 `check_schema()`：`ScoreStore` 開檔時（讀寫、唯讀兩種模式都驗）比對四張表的實體欄位
與宣告**逐欄、依序**相同，不符就 `ScoreStoreError`。
- 宣告欄位由 `_DDL` 在記憶體庫實建後讀回（`DECLARED_COLUMNS`），**不另抄一份欄名清單**。
- 讀寫模式下 DDL 與檢查包在同一個交易裡，不符就 ROLLBACK：**舊檔的表與列一個都不動**（測試比對 sha256 與列數）。
- 欄位順序也比，因為 `replay_meta` 的 INSERT 依位置寫入。
- 多出未知欄（新版程式寫的檔被舊版程式開）同樣拒絕。
- 唯讀模式原本要到 `rows_for_day` 才炸 `sqlite3.OperationalError`，現在開檔就給明確的 `ScoreStoreError`。
- `replay_scores.py` 既有的例外處理把它收成 rc=2、不印 traceback。

### 刻意不做遷移

不做 `ALTER TABLE ADD COLUMN`：§18 三欄的 NULL 語意是「不適用」（DDL 註解：NULL 不等於 0，算 binding 率時
分母要排除 NULL），替舊列補 NULL 會把「沒算過」讀成「不適用」。這與 `set_params` 原本「不做遷移」的立場一致；
本次補的是**讓這個立場在實體表層面真的成立**。遇到舊檔時訊息要求以現行程式重播產生新的 `scores.db`
（`replay_scores.py` 指定新的 `--out`），或確認舊檔不需要後自行移走。

### 驗收（`a5eacd8`）退回的一處與補強

fresh-context 驗收另以 §18 之前的**舊版程式真跑**產出舊檔（不是 DROP COLUMN 造的），K1–K5、K7–K10 通過；
突變實測拿掉檢查後 90 列 → 0 列，且 `replay_meta` 被改寫成 `schema_version=2`（守門被污染）。退回與補強：
- **錯誤訊息（阻擋）**：原寫「改用新的 `--out` 路徑」只對 `replay_scores` 成立，但訊息由共用的 `check_schema`
  發出，唯讀消費端（`export_*` 的 `--out` 是 repo 根目錄）照做無效。改為通用說法。
- 原寫「也不會動這個檔」過寬：舊檔若是 DELETE journal，開檔前的 `PRAGMA journal_mode=WAL` 會永久改成 WAL；
  孤兒 `-wal` 會在開檔時被 SQLite 寫回主檔。兩者**位元組會變、表與資料列不變**，訊息改為「拒開時不改動表與資料列」。
- 補測試：拒開後不得補建其他表（檢查移到 COMMIT 之後的突變原本全綠）；`versions`／`replay_day`／`replay_meta`
  多欄各自直接測（跳過 `versions` 的突變原本全綠）；只多欄時訊息不印空清單。

**已知限制（記錄即可）**：`check_schema` 只比欄名與順序，不比型別、NOT NULL、PK。有人手動
`ALTER TABLE ADD COLUMN` 補上三欄的舊檔會通過檢查，唯讀消費端就會把舊列的 NULL 讀成「不適用」——需要人為操作才會發生。

### 範圍外、記下不做

`src/iching/features_io.py` 是同一種寫法（schema 只記在 `scan_meta`、`clear` 會刪掉那一列）。它的
`SCHEMA_VERSION` 從 1 起沒改過，**目前沒有暴露**；下次改它的表結構時要比照本節補實體結構檢查。

## 24. `:712`／`:714`／`:716` 的前置裁定 #64 與 `:714` 登錄檔（2026-09-24）

### 事實盤點（動手前）

- 三項都是 `spec/stock-iching-plan-v1.2.2.md` §16.5 表格的列：`:712` 合法範圍、`:714` 可達邊界、`:716` 分布檢查。
- **`docs/score-ranges.md` 在此之前從未存在**（全分支與 git 歷史皆無），三項也都沒有任何工具。
  §7.3 C「可達邊界登錄要重跑」的說法因此不精確——是**首次建立**。
- `scores.db` 不存子指標層資料，所以 `:712` 的「`N` 只套一次」無法從 db 驗，要另設驗法（下一批）。
- `:716` 的「達邊界比例」要用 `:714` 的登錄值，所以 `:714` 先做。

### 裁定 #64（使用者依建議裁定七項）

| # | 問題 | 裁定 |
|---|---|---|
| ① | `:712`／`:716` 的統計樣本段 | **訓練＋驗證段**（2021-01-01～2024-12-31），排除保留段 |
| ② | `:717` 用了含保留段的全段（只比分數、未看報酬），算不算動用保留段 | **不算動用，記錄即可**（登錄書 §3 已記） |
| ③ | 分組用的 `coverage` | **逐爻 `line_k_reweighted`**（列級 `coverage` 任一爻重配就整列算乙，會混歸） |
| ④ | `:714` 步驟 1 的 x 數學支撐放哪 | **放在 `scripts/score_ranges.py`**，不進 ParamSet，model_version／params_sha 不變 |
| ⑤ | 個股上爻族 A（沿用大盤方向分數）在甲區間的上游 | **大盤各爻所有可行狀態的聯集**（大盤方向分數只要求六爻已知、允許大盤爻重配） |
| ⑥ | 乙區間是否含滿覆蓋狀態 | **只含真的有重配的狀態**（與實測分組 `line_k_reweighted=1` 一致） |
| ⑦ | 各族能否以 `insufficient_history` 缺（影響 `exclude_insufficient` 可行性） | **保守外界：任何族都可能**（不靠讀程式推論原因碼） |

### `:714` 步驟 1～3：`scripts/score_ranges.py` → `docs/score-ranges.md`＋`data/score_ranges.json`

- 144 筆（scope × market × horizon × line × 甲乙），由程式產生、`--check` 守門，**數字不在本節另抄**。
- 子指標可達輸出一律呼叫程式本身的轉換函式（`S_clip`／`L`／`normalize`／`Rules` 情境值），不重抄公式；
  爻的可行性與是否重配直接呼叫 `aggregate.line_score` 判定。
- **外界、非緊界**：子指標與爻之間不獨立，依權重取極值不保證端點同時可達；`:714` 步驟 4 只要求實測落在外界內。
- 兩個特例（個股初爻中期族 A 下限 84.16、個股三爻封頂 79.89）只會把分數拉向區間內，程式逐狀態斷言，不成立即中止。
- **獨立對照**：規格調查時另一個 subagent 自行寫的參考算法（不同程式碼）與本腳本逐格相符（例如大盤初爻甲、
  三爻甲、個股四爻短線甲乙）。這只證明兩份實作一致，不證明規格讀法正確——後者由驗收檢查。
- 數值觀察（2026-09-24 產出當下由 `data/score_ranges.json` 讀出，**不是門檻判定**；參數一改即須重讀）：
  72 組乙區間中 68 組達 S 全幅，例外是個股四爻短線／波段（兩市場共 4 組，族 A `volume_scenario`、族 B L 型、
  族 C 情境表都窄於全幅）。甲區間比全幅窄的，來源都是 L 型、情境表、P_hist、`volume_scenario` 或
  `margin_scenario` 這類原生值域較窄的子指標。
- `equity_qoq`（裁定 #36 目前恆缺）照樣列為可能出現：它是全幅 S，不影響數值，符合 ⑦ 的保守外界立場。

### 驗收（`013d10a`）

fresh-context 驗收另寫獨立程式（不 import 本腳本、不呼叫 transform／aggregate）重算 144 格，lo／hi（±1e-6）與
各組合數全數相符；另以 Fraction 精確判可行性，與浮點判定零分歧。**退回一處（阻擋）**：腳本與產出檔用另一套
①～④ 引裁定 #64，與本節表不符（把「上游取聯集」引成 ②，而表中 ② 是保留段），已改為沿用本節編號並加測試守住。
同批補三個記錄項：`dist_ma_*` 依據的函式名改正；`short_sale_change` 上界改為 100（依其自身依據，數值不變）；
斷言 `direction_unknown_policy == "missing"`（方向分數外界的推算只對它成立）。

### 尚未做（下一批）

`:712`（實測最小／最大值＋「`N` 只套一次」）、`:714` 步驟 4（實測極值與本登錄比對）、`:716`（分布統計）——
前三者的實測部分合成一支 Hetzner 腳本，讀生產 `scores.db` 的訓練＋驗證段。

## 25. `:712`／`:714` 步驟 4／`:716` 的實測腳本（2026-09-24）

### 做什麼

`scripts/score_stats.py` 讀生產 `cache/scores.db`，一次產出三項；`scripts/hetzner_stats.sh` 是 Hetzner 一句話貼：

```
tmux new -d -s stats 'bash scripts/hetzner_stats.sh'
```

**不重播、不寫 db**，只讀。報告推到 `hetzner/stats-<db 最末資料日>` 分支（只放報告，不放 db）。

| 項目 | 判準（規格原文見 `spec/stock-iching-plan-v1.2.2.md` 各行） | 不過時 |
|---|---|---|
| `:712` 合法範圍 | 所有爻分數落在 [7.30, 92.70]，容許 ±0.01，零逸出 | 報告寫 FAIL（不中止） |
| `:714` 步驟 4 | 各組實測極值落在 `docs/score-ranges.md` 對應區間 ±0.01 內；甲比甲、乙比乙 | 報告列越界組 |
| `:716` 分布 | 五數、偏態、[45, 55] 比例、達邊界比例、相異值數；達邊界比例 > 20% 或相異值數 < 10 須解釋（**§27 起另加「單一值佔比 > 20%」，被標組另跑族組成診斷**） | 報告列須解釋的組 |

`:712` 的「`N` 只套一次」**不在本腳本**：`scores.db` 不存子指標層資料，驗法另案提出。

### 口徑（裁定 #64 之外、本腳本自訂並寫在檔頭）

- 樣本段寫死為訓練＋驗證段（裁定 #64 ①），**命令列不開日期參數**；只有內部 `run()` 開給測試（合成資料在 2020 年）。
- 分組用逐爻 `line_k_reweighted`（裁定 #64 ③）。
- 樣本＝該段內 `scores` 表所有列（大盤＋個股，不限 `in_rank_pool`）：檢查的是計分函數的輸出，不是排名池；池內外列數另列。
- 「達到可達邊界」＝距登錄端點 ≤ 0.01；相異值數以四捨五入到小數 6 位計；五數用線性內插；偏態用母體 Fisher–Pearson，
  6 位內只有 1 個值的組記 None（浮點尾數造成的雜訊）。
- 未知爻（NULL）不進統計，另計筆數。

### 開跑前守門

1. `docs/score-ranges.md` 必須與現行碼實算一致（`score_ranges.py --check`）→ 不過 rc=2。
2. db 必須是現行碼算的（`export_dataset.check_params`）→ 不過 rc=2。
3. db 內每個市場的 `model_version` 必須與登錄檔相同 → 不過 rc=2（否則登錄區間不是這份分數的區間）。

Hetzner 腳本：shell 內的守門（db 不存在、登錄檔過期）不過 rc=2；`score_stats.py` 內的守門（db 血統、model_version）
與統計失敗都會讓該步回非 0，經 shell 後為 rc=3。**兩者都不推送**；步驟 0 的 git 失敗靠 `body` 內重開的 `set -e` 停下
（比照 `hetzner_t717.sh` 2026-09-22 的教訓）。

### 自測

- `tests/test_score_stats.py` 19 支：統計函式手算、真實 replay 產出的 db（逐組筆數另以原始 SQL 重數比對）、
  三道守門、Hetzner 腳本以假 `python3`＋本機 bare repo 實跑。
- 突變 10 個全數抓到。**其中一個第一次沒抓到**：「db 不是現行碼算的」那支測試走 `main()`，而 `main()` 用寫死的
  2021～2024 樣本段、合成資料在 2020 年，rc=2 其實來自「樣本段沒資料」，拿掉守門照樣綠。已改為指定 2020 年樣本
  並比對錯誤訊息。與 §20.1 末「紅了要問紅的是不是那一支」同一條判準的反面：綠了也要問綠的理由是不是聲稱要守的那一道。

## 26. `:712` 後半「`N` 只套一次、`P_cs` 不套」的驗法（2026-09-24，使用者裁定：執行期計數＋呼叫點守門）

`scores.db` 不存子指標層資料，所以這一半不在 `score_stats.py`，改以 `tests/test_n_once.py` 在本機驗：

1. **執行期計數**：用合成資料跑真實重播，攔截 `market.sub_result`／`stock.sub_result`（子指標唯一入口），逐次計算
   該次內 `normalize` 與 `N` 的呼叫數：
   - `Ind`：`normalize` 恰 1 次；`N` 在原生值域非 S 時恰 1 次、是 S 時 0 次；`Missing`：兩者皆 0。
   - **`N` 總次數＝各 `sub_result` 內的次數＋兩個常數換算（`scenario_value_after_N`：下限 84.16、封頂 79.89）**。
     等式不成立＝有 `ind_*` 在 `sub_result` 之外先套了 `N`（重複映射）。
   - `Param.native_range`（宣告）與 `ind_*` 實際回傳的 `Ind.native_range` 一致。
   - `P_cs` 不出現在任何子指標名。
2. **呼叫點守門（AST）**：`normalize(` 只在 `aggregate.sub_result`；`N(` 只在 `transform.normalize` 與
   `transform.scenario_value_after_N`；`scenario_value_after_N(` 只在 `stock.line1_operations`／`line3_momentum`。
   多一處即紅。

**覆蓋的誠實邊界**：合成資料跑到的子指標以執行期證據驗；**沒跑到的 11 個**（`basis`、`eps_diff_over_price`、
`equity_qoq`、`excess_vs_industry`、`foreign_net_oi_phist`、`pretax_income_yoy`、`revenue_accel`、
`revenue_yoy_vs_industry`、`short_sale_change`、`updown_volume_ratio`、`vix_phist_rev`）只受第 2 點的靜態保證——
它們與跑到的子指標走同一個入口，且全程式沒有其他地方呼叫 `N`。其中 `foreign_net_oi_phist`／`vix_phist_rev`
是要套 `N` 的百分位類。清單寫死在測試裡，變了就紅。

實跑結果：全部斷言成立。首版突變三個全數抓到（情境表內先套 `N`＝重複映射、`normalize` 把百分位值域也當成不套 N、
`normalize` 呼叫兩次）。

### 驗收（`347fee2`）退回一處阻擋與補強

- **阻擋 B1**：上面「沒跑到的只受靜態保證」**對漏套不成立**。靜態守門只擋「多套」；在指標層把 `vix_phist_rev`
  改成漏套（0–100 的值宣告成 S 值域），全套測試全綠。而兩個要套 `N` 的百分位類恰好都沒跑到。
  補 `test_unexercised_phist_applies_N_once`：直接呼叫 `ind_oi_phist`／`ind_vix_rev` 經 `market.sub_result`，
  斷言 `N` 恰 1 次、分數等於手算值。首版那句「百分位類漏套被抓到」指的是 `normalize` 層的突變，
  不是指標層的漏套，表述錯誤，已更正。
- **R6**：`from .transform import N as _NN` 再在 `ind_*` 裡用，可同時逃過執行期攔截與只認名稱的呼叫點守門。
  補 `test_imports_locked`：鎖死誰能 import `N`／`normalize`／`scenario_value_after_N`、一律不得取別名。
- 同批處理的記錄項：R1 近乎常數組的偏態記 None；R2 查詢改依主鍵順序掃描；R3 §25 的 rc 敘述更正；
  R7 測試內章節號更正。
- **未處理、記錄**：R4 成功後 `git checkout main` 會移除本機報告檔（與 t717 同模式，報告在分支上）；
  R5 db 有多個 data_version 時分支名取的 TO 可能不是實際統計的那個；R8 樣本極小的組必然觸發「相異值數 < 10」，
  報告未區分樣本太小與真的退化——看報告時要一併看 n。

## 27. `:716`「堆在單一值」的量化與被標組的族組成診斷（2026-09-24，裁定 #65）

### 起因

`:716`（`spec/stock-iching-plan-v1.2.2.md:716`）原文有三個須解釋的情形：「無爻長期堆在單一值或邊界（達邊界比例 > 20%
須解釋）；無爻的分布退化為少數離散點（相異值數 < 10 須解釋）」。§25 的 `score_stats.py` 只實作了後兩個，
**「堆在單一值」沒有量化、也沒有實作**。Hetzner 生產報告（分支 `hetzner/stats-2026-09-14`，
`runs/stats/report_2026-09-14.txt`）標出 3 組須解釋：個股初爻 reweighted 的 tpex 短線／twse 短線／twse 波段，
達邊界比例 28.4%／34.3%／29.9%，相異值 361／228／131。

### 裁定 #65（使用者，兩項；以下為派工轉述的原文）

| # | 裁定 |
|---|---|
| ① | `:716`「堆在單一值」量化為：**最常出現值（四捨五入到小數 6 位）的佔比 > 20% 須解釋**（比照達邊界門檻）。 |
| ② | `:716` 被標組的解釋**必須有實測依據**（族組成診斷），不得只寫推測。 |

### ① 的口徑（`scripts/score_stats.py`）

- `mode_value`＝該組分數四捨五入到小數 6 位後出現最多的值，**平手取最小值**；`mode_share`＝該值筆數 ÷ n；
  n＝0 時兩者為 None。與「相異值數」共用同一次 `np.unique(..., return_counts=True)`（同一個 6 位口徑）。
- 門檻 `MODE_SHARE_MAX = 0.20`，**嚴格大於**才觸發（恰 20% 不觸發），寫進報告表頭 `mode_share_max`。
  理由字串「單一值佔比 > 20%」與既有兩個理由並列，一組可同時有多個理由（`explain_716()`）。
- `.txt` 表格多一欄「單一值」＝`mode_share@mode_value`。
- 記憶體：多出的只有 `counts`（int64、長度＝相異值數）。以 96 萬筆、大量平手的合成陣列量 `tracemalloc`，
  單組峰值由 21.0 MiB（舊式三行）→ 28.8 MiB（新 `group_stats`）；整支腳本的峰值由 `collect` 的逐組陣列主導
  （§25 驗收 980 萬列實測 349 MiB），**生產 db 上的整體峰值尚未實測**。

### ② 的做法：`scripts/score_diag716.py`（族組成診斷）

讀 stats 報告的 `summary.explain_716`，每組抽樣列、**以真實計分程式碼重算該爻**，量出族缺值型態與決定分數的子指標。

- **為什麼不整段重播**：整段 12.6 小時（`docs/P2-KICKOFF.md` §5 #38）。只重做影響爻分數的部分，全部呼叫原函式：
  ①自最早交易日逐日 `WindowCache.ingest`（不計分）到最晚抽樣日；②大盤只在需要的日子 `score_market`
  （大盤列被抽到、或個股上爻要當日大盤方向分數）——`MarketInputs` 唯一的跨日欄位 `line2_score_t_minus_5`
  只進旗標（`market.py` 的 `flag_breadth`），不進爻分數，所以不重建大盤二爻鏈；③個股四爻（短線／波段）的
  T−9…T−1 二爻：由 `scores.db` 查該檔前 9 個被計分日（db 有列＝重播有 push），在那幾天以 `stock.line2_trend`
  **重算**後填入（db 只提供「哪幾天」，不提供數值）；④抽樣列走 `wc.stock_inputs` → `score_stock`（基本面走
  `FundamentalsBridge.provider()`）。遲滯、旗標、ADV 不影響爻分數，不重做。**這些省略若將來變得影響分數，
  下面的 parity 守門會擋。**
- **攔截**：同 `tests/test_n_once.py`，包 `stock.S_clip`／`market.S_clip`／`stock.sub_result`／`market.sub_result`，
  只記錄（x、c、d、direction、原生值域），回傳值不動。族在場與否、缺值原因、子指標分數、clip 是否生效、重配後權重
  直接讀回傳的 `LineResult → FamilyResult → SubResult`。截斷位置 `u＝direction·(x−c)／(3d)`，|u|>1 ⇔ clip 生效
  （u>1 高分端、u<−1 低分端）。`S_clip` 與子指標的配對以物件同一性（`is`）為準；情境型內部呼叫一次 `S_clip`
  再重包者標 `indirect`；其餘（L 型、情境表、百分位、多次 `S_clip` 的量價情境）不配、u 記 None。
- **樣本段**：寫死＝訓練＋驗證段（同 `score_stats.py`，裁定 #64 ①），CLI 不開日期；報告的樣本段不同即中止。
- **抽樣與分層**：每組母體＝該段內 `line_k_reweighted` 與 coverage 相符、分數非 NULL 的列。以「達邊界」
  （距登錄端點 ≤ 0.01）與「單一值」（6 位＝最常出現值）兩個旗標切**四層** BM（兩者皆是）／B／M／R——
  最常出現值可能正好貼在邊界（例如 92.702703），切四層才能讓兩個母體列數的估計**恰等於**母體值。
  母體 ≤ `--per-group`（預設 3000）時普查；否則 BM、B、M 各配 `per_group // 4`（不足全取），餘給 R，R 不足依
  BM→B→M 回補；層內 `numpy.random.default_rng(--seed)`（預設 20260924，寫進報告）不放回均勻抽樣。
  **權重＝層母體列數 ÷ 層抽樣列數**；報告所有比例都是加權後的母體估計，另列各層原始抽樣筆數。
  **分層樣本本身的比例不是母體比例，不得直接引用。**
- **parity 守門（必須）**：每個抽樣列重算的爻分數與 db 相符（|差| ≤ 1e-9）且逐爻 `reweighted` 旗標相同；
  任一列不符 → rc=2、印前幾筆、**不寫報告**。其他守門：`export_dataset.check_params`（db 是現行碼算的）、
  `score_stats.check_versions`（model_version 與登錄檔一致）、以現行 ParamSet＋db 的 window／基本面開關重建參數指紋
  ＝db `params_sha`、報告的 `data_version`／`params_sha`／`registry_model_versions` 與 db／登錄檔相同、
  報告各組的 n／登錄區間／`mode_value`／`mode_share`／`share_at_boundary` 與本腳本由 db 重數的結果相同
  （舊版沒有 `mode_share_max` 的報告直接拒收）。任何例外 rc=2。
- **輸出**（`runs/stats/diag716_<TO>.{json,txt}`，每組）：母體與四層筆數／權重；族缺值型態分布（估計列數、
  佔母體、型態內達邊界比例、型態內單一值比例、各層抽樣筆數）；邊界列與單一值列各自的「子指標簽名」分布
  （每族在場的子指標及其狀態 `clip↑`／`clip↓`／`端點↑(未clip)`／`=值`，缺者寫原因碼；族下限、過熱封頂生效另標）
  與在場子指標的 clip 比例、u 的最小／中位／最大、c、d；一句自動產生的「量測結論」（只陳述「邊界列 N 列中 x% 為
  『簽名』」這類數字，**不寫成因**）。

### Hetzner 執行

`scripts/hetzner_stats.sh`（§25 的一句話貼不變）在步驟 2 產出 stats 報告後讀 `summary.explain_716`：
非空 → 跑 `score_diag716.py --report runs/stats/report_<TO>.json --db <DB> --out runs/stats/diag716_<TO>.json`，
失敗或產物為空 → **rc=4、不推送**；空 → log 印「須解釋 0 組，略過族組成診斷」。成功時診斷報告與 stats 報告
commit 到同一個 `hetzner/stats-<TO>` 分支（force-with-lease 邏輯不變）。

**生產耗時與記憶體未實測**：目標單次 < 30 分鐘、峰值 < 1.5 GiB（Hetzner 約 3.2 GiB 可用）。成本主體是逐日
`read_day`＋`ingest` 共 1,216 日（`data/calendar_tpe.json` 實算，自最早交易日到 2024-12-31）加上 ingest 期間常駐的
視窗與基本面橋；逐日 ingest 的單日成本在生產規模沒有單獨量過，**30 分鐘是目標、不是量測值**，以 Hetzner log
（每 100 日印一次耗時與 RSS、報告記 `elapsed_s`／`rss_peak_mib`）為準。
驗收者的**縮比外推**（容器 2,204 檔合成資料；Hetzner／容器速度比取 2.2，依據 P2-KICKOFF #38 的每檔成本
14 ／ 6.3 ms）：12 組 × 3000 列約 10～15 分，依「分段量測加總常低估約 2 倍」的既有教訓放大為 **20～30 分**，
落在目標上緣；記憶體估 **0.7～0.9 GiB**（整段重播 RSS 峰值 551 MiB ＋ 36k 抽樣列約 150 MiB——551 MiB 不是嚴格
上界）。未量：生產基本面橋的 `_industry_stats`、Hetzner 冷磁碟 I/O。被標組數明顯多於 12 組時，考慮降低
`--per-group`。
另：Hetzner 報告在加入 ① 之後重跑，被標的組**可能多於現在的 3 組**（例如 2026-09-14 報告中個股四爻短線
reweighted 上櫃那組的 q1＝中位數＝50.0000——上市那組是 q1 46.7014、中位數 49.9982，並非如此——**推測**上櫃那組
的 50.0 佔比 > 20%，未實測）；診斷對所有爻與大盤列都能重算，
不限初爻。

**fresh-context 驗收（`d7cbd8a`）已知、記錄不修**：① 診斷端的邊界容差改成 0.1 時測試全綠（合成資料沒有值落在離
端點 (0.01, 0.1]）；生產上會被「報告 `share_at_boundary` 與 db 重數不一致」擋下（rc=2，不會無聲），但測試不守。
② `indirect` 配對路徑（`margin_scenario`）在合成測試世界從未觸發；驗收者在縮比資料上實測 clip 與 u 一致，沒有測試守。
③ explain 為空時 Hetzner 分支的 commit 訊息仍寫「＋族組成診斷」（措辭，內容只含 stats 報告）。

### 自測（合成資料）

- `tests/test_score_stats.py`：單一值手算（平手取最小、6 位合併、n＝0、恰 20% 不觸發、20.01% 觸發、三個理由並列）、
  真實 replay db 上以原始 SQL（`ROUND(…, 6)`／`GROUP BY`／平手取小）逐組比對 mode；Hetzner 腳本以假 `python3`
  ＋本機 bare repo 實跑 explain 空／非空／診斷失敗／診斷空產物四條路徑（讀 `explain_716` 組數那一段用真 python 執行）。
- `tests/test_score_diag716.py`：合成世界把**全部 67 個已觀測組**都列進 explain_716 普查——parity 全數相符
  （max |差| 0），涵蓋個股二～六爻與大盤列；族型態估計列數加總＝原始 SQL 列數；reweighted 組每列簽名都含「缺」、
  full 組都不含；攔截到的 u 與計分函式回報的 `clipped` 一致；分層抽樣（每組 5 列）權重＝層母體 ÷ 層抽樣、
  加權列數還原母體、型態內達邊界／單一值比例 × 列數加總恰等於母體邊界／單一值列數；同 seed 同結果。
  **parity 會紅且紅得對**：db 某列分數 +0.5（報告由被改的 db 重產，報告與 db 自洽）→ `ParityError` 指名該列；
  某列 full 改標 reweighted → `ParityError`「reweighted 重算 0 ≠ db 1」；`main()` 走到 parity 時 rc=2。
- **合成資料驗不到、改以直接測試補的兩條**：①四爻的二爻歷史——合成資料裡序 1 情境沒觸發，歷史錯了四爻分數也不變，
  parity 抓不到；改驗「實際餵進 `score_stock` 的歷史＝db 該檔前 9 個被計分日的 `line_2`」。②個股上爻的大盤方向——
  合成資料大盤六爻未齊、方向恆缺（db 的 `base_score` 全 NULL）；改驗餵進去的值＝db 同日大盤列 `base_score`，
  並把 `score_market` 的方向換成 42.0 → 必須 `ParityError`（證明方向真的有接到個股上爻）。
- 自測突變最終 19 個全數抓到（清單見派工回報）。**第一輪有三處沒抓到或抓錯理由**，都已處理：
  ①「CLI 測試」拿掉 parity 也照樣 rc=2——rc=2 來自寫死樣本段在合成資料上 0 列 ≠ 報告，已改為只在該測試把樣本段
  常數換成 2020、並比對 stderr 是 `ParityError`（與 §25 自測同型：綠了要問綠的理由是不是聲稱要守的那一道）；
  ②「二爻歷史不填」全綠——合成資料裡歷史不影響分數，且第一版的歷史核對讀的是本腳本自己的記帳而非真正餵進計分的值，
  已改為讀 `StockInputs.line2_score_history`；③「大盤二爻 T−5 鏈不推」全綠——查證後是**等價突變**（該欄只進旗標），
  於是把大盤鏈整個拿掉、只在需要的日子算大盤，改以「大盤方向接到個股上爻」的直接測試守。

### 範圍外、記下不做

- `docs/pre-registration.md` 不動，等 Hetzner 實測後另批。
- 本容器 `python3`（3.11）下 `scripts/score_ranges.py --check` 在未改動的 HEAD 上即回報不一致、且有 4 個測試檔
  因 f-string 語法（3.12 起才合法）收集失敗；CI 與 Hetzner 分別是 3.12／3.14，本批的驗證一律在 3.12 虛擬環境跑。

## 28. `:712`／`:714` 步驟 4／`:716` 實測結果：裁定 #66 與附錄 C（2026-09-24）

### 實跑與存放

Hetzner `scripts/hetzner_stats.sh`（§25／§27）產出的四個檔推在 `hetzner/stats-2026-09-14` 分支（`d4668af`）。
本批以 `git show d4668af:<path>` 原樣拷入 `runs/stats/`：`report_2026-09-14.{json,txt}`（`score_stats.py`）與
`diag716_2026-09-14.{json,txt}`（`score_diag716.py`）。**比照 `runs/t717/` 的慣例 json 與 txt 都存**
（t717 在 main 上就是 `report_2026-09-14.json`＋`.txt` 兩檔）：json 是附錄的唯一資料來源，txt 是同次產出的人讀版，
附錄只讀 json、不核對 txt 內容。

結果一句話：`:712` PASS、`:714` 步驟 4 PASS、`:716` 標出須解釋的組、診斷 parity 全數相符。
**逐組數字、sha256 與判定一律見登錄書附錄 C，本節不另抄**（同 §22 的立場：手抄數字會與表脫鉤）。

### 裁定 #66（2026-09-24，使用者；以下為派工轉述的原文）

> `:716` 被標的 5 組全部確認為**預期行為**，以診斷實測數據登錄（比照 #62／#63）。

使用者確認時看到的依據分三型（實測數字見附錄 C 各組小節；型態由 `scripts/stats_appendix.py` 的 `EXPLANATION` 指定、
每型的每一句都配守門）：

| 型 | 組 | 依據（實測／程式現況／由定義推得，逐句標明於附錄 C） |
|---|---|---|
| 族 A 只剩一個子指標（`single_sub`） | 個股初爻 reweighted：tpex 短線、twse 短線、twse 波段 | 短線／波段個股初爻的族權重只有族 A（`params.py` 的 `build_params`：`FW[(sc, h, "1")] = {"A": 1.0}`，程式現況）；三組族缺值型態 100% 為「A 在場」（實測）；reweighted＝族 A 兩個子指標（`revenue_yoy`、`revenue_accel`）缺一個，族分數與爻分數都是加權平均 ⇒ 爻分數＝剩下那一個子指標的 S 值（由定義推得）；邊界列與單一值列的子指標簽名全部是「一個缺、另一個在 S 端點」（實測）；最常出現值即登錄上界、單一值列全部也是達邊界列（實測） |
| 在場子指標皆等於最常出現值（`equal_subs`） | 個股四爻短線 reweighted tpex（單一值 @ 50） | 單一值列最多的簽名是「A{`volume_scenario`=50}；B 缺:denominator_zero；C{`continuation`=50}」（佔比見附錄 C）；單一值列的**所有**簽名在場子指標都等於 50、至少一族缺席 ⇒ 加權平均恆為 50（由定義推得） |
| 在場子指標的截斷位置固定（`fixed_pos`） | 個股五爻短線 reweighted tpex（單一值 @ 41.459459） | 單一值列只有一種簽名：外資／投信四個強度子指標 u＝0（x＝c）、`foreign_persistence` u＝−1（低端點）、D 缺:not_eligible、E 缺:missing；各子指標 c／d 為單一值（實測）⇒ 子指標分數固定 ⇒ 加權平均固定（由定義推得） |

**確認的是「屬預期行為、不阻擋凍結」，不是成因已證實**（同 #63）：子指標為什麼取到那個值（例如四爻情境表為何輸出 50、
五爻強度類的 x 為何恰等於 c）附錄 C 一律標「**未量測**」，不因確認而拿掉。
實作＝`CONFIRMED` 映射五組皆為 `#66`；登錄書 §3 同步加一條指向附錄 C。§0 的 TBD（凍結 commit）**不在本批**。

### 附錄 C 由程式生成（`scripts/stats_appendix.py`，仿 `t717_appendix.py`）

每個數字由 f-string 從兩份 JSON 算出；字面常數只剩規格常數（`:712` 的 [7.30, 92.70]、±0.01、`:716` 的三個門檻、
[45, 55]）與裁定編號。`--check`：附錄與重新產生的不一致 → rc=1；任何守門或例外 → rc=2。守門共 57 道 `_assert`，分七類：

- **輸入完整**：兩份報告必要欄位存在、`schema`＝1；報告表頭的門檻＝規格常數（報告用的門檻不是規格的門檻，附錄寫的門檻就是錯的）；
  樣本段＝裁定 #64 ①（取自 `iching.config.SEGMENTS`）、落地日在段內。
- **兩份報告互相綁定**：`data_version`／`params_sha`／樣本段相同、診斷記錄的報告檔名＝本附錄讀的報告；
  須解釋組集合、理由、登錄區間、n、達邊界比例、最常出現值與其佔比兩邊一致（浮點欄容差 1e-12）。
- **報告自洽**：組鍵不重複、`groups_total`／`groups_observed` 對得上；**列數守恆**（各組 n＋未知爻＝6 × 列數）；
  `:712` 逸出數＝各組加總、`pass_712` 與逸出數一致、全體極值對規格範圍的判定與逸出數一致；`:714` 各組旗標與越界清單
  由極值／登錄區間／容差**本檔獨立重算**（不 import `score_stats`）；`:716` 各組理由與 `summary.explain_716` 由門檻獨立重算。
- **`:712`／`:714` 必須 PASS**：不是就中止——那代表不該凍結、附錄不該存在。
- **確認映射雙向**：報告多一組須解釋（未裁定）或 `CONFIRMED` 多一組（報告裡沒有）都中止；`EXPLANATION` 與 `CONFIRMED` 同集合。
- **診斷可信**：parity 列數＝各組抽樣列數加總且 > 0、容差 ≤ 1e-9、最大差 ≤ 容差；族缺值型態佔比加總＝1、抽樣筆數加總＝`n_sampled`；
  邊界／單一值簽名的有無與母體列數一致、比例加總＝1。
- **說明段逐句守門**：三型各自的定性句（適用範圍、「100% A 在場」、「一個缺、另一個在端點」、「最常出現值即登錄上界」、
  「登錄區間是 S 全幅」、「單一值列全部也是邊界列」、「在場子指標全等於 50 且有族缺席」、「只有一種簽名」、
  「u 固定」、「c／d 單一」）被資料推翻就中止；簽名解析失敗也中止。

### Hetzner 實測耗時與記憶體 vs §27 的外推

診斷報告記錄：耗時 **658.2 s（約 11 分）**、RSS 峰值 **436.8 MiB**（`runs/stats/diag716_2026-09-14.json` 的
`elapsed_s`／`rss_peak_mib`）。§27 的縮比外推是 **20～30 分**、**0.7～0.9 GiB**：**實測遠低於外推，外推高估**。
但兩者條件不同——外推假設 12 組 × 3000 列（約 3.6 萬抽樣列），實跑是 5 組、13,213 列——所以高估了多少、
其中多少來自抽樣列數較少、多少來自外推方法本身（「分段量測加總常低估約 2 倍」的放大係數在這裡反向）：
**這一次實跑拆不出來、未量測**。能確定的只有：在這份生產 db 上，單次診斷遠在 §27 的目標（< 30 分、< 1.5 GiB）之內。

### 待量測：營收子指標沒有最小基期門檻（使用者裁定：先量影響面再決定）

**量到的**（附錄 C 末節由診斷 JSON 生成；樣本內、未加權）：`revenue_accel` 的截斷位置 u 在 twse 短線初爻邊界列上
最大 134,789.584、最小 −134,839.959；`revenue_yoy` 的 u 最大 120.934（tpex 短線初爻）。u＝direction·(x−c)／(3d)，
|u| 在十萬量級表示原始 x 遠在截斷範圍外。

**程式現況**（`檔案:行號` 只證明程式這樣寫）：`revenue_yoy_3m` 只在去年同期合計**恰為 0** 時回缺值
（`src/iching/score/stock.py:111`）；`ind_revenue_accel` 是兩組 YoY 相減後直接 `S_clip`（`stock.py:133-140`），
兩者都**沒有**最小基期門檻。對照 EPS：前期 EPS ≤ `eps_yoy_min_base`（0.1，`src/iching/score/params.py:86`）時改用
差額 ÷ 股價的替代指標（`stock.py:148`）。

**未量測**：u 為什麼這麼大（是否為去年同期營收極小所致）、全樣本有多少列受影響（診斷只抽了 `:716` 被標的 5 組，
其他組與 full coverage 沒量）、對爻分數以外（排序、候選、回測）的影響。這些列的爻分數被 clip 在 S 端點，
不影響本批 `:712`／`:714` 的 PASS 與裁定 #66。**使用者裁定先量影響面再決定**：另批寫 Hetzner 唯讀量測；
**本批不改任何計分程式**（改了會動 `params_sha`／`model_version`，§16.5 全部要重跑）。

### 驗收條件（先寫；改的人不得自驗，驗收綁本批 commit）

1. `runs/stats/` 四檔與 `d4668af` 逐位相同（`git diff d4668af -- runs/stats` 為空）。
2. `python scripts/stats_appendix.py --check` rc=0；`python scripts/t717_appendix.py --check` rc=0；
   `python scripts/score_ranges.py --check` rc=0；全套 `python -m pytest -q` 綠（3.12）。
3. 附錄 C 的每個數字可由兩份 JSON 復算；三型說明的每一句都有對應守門（竄改 JSON 讓該句不成立 → rc=2，且錯誤訊息指向該句）。
4. 附錄 A／B 不被本產生器改動、本產生器的區塊也不被 `rank_table.py`／`t717_appendix.py` 改動。
5. 計分程式（`src/`）零改動；§0 的 TBD 未動。

### 自測

`tests/test_stats_appendix.py` 76 支：真實報告 `--check` rc=0、竄改附錄一個數字 rc=1 且不寫入、冪等、關鍵數字獨立寫死；
每道守門以「其餘部分自洽」的手造報告測紅，並比對錯誤訊息（紅的是聲稱要守的那一道，§20.1 末的判準）。
突變：57 道 `_assert` 逐一改成空操作，**57 個全數被抓到**；另 15 個手工突變（門檻 `>`→`>=` 三處、`:714`／`:712` 拿掉容差、
例外改回 rc=1、`--check` 不比對、確認文字不看映射、sha256 改雜湊路徑、parity／浮點容差放寬、樣本段改錯、端點狀態集合放寬、
splice 多吃一字元）亦全數被抓到。
**突變工具踩到的坑**：第一輪逐一改寫同一檔、每次長度相同且在同一秒內，`__pycache__` 的 pyc 以「mtime 秒＋大小」判斷新舊，
於是每個突變跑到的其實是**上一個**突變的 pyc，結果整排錯位一格。改以 `python -B`／`PYTHONDONTWRITEBYTECODE=1` 重跑才正確。

### 範圍外、記下不做

- `score_diag716.py` 報告的 `u_definition` 寫「|u|≥1 ⇔ clip 生效」，但 `S_clip` 在 x 恰落在 c±3d 時不 clip
  （`clipped=(xc != xx)`），§27 的敘述「|u|>1」才對。附錄 C 用 |u|>1 的寫法；診斷工具的字串未改。
  注意診斷簽名的「端點↑(未clip)」判準是「未 clip 且子指標分數距常數 `S_HI`＝92.70／`S_LO`＝7.30 ≤ 0.01」
  （`score_diag716.py` 的 `sub_status`；比的是四捨五入後的常數，不是真實 S(±1)＝92.7027／7.2973），未 clip 時
  即分數落在 [92.69, 92.7027]（低端對稱），換算 **0.99926 ≤ |u| ≤ 1**，**不必然恰為 |u|＝1**；附錄 C 據此措辭
  （驗收 `746e150` 建議 2；u 範圍經驗收 `dd1b5f1` 以 S 公式驗算更正，原寫 0.9994 是誤用 92.7027 當端點）。
  附錄 C 的 `TOL` 只和 stats 報告表頭互驗，未直接綁 `score_stats.TOL`；兩者現值皆 0.01（驗收 `dd1b5f1` 建議 3，記下不修）。
- fresh-context 驗收（`746e150`）另記、不修：① 把 json 讀數換成寫死常數的突變（`elapsed_s`／`rss_peak_mib`／
  `n_days`／`seed`／`parity.rows`／u 最大值／未觀測組數／`model_version`）全套測試照綠——現值恰等於常數，測試只驗
  輸出字串；現行程式確實從 json 讀（驗收者獨立重算全數吻合）。② `single_sub` 守門只核對「兩個子指標、一缺一在
  端點」，未核對名稱是否就是 `revenue_yoy`／`revenue_accel`。③ 產生器不綁 repo 現況：不檢查報告的
  `registry_model_versions`／`params_sha` 是否等於現行 `build_params`、`registry` 是否等於 `data/score_ranges.json`；
  驗收者本次手動核對兩者一致，日後校準改動時附錄 C 不會自己變紅。
- 附錄 C 的報告綁定只核對 json；txt 未由程式核對與 json 一致。

## 29. 營收子指標基期影響面的唯讀量測（2026-09-24，使用者裁定：先量影響面再決定）

### 目的

§28「待量測」一節記下：`revenue_yoy`／`revenue_accel` 只擋分母恰為 0（`src/iching/score/stock.py` 的
`revenue_yoy_3m`），沒有 EPS 那種 `eps_yoy_min_base`；`:716` 診斷在被標的 5 組量到 u 達十萬量級。使用者裁定
**先量影響面再決定**，所以本批只做量測：`src/` 零改動、不寫 db、`params_sha`／`model_version` 不變；**不提出、
不實作任何門檻規則**。反事實（下述 C）只為量「影響有多大」，只在記憶體內重算。

新增：`scripts/revenue_base_impact.py`（量測）、`scripts/hetzner_revbase.sh`（Hetzner 一句話貼）、
`tests/test_revenue_base_impact.py`。附錄 C 的「待量測」節與登錄書**本批不動**，等 Hetzner 實測後另批。

### 口徑

樣本段寫死＝`SEGMENTS["train"][0]`～`SEGMENTS["valid"][1]`（同 `score_stats.py`，裁定 #64 ①），CLI 不開日期參數。
u＝direction·(x−c)／(3d)，c、d、direction 取自該列**實際傳給 `S_clip` 的參數**（每列用自己 horizon 的 c、d）；
|u| > 1 ⇔ clip 生效（|u| 恰為 1 不 clip，同 §28 範圍外第一條的更正）。

- **A 全母體分布（只讀營收／季報，不 ingest 價量）**：母體＝`scores.db` 樣本段內全部個股列（池內＋池外、兩市場、
  三期間）。初爻只讀 `StockInputs` 的五個欄位，所以 A 不重建價量視窗：以重播**同一支**
  `ReplaySource.load_fundamentals` → `FundamentalsBridge.inputs_for(sid, T)` 取「T 日最新可用月份」與產業中位數
  （不複寫 as-of 判定），建最小 `StockInputs`，呼叫**真實** `line1_operations`，攔截沿用 `score_diag716.Capture`。
  同一檔同一期間五個欄位完全相同的連續被計分日只算一次、按列數展開（run-length）。
  報告依 market × horizon × 子指標：列數（池內／池外）、各缺值原因列數、|u| 的 p50／p90／p99／p99.9／max、
  |u| > 1／3／10／100 的列數與比例（分母分列「全部列」與「在場列」）、clip 旗標列數；|u| > 10 列的**基期營收**
  （元，原始資料單位）分位數與最小值，以及「該列基期 ÷ 同一檔在同組樣本內基期可算列的列加權中位數」的分布
  （中位數 ≤ 0 另計）；另列同組全部在場列的基期分位數當對照。`revenue_accel` 的兩組 YoY 各自的分母分開列
  （`den`＝近組、`den_prev`＝前組）。
- **B 抽樣 parity（必須）**：每組（market × horizon）以「任一營收子指標 |u| > 10」切 extreme／rest 兩層，
  extreme 配 `per_group // 2`（不足全取）、rest 取餘、rest 不足回補 extreme；母體 ≤ `--per-group`（預設 1000）普查；
  `numpy.random.default_rng(--seed)`（預設 20260924）不放回均勻抽樣；權重＝層母體 ÷ 層抽樣。抽到的列走
  `score_diag716.py` 同一套部分重播：自最早交易日逐日 `read_day` → `WindowCache.ingest`，抽樣日
  `wc.stock_inputs` → `score_stock`（基本面走 `FundamentalsBridge.provider()`），攔截 `stock.sub_result`
  與 `ind_revenue_yoy`／`ind_revenue_accel`。
- **C 反事實影響（只量不改）**：對 |u| > K（K ∈ {3, 10, 100}）的列，暫時包一層 `stock.sub_result`，只把目標子指標的
  `out` 換成 `Missing(denominator_zero, "反事實…")` 後**重跑真實 `line1_operations`**（族分／爻分走 `family_score`／
  `line_score`，中期族 A 的 12 月新高下限照原碼）。原因碼只是反事實標記：`line_score` 的分母只對
  `insufficient_history` 特別處理，其餘原因碼聚合路徑相同。情境＝三個子指標各一套＋`joint`（同列所有 |u| > K 者
  一起改缺）。**普查**（run-length 展開後逐列計數，權重皆 1）。每組報：受影響列數與佔組比例、初爻變化量分位數、
  以 50 為界的陰陽翻轉（分兩向；只計兩側皆已知）、變未知、進／出 [45, 55]（兩端含；只計兩側皆已知）、
  `line_1_reweighted` 0→1（含變未知的列）。
- **D 使用處**：以 `ast` 掃 `src/iching/` 全部 .py。現況（2026-09-24 實查）：營收子指標只出現在初爻——
  `score/stock.py` 的 `line1_operations`（族 A 兩個子指標＋**中期族 C `revenue_yoy_vs_industry` 直接呼叫
  `revenue_yoy_3m`**，與中期 `revenue_yoy` 同一個分母）、`ind_revenue_yoy`／`ind_revenue_accel`、無呼叫者的
  `revenue_yoy_single`；`fundamentals.py` 的 `FundamentalsBridge._industry_stats`（產業中位數，族 C 的輸入）；
  其餘是參數宣告（`params.py` 的 `_mk_stock`）與校準值表（`calibrated.py`）。二～上爻**零**使用。
  因此 A／C 把 `revenue_yoy_vs_industry`（中期）一併納入；產業中位數本身用到 `revenue_yoy_3m`，但**它對中位數的
  影響本批未量**（只量各列自己的子指標）。掃到 `KNOWN_USES` 以外的使用處 → 中止（量測可能不完整）。

### 守門（任一不過 → rc=2、不寫報告）

`export_dataset.check_params`（db 是現行碼算的）；`score_stats.check_versions`（model_version 與登錄檔一致）；
db 未開基本面即中止；以現行 ParamSet＋db 的 window／基本面開關重建參數指紋＝db `params_sha`；樣本段無個股列即中止；
db 日期須在原料交易日軸；每列市場＝重播的 `pool.listed(sid, T)`；**A 全母體 parity**（每列初爻分數 |差| ≤ 1e-9、
`line_1_reweighted`／`line_1_unknown` 相同）；**基期核對**（本檔以
與 `revenue_yoy_3m` 同月份清單、同加總式子算出的 num／den，推得的 YoY 必須與真實 `revenue_yoy_3m` 回傳值**逐位相同**，
且與攔截到的 x 逐位相同；分母為 0 的缺值列 den 必須為 0；`revenue_accel` 的近組與前組**各自**核對——前組在近組缺值時
也照樣核對，因為 `den_prev` 無論近組在不在都會記下）；攔截必須是直接的 `S_clip`、`S_clip` 收到的 x＝`SubResult.x`；
子指標所在族與每期間應有的子指標集合；**B 抽樣 parity**（重算初爻與 db 相符、`reweighted`／`unknown` 相同、每個營收
子指標的在場／缺值原因碼＋detail 與 A 相同、x |差| ≤ 1e-9、`ind_revenue_*` 收到的 d 與 A 相同、每個子指標恰被送進
`sub_result` 一次、所有抽樣列都走到）——run-length 鍵的完整性由 A 與 B **共同**守（子指標 clip 在端點時換了輸入
初爻分數可能不變，只看分數擋不全；自測突變「鍵漏掉 `monthly_revenue`」在合成資料上是 B 擋下的）；D 的使用處掃描；`--per-group` ≥ 2。報告**只陳述數據**，不寫成因推測、不提門檻建議。

### Hetzner 執行

```
tmux new -d -s revbase 'bash scripts/hetzner_revbase.sh'
```

仿 `hetzner_stats.sh`：0 同步 main（工作樹不乾淨 rc=2；main 前進則以新版 re-exec）→ 1 守門（db 存在、
`score_ranges.py --check`，不過 rc=2）→ 2 `revenue_base_impact.py --db cache/scores.db --out runs/revbase/report_<TO>.json`
（失敗或產物為空 rc=3）→ 3 報告 commit 到 `hetzner/revbase-<TO>`、`--force-with-lease` 推送（只放報告、不放 db）。
失敗一律不推送；log 在 `cache/logs/revbase.log`（每次輪替）。報告每 100 日印進度（A、B 各自的耗時與 RSS），
最後寫 `elapsed_s`／`rss_peak_mib`。

**成本與未知**：A 不 ingest 價量，成本是一次讀出樣本段全部個股列（緊湊陣列）＋逐日 `inputs_for`（含每日一次的產業
中位數）＋每個 run 一次 `line1_operations`；B 的成本主體與 `score_diag716.py` 相同（自最早交易日逐日 ingest 到最後
抽樣日），該工具 Hetzner 實測 658 s、437 MiB（§28）。**本工具的生產耗時與記憶體未實測**，目標 < 60 分、< 1.5 GiB，
以 Hetzner log 與報告的 `elapsed_s`／`rss_peak_mib` 為準。
run 數（決定 A 的主要成本）有結構可估：月營收與季報的可用日都走法定期限，同產業各檔屬同一金融桶、同一天前進，
所以一檔一期間的 run 大致一個月一兩個。**fresh-context 驗收（`f77bdca`）的縮比實測**（生產形狀資料，2,153 檔 × 1,043 日
× 3 期間＝6,736,737 列）：419,835 個 run，約 65 個 run／(檔, 期間)、約為列數的 6%；A 耗時 231 s、maxRSS 390 MiB；
`read_rows` 讀 5.63M 列 17 s／330 MiB。**生產外推約 15～25 分、0.7～1.0 GiB——這是推測，未在 Hetzner 實測**
（外推方法與誤差來源見驗收紀錄；§28 已記過一次外推高估的前例）。

### 自測（合成資料）

`tests/test_revenue_base_impact.py` 66 支（首版 55 支＋驗收補測 11 支，見下「驗收補測」）。合成世界＝`synth_db.build_full`＋本測試追加的月營收（刻意做成手算得出的
極端：1102 的 2019 年基期只有 1,000 元且 2019-02 為 0、6488 近組 YoY 巨大而加速度約 −25 pp、1103 的 2019-01 基期
極小而加速度為 0）＋真實 `replay_scores` 80 日：

- **手算**：1102 於 2020-02-10 短線的單月 YoY x＝(1e8／1e3 − 1)×100、den＝1,000、u＝x／(3d)（d 取校準表字面值）；
  加速度兩組分母 2.00001e8／3e8；2020-03-10 分母 0 → `denominator_zero` 且 den 記 0。
- **A 對原始 SQL／逐列展開**：母體列數、池內列數、各組列數＝SQL；|u| 分位數與門檻計數＝`np.repeat` 展開後
  `np.quantile`；|u|>1 列數＝clip 旗標列數；基期比值分布以每檔 `np.median` 另算、全部分位數與無定義列數相同。
- **反事實手算**：中期（族 A 兩子指標＋族 C 在場、族 B 缺）拿掉 `revenue_yoy` 後＝(0.5·S(加速度)＋0.2·S(族 C))／0.7、
  拿掉族 C 唯一子指標後＝族 A、兩者一起拿掉後＝S(加速度)；短線兩個族 A 子指標一起拿掉 → 未知；S 與權重以測試內的
  公式手算、不經聚合碼。6488 拿掉 YoY 由陽翻陰（手算 S(−25)）。
- **翻轉／進出帶／變未知／rw 0→1**：以逐列（原分數取 db 的 `line_1`）直接比對另算，與報告逐組相同，且合成資料上
  翻轉與進帶都 > 0；另以手造 run 表驗 50 與 45／55 兩端的邊界語意。
- **分層權重還原母體**（每組 4 列）：層權重 × 層抽樣＝層母體、extreme 層加權列數＝母體 extreme 列數、至少兩組真的抽樣。
- **parity 會紅且紅得對**：db 某列初爻 +0.5 → A「全母體 parity 不符 1／…」並指名該列；竄改 A 的 x／缺值原因／在場與否／
  d → B 各自的訊息；真實計分的初爻 +1e-6、變未知、reweighted 翻轉、子指標被送兩次、不適用的子指標出現、當日不在
  計分名單、抽樣日走不到 → B 各自的訊息；`main()` 在 parity 不符時 rc=2（stderr 為 `ParityError`、不寫報告）。
- 其他守門各一支（params_sha、model_version、指紋重建、基本面開關、空樣本段、db 日期不在交易日軸、市場≠`pool.listed`、
  基期逐位核對、加速度／YoY／產業相對的 x 關係、直接 `S_clip`、`S_clip` 的 x＝`SubResult.x`、族歸屬、應有子指標集合、
  `--per-group` 下限、D 的未涵蓋使用處）；唯讀（db 的 sha256 前後相同）；CLI 樣本段寫死、不接受 `--start`。
- **Hetzner 腳本**以假 `python3`＋本機 bare repo 實跑：成功推 `hetzner/revbase-<TO>`（只含報告、不含 db，重跑可再推）、
  量測失敗／產物空 rc=3、登錄檔過期 rc=2 且不呼叫量測、db 不存在 rc=2、工作樹不乾淨 rc=2、步驟 0 git 失敗即停，全不推送。
- **突變**（首版）：守門 39 個（Python 32、shell 7）逐一拿掉，全數被對應測試抓到；另 9 個計算面突變（run 鍵漏 `monthly_revenue`、
  反事實開關失效、翻轉界線 ≥→>、進帶改開區間、u 少了 3、加權分位數取錯位、抽樣權重倒數、加速度前組基期錯位、
  自身中位數改 min）亦全數被抓到。**第一輪有三處沒抓到**，都已處理：①「db 日期不在交易日軸」拿掉後測試照綠——
  B 的「抽樣日不在原料交易日軸」訊息也含同一串字，已把期待訊息改成 A 那道獨有的「db 日期不在原料交易日軸」
  （§25／§27 同一條教訓：綠了要問綠的理由是不是聲稱要守的那一道）；②進帶改開區間、③自身中位數改 min——合成資料
  恰好沒有值落在 45／55 端點、且 1102 的中位數與極端列基期恰好相等，補了手造 run 表的邊界測試與逐列展開的比值測試。
  另記：「run 鍵漏 `monthly_revenue`」在合成資料上是 **B** 擋下、A 的初爻分數恰好全數相符（子指標 clip 在端點），
  所以守門敘述寫成「A 與 B 共同守」。
- 全套 `python -m pytest -q`（3.12）、`bash -n scripts/*.sh`、`score_ranges.py --check`、`stats_appendix.py --check`、
  `t717_appendix.py --check`、spec 工具鏈皆綠；新檔 `ruff check` 乾淨（ruff 0.16.7；repo 無 ruff 設定檔，即預設規則）。

### 驗收補測（fresh-context 驗收 `f77bdca`：無阻擋，但有 12 個「報告數字寫錯、測試照綠」的突變）

報告會拿來做決策，所以逐項補**手算預期值**的測試，每個突變都各有至少一支測試紅且紅的原因對：

| 突變 | 測試 | 紅的原因 |
|---|---|---|
| ① `share_of_rows`／`share_of_present` 分母互換（兩向） | `test_summarize_a_hand_table`、`test_share_denominators_on_replay_db` | 手造組：u 絕對值 > 10 的列 3，組列 16、在場 12 → 3/16 與 3/12；replay db 上 77/288 ≠ 77/159 |
| ② 組層池內／池外對調 | 同上手造表、`test_group_pool_counts_match_raw_sql` | 手算 (5, 11)；對原始 SQL 的組層 `SUM(in_rank_pool=1)` |
| ③ A parity 不比 `reweighted`／不比 `unknown` | `test_db_reweighted_tamper_hits_a_parity`、`test_db_unknown_tamper_hits_a_parity` | db 一列該欄翻轉，期待訊息以「A 全母體 parity 不符 1／」**開頭**（拿掉 A 的比對後改由 B 擋，訊息不同 → 紅） |
| ④ `became_unknown` 把原本就未知的列算進去 | `test_summarize_c_unknown_and_lower_band_edge` | 手算 5（原本未知 3 列只進 `orig_unknown_rows`） |
| ⑤ 比值無定義界線 `md<=0` 改 `md<0` | `test_summarize_a_hand_table` | 某檔列加權中位數恰為 0 → 無定義 2 列；突變後 0 除 0 例外 |
| ⑥ 自身中位數改不加權 | `test_summarize_a_hand_table` | 基期 100(w1)／200(w1)／300(w5) 的列加權中位數＝300（不加權 200）→ 比值 1/3（突變得 0.5） |
| ⑦ `read_rows` 終點改不含 | `test_sample_segment_endpoints_inclusive` | 樣本段＝資料首日～末日（2020-04-20）時列數＝SQL 兩端含（1,101；突變得 1,086）；只取末日一天也要有列 |
| ⑧ B 缺值只比原因碼、不比 detail | `test_b_missing_detail_tamper` | A 的 `denominator_zero` 只改 detail → B 須不符 |
| ⑨ C 原分數帶內下界 45 改開 | `test_summarize_c_unknown_and_lower_band_edge` | 原分數恰 45.0 屬帶內：`orig_in_band_rows`＝18、出帶 7 |
| ⑩ B 分層門檻改 100 | `test_draw_b_extreme_threshold_is_10` | u 絕對值恰 10 屬 rest、10.5／50／−10.5 屬 extreme |
| ⑪ run 鍵漏 `fundamentals`／中位數／樣本數／`monthly_revenue` | `test_run_key_every_member_matters` | 手造 bridge：一檔六日，五項輸入各在不同日單獨變動、每次都改變中期初爻；漏哪一項，該日 A parity 紅並指名該列 |
| ⑬ 近組缺值時不核對 `den_prev` | `test_accel_prev_base_checked_when_near_missing` | 近組缺 2018-11 → accel 缺值、`den_prev`＝108+107+106；前組基期被竄改 → 須中止 |

**⑪ 的結論（中位數／樣本數與月營收是否等價）**：程式面（`檔案:行號` 只證明程式這樣寫）——產業由 `pool.industry_of`
決定，金融桶由產業決定（`universe.is_financial`），所以同產業各檔屬同一個法定期限桶；月營收可用日只由
`(年, 月, 金融桶)` 決定（`fundamentals.build_stock`），產業中位數與樣本數因此**只會在本檔自己的月營收可用日變動**，
例外是本檔缺那個月的營收列（同業前進了、本檔沒前進）。所以在真實資料上兩者**通常但不必然**共變。驗收者在生產形狀資料
（2,153 檔 × 110 日）實測：鍵漏掉中位數／樣本數／月營收任一項，run 數與正確鍵**完全相同**；漏掉 `fundamentals` 則
A parity 紅 46,830／710,490 列。鍵照舊保留全部五項（多存幾個 run 的成本可忽略，漏掉在「本檔缺月」時會靜默算錯）；
手造 bridge 的測試證明每一項在一般情況下都是必要的。

**⑬ 屬程式修正但不改輸出**：`revenue_accel` 近組缺值時，`den_prev` 照樣寫進 run 表，卻沒經過 `_check_base`；
已改為前組**一律**核對。這只多一道守門，資料正確時輸出逐位不變（全套測試與合成資料報告相同）；不是量測結果的 bug。

**其餘驗收觀察未揭露 bug**：①～⑩ 都是「程式正確、測試沒守」，補測後程式行為不變。

### 範圍外、記下不做

- 附錄 C「待量測」節與登錄書不動，等 Hetzner 實測後另批。
- 產業中位數（族 C 的輸入）本身由各檔 `revenue_yoy_3m` 算出，極端基期對**中位數**的影響本批未量。
- 反事實只量到初爻分數；對方向分數、卦名、排序、候選名單與回測的影響**未量**。
- 合成世界裡產業樣本數 < 5，族 C `revenue_yoy_vs_industry` 在 replay 資料上恆缺；它的在場路徑只由手造輸入的單元測試覆蓋。

## 30. 營收子指標的負值基期：裁定 #67 與唯讀量測（2026-09-24）

### 裁定 #67（2026-09-24，使用者；以下為派工轉述的原文）

> 營收子指標**不加**最小基期門檻；記為已知限制與下一版候選，凍結版維持現狀。

依據＝§29 的 Hetzner 生產量測。報告由 `hetzner/revbase-2026-09-14` 分支（`580e97a`）以 `git show 580e97a:<path>` 原樣拷入
`runs/revbase/report_2026-09-14.{json,txt}`（blob hash 與該 commit 相同）；報告表頭：樣本段 2021-01-01～2024-12-31、
`params_sha` `c7385e78cb9f`、母體 5,243,958 列、run 354,534、A 全母體 parity 與 B 抽樣 parity（6,000 列）全數相符。
使用者裁定時看到的三項數字（取自該 JSON，以下欄位路徑可復算）：

| 依據 | 範圍 | 兩端所在組（JSON 欄位） |
|---|---|---|
| u 絕對值 > 10 的列佔各組列數 | 0.76%～1.78% | 最小 twse 波段／中期 `revenue_yoy` 0.756%；最大 tpex 三期間 `revenue_accel` 1.781%（`a_groups[].abs_u_over["10"].share_of_rows`） |
| 極端列（u 絕對值 > 10）基期相對同檔自身中位數比值的中位數 | 0.19～0.54 | 最小 tpex 短線 `revenue_yoy` 的 den 0.193；最大 tpex 三期間 `revenue_accel` 的 den 0.538（`a_groups[].base_revenue_yuan.*.ratio_to_own_median.p50`） |
| K＝10、`joint` 反事實的陰陽翻轉佔組列數 | 0.05%～0.36% | 最小 twse 中期 468／965,061＝0.048%；最大 tpex 短線 2,853／782,925＝0.364%（`c_groups[]` 的 `flip_rows`／`group_rows`） |

第二項的意思是：多數極端列的基期是自身中位數的兩成到五成，**不是**極小的基期（這是量到的分布，不是對成因的推論）。
**本裁定不改任何計分程式**：`params_sha`／`model_version` 不變，§16.5 的實測與附錄 C 不受影響。登錄書與附錄 C 的
「待量測」節**本批不動**（附錄 C 由 `stats_appendix.py` 生成、有 `--check` 守門）。

### §29 生產報告裡的負值基期（發現）

§29 的報告記了每組在場列基期（den＝`revenue_yoy_3m` 的分母＝去年同期合計，元）的分位數，其中**最小值為負**：

| 市場 | 單月組（短線 `revenue_yoy`）den 最小 | 三月組（其餘）den 最小 | 所有組在場列 den 的 p1 |
|---|---|---|---|
| twse | −28,846,346,000（−2.885e10） | −31,362,735,000（−3.136e10） | 皆為正（最小 1,694,000） |
| tpex | −478,320,000（−4.783e8） | −548,005,000（−5.480e8） | 皆為正（最小 186,000） |

（派工轉述的「上市 den 最小 −2.885e10」是**短線單月組**的值；三月組更小，如上表。）
所以負基期不到各組在場列的 1%，但**確切列數與股票**§29 沒有量——這正是本節量測的對象。

**由定義推得**（不是量測）：den > 0 且 num ≥ 0 時 YoY ≥ −100%，對應 u ≥ −100／(3d)；u 低於這個下界必涉及負值（den < 0 或
num < 0）。§29 報告的 `revenue_yoy` u 最小值全部遠低於下界，例如 twse 短線 −6,640.634（d＝19.274，下界 −1.729）、
tpex 短線 −243.655（d＝23.695，下界 −1.407）。`revenue_accel` 是兩組 YoY 相減、`revenue_yoy_vs_industry` 另減產業中位數，
兩者都沒有這個下界，不據此推論。

**程式現況**（`檔案:行號` 只證明程式這樣寫）：`src/iching/score/stock.py` 的 `revenue_yoy_3m` 算 `(num/den − 1)×100`，只在
`den == 0` 時回缺值；den < 0 時方向全部顛倒：−10→+5 得 −150%、−10→−2 得 −80%、−2→−10 得 +400%。

### 候選修法（候選、未採用、待量測後由使用者裁定）

`(num − den)／|den| × 100`：−10→+5 得 +150、−10→−2 得 +80、−2→−10 得 −400。den > 0 時與現行式子**數學上**相等。

**浮點上不必然逐位相同**（實測）：本量測另記「若 den > 0 也用字面式子」與現行式子不逐位相同的列數（報告 `counts[].literal_den_pos`）；
合成資料上即有（例：num＝1、den＝3 兩式差一個 ulp）。因此本量測的替身**只在 den < 0 時改寫**，den > 0 一律回傳現行式子的值，
並以守門斷言 den > 0 的列替換前後逐位相同。**若日後採用，實作要自己決定 den > 0 走哪個式子**；字面式子會讓 den > 0 的列也動到
最後一位（對 S 值與爻分數的影響本批未量）。

### 本量測：`scripts/revenue_negative_base.py`（唯讀；`src/` 零改動、不寫 db）

**重用 §29**（`import revenue_base_impact as RB`，不複寫）：母體讀取 `read_rows`、db 守門 `check_db`、使用處掃描 `usage_scan`、
最小 `StockInputs`（`line1_inputs`）、真實 `line1_operations`＋攔截＋基期逐位核對（`line1_detail`／`instrumented`）、run 表
`Runs`、A 全母體 parity `parity_a`、B 真實重播 parity `run_b`、加權分位數。樣本段寫死＝訓練＋驗證段（同 §29）。

- **列數**：每個 market × horizon × 月份組（`revenue_yoy`、`revenue_accel` 近組／前組、中期 `revenue_yoy_vs_industry`）數
  den < 0、num < 0、兩者皆 < 0、den > 0 且 num < 0、den＝0 的列（run-length 展開；比例分母＝該組 db 列數），另記 den < 0 且子指標在場的列。
- **股票清單**：出現 den < 0 或 num < 0 的每檔：代號、名稱（`TaiwanStockInfo` 的 `stock_name`）、產業（`pool.industry_of`）、
  `is_financial`、市場、受影響列數（另分 den < 0／num < 0）、受影響列的 as-of 最新月範圍、加總實際引用到的負值月（數量、首末、
  前 6 個、原始值範圍）、該檔歷史負值月數；依列數排序，> 200 檔只列前 200 並給總數。另彙總金融／非金融、產業前 10。
- **原始月營收**：直接讀 `raw_month_revenue`（同 `data_version`，數值經重播同一支 `F_num`）數負值的列、(檔, 月)、檔數——全表與
  池內、全期間與「營收月落在樣本段內」；另列重播所見（`load_fundamentals` 的橋，同月多列取最後）與「樣本段列的加總實際引用到的負值月」。
- **反事實（候選式）**：在 `revenue_yoy_3m` 的呼叫處換成替身，重跑**真實** `line1_operations`（真實 `S_clip`、同一 c／d／direction）。
  兩情境：`sub_only`＝只換 `score/stock.py` 的名稱（子指標）；`with_median`＝另換 `fundamentals.py` 的名稱，以**真實**
  `FundamentalsBridge._industry_stats` 重算一份候選產業中位數（另建一個共用原料的橋），中期族 C 改用它。報：受影響列、
  初爻分數變化分布、陰陽翻轉兩向（50 為界、≥50 陽、只計兩側皆已知）、變未知／變已知、進／出 [45, 55]（兩端含）、各子指標
  x 符號翻轉與子指標分數變化；另報逐日逐產業的中位數改變次數與變化量。run-length 鍵另加候選中位數（現行中位數不變、候選中位數
  改變時不得沿用前一個 run；手造世界有測試守）。
- **守門**（任一不過 rc=2、不寫報告）：§29 的全部 A 守門；den 皆非負（且中位數未變、或現行缺值）的子指標替換前後 x／分數／缺值
  **逐位相同**，整列都沒有則初爻逐位相同；den < 0 的在場子指標替換後 x＝由本檔 num／den 以候選式算出的值、在場／缺值不變；
  兩個模組引用同一支 `revenue_yoy_3m`、替身確實被呼叫；候選中位數的產業集合與樣本數不變；num／den 為負時加總月份裡必有負值月；
  本檔記錄的 den＝§29 `line1_detail` 的 den；B 抽樣 parity。
- **B 縮小**：只抽「任一月份組 den < 0 或 num < 0」的列，每組至多 `--per-group`（預設 300），seed 20260924。理由：A 已是全母體
  parity，B 的用途是確認這些列在真實重播路徑上收到的子指標輸入（x／缺值原因／d）與 A 相同；其餘列 §29 已抽過。
  **B 空抽樣要明標**（驗收 `9825f4f` 補）：某組沒有負值列可抽時，json 的該組 `b_strata[].note` 與 txt 分層行寫「無負值列可抽、B 未執行」；
  整體 0 列時 `parity_b.executed=false`、`parity_b.note` 同字樣，txt 的 B 行寫「B 抽樣 parity：無負值列可抽、B 未執行」而**不寫「全數相符」**
  （`RB.run_b` 對空抽樣回 rows 0，照原句會讀成「0 列全數相符」）；部分組為 0 時 B 行另點名這些組。
- 報告只陳述數據，不寫成因推測、不寫建議。

### Hetzner 執行

```
tmux new -d -s revneg 'bash scripts/hetzner_revneg.sh'
```

仿 `hetzner_revbase.sh`：0 同步 main（工作樹不乾淨 rc=2；main 前進則以新版 re-exec）→ 1 守門（db 存在、`score_ranges.py --check`）
→ 2 `revenue_negative_base.py --db cache/scores.db --out runs/revneg/report_<TO>.json`（失敗或產物為空 rc=3）→ 3 報告 commit 到
`hetzner/revneg-<TO>`、`--force-with-lease` 推送（只放報告）。log 在 `cache/logs/revneg.log`。

**成本（推測，未實測）**：A 每個新 run 跑 `line1_detail`＋現行／`sub_only`／（中期）`with_median` 三到四次 `line1_operations`，
每個交易日多一次產業中位數；§29 生產（A＋B 合計）894 s、678 MiB。fresh-context 驗收（`9825f4f`）的縮比實測：本檔 `run_a` 耗時為
§29 `run_a` 的 **2.15 倍**，據此外推生產約 **18～32 分、約 0.8 GiB**——**推測、未在 Hetzner 實測**。本工具的生產耗時與記憶體以報告的
`elapsed_s`／`rss_peak_mib` 為準。

### 未知（本批不下結論）

- 生產資料上 den < 0／num < 0 的確切列數、股票、原始負值月數：**待 Hetzner 實跑**。
- 負值月是原始資料本身還是加總產生：由定義，den < 0 或 num < 0 必有至少一個負值月（守門即斷言這點）；原始表的負值月數量待實跑。
  負值月營收**在財報上代表什麼**（更正、沖銷或其他）本批不查、不推論。
- 候選式對方向分數、卦名、排序、候選名單與回測的影響**未量**（只量到初爻）。
- 字面式子在 den > 0 的逐位差異對 S 值與初爻的影響**未量**（只數列數）。

### 自測（合成資料）

`tests/test_revenue_negative_base.py` 54 支（52 個測試函式，其一參數化 3 例）。合成世界＝`synth_db.build_full`＋注入的月營收＋真實
`replay_scores` 80 日：1102（上市）2019-01／02／03＝−10／−10／−2、2020-01／02／03＝5／−2／−10、2018-10＝−40，其餘月 10 元；
6488（上櫃）2020-01／02＝−5／−30（den > 0、num < 0）；1103 正值（供字面式子的逐位差異），但 2020-01＝**0**（營收恰為 0：原始表與橋
不得算負值，短線單月 num＝0 的列不得算 num < 0）；2330（上市）補 2018～2019 各 5e9、其中 2019-10＝−1e11（每一列都受影響、240 列多於
1102 的 234 列，但只引用 1 個負值月、代號在 1102 之後——守股票清單排序鍵）；另一檔池外代號只進原始表。另有一個**無負值**的世界
（只有 `build_full` 的正值營收）驗 B 空抽樣的標示。

- **手算**：三個典型例（−10→+5 得 +150／現行 −150、−10→−2 得 +80／現行 −80、−2→−10 得 −400／現行 +400，現行值取真實
  `revenue_yoy_3m`）；1102 短線三個日期的 x 現行／候選逐位；波段三月 YoY（num 13、den −10）；加速度前組 den −20 時兩組相減。
- **列數對原始 SQL**：每個四類計數以「最新可用月 → 日期區間」手推後，用 SQL 數 db 列另算；比例分母＝組列數；手造 run 表驗
  四類、den＝0、權重與字面式計數。
- **den > 0 不動**：6488（den > 0、num < 0）兩情境受影響 0 列、x 與現行逐位相同；未受影響列的初爻逐位相同。
- **反事實彙總**：翻轉／進出帶／分數改變以逐列（原分數取 db 的 `line_1`）另算、兩向翻轉都有；手造表驗 50 與 45／55 兩端、
  變未知只計原本已知、變已知另列。
- **產業中位數情境**（手造 `FundamentalsBridge`，同產業 5 檔、族 C 在場）：現行中位數 20、候選 30（真實 `_industry_stats`）；
  den > 0 的 9002 族 C 在 `sub_only` 不變、`with_median` 改用 30；另一個世界現行中位數兩日相同而候選中位數 30→25，驗 run 鍵含
  候選中位數；無負值世界兩情境全等。
- **原始月營收／股票清單**：全表 10、池內 9、樣本段內 5／4、重播所見 9／4、引用 9（營收 0 的月一律不計）；清單順序
  2330、1102、6488（列數降冪），1102／6488／2330 的列數、as-of 月、負值月首末與值域、金融／產業彙總；清單截斷行為。
- **B 空抽樣**：無負值世界 → json `executed=false` 與字樣、txt B 行有字樣且無「全數相符」、六組分層行皆為字樣；部分組為 0 時 B 行點名該組。
- **守門各一支、紅的訊息對**：den > 0 字面式突變（守門 1）、初爻變而子指標未變、在場改變、替身未生效、子指標集合改變、兩模組非同一支、
  替身未被呼叫、產業樣本數／產業集合改變、負值加總無負值月、記錄的 den 與 §29 不符、候選橋改了別的輸入、`line1_detail` 重跑不符、
  run id 錯位、交易日軸、市場＝`pool.listed`、原始表缺、替身月份不齊、`--per-group` 下限（開跑前與抽樣兩處）、A／B parity；唯讀
  （db sha256 前後相同）、CLI 樣本段寫死。
- **Hetzner 腳本**以假 `python3`＋本機 bare repo 實跑：成功推 `hetzner/revneg-<TO>`（只含報告）、量測失敗 rc=3（即使留下產物也不推）、
  產物空 rc=3、登錄檔過期 rc=2 且不呼叫量測、db 不存在 rc=2、工作樹不乾淨 rc=2、步驟 0 git 失敗即停。
- **突變**：Python 47 個——守門 24（逐道改成空操作，含拿掉 A／B parity、B 改抽非負值列）＋計算 15（候選式在 den > 0 也改、
  候選式除以 den、翻轉界線 ≥→>、進帶改開區間、比例分母改可算基期列、den > 0 且 num < 0 漏 den > 0、變未知含原本未知、
  run 鍵漏候選中位數、中位數改變計數反向、股票列數不加權、股票 num < 0 列誤用 den、原始表樣本段不篩、「輸入改變」含現行缺值、
  `with_median` 不換中位數、字面式計數恆 0）＋驗收 `9825f4f` 補的 8（清單排序改負值月數／只依代號、原始表與橋的 `< 0` 改 `<= 0`、
  計數 `num < 0` 改 `<= 0`、B 空抽樣 `executed` 恆真／空組不標／部分空組不點名）；shell 8 個（工作樹、db、登錄檔、量測失敗 rc=3、產物空、body 的 `set -e`、rc≠0 不推送、
  `--force-with-lease`），**全數被抓到**。第一輪有四處沒抓到，都已補測：`--per-group` 在 `run()` 的開跑前檢查被抽樣那道補位
  （訊息改為可區分）、手造表沒有原分數恰為 45 的受影響列（進帶開區間）、手造表沒有原本就未知的列（變未知）、量測失敗被「產物空」
  那道補位（stub 改為失敗時也寫產物）。突變一律 `python -B`＋`PYTHONDONTWRITEBYTECODE=1`（§28 的 pyc 教訓）。
- 全套 `python -m pytest -q`（3.12）、`bash -n scripts/*.sh`、`score_ranges.py`／`stats_appendix.py`／`t717_appendix.py`／
  `apply_calibration.py` 的 `--check`、spec 工具鏈、`tblcheck docs/P3-CALIBRATION.md` 皆綠。
- **檔案模式與 lint 依 repo 慣例**：`scripts/*.py` 33 檔中 31 檔為 100644，本檔同為 100644；sys.path 之後的 import 保留
  `# noqa: E402`（lambda 保留 `# noqa: E731`，同 §29）。CI 不跑 ruff、repo 無 ruff 設定。**ruff 結果依版本**：0.15.8 下需
  `# noqa: E402`（新檔兩支與 §29 兩支皆乾淨）；0.16.7 預設規則集不含 E402，反而以 RUF100 標這些 `noqa` 為多餘——兩者不可兼得，依慣例取前者。

### 範圍外、記下不做

- 登錄書、附錄 C「待量測」節、`stats_appendix.py` 不動。
- 不提出、不實作任何門檻或候選式的正式版本；採不採用候選式待 Hetzner 實測後由使用者裁定。
- §29 的 `revenue_base_impact.py` 在 ruff 0.16.7 下有既有告警（0.15.8 下乾淨；本批未動該檔）。
