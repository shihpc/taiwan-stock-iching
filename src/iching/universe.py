"""個股池（使用者 2026-09-09 裁定 1）：

全市場 4 碼普通股＝`TaiwanStockInfo` 的 `type` ∈ {twse, tpex}、代號 4 碼純數字、非 `00` 開頭
（現為 3,060 檔）；point-in-time 池＝「當日有價格列」者。流動性門檻不在本腳本處理。

P0-A §4.4：`TaiwanStockInfo` 會有殘留列（同一代號多列、市場轉換／產業重分類），故以「任一列符合」納入，
市場別／產業別取 **`date` 最大的那一列**；同 `date` 仍多列時走**決定性 tie-break**（2026-09-09 驗收更正：
原「後者覆蓋」取決於 FinMind 回列順序，例 3092 同日兩列 `電子零組件業`／`電子工業`）：
排除 FinMind 傘狀類別（`UMBRELLA_CATEGORIES`，例 `電子工業`）若尚有更細者，再**優先 `type=="twse"`**，
最後依 (industry_category, stock_name) 字串序取第一。`same_date_multi=True` 標出這種代號，report 列出檔數供人工複核。
優先 twse 的理由：同代號同日殘留兩個市場時（2026-09-09 實查 11 檔跨 twse/tpex），台股轉板慣例是上櫃→上市，
取上市作為「較新狀態」的近似——**這是推測、不是查證**，所以只當 tie-break、不當市場判定（T 日所屬市場在
`config.OUT_OF_SCOPE`，由後續模組以殘留列 `date` 重建）。

**2026-09-09 本容器免 token 實打 `TaiwanStockInfo`（4,319 列）**：type∈{twse,tpex} 且 4 碼純數字非 00 開頭的
**列數**＝1,996＋1,064＝**3,060**、恰等於裁定寫的「現為 3,060 檔」；但**不重複代號只有 2,149**（835 檔有多列，
例 5348 兩列：2025-06-01 通信網路業／2026-09-09 運動休閒類）。裁定的 3,060 疑為列數而非檔數——**待使用者確認**；
本模組以不重複代號為池。

**「T 日所屬市場」不在本模組**（`config.OUT_OF_SCOPE`）：`pit_pool()` 只回「合格代號 ∩ 當日有價格列」，
T 日屬 twse／tpex 需由殘留列的 `date` 重建轉換點，交後續 universe 模組。
"""
from __future__ import annotations

from typing import Iterable

POOL_TYPES = frozenset({"twse", "tpex"})
# FinMind `industry_category` 的傘狀類別：同代號同日另有更細類別時不取它（2026-09-09 驗收所見：3092）
UMBRELLA_CATEGORIES = frozenset({"電子工業"})


def is_pool_candidate(stock_id: str, type_: str | None) -> bool:
    sid = str(stock_id or "")
    return (
        (type_ or "") in POOL_TYPES
        and len(sid) == 4
        and sid.isdigit()
        and not sid.startswith("00")
    )


def _pick(rows: list[dict]) -> dict:
    """同一代號多列 → 取 date 最大；同 date 多列 → 決定性 tie-break。"""
    max_date = max(str(r.get("date") or "") for r in rows)
    tied = [r for r in rows if str(r.get("date") or "") == max_date]
    finer = [r for r in tied if (r.get("industry_category") or "") not in UMBRELLA_CATEGORIES]
    cands = finer or tied
    cands = sorted(cands, key=lambda r: (0 if r.get("type") == "twse" else 1,
                                         str(r.get("industry_category") or ""), str(r.get("stock_name") or "")))
    return cands[0]


def pool_from_info(rows: Iterable[dict]) -> dict[str, dict]:
    """{stock_id: {"type","industry_category","stock_name","date","n_rows","same_date_multi"}}，只含合格代號。"""
    by_id: dict[str, list[dict]] = {}
    for r in rows:
        sid = str(r.get("stock_id") or "")
        if not is_pool_candidate(sid, r.get("type")):
            continue
        by_id.setdefault(sid, []).append(r)
    out: dict[str, dict] = {}
    for sid, rs in by_id.items():
        best = _pick(rs)
        max_date = str(best.get("date") or "")
        n_tied = sum(1 for r in rs if str(r.get("date") or "") == max_date)
        out[sid] = {"type": best.get("type"), "industry_category": best.get("industry_category"),
                    "stock_name": best.get("stock_name"), "date": max_date, "n_rows": len(rs),
                    "same_date_multi": n_tied > 1}
    return out


def pit_pool(pool_ids: Iterable[str], price_rows_for_day: Iterable[dict]) -> list[str]:
    """point-in-time 池：合格代號 ∩ 當日有價格列（不分市場，見模組 docstring）。"""
    ids = set(pool_ids)
    have = {str(r.get("stock_id") or "") for r in price_rows_for_day}
    return sorted(ids & have)
