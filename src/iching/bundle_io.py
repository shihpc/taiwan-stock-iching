"""原料包（`replay_state.DayBundle`）的序列化——每日班與 Hetzner 回補層共用的交換格式（`docs/P2-DAILY-PLAN.md` §2 路 C）。

- 一天一個檔：`runs/collect/<T>-daily.json.gz`。JSON 物件、鍵排序、`separators=(",", ":")`、`allow_nan=False`
  （NaN 一律先轉 None，讀回時 `_f()` 再變 NaN——`WindowCache.ingest` 本來就把 None 當缺值）。
- **決定性**：同一份 `DayBundle` 兩次 `dumps` 逐位元相同（parity 儀式靠這一點比原料包）。
- 只放 `DayBundle` 的欄位，不放 features（廣度／產業／P_cs 由每日班自己算）——`breadth`／`industry`／`p_cs`
  讀回時一律為空 dict；需要它們的路徑自己補。
- 不做任何 DB 存取。
"""
from __future__ import annotations

import gzip
import json
import math
import zlib
from pathlib import Path
from typing import Any

from .replay_state import DayBundle

BUNDLE_SCHEMA = 1
BUNDLE_DIR = "runs/collect"
BAND = "daily"


class BundleError(RuntimeError):
    pass


def _clean(v: Any) -> Any:
    """NaN／inf → None；tuple → list；巢狀遞迴。"""
    if isinstance(v, float):
        return None if (math.isnan(v) or math.isinf(v)) else v
    if isinstance(v, dict):
        return {str(k): _clean(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [_clean(x) for x in v]
    return v


def bundle_to_dict(b: DayBundle) -> dict:
    return _clean({
        "schema": BUNDLE_SCHEMA, "band": BAND, "tpe_date": b.tpe_date,
        "index": b.index, "stocks": b.stocks, "official": b.official, "futures": b.futures,
        "total_margin": b.total_margin, "vix": b.vix, "foreign_net_oi": b.foreign_net_oi,
        "us": [list(x) for x in b.us], "fx": [list(x) for x in b.fx],
    })


def bundle_from_dict(d: dict) -> DayBundle:
    if d.get("schema") != BUNDLE_SCHEMA:
        raise BundleError(f"原料包 schema 不符：{d.get('schema')!r} ≠ {BUNDLE_SCHEMA}")
    if not d.get("tpe_date"):
        raise BundleError("原料包缺 tpe_date")
    return DayBundle(
        tpe_date=str(d["tpe_date"]),
        index={k: dict(v) for k, v in (d.get("index") or {}).items()},
        stocks={k: dict(v) for k, v in (d.get("stocks") or {}).items()},
        official={k: dict(v) for k, v in (d.get("official") or {}).items()},
        futures=dict(d.get("futures") or {}),
        total_margin=d.get("total_margin"), vix=d.get("vix"), foreign_net_oi=d.get("foreign_net_oi"),
        us=[tuple(x) for x in (d.get("us") or [])], fx=[tuple(x) for x in (d.get("fx") or [])],
    )


def dumps(b: DayBundle) -> bytes:
    return json.dumps(bundle_to_dict(b), ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def loads(raw: bytes) -> DayBundle:
    try:
        return bundle_from_dict(json.loads(raw.decode("utf-8")))
    except (ValueError, UnicodeDecodeError) as e:
        raise BundleError(f"原料包非合法 JSON：{e}") from e


def bundle_path(root: Path, tpe_date: str, band: str = BAND) -> Path:
    return Path(root) / BUNDLE_DIR / f"{tpe_date}-{band}.json.gz"


def dumps_json(obj: Any) -> bytes:
    """任意 JSON 物件的**決定性**序列化（與 `dumps` 同一組參數：`_clean` NaN→null、鍵排序、無空白）——
    entrants 側檔（`daily_core`）與原料包共用同一套，位元組才可比。"""
    return json.dumps(_clean(obj), ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def write_json_gz(path: Path, obj: Any) -> Path:
    """`dumps_json` → gzip（mtime 固定 0）→ 同目錄 `.tmp` 再 `replace`。與 `write_bundle` 同款，供非 `DayBundle` 的 gz 檔用。"""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "wb") as f, gzip.GzipFile(fileobj=f, mode="wb", mtime=0) as g:
        g.write(dumps_json(obj))
    tmp.replace(path)
    return path


def read_json_gz(path: Path) -> Any:
    """壞檔一律 `BundleError`：讀不到／非 gzip（`BadGzipFile` 是 OSError）／截斷（`EOFError`）／壓縮流損壞（`zlib.error`）／非 JSON。"""
    try:
        with gzip.open(path, "rb") as g:
            return json.loads(g.read().decode("utf-8"))
    except (OSError, EOFError, zlib.error) as e:
        raise BundleError(f"讀不到 gz JSON {path}：{type(e).__name__}: {e}") from e
    except (ValueError, UnicodeDecodeError) as e:
        raise BundleError(f"{path} 非合法 gz JSON：{type(e).__name__}: {e}") from e


def write_bundle(root: Path, b: DayBundle, band: str = BAND) -> Path:
    """gzip（mtime 固定 0 → 同內容同位元組，git 不會因時戳而 diff）。"""
    p = bundle_path(root, b.tpe_date, band)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + ".tmp")
    with open(tmp, "wb") as f, gzip.GzipFile(fileobj=f, mode="wb", mtime=0) as g:
        g.write(dumps(b))
    tmp.replace(p)
    return p


def read_bundle(path: Path) -> DayBundle:
    try:
        with gzip.open(path, "rb") as g:
            return loads(g.read())
    except OSError as e:
        raise BundleError(f"讀不到原料包 {path}：{e}") from e


def list_bundles(root: Path, band: str = BAND) -> list[tuple[str, Path]]:
    """`(tpe_date, path)` 升冪。"""
    d = Path(root) / BUNDLE_DIR
    if not d.exists():
        return []
    out = []
    suf = f"-{band}.json.gz"
    for p in sorted(d.iterdir()):
        if p.name.endswith(suf):
            out.append((p.name[: -len(suf)], p))
    return out
