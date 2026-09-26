"""`index.html` 說明層純函式（`explainHex`／`explainLine`）與靜態文案表（`FACET`／`TRI`／`TERMS`）的案例測試
（`docs/P4-PREVIEW.md` §6 F2）：以 subprocess 呼叫 node 跑 `tests/explain_cases.mjs`，該檔從 `index.html` 抽宣告切片在 vm 沙箱執行、
斷言輸出逐字＝預期句（51 案例＋19 結構斷言；25–34 與 §9 那 3 條結構斷言為模型換版標示、35–47 與 §10 那 4 條為懂卦理、
48–51 與 §11 那 4 條為「我的持股」唯讀 pm_holdings）。node 不存在時 skip（CI runner 有 node；本地沒有就跳過，不假綠）。免 token 免網路。
另有一支不依賴 node 的靜態測試 `test_holdings_storage_read_only`（§11 H2：全檔對本機儲存只准 getItem）。
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
MJS = ROOT / "tests" / "explain_cases.mjs"
NODE = shutil.which("node")


@pytest.mark.skipif(NODE is None, reason="node 不存在，無法跑 index.html 的 JS 案例")
def test_explain_cases_via_node():
    r = subprocess.run([NODE, str(MJS), str(ROOT / "index.html")], capture_output=True, text=True, timeout=120, encoding="utf-8")
    assert r.returncode == 0, f"stdout:\n{r.stdout}\nstderr:\n{r.stderr}"
    assert "FAIL 0 ===" in r.stdout and "FAIL " not in r.stdout.replace("FAIL 0 ===", ""), r.stdout
    assert "51 案例" in r.stdout


@pytest.mark.skipif(NODE is None, reason="node 不存在")
def test_explain_cases_detects_mutation(tmp_path: Path):
    """守門本身要活著：把 index.html 的「轉弱」改字後案例應紅（防止抽取失敗卻假綠）。"""
    mutated = tmp_path / "index.html"
    mutated.write_text((ROOT / "index.html").read_text(encoding="utf-8").replace("陽→陰（轉弱）", "陽→陰（轉差）"), encoding="utf-8")
    r = subprocess.run([NODE, str(MJS), str(mutated)], capture_output=True, text=True, timeout=120, encoding="utf-8")
    assert r.returncode == 1 and "FAIL 5 動爻 陽→陰" in r.stdout, r.stdout


@pytest.mark.skipif(NODE is None, reason="node 不存在")
def test_explain_cases_detects_mv_off_mutation(tmp_path: Path):
    """§9 P4 守門要活著：拿掉 explainHex 的「模型換版不比較動爻」分支，案例 31 應紅（動爻句又冒出來）。"""
    src = (ROOT / "index.html").read_text(encoding="utf-8")
    needle = '  if (mvOff) return hex + "\\n" + MV_OFF_TXT;\n'
    assert src.count(needle) == 1
    mutated = tmp_path / "index.html"
    mutated.write_text(src.replace(needle, ""), encoding="utf-8")
    r = subprocess.run([NODE, str(MJS), str(mutated)], capture_output=True, text=True, timeout=120, encoding="utf-8")
    assert r.returncode == 1 and "FAIL 31 P4 動爻抑制" in r.stdout, r.stdout


@pytest.mark.skipif(NODE is None, reason="node 不存在")
def test_explain_cases_detects_model_shift_at_mutation(tmp_path: Path):
    """§9 P3 守門要活著：把 modelShiftAt 改成恆回 false（換卦紀錄永遠不加「模型換版」標），案例 34 應紅。"""
    src = (ROOT / "index.html").read_text(encoding="utf-8")
    needle = "  return psDiffers(ps[i], ps[i - 1]);\n"
    assert src.count(needle) == 1
    mutated = tmp_path / "index.html"
    mutated.write_text(src.replace(needle, "  return false;\n"), encoding="utf-8")
    r = subprocess.run([NODE, str(MJS), str(mutated)], capture_output=True, text=True, timeout=120, encoding="utf-8")
    assert r.returncode == 1 and "FAIL 34 P3 modelShiftAt" in r.stdout, r.stdout


def _mutate(tmp_path: Path, needle: str, repl: str) -> subprocess.CompletedProcess:
    src = (ROOT / "index.html").read_text(encoding="utf-8")
    assert src.count(needle) == 1, needle
    mutated = tmp_path / "index.html"
    mutated.write_text(src.replace(needle, repl), encoding="utf-8")
    return subprocess.run([NODE, str(MJS), str(mutated)], capture_output=True, text=True, timeout=120, encoding="utf-8")


@pytest.mark.skipif(NODE is None, reason="node 不存在")
def test_guide_detects_dist_off_by_one(tmp_path: Path):
    """§10 G4 守門要活著：hexDist 第一次計到某卦時多算 1（off-by-one），案例 38 應紅。"""
    r = _mutate(tmp_path, "      d.count[kw] = (d.count[kw] || 0) + 1;\n", "      d.count[kw] = (d.count[kw] || 1) + 1;\n")
    assert r.returncode == 1 and "FAIL 38 G4 hexDist" in r.stdout, r.stdout


@pytest.mark.skipif(NODE is None, reason="node 不存在")
def test_guide_detects_hash_whitelist_relaxed(tmp_path: Path):
    """§10 G5 守門要活著：parseKw 上限 64 放寬成 99（hash kw=99 不再退回），案例 37 應紅。"""
    r = _mutate(tmp_path, "  return (n >= 1 && n <= 64) ? n : null;\n", "  return (n >= 1 && n <= 99) ? n : null;\n")
    assert r.returncode == 1 and "FAIL 37 G5 parseKw" in r.stdout, r.stdout


@pytest.mark.skipif(NODE is None, reason="node 不存在")
def test_guide_detects_facet_table_copied_string(tmp_path: Path):
    """§10 G3 守門要活著：六爻對應表的「看什麼」欄改成抄一份字面字串（不讀 FACET），哨兵沙箱案例 42 應紅。"""
    r = _mutate(tmp_path, "<td>${esc(f.what)}</td>", "<td>月營收年增、營收加速度</td>")
    assert r.returncode == 1 and "FAIL 42 G3 guideFacetRows" in r.stdout, r.stdout


@pytest.mark.skipif(NODE is None, reason="node 不存在")
def test_guide_detects_tri_order_from_object_keys(tmp_path: Path):
    """§10 G5 守門要活著：TRI_ORDER 改成 Object.keys(TRI_NAME) 推導（JS 會把整數字串鍵排前面 → 震離兌乾…），
    案例 40／結構斷言應紅或抽取失敗——兩者都是非 0 退出、且不得印出 FAIL 0。"""
    r = _mutate(tmp_path, 'const TRI_ORDER = ["乾", "兌", "離", "震", "巽", "坎", "艮", "坤"];\n',
                "const TRI_ORDER = Object.keys(TRI_NAME).map(b => TRI_NAME[b]);\n")
    assert r.returncode != 0 and "FAIL 0 ===" not in r.stdout, r.stdout + r.stderr


@pytest.mark.skipif(NODE is None, reason="node 不存在")
def test_guide_detects_kwlabel_without_whitelist(tmp_path: Path):
    """§10 G5 守門要活著：kwLabel 不過 parseKw（任何 kw 都掛 .kwlink），案例 45 應紅。"""
    r = _mutate(tmp_path, "  const k = parseKw(kw);\n  const link = k !== null", "  const k = kw;\n  const link = k !== null")
    assert r.returncode == 1 and "FAIL 45 G5 kwLabel" in r.stdout, r.stdout


@pytest.mark.skipif(NODE is None, reason="node 不存在")
def test_guide_detects_list_filter_without_trim(tmp_path: Path):
    """§10 G5 守門要活著：清單過濾不 trim（\" 天 \" 得 0 筆），案例 46 應紅。"""
    r = _mutate(tmp_path, '  const q = String(state.gq || "").trim();\n', '  const q = String(state.gq || "");\n')
    assert r.returncode == 1 and "FAIL 46 G5 guideListRows" in r.stdout, r.stdout


