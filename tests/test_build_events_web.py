"""`scripts/build_events_web.py`（P4-E1 事件層 1，`docs/P4-PREVIEW.md` §17）。免 token 免網路；以合成的 `data/events` 驗契約。"""
from __future__ import annotations

import gzip
import hashlib
import importlib.util
import json
import socket
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("build_events_web", ROOT / "scripts" / "build_events_web.py")
EW = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(EW)

# 交易日曆：9/1～9/30 的平日（無休市），T＝9/30 → 往前第 20 個交易日（不含）＝9/2 → 窗＝9/3～9/30
CAL = [f"2026-09-{d:02d}" for d in range(1, 31) if __import__("datetime").date(2026, 9, d).weekday() < 5]
T = "2026-09-30"


def ver(no, subject="主旨", clause="第51款", fact="2026-09-01", body="原文", status="active", missing=False):
    return {"version_no": no, "version_id": f"x#{no}", "content_hash": "h", "status": status, "supersedes": None,
            "fulltext_missing": missing, "first_seen_at": "2026-09-01T00:00:00+08:00", "revised_at": "2026-09-01T00:00:00+08:00",
            "subject": subject, "clause": clause, "fact_date": fact, "body": None if missing else body, "sources": ["mopsfin"]}


def ev(sid, date, time, versions, market="sii", name="名"):
    return {"market": market, "stock_id": sid, "name": name, "spoke_date": date, "spoke_time": time, "versions": versions}


def write_events(root: Path, date: str, events: dict):
    d = root / "data" / "events"
    d.mkdir(parents=True, exist_ok=True)
    with gzip.open(d / f"{date}.json.gz", "wt", encoding="utf-8") as f:
        json.dump({"schema": 1, "date": date, "events": events}, f, ensure_ascii=False)


def make_repo(tmp: Path, stocks=("2330", "2317", "1101", "6949"), date=T, cal=CAL) -> Path:
    (tmp / "data" / "web").mkdir(parents=True, exist_ok=True)
    (tmp / "data" / "web" / "latest.json").write_text(json.dumps({"schema": 1, "date": date, "stocks": {s: {} for s in stocks}}),
                                                      encoding="utf-8")
    (tmp / "data" / "calendar_tpe.json").write_text(json.dumps({"dates": cal}), encoding="utf-8")
    return tmp


def run(root: Path) -> int:
    return EW.main(["--root", str(root)])


def shard(root: Path, name: str) -> dict:
    return json.loads((root / "data" / "web" / "ev" / name).read_text(encoding="utf-8"))


def tree(root: Path) -> dict[str, str]:
    return {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(root.rglob("*")) if p.is_file()}


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("build_events_web 不得碰網路")
    monkeypatch.setattr(socket, "socket", boom)
    monkeypatch.setattr(socket, "create_connection", boom)


def test_window_bounds_weekend_and_out_of_window(tmp_path):
    root = make_repo(tmp_path)
    write_events(root, "2026-09-02", {"e0": ev("2330", "2026-09-02", "10:00:00", [ver(1, "窗外（第 20 個交易日當天）")])})
    write_events(root, "2026-09-03", {"e1": ev("2330", "2026-09-03", "10:00:00", [ver(1, "窗內第一日")])})
    write_events(root, "2026-09-05", {"e2": ev("2330", "2026-09-05", "10:00:00", [ver(1, "週六")])})
    write_events(root, "2026-10-01", {"e3": ev("2330", "2026-10-01", "10:00:00", [ver(1, "T 之後")])})
    assert run(root) == 0
    j = shard(root, "23.json")
    assert j["window"] == ["2026-09-03", T] and j["date"] == T and j["events_upto"] == "2026-10-01"
    assert [r[3] for r in j["stocks"]["2330"]] == ["週六", "窗內第一日"]
    assert j["cols"] == ["id", "d", "t", "s", "c", "f", "m", "v", "b"]


def test_window_weekend_right_after_start_exclusive_day(tmp_path):
    # 起點不含的交易日是週五（9/4）時，窗從週六 9/5 起（含週末）
    cal = CAL + ["2026-10-01", "2026-10-02"]
    root = make_repo(tmp_path, date="2026-10-02", cal=cal)
    assert EW.window_of("2026-10-02", cal) == ("2026-09-05", "2026-10-02")
    write_events(root, "2026-09-04", {"a": ev("2330", "2026-09-04", "10:00:00", [ver(1, "週五不含")])})
    write_events(root, "2026-09-05", {"b": ev("2330", "2026-09-05", "10:00:00", [ver(1, "週六含")])})
    assert run(root) == 0
    assert [r[3] for r in shard(root, "23.json")["stocks"]["2330"]] == ["週六含"]


