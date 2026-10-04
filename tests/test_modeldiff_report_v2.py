"""`runs/modeldiff/report_2026-10-04.{json,txt}`（§37）綁定到 Hetzner `4622276` 那一份：prereg-v2（裁定 #71，NaN→Missing 守門、
`RULES_VERSION` -2 → -3）換版後新舊 `scores.db` 的換版比對報告——`--profile prereg-v2`（允許爻空集合、任何爻差異皆違反）、
`--expect-dates` 空檔（D_nan＝∅）。**預期與實測都是「全段逐位相同」**：rc=0、971 日 5,249,784 列差異 0。

體例同 `tests/test_modeldiff_report.py`（09-27 那份為 v1 時代 #68／#69 的報告，**不改**，兩支並存）。守的四件事：
① **報告檔＝`4622276` 那一份**（json／txt sha256 全文獨立寫死；竄改任一數字 → 紅）；
② 關鍵字面**獨立寫死**（rc=0、C1–C7 全 OK 且預期差異 0、兩側 `model_version`／`params_sha`／db sha256、profile `prereg-v2`、
   `allowed_lines` 兩市場皆空、`expect_dates` n=0、大盤 6 組差異 0、個股 6 組 `n_diff_rows` 0 且 `lines` 空、C5 零差），不由被測函式產生；
③ txt ＝ `model_diff.render_txt(json)`（兩檔不可能脫鉤）；
④ 新側＝**現行碼** `model_version`（C6 的另一半；碼再換版時本測試會紅——那時要重跑 modeldiff、換新報告，不是拿掉這條），
   且 §37 記有兩個 sha256 與 `4622276`。
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "src"))

import model_diff as MD

REPORT = ROOT / "runs" / "modeldiff" / "report_2026-10-04.json"
REPORT_TXT = REPORT.with_suffix(".txt")
DOC = ROOT / "docs" / "P3-CALIBRATION.md"

#: `git show 4622276:runs/modeldiff/report_2026-10-04.json | sha256sum`（2026-10-04 實算、獨立寫死）。
REPORT_SHA256 = "75234cb84e7cf1272e51fbe8bde90f035490e9a14b42cd4c5269c433315dd009"
#: 同上，`.txt`。
REPORT_TXT_SHA256 = "00b6bb066048e8b0b6daafe2648600ee60753f2f5e81669c1a6b67c37e136dfc"

#: 舊側＝v1 凍結值（`docs/pre-registration-v1.md` §0）；新側＝v2（`docs/pre-registration.md` §0）。
OLD_MV = {"twse": "p2-score-engine-2.01697576a7b0", "tpex": "p2-score-engine-2.83b5c5dfdb23"}
NEW_MV = {"twse": "p2-score-engine-3.4b5db7fc6f6d", "tpex": "p2-score-engine-3.15407a6adb13"}
OLD_PARAMS_SHA, NEW_PARAMS_SHA = "8ca174ee8bc7", "cb3f2d905846"
#: 0b 備份 `cache/scores_prereg_v1.db`（`scratchpad/hetzner_0a_result.md`，使用者 2026-10-03 實跑 `sha256sum`）／重播後的 `cache/scores.db`。
OLD_DB_SHA256 = "fdbb850ff9b2c3d839f4967c6a43d059e71b1c8728ba2a454cc2ba9838c838d1"
NEW_DB_SHA256 = "5f187582c034ae90560f65dff312ddcedc219219046ece4d32d0c7d5c1943cf7"
GROUPS = ("twse|short", "twse|swing", "twse|mid", "tpex|short", "tpex|swing", "tpex|mid")
N_ROWS = {"twse": 965_061, "tpex": 782_925}


@pytest.fixture()
def rep() -> dict:
    return json.loads(REPORT.read_text(encoding="utf-8"))


def _sec37() -> str:
    doc = DOC.read_text(encoding="utf-8")
    i = doc.index("\n## 37. ")
    j = doc.find("\n## ", i + 1)
    return doc[i:] if j < 0 else doc[i:j]


# ---- ① 報告檔＝4622276 那一份 ----

def test_report_bound_to_4622276():
    assert hashlib.sha256(REPORT.read_bytes()).hexdigest() == REPORT_SHA256
    assert hashlib.sha256(REPORT_TXT.read_bytes()).hexdigest() == REPORT_TXT_SHA256


def test_sha_recorded_in_doc():
    sec = _sec37()
    assert REPORT_SHA256 in sec and REPORT_TXT_SHA256 in sec and "`4622276`" in sec


# ---- ② 關鍵字面獨立寫死 ----

def test_rc0_and_all_invariants_ok_with_zero_expected(rep):
    assert rep["schema"] == 1 and rep["tool"] == "scripts/model_diff.py"
    assert rep["result_rc"] == 0
    assert tuple(rep["invariants"]) == ("C1", "C2", "C3", "C4", "C5", "C6", "C7") == MD.INVARIANTS
    for c, v in rep["invariants"].items():
        assert v == {"ok": True, "n": 0, "examples": [], "expected_n": 0, "expected_examples": []}, c
    assert rep["expected_diffs"] == {"by_code": {c: 0 for c in MD.INVARIANTS}, "days": [], "n_days": 0}


def test_profile_prereg_v2_and_empty_expect_dates(rep):
    """v2 設定檔：允許爻兩市場皆空（任何爻差異皆違反）；D_nan 普查為空 → 預期差異日 0。"""
    assert rep["profile"] == "prereg-v2"
    assert rep["allowed_lines"] == {"twse": [], "tpex": []}
    assert {m: list(v) for m, v in MD.PROFILES["prereg-v2"].items()} == rep["allowed_lines"]
    assert rep["expect_dates"] == {"path": "cache/nan_dates.txt", "n": 0, "dates": []}
    for k, g in rep["groups"].items():
        assert g["lines"] == {}, k        # 允許爻空集合 → 沒有逐爻統計


def test_fingerprints_literal(rep):
    old, new = rep["dbs"]["old"], rep["dbs"]["new"]
    assert old["path"] == "cache/scores_prereg_v1.db" and new["path"] == "cache/scores.db"
    assert old["model_versions"] == {m: [v] for m, v in OLD_MV.items()}
    assert new["model_versions"] == {m: [v] for m, v in NEW_MV.items()}
    assert old["params_sha"] == {"fm-20260911-01": OLD_PARAMS_SHA}
    assert new["params_sha"] == {"fm-20260911-01": NEW_PARAMS_SHA}
    assert old["sha256"] == OLD_DB_SHA256 and new["sha256"] == NEW_DB_SHA256
    assert rep["current_model_versions"] == NEW_MV
    rd = rep["replay_day"]
    assert rd["model_version_twse"] == {"old": [OLD_MV["twse"]], "new": [NEW_MV["twse"]]}
    assert rd["model_version_tpex"] == {"old": [OLD_MV["tpex"]], "new": [NEW_MV["tpex"]]}


def test_new_side_is_current_code(rep):
    """C6 的另一半：報告的新側＝**現在**這份碼的 `model_version`（嚴格，無紅窗例外——v2 報告就是為現行碼跑的）。"""
    assert MD.current_model_versions() == rep["current_model_versions"] == NEW_MV


def test_scope_and_counts(rep):
    assert rep["range"] == {"start": "2021-01-01", "end": "2024-12-31"}
    assert rep["include_holdout"] is False and rep["data_version_filter"] is None
    # 範圍後略過 424 日（09-27 那份為 412：db 末日 09-26 → 10-02，多 12 個交易日）
    assert rep["days"] == {"compared": 971, "skipped_before_train": {"old": 245, "new": 245},
                           "skipped_after_range": {"old": 424, "new": 424}}
    assert rep["rows_compared"] == 5_249_784
    assert rep["generated_at"] == "2026-10-04T14:35:31+00:00"
    assert rep["elapsed_s"] == 639.351 and rep["rss_peak_mib"] == 89.3
    # 列數守恆（本檔手算：3×965,061＋3×782,925＋6×971＝5,249,784）
    assert 3 * N_ROWS["twse"] + 3 * N_ROWS["tpex"] + 6 * 971 == 5_249_784
    assert sum(g["n_rows"] for g in rep["groups"].values()) + sum(m["n_rows"] for m in rep["market_rows"].values()) == rep["rows_compared"]


def test_market_rows_six_groups_zero_diff(rep):
    assert tuple(rep["market_rows"]) == GROUPS == tuple(rep["groups"])
    for k, m in rep["market_rows"].items():
        assert m == {"n_rows": 971, "n_diff_rows": 0}, k


def test_stock_rows_six_groups_zero_diff(rep):
    """乾淨日零影響（Hetzner 側）：六組個股列差異 0、`base_score`／`king_wen`／`king_wen_provisional` 皆 0 變。"""
    for k, g in rep["groups"].items():
        assert g == {"n_rows": N_ROWS[k.split("|")[0]], "n_diff_rows": 0, "lines": {}, "base_score_changed": 0,
                     "base_score_max_abs_delta": 0.0, "king_wen_changed": 0, "king_wen_changed_ratio": 0.0,
                     "king_wen_provisional_changed": 0}, k


def test_c5_stock_any_unknown_zero(rep):
    assert rep["replay_day"]["n_stock_any_unknown"] == {"days_diff": 0, "sum_delta": 0, "max_abs_delta": 0}


# ---- ③ txt＝render_txt(json) ----

def test_txt_equals_render_txt(rep):
    assert MD.render_txt(rep) == REPORT_TXT.read_text(encoding="utf-8")


def test_sha_would_catch_a_changed_number(rep):
    """①的反向：json 改一個數字再序列化，sha256 必不等於釘值（證明綁定不是恆真）。"""
    rep["groups"]["tpex|short"]["n_diff_rows"] += 1
    assert hashlib.sha256(json.dumps(rep, ensure_ascii=False).encode("utf-8")).hexdigest() != REPORT_SHA256
