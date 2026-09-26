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
5. **「懂卦理」第三個分頁**（2026-09-26 使用者裁定，原列「不做」；規則見 §10）：內容四項＝①六十四卦卦爻辭 ②卦理入門
   ③本站六爻對應 ④今日卦分布；瀏覽＝8×8 上下卦格＋卦序清單可切換。純前端、`build_web.py` 與 `data/web/` 不動。
6. **持股卦象一覽（唯讀 `pm_holdings`，2026-09-26 使用者指定；規則見 §11）**：「診個股」tab 頂部一個區塊「我的持股」，只讀同 origin
   `localStorage["pm_holdings"]`（postmkt 持股診斷寫入）的代號 `c`，逐列顯示既有「診個股」輸出（正式卦名＋六爻圖）。本站唯讀、不排序、
   不評價、不顯示任何分數；純前端、`build_web.py` 與 `data/web/` 不動。

**不做**（明列，避免範圍擴張）：「選多空」入口、日 K、事件層、方向分數／候選名單（§13.3a）、卦辭原文（無實檔；
後由 §7 裁定 #52 補上 `data/hexagram_text.json`）、Worker 整合與入口站卡片（**原列不做；2026-09-26 使用者授權後已完成**：live-v2 `/status` 第七站 `iching`、Hub 第 6 張卡，見各該 repo PR）、cron（`daily.yml` 維持只有 `workflow_dispatch`）、
ES modules 拆檔（§12.1 寫「ES modules 拆檔」是正式版要求；預覽版沿姊妹站單檔慣例，正式版再拆）。

**需使用者親手做的一件事**：GitHub repo Settings → Pages → Source 選 `main` / `(root)`。本 session 無法代開（無該 API 權限），
開了之後線上位置預期為 `https://shihpc.github.io/taiwan-stock-iching/`（**推測**，以實際 Pages 設定頁顯示為準）。

## 1. 資料契約（`data/web/`）

**`latest.json`**（估算：5,841 列 × 約 12 欄，原始 ≈ 1.2 MB、gzip ≈ 150 KB；**實測 2026-09-19，14 日資料**：1,319,659 bytes、gzip -9 178,637 bytes）：
```
{ "schema": 1, "date": "2026-09-18", "data_version": "...", "params_sha": "...", "text_version": "0.2",
  "calibrated": false, "generated_from": "data/scores/2026-09-18.json", "n_rows": 5841,
  "model_version": { "twse": ["p2-score-engine-2.01697576a7b0"], "tpex": ["p2-score-engine-2.83b5c5dfdb23"] },  // §9 W2（2026-09-26 新增）
  "names": { "2330": ["台積電", "半導體業"], ... },              // 只含 rows 出現的代號；來源 data/pool.json
  "market": { "twse|short": {...}, "twse|swing": ..., "tpex|mid": ... },   // 6 筆大盤列
  "stocks": { "2330": { "market": "twse", "in_rank_pool": 1,
                        "short": {...}, "swing": {...}, "mid": {...} }, ... } }
每筆 {...} ＝ { "kw": king_wen|null, "name": hexagram_name|null, "kwp": king_wen_provisional, "namep": hexagram_name_provisional,
              "lf": lines_formal|null, "lp": lines_provisional, "st": line_states, "sk": streaks,
              "l": [line_1..line_6]（各 1 位小數）, "unk": [line_1_unknown..line_6_unknown], "cov": coverage,
              "bs": base_score（**只有 short 帶**；swing／mid 一律省略，§13.3a）,
              "ti"／"to": inner／outer_trigram_score（1 位小數，缺即 null；**同樣只有 short 帶**，§6 S2-4／F1）}
```
**`timeline.json`**（最近 20 交易日；估算原始 ≈ 3 MB、gzip ≈ 400 KB——超過 500 KB gzip 就把 N 降到 10；**實測 14 日**：1,227,012 bytes、gzip -9 65,062 bytes，遠低於門檻、N 維持 20）：
```
{ "schema": 1, "dates": ["2026-08-21", ..., "2026-09-18"],      // 升冪，＝data/scores/ 現有檔取最後 N 個
  "ps": ["c7385e78cb9f", ..., "8ca174ee8bc7"],                  // §9 W1（2026-09-26 新增）：與 dates 等長，該日分數檔頂層 params_sha；讀不到／壞檔／缺欄＝null
  "series": { "2330|short": [[kw|null, "yynnyy"], ...], ... } }  // 與 dates 等長；該日無列＝null
```
兩檔都**不含** `cross.json` 任何內容、不含 `flags`、不含 `adv`。`build_web.py` 讀不到某日分數檔＝該日 null，不中止；
`latest.json` 取 `data/scores/` 檔名最大者。`calibrated` 由分數列的 `calibrated` 欄 all-equal 判定（目前全 0 → false）。
`model_version`＝該日各市場列（含大盤列）出現過的 `model_version` 去重升冪，兩個市場鍵一律存在（無列＝空陣列）；
`ps`／`model_version` 兩欄**只新增、不改既有鍵與位元組**，舊檔沒有這兩欄時前端不出任何換版標示（§9）。

## 2. 頁面義務（規格條文對應，逐條可勾）

