#!/usr/bin/env python3
"""回測資料出口（`docs/P3-DATASET.md` §2 A、裁定 #50）：從 Hetzner 的 `cache/scores.db`＋`cache/prices.db` 匯出
訓練／驗證段「每檔×每日×每 horizon 一列」的最小評估集合 `data/backtest/<segment>_<horizon>.csv.gz`（六檔）＋`manifest.json`。

    python3 scripts/export_dataset.py --cache-dir cache --out . --segment all --force

**只用標準庫**（Hetzner 是 Python 3.14 系統層；不引入 pandas／pyarrow）。與 `export_scores.py` 共用 `resolve_data_version`
與 rc 約定：0 成功／1 目標已存在且內容不同、未 `--force`（一個都不覆蓋）／2 中止（db 缺、`params_sha` 非現行碼指紋或
非 PIT 池、日曆缺日、價格表缺 `open`、同鍵重複列…）。

## 每列欄位（`COLUMNS`，欄序固定＝§2 A3）

`date, market, stock_id, horizon, base_score, in_rank_pool, coverage, king_wen, lines_formal, fwd_ret, mkt_ret_h, exit_reason,
entry_limit_up, exit_limit_down`。前九欄**零轉換**自 `scores`（JOIN `versions` 取 data_version；只匯 `scope='stock'` 的個股列，
大盤列 `stock_id='__MARKET__'` 不匯、manifest 記 `n_market_rows_excluded`）；`base_score` 用 `repr` 全精度（與 `data/scores/<T>.json`
的 `json.dumps` 同一個 `float.__repr__`，逐位可對）；缺值一律空字串。

## 前向報酬（§2 A4）

交易日序＝`data/calendar_tpe.json`（`--calendar`，預設 `<out>/data/calendar_tpe.json`）。訊號日 T 在日曆的位置 i，
進場日 e＝cal[i+1]、出場日 x＝cal[i+1+h]（h＝`H_BY_HORIZON`）。

    fwd_ret   = adj_close(exit) / adj_open(e) − 1          adj_x(t) = raw_x(t) × factor_at(t)
    mkt_ret_h = idx_close(exit) / idx_open(e) − 1          同市場指數（twse→TAIEX、tpex→TPEx）、原始值不還原

後復權係數由 `raw_dividend_result` → `feed.load_factors`（＝`adjust.cumulative_factors`；同 (stock_id, date) 只取第一列、非數或 ≤0 跳過）
→ `adjust.factor_at`。兩者 `round(x, ROUND_DIGITS=6)`（`-0.0` 正規化為 `0.0`）。
**`mkt_ret_h` 與 `fwd_ret` 同窗**：出場日取個股實際出場日（halt／delist 提前出場時跟著提前）；個股沒進場（`no_entry`）時取名目出場日 x。

## 邊界（§2 A5）——三類皆不刪列

| 情況 | fwd_ret | exit_reason |
|---|---|---|
| e 或 x 超出資料末日 `data_end`（＝日曆末日與 TAIEX 指數列末日的較小者） | 空 | 空（e 在資料內但無法進場時仍記 `no_entry`） |
| T+1 無法進場：該日沒有列、`open` NULL／≤0、或非成交列（`universe.is_traded_row`：`close>0` 且 `Trading_Volume>0`） | 空 | `no_entry` |
| x 日有成交列 | 正常 | 空 |
| x 日無成交列、該檔在 x 之後（≤ data_end）仍有價格列 | 出場價＝[e, x] 內最後一個成交日的後復權收盤 | `halt` |
| x 日無成交列、該檔**最後一筆價格列 < x 且 < data_end**（列永久消失） | 同上 | `delist` |

「下市」的定義是**價格列永久消失**（`raw_price_daily` 該檔最後一列的日期 `< x` 且 `< data_end`），不查 `raw_stock_info`——快照只有
現行名單、沒有下市日（`universe.py` 已知：快照裡沒有的代號不進池，所以生產環境下已下市股多半根本沒被計分，這條主要守的是
「T 時在快照、之後列消失」的檔）。出場價的「最後一個成交日」以 `is_traded_row` 判，至少會落在 e（e 已要求成交）。

## 漲跌停旗標（裁定 #50 Q19，**近似**）

`raw_price_daily` 沒有漲跌停欄，用 10% 規則近似：`entry_limit_up = 1` 若 `open(e) >= round(prev_close × 1.10, 2)`、
`exit_limit_down = 1` 若 `close(exit) <= round(prev_close × 0.90, 2)`，`prev_close`＝該日之前最後一個成交日的**原始**收盤。
近似之處：①真實漲跌停價要依檔位（tick）取整，這裡只取到分；②除權息日的參考價是 `after_price` 不是前收，這裡一律用前收；
③只看開盤／收盤價位、不看是否「鎖死」。找不到前收（載入區間內無成交日）寫空字串；無法進場／無出場價時對應旗標寫空字串。

## 守門（§2 A2）

- `replay_meta.params_sha` 必須等於**用現行碼**重算的指紋：`build_params_payload({m: build_params(m).model_version()}, window,
  adv, fundamentals)`（window／adv_window／adv_threshold／fundamentals 取 `params_json`）→ `params_fingerprint`；
  且 `params_json.pool_semantics == universe.POOL_SEMANTICS`（pit-1）。不符 rc 2——擋「用錯 db」，不擋「PIT 本身有錯」。
- 日曆：選定段內 `scores` 的每個日期都必須在日曆裡；日曆須涵蓋到段末；`[段起, data_end]` 內日曆日集合必須＝TAIEX 指數列日期集合。
- `raw_price_daily` 必須有 `stock_id/date/open/close/Trading_Volume` 欄。

## 輸出決定性

CSV 由 `csv` 模組寫、`\\n` 換行、gzip `mtime=0`＋`compresslevel=9`——同內容同位元組（`tests/test_export_dataset.py` 跑兩次比 sha256）。
六檔先寫 `.tmp`，全部算完再一次 `replace`；任一目標已存在且內容不同又未 `--force` → 全部不寫、rc 1。
manifest（`bundle_io.dumps_json` 同組參數：鍵排序、無空白）永遠重寫（它含 `head`，不參與 rc 1 判定）；`--segment train|valid`
時 manifest 只列本次產出的檔。
"""
from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import io
import math
import subprocess
import sys
import time
from array import array
from pathlib import Path
from types import SimpleNamespace
from typing import Any

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "src"))
sys.path.insert(0, str(REPO / "scripts"))

