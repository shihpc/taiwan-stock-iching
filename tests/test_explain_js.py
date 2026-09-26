"""`index.html` 說明層純函式（`explainHex`／`explainLine`）與靜態文案表（`FACET`／`TRI`／`TERMS`）的案例測試
（`docs/P4-PREVIEW.md` §6 F2）：以 subprocess 呼叫 node 跑 `tests/explain_cases.mjs`，該檔從 `index.html` 抽宣告切片在 vm 沙箱執行、
斷言輸出逐字＝預期句（34 案例＋11 結構斷言；25–34 與後 3 條結構斷言為 §9 模型換版標示）。node 不存在時 skip（CI runner 有 node；本地沒有就跳過，不假綠）。免 token 免網路。
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
    assert "34 案例" in r.stdout


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
