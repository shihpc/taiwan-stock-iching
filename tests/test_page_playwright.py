"""`index.html` 頁面 DOM 接線層的自動守門（Playwright；docs/P4-PREVIEW.md §10「怎麼驗 2」①–⑧＋§9 核心情境回歸）。

- playwright（Python 套件）或 Chromium 不可用時整個模組 `pytest.skip`（CI 沒裝就跳過、不假綠也不紅）。
- 本機 `http.server` 從 repo 根服務 `index.html`／`data/hexagram_text.json`（repo 內檔案），`latest.json`／`timeline.json`／
  `api.github.com` 全部由 `page.route` 餵測試內建構的 fixture——**不依賴網路**、不依賴 `data/web/` 現況。
- fixture 手算值（§10 G4 ⑤）：short 4 檔＝乾為天 1（排名池 1）＋坤為地 1（0）＋澤天夬 1（1）＋未定 1（1）；
  swing 3 檔（3008 無 swing）＝火天大有 2（2）＋乾為天 1（0）；mid 4 檔＝雷天大壯 3（3）＋未定 1（0）。
- 沙箱慣例：Chromium 在 `/opt/pw-browsers` 時自動帶 `PLAYWRIGHT_BROWSERS_PATH`（環境已設則不動）。
- §11「我的持股」（唯讀 `pm_holdings`）：`Page(init_script=…)` 在 goto 前注入 localStorage 與 Storage 寫入 spy；
  `Page.reqs` 收集全部請求（URL／headers／post_data）供「持股代號不進任何網路請求」斷言。
"""
from __future__ import annotations

import copy
import functools
import http.server
import json
import os
import socket
import threading
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if "PLAYWRIGHT_BROWSERS_PATH" not in os.environ and Path("/opt/pw-browsers").is_dir():
    os.environ["PLAYWRIGHT_BROWSERS_PATH"] = "/opt/pw-browsers"

try:
    import playwright.sync_api as pw_api
except ImportError:   # 套件缺／壞都略過（不用 importorskip 的 exc_type：requirements-dev 只要求 pytest>=8，8.2 前沒有該參數）
    pytest.skip("playwright 未安裝，略過頁面 DOM 測試", allow_module_level=True)

SPEC64 = json.loads((ROOT / "spec" / "hexagrams64.json").read_text(encoding="utf-8"))
HXTEXT = {h["king_wen"]: h for h in json.loads((ROOT / "data" / "hexagram_text.json").read_text(encoding="utf-8"))["hexagrams"]}
NAME = {x["king_wen"]: x["name"] for x in SPEC64}
BITS = {x["king_wen"]: "".join(str(b) for b in x["lines_bottom_up"]) for x in SPEC64}
A, B = "c7385e78cb9f", "8ca174ee8bc7"
MV_OFF = "今日與前一日模型版本不同，不比較動爻。"
CAL_T = "部分參數已依訓練段校準（calibrated=true），其餘仍為未校準的起點值"
CAL_F = "參數未校準（calibrated=false），數字在校準後會變"
DISC_REST = ["預覽版", "未經回測驗證", "陰陽不是買賣指令", "不建吉凶排名", "AI 研判、非保證"]
# §6 S2-5 ＋ §8 #53：說明文字不得出現（古文欄 .classic／.gloss 與免責卡 #disc 除外；轉弱／轉強只准在動爻句）
FORBID = ["機率", "勝率", "看多", "看空", "買進", "賣出", "多頭", "空頭", "吉", "凶", "趨勢反轉", "亢龍有悔",
          "上行", "回撤", "衍生品", "期貨選擇權"]
DATES = [f"2026-09-{d:02d}" for d in (1, 2, 3, 4, 5, 8, 9, 10, 11, 12, 15, 16, 17, 18, 19, 22, 23, 24, 25, 26)]
N = len(DATES)
LAST = DATES[-1]


def entry(kw, st=None, **kw_):
    """一筆期間物件；kw None＝正式卦待補（lf null、暫定卦 43 澤天夬）。"""
    if kw is None:
        e = {"kw": None, "name": None, "kwp": 43, "namep": NAME[43], "lf": None, "lp": BITS[43], "st": "yyyyy-"}
    else:
        st = st or "".join("y" if c == "1" else "n" for c in BITS[kw])
        e = {"kw": kw, "name": NAME[kw], "kwp": kw, "namep": NAME[kw], "lf": BITS[kw], "lp": BITS[kw], "st": st}
    e.update({"sk": "0,0,0,0,0,0", "l": [70, 70, 70, 70, 70, 70], "unk": [0, 0, 0, 0, 0, 0], "cov": "full"})
    e.update(kw_)
    return e


def base_latest():
    return {
        "schema": 1, "date": LAST, "data_version": "dv-test", "params_sha": B, "text_version": "0.2", "calibrated": False,
        "generated_from": "test", "n_rows": 11,
        "model_version": {"twse": ["p2-score-engine-2.01697576a7b0"], "tpex": ["p2-score-engine-2.83b5c5dfdb23"]},
        "names": {"2330": ["台積電", "半導體業"], "2317": ["鴻海", "其他電子業"], "1101": ["台泥", "水泥工業"], "3008": ["大立光", "光電業"]},
        "market": {"twse|short": entry(13, "ynyyyy"), "twse|swing": entry(49), "twse|mid": entry(42),
                   "tpex|short": entry(9), "tpex|swing": entry(63), "tpex|mid": entry(37)},
        "stocks": {
            "2330": {"market": "twse", "in_rank_pool": 1, "short": entry(1, bs=53.4, ti=61.8, to=53.0), "swing": entry(14), "mid": entry(34)},
            "2317": {"market": "twse", "in_rank_pool": 0, "short": entry(2), "swing": entry(1), "mid": entry(None)},
            "1101": {"market": "twse", "in_rank_pool": 1, "short": entry(None), "swing": entry(14), "mid": entry(34)},
            "3008": {"market": "twse", "in_rank_pool": 1, "short": entry(43, "yyyyyn"), "mid": entry(34)},
        },
    }


def base_timeline():
    ser = {}
    for code, s in base_latest()["stocks"].items():
        for h in ("short", "swing", "mid"):
            e = s.get(h)
            ser[f"{code}|{h}"] = [[e["kw"], e["st"]] if e else None for _ in range(N)]
    for k, e in base_latest()["market"].items():
        ser[k] = [[e["kw"], e["st"]] for _ in range(N)]
    # 2330|short：換卦 @D[5]／D[6]、@D[10]／D[11]；最後一日 43→1 換卦＋上爻 陰→陽（動爻）——同 §9 驗收腳本
    s = ser["2330|short"]
    s[5] = [14, "yyyyyy"]
    s[10] = [34, "yyyyyy"]
    s[N - 2] = [43, "yyyyyn"]
    ser["twse|short"][N - 2] = [1, "yyyyyy"]           # 前一日乾為天 → 今日天火同人：二爻 陽→陰（動爻）
    return {"schema": 1, "dates": list(DATES), "ps": [B] * N, "series": ser}


