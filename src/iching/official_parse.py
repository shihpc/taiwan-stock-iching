"""TWSE／TPEx 官方端點回應的**純函式**解析器（B1.4 官方法人、B1.3 市場成交金額）。

**為什麼是純函式**：回補這層拿到的是存在 `raw_*.body` 的 JSON 字串，每日班那層拿到的是
API 即時回應——兩層能共用的只有「JSON dict → 結構值」這一段。把它與 sqlite 讀取分離，
是 `spec/P1-B3-replay.md` §B3.2 兩層 parity 的前提；本模組**不得 import sqlite3**。

口徑沿用姊妹站 `taiwan-flows/src/totals.py`（家族已踩過的坑，不重寫一套）：

- **上市 BFI82U 沒有「合計」列，必須加總分項**；上櫃 TPEx 有合計列，直接取，
  **不可把「外資及陸資合計」與其兩個子列一起加**（會重複計）。
- **TPEx 的列名與 TWSE 不同**：TWSE 是「外資及陸資(不含**外資**自營商)」，TPEx 是
  「外資及陸資(不含自營商)」，且列名帶**全形空格**前綴（`_norm` 專門剝它）。
- **單位不一致**：FMTQIK 的成交金額是**元**（要 ÷1000），tradingIndex 的**已經是千元**。
  這兩個弄反會差 1000 倍且不會報錯，是本模組最危險的地方，`tests/test_official_parse.py` 有守。
- **tradingIndex 的回應沒有 `fields` 欄**（FMTQIK 有），欄位只能靠位置。本模組因此對列長度
  設下限並在取不到時回缺值，不做「猜哪一欄」的補救。
"""
from __future__ import annotations

from typing import Any

# 上市 BFI82U 列名（無「合計」列，需加總分項）
TSE_FOREIGN = frozenset({"外資及陸資(不含外資自營商)", "外資自營商"})
TSE_TRUST = frozenset({"投信"})
TSE_DEALER = frozenset({"自營商(自行買賣)", "自營商(避險)"})
# 上櫃 TPEx 有「合計」列，直接取（**不可再加子列**）
OTC_FOREIGN = frozenset({"外資及陸資合計"})
OTC_TRUST = frozenset({"投信"})
OTC_DEALER = frozenset({"自營商合計"})

# tradingIndex 逐日列的欄位位置（無 fields 欄，只能靠位置；來源：taiwan-flows/src/totals.py:69-71）
TPEX_IDX_DATE = 0
TPEX_IDX_AMOUNT_K = 2      # 已是千元
FMTQIK_IDX_DATE = 0
FMTQIK_IDX_AMOUNT = 2      # 元
FMTQIK_IDX_INDEX = 4       # 發行量加權股價指數


class OfficialParseError(ValueError):
    """回應形狀不符預期（缺欄、缺必要列、日期解不出）。"""


def roc_to_iso(s: Any) -> str | None:
    """民國 `109/01/02` → `2020-01-02`；解不出回 None。"""
    try:
        y, m, d = str(s).split("/")
        return f"{int(y) + 1911}-{int(m):02d}-{int(d):02d}"
    except (ValueError, AttributeError):
        return None


def _norm(name: Any) -> str:
    """剝全形空格與前後空白（TPEx 列名帶全形空格前綴）。"""
    return str(name).replace("　", "").strip()


def _num(s: Any) -> float:
    """去千分位逗號轉 float。"""
    return float(str(s).replace(",", ""))


def _parse_inst_rows(rows: Any, foreign: frozenset, trust: frozenset, dealer: frozenset) -> dict[str, int]:
    """三大法人列 → {f,t,d}×{buy_k,sell_k,net_k}（千元）。必要列缺失即 raise。"""
    if not isinstance(rows, list):
        raise OfficialParseError(f"data 非 list：{type(rows).__name__}")
    agg = {"f": [0.0, 0.0], "t": [0.0, 0.0], "d": [0.0, 0.0]}
    matched: set[str] = set()
    for row in rows:
        if not isinstance(row, list) or len(row) < 3:
            continue
        name = _norm(row[0])
        for tag, names in (("f", foreign), ("t", trust), ("d", dealer)):
            if name in names:
                agg[tag][0] += _num(row[1])
                agg[tag][1] += _num(row[2])
                matched.add(name)
    # **要求每個集合的列名全部到齊**（比姊妹站 taiwan-flows 的「任一對上即可」嚴格）。
    # 2026-09-12 寫測試時發現：拿 TWSE 列名去解 TPEx 回應，寬鬆檢查**不會報錯**——
    # TPEx 也有「投信」列，且其「　外資自營商」剝掉全形空格後正好對上 TSE_FOREIGN 的成員之一，
    # 於是外資只認到那一列、值恆為 0，**每天的外資買賣超都會是 0 且完全無聲**。
    # 研究回測裡「數字系統性錯誤」比「當天抓不到」嚴重得多，故一律要求全到齊、否則 raise。
    expected = foreign | trust | dealer
    missing = expected - matched
    if missing:
        raise OfficialParseError(f"缺必要列 {sorted(missing)}；matched={sorted(matched)}")
    out: dict[str, int] = {}
    for tag in ("f", "t", "d"):
        b, sell = agg[tag]
        out[f"{tag}_buy_k"] = round(b / 1000)
        out[f"{tag}_sell_k"] = round(sell / 1000)
        out[f"{tag}_net_k"] = round((b - sell) / 1000)
    return out


