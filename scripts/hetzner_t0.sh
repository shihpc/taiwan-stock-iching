#!/usr/bin/env bash
# P4-L4 方向門檻 T0 校準的 Hetzner「一句話貼」（docs/P4-PREVIEW.md §27）：
#   tmux new -s t0 'bash scripts/hetzner_t0.sh 2026-11-15'
# 做的事：0 同步 main 並印 HEAD（HEAD 前進即改用新版腳本重新執行）→ 1 `export_t0_input.py`（訓練段 scores.db → runs/t0/t0_input.json.gz；
#   params_sha 守門在該檔內）→ 2 `calib_t0.py`（→ data/pick/t0.json＋runs/t0/report.md；q=0 仍不可行時 rc 3、只留報告）
#   → 3 runs/t0/ 與 data/pick/t0.json commit 到分支 hetzner/t0-<TO> 並 push（`--force-with-lease`）。
# TO 只拿來命名分支（慣例＝當天日期），不影響匯出範圍（訓練段寫死在 export_dataset.SEGMENTS）。
# 中途任一步失敗即停（set -e），log 在 cache/logs/t0-*.log；重貼同一行可重跑（兩支腳本冪等、同內容同位元組）。
# 產物由 session fetch 該分支後**獨立重算核對**，驗收後才進 main（data/pick/t0.json 進 main 才會被每日班啟用）。
set -euo pipefail
# 自我複製後執行（同 hetzner_pit.sh，2026-09-17 第二輪實跑踩到）：bash 邊讀邊執行、第 0 步 `git pull` 換掉本檔後正在跑的仍是舊版。
# ①先把自己複製到暫存檔再執行；②第 0 步 pull 後若 HEAD 前進，改用 repo 內的新版重新執行（HETZNER_T0_PULLED=1 讓新版不再重複這一步）。
REPO_DIR=${HETZNER_T0_REPO:-$(cd "$(dirname "$0")/.." && pwd)}
if [ -z "${HETZNER_T0_SELF:-}" ]; then
  _self=$(mktemp "${TMPDIR:-/tmp}/hetzner_t0.XXXXXX")
  cp "$0" "$_self"
  HETZNER_T0_SELF="$_self" HETZNER_T0_REPO="$REPO_DIR" exec bash "$_self" "$@"
fi
trap 'rm -f "${HETZNER_T0_SELF:-}"' EXIT
cd "$REPO_DIR"
TO=${1:?用法: hetzner_t0.sh TO(YYYY-MM-DD，只用來命名分支 hetzner/t0-<TO>)}
[[ "$TO" =~ ^[0-9]{4}-[0-9]{2}-[0-9]{2}$ ]] && [ "$(date -d "$TO" +%F 2>/dev/null)" = "$TO" ] || { echo "!! 日期須為合法的 YYYY-MM-DD：$TO"; exit 2; }
mkdir -p cache/logs
if [ -z "${HETZNER_T0_LOG:-}" ]; then
  LOG="cache/logs/t0-$(date -u +%Y%m%dT%H%M%SZ).log"
  exec > >(tee -a "$LOG") 2>&1
  export HETZNER_T0_LOG="$LOG"
else
  LOG="$HETZNER_T0_LOG"        # 重新執行的新版：stdout 已經接在同一個 tee 上（exec 保留 fd），不再另開 log
fi
echo "== hetzner_t0 $TO  $(date -u +%FT%TZ)  log=$LOG  script=$0"

echo "== 0 同步 main 並核對 HEAD"
git reset -q
restore_calendars() {
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
HEAD_BEFORE=$(git rev-parse HEAD)
git pull -q --ff-only origin main
git log -1 --format='HEAD %h %ci %s'
if [ "$(git rev-parse HEAD)" != "$HEAD_BEFORE" ] && [ -z "${HETZNER_T0_PULLED:-}" ]; then
  echo "== main 已由 ${HEAD_BEFORE:0:7} 前進到 $(git rev-parse --short HEAD)，改用新版 scripts/hetzner_t0.sh 重新執行"
  rm -f "${HETZNER_T0_SELF:-}"
  HETZNER_T0_PULLED=1 HETZNER_T0_SELF= exec bash "$REPO_DIR/scripts/hetzner_t0.sh" "$TO"
fi
python3 -c "import sys; sys.path.insert(0,'src'); from iching.universe import POOL_SEMANTICS; print('pool_semantics', POOL_SEMANTICS)" | grep -q "pit-1" \
  || { echo "!! 這份 main 的 universe.POOL_SEMANTICS 不是 pit-1，不是 PIT 池的版本，停止"; exit 2; }

# 產物目錄必須在第 0 步 checkout 之後才建（從 hetzner/t0-* 分支切回 main 時 git 會連同該分支追蹤的檔把空目錄移掉，
# 同 hetzner_pit.sh 第 5 步 runs/pit 的教訓）。
mkdir -p runs/t0 data/pick
echo "== 1 export_t0_input（訓練段；scores.db params_sha 守門在該檔內，非 PIT 池／非現行碼指紋 rc 2）"
python3 scripts/export_t0_input.py --cache-dir cache --out .
ls -l runs/t0/t0_input.json.gz

echo "== 2 calib_t0（rc 3＝q=0 仍不可行、N0 過大：只留報告、不寫 t0.json）"
set +e
python3 scripts/calib_t0.py --input runs/t0/t0_input.json.gz --root .
RC=$?
set -e
[ "$RC" = 0 ] || [ "$RC" = 3 ] || { echo "!! calib_t0 rc=$RC，停止"; exit "$RC"; }
head -n 12 runs/t0/report.md

echo "== 3 runs/t0 與 data/pick/t0.json commit＋push 到 hetzner/t0-$TO"
BR="hetzner/t0-${TO}"
git checkout -q -B "$BR"
git add runs/t0
if [ "$RC" = 0 ]; then git add data/pick/t0.json
else git rm -q --cached --ignore-unmatch data/pick/t0.json   # rc 3：不讓 main 上既有（較舊）的 t0.json 混進本分支被誤當本次結果
fi
if git diff --cached --quiet; then echo "（無變更，不新增 commit）"; else
  git -c user.name="hetzner-t0" -c user.email="hetzner-t0@users.noreply.github.com" \
    commit -q -m "t0: 方向門檻 T0 校準（訓練段匯出＋t0.json＋報告，calib rc=$RC），calib_t0.py @ $(git rev-parse --short main)"
fi
# --force-with-lease 綁「我方看到的遠端該分支 SHA」；必須 `--verify -q`（同 hetzner_pit.sh：不帶 --verify 時解析不到的名字會把
# 原字串照印到 stdout，EXPECT 變兩行，push 以 cannot parse expected object name 失敗）。分支不存在＝期望 0000000，等同直接 push。
git fetch -q origin "$BR" 2>/dev/null || true
EXPECT=$(git rev-parse --verify -q "origin/$BR" || echo 0000000000000000000000000000000000000000)
git push -q --force-with-lease="$BR:$EXPECT" origin "$BR"
git checkout -q main
echo "== done  分支 $BR  calib rc=$RC  log $LOG"
