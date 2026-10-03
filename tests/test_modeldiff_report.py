"""`runs/modeldiff/report_2026-09-27.{json,txt}`（§36）綁定到 Hetzner `85e89ed` 那一份：#68／#69 換模型後新舊 `scores.db` 的
換版比對報告（§33 工具的第一次實跑）。

守的四件事：
① **報告檔＝`85e89ed` 那一份**（json／txt sha256 全文獨立寫死；竄改任一數字 → 紅）；
② 報告內容的關鍵字面（rc=0、C1–C7 全 OK、兩側 `model_version`／`params_sha`、允許爻、大盤 6 組差異 0、`twse|short` 爻6 全 0）
   **獨立寫死**，不由被測函式產生（§20.1 末的判準 ③）；
③ txt ＝ `model_diff.render_txt(json)`（兩檔不可能脫鉤）；
④ §36 的兩張統計表＝由 json 重新格式化的字串（文件與報告不可能脫鉤；格式在本檔獨立重寫，不呼叫 `render_txt`）。
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

import model_diff as MD  # noqa: E402

REPORT = ROOT / "runs" / "modeldiff" / "report_2026-09-27.json"
REPORT_TXT = REPORT.with_suffix(".txt")
DOC = ROOT / "docs" / "P3-CALIBRATION.md"

#: `git show 85e89ed:runs/modeldiff/report_2026-09-27.json | sha256sum`（2026-09-27 實算、獨立寫死）。
REPORT_SHA256 = "bb6835000d50cde1c6615050cfa8fd3f35c55b6a96b518c754c6046e3710311c"
#: 同上，`.txt`。
REPORT_TXT_SHA256 = "ba9f1924b3696b93c4772252acf010c4b636f253cafef51abebad9c692640454"

OLD_MV = {"twse": "p2-score-engine-1.0bb386e9cf3b", "tpex": "p2-score-engine-1.8eb4f29fec3a"}
NEW_MV = {"twse": "p2-score-engine-2.01697576a7b0", "tpex": "p2-score-engine-2.83b5c5dfdb23"}
OLD_PARAMS_SHA, NEW_PARAMS_SHA = "c7385e78cb9f", "8ca174ee8bc7"
GROUPS = ("twse|short", "twse|swing", "twse|mid", "tpex|short", "tpex|swing", "tpex|mid")


@pytest.fixture()
def rep() -> dict:
    return json.loads(REPORT.read_text(encoding="utf-8"))


def _sec36() -> str:
    doc = DOC.read_text(encoding="utf-8")
    i = doc.index("\n## 36. ")
    j = doc.find("\n## ", i + 1)
    return doc[i:] if j < 0 else doc[i:j]


# ---- ① 報告檔＝85e89ed 那一份 ----

def test_report_bound_to_85e89ed():
    """兩檔 sha256 全文＝Hetzner `85e89ed`（竄改任一數字 → 紅）。"""
    assert hashlib.sha256(REPORT.read_bytes()).hexdigest() == REPORT_SHA256
    assert hashlib.sha256(REPORT_TXT.read_bytes()).hexdigest() == REPORT_TXT_SHA256


def test_sha_recorded_in_doc():
    sec = _sec36()
    assert REPORT_SHA256 in sec and REPORT_TXT_SHA256 in sec and "`85e89ed`" in sec


# ---- ② 關鍵字面獨立寫死 ----

def test_rc0_and_all_invariants_ok(rep):
    assert rep["schema"] == 1 and rep["tool"] == "scripts/model_diff.py"
    assert rep["result_rc"] == 0
    assert tuple(rep["invariants"]) == ("C1", "C2", "C3", "C4", "C5", "C6", "C7") == MD.INVARIANTS
    for c, v in rep["invariants"].items():
        assert v == {"ok": True, "n": 0, "examples": []}, c


def test_fingerprints_literal(rep):
    old, new = rep["dbs"]["old"], rep["dbs"]["new"]
    assert old["path"] == "cache/scores_pre68.db" and new["path"] == "cache/scores.db"
    assert old["model_versions"] == {m: [v] for m, v in OLD_MV.items()}
    assert new["model_versions"] == {m: [v] for m, v in NEW_MV.items()}
    assert old["params_sha"] == {"fm-20260911-01": OLD_PARAMS_SHA}
    assert new["params_sha"] == {"fm-20260911-01": NEW_PARAMS_SHA}
    assert old["sha256"] == "123700e345acb0013d83262a6842fb4ab1425b73648d593c5cec1080123cdb6c"
    assert new["sha256"] == "42aef5b3ebd7873d81b68390babbba0d9e719d53dda98ab489deccae7c94001a"
    assert rep["current_model_versions"] == NEW_MV
    rd = rep["replay_day"]
    assert rd["model_version_twse"] == {"old": [OLD_MV["twse"]], "new": [NEW_MV["twse"]]}
    assert rd["model_version_tpex"] == {"old": [OLD_MV["tpex"]], "new": [NEW_MV["tpex"]]}


def test_new_side_is_current_code(rep):
    """C6 的另一半：報告的新側＝**現在**這份碼的 `model_version`。碼再換版（新裁定）時本測試會紅——那時報告就過期了，
    要重跑 `hetzner_modeldiff.sh`、換新報告並改本檔的 `NEW_MV`，不是把這條拿掉。
    **prereg-v2 紅窗的唯一例外（2026-10-01 PR-B，`docs/P3-CALIBRATION.md` §37）**：碼已升 `p2-score-engine-3`、v2 的換版比對報告要等 Hetzner
    全量重播後由 PR-D 拷入；這段期間本報告仍是 v1 時代那份，新側必須仍＝v1 凍結值（`NEW_MV`，與 `docs/pre-registration-v1.md` §0 同值）。
    PR-D 換新報告並改 `NEW_MV` 後此例外自然失效（現行碼＝報告新側），不是放寬守門。"""
    cur = MD.current_model_versions()
    if cur != rep["current_model_versions"]:
        assert rep["current_model_versions"] == NEW_MV, "報告新側既不是現行碼、也不是 v1 凍結值"
        assert all(v.startswith("p2-score-engine-3.") for v in cur.values()), f"現行碼 {cur} 不是 prereg-v2（-3）——報告過期，重跑 modeldiff"


def test_allowed_lines_literal(rep):
    assert rep["allowed_lines"] == {"twse": [1, 3, 6], "tpex": [1]}
    assert {m: list(v) for m, v in MD.ALLOWED_LINES.items()} == rep["allowed_lines"]
    for k, g in rep["groups"].items():
        assert list(g["lines"]) == [str(x) for x in rep["allowed_lines"][k.split("|")[0]]], k


def test_scope_and_counts(rep):
    assert rep["range"] == {"start": "2021-01-01", "end": "2024-12-31"}
    assert rep["include_holdout"] is False and rep["data_version_filter"] is None
    assert rep["days"] == {"compared": 971, "skipped_before_train": {"old": 245, "new": 245},
                           "skipped_after_range": {"old": 412, "new": 412}}
    assert rep["rows_compared"] == 5_249_784
    assert rep["generated_at"] == "2026-09-27T09:25:46+00:00"
    assert rep["elapsed_s"] == 806.408 and rep["rss_peak_mib"] == 159.4
    # 列數守恆：六組個股列＋大盤列＝rows_compared（本檔手算：3×965,061＋3×782,925＋6×971＝5,249,784）
    assert 3 * 965_061 + 3 * 782_925 + 6 * 971 == 5_249_784
    assert sum(g["n_rows"] for g in rep["groups"].values()) + sum(m["n_rows"] for m in rep["market_rows"].values()) == rep["rows_compared"]


def test_market_rows_six_groups_zero_diff(rep):
    assert tuple(rep["market_rows"]) == GROUPS == tuple(rep["groups"])
    for k, m in rep["market_rows"].items():
        assert m == {"n_rows": 971, "n_diff_rows": 0}, k


def test_twse_short_line6_all_zero(rep):
    """判讀 ②：`twse|short` 上爻零變動（#69 的 twse 上爻鍵只有 swing／mid，`_SEC11`）。"""
    l6 = rep["groups"]["twse|short"]["lines"]["6"]
    assert l6 == {"score_changed": 0, "unknown_flip": 0, "formal_flip": 0, "abs_delta_n": 965_061,
                  "abs_delta_nonzero": 0, "abs_delta_max": 0.0, "abs_delta_p50": 0.0, "abs_delta_p99": 0.0}
    # 對稱的另一側：swing／mid 的上爻確實有變（不是「上爻整個沒被比」）
    assert rep["groups"]["twse|swing"]["lines"]["6"]["score_changed"] == 817_420
    assert rep["groups"]["twse|mid"]["lines"]["6"]["score_changed"] == 824_410


