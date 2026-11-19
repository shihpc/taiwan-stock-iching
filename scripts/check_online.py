#!/usr/bin/env python3
"""P4 驗收：線上破快取實查（docs/P4-PREVIEW.md §34；v1.2.2 §14 P4 驗收「線上破快取實查」）。

對 GitHub Pages 上的每個檔，各抓兩次——①帶一次性查詢參數（繞過 CDN 快取，看伺服器現況）②不帶參數（使用者實際會拿到的 CDN 版本）——
與 git 指定 ref 的同一檔逐位元組比對，並記下 cache-control／age；再以 If-None-Match 送條件式請求（前端 fetchFresh 走
`cache: "no-cache"`，每次都會這樣重新驗證）：回 304 或回 200 且內容與 ref 相同都算通過——GitHub Pages 各 CDN 節點的 etag 不同
（2026-11-20 實測同一檔三次請求 etag 有兩種），打到別的節點時會回 200 完整內容，內容正確即可。

用法：python3 scripts/check_online.py [--ref origin/main] [--base https://shihpc.github.io/taiwan-stock-iching/]
exit：0 全部與 ref 相同；1 有不同（含只有 CDN 版本未更新）；2 參數錯。
不比對每日班持續改寫的資料檔時，可用 --files 指定。
"""
from __future__ import annotations

import argparse
import hashlib
import subprocess
import sys
import time
import urllib.error
import urllib.request

FILES = ["index.html", "data/web/latest.json", "data/web/status.json", "data/web/pick.json", "data/web/timeline.json",
         "data/hexagram_text.json"]
UA = "iching-check-online"


def fetch(url: str, headers: dict | None = None):
    req = urllib.request.Request(url, headers={"User-Agent": UA, **(headers or {})})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.status, r.read(), {k.lower(): v for k, v in r.headers.items()}
    except urllib.error.HTTPError as e:
        return e.code, e.read() if e.code != 304 else b"", {k.lower(): v for k, v in e.headers.items()}


def git_bytes(ref: str, path: str) -> bytes | None:
    r = subprocess.run(["git", "show", f"{ref}:{path}"], capture_output=True)
    return r.stdout if r.returncode == 0 else None


def sha(b) -> str:
    return hashlib.sha256(b).hexdigest()[:12] if b is not None else "—"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--ref", default="origin/main")
    ap.add_argument("--base", default="https://shihpc.github.io/taiwan-stock-iching/")
    ap.add_argument("--files", nargs="*", default=FILES)
    a = ap.parse_args(argv)
    if not a.base.endswith("/"):
        print("!! --base 須以 / 結尾", file=sys.stderr)
        return 2
    bad = 0
    print(f"== check_online ref={a.ref}（{subprocess.run(['git', 'rev-parse', '--short', a.ref], capture_output=True, text=True).stdout.strip()}）base={a.base}")
    for f in a.files:
        want = git_bytes(a.ref, f)
        url = a.base + f
        s1, b1, h1 = fetch(f"{url}?cb={time.time_ns()}")
        s2, b2, h2 = fetch(url)
        et = h2.get("etag")
        s3, b3, _ = fetch(url, {"If-None-Match": et}) if et else (None, None, {})
        fresh, cdn = (s1 == 200 and b1 == want), (s2 == 200 and b2 == want)
        # 條件式請求：304＝沿用快取；200＝回完整內容（GitHub Pages 各 CDN 節點的 etag 不同，打到別的節點會回 200）——內容須與 ref 相同
        cond = s3 == 304 or (s3 == 200 and b3 == want)
        ok = fresh and cdn and cond
        bad += not ok
        print(f"{'✓' if ok else '✗'} {f}: ref {sha(want)}｜破快取 {s1} {sha(b1)}{'' if fresh else ' ≠ref'}｜一般 {s2} {sha(b2)}"
              f"{'' if cdn else ' ≠ref'}（cache-control {h2.get('cache-control', '—')}，age {h2.get('age', '—')}）｜If-None-Match → {s3}{'' if cond else ' 內容≠ref'}")
    print("結果：" + ("全部與 ref 相同" if not bad else f"{bad} 檔不符"))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
