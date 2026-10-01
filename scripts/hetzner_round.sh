#!/usr/bin/env bash
# D-3 對帳儀式的 Hetzner 回合（「一句話貼」版，claude-harness 02-judgment §6）：
#   bash scripts/hetzner_round.sh 2026-09-01 2026-09-14
# 做的事：0 同步 main 並印 HEAD（核對用）→ 1 回補 [FROM..TO] 原料（沿用 cache 內 data_version）
#   → 2 scan_features --resume（掃描仍從頭重播，只補寫新日）→ 3 replay_scores --resume（或指定快照重播，見 HETZNER_ROUND_REPLAY_STATE）
#   → 4 parity_check 寫 runs/parity/<FROM>_<TO>.txt（＋全部差異明細 <FROM>_<TO>.diff.jsonl.gz，--dump）
#   → 5 報告與明細一起 commit 到分支 hetzner/parity-<TO> 並 push。
# 使用者只需貼這一行；結果由 session 自己 fetch 那個分支，不用把輸出貼回來。
# 中途任一步失敗即停（set -e），log 在 cache/logs/parity-round-*.log；重貼同一行可續跑（各步皆冪等／可續）。
# 可選環境變數：
#   HETZNER_ROUND_SKIP_BACKFILL=1   離線煙霧測試，跳過第 1 步回補。
#   HETZNER_ROUND_REPLAY_STATE=<快照路徑>（2026-09-27 PR-5d）：第 3 步改以
#       replay_scores.py --from $FROM --to $TO --state <路徑> --window $WINDOW
#     取代 --resume。用途＝「參考分數已寫入 scores.db 但原料補齊後須重算」（D-3 第二輪：官方月表未滿月未重抓，
#     09-15～09-24 的參考分數是在缺 amount_k 的原料上算的，--resume 只會從快照 last_date 之後續跑、不會回頭重算既有日，
#     docs/P2-DAILY-PLAN.md §7.6.6）。快照必須是 **FROM 前一交易日**的 CrossDayState（replay_scores 會核 last_date＝FROM 的
#     前一交易日、meta.window／params_sha 與本次相同，不符即中止）；例如 `git show 87c5691:data/state/cross.json > cache/state_0914.json`
#     （last_date 2026-09-14）配 FROM=2026-09-15。**冪等**：`ScoreStore.write_day` 同一 (data_version, date) 整日取代
#     （src/iching/scores_io.py），[FROM..TO] 既有列被覆寫、其餘日不動；跑完 cache/scores.db.state.json 停在 TO（輸入快照不被覆寫）。
#     不需 --rebuild（12.6h 全量）也不需 --force 回補（未滿月鍵已由回補層自動放回 pending）。未設時第 3 步行為逐字不變。
set -euo pipefail
cd "$(dirname "$0")/.."
FROM=${1:?用法: hetzner_round.sh FROM(YYYY-MM-DD) TO(YYYY-MM-DD)}
TO=${2:?用法: hetzner_round.sh FROM(YYYY-MM-DD) TO(YYYY-MM-DD)}
for d in "$FROM" "$TO"; do
  [[ "$d" =~ ^[0-9]{4}-[0-9]{2}-[0-9]{2}$ ]] && [ "$(date -d "$d" +%F 2>/dev/null)" = "$d" ] || { echo "!! 日期須為合法的 YYYY-MM-DD：$d"; exit 2; }
done
[[ "$FROM" > "$TO" ]] && { echo "!! FROM 晚於 TO"; exit 2; }
mkdir -p cache/logs runs/parity
LOG="cache/logs/parity-round-$(date -u +%Y%m%dT%H%M%SZ).log"
exec > >(tee -a "$LOG") 2>&1
echo "== hetzner_round $FROM..$TO  $(date -u +%FT%TZ)  log=$LOG"

echo "== 0 同步 main 並核對 HEAD"
git reset -q                                                    # 上一輪若在 add 與 commit 之間中斷，先解除 staged
restore_calendars() {                                           # 回補 finally 會改寫兩份日曆；repo 那份才是每日班的，丟棄 Hetzner 派生版
  for f in data/calendar_tpe.json data/calendar_us.json; do
    git ls-files --error-unmatch "$f" >/dev/null 2>&1 && git checkout -q -- "$f" || true
  done
}
restore_calendars
if [ -n "$(git status --porcelain --untracked-files=no)" ]; then
  echo "!! 工作樹有未提交的追蹤檔改動，先處理再跑："; git status --short --untracked-files=no; exit 2
fi
git fetch -q origin main
git checkout -q main
git pull -q --ff-only origin main
git log -1 --format='HEAD %h %ci %s'

