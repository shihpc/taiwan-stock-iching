"""`scripts/rank_table.py`：卦別分組排序表（裁定 #54 Q6 ＋ #57 R1–R6 ＋ #60 R7–R8）。

**本檔的測資是從零造的合成 `train_*.csv.gz`**，不走 `synth_db` 那條重管線——R2／R3／R4 要的是
「各造一列」「造一個均值正中位數負的卦」，跑完整重播是殺雞用牛刀，而且造不出想要的分布。

守門的判準（這批的教訓，寫在這裡免得下一個人又踩）：
① 突變要打在**你聲稱要守的那個性質**上；②紅了要問「紅的是不是我想守的那支」；
③**期待值不得由被測函式自己產生**——R1 的 −0.00981… 在本檔獨立寫死，不得寫成 `net_ret(0)`。
"""
from __future__ import annotations

import csv
import gzip
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

import rank_table as RT  # noqa: E402

#: 驗收 R1 釘住的常數。**獨立寫死**：`fwd_ret=0` 時 `(1−0.002)(1−0.001425−0.003)/[(1.002)(1.001425)]−1`。
ZERO_NET = -0.009810371517992134


def write_dataset(data_dir: Path, rows_by_h: dict[str, list[dict]], *, segment="train",
                  manifest: dict | None = None) -> None:
    """造一份合成資料集。`rows_by_h` 的 key 是 horizon，值是 dict 列（缺的欄補空字串）。

    **三個 horizon 的檔一律都造**（沒給的造空檔）：真實資料集就是三個檔，而 `build()` 缺一個就
    rc=2。只造 `short` 會讓每一支測試都死在「找不到 train_swing.csv.gz」，而不是死在它要測的東西。
    """
    data_dir.mkdir(parents=True, exist_ok=True)
    rows_by_h = {hz: rows_by_h.get(hz, []) for hz in RT.HORIZONS}
    for hz, rows in rows_by_h.items():
        p = data_dir / f"{segment}_{hz}.csv.gz"
        with gzip.GzipFile(p, "wb", mtime=0) as raw, \
                __import__("io").TextIOWrapper(raw, encoding="utf-8", newline="") as fh:
            w = csv.writer(fh, lineterminator="\n")
            w.writerow(RT.COLUMNS)
            for r in rows:
                w.writerow([str(r.get(c, "")) for c in RT.COLUMNS])
    man = {"data_version": "dv", "params_sha": "sha", "head": "head", "window": 320,
           "pool_semantics": "pit-1", "model_version": {}, "h_by_horizon": {"short": 10},
           "segments": {"train": {"from": "2021-01-01", "to": "2023-06-30"}}}
    (data_dir / "manifest.json").write_text(json.dumps(man | (manifest or {})), encoding="utf-8")


def row(**kw):
    base = {"date": "2021-01-04", "market": "twse", "stock_id": "1101", "horizon": "short",
            "base_score": "50.0", "in_rank_pool": "1", "coverage": "full", "king_wen": "1",
            "lines_formal": "010101", "fwd_ret": "0.0", "mkt_ret_h": "0.0",
            "exit_reason": "", "entry_limit_up": "0", "exit_limit_down": "0"}
    return base | kw


def build(tmp_path, rows, hz="short", **kw):
    write_dataset(tmp_path, {hz: rows}, **kw)
    return RT.build(tmp_path)


def cell(rep, kw, market="twse", horizon="short"):
    got = [r for r in rep["rows"] if r["king_wen"] == kw and r["market"] == market
           and r["horizon"] == horizon]
    return got[0] if got else None


# ---------------------------------------------------------------- R1 成本算式

