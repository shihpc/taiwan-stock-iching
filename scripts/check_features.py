#!/usr/bin/env python3
"""`features.db` 落地後的健檢報表（**唯讀**）。

`scan_features.py` 自己只保證「日期完整」；這支看的是**數字合不合理**——
日期完整但每天 `N=0` 也會印「全部落地」。

    python3 scripts/check_features.py                     # 預設 cache/features.db
    python3 scripts/check_features.py path/to/features.db

判讀要點（實際數字要對照 `docs/pre-registration.md` §1.1 的池規模）：
- **各表列數**：`p_cs` 應佔絕大多數；`market_breadth` 應為 `日數 × 2`。
- **排名池／ADV 暖機**：頭 60 個交易日 `滿窗` 應為 0、`池` 應為 0（那是正確的暖機行為）；
  之後 `池` 應穩定在數百檔（§1.1 的訓練段快照是 901 檔，逐日會變動）。
- **`eligible` vs `N`**：MA60 的 `eligible` 略小於 `N`（新股沒有 60 天歷史），差距應在 1% 上下
  （2026-09-13 實測中位 0.9950）。
- **`p_cs` 的 mean 應接近 50**：那是百分位的性質，明顯偏離代表母體或平手規則出了問題。
- **指數缺值的日子**應為 0；非 0 表示那些日子的 `excess`／`p_cs`／指數報酬缺，要查上游。
"""
import sqlite3
import sys

def main() -> int:
    db = sys.argv[1] if len(sys.argv) > 1 else "cache/features.db"
    c = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    dv = c.execute("SELECT data_version, params_sha, params_json FROM scan_meta").fetchall()
    print("scan_meta:", dv[0][0], dv[0][1])
    print("params:", dv[0][2])
    v = dv[0][0]
    print("\n各表列數:")
    for t in ("market_breadth","market_breadth_window","industry_agg","industry_breadth",
              "industry_breadth_window","p_cs","scan_day"):
        print(f"  {t:<26}{c.execute(f'SELECT COUNT(*) FROM {t} WHERE data_version=?',(v,)).fetchone()[0]:>12,}")
    d = c.execute("SELECT MIN(date),MAX(date),COUNT(*) FROM scan_day WHERE data_version=?", (v,)).fetchone()
    print(f"\n日期: {d[0]} ~ {d[1]}  共 {d[2]} 日")
    print("\n排名池／ADV 暖機（每 400 日取樣）:")
    for r in c.execute("SELECT date,rank_pool_size,adv_tracked,adv_ready FROM scan_day WHERE data_version=?"
                       " ORDER BY date", (v,)).fetchall()[::400]:
        print(f"  {r[0]}  池 {r[1]:>5}  追蹤 {r[2]:>5}  滿窗 {r[3]:>5}")
    print("\n指數缺值的日子:", c.execute("SELECT COUNT(*) FROM scan_day WHERE data_version=? AND index_missing<>''",(v,)).fetchone()[0])
    print("\n末日大盤廣度:")
    for r in c.execute("SELECT market,n_stocks,advance_count,decline_count,ad_line,"
                       "ROUND(amount_up/NULLIF(amount_total,0),4) FROM market_breadth "
                       "WHERE data_version=? AND date=? ORDER BY market",(v,d[1])):
        print(f"  {r[0]:<6} N={r[1]:<5} 漲{r[2]:<5} 跌{r[3]:<5} AD={r[4]:<8} 上漲股成交占比={r[5]}")
    print("\n末日 above_ma_ratio（count/N）:")
    for r in c.execute("SELECT w.market,w.window,w.count,b.n_stocks,w.eligible FROM market_breadth_window w "
                       "JOIN market_breadth b ON b.data_version=w.data_version AND b.market=w.market AND b.date=w.date "
                       "WHERE w.data_version=? AND w.date=? AND w.kind='above_ma' ORDER BY w.market,w.window",(v,d[1])):
        print(f"  {r[0]:<6} MA{r[1]:<3} {r[2]}/{r[3]} = {r[2]/r[3]:.3f}   (eligible {r[4]})")
    print("\n產業聚合 — 末日 twse window=60 前 5 大中位報酬:")
    for r in c.execute("SELECT industry,n,ROUND(median_ret,3) FROM industry_agg WHERE data_version=? AND date=?"
                       " AND market='twse' AND window=60 ORDER BY median_ret DESC LIMIT 5",(v,d[1])):
        print(f"  {r[0]:<14} n={r[1]:<4} {r[2]:>8}")
    print("\np_cs 分布健檢（末日 twse window=60）:")
    r = c.execute("SELECT COUNT(*),ROUND(MIN(p_cs),2),ROUND(MAX(p_cs),2),ROUND(AVG(p_cs),2) FROM p_cs "
                  "WHERE data_version=? AND date=? AND market='twse' AND window=60",(v,d[1])).fetchone()
    print(f"  n={r[0]}  min={r[1]}  max={r[2]}  mean={r[3]}  （mean 應接近 50）")
    c.close()
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except BrokenPipeError:
        # `check_features.py | head` 會在這裡炸（同 `backfill_hetzner.py:1228` 的既有處置）
        import os
        os.dup2(os.open(os.devnull, os.O_WRONLY), sys.stdout.fileno())
        raise SystemExit(0)
