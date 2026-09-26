"""`scripts/stats_appendix.py`：§16.5 `:712`／`:714` 步驟4／`:716` 實測結果 → 登錄書附錄 C（裁定 #66／#70）。

守的五件事：
① **附錄內容＝從兩份報告重新產生的結果**（附錄與 JSON 不可能脫鉤）；
② `rank_table.py`／`t717_appendix.py` 重寫附錄 A／B 時**不會吃掉**附錄 C，反之亦然；
③ 報告自洽、兩份報告互相綁定、`:712`／`:714` 為 PASS、各說明段的定性句——任一被資料推翻就**中止**（rc=2），
   且紅的是**聲稱要守的那一道**（以錯誤訊息比對；每支守門測試都讓其餘部分保持自洽）；
④ 確認狀態 `CONFIRMED` 與報告的須解釋組**雙向**比對，逐組綁定到 #66（5 組，2026-09-26 依新數字重確認）／#70（1 組）；
⑤ `--check` 的 rc 語意：一致 0、附錄過期 1、任何例外 2。

期待值一律在本檔**獨立寫死**（2026-09-24 從兩份 JSON 手查；2026-09-26 依 #68／#69 換模型後重跑的報告 `abbda97` 重查改值），
不由被測函式產生。
"""
from __future__ import annotations

import copy
import json
import shutil
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import rank_table as RT  # noqa: E402
import stats_appendix as SA  # noqa: E402
import t717_appendix as TA  # noqa: E402

REPORT = ROOT / "runs" / "stats" / "report_2026-09-14.json"
DIAG = ROOT / "runs" / "stats" / "diag716_2026-09-14.json"
PREREG = ROOT / "docs" / "pre-registration.md"

K1_TPEX_S = ("stock", "tpex", "short", "1", "reweighted")
K1_TWSE_S = ("stock", "twse", "short", "1", "reweighted")
K1_TWSE_W = ("stock", "twse", "swing", "1", "reweighted")
K1_TPEX_W = ("stock", "tpex", "swing", "1", "reweighted")  # 換模型後新被標（裁定 #70）
K4 = ("stock", "tpex", "short", "4", "reweighted")
K5 = ("stock", "tpex", "short", "5", "reweighted")

#: 各組「裁定」欄的完整字串（獨立寫死，不由 `SA.RULING_NOTE` 產生）。
STATUS_66 = "已確認為預期行為（#66，2026-09-24 裁定；2026-09-26 依新數字重確認）"
STATUS_70 = "已確認為預期行為（#70，2026-09-26 裁定）"
EXPECT_STATUS = {K1_TPEX_S: STATUS_66, K1_TWSE_S: STATUS_66, K1_TWSE_W: STATUS_66, K4: STATUS_66, K5: STATUS_66,
                 K1_TPEX_W: STATUS_70}


@pytest.fixture()
def rep() -> dict:
    return json.loads(REPORT.read_text(encoding="utf-8"))


@pytest.fixture()
def diag() -> dict:
    return json.loads(DIAG.read_text(encoding="utf-8"))


def B(rep: dict, diag: dict) -> str:
    return SA.build(rep, diag, REPORT, DIAG)


def _block(doc: str) -> str:
    i, j = doc.index(SA.BEGIN), doc.index(SA.END)
    return doc[i:j + len(SA.END)]


def _g(rep: dict, key: tuple) -> dict:
    return next(g for g in rep["groups"] if SA._key(g) == key)


def _d(diag: dict, key: tuple) -> dict:
    return next(g for g in diag["groups"] if SA._key(g) == key)


# ---- ① 附錄＝重新產生 ----

def test_check_mode_passes_on_repo():
    assert SA.main(["--check"]) == 0


def test_check_mode_detects_hand_edit(tmp_path):
    doc = PREREG.read_text(encoding="utf-8")
    bad = doc.replace("| stock/tpex/short/爻4/reweighted | 1,213 |", "| stock/tpex/short/爻4/reweighted | 1,214 |")
    assert bad != doc
    p = tmp_path / "prereg.md"
    p.write_text(bad, encoding="utf-8")
    assert SA.main(["--check", "--prereg", str(p)]) == 1
    assert p.read_text(encoding="utf-8") == bad  # --check 不寫入