| # | 義務 | 出處 |
|---|---|---|
| P1 | 免責固定置頂：「預覽版・未經回測驗證・〈校準句〉・陰陽不是買賣指令、不建吉凶排名・AI 研判非保證」；〈校準句〉依 `latest.json` 的 `calibrated` 顯示（§9 P5，2026-09-26）：`calibrated` 為布林 `true` ＝「部分參數已依訓練段校準（calibrated=true），其餘仍為未校準的起點值」，其他（含 `false`、讀不到資料）＝原句「參數未校準（calibrated=false），數字在校準後會變」 | §10:441、§2:89、§13.3a:588 |
| P2 | 波段／中期**不顯示** `base_score`、不顯示任何方向分數、不列候選名單；短線的 `base_score` 只在展開區顯示並標「未校準」 | §13.3a:588-590、§16:726 |
| P3 | 正式卦與暫定卦**分區標示**；`lines_formal` 為 null 時顯示「六爻尚未全部確立」而非空白 | §10:441、§8 |
| P4 | 每爻顯示：面向名（§2 表）、分數、陽／陰／未定（`line_states`）、連續確認天數（`streaks`）、未知（`unknown`）、覆蓋率；用語「較有利上漲／支撐不足或偏弱」，不出現「看多／看空／買／賣」 | §2:89-92 |
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

## 6. 說明層（2026-09-19 使用者指示「卦象與各爻要有簡潔易懂的說明；這些說明才是 alpha 所在」）

**性質與紅線**：`spec/P1-B4-hexagram-text.md:35` 明定第一版**不得用 AI 生成敘事，所有句子必須來自靜態文本或規則填空**；
逐爻靜態爻文本應在 `data/hexagram_text_v0.2.json`，**repo 內不存在**。故預覽版說明層＝**規則填空**：文案素材只有兩種來源——
①規格 `spec/stock-iching-plan-v1.2.2.md:87-116`（八卦內外卦模板逐字、爻位面向、三爻位元）②計分程式對每爻**實際計算內容**的描述
（`src/iching/score/stock.py`／`market.py`／`params.py`，只寫程式真的在算的指標與視窗）。逐檔動態句一律由分數／爻態／連續天數／
timeline 相鄰日爻態差 依固定句型填入（句型取 `P1-B4:33`「目前〔A〕支持而〔B〕尚未確認…本次由〔前卦〕的〔爻〕確認〔變化〕形成」）。
**不引古典卦辭**（規格未授權、且 `v1.2.2:92` 禁「亢龍有悔」）、**不排吉凶**（`P1-B4:82,:113`）、**個股與大盤各存獨立字串、不共用替換名詞**
（`P1-B4:92,94-101`）、**「轉弱／轉強」只在動爻句出現**（`v1.2.2:92`）、不用「趨勢反轉」（`P1-B4:114`）。這是規則生成、標「規則研判・非保證」。

**S1 靜態文案表（進 `index.html` 常數，個股／大盤各一份）**：每爻 `{name, what, yang, yin, window}`——`what`＝看哪些指標（≤40 字）、
`yang`／`yin`＝「〈面向〉較支持上行」／「〈面向〉支持不足或偏弱」（`v1.2.2:89` 口徑）、`window`＝三期間視窗（來自 `params.py`）。
八卦模板 `TRI`：個股側逐字取 `v1.2.2:103-114` 兩欄；大盤側以同結構改寫面向名（內卦＝趨勢／廣度／量價參與，外卦＝現貨資金／衍生品／海外），
措辭只做面向名替換、不加任何形容。名詞說明六條（正式卦／暫定卦／動爻／遲滯／分數區間／資料不足）為 `v1.2.2:366-374` §8 規則的**白話改寫**（非逐字）。

**S2 動態句規則**（純函式 `explainHex(entry, prevSt)`／`explainLine(i, entry)`，輸入只有 `latest.json` 的欄位與 timeline 前一日 `st`）：
1. 卦象句＝「內卦〈卦名〉：〈內卦描述〉；外卦〈卦名〉：〈外卦描述〉。」——卦名由 `lf`（缺則 `lp`）三爻位元查 `v1.2.2:87` 對應；
   任一爻 `unk=1` 時該三爻組**不查表**，改寫「內卦含未知爻，僅列已知：〈逐爻 支持／未達門檻／未知〉」。
2. 動爻句（只在 timeline 前一日 `st` 存在且與今日 `st` 有差時）＝「本次由〈前一日 kw 對應卦名〉的〈第 N 爻・面向〉確認由〈陰→陽｜陽→陰〉形成。」
   陽→陰用「轉弱」、陰→陽用「轉強」，**其他任何句子不得出現這兩個詞**。多爻同日翻則逐爻列。
