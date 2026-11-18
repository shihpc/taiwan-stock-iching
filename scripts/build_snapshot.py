#!/usr/bin/env python3
"""P4-B1 每日快照＋校驗清單（docs/P4-PREVIEW.md §31；v1.2.2 §12.2 份 2／份 3）。

產出（--out 目錄）：
  snap-<T>.tar.gz   決定性 tar.gz（檔案依路徑排序、mtime／uid／gid 固定、gzip mtime 0）——同一份輸入逐位元組相同
  manifest-<T>.json {schema, date, commit, batch_id, generated_at, archive{name,size,sha256}, files[{path,size,sha256}],
                     n_files, total_size, content_sha256}

內容＝repo 內 `data/` 除 `data/backtest/` 外的全部檔；日期型目錄只收 T（scores／events／pick／limits，pick 另收 t0.json），
另收 `runs/collect/<T>-*` 與 `runs/trial/<T>-*`。`content_sha256`＝files 清單的 sha256（不含 generated_at／commit），
供 backup.yml 判斷「內容是否變過」。

用法：python scripts/build_snapshot.py --date 2026-11-18 --out /tmp/snap [--root .] [--commit SHA]
exit：0 成功；2 T 的 data/scores/<T>.json 不存在或日期格式錯（不產物）。
"""
from __future__ import annotations

import argparse
import datetime as dt
import gzip
import hashlib
import io
import json
import re
import subprocess
import sys
import tarfile
from pathlib import Path

DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
TPE = dt.timezone(dt.timedelta(hours=8))
EXCLUDE_DIRS = ("data/backtest/",)
# 日期型目錄：只收 T（值＝該目錄下 T 的檔名判斷式）
DATED = {
    "data/scores/": lambda name, T: name == f"{T}.json",
    "data/events/": lambda name, T: name.startswith(T) and name.endswith(".json.gz"),
    "data/pick/": lambda name, T: name in (f"{T}.json", "t0.json"),
    "data/limits/": lambda name, T: name == f"{T}.json",
}
EXTRA = ("runs/collect/", "runs/trial/")   # 只收 <T>-* 的檔


def sha256_file(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def select_files(root: Path, T: str) -> list[str]:
    """回快照內容的相對路徑清單（排序、POSIX 斜線）。不含目錄、不跟隨 symlink、略過 __pycache__ 與隱藏檔。"""
    out = []
    data = root / "data"
    for p in sorted(data.rglob("*")):
        if not p.is_file() or p.is_symlink():
            continue
        rel = p.relative_to(root).as_posix()
        if any(part.startswith(".") or part == "__pycache__" for part in rel.split("/")):
            continue
        if rel.startswith(EXCLUDE_DIRS):
            continue
        d = next((k for k in DATED if rel.startswith(k)), None)
        if d is not None:
            sub = rel[len(d):]
            if "/" in sub or not DATED[d](sub, T):
                continue
        out.append(rel)
    for d in EXTRA:
        base = root / d
        if not base.is_dir():
            continue
        for p in sorted(base.iterdir()):
            if p.is_file() and not p.is_symlink() and p.name.startswith(T + "-"):
                out.append(p.relative_to(root).as_posix())
    return sorted(set(out))


def build_archive(root: Path, files: list[str]) -> bytes:
    buf = io.BytesIO()
    with gzip.GzipFile(filename="", mode="wb", fileobj=buf, mtime=0, compresslevel=9) as gz:
        with tarfile.open(fileobj=gz, mode="w", format=tarfile.PAX_FORMAT) as tar:
            for rel in files:
                data = (root / rel).read_bytes()
                ti = tarfile.TarInfo(rel)
                ti.size = len(data)
                ti.mtime = 0
                ti.mode = 0o644
                ti.uid = ti.gid = 0
                ti.uname = ti.gname = ""
                tar.addfile(ti, io.BytesIO(data))
    return buf.getvalue()


def files_entries(root: Path, files: list[str]) -> list[dict]:
    return [{"path": rel, "size": (root / rel).stat().st_size, "sha256": sha256_file(root / rel)} for rel in files]


def content_sha(entries: list[dict]) -> str:
    s = json.dumps(entries, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def git_head(root: Path) -> str | None:
    try:
        r = subprocess.run(["git", "-C", str(root), "rev-parse", "HEAD"], capture_output=True, text=True, timeout=30)
        v = r.stdout.strip()
        return v if r.returncode == 0 and re.fullmatch(r"[0-9a-f]{40}", v) else None
    except Exception:  # noqa: BLE001
        return None


def batch_id(root: Path, T: str):
    try:
        j = json.loads((root / "data" / "scores" / f"{T}.json").read_text(encoding="utf-8"))
        v = j.get("data_version")
        return v if isinstance(v, str) and v else None
    except Exception:  # noqa: BLE001
        return None


def build(root: Path, T: str, out: Path, commit: str | None = None, now: dt.datetime | None = None) -> dict:
    files = select_files(root, T)
    entries = files_entries(root, files)
    arc = build_archive(root, files)
    name = f"snap-{T}.tar.gz"
    out.mkdir(parents=True, exist_ok=True)
    (out / name).write_bytes(arc)
    now = now or dt.datetime.now(TPE)
    man = {
        "schema": 1, "date": T, "commit": commit if commit is not None else git_head(root),
        "batch_id": batch_id(root, T), "generated_at": now.astimezone(TPE).isoformat(timespec="seconds"),
        "archive": {"name": name, "size": len(arc), "sha256": hashlib.sha256(arc).hexdigest()},
        "files": entries, "n_files": len(entries), "total_size": sum(e["size"] for e in entries),
        "content_sha256": content_sha(entries),
    }
    (out / f"manifest-{T}.json").write_text(json.dumps(man, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    return man


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--date", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--root", default=".")
    ap.add_argument("--commit", default=None)
    a = ap.parse_args(argv)
    root = Path(a.root).resolve()
    if not DATE_RE.match(a.date):
        print(f"!! 日期格式錯：{a.date!r}", file=sys.stderr)
        return 2
    if not (root / "data" / "scores" / f"{a.date}.json").is_file():
        print(f"!! data/scores/{a.date}.json 不存在，不產快照", file=sys.stderr)
        return 2
    man = build(root, a.date, Path(a.out), commit=a.commit)
    print(f"snapshot {a.date}: {man['n_files']} 檔、原始 {man['total_size']:,} bytes → {man['archive']['name']} "
          f"{man['archive']['size']:,} bytes sha256 {man['archive']['sha256'][:12]} content {man['content_sha256'][:12]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