def test_write_is_idempotent_and_single_marker(tmp_path):
    p = tmp_path / "prereg.md"
    p.write_text(PREREG.read_text(encoding="utf-8"), encoding="utf-8")
    assert SA.main(["--prereg", str(p)]) == 0
    once = p.read_text(encoding="utf-8")
    assert once == PREREG.read_text(encoding="utf-8")
    assert SA.main(["--prereg", str(p)]) == 0
    assert p.read_text(encoding="utf-8") == once
    assert once.count(SA.BEGIN) == 1 and once.count(SA.END) == 1


def test_real_report_key_numbers():
    """對真實報告的關鍵數字（2026-09-26 從 `abbda97` 的兩份 JSON 手查、獨立寫死；#68／#69 換模型後重跑）。"""
    block = _block(PREREG.read_text(encoding="utf-8"))
    assert "sha256 `7be9533afbe73326dae108a596467e224f990be10bd1159d0d63f7f3fdb40452`" in block
    assert "sha256 `37e8a9b3f5b0801f0e67f898440272162e660259083b22a753a838da21989a47`" in block
    assert "| `params_sha` | `8ca174ee8bc7`（兩份相同） |" in block
    assert "twse `p2-score-engine-2.01697576a7b0`／tpex `p2-score-engine-2.83b5c5dfdb23`" in block
    assert "落地 971 日（2021-01-04～2024-12-31）" in block
    assert "個股池內 2,195,451＋池外 3,048,507＋大盤 5,826＝5,249,784 列" in block
    assert "其中未知爻 165,602 個不進統計" in block
    assert "逸出 **0** 筆 → **PASS**" in block
    assert "108／144 組有觀測，越界 **0** 組 → **PASS**" in block
    assert "`market_index`／`reweighted`：30 組（爻 1、2、3、4、6）" in block
    assert "`market_index`／`full`：6 組（爻 5）" in block
    assert "須解釋 **6** 組" in block
    assert ("| stock/tpex/short/爻4/reweighted | 1,213 | 0.00% | 424 | 38.00% @ 50.000000 | 單一值佔比 > 20% | "
            f"{STATUS_66} |") in block
    assert ("| stock/tpex/short/爻1/reweighted | 10,921 | 32.82% | 378 | 22.25% @ 92.702703 | "
            f"達邊界比例 > 20%、單一值佔比 > 20% | {STATUS_66} |") in block
    assert ("| stock/tpex/swing/爻1/reweighted | 5,184 | 23.61% | 199 | 19.12% @ 92.702703 | 達邊界比例 > 20% | "
            f"{STATUS_70} |") in block
    assert ("| stock/twse/short/爻1/reweighted | 7,556 | 39.53% | 244 | 29.35% @ 92.702703 | "
            f"達邊界比例 > 20%、單一值佔比 > 20% | {STATUS_66} |") in block
    assert ("| stock/twse/swing/爻1/reweighted | 3,926 | 33.85% | 137 | 27.51% @ 92.702703 | "
            f"達邊界比例 > 20%、單一值佔比 > 20% | {STATUS_66} |") in block
    assert "共 16,213 列" in block and "最大 |差| 0.0" in block
    assert "耗時 669.1 s、RSS 峰值 436.4 MiB" in block
    assert "最大 |u| 為 134,974.249（stock/twse/short/爻1/reweighted 的 `A.revenue_accel`，邊界列）" in block
    assert "| stock/twse/short/爻1/reweighted | `A.revenue_yoy` | 邊界列 | 1,018 | -6.010 | 1.031 | 57,902.243 |" in block


def test_tail_section_states_rulings_67_68_not_pending():
    """末節寫的是 #67／#68 已裁定後的現況，不再是「待量測」。"""
    block = _block(PREREG.read_text(encoding="utf-8"))
    i = block.index("### 營收子指標的截斷位置 u（已裁定 #67／#68；不影響上列裁定）")
    tail = block[i:]
    assert "裁定 #67 不加最小基期門檻" in tail and "`denominator_zero`" in tail
    assert "待量測" not in tail and "未量測（使用者裁定先量影響面再決定" not in tail
    assert "### 待量測事項" not in block


def test_guard_pre68_model_version_refused(rep, diag):
    """#68 前的報告（`p2-score-engine-1.*`）不得生成：末節的 #67／#68 敘述對它不成立。"""
    rep["registry_model_versions"] = {"twse": "p2-score-engine-1.0bb386e9cf3b", "tpex": "p2-score-engine-1.8eb4f29fec3a"}
    with pytest.raises(SA.AppendixError, match="裁定 #68 之後"):
        B(rep, diag)


