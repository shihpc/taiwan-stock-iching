"""登錄書凍結守門（PR-6 2026-09-28 建立；2026-10-01 prereg-v2 PR-A 改成**版本感知**，`scratchpad/plan_prereg_v2.md` §1.4）。

守的四條（與 PR-6 當初相同）：
①§0 區段（`## 0.` 起到下一個 `## ` 止，**用區段切割、不是全檔**——前言的政策句「空欄位一律標 TBD」刻意留著）不含佔位標記；
②§0 的 `model_version`（twse／tpex）與 `params_sha` ＝ `build_params` ＋ `build_params_payload(mv, 320, cross.adv, fundamentals=True)`
  實算（同 `tests/test_apply_calibration.py` H3 的算法；`cross` 取 `data/state/cross.json`，`adv` 的 window／threshold 由快照帶）；
③§0 的 `RULES_VERSION` 列＝現行碼 `RULES_VERSION`；
④`data/rank_table.json`（§1.6 卦別排序表，凍結後驗證段／保留段不得重排）sha256 釘值。

版本感知（換版期間 CI 不得假紅、也不得把守門改成 skip）：
- **現行檔** `docs/pre-registration.md` 依 §0「本書版本」列切模式：含「已凍結」＝**嚴模式**（上面四條原樣：§0 無 TBD、§0 三列＝現行碼
  ＝`cross.json` meta、rank_table sha＝該版釘值）；含「草稿」＝**寬模式**（§0 允許 TBD；§0 三列**仍必須＝現行碼**；`cross.json` meta
  的 `params_sha` 允許 ∈ {最近封存版的值, 現行碼值}——PR-B 合、種子 PR 未合的紅窗期間 cross 仍是舊版；rank_table 不釘、只驗存在且可解析
  ——附錄 A 由 PR-D 重生，凍結時再釘）。兩種模式都要求 `cross.json` 的 window＝320。
- **封存版** `FROZEN[<ver>]`＝字面釘值（§0 三列、§1～§4 區段 sha256、rank_table sha256）。封存檔 `docs/pre-registration-<ver>.md`
  存在時對它驗；**不存在時對現行檔驗**（v1 現況：現行檔就是 v1，此時全部判定與改版前逐字等價＝仍全綠）；現行檔不是那一版又沒有封存檔
  ＝違反 §5「開新版本並保留舊版」→ 紅。
- **新增**：§1「方法」～§4「不在本次範圍」現行檔＝最近封存版**逐字**（只去掉 `vN` 版本字樣）——「候選清單／判準不動」的機械守門；
  封存檔不存在時該項 skip 並明印理由（v1 現況，現行檔就是封存版、無從比）。
任一紅＝有人動了凍結內容而沒開新版本（`docs/pre-registration.md` §5）。凍結後真的要改模型：開 vN+1 草稿、封存 vN、換 tag，
凍結時把 vN+1 的字面釘值收進 `FROZEN`——而不是把測試改綠。判定本體都是純函式（`check_*`），下方以合成文件測三態。

**2026-10-07 prereg-v2 PR-E（v2 凍結）**：現行檔「本書版本」列改「**v2（已凍結）**」→ 上面四條自動切回嚴模式（無程式分支要改）；
v2 字面釘值收進 `FROZEN["v2"]`（rank sha 由此取得；供下一次換版封存）。`FROZEN["v2"]["doc"]` 指向**日後**的封存路徑
`docs/pre-registration-v2.md`——現在不存在，故 `test_archived_version_literals_pinned[v2]` 對現行檔驗（同 v1 封存前的現況），
且「現行檔就是該版」時一律加驗釘值＝現行碼（原只對 `LATEST_ARCHIVED` 驗，改為對任何未封存版驗）。`LATEST_ARCHIVED` 仍是 v1
（最近一個**已有封存檔**的版本，§1～§4 逐字比對的對象）；開 v3 草稿、封存 v2 時改成 "v2"。
"""
from __future__ import annotations

import difflib
import hashlib
import json
import re
import sys

import pytest

from conftest import ROOT
from iching.features_io import params_fingerprint
from iching.run_common import build_params_payload, load_state
from iching.score.params import MARKETS, RULES_VERSION, build_params

