"""回補速度診斷（2026-09-11；使用者在 Hetzner 觀察 price_daily 每請求由 1.6s 單調退化到 3.4s）：

1. 次要索引延後建立：`run` 路徑 `create_indexes=False`（回補只走主鍵）；`reindex`／`reindex --drop` 建／刪，皆冪等；
   `--drop` 後 `run` 不得重建；`run` 開頭偵測到既有索引只建議不動手、結尾索引缺失只提醒。
2. 進度列計時拆分（fetch／land／sleep／other）是**本段平均**而非累計；run 摘要印各資料集總計與平均。
免 token、免網路；計時測試用假 client 注入可控延遲。
"""
from __future__ import annotations

import logging
import re
import sqlite3
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

from iching import config as C  # noqa: E402
from iching.store import Store, open_stores  # noqa: E402

import backfill_hetzner as B  # noqa: E402

DV = "fm-20260911-01"
DAYS = [f"2022-01-{d:02d}" for d in (3, 4, 5, 6)]


def _index_names(db_path: Path, table: str) -> set[str]:
    """直接用 PRAGMA index_list 斷言（不經 Store 的包裝），只取 CREATE INDEX 建的（origin='c'）。"""
    c = sqlite3.connect(db_path)
    try:
        return {r[1] for r in c.execute(f'PRAGMA index_list("{table}")') if r[3] == "c"}
    finally:
        c.close()


def _seed(cache: Path, create_indexes: bool) -> None:
    """種台北日曆（index_price）＋ info；create_indexes 決定種下去時有沒有次要索引（模擬舊版程式建的 DB）。"""
    stores = open_stores(cache, C.DB_FILES)
    stores["prices"].record_success("index_price", "raw_index_price", "TAIEX:2022-01-01~2022-12-31",
                                    [{"date": d, "stock_id": "TAIEX"} for d in DAYS], DV, "TaiwanStockPrice",
                                    create_indexes=create_indexes)
    stores["universe"].record_success("stock_info", "raw_stock_info", "all",
                                      [{"stock_id": s, "type": "twse", "industry_category": "x"} for s in ("2330", "1101")],
                                      DV, "TaiwanStockInfo", ("stock_id",), create_indexes=create_indexes)
    for s in stores.values():
        s.close()


class _FakeFM:
    """每鍵：節流 sleep_wait 秒（計入 sleep_s）＋ 網路 delays[i] 秒（第 i 次呼叫）。"""
    def __init__(self, delays, sleep_wait=0.0):
        self.delays = list(delays)
        self.sleep_wait = sleep_wait
        self.sleep_s = 0.0
        self.i = 0
        self.n_requests = 0
        self.n_quota_waits = 0

    def has_token(self):
        return True

    def get(self, dataset, **p):
        d = self.delays[min(self.i, len(self.delays) - 1)]
        self.i += 1
        self.n_requests += 1
        if self.sleep_wait:
            t = time.perf_counter()
            time.sleep(self.sleep_wait)
            self.sleep_s += time.perf_counter() - t
        time.sleep(d)
        return [{"date": p["start_date"], "stock_id": s, "close": 1} for s in ("2330", "1101")]


def _args(*argv):
    return B.build_parser().parse_args(["run", "--no-fallback", *argv])


# ---------------------------------------------------------------------------
# Store 層
# ---------------------------------------------------------------------------
def test_store_declared_index_names_and_pk_autoindex_excluded(tmp_path):
    assert Store.declared_indexes("raw_x", ("stock_id", "date")) == [("idx_raw_x_date", ("date",)), ("idx_raw_x_stock_id_date", ("stock_id", "date"))]
    assert Store.declared_indexes("raw_x", ("date",)) == [("idx_raw_x_date", ("date",))]
    assert Store.declared_indexes("raw_x", ("stock_id",)) == [("idx_raw_x_date", ("date",)), ("idx_raw_x_stock_id", ("stock_id",))]
    with Store(tmp_path / "a.db") as st:
        st.ensure_raw_table("raw_x", ("stock_id", "date"), create_indexes=False)
        # WITHOUT ROWID 的主鍵在 PRAGMA index_list 會以 sqlite_autoindex 出現（origin=pk）——不得被當成次要索引
        assert any(r[1].startswith("sqlite_autoindex") for r in st.conn.execute('PRAGMA index_list("raw_x")'))
        assert st.existing_indexes("raw_x") == set()
        assert st.missing_indexes("raw_x", ("stock_id", "date")) == ["idx_raw_x_date", "idx_raw_x_stock_id_date"]
        assert st.build_indexes("raw_x", ("stock_id", "date")) == ["idx_raw_x_date", "idx_raw_x_stock_id_date"]
        assert st.build_indexes("raw_x", ("stock_id", "date")) == []            # 冪等
        assert st.missing_indexes("raw_x", ("stock_id", "date")) == []
        assert st.drop_indexes("raw_x", ("stock_id", "date")) == ["idx_raw_x_date", "idx_raw_x_stock_id_date"]
        assert st.drop_indexes("raw_x", ("stock_id", "date")) == []             # 冪等
        assert st.existing_indexes("raw_x") == set() and st.existing_indexes("no_such_table") == set()
        # 預設行為不變：ensure_raw_table／record_success 不帶旗標仍建索引（taiex-open-check 等既有呼叫端）
        st.record_success("y", "raw_y", "k", [{"date": "2022-01-03", "stock_id": "2330"}], DV, "X")
        assert st.existing_indexes("raw_y") == {"idx_raw_y_date", "idx_raw_y_stock_id_date"}


