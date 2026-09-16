"""P2 公告收集器（裁定 #43／#45；規格 `docs/P2-COLLECTOR.md`）。`.github/workflows/collect-events.yml` 四班呼叫。

子命令：
  collect --band {1530,1830,2345,am,auto} [--date D] [--root R]   抓 mopsfin 兩支 CSV（sii／otc 當日快照，含說明全文）
                                                                 → 依**發言日**併進 `data/events/<日>.json.gz` → 重算 `_timing.json`
                                                                 → 留痕 `runs/collect/<班次日>-<band>.json`
  verify  [--date D] [--root R]                                   mopsov 單日列表（sii＋otc）對事件庫核對 → `runs/collect/<D>-verify.json`
`am` 班＝先 verify「上次 verify 過的日＋1」到昨日的每個曆日（上限 7 日、無紀錄時只做昨日），再做一次 collect。
`--band auto`（cron 用）依台北時鐘取最近一個已到點的班（`announce.pick_band` 的判定表）。

留痕檔名用**班次的排程日**而非執行日：23:45 班被 GitHub cron 延遲到隔日 00:xx 執行時，若用執行日會與隔日自己的 23:45 班撞名
（`docs/P2-COLLECTOR.md` §5.2 取捨）。`scheduled_for`／`started_at`／`completed_at` 都在檔內，執行日看 `started_at`。

失敗處置：來源回 WAF 擋頁或非 200 ＝ **該來源失敗**（留痕 `status:"waf"|"error"`、不寫事件、整班 exit 1 讓 notify-failure 亮）；
另一個來源正常時照常落地（一個來源失敗不影響另一個）。留痕永遠寫（失敗也寫），workflow 先 commit 留痕再依 rc 讓 job 紅。

網路只在 `announce.fetch_csv`／`fetch_mopsov`，`--fixture-dir` 讀本地檔代替（離線驗證用；檔名見 `FixtureHttp`）。
時區一律台北（`datetime.now(TPE)`）。不吃任何 secret。
"""
from __future__ import annotations

import argparse
import os
import re
import sys
from datetime import datetime, timedelta
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from iching import announce as A  # noqa: E402

MAX_VERIFY_DAYS = 7


class FixtureHttp:
    """離線用：`<dir>/twse_csv.csv`／`tpex_csv.csv`／`mopsov_<typek>_<YYYY-MM-DD>.html`；缺檔＝連線失敗（拋 FileNotFoundError）。
    另可放 `<name>.status`（一行整數）模擬非 200。"""

    def __init__(self, d: Path):
        self.dir = Path(d)
        self.calls: list[tuple[str, str, dict | None]] = []

    def _file(self, method: str, url: str, data: dict | None) -> str:
        if method == "POST":
            d = f"{int(data['year']) + 1911}-{data['month']}-{data['b_date']}"
            return f"mopsov_{data['TYPEK']}_{d}.html"
        for name, (_, u) in A.CSV_URLS.items():
            if u == url:
                return f"{name}.csv"
        raise FileNotFoundError(url)

    def __call__(self, method: str, url: str, data: dict | None) -> A.HttpResult:
        self.calls.append((method, url, data))
        fn = self._file(method, url, data)
        body = (self.dir / fn).read_bytes()
        st = self.dir / (fn.rsplit(".", 1)[0] + ".status")
        status = int(st.read_text().strip()) if st.exists() else 200
        ctype = "text/html; charset=UTF-8" if fn.endswith(".html") else "text/csv"
        return A.HttpResult(status, body, ctype)


def log(msg: str) -> None:
    print(msg, flush=True)


def runs_dir(root: Path) -> Path:
    return Path(root) / A.RUNS_DIR


def run_path(root: Path, date: str, band: str) -> Path:
    return runs_dir(root) / f"{date}-{band}.json"


def verify_path(root: Path, date: str) -> Path:
    return runs_dir(root) / f"{date}-verify.json"


def last_verified_date(root: Path) -> str | None:
    d = runs_dir(root)
    if not d.exists():
        return None
    dates = [m.group(1) for p in d.iterdir() if (m := re.fullmatch(r"(\d{4}-\d{2}-\d{2})-verify\.json", p.name))]
    return max(dates) if dates else None


