# P2 開工前置：目標、完成定義、驗收方式

依 CANON 第 3 條「先寫驗收條件再動手」。本檔在動任何一行程式前寫成。

## 0. P2 的規格定義（v1.2.2 §14，逐字）

| 項目 | 內容 |
|---|---|
| **交付** | 歷史回補（Hetzner）、每日更新、收集器、持久狀態、品質報告、公告時間分布實測 |
| **授權邊界** | 需核准：**啟用 workflow 排程**、**Release 上傳**、**付費 AI**（可先關閉）|
| **驗收** | §16.1（公告漏收）、§16.2（並行寫入）、§16.3 |

## 1. 前置未滿足：P0-B 尚未授權

§14 的 **P0-B 初始化**（建立 repo、CANON 守門、notify-failure、目錄骨架、CLAUDE.md、預先登錄書骨架）
標明「**需核准：建立 repository**」，且**尚未取得**。P2 的所有產物都要落在該 repo，故 P0-B 是硬前置。

`list_repos` 實查（2026-09-09）：`shihpc/` 下**無**任何名稱含 `iching` 的 repo，P0-B 確未執行。

## 2. 本 session 的執行限制（必須先講清楚）

- **本 session 是雲端容器，無 Hetzner SSH**。P0-A 是使用者自己在 Hetzner 上跑腳本、把輸出貼回來完成的。
  P2 的「歷史回補（Hetzner）」**我無法在此直接執行**，只能：
  ①寫好腳本交給使用者在 Hetzner 跑；或②把回補改成在 GitHub Actions 跑（需 P2 的排程授權）。
- **`FINMIND_TOKEN` 不在本環境**（依 CANON 第 1 條，token 走 .env／Actions secret，且不得出現在對話輸出）。
  故任何需要真實 FinMind 資料的步驟，我在這裡都跑不了。
- **推論**：P2 若要在本 session 推進，實際可做的是「**寫出可交付的程式與 workflow**」，
  「**實跑**」必須落在 Hetzner（使用者執行）或 Actions（需授權）。CANON 第 6 條「沒實跑過不算完成」
  因此在 P2 會卡在授權與執行環境，**不是寫完程式就算 P2 完成**。

## 3. 完成定義（逐條可勾）

P2 視為完成，須全部成立：

| # | 條件 | 怎麼驗 |
|---|---|---|
| 1 | repo 建立且 `canon.yml` 綠燈 | Actions run 頁面 |
| 2 | 目錄與資料契約符合家族規範（`claude-harness/docs/data-contract.md`）| fresh-context 子代理比對 |
| 3 | `check_dims.py`／`tblcheck.py`／`inject_test.py` 納入 CI 且綠燈 | Actions run |
| 4 | 歷史回補**實跑完成**、產物落地、`coverage` 正確 | 子代理讀產物；抽日重播 |
| 5 | 每日更新班連續 **10 個交易日**每班有 `runs/collect/<date>-<band>.json` | §16.1 第 1 列 |
| 6 | 公告主旨層級缺漏率 ≤ 1%，每筆缺漏有原因碼 | §16.1 第 2 列，子代理跑比對腳本 |
| 7 | 公告發布時間分布實測（3 個月），晚於 21:30 占比 > 3% 即須加班次並重測 | §16.1 第 3 列 |
| 8 | 所有會 commit 的 workflow 宣告同一 `concurrency.group` 且 `cancel-in-progress: false` | §16.2 |
| 9 | 決定性重播測試通過（以 B3.1 清單重播 T，三個 horizon 各驗一次）| `P1-B3-replay.md` §B3.3 |
| 10 | **校準前**所有 c／d／權重／門檻仍標 `calibrated=false`；校準後才可改 true，且母體限訓練段 | `P1-B2-params.md` §B2.8 |

## 4. 不在 P2 範圍（避免範圍蔓延）

- 三期間回測、成本敏感度、事件探索 → **P3**
- 網站四入口、入口站卡、Worker 整合 → **P4**
- 盤中候選池 → **P5**
- **改既有五個 repo 的任何檔案** → 非 P2；本專案為獨立新 repo

## 5. 待使用者裁定（阻擋開工）

1. **P0-B 授權**：是否建立 repo？名稱與可見性？
2. **`shihpc/taiwan-backtest`**（public，2026-09-08 push）**先前未納入規劃**——
   它與 P3 回測的關係要先釐清，否則可能重複造輪子或口徑打架。
3. **排程**：P2 的 workflow 先「建好但停用」還是直接啟用？
4. **執行環境**：歷史回補走 Hetzner（你執行）還是 Actions（需排程授權）？
