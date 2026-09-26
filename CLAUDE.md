# CLAUDE.md — taiwan-stock-iching 接手速覽

<!-- CANON:BEGIN v1 -->
<!-- 唯一事實來源＝shihpc/claude-harness 的 CANON.md。以下區塊在家族各 repo 的 CLAUDE.md 頂端
     有 byte-identical 逐字副本，由各 repo 的 .github/workflows/canon.yml 守門（比對 sha256）。
     改動流程：先改 claude-harness/CANON.md → 跑 tools/sync_canon.py 同步全部副本 → 更新守門 hash。
     **repo 名單以 tools/sync_canon.py 的 TARGET_REPOS 為準，此處刻意不寫死數量**——
     數量已改過兩次（五→六→七），每改一次就得動全部 repo 的 CLAUDE.md 與守門 hash。
     不要只改單一 repo，CI 會擋下來。 -->

## 通用工作鐵律（家族各 repo 逐字相同，勿單獨修改）

1. **機密**：token／金鑰只存在不受版控的本機設定或受控 secrets（`.env`／Actions secret／
   `wrangler secret`），絕不寫進會 commit 的檔案、log 或對話輸出。commit 前掃 staged 內容，
   **只回報檔名／行號／類型、不把可疑字串原文印出來**；`sk-ant-`／`ghp_`／`eyJ` 是線索不是全集。
2. **指揮官不下場**：掃 repo、通讀 >300 行的檔、一次讀 >3 個檔、查網頁研究、批次改檔、
   驗收改過的東西——這六類一律派 subagent，主對話只收結論＋`檔案:行號`；但 subagent 回報
   **不等於事實**，主對話為核對結論可直接查原始證據。雲端 session 的 subagent 派工（含第 3 條
   驗收）已獲常備授權，需要時直接派，不需逐次詢問。
3. **先寫驗收條件再動手**：動手前先寫下目標專案完整路徑＋怎樣算完成＋怎麼驗。修改者先自測，
   再派 fresh-context subagent 驗收——**改東西的 agent（含主對話自己）不得擔任驗收者**。
   驗收要綁**確切 commit／產物版本**；驗收後又改動，受影響部分要重驗。
4. **不確定不亂說**：陳述事實（尤其技術細節、數字、外部服務的限制與行為）要嘛附佐證（官方
   文件、實測、`檔案:行號`），要嘛明說「這點我不確定，需要查證」，不可憑印象當確定講。區分
   「已驗證事實」與「推測」，推測要標明；**`檔案:行號` 只證明程式這樣寫，不證明線上這樣跑**。
5. **一次只做一件事**：聚焦一個明確目標，完成該目標必要的修改、測試與整合；不擅自加入無關
   重構或延伸功能。範圍外問題簡短記錄、不自行擴張任務。
6. **完成的定義**：驗收條件逐條打勾＋fresh-context subagent 驗過＋產物在使用者拿得到的位置，
   並明示已完成與未完成；**可執行的東西沒實跑過不算完成**（純文件交付以內容與結構檢查為準）。
   涉及部署者另需 push＋部署 workflow 成功＋在**實際服務的位置**驗證本次變更（線上頁面／API／
   資料時戳）——**raw URL 只證明原始碼進了 repo，不證明線上跑的是該版本**，200 也不等於功能正確。
7. **push 前**：先確認目前分支與推送目標，`git fetch` 後檢查遠端是否領先，非空必須先看內容
   （訊息／時間戳／diff）。一般 push → rebase 整合（本專案既定政策），嚴禁直接覆蓋；force push
   前若遠端領先的 commit 是真實新工作 → 停下來問，且一律用 `--force-with-lease=<ref>:<預期 SHA>`；
   授權「這次 force push」不等於授權蓋掉遠端所有領先 commit。
