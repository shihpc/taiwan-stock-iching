"""重播驅動（Hetzner）與每日班（Actions）**共用**的執行期小工具（每日班 D-2a 自 `scripts/replay_scores.py` 搬出，行為不變）。

兩層必須用同一份 `TEXT_VERSION`、同一支 `build_params_payload`（參數指紋）、同一支快照讀寫與 `meta` 守門，
否則 parity 從「構造保證」退化成「兩份實作恰好一樣」。
"""
from __future__ import annotations

import os
from pathlib import Path

from . import replay_state as RS

TEXT_VERSION = "0.2"          # spec/P1-B4-hexagram-text.md:127


class ReplayDriverError(RuntimeError):
    pass


def build_params_payload(mv: dict[str, str], window: int, adv, *, fundamentals: bool = True) -> dict:
    """釘進 `replay_meta` 的參數集合：任何會改變輸出的設定都要在這裡（含 13b 基本面橋開關）。"""
    return {"model_version": dict(mv), "text_version": TEXT_VERSION, "window": int(window),
            "adv_window": adv.window, "adv_threshold": adv.threshold, "state_schema": RS.STATE_SCHEMA,
            "fundamentals": bool(fundamentals)}


def load_state(path: Path) -> RS.CrossDayState:
    try:
        return RS.CrossDayState.from_json(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError, KeyError, RS.ReplayStateError) as e:
        raise ReplayDriverError(f"狀態快照讀取失敗 {path}：{e}") from e


def save_state(path: Path, cross: RS.CrossDayState) -> None:
    """同目錄 `.tmp` 再 `replace`（原子）＋ fsync：程序被殺或斷電都不會留半個檔。"""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(cross.to_json())
        f.flush()
        os.fsync(f.fileno())
    tmp.replace(path)


def check_snapshot_meta(cross: RS.CrossDayState, *, window: int, params_sha: str, path: Path) -> None:
    """快照的 `meta`（驅動寫入的 window／params_sha）必須與本次相同——`--window` 不在 `replay_meta` 之外的任何地方，
    `--from --state` 進新 DB 時若不比對，會無聲產出既非舊 window 也非新 window 的列（13a-3 驗收實測）。
    舊快照沒有 meta 一律拒，不猜。"""
    m = cross.meta or {}
    if m.get("window") != window or m.get("params_sha") != params_sha:
        raise ReplayDriverError(f"快照 {path} 的 meta={m} 與本次 window={window}／參數指紋={params_sha} 不符；"
                                f"快照只能接續產生它的那組設定（不同 window／參數請 --rebuild 全量）")
