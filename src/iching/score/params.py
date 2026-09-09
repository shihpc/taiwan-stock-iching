"""子指標參數字典（P1-B2 §B2.8 機器可讀格式）＋族／爻權重（B1.7／B2.7）＋版本三元組的 `model_version`。

**全部 c／d／權重／錨點皆為 P1 起點值、`calibrated=False`**（CLAUDE.md 約定 4：校準母體限訓練段——本項不校準）。

鍵維度依 `spec/dimensions.json` `targets.indicator_params.key`
＝ `market × scope × horizon × line × family × indicator_id`；本模組以 `(scope, horizon, line, family, indicator_id)`
為字典鍵、`market` 由 `ParamSet.market` 承載（**每個市場各建一份 `ParamSet`**，B1 明令「不得共用同一份設定物件」）。

三份「同一張查表」的規定（B1 §B1.1 註）：距離型 d（MA5 0.6／MA10 0.8／MA20 1.0／MA60 1.5）由 `ParamSet.distance_d`
一個物件承載，B1.1 大盤距離、B2.2 個股距離、B1.6 A2 的 S&P 距離三處都引用它（同市場內同一物件）。
**大盤斜率族與個股斜率族各自一份**（`market_slope_d` vs `stock_slope_d`），B1 ⚠ 明說不可共用。
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from typing import Any

from .transform import L_DECLARED_RANGE, PCT_RANGE, S_RANGE

MARKETS = ("twse", "tpex")
HORIZONS = ("short", "swing", "mid")
LINES = ("1", "2", "3", "4", "5", "6")
SCOPE_MARKET = "market_index"
SCOPE_STOCK = "stock"

# 規則版本：改任何公式／權重／缺值規則都要 bump；與參數指紋一起構成 model_version
RULES_VERSION = "p2-score-engine-1"


@dataclass(frozen=True)
class Param:
    """B2.8 欄位（market 由 ParamSet 承載；version 由 model_version 承載）。"""
    indicator_id: str
    line: str
    family: str
    horizon: str
    scope: str
    transform: str                      # S | L | P_hist | P_hist_rev | scenario | passthrough
    c: float | None = None
    d: float | None = None
    native_range: tuple[float, float] = S_RANGE
    clip_policy: str = "clip_3d"        # clip_3d | n/a
    direction: int = 1                  # +1 / −1（反向）
    unit: str = ""
    window: Any = None                  # 視窗（int 或 tuple；語意見 formula）
    formula: str = ""
    anchors: tuple[float, ...] | None = None   # L 的三錨點
    scored: bool = True
    sub_weight: float = 1.0             # 族內權重（等權者 1.0）
    missing_rule: str = ""
    denominator_rule: str = ""
    source_dataset: str = ""
    available_at_rule: str = ""
    calibrated: bool = False
    note: str = ""


@dataclass
class ParamSet:
    market: str
    distance_d: dict[int, float]
    market_slope_d: dict[int, float]
    stock_slope_d: dict[int, float]
    params: dict[tuple[str, str, str, str, str], Param]
    family_weights: dict[tuple[str, str, str], dict[str, float]]   # (scope, horizon, line) -> {family: w}
    line_weights: dict[tuple[str, str], dict[str, float]]          # (scope, horizon) -> {line: w}
    calibrated: bool = False

    def get(self, scope: str, horizon: str, line: str, family: str, indicator_id: str) -> Param:
        return self.params[(scope, horizon, line, family, indicator_id)]

    def family(self, scope: str, horizon: str, line: str, family: str) -> list[Param]:
        return [p for k, p in self.params.items() if k[:4] == (scope, horizon, line, family)]

    def fingerprint(self) -> str:
        payload = {
            "rules": RULES_VERSION,
            "market": self.market,
            "distance_d": self.distance_d,
            "market_slope_d": self.market_slope_d,
            "stock_slope_d": self.stock_slope_d,
            "params": {"|".join(k): asdict(v) for k, v in sorted(self.params.items())},
            "family_weights": {"|".join(k): v for k, v in sorted(self.family_weights.items())},
            "line_weights": {"|".join(k): v for k, v in sorted(self.line_weights.items())},
        }
        s = json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str)
        return hashlib.sha256(s.encode("utf-8")).hexdigest()

    def model_version(self) -> str:
        """B3.1 #9：`model_version` 含各子指標 c／d／native_range 設定 → 由參數指紋導出；改任一參數即改版本。"""
        return f"{RULES_VERSION}.{self.fingerprint()[:12]}"

    def with_param(self, scope: str, horizon: str, line: str, family: str, indicator_id: str, **changes) -> "ParamSet":
        """回傳改了一個參數的**新** ParamSet（測試版本綁定用；不改原物件）。"""
        import dataclasses
        new = dict(self.params)
        key = (scope, horizon, line, family, indicator_id)
        new[key] = dataclasses.replace(new[key], **changes)
        return ParamSet(self.market, dict(self.distance_d), dict(self.market_slope_d), dict(self.stock_slope_d),
                        new, dict(self.family_weights), dict(self.line_weights), self.calibrated)


