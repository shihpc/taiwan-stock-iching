"""P3 回測統計層（PR-S2）：讀 `data/backtest/<segment>_<horizon>.csv.gz` 三檔 → 逐 (market, horizon) 格跑登錄書 §1 的
統計 → 寫 `runs/backtest/<segment>_<date>.{json,txt}`。

    scratchpad/venv312/bin/python scripts/backtest_stats.py --segment valid [--peer runs/backtest/train_<date>.json]

## 判準出處（一律不憑印象；行號為 `docs/pre-registration.md` 凍結版 tag `prereg-v2`＝`59e03f1`）

- §1.1 `:42-67` 兩市場各算、三段切點；§1.2.1 `:87` h＝10／20／40、`:91` 成本費率；
- §1.3 `:266-270` purge 索引法 `e=i+1`／`x=i+1+h`、embargo 20 交易日、NW、循環區塊 `max(21,3h)`、區塊數量近似值 <8 → 證據不足；
- §1.4 `:274-302` 主要（IC ≥ 0.03 且 NW t ≥ 2.0，lag h／2h）、次要①②、穩健、只報三項、T5 有效日數公式、T3 波段／中期必然證據不足；
- §1.5 `:306-324` K＝216 只揭露不校正；§1.6 `:331-341` 卦別排序沿用 `data/rank_table.json`、不重排；
- v1.2.2 §13.3 `:573`：bootstrap 1,000 次、h=40 另報區塊 126 日敏感度；
- **裁定 #72 Q9～Q17（`docs/P3-KICKOFF.md` §5b，commit `c1240f2`（#111 squash；裁定內容於 `dec6cff` 02:04Z 登錄、早於本腳本產物生成 02:34Z；squash 早於產物 commit）**：IC 母體只算 `in_rank_pool=1`（Q9）、
  `entry_limit_up=1` 整列排除且 raw／歸屬兩組計數、`halt`／`delist` 保留、`no_entry` 的空 `fwd_ret` 落掉並計數（Q10）、
  IC 用原始 `fwd_ret`、次要①②與分組用 `net_ret_long`（slip 0.2% 基準）、空方 `net_ret_short`（借券 2%，曆日/365）（Q11）、
  日內有效配對 <30 不進序列（Q12）、seed 42（Q13）、次要②候選名單＝頂十分位代理（Q14）、tpex 大盤不評估（Q15）、
  產物位置（Q16）、十分位先換平均秩再切、揭露同分比例（Q17）。

## 列處理順序（每一步的計數都進 json `cells[*].rows`，另有獨立母體 `raw`）

檔案列 → ①`in_rank_pool=1`（Q9）→ ②評估窗：段內、段末 purge（索引法）、段起 embargo（§1.3；訓練段 embargo 0）
→ ③`entry_limit_up=1` 整列排除（Q10）→ ④`fwd_ret` 空落掉（`no_entry`／超出資料末日）→ ⑤`base_score` 空落掉
（`in_rank_pool=1` 但無分數的 `coverage=reweighted` 列，`docs/P3-DATASET.md` §6）→ ⑥保留列：`halt`／`delist`／`exit_limit_down`
計數、`coverage=reweighted` 只計比例不過濾 → ⑦逐日 Spearman：有效配對 <30 的日子不進序列並計數（Q12）。
③④的先後**不改變任何數字**（`rank_table.py` 已實測兩者交集為 0），沿該腳本把「`entry_limit_up` 整列排除」排在「沒有報酬」之前。
`raw.*` 是在 ①②之後的母體上**各自獨立數一次**，與③④⑤的先後無關（`rank_table.Disclosure` 的教訓：只給歸屬數會被讀成母體）。

## 判定邏輯一律呼叫 `iching.stats.*` 的純函式

本檔只做讀檔、分組、組裝、輸出：Spearman／NW／bootstrap／purge／embargo／成本式／分組／verdict 全部來自 PR-S1 套件
（`src/iching/stats/`），不在這裡重寫。成本式 `net_ret_long` 直接以 numpy 陣列呼叫（純算術、duck typing），`net_ret_short`
因內含標量守門改以 `np.vectorize` 逐列呼叫——都是同一支函式，不是複本。

## 守門（任一不符 rc=2、不產檔）

manifest `params_sha`＝`cb3f2d905846`、`pool_semantics`＝`pit-1`、`h_by_horizon`＝{short 10, swing 20, mid 40}、三檔 sha256＝manifest
所記、檔案層計數（列數／`entry_limit_up`／`fwd_ret` 缺／halt／delist）＝manifest、`data/rank_table.json` sha256＝凍結釘值、
表頭＝`export_dataset.COLUMNS`、段內日期全部在日曆上。**`--segment` 只收 `train`／`valid`：保留段不在本批，本檔沒有任何讀
`holdout_*` 的路徑**（`SEGMENTS` 常數只有兩段；`iching.config.SEGMENTS` 的 `holdout` 條目在此不被引用）。

## 快取與資源

逐檔 `csv`＋`gzip` 串流解析進預配 numpy 陣列（不建 list of dict），解析結果快取為 `cache/backtest_<file>.npz`（鍵含該檔 sha256，
檔變即失效；`cache/` 不進 git）。耗時／峰值 RSS（`resource.getrusage`）／numpy 版本寫進 json `meta`。
本檔不 import 日期時間模組：借券費的曆日數由 `_days_from_civil`（純算術）算，purge／embargo 全走交易日索引（§16.5 `:722`）。

rc：0 成功／2 中止（守門不過、檔案缺、任何例外）。
"""
from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import json
import platform
import resource
import sys
import time
from pathlib import Path
from typing import Any

import numpy as np

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "src"))

from iching.stats import boot, groups, ic, metrics, verdict, windows
from iching.stats.constants import (
    BLOCKS_MIN,
    DEFAULT_SEED,
    EMBARGO_DAYS,
    H_BY_HORIZON,
    IC_MIN,
    NBOOT,
    POS_SHARE_MIN,
    T_MIN,
    block_len,
)
from iching.stats.cost import (
    BORROW_BASE,
    BORROW_GRID,
    DAYS_PER_YEAR,
    FEE,
    SLIP_BASE,
    SLIP_GRID,
    TAX,
    ZERO_FWD_NET,
    net_ret_long,
    net_ret_short,
)

SCHEMA = 1
#: 本批只有兩段（保留段不在 PR-S2；`docs/P3-KICKOFF.md` §5b 連帶既定）。值＝`iching.config.SEGMENTS` 的前兩條，啟動時互核。
SEGMENTS: dict[str, tuple[str, str]] = {"train": ("2021-01-01", "2023-06-30"), "valid": ("2023-07-01", "2024-12-31")}
MARKETS = ("twse", "tpex")
HORIZONS = ("short", "swing", "mid")
COLUMNS = ("date", "market", "stock_id", "horizon", "base_score", "in_rank_pool", "coverage", "king_wen", "lines_formal",
           "fwd_ret", "mkt_ret_h", "exit_reason", "entry_limit_up", "exit_limit_down")
