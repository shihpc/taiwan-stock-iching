"""P4-B2 Hetzner 備份拉取＋本機第三份（scripts/backup_pull.py／hetzner_backup.sh／local_manifests.ps1；docs/P4-PREVIEW.md §32）。離線。"""
from __future__ import annotations

import datetime as dt
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import backup_pull as BP  # noqa: E402
import build_snapshot as BS  # noqa: E402

NOW = dt.datetime(2026, 11, 18, 23, 0, tzinfo=BS.TPE)


def mkdata(root: Path, T: str, extra: str = ""):
    for k, v in {"data/state/cross.json": "{}" + extra, f"data/scores/{T}.json": json.dumps({"data_version": "dv"}),
                 "data/web/latest.json": json.dumps({"date": T})}.items():
        p = root / k
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(v, encoding="utf-8")


class Server:
    """假 GitHub：releases API＋資產下載。"""
    def __init__(self, tmp: Path):
        self.tmp, self.rels, self.blobs, self.calls = tmp, [], {}, []

    def publish(self, T: str, extra: str = "", tag=None):
        src = self.tmp / f"src-{T}-{len(self.blobs)}"
        mkdata(src, T, extra)
        out = src / "out"
        BS.build(src, T, out, commit="a" * 40, now=NOW)
        tag = tag or "snap-" + T.replace("-", "")
        assets = []
        for f in out.iterdir():
            url = f"https://dl/{tag}/{f.name}"
            self.blobs[url] = f.read_bytes()
            assets.append({"name": f.name, "browser_download_url": url})
        self.rels = [r for r in self.rels if r["tag_name"] != tag] + [{"tag_name": tag, "assets": assets}]
        return out

    def get(self, url, timeout=60):
        self.calls.append(url)
        if url.startswith("https://api.github.com/"):
            return json.dumps(self.rels).encode()
        if url not in self.blobs:
            raise RuntimeError("404 " + url)
        return self.blobs[url]


def run_pull(dest, srv, today="2026-11-19", keep=180):
    return BP.pull(dest, "o/r", keep, today, get=srv.get, git=False)


def test_pull_download_skip_and_manifests(tmp_path):
    srv = Server(tmp_path)
    srv.publish("2026-11-17"); srv.publish("2026-11-18")
    srv.rels.append({"tag_name": "v1.0", "assets": []})            # 非快照 tag 不理
    dest = tmp_path / "dest"
    r1 = run_pull(dest, srv)
    assert r1["errors"] == [] and r1["downloaded"] == ["2026-11-17", "2026-11-18"] and r1["local_ok"] == 2
    for T in ("2026-11-17", "2026-11-18"):
        assert (dest / "snap" / T / f"snap-{T}.tar.gz").is_file()
        assert (dest / "manifests" / f"manifest-{T}.json").read_bytes() == (dest / "snap" / T / f"manifest-{T}.json").read_bytes()
    n = len(srv.calls)
    r2 = run_pull(dest, srv)
    assert r2["downloaded"] == [] and r2["skipped"] == ["2026-11-17", "2026-11-18"] and r2["errors"] == []
    assert not any(u.endswith(".tar.gz") for u in srv.calls[n:])   # 未變不重下 tar.gz
    assert BP.main(["--dest", str(dest), "--today", "bad"]) == 2


def test_content_change_replaces_and_keeps_old_manifest(tmp_path):
    srv = Server(tmp_path)
    srv.publish("2026-11-10"); srv.publish("2026-11-18")
    dest = tmp_path / "dest"
    run_pull(dest, srv)
    old = json.loads((dest / "manifests" / "manifest-2026-11-10.json").read_text())
    srv.publish("2026-11-10", extra=" "); srv.publish("2026-11-18", extra=" ")
    r = run_pull(dest, srv)
    assert sorted(r["downloaded"]) == ["2026-11-10", "2026-11-18"] and len(r["errors"]) == 1
    # 11-10 變動時本機已有 11-18 的清單 → 異常（算失敗）；11-18 是最新日 → 更新
    assert any(e.startswith("異常：2026-11-10") for e in r["errors"]) and any(n.startswith("更新：2026-11-18") for n in r["notes"])
    assert not any("2026-11-18" in e for e in r["errors"])
    assert (dest / "manifests" / f"manifest-2026-11-10.{old['content_sha256'][:12]}.json").is_file()
    assert (dest / "snap" / ".replaced" / f"2026-11-10.{old['content_sha256'][:12]}").is_dir()
    new = json.loads((dest / "snap/2026-11-10/manifest-2026-11-10.json").read_text())
    assert new["content_sha256"] != old["content_sha256"]


