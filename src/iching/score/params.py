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
LINE2_SERIES_LEN = 10   # B3.1 #11：二爻分數序列 T−9…T（含當日）；B2.4 族 A 多日平均的天數上限


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


@dataclass(frozen=True)
class Rules:
    """非逐子指標的門檻／情境／旗標常數（**全部 calibrated=False、全部進 `model_version` 指紋**）。
    這裡的每個值都會改變輸出；不得再在 market.py／stock.py／hexagram.py／aggregate.py 內寫死（`tests/test_score_params_guard.py` 守門）。"""
    calibrated: bool = False
    unknown_below: float = 0.5                  # B1.7：coverage_ratio < 0.5 → 該爻未知
    stale_degrade_at: int = 3                   # B1.6：stale_days ≥ 3 → 上爻族 A 降級缺值
    # 遲滯（v1.2.2 §8）：首次 50 分界；陰→陽連續 2 日 ≥55、陽→陰連續 2 日 ≤45
    hysteresis_first: float = 50.0
    hysteresis_up: float = 55.0
    hysteresis_down: float = 45.0
    hysteresis_confirm_days: int = 2
    # B1.3 族 C 背離情境（序 1 新高縮量／序 2 新低放量 ≥1.5×／序 3 新低縮量 <0.8×／序 4 其他）
    divergence_high_amt_mult: float = 1.5
    divergence_low_amt_mult: float = 0.8
    divergence_scores: tuple[float, float, float, float] = (35.0, 25.0, 55.0, 50.0)
    # B2.1
    eps_yoy_min_base: float = 0.1               # 前期 EPS > 0.1 才用 YoY，否則用 EPS 差額÷股價
    revenue_high_floor_native: float = 90.0     # 創高下限（原生 90 → N → 84.16）
    industry_min_sample: int = 5                # SPEC-NOTE：初爻族 C 的「產業樣本 <5 判缺」借自 B2.3 族 B（B2.1 未另定；不在 §5 #13–23 裁決範圍）
    # B2.2 族 C 結構（HH+HL／LH+LL／其他）
    structure_scores: tuple[float, float, float] = (80.0, 20.0, 50.0)
    # B2.3 過熱旗標：P_cs ≥ 95 ∧ (C−MA20)/ATR > 3 → 封頂 N(85)=79.89
    overheat_cap_native: float = 85.0
    p_cs_overheat: float = 95.0
    overheat_dist_atr: float = 3.0
    # B2.4 族 A 情境表（S1 §A2.2 修訂版）
    vs_ratio_c: float = 0.3                     # 序 2／3 內嵌 S(量比−1; 0.3, 0.7)
    vs_ratio_d: float = 0.7
    vs_low_ratio: float = 0.8                   # 量比 < 0.8 縮量
    vs_high_ratio: float = 1.3                  # 量比 ≥ 1.3 放量
    vs_day_change: float = 0.5                  # |日變動| ≥ 0.5 ATR
    vs_drawdown_max: float = 2.0                # 序 1：0 < n 日回撤 ≤ 2
    vs_line2_min: float = 55.0                  # 序 1：二爻分 ≥ 55（水準門檻，不換算）
    vs_scores: tuple[float, float, float, float] = (60.0, 60.0, 40.0, 50.0)   # 序 1 縮量回檔／序 2 基底／序 3 基底／序 4-5
    vs_formula_half: float = 0.5                # 序 2／3：± 0.5 × (S − 50)
    vs_today_weight: float = 0.6                # 短線 0.6×當日 + 0.4×近 5 日均
    vs_avg_days_short: int = 5
    vs_avg_days_swing: int = 10
    # B2.4 族 C 延續（守住／未收復／其他）
    continuation_scores: tuple[float, float, float] = (80.0, 20.0, 50.0)
    # B2.5 族 D 融資（S1 §A2.1）：|r| < 0.5% 近零；序 4 半幅
    margin_near_zero_pct: float = 0.5
    margin_half: float = 0.5
    # B1.8 旗標（門檻位移＝十分位；名額乘數）；第一版 >1.0 視為 1.0、<0 視為 0
    flag_effects: dict = field(default_factory=lambda: {
        "F-臨界":     {"long": (0.5, 0.75), "short": (0.5, 0.75)},
        "F-高波動":   {"long": (1.0, 0.50), "short": (1.0, 0.50)},
        "F-分歧":     {"long": (0.5, 0.75), "short": (0.5, 0.75)},
        "F-廣度擴張": {"long": (0.0, 1.25), "short": (0.5, 0.75)},
        "F-廣度收縮": {"long": (0.5, 0.75), "short": (0.0, 1.25)},
    })
    breadth_change_threshold: float = 5.2       # 政策第 9 點（原 ±5 × 跨幅比 1.0394）
    shift_cap_deciles: float = 2.0              # 位移加總上限 +2 個十分位
    high_vol_pct: float = 80.0                  # VIX ≥ 自身 250 日 80 百分位
    critical_band: tuple[float, float] = (45.0, 55.0)
    trigram_hi: float = 55.0                    # 內外卦方向相反：一者 ≥55 且另一者 ≤45
    trigram_lo: float = 45.0
    insufficient_causes: int = 2                # B5.4：獨立缺因 ≥ 2 → 名額 ×0.5
    insufficient_multiplier: float = 0.5
    # ---- 規格缺口裁決（2026-09-10，使用者裁定全甲；正本 docs/P2-KICKOFF.md §5 第 13–23 列）。慣例本身可參數化者列於此、進指紋
    atr_method: str = "simple"                  # 裁定（2026-09-10，P2-KICKOFF §5 #13）：ATR14 用簡單平均（非 Wilder）；可選 "wilder"
    phist_include_today: bool = True            # 裁定（§5 #14）：P_hist 250 日視窗含當日
    phist_tie: str = "mid"                      # 裁定（§5 #14）：平手取中位名次 mid-rank；可選 "low"／"high"
    pct_interp: str = "linear"                  # 裁定（§5 #14）：門檻分位數線性內插（numpy method）；可選 "lower"／"higher"／"nearest"
    ad_std_ddof: int = 0                        # 裁定（§5 #15）：騰落線 x=dev/std_n(dev)，母體標準差 ddof=0
    basis_median_include_today: bool = True     # 裁定（§5 #16）：基差 c＝近 60 日中位數含當日
    stale_unit: str = "tpe_trading_days"        # 裁定（§5 #17）：stale_days 以台北交易日計（週一沿用上週五＝0）；可選 "calendar_days"
    swing_tie_counts: bool = True               # 裁定（§5 #18）：擺動點平手也計為波峰／波谷
    avg_include_today: bool = True              # 裁定（§5 #19）：多日情境分平均含當日
    avg_min_available_ratio: float = 0.5        # 裁定（§5 #19）：可得日不足一半 → 缺值
    fx_asof_rule: str = "us_asof"               # 裁定（§5 #21）：USD/TWD 取觀測日 ≤ 對齊美股日的最近一筆；可選 "tpe_prev_day"
    direction_unknown_policy: str = "missing"   # 裁定（§5 #22）：任一爻未知 → 方向分數缺值、不重配；可選 "reweight"
    family_missing_policy: str = "weighted"     # 裁定（§5 #23）：族內子指標缺 → 按權重重配（等權即算術平均）；可選 "equal_mean"

    def __post_init__(self) -> None:
        _enum = {"atr_method": ("simple", "wilder"), "phist_tie": ("mid", "low", "high"),
                 "pct_interp": ("linear", "lower", "higher", "nearest"), "stale_unit": ("tpe_trading_days", "calendar_days"),
                 "fx_asof_rule": ("us_asof", "tpe_prev_day"), "direction_unknown_policy": ("missing", "reweight"),
                 "family_missing_policy": ("weighted", "equal_mean")}
        for name, allowed in _enum.items():
            if getattr(self, name) not in allowed:
                raise ValueError(f"Rules.{name}={getattr(self, name)!r} must be one of {allowed}")
        if not (0.0 < self.avg_min_available_ratio <= 1.0) or self.ad_std_ddof not in (0, 1):
            raise ValueError("Rules.avg_min_available_ratio must be in (0, 1]; ad_std_ddof must be 0 or 1")
        # B2.4 族 A 多日平均只有 T−9…T 這 10 天的二爻分數可用（LINE2_SERIES_LEN）；超過會在 stock.py 的
        # `l2[LINE2_SERIES_LEN − 1 − j]` 靜默負索引取到錯的值（11–20），到 21 才 IndexError → 建構時就擋。
        for name in ("vs_avg_days_short", "vs_avg_days_swing"):
            v = getattr(self, name)
            if not (1 <= v <= LINE2_SERIES_LEN):
                raise ValueError(f"Rules.{name}={v} must be within 1..{LINE2_SERIES_LEN} (LINE2_SERIES_LEN)")
        if self.hysteresis_confirm_days < 1 or self.insufficient_causes < 1:
            raise ValueError("Rules.hysteresis_confirm_days / insufficient_causes must be >= 1")