#: 開跑守門的釘值（登錄書 §0 `:22-23`；`tests/test_prereg_frozen.py` `FROZEN["v2"]["rank_sha256"]` 同值，測試互核）
EXPECTED_PARAMS_SHA = "cb3f2d905846"
EXPECTED_POOL_SEMANTICS = "pit-1"
RANK_TABLE_SHA256 = "0c4039de3333ac10e4cfe0e4eb1662ee48ad718af0edb81a71c7bcc6d20fda49"
#: 裁定 #72 Q12
MIN_PAIRS = 30
#: v1.2.2 `:573`：40／60 日另報區塊 126 日敏感度（本層 h=40 的區塊長 120）
BLOCK_SENSITIVITY_H = 40
BLOCK_SENSITIVITY = 126
N_GROUPS = 10
EXIT_CODES = {"": 0, "no_entry": 1, "halt": 2, "delist": 3}
COVERAGE_CODES = {"full": 0, "reweighted": 1}
#: 逐年角色（v1.2.2 §13.2 `:566`、§16.5 `:725`）；保留段年份由報告列「未見（未跑）」
YEAR_ROLE = {"train": "訓練", "valid": "調參（驗證段）"}

RULINGS = {
    "Q9": "IC 母體只算 in_rank_pool=1；不另出全池版本（附錄 A 排序表當時未過濾，口徑差異揭露）",
    "Q10": "entry_limit_up=1 整列排除（raw／歸屬兩組計數）；halt／delist 保留；no_entry 的空 fwd_ret 落掉並計數",
    "Q11": "IC 用原始 fwd_ret；次要①②與分組用 net_ret_long（slip 0.2%，敏感度 0.1／0.3）；空方 net_ret_short（借券 2%，1／4；曆日/365）",
    "Q12": f"某日某格有效配對 <{MIN_PAIRS} → 該日不進 IC 序列，計數揭露",
    "Q13": f"bootstrap seed={DEFAULT_SEED}",
    "Q14": "次要②候選名單＝頂十分位代理（與次要①共用；入場門檻 T0 未定案，代理定義、登錄書未定）",
    "Q15": "tpex 大盤側：登錄書 §1.2.1 只寫 TX → 標「無可交易代理、不評估」",
    "Q16": "產物 runs/backtest/<segment>_<date>.{json,txt}＋docs/P3-BACKTEST-VALID.md；中間檔只放 cache/",
    "Q17": "十分位切組前先換平均秩（同分同秩）再切，揭露同分比例；不得用列序決定同分歸組",
}


class BacktestStatsError(Exception):
    pass


def _assert(cond: bool, what: str) -> None:
    if not cond:
        raise BacktestStatsError(what)


# ---------------------------------------------------------------- 曆日（純算術，不 import 日期時間模組）

def _days_from_civil(y: int, m: int, d: int) -> int:
    """公曆日期 → 連續日序（Howard Hinnant 的 days_from_civil）。只用於借券費的曆日數（Q11 曆日/365）。"""
    y -= m <= 2
    era = (y if y >= 0 else y - 399) // 400
    yoe = y - era * 400
    doy = (153 * (m + (-3 if m > 2 else 9)) + 2) // 5 + d - 1
    doe = yoe * 365 + yoe // 4 - yoe // 100 + doy
    return era * 146097 + doe - 719468


def _iso_to_ordinal(s: str) -> int:
    return _days_from_civil(int(s[0:4]), int(s[5:7]), int(s[8:10]))


# ---------------------------------------------------------------- 讀檔

def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def load_calendar(path: Path) -> list[str]:
    cal = json.loads(path.read_text(encoding="utf-8"))["dates"]
    _assert(cal == sorted(cal) and len(set(cal)) == len(cal), f"{path} 日曆未排序或有重複")
    return cal


def parse_file(path: Path, n_rows: int, cal_pos: dict[str, int]) -> dict[str, np.ndarray]:
    """串流解析一個 `<segment>_<horizon>.csv.gz` 進預配陣列。空字串 → NaN（浮點欄）／−1（整數欄）。"""
    date = np.empty(n_rows, np.int32)
    market = np.empty(n_rows, np.int8)
    score = np.empty(n_rows, np.float64)
    pool = np.empty(n_rows, np.int8)
    cov = np.empty(n_rows, np.int8)
    kw = np.empty(n_rows, np.int16)
    fwd = np.empty(n_rows, np.float64)
    exit_r = np.empty(n_rows, np.int8)
    lim_up = np.empty(n_rows, np.int8)
    lim_dn = np.empty(n_rows, np.int8)
    i_dt, i_mk, i_hz = COLUMNS.index("date"), COLUMNS.index("market"), COLUMNS.index("horizon")
    i_sc, i_pl, i_cv = COLUMNS.index("base_score"), COLUMNS.index("in_rank_pool"), COLUMNS.index("coverage")
    i_kw, i_fr, i_er = COLUMNS.index("king_wen"), COLUMNS.index("fwd_ret"), COLUMNS.index("exit_reason")
    i_up, i_dn = COLUMNS.index("entry_limit_up"), COLUMNS.index("exit_limit_down")
    mk_code = {"twse": 0, "tpex": 1}
    hz_expected = path.name.split("_", 1)[1].split(".")[0]
    n = 0
    with gzip.open(path, "rt", encoding="utf-8", newline="") as fh:
        r = csv.reader(fh)
        head = next(r, None)
        _assert(head is not None and tuple(head) == COLUMNS, f"{path.name} 表頭與 export_dataset.COLUMNS 不符：{head}")
        for row in r:
            _assert(n < n_rows, f"{path.name} 列數超過 manifest 的 n_rows={n_rows}")
            _assert(row[i_hz] == hz_expected, f"{path.name} 第 {n + 2} 行 horizon={row[i_hz]!r} ≠ 檔名")
            p = cal_pos.get(row[i_dt])
            _assert(p is not None, f"{path.name} 第 {n + 2} 行 date={row[i_dt]} 不在日曆上")
            date[n] = p
            market[n] = mk_code[row[i_mk]]
            v = row[i_sc]
            score[n] = float(v) if v else np.nan
            pool[n] = 1 if row[i_pl] == "1" else 0
            cov[n] = COVERAGE_CODES[row[i_cv]]
            v = row[i_kw]
            kw[n] = int(v) if v else -1
            v = row[i_fr]
            fwd[n] = float(v) if v else np.nan
            exit_r[n] = EXIT_CODES[row[i_er]]
            v = row[i_up]
            lim_up[n] = -1 if not v else (1 if v == "1" else 0)
            v = row[i_dn]
            lim_dn[n] = -1 if not v else (1 if v == "1" else 0)
            n += 1
    _assert(n == n_rows, f"{path.name} 列數 {n} ≠ manifest n_rows={n_rows}")
    return {"date": date, "market": market, "score": score, "pool": pool, "cov": cov, "kw": kw, "fwd": fwd,
            "exit": exit_r, "lim_up": lim_up, "lim_dn": lim_dn}


