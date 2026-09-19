# P4 預覽版網頁（2026-09-19 使用者裁定「要」：與 P3 第 3～4 項並行）

> **性質**：預覽版，**不是** v1.2.2 §14 的 P4 正式交付。目的＝讓使用者提前看到卦與六爻的呈現、提早給版式意見；
> 數字在校準（P3 第 3 項）後**會變**。規格 §14 的順序（P3 → P4）被打破一事由使用者 2026-09-19 裁定，本檔即其紀錄。
> 依 CANON 第 3 條，本檔在動任何程式前寫成；驗收綁確切 commit。

## 0. 範圍（一次只做一件事）

**做**：
1. `scripts/build_web.py`：由 `data/scores/<T>.json`（＋`data/pool.json` 股名）產兩份輕量檔到 `data/web/`——
   `latest.json`（最新交易日全部個股×三期間＋大盤列的展示欄位）與 `timeline.json`（最近 `N=20` 交易日每檔每期間的卦序與爻態，
   供「換卦日」標示）。純標準庫、決定性（同輸入同輸出、鍵排序、無時戳以外的浮動）。
2. `index.html`（單檔、CSS/JS 內嵌、GitHub Pages 由 main root 服務）兩個入口：**觀大勢**（加權／櫃買 × 三期間的正式卦、六爻、遲滯天數）、
   **診個股**（代號查詢 → 三期間切換 → 正式卦／暫定卦、六爻分數與狀態、遲滯連續天數、覆蓋率、最近 20 日換卦紀錄）。
3. `daily.yml` 在每日班之後、commit 之前加一步 `build_web.py`（產物在 `data/`，既有 `git add data` 會一起收）。
4. `docs/P4-PREVIEW.md`（本檔）＋ README「進度」表更新（「授權邊界」段不需動：仍無 cron、未動入口站；Pages 開通是使用者親手事項）。

**不做**（明列，避免範圍擴張）：「選多空」「懂卦理」兩入口、日 K、事件層、方向分數／候選名單（§13.3a）、卦辭原文（無實檔）、
Worker 整合、入口站卡片（README 授權邊界「改既有五站或入口站」未取得）、cron（`daily.yml` 維持只有 `workflow_dispatch`）、
ES modules 拆檔（§12.1 寫「ES modules 拆檔」是正式版要求；預覽版沿姊妹站單檔慣例，正式版再拆）。

**需使用者親手做的一件事**：GitHub repo Settings → Pages → Source 選 `main` / `(root)`。本 session 無法代開（無該 API 權限），
開了之後線上位置預期為 `https://shihpc.github.io/taiwan-stock-iching/`（**推測**，以實際 Pages 設定頁顯示為準）。

## 1. 資料契約（`data/web/`）

**`latest.json`**（估算：5,841 列 × 約 12 欄，原始 ≈ 1.2 MB、gzip ≈ 150 KB；**實測 2026-09-19，14 日資料**：1,319,659 bytes、gzip -9 178,637 bytes）：
```
{ "schema": 1, "date": "2026-09-18", "data_version": "...", "params_sha": "...", "text_version": "0.2",
  "calibrated": false, "generated_from": "data/scores/2026-09-18.json", "n_rows": 5841,
  "names": { "2330": ["台積電", "半導體業"], ... },              // 只含 rows 出現的代號；來源 data/pool.json
  "market": { "twse|short": {...}, "twse|swing": ..., "tpex|mid": ... },   // 6 筆大盤列
  "stocks": { "2330": { "market": "twse", "in_rank_pool": 1,
                        "short": {...}, "swing": {...}, "mid": {...} }, ... } }
每筆 {...} ＝ { "kw": king_wen|null, "name": hexagram_name|null, "kwp": king_wen_provisional, "namep": hexagram_name_provisional,
              "lf": lines_formal|null, "lp": lines_provisional, "st": line_states, "sk": streaks,
              "l": [line_1..line_6]（各 1 位小數）, "unk": [line_1_unknown..line_6_unknown], "cov": coverage,
              "bs": base_score（**只有 short 帶**；swing／mid 一律省略，§13.3a）}
```
**`timeline.json`**（最近 20 交易日；估算原始 ≈ 3 MB、gzip ≈ 400 KB——超過 500 KB gzip 就把 N 降到 10；**實測 14 日**：1,227,012 bytes、gzip -9 65,062 bytes，遠低於門檻、N 維持 20）：
```
{ "schema": 1, "dates": ["2026-08-21", ..., "2026-09-18"],      // 升冪，＝data/scores/ 現有檔取最後 N 個
  "series": { "2330|short": [[kw|null, "yynnyy"], ...], ... } }  // 與 dates 等長；該日無列＝null
```
兩檔都**不含** `cross.json` 任何內容、不含 `flags`、不含 `adv`。`build_web.py` 讀不到某日分數檔＝該日 null，不中止；
`latest.json` 取 `data/scores/` 檔名最大者。`calibrated` 由分數列的 `calibrated` 欄 all-equal 判定（目前全 0 → false）。

## 2. 頁面義務（規格條文對應，逐條可勾）

