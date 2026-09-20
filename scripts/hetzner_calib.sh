#!/usr/bin/env bash
# P3 第 3 項第 1 步（`docs/P3-CALIBRATION.md` §2 第 1～2 步）：訓練段子指標原始值 x 出口＋ d 校準報告的 Hetzner「一句話貼」：
#   tmux new -d -s calib 'bash scripts/hetzner_calib.sh 2023-06-30'       # 第二個參數 DUMP_FROM 預設 2021-01-01（訓練段起日）
# 前提：`cache/scores.db` 是**現行碼**算的全量重播（守門 b 會驗 `replay_meta.params_sha`＝現行碼指紋；不是就先重播，本腳本不重播進 db）。
# 做的事：0 同步 main 並印 HEAD（HEAD 前進即改用新版腳本重新執行）＋守門（工作樹乾淨／scores.db 的 params_sha＝現行碼指紋且
#   pool_semantics=pit-1／DUMP_TO ≤ scores.db 末日／window 取 scores.db 記的）
#   → 1 `replay_scores.py --dump-only --dump-x cache/xdump --dump-from $DUMP_FROM --dump-to $DUMP_TO`
#     **不加 `--rebuild`、不寫 scores.db 也不寫快照**：`replay_scores.py` 沒有「只重算某區間但不寫 db」的模式（`--resume` 只能接在
#     db 末日之後、`--from` 要前一日快照且照樣寫 db），本批加的 `--dump-only` 就是為此：從最早交易日起重算到 DUMP_TO
#     （狀態鏈不可跳日，裁定 #54 Q7；暖機日只算不寫）、只落地 x 分鍵 float32 檔（Hetzner 本地、不進 git，裁定 #54 Q1 (a)）。
#     舊的 cache/xdump 先搬到 cache/xdump.prev-<時戳>（XDump 拒絕非空目錄：append 會重複計數）。
#   → 2 `calibrate_d.py --dump-dir cache/xdump --out-dir runs/calib --tag $DUMP_TO`（p85／d_new／截斷比例／分類／閘門；只報不改 params.py）
#   → 3 runs/calib commit 到分支 hetzner/calib-<DUMP_TO> 並 push（--force-with-lease）；回 main。
# 預估：第 1 步≈全量重播的 (DUMP_TO 之前交易日數 ÷ 全段日數) 倍——2020-01-02～2023-06-30 約 850／1628 日 × 12.6h ≈ 6.6h
#（不寫 db 略快；文件 §2 第 2 步寫的 4.7h 只算了 603 個訓練日，暖機 2020 年那段照樣要計分）。中途任一步失敗即停（set -e），
# log 在 cache/logs/calib-*.log；重貼同一行可重跑（各步冪等）。
set -euo pipefail
# 自我複製後執行（同 hetzner_adj.sh／hetzner_pit.sh）：bash 邊讀邊執行、第 0 步 `git pull` 換掉本檔後正在跑的仍是舊版。
# ①先把自己複製到暫存檔再執行；②第 0 步 pull 後若 HEAD 前進，改用 repo 內的新版重新執行（HETZNER_CALIB_PULLED=1 讓新版不再重複這一步）。
REPO_DIR=${HETZNER_CALIB_REPO:-$(cd "$(dirname "$0")/.." && pwd)}
if [ -z "${HETZNER_CALIB_SELF:-}" ]; then
  _self=$(mktemp "${TMPDIR:-/tmp}/hetzner_calib.XXXXXX")
  cp "$0" "$_self"
  HETZNER_CALIB_SELF="$_self" HETZNER_CALIB_REPO="$REPO_DIR" exec bash "$_self" "$@"
fi
trap 'rm -f "${HETZNER_CALIB_SELF:-}"' EXIT
cd "$REPO_DIR"
DUMP_TO=${1:?用法: hetzner_calib.sh DUMP_TO(YYYY-MM-DD，訓練段迄日 2023-06-30；命名分支 hetzner/calib-<DUMP_TO> 與報告檔名) [DUMP_FROM(YYYY-MM-DD，預設 2021-01-01)]}
DUMP_FROM=${2:-2021-01-01}
for d in "$DUMP_TO" "$DUMP_FROM"; do
  [[ "$d" =~ ^[0-9]{4}-[0-9]{2}-[0-9]{2}$ ]] && [ "$(date -d "$d" +%F 2>/dev/null)" = "$d" ] || { echo "!! 日期須為合法的 YYYY-MM-DD：$d"; exit 2; }