def load_file(path: Path, n_rows: int, sha: str, cal_pos: dict[str, int], cache_dir: Path | None,
              cache_used: list[str] | None = None) -> dict[str, np.ndarray]:
    """有快取且 sha 相同就讀 npz，否則解析後寫快取（寫失敗不中止）。命中快取的檔名收進 `cache_used`（寫進 json meta）。"""
    cache = (cache_dir / f"backtest_{path.name.split('.')[0]}.npz") if cache_dir else None
    if cache and cache.exists():
        try:
            z = np.load(cache, allow_pickle=False)
            if str(z["sha256"]) == sha and str(z["calendar_sha"]) == _cal_sig(cal_pos):
                if cache_used is not None:
                    cache_used.append(path.name)
                return {k: z[k] for k in ("date", "market", "score", "pool", "cov", "kw", "fwd", "exit", "lim_up", "lim_dn")}
        except Exception as e:  # noqa: BLE001 — 快取壞掉就重新解析（只印一行，不中止）
            print(f"  快取 {cache.name} 不可用（{type(e).__name__}），重新解析", flush=True)
    arrs = parse_file(path, n_rows, cal_pos)
    if cache:
        try:
            cache.parent.mkdir(parents=True, exist_ok=True)
            np.savez(cache, sha256=np.array(sha), calendar_sha=np.array(_cal_sig(cal_pos)), **arrs)
        except OSError:
            pass
    return arrs


def _cal_sig(cal_pos: dict[str, int]) -> str:
    return hashlib.sha256("\n".join(cal_pos).encode("utf-8")).hexdigest()[:16]


# ---------------------------------------------------------------- 守門

def check_manifest(man: dict[str, Any], data_dir: Path, segment: str) -> dict[str, Any]:
    _assert(man.get("params_sha") == EXPECTED_PARAMS_SHA,
            f"manifest params_sha={man.get('params_sha')!r} ≠ {EXPECTED_PARAMS_SHA}（不是登錄書 v2 凍結的指紋）")
    _assert(man.get("pool_semantics") == EXPECTED_POOL_SEMANTICS,
            f"manifest pool_semantics={man.get('pool_semantics')!r} ≠ {EXPECTED_POOL_SEMANTICS}")
    _assert(man.get("h_by_horizon") == dict(H_BY_HORIZON),
            f"manifest h_by_horizon={man.get('h_by_horizon')} ≠ {dict(H_BY_HORIZON)}")
    _assert(tuple(man.get("columns") or ()) == COLUMNS, "manifest columns 與 COLUMNS 不符")
    files = man.get("files") or {}
    out = {}
    for hz in HORIZONS:
        name = f"{segment}_{hz}.csv.gz"
        _assert(name in files, f"manifest 沒有 {name}")
        p = data_dir / name
        _assert(p.exists(), f"找不到 {p}")
        got = sha256_file(p)
        _assert(got == files[name].get("sha256"), f"{name} sha256 {got[:12]}… ≠ manifest {str(files[name].get('sha256'))[:12]}…")
        out[name] = {"sha256": got, "n_rows": int(files[name]["n_rows"]), "manifest": files[name]}
    return out


def check_rank_table(path: Path) -> dict[str, Any]:
    _assert(path.exists(), f"找不到 {path}")
    raw = path.read_bytes()
    got = hashlib.sha256(raw).hexdigest()
    _assert(got == RANK_TABLE_SHA256, f"{path.name} sha256 {got[:12]}… ≠ 凍結釘值 {RANK_TABLE_SHA256[:12]}…（§1.6 排序表被改動）")
    table = json.loads(raw.decode("utf-8"))
    return {"sha256": got, "rows": table["rows"]}


def rank_groups(rows: list[dict], market: str, horizon: str) -> dict[int, str]:
    """`king_wen → group` 只照表套用（§1.6、§16.5 `:727`）；本檔沒有任何排序邏輯。"""
    return {int(r["king_wen"]): str(r["group"]) for r in rows if r["market"] == market and r["horizon"] == horizon}


def check_file_counts(arrs: dict[str, np.ndarray], mf: dict[str, Any], name: str) -> dict[str, int]:
    """檔案層計數＝manifest（擋「讀錯檔」與解析錯位）。"""
    counts = {
        "n_rows": int(arrs["date"].size),
        "n_entry_limit_up": int((arrs["lim_up"] == 1).sum()),
        "n_exit_limit_down": int((arrs["lim_dn"] == 1).sum()),
        "n_fwd_ret_missing": int(np.isnan(arrs["fwd"]).sum()),
        "halt": int((arrs["exit"] == EXIT_CODES["halt"]).sum()),
        "delist": int((arrs["exit"] == EXIT_CODES["delist"]).sum()),
        "no_entry": int((arrs["exit"] == EXIT_CODES["no_entry"]).sum()),
    }
    want = {"n_rows": mf["n_rows"], "n_entry_limit_up": mf["n_entry_limit_up"], "n_exit_limit_down": mf["n_exit_limit_down"],
            "n_fwd_ret_missing": mf["n_fwd_ret_missing"]}
    want.update({k: mf["exit_reason_counts"].get(k, 0) for k in ("halt", "delist", "no_entry")})
    for k, v in want.items():
        _assert(counts[k] == int(v), f"{name} 檔案層計數 {k}={counts[k]} ≠ manifest {v}")
    return counts


# ---------------------------------------------------------------- 每格

def _sign(x: float) -> int:
    if not np.isfinite(x) or x == 0.0:
        return 0
    return 1 if x > 0 else -1


def _ci(lo: float, hi: float) -> list[float]:
    return [float(lo), float(hi)]


def _sec_pass(pos_share: float, excess_mean: float, ci: tuple[float, float]) -> bool:
    return verdict.secondary_pass(pos_share, excess_mean, ci[0], ci[1])


