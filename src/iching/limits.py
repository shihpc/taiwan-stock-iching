"""P4-E2 交易限制旗標：官方來源的純函式解析（docs/P4-PREVIEW.md §20）。

依據：裁定 #76 §14.2 Q4（層 2 只顯示旗標、不進分數）；v1.2.2 `:411`／`:422`／`:423`。
來源與欄位形狀＝2026-10-28 runner 實測（run 40720532027）與調查樣本；本模組**不碰網路**，抓取在 `scripts/build_limits.py`。

所有解析函式回「代號 → 內容」或代號集合；日期一律正規化成 ISO `YYYY-MM-DD`，民國年三種寫法都收
（`1151027`、`115/10/27`、`115.10.27`）。官方欄位以「非空白即標記」判定（TWSE 用 `*`、TPEx 用 `Ｙ`／`*`，只看是否非空白）。
"""
from __future__ import annotations

import datetime as dt
import re
from typing import Any, Iterable, Mapping, Sequence

_ROC7 = re.compile(r"^\s*(\d{3})(\d{2})(\d{2})\s*$")
_ROCSEP = re.compile(r"^\s*(\d{2,3})[/.\-](\d{1,2})[/.\-](\d{1,2})\s*$")
_CODE = re.compile(r"^[0-9A-Z]{4,6}$")
_PERIOD_SPLIT = re.compile(r"\s*[~～]\s*")


def roc(s: Any) -> str | None:
    """民國日期（`1151027`／`115/10/27`／`115.10.27`）→ ISO；不合或日期不存在回 None。"""
    if not isinstance(s, str):
        return None
    m = _ROC7.match(s) or _ROCSEP.match(s)
    if not m:
        return None
    try:
        return dt.date(int(m.group(1)) + 1911, int(m.group(2)), int(m.group(3))).isoformat()
    except ValueError:
        return None


def period(s: Any) -> tuple[str, str] | None:
    """`115/10/28～115/11/03`／`1151029~1151106` → (起, 迄) ISO；任一端不合回 None。"""
    if not isinstance(s, str):
        return None
    parts = _PERIOD_SPLIT.split(s.strip())
    if len(parts) != 2:
        return None
    a, b = roc(parts[0]), roc(parts[1])
    return (a, b) if a and b else None


def code_of(v: Any) -> str | None:
    """代號欄：去空白；TPEx www 處置表的名稱欄帶連結不影響代號欄。不合 4–6 位大寫英數回 None。"""
    c = str(v or "").strip().upper()
    return c if _CODE.match(c) else None


def marked(v: Any) -> bool:
    return bool(str(v or "").strip())


def _col(fields: Sequence[str], *keys: str) -> int:
    """以欄名子字串找欄位索引（官方欄名偶有全形標點差異，例「融(借)券」／「融券、借券」）；找不到拋 KeyError。"""
    for k in keys:
        for i, f in enumerate(fields):
            if k in str(f):
                return i
    raise KeyError(f"缺欄位 {keys}：{list(fields)}")


def rows_fields(body: Any) -> tuple[list, list[str]]:
    """TWSE rwd＝{fields,data}；TPEx www＝{tables:[{fields,data}]}（取第一個有 fields 的表）。"""
    if isinstance(body, dict) and isinstance(body.get("data"), list) and isinstance(body.get("fields"), list):
        return body["data"], [str(f) for f in body["fields"]]
    if isinstance(body, dict):
        for t in body.get("tables") or []:
            if isinstance(t, dict) and isinstance(t.get("data"), list) and isinstance(t.get("fields"), list):
                return t["data"], [str(f) for f in t["fields"]]
    raise ValueError("回應不是 {fields,data} 或 {tables:[...]} 形狀")


# ---------- 信用交易資格＋放空限制（平盤下名單） ----------

