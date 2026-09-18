#!/usr/bin/env bash
# 裁定 #51 甲（減資／分割／面額變更納入還原係數）重播完成後的 Hetzner「一句話貼」（`docs/P3-DATASET.md` §7.3 G）：
#   tmux new -d -s adj 'bash scripts/hetzner_adj.sh 2026-09-14'          # 第二個參數 FROM_SCORES 預設 2026-09-01
# 前提：三表已回補、`scan_features --rebuild`＋`replay_scores --rebuild --window 320` 已跑完，重播 log
# `cache/logs/replay-adj.log`（可用 HETZNER_ADJ_REPLAY_LOG 改）末行是 `== replay exit 0`。
# 做的事：0 同步 main 並印 HEAD（HEAD 前進即改用新版腳本重新執行）＋三道守門（重播 log 末行 exit 0／`cache/scores.db` 的
#   `replay_meta.params_sha`＝現行碼指紋且 pool_semantics=pit-1、adjust_sources＝`adjust.ADJUST_SOURCES`／TO ≤ scores.db 末日）
#   → 1 `check_scores.py`（唯讀健檢報表；只有開不了 db／表空才非 0）
#   → 2 `export_seed`（pool／factors／fundamentals／state／原料包；window 取 repo data/state/cross.json，須＝scores.db 記的 window）
#   → 3 `export_scores --from FROM_SCORES --to TO --force`（主線既有分數檔就是要被重播結果覆蓋）
#   → 4 `export_dataset --segment all --force`＋`check_dataset`（rc 非 0 即停）＋manifest 摘要＋3095／6415／6763／2364 事件窗 `fwd_ret`
#   → 5 種子＋分數檔＋data/backtest＋runs/adj commit 到分支 hetzner/adj-<TO> 並 push（--force-with-lease）；回 main。
# TO＝分支名＋匯出分數檔的迄日（取 scores.db 末日，守門會印出來；晚於它拒跑）。中途任一步失敗即停（set -e），log 在
# cache/logs/adj-round-*.log；重貼同一行可重跑（各步冪等；但 export_scores 覆蓋過的追蹤檔若尚未 commit，第 0 步會擋，
# 先 `git checkout -- data/scores data/pool.json data/factors.json data/fundamentals.json data/state/cross.json`
#（export_seed 會改後四檔、export_scores 改 data/scores）或直接跑到第 5 步 commit 掉）。
set -euo pipefail
# 自我複製後執行（同 hetzner_pit.sh，2026-09-17 第二輪實跑踩到）：bash 邊讀邊執行、第 0 步 `git pull` 換掉本檔後正在跑的仍是舊版。
# ①先把自己複製到暫存檔再執行；②第 0 步 pull 後若 HEAD 前進，改用 repo 內的新版重新執行（HETZNER_ADJ_PULLED=1 讓新版不再重複這一步）。
REPO_DIR=${HETZNER_ADJ_REPO:-$(cd "$(dirname "$0")/.." && pwd)}
if [ -z "${HETZNER_ADJ_SELF:-}" ]; then
  _self=$(mktemp "${TMPDIR:-/tmp}/hetzner_adj.XXXXXX")
  cp "$0" "$_self"
  HETZNER_ADJ_SELF="$_self" HETZNER_ADJ_REPO="$REPO_DIR" exec bash "$_self" "$@"
fi
trap 'rm -f "${HETZNER_ADJ_SELF:-}"' EXIT
cd "$REPO_DIR"
TO=${1:?用法: hetzner_adj.sh TO(YYYY-MM-DD，＝scores.db 末日；命名分支 hetzner/adj-<TO> 與分數檔迄日) [FROM_SCORES(YYYY-MM-DD，預設 2026-09-01)]}
FROM_SCORES=${2:-2026-09-01}
for d in "$TO" "$FROM_SCORES"; do
  [[ "$d" =~ ^[0-9]{4}-[0-9]{2}-[0-9]{2}$ ]] && [ "$(date -d "$d" +%F 2>/dev/null)" = "$d" ] || { echo "!! 日期須為合法的 YYYY-MM-DD：$d"; exit 2; }
