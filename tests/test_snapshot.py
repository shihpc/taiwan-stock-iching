"""P4-B1 每日快照（scripts/build_snapshot.py／verify_snapshot.py／.github/workflows/backup.yml；docs/P4-PREVIEW.md §31）。離線。"""
from __future__ import annotations

import datetime as dt
import gzip
import hashlib
import io
import json
import shutil
import sys
import tarfile
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import build_snapshot as BS  # noqa: E402
import verify_snapshot as VS  # noqa: E402

T = "2026-11-18"
NOW = dt.datetime(2026, 11, 18, 23, 0, tzinfo=BS.TPE)


def mkrepo(tmp: Path) -> Path:
    r = tmp / "repo"
    files = {
        "data/state/cross.json": "{}",
        "data/web/latest.json": json.dumps({"date": T}),
        "data/web/ind/23.json": "{}",
        "data/scores/2026-11-17.json": json.dumps({"data_version": "dv-17"}),
        f"data/scores/{T}.json": json.dumps({"data_version": "dv-18"}),
        "data/events/2026-11-17.json.gz": "x",
        f"data/events/{T}.json.gz": "y",
        "data/pick/2026-11-17.json": "{}", f"data/pick/{T}.json": "{}", "data/pick/t0.json": "{}",
        "data/limits/2026-11-17.json": "{}", f"data/limits/{T}.json": "{}",
        "data/backtest/big.json": "z" * 100,
        "data/pool.json": "{}", "data/entrants/2330.json.gz": "e",
        "data/scores/sub/x.json": "nested",
        "data/web/.hidden": "h", "data/__pycache__/a.pyc": "p",
        f"runs/collect/{T}-daily.json.gz": "c", f"runs/collect/{T}-am.json": "a", "runs/collect/2026-11-17-daily.json.gz": "old",
        f"runs/trial/{T}-0817.json.gz": "t", "runs/trial/2026-11-17-0817.json.gz": "t0",
        "runs/stats/x.json": "s",
    }
    for k, v in files.items():
        p = r / k
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(v, encoding="utf-8")
    return r


EXPECT = sorted([
    "data/state/cross.json", "data/web/latest.json", "data/web/ind/23.json", f"data/scores/{T}.json", f"data/events/{T}.json.gz",
    f"data/pick/{T}.json", "data/pick/t0.json", f"data/limits/{T}.json", "data/pool.json", "data/entrants/2330.json.gz",
    f"runs/collect/{T}-daily.json.gz", f"runs/collect/{T}-am.json", f"runs/trial/{T}-0817.json.gz",
])


def test_select_rules(tmp_path):
    assert BS.select_files(mkrepo(tmp_path), T) == EXPECT


def test_deterministic_and_manifest(tmp_path):
    r = mkrepo(tmp_path)
    m1 = BS.build(r, T, tmp_path / "o1", commit="a" * 40, now=NOW)
    m2 = BS.build(r, T, tmp_path / "o2", commit="b" * 40, now=NOW + dt.timedelta(hours=1))
    a1 = (tmp_path / "o1" / f"snap-{T}.tar.gz").read_bytes()
    assert a1 == (tmp_path / "o2" / f"snap-{T}.tar.gz").read_bytes()
    assert m1["content_sha256"] == m2["content_sha256"] and m1["commit"] != m2["commit"]
    assert m1["schema"] == 1 and m1["date"] == T and m1["batch_id"] == "dv-18" and m1["generated_at"] == "2026-11-18T23:00:00+08:00"
    assert m1["archive"] == {"name": f"snap-{T}.tar.gz", "size": len(a1), "sha256": hashlib.sha256(a1).hexdigest()}
    assert [e["path"] for e in m1["files"]] == EXPECT and m1["n_files"] == len(EXPECT)
    assert m1["total_size"] == sum((r / p).stat().st_size for p in EXPECT)
    e = next(x for x in m1["files"] if x["path"] == "data/pool.json")
    assert e["sha256"] == hashlib.sha256(b"{}").hexdigest()
    disk = json.loads((tmp_path / "o1" / f"manifest-{T}.json").read_text(encoding="utf-8"))
    assert disk == m1
    with tarfile.open(tmp_path / "o1" / f"snap-{T}.tar.gz") as tar:
        ms = tar.getmembers()
    assert [m.name for m in ms] == EXPECT and all(m.mtime == 0 and m.uid == 0 and m.mode == 0o644 for m in ms)
    assert gzip.decompress(a1)[:3]   # 可解
    assert int.from_bytes(a1[4:8], "little") == 0   # gzip header mtime 0


def test_content_change_changes_sha(tmp_path):
    r = mkrepo(tmp_path)
    m1 = BS.build(r, T, tmp_path / "o1", commit=None, now=NOW)
    (r / "data/web/latest.json").write_text(json.dumps({"date": T, "x": 1}), encoding="utf-8")
    m2 = BS.build(r, T, tmp_path / "o2", commit=None, now=NOW)
    assert m1["content_sha256"] != m2["content_sha256"]