def test_r1_net_ret_matches_the_multiplicative_formula():
    """算式逐位；`fwd_ret=0` 的常數獨立寫死（不得拿 `net_ret(0)` 自己驗自己）。"""
    assert RT.net_ret(0.0) == ZERO_NET
    assert RT.ZERO_FWD_NET == ZERO_NET
    assert (RT.FEE, RT.TAX, RT.SLIP) == (0.001425, 0.003, 0.002), "費率不得更動（裁定 #57 :543）"
    # 20 個點以獨立重算的算式對比（分子分母在這裡另寫一次，不呼叫 RT 的任何東西）
    for i in range(-10, 10):
        fwd = i * 0.37
        want = (1.0 + fwd) * 0.998 * (1.0 - 0.001425 - 0.003) / (1.002 * 1.001425) - 1.0
        assert RT.net_ret(fwd) == want, fwd
    # 乘法 ≠ 算術扣除：文件記的兩個釘子（`docs/P3-CALIBRATION.md:556-558`）
    assert abs(RT.net_ret(3.26) - 3.2182078173333535) < 1e-15
    arith = 3.26 - (0.002 + 0.001425) - (0.002 + 0.001425 + 0.003)
    assert abs((3.26 + arith - 3.26) - RT.net_ret(3.26)) > 0.03, "大報酬上兩者應差 >3pp"


# ---------------------------------------------------------------- R2 列處理（三種各自分開）

def test_r2_entry_limit_up_row_is_excluded(tmp_path):
    """買不到的列整列排除，且進**排除**計數而不是「無報酬」計數。"""
    rep = build(tmp_path, [row(fwd_ret="0.5"), row(fwd_ret="9.9", entry_limit_up="1")])
    assert rep["disclosure"]["excluded_entry_limit_up"] == 1
    assert rep["disclosure"]["skipped_null_fwd_ret"] == 0, "不得被算成 fwd_ret 空"
    assert cell(rep, 1)["n"] == 1, "只剩那筆買得到的"


def test_r2_halt_and_delist_rows_are_kept(tmp_path):
    """停牌／下市是真實損失，保留。**`exit_reason` 在 CSV 裡沒有 `ok`**，空字串才是正常列。"""
    rep = build(tmp_path, [row(fwd_ret="-0.5", exit_reason="halt"),
                           row(fwd_ret="-0.9", exit_reason="delist"),
                           row(fwd_ret="0.1", exit_reason="")])
    assert cell(rep, 1)["n"] == 3, "三列都要留"
    assert (rep["disclosure"]["kept_halt"], rep["disclosure"]["kept_delist"]) == (1, 1)
    assert cell(rep, 1)["mean"] < 0, "保留虧損列後均值應為負（排除掉就會變正）"


def test_r2_exit_limit_down_row_is_kept(tmp_path):
    rep = build(tmp_path, [row(fwd_ret="-0.4", exit_limit_down="1"), row(fwd_ret="0.4")])
    assert cell(rep, 1)["n"] == 2 and rep["disclosure"]["kept_exit_limit_down"] == 1


def test_r2_null_fwd_ret_not_counted(tmp_path):
    """`fwd_ret` 空不計入 n；`no_entry` 的列三個欄都是空字串，不得 `int()`。"""
    rep = build(tmp_path, [row(fwd_ret="0.2"),
                           row(fwd_ret="", exit_reason="no_entry",
                               entry_limit_up="", exit_limit_down="")])
    assert cell(rep, 1)["n"] == 1 and rep["disclosure"]["skipped_null_fwd_ret"] == 1
    assert rep["disclosure"]["excluded_entry_limit_up"] == 0


def test_r2_empty_king_wen_not_counted(tmp_path):
    """空 `king_wen` 不計（Q6）——實檔 `train_short` 有 19,961 列這種，且 `fwd_ret` 有值。"""
    rep = build(tmp_path, [row(fwd_ret="0.2"), row(king_wen="", fwd_ret="0.44", lines_formal="")])
    assert rep["disclosure"]["skipped_empty_king_wen"] == 1
    assert sum(r["n"] for r in rep["rows"]) == 1


# ---------------------------------------------------------------- R3／R4 兩個標示

def _many(kw, vals, **kw2):
    return [row(king_wen=str(kw), fwd_ret=repr(v), **kw2) for v in vals]


