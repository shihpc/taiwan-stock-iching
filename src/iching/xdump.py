"""子指標原始值 x 的落地出口（P3 第 3 項 c／d 校準，`docs/P3-CALIBRATION.md` §2 第 1 步；裁定 #54 Q1 (a)）。

`replay_scores.py --dump-x <dir>` 在計分時把每個 `SubResult.x`（送入變換前的**未截斷原值**）依鍵
`(scope, market, horizon, line, family, indicator_id)` 以 float32 追加寫進 `<dir>/<鍵>.f32`，只寫 `--dump-from ≤ T ≤ --dump-to`
的日期（訓練段；暖機／驗證／保留段不進母體，CLAUDE.md 約定 4），另寫 `<dir>/manifest.json`（每鍵筆數／跳過筆數／日期範圍
＋ `ParamSet` 的 c／d／transform／clip_policy 快照＋ `params_sha`）。`scripts/calibrate_d.py` 讀這些檔算 p85。

**只讀計分結果、不改任何計分邏輯**：掛在 `replay_step.step(..., on_scores=...)`，每算完一個 `MarketScores`／`StockScores`
被呼叫一次，走訪 `lines → families → subs` 抄 `x`。`on_scores=None`（預設、每日班）時 `step` 不多做任何事。

## 檔案格式

- `<key>.f32`：little-endian float32 平鋪，無檔頭；`numpy.fromfile(path, dtype="<f4")` 即得。每日收尾一次 append。
- 檔名＝鍵各段以 `__` 連接、非 `[A-Za-z0-9_.-]` 字元換成 `_`（`key_name()`），例如
  `stock__twse__short__2__A__dist_ma_short.f32`。
- `x` 為 `None` 或 NaN 者**跳過並計數**（`skipped`），不寫 0、不寫 NaN——那會污染 p85。
- **`basis`（B1.5 族 B）寫的是 `x − c_rolling_median`**（`x_kind="x_minus_rolling_c"`，manifest 的 `c` 為 `None`、
  校準時以 c=0 算 `|x−c|`）：它的 c 是每日滾動的 60 日中位數（`Param.c=None`、`ind_basis` 的 `meta.c_rolling_median`），
  存原始 x 就對不出 `|x−c|`。其餘鍵 `x_kind="x"`。

## 量級（真實池外推，`docs/P3-CALIBRATION.md` §2 第 1 步）

每個個股列（一檔 × 一期間 × 一日）約 20–25 個子指標、大盤列約 27 個；603 個訓練日 × ≈1,900 檔 × 3 期間 × ≈22
≈ 7.5×10⁷ 個 float32 ≈ 0.3 GB（文件估 0.6 GB 為上界）。
"""
from __future__ import annotations

import datetime as dt
import json
import math
import re
from array import array
from pathlib import Path
from typing import Any, Mapping

from .score.params import SCOPE_MARKET, SCOPE_STOCK, Param, ParamSet

MANIFEST = "manifest.json"
SCHEMA = 1
X_KIND_PLAIN = "x"
X_KIND_ROLLING = "x_minus_rolling_c"
_SAFE = re.compile(r"[^A-Za-z0-9_.-]")


class XDumpError(RuntimeError):
    pass


def key_name(scope: str, market: str, horizon: str, line: str, family: str, indicator_id: str) -> str:
    """鍵 → 檔名主幹（不含 `.f32`）。各段內的非法字元換 `_`，段間以 `__` 連接。"""
    parts = (scope, market, horizon, line, family, indicator_id)
    return "__".join(_SAFE.sub("_", str(p)) for p in parts)


def shared_d_of(p: Param) -> tuple[str, int] | None:
    """該子指標的 d 是否引用 `ParamSet` 的共用查表（`params.py` 模組 docstring「三份同一張查表」）：
    回 `(表名, 視窗 n)`；不是共用者回 None。判定依 `params.py` 建表時的引用關係（indicator_id 固定）。"""
    if p.indicator_id in ("dist_ma_short", "dist_ma_long", "spx_ma_distance"):
        return ("distance_d", int(p.window))
    if p.indicator_id == "ma20_slope":
        return ("market_slope_d", int(p.window[1]))
    if p.indicator_id == "ma_long_slope":
        return ("stock_slope_d", int(p.window[1]))
    return None


def _snapshot(ps: ParamSet, p: Param) -> dict[str, Any]:
    shared = shared_d_of(p)
    x_kind = X_KIND_ROLLING if (p.c is None and p.transform == "S" and p.clip_policy == "clip_3d") else X_KIND_PLAIN
    return {"scope": p.scope, "market": ps.market, "horizon": p.horizon, "line": p.line, "family": p.family,
            "indicator_id": p.indicator_id, "transform": p.transform, "clip_policy": p.clip_policy, "direction": p.direction,
            "c": p.c, "d": p.d, "unit": p.unit, "window": list(p.window) if isinstance(p.window, tuple) else p.window,
            "sub_weight": p.sub_weight, "scored": p.scored, "x_kind": x_kind,
            "shared_d_table": shared[0] if shared else None, "shared_d_n": shared[1] if shared else None,
            "shared_d_value": (getattr(ps, shared[0])[shared[1]] if shared else None)}


