#!/usr/bin/env bash
# 回測資料出口的 Hetzner「一句話貼」（`docs/P3-DATASET.md` §2 B、裁定 #50）：
#   tmux new -s ds 'bash scripts/hetzner_dataset.sh 2024-12-31'
# 做的事：0 同步 main 並印 HEAD（核對用；HEAD 前進即改用新版腳本重新執行）→ 1 `export_dataset.py --segment all --force`
#   （三段切點與 h 寫死在該檔 SEGMENTS／H_BY_HORIZON，這裡不傳日期）→ 2 印六檔大小與 manifest 摘要
#   → 3 `data/backtest/` commit 到分支 hetzner/dataset-<TO> 並 push（`--force-with-lease`）。
# TO 只拿來命名分支（慣例＝驗證段末日 2024-12-31，或當天日期），不影響匯出範圍。
# 中途任一步失敗即停（set -e），log 在 cache/logs/dataset-*.log；重貼同一行可重跑（export 冪等、同內容同位元組）。
set -euo pipefail
# 自我複製後執行（同 hetzner_pit.sh，2026-09-17 第二輪實跑踩到）：bash 邊讀邊執行、第 0 步 `git pull` 換掉本檔後正在跑的仍是舊版。
# ①先把自己複製到暫存檔再執行；②第 0 步 pull 後若 HEAD 前進，改用 repo 內的新版重新執行（HETZNER_DS_PULLED=1 讓新版不再重複這一步）。
REPO_DIR=${HETZNER_DS_REPO:-$(cd "$(dirname "$0")/.." && pwd)}
if [ -z "${HETZNER_DS_SELF:-}" ]; then
  _self=$(mktemp "${TMPDIR:-/tmp}/hetzner_dataset.XXXXXX")
  cp "$0" "$_self"
  HETZNER_DS_SELF="$_self" HETZNER_DS_REPO="$REPO_DIR" exec bash "$_self" "$@"
fi
trap 'rm -f "${HETZNER_DS_SELF:-}"' EXIT
cd "$REPO_DIR"
TO=${1:?用法: hetzner_dataset.sh TO(YYYY-MM-DD，只用來命名分支 hetzner/dataset-<TO>)}
[[ "$TO" =~ ^[0-9]{4}-[0-9]{2}-[0-9]{2}$ ]] && [ "$(date -d "$TO" +%F 2>/dev/null)" = "$TO" ] || { echo "!! 日期須為合法的 YYYY-MM-DD：$TO"; exit 2; }
mkdir -p cache/logs
if [ -z "${HETZNER_DS_LOG:-}" ]; then
  LOG="cache/logs/dataset-$(date -u +%Y%m%dT%H%M%SZ).log"
  exec > >(tee -a "$LOG") 2>&1
  export HETZNER_DS_LOG="$LOG"
else
  LOG="$HETZNER_DS_LOG"        # 重新執行的新版：stdout 已經接在同一個 tee 上（exec 保留 fd），不再另開 log
fi
echo "== hetzner_dataset $TO  $(date -u +%FT%TZ)  log=$LOG  script=$0"

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
if [ "$(git rev-parse HEAD)" != "$HEAD_BEFORE" ] && [ -z "${HETZNER_DS_PULLED:-}" ]; then
  echo "== main 已由 ${HEAD_BEFORE:0:7} 前進到 $(git rev-parse --short HEAD)，改用新版 scripts/hetzner_dataset.sh 重新執行"
  rm -f "${HETZNER_DS_SELF:-}"
  HETZNER_DS_PULLED=1 HETZNER_DS_SELF= exec bash "$REPO_DIR/scripts/hetzner_dataset.sh" "$TO"
fi
python3 -c "import sys; sys.path.insert(0,'src'); from iching.universe import POOL_SEMANTICS; print('pool_semantics', POOL_SEMANTICS)" | grep -q "pit-1" \
  || { echo "!! 這份 main 的 universe.POOL_SEMANTICS 不是 pit-1，不是 PIT 池的版本，停止"; exit 2; }

# 產物目錄必須在第 0 步 checkout 之後才建（從 hetzner/dataset-* 分支切回 main 時 git 會連同該分支追蹤的檔把空目錄移掉，
# 同 hetzner_pit.sh 第 5 步 runs/pit 的教訓）。
mkdir -p data/backtest
echo "== 1 export_dataset --segment all --force（scores.db params_sha 守門在該檔內，非 PIT 池／非現行碼指紋 rc 2）"
python3 scripts/export_dataset.py --cache-dir cache --out . --segment all --force

echo "== 2 產物大小與 manifest 摘要"
ls -l data/backtest/*.csv.gz
du -ch data/backtest/*.csv.gz | tail -1
python3 - <<'EOF'
import json
m = json.load(open("data/backtest/manifest.json", encoding="utf-8"))
print(f"data_version={m['data_version']} params_sha={m['params_sha']} model_version={m['model_version']} text_version={m['text_version']} head={m['head']}")
print(f"segments={ {k: (v['from'], v['to'], v['n_score_days'], len(v['calendar_days_without_scores'])) for k, v in m['segments'].items()} }  data_end={m['calendar']['data_end']}")
oq = m["open_quality"]["train_plus_valid"]
print(f"open 品質 {oq['range']}: 全列 {oq['open_null_or_nonpos']} / 成交列 {oq['open_null_or_nonpos_traded']}（共 {oq['n_rows']} 列）")
print(f"大盤列排除 {m['n_market_rows_excluded']}  價格列日曆外 {m['n_price_rows_off_calendar']}")
for name, f in sorted(m["files"].items()):
    print(f"  {name}: rows={f['n_rows']} bytes={f['bytes']} sha256={f['sha256'][:12]} exit={f['exit_reason_counts']} "
          f"fwd_missing={f['n_fwd_ret_missing']} last_signal_with_fwd={f['last_signal_date_with_fwd_ret']}")
EOF

echo "== 3 data/backtest commit＋push 到 hetzner/dataset-$TO"
BR="hetzner/dataset-${TO}"
git checkout -q -B "$BR"
git add data/backtest
if git diff --cached --quiet; then echo "（無變更，不新增 commit）"; else
  git -c user.name="hetzner-dataset" -c user.email="hetzner-dataset@users.noreply.github.com" \
    commit -q -m "dataset: 回測資料出口（訓練＋驗證段 × 3 horizon 六檔＋manifest），export_dataset.py @ $(git rev-parse --short main)"
fi
# --force-with-lease 綁「我方看到的遠端該分支 SHA」；必須 `--verify -q`（同 hetzner_pit.sh：不帶 --verify 時解析不到的名字會把
# 原字串照印到 stdout，EXPECT 變兩行，push 以 cannot parse expected object name 失敗）。分支不存在＝期望 0000000，等同直接 push。
git fetch -q origin "$BR" 2>/dev/null || true
EXPECT=$(git rev-parse --verify -q "origin/$BR" || echo 0000000000000000000000000000000000000000)
git push -q --force-with-lease="$BR:$EXPECT" origin "$BR"
git checkout -q main
echo "== done  分支 $BR  log $LOG"