def test_r3_sign_mismatch_is_ranked_but_forced_to_mid(tmp_path):
    """均值正、中位數負的卦：**照常排名**（裁定 #61 改回規格字面），但不進高／低組。

    裁定 #60 原本把它排除在排名之外；實測兩種讀法的高組名單差很多（tpex/short 15 vs 3）
    且方向不一致，使用者看過實證後改回字面。這支測試釘的是**字面版**：`rank` 不得是 None。
    """
    vals = [-0.01] * 600 + [5.0] * 400            # 均值正、中位數負，n=1000 >= 500
    rows = _many(1, vals)
    for kw in range(2, 10):                        # 另外 8 個正常卦撐出排名
        rows += _many(kw, [0.02] * 600)
    rep = build(tmp_path, rows)
    r = cell(rep, 1)
    assert r["mean"] > 0 > r["median"], f"測資沒造出異號：{r['mean']} / {r['median']}"
    assert r["flag_sign_mismatch"] is True and r["flag_small_n"] is False
    assert r["rank"] is not None, "異號卦照常排名（裁定 #61）"
    assert r["rank"] == 1, "均值最高（+199%），名次應為 1"
    assert r["group"] == "mid", "但不得進高組"


def test_r3_high_group_shrinks_when_sign_mismatch_lands_in_it(tmp_path):
    """高組實際人數 ≤ `floor(M/3)`——落在前 cut 名的異號卦被拉回中組，名額**不遞補**。

    突變守門：若異號卦沒被拉回中組，高組會剛好等於 cut。
    """
    rows = _many(1, [-0.01] * 600 + [5.0] * 400)   # 異號，均值最高 → 會落在前 cut 名
    for kw in range(2, 13):                        # 共 12 卦 → M=12、cut=4
        rows += _many(kw, [0.001 * kw] * 600)
    rep = build(tmp_path, rows)
    c = [r for r in rep["rows"] if r["horizon"] == "short"]
    assert sum(1 for r in c if r["rank"] is not None) == 12
    assert sum(1 for r in c if r["group"] == "high") == 3, "cut=4，但第 1 名是異號卦被拉回中組"
    assert sum(1 for r in c if r["group"] == "low") == 4, "低組沒有異號卦，維持 4"


def test_r4_small_n_is_flagged_by_a_different_field(tmp_path):
    """`n<500` 歸中組並標示，兩個標示欄**可分辨**（R4）。"""
    rows = _many(1, [0.9] * 499)                   # n=499 < 500，而且報酬最高
    for kw in range(2, 10):
        rows += _many(kw, [0.02] * 600)
    rep = build(tmp_path, rows)
    r = cell(rep, 1)
    assert r["n"] == 499
    assert r["flag_small_n"] is True and r["flag_sign_mismatch"] is False
    assert r["group"] == "mid" and r["rank"] is None, "報酬最高也不得進高組"
    other = cell(rep, 2)
    assert other["rank"] == 1, "把 n<500 的排掉之後，第一名要遞補"


# ---------------------------------------------------------------- R5 只讀訓練段

def test_r5_validation_segment_is_never_opened(tmp_path, monkeypatch):
    """驗證段檔就放在同一個目錄，斷言**一次都沒被開啟**。"""
    write_dataset(tmp_path, {"short": _many(1, [0.02] * 600)})
    write_dataset(tmp_path, {"short": _many(1, [9.99] * 600)}, segment="valid")
    opened: list[str] = []
    real = RT.gzip.open
    monkeypatch.setattr(RT.gzip, "open", lambda p, *a, **k: (opened.append(Path(p).name), real(p, *a, **k))[1])
    RT.build(tmp_path)
    assert opened, "根本沒開過檔，這支測試會假綠"
    # **只比檔名**：tmp_path 的目錄名本身就含 "valid"（test_r5_validation_…），
    # 比整條路徑會讓這支測試因為別的理由而紅（第一版就是這樣）。
    assert not [p for p in opened if p.startswith("valid_")], f"讀到驗證段：{opened}"
    assert sorted(opened) == ["train_mid.csv.gz", "train_short.csv.gz", "train_swing.csv.gz"]