RULES_START = Rules()


@dataclass
class ParamSet:
    market: str
    distance_d: dict[int, float]
    market_slope_d: dict[int, float]
    stock_slope_d: dict[int, float]
    params: dict[tuple[str, str, str, str, str], Param]
    family_weights: dict[tuple[str, str, str], dict[str, float]]   # (scope, horizon, line) -> {family: w}
    line_weights: dict[tuple[str, str], dict[str, float]]          # (scope, horizon) -> {line: w}
    rules: Rules = field(default_factory=Rules)
    calibrated: bool = False

    def get(self, scope: str, horizon: str, line: str, family: str, indicator_id: str) -> Param:
        return self.params[(scope, horizon, line, family, indicator_id)]

    def family(self, scope: str, horizon: str, line: str, family: str) -> list[Param]:
        return [p for k, p in self.params.items() if k[:4] == (scope, horizon, line, family)]

    def fingerprint(self) -> str:
        payload = {
            "rules": RULES_VERSION,
            "market": self.market,
            "calibrated": self.calibrated,
            "rule_constants": asdict(self.rules),
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
                        new, dict(self.family_weights), dict(self.line_weights), self.rules, self.calibrated)

    def with_rules(self, **changes) -> "ParamSet":
        """回傳改了 Rules 常數的**新** ParamSet（測試指紋綁定用）。"""
        import dataclasses
        return ParamSet(self.market, dict(self.distance_d), dict(self.market_slope_d), dict(self.stock_slope_d),
                        dict(self.params), dict(self.family_weights), dict(self.line_weights),
                        dataclasses.replace(self.rules, **changes), self.calibrated)

    def numeric_values(self) -> set[float]:
        """指紋內全部數值（供 `tests/test_score_params_guard.py`：程式內寫死的數字必須在此集合或白名單）。"""
        out: set[float] = set()

        def walk(v):
            if isinstance(v, bool):
                return
            if isinstance(v, (int, float)):
                out.add(float(v))
            elif isinstance(v, dict):
                for x in v.values():
                    walk(x)
            elif isinstance(v, (list, tuple)):
                for x in v:
                    walk(x)
        walk({"d": self.distance_d, "ms": self.market_slope_d, "ss": self.stock_slope_d,
              "p": {"|".join(k): asdict(v) for k, v in self.params.items()},
              "fw": {"|".join(k): v for k, v in self.family_weights.items()},
              "lw": {"|".join(k): v for k, v in self.line_weights.items()}, "r": asdict(self.rules)})
        return out


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
        add(Param("ma20_slope", "1", "B", h, sc, "S", 0.0, mslope[slope_n], unit="ATR", window=(20, slope_n),
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
            add(Param("revenue_yoy_vs_industry", "1", "C", h, sc, "S", 0.0, 10.0, unit="pp", window=3,
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
                      c=None, d=None, window=dd_n,
                      formula="B2.4 族 A 情境表；短線 0.6×當日＋0.4×近 5 日均、波段近 10 日均；內嵌 S(量比−1; rules.vs_ratio_c, rules.vs_ratio_d)",
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
                    params=params, family_weights=fw, line_weights=lw, rules=Rules(), calibrated=False)


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


# ---------------------------------------------------------------------------
# 非參數常數白名單（`tests/test_score_params_guard.py` 守門用）
# ---------------------------------------------------------------------------
# `src/iching/score/`（params.py 除外）裡出現的每個數字字面量，必須是 `ParamSet.numeric_values()` 的成員，或列在此處
# 並說明**為何不是參數**。值以 float 比對。
# **守門範圍（據實）**：只攔「與任何參數值／白名單都**不重合**的新字面量」——把 `rules.vs_ratio_c` 改回寫死 `0.3`、
# `rules.unknown_below` 改回 `0.5` 這類**同值寫死**攔不到（0.3／0.5 本來就是某個參數值）。「Rules 欄位被寫死取代」
# 的真守門是 `tests/test_score_params_guard.py::test_every_rules_field_is_consumed`（逐欄突變 Rules，輸出必須改變）。
# 白名單每一條值都必須仍在程式裡出現（同檔 `test_whitelist_values_still_appear`），過時項要清掉。
NON_PARAM_CONSTANTS: dict[float, str] = {
    0.0: "索引／零檢查／算術恆等元（分母為零、空集合、初始值）；與 c=0 同值但此處非門檻",
    1.0: "索引步長、比值 −1（報酬＝a/b−1）、旗標乘數恆等元 1.0、位元 1＝陽；與 sub_weight=1.0 等同值但此處為結構",
    2.0: "算術結構：2n−1（AD 需求長度）、2k+1（擺動點視窗）、n/2（持續性中點）、T−2（ATR_{t−1} 對應 TR 索引）；與 d=2 等參數同值",
    3.0: "clip_3d 定義的 3d（v1.2.2 §4.1a）、hexagrams 路徑 parents[3]；與 d=3pp 等參數值同值但此處為變換定義",
    4.0: "lru_cache maxsize（非算式）；與 d=4pp 同值純屬巧合",
    5.0: "hexagram 六爻位元索引範圍 range(k)/parents；與視窗 5 同值但此處為結構索引",
    12.0: "MONTHS_PER_YEAR（YoY 對去年同月，曆法常數）；與 revenue_high_12m.window=12 同值",
    6.0: "六爻／位元長度（結構常數）",
    7.0: "ln(7/3) 的 7（S 的 k 定義：分數 50→70）",
    10.0: "T−9…T 序列長度 10；L 的宣告值域下界 10（transform）；與視窗 10／d=10pp 同值但此處為結構",
    14.0: "ATR14 的視窗（B1.0／B2.0 符號定義 ATR14_{t−1}，非校準對象）",
    20.0: "L 的錨點值 20（a→20）；MA20／VMA20_{t−1}（B2.0 符號定義，MA20_N）；與視窗 20 參數同值",
    30.0: "L 的定義：50±30（錨點 a→20、b→50、c→80）",
    50.0: "中性點 50（N 的不動點；L 的 b→50）；與 Rules.hysteresis_first=50 等同值，transform 內的 50 為定義",
    64.0: "64 卦（結構常數）",
    65.0: "range(1, 65) 的上界（64 卦）",
    85.4: "N 的值域跨幅 85.40（7.30→92.70）",
    90.0: "L 的宣告值域上界 90；與 Rules.revenue_high_floor_native=90 同值純屬巧合",
    92.7: "S＋clip_3d 的有效值域上界 92.70（與 Param.native_range 同值，來源即此定義）",
    7.3: "S＋clip_3d 的有效值域下界 7.30（與 Param.native_range 同值，來源即此定義）",
    100.0: "S 的分子 100／百分比換算 ×100／百分位 0–100 上界（與 native_range (0,100) 同值，來源即此定義）",
    700.0: "exp 溢位保護的 |z| 門檻（純數值防護，不影響非極端輸出）",
}
