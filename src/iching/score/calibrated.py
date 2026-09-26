"""d 校準值表——**本檔由 `scripts/apply_calibration.py` 產生，不要手改**。

來源報告與逐類別規則見 `CALIBRATION_META`；設計與驗收條件見 `docs/P3-CALIBRATION.md` §8。
`params.py` 的 `build_params()` 查這裡，查不到才用設計起點值（`*_START`／原字面量），
所以「不校準的鍵」就是「不在這裡的鍵」。`CALIBRATION_META` 非空 ⇒ `ParamSet.calibrated=True`。

重生方式：`python3 scripts/apply_calibration.py`（`--check` 只比對不寫，CI／測試用）。
"""
from __future__ import annotations

# (market, scope, indicator_id, horizon) -> d
CALIBRATED_D: dict[tuple[str, str, str, str], float] = {
    ("tpex", "market_index", "ad_line_dev", "mid"): 0.7315498272577919,   # p85/3
    ("tpex", "market_index", "ad_line_dev", "short"): 0.7541330099105834,   # p85/3
    ("tpex", "market_index", "ad_line_dev", "swing"): 0.7766956965128581,   # p85/3
    ("tpex", "market_index", "amount_ratio", "mid"): 0.06282302935918173,   # p85/3
    ("tpex", "market_index", "amount_ratio", "short"): 0.09074781537055969,   # p85/3
    ("tpex", "market_index", "amount_ratio", "swing"): 0.06713309486707052,   # p85/3
    ("tpex", "market_index", "basis", "mid"): 0.11245795289675393,   # p85/3
    ("tpex", "market_index", "basis", "short"): 0.11245795289675393,   # p85/3
    ("tpex", "market_index", "basis", "swing"): 0.11245795289675393,   # p85/3
    ("tpex", "market_index", "foreign_buy_days", "mid"): 1.6666666666666667,   # range_upper/3
    ("tpex", "market_index", "foreign_buy_days", "short"): 1.6666666666666667,   # range_upper/3
    ("tpex", "market_index", "foreign_buy_days", "swing"): 1.6666666666666667,   # range_upper/3
    ("tpex", "market_index", "foreign_net_oi_change", "mid"): 4800.533333333334,   # p85/3
    ("tpex", "market_index", "foreign_net_oi_change", "short"): 2535.1999999999994,   # p85/3
    ("tpex", "market_index", "foreign_net_oi_change", "swing"): 3395.4999999999995,   # p85/3
    ("tpex", "market_index", "foreign_net_ratio", "mid"): 0.41144119898478193,   # p85/3
    ("tpex", "market_index", "foreign_net_ratio", "short"): 0.5864300171534219,   # p85/3
    ("tpex", "market_index", "foreign_net_ratio", "swing"): 0.4984737594922384,   # p85/3
    ("tpex", "market_index", "margin_change", "mid"): 3.857227166493734,   # p85/3
    ("tpex", "market_index", "margin_change", "short"): 1.2539348125457763,   # p85/3
    ("tpex", "market_index", "margin_change", "swing"): 2.255927499135335,   # p85/3
    ("tpex", "market_index", "new_high_low_ratio", "mid"): 3.8425209999084466,   # p85/3
    ("tpex", "market_index", "new_high_low_ratio", "short"): 9.946306228637695,   # p85/3
    ("tpex", "market_index", "new_high_low_ratio", "swing"): 7.200952657063802,   # p85/3
    ("tpex", "market_index", "sox_return", "mid"): 4.626386992136637,   # p85/3
    ("tpex", "market_index", "sox_return", "short"): 2.146884234746297,   # p85/3
    ("tpex", "market_index", "sox_return", "swing"): 3.1874435106913253,   # p85/3
    ("tpex", "market_index", "spx_return", "mid"): 2.2252599080403646,   # p85/3
    ("tpex", "market_index", "spx_return", "short"): 1.1214777628580728,   # p85/3
    ("tpex", "market_index", "spx_return", "swing"): 1.5270509878794352,   # p85/3
    ("tpex", "market_index", "trust_net_ratio", "mid"): 0.2001727819442749,   # p85/3
    ("tpex", "market_index", "trust_net_ratio", "short"): 0.2689628620942433,   # p85/3
    ("tpex", "market_index", "trust_net_ratio", "swing"): 0.22268803715705868,   # p85/3
    ("tpex", "market_index", "usdtwd_change", "mid"): 0.7264545758565267,   # p85/3
    ("tpex", "market_index", "usdtwd_change", "short"): 0.2831814924875895,   # p85/3
    ("tpex", "market_index", "usdtwd_change", "swing"): 0.44220530589421586,   # p85/3
    ("tpex", "stock", "eps_diff_over_price", "mid"): 1.3061224619547527,   # p85/3
    ("tpex", "stock", "eps_yoy", "mid"): 55.55555725097656,   # p85/3
    ("tpex", "stock", "excess_accel", "mid"): 6.879360898335775,   # p85/3
    ("tpex", "stock", "excess_accel", "short"): 3.2920310497283936,   # p85/3
    ("tpex", "stock", "excess_accel", "swing"): 4.64125207265218,   # p85/3
    ("tpex", "stock", "excess_long", "mid"): 7.828476206461587,   # p85/3
    ("tpex", "stock", "excess_long", "short"): 3.0905490716298423,   # p85/3
    ("tpex", "stock", "excess_long", "swing"): 4.450597476959228,   # p85/3
    ("tpex", "stock", "excess_short", "mid"): 4.450597476959228,   # p85/3
    ("tpex", "stock", "excess_short", "short"): 2.183100461959839,   # p85/3
    ("tpex", "stock", "excess_short", "swing"): 3.0905490716298423,   # p85/3
    ("tpex", "stock", "excess_vs_industry", "mid"): 7.279462178548177,   # p85/3
    ("tpex", "stock", "excess_vs_industry", "short"): 2.849581766128539,   # p85/3
    ("tpex", "stock", "excess_vs_industry", "swing"): 4.094762802124023,   # p85/3
    ("tpex", "stock", "foreign_persistence", "mid"): 3.3333333333333335,   # range_upper/3
    ("tpex", "stock", "foreign_persistence", "short"): 0.8333333333333334,   # range_upper/3
    ("tpex", "stock", "foreign_persistence", "swing"): 1.6666666666666667,   # range_upper/3
    ("tpex", "stock", "foreign_strength_long", "mid"): 2.1996573607126866,   # p85/3
    ("tpex", "stock", "foreign_strength_long", "short"): 3.782750988006592,   # p85/3
    ("tpex", "stock", "foreign_strength_long", "swing"): 2.9085218588511137,   # p85/3
    ("tpex", "stock", "foreign_strength_short", "mid"): 2.9085218588511137,   # p85/3
    ("tpex", "stock", "foreign_strength_short", "short"): 4.595350710550943,   # p85/3
    ("tpex", "stock", "foreign_strength_short", "swing"): 3.782750988006592,   # p85/3
    ("tpex", "stock", "gross_margin_qoq", "mid"): 3.0450992584228516,   # p85/3
    ("tpex", "stock", "industry_relative_return", "mid"): 2.014067014058431,   # p85/3
    ("tpex", "stock", "industry_relative_return", "short"): 0.9262250264485677,   # p85/3
    ("tpex", "stock", "industry_relative_return", "swing"): 1.3561453819274902,   # p85/3
    ("tpex", "stock", "margin_scenario", "mid"): 10.569105784098307,   # p85/3
    ("tpex", "stock", "margin_scenario", "short"): 4.226718028386434,   # p85/3
    ("tpex", "stock", "margin_scenario", "swing"): 6.731996663411455,   # p85/3
    ("tpex", "stock", "obv_slope", "mid"): 0.16157451967398326,   # p85/3
    ("tpex", "stock", "pretax_income_yoy", "mid"): 55.76335144042969,   # p85/3
    ("tpex", "stock", "revenue_accel", "mid"): 17.983194986979168,   # p85/3
    ("tpex", "stock", "revenue_accel", "short"): 17.983194986979168,   # p85/3
    ("tpex", "stock", "revenue_accel", "swing"): 17.983194986979168,   # p85/3
    ("tpex", "stock", "revenue_yoy", "mid"): 20.27252197265625,   # p85/3
    ("tpex", "stock", "revenue_yoy", "short"): 23.59056854248047,   # p85/3
    ("tpex", "stock", "revenue_yoy", "swing"): 20.27252197265625,   # p85/3
    ("tpex", "stock", "revenue_yoy_vs_industry", "mid"): 18.51854705810547,   # p85/3
    ("tpex", "stock", "short_sale_change", "mid"): 0.08859696090221401,   # p85/3
    ("tpex", "stock", "short_sale_change", "short"): 0.031516302252809145,   # p85/3
    ("tpex", "stock", "short_sale_change", "swing"): 0.05400458425283427,   # p85/3
    ("tpex", "stock", "trust_strength_long", "mid"): 1.569640318552653,   # nonzero_p85/3
    ("tpex", "stock", "trust_strength_long", "short"): 2.685594574610393,   # nonzero_p85/3
    ("tpex", "stock", "trust_strength_long", "swing"): 2.0466300805409747,   # nonzero_p85/3
    ("tpex", "stock", "trust_strength_short", "mid"): 2.0466300805409747,   # nonzero_p85/3
    ("tpex", "stock", "trust_strength_short", "short"): 3.2886135737101223,   # nonzero_p85/3
    ("tpex", "stock", "trust_strength_short", "swing"): 2.685594574610393,   # nonzero_p85/3
    ("tpex", "stock", "updown_volume_ratio", "mid"): 0.24397260745366411,   # p85/3
    ("twse", "market_index", "ad_line_dev", "mid"): 0.6971082766850789,   # p85/3
    ("twse", "market_index", "ad_line_dev", "short"): 0.7429011901219686,   # p85/3
    ("twse", "market_index", "ad_line_dev", "swing"): 0.7234435160954793,   # p85/3
    ("twse", "market_index", "amount_ratio", "mid"): 0.06135172843933106,   # p85/3
    ("twse", "market_index", "amount_ratio", "short"): 0.0833617309729258,   # p85/3
    ("twse", "market_index", "amount_ratio", "swing"): 0.05839176177978516,   # p85/3
    ("twse", "market_index", "basis", "mid"): 0.11245795289675393,   # p85/3
    ("twse", "market_index", "basis", "short"): 0.11245795289675393,   # p85/3
    ("twse", "market_index", "basis", "swing"): 0.11245795289675393,   # p85/3
    ("twse", "market_index", "foreign_buy_days", "mid"): 1.6666666666666667,   # range_upper/3
    ("twse", "market_index", "foreign_buy_days", "short"): 1.6666666666666667,   # range_upper/3
    ("twse", "market_index", "foreign_buy_days", "swing"): 1.6666666666666667,   # range_upper/3
    ("twse", "market_index", "foreign_net_oi_change", "mid"): 4800.533333333334,   # p85/3
    ("twse", "market_index", "foreign_net_oi_change", "short"): 2535.1999999999994,   # p85/3
    ("twse", "market_index", "foreign_net_oi_change", "swing"): 3395.4999999999995,   # p85/3
    ("twse", "market_index", "foreign_net_ratio", "mid"): 1.2383761246999105,   # p85/3
    ("twse", "market_index", "foreign_net_ratio", "short"): 1.6818529764811199,   # p85/3
    ("twse", "market_index", "foreign_net_ratio", "swing"): 1.4368813196818033,   # p85/3
    ("twse", "market_index", "margin_change", "mid"): 3.857227166493734,   # p85/3
    ("twse", "market_index", "margin_change", "short"): 1.2539348125457763,   # p85/3
    ("twse", "market_index", "margin_change", "swing"): 2.255927499135335,   # p85/3
    ("twse", "market_index", "new_high_low_ratio", "mid"): 4.360007476806641,   # p85/3
    ("twse", "market_index", "new_high_low_ratio", "short"): 11.162954966227213,   # p85/3
    ("twse", "market_index", "new_high_low_ratio", "swing"): 7.9365081787109375,   # p85/3
    ("twse", "market_index", "sox_return", "mid"): 4.626386992136637,   # p85/3
    ("twse", "market_index", "sox_return", "short"): 2.146884234746297,   # p85/3
    ("twse", "market_index", "sox_return", "swing"): 3.1874435106913253,   # p85/3
    ("twse", "market_index", "spx_return", "mid"): 2.2252599080403646,   # p85/3
    ("twse", "market_index", "spx_return", "short"): 1.1214777628580728,   # p85/3
    ("twse", "market_index", "spx_return", "swing"): 1.5270509878794352,   # p85/3
    ("twse", "market_index", "trust_net_ratio", "mid"): 0.19941376050313311,   # p85/3
    ("twse", "market_index", "trust_net_ratio", "short"): 0.23387381434440613,   # p85/3
    ("twse", "market_index", "trust_net_ratio", "swing"): 0.2212359885374705,   # p85/3
    ("twse", "market_index", "usdtwd_change", "mid"): 0.7264545758565267,   # p85/3
    ("twse", "market_index", "usdtwd_change", "short"): 0.2831814924875895,   # p85/3
    ("twse", "market_index", "usdtwd_change", "swing"): 0.44220530589421586,   # p85/3
    ("twse", "stock", "eps_diff_over_price", "mid"): 1.6450215975443523,   # p85/3
    ("twse", "stock", "eps_yoy", "mid"): 50.87719217936198,   # p85/3
    ("twse", "stock", "excess_accel", "mid"): 6.137445163726806,   # p85/3
    ("twse", "stock", "excess_accel", "short"): 2.838035011291504,   # p85/3
    ("twse", "stock", "excess_accel", "swing"): 4.08416741689046,   # p85/3
    ("twse", "stock", "excess_long", "mid"): 7.206752745310465,   # p85/3
    ("twse", "stock", "excess_long", "short"): 2.8156930287679036,   # p85/3
    ("twse", "stock", "excess_long", "swing"): 4.086084620157876,   # p85/3
    ("twse", "stock", "excess_short", "mid"): 4.086084620157876,   # p85/3
    ("twse", "stock", "excess_short", "short"): 1.940744686126709,   # p85/3
    ("twse", "stock", "excess_short", "swing"): 2.8156930287679036,   # p85/3
    ("twse", "stock", "excess_vs_industry", "mid"): 6.266837819417318,   # p85/3
    ("twse", "stock", "excess_vs_industry", "short"): 2.3984294335047402,   # p85/3
    ("twse", "stock", "excess_vs_industry", "swing"): 3.4519256909688316,   # p85/3
    ("twse", "stock", "foreign_persistence", "mid"): 3.3333333333333335,   # range_upper/3
    ("twse", "stock", "foreign_persistence", "short"): 0.8333333333333334,   # range_upper/3
    ("twse", "stock", "foreign_persistence", "swing"): 1.6666666666666667,   # range_upper/3
    ("twse", "stock", "foreign_strength_long", "mid"): 3.9472339630126947,   # p85/3
    ("twse", "stock", "foreign_strength_long", "short"): 6.1201310475667325,   # p85/3
    ("twse", "stock", "foreign_strength_long", "swing"): 4.969406700134277,   # p85/3
    ("twse", "stock", "foreign_strength_short", "mid"): 4.969406700134277,   # p85/3
    ("twse", "stock", "foreign_strength_short", "short"): 7.1624840736389155,   # p85/3
    ("twse", "stock", "foreign_strength_short", "swing"): 6.1201310475667325,   # p85/3
    ("twse", "stock", "gross_margin_qoq", "mid"): 2.464895248413086,   # p85/3
    ("twse", "stock", "industry_relative_return", "mid"): 2.089364846547445,   # p85/3
    ("twse", "stock", "industry_relative_return", "short"): 0.9295907020568848,   # p85/3
    ("twse", "stock", "industry_relative_return", "swing"): 1.3555833498636882,   # p85/3
    ("twse", "stock", "margin_scenario", "mid"): 9.941520690917969,   # p85/3
    ("twse", "stock", "margin_scenario", "short"): 4.242424329121907,   # p85/3
    ("twse", "stock", "margin_scenario", "swing"): 6.589147567749023,   # p85/3
    ("twse", "stock", "obv_slope", "mid"): 0.15170633991559343,   # p85/3
    ("twse", "stock", "pretax_income_yoy", "mid"): 38.56039174397787,   # p85/3
    ("twse", "stock", "revenue_accel", "mid"): 13.725334167480469,   # p85/3
    ("twse", "stock", "revenue_accel", "short"): 13.725334167480469,   # p85/3
    ("twse", "stock", "revenue_accel", "swing"): 13.725334167480469,   # p85/3
    ("twse", "stock", "revenue_yoy", "mid"): 16.892842610677082,   # p85/3
    ("twse", "stock", "revenue_yoy", "short"): 19.251934051513672,   # p85/3
    ("twse", "stock", "revenue_yoy", "swing"): 16.892842610677082,   # p85/3
    ("twse", "stock", "revenue_yoy_vs_industry", "mid"): 14.63149897257487,   # p85/3
    ("twse", "stock", "short_sale_change", "mid"): 0.1269794543584187,   # p85/3
    ("twse", "stock", "short_sale_change", "short"): 0.046072507401307417,   # p85/3
    ("twse", "stock", "short_sale_change", "swing"): 0.07839718957742055,   # p85/3
    ("twse", "stock", "trust_strength_long", "mid"): 1.3437846899032593,   # nonzero_p85/3
    ("twse", "stock", "trust_strength_long", "short"): 2.065882921218872,   # nonzero_p85/3
    ("twse", "stock", "trust_strength_long", "swing"): 1.664462407430013,   # nonzero_p85/3
    ("twse", "stock", "trust_strength_short", "mid"): 1.664462407430013,   # nonzero_p85/3
    ("twse", "stock", "trust_strength_short", "short"): 2.4298429807027193,   # nonzero_p85/3
    ("twse", "stock", "trust_strength_short", "swing"): 2.065882921218872,   # nonzero_p85/3
    ("twse", "stock", "updown_volume_ratio", "mid"): 0.20367583433787026,   # p85/3
}

