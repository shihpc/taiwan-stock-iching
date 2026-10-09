#!/usr/bin/env bash
# 校準後全量重播的 Hetzner「一句話貼」（docs/P3-CALIBRATION.md §15，2026-09-20 新增）：
#   tmux new -d -s replay 'bash scripts/hetzner_replay.sh'
# 為什麼要有這支：`hetzner_adj.sh` 守門 a（:70-77）要求重播 log 末行含 `== replay exit 0`，但 repo 裡
#   **從來沒有任何腳本會印那一行**——上一輪是人手打的 tmux 一句話、原文沒進版控。手打那句有兩個會付出
#   12 小時代價的坑：①`--window` 靠人輸入，打錯要到 `hetzner_adj.sh:123` 比對 cross.json 時才擋得下來；
#   ②`echo "== replay exit $?"` 漏在 tee 之外，log 就沒有標記、守門 a 永遠不過。
# 做的事：0 同步 main 並印 HEAD（HEAD 前進即改用新版重新執行）→ 1 開跑前守門（工作樹／池語意／window／現行碼 model_version）
#   → 1b `scan_features --rebuild`（預設；opt-out 時改為只檢查 features.db 存在）→ 2 replay_scores（首次 --rebuild、
#   中斷後重貼同一行走 --resume）→ 3 收尾把 `== replay exit <rc>` 寫成 log 的最後一行，退出碼＝重播（或失敗那一步）的退出碼。
# **預設先重建特徵庫（2026-10-09 起，裁定 #73 C；原本預設不跑 scan_features）**：features.db 的參數指紋不含 model_version
#   （`scripts/scan_features.py` 的 `build_params`），所以「計分參數變了」本身不需重掃——但**原料**會變：晚到的除權事件
#   （6949 分割／1563 減資，ex 2026-09-07）落地後，舊 features.db 的已寫日不會重算，10-03 v2 全量重播（當時預設不重掃）
#   因此 09-07 起用了過期特徵。scan 全量約 5.4 分鐘，相對 12.6 h 的重播可忽略，所以改成每輪先 `--rebuild`
#   （同時寫入 `scan_inputs` 輸入基準，供例行輪 `scan_features --resume` 的 rc 4 守門比對）。
#   `HETZNER_REPLAY_SCAN=0` 為 opt-out（沿用既有 features.db、印 `!!` 警告），只在能證明特徵層原料沒變時用。
#   **續跑（標記檔指紋相同 → replay --resume）不重掃**：那一輪開跑時已重建過，重掃會讓同一輪前後兩段吃到不同的特徵。
#   scan 失敗即停、不呼叫 replay（log 末行照樣是 `== replay exit <scan 的 rc>`），標記檔也不會寫，重貼同一行從 scan 重來。
# log：`cache/logs/replay-adj.log`（`HETZNER_REPLAY_LOG` 可改；對應 `hetzner_adj.sh` 的 `HETZNER_ADJ_REPLAY_LOG`）。
#   **開跑前會把既有那份改名成 `<log>.prev-<UTC>`**——否則上一輪留下的 `== replay exit 0` 會在這一輪失敗時
#   仍然是末行，守門 a 就放行了一份不存在的成功重播。
# 中斷後重貼同一行即續跑：標記檔 `cache/logs/replay.started` 首行記本輪的 model_version 指紋，相同才 `--resume`，
#   不同（換了一組參數）一律重新 `--rebuild`；重播成功後刪除。
set -euo pipefail
REPO_DIR=${HETZNER_REPLAY_REPO:-$(cd "$(dirname "$0")/.." && pwd)}
# 自我複製後執行（同 hetzner_pit.sh／hetzner_adj.sh）：bash 邊讀邊執行，第 0 步 `git pull` 換掉本檔後正在跑的仍是舊版。
if [ -z "${HETZNER_REPLAY_SELF:-}" ]; then
  _self=$(mktemp "${TMPDIR:-/tmp}/hetzner_replay.XXXXXX")
  cp "$0" "$_self"
  HETZNER_REPLAY_SELF="$_self" HETZNER_REPLAY_REPO="$REPO_DIR" exec bash "$_self" "$@"
fi
trap 'rm -f "${HETZNER_REPLAY_SELF:-}"' EXIT
cd "$REPO_DIR"
mkdir -p cache/logs
LOG=${HETZNER_REPLAY_LOG:-cache/logs/replay-adj.log}
MARK=${HETZNER_REPLAY_MARK:-cache/logs/replay.started}
REEXEC="cache/logs/.replay-reexec"                       # 第 0 步要求重新執行的旗標（body 在子殼層，exec 只換得掉子殼層）
rm -f "$REEXEC"
if [ -z "${HETZNER_REPLAY_ROTATED:-}" ]; then
  if [ -f "$LOG" ]; then mv "$LOG" "$LOG.prev-$(date -u +%Y%m%dT%H%M%SZ)"; fi   # 舊 log 移開，末行標記不得跨輪沿用
  export HETZNER_REPLAY_ROTATED=1
fi

