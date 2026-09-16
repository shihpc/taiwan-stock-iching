# P2 公告收集器——驗收條件（2026-09-16，動手前寫；裁定 #43／#45）

規格來源：`spec/stock-iching-plan-v1.2.2.md` §7.2／§7.4／§12.1／§12.3／§12.4／§16.1、
`spec/stock-iching-S1-supplement.md` A3（事件版本鏈）、`docs/P0A-report.md` §5／修訂表第 7／11／12／13／15 列、
`docs/P2-KICKOFF.md` 完成定義 #5～#8。本文件只寫**P2 收集器**的範圍與驗法；AI 抽取／shadow 計分／事件探索屬 P3。

## 0. 前置：Actions 可達性（裁定 #45，失敗即停）

`.github/workflows/probe-mops.yml`（一次性、只 `workflow_dispatch`）跑 `scripts/probe_mops.py`：
mopsfin `t187ap04_L.csv`／`_O.csv` 與 mopsov `ajax_t05st01`（POST／GET 各試）三端點回 200、**非 WAF 擋頁**、
解析列數 > 0。任一失敗 → 停下來回報使用者，不改走 Worker 代抓。結果記在本文件 §5。

## 1. 範圍

| 項目 | 做 | 不做（本批） |
|---|---|---|
| 來源 | ①`t187ap04_L/_O.csv`（當日快照，含說明全文）②mopsov 全市場單日列表（只有主旨，供隔日核對與補主旨） | mopsov 詳情頁全文（參數未驗）、MOPS 新版 API、新聞 |
| 落地 | `data/events/<發言日 YYYY-MM-DD>.json.gz`，以**發言日期**為鍵（修訂表第 15 列：23:45 班延遲跨日不可用執行日） | 歷史回補（mopsov 隨時可回補，P3 需要時再做） |
| 事件模型 | A3.1 最小版本鏈：`event_id`／`version_no`／`version_id`／`content_hash`／`status`／`supersedes`／`fulltext_missing`／`first_seen_at`／`revised_at`＋原始欄位（市場、代號、名稱、發言日期時間、主旨、符合條款、事實發生日、說明） | `relates_to`、`type`／方向／級距（P3 抽取） |
| 留痕 | 每班 `runs/collect/<班次排程日>-<band>.json`（**用排程日不用執行日**：23:45 班被 cron 延到隔日 00:xx 執行時，用執行日會與隔日自己的 23:45 班撞名；`started_at` 記真實執行時刻）：`scheduled_for`／`started_at`／`completed_at`／來源／收到／新增／更新版本／錯誤 | — |
| 核對 | 隔日首班以 mopsov 單日查詢（sii＋otc）對前一日事件庫比對，缺漏逐筆標原因碼（跨日發言／撤回／來源當時未出／WAF／晚於末班），寫 `runs/collect/<日>-verify.json` | — |
| 分布統計 | 每筆 `發言時間` 累積進 `data/events/_timing.json`（按日：各時段筆數、晚於 21:30 筆數），供 #7 三個月統計 | 門檻裁定（整體 vs 單日最差，P0-A 兩值判定相反，文件未裁） |
| 排程 | `collect-events.yml` 自帶 GH cron：台北 15:30／18:30／23:45 ＋隔日 08:30 補抓核對班；`workflow_dispatch` 帶 `band`／`date` 供補跑 | Worker dispatch 角色（另案） |
| commit | 沿用 `daily.yml` 的 inline 作法：`concurrency.group: iching-commit`／`cancel-in-progress: false`（完成定義 #8）、`pull --rebase` 重試、`notify-failure`（pipeline `iching-collect`）；**只寫 `data/events/` 與 `runs/collect/`**（§12.3 各 workflow 只寫自己的目錄） | Release 快照、manifest（§12.2，另案） |
| 執行地點 | 全部 Actions（判準 §6：資料不只在 Hetzner、非長跑） | Hetzner |

## 2. 固定的實作事實（P0-A 實測，改前先讀）

- 發言時間 `70003` 是無前導零 HHMMSS，補零至 6 碼（修訂表第 13 列）；「說明」含 `\r\n`，一律正規 CSV parser。
- **TPEx CSV 欄名實測是中文**（2026-09-16 Actions run 35045679279：`_O.csv` 表頭與 `_L.csv` 同一組九欄
  `出表日期／發言日期／發言時間／公司代號／公司名稱／主旨／符合條款／事實發生日／說明`），**與 P0-A 修訂表第 13 列記的英文欄名
  （`Date`／`SecuritiesCompanyCode`／`CompanyName`）不符**。收集器以表頭內容判定映射、兩組都收（`probe_mops.CSV_EXPECT` 的做法），
  不得寫死其中一組；哪一組命中要記進 `runs/collect` 留痕。