def scen(name):
    L, T = base_latest(), base_timeline()
    if name == "1_none":
        pass
    elif name == "2_mid":
        T["ps"] = [A] * 10 + [B] * (N - 10)
    elif name == "3_today":
        T["ps"] = [A] * (N - 1) + [B]
    elif name == "4_old":
        T.pop("ps"); L.pop("model_version"); L["params_sha"] = A
    elif name == "6_inject":
        X = '<img src=x onerror="window.__xss=1">'
        T["ps"] = [A] * 10 + [X] * (N - 10); L["params_sha"] = X
        L["model_version"] = {"twse": [X, "p2"], "tpex": [X]}
        L["names"]["2330"] = [X, X]
        L["stocks"]["3008"]["short"]["name"] = L["stocks"]["3008"]["short"]["namep"] = X   # KW_NAME[43] 由 latest 收集（name 與 namep）→ 今日卦分布卦名欄
    return L, T


# ---------- 基礎設施 ----------
@pytest.fixture(scope="module")
def server():
    sock = socket.socket(); sock.bind(("127.0.0.1", 0)); port = sock.getsockname()[1]; sock.close()

    class Quiet(http.server.SimpleHTTPRequestHandler):
        def log_message(self, *a, **k):
            pass

    srv = http.server.ThreadingHTTPServer(("127.0.0.1", port), functools.partial(Quiet, directory=str(ROOT)))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{port}/index.html"
    srv.shutdown()


@pytest.fixture(scope="module")
def browser():
    with pw_api.sync_playwright() as p:
        b = None
        try:
            b = p.chromium.launch()
        except Exception as e1:  # noqa: BLE001 — 版本不合時退而找 PLAYWRIGHT_BROWSERS_PATH 下任一 chromium；都沒有就 skip
            root = Path(os.environ.get("PLAYWRIGHT_BROWSERS_PATH", ""))
            exes = sorted(root.glob("chromium-*/chrome-linux/chrome")) if root.is_dir() else []
            for exe in reversed(exes):
                try:
                    b = p.chromium.launch(executable_path=str(exe)); break
                except Exception:  # noqa: BLE001
                    continue
            if b is None:
                pytest.skip(f"Chromium 不可用：{str(e1).splitlines()[0][:200]}")
        yield b
        b.close()


class Page:
    def __init__(self, ctx, base, L, T, hash_="", latest_status=200, HX=None, init_script=None):
        self.errs = []
        self.reqs = []   # (url, headers dict, post_data or None)
        self.pg = ctx.new_page()
        self.pg.on("pageerror", lambda e: self.errs.append("pageerror " + str(e)))
        self.pg.on("console", lambda m: self.errs.append("console " + m.text) if m.type == "error" else None)
        self.pg.on("request", lambda r: self.reqs.append((r.url, dict(r.headers), r.post_data)))
        if init_script:   # goto 前注入（每次導覽都會先跑）：§11 用來寫 localStorage fixture 與裝 Storage 寫入 spy
            self.pg.add_init_script(init_script)
        self.pg.route("https://api.github.com/**", lambda r: r.fulfill(status=200, content_type="application/json",
                      body=json.dumps({"sha": "abcdef1234567", "commit": {"committer": {"date": "2026-09-26T00:00:00Z"}}})))
        self.pg.route("**/data/web/latest.json*", lambda r: r.fulfill(status=latest_status, content_type="application/json",
                      body=json.dumps(L, ensure_ascii=False) if latest_status == 200 else "nope"))
        self.pg.route("**/data/web/timeline.json*", lambda r: r.fulfill(status=200, content_type="application/json",
                      body=json.dumps(T, ensure_ascii=False)))
        if HX is not None:   # 預設從 http.server 讀 repo 內真檔；注入測試才餵污染版
            self.pg.route("**/data/hexagram_text.json*", lambda r: r.fulfill(status=200, content_type="application/json",
                          body=json.dumps(HX, ensure_ascii=False)))
        self.pg.goto(base + hash_)

    def wait_guide(self):
        # 格／清單由一次 innerHTML 寫入，第一個 .gcell 出現即整批在；不用 wait_for_function（其字串 predicate 走 eval，被本頁 CSP 擋）
        self.pg.wait_for_selector(".gcell[data-kw], #glist", timeout=10000)
        self.pg.wait_for_timeout(150)
        return self

    def wait_card(self):
        self.pg.wait_for_selector("#main .card .chg", timeout=10000)
        self.pg.wait_for_timeout(150)
        return self

    def wait_hold(self):
        # 持股區在首次 render 就有；timeline／hexagram_text 載完會再 render 一次（route 餵本機 fixture，毫秒級），多等一拍再操作
        self.pg.wait_for_selector("#hold", timeout=10000)
        self.pg.wait_for_timeout(400)
        return self

    def text(self, sel="body"):
        return self.pg.inner_text(sel)

    def ev(self, js, arg=None):
        return self.pg.evaluate(js, arg)

    def close(self):
        self.pg.close()


@pytest.fixture
def ctx(browser):
    c = browser.new_context(viewport={"width": 1280, "height": 900})
    yield c
    c.close()


def forbid_hits(p: Page):
    """規則欄／其餘畫面的禁用字命中：去掉動爻句（.expl p.mv）、古文欄（.classic／.gloss）、免責卡（#disc）後掃描。"""
    rest = p.text("body")
    for line in p.ev("[...document.querySelectorAll('.expl p.mv')].map(p=>p.innerText).join('\\n')").split("\n"):
        if line:
            rest = rest.replace(line, "")
    for c in p.ev("[...document.querySelectorAll('.classic,.gloss')].map(p=>p.innerText)"):
        rest = rest.replace(c, "")
    rest = rest.replace(p.text("#disc"), "")
    return [w for w in FORBID + ["轉弱", "轉強"] if w in rest]


def hex_page_expected(kw):
    """卦頁應逐字出現的字串：卦名、卦辭、六爻爻題＋爻辭、白話摘義、乾坤的用九／用六。"""
    hx = HXTEXT[kw]
    out = [hx["name"], hx["judgment"], hx["gloss_judgment"]]
    for ln, gl in zip(hx["lines"], hx["gloss_lines"]):
        out += [ln["title"], ln["text"], gl]
    if hx["extra"]:
        out += [hx["extra"]["title"], hx["extra"]["text"]]
    return out


