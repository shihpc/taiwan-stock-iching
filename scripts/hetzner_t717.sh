#!/usr/bin/env bash
# §16.5 `:717` 門檻行為重驗的 Hetzner「一句話貼」（`docs/P3-CALIBRATION.md` §19／§20）：
#   tmux new -d -s t717 'bash scripts/hetzner_t717.sh'
# **前提：A（生產用）那次重播已經跑完**——本腳本要拿 `cache/scores.db` 當「後側」。
# 做的事：0 同步 main 並印 HEAD＋開跑前守門 → 1 前側重播（`--uncalibrated`，約 12.6 h）
#   → 2 `revalidate_thresholds.py` 比對八項 → 3 報告 commit 到 `hetzner/t717-<TO>` 並 push。
#
# ⚠ **前側那份 db 不是生產資料**：`cache/scores_t717_before.db`，指紋 d056ddc37920／4eb1be892c9c，
#   只供本分析使用。**不得匯出成 `data/scores`／`data/backtest`／種子**——`export_scores` 與
#   `export_dataset` 的血統守門會擋，但別去試。分支 `hetzner/t717-<TO>` 只放報告，不放 db。
set -euo pipefail
REPO_DIR=${HETZNER_T717_REPO:-$(cd "$(dirname "$0")/.." && pwd)}
if [ -z "${HETZNER_T717_SELF:-}" ]; then
  _self=$(mktemp "${TMPDIR:-/tmp}/hetzner_t717.XXXXXX")
  cp "$0" "$_self"
  HETZNER_T717_SELF="$_self" HETZNER_T717_REPO="$REPO_DIR" exec bash "$_self" "$@"
fi
trap 'rm -f "${HETZNER_T717_SELF:-}"' EXIT
cd "$REPO_DIR"
mkdir -p cache/logs runs/t717
BEFORE_DB=${HETZNER_T717_BEFORE_DB:-cache/scores_t717_before.db}
AFTER_DB=${HETZNER_T717_AFTER_DB:-cache/scores.db}
MARK="cache/logs/t717.started"
REEXEC="cache/logs/.t717-reexec"
rm -f "$REEXEC"
LOG=${HETZNER_T717_LOG:-cache/logs/t717.log}
if [ -z "${HETZNER_T717_ROTATED:-}" ]; then
  if [ -f "$LOG" ]; then mv "$LOG" "$LOG.prev-$(date -u +%Y%m%dT%H%M%SZ)"; fi
  export HETZNER_T717_ROTATED=1
fi