- 民國年 7 碼 `1150907`；一律轉 `YYYY-MM-DD`。
- 當日 CSV 是滾動快照、**窗大小未驗**（P0-A 待驗證第 13 列）：第一週要記錄每班 CSV 的最早／最晚發言時間，決定 23:45 一班是否已足夠涵蓋全文。
- WAF 擋頁：HTTP 200／307 都見過、body 含「FOR SECURITY REASONS」；收集器對每個回應都要先判擋頁，擋頁＝該來源失敗（原因碼 `waf`），**不得當成空資料**。
- 冪等：同一輸入跑兩次事件檔逐位相同；內容未變不寫檔、不 commit（A3.3）。

## 3. 怎樣算完成（每條附驗法）

1. **probe 通過**（§0）——Actions run 的 step summary 三端點 OK；記錄 run id 與日期於 §5。
2. **離線測試**（`tests/test_collect_events.py`，免網路）：
   (a) 三個陷阱樣本（`70003`、`\r\n` 說明、TPEx 英文欄名）逐欄解析正確；
   (b) 同一輸入跑兩次 `data/events/<d>.json.gz` 位元組相同；第二次不產生任何寫入；
   (c) 同 `event_id` 更正版只有主旨 → `version_no` 遞增、舊版 `status=superseded`、新版 `fulltext_missing=true`（A3.2 規則 2）；
   (d) 執行時鐘為台北 00:10 而發言日期為前一日 → 落在前一日的檔（修訂表第 15 列）；
   (e) 來源回 WAF 擋頁 → 該班 `runs/collect` 記 `waf`、不寫事件、exit 非 0（觸發 notify-failure）；
   (f) 核對腳本對合成缺漏各給正確原因碼（五種各一例）；
   (g) `test_daily_run.py::test_daily_workflow_yaml` 不受影響；新增 `collect-events.yml` 的 yaml 測試釘 cron 四條、`concurrency`、`permissions`、notify-failure。
3. **線上**：合併後連續 **5 個交易日**每班都有 `runs/collect/<日>-<band>.json`，`data/events/<日>.json.gz` 每交易日一檔；任一班失敗有 issue。10 日由後續觀察累積（完成定義 #5 同型）。
4. **#6 核對**：第一週每日 verify 檔的缺漏率與原因碼分布列成表；≤ 1% 才算通過，**超過就照實回報、不硬過**。
5. **#7 分布**：`_timing.json` 從第一次實跑起累積；3 個月後統計整體與單日最差兩個值，門檻由使用者裁。
6. 全套 pytest 綠、ruff 乾淨、fresh-context 驗收綁 commit（T5 模板），改動者不自驗。

## 4. 未決（動手時遇到再問，不自行擴張）

- mopsov 單日查詢的參數形狀（POST／GET、`b_date` 格式）以 probe 實測為準，寫進 §5。
- 兩市同一公告在 CSV 與 mopsov 的主旨正規化規則（全形／空白／截斷），第一週實測後定。
- `_timing.json` 是否改進 `runs/`（不進事件檔）——先放 `data/events/`，理由是它是 #7 的交付物、不是留痕。

## 5. 交付紀錄

### 5.1 probe（2026-09-16，通過）

Actions run **35045679279**（分支 `claude/dazzling-maxwell-serk13`，commit `ccf701a`，台北 09:51；前七輪 35044727905～35045554891
是逐步修正判定與參數的過程）。**結論：三端點從 Actions runner 全部可達、非 WAF 擋頁、有資料**——P0-A 修訂表第 7 列的
「Actions 的 WAF 行為未驗證」到此驗畢，裁定 #45 的「失敗即停」沒有觸發。

| 端點 | 狀態 | 大小 | 內容 |
|---|---|---|---|
| `mopsfin …/t187ap04_L.csv` | 200 `text/csv` utf-8-sig | 174,105 B | 108 列，中文九欄表頭 |
| `mopsfin …/t187ap04_O.csv` | 200 `text/csv` utf-8-sig | 96,835 B | 52 列，**中文九欄表頭**（非 P0-A 記的英文） |
| `mopsov …/ajax_t05st01` sii 115/09/15 | 200 `text/html; charset=UTF-8` | 94,206 B | 113 `<tr>`＝表頭＋112 列 |
| `mopsov …/ajax_t05st01` otc 115/09/15 | 200 | 46,406 B | 53 `<tr>`＝表頭＋52 列 |