def test_main_missing_scores_exit2(tmp_path, capsys):
    r = mkrepo(tmp_path)
    out = tmp_path / "o"
    assert BS.main(["--date", "2026-11-19", "--out", str(out), "--root", str(r)]) == 2
    assert not out.exists()
    assert BS.main(["--date", "2026/11/18", "--out", str(out), "--root", str(r)]) == 2
    assert BS.main(["--date", T, "--out", str(out), "--root", str(r), "--commit", "c" * 40]) == 0
    assert (out / f"snap-{T}.tar.gz").is_file()


def _built(tmp_path):
    r = mkrepo(tmp_path)
    BS.build(r, T, tmp_path / "o", commit="a" * 40, now=NOW)
    return tmp_path / "o" / f"manifest-{T}.json", tmp_path / "o" / f"snap-{T}.tar.gz"


def _retar(src: Path, dst: Path, edit):
    with tarfile.open(src) as tar:
        items = [(m, tar.extractfile(m).read()) for m in tar.getmembers()]
    items = edit(items)
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for m, d in items:
            ti = tarfile.TarInfo(m.name); ti.size = len(d)
            tar.addfile(ti, io.BytesIO(d))
    dst.write_bytes(buf.getvalue())


def test_verify_ok(tmp_path):
    man, arc = _built(tmp_path)
    assert VS.main([str(man), str(arc)]) == 0


@pytest.mark.parametrize("kind", ["tamper", "missing", "extra", "archive_only"])
def test_verify_catches(tmp_path, kind, capsys):
    man, arc = _built(tmp_path)
    bad = tmp_path / "bad.tar.gz"
    if kind == "tamper":
        _retar(arc, bad, lambda it: [(m, b"X" if m.name == "data/pool.json" else d) for m, d in it])
    elif kind == "missing":
        _retar(arc, bad, lambda it: [x for x in it if x[0].name != "data/pool.json"])
    elif kind == "extra":
        _retar(arc, bad, lambda it: it + [(tarfile.TarInfo("data/evil.json"), b"e")])
    else:
        bad.write_bytes(arc.read_bytes() + b"\0")
    assert VS.main([str(man), str(bad)]) == 1
    out = capsys.readouterr().out
    assert {"tamper": "sha256 不符：data/pool.json", "missing": "缺少：data/pool.json",
            "extra": "多出：data/evil.json", "archive_only": "archive sha256 不符"}[kind] in out


def test_verify_manifest_selfcheck(tmp_path, capsys):
    man, arc = _built(tmp_path)
    j = json.loads(man.read_text(encoding="utf-8"))
    j["content_sha256"] = "0" * 64
    man.write_text(json.dumps(j), encoding="utf-8")
    assert VS.main([str(man), str(arc)]) == 1
    assert "content_sha256 與 files 不符" in capsys.readouterr().out
    man.write_text("{}", encoding="utf-8")
    assert VS.main([str(man), str(arc)]) == 2


def test_repo_real_snapshot_verifies(tmp_path):
    """repo 內真實 data/：最新一個有 scores 的日子能產快照且校驗通過（無資料即 skip）。"""
    days = sorted(p.stem for p in (ROOT / "data" / "scores").glob("????-??-??.json"))
    if not days:
        pytest.skip("無 data/scores")
    m = BS.build(ROOT, days[-1], tmp_path / "o", commit=None, now=NOW)
    assert m["n_files"] > 0 and not any(e["path"].startswith("data/backtest/") for e in m["files"])
    assert VS.verify(m, tmp_path / "o" / m["archive"]["name"]) == []


def test_backup_workflow_structure():
    wf = yaml.safe_load((ROOT / ".github/workflows/backup.yml").read_text(encoding="utf-8"))
    on = wf.get("on") or wf.get(True)
    assert on["workflow_run"] == {"workflows": ["daily"], "types": ["completed"]} and "workflow_dispatch" in on
    assert wf["permissions"] == {"contents": "write", "issues": "write"}
    job = wf["jobs"]["backup"]
    assert "cancelled" in job["if"]
    names = [s.get("name", s.get("uses", "")) for s in job["steps"]]
    order = ["決定快照日 T", "產快照與校驗清單", "比對既有 Release（內容相同就跳過）", "建立或覆蓋上傳 Release", "重新下載並校驗", "Notify failure"]
    assert [n for n in names if n in order] == order
    steps = {s.get("name"): s for s in job["steps"]}
    assert steps["建立或覆蓋上傳 Release"]["if"] == steps["重新下載並校驗"]["if"] == "steps.cmp.outputs.skip == '0'"
    assert steps["Notify failure"]["if"] == "failure() || cancelled()"
    assert job["steps"][0]["with"]["ref"] == "main"
    txt = (ROOT / ".github/workflows/backup.yml").read_text(encoding="utf-8")
    assert "git push" not in txt and "git commit" not in txt
    assert "content_sha256" in steps["比對既有 Release（內容相同就跳過）"]["run"]
    assert "verify_snapshot.py" in steps["產快照與校驗清單"]["run"] and "verify_snapshot.py" in steps["重新下載並校驗"]["run"]