8. **新指標／訊號若會影響投資方向、候選排序、進出場或風險判定，先問有沒有回測依據**，沒有就
   先驗證再上線；純描述性顯示（欄位、日期、圖示）只需驗算式正確。市場內容可做情境判讀與多空
   因素分析，可研判市場與大眾情緒對該數值或新聞的可能反應，並可提供具體個股／標的的買賣建議
   與進出點位；以上均須附依據、區分事實與推論，並標明屬 AI 研判而非保證。
9. **語言**：對話與文件用繁體中文；程式碼註解可中文，identifier 用英文；外部原文、API 名稱、
   指令與錯誤訊息保留原樣。

> 判準細則、派工模板、教訓簿見 `shihpc/claude-harness`（private）。雲端 session 需 add_repo 才讀得到。
<!-- CANON:END v1 -->

「股市易經」：把個股與大盤的量化狀態對應到 64 卦六爻。家族第七站。
線上 https://shihpc.github.io/taiwan-stock-iching/ （2026-09-26 curl 200；P4 預覽版三分頁 觀大勢／診個股／懂卦理 已上線，規則見 `docs/P4-PREVIEW.md`）。**P1 規格完成、P2 計分引擎已上線並部分校準（見下）。**

## 進行到哪

見 `README.md` 的階段表與 `docs/P2-KICKOFF.md` 的完成定義。一句話：
**2026-09-20 起 c／d 已部分校準**：212 個子指標的 `d` 由訓練段（2021-01-01～2023-06-30，603 日）
`p85 ÷ 3` 判準校準完畢（`ParamSet.calibrated=True`）。**`model_version` 現為 twse `p2-score-engine-2.01697576a7b0`／
tpex `p2-score-engine-2.83b5c5dfdb23`**（`params_sha`＝`8ca174ee8bc7`，window 320）——**裁定 #69**（2026-09-26，d 整份改用
#68 後在現行碼上重跑的訓練段報告 `288fd36`，25 個 d 變、規則不變，`docs/P3-CALIBRATION.md` §32）之後的值；
**#68 後、#69 前**為 twse `p2-score-engine-2.8f81122a37ae`／tpex `p2-score-engine-2.dfa55ced4a96`（`params_sha` `6bd41e811f49`，
**裁定 #68**：營收年增率分母 ≤ 0 視為缺值、`RULES_VERSION` 升 `-2`，§31；新校準報告的 x 是以這組碼 dump 的）；
**#68 前**為 twse `p2-score-engine-1.0bb386e9cf3b`／tpex `p2-score-engine-1.8eb4f29fec3a`（`params_sha` `c7385e78cb9f`，
`runs/` 下 stats／t717／revbase／revneg 的報告與登錄書附錄 A／B／C 記的是這組）。`--uncalibrated` 的指紋不讀校準表，
#69 前後不變（twse `18baea0222c0`／tpex `05c3788311f8`）。
再往前：校準當時是 twse `b45aa4dac4dc`／tpex `313f6b5dd3c1`，之後被 **§17**（coverage 分母排除結構上不可得的族）
改過**一次**；**§18**（binding／過熱旗標三個出口欄）**只加輸出欄位、指紋不變**（三個 commit 實算：
`7c1103a` b45aa4dac4dc → `a4218d3` 0bb386e9cf3b → `6dde23f` 0bb386e9cf3b）。沿革見 `docs/P3-CALIBRATION.md` §17／§18／§31／§32。
**#68 之後、新種子合併之前每日班會紅**（`data/state/cross.json` 的 `params_sha` 仍是 #68 前的值，使用者裁定接受），重跑鏈見 §31／§32。
d 的規則與值見 `docs/P3-CALIBRATION.md` §9／§12／§32 與 `src/iching/score/calibrated.py` 的 `CALIBRATION_META`。
**尚未校準的仍是候選假說**：所有 `c`（裁定 #54 Q2 維持不動）、族／爻權重、`Rules` 門檻常數、
以及 90 個 `clip_policy=n/a` 的子指標。登錄書尚未凍結、保留段尚未動用。

## 佈局

