import re,sys
SEP=re.compile(r'^\|(\s*:?-{2,}:?\s*\|)+$')
def cols(x): return len(re.findall(r'(?<!\\)\|',x))-1
def check(files):
    bad=0; tot=0
    for f in files:
        L=open(f,encoding='utf-8').read().split('\n'); fen=False; blk=[]
        def flush(blk):
            nonlocal bad,tot
            if not blk: return
            tot+=1
            hdr,sep=blk[0],(blk[1] if len(blk)>1 else None)
            if sep is None or not SEP.match(sep[1]):
                bad+=1; print(f"  ✗ {f}:{hdr[0]} 表格區塊無分隔列（孤兒列 {len(blk)} 行）— 前一張表被切斷？")
                return
            n=[cols(x) for _,x in blk]
            if len(set(n))>1:
                bad+=1
                for ln,x in blk:
                    if cols(x)!=n[0]: print(f"  ✗ {f}:{ln} 欄數{cols(x)}（表頭{n[0]}）")
        for i,l in enumerate(L,1):
            x=l.strip()
            if x.startswith('```'): fen=not fen; flush(blk); blk=[]; continue
            if fen: continue
            y=re.sub(r'^>\s*','',x)
            if y.startswith('|') and y.endswith('|'): blk.append((i,y))
            else:
                # 表格中間出現非表格行 = 切斷
                if blk and y and not y.startswith('#'):
                    print(f"  ⚠ {f}:{i} 表格被非表格行切斷 → {y[:60]}")
                    bad+=1
                flush(blk); blk=[]
        flush(blk)
    print(f"表格區塊 {tot}，問題 {bad} 處")
    return bad
if __name__=='__main__':
    sys.exit(1 if check(sys.argv[1:]) else 0)