done
[[ "$FROM_SCORES" > "$TO" ]] && { echo "!! FROM_SCORES 晚於 TO"; exit 2; }
mkdir -p cache/logs
if [ -z "${HETZNER_ADJ_LOG:-}" ]; then
  LOG="cache/logs/adj-round-$(date -u +%Y%m%dT%H%M%SZ).log"
  exec > >(tee -a "$LOG") 2>&1
  export HETZNER_ADJ_LOG="$LOG"
else
  LOG="$HETZNER_ADJ_LOG"       # 重新執行的新版：stdout 已經接在同一個 tee 上（exec 保留 fd），不再另開 log
fi
echo "== hetzner_adj TO=$TO FROM_SCORES=$FROM_SCORES  $(date -u +%FT%TZ)  log=$LOG  script=$0"

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
if [ "$(git rev-parse HEAD)" != "$HEAD_BEFORE" ] && [ -z "${HETZNER_ADJ_PULLED:-}" ]; then
  echo "== main 已由 ${HEAD_BEFORE:0:7} 前進到 $(git rev-parse --short HEAD)，改用新版 scripts/hetzner_adj.sh 重新執行"
  rm -f "${HETZNER_ADJ_SELF:-}"
  HETZNER_ADJ_PULLED=1 HETZNER_ADJ_SELF= exec bash "$REPO_DIR/scripts/hetzner_adj.sh" "$TO" "$FROM_SCORES"
fi
python3 -c "import sys; sys.path.insert(0,'src'); from iching.universe import POOL_SEMANTICS; print('pool_semantics', POOL_SEMANTICS)" | grep -q "pit-1" \
  || { echo "!! 這份 main 的 universe.POOL_SEMANTICS 不是 pit-1，不是 PIT 池的版本，停止"; exit 2; }
ADJ_SOURCES=$(python3 -c "import sys; sys.path.insert(0,'src'); from iching.adjust import ADJUST_SOURCES; print(ADJUST_SOURCES)")
echo "== adjust_sources=$ADJ_SOURCES（src/iching/adjust.py 現行值；scores.db 的 params_json.adjust_sources 必須相同）"

# 守門 a：重播 log 末行（去掉空白行）必須含「== replay exit 0」——那是重播那句 tmux 一句話貼結尾印的；沒有＝重播沒跑完或失敗。
REPLAY_LOG=${HETZNER_ADJ_REPLAY_LOG:-cache/logs/replay-adj.log}
[ -f "$REPLAY_LOG" ] || { echo "!! 找不到重播 log $REPLAY_LOG（scan_features --rebuild＋replay_scores --rebuild 跑完了嗎？路徑可用 HETZNER_ADJ_REPLAY_LOG 改）"; exit 2; }
REPLAY_LAST=$(grep -v '^[[:space:]]*$' "$REPLAY_LOG" | tail -n 1 || true)
case "$REPLAY_LAST" in
  *"== replay exit 0"*) echo "== 重播 log OK：$REPLAY_LAST（$REPLAY_LOG）" ;;
  *) echo "!! 重播 log $REPLAY_LOG 末行不是「== replay exit 0」，拒跑；末 3 行："; tail -n 3 "$REPLAY_LOG"; exit 2 ;;
esac

