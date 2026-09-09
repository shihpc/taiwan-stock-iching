#!/usr/bin/env python3
"""維度宣告機檢（v1.2.2 §16.5「鍵維度涵蓋」列的實作）。
規則見 P1-B5-dimensions.md §B5.0。零輸出＝通過。
R5i：依故障注入測試補強——旗標段、標的名單、空鍵、重複維度、count=null 不再靜默跳過、孤兒維度。"""
import json, sys
D = json.load(open('dimensions.json', encoding='utf-8'))
dims, targets, flags = D['dimensions'], D['targets'], D['flags']
err = []

# 規則 6（R5i 實作）：標的與旗標的**名單本身**是必備清單，缺任一即不合格
REQUIRED_TARGETS = {"indicator_params","line_score","line_reachable_range","aggregate_reachable_range",
    "hysteresis_state","formation_path","industry_aggregate","event_version_chain","threshold_T0_N0",
    "flag","clip_ratio_stat","scores_db_row","hexagram_text","trading_calendar","upper_line_asof","p_cs","threshold_revalidation"}
REQUIRED_FLAGS = {"F-臨界","F-廣度擴張","F-廣度收縮","F-高波動","F-分歧"}
for n in REQUIRED_TARGETS - set(targets):
    err.append(f"[規則6] 必備標的 `{n}` 未登錄於 targets")
for n in REQUIRED_FLAGS - set(flags):
    err.append(f"[規則6] 必備旗標 `{n}` 未登錄於 flags")

ALL = list(targets.items()) + [(f'flag:{k}', v) for k, v in flags.items()]

# 規則 1：鍵只能引用已宣告的維度名
for name, t in ALL:
    for k in (t.get('key') or []):
        if k not in dims:
            err.append(f"[規則1] {name} 的鍵 `{k}` 未在 dimensions 宣告")

# 規則 2r（R5i 擴充）：每個標的與旗標都必須有**非空**鍵，且不得重複維度
for name, t in ALL:
    key = t.get('key')
    if name == 'flag':          # flag 是指向 flags 段的佔位
        continue
    if not key:
        err.append(f"[規則2r] {name} 未宣告鍵（或為空）")
    elif len(key) != len(set(key)):
        dup = [k for k in set(key) if key.count(k) > 1]
        err.append(f"[規則2r] {name} 的鍵有重複維度 {dup}")

# 規則 3：已知必帶維度
MUST = {'line_reachable_range': ['market','horizon','line','scope','coverage'],
        'aggregate_reachable_range': ['market','horizon','scope','direction','aggregate_name','coverage'],
        'threshold_T0_N0': ['state','direction','horizon','market'],
        'scores_db_row': ['market','horizon','stock_id','tpe_trading_date','model_version','data_version','text_version'],
        'indicator_params': ['market','scope','horizon'],
        'line_score': ['market','horizon','line','stock_id'],
        'hysteresis_state': ['market','horizon','line','stock_id'],
        'formation_path': ['market','horizon','stock_id','tpe_trading_date','line','flip_direction'],
        'clip_ratio_stat': ['market','horizon','indicator_id','segment']}
for name, need in MUST.items():
    k = set(targets.get(name, {}).get('key') or [])
    for d in need:
        if d not in k:
            err.append(f"[規則3] {name} 必帶維度 `{d}` 缺席")

# 規則 3f（R5i 新增）：五支旗標必帶 market／horizon／direction
for fname, t in flags.items():
    k = set(t.get('key') or [])
    if not k:
        err.append(f"[規則3f] 旗標 `{fname}` 未宣告鍵（或為空）")
    for d in ('market','horizon','direction'):
        if d not in k:
            err.append(f"[規則3f] 旗標 `{fname}` 必帶維度 `{d}` 缺席"
                       f"（R5i：先前『F-高波動／F-分歧 無 market』的豁免已由 R5h 撤回，此規則防止無聲復原）")