from export_scores import ExportScoresError, resolve_data_version  # noqa: E402
from iching import daily_core as DC  # noqa: E402
from iching import feed as F  # noqa: E402
from iching import universe as U  # noqa: E402
from iching.adjust import factor_at  # noqa: E402
from iching.bundle_io import dumps_json  # noqa: E402
from iching.features_io import params_fingerprint  # noqa: E402
from iching.run_common import build_params_payload  # noqa: E402
from iching.score.assemble import MARKET_STOCK_ID  # noqa: E402
from iching.score.params import MARKETS, build_params  # noqa: E402
from iching.scores_io import ScoreStore, ScoreStoreError  # noqa: E402

# 三段切點：docs/pre-registration.md:47-50（訓練 2021-01-01～2023-06-30 603 日／驗證 2023-07-01～2024-12-31 368 日；
# 暖機 2020 與保留 2025-01 起不匯，docs/P3-DATASET.md §0）
SEGMENTS: dict[str, tuple[str, str]] = {"train": ("2021-01-01", "2023-06-30"), "valid": ("2023-07-01", "2024-12-31")}
# h：docs/pre-registration.md:55（短線 10／波段 20／中期 40 交易日；不是計分用 L3 長視窗 10/20/60）
H_BY_HORIZON: dict[str, int] = {"short": 10, "swing": 20, "mid": 40}
HORIZONS = ("short", "swing", "mid")
COLUMNS = ("date", "market", "stock_id", "horizon", "base_score", "in_rank_pool", "coverage", "king_wen", "lines_formal",
           "fwd_ret", "mkt_ret_h", "exit_reason", "entry_limit_up", "exit_limit_down")
SCORE_COLS = COLUMNS[:9]
ROUND_DIGITS = 6
LIMIT_UP, LIMIT_DOWN = 1.10, 0.90
EXIT_REASONS = ("", "halt", "delist", "no_entry")
OUT_DIR = "data/backtest"
MANIFEST = "manifest.json"
MANIFEST_SCHEMA = 1
PRICE_COLS = {"stock_id", "date", "open", "close", U.PRICE_VOLUME}
NAN = float("nan")


