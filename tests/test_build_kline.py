"""`scripts/build_kline.py`（P4-K 日 K，`docs/P4-PREVIEW.md` §19）。免 token 免網路；以合成原料包驗契約。"""
from __future__ import annotations

import datetime as dt
import gzip
import hashlib
import importlib.util
import json
import socket
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("build_kline", ROOT / "scripts" / "build_kline.py")
BK = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(BK)


def weekdays(n: int, start=dt.date(2026, 1, 5)) -> list[str]:
    out, d = [], start
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d.isoformat())
        d += dt.timedelta(days=1)
    return out


def px(i: int) -> float:
    return 100.0 + i                              # 收盤＝100＋第 i 天，均線好驗算


def write_bundle(root: Path, d: str, i: int, stocks: dict | None = None, index=True):
    p = root / "runs" / "collect"
    p.mkdir(parents=True, exist_ok=True)
    if stocks is None:
        stocks = {"2330": {"open": px(i) - 1, "high": px(i) + 2, "low": px(i) - 2, "close": px(i), "volume": 1234567.0},
                  "2317": {"open": 50.0, "high": 51.0, "low": 49.0, "close": 50.5, "volume": 999.0}}
    b = {"schema": 1, "tpe_date": d, "stocks": stocks}
    if index:
        b["index"] = {"twse": {"open": 1.0, "high": 3.0, "low": 0.5, "close": 2.0 + i}, "tpex": {"open": 1, "high": 2, "low": 1, "close": 1}}
        b["official"] = {"twse": {"amount_k": 123456789.0}, "tpex": {"amount_k": None}}
    with gzip.open(p / f"{d}-daily.json.gz", "wt", encoding="utf-8") as f:
        json.dump(b, f)


def make_repo(tmp: Path, n_days=200, site=("2330", "2317", "1101", "6949"), factors=None, skip=()) -> tuple[Path, list[str]]:
    cal = weekdays(n_days)
    (tmp / "data" / "web").mkdir(parents=True, exist_ok=True)
    (tmp / "data" / "web" / "latest.json").write_text(json.dumps({"date": cal[-1], "stocks": {s: {} for s in site}}), encoding="utf-8")
    (tmp / "data" / "calendar_tpe.json").write_text(json.dumps({"dates": cal}), encoding="utf-8")
    (tmp / "data" / "factors.json").write_text(json.dumps({"schema": 1, "rows": factors or []}), encoding="utf-8")
    for i, d in enumerate(cal):
        if d not in skip:
            write_bundle(tmp, d, i)
    return tmp, cal


def run(root: Path) -> int:
    return BK.main(["--root", str(root)])


def shard(root: Path, name: str) -> dict:
    return json.loads((root / "data" / "web" / "k" / name).read_text(encoding="utf-8"))


def tree(root: Path) -> dict[str, str]:
    return {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(root.rglob("*")) if p.is_file()}


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("build_kline 不得碰網路")
    monkeypatch.setattr(socket, "create_connection", boom)
    monkeypatch.setattr(socket.socket, "connect", boom)


def test_basic_contract(tmp_path):
    root, cal = make_repo(tmp_path)
    assert run(root) == 0
    j = shard(root, "23.json")
    assert j["schema"] == 1 and j["date"] == cal[-1] and j["price_basis"] == "raw" and j["cols"] == ["o", "h", "l", "c", "v"]
    assert j["dates"] == cal[-120:] and j["unit"] == {"v": "張"} and j["ma"] == [20, 60]
    s = j["stocks"]["2330"]
    i = len(cal) - 1
    assert s["k"][-1] == [px(i) - 1, px(i) + 2, px(i) - 2, px(i), 1235]       # 1,234,567 股 → 1,235 張（四捨五入）
    assert len(s["k"]) == len(s["m20"]) == len(s["m60"]) == 120
    assert s["m20"][-1] == round(sum(px(k) for k in range(i - 19, i + 1)) / 20, 2)
    assert s["m60"][-1] == round(sum(px(k) for k in range(i - 59, i + 1)) / 60, 2)
    assert s["x"] == []
    assert set(j["stocks"]) == {"2330", "2317"}
    # 涵蓋的每個前綴都有片；無資料的代號不列（11、69 為空片）
    for g in ("11", "69"):
        assert shard(root, f"{g}.json")["stocks"] == {}
    m = shard(root, "market.json")
    assert m["unit"] == {"v": "億元"} and m["dates"] == cal[-120:]
    assert m["idx"]["twse"]["k"][-1] == [1.0, 3.0, 0.5, 2.0 + i, 1234.6]
    assert m["idx"]["tpex"]["k"][-1] == [1.0, 2.0, 1.0, 1.0, None]             # 成交值缺 → 量 null，K 棒照畫


def test_reads_exactly_179_bundles_and_ma_window(tmp_path):
    root, cal = make_repo(tmp_path, n_days=179)
    assert run(root) == 0
    s = shard(root, "23.json")["stocks"]["2330"]
    assert s["m60"][0] is not None                                           # 第一根輸出 K 棒剛好有 60 天窗
    root2, cal2 = make_repo(tmp_path / "b", n_days=178)
    assert run(root2) == 0
    s2 = shard(root2, "23.json")["stocks"]["2330"]
    assert s2["m60"][0] is None and s2["m60"][1] is not None and s2["m20"][0] is not None


def test_short_history_outputs_what_exists(tmp_path):
    root, cal = make_repo(tmp_path, n_days=30)
    assert run(root) == 0
    s = shard(root, "23.json")["stocks"]["2330"]
    assert len(s["k"]) == 30 and s["m60"] == [None] * 30
    assert s["m20"][:19] == [None] * 19 and s["m20"][19] is not None


