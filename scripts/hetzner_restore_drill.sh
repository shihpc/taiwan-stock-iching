#!/usr/bin/env bash
# P4-B3 還原演練一行入口（docs/P4-PREVIEW.md §33）。使用者在 Hetzner 貼：
#   cd /root/projects/taiwan-stock-iching && git fetch -q origin main && git show origin/main:scripts/hetzner_restore_drill.sh | bash
# 取 GitHub main 上最新的 restore_drill.py 與其依賴到暫存目錄執行；不刪改 main、snap-* Release 與 /root/backup/iching 既有內容，
# 只推演練分支 hetzner/drill-<UTC 時刻>（及 …-mirror）。repo 目前所在分支與工作樹不動。
set -uo pipefail
# 整段包在函式、最後以 </dev/null 執行：`… | bash` 時子程序不會吃掉尚未讀到的腳本內容
main() {
  REPO_DIR=${HETZNER_BACKUP_REPO:-/root/projects/taiwan-stock-iching}
  DEST=${BACKUP_DEST:-/root/backup/iching}
  TMP=$(mktemp -d "${TMPDIR:-/tmp}/iching-drill-src.XXXXXX")
  trap 'rm -rf "$TMP"' RETURN
  git -C "$REPO_DIR" fetch -q origin main || { echo "!! git fetch 失敗"; return 1; }
  for f in restore_drill.py backup_pull.py verify_snapshot.py build_snapshot.py; do
    git -C "$REPO_DIR" show "origin/main:scripts/$f" > "$TMP/$f" || { echo "!! main 上沒有 scripts/$f（PR 還沒合併？）"; return 1; }
  done
  echo "== 腳本來源：GitHub main $(git -C "$REPO_DIR" rev-parse --short origin/main)"
  python3 "$TMP/restore_drill.py" --dest "$DEST" --repo-dir "$REPO_DIR" "$@"
  local rc=$?
  echo "== 完成 rc=$rc（0＝全部通過）。把這整段輸出貼回給 Claude。"
  return $rc
}
main "$@" </dev/null
