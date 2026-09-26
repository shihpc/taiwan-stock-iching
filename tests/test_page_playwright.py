"""`index.html` 頁面 DOM 接線層的自動守門（Playwright；docs/P4-PREVIEW.md §10「怎麼驗 2」①–⑧＋§9 核心情境回歸）。

- playwright（Python 套件）或 Chromium 不可用時整個模組 `pytest.skip`（CI 沒裝就跳過、不假綠也不紅）。
- 本機 `http.server` 從 repo 根服務 `index.html`／`data/hexagram_text.json`（repo 內檔案），`latest.json`／`timeline.json`／
  `api.github.com` 全部由 `page.route` 餵測試內建構的 fixture——**不依賴網路**、不依賴 `data/web/` 現況。
- fixture 手算值（§10 G4 ⑤）：short 4 檔＝乾為天 1（排名池 1）＋坤為地 1（0）＋澤天夬 1（1）＋未定 1（1）；
  swing 3 檔（3008 無 swing）＝火天大有 2（2）＋乾為天 1（0）；mid 4 檔＝雷天大壯 3（3）＋未定 1（0）。
- 沙箱慣例：Chromium 在 `/opt/pw-browsers` 時自動帶 `PLAYWRIGHT_BROWSERS_PATH`（環境已設則不動）。
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

pw_api = pytest.importorskip("playwright.sync_api", reason="playwright 未安裝，略過頁面 DOM 測試")

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
    def __init__(self, ctx, base, L, T, hash_="", latest_status=200):
        self.errs = []
        self.pg = ctx.new_page()
        self.pg.on("pageerror", lambda e: self.errs.append("pageerror " + str(e)))
        self.pg.on("console", lambda m: self.errs.append("console " + m.text) if m.type == "error" else None)
        self.pg.route("https://api.github.com/**", lambda r: r.fulfill(status=200, content_type="application/json",
                      body=json.dumps({"sha": "abcdef1234567", "commit": {"committer": {"date": "2026-09-26T00:00:00Z"}}})))
        self.pg.route("**/data/web/latest.json*", lambda r: r.fulfill(status=latest_status, content_type="application/json",
                      body=json.dumps(L, ensure_ascii=False) if latest_status == 200 else "nope"))
        self.pg.route("**/data/web/timeline.json*", lambda r: r.fulfill(status=200, content_type="application/json",
                      body=json.dumps(T, ensure_ascii=False)))
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


def test_injection_escaped_everywhere(server, ctx):
    """§9 P6／§10 G7：ps／model_version／股名／卦名帶 `<img onerror>` → 不執行、以字面顯示（診個股頂列與說明句、懂卦理今日卦分布卦名欄）。"""
    L, T = scen("6_inject")
    X = '<img src=x onerror="window.__xss=1">'
    p = Page(ctx, server, L, T, "#tab=stock&code=2330").wait_card()
    assert p.ev("window.__xss") is None and p.ev("document.querySelectorAll('#main img, #stats img').length") == 0
    assert X in p.text("body")
    p.pg.click('#tabs .tab[data-tab="guide"]'); p.wait_guide()
    t = p.text("#gdist")
    assert X in t and p.ev("window.__xss") is None and p.ev("document.querySelectorAll('#main img').length") == 0
    assert not p.errs, p.errs
    p.close()