def run_cell(arrs: dict[str, np.ndarray], market: str, horizon: str, segment: str, cal: list[str],
             seg_pos: tuple[int, int], rank_rows: list[dict]) -> dict[str, Any]:
    h = H_BY_HORIZON[horizon]
    embargo = 0 if segment == "train" else EMBARGO_DAYS
    p0, p1 = seg_pos
    mk = {"twse": 0, "tpex": 1}[market]
    n_seg_days = p1 - p0

    # ---- ①池 ②評估窗（索引法）
    in_file = arrs["market"] == mk
    in_pool = in_file & (arrs["pool"] == 1)
    pos = arrs["date"]
    in_seg = in_pool & (pos >= p0) & (pos < p1)
    purged = in_seg & windows.purge_mask(pos, h, p1)
    embargoed = in_seg & windows.embargo_mask(pos, p0, embargo)
    win = in_pool & windows.eval_mask(pos, p0, p1, h, embargo)
    _assert(int(in_pool.sum()) == int(in_seg.sum()), f"{market}/{horizon}：有池內列落在段外")
    seg_idx = np.arange(p0, p1)
    purge_days_idx = seg_idx[windows.purge_mask(seg_idx, h, p1)]
    embargo_days_idx = seg_idx[windows.embargo_mask(seg_idx, p0, embargo)]
    elig_idx = seg_idx[windows.eval_mask(seg_idx, p0, p1, h, embargo)]
    _assert(purge_days_idx.size == windows.purged_signal_days_index(h), "purge 日數 ≠ h+1")
    window = {
        "seg_start": cal[p0], "seg_end": cal[p1 - 1], "seg_days": int(n_seg_days),
        "boundary_date": cal[p1] if p1 < len(cal) else None, "boundary_pos": int(p1),
        "purge_days": int(purge_days_idx.size), "purge_first_day": cal[int(purge_days_idx[0])],
        "purge_last_day": cal[int(purge_days_idx[-1])],
        "embargo_days": int(embargo_days_idx.size),
        "embargo_last_day": cal[int(embargo_days_idx[-1])] if embargo_days_idx.size else None,
        "first_day_eligible": cal[int(elig_idx[0])], "last_day_eligible": cal[int(elig_idx[-1])],
        "first_pos_eligible": int(elig_idx[0]), "last_pos_eligible": int(elig_idx[-1]),
        "n_eff_index": int(elig_idx.size), "n_eff_t5": int(n_seg_days - h - embargo),
        "n_blocks_t5": float(boot.block_count(n_seg_days, h, embargo)),
        "n_blocks_index": float(elig_idx.size / block_len(h)),
        "block_len": int(block_len(h)),
    }

    # ---- 獨立母體（①②之後）
    raw = {
        "entry_limit_up": int((arrs["lim_up"][win] == 1).sum()),
        "null_fwd_ret": int(np.isnan(arrs["fwd"][win]).sum()),
        "null_base_score": int(np.isnan(arrs["score"][win]).sum()),
        "exit_limit_down": int((arrs["lim_dn"][win] == 1).sum()),
        "halt": int((arrs["exit"][win] == EXIT_CODES["halt"]).sum()),
        "delist": int((arrs["exit"][win] == EXIT_CODES["delist"]).sum()),
        "no_entry": int((arrs["exit"][win] == EXIT_CODES["no_entry"]).sum()),
        "reweighted": int((arrs["cov"][win] == 1).sum()),
        "null_king_wen": int((arrs["kw"][win] < 0).sum()),
    }
    # ---- ③④⑤ 歸屬（順序：entry_limit_up → fwd 空 → base_score 空）
    step = win.copy()
    ex_up = step & (arrs["lim_up"] == 1)
    step &= ~ex_up
    ex_fwd = step & np.isnan(arrs["fwd"])
    step &= ~ex_fwd
    ex_sc = step & np.isnan(arrs["score"])
    step &= ~ex_sc
    keep = step
    n_keep = int(keep.sum())
    rows = {
        "in_file": int(in_file.sum()), "in_pool": int(in_pool.sum()), "out_of_pool": int((in_file & ~in_pool).sum()),
        "purged_rows": int(purged.sum()), "embargo_rows": int((embargoed & ~purged).sum()),
        "in_window": int(win.sum()), "raw": raw,
        "excluded_entry_limit_up": int(ex_up.sum()), "skipped_null_fwd_ret": int(ex_fwd.sum()),
        "skipped_null_base_score": int(ex_sc.sum()), "counted": n_keep,
        "kept_halt": int((arrs["exit"][keep] == EXIT_CODES["halt"]).sum()),
        "kept_delist": int((arrs["exit"][keep] == EXIT_CODES["delist"]).sum()),
        "kept_exit_limit_down": int((arrs["lim_dn"][keep] == 1).sum()),
        "reweighted": int((arrs["cov"][keep] == 1).sum()),
        "reweighted_share": float((arrs["cov"][keep] == 1).sum() / n_keep) if n_keep else float("nan"),
        "null_king_wen_kept": int((arrs["kw"][keep] < 0).sum()),
    }
    _assert(rows["in_window"] == rows["excluded_entry_limit_up"] + rows["skipped_null_fwd_ret"]
            + rows["skipped_null_base_score"] + rows["counted"], "歸屬計數加總 ≠ 窗內列數")

    d_pos = pos[keep]
    score = arrs["score"][keep]
    fwd = arrs["fwd"][keep]
    kw = arrs["kw"][keep]
    d_iso = np.asarray(cal, dtype=object)[d_pos].astype(str)

    # ---- 同分比例（Q17）：同日同分
    order = np.lexsort((score, d_pos))
    ds, ss = d_pos[order], score[order]
    same = np.zeros(ds.size, dtype=bool)
    eq = (ds[1:] == ds[:-1]) & (ss[1:] == ss[:-1])
    same[1:] |= eq
    same[:-1] |= eq
    rows["tie_rows"] = int(same.sum())
    rows["tie_share"] = float(same.sum() / n_keep) if n_keep else float("nan")

    # ---- ⑦ 主要：逐日 Spearman（原始 fwd_ret，Q11）
    dic = ic.daily_ic(d_pos, score, fwd, MIN_PAIRS)
    _assert(dic.dropped["nan_pairs"] == 0, "IC 階段仍有 NaN 配對（④⑤應已落掉）")
    ic_arr = dic.ic
    n_days = int(ic_arr.size)
    ic_dates = [cal[int(p)] for p in dic.dates]
    _assert(n_days > 0, f"{market}/{horizon}：IC 序列為空")
    # 進序列的日子 ⊆ 合格訊號日（§16.5 標籤不越界／purge／embargo 的機械證據）
    _assert({int(p) for p in dic.dates} <= {int(p) for p in elig_idx}, "IC 序列含不合格訊號日")
    lag_h, lag_2h = h, 2 * h
    ic_sec = {
        "n_days_used": n_days, "days_below_min_n": int(dic.dropped["days_below_min_n"]),
        "days_degenerate": int(dic.dropped["days_degenerate"]), "nan_pairs": int(dic.dropped["nan_pairs"]),
        "days_eligible_without_rows": int(elig_idx.size - np.unique(d_pos).size),
        "pairs_min": int(dic.n.min()), "pairs_median": float(np.median(dic.n)), "pairs_max": int(dic.n.max()),
        "ic_mean": float(dic.mean), "ic_std": float(np.std(ic_arr, ddof=1)) if n_days > 1 else float("nan"),
        "ic_positive_share": float((ic_arr > 0).mean()),
        "nw_lag_h": lag_h, "nw_se_h": boot.nw_se_guarded(ic_arr, lag_h), "nw_t_h": boot.nw_t(ic_arr, lag_h),
        "nw_lag_2h": lag_2h, "nw_se_2h": boot.nw_se_guarded(ic_arr, lag_2h), "nw_t_2h": boot.nw_t(ic_arr, lag_2h),
        "boot_block": int(block_len(h)), "boot_nboot": NBOOT, "boot_seed": DEFAULT_SEED,
        "boot_ci": _ci(*boot.boot_ci(ic_arr, h)),
        "first_day": ic_dates[0], "last_day": ic_dates[-1],
        "dates": ic_dates, "series": [float(v) for v in ic_arr], "pairs": [int(v) for v in dic.n],
    }
    if h == BLOCK_SENSITIVITY_H:
        ic_sec["boot_block_sensitivity"] = BLOCK_SENSITIVITY
        ic_sec["boot_ci_block_sensitivity"] = _ci(*boot.block_boot_ci(ic_arr, BLOCK_SENSITIVITY, NBOOT, DEFAULT_SEED))

    # ---- 次要①②＋空方（net_ret，Q11／Q14／Q17）
    labels_by_day = _daily_labels(d_pos, score)
    top = labels_by_day == N_GROUPS - 1
    bot = labels_by_day == 0
    # 曆日數：進場 e=i+1、名目出場 x=i+1+h（halt／delist 提前出場的真實曆日更短，此處一律用名目日，偏保守）
    cal_ord = np.array([_iso_to_ordinal(s) for s in cal], dtype=np.int64)
    cal_days = cal_ord[d_pos + 1 + h] - cal_ord[d_pos + 1]
    nrs = np.vectorize(net_ret_short, otypes=[float])

    def sec(slip: float) -> dict[str, Any]:
        net = net_ret_long(fwd, slip)                     # 純算術，同一支函式以陣列呼叫
        sp = groups.spread_series(d_iso, score, net, N_GROUPS, MIN_PAIRS, label_fn=groups.group_labels_avg_rank)
        share, pos_m, n_m = metrics.monthly_positive_share(sp.dates, sp.value, "mean")
        ex = groups.excess_series(d_iso, net, top)
        ci_ex = boot.boot_ci(ex.value, h)
        return {
            "slip": slip,
            "secondary1": {"pos_share": float(share), "pos_months": pos_m, "n_months": n_m,
                           "spread_mean": float(sp.mean), "n_days": int(sp.value.size),
                           "days_dropped": int(sp.dropped["days_dropped"])},
            "secondary2": {"excess_mean": float(ex.mean), "boot_ci": _ci(*ci_ex), "n_days": int(ex.value.size),
                           "days_dropped": int(ex.dropped["days_dropped"])},
            "pass": _sec_pass(float(share), float(ex.mean), ci_ex),
            "_sp": sp, "_ex": ex,
        }

    base = sec(SLIP_BASE)

    def short_side(borrow: float) -> dict[str, Any]:
        net_s = nrs(fwd, SLIP_BASE, borrow, cal_days)
        ser = groups.excess_series(d_iso, net_s, bot)      # 底十分位空方淨報酬 − 全池空方等權
        day_mean = _daily_mean(d_iso, net_s, bot)
        ci_s = boot.boot_ci(day_mean.value, h)
        return {"borrow_annual": borrow, "bottom_short_mean": float(day_mean.mean), "boot_ci": _ci(*ci_s),
                "excess_vs_pool_short": float(ser.mean), "n_days": int(day_mean.value.size)}

    short_base = short_side(BORROW_BASE)
    cost_grid = {
        "slip": {},
        "borrow": {},
    }
    for s_ in SLIP_GRID:
        r = base if s_ == SLIP_BASE else sec(s_)
        cost_grid["slip"][f"{s_:.3f}"] = {
            "pos_share": r["secondary1"]["pos_share"], "excess_mean": r["secondary2"]["excess_mean"],
            "boot_ci": r["secondary2"]["boot_ci"], "secondary_pass": r["pass"],
            "flip_vs_base": r["pass"] != base["pass"] or _sign(r["secondary2"]["excess_mean"]) != _sign(base["secondary2"]["excess_mean"]),
        }
    for b_ in BORROW_GRID:
        r = short_base if b_ == BORROW_BASE else short_side(b_)
        cost_grid["borrow"][f"{b_:.2f}"] = {
            "bottom_short_mean": r["bottom_short_mean"], "boot_ci": r["boot_ci"],
            "flip_vs_base": _sign(r["bottom_short_mean"]) != _sign(short_base["bottom_short_mean"]),
        }
    cost_flips = any(v["flip_vs_base"] for v in cost_grid["slip"].values())
    cost_grid["any_slip_flip"] = bool(cost_flips)
    cost_grid["any_borrow_flip"] = bool(any(v["flip_vs_base"] for v in cost_grid["borrow"].values()))

    # ---- 逐年（角色標示）
    years = sorted({d[:4] for d in ic_dates})
    yearly = []
    for y in years:
        m = np.array([d[:4] == y for d in ic_dates])
        ym = float(ic_arr[m].mean())
        role = YEAR_ROLE[segment]
        if y == "2023":
            role += "（2023H1）" if segment == "train" else "（2023H2）"
        yearly.append({"year": y, "role": role, "n_days": int(m.sum()), "ic_mean": ym, "sign": _sign(ym)})
    signs = {r["sign"] for r in yearly}
    years_same_sign = len(signs) == 1 and 0 not in signs

    # ---- 只報三項（§1.4 T4；口徑為本層選擇，報告寫明）
    sp_b, ex_b = base["_sp"], base["_ex"]
    report_only = {
        "sharpe_spread_daily": metrics.sharpe(sp_b.value),
        "sharpe_excess_daily": metrics.sharpe(ex_b.value),
        "max_drawdown_excess_cumsum": metrics.max_drawdown(ex_b.value, compound=False),
        "monthly_winrate_excess": metrics.monthly_winrate(ex_b.dates, ex_b.value),
        "note": "sharpe＝日序列 mean/std(ddof=1)，不年化（h 日重疊報酬）；最大回撤＝超額序列 cumsum 自高點回落；"
                "月勝率＝月加總>0 比例。三項只報不設門檻。",
    }

    # ---- 卦別分組報酬（§1.6 只套用 data/rank_table.json 的 group，不重排）
    gmap = rank_groups(rank_rows, market, horizon)
    net_b = net_ret_long(fwd, SLIP_BASE)
    hexa = {}
    for g in ("high", "mid", "low"):
        kws = {k for k, v in gmap.items() if v == g}
        m = np.isin(kw, list(kws)) if kws else np.zeros(kw.size, dtype=bool)
        hexa[g] = {"n_hexagrams": len(kws), "n_rows": int(m.sum()),
                   "mean_net": float(net_b[m].mean()) if m.any() else float("nan"),
                   "mean_raw": float(fwd[m].mean()) if m.any() else float("nan")}
    hexa["rows_without_king_wen"] = int((kw < 0).sum())
    hexa["rows_king_wen_not_in_table"] = int(((kw >= 0) & ~np.isin(kw, list(gmap))).sum())

    cell = {
        "market": market, "horizon": horizon, "h": h, "embargo": embargo,
        "window": window, "rows": rows, "ic": ic_sec,
        "secondary1": base["secondary1"], "secondary2": base["secondary2"],
        "short_side": {k: v for k, v in short_base.items()},
        "cost_grid": cost_grid, "yearly": yearly,
        "report_only": report_only, "hexagram_groups": hexa,
        "primary_pass": bool(verdict.primary_pass(ic_sec["ic_mean"], ic_sec["nw_t_h"])),
        "secondary_pass": bool(base["pass"]),
        "robust": {"segments_same_sign": None, "peer_ic_mean": None, "years_same_sign": bool(years_same_sign),
                   "cost_flips": bool(cost_flips)},
        "insufficient": bool(boot.insufficient(window["n_blocks_t5"])),
    }
    return cell


