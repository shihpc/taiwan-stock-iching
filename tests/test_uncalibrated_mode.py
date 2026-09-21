"""`build_params(calibrated=False)`：`:717` 前側用的「舊 d」模式（`docs/P3-CALIBRATION.md` §19 的 E1～E4）。

`:717` 要比對縮放前後，而前側**不能**用「checkout 校準前的 commit」來跑——那份沒有 §17（coverage 分母）
與 §18（三個出口欄），差異會混進語意變更與欄位差。所以前側必須是「舊 d ＋ §17 ＋ §18」。

**E1 是本檔的重點**：證明不校準模式吐出的真的是校準前那一組 d（對 `c1fc988` 逐位比），
而不是有人另外編了一組數字。其餘幾支守的是「預設模式不受影響」與旗標語意。
"""
from __future__ import annotations

import dataclasses
import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from iching.score.params import (  # noqa: E402
    DISTANCE_D_START, MARKET_SLOPE_D_START, MARKETS, STOCK_SLOPE_D_START, build_params,
)

PRE_CALIB = "c1fc988"        # PR #50（d 校準）的 parent ＝ 校準前的最後一版

import pytest  # noqa: E402


DUMP = """
import json, sys
sys.path.insert(0, "src")
from iching.score.params import MARKETS, build_params
out = {}
for m in MARKETS:
    p = build_params(m)
    out[m] = {
        "d": {"|".join(k): (None if p.params[k].d is None else float(p.params[k].d).hex()) for k in p.params},
        "distance_d": {str(k): v for k, v in p.distance_d.items()},
        "market_slope_d": {str(k): v for k, v in p.market_slope_d.items()},
        "stock_slope_d": {str(k): v for k, v in p.stock_slope_d.items()},
    }
print(json.dumps(out))
"""


def _pre_calib_dump():
    """在校準前那個 commit 的 worktree 裡**實際跑一次** `build_params`，把 d 值 dump 回來。

    刻意不用「單檔載入舊 params.py」——它是套件內模組、有相對匯入，單檔載入會 ImportError；
    而且在真 worktree 裡跑才真的是「那一版的行為」，不是把舊檔塞進新樹拼湊出來的。
    """
    wt = Path(tempfile.mkdtemp()) / "pre"
    subprocess.run(["git", "worktree", "add", "-q", "--detach", str(wt), PRE_CALIB],
                   cwd=ROOT, check=True, capture_output=True)
    try:
        r = subprocess.run([sys.executable, "-c", DUMP], cwd=wt, capture_output=True, text=True, check=True)
        return json.loads(r.stdout)
    finally:
        subprocess.run(["git", "worktree", "remove", "--force", str(wt)], cwd=ROOT, capture_output=True)


# E1：不校準模式的每個 d，與校準前 commit 的同鍵 d 逐位相同（兩市場）
def test_uncalibrated_d_matches_pre_calibration_commit(pre_dump):
    for m in MARKETS:
        n = build_params(m, calibrated=False)
        od = pre_dump[m]["d"]
        nd = {"|".join(k): (None if n.params[k].d is None else float(n.params[k].d).hex()) for k in n.params}
        assert set(od) == set(nd), f"{m}：參數鍵集合不同，比對前提不成立"
        diffs = [(k, od[k], nd[k]) for k in sorted(od) if od[k] != nd[k]]
        assert not diffs, f"{m}：{len(diffs)} 個鍵的 d 與校準前不同，前 3 筆 {diffs[:3]}"
        for attr in ("distance_d", "market_slope_d", "stock_slope_d"):
            assert {str(k): v for k, v in getattr(n, attr).items()} == pre_dump[m][attr], f"{m}|{attr} 與校準前不同"


# E1 的反面：校準模式**必須**與校準前不同，否則上一支等於在比兩個一樣的東西（空測）
def test_calibrated_mode_really_differs_from_pre_calibration(pre_dump):
    for m in MARKETS:
        n = build_params(m)
        od = pre_dump[m]["d"]
        nd = {"|".join(k): (None if n.params[k].d is None else float(n.params[k].d).hex()) for k in n.params}
        changed = sum(1 for k in od if od[k] is not None and nd[k] is not None and od[k] != nd[k])
        assert changed >= 100, f"{m}：只有 {changed} 個 d 變了，校準沒生效或比對對象錯了"


# E2：預設模式逐位不變（§17／§18 之後的現行指紋）
def test_default_mode_fingerprints_unchanged():
    assert build_params("twse").model_version() == "p2-score-engine-1.0bb386e9cf3b"
    assert build_params("tpex").model_version() == "p2-score-engine-1.8eb4f29fec3a"


# E3：旗標語意——calibrated 欄位、指紋必須不同
def test_uncalibrated_flag_semantics():
    for m in MARKETS:
        a, b = build_params(m), build_params(m, calibrated=False)
        assert a.calibrated is True and b.calibrated is False
        assert a.model_version() != b.model_version(), "不校準模式沒換指紋＝兩份 db 可能被混用"


# E4：三個查表退回起點值
def test_tables_fall_back_to_start_values():
    for m in MARKETS:
        p = build_params(m, calibrated=False)
        assert p.distance_d == DISTANCE_D_START
        assert p.market_slope_d == MARKET_SLOPE_D_START
        assert p.stock_slope_d == STOCK_SLOPE_D_START
        # 對照：校準模式至少有一格不同，否則這支也是空測
        c = build_params(m)
        assert (c.distance_d, c.market_slope_d, c.stock_slope_d) != (DISTANCE_D_START, MARKET_SLOPE_D_START, STOCK_SLOPE_D_START)