def test_r5_segment_is_not_a_cli_option():
    """segment 寫死、不給參數——**給了參數就不是守門**。"""
    assert RT.SEGMENT == "train"
    out = RT.argparse.ArgumentParser  # noqa: F841  (只為確認模組真的用 argparse)
    import subprocess
    h = subprocess.run([sys.executable, str(ROOT / "scripts" / "rank_table.py"), "--help"],
                       capture_output=True, text=True, check=True).stdout
    assert "--segment" not in h and "--valid" not in h


# ---------------------------------------------------------------- R6 揭露行

def test_r6_disclosure_line_has_everything(tmp_path):
    rows = (_many(1, [0.02] * 600) + [row(fwd_ret="9.9", entry_limit_up="1"),
                                      row(fwd_ret="", exit_reason="no_entry",
                                          entry_limit_up="", exit_limit_down=""),
                                      row(king_wen="", fwd_ret="0.1"),
                                      row(fwd_ret="-0.3", exit_reason="halt")])
    rep = build(tmp_path, rows)
    md = RT.as_markdown(rep)
    assert "排除" in md and "entry_limit_up" in md
    assert "保留" in md and "halt" in md and "delist" in md
    assert "-0.981%" in md, "成本率要明寫（裁定 #57：以免日後被當成兩套數字）"
    assert "偏樂觀" in md and "0.2%" in md, "滑價不分層的註記（Q12）"
    for k in ("rows_read", "rows_counted", "excluded_entry_limit_up", "skipped_null_fwd_ret",
              "skipped_empty_king_wen", "kept_exit_limit_down", "kept_halt", "kept_delist"):
        assert k in rep["disclosure"]


# ---------------------------------------------------------------- R7／R8 裁定 #60

def test_r7_group_cut_is_floor_of_ranked_count(tmp_path):
    """高／低組各 `floor(M/3)`，`M`＝實際排到名次的卦數。M=64 → 21/21/22。"""
    rows = []
    for kw in range(1, 65):
        rows += _many(kw, [0.001 * kw] * 600)
    rep = build(tmp_path, rows)
    c = [r for r in rep["rows"] if r["horizon"] == "short"]
    assert sum(1 for r in c if r["rank"] is not None) == 64
    assert sum(1 for r in c if r["group"] == "high") == 21
    assert sum(1 for r in c if r["group"] == "low") == 21
    assert sum(1 for r in c if r["group"] == "mid") == 22
    assert cell(rep, 64)["rank"] == 1, "報酬最高者第 1 名"
    assert cell(rep, 64)["group"] == "high" and cell(rep, 1)["group"] == "low"


def test_r7_denominator_excludes_small_n_only(tmp_path):
    """分母只排除 `n<500`（裁定 #61）：4 卦 n<500 → M 由 64 掉到 60 → cut 由 21 變 20。"""
    rows = []
    for kw in range(1, 65):
        rows += _many(kw, [0.001 * kw] * (499 if kw <= 4 else 600))
    rep = build(tmp_path, rows)
    c = [r for r in rep["rows"] if r["horizon"] == "short"]
    assert sum(1 for r in c if r["rank"] is not None) == 60
    assert sum(1 for r in c if r["group"] == "high") == 20
    assert sum(1 for r in c if r["group"] == "low") == 20


def test_r8_rank_tie_break_is_king_wen(tmp_path):
    """均值同值時次鍵 `king_wen`——不帶次鍵名次會隨走訪序飄（budget.py／sectors.py 的家族教訓）。"""
    rows = []
    for kw in (9, 3, 7, 1):                        # 刻意不照順序寫入
        rows += _many(kw, [0.02] * 600)
    rep = build(tmp_path, rows)
    ranked = sorted((r for r in rep["rows"] if r["rank"] is not None), key=lambda r: r["rank"])
    assert [r["king_wen"] for r in ranked] == [1, 3, 7, 9], "同分時應依 king_wen 升冪"


# ---------------------------------------------------------------- 逐格重排與 md 冪等