3. 每爻句＝「看什麼：〈what〉（〈window〉）。目前分數 〈l〉，〈≥55：偏多區｜≤45：偏空區｜45–55：臨界區〉，〈yang｜yin｜（臨界時）以正式爻態為準〉。
   尾句三態（**`streaks` 語意實查 `hexagram.py:113-137`＝分數已連續 N 日站到翻轉門檻另一側、尚未確認翻爻；翻爻當日歸 0**——首版誤寫成「已連續確認 N 日」，2026-09-19 修正）：
   (a) 今日翻轉（前一日 `st` y↔n）→「今日完成翻轉（動爻）。」(b) `sk=0` 未翻轉→「爻態穩定，分數未站到翻轉門檻另一側。」
   (c) `sk≥1`→「候選變化：分數已連續 N 日站在翻轉門檻另一側，再 (2−N) 日仍站住即翻爻。」（＝§8「候選變化」）。
   `unk=1` 時整句改「必要資料缺，本爻不計、不補陰、不累計確認天數。」`cov=reweighted` 加註「可選資料部分缺、已重配權重」（列層無逐爻比率，不寫數字）。
4. 內卦／外卦分數：短線可顯示（`inner/outer_trigram_score` 由 `build_web.py` 加進 `latest.json` short 物件為 `ti`／`to`；引擎按三爻組分別給 null，故「缺或非數即 null」）；
   **波段／中期只出文字、不出數值**（`v1.2.2:590`、`P1-B4:116`）。
5. 用字檢核清單（頁面 innerText 與原始碼皆不得出現）：機率、勝率、看多、看空、買進、賣出、多頭、空頭、吉、凶、趨勢反轉、亢龍有悔；
   「轉弱」「轉強」只允許出現在動爻句。

**驗收條件（綁 commit）**：
- F1 `build_web.py`：short 物件新增 `ti`／`to`（1 位小數，缺或非數即 null）；swing／mid **仍無** `bs`／`ti`／`to`；測試補斷言；決定性不變；`latest.json` 重產。
- F2 `explainHex(entry, kind, prevSt, prevName)`／`explainLine` 抽成可單測的純函式並放在 `index.html` 同一 `<script>` 內（`prevName`＝前一日卦名由外部傳入，函式不碰可變狀態）；用 node 以 `tests/extract_js.mjs` 式手法抽出跑 ≥12 個案例
  （純陽／純陰／含未知／臨界／動爻陽→陰／動爻陰→陽／多爻同翻／無前一日／reweighted／大盤／`lf` null 用 `lp`／個股與大盤同位元得不同字串），
  斷言輸出逐字＝預期句。
- F3 Playwright：診個股與觀大勢每爻列展開後有說明句；卦象卡有內外卦句；有換卦紀錄的檔顯示動爻句且含「轉弱」或「轉強」；
  用字檢核清單（S2-5）在全部畫面 innerText 零命中（動爻句除外）；375／390／1280 無頁面級溢出；console 零 error。
- F4 靜態文案表每條回溯：`what`／`window` 的每個指標名在 `params.py`／`stock.py`／`market.py` 找得到對應（驗收者抽 6 條核）；
  八卦表個股側與 `v1.2.2:103-114` 逐字相同。
- F5 fresh-context 驗收綁 PR head；合併後線上由使用者實機看。

## 7. 裁定 #52（2026-09-19）：卦象與各爻先引卦辭、爻辭古文，再輔以簡潔說明

使用者原話：「卦象與各爻可先引卦辭與爻辭古文，在輔以簡潔易懂的說明」。本裁定**優先於** `spec/P1-B4-hexagram-text.md:35`
（第一版不引古文／不用 AI 敘事）；與 `v1.2.2:92` 的關係：①「古義原文與市場解讀分欄」→ 頁面古文一欄、規則說明（§6）一欄，**不混寫**；
②「上爻不引用『亢龍有悔』」→ 乾卦上九的爻辭原文照列於古文欄（它就是原文），**市場解讀欄永不引用它作為判斷依據**。

- **資料檔 `data/hexagram_text.json`**：64 卦 × {`king_wen`, `name`, `judgment`（卦辭）, `lines`[6]（初→上，各 {`title`（初九／六二…）, `text`}）,
  `extra`（乾用九／坤用六，其餘 null）, `gloss_judgment`, `gloss_lines`[6]（白話摘義，各 ≤40 字）}＋頂層 `source`（來源 URL 與抓取日）、
  `gloss_note`＝「白話摘義由 AI 撰寫，非學術譯注」。古文來源＝維基文庫《周易》（公有領域），以第二來源（ctext.org 或另一版本）交叉比對；
  正體字（維基文庫殘留的簡化字形如 后／系／瓮／并 改為 後／係／甕／並，逐條記在該 commit 訊息）；標點統一全形。**自動守門**：`tests/test_hexagram_text.py` 驗 64 卦齊、`king_wen`／`name` 與 `spec/hexagrams64.json` 一致、
  每卦 6 爻、**爻題的九／六必須與 `lines_bottom_up` 位元一致**（陽 1 → 九、陰 0 → 六；初／上／二三四五位名正確）、只有第 1／2 卦有 `extra`、
  無空字串、摘義字數上限。
- **頁面**：卦象卡＝卦名 → 卦辭古文 → 白話摘義 → §6 規則解讀（分欄／分段標示「古文」「白話摘義」「規則研判」）；六爻表每列展開＝爻題＋爻辭古文 →
  白話摘義 → §6 該爻說明；動爻（§6 S2-2）那一爻的爻辭**加強顯示**（古法讀變爻）。`build_web.py` 不變（古文由前端另抓 `data/hexagram_text.json`，
  約數十 KB，同源）。
