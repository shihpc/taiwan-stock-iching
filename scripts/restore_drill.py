#!/usr/bin/env python3
"""P4-B3 還原演練（Hetzner 執行；docs/P4-PREVIEW.md §33；v1.2.2 §12.2「還原能力要求」1–4）。

**不刪改任何正式資料**：main 與 `snap-*` Release 不動；只寫暫存目錄、只推分支 `hetzner/drill-<日期>`（及 `…-mirror`）。

  D1 從 Release 重建 Hetzner 副本：backup_pull 把 T 拉到暫存 dest，tar.gz 與清單和 <dest>/snap/<T>/ 逐位元組相同。
  D2 清單比對：<dest>/manifests 與 Release 上的清單逐檔相同；暫存複本竄改一份、刪除一份 → 兩者都要被偵測到。
  D3 GitHub 端 git 誤刪 → 由 Hetzner 還原：以 T 快照清單的 commit 為基底開分支，先推一個刪除
     `data/state/cross.json`、`data/scores/<T>.json`、`data/web/latest.json` 的 commit（模擬誤刪），再從 Hetzner 快照 tar.gz
     取出這三檔、對清單 sha256 校驗後還原並推送；從 GitHub 取回分支末端核對 sha。另把 Git 鏡像的 main 推到 `…-mirror` 分支，
     GitHub 端回報的 sha 須與鏡像相同（從鏡像還原 git）。
  D4 前置：把 Hetzner 的 snap tar.gz＋清單放進演練分支 `drill/`，供 GitHub 端 restore-drill.yml 以這些位元組重建 Release。

推送一律經 repo 工作樹的 origin（沿用其憑證）：暫存 clone 先推到 repo 的本機分支，再由 repo 推上 GitHub；repo 目前所在分支與工作樹不動。
用法：python3 scripts/restore_drill.py --dest /root/backup/iching --repo-dir /root/projects/taiwan-stock-iching [--date T]
exit：0 全部通過；1 有任一步失敗。
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import backup_pull as BP  # noqa: E402
from verify_snapshot import verify  # noqa: E402

DRILL_PATHS = ("data/state/cross.json", "data/scores/{T}.json", "data/web/latest.json")
TPE = dt.timezone(dt.timedelta(hours=8))


def sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def git(*a, cwd=None, check=True, run=subprocess.run) -> str:
    r = run(["git", *a], cwd=cwd, check=check, capture_output=True, text=True, timeout=1800)
    return (r.stdout or "").strip()


def compare_manifests(local_dir: Path, remote: dict[str, bytes]) -> list[str]:
    """remote＝{T: 清單位元組}。回差異：本機缺、本機多、內容不同（只比 manifest-<T>.json，不含改名留存的舊版）。"""
    loc = {}
    for p in local_dir.iterdir():
        m = BP.MAN_RE.match(p.name)
        if m:
            loc[m.group(1)] = p.read_bytes()
    out = []
    for T in sorted(set(loc) | set(remote)):
        if T not in loc:
            out.append(f"本機缺 {T}")
        elif T not in remote:
            out.append(f"Release 無 {T}（本機有）")
        elif sha(loc[T]) != sha(remote[T]):
            out.append(f"內容不同 {T}")
    return out


def tar_members(arc: Path, man: dict, paths: list[str]) -> dict[str, bytes]:
    """從快照 tar.gz 取出指定檔，逐檔對清單 sha256 校驗；任何不符即 raise。"""
    want = {e["path"]: e for e in man["files"]}
    out = {}
    with tarfile.open(arc, "r:gz") as tar:
        for p in paths:
            if p not in want:
                raise RuntimeError(f"清單無 {p}")
            m = tar.getmember(p)
            if not m.isfile():
                raise RuntimeError(f"成員不合法 {p}")
            b = tar.extractfile(m).read()
            if sha(b) != want[p]["sha256"]:
                raise RuntimeError(f"sha256 不符 {p}")
            out[p] = b
    return out


def drill(dest: Path, repo_dir: Path, T: str | None, repo: str = "shihpc/taiwan-stock-iching", get=BP.http_get,
          now: dt.datetime | None = None, remote: str = "origin") -> dict:
    rep = {"steps": [], "errors": [], "branch": None}

    def ok(name, msg):
        rep["steps"].append(f"✓ {name}：{msg}")

    def bad(name, msg):
        rep["errors"].append(f"✗ {name}：{msg}")

    snapdir = dest / "snap"
    days = sorted(p.name for p in snapdir.iterdir() if re.fullmatch(r"\d{4}-\d{2}-\d{2}", p.name)) if snapdir.is_dir() else []
    T = T or (days[-1] if days else None)
    if not T or T not in days:
        bad("前置", f"{dest}/snap 沒有可演練的快照（T={T}）")
        return rep
    man_path, arc_path = snapdir / T / f"manifest-{T}.json", snapdir / T / f"snap-{T}.tar.gz"
    man = json.loads(man_path.read_text(encoding="utf-8"))
    if verify(man, arc_path):
        bad("前置", f"本機快照 {T} 校驗不過，先跑 hetzner_backup.sh")
        return rep
    now = now or dt.datetime.now(dt.timezone.utc)
    with tempfile.TemporaryDirectory(prefix="iching-drill-") as td:
        td = Path(td)
        # ---- D1 從 Release 重建 ----
        try:
            r = BP.pull(td / "rebuild", repo, 3650, dt.datetime.now(TPE).date().isoformat(), get=get, git=False)
            got = td / "rebuild" / "snap" / T
            same = (got / arc_path.name).read_bytes() == arc_path.read_bytes() and (got / man_path.name).read_bytes() == man_path.read_bytes()
            if r["errors"] or not same:
                bad("D1 從 Release 重建", f"errors={r['errors'][:3]} 逐位元組相同={same}")
            else:
                ok("D1 從 Release 重建", f"拉回 {len(r['downloaded'])} 份，{T} 的 tar.gz 與清單和 Hetzner 現有副本逐位元組相同")
        except Exception as e:  # noqa: BLE001
            bad("D1 從 Release 重建", f"{type(e).__name__}: {e}")
        # ---- D2 清單比對與竄改偵測 ----
        try:
            rel_mans = {}
            for x in BP.list_releases(repo, get=get):
                u = x["assets"].get(f"manifest-{x['date']}.json")
                if u:
                    rel_mans[x["date"]] = get(u)
            diff = compare_manifests(dest / "manifests", rel_mans)
            cp = td / "mcopy"
            shutil.copytree(dest / "manifests", cp)
            names = sorted(p for p in cp.iterdir() if BP.MAN_RE.match(p.name))
            if diff:
                bad("D2 清單比對", f"Hetzner 與 Release 有差異：{diff[:5]}")
            elif len(names) < 2:
                bad("D2 清單比對", "清單不足兩份，無法同時演練竄改與刪除")
            else:
                names[0].write_bytes(names[0].read_bytes().replace(b'"sha256": "', b'"sha256": "0', 1))
                names[1].unlink()
                d2 = compare_manifests(cp, rel_mans)
                t0, t1 = BP.MAN_RE.match(names[0].name).group(1), BP.MAN_RE.match(names[1].name).group(1)
                if f"內容不同 {t0}" in d2 and f"本機缺 {t1}" in d2 and len(d2) == 2:
                    ok("D2 清單比對", f"Hetzner 與 Release {len(rel_mans)} 份清單逐檔相同；暫存複本竄改 {t0}、刪除 {t1} 皆被偵測")
                else:
                    bad("D2 清單比對", f"竄改／刪除偵測結果不符：{d2}")
        except Exception as e:  # noqa: BLE001
            bad("D2 清單比對", f"{type(e).__name__}: {e}")
        # ---- D3／D4 GitHub 端 git 誤刪 → 由 Hetzner 還原 ----
        branch = f"hetzner/drill-{now.strftime('%Y%m%d-%H%M%S')}"
        rep["branch"] = branch
        try:
            base = man.get("commit")
            mirror = dest / "git.git"
            if not base or not mirror.is_dir():
                raise RuntimeError(f"缺基底 commit 或 Git 鏡像（commit={base}）")
            git("cat-file", "-e", f"{base}^{{commit}}", cwd=mirror)
            wt = td / "wt"
            git("clone", "-q", "--no-checkout", str(mirror), str(wt))
            git("checkout", "-q", "-b", "drill", base, cwd=wt)
            paths = [p.format(T=T) for p in DRILL_PATHS]
            for p in paths:
                (wt / p).unlink()
            ident = ["-c", "user.name=iching-restore-drill", "-c", "user.email=iching-drill@users.noreply.github.com"]
            git(*ident, "commit", "-q", "-am", f"drill: 模擬 GitHub 端誤刪 {', '.join(paths)}", cwd=wt)
            deleted = git("rev-parse", "HEAD", cwd=wt)
            files = tar_members(arc_path, man, paths)   # 從 Hetzner 快照取回並校驗
            for p, b in files.items():
                (wt / p).parent.mkdir(parents=True, exist_ok=True)
                (wt / p).write_bytes(b)
            (wt / "drill").mkdir()
            shutil.copy(arc_path, wt / "drill" / arc_path.name)
            shutil.copy(man_path, wt / "drill" / man_path.name)
            info = {"date": T, "base": base, "deleted_commit": deleted, "paths": paths, "mirror_main": git("rev-parse", "main", cwd=mirror)}
            (wt / "drill" / "drill.json").write_text(json.dumps(info, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
            git("add", "-A", cwd=wt)
            git(*ident, "commit", "-q", "-m", f"drill: 由 Hetzner 快照還原 {T}（並附 drill/ 供 Release 還原演練）", cwd=wt)
            restored = git("rev-parse", "HEAD", cwd=wt)
            # 經 repo 工作樹的 origin 推送（沿用其憑證）；repo 目前分支與工作樹不動
            git("push", "-q", str(repo_dir), f"{restored}:refs/heads/{branch}", cwd=wt)
            git("fetch", "-q", str(mirror), f"main:refs/heads/{branch}-mirror", cwd=repo_dir)
            git("push", "-q", remote, f"refs/heads/{branch}", f"refs/heads/{branch}-mirror", cwd=repo_dir)
            # 從 GitHub 取回核對
            ls = dict(reversed(x.split("\t")) for x in git("ls-remote", remote, f"refs/heads/{branch}", f"refs/heads/{branch}-mirror",
                                                          cwd=repo_dir).splitlines() if "\t" in x)
            if ls.get(f"refs/heads/{branch}") != restored or ls.get(f"refs/heads/{branch}-mirror") != info["mirror_main"]:
                raise RuntimeError(f"GitHub 端分支 sha 不符：{ls}")
            git("fetch", "-q", remote, f"refs/heads/{branch}", cwd=repo_dir)
            want = {e["path"]: e["sha256"] for e in man["files"]}
            badp = [p for p in paths if sha(subprocess.run(["git", "show", f"FETCH_HEAD:{p}"], cwd=repo_dir, check=True,
                                                            capture_output=True).stdout) != want[p]]
            if badp:
                raise RuntimeError(f"GitHub 端還原後 sha 不符：{badp}")
            parent = git("rev-parse", f"{restored}^", cwd=repo_dir)
            if parent != deleted:
                raise RuntimeError("分支歷史不是「誤刪 → 還原」兩個 commit")
            ok("D3 git 誤刪還原", f"分支 {branch}：誤刪 {deleted[:7]} → 由 Hetzner 快照還原 {restored[:7]}，GitHub 端三檔 sha 與清單相同")
            ok("D3 鏡像還原", f"鏡像 main {info['mirror_main'][:7]} 推到 {branch}-mirror，GitHub 端 sha 相同")
            ok("D4 前置", f"drill/ 已含 Hetzner 的 snap-{T}.tar.gz 與清單；請 Claude dispatch restore-drill.yml（branch={branch}）")
        except Exception as e:  # noqa: BLE001
            msg = getattr(e, "stderr", None) or str(e)
            bad("D3／D4 前置", f"{type(e).__name__}: {str(msg).strip()[:400]}")
        finally:
            for b in (branch, f"{branch}-mirror"):   # 只清 repo 的本機暫用分支；GitHub 上的演練分支保留作紀錄
                subprocess.run(["git", "branch", "-q", "-D", b], cwd=repo_dir, capture_output=True)
    return rep


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--dest", default="/root/backup/iching")
    ap.add_argument("--repo-dir", default="/root/projects/taiwan-stock-iching")
    ap.add_argument("--date", default=None)
    a = ap.parse_args(argv)
    rep = drill(Path(a.dest), Path(a.repo_dir), a.date)
    print(f"== restore_drill {dt.datetime.now(TPE).isoformat(timespec='seconds')} dest={a.dest}")
    for s in rep["steps"] + rep["errors"]:
        print(s)
    print("演練分支：" + (rep["branch"] or "—"))
    print("結果：" + ("失敗" if rep["errors"] else "全部通過"))
    return 1 if rep["errors"] else 0


if __name__ == "__main__":
    sys.exit(main())