def test_missing_bundle_aborts_without_writing(tmp_path):
    root, cal = make_repo(tmp_path, skip={weekdays(200)[150]})
    before = tree(root)
    assert run(root) == 2
    assert tree(root) == before


def test_old_gap_outside_window_is_fine(tmp_path):
    root, cal = make_repo(tmp_path, skip={weekdays(200)[5]})                 # 179 天窗之外的洞不影響
    assert run(root) == 0


def test_t_bundle_missing_or_not_in_calendar(tmp_path):
    root, cal = make_repo(tmp_path, skip={weekdays(200)[-1]})
    assert run(root) == 2
    root2, cal2 = make_repo(tmp_path / "b")
    (root2 / "data" / "web" / "latest.json").write_text(json.dumps({"date": "2027-01-01", "stocks": {}}), encoding="utf-8")
    assert run(root2) == 2


def test_null_fields_and_absent_stock(tmp_path):
    root, cal = make_repo(tmp_path, n_days=100)
    i = 99
    write_bundle(root, cal[-1], i, stocks={"2330": {"open": None, "high": 1, "low": 1, "close": 1, "volume": 5}})
    write_bundle(root, cal[-2], i - 1, stocks={"2330": {"open": 1, "high": 1, "low": 1, "close": 1, "volume": None}})
    assert run(root) == 0
    s = shard(root, "23.json")["stocks"]["2330"]
    assert s["k"][-1] is None and s["m20"][-1] is None                       # 缺 open → 整根 null；均線窗內有缺 → null
    assert s["k"][-2] == [1.0, 1.0, 1.0, 1.0, None]                          # 量缺 → 量 null，K 棒照畫
    assert shard(root, "23.json")["stocks"]["2317"]["k"][-1] is None         # 該日包內無此股 → null


def test_raw_price_not_adjusted_and_exdiv_marks(tmp_path):
    cal = weekdays(200)
    rows = [["2330", cal[-10], 110.0, 105.0], ["2330", cal[-10], 110.0, 105.0], ["2330", cal[-3], 120.0, 119.5],
            ["2330", cal[0], 1.0, 0.5], ["2317", "2030-01-01", 1, 1], ["9999", cal[-2], 1, 1]]
    root, cal = make_repo(tmp_path, factors=rows)
    assert run(root) == 0
    s = shard(root, "23.json")["stocks"]["2330"]
    assert s["x"] == [[cal[-10], 110.0, 105.0], [cal[-3], 120.0, 119.5]]   # 去重、只留輸出區間內
    assert s["k"][-1][3] == px(len(cal) - 1)                                 # 原始價不還原
    assert shard(root, "23.json")["stocks"]["2317"]["x"] == []


def test_stale_shards_removed_and_idempotent(tmp_path):
    root, cal = make_repo(tmp_path)
    out = root / "data" / "web" / "k"
    out.mkdir(parents=True)
    (out / "99.json").write_text("{}", encoding="utf-8")
    (out / "keep.txt").write_text("x", encoding="utf-8")
    assert run(root) == 0
    assert not (out / "99.json").exists() and (out / "keep.txt").exists()
    a = tree(root)
    assert run(root) == 0
    assert tree(root) == a


def test_bad_factors_shape_aborts(tmp_path):
    root, cal = make_repo(tmp_path)
    (root / "data" / "factors.json").write_text(json.dumps({"rows": [["2330", "bad", 1, 1]]}), encoding="utf-8")
    assert run(root) == 2
    assert not (root / "data" / "web" / "k").exists()


def test_sma_pure():
    assert BK.sma([1.0, 2.0, 3.0, 4.0], 2, 3) == [1.5, 2.5, 3.5]
    assert BK.sma([1.0, None, 3.0, 4.0], 2, 3) == [None, None, 3.5]
    assert BK.sma([1.0, 2.0], 3, 2) == [None, None]


def test_daily_workflow_kline_step():
    """daily.yml：kline 在 ev 之後、status／commit 之前；ok=1/0 分支、只還原 data/web/k/、無 continue-on-error；紅燈在 commit 之後。"""
    import yaml
    steps = yaml.safe_load((ROOT / ".github/workflows/daily.yml").read_text(encoding="utf-8"))["jobs"]["daily"]["steps"]
    assert all("continue-on-error" not in s for s in steps)
    idx = lambda pred: next(i for i, s in enumerate(steps) if pred(s))  # noqa: E731
    i_ev, i_k, i_st, i_commit = (idx(lambda s, x=x: s.get("id") == x) for x in ("ev", "kline", "status", "commit"))
    assert i_ev < i_k < i_st < i_commit
    st = steps[i_k]
    assert "if" not in st and "python scripts/build_kline.py --root ." in st["run"]
    assert 'echo "ok=1" >> "$GITHUB_OUTPUT"' in st["run"] and 'echo "ok=0" >> "$GITHUB_OUTPUT"' in st["run"]
    restore = [ln.strip() for ln in st["run"].splitlines() if "git checkout" in ln or "git clean" in ln]
    assert len(restore) == 2 and all("data/web/k/" in ln for ln in restore), restore
    i_red = idx(lambda s: "steps.kline.outputs.ok" in str(s.get("if") or ""))
    red = steps[i_red]
    assert i_red > i_commit and red["if"] == "${{ !cancelled() && steps.kline.outcome == 'success' && steps.kline.outputs.ok == '0' }}"
    assert red["env"] == {"COMMIT_OUTCOME": "${{ steps.commit.outcome }}"} and "exit 1" in red["run"]
    commit = steps[i_commit]["run"]
    assert "git add data" in commit                                          # data/web/k/ 隨 data 一併 commit