# 守門 b：scores.db 必須是現行碼算的（replay_meta.params_sha＝export_dataset.expected_params_sha 用現行 build_params 重算的指紋、
# pool_semantics=pit-1、自洽），adjust_sources＝現行值，且 TO ≤ db 末日（TO 正常就取末日；晚於它＝分數檔迄日超出 db）。
[ -f cache/scores.db ] || { echo "!! cache/scores.db 不存在"; exit 2; }
GUARD_OUT="cache/logs/adj-guard-${TO}.txt"
python3 - "$TO" "$ADJ_SOURCES" <<'EOF' | tee "$GUARD_OUT"
import sys
from pathlib import Path
sys.path.insert(0, "src"); sys.path.insert(0, "scripts")
from export_dataset import ExportDatasetError, check_params  # noqa: E402
from export_scores import ExportScoresError, resolve_data_version  # noqa: E402
from iching.scores_io import ScoreStore, ScoreStoreError  # noqa: E402
to, adj = sys.argv[1], sys.argv[2]
store = None
try:
    store = ScoreStore(Path("cache/scores.db"), readonly=True)
    dv = resolve_data_version(store, Path("cache"), None)
    sha, params = check_params(store, dv)
    first, last, n = store.conn.execute("SELECT MIN(date), MAX(date), COUNT(*) FROM replay_day WHERE data_version=?", (dv,)).fetchone()
except (ExportDatasetError, ExportScoresError, ScoreStoreError, OSError, ValueError, KeyError) as e:
    print(f"!! scores.db 守門失敗：{type(e).__name__}: {e}")
    sys.exit(2)
finally:
    if store is not None:
        store.close()
print(f"params_sha={sha}")
print(f"data_version={dv}")
print(f"db_window={params.get('window')}")
print(f"db_adjust_sources={params.get('adjust_sources')}")
print(f"db_days={n} first={first} last={last}")
if params.get("adjust_sources") != adj:
    print(f"!! scores.db 的 adjust_sources={params.get('adjust_sources')!r} ≠ 現行 {adj!r}（這份 db 不是四源還原的重播）")
    sys.exit(2)
if not last or to > str(last):
    print(f"!! TO={to} 晚於 scores.db 末日 {last}（TO 應取末日）")
    sys.exit(2)
if to != str(last):
    print(f"（注意：TO={to} 早於 scores.db 末日 {last}，分數檔只匯到 {to}）")
EOF
PARAMS_SHA=$(sed -n 's/^params_sha=//p' "$GUARD_OUT")
DB_WINDOW=$(sed -n 's/^db_window=//p' "$GUARD_OUT")
[ -n "$PARAMS_SHA" ] || { echo "!! 守門輸出沒有 params_sha 行（$GUARD_OUT）"; exit 2; }

WINDOW=$(python3 -c "import json;print(json.load(open('data/state/cross.json'))['meta']['window'])")
echo "== window=$WINDOW（取自 repo data/state/cross.json，與每日班一致；scores.db 記的 window=$DB_WINDOW）"
[ "$WINDOW" = "$DB_WINDOW" ] || { echo "!! cross.json 的 window=$WINDOW ≠ scores.db 的 window=$DB_WINDOW，重播用錯 window 或 cross.json 不是主線版，停止"; exit 2; }

echo "== 1 check_scores（唯讀健檢報表；只有開不了 db／replay_meta 或 replay_day 空才非 0，數字合不合理要人看）"
python3 scripts/check_scores.py cache/scores.db

echo "== 2 export_seed 匯新種子（--window $WINDOW；pool／factors（含 sources=$ADJ_SOURCES）／fundamentals／state／原料包）"
python3 scripts/export_seed.py --cache-dir cache --out . --window "$WINDOW"
restore_calendars

echo "== 3 export_scores 匯逐日分數檔 data/scores/${FROM_SCORES}..${TO}（--force：主線既有分數檔就是要被四源重播結果覆蓋）"
python3 scripts/export_scores.py --cache-dir cache --out . --from "$FROM_SCORES" --to "$TO" --force