# ---------- §10 懂卦理 ----------
def test_guide_tab_grid_and_hex_pages(server, ctx):
    """怎麼驗 2 ①②⑦：tab 出現、切換正常；64 格全在、列＝上卦欄＝下卦；點第 1／2／32／63／64 格內容與 hexagram_text.json 逐字相同；
    卦頁零本站分數（bs 值）、零個股代號；console／pageerror 零。"""
    L, T = scen("1_none")
    p = Page(ctx, server, L, T).wait_card()
    tabs = p.ev("[...document.querySelectorAll('#tabs .tab')].map(t=>[t.dataset.tab, t.innerText])")
    assert tabs == [["market", "觀大勢"], ["stock", "診個股"], ["guide", "懂卦理"]]
    p.pg.click('#tabs .tab[data-tab="guide"]'); p.wait_guide()
    assert p.ev("document.querySelector('#tabs .tab.active').dataset.tab") == "guide"
    assert p.ev("location.hash") == "#tab=guide"
    cells = p.ev("[...document.querySelectorAll('.gcell[data-kw]')].map(c=>[Number(c.dataset.kw), c.querySelector('.gn').innerText])")
    assert len(cells) == 64 and sorted(k for k, _ in cells) == list(range(1, 65))
    assert all(NAME[k] == n for k, n in cells)
    # 格順序＝列上卦、欄下卦，兩軸皆 乾兌離震巽坎艮坤：(乾,乾)=1、(坤,坤)=2、(震,巽)=32、(坎,離)=63、(離,坎)=64
    order = [k for k, _ in cells]
    tri = ["乾", "兌", "離", "震", "巽", "坎", "艮", "坤"]
    at = lambda up, lo: order[tri.index(up) * 8 + tri.index(lo)]  # noqa: E731
    assert (at("乾", "乾"), at("坤", "坤"), at("震", "巽"), at("坎", "離"), at("離", "坎")) == (1, 2, 32, 63, 64)
    heads = p.ev("[...document.querySelectorAll('.hexgrid .gh')].map(e=>e.innerText)")
    assert heads == ["上＼下"] + tri + tri
    for kw in (1, 2, 32, 63, 64):
        p.pg.click(f'.gcell[data-kw="{kw}"]'); p.pg.wait_for_selector("#ghex", timeout=5000)
        t = p.text("#ghex")
        missing = [s for s in hex_page_expected(kw) if s not in t]
        assert not missing, (kw, missing)
        assert p.ev("document.querySelector('#ghex h2').innerText") == f"{NAME[kw]}第 {kw} 卦"
        assert p.ev("[...document.querySelectorAll('#ghex .hexfig .yao')].map(y=>y.classList.contains('yang')?'1':'0').join('')") == BITS[kw]
        assert p.ev("location.hash") == f"#tab=guide&kw={kw}"
        assert p.ev("document.querySelector('.gcell.sel').dataset.kw") == str(kw)
        assert "53.4" not in t and "2330" not in t and "台積電" not in t
        assert ("用九" in t) == (kw == 1) and ("用六" in t) == (kw == 2)
    # 回到觀大勢再回來：選中的卦保留、hash 一致
    p.pg.click('#tabs .tab[data-tab="market"]'); p.wait_card()
    assert p.ev("location.hash") == ""
    p.pg.click('#tabs .tab[data-tab="guide"]'); p.wait_guide()
    assert p.ev("location.hash") == "#tab=guide&kw=64" and p.text("#ghex h2") == "火水未濟第 64 卦"
    assert not p.errs, p.errs
    p.close()


def test_guide_list_filter(server, ctx):
    """怎麼驗 2 ③：卦序清單可依卦名／卦序過濾。「乾」的命中數＝hexagram_text.json 卦名含「乾」的卦數（資料實查 1：乾為天）；
    「3」＝卦序 3（水雷屯）1 筆；空字串 64 筆；點列開該卦。"""
    L, T = scen("1_none")
    p = Page(ctx, server, L, T, "#tab=guide").wait_guide()
    p.pg.click('.chip.gview[data-v="list"]'); p.pg.wait_for_selector("#glist .glrow", timeout=5000)
    assert p.ev("document.querySelectorAll('#glist .glrow[data-kw]').length") == 64
    exp_qian = sorted(k for k, n in NAME.items() if "乾" in n)
    p.pg.fill("#gq", "乾"); p.pg.wait_for_timeout(100)
    rows = p.ev("[...document.querySelectorAll('#glist .glrow[data-kw]')].map(r=>Number(r.dataset.kw))")
    assert rows == exp_qian, rows
    exp_tian = sorted(k for k, n in NAME.items() if "天" in n)
    assert exp_tian == [1, 5, 6, 9, 10, 11, 12, 13, 14, 25, 26, 33, 34, 43, 44]
    p.pg.fill("#gq", "天"); p.pg.wait_for_timeout(100)
    assert p.ev("[...document.querySelectorAll('#glist .glrow[data-kw]')].map(r=>Number(r.dataset.kw))") == exp_tian
    p.pg.fill("#gq", " 天 "); p.pg.wait_for_timeout(100)        # 前後空白 trim
    assert p.ev("document.querySelectorAll('#glist .glrow[data-kw]').length") == 15
    p.pg.fill("#gq", "3"); p.pg.wait_for_timeout(100)
    assert p.ev("[...document.querySelectorAll('#glist .glrow[data-kw]')].map(r=>Number(r.dataset.kw))") == [3]
    assert p.ev("document.activeElement && document.activeElement.id") == "gq"      # 過濾只重繪清單、輸入不失焦
    p.pg.fill("#gq", "無此卦"); p.pg.wait_for_timeout(100)
    assert p.ev("document.querySelectorAll('#glist .glrow[data-kw]').length") == 0 and "沒有符合的卦" in p.text("#glist")
    p.pg.fill("#gq", "既濟"); p.pg.wait_for_timeout(100)
    p.pg.click('#glist .glrow[data-kw="63"]'); p.pg.wait_for_selector("#ghex", timeout=5000)
    assert p.text("#ghex h2") == "水火既濟第 63 卦" and p.ev("location.hash") == "#tab=guide&kw=63"
    assert p.ev("document.querySelector('#gq').value") == "既濟"        # 重繪後過濾字串保留
    assert not p.errs, p.errs
    p.close()


@pytest.mark.parametrize("hash_,kw,name", [("#tab=guide&kw=32", 32, "雷風恆"), ("#tab=guide&kw=1", 1, "乾為天"), ("#tab=guide&kw=64", 64, "火水未濟")])
def test_guide_hash_direct_open(server, ctx, hash_, kw, name):
    """怎麼驗 2 ④：`#tab=guide&kw=32` 直開雷風恆（含 1／64 邊界）。"""
    L, T = scen("1_none")
    p = Page(ctx, server, L, T, hash_).wait_guide()
    p.pg.wait_for_selector("#ghex", timeout=5000)
    assert p.text("#ghex h2") == f"{name}第 {kw} 卦" and p.ev("location.hash") == hash_
    assert not p.errs, p.errs
    p.close()


@pytest.mark.parametrize("bad", ["99", "0", "65", "abc", "3.5", "032", "%zz", "1e1"])
def test_guide_hash_bad_kw_silent(server, ctx, bad):
    """怎麼驗 2 ④：非法 kw 靜默退回——無卦頁、hash 改寫成 `#tab=guide`、console 零。"""
    L, T = scen("1_none")
    p = Page(ctx, server, L, T, "#tab=guide&kw=" + bad).wait_guide()
    assert p.ev("document.querySelector('#ghex')") is None
    assert p.ev("location.hash") == "#tab=guide"
    assert p.ev("document.querySelectorAll('.gcell.sel').length") == 0
    assert not p.errs, p.errs
    p.close()


def test_guide_dist_counts(server, ctx):
    """怎麼驗 2 ⑤／G4：每個 horizon 檔數總和（含未定）＝該 horizon 有列的股票數（fixture 手算）；依卦序排列、不列個股、
    不放名單入口；頂部說明句在。"""
    L, T = scen("1_none")
    p = Page(ctx, server, L, T, "#tab=guide").wait_guide()
    exp = {
        "short": ([(1, "乾為天", 1, 1), (2, "坤為地", 1, 0), (43, "澤天夬", 1, 1)], 1, 1, 4),
        "swing": ([(1, "乾為天", 1, 0), (14, "火天大有", 2, 2)], 0, 0, 3),
        "mid": ([(34, "雷天大壯", 3, 3)], 1, 0, 4),
    }
    for h, (rows, undef, undef_pool, n_rows) in exp.items():
        p.pg.click(f'#gdist .chip[data-h="{h}"]'); p.pg.wait_for_timeout(100)
        got = p.ev("[...document.querySelectorAll('#gdistTbl tbody tr')].map(r=>[...r.children].map(c=>c.innerText))")
        assert got[:-1] == [[str(k), n, str(c), str(pl)] for k, n, c, pl in rows], (h, got)
        assert got[-1] == ["—", "未定", str(undef), str(undef_pool)], (h, got)
        assert sum(int(r[2]) for r in got) == n_rows
        assert [int(r[0]) for r in got[:-1]] == sorted(int(r[0]) for r in got[:-1])     # 依卦序、不依檔數
        meta = p.text("#gdist .meta")
        assert f"有列股票 {n_rows} 檔" in meta
    d = p.text("#gdist")
    assert "僅為當日卦象計數，不是選股清單、不代表方向。" in d and LAST in d
    assert all(c not in d for c in ("2330", "2317", "1101", "3008", "台積電", "鴻海"))
    assert not any(w in d for w in ("名單", "查看", "候選"))
    assert p.ev("document.querySelectorAll('#gdist a, #gdist [data-code], #gdist .kwlink').length") == 0
    assert not p.errs, p.errs
    p.close()


