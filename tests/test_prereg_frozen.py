"""登錄書 v1 凍結守門（PR-6，2026-09-28）：`docs/pre-registration.md` §0 寫的指紋必須與現行碼實算相等、凍結產物 sha256 釘死。

①§0 區段（`## 0.` 起到下一個 `## ` 止，**用區段切割、不是全檔**——前言的政策句「空欄位一律標 TBD」刻意留著）不含佔位標記；
②§0 的 `model_version`（twse／tpex）與 `params_sha` ＝ `build_params` ＋ `build_params_payload(mv, 320, cross.adv, fundamentals=True)`
  實算（同 `tests/test_apply_calibration.py` H3 的算法；`cross` 取 `data/state/cross.json`，`adv` 的 window／threshold 由快照帶）；
③`RULES_VERSION == "p2-score-engine-2"`（裁定 #68 之後的值，凍結時的規則版本）；
④`data/rank_table.json`（§1.6 卦別排序表，凍結後驗證段／保留段不得重排）sha256 釘值。
任一紅＝有人動了凍結內容而沒開新版本（`docs/pre-registration.md` §5）。凍結後真的要改模型：開 v2、換 tag、改這裡的釘值，
而不是把測試改綠。
"""
from __future__ import annotations

import hashlib
import re
import sys

from conftest import ROOT
from iching.features_io import params_fingerprint
from iching.run_common import build_params_payload, load_state
from iching.score.params import MARKETS, RULES_VERSION, build_params

sys.path.insert(0, str(ROOT / "scripts"))
from export_dataset import expected_params_sha  # noqa: E402

PREREG = ROOT / "docs" / "pre-registration.md"
CROSS = ROOT / "data" / "state" / "cross.json"
RANK_TABLE = ROOT / "data" / "rank_table.json"

FROZEN_RULES_VERSION = "p2-score-engine-2"
# 2026-09-28 實算：sha256sum data/rank_table.json（附錄 A 由 a7b713b 重生後未再變）
FROZEN_RANK_TABLE_SHA256 = "243a1a19070e6125b81f926545fbde996100420c4e7f4d4f7d7962a558cdab5f"
WINDOW = 320


def _section0() -> str:
    text = PREREG.read_text(encoding="utf-8")
    m = re.search(r"^## 0\. .*?(?=^## )", text, re.DOTALL | re.MULTILINE)
    assert m, "docs/pre-registration.md 找不到 `## 0.` 區段"
    return m.group(0)


def _row(sec: str, label: str) -> str:
    rows = [ln for ln in sec.splitlines() if ln.startswith(f"| {label} |")]
    assert len(rows) == 1, f"§0 的「{label}」列應恰 1 列，實得 {len(rows)}"
    return rows[0]


def _computed() -> tuple[dict[str, str], str, dict]:
    mv = {m: build_params(m).model_version() for m in MARKETS}
    cross = load_state(CROSS)
    payload = build_params_payload(mv, WINDOW, cross.adv, fundamentals=True)
    return mv, params_fingerprint(payload), cross.meta or {}


# ---------------------------------------------------------------------------
def test_section0_has_no_placeholder():
    sec = _section0()
    assert "TBD" not in sec, "§0 仍有佔位標記——登錄書未凍結或有人把欄位清回去了"
    # 政策句本身要還在（它是凍結 commit 機械判定的依據，不可順手刪掉）
    assert "空欄位一律標 `TBD`" in PREREG.read_text(encoding="utf-8")


def test_section0_model_version_and_params_sha_match_live_code():
    sec = _section0()
    mv_row = _row(sec, "`model_version`")
    m = re.search(r"twse \*\*`(p2-score-engine-[^`]+)`\*\*／tpex \*\*`(p2-score-engine-[^`]+)`\*\*", mv_row)
    assert m, f"§0 model_version 列格式不符：{mv_row[:120]}"
    written_mv = {"twse": m.group(1), "tpex": m.group(2)}
    sha_row = _row(sec, "`params_sha`")
    m2 = re.search(r"\*\*`([0-9a-f]{12})`\*\*", sha_row)
    assert m2, f"§0 params_sha 列格式不符：{sha_row[:120]}"
    written_sha = m2.group(1)

    mv, sha, meta = _computed()
    assert written_mv == mv, f"§0 model_version {written_mv} ≠ 現行碼 {mv}"
    assert written_sha == sha, f"§0 params_sha {written_sha} ≠ 現行碼實算 {sha}"
    # 與 H3 的另一條算法（db 記的 adv 參數）同值；與種子快照 meta 同值——三路互證
    want = {"window": WINDOW, "adv_window": 60, "adv_threshold": 30000000.0, "fundamentals": True}
    assert expected_params_sha(want)[:12] == sha
    assert meta.get("params_sha") == sha and meta.get("window") == WINDOW, f"cross.json meta {meta} 與凍結值不符"


def test_rules_version_frozen():
    assert RULES_VERSION == FROZEN_RULES_VERSION
    sec = _section0()
    assert f"**`{FROZEN_RULES_VERSION}`**" in _row(sec, "`RULES_VERSION`")


def test_rank_table_sha256_pinned():
    got = hashlib.sha256(RANK_TABLE.read_bytes()).hexdigest()
    assert got == FROZEN_RANK_TABLE_SHA256, f"data/rank_table.json sha256 {got} ≠ 釘值——排序表在凍結後被重生或改動"
