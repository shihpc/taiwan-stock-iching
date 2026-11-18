#!/usr/bin/env bash
# P4-B2 一次性安裝（docs/P4-PREVIEW.md §32.1）：使用者在 Hetzner 貼一行即可——
#   cd /root/projects/taiwan-stock-iching && git fetch -q origin main && git show origin/main:scripts/hetzner_backup_install.sh | bash
# 做的事：⓪把 main 上的 hetzner_backup.sh 複製到 <dest>/bin/（cron 指向這份）①先跑一次（首次會建 Git 鏡像，數百 MB）②印出 log 末段 ③在 root 的 crontab 加入每日一班
# （換算成伺服器時區的台北 18:30；已存在同一支腳本的行就不重複加）④印出目前 crontab 與結果。不切換、不改本機 repo 工作樹。
set -uo pipefail
# 全部包在函式裡、最後一行才以 </dev/null 執行：以 `… | bash` 從 stdin 讀本檔時，bash 會先讀完整個函式定義，
# 子程序（git、python）也就不會從 stdin 吃掉尚未讀到的腳本內容。
main() {
  REPO_DIR=${HETZNER_BACKUP_REPO:-/root/projects/taiwan-stock-iching}
  DEST=${BACKUP_DEST:-/root/backup/iching}
  SCRIPT="$DEST/bin/hetzner_backup.sh"      # cron 指向這份副本：不受本機 repo 目前切在哪個分支影響
  git -C "$REPO_DIR" fetch -q origin main || { echo "!! git fetch 失敗"; return 1; }
  mkdir -p "$DEST/bin"
  git -C "$REPO_DIR" show origin/main:scripts/hetzner_backup.sh > "$SCRIPT.new" && mv "$SCRIPT.new" "$SCRIPT" && chmod +x "$SCRIPT" \
    || { echo "!! main 上沒有 scripts/hetzner_backup.sh（PR 還沒合併？）"; return 1; }
  echo "== 1/3 先跑一次備份（首次較久，請稍候）"
  HETZNER_BACKUP_REPO="$REPO_DIR" BACKUP_DEST="$DEST" bash "$SCRIPT"
  rc=$?
  echo "== 2/3 log 末段"
  tail -8 "$DEST/logs/backup-$(TZ=Asia/Taipei date +%F).log"
  echo "== 3/3 排程"
  # cron 用伺服器本地時區：把台北 18:30 換算成本地時、分
  off=$(date +%z)                         # 例 +0000、+0800
  oh=$((10#${off:1:2})); om=$((10#${off:3:2})); d=$((oh*60+om)); [ "${off:0:1}" = "-" ] && d=$((-d))
  loc=$(( (18*60+30) - 8*60 + d ))
  loc=$(( (loc % 1440 + 1440) % 1440 ))
  LINE="$((loc % 60)) $((loc / 60)) * * * BACKUP_DEST=$DEST HETZNER_BACKUP_REPO=$REPO_DIR $SCRIPT   # iching 每日備份（台北 18:30）"
  if crontab -l 2>/dev/null | grep -Fq "$SCRIPT"; then
    echo "crontab 已有備份排程，不重複加入"
  else
    ( crontab -l 2>/dev/null; echo "$LINE" ) | crontab - && echo "已加入 crontab（伺服器時區 $off）"
  fi
  crontab -l 2>/dev/null | grep -F "$SCRIPT"
  echo "== 完成：第一次備份 rc=$rc（0＝正常）。把這整段輸出貼回給 Claude。"
  return $rc
}
main "$@" </dev/null
