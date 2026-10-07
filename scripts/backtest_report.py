"""`runs/backtest/{train,valid}_<date>.json`＋`gate_<date>.txt` → `docs/P3-BACKTEST-VALID.md`（整檔生成，不得手改）。

    scratchpad/venv312/bin/python scripts/backtest_report.py --date 2026-10-07 [--check]

## 體例（沿附錄 B／C：`scripts/t717_appendix.py`／`stats_appendix.py`）

- **每一個數字**都由 f-string 從兩份 json／守門 txt 取出；字面常數只剩登錄書常數（0.03／2.0／60%／8／20／`max(21,3h)`／1,000）
  與裁定編號、tag／commit。
- **會隨資料變真變假的定性句都配 `_assert`**（→ `ReportError` → rc=2）：波段／中期六格 `insufficient`、區塊數量近似值＝登錄書
  `:295-297`、purge／embargo 日數、§16.5 守門 0 FAIL、兩段同一指紋、排序表 sha＝釘值……資料推翻就中止，不寫出被自己的表推翻的句子。
- 用語：不出現交易方向字樣；登錄書 §1.4 次要②的名單一律稱「候選名單（§1.4 次要②，頂十分位代理）」；稱「區塊數量近似值」。
- 本報告**不是保留段結果**：驗證段是調參段、訓練段是校準段，verdict 欄是「登錄書 §1.4 判定邏輯套在該段數字上的結果」，供對照；
  正式採用與否以保留段為準（本批未跑）。

rc：0 成功（或 `--check` 無差異）／1 `--check` 有差異／2 中止（守門不過、讀不到檔、任何例外）。
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "src"))
sys.path.insert(0, str(REPO / "scripts"))

import backtest_gate as BG
import backtest_stats as BS

from iching.stats.constants import (
    BLOCKS_MIN,
    EMBARGO_DAYS,
    IC_MIN,
    NBOOT,
    POS_SHARE_MIN,
    T_MIN,
)

OUT = REPO / "docs" / "P3-BACKTEST-VALID.md"
MARKER = "<!-- 本檔由 scripts/backtest_report.py 由 runs/backtest/*.json 與 gate_*.txt 整檔生成，不得手改（重跑會整檔覆寫；--check 守） -->"
PREREG_TAG, PREREG_SHA = "prereg-v2", "59e03f1"
RULING_COMMIT = "c1240f2"
#: 登錄書 §1.4 `:295-297` 九格中本批兩段的六格
BLOCKS_TABLE = BG.BLOCKS_TABLE
H_LABEL = {"short": "短線", "swing": "波段", "mid": "中期"}
M_LABEL = {"twse": "上市 twse", "tpex": "上櫃 tpex"}
SEG_LABEL = {"train": "訓練段", "valid": "驗證段"}
#: K＝216 的結構（§1.5 `:306-312`）
K_LINES, K_H, K_SCOPE, K_MARKET, K_WEIGHTS = 6, 3, 2, 2, 3
K_TOTAL = K_LINES * K_H * K_SCOPE * K_MARKET * K_WEIGHTS
NOMINAL_SENTENCE = "IC ≥ 0.03、NW t ≥ 2.0 是未經多重比較校正的名目值"
T3_SENTENCE = "波段／中期證據不足是裁定 T3 預期結果"
CANDIDATE_NAME = "候選名單（§1.4 次要②，頂十分位代理）"


class ReportError(Exception):
    pass


def _assert(cond: bool, what: str) -> None:
    if not cond:
        raise ReportError(what)


def _n(x: float | None, nd: int = 4) -> str:
    return "—" if x is None else f"{x:.{nd}f}"


def _pct(x: float | None, nd: int = 1) -> str:
    return "—" if x is None else f"{x * 100:.{nd}f}%"


def _ci(ci: list | None, nd: int = 4) -> str:
    return "—" if not ci or ci[0] is None else f"[{ci[0]:.{nd}f}, {ci[1]:.{nd}f}]"


def _cell(seg: dict, market: str, horizon: str) -> dict:
    for c in seg["cells"]:
        if c["market"] == market and c["horizon"] == horizon:
            return c
    raise ReportError(f"{seg['segment']} 缺 {market}/{horizon}")


def parse_gate(text: str) -> tuple[list[dict[str, str]], str]:
    rows = []
    for line in text.split("\n"):
        m = re.match(r"^:(\d+) (.+?) \| (.+?) \| (.*)$", line)
        if m:
            rows.append({"line": m.group(1), "name": m.group(2), "status": m.group(3), "evidence": m.group(4)})
    head = text.split("\n", 2)
    _assert(len(rows) == len(BG.ROWS), f"守門 txt 解析出 {len(rows)} 列 ≠ {len(BG.ROWS)}")
    _assert([(int(r["line"]), r["name"]) for r in rows] == BG.ROWS, "守門 txt 的列與 backtest_gate.ROWS 不符")
    return rows, head[1]


def validate(train: dict, valid: dict, gate_rows: list[dict[str, str]]) -> None:
    """定性句的守門：資料推翻就中止。"""
    for seg, rep in (("train", train), ("valid", valid)):
        _assert(rep["schema"] == BS.SCHEMA and rep["segment"] == seg, f"{seg} json schema／segment 不符")
        _assert(rep["gate"]["params_sha"] == BS.EXPECTED_PARAMS_SHA, f"{seg} params_sha ≠ {BS.EXPECTED_PARAMS_SHA}")
        _assert(rep["gate"]["pool_semantics"] == BS.EXPECTED_POOL_SEMANTICS, f"{seg} pool_semantics 不符")
        _assert(rep["gate"]["rank_table_sha256"] == BS.RANK_TABLE_SHA256, f"{seg} rank_table sha 不是凍結釘值")
        _assert(rep["params"]["seed"] == 42 and rep["params"]["nboot"] == NBOOT and rep["params"]["min_pairs"] == BS.MIN_PAIRS,
                f"{seg} seed／nboot／min_pairs 不是裁定值")
        _assert(rep["segment_info"]["n_days"] == BG.SEG_DAYS[seg], f"{seg} 段日數 ≠ {BG.SEG_DAYS[seg]}")
        _assert(len(rep["cells"]) == 6, f"{seg} 不是 6 格")
        for c in rep["cells"]:
            w = c["window"]
            _assert(w["purge_days"] == c["h"] + 1, f"{seg}/{c['market']}/{c['horizon']} purge 日數 ≠ h+1")
            _assert(w["embargo_days"] == (EMBARGO_DAYS if seg == "valid" else 0), f"{seg} embargo 日數不符")
            _assert(round(w["n_blocks_t5"], 2) == BLOCKS_TABLE[(seg, c["horizon"])], f"{seg}/{c['horizon']} 區塊數 ≠ 登錄書表")
            _assert((w["n_blocks_t5"] < BLOCKS_MIN) == (c["verdict"] == "insufficient"), f"{seg}/{c['horizon']} verdict 與區塊數不一致")
            _assert(c["verdict"] in ("insufficient", "adopted", "rejected"), f"{seg}/{c['market']}/{c['horizon']} verdict 留空（缺同儕段）")
            _assert(c["ic"]["nan_pairs"] == 0, "IC 階段含 NaN 配對")
            if c["h"] == 40:
                _assert("boot_ci_block_sensitivity" in c["ic"], "h=40 缺區塊 126 敏感度")
    # T3：驗證段波段／中期四格必為證據不足（訓練段中期兩格亦然；訓練段波段 9.72 ≥ 8 不在 T3 範圍）
    for hz in ("swing", "mid"):
        for mk in ("twse", "tpex"):
            _assert(_cell(valid, mk, hz)["verdict"] == "insufficient", f"驗證段 {mk}/{hz} 不是 insufficient——與裁定 T3 衝突")
    for mk in ("twse", "tpex"):
        _assert(_cell(train, mk, "mid")["verdict"] == "insufficient", f"訓練段 {mk}/mid 不是 insufficient")
    _assert(valid["peer"] and train["peer"], "兩段 json 須互為同儕（robust 三段同號才有值）")
    _assert(not any(r["status"] == "FAIL" for r in gate_rows), "§16.5 守門有 FAIL，不產報告")
    _assert(sum(r["status"] == "PASS" for r in gate_rows) >= 11, "§16.5 本層可機械驗的列未全 PASS")


def build(train: dict, valid: dict, gate_text: str, date_tag: str) -> str:
    gate_rows, gate_summary = parse_gate(gate_text)
    validate(train, valid, gate_rows)
    P = valid["params"]
    L: list[str] = []
    A = L.append
    A(MARKER)
    A(f"# P3 回測統計層：訓練段＋驗證段結果（PR-S2，{date_tag}）")
    A("")
    A(f"> **這不是保留段結果。** 驗證段（2023-07-03～2024-12-31）是調參段、訓練段（2021-01-04～2023-06-30）是校準段；本報告的 verdict 欄"
      f"＝登錄書 §1.4 判定邏輯套在該段數字上的結果，**供對照、不構成採用**，正式採用與否以保留段為準（保留段本批未跑、未匯出）。"
      f"判準正本＝`docs/pre-registration.md` §1（凍結 tag `{PREREG_TAG}`＝`{PREREG_SHA}`，2026-10-07 07:25 台北），"
      f"開跑前裁定＝`docs/P3-KICKOFF.md` §5b 裁定 #72（`{RULING_COMMIT}`）；**本報告與 `runs/backtest/` 所有產物的 commit 都晚於這兩者**"
      f"（§16.5 `:728`，守門表第 18 列）。資料集 `data/backtest/` `params_sha={valid['gate']['params_sha']}`、"
      f"`pool_semantics={valid['gate']['pool_semantics']}`、`data_version={valid['gate']['data_version']}`、`head={valid['gate']['head'][:7]}`。")
    A("")
    # ---------------------------------------------------------------- ① 結論首頁
    A("## 1. 結論首頁：六格 verdict（先寫裁定 T3）")
    A("")
    A(f"**{T3_SENTENCE}**：登錄書 §1.4 裁定 T3 以 `data/calendar_tpe.json` 實算，驗證段波段（區塊數量近似值 "
      f"{BLOCKS_TABLE[('valid', 'swing')]:.2f}）與中期（{BLOCKS_TABLE[('valid', 'mid')]:.2f}）、訓練段中期（{BLOCKS_TABLE[('train', 'mid')]:.2f}）"
      f"都 < {BLOCKS_MIN} → 一律「證據不足」，門檻不下修、保留段不提前（§1.3 `:270`、§1.4 `:299-302`）。"
      f"**這是預期中的產品結果，不是失敗**；下表先列它。「區塊數量近似值」＝有效日數 ÷ `max(21, 3h)`，相除不證明區塊互相獨立。")
    A("")
    A("| 段 | 市場 | 期間（h） | 區塊數量近似值（T5） | IC 均值 | NW t（lag h） | verdict | 主要／次要／穩健（代理） |")
    A("|---|---|---|---:|---:|---:|---|---|")
    for seg_name, rep in (("valid", valid), ("train", train)):
        for hz in ("swing", "mid", "short"):
            for mk in ("twse", "tpex"):
                c = _cell(rep, mk, hz)
                i = c["ic"]
                tri = ("—" if c["verdict"] == "insufficient"
                       else f"{'過' if c['primary_pass'] else '不過'}／{'過' if c['secondary_pass'] else '不過'}／{'過' if c.get('robust_pass') else '不過'}（代理）")
                A(f"| {SEG_LABEL[seg_name]} | {M_LABEL[mk]} | {H_LABEL[hz]}（{c['h']}） | {c['window']['n_blocks_t5']:.2f}"
                  f"{'' if c['window']['n_blocks_t5'] >= BLOCKS_MIN else f' < {BLOCKS_MIN}'} | {_n(i['ic_mean'])} | {_n(i['nw_t_h'], 2)} | "
                  f"**{c['verdict']}** | {tri} |")
    A("")
    A("穩健欄「（代理）」：登錄書 `:284` 的「未參與選擇的年度同號」本批無未見年度可驗，以段內逐年同號代理（§2 表、§5）；「三段同號」只比得到訓練／驗證兩段。")
    A("")
    vs = {mk: _cell(valid, mk, "short") for mk in ("twse", "tpex")}
    A(f"驗證段短線兩格區塊數量近似值 {BLOCKS_TABLE[('valid', 'short')]:.2f} ≥ {BLOCKS_MIN}，可套門檻：twse IC 均值 {_n(vs['twse']['ic']['ic_mean'])}"
      f"（NW t {_n(vs['twse']['ic']['nw_t_h'], 2)}）、tpex {_n(vs['tpex']['ic']['ic_mean'])}（NW t {_n(vs['tpex']['ic']['nw_t_h'], 2)}），"
      f"主要門檻 IC ≥ {IC_MIN} 且 NW t ≥ {T_MIN} 兩格皆{'不' if not (vs['twse']['primary_pass'] or vs['tpex']['primary_pass']) else ''}過"
      f"→ verdict `{vs['twse']['verdict']}`／`{vs['tpex']['verdict']}`。**{NOMINAL_SENTENCE}**（§1.5 裁定 T8）；且本段是調參段，"
      f"此處的過／不過都不是保留段證據。")
    _assert(not vs["twse"]["primary_pass"] and not vs["tpex"]["primary_pass"], "上句寫「兩格皆不過」，但有格主要過了——改寫句子")
    A("")
    # ---------------------------------------------------------------- ② 方法與裁定對照
    A("## 2. 方法與裁定對照")
    A("")
    A("| 項目 | 本報告做法 | 出處 |")
    A("|---|---|---|")
    A("| IC | 每日 Spearman(`base_score`, 原始 `fwd_ret`)（平均秩）的日序列均值；NW 標準誤 lag＝h 主、2h 敏感度 | §1.4 `:277-278`；裁定 #72 Q11 |")
    A(f"| 區間 | 循環區塊 bootstrap，區塊 `max(21,3h)`＝{P['block_len']['short']}／{P['block_len']['swing']}／{P['block_len']['mid']}，"
      f"{P['nboot']:,} 次，seed {P['seed']}；h=40 另報區塊 {P['block_sensitivity']['block']} 日 | §1.3 `:269`、v1.2.2 `:573`；Q13 |")
    A(f"| purge | 索引法 `e=i+1`、`x=i+1+h`，`x ≥ 下一段首個交易日索引` 的訊號日排除（訓練→驗證邊界 {valid['segment_info']['first_trading_day']}、"
      f"驗證→保留邊界 {valid['segment_info']['next_segment_first_day']}）；排除訊號日 h+1＝11／21／41 | §1.3 `:266`、§16.5 `:721-722` |")
    A(f"| embargo | 驗證段起點後 {EMBARGO_DAYS} 個交易日的訊號不評估（{_cell(valid, 'twse', 'short')['window']['seg_start']}～"
      f"{_cell(valid, 'twse', 'short')['window']['embargo_last_day']}）；訓練段不扣 | §1.3 `:267`、§1.4 `:288` |")
    A("| 有效日數／區塊數 | 區塊數量近似值照 T5 公式（段日數 − h − embargo）÷ 區塊長；實際排除照索引法（多 1 日）；兩數都列 | §1.4 `:288-297`；§5b 連帶既定 |")
    A(f"| 次要① | 每日每格池內 `base_score` 先換平均秩再切十分位（Q17），頂減底的 `net_ret_long`（slip {P['slip_base'] * 100:.1f}%）均值，"
      f"月度（訊號日曆月）均值為正的月份比例 ≥ {POS_SHARE_MIN:.0%} | §1.4 `:281`；Q11／Q17 |")
    A(f"| 次要② | {CANDIDATE_NAME}對全池等權 `net_ret_long` 超額 > 0 且 bootstrap 95% 區間不含 0；入場門檻 T0 未定案，"
      f"頂十分位為**代理定義、登錄書未定** | §1.4 `:281-282`；Q14 |")
    A(f"| 空方另報 | 底十分位的 `net_ret_short`（借券年化 {P['borrow_base'] * 100:.0f}%，曆日/365，名目出場日）日均值＋bootstrap 區間；"
      f"個股側受借券限制、可執行性未驗證 | §1.4 `:282`；Q11 |")
    A(f"| 成本 | 手續費 {P['fee'] * 100:.4f}%×2、稅 {P['tax'] * 100:.1f}%、滑價 {P['slip_base'] * 100:.1f}%（敏感度 "
      f"{'／'.join(f'{s * 100:.1f}%' for s in P['slip_grid'])}）、借券 {'／'.join(f'{b * 100:.0f}%' for b in P['borrow_grid'])}；"
      f"`fwd_ret=0` 時 `net_ret_long`＝{P['zero_fwd_net_long'] * 100:.4f}%、`net_ret_short`（0 曆日）＝{P['zero_fwd_net_short'] * 100:.4f}%"
      f"——兩式不對稱（長方比式、空方差式），差 {abs(P['zero_fwd_net_long'] - P['zero_fwd_net_short']) * 1e4:.2f} bp | §1.2.1 `:91`；Q11 |")
    A(f"| 列處理 | 只算 `in_rank_pool=1`（Q9）→ 評估窗 → `entry_limit_up=1` 整列排除、`halt`／`delist` 保留、空 `fwd_ret`／空 `base_score` 落掉、"
      f"`coverage=reweighted` 只計比例（Q10）→ 日內有效配對 < {P['min_pairs']} 不進序列（Q12）；每步計數見 §8 | 裁定 #72 Q9～Q12 |")
    A(f"| 卦別分組 | 只套用 `data/rank_table.json`（sha256 `{valid['gate']['rank_table_sha256'][:12]}…`＝`tests/test_prereg_frozen.py` 釘值）的"
      f"高／中／低組，不重排；附錄 A 當時未過濾 `in_rank_pool`，與本報告口徑不同、如實揭露 | §1.6；Q9 |")
    A("| tpex 大盤 | 無可交易代理、不評估；K 表對應格「未評估」 | Q15 |")
    A("| 穩健「未參與選擇的年度同號」 | 本批**沒有任何未見年度**（2021～2023H1 訓練、2023H2～2024 調參），以**段內逐年 IC 同號**為代理計入 verdict；"
      "真正的未見年度要等保留段。「三段同號」亦只比得到兩段 | §1.4 `:284`；本層代理，報告 §1 表標「（代理）」 |")
    A("| 只報三項 | 夏普（日序列 mean/std，不年化）、最大回撤（超額序列 cumsum 自高點回落）、月勝率（月加總 > 0）；"
      "口徑為本層選擇、登錄書未定義、不設門檻、不進 K | §1.4 裁定 T4 |")
    A(f"| 借用函式 | `block_boot_ci`／`nw_se` 借自 shihpc/taiwan-backtest `676c69b`（{P['nw_note']}） | PR-S1 |")
    A("")
    # ---------------------------------------------------------------- ③ 每格數字
    A("## 3. 每格數字")
    A("")
    for seg_name, rep in (("valid", valid), ("train", train)):
        si = rep["segment_info"]
        A(f"### 3.{1 if seg_name == 'valid' else 2} {SEG_LABEL[seg_name]}（{si['first_trading_day']}～{si['last_trading_day']}，{si['n_days']} 交易日；"
          f"embargo {rep['params']['embargo_days']} 日）")
        A("")
        A("| 市場 | 期間 | 合格訊號日（索引法） | 有效日（T5） | 區塊數 T5／索引法 | IC 日數 | IC 均值 | IC 標準差 | 正 IC 日比例 | NW se／t（lag h） | NW se／t（lag 2h） | bootstrap 95% |")
        A("|---|---|---:|---:|---|---:|---:|---:|---:|---|---|---|")
        for hz in ("short", "swing", "mid"):
            for mk in ("twse", "tpex"):
                c = _cell(rep, mk, hz)
                w, i = c["window"], c["ic"]
                A(f"| {M_LABEL[mk]} | {H_LABEL[hz]} | {w['n_eff_index']} | {w['n_eff_t5']} | {w['n_blocks_t5']:.2f}／{w['n_blocks_index']:.2f} | "
                  f"{i['n_days_used']} | {_n(i['ic_mean'])} | {_n(i['ic_std'])} | {_pct(i['ic_positive_share'])} | "
                  f"{_n(i['nw_se_h'])}／{_n(i['nw_t_h'], 2)} | {_n(i['nw_se_2h'])}／{_n(i['nw_t_2h'], 2)} | {_ci(i['boot_ci'])} |")
        A("")
        A("| 市場 | 期間 | 次要① 正月比例（正月／月數） | 頂減底均值 | 次要② 超額均值 | 超額 bootstrap 95% | 空方底十分位均值 | 空方 bootstrap 95% | 夏普（頂減底） | 夏普（超額） | 最大回撤（超額 cumsum） | 月勝率（超額） |")
        A("|---|---|---|---:|---:|---|---:|---|---:|---:|---:|---:|")
        for hz in ("short", "swing", "mid"):
            for mk in ("twse", "tpex"):
                c = _cell(rep, mk, hz)
                s1, s2, ss, ro = c["secondary1"], c["secondary2"], c["short_side"], c["report_only"]
                A(f"| {M_LABEL[mk]} | {H_LABEL[hz]} | {_pct(s1['pos_share'])}（{s1['pos_months']}／{s1['n_months']}） | {_n(s1['spread_mean'])} | "
                  f"{_n(s2['excess_mean'])} | {_ci(s2['boot_ci'])} | {_n(ss['bottom_short_mean'])} | {_ci(ss['boot_ci'])} | "
                  f"{_n(ro['sharpe_spread_daily'], 3)} | {_n(ro['sharpe_excess_daily'], 3)} | {_n(ro['max_drawdown_excess_cumsum'], 3)} | "
                  f"{_pct(ro['monthly_winrate_excess'])} |")
        A("")
        mids = [_cell(rep, mk, "mid") for mk in ("twse", "tpex")]
        A(f"h=40 的區塊 {P['block_sensitivity']['block']} 日敏感度（v1.2.2 `:573`）：twse {_ci(mids[0]['ic']['boot_ci_block_sensitivity'])}、"
          f"tpex {_ci(mids[1]['ic']['boot_ci_block_sensitivity'])}（區塊 120 日：{_ci(mids[0]['ic']['boot_ci'])}／{_ci(mids[1]['ic']['boot_ci'])}）"
          f"——中期兩格本就「證據不足」，區間只作揭露。只報三項的口徑見 §2；超額序列是 h 日重疊報酬逐日相加，最大回撤的量級約被放大 h 倍，"
          f"**不可讀成資金曲線**。")
        A("")
        A("逐日 IC 序列（日期、ρ、配對數）在 json `cells[*].ic.dates／series／pairs`，供審計重算。")
        A("")
    # ---------------------------------------------------------------- ④ 成本敏感度
    A("## 4. 成本敏感度（§1.4 穩健「成本敏感度不翻轉」）")
    A("")
    A("「翻轉」＝次要①②合併的過／不過結果、或次要②超額均值的正負號，與基準（滑價 0.2%、借券 2%）不同。借券欄＝底十分位空方淨報酬日均值的正負號是否改變。")
    A("")
    A("| 段 | 市場 | 期間 | slip 0.1%：正月比例／超額／翻轉 | slip 0.2%（基準） | slip 0.3% | 借券 1%：空方均值／翻轉 | 借券 2%（基準） | 借券 4% | 任一翻轉 |")
    A("|---|---|---|---|---|---|---|---|---|---|")
    for seg_name, rep in (("valid", valid), ("train", train)):
        for hz in ("short", "swing", "mid"):
            for mk in ("twse", "tpex"):
                c = _cell(rep, mk, hz)
                g = c["cost_grid"]
                sl = [g["slip"][k] for k in ("0.001", "0.002", "0.003")]
                br = [g["borrow"][k] for k in ("0.01", "0.02", "0.04")]
                A(f"| {SEG_LABEL[seg_name]} | {M_LABEL[mk]} | {H_LABEL[hz]} | "
                  + " | ".join(f"{_pct(x['pos_share'])}／{_n(x['excess_mean'])}／{'翻轉' if x['flip_vs_base'] else '否'}" for x in sl)
                  + " | " + " | ".join(f"{_n(x['bottom_short_mean'])}／{'翻轉' if x['flip_vs_base'] else '否'}" for x in br)
                  + f" | {'有' if g['any_slip_flip'] or g['any_borrow_flip'] else '無'} |")
    A("")
    # ---------------------------------------------------------------- ⑤ 穩健
    A("## 5. 穩健：兩段同號與逐年表（§1.4 `:284`、§16.5 `:725`）")
    A("")
    A("登錄書要的是「三段主要指標同號、未參與選擇的年度同號」；保留段未跑，這裡只比得到訓練／驗證兩段，逐年只有訓練（2021、2022、2023H1）"
      "與調參（2023H2、2024）兩種角色——**沒有任何年度是「未見」**，2025 起的年份屬保留段、本批未跑。調參年度不得稱為獨立證據。")
    A("")
    A("| 市場 | 期間 | 訓練段 IC | 驗證段 IC | 兩段同號 | 2021（訓練） | 2022（訓練） | 2023H1（訓練） | 2023H2（調參） | 2024（調參） | 段內逐年同號（訓練／驗證） |")
    A("|---|---|---:|---:|---|---:|---:|---:|---:|---:|---|")
    for hz in ("short", "swing", "mid"):
        for mk in ("twse", "tpex"):
            ct, cv = _cell(train, mk, hz), _cell(valid, mk, hz)
            yt = {y["year"]: y for y in ct["yearly"]}
            yv = {y["year"]: y for y in cv["yearly"]}
            _assert(set(yt) == {"2021", "2022", "2023"} and set(yv) == {"2023", "2024"}, f"{mk}/{hz} 逐年表的年份不是預期集合")
            _assert(cv["robust"]["segments_same_sign"] == ct["robust"]["segments_same_sign"], "兩段 json 的同號判定不一致")
            A(f"| {M_LABEL[mk]} | {H_LABEL[hz]} | {_n(ct['ic']['ic_mean'])} | {_n(cv['ic']['ic_mean'])} | "
              f"{'是' if cv['robust']['segments_same_sign'] else '否'} | {_n(yt['2021']['ic_mean'])} | {_n(yt['2022']['ic_mean'])} | "
              f"{_n(yt['2023']['ic_mean'])} | {_n(yv['2023']['ic_mean'])} | {_n(yv['2024']['ic_mean'])} | "
              f"{'是' if ct['robust']['years_same_sign'] else '否'}／{'是' if cv['robust']['years_same_sign'] else '否'} |")
    A("")
    # ---------------------------------------------------------------- ⑥ K=216
    A("## 6. 多重比較揭露：K＝216（§1.5 裁定 T6～T8，只揭露不校正）")
    A("")
    n_total_cells = 2 * 3 * 2
    k_stock, k_mkt_side = K_LINES * K_H * K_MARKET * K_WEIGHTS, K_LINES * K_H * K_WEIGHTS
    _assert(k_stock + 2 * k_mkt_side == K_TOTAL, "K 的互斥分割加總 ≠ 216")
    A(f"**K＝{K_TOTAL}＝爻({K_LINES}) × horizon({K_H}) × scope({K_SCOPE}：大盤／個股) × market({K_MARKET}) × 權重候選({K_WEIGHTS})**"
      f"＝72 個爻層假說 × 3 組事先列舉的權重候選（①P1 起點權重 ②全等權 ③驗證段聯合估計；凍結後不得增加第四組）。「段」不是 K 的維度。")
    A("")
    A(f"**K 的 {K_TOTAL} 格本次 0 格實算。** 本報告另算的是 {n_total_cells} 個**總分格**（個股 scope × 2 市場 × 3 h × 2 段，`base_score`＝權重候選①下六爻的聚合量），"
      f"**不在 K 內**——K 計的是逐爻假說，總分不是其子集，不得讀成 {n_total_cells}/{K_TOTAL}。K 的 {K_TOTAL} 格互斥分割與未算原因：")
    A("")
    A("| K 的分割（互斥） | 格數 | 原因 |")
    A("|---|---:|---|")
    A(f"| 個股 scope：6 爻 × 3 h × 2 市場 × 3 權重候選 | {k_stock} | 資料集不帶六爻分數（裁定 #50 Q15：爻層假說要做時另匯），候選②全等權／③驗證段聯合估計也需逐爻分數重新聚合"
      f"——同一次 Hetzner 出口（PR-S3 候選） |")
    A(f"| 大盤 scope（twse 側）：6 爻 × 3 h × 3 權重候選 | {k_mkt_side} | 資料集不匯 `__MARKET__` 列（manifest `n_market_rows_excluded`＝{valid['gate']['n_market_rows_excluded']:,}）"
      f"且無 TX 價格（§1.2.4 成本需逐筆點位）；TX 成本純函式已在 `iching.stats.cost` |")
    A(f"| 大盤 scope（tpex 側）：6 爻 × 3 h × 3 權重候選 | {k_mkt_side} | 登錄書只寫 TX 為代理工具，tpex 側無可交易代理 → **不評估**（裁定 #72 Q15） |")
    A(f"| 合計 | {K_TOTAL} | ＝K |")
    A("")
    A(f"**{NOMINAL_SENTENCE}**（§1.5 `:317-318` 原句），讀者自行打折；流動性門檻 0.3 億／日本身也是一個假說維度（§1.5 `:315`），本報告只用這一個值。"
      f"為什麼不校正的理由寫在 §1.5 `:320-324`，此處不複述。")
    A("")
    # ---------------------------------------------------------------- ⑦ §16.5 守門
    A("## 7. §16.5 守門表（`scripts/backtest_gate.py`，`runs/backtest/gate_" + date_tag + ".txt`）")
    A("")
    A(f"v1.2.2 §16.5 的表共 **{len(gate_rows)} 列**（`spec/stock-iching-plan-v1.2.2.md:{BG.SPEC_TABLE_FIRST}-{BG.SPEC_TABLE_LAST}`，實作者逐列重數；"
      f"`docs/P3-KICKOFF.md` 完成定義 #7 寫「十六列」是扣掉附錄 B／C 已結案的四列）。{gate_summary}。本層可機械驗的列全部 PASS；"
      f"不屬本層的列標 N-A 並指到另案或已有工具，不假裝驗過。")
    A("")
    A("| v1.2.2 行 | 條件 | 判定 | 證據（節錄） |")
    A("|---|---|---|---|")
    for r in gate_rows:
        ev = r["evidence"].replace("|", "／")
        if len(ev) > 180:
            ev = ev[:177] + "…"
        A(f"| `:{r['line']}` | {r['name']} | {r['status']} | {ev} |")
    A("")
    A("完整證據（逐格列舉）見守門 txt。")
    A("")
    # ---------------------------------------------------------------- ⑧ 資料揭露
    A("## 8. 資料揭露：列處理計數（裁定 #72 Q9～Q12；`raw` 為窗內獨立母體、其餘為歸屬數）")
    A("")
    A("| 段 | 市場 | 期間 | 檔內列 | 池內（Q9） | purge 列 | embargo 列 | 窗內 | 排除 limit_up（raw） | 落掉空 fwd_ret（raw） | 落掉空 base_score（raw） | 計入 | 保留 halt／delist／exit_limit_down | reweighted 比例 | 同分比例（Q17） | 配對 <30 日數 |")
    A("|---|---|---|---:|---:|---:|---:|---:|---|---|---|---:|---|---:|---:|---:|")
    for seg_name, rep in (("valid", valid), ("train", train)):
        for hz in ("short", "swing", "mid"):
            for mk in ("twse", "tpex"):
                c = _cell(rep, mk, hz)
                r, raw = c["rows"], c["rows"]["raw"]
                A(f"| {SEG_LABEL[seg_name]} | {M_LABEL[mk]} | {H_LABEL[hz]} | {r['in_file']:,} | {r['in_pool']:,} | {r['purged_rows']:,} | {r['embargo_rows']:,} | "
                  f"{r['in_window']:,} | {r['excluded_entry_limit_up']}（{raw['entry_limit_up']}） | {r['skipped_null_fwd_ret']}（{raw['null_fwd_ret']}） | "
                  f"{r['skipped_null_base_score']:,}（{raw['null_base_score']:,}） | {r['counted']:,} | {r['kept_halt']}／{r['kept_delist']}／{r['kept_exit_limit_down']} | "
                  f"{_pct(r['reweighted_share'], 2)} | {_pct(r['tie_share'], 2)} | {c['ic']['days_below_min_n']} |")
    A("")
    A("檔案層計數（列數、`entry_limit_up`、`fwd_ret` 缺、halt／delist）開跑時已逐檔與 `manifest.json` 核對相符（不符 rc=2）；"
      "三檔 sha256＝manifest。`no_entry` 列的 `fwd_ret` 為空、在「落掉空 fwd_ret」計入。")
    A("")
    A("卦別分組報酬（§1.6，只套用凍結排序表；`net_ret_long` 均值，描述性、不進判定）：")
    A("")
    A("| 段 | 市場 | 期間 | 高組（卦／列／淨報酬均值） | 中組 | 低組 |")
    A("|---|---|---|---|---|---|")
    for seg_name, rep in (("valid", valid), ("train", train)):
        for hz in ("short", "swing", "mid"):
            for mk in ("twse", "tpex"):
                hg = _cell(rep, mk, hz)["hexagram_groups"]
                A(f"| {SEG_LABEL[seg_name]} | {M_LABEL[mk]} | {H_LABEL[hz]} | "
                  + " | ".join(f"{hg[g]['n_hexagrams']}／{hg[g]['n_rows']:,}／{_n(hg[g]['mean_net'])}" for g in ("high", "mid", "low")) + " |")
    A("")
    # ---------------------------------------------------------------- ⑨ 限制
    A("## 9. 限制")
    A("")
    ties = max(c["rows"]["tie_share"] for rep in (train, valid) for c in rep["cells"])
    A(f"1. **K 的 {K_TOTAL} 格本次 0 格實算**，只算了 {n_total_cells} 個不在 K 內的總分格（§6）；結論範圍限個股 scope、權重候選①的總分。")
    A(f"2. **{CANDIDATE_NAME}是代理定義**：入場門檻 T0 未定案（登錄書 `:398`），頂十分位是裁定 #72 Q14 在看結果前定的代理，不是登錄書字面。")
    A("3. **tpex 大盤不評估**（Q15）：登錄書只給 TX 為代理工具，tpex 側沒有可交易代理；本報告沒有任何大盤側數字。")
    A(f"4. **同分比例**：各格同日同分比例最大 {_pct(ties, 3)}（`base_score` 為全精度浮點，實務上無同分）；Q17 的平均秩規則因此在本份資料上不改變任何分組。")
    A(f"5. **Q11 兩式不對稱**：`net_ret_long` 是比式 `(1+fwd)(1−s)(1−f−t)/[(1+s)(1+f)]−1`、`net_ret_short` 是差式"
      f" `(1−s)(1−f−t)−(1+fwd)(1+s)(1+f)−借券`，`fwd=0` 時 {P['zero_fwd_net_long'] * 100:.4f}% 對 {P['zero_fwd_net_short'] * 100:.4f}%；"
      f"空方借券曆日數用名目出場日 `x=i+1+h`（halt／delist 提前出場的真實曆日更短，此處偏保守）。空方可執行性未驗證（券源）。")
    A("6. **主要指標 IC 用原始 `fwd_ret`、未扣成本**（Q11）；`fwd_ret` 本身的口徑（T+1 開盤進、T+1+h 收盤出、後復權、漲跌停 10% 近似旗標）見 `scripts/export_dataset.py` 檔頭。")
    A("7. **逐年表沒有未見年度**（§5）；「三段同號」只比得到兩段。保留段未跑、未匯出，§16.5 `:729` 標 N-A（未跑）。")
    A("8. **附錄 A 與本報告口徑不同**：排序表當時未過濾 `in_rank_pool`（凍結事實），本報告只算池內（Q9）；分組報酬因此不可與附錄 A 的均值逐位對照。")
    A(f"9. **執行環境**：numpy {valid['meta']['numpy_version']}、Python {valid['meta']['python_version']}；驗證段耗時 {valid['meta']['elapsed_sec']} s、峰值 RSS "
      f"{valid['meta']['max_rss_kb'] / 1024:.0f} MB，訓練段 {train['meta']['elapsed_sec']} s、{train['meta']['max_rss_kb'] / 1024:.0f} MB（程序內 `getrusage`；"
      f"`cache_used` 非空表示該次由 npz 快取載入、不含 csv 解析）。bootstrap 抽樣依賴 `numpy.random.default_rng(42)`，換 numpy 版本是否逐位重現未查證。")
    A("")
    return "\n".join(L) + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="P3 回測統計層報告 docs/P3-BACKTEST-VALID.md（整檔生成）")
    ap.add_argument("--date", default=time.strftime("%Y-%m-%d"))
    ap.add_argument("--train", default=None)
    ap.add_argument("--valid", default=None)
    ap.add_argument("--gate", default=None)
    ap.add_argument("--out", default=str(OUT))
    ap.add_argument("--check", action="store_true", help="只比對、不寫入；內容會變就 rc=1")
    args = ap.parse_args(argv)
    try:
        d = REPO / "runs" / "backtest"
        train = json.loads(Path(args.train or d / f"train_{args.date}.json").read_text(encoding="utf-8"))
        valid = json.loads(Path(args.valid or d / f"valid_{args.date}.json").read_text(encoding="utf-8"))
        gate = Path(args.gate or d / f"gate_{args.date}.txt").read_text(encoding="utf-8")
        new = build(train, valid, gate, args.date)
        out = Path(args.out)
        if args.check:
            if not out.exists() or out.read_text(encoding="utf-8") != new:
                print("[backtest_report] 報告與 json／守門不一致，請重跑本腳本", file=sys.stderr)
                return 1
            print("== 報告與 json 一致")
            return 0
        out.write_text(new, encoding="utf-8")
        print(f"== 已寫 {out}")
        return 0
    except Exception as e:  # noqa: BLE001 — 任何例外一律 rc=2（與 --check 的 rc=1 分開）
        print(f"[backtest_report 中止] {type(e).__name__}: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