**mopsov 查詢參數的真實語意（抓表單頁 `mopsov.twse.com.tw/mops/web/t05st01` 實查，run 35045554891）**：
`form1` action=`/mops/web/ajax_t05st01`、method POST；`year`＝民國 3 碼、`month`＝兩位、**`b_date`／`e_date`＝只有「日」（`01`～`31`）**、
`TYPEK`＝`sii`／`otc`（表單預設值是 `all`，**`all` 未實測**）、`co_id` 空＝全市場；隱藏欄位 `step=1`／`firstin=ture`（照表單原樣送，
**`firstin=1` 在此參數組下未實測**——只有舊參數組試過 `1`）／`off=1`／
`keyword4`／`code1`／`TYPEK2`／`checkbtn`／`queryName=co_id`／`inpuType=co_id`／`encodeURIComponent=1`。
**spec §12.4 只寫了參數名、沒寫語意**：把整個日期塞進 `b_date`（`1150915`）回無錯誤的空殼、`115/09/15` 回「起始日輸入錯誤」，
不帶日只給年月回「未指定公司代號時，僅能查詢單日重大訊息」；`ajax_t05st02` 對**舊參數組**（整個日期塞進 `b_date`）回「資料庫中查無需求資料」，
正確的「日」參數組**沒有打過** t05st02——收集器不用它，這點不再追。
回應是 HTML 表格：`公司代號｜公司名稱｜發言日期（115/09/15）｜發言時間（06:41:17）｜主旨`，格內有 `&nbsp;` 前綴要去掉。

**本雲端容器的對照**：三端點全部回擋頁（`FOR SECURITY REASONS`，CSV 端點還是 HTTP 200）——收集器在本機／雲端 session 測不了線上，
只能離線 mock；線上驗證一律看 Actions run。

**待第一週實測**：CSV 滾動窗大小（09:51 抓到的 108／52 列 vs mopsov 前一日 112／52 列，是否含前一日全部）。

### 5.2 實作交付（2026-09-16，離線驗證；線上要等合併後第一週 Actions run）

**檔案**：`src/iching/announce.py`（純函式核心：解析／`event_id`／`content_hash`／`merge`／`write_if_changed`／`timing_update`／
`verify_day`／`fetch_csv`／`fetch_mopsov`／`pick_band`）、`scripts/collect_events.py`（子命令 `collect`／`verify`，`FixtureHttp`
離線重放）、`.github/workflows/collect-events.yml`（四條 cron＋`workflow_dispatch`）、`tests/test_collect_events.py`
（§3 第 2 條 (a)～(g) 逐條、冪等、原因碼五例、`pick_band` 判定表、yaml）。`daily.yml`／`test_daily_run.py` 未動。

**事件檔** `data/events/<發言日>.json.gz`（`bundle_io.dumps_json`：鍵排序、無空白、gzip mtime=0 → 同輸入同位元組），一筆範例：

```json
{"schema":1,"date":"2026-09-16","events":{"672bc51ab48c1398":{"market":"sii","stock_id":"2330","name":"台積電",
 "spoke_date":"2026-09-16","spoke_time":"07:00:03","versions":[{"version_no":1,"version_id":"672bc51ab48c1398#1",
 "content_hash":"0f51d2b2…","status":"active","supersedes":null,"fulltext_missing":false,
 "first_seen_at":"2026-09-16T15:31:02+08:00","revised_at":"2026-09-16T15:31:02+08:00",
 "subject":"公告本公司董事會決議","clause":"第11款","fact_date":"2026-09-16","body":"1.事實發生日:115/09/16\n2.說明:…","sources":["csv"]}]}}}
```

- `event_id`＝`sha1("{market}|{stock_id}|{spoke_date}|{spoke_time}")[:16]`，**不含主旨**：同一公告的更正版（mopsov 只有主旨）與
  全文補齊（CSV 晚到）才會落在同一鏈；MOPS 更正公告通常是新發言時間＝新事件，`relates_to` 留 P3。
- `content_hash`＝sha1(正規化 `subject|clause|fact_date|body`)；正規化＝去 `&nbsp;`／U+00A0、全形空白→半形、合併空白；說明換行統一 `\n`。
- 合併（A3.2）：①最新版 `fulltext_missing` 且新列有 body 且主旨同 → 同版補全（`revised_at` 不動）；②hash 不同 → 新版本
  （舊版 `superseded`、`supersedes`、`revised_at=now`、無 body 則 `fulltext_missing`）；③相同 → 只併 `sources`。
  **②的刻意收窄**：只有主旨的列（mopsov）主旨與最新版相同時一律視為③——否則每次隔日核對都會替每個事件多開一版
  （A3.2 規則 1「同版本可由 CSV 與 mopsov 各取一份 → 取較完整者」）。`sources` 是來源名的排序去重清單（`csv`／`mopsov-verify`）。
