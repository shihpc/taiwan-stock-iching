#!/usr/bin/env python3
"""P4-B 快照校驗（docs/P4-PREVIEW.md §31）：tar.gz 與 manifest 逐檔比對。

檢查：archive 的大小與 sha256＝manifest.archive；tar 內每個成員皆為一般檔、路徑不含 `..`／絕對路徑；
成員集合＝manifest.files（無多無少）；逐檔大小與 sha256 相同；n_files／total_size／content_sha256 自洽。
用法：python scripts/verify_snapshot.py manifest-T.json snap-T.tar.gz
exit：0 通過；1 不符（逐項列出）；2 讀不到／形狀錯。
"""
from __future__ import annotations

import hashlib
import json
import re
import sys
import tarfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from build_snapshot import content_sha  # noqa: E402


def verify(manifest: dict, archive: Path) -> list[str]:
    errs = []
    raw = archive.read_bytes()
    a = manifest.get("archive") or {}
    if a.get("size") != len(raw):
        errs.append(f"archive 大小 {len(raw)} ≠ manifest {a.get('size')}")
    if a.get("sha256") != hashlib.sha256(raw).hexdigest():
        errs.append("archive sha256 不符")
    want = {e["path"]: e for e in manifest.get("files", [])}
    if len(want) != len(manifest.get("files", [])):
        errs.append("manifest files 有重複路徑")
    if not (isinstance(manifest.get("date"), str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", manifest["date"])):
        errs.append("manifest date 形狀錯")
    elif a.get("name") != f"snap-{manifest['date']}.tar.gz" or archive.name != a.get("name"):
        errs.append(f"archive 檔名不符：{archive.name} ／ manifest {a.get('name')}")
    seen = set()
    with tarfile.open(archive, "r:gz") as tar:
        for m in tar.getmembers():
            n = m.name
            if not m.isfile() or n.startswith("/") or ".." in n.split("/"):
                errs.append(f"成員不合法：{n!r}")
                continue
            if n in seen:
                errs.append(f"成員重複：{n}")
                continue
            seen.add(n)
            e = want.get(n)
            if e is None:
                errs.append(f"多出：{n}")
                continue
            data = tar.extractfile(m).read()
            if len(data) != e["size"]:
                errs.append(f"大小不符：{n}")
            if hashlib.sha256(data).hexdigest() != e["sha256"]:
                errs.append(f"sha256 不符：{n}")
    for n in sorted(set(want) - seen):
        errs.append(f"缺少：{n}")
    files = manifest.get("files", [])
    if manifest.get("n_files") != len(files):
        errs.append("n_files 與 files 筆數不符")
    if manifest.get("total_size") != sum(e["size"] for e in files):
        errs.append("total_size 與 files 合計不符")
    if manifest.get("content_sha256") != content_sha(files):
        errs.append("content_sha256 與 files 不符")
    return errs


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) != 2:
        print(__doc__, file=sys.stderr)
        return 2
    try:
        man = json.loads(Path(argv[0]).read_text(encoding="utf-8"))
        assert isinstance(man, dict) and man.get("schema") == 1 and isinstance(man.get("files"), list)
        errs = verify(man, Path(argv[1]))
    except Exception as e:  # noqa: BLE001
        print(f"!! 讀不到或形狀錯：{type(e).__name__}: {e}", file=sys.stderr)
        return 2
    if errs:
        for x in errs[:50]:
            print("✗ " + x)
        print(f"!! 校驗失敗 {len(errs)} 項")
        return 1
    print(f"✓ {man['date']} {man['n_files']} 檔全數相符（archive {man['archive']['sha256'][:12]}）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
