"""§16.5 `:712` 後半：每個子指標的 `native_range` 只被套用一次 `N`（無重複映射、無漏套、`P_cs` 未被套）。

`scores.db` 不存子指標層資料，所以不能從 db 驗。使用者裁定（`docs/P3-CALIBRATION.md` §25「`N` 只套一次」）：
**執行期計數＋呼叫點守門**——

1. **執行期**：以合成資料跑真實重播，攔截 `market.sub_result`／`stock.sub_result`（子指標唯一入口），逐次計算
   該次 `sub_result` 內 `normalize` 與 `N` 被呼叫幾次：`Ind` 須恰 1 次 `normalize`；`N` 在原生值域非 S 值域時恰 1 次、
   是 S 值域時 0 次；`Missing` 兩者皆 0。另核對 **`N` 總呼叫數＝各 `sub_result` 內的次數＋兩個常數換算
   （`scenario_value_after_N`：下限 84.16、封頂 79.89）的次數**——等式不成立＝有 `ind_*` 在 `sub_result` 之外
   偷套了 `N`（重複映射）。
2. **呼叫點守門**：以 AST 鎖死 `normalize(`／`N(`／`scenario_value_after_N(` 在 `src/iching/` 的呼叫點；多一處即紅，
   逼人回來看是否重複映射。
3. **`P_cs`**：只在過熱旗標比較，不得出現在任何 `sub_result` 的子指標名，也不得作為 `Ind` 回傳。

合成資料**沒跑到**的子指標只受第 2 點的靜態守門，清單寫死在 `UNEXERCISED`（變了就紅，逼人更新紀錄）。
"""
from __future__ import annotations

import ast
import sys
from collections import Counter, defaultdict
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import replay_scores as R  # noqa: E402
import scan_features as SF  # noqa: E402
from iching.score import aggregate, market, stock, transform  # noqa: E402
from iching.score.params import build_params  # noqa: E402
from iching.score.transform import S_RANGE, Ind  # noqa: E402
from synth_db import build_full  # noqa: E402

#: 合成資料跑不到的子指標（上游表缺或樣本不足）。只受呼叫點靜態守門保障；集合變了測試即紅，須同步更新 §25。
#: 其中 `foreign_net_oi_phist`／`vix_phist_rev` 會套 `N`（P_hist），其餘是 S 型（不套）。它們與跑到的子指標走同一個
#: `sub_result`→`normalize` 入口；「`ind_*` 內部沒有偷套 `N`」由 `test_call_sites_locked` 對**全部**程式靜態保證。
UNEXERCISED: set[str] = {
    "basis", "eps_diff_over_price", "equity_qoq", "excess_vs_industry", "foreign_net_oi_phist", "pretax_income_yoy",
    "revenue_accel", "revenue_yoy_vs_industry", "short_sale_change", "updown_volume_ratio", "vix_phist_rev",
}


@pytest.fixture(scope="module")
def trace(tmp_path_factory):
    c = tmp_path_factory.mktemp("nonce") / "cache"
    build_full(c)
    assert SF.main(["--cache-dir", str(c), "--quiet", "--allow-short-warmup", "--warmup-days", "0"]) == 0

    n_calls = {"N": 0, "normalize": 0, "const": 0}
    records: list[dict] = []
    orig_N, orig_norm = transform.N, aggregate.normalize
    orig_sub = {m: m.sub_result for m in (market, stock)}
    orig_const = stock.scenario_value_after_N

    def N_counted(*a, **k):
        n_calls["N"] += 1
        return orig_N(*a, **k)

    def norm_counted(ind):
        n_calls["normalize"] += 1
        return orig_norm(ind)

    def const_counted(v):
        n_calls["const"] += 1
        return orig_const(v)

    def make_sub(orig):
        def sub_counted(indicator_id, out, *a, **k):
            n0, z0 = n_calls["N"], n_calls["normalize"]
            res = orig(indicator_id, out, *a, **k)
            records.append({"id": indicator_id, "ind": isinstance(out, Ind),
                            "native_range": tuple(out.native_range) if isinstance(out, Ind) else None,
                            "dN": n_calls["N"] - n0, "dnorm": n_calls["normalize"] - z0})
            return res
        return sub_counted

    mp = pytest.MonkeyPatch()
    mp.setattr(transform, "N", N_counted)
    mp.setattr(aggregate, "normalize", norm_counted)
    mp.setattr(stock, "scenario_value_after_N", const_counted)
    for mod in (market, stock):
        mp.setattr(mod, "sub_result", make_sub(orig_sub[mod]))
    try:
        out = c / "scores.db"
        assert R.main(["--cache-dir", str(c), "--out", str(out), "--window", "30", "--quiet"]) == 0
    finally:
        mp.undo()
    return {"records": records, "n": dict(n_calls)}


