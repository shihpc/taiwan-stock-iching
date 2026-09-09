#!/usr/bin/env python3
"""check_dims.py 的故障注入回歸測試（R5i）。每個案例注入一種已知錯誤，必須被抓到。"""
import json, subprocess, copy, sys, os, tempfile, shutil
# R5k：工具可能被放在 tools/ 子目錄，check_dims.py 一律以**本檔所在目錄**解析；
# dimensions.json 則維持相對 cwd（資料在 spec/ 根，工具在 spec/tools/）。
HERE = os.path.dirname(os.path.abspath(__file__))
BASE=json.load(open('dimensions.json',encoding='utf-8'))
def run(d):
    tmp=tempfile.mkdtemp()
    try:
        shutil.copy(os.path.join(HERE, 'check_dims.py'), tmp+'/check_dims.py')
        json.dump(d, open(tmp+'/dimensions.json','w',encoding='utf-8'), ensure_ascii=False)
        r=subprocess.run([sys.executable,'check_dims.py'],cwd=tmp,capture_output=True,text=True)
        return r.returncode!=0
    finally: shutil.rmtree(tmp)
cases={}
def C(name):
    def deco(fn):
        d=copy.deepcopy(BASE); fn(d); cases[name]=d
    return deco
@C("a1 爻可達區間拿掉 market")
def _(d): d['targets']['line_reachable_range']['key'].remove('market')
@C("a3 爻分數拿掉 stock_id")
def _(d): d['targets']['line_score']['key'].remove('stock_id')
@C("a4 scores.db 拿掉 data_version")
def _(d): d['targets']['scores_db_row']['key'].remove('data_version')
@C("a5 形成路徑拿掉日期")
def _(d): d['targets']['formation_path']['key'].remove('tpe_trading_date')
@C("a6 F-高波動 拿掉 market")
def _(d): d['flags']['F-高波動']['key'].remove('market')
@C("y3 五支旗標全拿掉 market")
def _(d):
    for f in d['flags'].values(): f['key']=[k for k in f['key'] if k!='market']
@C("y1 旗標無 key 欄")
def _(d): del d['flags']['F-分歧']['key']
@C("y2 旗標 key 為空")
def _(d): d['flags']['F-臨界']['key']=[]
@C("b1 鍵名打錯")
def _(d): d['targets']['line_reachable_range']['key'][-1]='coverag'
@C("c1 count 144→143")
def _(d): d['targets']['line_reachable_range']['count']=143
@C("c2 聚合量 count 退回 48（第九次復發舊值）")
def _(d): d['targets']['aggregate_reachable_range']['count']=48
@C("c3 T0 忘記 exclude")
def _(d): del d['targets']['threshold_T0_N0']['exclude']
@C("c4 count 改成 null 想規避驗算")
def _(d): d['targets']['line_reachable_range']['count']=None
@C("d1 刪 flip_direction")
def _(d): del d['dimensions']['flip_direction']; d['targets']['formation_path']['key'].remove('flip_direction')
@C("d2 刪 coverage_ratio")
def _(d): del d['dimensions']['coverage_ratio']
@C("e1 exclude 指非鍵維度")
def _(d): d['targets']['threshold_T0_N0']['exclude']={'market':['twse']}
@C("e2 exclude 值不存在")
def _(d): d['targets']['threshold_T0_N0']['exclude']={'state':['S9']}
@C("e3 exclude 掛在 count=null 標的")
def _(d): d['targets']['p_cs']['exclude']={'market':['twse']}
@C("x1 刪 industry_aggregate 整列")
def _(d): del d['targets']['industry_aggregate']
@C("x2 刪 event_version_chain 整列")
def _(d): del d['targets']['event_version_chain']
@C("x5 刪旗標 F-分歧")
def _(d): del d['flags']['F-分歧']
@C("x8 target key 為空")
def _(d): d['targets']['p_cs']['key']=[]
@C("x9 鍵重複同一維度")
def _(d): d['targets']['p_cs']['key'].append('market')
@C("y4 indicator_params 只留兩維")
def _(d): d['targets']['indicator_params']['key']=['market','indicator_id']
@C("y5 新增沒人用的孤兒維度")
def _(d): d['dimensions']['orphan_dim']={'values':['a'],'note':'x'}
@C("y6 coverage 加第三個值")
def _(d): d['dimensions']['coverage']['values'].append('partial')

# ---- R5j：終驗者自行構造的 6 個新型案例（原 26 例漏掉其中 5 個）----
@C("N1 刪掉新標的 upper_line_asof")
def _(d): del d['targets']['upper_line_asof']
# N2「過度加鍵」刻意不列入必抓案例——見檔尾 EXPECTED_NOT_CAUGHT
@C("N3 值域語意漂移：direction 值改 up/down（基數不變）")
def _(d): d['dimensions']['direction']['values']=['up','down']
@C("N4 維度改名 market_type→mkt_type 並同步改下游")
def _(d):
    d['dimensions']['mkt_type']=d['dimensions'].pop('market_type')
    d['targets']['hexagram_text']['key']=[('mkt_type' if k=='market_type' else k) for k in d['targets']['hexagram_text']['key']]
@C("N5 聚合量可達區間拿掉 coverage 並同步改 count")
def _(d):
    d['targets']['aggregate_reachable_range']['key'].remove('coverage')
    d['targets']['aggregate_reachable_range']['count']=96
@C("N6 scores.db 拿掉 tpe_trading_date")
def _(d): d['targets']['scores_db_row']['key'].remove('tpe_trading_date')
@C("N7 hexagram_text 拿掉 king_wen 並同步改 count")
def _(d):
    d['targets']['hexagram_text']['key'].remove('king_wen')
    d['targets']['hexagram_text']['count']=12

assert run(BASE) is False, "基準應通過"
bad=[n for n,d in cases.items() if not run(d)]
print(f"故障注入：{len(cases)} 例，抓到 {len(cases)-len(bad)}，漏 {len(bad)}")
for n in bad: print("  ✗ 漏:", n)

# ---- 刻意不檢查的邊界（記錄下來，避免日後誤以為是漏洞）----
# N2 過度加鍵：在某標的鍵上多加一個不必要的維度、並同步改對 count。
# 判斷：連續九次復發的是「**少**宣告維度」；多宣告不會讓實作者做錯，
# 把它判不合格反而會擋掉正當的新增（例如將來真的需要按 segment 分登錄）。
# 故 check_dims.py 刻意不檢查，此處以測試斷言「確實不被抓」，讓這個邊界是**明示**而非遺漏。
over = copy.deepcopy(BASE)
over['targets']['line_reachable_range']['key'].append('segment')
over['targets']['line_reachable_range']['count'] = 432
if run(over):
    print("  ! 邊界變更：N2『過度加鍵』現在會被抓——若非刻意，請確認是否誤擋正當新增")
    bad.append("N2 邊界斷言")
else:
    print("  · 邊界確認：N2『過度加鍵』刻意不檢查（理由見檔內註解）")
sys.exit(1 if bad else 0)
