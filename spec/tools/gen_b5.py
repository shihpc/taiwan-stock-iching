#!/usr/bin/env python3
"""由 dimensions.json 生成 P1-B5-dimensions.md。md 不得手改。"""
import json, io
D=json.load(open('dimensions.json',encoding='utf-8'))
HEAD=open('b5_head.md',encoding='utf-8').read()
o=io.StringIO(); w=o.write
w(HEAD)
w("\n## B5.1 維度全集\n\n**本節與 §B5.2／§B5.3 由 `dimensions.json` 經 `gen_b5.py` 生成，`P1-B5-dimensions.md` 不得手改**——"
  "R5h 驗收指出：Markdown 表格夾散文無法穩定解析，機檢會退化回人工掃描（即已失敗八次的做法）。\n\n")
w("| 維度 | 值域 | 說明 |\n|---|---|---|\n")
for k,v in D['dimensions'].items():
    vals='／'.join(f"`{x}`" for x in v['values']) if v['values'] else '（開放值域）'
    w(f"| `{k}` | {vals} | {v['note']} |\n")
w("\n## B5.2 逐標的的必要維度\n\n| 標的 | 必要維度 | 宣告筆數 | 備註 |\n|---|---|---:|---|\n")
for k,t in D['targets'].items():
    key='　×　'.join(f"`{x}`" for x in t['key']) if t.get('key') else '見 §B5.3'
    c=t.get('count'); cs=str(c) if c else '—'
    ex=t.get('exclude')
    if ex: cs+=f"（排除 {'、'.join(f'{a}={b}' for a,vs in ex.items() for b in vs)}）"
    w(f"| `{k}`　{t['label']} | {key} | {cs} | {t.get('note','')} |\n")
w("\n> **筆數由 `check_dims.py` 規則 5 驗算**＝各維度基數乘積（扣除顯式 `exclude`）。不符即機檢不過。\n")
w("\n## B5.3 五支旗標的鍵\n\n| 旗標 | 必要維度 | 備註 |\n|---|---|---|\n")
for k,t in D['flags'].items():
    w(f"| `{k}` | {'　×　'.join(f'`{x}`' for x in t['key'])} | {t.get('note','')} |\n")
w("\n**R5h 撤回 R5g 的兩個豁免**：原宣稱 `F-高波動` 無 `market`（VIX 單一序列）、`F-分歧` 無 `market`（條件跨市場），"
  "**兩個都錯**——前者的降級路徑用大盤 ATR÷I 的 250 日百分位、TAIEX／TPEx 各一條；"
  "後者判定式的第二個 disjunct（內卦 ≥55 且外卦 ≤45）是該市場自己的六爻，加權可能觸發而櫃買不觸發。五支旗標現皆帶 `market`。\n")
w(open('b5_tail.md',encoding='utf-8').read())
open('P1-B5-dimensions.md','w',encoding='utf-8').write(o.getvalue())
print("已生成 P1-B5-dimensions.md")