def test_guide_dist_without_latest(server, ctx):
    """latest.json 讀不到：懂卦理其餘三段照常（64 格在）、分布段只顯示讀不到。"""
    L, T = scen("1_none")
    p = Page(ctx, server, L, T, "#tab=guide", latest_status=404).wait_guide()
    assert p.ev("document.querySelectorAll('.gcell[data-kw]').length") == 64
    assert "讀不到 data/web/latest.json" in p.text("#gdist") and p.ev("document.querySelector('#gdistTbl')") is None
    assert all("404" in e for e in p.errs), p.errs
    p.close()


def test_guide_facet_tables_from_constants(server, ctx):
    """G3：本站六爻對應兩張表的每格＝index.html 的 FACET 常數（在頁面裡讀回常數逐格比對）；八卦表 8 列順序＝TRI_ORDER。"""
    L, T = scen("1_none")
    p = Page(ctx, server, L, T, "#tab=guide").wait_guide()
    for kind in ("stock", "market"):
        rows = p.ev(f"[...document.querySelectorAll('table.gtable[data-kind=\"{kind}\"] tbody tr')].map(r=>[...r.children].map(c=>c.innerText))")
        exp = p.ev(f"FACET.{kind}.map((f,i)=>[POS[i]+' '+f.name, f.what, f.window, f.yang, f.yin]).reverse()")
        assert rows == exp, kind
    tri = p.ev("[...document.querySelectorAll('#gtri tbody tr')].map(r=>[...r.children].map(c=>c.innerText))")
    exp = p.ev("TRI_ORDER.map(n=>[n, Object.keys(TRI_NAME).find(b=>TRI_NAME[b]===n), TRI_ELEM[n], TRI.stock[n][0], TRI.stock[n][1], TRI.market[n][0], TRI.market[n][1]])")
    assert tri == exp and [r[0] for r in tri] == ["乾", "兌", "離", "震", "巽", "坎", "艮", "坤"]
    assert p.ev("TERMS[3][1]") in p.text("#gfacet .meta")
    assert not p.errs, p.errs
    p.close()


def test_guide_forbidden_words_zero(server, ctx):
    """G7：懂卦理分頁（格＋清單＋卦頁＋四段）說明文字零 S2-5／#53 禁用字（古文欄不受限）。"""
    L, T = scen("1_none")
    p = Page(ctx, server, L, T, "#tab=guide&kw=1").wait_guide()
    p.pg.wait_for_selector("#ghex", timeout=5000)
    assert not forbid_hits(p), forbid_hits(p)
    p.pg.click('.chip.gview[data-v="list"]'); p.pg.wait_for_selector("#glist .glrow", timeout=5000)
    assert not forbid_hits(p), forbid_hits(p)
    assert not p.errs, p.errs
    p.close()


def test_stock_and_market_kwlink_jump(server, ctx):
    """怎麼驗 2 ⑧：診個股／觀大勢卦象卡的卦名可點 → 同頁切到懂卦理該卦（hash 亦更新）；正式卦待補時不掛連結。"""
    L, T = scen("1_none")
    p = Page(ctx, server, L, T, "#tab=stock&code=2330").wait_card()
    assert p.ev("document.querySelector('#main .kwlink').dataset.kw") == "1"
    p.pg.click("#main .kwlink"); p.pg.wait_for_selector("#ghex", timeout=5000)
    assert p.ev("document.querySelector('#tabs .tab.active').dataset.tab") == "guide"
    assert p.text("#ghex h2") == "乾為天第 1 卦" and p.ev("location.hash") == "#tab=guide&kw=1"
    p.close()
    p = Page(ctx, server, L, T, "").wait_card()
    links = p.ev("[...document.querySelectorAll('#main .kwlink')].map(e=>e.dataset.kw)")
    assert links == ["13", "9"]
    p.pg.click('#main .kwlink[data-kw="9"]'); p.pg.wait_for_selector("#ghex", timeout=5000)
    assert p.text("#ghex h2") == "風天小畜第 9 卦" and p.ev("location.hash") == "#tab=guide&kw=9"
    p.close()
    p = Page(ctx, server, L, T, "#tab=stock&code=2317&h=mid").wait_card()      # 正式卦待補（kw null）→ 無連結；暫定卦 43 有
    assert p.ev("[...document.querySelectorAll('#main .kwlink')].map(e=>e.dataset.kw)") == ["43"]
    assert "正式卦待補" in p.text("#main .notice") and "暫定卦" in p.text("#main .hexname")
    assert not p.errs, p.errs
    p.close()


@pytest.mark.parametrize("bad", [99, "abc"])
def test_kwlabel_whitelist_no_link_for_bad_kw(server, ctx, bad):
    """G5：latest 某列 kw 不在 1..64（99／"abc"）→ 卦象卡仍顯示該列的卦名，但不掛 .kwlink、不帶 data-kw；正常列照掛。"""
    L, T = scen("1_none")
    L["stocks"]["2330"]["short"]["kw"] = bad; L["stocks"]["2330"]["short"]["kwp"] = bad
    L["stocks"]["2330"]["short"]["name"] = L["stocks"]["2330"]["short"]["namep"] = "假卦名"
    T["series"]["2330|short"][N - 1] = [bad, "yyyyyy"]
    p = Page(ctx, server, L, T, "#tab=stock&code=2330").wait_card()
    hexname = p.text("#main .hexname")
    assert "假卦名" in hexname and f"第 {bad} 卦" in hexname
    assert p.ev("document.querySelectorAll('#main .kwlink, #main [data-kw]').length") == 0
    p.close()
    p = Page(ctx, server, L, T, "#tab=stock&code=2330&h=swing").wait_card()     # 對照：swing 仍是 14 → 有連結
    assert p.ev("[...document.querySelectorAll('#main .kwlink')].map(e=>e.dataset.kw)") == ["14"]
    assert not p.errs, p.errs
    p.close()


