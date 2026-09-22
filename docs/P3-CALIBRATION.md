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
| ③ 各旗標觸發率 | **有** | 大盤列 `flags.by_direction[dir].active[flag]`；個股過熱走 §18 的 `overheated` |
| ⑦ 候選名單與排名重疊率 | **有** | `base_score`／`in_rank_pool` ＋ 大盤 `by_direction` 的 `quota_multiplier`／`threshold_shift_deciles` |
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
| ⑥ | 主卦與之卦一致率 | 主卦＝`king_wen`；之卦由 `hexagram.py` 的 `to_king_wen(king_wen, 動爻位)` 現算（非落地欄）。前後側逐日比對兩者是否相同 | `king_wen`、`lines_formal` |
| ⑦ | 候選名單與排名重疊率 | 逐日取 `in_rank_pool=1` 的列依 `base_score` 排序，取前 N（N 由大盤 `quota_multiplier` 決定）；前後側名單的 Jaccard ＋ 排名 Spearman | `base_score`、`in_rank_pool`、`flags.by_direction` |
| ⑧ | 封頂／下限 binding 率 | `floor_applied=1` 的列數 ÷ 非 None 列數；`overheat_cap_applied` 同理。**分母排除 None**（§18 已記：`floor_applied` 的 None 混四種成因，分母實為「創高日 ∩ 族 A 有分數日」） | §18 三欄 |

**門檻**：任一項差異 > 10% 須在登錄文件說明原因並確認是預期行為（規格原文）。

### 驗收條件（先寫，改的人不得自驗）

| # | 條件 | 怎麼驗 |
|---|------|--------|
| F1 | 八項都真的算得出來，且每項的分組鍵與上表一致（六項 `direction="n/a"`） | 對合成的兩份小 db 實跑，逐項檢查輸出形狀 |
| F2 | **同一份 db 自己對自己比 → 八項差異全為 0**（②④ 的「次數／分布」則兩側相同） | 拿同一個 db 當前後側跑一次，斷言全零；這是最基本的自洽檢查 |
| F3 | 每一項都能被對應的人工擾動打出非零 | 逐項造一個只動該項來源欄位的合成差異，斷言只有該項變動 |
| F4 | ⑧ 的分母**排除 None**（不得把 None 當 0） | 造含 None 的列，斷言分母與 §18 的定義一致 |
| F5 | ⑥ 的之卦是現算而非讀欄；動爻位取自 `lines_formal` 相鄰日差 | 以已知卦例手算對照 |
| F6 | 報告同時輸出 JSON 與純文字，數字一致；>10% 的項目自動標記 | 實跑後比對兩份 |
| F7 | 全量測試綠、ruff 零新增項、`spec/tools` 四支綠 | 實跑（ruff 用 `origin/main` worktree 比） |