@pytest.mark.skipif(NODE is None, reason="node 不存在")
def test_guide_detects_list_filter_startswith(tmp_path: Path):
    """§10 G5 守門要活著：清單過濾改成「開頭」（\"天\" 只剩 7 筆），案例 46／47 應紅。"""
    r = _mutate(tmp_path, "x.name.includes(q)", "x.name.startsWith(q)")
    assert r.returncode == 1 and "FAIL 46 G5 guideListRows" in r.stdout and "FAIL 47 G5 guideListRows" in r.stdout, r.stdout


# ---------- §11 我的持股（docs/P4-PREVIEW.md §11）：突變守門＋靜態守門 ----------
@pytest.mark.skipif(NODE is None, reason="node 不存在")
def test_hold_detects_code_re_filter_removed(tmp_path: Path):
    """§11 H3 守門要活著：holdingsCodes 拿掉 CODE_RE 過濾（7 碼／3 碼／注入字串照收），案例 48 應紅。"""
    r = _mutate(tmp_path, "    if (!CODE_RE.test(c) || seen.has(c)) continue;\n", "    if (seen.has(c)) continue;\n")
    assert r.returncode == 1 and "FAIL 48 H3 holdingsCodes" in r.stdout, r.stdout


@pytest.mark.skipif(NODE is None, reason="node 不存在")
def test_hold_detects_sorted_by_kw(tmp_path: Path):
    """§11 H5 守門要活著：holdHtml 把清單依當前期間卦序排序（不再是清單原順序），案例 50 與結構斷言（無 sort）應紅。"""
    r = _mutate(tmp_path, "    codes.map(holdRowHtml).join(\"\")",
                "    codes.slice().sort((a, b) => Number((((DATA.stocks || {})[a] || {})[state.h] || {}).kw || 99) - Number((((DATA.stocks || {})[b] || {})[state.h] || {}).kw || 99)).map(holdRowHtml).join(\"\")")
    assert r.returncode == 1 and "FAIL 50 H5／H6 holdHtml" in r.stdout and "FAIL [結構] §11 H2／H5" in r.stdout, r.stdout