def test_c5_stock_any_unknown_delta(rep):
    """判讀 ③：C5 只報差的欄，方向為增加。"""
    assert rep["replay_day"]["n_stock_any_unknown"] == {"days_diff": 641, "sum_delta": 1483, "max_abs_delta": 5}


def test_line1_max_abs_delta_is_half_span(rep):
    """判讀 ①：爻1 max|Δ| 與 S 值域半幅的倍率（1／1／0.5／1／1／5/7；本檔手算：(92.702703 − 7.297297)/2＝42.702703）。"""
    half = (92.70270270270271 - 7.297297297297298) / 2
    want = {"twse|short": 1.0, "twse|swing": 1.0, "twse|mid": 0.5, "tpex|short": 1.0, "tpex|swing": 1.0, "tpex|mid": 5 / 7}
    for k, r in want.items():
        assert rep["groups"][k]["lines"]["1"]["abs_delta_max"] == pytest.approx(half * r, rel=1e-12), k


# ---- ③ txt＝render_txt(json) ----

def test_txt_equals_render_txt(rep):
    assert MD.render_txt(rep) == REPORT_TXT.read_text(encoding="utf-8")


# ---- ④ §36 的兩張表＝由 json 重新格式化（格式本檔獨立重寫） ----

def _g6(x):
    return "—" if x is None else (f"{x:.6g}" if isinstance(x, float) else str(x))