def test_numbers_follow_report(rep, diag):
    """數字由 JSON 算出：改報告，附錄跟著變（期待值獨立寫死）。改列數要同步改一個未知爻計數維持守恆。"""
    rep["sample"]["rows"]["market_index"] += 1
    rep["unknown"][0]["n"] += 6
    out = B(rep, diag)
    assert "大盤 5,827＝5,249,785 列" in out


# ---- ② 附錄之間互不吃掉 ----

def test_other_splices_preserve_appendix_c():
    doc = PREREG.read_text(encoding="utf-8")
    before = _block(doc)
    new_a = RT.splice_markdown(doc, "## 附錄 A：佔位\n")
    new_b = TA.splice(doc, "## 附錄 B：佔位\n")
    assert _block(new_a) == before and _block(new_b) == before
    assert doc.index(TA.END) < doc.index(SA.BEGIN)


def test_stats_splice_preserves_appendix_a_and_b():
    doc = PREREG.read_text(encoding="utf-8")
    new = SA.splice(doc, "## 附錄 C：佔位\n")
    for b, e in ((RT.BEGIN, RT.END), (TA.BEGIN, TA.END)):
        assert new[new.index(b):new.index(e)] == doc[doc.index(b):doc.index(e)]
    assert new[:new.index(SA.BEGIN)] == doc[:doc.index(SA.BEGIN)]


def test_splice_half_marker_refuses():
    with pytest.raises(SA.AppendixError):
        SA.splice(f"x\n{SA.BEGIN}\ny\n", "z\n")
    with pytest.raises(SA.AppendixError):
        SA.splice(f"x\n{SA.END}\n", "z\n")


# ---- ③ 守門：輸入與綁定 ----

def test_guard_schema(rep, diag):
    diag["schema"] = 2
    with pytest.raises(SA.AppendixError, match="schema"):
        B(rep, diag)


def test_guard_missing_field(rep, diag):
    del rep["registry_model_versions"]
    with pytest.raises(SA.AppendixError, match="缺欄位"):
        B(rep, diag)


def test_guard_spec_thresholds(rep, diag):
    """報告用的門檻不是規格的門檻：附錄寫的門檻就是錯的。"""
    rep["mode_share_max"] = 0.25
    with pytest.raises(SA.AppendixError, match="規格常數"):
        B(rep, diag)


def test_guard_sample_segment(rep, diag):
    rep["sample"]["start"] = diag["sample"]["start"] = "2021-01-02"
    with pytest.raises(SA.AppendixError, match="裁定 #64"):
        B(rep, diag)


def test_guard_diag_sample(rep, diag):
    diag["sample"]["end"] = "2025-12-31"
    with pytest.raises(SA.AppendixError, match="診斷的樣本段"):
        B(rep, diag)


def test_guard_data_version_binding(rep, diag):
    diag["data_version"] = "fm-20260912-01"
    with pytest.raises(SA.AppendixError, match="data_version"):
        B(rep, diag)


def test_guard_params_sha_binding(rep, diag):
    diag["params_sha"] = "000000000000"
    with pytest.raises(SA.AppendixError, match="params_sha"):
        B(rep, diag)


def test_guard_report_path_binding(rep, diag):
    diag["report"] = "runs/stats/report_2026-09-15.json"
    with pytest.raises(SA.AppendixError, match="診斷讀的報告"):
        B(rep, diag)


def test_guard_registry_markets(rep, diag):
    rep["registry_model_versions"].pop("tpex")
    with pytest.raises(SA.AppendixError, match="兩市場"):
        B(rep, diag)


def test_guard_row_conservation(rep, diag):
    rep["sample"]["rows"]["stock_in_pool"] += 1
    with pytest.raises(SA.AppendixError, match="未知爻"):
        B(rep, diag)


def test_guard_sample_days(rep, diag):
    rep["sample"]["first_day"] = "2020-12-31"
    with pytest.raises(SA.AppendixError, match="落地日"):
        B(rep, diag)


def test_guard_groups_total(rep, diag):
    rep["summary"]["groups_total"] -= 1
    with pytest.raises(SA.AppendixError, match="groups_total"):
        B(rep, diag)


