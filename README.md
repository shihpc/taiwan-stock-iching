# 股市易經（taiwan-stock-iching）

把個股與大盤的量化狀態對應到 **64 卦六爻**，輸出可讀的現況描述與候選名單。
「股市雷達」家族第七站。

> **誠實原則**：技術指標為**現況描述、非買賣訊號**，僅供參考。狀態詞用中性色、不寫該買該賣、不做預測。
> 本站所有 AI 研判均標明為研判、非保證。

## 現況：P1 規格完成，P2 尚未開工

| 階段 | 狀態 |
|---|---|
| P0-A 盤點（唯讀）| ✅ 完成，見 `docs/P0A-report.md` |
| P0-B 初始化 | 🔄 進行中（本 commit）|
| P1 規格 | ✅ 完成，見 `spec/` |
| P2 資料 | ⬜ 未開工，前置與驗收條件見 `docs/P2-KICKOFF.md` |
| P3 驗證 / P4 網站 / P5 盤中 | ⬜ 未開工 |

**目前所有 c／d／權重／門檻一律標 `calibrated=false`** —— 它們是候選假說，不是已驗證的參數。
校準判準已定死（`d = p85 ÷ 3`，母體限訓練段），但**尚未用真實資料跑過**。

## 佈局

```
spec/     P1 規格（B1 大盤公式／B2 個股參數字典／B3 重播清單／B4 卦文接入／B5 維度表）
          ＋基準文件（v1.2.2、S1、S1a）＋ hexagrams64.json
spec/tools/  規格自檢工具（見下）
docs/     P0-A 查核報告、P2 開工前置、預先登錄書
src/      管線程式（P2 起）
data/     產出 JSON（P2 起）
runs/     每班留痕（§16.1 要求）
```

## 規格自檢工具

`spec/` 的規格由程式守門，不靠人工掃描——這是十二輪交叉檢查換來的教訓：
同一個根因（**宣告的鍵少於實際的變動來源**）復發了九次，其中第九次就發生在
為了根治它而寫的那張表裡。人工掃描每輪都找得到，但每輪也都會再犯一次。

```bash
cd spec
python tools/check_dims.py    # 維度機檢：鍵是否涵蓋所有變動來源（9 條規則）
python tools/inject_test.py   # 故障注入：守門本身還抓不抓得到已知的 32 種回歸
python tools/tblcheck.py *.md # 表格結構：欄數／分隔列／被切斷／孤兒區塊
python tools/gen_b5.py        # 由 dimensions.json 生成 B5；md 不得手改
```

`P1-B5-dimensions.md` 是 `dimensions.json` 的**生成物**。要改維度宣告請改 JSON 後重新生成——
CI 會比對 sha256，手改 md 一定紅。

## 資料契約

新產出的 JSON 頂層必含 `schema`／`generated_at`（ISO8601 +08:00，**產出時刻**）／
`date`（**資料日**，非產出日）／`status`，見 `claude-harness/docs/data-contract.md`。

## 授權邊界（尚未取得）

- 啟用 workflow 排程（目前所有 workflow **無 cron**；觸發僅 `push`／`pull_request`／`workflow_dispatch`）
- Release 上傳
- 付費 AI
- 改既有五站或入口站
