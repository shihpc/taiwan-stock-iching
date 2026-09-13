# P2 批二：重播驅動（第 13 項）架構方案

2026-09-13 動手前寫（CANON 第 3 條）。狀態：**§6 四題已裁定（全乙，同日；P2-KICKOFF §5 #34），開工中**。
本檔是設計正本；實作細節以程式 docstring 為準，衝突時以本檔 §1–§4 的**約束**為準、§5 的**估算**為參考。

## 0. 一句話

把 `features.db`（第 12 項）＋原料 DB 逐日餵進 `src/iching/score/`，產出 `scores.db`；
**Hetzner 全量回補與每日班共用同一個 `step(T)`**，兩層 parity 由構造保證、不靠事後比對。

## 1. 已查證的約束（不是設計選擇，是引擎與規格既定的）

| # | 約束 | 出處（實查） |
|---|---|---|
| 1 | 重播**必須嚴格逐日單向**，不可跨日平行、不可從中間冷起跑 | §B3.1 #7（遲滯要 T−1 state/streak）、#11（個股要 T−9…T−1 二爻分數、大盤要 T−5） |
| 2 | 首日遲滯：`prev_state=None` → 以 50 分界、streak 0 | `hexagram.py:113` `hysteresis_step`（v1.2.2 §8）——**不需裁定** |
| 3 | 個股 `line2_score_history[h]` **恰長 9、缺者 `None`**；長度不等於 9 整段視為缺 | `stock.py:524-526`；缺值行為由 `Rules.avg_min_available_ratio`（§5 #19）決定——**不需裁定** |
| 4 | 同日內順序：兩市場 `score_market` → 兩市場遲滯 → `basic_state(l1,l2)` → **兩市場互為 `other_market_state`** → `market_flags` → 個股（吃 `market_direction_score`） | `market.py:580` F-分歧、`stock.py:657` B2.6 族 A |
| 5 | `assemble_row` 的 7 個鍵由 `spec/dimensions.json` 決定；`formal_lines` 由驅動端算；**`model_version` 一定要傳入**（否則每列重算指紋 5 ms，是計分本身的 2.5 倍） | `assemble.py:68`、實測 5.02 → 0.01 ms/列 |
| 6 | 個股價量必須**後復權**（B2.0 政策 2：價格與 ATR 同口徑）；`fundamentals.price_at_period_end` 例外用原始價 | `adjust.py` 模組 docstring；**現況 `score_io.load_stock_ohlcv` 是原始價**（跨批次斷點，本項修） |
| 7 | 官方口徑單位：BFI82U／TPEx 法人 → **千元**；FMTQIK `turnover_k`／tradingIndex → **千元** | `official_parse.py:95-166`。**引擎端 `foreign_net_amount`／`amount` 期望的單位待接線時逐一核對**（單位錯不報錯） |
| 8 | 暖機兩軌：價格類 250、基本面類 400 交易日；評估樣本最早日期不得落在暖機期 | `docs/pre-registration.md` §1.1；§B3.3「暖機隔離」。價格資料 2020-01 起只有 245 日，前 5 個訓練日 `P_hist(250)` 會缺——**已預先登錄，不重議** |
| 9 | `text_version = "0.2"`；`data_version = fm-20260911-01`；`model_version` 每市場各一（`ps.model_version()`） | `spec/P1-B4-hexagram-text.md:127`、§B3.1 #9 |
| 10 | 記憶體峰值 < 1.5 GiB；視窗不得用 `timedelta(days=N)`；`P_cs` 套 `N` 次數為 0 | §B3.3 |

## 2. 架構：`ReplayState` ＋ `step(T)`

```
                 ┌─────────────── ReplayState ───────────────┐
                 │ CrossDayState（小、可序列化、進 git）        │
                 │   遲滯 (state, streak) × market×h×stock×line │
                 │   line2 history: 個股 9 筆／大盤 5 筆 × h     │
                 │   AdvTracker（排名池，60 日成交值）            │
                 │ WindowCache（大、可由原料重建、不進 git）       │
                 │   個股 320 日 OHLCV(後復權)＋法人＋融資＋借券   │
                 │   大盤 320 日指數 OHLC＋官方成交金額＋法人金額  │
                 │   ＋期貨 OI／基差＋VIX＋美股／匯率（美股曆）    │
                 └────────────────────────────────────────────┘
                                   │
   原料 DB(T) ──┐                  ▼
   features.db(T) ┴──→ ingest(T) → step(T) → scores.db 列 ＋ CrossDayState(T)
```

