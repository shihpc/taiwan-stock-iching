#!/usr/bin/env bash
# PIT 池切換的 Hetzner 全量回合（「一句話貼」版，`docs/P3-PIT-POOL.md` §2 #9、裁定 #49 Q13／Q14）：
#   tmux new -s pit 'bash scripts/hetzner_pit.sh 2020-01-01 2026-09-16'
# 做的事：0 同步 main 並印 HEAD（核對用）→ 1 備份舊 scores.db（cache/scores_prepit.db，比對用；已存在就沿用）
#   → 2 scan_features --rebuild（約 5.4 分）→ 3 replay_scores --rebuild（約 12.6 h；中斷後重貼同一行改走 --resume）
#   → 4 export_seed 匯新種子（pool／factors／fundamentals／state／原料包，window 取自 repo data/state/cross.json）
#   → 5 轉換表報告 runs/pit/<FROM>_<TO>.transitions.txt（＋.json）＋ 與舊 scores.db 逐日比對摘要 runs/pit/<FROM>_<TO>.txt（＋.json）
#   → 6 種子＋報告 commit 到分支 hetzner/pit-<TO> 並 push。
# FROM／TO＝比對報告的日期區間（TO 兼作分支名）；scan／replay 一律全量（PIT 池改變每一日的母體，沒有部分重算這回事）。
# 中途任一步失敗即停（set -e），log 在 cache/logs/pit-round-*.log；重貼同一行可續跑（scan 冪等、replay 走 --resume、其餘可重跑）。
set -euo pipefail
cd "$(dirname "$0")/.."
FROM=${1:?用法: hetzner_pit.sh FROM(YYYY-MM-DD) TO(YYYY-MM-DD)}
TO=${2:?用法: hetzner_pit.sh FROM(YYYY-MM-DD) TO(YYYY-MM-DD)}
for d in "$FROM" "$TO"; do
  [[ "$d" =~ ^[0-9]{4}-[0-9]{2}-[0-9]{2}$ ]] && [ "$(date -d "$d" +%F 2>/dev/null)" = "$d" ] || { echo "!! 日期須為合法的 YYYY-MM-DD：$d"; exit 2; }
done
[[ "$FROM" > "$TO" ]] && { echo "!! FROM 晚於 TO"; exit 2; }
mkdir -p cache/logs runs/pit
LOG="cache/logs/pit-round-$(date -u +%Y%m%dT%H%M%SZ).log"
exec > >(tee -a "$LOG") 2>&1
echo "== hetzner_pit $FROM..$TO  $(date -u +%FT%TZ)  log=$LOG"

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
git pull -q --ff-only origin main
git log -1 --format='HEAD %h %ci %s'
python3 -c "import sys; sys.path.insert(0,'src'); from iching.universe import POOL_SEMANTICS; print('pool_semantics', POOL_SEMANTICS)" | grep -q "pit-1" \
  || { echo "!! 這份 main 的 universe.POOL_SEMANTICS 不是 pit-1，不是 PIT 池的版本，停止"; exit 2; }

WINDOW=$(python3 -c "import json;print(json.load(open('data/state/cross.json'))['meta']['window'])")
echo "== window=$WINDOW（取自 repo data/state/cross.json，與每日班一致）"

MARK="cache/logs/pit-${TO}.replay-started"
echo "== 1 備份舊 scores.db → cache/scores_prepit.db（比對用）"
if [ -f cache/scores_prepit.db ]; then echo "（已存在，沿用；那是切換前的舊 DB）"; else
  [ -f cache/scores.db ] || { echo "!! cache/scores.db 不存在，沒有舊 DB 可比對（全新機器請先跑舊版）"; exit 2; }
  [ -f "$MARK" ] && { echo "!! 已開始 PIT 重播但沒有備份檔，舊 DB 已被 --rebuild 清掉；比對不可能，停止"; exit 2; }
  cp cache/scores.db cache/scores_prepit.db
  [ -f cache/scores.db.state.json ] && cp cache/scores.db.state.json cache/scores_prepit.db.state.json || true