class ExportDatasetError(RuntimeError):
    pass


def file_name(segment: str, horizon: str) -> str:
    return f"{segment}_{horizon}.csv.gz"


def _f(v: Any) -> float:
    """sqlite 值 → float；None／非數 → NaN。"""
    if v is None or isinstance(v, bool):
        return NAN
    try:
        return float(v)
    except (TypeError, ValueError):
        return NAN


def _fmt_ret(v: float | None) -> str:
    if v is None or not math.isfinite(v):
        return ""
    r = round(v, ROUND_DIGITS) + 0.0                      # `+ 0.0`：-0.0 → 0.0
    return repr(r)


def _cell(v: Any) -> str:
    """分數欄零轉換：None → ""；float → repr（全精度）；其餘 str。"""
    if v is None:
        return ""
    if isinstance(v, float):
        return repr(v)
    return str(v)


# ---------------------------------------------------------------------------
class PriceBook:
    """個股價格：每檔三個依日曆位置索引的 array（open／close 為 NaN＝無列或 NULL；traded＝`is_traded_row`），
    ＋ 該檔最後一筆價格列的位置（下市判定用）。指數：每市場 open／close 兩個 array。"""

    def __init__(self, cal: list[str]) -> None:
        self.cal = cal
        self.pos = {d: i for i, d in enumerate(cal)}
        self.n = len(cal)
        self.open: dict[str, array] = {}
        self.close: dict[str, array] = {}
        self.traded: dict[str, array] = {}
        self.has_row: dict[str, array] = {}
        self.last_row_pos: dict[str, int] = {}
        self.idx_open: dict[str, array] = {}
        self.idx_close: dict[str, array] = {}
        self.n_rows = 0
        self.n_off_calendar = 0

    def _new(self, sid: str) -> None:
        self.open[sid] = array("d", [NAN]) * self.n
        self.close[sid] = array("d", [NAN]) * self.n
        self.traded[sid] = array("b", [0]) * self.n
        self.has_row[sid] = array("b", [0]) * self.n
        self.last_row_pos[sid] = -1

    def load_prices(self, conn, dv: str, start: str, end: str) -> None:
        q = (f'SELECT stock_id, date, open, close, "{U.PRICE_VOLUME}" FROM "{F.PRICE_TABLE}" '
             f"WHERE data_version=? AND date>=? AND date<=?")
        for sid, d, o, c, vol in conn.execute(q, (dv, start, end)):
            self.n_rows += 1
            p = self.pos.get(str(d))
            if p is None:
                self.n_off_calendar += 1
                continue
            sid = str(sid)
            if sid not in self.open:
                self._new(sid)
            self.open[sid][p] = _f(o)
            self.close[sid][p] = _f(c)
            self.traded[sid][p] = 1 if U.is_traded_row({U.PRICE_CLOSE: c, U.PRICE_VOLUME: vol}) else 0
            self.has_row[sid][p] = 1
            if p > self.last_row_pos[sid]:
                self.last_row_pos[sid] = p

    def load_index(self, conn, dv: str, start: str, end: str) -> None:
        ids = {v: k for k, v in F.INDEX_ID.items()}
        q = f'SELECT stock_id, date, open, close FROM "{F.INDEX_TABLE}" WHERE data_version=? AND date>=? AND date<=?'
        for sid, d, o, c in conn.execute(q, (dv, start, end)):
            m = ids.get(str(sid))
            p = self.pos.get(str(d))
            if m is None or p is None:
                continue
            if m not in self.idx_open:
                self.idx_open[m] = array("d", [NAN]) * self.n
                self.idx_close[m] = array("d", [NAN]) * self.n
            self.idx_open[m][p] = _f(o)
            self.idx_close[m][p] = _f(c)

    # -- 查 --
    def is_traded(self, sid: str, p: int) -> bool:
        t = self.traded.get(sid)
        return bool(t and 0 <= p < self.n and t[p])

    def open_at(self, sid: str, p: int) -> float:
        a = self.open.get(sid)
        return a[p] if a is not None and 0 <= p < self.n else NAN

    def close_at(self, sid: str, p: int) -> float:
        a = self.close.get(sid)
        return a[p] if a is not None and 0 <= p < self.n else NAN

    def last_traded_in(self, sid: str, lo: int, hi: int) -> int | None:
        """[lo, hi] 內最後一個成交日的位置（由後往前找）；沒有回 None。"""
        t = self.traded.get(sid)
        if t is None:
            return None
        for p in range(min(hi, self.n - 1), max(lo, 0) - 1, -1):
            if t[p]:
                return p
        return None

    def open_quality(self, start_pos: int, end_pos: int) -> dict[str, Any]:
        """[start_pos, end_pos] 內 `open` 為 NULL／≤0 的列數與檔數（§2 A2）：全部有列者、與成交列（`is_traded_row`）兩個口徑。"""
        n_all = n_traded = n_rows = n_traded_rows = 0
        s_all: set[str] = set()
        s_traded: set[str] = set()
        for sid, hr in self.has_row.items():
            o, t = self.open[sid], self.traded[sid]
            for p in range(start_pos, end_pos + 1):
                if not hr[p]:
                    continue
                n_rows += 1
                bad = math.isnan(o[p]) or o[p] <= 0
                if bad:
                    n_all += 1
                    s_all.add(sid)
                if t[p]:
                    n_traded_rows += 1
                    if bad:
                        n_traded += 1
                        s_traded.add(sid)
        return {"range": [self.cal[start_pos], self.cal[end_pos]], "n_rows": n_rows, "n_traded_rows": n_traded_rows,
                "open_null_or_nonpos": {"rows": n_all, "stocks": len(s_all)},
                "open_null_or_nonpos_traded": {"rows": n_traded, "stocks": len(s_traded)}}