def _daily_labels(d_pos: np.ndarray, score: np.ndarray) -> np.ndarray:
    """逐日十分位標籤（Q17：平均秩後切組）。回與輸入等長的 int 陣列。"""
    out = np.empty(score.size, dtype=int)
    uniq, inv = np.unique(d_pos, return_inverse=True)
    order = np.argsort(inv, kind="stable")
    bounds = np.searchsorted(inv[order], np.arange(uniq.size + 1))
    for k in range(uniq.size):
        idx = order[bounds[k]:bounds[k + 1]]
        out[idx] = groups.group_labels_avg_rank(score[idx], N_GROUPS)
    return out


def _daily_mean(d_iso: np.ndarray, x: np.ndarray, member: np.ndarray) -> groups.DailySeries:
    """名單成員的逐日均值（不減全池）；沿 `excess_series` 的分組方式。"""
    uniq, inv = np.unique(d_iso, return_inverse=True)
    order = np.argsort(inv, kind="stable")
    bounds = np.searchsorted(inv[order], np.arange(uniq.size + 1))
    out_d, out_v = [], []
    for k in range(uniq.size):
        idx = order[bounds[k]:bounds[k + 1]]
        sel = x[idx][member[idx]]
        if sel.size:
            out_d.append(uniq[k])
            out_v.append(float(sel.mean()))
    return groups.DailySeries(np.asarray(out_d), np.asarray(out_v, float))


