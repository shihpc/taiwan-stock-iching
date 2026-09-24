#!/usr/bin/env bash
# §16.5 `:712`／`:714` 步驟 4／`:716` 的 Hetzner「一句話貼」（`docs/P3-CALIBRATION.md` §25）：
#   tmux new -d -s stats 'bash scripts/hetzner_stats.sh'
# **不重播、不寫 db**：只讀生產 `cache/scores.db`，樣本寫死為訓練＋驗證段（裁定 #64 ①）。
# 做的事：0 同步 main 並印 HEAD → 1 開跑前守門（登錄檔與現行碼一致、db 存在）
#   → 2 `score_stats.py` 產報告 → 2b 若 `summary.explain_716` 非空，`score_diag716.py` 產族組成診斷
#   （裁定 #65 ②，`docs/P3-CALIBRATION.md` §27；parity 不符／失敗 rc=4、不推送）
#   → 3 報告（＋診斷）commit 到 `hetzner/stats-<TO>` 並 push（只放報告，不放 db）。
set -euo pipefail
REPO_DIR=${HETZNER_STATS_REPO:-$(cd "$(dirname "$0")/.." && pwd)}
if [ -z "${HETZNER_STATS_SELF:-}" ]; then
  _self=$(mktemp "${TMPDIR:-/tmp}/hetzner_stats.XXXXXX")
  cp "$0" "$_self"
  HETZNER_STATS_SELF="$_self" HETZNER_STATS_REPO="$REPO_DIR" exec bash "$_self" "$@"
fi
trap 'rm -f "${HETZNER_STATS_SELF:-}"' EXIT
cd "$REPO_DIR"
mkdir -p cache/logs runs/stats
DB=${HETZNER_STATS_DB:-cache/scores.db}
REEXEC="cache/logs/.stats-reexec"
rm -f "$REEXEC" cache/logs/stats.diag
LOG=${HETZNER_STATS_LOG:-cache/logs/stats.log}
if [ -z "${HETZNER_STATS_ROTATED:-}" ]; then
  if [ -f "$LOG" ]; then mv "$LOG" "$LOG.prev-$(date -u +%Y%m%dT%H%M%SZ)"; fi
  export HETZNER_STATS_ROTATED=1
fi

body() {
  # `body` 跑在 pipeline 左側的子 shell；在這裡重開 errexit（同 hetzner_t717.sh 的理由）。
  set -e
  echo "== hetzner_stats  $(date -u +%FT%TZ)  log=$LOG"

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
  if [ "$(git rev-parse HEAD)" != "$head_before" ] && [ -z "${HETZNER_STATS_PULLED:-}" ]; then
    echo "== main 已由 ${head_before:0:7} 前進到 $(git rev-parse --short HEAD)，改用新版重新執行"
    : > "$REEXEC"
    return 0
  fi

  echo "== 1 開跑前守門"
  [ -f "$DB" ] || { echo "!! $DB 不存在；請先跑 scripts/hetzner_replay.sh"; return 2; }
  # 登錄檔必須就是現行碼實算的結果——否則 :714 步驟 4 比的是舊區間
  python3 scripts/score_ranges.py --check || { echo "!! docs/score-ranges.md 與現行碼不一致"; return 2; }

  echo "== 2 三項統計（:712／:714 步驟 4／:716；db 是否為現行碼所算、model_version 是否與登錄檔相同，由腳本內守門）"
  local to
  to=$(python3 -c "
import sys; sys.path.insert(0,'src')
from iching.scores_io import ScoreStore
with ScoreStore('$DB', readonly=True) as s:
    dv = [r[0] for r in s.conn.execute('SELECT DISTINCT data_version FROM replay_meta')][0]
    print(s.dates(dv)[-1])") || { echo "!! 取不到 $DB 的最末資料日"; return 2; }
  [ -n "$to" ] || { echo "!! $DB 最末資料日是空的"; return 2; }
  python3 scripts/score_stats.py --db "$DB" --out "runs/stats/report_${to}.json" || { echo "!! 三項統計失敗"; return 3; }
  for f in "runs/stats/report_${to}.json" "runs/stats/report_${to}.txt"; do
    [ -s "$f" ] || { echo "!! 報告 $f 沒產出或是空的"; return 3; }
  done
  echo "== TO=$to"

  local n716
  n716=$(python3 -c "import json,sys; print(len(json.load(open(sys.argv[1], encoding='utf-8'))['summary']['explain_716']))" \
         "runs/stats/report_${to}.json") || { echo "!! 讀不到報告的 summary.explain_716"; return 3; }
  case "$n716" in ''|*[!0-9]*) echo "!! explain_716 組數不是整數：$n716"; return 3;; esac
  if [ "$n716" -gt 0 ]; then
    echo "== 2b :716 須解釋 ${n716} 組 → 族組成診斷（真實計分碼重算抽樣列；parity 不符即中止、不推送）"
    python3 scripts/score_diag716.py --report "runs/stats/report_${to}.json" --db "$DB" \
      --out "runs/stats/diag716_${to}.json" || { echo "!! 族組成診斷失敗"; return 4; }
    for f in "runs/stats/diag716_${to}.json" "runs/stats/diag716_${to}.txt"; do
      [ -s "$f" ] || { echo "!! 診斷 $f 沒產出或是空的"; return 4; }
    done
    echo 1 > cache/logs/stats.diag
  else
    echo "== 2b :716 須解釋 0 組，略過族組成診斷"
    echo 0 > cache/logs/stats.diag
  fi
  echo "$to" > cache/logs/stats.to
}

set +e
body 2>&1 | tee -a "$LOG"
rc=${PIPESTATUS[0]}
set -e
if [ -f "$REEXEC" ]; then
  rm -f "$REEXEC" "${HETZNER_STATS_SELF:-}"
  HETZNER_STATS_PULLED=1 HETZNER_STATS_SELF= exec bash "$REPO_DIR/scripts/hetzner_stats.sh" "$@"
fi
if [ "$rc" != "0" ]; then
  echo "== 收尾：rc=$rc（未推送報告）"
  exit "$rc"
fi

TO=$(cat cache/logs/stats.to)
BR="hetzner/stats-${TO}"
echo "== 3 報告（＋:716 診斷）commit＋push 到 $BR（**只放報告，不放 db**）"
git checkout -q -B "$BR"
git add "runs/stats/report_${TO}.json" "runs/stats/report_${TO}.txt"
if [ "$(cat cache/logs/stats.diag)" = "1" ]; then
  git add "runs/stats/diag716_${TO}.json" "runs/stats/diag716_${TO}.txt"
fi
if ! git diff --cached --quiet; then
  git -c user.name=hetzner-stats -c user.email=hetzner-stats@users.noreply.github.com \
      commit -q -m "stats: §16.5 :712／:714 步驟4／:716 報告＋族組成診斷（訓練＋驗證段，db 迄 ${TO}）"
fi
git fetch -q origin "$BR" || true
EXPECT=$(git rev-parse --verify -q "origin/$BR" || echo 0000000000000000000000000000000000000000)
git push --force-with-lease="$BR:$EXPECT" -q origin "$BR"
git checkout -q main
echo "== done rc=0  分支 $BR  報告 runs/stats/report_${TO}.txt  log $LOG"