def test_window_short_calendar_and_T_missing(tmp_path):
    assert EW.window_of("2026-09-03", CAL) == ("2026-09-01", "2026-09-03")
    with pytest.raises(EW.BuildEventsError):
        EW.window_of("2026-09-06", CAL)
    root = make_repo(tmp_path, date="2026-09-06")
    assert run(root) == 2 and not (root / "data" / "web" / "ev").exists()


def test_sort_and_version_choice_and_fulltext_missing(tmp_path):
    root = make_repo(tmp_path)
    write_events(root, "2026-09-29", {
        "bbb": ev("2330", "2026-09-29", "09:00:00", [ver(1)]),
        "aaa": ev("2330", "2026-09-29", "09:00:00", [ver(1, "同時間 id 小")]),
        "ccc": ev("2330", "2026-09-29", "17:30:00", [ver(1, "舊版", status="superseded"), ver(2, "更正版", body="新原文")]),
        "ddd": ev("2330", "2026-09-29", "18:00:00", [ver(1, "只有主旨", missing=True)]),
        "eee": ev("2330", "2026-09-29", "19:00:00", [ver(1, "全撤", status="superseded")]),
    })
    write_events(root, "2026-09-30", {"fff": ev("2330", "2026-09-30", "08:00:00", [ver(1, "最新日")])})
    assert run(root) == 0
    rows = shard(root, "23.json")["stocks"]["2330"]
    assert [r[0] for r in rows] == ["fff", "ddd", "ccc", "aaa", "bbb"]
    ccc = rows[2]
    assert ccc[3] == "更正版" and ccc[7] == 2 and ccc[8] == "新原文" and ccc[6] == 0
    ddd = rows[1]
    assert ddd[6] == 1 and ddd[8] is None and ddd[3] == "只有主旨"
    assert rows[0] == ["fff", "2026-09-30", "08:00:00", "最新日", "第51款", "2026-09-01", 0, 1, "原文"]
    m = shard(root, "market.json")
    assert m["n"]["no_active"] == 1 and m["n"]["events"] == 5


def test_only_latest_stocks_and_empty_prefix_shards(tmp_path):
    root = make_repo(tmp_path, stocks=("2330", "2317", "1101", "6949", "00631L"))
    write_events(root, "2026-09-29", {"a": ev("2330", "2026-09-29", "10:00:00", [ver(1)]),
                                      "b": ev("9999", "2026-09-29", "11:00:00", [ver(1, "非本站")], market="otc")})
    assert run(root) == 0
    names = sorted(p.name for p in (root / "data" / "web" / "ev").iterdir())
    assert names == ["00.json", "11.json", "23.json", "69.json", "market.json"]
    assert shard(root, "11.json")["stocks"] == {} and shard(root, "69.json")["stocks"] == {}
    assert shard(root, "11.json")["date"] == T
    assert set(shard(root, "23.json")["stocks"]) == {"2330"}
    assert not any("9999" in json.dumps(shard(root, n)) for n in names if n != "market.json")
    m = shard(root, "market.json")
    assert m["n"] == {"events": 2, "site": 1, "not_site": 1, "no_active": 0}
    assert [r[1] for r in m["rows"]] == ["9999", "2330"] and [r[8] for r in m["rows"]] == [0, 1]


def test_old_shards_deleted_others_untouched(tmp_path):
    root = make_repo(tmp_path)
    out = root / "data" / "web" / "ev"
    out.mkdir(parents=True)
    (out / "99.json").write_text("{}", encoding="utf-8")
    (out / "README.txt").write_text("keep", encoding="utf-8")
    assert run(root) == 0
    assert not (out / "99.json").exists() and (out / "README.txt").read_text(encoding="utf-8") == "keep"


def test_deterministic_and_writes_only_ev_and_events_untouched(tmp_path):
    root = make_repo(tmp_path)
    write_events(root, "2026-09-29", {"a": ev("2330", "2026-09-29", "10:00:00", [ver(1, "x<img src=x onerror=1>")]),
                                      "b": ev("1101", "2026-09-28", "10:00:00", [ver(1)]) | {"spoke_date": "2026-09-29"}})
    before = tree(root)
    assert run(root) == 0
    after1 = tree(root)
    changed = {k for k in after1 if before.get(k) != after1[k]}
    assert changed and all(k.startswith("data/web/ev/") for k in changed)
    assert {k: v for k, v in after1.items() if not k.startswith("data/web/ev/")} == before
    assert run(root) == 0
    assert tree(root) == after1
    raw = (root / "data" / "web" / "ev" / "23.json").read_bytes()
    assert raw.endswith(b"\n") and b"\n" not in raw[:-1] and not list(root.rglob("*.tmp"))


