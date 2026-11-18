#!/usr/bin/env bash
# P4-B2 Hetzner 每日備份（docs/P4-PREVIEW.md §32）。由使用者在 Hetzner 親手裝 cron（裁定 #76 Q5），例：
#   30 10 * * * /root/projects/taiwan-stock-iching/scripts/hetzner_backup.sh     # 伺服器時區為 UTC 時＝台北 18:30
# 做的事：取 GitHub main 上最新版的 backup_pull.py／verify_snapshot.py／build_snapshot.py 到暫存目錄執行（不切換、不改本機 repo 工作樹）；
# GitHub 連不到時改取本機 Git 鏡像 <dest>/git.git 的 main（GitHub 端出事時備份腳本仍可用）。
# 結果寫 <dest>/logs/backup-<台北日期>.log（同日多次執行附加），exit code＝backup_pull.py 的（0 正常／1 有失敗）。
set -uo pipefail
REPO_DIR=${HETZNER_BACKUP_REPO:-$(cd "$(dirname "$0")/.." && pwd)}
DEST=${BACKUP_DEST:-/root/backup/iching}
mkdir -p "$DEST/logs"
LOG="$DEST/logs/backup-$(TZ=Asia/Taipei date +%F).log"
exec >>"$LOG" 2>&1
echo "== hetzner_backup $(date -u +%FT%TZ) repo=$REPO_DIR dest=$DEST"
TMP=$(mktemp -d "${TMPDIR:-/tmp}/iching-backup.XXXXXX")
trap 'rm -rf "$TMP"' EXIT
FILES="backup_pull.py verify_snapshot.py build_snapshot.py"
take() {   # $1＝git 目錄，$2＝ref
  for f in $FILES; do git -C "$1" show "$2:scripts/$f" > "$TMP/$f" 2>/dev/null || return 1; done
}
if git -C "$REPO_DIR" fetch -q origin main 2>/dev/null && take "$REPO_DIR" origin/main; then
  echo "腳本來源：GitHub main $(git -C "$REPO_DIR" rev-parse --short origin/main)"
elif [ -d "$DEST/git.git" ] && take "$DEST/git.git" main; then
  echo "!! GitHub 取不到，腳本改取本機鏡像 main $(git -C "$DEST/git.git" rev-parse --short main)"
else
  echo "!! 取不到備份腳本（GitHub 與本機鏡像皆失敗）"; exit 1
fi
python3 "$TMP/backup_pull.py" --dest "$DEST" "$@"
rc=$?
echo "== rc=$rc"
exit $rc