def waf_seen_for(root: Path, date: str) -> bool:
    """該發言日的任一收集班（1530／1830／2345，加隔日 am 的 collect）留痕記 waf → 核對缺漏可標 `waf`。"""
    nxt = (datetime.strptime(date, "%Y-%m-%d") + timedelta(days=1)).strftime("%Y-%m-%d")
    for band, d in (("1530", date), ("1830", date), ("2345", date), ("am", nxt)):
        p = run_path(root, d, band)
        if not p.exists():
            continue
        try:
            rec = A.DC.read_json(p, what="留痕")
        except A.DC.DailyCoreError:
            continue
        if any(s.get("waf") for s in rec.get("sources", [])):
            return True
    return False


# ---------------------------------------------------------------------------
def do_collect(root: Path, band: str, date: str, http: A.Http, now: datetime) -> tuple[dict, int]:
    """抓兩支 CSV → 合併寫檔 → 留痕。回 `(record, rc)`；rc≠0＝任一來源失敗。"""
    started = A.now_iso(now)
    rec: dict = {"schema": A.SCHEMA, "band": band, "scheduled_for": A.scheduled_for(band, date), "started_at": started,
                 "completed_at": None, "sources": [], "received": 0, "new_events": 0, "new_versions": 0, "filled": 0,
                 "unchanged": 0, "files": [], "errors": []}
    all_rows: list[dict] = []
    for name in A.CSV_URLS:
        rows, src = A.fetch_csv(http, name)
        rec["sources"].append(src)
        if src["status"] != "ok":
            rec["errors"].append(f"{name}: {src['error']}")
            log(f"[collect] {name} 失敗：{src['error']}（status={src['status']}）")
            continue
        log(f"[collect] {name} {src['rows']} 列（表頭 {src['header_kind']}，丟棄 {src['dropped']}，範圍 {src['span']}）")
        all_rows.extend(rows)
    rec["received"] = len(all_rows)
    ts = A.now_iso(now)
    for d, group in A.group_by_date(all_rows).items():
        doc, st = A.merge(A.load_doc(root, d), group, ts, "csv")
        written = A.write_if_changed(A.events_path(root, d), doc)
        for k in ("new_events", "new_versions", "filled", "unchanged"):
            rec[k] += st[k]
        rec["files"].append({"date": d, "rows": len(group), "written": written, **{k: st[k] for k in ("new_events", "new_versions", "filled")}})
        log(f"[collect] {d}: 列 {len(group)} 新事件 {st['new_events']} 新版本 {st['new_versions']} 補全 {st['filled']} {'寫檔' if written else '未變'}")
    if all_rows:
        _, tw = A.timing_update(root)
        rec["timing_written"] = tw
    rec["completed_at"] = A.now_iso(datetime.now(A.TPE) if now is None else now)
    A.DC.write_json(run_path(root, date, band), rec)
    return rec, (0 if not rec["errors"] else 1)


def do_verify(root: Path, date: str, http: A.Http, now: datetime) -> tuple[dict, int]:
    """mopsov sii＋otc 單日列表 → `verify_day` → 缺漏補進事件庫 → 寫 `<date>-verify.json`。回 `(report, rc)`。"""
    started = A.now_iso(now)
    rows: list[dict] = []
    sources, errors = [], []
    for typek in ("sii", "otc"):
        r, src = A.fetch_mopsov(http, typek, date)
        sources.append(src)
        if src["status"] != "ok":
            errors.append(f"mopsov_{typek}: {src['error']}")
            log(f"[verify] {date} mopsov_{typek} 失敗：{src['error']}")
        else:
            log(f"[verify] {date} mopsov_{typek} {src['rows']} 列{'（查無資料）' if src['empty'] else ''}")
        rows.extend(r)
    docs: dict[str, dict] = {}
    for d in {r["spoke_date"] for r in rows} | {date}:
        docs[d] = A.load_doc(root, d)
    if errors:
        # 來源失敗時不下「缺漏」判斷（半份列表比出來的缺漏率是假的），只留痕
        rep = {"schema": A.SCHEMA, "date": date, "started_at": started, "completed_at": A.now_iso(now), "sources": sources,
               "mopsov_total": None, "matched": None, "missing": [], "missing_rate": None, "reasons": None, "errors": errors}
        A.DC.write_json(verify_path(root, date), rep)
        return rep, 1
    rep = A.verify_day(date, rows, docs, A.now_iso(now), waf=waf_seen_for(root, date))
    written = []
    for d, doc in sorted(docs.items()):
        if A.write_if_changed(A.events_path(root, d), doc):
            written.append(d)
    if written:
        A.timing_update(root)
    rep.update(started_at=started, completed_at=A.now_iso(now), sources=sources, files_written=written, errors=[])
    A.DC.write_json(verify_path(root, date), rep)
    log(f"[verify] {date}: mopsov {rep['mopsov_total']} 列，命中 {rep['matched']}，缺漏 {len(rep['missing'])}（{rep['missing_rate']}）{rep['reasons']}")
    return rep, 0


