"""P4-B3 還原演練（scripts/restore_drill.py／restore_drill_check.py／hetzner_restore_drill.sh／restore-drill.yml；docs/P4-PREVIEW.md §33）。
離線：本機 bare repo 當 GitHub、假 Release 伺服器。"""
from __future__ import annotations

import datetime as dt
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import backup_pull as BP  # noqa: E402
import build_snapshot as BS  # noqa: E402
import restore_drill as RD  # noqa: E402
import restore_drill_check as RC  # noqa: E402

pytestmark = pytest.mark.skipif(not shutil.which("git"), reason="無 git")
NOW = dt.datetime(2026, 11, 19, 16, 0, tzinfo=dt.timezone.utc)


def git(*a, cwd=None):
    return subprocess.run(["git", *a], cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()


ID = ["-c", "user.name=t", "-c", "user.email=t@t"]


class World:
    """gh.git＝假 GitHub；repo＝Hetzner 上的工作樹（origin→gh.git）；Release 由 self.get 提供。"""
    def __init__(self, tmp: Path):
        self.tmp, self.blobs, self.rels = tmp, {}, []
        self.gh = tmp / "gh.git"
        git("init", "-q", "--bare", "-b", "main", str(self.gh))
        self.repo = tmp / "repo"
        git("clone", "-q", str(self.gh), str(self.repo))
        git("checkout", "-q", "-b", "main", cwd=self.repo)

    def day(self, T: str, n: int):
        r = self.repo
        for k, v in {"data/state/cross.json": json.dumps({"n": n}), f"data/scores/{T}.json": json.dumps({"data_version": f"dv{n}"}),
                     "data/web/latest.json": json.dumps({"date": T}), "data/pool.json": "{}"}.items():
            (r / k).parent.mkdir(parents=True, exist_ok=True)
            (r / k).write_text(v, encoding="utf-8")
        git("add", "-A", cwd=r)
        git(*ID, "commit", "-q", "-m", f"daily {T}", cwd=r)
        git("push", "-q", "origin", "main", cwd=r)
        out = self.tmp / f"out-{T}"
        BS.build(r, T, out, now=NOW)
        tag = "snap-" + T.replace("-", "")
        assets = []
        for f in out.iterdir():
            u = f"https://dl/{tag}/{f.name}"
            self.blobs[u] = f.read_bytes()
            assets.append({"name": f.name, "browser_download_url": u})
        self.rels.append({"tag_name": tag, "assets": assets})

    def get(self, url, timeout=60):
        if url.startswith("https://api.github.com/"):
            return json.dumps(self.rels).encode()
        return self.blobs[url]


def setup_world(tmp_path):
    w = World(tmp_path)
    w.day("2026-11-18", 1)
    w.day("2026-11-19", 2)
    dest = tmp_path / "dest"
    assert BP.git_mirror(dest, "o/r", url=str(w.gh), now=NOW) == []
    r = BP.pull(dest, "o/r", 180, "2026-11-19", get=w.get, git=False)
    assert r["errors"] == [] and r["downloaded"] == ["2026-11-18", "2026-11-19"]
    return w, dest


def test_drill_full_pass_and_github_check(tmp_path):
    w, dest = setup_world(tmp_path)
    head_before = git("rev-parse", "--abbrev-ref", "HEAD", cwd=w.repo)
    before = sorted(p.relative_to(dest).as_posix() for p in dest.rglob("*") if "git.git" not in p.parts)
    rep = RD.drill(dest, w.repo, None, get=w.get, now=NOW)
    assert rep["errors"] == [], rep
    names = " ".join(rep["steps"])
    for k in ("D1 從 Release 重建", "D2 清單比對", "D3 git 誤刪還原", "D3 鏡像還原", "D4 前置"):
        assert k in names, k
    br = rep["branch"]
    assert br == "hetzner/drill-20261119-160000"
    # GitHub（gh.git）上有演練分支與 -mirror 分支；main 未動
    refs = git("for-each-ref", "--format=%(refname:short)", cwd=w.gh).split()
    assert br in refs and f"{br}-mirror" in refs
    assert git("rev-parse", f"{br}-mirror", cwd=w.gh) == git("rev-parse", "main", cwd=w.gh)
    # repo 工作樹分支不變、本機暫用分支已清
    assert git("rev-parse", "--abbrev-ref", "HEAD", cwd=w.repo) == head_before
    assert br not in git("branch", "--format=%(refname:short)", cwd=w.repo).split()
    # dest 既有內容不變（只多 refs 以外無新增）
    after = sorted(p.relative_to(dest).as_posix() for p in dest.rglob("*") if "git.git" not in p.parts)
    assert after == before
    # GitHub 端獨立核對（restore-drill.yml 那一步）
    co = tmp_path / "co"
    git("clone", "-q", "-b", br, str(w.gh), str(co))
    T, errs = RC.check(co)
    assert T == "2026-11-19" and errs == []
    assert (co / "drill" / "snap-2026-11-19.tar.gz").read_bytes() == (dest / "snap/2026-11-19/snap-2026-11-19.tar.gz").read_bytes()
    assert RC.main([str(co)]) == 0
    # 竄改還原結果 → 核對失敗
    (co / "data/state/cross.json").write_text("{}", encoding="utf-8")
    T, errs = RC.check(co)
    assert any("data/state/cross.json 還原後 sha 不符" in e for e in errs)


def test_drill_detects_manifest_mismatch_and_bad_local_snapshot(tmp_path):
    w, dest = setup_world(tmp_path)
    m = dest / "manifests" / "manifest-2026-11-18.json"
    m.write_bytes(m.read_bytes() + b" ")                       # Hetzner 的清單與 Release 不同
    rep = RD.drill(dest, w.repo, "2026-11-19", get=w.get, now=NOW)
    assert any("D2 清單比對" in e and "內容不同 2026-11-18" in e for e in rep["errors"])
    arc = dest / "snap/2026-11-19/snap-2026-11-19.tar.gz"
    arc.write_bytes(arc.read_bytes() + b"\0")
    rep = RD.drill(dest, w.repo, "2026-11-19", get=w.get, now=NOW)
    assert rep["steps"] == [] and any("本機快照 2026-11-19 校驗不過" in e for e in rep["errors"])
    rep = RD.drill(tmp_path / "empty", w.repo, None, get=w.get, now=NOW)
    assert any("沒有可演練的快照" in e for e in rep["errors"])


def test_drill_d1_detects_release_differs(tmp_path):
    w, dest = setup_world(tmp_path)
    u = next(k for k in w.blobs if k.endswith("snap-2026-11-19.tar.gz"))
    w.blobs[u] = w.blobs[u][:-1] + bytes([w.blobs[u][-1] ^ 1])  # Release 上的 tar.gz 損毀
    rep = RD.drill(dest, w.repo, "2026-11-19", get=w.get, now=NOW)
    assert any(e.startswith("✗ D1 從 Release 重建") for e in rep["errors"])


def test_compare_and_tar_members(tmp_path):
    d = tmp_path / "m"; d.mkdir()
    (d / "manifest-2026-11-18.json").write_bytes(b"a")
    (d / "manifest-2026-11-19.json").write_bytes(b"b")
    (d / "manifest-2026-11-18.abcdef012345.json").write_bytes(b"old")   # 改名留存的舊版不比
    assert RD.compare_manifests(d, {"2026-11-18": b"a", "2026-11-19": b"b"}) == []
    assert RD.compare_manifests(d, {"2026-11-18": b"x", "2026-11-20": b"c"}) == \
        ["內容不同 2026-11-18", "Release 無 2026-11-19（本機有）", "本機缺 2026-11-20"]
    w, dest = setup_world(tmp_path / "w")
    man = json.loads((dest / "snap/2026-11-19/manifest-2026-11-19.json").read_text())
    arc = dest / "snap/2026-11-19/snap-2026-11-19.tar.gz"
    got = RD.tar_members(arc, man, ["data/pool.json"])
    assert got == {"data/pool.json": b"{}"}
    with pytest.raises(RuntimeError, match="清單無"):
        RD.tar_members(arc, man, ["data/nope.json"])
    man["files"][[e["path"] for e in man["files"]].index("data/pool.json")]["sha256"] = "0" * 64
    with pytest.raises(RuntimeError, match="sha256 不符"):
        RD.tar_members(arc, man, ["data/pool.json"])


@pytest.mark.skipif(not shutil.which("bash"), reason="無 bash")
def test_hetzner_restore_drill_wrapper(tmp_path):
    """一行入口：取 main 上的四支腳本執行；main 上缺檔時 return 1 並提示 PR 未合併。"""
    import os
    origin = tmp_path / "origin.git"
    git("init", "-q", "--bare", "-b", "main", str(origin))
    work = tmp_path / "work"
    git("clone", "-q", str(origin), str(work))
    (work / "scripts").mkdir(parents=True)
    (work / "scripts/restore_drill.py").write_text("import sys; print('DRILL', ' '.join(sys.argv[1:])); sys.exit(3)\n")
    for f in ("backup_pull.py", "verify_snapshot.py", "build_snapshot.py"):
        (work / "scripts" / f).write_text("")
    shutil.copy(ROOT / "scripts/hetzner_restore_drill.sh", work / "scripts/hetzner_restore_drill.sh")
    git("add", ".", cwd=work)
    git(*ID, "commit", "-q", "-m", "s", cwd=work)
    git("push", "-q", "origin", "HEAD:main", cwd=work)
    git("checkout", "-q", "-b", "other", cwd=work)
    env = dict(os.environ, HETZNER_BACKUP_REPO=str(work), BACKUP_DEST=str(tmp_path / "dest"))
    cmd = f'git -C "{work}" fetch -q origin main && git -C "{work}" show origin/main:scripts/hetzner_restore_drill.sh | bash'
    r = subprocess.run(["bash", "-c", cmd], env=env, capture_output=True, text=True)
    assert r.returncode == 3 and f"DRILL --dest {tmp_path / 'dest'} --repo-dir {work}" in r.stdout and "完成 rc=3" in r.stdout
    assert git("rev-parse", "--abbrev-ref", "HEAD", cwd=work) == "other"
    git("checkout", "-q", "main", cwd=work)
    git("rm", "-q", "scripts/build_snapshot.py", cwd=work)
    git(*ID, "commit", "-q", "-m", "rm", cwd=work)
    git("push", "-q", "origin", "HEAD:main", cwd=work)
    r = subprocess.run(["bash", "-c", cmd], env=env, capture_output=True, text=True)
    assert r.returncode == 1 and "PR 還沒合併" in r.stdout


def test_restore_drill_workflow_structure():
    wf = yaml.safe_load((ROOT / ".github/workflows/restore-drill.yml").read_text(encoding="utf-8"))
    on = wf.get("on") or wf.get(True)
    assert list(on) == ["workflow_dispatch"] and on["workflow_dispatch"]["inputs"]["branch"]["required"] is True
    assert wf["permissions"] == {"contents": "write", "issues": "write"}
    steps = wf["jobs"]["drill"]["steps"]
    names = [s.get("name", s.get("uses", "")) for s in steps]
    order = ["檢查分支名稱", "核對 git 還原", "以 Hetzner 的位元組建立演練 Release、重下載校驗、與正式版比對", "刪除演練 Release 與 tag", "Notify failure"]
    assert [n for n in names if n in order] == order
    by = {s.get("name"): s for s in steps}
    assert "^hetzner/drill-[0-9]{8}-[0-9]{6}$" in by["檢查分支名稱"]["run"]
    assert by["刪除演練 Release 與 tag"]["if"] == "always()" and "--cleanup-tag" in by["刪除演練 Release 與 tag"]["run"]
    rel = by["以 Hetzner 的位元組建立演練 Release、重下載校驗、與正式版比對"]["run"]
    assert "drill-snap-" in rel and "--prerelease" in rel and "verify_snapshot.py" in rel and rel.count("cmp ") == 2
    txt = (ROOT / ".github/workflows/restore-drill.yml").read_text(encoding="utf-8")
    assert "git push" not in txt and 'gh release delete "$TAG"' in txt and "snap-*" not in txt.replace("不動任何 snap-* Release", "")