- **驗收**：G1 資料檔守門測試全綠；G2 驗收者抽 8 卦（含乾、坤、第 32 卦雷風恆、第 63／64 卦）逐字對照維基文庫；G3 Playwright：卦象卡與每爻列
  顯示古文與摘義、動爻爻辭加強、375 無溢出、console 零 error；G4 §6 S2-5 用字檢核在**規則欄**維持零命中（古文欄不受此限）。

## 8. 裁定 #53（2026-09-19）：卦爻文案改台灣用語

| # | 原用語 | 裁定後 | 落點 |
|---|---|---|---|
| A1 | 支持上行 | **有利上漲**（陽＝該面向較有利上漲） | `v1.2.2:89`、`FACET.*.yang`、§6 S2 |
| A2 | 支持不足或偏弱 | **支撐不足或偏弱** | `v1.2.2:89`、`FACET.*.yin` |
| A3 | 八卦模板的「支持」／「未達支持門檻」 | **有支撐**／**未達門檻**（例：兌內「營運與趨勢有支撐，相對動能不足」；坤「三面向均未達門檻」） | `v1.2.2:103-114`、`TRI.*` |
| A4 | 支持區（≥55） | **偏多區**；≤45 **偏空區**；45–55 臨界區（純描述分數落點，非看多看空） | `explainLine` |
| B1 | 回撤 | 回檔 | `FACET.stock[3].what` |
| B2 | 收盤與均線的距離／離均線距離 | 乖離 | `FACET.stock[1]`、`FACET.market[0]`、`[5]` |
| B3／B4 | 衍生品／期權市場條件 | **期權**（台灣「期貨與選擇權」簡稱；面向名與說明句統一。使用者 2026-09-19 由「期貨選擇權」改裁為「期權」） | `FACET.market[4]`、`TRI.market` 外卦句 |
| B5 | 量比 | 成交量／均量（大盤三爻程式算的是**成交金額**，頁面寫「成交金額／均量」，驗收補記） | `FACET.stock[3]`、`FACET.market[2]` |
| B6 | 百分位 | 百分位數 | `FACET.market[4]` |
| B7 | 淨未平倉 | 未平倉淨部位 | `FACET.market[4]` |
| B8 | 贏同業幅度 | 領先同業幅度 | `FACET.stock[0]` |
| B9 | 現貨資金 | **現貨籌碼**（`what` 明寫：法人淨買超占比（同向）、外資買超天數、融資餘額變化（**反向**，`params.py:349` direction=−1）） | `FACET.market[3]`、`TRI.market` |
| B10 | 外部支持 | 外部環境 | `FACET.stock[5]`、`v1.2.2:101` |
| B11 | 突破後是否守住 | 突破後是否站穩 | `FACET.stock[3]` |
| B12～B15 | 營運數據／美元兌台幣／通過／查詢 | **維持不變** | — |
| C1 | 遲滯 | 名稱保留「遲滯」，說明改：「連續兩日過門檻才翻爻的確認緩衝機制（陰→陽 ≥55、陽→陰 ≤45）；只站住一日的爻稱候選變化」 | `TERMS` |

規格檔 `spec/stock-iching-plan-v1.2.2.md` 的 `:89`／`:101`／`:103-114` 同步改字（語意不變，只改用語）；§6 S2 句型與 F2 案例預期句同步；
用字檢核清單（S2-5）不變，另加「上行」「回撤」「衍生品」「期貨選擇權」為**不得出現**（確認舊字全部清掉；「期權」是裁定用語、允許）。

## 9. 模型換版標示（2026-09-26，任務 C2；#68／#69 換模型後）

**動機**：#68（`RULES_VERSION` `p2-score-engine-1` → `-2`）與 #69（d 重校準）換了模型——`params_sha` `c7385e78cb9f` → `8ca174ee8bc7`，
兩市場 `model_version` 皆換。重播資料補進 `data/scores/` 後，近 20 日時間軸可能新舊模型混雜，而換卦紀錄（相鄰日 kw 不同）與動爻
（前一日 `st` 與今日 `st` 翻轉）都會把「模型換版」誤讀成「市場變化」。**只做描述性標示，不改任何計分、不改排序、不做任何判斷**。

**資料層（`scripts/build_web.py`，維持決定性、不寫時戳；新欄只新增、不改既有鍵與位元組）**：
- W1 `timeline.json` 新增 `ps`：與 `dates` 等長，每格＝該日分數檔頂層 `params_sha`；讀不到／壞 JSON／形狀不對／缺該欄／非字串或空字串＝null
  （**不沿用前後日的值**）。
- W2 `latest.json` 新增 `model_version`＝`{"twse": [...], "tpex": [...]}`：該日各市場列（含大盤列）出現過的 `model_version` 去重升冪；
  非字串／空值不收；正常各 1 個。
- W3 其餘位元組不變：以 2026-09-26 的 `data/scores/`（18 日，全為 #68 前模型）實跑，新舊輸出的差異**只有**插入的
  `"model_version":{...},`（latest，+102 bytes）與 `"ps":[...],`（timeline，+277 bytes）兩段，拿掉後逐位相同；gzip -9 後
  latest 185,948 → 186,011、timeline 75,483 → 75,504 bytes。