def test_guard_duplicate_group(rep, diag):
    """重複一個未觀測組（n＝0，不牽動列數守恆），summary 的組數也跟著改成自洽。"""
    rep["groups"].append(copy.deepcopy(next(g for g in rep["groups"] if not g["n"])))
    rep["summary"]["groups_total"] += 1
    with pytest.raises(SA.AppendixError, match="重複組鍵"):
        B(rep, diag)


def test_guard_groups_observed(rep, diag):
    rep["summary"]["groups_observed"] += 1
    with pytest.raises(SA.AppendixError, match="groups_observed"):
        B(rep, diag)


# ---- ③ 守門：:712／:714 ----

def _fail_712(rep: dict) -> dict:
    """自洽的 :712 FAIL：某組極值與逸出數一起改、summary 同步。"""
    g = _g(rep, ("stock", "twse", "mid", "2", "full"))
    g["min"], g["escape_712"] = 7.0, 1
    rep["summary"]["escape_712"], rep["summary"]["pass_712"] = 1, False
    return rep


def test_guard_712_fail_refuses(rep, diag):
    with pytest.raises(SA.AppendixError, match=":712 FAIL"):
        B(_fail_712(rep), diag)


def test_guard_712_escape_sum(rep, diag):
    _g(rep, ("stock", "twse", "mid", "2", "full"))["escape_712"] = 1
    with pytest.raises(SA.AppendixError, match="加總"):
        B(rep, diag)


def test_guard_712_extremes_vs_escape(rep, diag):
    """逸出 0 卻有極值在範圍外：報告自相矛盾。"""
    _g(rep, ("stock", "twse", "mid", "2", "full"))["max"] = 95.0
    with pytest.raises(SA.AppendixError, match="全體極值"):
        B(rep, diag)


def test_guard_712_pass_flag_vs_escape(rep, diag):
    """逸出 0 卻標 FAIL：報告自相矛盾（與「FAIL 不產出」分開守，訊息不同）。"""
    rep["summary"]["pass_712"] = False
    with pytest.raises(SA.AppendixError, match="與逸出"):
        B(rep, diag)


def test_guard_712_boundary_tol_is_ok(rep, diag):
    """恰在 92.70＋0.01 內不算逸出（邊界）：改 max 為 92.709，登錄上界也放寬到同值以免 :714 越界。"""
    g = _g(rep, ("stock", "twse", "mid", "2", "full"))
    g["max"], g["registry"] = 92.709, [g["registry"][0], 92.709]
    B(rep, diag)


def _fail_714(rep: dict) -> dict:
    g = _g(rep, ("stock", "twse", "mid", "2", "full"))
    g["max"], g["within_registry_714"] = g["registry"][1] + 0.02, False
    rep["summary"]["violations_714"] = [{k: g[k] for k in ("scope", "market", "horizon", "line", "coverage")}]
    rep["summary"]["pass_714"] = False
    return rep


def test_guard_714_fail_refuses(rep, diag):
    with pytest.raises(SA.AppendixError, match=":714 步驟 4 FAIL"):
        B(_fail_714(rep), diag)


def test_guard_714_flag_vs_recompute(rep, diag):
    _g(rep, ("stock", "twse", "mid", "2", "full"))["within_registry_714"] = False
    with pytest.raises(SA.AppendixError, match="within_registry_714"):
        B(rep, diag)


def test_guard_714_pass_flag_vs_violations(rep, diag):
    rep["summary"]["pass_714"] = False
    with pytest.raises(SA.AppendixError, match="與越界"):
        B(rep, diag)


def test_guard_714_tolerance_is_ok(rep, diag):
    """極值超出登錄端點但在 ±0.01 內不算越界（邊界）；登錄下界 7.2973 比 :712 下界 7.29 高，改 0.005 不牽動 :712。"""
    g = _g(rep, ("stock", "twse", "mid", "1", "full"))
    g["min"] = g["registry"][0] - 0.005
    B(rep, diag)


def test_guard_714_violation_list(rep, diag):
    rep["summary"]["violations_714"] = [{"scope": "stock", "market": "twse", "horizon": "mid", "line": "2", "coverage": "full"}]
    with pytest.raises(SA.AppendixError, match="violations_714"):
        B(rep, diag)


# ---- ③ 守門：:716 與確認映射（雙向） ----