def _rows_from_json(rep: dict) -> list[str]:
    rows = []
    for k, g in rep["groups"].items():
        kk = k.replace("|", "\\|")
        for ln, a in g["lines"].items():
            rows.append(f"| {kk} | {g['n_rows']:,} | {g['n_diff_rows']:,} | 爻{ln} | {a['score_changed']:,} | {a['unknown_flip']:,} | "
                        f"{a['formal_flip']:,} | {a['abs_delta_n']:,}（{a['abs_delta_nonzero']:,}） | {_g6(a['abs_delta_max'])} | "
                        f"{_g6(a['abs_delta_p50'])} | {_g6(a['abs_delta_p99'])} |")
        rows.append(f"| {kk} | {g['base_score_changed']:,}（{_g6(g['base_score_max_abs_delta'])}） | "
                    f"{g['king_wen_changed']:,}（{g['king_wen_changed_ratio']:.4%}） | {g['king_wen_provisional_changed']:,} |")
    return rows


def test_doc_tables_match_json(rep):
    sec = _sec36()
    rows = _rows_from_json(rep)
    assert len(rows) == 12 + 6
    for r in rows:
        assert sec.count(r) == 1, r


def test_doc_tables_would_catch_a_changed_number(rep):
    """④ 的反向：json 改一個數字，重算出的列就不在文件裡（證明比對不是恆真）。"""
    rep["groups"]["tpex|short"]["king_wen_changed"] += 1
    sec = _sec36()
    assert any(sec.count(r) == 0 for r in _rows_from_json(rep))


def test_doc_c3_confirmed():
    """M5：C3 兩條解釋已於 2026-09-27 由使用者確認（原文「確認兩條解釋，modeldiff 結果登錄為已驗證」）——§36 須寫明已確認、
    §33 該句改為正式語意，且全檔不得再出現「主對話已送出詢問」。"""
    sec = _sec36()
    assert "已於 2026-09-27 由使用者確認" in sec and "確認兩條解釋，modeldiff 結果登錄為已驗證" in sec
    doc = DOC.read_text(encoding="utf-8")
    assert "C3 的兩條本工具解釋（**使用者 2026-09-27 確認為 C3 正式語意**，§36）" in doc
    assert "主對話已送出詢問" not in doc and "待使用者確認（主對話" not in doc