- 前端相容：舊前端不讀新欄、不受影響；新前端遇到缺 `ps`（或 `ps` 長度 ≠ `dates`、非陣列）一律把 `ps` 整個視為缺席、
  遇到缺 `model_version` 就不出模型版本格——**不部分採信、不臆測**，行為與換版標示上線前相同。

**頁面層（`index.html`，純函式 `psVal`／`tlPs`／`psDiffers`／`modelShiftAt`／`modelShifts`／`modelShiftText`／`mvOffFor`／`modelVersionInfo`）**：
- P1 頂列在原六格之後加「模型（上市）」「模型（上櫃）」兩格，值＝`latest.json` 的 `model_version`。**選 `model_version` 而不是
  「RULES 版號＋`params_sha`」的理由**：`model_version` 就是逐列蓋在分數檔上的那個值（＝`RULES_VERSION` ＋ 該市場參數指紋），
  兩市場各一份指紋、且本身已含 RULES 版號；`params_sha` 已在頂列另一格，再拼一次只是重複。某市場 >1 個 → 另一格中性灰
  （虛線框、`--muted` 色，不用紅黃綠）「〈市場〉：該日混有多個模型版本」。
- P2 `ps` 出現 ≥2 個不同非 null 值 → 每張卡換卦紀錄段上方出中性說明（`.mvnote`）：「近 N 日含模型換版：YYYY-MM-DD 起改用新參數版本
  （params_sha 前 → 後）。換版前後的卦象不可直接比較，換卦可能來自模型調整而非市場變化。」多次換版以「；」逐次列出。
  換版日判定＝沿時間軸**跳過 null**、非 null 值與上一個非 null 值不同的那一天（中間夾 null 時，實際換版可能落在 null 那幾天，
  句中的日期是「最早看得到新值的那一天」）。
- P3 換卦紀錄每筆：換卦當日 `ps` 與**該筆比較的前一日**（i−1，換卦紀錄本來就要求該日有列）`ps` 皆非 null 且不同 → 加中性 badge「模型換版」。
  前一日 `ps` 為 null → 不加（不臆測換版落在哪一天）。
- P4 動爻：今日 `ps` 取**兩個來源**——timeline 當日 `ps` 與 `latest.json` 頂層 `params_sha`；前一日取 timeline 前一日 `ps`。
  前一日非 null、且今日任一已知來源與它不同 → 不出動爻句、不加強動爻爻辭、每爻句不寫「今日完成翻轉」、不判用九／用六，
  改一句「今日與前一日模型版本不同，不比較動爻。」；前一日 null 或今日兩來源皆 null → 維持現行行為。
  **兩來源矛盾**（`latest.json` 與 `timeline.json` 分別抓取，快取可能讓兩者不同批）時必有一個與前一日不同，因此一律抑制——這是最保守解釋。
- P5 頂部免責卡的校準句包進 `#calTxt`，由 `renderDisc()` 依資料換字：`calibrated === true`（布林）才顯示
  「部分參數已依訓練段校準（calibrated=true），其餘仍為未校準的起點值」；其他一律維持靜態預設原句「參數未校準（calibrated=false），
  數字在校準後會變」（HTML 靜態預設＝`CAL_FALSE_HTML`，所以 latest.json 讀不到時也是這句）。**理由**：現行 `latest.json`
  `calibrated=true`（2026-09-20 起 212 個子指標的 `d` 依訓練段校準，`src/iching/score/params.py` 檔頭），頁頂寫死「未校準」與頂列
  `calibrated true` 自相矛盾；但 `c`、族／爻權重、`Rules` 門檻常數仍未校準，所以用「部分」，不寫「已校準」。
  **「未經回測驗證」「陰陽不是買賣指令」「不建吉凶排名」「AI 研判、非保證」等其他免責語句一字不動**（`tests/explain_cases.mjs`
  以 2026-09-26 前原文逐字比對守門）。§2 P1 條文同批改寫為「〈校準句〉依資料顯示」。
  **未動**：短線 `base_score` 展開區的「未校準・僅供參考」標示（§2 P2 規定，屬另一條義務；是否隨 `calibrated` 改字待裁定）。
- P6 用字：新增字串全部中性、零 S2-5／#53 禁用詞（`explain_cases.mjs` 結構斷言）；`ps`／`model_version` 進 `innerHTML` 一律過 `esc()`
  （Playwright 以 `<img onerror>` 注入實測不執行、以字面顯示）。
- P7 CSP 不改（同源）；375／390／1280 無頁面級水平溢出、console／pageerror 零。

**測試**：`tests/test_build_web.py`（W1 逐日對應含壞檔／缺鍵／非字串→null、`--n` 截取、W2 去重排序、W3 位元組只新增、決定性）；
`tests/explain_cases.mjs` 案例 25–34＋3 條結構斷言（W3 降級、P1／P2／P3／P4 純函式、P5 預設句與免責卡原文）；
`tests/test_explain_js.py` 另有 P3（`modelShiftAt` 恆回 false）與 P4（拿掉抑制分支）兩支突變守門。頁面層以 Playwright（fixture：①無換版 ②中途換版 ③當日換版 ④舊檔 ⑤混版）驗。

## 10. 懂卦理分頁（2026-09-26，任務 C3；使用者裁定：第三個分頁、四項內容、格＋清單）