@pytest.mark.skipif(NODE is None, reason="node 不存在")
def test_hold_detects_bs_displayed(tmp_path: Path):
    """§11 H5 守門要活著：持股列把短線 base_score（bs）印出來，案例 50（53.4 出現）與結構斷言（引用 .bs）應紅。"""
    r = _mutate(tmp_path, '    `<td>${esc(code)}</td><td>${esc(nm[0] || "")}</td>',
                '    `<td>${esc(code)}</td><td>${esc(nm[0] || "")} ${s && s.short ? s.short.bs : ""}</td>')
    assert r.returncode == 1 and "FAIL 50 H5／H6 holdHtml" in r.stdout and "FAIL [結構] §11 H2／H5" in r.stdout, r.stdout


@pytest.mark.skipif(NODE is None, reason="node 不存在")
def test_hold_detects_sh_read(tmp_path: Path):
    """§11 H2 守門要活著：holdingsCodes 讀 sh（缺 sh 的筆被丟掉），案例 48／51 與結構斷言（碰 .sh）應紅。"""
    r = _mutate(tmp_path, '    if (!h || typeof h.c !== "string") continue;\n', '    if (!h || typeof h.c !== "string" || h.sh == null) continue;\n')
    assert r.returncode == 1 and "FAIL 48 H3 holdingsCodes" in r.stdout and "FAIL 51 H3 holdingsCodes" in r.stdout \
        and "FAIL [結構] §11 H2／H5" in r.stdout, r.stdout


@pytest.mark.skipif(NODE is None, reason="node 不存在")
def test_hold_detects_extra_set_item(tmp_path: Path):
    """§11 H2 守門要活著：readHoldings 多寫一次 localStorage.setItem（正規化後寫回），結構斷言「只准 getItem」應紅。"""
    r = _mutate(tmp_path, "  try{ return holdingsCodes(localStorage.getItem(HOLD_KEY)); }catch(e){ return []; }\n",
                "  try{ const v = holdingsCodes(localStorage.getItem(HOLD_KEY)); localStorage.setItem(HOLD_KEY, JSON.stringify(v.map(c => ({ c })))); return v; }catch(e){ return []; }\n")
    assert r.returncode == 1 and "FAIL [結構] §11 H2 全檔" in r.stdout, r.stdout


# lookbehind不排除 `.`：`window.localStorage.setItem` 這種帶前綴的寫法也要計入（D2）
_LS_RE = __import__("re").compile(r"(?<![A-Za-z_$])localStorage\b([^\n;]*)")
_LS_PREFIX_WRITE = __import__("re").compile(r"\b(window|self|globalThis)\.localStorage\.(setItem|removeItem|clear)\b")


def _storage_uses(src: str) -> list[str]:
    code = __import__("re").sub(r"<!--[\s\S]*?-->", "", src)
    code = __import__("re").sub(r"//[^\n]*", "", code)
    return [m.group(1) for m in _LS_RE.finditer(code)]


def test_holdings_storage_read_only():
    """§11 H2（不依賴 node）：index.html 去註解後，每一處 localStorage 都只是 `.getItem(HOLD_KEY)`；
    全檔無 localStorage 的 setItem／removeItem／clear／方括號存取，也不碰 Storage 原型。"""
    src = (ROOT / "index.html").read_text(encoding="utf-8")
    uses = _storage_uses(src)
    assert uses and all(u.startswith(".getItem(HOLD_KEY)") for u in uses), uses
    assert not _LS_PREFIX_WRITE.search(src)
    assert "Storage.prototype" not in src and 'const HOLD_KEY = "pm_holdings";' in src


def test_holdings_storage_static_guard_alive():
    """上一支的守門本身要活著：把 readHoldings 加一個 setItem 後，同一個檢查必須抓到。"""
    src = (ROOT / "index.html").read_text(encoding="utf-8")
    needle = "holdingsCodes(localStorage.getItem(HOLD_KEY))"
    assert src.count(needle) == 1
    mutated = src.replace(needle, 'localStorage.setItem(HOLD_KEY, "[]") || holdingsCodes(localStorage.getItem(HOLD_KEY))')
    uses = _storage_uses(mutated)
    assert not all(u.startswith(".getItem(HOLD_KEY)") for u in uses), uses
    # 帶 window. 前綴的寫入也要抓到（兩道各自獨立成立）
    mutated2 = src.replace(needle, 'window.localStorage.setItem(HOLD_KEY, "[]") || holdingsCodes(localStorage.getItem(HOLD_KEY))')
    uses2 = _storage_uses(mutated2)
    assert not all(u.startswith(".getItem(HOLD_KEY)") for u in uses2), uses2
    assert _LS_PREFIX_WRITE.search(mutated2)