sys.path.insert(0, str(ROOT / "scripts"))
from export_dataset import expected_params_sha  # noqa: E402

PREREG = ROOT / "docs" / "pre-registration.md"
CROSS = ROOT / "data" / "state" / "cross.json"
RANK_TABLE = ROOT / "data" / "rank_table.json"
WINDOW = 320

# 封存版字面釘值。v1：2026-09-28 凍結（`e6f62a6`／tag `prereg-v1`）；rank sha＝`sha256sum data/rank_table.json`（附錄 A 由 a7b713b 重生後未再變）；
# `sections_1_4_sha256`＝`docs/pre-registration.md` 自 `## 1. ` 起到 `## 5. ` 前（不含）的 UTF-8 位元組 sha256，2026-10-01 於 `5c50b9b` 實算。
# v2：2026-10-07 凍結（prereg-v2 PR-E；tag `prereg-v2` 合併後打）。rank sha＝`sha256sum data/rank_table.json`（附錄 A 由 PR-D1 以 v2 train 重生，
# 384 列表格與 v1 逐位相同、只換頂端指紋）；`sections_1_4_sha256` 與 v1 相同——§1～§4 逐字沿用 v1（連 `vN` 字樣都沒有差），PR-E 實算。
FROZEN: dict[str, dict] = {
    "v1": {"doc": "docs/pre-registration-v1.md", "rules": "p2-score-engine-2",
           "mv": {"twse": "p2-score-engine-2.01697576a7b0", "tpex": "p2-score-engine-2.83b5c5dfdb23"}, "sha": "8ca174ee8bc7",
           "rank_sha256": "243a1a19070e6125b81f926545fbde996100420c4e7f4d4f7d7962a558cdab5f",
           "sections_1_4_sha256": "2b10ce06134b6f6fa7638e45b67178ac334600ecbb2195f9e6283e5306a841cd"},
    "v2": {"doc": "docs/pre-registration-v2.md", "rules": "p2-score-engine-3",
           "mv": {"twse": "p2-score-engine-3.4b5db7fc6f6d", "tpex": "p2-score-engine-3.15407a6adb13"}, "sha": "cb3f2d905846",
           "rank_sha256": "0c4039de3333ac10e4cfe0e4eb1662ee48ad718af0edb81a71c7bcc6d20fda49",
           "sections_1_4_sha256": "2b10ce06134b6f6fa7638e45b67178ac334600ecbb2195f9e6283e5306a841cd"},
}
FROZEN_RULES_VERSION = "p2-score-engine-3"   # 現行凍結版（v2）的 `RULES_VERSION`；改碼升版而沒開新版本 → `test_current_frozen_version_pinned` 紅
LATEST_ARCHIVED = "v1"           # 最近一個「已有封存檔」的版本：新版草稿期間 cross.json 允許停在它的 params_sha；§1～§4 要與它逐字相同
_VER_RE = re.compile(r"\*\*(v\d+)（(草稿|已凍結)）\*\*")


# ---------------------------------------------------------------------------
# 純函式：切段、解析、判定
# ---------------------------------------------------------------------------
def section0(text: str) -> str:
    m = re.search(r"^## 0\. .*?(?=^## )", text, re.DOTALL | re.MULTILINE)
    assert m, "登錄書找不到 `## 0.` 區段"
    return m.group(0)


def sections_1_4(text: str) -> str:
    m = re.search(r"^## 1\. .*?(?=^## 5\. )", text, re.DOTALL | re.MULTILINE)
    assert m, "登錄書找不到 `## 1.`～`## 5.` 區段"
    return m.group(0)


def _row(sec: str, label: str) -> str:
    rows = [ln for ln in sec.splitlines() if ln.startswith(f"| {label} |")]
    assert len(rows) == 1, f"§0 的「{label}」列應恰 1 列，實得 {len(rows)}"
    return rows[0]


def doc_version(text: str) -> tuple[str, str]:
    """§0「本書版本」列 → (版本 `vN`, 模式 `draft`／`frozen`)。沒有「**vN（草稿）**」或「**vN（已凍結）**」字樣＝格式不符。"""
    row = _row(section0(text), "本書版本")
    m = _VER_RE.search(row)
    assert m, f"§0「本書版本」列須含「**vN（草稿）**」或「**vN（已凍結）**」：{row[:120]}"
    return m.group(1), ("draft" if m.group(2) == "草稿" else "frozen")