# ---------------------------------------------------------------------------
# 價格路徑拆成小函式：測試以 monkeypatch 注入突變（少乘係數、用 T 收盤進場…）驗手算對帳會紅
def adj_price(raw: float, date: str, fac: tuple[list[str], list[float]] | None) -> float:
    """後復權：raw × factor_at(date)。無事件（fac None）係數 1。"""
    if fac is None:
        return raw
    return raw * factor_at(date, fac[0], fac[1])


def entry_price(book: PriceBook, sid: str, e_pos: int, fac) -> float:
    """T+1 開盤的後復權價。"""
    return adj_price(book.open_at(sid, e_pos), book.cal[e_pos], fac)


def exit_price(book: PriceBook, sid: str, x_pos: int, fac) -> float:
    """出場日收盤的後復權價。"""
    return adj_price(book.close_at(sid, x_pos), book.cal[x_pos], fac)


def limit_flag(book: PriceBook, sid: str, p: int, price: float, mult: float, up: bool) -> str:
    """`price` 是否觸及 `prev_close × mult`（前收＝p 之前最後一個成交日的原始收盤；近似規則見檔頭）。"""
    q = book.last_traded_in(sid, 0, p - 1)
    if q is None or not math.isfinite(price):
        return ""
    prev = book.close_at(sid, q)
    if not math.isfinite(prev) or prev <= 0:
        return ""
    lim = round(prev * mult, 2)
    return "1" if (price >= lim if up else price <= lim) else "0"


def compute_row(book: PriceBook, sid: str, market: str, t_pos: int, h: int, fac, data_end_pos: int) -> dict[str, str]:
    """一列的五個計算欄（fwd_ret／mkt_ret_h／exit_reason／entry_limit_up／exit_limit_down），值皆為 CSV 字串。"""
    out = {"fwd_ret": "", "mkt_ret_h": "", "exit_reason": "", "entry_limit_up": "", "exit_limit_down": ""}
    e, x = t_pos + 1, t_pos + 1 + h
    if e > data_end_pos:
        return out
    o = book.open_at(sid, e)
    can_enter = book.is_traded(sid, e) and math.isfinite(o) and o > 0
    if can_enter:
        out["entry_limit_up"] = limit_flag(book, sid, e, o, LIMIT_UP, up=True)
    else:
        out["exit_reason"] = "no_entry"
    if x > data_end_pos:
        return out                                            # 視窗超出資料末日：fwd_ret／mkt_ret_h 空、列保留
    if not can_enter:
        out["mkt_ret_h"] = _mkt_ret(book, market, e, x)
        return out
    if book.is_traded(sid, x):
        xp = x
    else:
        xp = book.last_traded_in(sid, e, x)                  # ≥ e（e 已成交）
        last = book.last_row_pos.get(sid, -1)
        out["exit_reason"] = "delist" if (last < x and last < data_end_pos) else "halt"
    ep, xv = entry_price(book, sid, e, fac), exit_price(book, sid, xp, fac)
    if math.isfinite(ep) and ep > 0 and math.isfinite(xv):
        out["fwd_ret"] = _fmt_ret(xv / ep - 1.0)
    out["exit_limit_down"] = limit_flag(book, sid, xp, book.close_at(sid, xp), LIMIT_DOWN, up=False)
    out["mkt_ret_h"] = _mkt_ret(book, market, e, xp)
    return out