def finalize_verdicts(cells: list[dict[str, Any]], peer: dict[str, Any] | None) -> None:
    """同儕段（另一段）的 IC 均值進穩健「同號」；缺同儕時 verdict 只在區塊不足時給 insufficient、其餘留空並註明。"""
    peer_ic = {}
    if peer:
        for c in peer["cells"]:
            peer_ic[(c["market"], c["horizon"])] = float(c["ic"]["ic_mean"])
    for c in cells:
        key = (c["market"], c["horizon"])
        rb = c["robust"]
        if key in peer_ic:
            rb["peer_ic_mean"] = peer_ic[key]
            rb["segments_same_sign"] = _sign(peer_ic[key]) == _sign(c["ic"]["ic_mean"]) != 0
        nb = c["window"]["n_blocks_t5"]
        if c["insufficient"]:
            c["verdict"] = verdict.INSUFFICIENT
            c["verdict_note"] = f"區塊數量近似值 {nb:.2f} < {BLOCKS_MIN} → 一律「證據不足」（§1.3 `:270`、裁定 T3）"
            continue
        if rb["segments_same_sign"] is None:
            c["verdict"] = None
            c["verdict_note"] = "缺同儕段 json（--peer），穩健「三段同號」無法評估；verdict 留空"
            continue
        c["verdict"] = verdict.verdict(
            nb, c["ic"]["ic_mean"], c["ic"]["nw_t_h"], c["secondary1"]["pos_share"], c["secondary2"]["excess_mean"],
            c["secondary2"]["boot_ci"][0], c["secondary2"]["boot_ci"][1],
            rb["segments_same_sign"], rb["years_same_sign"], rb["cost_flips"])
        c["verdict_note"] = ("本段非保留段：verdict 為登錄書 §1.4 判定邏輯套在本段數字上的結果，供對照；"
                             "正式採用與否以保留段為準（保留段未跑）。三段同號只比得到訓練／驗證兩段。")
        c["robust_pass"] = bool(verdict.robust_pass(rb["segments_same_sign"], rb["years_same_sign"], rb["cost_flips"]))


# ---------------------------------------------------------------- 主流程

