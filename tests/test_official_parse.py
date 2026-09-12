"""官方端點解析器（B1.4 法人／B1.3 成交金額）。

**fixture 全部取自 Hetzner `raw_*.body` 的真實回應**（2026-09-12 dump，2020-01-02／2020-01 月表），
不是編造的假資料——這個模組最容易錯的地方是「列名」與「單位」，兩者都只有真實回應才守得住。
月表 fixture 只保留前三個日列（原回應是整月），形狀與真實一致。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from iching import official_parse as OP  # noqa: E402

BFI82U = {
    "stat": "OK", "date": "20200102", "title": "109年01月02日 三大法人買賣金額統計表",
    "fields": ["單位名稱", "買進金額", "賣出金額", "買賣差額"],
    "data": [
        ["自營商(自行買賣)", "2,618,174,510", "1,222,750,424", "1,395,424,086"],
        ["自營商(避險)", "6,862,489,127", "4,486,023,470", "2,376,465,657"],
        ["投信", "2,106,517,650", "2,184,135,251", "-77,617,601"],
        ["外資及陸資(不含外資自營商)", "24,829,743,865", "28,259,613,600", "-3,429,869,735"],
        ["外資自營商", "9,852,550", "12,604,610", "-2,752,060"],
        ["合計", "36,416,925,152", "36,152,522,745", "264,402,407"],
    ],
}

# 注意子列的**全形空格**前綴（　），以及「外資及陸資合計」與其子列並存
TPEX_INST = {"tables": [{
    "title": "三大法人買賣金額彙總表", "date": "109/01/02",
    "fields": ["單位名稱", "買進金額(元)", "賣出金額(元)", "買賣超(元)"],
    "data": [
        ["外資及陸資合計", "3,324,823,014", "3,373,174,229", "-48,351,215"],
        ["　外資及陸資(不含自營商)", "3,324,823,014", "3,373,174,229", "-48,351,215"],
        ["　外資自營商", "0", "0", "0"],
        ["投信", "764,766,500", "615,048,500", "149,718,000"],
        ["自營商合計", "1,630,859,510", "788,180,856", "842,678,654"],
        ["　自營商(自行買賣)", "672,307,510", "330,273,206", "342,034,304"],
        ["　自營商(避險)", "958,552,000", "457,907,650", "500,644,350"],
        ["三大法人合計*", "5,720,449,024", "4,776,403,585", "944,045,439"],
    ],
}]}

FMTQIK = {
    "stat": "OK", "date": "20200101", "title": "109年01月市場成交資訊", "hints": "單位：元、股",
    "fields": ["日期", "成交股數", "成交金額", "成交筆數", "發行量加權股價指數", "漲跌點數"],
    "data": [
        ["109/01/02", "4,335,855,134", "142,714,556,309", "1,060,157", "12,100.48", "103.34"],
        ["109/01/03", "5,527,302,314", "193,759,680,797", "1,346,033", "12,110.43", "9.95"],
        ["109/01/06", "4,679,171,407", "144,304,046,312", "1,093,048", "11,953.36", "-157.07"],
    ],
}

# tradingIndex 的回應**沒有 fields 欄**，欄位只能靠位置
TPEX_IDX = {"tables": [{"title": "日成交量值指數", "date": "20200101", "data": [
    ["109/01/02", "465,310", "37,279,552", "275,237", 150.91, 1.55],
    ["109/01/03", "548,315", "42,701,271", "324,889", 148.94, -1.97],
    ["109/01/06", "404,812", "30,490,872", "240,444", 147.25, -1.69],
]}]}


def test_bfi82u_sums_components_because_there_is_no_total_row() -> None:
    """上市無「合計」列，外資與自營必須加總分項。"""
    r = OP.parse_bfi82u(BFI82U)
    assert (r["f_buy_k"], r["f_sell_k"], r["f_net_k"]) == (24_839_596, 28_272_218, -3_432_622)
    assert (r["t_buy_k"], r["t_sell_k"], r["t_net_k"]) == (2_106_518, 2_184_135, -77_618)
    assert (r["d_buy_k"], r["d_sell_k"], r["d_net_k"]) == (9_480_664, 5_708_774, 3_771_890)


def test_tpex_takes_total_rows_and_does_not_double_count() -> None:
    """上櫃有「合計」列：只取合計，**不可再加子列**（加了外資會變兩倍）。"""
    r = OP.parse_tpex_inst_summary(TPEX_INST)
    assert (r["f_buy_k"], r["f_sell_k"], r["f_net_k"]) == (3_324_823, 3_373_174, -48_351)
    assert (r["t_buy_k"], r["t_sell_k"], r["t_net_k"]) == (764_766, 615_048, 149_718)
    assert (r["d_buy_k"], r["d_sell_k"], r["d_net_k"]) == (1_630_860, 788_181, 842_679)
    # 反面：若誤把子列一起加，外資買進會是 6,649,646 千元
    assert r["f_buy_k"] != 6_649_646


def test_tpex_row_names_differ_from_twse_and_carry_fullwidth_space() -> None:
    """TWSE 是「不含**外資**自營商」、TPEx 是「不含自營商」，且 TPEx 子列帶全形空格。
    拿 TWSE 的列名去解 TPEx 會一列都對不上。"""
    assert OP.TSE_FOREIGN != OP.OTC_FOREIGN
    assert OP._norm("　外資及陸資(不含自營商)") == "外資及陸資(不含自營商)"
    # 用 TWSE 列名解 TPEx 回應必須 raise。**守門一定要「全部列名到齊」而非「任一對上」**：
    # TPEx 也有「投信」，且其子列剝掉全形空格後正好是 TSE_FOREIGN 的成員之一（外資自營商），
    # 寬鬆檢查會通過、外資只認到那一列（值 0），於是外資買賣超每天都是 0 且完全無聲。
    with pytest.raises(OP.OfficialParseError) as e:
        OP._parse_inst_rows(TPEX_INST["tables"][0]["data"], OP.TSE_FOREIGN, OP.TSE_TRUST, OP.TSE_DEALER)
    assert "缺必要列" in str(e.value)


def test_banker_rounding_is_the_family_convention() -> None:
    """764,766,500 ÷ 1000 ＝ 764766.5 → Python `round()` 進偶數得 764766（不是 764767）。
    姊妹站 `taiwan-flows/src/totals.py` 用同一個 `round()`，改成 `int(x+0.5)` 會差 1 且無聲。"""
    assert OP.parse_tpex_inst_summary(TPEX_INST)["t_buy_k"] == 764_766
    assert round(764_766_500 / 1000) == 764_766


def test_fmtqik_amount_is_yuan_and_converts_to_thousand() -> None:
    r = OP.parse_fmtqik_month(FMTQIK)
    assert set(r) == {"2020-01-02", "2020-01-03", "2020-01-06"}
    assert r["2020-01-02"]["turnover_k"] == 142_714_556      # 元 ÷ 1000
    assert r["2020-01-02"]["taiex"] == 12_100.48


def test_tpex_trading_index_amount_is_already_thousand() -> None:
    r = OP.parse_tpex_trading_index_month(TPEX_IDX)
    assert r["2020-01-02"] == 37_279_552                      # **不再除 1000**


def test_the_two_units_are_not_the_same_and_the_ratio_proves_it() -> None:
    """單位弄反是本模組最危險的錯（差 1000 倍、不會報錯）。
    同一天上櫃 372.8 億／上市 1,427.1 億 ＝ 26.1%，是 2020 年的合理比例；
    若把 tradingIndex 也除以 1000，會變成 0.026%，一眼看得出荒謬。"""
    tse = OP.parse_fmtqik_month(FMTQIK)["2020-01-02"]["turnover_k"]
    otc = OP.parse_tpex_trading_index_month(TPEX_IDX)["2020-01-02"]
    assert 0.15 < otc / tse < 0.40, f"上櫃/上市 成交金額比 {otc / tse:.4f} 不合理——多半是單位弄反"
    # 這個帶是給**這一天的 fixture** 用的。2026-09-12 對全部 1,618 天實測：
    # 中位 0.239、最小 0.074、最大 0.469——真實跨度比此帶寬，看到單日 0.08 不代表資料壞。
    # 單位弄反的特徵是差三個數量級（0.0002 或 260），不是落在 0.05~0.5 之間。


def test_roc_date_conversion() -> None:
    assert OP.roc_to_iso("109/01/02") == "2020-01-02"
    assert OP.roc_to_iso("115/12/31") == "2026-12-31"
    assert OP.roc_to_iso("壞資料") is None


@pytest.mark.parametrize("body", [{}, {"stat": "OK"}, {"stat": "很忙"}, {"stat": "OK", "data": []}])
def test_bad_shapes_raise_not_return_zero(body) -> None:
    """形狀不符一律 raise——回 0 會讓「抓不到」與「當天真的是 0」無法區分。"""
    with pytest.raises(OP.OfficialParseError):
        OP.parse_bfi82u(body)