# §19 的硬約束：除了 d 與 calibrated 旗標，兩個模式的其餘參數欄位必須完全相同
def test_only_d_and_flag_differ_between_modes():
    for m in MARKETS:
        a, b = build_params(m), build_params(m, calibrated=False)
        for k in sorted(a.params):
            x, y = dataclasses.asdict(a.params[k]), dataclasses.asdict(b.params[k])
            for f in sorted(x):
                if f == "d":
                    continue
                assert x[f] == y[f], f"{m}|{k}|{f} 兩模式不同（只許 d 不同）"
        assert a.family_weights == b.family_weights and a.line_weights == b.line_weights
        assert dataclasses.asdict(a.rules) == dataclasses.asdict(b.rules)


@pytest.fixture(scope="module")
def pre_dump():
    return _pre_calib_dump()


# E5 的前哨：**旗標必須真的被消費**。
# 本批差點出事的地方——第一版的修改腳本在中途 assert 失敗、而 write_text 排在最後，於是「接上
# build_params」那一處根本沒寫入，只有 argparse 定義落地。結果是：`--help` 看得到、
# `tests/test_argparse_help.py` 也綠（它只證明 argparse 定義合法、不炸），但旗標**毫無作用**。
# 那會讓 :717 前側那次 12.6 小時的重播跑出新 d，且因指紋與生產相同而安靜覆蓋掉生產 db。
# 所以這裡直接對真的合成世界跑一次，比對兩種模式寫出的 db 指紋。
def test_uncalibrated_flag_is_actually_consumed(tmp_path):
    sys.path.insert(0, str(ROOT / "scripts"))
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import replay_scores as RP
    import scan_features as SF
    from iching.scores_io import ScoreStore
    from synth_db import DV, build_full

    cache = tmp_path / "cache"
    build_full(cache)
    assert SF.main(["--cache-dir", str(cache), "--quiet", "--allow-short-warmup", "--warmup-days", "0"]) == 0
    shas = {}
    for tag, extra in (("cal", []), ("uncal", ["--uncalibrated"])):
        out = tmp_path / f"{tag}.db"
        assert RP.main(["--cache-dir", str(cache), "--out", str(out), "--quiet", *extra]) == 0
        with ScoreStore(out, readonly=True) as s:
            shas[tag] = s.params_sha_of(DV)
    assert shas["cal"] and shas["uncal"]
    assert shas["cal"] != shas["uncal"], (
        f"--uncalibrated 沒有改變 params_sha（兩者皆 {shas['cal']}）＝旗標沒接到 build_params，"
        "那份 db 會與生產 db 混用")


# 缺口 1：`export_scores` 原本零血統檢查，是**唯一**能把 uncal 分數寫進 `data/scores/` 的路徑
# （驗收者實測 rc=0 寫出去了）。現在它與 `export_dataset` 共用同一支 `check_params`。
def test_export_scores_refuses_uncalibrated_db(tmp_path):
    sys.path.insert(0, str(ROOT / "scripts"))
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import export_scores as EX
    import replay_scores as RP
    import scan_features as SF
    from synth_db import build_full

    cache = tmp_path / "cache"
    build_full(cache)
    assert SF.main(["--cache-dir", str(cache), "--quiet", "--allow-short-warmup", "--warmup-days", "0"]) == 0
    unc = tmp_path / "uncal.db"
    assert RP.main(["--cache-dir", str(cache), "--out", str(unc), "--quiet", "--uncalibrated"]) == 0
    # 把 uncal db 擺到 export_scores 會讀的位置
    (cache / "scores.db").write_bytes(unc.read_bytes())
    for extra in ([], ["--force"]):          # --force 是覆寫開關，不是繞過血統檢查的開關
        rc = EX.main(["--cache-dir", str(cache), "--out", str(tmp_path / "o"), *extra])
        assert rc != 0, f"uncal db 竟然匯得出去（extra={extra}）"
    assert not (tmp_path / "o" / "data" / "scores").exists(), "被擋下來卻仍寫出了分數檔"


# 缺口 2：`--uncalibrated --rebuild` 打在預設 out（＝生產 db）會先 clear 再寫，
# 「同 dv 換指紋即拒寫」那道救不了。故要求明給 --out 且不得指向 cache/scores.db。
def test_uncalibrated_refuses_production_out(tmp_path):
    sys.path.insert(0, str(ROOT / "scripts"))
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import replay_scores as RP
    import scan_features as SF
    from synth_db import build_full

    cache = tmp_path / "cache"
    build_full(cache)
    # **必須先建 features.db**：否則重播本來就會因缺 features 而 rc=2，這支會靠巧合變綠
    # （第一版就是這樣——把守門關掉仍全綠，突變測試才抓出來）。先證明同一組參數在給了合法
    # --out 時真的跑得起來（rc=0），守門才是 rc=2 的唯一成因。
    assert SF.main(["--cache-dir", str(cache), "--quiet", "--allow-short-warmup", "--warmup-days", "0"]) == 0
    assert RP.main(["--cache-dir", str(cache), "--quiet", "--uncalibrated",
                    "--out", str(tmp_path / "ok.db")]) == 0
    assert RP.main(["--cache-dir", str(cache), "--quiet", "--uncalibrated", "--rebuild"]) == 2
    assert RP.main(["--cache-dir", str(cache), "--quiet", "--uncalibrated",
                    "--out", str(cache / "scores.db")]) == 2
    assert not (cache / "scores.db").exists(), "被擋下來卻仍動到了生產 db 路徑"