- `_timing.json`：`{"schema":1,"days":{<日>:{hour_counts:{"00"…"23"}, after_2130, total, by_market:{sii,otc}}}}`，
  由全部事件檔**重算**（非增量），一事件計一次（看事件的 `spoke_time`，不看版本）。頂層多包一層 `days` 而非把日期直接當頂層鍵，
  是為了放 `schema`。

**留痕** `runs/collect/<班次排程日>-<band>.json`：`schema, band, scheduled_for, started_at, completed_at, sources:[{name, market, status
(ok|waf|error), http_status, bytes, rows, header_kind(zh|en), waf, dropped, span:{earliest,latest}, error}], received, new_events,
new_versions, filled, unchanged, files:[{date, rows, written,…}], errors, timing_written`。`span`＝該班 CSV 的最早／最晚發言時間
（§2「第一週要記錄」）。**檔名用班次排程日不用執行日**：23:45 班被 cron 延遲到隔日 00:xx 執行時，用執行日會與隔日自己的 23:45 班撞名；
執行時刻看 `started_at`。核對檔 `runs/collect/<發言日>-verify.json`：`date, mopsov_total, matched, missing:[{event_id, market, stock_id,
spoke_date, spoke_time, subject, reason}], missing_rate, reasons:{五碼各幾筆}, sources, files_written`；mopsov 任一市場失敗時
`mopsov_total`／`missing_rate` 為 `null`（半份列表比出的缺漏率是假的，不下判斷）。

**原因碼**（`announce.missing_reason`，依序命中第一個）：

| 碼 | 判定 |
|---|---|
| `cross_day` | 列的 `spoke_date` ≠ 核對日（列補進它自己那天的檔） |
| `withdrawn` | 主旨含「撤銷」或「撤回」 |
| `late_after_last_band` | `spoke_time` > `23:45:00` |
| `waf` | 該發言日任一收集班留痕（1530／1830／2345／隔日 am）有來源 `waf=true` |
| `source_missing_at_time` | 其餘（來源當時未出） |

缺漏列一律補進事件庫（主旨層、`fulltext_missing=true`、source `mopsov-verify`、`first_seen_at`＝當下、不回填）。**只由核對補回的事件
在下一次核對仍算缺漏**（看 `sources` 有沒有非 `mopsov-verify` 的來源）——核對報告才冪等，重跑不會把缺漏率洗成 0。

**`--band auto` 判定表**（`announce.pick_band`，台北時鐘、取最近一個已到點的班；cron 延遲 1～2 小時仍認對）：

| 台北時刻 | band | scheduled_for 日 |
|---|---|---|
| 00:00～08:29 | `2345` | **前一日** |
| 08:30～15:29 | `am` | 當日 |
| 15:30～18:29 | `1530` | 當日 |
| 18:30～23:44 | `1830` | 當日 |
| 23:45～23:59 | `2345` | 當日 |

`am` 班＝先核對「上次 verify 過的日＋1」到昨日的每個**曆日**（含週末，mopsov 回「查無」＝0 列照樣留痕；上限 7 日取最近；無紀錄只做昨日），
再做一次 collect。失敗處置：來源回擋頁／非 200／例外＝該來源失敗（留痕 `status`、不寫事件、整班 rc≠0），另一來源照常落地；workflow
先 commit 留痕再依 rc 讓 job 紅（順序不可反）。請求間隔 ≥1 秒、UA 同 probe、不吃 secret。

**驗收退回修正（19203f3 之後、同工作樹）**：①`data/events/.gitkeep` 進 git＋workflow 逐一 add 存在的目錄（首次 run 兩來源皆失敗時 `data/events` 不存在，合併 add 會 128、留痕整包沒進 staging）；②`--date` 走 argparse type 驗 `YYYY-MM-DD`＋真日期，workflow 的 step output 改經 `env:` 進 shell、不內插進 `run:`；③`merge` ①補全加守門（`first_seen_at`／`revised_at`／`version_no` 等改到就拋）；④CSV 欄數少於表頭的列丟棄並計入 `dropped`（不混進事件庫變 `fulltext_missing`）；⑤`parse_mopsov` 以 `<tr>` 開標籤切段（外層 wrapper 列不吞內層第一列）、「查無」改成「解析不到任何列且頁面含該字」才回空，**既無列也無「查無」→ `AnnounceError`（fetch 層記 `status:error`，不記成 0 筆 OK）**。

