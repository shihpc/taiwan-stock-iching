"""P3 回測統計層——**純函式、不讀任何資料檔**（`docs/P3-KICKOFF.md` 完成定義 #5／#6；判準正本＝
`docs/pre-registration.md` §1.2.1／§1.2.4／§1.3／§1.4，凍結 tag `prereg-v2`，本套件只實作、不改動）。

- `constants` ：登錄書寫死的數字集中於此（`block_len`／`NBOOT`／`BLOCKS_MIN`／`IC_MIN`／`T_MIN`／
                `POS_SHARE_MIN`／`EMBARGO_DAYS`／`H_BY_HORIZON`／`DEFAULT_SEED`）
- `boot`      ：`block_boot_ci`／`nw_se`（借自 shihpc/taiwan-backtest `676c69b`，本體逐字）＋包裝層守門＋
                `block_count`（§1.4 T5 公式）
- `ic`        ：平均秩 Spearman 與逐日 IC 序列
- `windows`   ：purge／embargo／段界——**一律索引法**（§1.3 `:266-267`、§16.5 `:722`），本套件不出現日曆天數
- `cost`      ：個股成本（§1.2.1 `:91`；`net_ret_long` 由 `scripts/rank_table.py` 搬入，該腳本反向 import）＋
                大盤 TX 成本（§1.2.4）
- `groups`    ：分位組標籤、頂減底日序列、候選名單對全池等權超額日序列
- `metrics`   ：只報不設門檻的三項（§1.4 裁定 T4）＋月度彙總
- `verdict`   ：主要→次要→穩健 AND 判定；區塊數量近似值 <8 一律「證據不足」（§1.3 `:270`）

**刻意不在套件層 re-export**：`cost`／`constants`／`verdict` 只用標準庫，`scripts/rank_table.py` 反向依賴 `cost`
時不得被拖進 numpy（該腳本的「只用標準庫」慣例，Hetzner 系統層 Python）。使用端一律 `from iching.stats import boot`
或 `from iching.stats.cost import net_ret_long` 這樣按子模組取用。

依賴：numpy＋標準庫＋`iching.config`（`SEGMENTS`），**不 import `iching.score`**（`tests/test_stats_hygiene.py` 守門）。
"""