- **`step(T)` 是唯一的計分入口**。Hetzner 回補＝`for T in 交易日: step(T)`；每日班＝載入 `CrossDayState(T−1)`、
  由當日 API 重建 `WindowCache`、`step(T)` 一次、存回 `CrossDayState(T)`。**兩層跑的是同一個函式**——
  這就是 §B3.2 parity 不變式的實現方式，parity 測試只是驗「WindowCache 兩種來源重建後相同」。
- **`CrossDayState` 刻意只放不可重建的東西**（遲滯、歷史分數、ADV 視窗）。§B3.2 說每日班的 git 狀態
  「能多小取決於各族的暖機窗、本節不預先猜」——實測估算 < 5 MB（2,139 檔 × 3 h × 6 爻 ×(state,streak) ＋ 9 筆分數 ＋ 60 筆成交值）。
  價格視窗 55 MB 級、每日重建，不進 git。
- **資料讀取＝逐日點查詢**（每表 `WHERE date=?` 走 date 索引），不做 k-way merge、不整表載入（§B3.2 第 4 點）。
  1,618 日 × ~15 表 ≈ 24k 次索引查詢，相對 1,000 萬次 `score_stock` 可忽略。啟動時檢查各表 date 索引存在，缺即拒跑。
- 個股序列對齊沿用 `score_io.stock_inputs_from_stores` 的既定語意：只取「該股有成交且指數有列」的日期，
  `index_close` 逐日對齊；法人無列補 0（既定假設）、餘額無列補 NaN。

## 3. `scores.db`

比照 `features_io.py`（四條 PRAGMA、複合 PK ＋ `WITHOUT ROWID` ＋ date 索引、逐日一交易、逐日先 DELETE 再 INSERT）。

| 表 | 鍵 | 內容 |
|---|---|---|
| `scores` | `dimensions.json` 的 7 鍵（`market, horizon, stock_id, tpe_trading_date, model_version, data_version, text_version`），大盤列 `stock_id='__MARKET__'` | `assemble_row(detail=False)` 的全部欄位（43 欄）攤平：純量進真欄，`lines_*`／`flags` 進 JSON TEXT；另加 **`streaks` TEXT（6 個 int）** 與 **`in_rank_pool` INTEGER**（個股列） |
| `replay_day` | `(data_version, model_version_twse, model_version_tpex, date)` | 當日診斷：算了幾檔、幾檔在池、缺值家數、耗時、`index_missing` |
| `replay_meta` | `data_version` | 參數指紋（兩市場 `model_version`＋`text_version`＋視窗設定），不一致拒寫（同 `features_io.set_params`） |

**遲滯狀態不另立表**：T−1 的 `lines_formal`＋`streaks` 就在 `scores` 列裡，`CrossDayState.load(T−1)` 從那裡讀。
另立 `hysteresis_state` 會是 6,200 萬列（10.4M 列 × 6 爻），不值得。

**版本三元組的儲存形式待裁定**（§6 Q2）——第 12 項的教訓：`data_version` 14 字元進 `WITHOUT ROWID` 的 PK 又被兩條索引各帶一份，
每列多 43 bytes。這裡三個版本字串合計約 60 字元、1,040 萬列，字面儲存約 **+1.9 GB**。

## 4. 同日順序（`step(T)` 內部）