@pytest.mark.parametrize("width", [375, 390, 1280])
def test_widths_no_horizontal_overflow(server, browser, width):
    """怎麼驗 2 ⑥／G6：375／390／1280 × 懂卦理（格／清單／卦頁）＋觀大勢＋診個股 scrollWidth<=innerWidth；console 零。"""
    c = browser.new_context(viewport={"width": width, "height": 900})
    L, T = scen("2_mid")
    try:
        for h in ("#tab=guide&kw=2", "#tab=guide", "", "#tab=stock&code=2330"):
            p = Page(c, server, L, T, h)
            (p.wait_guide() if "guide" in h else p.wait_card())
            if h == "#tab=guide":
                p.pg.click('.chip.gview[data-v="list"]'); p.pg.wait_for_selector("#glist .glrow", timeout=5000)
            sw, iw = p.ev("[document.documentElement.scrollWidth, innerWidth]")
            assert sw <= iw, (width, h, sw, iw)
            if "guide" in h:
                gw = p.ev("document.querySelector('.hexgrid') ? document.querySelector('.hexgrid').scrollWidth : 0")
                assert gw <= iw, (width, gw, iw)
            assert not p.errs, (width, h, p.errs)
            p.close()
    finally:
        c.close()


# ---------- §9 模型換版標示核心情境回歸（移植自 2026-09-26 驗收腳本） ----------
def card_info(p: Page, sel="#main .card"):
    return p.ev("""sel => { const c = document.querySelector(sel); if (!c) return null;
      const note = c.querySelector('.mvnote');
      return { note: note ? note.innerText : null,
        items: [...c.querySelectorAll('.chg li')].map(li => ({ d: li.querySelector('.d').innerText, sw: !!li.querySelector('.badge.mvsw'), t: li.innerText })),
        mv: [...c.querySelectorAll('.expl p.mv')].map(p => p.innerText).join('\\n'),
        mvtext: c.querySelectorAll('.mvtext').length, mvtag: c.querySelectorAll('.mvtag').length,
        lexp: [...c.querySelectorAll('.lexp')].map(x => x.textContent).join('\\n') }; }""", sel)


def test_mv_none_moving_line_and_topline(server, ctx):
    L, T = scen("1_none")
    p = Page(ctx, server, L, T, "#tab=stock&code=2330").wait_card()
    ci = card_info(p)
    assert ci["note"] is None and not any(i["sw"] for i in ci["items"]) and len(ci["items"]) >= 4
    assert "轉強" in ci["mv"] and MV_OFF not in p.text("#main") and ci["mvtext"] == 1 and "今日完成翻轉" in ci["lexp"]
    stats = p.ev("[...document.querySelectorAll('#stats .stat')].map(e=>e.innerText)")
    assert [s.split(" ")[0] for s in stats[:6]] == ["資料日", "data_version", "params_sha", "text_version", "calibrated", "列數"]
    assert stats[6:] == ["模型（上市） p2-score-engine-2.01697576a7b0", "模型（上櫃） p2-score-engine-2.83b5c5dfdb23"]
    assert not forbid_hits(p) and not p.errs, (forbid_hits(p), p.errs)
    p.close()


def test_mv_mid_note_and_tag(server, ctx):
    """§9 P2／P3：中途換版 → 換卦紀錄段上方逐字說明；只有換版日那筆換卦加標「模型換版」；動爻照常。"""
    L, T = scen("2_mid")
    p = Page(ctx, server, L, T, "#tab=stock&code=2330").wait_card()
    ci = card_info(p)
    exp = (f"近 {N} 日含模型換版：{DATES[10]} 起改用新參數版本（params_sha {A} → {B}）。"
           "換版前後的卦象不可直接比較，換卦可能來自模型調整而非市場變化。")
    assert ci["note"] == exp
    assert {i["d"] for i in ci["items"] if i["sw"]} == {DATES[10]}
    assert {i["d"] for i in ci["items"]} >= {DATES[5], DATES[6], DATES[10], DATES[11], LAST}
    assert all(("模型換版" in i["t"]) == i["sw"] for i in ci["items"])
    assert "轉強" in ci["mv"] and ci["mvtext"] == 1
    assert not forbid_hits(p) and not p.errs
    p.close()


def test_mv_today_suppresses_moving_lines(server, ctx):
    """§9 P4：當日換版 → 不出動爻句、改一句不比較；不加強爻辭；觀大勢兩卡皆同。"""
    L, T = scen("3_today")
    p = Page(ctx, server, L, T, "#tab=stock&code=2330").wait_card()
    ci = card_info(p)
    assert ci["mv"] == MV_OFF and "轉強" not in p.text("#main") and ci["mvtext"] == 0 and ci["mvtag"] == 0
    assert "今日完成翻轉" not in ci["lexp"] and {i["d"] for i in ci["items"] if i["sw"]} == {LAST}
    p.close()
    p = Page(ctx, server, L, T, "").wait_card()
    mvs = p.ev("[...document.querySelectorAll('#main .card')].map(c=>[...c.querySelectorAll('.expl p.mv')].map(p=>p.innerText).join('|'))")
    assert mvs == [MV_OFF, MV_OFF] and "轉弱" not in p.text("#main") and p.ev("document.querySelectorAll('#main .mvtext').length") == 0
    assert not p.errs, p.errs
    p.close()


def test_mv_old_file_degrades(server, ctx):
    """§9 W3：舊檔（無 ps／model_version）→ 無說明、無標、動爻照常、頂列只有原六格。"""
    L, T = scen("4_old")
    p = Page(ctx, server, L, T, "#tab=stock&code=2330").wait_card()
    ci = card_info(p)
    assert ci["note"] is None and not any(i["sw"] for i in ci["items"]) and "轉強" in ci["mv"] and ci["mvtext"] == 1
    assert p.ev("document.querySelectorAll('#stats .stat').length") == 6
    assert not p.errs, p.errs
    p.close()


def test_disc_calibration_sentence(server, ctx):
    """§9 P5：免責卡校準句依 calibrated 布林換字；其餘免責語不動；讀不到資料維持未校準句。"""
    L, T = scen("1_none")
    for cal, exp in ((True, CAL_T), (False, CAL_F), ("true", CAL_F)):
        L["calibrated"] = cal
        p = Page(ctx, server, L, T).wait_card(); d = p.text("#disc"); p.close()
        assert exp in d and all(w in d for w in DISC_REST), (cal, d)
        if cal is True:
            assert "參數未校準" not in d and "calibrated=false" not in d
    p = Page(ctx, server, L, T, "", latest_status=404)
    p.pg.wait_for_selector("#main .err", timeout=10000); d = p.text("#disc"); p.close()
    assert CAL_F in d and all(w in d for w in DISC_REST)


X_INJ = '<img src=x onerror="window.__xss=1">'


def injected_hx():
    """污染版 hexagram_text.json：第 1 卦的 name／judgment／gloss_judgment／lines[0].title＋text／gloss_lines[0]／extra 全帶注入字串。
    lines[0].title 保留「九」讓 hexBits 仍能反推（否則該卦整個被索引略過，就測不到卦頁）。"""
    doc = json.loads((ROOT / "data" / "hexagram_text.json").read_text(encoding="utf-8"))
    h = doc["hexagrams"][0]
    assert h["king_wen"] == 1
    h["name"] = X_INJ; h["judgment"] = X_INJ + "元亨"; h["gloss_judgment"] = X_INJ + "摘義"
    h["lines"][0]["title"] = X_INJ + "初九"; h["lines"][0]["text"] = X_INJ + "潛龍"; h["gloss_lines"][0] = X_INJ + "摘"
    h["extra"] = {"title": X_INJ + "用九", "text": X_INJ + "見羣龍"}
    return doc