fi

if [ "${HETZNER_PIT_SKIP_REPLAY:-0}" = "1" ]; then echo "（HETZNER_PIT_SKIP_REPLAY=1：離線煙霧測試，跳過 scan／replay）"; else
if [ -f "$MARK" ]; then
  echo "== 2 scan_features：已重建過（$MARK 存在），--resume 只補寫"
  python3 scripts/scan_features.py --resume --progress-every 400
  echo "== 3 replay_scores --resume --window $WINDOW（續跑）"
  python3 scripts/replay_scores.py --resume --window "$WINDOW" --progress-every 5
else
  echo "== 2 scan_features --rebuild（PIT 池：features.db 的 params 指紋含 pool_semantics，舊列一律砍掉重寫）"
  python3 scripts/scan_features.py --rebuild --progress-every 400
  echo "== 3 replay_scores --rebuild --window $WINDOW（約 12.6 h，單核；中斷後重貼同一行走 --resume）"
  date -u +%FT%TZ > "$MARK"
  python3 scripts/replay_scores.py --rebuild --window "$WINDOW" --progress-every 5
fi
fi

echo "== 4 export_seed 匯新種子（--window $WINDOW）"
python3 scripts/export_seed.py --cache-dir cache --out . --window "$WINDOW"
restore_calendars

TRANS="runs/pit/${FROM}_${TO}.transitions"
REPORT="runs/pit/${FROM}_${TO}.txt"
echo "== 5a 轉換表報告 → $TRANS.txt／.json"
python3 scripts/pit_report.py transitions --cache-dir cache --out "$TRANS.json" | tee "$TRANS.txt"
echo "== 5b 與舊 scores.db 逐日比對 → $REPORT（明細 ${REPORT%.txt}.json）"
set +e
python3 scripts/pit_report.py compare --cache-dir cache --old cache/scores_prepit.db --new cache/scores.db \
  --from "$FROM" --to "$TO" --show 50 --out "${REPORT%.txt}.json" 2>&1 | tee "$REPORT"
RC=${PIPESTATUS[0]}
set -e
grep -q '^結果：rc=' "$REPORT" || { echo "!! pit_report compare 未正常結束（報告無「結果：rc=」行），視為中止"; RC=2; }
{ echo; echo "pit rc=$RC  HEAD=$(git rev-parse --short HEAD)  window=$WINDOW  at=$(date -u +%FT%TZ)"; } | tee -a "$REPORT"

echo "== 6 種子＋報告 commit＋push 到 hetzner/pit-$TO"
BR="hetzner/pit-${TO}"
git checkout -q -B "$BR"
git add data/pool.json data/factors.json data/fundamentals.json data/state/cross.json runs/collect "$TRANS.txt" "$TRANS.json" "$REPORT"
[ -f "${REPORT%.txt}.json" ] && git add "${REPORT%.txt}.json"
if git diff --cached --quiet; then echo "（無變更，不新增 commit）"; else
  git -c user.name="hetzner-pit" -c user.email="hetzner-pit@users.noreply.github.com" \
    commit -q -m "pit: Hetzner 全量重算（PIT 池 pool_semantics=pit-1）＋新種子 ${FROM}..${TO} compare rc=${RC}"
fi
# --force-with-lease 綁「我方看到的遠端該分支 SHA」：別人在這期間推了東西就拒（分支不存在＝期望 0000000，等同直接 push）。
# `hetzner_round.sh:77` 仍是 `push -f`，本批不動（另案），見 docs/P3-PIT-POOL.md §6.6。
git fetch -q origin "$BR" 2>/dev/null || true
EXPECT=$(git rev-parse "origin/$BR" 2>/dev/null || echo 0000000000000000000000000000000000000000)
git push -q --force-with-lease="$BR:$EXPECT" origin "$BR"
git checkout -q main
echo "== done rc=$RC  分支 $BR  報告 $REPORT"
exit "$RC"