```
ingest(T)                                  # 各表 date=T 點查詢 → 更新 WindowCache；讀 features(T)
pool_T = adv.eligible()                    # PIT：先取（T−1 為止）
for mk in (twse, tpex):
    mi = MarketInputs(mk, T, WindowCache, features, line2_t_minus_5=cross.market_line2[mk][h])
    ms[mk][h] = score_market(mi, ps[mk], h)          # 3 horizon
    lines_formal[mk][h] = hysteresis(cross.market_state[mk][h], ms[mk][h].line_scores())
    own_state[mk][h] = basic_state(lines_formal[mk][h][0], [1])
for mk: flags[mk][h] = market_flags(ms[mk][h], mi with own_state[mk][h], other_market_state[~mk][h])
write market rows（6 列）
for sid in universe(T):                    # §6 Q1：全普通股 或 只排名池
    si = StockInputs(sid, T, WindowCache, features(T), line2_history=cross.stock_line2[sid][h](9 筆 None 補齊),
                     market_direction_score={h: ms[mk][h].direction_score})
    ss[h] = score_stock(si, ps[mk], h); lines_formal = hysteresis(...); push line2 today
    write stock rows（3 列，in_rank_pool = sid in pool_T）
adv.push_day(T, amounts)                   # PIT：後推
cross.market_line2 推進；cross 存檔（可選，供中斷續跑）
```

## 5. 成本估算（**分段量測相加，上一批已證明這種外推會低估**；只當數量級）

| 段 | 實測（本容器，合成 320 日輸入） |
|---|---|
| `score_market` × 3 h ＋ `market_flags` × 3 | 2.5 ms／市場／日 |
| `score_stock` × 3 h | **6.3 ms／檔／日** |
| `assemble_row`（`model_version` 傳入） | 0.01 ms／列 |

- 全 2,139 檔：13.5 s／日 → **1,618 日 ≈ 6 小時**（本容器）；只排名池 ~900 檔：≈ 2.6 小時。
- 讀取＋落地依第 12 項經驗另加 0.2–0.5 s／日（≈ 10 分鐘）。
- Hetzner CPU 與本容器不同，方向未知。在 tmux 跑、`--limit-days 20` 先量真實每日成本再決定要不要開分段。
- 記憶體：`WindowCache` numpy 約 55 MB（2,139 × 320 × 10 欄）＋ `CrossDayState` < 5 MB ＋ features 當日切片。**遠低於 1.5 GiB**。
  基準 fixture 用相異浮點值量（`[1.0]*N` 會因 interned 物件低估 3.5 倍，2026-09-13 實測）。
- `scores.db` 大小：10.4M 列（2,139 × 3 × 1,618）；依 §6 Q2 決定，粗估 **1.5–3.5 GB**。Hetzner 現有 19 GB 可用。

## 6. 四題裁定（2026-09-13 使用者裁定**全乙**；表列「建議」欄保留為當時的提案）

| Q | 題目 | 甲 | 乙 | 建議 |
|---|---|---|---|---|
| 1 | **計分範圍** | 只算當日排名池（~900 檔，2.6 h，DB 0.4×）；股票進出池時遲滯與二爻歷史從零起算，池邊界有暖機殘影 | **全 2,139 檔普通股**，列上帶 `in_rank_pool` 旗標，評估層再篩（6 h，DB 1×） | **乙**：池每日進出數檔，甲會讓那幾檔的四爻族 A 連續 9 日缺值、正式爻態重新初始化——那是實作造成的假訊號，不是市場的 |
| 2 | **版本三元組儲存** | 照 `dimensions.json` 字面 7 欄全存字串（每列 +~60 bytes，約 +1.9 GB） | `versions(version_id, model_version, data_version, text_version)` 一張小表，`scores` 存 `version_id` INTEGER；讀取端 JOIN 還原 7 鍵 | **乙**：邏輯鍵不變（`row_key()` 讀取端仍回 7 鍵），只是物理儲存正規化；第 12 項實測字串進 PK 被索引乘 3 的代價 |
| 3 | **`shares_outstanding` 無任何資料集** → 五爻族 E 永遠缺值（`stock.py:591-604`） | 接受缺值，先跑 | 另案回補 `TaiwanStockShareholding.NumberOfSharesIssued`（taiwan-flows 已在用同一欄，`config.py` 21 個 spec 裡沒有） | 我提**甲先乙另案**、**裁定乙**（`config.py` 已加 `shareholding`）：不擋本項；但要記進 §5 裁定表，否則校準時五爻權重會在缺一族的狀態下定案 |
| 4 | **基本面軌拆成 13b** | 13a 先跑，`monthly_revenue`／`fundamentals`／`industry_median_3m_yoy` 全 None → `horizon=mid` 個股初爻整條缺值 | 等 13b 一起 | 我提**甲**、**裁定乙**：13b 需要先在 Hetzner 探 `raw_financial_statements.origin_name` 的實際值才能寫 FinMind `type` → 9 個鍵的對應，那是另一輪「我寫探測、你跑」；13a 的 parity／決定性驗收不依賴它 |