**性質**：純描述、純前端。**不排名、不作評價、不放個股名單、不寫買賣**；`build_web.py` 與 `data/web/` 位元組不動（今日卦分布由前端
現算）。tab id `guide`，hash `#tab=guide&kw=<1..64>`。所有新字串進 `innerHTML` 一律過 `esc()`；CSP 不改（只讀同源 `data/hexagram_text.json`，
與古文層共用 `HX`、不重複抓）。

| # | 規則 | 落點（`index.html` 宣告字串） |
|---|---|---|
| G1 | 六十四卦：資料只讀 `data/hexagram_text.json`（同源、與 `HX` 共用）。卦頁＝卦名、卦序、上下卦（名＋自然象＋位元）、六爻圖（`hexFig`，初爻在下）、卦辭古文＋白話摘義、六爻爻題＋爻辭＋摘義（初→上）、乾坤另列用九／用六並註「只在六爻全部為動爻時讀」。分欄標「古文」「白話摘義」（#52）。**不放本站分數、不放個股、不寫市場解讀** | `function guideHexHtml`；六爻位元由爻題反推 `function hexBits`／`hexTri`（含「九」＝1、「六」＝0；`tests/test_hexagram_text.py` 守住爻題與 `spec/hexagrams64.json` 一致，前端不另抓 spec 檔） |
| G2 | 卦理入門：五段 ≤600 漢字，只講結構性事實（陰陽爻、八卦名稱／位元／自然象、上下卦組成 64 卦、爻位名規則、本站動爻／換卦定義＝§6 S2-2）；不引占斷、不排名、「亢龍有悔」不作判斷依據 | `const GUIDE_INTRO`；自然象 `const TRI_ELEM`（＝`spec/hexagrams64.json` 純卦卦名「X為Y」的 Y） |
| G3 | 本站六爻對應：個股／大盤兩表（爻位・面向、看什麼、視窗、陽的意思、陰的意思）＋八卦對照表（名、位元、象、個股內外卦句、大盤內外卦句）——**直接由 `POS`／`FACET`／`TRI_ORDER`／`TRI_NAME`／`TRI_ELEM`／`TRI` 渲染，不另抄字串**（#53 用語單一來源）；遲滯說明取 `TERMS[3]` | `function guideFacetRows`／`guideTriRows` |
| G4 | 今日卦分布：由 `latest.json` 的 `stocks` 現算每個 horizon 各卦檔數（全部＋「其中排名池」）；缺 `kw` 的列計「未定」；**依卦序排列、只列當日出現的卦、不依檔數排序、不列個股、不加顏色強弱**；大盤六列不計。不變式 Σ檔數＋未定＝該 horizon 有列股票數。頂部固定一句「僅為當日卦象計數，不是選股清單、不代表方向。」 | `function hexDist`（純函式）、`function guideDistHtml`、`const GUIDE_DIST_NOTE` |
| G5 | 瀏覽：8×8 格（列＝上卦、欄＝下卦，兩軸皆 乾兌離震巽坎艮坤＝位元 111→000 遞減，純結構）與卦序清單（可依卦名含字／卦序過濾；過濾只重繪清單、輸入不失焦）切換；點格或列開該卦；hash `kw=` 白名單 1..64 整數（不含前導 0／小數／空白），非法值靜默退回 `#tab=guide`；診個股／觀大勢卦象卡的卦名可點 → 同頁切 tab 開該卦（`kwLabel` 掛 `.kwlink[data-kw]`，`kw` 不在白名單就不掛） | `const TRI_ORDER`（**必須是陣列字面值，不得 `Object.keys(TRI_NAME)`**——JS 會把 `"111"`～`"100"` 這類整數字串鍵依數值升冪排前面，得到 震離兌乾巽坎艮坤）、`function parseKw`、`const KW_RE`、`function guideGridHtml`／`guideListRows` |
| G6 | 手機：8×8 格用 CSS grid 九欄 `minmax(0,1fr)` 自動縮、卦名可換行；375／390／1280 `scrollWidth <= innerWidth`；對應表包 `.tblwrap` | `.hexgrid`／`.gcell`／`table.gtable` |
| G7 | 用字：§6 S2-5 與 #53 清單在本分頁**說明文字**零命中（古文欄 `.classic`／`.gloss` 不受限，比照 §7 G4）；新字串全過 `esc()`；CSP 不改 | `tests/explain_cases.mjs` 結構斷言＋`tests/test_page_playwright.py` |

**清單過濾的口徑**：卦名含過濾字或卦序＝過濾字。實查 64 卦卦名含「乾」的只有乾為天（1 筆）；含「天」的 **15 筆**（卦序 1、5、6、9、10、11、12、13、14、25、26、33、34、43、44；2026-09-26 驗收更正，原寫 8 只算了以「天」開頭或結尾的）。過濾字先 `trim()`；卦名比對用「含」（`includes`）不是「開頭」。
上下卦欄（「上乾下乾」）**不參與過濾**——參與的話「乾」會命中 15 筆，與「依卦名／卦序過濾」的字面不合。