def test_market_upto_lagging_trading_day_plus_weekend(tmp_path):
    """使用者 2026-10-19 決定：U＝min(T, events_upto)；D＝≤U 的最近交易日；清單＝D～U（含其後週末）全部公告、每則帶發言日。
    T（9/30 週三）為交易日但收錄只到 9/27（週日）→ U＝9/27、D＝9/25（週五），收 9/25～9/27；9/24 不收。"""
    root = make_repo(tmp_path)
    write_events(root, "2026-09-24", {"a": ev("2330", "2026-09-24", "10:00:00", [ver(1, "週四不收")])})
    write_events(root, "2026-09-25", {"b": ev("2330", "2026-09-25", "09:00:00", [ver(1, "週五早")]),
                                      "c": ev("1101", "2026-09-25", "15:00:00", [ver(1, "週五晚", missing=True)], market="otc"),
                                      "d": ev("2317", "2026-09-25", "15:00:00", [ver(1, "同時間代號小")])})
    write_events(root, "2026-09-26", {"e": ev("9999", "2026-09-26", "08:00:00", [ver(1, "週六")])})
    write_events(root, "2026-09-27", {"f": ev("2330", "2026-09-27", "07:00:00", [ver(1, "週日")])})
    assert run(root) == 0
    m = shard(root, "market.json")
    assert m["cols"] == ["d", "sid", "n", "mk", "t", "s", "c", "m", "in"]
    assert (m["from"], m["to"], m["days"]) == ("2026-09-25", "2026-09-27", ["2026-09-25", "2026-09-26", "2026-09-27"])
    assert [(r[0], r[5]) for r in m["rows"]] == [("2026-09-27", "週日"), ("2026-09-26", "週六"), ("2026-09-25", "週五晚"),
                                                ("2026-09-25", "同時間代號小"), ("2026-09-25", "週五早")]
    assert m["rows"][2][3] == "otc" and m["rows"][2][7] == 1 and m["rows"][1][8] == 0 and m["rows"][0][8] == 1
    assert m["counts"]["2026-09-24"] == {"sii": [1, 0]} and m["events_upto"] == "2026-09-27" and m["window"] == ["2026-09-03", T]


def test_market_D_without_events_steps_back_to_previous_trading_day(tmp_path):
    """T＝9/28（週一）、收錄到 9/28 但 9/28 無事件 → D 改 9/25（週五）、迄日＝9/27（D 的前一曆日），含週末；T 之後的不收。"""
    cal = [d for d in CAL if d <= "2026-09-28"]
    root = make_repo(tmp_path, date="2026-09-28", cal=cal)
    write_events(root, "2026-09-25", {"a": ev("2330", "2026-09-25", "10:00:00", [ver(1, "週五")])})
    write_events(root, "2026-09-27", {"b": ev("2330", "2026-09-27", "10:00:00", [ver(1, "週日")])})
    write_events(root, "2026-09-28", {})
    assert run(root) == 0
    m = shard(root, "market.json")
    assert (m["from"], m["to"], m["days"]) == ("2026-09-25", "2026-09-27", ["2026-09-25", "2026-09-27"])
    assert [r[5] for r in m["rows"]] == ["週日", "週五"]
    # 收錄超前 T：U＝T（9/28 無事件 → 退回）；T 之後的 9/29 不算
    write_events(root, "2026-09-29", {"c": ev("2330", "2026-09-29", "10:00:00", [ver(1, "T 之後")])})
    assert run(root) == 0
    m = shard(root, "market.json")
    assert (m["from"], m["to"]) == ("2026-09-25", "2026-09-27") and m["events_upto"] == "2026-09-29"


def test_market_steps_back_multiple_days_bounded_by_window(tmp_path):
    root = make_repo(tmp_path)
    write_events(root, "2026-09-02", {"x": ev("2330", "2026-09-02", "10:00:00", [ver(1, "窗外")])})
    write_events(root, "2026-09-03", {"a": ev("2330", "2026-09-03", "10:00:00", [ver(1, "窗內最早")])})
    write_events(root, "2026-09-30", {})
    assert run(root) == 0
    m = shard(root, "market.json")
    assert (m["from"], m["to"], m["days"]) == ("2026-09-03", "2026-09-03", ["2026-09-03"]) and [r[5] for r in m["rows"]] == ["窗內最早"]