class XDump:
    """每日累積、每日收尾 append；`finish()` 寫 manifest。目錄必須是空的或不存在（append 到舊檔會重複計數）。"""

    def __init__(self, out_dir: str | Path, ps: Mapping[str, ParamSet], *, dump_from: str, dump_to: str,
                 data_version: str, params_sha: str, params: Mapping[str, Any]) -> None:
        if dump_to < dump_from:
            raise XDumpError(f"--dump-to {dump_to} 早於 --dump-from {dump_from}")
        self.dir = Path(out_dir)
        if self.dir.exists() and any(self.dir.iterdir()):
            raise XDumpError(f"--dump-x 目錄 {self.dir} 不是空的；append 到舊檔會重複計數，請換目錄或先移走")
        self.dir.mkdir(parents=True, exist_ok=True)
        self.dump_from, self.dump_to = dump_from, dump_to
        self.data_version, self.params_sha, self.params = data_version, params_sha, dict(params)
        self.ps = ps
        # 鍵快照：由 ParamSet 建（不是等資料來了才建），manifest 才會列出「該有卻一筆都沒出現」的鍵（n=0）
        self.snap: dict[str, dict[str, Any]] = {}
        self._key_of: dict[tuple[str, str, str, str, str, str], str] = {}
        for m, pset in ps.items():
            for (scope, h, line, fam, iid), p in sorted(pset.params.items()):
                k = key_name(scope, m, h, line, fam, iid)
                if k in self.snap:
                    raise XDumpError(f"鍵名碰撞：{k}")
                self.snap[k] = _snapshot(pset, p)
                self._key_of[(scope, m, h, line, fam, iid)] = k
        self.n: dict[str, int] = {k: 0 for k in self.snap}
        self.skipped: dict[str, int] = {k: 0 for k in self.snap}
        self.date_min: dict[str, str | None] = {k: None for k in self.snap}
        self.date_max: dict[str, str | None] = {k: None for k in self.snap}
        self._buf: dict[str, array] = {}
        self.days_dumped = 0
        self._cur_day: str | None = None
        self.unknown_keys: dict[str, int] = {}

    # -- 熱路徑 --
    def in_range(self, T: str) -> bool:
        return self.dump_from <= T <= self.dump_to

    def on_scores(self, sc: Any) -> None:
        """`MarketScores`／`StockScores` 各一次。只讀。"""
        scope = SCOPE_STOCK if hasattr(sc, "stock_id") else SCOPE_MARKET
        market, horizon, T = sc.market, sc.horizon, sc.tpe_date
        if not self.in_range(T):
            return
        if self._cur_day != T:
            self.flush_day()
            self._cur_day = T
            self.days_dumped += 1
        for line_id, lr in sc.lines.items():
            for fr in lr.families:
                for sub in fr.subs:
                    k = self._key_of.get((scope, market, horizon, line_id, fr.family, sub.indicator_id))
                    if k is None:
                        kk = key_name(scope, market, horizon, line_id, fr.family, sub.indicator_id)
                        self.unknown_keys[kk] = self.unknown_keys.get(kk, 0) + 1
                        continue
                    x = sub.x
                    if x is None or isinstance(x, bool) or not isinstance(x, (int, float)) or math.isnan(x) or math.isinf(x):
                        self.skipped[k] += 1
                        continue
                    if self.snap[k]["x_kind"] == X_KIND_ROLLING:
                        c_roll = sub.meta.get("c_rolling_median")
                        if c_roll is None:
                            self.skipped[k] += 1
                            continue
                        x = float(x) - float(c_roll)
                    buf = self._buf.get(k)
                    if buf is None:
                        buf = self._buf[k] = array("f")
                    buf.append(float(x))
                    self.n[k] += 1
                    if self.date_min[k] is None:
                        self.date_min[k] = T
                    self.date_max[k] = T

    def flush_day(self) -> None:
        for k, buf in self._buf.items():
            if len(buf):
                with open(self.dir / f"{k}.f32", "ab") as f:
                    f.write(buf.tobytes())
                del buf[:]

    def finish(self) -> Path:
        self.flush_day()
        keys = {}
        for k, s in self.snap.items():
            keys[k] = {**s, "n": self.n[k], "skipped": self.skipped[k], "date_min": self.date_min[k], "date_max": self.date_max[k],
                       "file": f"{k}.f32" if self.n[k] else None}
        payload = {"schema": SCHEMA, "dtype": "<f4", "data_version": self.data_version, "params_sha": self.params_sha,
                   "params": self.params, "dump_from": self.dump_from, "dump_to": self.dump_to, "days_dumped": self.days_dumped,
                   "written_at": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                   "tables": {m: {"distance_d": pset.distance_d, "market_slope_d": pset.market_slope_d, "stock_slope_d": pset.stock_slope_d}
                              for m, pset in self.ps.items()},
                   "n_values": sum(self.n.values()), "n_skipped": sum(self.skipped.values()),
                   "unknown_keys": dict(sorted(self.unknown_keys.items())), "keys": keys}
        path = self.dir / MANIFEST
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=1, sort_keys=True), encoding="utf-8")
        tmp.replace(path)
        return path


def load_manifest(dump_dir: str | Path) -> dict[str, Any]:
    p = Path(dump_dir) / MANIFEST
    if not p.is_file():
        raise XDumpError(f"找不到 {p}（dump 沒跑完或目錄錯）")
    try:
        m = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise XDumpError(f"{p} 讀取失敗：{e}") from e
    if m.get("schema") != SCHEMA or "keys" not in m or "params_sha" not in m:
        raise XDumpError(f"{p} 不是 schema {SCHEMA} 的 x dump manifest")
    return m