- `spec/` — P1 規格五份（B1 大盤公式／B2 個股參數字典／B3 重播清單／B4 卦文接入／**B5 維度表**）
  ＋基準三份（v1.2.2／S1／S1a）＋ `hexagrams64.json`（64 卦位元事實來源）
- `spec/dimensions.json` — **鍵／維度宣告的機器可讀正本**
- `spec/tools/` — `check_dims.py`／`inject_test.py`／`tblcheck.py`／`gen_b5.py`
- `docs/` — P0-A 查核報告、P2 開工前置、預先登錄書
- `src/`、`data/`、`runs/` — P2 起

## 不可破壞的約定

1. **`P1-B5-dimensions.md` 是生成物，不得手改。** 改 `spec/dimensions.json` 後跑
   `python tools/gen_b5.py`。CI 比對 sha256，手改一定紅。這是「md 不得手改」的**唯一機器守門**。
2. **優先序**：`P1-B5` ＞ `P1-B4` ＞ `P1-B3` ＞ `P1-B2` ＞ `P1-B1` ＞ `S1a` ＞ `S1` ＞ `v1.2.2`。
   v1.2.2 的 §4（參數表）／§5（權重）／§9.2（環境×門檻×名額）**保留僅作沿革、不得據以實作**；
   已知衝突點列在五份 P1 文件開頭的「優先序」段（逐字相同）。
3. **凡「per X」的鍵／維度宣告，一律以 `spec/dimensions.json` 為唯一事實來源。**
   「兩市場實算相同」不是省略 `market` 的理由；「只出短線名單」不是省略 `horizon` 的理由。
   維度塌縮為 1 時**仍須宣告並寫死值**——省略就是第十次復發。
4. **校準母體限訓練段。** `d = p85 ÷ 3`，p85 取自訓練段樣本上的 `|x − c|`；暖機與驗證／保留段
   **不進母體**。樣本外截斷率超標**只觸發人工複核，不得自動重校準、不得回頭改 `d`**——
   依樣本外調參會使回測結論失效。
5. **保留段一經動用即消耗。** 由樣本外觀察觸發的模型變更不得再用同一份保留段驗證。
6. **誠實原則**（家族鐵律）：技術指標為現況描述、非買賣訊號；狀態詞中性、不寫該買該賣、不做預測。
7. **兩層儲存要 parity**（2026-09-09 裁定乙）：回補／回測在 Hetzner SQLite（不進 git），每日班在
   GitHub Actions 靠 git 內狀態＋當日 API 重算（進 git）。同一鍵同一版本三元組下兩層分數必須逐位相同
   （`spec/P1-B3-replay.md` §B3.2）。改任一層的算法，parity 測試不過就不能上。

## 已知坑

1. **「宣告的鍵少於實際的變動來源」在規格審查中復發九次**，第九次發生在為根治它而寫的 B5 表裡。
   所以才有 `check_dims.py` ＋ `inject_test.py`。**改維度宣告前先跑一次故障注入**，
   確認守門還活著——它被改弱過一次（旗標段整段沒守門，五支旗標拿掉 `market` 全綠）。
2. **本 session 無 Hetzner SSH、無 `FINMIND_TOKEN`。** P2 的歷史回補依使用者裁定
   走「Claude 寫腳本、使用者在 Hetzner 執行」；**每日班**則走 Worker 排程→Actions 執行（裁定乙，
   `docs/P2-KICKOFF.md` §5 第 11 列、§7），token 分別在 Hetzner `.env` 與本 repo Actions secret。
3. **上爻走美股交易日曆**，不是台北曆；重播需保存**兩份**日曆（`P1-B3-replay.md` 重播清單第 10 項）。

## 驗證方式

```bash
cd spec
python tools/check_dims.py && python tools/inject_test.py
python tools/tblcheck.py *.md
python tools/gen_b5.py && git diff --exit-code P1-B5-dimensions.md   # 必須無 diff
```