# ---------------------------------------------------------------------------
# 起點值（逐項附 spec 出處）
# ---------------------------------------------------------------------------
DISTANCE_D_START = {5: 0.6, 10: 0.8, 20: 1.0, 60: 1.5}        # B1.1／B2.2 距離族查表（MA5／MA10 未校準候選）
MARKET_SLOPE_D_START = {5: 0.5, 10: 0.7, 20: 1.0}               # B1.1 族 B：MA20 n 日變化 ÷ ATR14
STOCK_SLOPE_D_START = {5: 0.5, 10: 0.7, 20: 1.0}                # B2.2 族 B：MA_長 n 日變化 ÷ ATR14（另一份物件）

# B1 三期間視窗
MKT_L1_WIN = {"short": (5, 20, 5, 20), "swing": (10, 20, 10, 20), "mid": (20, 60, 20, 60)}   # (MA_短, MA_長, 斜率n, 區間n)
MKT_L2_WIN = {"short": (5, 20, 1, 10), "swing": (10, 20, 1, 20), "mid": (20, 60, 5, 60)}     # (MA_短, MA_長, 漲跌家數均n, 新高低n=騰落n)
MKT_L3_WIN = {"short": (1, 20, 1, 10), "swing": (5, 20, 5, 20), "mid": (20, 60, 20, 60)}     # (量能分子n, 分母n, 上漲占比n, 背離n)
MKT_WIN_N = {"short": 5, "swing": 10, "mid": 20}                                             # B1.4／B1.5 A2／B1.6 期間視窗
MKT_L4_D_FOREIGN = {"short": 1.0, "swing": 0.7, "mid": 0.5}     # %
MKT_L4_D_TRUST = {"short": 0.20, "swing": 0.15, "mid": 0.10}   # %
MKT_L6_D_SPX = {"short": 1.5, "swing": 2.1, "mid": 3.0}         # %
MKT_L6_D_SOX = {"short": 2.5, "swing": 3.5, "mid": 5.0}         # %
MKT_L6_D_FX = {"short": 0.30, "swing": 0.42, "mid": 0.60}       # %
MKT_L6_MA = {"short": 5, "swing": 10, "mid": 20}

# B2 三期間視窗
STK_L2_WIN = {"short": (5, 20, 5, 20, 2), "swing": (10, 20, 10, 20, 3), "mid": (20, 60, 20, 60, 5)}   # (MA_短, MA_長, 斜率n, 結構窗, 擺動k)
STK_L3_WIN = {"short": (5, 10), "swing": (10, 20), "mid": (20, 60)}    # (短視窗, 長視窗)；加速度兩段各＝短視窗
STK_L3_D = {"short": (3.0, 4.0, 4.0, 3.0), "swing": (4.0, 5.0, 5.0, 4.0), "mid": (5.0, 8.0, 8.0, 5.0)}   # (短, 長, 產業, 加速度) pp
STK_L4_WIN = {"short": (5, 5, 20, 3), "swing": (10, 10, 60, 5), "mid": (20, 20, 120, 10)}   # (回撤n＝序1, 收盤位置n, 延續基準, 確認窗)
STK_L5_WIN = {"short": (3, 5, 5), "swing": (5, 10, 10), "mid": (10, 20, 20)}     # (短視窗, 長視窗, 持續性／融資／借券視窗)
STK_L5_D = {"short": (5.0, 4.0, 1.0), "swing": (4.0, 3.0, 2.0), "mid": (3.0, 2.0, 3.34)}   # (短視窗%, 長視窗%, 持續性天)
STK_L6_WIN = {"short": 5, "swing": 10, "mid": 20}
STK_L6_D = {"short": 3.0, "swing": 4.0, "mid": 5.0}

