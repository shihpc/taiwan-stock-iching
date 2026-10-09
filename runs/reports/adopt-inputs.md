# scan_inputs adopt 報告（RUNBOOK §4.8，裁定 #73 C）

- 執行機：Hetzner `/root/projects/taiwan-stock-iching`（UTC），2026-10-09
- 程式版本：main `051ccac`（#114）
- 結論：**前提成立 → adopt rc 0 → 驗證輪 `--resume` rc 0、寫 0 日**；`scan_inputs` 已有基準（1642 日），features 參數指紋仍 `084c1b9b8348`。

## 1. 前置檢查

- 相關程序：none（`pgrep -af` 只命中自身 shell；以 `ps | grep -v` 排除自身後為 none）
- `tmux ls`：`claude: 2 windows`（`claude:0 bash`、`claude:1 rc`；未動）
- `git status --short --untracked-files=no`：空；分支 main

## 2. 更新 main

`git log -1` → `051ccac C（裁定 #73）：hetzner_replay 預設重建特徵庫＋scan_features --resume 輸入指紋守門（rc 4）＋例行輪接 rc 4 (#114)`

## 3. 唯讀前提檢查（§4.8）

`scan_meta`（adopt 前）：`fm-20260911-01`／schema 1／params_sha `084c1b9b8348`／first_written_at `2026-10-07T15:21:01+00:00`／last_written_at `2026-10-07T15:21:01+00:00`
adopt 前 scan_day：`fm-20260911-01` 1642 日，2020-01-02～2026-10-06。

各庫 coverage（fetched_at 為 +08:00，已解析成 aware datetime 後與 UTC 起點比較；全部值皆帶時區、比較無 naive/aware 錯誤）：

| 庫 | coverage 鍵數 | MAX(fetched_at) | 換算 UTC | rebuild 之後落地 |
|---|---|---|---|---|
| prices.db | 5553 | 2026-10-07T23:16:41+08:00 | 15:16:41Z | **0** |
| universe.db | 1 | 2026-10-07T23:01:57+08:00 | 15:01:57Z | **0** |
| chips.db（參考） | 6568 | 2026-10-07T00:48:20+08:00 | 10-06 16:48:20Z | 0 |
| market.db（參考） | 5189 | 2026-10-07T01:14:16+08:00 | 10-06 17:14:16Z | 0 |
| fundamentals.db（參考） | 2499 | 2026-10-07T23:16:43+08:00 | 15:16:43Z | 0 |

判讀：rebuild 起點 15:21:01Z 之後，五庫皆 0 鍵落地（最晚的 fundamentals 15:16:43Z 早於起點約 4 分鐘）。檔案 mtime 佐證：除 features.db／scores.db 外各原料庫 mtime 皆 ≤ 2026-10-07 15:16 UTC。**前提成立，可 adopt。**

## 4. adopt

指令：`python3 scripts/scan_features.py --resume --adopt-inputs --progress-every 400`（log：`cache/logs/adopt-inputs-20261009T123900Z.out`）

```
還原係數 事件源 div+capred+split+par-1：dividend 10,781／capred 267／split 34／parvalue 0（…合併後 11,082 列）；band 外 13 筆（只報不擋）：…6949 2026-09-07 split 20.0000(1490.0/74.5)
[resume] fm-20260911-01 已有 1642 日；**掃描仍從頭重播**（deque 需要歷史），只是不重寫
!! --adopt-inputs：把現況原料當成已寫 1642 日的基準。前提＝自上次 --rebuild 後沒有新原料落地…
data_version=fm-20260911-01 參數指紋=084c1b9b8348 池=2143 檔 除權息=1927 檔
  400 日（2021-08-24） 寫 0　49s … 1600 日（2026-08-05） 寫 0　212s
掃 1642 日、寫 0 日，218.2s（132.9 ms/日）
[resume] 要寫的 1642 日都已存在，未新增。
rc=0
real 4m19s
```

## 5. 守門驗證

再跑 `python3 scripts/scan_features.py --resume --progress-every 400`：同樣 `掃 1642 日、寫 0 日，187.7s`、`要寫的 1642 日都已存在，未新增`，**rc=0**（未觸發 rc 4）。

驗證後唯讀查詢：

- `scan_inputs`：5713 列，全部 `fm-20260911-01`
  - `day` 1642（2020-01-02～2026-10-06，與 scan_day 一致）
  - `factor` 1927（＝除權息 1927 檔）
  - `pool` 2143（＝池 2143 檔）
  - `meta` 1：`{"adopted": true, "factor_sha": "165bc8528116", "last_scanned": "2026-10-06", "mode": "resume", "pool_sha": "971b2c950445", "scan_from": "2020-01-02", "version": 1, "written_at": "2026-10-09T12:46:49+00:00"}`
- `scan_meta`：`fm-20260911-01`／1／`084c1b9b8348`／first_written_at `2026-10-07T15:21:01+00:00`／last_written_at `2026-10-09T12:46:49+00:00`
- `scan_day`：1642 日，2020-01-02～2026-10-06（不變）
- features 參數指紋：**`084c1b9b8348`（不變）**

備註：`last_written_at` 與 meta `written_at` 為 12:46:49Z，時間落在第 5 步驗證輪（adopt 約 12:43Z 結束），符合 §4.8「`--resume` 守門通過時合併更新」；`adopted: true` 保留。這是推論（依時間），未逐行查程式。

## 6. 收尾

`git status --short --untracked-files=no`：空；分支 main。未跑 hetzner_round／hetzner_replay／backfill_hetzner／replay_scores／export_dataset；cache/ 下只有 features.db 被 adopt（及驗證輪的守門合併更新）寫入；未刪檔。

## 後續

例行輪 `hetzner_round.sh` 第 2 步現在有基準可比；下一輪若 `--resume` 回 rc 4，依 §4.8 整庫重建。§7.6 的 A（原料包重匯）等排程不在本報告範圍。