def test_injection_escaped_everywhere(server, ctx):
    """§9 P6／§10 G7：ps／model_version／股名／卦名帶 `<img onerror>` → 不執行、以字面顯示（診個股頂列與說明句、懂卦理今日卦分布卦名欄）；
    hexagram_text.json 七個欄位帶注入 → 8×8 格、卦序清單、卦頁全部字面顯示、零 img、__xss 未觸發。"""
    L, T = scen("6_inject")
    X = X_INJ
    p = Page(ctx, server, L, T, "#tab=stock&code=2330", HX=injected_hx()).wait_card()
    assert p.ev("window.__xss") is None and p.ev("document.querySelectorAll('#main img, #stats img').length") == 0
    assert X in p.text("body")
    p.pg.click('#tabs .tab[data-tab="guide"]'); p.wait_guide()
    t = p.text("#gdist")
    assert X in t and p.ev("window.__xss") is None and p.ev("document.querySelectorAll('#main img').length") == 0
    # 格：第 1 格卦名＝字面；清單：列文字＝字面；卦頁：h2／卦辭／摘義／初爻題與爻辭／初爻摘義／用九 全部字面
    assert p.ev("document.querySelector('.gcell[data-kw=\"1\"] .gn').innerText") == X
    p.pg.click('.chip.gview[data-v="list"]'); p.pg.wait_for_selector("#glist .glrow", timeout=5000)
    assert p.ev("document.querySelector('#glist .glrow[data-kw=\"1\"] .gn').innerText") == X
    p.pg.click('#glist .glrow[data-kw="1"]'); p.pg.wait_for_selector("#ghex", timeout=5000)
    hx = p.text("#ghex")
    for frag in (X + "元亨", X + "摘義", X + "初九", X + "潛龍", X + "摘", X + "用九", X + "見羣龍"):
        assert frag in hx, frag
    assert p.ev("document.querySelector('#ghex h2').innerText") == X + "第 1 卦"
    assert p.ev("window.__xss") is None and p.ev("document.querySelectorAll('#main img, #stats img').length") == 0
    assert p.ev("document.querySelectorAll('#ghex .gcell, #ghex script').length") == 0
    assert not p.errs, p.errs
    p.close()


def test_injection_guard_alive_mutation(server, ctx):
    """守門本身要活著：餵一份「guideHexHtml 的 h2 不過 esc」的 index.html（page.route），同一組污染 hexagram_text.json 下注入**會**執行
    （__xss 為 1、#ghex 內出現 img）——證明上一支測試不是因為注入根本沒渲染才綠。"""
    src = (ROOT / "index.html").read_text(encoding="utf-8")
    needle = '<h2>${esc(hx.name)}<span class="mk">第 ${esc(kw)} 卦</span></h2>'
    assert src.count(needle) == 1
    mutated = src.replace(needle, '<h2>${hx.name}<span class="mk">第 ${esc(kw)} 卦</span></h2>')
    L, T = scen("1_none")
    pg = ctx.new_page()
    pg.route("**/index.html*", lambda r: r.fulfill(status=200, content_type="text/html; charset=utf-8", body=mutated))
    pg.route("https://api.github.com/**", lambda r: r.abort())
    pg.route("**/data/web/latest.json*", lambda r: r.fulfill(status=200, content_type="application/json", body=json.dumps(L, ensure_ascii=False)))
    pg.route("**/data/web/timeline.json*", lambda r: r.fulfill(status=200, content_type="application/json", body=json.dumps(T, ensure_ascii=False)))
    pg.route("**/data/hexagram_text.json*", lambda r: r.fulfill(status=200, content_type="application/json", body=json.dumps(injected_hx(), ensure_ascii=False)))
    pg.goto(server + "#tab=guide&kw=1"); pg.wait_for_selector("#ghex", timeout=10000); pg.wait_for_timeout(300)
    assert pg.evaluate("window.__xss") == 1 and pg.evaluate("document.querySelectorAll('#ghex h2 img').length") == 1
    pg.close()


# ---------- §11 我的持股（唯讀 pm_holdings；docs/P4-PREVIEW.md §11、驗收條件 H1–H9） ----------
HOLD_EMPTY = "尚無持股（於「盤後分析」站的持股診斷設定後，同一瀏覽器此處自動顯示）。"
HOLD_NOTE = "此區代號來自「盤後分析」站的持股診斷，本站唯讀；增刪請至該站管理。持股清單只存本機瀏覽器，持股代號不進任何網路請求。"
HOLD_SRC = "來自持股診斷（唯讀）"
HOLD_POOL = "未達流動性門檻（60 日成交值 <3,000 萬）"
HOLD_EXTRA_FORBID = ["名單", "查看", "候選", "看多", "看空", "買", "賣", "機率", "勝率", "轉弱", "轉強"]
# 正常清單：刻意不依卦序、不依代號序；含 sh／cost（不得顯示）、缺 sh 的筆、in_rank_pool=0（2317）、缺 swing（3008）、不在分數檔（9999）、
# 短線正式卦待補（1101）
HOLD_RAW = [{"c": "3008", "sh": 8848, "cost": 123.45}, {"c": "2330", "sh": None, "cost": None}, {"c": "2317"},
            {"c": "9999", "sh": 777, "cost": 9.5}, {"c": "1101"}]
HOLD_CODES = [h["c"] for h in HOLD_RAW]
HOLD_SECRETS = ["8848", "123.45", "777", "9.5", "53.4", "61.8"]   # sh／cost 值＋2330 短線 bs／ti（皆不得出現在持股區）


def hold_init(raw) -> str:
    """init_script：先寫入 pm_holdings（raw 為 list → JSON；str → 原字串，供壞 JSON 情境），再裝 Storage 寫入 spy
    （只記 localStorage 的 setItem／removeItem／clear，不記 sessionStorage——loadSiteVer 本來就會寫 sessionStorage）。"""
    text = raw if isinstance(raw, str) else json.dumps(raw, ensure_ascii=False)
    return f"""
      localStorage.setItem("pm_holdings", {json.dumps(text, ensure_ascii=False)});
      window.__lsw = [];
      for (const k of ["setItem", "removeItem", "clear"]) {{
        const orig = Storage.prototype[k];
        Storage.prototype[k] = function(...a) {{ if (this === window.localStorage) window.__lsw.push(k); return orig.apply(this, a); }};
      }}
    """


def hold_rows(p: Page):
    return p.ev("""[...document.querySelectorAll('#holdTbl tbody tr.hrow')].map(r => ({
      code: r.dataset.code, cells: [...r.children].map(c => c.innerText),
      bits: [...r.querySelectorAll('.hexfig .yao')].map(y => y.classList.contains('yang') ? '1' : y.classList.contains('yin') ? '0' : '-').join(''),
      kw: [...r.querySelectorAll('.kwlink')].map(k => k.dataset.kw) }))""")


def test_hold_empty_block_and_position(server, ctx):
    """H1／H6 ①空清單：無 localStorage → 區塊在（標題＋唯讀 badge＋一句提示）、無表格、無按鈕／輸入框；位置＝輸入框之下、查詢結果之上；
    不新增 tab、不新增 hash 鍵；console 零。"""
    L, T = scen("1_none")
    p = Page(ctx, server, L, T, "#tab=stock&code=2330").wait_card()
    assert p.ev("[...document.querySelectorAll('#tabs .tab')].length") == 3
    t = p.text("#hold")
    assert HOLD_EMPTY in t and HOLD_SRC in t and "我的持股" in t and HOLD_NOTE not in t
    assert p.ev("document.querySelector('#holdTbl')") is None
    assert p.ev("document.querySelectorAll('#hold button, #hold input, #hold .btn').length") == 0
    # 順序：.search → #hold → #stockCard（compareDocumentPosition 4＝FOLLOWING）
    assert p.ev("document.querySelector('#main .search').compareDocumentPosition(document.getElementById('hold')) & 4") == 4
    assert p.ev("document.getElementById('hold').compareDocumentPosition(document.getElementById('stockCard')) & 4") == 4
    assert p.ev("location.hash") == "#tab=stock&code=2330"
    assert p.ev("document.querySelectorAll('#main .chips').length") == 1
    assert not p.errs, p.errs
    p.close()


