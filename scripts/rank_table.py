#!/usr/bin/env python3
"""卦別分組排序表（裁定 #54 Q6 ＋ #57 Q9–Q12 ＋ #60）：讀訓練段 `train_*.csv.gz` → 算扣成本淨報酬
的分組報酬 → 寫 `data/rank_table.json` ＋ `docs/pre-registration.md` 檔尾的「附錄 A」。

## 判準出處（一律不憑印象）

- 表的鍵與欄、高/中/低組、`n<500`、空 `king_wen` 不計：`docs/P3-CALIBRATION.md:162-163`（裁定 #54 Q6）
- 成本模型與列處理：`docs/P3-CALIBRATION.md:540-589`（裁定 #57 Q9–Q12 ＋ 驗收 R1–R6）
- 取整、JSON schema、附錄位置：裁定 #60（本檔實作即正本，同步記在 `docs/P3-CALIBRATION.md` §21）

## 三件容易寫錯、已實查的事

1. **`exit_reason` 在 CSV 裡沒有 `ok`**：真值只有 `""`／`no_entry`／`halt`／`delist`
   （`train_short` 全檔實測 1,060,702／8,678／8,502／263）。`ok` 只是 `export_dataset.py:435`
   寫 manifest 統計時把空字串 map 出來的標籤。判 halt／delist 時寫 `== "ok"` 會全錯。
2. **`no_entry` 的列，三個欄都是空字串**（`entry_limit_up`／`exit_limit_down`／`fwd_ret`，
   實測 8,678 列三者精確相等）。所以布林欄一律用 `== "1"` 比對，**不可 `int(v)`**（會 ValueError）。
3. **只讀 train**：segment 寫死、不給 CLI 參數（驗收 R5「驗證段一列都不讀」）。給了參數就不是守門。

## 只用標準庫

同 `export_dataset.py`／`check_dataset.py` 的既定慣例（Hetzner 是系統層 Python，不引入 pandas）。
每卦的淨報酬用 `array('d')` 累積（`train_*` 三檔合計約 323 萬列，list of float 約 100 MB、array 約 26 MB）。

rc：0 成功／2 中止（檔案缺、表頭不符、manifest 讀不到）。
"""
from __future__ import annotations

import argparse
import csv
import gzip
import json
import statistics
import sys
from array import array
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parent.parent

# ---- 成本模型（裁定 #54 Q6 定費率、#57 Q9 定套用方式；費率一個字都不得改）----
FEE = 0.001425          # 手續費，未打折公定價（偏保守），買賣各一次
TAX = 0.003             # 證交稅，只在賣出
SLIP = 0.002            # 滑價，單邊；裁定 #57 Q12：一律 0.2% 不分層（對低流動性個股偏樂觀）

SEGMENT = "train"       # 寫死：驗收 R5 要求驗證段一列都不讀
HORIZONS = ("short", "swing", "mid")
SMALL_N = 500           # 裁定 #54 Q6：n<500 不參與排名、歸中組並標示
COLUMNS = ("date", "market", "stock_id", "horizon", "base_score", "in_rank_pool", "coverage",
           "king_wen", "lines_formal", "fwd_ret", "mkt_ret_h", "exit_reason",
           "entry_limit_up", "exit_limit_down")
KEEP_EXIT = ("halt", "delist")      # 裁定 #57 Q11：賣不掉／停牌／下市都是真實損失，保留


class RankTableError(Exception):
    pass


def net_ret(fwd: float) -> float:
    """扣成本淨報酬（裁定 #57 Q9 的乘法式，逐字）。

        net = (1 + fwd) × (1−s)(1−f−t) / [(1+s)(1+f)] − 1

    **乘法不是算術扣除**：mid horizon 實測 `max|fwd_ret|` 達 3.26，算術扣除在大報酬上失真
    （`fwd_ret=3.26` 時兩者差 3.19 個百分點）。`fwd_ret=0` 時本式為 −0.00981037…，
    與 Q6 那個 0.985% 的一階估算差 0.004 個百分點（二階項），是**同一個成本模型的精確化**。
    """
    return (1.0 + fwd) * (1.0 - SLIP) * (1.0 - FEE - TAX) / ((1.0 + SLIP) * (1.0 + FEE)) - 1.0


#: `net_ret(0.0)` 的值。**測試端要獨立寫死這個常數**（驗收 R1），不得拿 `net_ret(0)` 自己驗自己。
ZERO_FWD_NET = net_ret(0.0)