**「今日卦分布」的邊界**：它是計數不是名單。同一卦在三個期間的檔數會不同（三期間各自定卦），數字只反映當日卦象在
股池中的分布，**不代表方向、不是候選、不作排序依據**（鐵律 8：任何可能影響選股的呈現都要先有回測依據，目前為零）。

**待辦（本批刻意不做，登錄書凍結）：按卦列出個股名單**。`spec/stock-iching-plan-v1.2.2.md` §13.3a:590／§13.3 表 :617-618
規定回測門檻通過前不輸出候選名單；使用者 2026-09-26 裁定「按卦列出個股名單本批不做」。開通條件＝該期間的回測門檻通過，
屆時**只開通過門檻的期間**；在此之前頁面**不放任何「查看名單」入口**（`tests/test_page_playwright.py::test_guide_dist_counts`
守住分布段無連結、無代號、無「名單／查看／候選」字樣）。

**測試**：`tests/explain_cases.mjs` 案例 35–44＋§10 結構斷言 4 條（hexBits／hexTri 64 卦逐卦＝spec、parseKw 白名單、hexDist 手算與不變式、
TRI_ORDER 順序、TRI_ELEM＝純卦名、guideFacetRows／guideTriRows **哨兵沙箱**（只給哨兵常數執行、輸出必含全部哨兵且各被 esc 包過——抄字串
就會紅）、卦理入門五段 ≤600 字、新字串零禁用詞、卦頁原始碼不引用分數欄）；`tests/test_explain_js.py` 七支突變守門（G4 計數 off-by-one、
G5 白名單放寬到 99、G3 表抄字串、TRI_ORDER 改 `Object.keys`、`kwLabel` 不過 `parseKw`、清單過濾不 `trim`、清單過濾改 `startsWith`）。
`tests/test_page_playwright.py` 另有 `test_injection_guard_alive_mutation`：以 `page.route` 餵一份「`guideHexHtml` 的 h2 不過 esc」的 `index.html`，
斷言注入**會**執行——證明注入守門本身活著。
**頁面 DOM 接線層自動守門（PR #75 驗收指出原本零自動守門）**：`tests/test_page_playwright.py`——pytest 模組，playwright 或 Chromium
不可用時 `pytest.skip`（CI 沒裝不紅）；本機 `http.server`＋`page.route` 餵測試內建構的 fixture（不依賴網路、不依賴 `data/web/` 現況；
`hexagram_text.json`／`spec/hexagrams64.json` 讀 repo 內檔）。涵蓋「怎麼驗 2」①–⑧（tab／64 格與 5 卦逐字／清單過濾／hash 直開與
非法值／分布手算與不變式／三寬度／console 零／卦名跳轉）＋G3 表逐格＝頁內常數＋G7 零禁用字＋latest 讀不到時分布段降級，
並移植 §9 核心情境（無換版／中途換版說明與加標／當日換版抑制動爻／舊檔降級／免責卡校準句四情境／注入）作回歸。

## 11. 持股卦象一覽「我的持股」（2026-09-26，使用者指定；唯讀 `pm_holdings`）

**性質**：這是使用者**自己在 postmkt 建立的清單**（「盤後分析」站的持股診斷，`localStorage["pm_holdings"]`），**不是本站篩出的名單**；
本區只把既有「診個股」的輸出（當前期間的正式卦名＋六爻圖）依清單逐列排出，是**批次檢視**、不構成候選名單。與 §13.3a 的關係：
§13.3a 禁的是「本站依分數／方向產出名單」，本區的成員與順序完全由使用者在他站決定，本站不排序、不篩選、不評價、不顯示任何分數
（鐵律 8：不新增任何訊號）。純前端；`build_web.py` 與 `data/web/` 位元組不動；CSP 不改（讀 localStorage 不涉 `connect-src`）。