# ---------------------------------------------------------------------------
# reindex 子命令：建立／刪除、冪等、run 不重建
# ---------------------------------------------------------------------------
def test_reindex_build_and_drop_are_idempotent(tmp_path, capsys):
    cache = tmp_path / "cache"
    _seed(cache, create_indexes=False)
    assert _index_names(cache / "prices.db", "raw_index_price") == set()
    assert B.main(["--cache-dir", str(cache), "reindex"]) == 0
    out = capsys.readouterr().out
    assert "建立 2 個" in out and "idx_raw_index_price_date、idx_raw_index_price_stock_id_date" in out
    assert "idx_raw_stock_info_date、idx_raw_stock_info_stock_id" in out       # stock_info 宣告 index_cols=("stock_id",)
    assert _index_names(cache / "prices.db", "raw_index_price") == {"idx_raw_index_price_date", "idx_raw_index_price_stock_id_date"}
    assert _index_names(cache / "universe.db", "raw_stock_info") == {"idx_raw_stock_info_date", "idx_raw_stock_info_stock_id"}
    # 再跑一次：全部「無，已是目標狀態」，總數 0
    assert B.main(["--cache-dir", str(cache), "reindex"]) == 0
    out = capsys.readouterr().out
    assert "建立 0 個索引" in out and "建立 2 個" not in out
    # --drop：刪光；再 --drop：0
    assert B.main(["--cache-dir", str(cache), "reindex", "--drop"]) == 0
    out = capsys.readouterr().out
    assert "刪除 4 個索引" in out and "建回來" in out
    assert _index_names(cache / "prices.db", "raw_index_price") == set()
    assert _index_names(cache / "universe.db", "raw_stock_info") == set()
    assert B.main(["--cache-dir", str(cache), "reindex", "--drop"]) == 0
    assert "刪除 0 個索引" in capsys.readouterr().out
    # 主鍵不受影響（WITHOUT ROWID 的 autoindex 還在、列還在）
    c = sqlite3.connect(cache / "prices.db")
    assert c.execute("SELECT COUNT(*) FROM raw_index_price").fetchone()[0] == len(DAYS)
    assert any(r[3] == "pk" for r in c.execute('PRAGMA index_list("raw_index_price")'))
    c.close()
    # 空 cache：不建任何 DB 檔
    empty = tmp_path / "nothing"
    assert B.main(["--cache-dir", str(empty), "reindex"]) == 0
    assert "無事可做" in capsys.readouterr().out and not list(empty.glob("*.db")) if empty.exists() else True


def test_run_after_drop_does_not_recreate_indexes(tmp_path, capsys):
    """`--drop` 後 `run`：create_indexes=False 真的生效——新落地的 raw_price_daily 與既有 raw_index_price 都沒有次要索引。"""
    cache = tmp_path / "cache"
    _seed(cache, create_indexes=True)
    assert B.main(["--cache-dir", str(cache), "reindex", "--drop"]) == 0
    capsys.readouterr()
    stores = open_stores(cache, C.DB_FILES)
    fm = _FakeFM([0.0])
    st = B.run_dataset(C.DATASET_BY_KEY["price_daily"], "daily_slice", stores, fm, None, DV,
                       _args("--dataset", "price_daily", "--from", "2022-01-01", "--to", "2022-01-10"))
    assert st["ok"] == 4 and st["aborted"] is None
    assert stores["prices"].rows_for_key("raw_price_daily", "2022-01-03") == 2
    for s in stores.values():
        s.close()
    assert _index_names(cache / "prices.db", "raw_price_daily") == set()
    assert _index_names(cache / "prices.db", "raw_index_price") == set()
    # 之後 reindex → 兩張表索引齊全（PRAGMA index_list 斷言）
    assert B.main(["--cache-dir", str(cache), "reindex"]) == 0
    assert _index_names(cache / "prices.db", "raw_price_daily") == {"idx_raw_price_daily_date", "idx_raw_price_daily_stock_id_date"}
    assert _index_names(cache / "prices.db", "raw_index_price") == {"idx_raw_index_price_date", "idx_raw_index_price_stock_id_date"}