def _mkt_ret(book: PriceBook, market: str, e: int, xp: int) -> str:
    io_, ic = book.idx_open.get(market), book.idx_close.get(market)
    if io_ is None or ic is None:
        return ""
    o, c = io_[e], ic[xp]
    if not (math.isfinite(o) and o > 0 and math.isfinite(c)):
        return ""
    return _fmt_ret(c / o - 1.0)


# ---------------------------------------------------------------------------
def expected_params_sha(params: dict) -> str:
    """用現行碼重算 `replay_meta` 應有的指紋：model_version 取現行 `build_params`，window／adv／fundamentals 取 db 記的。"""
    mv = {m: build_params(m).model_version() for m in MARKETS}
    adv = SimpleNamespace(window=params.get("adv_window"), threshold=params.get("adv_threshold"))
    payload = build_params_payload(mv, int(params["window"]), adv, fundamentals=bool(params.get("fundamentals", True)))
    return params_fingerprint(payload)


def check_params(store: ScoreStore, dv: str) -> tuple[str, dict]:
    sha = store.params_sha_of(dv)
    params = store.params_of(dv)
    if sha is None or params is None:
        raise ExportDatasetError(f"replay_meta 沒有 data_version={dv}")
    if params.get("pool_semantics") != U.POOL_SEMANTICS:
        raise ExportDatasetError(f"scores.db 的 pool_semantics={params.get('pool_semantics')!r} 不是 {U.POOL_SEMANTICS!r}（非 PIT 池版本）")
    if params_fingerprint(params) != sha:
        raise ExportDatasetError(f"replay_meta 不自洽：params_json 指紋 {params_fingerprint(params)} ≠ params_sha {sha}")
    want = expected_params_sha(params)
    if want != sha:
        raise ExportDatasetError(f"replay_meta.params_sha={sha} ≠ 現行碼指紋 {want}（model_version／text_version／state_schema／"
                                 f"pool_semantics 任一不同；這份 scores.db 不是現行碼算的）")
    return sha, params


def _pos_ge(cal: list[str], d: str) -> int:
    """第一個 ≥ d 的日曆位置。"""
    return next(i for i, x in enumerate(cal) if x >= d)


def _pos_le(cal: list[str], d: str) -> int:
    """最後一個 ≤ d 的日曆位置。"""
    return next(i for i in range(len(cal) - 1, -1, -1) if cal[i] <= d)