**離線驗證（2026-09-16）**：`pytest tests/ -q` 828 passed／20 skipped（本批新增 9 支，`test_collect_events.py` 24→33 支）；ruff 對新檔零項；同輸入跑兩次事件檔 sha256 相同、
第二次 `write_if_changed` 回 False；突變自測（拿掉 `merge` 的 `version_no+1` → §3 (c) 那支紅、還原後綠）；
`collect_events.py collect --band 1530 --fixture-dir <dir>` 跑通並寫出三個檔。**未驗**：線上端點（本容器 WAF）、§3 第 3～5 條要合併後累積。

### 5.3 線上首班（2026-09-16，手動 dispatch `am` 班，run 35049861738，commit `6cb8733`）

PR #25 合併後不等 15:30 cron，先 dispatch 一次 `am` 班把三個來源一次驗到。**成功**：
mopsov 09-15 上市 110／上櫃 52 列（真實 HTML 解析正確，`<tr>`／`&nbsp;`／民國日期都如 §5.1 所述）；
`t187ap04_L.csv` 108 列（發言 09-15 06:41:17～21:28:58）、`_O.csv` 52 列（07:00:03～23:14:24），表頭皆中文；
`data/events/2026-09-15.json.gz` 162 則＋`_timing.json`＋`runs/collect/2026-09-16-am.json`＋`2026-09-15-verify.json` 進 main。

**三個發現**：
1. **假版本一則（已修）**：6239 18:08:26 的主旨，mopsov 給「⾦」（U+2FA6）、CSV 給字面 `&#12198;`（同一字的 HTML 實體）
   → 首版 `norm_text` 沒 unescape，被當成更正開了 v2（真檔另有 2546／2438 的 v1 主旨也存了字面實體 `&#29670;`／`&#63799;`）。
   修法只有一件：`norm_text`／`norm_body` 先 `html.unescape`（`&#12198;` 解出來就是 U+2FA6，**不需要 NFKC**——曾試過加 NFKC，
   覆驗實測它讓既有檔全形標點的雜湊全數失效，已撤回）。**連帶的結構性修正**：`merge` 比對改用**現行算式重算儲存欄位**
   （`row_hash(latest)`），不再信檔內存的 `content_hash` 字串——否則任何正規化調整都會讓既有事件被判成「有變」而開假版本
   （覆驗實測：**在 440f750 的 NFKC 算式下**只改算式不改比對，09-15 檔 162 則餵回自己會開 144 個假版本；在現行 unescape-only
   算式下同一突變開 3 個——正是檔內存舊算式 hash 的 6239 v2／2546 v1／2438 v1；修後同一實驗 0 新版本、160 unchanged、2 只加來源）。
   **這 3 則檔內的 `content_hash` 刻意不回寫**——比對維持重算就無害；日後誰把比對改回信檔內 hash，這 3 則會立刻各開一版。
   回歸測試 `test_html_entity_in_csv_subject_matches_decoded_mopsov_subject`（6239 真實資料）與
   `test_merge_compares_recomputed_hash_not_stored_string`（儲存 hash 為舊值仍 unchanged、真有變仍開版）。
   **09-15 檔內 6239 那則 v1/v2 刻意不回頭改**（兩版是同一公告、active 版有全文；重寫首日檔會動 `first_seen_at`）。
2. **2 則只在 mopsov、不在 CSV**：9110（15:55:35）、9105（17:30:10），都是 9 開頭存託憑證，留 `fulltext_missing=True`、
   原因碼 `source_missing_at_time`。是否「CSV 不含 DR」要看幾天樣本，先觀察。
3. **CSV 疑似「每日一檔 T−1」而非盤中滾動**：10:57 抓到的兩支 CSV 位元組與 09:51 probe **完全相同**（174,105／96,835），內容全是
   09-15 的發言、沒有任何 09-16 的列。若 15:30／18:30 兩班抓到的還是同一份，代表全文只在 T+1 早上出現，
   三個盤中班對全文毫無貢獻，四班設計要重議（例如只留 am 班抓 T−1 全文＋核對、其餘班改抓 mopsov 當日主旨）。**待 09-16 的
   15:30／18:30／23:45 三班留痕的 `span`／`bytes` 定案，改 cron 前先問使用者。**

**首日核對報告的正確讀法**：`2026-09-15-verify.json` 記「缺漏 162／162（1.0）」——那是**啟動日**的必然（核對跑在事件檔存在之前），
不是 #6 的量測值；#6 從第二個核對日起算。
