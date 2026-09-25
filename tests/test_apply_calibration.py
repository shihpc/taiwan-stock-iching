"""d 校準寫回 `params.py` 的驗收（`docs/P3-CALIBRATION.md` §8.3 H1～H8）。免 token、免網路。

**規則不寫死數字**：H1／H4／H6 一律從 `runs/calib/d_report_2023-06-30.json` 重算裁定 #54／#55／#56 的採用值，
再跟 `build_params()` 的實際 `Param.d` 逐位比；寫死的只有 H3 的指紋（那正是「下次誰再動 d 就會紅」的用意）
與 H2 的基準 commit。

H2 的基準**釘死在 `05f4120`**（本批動手前的 HEAD），不是 `HEAD`——`HEAD` 在 commit 之後就是新版自己、
會變成自我比對，證不出「只有 d 變」（同 `tests/test_calibrate.py` 那支 `_vs_worktree_head` 的效力邊界）。
"""
from __future__ import annotations

import dataclasses
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

from conftest import ROOT
from iching.score.calibrated import (CALIBRATED_D, CALIBRATED_DISTANCE_D, CALIBRATED_SLOPE_D,
                                     CALIBRATION_META)
from iching.score.params import MARKETS, build_params

sys.path.insert(0, str(ROOT / "scripts"))
import apply_calibration as AC  # noqa: E402
from export_dataset import expected_params_sha  # noqa: E402

REPORT = ROOT / "runs" / "calib" / "d_report_2023-06-30.json"

# H2 的基準（本批動手前的 HEAD）——**刻意不是 HEAD**，理由見檔頭
BASE_SHA = "05f4120"

# H3：現行指紋，寫死（下次誰再動 d／任一 Param 欄位／任一 Rules 欄位／RULES_VERSION 就會紅）
# 沿革：校準前 a6a3f35cd1f0 →（d 校準，PR #50）b5bb5f00d91c →（§17 coverage 分母）c7385e78cb9f
#   →（裁定 #68：營收分母 ≤ 0 視為缺值、RULES_VERSION 升 -2，docs/P3-CALIBRATION.md §31）現值。
# 中段那組 twse b45aa4dac4dc／tpex 313f6b5dd3c1／payload b5bb5f00d91c 留在這裡當歷史對照，
# 因為 runs/calib 的報告與 CALIBRATION_META 記的是那個時點；#68 前那組見 PRE68_*。
NEW_MODEL_VERSION = {"twse": "p2-score-engine-2.8f81122a37ae", "tpex": "p2-score-engine-2.dfa55ced4a96"}
NEW_REPLAY_PARAMS_SHA = "6bd41e811f49"        # build_params_payload（window=320、AdvTracker 預設、fundamentals=True）
PRE68_MODEL_VERSION = {"twse": "p2-score-engine-1.0bb386e9cf3b", "tpex": "p2-score-engine-1.8eb4f29fec3a"}
PRE68_PARAMS_SHA = "c7385e78cb9f"             # runs/stats／runs/revbase／runs/t717 的報告記的是這個
CALIB_ERA_MODEL_VERSION = {"twse": "p2-score-engine-1.b45aa4dac4dc", "tpex": "p2-score-engine-1.313f6b5dd3c1"}
CALIB_ERA_PARAMS_SHA = "b5bb5f00d91c"
OLD_MODEL_SHA = {"twse": "f7b0f6e1d71b", "tpex": "e7581159c2e2"}
OLD_REPLAY_PARAMS_SHA = "a6a3f35cd1f0"        # 校準前，＝報告的 params_sha

GATE_PCT = 15.0
NEEDS_D = ("calibrate", "distance", "persistence")


