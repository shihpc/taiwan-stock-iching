#!/usr/bin/env python3
"""P4-B3 還原演練 GitHub 端核對（restore-drill.yml 呼叫；docs/P4-PREVIEW.md §33）。

讀演練分支 checkout 目錄裡的 drill/drill.json，獨立核對 git 還原：
  ①分支末端的父 commit＝drill.json 的 deleted_commit，且該 commit 確實不含那幾個檔（誤刪成立）；
  ②deleted_commit 的父＝base（誤刪是從基底直接刪的）；
  ③末端的那幾個檔 sha256＝drill/manifest-<T>.json 的條目＝base commit 裡的同一檔。
通過：stdout 只印 `T=<日期>`（供 $GITHUB_OUTPUT），說明走 stderr；不通過 exit 1。
用法：python scripts/restore_drill_check.py <演練分支 checkout 目錄>
"""
from __future__ import annotations

import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path


def gitb(repo: Path, *a) -> bytes:
    return subprocess.run(["git", "-C", str(repo), *a], check=True, capture_output=True).stdout


def check(repo: Path) -> tuple[str, list[str]]:
    errs = []
    info = json.loads((repo / "drill" / "drill.json").read_text(encoding="utf-8"))
    T = info.get("date", "")
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", T):
        return T, ["drill.json date 格式錯"]
    man = json.loads((repo / "drill" / f"manifest-{T}.json").read_text(encoding="utf-8"))
    want = {e["path"]: e["sha256"] for e in man["files"]}
    head = gitb(repo, "rev-parse", "HEAD").decode().strip()
    parent = gitb(repo, "rev-parse", "HEAD^").decode().strip()
    if parent != info["deleted_commit"]:
        errs.append("末端的父 commit 不是 drill.json 記載的誤刪 commit")
    if gitb(repo, "rev-parse", f"{info['deleted_commit']}^").decode().strip() != info["base"]:
        errs.append("誤刪 commit 的父不是基底 commit")
    if man.get("commit") != info["base"]:
        errs.append("快照清單的 commit 與基底不同")
    for p in info["paths"]:
        r = subprocess.run(["git", "-C", str(repo), "cat-file", "-e", f"{info['deleted_commit']}:{p}"], capture_output=True)
        if r.returncode == 0:
            errs.append(f"誤刪 commit 仍含 {p}")
        now = hashlib.sha256((repo / p).read_bytes()).hexdigest()
        base = hashlib.sha256(gitb(repo, "show", f"{info['base']}:{p}")).hexdigest()
        if not (now == want.get(p) == base):
            errs.append(f"{p} 還原後 sha 不符（末端 {now[:12]}／清單 {str(want.get(p))[:12]}／基底 {base[:12]}）")
    print(f"head {head[:7]} ← 誤刪 {info['deleted_commit'][:7]} ← 基底 {info['base'][:7]}；核對 {len(info['paths'])} 檔", file=sys.stderr)
    return T, errs


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) != 1:
        print(__doc__, file=sys.stderr)
        return 2
    try:
        T, errs = check(Path(argv[0]))
    except Exception as e:  # noqa: BLE001
        print(f"!! {type(e).__name__}: {e}", file=sys.stderr)
        return 1
    for e in errs:
        print("✗ " + e, file=sys.stderr)
    if errs:
        return 1
    print("✓ git 還原核對通過", file=sys.stderr)
    print(f"T={T}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