done
[[ "$DUMP_FROM" > "$DUMP_TO" ]] && { echo "!! DUMP_FROM 晚於 DUMP_TO"; exit 2; }
mkdir -p cache/logs
if [ -z "${HETZNER_CALIB_LOG:-}" ]; then
  LOG="cache/logs/calib-$(date -u +%Y%m%dT%H%M%SZ).log"
  exec > >(tee -a "$LOG") 2>&1
  export HETZNER_CALIB_LOG="$LOG"
else
  LOG="$HETZNER_CALIB_LOG"     # 重新執行的新版：stdout 已經接在同一個 tee 上（exec 保留 fd），不再另開 log
fi
echo "== hetzner_calib DUMP_FROM=$DUMP_FROM DUMP_TO=$DUMP_TO  $(date -u +%FT%TZ)  log=$LOG  script=$0"

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
if [ "$(git rev-parse HEAD)" != "$HEAD_BEFORE" ] && [ -z "${HETZNER_CALIB_PULLED:-}" ]; then
  echo "== main 已由 ${HEAD_BEFORE:0:7} 前進到 $(git rev-parse --short HEAD)，改用新版 scripts/hetzner_calib.sh 重新執行"
  rm -f "${HETZNER_CALIB_SELF:-}"
  HETZNER_CALIB_PULLED=1 HETZNER_CALIB_SELF= exec bash "$REPO_DIR/scripts/hetzner_calib.sh" "$DUMP_TO" "$DUMP_FROM"
fi
python3 -c "import sys; sys.path.insert(0,'src'); from iching.universe import POOL_SEMANTICS; print('pool_semantics', POOL_SEMANTICS)" | grep -q "pit-1" \
  || { echo "!! 這份 main 的 universe.POOL_SEMANTICS 不是 pit-1，不是 PIT 池的版本，停止"; exit 2; }

# 守門：scores.db 必須是現行碼算的（replay_meta.params_sha＝export_dataset.expected_params_sha 用現行 build_params 重算的指紋、
# pool_semantics=pit-1、自洽——同 hetzner_adj.sh 守門 b 的 check_params），且 DUMP_TO ≤ db 末日（訓練段本來就在 db 裡；晚於末日＝
# 原料還沒回補到那天）。window 取 db 記的（--dump-only 重算要用同一個 window 才與 db 同口徑）。
[ -f cache/scores.db ] || { echo "!! cache/scores.db 不存在（先跑全量重播）"; exit 2; }
GUARD_OUT="cache/logs/calib-guard-${DUMP_TO}.txt"
python3 - "$DUMP_TO" "$DUMP_FROM" <<'PYEOF' | tee "$GUARD_OUT"
import sys
from pathlib import Path
sys.path.insert(0, "src"); sys.path.insert(0, "scripts")
from export_dataset import ExportDatasetError, check_params  # noqa: E402
from export_scores import ExportScoresError, resolve_data_version  # noqa: E402
from iching.scores_io import ScoreStore, ScoreStoreError  # noqa: E402
to, frm = sys.argv[1], sys.argv[2]
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
print(f"db_days={n} first={first} last={last}")
if not last or to > str(last):
    print(f"!! DUMP_TO={to} 晚於 scores.db 末日 {last}")
    sys.exit(2)
if not first or frm < str(first):
    print(f"（注意：DUMP_FROM={frm} 早於 scores.db 首日 {first}，區間前段沒有可比對的分數）")
PYEOF
PARAMS_SHA=$(sed -n 's/^params_sha=//p' "$GUARD_OUT")
WINDOW=$(sed -n 's/^db_window=//p' "$GUARD_OUT")
[ -n "$PARAMS_SHA" ] && [ -n "$WINDOW" ] || { echo "!! 守門輸出沒有 params_sha／db_window 行（$GUARD_OUT）"; exit 2; }
echo "== window=$WINDOW（取自 scores.db 的 replay_meta）params_sha=$PARAMS_SHA"

XDUMP="cache/xdump"
# HETZNER_CALIB_REUSE_DUMP=1：沿用既有 cache/xdump（manifest 的 params_sha／dump_from／dump_to 都要相符），跳過第 1 步的 6 小時重算。
# 用途＝第 2 步以後失敗時重跑（2026-09-20 首跑：calibrate_d 的 argparse help 含裸 % 在 Python 3.14 直接拋錯，dump 已完成但報告沒產）。
if [ "${HETZNER_CALIB_REUSE_DUMP:-0}" = "1" ] && [ -f "$XDUMP/manifest.json" ]; then
  python3 - "$XDUMP" "$PARAMS_SHA" "$DUMP_FROM" "$DUMP_TO" <<'PYEOF'