def test_hold_rows_three_horizons(server, ctx):
    """H5 ②正常清單：列順序＝清單原順序（不依卦序）；每列＝代號、股名、六爻圖、正式卦名（可點）；in_rank_pool=0 標未達流動性門檻；
    不在分數檔標「不在最新分數檔（date）內」；缺該期間標「該期間無列」；正式卦待補顯暫定卦；三期間切換同一組 chips；
    sh／cost／bs 值不出現；無按鈕。"""
    L, T = scen("1_none")
    p = Page(ctx, server, L, T, "#tab=stock", init_script=hold_init(HOLD_RAW)).wait_hold()
    assert p.ev("document.querySelectorAll('#main .chips').length") == 1
    t = p.text("#hold")
    assert HOLD_NOTE in t and HOLD_SRC in t and "期間 短線" in t and HOLD_EMPTY not in t
    assert p.ev("[...document.querySelectorAll('#holdTbl thead th')].map(e=>e.innerText)") == ["代號", "股名", "六爻", "正式卦", "備註"]
    rows = hold_rows(p)
    assert [r["code"] for r in rows] == HOLD_CODES
    by = {r["code"]: r for r in rows}
    assert by["3008"]["cells"][1] == "大立光" and by["3008"]["cells"][3] == "澤天夬第 43 卦" and by["3008"]["bits"] == "111110" and by["3008"]["kw"] == ["43"]
    assert by["2330"]["cells"][1] == "台積電" and by["2330"]["cells"][3] == "乾為天第 1 卦" and by["2330"]["bits"] == "111111" and by["2330"]["cells"][4] == ""
    assert by["2317"]["cells"][3] == "坤為地第 2 卦" and by["2317"]["cells"][4] == HOLD_POOL and by["2317"]["bits"] == "000000"
    assert by["9999"]["cells"][1] == "" and by["9999"]["cells"][3] == "—" and by["9999"]["cells"][4] == f"不在最新分數檔（{LAST}）內" and by["9999"]["bits"] == "" and by["9999"]["kw"] == []
    assert "正式卦待補" in by["1101"]["cells"][3] and "暫定卦：澤天夬第 43 卦" in by["1101"]["cells"][3] and by["1101"]["bits"] == "11111-" and by["1101"]["kw"] == ["43"]
    assert not any(x in t for x in HOLD_SECRETS), t
    assert p.ev("document.querySelectorAll('#hold button, #hold input, #hold .btn').length") == 0
    # 波段：3008 缺 swing → 該期間無列；其餘照 fixture（2330／1101 火天大有 14、2317 乾為天 1）
    p.pg.click('#main .chip[data-h="swing"]'); p.pg.wait_for_timeout(150)
    rows = hold_rows(p); by = {r["code"]: r for r in rows}
    assert [r["code"] for r in rows] == HOLD_CODES and "期間 波段" in p.text("#hold")
    assert by["3008"]["cells"][3] == "—" and by["3008"]["cells"][4] == "該期間無列" and by["3008"]["bits"] == "" and by["3008"]["kw"] == []
    assert by["2330"]["cells"][3] == "火天大有第 14 卦" and by["2330"]["bits"] == BITS[14] and by["1101"]["kw"] == ["14"]
    assert by["2317"]["cells"][3] == "乾為天第 1 卦" and by["2317"]["cells"][4] == HOLD_POOL
    assert p.ev("location.hash") == "#tab=stock&h=swing"
    # 中期：2317 正式卦待補（暫定 43）；2330／1101／3008 雷天大壯 34
    p.pg.click('#main .chip[data-h="mid"]'); p.pg.wait_for_timeout(150)
    rows = hold_rows(p); by = {r["code"]: r for r in rows}
    assert [r["code"] for r in rows] == HOLD_CODES
    assert "正式卦待補" in by["2317"]["cells"][3] and by["2317"]["kw"] == ["43"] and HOLD_POOL in by["2317"]["cells"][4]
    assert all(by[c]["cells"][3] == "雷天大壯第 34 卦" for c in ("2330", "1101", "3008"))
    assert not any(x in p.text("#hold") for x in HOLD_SECRETS)
    assert not p.errs, p.errs
    p.close()


@pytest.mark.parametrize("raw", ["{bad", '{"c":"2330"}', "null", "7", '"2330"', "[]"])
def test_hold_bad_raw_shows_empty(server, ctx, raw):
    """H3 ③壞 JSON ④非陣列（物件／null／數字／字串）／空陣列 → 空清單一句提示、無表格、console 零、不寫回。"""
    L, T = scen("1_none")
    p = Page(ctx, server, L, T, "#tab=stock", init_script=hold_init(raw)).wait_hold()
    assert HOLD_EMPTY in p.text("#hold") and p.ev("document.querySelector('#holdTbl')") is None
    assert p.ev("window.__lsw") == [] and p.ev('localStorage.getItem("pm_holdings")') == raw
    assert not p.errs, p.errs
    p.close()


def test_hold_bad_entries_skipped(server, ctx):
    """H3 ⑤壞筆：c 非字串、7 碼、3 碼、`<img onerror>`、null、缺 c 靜默略過；小寫／空白正規化後命中；重複只留一筆；注入不執行、不寫回。"""
    raw = [{"c": 1234}, {"c": "1234567"}, {"c": "123"}, {"c": X_INJ}, None, {"sh": 5}, {"c": " 2330 "}, {"c": "00631l", "sh": 3},
           {"c": "2330"}, {"c": "2317 "}, {"c": ""}]
    L, T = scen("1_none")
    p = Page(ctx, server, L, T, "#tab=stock", init_script=hold_init(raw)).wait_hold()
    rows = hold_rows(p)
    assert [r["code"] for r in rows] == ["2330", "00631L", "2317"]
    by = {r["code"]: r for r in rows}
    assert by["00631L"]["cells"][4] == f"不在最新分數檔（{LAST}）內" and by["2330"]["cells"][3] == "乾為天第 1 卦"
    t = p.text("#hold")
    assert X_INJ not in t and "1234567" not in t and p.ev("window.__xss") is None and p.ev("document.querySelectorAll('#main img').length") == 0
    assert p.ev("window.__lsw") == [] and json.loads(p.ev('localStorage.getItem("pm_holdings")')) == raw   # 壞筆不寫回
    assert not p.errs, p.errs
    p.close()