def verify_range(root: Path, today: str) -> list[str]:
    """am 班要核對的日：上次 verify 日＋1 到昨日（曆日，含週末——mopsov 回查無＝0 列照樣留痕），上限 7 日取最近的。"""
    yday = (datetime.strptime(today, "%Y-%m-%d") - timedelta(days=1))
    last = last_verified_date(root)
    start = (datetime.strptime(last, "%Y-%m-%d") + timedelta(days=1)) if last else yday
    if start > yday:
        return []
    days = []
    d = start
    while d <= yday:
        days.append(d.strftime("%Y-%m-%d"))
        d += timedelta(days=1)
    return days[-MAX_VERIFY_DAYS:]


def iso_date(s: str) -> str:
    """`--date` 只收 `YYYY-MM-DD` 且是真日期——它會經 step output 進 commit 訊息，不能讓任意字串流過去。"""
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", s):
        raise argparse.ArgumentTypeError(f"日期須為 YYYY-MM-DD：{s!r}")
    try:
        datetime.strptime(s, "%Y-%m-%d")
    except ValueError as e:
        raise argparse.ArgumentTypeError(f"不是合法日期：{s!r}") from e
    return s


def gh_output(**kv: str) -> None:
    p = os.environ.get("GITHUB_OUTPUT")
    if p:
        with open(p, "a", encoding="utf-8") as f:
            for k, v in kv.items():
                f.write(f"{k}={v}\n")


def main(argv: list[str] | None = None, http: A.Http | None = None, now: datetime | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("collect")
    c.add_argument("--band", required=True, choices=A.BANDS + ("auto",))
    c.add_argument("--date", default=None, type=iso_date, help="班次排程日（YYYY-MM-DD；預設依台北時鐘）")
    v = sub.add_parser("verify")
    v.add_argument("--date", default=None, type=iso_date, help="核對的發言日（預設台北昨日）")
    for p in (c, v):
        p.add_argument("--root", default=str(REPO))
        p.add_argument("--fixture-dir", default=os.environ.get("COLLECT_FIXTURE_DIR") or None, help=argparse.SUPPRESS)
        p.add_argument("--interval", type=float, default=1.0)
        p.add_argument("--timeout", type=float, default=30.0)
    args = ap.parse_args(argv)

    root = Path(args.root)
    now = now or datetime.now(A.TPE)
    if http is None:
        http = FixtureHttp(Path(args.fixture_dir)) if args.fixture_dir else A.RequestsHttp(args.interval, args.timeout)
    today = now.astimezone(A.TPE).strftime("%Y-%m-%d")

    if args.cmd == "verify":
        date = args.date or (now.astimezone(A.TPE) - timedelta(days=1)).strftime("%Y-%m-%d")
        _, rc = do_verify(root, date, http, now)
        gh_output(run_date=date, band="verify")
        return rc

    band, date = (A.pick_band(now) if args.band == "auto" else (args.band, args.date or today))
    if args.date:
        date = args.date
    log(f"[collect] band={band} scheduled_for={A.scheduled_for(band, date)} now={A.now_iso(now)}")
    rc = 0
    if band == "am":
        days = verify_range(root, date)
        log(f"[collect] am 班先核對：{days or '（無）'}")
        for d in days:
            _, r = do_verify(root, d, http, now)
            rc |= r
    _, r = do_collect(root, band, date, http, now)
    rc |= r
    gh_output(run_date=date, band=band)
    return rc


if __name__ == "__main__":
    sys.exit(main())