**不列為裁定、但要記錄的已知缺口**：§B3.1 #6 事件版本鏈（`config.OUT_OF_SCOPE` ①，整個 events.db 未做，相關族 E＝0）；
`put_call_ratio` 只顯示、給 None；裁定 #6 說「可計分標的含 ETF」但目前池是普通股（features.db 也無 ETF），ETF 計分留待另案。

## 7. 驗收（§B3.3 逐項對應）

| §B3.3 條件 | 本項怎麼驗 |
|---|---|
| 決定性（抽 5 日重播） | 合成 DB 全量跑兩次逐位相同；**另從 `CrossDayState(T−1)` ＋重建 `WindowCache` 單步 `step(T)`，與全量跑的第 T 日列逐位相同**（這就是每日班路徑，parity 的本體） |
| 暖機隔離 | `replay_day` 記每日「P_hist 可得檔數」；測試斷言評估段起點（2021-01-01）之前不寫入評估用旗標——實際隔離由下游評估層按日期篩 |
| 版本綁定 | 改一個 `Rules` 欄位重跑同一日 → 列必須不同（`test_score_params_guard` 已有指紋層，這裡驗到落地層） |
| 值域映射一致／`P_cs` 不套 `N` | `p_cs_long_excess` 從 features.db 原樣進 `StockInputs`，測試斷言落地 meta 裡的值 ＝ features.db 的值 |
| 記憶體 < 1.5 GiB | 驅動腳本每 200 日印 RSS；測試用 `tracemalloc` 驗 `WindowCache` 建構量級 |
| 交易日 vs 曆日 | AST／grep 靜態檢查：`src/iching/replay*.py` 不得出現 `timedelta(days=` |
| 事件 as-of | **本項不驗**（events.db 未做），明記 |

另加本專案慣例：fresh-context 驗收綁 commit；每條守門做突變測試；合成 DB 實跑整支腳本。

## 8. 交付切分（依裁定調整：13b 與 `shareholding` 回補改為**平行、且擋 Hetzner 全量跑**）

0. ~~**先交給使用者平行跑**：`shareholding` 回補（`config.py` 已加 spec）＋ `scripts/probe_fundamentals.py`（13b 的對應表靠它）。~~ **已完成（2026-09-13，`P2-KICKOFF.md` §5 第 35 列）**：shareholding 3,398,640 列落地（`reindex` 待跑）；對應表定案見 §9；**新增一題待裁定（§9 Q5：淨值 QoQ 無來源）**。

1. **13a-1** `replay_state.py`：`CrossDayState`（序列化＋載入）、`WindowCache`（含後復權接線）、`ingest(T)`。純函式層不 import sqlite3；I/O 在 `replay_io.py`。
   **已交付（2026-09-13）**：`src/iching/replay_state.py`（`DayBundle`／`CrossDayState`／`WindowCache`／`Ring`）＋ `src/iching/replay_io.py`（`ReplaySource.read_day(T)`：15 張表逐日點查詢，含官方 JSON 解析、FMTQIK／tradingIndex 月表快取、TX 近月基差、VIX 末筆、美股／匯率增量）＋ `features_io.FeatureStore(readonly=True)` 與 `day_breadth／day_industry／day_p_cs` 讀取器＋ `liquidity.AdvTracker.state()/from_state()`；測試 `tests/test_replay_state.py`（18 條，fixture `tests/synth_db.build_full`）。**實作時定下的三個語意**（都寫在 `replay_state.py` docstring）：①個股序列只在「有成交且所屬市場有指數列」推進（`score_io` 連停牌列也算，本檔與 `feed.day_records` 同尺）；②籌碼沿用 `score_io.aligned()`——視窗內完全無列→整欄 None、部分缺→法人補 0／餘額 NaN；③**`ad_line` 在視窗內從 0 起算**，否則每日班（只重建 320 日）與全量跑差一個常數、`(AD − MA)` 的浮點結果不逐位相等（測試 `test_window_rebuilt_from_last_n_days_is_bitwise_identical` 守）。`score_io.load_stock_ohlcv` 仍為原始價、只供量測對照，未動。