def test_cmd_run_advises_drop_when_indexes_exist_and_reminds_when_missing(tmp_path, monkeypatch, capsys):
    """run 開頭：既有索引 → WARNING 建議 `reindex --drop`（**不自動刪**）；結尾：索引缺失 → 提醒 `reindex`。
    cmd_run 的 setup_logging 用 basicConfig(force=True) 會拔掉 caplog 的 handler，所以一律看 stdout（StreamHandler 印到 stdout）。"""
    cache = tmp_path / "cache"
    _seed(cache, create_indexes=True)                       # 模擬舊版程式建的 DB：index_price／stock_info 帶索引
    monkeypatch.setattr(B, "FinMind", lambda *a, **k: _FakeFM([0.0]))
    rc = B.main(["--cache-dir", str(cache), "run", "--dataset", "price_daily", "--from", "2022-01-01", "--to", "2022-01-10", "--no-fallback"])
    out = capsys.readouterr().out
    assert rc == 0
    warn = [ln for ln in out.splitlines() if "WARNING" in ln and "有次要索引" in ln]
    assert len(warn) == 1 and "reindex --drop" in warn[0] and "不會自動刪" in warn[0], out
    assert "raw_index_price／raw_stock_info（2 張表）" in warn[0]
    # 沒動手：既有索引還在；新表沒建索引
    assert _index_names(cache / "prices.db", "raw_index_price") == {"idx_raw_index_price_date", "idx_raw_index_price_stock_id_date"}
    assert _index_names(cache / "prices.db", "raw_price_daily") == set()
    assert "⚠ 次要索引尚未建立：raw_price_daily（1 張表）" in out and "reindex` 建回來" in out
    # 全部乾淨（--drop 後）的 run：不再建議；結尾仍提醒缺失（含 index_price／stock_info）
    assert B.main(["--cache-dir", str(cache), "reindex", "--drop"]) == 0
    capsys.readouterr()
    # （不帶 --force：鍵已 covered、0 待抓，偵測與提醒不依賴有沒有抓東西）
    rc = B.main(["--cache-dir", str(cache), "run", "--dataset", "price_daily", "--from", "2022-01-01", "--to", "2022-01-10", "--no-fallback"])
    out = capsys.readouterr().out
    assert rc == 0 and "有次要索引" not in out
    assert "次要索引尚未建立：raw_index_price／raw_price_daily／raw_stock_info（3 張表）" in out
    assert _index_names(cache / "prices.db", "raw_price_daily") == set()
    # reindex 之後再 run：既有索引會被建議 --drop（回補期間），但結尾不再提醒缺失
    assert B.main(["--cache-dir", str(cache), "reindex"]) == 0
    capsys.readouterr()
    rc = B.main(["--cache-dir", str(cache), "run", "--dataset", "price_daily", "--from", "2022-01-01", "--to", "2022-01-10", "--no-fallback"])
    out = capsys.readouterr().out
    assert rc == 0 and "次要索引尚未建立" not in out
    assert any("有次要索引" in ln and "raw_price_daily" in ln for ln in out.splitlines())


# ---------------------------------------------------------------------------
# 進度列計時拆分：本段平均而非累計
# ---------------------------------------------------------------------------
_PROG = re.compile(r"本段 fetch ([\d.]+)s land ([\d.]+)s sleep ([\d.]+)s other ([\d.]+)s")