def test_bad_archive_not_swapped(tmp_path):
    srv = Server(tmp_path)
    srv.publish("2026-11-18")
    dest = tmp_path / "dest"
    run_pull(dest, srv)
    good = (dest / "snap/2026-11-18/snap-2026-11-18.tar.gz").read_bytes()
    srv.publish("2026-11-18", extra="x")
    url = next(u for u in srv.blobs if u.endswith("snap-2026-11-18.tar.gz"))
    srv.blobs[url] = srv.blobs[url][:-5] + b"XXXXX"                  # Release 上的 tar.gz 損毀
    r = run_pull(dest, srv)
    assert any("2026-11-18 校驗失敗" in e for e in r["errors"]) and r["downloaded"] == []
    assert (dest / "snap/2026-11-18/snap-2026-11-18.tar.gz").read_bytes() == good   # 原版保留
    assert not any(p.name.startswith(".tmp-") for p in (dest / "snap").iterdir())
    assert BP.main.__code__ is not None


def test_local_corruption_retention_and_missing_assets(tmp_path):
    srv = Server(tmp_path)
    srv.publish("2026-05-01"); srv.publish("2026-11-18")
    dest = tmp_path / "dest"
    r = run_pull(dest, srv, today="2026-11-19", keep=180)
    assert r["downloaded"] == ["2026-11-18"]                          # 05-01 在保留窗外，不下載
    (dest / "snap/2026-05-02").mkdir()                                 # 窗外本機舊快照 → 刪
    arc = dest / "snap/2026-11-18/snap-2026-11-18.tar.gz"
    arc.write_bytes(arc.read_bytes() + b"\0")                          # 本機靜默損毀，且 Release 端已不在（無從自癒）
    srv.rels = [x for x in srv.rels if x["tag_name"] != "snap-20261118"]
    srv.rels.append({"tag_name": "snap-20261119", "assets": []})
    r = run_pull(dest, srv, today="2026-11-19")
    assert "2026-05-02" in r["removed"] and not (dest / "snap/2026-05-02").exists()
    assert any("本機快照損毀或不完整：2026-11-18" in e for e in r["errors"])
    assert any("snap-20261119 缺資產" in e for e in r["errors"])
    assert (dest / "manifests/manifest-2026-11-18.json").is_file()