| # | 義務 | 出處 |
|---|---|---|
| P1 | 免責固定置頂：「預覽版・未經回測驗證・參數未校準（calibrated=false）・陰陽不是買賣指令、不建吉凶排名・AI 研判非保證」 | §10:441、§2:89、§13.3a:588 |
| P2 | 波段／中期**不顯示** `base_score`、不顯示任何方向分數、不列候選名單；短線的 `base_score` 只在展開區顯示並標「未校準」 | §13.3a:588-590、§16:726 |
| P3 | 正式卦與暫定卦**分區標示**；`lines_formal` 為 null 時顯示「六爻尚未全部確立」而非空白 | §10:441、§8 |
| P4 | 每爻顯示：面向名（§2 表）、分數、陽／陰／未定（`line_states`）、連續確認天數（`streaks`）、未知（`unknown`）、覆蓋率；用語「較支持上行／支持不足或偏弱」，不出現「看多／看空／買／賣」 | §2:89-92 |
| P5 | 「轉弱／轉強」只在換卦（timeline 相鄰日 kw 不同）時出現，標「換卦日」 | §2:91 |
| P6 | 分數不得表述為機率；頁面不出現「機率」「勝率」字樣 | §5:275 |
| P7 | 資料日、`data_version`、`params_sha`、`text_version`、本站更新（`loadSiteVer` 四站慣例，key `ic_site_ver`）在頂列 | 家族慣例 |
| P8 | 個股列不在排名池（`in_rank_pool=0`）時標「未達流動性門檻（60 日成交值 <3,000 萬）」 | `daily_core.py:404-408` |

## 3. 工程約束（家族慣例）

- CSP meta 比照 taiwan-stock-news `index.html:11`，`connect-src 'self' https://api.github.com`（只抓同源 `data/web/*.json` 與 `loadSiteVer`）。
- 所有進 `innerHTML` 的外部字串（股名、產業、卦名）過 `esc()`；`stock_id` 只允許 `^[0-9A-Z]{4,6}$`。
- fetch 走 `fetchFresh()`（`cache:"no-cache"`），`CACHE_BUST=false` 回退開關同姊妹站。
- hash 路由 `#tab=market|stock&code=&h=short|swing|mid`，白名單＋型別檢查、非法值靜默退回、寫出走 `history.replaceState`。
- 手機：表格包 `.tblwrap`；375／390／1280 三寬度 `document.documentElement.scrollWidth <= innerWidth`。
- 零外連 script／字型；無 `on*=` 屬性；無 `eval`。
- `build_web.py` 失敗**不得讓每日班的計分 commit 被 skip**：`daily.yml` 該步驟用「記 `ok=0`、commit 後再轉紅」模式（同 taiwan-flow-live-v2 `intraday.yml` 慣例，理由：分數與原料包才是主產物），**不用 `continue-on-error`**。

## 4. 驗收條件（綁 commit；修改者不得自驗）

- A. `python scripts/build_web.py --root .` 對現行 `data/scores/`（14 日）產出兩檔；`latest.json` 的 `n_rows`＝分數檔 `rows` 數、`stocks` 代號數＝分數檔個股代號數、
  每檔三期間齊或缺者明確為缺；`swing`／`mid` 物件**沒有 `bs` 鍵**；`timeline.json` `dates` 長度＝min(N, 現有日數)、每個 series 長度＝dates 長度；
  跑兩次輸出逐位相同（決定性）；兩檔 bytes 與 gzip bytes 記回本檔 §1。
- B. `tests/test_build_web.py`：合成分數檔（含 `lines_formal` null、`in_rank_pool` 0、缺某日）→ 結構斷言＋ §13.3a 斷言（swing/mid 無 `bs`）＋決定性斷言；
  `pytest tests -q` 全綠、ruff 對新檔零項。
- C. `index.html` 以 Playwright（本機 `http.server`）驗：①兩入口各 3 期間切換 console 零 error、零 `Refused to`；②免責卡在首屏且文字含「未經回測驗證」「不是買賣指令」；
  ③診個股輸入 `2330`／不存在代號／非法字串三種輸入的行為（顯示、提示、不炸）；④swing／mid 畫面 DOM 內不出現 `base_score` 數值（用一個已知 `bs` 值 grep innerText）；
  ⑤股名注入樣本（`pool.json` 替換成含 `<img onerror>` 的 fixture，經 `page.route`）不執行、以字面顯示；⑥375／390／1280 無頁面級水平捲軸；
  ⑦hash `#tab=stock&code=2330&h=mid` 直開落在正確畫面，非法 `h=xxx` 退回預設；⑧頁面 innerText 不含「機率」「勝率」「看多」「看空」「買進」「賣出」。
- D. `daily.yml` 改動：`yaml.safe_load` 過；步驟順序 每日班 → build_web → commit；build_web 失敗時 commit 步驟仍執行、job 最後才紅（以 `act` 不可用，用結構檢查＋讀 shell 邏輯驗）。
- E. fresh-context 驗收綁 PR head；使用者合併後：下一班每日班綠且 commit 含 `data/web/latest.json` 更新；Pages 開通後線上 URL 開得出兩入口（**線上驗證由使用者實機確認**，本沙箱瀏覽器連不到外網）。

## 5. 未知／風險

- `data/scores/` 只有 14 個交易日，timeline 首版最多 14 點。
- 個股列 `flags` 缺席、大盤列有——預覽版不用 `flags`。
- Pages 一旦開通，repo 內**所有檔**（含 `data/backtest/` 100 MB、`runs/`）都可被 URL 直接取用；本 repo 無機密檔（`cache/` 不進 git），但 `data/events/`
  與 `runs/collect/` 的原料包會變成可公開下載——這些本來就是 public repo 內容，只是取用門檻降低。若使用者介意，Pages 改用 `gh-pages` 分支只放前端與 `data/web/`（需改 workflow，另案）。