def test_rank_is_per_market_horizon_cell(tmp_path):
    """排名的作用域是每個 (market, horizon) 格，不是全表一起排。"""
    rows = _many(1, [0.9] * 600) + _many(2, [0.1] * 600)
    rows += [r | {"market": "tpex"} for r in _many(1, [0.5] * 600)]
    rows += [r | {"market": "tpex"} for r in _many(2, [0.8] * 600)]
    rep = build(tmp_path, rows)
    assert cell(rep, 1, "twse")["rank"] == 1 and cell(rep, 2, "twse")["rank"] == 2
    assert cell(rep, 2, "tpex")["rank"] == 1 and cell(rep, 1, "tpex")["rank"] == 2


def test_markdown_splice_is_idempotent(tmp_path):
    doc = "# 標題\n\n內文\n"
    a = RT.splice_markdown(doc, "表 v1\n")
    b = RT.splice_markdown(a, "表 v2\n")
    assert a.count(RT.BEGIN) == 1 and b.count(RT.BEGIN) == 1 and b.count(RT.END) == 1
    assert "表 v1" not in b and "表 v2" in b
    assert b.startswith("# 標題"), "原有內容不得被吃掉"
    with pytest.raises(RT.RankTableError):
        RT.splice_markdown(doc + RT.BEGIN + "\n", "x\n")     # marker 只剩一半要拒絕


def test_missing_file_returns_rc2(tmp_path):
    assert RT.main(["--data-dir", str(tmp_path), "--out-json", str(tmp_path / "o.json"), "--no-md"]) == 2


def test_bad_header_returns_rc2(tmp_path):
    write_dataset(tmp_path, {"short": _many(1, [0.02] * 3)})
    p = tmp_path / "train_short.csv.gz"
    with gzip.open(p, "rt", encoding="utf-8") as fh:
        body = fh.read().split("\n", 1)[1]
    with gzip.open(p, "wt", encoding="utf-8", newline="") as fh:
        fh.write("date,market\n" + body)
    assert RT.main(["--data-dir", str(tmp_path), "--out-json", str(tmp_path / "o.json"), "--no-md"]) == 2


def test_r7_cut_is_floor_not_round(tmp_path):
    """`floor` 不是 `round`——**測資要挑兩者分歧的 M**。

    `M` 在 50..64 之間只有 {50, 53, 56, 59, 62} 會讓 `floor(M/3) != round(M/3)`；
    上面兩支 R7 用的 `M=64` 與 `M=60` 恰好都是兩者同值，**殺不掉 `floor→round` 的突變**
    （實測存活）。這與前幾輪 ⑦ 的 `quota_multiplier` 用 1.0／0.5（在 `N0=5` 下同值）是同型錯誤：
    **測資的參數值若剛好讓兩種實作同值，突變就殺不掉**。這裡用 `M=62`（floor 20／round 21）。
    """
    rows = []
    for kw in range(1, 65):                        # 2 卦 n<500 → M=62
        rows += _many(kw, [0.001 * kw] * (499 if kw <= 2 else 600))
    rep = build(tmp_path, rows)
    c = [r for r in rep["rows"] if r["horizon"] == "short"]
    assert sum(1 for r in c if r["rank"] is not None) == 62
    assert 62 // 3 != round(62 / 3), "測資選錯了，這個 M 分不出 floor 與 round"
    assert sum(1 for r in c if r["group"] == "high") == 20, "floor(62/3)=20；round 會給 21"
    assert sum(1 for r in c if r["group"] == "low") == 20


# ---------------------------------------------------------------- 裁定 #61 的分位數欄

