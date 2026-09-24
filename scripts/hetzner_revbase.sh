#!/usr/bin/env bash
# 營收子指標基期影響面量測的 Hetzner「一句話貼」（`docs/P3-CALIBRATION.md` §29）：
#   tmux new -d -s revbase 'bash scripts/hetzner_revbase.sh'
# **唯讀量測、不重播、不寫 db**：只讀生產 `cache/scores.db` 與原料 DB，樣本寫死為訓練＋驗證段（裁定 #64 ①）。
# 做的事：0 同步 main 並印 HEAD → 1 開跑前守門（db 存在、登錄檔與現行碼一致）
#   → 2 `revenue_base_impact.py` 產報告（db 血統／model_version／parity 由腳本內守門，不過 rc=3、不推送）
#   → 3 報告 commit 到 `hetzner/revbase-<TO>` 並 push（只放報告，不放 db）。
set -euo pipefail
REPO_DIR=${HETZNER_REVBASE_REPO:-$(cd "$(dirname "$0")/.." && pwd)}
if [ -z "${HETZNER_REVBASE_SELF:-}" ]; then
  _self=$(mktemp "${TMPDIR:-/tmp}/hetzner_revbase.XXXXXX")
  cp "$0" "$_self"
  HETZNER_REVBASE_SELF="$_self" HETZNER_REVBASE_REPO="$REPO_DIR" exec bash "$_self" "$@"
fi
trap 'rm -f "${HETZNER_REVBASE_SELF:-}"' EXIT
cd "$REPO_DIR"
mkdir -p cache/logs runs/revbase
DB=${HETZNER_REVBASE_DB:-cache/scores.db}
REEXEC="cache/logs/.revbase-reexec"
rm -f "$REEXEC" cache/logs/revbase.to
LOG=${HETZNER_REVBASE_LOG:-cache/logs/revbase.log}
if [ -z "${HETZNER_REVBASE_ROTATED:-}" ]; then
  if [ -f "$LOG" ]; then mv "$LOG" "$LOG.prev-$(date -u +%Y%m%dT%H%M%SZ)"; fi
  export HETZNER_REVBASE_ROTATED=1
fi

body() {
  # `body` 跑在 pipeline 左側的子 shell；在這裡重開 errexit（同 hetzner_t717.sh／hetzner_stats.sh 的理由）。
  set -e
  echo "== hetzner_revbase  $(date -u +%FT%TZ)  log=$LOG"

  echo "== 0 同步 main 並核對 HEAD"
  git reset -q
  for f in data/calendar_tpe.json data/calendar_us.json; do
    git ls-files --error-unmatch "$f" >/dev/null 2>&1 && git checkout -q -- "$f" || true
  done
  local dirty head_before
  dirty=$(git status --porcelain --untracked-files=no)
  if [ -n "$dirty" ]; then echo "!! 工作樹不乾淨，先處理再跑："; echo "$dirty"; return 2; fi
  head_before=$(git rev-parse HEAD)
  git fetch -q origin main
  git checkout -q main
  git pull -q --ff-only origin main
  git log -1 --format='HEAD %h %ci %s'
  if [ "$(git rev-parse HEAD)" != "$head_before" ] && [ -z "${HETZNER_REVBASE_PULLED:-}" ]; then
    echo "== main 已由 ${head_before:0:7} 前進到 $(git rev-parse --short HEAD)，改用新版重新執行"
    : > "$REEXEC"
    return 0
  fi

  echo "== 1 開跑前守門"
  [ -f "$DB" ] || { echo "!! $DB 不存在；請先跑 scripts/hetzner_replay.sh"; return 2; }
  python3 scripts/score_ranges.py --check || { echo "!! docs/score-ranges.md 與現行碼不一致"; return 2; }

  echo "== 2 營收子指標基期影響面量測（唯讀；A 全母體＋B 抽樣 parity＋C 反事實；parity 不符即中止、不推送）"
  local to
  to=$(python3 -c "
import sys; sys.path.insert(0,'src')
from iching.scores_io import ScoreStore
with ScoreStore('$DB', readonly=True) as s:
    dv = [r[0] for r in s.conn.execute('SELECT DISTINCT data_version FROM replay_meta')][0]
    print(s.dates(dv)[-1])") || { echo "!! 取不到 $DB 的最末資料日"; return 2; }
  [ -n "$to" ] || { echo "!! $DB 最末資料日是空的"; return 2; }
  echo "== TO=$to"
  python3 scripts/revenue_base_impact.py --db "$DB" --out "runs/revbase/report_${to}.json" \
    || { echo "!! 營收基期影響面量測失敗"; return 3; }
  for f in "runs/revbase/report_${to}.json" "runs/revbase/report_${to}.txt"; do
    [ -s "$f" ] || { echo "!! 報告 $f 沒產出或是空的"; return 3; }
  done
  echo "$to" > cache/logs/revbase.to
}

set +e
body 2>&1 | tee -a "$LOG"
rc=${PIPESTATUS[0]}
set -e
if [ -f "$REEXEC" ]; then
  rm -f "$REEXEC" "${HETZNER_REVBASE_SELF:-}"
  HETZNER_REVBASE_PULLED=1 HETZNER_REVBASE_SELF= exec bash "$REPO_DIR/scripts/hetzner_revbase.sh" "$@"
fi
if [ "$rc" != "0" ]; then
  echo "== 收尾：rc=$rc（未推送報告）"
  exit "$rc"
fi

TO=$(cat cache/logs/revbase.to)
BR="hetzner/revbase-${TO}"
echo "== 3 報告 commit＋push 到 $BR（**只放報告，不放 db**）"
git checkout -q -B "$BR"
git add "runs/revbase/report_${TO}.json" "runs/revbase/report_${TO}.txt"
if ! git diff --cached --quiet; then
  git -c user.name=hetzner-revbase -c user.email=hetzner-revbase@users.noreply.github.com \
      commit -q -m "revbase: 營收子指標基期影響面量測報告（唯讀；訓練＋驗證段，db 迄 ${TO}）"
fi
git fetch -q origin "$BR" || true
EXPECT=$(git rev-parse --verify -q "origin/$BR" || echo 0000000000000000000000000000000000000000)
git push --force-with-lease="$BR:$EXPECT" -q origin "$BR"
git checkout -q main
echo "== done rc=0  分支 $BR  報告 runs/revbase/report_${TO}.txt  log $LOG"