def parse_margin_list(body: Any) -> dict[str, dict[str, bool]]:
    """TWSE `TWT92U`／TPEx www `margin/mark`：表列＝所有得為融資融券之證券（官方 notes）。
    回 {代號: {ms: 暫停融券賣出, sbl: 暫停借券賣出, dn: 前一交易日跌停本日禁止平盤下融(借)券賣出}}。"""
    rows, f = rows_fields(body)
    ic, ims, isbl, idn = _col(f, "代號"), _col(f, "暫停融券"), _col(f, "暫停借券"), _col(f, "跌停")
    out: dict[str, dict[str, bool]] = {}
    for r in rows:
        c = code_of(r[ic]) if isinstance(r, list) and len(r) > max(ic, ims, isbl, idn) else None
        if c:
            out[c] = {"ms": marked(r[ims]), "sbl": marked(r[isbl]), "dn": marked(r[idn])}
    return out


# ---------- 注意股 ----------

def parse_notice(body: Any) -> dict[str, dict[str, Any]]:
    """TWSE rwd `announcement/notice`／TPEx www `bulletin/attention` → {代號: {d: 公告日 ISO, cnt: 累計次數, info: 注意交易資訊}}。"""
    rows, f = rows_fields(body)
    ic, icnt, iinfo, idate = _col(f, "代號"), _col(f, "累計"), _col(f, "注意交易資訊"), _col(f, "日期")
    out: dict[str, dict[str, Any]] = {}
    for r in rows:
        if not (isinstance(r, list) and len(r) > max(ic, icnt, iinfo, idate)):
            continue
        c = code_of(r[ic])
        if not c:
            continue
        try:
            cnt = int(str(r[icnt]).strip())
        except ValueError:
            cnt = None
        info = re.sub(r"<br\s*/?>", "；", str(r[iinfo] or "")).strip()
        out[c] = {"d": roc(str(r[idate])), "cnt": cnt, "info": info[:300]}
    return out


# ---------- 處置 ----------

def parse_disposal(body: Any) -> dict[str, list[dict[str, Any]]]:
    """TWSE rwd `announcement/punish`／TPEx www `bulletin/disposal` → {代號: [{pub, from, to, measure, reason}]}（依起日排序、去重）。
    起訖不合的列略過（不臆造期間）。"""
    rows, f = rows_fields(body)
    ic, ipub, iper = _col(f, "代號"), _col(f, "公布日期"), _col(f, "起迄", "起訖")
    imeas = _col(f, "處置措施")
    ireason = _col(f, "處置條件", "處置原因")
    out: dict[str, set[tuple]] = {}
    for r in rows:
        if not (isinstance(r, list) and len(r) > max(ic, ipub, iper, imeas, ireason)):
            continue
        c, p = code_of(r[ic]), period(str(r[iper]))
        if not c or not p:
            continue
        out.setdefault(c, set()).add((roc(str(r[ipub])), p[0], p[1], str(r[imeas] or "").strip()[:40],
                                      str(r[ireason] or "").strip()[:80]))
    return {c: [dict(zip(("pub", "from", "to", "measure", "reason"), t)) for t in sorted(v, key=lambda t: (t[1], t[2]))]
            for c, v in out.items()}


def disposal_state(items: Iterable[Mapping[str, Any]], T: str) -> dict[str, Any] | None:
    """T 當日生效中（from ≤ T ≤ to）優先，否則最近一筆將生效（from > T）；都沒有回 None。"""
    items = list(items)
    act = [x for x in items if x["from"] <= T <= x["to"]]
    if act:
        return {"st": "active", **max(act, key=lambda x: x["to"])}
    up = [x for x in items if x["from"] > T]
    if up:
        return {"st": "upcoming", **min(up, key=lambda x: x["from"])}
    return None


# ---------- 停券預告（融券回補日） ----------

def parse_twse_bfi84u(rows: Any) -> dict[str, dict[str, Any]]:
    """TWSE OpenAPI `exchangeReport/BFI84U`（停資停券預告；只有一組起訖）→ {代號: {from, to, reason}}。"""
    out: dict[str, dict[str, Any]] = {}
    for r in rows if isinstance(rows, list) else []:
        c = code_of((r or {}).get("Code"))
        a, b = roc((r or {}).get("StartDate")), roc((r or {}).get("EndDate"))
        if c and a and b:
            out[c] = {"from": a, "to": b, "reason": str(r.get("Reason") or "").strip()[:40]}
    return out