def parse_section0(text: str) -> dict:
    sec = section0(text)
    mv_row = _row(sec, "`model_version`")
    m = re.search(r"twse \*\*`(p2-score-engine-[^`]+)`\*\*／tpex \*\*`(p2-score-engine-[^`]+)`\*\*", mv_row)
    assert m, f"§0 model_version 列格式不符：{mv_row[:120]}"
    sha_row = _row(sec, "`params_sha`")
    m2 = re.search(r"\*\*`([0-9a-f]{12})`\*\*", sha_row)
    assert m2, f"§0 params_sha 列格式不符：{sha_row[:120]}"
    rules_row = _row(sec, "`RULES_VERSION`")
    m3 = re.search(r"\*\*`(p2-score-engine-\d+)`\*\*", rules_row)
    assert m3, f"§0 RULES_VERSION 列格式不符：{rules_row[:120]}"
    return {"mv": {"twse": m.group(1), "tpex": m.group(2)}, "sha": m2.group(1), "rules": m3.group(1)}


def check_placeholder(text: str, mode: str) -> None:
    """①嚴模式 §0 不含 TBD；寬模式允許。政策句本身兩種模式都要在（它是凍結 commit 機械判定的依據，不可順手刪掉）。"""
    assert "空欄位一律標 `TBD`" in text, "前言的政策句不見了"
    if mode == "frozen":
        assert "TBD" not in section0(text), "§0 仍有佔位標記——登錄書未凍結或有人把欄位清回去了"


def check_fingerprints(text: str, mode: str, *, live_mv: dict, live_sha: str, cross_meta: dict, archived_sha: str) -> None:
    """②§0 三列＝現行碼（兩種模式都要）；cross.json meta 的 params_sha：嚴模式＝現行碼、寬模式 ∈ {封存版值, 現行碼值}。"""
    w = parse_section0(text)
    assert w["mv"] == live_mv, f"§0 model_version {w['mv']} ≠ 現行碼 {live_mv}"
    assert w["sha"] == live_sha, f"§0 params_sha {w['sha']} ≠ 現行碼實算 {live_sha}"
    allowed = {live_sha} if mode == "frozen" else {live_sha, archived_sha}
    assert cross_meta.get("params_sha") in allowed and cross_meta.get("window") == WINDOW, \
        f"cross.json meta {cross_meta} 與允許值 {sorted(allowed)}／window {WINDOW} 不符（模式 {mode}）"


def check_rules_version(text: str, live_rules: str) -> None:
    """③§0 RULES_VERSION 列＝現行碼。"""
    assert parse_section0(text)["rules"] == live_rules, f"§0 RULES_VERSION ≠ 現行碼 {live_rules}"


def check_rank_table(rank_bytes: bytes, mode: str, pinned: str | None) -> None:
    """④嚴模式 sha256＝該版釘值；寬模式不釘（附錄 A 由換版 PR 重生），只驗可解析。"""
    got = hashlib.sha256(rank_bytes).hexdigest()
    if mode == "frozen":
        assert pinned is not None, f"凍結版尚未在 FROZEN 釘 rank_table sha256（現值 {got}）"
        assert got == pinned, f"data/rank_table.json sha256 {got} ≠ 釘值——排序表在凍結後被重生或改動"
    else:
        try:
            assert isinstance(json.loads(rank_bytes.decode("utf-8")), (dict, list))
        except (ValueError, UnicodeDecodeError) as e:
            raise AssertionError(f"data/rank_table.json 不是合法 JSON：{e}") from None


def check_archived(text: str, ver: str, pin: dict) -> None:
    """封存版：§0 寫明「vN（已凍結）」、無 TBD、三列＝字面釘值、§1～§4 位元組 sha256＝釘值（舊版保留、不得改）。"""
    got_ver, mode = doc_version(text)
    assert (got_ver, mode) == (ver, "frozen"), f"封存版應為「{ver}（已凍結）」，實得 {got_ver}（{mode}）"
    assert "TBD" not in section0(text)
    w = parse_section0(text)
    assert w == {"mv": pin["mv"], "sha": pin["sha"], "rules": pin["rules"]}, f"封存版 {ver} §0 三列 {w} ≠ 釘值"
    got = hashlib.sha256(sections_1_4(text).encode("utf-8")).hexdigest()
    assert got == pin["sections_1_4_sha256"], f"封存版 {ver} §1～§4 sha256 {got} ≠ 釘值——凍結內容被改動"


