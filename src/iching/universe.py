"""個股池（使用者 2026-09-09 裁定 1）：

全市場 4 碼普通股＝`TaiwanStockInfo` 的 `type` ∈ {twse, tpex}、代號 4 碼純數字、非 `00` 開頭
（現為 3,060 檔）；point-in-time 池＝「當日有價格列」者。流動性門檻不在本腳本處理。

P0-A §4.4：`TaiwanStockInfo` 會有殘留列（同一代號多列、市場轉換／產業重分類），故以「任一列符合」納入，
市場別／產業別取 **`date` 最大的那一列**（無 `date` 時取最後一列）；report 會列出多列代號數供人工複核。

**2026-09-09 本容器免 token 實打 `TaiwanStockInfo`（4,319 列）**：type∈{twse,tpex} 且 4 碼純數字非 00 開頭的
**列數**＝1,996＋1,064＝**3,060**、恰等於裁定寫的「現為 3,060 檔」；但**不重複代號只有 2,149**（835 檔有多列，
例 5348 兩列：2025-06-01 通信網路業／2026-09-09 運動休閒類）。裁定的 3,060 疑為列數而非檔數——**待使用者確認**；
本模組以不重複代號為池。
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
        d = str(r.get("date") or "")
        cur = out.get(sid)
        if cur is None:
            out[sid] = {"type": t, "industry_category": r.get("industry_category"),
                        "stock_name": r.get("stock_name"), "n_rows": 1, "date": d}
        else:
            cur["n_rows"] += 1
            if d >= cur["date"]:   # 取 date 最大者；同日或無 date 時後者覆蓋
                cur.update({"type": t, "industry_category": r.get("industry_category"),
                            "stock_name": r.get("stock_name"), "date": d})
    return out


def pit_pool(pool_ids: Iterable[str], price_rows_for_day: Iterable[dict]) -> list[str]:
    """point-in-time 池：合格代號 ∩ 當日有價格列。"""
    ids = set(pool_ids)
    have = {str(r.get("stock_id") or "") for r in price_rows_for_day}
    return sorted(ids & have)