def git_head(root: Path) -> str | None:
    try:
        r = subprocess.run(["git", "-C", str(root), "rev-parse", "HEAD"], capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return None
    return r.stdout.strip() if r.returncode == 0 and r.stdout.strip() else None


def user_version(conn) -> int | None:
    try:
        return int(conn.execute("PRAGMA user_version").fetchone()[0])
    except Exception:  # noqa: BLE001
        return None


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


class GzCsv:
    """一個 `<segment>_<horizon>.csv.gz` 的串流寫入端（先寫 `.tmp`）＋ 該檔統計。"""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.tmp = path.with_suffix(path.suffix + ".tmp")
        self.tmp.parent.mkdir(parents=True, exist_ok=True)
        self._f = open(self.tmp, "wb")
        self._gz = gzip.GzipFile(fileobj=self._f, mode="wb", mtime=0, compresslevel=9)
        self._txt = io.TextIOWrapper(self._gz, encoding="utf-8", newline="")
        self._w = csv.writer(self._txt, lineterminator="\n")
        self._w.writerow(COLUMNS)
        self.n_rows = 0
        self.exit_counts = {k: 0 for k in EXIT_REASONS}
        self.n_fwd_missing = self.n_mkt_missing = self.n_entry_lu = self.n_exit_ld = 0
        self.last_signal_with_fwd: str | None = None

    def write(self, cells: list[str]) -> None:
        self._w.writerow(cells)
        self.n_rows += 1
        fwd, mkt, reason, lu, ld = cells[9], cells[10], cells[11], cells[12], cells[13]
        self.exit_counts[reason] += 1
        if fwd == "":
            self.n_fwd_missing += 1
        else:
            self.last_signal_with_fwd = cells[0]
        if mkt == "":
            self.n_mkt_missing += 1
        self.n_entry_lu += lu == "1"
        self.n_exit_ld += ld == "1"

    def close(self) -> None:
        self._txt.flush()
        self._txt.detach()
        self._gz.close()
        self._f.close()

    def abort(self) -> None:
        try:
            self.close()
        finally:
            self.tmp.unlink(missing_ok=True)

    def stats(self) -> dict[str, Any]:
        return {"n_rows": self.n_rows, "bytes": self.tmp.stat().st_size, "sha256": sha256_file(self.tmp),
                "exit_reason_counts": {(k or "ok"): v for k, v in self.exit_counts.items()},
                "n_fwd_ret_missing": self.n_fwd_missing, "n_mkt_ret_missing": self.n_mkt_missing,
                "n_entry_limit_up": self.n_entry_lu, "n_exit_limit_down": self.n_exit_ld,
                "last_signal_date_with_fwd_ret": self.last_signal_with_fwd}


# ---------------------------------------------------------------------------
def run(args: argparse.Namespace) -> int:
    cache, out = Path(args.cache_dir), Path(args.out)
    cal_path = Path(args.calendar) if args.calendar else out / DC.CALENDAR_TPE_FILE
    segments = list(SEGMENTS) if args.segment == "all" else [args.segment]
    t0 = time.perf_counter()
    store = prices = None
    writers: dict[str, GzCsv] = {}
    try:
        try:
            store = ScoreStore(cache / "scores.db", readonly=True)
        except ScoreStoreError as e:
            raise ExportDatasetError(str(e)) from e
        dv = resolve_data_version(store, cache, args.data_version)
        sha, params = check_params(store, dv)
        prices = F.open_ro(cache / "prices.db")
        have = F.require(prices, F.PRICE_TABLE, PRICE_COLS)
        F.require(prices, F.INDEX_TABLE, {"stock_id", "date", "open", "close"})
        table_info = [list(r) for r in prices.execute(f'PRAGMA table_info("{F.PRICE_TABLE}")')]
        print(f"data_version={dv} params_sha={sha} pool_semantics={params.get('pool_semantics')} window={params.get('window')}")
        print(f"PRAGMA table_info({F.PRICE_TABLE})：{[r[1] for r in table_info]}（open 欄型別 {next((r[2] for r in table_info if r[1] == 'open'), '?')}）")

        # -- 日曆與資料末日 --
        cal = DC.load_calendar_dates(cal_path)
        if cal != sorted(set(cal)):
            raise ExportDatasetError(f"日曆 {cal_path} 非嚴格遞增")
        seg_to = max(SEGMENTS[s][1] for s in segments)
        load_from = min(SEGMENTS[s][0] for s in SEGMENTS)                         # open 品質統計固定量訓練＋驗證全段
        taiex_last = prices.execute(f'SELECT MAX(date) FROM "{F.INDEX_TABLE}" WHERE data_version=? AND stock_id=?',
                                    (dv, F.INDEX_ID["twse"])).fetchone()[0]
        if not taiex_last:
            raise ExportDatasetError(f"{F.INDEX_TABLE} 沒有 {F.INDEX_ID['twse']} 的列")
        data_end = min(cal[-1], str(taiex_last))
        if cal[-1] < seg_to:
            raise ExportDatasetError(f"日曆只到 {cal[-1]}，未涵蓋段末 {seg_to}（日曆缺日）")
        cal_in = {d for d in cal if load_from <= d <= data_end}
        taiex_in = {str(r[0]) for r in prices.execute(
            f'SELECT date FROM "{F.INDEX_TABLE}" WHERE data_version=? AND stock_id=? AND date>=? AND date<=?',
            (dv, F.INDEX_ID["twse"], load_from, data_end))}
        if cal_in != taiex_in:
            a, b = sorted(cal_in - taiex_in)[:5], sorted(taiex_in - cal_in)[:5]
            raise ExportDatasetError(f"日曆與 TAIEX 指數列日期不一致（{load_from}..{data_end}）：日曆有指數缺 {a}…（{len(cal_in - taiex_in)} 日）／"
                                     f"指數有日曆缺 {b}…（{len(taiex_in - cal_in)} 日）")
        book = PriceBook(cal)
        data_end_pos = book.pos[data_end]
        book.load_prices(prices, dv, load_from, data_end)
        book.load_index(prices, dv, load_from, data_end)
        factors, fstat = F.load_factors(prices, dv)
        # open 品質統計（A2）固定量「訓練＋驗證」全段（不隨 --segment 縮小），並另附各段
        oq_to = min(max(SEGMENTS[s][1] for s in SEGMENTS), data_end)
        oq = book.open_quality(_pos_ge(cal, load_from), _pos_le(cal, oq_to))
        oq_seg = {s: book.open_quality(_pos_ge(cal, SEGMENTS[s][0]), _pos_le(cal, min(SEGMENTS[s][1], data_end)))
                  for s in SEGMENTS if SEGMENTS[s][0] <= data_end}
        print(f"價格列 {book.n_rows}（{len(book.open)} 檔；日曆外 {book.n_off_calendar} 列）data_end={data_end} "
              f"係數 {fstat}；open 品質 {oq['range']}：全列 {oq['open_null_or_nonpos']}／成交列 {oq['open_null_or_nonpos_traded']}"
              f"（共 {oq['n_rows']} 列／成交 {oq['n_traded_rows']} 列）")

        # -- 段內日期核對 --
        all_dates = store.dates(dv)
        n_mkt_excluded = 0
        seg_meta: dict[str, Any] = {}
        for s in segments:
            f0, f1 = SEGMENTS[s]
            sdays = [d for d in all_dates if f0 <= d <= f1]
            if not sdays:
                raise ExportDatasetError(f"{s} 段 {f0}..{f1} 在 scores.db 沒有任何 replay_day")
            missing = [d for d in sdays if d not in book.pos]
            if missing:
                raise ExportDatasetError(f"{s} 段有 {len(missing)} 個計分日不在日曆：{missing[:5]}…（日曆缺日）")
            cal_days = [d for d in cal if f0 <= d <= f1]
            no_scores = sorted(set(cal_days) - set(sdays))
            if no_scores:
                print(f"[警告] {s} 段有 {len(no_scores)} 個日曆日沒有 replay_day：{no_scores[:5]}…", file=sys.stderr)
            seg_meta[s] = {"from": f0, "to": f1, "n_calendar_days": len(cal_days), "n_score_days": len(sdays),
                           "calendar_days_without_scores": no_scores}

        # -- 逐段串流寫出 --
        cols = ", ".join(f"s.{c}" for c in SCORE_COLS)
        sql = (f"SELECT {cols} FROM scores s JOIN versions v ON v.version_id = s.version_id "
               f"WHERE v.data_version=? AND s.date>=? AND s.date<=? AND s.stock_id<>? ORDER BY s.date, s.market, s.stock_id, s.horizon")
        for s in segments:
            f0, f1 = SEGMENTS[s]
            n_mkt_excluded += store.conn.execute(
                "SELECT COUNT(*) FROM scores s JOIN versions v ON v.version_id = s.version_id "
                "WHERE v.data_version=? AND s.date>=? AND s.date<=? AND s.stock_id=?", (dv, f0, f1, MARKET_STOCK_ID)).fetchone()[0]
            for hz in HORIZONS:
                writers[file_name(s, hz)] = GzCsv(out / OUT_DIR / file_name(s, hz))
            prev_key = None
            n = 0
            for r in store.conn.execute(sql, (dv, f0, f1, MARKET_STOCK_ID)):
                d, market, sid, hz = str(r[0]), str(r[1]), str(r[2]), str(r[3])
                key = (d, market, sid, hz)
                if key == prev_key:
                    raise ExportDatasetError(f"同鍵重複列 {key}（多個 model_version／text_version？）")
                prev_key = key
                if hz not in H_BY_HORIZON:
                    raise ExportDatasetError(f"未知 horizon {hz!r}（{key}）")
                calc = compute_row(book, sid, market, book.pos[d], H_BY_HORIZON[hz], factors.get(sid), data_end_pos)
                cells = [_cell(v) for v in r] + [calc[c] for c in COLUMNS[9:]]
                writers[file_name(s, hz)].write(cells)
                n += 1
            print(f"  {s}: {n} 列 → {', '.join(f'{hz}={writers[file_name(s, hz)].n_rows}' for hz in HORIZONS)}"
                  f"（大盤列排除 {n_mkt_excluded}）", flush=True)
        for w in writers.values():
            w.close()

        # -- rc 1 判定（全部算完才比） --
        blocked: list[str] = []
        actions: dict[str, str] = {}
        for name, w in writers.items():
            if not w.path.exists():
                actions[name] = "寫出"
            elif w.path.read_bytes() == w.tmp.read_bytes():
                actions[name] = "已相同"
            elif args.force:
                actions[name] = "覆蓋"
            else:
                actions[name] = "未覆蓋（要覆蓋請 --force）"
                blocked.append(name)
        if blocked:
            for w in writers.values():
                w.tmp.unlink(missing_ok=True)
            print(f"[export_dataset] {len(blocked)} 檔與現有檔內容不同且未 --force，全部未寫：{blocked}", file=sys.stderr)
            return 1
        files = {name: w.stats() for name, w in writers.items()}
        for name, w in writers.items():
            if actions[name] == "已相同":
                w.tmp.unlink(missing_ok=True)
            else:
                w.tmp.replace(w.path)
            print(f"  {name}: {files[name]['n_rows']} 列 {files[name]['bytes']} bytes sha256={files[name]['sha256'][:12]} "
                  f"exit={files[name]['exit_reason_counts']} → {actions[name]}")
        manifest = {
            "schema": MANIFEST_SCHEMA, "data_version": dv, "params_sha": sha,
            "model_version": dict(params.get("model_version") or {}), "text_version": params.get("text_version"),
            "window": params.get("window"), "pool_semantics": params.get("pool_semantics"),
            "segments": seg_meta, "h_by_horizon": dict(H_BY_HORIZON), "columns": list(COLUMNS), "round_digits": ROUND_DIGITS,
            "calendar": {"path": str(cal_path.name), "first": cal[0], "last": cal[-1], "n": len(cal), "data_end": data_end},
            "user_version": {"scores.db": user_version(store.conn), "prices.db": user_version(prices)},
            "price_table_info": table_info, "price_columns": sorted(have),
            "open_quality": {"train_plus_valid": oq, "by_segment": oq_seg},
            "n_price_rows_off_calendar": book.n_off_calendar, "factors": fstat,
            "n_market_rows_excluded": n_mkt_excluded, "files": files, "head": git_head(out),
        }
        mpath = out / OUT_DIR / MANIFEST
        mpath.write_bytes(dumps_json(manifest) + b"\n")
        total = sum(f["n_rows"] for f in files.values())
        print(f"完成：{len(files)} 檔 {total} 列 → {out / OUT_DIR}；manifest {mpath.name}；params_sha={sha}；"
              f"耗時 {time.perf_counter() - t0:.1f}s")
        return 0
    except (ExportDatasetError, ExportScoresError, ScoreStoreError, F.FeedError, DC.DailyCoreError, OSError, ValueError, KeyError) as e:
        for w in writers.values():
            try:
                w.abort()
            except Exception:  # noqa: BLE001
                pass
        print(f"[export_dataset 中止] {type(e).__name__}: {e}", file=sys.stderr)
        return 2
    finally:
        if prices is not None:
            prices.close()
        if store is not None:
            store.close()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="回測資料出口：scores.db＋prices.db → data/backtest/<segment>_<horizon>.csv.gz ＋ manifest.json")
    ap.add_argument("--cache-dir", default=str(REPO / "cache"))
    ap.add_argument("--out", default=str(REPO), help="repo 根（寫 data/backtest/）")
    ap.add_argument("--segment", choices=("train", "valid", "all"), default="all")
    ap.add_argument("--data-version", default=None, help="預設：replay_meta 唯一者，否則取 scores.db.state.json 的 meta")
    ap.add_argument("--calendar", default=None, help="交易日曆 JSON，預設 <out>/data/calendar_tpe.json")
    ap.add_argument("--force", action="store_true", help="目標已存在且內容不同時覆蓋（預設不覆蓋、rc 1）")
    return run(ap.parse_args(argv))


if __name__ == "__main__":
    raise SystemExit(main())