class Disclosure:
    """表下揭露行要的計數（驗收 R6：排除與保留的列數都要給，不得只給淨報酬不給母體）。"""

    def __init__(self) -> None:
        self.rows_read = 0
        self.skipped_empty_king_wen = 0
        self.excluded_entry_limit_up = 0
        self.skipped_null_fwd_ret = 0
        self.rows_counted = 0
        self.kept_exit_limit_down = 0
        self.kept_halt = 0
        self.kept_delist = 0

    def as_dict(self) -> dict[str, int]:
        return {k: v for k, v in sorted(vars(self).items())}


def scan_file(path: Path, acc: dict, disc: Disclosure) -> None:
    """串流讀一個 `train_<horizon>.csv.gz`，把淨報酬累進 `acc[(market, horizon, king_wen)]`。

    **判斷順序是有意義的**（裁定 #57 Q11 ＋ Q6）：空 `king_wen` → `entry_limit_up` → 空 `fwd_ret`。
    `entry_limit_up` 排在空 `fwd_ret` 之前，是為了讓「買不到」的列進排除計數而不是進「無報酬」計數；
    兩者在揭露行上是不同的數字。
    """
    with gzip.open(path, "rt", encoding="utf-8", newline="") as fh:
        r = csv.reader(fh)
        head = next(r, None)
        if head is None or tuple(head) != COLUMNS:
            raise RankTableError(f"{path.name} 表頭與 export_dataset.COLUMNS 不符：{head}")
        i_mk, i_hz = COLUMNS.index("market"), COLUMNS.index("horizon")
        i_kw, i_fr = COLUMNS.index("king_wen"), COLUMNS.index("fwd_ret")
        i_er = COLUMNS.index("exit_reason")
        i_up, i_dn = COLUMNS.index("entry_limit_up"), COLUMNS.index("exit_limit_down")
        for row in r:
            disc.rows_read += 1
            kw = row[i_kw]
            if not kw:                                  # Q6：空 king_wen 不計
                disc.skipped_empty_king_wen += 1
                continue
            if row[i_up] == "1":                        # Q11：買不到，整列排除
                disc.excluded_entry_limit_up += 1
                continue
            fr = row[i_fr]
            if not fr:                                  # Q11：視窗超出資料末日，不計入 n
                disc.skipped_null_fwd_ret += 1
                continue
            if row[i_dn] == "1":
                disc.kept_exit_limit_down += 1          # Q11：賣不掉仍是真實損失，保留
            er = row[i_er]
            if er == "halt":
                disc.kept_halt += 1
            elif er == "delist":
                disc.kept_delist += 1
            disc.rows_counted += 1
            acc.setdefault((row[i_mk], row[i_hz], int(kw)), array("d")).append(net_ret(float(fr)))


def summarise(acc: dict) -> list[dict[str, Any]]:
    """每卦一列：`n`／均值／中位數／兩個標示欄。排名與分組另外做（要逐格重排）。"""
    out = []
    for (mk, hz, kw), vals in acc.items():
        n = len(vals)
        mean = statistics.fmean(vals)
        med = statistics.median(vals)
        # 分位數（裁定 #61）：異號卦佔比高（實測 tpex/mid 過半），p25／p75 讓凍結後看得出離散程度。
        # `method="inclusive"` ＝ numpy 的 linear 口徑，n>=2 即可；n<2 時退為該值本身（不拋例外）。
        if n >= 2:
            q = statistics.quantiles(vals, n=4, method="inclusive")
            p25, p75 = q[0], q[2]
        else:
            p25 = p75 = (vals[0] if n else 0.0)
        out.append({
            "market": mk, "horizon": hz, "king_wen": kw, "n": n,
            "mean": mean, "median": med, "p25": p25, "p75": p75,
            # 兩條**獨立**規則，標示必須分得開（驗收 R4）
            "flag_small_n": n < SMALL_N,
            # 均值與中位數一正一負。0 不算異號（0 既非正也非負），故用乘積 < 0
            "flag_sign_mismatch": mean * med < 0.0,
        })
    return out


