#!/usr/bin/env bash
# 模型換版比對的 Hetzner「一句話貼」（`docs/P3-CALIBRATION.md` §33；骨架仿 `hetzner_revneg.sh`）：
#   tmux new -d -s modeldiff 'bash scripts/hetzner_modeldiff.sh'
# **唯讀比對、不重播、不寫 db**：比 `cache/scores.db`（裁定 #68／#69 後重播）與 `cache/scores_pre68.db`（#68 前備份），
#   預設只看訓練＋驗證段（保留段未動用）。
# 做的事：0 同步 main 並印 HEAD（HEAD 前進即改用新版重新執行）→ 1 開跑前守門（兩個 db 存在；重播已完成＝
#   重播 log 末行是 `== replay exit 0` 且 `cache/logs/replay.started` 不存在）→ 2 `model_diff.py` 產報告
#   → 3 報告 commit 到 `hetzner/modeldiff-<UTC 日期>` 並 push（只放報告，不放 db）。
# 回傳碼：0 全符合（推報告）／1 不變式違反（**也推報告**——那正是要看的；log 末行標明）／
#   2 守門或前置條件不過（不推）／3 報告沒產出或推送失敗。
# 記憶體：與 `hetzner_t717.sh`（含一次前側重播）同跑會互搶記憶體（本機 3.2 GiB），建議錯開；偵測到 replay_scores.py
#   正在跑時只印警告、不擋（前側重播寫的是另一個 db）。
set -euo pipefail
REPO_DIR=${HETZNER_MODELDIFF_REPO:-$(cd "$(dirname "$0")/.." && pwd)}
# 自我複製後執行（同 hetzner_revneg.sh）：bash 邊讀邊執行，第 0 步 `git pull` 換掉本檔後正在跑的仍是舊版。
if [ -z "${HETZNER_MODELDIFF_SELF:-}" ]; then
  _self=$(mktemp "${TMPDIR:-/tmp}/hetzner_modeldiff.XXXXXX")
  cp "$0" "$_self"
  HETZNER_MODELDIFF_SELF="$_self" HETZNER_MODELDIFF_REPO="$REPO_DIR" exec bash "$_self" "$@"
fi
trap 'rm -f "${HETZNER_MODELDIFF_SELF:-}"' EXIT
cd "$REPO_DIR"
mkdir -p cache/logs
NEW_DB=${HETZNER_MODELDIFF_NEW_DB:-cache/scores.db}
OLD_DB=${HETZNER_MODELDIFF_OLD_DB:-cache/scores_pre68.db}
REPLAY_LOG=${HETZNER_MODELDIFF_REPLAY_LOG:-cache/logs/replay-adj.log}      # 同 hetzner_replay.sh 的 LOG、hetzner_adj.sh 守門 a
REPLAY_MARK=${HETZNER_MODELDIFF_REPLAY_MARK:-cache/logs/replay.started}    # 同 hetzner_replay.sh 的 MARK（重播成功後才刪）
REEXEC="cache/logs/.modeldiff-reexec"
DAYF="cache/logs/modeldiff.day"
RCF="cache/logs/modeldiff.rc"
rm -f "$REEXEC" "$DAYF" "$RCF"
LOG=${HETZNER_MODELDIFF_LOG:-cache/logs/modeldiff.log}
if [ -z "${HETZNER_MODELDIFF_ROTATED:-}" ]; then
  if [ -f "$LOG" ]; then mv "$LOG" "$LOG.prev-$(date -u +%Y%m%dT%H%M%SZ)"; fi   # 舊 log 移開，末行標記不得跨輪沿用
  export HETZNER_MODELDIFF_ROTATED=1
fi

