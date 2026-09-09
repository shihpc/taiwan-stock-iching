"""個股池（使用者 2026-09-09 裁定 1）：

全市場 4 碼普通股＝`TaiwanStockInfo` 的 `type` ∈ {twse, tpex}、代號 4 碼純數字、非 `00` 開頭
（現為 3,060 檔）；point-in-time 池＝「當日有價格列」者。流動性門檻不在本腳本處理。

P0-A §4.4：`TaiwanStockInfo` 會有殘留列（同一代號多列、市場轉換），故以「任一列符合」納入，
市場別取**最後一列**（FinMind 列序，推測為最新；未驗證——report 會列出多列代號數供人工複核）。
"""
from __future__ import annotations

from typing import Iterable

POOL_TYPES = frozenset({"twse", "tpex"})


def is_pool_candidate(stock_id: str, type_: str | None) -> bool:
    sid = str(stock_id or "")
    return (
        (type_ or "") in POOL_TYPES
        and len(sid) == 4
        and sid.isdigit()
        and not sid.startswith("00")
    )


def pool_from_info(rows: Iterable[dict]) -> dict[str, dict]:
    """{stock_id: {"type","industry_category","stock_name","n_rows"}}，只含合格代號。"""
    out: dict[str, dict] = {}
    for r in rows:
        sid = str(r.get("stock_id") or "")
        t = r.get("type")
        if not is_pool_candidate(sid, t):
            continue
        cur = out.get(sid)
        if cur is None:
            out[sid] = {"type": t, "industry_category": r.get("industry_category"),
                        "stock_name": r.get("stock_name"), "n_rows": 1}
        else:
            cur["n_rows"] += 1
            cur["type"] = t
            cur["industry_category"] = r.get("industry_category")
            cur["stock_name"] = r.get("stock_name")
    return out


def pit_pool(pool_ids: Iterable[str], price_rows_for_day: Iterable[dict]) -> list[str]:
    """point-in-time 池：合格代號 ∩ 當日有價格列。"""
    ids = set(pool_ids)
    have = {str(r.get("stock_id") or "") for r in price_rows_for_day}
    return sorted(ids & have)