def test_quantiles_are_p25_p75_inclusive(tmp_path):
    """p25／p75 用 `method="inclusive"`（＝numpy 的 linear 口徑）。期待值在測試端獨立算。"""
    vals = [i / 100 for i in range(1, 601)]        # 0.01 … 6.00，n=600
    rep = build(tmp_path, _many(1, vals))
    r = cell(rep, 1)
    nets = sorted(RT.net_ret(v) for v in vals)
    # 獨立重算 inclusive 分位數：位置 = q*(n-1)，線性內插
    def q(p):
        pos = p * (len(nets) - 1)
        lo = int(pos)
        return nets[lo] + (nets[min(lo + 1, len(nets) - 1)] - nets[lo]) * (pos - lo)
    assert abs(r["p25"] - q(0.25)) < 1e-12, (r["p25"], q(0.25))
    assert abs(r["p75"] - q(0.75)) < 1e-12
    assert r["p25"] < r["median"] < r["p75"]


def test_quantiles_survive_single_row(tmp_path):
    """n=1 時 `statistics.quantiles` 會拋 StatisticsError，必須退為該值本身、不得整支炸。"""
    rep = build(tmp_path, _many(1, [0.25]))
    r = cell(rep, 1)
    assert r["n"] == 1 and r["p25"] == r["p75"] == r["mean"] == r["median"]


# ---------------------------------------------------------------- 2026-09-23 驗收退回後補

def test_disclosure_gives_both_population_and_attribution(tmp_path):
    """揭露要同時給**母體**與**歸屬**（F2）：兩者的差額是被更前面的規則先攔掉的列。

    測資＝一列同時「空 `king_wen`」且 `entry_limit_up=1`：母體兩邊各算一次，
    歸屬只算在前面那一條（空 `king_wen`）。突變守門：只給歸屬數時這支會紅。
    """
    rows = _many(1, [0.02] * 3) + [row(king_wen="", entry_limit_up="1", fwd_ret="")]
    rep = build(tmp_path, rows)
    d = rep["disclosure"]
    assert (d["raw_empty_king_wen"], d["raw_entry_limit_up"], d["raw_null_fwd_ret"]) == (1, 1, 1)
    assert d["skipped_empty_king_wen"] == 1, "歸屬給最前面那一條"
    assert d["excluded_entry_limit_up"] == 0 and d["skipped_null_fwd_ret"] == 0
    md = RT.as_markdown(rep)
    assert "母體" in md and "歸屬" in md


def test_median_is_the_true_median_not_median_high(tmp_path):
    """`median` 的定義要釘住（F5）：偶數筆時取兩個中間值的平均，不是 `median_high`。

    突變守門：改成 `statistics.median_high` 會動到真實產出 384 列裡的 152 列。
    期待值在測試端獨立算（判準③），不呼叫 `statistics`。
    """
    rep = build(tmp_path, _many(1, [0.0, 0.0, 1.0, 1.0]))    # 四筆，兩個中間值不同
    nets = sorted(RT.net_ret(v) for v in (0.0, 0.0, 1.0, 1.0))
    want = (nets[1] + nets[2]) / 2
    got = cell(rep, 1)["median"]
    assert abs(got - want) < 1e-15, f"median_high 會給 {nets[2]}，實得 {got}"
    assert got != nets[2], "取到了 median_high"


def test_sign_mismatch_needs_strict_opposite_signs(tmp_path):
    """`mean * median < 0`——**0 不算異號**（`<= 0` 會把中位數恰為 0 的卦誤判成異號）。

    第一版這支的最後一行是 `assert (0.0 * -1.0 < 0.0) is False`——一個**恆真的算術斷言**，
    根本沒碰到程式，於是 `< 0` → `<= 0` 的突變照樣全綠（實測存活）。現在改成造一個
    **中位數恰為 0** 的卦：解 `net_ret(x) = 0` 得 `x = 1/k - 1`，四筆取兩筆該值當中間值。
    """
    k = 0.998 * (1.0 - 0.001425 - 0.003) / (1.002 * 1.001425)
    zero_fwd = 1.0 / k - 1.0                       # net_ret(zero_fwd) == 0
    assert abs(RT.net_ret(zero_fwd)) < 1e-15, "測資沒造出 net_ret 恰為 0"
    rep = build(tmp_path, _many(1, [-0.5, zero_fwd, zero_fwd, 5.0]))
    r = cell(rep, 1)
    assert abs(r["median"]) < 1e-15, f"中位數應恰為 0，實得 {r['median']}"
    assert r["mean"] > 0, "均值要非零，否則測不出 0 的那一邊"
    assert r["flag_sign_mismatch"] is False, "中位數為 0 不算異號（`<= 0` 會誤判成 True）"


