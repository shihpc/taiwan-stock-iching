"""測試專用：裁定 #68 **之前**的 `revenue_yoy_3m`（只擋去年同期合計恰為 0），供 §29／§30 量測工具的測試照舊跑。

出處：commit `a0b2eca` 的 `src/iching/score/stock.py` `revenue_yoy_3m`（函式本體逐字照抄，只改名）。
`scripts/revenue_base_impact.py`／`scripts/revenue_negative_base.py` 量的是 #68 前的語意，在現行碼下會明確拒跑
（`docs/P3-CALIBRATION.md` §31）；它們原本的邏輯測試改為在 `pre68_semantics()` 之內執行——把
`iching.score.stock` 與 `iching.fundamentals` 兩個模組上的 `revenue_yoy_3m` 名稱**同時**換成本檔的舊版
（兩者必須是同一支函式，§30 的守門 3 即斷言這點），`extra` 另給要一併換掉的模組屬性（例如
`revenue_negative_base.ORIG_YOY`）。**不是生產碼**，只在測試行程內生效、離開即還原。
"""
from __future__ import annotations

from contextlib import contextmanager

from iching import fundamentals as FUND
from iching.score import stock as STK
from iching.score.stock import MONTHS_PER_YEAR, _ym_shift
from iching.score.transform import REASON_DENOM_ZERO, REASON_MISSING, Missing


def revenue_yoy_3m_pre68(rev: dict[str, float], latest: str, offset_months: int = 0, months: int = 3) -> float | Missing:
    """近 `months`（3）月合計 ÷ 去年同期合計 − 1（×100 pp）。`offset_months` 往前平移（加速度的前一組用 `months`）。"""
    ms = [_ym_shift(latest, offset_months + i) for i in range(months)]
    ly = [_ym_shift(m, MONTHS_PER_YEAR) for m in ms]
    if any(m not in rev for m in ms + ly):
        return Missing(REASON_MISSING, "revenue months incomplete")
    num = sum(rev[m] for m in ms)
    den = sum(rev[m] for m in ly)
    if den == 0:
        return Missing(REASON_DENOM_ZERO, f"last-year {months}M sum=0")
    return (num / den - 1.0) * 100.0


#: 現行（#68 後）那一支，供「拒跑」測試在 pre68 環境裡臨時換回來。
CURRENT_YOY = STK.revenue_yoy_3m


@contextmanager
def pre68_semantics(mp, extra: tuple[tuple[object, str], ...] = ()):
    """`mp`＝`pytest.MonkeyPatch`（函式層的 `monkeypatch` 或 `MonkeyPatch.context()`）；離開時由它還原。"""
    mp.setattr(STK, "revenue_yoy_3m", revenue_yoy_3m_pre68)
    mp.setattr(FUND, "revenue_yoy_3m", revenue_yoy_3m_pre68)
    for obj, name in extra:
        mp.setattr(obj, name, revenue_yoy_3m_pre68)
    yield revenue_yoy_3m_pre68