# 規則 5：宣告筆數 ＝ 各維度基數乘積（扣除顯式 exclude）。exclude 的合法性**一律**檢查
for name, t in targets.items():
    exc = t.get('exclude') or {}
    key = t.get('key') or []
    for k, vs in exc.items():
        if k not in key:
            err.append(f"[規則5] {name} 的 exclude 提到非鍵維度 `{k}`")
        for v in vs:
            if v not in (dims.get(k, {}).get('values') or []):
                err.append(f"[規則5] {name} 的 exclude 值 `{k}={v}` 不在該維度值域內")
    c = t.get('count')
    if exc and c is None:
        err.append(f"[規則5] {name} 宣告了 exclude 卻沒有 count——exclude 只在驗算筆數時生效，"
                   f"掛在 count=null 的標的上會被靜默忽略（R5i 故障注入 e3）")
    if c is None:
        # 值域全封閉卻不宣告 count → 漏掉一次驗算機會，要求補
        if key and all(dims.get(k, {}).get('values') for k in key):
            err.append(f"[規則5] {name} 的鍵全為封閉值域，必須宣告 count 以供驗算（不得留 null）")
        continue
    card, ok = [], True
    for k in key:
        v = dims.get(k, {}).get('values')
        if v is None: ok = False; break
        card.append(len([x for x in v if x not in exc.get(k, [])]))
    if not ok:
        err.append(f"[規則5] {name} 宣告 count={c}，但鍵含開放值域維度，無法驗算")
    else:
        prod = 1
        for n in card: prod *= n
        if prod != c:
            err.append(f"[規則5] {name} 宣告 count={c}，各維度基數乘積為 {prod}（{'×'.join(map(str,card))}）")

# 規則 8（R5j 實作）：封閉維度的值域凍結——改名／增刪值即報錯（原僅為條文，無程式）
frozen = D.get('_frozen_values') or {}
# R5k：原有一個未加註解的 `k != 'king_wen'` 豁免，已移除——無理由的特例正是本案要避免的東西。
# king_wen 的 64 值現已納入 _frozen_values，其值域改名（如 "1"→"01"，基數不變）因此也會被抓。
for k, vals in frozen.items():
    cur = dims.get(k, {}).get('values')
    if cur is None:
        err.append(f"[規則8] 維度 `{k}` 曾宣告封閉值域，現已消失或改為開放值域")
    elif list(cur) != list(vals):
        err.append(f"[規則8] 維度 `{k}` 的值域被改動：{vals} → {cur}"
                   f"（值域語意漂移／改名不會被其他規則發現，故獨立凍結；"
                   f"確係刻意變更時須同步更新 _frozen_values 並在 B5 記錄理由）")
for k, meta in dims.items():
    if meta.get('values') and k not in frozen:
        err.append(f"[規則8] 封閉值域維度 `{k}` 未列入 _frozen_values，值域改動將無人發現")

# 規則 7：維度名語意碰撞——已解的對子雙方都須存在，且**舊名不得再被當新義使用**
for a, b in [('direction','flip_direction'), ('coverage','coverage_ratio')]:
    if a not in dims or b not in dims:
        err.append(f"[規則7] 碰撞對 ({a}, {b}) 未同時宣告，碰撞未解")

# 規則 9（R5i 新增）：孤兒維度——宣告了卻沒有任何鍵引用，通常表示「正本改名但下游沒改」
used = set()
for _, t in ALL: used |= set(t.get('key') or [])
for k, meta in dims.items():
    if meta.get('key_dimension') is False: continue   # 欄位名，宣告以防碰撞、不參與鍵
    if k not in used:
        err.append(f"[規則9] 維度 `{k}` 已宣告但**無任何鍵引用**（孤兒）"
                   f"——多半是「正本改名／新增，下游沒跟」的徵兆，須確認消費端")

for e in err: print("  ✗", e)
print(f"維度機檢：{len(targets)} 個標的 / {len(flags)} 支旗標 / {len(dims)} 個維度，問題 {len(err)} 處")
sys.exit(1 if err else 0)
