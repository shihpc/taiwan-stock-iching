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
mkdir -p cache/logs runs/parity
LOG="cache/logs/parity-round-$(date -u +%Y%m%dT%H%M%SZ).log"
exec > >(tee -a "$LOG") 2>&1
echo "== hetzner_round $FROM..$TO  $(date -u +%FT%TZ)  log=$LOG"

echo "== 0 同步 main 並核對 HEAD"
if [ -n "$(git status --porcelain --untracked-files=no)" ]; then
  echo "!! 工作樹有未提交的追蹤檔改動，先處理再跑："; git status --short --untracked-files=no; exit 2
fi
git fetch -q origin main
git checkout -q main
git pull -q --ff-only origin main
git log -1 --format='HEAD %h %ci %s'

echo "== 1 回補 $FROM..$TO（沿用 cache 內 data_version）"
if [ "${HETZNER_ROUND_SKIP_BACKFILL:-0}" = "1" ]; then echo "（HETZNER_ROUND_SKIP_BACKFILL=1：離線煙霧測試，跳過回補）"; else
python3 scripts/backfill_hetzner.py run --from "$FROM" --to "$TO" --progress-every 200
fi

echo "== 2 scan_features --resume（掃描從頭重播、只補寫新日）"
python3 scripts/scan_features.py --resume --progress-every 400

WINDOW=$(python3 -c "import json;print(json.load(open('data/state/cross.json'))['meta']['window'])")
echo "== 3 replay_scores --resume --window $WINDOW（window 取自 data/state/cross.json，與每日班一致；不符會被快照參數守門擋下）"
python3 scripts/replay_scores.py --resume --window "$WINDOW" --progress-every 5

REPORT="runs/parity/${FROM}_${TO}.txt"
echo "== 4 parity_check → $REPORT"
set +e
python3 scripts/parity_check.py --cache-dir cache --repo . --from "$FROM" --to "$TO" --show 50 | tee "$REPORT"
RC=${PIPESTATUS[0]}
set -e
{ echo; echo "parity rc=$RC  HEAD=$(git rev-parse --short HEAD)  at=$(date -u +%FT%TZ)"; } | tee -a "$REPORT"

echo "== 5 報告 commit＋push 到 hetzner/parity-$TO"
BR="hetzner/parity-${TO}"
git checkout -q -B "$BR"
git add "$REPORT"
git -c user.name="hetzner-round" -c user.email="hetzner-round@users.noreply.github.com" \
  commit -q -m "parity: Hetzner 對帳 ${FROM}..${TO} rc=${RC}" || echo "（報告無變更，不新增 commit）"
git push -q -f origin "$BR"
git checkout -q main
echo "== done rc=$RC  分支 $BR  報告 $REPORT"
exit "$RC"