def parse_tpex_term(rows: Any) -> dict[str, dict[str, Any]]:
    """TPEx OpenAPI `tpex_margin_trading_term`（欄名「停券起日(最後回補日)」）→ {代號: {from, to, reason}}。"""
    out: dict[str, dict[str, Any]] = {}
    for r in rows if isinstance(rows, list) else []:
        c = code_of((r or {}).get("SecuritiesCompanyCode"))
        a, b = roc((r or {}).get("ShortSaleSuspensionStartDate")), roc((r or {}).get("ShortSaleSuspensionEndDate"))
        if c and a and b:
            out[c] = {"from": a, "to": b, "reason": str(r.get("Reason") or "").strip()[:40]}
    return out


# ---------- 停牌／暫停交易 ----------

def halted_twse(rows: Any, T: str) -> dict[str, dict[str, Any]]:
    """TWSE OpenAPI `exchangeReport/TWTAWU`（最近一次公告區間）：暫停日 ≤ T 且（無恢復日或恢復日 > T）→ 停牌中。"""
    out: dict[str, dict[str, Any]] = {}
    for r in rows if isinstance(rows, list) else []:
        c, a, b = code_of((r or {}).get("Code")), roc((r or {}).get("TradingHaltDate")), roc((r or {}).get("TradingResumptionDate"))
        if c and a and a <= T and (b is None or b > T):
            out[c] = {"from": a, "resume": b}
    return out


def halted_tpex(rows: Any, T: str) -> dict[str, dict[str, Any]]:
    """TPEx OpenAPI `tpex_spendi_history`（本民國年事件；每列只填暫停或恢復其一）：依 Serial 序走訪，
    最後一個 ≤ T 的事件是暫停 → 停牌中（同列或之後有 > T 的恢復日則附上）。"""
    ev: dict[str, list[tuple[int, str, str]]] = {}
    for r in rows if isinstance(rows, list) else []:
        c = code_of((r or {}).get("SecuritiesCompanyCode"))
        try:
            s = int(str(r.get("Serial")).strip())
        except (TypeError, ValueError):
            continue
        a, b = roc(r.get("DateOfSuspendedTrading")), roc(r.get("DateOfResumedTrading"))
        if c and a:
            ev.setdefault(c, []).append((s, "halt", a))
        if c and b:
            ev.setdefault(c, []).append((s, "resume", b))
    out: dict[str, dict[str, Any]] = {}
    for c, es in ev.items():
        es.sort()
        past = [e for e in es if e[2] <= T]
        if past and past[-1][1] == "halt":
            fut = [e[2] for e in es if e[1] == "resume" and e[2] > T]
            out[c] = {"from": past[-1][2], "resume": min(fut) if fut else None}
    return out


# ---------- 全額交割（變更交易）、櫃買 cmode 停止買賣 ----------

def parse_twse_altered(rows: Any) -> set[str]:
    """TWSE OpenAPI `exchangeReport/TWT85U`：表列即變更交易（全額交割）。"""
    return {c for c in (code_of((r or {}).get("Code")) for r in (rows if isinstance(rows, list) else [])) if c}


def parse_tpex_cmode(rows: Any) -> tuple[set[str], set[str]]:
    """TPEx OpenAPI `tpex_cmode` → (變更交易＝AlteredTrading 非空, 停止買賣＝SuspensionOfTrading 非空)。"""
    alt, sus = set(), set()
    for r in rows if isinstance(rows, list) else []:
        c = code_of((r or {}).get("SecuritiesCompanyCode"))
        if not c:
            continue
        if marked(r.get("AlteredTrading")):
            alt.add(c)
        if marked(r.get("SuspensionOfTrading")):
            sus.add(c)
    return alt, sus