echo "== 1 回補 $FROM..$TO（沿用 cache 內 data_version）"
if [ "${HETZNER_ROUND_SKIP_BACKFILL:-0}" = "1" ]; then echo "（HETZNER_ROUND_SKIP_BACKFILL=1：離線煙霧測試，跳過回補）"; else
# 兩趟：全市場切片／官方端點的守門要求「同一 data_version 落地的 TAIEX 日曆」涵蓋區間（backfill_hetzner.py `calendar_covers`），
# 而該守門在同一趟內先於 index_price 落地就評估 → 第一輪實跑（2026-09-15）全部 daily_slice／official 計畫=0 中止。先補指數再補其餘。
python3 scripts/backfill_hetzner.py run --dataset stock_info index_price --from "$FROM" --to "$TO" --data-end "$TO" --progress-every 200
# dividend_result 走 per_stock（與 09-12 原始回補一致）：TaiwanStockDividendResult 全市場年區間回 200 空陣列（第三次實跑 empty_unexpected ×12），
# coverage 本來就在 per_stock 鍵下。--data-end 下 per_stock 鍵 `<sid>:2020-01-01~<TO>` 整段延伸並取代舊鍵，個股池約 2,100 檔＝約 2,100 次請求，
# --interval 預設下約 25 分鐘；同 TO 重跑會被 covered 跳過。
# financial_statements 也走 per_stock（2026-10-01 起，例行第二輪 09-29～09-30 兩次都停在這一趟）：TaiwanStockFinancialStatements 全市場不帶
# data_id 的區間查詢實測回 200 空陣列——09-12 原始回補 2019-06-01～2026-08-31 每一季 range_slice 鍵皆 empty_unexpected ×1、改 per_stock 後
# 2,051 ok／88 empty（docs/BACKFILL-RUNBOOK.md「策略被取代」段）；每日班同一支 client 實測 06-30～06-30 → 38,691 列、05-04～09-01 → 0 列
# （src/iching/daily_fetch.py 檔頭）。所以 range_slice 對它任何季塊都不會成功：未滿期季塊只被記 empty（什麼都沒落地），而 --data-end 恰為
# 季末（如 2026-09-30）時 Q3 塊 `2026-07-01~2026-09-30` 滿期、不再享 empty_ok_partial 豁免 → empty_unexpected、rc=6、set -e 中止。
# config 的 fallback 只在 PermissionRequired（400 含 level/sponsor）觸發，200 空陣列不會退回 per_stock，故在這裡固定指定。
# 代價同 dividend_result：per_stock 鍵 `<sid>:2019-06-01~<TO>` 整段延伸並取代舊鍵，每輪多約 2,100 次請求 ≈ 25 分鐘；per_stock 是
# empty_ok_for 的策略，Q3 季報上線前回空只記 empty、不失敗（合法 empty），季報上線後下一輪延伸即補進。
python3 scripts/backfill_hetzner.py run --from "$FROM" --to "$TO" --data-end "$TO" --strategy dividend_result=per_stock financial_statements=per_stock --progress-every 200
restore_calendars                                               # 同上：不讓回補派生的日曆弄髒工作樹（對帳要用 repo 那份）
fi

echo "== 2 scan_features --resume（掃描從頭重播、只補寫新日）"
python3 scripts/scan_features.py --resume --progress-every 400

WINDOW=$(python3 -c "import json;print(json.load(open('data/state/cross.json'))['meta']['window'])")
if [ -n "${HETZNER_ROUND_REPLAY_STATE:-}" ]; then
  # 指定快照重播（見檔頭說明）：--from/--to/--state 取代 --resume；[FROM..TO] 整日取代、冪等；快照守門由 replay_scores 自己做。
  [ -f "$HETZNER_ROUND_REPLAY_STATE" ] || { echo "!! HETZNER_ROUND_REPLAY_STATE 指向的快照不存在：$HETZNER_ROUND_REPLAY_STATE"; exit 2; }
  echo "== 3 replay_scores --from $FROM --to $TO --state $HETZNER_ROUND_REPLAY_STATE --window $WINDOW（指定快照重播，整日取代 [FROM..TO] 既有列；window 取自 data/state/cross.json，不符會被快照參數守門擋下）"
  python3 scripts/replay_scores.py --from "$FROM" --to "$TO" --state "$HETZNER_ROUND_REPLAY_STATE" --window "$WINDOW" --progress-every 5
else
  echo "== 3 replay_scores --resume --window $WINDOW（window 取自 data/state/cross.json，與每日班一致；不符會被快照參數守門擋下）"
  python3 scripts/replay_scores.py --resume --window "$WINDOW" --progress-every 5
fi

REPORT="runs/parity/${FROM}_${TO}.txt"
DUMP="runs/parity/${FROM}_${TO}.diff.jsonl.gz"                  # 全部差異的 JSON Lines（分數逐欄＋⑤⑥檔級），一輪 58k 列壓縮後數 MB
echo "== 4 parity_check → $REPORT（明細 $DUMP）"
set +e
python3 scripts/parity_check.py --cache-dir cache --repo . --from "$FROM" --to "$TO" --show 50 --dump "$DUMP" 2>&1 | tee "$REPORT"   # stderr 也進報告：session 只 fetch 分支時才看得到 rc=2 的原因
RC=${PIPESTATUS[0]}
set -e
grep -q '^結果：rc=' "$REPORT" || { echo "!! parity_check 未正常結束（報告無「結果：rc=」行，多半是未被捕捉的例外），視為中止"; RC=2; }
{ echo; echo "parity rc=$RC  HEAD=$(git rev-parse --short HEAD)  at=$(date -u +%FT%TZ)"; } | tee -a "$REPORT"

echo "== 5 報告 commit＋push 到 hetzner/parity-$TO"
BR="hetzner/parity-${TO}"
git checkout -q -B "$BR"
git add "$REPORT"
[ -f "$DUMP" ] && git add "$DUMP"                                 # rc=2 中止時可能沒寫出（比對前就停），不因缺檔而卡住
if git diff --cached --quiet; then echo "（報告無變更，不新增 commit）"; else
  git -c user.name="hetzner-round" -c user.email="hetzner-round@users.noreply.github.com" \
    commit -q -m "parity: Hetzner 對帳 ${FROM}..${TO} rc=${RC}"
fi
git push -q -f origin "$BR"
git checkout -q main
echo "== done rc=$RC  分支 $BR  報告 $REPORT"
exit "$RC"