def test_trace_is_nonempty(trace):
    assert len(trace["records"]) > 1000 and sum(r["ind"] for r in trace["records"]) > 500


def test_each_sub_normalized_exactly_once(trace):
    bad = [r for r in trace["records"] if r["dnorm"] != (1 if r["ind"] else 0)]
    assert not bad, bad[:5]


def test_N_applied_iff_native_range_not_S(trace):
    bad = [r for r in trace["records"]
           if r["dN"] != (0 if (not r["ind"] or r["native_range"] == S_RANGE) else 1)]
    assert not bad, bad[:5]


def test_no_N_outside_sub_result_except_two_constants(trace):
    """N 總次數＝各 sub_result 內的次數＋常數換算次數。不等＝有 ind_* 在外面偷套了 N（重複映射）。"""
    inside = sum(r["dN"] for r in trace["records"])
    assert trace["n"]["N"] == inside + trace["n"]["const"]
    assert trace["n"]["normalize"] == sum(r["dnorm"] for r in trace["records"])


def test_declared_native_range_matches_runtime(trace):
    """Param.native_range（宣告）與 ind_* 實際回傳的 Ind.native_range 一致（決定套不套 N 的是後者）。"""
    declared = {}
    for m in ("twse", "tpex"):
        for (_, _, _, _, iid), p in build_params(m).params.items():
            declared.setdefault(iid, set()).add(tuple(p.native_range))
    runtime = defaultdict(set)
    for r in trace["records"]:
        if r["ind"]:
            runtime[r["id"]].add(r["native_range"])
    bad = {i: (sorted(runtime[i]), sorted(declared.get(i, ()))) for i in runtime if not runtime[i] <= declared.get(i, set())}
    assert not bad, bad


def test_p_cs_never_a_sub_indicator(trace):
    assert not [r for r in trace["records"] if "p_cs" in r["id"].lower()]


def test_coverage_is_recorded(trace):
    """合成資料跑到的子指標集合；跑不到的只靠靜態守門。集合改變時必須同步更新 UNEXERCISED 與 §25。"""
    scored = set()
    for m in ("twse", "tpex"):
        for (_, _, _, _, iid), p in build_params(m).params.items():
            if p.scored and iid not in ("revenue_high_12m",):
                scored.add(iid)
    hit = {r["id"] for r in trace["records"] if r["ind"]}
    assert scored - hit == UNEXERCISED, "UNEXERCISED=" + repr(sorted(scored - hit))


# ---- 呼叫點守門（AST） ----

def _calls(name: str) -> Counter:
    out: Counter = Counter()
    for p in sorted((ROOT / "src" / "iching").rglob("*.py")):
        tree = ast.parse(p.read_text(encoding="utf-8"))
        funcs = {}
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                for sub in ast.walk(node):
                    funcs.setdefault(id(sub), node.name)
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                f = node.func
                nm = f.id if isinstance(f, ast.Name) else (f.attr if isinstance(f, ast.Attribute) else None)
                if nm == name:
                    out[f"{p.relative_to(ROOT)}:{funcs.get(id(node), '<module>')}"] += 1
    return out


def test_call_sites_locked():
    """多一處呼叫即紅：新呼叫點要先證明不是重複映射，再更新這裡。"""
    assert _calls("normalize") == Counter({"src/iching/score/aggregate.py:sub_result": 1})
    assert _calls("N") == Counter({"src/iching/score/transform.py:normalize": 1,
                                   "src/iching/score/transform.py:scenario_value_after_N": 1})
    assert _calls("scenario_value_after_N") == Counter({"src/iching/score/stock.py:line1_operations": 1,
                                                        "src/iching/score/stock.py:line3_momentum": 1})