# 第 0～2 步；輸出全部 tee 進 $LOG，收尾標記在管線結束後才直接 append，確保它是檔案的最後一行。
body() {
  echo "== hetzner_replay  $(date -u +%FT%TZ)  log=$LOG"

  echo "== 0 同步 main 並核對 HEAD"
  git reset -q
  for f in data/calendar_tpe.json data/calendar_us.json; do          # 回補會改寫兩份日曆；repo 那份才是每日班的
    git ls-files --error-unmatch "$f" >/dev/null 2>&1 && git checkout -q -- "$f" || true
  done
  local dirty head_before
  dirty=$(git status --porcelain --untracked-files=no)
  if [ -n "$dirty" ]; then echo "!! 工作樹不乾淨，先處理再跑（未追蹤檔不算）："; echo "$dirty"; return 2; fi
  head_before=$(git rev-parse HEAD)
  git fetch -q origin main
  git checkout -q main
  git pull -q --ff-only origin main
  git log -1 --format='HEAD %h %ci %s'
  if [ "$(git rev-parse HEAD)" != "$head_before" ] && [ -z "${HETZNER_REPLAY_PULLED:-}" ]; then
    echo "== main 已由 ${head_before:0:7} 前進到 $(git rev-parse --short HEAD)，改用新版 scripts/hetzner_replay.sh 重新執行"
    : > "$REEXEC"
    return 0
  fi

  echo "== 1 開跑前守門（要在燒掉 12 小時之前擋下來）"
  python3 -c "import sys; sys.path.insert(0,'src'); from iching.universe import POOL_SEMANTICS; print('pool_semantics', POOL_SEMANTICS)" | grep -q "pit-1" \
    || { echo "!! 這份 main 的 universe.POOL_SEMANTICS 不是 pit-1，停止"; return 2; }
  local window sha resume=0
  window=$(python3 -c "import json;print(json.load(open('data/state/cross.json'))['meta']['window'])") \
    || { echo "!! data/state/cross.json 取不到 meta.window，停止（hetzner_adj.sh:121 用同一個值，這裡取不到後面也過不了）"; return 2; }
  echo "== window=$window（取自 repo data/state/cross.json，與 hetzner_adj.sh 守門 c 同一路徑）"
  sha=$(python3 -c "
import sys; sys.path.insert(0,'src')
from iching.score.params import build_params
ps = {m: build_params(m) for m in ('twse','tpex')}
print(','.join(f'{m}={p.model_version()}' for m, p in ps.items()) + ' calibrated=' + str(all(p.calibrated for p in ps.values())))") \
    || { echo "!! 取不到現行碼的 model_version，停止"; return 2; }
  echo "== 現行碼 model_version $sha"
  if [ -f "$MARK" ] && [ "$(head -n 1 "$MARK")" = "$sha" ]; then resume=1; fi

  if [ "$resume" = "1" ]; then
    echo "== 1b 續跑（$MARK 指紋相同）：不重掃，沿用本輪開跑時重建的 features.db"
  elif [ "${HETZNER_REPLAY_SCAN:-1}" = "0" ]; then
    echo "!! HETZNER_REPLAY_SCAN=0：跳過 scan_features --rebuild、沿用既有 features.db——晚到的原料（除權事件／價格修訂／池變動）"
    echo "!!   不會反映在已寫日的特徵上（裁定 #73 C 的起因）；只在能證明自上次 --rebuild 後特徵層原料沒變時用"
    [ -f cache/features.db ] || { echo "!! cache/features.db 不存在——HETZNER_REPLAY_SCAN=0 時本腳本不跑 scan_features；拿掉該設定即會先重建"; return 2; }
  else
    echo "== 1b scan_features --rebuild（預設；HETZNER_REPLAY_SCAN=0 可跳過）"
    local src=0
    python3 scripts/scan_features.py --rebuild --progress-every 400 || src=$?
    if [ "$src" != "0" ]; then echo "!! scan_features --rebuild 失敗 rc=$src，不呼叫 replay_scores"; return "$src"; fi
  fi
  [ ! -f cache/features.db ] || python3 - <<'PY' || return 2     # 印出本輪實際用的特徵庫指紋（讀不到＝壞檔，停）
import sqlite3
for dv, sv, sha in sqlite3.connect("file:cache/features.db?mode=ro", uri=True).execute(
        "SELECT data_version, schema_version, params_sha FROM scan_meta ORDER BY data_version"):
    print(f"== features.db data_version={dv} schema={sv} params_sha={sha}")
PY

  if [ "$resume" = "1" ]; then
    echo "== 2 replay_scores --resume --window $window（$MARK 存在且指紋相同＝同一輪的續跑）"
    python3 scripts/replay_scores.py --resume --window "$window" --progress-every 5
  else
    if [ -f "$MARK" ]; then echo "== （$MARK 的指紋與本輪不同，不沿用，重新全量）"; fi
    echo "== 2 replay_scores --rebuild --window $window（約 12.6 h，單核；中斷後重貼同一行走 --resume）"
    printf '%s\n%s\n' "$sha" "$(date -u +%FT%TZ)" > "$MARK"
    python3 scripts/replay_scores.py --rebuild --window "$window" --progress-every 5
  fi
}

set +e
body 2>&1 | tee -a "$LOG"
rc=${PIPESTATUS[0]}
set -e
if [ -f "$REEXEC" ]; then                                 # 第 0 步換了版本：不寫標記，交給新版從頭跑（log 不再輪替）
  rm -f "$REEXEC" "${HETZNER_REPLAY_SELF:-}"
  HETZNER_REPLAY_PULLED=1 HETZNER_REPLAY_SELF= exec bash "$REPO_DIR/scripts/hetzner_replay.sh" "$@"
fi
if [ "$rc" = "0" ]; then rm -f "$MARK"; fi
echo "== 收尾：replay rc=$rc，標記寫入 $LOG（下一步：bash scripts/hetzner_adj.sh <TO>）"
# 管線是同步的，讀到 PIPESTATUS 時 tee 其實已經收尾；關 fd 與 wait 是多一層保險，不是標記排在最後的理由
# （那個理由是「標記由本行之後的 append 寫，不經過 tee」）。⚠ 關掉 fd 1 之後任何 echo 都會失敗，別在下面加輸出。
exec 1>&- 2>&-
wait 2>/dev/null || true
printf '== replay exit %d\n' "$rc" >> "$LOG"
exit "$rc"