def test_market_no_events_in_window(tmp_path):
    root = make_repo(tmp_path)
    write_events(root, "2026-09-02", {"x": ev("2330", "2026-09-02", "10:00:00", [ver(1, "窗外")])})
    assert run(root) == 0
    m = shard(root, "market.json")
    assert (m["from"], m["to"], m["days"], m["rows"]) == (None, None, [], []) and m["counts"] == {}
    assert shard(root, "23.json")["stocks"] == {}
    (root / "data" / "events" / "2026-09-02.json.gz").unlink()
    assert run(root) == 0
    m = shard(root, "market.json")
    assert (m["from"], m["to"], m["rows"], m["events_upto"]) == (None, None, [], None)


@pytest.mark.parametrize("bad", ["latest", "calendar", "event"])
def test_abort_writes_nothing(tmp_path, bad):
    root = make_repo(tmp_path)
    out = root / "data" / "web" / "ev"
    out.mkdir(parents=True)
    (out / "23.json").write_text("old", encoding="utf-8")
    if bad == "latest":
        (root / "data" / "web" / "latest.json").write_text("{", encoding="utf-8")
    elif bad == "calendar":
        (root / "data" / "calendar_tpe.json").write_text(json.dumps({"dates": "x"}), encoding="utf-8")
    else:
        d = root / "data" / "events"
        d.mkdir(parents=True)
        with gzip.open(d / "2026-09-29.json.gz", "wt", encoding="utf-8") as f:
            json.dump({"schema": 1, "date": "2026-09-28", "events": {}}, f)
    assert run(root) == 2
    assert sorted(p.name for p in out.iterdir()) == ["23.json"] and (out / "23.json").read_text(encoding="utf-8") == "old"



def test_daily_workflow_ev_step():
    """E1-4：步驟序 web→ev→status→commit；ev 有 id、ok=1/0 分支、只還原 data/web/ev/、無 continue-on-error；
    ev 紅燈在 commit 之後、含 !cancelled()、依 commit outcome 兩種措辭；ind 紅燈仍是倒數第二步、notify 最後。"""
    import os
    import subprocess

    import yaml
    d = yaml.safe_load((ROOT / ".github/workflows/daily.yml").read_text(encoding="utf-8"))
    steps = d["jobs"]["daily"]["steps"]
    assert all("continue-on-error" not in s for s in steps) and "continue-on-error" not in d["jobs"]["daily"]
    idx = lambda pred: next(i for i, s in enumerate(steps) if pred(s))  # noqa: E731
    i_web, i_ev, i_status, i_commit = (idx(lambda s, k=k: s.get("id") == k) for k in ("web", "ev", "status", "commit"))
    assert i_web < i_ev < i_status < i_commit
    e = steps[i_ev]
    assert "build_events_web.py --root ." in e["run"] and "if" not in e
    assert 'echo "ok=1" >> "$GITHUB_OUTPUT"' in e["run"] and 'echo "ok=0" >> "$GITHUB_OUTPUT"' in e["run"]
    restore = [ln.strip() for ln in e["run"].splitlines() if "git checkout" in ln or "git clean" in ln or "rm " in ln]
    assert restore == ["git checkout -- data/web/ev/ 2>/dev/null || true", "git clean -fdq -- data/web/ev/ 2>/dev/null || true"]
    i_red = idx(lambda s: "steps.ev.outputs.ok" in str(s.get("if") or ""))
    red = steps[i_red]
    assert i_commit < i_red < len(steps) - 2 and red["if"] == "${{ !cancelled() && steps.ev.outputs.ok == '0' }}"
    assert red["env"] == {"COMMIT_OUTCOME": "${{ steps.commit.outcome }}"}
    assert "steps.ind.outputs.ok" in steps[-2]["if"] and steps[-1]["uses"] == "./.github/actions/notify-failure"

    def sh(outcome):
        p = subprocess.run(["bash", "-eo", "pipefail", "-c", red["run"]], capture_output=True, text=True,
                           env={**os.environ, "COMMIT_OUTCOME": outcome})
        return p.returncode, p.stdout
    rc, out = sh("success")
    assert rc == 1 and out.startswith("::error::") and "步驟成功" in out and "可能未進 main" not in out
    rc, out = sh("failure")
    assert rc == 1 and "可能未進 main" in out and "步驟成功" not in out


def test_collect_events_workflow_untouched_by_e1():
    """collect-events.yml 不寫 data/web/（目錄互斥）；本 PR 不改它（位元組比對由驗收以 git diff 做）。"""
    text = (ROOT / ".github/workflows/collect-events.yml").read_text(encoding="utf-8")
    assert "build_events_web" not in text and "data/web" not in text