MARKET_LINE_WEIGHTS = {   # B1.7
    "short": {"1": .25, "2": .20, "3": .20, "4": .15, "5": .10, "6": .10},
    "swing": {"1": .30, "2": .20, "3": .15, "4": .15, "5": .10, "6": .10},
    "mid":   {"1": .30, "2": .20, "3": .10, "4": .15, "5": .10, "6": .15},
}
STOCK_LINE_WEIGHTS = {    # B2.7
    "short": {"1": .05, "2": .25, "3": .20, "4": .20, "5": .20, "6": .10},
    "swing": {"1": .10, "2": .25, "3": .20, "4": .15, "5": .20, "6": .10},
    "mid":   {"1": .20, "2": .25, "3": .15, "4": .10, "5": .15, "6": .15},
}


def _mk_market(market: str, dist: dict[int, float], mslope: dict[int, float]) -> tuple[dict, dict]:
    P: dict[tuple, Param] = {}
    FW: dict[tuple, dict[str, float]] = {}
    sc = SCOPE_MARKET

    def add(p: Param) -> None:
        key = (p.scope, p.horizon, p.line, p.family, p.indicator_id)
        assert key not in P, key
        P[key] = p

    for h in HORIZONS:
        ma_s, ma_l, slope_n, range_n = MKT_L1_WIN[h]
        # ---- B1.1 初爻 趨勢
        add(Param("dist_ma_short", "1", "A", h, sc, "S", 0.0, dist[ma_s], unit="ATR", window=ma_s,
                  formula="(I − MA_短)/ATR14_{t−1}", denominator_rule="ATR14=0 → denominator_zero",
                  source_dataset="TaiwanStockPrice(TAIEX/TPEx)", available_at_rule="T+0 盤後"))
        add(Param("dist_ma_long", "1", "A", h, sc, "S", 0.0, dist[ma_l], unit="ATR", window=ma_l,
                  formula="(I − MA_長)/ATR14_{t−1}", denominator_rule="ATR14=0 → denominator_zero"))
        add(Param("ma20_slope", "1", "B", h, sc, "S", 0.0, mslope[slope_n], unit="ATR", window=slope_n,
                  formula="MA20 n 日變化 ÷ ATR14_{t−1}", denominator_rule="ATR14=0 → denominator_zero",
                  note="B1.1 斜率族起點值 0.5/0.7/1.0，實測中位數 0.62/1.11/2.02，P2 依 p85÷3 重定"))
        add(Param("range_position", "1", "C", h, sc, "L", native_range=L_DECLARED_RANGE, clip_policy="n/a",
                  window=range_n, anchors=(0.0, 0.5, 1.0), formula="(I − min_n)/(max_n − min_n)",
                  denominator_rule="max=min → denominator_zero"))
        FW[(sc, h, "1")] = {"A": .40, "B": .30, "C": .30}
        # ---- B1.2 二爻 廣度
        ma_s, ma_l, adv_n, n = MKT_L2_WIN[h]
        add(Param("above_ma_short_ratio", "2", "A", h, sc, "L", native_range=L_DECLARED_RANGE, clip_policy="n/a",
                  window=ma_s, anchors=(0.30, 0.50, 0.70), formula="站上 MA_短 家數 ÷ N", unit="ratio",
                  source_dataset="TaiwanStockPrice 全市場切片"))
        add(Param("above_ma_long_ratio", "2", "A", h, sc, "L", native_range=L_DECLARED_RANGE, clip_policy="n/a",
                  window=ma_l, anchors=(0.30, 0.50, 0.70), formula="站上 MA_長 家數 ÷ N", unit="ratio"))
        add(Param("advance_ratio", "2", "B", h, sc, "L", native_range=L_DECLARED_RANGE, clip_policy="n/a",
                  window=adv_n, anchors=(0.40, 0.50, 0.60), formula="上漲家數 ÷ N（中期取 5 日平均）", unit="ratio"))
        add(Param("new_high_low_ratio", "2", "C", h, sc, "S", 0.0, 3.0, unit="%", window=n,
                  formula="(n 日新高家數 − n 日新低家數) ÷ N × 100"))
        add(Param("ad_line_dev", "2", "D", h, sc, "S", 0.0, 1.0, unit="sd", window=n,
                  formula="((AD − MA_n(AD)) ÷ N) ÷ 該量 n 日標準差", denominator_rule="std=0 → denominator_zero",
                  note="SPEC-NOTE 解析見 market.py ind_ad_line_dev"))
        FW[(sc, h, "2")] = {"A": .40, "B": .20, "C": .20, "D": .20}
        # ---- B1.3 三爻 量價參與
        num_n, den_n, up_n, div_n = MKT_L3_WIN[h]
        add(Param("amount_ratio", "3", "A", h, sc, "S", 1.0, 0.30, unit="ratio", window=(num_n, den_n),
                  formula="AMT 分子均 ÷ AMTMA_分母（取 T−1 為止）", denominator_rule="AMTMA=0 → denominator_zero",
                  source_dataset="TWSE FMTQIK / TPEx tradingIndex"))
        add(Param("up_amount_ratio", "3", "B", h, sc, "L", native_range=L_DECLARED_RANGE, clip_policy="n/a",
                  window=up_n, anchors=(0.35, 0.50, 0.65), formula="上漲股成交金額 ÷ 總成交金額（n 日均）", unit="ratio"))
        add(Param("divergence_scenario", "3", "C", h, sc, "scenario", native_range=PCT_RANGE, clip_policy="n/a",
                  window=div_n, formula="B1.3 族 C 情境表：n 日新高／新低 × AMT vs AMTMA_n",
                  denominator_rule="max=min → denominator_zero"))
        FW[(sc, h, "3")] = {"A": .40, "B": .30, "C": .30}
        # ---- B1.4 四爻 現貨資金
        n = MKT_WIN_N[h]
        add(Param("foreign_net_ratio", "4", "A", h, sc, "S", 0.0, MKT_L4_D_FOREIGN[h], unit="%", window=n,
                  formula="Σ外資淨買超金額 ÷ Σ市場成交金額 × 100", source_dataset="TWSE BFI82U / TPEx summary",
                  denominator_rule="Σ成交金額=0 → denominator_zero"))
        add(Param("trust_net_ratio", "4", "B", h, sc, "S", 0.0, MKT_L4_D_TRUST[h], unit="%", window=n,
                  formula="Σ投信淨買超金額 ÷ Σ市場成交金額 × 100"))
        add(Param("foreign_buy_days", "4", "C", h, sc, "S", 0.0, 2.0, unit="day", window=10,
                  formula="近 10 日外資買超天數 − 5"))
        add(Param("margin_change", "4", "D", h, sc, "S", 0.0, 2.0, unit="%", window=n, direction=-1,
                  formula="全市場融資餘額 n 日變化率 × 100（反向）", source_dataset="TaiwanStockTotalMarginPurchaseShortSale",
                  denominator_rule="M_{t−n}=0 → denominator_zero"))
        FW[(sc, h, "4")] = {"A": .35, "B": .25, "C": .20, "D": .20}
        # ---- B1.5 五爻 衍生品
        add(Param("foreign_net_oi_phist", "5", "A", h, sc, "P_hist", native_range=PCT_RANGE, clip_policy="n/a",
                  window=250, sub_weight=.5, formula="外資台指期淨未平倉口數 P_hist(250)",
                  source_dataset="TaiwanFuturesInstitutionalInvestors"))
        add(Param("foreign_net_oi_change", "5", "A", h, sc, "S", 0.0, 5000.0, unit="口", window=n, sub_weight=.5,
                  formula="淨未平倉 n 日變化"))
        add(Param("basis", "5", "B", h, sc, "S", None, 0.30, unit="%", window=60,
                  formula="(近月期指 − 現貨)/現貨 × 100；c＝近 60 日中位數（B1.5.1）", source_dataset="TaiwanFuturesDaily",
                  missing_rule="換月日 contract_rolled → 缺值"))
        add(Param("vix_phist_rev", "5", "C", h, sc, "P_hist_rev", native_range=PCT_RANGE, clip_policy="n/a",
                  window=250, formula="100 − P_hist(250)（已內含反向，方向欄勿再取負）", source_dataset="TaiwanOptionVix"))
        add(Param("put_call_ratio", "5", "—", h, sc, "passthrough", scored=False, clip_policy="n/a",
                  formula="只顯示不計分（B1.5.2）", source_dataset="TAIFEX PutCallRatio"))
        FW[(sc, h, "5")] = {"A": .40, "B": .30, "C": .30}
        # ---- B1.6 上爻 外部（美股曆）
        add(Param("spx_return", "6", "A", h, sc, "S", 0.0, MKT_L6_D_SPX[h], unit="%", window=n, sub_weight=.35,
                  formula="S&P 500 n 個美股交易日報酬 × 100", source_dataset="USStockPrice(^GSPC)",
                  available_at_rule="截至台北 T 日 08:00 已收盤的最近美股交易日"))
        add(Param("spx_ma_distance", "6", "A", h, sc, "S", 0.0, dist[MKT_L6_MA[h]], unit="ATR", window=MKT_L6_MA[h],
                  sub_weight=.30, formula="(SPX − MA_n)/ATR14_{t−1}（距離型，d 依 MA 窗長查表，同 distance_d）"))
        add(Param("sox_return", "6", "A", h, sc, "S", 0.0, MKT_L6_D_SOX[h], unit="%", window=n, sub_weight=.35,
                  formula="費城半導體 n 個美股交易日報酬 × 100", source_dataset="USStockPrice(^SOX)"))
        add(Param("usdtwd_change", "6", "B", h, sc, "S", 0.0, MKT_L6_D_FX[h], unit="%", window=n, direction=-1,
                  formula="USD/TWD n 期變化 × 100（反向：台幣升值偏多）", source_dataset="TaiwanExchangeRate(USD)"))
        FW[(sc, h, "6")] = {"A": .50, "B": .50}
    return P, FW