def build(segment: str, data_dir: Path, calendar: Path, rank_table: Path, cache_dir: Path | None,
          peer_path: Path | None, date_tag: str) -> dict[str, Any]:
    t0 = time.monotonic()
    _assert(segment in SEGMENTS, f"segment={segment!r} 不在 {sorted(SEGMENTS)}（保留段不在本批）")
    from iching.config import SEGMENTS as CFG_SEGMENTS  # 純常數模組；只互核本批兩段
    for k, v in SEGMENTS.items():
        _assert(tuple(CFG_SEGMENTS[k]) == v, f"SEGMENTS[{k}] 與 iching.config 不符")
    man_path = data_dir / "manifest.json"
    _assert(man_path.exists(), f"找不到 {man_path}")
    man = json.loads(man_path.read_text(encoding="utf-8"))
    files = check_manifest(man, data_dir, segment)
    rt = check_rank_table(rank_table)
    cal = load_calendar(calendar)
    cal_pos = {d: i for i, d in enumerate(cal)}
    seg_from, seg_to = SEGMENTS[segment]
    p0, p1 = windows.segment_bounds(cal, seg_from, seg_to)
    _assert(p1 < len(cal), "日曆未涵蓋段末之後的交易日（purge 邊界索引不存在）")
    peer = None
    if peer_path is not None:
        _assert(peer_path.exists(), f"找不到同儕段 {peer_path}")
        peer = json.loads(peer_path.read_text(encoding="utf-8"))
        other = "valid" if segment == "train" else "train"
        _assert(peer.get("segment") == other, f"--peer 的 segment={peer.get('segment')!r} 應為 {other}")
        _assert(peer.get("gate", {}).get("params_sha") == EXPECTED_PARAMS_SHA, "同儕段 json 的 params_sha 不符")

    cells: list[dict[str, Any]] = []
    file_counts = {}
    cache_used: list[str] = []
    for hz in HORIZONS:
        name = f"{segment}_{hz}.csv.gz"
        print(f"  讀 {name} …", flush=True)
        arrs = load_file(data_dir / name, files[name]["n_rows"], files[name]["sha256"], cal_pos, cache_dir, cache_used)
        file_counts[name] = check_file_counts(arrs, files[name]["manifest"], name)
        for mk in MARKETS:
            print(f"  算 {mk}/{hz} …", flush=True)
            cells.append(run_cell(arrs, mk, hz, segment, cal, (p0, p1), rt["rows"]))
        del arrs
    finalize_verdicts(cells, peer)
    elapsed = time.monotonic() - t0
    rss_kb = int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
    return {
        "schema": SCHEMA, "segment": segment, "date": date_tag,
        "meta": {"elapsed_sec": round(elapsed, 1), "max_rss_kb": rss_kb, "numpy_version": np.__version__,
                 "python_version": platform.python_version(), "script": "scripts/backtest_stats.py",
                 "cache_used": cache_used, "generated_at_epoch": int(time.time()),
                 "note": "elapsed_sec／max_rss_kb 由 resource.getrusage(RUSAGE_SELF) 於程序內量得；cache_used 非空表示該檔由 npz 快取載入、"
                         "耗時不含 csv 解析"},
        "rulings": RULINGS,
        "params": {"h_by_horizon": dict(H_BY_HORIZON), "embargo_days": EMBARGO_DAYS if segment != "train" else 0,
                   "embargo_days_registered": EMBARGO_DAYS, "block_len": {hz: block_len(h) for hz, h in H_BY_HORIZON.items()},
                   "nboot": NBOOT, "seed": DEFAULT_SEED, "min_pairs": MIN_PAIRS, "n_groups": N_GROUPS,
                   "blocks_min": BLOCKS_MIN, "ic_min": IC_MIN, "t_min": T_MIN, "pos_share_min": POS_SHARE_MIN,
                   "fee": FEE, "tax": TAX, "slip_base": SLIP_BASE, "slip_grid": list(SLIP_GRID),
                   "borrow_base": BORROW_BASE, "borrow_grid": list(BORROW_GRID), "days_per_year": DAYS_PER_YEAR,
                   "zero_fwd_net_long": ZERO_FWD_NET, "zero_fwd_net_short": net_ret_short(0.0),
                   "block_sensitivity": {"h": BLOCK_SENSITIVITY_H, "block": BLOCK_SENSITIVITY},
                   "nw_note": "借用本體：Bartlett 權重 1−l/(L+1)、自協方差分母 n−l；bootstrap 區間 np.percentile 線性內插"},
        "gate": {"params_sha": man["params_sha"], "pool_semantics": man["pool_semantics"], "data_version": man.get("data_version"),
                 "head": man.get("head"), "model_version": man.get("model_version"),
                 "n_market_rows_excluded": man.get("n_market_rows_excluded"),
                 "files": {k: {"sha256": v["sha256"], "n_rows": v["n_rows"]} for k, v in files.items()},
                 "file_counts_match_manifest": file_counts,
                 "rank_table_sha256": rt["sha256"], "calendar": {"path": str(calendar.name), "n": len(cal), "last": cal[-1]}},
        "segment_info": {"from": seg_from, "to": seg_to, "first_trading_day": cal[p0], "last_trading_day": cal[p1 - 1],
                         "n_days": p1 - p0, "next_segment_first_day": cal[p1], "boundary_pos": p1,
                         "boundary_note": ("驗證→保留邊界＝日曆上段末之後第一個交易日的索引" if segment == "valid"
                                           else "訓練→驗證邊界＝日曆上段末之後第一個交易日的索引；訓練段不扣 embargo（§1.4 `:288`）")},
        "peer": {"path": _rel(peer_path), "segment": peer["segment"], "date": peer.get("date")} if peer else None,
        "cells": cells,
    }


def _jsonable(obj: Any) -> Any:
    """json 前清理：numpy 標量→Python、非有限浮點→None（json 標準無 NaN；下游一律把 None 讀成「無值」）。"""
    if isinstance(obj, dict):
        return {str(k): _jsonable(v) for k, v in obj.items() if not str(k).startswith("_")}
    if isinstance(obj, (list, tuple)):
        return [_jsonable(v) for v in obj]
    if isinstance(obj, (np.floating, float)):
        f = float(obj)
        return f if np.isfinite(f) else None
    if isinstance(obj, (np.integer,)):
        return int(obj)
    if isinstance(obj, (np.bool_,)):
        return bool(obj)
    if isinstance(obj, np.ndarray):
        return _jsonable(obj.tolist())
    return obj


def _rel(p: Path) -> str:
    """repo 內的路徑寫相對路徑（產物進 git，不帶本機絕對路徑）。"""
    try:
        return str(p.resolve().relative_to(REPO))
    except ValueError:
        return str(p)


def _f(x: float | None, nd: int = 4) -> str:
    if x is None or not np.isfinite(x):
        return "—"
    return f"{x:.{nd}f}"