def test_guard_716_group_reasons(rep, diag):
    _g(rep, ("stock", "twse", "mid", "2", "full"))["explain_716"] = [SA.R_MODE]
    with pytest.raises(SA.AppendixError, match="explain_716"):
        B(rep, diag)


def test_guard_716_summary(rep, diag):
    rep["summary"]["explain_716"] = rep["summary"]["explain_716"][1:]
    with pytest.raises(SA.AppendixError, match="summary.explain_716"):
        B(rep, diag)


def test_guard_unconfirmed_flagged_group(rep, diag):
    """報告多一組須解釋（自洽：原值、組內理由、summary 一起改）→ 未裁定，中止。"""
    g = _g(rep, ("stock", "twse", "mid", "2", "full"))
    g["mode_share"], g["explain_716"] = 0.3, [SA.R_MODE]
    rep["summary"]["explain_716"].append({**{k: g[k] for k in ("scope", "market", "horizon", "line", "coverage")},
                                          "reasons": [SA.R_MODE]})
    with pytest.raises(SA.AppendixError, match="未裁定"):
        B(rep, diag)


def test_guard_confirmed_group_missing_from_report(rep, diag, monkeypatch):
    """映射多一組（報告裡沒有）→ 中止。"""
    conf = dict(SA.CONFIRMED)
    conf[("stock", "twse", "mid", "2", "full")] = "#66"
    monkeypatch.setattr(SA, "CONFIRMED", conf)
    with pytest.raises(SA.AppendixError, match="報告中不存在"):
        B(rep, diag)


def test_guard_explanation_set(rep, diag, monkeypatch):
    exp = dict(SA.EXPLANATION)
    del exp[K5]
    monkeypatch.setattr(SA, "EXPLANATION", exp)
    with pytest.raises(SA.AppendixError, match="EXPLANATION"):
        B(rep, diag)


def test_recompute_716_boundaries():
    """三個門檻皆嚴格：恰 20% 不觸發、相異值恰 10 不觸發（期待值本檔寫死）。"""
    base = {"share_at_boundary": 0.20, "distinct": 10, "mode_share": 0.20}
    assert SA.recompute_716(base) == []
    assert SA.recompute_716({**base, "share_at_boundary": 0.2001}) == [SA.R_BOUND]
    assert SA.recompute_716({**base, "distinct": 9}) == [SA.R_DISTINCT]
    assert SA.recompute_716({**base, "mode_share": 0.2001}) == [SA.R_MODE]
    assert SA.recompute_716({**base, "share_at_boundary": None, "mode_share": 0.5}) == [SA.R_MODE]


# ---- ③ 守門：診斷 ----

def test_guard_diag_group_set(rep, diag):
    diag["groups"] = [g for g in diag["groups"] if SA._key(g) != K4]
    with pytest.raises(SA.AppendixError, match="診斷的組"):
        B(rep, diag)


def test_guard_diag_reasons(rep, diag):
    _d(diag, K4)["reasons"] = [SA.R_BOUND]
    with pytest.raises(SA.AppendixError, match="診斷的理由"):
        B(rep, diag)


def test_guard_diag_duplicate_group(rep, diag):
    diag["groups"].append(copy.deepcopy(_d(diag, K4)))
    diag["parity"]["rows"] += _d(diag, K4)["n_sampled"]
    with pytest.raises(SA.AppendixError, match="重複組鍵"):
        B(rep, diag)


def test_guard_diag_registry(rep, diag):
    _d(diag, K5)["registry"] = [7.0, 93.0]
    with pytest.raises(SA.AppendixError, match="登錄區間 ≠"):
        B(rep, diag)


def test_guard_diag_population(rep, diag):
    _d(diag, K5)["population"]["mode_share"] += 1e-6
    with pytest.raises(SA.AppendixError, match="mode_share"):
        B(rep, diag)


def test_guard_diag_n(rep, diag):
    _d(diag, K5)["population"]["n"] += 1
    with pytest.raises(SA.AppendixError, match="診斷母體"):
        B(rep, diag)


def test_guard_parity_diff(rep, diag):
    diag["parity"]["max_abs_diff"] = 1e-6
    with pytest.raises(SA.AppendixError, match="parity 最大差"):
        B(rep, diag)


def test_guard_parity_rows(rep, diag):
    diag["parity"]["rows"] -= 1
    with pytest.raises(SA.AppendixError, match="parity 列數"):
        B(rep, diag)


