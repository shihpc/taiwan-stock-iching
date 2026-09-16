"""P2 公告收集器（裁定 #43／#45；班次改制甲，`docs/P2-COLLECTOR.md` §5.4）。`.github/workflows/collect-events.yml` 每交易日 am 班呼叫
（台北 08:30 主班＋09:30 兜底，同 band、冪等）。

子命令：
  collect --band {1530,1830,2345,am,auto} [--date D] [--root R]   抓 mopsfin 兩支 CSV（sii／otc 當日快照，含說明全文）
                                                                 → 依**發言日**併進 `data/events/<日>.json.gz` → 重算 `_timing.json`
                                                                 → 留痕 `runs/collect/<班次日>-<band>.json`
  verify  [--date D] [--root R]                                   mopsov 單日列表（sii＋otc）對事件庫核對 → `runs/collect/<D>-verify.json`
`am` 班＝先 verify「上次 verify 過的日＋1」到昨日的每個曆日（上限 7 日、無紀錄時只做昨日），再做一次 collect。
`--band auto`：**優先讀環境變數 `CRON_EXPR`**（workflow 從 `github.event.schedule` 帶進來的 cron 字串）查 `CRON_BANDS` 對照表，
未知 cron → 明確 error（rc 2）、不猜；沒有 `CRON_EXPR`（`workflow_dispatch`／本機）才依台北時鐘推斷（`announce.pick_band`）。
理由：GitHub cron 實測延遲 4.5～5 小時，依時鐘判班會把不同 cron 判成同一班（§5.3 第 3 點）。

留痕檔名用**班次的排程日**而非執行日（§5.2 取捨），`scheduled_for`／`started_at`／`completed_at` 都在檔內。**撞名不覆蓋**
（`write_run_record`）：`<日>-<band>.json` 已存在時既有檔一個位元組不動——這次是「冪等命中」（沒寫任何事件檔、沒錯誤，且最近一份
留痕也沒錯誤）就不另寫（兜底班的常態）；否則寫成 `<日>-<band>-2.json`（序號遞增；與已有序號檔去掉時戳後內容相同也不寫）。

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
# cron 字串 → band（與 .github/workflows/collect-events.yml 的 schedule 逐字一致；改一邊要改另一邊，yaml 測試釘著）
CRON_BANDS = {"30 0 * * 1-5": "am", "30 1 * * 1-5": "am"}
RUN_VOLATILE = ("started_at", "completed_at")     # 比對「內容相同」時忽略的欄


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


def run_path(root: Path, date: str, band: str, seq: int = 1) -> Path:
    return runs_dir(root) / (f"{date}-{band}.json" if seq <= 1 else f"{date}-{band}-{seq}.json")


def run_records(root: Path, date: str, band: str) -> list[Path]:
    """該日該 band 的全部留痕（含序號檔），依序號升冪。"""
    d = runs_dir(root)
    if not d.exists():
        return []
    pat = re.compile(rf"{re.escape(date)}-{re.escape(band)}(?:-(\d+))?\.json")
    out = []
    for p in d.iterdir():
        m = pat.fullmatch(p.name)
        if m:
            out.append((int(m.group(1) or 1), p))
    return [p for _, p in sorted(out)]


def _run_sig(rec: dict) -> dict:
    return {k: v for k, v in rec.items() if k not in RUN_VOLATILE}


def _is_noop(rec: dict) -> bool:
    return not rec.get("errors") and not rec.get("verify_errors") and not any(f.get("written") for f in rec.get("files", [])) \
        and not rec.get("timing_written") and not rec.get("verify_written")


def write_run_record(root: Path, date: str, band: str, rec: dict) -> Path | None:
    """撞名不覆蓋。第一份直接寫；之後：冪等命中（本次無寫入無錯誤、最近一份留痕也無錯誤）→ 不寫、回 None；
    否則寫下一個序號檔（與任一既有序號檔去掉時戳後相同 → 不寫）。既有檔永不改寫。"""
    existing = run_records(root, date, band)
    if not existing:
        p = run_path(root, date, band)
        A.DC.write_json(p, rec)
        return p
    sigs = []
    for p in existing:
        try:
            sigs.append(_run_sig(A.DC.read_json(p, what="留痕")))
        except A.DC.DailyCoreError:
            sigs.append(None)
    last_ok = sigs[-1] is not None and not sigs[-1].get("errors")
    if _is_noop(rec) and last_ok:
        log(f"[collect] {date}-{band} 冪等命中（無寫入、無錯誤），沿用既有留痕 {existing[-1].name}、不另寫")
        return None
    sig = _run_sig(rec)
    if sig in sigs:
        log(f"[collect] {date}-{band} 與既有留痕內容相同（時戳除外），不另寫")
        return None
    p = run_path(root, date, band, len(existing) + 1)
    A.DC.write_json(p, rec)
    log(f"[collect] {date}-{band} 留痕撞名 → 寫 {p.name}（既有 {len(existing)} 份不動）")
    return p


def verify_path(root: Path, date: str) -> Path:
    return runs_dir(root) / f"{date}-verify.json"


def last_verified_date(root: Path) -> str | None:
    d = runs_dir(root)
    if not d.exists():
        return None
    dates = []
    for p in d.iterdir():
        m = re.fullmatch(r"(\d{4}-\d{2}-\d{2})-verify\.json", p.name)
        if not m:
            continue
        try:
            rep = A.DC.read_json(p, what="核對留痕")
        except A.DC.DailyCoreError:
            continue
        if not rep.get("errors"):                 # 來源失敗的核對不算「驗過」——兜底班才會回頭重驗那天
            dates.append(m.group(1))
    return max(dates) if dates else None


def waf_seen_for(root: Path, date: str) -> bool:
    """該發言日的任一收集班（1530／1830／2345，加隔日 am 的 collect）留痕記 waf → 核對缺漏可標 `waf`。"""
    nxt = (datetime.strptime(date, "%Y-%m-%d") + timedelta(days=1)).strftime("%Y-%m-%d")
    for band, d in (("1530", date), ("1830", date), ("2345", date), ("am", nxt)):
        for p in run_records(root, d, band):            # 含撞名序號檔
            try:
                rec = A.DC.read_json(p, what="留痕")
            except A.DC.DailyCoreError:
                continue
            if any(s.get("waf") for s in rec.get("sources", [])):
                return True
    return False


# ---------------------------------------------------------------------------
def do_collect(root: Path, band: str, date: str, http: A.Http, now: datetime, extra: dict | None = None) -> tuple[dict, int]:
    """抓兩支 CSV → 合併寫檔 → 留痕。回 `(record, rc)`；rc≠0＝任一來源失敗。`extra`＝am 班先做的核對摘要（併進留痕）。"""
    started = A.now_iso(now)
    rec: dict = {"schema": A.SCHEMA, "band": band, "scheduled_for": A.scheduled_for(band, date), "started_at": started,
                 "completed_at": None, "sources": [], "received": 0, "new_events": 0, "new_versions": 0, "filled": 0,
                 "unchanged": 0, "files": [], "errors": [], **(extra or {})}
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
    rec["completed_at"] = A.now_iso(now)
    rec["record"] = (p.name if (p := write_run_record(root, date, band, rec)) else None)
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


def resolve_band(band_arg: str, now: datetime, cron_expr: str | None) -> tuple[str, str] | None:
    """`--band auto`：有 `CRON_EXPR` 就查表（未知 → None＝錯，不猜），沒有才用時鐘；明給 band → 排程日＝台北今日。"""
    today = now.astimezone(A.TPE).strftime("%Y-%m-%d")
    if band_arg != "auto":
        return band_arg, today
    if cron_expr:
        band = CRON_BANDS.get(cron_expr.strip())
        return (band, today) if band else None
    return A.pick_band(now)


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
    if args.cmd == "verify":
        date = args.date or (now.astimezone(A.TPE) - timedelta(days=1)).strftime("%Y-%m-%d")
        _, rc = do_verify(root, date, http, now)
        gh_output(run_date=date, band="verify")
        return rc

    cron_expr = os.environ.get("CRON_EXPR") or None
    resolved = resolve_band(args.band, now, cron_expr)
    if resolved is None:
        log(f"[collect] 未知的 CRON_EXPR {cron_expr!r}，不猜班次（對照表 {sorted(CRON_BANDS)}）")
        return 2
    band, date = resolved
    if args.date:
        date = args.date
    log(f"[collect] band={band} scheduled_for={A.scheduled_for(band, date)} now={A.now_iso(now)}")
    rc = 0
    extra: dict = {}
    if band == "am":
        days = verify_range(root, date)
        log(f"[collect] am 班先核對：{days or '（無）'}")
        extra = {"verify_days": days, "verify_written": False, "verify_errors": []}
        for d in days:
            rep, r = do_verify(root, d, http, now)
            rc |= r
            extra["verify_written"] = extra["verify_written"] or bool(rep.get("files_written"))
            extra["verify_errors"] += [f"{d}: {e}" for e in rep.get("errors", [])]
    _, r = do_collect(root, band, date, http, now, extra)
    rc |= r
    gh_output(run_date=date, band=band)
    return rc


if __name__ == "__main__":
    sys.exit(main())