body() {
  # `body` 跑在 pipeline 左側的子 shell；在這裡重開 errexit（同 hetzner_t717.sh／hetzner_revneg.sh 的理由）。
  set -e
  echo "== hetzner_modeldiff  $(date -u +%FT%TZ)  log=$LOG"

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
  if [ "$(git rev-parse HEAD)" != "$head_before" ] && [ -z "${HETZNER_MODELDIFF_PULLED:-}" ]; then
    echo "== main 已由 ${head_before:0:7} 前進到 $(git rev-parse --short HEAD)，改用新版重新執行"
    : > "$REEXEC"
    return 0
  fi

  echo "== 1 開跑前守門"
  [ -f "$OLD_DB" ] || { echo "!! 舊側 $OLD_DB 不存在（#68 前的備份）；停止"; return 2; }
  [ -f "$NEW_DB" ] || { echo "!! 新側 $NEW_DB 不存在；停止"; return 2; }
  [ -f "$REPLAY_LOG" ] || { echo "!! 找不到重播 log $REPLAY_LOG（hetzner_replay.sh 跑完了嗎？）；停止"; return 2; }
  local last
  last=$(grep -v '^[[:space:]]*$' "$REPLAY_LOG" | tail -n 1 || true)
  if [ "$last" != "== replay exit 0" ]; then
    echo "!! 重播 log $REPLAY_LOG 末行不是「== replay exit 0」（重播未完成或失敗），不跑；末 3 行："; tail -n 3 "$REPLAY_LOG"
    return 2
  fi
  if [ -e "$REPLAY_MARK" ]; then
    echo "!! $REPLAY_MARK 仍在（重播未完成：hetzner_replay.sh 成功才刪它），不跑"; return 2
  fi
  echo "== 重播已完成：$last（$REPLAY_LOG）；$REPLAY_MARK 不存在"
  if pgrep -f 'scripts/replay_scores\.py' >/dev/null 2>&1; then
    echo "== 注意：偵測到 replay_scores.py 正在跑（可能是 hetzner_t717.sh 的前側重播）——記憶體會互搶，建議錯開"
  fi

  local day out rc
  day=$(date -u +%F)
  out="runs/modeldiff/report_${day}.json"
  mkdir -p runs/modeldiff                                   # checkout 之後才建（切分支會把空目錄帶走，同 hetzner_adj.sh 第 4 步）
  echo "== 2 model_diff（唯讀；--new $NEW_DB --old $OLD_DB；預設不讀保留段）→ $out"
  set +e
  python3 scripts/model_diff.py --new "$NEW_DB" --old "$OLD_DB" --out "$out" --progress-every 50
  rc=$?
  set -e
  if [ "$rc" != "0" ] && [ "$rc" != "1" ]; then echo "!! model_diff rc=$rc（前置條件不過或例外），不推送"; return 2; fi
  for f in "$out" "${out%.json}.txt"; do
    [ -s "$f" ] || { echo "!! 報告 $f 沒產出或是空的"; return 3; }
  done
  grep -q "^結果：rc=${rc}（" "${out%.json}.txt" || { echo "!! 報告結果行與 rc=$rc 不符"; return 3; }
  echo "$day" > "$DAYF"
  echo "$rc" > "$RCF"
  return 0
}

publish() {
  set -e
  local day="$1" rc="$2" br="hetzner/modeldiff-$1" msg
  echo "== 3 報告 commit＋push 到 $br（**只放報告，不放 db**）"
  git checkout -q -B "$br"
  git add "runs/modeldiff/report_${day}.json" "runs/modeldiff/report_${day}.txt"
  if [ "$rc" = "0" ]; then msg="全符合"; else msg="不變式違反（見報告）"; fi
  if ! git diff --cached --quiet; then
    git -c user.name=hetzner-modeldiff -c user.email=hetzner-modeldiff@users.noreply.github.com \
        commit -q -m "modeldiff: 模型換版比對報告（唯讀；訓練＋驗證段；rc=${rc} ${msg}）"
  fi
  git fetch -q origin "$br" || true
  local expect
  expect=$(git rev-parse --verify -q "origin/$br" || echo 0000000000000000000000000000000000000000)
  git push --force-with-lease="$br:$expect" -q origin "$br"
  git checkout -q main
}

set +e
body 2>&1 | tee -a "$LOG"
rc=${PIPESTATUS[0]}
set -e
if [ -f "$REEXEC" ]; then
  rm -f "$REEXEC" "${HETZNER_MODELDIFF_SELF:-}"
  HETZNER_MODELDIFF_PULLED=1 HETZNER_MODELDIFF_SELF= exec bash "$REPO_DIR/scripts/hetzner_modeldiff.sh" "$@"
fi
if [ "$rc" != "0" ]; then
  echo "== modeldiff exit $rc（未推送報告）" | tee -a "$LOG"
  exit "$rc"
fi

DAY=$(cat "$DAYF")
DRC=$(cat "$RCF")
BR="hetzner/modeldiff-${DAY}"
set +e
publish "$DAY" "$DRC" 2>&1 | tee -a "$LOG"
prc=${PIPESTATUS[0]}
set -e
if [ "$prc" != "0" ]; then
  echo "== modeldiff exit 3（推送失敗；比對 rc=$DRC，報告留在 runs/modeldiff/report_${DAY}.*）" | tee -a "$LOG"
  exit 3
fi
if [ "$DRC" = "0" ]; then
  echo "== modeldiff exit 0（全符合；報告已推 $BR）" | tee -a "$LOG"
else
  echo "== modeldiff exit 1（不變式違反；報告已推 $BR——請看報告）" | tee -a "$LOG"
fi
exit "$DRC"