def as_text(rep: dict[str, Any]) -> str:
    si, m = rep["segment_info"], rep["meta"]
    L = [(f"P3 回測統計層｜{rep['segment']} 段 {si['from']}～{si['to']}（{si['n_days']} 交易日，{si['first_trading_day']}～{si['last_trading_day']}）"
          f"｜params_sha {rep['gate']['params_sha']}｜rank_table {rep['gate']['rank_table_sha256'][:12]}…"),
         (f"耗時 {m['elapsed_sec']} s｜峰值 RSS {m['max_rss_kb'] / 1024:.0f} MB｜numpy {m['numpy_version']}｜seed {rep['params']['seed']}"
          f"｜nboot {rep['params']['nboot']}｜min_pairs {rep['params']['min_pairs']}"), ""]
    L.append("mkt | h | 段日 | purge日(索引法) | purge首日 | embargo日 | 合格日 n_eff_index | 有效日 T5 | 區塊數 T5 | 區塊數(索引法) | IC 日數")
    for c in rep["cells"]:
        w = c["window"]
        L.append(f"{c['market']} | {c['horizon']}({c['h']}) | {w['seg_days']} | {w['purge_days']} | {w['purge_first_day']} | {w['embargo_days']} | "
                 f"{w['n_eff_index']} | {w['n_eff_t5']} | {w['n_blocks_t5']:.2f} | {w['n_blocks_index']:.2f} | {c['ic']['n_days_used']}")
    L.append("")
    L.append("mkt | h | ic_mean | nw_t_h | nw_t_2h | boot_ci | 次要① 正月比例 | 次要② 超額 | 超額 CI | 空方底組 | verdict")
    for c in rep["cells"]:
        i, s1, s2, ss = c["ic"], c["secondary1"], c["secondary2"], c["short_side"]
        L.append(f"{c['market']} | {c['horizon']} | {_f(i['ic_mean'])} | {_f(i['nw_t_h'], 2)} | {_f(i['nw_t_2h'], 2)} | "
                 f"[{_f(i['boot_ci'][0])}, {_f(i['boot_ci'][1])}] | {_f(s1['pos_share'], 3)} ({s1['pos_months']}/{s1['n_months']}) | "
                 f"{_f(s2['excess_mean'])} | [{_f(s2['boot_ci'][0])}, {_f(s2['boot_ci'][1])}] | {_f(ss['bottom_short_mean'])} | "
                 f"{c.get('verdict')}")
    L.append("")
    L.append("列處理計數（裁定 #72 Q9～Q12；raw.* 為窗內獨立母體）")
    L.append("mkt | h | 檔內 | 池內 | purge列 | embargo列 | 窗內 | 排除 limit_up | 落掉 fwd空 | 落掉 score空 | 計入 | halt | delist | exit_dn | reweighted 比例 | 同分比例 | <30 日數")
    for c in rep["cells"]:
        r = c["rows"]
        L.append(f"{c['market']} | {c['horizon']} | {r['in_file']} | {r['in_pool']} | {r['purged_rows']} | {r['embargo_rows']} | {r['in_window']} | "
                 f"{r['excluded_entry_limit_up']} (raw {r['raw']['entry_limit_up']}) | {r['skipped_null_fwd_ret']} (raw {r['raw']['null_fwd_ret']}) | "
                 f"{r['skipped_null_base_score']} (raw {r['raw']['null_base_score']}) | {r['counted']} | {r['kept_halt']} | {r['kept_delist']} | "
                 f"{r['kept_exit_limit_down']} | {_f(r['reweighted_share'])} | {_f(r['tie_share'])} | {c['ic']['days_below_min_n']}")
    L.append("")
    L.append("成本敏感度（slip 三欄：次要①正月比例／次要②超額／是否翻轉；借券三欄：底十分位空方淨報酬日均值／是否翻轉）")
    for c in rep["cells"]:
        g = c["cost_grid"]
        parts = [f"slip {k}: {_f(v['pos_share'], 3)}/{_f(v['excess_mean'])}/{'翻轉' if v['flip_vs_base'] else '不翻轉'}" for k, v in g["slip"].items()]
        parts += [f"借券 {k}: {_f(v['bottom_short_mean'])}/{'翻轉' if v['flip_vs_base'] else '不翻轉'}" for k, v in g["borrow"].items()]
        L.append(f"{c['market']} | {c['horizon']} | " + " | ".join(parts))
    L.append("")
    L.append("逐年 IC 均值（角色）")
    for c in rep["cells"]:
        L.append(f"{c['market']} | {c['horizon']} | " + " | ".join(f"{y['year']} {y['role']} {_f(y['ic_mean'])} ({y['n_days']}日)" for y in c["yearly"])
                 + f" | 同號={c['robust']['years_same_sign']} | 同儕段同號={c['robust']['segments_same_sign']} | 成本翻轉={c['robust']['cost_flips']}")
    L.append("")
    L.append("卦別分組（只套用 data/rank_table.json；高／中／低組 n 卦／n 列／淨報酬均值）")
    for c in rep["cells"]:
        hg = c["hexagram_groups"]
        L.append(f"{c['market']} | {c['horizon']} | " + " | ".join(
            f"{g}: {hg[g]['n_hexagrams']}卦/{hg[g]['n_rows']}列/{_f(hg[g]['mean_net'])}" for g in ("high", "mid", "low")))
    L.append("")
    return "\n".join(L) + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="P3 回測統計層（PR-S2）：訓練段／驗證段實跑")
    ap.add_argument("--segment", required=True, choices=sorted(SEGMENTS), help="只收 train／valid（保留段不在本批）")
    ap.add_argument("--data-dir", default=str(REPO / "data" / "backtest"))
    ap.add_argument("--calendar", default=str(REPO / "data" / "calendar_tpe.json"))
    ap.add_argument("--rank-table", default=str(REPO / "data" / "rank_table.json"))
    ap.add_argument("--out-dir", default=str(REPO / "runs" / "backtest"))
    ap.add_argument("--cache-dir", default=str(REPO / "cache"), help="npz 快取目錄；'' 表示不快取")
    ap.add_argument("--date", default=time.strftime("%Y-%m-%d"), help="產物檔名的日期標籤")
    ap.add_argument("--peer", default=None, help="另一段的 json（穩健「同號」用）；預設自動找 <out-dir>/<另一段>_<date>.json")
    ap.add_argument("--no-peer", action="store_true", help="不讀同儕段 json")
    args = ap.parse_args(argv)
    try:
        out_dir = Path(args.out_dir)
        peer = None
        if not args.no_peer:
            other = "valid" if args.segment == "train" else "train"
            cand = Path(args.peer) if args.peer else out_dir / f"{other}_{args.date}.json"
            if args.peer or cand.exists():
                peer = cand
        rep = _jsonable(build(args.segment, Path(args.data_dir), Path(args.calendar), Path(args.rank_table),
                              Path(args.cache_dir) if args.cache_dir else None, peer, args.date))
        out_dir.mkdir(parents=True, exist_ok=True)
        jp = out_dir / f"{args.segment}_{args.date}.json"
        tp = out_dir / f"{args.segment}_{args.date}.txt"
        jp.write_text(json.dumps(rep, ensure_ascii=False, indent=1, allow_nan=False) + "\n", encoding="utf-8")
        tp.write_text(as_text(rep), encoding="utf-8")
        print(as_text(rep))
        print(f"== 已寫 {jp} 與 {tp}（耗時 {rep['meta']['elapsed_sec']} s、峰值 RSS {rep['meta']['max_rss_kb'] // 1024} MB）")
        return 0
    except Exception as e:  # noqa: BLE001 — 任何例外一律 rc=2、不產檔
        print(f"[backtest_stats 中止] {type(e).__name__}: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
