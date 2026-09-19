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
              "bs": base_score（**只有 short 帶**；swing／mid 一律省略，§13.3a）,
              "ti"／"to": inner／outer_trigram_score（1 位小數，缺即 null；**同樣只有 short 帶**，§6 S2-4／F1）}
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
3. 每爻句＝「看什麼：〈what〉（〈window〉）。目前分數 〈l〉，〈≥55：落在支持區｜≤45：低於支持門檻｜45–55：臨界區〉，〈yang｜yin｜（臨界時）以正式爻態為準〉。
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
  正體字；標點統一全形。**自動守門**：`tests/test_hexagram_text.py` 驗 64 卦齊、`king_wen`／`name` 與 `spec/hexagrams64.json` 一致、
  每卦 6 爻、**爻題的九／六必須與 `lines_bottom_up` 位元一致**（陽 1 → 九、陰 0 → 六；初／上／二三四五位名正確）、只有第 1／2 卦有 `extra`、
  無空字串、摘義字數上限。
- **頁面**：卦象卡＝卦名 → 卦辭古文 → 白話摘義 → §6 規則解讀（分欄／分段標示「古文」「白話摘義」「規則研判」）；六爻表每列展開＝爻題＋爻辭古文 →
  白話摘義 → §6 該爻說明；動爻（§6 S2-2）那一爻的爻辭**加強顯示**（古法讀變爻）。`build_web.py` 不變（古文由前端另抓 `data/hexagram_text.json`，
  約數十 KB，同源）。
- **驗收**：G1 資料檔守門測試全綠；G2 驗收者抽 8 卦（含乾、坤、第 32 卦雷風恆、第 63／64 卦）逐字對照維基文庫；G3 Playwright：卦象卡與每爻列
  顯示古文與摘義、動爻爻辭加強、375 無溢出、console 零 error；G4 §6 S2-5 用字檢核在**規則欄**維持零命中（古文欄不受此限）。