def test_asymmetry_narrative_follows_the_data(tmp_path):
    """F1：「被掏空的是高組還是低組」**必須由資料判定**，不得硬編方向。

    首版硬編「異號卦系統性集中在排名前段、被掏空的是高組」——在真實資料的兩個 `mid` 格
    完全相反（異號名次中位數 36 vs 同號 21~23，被掏空的是低組），而反證的表就印在那段
    文字下面四行。這支造一個**異號卦全在後段**的格，斷言敘述說的是「低組」。
    """
    rows = []
    for kw in range(1, 13):                        # 12 卦：前 8 名同號、後 4 名異號
        if kw <= 8:
            rows += _many(kw, [0.05 * kw] * 600)   # 均值高、中位數同號
        else:
            # 異號（mean>0>median）**且均值低於所有同號卦** → 必然排在後段。
            # 500 筆 −1%（決定中位數為負）＋100 筆 +17%（把均值拉到 +1.0%，仍低於 kw=1 的 +3.97%）
            rows += _many(kw, [-0.01] * 500 + [0.17] * 100)
    rep = build(tmp_path, rows)
    c = [r for r in rep["rows"] if r["horizon"] == "short"]
    sm = [r["rank"] for r in c if r["flag_sign_mismatch"] and r["rank"]]
    ok = [r["rank"] for r in c if not r["flag_sign_mismatch"] and r["rank"]]
    assert sm and ok and min(sm) > max(ok), f"測資沒造出「異號全在後段」：{sorted(sm)} vs {sorted(ok)}"
    md = RT.as_markdown(rep)
    assert "被掏空的是**低組**" in md, "異號卦在後段時，敘述必須說低組"
    assert "被掏空的是**高組**" not in md, "沒有任何格是前段，不得出現高組那一句"


def test_rows_are_sorted_deterministically(tmp_path):
    """凍結產物的列序要釘住（F6）：`(market, horizon, king_wen)`，不靠 dict 插入序。"""
    rows = []
    for kw in (7, 2, 9):
        rows += _many(kw, [0.02] * 3)
        rows += [r | {"market": "tpex"} for r in _many(kw, [0.03] * 3)]
    rep = build(tmp_path, rows)
    keys = [(r["market"], r["horizon"], r["king_wen"]) for r in rep["rows"]]
    assert keys == sorted(keys), f"列序不是排好的：{keys[:5]}"


def test_asymmetry_narrative_the_other_direction(tmp_path):
    """F1 的反向：異號卦全在**前**段時，敘述只能說「高組」。

    與上一支合起來才擋得住「把兩句其中一句硬編成永遠出現」——單有一支時，
    `if back:` → `if True:` 是等價突變（那組測資的 `back` 本來就非空，實測存活）。
    """
    rows = []
    for kw in range(1, 13):
        if kw <= 8:
            rows += _many(kw, [-0.05 * kw] * 600)  # 均值與中位數同為負
        else:
            rows += _many(kw, [-0.01] * 500 + [0.17] * 100)   # 異號，均值 +1.0% 高於所有同號
    rep = build(tmp_path, rows)
    c = [r for r in rep["rows"] if r["horizon"] == "short"]
    sm = [r["rank"] for r in c if r["flag_sign_mismatch"] and r["rank"]]
    ok = [r["rank"] for r in c if not r["flag_sign_mismatch"] and r["rank"]]
    assert sm and ok and max(sm) < min(ok), f"測資沒造出「異號全在前段」：{sorted(sm)} vs {sorted(ok)}"
    md = RT.as_markdown(rep)
    assert "被掏空的是**高組**" in md
    assert "被掏空的是**低組**" not in md, "沒有任何格是後段，不得出現低組那一句"