def test_guard_parity_tol(rep, diag):
    diag["parity"]["tol"] = 1e-6
    with pytest.raises(SA.AppendixError, match="寬於"):
        B(rep, diag)


def test_guard_pattern_share_sum(rep, diag):
    _d(diag, K4)["patterns"][0]["est_share"] -= 0.01
    with pytest.raises(SA.AppendixError, match="佔母體比例加總"):
        B(rep, diag)


def test_guard_pattern_sample_sum(rep, diag):
    _d(diag, K4)["n_sampled"] += 1
    with pytest.raises(SA.AppendixError, match="抽樣筆數加總"):
        B(rep, diag)


def test_guard_signature_share_sum(rep, diag):
    _d(diag, K1_TWSE_W)["boundary_rows"]["signatures"][0]["share"] -= 0.01
    with pytest.raises(SA.AppendixError, match="簽名比例加總"):
        B(rep, diag)


def test_guard_signature_presence(rep, diag):
    """母體有邊界列、診斷卻沒有邊界簽名（或反之）。"""
    _d(diag, K1_TWSE_W)["boundary_rows"]["signatures"] = []
    with pytest.raises(SA.AppendixError, match="簽名有無"):
        B(rep, diag)


# ---- ③ 守門：三種說明型態 ----

def test_guard_single_sub_pattern(rep, diag):
    """初爻組出現第二種族缺值型態：「100% 為 A 在場」不成立。比例重新分配以維持加總＝1。"""
    d = _d(diag, K1_TWSE_S)
    p = copy.deepcopy(d["patterns"][0])
    p["pattern"], p["est_share"], p["n_sample"] = "A 缺:missing", 0.01, {"boundary": 0, "boundary_mode": 0, "mode": 0, "rest": 0}
    d["patterns"][0]["est_share"] -= 0.01
    d["patterns"].append(p)
    with pytest.raises(SA.AppendixError, match="100% 的『A 在場』"):
        B(rep, diag)


def test_guard_single_sub_signature_two_present(rep, diag):
    s = _d(diag, K1_TWSE_S)["boundary_rows"]["signatures"][0]
    s["key"] = "A{revenue_yoy clip↑｜revenue_accel clip↑}"
    with pytest.raises(SA.AppendixError, match="一個缺、另一個在 S 端點"):
        B(rep, diag)


def test_guard_single_sub_signature_not_endpoint(rep, diag):
    s = _d(diag, K1_TWSE_S)["mode_rows"]["signatures"][0]
    s["key"] = "A{revenue_yoy=80.0000｜revenue_accel 缺:missing}"
    with pytest.raises(SA.AppendixError, match="一個缺、另一個在 S 端點"):
        B(rep, diag)


def test_guard_single_sub_other_family(rep, diag):
    s = _d(diag, K1_TWSE_S)["boundary_rows"]["signatures"][0]
    s["key"] = s["key"] + "；B 缺:missing"
    with pytest.raises(SA.AppendixError, match="不是只有族 A"):
        B(rep, diag)


def test_guard_single_sub_mode_not_at_endpoint(rep, diag):
    """報告與診斷一起改（兩者須一致），只讓「最常出現值即登錄端點」不成立。"""
    _g(rep, K1_TWSE_S)["mode_value"] = _d(diag, K1_TWSE_S)["population"]["mode_value"] = 90.0
    with pytest.raises(SA.AppendixError, match="不在登錄區間任一端點"):
        B(rep, diag)


def test_guard_single_sub_mode_rows_subset(rep, diag):
    _d(diag, K1_TWSE_S)["strata"]["mode"]["population"] = 5
    with pytest.raises(SA.AppendixError, match="M 層"):
        B(rep, diag)


def test_guard_single_sub_registry_full_range(rep, diag):
    """登錄區間不是 S 全幅：「子指標在端點 ⇒ 爻在登錄端點」不成立。報告與診斷的登錄區間一起改，且 min 仍在區間內。"""
    g, d = _g(rep, K1_TWSE_W), _d(diag, K1_TWSE_W)
    reg = [g["registry"][0] - 0.5, g["registry"][1]]
    g["registry"] = d["registry"] = reg
    with pytest.raises(SA.AppendixError, match="S 全幅"):
        B(rep, diag)