def git(*a, cwd=None):
    return subprocess.run(["git", *a], cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()


@pytest.mark.skipif(not shutil.which("git"), reason="無 git")
def test_git_mirror_no_prune_and_reflog(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    git("init", "-q", "-b", "main", cwd=src)
    git("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "--allow-empty", "-m", "a", cwd=src)
    git("branch", "keep", cwd=src)
    git("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "--allow-empty", "-m", "b", cwd=src)
    old_main = git("rev-parse", "main", cwd=src)
    dest = tmp_path / "dest"
    assert BP.git_mirror(dest, "o/r", url=str(src), now=NOW) == []
    m = dest / "git.git"
    assert git("rev-parse", "main", cwd=m) == old_main and git("rev-parse", "keep", cwd=m)
    git("branch", "-D", "keep", cwd=src)                               # GitHub 端誤刪分支
    git("reset", "-q", "--hard", "HEAD~1", cwd=src)                    # 歷史被改寫
    git("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "--allow-empty", "-m", "c", cwd=src)
    assert BP.git_mirror(dest, "o/r", url=str(src), now=NOW + dt.timedelta(days=1)) == []
    assert git("rev-parse", "keep", cwd=m)                             # 不 prune：鏡像仍有
    assert git("rev-parse", "main", cwd=m) == git("rev-parse", "main", cwd=src)
    refs = sorted((dest / "refs").glob("refs-*.txt"))
    assert f"{old_main} refs/heads/main" in refs[0].read_text()            # 舊 tip 的 sha 留在第一份 refs 清單
    git("cat-file", "-e", old_main, cwd=m)                                 # 舊 commit 物件仍在鏡像
    git("gc", "-q", cwd=m)
    git("cat-file", "-e", old_main, cwd=m)                                 # gc 後仍在（pruneExpire never）
    assert git("config", "gc.reflogExpire", cwd=m) == "never" and git("config", "gc.pruneExpire", cwd=m) == "never"
    assert len(list((dest / "refs").glob("refs-*.txt"))) == 2
    assert BP.git_mirror(tmp_path / "d2", "o/r", url=str(tmp_path / "nope")) != []


# ---- hetzner_backup.sh：腳本來源（GitHub main → 本機鏡像 → 皆無）----
STUB = "import sys; print('STUB-{tag}', ' '.join(sys.argv[1:]))\n"


def _repo_with_stub(path: Path, tag: str):
    path.mkdir(parents=True)
    git("init", "-q", "-b", "main", cwd=path)
    (path / "scripts").mkdir()
    (path / "scripts/backup_pull.py").write_text(STUB.format(tag=tag))
    for f in ("verify_snapshot.py", "build_snapshot.py"):
        (path / "scripts" / f).write_text("")
    git("add", ".", cwd=path)
    git("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "s", cwd=path)


@pytest.mark.skipif(not (shutil.which("git") and shutil.which("bash")), reason="無 git／bash")
@pytest.mark.parametrize("case", ["origin", "mirror", "none"])
def test_hetzner_backup_script_source(tmp_path, case):
    origin = tmp_path / "origin"
    _repo_with_stub(origin, "ORIGIN")
    work = tmp_path / "work"
    git("clone", "-q", str(origin), str(work))
    dest = tmp_path / "dest"
    if case != "origin":
        git("-C", str(work), "remote", "set-url", "origin", str(tmp_path / "gone"))
        git("-C", str(work), "update-ref", "-d", "refs/remotes/origin/main")
    if case == "mirror":
        mir = tmp_path / "mirsrc"
        _repo_with_stub(mir, "MIRROR")
        git("clone", "-q", "--mirror", str(mir), str(dest / "git.git"))
    env = dict(os.environ, HETZNER_BACKUP_REPO=str(work), BACKUP_DEST=str(dest))
    r = subprocess.run(["bash", str(ROOT / "scripts/hetzner_backup.sh"), "--no-git"], env=env, capture_output=True, text=True)
    log = next((dest / "logs").glob("backup-*.log")).read_text()
    if case == "none":
        assert r.returncode == 1 and "取不到備份腳本" in log
    else:
        assert r.returncode == 0, log
        assert f"STUB-{case.upper()} --dest {dest} --no-git" in log
        assert ("腳本來源：GitHub main" in log) == (case == "origin") and ("改取本機鏡像" in log) == (case == "mirror")


def test_local_manifests_ps1_static():
    """PowerShell 本沙箱無法實跑（需使用者本機首跑）：只做靜態守門。"""
    raw = (ROOT / "scripts/local_manifests.ps1").read_bytes()
    assert raw[:3] == b"\xef\xbb\xbf"                                   # UTF-8 BOM（PS 5.1 讀中文）
    s = raw[3:].decode("utf-8")
    assert not {c for c in s if ord(c) > 127 and not c.encode("cp950", "ignore")}   # 主控台 cp950 可顯示
    for k in ("snap-(\\d{4})(\\d{2})(\\d{2})", "-OutFile", "Tls12", "exit 2", "exit 1", "exit 0", "異常", "-UseBasicParsing",
              "-UserAgent $ua", "Sort-Object", "$existing", "content_sha256", "schtasks /Create"):
        assert k in s, k
    assert "| ConvertFrom-Json" not in s and not __import__("re").search(r"-Headers\s+[@$]", s)
    assert "Authorization" not in s and "token" not in s.lower().replace("免 token", "")
    assert s.count("{") == s.count("}") and s.count("(") == s.count(")")


@pytest.mark.skipif(not (shutil.which("git") and shutil.which("bash")), reason="無 git／bash")
@pytest.mark.parametrize("tz,expect", [("UTC", "30 10 * * *"), ("Asia/Taipei", "30 18 * * *"), ("America/New_York", "30 5 * * *")])
def test_install_script(tmp_path, tz, expect):
    """一次性安裝：複製 wrapper 到 <dest>/bin、先跑一次、依伺服器時區換算台北 18:30 加入 crontab、重跑不重複加。"""
    origin = tmp_path / "origin"
    _repo_with_stub(origin, "ORIGIN")
    for f in ("hetzner_backup.sh", "hetzner_backup_install.sh"):
        shutil.copy(ROOT / "scripts" / f, origin / "scripts" / f)
    git("add", ".", cwd=origin)
    git("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "w", cwd=origin)
    work = tmp_path / "work"
    git("clone", "-q", str(origin), str(work))
    git("-C", str(work), "checkout", "-q", "-b", "other")                 # 工作樹不在 main 也要能裝
    dest = tmp_path / "dest"
    bin_ = tmp_path / "bin"; bin_.mkdir()
    tab = tmp_path / "crontab.txt"
    (bin_ / "crontab").write_text(f'#!/bin/bash\nif [ "$1" = "-l" ]; then [ -f "{tab}" ] && cat "{tab}" || exit 1; else cat > "{tab}"; fi\n')
    (bin_ / "crontab").chmod(0o755)
    if tz == "America/New_York":   # 2026-11 為 EST（-0500）：台北 18:30 ＝ 前一日 05:30
        pass
    env = dict(os.environ, PATH=f"{bin_}:{os.environ['PATH']}", HETZNER_BACKUP_REPO=str(work), BACKUP_DEST=str(dest), TZ=tz)
    cmd = f'git -C "{work}" fetch -q origin main && git -C "{work}" show origin/main:scripts/hetzner_backup_install.sh | bash'
    r = subprocess.run(["bash", "-c", cmd], env=env, capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr
    assert (dest / "bin/hetzner_backup.sh").is_file() and "STUB-ORIGIN" in r.stdout
    lines = tab.read_text().splitlines()
    assert len(lines) == 1 and lines[0].startswith(expect + f" BACKUP_DEST={dest} HETZNER_BACKUP_REPO={work} {dest}/bin/hetzner_backup.sh")
    r2 = subprocess.run(["bash", "-c", cmd], env=env, capture_output=True, text=True)
    assert r2.returncode == 0 and "不重複加入" in r2.stdout and len(tab.read_text().splitlines()) == 1
    assert git("-C", str(work), "rev-parse", "--abbrev-ref", "HEAD") == "other"


def test_holiday_update_not_anomaly_and_self_heal(tmp_path):
    """假日試算班讓舊 T 在數天後合法更新：本機沒有更新日期的清單＝「更新」不算失敗；本機 tar.gz 損毀但清單未變 → 自動重下。"""
    srv = Server(tmp_path)
    srv.publish("2026-11-12")
    dest = tmp_path / "dest"
    run_pull(dest, srv, today="2026-11-13")
    srv.publish("2026-11-12", extra=" ")
    r = run_pull(dest, srv, today="2026-11-16")          # 隔了週末、無更新日期的快照
    assert r["errors"] == [] and any(n.startswith("更新：2026-11-12") for n in r["notes"])
    arc = dest / "snap/2026-11-12/snap-2026-11-12.tar.gz"
    good = arc.read_bytes()
    arc.write_bytes(good[:-1] + bytes([good[-1] ^ 1]))
    r = run_pull(dest, srv, today="2026-11-16")
    assert r["downloaded"] == ["2026-11-12"] and r["errors"] == [] and any("本機快照損毀，重新下載" in n for n in r["notes"])
    assert arc.read_bytes() == good
    arc.unlink()                                          # 清單在、tar.gz 不見 → 也重下
    r = run_pull(dest, srv, today="2026-11-16")
    assert r["downloaded"] == ["2026-11-12"] and arc.read_bytes() == good


def test_window_boundary_tmp_cleanup_date_mismatch_and_pages(tmp_path):
    srv = Server(tmp_path)
    srv.publish("2026-05-23"); srv.publish("2026-05-22")
    dest = tmp_path / "dest"
    (dest / "snap/.tmp-stale").mkdir(parents=True)
    r = run_pull(dest, srv, today="2026-11-19", keep=180)   # cutoff＝2026-05-23（含）
    assert r["downloaded"] == ["2026-05-23"] and not (dest / "snap/.tmp-stale").exists()
    url = next(u for u in srv.blobs if u.endswith("manifest-2026-05-23.json"))
    j = json.loads(srv.blobs[url]); j["date"] = "2026-05-24"; srv.blobs[url] = json.dumps(j).encode()
    r = run_pull(dest, srv, today="2026-11-19")
    assert any("2026-05-23 清單下載／解析失敗" in e for e in r["errors"])
    pages = {1: [{"tag_name": f"x{i}"} for i in range(100)], 2: [{"tag_name": "snap-20261118", "assets": []}]}
    calls = []

    def get(url, timeout=60):
        calls.append(url)
        return json.dumps(pages[int(url.rsplit("page=", 1)[1])]).encode()
    assert [x["tag"] for x in BP.list_releases("o/r", get=get)] == ["snap-20261118"] and len(calls) == 2


@pytest.mark.skipif(not shutil.which("git"), reason="無 git")
def test_git_mirror_skips_pull_refs_and_reapplies_config(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    git("init", "-q", "-b", "main", cwd=src)
    git("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "--allow-empty", "-m", "a", cwd=src)
    git("update-ref", "refs/pull/1/head", "main", cwd=src)
    dest = tmp_path / "dest"
    assert BP.git_mirror(dest, "o/r", url=str(src), now=NOW) == []
    m = dest / "git.git"
    assert "refs/pull/" not in git("for-each-ref", cwd=m) and "refs/heads/main" in git("for-each-ref", cwd=m)
    git("config", "--unset", "gc.pruneExpire", cwd=m)                   # 模擬首次中途被殺、設定沒寫齊
    assert BP.git_mirror(dest, "o/r", url=str(src), now=NOW) == []
    assert git("config", "gc.pruneExpire", cwd=m) == "never"