| # | 規則 | 落點（`index.html` 宣告字串） |
|---|---|---|
| H1 | 位置：「診個股」tab 頂部，輸入框之下、查詢結果之上；不新增 tab、不新增 hash 鍵。有清單時期間 chips 提到區塊上方（同一組 chips 同時控制持股表與查詢結果，畫面只有一組） | `function stockHtml`（`readHoldings()` → `holdHtml()`） |
| H2 | 資料只來自同 origin `localStorage["pm_holdings"]`（postmkt 寫入的裸陣列 `[{c,sh,cost}]`，無版本欄）。**全檔對本機儲存只准 `getItem(HOLD_KEY)`**，不得出現 `setItem`／`removeItem`／`clear`／方括號存取／`Storage` 原型；只讀 `c`，**不讀不顯示 `sh`／`cost`**。本機儲存被封鎖時視為空清單 | `const HOLD_KEY`、`function readHoldings`；靜態守門＝`tests/test_explain_js.py::test_holdings_storage_read_only`（不依賴 node）＋`explain_cases.mjs` 結構斷言「§11 H2 全檔」；動態守門＝Playwright `Storage.prototype` spy 0 次 |
| H3 | 防禦性解析：try/catch；壞 JSON／非陣列 → 空；每筆須 `h && typeof h.c==="string"`，`trim().toUpperCase()` 後以本站 `CODE_RE`（4–6 碼大寫英數）過濾（postmkt 手動新增沒驗格式）；去重（保留第一筆）；壞筆靜默略過、**不寫回** | `function holdingsCodes`（純函式，`explain_cases.mjs` 案例 48／49／51） |
| H4 | **持股代號不進任何網路請求**（URL／header／body）；整份清單不進 hash。每列點擊＝使用者主動查詢該**單一**代號，只有該代號進 hash（`#tab=stock&code=<代號>`）——與 postmkt 持股異動→持股診斷同樣只帶單一代號；但本站走 `replaceState` 不塞歷史（持股表仍留在同頁上方，不需 Back），與 postmkt 刻意 `location.hash=` 塞歷史不同；列內卦名連結仍跳懂卦理（`#tab=guide&kw=`） | 事件委派 `closest("tr.hrow[data-code]")`（先過 `CODE_RE` 再進 state）；Playwright `page.on("request")` 逐請求斷言 |
| H5 | 每列＝代號、股名（`DATA.names`，缺則只顯代號）、當前期間（`state.h`）的六爻圖（重用 `hexFig`）與正式卦名（`kwLabel` 可點）。正式卦待補 → 標「正式卦待補」並附「暫定卦：〈名〉」；`in_rank_pool≠1` 標「未達流動性門檻（60 日成交值 <3,000 萬）」（P8）；不在 `DATA.stocks` 標「不在最新分數檔（〈date〉）內」；該期間缺席標「該期間無列」。**不顯示 `bs`／`ti`／`to`、不顯示任何分數、不排序、不篩選、不評價——順序＝清單原順序** | `function holdRowHtml`／`holdHtml`（原始碼不含 `.sh`／`.cost`／`.bs`／`.ti`／`.to`／`.l[`／`sort(`／`filter(`，結構斷言守） |
| H6 | 標題「我的持股〈badge 來自持股診斷（唯讀）〉」；有清單＝「此區代號來自「盤後分析」站的持股診斷，本站唯讀；增刪請至該站管理。持股清單只存本機瀏覽器，持股代號不進任何網路請求。」；空清單＝「尚無持股（於「盤後分析」站的持股診斷設定後，同一瀏覽器此處自動顯示）。」**不可寫「本頁不發任何網路請求」**（`load()` 本來就抓 `latest.json`）。區塊內無新增／刪除鈕 | `const HOLD_SRC_TXT`／`HOLD_NOTE_TXT`／`HOLD_EMPTY_TXT`／`HOLD_NO_H_TXT` |
| H7 | 用字：避開 §6 S2-5／#53 清單與「轉弱／轉強」；不出現「名單／查看／候選／看多／看空／買／賣／機率／勝率」；新字串全過 `esc()` 進 `innerHTML` | `explain_cases.mjs` 結構斷言「§11 H7」＋Playwright `test_hold_forbidden_words_zero` |
| H8 | 手機：表格包 `.tblwrap`；375／390／1280 `scrollWidth <= innerWidth`；console／pageerror 零；CSP 不改 | `.holdbox`／`#hold .hexfig`（縮小版六爻圖）；Playwright 三寬度 |
| H9 | 與 §13.3a 的關係（見本節「性質」段）：使用者自建清單、非本站篩出；既有輸出的批次檢視；不構成候選名單；仍不排序不評價 | — |

**跨站約定**：postmkt `CLAUDE.md` 約定 6（持股清單只存 localStorage、不進任何網路 payload）在本站同樣成立，本站更進一步**只讀不寫**；
先例＝taiwan-stock-news `index.html` 的 `trackHoldings()`（唯讀 `pm_holdings`、只取 `c`）與其「來自持股診斷（唯讀）」用語，本區的
說明句沿用。postmkt 是唯一寫入者；本站與 news 站都不得寫回（格式若日後由 postmkt 改版，本站只需改 `holdingsCodes`）。
線上三站同 origin（`https://shihpc.github.io/`，本站 2026-09-26 curl 200）所以同一瀏覽器直接看得到。

**刻意不做**：新增／刪除／匯入（那是 postmkt 的事，本站唯讀）；顯示股數／成本／市值（`sh`／`cost` 不讀）；依卦象或分數排序／篩選／
分組；把清單放進 hash 或任何請求。

**測試**：`tests/explain_cases.mjs` 案例 48–51（`holdingsCodes` 正常／壞輸入／postmkt 實際寫法、`holdHtml` 列順序＝原順序與各列標示）
＋結構斷言 4 條（全檔本機儲存只准 getItem、持股區原始碼不碰 `sh`／`cost`／分數欄／`sort`、新字串零禁用詞且常數全經 `esc()`、hash 白名單不變）；
`tests/test_explain_js.py` 五支突變守門（拿掉 `CODE_RE` 過濾、依卦序排序、顯示 `bs`、讀 `sh`、多一個 `setItem`）＋不依賴 node 的
靜態測試 `test_holdings_storage_read_only`（附守門活著的自證）；`tests/test_page_playwright.py` 十項（①空 ②正常清單三期間 ③壞 JSON
④非陣列 ⑤壞筆與注入 ⑥點列 hash 只含單一代號＋Enter 鍵＋列內卦名跳懂卦理 ⑦所有請求不含持股代號 ⑧`Storage` 寫入 spy 0 次（含 spy 自證）
⑨三寬度 ⑩用字檢核），`Page(init_script=…)` 在 goto 前注入 localStorage fixture。
