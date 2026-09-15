#!/usr/bin/env bash
# D-3 對帳儀式的 Hetzner 回合（「一句話貼」版，claude-harness 02-judgment §6）：
#   bash scripts/hetzner_round.sh 2026-09-01 2026-09-14
# 做的事：0 同步 main 並印 HEAD（核對用）→ 1 回補 [FROM..TO] 原料（沿用 cache 內 data_version）
#   → 2 scan_features --resume（掃描仍從頭重播，只補寫新日）→ 3 replay_scores --resume
#   → 4 parity_check 寫 runs/parity/<FROM>_<TO>.txt → 5 報告 commit 到分支 hetzner/parity-<TO> 並 push。
# 使用者只需貼這一行；結果由 session 自己 fetch 那個分支，不用把輸出貼回來。
# 中途任一步失敗即停（set -e），log 在 cache/logs/parity-round-*.log；重貼同一行可續跑（各步皆冪等／可續）。
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
python3 scripts/backfill_hetzner.py run --from "$FROM" --to "$TO" --data-end "$TO" --progress-every 200
restore_calendars                                               # 同上：不讓回補派生的日曆弄髒工作樹（對帳要用 repo 那份）
fi

echo "== 2 scan_features --resume（掃描從頭重播、只補寫新日）"
python3 scripts/scan_features.py --resume --progress-every 400

WINDOW=$(python3 -c "import json;print(json.load(open('data/state/cross.json'))['meta']['window'])")
echo "== 3 replay_scores --resume --window $WINDOW（window 取自 data/state/cross.json，與每日班一致；不符會被快照參數守門擋下）"
python3 scripts/replay_scores.py --resume --window "$WINDOW" --progress-every 5

REPORT="runs/parity/${FROM}_${TO}.txt"
echo "== 4 parity_check → $REPORT"
set +e
python3 scripts/parity_check.py --cache-dir cache --repo . --from "$FROM" --to "$TO" --show 50 2>&1 | tee "$REPORT"   # stderr 也進報告：session 只 fetch 分支時才看得到 rc=2 的原因
RC=${PIPESTATUS[0]}
set -e
grep -q '^結果：rc=' "$REPORT" || { echo "!! parity_check 未正常結束（報告無「結果：rc=」行，多半是未被捕捉的例外），視為中止"; RC=2; }
{ echo; echo "parity rc=$RC  HEAD=$(git rev-parse --short HEAD)  at=$(date -u +%FT%TZ)"; } | tee -a "$REPORT"

echo "== 5 報告 commit＋push 到 hetzner/parity-$TO"
BR="hetzner/parity-${TO}"
git checkout -q -B "$BR"
git add "$REPORT"
if git diff --cached --quiet; then echo "（報告無變更，不新增 commit）"; else
  git -c user.name="hetzner-round" -c user.email="hetzner-round@users.noreply.github.com" \
    commit -q -m "parity: Hetzner 對帳 ${FROM}..${TO} rc=${RC}"
fi
git push -q -f origin "$BR"
git checkout -q main
echo "== done rc=$RC  分支 $BR  報告 $REPORT"
exit "$RC"