2. **13a-2** `replay_step.py`：`step(T)` 同日順序（§4）；`scores_io.py`：schema／writer。
3. **13a-3** `scripts/replay_scores.py`：CLI（同構 `scan_features.py`：`--from/--to/--limit-days/--rebuild/--resume`，暖機拒跑）＋ `check_scores.py` 健檢。
4. **13b** 基本面橋（需 Hetzner 探測）。
5. Hetzner 實跑 → 健檢 → §5 歸檔。

## 9. 13b 對應表與新開的裁定（2026-09-13，依 Hetzner 探測實值）

`raw_financial_statements`（`date`＝期別末日、值＝**單季、元**，實證見 §5 第 35 列 ③）→ `StockInputs.fundamentals`：

| 鍵 | 來源 `type` | 期別 | 備註 |
|----|-------------|------|------|
| `eps` | `EPS` | 最近一期 `date ≤ available_at 對應期` | origin 兩種寫法同 type，不分 |
| `eps_ly` | `EPS` | 早 4 期 | 同 `stock_id` 同 type，缺任一期→缺值 |
| `gross_margin` | `GrossProfit` ÷ `Revenue` ×100 | 最近一期 | `Revenue ≤ 0` → 缺值（`REASON_DENOM_ZERO`） |
| `gross_margin_prev_q` | 同上 | 早 1 期 | |
| `pretax_income` | `PreTaxIncome`，缺則 `IncomeBeforeIncomeTax` | 最近一期 | 後者是金融業寫法（4,769 列）；兩者同期同時存在時取 `PreTaxIncome` |
| `pretax_income_ly` | 同上 | 早 4 期 | |
| `price_at_period_end` | `raw_price_daily.close` | 期末日或其前最近交易日 | **原始價**（B2.0 政策 2 的明文例外） |
| `equity` ／ `equity_prev_q` | **無來源** | — | 見 Q5 |

**季報可得日（`available_at`）**：沿用 B2.1 法定期限口徑（Q1 5/15、Q2 8/14、Q3 11/14、年報 3/31），與月營收同一套「不用 `create_time`」的理由（83% 空值）。

**Q5（2026-09-13 使用者裁定**乙**，P2-KICKOFF §5 #36 已落地）：金融保險業替代規則的「淨值 QoQ」（`spec/P1-B2-params.md:146`，c=0／d=2%）沒有資料來源。**
`raw_financial_statements` 是純損益表，`EquityAttributableToOwnersOfParent` 實為淨利分配（§5 第 35 列 ②）。
- **甲**：新增資料集（FinMind `TaiwanStockBalanceSheet`，**未實測**是否存在／欄位／權限，需再一輪探測＋回補；
  只影響 56 檔金融股，可 per_stock 只補這 56 檔）。
- **乙**：接受該子項缺值——金融股族 B 只剩「稅前淨利 YoY」一項（族內權重 1.0），`coverage_ratio` 照通用規則計；
  在 `params.py` 記 SPEC-NOTE 綁進 `model_version`。
- 我提**乙**：56 檔／2,139 檔、且只動族 B 的一半，代價是金融股初爻少一個維度；甲要再一輪 Hetzner 往返且 FinMind 該資料集存在與否是猜的。
- **裁定乙**：`params.py` `equity_qoq` 的 `source_dataset`／`missing_rule` 記錄此裁定並綁進 `model_version`（twse `…f7b0f6e1d71b`／tpex `…e7581159c2e2`）；13b 的 `equity`／`equity_prev_q` 固定給 `None`。