def test_guard_explainer_applicability(rep, diag, monkeypatch):
    """把初爻組誤標成 equal_subs：該說明只說明單一值，初爻組還有達邊界理由，必須中止。"""
    exp = dict(SA.EXPLANATION)
    exp[K1_TPEX_S] = "equal_subs"
    monkeypatch.setattr(SA, "EXPLANATION", exp)
    with pytest.raises(SA.AppendixError, match="只說明單一值"):
        B(rep, diag)


def test_guard_single_sub_applicability(rep, diag, monkeypatch):
    exp = dict(SA.EXPLANATION)
    exp[K4] = "single_sub"
    monkeypatch.setattr(SA, "EXPLANATION", exp)
    with pytest.raises(SA.AppendixError, match="只適用於短線／波段個股初爻"):
        B(rep, diag)


def test_guard_equal_subs_value(rep, diag):
    s = _d(diag, K4)["mode_rows"]["signatures"][1]
    s["key"] = s["key"].replace("C{continuation=50.0000}", "C{continuation=49.0000}")
    with pytest.raises(SA.AppendixError, match="不全等於"):
        B(rep, diag)


def test_guard_equal_subs_reweighted(rep, diag):
    s = _d(diag, K4)["mode_rows"]["signatures"][0]
    s["key"] = "A{volume_scenario=50.0000}；B{close_position=50.0000}；C{continuation=50.0000}"
    with pytest.raises(SA.AppendixError, match="沒有缺席的族"):
        B(rep, diag)


def test_guard_fixed_pos_applicability(rep, diag, monkeypatch):
    exp = dict(SA.EXPLANATION)
    exp[K1_TWSE_W] = "fixed_pos"
    monkeypatch.setattr(SA, "EXPLANATION", exp)
    with pytest.raises(SA.AppendixError, match="「截斷位置固定」只說明單一值"):
        B(rep, diag)


def test_guard_fixed_pos_no_subs(rep, diag):
    _d(diag, K5)["mode_rows"]["subs"] = {}
    with pytest.raises(SA.AppendixError, match="沒有在場子指標"):
        B(rep, diag)


def test_guard_fixed_pos_u(rep, diag):
    _d(diag, K5)["mode_rows"]["subs"]["A.foreign_strength_long"]["u_max"] = 0.1
    with pytest.raises(SA.AppendixError, match="不固定"):
        B(rep, diag)


def test_guard_fixed_pos_cd(rep, diag):
    _d(diag, K5)["mode_rows"]["subs"]["C.foreign_persistence"]["c"] = "7 個相異值（滾動 c）"
    with pytest.raises(SA.AppendixError, match="c／d 不是單一值"):
        B(rep, diag)


def test_guard_fixed_pos_single_signature(rep, diag):
    sigs = _d(diag, K5)["mode_rows"]["signatures"]
    extra = copy.deepcopy(sigs[0])
    extra["key"], extra["share"] = extra["key"].replace("E 缺:missing", "E 缺:denominator_zero"), 0.1
    sigs[0]["share"] -= 0.1
    sigs.append(extra)
    with pytest.raises(SA.AppendixError, match="不只一種簽名"):
        B(rep, diag)


def test_parse_signature():
    """簽名解析（格式同 `score_diag716`；期待值本檔寫死）。"""
    assert SA.parse_signature("A{revenue_yoy clip↑｜revenue_accel 缺:missing}") == [
        ("A", [("revenue_yoy", "clip↑"), ("revenue_accel", "缺:missing")])]
    assert SA.parse_signature("A{volume_scenario=50.0000}；B 缺:denominator_zero；C{foreign_persistence 端點↓(未clip)}") == [
        ("A", [("volume_scenario", "=50.0000")]), ("B", "缺:denominator_zero"),
        ("C", [("foreign_persistence", "端點↓(未clip)")])]
    with pytest.raises(SA.AppendixError, match="無法解析"):
        SA.parse_signature("A 在場")
    with pytest.raises(SA.AppendixError, match="無法解析"):
        SA.parse_signature("A{revenue_yoy}")


# ---- ④ 確認狀態 ----

def _section(block: str, key: tuple) -> str:
    i = block.index(f"#### {SA._kname(key)}")
    j = block.find("\n###", i + 1)
    return block[i:] if j < 0 else block[i:j]