@pytest.fixture(scope="module")
def report() -> dict:
    return json.loads(REPORT.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def ps() -> dict:
    return {m: build_params(m) for m in MARKETS}


def param_key(row: dict) -> tuple[str, str, str, str, str]:
    return (row["scope"], row["horizon"], row["line"], row["family"], row["indicator_id"])


# ---------------------------------------------------------------------------
# 規則的**獨立**重算（不呼叫 apply_calibration，免得拿它自己驗自己）
# ---------------------------------------------------------------------------
def expected_tables(report: dict, *, zero_threshold: float = 0.50, slot_agg=max) -> tuple[dict, dict, dict]:
    """回傳 `(direct, distance, slope)`，鍵形狀同 `calibrated.py`。

    `zero_threshold`／`slot_agg` 只為突變測試而參數化：預設值就是裁定 #56／#55。
    """
    direct: dict[tuple[str, str, str, str], float] = {}
    distance: dict[str, dict[int, float]] = {}
    slope: dict[str, dict[str, dict[int, float]]] = {}
    slot: dict[tuple[str, int], list[float]] = {}
    for row in report["rows"]:
        cat, n = row["category"], int(row["n"])
        if cat not in NEEDS_D or n == 0:
            continue
        market, sn, tbl = row["market"], row.get("shared_d_n"), row.get("shared_d_table")
        if cat == "distance":
            slot.setdefault((market, int(sn)), []).append(float(row["d_new"]))
            continue
        if cat == "persistence":
            win = row["window"] if not isinstance(row["window"], list) else row["window"][0]
            d = (int(win) / 2.0) / 3.0                      # 原始值域上界 ÷ 3（spec P1-B1-market.md:48 5a）
        elif float(row["z_zero"]) >= zero_threshold:
            d = float(row["d_nonzero"])                     # 裁定 #56：非零樣本 p85 ÷ 3
        else:
            d = float(row["d_new"])                         # p85 ÷ 3
        if tbl in ("market_slope_d", "stock_slope_d"):
            slope.setdefault(tbl, {}).setdefault(market, {})[int(sn)] = d
        else:
            direct[(market, row["scope"], row["indicator_id"], row["horizon"])] = d
    for (market, sn), vals in slot.items():
        distance.setdefault(market, {})[sn] = slot_agg(vals)
    return direct, distance, slope


def actual_d(ps: dict, row: dict) -> float:
    """`build_params` 實際給該鍵的 d（距離／斜率走共用表，語意同 `docs/P3-CALIBRATION.md` §8.3 H1）。"""
    p = ps[row["market"]]
    tbl = row.get("shared_d_table")
    if tbl == "distance_d":
        return p.distance_d[int(row["shared_d_n"])]
    if tbl == "market_slope_d":
        return p.market_slope_d[int(row["shared_d_n"])]
    if tbl == "stock_slope_d":
        return p.stock_slope_d[int(row["shared_d_n"])]
    return p.get(*param_key(row)).d


# ---------------------------------------------------------------------------
# H1：雙向逐項
# ---------------------------------------------------------------------------
def test_h1_every_report_key_gets_the_rule_value(report, ps):
    direct, distance, slope = expected_tables(report)
    seen = 0
    for row in report["rows"]:
        if row["category"] not in NEEDS_D or int(row["n"]) == 0:
            continue
        tbl = row.get("shared_d_table")
        if tbl == "distance_d":
            want = distance[row["market"]][int(row["shared_d_n"])]
        elif tbl in ("market_slope_d", "stock_slope_d"):
            want = slope[tbl][row["market"]][int(row["shared_d_n"])]
        else:
            want = direct[(row["market"], row["scope"], row["indicator_id"], row["horizon"])]
        got = actual_d(ps, row)
        assert got == want, f"{row['key']}：build_params 給 {got!r}，規則算出 {want!r}"
        seen += 1
    assert seen == 172 + 30 + 12 - 2, seen      # calibrate 172＋distance 30＋persistence 12，扣掉 n=0 的 equity_qoq 2 鍵


def test_h1_reverse_tables_have_no_extra_keys(report):
    direct, distance, slope = expected_tables(report)
    assert CALIBRATED_D == direct
    assert CALIBRATED_DISTANCE_D == distance
    assert CALIBRATED_SLOPE_D == slope


def test_h1_distance_slot_is_the_max_of_its_own_keys(report, ps):
    """裁定 #55 的方向性：採用值＝該格最大，因此**不小於**該格任一鍵自己的 `p85/3`。"""
    for row in report["rows"]:
        if row["category"] != "distance" or int(row["n"]) == 0:
            continue
        assert ps[row["market"]].distance_d[int(row["shared_d_n"])] >= float(row["d_new"])


def test_h1_zero_inflation_hits_exactly_the_trust_strength_keys(report):
    z = sorted(r["key"] for r in report["rows"]
               if r["category"] in NEEDS_D and int(r["n"]) and float(r["z_zero"]) >= 0.50)
    assert len(z) == 12 and all("trust_strength_" in k for k in z), z
    assert len(CALIBRATION_META["zero_inflation_keys"]) == 12


# ---------------------------------------------------------------------------
# H2：只有 d 變（基準釘死 05f4120）
# ---------------------------------------------------------------------------
def _old_build_params(tmp_path: Path):
    """把 `05f4120` 版 `params.py`＋`transform.py` 放進一個臨時套件再 import（相對 import 需要套件）。"""
    pkg = tmp_path / "oldscore"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    for name in ("transform.py", "params.py"):
        r = subprocess.run(["git", "show", f"{BASE_SHA}:src/iching/score/{name}"],
                           cwd=ROOT, capture_output=True, text=True)
        if r.returncode != 0:
            pytest.skip(f"取不到 {BASE_SHA}:src/iching/score/{name}（非 git checkout？）")
        (pkg / name).write_text(r.stdout, encoding="utf-8")
    sys.path.insert(0, str(tmp_path))
    try:
        spec = importlib.util.spec_from_file_location("oldscore.params", pkg / "params.py")
        mod = importlib.util.module_from_spec(spec)
        sys.modules["oldscore.params"] = mod
        importlib.import_module("oldscore")
        spec.loader.exec_module(mod)
    finally:
        sys.path.remove(str(tmp_path))
    return mod


def test_h2_only_d_and_paramset_calibrated_changed(tmp_path, ps):
    old = _old_build_params(tmp_path)
    for m in MARKETS:
        o, nw = old.build_params(m), ps[m]
        assert set(o.params) == set(nw.params)
        for k in sorted(o.params):
            a, b = dataclasses.asdict(o.params[k]), dataclasses.asdict(nw.params[k])
            for f in sorted(a):
                if f == "d":
                    continue
                assert a[f] == b[f], f"{m}|{k}|{f}：{a[f]!r} → {b[f]!r}（本批只許改 d）"
        assert o.family_weights == nw.family_weights and o.line_weights == nw.line_weights
        # H2 守的是「d 校準那一批只改 d」。§17（2026-09-21）在其後新增了 coverage_excludes_insufficient，
        # 故只放行這一個**新增欄位**，其餘欄位仍逐欄比對——任何別的 Rules 變動照樣紅。
        o_rules, n_rules = dataclasses.asdict(o.rules), dataclasses.asdict(nw.rules)
        assert set(n_rules) - set(o_rules) == {"coverage_excludes_insufficient"}
        assert set(o_rules) - set(n_rules) == set()
        for f in sorted(o_rules):
            assert o_rules[f] == n_rules[f], f"{m}|Rules.{f}：{o_rules[f]!r} → {n_rules[f]!r}"
        assert set(o.distance_d) == set(nw.distance_d) and set(o.market_slope_d) == set(nw.market_slope_d)
        assert set(o.stock_slope_d) == set(nw.stock_slope_d)
        assert o.calibrated is False and nw.calibrated is True
        assert old.RULES_VERSION == "p2-score-engine-1"      # RULES_VERSION 不 bump（規則沒變，只有 d）


# ---------------------------------------------------------------------------
# H3：指紋如預期變
# ---------------------------------------------------------------------------
def test_h3_fingerprints_changed_to_pinned_values(ps):
    for m in MARKETS:
        assert ps[m].model_version() == NEW_MODEL_VERSION[m]
        assert OLD_MODEL_SHA[m] not in ps[m].model_version()
    assert ps["twse"].model_version() != ps["tpex"].model_version()
    want = {"window": 320, "adv_window": 60, "adv_threshold": 30000000.0, "fundamentals": True}
    assert expected_params_sha(want)[:12] == NEW_REPLAY_PARAMS_SHA
    assert expected_params_sha(want)[:12] != OLD_REPLAY_PARAMS_SHA
    assert CALIBRATION_META["params_sha_before"] == OLD_REPLAY_PARAMS_SHA
    # §17 之後指紋必須再變一次；等於校準當時的值＝旗標沒進指紋
    assert expected_params_sha(want)[:12] != CALIB_ERA_PARAMS_SHA
    for mm in MARKETS:
        assert ps[mm].model_version() != CALIB_ERA_MODEL_VERSION[mm]
    # 裁定 #68 之後必須再變一次；等於 #68 前的值＝RULES_VERSION 沒升（缺值規則不經任何 Param／Rules 欄位）
    assert expected_params_sha(want)[:12] != PRE68_PARAMS_SHA
    for mm in MARKETS:
        assert ps[mm].model_version() != PRE68_MODEL_VERSION[mm]
        assert ps[mm].model_version().startswith("p2-score-engine-2.")


# ---------------------------------------------------------------------------
# H4：未校準鍵維持起點值（逐鍵）
# ---------------------------------------------------------------------------
def test_h4_uncalibrated_keys_keep_start_values(tmp_path, report, ps):
    old = _old_build_params(tmp_path)
    olds = {m: old.build_params(m) for m in MARKETS}
    n_na = n_zero = 0
    for row in report["rows"]:
        cat, n = row["category"], int(row["n"])
        if cat in NEEDS_D and n:
            continue
        k, m = param_key(row), row["market"]
        assert ps[m].get(*k).d == olds[m].get(*k).d, f"{row['key']} 不該被校準"
        assert (m, row["scope"], row["indicator_id"], row["horizon"]) not in CALIBRATED_D
        n_na += cat == "not_applicable"
        n_zero += cat != "not_applicable" and n == 0
    assert n_na == 90 and n_zero == 2                      # n/a 90 鍵；n=0 的 calibrate＝equity_qoq 2 鍵
    zero_keys = sorted(r["indicator_id"] for r in report["rows"] if int(r["n"]) == 0)
    assert zero_keys.count("vix_phist_rev") == 6 and zero_keys.count("equity_qoq") == 2
    assert len(CALIBRATION_META["not_calibrated"]) == 92


# ---------------------------------------------------------------------------
# H5：決定性
# ---------------------------------------------------------------------------
def test_h5_generator_is_deterministic(tmp_path):
    a, b = tmp_path / "a.py", tmp_path / "b.py"
    assert AC.main(["--report", str(REPORT), "--out", str(a), "--quiet"]) == 0
    assert AC.main(["--report", str(REPORT), "--out", str(b), "--quiet"]) == 0
    assert a.read_bytes() == b.read_bytes()


def test_h5_committed_file_equals_regenerated():
    assert AC.main(["--report", str(REPORT), "--quiet", "--check"]) == 0


def test_h5_report_sha256_recorded_matches_file():
    import hashlib
    assert CALIBRATION_META["report_sha256"] == hashlib.sha256(REPORT.read_bytes()).hexdigest()
    assert CALIBRATION_META["source_commit"] == "4f2f378"
    assert CALIBRATION_META["source_report"] == "runs/calib/d_report_2023-06-30.json"


# ---------------------------------------------------------------------------
# H6：閘門
# ---------------------------------------------------------------------------
def test_h6_expected_clip_within_gate(report):
    """採用 d 下的預期截斷 ≤ 15%＋100/n；不成立者必須在 `CALIBRATION_META["gate_exceptions"]`。

    `calibrate` 兩支規則的截斷比例報告直接有（`clip_new_pct`／`clip_nonzero_pct`，都以**全體樣本**為分母）。
    `distance` 採用值 ≥ 自身 `d_new`（上一支測試守），截斷單調不增，故以 `clip_new_pct` 當上界。
    `persistence` 的 `3d` 恰等於值域上界，而截斷是嚴格大於，故必為 0——以報告的 `x_min`／`x_max` 實證。
    """
    exceptions = set(CALIBRATION_META["gate_exceptions"])
    checked = 0
    for row in report["rows"]:
        cat, n = row["category"], int(row["n"])
        if cat not in NEEDS_D or n == 0:
            continue
        gate = GATE_PCT + 100.0 / n
        if cat == "persistence":
            win = row["window"] if not isinstance(row["window"], list) else row["window"][0]
            assert max(abs(row["x_min"]), abs(row["x_max"])) <= 3.0 * ((int(win) / 2.0) / 3.0) + 1e-12
            checked += 1
            continue
        if cat == "distance":
            clip = float(row["clip_new_pct"])
        elif float(row["z_zero"]) >= 0.50:
            clip = float(row["clip_nonzero_pct"])
        else:
            clip = float(row["clip_new_pct"])
        assert clip <= gate or any(row["key"] in e for e in exceptions), f"{row['key']} 截斷 {clip} > {gate}"
        checked += 1
    assert checked == 212 and exceptions == set()


# ---------------------------------------------------------------------------
# H8：下游守門在新指紋下的行為
# ---------------------------------------------------------------------------
def test_h8_downstream_guards_reject_old_artifacts(tmp_path):
    from iching import replay_state as RS
    from iching.run_common import ReplayDriverError, check_snapshot_meta
    want = {"window": 320, "adv_window": 60, "adv_threshold": 30000000.0, "fundamentals": True}
    sha = expected_params_sha(want)
    assert sha[:12] == NEW_REPLAY_PARAMS_SHA
    cross = RS.CrossDayState()
    cross.meta = {"window": 320, "params_sha": OLD_REPLAY_PARAMS_SHA}     # 校準前的 scores.db／cross.json
    with pytest.raises(ReplayDriverError):
        check_snapshot_meta(cross, window=320, params_sha=sha, path=tmp_path / "state.json")
    cross.meta = {"window": 320, "params_sha": sha}
    check_snapshot_meta(cross, window=320, params_sha=sha, path=tmp_path / "state.json")


# ---------------------------------------------------------------------------
# 突變：規則寫錯時，上面的 H1 必須紅
# ---------------------------------------------------------------------------
def test_mutation_zero_inflation_reverted_to_plain_p85_breaks_h1(report, ps):
    """突變①：把裁定 #56 改回「一律 p85/3」（門檻拉到 2.0＝永不觸發）。"""
    direct, _, _ = expected_tables(report, zero_threshold=2.0)
    bad = [k for k, v in direct.items() if CALIBRATED_D[k] != v]
    assert len(bad) == 12 and all("trust_strength_" in k[2] for k in bad), bad
    with pytest.raises(AssertionError):
        for row in report["rows"]:
            if row["category"] == "calibrate" and int(row["n"]) and not row.get("shared_d_table"):
                k = (row["market"], row["scope"], row["indicator_id"], row["horizon"])
                assert actual_d(ps, row) == direct[k]


def test_mutation_distance_slot_min_instead_of_max_breaks_h1(report, ps):
    """突變②：把裁定 #55 的「取該格最大」改成「取最小」。"""
    _, distance, _ = expected_tables(report, slot_agg=min)
    assert distance != CALIBRATED_DISTANCE_D
    diff = [(m, n) for m in distance for n in distance[m] if distance[m][n] != CALIBRATED_DISTANCE_D[m][n]]
    assert len(diff) == 8, diff        # 八格全部不同
    with pytest.raises(AssertionError):
        for row in report["rows"]:
            if row["category"] == "distance" and int(row["n"]):
                assert actual_d(ps, row) == distance[row["market"]][int(row["shared_d_n"])]
