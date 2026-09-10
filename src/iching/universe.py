"""個股池（使用者 2026-09-09 裁定 1）：

全市場 4 碼普通股＝`TaiwanStockInfo` 的 `type` ∈ {twse, tpex}、代號 4 碼純數字、非 `00` 開頭、
**且排除 DR**（2026-09-10 裁定甲，見末段；4 碼形狀含 11 檔存託憑證，「4 碼池 ≠ 普通股池」）；
point-in-time 池＝「當日有價格列」者。裁定原文的「現為 3,060 檔」是列數（見下文），不重複代號 2,149、再扣 11 檔 DR＝2,138。
流動性門檻不在本腳本處理。

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

**DR 不進個股池（使用者 2026-09-10 裁定甲）**：4 碼純數字非 `00` 的代號裡有 **11 檔存託憑證**
（`9101,9102,9103,9104,9105,9106,9110,9136,9151,9157,9188`，`industry_category='存託憑證'`），裁定 #6 的形狀條件
會把它們收進池。理由：與 `spec/P1-B1-market.md:157`「排除 ETF／權證／DR／特別股／興櫃」一致；DR 是境外公司存託憑證，
財報／營收口徑與本國股不同，B2 基本面族會算出無意義的值；廣度母體 `N` 一律排除 DR（spec 明文）。
`is_dr_code()` 以**兩條件取聯集**排除：①`industry_category == '存託憑證'`；②純形狀後備＝4 碼且以 `91` 開頭。
兩條並存的理由：①來自**今日快照**，已下市 DR 在 `TaiwanStockInfo` 查不到、只剩價格列，只能靠形狀②；
②單獨用則依賴「91xx 全是 DR」這個經驗規律，故以①為主、②為後備。
**實查（2026-09-10 本容器免 token 打 `TaiwanStockInfo`，4,321 列）**：4 碼純數字 `91xx` 恰 11 檔、全為 `存託憑證`；
`industry_category='存託憑證'` 共 36 檔（4 碼 11＋6 碼 25），**全部以 `91` 開頭**，無例外。
**殘餘風險**：日後若有非 DR 的 `91xx` 4 碼普通股掛牌，會被②誤殺——依上述實查目前不存在，但快照只能證明「現在」。
排除只發生在**讀取端的名單建構**（本函式）；落地仍保留 DR 原始列（4 碼與 6 碼皆是），與 `config.is_warrant_code`
（落地過濾，只砍權證）是兩件事、不混在同一函式。
"""
from __future__ import annotations

from typing import Iterable

POOL_TYPES = frozenset({"twse", "tpex"})
# FinMind `industry_category` 的傘狀類別：同代號同日另有更細類別時不取它（2026-09-09 驗收所見：3092）
UMBRELLA_CATEGORIES = frozenset({"電子工業"})
# 存託憑證（DR）：FinMind `industry_category` 的字面值；4 碼 DR 的形狀前綴（實查見模組 docstring）
DR_CATEGORY = "存託憑證"
DR_PREFIX_4 = "91"


def is_dr_code(stock_id: str, industry_category: str | None) -> bool:
    """DR 判定（池過濾，2026-09-10 裁定甲）：`industry_category=='存託憑證'` **或** 4 碼且以 `91` 開頭（已下市 DR 的後備）。
    只用於個股池名單建構；不在落地路徑使用。"""
    sid = str(stock_id or "")
    return (industry_category or "") == DR_CATEGORY or (len(sid) == 4 and sid.isdigit() and sid.startswith(DR_PREFIX_4))


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
    """{stock_id: {"type","industry_category","stock_name","date","n_rows","same_date_multi"}}，只含合格代號。

    DR 排除（2026-09-10 裁定甲）看**該代號的任一列**：任一列 `industry_category=='存託憑證'`，或代號本身符合
    4 碼 `91` 開頭的形狀規則，整個代號不進池——不因殘留列（產業重分類）恰好不是 DR 而漏放。"""
    by_id: dict[str, list[dict]] = {}
    for r in rows:
        sid = str(r.get("stock_id") or "")
        if not is_pool_candidate(sid, r.get("type")):
            continue
        by_id.setdefault(sid, []).append(r)
    out: dict[str, dict] = {}
    for sid, rs in by_id.items():
        if any(is_dr_code(sid, r.get("industry_category")) for r in rs):
            continue
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
