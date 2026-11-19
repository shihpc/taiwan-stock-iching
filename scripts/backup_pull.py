#!/usr/bin/env python3
"""P4-B2 Hetzner 每日備份拉取（docs/P4-PREVIEW.md §32；v1.2.2 §12.2 份 1／份 2／份 3 的 Hetzner 那一處）。

做三件事（只寫 --dest 底下，不碰 repo 工作樹）：
  1. Git 鏡像（份 1）：<dest>/git.git；每次 `git fetch` 全部 heads／tags，**不 prune**
     （首次以 `git init --bare` 建立、只抓 heads／tags，不帶 refs/pull；GitHub 端誤刪分支或 tag 時，鏡像不跟著刪）；鏡像開 reflog 永不過期、gc 不清理（gc.pruneExpire never／gc.auto 0），
     被改寫掉的舊 commit 物件仍留在鏡像裡；每次另存 refs 清單 <dest>/refs/refs-<UTC 時刻>.txt，舊 tip 的 sha 從這裡查。
  2. Release 快照（份 2）：列出 tag `snap-YYYYMMDD` 且日期在保留窗內（今日往前 --keep-days 天）的 Release；
     本機 <dest>/snap/<T>/ 已有且清單 content_sha256 相同 → 跳過；否則下載 tar.gz＋清單到暫存、`verify_snapshot.verify` 通過才換入
     （原子改名，舊版移到 <dest>/snap/.replaced/）。保留窗外的本機快照刪除。
  3. 校驗清單（份 3）：每份清單另存 <dest>/manifests/manifest-<T>.json（全歷史、不刪）；同一 T 的清單變了，舊版改名
     manifest-<T>.<舊 content_sha256 前 12 碼>.json 保留，並在報告列出（本機已有更新日期的清單時仍變動＝「異常」，算失敗）。
  另：每次重算本機保留窗內所有 tar.gz 的 sha256 與本機清單比對（偵測本機靜默損毀）；與 Release 清單相同但本機 tar.gz 壞了就重新下載。

用法：python3 scripts/backup_pull.py --dest /root/backup/iching [--repo shihpc/taiwan-stock-iching] [--keep-days 180] [--no-git]
exit：0 全部正常；1 有任何下載／校驗失敗或本機損毀；2 參數錯。
public repo、免 token（GitHub API 未驗證呼叫每小時 60 次，每日一次列 Release 只用 1～3 次）。
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from verify_snapshot import verify  # noqa: E402

TAG_RE = re.compile(r"^snap-(\d{4})(\d{2})(\d{2})$")
TPE = dt.timezone(dt.timedelta(hours=8))
UA = "iching-backup-pull"


def http_get(url: str, timeout: int = 60) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/vnd.github+json"})
    last = None
    for i in range(3):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read()
        except Exception as e:  # noqa: BLE001
            last = e
            time.sleep(2 * (i + 1))
    raise RuntimeError(f"下載失敗 {url}：{type(last).__name__}: {last}")


def list_releases(repo: str, get=http_get) -> list[dict]:
    """回 [{tag, date, assets:{name: url}}]，只收 tag 形如 snap-YYYYMMDD 者。"""
    out, page = [], 1
    while True:
        j = json.loads(get(f"https://api.github.com/repos/{repo}/releases?per_page=100&page={page}"))
        if not isinstance(j, list):
            raise RuntimeError("releases API 回應形狀錯")
        for r in j:
            m = TAG_RE.match(str(r.get("tag_name", "")))
            if not m:
                continue
            assets = {a.get("name"): a.get("browser_download_url") for a in r.get("assets") or [] if a.get("name")}
            out.append({"tag": r["tag_name"], "date": f"{m.group(1)}-{m.group(2)}-{m.group(3)}", "assets": assets})
        if len(j) < 100:
            return out
        page += 1


def sha256_file(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def git_mirror(dest: Path, repo: str, run=subprocess.run, now: dt.datetime | None = None, url: str | None = None) -> list[str]:
    errs = []
    g = dest / "git.git"
    url = url or f"https://github.com/{repo}.git"
    try:
        if not (g / "HEAD").is_file():
            run(["git", "init", "-q", "--bare", str(g)], check=True, capture_output=True, text=True)
        # 每次都套用（冪等）：首次 fetch 中途被殺時，下一次仍補得上保護性設定
        for k, v in (("remote.origin.url", url), ("core.logAllRefUpdates", "always"), ("gc.reflogExpire", "never"),
                     ("gc.reflogExpireUnreachable", "never"), ("gc.pruneExpire", "never"), ("gc.auto", "0"),
                     ("remote.origin.prune", "false"), ("fetch.prune", "false")):
            run(["git", "-C", str(g), "config", k, v], check=True, capture_output=True, text=True)
        run(["git", "-C", str(g), "symbolic-ref", "HEAD", "refs/heads/main"], check=True, capture_output=True, text=True)
        run(["git", "-C", str(g), "fetch", "--no-prune", "origin", "+refs/heads/*:refs/heads/*", "+refs/tags/*:refs/tags/*"],
            check=True, capture_output=True, text=True, timeout=1800)
        refs = run(["git", "-C", str(g), "for-each-ref", "--format=%(objectname) %(refname)"],
                   check=True, capture_output=True, text=True).stdout
        (dest / "refs").mkdir(parents=True, exist_ok=True)
        stamp = (now or dt.datetime.now(dt.timezone.utc)).strftime("%Y%m%dT%H%M%SZ")
        (dest / "refs" / f"refs-{stamp}.txt").write_text(refs, encoding="utf-8")
    except Exception as e:  # noqa: BLE001
        msg = getattr(e, "stderr", "") or str(e)
        errs.append(f"git 鏡像失敗：{str(msg).strip()[:300]}")
    return errs


def load_json(p: Path):
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return None


MAN_RE = re.compile(r"^manifest-(\d{4}-\d{2}-\d{2})\.json$")


def keep_manifest(dest: Path, T: str, raw: bytes, today: str) -> tuple[str, str] | None:
    """份 3：存 manifests/manifest-<T>.json；內容變了舊版改名保留。回 (種類, 說明) 或 None（無變動）。
    種類＝「異常」：變動前本機已存有比 T 新的清單（正常流程下 backup.yml 只重建 latest.json 的資料日，
    出現更新一日的快照之後 T 就不該再變；手動 dispatch 指定舊日期屬例外，需人工確認）；否則「更新」。
    不用「距今天數」判斷：國定假日／週末的試算班會讓同一個 T 在數天後仍合法更新。"""
    md = dest / "manifests"
    md.mkdir(parents=True, exist_ok=True)
    p = md / f"manifest-{T}.json"
    newer = any(m and m.group(1) > T for m in (MAN_RE.match(x.name) for x in md.iterdir()))
    if p.exists():
        if p.read_bytes() == raw:
            return None
        old = load_json(p) or {}
        new = json.loads(raw)
        if old.get("content_sha256") == new.get("content_sha256"):
            p.write_bytes(raw)      # 只有 commit／generated_at 不同：以新版為準，不留舊版
            return None
        tag = str(old.get("content_sha256") or sha256_file(p))[:12]
        p.rename(md / f"manifest-{T}.{tag}.json")
        p.write_bytes(raw)
        kind = "異常" if newer else "更新"
        return kind, f"{kind}：{T} 清單內容變動（舊 {tag} → 新 {str(new.get('content_sha256'))[:12]}）" + \
            ("；本機已有更新日期的清單，正常流程不會再改動此日" if newer else "")
    p.write_bytes(raw)
    return None


def pull(dest: Path, repo: str, keep_days: int, today: str, get=http_get, git=True, run=subprocess.run, git_url: str | None = None) -> dict:
    rep = {"errors": [], "notes": [], "downloaded": [], "skipped": [], "removed": [], "local_ok": 0}
    dest.mkdir(parents=True, exist_ok=True)
    if git:
        rep["errors"] += git_mirror(dest, repo, run=run, url=git_url)
    cutoff = (dt.date.fromisoformat(today) - dt.timedelta(days=keep_days)).isoformat()
    try:
        rels = list_releases(repo, get=get)
    except Exception as e:  # noqa: BLE001
        rep["errors"].append(f"列 Release 失敗：{e}")
        rels = []
    snap = dest / "snap"
    snap.mkdir(parents=True, exist_ok=True)
    for d in snap.glob(".tmp-*"):          # 上次被殺留下的暫存
        shutil.rmtree(d, ignore_errors=True)
    for r in sorted(rels, key=lambda x: x["date"]):
        T = r["date"]
        if T < cutoff:
            continue
        mname, aname = f"manifest-{T}.json", f"snap-{T}.tar.gz"
        if mname not in r["assets"] or aname not in r["assets"]:
            rep["errors"].append(f"{r['tag']} 缺資產（需 {mname} 與 {aname}）")
            continue
        try:
            mraw = get(r["assets"][mname])
            man = json.loads(mraw)
            if not isinstance(man, dict) or man.get("date") != T:
                raise RuntimeError("清單形狀錯或 date 不符")
        except Exception as e:  # noqa: BLE001
            rep["errors"].append(f"{T} 清單下載／解析失敗：{e}")
            continue
        note = keep_manifest(dest, T, mraw, today)
        if note:
            (rep["errors"] if note[0] == "異常" else rep["notes"]).append(note[1])
        cur = load_json(snap / T / mname)
        if cur and cur.get("content_sha256") == man.get("content_sha256") and (snap / T / aname).is_file():
            if sha256_file(snap / T / aname) == (man.get("archive") or {}).get("sha256"):
                rep["skipped"].append(T)
                continue
            rep["notes"].append(f"本機快照損毀，重新下載：{T}")   # 自癒：本機 tar.gz 與清單不符就重抓
        with tempfile.TemporaryDirectory(dir=snap, prefix=".tmp-") as td:
            tdp = Path(td) / T
            tdp.mkdir()
            try:
                (tdp / aname).write_bytes(get(r["assets"][aname], timeout=600))
                (tdp / mname).write_bytes(mraw)
                errs = verify(man, tdp / aname)
            except Exception as e:  # noqa: BLE001
                errs = [f"下載或校驗例外：{e}"]
            if errs:
                rep["errors"].append(f"{T} 校驗失敗：{'；'.join(errs[:5])}")
                continue
            if (snap / T).exists():
                bak = snap / ".replaced" / f"{T}.{str((cur or {}).get('content_sha256', 'x'))[:12]}"
                bak.parent.mkdir(parents=True, exist_ok=True)
                if bak.exists():
                    shutil.rmtree(bak)
                (snap / T).rename(bak)
            tdp.rename(snap / T)
            rep["downloaded"].append(T)
    # 保留窗外刪除（.replaced 也套同一窗）；manifests/ 全歷史保留
    for d in sorted(snap.iterdir()):
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", d.name) and d.name < cutoff:
            shutil.rmtree(d)
            rep["removed"].append(d.name)
    rp = snap / ".replaced"
    if rp.is_dir():
        for d in sorted(rp.iterdir()):
            if d.name[:10] < cutoff:
                shutil.rmtree(d)
    # 本機靜默損毀：保留窗內每份 tar.gz 重算 sha256 對本機清單
    for d in sorted(snap.iterdir()):
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", d.name):
            continue
        man = load_json(d / f"manifest-{d.name}.json")
        arc = d / f"snap-{d.name}.tar.gz"
        if not man or not arc.is_file() or sha256_file(arc) != (man.get("archive") or {}).get("sha256"):
            rep["errors"].append(f"本機快照損毀或不完整：{d.name}")
        else:
            rep["local_ok"] += 1
    return rep


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--dest", required=True)
    ap.add_argument("--repo", default="shihpc/taiwan-stock-iching")
    ap.add_argument("--keep-days", type=int, default=180)
    ap.add_argument("--today", default=None, help="台北日期 YYYY-MM-DD（測試用；預設今日）")
    ap.add_argument("--no-git", action="store_true", help="不做 Git 鏡像（只拉 Release）")
    a = ap.parse_args(argv)
    today = a.today or dt.datetime.now(TPE).date().isoformat()
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", today) or a.keep_days < 1 or not re.fullmatch(r"[\w.-]+/[\w.-]+", a.repo):
        print("!! 參數錯", file=sys.stderr)
        return 2
    rep = pull(Path(a.dest), a.repo, a.keep_days, today, git=not a.no_git)
    stamp = dt.datetime.now(TPE).isoformat(timespec="seconds")
    print(f"== backup_pull {stamp} dest={a.dest} 保留 {a.keep_days} 天")
    print(f"下載 {len(rep['downloaded'])}：{' '.join(rep['downloaded']) or '—'}")
    print(f"未變跳過 {len(rep['skipped'])}；本機校驗通過 {rep['local_ok']}；刪除過期 {len(rep['removed'])}：{' '.join(rep['removed']) or '—'}")
    for n in rep["notes"]:
        print("※ " + n)
    for e in rep["errors"]:
        print("✗ " + e)
    print("結果：" + ("失敗" if rep["errors"] else "正常"))
    return 1 if rep["errors"] else 0


if __name__ == "__main__":
    sys.exit(main())