def test_hold_click_row_single_code_hash(server, ctx):
    """H4 ⑥點列 → 查詢該單一代號：hash 只含該代號（其餘持股代號不進 hash）、卡片顯示該股；列內卦名連結仍跳懂卦理；Enter 鍵等同點擊。"""
    L, T = scen("1_none")
    p = Page(ctx, server, L, T, "#tab=stock", init_script=hold_init(HOLD_RAW)).wait_hold()
    assert p.ev("location.hash") == "#tab=stock" and p.ev("document.querySelector('#stockCard')") is None
    p.pg.click('tr.hrow[data-code="2317"] td:nth-child(2)'); p.pg.wait_for_selector("#stockCard .chg", timeout=5000)
    assert p.ev("location.hash") == "#tab=stock&code=2317"
    assert p.text("#stockCard h2").startswith("2317 鴻海") and p.ev("document.querySelector('#code').value") == "2317"
    assert not any(c in p.ev("location.hash") for c in HOLD_CODES if c != "2317")
    assert [r["code"] for r in hold_rows(p)] == HOLD_CODES     # 查詢後持股表仍在、順序不變
    # 9999（不在分數檔）：點列 → 既有「查無代號」錯誤句
    p.pg.click('tr.hrow[data-code="9999"] td:nth-child(1)'); p.pg.wait_for_timeout(200)
    assert p.ev("location.hash") == "#tab=stock&code=9999" and "查無代號 9999" in p.text("#main .err")
    # 鍵盤：列聚焦後 Enter
    p.pg.focus('tr.hrow[data-code="2330"]'); p.pg.keyboard.press("Enter"); p.pg.wait_for_selector("#stockCard .chg", timeout=5000)
    assert p.ev("location.hash") == "#tab=stock&code=2330" and p.text("#stockCard h2").startswith("2330 台積電")
    # 列內卦名連結 → 懂卦理該卦（hash 只有 kw，不帶任何持股代號）
    p.pg.click('tr.hrow[data-code="3008"] .kwlink'); p.pg.wait_for_selector("#ghex", timeout=5000)
    assert p.text("#ghex h2") == "澤天夬第 43 卦" and p.ev("location.hash") == "#tab=guide&kw=43"
    assert not p.errs, p.errs
    p.close()


def test_hold_codes_never_in_requests_and_no_storage_writes(server, ctx):
    """H2／H4 ⑦⑧：走完載入→三期間→點列→跳懂卦理→回診個股，所有請求的 URL（path＋query）／headers／body 不含任何持股代號；
    localStorage 的 setItem／removeItem／clear 呼叫 0 次（spy）；spy 本身活著。"""
    L, T = scen("1_none")
    p = Page(ctx, server, L, T, "#tab=stock", init_script=hold_init(HOLD_RAW)).wait_hold()
    for h in ("swing", "mid", "short"):
        p.pg.click(f'#main .chip[data-h="{h}"]'); p.pg.wait_for_timeout(100)
    p.pg.click('tr.hrow[data-code="2317"] td:nth-child(1)'); p.pg.wait_for_selector("#stockCard .chg", timeout=5000)
    p.pg.click('tr.hrow[data-code="2330"] .kwlink'); p.pg.wait_for_selector("#ghex", timeout=5000)
    p.pg.click('#tabs .tab[data-tab="stock"]'); p.pg.wait_for_selector("#stockCard .chg", timeout=5000)
    p.pg.click('#tabs .tab[data-tab="market"]'); p.wait_card()
    from urllib.parse import urlsplit
    assert len(p.reqs) >= 4, p.reqs       # index.html／latest／timeline／hexagram_text（api.github 由 route 回應，亦計入）
    seen = set()
    for url, headers, body in p.reqs:
        u = urlsplit(url); seen.add(u.path.rsplit("/", 1)[-1])
        blob = u.path + "?" + u.query + "|" + json.dumps(headers, ensure_ascii=False) + "|" + (body or "")
        # netloc（127.0.0.1:<隨機 port>）排除：port 可能恰好含 4 位數字，與持股代號無關
        assert not any(c in blob for c in HOLD_CODES), (url, headers, body)
    assert {"latest.json", "timeline.json", "hexagram_text.json"} <= seen, seen
    assert p.ev("window.__lsw") == []
    # spy 活著：測試端自己呼叫一次 setItem 應被記到
    p.ev('localStorage.setItem("__probe", "1")'); assert p.ev("window.__lsw") == ["setItem"]
    p.ev('localStorage.removeItem("__probe")'); assert p.ev("window.__lsw") == ["setItem", "removeItem"]
    assert not p.errs, p.errs
    p.close()


@pytest.mark.parametrize("width", [375, 390, 1280])
def test_hold_widths_no_horizontal_overflow(server, browser, width):
    """H8 ⑨：有持股清單時，診個股（無查詢／有查詢）三寬度 scrollWidth<=innerWidth；表格包 .tblwrap；console 零。"""
    c = browser.new_context(viewport={"width": width, "height": 900})
    L, T = scen("1_none")
    try:
        for h in ("#tab=stock", "#tab=stock&code=2330&h=mid"):
            p = Page(c, server, L, T, h, init_script=hold_init(HOLD_RAW)).wait_hold()
            if "code=" in h:
                p.wait_card()
            assert p.ev("document.querySelector('#holdTbl').closest('.tblwrap') !== null")
            assert p.ev("document.querySelectorAll('#main .chips').length") == 1, (width, h)   # 有清單＋有查詢：期間 chips 仍只一組
            sw, iw = p.ev("[document.documentElement.scrollWidth, innerWidth]")
            assert sw <= iw, (width, h, sw, iw)
            assert len(hold_rows(p)) == 5 and not p.errs, (width, h, p.errs)
            p.close()
    finally:
        c.close()


def test_hold_injection_escaped(server, ctx):
    """H7 注入：scen 6_inject 的污染股名（2330 names）與卦名（3008 短線 name／namep）進持股區 → 不執行、以字面顯示、#hold 零 img。"""
    L, T = scen("6_inject")
    p = Page(ctx, server, L, T, "#tab=stock", init_script=hold_init([{"c": "2330"}, {"c": "3008"}])).wait_hold()
    rows = hold_rows(p); by = {r["code"]: r for r in rows}
    assert [r["code"] for r in rows] == ["2330", "3008"]
    assert by["2330"]["cells"][1] == X_INJ                      # 股名欄字面
    assert by["3008"]["cells"][3].startswith(X_INJ)             # 卦名欄字面（kwLabel 內 esc）
    assert p.ev("document.querySelectorAll('#hold img, #main img').length") == 0 and p.ev("window.__xss") is None
    assert p.ev("window.__lsw") == []
    assert not p.errs, p.errs
    p.close()


def test_hold_forbidden_words_zero(server, ctx):
    """H7 ⑩：持股區可見文字零 FORBID、零「轉弱／轉強」、零「名單／查看／候選／看多／看空／買／賣／機率／勝率」；整頁規則欄亦零禁用字。"""
    L, T = scen("1_none")
    p = Page(ctx, server, L, T, "#tab=stock&code=2330", init_script=hold_init(HOLD_RAW)).wait_hold().wait_card()
    t = p.text("#hold")
    assert not [w for w in FORBID + HOLD_EXTRA_FORBID if w in t], [w for w in FORBID + HOLD_EXTRA_FORBID if w in t]
    assert not forbid_hits(p), forbid_hits(p)
    assert p.ev("document.querySelectorAll('#main .chips').length") == 1      # 有清單＋有查詢：chips 只一組
    p.pg.click('#main .chip[data-h="mid"]'); p.pg.wait_for_timeout(150)
    t = p.text("#hold")
    assert not [w for w in FORBID + HOLD_EXTRA_FORBID if w in t]
    assert not p.errs, p.errs
    p.close()