def _mk_stock(market: str, dist: dict[int, float], sslope: dict[int, float]) -> tuple[dict, dict]:
    P: dict[tuple, Param] = {}
    FW: dict[tuple, dict[str, float]] = {}
    sc = SCOPE_STOCK

    def add(p: Param) -> None:
        key = (p.scope, p.horizon, p.line, p.family, p.indicator_id)
        assert key not in P, key
        P[key] = p

    for h in HORIZONS:
        # ---- B2.1 初爻 營運基礎
        if h == "short":
            add(Param("revenue_yoy", "1", "A", h, sc, "S", 0.0, 20.0, unit="pp", window=1,
                      formula="最新單月營收 YoY × 100", source_dataset="TaiwanStockMonthRevenue",
                      available_at_rule="B2.1 時間對齊表（create_time / 次月 10 日收盤後）",
                      missing_rule="無月營收 → 族 A 缺"))
        else:
            add(Param("revenue_yoy", "1", "A", h, sc, "S", 0.0, 15.0, unit="pp", window=3,
                      formula="近 3 月合計 ÷ 去年同期 3 月合計 − 1 × 100", source_dataset="TaiwanStockMonthRevenue"))
        add(Param("revenue_accel", "1", "A", h, sc, "S", 0.0, 10.0, unit="pp", window=3,
                  formula="近 3 月合計 YoY − 前一組 3 月合計 YoY"))
        if h == "mid":
            add(Param("revenue_high_12m", "1", "A", h, sc, "passthrough", clip_policy="n/a", window=12,
                      formula="最新月營收為近 12 月最高 → 族分 max(base, 84.16)（下限）"))
            add(Param("eps_yoy", "1", "B", h, sc, "S", 0.0, 20.0, unit="pp",
                      formula="季 EPS YoY × 100（僅前期 EPS > 0.1）", source_dataset="TaiwanStockFinancialStatements",
                      available_at_rule="法定期限近似 5/15、8/14、11/14、3/31 收盤後"))
            add(Param("eps_diff_over_price", "1", "B", h, sc, "S", 0.0, 2.0, unit="%",
                      formula="(本期EPS − 去年同期EPS) ÷ 期末股價 × 100（前期 EPS ≤ 0.1 或由負轉正）"))
            add(Param("gross_margin_qoq", "1", "B", h, sc, "S", 0.0, 1.0, unit="pp", formula="本季毛利率 − 上季毛利率"))
            add(Param("pretax_income_yoy", "1", "B", h, sc, "S", 0.0, 20.0, unit="pp",
                      formula="金融保險業替代：稅前淨利 YoY × 100"))
            add(Param("equity_qoq", "1", "B", h, sc, "S", 0.0, 2.0, unit="%", formula="金融保險業替代：淨值 QoQ × 100"))
            add(Param("revenue_yoy_vs_industry", "1", "C", h, sc, "S", 0.0, 10.0, unit="pp",
                      formula="三月 YoY − 同產業中位數", missing_rule="產業樣本 < 5 → 族缺"))
            FW[(sc, h, "1")] = {"A": .50, "B": .30, "C": .20}
        else:
            FW[(sc, h, "1")] = {"A": 1.0}
        # ---- B2.2 二爻 價格趨勢
        ma_s, ma_l, slope_n, struct_w, k = STK_L2_WIN[h]
        add(Param("dist_ma_short", "2", "A", h, sc, "S", 0.0, dist[ma_s], unit="ATR", window=ma_s,
                  formula="(C − MA_短)/ATR14_{t−1}", denominator_rule="ATR14=0 → denominator_zero"))
        add(Param("dist_ma_long", "2", "A", h, sc, "S", 0.0, dist[ma_l], unit="ATR", window=ma_l,
                  formula="(C − MA_長)/ATR14_{t−1}"))
        add(Param("ma_long_slope", "2", "B", h, sc, "S", 0.0, sslope[slope_n], unit="ATR", window=(ma_l, slope_n),
                  formula="MA_長 n 日變化 ÷ ATR14_{t−1}", note="個股斜率族設定物件，與大盤斜率族分開"))
        add(Param("structure", "2", "C", h, sc, "scenario", native_range=PCT_RANGE, clip_policy="n/a",
                  window=(struct_w, k), formula="HH＋HL→80、LH＋LL→20、其他→50；擺動點前後 k 根確認"))
        FW[(sc, h, "2")] = {"A": .40, "B": .30, "C": .30}
        # ---- B2.3 三爻 相對動能
        ws, wl = STK_L3_WIN[h]
        d_s, d_l, d_ind, d_acc = STK_L3_D[h]
        add(Param("excess_long", "3", "A", h, sc, "S", 0.0, d_l, unit="pp", window=wl, formula="長視窗超額報酬（對所屬市場指數）"))
        add(Param("excess_short", "3", "A", h, sc, "S", 0.0, d_s, unit="pp", window=ws, formula="短視窗超額報酬"))
        add(Param("excess_vs_industry", "3", "B", h, sc, "S", 0.0, d_ind, unit="pp", window=wl,
                  formula="長視窗超額（對產業中位）", missing_rule="同產業有效樣本 < 5 → 族缺"))
        add(Param("excess_accel", "3", "C", h, sc, "S", 0.0, d_acc, unit="pp", window=ws,
                  formula="最近 ws 日超額 − 其前 ws 日超額"))
        FW[(sc, h, "3")] = {"A": .50, "B": .25, "C": .25}
        # ---- B2.4 四爻 量價確認
        dd_n, cp_n, base_n, confirm_k = STK_L4_WIN[h]
        if h == "mid":
            add(Param("updown_volume_ratio", "4", "A", h, sc, "S", 0.0, 0.3, unit="ln", window=20, sub_weight=.5,
                      formula="ln(近 20 日上漲日均量 ÷ 下跌日均量)", denominator_rule="無上漲日或無下跌日 → denominator_zero"))
            add(Param("obv_slope", "4", "A", h, sc, "S", 0.0, 0.5, unit="ratio", window=20, sub_weight=.5,
                      formula="OBV 20 日最小平方斜率 ÷ VMA20_{t−1}", denominator_rule="VMA20=0 → denominator_zero"))
        else:
            add(Param("volume_scenario", "4", "A", h, sc, "scenario", native_range=PCT_RANGE, clip_policy="n/a",
                      window=dd_n, formula="B2.4 族 A 情境表；短線 0.6×當日＋0.4×近 5 日均、波段近 10 日均",
                      missing_rule="ATR14／VMA20 缺或 0、當日無成交 → 缺值"))
        add(Param("close_position", "4", "B", h, sc, "L", native_range=L_DECLARED_RANGE, clip_policy="n/a",
                  window=cp_n, anchors=(0.0, 0.5, 1.0), formula="n 日均 (C−L)/(H−L)，H=L 日不計入"))
        add(Param("continuation", "4", "C", h, sc, "scenario", native_range=PCT_RANGE, clip_policy="n/a",
                  window=(base_n, confirm_k), formula="突破／跌破後第 k 日守住 → 80／20；否則 50"))
        FW[(sc, h, "4")] = {"A": .50, "B": .20, "C": .30}
        # ---- B2.5 五爻 籌碼供需
        ws5, wl5, wn5 = STK_L5_WIN[h]
        ds5, dl5, dp5 = STK_L5_D[h]
        for fam, who in (("A", "foreign"), ("B", "trust")):
            add(Param(f"{who}_strength_long", "5", fam, h, sc, "S", 0.0, dl5, unit="%", window=wl5, sub_weight=.6,
                      formula="期間淨買超股數 ÷ 同期間成交股數 × 100", source_dataset="TaiwanStockInstitutionalInvestorsBuySell",
                      denominator_rule="Σ成交股數=0 → denominator_zero"))
            add(Param(f"{who}_strength_short", "5", fam, h, sc, "S", 0.0, ds5, unit="%", window=ws5, sub_weight=.4,
                      formula="同上（短視窗）"))
        add(Param("foreign_persistence", "5", "C", h, sc, "S", 0.0, dp5, unit="day", window=wn5,
                  formula="近 n 日外資買超天數 − n/2", note="3d ≥ 值域上界，截斷永不觸發（v1.2.2 §4.1a）"))
        add(Param("margin_scenario", "5", "D", h, sc, "scenario", native_range=S_RANGE, clip_policy="clip_3d",
                  c=0.0, d=5.0, unit="%", window=wn5,
                  formula="S1 §A2.1 有序情境；序 3／4 為 S(−r; 0, 5%) 及其半幅仿射，原生值域即 S 值域、N 恆等",
                  missing_rule="無信用交易資格 → 族缺"))
        add(Param("short_sale_change", "5", "E", h, sc, "S", 0.0, 0.3, unit="pp", window=wn5, direction=-1,
                  formula="借券賣出餘額 n 日變化 ÷ 流通股數 × 100（反向）", source_dataset="TaiwanDailyShortSaleBalances"))
        FW[(sc, h, "5")] = {"A": .30, "B": .30, "C": .15, "D": .10, "E": .15}
        # ---- B2.6 上爻 外部支持
        add(Param("market_direction", "6", "A", h, sc, "passthrough", native_range=S_RANGE, clip_policy="n/a",
                  formula="所屬市場該期間的大盤方向分數（B1.7）；恆等映射"))
        add(Param("industry_relative_return", "6", "B", h, sc, "S", 0.0, STK_L6_D[h], unit="pp", window=STK_L6_WIN[h],
                  sub_weight=.6, formula="產業期間相對大盤報酬（中位數）"))
        add(Param("industry_above_ma20_ratio", "6", "B", h, sc, "L", native_range=L_DECLARED_RANGE, clip_policy="n/a",
                  window=20, anchors=(0.30, 0.50, 0.70), sub_weight=.4, formula="產業內站上 MA20 家數比"))
        FW[(sc, h, "6")] = {"A": .50, "B": .50}
    return P, FW