def _norm(s: str) -> str:
    return re.sub(r"\bv\d+\b", "vN", s)


def check_sections_1_4_equal(cur_text: str, archived_text: str) -> None:
    """§1～§4 現行檔＝封存檔逐字（只去 `vN` 版本字樣）。"""
    a, b = _norm(sections_1_4(archived_text)).splitlines(), _norm(sections_1_4(cur_text)).splitlines()
    if a != b:
        diff = "\n".join(list(difflib.unified_diff(a, b, "封存版", "現行檔", lineterm="", n=1))[:40])
        raise AssertionError("§1～§4（方法～不在本次範圍）現行檔與封存版不同——候選清單／判準不得隨換版改動：\n" + diff)


# ---------------------------------------------------------------------------
# 真檔
# ---------------------------------------------------------------------------
def _live() -> tuple[dict[str, str], str, dict]:
    mv = {m: build_params(m).model_version() for m in MARKETS}
    cross = load_state(CROSS)
    payload = build_params_payload(mv, WINDOW, cross.adv, fundamentals=True)
    return mv, params_fingerprint(payload), cross.meta or {}


def _current() -> str:
    return PREREG.read_text(encoding="utf-8")


def _archived_text(ver: str) -> tuple[str, str]:
    """封存檔存在 → (它的內容, 路徑)；不存在 → (現行檔, 現行檔路徑)（`check_archived` 會驗現行檔真的是那一版）。"""
    p = ROOT / FROZEN[ver]["doc"]
    return (p.read_text(encoding="utf-8"), str(p)) if p.exists() else (_current(), str(PREREG))


def test_section0_has_no_placeholder():
    check_placeholder(_current(), doc_version(_current())[1])


def test_section0_model_version_and_params_sha_match_live_code():
    text = _current()
    mv, sha, meta = _live()
    check_fingerprints(text, doc_version(text)[1], live_mv=mv, live_sha=sha, cross_meta=meta, archived_sha=FROZEN[LATEST_ARCHIVED]["sha"])
    # 與 H3 的另一條算法（db 記的 adv 參數）同值——三路互證
    want = {"window": WINDOW, "adv_window": 60, "adv_threshold": 30000000.0, "fundamentals": True}
    assert expected_params_sha(want)[:12] == sha


def test_rules_version_frozen():
    check_rules_version(_current(), RULES_VERSION)


def test_rank_table_sha256_pinned():
    ver, mode = doc_version(_current())
    check_rank_table(RANK_TABLE.read_bytes(), mode, FROZEN.get(ver, {}).get("rank_sha256"))


@pytest.mark.parametrize("ver", sorted(FROZEN))
def test_archived_version_literals_pinned(ver):
    text, where = _archived_text(ver)
    try:
        check_archived(text, ver, FROZEN[ver])
    except AssertionError as e:
        raise AssertionError(f"[{where}] {e}") from None
    if not (ROOT / FROZEN[ver]["doc"]).exists():
        # 現行檔就是這一版（v1 封存前／v2 凍結後的現況）→ 它的字面釘值必須＝現行碼（與改版前的四條逐字等價）
        mv, sha, _ = _live()
        assert FROZEN[ver]["mv"] == mv and FROZEN[ver]["sha"] == sha and FROZEN[ver]["rules"] == RULES_VERSION


def test_current_frozen_version_pinned():
    """現行檔已凍結時：它的版本必須在 `FROZEN` 有字面釘值，且釘的 `RULES_VERSION`＝`FROZEN_RULES_VERSION`＝現行碼（v2：`p2-score-engine-3`）。
    草稿期不適用（v2 草稿期間現行檔不在 `FROZEN`）。"""
    ver, mode = doc_version(_current())
    if mode != "frozen":
        pytest.skip(f"現行檔 {ver} 為草稿，尚未凍結")
    assert ver in FROZEN, f"現行檔 {ver} 已標「已凍結」，但 FROZEN 沒有它的字面釘值"
    assert FROZEN[ver]["rules"] == FROZEN_RULES_VERSION == RULES_VERSION, \
        f"FROZEN[{ver}] rules {FROZEN[ver]['rules']}／FROZEN_RULES_VERSION {FROZEN_RULES_VERSION}／現行碼 {RULES_VERSION} 不一致"