def test_progress_line_reports_segment_not_cumulative_averages(tmp_path, monkeypatch, caplog):
    """假 client：前 2 鍵網路 0.05s、後 2 鍵 0.15s，節流 0.03s（計入 sleep_s）；過濾 0.06s（land）。
    --progress-every 2 → 兩條進度列：第二條 fetch 必須 ≈0.15（本段），不是累計平均 0.10。"""
    cache = tmp_path / "cache"
    _seed(cache, create_indexes=False)
    stores = open_stores(cache, C.DB_FILES)
    fm = _FakeFM([0.05, 0.05, 0.15, 0.15], sleep_wait=0.03)
    real_filter = B.apply_landing_filter

    def slow_filter(rows, ids):
        time.sleep(0.06)
        return real_filter(rows, ids)
    monkeypatch.setattr(B, "apply_landing_filter", slow_filter)
    caplog.set_level(logging.INFO, logger="backfill")
    st = B.run_dataset(C.DATASET_BY_KEY["price_daily"], "daily_slice", stores, fm, None, DV,
                       _args("--dataset", "price_daily", "--from", "2022-01-01", "--to", "2022-01-10", "--progress-every", "2"))
    for s in stores.values():
        s.close()
    assert st["ok"] == 4
    lines = [r.getMessage() for r in caplog.records if "本段 fetch" in r.getMessage()]
    assert len(lines) == 2, lines
    (f1, l1, s1, o1), (f2, l2, s2, o2) = (tuple(map(float, _PROG.search(x).groups())) for x in lines)
    tol = 0.03
    assert abs(f1 - 0.05) < tol and abs(f2 - 0.15) < tol, (f1, f2)          # 本段：第二段是 0.15，不是累計的 0.10
    assert abs(f2 - 0.10) > tol
    assert abs(l1 - 0.06) < tol and abs(l2 - 0.06) < tol, (l1, l2)
    assert abs(s1 - 0.03) < tol and abs(s2 - 0.03) < tol, (s1, s2)          # 節流 sleep 被從 fetch 扣掉、單獨列出
    assert o1 < tol and o2 < tol
    assert "2/4" in lines[0] and "4/4" in lines[1] and "req/s" in lines[0] and "ETA" in lines[1]
    # 本資料集累計：n_timed=4，總計＝各段之和；摘要行印總計與平均
    assert st["n_timed"] == 4
    assert abs(st["t_fetch"] - 0.40) < 2 * tol and abs(st["t_land"] - 0.24) < 2 * tol and abs(st["t_sleep"] - 0.12) < 2 * tol
    tl = B.timing_summary_line(st)
    assert tl.startswith("計時 4 鍵：fetch Σ") and "land Σ" in tl and "sleep Σ" in tl and "／均 0.10s" in tl
    assert B.timing_summary_line({"n_timed": 0}) is None


def test_timing_counts_failed_keys_and_fake_client_without_sleep_s(tmp_path, caplog):
    """失敗鍵（record_failure 路徑）也計時；沒有 sleep_s 的假 client（既有測試那種）→ sleep 0、不炸。
    用 TransientError（走 except 分支）而非回空：回空的 empty_on_trading_day 路徑是 `continue`，本來就不印進度列。"""
    from iching.fm import TransientError
    cache = tmp_path / "cache"
    _seed(cache, create_indexes=False)
    stores = open_stores(cache, C.DB_FILES)

    class _Bare:
        def get(self, dataset, **p):
            raise TransientError(f"{dataset} {p.get('start_date')}: boom")
    caplog.set_level(logging.INFO, logger="backfill")
    st = B.run_dataset(C.DATASET_BY_KEY["price_daily"], "daily_slice", stores, _Bare(), None, DV,
                       _args("--dataset", "price_daily", "--from", "2022-01-01", "--to", "2022-01-10", "--progress-every", "4"))
    for s in stores.values():
        s.close()
    assert st["failed"] == 4 and st["n_timed"] == 4 and st["t_sleep"] == 0.0
    assert B._client_sleep_s(_Bare()) == 0.0 and B._client_sleep_s(None) == 0.0
    lines = [r.getMessage() for r in caplog.records if "本段 fetch" in r.getMessage()]
    assert len(lines) == 1 and "sleep 0.00s" in lines[0]


def test_fm_and_official_client_accumulate_sleep_s():
    """FinMind／OfficialClient 的 sleep_s 只累計等待、不改任何行為（注入假 sleep 與時鐘）。"""
    from iching.fm import FinMind
    from iching.twse import OfficialClient
    slept = []
    clock = {"t": 0.0}
    fm = FinMind(token="x", min_interval=0.7, sleep=lambda s: slept.append(s), clock=lambda: clock["t"])
    assert fm.sleep_s == 0.0
    fm._throttle()                        # 第一次：距 _last_call=0 已 0 秒 → 等 0.7
    assert slept == [0.7] and fm.sleep_s >= 0.0
    clock["t"] = 10.0
    fm._throttle()                        # 距上次 10 秒 → 不等
    assert slept == [0.7]
    oc = OfficialClient(interval=4.0, sleep=lambda s: slept.append(s), clock=lambda: clock["t"])
    assert oc.sleep_s == 0.0