def test_confirmation_bound_per_group():
    block = _block(PREREG.read_text(encoding="utf-8"))
    assert len(EXPECT_STATUS) == 6
    for k, status in EXPECT_STATUS.items():
        sec = _section(block, k)
        assert f"**{status}**" in sec and "待使用者確認" not in sec, k
        row = next(ln for ln in block.splitlines() if ln.startswith(f"| {SA._kname(k)} |"))
        assert row.endswith(f"| {status} |"), k
    assert block.count("#70") == 3  # 標題＋表列＋小節末行；其餘 5 組都是 #66
    assert "待使用者確認" not in block


def test_guard_ruling_without_note(rep, diag, monkeypatch):
    """`CONFIRMED` 出現的裁定號必須在 `RULING_NOTE` 有註記，否則中止（不得印出沒有日期的裁定）。"""
    conf = dict(SA.CONFIRMED)
    conf[K4] = "#71"
    monkeypatch.setattr(SA, "CONFIRMED", conf)
    with pytest.raises(SA.AppendixError, match="RULING_NOTE"):
        B(rep, diag)


def test_unconfirmed_changes_only_that_group(rep, diag, monkeypatch):
    conf = dict(SA.CONFIRMED)
    conf[K4] = None
    monkeypatch.setattr(SA, "CONFIRMED", conf)
    out = B(rep, diag)
    assert "待使用者確認" in _section(out, K4)
    assert out.count("**待使用者確認（凍結前必須補齊）**") == 1
    assert f"**{STATUS_66}**" in _section(out, K5)
    assert f"**{STATUS_70}**" in _section(out, K1_TPEX_W)


def test_explanations_bound_per_group():
    """三種說明段落落在正確的組（對調說明型態後只數次數會全綠）。"""
    block = _block(PREREG.read_text(encoding="utf-8"))
    for k in (K1_TPEX_S, K1_TWSE_S, K1_TWSE_W, K1_TPEX_W):
        assert "族 A 的兩個子指標（`revenue_yoy`、`revenue_accel`）缺一個" in _section(block, k)
    assert "在場子指標的值都等於 50.0000" in _section(block, K4)
    assert "u＝0：`foreign_strength_long`、`foreign_strength_short`、`trust_strength_long`、`trust_strength_short`；" \
           "u＝−1：`foreign_persistence`" in _section(block, K5)


# ---- ⑤ rc 語意 ----

def _tmp_reports(tmp_path: Path, rep: dict, diag: dict) -> tuple[Path, Path, Path]:
    """診斷綁定報告的檔名，所以暫存報告沿用原檔名。"""
    rp, dp = tmp_path / REPORT.name, tmp_path / DIAG.name
    rp.write_text(json.dumps(rep), encoding="utf-8")
    dp.write_text(json.dumps(diag), encoding="utf-8")
    p = tmp_path / "prereg.md"
    shutil.copy(PREREG, p)
    return rp, dp, p


def test_main_rc2_on_712_fail(rep, diag, tmp_path):
    rp, dp, p = _tmp_reports(tmp_path, _fail_712(rep), diag)
    before = p.read_text(encoding="utf-8")
    assert SA.main(["--report", str(rp), "--diag", str(dp), "--prereg", str(p)]) == 2
    assert p.read_text(encoding="utf-8") == before


def test_main_rc2_on_714_fail_check_mode(rep, diag, tmp_path):
    """--check 下守門中止也是 rc=2，不得落到 rc=1（附錄過期）。"""
    rp, dp, p = _tmp_reports(tmp_path, _fail_714(rep), diag)
    assert SA.main(["--check", "--report", str(rp), "--diag", str(dp), "--prereg", str(p)]) == 2


def test_main_tmp_copy_is_stale_not_crash(rep, diag, tmp_path):
    """同內容、重新序列化的暫存報告（repo 外路徑、sha256 不同）：守門全過、附錄的路徑與 sha256 變了 → rc=1，不是 2。"""
    rp, dp, p = _tmp_reports(tmp_path, rep, diag)
    assert SA.main(["--check", "--report", str(rp), "--diag", str(dp), "--prereg", str(p)]) == 1


def test_main_crash_is_rc2_not_rc1(monkeypatch):
    for exc in (TypeError, KeyError, ZeroDivisionError, RuntimeError):
        def boom(*a, exc=exc):
            raise exc("x")
        monkeypatch.setattr(SA, "build", boom)
        assert SA.main(["--check"]) == 2


def test_main_missing_file_rc2(tmp_path):
    assert SA.main(["--check", "--diag", str(tmp_path / "none.json")]) == 2