body() {
  # **`body` 在 pipeline 左側＝跑在子 shell**，而下面為了拿 `PIPESTATUS` 關掉了 errexit。
  # 這裡把它在子 shell 內重新打開（不外洩），否則 `replay_scores.py` 失敗時 `body` 會若無其事
  # 往下走到步驟 3，用半套資料產一份報告、rc 還是 0，第 4 步就把它推出去了（2026-09-22 驗收抓到）。
  # 長跑的兩支另外各給一個**可分辨的 rc**，光看收尾那行就知道斷在哪一步。
  set -e
  echo "== hetzner_t717  $(date -u +%FT%TZ)  log=$LOG"

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
  if [ "$(git rev-parse HEAD)" != "$head_before" ] && [ -z "${HETZNER_T717_PULLED:-}" ]; then
    echo "== main 已由 ${head_before:0:7} 前進到 $(git rev-parse --short HEAD)，改用新版重新執行"
    : > "$REEXEC"
    return 0
  fi

  echo "== 1 開跑前守門"
  # 後側必須存在、而且必須是**現行碼**算的——否則比出來的差異不只是 d 縮放。
  [ -f "$AFTER_DB" ] || { echo "!! 後側 $AFTER_DB 不存在；請先跑 scripts/hetzner_replay.sh（A，約 12.6 h）"; return 2; }
  python3 - "$AFTER_DB" <<'PY' || return 2
import sys
sys.path.insert(0, "src"); sys.path.insert(0, "scripts")
from iching.scores_io import ScoreStore
import export_dataset as ED, export_scores as EX
with ScoreStore(sys.argv[1], readonly=True) as s:
    dv = EX.resolve_data_version(s, __import__("pathlib").Path("cache"), None)
    sha, _ = ED.check_params(s, dv)          # 不是現行碼算的就拋錯
    n = s.conn.execute("SELECT COUNT(*) FROM replay_day WHERE data_version=?", (dv,)).fetchone()[0]
    print(f"== 後側 OK：data_version={dv} params_sha={sha} 已落地 {n} 日")
PY
  [ -f cache/features.db ] || { echo "!! cache/features.db 不存在"; return 2; }
  local window sha
  window=$(python3 -c "import json;print(json.load(open('data/state/cross.json'))['meta']['window'])") \
    || { echo "!! data/state/cross.json 取不到 meta.window"; return 2; }
  sha=$(python3 -c "
import sys; sys.path.insert(0,'src')
from iching.score.params import build_params
ps = {m: build_params(m, calibrated=False) for m in ('twse','tpex')}
print(','.join(f'{m}={p.model_version()}' for m, p in ps.items()) + ' calibrated=' + str(any(p.calibrated for p in ps.values())))") \
    || { echo "!! 取不到前側的 model_version"; return 2; }
  echo "== 前側（--uncalibrated）model_version $sha"
  echo "== window=$window"

  if [ -f "$MARK" ] && [ "$(head -n 1 "$MARK")" = "$sha" ]; then
    echo "== 2 前側重播 --resume（$MARK 指紋相同＝同一輪續跑）"
    python3 scripts/replay_scores.py --resume --window "$window" --out "$BEFORE_DB" --uncalibrated --progress-every 5 \
      || { echo "!! 前側重播（--resume）失敗"; return 3; }
  else
    if [ -f "$MARK" ]; then echo "== （$MARK 指紋與本輪不同，不沿用）"; fi
    echo "== 2 前側重播 --rebuild --uncalibrated（約 12.6 h；中斷後重貼同一行走 --resume）"
    printf '%s\n%s\n' "$sha" "$(date -u +%FT%TZ)" > "$MARK"
    python3 scripts/replay_scores.py --rebuild --window "$window" --out "$BEFORE_DB" --uncalibrated --progress-every 5 \
      || { echo "!! 前側重播（--rebuild）失敗"; return 3; }
  fi

  echo "== 3 八項差異分析"
  local to
  to=$(python3 -c "
import sys; sys.path.insert(0,'src')
from iching.scores_io import ScoreStore
with ScoreStore('$AFTER_DB', readonly=True) as s:
    dv = [r[0] for r in s.conn.execute('SELECT DISTINCT data_version FROM replay_meta')][0]
    print(s.dates(dv)[-1])") || { echo "!! 取不到後側的最末資料日"; return 2; }
  [ -n "$to" ] || { echo "!! 後側最末資料日是空的"; return 2; }
  python3 scripts/revalidate_thresholds.py --before "$BEFORE_DB" --after "$AFTER_DB" \
      --out "runs/t717/report_${to}.json" || { echo "!! 八項差異分析失敗"; return 4; }
  for f in "runs/t717/report_${to}.json" "runs/t717/report_${to}.txt"; do
    [ -s "$f" ] || { echo "!! 報告 $f 沒產出或是空的"; return 4; }
  done
  echo "== TO=$to"
  echo "$to" > cache/logs/t717.to
}

set +e
body 2>&1 | tee -a "$LOG"
rc=${PIPESTATUS[0]}
set -e
if [ -f "$REEXEC" ]; then
  rm -f "$REEXEC" "${HETZNER_T717_SELF:-}"
  HETZNER_T717_PULLED=1 HETZNER_T717_SELF= exec bash "$REPO_DIR/scripts/hetzner_t717.sh" "$@"
fi
if [ "$rc" != "0" ]; then
  echo "== 收尾：rc=$rc（未推送報告）"
  exit "$rc"
fi
rm -f "$MARK"

TO=$(cat cache/logs/t717.to)
BR="hetzner/t717-${TO}"
echo "== 4 報告 commit＋push 到 $BR（**只放報告，不放 db**）"
git checkout -q -B "$BR"
git add "runs/t717/report_${TO}.json" "runs/t717/report_${TO}.txt"
if ! git diff --cached --quiet; then
  git -c user.name=hetzner-t717 -c user.email=hetzner-t717@users.noreply.github.com \
      commit -q -m "t717: 門檻行為重驗八項報告（前側 --uncalibrated，迄 ${TO}）"
fi
git fetch -q origin "$BR" || true
EXPECT=$(git rev-parse --verify -q "origin/$BR" || echo 0000000000000000000000000000000000000000)
git push --force-with-lease="$BR:$EXPECT" -q origin "$BR"
git checkout -q main
echo "== done rc=0  分支 $BR  報告 runs/t717/report_${TO}.txt  log $LOG"
