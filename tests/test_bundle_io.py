"""每日班原料包（`iching.bundle_io`＋`scripts/export_bundles.py`）：決定性、往返、**餵原料包重建的 WindowCache 與直接讀 DB 逐位相同**。"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import export_bundles as EX  # noqa: E402
import scan_features as SF  # noqa: E402
from iching import bundle_io as B  # noqa: E402
from iching import replay_io as RIO  # noqa: E402
from iching import replay_state as RS  # noqa: E402
from synth_db import DAYS, DV, build_full  # noqa: E402


@pytest.fixture(scope="module")
def cache(tmp_path_factory) -> Path:
    c = tmp_path_factory.mktemp("bundle") / "cache"
    build_full(c)
    assert SF.main(["--cache-dir", str(c), "--quiet", "--allow-short-warmup", "--warmup-days", "0"]) == 0
    return c


def test_roundtrip_is_deterministic_and_lossless(cache, tmp_path):
    src = RIO.ReplaySource(cache, DV, window=30)
    b = src.read_day(DAYS[-1])
    src.close()
    raw1, raw2 = B.dumps(b), B.dumps(b)
    assert raw1 == raw2
    b2 = B.loads(raw1)
    assert B.dumps(b2) == raw1                                   # 讀回再寫逐位元相同
    assert b2.tpe_date == b.tpe_date and set(b2.stocks) == set(b.stocks) and b2.official == b.official
    assert b2.futures == b.futures and b2.vix == b.vix and [tuple(x) for x in b2.us] == list(b.us)
    # NaN 進去變 None、讀回 ingest 當缺值
    b.stocks["1101"]["margin_balance"] = float("nan")
    d = B.bundle_to_dict(b)
    assert d["stocks"]["1101"]["margin_balance"] is None
    p = B.write_bundle(tmp_path, b)
    assert p.name == f"{DAYS[-1]}-daily.json.gz" and B.read_bundle(p).stocks["1101"]["margin_balance"] is None
    p2 = B.write_bundle(tmp_path, b)
    assert p2.read_bytes() == p.read_bytes()                     # gzip mtime=0 → 同內容同位元組
    assert B.list_bundles(tmp_path) == [(DAYS[-1], p)]
    with pytest.raises(B.BundleError):
        B.bundle_from_dict({"schema": 99, "tpe_date": "x"})
    with pytest.raises(B.BundleError):
        B.read_bundle(tmp_path / "沒有.json.gz")


def test_window_rebuilt_from_bundles_is_bitwise_identical(cache, tmp_path, capsys):
    """每日班路徑：讀原料包 → ingest；Hetzner 路徑：read_day → ingest。兩者的 WindowCache 逐位相同（features 除外，每日班自己算）。"""
    assert EX.main(["--cache-dir", str(cache), "--out", str(tmp_path), "--quiet"]) == 0
    out = capsys.readouterr().out
    assert "匯出 80 日" in out
    files = B.list_bundles(tmp_path)
    assert [d for d, _ in files] == DAYS
    src = RIO.ReplaySource(cache, DV, window=30)
    wa = RS.WindowCache(src.pool, src.factors, window=30)
    wb = RS.WindowCache(src.pool, src.factors, window=30)
    for T in DAYS:
        wa.ingest(src.read_day(T))
    src.close()
    for _, p in files:
        wb.ingest(B.read_bundle(p))
    cross = RS.CrossDayState()
    for sid in wa.stock_ids_today():
        assert np.array_equal(wa.stock_window(sid), wb.stock_window(sid), equal_nan=True), sid
    for m in ("twse", "tpex"):
        a, b = wa.market_inputs(m, DAYS[-1], cross), wb.market_inputs(m, DAYS[-1], cross)
        for k in ("index_close", "amount", "foreign_net_amount", "trust_net_amount", "margin_balance", "foreign_net_oi",
                  "basis", "vix", "spx_close", "sox_close", "fx_usdtwd"):
            x, y = getattr(a, k), getattr(b, k)
            assert (x is None) == (y is None), (m, k)
            if x is not None:
                assert np.array_equal(np.asarray(x), np.asarray(y), equal_nan=True), (m, k)
        assert a.us_dates == b.us_dates and a.fx_dates == b.fx_dates and a.contract_rolled == b.contract_rolled
        # 廣度來自 features，原料包刻意不帶 → 每日班自己算；這裡只確認它確實是空的而非錯值
        assert b.n_stocks is not None and np.isnan(b.n_stocks).all()
    assert wa.shares == wb.shares
    assert EX.main(["--cache-dir", str(cache), "--out", str(tmp_path), "--last", "3", "--from", DAYS[0]]) == 2