import json, sys
m = json.load(open(sys.argv[1] + "/manifest.json"))
bad = [k for k, want in (("params_sha", sys.argv[2]), ("dump_from", sys.argv[3]), ("dump_to", sys.argv[4])) if str(m.get(k)) != want]
if bad:
    print(f"!! HETZNER_CALIB_REUSE_DUMP=1 但既有 xdump manifest 的 {bad} 與本次不符（manifest={ {k: m.get(k) for k in ('params_sha','dump_from','dump_to')} }）")
    sys.exit(2)
print(f"== 沿用既有 {sys.argv[1]}（params_sha／dump_from／dump_to 相符，跳過第 1 步）")
PYEOF
else
  if [ -e "$XDUMP" ]; then
    PREV="${XDUMP}.prev-$(date -u +%Y%m%dT%H%M%SZ)"
    echo "== 舊的 $XDUMP 搬到 $PREV（XDump 拒絕非空目錄）"
    mv "$XDUMP" "$PREV"
  fi
  echo "== 1 replay_scores --dump-only（從最早交易日重算到 $DUMP_TO、不寫 scores.db；x 只寫 $DUMP_FROM～$DUMP_TO）"
  python3 scripts/replay_scores.py --cache-dir cache --window "$WINDOW" --dump-only --dump-x "$XDUMP" --dump-from "$DUMP_FROM" --dump-to "$DUMP_TO"
fi
XDUMP_SHA=$(python3 -c "import json;print(json.load(open('$XDUMP/manifest.json'))['params_sha'])")
[ "$XDUMP_SHA" = "$PARAMS_SHA" ] || { echo "!! xdump manifest 的 params_sha=$XDUMP_SHA ≠ scores.db 的 $PARAMS_SHA（重算與 db 不同口徑）"; exit 2; }
du -sh "$XDUMP"

# 產物目錄必須在第 0 步 checkout 之後才建（從 hetzner/calib-* 分支切回 main 時 git 會連同該分支追蹤的檔把空目錄移掉，
# 同 hetzner_pit.sh 第 5 步 runs/pit 的教訓）。
mkdir -p runs/calib
echo "== 2 calibrate_d → runs/calib/d_report_${DUMP_TO}.json／.txt（只報不改 params.py；閘門超標鍵印在報告末）"
python3 scripts/calibrate_d.py --dump-dir "$XDUMP" --out-dir runs/calib --tag "$DUMP_TO"
GATE_N=$(python3 -c "import json;r=json.load(open('runs/calib/d_report_${DUMP_TO}.json'));print(len(r['gate_failures']))")
DEG_N=$(python3 -c "import json;r=json.load(open('runs/calib/d_report_${DUMP_TO}.json'));print(len(r['degenerate']))")

echo "== 3 runs/calib commit＋push 到 hetzner/calib-$DUMP_TO"
BR="hetzner/calib-${DUMP_TO}"
git checkout -q -B "$BR"
git add runs/calib
if git diff --cached --quiet; then echo "（無變更，不新增 commit）"; else
  git -c user.name="hetzner-calib" -c user.email="hetzner-calib@users.noreply.github.com" \
    commit -q -m "calib: 訓練段 ${DUMP_FROM}..${DUMP_TO} x 出口＋d 校準報告，params_sha=${PARAMS_SHA}，閘門超標 ${GATE_N} 鍵、退化 ${DEG_N} 鍵（只報不改 params.py）"
fi
# --force-with-lease 綁「我方看到的遠端該分支 SHA」；必須 `--verify -q`（同 hetzner_pit.sh：不帶 --verify 時解析不到的名字會把
# 原字串照印到 stdout，EXPECT 變兩行，push 以 cannot parse expected object name 失敗）。分支不存在＝期望 0000000，等同直接 push。
git fetch -q origin "$BR" 2>/dev/null || true
EXPECT=$(git rev-parse --verify -q "origin/$BR" || echo 0000000000000000000000000000000000000000)
git push -q --force-with-lease="$BR:$EXPECT" origin "$BR"
git checkout -q main
echo "== done  分支 $BR  params_sha=$PARAMS_SHA  閘門超標 $GATE_N  退化 $DEG_N  報告 runs/calib/d_report_${DUMP_TO}.txt  xdump $XDUMP  log $LOG"
exit 0