def rank_and_group(rows: list[dict[str, Any]]) -> None:
    """逐 (market, horizon) 格排名與分組，**就地**寫入 `rank`／`group`。

    **裁定 #61（改回規格字面）**：只有 `n<500` 的卦不參與排名（`docs/P3-CALIBRATION.md:163`）；
    均值與中位數**異號**的卦**照常排名**，只是「不進高／低組」（`:564-565`）→ 強制歸中組。
    所以 `M`＝64 − 該格 `n<500` 的卦數，高／低組各取**前／後 `floor(M/3)` 名**，
    但**落在那個名次區間裡的異號卦會被拉回中組**，因此高組實際人數 ≤ `floor(M/3)`、且不可預測。

    > 裁定 #60 原本把異號卦一併排除在 `M` 之外；實測兩種讀法的高組名單差很多
    > （tpex/short 15 vs 3）且方向不一致（short 是排除版多、mid 是字面版多），
    > 使用者看過實證後改回字面。此處保留這段沿革，免得日後有人又「順手修正」回去。

    排序帶次鍵 `king_wen`：均值同值時若不帶次鍵，名次會隨 dict 走訪序飄
    （`budget.py`／`sectors.py` 那條「排序一律帶次鍵」的家族教訓）。
    """
    cells: dict[tuple[str, str], list[dict]] = {}
    for r in rows:
        cells.setdefault((r["market"], r["horizon"]), []).append(r)
    for cell in cells.values():
        ranked = [r for r in cell if not r["flag_small_n"]]
        ranked.sort(key=lambda r: (-r["mean"], r["king_wen"]))
        m = len(ranked)
        cut = m // 3
        for i, r in enumerate(ranked):
            r["rank"] = i + 1
            if r["flag_sign_mismatch"]:
                r["group"] = "mid"          # 照常排名，但不進高／低組
            else:
                r["group"] = "high" if i < cut else ("low" if i >= m - cut else "mid")
        for r in cell:
            if r.get("rank") is None:       # n<500：不參與排名、歸中組
                r["rank"], r["group"] = None, "mid"


def build(data_dir: Path) -> dict[str, Any]:
    man_path = data_dir / "manifest.json"
    if not man_path.exists():
        raise RankTableError(f"找不到 {man_path}")
    man = json.loads(man_path.read_text(encoding="utf-8"))
    acc: dict = {}
    disc = Disclosure()
    files = []
    for hz in HORIZONS:
        p = data_dir / f"{SEGMENT}_{hz}.csv.gz"
        if not p.exists():
            raise RankTableError(f"找不到 {p}")
        files.append(p.name)
        print(f"  讀 {p.name} …", flush=True)
        scan_file(p, acc, disc)
    rows = summarise(acc)
    rank_and_group(rows)
    rows.sort(key=lambda r: (r["market"], r["horizon"], r["king_wen"]))
    seg = (man.get("segments") or {}).get(SEGMENT) or {}
    return {
        "schema": 1,
        "generated_from": {
            "segment": SEGMENT, "files": files,
            "data_version": man.get("data_version"), "params_sha": man.get("params_sha"),
            "model_version": man.get("model_version"), "head": man.get("head"),
            "window": man.get("window"), "pool_semantics": man.get("pool_semantics"),
            "segment_from": seg.get("from"), "segment_to": seg.get("to"),
            "h_by_horizon": man.get("h_by_horizon"),
        },
        "cost": {"fee": FEE, "tax": TAX, "slippage": SLIP, "zero_fwd_ret_net": ZERO_FWD_NET,
                 "note": "net=(1+fwd)*(1-s)*(1-f-t)/((1+s)*(1+f))-1；滑價一律 0.2% 不分層，"
                         "對低流動性個股偏樂觀（裁定 #57 Q12）"},
        "rules": {"small_n_threshold": SMALL_N, "group_denominator": "ranked_excludes_small_n_only",
                  "group_rounding": "floor", "rank_key": "mean_desc_then_king_wen",
                  "sign_mismatch_excluded_from_rank": False,
                  "sign_mismatch_forced_to_mid": True},
        "disclosure": disc.as_dict(),
        "rows": rows,
    }


def _pct(x: float | None) -> str:
    return "—" if x is None else f"{x * 100:.3f}%"