def test_sections_1_to_4_identical_to_archived():
    p = ROOT / FROZEN[LATEST_ARCHIVED]["doc"]
    if not p.exists():
        pytest.skip(f"封存檔 {p.relative_to(ROOT)} 不存在：現行檔即 {LATEST_ARCHIVED}（v1 現況），無從比對；開新版本時封存檔必須一併出現")
    check_sections_1_4_equal(_current(), p.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# 合成文件：三態（v1 現況＝真檔測試本身；草稿；已凍結）＋突變
# ---------------------------------------------------------------------------
LIVE_MV = {"twse": "p2-score-engine-3.aaaaaaaaaaaa", "tpex": "p2-score-engine-3.bbbbbbbbbbbb"}
LIVE_SHA, V1_SHA, LIVE_RULES = "cccccccccccc", FROZEN["v1"]["sha"], "p2-score-engine-3"
RANK_V2 = b'{"schema": 1, "rows": []}'


def _v1_doc() -> str:
    return _current() if not (ROOT / FROZEN["v1"]["doc"]).exists() else (ROOT / FROZEN["v1"]["doc"]).read_text(encoding="utf-8")


def _v2_doc(state: str, *, tbd: bool) -> str:
    """由 v1 真檔造 v2：標題與「本書版本」列換版、§0 三列換成「現行碼」（上面的假值）、凍結 commit 列依 `tbd` 填 TBD；§1～§4 原樣。"""
    t = _v1_doc()
    t = t.replace("預先登錄書 v1（已凍結）", f"預先登錄書 v2（{state}）", 1)
    sec = section0(t)
    new = sec
    new = new.replace(_row(sec, "本書版本"), f"| 本書版本 | **v2（{state}）**——D4-①(a) 換版；候選清單／判準逐字沿用 v1 |")
    new = new.replace(_row(sec, "`model_version`"), f"| `model_version` | twse **`{LIVE_MV['twse']}`**／tpex **`{LIVE_MV['tpex']}`** |")
    new = new.replace(_row(sec, "`params_sha`"), f"| `params_sha` | **`{LIVE_SHA}`** |")
    new = new.replace(_row(sec, "`RULES_VERSION`"), f"| `RULES_VERSION` | **`{LIVE_RULES}`** |")
    new = new.replace(_row(sec, "凍結 commit"), "| 凍結 commit | TBD |" if tbd else "| 凍結 commit | **`ffffff0`** |")
    assert new != sec
    return t.replace(sec, new, 1)


def _archive_doc() -> str:
    """v1 封存副本＝檔首加一行索引註記，其餘逐位相同。"""
    return "> v1 封存副本（凍結 `e6f62a6`／tag `prereg-v1`）；自本 commit 起不再修改。\n\n" + _v1_doc()


def test_status_quo_v1_is_frozen_mode():
    assert doc_version(_v1_doc()) == ("v1", "frozen")
    check_archived(_archive_doc(), "v1", FROZEN["v1"])                    # 封存副本只在檔首加註記 → 仍過


def test_synthetic_draft_mode():
    """v2 草稿：§0 有 TBD 可、三列必須＝現行碼、cross 停在 v1 或已是現行皆可、rank 不釘、§1～§4＝v1。"""
    doc = _v2_doc("草稿", tbd=True)
    assert doc_version(doc) == ("v2", "draft")
    check_placeholder(doc, "draft")
    for meta_sha in (V1_SHA, LIVE_SHA):
        check_fingerprints(doc, "draft", live_mv=LIVE_MV, live_sha=LIVE_SHA, cross_meta={"params_sha": meta_sha, "window": WINDOW},
                           archived_sha=V1_SHA)
    check_rules_version(doc, LIVE_RULES)
    check_rank_table(RANK_V2, "draft", None)
    check_rank_table(RANK_TABLE.read_bytes(), "draft", None)
    check_sections_1_4_equal(doc, _archive_doc())
    # 突變
    with pytest.raises(AssertionError, match="cross.json meta"):
        check_fingerprints(doc, "draft", live_mv=LIVE_MV, live_sha=LIVE_SHA, cross_meta={"params_sha": "deadbeefdead", "window": WINDOW},
                           archived_sha=V1_SHA)
    with pytest.raises(AssertionError, match="window"):
        check_fingerprints(doc, "draft", live_mv=LIVE_MV, live_sha=LIVE_SHA, cross_meta={"params_sha": LIVE_SHA, "window": 250},
                           archived_sha=V1_SHA)
    with pytest.raises(AssertionError, match="model_version"):                     # 三列必須＝現行碼，草稿也不例外
        check_fingerprints(doc, "draft", live_mv=FROZEN["v1"]["mv"], live_sha=LIVE_SHA, cross_meta={"params_sha": LIVE_SHA, "window": WINDOW},
                           archived_sha=V1_SHA)
    with pytest.raises(AssertionError, match="RULES_VERSION"):
        check_rules_version(doc, "p2-score-engine-2")
    with pytest.raises(AssertionError, match="候選清單／判準不得隨換版改動"):
        check_sections_1_4_equal(doc.replace("### 1.4 採用門檻", "### 1.4 採用門檻（放寬）", 1), _archive_doc())
    with pytest.raises(AssertionError, match="候選清單／判準不得隨換版改動"):
        check_sections_1_4_equal(doc, _archive_doc().replace("## 2. 候選清單", "## 2. 候選清單（多一個）", 1))
    with pytest.raises(AssertionError, match="rank_table"):
        check_rank_table(b"not json", "draft", None)


def test_synthetic_frozen_mode():
    """v2 已凍結：§0 不得有 TBD、cross 必須＝現行碼、rank 必須＝v2 釘值（未釘＝紅）。"""
    doc = _v2_doc("已凍結", tbd=False)
    assert doc_version(doc) == ("v2", "frozen")
    check_placeholder(doc, "frozen")
    check_fingerprints(doc, "frozen", live_mv=LIVE_MV, live_sha=LIVE_SHA, cross_meta={"params_sha": LIVE_SHA, "window": WINDOW}, archived_sha=V1_SHA)
    check_rank_table(RANK_V2, "frozen", hashlib.sha256(RANK_V2).hexdigest())
    with pytest.raises(AssertionError, match="仍有佔位標記"):
        check_placeholder(_v2_doc("已凍結", tbd=True), "frozen")
    with pytest.raises(AssertionError, match="cross.json meta"):                   # 嚴模式不再允許 cross 停在 v1
        check_fingerprints(doc, "frozen", live_mv=LIVE_MV, live_sha=LIVE_SHA, cross_meta={"params_sha": V1_SHA, "window": WINDOW}, archived_sha=V1_SHA)
    with pytest.raises(AssertionError, match="尚未在 FROZEN 釘"):
        check_rank_table(RANK_V2, "frozen", None)
    with pytest.raises(AssertionError, match="被重生或改動"):
        check_rank_table(RANK_V2 + b"\n", "frozen", hashlib.sha256(RANK_V2).hexdigest())


def test_synthetic_archive_guard():
    """封存版守門：三列字面、「v1（已凍結）」字樣、§1～§4 sha256——任一被改即紅；v2 草稿冒充封存檔也紅。"""
    arch = _archive_doc()
    check_archived(arch, "v1", FROZEN["v1"])
    with pytest.raises(AssertionError, match="凍結內容被改動"):
        check_archived(arch.replace("### 1.4 採用門檻", "### 1.4 採用門檻（放寬）", 1), "v1", FROZEN["v1"])
    with pytest.raises(AssertionError, match="三列"):
        check_archived(arch.replace("**`8ca174ee8bc7`**", "**`8ca174ee8bc8`**", 1), "v1", FROZEN["v1"])
    with pytest.raises(AssertionError, match="應為「v1（已凍結）」"):
        check_archived(_v2_doc("草稿", tbd=True), "v1", FROZEN["v1"])
    with pytest.raises(AssertionError, match="應為「v1（已凍結）」"):
        check_archived(arch.replace("**v1（已凍結）**", "**v1（草稿）**", 1), "v1", FROZEN["v1"])