# market -> MA 窗長 n -> d（裁定 #55：每格取該格所有鍵 p85/3 的最大值）
CALIBRATED_DISTANCE_D: dict[str, dict[int, float]] = {
    "tpex": {5: 0.4206873099009196, 10: 0.7122490326563516, 20: 1.0746443669001262, 60: 1.8148725191752115},
    "twse": {5: 0.4364946047465006, 10: 0.7074257373809814, 20: 1.0922580003738405, 60: 1.754890505472819},
}

# 表名 -> market -> 斜率視窗 n -> d（兩張表各自一份；每格只被一個期間引用，故逐鍵校準）
CALIBRATED_SLOPE_D: dict[str, dict[str, dict[int, float]]] = {
    "market_slope_d": {
        "tpex": {5: 0.499282705783844, 10: 0.913856840133667, 20: 1.6074917634328205},
        "twse": {5: 0.47668463389078775, 10: 0.887348437309265, 20: 1.5196943124135336},
    },
    "stock_slope_d": {
        "tpex": {5: 0.340577208995819, 10: 0.6393939256668091, 20: 0.7511756936709085},
        "twse": {5: 0.33541667461395264, 10: 0.6305045386155447, 20: 0.7447820345560708},
    },
}

# 全部欄位一律字串（理由見 scripts/apply_calibration.py 檔頭「CALIBRATION_META 的值一律寫成字串」）
CALIBRATION_META: dict[str, object] = {
    "c_unchanged": "裁定 #54 Q2：c 一律不動，只校 d。median_flags 只記錄、不改 c。",
    "data_version": "fm-20260911-01",
    "days_dumped": "603",
    "distance_slot_sources": {
        "tpex|10": "market_index__tpex__swing__1__A__dist_ma_short",
        "tpex|20": "market_index__tpex__mid__1__A__dist_ma_short, market_index__tpex__short__1__A__dist_ma_long, market_index__tpex__swing__1__A__dist_ma_long",
        "tpex|5": "market_index__tpex__short__1__A__dist_ma_short",
        "tpex|60": "market_index__tpex__mid__1__A__dist_ma_long",
        "twse|10": "market_index__twse__swing__1__A__dist_ma_short",
        "twse|20": "market_index__twse__mid__1__A__dist_ma_short, market_index__twse__short__1__A__dist_ma_long, market_index__twse__swing__1__A__dist_ma_long",
        "twse|5": "market_index__twse__short__1__A__dist_ma_short",
        "twse|60": "market_index__twse__mid__1__A__dist_ma_long"
    },
    "dump_from": "2021-01-01",
    "dump_to": "2023-06-30",
    "gate_exceptions": [],
    "gate_pct": "15.0",
    "gate_slack": "100/n（linear 內插的離散化餘裕，見 scripts/calibrate_d.py 檔頭）",
    "generator": "scripts/apply_calibration.py",
    "generator_version": "1",
    "median_flags": [
        "market_index__twse__mid__4__B__trust_net_ratio — |median(x)−c| > d_new；裁定 #54 Q2：c 不動，只記錄",
        "market_index__twse__short__4__B__trust_net_ratio — |median(x)−c| > d_new；裁定 #54 Q2：c 不動，只記錄",
        "market_index__twse__swing__4__B__trust_net_ratio — |median(x)−c| > d_new；裁定 #54 Q2：c 不動，只記錄"
    ],
    "not_calibrated": [
        "market_index__tpex__mid__1__C__range_position — not_applicable（clip_policy=n/a，無 c 無 d）",
        "market_index__tpex__mid__2__A__above_ma_long_ratio — not_applicable（clip_policy=n/a，無 c 無 d）",
        "market_index__tpex__mid__2__A__above_ma_short_ratio — not_applicable（clip_policy=n/a，無 c 無 d）",
        "market_index__tpex__mid__2__B__advance_ratio — not_applicable（clip_policy=n/a，無 c 無 d）",
        "market_index__tpex__mid__3__B__up_amount_ratio — not_applicable（clip_policy=n/a，無 c 無 d）",
        "market_index__tpex__mid__3__C__divergence_scenario — not_applicable（clip_policy=n/a，無 c 無 d）",
        "market_index__tpex__mid__5__A__foreign_net_oi_phist — not_applicable（clip_policy=n/a，無 c 無 d）",
        "market_index__tpex__mid__5__C__vix_phist_rev — not_applicable（clip_policy=n/a，無 c 無 d）",
        "market_index__tpex__mid__5_____put_call_ratio — not_applicable（clip_policy=n/a，無 c 無 d）",
        "market_index__tpex__short__1__C__range_position — not_applicable（clip_policy=n/a，無 c 無 d）",
        "market_index__tpex__short__2__A__above_ma_long_ratio — not_applicable（clip_policy=n/a，無 c 無 d）",
        "market_index__tpex__short__2__A__above_ma_short_ratio — not_applicable（clip_policy=n/a，無 c 無 d）",
        "market_index__tpex__short__2__B__advance_ratio — not_applicable（clip_policy=n/a，無 c 無 d）",
        "market_index__tpex__short__3__B__up_amount_ratio — not_applicable（clip_policy=n/a，無 c 無 d）",
        "market_index__tpex__short__3__C__divergence_scenario — not_applicable（clip_policy=n/a，無 c 無 d）",
        "market_index__tpex__short__5__A__foreign_net_oi_phist — not_applicable（clip_policy=n/a，無 c 無 d）",
        "market_index__tpex__short__5__C__vix_phist_rev — not_applicable（clip_policy=n/a，無 c 無 d）",
        "market_index__tpex__short__5_____put_call_ratio — not_applicable（clip_policy=n/a，無 c 無 d）",
        "market_index__tpex__swing__1__C__range_position — not_applicable（clip_policy=n/a，無 c 無 d）",
        "market_index__tpex__swing__2__A__above_ma_long_ratio — not_applicable（clip_policy=n/a，無 c 無 d）",
        "market_index__tpex__swing__2__A__above_ma_short_ratio — not_applicable（clip_policy=n/a，無 c 無 d）",
        "market_index__tpex__swing__2__B__advance_ratio — not_applicable（clip_policy=n/a，無 c 無 d）",
        "market_index__tpex__swing__3__B__up_amount_ratio — not_applicable（clip_policy=n/a，無 c 無 d）",
        "market_index__tpex__swing__3__C__divergence_scenario — not_applicable（clip_policy=n/a，無 c 無 d）",
        "market_index__tpex__swing__5__A__foreign_net_oi_phist — not_applicable（clip_policy=n/a，無 c 無 d）",
        "market_index__tpex__swing__5__C__vix_phist_rev — not_applicable（clip_policy=n/a，無 c 無 d）",
        "market_index__tpex__swing__5_____put_call_ratio — not_applicable（clip_policy=n/a，無 c 無 d）",
        "market_index__twse__mid__1__C__range_position — not_applicable（clip_policy=n/a，無 c 無 d）",
        "market_index__twse__mid__2__A__above_ma_long_ratio — not_applicable（clip_policy=n/a，無 c 無 d）",
        "market_index__twse__mid__2__A__above_ma_short_ratio — not_applicable（clip_policy=n/a，無 c 無 d）",
        "market_index__twse__mid__2__B__advance_ratio — not_applicable（clip_policy=n/a，無 c 無 d）",
        "market_index__twse__mid__3__B__up_amount_ratio — not_applicable（clip_policy=n/a，無 c 無 d）",
        "market_index__twse__mid__3__C__divergence_scenario — not_applicable（clip_policy=n/a，無 c 無 d）",
        "market_index__twse__mid__5__A__foreign_net_oi_phist — not_applicable（clip_policy=n/a，無 c 無 d）",
        "market_index__twse__mid__5__C__vix_phist_rev — not_applicable（clip_policy=n/a，無 c 無 d）",
        "market_index__twse__mid__5_____put_call_ratio — not_applicable（clip_policy=n/a，無 c 無 d）",
        "market_index__twse__short__1__C__range_position — not_applicable（clip_policy=n/a，無 c 無 d）",
        "market_index__twse__short__2__A__above_ma_long_ratio — not_applicable（clip_policy=n/a，無 c 無 d）",
        "market_index__twse__short__2__A__above_ma_short_ratio — not_applicable（clip_policy=n/a，無 c 無 d）",
        "market_index__twse__short__2__B__advance_ratio — not_applicable（clip_policy=n/a，無 c 無 d）",
        "market_index__twse__short__3__B__up_amount_ratio — not_applicable（clip_policy=n/a，無 c 無 d）",
        "market_index__twse__short__3__C__divergence_scenario — not_applicable（clip_policy=n/a，無 c 無 d）",
        "market_index__twse__short__5__A__foreign_net_oi_phist — not_applicable（clip_policy=n/a，無 c 無 d）",
        "market_index__twse__short__5__C__vix_phist_rev — not_applicable（clip_policy=n/a，無 c 無 d）",
        "market_index__twse__short__5_____put_call_ratio — not_applicable（clip_policy=n/a，無 c 無 d）",
        "market_index__twse__swing__1__C__range_position — not_applicable（clip_policy=n/a，無 c 無 d）",
        "market_index__twse__swing__2__A__above_ma_long_ratio — not_applicable（clip_policy=n/a，無 c 無 d）",
        "market_index__twse__swing__2__A__above_ma_short_ratio — not_applicable（clip_policy=n/a，無 c 無 d）",
        "market_index__twse__swing__2__B__advance_ratio — not_applicable（clip_policy=n/a，無 c 無 d）",
        "market_index__twse__swing__3__B__up_amount_ratio — not_applicable（clip_policy=n/a，無 c 無 d）",
        "market_index__twse__swing__3__C__divergence_scenario — not_applicable（clip_policy=n/a，無 c 無 d）",
        "market_index__twse__swing__5__A__foreign_net_oi_phist — not_applicable（clip_policy=n/a，無 c 無 d）",
        "market_index__twse__swing__5__C__vix_phist_rev — not_applicable（clip_policy=n/a，無 c 無 d）",
        "market_index__twse__swing__5_____put_call_ratio — not_applicable（clip_policy=n/a，無 c 無 d）",
        "stock__tpex__mid__1__A__revenue_high_12m — not_applicable（clip_policy=n/a，無 c 無 d）",
        "stock__tpex__mid__1__B__equity_qoq — n=0（訓練段無樣本，維持設計起點值）",
        "stock__tpex__mid__2__C__structure — not_applicable（clip_policy=n/a，無 c 無 d）",
        "stock__tpex__mid__4__B__close_position — not_applicable（clip_policy=n/a，無 c 無 d）",
        "stock__tpex__mid__4__C__continuation — not_applicable（clip_policy=n/a，無 c 無 d）",
        "stock__tpex__mid__6__A__market_direction — not_applicable（clip_policy=n/a，無 c 無 d）",
        "stock__tpex__mid__6__B__industry_above_ma20_ratio — not_applicable（clip_policy=n/a，無 c 無 d）",
        "stock__tpex__short__2__C__structure — not_applicable（clip_policy=n/a，無 c 無 d）",
        "stock__tpex__short__4__A__volume_scenario — not_applicable（clip_policy=n/a，無 c 無 d）",
        "stock__tpex__short__4__B__close_position — not_applicable（clip_policy=n/a，無 c 無 d）",
        "stock__tpex__short__4__C__continuation — not_applicable（clip_policy=n/a，無 c 無 d）",
        "stock__tpex__short__6__A__market_direction — not_applicable（clip_policy=n/a，無 c 無 d）",
        "stock__tpex__short__6__B__industry_above_ma20_ratio — not_applicable（clip_policy=n/a，無 c 無 d）",
        "stock__tpex__swing__2__C__structure — not_applicable（clip_policy=n/a，無 c 無 d）",
        "stock__tpex__swing__4__A__volume_scenario — not_applicable（clip_policy=n/a，無 c 無 d）",
        "stock__tpex__swing__4__B__close_position — not_applicable（clip_policy=n/a，無 c 無 d）",
        "stock__tpex__swing__4__C__continuation — not_applicable（clip_policy=n/a，無 c 無 d）",
        "stock__tpex__swing__6__A__market_direction — not_applicable（clip_policy=n/a，無 c 無 d）",
        "stock__tpex__swing__6__B__industry_above_ma20_ratio — not_applicable（clip_policy=n/a，無 c 無 d）",
        "stock__twse__mid__1__A__revenue_high_12m — not_applicable（clip_policy=n/a，無 c 無 d）",
        "stock__twse__mid__1__B__equity_qoq — n=0（訓練段無樣本，維持設計起點值）",
        "stock__twse__mid__2__C__structure — not_applicable（clip_policy=n/a，無 c 無 d）",
        "stock__twse__mid__4__B__close_position — not_applicable（clip_policy=n/a，無 c 無 d）",
        "stock__twse__mid__4__C__continuation — not_applicable（clip_policy=n/a，無 c 無 d）",
        "stock__twse__mid__6__A__market_direction — not_applicable（clip_policy=n/a，無 c 無 d）",
        "stock__twse__mid__6__B__industry_above_ma20_ratio — not_applicable（clip_policy=n/a，無 c 無 d）",
        "stock__twse__short__2__C__structure — not_applicable（clip_policy=n/a，無 c 無 d）",
        "stock__twse__short__4__A__volume_scenario — not_applicable（clip_policy=n/a，無 c 無 d）",
        "stock__twse__short__4__B__close_position — not_applicable（clip_policy=n/a，無 c 無 d）",
        "stock__twse__short__4__C__continuation — not_applicable（clip_policy=n/a，無 c 無 d）",
        "stock__twse__short__6__A__market_direction — not_applicable（clip_policy=n/a，無 c 無 d）",
        "stock__twse__short__6__B__industry_above_ma20_ratio — not_applicable（clip_policy=n/a，無 c 無 d）",
        "stock__twse__swing__2__C__structure — not_applicable（clip_policy=n/a，無 c 無 d）",
        "stock__twse__swing__4__A__volume_scenario — not_applicable（clip_policy=n/a，無 c 無 d）",
        "stock__twse__swing__4__B__close_position — not_applicable（clip_policy=n/a，無 c 無 d）",
        "stock__twse__swing__4__C__continuation — not_applicable（clip_policy=n/a，無 c 無 d）",
        "stock__twse__swing__6__A__market_direction — not_applicable（clip_policy=n/a，無 c 無 d）",
        "stock__twse__swing__6__B__industry_above_ma20_ratio — not_applicable（clip_policy=n/a，無 c 無 d）"
    ],
    "params_sha_before": "6bd41e811f49",
    "percentile": "85.0",
    "percentile_method": "linear",
    "persistence_upper_bound_source": "Param 沒有任何欄位記錄 x 的原生值域（native_range 是轉換後的分數值域，src/iching/score/params.py:44），故上界由『近 n 日買超天數 − n/2』反推為 視窗 n ÷ 2，與 scripts/calibrate_d.py 的 range_upper 同一套。登錄書引用本欄，不要再引口頭裁定（docs/P3-CALIBRATION.md §7.3 C）。",
    "report_generated_at": "2026-09-25T22:07:47Z",
    "report_schema": "1",
    "report_sha256": "aee0a3a13c32a93140e7bbd99ea145eaf3e7618787ca8577d57c5b2c7988dee2",
    "rules": {
        "calibrate": "z_zero < 0.5 → d = p85/3（spec/P1-B1-market.md:42 第 3 點；裁定 #54）；z_zero ≥ 0.5 → d = nonzero_p85/3（裁定 #56，零膨脹＝第三種例外）",
        "distance": "d = slot_max(p85/3)：每個 distance_d[n] 格取該格所有鍵 p85/3 的最大值（裁定 #55，取代 #54 Q3 的 25% 容差與合併樣本 p85）；查表結構保留（spec/P1-B1-market.md:49）",
        "not_applicable": "keep_start：not_applicable（clip_policy=n/a）與 n=0 的鍵不校準，維持 params.py 的設計起點值",
        "persistence": "d = range_upper/3：原始值域上界 ÷ 3，不套 p85（spec/P1-B1-market.md:48 5a；裁定 #54 Q4）"
    },
    "rulings": "docs/P3-CALIBRATION.md §6 裁定 #54／§9 裁定 #55／§12 裁定 #56；設計與驗收 §8 H1～H8；報告出處：§32 裁定 #69（整份套用 #68 後重跑的報告）",
    "slope_one_key_per_slot": "market_slope_d／stock_slope_d 雖是共用查表，但 MKT_L1_WIN／STK_L2_WIN 讓每個視窗 n 只被一個期間引用（short→5、swing→10、mid→20），故一格 d 對一個鍵，逐鍵校準不衝突；apply_calibration.py 產生時會實查斷言。",
    "source_branch": "origin/hetzner/calib-2023-06-30",
    "source_commit": "288fd36d2c0f1ef7a06f11c13b24481e6120f784",
    "source_report": "runs/calib/d_report_2023-06-30.json",
    "zero_inflation_keys": [
        "stock__tpex__mid__5__B__trust_strength_long — z_zero=0.8510",
        "stock__tpex__mid__5__B__trust_strength_short — z_zero=0.8726",
        "stock__tpex__short__5__B__trust_strength_long — z_zero=0.8935",
        "stock__tpex__short__5__B__trust_strength_short — z_zero=0.9083",
        "stock__tpex__swing__5__B__trust_strength_long — z_zero=0.8726",
        "stock__tpex__swing__5__B__trust_strength_short — z_zero=0.8935",
        "stock__twse__mid__5__B__trust_strength_long — z_zero=0.5879",
        "stock__twse__mid__5__B__trust_strength_short — z_zero=0.6331",
        "stock__twse__short__5__B__trust_strength_long — z_zero=0.6778",
        "stock__twse__short__5__B__trust_strength_short — z_zero=0.7121",
        "stock__twse__swing__5__B__trust_strength_long — z_zero=0.6331",
        "stock__twse__swing__5__B__trust_strength_short — z_zero=0.6778"
    ],
    "zero_inflation_threshold": "0.5"
}