def as_markdown(rep: dict[str, Any]) -> str:
    g, c, d = rep["generated_from"], rep["cost"], rep["disclosure"]
    L = ["## 附錄 A：卦別分組排序表（裁定 #54 Q6／#57 Q9–Q12／#60／#61）", "",
         "**本節由 `scripts/rank_table.py` 生成，不得手改**（重跑會整段覆寫）。",
         f"資料：`{g['segment']}` 段 {g['segment_from']}～{g['segment_to']}，"
         f"`data_version={g['data_version']}`、`params_sha={g['params_sha']}`、`head={g['head']}`、"
         f"`window={g['window']}`、`pool_semantics={g['pool_semantics']}`。", ""]
    L += ["### 判準", "",
          f"- 淨報酬＝`(1+fwd_ret)×(1−s)(1−f−t)/[(1+s)(1+f)]−1`，`f={c['fee']}`、`t={c['tax']}`（只賣出）、"
          f"`s={c['slippage']}`（單邊）；**`fwd_ret=0` 時為 {c['zero_fwd_ret_net'] * 100:.3f}%**",
          "- 排名依**均值**降冪（同值次鍵 `king_wen`），中位數並列作穩健性對照",
          f"- `n<{SMALL_N}` 的卦**不參與排名**、歸中組並標示",
          "- 均值與中位數**異號**的卦另標示，且不進高／低組",
          "- 高／低組各取前／後 `floor(M/3)` 名，`M`＝64 −「該格 `n<500` 的卦數」；"
          "**異號卦照常排名，但落在該區間時會被拉回中組**，故高組實際人數 ≤ `floor(M/3)`"
          "（裁定 #61 改回規格字面；裁定 #60 原本把異號卦排除在 `M` 外，實測兩種讀法的"
          "高組名單差很多且方向不一致，故改）", ""]
    L += ["### 揭露（驗收 R6）", "",
          f"- 讀入 {d['rows_read']:,} 列；計入 **{d['rows_counted']:,}** 列",
          f"- **排除** `entry_limit_up=1`（買不到）**{d['excluded_entry_limit_up']:,}** 列",
          f"- **不計入 n**：空 `king_wen` {d['skipped_empty_king_wen']:,} 列、"
          f"`fwd_ret` 空 {d['skipped_null_fwd_ret']:,} 列",
          f"- **保留**（賣不掉／停牌／下市都是真實損失）：`exit_limit_down=1` "
          f"{d['kept_exit_limit_down']:,} 列、`halt` {d['kept_halt']:,} 列、`delist` {d['kept_delist']:,} 列",
          f"- 成本率：`fwd_ret=0` 時淨報酬 **{c['zero_fwd_ret_net'] * 100:.3f}%**",
          "- **滑價一律 0.2%、不分層，對低流動性個股偏樂觀**（裁定 #57 Q12：分層等於引入"
          "一組沒有回測依據的新參數，刻意不做）", ""]
    # ⚠ 高組不足額而低組滿額，是這份資料的結構特徵，讀表前必須先講清楚
    cells: dict[str, list[int]] = {}
    for r in rep["rows"]:
        k = f"{r['market']}/{r['horizon']}"
        c = cells.setdefault(k, [0, 0, 0])          # [high, low, M]
        c[0] += r["group"] == "high"
        c[1] += r["group"] == "low"
        c[2] += r["rank"] is not None
    worst = min(cells.items(), key=lambda kv: kv[1][0])
    cuts = sorted({v[2] // 3 for v in cells.values()})
    cut_txt = str(cuts[0]) if len(cuts) == 1 else "／".join(map(str, cuts))
    L += ["### ⚠ 高組與低組**不對稱**（讀表前必看）", "",
          f"六格的 `floor(M/3)` 為 {cut_txt}，但**高組普遍不足額、低組多為滿額**"
          f"（最少的一格 `{worst[0]}` 高組只有 **{worst[1][0]}** 卦、低組 {worst[1][1]} 卦）。", "",
          "原因是結構性的、**不是訊號強弱**：均值與中位數異號的卦**系統性集中在排名前段**——"
          "報酬分布右偏（少數大漲把均值拉正、中位數仍負）正是「均值高」與「異號」的共同成因，"
          "所以前 `floor(M/3)` 名裡有大量異號卦被拉回中組；而均值最低的那一端多是普遍下跌、"
          "中位數同號，不觸發這條規則。**把高組卦數讀成「這個市場／期間的強訊號較少」是錯的。**", "",
          "| 格 | 參與排名 M | `floor(M/3)` | 高組 | 低組 |", "|---|---:|---:|---:|---:|"]
    L += [f"| {k} | {v[2]} | {v[2] // 3} | {v[0]} | {v[1]} |" for k, v in sorted(cells.items())]
    L.append("")
    for mk in sorted({r["market"] for r in rep["rows"]}):
        for hz in HORIZONS:
            cell = [r for r in rep["rows"] if r["market"] == mk and r["horizon"] == hz]
            if not cell:
                continue
            m = sum(1 for r in cell if r["rank"] is not None)
            cut = m // 3
            n_hi = sum(1 for r in cell if r["group"] == "high")
            n_lo = sum(1 for r in cell if r["group"] == "low")
            n_sm = sum(1 for r in cell if r["flag_sign_mismatch"])
            L += [f"### {mk} · {hz}（h={(g.get('h_by_horizon') or {}).get(hz)} 交易日）", "",
                  f"參與排名 {m} 卦（未參與 {len(cell) - m}）→ `floor(M/3)`＝{cut}；"
                  f"扣掉落在該區間的異號卦後，**高組 {n_hi}／低組 {n_lo}／中組 {len(cell) - n_hi - n_lo}**；"
                  f"該格異號卦共 {n_sm} 個。", "",
                  "| 卦 | n | 均值 | 中位數 | p25 | p75 | 名次 | 組 | n<500 | 均值中位數異號 |",
                  "|---:|---:|---:|---:|---:|---:|---:|---|---|---|"]
            for r in sorted(cell, key=lambda r: (r["rank"] is None, r["rank"] or 0, r["king_wen"])):
                L.append(f"| {r['king_wen']} | {r['n']:,} | {_pct(r['mean'])} | {_pct(r['median'])} | "
                         f"{_pct(r['p25'])} | {_pct(r['p75'])} | "
                         f"{r['rank'] if r['rank'] is not None else '—'} | {r['group']} | "
                         f"{'●' if r['flag_small_n'] else ''} | {'●' if r['flag_sign_mismatch'] else ''} |")
            L.append("")
    return "\n".join(L) + "\n"


BEGIN = "<!-- BEGIN rank_table (generated by scripts/rank_table.py — do not hand-edit) -->"
END = "<!-- END rank_table -->"


def splice_markdown(doc: str, block: str) -> str:
    """把附錄整段換進 `docs/pre-registration.md`：有 marker 就替換、沒有就附到檔尾。**冪等**。"""
    new = f"{BEGIN}\n{block}{END}\n"
    i, j = doc.find(BEGIN), doc.find(END)
    if i >= 0 and j > i:
        return doc[:i] + new + doc[j + len(END) + 1:]
    if i >= 0 or j >= 0:
        raise RankTableError("pre-registration.md 的 rank_table marker 只剩一半，請人看")
    return doc.rstrip("\n") + "\n\n" + new


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="卦別分組排序表（裁定 #54 Q6／#57／#60）")
    ap.add_argument("--data-dir", default=str(REPO / "data" / "backtest"))
    ap.add_argument("--out-json", default=str(REPO / "data" / "rank_table.json"))
    ap.add_argument("--out-md", default=str(REPO / "docs" / "pre-registration.md"),
                    help="附錄 A 寫進這個檔的 marker 區塊；--no-md 可略過")
    ap.add_argument("--no-md", action="store_true", help="只產 JSON（測試用）")
    args = ap.parse_args(argv)
    try:
        print(f"== rank_table  segment={SEGMENT}（**不讀驗證段**）")
        rep = build(Path(args.data_dir))
        out = Path(args.out_json)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(rep, ensure_ascii=False, sort_keys=True,
                                  separators=(",", ":"), allow_nan=False) + "\n", encoding="utf-8")
        print(f"== JSON {out}（{len(rep['rows'])} 列）")
        if not args.no_md:
            md = Path(args.out_md)
            md.write_text(splice_markdown(md.read_text(encoding="utf-8"), as_markdown(rep)),
                          encoding="utf-8")
            print(f"== 附錄 A 已寫入 {md}")
        d = rep["disclosure"]
        print(f"== 計入 {d['rows_counted']:,} 列／讀入 {d['rows_read']:,} 列；"
              f"排除 entry_limit_up {d['excluded_entry_limit_up']:,}；"
              f"fwd_ret=0 時淨報酬 {ZERO_FWD_NET * 100:.3f}%")
        return 0
    except (RankTableError, OSError, ValueError, KeyError) as e:
        print(f"[rank_table 中止] {type(e).__name__}: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