# 產物目錄必須在第 0 步 checkout 之後才建（從 hetzner/adj-* 分支切回 main 時 git 會連同該分支追蹤的檔把空目錄移掉，
# 同 hetzner_pit.sh 第 5 步 runs/pit 的教訓）。
mkdir -p data/backtest runs/adj
echo "== 4a export_dataset --segment all --force（params_sha 守門在該檔內，與守門 b 同一支 check_params）"
python3 scripts/export_dataset.py --cache-dir cache --out . --segment all --force

CHECK_JSON="runs/adj/check_dataset_${TO}.json"
CHECK_TXT="runs/adj/check_dataset_${TO}.txt"
echo "== 4b check_dataset --sample 300 --seed 7 → $CHECK_JSON（＋$CHECK_TXT）"
set +e
python3 scripts/check_dataset.py --cache-dir cache --data-dir data/backtest --sample 300 --seed 7 --out "$CHECK_JSON" 2>&1 | tee "$CHECK_TXT"
RC=${PIPESTATUS[0]}
set -e
grep -q '^結果：rc=' "$CHECK_TXT" || { echo "!! check_dataset 未正常結束（報告無「結果：rc=」行），視為中止"; RC=2; }
[ "$RC" = "0" ] || { echo "!! check_dataset rc=$RC，不匯出、不 commit（報告 $CHECK_TXT／$CHECK_JSON）"; exit "$RC"; }

EVENT_TXT="runs/adj/event_report_${TO}.txt"
echo "== 4c manifest 摘要＋3095／6415／6763／2364 事件窗 fwd_ret → $EVENT_TXT（肉眼看有沒有回到 raw 連續價量級；不下判定）"
ls -l data/backtest/*.csv.gz
du -ch data/backtest/*.csv.gz | tail -1
# 只是給人看的摘要：失敗大聲印但不擋 commit（產物正確性已由 4b check_dataset 守；擋了反而要人手動清 data/scores 再重跑整條）。
python3 scripts/adj_event_report.py --cache-dir cache --data-dir data/backtest --stocks 3095 6415 6763 2364 2>&1 | tee "$EVENT_TXT" \
  || echo "!! adj_event_report 失敗 rc=${PIPESTATUS[0]}（只影響摘要，不影響產物；照常 commit）"
SIZE_TOTAL=$(python3 -c "import json;m=json.load(open('data/backtest/manifest.json'));print(sum(int(f['bytes']) for f in m['files'].values()))")

echo "== 5 種子＋分數檔＋data/backtest＋runs/adj commit＋push 到 hetzner/adj-$TO"
BR="hetzner/adj-${TO}"
git checkout -q -B "$BR"
git add data/pool.json data/factors.json data/fundamentals.json data/state/cross.json data/scores runs/collect data/backtest runs/adj
if git diff --cached --quiet; then echo "（無變更，不新增 commit）"; else
  git -c user.name="hetzner-adj" -c user.email="hetzner-adj@users.noreply.github.com" \
    commit -q -m "adj: 四源還原係數（adjust_sources=${ADJ_SOURCES}）重播後的新種子＋分數檔 ${FROM_SCORES}..${TO}＋回測資料集，params_sha=${PARAMS_SHA}，六檔合計 ${SIZE_TOTAL} bytes，check_dataset rc=${RC}"
fi
# --force-with-lease 綁「我方看到的遠端該分支 SHA」；必須 `--verify -q`（同 hetzner_pit.sh：不帶 --verify 時解析不到的名字會把
# 原字串照印到 stdout，EXPECT 變兩行，push 以 cannot parse expected object name 失敗）。分支不存在＝期望 0000000，等同直接 push。
git fetch -q origin "$BR" 2>/dev/null || true
EXPECT=$(git rev-parse --verify -q "origin/$BR" || echo 0000000000000000000000000000000000000000)
git push -q --force-with-lease="$BR:$EXPECT" origin "$BR"
git checkout -q main
echo "== done rc=$RC  分支 $BR  params_sha=$PARAMS_SHA  check_dataset $CHECK_TXT  事件窗 $EVENT_TXT  log $LOG"
exit "$RC"