def build_params(market: str) -> ParamSet:
    """每個市場各建一份（即使目前值相同）。B1 序言：「兩市場的參數必須從各自的參數表讀取，不得共用同一份設定物件」。"""
    if market not in MARKETS:
        raise ValueError(f"market must be one of {MARKETS}, got {market!r}")
    dist = dict(DISTANCE_D_START)
    mslope = dict(MARKET_SLOPE_D_START)
    sslope = dict(STOCK_SLOPE_D_START)
    pm, fwm = _mk_market(market, dist, mslope)
    ps, fws = _mk_stock(market, dist, sslope)
    params = {**pm, **ps}
    fw = {**fwm, **fws}
    lw = {(SCOPE_MARKET, h): dict(MARKET_LINE_WEIGHTS[h]) for h in HORIZONS}
    lw.update({(SCOPE_STOCK, h): dict(STOCK_LINE_WEIGHTS[h]) for h in HORIZONS})
    return ParamSet(market=market, distance_d=dist, market_slope_d=mslope, stock_slope_d=sslope,
                    params=params, family_weights=fw, line_weights=lw, calibrated=False)


def all_param_rows(ps: ParamSet) -> list[dict]:
    """B2.8 人類可讀版：每子指標一列（含 market 與 version），供報表／校準登錄。"""
    rows = []
    mv = ps.model_version()
    for (scope, h, line, fam, iid), p in sorted(ps.params.items()):
        r = asdict(p)
        r["market"] = ps.market
        r["version"] = mv
        rows.append(r)
    return rows