def parse_bfi82u(body: dict) -> dict[str, int]:
    """TWSE BFI82U（上市三大法人，單日）→ 千元。金額原始單位為元。"""
    if not isinstance(body, dict):
        raise OfficialParseError(f"body 非 dict：{type(body).__name__}")
    if str(body.get("stat", "")).upper() != "OK":
        raise OfficialParseError(f"stat={body.get('stat')!r}")
    return _parse_inst_rows(body.get("data"), TSE_FOREIGN, TSE_TRUST, TSE_DEALER)


def _tpex_first_table(body: dict) -> dict:
    if not isinstance(body, dict):
        raise OfficialParseError(f"body 非 dict：{type(body).__name__}")
    tables = body.get("tables")
    if not tables:
        raise OfficialParseError("tables 空或缺")
    tbl = tables[0] if isinstance(tables, list) else tables
    if not isinstance(tbl, dict):
        raise OfficialParseError(f"tables[0] 非 dict：{type(tbl).__name__}")
    return tbl


def parse_tpex_inst_summary(body: dict) -> dict[str, int]:
    """TPEx 三大法人買賣金額彙總（上櫃，單日）→ 千元。金額原始單位為元。"""
    return _parse_inst_rows(_tpex_first_table(body).get("data"), OTC_FOREIGN, OTC_TRUST, OTC_DEALER)


def parse_fmtqik_month(body: dict) -> dict[str, dict[str, float]]:
    """TWSE FMTQIK（上市，整月日列）→ {ISO 日期: {"turnover_k": 千元, "taiex": 指數}}。

    **成交金額原始單位是元**，本函式除以 1000 轉千元。`taiex` 解不出時該日不帶該鍵。
    """
    if not isinstance(body, dict):
        raise OfficialParseError(f"body 非 dict：{type(body).__name__}")
    if str(body.get("stat", "")).upper() != "OK":
        raise OfficialParseError(f"stat={body.get('stat')!r}")
    out: dict[str, dict[str, float]] = {}
    for row in body.get("data") or []:
        if not isinstance(row, list) or len(row) <= FMTQIK_IDX_INDEX:
            continue
        iso = roc_to_iso(row[FMTQIK_IDX_DATE])
        if not iso:
            continue
        rec: dict[str, float] = {"turnover_k": round(_num(row[FMTQIK_IDX_AMOUNT]) / 1000)}
        try:
            rec["taiex"] = _num(row[FMTQIK_IDX_INDEX])
        except ValueError:
            pass
        out[iso] = rec
    if not out:
        raise OfficialParseError("FMTQIK 無可解析的日列")
    return out


def parse_tpex_trading_index_month(body: dict) -> dict[str, int]:
    """TPEx 日成交量值指數（上櫃，整月日列）→ {ISO 日期: 成交金額千元}。

    **原始單位已是千元**，不再除。回應**沒有 `fields` 欄**，欄位靠位置（見模組 docstring）。
    """
    data = _tpex_first_table(body).get("data")
    if not isinstance(data, list):
        raise OfficialParseError(f"tables[0].data 非 list：{type(data).__name__}")
    out: dict[str, int] = {}
    for row in data:
        if not isinstance(row, list) or len(row) <= TPEX_IDX_AMOUNT_K:
            continue
        iso = roc_to_iso(row[TPEX_IDX_DATE])
        if not iso:
            continue
        out[iso] = round(_num(row[TPEX_IDX_AMOUNT_K]))
    if not out:
        raise OfficialParseError("tradingIndex 無可解析的日列")
    return out
